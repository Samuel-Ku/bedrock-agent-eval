# Agent Toolkit for AWS — setup record

Setup followed from AWS's own instructions:
<https://raw.githubusercontent.com/aws/agent-toolkit-for-aws/refs/heads/main/setup-instructions/setup.md>

This file records what was done, **where we deliberately deviated from those instructions and
why**, and the exact remaining commands. The deviations matter more than the steps: an
instruction file is written for the general case, and four of its steps do not fit this machine.

Environment at the time of setup: Linux x86_64 · AWS CLI **2.36.49** (installed from
`awscli.amazonaws.com`, GPG signature verified) · `uv` present · `~/.local/bin` already on `PATH`.

---

## Done

| Setup step | State |
|---|---|
| 1 — Determine OS | done: Linux x86_64 |
| 2 — Install the AWS CLI | done: 2.36.49, signature verified, `~/.local/bin/aws` |
| 3 — Log in | **not applicable** — see deviation 1 |
| 4 — Verify access | pending: needs the AWS account |
| 4 — Verify access | done: the scoped IAM user `bedrock-agent-eval` |
| 5 — Configure the toolkit | **done**: 23 skills installed, AWS MCP server configured for four agents |
| 6 — Verify the toolkit | **done**: remote catalogue reachable, 105 skills available |
| 7 — Rules file | done: `AGENTS.md` in this repository — see deviation 4 |

## Outcome

`aws configure agent-toolkit --yes --region us-east-1 --profile default` completed cleanly.

**Skills installed** (23, to `~/.claude/skills`, `~/.agents/skills/`, `~/.pi/agent/skills`):
`amazon-bedrock`, `aws-ai-ml`, `aws-auth`, `aws-billing-and-cost-management`, `aws-blocks`,
`aws-cdk`, `aws-cloudformation`, `aws-compute`, `aws-containers`, `aws-database`,
`aws-deployment`, `aws-iam`, `aws-messaging-and-streaming`, `aws-networking`,
`aws-observability`, `aws-sdk-js-v3-usage`, `aws-sdk-python-usage`, `aws-sdk-swift-usage`,
`aws-security`, `aws-serverless`, `aws-storage`, `launch-with-aws`, `signing-in-to-aws`.
The remote catalogue reports **105** skills available in total.

**AWS MCP server configured** for Claude Code, Codex, Gemini CLI and OpenCode. The generated
entry is a stdio proxy:

```json
"aws-mcp": {
  "command": "uvx",
  "args": ["mcp-proxy-for-aws@latest", "https://aws-mcp.us-east-1.api.aws/mcp",
           "--metadata", "INSTALL_SOURCE=aws-cli"]
}
```

**Configuration files, verified after the run** — no existing server entry was lost:

| File | Key | Servers before → after | `aws-mcp` |
|---|---|---|---|
| `~/.claude.json` | `mcpServers` | 1 → 2 | added |
| `~/.config/opencode/opencode.json` | `mcp` | 3 → 4 | added |
| `~/.gemini/settings.json` | `mcpServers` | 2 → 3 | added |
| `~/.agents/mcp.json` | `mcpServers` | 6 → 6 | untouched |
| `~/.config/opencode/mcp.json` | `mcpServers` | 1 → 1 | untouched |
| `~/.claude/mcp-configs/mcp-servers.json` | `mcpServers` | 32 → 32 | untouched |

### The MCP server was started, not merely configured

Step 6 of the official instructions verifies the **skill catalogue**
(`aws agent-toolkit list-available-skills`) and never checks that the MCP server it just wrote into
four configuration files can actually run. So that check was done here instead: the configured
command was launched over stdio with a real MCP client, and it connected.

```
$ uv run python -c "...Client(StdioTransport(command='uvx', args=['mcp-proxy-for-aws@latest', ...]))"
CONNECTED — 8 tools:
   aws___get_presigned_url      aws___list_regions
   aws___get_tasks              aws___read_documentation
   aws___run_script             aws___retrieve_skill
   aws___get_regional_availability   aws___search_documentation
```

Two of them were then called, to prove the connection is functional rather than merely open:

