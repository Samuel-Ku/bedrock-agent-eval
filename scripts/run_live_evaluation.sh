#!/usr/bin/env bash
#
# The whole live evaluation, in the order that fails fastest.
#
# Written so that the moment the account stops blocking Bedrock, one command produces every
# number the README and the CV are waiting for. It checks reachability first and stops there if
# the account is not ready, rather than running twenty cases into a wall.
#
#   bash scripts/run_live_evaluation.sh              # full run
#   bash scripts/run_live_evaluation.sh --skip-guardrail
#
# Produces:
#   evals/results-guardrail-off.json   20 cases, routed arm, no guardrail
#   evals/results-guardrail-on.json    20 cases, routed arm, guardrail on (if configured)
#   experiments/results.json           the cost arms
# and then prints the README rows, generated from those files.

set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "${BASH_SOURCE[0]}")/.."

SKIP_GUARDRAIL=0
[[ "${1:-}" == "--skip-guardrail" ]] && SKIP_GUARDRAIL=1

say()  { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }

say "1. Is Bedrock reachable at all?"
if uv run python - <<'PY'
import sys
import boto3
from src import config
try:
    boto3.client("bedrock-runtime", region_name=config.REGION).converse(
        modelId=config.CHEAP_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": "Reply with one word: ok"}]}],
        inferenceConfig={"maxTokens": 8, "temperature": 0},
    )
except Exception as exc:
    print(f"    {type(exc).__name__}: {str(exc)[:200]}")
    sys.exit(1)
PY
then
  ok "the cheap model answered"
else
  bad "Bedrock is not reachable, so nothing below would produce a real number."
  echo
  echo "  This is the account gate, not a code problem. See docs/RESUME.md."
  echo "  Do not continue by filling the tables by hand."
  exit 1
fi

say "2. Prices"
if uv run python -c "from src import config, pricing; pricing.assert_prices_loaded([config.CHEAP_MODEL_ID, config.STRONG_MODEL_ID])" 2>/dev/null; then
  ok "prices loaded ($(uv run python -c "from src import pricing; print(pricing.PRICES_AS_OF)"))"
else
  bad "no prices recorded. Run \`make prices\` or \`python scripts/fetch_prices_from_offers.py --write\`."
  echo "  Without them the cost experiment refuses to run, deliberately: an unpriced cost table"
  echo "  is the one claim in this repository that anyone can falsify in thirty seconds."
  exit 1
fi

say "3. The guardrail, verified without the model"
if [[ "$SKIP_GUARDRAIL" == 0 ]] && uv run python -c "from src import config; raise SystemExit(0 if config.GUARDRAIL_ID else 1)"; then
  if uv run python scripts/verify_guardrail.py; then
    ok "the guardrail masks what it claims to"
  else
    warn "guardrail verification did not pass — the leak numbers below would be meaningless"
  fi
else
  warn "skipping: no GUARDRAIL_ID configured (run \`make guardrail\`)"
fi

say "4. The evaluation, guardrail off"
uv run bedrock-agent-eval eval --arm routed --guardrail off --out evals/results-guardrail-off.json
ok "wrote evals/results-guardrail-off.json"

if [[ "$SKIP_GUARDRAIL" == 0 ]] && uv run python -c "from src import config; raise SystemExit(0 if config.GUARDRAIL_ID else 1)"; then
  say "5. The evaluation, guardrail on"
  uv run bedrock-agent-eval eval --arm routed --guardrail on --out evals/results-guardrail-on.json
  ok "wrote evals/results-guardrail-on.json"
else
  warn "skipping the guardrail arm"
fi

say "6. The cost experiment"
uv run bedrock-agent-eval compare
ok "wrote experiments/results.json"

say "7. The README rows, generated from those files"
uv run python scripts/summarise_results.py --check

printf '\n\033[1mDone.\033[0m Paste the rows above into README.md, then update the CV Bedrock section\n'
printf 'against the same numbers. Nothing may be quoted that a run did not produce.\n\n'
