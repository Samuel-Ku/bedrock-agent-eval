"""Smoke tests for the parts that can be tested without an AWS account.

Run with:

    make test          # uv run python -m unittest discover -s tests -v

What these tests are for: the deterministic machinery — the schema guard, the scoring
helpers, the cost arithmetic, the escalation path, the guardrail simulation, and the MCP
surface. What they cannot test: whether a Bedrock model behaves as expected. That is what
the live evaluation run is for, and no unit test can substitute for it.

Deliberately dependency-light: `unittest` from the standard library, so the tests run from
a bare install. The two MCP tests skip themselves if the optional `mcp` extra is absent.
"""

from __future__ import annotations

import asyncio
import copy
import os
import unittest
from unittest import mock

from evals.run_evals import pii_leaks, quote_grounded, score_case, title_recall
from src import config, model_capabilities
from src.agent import run_case, run_case_routed
from src.bedrock_client import OfflineConverseClient
from src.cases import all_cases, get_case
from src.cost import Usage, usage_from_response
from src.mcp_server import contract, extract, list_cases, resolve_mode, serve
from src.schema import assert_bedrock_compatible, output_schema

try:
    import fastmcp  # noqa: F401

    HAVE_FASTMCP = True
except ImportError:  # pragma: no cover - depends on the optional extra
    HAVE_FASTMCP = False


class TestSchemaGuard(unittest.TestCase):
    def test_generated_schema_is_bedrock_compatible(self) -> None:
        problems = assert_bedrock_compatible(output_schema())
        self.assertEqual(problems, [], f"schema would be rejected: {problems}")

    def test_guard_catches_each_documented_violation(self) -> None:
        bad = copy.deepcopy(output_schema())
        bad["properties"]["summary"]["minLength"] = 5
        bad["properties"]["summary"]["additionalProperties"] = True
        bad["$defs"]["Task"]["properties"]["priority"]["enum"] = [{"x": 1}]
        bad["properties"]["tasks"]["$ref"] = "https://example.com/schema.json"
        problems = " ".join(assert_bedrock_compatible(bad))
        self.assertIn("minLength", problems)
        self.assertIn("additionalProperties", problems)
        self.assertIn("scalars", problems)
        self.assertIn("external $ref", problems)


class TestScoringHelpers(unittest.TestCase):
    def test_title_recall_accepts_a_paraphrase(self) -> None:
        recall = title_recall(
            "Add retry with exponential backoff to card authorisation",
            "Add exponential backoff retry to the card authorisation path",
        )
        self.assertGreaterEqual(recall, 0.6)

    def test_title_recall_rejects_an_unrelated_task(self) -> None:
        recall = title_recall(
            "Rotate the signing keys",
            "Rewrite the onboarding copy for the address screen",
        )
        self.assertLess(recall, 0.6)

    def test_quote_grounded_accepts_a_verbatim_fragment(self) -> None:
        transcript = "Marek: the card authorisation retry is dropping attempts under load"
        self.assertTrue(quote_grounded("the card authorisation retry is dropping", transcript))

    def test_quote_grounded_rejects_an_invented_fragment(self) -> None:
        transcript = "Marek: the reconciliation dashboard needs a new column"
        self.assertFalse(
            quote_grounded("the payout scheduler must move to a queue", transcript)
        )

    def test_pii_leak_detects_a_reformatted_number(self) -> None:
        # The leak check must not be defeated by punctuation alone.
        leaks = pii_leaks("card 4111-1111-1111-1111 on file", ["4111 1111 1111 1111"])
        self.assertEqual(len(leaks), 1)

    def test_pii_leak_clean_when_absent(self) -> None:
        self.assertEqual(pii_leaks("no identifiers here", ["+48 601 234 567"]), [])


