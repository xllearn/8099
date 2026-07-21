from __future__ import annotations

import copy
import importlib
import json
import os
import unittest
from unittest.mock import patch

from app.evidence_schema import (
    EvidenceValidationError,
    create_evidence_item,
    evidence_item_supports_fact,
    read_evidence_pack,
    text_source_hash,
)


class VbpFactExtractionTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module("app.vbp_facts")
        except ModuleNotFoundError:
            self.fail("S1c VBP fact extraction capability is missing: app.vbp_facts")

    @staticmethod
    def source_ref(text: str, *, articleid: str = "article-1") -> dict[str, object]:
        return {
            "menu_code": "project_notice",
            "articleid": articleid,
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

    def article_text(self, text: str, *, articleid: str = "article-1", mandatory: bool = True):
        return create_evidence_item(
            level="A",
            kind="article_text",
            value=text,
            source_ref=self.source_ref(text, articleid=articleid),
            mandatory=mandatory,
        )

    def article_field(self, name: str, value: str):
        return create_evidence_item(
            level="A",
            kind="article_field",
            value={"name": name, "value": value},
            source_ref=self.source_ref(value),
            mandatory=True,
        )

    @staticmethod
    def pack(items: list[dict[str, object]]) -> dict[str, object]:
        return {
            "pack_id": "pack_s1c_vbp_facts",
            "evidence_schema_version": 2,
            "primary_materials": [
                {
                    "menu_code": "project_notice",
                    "articleid": "article-1",
                    "menu_name": "项目公告",
                    "title": "医用耗材集中带量采购文件公告",
                }
            ],
            "auxiliary_materials": [],
            "evidence_items": items,
        }

    def test_primary_a_topic_sentence_becomes_traceable_b_fact(self):
        source = self.article_text("本次采购周期为2年。其他规则按公告执行。")
        result = self.module().enrich_vbp_facts(self.pack([source]))

        facts = [item for item in result["vbp_facts"] if item["fact_type"] == "procurement_cycle"]
        self.assertEqual(1, len(facts))
        derived = next(item for item in result["evidence_items"] if item["evidence_id"] == facts[0]["evidence_id"])
        self.assertEqual("B", derived["level"])
        self.assertEqual([source["evidence_id"]], derived["derived_from"])
        self.assertEqual(source["source_ref"]["source_hash"], derived["source_ref"]["source_hash"])
        self.assertTrue(derived["mandatory"])
        self.assertTrue(evidence_item_supports_fact(derived, result["evidence_items"]))

    def test_repeated_identical_fact_is_deduplicated_deterministically(self):
        source = self.article_text("采购周期为2年。采购周期为2年。")
        first = self.module().enrich_vbp_facts(self.pack([source]))
        second = self.module().enrich_vbp_facts(copy.deepcopy(self.pack([source])))

        cycle_facts = [item for item in first["vbp_facts"] if item["fact_type"] == "procurement_cycle"]
        self.assertEqual(1, len(cycle_facts))
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True),
            json.dumps(second, ensure_ascii=False, sort_keys=True),
        )

    def test_conflicting_scalar_facts_are_diagnostic_only(self):
        source = self.article_text("采购周期为2年。采购周期为3年。")
        result = self.module().enrich_vbp_facts(self.pack([source]))

        self.assertFalse(any(item["fact_type"] == "procurement_cycle" for item in result["vbp_facts"]))
        conflicts = [item for item in result["vbp_fact_diagnostics"] if item["code"] == "VBP_FACT_CONFLICT"]
        self.assertEqual(1, len(conflicts))
        self.assertEqual("procurement_cycle", conflicts[0]["fact_type"])
        self.assertGreaterEqual(conflicts[0]["candidate_count"], 2)
        self.assertFalse(
            any(
                item.get("level") == "B"
                and (item.get("value") or {}).get("fact_type") == "procurement_cycle"
                for item in result["evidence_items"]
            )
        )

    def test_multi_value_rule_topics_are_not_misclassified_as_scalar_conflicts(self):
        source = self.article_text("报价要求为企业按产品申报。报价要求为同组不得高于限价。")
        result = self.module().enrich_vbp_facts(self.pack([source]))
        facts = [item for item in result["vbp_facts"] if item["fact_type"] == "quote_rules"]
        self.assertEqual(2, len(facts))
        self.assertFalse(any(item["fact_type"] == "quote_rules" for item in result["vbp_fact_diagnostics"]))

    def test_primary_structured_fields_produce_region_institution_and_date(self):
        items = [
            self.article_field("地区", "山东省"),
            self.article_field("发布机构", "山东省医疗保障局"),
            self.article_field("发布时间", "2026-05-22 09:28:26"),
        ]
        result = self.module().enrich_vbp_facts(self.pack(items))
        by_type = {item["fact_type"]: item for item in result["vbp_facts"]}

        self.assertEqual("山东省", by_type["region"]["normalized_value"])
        self.assertEqual("山东省医疗保障局", by_type["institution"]["normalized_value"])
        self.assertEqual("2026-05-22", by_type["publication_date"]["normalized_value"])

    def test_auxiliary_a_and_c_memory_history_diagnostics_never_create_facts(self):
        auxiliary_text = "采购周期为9年。最高有效申报价为0.01元。"
        auxiliary = self.article_text(auxiliary_text, articleid="aux-1", mandatory=False)
        context = create_evidence_item(level="C", kind="summary", value="采购周期为8年")
        pack = self.pack([auxiliary, context])
        pack["auxiliary_materials"] = [
            {"menu_code": "project_notice", "articleid": "aux-1", "material_role": "auxiliary"}
        ]
        pack["memory"] = "采购周期为7年"
        pack["history"] = [{"report": "采购周期为6年"}]
        pack["diagnostics"] = {"message": "采购周期为5年"}

        result = self.module().enrich_vbp_facts(pack)
        self.assertEqual([], result["vbp_facts"])
        self.assertEqual([auxiliary, context], result["evidence_items"])

    def test_non_vbp_primary_material_does_not_extract_topic_facts(self):
        source = self.article_text("采购周期为2年。")
        pack = self.pack([source])
        pack["primary_materials"][0]["title"] = "关于系统维护的普通通知"
        pack["primary_materials"][0]["category"] = "运维通知"
        result = self.module().enrich_vbp_facts(pack)
        self.assertEqual([], result["vbp_facts"])

    def test_b_source_ref_quote_preserves_exact_original_span(self):
        text = "本次采购周期为２年。"
        source = self.article_text(text)
        result = self.module().enrich_vbp_facts(self.pack([source]))
        fact = next(item for item in result["vbp_facts"] if item["fact_type"] == "procurement_cycle")
        self.assertEqual(text, fact["source_ref"]["quote"])
        self.assertIn(fact["source_ref"]["quote"], text)

    def test_invalid_calendar_date_is_diagnostic_only(self):
        source = self.article_field("发布时间", "2026-02-31 09:00:00")
        result = self.module().enrich_vbp_facts(self.pack([source]))
        self.assertFalse(any(item["fact_type"] == "publication_date" for item in result["vbp_facts"]))
        self.assertTrue(any(item["code"] == "VBP_FACT_INVALID_DATE" for item in result["vbp_fact_diagnostics"]))

    def test_enrichment_is_idempotent(self):
        source = self.article_text("采购周期为2年。")
        first = self.module().enrich_vbp_facts(self.pack([source]))
        second = self.module().enrich_vbp_facts(first)
        self.assertEqual(first, second)

    def test_v1_adapter_does_not_add_vbp_fields_when_feature_is_not_applied(self):
        adapted = read_evidence_pack(
            {
                "primary_materials": [
                    {
                        "menu_code": "project_notice",
                        "articleid": "article-1",
                        "material_role": "primary",
                        "title": "集中带量采购公告",
                        "content_text": "采购周期为2年。",
                        "areaname": "山东省",
                        "publicorg": "山东省医疗保障局",
                        "audittime": "2026-05-22 09:28:26",
                    }
                ],
                "auxiliary_materials": [],
            }
        )
        field_names = {
            str((item.get("value") or {}).get("name") or "")
            for item in adapted["evidence_items"]
            if item.get("kind") == "article_field"
        }
        self.assertEqual({"标题"}, field_names)

    def test_invalid_a_source_hash_is_rejected_before_fact_extraction(self):
        source = self.article_text("采购周期为2年。")
        source["source_ref"]["source_hash"] = "0" * 64
        source["evidence_id"] = create_evidence_item(
            level="A",
            kind="article_text",
            value=source["value"],
            source_ref=source["source_ref"],
            mandatory=True,
        )["evidence_id"]

        with self.assertRaises(EvidenceValidationError):
            self.module().enrich_vbp_facts(self.pack([source]))

    def test_feature_switch_defaults_false_and_parses_explicit_true(self):
        module = self.module()
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(module.vbp_fact_extraction_enabled())
        with patch.dict(os.environ, {"ENABLE_VBP_FACT_EXTRACTION": "true"}, clear=True):
            self.assertTrue(module.vbp_fact_extraction_enabled())

    def test_placeholder_reference_sentence_does_not_create_fact(self):
        source = self.article_text("采购周期详见采购文件。")
        result = self.module().enrich_vbp_facts(self.pack([source]))

        self.assertFalse(any(item["fact_type"] == "procurement_cycle" for item in result["vbp_facts"]))

    def test_equivalent_scalar_phrases_share_one_normalized_value(self):
        source = self.article_text("采购周期为2年。采购期限为2年。")
        result = self.module().enrich_vbp_facts(self.pack([source]))

        facts = [item for item in result["vbp_facts"] if item["fact_type"] == "procurement_cycle"]
        self.assertEqual(1, len(facts))
        self.assertEqual("P2Y", facts[0]["normalized_value"])
        self.assertFalse(any(item.get("fact_type") == "procurement_cycle" for item in result["vbp_fact_diagnostics"]))

    def test_primary_attachment_text_can_create_traceable_fact(self):
        text = "采购周期为3年。"
        source_ref = {
            **self.source_ref(text),
            "attachment_id": "attachment-1",
            "filename": "rules.pdf",
        }
        source = create_evidence_item(
            level="A",
            kind="attachment_text",
            value=text,
            source_ref=source_ref,
            mandatory=True,
        )

        result = self.module().enrich_vbp_facts(self.pack([source]))
        fact = next(item for item in result["vbp_facts"] if item["fact_type"] == "procurement_cycle")

        self.assertEqual([source["evidence_id"]], fact["derived_from"])
        self.assertEqual("attachment-1", fact["source_ref"]["attachment_id"])

    def test_same_table_row_label_and_value_create_one_traceable_fact(self):
        source_hash = text_source_hash("最高有效申报价100元")

        def table_cell(value: str, column: int):
            return create_evidence_item(
                level="A",
                kind="table_cell",
                value=value,
                source_ref={
                    "menu_code": "project_notice",
                    "articleid": "article-1",
                    "attachment_id": "attachment-1",
                    "filename": "price.pdf",
                    "page_no": 1,
                    "sheet_name": None,
                    "table_index": 1,
                    "row": 2,
                    "column": column,
                    "cell_range": f"R2C{column}",
                    "quote": value,
                    "source_hash": source_hash,
                    "region": None,
                },
                mandatory=False,
            )

        label = table_cell("最高有效申报价", 1)
        value = table_cell("100元", 2)
        result = self.module().enrich_vbp_facts(self.pack([label, value]))

        fact = next(item for item in result["vbp_facts"] if item["fact_type"] == "group_price_table")
        self.assertEqual({label["evidence_id"], value["evidence_id"]}, set(fact["derived_from"]))
        self.assertEqual(2, len(fact["derived_from"]))


if __name__ == "__main__":
    unittest.main()
