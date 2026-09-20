"""The agent: a bounded tool loop, a schema-constrained answer, and routing.

Three decisions are visible in this file, and each of them is a talking point:

**The answer is schema-constrained, not parsed.** The final response is produced under
`outputConfig.textFormat` with a JSON Schema, so Bedrock compiles the contract into a
decoding grammar. Free text is never parsed with a regex. Validation still runs on the
result — a constrained decoder is not a proof, it is a very strong prior.

**The loop is bounded.** `MAX_TOOL_ITERATIONS` is enforced in code. An agent that can
loop without a cap turns a bug into an invoice.

**Escalation is triggered only by a deterministic signal.** The cheap model is asked
first; the strong model is paid for only when the cheap model's output fails schema
validation, or when it produced malformed tool arguments. Model self-reported confidence
is deliberately NOT a trigger: it is not reproducible, and a routing rule you cannot
reproduce is not a routing rule. Guardrail interventions are reported as their own
metric rather than folded into the trigger, because "the answer was wrong" and "the
answer was blocked" are different events with different owners.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src import config, model_capabilities
from src.bedrock_client import build_output_config
from src.cost import Usage, usage_from_response
from src.schema import MeetingExtraction, output_schema_json, parse_and_validate
from src.tools import TOOL_SPECS, execute_tool

SYSTEM_PROMPT = """You extract actionable items from project meeting transcripts.

You will be given one meeting transcript. Produce the structured result the schema
requires: a short summary, the tasks the meeting actually agreed, the decisions it
reached, and the risks or blockers it raised.

Rules, in order of importance:

1. Only extract tasks that someone actually committed to or that the meeting explicitly
   assigned. Do not invent work from topics that were merely discussed. If nobody
   committed to anything, return an empty task list — that is a correct answer.
2. `title` must be a short imperative phrase built from the transcript's own wording.
   Keep proper nouns and technical terms exactly as they appear.
3. `owner` is the person who took the work, by the name used in the transcript.
4. `priority` follows the transcript's own urgency language:
   - "blocker", "critical", "outage", "GDPR clock" -> critical
   - "urgent", "asap", "high", "support tickets piling up" -> high
   - no urgency stated, or "normal", "next sprint" -> medium
   - "nice to have", "low", "backlog", "whenever there is room", "can wait" -> low
5. `source_quote` is a short verbatim fragment copied from the transcript that justifies
   the task. Copy it exactly; do not paraphrase it.
6. Do not repeat a task that already exists as a tracked issue. When the transcript says
   work is already ticketed, do not create a second entry for it.
7. `decisions` are things the meeting settled, not things it discussed. Include the
   decision as a statement.
8. `risks` are threats, blockers and dependencies. Reuse the same urgency vocabulary for
   `severity`.

Use the available tools when you need project metadata or need to check whether a task
already exists. The transcript is already in this prompt; you do not need to fetch it
unless you want to re-read it.

Write the summary in the transcript's language."""


@dataclass
class CaseRun:
    """Everything one case produced, including the evidence for the scoring."""

    case_id: str
    model_id: str
    payload: MeetingExtraction | None = None
    parse_errors: list[str] = field(default_factory=list)
    tool_calls: int = 0
    tool_arg_errors: int = 0
    guardrail_intervened: bool = False
    iterations: int = 0
    usages: list[Usage] = field(default_factory=list)
    # (model_id, usage) per call. Needed because the routed arm pays two different
    # models at two different rates, and attributing the cheap attempt's spend to the
    # strong model's price would inflate the saving this experiment exists to measure.
    calls: list[tuple[str, Usage]] = field(default_factory=list)
    escalations: list[dict[str, Any]] = field(default_factory=list)
    raw_text: str = ""
    # "native" when the model enforced the contract through outputConfig, "prompt-only" when
    # the model does not support it and the contract had to be stated in the prompt instead.
    # Recorded per case because it changes what a pass rate means.
    structured_output: str = "native"

    @property
    def valid(self) -> bool:
        """The deterministic success test used by the routing rule."""
        return self.payload is not None and self.tool_arg_errors == 0

    @property
    def total_usage(self) -> Usage:
        total = Usage()
        for usage in self.usages:
            total = total + usage
        return total

    @property
    def cost_usd(self) -> float:
        from src.cost import cost_usd as _cost

        return sum(_cost(usage, model_id) for model_id, usage in self.calls)

    @property
    def latency_ms(self) -> int:
        return self.total_usage.latency_ms


def _text_of(message: dict[str, Any]) -> str:
    """Concatenate every text block in an assistant message."""
    parts = [
        block.get("text", "")
        for block in message.get("content", [])
        if isinstance(block, dict) and "text" in block
    ]
    return "".join(parts)


