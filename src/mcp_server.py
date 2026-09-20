"""The agent as an MCP server.

Why MCP at all: the extraction is more useful as a *capability other agents can call* than
as a script a human remembers to run. That is also exactly what the target role asks for —
"experience with working with Model Context Protocol (MCP) servers" — and this repository
builds one rather than only consuming one.

Four decisions shape this file, and each is worth defending out loud.

**The surface is read-only apart from one tool.** Nothing here writes to Jira, to S3, or
to any external system. An agent-facing API that can mutate state is a much larger
security conversation than this demo can honestly have, so it does not have one. The single
action tool extracts; everything else reads.

**HTTP fails closed.** Running the streamable-HTTP transport without a bearer token is a
startup error, not a warning. A local stdio process has no network surface and needs no
token; a listening socket does. The failure mode this prevents — a demo MCP server left
running on a laptop, reachable, unauthenticated — is common enough to be worth one
explicit branch.

**Every response carries `mode`.** The server can answer from the offline stub when no AWS
credentials are present, which is what makes it demonstrable in a meeting. Stub answers
also carry a `warning` field, because the one thing this project must never do is let
simulated output travel as if it were measured.

**No secrets on the command line.** The token is read from `MCP_BEARER_TOKEN` in the
environment. Arguments are visible in `ps` and in shell history; environment variables are
not.
"""

from __future__ import annotations

import os
import secrets
import uuid
from typing import Any

from src import config, pricing
from src.agent import CaseRun, run_case, run_case_routed
from src.bedrock_client import ConverseError, make_client
from src.cases import all_cases, forget_runtime_case, get_case, register_runtime_case
from src.schema import output_schema

SERVER_NAME = "bedrock-agent-eval"
SERVER_INSTRUCTIONS = (
    "Extracts actionable items from project meeting transcripts. Use extract_actions to "
    "turn a transcript into structured tasks, decisions and risks. Read-only helpers "
    "expose the output contract, the evaluation fixtures and the last evaluation summary. "
    "Nothing here writes to any external system."
)

TOKEN_ENV_VAR = "MCP_BEARER_TOKEN"
_DEFAULT_HTTP_PATH = "/mcp"


# --------------------------------------------------------------------------------------
# Credentials and mode
# --------------------------------------------------------------------------------------
def has_aws_credentials() -> bool:
    """Whether boto3 can find credentials right now.

    Used only to choose between the live client and the stub in `auto` mode. It is a
    presence check, not an authorisation check: the API will still reject bad
    credentials, and this cannot make a bad credential good.
    """
    try:
        import boto3

        return boto3.Session().get_credentials() is not None
    except Exception:  # noqa: BLE001 - any failure means "assume no credentials"
        return False


def resolve_mode(mode: str) -> str:
    """Turn a requested mode into `live-bedrock` or `offline-stub`."""
    if mode not in {"auto", "live", "offline"}:
        raise ValueError(f"mode must be auto, live or offline; got {mode!r}")
    if mode == "offline":
        return "offline-stub"
    if mode == "live":
        return "live-bedrock"
    return "live-bedrock" if has_aws_credentials() else "offline-stub"


# --------------------------------------------------------------------------------------
# Core operations, shared by the MCP tools and the CLI
# --------------------------------------------------------------------------------------
def extract(
    transcript: str,
    case_id: str | None = None,
    model: str = "routed",
    mode: str = "auto",
) -> dict[str, Any]:
    """Run the agent on a transcript and return the result plus its provenance.

    The provenance is not decoration. A caller that cannot tell a stub answer from a
    measured one will eventually quote the wrong one.
    """
    resolved = resolve_mode(mode)
    offline = resolved == "offline-stub"

    # Give the tools something to resolve. A caller-supplied transcript is not a fixture,
    # so it is registered under a unique id for the duration of the call.
    registered_id: str | None = None
    if case_id is None:
        case_id = f"adhoc-{uuid.uuid4().hex[:8]}"
    try:
        case = get_case(case_id)
    except KeyError:
        case = {
            "id": case_id,
            "project_key": "ADHOC",
            "sprint": 0,
            "team": "Ad-hoc transcript",
            "transcript": transcript,
            "existing_issues": [],
            "expected": {"tasks": [], "decisions": [], "risks": []},
            "pii": [],
        }
        register_runtime_case(case)
        registered_id = case_id
    else:
        # A known fixture: honour the caller's transcript when they supplied one.
        if transcript and transcript != case["transcript"]:
            case = {**case, "transcript": transcript}

    try:
        client = make_client(offline=offline)
        guardrail = config.guardrail_config()
        if model == "cheap":
            run = run_case(client, case, config.CHEAP_MODEL_ID, guardrail=guardrail)
        elif model == "strong":
            run = run_case(client, case, config.STRONG_MODEL_ID, guardrail=guardrail)
        elif model == "routed":
            run = run_case_routed(client, case, guardrail=guardrail)
        else:
            raise ValueError(f"model must be cheap, strong or routed; got {model!r}")
    except ConverseError as exc:
        return {"mode": resolved, "error": str(exc), "valid": False}
    finally:
        if registered_id:
            forget_runtime_case(registered_id)

    return _present(run, resolved)


