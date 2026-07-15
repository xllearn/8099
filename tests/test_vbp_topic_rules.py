from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class VbpTopicRulesTests(unittest.TestCase):
    def test_feature_flags_are_documented_in_env_and_compose(self) -> None:
        root = Path(__file__).resolve().parents[1]
        env_path = root / ".env.example"
        compose_path = root / "docker-compose.yml"
        if not env_path.exists() or not compose_path.exists():
            self.skipTest("repository root config files are not packaged in this runtime image")
        env_example = env_path.read_text(encoding="utf-8")
        compose = compose_path.read_text(encoding="utf-8")

        for name in (
            "ENABLE_VBP_PROJECT_OPTIMIZATION",
            "ENABLE_PROJECT_NOTICE_PRIORITY",
            "ENABLE_ANALYSIS_HISTORY",
        ):
            self.assertIn(f"{name}=true", env_example)
            self.assertIn(f"{name}: ${{{name}:-true}}", compose)
        for name in (
            "ENABLE_VBP_FACT_EXTRACTION",
            "ENABLE_VBP_COMPACT_PRESERVATION",
            "ENABLE_VBP_REPORT_RULES",
            "ENABLE_EVIDENCE_INDEX",
        ):
            self.assertIn(f"{name}=false", env_example)
            self.assertIn(f"{name}: ${{{name}:-false}}", compose)

    def test_default_vbp_rules_file_loads_and_validates(self) -> None:
        from app.report_rules.schema import load_vbp_topic_rules

        rules = load_vbp_topic_rules()

        self.assertEqual(rules["version"], "2026-07-15-s2-v1")
        self.assertEqual(rules["report_structure"]["version"], "vbp-report-structure-v1")
        required_section_ids = {
            item["section_id"] for item in rules["report_structure"]["required_sections"]
        }
        self.assertEqual(
            {"overview", "core_rules", "actions", "impact_and_risk", "recommendations"},
            required_section_ids,
        )
        self.assertIn("项目公告", rules["trigger"]["menu_names"])
        topic_ids = {topic["topic_id"] for topic in rules["topics"]}
        self.assertEqual(
            {
                "procurement_varieties",
                "procurement_cycle",
                "alliance_provinces",
                "group_price_table",
                "quote_rules",
                "selection_rules",
                "agreed_purchase_volume",
            },
            topic_ids,
        )
        self.assertEqual(topic_ids, {topic["fact_type"] for topic in rules["topics"]})

    def test_vbp_rule_validation_rejects_missing_required_topic_fields(self) -> None:
        from app.report_rules.schema import VbpTopicRulesError, validate_vbp_topic_rules

        rules = {
            "version": "test",
            "trigger": {"menu_names": ["项目公告"], "title_keywords": [], "category_keywords": []},
            "topics": [{"topic_id": "quote_rules", "presentation": "dedicated_section"}],
        }

        with self.assertRaisesRegex(VbpTopicRulesError, "failure_code"):
            validate_vbp_topic_rules(rules)

    def test_vbp_rule_validation_rejects_invalid_presentation_type(self) -> None:
        from app.report_rules.schema import VbpTopicRulesError, validate_vbp_topic_rules

        rules = {
            "version": "test",
            "trigger": {"menu_names": ["项目公告"], "title_keywords": [], "category_keywords": []},
            "topics": [
                {
                    "topic_id": "quote_rules",
                    "labels": ["报价要求"],
                    "presentation": "paragraph",
                    "failure_code": "VBP_PRICE_RULE_MISSING",
                }
            ],
        }

        with self.assertRaisesRegex(VbpTopicRulesError, "presentation"):
            validate_vbp_topic_rules(rules)

    def test_vbp_rule_validation_rejects_invalid_report_structure(self) -> None:
        from app.report_rules.schema import VbpTopicRulesError, validate_vbp_topic_rules

        rules = {
            "version": "test",
            "trigger": {"menu_names": ["项目公告"], "title_keywords": [], "category_keywords": []},
            "topics": [
                {
                    "topic_id": "quote_rules",
                    "labels": ["报价要求"],
                    "presentation": "dedicated_section",
                    "failure_code": "VBP_PRICE_RULE_MISSING",
                }
            ],
            "report_structure": {
                "version": "structure-v1",
                "required_sections": [{"section_id": "overview", "labels": []}],
            },
        }

        with self.assertRaisesRegex(VbpTopicRulesError, "required_sections.*labels"):
            validate_vbp_topic_rules(rules)

    def test_vbp_rule_loading_respects_feature_flag(self) -> None:
        from app.report_rules.schema import load_vbp_topic_rules_if_enabled

        with patch.dict(os.environ, {"ENABLE_VBP_PROJECT_OPTIMIZATION": "false"}):
            self.assertIsNone(load_vbp_topic_rules_if_enabled())

        with patch.dict(os.environ, {"ENABLE_VBP_PROJECT_OPTIMIZATION": "true"}):
            self.assertIsNotNone(load_vbp_topic_rules_if_enabled())

    def test_loading_from_custom_path_validates_yaml(self) -> None:
        from app.report_rules.schema import VbpTopicRulesError, load_vbp_topic_rules

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.yml"
            path.write_text(
                """
version: test
trigger:
  menu_names: ["项目公告"]
topics:
  - topic_id: quote_rules
    labels: ["报价要求"]
    presentation: paragraph
    failure_code: VBP_PRICE_RULE_MISSING
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(VbpTopicRulesError, "presentation"):
                load_vbp_topic_rules(path)


if __name__ == "__main__":
    unittest.main()
