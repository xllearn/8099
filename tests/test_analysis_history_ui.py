from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

import app.main as main_module


class AnalysisHistoryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main_module.app)

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
        self.assertIn('href="/analysis-history-ui"', response.text)
        self.assertIn("分析历史", response.text)

    def test_history_ui_has_narrow_screen_layout_contract(self) -> None:
        response = self.client.get("/analysis-history-ui")
        html = response.text

        self.assertIn("@media (max-width: 640px)", html)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", html)
        self.assertIn("word-break: keep-all;", html)


if __name__ == "__main__":
    unittest.main()
