"""The structured-output contract, and a guard against Bedrock's schema restrictions.

One source of truth: the same Pydantic model generates the JSON Schema that Bedrock
compiles into a decoding grammar, AND validates what comes back. There is no second,
drifting definition of the contract.

Why `assert_bedrock_compatible` exists: Bedrock accepts a JSON Schema Draft 2020-12
*subset*. If you send a construct outside that subset the API returns HTTP 400 at
request time — in the middle of a 20-case evaluation run. The restrictions are
documented, so they can be asserted at import time instead of discovered in production.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from src import config


class Priority(StrEnum):
    """Priority as an enum, not a free string.

    Constrained decoding makes this genuinely enforced rather than merely requested,
    which is the difference between a schema and a suggestion.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Task(BaseModel):
    """One actionable item, shaped so it can be posted to Jira without editing."""

    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    priority: Priority
    owner: str
    source_quote: str


class Decision(BaseModel):
    """A decision the meeting actually reached — not a topic that was discussed."""

    model_config = ConfigDict(extra="forbid")

    statement: str
    made_by: str


class Risk(BaseModel):
    """A risk or blocker, with severity reusing the priority vocabulary."""

    model_config = ConfigDict(extra="forbid")

    description: str
    severity: Priority


class MeetingExtraction(BaseModel):
    """The top-level contract returned for every case."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    tasks: list[Task]
    decisions: list[Decision]
    risks: list[Risk]


# Keys that Bedrock documents as unsupported. Sending any of them is a 400.
_UNSUPPORTED_KEYS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "minProperties",
    "maxProperties",
    "uniqueItems",
    "patternProperties",
    "propertyNames",
    "unevaluatedProperties",
    "dependentRequired",
    "dependentSchemas",
    "if",
    "then",
    "else",
    "not",
    "contains",
}


def output_schema() -> dict[str, Any]:
    """The JSON Schema Bedrock will compile into a grammar."""
    return MeetingExtraction.model_json_schema()


def output_schema_json() -> str:
    """Bedrock takes the schema as a string, not as a JSON object."""
    return json.dumps(output_schema())


def assert_bedrock_compatible(schema: dict[str, Any] | None = None) -> list[str]:
    """Return a list of schema violations; empty means Bedrock will accept it.

    Called at import time by the agent so that an unsupported construct fails
    immediately and locally, with a readable message, instead of as an HTTP 400
    logged somewhere in the middle of a run.
    """
    schema = schema if schema is not None else output_schema()
    problems: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{path}.{key}"
                if key in _UNSUPPORTED_KEYS:
                    problems.append(f"{here}: documented as unsupported by Bedrock")
                elif key == "additionalProperties" and value is not False:
                    problems.append(
                        f"{here}: must be false or omitted, found {value!r}"
                    )
                elif key == "minItems" and value not in (0, 1):
                    problems.append(f"{here}: only 0 or 1 is supported, found {value!r}")
                elif key == "enum" and isinstance(value, list):
                    for item in value:
                        if isinstance(item, (dict, list)):
                            problems.append(f"{here}: enum values must be scalars")
                elif key == "$ref" and isinstance(value, str) and not value.startswith("#/"):
                    problems.append(f"{here}: external $ref is not supported ({value})")
                walk(value, here)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")

    walk(schema, "$")
    return problems


def validate_payload(payload: Any) -> tuple[MeetingExtraction | None, list[str]]:
    """Validate a decoded payload against the contract.

    Native structured outputs already constrain generation, so this should almost
    never fail. It runs anyway: 'almost never' is not a guarantee, and this is the
    check that turns a malformed response into an escalation rather than a crash.
    """
    try:
        return MeetingExtraction.model_validate(payload), []
    except ValidationError as exc:
        errors = [
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        ]
        return None, errors


def parse_and_validate(text: str) -> tuple[MeetingExtraction | None, list[str]]:
    """Decode the model's text as JSON, then validate it against the contract."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"response was not valid JSON: {exc}"]
    return validate_payload(payload)


def schema_name() -> str:
    """Schema name sent to Bedrock; also the grammar-cache key."""
    return config.SCHEMA_NAME


# Fail fast, at import, rather than at the first API call of the run. Pydantic also
# emits cosmetic keys (`title`, `default`) that Bedrock ignores; the checker in
# `assert_bedrock_compatible` only inspects the constructs that are actually
# restricted, so those decorations cannot mask a real violation.
_SCHEMA_PROBLEMS = assert_bedrock_compatible(output_schema())
if _SCHEMA_PROBLEMS:  # pragma: no cover - a build-time guard, not a runtime path
    raise RuntimeError(
        "The structured-output schema uses constructs Bedrock does not support; "
        "every request would fail with HTTP 400:\n  - "
        + "\n  - ".join(_SCHEMA_PROBLEMS)
    )


__all__ = [
    "Decision",
    "MeetingExtraction",
    "Priority",
    "Risk",
    "Task",
    "assert_bedrock_compatible",
    "output_schema",
    "output_schema_json",
    "parse_and_validate",
    "schema_name",
    "validate_payload",
]