- `aws___list_regions` → the region list came back;
- `aws___search_documentation` with *"Bedrock structured outputs"* → the top hit is AWS's own post
  *"Structured outputs on Amazon Bedrock: Schema-compliant AI responses"*, which confirms the
  `outputConfig` + JSON Schema mechanism this repository is built on.

**This is worth knowing for a reason beyond verification:** `aws___search_documentation` and
`aws___read_documentation` give an authoritative AWS documentation search that does not depend on
a general web search. In this session the general web-search tool was returning HTTP 401 the whole
time, and the AWS MCP server was the only way to check AWS behaviour against AWS's own words.

The proxy needs no permissions beyond the scoped user's: `aws___run_script` executes in AWS's own
sandbox, so the tool surface works without widening the least-privilege policy.

### Two things the first run taught us

**The backup list was incomplete, and that is now fixed.** The first version of
`setup_agent_toolkit.sh` backed up only the four Claude/Codex files. The toolkit also
rewrote `~/.config/opencode/opencode.json` and `~/.gemini/settings.json` — the latter is where
OpenCode's *real* MCP configuration lives, not the `mcp.json` alongside it. Both are now in the
script's `CONFIGS` array. Nothing was actually lost (the untouched catalog file is
byte-identical across both backups, and every pre-existing server is still present), but the
gap existed and is recorded here rather than quietly patched.

**`~/.claude.json` shows a 17-line diff that is not 17 semantic changes.** Nine lines are the
new `aws-mcp` entry. The rest are two of Claude Code's own internal prompt strings re-encoded
with `\u2014` escapes instead of literal em dashes — semantically identical JSON, produced by
whatever serialiser the toolkit used. If that cosmetic churn is unwanted:

```bash
cp -p ~/.mcp-config-backups/20260920-183741/.claude.json ~/.claude.json
```

...but note that this also removes the `aws-mcp` entry, so re-run with `--inject-env` or add it
back by hand.

**Commands verified to exist** in the installed CLI before relying on them, because the
instructions may describe a different version: `aws login`, `aws agent-toolkit`,
`aws configure agent-toolkit`, and their `--profile` / `--region` flags.

---

## Four deliberate deviations

### 1. `aws login` is skipped; credentials come from the Phase 0 IAM user

The instructions require browser sign-in via `aws login`. We do not use it, for two reasons.
This account does not exist yet, so the flow cannot run — and more importantly, this project
already provisions an IAM user with a least-privilege policy and a **region lock** that denies
every `bedrock:*` action outside the EU region list (`aws/iam-policy.json`). That lock is both a
spend control and a data-residency control, and it is one of the artifacts worth showing.

`aws login` is not a worse mechanism in general — it mints **short-lived credentials with a
refresh token** instead of a long-lived secret on disk, which is better hygiene. What it does
not give you is a policy you can read, publish and reason about. On a shared account, or once
this prototype is done, adding an `aws login` profile alongside the static one is the better
end state. It is recorded here rather than dismissed.

**Consequence:** the setup file's warning about `aws-mcp` failing with
`JSON-RPC error: -32602` applies to *named* profiles. Our profile is `default`, which is exactly
what the generated `aws-mcp` entry falls back to, so the failure mode does not arise. Adding
`"env": {"AWS_MCP_PROXY_PROFILES": "default"}` is still worthwhile — it is the hook for
cross-account switching later — but it is an improvement, not a fix.

### 2. The shell-rc `PATH` edit is skipped

The instructions append `export PATH="$HOME/.local/bin:$PATH"` to `~/.bashrc` or `~/.zshrc`.
Not done: `~/.local/bin` is already on `PATH`, and AWS's own installer does not modify shell rc
files at all (verified by reading the installer before running it). Editing a human's shell
configuration to add a directory that is already there is pure risk with no benefit.

### 3. Step 5 runs without `--yes`

The instructions recommend `aws configure agent-toolkit --yes --region us-east-1 --profile …`.
The CLI's own help says `--yes` means "select **all detected agents**, install default skills,
and configure the AWS MCP server". Four MCP configuration files exist on this machine, and one
of them holds live credentials in plain text. Rewriting all four unattended is not a risk worth
taking to save a keystroke.