class TestCostArithmetic(unittest.TestCase):
    def test_billable_input_includes_cache_tokens(self) -> None:
        # The documented trap: usage.inputTokens excludes cached tokens, so costing from
        # it alone understates the request.
        response = {
            "usage": {
                "inputTokens": 100,
                "outputTokens": 50,
                "cacheReadInputTokens": 900,
                "cacheWriteInputTokens": 200,
            },
            "metrics": {"latencyMs": 123},
        }
        usage = usage_from_response(response)
        self.assertEqual(usage.billable_input_tokens, 1200)
        self.assertEqual(usage.total_tokens, 1250)
        self.assertEqual(usage.latency_ms, 123)

    def test_absent_cache_fields_are_treated_as_zero(self) -> None:
        usage = usage_from_response({"usage": {"inputTokens": 10, "outputTokens": 5}})
        self.assertEqual(usage.cache_read_tokens, 0)
        self.assertEqual(usage.billable_input_tokens, 10)

    def test_usage_addition(self) -> None:
        total = Usage(input_tokens=1, output_tokens=2) + Usage(
            input_tokens=10, output_tokens=20, cache_read_tokens=5
        )
        self.assertEqual(total.input_tokens, 11)
        self.assertEqual(total.output_tokens, 22)
        self.assertEqual(total.billable_input_tokens, 16)


class TestOfflineAgent(unittest.TestCase):
    """The stub is a perfect oracle, so these test the harness, not the models."""

    def setUp(self) -> None:
        self.client = OfflineConverseClient()

    def test_a_clean_case_succeeds_without_escalating(self) -> None:
        run = run_case_routed(self.client, get_case("case-01"))
        self.assertTrue(run.valid, run.parse_errors)
        self.assertFalse(
            any(e["stage"] == "strong_after_escalation" for e in run.escalations)
        )
        self.assertEqual(len(run.payload.tasks), 2)

    def test_an_injected_failure_escalates_and_recovers(self) -> None:
        # case-05 is one of the cases where the stub makes the cheap model fail, so this
        # exercises the whole routing path rather than just reporting a failure.
        run = run_case_routed(self.client, get_case("case-05"))
        self.assertTrue(run.valid, run.parse_errors)
        self.assertTrue(
            any(e["stage"] == "strong_after_escalation" for e in run.escalations)
        )
        self.assertEqual(run.model_id, config.STRONG_MODEL_ID)

    def test_the_routed_arm_pays_for_both_attempts(self) -> None:
        run = run_case_routed(self.client, get_case("case-05"))
        models = {model_id for model_id, _ in run.calls}
        self.assertEqual(models, {config.CHEAP_MODEL_ID, config.STRONG_MODEL_ID})

    def test_tool_loop_is_exercised(self) -> None:
        run = run_case(self.client, get_case("case-03"), config.CHEAP_MODEL_ID)
        self.assertGreaterEqual(run.tool_calls, 1)
        self.assertGreaterEqual(run.iterations, 2)

    def test_guardrail_simulation_removes_the_leaks(self) -> None:
        case = get_case("case-04")
        self.assertTrue(case["pii"], "case-04 should carry planted identifiers")
        without = score_case(case, run_case(self.client, case, config.CHEAP_MODEL_ID))
        self.assertGreater(len(without["pii_leaked"]), 0)

        with_guard = score_case(
            case,
            run_case(
                self.client,
                case,
                config.CHEAP_MODEL_ID,
                guardrail={"guardrailIdentifier": "test", "guardrailVersion": "DRAFT"},
            ),
        )
        self.assertEqual(with_guard["pii_leaked"], [])

    def test_every_fixture_reaches_a_verdict(self) -> None:
        # The whole suite must complete; a crash in scoring would be worse than a failed
        # assertion, because it would hide the failure.
        for case in all_cases():
            with self.subTest(case=case["id"]):
                row = score_case(case, run_case_routed(self.client, case))
                self.assertIn("fields_correct", row)
                self.assertIsInstance(row["quotes_grounded"], int)


