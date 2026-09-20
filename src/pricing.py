"""Model prices.

Two ways to fill this in, and no third way:

1. **Environment variables** (what the Phase 0 wizard writes into `.env`):
       PRICES_AS_OF=2026-09-19
       PRICE_HAIKU_INPUT=1.00
       PRICE_HAIKU_OUTPUT=5.00
       PRICE_SONNET_INPUT=3.00
       PRICE_SONNET_OUTPUT=15.00
2. **Editing the table below by hand**, and setting `_FILE_PRICES_AS_OF`.

Until one of those is done, `assert_prices_loaded()` raises and nothing in this
repository will print a currency amount. A cost table built on remembered or borrowed
numbers is worse than no cost table: it is the one claim in the demo that a reviewer can
check in thirty seconds and find wrong.

Prices must be read off the AWS Bedrock pricing page in a browser. They cannot be fetched
programmatically: the page renders its tables with client-side JavaScript, and Claude
models on Bedrock are billed through AWS Marketplace under the model provider rather than
under the `AmazonBedrock` offer code.

**Do not generalise a multiplier between model families.** Bedrock's cache pricing is not
uniform — the Nova family is documented at 0.25x cache read with free cache writes, while
Anthropic's reference table uses 1.25x write / 0.1x read. Read the per-model rows.

**Record the right variant.** AWS documents that Bedrock price varies by *token type*, *service
tier* and *routing*, and that cross-region usage is reported under its own usage type. This
repository always routes through the `eu.` inference profile, so the rates recorded here must be
the **standard tier, on-demand, cross-region** rates — not batch, not priority, not flex. See
<https://docs.aws.amazon.com/bedrock/latest/userguide/cost-mgmt-understanding-cur-data.html>.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Loaded here as well as in src/config.py, because this module reads its own environment
# variables and must behave the same whether or not config was imported first.
load_dotenv()


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1,000,000 tokens. `None` means 'not yet looked up'."""

    input: float | None = None
    output: float | None = None
    cache_read: float | None = None
    cache_write_5m: float | None = None
    cache_write_1h: float | None = None

    def is_complete(self) -> bool:
        return self.input is not None and self.output is not None


PRICING_PAGE = "https://aws.amazon.com/bedrock/pricing/"

# Date on which the rates below were read off the pricing page for eu-north-1. An undated
# price is not evidence. Overridden by the PRICES_AS_OF environment variable.
_FILE_PRICES_AS_OF = ""

# The model ID -> environment-variable prefix mapping. If you change a model ID in
# src/config.py, add its prefix here and to .env.example, or pricing will refuse to run
# with a clear error naming the model it cannot find.
_ENV_PREFIXES = {
    "eu.anthropic.claude-haiku-4-5-20251001-v1:0": "PRICE_HAIKU",
    "eu.anthropic.claude-sonnet-4-5-20250929-v1:0": "PRICE_SONNET",
    "deepseek.v3.2": "PRICE_THIRD",
}

# Fill in from the pricing page for eu-north-1 if you prefer editing code to .env.
_FILE_PRICES: dict[str, ModelPrice] = {
    model_id: ModelPrice() for model_id in _ENV_PREFIXES
}


class PricesNotLoaded(RuntimeError):
    """Raised instead of quietly producing a wrong cost table."""


def _env_float(name: str) -> float | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise PricesNotLoaded(
            f"{name} is set in the environment but is not a number: {raw!r}. "
            "Fix it in .env or unset it."
        ) from exc


def _overlay_env(model_id: str, base: ModelPrice) -> ModelPrice:
    """Environment values win over the in-file table, field by field."""
    prefix = _ENV_PREFIXES.get(model_id)
    if not prefix:
        return base

    def pick(field: str, suffix: str):
        from_env = _env_float(f"{prefix}_{suffix}")
        return from_env if from_env is not None else getattr(base, field)

    return ModelPrice(
        input=pick("input", "INPUT"),
        output=pick("output", "OUTPUT"),
        cache_read=pick("cache_read", "CACHE_READ"),
        cache_write_5m=pick("cache_write_5m", "CACHE_WRITE_5M"),
        cache_write_1h=pick("cache_write_1h", "CACHE_WRITE_1H"),
    )


PRICES_AS_OF: str = (os.getenv("PRICES_AS_OF") or "").strip() or _FILE_PRICES_AS_OF

PRICES: dict[str, ModelPrice] = {
    model_id: _overlay_env(model_id, base) for model_id, base in _FILE_PRICES.items()
}


def assert_prices_loaded(model_ids: list[str] | None = None) -> None:
    """Fail loudly if any price needed for the run is missing.

    Called before the evaluation and before the cost experiment, so a run stops before it
    spends money it cannot account for.
    """
    if not PRICES_AS_OF:
        raise PricesNotLoaded(
            "No price date is set.\n"
            "Run `make phase0` (or set PRICES_AS_OF in .env), or set _FILE_PRICES_AS_OF in "
            "src/pricing.py.\n"
            f"The rates themselves come from {PRICING_PAGE} — region eu-north-1, read in a "
            "browser.\n"
            "No cost table is produced until this is done, on purpose: see the module "
            "docstring."
        )

    wanted = model_ids if model_ids is not None else list(PRICES)
    missing = [
        model_id
        for model_id in wanted
        if model_id not in PRICES or not PRICES[model_id].is_complete()
    ]
    if missing:
        raise PricesNotLoaded(
            "Missing input/output prices for:\n  - "
            + "\n  - ".join(missing)
            + "\n\nRun `make phase0` to be walked through reading them off "
            f"{PRICING_PAGE} (region eu-north-1), or edit src/pricing.py by hand."
        )


def price_for(model_id: str) -> ModelPrice:
    try:
        return PRICES[model_id]
    except KeyError as exc:
        raise PricesNotLoaded(
            f"no price row for model {model_id!r}. If you changed a model ID in "
            "src/config.py, add its prefix to _ENV_PREFIXES in src/pricing.py."
        ) from exc


__all__ = [
    "PRICES",
    "PRICES_AS_OF",
    "PRICING_PAGE",
    "ModelPrice",
    "PricesNotLoaded",
    "assert_prices_loaded",
    "price_for",
]
