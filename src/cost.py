"""Token and cost accounting for every model call.

The one non-obvious fact encoded here, and the reason this module exists rather than a
one-line multiplication at the call site:

    When prompt caching is enabled, `usage.inputTokens` reports ONLY the non-cached
    input tokens. The billable input is

        inputTokens + cacheReadInputTokens + cacheWriteInputTokens

    Costing from `inputTokens` alone understates the request. Both cache fields are
    marked optional in the API, so they can be absent as well as zero — hence the
    defensive coercion below rather than a bare `+`.

This demo does not enable prompt caching (see docs/DESIGN.md for why), but the
accounting is written for it, because the first person to add a `cachePoint` should not
also have to discover this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src import pricing


@dataclass
class Usage:
    """Normalised token usage for a single model call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0

    @property
    def billable_input_tokens(self) -> int:
        """Total input tokens the request is billed for."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def total_tokens(self) -> int:
        return self.billable_input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            latency_ms=self.latency_ms + other.latency_ms,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "billable_input_tokens": self.billable_input_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
        }


def _int(value: Any) -> int:
    """Coerce an optional API field to an int. Absent and 0 both mean 'none'."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def usage_from_response(response: dict[str, Any]) -> Usage:
    """Build a Usage from a Converse response.

    Reads `usage` and `metrics.latencyMs` exactly as the Converse API names them.
    """
    usage = response.get("usage") or {}
    metrics = response.get("metrics") or {}
    return Usage(
        input_tokens=_int(usage.get("inputTokens")),
        output_tokens=_int(usage.get("outputTokens")),
        cache_read_tokens=_int(usage.get("cacheReadInputTokens")),
        cache_write_tokens=_int(usage.get("cacheWriteInputTokens")),
        latency_ms=_int(metrics.get("latencyMs")),
    )


def cost_usd(usage: Usage, model_id: str) -> float:
    """Cost in USD for one call. Requires prices to have been loaded.

    Cache rates are only applied when a price row actually defines them; otherwise the
    cached tokens are charged at the standard input rate, which is the conservative
    choice (it never flatters the result).
    """
    price = pricing.price_for(model_id)
    assert price.input is not None and price.output is not None  # guarded upstream

    per_token_in = price.input / 1_000_000
    per_token_out = price.output / 1_000_000

    cost = usage.input_tokens * per_token_in
    cost += usage.output_tokens * per_token_out

    if price.cache_read is not None:
        cost += usage.cache_read_tokens * (price.cache_read / 1_000_000)
    else:
        cost += usage.cache_read_tokens * per_token_in

    if price.cache_write_5m is not None:
        cost += usage.cache_write_tokens * (price.cache_write_5m / 1_000_000)
    else:
        cost += usage.cache_write_tokens * per_token_in

    return cost


@dataclass
class CallRecord:
    """One model call inside one case."""

    case_id: str
    model_id: str
    usage: Usage
    cost: float
    escalated: bool = False


@dataclass
class CostLedger:
    """Accumulates calls so a run can report cost per case, per arm and in total."""

    records: list[CallRecord] = field(default_factory=list)

    def record(
        self,
        case_id: str,
        model_id: str,
        usage: Usage,
        escalated: bool = False,
        with_cost: bool = True,
    ) -> CallRecord:
        call = CallRecord(
            case_id=case_id,
            model_id=model_id,
            usage=usage,
            cost=cost_usd(usage, model_id) if with_cost else 0.0,
            escalated=escalated,
        )
        self.records.append(call)
        return call

    @property
    def total_cost(self) -> float:
        return sum(record.cost for record in self.records)

    @property
    def total_usage(self) -> Usage:
        total = Usage()
        for record in self.records:
            total = total + record.usage
        return total

    def by_model(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for record in self.records:
            bucket = out.setdefault(
                record.model_id,
                {"calls": 0, "cost": 0.0, "input_tokens": 0, "output_tokens": 0},
            )
            bucket["calls"] += 1
            bucket["cost"] += record.cost
            bucket["input_tokens"] += record.usage.billable_input_tokens
            bucket["output_tokens"] += record.usage.output_tokens
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_cost_usd": self.total_cost,
            "total_usage": self.total_usage.as_dict(),
            "by_model": self.by_model(),
            "records": [
                {
                    "case_id": r.case_id,
                    "model_id": r.model_id,
                    "cost_usd": r.cost,
                    "escalated": r.escalated,
                    **r.usage.as_dict(),
                }
                for r in self.records
            ],
        }


__all__ = [
    "CallRecord",
    "CostLedger",
    "Usage",
    "cost_usd",
    "usage_from_response",
]
