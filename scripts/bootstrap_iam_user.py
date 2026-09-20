#!/usr/bin/env python3
"""Bootstrap the scoped IAM user, then get out of the way.

Run once, while a temporary bootstrap identity (root, via `aws login`) is active. It
creates the least-privilege user, attaches the region-locked policy from
`aws/iam-policy.json`, mints one access key, and writes it to `~/.aws/credentials`.

After this runs, the root session should be discarded: every subsequent command uses the
scoped user, whose policy denies all `bedrock:*` actions outside the EU region list.

Idempotent. Re-running updates the policy and reports the state of the access keys rather
than silently minting a second one (IAM allows two per user, and a forgotten second key is
a small liability).

Usage:
    python scripts/bootstrap_iam_user.py                  # create if missing
    python scripts/bootstrap_iam_user.py --rotate         # replace the existing access key
    python scripts/bootstrap_iam_user.py --user NAME --profile NAME
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_USER = "bedrock-agent-eval"
DEFAULT_POLICY = "bedrock-agent-eval"
CREDENTIALS_PATH = Path.home() / ".aws" / "credentials"


def _iam(profile: str | None):
    import boto3

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    return session.client("iam"), session.client("sts")


def ensure_policy(iam, policy_name: str, document: dict, account_id: str) -> str:
    """Create the policy, or update it in place if it already exists. Returns its ARN."""
    body = json.dumps(document)
    arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
    try:
        created = iam.create_policy(
            PolicyName=policy_name,
            PolicyDocument=body,
            Description=(
                "Invoke and govern Bedrock in eu-north-1 for the bedrock-agent-eval "
                "project. Region-locked by condition."
            ),
        )
        print(f"  policy:  created {policy_name}")
        return created["Policy"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        # IAM keeps at most five versions; make ours the default and prune the oldest.
        iam.create_policy_version(PolicyArn=arn, PolicyDocument=body, SetAsDefault=True)
        versions = iam.list_policy_versions(PolicyArn=arn)["Versions"]
        for version in sorted(versions, key=lambda v: v["CreateDate"])[:-5]:
            if not version["IsDefaultVersion"]:
                iam.delete_policy_version(PolicyArn=arn, VersionId=version["VersionId"])
        print(f"  policy:  updated {policy_name} (new default version)")
        return arn


def ensure_user(iam, user_name: str) -> None:
    try:
        iam.create_user(UserName=user_name)
        print(f"  user:    created {user_name}")
    except iam.exceptions.EntityAlreadyExistsException:
        print(f"  user:    {user_name} already exists")


def ensure_attachment(iam, user_name: str, policy_arn: str) -> None:
    attached = {
        p["PolicyArn"]
        for p in iam.list_attached_user_policies(UserName=user_name)["AttachedPolicies"]
    }
    if policy_arn in attached:
        print("  attach:  policy already attached")
    else:
        iam.attach_user_policy(UserName=user_name, PolicyArn=policy_arn)
        print("  attach:  policy attached")


def ensure_access_key(iam, user_name: str, rotate: bool) -> dict:
    keys = iam.list_access_keys(UserName=user_name)["AccessKeyMetadata"]

    if rotate:
        for key in keys:
            iam.delete_access_key(UserName=user_name, AccessKeyId=key["AccessKeyId"])
            print(f"  key:     deleted {key['AccessKeyId']}")
        keys = []

    if keys:
        if len(keys) > 1:
            print(
                f"  warn:    {len(keys)} access keys already exist for {user_name}; "
                "IAM allows two. Consider revoking the spare in the console."
            )
        raise SystemExit(
            f"  key:     an access key already exists ({keys[0]['AccessKeyId']}) and its "
            "secret cannot be read again.\n"
            "           Re-run with --rotate to replace it and write fresh credentials."
        )

    created = iam.create_access_key(UserName=user_name)["AccessKey"]
    print(f"  key:     created {created['AccessKeyId']}")
    return created

def write_credentials(access_key_id: str, secret: str, region: str) -> None:
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(CREDENTIALS_PATH.parent, 0o700)

    existing = ""
    if CREDENTIALS_PATH.exists():
        existing = CREDENTIALS_PATH.read_text(encoding="utf-8")

    # Replace any [default] block that we or a previous run wrote; leave other profiles be.
    lines = existing.splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        if line.strip().startswith("["):
            skipping = line.strip() == "[default]"
        if not skipping:
            kept.append(line)

    block = [
        "[default]",
        f"aws_access_key_id = {access_key_id}",
        f"aws_secret_access_key = {secret}",
        f"region = {region}",
    ]
    body = "\n".join([line for line in kept if line.strip()] + block) + "\n"

    old_umask = os.umask(0o077)
    try:
        CREDENTIALS_PATH.write_text(body, encoding="utf-8")
    finally:
        os.umask(old_umask)
    os.chmod(CREDENTIALS_PATH, 0o600)
    print(f"  creds:   wrote [default] to {CREDENTIALS_PATH} (mode 0600)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the scoped IAM user for this project.")
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    parser.add_argument(
        "--profile",
        default=None,
        help="bootstrap profile (default: the default credential chain)",
    )
    parser.add_argument("--rotate", action="store_true", help="replace an existing access key")
    parser.add_argument(
        "--policy-only",
        action="store_true",
        help="update the policy version and stop; touch no users, keys or credentials",
    )
    parser.add_argument("--region", default="eu-north-1")
    args = parser.parse_args(argv)

    try:
        iam, sts = _iam(args.profile)
        identity = sts.get_caller_identity()
    except Exception as exc:  # noqa: BLE001
        print(
            f"error: no working bootstrap credentials: {exc}\n"
            "  Run `aws login --region eu-north-1 --profile default` first."
        )
        return 2

    arn = identity["Arn"]
    account = identity["Account"]
    print(f"\nbootstrap identity: {arn}")
    if ":root" not in arn:
        print(
            "  note: this is not root. That is fine for creating a user only if this "
            "identity already has IAM permissions."
        )

    policy_document = json.loads(
        (REPO_ROOT / "aws" / "iam-policy.json").read_text(encoding="utf-8")
    )

    print("\ncreating the scoped user\n")
    policy_arn = ensure_policy(iam, args.policy, policy_document, account)

    if args.policy_only:
        print(
            f"\npolicy updated: {policy_arn}\n"
            "  --policy-only: no user, key or credentials were touched.\n"
        )
        return 0

    ensure_user(iam, args.user)
    ensure_attachment(iam, args.user, policy_arn)
    key = ensure_access_key(iam, args.user, args.rotate)
    # The API returns the secret exactly once, in `SecretAccessKey`. Getting this name wrong
    # means the key exists and nobody can ever use it, so the write happens immediately and
    # the field is asserted rather than assumed.
    try:
        secret = key["SecretAccessKey"]
    except KeyError as exc:  # pragma: no cover - guards a silent, unrecoverable mistake
        raise SystemExit(
            f"  fatal:  create_access_key returned no SecretAccessKey (keys: "
            f"{sorted(key)}). The key {key.get('AccessKeyId')} now exists with an unknown "
            "secret. Revoke it in the console, or re-run with --rotate."
        ) from exc
    write_credentials(key["AccessKeyId"], secret, args.region)

    user_arn = f"arn:aws:iam::{account}:user/{args.user}"
    print(
        f"\ndone. The scoped user is {user_arn}\n\n"
        "next, drop the bootstrap session so every command uses this user instead:\n"
        "    python scripts/switch_off_bootstrap_session.py\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
