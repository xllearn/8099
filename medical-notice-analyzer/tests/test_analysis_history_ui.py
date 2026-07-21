from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.analysis_history import AnalysisHistoryStore


COMPARE_FIELDS = {
    "started_at",
    "ended_at",
    "duration_ms",
    "provider",
    "workflow_run_id",
    "compact_pack_chars",
    "quality_status",
    "primary_failure_code",
    "draft_word_export_available",
    "final_word_export_available",
    "deliverable",
    "needs_manual_review",
}


def history_record(run_id: str, *, articleid: str = "article-1", finished: bool = False) -> dict[str, object]:
    return {
        "run_id": run_id,
        "pack_id": f"pack-{articleid}",
        "status": "finished" if finished else "needs_manual_review",
        "created_at": "2026-07-16 08:00:00",
        "updated_at": "2026-07-16 08:02:00" if finished else "2026-07-16 08:01:00",
        "started_at": "2026-07-16 08:00:00",
        "ended_at": "2026-07-16 08:02:00" if finished else "2026-07-16 08:01:00",
        "provider": "dify",
        "workflow_run_id": f"workflow-{run_id}",
        "compact_pack_chars": 79000 if finished else 78000,
        "quality_check": {"passed": finished, "issues": []},
        "quality_gate": {"deliverable_status": "deliverable" if finished else "needs_manual_review"},
        "primary_failure_code": "" if finished else "VBP_UNSUPPORTED_CLAIM",
        "draft_word_export_available": True,
        "final_word_export_available": finished,
        "word_export_available": True,
        "deliverable": finished,
        "needs_manual_review": not finished,
        "word_download_url": f"/analysis/runs/{run_id}/download",
        "word_filename": f"{run_id}.docx",
        "report_markdown": "# private report body",
        "report_ir": {"body": "private report IR"},
        "diagnostics": {"full": "private diagnostics"},
        "attachments": [{"filepath": "/srv/private/source.pdf", "content": "private attachment"}],
        "api_key": "secret",
        "record_id": f"project_notice:{articleid}",
        "notice_id": f"project_notice:{articleid}",
        "menu_code": "project_notice",
        "menu_name": "项目公告",
        "articleid": articleid,
        "title": f"Notice {articleid}",
        "material_identities": [
            {
                "role": "primary",
                "record_id": f"project_notice:{articleid}",
                "notice_id": f"project_notice:{articleid}",
                "menu_code": "project_notice",
                "articleid": articleid,
                "title": f"Notice {articleid}",
            }
        ],
    }


class AnalysisHistoryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main_module.app)

    def setUp(self) -> None:
        self.enabled_environment = patch.dict(
            main_module.os.environ,
            {
                "ENABLE_ANALYSIS_HISTORY": "true",
                "ENABLE_ANALYSIS_HISTORY_UI": "true",
                "WEB_CONCURRENCY": "1",
            },
            clear=False,
        )
        self.enabled_environment.start()

    def tearDown(self) -> None:
        self.enabled_environment.stop()

    def test_history_ui_route_serves_static_page(self) -> None:
        response = self.client.get("/analysis-history-ui")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers.get("content-type", ""))
        self.assertIn("分析历史", response.text)

    def test_history_ui_uses_only_history_list_contract_and_required_trace_fields(self) -> None:
        response = self.client.get("/analysis-history-ui")
        html = response.text

        self.assertIn("/analysis/history", html)
        for query_name in ("articleid", "record_id", "status", "provider", "q", "page", "page_size"):
            self.assertIn(query_name, html)
        for field in (
            "started_at",
            "ended_at",
            "duration_ms",
            "provider",
            "workflow_run_id",
            "compact_pack_chars",
            "primary_failure_code",
            "draft_word_export_available",
            "final_word_export_available",
            "deliverable",
            "needs_manual_review",
        ):
            self.assertIn(field, html)
        self.assertIn("/analysis-runs/", html)
        self.assertIn("/analysis/history/compare", html)
        self.assertIn('id="compareRuns"', html)
        self.assertIn("page_size: 20", html)
        self.assertIn("escapeHtml", html)

    def test_history_ui_does_not_request_or_render_private_fact_sources(self) -> None:
        response = self.client.get("/analysis-history-ui")
        self.assertEqual(response.status_code, 200)
        html = response.text.lower()

        forbidden_tokens = (
            "report_markdown",
            "report_ir",
            "server_path",
            "filepath",
            "attachment_content",
            "database_password",
            "api_key",
            "memory_items",
            "/report",
        )
        for token in forbidden_tokens:
            self.assertNotIn(token, html)

    def test_records_ui_exposes_history_navigation(self) -> None:
        response = self.client.get("/records-ui")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="analysisHistoryLink"', response.text)
        self.assertIn('href="/analysis-history-ui" hidden', response.text)
        self.assertIn("analysis_history_ui_enabled", response.text)
        self.assertIn("分析历史", response.text)

    def test_ui_switch_hides_route_and_navigation_without_disabling_history_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = Path(tmpdir) / "history"
            AnalysisHistoryStore(history_dir, enforce_single_worker=False).record_run(
                history_record("run_ui_switch_001")
            )
            with patch.object(main_module, "_analysis_history_dir", return_value=history_dir), patch.dict(
                main_module.os.environ,
                {"ENABLE_ANALYSIS_HISTORY": "true", "ENABLE_ANALYSIS_HISTORY_UI": "false"},
                clear=False,
            ):
                ui = self.client.get("/analysis-history-ui")
                listing = self.client.get("/analysis/history")
                health = self.client.get("/health")

        self.assertEqual(ui.status_code, 404)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["total"], 1)
        self.assertFalse(health.json()["analysis_history_ui_enabled"])

    def test_history_list_defaults_to_20_caps_at_100_and_redacts_private_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = Path(tmpdir) / "history"
            store = AnalysisHistoryStore(history_dir, enforce_single_worker=False)
            for index in range(25):
                store.record_run(history_record(f"run_ui_page_{index:03d}"))
            with patch.object(main_module, "_analysis_history_dir", return_value=history_dir):
                default_page = self.client.get("/analysis/history")
                invalid_page = self.client.get("/analysis/history", params={"page_size": 101})

        self.assertEqual(default_page.status_code, 200)
        self.assertEqual(default_page.json()["page_size"], 20)
        self.assertEqual(len(default_page.json()["items"]), 20)
        self.assertEqual(invalid_page.status_code, 422)
        serialized = json.dumps(default_page.json(), ensure_ascii=False).lower()
        for token in (
            "report_markdown",
            "report_ir",
            "attachments",
            "filepath",
            "word_download_url",
            "word_filename",
            "diagnostics",
            "api_key",
            "/srv/private",
        ):
            self.assertNotIn(token, serialized)

    def test_compare_two_runs_of_same_material_returns_only_trace_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = Path(tmpdir) / "history"
            store = AnalysisHistoryStore(history_dir, enforce_single_worker=False)
            store.record_run(history_record("run_ui_compare_001"))
            store.record_run(history_record("run_ui_compare_002", finished=True))
            store.record_run(history_record("run_ui_compare_other", articleid="article-2"))
            with patch.object(main_module, "_analysis_history_dir", return_value=history_dir):
                response = self.client.get(
                    "/analysis/history/compare",
                    params={"left_run_id": "run_ui_compare_001", "right_run_id": "run_ui_compare_002"},
                )
                mismatch = self.client.get(
                    "/analysis/history/compare",
                    params={"left_run_id": "run_ui_compare_001", "right_run_id": "run_ui_compare_other"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload["left"]), COMPARE_FIELDS)
        self.assertEqual(set(payload["right"]), COMPARE_FIELDS)
        self.assertTrue(set(payload["changes"]).issubset(COMPARE_FIELDS))
        self.assertIn("deliverable", payload["changes"])
        self.assertEqual(mismatch.status_code, 409)
        self.assertEqual(mismatch.json()["error"]["code"], "ANALYSIS_HISTORY_MATERIAL_MISMATCH")
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        for token in ("report_markdown", "attachment", "server_path", "word_download_url", "diagnostics", "secret"):
            self.assertNotIn(token, serialized)

    def test_history_ui_has_narrow_screen_layout_contract(self) -> None:
        response = self.client.get("/analysis-history-ui")
        html = response.text

        self.assertIn("@media (max-width: 640px)", html)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", html)
        self.assertIn("word-break: keep-all;", html)


if __name__ == "__main__":
    unittest.main()
