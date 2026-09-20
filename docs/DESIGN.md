# Design decisions

Why this repository is shaped the way it is, including the decisions that were rejected.
Read this before the code: the code shows *what*, and most of the value here is in *why*.

Each entry states the decision, the reasoning, and what was rejected — because a decision
without a rejected alternative is not a decision, it is a default.

---

## 1. Domain: meeting transcript → Jira task drafts

**Decision.** The agent extracts tasks, decisions and risks from a project meeting
transcript and emits them in a shape that could be posted to Jira.

**Why.** Two reasons, and neither is about the domain being fashionable.

The first is that the ground truth is *decidable*. The author of the input is the author
of the expected output, so every assertion is a comparison rather than a judgement. An
advertising-audit domain was considered and rejected for exactly this reason: "is this
finding correct?" is a matter of opinion, and an evaluation built on opinions measures the
author, not the model.

The second is that Jira and Confluence appear in the job description this was built for,
so the demo lands on known ground rather than requiring a translation step.

**Rejected.** Ad-campaign audit with scored findings (subjective ground truth); bank
transaction classification (the author has no access to realistic schemas, so the demo
would have been a guess dressed as domain knowledge).

---

## 2. Native structured outputs, not a forced tool call

**Decision.** The final answer is generated under `outputConfig.textFormat` with a JSON
Schema. Tool use is used for the intermediate work only.

**Why.** Bedrock now supports schema-constrained decoding natively. The widely-copied
older recipe — declare a tool whose `inputSchema` is your contract and force it — is a
workaround where a first-class feature exists. It also has a trap: `toolChoice: "tool"`
is documented as supported only on Claude 3 and Nova, so "force a specific tool" does not
generalise to the current Claude models, while `toolChoice: "any"` does.

The schema is generated from the same Pydantic model that validates the result, so the
contract has one definition rather than two that drift.

**Rejected.** Forced tool call (outdated, and the forcing mechanism does not generalise);
regex-parsing free text (the failure mode this whole decision exists to avoid).

---

## 3. The schema restrictions are asserted at import

**Decision.** `assert_bedrock_compatible()` walks the generated schema and raises on
constructs Bedrock does not support.

**Why.** Bedrock accepts a JSON Schema Draft 2020-12 **subset**. Unsupported constructs are
rejected with HTTP 400 at request time — i.e. in the middle of a twenty-case run, after
the first cases have already been paid for. The restrictions are documented, so they can be
checked in a millisecond at import instead.

The specific trap worth knowing: `additionalProperties` must be `false` or absent, not
`true`; numeric and string-length constraints are unsupported, so a perfectly ordinary
`minLength` will fail the request; `enum` values must be scalars; `minItems` may only be
0 or 1; `$ref` must be internal.

**Rejected.** Discovering this from a 400 during the first live run, which is the default
outcome if you do nothing.

---

## 4. Validity and correctness are scored on separate axes

**Decision.** The gate reports schema validity, field correctness, and quote grounding as
three numbers. It never blends them into one.

**Why.** A blended score lets a well-formed but wrong answer hide behind a well-formed
answer — which is exactly how an AI demo misleads its own author. Splitting them also makes
the failure legible: if validity is 100% and correctness is 60%, the problem is the prompt
or the model's reasoning. If validity is 60%, the problem is the contract or the decoder.

Quote grounding is a third axis because it tests something the other two cannot: whether
the stated justification exists in the source. A hallucinated quote behind a correct task
title is still a hallucination, and it is the kind that survives review because the answer
looks right.

**Rejected.** A single "accuracy" number (unscientific, hides the failure mode); exact
string matching on titles (two competent writers phrase one action differently, so this
measures phrasing, not extraction).

---

## 5. The cost axis is routing, and prompt caching was deliberately dropped

**Decision.** The experiment compares three arms: always-cheap, always-strong, and routed
(cheap first, escalate to strong only on deterministic validation failure). Prompt caching
is not in the experiment at all.

**Why caching was dropped — this is the most important rejected alternative in the
repository.** The plan was originally a model-tier × prompt-caching matrix. The documented
facts killed it:

* Claude Haiku 4.5's minimum cacheable prefix is **4,096 tokens**. Sonnet 4.5's is 1,024.
* Below that minimum the request **succeeds, bills normally, caches nothing, and raises no
  error** — AWS's own sample material says it "fails silently".
* The shared prefix here is a system prompt plus tool definitions: a few hundred tokens,
  roughly an order of magnitude short of Haiku's floor.
