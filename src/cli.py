"""Command-line interface.

One entry point for the four things you actually do with this repository: extract from a
transcript, check the contract, run the evaluation gate, and run the cost experiment — plus
starting the MCP server.

    bedrock-agent-eval extract --case case-01
    bedrock-agent-eval extract --transcript-file notes.txt --offline
    bedrock-agent-eval contract
    bedrock-agent-eval eval --offline
    bedrock-agent-eval compare
    bedrock-agent-eval serve-mcp --transport stdio
    bedrock-agent-eval price-check

The `eval` and `compare` subcommands delegate to their modules rather than reimplementing
anything, so the CLI can never drift from the harness it wraps.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from src import config, pricing
from src.mcp_server import extract, list_cases, serve
from src.schema import output_schema

EPILOG = """
examples:
  bedrock-agent-eval extract --case case-05            # the case that escalates offline
  bedrock-agent-eval extract --case case-04 --json     # the case with planted PII
  bedrock-agent-eval eval --offline --gate 0.9         # the gate, no AWS
  bedrock-agent-eval eval --arm all                    # per-model detail, live
  bedrock-agent-eval compare                           # the three-arm cost experiment
  bedrock-agent-eval serve-mcp --transport stdio       # local MCP, no token needed

Offline mode is a plumbing test: the stub fabricates token counts, so no currency is ever
printed for it.
"""


def _cmd_extract(args: argparse.Namespace) -> int:
    transcript = ""
    if args.transcript_file:
        path = Path(args.transcript_file)
        if not path.is_file():
            print(f"error: no such file: {path}")
            return 2
        transcript = path.read_text(encoding="utf-8")
    elif not args.case:
        print("error: pass --case or --transcript-file")
        return 2

    if args.offline and args.live:
        print("error: --offline and --live are mutually exclusive")
        return 2
    mode = "offline" if args.offline else "live" if args.live else "auto"

    result = extract(transcript, case_id=args.case, model=args.model, mode=mode)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_extract(result)

    if result.get("error"):
        return 1
    return 0 if result.get("valid") else 1


def _print_extract(result: dict[str, Any]) -> None:
    mode = result.get("mode")
    print(f"mode:      {mode}")
    if mode == "offline-stub":
        print(
            "WARNING:    offline stub — fabricated tokens, deterministic answer. This is "
            "a plumbing test, not a result."
        )
    print(f"model:     {result.get('model_id')}")
    print(f"valid:     {result.get('valid')}")
    if result.get("escalated"):
        print("escalated: yes (the cheap attempt failed deterministic validation)")
    print(f"tools:     {result.get('tool_calls')} calls, {result.get('iterations')} iterations")
    usage = result.get("usage") or {}
    print(
        f"tokens:    in {usage.get('billable_input_tokens')} / "
        f"out {usage.get('output_tokens')}"
    )
    if result.get("cost_usd") is not None:
        print(f"cost:      ${result['cost_usd']:.6f} (prices as of {result.get('prices_as_of')})")
    elif result.get("cost_note"):
        print(f"cost:      not reported — {result['cost_note']}")

    for error in result.get("errors") or []:
        print(f"error:     {error}")

    payload = result.get("result")
    if not payload:
        return

    print(f"\nsummary:   {payload['summary']}")
    if payload["tasks"]:
        print("\ntasks:")
        for task in payload["tasks"]:
            print(f"  [{task['priority']:<8}] {task['owner']:<10} {task['title']}")
    else:
        print("\ntasks:     none (a correct answer for a meeting with no commitments)")
    for label, key in (("decisions", "decisions"), ("risks", "risks")):
        items = payload.get(key) or []
        if items:
            print(f"\n{label}:")
            for item in items:
                text = item.get("statement") or item.get("description")
                print(f"  - {text}")


def _cmd_cases(args: argparse.Namespace) -> int:
    cases = list_cases()
    print(f"{len(cases)} evaluation fixtures\n")
    header = f"{'id':<9} {'project':<8} {'tasks':>5} {'dec':>4} {'risk':>5} {'PII':>4}  team"
    print(header)
    print("-" * len(header))
    for case in cases:
        print(
            f"{case['id']:<9} {case['project_key']:<8} {case['expected_tasks']:>5} "
            f"{case['expected_decisions']:>4} {case['expected_risks']:>5} "
            f"{'yes' if case['has_planted_pii'] else '-':>4}  {case['team']}"
        )
    return 0


def _cmd_contract(args: argparse.Namespace) -> int:
    from src.schema import assert_bedrock_compatible

    problems = assert_bedrock_compatible()
    print(f"schema name:        {config.SCHEMA_NAME}")
    print(f"bedrock compatible: {not problems}")
    for problem in problems:
        print(f"  problem: {problem}")
    print(f"\nregion:             {config.REGION}")
    print(f"cheap model:        {config.CHEAP_MODEL_ID}")
    print(f"strong model:       {config.STRONG_MODEL_ID}")
    print(f"max tool iterations:{config.MAX_TOOL_ITERATIONS}")
    guardrail = config.guardrail_config()
    print(f"guardrail:          {guardrail or 'not configured'}")
    if args.schema:
        print("\n" + json.dumps(output_schema(), indent=2))
    else:
        print("\npass --schema to print the full JSON Schema")
    return 0


def _cmd_price_check(args: argparse.Namespace) -> int:
    try:
        pricing.assert_prices_loaded([config.CHEAP_MODEL_ID, config.STRONG_MODEL_ID])
    except pricing.PricesNotLoaded as exc:
        print(f"NOT READY: {exc}")
        return 2
    print(f"prices loaded, as of {pricing.PRICES_AS_OF}")
    for model_id in (config.CHEAP_MODEL_ID, config.STRONG_MODEL_ID):
        price = pricing.price_for(model_id)
        print(f"  {model_id}\n    in {price.input} / out {price.output} USD per 1M tokens")
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    from evals.run_evals import main as run_evals_main

    return run_evals_main(getattr(args, "rest", []))


def _cmd_compare(args: argparse.Namespace) -> int:
    from experiments.compare import main as compare_main

    return compare_main(getattr(args, "rest", []))


def _cmd_serve_mcp(args: argparse.Namespace) -> int:
    return serve(
        transport=args.transport,
        host=args.host,
        port=args.port,
        path=args.path,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bedrock-agent-eval",
        description=(
            "Extract structured Jira drafts from meeting transcripts with an Amazon "
            "Bedrock agent that is evaluated, guardrailed and costed."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser(
        "extract", help="run the agent on one transcript or fixture"
    )
    p_extract.add_argument("--case", help="fixture id, e.g. case-01")
    p_extract.add_argument(
        "--transcript-file", help="a file containing a transcript to process"
    )
    p_extract.add_argument(
        "--model", choices=["routed", "cheap", "strong"], default="routed"
    )
    p_extract.add_argument("--offline", action="store_true", help="use the stub, no AWS")
    p_extract.add_argument("--live", action="store_true", help="force the live client")
    p_extract.add_argument("--json", action="store_true", help="print the raw JSON")
    p_extract.set_defaults(func=_cmd_extract)

    p_cases = sub.add_parser("cases", help="list the evaluation fixtures")
    p_cases.set_defaults(func=_cmd_cases)

    p_contract = sub.add_parser(
        "contract", help="show the output contract and the active configuration"
    )
    p_contract.add_argument("--schema", action="store_true", help="print the JSON Schema")
    p_contract.set_defaults(func=_cmd_contract)

    p_price = sub.add_parser(
        "price-check", help="verify prices are loaded before a priced run"
    )
    p_price.set_defaults(func=_cmd_price_check)

    # `eval` and `compare` forward their flags to the modules that own them.
    #
    # Two argparse details make this work. Their parsers are created with
    # `add_help=False`, so `--help` reaches the target module and shows the real options
    # instead of an empty subcommand help. And `main()` uses `parse_known_args`, because
    # `nargs=REMAINDER` does not reliably capture a leading option such as `--offline`.
    p_eval = sub.add_parser(
        "eval",
        help="run the evaluation gate (flags are forwarded: --offline, --arm, --guardrail, --gate)",
        add_help=False,
    )
    p_eval.set_defaults(func=_cmd_eval)

    p_compare = sub.add_parser(
        "compare",
        help="run the three-arm cost experiment (flags are forwarded: --arms, --guardrail)",
        add_help=False,
    )
    p_compare.set_defaults(func=_cmd_compare)

    p_serve = sub.add_parser("serve-mcp", help="start the MCP server")
    p_serve.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http"],
        default="stdio",
        help="stdio needs no token; http refuses to start without one",
    )
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--path", default="/mcp")
    p_serve.set_defaults(func=_cmd_serve_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # parse_known_args, not parse_args: unknown flags belong to the subcommand being
    # forwarded to, and rejecting them here would break `eval --offline`.
    args, forwarded = parser.parse_known_args(argv)
    if getattr(args, "func", None) in (_cmd_eval, _cmd_compare):
        args.rest = forwarded
    elif forwarded:
        parser.error(f"unrecognized arguments: {' '.join(forwarded)}")
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    except ValueError as exc:
        # Bad user input, not a crash.
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
