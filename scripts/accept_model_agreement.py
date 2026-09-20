#!/usr/bin/env python3
"""Accept the foundation-model agreement — the gate nobody documents.

Bedrock has four separate gates, and a new account trips three of them in an order that
hides each one behind a misleading error:

  1. the use-case form      `put_use_case_for_model_access`  (self-service, this repo's scripts)
  2. the model agreement    `create_foundation_model_agreement`  <-- this script
  3. the Marketplace grant  IAM: aws-marketplace:ViewSubscriptions, Subscribe
  4. the account's plan     free-plan accounts are throttled on inference regardless

Between 2 and 3 the error message blames the *form* ("Model use case details have not been
submitted"), which sends you back to a step that is already done.
`get_foundation_model_availability` is the only honest diagnostic: it reports agreement,
authorization, entitlement and region separately.

Requires an identity that can touch AWS Marketplace — root, or an admin profile. The scoped
runtime user deliberately cannot: accepting a provider agreement is a one-time administrative
act, not something a token-billing service account should be able to repeat.

Usage:
    python scripts/accept_model_agreement.py --profile bootstrap
    python scripts/accept_model_agreement.py --profile bootstrap --check-only
"""

from __future__ import annotations

import argparse
import sys
import time

from src import config

MODELS = ("anthropic.claude-haiku-4-5-20251001-v1:0", "anthropic.claude-sonnet-4-5-20250929-v1:0")


def _clients(profile: str | None):
    import boto3

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    return (
        session.client("bedrock", region_name=config.REGION),
        session.client("sts"),
    )


def status_for(bedrock, model_id: str) -> dict:
    result = bedrock.get_foundation_model_availability(modelId=model_id)
    return {
        "agreement": result["agreementAvailability"]["status"],
        "authorization": result["authorizationStatus"],
        "entitlement": result["entitlementAvailability"],
        "region": result["regionAvailability"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Accept the model agreement for the two models.")
    parser.add_argument("--profile", default=None, help="an identity with Marketplace access")
    parser.add_argument("--check-only", action="store_true", help="report state, change nothing")
    parser.add_argument(
        "--wait",
        type=int,
        default=180,
        help="seconds to poll for the agreement to become AVAILABLE (default 180)",
    )
    args = parser.parse_args(argv)

    try:
        bedrock, sts = _clients(args.profile)
        arn = sts.get_caller_identity()["Arn"]
    except Exception as exc:  # noqa: BLE001
        print(f"error: no working credentials: {exc}")
        return 2

    print(f"\nidentity: {arn}\n")

    for model_id in MODELS:
        short = model_id.split(".")[1]
        state = status_for(bedrock, model_id)
        print(f"  {short}")
        print(f"    agreement      {state['agreement']}")
        print(f"    authorization  {state['authorization']}")
        print(f"    entitlement    {state['entitlement']}")
        print(f"    region         {state['region']}")

        if state["agreement"] == "AVAILABLE":
            print("    -> already accepted, nothing to do\n")
            continue
        if args.check_only:
            print("    -> --check-only: not accepting\n")
            continue

        try:
            offers = bedrock.list_foundation_model_agreement_offers(modelId=model_id).get(
                "offers", []
            )
        except Exception as exc:  # noqa: BLE001
            print(f"    -> could not list offers: {exc}\n")
            continue

        if not offers:
            print("    -> no agreement offer returned; is this model available in the region?\n")
            continue

        for offer in offers:
            try:
                bedrock.create_foundation_model_agreement(
                    offerToken=offer["offerToken"], modelId=model_id
                )
                print(f"    -> accepted offer {offer.get('offerId')}")
            except Exception as exc:  # noqa: BLE001
                if "already" in str(exc).lower() or "Conflict" in type(exc).__name__:
                    print("    -> already accepted")
                else:
                    print(f"    -> failed: {type(exc).__name__}: {str(exc)[:180]}")
        print()

    if args.check_only:
        return 0

    print(f"polling for up to {args.wait}s ...")
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        states = {m: status_for(bedrock, m)["agreement"] for m in MODELS}
        if all(s == "AVAILABLE" for s in states.values()):
            print("  all agreements AVAILABLE")
            print(
                "\nnext: the runtime identity still needs aws-marketplace:ViewSubscriptions and "
                "aws-marketplace:Subscribe, or Converse fails with AccessDenied.\n"
                "Then: make verify-aws ARGS=--invoke\n"
            )
            return 0
        time.sleep(15)

    print(
        "  still pending after the wait. That is normal on a new account; re-run with "
        "--check-only later, or continue and let the evaluation pick it up.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
