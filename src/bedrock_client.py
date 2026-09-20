"""Converse clients: one real, one offline stub.

The offline stub exists so the plumbing — tool loop, schema validation, escalation,
scoring, reporting — can be exercised without an AWS account and without spending
anything. It is a plumbing test, not a measurement.

**Its token counts are fabricated.** Any cost derived from the stub is meaningless, so
`evals/run_evals.py` suppresses currency in offline mode and
`experiments/compare.py` refuses to run offline at all. That separation is the point:
the one number this repository must never fudge is the cost table.
"""

from __future__ import annotations

import json
import re
from typing import Any

from src import config
from src.schema import output_schema_json

_CASE_ID_RE = re.compile(r"Case ID:\s*([a-z0-9-]+)", re.IGNORECASE)

# Index used for transcripts that are not fixtures, e.g. submitted through the CLI or over
# MCP. Chosen to exercise the tool path (3 % 3 == 0) while NOT injecting the schema
# failure (3 % 5 != 0), so an ad-hoc request is never escalated for a reason the caller
# cannot see.
_ADHOC_INDEX = 3


class ConverseError(RuntimeError):
    """A Bedrock call failed. Carries the API message for the run log."""


class LiveConverseClient:
    """Thin wrapper over the bedrock-runtime Converse API.

    Thin on purpose: the request shape is the interesting artefact here, and hiding it
    behind an abstraction would hide exactly what a reviewer wants to read.
    """

    def __init__(self, region: str | None = None) -> None:
        try:
            import boto3  # imported lazily so offline mode needs no AWS SDK
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ConverseError(
                "boto3 is not installed. Run `make install`, or use --offline."
            ) from exc

        self.region = region or config.REGION
        self._client = boto3.client("bedrock-runtime", region_name=self.region)

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        try:
            return self._client.converse(**kwargs)
        except Exception as exc:  # noqa: BLE001 - re-raised with context
            raise ConverseError(
                f"Bedrock Converse call failed for model "
                f"{kwargs.get('modelId')!r} in {self.region}: {exc}"
            ) from exc


