"""The cost experiment: three arms, one table.

    always-cheap    Haiku 4.5 on every case.
    always-strong   Sonnet 4.5 on every case.
    routed          Haiku 4.5 first; Sonnet 4.5 only when the cheap attempt failed
                    deterministic validation.

The routed arm is the claim. Everything else in this repository exists to make its
numbers trustworthy. The report is therefore built around two questions a reviewer will
actually ask:

  1. Did routing lose any accuracy? (If yes, the saving is not real.)
  2. What did it save, per case and extrapolated? (The extrapolation is labelled as an
     extrapolation, because 20 cases is 20 cases.)

`cost per 1,000 cases` is derived by scaling the measured total. It is an extrapolation
from twenty cases, not a measurement, and the README says so.

This module refuses to run in offline mode: the stub fabricates token counts, so a cost
table produced from it would be fiction wearing a decimal point.

Usage:
    python -m experiments.compare                 # all three arms, live
    python -m experiments.compare --arms routed always-strong
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from evals.run_evals import score_case
from src import config, pricing
from src.agent import CaseRun, run_case, run_case_routed
from src.bedrock_client import make_client
from src.cases import all_cases

RESULTS_PATH = Path(__file__).resolve().parent / "results.json"

ARMS = ("always-cheap", "always-strong", "always-third", "routed")


def run_arm(
    client: Any, arm: str, guardrail: dict[str, Any] | None
) -> list[tuple[dict, CaseRun]]:
    out: list[tuple[dict, CaseRun]] = []
    for case in all_cases():
        if arm == "always-cheap":
            run = run_case(client, case, config.CHEAP_MODEL_ID, guardrail=guardrail)
        elif arm == "always-strong":
            run = run_case(client, case, config.STRONG_MODEL_ID, guardrail=guardrail)
        elif arm == "always-third":
            run = run_case(client, case, config.THIRD_MODEL_ID, guardrail=guardrail)
        else:
            run = run_case_routed(client, case, guardrail=guardrail)
        out.append((case, run))
    return out


def per_model_spend(runs: list[CaseRun]) -> dict[str, dict[str, float]]:
    from src.cost import cost_usd

    buckets: dict[str, dict[str, float]] = {}
    for run in runs:
        for model_id, usage in run.calls:
            bucket = buckets.setdefault(
                model_id, {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}
            )
            bucket["calls"] += 1
            bucket["cost_usd"] += cost_usd(usage, model_id)
            bucket["input_tokens"] += usage.billable_input_tokens
            bucket["output_tokens"] += usage.output_tokens
    return buckets


def summarise_arm(pairs: list[tuple[dict, CaseRun]]) -> dict[str, Any]:
    runs = [run for _, run in pairs]
    rows = [score_case(case, run) for case, run in pairs]

    total_cost = sum(run.cost_usd for run in runs)
    n = len(pairs)
    latencies = [run.total_usage.latency_ms for run in runs]

    return {
        "arm": None,
        "cases": n,
        "schema_valid_rate": sum(1 for r in rows if r["schema_valid"]) / n,
        "fields_correct_rate": sum(1 for r in rows if r["fields_correct"]) / n,
        "end_to_end_rate": sum(
            1 for r in rows if r["schema_valid"] and r["fields_correct"]
        )
        / n,
        "escalations": sum(1 for r in rows if r["escalated"]),
        "pii_leaks": sum(len(r["pii_leaked"]) for r in rows),
        "total_cost_usd": total_cost,
        "cost_per_case_usd": total_cost / n,
        "cost_per_1k_cases_usd": total_cost / n * 1000,
        "mean_latency_ms": statistics.fmean(latencies) if latencies else 0.0,
        "per_model": per_model_spend(runs),
    }


def print_report(summaries: dict[str, dict[str, Any]], prices_as_of: str) -> None:
    if not summaries:
        print("\nNo arms ran, so there is nothing to report.\n")
        return

    print(f"\nPrices as of {prices_as_of} (AWS Bedrock pricing page, {config.REGION}).\n")

    header = (
        f"{'arm':<15} {'schema':>7} {'fields':>7} {'e2e':>7} {'esc':>5} "
        f"{'$ / case':>10} {'$ / 1k cases':>13} {'mean ms':>9}"
    )
    print(header)
    print("-" * len(header))
    for arm, summary in summaries.items():
        print(
            f"{arm:<15} "
            f"{summary['schema_valid_rate']:>6.0%} "
            f"{summary['fields_correct_rate']:>7.0%} "
            f"{summary['end_to_end_rate']:>7.0%} "
            f"{summary['escalations']:>5} "
            f"{summary['cost_per_case_usd']:>10.6f} "
            f"{summary['cost_per_1k_cases_usd']:>13.2f} "
            f"{summary['mean_latency_ms']:>9.0f}"
        )

    if "always-strong" in summaries and "routed" in summaries:
        strong = summaries["always-strong"]
        routed = summaries["routed"]
        saved = 1 - (routed["total_cost_usd"] / strong["total_cost_usd"])
        delta = routed["end_to_end_rate"] - strong["end_to_end_rate"]
        print(
            f"\nrouted vs always-strong: "
            f"{saved:+.1%} cost, {delta:+.1%} end-to-end accuracy "
            f"({routed['escalations']}/{routed['cases']} cases escalated)."
        )
        print(
            "Read this as: the escalation rule costs the difference in accuracy, if any, "
            "and buys the difference in spend."
        )

    print("\nper-model spend")
    for arm, summary in summaries.items():
        print(f"  {arm}:")
        for model_id, bucket in summary["per_model"].items():
            print(
                f"    {model_id}\n"
                f"      calls {bucket['calls']:.0f}, "
                f"in {bucket['input_tokens']:.0f} tok, "
                f"out {bucket['output_tokens']:.0f} tok, "
                f"${bucket['cost_usd']:.6f}"
            )

    print(
        "\n'$ / 1k cases' is the measured total scaled by 50 — an extrapolation from "
        f"{list(summaries.values())[0]['cases']} cases, not a measurement. Say so when "
        "you quote it."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the three-arm cost experiment.")
    parser.add_argument(
        "--arms",
        nargs="+",
        choices=list(ARMS),
        default=list(ARMS),
        help="which arms to run (default: all three)",
    )
    parser.add_argument(
        "--guardrail",
        action="store_true",
        help="apply the input guardrail (guardrail text units are billed separately)",
    )
    args = parser.parse_args(argv)

    # The two Claude tiers are the design and must be priced. The third arm is optional: if its
    # rates have not been recorded yet, drop the arm with a note rather than refusing the run.
    try:
        pricing.assert_prices_loaded([config.CHEAP_MODEL_ID, config.STRONG_MODEL_ID])
    except pricing.PricesNotLoaded as exc:
        print(f"error: {exc}")
        return 2

    arms = list(args.arms)
    if "always-third" in arms:
        try:
            pricing.assert_prices_loaded([config.THIRD_MODEL_ID])
        except pricing.PricesNotLoaded:
            print(
                f"note: dropping the always-third arm — no prices recorded for "
                f"{config.THIRD_MODEL_ID}.\n"
                "      add PRICE_THIRD_INPUT and PRICE_THIRD_OUTPUT to .env to include it.\n"
            )
            arms = [arm for arm in arms if arm != "always-third"]

    client = make_client(offline=False)

    guardrail_cfg: dict[str, Any] | None = None
    if args.guardrail:
        guardrail_cfg = config.guardrail_config()
        if guardrail_cfg is None:
            print(
                "error: --guardrail requires GUARDRAIL_ID (run `make guardrail`)."
            )
            return 2

    report: dict[str, Any] = {
        "mode": "live-bedrock",
        "region": config.REGION,
        "guardrail": guardrail_cfg is not None,
        "prices_as_of": pricing.PRICES_AS_OF,
        "cheap_model_id": config.CHEAP_MODEL_ID,
        "strong_model_id": config.STRONG_MODEL_ID,
        "extrapolation_note": (
            "cost_per_1k_cases_usd is the measured total scaled by 50; it is an "
            "extrapolation from the case count in this run, not a measurement."
        ),
        "arms": {},
    }

    summaries: dict[str, dict[str, Any]] = {}
    for arm in arms:
        print(f"\n=== {arm} ===", flush=True)
        pairs = run_arm(client, arm, guardrail_cfg)
        summary = summarise_arm(pairs)
        summary["arm"] = arm
        summaries[arm] = summary
        report["arms"][arm] = {
            "summary": summary,
            "rows": [score_case(case, run) for case, run in pairs],
        }
        print(
            f"  e2e {summary['end_to_end_rate']:.0%} | "
            f"esc {summary['escalations']}/{summary['cases']} | "
            f"${summary['total_cost_usd']:.6f} total"
        )

    if not summaries:
        print(
            "\nNo arms ran. Every requested arm was dropped for a missing price, so there is "
            "nothing to measure.\nRecord the missing rates in .env and re-run.\n"
        )
        return 2

    print_report(summaries, pricing.PRICES_AS_OF)
    RESULTS_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