* Sonnet's 1,024 floor might be cleared. That is the worst possible outcome, because the
  resulting "difference between models" would actually be the difference between whether
  caching engaged at all.

Two further confounders made it worse rather than better: the cache is per-Region while the
`eu.` cross-region profile fans requests across several EU regions, and prompt caching is
invalidated by a changed output schema (documented by Anthropic and reproduced on Bedrock).
A null or misleading result from twenty short cases was the likely outcome, and a misleading
result is worse than no result.

Routing was chosen instead because the output is a **policy** rather than a table, it rests
on the deterministic signal already defined in decision 6, and rule-based routing is
documented by AWS as delivering substantial savings. The whole caching analysis is retained
in this document and in the README's limitations section, because knowing why a lever was
rejected is worth more than not having considered it.

**Rejected.** Caching A/B (fragile, and the fragility is provable in advance); a padded
prefix to clear the 4,096 floor (the finding would then be about the padding); context
trimming (a reasonable third axis, listed under next steps rather than built).

---

## 6. Escalation is triggered only by a deterministic signal

**Decision.** The strong model is paid for when, and only when, the cheap attempt fails
schema validation or produced malformed tool arguments.

**Why.** A routing rule that cannot be reproduced is not a routing rule; it is a mood. Model
self-reported confidence is a plausible-looking trigger that fails this test — it is not
stable across runs and cannot be regression-tested, which makes the resulting cost figures
unfalsifiable.

Guardrail interventions are reported as their own metric rather than folded into the
trigger, because they are a different kind of event: "the answer was wrong" and "the answer
was blocked" have different owners and different fixes. Mixing them would also make the
extraction quality score depend on the safety policy, which is precisely the entanglement
that makes AI systems hard to debug.

**Rejected.** Confidence-threshold escalation; escalating on any tool error, including
benign ones such as "no such case" (which the model can and should recover from on its own).

---

## 7. The guardrail masks identifiers, not names

**Decision.** The input guardrail anonymises phone numbers and email addresses and blocks
card numbers. Personal names are **not** masked. A denied topic is configured as well.

**Why not names.** The extraction produces task owners, and owners appear in the transcript
by name. Masking names would make the artifact unscoreable and would demonstrate nothing
except that masking works. The masking policy therefore targets *identifiers* — things that
are unambiguously sensitive and irrelevant to the task — and leaves names, which are the
task's actual payload.

This is a deliberate scoping decision rather than an omission, and it is the kind of thing
that should be written down and owned rather than inherited from a product default. If the
requirement were different — a support transcript reaching a third-party model, say — the
right answer would change, and this document is where that change would be recorded.

**Why the leak count is still meaningful.** Because masking the input should make leakage
impossible, measuring it is not a formality: a non-zero count means a mask failed, a tool
result bypassed the guardrail, or the model reconstructed the identifier. The eval reports
leaks next to field correctness so that the accuracy cost of masking is visible if it
exists, rather than assumed away.

**Rejected.** Masking everything including names (breaks the artifact, proves nothing);
guardrail on the output only (the identifiers would already have reached the model);
no guardrail (leaves the required qualification, and a real risk, unaddressed).

---

## 8. Data residency was traded against feature availability, knowingly

**Decision.** Use `bedrock-runtime` with `eu.` cross-region inference profiles, accepting
cross-region routing in exchange for native structured outputs.

**Why this is a real fork.** For Haiku 4.5 and Sonnet 4.5 in `eu-north-1`, in-region
on-demand is not offered on `bedrock-runtime`: a geo or global inference profile ID is
required. Stockholm *does* offer these models in-region via `bedrock-mantle` — but the
Anthropic Messages API on that endpoint does not support native structured outputs. So:

**you can have data staying in the region, or a schema-constrained decoder, but not both.**

For a bank this is not a footnote. The `eu.` profile keeps requests inside EU regions and is
priced at source-region rates with no cross-region surcharge, but the documentation warns
that geo requests may still be routed to opt-in regions and stored there for abuse
detection. That warning is reproduced in the README rather than omitted, because a reviewer
who finds it later will reasonably ask what else was left out.

**Rejected.** In-region via `bedrock-mantle` with client-side validation (strongest residency
story, but the demo would showcase a workaround instead of the current API); not mentioning
residency at all (an evasion that would be found in the first interview).

---

## 9. Prices ship empty, and the code refuses to guess