class TestModelCapabilities(unittest.TestCase):
    """`None` and `False` must never collapse: one means 'do not send it', the other
    'we do not know'. Conflating them either breaks every case or silently drops schema
    enforcement, and both look like a model result when they are a harness bug."""

    def test_unknown_model_is_none_not_false(self) -> None:
        self.assertIsNone(model_capabilities.native_structured_outputs("no.such.model"))
        self.assertIsNone(model_capabilities.tool_use("no.such.model"))
        self.assertEqual(model_capabilities.describe("no.such.model"), "not probed")

    def test_probe_results_load_when_present(self) -> None:
        data = model_capabilities.load()
        self.assertIn("models", data)
        self.assertIn("probed_at", data)

    def test_agent_degrades_to_prompt_only_when_the_probe_says_unsupported(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(
                {
                    "models": {
                        "stub-model": {
                            "native_structured_outputs": {"ok": False},
                            "tool_use": {"ok": False},
                        }
                    }
                },
                handle,
            )
            path = Path(handle.name)

        with mock.patch.object(model_capabilities, "CAPABILITIES_PATH", path):
            self.assertFalse(model_capabilities.native_structured_outputs("stub-model"))
            run = run_case(OfflineConverseClient(), get_case("case-01"), "stub-model")
        self.assertEqual(run.structured_output, "prompt-only")
        # The contract is still validated, so the run is still judged the same way.
        self.assertTrue(run.valid, run.parse_errors)

    def test_agent_keeps_native_mode_when_the_probe_is_silent(self) -> None:
        run = run_case(OfflineConverseClient(), get_case("case-01"), config.CHEAP_MODEL_ID)
        self.assertEqual(run.structured_output, "native")


class TestGuardrailConfig(unittest.TestCase):
    """Two names in this script were wrong in its first version and only running it revealed
    them: the API calls the topic policy `topicPolicyConfig`, and the card entity is
    `CREDIT_DEBIT_CARD_NUMBER`. Guard the API's vocabulary, not the console's."""

    @staticmethod
    def _module():
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "scripts" / "create_guardrail.py"
        spec = importlib.util.spec_from_file_location("create_guardrail", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, path.read_text(encoding="utf-8")

    def test_uses_api_parameter_names(self) -> None:
        _, source = self._module()
        # Assert on the keyword argument, not on the raw text: the comment deliberately names
        # the rejected spelling so the next reader does not try it again.
        self.assertIn("topicPolicyConfig=", source)
        self.assertNotIn("deniedTopicsPolicyConfig=", source)

    def test_pii_entity_types_are_valid_and_names_are_deliberately_absent(self) -> None:
        module, _ = self._module()
        types = {entry["type"] for entry in module.PII_ENTITIES}
        self.assertIn("PHONE", types)
        self.assertIn("EMAIL", types)
        self.assertIn("CREDIT_DEBIT_CARD_NUMBER", types)
        self.assertNotIn("CREDIT_DEBIT_NUMBER", types)
        # Masking names would remove the task owners and make the artifact unscoreable.
        self.assertNotIn("NAME", types)


class TestPriceDimensionPicker(unittest.TestCase):
    """A rate card lists many dimensions per model. Picking the wrong one produces a
    plausible-looking cost table that is simply wrong, so the choice is tested."""

    @staticmethod
    def _module():
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "scripts" / "fetch_prices_from_offers.py"
        spec = importlib.util.spec_from_file_location("fetch_prices_from_offers", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_prefers_cross_region_and_excludes_batch_and_non_standard_tiers(self) -> None:
        fp = self._module()
        card = [
            {"dimension": "X_input_tokens_global_batch", "price": "0.5"},
            {"dimension": "X_InputTokenCount", "price": "3.0"},          # in-region
            {"dimension": "X_InputTokenCount_Global", "price": "1.5"},   # cross-region
            {"dimension": "X_output_tokens_global", "price": "5.0"},
            {"dimension": "X_output_tokens_priority", "price": "9.0"},
        ]
        chosen = fp.pick(card)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["on_demand_input"]["price"], "1.5")
        self.assertEqual(chosen["on_demand_output"]["price"], "5.0")
        for entry in chosen.values():
            self.assertNotIn("batch", entry["dimension"].lower())
            self.assertNotIn("priority", entry["dimension"].lower())

    def test_refuses_an_incomplete_pair_rather_than_guessing(self) -> None:
        fp = self._module()
        self.assertIsNone(fp.pick([{"dimension": "X_input_tokens_batch", "price": "1"}]))


class TestPhase0Assets(unittest.TestCase):
    """The IAM policy and the price plumbing are deliverables, so they are tested."""

    def test_iam_policy_is_valid_and_locks_bedrock_to_the_eu(self) -> None:
        import json
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "aws" / "iam-policy.json"
        policy = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(policy["Version"], "2012-10-17")
        by_sid = {statement["Sid"]: statement for statement in policy["Statement"]}

        lock = by_sid["LockBedrockToEuRegions"]
        self.assertEqual(lock["Effect"], "Deny")
        # A region lock must be written as a condition, not a resource list: resource-based
        # denials break the actions that take no resource (ListFoundationModels and friends).
        self.assertEqual(lock["Resource"], "*")

        allowed = lock["Condition"]["StringNotEquals"]["aws:RequestedRegion"]
        self.assertIsInstance(allowed, list)

        # The lock is EU-wide, not single-region, and that is deliberate: the two Claude 4.5
        # models are not offered in-region on bedrock-runtime, so requests go through the
        # `eu.` cross-region profile, which routes outside Stockholm. A single-region lock
        # denies the very call it is meant to permit. eu-west-3 is asserted specifically
        # because that is the region a live call was observed being routed to.
        self.assertIn("eu-north-1", allowed)
        self.assertIn("eu-west-3", allowed)
        for region in allowed:
            self.assertTrue(
                region.startswith("eu-"),
                f"non-EU region {region!r} in a policy meant to enforce EU residency",
            )

        self.assertIn("bedrock:Converse", by_sid["BedrockInvokeAndConverse"]["Action"])
        # `scripts/verify_guardrail.py` checks the guardrail without invoking a model, which is
        # the only way to verify it while inference is unavailable.
        self.assertIn("bedrock:ApplyGuardrail", by_sid["BedrockInvokeAndConverse"]["Action"])

        # `scripts/fetch_prices_from_offers.py` depends on this read action. Losing it
        # silently downgrades the price step back to a human reading numbers off a web page,
        # which is exactly the manual step the repository is trying to remove.
        read_actions = by_sid["BedrockReadMetadata"]["Action"]
        self.assertIn("bedrock:ListFoundationModelAgreementOffers", read_actions)
        self.assertIn("bedrock:GetFoundationModelAvailability", read_actions)

        # Anthropic models on Bedrock are billed through AWS Marketplace, so the runtime
        # identity needs the subscribe actions or Converse fails with AccessDenied even
        # though the model agreement is accepted. Verified by hitting exactly that error.
        marketplace = by_sid["MarketplaceModelSubscription"]["Action"]
        self.assertIn("aws-marketplace:Subscribe", marketplace)
        self.assertIn("aws-marketplace:ViewSubscriptions", marketplace)
        self.assertNotIn(
            "aws-marketplace:Unsubscribe",
            marketplace,
            "unsubscribing would silently disable model access; keep it a deliberate act",
        )

        self.assertTrue(
            set(by_sid).issubset(
                {
                    "BedrockInvokeAndConverse",
                    "BedrockReadMetadata",
                    "GuardrailLifecycle",
                    "MarketplaceModelSubscription",
                    "BudgetAlarm",
                    "BudgetAlertTopic",
                    "LockBedrockToEuRegions",
                }
            ),
            f"unexpected statement added to the policy: {sorted(by_sid)}",
        )

    def test_env_prices_override_the_in_file_table(self) -> None:
        import importlib

        from src import pricing

        env = {
            "PRICES_AS_OF": "2026-01-02",
            "PRICE_HAIKU_INPUT": "1.00",
            "PRICE_HAIKU_OUTPUT": "5.00",
            "PRICE_SONNET_INPUT": "3.00",
            "PRICE_SONNET_OUTPUT": "15.00",
        }
        try:
            with mock.patch.dict(os.environ, env, clear=False):
                importlib.reload(pricing)
                pricing.assert_prices_loaded(
                    [config.CHEAP_MODEL_ID, config.STRONG_MODEL_ID]
                )
                self.assertEqual(pricing.PRICES_AS_OF, "2026-01-02")
                self.assertEqual(pricing.price_for(config.CHEAP_MODEL_ID).input, 1.0)
                self.assertEqual(pricing.price_for(config.STRONG_MODEL_ID).output, 15.0)
        finally:
            # Restore the pristine module state so other tests see no prices.
            importlib.reload(pricing)

    def test_prices_refuse_to_load_when_nothing_is_set(self) -> None:
        import importlib

        from src import pricing

        original = {
            key: os.environ.pop(key)
            for key in list(os.environ)
            if key.startswith("PRICE") or key == "PRICES_AS_OF"
        }
        try:
            importlib.reload(pricing)
            self.assertEqual(pricing.PRICES_AS_OF, "")
            # The exception class is rebound by reload, so match on the message rather than
            # on the class object captured before the reload.
            try:
                pricing.assert_prices_loaded()
                self.fail("assert_prices_loaded should refuse with no price date")
            except Exception as exc:  # noqa: BLE001 - asserting on the message on purpose
                self.assertIn("No price date is set", str(exc))
        finally:
            os.environ.update(original)
            importlib.reload(pricing)

    def test_a_non_numeric_price_is_rejected_with_a_clear_error(self) -> None:
        import importlib

        from src import pricing

        try:
            with mock.patch.dict(
                os.environ, {"PRICE_HAIKU_INPUT": "one dollar"}, clear=False
            ):
                # Same reload caveat: assert on the message, not the class identity.
                try:
                    importlib.reload(pricing)
                    self.fail("a non-numeric price should have raised")
                except Exception as exc:  # noqa: BLE001
                    self.assertIn("PRICE_HAIKU_INPUT", str(exc))
                    self.assertIn("not a number", str(exc))
        finally:
            os.environ.pop("PRICE_HAIKU_INPUT", None)
            importlib.reload(pricing)


class TestMcpAuth(unittest.TestCase):
    """The token verifier is the only thing standing between a socket and the agent."""

    @unittest.skipUnless(HAVE_FASTMCP, "the optional mcp extra is not installed")
    def test_verifier_accepts_only_the_exact_token(self) -> None:
        from src.mcp_server import build_verifier

        verifier = build_verifier("correct-horse-battery-staple")

        async def check(token: str):
            return await verifier.verify_token(token)

        good = asyncio.run(check("correct-horse-battery-staple"))
        self.assertIsNotNone(good)
        self.assertEqual(good.scopes, ["extract"])

        for bad in ("", "wrong", "correct-horse-battery-stapl", "correct-horse-battery-staple "):
            with self.subTest(token=bad):
                self.assertIsNone(asyncio.run(check(bad)))


class TestMcpSurface(unittest.TestCase):
    def test_resolve_mode(self) -> None:
        self.assertEqual(resolve_mode("offline"), "offline-stub")
        self.assertEqual(resolve_mode("live"), "live-bedrock")
        self.assertIn(resolve_mode("auto"), {"live-bedrock", "offline-stub"})
        with self.assertRaises(ValueError):
            resolve_mode("nonsense")

    def test_contract_reports_bedrock_compatibility(self) -> None:
        info = contract()
        self.assertTrue(info["bedrock_compatible"])
        self.assertEqual(info["bedrock_schema_problems"], [])
        self.assertIn("properties", info["schema"])

    def test_list_cases_covers_every_fixture(self) -> None:
        self.assertEqual(len(list_cases()), len(all_cases()))

    def test_extract_on_an_arbitrary_transcript(self) -> None:
        result = extract(
            "Anna: the export times out, I will fix it today, urgent.\n"
            "Decision: we ship the fix behind a flag.",
            mode="offline",
        )
        self.assertEqual(result["mode"], "offline-stub")
        self.assertIn("warning", result)
        self.assertIsNone(result["cost_usd"])
        self.assertIn("result", result)

    def test_http_transport_refuses_to_start_without_a_token(self) -> None:
        # Fail closed: a listening socket with no auth is an incident, not a preference.
        import contextlib
        import io

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MCP_BEARER_TOKEN", None)
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                code = serve(transport="http", token=None)
        self.assertEqual(code, 2)
        self.assertIn("without a bearer token", captured.getvalue())

    @unittest.skipUnless(HAVE_FASTMCP, "the optional mcp extra is not installed")
    def test_mcp_in_memory_client_lists_and_calls_tools(self) -> None:
        from fastmcp import Client

        from src.mcp_server import SERVER_NAME, build_server

        server = build_server(bearer_token=None)
        self.assertEqual(server.name, SERVER_NAME)

        async def exercise() -> tuple[list[str], dict]:
            async with Client(server) as client:
                tools = await client.list_tools()
                names = sorted(tool.name for tool in tools)
                result = await client.call_tool(
                    "extract_actions",
                    {"transcript": "Anna: I will fix the export timeout, urgent.",
                     "mode": "offline"},
                )
                return names, result

        names, result = asyncio.run(exercise())

        self.assertIn("extract_actions", names)
        self.assertIn("output_contract", names)
        self.assertIn("list_evaluation_cases", names)
        # No tool may write anywhere; the surface is read-only apart from extraction.
        for forbidden in ("write", "create", "delete", "update", "push"):
            self.assertFalse(
                any(forbidden in name for name in names),
                f"unexpected mutating tool in the MCP surface: {names}",
            )

        payload = getattr(result, "structured_content", None) or getattr(
            result, "data", None
        )
        if payload is None:
            import json

            payload = json.loads(result.content[0].text)
        self.assertEqual(payload["mode"], "offline-stub")


if __name__ == "__main__":
    unittest.main(verbosity=2)
