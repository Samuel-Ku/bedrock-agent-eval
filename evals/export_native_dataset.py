"""Export the same twenty cases into the JSONL shape a native Bedrock evaluation job wants.

Why this exists: the primary eval harness is local and deterministic, but the posting
lists AWS-native tooling as a preference, and "I know Bedrock Evaluations exists and here
is the dataset it eats" is a defensible way to show that without paying the setup cost of
two S3 buckets and an IAM role for twenty cases.

The native job's dataset is JSONL in S3 with these keys:

  prompt             required — the full prompt for one case
  referenceResponse  required for accuracy-style metrics — the expected answer
  category           optional — produces per-category scores

Usage:
    python -m evals.export_native_dataset            # writes evals/native_dataset.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.agent import SYSTEM_PROMPT
from src.cases import all_cases

DEFAULT_OUT = Path(__file__).resolve().parent / "native_dataset.jsonl"


def build_prompt(case: dict) -> str:
    """The same instruction the local harness sends, flattened into one string."""
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"Case ID: {case['id']}\n"
        f"Project: {case['project_key']}, sprint {case['sprint']}, team {case['team']}.\n\n"
        f"Transcript:\n\"\"\"\n{case['transcript']}\n\"\"\""
    )


def build_reference(case: dict) -> str:
    """The expected answer in the contract's own shape.

    Decisions and risks are emitted as the contract expects them, so the reference can
    be compared field by field rather than as prose.
    """
    payload = {
        "summary": f"{case['team']} meeting, sprint {case['sprint']}.",
        "tasks": [
            {
                "title": task["title"],
                "description": f"Agreed in the {case['project_key']} meeting.",
                "priority": task["priority"],
                "owner": task["owner"],
                "source_quote": "",
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
    return json.dumps(payload, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the native-eval JSONL dataset.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    cases = all_cases()
    # The native job accepts at most 1000 prompts; 20 is comfortably inside it.
    if len(cases) > 1000:
        print(f"error: {len(cases)} cases exceeds the 1000-prompt limit.")
        return 2

    with args.out.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(
                json.dumps(
                    {
                        "prompt": build_prompt(case),
                        "referenceResponse": build_reference(case),
                        # Per-category scores come free if you set this.
                        "category": case["project_key"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    print(f"wrote {len(cases)} prompts to {args.out}")
    print(
        "next: upload to S3 and create an evaluation job — see docs/NATIVE_EVAL.md"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
