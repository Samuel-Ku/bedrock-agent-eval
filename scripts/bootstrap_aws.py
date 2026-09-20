#!/usr/bin/env python3
"""Create the Phase 0 AWS guardrails: a budget alarm and its email notification.

Run this after the IAM user exists and its credentials are in `~/.aws/credentials`.

What it creates:

* an SNS topic for budget notifications (in us-east-1, where the AWS Budgets API lives);
* an email subscription to that topic — **you must click the confirmation link AWS sends**,
  or the alarm will fire silently into nothing;
* a monthly COST budget of 10 USD with notifications at 5 USD actual, 10 USD actual and
  10 USD forecast.

Why a budget alarm is Phase 0 and not an afterthought: a runaway agent loop is the only way
this project gets expensive, and the alarm is what turns "I forgot an iteration cap" from an
invoice into an email. It is also, for a bank, the cheapest possible demonstration that you
think about spend before you think about features.

The script is idempotent: re-running it updates the existing budget rather than failing.

Usage:
    python scripts/bootstrap_aws.py --email you@example.com
    python scripts/bootstrap_aws.py --email you@example.com --limit 10
"""

from __future__ import annotations

import argparse
import sys

from src import config

BUDGETS_REGION = "us-east-1"  # AWS Budgets is a global service with a us-east-1 endpoint.
TOPIC_NAME = "bedrock-agent-eval-budget-alerts"


def _client(service: str, region: str):
    try:
        import boto3
    except ImportError:
        print("error: boto3 is not installed. Run `make install`.")
        raise SystemExit(2) from None
    return boto3.client(service, region_name=region)


def ensure_topic(email: str) -> str:
    sns = _client("sns", BUDGETS_REGION)
    topic_arn = sns.create_topic(Name=TOPIC_NAME)["TopicArn"]
    print(f"  SNS topic:   {topic_arn}")

    # Idempotent: subscribing the same address twice is harmless.
    sns.subscribe(TopicArn=topic_arn, Protocol="email", Endpoint=email)
    print(f"  subscribed:  {email}")
    print(
        "  ACTION: AWS has emailed a confirmation link to that address.\n"
        "          The alarm does nothing until you click it."
    )
    return topic_arn


def ensure_budget(account_id: str, topic_arn: str, limit: float) -> None:
    budgets = _client("budgets", BUDGETS_REGION)

    definition = {
        "BudgetName": "bedrock-agent-eval-monthly",
        "BudgetType": "COST",
        "TimeUnit": "MONTHLY",
        "BudgetLimit": {"Amount": str(limit), "Unit": "USD"},
        "CostFilters": {},
        "CostTypes": {
            "IncludeTax": True,
            "IncludeSubscription": True,
            "UseBlended": False,
            "IncludeRefund": False,
            "IncludeCredit": False,
            "IncludeUpfront": True,
            "IncludeRecurring": True,
            "IncludeOtherSubscription": True,
            "IncludeSupport": True,
            "IncludeDiscount": True,
            "UseAmortized": False,
        },
        "TimePeriod": {"Start": "2020-01-01T00:00:00Z", "End": "2087-06-15T00:00:00Z"},
    }
    name = definition["BudgetName"]

    # Three thresholds, because they answer three different questions: "am I spending more
    # than expected", "have I hit the cap", and "will I hit the cap if this continues".
    notifications = [
        {
            "NotificationType": "ACTUAL",
            "ComparisonOperator": "GREATER_THAN",
            "Threshold": limit / 2,
            "ThresholdType": "ABSOLUTE_VALUE",
        },
        {
            "NotificationType": "ACTUAL",
            "ComparisonOperator": "GREATER_THAN",
            "Threshold": limit,
            "ThresholdType": "ABSOLUTE_VALUE",
        },
        {
            "NotificationType": "FORECASTED",
            "ComparisonOperator": "GREATER_THAN",
            "Threshold": limit,
            "ThresholdType": "ABSOLUTE_VALUE",
        },
    ]

    existing = {
        budget["BudgetName"]
        for budget in budgets.describe_budgets(AccountId=account_id).get("Budgets", [])
    }

    if name in existing:
        budgets.update_budget(AccountId=account_id, NewBudget=definition)
        print(f"  budget:      updated {name} (limit {limit} USD/month)")
    else:
        budgets.create_budget(AccountId=account_id, Budget=definition)
        print(f"  budget:      created {name} (limit {limit} USD/month)")

    # Notifications are managed separately from the budget definition, so they are
    # replaced rather than merged: clear the old ones, then set the current three. That
    # keeps re-runs idempotent instead of accumulating thresholds.
    for stale in budgets.describe_notifications_for_budget(
        AccountId=account_id, BudgetName=name
    ).get("Notifications", []):
        budgets.delete_notification(
            AccountId=account_id, BudgetName=name, Notification=stale
        )
    for notification in notifications:
        budgets.create_notification(
            AccountId=account_id,
            BudgetName=name,
            Notification=notification,
            Subscribers=[{"SubscriptionType": "SNS", "Address": topic_arn}],
        )

    print(
        f"  alerts:      > {limit / 2:.2f} USD actual, > {limit:.2f} USD actual, "
        f"> {limit:.2f} USD forecasted"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the AWS budget alarm for this project.")
    parser.add_argument("--email", help="address to notify; also settable as BUDGET_ALERT_EMAIL")
    parser.add_argument("--limit", type=float, default=10.0, help="monthly limit in USD")
    args = parser.parse_args(argv)

    import os

    email = args.email or os.getenv("BUDGET_ALERT_EMAIL", "").strip()
    if not email:
        print(
            "error: no email address.\n"
            "  Pass --email you@example.com, or set BUDGET_ALERT_EMAIL in .env."
        )
        return 2

    try:
        sts = _client("sts", config.REGION)
        identity = sts.get_caller_identity()
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim for the operator
        print(
            f"error: could not reach AWS: {exc}\n"
            "  Check that ~/.aws/credentials exists and that the IAM user is active.\n"
            "  Run `make verify-aws` for a fuller diagnosis."
        )
        return 2

    account_id = identity["Account"]
    arn = identity["Arn"]
    if ":root" in arn:
        print(
            "refusing to continue: these are ROOT credentials.\n"
            "Create the IAM user from aws/iam-policy.json first. See docs/PHASE0.md."
        )
        return 2

    print(f"account {account_id} as {arn}\n")
    topic_arn = ensure_topic(email)
    ensure_budget(account_id, topic_arn, args.limit)

    print(
        "\nnext:\n"
        "  1. Click the confirmation link AWS just emailed you.\n"
        "  2. `make verify-aws`\n"
        f"  3. Add to .env:  AWS_ACCOUNT_ID={account_id}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
