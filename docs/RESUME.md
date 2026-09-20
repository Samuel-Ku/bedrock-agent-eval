# Resume here

State of play at the point the account hit its wall, and the exact commands to pick it up. Written
to be read cold.

---

## Where things actually stand

**Everything on this side is done and verified.** The one thing that is not working is Bedrock
inference on the AWS account, and it is not a configuration problem on our side.

| Component | State |
|---|---|
| AWS CLI | 2.36.49 installed, signature verified |
| IAM user `bedrock-agent-eval` | created, least-privilege policy attached, access key in `~/.aws/credentials` (0600) |
| Root access | **gone** — the bootstrap session expired on its own; `~/.aws/config` is empty |
| Region lock | EU-wide deny on `bedrock:*`, corrected after a live call was routed to `eu-west-3` |
| Marketplace permission | `aws-marketplace:ViewSubscriptions` / `Subscribe` added; `Unsubscribe` deliberately not |
| Model agreement | accepted for both Claude 4.5 models (`agreement=AVAILABLE`) |
| Budget alarm | `bedrock-agent-eval-monthly`, 10 USD, 3 thresholds, SNS topic + email |
| Prices | **not recorded** — needs a human to read four numbers (`make prices`) |
| Bedrock inference | **blocked**: `ThrottlingException: Too many tokens per day` on every model |
| Agent Toolkit | 23 skills installed, 105 in the catalogue, `aws-mcp` configured for 4 agents |
| Repo | 32 tests pass, `ruff` clean, offline eval green |

## The blocker, precisely

**The account is under AWS verification.** That is the explicit finding, and it took a
region-by-region probe to surface, because the same underlying restriction is reported as
different errors depending on which edge answers:

| Region | Model | Response |
|---|---|---|
| eu-north-1 | Nova Lite | `ThrottlingException: Too many tokens per day` |
| eu-central-1 | Nova Lite | `ThrottlingException: Too many tokens per day` |
| eu-central-1 | Claude Haiku 4.5 | `ThrottlingException: Too many tokens per day` |
| **eu-west-1** | Nova Lite | **`AccessDeniedException: Your account is currently being verified`** |

The eu-west-1 message is the only one that names the cause. The others are surface symptoms of
the same state, and they are extremely misleading: `Too many tokens per day` reads like a quota
or a throttling problem, and it sent this investigation down two wrong paths before the explicit
message appeared.

**It is also intermittent.** Minutes after naming verification, all three regions reverted to the
throttle message. The account state appears to fluctuate while verification completes, so a single
probe is not conclusive — which is why `scripts/probe_structured_outputs.py` now asks a second
region and prefers the honest answer when the two disagree.

**And the error string itself is not documented.** AWS's own troubleshooting page for Bedrock
throttling lists exactly three client-side messages — *"Too many requests… you have sent too many
requests"*, *"Your request rate is too high"*, and *"Too many tokens, please wait before trying
again"*. Ours is **"Too many tokens per day"**, which is not among them. So this is an undocumented
daily-bucket restriction, and the one message that names a cause (**account verification**) is the
best available explanation until the account state changes.

That check was possible at all only because the AWS MCP server, set up earlier in this session,
exposes `aws___search_documentation` — a general web search was unavailable.

The evidence that it is not a limit we tripped:

- **first-ever calls** to models never used on this account (DeepSeek V3, GLM 4.7 Flash, Qwen3
  32B, GPT-OSS 20B) returned the same message immediately — the meter reads zero and the limit
  still trips;
- the account-level quota (`Cross-Model Account-Level Tokens Per Day`) sits at **150,000,000** and
  is `Adjustable: false`, so there is nothing to raise;
- every model, from six providers, fails identically, while non-Bedrock APIs on the same account
  (SNS, Budgets) work normally;
- all four availability flags read
  `agreement=AVAILABLE authorization=AUTHORIZED entitlement=AVAILABLE region=AVAILABLE`.

The account is on the **Free plan** (`accountPlanType: FREE`). Whether the plan contributes to the
restriction is **not established** — the explicit error
blames verification, and the earlier conclusion that the plan was the cause was reached from
circumstantial evidence and should not be repeated as fact.

## What to do, in order

### 1. Let verification finish, and check for its paperwork

New AWS accounts are verified, and Bedrock is one of the services held back until that completes.
Usually hours; occasionally it waits on something from you.

- check the email address the account was created with for anything from AWS about verification
  or additional documents;
- open <https://console.aws.amazon.com/bedrock/home?region=eu-west-1#/modelaccess> — the console
  tends to show the verification banner where the API only shows an error code;
- re-probe at intervals: `python scripts/probe_structured_outputs.py` records the reason per model,
  so the moment the state changes it is visible in `evals/model_capabilities.json`.

### 2. The free playground activity — two minutes, and it pays 20 USD

There is a listed onboarding activity, **"Use a foundation model in the Amazon Bedrock
playground"**. It is worth +20 USD of credits and it is the cheapest test of whether Bedrock works
on this account at all:

