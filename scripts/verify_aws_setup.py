#!/usr/bin/env python3
"""Verify that Phase 0 actually worked, and say precisely what is still missing.

Run this after creating the account, the IAM user and the budget alarm:

    make verify-aws              # checks credentials, model access, prices, guardrail
    make verify-aws ARGS=--invoke   # also makes one tiny live call to prove access

Every check is independent, so a failure in one does not hide the state of the others. The
exit code is non-zero only when something blocking failed.

The `--invoke` check sends one short prompt to the cheap model. It costs a fraction of a
cent and it is the only check that proves model access end to end rather than by
inference; everything else can pass while invocation still fails.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from src import config, pricing

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"

SYMBOL = {OK: "✓", WARN: "!", FAIL: "✗", SKIP: "·"}


@dataclass
class Check:
    name: str
    status: str
    detail: str
    next_action: str = ""


def client(service: str, region: str):
    import boto3

    return boto3.client(service, region_name=region)


def stripped_model_id(inference_profile_id: str) -> str:
    """`eu.anthropic.claude-...` -> `anthropic.claude-...` (the foundation-model id)."""
    if "." not in inference_profile_id:
        return inference_profile_id
    return inference_profile_id.split(".", 1)[1]


def check_credentials() -> tuple[list[Check], str | None]:
    checks: list[Check] = []

    try:
        import boto3
    except ImportError:
        checks.append(
            Check(
                "boto3 installed",
                FAIL,
                "not importable",
                "run `make install`",
            )
        )
        return checks, None

    checks.append(Check("boto3 installed", OK, f"version {boto3.__version__}"))

    try:
        session = boto3.Session(region_name=config.REGION)
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("credentials resolvable", FAIL, str(exc), "see docs/PHASE0.md"))
        return checks, None

    credentials = session.get_credentials()
    if credentials is None:
        checks.append(
            Check(
                "credentials resolvable",
                FAIL,
                "no credentials found",
                "create the IAM user and write ~/.aws/credentials — see docs/PHASE0.md",
            )
        )
        return checks, None

    checks.append(Check("credentials resolvable", OK, "found"))

    try:
        identity = session.client("sts").get_caller_identity()
    except Exception as exc:  # noqa: BLE001
        checks.append(
            Check("credentials accepted by AWS", FAIL, str(exc), "check the key/secret pair")
        )
        return checks, None

    account_id = identity["Account"]
    arn = identity["Arn"]
    checks.append(Check("credentials accepted by AWS", OK, f"{account_id} as {arn}"))

    if ":root" in arn:
        checks.append(
            Check(
                "not using root",
                FAIL,
                "these are root credentials",
                "create the IAM user from aws/iam-policy.json and use its keys instead",
            )
        )
    else:
        checks.append(Check("not using root", OK, "scoped IAM principal"))

    return checks, account_id


def check_region() -> Check:
    if config.REGION == "eu-north-1":
        return Check("region is eu-north-1", OK, config.REGION)
    return Check(
        "region is eu-north-1",
        WARN,
        f"configured region is {config.REGION}",
        "the two Claude 4.5 models are EU-geo only; eu-north-1 is the documented choice",
    )


def check_model_access() -> list[Check]:
    checks: list[Check] = []
    try:
        bedrock = client("bedrock", config.REGION)
        available = {
            model["modelId"]
            for model in bedrock.list_foundation_models().get("modelSummaries", [])
        }
    except Exception as exc:  # noqa: BLE001
        checks.append(
            Check(
                "foundation model catalogue readable",
                FAIL,
                str(exc),
                "the IAM policy needs bedrock:ListFoundationModels",
            )
        )
        return checks

    checks.append(Check("foundation model catalogue readable", OK, f"{len(available)} models"))

    for label, model_id in (
        ("cheap (Haiku 4.5)", config.CHEAP_MODEL_ID),
        ("strong (Sonnet 4.5)", config.STRONG_MODEL_ID),
    ):
        underlying = stripped_model_id(model_id)
        if underlying in available:
            checks.append(Check(f"model available: {label}", OK, underlying))
        else:
            checks.append(
                Check(
                    f"model available: {label}",
                    FAIL,
                    f"{underlying} not in the catalogue for {config.REGION}",
                    "request model access in the Bedrock console — see docs/PHASE0.md",
                )
            )
    return checks


def check_invocation() -> Check:
    """One tiny live call. The only definitive proof that access works."""
    try:
        runtime = client("bedrock-runtime", config.REGION)
        response = runtime.converse(
            modelId=config.CHEAP_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": "Reply with the single word: ok"}]}],
            inferenceConfig={"maxTokens": 8, "temperature": 0},
        )
    except Exception as exc:  # noqa: BLE001
        return Check(
            "live invocation works",
            FAIL,
            str(exc),
            "model access may still be pending; re-run later",
        )

    usage = response.get("usage", {})
    text = "".join(
        block.get("text", "")
        for block in response.get("output", {}).get("message", {}).get("content", [])
    ).strip()
    return Check(
        "live invocation works",
        OK,
        f"replied {text!r}, in {usage.get('inputTokens')} / out {usage.get('outputTokens')} tokens",
    )


def check_guardrail() -> Check:
    if not config.GUARDRAIL_ID:
        return Check(
            "guardrail configured",
            SKIP,
            "GUARDRAIL_ID is empty",
            "optional here; `make guardrail` creates it (Phase 5)",
        )
    try:
        bedrock = client("bedrock", config.REGION)
        guardrail = bedrock.get_guardrail(
            guardrailIdentifier=config.GUARDRAIL_ID,
            guardrailVersion=config.GUARDRAIL_VERSION,
        )
    except Exception as exc:  # noqa: BLE001
        return Check("guardrail configured", FAIL, str(exc), "re-run `make guardrail`")
    return Check(
        "guardrail configured",
        OK,
        f"{guardrail.get('name')} v{guardrail.get('version')} ({guardrail.get('status')})",
    )


def check_prices() -> Check:
    try:
        pricing.assert_prices_loaded([config.CHEAP_MODEL_ID, config.STRONG_MODEL_ID])
    except pricing.PricesNotLoaded as exc:
        return Check(
            "prices loaded",
            FAIL,
            str(exc).splitlines()[0],
            "run `make phase0` stage 6, or set PRICE_* and PRICES_AS_OF in .env",
        )
    haiku = pricing.price_for(config.CHEAP_MODEL_ID)
    sonnet = pricing.price_for(config.STRONG_MODEL_ID)
    return Check(
        "prices loaded",
        OK,
        f"as of {pricing.PRICES_AS_OF} — haiku in/out {haiku.input}/{haiku.output}, "
        f"sonnet {sonnet.input}/{sonnet.output}",
    )


def check_budget_with_account(account_id: str) -> Check:
    try:
        budgets = client("budgets", "us-east-1")
        budgets_page = budgets.describe_budgets(AccountId=account_id)
    except Exception as exc:  # noqa: BLE001
        return Check(
            "budget alarm exists",
            WARN,
            f"could not read budgets: {exc}",
            "run `python scripts/bootstrap_aws.py --email you@example.com`",
        )

    names = {budget["BudgetName"] for budget in budgets_page.get("Budgets", [])}
    if "bedrock-agent-eval-monthly" not in names:
        return Check(
            "budget alarm exists",
            FAIL,
            "not found",
            "run `python scripts/bootstrap_aws.py --email you@example.com`",
        )

    notifications = budgets.describe_notifications_for_budget(
        AccountId=account_id, BudgetName="bedrock-agent-eval-monthly"
    ).get("Notifications", [])
    subscribers = sum(
        len(
            budgets.describe_subscribers_for_notification(
                AccountId=account_id,
                BudgetName="bedrock-agent-eval-monthly",
                Notification=notification,
            ).get("Subscribers", [])
        )
        for notification in notifications
    )
    unconfirmed = subscribers == 0
    return Check(
        "budget alarm exists",
        WARN if unconfirmed else OK,
        f"bedrock-agent-eval-monthly, {len(notifications)} notification(s), "
        f"{subscribers} subscriber(s)"
        + (" — the confirmation email may not be clicked yet" if unconfirmed else ""),
        "click the confirmation link AWS emailed you" if unconfirmed else "",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the Phase 0 AWS setup.")
    parser.add_argument(
        "--invoke",
        action="store_true",
        help="also make one tiny live call (costs a fraction of a cent, proves access)",
    )
    args = parser.parse_args(argv)

    print(f"\nPhase 0 verification — region {config.REGION}\n")

    checks, account_id = check_credentials()

    credentials_ok = all(check.status != FAIL for check in checks)
    if credentials_ok:
        checks.append(check_region())
        checks.extend(check_model_access())
        if args.invoke:
            checks.append(check_invocation())
        else:
            checks.append(
                Check("live invocation works", SKIP, "pass --invoke to test it", "")
            )
        checks.append(check_guardrail())
        checks.append(check_prices())
        if account_id:
            checks.append(check_budget_with_account(account_id))

    width = max(len(check.name) for check in checks)
    for check in checks:
        print(f"  {SYMBOL[check.status]} {check.name.ljust(width)}  {check.detail}")
        if check.status in (FAIL, WARN) and check.next_action:
            print(f"    {' ' * width}  → {check.next_action}")

    failures = [check for check in checks if check.status == FAIL]
    warnings = [check for check in checks if check.status == WARN]
    print(
        f"\n  {len(checks) - len(failures) - len(warnings)} ok, "
        f"{len(warnings)} warning(s), {len(failures)} blocking failure(s)\n"
    )

    if failures:
        print("  Not ready. Fix the failures above, then re-run `make verify-aws`.\n")
        return 1

    if not args.invoke:
        print("  Ready. Re-run with --invoke to prove model access end to end.\n")
    else:
        print("  Phase 0 complete. Next: `make evals` for the live evaluation run.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
