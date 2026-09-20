# Native Bedrock evaluation job — runbook

The local harness in `evals/run_evals.py` is the primary gate. This runbook adds **one**
native Bedrock evaluation job on top of it, so the repository shows awareness of AWS's own
tooling without paying its setup cost for the primary path.

**Do this last.** It is scheduled after the MVP and after Guardrails for a reason: it needs
two S3 buckets and an IAM role, it is asynchronous, and it produces a judge-model score that
is strictly less exact than the `json.loads` + schema validation you already have. If the
clock runs out, this is the item to drop.

Everything below is a starting point that **must be verified against the current AWS
documentation** before you rely on the exact field names — the API surface for evaluation
jobs moves faster than the runbook in any repository, including this one.

---

## What you get that the local harness does not

- **A second opinion from a judge model** on qualities a deterministic assertion cannot see:
  whether the summary reads as a coherent account of the meeting, whether task descriptions
  are useful, whether tone is professional.
- **Per-category scores** for free, via the `category` field — here set to the project key,
  so you can see whether one project's transcripts are harder than another's.
- **A number produced by AWS tooling**, which is worth having in a conversation about
  building evaluation pipelines on Bedrock.

## What you explicitly do not get

- **Exactness.** A judge model can be wrong about whether an answer is right. Your local
  schema and field assertions cannot be wrong, because they are comparisons.
- **Speed.** Jobs are asynchronous; iterate locally first, then confirm natively.
- **Universality.** Model support for evaluation jobs is not uniform. As of writing, the
  Nova Lite model card omits model evaluation among supported features and the Nova 2 Lite
  card marks it as not supported, even though the judge documentation lists Nova 2 Lite as an
  evaluator model — a documentation inconsistency. Check the model cards for the model you
  intend to use as both generator and evaluator before committing.

---

## Prerequisites

1. **Two S3 buckets** in `eu-north-1`: one for the prompt dataset, one for job output.
   They may be the same bucket under different prefixes.
2. **An IAM role** that Bedrock can assume. Confirm the trust relationship and the required
   permissions against the current evaluation documentation — the service principal is
   `bedrock.amazonaws.com` and the role needs read access to the prompt bucket and write
   access to the output bucket. This is the part most likely to have drifted.
3. **The dataset**, which this repository already produces.

## Step 1 — Export the dataset

```bash
uv run python -m evals.export_native_dataset
# writes evals/native_dataset.jsonl — 20 lines, one per case
```

Each line has:

| Key | Contents |
|---|---|
| `prompt` | the full instruction plus the transcript, as one string |
| `referenceResponse` | the expected answer, in the contract's own JSON shape |
| `category` | the project key, so scores break down per project |

Constraints to respect: JSONL, **maximum 1000 prompts per job** (20 is comfortable), and
`referenceResponse` is required for accuracy-style metrics.

## Step 2 — Upload

```bash
BUCKET=<your-prompt-bucket>
aws s3 cp evals/native_dataset.jsonl "s3://${BUCKET}/datasets/meeting-actions.jsonl" \
  --region eu-north-1
```

Record the exact `s3://` URI of the object — the job references the object, not the bucket.
Verify it landed, and check the file was not transformed in transit.

## Step 3 — Create the job

Two shapes, in increasing order of usefulness:

**a) A judge-based job with built-in metrics.** Cheapest to set up; the judge model scores
each prediction against the reference on metrics such as correctness, completeness,
following instructions, or professional style and tone.

**b) A judge-based job with a custom metric.** Up to **10 custom metrics per job**. A custom
metric is your own judge prompt: a role definition, a task definition of at least fifteen
words, an optional rubric, then the input variables `{{prompt}}`, `{{prediction}}` and
`{{ground_truth}}`. The prompt is capped at 5000 characters, and the optional `ratingScale`
output schema must be all-numeric or all-string, not mixed.

A custom metric worth writing for this domain: *does the reference justification appear in
the prediction's `source_quote`, and is the priority consistent with the urgency language in
the transcript?* That is the judge checking the thing your deterministic harness checks,
which makes the two numbers comparable — and a disagreement between them is the interesting
result, not a problem.

**Use the AWS console for the first attempt.** The console validates your dataset and role
before it spends anything, and it shows the exact configuration the API would have taken.
Transcribe that into a script only if you intend to run it repeatedly.

## Step 4 — Read the results and record them honestly

Write into the README's results section:

- the job type and the metric names used;
- the judge model, and the generator model;
- **the date**, because a score without a date is not comparable to anything;
- the per-category breakdown if you used `category`;
- and one sentence on where the native score disagreed with the local gate.

That last sentence is the most valuable line you can take into an interview, because it is
the one only someone who ran both could write.

## Cost note

Judge-based evaluation bills the evaluator model's tokens for every scored prediction, so a
20-case job with one or two metrics is small but not free. The budget alarm from Phase 0 is
the backstop, not the plan: know roughly what you expect to spend before you create the job,
and check that the number of prompts is what you intended.

## If you run out of time

Skip the job and keep the export. `evals/export_native_dataset.py` running cleanly, next to a
README note that the local harness was chosen deliberately because two S3 buckets and an IAM
role are more infrastructure than twenty cases justify, is a defensible position. An
abandoned half-configured job is not.
