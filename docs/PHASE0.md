# Phase 0 — AWS account, guardrails and Bedrock access

Phase 0 is everything that must exist before the first live Bedrock call. It is the only part
of this project that depends on someone else's queue, so it happens first and separately.

**Run it as a wizard:**

```bash
make phase0          # interactive: opens each page, tells you what to click, captures values
make verify-aws ARGS=--invoke
```

The rest of this document is the same procedure as prose — for reading beforehand, for when a
step fails, and for whoever picks this repository up next.

---

## The bootstrap session expires, and that is the design

`aws login` mints short-lived credentials with a refresh token. The token is valid for a while and
then it is not: this project's bootstrap session died on its own during the same evening, and the
only symptom is a `CreateOAuth2Token` failure reading *"The provided authorization grant is
invalid, expired, revoked, or malformed"*.

That is the intended behaviour, not a fault: the privileged session is supposed to be brief. When
an administrative step needs it again — accepting a new model agreement, updating the IAM policy,
subscribing to another model — re-run:

```bash
aws login --region eu-north-1 --profile bootstrap   # or --profile default
```

Do the administrative work, then drop it again:

```bash
python scripts/switch_off_bootstrap_session.py --profile bootstrap
```

The script backs up `~/.aws/config` and archives the cached tokens rather than deleting them, so
this is reversible and repeatable. After it runs, `~/.aws/config` is empty and the only identity
left is the scoped IAM user — which is the state this project is supposed to sit in.

**The scoped user deliberately cannot do the administrative steps.** It has no `iam:*`, no
`servicequotas:*`, no `freetier:*`, and no `bedrock:CreateFoundationModelAgreement`. Accepting a
provider agreement, changing a plan or widening a policy are decisions for a human with an admin
identity, and a token-billing service account should not be able to repeat them.

## Why one step cannot be automated

**Creating the AWS account is yours alone.** It requires an email address not already tied to
an AWS account, a payment card, phone verification, and sometimes identity verification. No
script can do that, and no script should: it is your personal financial instrument and your
legal agreement with AWS.

**Four separate gates stand between a new account and a working `Converse` call, and each one
hides behind the previous one's error message.** This cost a debugging session to establish, and
it is written down because every error in the chain points at the wrong thing:

| # | Gate | How it fails | How it is opened |
|---|---|---|---|
| 1 | Anthropic use-case form | `ResourceNotFoundException: You have not filled out the request form` | `put_use_case_for_model_access` |
| 2 | **Model agreement** | `Converse` says *"Model use case details have not been submitted"* — blaming gate 1, which is already done | `make model-agreement` |
| 3 | Marketplace grant | `AccessDeniedException: not authorized to perform aws-marketplace:ViewSubscriptions, aws-marketplace:Subscribe` | the `MarketplaceModelSubscription` policy statement |
| 4 | **Account state** | `ThrottlingException: Too many tokens per day` on every model, **including Amazon's own** — and in `eu-west-1` the same restriction reports itself honestly as `AccessDeniedException: Your account is currently being verified` | let account verification finish; the plan is a *possible* contributing factor, not an established one |

Only `get_foundation_model_availability` reports the gates honestly and separately:

```bash
python scripts/accept_model_agreement.py --check-only --profile bootstrap
#   agreement AVAILABLE | authorization AUTHORIZED | entitlement AVAILABLE | region AVAILABLE
```

Do not let the error text tell you which gate you are at. At the time of writing, all four gates
above read green while `Converse` still returned `Too many tokens per day`: the account was on
the Free plan (`accountPlanType: FREE`), and its listed service quotas were in the hundreds of
millions and essentially unused. Every Bedrock model was throttled — including Amazon Nova,
which needs neither the agreement nor a Marketplace subscription — while non-Bedrock APIs on the
same account worked normally. That combination points at the account, not at the request.

**The credits are not the constraint.** The account's remaining credits were untouched throughout,
and a Free-plan account cannot spend them on a service the plan does not grant. Upgrading keeps
the credits and bills only for what they do not cover.

Check the account state directly (this needs `freetier:*`, which the scoped runtime user
deliberately does not have, so use root or an admin profile):

```bash
aws freetier get-account-plan-state --region us-east-1
# accountPlanType: FREE | PAID, plus remaining credits and their expiry
```

Everything *after* account creation is automated by `make phase0`: the IAM user, the
least-privilege policy, the credentials file, the budget alarm, the price capture, and the
verification. That division — human does the identity step, automation does the rest — is
exactly how provisioning should be split, and it is worth saying out loud in an interview.

---

## What each stage produces

| # | Stage | You do | Value captured | Written to |
|---|---|---|---|---|
| 1 | Create the AWS account | email, card, phone verification | — | — |
| 2 | Secure the root user | assign MFA to root | — | — |
| 3 | Create the IAM user | paste `aws/iam-policy.json`, create an access key | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | `~/.aws/credentials`, mode 0600 — **never the repo** |
| 4 | Record the account id | copy it from the billing page | `AWS_ACCOUNT_ID` | `.env` (gitignored) |
| 5 | Budget alarm | click the SNS confirmation email | `BUDGET_ALERT_EMAIL` | `.env`, plus the SNS topic and budget in AWS |
| 6 | Request model access | tick the two Claude models in the console | — | — |
| 7 | Read the prices | read four numbers off the pricing page | `PRICES_AS_OF`, `PRICE_*` | `.env` |
| 8 | Verify | nothing | — | — |

Guardrails are **not** part of Phase 0 — they are Phase 5, run with `make guardrail`.
Everything here is needed for the evaluation to run at all; guardrails can be added after.

---

## The security decisions, and why

