#!/usr/bin/env python3
"""Drop the bootstrap session so the scoped IAM user becomes the active identity.

`aws login` leaves a `login_session` line in `~/.aws/config` under the profile it
authenticated. That session is whatever you signed in as — in a fresh account, root. It is
fine for creating the scoped user and nothing else.

This removes that line and clears the cached login tokens, so the profile falls back to the
access key that `bootstrap_iam_user.py` wrote into `~/.aws/credentials`. The config file is
backed up first, and the cached tokens are moved aside rather than deleted, so re-running
`aws login` is still possible.

Usage:
    python scripts/switch_off_bootstrap_session.py
    python scripts/switch_off_bootstrap_session.py --profile bedrock-agent-eval
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

CONFIG_PATH = Path.home() / ".aws" / "config"
CREDENTIALS_PATH = Path.home() / ".aws" / "credentials"
LOGIN_CACHE = Path.home() / ".aws" / "login"


def remove_login_session(profile: str) -> bool:
    if not CONFIG_PATH.exists():
        print("  config:  no ~/.aws/config; nothing to remove")
        return False

    backup = CONFIG_PATH.with_suffix(f".config.backup-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(CONFIG_PATH, backup)
    print(f"  config:  backed up to {backup.name}")

    lines = CONFIG_PATH.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    current: str | None = None
    removed = False

    # A non-default profile is written as `[profile name]`; the default one is just
    # `[default]`. Matching only the bare name silently misses every named profile.
    wanted = {profile, f"profile {profile}"}

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            current = stripped.strip("[]")
        if current in wanted and stripped.startswith("login_session"):
            removed = True
            continue
        out.append(line)

    # Collapse a section header that lost all of its keys.
    cleaned: list[str] = []
    for index, line in enumerate(out):
        if line.strip().startswith("["):
            following = [item.strip() for item in out[index + 1 :] if item.strip()]
            if not following or following[0].startswith("["):
                continue
        cleaned.append(line)

    text = "\n".join(cleaned).strip()
    CONFIG_PATH.write_text((text + "\n") if text else "", encoding="utf-8")
    return removed


def move_login_cache() -> None:
    if not LOGIN_CACHE.exists():
        return
    target = LOGIN_CACHE.with_name(f"login.archived-{time.strftime('%Y%m%d-%H%M%S')}")
    LOGIN_CACHE.rename(target)
    print(f"  cache:   moved ~/.aws/login aside to {target.name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remove the bootstrap login session.")
    parser.add_argument("--profile", default="default")
    args = parser.parse_args(argv)

    print(f"\nswitching profile '{args.profile}' off the bootstrap session\n")

    if not CREDENTIALS_PATH.exists():
        print(
            "error: ~/.aws/credentials does not exist, so removing the login session would "
            "leave no way to authenticate.\n"
            "  Run scripts/bootstrap_iam_user.py first."
        )
        return 2

    if "[default]" not in CREDENTIALS_PATH.read_text(encoding="utf-8"):
        print(
            "error: ~/.aws/credentials has no [default] profile, so removing the login "
            "session would leave no way to authenticate."
        )
        return 2

    if remove_login_session(args.profile):
        print(f"  config:  removed login_session from [{args.profile}]")
    else:
        print(f"  config:  no login_session found for [{args.profile}]")

    move_login_cache()

    print(
        "\nnow verify the identity changed:\n"
        "    aws sts get-caller-identity\n"
        "    make verify-aws\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