class OfflineConverseClient:
    """Deterministic stand-in for Bedrock, for testing the plumbing.

    Behaviour, chosen so one offline run exercises every branch:

    * every third case emits a `toolUse` block first, so the tool loop and the
      `toolResult` continuation are exercised;
    * every fifth case returns a payload that violates the schema, so the escalation
      path is exercised and shows up in the routing report;
    * everything else returns a schema-valid payload.

    It answers from the fixture's `expected` block, i.e. it is a perfect oracle. A high
    offline pass rate therefore proves the harness works, and proves nothing at all
    about the models.

    Two behaviours are simulated rather than measured, and both are labelled as such in
    the docs: the schema failure is injected on the CHEAP model only (so the analysis
    shows a real escalation succeeding), and when a `guardrailConfig` is present the stub
    redacts the planted identifiers out of the quote it echoes, mimicking an input
    mask. The offline PII-leak numbers are therefore a simulation of the mechanism, not
    evidence about Bedrock Guardrails. Only a live run produces evidence.
    """

    _INDEX_RE = re.compile(r"case-(\d+)")

    def __init__(self) -> None:
        self.region = "offline"
        self.simulated_guardrail = False

    # -- helpers -------------------------------------------------------------------
    @staticmethod
    def _case_id(kwargs: dict[str, Any]) -> str:
        blob = json.dumps(kwargs.get("messages", [])) + json.dumps(
            kwargs.get("system", [])
        )
        match = _CASE_ID_RE.search(blob)
        return match.group(1) if match else "case-01"

    @staticmethod
    def _calls_tools(kwargs: dict[str, Any]) -> bool:
        return "toolResult" in json.dumps(kwargs.get("messages", []))

    @staticmethod
    def _usage(prompt_chars: int, answer_chars: int) -> dict[str, int]:
        return {
            "inputTokens": max(1, prompt_chars // 4),
            "outputTokens": max(1, answer_chars // 4),
            "totalTokens": max(2, (prompt_chars + answer_chars) // 4),
        }

    @staticmethod
    def _response(content: list[dict[str, Any]], stop_reason: str, usage: dict) -> dict:
        return {
            "output": {"message": {"role": "assistant", "content": content}},
            "stopReason": stop_reason,
            "usage": usage,
            "metrics": {"latencyMs": 0},
        }

    # -- the interface -------------------------------------------------------------
    def converse(self, **kwargs: Any) -> dict[str, Any]:
        from src.cases import get_case  # local import keeps module import cost low

        case_id = self._case_id(kwargs)
        matched = self._INDEX_RE.search(case_id)
        index = int(matched.group(1)) if matched else _ADHOC_INDEX
        case = get_case(case_id)
        prompt_chars = len(json.dumps(kwargs.get("messages", []))) + len(
            json.dumps(kwargs.get("system", []))
        )

        # 1. Exercise the tool loop on every third case, once.
        if index % 3 == 0 and not self._calls_tools(kwargs):
            content = [
                {
                    "toolUse": {
                        "toolUseId": f"stub-{case_id}-1",
                        "name": "get_project_metadata",
                        "input": {"case_id": case_id},
                    }
                }
            ]
            answer = json.dumps(content)
            return self._response(
                content, "tool_use", self._usage(prompt_chars, len(answer))
            )

        # 2. Exercise the escalation path: the CHEAP model fails, the strong one must
        #    recover. Injecting the failure on both models would only ever prove that
        #    the harness reports failures.
        if index % 5 == 0 and kwargs.get("modelId") == config.CHEAP_MODEL_ID:
            broken = json.dumps({"summary": "stub", "tasks": [{"title": "incomplete"}]})
            content = [{"text": broken}]
            return self._response(
                content, "end_turn", self._usage(prompt_chars, len(broken))
            )

        # 3. Otherwise act as a perfect oracle. When a guardrail is configured, redact
        #    the planted identifiers out of the echoed quote, mimicking an input mask.
        transcript = case["transcript"]
        if kwargs.get("guardrailConfig") and case.get("pii"):
            self.simulated_guardrail = True
            for item in case["pii"]:
                transcript = transcript.replace(item, "[REDACTED]")

        payload = {
            "summary": f"{case['team']} meeting, sprint {case['sprint']}.",
            "tasks": [
                {
                    "title": task["title"],
                    "description": f"Agreed in the {case['project_key']} meeting.",
                    "priority": task["priority"],
                    "owner": task["owner"],
                    "source_quote": transcript[:120] or "no transcript",
                }
                for task in case["expected"]["tasks"]
            ],
            "decisions": [
                {"statement": decision, "made_by": "the team"}
                for decision in case["expected"]["decisions"]
            ],
            "risks": [
                {"description": risk, "severity": "medium"}
                for risk in case["expected"]["risks"]
            ],
        }
        text = json.dumps(payload)
        return self._response([{"text": text}], "end_turn", self._usage(prompt_chars, len(text)))


def build_output_config() -> dict[str, Any]:
    """The native structured-output block: `outputConfig.textFormat`.

    This is the 2026 mechanism. The older recipe — declaring a tool whose input schema
    is your contract and forcing it with `toolChoice` — is still documented, but it is
    a workaround where a first-class feature now exists, and `toolChoice: "tool"` is
    only supported on Claude 3 and Nova. So: native schema for the final answer, tool
    use for the intermediate work.
    """
    return {
        "textFormat": {
            "type": "json_schema",
            "structure": {
                "jsonSchema": {
                    "schema": output_schema_json(),
                    "name": config.SCHEMA_NAME,
                    "description": config.SCHEMA_DESCRIPTION,
                }
            },
        }
    }


def make_client(offline: bool = False) -> LiveConverseClient | OfflineConverseClient:
    return OfflineConverseClient() if offline else LiveConverseClient()


__all__ = [
    "ConverseError",
    "LiveConverseClient",
    "OfflineConverseClient",
    "build_output_config",
    "make_client",
]
