# Agent instructions for this repository

Project-specific instructions live above the marked block below. The block between the
markers is maintained by AWS's Agent Toolkit (`aws configure agent-toolkit`) and its setup
instructions; everything outside it is ours and takes precedence where the two conflict.

Source: <https://github.com/aws/agent-toolkit-for-aws> — `rules/aws-agent-rules.md`, the
"advanced AWS experience" ruleset. Fetched on 2026-09-20.

## Project rules

- This repository authenticates in **`eu-north-1`** and its IAM policy confines every Bedrock
  call to **EU regions**. The confinement is EU-wide rather than single-region on purpose: the
  two Claude 4.5 models are not offered in-region on `bedrock-runtime`, so calls go through the
  `eu.` cross-region profile and are routed across the EU. Do not add a non-EU region to that
  policy, and do not narrow it to one region — a single-region lock blocks the calls it is meant
  to permit.
- **Prices come from the AWS pricing page, read by a human**, and every cost table carries the
  date they were read (`PRICES_AS_OF`). Never invent or infer a rate, and never print a
  currency amount when `assert_prices_loaded()` has not passed.
- **Only claims that are checkable in the code** may appear in the README or in a CV. If a run
  did not produce a number, the document says so rather than approximating.
- The MCP surface is **read-only apart from `extract_actions`**. Do not add a tool that writes
  to any external system; `tests/test_smoke.py` asserts this and will fail.
- The offline stub exists for plumbing tests only. Its token counts are fabricated, so it must
  never produce a cost figure — `run_evals --offline` suppresses currency deliberately.

<!-- BEGIN AWS Agent Toolkit rules -->
# AWS Guidance

- Where these AWS rules conflict with the project's own instructions, the
  project's instructions take precedence.
- Prefer the AWS MCP Server for AWS interactions — it provides sandboxed
  execution, observability, and audit logging. If unavailable, use the
  AWS CLI directly.
- Before starting a task, check whether a relevant AWS skill is available.
  Load the skill with `retrieve_skill` and prefer its guidance over
  general knowledge.
- When uncertain about specific AWS details (API parameters, permissions,
  limits, error codes), verify against documentation rather than guessing.
  State uncertainty explicitly if you cannot confirm.
- When creating infrastructure, prefer infrastructure-as-code (AWS CDK or
  CloudFormation) over direct CLI commands.
- When working with infrastructure, follow AWS Well-Architected Framework
  principles.
- Do not use em dashes in AWS resource names or descriptions. Use
  hyphens instead.

## Secret Safety

- MUST load the `aws-secrets-manager` skill first for any secret,
  credential, API key, token, or password task. MUST NOT call
  `secretsmanager get-secret-value` or `batch-get-secret-value`, and MUST
  NOT hit the Secrets Manager Agent daemon directly. MUST use
  `{{resolve:secretsmanager:secret-id:SecretString:json-key}}` with
  `asm-exec` so the secret resolves at runtime without entering context.
<!-- END AWS Agent Toolkit rules -->
