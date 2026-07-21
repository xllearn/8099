from __future__ import annotations

import importlib
import json
import unittest


class LayeredDiagnosticsTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module("app.layered_diagnostics")
        except ModuleNotFoundError:
            self.fail("five-layer diagnostics capability is missing")

    def stage(self, layer: str, code: str, **kwargs):
        module = self.module()
        return module.make_layer_result(layer, status="blocked", code=code, **kwargs)

    def test_each_injected_failure_has_exactly_one_primary_layer(self):
        module = self.module()
        cases = [
            ("attachment_parse", "ATTACHMENT_PARSE_FAILED"),
            ("compact", "COMPACT_PROVENANCE_LOST"),
            ("provider", "TIMEOUT"),
            ("cleanup", "CLEANUP_OUTPUT_EMPTY"),
            ("quality_gate", "UNSUPPORTED_FACT"),
        ]
        for layer, code in cases:
            with self.subTest(layer=layer):
                result = module.build_failure_attribution(
                    run_status="failed",
                    deliverable=False,
                    stages=[self.stage(layer, code)],
                )
                self.assertEqual(layer, result["primary_layer"])
                self.assertEqual(code, result["primary_failure_code"])
                self.assertEqual(1, sum(1 for value in result["layers"].values() if value["status"] == "blocked"))

    def test_first_causal_layer_wins_independent_of_input_order(self):
        module = self.module()
        stages = [
            self.stage("quality_gate", "UNSUPPORTED_FACT"),
            self.stage("provider", "OUTPUT_EMPTY"),
            self.stage("cleanup", "CLEANUP_OUTPUT_EMPTY"),
        ]
        first = module.build_failure_attribution(run_status="needs_manual_review", deliverable=False, stages=stages)
        second = module.build_failure_attribution(run_status="needs_manual_review", deliverable=False, stages=list(reversed(stages)))
        self.assertEqual(first, second)
        self.assertEqual("provider", first["primary_layer"])
        self.assertEqual("OUTPUT_EMPTY", first["primary_failure_code"])
        self.assertEqual(["CLEANUP_OUTPUT_EMPTY", "UNSUPPORTED_FACT"], first["secondary_failure_codes"])

    def test_nonterminal_and_deliverable_runs_clear_stale_primary_failure(self):
        module = self.module()
        existing = {
            "primary_layer": "provider",
            "primary_failure_code": "TIMEOUT",
            "secondary_failure_codes": ["OUTPUT_EMPTY"],
        }
        for status in ("created", "preparing", "running", "generating", "repairing"):
            result = module.build_failure_attribution(
                run_status=status,
                deliverable=False,
                stages=[self.stage("provider", "TIMEOUT")],
                existing=existing,
            )
            self.assertEqual("", result["primary_layer"])
            self.assertEqual("", result["primary_failure_code"])
            self.assertEqual([], result["secondary_failure_codes"])
        delivered = module.build_failure_attribution(
            run_status="finished",
            deliverable=True,
            stages=[self.stage("quality_gate", "UNSUPPORTED_FACT")],
            existing=existing,
        )
        self.assertEqual("", delivered["primary_failure_code"])

    def test_interrupted_is_terminal_and_gets_one_failure(self):
        module = self.module()
        result = module.build_failure_attribution(
            run_status="interrupted",
            deliverable=False,
            stages=[],
        )
        self.assertEqual("quality_gate", result["primary_layer"])
        self.assertEqual("UNCLASSIFIED", result["primary_failure_code"])
        module.validate_failure_attribution(result, run_status="interrupted", deliverable=False)

    def test_terminal_nondeliverable_without_explicit_stage_is_fail_closed(self):
        module = self.module()
        for status in ("finished", "needs_manual_review", "failed"):
            result = module.build_failure_attribution(run_status=status, deliverable=False, stages=[])
            self.assertTrue(result["primary_layer"])
            self.assertTrue(result["primary_failure_code"])
            self.assertEqual(1, len([result["primary_failure_code"]]))

    def test_cleanup_loss_is_not_provider_failure(self):
        module = self.module()
        result = module.build_failure_attribution(
            run_status="needs_manual_review",
            deliverable=False,
            stages=[
                module.make_layer_result(
                    "provider",
                    status="ok",
                    input_value={"request": "safe metadata"},
                    output_value={"report_markdown": "完整正文"},
                ),
                module.make_layer_result(
                    "cleanup",
                    status="blocked",
                    code="CLEANUP_OUTPUT_EMPTY",
                    input_value="完整正文",
                    output_value="",
                ),
            ],
        )
        self.assertEqual("cleanup", result["primary_layer"])
        self.assertEqual("CLEANUP_OUTPUT_EMPTY", result["primary_failure_code"])
        self.assertEqual("ok", result["layers"]["provider"]["status"])

    def test_unknown_layer_code_is_canonical_unclassified(self):
        module = self.module()
        stage = module.make_layer_result("provider", status="blocked", code="random-provider-error")
        self.assertEqual("UNCLASSIFIED", stage["code"])
        result = module.build_failure_attribution(run_status="failed", deliverable=False, stages=[stage])
        self.assertEqual("UNCLASSIFIED", result["primary_failure_code"])

    def test_diagnostics_store_hashes_lengths_and_safe_metrics_not_full_sensitive_text(self):
        module = self.module()
        secret = "provider-secret-body-123456789"
        stage = module.make_layer_result(
            "provider",
            status="blocked",
            code="OUTPUT_SCHEMA_INVALID",
            input_value={"prompt": secret},
            output_value={"response": secret},
            metrics={"attempts": 2, "elapsed_ms": 1200, "response_body": secret, "token": secret},
        )
        serialized = json.dumps(stage, ensure_ascii=False)
        self.assertNotIn(secret, serialized)
        self.assertRegex(stage["input_artifact"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(stage["output_artifact"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(stage["input_artifact"]["chars"], 0)
        self.assertEqual({"attempts": 2, "elapsed_ms": 1200}, stage["metrics"])

    def test_failure_schema_roundtrip_is_stable(self):
        module = self.module()
        result = module.build_failure_attribution(
            run_status="failed",
            deliverable=False,
            stages=[self.stage("compact", "COMPACT_LIMIT_EXCEEDED")],
        )
        normalized = module.validate_failure_attribution(result, run_status="failed", deliverable=False)
        self.assertEqual(result, normalized)
        self.assertEqual("8099.failure-attribution/v1", result["failure_schema_version"])
        self.assertEqual(list(module.DIAGNOSTIC_LAYERS), list(result["layers"]))

    def test_same_layer_uses_fixed_semantic_priority_not_alphabetical_order(self):
        module = self.module()
        stages = [
            self.stage("provider", "OUTPUT_SCHEMA_INVALID"),
            self.stage("provider", "TIMEOUT"),
        ]

        first = module.build_failure_attribution(
            run_status="failed",
            deliverable=False,
            stages=stages,
        )
        second = module.build_failure_attribution(
            run_status="failed",
            deliverable=False,
            stages=list(reversed(stages)),
        )

        self.assertEqual("TIMEOUT", first["primary_failure_code"])
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
