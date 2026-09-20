#!/usr/bin/env python3
"""Probe each candidate model for the three things this repository needs from it.

The evaluation gate depends on a model's *capabilities*, not on its price or its marketing.
There are exactly three questions, and they have to be answered per model per region:

  1. **Can it be invoked at all?** (the account throttle, and inference-profile requirements)
  2. **Does it support native structured outputs?** (`outputConfig.textFormat` with a JSON
     Schema — the mechanism that makes the final answer schema-constrained rather than parsed)
  3. **Does it support tool use?** (`toolConfig`)

Capability 2 is the one that decides whether a model can stand in for Claude in the routed arm.
It is model-specific and documented only per model card, so the honest way to know is to ask
the API — with one tiny call each, which is what this script does.

Writes `evals/model_capabilities.json`, which `src/model_capabilities.py` reads so the agent can
omit `outputConfig` for a model that would reject it instead of failing every case.

Usage:
    python scripts/probe_structured_outputs.py
    python scripts/probe_structured_outputs.py --model deepseek.v3.2
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from src import config
from src.schema import output_schema_json

OUT_PATH = Path(__file__).resolve().parents[1] / "evals" / "model_capabilities.json"

# The two arms this repository is designed around, plus the cheap alternatives worth knowing
# about. Every one is available ON_DEMAND or via an EU inference profile in eu-north-1.
CANDIDATES: list[tuple[str, str]] = [
    (config.CHEAP_MODEL_ID, "Claude Haiku 4.5 (cheap tier)"),
    (config.STRONG_MODEL_ID, "Claude Sonnet 4.5 (strong tier)"),
    ("deepseek.v3.2", "DeepSeek V3.2"),
    ("deepseek.v3-v1:0", "DeepSeek V3"),
    ("openai.gpt-oss-20b-1:0", "GPT-OSS 20B"),
    ("amazon.nova-lite-v1:0", "Nova Lite"),
    ("zai.glm-4.7-flash", "GLM 4.7 Flash"),
]

PROMPT = [{"role": "user", "content": [{"text": "Name one colour. Answer in one word."}]}]
INFERENCE = {"maxTokens": 64, "temperature": 0}

# Not every region reports the same restriction the same way. A throttled account answers
# "Too many tokens per day" in eu-north-1 and eu-central-1, but names the cause outright in
# eu-west-1. When the primary region gives a reason that smells like a surface symptom, ask the
# one that is honest before writing it down.
HONEST_REGION = "eu-west-1"


def _runtime(region: str | None = None):
    import boto3

    return boto3.client("bedrock-runtime", region_name=region or config.REGION)


def classify(exc: Exception) -> str:
    """Turn a failure into a short, stable reason string."""
    text = str(exc)
    if "currently being verified" in text:
        return "account under verification (AWS-side, not a configuration fault)"
    if "Too many tokens per day" in text:
        return "throttled: account-level daily token restriction"
    if "on-demand throughput isn" in text:
        return "requires an inference profile (bare model id is not on-demand)"
    if "not authorized to perform" in text:
        return "iam: the scoped policy does not grant this action"
    if "use case details" in text:
        return "model access not granted for this account"
    if "isn't supported" in text or "not supported" in text:
        return "unsupported feature for this model"
    if "ValidationException" in text:
        return f"validation: {text.split(':', 2)[-1].strip()[:120]}"
    return f"{type(exc).__name__}: {text[:140]}"


def probe_invoke(rt, model_id: str, honest_rt=None) -> dict:
    try:
        response = rt.converse(
            modelId=model_id, messages=PROMPT, inferenceConfig=INFERENCE
        )
        return {"ok": True, "usage": response.get("usage")}
    except Exception as exc:  # noqa: BLE001
        reason = classify(exc)
        if honest_rt is not None and reason.startswith("throttled"):
            # A throttle message is a symptom. Ask the region that names the cause, so the
            # record says what is actually wrong instead of sending the next reader down the
            # same blind alley this project already went down twice.
            try:
                honest_rt.converse(
                    modelId=model_id, messages=PROMPT, inferenceConfig=INFERENCE
                )
            except Exception as fallback_exc:  # noqa: BLE001
                alt = classify(fallback_exc)
                if "verification" in alt:
                    reason = f"{alt} (reported as throttling in {config.REGION})"
        return {"ok": False, "reason": reason}


def probe_native_schema(rt, model_id: str) -> dict:
    try:
        rt.converse(
            modelId=model_id,
            messages=PROMPT,
            inferenceConfig=INFERENCE,
            outputConfig={
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
            },
        )
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": classify(exc)}


def probe_tool_use(rt, model_id: str) -> dict:
    tools = [
        {
            "toolSpec": {
                "name": "report_colour",
                "description": "Report the chosen colour. You must call this.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {"colour": {"type": "string"}},
                        "required": ["colour"],
                        "additionalProperties": False,
                    }
                },
            }
        }
    ]
    try:
        rt.converse(
            modelId=model_id,
            messages=PROMPT,
            inferenceConfig=INFERENCE,
            toolConfig={"tools": tools, "toolChoice": {"any": {}}},
        )
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": classify(exc)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe model capabilities on this account.")
    parser.add_argument("--model", action="append", help="probe only this model id (repeatable)")
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between calls")
    args = parser.parse_args(argv)

    candidates = (
        [(m, m) for m in args.model] if args.model else CANDIDATES
    )

    rt = _runtime()
    honest_rt = _runtime(HONEST_REGION) if HONEST_REGION != config.REGION else None
    print(f"\nprobing {len(candidates)} model(s) in {config.REGION}\n")

    results: dict[str, dict] = {}
    if OUT_PATH.exists():
        results = json.loads(OUT_PATH.read_text(encoding="utf-8")).get("models", {})

    for model_id, label in candidates:
        print(f"  {label}")
        entry = {
            "model_id": model_id,
            "label": label,
            "invoke": probe_invoke(rt, model_id, honest_rt=honest_rt),
        }
        time.sleep(args.pause)

        if entry["invoke"]["ok"]:
            entry["native_structured_outputs"] = probe_native_schema(rt, model_id)
            time.sleep(args.pause)
            entry["tool_use"] = probe_tool_use(rt, model_id)
        else:
            # Do not probe further: the failures would all report the same root cause and
            # would cost three times as much of whatever the account is throttled on.
            entry["native_structured_outputs"] = {
                "ok": None,
                "reason": "not probed: invocation failed",
            }
            entry["tool_use"] = {"ok": None, "reason": "not probed: invocation failed"}

        for key in ("invoke", "native_structured_outputs", "tool_use"):
            value = entry[key]["ok"]
            mark = "✓" if value is True else "·" if value is None else "✗"
            detail = entry[key].get("reason", "")
            print(f"      {mark} {key:26} {detail}")
        print()
        results[model_id] = entry

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(
            {
                "region": config.REGION,
                "probed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "note": (
                    "Capabilities are per model per region. 'ok: null' means not probed, "
                    "not unsupported."
                ),
                "models": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {OUT_PATH.relative_to(OUT_PATH.parents[1])}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
