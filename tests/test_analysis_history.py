from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module


class AnalysisHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main_module.app)

    def test_history_store_records_run_created_and_run_updated_events(self) -> None:
        from app.analysis_history import AnalysisHistoryStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = AnalysisHistoryStore(Path(tmpdir) / "analysis_history")
            running = {
                "run_id": "run_history1234",
                "pack_id": "pack_history",
                "status": "running",
                "created_at": "2026-07-10 09:00:00",
                "updated_at": "2026-07-10 09:00:00",
                "articleid": "article-1",
                "record_id": "project_notice:article-1",
                "notice_id": "project_notice:article-1",
                "menu_code": "project_notice",
                "menu_name": "Project Notice",
                "title": "History Notice",
                "provider": "dify",
            }
            finished = {
                **running,
                "status": "needs_manual_review",
                "updated_at": "2026-07-10 09:01:30",
                "workflow_run_id": "workflow-history",
                "compact_pack_chars": 78326,
                "primary_failure_code": "UNSUPPORTED_FACT",
                "secondary_failure_codes": ["LOCAL_QUALITY_GATE_FAILED"],
                "word_export_available": True,
                "draft_word_export_available": True,
                "final_word_export_available": False,
                "deliverable": False,
                "needs_manual_review": True,
                "timings": {"generation_ms": 90000},
            }

            store.record_run_created(running)
            store.record_run_updated(finished)

            events = [json.loads(line) for line in store.events_path.read_text(encoding="utf-8").splitlines()]
            index = json.loads(store.index_path.read_text(encoding="utf-8"))

        self.assertEqual([event["event"] for event in events], ["run_created", "run_updated"])
        self.assertEqual(index["runs"]["run_history1234"]["status"], "needs_manual_review")
        self.assertEqual(index["runs"]["run_history1234"]["articleid"], "article-1")
        self.assertEqual(index["runs"]["run_history1234"]["workflow_run_id"], "workflow-history")
        self.assertEqual(index["runs"]["run_history1234"]["compact_pack_chars"], 78326)
        self.assertEqual(index["runs"]["run_history1234"]["duration_ms"], 90000)
        self.assertTrue(index["runs"]["run_history1234"]["draft_word_export_available"])
        self.assertFalse(index["runs"]["run_history1234"]["final_word_export_available"])
        self.assertFalse(index["runs"]["run_history1234"]["deliverable"])
        self.assertTrue(index["runs"]["run_history1234"]["needs_manual_review"])

    def test_write_analysis_run_failure_to_write_history_does_not_fail_run_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "runs"
            history_dir = Path(tmpdir) / "history"
            run_dir.mkdir()
            with patch.object(main_module, "_analysis_run_dir", return_value=run_dir, create=True), patch.object(
                main_module, "_analysis_history_dir", return_value=history_dir, create=True
            ), patch.dict(main_module.os.environ, {"ENABLE_ANALYSIS_HISTORY": "true"}, clear=False), patch(
                "app.analysis_history.AnalysisHistoryStore.record_run_updated",
                side_effect=OSError("history disk unavailable"),
            ):
                main_module._write_analysis_run(
                    {
                        "run_id": "run_historyfail1",
                        "pack_id": "pack_historyfail",
                        "status": "finished",
                        "report_markdown": "report body",
                        "quality_check": {"passed": True, "issues": []},
                        "quality_gate": {"deliverable_status": "deliverable"},
                    }
                )
                saved = main_module._read_analysis_run("run_historyfail1")
                run_file_exists = (run_dir / "run_historyfail1.json").exists()

        self.assertEqual(saved["status"], "finished")
        self.assertTrue(run_file_exists)

    def test_disabled_history_does_not_write_and_list_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "runs"
            history_dir = root / "analysis_history"
            run_dir.mkdir()
            with patch.object(main_module, "_analysis_run_dir", return_value=run_dir, create=True), patch.object(
                main_module, "_analysis_history_dir", return_value=history_dir, create=True
            ), patch.dict(main_module.os.environ, {"ENABLE_ANALYSIS_HISTORY": "false"}, clear=False):
                main_module._write_analysis_run(
                    {
                        "run_id": "run_historyoff1",
                        "pack_id": "pack_historyoff",
                        "status": "running",
                        "created_at": "2026-07-10 09:00:00",
                        "updated_at": "2026-07-10 09:00:00",
                    }
                )
                listing = self.client.get("/analysis/history")
                rebuild = self.client.post("/analysis/history/rebuild")
                history_exists = history_dir.exists()

        self.assertFalse(history_exists)
        self.assertEqual(listing.status_code, 200)
        self.assertFalse(listing.json()["enabled"])
        self.assertEqual(listing.json()["total"], 0)
        self.assertEqual(rebuild.status_code, 200)
        self.assertFalse(rebuild.json()["enabled"])

    def test_history_api_filters_by_articleid_and_rebuilds_from_run_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "runs"
            history_dir = root / "analysis_history"
            run_dir.mkdir()
            record = {
                "run_id": "run_historyapi1",
                "pack_id": "pack_historyapi",
                "status": "needs_manual_review",
                "created_at": "2026-07-10 09:00:00",
                "updated_at": "2026-07-10 09:02:00",
                "record_id": "project_notice:article-api",
                "notice_id": "project_notice:article-api",
                "articleid": "article-api",
                "menu_code": "project_notice",
                "menu_name": "Project Notice",
                "title": "API History Notice",
                "workflow_run_id": "workflow-api",
                "provider": "dify",
                "compact_pack_chars": 77777,
                "primary_failure_code": "UNSUPPORTED_FACT",
                "report_markdown": "report body",
                "quality_check": {"passed": False, "issues": []},
                "quality_gate": {"deliverable_status": "needs_manual_review"},
                "draft_word_export_available": True,
                "final_word_export_available": False,
                "deliverable": False,
                "needs_manual_review": True,
            }
            (run_dir / "run_historyapi1.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

            with patch.object(main_module, "_analysis_run_dir", return_value=run_dir, create=True), patch.object(
                main_module, "_analysis_history_dir", return_value=history_dir, create=True
            ), patch.dict(main_module.os.environ, {"ENABLE_ANALYSIS_HISTORY": "true"}, clear=False):
                rebuild = self.client.post("/analysis/history/rebuild")
                listing = self.client.get("/analysis/history", params={"articleid": "article-api"})
                detail = self.client.get("/analysis/history/run_historyapi1")

        self.assertEqual(rebuild.status_code, 200)
        self.assertEqual(rebuild.json()["total"], 1)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["total"], 1)
        self.assertEqual(listing.json()["items"][0]["run_id"], "run_historyapi1")
        self.assertEqual(listing.json()["items"][0]["articleid"], "article-api")
        self.assertEqual(listing.json()["items"][0]["workflow_run_id"], "workflow-api")
        self.assertEqual(listing.json()["items"][0]["compact_pack_chars"], 77777)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["item"]["run_id"], "run_historyapi1")

    def test_download_updates_history_with_word_export_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "runs"
            history_dir = root / "analysis_history"
            report_dir = root / "reports"
            run_dir.mkdir()
            record = {
                "run_id": "run_historyword1",
                "pack_id": "pack_historyword",
                "status": "finished",
                "created_at": "2026-07-10 09:00:00",
                "updated_at": "2026-07-10 09:01:00",
                "articleid": "article-word",
                "menu_code": "project_notice",
                "menu_name": "Project Notice",
                "title": "Word History Notice",
                "report_title": "Word History Report",
                "report_markdown": "# Word History Report\n\nbody",
                "quality_check": {"passed": True, "issues": []},
                "quality_gate": {"deliverable_status": "deliverable"},
                "word_export_available": True,
                "final_word_export_available": True,
                "draft_word_export_available": False,
                "deliverable": True,
                "needs_manual_review": False,
            }

            with patch.object(main_module, "_analysis_run_dir", return_value=run_dir, create=True), patch.object(
                main_module, "_analysis_history_dir", return_value=history_dir, create=True
            ), patch.dict(main_module.os.environ, {"ENABLE_ANALYSIS_HISTORY": "true"}, clear=False):
                main_module._write_analysis_run(record)
            with patch.object(main_module, "_analysis_run_dir", return_value=run_dir, create=True), patch.object(
                main_module, "_analysis_history_dir", return_value=history_dir, create=True
            ), patch.object(main_module, "REPORT_DIR", report_dir), patch.dict(
                main_module.os.environ, {"ENABLE_ANALYSIS_HISTORY": "true"}, clear=False
            ):
                response = self.client.get("/analysis/runs/run_historyword1/download")
                detail = self.client.get("/analysis/history/run_historyword1")
                rebuild = self.client.post("/analysis/history/rebuild")
                rebuilt_detail = self.client.get("/analysis/history/run_historyword1")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/analysis/runs/run_historyword1/download", detail.json()["item"]["word_download_url"])
        self.assertTrue(detail.json()["item"]["word_export_available"])
        self.assertEqual(rebuild.status_code, 200)
        self.assertIn(
            "/analysis/runs/run_historyword1/download",
            rebuilt_detail.json()["item"]["word_download_url"],
        )


if __name__ == "__main__":
    unittest.main()