def _present(run: CaseRun, mode: str) -> dict[str, Any]:
    """Shape a CaseRun into a JSON-safe response, with its provenance attached."""
    usage = run.total_usage
    payload = run.payload.model_dump(mode="json") if run.payload else None

    response: dict[str, Any] = {
        "mode": mode,
        "model_id": run.model_id,
        "valid": run.valid,
        "result": payload,
        "errors": list(run.parse_errors),
        "tool_calls": run.tool_calls,
        "tool_arg_errors": run.tool_arg_errors,
        "iterations": run.iterations,
        "escalated": any(
            event["stage"] == "strong_after_escalation" for event in run.escalations
        ),
        "escalation_trace": run.escalations,
        "usage": usage.as_dict(),
    }

    if mode == "offline-stub":
        response["warning"] = (
            "OFFLINE STUB: the transcript came from an arbitrary caller but the answer "
            "came from a deterministic stub, and the token counts are fabricated. This "
            "demonstrates plumbing, not model behaviour. Do not report these numbers."
        )
        response["cost_usd"] = None
    else:
        try:
            pricing.assert_prices_loaded([run.model_id])
            response["cost_usd"] = run.cost_usd
            response["prices_as_of"] = pricing.PRICES_AS_OF
        except pricing.PricesNotLoaded:
            response["cost_usd"] = None
            response["cost_note"] = (
                "prices are not loaded in src/pricing.py, so no cost is reported"
            )

    return response


def list_cases() -> list[dict[str, Any]]:
    """The evaluation fixtures, without their transcripts."""
    return [
        {
            "id": case["id"],
            "project_key": case["project_key"],
            "team": case["team"],
            "sprint": case["sprint"],
            "expected_tasks": len(case["expected"]["tasks"]),
            "expected_decisions": len(case["expected"]["decisions"]),
            "expected_risks": len(case["expected"]["risks"]),
            "has_planted_pii": bool(case.get("pii")),
            "strict_extras": bool(case.get("strict_extras", True)),
        }
        for case in all_cases()
    ]


def contract() -> dict[str, Any]:
    """The output contract, and whether Bedrock will accept it."""
    from src.schema import assert_bedrock_compatible

    problems = assert_bedrock_compatible()
    return {
        "name": config.SCHEMA_NAME,
        "description": config.SCHEMA_DESCRIPTION,
        "bedrock_compatible": not problems,
        "bedrock_schema_problems": problems,
        "schema": output_schema(),
    }


def evaluation_summary() -> dict[str, Any]:
    """The last local evaluation run, if one has been recorded."""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "evals" / "results.json"
    if not path.exists():
        return {
            "available": False,
            "note": "no run recorded yet; run `python -m evals.run_evals` first",
        }
    report = json.loads(path.read_text(encoding="utf-8"))
    arms = {
        arm: data["summary"] for arm, data in report.get("arms", {}).items()
    }
    out: dict[str, Any] = {
        "available": True,
        "mode": report.get("mode"),
        "region": report.get("region"),
        "guardrail": report.get("guardrail"),
        "prices_as_of": report.get("prices_as_of"),
        "arms": arms,
    }
    if report.get("mode") == "offline-stub":
        out["warning"] = (
            "the recorded run is an offline plumbing test; its pass rates describe the "
            "harness, not any model"
        )
    return out


# --------------------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------------------
def build_verifier(expected_token: str):
    """A constant-time static bearer-token verifier.

    `secrets.compare_digest` rather than `==`: string comparison short-circuits on the
    first differing byte, which leaks token length and prefix through timing. For a
    demo token it hardly matters; the habit is the point.
    """
    from fastmcp.server.auth import AccessToken, TokenVerifier

    class StaticBearerVerifier(TokenVerifier):
        async def verify_token(self, token: str) -> AccessToken | None:
            if not token or not secrets.compare_digest(token, expected_token):
                return None
            return AccessToken(token=token, client_id="bearer", scopes=["extract"])

    return StaticBearerVerifier()


