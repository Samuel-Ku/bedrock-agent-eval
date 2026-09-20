#!/usr/bin/env python3
"""Turn the recorded runs into the exact table rows the README is waiting for.

The repository's position is that no number appears in a document unless a run produced it.
This script is the other half of that: once the runs exist, the numbers are copied across by a
program rather than by eye, so a table cannot drift from its evidence.

Reads whatever has been recorded and prints markdown rows. Missing inputs are reported as
missing, never as zero.

    evals/results-guardrail-off.json   from `make evals` with the guardrail off
    evals/results-guardrail-on.json    from `make evals --guardrail on` (optional)
    experiments/results.json           from `make compare`

Usage:
    python scripts/summarise_results.py
    python scripts/summarise_results.py --check   # exit non-zero if anything is missing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVALS_OFF = ROOT / "evals" / "results-guardrail-off.json"
EVALS_ON = ROOT / "evals" / "results-guardrail-on.json"
COMPARE = ROOT / "experiments" / "results.json"
LEGACY = ROOT / "evals" / "results.json"

MISSING = "TBD"


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _pct(value: float | None) -> str:
    return MISSING if value is None else f"{value:.0%}"


def _mean(rows: list[dict], key: str) -> str:
    if not rows:
        return MISSING
    values = [row.get(key) or 0 for row in rows]
    return f"{sum(values) / len(values):.2f}"


def evaluation_rows(report: dict | None) -> list[str]:
    if not report:
        return ["| Metric | Value |", "|---|---|", f"| Cases | {MISSING} |"]
    arms = report.get("arms") or {}
    arm = next(iter(arms.values()), None)
    if not arm:
        return ["| Metric | Value |", "|---|---|", f"| Cases | {MISSING} |"]
    summary, rows = arm["summary"], arm.get("rows", [])

    cost = summary.get("cost_usd")
    return [
        "| Metric | Value |",
        "|---|---|",
        f"| Cases | {summary.get('cases', MISSING)} |",
        f"| Schema-valid rate | {_pct(summary.get('schema_valid_rate'))} |",
        f"| Fields-correct rate | {_pct(summary.get('fields_correct_rate'))} |",
        f"| End-to-end rate | {_pct(summary.get('end_to_end_rate'))} |",
        f"| Mean tool calls per case | {_mean(rows, 'tool_calls')} |",
        f"| Mean iterations per case | {_mean(rows, 'iterations')} |",
        f"| Cases that escalated | {summary.get('escalated', MISSING)} |",
        "| Source quotes grounded in the transcript | "
        f"{summary.get('quotes_grounded', MISSING)} / {summary.get('quotes_total', MISSING)} |",
        f"| Mode | {report.get('mode', MISSING)} |",
        f"| Prices as of | {report.get('prices_as_of') or MISSING} |",
        f"| Cost (USD, list price) | {f'{cost:.6f}' if cost is not None else MISSING} |",
    ]


def cost_rows(report: dict | None) -> list[str]:
    header = (
        "| Arm | Schema-valid | Fields-correct | End-to-end | Escalations | $ / case "
        "| $ / 1k cases | Mean latency |"
    )
    divider = "|---|---|---|---|---|---|---|---|"
    if not report:
        return [header, divider, f"| always-cheap | {MISSING} | {MISSING} | {MISSING} | 0 | "
                                 f"{MISSING} | {MISSING} | {MISSING} |"]
    out = [header, divider]
    for arm, payload in (report.get("arms") or {}).items():
        summary = payload["summary"]
        out.append(
            f"| {arm} | {_pct(summary.get('schema_valid_rate'))} "
            f"| {_pct(summary.get('fields_correct_rate'))} "
            f"| {_pct(summary.get('end_to_end_rate'))} "
            f"| {summary.get('escalations', MISSING)} "
            f"| {summary.get('cost_per_case_usd', 0):.6f} "
            f"| {summary.get('cost_per_1k_cases_usd', 0):.2f} "
            f"| {summary.get('mean_latency_ms', 0):.0f} |"
        )
    return out


def guardrail_rows(off: dict | None, on: dict | None) -> list[str]:
    out = [
        "| Input guardrail | PII leaking into the output | End-to-end rate |",
        "|---|---|---|",
    ]
    for label, report in (("off", off), ("on", on)):
        if not report:
            out.append(f"| {label} | {MISSING} | {MISSING} |")
            continue
        arm = next(iter((report.get("arms") or {}).values()), None)
        if not arm:
            out.append(f"| {label} | {MISSING} | {MISSING} |")
            continue
        summary = arm["summary"]
        out.append(
            f"| {label} | {summary.get('pii_leaks', MISSING)} "
            f"| {_pct(summary.get('end_to_end_rate'))} |"
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit README table rows from recorded runs.")
    parser.add_argument("--check", action="store_true", help="exit non-zero if data is missing")
    args = parser.parse_args(argv)

    off = _load(EVALS_OFF) or _load(LEGACY)
    on = _load(EVALS_ON)
    compare = _load(COMPARE)

    # The offline check comes first and is printed above the tables. It used to sit after the
    # missing-inputs branch, which returned early — so a stub run's numbers were printed with no
    # warning at all, which is precisely the failure this repository exists to prevent.
    offline = bool(off and off.get("mode") == "offline-stub")
    if offline:
        print("\n" + "!" * 78)
        print("WARNING: the recorded evaluation is an OFFLINE run.")
        print("Its pass rates describe the harness, not any model, and its cost is suppressed.")
        print("Do NOT paste these numbers into the README as results.")
        print("!" * 78)

    print("\n### Evaluation\n")
    print("\n".join(evaluation_rows(off)))
    print("\n### The cost experiment\n")
    print("\n".join(cost_rows(compare)))
    print("\n### Guardrail effect\n")
    print("\n".join(guardrail_rows(off, on)))

    missing = []
    if off is None:
        missing.append(str(EVALS_OFF.relative_to(ROOT)))
    if compare is None:
        missing.append(str(COMPARE.relative_to(ROOT)))
    if on is None:
        missing.append(f"{EVALS_ON.relative_to(ROOT)} (optional)")

    print()
    if missing:
        print("missing inputs:")
        for item in missing:
            print(f"  - {item}")
        print("\nrun `make live` to produce them, or fill the tables by hand from a run you trust.")
        return 1 if args.check else 0

    if offline:
        return 1 if args.check else 0

    print("all inputs present — these rows are ready to paste into README.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
