from __future__ import annotations

import copy
import os
import unittest
from unittest.mock import patch

from app import main as main_module
from app.evidence_schema import create_evidence_item, text_source_hash
from app.formal_body_safety import scan_formal_body
from app.generation.base import ReportGenerationResult
from app.formal_body import FormalBodyDocument


class ControlledRepairPipelineTests(unittest.TestCase):
    @staticmethod
    def source_ref(text: str) -> dict:
        return {
            "menu_code": "project_notice",
            "articleid": "notice-repair-1",
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
        }

    def pack(self) -> dict:
        text = "采购周期为2年。"
        direct = create_evidence_item(
            level="A",
            kind="article_text",
            value=text,
            source_ref=self.source_ref(text),
        )
        derived = create_evidence_item(
            level="B",
            kind="derived_fact",
            value={"fact_type": "procurement_cycle", "value": "采购周期为2年"},
            normalized_value={"fact_type": "procurement_cycle", "value": "P2Y"},
            source_ref=direct["source_ref"],
            derived_from=[direct["evidence_id"]],
            extractor_version="test-v1",
            mandatory=True,
        )
        return {
            "evidence_schema_version": 2,
            "evidence_items": [direct, derived],
            "primary_materials": [
                {
                    "menu_code": "project_notice",
                    "menu_name": "项目公告",
                    "articleid": "notice-repair-1",
                    "title": "带量采购项目公告",
                }
            ],
            "vbp_facts": [
                {
                    "evidence_id": derived["evidence_id"],
                    "fact_type": "procurement_cycle",
                    "value": "采购周期为2年",
                    "source_ref": copy.deepcopy(derived["source_ref"]),
                }
            ],
            "vbp_fact_diagnostics": [],
        }

    @staticmethod
    def markdown(body: str) -> str:
        return "\n\n".join(
            [
                "## 公告要点",
                body,
                "## 核心规则",
                "## 时间节点与操作步骤",
                "## 企业影响与风险提示",
                "## 企业操作建议",
            ]
        )

    @staticmethod
    def enabled_env() -> dict[str, str]:
        return {
            "ENABLE_UNSUPPORTED_FACT_REPAIR": "true",
            "ENABLE_STRICT_DELIVERY_GATE": "true",
            "ENABLE_EVIDENCE_INDEX": "true",
            "ENABLE_VBP_QUALITY_GATE": "true",
        }

    def run_repair(self, result: ReportGenerationResult) -> ReportGenerationResult:
        from app.repair_pipeline import ForbiddenPhraseRepairer, RepairPipeline, UnsupportedFactRepairer

        with patch.dict(os.environ, self.enabled_env(), clear=False):
            return RepairPipeline(
                repairers=[UnsupportedFactRepairer(), ForbiddenPhraseRepairer()]
            ).run(result, self.pack())

    def test_s2_repair_flags_are_default_off(self):
        root = main_module.Path(__file__).resolve().parents[1]
        env_path = root / ".env.example"
        compose_path = root / "docker-compose.yml"
        if not env_path.exists() or not compose_path.exists():
            from app.repair_pipeline import controlled_repair_enabled, strict_delivery_gate_enabled

            with patch.dict(os.environ, {}, clear=True):
                self.assertFalse(controlled_repair_enabled())
                self.assertFalse(strict_delivery_gate_enabled())
            return
        env_example = env_path.read_text(encoding="utf-8")
        compose = compose_path.read_text(encoding="utf-8")

        for name in ("ENABLE_UNSUPPORTED_FACT_REPAIR", "ENABLE_STRICT_DELIVERY_GATE"):
            self.assertIn(f"{name}=false", env_example)
            self.assertIn(f"{name}: ${{{name}:-false}}", compose)

    def test_clean_input_is_returned_unchanged_without_repair_attempt(self):
        from app.repair_pipeline import RepairPipeline, UnsupportedFactRepairer

        original = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown=self.markdown("采购周期为2年。"),
            quality_check={"passed": True, "issues": []},
            metadata={"status": "finished", "sentinel": "unchanged"},
        )
        with patch.dict(os.environ, self.enabled_env(), clear=False):
            repaired = RepairPipeline(repairers=[UnsupportedFactRepairer()]).run(
                original, self.pack()
            )

        self.assertIs(repaired, original)
        self.assertNotIn("repair_count", repaired.metadata)

    def test_markdown_repair_deletes_only_unsupported_sentence(self):
        original = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown=self.markdown("采购周期为2年。最高有效申报价999元。"),
            quality_check={"passed": True, "issues": []},
            metadata={"status": "finished"},
        )

        repaired = self.run_repair(original)

        self.assertIn("采购周期为2年", repaired.report_markdown)
        self.assertNotIn("999元", repaired.report_markdown)
        self.assertTrue(repaired.metadata["repair_attempted"])
        self.assertTrue(repaired.metadata["repair_success"])
        self.assertEqual(1, repaired.metadata["repair_count"])
        self.assertEqual(1, len(repaired.metadata["repair_actions"]))
        self.assertEqual("delete_unsupported_claim", repaired.metadata["repair_actions"][0]["action"])
        self.assertEqual(0, repaired.metadata["claim_evidence_index"]["metrics"]["unsupported_claim_count"])
        self.assertTrue(repaired.metadata["vbp_quality_gate"]["passed"])

    def test_markdown_repair_narrows_mixed_sentence_without_deleting_supported_clause(self):
        original = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown=self.markdown("采购周期为2年，最高有效申报价999元。"),
            quality_check={"passed": True, "issues": []},
            metadata={"status": "finished"},
        )

        repaired = self.run_repair(original)

        self.assertIn("采购周期为2年", repaired.report_markdown)
        self.assertNotIn("999元", repaired.report_markdown)
        self.assertTrue(repaired.metadata["repair_success"])
        self.assertEqual(1, repaired.metadata["repair_count"])
        self.assertEqual(
            "narrow_unsupported_claim",
            repaired.metadata["repair_actions"][0]["action"],
        )
        self.assertEqual(0, repaired.metadata["repair_new_fact_count"])
        self.assertEqual(
            0,
            repaired.metadata["claim_evidence_index"]["metrics"]["unsupported_claim_count"],
        )

    def test_markdown_repair_with_only_unsupported_content_remains_fail_closed(self):
        original = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown="## 导语\n\n最高有效申报价999元。",
            quality_check={"passed": False, "issues": []},
            metadata={"status": "needs_manual_review"},
        )

        repaired = self.run_repair(original)

        self.assertFalse(repaired.metadata["repair_success"])
        self.assertEqual("CONTROLLED_REPAIR_OUTPUT_EMPTY", repaired.metadata["repair_failure_code"])
        self.assertFalse(repaired.metadata["formal_body_present"])
        self.assertFalse(repaired.metadata["deliverable"])
        self.assertEqual("", repaired.metadata["word_download_url"])

    def test_markdown_table_repair_removes_only_unsupported_row(self):
        markdown = self.markdown(
            "| 规则 | 数值 |\n"
            "| --- | ---: |\n"
            "| 采购周期 | 2年 |\n"
            "| 最高有效申报价 | 999元 |"
        )
        result = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown=markdown,
            quality_check={"passed": True, "issues": []},
            metadata={"status": "finished"},
        )

        repaired = self.run_repair(result)

        self.assertIn("| 采购周期 | 2年 |", repaired.report_markdown)
        self.assertNotIn("999元", repaired.report_markdown)
        self.assertEqual(1, repaired.metadata["repair_count"])

    def test_report_ir_repair_handles_sentences_and_table_rows(self):
        report_ir = {
            "title": "项目公告分析",
            "lead_paragraphs": ["采购周期为2年，最高有效申报价999元。"],
            "sections": [
                {"heading": "公告要点", "paragraphs": [], "highlights": [], "tables": []},
                {
                    "heading": "核心规则",
                    "paragraphs": [],
                    "highlights": [],
                    "tables": [
                        {
                            "title": "规则",
                            "headers": ["规则", "数值"],
                            "rows": [["采购周期", "2年"], ["最高有效申报价", "999元"]],
                        }
                    ],
                },
                {"heading": "时间节点与操作步骤", "paragraphs": [], "highlights": [], "tables": []},
                {"heading": "企业影响与风险提示", "paragraphs": [], "highlights": [], "tables": []},
                {"heading": "企业操作建议", "paragraphs": [], "highlights": [], "tables": []},
            ],
        }
        result = ReportGenerationResult(
            success=True,
            provider="dify",
            report_ir=report_ir,
            quality_check={"passed": True, "issues": []},
            metadata={"status": "finished"},
        )

        repaired = self.run_repair(result)

        self.assertEqual(["采购周期为2年。"], repaired.report_ir["lead_paragraphs"])
        rows = repaired.report_ir["sections"][1]["tables"][0]["rows"]
        self.assertEqual([["采购周期", "2年"]], rows)
        self.assertNotIn("999元", str(repaired.report_ir))

    def test_repair_is_deterministic_idempotent_and_never_adds_fact_tokens(self):
        def make_result() -> ReportGenerationResult:
            return ReportGenerationResult(
                success=True,
                provider="dify",
                report_markdown=self.markdown("采购周期为2年。最高有效申报价999元。"),
                quality_check={"passed": True, "issues": []},
                metadata={"status": "finished"},
            )

        first = self.run_repair(make_result())
        second = self.run_repair(make_result())
        third = self.run_repair(first)

        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.assertEqual(1, third.metadata["repair_count"])
        self.assertNotIn("999", third.report_markdown)
        self.assertNotIn("1000", third.report_markdown)

    def test_forbidden_phrase_is_removed_before_final_reindex_and_scan(self):
        result = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown=self.markdown("采购周期为2年。最高有效申报价999元，以上内容需复核。"),
            quality_check={"passed": True, "issues": []},
            metadata={"status": "finished"},
        )

        repaired = self.run_repair(result)
        safety = scan_formal_body(
            FormalBodyDocument(markdown=repaired.report_markdown, report_ir=repaired.report_ir)
        )

        self.assertTrue(safety.safe)
        self.assertEqual((), safety.hits)
        self.assertIn("采购周期为2年", repaired.report_markdown)
        self.assertTrue(repaired.metadata["repair_success"])

    def test_forbidden_phrase_cleaner_preserves_safe_sentence_on_same_line(self):
        from app.repair_pipeline import ForbiddenPhraseRepairer

        result = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown=self.markdown("采购周期为2年。以上内容需复核。"),
            quality_check={"passed": True, "issues": []},
            metadata={"status": "finished"},
        )

        repaired = ForbiddenPhraseRepairer().run(result, self.pack())
        safety = scan_formal_body(
            FormalBodyDocument(markdown=repaired.report_markdown, report_ir=repaired.report_ir)
        )

        self.assertIn("采购周期为2年", repaired.report_markdown)
        self.assertNotIn("以上内容需复核", repaired.report_markdown)
        self.assertTrue(safety.safe)

    def test_index_failure_is_fail_closed_and_clears_all_download_locators(self):
        from app.repair_pipeline import RepairPipeline, UnsupportedFactRepairer

        result = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown=self.markdown("最高有效申报价999元。"),
            quality_check={"passed": True, "issues": []},
            metadata={
                "status": "finished",
                "word_download_url": "/analysis/runs/run_1/word",
                "download_url": "/analysis/runs/run_1/word",
                "word_filename": "unsafe.docx",
                "word_file_path": "/tmp/unsafe.docx",
                "word_path": "/tmp/unsafe.docx",
                "word_generated": True,
                "word_export_available": True,
                "draft_word_export_available": True,
                "final_word_export_available": True,
                "deliverable": True,
            },
        )
        with patch.dict(os.environ, self.enabled_env(), clear=False), patch(
            "app.repair_pipeline.build_claim_evidence_index",
            side_effect=RuntimeError("broken"),
        ):
            repaired = RepairPipeline(repairers=[UnsupportedFactRepairer()]).run(
                result, self.pack()
            )

        self.assertTrue(repaired.metadata["repair_attempted"])
        self.assertFalse(repaired.metadata["repair_success"])
        self.assertEqual(1, repaired.metadata["repair_count"])
        self.assertEqual("CONTROLLED_REPAIR_INDEX_FAILED", repaired.metadata["repair_failure_code"])
        for field in (
            "word_download_url",
            "download_url",
            "word_filename",
            "word_file_path",
            "word_path",
        ):
            self.assertEqual("", repaired.metadata[field])
        for field in (
            "word_generated",
            "word_export_available",
            "draft_word_export_available",
            "final_word_export_available",
            "deliverable",
        ):
            self.assertFalse(repaired.metadata[field])

    def test_exception_after_repair_started_is_fail_closed(self):
        from app.repair_pipeline import RepairPipeline, UnsupportedFactRepairer

        class FailingRepairer:
            def run(self, result, pack):
                raise RuntimeError("cleanup failed")

        result = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown=self.markdown("采购周期为2年。最高有效申报价999元。"),
            quality_check={"passed": True, "issues": []},
            metadata={"status": "finished", "word_download_url": "/unsafe.docx"},
        )
        with patch.dict(os.environ, self.enabled_env(), clear=False):
            repaired = RepairPipeline(
                repairers=[UnsupportedFactRepairer(), FailingRepairer()]
            ).run(result, self.pack())

        self.assertTrue(repaired.metadata["repair_attempted"])
        self.assertFalse(repaired.metadata["repair_success"])
        self.assertEqual("CONTROLLED_REPAIR_PIPELINE_FAILED", repaired.metadata["repair_failure_code"])
        self.assertEqual("", repaired.metadata["word_download_url"])

    def test_vbp_gate_failure_after_repair_is_fail_closed(self):
        from app.repair_pipeline import RepairPipeline, UnsupportedFactRepairer

        result = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown=self.markdown("采购周期为2年。最高有效申报价999元。"),
            quality_check={"passed": True, "issues": []},
            metadata={"status": "finished", "word_download_url": "/unsafe.docx"},
        )
        blocked_gate = {
            "passed": False,
            "primary_failure_code": "VBP_UNSUPPORTED_CLAIM",
            "blocking_issue_codes": ["VBP_UNSUPPORTED_CLAIM"],
        }
        with patch.dict(os.environ, self.enabled_env(), clear=False), patch(
            "app.repair_pipeline.evaluate_vbp_quality",
            return_value=blocked_gate,
        ):
            repaired = RepairPipeline(repairers=[UnsupportedFactRepairer()]).run(
                result, self.pack()
            )

        self.assertFalse(repaired.metadata["repair_success"])
        self.assertEqual(
            "CONTROLLED_REPAIR_VBP_GATE_FAILED",
            repaired.metadata["repair_failure_code"],
        )
        self.assertEqual("", repaired.metadata["word_download_url"])

    def test_structural_gate_after_safe_repair_allows_draft_only_state(self):
        from app.repair_pipeline import RepairPipeline, UnsupportedFactRepairer

        result = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown=self.markdown("采购周期为2年。最高有效申报价999元。"),
            quality_check={"passed": True, "issues": []},
            metadata={"status": "finished"},
        )
        blocked_gate = {
            "passed": False,
            "primary_failure_code": "VBP_REQUIRED_SECTION_MISSING",
            "blocking_issue_codes": ["VBP_REQUIRED_SECTION_MISSING"],
        }
        with patch.dict(os.environ, self.enabled_env(), clear=False), patch(
            "app.repair_pipeline.evaluate_vbp_quality",
            return_value=blocked_gate,
        ):
            repaired = RepairPipeline(repairers=[UnsupportedFactRepairer()]).run(
                result, self.pack()
            )

        self.assertTrue(repaired.metadata["repair_success"])
        self.assertEqual("", repaired.metadata["repair_failure_code"])
        record = repaired.to_legacy_result()
        record.update(
            {
                "status": "needs_manual_review",
                "quality_check": {"passed": False, "issues": []},
                "quality_gate": {
                    "deliverable_status": "needs_manual_review",
                    "blocking_issue_codes": ["VBP_REQUIRED_SECTION_MISSING"],
                },
            }
        )
        with patch.dict(os.environ, {"ENABLE_WORD_EXPORT": "true"}, clear=False):
            normalized = main_module._normalize_analysis_run_schema(record)

        self.assertTrue(normalized["formal_body_present"])
        self.assertTrue(normalized["draft_word_export_available"])
        self.assertTrue(normalized["word_export_available"])
        self.assertFalse(normalized["final_word_export_available"])
        self.assertFalse(normalized["deliverable"])
        self.assertTrue(normalized["needs_manual_review"])

    def test_new_claim_after_repair_is_detected_instead_of_declared_zero(self):
        from app.evidence_index import build_claim_evidence_index
        from app.repair_pipeline import RepairPipeline, UnsupportedFactRepairer

        markdown = self.markdown("采购周期为2年。最高有效申报价999元。")
        result = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown=markdown,
            quality_check={"passed": True, "issues": []},
            metadata={"status": "finished", "word_download_url": "/unsafe.docx"},
        )
        initial_index = build_claim_evidence_index(
            FormalBodyDocument(markdown=markdown), self.pack()
        )
        supported = next(
            copy.deepcopy(claim)
            for claim in initial_index["claims"]
            if claim["supported"]
        )
        final_index = {
            **copy.deepcopy(initial_index),
            "claims": [
                supported,
                {
                    **copy.deepcopy(supported),
                    "claim_id": "f" * 64,
                    "normalized_text": "新增事实1000元",
                },
            ],
            "metrics": {
                "claim_count": 2,
                "supported_claim_count": 2,
                "unsupported_claim_count": 0,
                "ab_support_rate": 1.0,
                "c_independent_support_count": 0,
            },
        }
        with patch.dict(os.environ, self.enabled_env(), clear=False), patch(
            "app.repair_pipeline.build_claim_evidence_index",
            side_effect=[initial_index, final_index],
        ), patch(
            "app.repair_pipeline.evaluate_vbp_quality",
            return_value={"passed": True, "blocking_issue_codes": []},
        ):
            repaired = RepairPipeline(repairers=[UnsupportedFactRepairer()]).run(
                result, self.pack()
            )

        self.assertFalse(repaired.metadata["repair_success"])
        self.assertEqual(1, repaired.metadata["repair_new_fact_count"])
        self.assertEqual(
            "CONTROLLED_REPAIR_NEW_FACT_DETECTED",
            repaired.metadata["repair_failure_code"],
        )
        self.assertEqual("", repaired.metadata["word_download_url"])

    def test_narrowed_claim_id_after_deletion_is_not_a_new_fact(self):
        from app.evidence_index import build_claim_evidence_index
        from app.repair_pipeline import RepairPipeline, UnsupportedFactRepairer

        markdown = self.markdown("采购周期为2年。最高有效申报价999元。")
        result = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown=markdown,
            quality_check={"passed": True, "issues": []},
            metadata={"status": "finished"},
        )
        initial_index = build_claim_evidence_index(
            FormalBodyDocument(markdown=markdown), self.pack()
        )
        supported = next(
            copy.deepcopy(claim)
            for claim in initial_index["claims"]
            if claim["supported"] and len(claim["normalized_text"]) > 2
        )
        narrowed = {
            **copy.deepcopy(supported),
            "claim_id": "e" * 64,
            "normalized_text": supported["normalized_text"][1:],
        }
        final_index = {
            **copy.deepcopy(initial_index),
            "claims": [narrowed],
            "metrics": {
                "claim_count": 1,
                "supported_claim_count": 1,
                "unsupported_claim_count": 0,
                "ab_support_rate": 1.0,
                "c_independent_support_count": 0,
            },
        }
        with patch.dict(os.environ, self.enabled_env(), clear=False), patch(
            "app.repair_pipeline.build_claim_evidence_index",
            side_effect=[initial_index, final_index],
        ), patch(
            "app.repair_pipeline.evaluate_vbp_quality",
            return_value={"passed": True, "blocking_issue_codes": []},
        ):
            repaired = RepairPipeline(repairers=[UnsupportedFactRepairer()]).run(
                result, self.pack()
            )

        self.assertTrue(repaired.metadata["repair_success"])
        self.assertEqual(0, repaired.metadata["repair_new_fact_count"])
        self.assertEqual(["e" * 64], repaired.metadata["repair_narrowed_claim_ids"])

    def test_clean_forbidden_phrase_repairer_preserves_carriers_exactly(self):
        from app.repair_pipeline import ForbiddenPhraseRepairer

        markdown = "## 公告要点\n\n  采购周期为2年。  \n"
        report_ir = {
            "title": "项目公告分析",
            "lead_paragraphs": ["  采购周期为2年。  "],
            "sections": [],
        }
        result = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown=markdown,
            report_ir=copy.deepcopy(report_ir),
            metadata={"status": "finished"},
        )

        repaired = ForbiddenPhraseRepairer().run(result, self.pack())

        self.assertEqual(markdown, repaired.report_markdown)
        self.assertEqual(report_ir, repaired.report_ir)

    def test_clean_report_ir_preserves_non_string_values_and_empty_items_exactly(self):
        from app.repair_pipeline import ForbiddenPhraseRepairer

        report_ir = {
            "title": "项目公告分析",
            "lead_paragraphs": ["采购周期为2年。", 0, "", False],
            "sections": [
                {
                    "heading": "公告要点",
                    "paragraphs": ["采购周期为2年。", ""],
                    "highlights": [0, False],
                    "tables": [
                        {
                            "title": "规则",
                            "headers": ["值"],
                            "rows": [[0], [None], [False]],
                            "notes": ["", 0],
                        }
                    ],
                }
            ],
        }
        result = ReportGenerationResult(
            success=True,
            provider="dify",
            report_ir=copy.deepcopy(report_ir),
            metadata={"status": "finished"},
        )

        repaired = ForbiddenPhraseRepairer().run(result, self.pack())

        self.assertEqual(report_ir, repaired.report_ir)

    def test_background_postprocess_exception_is_fail_closed_under_strict_gate(self):
        class StubGenerator:
            def generate(self, *args, **kwargs):
                return ReportGenerationResult(
                    success=True,
                    provider="dify",
                    report_markdown=self_markdown,
                    quality_check={"passed": True, "issues": []},
                    metadata={
                        "status": "finished",
                        "word_download_url": "/unsafe.docx",
                        "word_filename": "unsafe.docx",
                        "word_generated": True,
                        "deliverable": True,
                    },
                )

        self_markdown = self.markdown("采购周期为2年。")
        writes: list[dict] = []
        strict_only_env = {
            **self.enabled_env(),
            "ENABLE_UNSUPPORTED_FACT_REPAIR": "false",
            "ENABLE_STRICT_DELIVERY_GATE": "true",
        }
        with patch.dict(os.environ, strict_only_env, clear=False), patch.object(
            main_module,
            "_read_analysis_run",
            side_effect=[
                {"run_id": "run-s2", "status": "running"},
                {"run_id": "run-s2", "status": "running"},
                {"status": "running"},
            ],
        ), patch.object(
            main_module, "_read_database_evidence_pack", return_value=self.pack()
        ), patch.object(
            main_module, "_analysis_watchdog_timeout_seconds", return_value=0
        ), patch.object(
            main_module, "_configured_report_generator", return_value=StubGenerator()
        ), patch.object(
            main_module, "_input_pipeline_stages", return_value=([], {})
        ), patch.object(
            main_module,
            "_postprocess_generation_with_stages",
            side_effect=RuntimeError("postprocess failed"),
        ), patch.object(
            main_module,
            "_write_analysis_run",
            side_effect=lambda value: writes.append(copy.deepcopy(value)),
        ):
            main_module._execute_analysis_run_background("pack-s2", "run-s2")

        saved = writes[-1]
        self.assertTrue(any(isinstance(item.get("timings"), dict) for item in writes))
        self.assertEqual("CONTROLLED_REPAIR_POSTPROCESS_FAILED", saved.get("repair_failure_code"))
        self.assertEqual("needs_manual_review", saved.get("status"))
        self.assertFalse(saved.get("deliverable"))
        self.assertFalse(saved.get("word_generated"))
        self.assertEqual("", saved.get("word_download_url"))
        self.assertEqual("", saved.get("word_filename"))

    def test_invalid_repair_count_fails_closed_before_repair(self):
        from app.repair_pipeline import RepairPipeline, UnsupportedFactRepairer

        for invalid_count in (-1, "not-an-integer"):
            with self.subTest(repair_count=invalid_count):
                result = ReportGenerationResult(
                    success=True,
                    provider="dify",
                    report_markdown=self.markdown("采购周期为2年。最高有效申报价999元。"),
                    quality_check={"passed": True, "issues": []},
                    metadata={
                        "status": "finished",
                        "repair_count": invalid_count,
                        "word_download_url": "/unsafe.docx",
                    },
                )
                with patch.dict(os.environ, self.enabled_env(), clear=False):
                    repaired = RepairPipeline(
                        repairers=[UnsupportedFactRepairer()]
                    ).run(result, self.pack())

                self.assertFalse(repaired.metadata["repair_success"])
                self.assertEqual(
                    "CONTROLLED_REPAIR_COUNT_INVALID",
                    repaired.metadata["repair_failure_code"],
                )
                self.assertEqual("", repaired.metadata["word_download_url"])

    def test_normalized_run_keeps_repair_failure_fail_closed(self):
        record = {
            "success": True,
            "status": "needs_manual_review",
            "report_markdown": self.markdown("采购周期为2年。"),
            "quality_check": {"passed": False, "issues": []},
            "quality_gate": {"deliverable_status": "needs_manual_review"},
            "repair_attempted": True,
            "repair_success": False,
            "repair_count": 9,
            "repair_failure_code": "CONTROLLED_REPAIR_VALIDATION_FAILED",
            "word_download_url": "/analysis/runs/run_1/word",
            "download_url": "/analysis/runs/run_1/word",
            "word_filename": "unsafe.docx",
            "word_file_path": "/tmp/unsafe.docx",
            "word_path": "/tmp/unsafe.docx",
            "word_generated": True,
        }
        with patch.dict(os.environ, {"ENABLE_WORD_EXPORT": "true"}, clear=False):
            normalized = main_module._normalize_analysis_run_schema(record)

        self.assertEqual(1, normalized["repair_count"])
        self.assertFalse(normalized["deliverable"])
        self.assertFalse(normalized["word_export_available"])
        self.assertFalse(normalized["draft_word_export_available"])
        self.assertFalse(normalized["final_word_export_available"])
        self.assertFalse(normalized["word_generated"])
        for field in (
            "word_download_url",
            "download_url",
            "word_filename",
            "word_file_path",
            "word_path",
        ):
            self.assertEqual("", normalized[field])

    def test_repair_count_overflow_is_preserved_for_observation_and_fail_closed(self):
        record = {
            "success": True,
            "status": "finished",
            "report_markdown": self.markdown("采购周期为2年。"),
            "quality_check": {"passed": True, "issues": []},
            "repair_attempted": True,
            "repair_success": True,
            "repair_count": 2,
            "word_download_url": "/unsafe.docx",
            "word_generated": True,
            "deliverable": True,
        }

        normalized = main_module._normalize_analysis_run_schema(record)

        self.assertEqual(1, normalized["repair_count"])
        self.assertEqual(2, normalized["repair_count_observed"])
        self.assertTrue(normalized["repair_count_violation"])
        self.assertFalse(normalized["repair_success"])
        self.assertFalse(normalized["deliverable"])
        self.assertEqual("", normalized["word_download_url"])

    def test_invalid_normalized_repair_count_is_not_hidden_as_zero(self):
        for invalid_count in (-1, "not-an-integer"):
            with self.subTest(repair_count=invalid_count):
                normalized = main_module._normalize_analysis_run_schema(
                    {
                        "success": True,
                        "status": "finished",
                        "report_markdown": self.markdown("采购周期为2年。"),
                        "quality_check": {"passed": True, "issues": []},
                        "repair_attempted": True,
                        "repair_success": True,
                        "repair_count": invalid_count,
                        "word_download_url": "/unsafe.docx",
                        "deliverable": True,
                    }
                )

                self.assertTrue(normalized["repair_count_violation"])
                self.assertFalse(normalized["repair_success"])
                self.assertFalse(normalized["deliverable"])
                self.assertEqual("", normalized["word_download_url"])

    def test_analysis_run_response_exposes_bounded_repair_count(self):
        fields = main_module.AnalysisRunResponse.model_fields

        self.assertIn("repair_count", fields)
        self.assertEqual(0, fields["repair_count"].default)

    def test_main_postprocess_runs_repair_then_reindexes_and_gates(self):
        raw = {
            "success": True,
            "provider": "dify",
            "workflow_run_id": "workflow-repair-1",
            "status": "finished",
            "report_title": "项目公告分析",
            "report_markdown": self.markdown("采购周期为2年。" * 30 + "最高有效申报价999元。"),
            "quality_check": {"passed": True, "issues": []},
            "remaining_issues": [],
        }

        with patch.dict(os.environ, self.enabled_env(), clear=False), patch.object(
            main_module,
            "_repair_unusable_dify_result",
            side_effect=lambda result, pack: result,
        ):
            processed = main_module._postprocess_generation_with_stages(
                raw,
                self.pack(),
                input_stages=[],
                compact={"pack_id": "pack-repair-1"},
            )

        self.assertEqual(1, processed["repair_count"])
        self.assertTrue(processed["repair_success"])
        self.assertNotIn("999元", processed["report_markdown"])
        self.assertEqual(
            0,
            processed["claim_evidence_index"]["metrics"]["unsupported_claim_count"],
        )
        self.assertTrue(processed["vbp_quality_gate"]["passed"])


if __name__ == "__main__":
    unittest.main()
