#!/usr/bin/env python3
"""Create the Bedrock guardrail used by the demo, and print its id.

What it does, and the reasoning that matters more than the code:

* **Masks phone numbers, email addresses and the test card number on the INPUT.**
  Masking rather than blocking, because the extraction is supposed to keep working —
  a guardrail that breaks the feature is a guardrail someone will switch off.

* **Names are deliberately NOT masked.** The tasks have owners, and the owner's name is
  in the transcript. Masking names would make the extraction unscoreable and would prove
  nothing. The policy targets *identifiers*, not people. This is a real design decision
  and an interviewer will ask about it, which is the point of doing it explicitly rather
  than accepting a default.

* **Blocks one denied topic** so the denied-topics mechanism is exercised too.

* **Blocks the output side as a backstop.** The input mask means the model never sees the
  identifiers, so a leak should be impossible — which is exactly why measuring the leak
  count is a meaningful test rather than a formality.

Requires permission to create guardrails (`bedrock:CreateGuardrail`) in the account.
After running, put the printed identifier and version into `.env`.

The script is idempotent: if the guardrail already exists it is updated rather than duplicated, and
either way a **numbered, immutable version** is published. AWS's own guidance is to use numbered
versions rather than `DRAFT` for anything that is not an experiment in progress, because `DRAFT` is
mutable and an evaluation should not run against a policy that can change underneath it. Re-running
creates the next version number rather than overwriting the last one.

Usage:
    python scripts/create_guardrail.py
"""

from __future__ import annotations

import json
import sys

from src import config

GUARDRAIL_NAME = "meeting-extraction-pii"

# Entity types are chosen, not defaulted: `NAME` is absent on purpose (see docstring).
# The enum values are the API's, not the console's: it is `CREDIT_DEBIT_CARD_NUMBER`, and
# `CREDIT_DEBIT_NUMBER` is rejected. Both this and the topic-policy key were wrong in the first
# version of this script and were only found by running it.
PII_ENTITIES = [
    {"type": "PHONE", "action": "ANONYMIZE"},
    {"type": "EMAIL", "action": "ANONYMIZE"},
    {"type": "CREDIT_DEBIT_CARD_NUMBER", "action": "BLOCK"},
]

DENIED_TOPIC = {
    "name": "UnapprovedFinancialAdvice",
    "definition": (
        "Requests or responses that give customers specific investment, tax or legal "
        "advice about their own money, as opposed to describing a product or a process."
    ),
    "examples": [
        "Should I move my savings into index funds?",
        "Tell the customer which account gives the best return.",
        "Draft a reply advising the customer to remortgage.",
    ],
    "type": "DENY",
}


def main() -> int:
    try:
        import boto3
    except ImportError:
        print("error: boto3 is not installed. Run `make install`.")
        return 2

    client = boto3.client("bedrock", region_name=config.REGION)

    print(f"creating guardrail {GUARDRAIL_NAME!r} in {config.REGION} ...")
    try:
        response = client.create_guardrail(
            name=GUARDRAIL_NAME,
            description=(
                "Masks customer identifiers on the way in and blocks unapproved "
                "financial advice, for the meeting-transcript extraction demo."
            ),
            sensitiveInformationPolicyConfig={"piiEntitiesConfig": PII_ENTITIES},
            # The API calls this `topicPolicyConfig`; `deniedTopicsPolicyConfig` is the console's
            # wording and is rejected outright. Found by running it, not by reading about it.
            topicPolicyConfig={"topicsConfig": [DENIED_TOPIC]},
            blockedInputMessaging=(
                "This request contains content that cannot be processed."
            ),
            blockedOutputsMessaging=(
                "The response was withheld because it may contain restricted content."
            ),
        )
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim for the operator
        if type(exc).__name__ == "ConflictException" or "already exist" in str(exc):
            existing = next(
                (
                    g
                    for g in client.list_guardrails().get("guardrails", [])
                    if g.get("name") == GUARDRAIL_NAME
                ),
                None,
            )
            if existing is None:
                print(f"error: {GUARDRAIL_NAME!r} is reported as existing but cannot be listed")
                return 1
            guardrail_id = existing["id"]
            print(f"  guardrail {GUARDRAIL_NAME!r} already exists ({guardrail_id}); updating it")
            client.update_guardrail(
                guardrailIdentifier=guardrail_id,
                # `name` is required even though the identifier already names it.
                name=GUARDRAIL_NAME,
                sensitiveInformationPolicyConfig={"piiEntitiesConfig": PII_ENTITIES},
                topicPolicyConfig={"topicsConfig": [DENIED_TOPIC]},
                blockedInputMessaging="This request contains content that cannot be processed.",
                blockedOutputsMessaging=(
                    "The response was withheld because it may contain restricted content."
                ),
            )
            response = {"guardrailId": guardrail_id, "version": "DRAFT"}
        else:
            print(f"error: could not create the guardrail: {exc}")
            print(
                "hint: the caller needs bedrock:CreateGuardrail, and the name must not "
                "already exist. If it exists, list it with:\n"
                "  aws bedrock list-guardrails --region " + config.REGION
            )
            return 1

    guardrail_id = response["guardrailId"]

    # DRAFT is mutable and AWS's own guidance is to use a numbered, immutable version for
    # anything that is not an experiment in progress. Publishing one here means the evaluation
    # runs against a snapshot that cannot change underneath it, and it is the version worth
    # quoting. Re-running this script creates the next number rather than overwriting.
    try:
        published = client.create_guardrail_version(
            guardrailIdentifier=guardrail_id,
            description="Immutable snapshot for the evaluation runs; DRAFT is mutable.",
        )
        version = published.get("version", "DRAFT")
        print(f"  published version {version}")
    except Exception as exc:  # noqa: BLE001
        version = response.get("version", "DRAFT")
        print(f"  could not publish a numbered version ({str(exc)[:120]}); using {version}")

    print("\nready.")
    print(f"  guardrailId: {guardrail_id}")
    print(f"  version:     {version}")
    print("\nPut these in .env:\n")
    print(f"GUARDRAIL_ID={guardrail_id}")
    print(f"GUARDRAIL_VERSION={version}")
    print(
        "\nConfig summary:\n"
        + json.dumps(
            {"pii": PII_ENTITIES, "deniedTopics": [DENIED_TOPIC["name"]]}, indent=2
        )
    )
    print(
        "\nNote: guardrails are billed per text unit in addition to model tokens, so the\n"
        "cost experiment must be run twice — once with the guardrail, once without — if\n"
        "you want the guardrail's own cost in the table."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
