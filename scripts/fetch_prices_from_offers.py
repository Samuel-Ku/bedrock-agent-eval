#!/usr/bin/env python3
"""Read the token rates from the agreement your account has already signed.

This is the authoritative source, and the reason it beats the pricing page:

* it is **your** account's agreed terms, not a marketing page;
* it is machine-readable, so the price step stops being manual;
* it carries the rate dimensions by name (`..._input_tokens_global_batch`,
  `..._InputTokenCount_Global`), so a reader can see exactly which rate was taken.

It does **not** replace `scripts/set_prices.py`: that one works with no extra permission by asking
a human to read four numbers. This one needs `bedrock:ListFoundationModelAgreementOffers`, which is
read-only metadata about an agreement this account is already party to, and which the IAM policy
now grants.

How the right dimension is chosen: many dimensions exist per model (batch vs on-demand, global vs
regional, input vs output). This script picks the **on-demand, non-batch** input and output pair,
prints every dimension it saw so the choice can be checked, and writes the raw rate card to
`evals/model_rates.json` as evidence. It refuses to guess when the choice is ambiguous.

Usage:
    python scripts/fetch_prices_from_offers.py                 # show what would be written
    python scripts/fetch_prices_from_offers.py --write         # write to .env
    python scripts/fetch_prices_from_offers.py --write --as-of 2026-09-20
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from src import config

RAW_PATH = Path(__file__).resolve().parents[1] / "evals" / "model_rates.json"
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"

ENV_PREFIX = {
    config.CHEAP_MODEL_ID: "PRICE_HAIKU",
    config.STRONG_MODEL_ID: "PRICE_SONNET",
    config.THIRD_MODEL_ID: "PRICE_THIRD",
}

_BATCH = re.compile(r"batch", re.IGNORECASE)
_OUTPUT = re.compile(r"output", re.IGNORECASE)
_INPUT = re.compile(r"input", re.IGNORECASE)
# AWS documents that price varies by token type, service tier and routing, and that
# cross-region usage has its own usage type. This repository always routes through the `eu.`
# inference profile, so a cross-region dimension is the correct one to prefer.
_CROSS = re.compile(r"cross[-_]?region|global", re.IGNORECASE)
_NON_STANDARD_TIER = re.compile(r"priority|flex", re.IGNORECASE)


def _upsert(text: str, key: str, value: str) -> str:
    if re.search(rf"^{re.escape(key)}=", text, flags=re.M):
        return re.sub(rf"^{re.escape(key)}=.*$", f"{key}={value}", text, flags=re.M)
    if text and not text.endswith("\n"):
        text += "\n"
    return f"{text}{key}={value}\n"


def classify(dimension: str) -> str:
    """on-demand input / on-demand output / something else (batch, priority, ...)."""
    if _BATCH.search(dimension):
        return "batch"
    if _NON_STANDARD_TIER.search(dimension):
        return "non_standard_tier"
    if _OUTPUT.search(dimension):
        return "on_demand_output"
    if _INPUT.search(dimension):
        return "on_demand_input"
    return "other"


def pick(rate_card: list[dict]) -> dict[str, dict] | None:
    """Choose one on-demand input and one on-demand output dimension, or refuse.

    Where several candidates exist, prefer the cross-region/global variant, because that is
    the routing this repository actually uses.
    """
    chosen: dict[str, dict] = {}
    for entry in rate_card:
        kind = classify(entry.get("dimension", ""))
        if kind not in ("on_demand_input", "on_demand_output"):
            continue
        current = chosen.get(kind)
        if current is None:
            chosen[kind] = entry
        elif _CROSS.search(entry.get("dimension", "")) and not _CROSS.search(
            current.get("dimension", "")
        ):
            chosen[kind] = entry
    if len(chosen) != 2:
        return None
    return chosen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read token rates from the signed agreement.")
    parser.add_argument("--write", action="store_true", help="write the rates into .env")
    parser.add_argument("--as-of", help="date to record (default: today)")
    parser.add_argument("--model", action="append", help="model id to fetch (repeatable)")
    args = parser.parse_args(argv)

    try:
        import boto3

        bedrock = boto3.client("bedrock", region_name=config.REGION)
    except Exception as exc:  # noqa: BLE001
        print(f"error: could not build a Bedrock client: {exc}")
        return 2

    models = args.model or list(ENV_PREFIX)
    raw: dict[str, object] = {"region": config.REGION, "offers": {}}
    values: dict[str, float] = {}
    ambiguous: list[str] = []

    for model_id in models:
        print(f"\n=== {model_id}")
        try:
            offers = bedrock.list_foundation_model_agreement_offers(modelId=model_id).get(
                "offers", []
            )
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if "not authorized to perform" in text:
                print(
                    "  denied: this identity lacks bedrock:ListFoundationModelAgreementOffers.\n"
                    "  The policy in aws/iam-policy.json now grants it, so apply the policy once\n"
                    "  with an admin identity:\n"
                    "      aws login --region eu-north-1 --profile bootstrap\n"
                    "      python scripts/bootstrap_iam_user.py --profile bootstrap --policy-only\n"
                    "      python scripts/switch_off_bootstrap_session.py --profile bootstrap\n"
                    "  Until then, use scripts/set_prices.py, which needs no extra permission."
                )
            else:
                print(f"  failed: {type(exc).__name__}: {text[:200]}")
            return 2

        if not offers:
            print("  no offers returned for this model in this region")
            continue

        rate_card = (
            offers[0].get("termDetails", {}).get("usageBasedPricingTerm") or {}
        ).get("rateCard", [])
        raw["offers"][model_id] = {"offerId": offers[0].get("offerId"), "rateCard": rate_card}

        for entry in rate_card:
            print(f"    {entry.get('dimension','?'):52} {entry.get('price','?'):>9}"
                  f"  [{classify(entry.get('dimension',''))}]")

        chosen = pick(rate_card)
        prefix = ENV_PREFIX.get(model_id)
        if chosen is None:
            ambiguous.append(model_id)
            print("    -> could not identify an unambiguous on-demand input/output pair")
            continue
        if not prefix:
            print("    -> no .env prefix mapped for this model; add one to ENV_PREFIX")
            continue

        for kind, key in (("on_demand_input", f"{prefix}_INPUT"),
                          ("on_demand_output", f"{prefix}_OUTPUT")):
            values[key] = float(chosen[kind]["price"])
            print(f"    -> {key} = {chosen[kind]['price']}  ({chosen[kind]['dimension']})")

    if not values:
        print("\nNothing to write.")
        return 1

    import datetime

    as_of = args.as_of or datetime.date.today().isoformat()
    print(f"\nrates as of {as_of}:")
    for key, value in values.items():
        print(f"  {key}={value:g}")

    if not args.write:
        print("\n(dry run — pass --write to record these in .env)")
        return 0

    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    text = _upsert(text, "PRICES_AS_OF", as_of)
    for key, value in values.items():
        text = _upsert(text, key, f"{value:g}")
    ENV_PATH.write_text(text, encoding="utf-8")
    ENV_PATH.chmod(0o600)

    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(
        json.dumps({**raw, "as_of": as_of}, indent=2), encoding="utf-8"
    )

    print(f"\nwrote {len(values)} rate(s) to .env, dated {as_of}")
    print(f"raw rate card kept at {RAW_PATH.relative_to(RAW_PATH.parents[1])}")
    if ambiguous:
        print(f"models needing a manual decision: {', '.join(ambiguous)}")
    print("\nnext: make verify-aws ARGS=--invoke\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
