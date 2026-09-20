#!/usr/bin/env bash
#
# Agent Toolkit for AWS — steps 3 to 7, automated.
#
# The official setup instructions at
#   https://github.com/aws/agent-toolkit-for-aws
# designate two steps the human must perform (browser sign-in, and the interactive
# configure wizard). Everything else they leave to the operator to do by hand. This script
# does that rest: backup, configure, verify, and report exactly what changed.
#
# Run it after AWS credentials exist:
#   bash scripts/setup_agent_toolkit.sh --dry-run    # prove it works, touch nothing
#   bash scripts/setup_agent_toolkit.sh              # the real thing
#
# Flags:
#   --profile NAME    AWS profile to use (default: default)
#   --interactive     hand step 5 to the human instead of passing --yes
#   --inject-env      also add AWS_MCP_PROXY_PROFILES to each aws-mcp entry (optional:
#                     with the `default` profile the generated entry already resolves)
#   --dry-run         check and report, run nothing

set -euo pipefail

# The AWS CLI installs user-local, so make sure we find it even when this script is run
# from a non-interactive shell that never sourced the user's profile.
export PATH="$HOME/.local/bin:$PATH"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="default"
INTERACTIVE=0
INJECT_ENV=0
DRY_RUN=0
TOOLKIT_REGION="us-east-1"   # the Agent Toolkit service exists only in us-east-1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)     PROFILE="$2"; shift 2 ;;
    --interactive) INTERACTIVE=1; shift ;;
    --inject-env)  INJECT_ENV=1; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

# Every MCP configuration file the toolkit is known to touch on this machine.
#   ~/.config/opencode/opencode.json and ~/.gemini/settings.json were added after the first
#   run: the original list covered only the four Claude/Codex files, and the toolkit then
#   modified these two as well. Verified afterwards that no existing server entry was lost
#   (the catalog file is byte-identical across both backups), but the gap is why they are
#   listed now.
CONFIGS=(
  "$HOME/.claude.json"
  "$HOME/.agents/mcp.json"
  "$HOME/.config/opencode/mcp.json"
  "$HOME/.config/opencode/opencode.json"
  "$HOME/.claude/mcp-configs/mcp-servers.json"
  "$HOME/.gemini/settings.json"
)