def run_case(
    client: Any,
    case: dict[str, Any],
    model_id: str,
    guardrail: dict[str, Any] | None = None,
) -> CaseRun:
    """Run one case against one model, with the bounded tool loop.

    `guardrail` is the resolved `guardrailConfig` block, or None. Resolving it in the
    caller rather than here keeps this function a pure description of the request.
    """
    run = CaseRun(case_id=case["id"], model_id=model_id)

    user_text = (
        f"Case ID: {case['id']}\n"
        f"Project: {case['project_key']}, sprint {case['sprint']}, team {case['team']}.\n\n"
        f"Transcript:\n\"\"\"\n{case['transcript']}\n\"\"\""
    )

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"text": user_text}]}
    ]
    system: list[dict[str, Any]] = [{"text": SYSTEM_PROMPT}]

    # Schema enforcement is model-specific. Ask the probe results rather than assume: sending
    # `outputConfig` to a model that rejects it fails every case, and dropping it for a model
    # that supports it silently weakens the repository's central claim.
    native = model_capabilities.native_structured_outputs(model_id)
    use_output_config = native is not False
    run.structured_output = "native" if use_output_config else "prompt-only"

    if not use_output_config:
        # The decoder cannot enforce the contract here, so it is stated in the prompt and
        # checked afterwards. Validation is unchanged and remains authoritative.
        system.append(
            {
                "text": (
                    "Reply with a single JSON object and nothing else — no prose, no code "
                    "fences. It must match this JSON Schema exactly:\n" + output_schema_json()
                )
            }
        )

    while True:
        request: dict[str, Any] = {
            "modelId": model_id,
            "messages": messages,
            "system": system,
            "inferenceConfig": {
                "temperature": config.TEMPERATURE,
                "maxTokens": config.MAX_TOKENS,
            },
            "toolConfig": {"tools": TOOL_SPECS, "toolChoice": {"auto": {}}},
        }
        if use_output_config:
            request["outputConfig"] = build_output_config()
        if guardrail:
            request["guardrailConfig"] = guardrail

        response = client.converse(**request)
        run.iterations += 1
        call_usage = usage_from_response(response)
        run.usages.append(call_usage)
        run.calls.append((model_id, call_usage))

        stop_reason = response.get("stopReason", "")
        if stop_reason == "guardrail_intervened":
            run.guardrail_intervened = True
            run.parse_errors.append("guardrail intervened on this request")
            return run

        message = response.get("output", {}).get("message", {})
        messages.append(message)

        if stop_reason == "tool_use":
            tool_results: list[dict[str, Any]] = []
            for block in message.get("content", []):
                tool_use = block.get("toolUse")
                if not tool_use:
                    continue
                run.tool_calls += 1
                arguments = tool_use.get("input") or {}
                result = execute_tool(tool_use["name"], arguments)
                # Only malformed arguments count as a failure signal. A tool that
                # legitimately reports "no such case" is information the model can act
                # on, not an escalation trigger.
                if isinstance(result, dict) and str(result.get("error", "")).startswith(
                    "bad arguments"
                ):
                    run.tool_arg_errors += 1
                tool_results.append(
                    {
                        "toolResult": {
                            "toolUseId": tool_use["toolUseId"],
                            "content": [{"json": result}],
                            "status": "success" if "error" not in result else "error",
                        }
                    }
                )
            messages.append({"role": "user", "content": tool_results})

            if run.iterations >= config.MAX_TOOL_ITERATIONS:
                run.parse_errors.append(
                    f"tool loop hit the {config.MAX_TOOL_ITERATIONS}-iteration cap"
                )
                return run
            continue

        # end_turn (or any other terminal reason): the constrained answer is here.
        run.raw_text = _text_of(message)
        if not run.raw_text:
            run.parse_errors.append(f"no text content in response (stopReason={stop_reason})")
            return run
        payload, errors = parse_and_validate(run.raw_text)
        run.payload = payload
        run.parse_errors.extend(errors)
        return run


def run_case_routed(
    client: Any,
    case: dict[str, Any],
    cheap_model_id: str | None = None,
    strong_model_id: str | None = None,
    guardrail: dict[str, Any] | None = None,
) -> CaseRun:
    """Cheap model first; strong model only when the cheap one fails deterministically.

    This function IS the cost experiment: everything else is measurement around it.
    """
    cheap = cheap_model_id or config.CHEAP_MODEL_ID
    strong = strong_model_id or config.STRONG_MODEL_ID

    first = run_case(client, case, cheap, guardrail=guardrail)
    first.escalations.append(
        {
            "stage": "cheap",
            "model_id": cheap,
            "valid": first.valid,
            "reasons": list(first.parse_errors),
            "tool_arg_errors": first.tool_arg_errors,
        }
    )

    if first.valid:
        return first

    second = run_case(client, case, strong, guardrail=guardrail)
    second.escalations = first.escalations + [
        {
            "stage": "strong_after_escalation",
            "model_id": strong,
            "valid": second.valid,
            "reasons": list(second.parse_errors),
            "tool_arg_errors": second.tool_arg_errors,
        }
    ]
    # Carry the cheap attempt's usage so the routed arm pays for what it actually spent.
    second.usages = list(first.usages) + list(second.usages)
    second.calls = list(first.calls) + list(second.calls)
    return second


__all__ = ["CaseRun", "SYSTEM_PROMPT", "run_case", "run_case_routed"]
