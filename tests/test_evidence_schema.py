from __future__ import annotations

import copy
import hashlib
import importlib
import json
import unittest


class EvidenceSchemaTests(unittest.TestCase):
    def schema(self):
        try:
            return importlib.import_module("app.evidence_schema")
        except ModuleNotFoundError:
            self.fail("EvidenceItem v2 capability is missing: app.evidence_schema")

    @staticmethod
    def article_ref(source_hash: str | None = None, **overrides):
        value = {
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
            "quote": "采购周期为两年",
            "source_hash": source_hash or hashlib.sha256("采购周期为两年".encode("utf-8")).hexdigest(),
            "region": None,
        }
        value.update(overrides)
        return value

    def make_a(self, *, value="采购周期为两年", mandatory=False, **overrides):
        schema = self.schema()
        data = {
            "level": "A",
            "kind": "article_text",
            "value": value,
            "normalized_value": None,
            "source_ref": self.article_ref(),
            "derived_from": [],
            "extractor_version": None,
            "mandatory": mandatory,
        }
        data.update(overrides)
        return schema.create_evidence_item(**data)

    def test_text_hash_normalization_only_changes_newlines_and_line_trailing_space(self):
        schema = self.schema()
        normalized = schema.normalize_text_for_hash("  第一行  \r\n第二 行\t\r第三行  ")
        self.assertEqual("  第一行\n第二 行\n第三行", normalized)
        expected = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        self.assertEqual(expected, schema.text_source_hash("  第一行  \r\n第二 行\t\r第三行  "))

    def test_attachment_hash_prefers_raw_bytes(self):
        schema = self.schema()
        raw = b"%PDF-raw-bytes"
        self.assertEqual(hashlib.sha256(raw).hexdigest(), schema.attachment_source_hash(raw, "ignored text"))

    def test_attachment_hash_uses_text_only_domain_when_bytes_are_unavailable(self):
        schema = self.schema()
        expected = hashlib.sha256("TEXT_ONLY\n正文\n第二行".encode("utf-8")).hexdigest()
        self.assertEqual(expected, schema.attachment_source_hash(None, "正文  \r\n第二行"))

    def test_source_ref_requires_compound_material_identity_and_lowercase_sha256(self):
        schema = self.schema()
        for field in ("menu_code", "articleid"):
            ref = self.article_ref()
            ref[field] = ""
            with self.subTest(field=field), self.assertRaises(schema.EvidenceValidationError):
                schema.validate_source_ref(ref)
        with self.assertRaises(schema.EvidenceValidationError):
            schema.validate_source_ref(self.article_ref(source_hash="A" * 64))
        with self.assertRaises(schema.EvidenceValidationError):
            schema.validate_source_ref(self.article_ref(source_hash="abc"))

    def test_source_ref_locations_are_one_based_and_unknown_locations_stay_null(self):
        schema = self.schema()
        clean = schema.validate_source_ref(self.article_ref())
        for field in ("page_no", "table_index", "row", "column", "cell_range", "region"):
            self.assertIsNone(clean[field])
        for field in ("page_no", "table_index", "row", "column"):
            with self.subTest(field=field), self.assertRaises(schema.EvidenceValidationError):
                schema.validate_source_ref(self.article_ref(**{field: 0}))

    def test_attachment_and_table_refs_keep_parent_identity_and_real_locator(self):
        schema = self.schema()
        ref = self.article_ref(
            attachment_id="att-1",
            filename="采购表.pdf",
            page_no=2,
            table_index=1,
            row=3,
            column=4,
            cell_range="R3C4:R3C4",
            quote="10.50元",
            region={"x0": 10, "y0": 20, "x1": 30, "y1": 40, "page_width": 100, "page_height": 200},
        )
        normalized = schema.validate_source_ref(ref, require_attachment=True)
        self.assertEqual("project_notice", normalized["menu_code"])
        self.assertEqual("article-1", normalized["articleid"])
        self.assertEqual("att-1", normalized["attachment_id"])
        self.assertEqual("R3C4:R3C4", normalized["cell_range"])
        with self.assertRaises(schema.EvidenceValidationError):
            schema.validate_source_ref({**ref, "attachment_id": None}, require_attachment=True)
        with self.assertRaises(schema.EvidenceValidationError):
            schema.validate_source_ref({**ref, "region": {"x0": 30, "y0": 20, "x1": 10, "y1": 40}})

    def test_article_evidence_rejects_fake_attachment_locator(self):
        schema = self.schema()
        with self.assertRaises(schema.EvidenceValidationError):
            self.make_a(source_ref=self.article_ref(attachment_id="att-1", filename="x.pdf", page_no=1))

    def test_evidence_id_is_stable_across_mapping_order_derived_order_and_mandatory(self):
        schema = self.schema()
        ref = self.article_ref()
        first = schema.create_evidence_item(
            level="C",
            kind="summary",
            value={"b": 2, "a": 1},
            source_ref=None,
            derived_from=["b" * 64, "a" * 64],
            extractor_version="summary-v1",
            mandatory=False,
        )
        second = schema.create_evidence_item(
            level="C",
            kind="summary",
            value={"a": 1, "b": 2},
            source_ref=None,
            derived_from=["a" * 64, "b" * 64],
            extractor_version="summary-v1",
            mandatory=True,
        )
        self.assertEqual(first["evidence_id"], second["evidence_id"])
        self.assertRegex(first["evidence_id"], r"^[0-9a-f]{64}$")
        self.assertNotIn("supports_fact", first)
        self.assertEqual(ref, self.article_ref())

    def test_a_item_roundtrip_is_valid_and_supports_fact(self):
        schema = self.schema()
        item = self.make_a(mandatory=True)
        pack = schema.validate_evidence_pack({"evidence_schema_version": 2, "evidence_items": [item]})
        self.assertEqual(2, pack["evidence_schema_version"])
        self.assertEqual(item["evidence_id"], pack["evidence_items"][0]["evidence_id"])
        self.assertTrue(schema.evidence_item_supports_fact(pack["evidence_items"][0], pack["evidence_items"]))

    def test_b_requires_normalized_value_extractor_and_only_a_dependencies(self):
        schema = self.schema()
        a_item = self.make_a()
        base = {
            "level": "B",
            "kind": "derived_fact",
            "value": "2年",
            "normalized_value": {"value": 2, "unit": "年"},
            "source_ref": self.article_ref(),
            "derived_from": [a_item["evidence_id"]],
            "extractor_version": "duration-v1",
            "mandatory": True,
        }
        b_item = schema.create_evidence_item(**base)
        pack = schema.validate_evidence_pack({"evidence_schema_version": 2, "evidence_items": [a_item, b_item]})
        self.assertTrue(schema.evidence_item_supports_fact(pack["evidence_items"][1], pack["evidence_items"]))
        for field, missing in (("normalized_value", None), ("extractor_version", ""), ("derived_from", [])):
            bad = dict(base)
            bad[field] = missing
            candidate = schema.create_evidence_item(**bad)
            with self.subTest(field=field), self.assertRaises(schema.EvidenceValidationError):
                schema.validate_evidence_pack({"evidence_schema_version": 2, "evidence_items": [a_item, candidate]})

    def test_b_rejects_missing_or_non_a_dependencies(self):
        schema = self.schema()
        c_item = schema.create_evidence_item(level="C", kind="summary", value="摘要", source_ref=None)
        b_item = schema.create_evidence_item(
            level="B",
            kind="derived_fact",
            value="两年",
            normalized_value="2年",
            source_ref=self.article_ref(),
            derived_from=[c_item["evidence_id"]],
            extractor_version="duration-v1",
        )
        with self.assertRaises(schema.EvidenceValidationError):
            schema.validate_evidence_pack({"evidence_schema_version": 2, "evidence_items": [c_item, b_item]})
        missing = {**b_item, "derived_from": ["f" * 64]}
        with self.assertRaises(schema.EvidenceValidationError):
            schema.validate_evidence_pack({"evidence_schema_version": 2, "evidence_items": [missing]})

    def test_c_never_supports_fact_even_when_it_contains_a_real_number(self):
        schema = self.schema()
        item = schema.create_evidence_item(
            level="C",
            kind="summary",
            value="最高申报价5元",
            source_ref=self.article_ref(),
            mandatory=True,
        )
        pack = schema.validate_evidence_pack({"evidence_schema_version": 2, "evidence_items": [item]})
        self.assertFalse(schema.evidence_item_supports_fact(item, pack["evidence_items"]))
        self.assertEqual([], schema.fact_support_values(pack))

    def test_duplicate_evidence_ids_are_rejected(self):
        schema = self.schema()
        item = self.make_a()
        with self.assertRaises(schema.EvidenceValidationError):
            schema.validate_evidence_pack({"evidence_schema_version": 2, "evidence_items": [item, copy.deepcopy(item)]})

    def test_unknown_schema_version_fails_closed(self):
        schema = self.schema()
        with self.assertRaises(schema.EvidenceSchemaVersionError):
            schema.read_evidence_pack({"evidence_schema_version": 3, "evidence_items": []})

    def test_missing_version_is_v1_read_only_adapter_without_input_mutation(self):
        schema = self.schema()
        legacy = {
            "pack_id": "legacy-pack",
            "primary_materials": [
                {
                    "menu_code": "project_notice",
                    "articleid": "article-1",
                    "title": "采购公告",
                    "content_text": "采购周期为两年。",
                    "key_facts": [{"name": "地区", "value": "山东"}],
                    "attachments": [
                        {
                            "articleattid": "att-1",
                            "filename": "附表.xlsx",
                            "summary": "包含企业和产品清单",
                            "warnings": ["展示用提示"],
                        }
                    ],
                }
            ],
            "generation_guidance": {"main_topic": "采购"},
        }
        original = copy.deepcopy(legacy)
        view = schema.read_evidence_pack(legacy)
        self.assertEqual(original, legacy)
        self.assertNotIn("evidence_schema_version", legacy)
        self.assertEqual(1, view["source_evidence_schema_version"])
        self.assertEqual(2, view["evidence_schema_version"])
        levels = {item["level"] for item in view["evidence_items"]}
        self.assertIn("A", levels)
        self.assertIn("C", levels)
        self.assertTrue(any(item["kind"] == "article_text" for item in view["evidence_items"]))
        self.assertTrue(any(item["kind"] == "summary" for item in view["evidence_items"]))

    def test_v1_summary_only_content_is_downgraded_to_c(self):
        schema = self.schema()
        view = schema.read_evidence_pack(
            {
                "pack_id": "legacy-summary-only",
                "primary_materials": [
                    {
                        "menu_code": "project_notice",
                        "articleid": "article-1",
                        "summary": "最高申报价5元",
                        "content_text": "",
                    }
                ],
            }
        )
        self.assertTrue(view["evidence_items"])
        self.assertTrue(all(item["level"] == "C" for item in view["evidence_items"]))
        self.assertEqual([], schema.fact_support_values(view))

    def test_v1_key_fact_is_b_only_when_traceable_to_article_text(self):
        schema = self.schema()
        view = schema.read_evidence_pack(
            {
                "primary_materials": [
                    {
                        "menu_code": "project_notice",
                        "articleid": "legacy-key-fact-1",
                        "content_text": "公告正文明确地区为山东。",
                        "key_facts": [
                            {"name": "地区", "value": "山东"},
                            {"name": "未披露价格", "value": "999元"},
                        ],
                    }
                ]
            }
        )

        traceable = next(
            item
            for item in view["evidence_items"]
            if isinstance(item.get("value"), dict) and item["value"].get("value") == "山东"
        )
        untraceable = next(
            item
            for item in view["evidence_items"]
            if isinstance(item.get("value"), dict) and item["value"].get("value") == "999元"
        )

        self.assertEqual("B", traceable["level"])
        self.assertTrue(traceable["derived_from"])
        self.assertEqual("C", untraceable["level"])
        self.assertNotIn(untraceable["value"], schema.fact_support_values(view))

    def test_article_source_hash_must_match_direct_value(self):
        module = self.schema()
        forged = module.create_evidence_item(
            level="A",
            kind="article_text",
            value="公告原文中的直接事实",
            source_ref=self.article_ref(
                quote="公告原文中的直接事实",
                source_hash="0" * 64,
            ),
        )

        with self.assertRaises(module.EvidenceValidationError):
            module.validate_evidence_pack(
                {
                    "evidence_schema_version": module.EVIDENCE_SCHEMA_VERSION,
                    "evidence_items": [forged],
                }
            )

    def test_b_source_hash_must_reference_one_of_its_a_dependencies(self):
        module = self.schema()
        direct = module.create_evidence_item(
            level="A",
            kind="article_text",
            value="原始价格为100元",
            source_ref=self.article_ref(
                quote="原始价格为100元",
                source_hash=module.text_source_hash("原始价格为100元"),
            ),
        )
        derived = module.create_evidence_item(
            level="B",
            kind="derived_fact",
            value={"price": 100},
            normalized_value={"price": 100, "currency": "CNY"},
            source_ref=self.article_ref(
                quote="原始价格为100元",
                source_hash=module.text_source_hash("另一份无关材料"),
            ),
            derived_from=[direct["evidence_id"]],
            extractor_version="vbp-1",
        )

        with self.assertRaises(module.EvidenceValidationError):
            module.validate_evidence_pack(
                {
                    "evidence_schema_version": module.EVIDENCE_SCHEMA_VERSION,
                    "evidence_items": [direct, derived],
                }
            )

    def test_canonical_json_is_deterministic_and_non_ascii(self):
        schema = self.schema()
        self.assertEqual(
            '{"a":"中文","b":2}',
            schema.canonical_json({"b": 2, "a": "中文"}),
        )
        self.assertEqual(
            hashlib.sha256('{"a":"中文","b":2}'.encode("utf-8")).hexdigest(),
            schema.canonical_sha256({"b": 2, "a": "中文"}),
        )


if __name__ == "__main__":
    unittest.main()
