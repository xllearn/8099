from __future__ import annotations

import copy
import importlib
import unittest

from app.compact_pack import stabilize_character_fields
from app.evidence_schema import create_evidence_item, text_source_hash


class S1EvidenceRegressionTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module("app.evidence_regression")
        except ModuleNotFoundError:
            self.fail("S1 offline evidence regression capability is missing: app.evidence_regression")

    @staticmethod
    def source_ref(text: str) -> dict[str, object]:
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
        }

    def packs(self):
        text = "采购周期为2年。"
        direct = create_evidence_item(
            level="A",
            kind="article_text",
            value=text,
            source_ref=self.source_ref(text),
            mandatory=True,
        )
        derived = create_evidence_item(
            level="B",
            kind="derived_fact",
            value={"fact_type": "procurement_cycle", "value": text},
            normalized_value={"fact_type": "procurement_cycle", "value": text},
            source_ref=direct["source_ref"],
            derived_from=[direct["evidence_id"]],
            extractor_version="test-v1",
            mandatory=True,
        )
        context = create_evidence_item(level="C", kind="summary", value="context only")
        full = {
            "pack_id": "pack_s1_regression",
            "evidence_schema_version": 2,
            "evidence_items": [direct, derived, context],
        }
        compact = {
            **full,
            "pack_variant": "dify_compact",
            "hard_limit_chars": 80_000,
        }
        stabilize_character_fields(compact)
        return full, compact

    def test_evaluator_reports_a_b_support_c_zero_and_mandatory_retention(self):
        full, compact = self.packs()
        result = self.module().evaluate_evidence_artifact(full, compact)
        self.assertTrue(result["passed"])
        self.assertEqual(1.0, result["a_b_support_rate"])
        self.assertEqual(0.0, result["c_independent_support_rate"])
        self.assertEqual(1.0, result["mandatory_evidence_retention"]["rate"])
        self.assertEqual([], result["errors"])

    def test_evaluator_fails_over_limit_and_missing_mandatory(self):
        full, compact = self.packs()
        compact["oversized_payload"] = "X" * 81_000
        compact["evidence_items"] = compact["evidence_items"][2:]
        result = self.module().evaluate_evidence_artifact(full, compact)
        self.assertFalse(result["passed"])
        self.assertIn("COMPACT_LIMIT_EXCEEDED", result["errors"])
        self.assertIn("MANDATORY_EVIDENCE_RETENTION_BELOW_90", result["errors"])

    def test_evaluator_is_deterministic(self):
        full, compact = self.packs()
        first = self.module().evaluate_evidence_artifact(full, compact)
        second = self.module().evaluate_evidence_artifact(full, compact)
        self.assertEqual(first["artifact_sha256"], second["artifact_sha256"])

    def test_evaluator_rejects_a_or_b_without_source_ref(self):
        unsupported = create_evidence_item(
            level="A",
            kind="article_text",
            value="采购周期为2年。",
            mandatory=True,
        )
        full = {
            "pack_id": "pack_missing_source_ref",
            "evidence_schema_version": 2,
            "evidence_items": [unsupported],
        }
        compact = {
            **full,
            "hard_limit_chars": 80_000,
            "compact_pack_chars": 1_000,
        }

        result = self.module().evaluate_evidence_artifact(full, compact)

        self.assertFalse(result["passed"])
        self.assertIn("EVIDENCE_SOURCE_REF_MISSING", result["errors"])

    def test_evaluator_cross_checks_compact_a_b_against_full_pack(self):
        full, compact = self.packs()
        full["evidence_items"] = full["evidence_items"][:1] + full["evidence_items"][2:]
        result = self.module().evaluate_evidence_artifact(full, compact)

        self.assertFalse(result["passed"])
        self.assertIn("A_B_SUPPORT_BELOW_100", result["errors"])
        self.assertEqual(1, result["a_b_supported_count"])

    def test_evaluator_recomputes_compact_chars_instead_of_trusting_declared_value(self):
        full, compact = self.packs()
        compact["oversized_payload"] = "X" * 81_000
        compact["compact_pack_chars"] = 1_000
        compact["final_dify_input_chars"] = 1_000
        result = self.module().evaluate_evidence_artifact(full, compact)

        self.assertFalse(result["passed"])
        self.assertGreater(result["compact_pack_chars"], 80_000)
        self.assertIn("COMPACT_LIMIT_EXCEEDED", result["errors"])

    def test_evaluator_rejects_vbp_fact_not_backed_by_compact_b_evidence(self):
        full, compact = self.packs()
        context = compact["evidence_items"][2]
        compact["vbp_facts"] = [
            {
                "evidence_id": context["evidence_id"],
                "fact_type": "procurement_cycle",
                "value": "context only",
                "normalized_value": "context only",
                "source_ref": None,
                "derived_from": [],
                "extractor_version": "test-v1",
                "mandatory": False,
            }
        ]
        result = self.module().evaluate_evidence_artifact(full, compact)

        self.assertFalse(result["passed"])
        self.assertIn("VBP_FACT_UNSUPPORTED", result["errors"])
        self.assertEqual(1, result["c_independently_supported_count"])

    def test_evaluator_rejects_full_vbp_fact_missing_from_compact_pack(self):
        full, compact = self.packs()
        derived = full["evidence_items"][1]
        full["vbp_facts"] = [
            {
                "evidence_id": derived["evidence_id"],
                "fact_type": "procurement_cycle",
                "value": derived["value"]["value"],
                "normalized_value": derived["normalized_value"]["value"],
                "source_ref": derived["source_ref"],
                "derived_from": derived["derived_from"],
                "extractor_version": derived["extractor_version"],
                "mandatory": derived["mandatory"],
            }
        ]
        compact["vbp_facts"] = []
        stabilize_character_fields(compact)

        result = self.module().evaluate_evidence_artifact(full, compact)

        self.assertFalse(result["passed"])
        self.assertIn("VBP_FACT_UNSUPPORTED", result["errors"])
        self.assertEqual(1, result["vbp_fact_count"])
        self.assertEqual(0, result["vbp_fact_supported_count"])

    def test_evaluator_recomputes_chars_from_raw_compact_payload(self):
        full, compact = self.packs()
        compact["evidence_items"][0]["transport_padding"] = "X" * 81_000
        result = self.module().evaluate_evidence_artifact(full, compact)

        self.assertFalse(result["passed"])
        self.assertGreater(result["compact_pack_chars"], 80_000)
        self.assertIn("COMPACT_LIMIT_EXCEEDED", result["errors"])

    def test_evaluator_rejects_disagreeing_declared_char_fields(self):
        full, compact = self.packs()
        compact["final_dify_input_chars"] = int(compact["compact_pack_chars"]) + 1
        result = self.module().evaluate_evidence_artifact(full, compact)

        self.assertFalse(result["passed"])
        self.assertIn("COMPACT_CHAR_COUNT_MISMATCH", result["errors"])

    def test_evaluator_requires_declared_character_fields(self):
        full, compact = self.packs()
        compact.pop("compact_pack_chars", None)
        compact.pop("final_dify_input_chars", None)

        result = self.module().evaluate_evidence_artifact(full, compact)

        self.assertFalse(result["passed"])
        self.assertIn("COMPACT_CHAR_COUNT_MISMATCH", result["errors"])

    def test_evaluator_rejects_unmatched_compact_a_b_when_vbp_fact_is_valid(self):
        full, compact = self.packs()
        derived = full["evidence_items"][1]
        fact = {
            "evidence_id": derived["evidence_id"],
            "fact_type": "procurement_cycle",
            "value": derived["value"]["value"],
            "normalized_value": derived["normalized_value"]["value"],
            "source_ref": derived["source_ref"],
            "derived_from": derived["derived_from"],
            "extractor_version": derived["extractor_version"],
            "mandatory": derived["mandatory"],
        }
        full["vbp_facts"] = [fact]
        compact["vbp_facts"] = [fact]
        compact["evidence_items"] = copy.deepcopy(compact["evidence_items"])
        compact["evidence_items"].append(
            create_evidence_item(
                level="A",
                kind="article_text",
                value="compact-only evidence",
                source_ref=self.source_ref("compact-only evidence"),
            )
        )
        stabilize_character_fields(compact)

        result = self.module().evaluate_evidence_artifact(full, compact)

        self.assertFalse(result["passed"])
        self.assertIn("A_B_EVIDENCE_UNSUPPORTED", result["errors"])
        self.assertLess(result["a_b_support_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
