"""Configuration: region, model IDs, limits.

Every value here is overridable from the environment, because the same code has to
run in three modes: offline stub, live Bedrock, and a cost experiment that swaps
model IDs deliberately.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# --- Region and inference profiles -------------------------------------------------
#
# eu-north-1 (Stockholm) is the target region. For Claude Haiku 4.5 and Sonnet 4.5
# the model cards state that in-region on-demand is NOT available on bedrock-runtime:
# only the geographic and global inference profiles are. The `eu.` prefix selects the
# EU geographic profile, which keeps data inside EU regions and is documented as
# priced at source-region rates with no cross-region surcharge.
#
# The trade-off this creates is deliberate and is written up in the README: the
# in-region path (bedrock-mantle, Stockholm) does NOT support native structured
# outputs, so choosing schema enforcement chooses cross-region routing.
REGION = os.getenv("AWS_REGION", "eu-north-1")

CHEAP_MODEL_ID = os.getenv(
    "CHEAP_MODEL_ID", "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
)
STRONG_MODEL_ID = os.getenv(
    "STRONG_MODEL_ID", "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
)

# A third, cheaper model, used as an extra arm in the cost experiment. This is the
# cost-optimisation question the posting asks about, measured against the two Claude tiers
# rather than asserted. DeepSeek V3.2 is ON_DEMAND in eu-north-1, so unlike the Claude 4.5
# models it needs no cross-region inference profile — which makes it a useful contrast.
#
# It may not support native structured outputs. `src/model_capabilities.py` holds the probe
# result, and the agent degrades to a stated-in-the-prompt contract when it does not, rather
# than failing every case.
THIRD_MODEL_ID = os.getenv("THIRD_MODEL_ID", "deepseek.v3.2")

# --- Guardrails --------------------------------------------------------------------
# Applied to the input prompt. Created by scripts/create_guardrail.py.
GUARDRAIL_ID = os.getenv("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "DRAFT")


def guardrail_config() -> dict | None:
    """The `guardrailConfig` block for Converse, or None when no guardrail is set."""
    if not GUARDRAIL_ID:
        return None
    return {"guardrailIdentifier": GUARDRAIL_ID, "guardrailVersion": GUARDRAIL_VERSION}


# --- Limits ------------------------------------------------------------------------
# A hard iteration cap is the difference between a bug and a bill. The cheap model is
# the one that can loop, so the cap is enforced in the agent loop, not by hope.
MAX_TOOL_ITERATIONS = int(os.getenv("MAX_TOOL_ITERATIONS", "6"))

# Name of the structured-output schema. Bedrock compiles the schema into a grammar and
# caches that grammar for 24h; changing the schema structure invalidates it, so the
# schema is built once and reused for every case.
SCHEMA_NAME = "meeting_actions"
SCHEMA_DESCRIPTION = (
    "Actionable tasks, decisions and risks extracted from a project meeting "
    "transcript, ready to become Jira issues."
)

# Inference parameters. temperature=0 because the whole point is a deterministic
# extraction that can be regression-tested.
TEMPERATURE = float(os.getenv("TEMPERATURE", "0"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "2048"))
