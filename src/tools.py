"""The agent's tools: deterministic, side-effect free, and cheap to evaluate.

Design rules, all three deliberate:

1.  **No side effects.** Nothing here writes to Jira. The agent proposes; a human
    disposes. That keeps the evaluation deterministic and keeps the demo honest about
    what an agent should be allowed to do unattended.

2.  **Deterministic.** Every tool reads from the case fixture. Given the same case,
    the same call returns the same value, so a failing eval means the model failed —
    not that a tool was flaky.

3.  **Declared schemas.** Each tool's inputs are described by a JSON Schema and passed
    to Bedrock in `toolConfig.tools[].toolSpec.inputSchema.json`, so malformed tool
    arguments are the model's error, and are detectable.
"""

from __future__ import annotations

import json
from typing import Any

from src.cases import get_case

# Tool names must match [a-zA-Z0-9_-]+ per the Converse API.
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "toolSpec": {
            "name": "get_project_metadata",
            "description": (
                "Return the project key, sprint and team for a case. Use this before "
                "finalising output so tasks can be attributed to the right project."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "case_id": {
                            "type": "string",
                            "description": "The case identifier, e.g. 'case-01'.",
                        }
                    },
                    "required": ["case_id"],
                    "additionalProperties": False,
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "find_existing_issue",
            "description": (
                "Check whether a task with a similar title already exists in the "
                "project. Returns the existing issue key when it does. Use this to "
                "avoid proposing duplicate work."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "case_id": {"type": "string"},
                        "title": {
                            "type": "string",
                            "description": "The proposed task title to search for.",
                        },
                    },
                    "required": ["case_id", "title"],
                    "additionalProperties": False,
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_transcript",
            "description": (
                "Return the full meeting transcript for a case. The transcript is "
                "already in the prompt; call this only if you need to re-read it."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"case_id": {"type": "string"}},
                    "required": ["case_id"],
                    "additionalProperties": False,
                }
            },
        }
    },
]


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def get_project_metadata(case_id: str) -> dict[str, Any]:
    case = get_case(case_id)
    return {
        "project_key": case["project_key"],
        "sprint": case["sprint"],
        "team": case["team"],
    }


def find_existing_issue(case_id: str, title: str) -> dict[str, Any]:
    case = get_case(case_id)
    wanted = _normalise(title)
    for existing in case.get("existing_issues", []):
        known = _normalise(existing["title"])
        # Deliberately simple and explainable: a token-overlap test, not a model call.
        # Two tools that both call an LLM would make the eval unreadable.
        if wanted == known or wanted in known or known in wanted:
            return {"exists": True, "key": existing["key"], "title": existing["title"]}
    return {"exists": False, "key": None, "title": None}


def get_transcript(case_id: str) -> dict[str, Any]:
    case = get_case(case_id)
    return {"transcript": case["transcript"]}


_DISPATCH = {
    "get_project_metadata": get_project_metadata,
    "find_existing_issue": find_existing_issue,
    "get_transcript": get_transcript,
}


def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run a tool and always return a JSON-serialisable dict.

    Errors are returned as data rather than raised: the model gets to see the failure
    and adapt, which is what makes the loop an agent rather than a script. The
    exception text is included so a reviewer can see what went wrong.
    """
    handler = _DISPATCH.get(name)
    if handler is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return handler(**arguments)
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}
    except KeyError as exc:
        return {"error": f"unknown case: {exc}"}


def tool_specs_json() -> str:
    return json.dumps(TOOL_SPECS)


__all__ = ["TOOL_SPECS", "execute_tool", "tool_specs_json"]
