from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import app.main as main_module


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Quality87IsolationContractTests(unittest.TestCase):
    def test_capacity_defaults_are_400k_with_quality_tiers_and_utf8_guard(self) -> None:
        names = {
            "DIFY_PLATFORM_STRING_MAX_CHARS",
            "DIFY_FULL_INPUT_MAX_CHARS",
            "DIFY_SAFE_COMPACT_MAX_CHARS",
            "DIFY_EVIDENCE_PACK_HARD_MAX_CHARS",
            "DIFY_VARIABLE_HARD_MAX_CHARS",
            "DIFY_EVIDENCE_PACK_HARD_MAX_BYTES",
        }
        clean_environment = {key: value for key, value in os.environ.items() if key not in names}

        with patch.dict(main_module.os.environ, clean_environment, clear=True):
            thresholds = main_module._dify_input_thresholds()

        self.assertEqual(
            {key: thresholds.get(key) for key in (
                "platform_limit",
                "full_input",
                "safe_compact",
                "hard_limit",
                "hard_limit_bytes",
            )},
            {
                "platform_limit": 400_000,
                "full_input": 160_000,
                "safe_compact": 220_000,
                "hard_limit": 240_000,
                "hard_limit_bytes": 870_400,
            },
        )

    def test_active_runtime_defaults_are_isolated_to_quality87(self) -> None:
        expected_fragments = {
            ".env.example": (
                "PUBLIC_BASE_URL=http://192.168.34.87:8099",
                "DIFY_BASE_URL=http://192.168.34.87/v1",
                "DIFY_PLATFORM_STRING_MAX_CHARS=400000",
                "DIFY_FULL_INPUT_MAX_CHARS=160000",
                "DIFY_SAFE_COMPACT_MAX_CHARS=220000",
                "DIFY_EVIDENCE_PACK_HARD_MAX_CHARS=240000",
                "DIFY_EVIDENCE_PACK_HARD_MAX_BYTES=870400",
            ),
            "docker-compose.yml": (
                "PUBLIC_BASE_URL:-http://192.168.34.87:8099",
                "DIFY_BASE_URL:-http://192.168.34.87/v1",
                "DIFY_PLATFORM_STRING_MAX_CHARS:-400000",
                "DIFY_FULL_INPUT_MAX_CHARS:-160000",
                "DIFY_SAFE_COMPACT_MAX_CHARS:-220000",
                "DIFY_EVIDENCE_PACK_HARD_MAX_CHARS:-240000",
                "DIFY_EVIDENCE_PACK_HARD_MAX_BYTES:-870400",
            ),
            "app/main.py": ('DEFAULT_PUBLIC_BASE_URL = "http://192.168.34.87:8099"',),
            "scripts/build_dify_chatflow_dsl.py": (
                'DEFAULT_BACKEND_BASE_URL = "http://192.168.34.87:8099"',
            ),
            "scripts/import_dify_chatflow.ps1": (
                '[string]$BackendBaseUrl = "http://192.168.34.87:8099"',
            ),
            "scripts/run_16case_report_regression.py": (
                'DEFAULT_BASE_URL = "http://192.168.34.87:8099"',
            ),
        }

        for relative_path, fragments in expected_fragments.items():
            text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8-sig")
            for fragment in fragments:
                with self.subTest(path=relative_path, fragment=fragment):
                    self.assertIn(fragment, text)
            with self.subTest(path=relative_path, forbidden=".86/.88"):
                self.assertNotIn("192.168.34.86", text)
                self.assertNotIn("192.168.34.88", text)

    def test_dify_fetch_callback_targets_quality87_with_declared_pack_id_only(self) -> None:
        workflow = (PROJECT_ROOT / "dify_workflow_pack_id_human_style.yml").read_text(
            encoding="utf-8-sig"
        )
        data = yaml.safe_load(workflow)
        nodes = data["workflow"]["graph"]["nodes"]
        by_id = {node["id"]: node["data"] for node in nodes}
        callback_url = by_id["fetch_evidence_pack"]["url"]
        declared_start_variables = {
            item["variable"] for item in by_id["start_node"]["variables"]
        }
        callback_start_refs = set(
            re.findall(r"\{\{#start_node\.([A-Za-z0-9_]+)#\}\}", callback_url)
        )

        self.assertEqual(
            callback_url,
            "http://192.168.34.87:8099/analysis/packs/{{#start_node.pack_id#}}",
        )
        self.assertEqual(callback_start_refs, {"pack_id"})
        self.assertLessEqual(callback_start_refs, declared_start_variables)
        self.assertNotIn("192.168.34.86", workflow)
        self.assertNotIn("192.168.34.88", workflow)

    def test_legacy_history_routes_and_navigation_remain_available(self) -> None:
        route_paths = {route.path for route in main_module.app.routes}
        records_html = (PROJECT_ROOT / "app/static/records.html").read_text(encoding="utf-8-sig")

        self.assertTrue((PROJECT_ROOT / "app/analysis_history.py").is_file())
        self.assertTrue((PROJECT_ROOT / "app/static/analysis_history.html").is_file())
        self.assertTrue((PROJECT_ROOT / "scripts/rebuild_analysis_history.py").is_file())
        self.assertIn("/analysis-history-ui", route_paths)
        self.assertIn("/analysis/history", route_paths)
        self.assertIn("/analysis/history/compare", route_paths)
        self.assertIn("/analysis/history/{run_id}", route_paths)
        self.assertIn('href="/analysis-history-ui"', records_html)


if __name__ == "__main__":
    unittest.main()
