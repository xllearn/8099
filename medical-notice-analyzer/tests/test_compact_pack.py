from __future__ import annotations

import copy
import importlib
import json
import os
import unittest
from unittest.mock import patch

import app.main as main_module
from app.diagnostics import build_pack_diagnostics
from app.evidence_schema import create_evidence_item, text_source_hash
from fastapi import HTTPException


class CompactPackPreservationTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module("app.compact_pack")
        except ModuleNotFoundError:
            self.fail("S1c compact preservation capability is missing: app.compact_pack")

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
            "quote": text[:500],
            "source_hash": text_source_hash(text),
            "region": None,
        }

    def direct_item(self, text: str, *, mandatory: bool = False):
        return create_evidence_item(
            level="A",
            kind="article_text",
            value=text,
            source_ref=self.source_ref(text),
            mandatory=mandatory,
        )

    @staticmethod
    def base_pack(items: list[dict[str, object]]) -> dict[str, object]:
        body = "采购周期为2年。"
        return {
            "pack_id": "pack_s1c_compact",
            "primary_materials": [
                {
                    "menu_code": "project_notice",
                    "articleid": "article-1",
                    "title": "集中带量采购公告",
                    "content_text": body,
                    "content_text_length": len(body),
                    "attachments": [],
                }
            ],
            "auxiliary_materials": [],
            "combined_key_facts": [],
            "report_focus": [],
            "warnings": [],
            "generation_guidance": {},
            "evidence_schema_version": 2,
            "evidence_items": items,
        }

    def test_secondary_plan_uses_exact_boundaries_and_targets(self):
        plan = self.module().secondary_compression_plan
        self.assertFalse(plan(79_999)["required"])
        self.assertFalse(plan(80_000)["required"])
        self.assertEqual(("light", 78_000, 79_500), self._window(plan(80_001)))
        self.assertEqual(("light", 78_000, 79_500), self._window(plan(90_000)))
        self.assertEqual(("medium", 70_000, 79_000), self._window(plan(90_001)))
        self.assertEqual(("medium", 70_000, 79_000), self._window(plan(150_000)))
        self.assertEqual(("strong", 75_000, 79_000), self._window(plan(150_001)))
        self.assertEqual(("strong", 75_000, 79_000), self._window(plan(252_822)))

    @staticmethod
    def _window(plan: dict[str, object]) -> tuple[object, object, object]:
        return plan["tier"], plan["target_floor_chars"], plan["target_ceiling_chars"]

    def test_feature_enabled_does_not_change_under_limit_first_stage_result(self):
        source = self.direct_item("采购周期为2年。", mandatory=True)
        pack = self.base_pack([source])
        with patch.dict(os.environ, {"ENABLE_VBP_COMPACT_PRESERVATION": "false"}, clear=False):
            baseline = main_module._compact_evidence_pack_for_dify(copy.deepcopy(pack))
        with patch.dict(os.environ, {"ENABLE_VBP_COMPACT_PRESERVATION": "true"}, clear=False):
            actual = main_module._compact_evidence_pack_for_dify(copy.deepcopy(pack))

        self.assertLessEqual(len(json.dumps(baseline, ensure_ascii=False, sort_keys=True)), 80_000)
        self.assertEqual(baseline, actual)
        self.assertFalse(actual.get("second_pass_compression_applied", False))

    def test_final_character_fields_match_the_serialized_compact_pack(self):
        pack = {
            "pack_id": "probe",
            "primary_materials": [
                {
                    "menu_code": "project_notice",
                    "articleid": "a1",
                    "title": "t",
                    "content_text": "X",
                    "content_text_length": 1,
                    "attachments": [],
                }
            ],
            "auxiliary_materials": [],
            "combined_key_facts": [],
            "report_focus": [],
            "warnings": [],
            "generation_guidance": {},
        }

        with patch.dict(os.environ, {"ENABLE_VBP_COMPACT_PRESERVATION": "true"}, clear=False):
            compact = main_module._compact_evidence_pack_for_dify(pack)

        serialized_chars = len(json.dumps(compact, ensure_ascii=False, sort_keys=True))
        self.assertEqual(serialized_chars, compact["compact_pack_chars"])
        self.assertEqual(serialized_chars, compact["final_dify_input_chars"])

    def test_optional_c_is_removed_before_a_and_mandatory_retention_is_100_percent(self):
        mandatory = self.direct_item("采购周期为2年。", mandatory=True)
        optional_a = self.direct_item("最高有效申报价为10元。")
        context = [
            create_evidence_item(level="C", kind="summary", value=f"context-{index}-" + "C" * 8_000)
            for index in range(14)
        ]
        pack = self.base_pack([mandatory, optional_a, *context])

        with patch.dict(os.environ, {"ENABLE_VBP_COMPACT_PRESERVATION": "true"}, clear=False):
            compact = main_module._compact_evidence_pack_for_dify(pack)

        retained = {item["evidence_id"] for item in compact["evidence_items"]}
        self.assertLessEqual(compact["compact_pack_chars"], 80_000)
        self.assertTrue(compact["secondary_compression"])
        self.assertEqual(1.0, compact["mandatory_evidence_retention"]["rate"])
        self.assertIn(mandatory["evidence_id"], retained)
        self.assertIn(optional_a["evidence_id"], retained)
        self.assertLess(sum(item["level"] == "C" for item in compact["evidence_items"]), len(context))

    def test_b_fact_and_its_a_dependency_are_protected(self):
        source = self.direct_item("采购周期为2年。")
        derived = create_evidence_item(
            level="B",
            kind="derived_fact",
            value={"fact_type": "procurement_cycle", "value": "采购周期为2年"},
            normalized_value={"fact_type": "procurement_cycle", "value": "采购周期为2年"},
            source_ref=source["source_ref"],
            derived_from=[source["evidence_id"]],
            extractor_version="test-v1",
        )
        context = [
            create_evidence_item(level="C", kind="summary", value=f"context-{index}-" + "C" * 10_000)
            for index in range(12)
        ]
        pack = self.base_pack([source, derived, *context])

        reduced = self.module().reduce_optional_evidence(copy.deepcopy(pack), target_ceiling_chars=20_000)
        retained = {item["evidence_id"] for item in reduced["evidence_items"]}
        self.assertIn(source["evidence_id"], retained)
        self.assertIn(derived["evidence_id"], retained)

    def test_mandatory_evidence_over_limit_has_explicit_failure_code(self):
        mandatory = self.direct_item("M" * 82_000, mandatory=True)
        pack = self.base_pack([mandatory])
        with patch.dict(os.environ, {"ENABLE_VBP_COMPACT_PRESERVATION": "true"}, clear=False):
            with self.assertRaises(self.module().CompactPolicyError) as caught:
                main_module._compact_evidence_pack_for_dify(pack)
        self.assertEqual("COMPACT_MANDATORY_FIELDS_OVER_LIMIT", caught.exception.code)

    def test_mandatory_b_dependency_closure_over_limit_has_explicit_failure_code(self):
        source = self.direct_item("D" * 80_000, mandatory=False)
        derived = create_evidence_item(
            level="B",
            kind="derived_fact",
            value={"fact_type": "procurement_cycle", "value": "采购周期为2年"},
            normalized_value={"fact_type": "procurement_cycle", "value": "采购周期为2年"},
            source_ref=source["source_ref"],
            derived_from=[source["evidence_id"]],
            extractor_version="test-v1",
            mandatory=True,
        )
        pack = self.base_pack([source, derived])
        with patch.dict(os.environ, {"ENABLE_VBP_COMPACT_PRESERVATION": "true"}, clear=False):
            with self.assertRaises(self.module().CompactPolicyError) as caught:
                main_module._compact_evidence_pack_for_dify(pack)
        self.assertEqual("COMPACT_MANDATORY_FIELDS_OVER_LIMIT", caught.exception.code)

    def test_pack_endpoint_exposes_explicit_mandatory_overflow_code(self):
        mandatory = self.direct_item("M" * 82_000, mandatory=True)
        pack = self.base_pack([mandatory])
        with patch.dict(os.environ, {"ENABLE_VBP_COMPACT_PRESERVATION": "true"}, clear=False), patch.object(
            main_module,
            "_read_database_evidence_pack",
            return_value=pack,
        ):
            with self.assertRaises(HTTPException) as caught:
                main_module.get_analysis_pack("pack_s1c_overflow")
        self.assertEqual(422, caught.exception.status_code)
        self.assertEqual("COMPACT_MANDATORY_FIELDS_OVER_LIMIT", caught.exception.detail["code"])

    def test_pack_diagnostics_exposes_secondary_and_retention_metrics(self):
        compact = {
            "primary_materials": [],
            "auxiliary_materials": [],
            "compact_pack_chars": 78_500,
            "final_dify_input_chars": 78_500,
            "secondary_compression": True,
            "second_pass_tier": "light",
            "second_pass_first_pass_chars": 84_597,
            "mandatory_evidence_retention": {
                "version": "8099.mandatory-evidence-retention/v1",
                "total": 10,
                "retained": 10,
                "missing_ids": [],
                "rate": 1.0,
                "status": "measured",
            },
        }
        diagnostics = build_pack_diagnostics({"primary_materials": [], "auxiliary_materials": []}, compact)
        self.assertTrue(diagnostics["secondary_compression"])
        self.assertEqual("light", diagnostics["secondary_compression_tier"])
        self.assertEqual(84_597, diagnostics["secondary_compression_first_pass_chars"])
        self.assertEqual(1.0, diagnostics["mandatory_evidence_retention_rate"])

    def test_optional_evidence_reduction_is_deterministic(self):
        mandatory = self.direct_item("采购周期为2年。", mandatory=True)
        context = [
            create_evidence_item(level="C", kind="summary", value=f"context-{index}-" + "C" * 4_000)
            for index in range(20)
        ]
        pack = self.base_pack([mandatory, *context])
        module = self.module()
        first = module.reduce_optional_evidence(copy.deepcopy(pack), target_ceiling_chars=25_000)
        second = module.reduce_optional_evidence(copy.deepcopy(pack), target_ceiling_chars=25_000)
        self.assertEqual(
            module.canonical_sha256(first),
            module.canonical_sha256(second),
        )


    def test_structure_is_compacted_before_optional_evidence_is_maximized(self):
        mandatory = self.direct_item("The procurement cycle is one year.", mandatory=True)
        context = [
            create_evidence_item(level="C", kind="summary", value=f"context-{index}-" + "C" * 500)
            for index in range(120)
        ]
        pack = self.base_pack([mandatory, *context])
        pack["primary_materials"][0]["attachment_summaries"] = [
            {
                "articleattid": "attachment-1",
                "filename": "large-table.pdf",
                "summary": "S" * 4_000,
                "key_facts": ["fact-" + "K" * 240 for _ in range(12)],
                "important_sections": ["section-" + "I" * 320 for _ in range(8)],
                "table_summaries": [
                    {
                        "sheet_name": f"page-{index + 1}",
                        "rows": 40,
                        "columns_count": 12,
                        "headers": [f"header-{column}-" + "H" * 40 for column in range(12)],
                        "key_columns": [f"header-{column}" for column in range(8)],
                        "field_stats": {
                            f"field-{column}": {"non_empty": 40, "sample": "V" * 80}
                            for column in range(12)
                        },
                        "summary": "T" * 240,
                        "business_value": "B" * 180,
                    }
                    for index in range(120)
                ],
            }
        ]

        with patch.dict(os.environ, {"ENABLE_VBP_COMPACT_PRESERVATION": "true"}, clear=False):
            compact = main_module._compact_evidence_pack_for_dify(pack)

        retained = {item["evidence_id"] for item in compact["evidence_items"]}
        self.assertGreaterEqual(compact["compact_pack_chars"], 75_000)
        self.assertLessEqual(compact["compact_pack_chars"], 79_000)
        self.assertEqual(1.0, compact["mandatory_evidence_retention"]["rate"])
        self.assertIn(mandatory["evidence_id"], retained)
        self.assertGreater(sum(item["level"] == "C" for item in compact["evidence_items"]), 0)

    def test_run_diagnostics_returns_explicit_compact_failure_code(self):
        mandatory = self.direct_item("M" * 82_000, mandatory=True)
        pack = self.base_pack([mandatory])
        record = {"run_id": "run-compact-overflow", "pack_id": pack["pack_id"], "status": "failed"}

        with patch.dict(os.environ, {"ENABLE_VBP_COMPACT_PRESERVATION": "true"}, clear=False), patch.object(
            main_module,
            "_read_analysis_run",
            return_value=record,
        ), patch.object(
            main_module,
            "_maybe_finalize_timed_out_analysis_run",
            return_value=record,
        ), patch.object(main_module, "_read_database_evidence_pack", return_value=pack):
            response = main_module.get_analysis_run_diagnostics(record["run_id"])

        self.assertEqual(422, response.status_code)
        body = json.loads(response.body)
        self.assertEqual("COMPACT_MANDATORY_FIELDS_OVER_LIMIT", body["error"]["code"])

    def test_attachment_detail_compaction_retries_attachment_summary_profile(self):
        mandatory = self.direct_item("The procurement cycle is one year.", mandatory=True)
        context = [
            create_evidence_item(level="C", kind="summary", value=f"context-{index}-" + "C" * 500)
            for index in range(80)
        ]
        tables = [
            {
                "sheet_name": f"page-{index + 1}",
                "rows": 40,
                "columns_count": 12,
                "headers": [f"header-{column}-" + "H" * 30 for column in range(12)],
                "key_columns": [f"header-{column}" for column in range(8)],
                "field_stats": {f"field-{column}": {"sample": "V" * 100} for column in range(12)},
                "summary": "T" * 240,
                "business_value": "B" * 180,
            }
            for index in range(120)
        ]
        attachment = {
            "articleattid": "attachment-1",
            "filename": "large-table.pdf",
            "core_attachment": True,
            "parse_status": "parsed_table_summary",
            "summary": "S" * 4_000,
            "key_facts": ["fact-" + "K" * 240 for _ in range(12)],
            "important_sections": ["section-" + "I" * 320 for _ in range(8)],
            "table_summaries": tables,
        }
        pack = self.base_pack([mandatory, *context])
        pack["primary_materials"][0]["attachments"] = [copy.deepcopy(attachment)]
        pack["primary_materials"][0]["attachment_summaries"] = [copy.deepcopy(attachment)]

        with patch.dict(os.environ, {"ENABLE_VBP_COMPACT_PRESERVATION": "true"}, clear=False):
            compact = main_module._compact_evidence_pack_for_dify(pack)

        self.assertGreaterEqual(compact["compact_pack_chars"], 75_000)
        self.assertLessEqual(compact["compact_pack_chars"], 79_000)
        self.assertEqual(1.0, compact["mandatory_evidence_retention"]["rate"])
        self.assertIn("hard_limit_attachment_detail_compacted", compact["compression_reason"])
        self.assertIn("hard_limit_attachment_summary_compacted", compact["compression_reason"])
        compact_summary = compact["primary_materials"][0]["attachment_summaries"][0]
        self.assertEqual(attachment["key_facts"], compact_summary["key_facts"])
        expected_headers = {
            header
            for table in attachment["table_summaries"]
            for header in table["headers"]
        }
        self.assertEqual(expected_headers, set(compact_summary["table_header_catalog"]))

    def test_strong_profile_keeps_every_attachment_fact_and_meaningful_header(self):
        attachments = []
        for attachment_index in range(10):
            tables = [
                {
                    "sheet_name": f"page-{table_index + 1}",
                    "rows": 40,
                    "columns_count": 12,
                    "headers": [f"a{attachment_index}-header-{column}" for column in range(12)],
                    "key_columns": [f"a{attachment_index}-header-{column}" for column in range(8)],
                    "field_stats": {
                        f"field-{column}": {"sample": "V" * 100}
                        for column in range(12)
                    },
                    "summary": "T" * 240,
                    "business_value": "B" * 180,
                }
                for table_index in range(8)
            ]
            attachments.append(
                {
                    "articleattid": f"attachment-{attachment_index}",
                    "filename": f"attachment-{attachment_index}.pdf",
                    "core_attachment": True,
                    "parse_status": "parsed_table_summary",
                    "summary": "S" * 3_000,
                    "key_facts": [
                        f"attachment-{attachment_index}-fact-{fact_index}"
                        for fact_index in range(4)
                    ],
                    "important_sections": ["I" * 200 for _ in range(4)],
                    "table_summaries": tables,
                }
            )
        compact = {
            "pack_id": "pack_many_attachments",
            "primary_materials": [
                {
                    "content_text": "X" * 5_000,
                    "attachments": copy.deepcopy(attachments),
                    "attachment_summaries": copy.deepcopy(attachments),
                }
            ],
            "auxiliary_materials": [],
            "combined_key_facts": [],
            "report_focus": [],
            "warnings": [],
            "generation_guidance": {},
            "omitted_content": [],
        }
        first_pass_chars = main_module._refresh_dify_char_fields(compact)

        main_module._compact_for_dify_hard_limit_second_pass(
            compact,
            target_max_chars=80_000,
            compression_reason=[],
            first_pass_chars_override=first_pass_chars,
            preserve_structure=True,
        )

        expected_ids = {item["articleattid"] for item in attachments}
        expected_facts = {fact for item in attachments for fact in item["key_facts"]}
        expected_headers = {
            header
            for item in attachments
            for table in item["table_summaries"]
            for header in table["headers"]
            if header.strip()
        }
        catalog = compact["attachment_fidelity_catalog"]
        self.assertEqual(expected_ids, {item["articleattid"] for item in catalog})
        self.assertEqual(expected_facts, {fact for item in catalog for fact in item["key_facts"]})
        self.assertEqual(
            expected_headers,
            {header for item in catalog for header in item["table_headers"]},
        )
        self.assertTrue(all(len(item["summary"]) >= 160 for item in catalog))
        self.assertLessEqual(main_module._refresh_dify_char_fields(compact), 80_000)


if __name__ == "__main__":
    unittest.main()