**Decision.** `src/pricing.py` has `None` for every rate and an empty `PRICES_AS_OF`.
`assert_prices_loaded()` raises before any run that would print currency.

**Why.** Bedrock's pricing tables are client-rendered, and Claude models on Bedrock are
billed through AWS Marketplace under the model provider rather than under the
`AmazonBedrock` offer code, so the rates could not be retrieved programmatically. They have
to be read in a browser.

The important part is what happens next. A cost table built on remembered numbers is the
single claim in this repository that anyone can falsify in thirty seconds, and one wrong
number discredits the correct ones beside it. So the default is failure with instructions,
not a plausible-looking placeholder.

Related: Bedrock's cache multipliers are **not** uniform across model families — the Nova
family is documented at 0.25× read with free writes, while Anthropic's reference table uses
1.25× write / 0.1× read. Generalising a multiplier is a mistake with a citation attached.

**Rejected.** Pre-filling Anthropic's published rates (they are the provider's rates, not
Bedrock's); an "approximate" label (an approximate number still invites the question the
table cannot answer).

---

## 10. An offline stub exists, and it is fenced off from the money

**Decision.** `OfflineConverseClient` lets the entire pipeline run with no AWS account. It
fabricates token counts. Therefore `run_evals --offline` suppresses currency entirely and
`compare.py` refuses to run offline.

**Why.** The plumbing — tool loop, tool-result continuation, schema validation, escalation,
scoring, reporting — is where the bugs are, and finding them should not require credentials
or cost anything. But a stub's token counts are invented, so any cost derived from them
would be fiction wearing a decimal point. The fence is one line of code and it protects the
one number that must not be fudged.

The stub also simulates two things it cannot measure, and says so: it injects the schema
failure on the cheap model only (so the escalation path is exercised to a *successful*
recovery rather than to a double failure), and it redacts the planted identifiers out of the
quote when a guardrail is configured, mimicking an input mask. Offline leak numbers
therefore demonstrate the mechanism and are not evidence about Bedrock Guardrails.

---

## 11. The MCP surface is read-only apart from one tool

**Decision.** Five MCP tools: `extract_actions` acts, and `list_evaluation_cases`,
`get_evaluation_case`, `output_contract` and `evaluation_summary` only read. No tool writes
anywhere. A test asserts that no tool name contains a mutating verb.

**Why.** An agent-facing API that can mutate external state is a much larger security
conversation than this demo can honestly have — it needs authorisation per caller, an audit
trail, a rollback story and an owner. Shipping the read-and-extract surface and *saying* the
write surface needs that work is a stronger position than shipping a `create_jira_issue`
tool and hoping nobody asks who else can call it.

The same reasoning already applies inside the repository: no tool in `src/tools.py` writes
either.

**Rejected.** Mirroring the internal tools as MCP tools for symmetry (they are means to an
end, not capabilities a client should call directly); a `run_evaluation` MCP tool (a
twenty-case live run is a spending action; it belongs on the command line where a human
triggers it deliberately).

## 12. HTTP fails closed; stdio needs no token

**Decision.** Starting the streamable-HTTP transport without `MCP_BEARER_TOKEN` is a startup
error. stdio requires no token.

**Why.** The transports have different threat models and should not share a default. stdio is
a pipe owned by the parent process: nothing else can connect to it, so a token would be
ceremony. An HTTP listener is reachable by anything that can route to the port, and "a demo
MCP server left running on a laptop" is common enough to be worth one explicit branch.

The token is read from the environment rather than from a command-line flag, because
arguments are visible in `ps` and retained in shell history. The verifier uses
`secrets.compare_digest` rather than `==`, because string comparison short-circuits on the
first differing byte and leaks the token's prefix through timing — irrelevant for a demo
token, and the habit is the point.

**Rejected.** A warning instead of a refusal (warnings are read after the fact, if at all);
a token on the command line (visible to every user on the box); no auth on HTTP because it
is "only localhost" (the port is reachable by every process and every container on the
host, and by anything forwarded to it).

## 13. Every response carries its provenance

**Decision.** Every `extract_actions` result includes `mode`. Stub answers additionally carry
a `warning` and report `cost_usd: null`.

**Why.** The server can answer from the offline stub when no AWS credentials are present,
which is what makes it demonstrable in a meeting without an account. That convenience is only
safe because the difference is *in the payload*: a caller — human or agent — that cannot tell
a simulated answer from a measured one will eventually quote the wrong one, and this project's
entire position rests on not doing that.