These are four deliberate choices, not defaults. Each is the kind of thing a bank will ask
about, so each is written down rather than assumed.

**Credentials never enter the repository.** They are written to `~/.aws/credentials` with mode
0600. The repository's `.env` is gitignored too, but a public repository is one careless
`git add -A` away from leaking, and the credentials file is not inside it at all. Non-secrets
(the account id, the alert email, the prices) do go in `.env` because they are harmless and
the scripts need them.

**No root access keys, ever.** Root cannot be restricted by any policy. It gets MFA and then it
is not used again for this project.

**Bedrock is confined to EU regions — and this was corrected by measurement, not by guesswork.**
The policy's `LockBedrockToEuRegions` statement denies every `bedrock:*` action outside a list
of EU regions, using the `aws:RequestedRegion` condition. It is a spend control and a
data-residency control in one statement: a stale script or a mistyped region cannot ship prompts
to a different jurisdiction or a different price list.

The first version of this policy allowed only `eu-north-1`. **That version was wrong, and the
failure was found on the first live call.** The two Claude 4.5 models are not offered in-region
on `bedrock-runtime` — invoking the bare model id returns *"on-demand throughput isn't
supported"* — so every request goes through the `eu.` geographic inference profile, which routed
the call to `eu-west-3`. The single-region deny then rejected it: the policy was blocking the
very call it was written to permit.

The correction is not merely a longer list. It is a more honest claim: what the `eu.` profile
actually guarantees is **EU residency**, not single-region residency, so that is what the policy
enforces. A single-region lock would have asserted a property the architecture cannot deliver.

Two details worth keeping: the lock is written as a **condition**, not a resource list, because
resource-based denials break actions that take no resource (like `ListFoundationModels`); and
`tests/test_smoke.py` asserts both that `eu-west-3` is allowed and that **every** allowed region
starts with `eu-`, so a future edit cannot quietly widen it outside the EU.

**A budget alarm exists before anything can spend.** A runaway agent loop is the only way this
project gets expensive, and the alarm is what turns "I forgot the iteration cap" from an
invoice into an email. It alerts at 5 USD actual, 10 USD actual, and 10 USD forecast, to an SNS
topic with your address subscribed. **The alarm is inert until you click the confirmation link
AWS emails you** — this is the single most common way people end up with an alarm that never
fired.

### What the policy deliberately does not do

It does not require MFA on the API calls. That is a real trade-off: `aws:MultiFactorAuthPresent`
would be stronger, but it requires an STS session with MFA, which would break every scripted
run in this repository. The compensating controls are the region lock, the narrow action list,
and the budget alarm. In a shared account with other engineers, the right answer would be
different, and this paragraph is where that change would be recorded.

It also cannot scope model invocation to a specific model ARN. Cross-region inference profiles
resolve to ARNs that include a region and account that vary by request, so `Resource: "*"` is
unavoidable for the invoke actions. The actions are invoke-and-read only, and the budget alarm
bounds the consequence. Being able to explain *why* the wildcard is there is worth more than
pretending it is not.

---

## The IAM policy in this repository

`aws/iam-policy.json` is the exact policy the wizard has you paste. It grants:

- `bedrock:InvokeModel`, `InvokeModelWithResponseStream`, `Converse`, `ConverseStream`, `CountTokens`;
- read-only Bedrock metadata (`ListFoundationModels`, `GetInferenceProfile`, `GetGuardrail`, …);
- guardrail lifecycle (needed by `make guardrail`);
- `budgets:ViewBudget` / `ModifyBudget` and the SNS actions the alert topic needs;
- a deny on all Bedrock actions outside the EU region list.

It is published on purpose. In an interview, "here is the policy, and here is what I could not
scope and why" is a stronger answer than a description of having thought about permissions.

---

## When a step fails

| Symptom | Cause | Fix |
|---|---|---|
| `Unable to locate credentials` | stage 3 did not finish, or the file is elsewhere | re-run `make phase0`, or check `~/.aws/credentials` exists |
| `InvalidClientTokenId` / `SignatureDoesNotMatch` | wrong or truncated key | create a new access key in the IAM console and re-run stage 3 |
| `AccessDenied` on `ListFoundationModels` | policy not attached to the user | attach `bedrock-agent-eval` to the user |
| `AccessDenied` mentioning a region | the region lock is doing its job | use `eu-north-1`, or widen the policy if you meant to |
| Model access denied on invoke | access still pending | the console shows `Access granted` only after approval; usually minutes, occasionally hours |
| `ValidationException` about the model id | bare model id instead of an inference profile | the `eu.` prefix is required; see `src/config.py` |
| Budget created but no email | the SNS subscription is unconfirmed | click the confirmation link, then re-run stage 5 |
| Verification says `no price date is set` | stage 7 skipped | re-run `make phase0`, or set `PRICES_AS_OF` in `.env` by hand |

## What is worth sending back

Nothing here is needed for the scripts to work — `make verify-aws` is the source of truth. But
two things are worth a second pair of eyes:

1. **The four price numbers**, if you want them sanity-checked against the expected shape
   (input rates are typically a third to a fifth of output rates; Haiku should be materially
   cheaper than Sonnet).
2. **The verification output**, if anything shows a warning rather than a tick.

Do **not** send credentials, the account id, or the contents of `~/.aws/credentials`.

## After Phase 0

```bash
make evals                          # 20 cases, live, routed arm
make compare                        # the three-arm cost experiment
make guardrail                      # Phase 5, then re-run evals with and without
```

Then fill the README results tables with the real numbers, and update the CV's Bedrock section
against them. Nothing in either document may claim a number that Phase 0's run did not produce.