# --------------------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------------------
def build_server(bearer_token: str | None = None):
    """Construct the MCP server. Transport-agnostic on purpose."""
    try:
        from fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError(
            "fastmcp is not installed. Install the MCP extra:\n"
            '    uv pip install -e ".[mcp]"\n'
            "Everything except the MCP server works without it."
        ) from exc

    auth = build_verifier(bearer_token) if bearer_token else None
    server = FastMCP(
        name=SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        auth=auth,
    )

    @server.tool(
        description=(
            "Extract the actionable items from one project meeting transcript: tasks with "
            "priority and owner, decisions, and risks. Returns the structured result plus "
            "its provenance in `mode` — 'live-bedrock' means a real model produced it, "
            "'offline-stub' means it is plumbing output and must not be reported as a "
            "result. Nothing is written anywhere."
        )
    )
    def extract_actions(
        transcript: str,
        case_id: str | None = None,
        model: str = "routed",
        mode: str = "auto",
    ) -> dict[str, Any]:
        return extract(transcript, case_id=case_id, model=model, mode=mode)

    @server.tool(
        description=(
            "List the twenty evaluation fixtures with their expected item counts." 
        )
    )
    def list_evaluation_cases() -> list[dict[str, Any]]:
        return list_cases()

    @server.tool(
        description=(
            "Return one evaluation fixture in full: transcript, expected tasks, decisions "
            "and risks, and the identifiers planted in it for the PII-leak check."
        )
    )
    def get_evaluation_case(case_id: str) -> dict[str, Any]:
        try:
            return get_case(case_id)
        except KeyError as exc:
            return {"error": str(exc)}

    @server.tool(
        description=(
            "Return the structured-output contract the extractor must satisfy, and "
            "whether it passes Bedrock's documented schema restrictions."
        )
    )
    def output_contract() -> dict[str, Any]:
        return contract()

    @server.tool(
        description=(
            "Return the summary of the most recently recorded local evaluation run, "
            "including its mode — an offline run describes the harness, not a model."
        )
    )
    def evaluation_summary_tool() -> dict[str, Any]:
        return evaluation_summary()

    @server.resource(
        "contract://meeting-actions",
        name="meeting-actions-contract",
        description="The JSON Schema the extractor must satisfy.",
        mime_type="application/json",
    )
    def contract_resource() -> str:
        import json

        return json.dumps(output_schema(), indent=2)

    return server


def serve(
    transport: str = "http",
    host: str = "127.0.0.1",
    port: int = 8765,
    path: str = _DEFAULT_HTTP_PATH,
    token: str | None = None,
) -> int:
    """Run the server. Returns a process exit code."""
    if transport not in {"http", "streamable-http", "sse", "stdio"}:
        print(f"error: unknown transport {transport!r}")
        return 2

    if transport == "stdio":
        # A local process has no network surface, so no token is required. It cannot be
        # reached by anything except the client that spawned it.
        print("serving MCP over stdio (no network surface, no token required)")
        try:
            server = build_server(bearer_token=None)
        except ImportError as exc:
            print(f"error: {exc}")
            return 2
        server.run(transport="stdio", show_banner=False)
        return 0

    resolved_token = token or os.getenv(TOKEN_ENV_VAR)
    if not resolved_token:
        # Fail closed. A reachable, unauthenticated MCP server is a real incident, not a
        # configuration preference.
        print(
            f"error: refusing to serve {transport} without a bearer token.\n"
            f"Set {TOKEN_ENV_VAR} in the environment (not on the command line, where it "
            f"would be visible in `ps` and shell history):\n"
            f"    export {TOKEN_ENV_VAR}=$(python -c 'import secrets;"
            "print(secrets.token_urlsafe(32))')\n"
            "Then start the server again. Use --transport stdio for a local-only server, "
            "which needs no token."
        )
        return 2

    print(
        f"serving MCP over {transport} on http://{host}:{port}{path}\n"
        f"  bearer token: required (from {TOKEN_ENV_VAR})\n"
        "  client config:\n"
        '    {"mcpServers": {"bedrock-agent-eval": {"type": "http",\n'
        f'      "url": "http://{host}:{port}{path}",\n'
        f'      "headers": {{"Authorization": "Bearer ${{{TOKEN_ENV_VAR}}}"}}}}}}'
    )
    try:
        server = build_server(bearer_token=resolved_token)
    except ImportError as exc:
        print(f"error: {exc}")
        return 2
    server.run(
        transport=transport, host=host, port=port, path=path, show_banner=False
    )
    return 0


__all__ = [
    "SERVER_NAME",
    "TOKEN_ENV_VAR",
    "build_server",
    "build_verifier",
    "contract",
    "evaluation_summary",
    "extract",
    "has_aws_credentials",
    "list_cases",
    "resolve_mode",
    "serve",
]
