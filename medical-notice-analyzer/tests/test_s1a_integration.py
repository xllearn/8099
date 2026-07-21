from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import diagnostics
from app import main as main_module
from app.analysis_history import AnalysisHistoryStore
from app.evidence_schema import create_evidence_item, text_source_hash, validate_evidence_pack
from app.layered_diagnostics import FAILURE_SCHEMA_VERSION, make_layer_result


class S1aIntegrationTests(unittest.TestCase):
    @staticmethod
    def source_ref(text: str = "AUTHORIZED_FACT") -> dict[str, object]:
        return {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "attachment_id": None,
            "filename": None,
            "page_no": None,
            "sheet_name": None,
            "table_index": None,
            "row": None,
            "column": None,
            "cell_range": None,
            "quote": text,
            "source_hash": text_source_hash(text),
            "region": None,
            "bbox": None,
        }

    def evidence_pack(self) -> dict[str, object]:
        direct = create_evidence_item(
            level="A",
            kind="article_text",
            value="AUTHORIZED_FACT",
            source_ref=self.source_ref(),
            mandatory=True,
        )
        context = create_evidence_item(
            level="C",
            kind="summary",
            value="C_ONLY_SECRET",
        )
        return validate_evidence_pack(
            {
                "pack_id": "pack_s1atest01",
                "evidence_schema_version": 2,
                "evidence_items": [direct, context],
                "primary_materials": [
                    {
                        "material_role": "primary",
                        "menu_code": "project_notice",
                        "articleid": "article-1",
                        "title": "Test notice",
                        "content_text": "AUTHORIZED_FACT",
                        "attachments": [],
                    }
                ],
                "auxiliary_materials": [],
                "generation_guidance": {"secret": "GUIDANCE_SECRET"},
                "warnings": ["WARNING_SECRET"],
                "memory": "MEMORY_SECRET",
                "history": "HISTORY_SECRET",
                "diagnostics": "DIAGNOSTIC_SECRET",
            }
        )

    def test_database_pack_emits_valid_v2_evidence_items(self) -> None:
        row = {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "title": "Test notice",
            "content": "<p>Direct database article fact with enough source text for analysis.</p>",
            "summary": "Context summary",
            "areaname": "Shandong",
            "publicorg": "Test authority",
        }
        request = main_module.SelectionPreviewRequest(
            primary_materials=[
                main_module.MaterialRef(menu_code="project_notice", articleid="article-1")
            ]
        )
        with patch.object(
            main_module,
            "_fetch_database_material_rows",
            return_value={("project_notice", "article-1"): row},
        ), patch.object(
            main_module,
            "_fetch_database_attachments",
            return_value={("project_notice", "article-1"): []},
        ), patch.object(main_module, "_write_database_evidence_pack"):
            pack = main_module._build_database_evidence_pack(request)

        self.assertIsInstance(pack, dict)
        validated = validate_evidence_pack(pack)
        self.assertEqual(2, validated["evidence_schema_version"])
        self.assertTrue(validated["evidence_items"])
        self.assertTrue(any(item["level"] == "A" for item in validated["evidence_items"]))
        self.assertTrue(any(item["level"] == "C" for item in validated["evidence_items"]))
        for item in validated["evidence_items"]:
            if item["level"] in {"A", "B"}:
                self.assertEqual("project_notice", item["source_ref"]["menu_code"])
                self.assertEqual("article-1", item["source_ref"]["articleid"])

    def test_compact_preserves_evidence_provenance_exactly(self) -> None:
        pack = self.evidence_pack()

        compact = main_module._compact_evidence_pack_for_dify(pack, max_chars=80_000)

        self.assertEqual(2, compact["evidence_schema_version"])
        self.assertEqual(pack["evidence_items"], compact["evidence_items"])
        for original, compacted in zip(pack["evidence_items"], compact["evidence_items"]):
            self.assertEqual(original["evidence_id"], compacted["evidence_id"])
            self.assertEqual(original["source_ref"], compacted["source_ref"])
            self.assertEqual(original["level"], compacted["level"])
            self.assertEqual(original["mandatory"], compacted["mandatory"])

    def test_database_pack_reader_adapts_v1_without_rewriting_and_rejects_unknown(self) -> None:
        legacy = {
            "pack_id": "pack_v1fixture",
            "primary_materials": [
                {
                    "material_role": "primary",
                    "menu_code": "project_notice",
                    "articleid": "article-1",
                    "title": "Legacy title",
                    "content_text": "Legacy direct article text",
                    "attachments": [],
                }
            ],
            "auxiliary_materials": [],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "pack_v1fixture.json"
            raw = json.dumps(legacy, ensure_ascii=False, separators=(",", ":"))
            path.write_text(raw, encoding="utf-8")
            unknown_path = root / "pack_unknown1.json"
            unknown_path.write_text(
                json.dumps(
                    {
                        "pack_id": "pack_unknown1",
                        "evidence_schema_version": 999,
                        "evidence_items": [],
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(main_module, "_database_evidence_pack_dir", return_value=root):
                view = main_module._read_database_evidence_pack("pack_v1fixture")
                with self.assertRaises(HTTPException) as caught:
                    main_module._read_database_evidence_pack("pack_unknown1")

            self.assertEqual(2, view["evidence_schema_version"])
            self.assertEqual(1, view["source_evidence_schema_version"])
            self.assertEqual(raw, path.read_text(encoding="utf-8"))
            self.assertEqual(500, caught.exception.status_code)

    def test_quality_gate_evidence_text_uses_only_validated_a_b(self) -> None:
        evidence_text = diagnostics._pack_evidence_text(self.evidence_pack())

        self.assertIn("AUTHORIZED_FACT", evidence_text)
        for forbidden in (
            "C_ONLY_SECRET",
            "GUIDANCE_SECRET",
            "WARNING_SECRET",
            "MEMORY_SECRET",
            "HISTORY_SECRET",
            "DIAGNOSTIC_SECRET",
        ):
            self.assertNotIn(forbidden, evidence_text)

    def test_pure_v2_pack_cannot_bypass_a_b_quality_gate(self) -> None:
        pack = self.evidence_pack()
        pack.pop("primary_materials", None)
        pack.pop("auxiliary_materials", None)

        gate = diagnostics._build_quality_gate(
            {
                "status": "finished",
                "report_markdown": "# 报告\n\n本项目价格为999元。",
            },
            pack,
            {},
        )

        self.assertEqual(1, gate["unsupported_fact_count"])
        self.assertEqual("needs_manual_review", gate["deliverable_status"])

    def test_quality_gate_ignores_legacy_coverage_signals_not_backed_by_a_b(self) -> None:
        pack = self.evidence_pack()
        pack["primary_materials"][0]["price_rules"] = ["C_ONLY_PRICE_999元"]
        record = {
            "status": "finished",
            "report_markdown": "# Report\n\nAUTHORIZED_FACT",
            "quality_check": {"passed": True, "issues": []},
        }

        result = diagnostics.build_run_diagnostics(record, pack)
        coverage_codes = {item["label"] for item in result["coverage"]["missing_items"]}
        quality_codes = {item["code"] for item in result["quality_gate"]["blocking_issues"]}

        self.assertIn("价格规则", coverage_codes)
        self.assertNotIn("MISSING_CORE_COVERAGE", quality_codes)

    def test_run_schema_maps_legacy_provider_codes_without_rewriting_them(self) -> None:
        normalized = main_module._normalize_analysis_run_schema(
            {
                "run_id": "run-legacy-codes",
                "pack_id": "pack-legacy-codes",
                "status": "failed",
                "dify_error_code": "DIFY_TIMEOUT",
                "generation_failure_codes": [
                    "DIFY_OUTPUT_EMPTY",
                    "DIFY_FRAGMENTARY_REPORT",
                ],
            }
        )

        self.assertEqual("TIMEOUT", normalized["dify_error_code"])
        self.assertIn("TIMEOUT", normalized["generation_failure_codes"])
        self.assertIn("OUTPUT_EMPTY", normalized["generation_failure_codes"])
        self.assertIn("OUTPUT_TRUNCATED", normalized["generation_failure_codes"])
        serialized = json.dumps(
            {
                "dify_error_code": normalized["dify_error_code"],
                "generation_failure_codes": normalized["generation_failure_codes"],
                "primary_failure_code": normalized["primary_failure_code"],
                "secondary_failure_codes": normalized["secondary_failure_codes"],
            }
        )
        for legacy in ("DIFY_TIMEOUT", "DIFY_OUTPUT_EMPTY", "DIFY_FRAGMENTARY_REPORT"):
            self.assertNotIn(legacy, serialized)

    def test_run_schema_maps_unknown_provider_code_to_unclassified(self) -> None:
        normalized = main_module._normalize_analysis_run_schema(
            {
                "run_id": "run-unknown-provider-code",
                "pack_id": "pack-unknown-provider-code",
                "status": "failed",
                "generation_failure_codes": ["DIFY_VENDOR_ERROR"],
            }
        )

        self.assertNotIn("DIFY_VENDOR_ERROR", normalized["generation_failure_codes"])
        self.assertIn("UNCLASSIFIED", normalized["generation_failure_codes"])
        self.assertEqual("UNCLASSIFIED", normalized["primary_failure_code"])

    def test_dify_config_and_revision_failures_write_canonical_safe_provider_codes(self) -> None:
        with patch.dict(
            main_module.os.environ,
            {"DIFY_BASE_URL": "", "DIFY_WORKFLOW_API_KEY": ""},
            clear=False,
        ):
            with self.assertRaises(main_module.DifyWorkflowError) as missing_config:
                main_module._dify_config()

        self.assertEqual("HTTP_ERROR", missing_config.exception.code)
        self.assertEqual("HTTP_ERROR", missing_config.exception.provider_stage["code"])

        secret_body = "provider-secret-response-body"
        request = main_module.httpx.Request("POST", "https://dify.invalid/workflows/run")
        response = main_module.httpx.Response(500, request=request, text=secret_body)

        class FailedResponse:
            def raise_for_status(self) -> None:
                raise main_module.httpx.HTTPStatusError(
                    "provider request failed",
                    request=request,
                    response=response,
                )

        class FailedClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def post(self, *args, **kwargs):
                return FailedResponse()

        config = {
            "base_url": "https://dify.invalid",
            "api_key": "not-a-real-key",
            "endpoint": "/workflows/run",
            "response_mode": "blocking",
            "user": "test",
            "timeout_seconds": 1,
        }
        with patch.object(main_module, "_dify_config", return_value=config), patch.object(
            main_module.httpx,
            "Client",
            FailedClient,
        ):
            with self.assertRaises(main_module.DifyWorkflowError) as revision_failure:
                main_module._call_dify_revision_workflow(
                    pack_id="pack-safe",
                    run_id="run-safe",
                    feedback="revise",
                    current_report="current",
                    evidence_pack={"pack_id": "pack-safe"},
                    analysis_highlight=False,
                )

        self.assertEqual("HTTP_ERROR", revision_failure.exception.code)
        self.assertEqual("HTTP_ERROR", revision_failure.exception.provider_stage["code"])
        self.assertNotIn(secret_body, revision_failure.exception.detail)

    def test_invalid_evidence_cannot_support_a_report_fact(self) -> None:
        pack = self.evidence_pack()
        pack["evidence_items"][0]["source_ref"]["source_hash"] = "0" * 64

        self.assertEqual("", diagnostics._pack_evidence_text(pack))

    def test_run_schema_clears_stale_failure_for_nonterminal_and_deliverable(self) -> None:
        running = main_module._normalize_analysis_run_schema(
            {
                "run_id": "run_running01",
                "pack_id": "pack_running01",
                "status": "running",
                "primary_layer": "provider",
                "primary_failure_code": "TIMEOUT",
                "secondary_failure_codes": ["OUTPUT_EMPTY"],
                "failure_stages": [
                    make_layer_result("provider", status="blocked", code="TIMEOUT")
                ],
            }
        )
        delivered = main_module._normalize_analysis_run_schema(
            {
                "run_id": "run_delivered1",
                "pack_id": "pack_delivered1",
                "status": "finished",
                "report_markdown": "# Report\n\n## Facts\n\nA complete evidence-based report body.",
                "quality_check": {"passed": True, "issues": []},
                "quality_gate": {"deliverable_status": "deliverable"},
                "primary_layer": "provider",
                "primary_failure_code": "TIMEOUT",
                "failure_stages": [
                    make_layer_result("provider", status="blocked", code="TIMEOUT")
                ],
            }
        )

        for record in (running, delivered):
            self.assertEqual(FAILURE_SCHEMA_VERSION, record["failure_schema_version"])
            self.assertEqual("", record["primary_layer"])
            self.assertEqual("", record["primary_failure_code"])
            self.assertEqual([], record["secondary_failure_codes"])

    def test_run_schema_uses_first_causal_layer_and_interrupted_is_terminal(self) -> None:
        timeout = main_module._normalize_analysis_run_schema(
            {
                "run_id": "run_timeout001",
                "pack_id": "pack_timeout001",
                "status": "failed",
                "failure_stages": [
                    make_layer_result("quality_gate", status="blocked", code="UNSUPPORTED_FACT"),
                    make_layer_result("provider", status="blocked", code="TIMEOUT"),
                ],
            }
        )
        interrupted = main_module._normalize_analysis_run_schema(
            {
                "run_id": "run_interrupt1",
                "pack_id": "pack_interrupt1",
                "status": "interrupted",
            }
        )

        self.assertEqual("provider", timeout["primary_layer"])
        self.assertEqual("TIMEOUT", timeout["primary_failure_code"])
        self.assertEqual(["UNSUPPORTED_FACT"], timeout["secondary_failure_codes"])
        self.assertEqual("quality_gate", interrupted["primary_layer"])
        self.assertEqual("UNCLASSIFIED", interrupted["primary_failure_code"])

    def test_terminal_update_replaces_running_not_run_layers_with_real_quality_failure(self) -> None:
        running = main_module._normalize_analysis_run_schema(
            {
                "run_id": "run_transition1",
                "pack_id": "pack_transition1",
                "status": "running",
            }
        )
        running.update(
            {
                "status": "needs_manual_review",
                "report_markdown": "# Report\n\n## Facts\n\nA generated report body.",
                "quality_check": {"passed": False, "issues": []},
                "quality_gate": {
                    "deliverable_status": "needs_manual_review",
                    "unsupported_fact_count": 1,
                    "blocking_issue_codes": ["UNSUPPORTED_FACT"],
                },
            }
        )

        terminal = main_module._normalize_analysis_run_schema(running)

        self.assertEqual("quality_gate", terminal["primary_layer"])
        self.assertEqual("UNSUPPORTED_FACT", terminal["primary_failure_code"])
        self.assertEqual(
            "blocked",
            terminal["failure_attribution"]["layers"]["quality_gate"]["status"],
        )

    def test_current_provider_stage_replaces_stale_blocked_provider_layer(self) -> None:
        failed = main_module._normalize_analysis_run_schema(
            {
                "run_id": "run_recovered01",
                "pack_id": "pack_recovered01",
                "status": "failed",
                "failure_stages": [
                    make_layer_result("provider", status="blocked", code="TIMEOUT")
                ],
            }
        )
        provider_ok = make_layer_result(
            "provider",
            status="ok",
            output_value={"workflow_run_id": "wf-recovered"},
        )
        failed.update(
            {
                "status": "finished",
                "report_markdown": "# Report\n\n## Facts\n\nA recovered report body.",
                "quality_check": {"passed": True, "issues": []},
                "quality_gate": {"deliverable_status": "deliverable"},
                "dify_error_code": "",
                "generation_failure_codes": [],
                "quality_failure_codes": [],
                "blocking_issue_codes": [],
                "provider_stage": provider_ok,
                "failure_stages": [provider_ok],
            }
        )

        recovered = main_module._normalize_analysis_run_schema(failed)

        self.assertTrue(recovered["deliverable"])
        self.assertEqual("", recovered["primary_failure_code"])
        self.assertEqual(
            "ok",
            recovered["failure_attribution"]["layers"]["provider"]["status"],
        )

    def test_dify_normalization_requires_workflow_id_and_exposes_safe_provider_stage(self) -> None:
        missing_id = {
            "data": {
                "status": "succeeded",
                "outputs": {"report_markdown": "# Provider report"},
            }
        }
        with self.assertRaises(main_module.DifyWorkflowError) as caught:
            main_module._normalize_dify_result(missing_id, "pack_provider1")

        result = main_module._normalize_dify_result(
            {
                "workflow_run_id": "wf-1",
                "data": {
                    "status": "succeeded",
                    "outputs": {"report_markdown": "# Provider report"},
                },
            },
            "pack_provider1",
        )

        self.assertEqual("WORKFLOW_ID_MISSING", caught.exception.code)
        self.assertEqual("wf-1", result["workflow_run_id"])
        self.assertEqual("ok", result["provider_stage"]["status"])
        self.assertNotIn("Provider report", json.dumps(result["provider_stage"]))

    def test_real_provider_stage_records_compact_input_and_safe_output_hashes(self) -> None:
        pack = self.evidence_pack()
        result = main_module._normalize_dify_result(
            {
                "workflow_run_id": "wf-stage-hashes",
                "data": {
                    "status": "succeeded",
                    "outputs": {
                        "report_markdown": "# Report\n\n## Analysis\n\nAUTHORIZED_FACT",
                        "quality_check": {"passed": True, "issues": []},
                    },
                },
            },
            "pack_s1atest01",
        )

        staged = main_module._postprocess_generation_with_stages(result, pack)
        provider = staged["provider_stage"]

        self.assertIsNotNone(provider["input_artifact"])
        self.assertIsNotNone(provider["output_artifact"])
        self.assertNotIn("AUTHORIZED_FACT", json.dumps(provider, ensure_ascii=False))

    def test_history_explicit_empty_failure_fields_clear_previous_terminal_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = AnalysisHistoryStore(Path(tmpdir))
            store.record_run(
                {
                    "run_id": "run_history01",
                    "pack_id": "pack_history01",
                    "status": "failed",
                    "primary_layer": "provider",
                    "primary_failure_code": "TIMEOUT",
                    "secondary_failure_codes": ["UNSUPPORTED_FACT"],
                    "failure_schema_version": FAILURE_SCHEMA_VERSION,
                }
            )
            item = store.record_run(
                {
                    "run_id": "run_history01",
                    "pack_id": "pack_history01",
                    "status": "running",
                    "primary_layer": "",
                    "primary_failure_code": "",
                    "secondary_failure_codes": [],
                    "failure_schema_version": FAILURE_SCHEMA_VERSION,
                }
            )

        self.assertEqual("", item["primary_layer"])
        self.assertEqual("", item["primary_failure_code"])
        self.assertEqual([], item["secondary_failure_codes"])
        self.assertEqual(FAILURE_SCHEMA_VERSION, item["failure_schema_version"])

    def test_evaluator_rules_explicitly_version_evidence_and_failure_schemas(self) -> None:
        from app import offline_quality_evaluator

        self.assertEqual(2, offline_quality_evaluator.EVALUATOR_RULES["evidence_schema_version"])
        self.assertEqual(
            FAILURE_SCHEMA_VERSION,
            offline_quality_evaluator.EVALUATOR_RULES["failure_schema_version"],
        )
        self.assertGreaterEqual(tuple(map(int, offline_quality_evaluator.EVALUATOR_VERSION.split("."))), (1, 5, 0))


if __name__ == "__main__":
    unittest.main()
