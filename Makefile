.PHONY: help install env check test eval-offline evals compare guardrail cli mcp-stdio mcp-http native-export phase0 bootstrap-aws verify-aws agent-toolkit clean

help:
	@echo "phase0        guided setup: AWS account, IAM user, budget alarm, Bedrock access, prices"
	@echo "live          the whole live evaluation in order, then the README rows it produced"
	@echo "results       print README table rows from the recorded runs (ARGS=--check)"
	@echo "probe         probe each model for invocation, native schema and tool use"
	@echo "prices        record the token rates by hand, with validation"
	@echo "prices-offers record them from the signed agreement instead (needs one policy update)"
	@echo "model-agreement accept the foundation-model agreement (needs an admin identity)"
	@echo "bootstrap-aws create the budget alarm + SNS alert topic (EMAIL=you@example.com)"
	@echo "verify-aws    check credentials, model access, prices, guardrail (ARGS=--invoke)"
	@echo "agent-toolkit Agent Toolkit for AWS, steps 3-7 (ARGS=--dry-run to check without acting)"
	@echo "install       create the venv and install dependencies (uv), including the MCP extra"
	@echo "check         import every module and verify the output contract"
	@echo "test          run the smoke tests (no AWS account needed)"
	@echo "cli           show the command-line interface"
	@echo "eval-offline  run the 20-case suite against the offline stub (no AWS, no cost)"
	@echo "evals         run the 20-case suite against live Bedrock"
	@echo "compare       run the three cost arms and print the results table"
	@echo "guardrail     create the Bedrock guardrail (PII mask on input) and print its id"
	@echo "mcp-stdio     serve MCP over stdio (local only, no token required)"
	@echo "mcp-http      serve MCP over streamable HTTP (requires MCP_BEARER_TOKEN)"
	@echo "native-export export the JSONL dataset for a native Bedrock evaluation job"
	@echo "clean         remove caches and generated results"

phase0:
	bash scripts/phase0-wizard.sh

bootstrap-aws:
	uv run python scripts/bootstrap_aws.py --email "$(EMAIL)"

verify-aws:
	uv run python scripts/verify_aws_setup.py $(ARGS)

agent-toolkit:
	bash scripts/setup_agent_toolkit.sh $(ARGS)

prices:
	uv run python scripts/set_prices.py $(ARGS)

model-agreement:
	uv run python scripts/accept_model_agreement.py --profile "$(or $(PROFILE),bootstrap)" $(ARGS)

live:
	bash scripts/run_live_evaluation.sh $(ARGS)

results:
	uv run python scripts/summarise_results.py $(ARGS)

prices-offers:
	uv run python scripts/fetch_prices_from_offers.py $(ARGS)

probe:
	uv run python scripts/probe_structured_outputs.py $(ARGS)

install:
	uv venv
	uv pip install -e ".[dev,mcp]"

check:
	uv run python -c "import src.config, src.schema, src.tools, src.cost, src.agent, src.pricing, src.cases, src.cli; print('imports ok')"
	uv run bedrock-agent-eval contract

test:
	uv run python -m unittest discover -s tests -v

cli:
	uv run bedrock-agent-eval --help

eval-offline:
	uv run bedrock-agent-eval eval --offline

evals:
	uv run bedrock-agent-eval eval

compare:
	uv run bedrock-agent-eval compare

guardrail:
	uv run python scripts/create_guardrail.py

verify-guardrail:
	uv run python scripts/verify_guardrail.py $(ARGS)

mcp-stdio:
	uv run bedrock-agent-eval serve-mcp --transport stdio

mcp-http:
	uv run bedrock-agent-eval serve-mcp --transport http

native-export:
	uv run python -m evals.export_native_dataset

clean:
	rm -rf .venv .ruff_cache **/__pycache__ evals/results.json experiments/results.json
