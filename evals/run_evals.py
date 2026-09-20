"""The evaluation gate.

Run it before a prompt change, run it after, compare the pass rate. That is the whole
idea: a prompt is code, and code without a test is a guess.

Two scoring axes are kept strictly separate, because blending them hides the failure
mode that matters:

  * **schema validity** — did the model return the contract at all?
  * **field correctness** — were the extracted values the right ones?

A single blended score would let a well-formed but wrong answer hide behind a
well-formed answer, which is precisely how a demo lies to its author.

Usage:
    python -m evals.run_evals --offline              # plumbing only, no AWS, no cost
    python -m evals.run_evals                        # live, routed arm (the design)
    python -m evals.run_evals --arm all              # cheap, strong and routed
    python -m evals.run_evals --guardrail off        # to measure the leak delta
    python -m evals.run_evals --gate 0.9             # non-zero exit below the threshold
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from src import config, pricing
from src.agent import CaseRun, run_case, run_case_routed
from src.bedrock_client import make_client
from src.cases import all_cases

RESULTS_PATH = Path(__file__).resolve().parent / "results.json"

# A task title matches when the expected title's informative tokens are largely
# present. Token recall rather than exact equality, because two competent writers will
# phrase the same action differently and an eval that fails them both is measuring
# nothing. 0.6 is deliberately forgiving; the priority and owner checks below are the
# strict part.
_TITLE_RECALL = 0.6


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def compact(text: str) -> str:
    """Strip everything but alphanumerics — for detecting reformatted PII.

    A masked-then-leaked phone number is still a leak; comparing only raw substrings
    would miss `4111-1111-1111-1111` against `4111 1111 1111 1111`.
    """
    return re.sub(r"[^0-9a-z]", "", text.lower())


def title_recall(expected: str, produced: str) -> float:
    expected_tokens = set(normalise(expected).split())
    produced_tokens = set(normalise(produced).split())
    if not expected_tokens:
        return 0.0
    if normalise(expected) in normalise(produced) or normalise(produced) in normalise(
        expected
    ):
        return 1.0
    return len(expected_tokens & produced_tokens) / len(expected_tokens)


def quote_grounded(quote: str, transcript: str) -> bool:
    """Is the source quote actually in the transcript?

    A five-word shingle of the quote must appear verbatim in the transcript. This is the
    anti-hallucination check: a quote that cannot be found means the justification was
    invented, whatever the answer's other merits.
    """
    quote_tokens = normalise(quote).split()
    if len(quote_tokens) < 5:
        return False
    haystack = " " + normalise(transcript) + " "
    for start in range(len(quote_tokens) - 4):
        window = " ".join(quote_tokens[start : start + 5])
        if f" {window} " in haystack:
            return True
    return False


def pii_leaks(text: str, pii: list[str]) -> list[str]:
    """Which planted identifiers survived into the output."""
    haystack = text
    haystack_compact = compact(text)
    leaked = []
    for item in pii:
        if item in haystack or compact(item) in haystack_compact:
            leaked.append(item)
    return leaked


def score_case(case: dict[str, Any], run: CaseRun) -> dict[str, Any]:
    expected = case["expected"]
    produced = run.payload

    result: dict[str, Any] = {
        "case_id": case["id"],
        "model_id": run.model_id,
        "schema_valid": produced is not None,
        "errors": list(run.parse_errors),
        "tool_calls": run.tool_calls,
        "tool_arg_errors": run.tool_arg_errors,
        "iterations": run.iterations,
        "guardrail_intervened": run.guardrail_intervened,
        "escalated": any(e["stage"] == "strong_after_escalation" for e in run.escalations),
    }

    result["pii_leaked"] = pii_leaks(run.raw_text, case.get("pii", []))

    if produced is None:
        result.update(
            {
                "tasks_matched": 0,
                "tasks_expected": len(expected["tasks"]),
                "extra_tasks": 0,
                "decisions_found": 0,
                "decisions_expected": len(expected["decisions"]),
                "risks_found": 0,
                "risks_expected": len(expected["risks"]),
                "quotes_grounded": 0,
                "quotes_total": 0,
                "fields_correct": False,
            }
        )
        return result

    # -- tasks ---------------------------------------------------------------------
    unmatched = list(produced.tasks)
    matched = 0
    for want in expected["tasks"]:
        best_index, best_score = None, 0.0
        for index, got in enumerate(unmatched):
            score = title_recall(want["title"], got.title)
            if score > best_score:
                best_index, best_score = index, score
        if best_index is not None and best_score >= _TITLE_RECALL:
            got = unmatched.pop(best_index)
            priority_ok = got.priority.value == want["priority"]
            owner_ok = normalise(want["owner"]) in normalise(got.owner)
            if priority_ok and owner_ok:
                matched += 1
            else:
                result.setdefault("task_detail_errors", []).append(
                    {
                        "title": want["title"],
                        "priority_expected": want["priority"],
                        "priority_got": got.priority.value,
                        "owner_expected": want["owner"],
                        "owner_got": got.owner,
                    }
                )

    extra_tasks = len(unmatched)
    extra_excess = extra_tasks if case.get("strict_extras", True) else 0

    # -- decisions and risks -------------------------------------------------------
    produced_decisions = normalise(" || ".join(d.statement for d in produced.decisions))
    produced_risks = normalise(" || ".join(r.description for r in produced.risks))
    decisions_found = sum(
        1 for want in expected["decisions"] if normalise(want) in produced_decisions
    )
    risks_found = sum(1 for want in expected["risks"] if normalise(want) in produced_risks)

    # -- grounding -----------------------------------------------------------------
    quotes_total = len(produced.tasks)
    quotes_grounded = sum(
        1 for task in produced.tasks if quote_grounded(task.source_quote, case["transcript"])
    )

    fields_correct = (
        matched == len(expected["tasks"])
        and extra_excess == 0
        and decisions_found == len(expected["decisions"])
        and risks_found == len(expected["risks"])
    )

    result.update(
        {
            "tasks_matched": matched,
            "tasks_expected": len(expected["tasks"]),
            "extra_tasks": extra_excess,
            "decisions_found": decisions_found,
            "decisions_expected": len(expected["decisions"]),
            "risks_found": risks_found,
            "risks_expected": len(expected["risks"]),
            "quotes_grounded": quotes_grounded,
            "quotes_total": quotes_total,
            "fields_correct": fields_correct,
        }
    )
    return result


def run_arm(
    client: Any, arm: str, guardrail: dict[str, Any] | None
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in all_cases():
        if arm == "cheap":
            run = run_case(client, case, config.CHEAP_MODEL_ID, guardrail=guardrail)
        elif arm == "strong":
            run = run_case(client, case, config.STRONG_MODEL_ID, guardrail=guardrail)
        else:
            run = run_case_routed(client, case, guardrail=guardrail)
        row = score_case(case, run)
        row["usage"] = run.total_usage.as_dict()
        row["arm"] = arm
        rows.append(row)
    return rows


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    valid = sum(1 for r in rows if r["schema_valid"])
    correct = sum(1 for r in rows if r["fields_correct"])
    leaks = sum(len(r["pii_leaked"]) for r in rows)
    escalated = sum(1 for r in rows if r["escalated"])
    grounded = sum(r["quotes_grounded"] for r in rows)
    quotes = sum(r["quotes_total"] for r in rows)
    return {
        "cases": total,
        "schema_valid": valid,
        "schema_valid_rate": valid / total if total else 0.0,
        "fields_correct": correct,
        "fields_correct_rate": correct / total if total else 0.0,
        "end_to_end_rate": sum(
            1 for r in rows if r["schema_valid"] and r["fields_correct"]
        )
        / total
        if total
        else 0.0,
        "pii_leaks": leaks,
        "escalated": escalated,
        "quotes_grounded": grounded,
        "quotes_total": quotes,
        "input_tokens": sum(r["usage"]["billable_input_tokens"] for r in rows),
        "output_tokens": sum(r["usage"]["output_tokens"] for r in rows),
        "latency_ms_total": sum(r["usage"]["latency_ms"] for r in rows),
    }


def print_table(rows: list[dict[str, Any]]) -> None:
    header = (
        f"{'case':<9} {'schema':<7} {'tasks':<7} {'extra':<6} {'dec':<6} {'risk':<6} "
        f"{'quoted':<8} {'pii':<4} {'tools':<6} {'esc':<4} {'iter':<5}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['case_id']:<9} "
            f"{'ok' if row['schema_valid'] else 'FAIL':<7} "
            f"{row['tasks_matched']}/{row['tasks_expected']:<5} "
            f"{row['extra_tasks']:<6} "
            f"{row['decisions_found']}/{row['decisions_expected']:<4} "
            f"{row['risks_found']}/{row['risks_expected']:<4} "
            f"{row['quotes_grounded']}/{row['quotes_total']:<6} "
            f"{len(row['pii_leaked']):<4} "
            f"{row['tool_calls']:<6} "
            f"{'yes' if row['escalated'] else '-':<4} "
            f"{row['iterations']:<5}"
        )
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the 20-case evaluation suite.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="use the deterministic stub instead of Bedrock (plumbing test; cost suppressed)",
    )
    parser.add_argument(
        "--arm",
        choices=["cheap", "strong", "routed", "all"],
        default="routed",
        help="which model arm to evaluate (default: routed, i.e. the shipped design)",
    )
    parser.add_argument(
        "--guardrail",
        choices=["auto", "on", "off"],
        default="auto",
        help="apply the input guardrail (auto = on when GUARDRAIL_ID is set)",
    )
    parser.add_argument(
        "--gate",
        type=float,
        default=None,
        help="exit non-zero when the end-to-end rate is below this value, e.g. 0.9",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=RESULTS_PATH,
        help=(
            "where to write the report. The guardrail comparison needs two runs kept apart, "
            "so `make live` writes results-guardrail-off.json and results-guardrail-on.json."
        ),
    )
    args = parser.parse_args(argv)

    guardrail_wanted = (args.guardrail == "on") or (
        args.guardrail == "auto" and bool(config.GUARDRAIL_ID)
    )
    guardrail_cfg: dict[str, Any] | None = None
    if guardrail_wanted:
        guardrail_cfg = config.guardrail_config()
        if guardrail_cfg is None:
            if args.offline:
                # A sentinel id lets the offline stub simulate an input mask. This is
                # a mechanism demonstration, not evidence about Bedrock Guardrails.
                guardrail_cfg = {
                    "guardrailIdentifier": "offline-simulated",
                    "guardrailVersion": "DRAFT",
                }
                print(
                    "note: offline mode is SIMULATING the guardrail with a sentinel id. "
                    "The leak numbers below show the plumbing works; they are not "
                    "evidence about Bedrock Guardrails. Run live for evidence.\n"
                )
            else:
                print(
                    "error: --guardrail on requires GUARDRAIL_ID "
                    "(run `make guardrail`)."
                )
                return 2

    client = make_client(offline=args.offline)
    arms = ["cheap", "strong", "routed"] if args.arm == "all" else [args.arm]

    with_cost = not args.offline
    if with_cost:
        models = sorted(
            {
                config.CHEAP_MODEL_ID,
                config.STRONG_MODEL_ID,
            }
        )
        try:
            pricing.assert_prices_loaded(models)
        except pricing.PricesNotLoaded as exc:
            print(f"error: {exc}")
            return 2

    report: dict[str, Any] = {
        "mode": "offline-stub" if args.offline else "live-bedrock",
        "region": config.REGION,
        "guardrail": guardrail_cfg is not None,
        "guardrail_id": config.GUARDRAIL_ID or None,
        "prices_as_of": pricing.PRICES_AS_OF or None,
        "arms": {},
    }

    exit_code = 0
    for arm in arms:
        print(f"\n=== arm: {arm} | region: {config.REGION} | "
              f"guardrail: {'on' if guardrail_cfg else 'off'} | "
              f"mode: {'offline(stub)' if args.offline else 'live'} ===\n")
        rows = run_arm(client, arm, guardrail_cfg)
        print_table(rows)
        summary = summarise(rows)
        summary["cost_usd"] = None
        if with_cost:
            from src.cost import Usage as _Usage
            from src.cost import cost_usd

            total = 0.0
            for row in rows:
                usage = _Usage(
                    input_tokens=row["usage"]["input_tokens"],
                    output_tokens=row["usage"]["output_tokens"],
                    cache_read_tokens=row["usage"]["cache_read_tokens"],
                    cache_write_tokens=row["usage"]["cache_write_tokens"],
                )
                # A routed row may have called two different models; attribute its
                # tokens to the model it finished on. The compare experiment reports
                # per-model costs separately and precisely.
                total += cost_usd(usage, row["model_id"])
            summary["cost_usd"] = total
        report["arms"][arm] = {"summary": summary, "rows": rows}

        print(
            f"schema-valid {summary['schema_valid_rate']:.0%} | "
            f"fields-correct {summary['fields_correct_rate']:.0%} | "
            f"end-to-end {summary['end_to_end_rate']:.0%} | "
            f"escalations {summary['escalated']}/{summary['cases']} | "
            f"PII leaks {summary['pii_leaks']} | "
            f"quotes grounded {summary['quotes_grounded']}/{summary['quotes_total']}"
        )
        if with_cost:
            print(
                f"cost ${summary['cost_usd']:.6f} for {summary['cases']} cases "
                f"(prices as of {pricing.PRICES_AS_OF})"
            )
        else:
            print(
                "cost: suppressed — the offline stub fabricates token counts, so any "
                "currency here would be fiction. Run without --offline for cost."
            )

        if args.gate is not None and summary["end_to_end_rate"] < args.gate:
            print(
                f"GATE FAILED: end-to-end {summary['end_to_end_rate']:.0%} "
                f"< {args.gate:.0%}"
            )
            exit_code = 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
