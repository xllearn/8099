from __future__ import annotations

import hashlib
import os
import unittest
from unittest.mock import patch

from app.evidence_schema import create_evidence_item, text_source_hash
from app.formal_body import FormalBodyDocument
from app.generation.base import ReportGenerationResult


class EvidenceIndexTests(unittest.TestCase):
    @staticmethod
    def source_ref(text: str, **overrides):
        value = {
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
            "quote": text[:500],
            "source_hash": text_source_hash(text),
            "region": None,
        }
        value.update(overrides)
        return value

    def direct(self, text: str, *, level: str = "A", kind: str = "article_text"):
        return create_evidence_item(
            level=level,
            kind=kind,
            value=text,
            source_ref=self.source_ref(text) if level == "A" else None,
        )

    @staticmethod
    def pack(*items):
        return {
            "evidence_schema_version": 2,
            "evidence_items": list(items),
            "memory": "河南省采购周期为十年",
            "memory_items": [{"content": "最高申报价999元"}],
            "history": [{"report": "截止时间为2099年1月1日"}],
            "diagnostics": {"note": "采购金额1000万元"},
        }

    def test_claim_id_is_stable_across_carriers_nfkc_and_whitespace(self):
        from app.evidence_index import build_claim_evidence_index

        evidence = self.direct("采购周期为2年。")
        markdown = build_claim_evidence_index(
            FormalBodyDocument(markdown="# 核心规则\n\n采购周期为２年。"),
            self.pack(evidence),
        )
        report_ir = build_claim_evidence_index(
            FormalBodyDocument(
                report_ir={
                    "title": "报告",
                    "lead_paragraphs": ["采购周期为 2 年。"],
                    "sections": [],
                }
            ),
            self.pack(evidence),
        )

        self.assertEqual(markdown["claims"][0]["claim_id"], report_ir["claims"][0]["claim_id"])
        self.assertRegex(markdown["claims"][0]["claim_id"], r"^[0-9a-f]{64}$")
        self.assertTrue(markdown["claims"][0]["supported"])

    def test_only_validated_a_and_b_evidence_can_support_claims(self):
        from app.evidence_index import build_claim_evidence_index

        direct = self.direct("采购周期为2年。")
        derived = create_evidence_item(
            level="B",
            kind="derived_fact",
            value={"fact_type": "procurement_cycle", "value": "采购周期为2年"},
            normalized_value={"fact_type": "procurement_cycle", "value": "P2Y"},
            source_ref=direct["source_ref"],
            derived_from=[direct["evidence_id"]],
            extractor_version="test-v1",
        )
        c_only = create_evidence_item(level="C", kind="summary", value="最高申报价999元")
        document = FormalBodyDocument(
            markdown="# 报告\n\n采购周期为2年。\n\n最高申报价999元。\n\n截止时间为2099年1月1日。"
        )

        index = build_claim_evidence_index(document, self.pack(direct, derived, c_only))
        by_text = {claim["text"]: claim for claim in index["claims"]}

        self.assertTrue(by_text["采购周期为2年"]["supported"])
        self.assertEqual({"A", "B"}, set(by_text["采购周期为2年"]["support_levels"]))
        self.assertFalse(by_text["最高申报价999元"]["supported"])
        self.assertFalse(by_text["截止时间为2099年1月1日"]["supported"])
        self.assertEqual(0, index["metrics"]["c_independent_support_count"])

    def test_numeric_date_institution_and_table_cell_require_exact_context(self):
        from app.evidence_index import build_claim_evidence_index

        article = self.direct("山东省医疗保障局明确申报截止时间为2026年5月27日。")
        table_text = "最高有效申报价100元"
        table = create_evidence_item(
            level="A",
            kind="table_cell",
            value=table_text,
            source_ref=self.source_ref(
                table_text,
                attachment_id="att-1",
                filename="价格表.pdf",
                page_no=2,
                table_index=1,
                row=3,
                column=2,
                cell_range="R3C2",
            ),
        )
        document = FormalBodyDocument(
            report_ir={
                "title": "项目公告分析",
                "lead_paragraphs": ["山东省医疗保障局明确申报截止时间为2026年5月27日。"],
                "sections": [
                    {
                        "heading": "报价规则",
                        "paragraphs": [],
                        "highlights": [],
                        "tables": [{"title": "报价", "headers": ["规则"], "rows": [["最高有效申报价100元"]]}],
                    }
                ],
            }
        )

        index = build_claim_evidence_index(document, self.pack(article, table))

        self.assertTrue(all(claim["supported"] for claim in index["claims"]))
        table_claim = next(claim for claim in index["claims"] if "100元" in claim["text"])
        self.assertEqual(table["evidence_id"], table_claim["evidence_ids"][0])
        self.assertEqual("R3C2", table_claim["source_refs"][0]["cell_range"])

    def test_near_match_that_expands_entity_or_number_is_unsupported(self):
        from app.evidence_index import build_claim_evidence_index

        evidence = self.direct("山东省最高有效申报价为100元。")
        document = FormalBodyDocument(
            markdown="# 报价规则\n\n山东省和河南省最高有效申报价为1000元。"
        )

        index = build_claim_evidence_index(document, self.pack(evidence))

        self.assertEqual(1, index["metrics"]["unsupported_claim_count"])
        self.assertFalse(index["claims"][0]["supported"])
        self.assertEqual([], index["claims"][0]["evidence_ids"])

    def test_markdown_table_header_is_not_a_claim_and_row_keeps_header_context(self):
        from app.evidence_index import build_claim_evidence_index

        evidence = self.direct("最高有效申报价100元")
        document = FormalBodyDocument(
            markdown=(
                "# 报价规则\n\n"
                "| 项目 | 最高有效申报价 |\n"
                "| --- | ---: |\n"
                "| A组 | 100元 |"
            )
        )

        index = build_claim_evidence_index(document, self.pack(evidence))

        self.assertEqual(1, len(index["claims"]))
        self.assertIn("最高有效申报价: 100元", index["claims"][0]["text"])
        self.assertTrue(index["claims"][0]["supported"])

    def test_duplicate_claim_text_is_deduplicated_with_all_locations(self):
        from app.evidence_index import build_claim_evidence_index

        evidence = self.direct("采购周期为2年。")
        document = FormalBodyDocument(
            markdown="# 规则\n\n采购周期为2年。",
            report_ir={"title": "报告", "lead_paragraphs": ["采购周期为2年。"], "sections": []},
        )

        index = build_claim_evidence_index(document, self.pack(evidence))

        self.assertEqual(1, len(index["claims"]))
        self.assertEqual(2, len(index["claims"][0]["locations"]))

    def test_invalid_evidence_pack_fails_closed_without_using_auxiliary_sources(self):
        from app.evidence_index import EvidenceIndexError, build_claim_evidence_index

        forged = self.direct("采购周期为2年。")
        forged["source_ref"]["source_hash"] = hashlib.sha256(b"forged").hexdigest()

        with self.assertRaises(EvidenceIndexError):
            build_claim_evidence_index(
                FormalBodyDocument(markdown="# 规则\n\n采购周期为2年。"),
                self.pack(forged),
            )

    def test_index_metadata_is_versioned_and_support_rate_is_deterministic(self):
        from app.evidence_index import CLAIM_INDEX_SCHEMA_VERSION, MATCHER_VERSION, build_claim_evidence_index

        evidence = self.direct("采购周期为2年。")
        document = FormalBodyDocument(markdown="# 规则\n\n采购周期为2年。\n\n最高申报价999元。")

        first = build_claim_evidence_index(document, self.pack(evidence))
        second = build_claim_evidence_index(document, self.pack(evidence))

        self.assertEqual(first, second)
        self.assertEqual(CLAIM_INDEX_SCHEMA_VERSION, first["schema_version"])
        self.assertEqual(MATCHER_VERSION, first["matcher_version"])
        self.assertEqual(2, first["metrics"]["claim_count"])
        self.assertEqual(0.5, first["metrics"]["ab_support_rate"])

    def test_quality_component_only_attaches_index_when_feature_is_enabled(self):
        from app.quality_gate import ClaimEvidenceIndexComponent

        evidence = self.direct("采购周期为2年。")
        result = ReportGenerationResult(
            success=True,
            provider="dify",
            report_markdown="# 规则\n\n采购周期为2年。",
            metadata={"sentinel": "unchanged"},
        )
        component = ClaimEvidenceIndexComponent()

        with patch.dict(os.environ, {"ENABLE_EVIDENCE_INDEX": "false"}):
            disabled = component.run(result, self.pack(evidence))
        with patch.dict(os.environ, {"ENABLE_EVIDENCE_INDEX": "true"}):
            enabled = component.run(result, self.pack(evidence))

        self.assertIs(disabled, result)
        self.assertEqual(result.report_markdown, enabled.report_markdown)
        self.assertEqual("unchanged", enabled.metadata["sentinel"])
        self.assertTrue(enabled.metadata["claim_evidence_index"]["claims"][0]["supported"])
        self.assertEqual("", enabled.metadata["claim_index_error"])


if __name__ == "__main__":
    unittest.main()