So: **verified backups first**, then run it interactively and select only the tools actually in
use.

Backups taken, `cmp`-verified, all four confirmed valid JSON beforehand:

```
~/.mcp-config-backups/20260920-181415/
```

Restore with:

```bash
cp -a ~/.mcp-config-backups/20260920-181415/. "$HOME/"
```

The four files, and what they held at backup time:

| File | Servers before |
|---|---|
| `~/.claude.json` | serena |
| `~/.agents/mcp.json` | confluence, gitlab, jira, onyx-rag, slack, wwt-kb — **holds live tokens** |
| `~/.config/opencode/mcp.json` | hexstrike |
| `~/.claude/mcp-configs/mcp-servers.json` | 33 servers |

### 4. The rules file goes in this repository, not the workspace root

Step 7 appends AWS rules to the project's rules file. There was none, so it would have created
one. The instructions imply the workspace root; we put it at `bedrock-agent-eval/AGENTS.md`
instead, because the rules govern AWS work and this is the AWS project. Creating an instruction
file in the directory that holds the CV and the cover letter would change agent behaviour in a
place with no AWS work in it, for no benefit.

The block is wrapped in `<!-- BEGIN AWS Agent Toolkit rules -->` / `<!-- END … -->` as the
instructions require, so a re-run replaces it rather than appending a duplicate.

**The starter-vs-advanced assumption:** the file fetched is `rules/aws-agent-rules.md`, the
**advanced** experience ruleset, on the assumption that the account will be created with an
email address and a card rather than a social provider. If the account is instead created by
signing in with Google or GitHub and creating a project, the correct file is
`rules/aws-starter-rules.md`, and the marked block should be swapped for it.

---

## Remaining sequence

Steps 3–7 are automated by `scripts/setup_agent_toolkit.sh` (`make agent-toolkit`). It backs the
MCP configuration files up, runs the toolkit, verifies the remote skill catalogue, and then
reports exactly which files changed with a `diff` and a restore command for each.

Check it first without touching anything:

```bash
make agent-toolkit ARGS=--dry-run
```

Then, after the AWS account exists:

```bash
# 1. Phase 0: account, root MFA, IAM user, budget alarm, Bedrock model access, prices
make phase0
make verify-aws ARGS=--invoke

# 2. Agent Toolkit, steps 3-7
make agent-toolkit
```

Useful flags:

| Flag | Effect |
|---|---|
| `--dry-run` | check prerequisites and report, invoke nothing |
| `--interactive` | hand step 5 to you instead of passing `--yes` |
| `--inject-env` | also set `AWS_MCP_PROXY_PROFILES` in each `aws-mcp` entry (optional; see deviation 1) |
| `--profile NAME` | use a profile other than `default` |

Note the region: the Agent Toolkit service is available **only in `us-east-1`**, regardless of
where the account lives. The script hard-codes that and does not substitute `eu-north-1`.

Then check, in order:

1. The step-6 report lists the `aws-mcp` entry among the changes and **no other server entry was
   removed** — the `diff` command it prints is the fastest way to confirm.
2. Restart the AI tool so it picks up the new MCP server.
3. Ask it something that requires AWS, and confirm it answers rather than failing to start.

`.env` is already prepared: `AWS_REGION=eu-north-1`, a generated `MCP_BEARER_TOKEN`, and
`BUDGET_ALERT_EMAIL` pre-filled with the address from the CV. Change that address if the budget
alerts should go elsewhere — it is the one value that matters for noticing overspend.

## If something goes wrong

```bash
# restore every MCP configuration file to its state before the toolkit ran
cp -a ~/.mcp-config-backups/20260920-181415/. "$HOME/"

# remove the AWS rules we added (they are inside the marked block)
# and uninstall the toolkit skills from the agent config directories if needed
```

Per-step troubleshooting is in AWS's own
[setup-troubleshooting.md](https://github.com/aws/agent-toolkit-for-aws/blob/main/setup-instructions/setup-troubleshooting.md).
