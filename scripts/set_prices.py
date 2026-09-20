#!/usr/bin/env python3
"""Record the Bedrock prices. By hand, on purpose, in two minutes.

The pricing page renders its tables with client-side JavaScript, and Claude models on
Bedrock are billed through AWS Marketplace under the model provider rather than under the
`AmazonBedrock` offer code — so the rates cannot be retrieved by any API this project has
access to. A human reads four numbers off a web page; everything else is automatic.

This script exists so that "read four numbers" is the *only* thing left to do, rather than
one stage of a longer wizard. It validates what you type, refuses values that are obviously
transposed, writes them to `.env` with today's date, and prints the resulting table.

**Three independent attempts to avoid that manual step all failed**, recorded here so nobody
repeats them:

1. `web_fetch` on the pricing page returns navigation only — the rate table is client-rendered.
2. The AWS MCP documentation search finds the pricing page, but returns only its prose and a
   worked example, never the table.
3. The AWS MCP documentation reader returns roughly 12,000 characters from that URL with **zero**
   occurrences of any model name. The table is not in the payload at all.

The only machine-readable source is `bedrock:ListFoundationModelAgreementOffers`, which returns the
rates from the agreement the account has already signed — `scripts/fetch_prices_from_offers.py`.
It needs one permission the running IAM policy does not yet carry; see docs/RESUME.md.

Usage:
    python scripts/set_prices.py                       # prompts for all four
    python scripts/set_prices.py --haiku-in 1 --haiku-out 5 --sonnet-in 3 --sonnet-out 15
    python scripts/set_prices.py --as-of 2026-09-19
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
PRICING_PAGE = "https://aws.amazon.com/bedrock/pricing/"


def _upsert(text: str, key: str, value: str) -> str:
    if re.search(rf"^{re.escape(key)}=", text, flags=re.M):
        return re.sub(rf"^{re.escape(key)}=.*$", f"{key}={value}", text, flags=re.M)
    if text and not text.endswith("\n"):
        text += "\n"
    return f"{text}{key}={value}\n"


def _ask(prompt: str, default: str | None = None) -> str:
    while True:
        raw = input(f"  {prompt}" + (f" [{default}]" if default else "") + ": ").strip()
        if not raw and default:
            return default
        if raw:
            return raw
        print("    a value is required")


def _as_price(raw: str, label: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise SystemExit(f"error: {label} is not a number: {raw!r}") from None
    if value <= 0:
        raise SystemExit(f"error: {label} must be greater than zero, got {value}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record the Bedrock token prices in .env.")
    parser.add_argument("--haiku-in", help="Haiku 4.5 input, USD per 1M tokens")
    parser.add_argument("--haiku-out", help="Haiku 4.5 output, USD per 1M tokens")
    parser.add_argument("--sonnet-in", help="Sonnet 4.5 input, USD per 1M tokens")
    parser.add_argument("--sonnet-out", help="Sonnet 4.5 output, USD per 1M tokens")
    parser.add_argument("--as-of", help="date the prices were read (default: today)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="skip the sanity warnings (transposed-looking values)",
    )
    args = parser.parse_args(argv)

    if not all([args.haiku_in, args.haiku_out, args.sonnet_in, args.sonnet_out]):
        print(
            f"\nRead the four rates off {PRICING_PAGE}\n"
            "with the region selector set to EU (Stockholm) / eu-north-1.\n"
            "\n"
            "Which variant to take, because the page lists several:\n"
            "  * STANDARD tier, ON-DEMAND — not batch (typically ~50% cheaper) and not a\n"
            "    priority or flex tier;\n"
            "  * the CROSS-REGION rate when the page distinguishes it. This repository always\n"
            "    routes through the `eu.` inference profile, and AWS reports cross-region usage\n"
            "    as its own usage type, so the in-region rate would understate or overstate it.\n"
            "\n"
            "If a rate is quoted per 1K tokens, multiply it by 1000.\n"
        )
        args.haiku_in = args.haiku_in or _ask("Haiku 4.5 input price per 1M tokens (USD)")
        args.haiku_out = args.haiku_out or _ask("Haiku 4.5 output price per 1M tokens (USD)")
        args.sonnet_in = args.sonnet_in or _ask("Sonnet 4.5 input price per 1M tokens (USD)")
        args.sonnet_out = args.sonnet_out or _ask("Sonnet 4.5 output price per 1M tokens (USD)")

    haiku_in = _as_price(args.haiku_in, "Haiku input")
    haiku_out = _as_price(args.haiku_out, "Haiku output")
    sonnet_in = _as_price(args.sonnet_in, "Sonnet input")
    sonnet_out = _as_price(args.sonnet_out, "Sonnet output")

    # Sanity checks, not correctness checks: they catch transposed pairs and mixed-up models,
    # which are the two mistakes that produce a plausible-looking table that is simply wrong.
    warnings: list[str] = []
    for label, in_price, out_price in (
        ("Haiku", haiku_in, haiku_out),
        ("Sonnet", sonnet_in, sonnet_out),
    ):
        if in_price >= out_price:
            warnings.append(
                f"{label}: input ({in_price}) is not cheaper than output ({out_price}); "
                "output rates are normally the higher of the two"
            )
    if haiku_in > sonnet_in and haiku_out > sonnet_out:
        warnings.append(
            f"Haiku ({haiku_in}/{haiku_out}) is priced above Sonnet "
            f"({sonnet_in}/{sonnet_out}); the cheap model is normally the cheaper one"
        )

    if warnings and not args.force:
        print("\n  these look wrong:")
        for warning in warnings:
            print(f"    ! {warning}")
        print(
            "\n  Re-run with --force if the page really says this, or fix the values.\n"
        )
        return 1

    import datetime

    as_of = args.as_of or datetime.date.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
        print(f"error: --as-of must be YYYY-MM-DD, got {as_of!r}")
        return 2

    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    for key, value in (
        ("PRICES_AS_OF", as_of),
        ("PRICE_HAIKU_INPUT", f"{haiku_in:g}"),
        ("PRICE_HAIKU_OUTPUT", f"{haiku_out:g}"),
        ("PRICE_SONNET_INPUT", f"{sonnet_in:g}"),
        ("PRICE_SONNET_OUTPUT", f"{sonnet_out:g}"),
    ):
        text = _upsert(text, key, value)
    ENV_PATH.write_text(text, encoding="utf-8")
    ENV_PATH.chmod(0o600)

    print(f"\n  ✓ wrote 5 values to .env, dated {as_of}\n")
    print(f"    {'model':<12} {'input':>8} {'output':>8}   per 1M tokens, USD")
    print(f"    {'-' * 12} {'-' * 8} {'-' * 8}")
    print(f"    {'Haiku 4.5':<12} {haiku_in:>8g} {haiku_out:>8g}")
    print(f"    {'Sonnet 4.5':<12} {sonnet_in:>8g} {sonnet_out:>8g}")
    if warnings:
        print("\n    (accepted with --force despite the warnings above)")
    print("\n  next:\n    make verify-aws ARGS=--invoke\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