say()  { printf '  %s\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; exit 1; }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

[[ "$DRY_RUN" == 1 ]] && printf '\n\033[1mDRY RUN\033[0m — nothing will be changed or invoked\n'
printf '\nAgent Toolkit setup · profile %s · toolkit region %s\n' "$PROFILE" "$TOOLKIT_REGION"

# ── 1. Prerequisites ──────────────────────────────────────────────────────
head_ "1. Prerequisites"

command -v aws >/dev/null 2>&1 || die "aws CLI not found. Install it first: bash /tmp/awscli-install.sh"
ok "aws CLI: $(aws --version 2>&1 | head -1 | cut -d' ' -f1)"

for cmd in "aws login" "aws agent-toolkit" "aws configure agent-toolkit"; do
  $cmd help >/dev/null 2>&1 || die "'$cmd' is missing from this CLI version; the setup instructions may target a different one"
done
ok "aws login, aws agent-toolkit, aws configure agent-toolkit all present"

if [[ "$DRY_RUN" == 1 ]]; then
  warn "skipping the credential check in dry-run"
else
  if ! identity=$(aws sts get-caller-identity --profile "$PROFILE" --output json 2>&1); then
    die "no usable credentials for profile '$PROFILE':
      $identity

      This is the step only you can do. Create the AWS account (email + card + phone
      verification), then run 'make phase0' to create the IAM user, then re-run this script.
      The official instructions also require a browser sign-in here ('aws login'), which no
      script can complete on your behalf."
  fi
  account=$(printf '%s' "$identity" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Account"])')
  arn=$(printf '%s' "$identity" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Arn"])')
  ok "credentials work: $account as $arn"
  [[ "$arn" == *":root" ]] && die "these are ROOT credentials; create the IAM user from aws/iam-policy.json first"
fi

# ── 2. Backup ─────────────────────────────────────────────────────────────
head_ "2. Back up the MCP configuration files"

if [[ "$DRY_RUN" == 1 ]]; then
  BACKUP_DIR="$(ls -dt "$HOME"/.mcp-config-backups/*/ 2>/dev/null | head -1 || true)"
  BACKUP_DIR="${BACKUP_DIR%/}"
  if [[ -n "$BACKUP_DIR" ]]; then
    ok "reusing the most recent backup: $BACKUP_DIR"
  else
    warn "no backup exists yet; a real run would create one"
  fi
else
  BACKUP_DIR="$HOME/.mcp-config-backups/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$BACKUP_DIR"
  for f in "${CONFIGS[@]}"; do
    [[ -e "$f" ]] || continue
    rel="${f#"$HOME"/}"
    mkdir -p "$BACKUP_DIR/$(dirname "$rel")"
    cp -p "$f" "$BACKUP_DIR/$rel"
    cmp -s "$f" "$BACKUP_DIR/$rel" || die "backup verification failed for $rel"
    ok "backed up $rel"
  done
  ok "restore with: cp -a \"$BACKUP_DIR/.\" \"\$HOME/\""
fi

# ── 3. Configure the toolkit ──────────────────────────────────────────────
head_ "3. Configure the Agent Toolkit (instructions step 5)"

if [[ "$INTERACTIVE" == 1 ]]; then
  say "interactive mode: the wizard will prompt you to choose agents and skills"
  cmd=(aws configure agent-toolkit --region "$TOOLKIT_REGION" --profile "$PROFILE")
else
  say "--yes selects ALL detected agents and installs the default skills."
  say "The backup above is what makes that safe to do unattended."
  cmd=(aws configure agent-toolkit --yes --region "$TOOLKIT_REGION" --profile "$PROFILE")
fi
say "command: ${cmd[*]}"

if [[ "$DRY_RUN" == 1 ]]; then
  warn "not invoked in dry-run"
else
  if "${cmd[@]}"; then
    ok "toolkit configured"
  else
    die "the toolkit wizard failed. Troubleshooting:
      https://github.com/aws/agent-toolkit-for-aws/blob/main/setup-instructions/setup-troubleshooting.md"
  fi
fi

# ── 4. Optional: point aws-mcp at the profile ─────────────────────────────
head_ "4. AWS_MCP_PROXY_PROFILES (optional)"

if [[ "$INJECT_ENV" == 0 ]]; then
  say "skipped. Profile is '$PROFILE'; the generated aws-mcp entry falls back to it, so the"
  say "'JSON-RPC error: -32602' failure the instructions warn about cannot occur."
  say "Re-run with --inject-env when you add a second account and need cross-account switching."
elif [[ "$DRY_RUN" == 1 ]]; then
  warn "would inject AWS_MCP_PROXY_PROFILES=$PROFILE into every aws-mcp entry"
else
  python3 - "$PROFILE" "${CONFIGS[@]}" <<'PY'
import json, sys
from pathlib import Path

profile, paths = sys.argv[1], sys.argv[2:]
for raw in paths:
    path = Path(raw)
    if not path.exists():
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    servers = data.get("mcpServers") or {}
    entry = servers.get("aws-mcp")
    if entry is None:
        print(f"  ·  {path.name}: no aws-mcp entry")
        continue
    entry.setdefault("env", {})
    if entry["env"].get("AWS_MCP_PROXY_PROFILES") == profile:
        print(f"  ✓  {path.name}: already set")
        continue
    entry["env"]["AWS_MCP_PROXY_PROFILES"] = profile
    # Everything else in the file is preserved by the round-trip; the diff report in step 6
    # tells you how much the reformatting cost, and the backup undoes it.
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  ✓  {path.name}: AWS_MCP_PROXY_PROFILES={profile}")
PY
fi

# ── 5. Verify ─────────────────────────────────────────────────────────────
head_ "5. Verify (instructions step 6)"

if [[ "$DRY_RUN" == 1 ]]; then
  warn "not invoked in dry-run"
else
  if out=$(aws agent-toolkit list-available-skills --region "$TOOLKIT_REGION" --profile "$PROFILE" 2>&1); then
    count=$(printf '%s' "$out" | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    data = json.loads(raw)
except Exception:
    print("?"); raise SystemExit
skills = data.get("skills", data) if isinstance(data, dict) else data
print(len(skills) if isinstance(skills, list) else "?")
' 2>/dev/null || echo "?")
    ok "remote skill catalog reachable ($count skills available)"
  else
    die "list-available-skills failed:
      $out"
  fi
fi

# ── 6. Report what changed ────────────────────────────────────────────────
head_ "6. What changed in your MCP configuration files"

if [[ -z "${BACKUP_DIR:-}" ]]; then
  warn "no backup to compare against"
else
  for f in "${CONFIGS[@]}"; do
    [[ -e "$f" ]] || { printf '  %-44s (absent)\n' "${f#"$HOME"/}"; continue; }
    rel="${f#"$HOME"/}"
    ref="$BACKUP_DIR/$rel"
    if [[ ! -e "$ref" ]]; then printf '  %-44s (new file)\n' "$rel"; continue; fi
    if cmp -s "$f" "$ref"; then
      printf '  \033[32m·\033[0m %-44s unchanged\n' "$rel"
    else
      changed=$(diff "$ref" "$f" | grep -cE '^[<>]' || true)
      printf '  \033[33m!\033[0m %-44s %s changed line(s)\n' "$rel" "$changed"
      printf '      inspect: diff "%s" "%s"\n' "$ref" "$f"
      printf '      restore: cp -p "%s" "%s"\n' "$ref" "$f"
    fi
  done
fi

head_ "Done"
say "Next: restart your AI tool so it picks up the AWS MCP server, then ask it"
say "something that requires AWS and check that it answers rather than failing to start."
printf '\n'
