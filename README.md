# bedrock-agent-eval

[![gate](https://github.com/Samuel-Ku/bedrock-agent-eval/actions/workflows/ci.yml/badge.svg)](https://github.com/Samuel-Ku/bedrock-agent-eval/actions/workflows/ci.yml)

An Amazon Bedrock agent that turns a project-meeting transcript into structured Jira task
drafts, wrapped in the things that make an AI system trustworthy rather than merely
impressive: a native JSON-Schema output contract, a twenty-case evaluation gate, PII
guardrails on the input, and per-call token and cost accounting that ends in a routing
rule.

The interesting artefact here is not the agent. It is the measurement around it.

---

## What this demonstrates

Mapped to the requirements of the role this was built for:

| Requirement | Where it lives |
|---|---|
| Amazon Bedrock | `src/bedrock_client.py`, `src/agent.py` — Converse API, tool use, `outputConfig` |
| Python | the whole repository |
| Prompt engineering and prompt **testing**, structured outputs | `src/schema.py`, `evals/run_evals.py` — schema validity and field correctness scored separately |
| Designing **harnesses** for coding assistants; agentic patterns | the bounded tool loop, the deterministic escalation rule, the evaluation gate |
| Cost optimisation of GenAI solutions | `src/cost.py`, `experiments/compare.py` — a measured routing rule, not an assertion |
| Data security and risks in agentic workflows | `scripts/create_guardrail.py`, the PII-leak metric in the eval |
| MCP servers | `src/mcp_server.py` — a self-hosted server exposing the extraction, with bearer-token auth, a read-only surface, and a fail-closed HTTP transport |

## Why this is a platform artifact, not a feature

A product feature answers "does the extraction work". A platform answers four other questions,
and this repository is organised around them:

**Who is allowed to act?** Escalation between models is triggered only by a deterministic
signal — schema validation or malformed tool arguments — never by model self-assessment. No MCP
tool writes anywhere. The HTTP transport refuses to start without a token. The same allow-list
discipline shows up in the reasoning behind `src/tools.py`: the agent proposes, a human
disposes.

**How do you know it still works?** The evaluation gate is the delivery condition, not a
report. Validity and correctness are separate numbers so that a well-formed wrong answer is
visible as exactly that.

**What does it cost, and who owns that?** Every call is metered and attributed. The routing rule
exists because the numbers justified it, and the repository refuses to print a currency amount
until somebody has read real prices off the AWS page and dated them.

**Can another team pick it up?** This is the question a platform is really judged on. To adopt
this for a different extraction task you would replace `src/cases.py` with your own labelled
fixtures, point `src/tools.py` at your own data sources, decide your own escalation triggers,
and configure your own guardrail policy. Nothing else moves: the loop, the schema guard, the
gate, the cost accounting and the MCP surface are task-independent. That seam is the artifact.

## Quickstart

```bash
make install            # uv venv + editable install
make eval-offline       # runs the whole suite against a stub: no AWS, no cost
make check              # import every module, verify the schema passes the Bedrock checker
```

Offline mode exists so the plumbing — tool loop, schema validation, escalation, scoring,
reporting — can be exercised before an account exists and without spending anything.
It is a plumbing test, not a measurement: **the stub fabricates its token counts**, so
`--offline` prints no currency at all.

Then, with credentials in place:

```bash
make phase0             # guided: account, IAM user, budget alarm, Bedrock access, prices
make verify-aws ARGS=--invoke
make guardrail          # creates the guardrail and prints its id for .env
make evals              # 20 cases, routed arm
make compare            # the three-arm cost experiment
```

`make phase0` is an interactive wizard. It opens each console page, says exactly what to
click, captures the values, and writes them where they belong — non-secrets into the
gitignored `.env`, credentials into `~/.aws/credentials` and nowhere near the repository.
It cannot create the AWS account itself, because that needs a card and phone verification;
everything after that step it does for you. `docs/PHASE0.md` has the same procedure as
prose, plus what to do when a step fails.

`make test` runs the smoke tests with no AWS account: the schema guard, the scoring
helpers, the cost arithmetic, the escalation path, the guardrail simulation and the MCP
surface.

## Command line

```bash
bedrock-agent-eval extract --case case-05            # the case where the cheap model fails
bedrock-agent-eval extract --transcript-file notes.md --offline
bedrock-agent-eval extract --case case-04 --json     # the case with planted identifiers
bedrock-agent-eval cases                             # the twenty fixtures
bedrock-agent-eval contract --schema                 # the output contract Bedrock compiles
bedrock-agent-eval eval --offline --gate 0.9         # the gate, non-zero exit below 0.9
bedrock-agent-eval eval --arm all                    # per-model detail, live
bedrock-agent-eval compare                           # the three-arm cost experiment
bedrock-agent-eval price-check                       # refuses until prices are real
bedrock-agent-eval serve-mcp --transport stdio       # local MCP server
```

`extract` prints the provenance first — `mode: live-bedrock` or `mode: offline-stub` — and in
stub mode prints a warning above the answer. That ordering is on purpose: the answer is the
least trustworthy thing on the screen.

## MCP server

The extraction is more useful as a capability another agent can call than as a script a human
remembers to run. `src/mcp_server.py` exposes it over the Model Context Protocol.

**Five tools, one of which acts.** `extract_actions` runs the agent on a transcript;
`list_evaluation_cases`, `get_evaluation_case`, `output_contract` and `evaluation_summary`
only read. There is no tool that writes to Jira, to S3, or to anything else — an
agent-facing API that can mutate external state is a much larger security conversation than
this demo can honestly have. A test asserts the surface stays that way.

There is also a `contract://meeting-actions` resource carrying the JSON Schema, so a client
can discover the output shape without calling a tool.

### Local, over stdio

```bash
make mcp-stdio        # no token: the transport is a pipe, not a socket
```

```json
{ "mcpServers": { "bedrock-agent-eval": {
    "command": "uv", "args": ["run", "bedrock-agent-eval", "serve-mcp", "--transport", "stdio"],
    "cwd": "/path/to/bedrock-agent-eval" } } }
```

### Remote, over streamable HTTP

```bash
export MCP_BEARER_TOKEN=$(python -c 'import secrets;print(secrets.token_urlsafe(32))')
make mcp-http         # refuses to start without that variable
```

```json
{ "mcpServers": { "bedrock-agent-eval": {
    "type": "http",
    "url": "http://127.0.0.1:8765/mcp",
    "headers": { "Authorization": "Bearer ${MCP_BEARER_TOKEN}" } } } }
```

**HTTP fails closed.** Starting the HTTP transport without a token is a startup error, not a
warning, because a demo MCP server left listening on a laptop is a real incident and not a
configuration preference. The token is read from the environment rather than from a flag:
arguments are visible in `ps` and in shell history, environment variables are not, and the
verifier compares with `secrets.compare_digest` so the comparison does not leak the token's
prefix through timing.

Verified behaviour, for the record: request without a token → `401`; with the token →
`200` and an `Mcp-Session-Id`; `tools/list` returns the five tools above.

### Provenance on every response

Every `extract_actions` result carries `mode`. When it answers from the offline stub it also
carries a `warning` field and reports `cost_usd: null`. A caller that cannot distinguish a
simulated answer from a measured one will eventually quote the wrong one, so the distinction
is in the payload rather than in the documentation.

## Prices

`src/pricing.py` ships **empty on purpose**, and `assert_prices_loaded()` raises until it
is filled in — from `.env` (`PRICES_AS_OF` plus `PRICE_HAIKU_INPUT/OUTPUT` and
`PRICE_SONNET_INPUT/OUTPUT`) or by editing the table in that file. Two reasons, both of which
a reviewer can check:

1. Bedrock's pricing page renders its tables client-side, and Claude models on Bedrock are
   billed through AWS Marketplace under the model provider rather than under the
   `AmazonBedrock` offer code — so the rates could not be retrieved programmatically.
2. A cost table built on borrowed or remembered numbers is worse than no cost table. It is
   the one claim in this repository that anyone can falsify in thirty seconds.

`make phase0` walks you through reading them off the
[pricing page](https://aws.amazon.com/bedrock/pricing/) for `eu-north-1` and writes them
with today's date. An undated price is not evidence.

Note also that Bedrock's cache multipliers are **not** uniform across model families: the
Nova family is documented at 0.25× cache read with free cache writes, while Anthropic's
reference table uses 1.25× write / 0.1× read. Do not generalise a multiplier from one
family to another.

## Results

**The tables below read `TBD` because no live run has happened yet.** They are filled from a real
run against the account, and that run is currently gated by AWS account verification — the state,
the evidence and the exact next commands are in `docs/RESUME.md`. Nothing here is estimated or
carried over from an earlier run: an empty table is honest, and a plausible-looking one would not
be.

### Evaluation — `make evals`

<!-- Fill in from evals/results.json. Delete the placeholder rows. -->

| Metric | Value |
|---|---|
| Cases | 20 |
| Schema-valid rate | TBD |
| Fields-correct rate | TBD |
| End-to-end rate | TBD |
| Mean tool calls per case | TBD |
| Mean iterations per case | TBD |
| Cases that escalated | TBD |
| Source quotes grounded in the transcript | TBD / TBD |

### The cost experiment — `make compare`

<!-- Fill in from experiments/results.json, then state the date. -->

| Arm | Schema-valid | Fields-correct | End-to-end | Escalations | $ / case | $ / 1k cases | Mean latency |
|---|---|---|---|---|---|---|---|
| always-cheap | TBD | TBD | TBD | 0 | TBD | TBD | TBD |
| always-strong | TBD | TBD | TBD | 0 | TBD | TBD | TBD |
| routed | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

**The routing rule this produced:** _(one sentence — what the numbers justify, and at what
escalation rate it stops being worth it)_

`$ / 1k cases` is the measured total scaled by 50. It is an extrapolation from twenty
cases, not a measurement, and it is labelled that way wherever it appears.

Costs here are **list-price costs**: tokens multiplied by the rates read off the AWS pricing
page. They are not out-of-pocket spend. An account running on signup credits pays nothing while
those credits last, so the number this repository reports is what the workload *would* cost at
list price — which is the number worth comparing between arms, and the honest one to quote.

### Guardrail effect — `make evals` then `python -m evals.run_evals --guardrail off`

<!-- Two runs, same 20 cases, identical prompts. Fill in both leak counts. -->

| Input guardrail | PII leaking into the output | End-to-end rate |
|---|---|---|
| off | TBD | TBD |
| on | TBD | TBD |

The interesting number is the second column of the "on" row. If masking the identifiers
also costs extraction accuracy, that is the trade-off worth knowing about, and it is the
reason field correctness is reported next to leaks rather than instead of them.

The guardrail can also be checked **without invoking a model at all**, which matters on an account
where inference is restricted:

```bash
make verify-guardrail
```

That calls `ApplyGuardrail` directly over the identifiers planted in the fixtures and asserts the
observed action for each: PHONE and EMAIL anonymised, the card number blocked, the denied topic
fired, and clean text passed through untouched. "The guardrail is configured" and "the guardrail
masks what it claims to mask" are different claims, and only the second one is worth making.

Where `bedrock:ApplyGuardrail` is not granted, the script falls back to reading the stored policy
and checking the configuration, and **says so in the output** — a policy that reads correctly may
still fail to act, so that mode is labelled as configuration-only rather than passed off as the
behavioural check.

`make guardrail` is idempotent and publishes a **numbered, immutable version** rather than using
`DRAFT`: AWS's own guidance is that numbered versions are what production should reference, and an
evaluation should not run against a policy that can be edited underneath it. `DRAFT` is mutable.

---

## Six decisions worth reading the code for

### 1. The answer is schema-constrained, not parsed

The final response is generated under `outputConfig.textFormat` with a JSON Schema, so
Bedrock compiles the contract into a decoding grammar. Nothing parses free text, and
nothing hopes. The same Pydantic model generates the schema and validates the result, so
there is no second, drifting definition of the contract.

`src/schema.py` also encodes Bedrock's documented schema restrictions — no recursion, no
external `$ref`, no numeric or length constraints, `additionalProperties` must be `false`,
`enum` values must be scalars, `minItems` only 0 or 1 — as an assertion that runs at
import. Sending an unsupported construct is an HTTP 400 *in the middle of a run*; finding
out at import instead is free.

### 2. Validity and correctness are scored separately

A single blended score lets a well-formed but wrong answer hide behind a well-formed
answer. So the gate reports schema validity and field correctness on their own axes, plus
a third metric for whether each task's `source_quote` can actually be found in the
transcript. A quote that cannot be located means the justification was invented, whatever
else the answer got right.

### 3. Escalation is triggered only by a deterministic signal

Cheap model first; strong model only when the cheap attempt fails schema validation or
produced malformed tool arguments. Model self-reported confidence is not a trigger: a
routing rule you cannot reproduce is not a routing rule. Guardrail interventions are
reported as their own metric rather than folded into the trigger, because "the answer was
wrong" and "the answer was blocked" have different owners.

### 4. The tool loop is bounded, and tools are side-effect free

`MAX_TOOL_ITERATIONS` is enforced in code, because an agent that can loop without a cap
turns a bug into an invoice. No tool writes to Jira: the agent proposes, a human disposes.
That keeps the evaluation deterministic *and* keeps the demo honest about what should be
allowed to run unattended.

### 5. Cost accounting is written for a bug that is not present

`usage.inputTokens` reports only the non-cached input when prompt caching is on; the
billable input is `inputTokens + cacheReadInputTokens + cacheWriteInputTokens`. This
project does not use prompt caching, so the fields are always empty — but the accounting
handles them, because the first person to add a `cachePoint` should not also have to
discover that.

---

### 6. Schema enforcement is model-specific, so it is probed and then stated

Native structured outputs are not a Bedrock-wide feature — they are a per-model one, and the
documentation only says so on individual model cards. So the repository refuses to assume it:

- `scripts/probe_structured_outputs.py` asks each candidate model three questions with one tiny
  call each: can it be invoked, does `outputConfig` work, does tool use work. Results land in
  `evals/model_capabilities.json`, per model per region.
- `src/model_capabilities.py` distinguishes **three** states, not two: supported, unsupported, and
  *unknown*. Collapsing the last two would either send an unsupported parameter and fail every
  case, or silently drop schema enforcement from a model that supports it — and both produce a
  pass rate that looks like a model result while being a harness bug.
- When a model is known not to support it, `src/agent.py` omits `outputConfig`, states the
  contract in the prompt instead, and records `structured_output: "prompt-only"` per case. The
  validation is unchanged, so the answer is still judged the same way — but a reader can tell
  which mechanism produced it.

That is what makes the repository model-portable, and therefore able to answer the cost question
the posting actually asks: the third arm in `experiments/compare.py` runs a cheap non-Claude model
(DeepSeek V3.2 by default, `THIRD_MODEL_ID` to change it) against the two Claude tiers on the same
twenty cases. If its prices are not recorded the arm is dropped with a note rather than failing
the run.

## Data residency versus feature availability

This is the trade-off a bank will ask about, and it is a real fork rather than a detail.

For Claude Haiku 4.5 and Sonnet 4.5 in `eu-north-1`, on `bedrock-runtime`, **in-region
on-demand is not offered**: the model cards state that a geo or global inference profile
ID is required. The `eu.` prefix selects the EU geographic profile, which keeps requests
inside EU regions and is documented as priced at source-region rates with no cross-region
surcharge. That is the path this project uses.

The alternative — `bedrock-mantle`, which does offer Stockholm in-region for these models
— **does not support native structured outputs**. So:

| Path | Data residency | Native structured outputs |
|---|---|---|
| `bedrock-runtime` + `eu.` profile (used here) | EU regions; docs warn geo requests may still be routed to opt-in regions and stored there for abuse detection | yes |
| `bedrock-mantle`, Stockholm in-region | strongest available | **no** — falls back to strict tool use or client-side validation |

Choosing schema enforcement therefore chooses cross-region routing. That is a deliberate,
documented decision rather than an accident, and in a regulated environment it is the kind
of thing that should be a written decision with an owner — not a default that a library
picked.

## Native Bedrock evaluations

`evals/export_native_dataset.py` exports the same twenty cases into the JSONL shape a
native Bedrock evaluation job consumes (`prompt`, `referenceResponse`, optional
`category`; maximum 1000 prompts per job). The local harness remains the primary gate
because Bedrock evaluation jobs need S3 buckets, an IAM role and asynchronous polling —
more infrastructure than twenty cases justify, and a judge-model metric where a
`json.loads` plus schema validation is exact and free.

The export exists so the dataset is portable: the same fixtures can feed either path, and
`docs/NATIVE_EVAL.md` records the steps.

## Limitations, stated rather than discovered

- **Twenty cases is twenty cases.** The pass rates have real uncertainty on them, and the
  per-1k-cases cost is an extrapolation.
- **The fixtures are synthetic.** They are authored, not collected, so the ground truth is
  objective — but they are also cleaner than real meeting transcripts. Real transcripts
  contain cross-talk, corrections and decisions that are later reversed.
- **One schema, one domain.** The routing saving measured here is specific to this task's
  error rate. A task where the cheap model is wrong more often will escalate more often
  and save less; the break-even escalation rate is the number to carry across, not the
  percentage.
- **Tool results re-enter the prompt.** The guardrail is applied to the request, so tool
  output is covered on the following turn. No tool in this repository returns PII, so this
  is a design property rather than a tested one.
- **No caching.** Prompt caching was evaluated and dropped: Haiku 4.5's minimum cacheable
  prefix is 4,096 tokens, below which caching silently does nothing and raises no error,
  while Sonnet 4.5's floor is 1,024 — so a with/without-caching comparison over short
  cases would measure whether caching engaged rather than what it saved.
- **One rate per model, but AWS charges by tier and routing.** AWS reports standard, priority and
  flex service tiers, and cross-region routing, as separate usage types with different unit
  prices. This repository records a single standard-tier cross-region input/output pair per
  model, so the figure is a list-price estimate for that variant rather than a reconciliation of
  the bill. What it does get right, per AWS's own guidance, is accounting for all four token
  types — input, output, cache read and cache write — which the documentation names as the most
  common source of reconciliation gaps.
- **The price is a list price, not money spent.** An account running on signup credits pays
  nothing while those credits last. The number reported here is what the workload *would* cost at
  list price, which is the right number for comparing arms and the honest one to quote.

## Next steps

- **Native evaluation job.** One judge-based job over the exported dataset, as a second
  opinion next to the deterministic gate. See `docs/NATIVE_EVAL.md`.
- **Prompt-trimming axis.** Full transcript against a trimmed context, to add a
  cost/quality curve that does not depend on a cache threshold — the axis that replaced
  prompt caching, and the next honest place to look for savings.
- **Managed Bedrock Agents.** This project uses the Converse API with a client-side tool
  loop, which keeps the loop, the escalation rule and the trace visible and testable. A
  managed Agent resource with an action group is the other architecture, and it would trade
  some of that visibility for operational convenience. Worth building once, to be able to
  compare them rather than argue about them.

## Layout

```
src/
  config.py             region, inference-profile model IDs, limits
  schema.py             the contract + the Bedrock schema-restriction checker
  model_capabilities.py what each model was probed to support, in three states
  tools.py              three deterministic, side-effect-free tools
  cases.py              the twenty authored fixtures (single source of truth)
  bedrock_client.py     live Converse client + the offline stub
  agent.py              bounded tool loop, schema-constrained answer, routing rule
  cost.py               token and cost accounting
  pricing.py            prices, deliberately empty until read from a real source
  cli.py                the command-line interface
  mcp_server.py         the MCP server: five tools, one resource, fail-closed auth

evals/
  run_evals.py              the gate: schema validity, field correctness, PII leaks
  export_native_dataset.py  JSONL export for a native Bedrock evaluation job
  model_capabilities.json   the probe's output, read by the agent
  native_dataset.jsonl      the exported dataset

experiments/
  compare.py            four arms: always-cheap, always-strong, always-third, routed

scripts/
  # provisioning, in the order they are needed
  phase0-wizard.sh          walks a human through the console steps
  bootstrap_iam_user.py     the scoped user, its policy and one access key
  bootstrap_aws.py          the budget alarm and its SNS alert topic
  switch_off_bootstrap_session.py  drops the privileged session afterwards
  accept_model_agreement.py the gate nobody documents
  create_guardrail.py       the PII and denied-topic guardrail, versioned
  # prices
  fetch_prices_from_offers.py  authoritative rates from the signed agreement
  set_prices.py                the fallback: four numbers, validated
  # verification
  verify_aws_setup.py       credentials, model access, prices, guardrail, budget
  verify_guardrail.py       the guardrail's behaviour, or its configuration
  probe_structured_outputs.py  per-model invocation, native schema, tool use
  # running
  run_live_evaluation.sh    the whole live sequence, fail-fast, then README rows
  summarise_results.py      turns recorded runs into the table rows above
  setup_agent_toolkit.sh    AWS's Agent Toolkit, with the backups it needs

aws/
  iam-policy.json       the least-privilege policy, EU-locked by condition

tests/
  test_smoke.py         36 tests, no AWS account required

docs/
  DESIGN.md             why each decision was made, including the rejected ones
  PHASE0.md             the account-provisioning runbook and the four Bedrock gates
  NATIVE_EVAL.md        the native evaluation job runbook
  AGENT_TOOLKIT.md      the Agent Toolkit setup record, deviations included
  RESUME.md             current state, the account blocker, and the next commands

.github/workflows/ci.yml  lint, tests and the offline gate, with no credentials
AGENTS.md                 repository instructions, including AWS's own rules
Makefile                  every entry point, discoverable with `make help`
```

## Data note

Every transcript, name, phone number, email address and card number in this repository is
fabricated. Email addresses use the reserved `example.com` domain and the card number is
the standard test prefix. No employer data and no real personal data appear anywhere in
this repository.