This is the same rule as decision 9, applied one layer up: the failure mode is identical, and
so is the fix.

**Rejected.** Refusing to serve at all without credentials (loses the demo value); a silent
fallback with a log line (logs are not read by the agent that consumed the answer).

---

## 14. Model capabilities are probed, never assumed

**Decision.** `scripts/probe_structured_outputs.py` asks each candidate model three questions with
one tiny call each — can it be invoked, does `outputConfig.textFormat` work, does `toolConfig`
work — and writes the answers to `evals/model_capabilities.json`. `src/agent.py` reads that file
and, for a model probed as unsupported, omits `outputConfig`, states the contract in the prompt
instead, and records `structured_output: "prompt-only"` on the run.

**Why.** Native structured outputs are **not** a Bedrock-wide feature; they are per model, and the
documentation says so only on individual model cards. The repository's central claim is that the
answer is *schema-constrained rather than parsed*, and that claim is only true for models that
support it. So the question has to be answered by measurement, once, and then recorded.

The important detail is that there are **three** states, not two:

| State | Meaning | What the agent does |
|---|---|---|
| `True` | probed and supported | send `outputConfig` |
| `False` | probed and rejected | omit it, state the contract in the prompt |
| `None` | never probed, or probing was blocked | send it, and record that it was unverified |

Collapsing `None` into either of the others produces a specific, quiet failure. Treat it as `True`
and every case fails on a model that cannot accept the parameter; treat it as `False` and the
repository silently stops enforcing schemas on a model that supports them. Both outcomes look like
a *model* result — a low pass rate, or a suspiciously good one — while actually being a harness
bug. This is the same reasoning as decision 4, one layer down.

Probing also cannot lie about *why* it failed: the classifier distinguishes throttle, missing
inference profile, missing IAM action, ungranted model access, and genuine unsupported-feature, so
`evals/model_capabilities.json` doubles as the account-state record.

**Rejected.** Assuming support (fails loudly on the wrong models); assuming the absence of support
(silently weakens the claim); a single boolean instead of three states (the failure described
above); probing at request time (twenty cases would pay for twenty capability probes).

---

## 15. The guardrail is verified without the model

**Decision.** `scripts/verify_guardrail.py` calls `ApplyGuardrail` directly, over the identifiers
actually planted in the fixtures, and asserts that PHONE and EMAIL are anonymised, the card number
is blocked, the denied topic fires, and clean text passes through untouched.

**Why.** The evaluation applies the guardrail through `guardrailConfig` on `Converse`, so the only
way to test it would normally be a live model call. That couples two things that should be
independent: "is the guardrail configured correctly" and "does the model work". An account that
refuses inference then cannot verify its own safety policy at all — which is precisely the
situation this project spent an evening in.

`ApplyGuardrail` breaks the coupling: it runs the policy over text directly. So the guardrail can
be verified on an account where no model call succeeds.

There is a second reason, and it is about what may be claimed. *"The guardrail is configured"* and
*"the guardrail masks what it claims to mask"* are different statements, and only the second one
belongs in a CV. This script produces the second one, with the observed action per entity type as
evidence.

The permission is `bedrock:ApplyGuardrail`, which the IAM policy grants. Note that the evaluation
path does not need it — that path goes through `Converse` — so it is granted for verification
only, and the policy says so.

**Rejected.** Verifying the guardrail only through the evaluation (couples safety verification to
inference availability, and to twenty cases' worth of model behaviour); trusting the configuration
because it was accepted by the API (that proves the request was well-formed, not that the policy
does anything).

---

## What was deliberately not built

| Not built | Why |
|---|---|
| Any write tool, in MCP or internally | See decision 11. The agent proposes; a human disposes. |
| IaC (Terraform/CDK) | Noise for a one-day artifact. The guardrail script and a documented IAM policy carry the infrastructure intent. |
| CI | Without a stable live account, a CI job would either be skipped or spend money unpredictably. The gate is `--gate`, run deliberately. |
| Prompt caching | See decision 5. |
| A managed Bedrock Agent resource | The client-side Converse loop keeps the loop, the escalation rule and the trace visible and testable. `README.md` lists the managed alternative as a next step rather than dismissing it. |

## Numbers this repository will not claim

- No cost figure is ever printed without a price read from the AWS page and a date.
- `$ / 1k cases` is labelled an extrapolation everywhere it appears.
- Offline results are labelled as a plumbing test, never as model performance.
- No claim is made about Bedrock Guardrails from a simulated run.
