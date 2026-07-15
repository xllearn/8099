from __future__ import annotations

import copy
import os
import unittest
from unittest.mock import patch

from app.evidence_index import build_claim_evidence_index
from app.evidence_schema import create_evidence_item, text_source_hash
from app.formal_body import FormalBodyDocument
from app.generation.base import ReportGenerationResult


class VbpQualityGateTests(unittest.TestCase):
    @staticmethod
    def source_ref(text: str):
        return {
            "menu_code": "project_notice",
            "articleid": "notice-1",
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

    def evidence(self):
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
            value={"fact_type": "procurement_cycle", "label": "采购周期", "value": "采购周期为2年"},
            normalized_value={"fact_type": "procurement_cycle", "value": "P2Y"},
            source_ref=direct["source_ref"],
            derived_from=[direct["evidence_id"]],
            extractor_version="test-v1",
            mandatory=True,
        )
        return direct, derived

    def pack(self, *, vbp: bool = True):
        direct, derived = self.evidence()
        title = "带量采购项目公告" if vbp else "普通办事通知"
        return {
            "evidence_schema_version": 2,
            "evidence_items": [direct, derived],
            "primary_materials": [
                {
                    "menu_code": "project_notice",
                    "menu_name": "项目公告",
                    "articleid": "notice-1",
                    "title": title,
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
    def complete_markdown(body: str = "采购周期为2年。") -> str:
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

    def evaluate(self, markdown: str | None = None, pack: dict | None = None, index: dict | None = None):
        from app.vbp_quality_gate import evaluate_vbp_quality

        selected_pack = pack or self.pack()
        document = FormalBodyDocument(markdown=markdown or self.complete_markdown())
        selected_index = index or build_claim_evidence_index(document, selected_pack)
        return evaluate_vbp_quality(document, selected_pack, selected_index)

    def test_complete_vbp_report_with_ab_support_passes(self):
        result = self.evaluate()

        self.assertTrue(result["applicable"])
        self.assertTrue(result["passed"])
        self.assertEqual([], result["blocking_issues"])
        self.assertEqual("", result["primary_failure_code"])
        self.assertEqual(1.0, result["claim_ab_support_rate"])

    def test_non_vbp_material_bypasses_specialized_gate(self):
        pack = self.pack(vbp=False)
        result = self.evaluate(pack=pack)

        self.assertFalse(result["applicable"])
        self.assertTrue(result["passed"])
        self.assertEqual("not_applicable", result["status"])

    def test_report_ir_sections_and_claims_use_the_same_gate_contract(self):
        from app.vbp_quality_gate import evaluate_vbp_quality

        document = FormalBodyDocument(
            report_ir={
                "title": "项目公告分析",
                "lead_paragraphs": ["采购周期为2年。"],
                "sections": [
                    {"heading": "公告要点", "paragraphs": [], "highlights": [], "tables": []},
                    {"heading": "核心规则", "paragraphs": [], "highlights": [], "tables": []},
                    {"heading": "时间节点与操作步骤", "paragraphs": [], "highlights": [], "tables": []},
                    {"heading": "企业影响与风险提示", "paragraphs": [], "highlights": [], "tables": []},
                    {"heading": "企业操作建议", "paragraphs": [], "highlights": [], "tables": []},
                ],
            }
        )
        pack = self.pack()
        index = build_claim_evidence_index(document, pack)

        result = evaluate_vbp_quality(document, pack, index)

        self.assertTrue(result["passed"])
        self.assertEqual(1.0, result["claim_ab_support_rate"])

    def test_missing_required_section_blocks_delivery(self):
        markdown = self.complete_markdown().replace("## 企业操作建议", "")

        result = self.evaluate(markdown=markdown)

        self.assertFalse(result["passed"])
        self.assertIn("VBP_REQUIRED_SECTION_MISSING", result["blocking_issue_codes"])
        self.assertIn("recommendations", result["missing_section_ids"])

    def test_evidence_present_topic_must_have_supported_claim(self):
        pack = self.pack()
        other_text = "申报截止时间为2026年5月27日。"
        other = create_evidence_item(
            level="A",
            kind="article_text",
            value=other_text,
            source_ref={**self.source_ref(other_text), "source_hash": text_source_hash(other_text), "quote": other_text},
        )
        pack["evidence_items"].append(other)

        result = self.evaluate(markdown=self.complete_markdown(other_text), pack=pack)

        self.assertFalse(result["passed"])
        self.assertIn("VBP_REQUIRED_TOPIC_MISSING", result["blocking_issue_codes"])
        self.assertIn("procurement_cycle", result["missing_topic_ids"])

    def test_unsupported_claim_blocks_without_placeholder_workaround(self):
        markdown = self.complete_markdown("采购周期为2年。最高有效申报价999元，待核实。")

        result = self.evaluate(markdown=markdown)

        self.assertFalse(result["passed"])
        self.assertIn("VBP_UNSUPPORTED_CLAIM", result["blocking_issue_codes"])
        self.assertGreater(result["unsupported_claim_count"], 0)

    def test_injected_c_support_never_counts_as_fact_support(self):
        pack = self.pack()
        document = FormalBodyDocument(markdown=self.complete_markdown())
        index = build_claim_evidence_index(document, pack)
        index["claims"][0]["supported"] = True
        index["claims"][0]["support_levels"] = ["C"]
        index["claims"][0]["evidence_ids"] = ["c" * 64]
        index["claims"][0]["source_refs"] = []

        result = self.evaluate(pack=pack, index=index)

        self.assertFalse(result["passed"])
        self.assertIn("VBP_C_LEVEL_FACT_USED", result["blocking_issue_codes"])
        self.assertEqual(0, result["c_independent_support_count"])

    def test_missing_or_invalid_source_ref_blocks_delivery(self):
        pack = self.pack()
        document = FormalBodyDocument(markdown=self.complete_markdown())
        index = build_claim_evidence_index(document, pack)
        index["claims"][0]["source_refs"] = [{"menu_code": "project_notice", "articleid": ""}]

        result = self.evaluate(pack=pack, index=index)

        self.assertFalse(result["passed"])
        self.assertIn("VBP_FACT_SOURCE_REF_INVALID", result["blocking_issue_codes"])

    def test_fact_conflict_blocks_with_stable_primary_failure_code(self):
        pack = self.pack()
        pack["vbp_fact_diagnostics"] = [
            {"code": "VBP_FACT_CONFLICT", "fact_type": "procurement_cycle"}
        ]

        first = self.evaluate(pack=pack)
        second = self.evaluate(pack=pack)

        self.assertEqual(first, second)
        self.assertFalse(first["passed"])
        self.assertIn("VBP_RULE_CONFLICT", first["blocking_issue_codes"])
        self.assertEqual("VBP_RULE_CONFLICT", first["primary_failure_code"])

    def test_component_blocks_delivery_and_preserves_formal_body(self):
        from app import main as main_module
        from app.quality_gate import QualityGate

        markdown = self.complete_markdown("最高有效申报价999元。")
        generated = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown=markdown,
            quality_check={"passed": True, "issues": []},
            metadata={"status": "finished"},
        )
        with patch.dict(
            os.environ,
            {"ENABLE_EVIDENCE_INDEX": "true", "ENABLE_VBP_QUALITY_GATE": "true"},
        ):
            gated = QualityGate().run(generated, self.pack())

        self.assertEqual(markdown, gated.report_markdown)
        self.assertFalse(gated.quality_check["passed"])
        self.assertEqual("needs_manual_review", gated.metadata["status"])
        self.assertEqual("needs_manual_review", gated.metadata["quality_gate"]["deliverable_status"])
        self.assertIn("VBP_UNSUPPORTED_CLAIM", gated.metadata["quality_gate"]["blocking_issue_codes"])
        with patch.dict(os.environ, {"ENABLE_WORD_EXPORT": "true"}):
            normalized = main_module._normalize_analysis_run_schema(gated.to_legacy_result())
        self.assertEqual("VBP_UNSUPPORTED_CLAIM", normalized["primary_failure_code"])
        self.assertTrue(normalized["draft_word_export_available"])
        self.assertFalse(normalized["final_word_export_available"])
        self.assertFalse(normalized["deliverable"])
        self.assertTrue(normalized["needs_manual_review"])


if __name__ == "__main__":
    unittest.main()
