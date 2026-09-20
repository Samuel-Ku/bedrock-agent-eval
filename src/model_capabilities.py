"""What each model on this account can actually do.

Reads `evals/model_capabilities.json`, written by `scripts/probe_structured_outputs.py`. The
data is per model **per region per account**, because that is how Bedrock works: a model can
exist in the catalogue and still reject `outputConfig`.

Three states, and the difference between them is the point:

    True   probed and supported
    False  probed and rejected — the caller must not send `outputConfig`
    None   unknown (never probed, or probing was blocked)

`None` is not `False`. Collapsing them would either send an unsupported parameter and fail every
case, or silently drop schema enforcement from a model that supports it — and both of those
produce a pass rate that looks like a model result and is actually a harness bug.

This module exists because the repository's central claim is that the answer is
*schema-constrained rather than parsed*. That claim is only true for models that support it, so
the agent has to know which ones do and say which mode it used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CAPABILITIES_PATH = Path(__file__).resolve().parents[1] / "evals" / "model_capabilities.json"


def load() -> dict[str, Any]:
    """The probe results, or an empty structure when nothing has been probed yet."""
    if not CAPABILITIES_PATH.exists():
        return {"region": None, "probed_at": None, "models": {}}
    try:
        return json.loads(CAPABILITIES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"region": None, "probed_at": None, "models": {}, "error": "unreadable"}


def _flag(model_id: str, key: str) -> bool | None:
    entry = load().get("models", {}).get(model_id)
    if not entry:
        return None
    value = (entry.get(key) or {}).get("ok")
    return value if isinstance(value, bool) else None


def native_structured_outputs(model_id: str) -> bool | None:
    """Whether `outputConfig.textFormat` works for this model. None means unknown."""
    return _flag(model_id, "native_structured_outputs")


def tool_use(model_id: str) -> bool | None:
    """Whether `toolConfig` works for this model. None means unknown."""
    return _flag(model_id, "tool_use")


def invocable(model_id: str) -> bool | None:
    """Whether the model can be called at all right now."""
    return _flag(model_id, "invoke")


def describe(model_id: str) -> str:
    """A one-line human summary, for logs and reports."""
    entry = load().get("models", {}).get(model_id)
    if not entry:
        return "not probed"

    def mark(key: str) -> str:
        value = (entry.get(key) or {}).get("ok")
        return {True: "yes", False: "no", None: "?"}.get(value, "?")

    reason = (entry.get("invoke") or {}).get("reason")
    suffix = f" ({reason})" if reason else ""
    return (
        f"invoke={mark('invoke')} native_schema={mark('native_structured_outputs')} "
        f"tools={mark('tool_use')}{suffix}"
    )


def probed_at() -> str | None:
    return load().get("probed_at")


__all__ = [
    "CAPABILITIES_PATH",
    "describe",
    "invocable",
    "load",
    "native_structured_outputs",
    "probed_at",
    "tool_use",
]