- <https://console.aws.amazon.com/bedrock/home?region=eu-north-1#/playgrounds>
- pick any model, send any prompt, see whether it answers

If the playground answers while the API does not, that is a different and more interesting finding.
If it also fails, the verification restriction is confirmed from the console side too.

### 3. If verification drags on: raise it with AWS, and consider the plan only then

Verification is not something a script can hurry. If it is still pending after a day or two, that
is an AWS Support conversation, not a configuration change.

Upgrading to Pay-as-you-go is worth doing **if and only if** verification completes and Bedrock is
still restricted — the earlier claim that the plan was the cause was inference, not evidence, and
it has been corrected twice in this project's own documents. Upgrading keeps the signup credits and
bills only for what they do not cover; the whole workload costs well under a dollar.

### 4. Record the prices

```bash
cd bedrock-agent-eval
make prices        # prompts for four numbers, refuses transposed or implausible ones
```

Read them from <https://aws.amazon.com/bedrock/pricing/> with the region set to EU (Stockholm).
The script validates input-vs-output and Haiku-vs-Sonnet orderings, so a slip is caught rather
than stored.

There are two routes, and the second is the one worth taking:

**Fast, needs nothing:** `make prices` prompts for four numbers and validates them. Two minutes,
always available.

**Authoritative and automatic:** `python scripts/fetch_prices_from_offers.py --write` reads the
rates straight out of the agreement this account has already signed, keeps the raw rate card at
`evals/model_rates.json` as evidence, and refuses to guess when the dimension choice is ambiguous.
It is the better source because it is the account's own agreed terms rather than a marketing page.

It needs `bedrock:ListFoundationModelAgreementOffers`, which **the policy in
`aws/iam-policy.json` now grants** — but the running policy in IAM predates that edit, so it has to
be applied once with an admin identity.

That one session is worth taking for two reasons, not one. The policy file now also carries
`bedrock:ApplyGuardrail`, which lets `make verify-guardrail` check the safety policy **without
invoking a model** — the only way to verify it on an account where inference is refused.

```bash
aws login --region eu-north-1 --profile bootstrap
python scripts/bootstrap_iam_user.py --profile bootstrap --policy-only
python scripts/switch_off_bootstrap_session.py --profile bootstrap
python scripts/fetch_prices_from_offers.py --write   # prices, automatically
make verify-guardrail                                # the guardrail, independently
```

After that both steps are automatic for good, on this account and any future one set up from this
policy file.

**What was deliberately not done with it.** The AWS MCP sandbox (`aws___run_script`) executes code
with AWS API access, and it was tested once — for a read-only price lookup, which it refused with
`OperationNotFoundError`. It was not used to touch IAM. Using a sandbox to widen our own policy
would be privilege escalation, whatever the motive, and it would invalidate the least-privilege
claim this project rests on.

### 5. Run everything

```bash
make verify-aws ARGS=--invoke   # expect all green
make eval-offline               # sanity, no AWS
make evals                      # the 20-case gate, live
make compare                    # four arms: cheap, strong, third-party, routed
make guardrail                  # Phase 5, then re-run evals with and without
```

Then fill the README results tables and the CV's Bedrock section from the real numbers. Nothing in
either document may claim a number the run did not produce.

## The watcher

A background job is retrying a tiny Bedrock call every 20 minutes for six hours in case the
throttle lifts on its own.

```bash
tail -f bedrock-agent-eval/evals/throttle-watch.log
```

`evals/throttle-lifted.marker` appears the moment it succeeds. If the marker exists, skip straight
to step 3 — the plan question answered itself.

## Security state, so nothing is left loose

- **No root access exists.** The bootstrap session expired and its profile was removed;
  `~/.aws/config` is empty.
- Only `~/.aws/credentials` holds a secret: the scoped IAM user's key, mode 0600, gitignored
  location, never inside the repository.
- The root login cache is archived at `~/.aws/login.archived-*` and is dead.
- MCP configuration files were backed up before the Agent Toolkit touched them, at
  `~/.mcp-config-backups/20260920-181415/` and `20260920-183741/`, each `cmp`-verified:
  `cp -a ~/.mcp-config-backups/20260920-183741/. "$HOME/"` restores.
- The Agent Toolkit rewrote two configuration files that the first backup pass did not know
  about (`~/.config/opencode/opencode.json` and `~/.gemini/settings.json`). They are in the
  script's list now, and every pre-existing server entry was verified still present.

## If something in the repo misbehaves

```bash
make test                        # 32 tests, no AWS needed
uv run python -m evals.run_evals --offline
uv run python scripts/accept_model_agreement.py --check-only   # needs an admin identity
uv run python scripts/verify_aws_setup.py
```

`docs/PHASE0.md` has the four-gate table and a troubleshooting matrix. `docs/DESIGN.md` records why
each decision was made, including the ones that were rejected.
