from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module


class ReportMemoryServiceTests(unittest.TestCase):
    def test_read_creates_default_report_and_candidate_memory(self) -> None:
        from app.report_memory import MemoryKind, read_memory

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict("os.environ", {"MEMORY_DIR": tmpdir}, clear=False):
            report = read_memory(MemoryKind.REPORT)
            candidates = read_memory(MemoryKind.CANDIDATES)

            self.assertTrue((Path(tmpdir) / "report_memory.md").exists())
            self.assertTrue((Path(tmpdir) / "memory_candidates.md").exists())
            self.assertIn("# Report Memory", report.content)
            self.assertIn("# Memory Candidates", candidates.content)
            self.assertEqual(report.max_chars, 15000)
            self.assertEqual(candidates.max_chars, 100000)
            self.assertEqual(len(report.sha256), 64)

    def test_save_backs_up_old_content_and_rejects_blank_or_oversized_content(self) -> None:
        from app.report_memory import MemoryContentError, MemoryKind, read_memory, save_memory

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {"MEMORY_DIR": tmpdir, "REPORT_MEMORY_MAX_CHARS": "30", "MEMORY_BACKUP_KEEP_COUNT": "2"},
            clear=False,
        ):
            original = read_memory(MemoryKind.REPORT)
            saved = save_memory(MemoryKind.REPORT, "# Report Memory\n\nrule")

            backup = Path(tmpdir) / saved.backup_name
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_text(encoding="utf-8"), original.content)
            self.assertEqual(saved.document.content, "# Report Memory\n\nrule")

            with self.assertRaises(MemoryContentError) as blank:
                save_memory(MemoryKind.REPORT, "  \n")
            self.assertEqual(blank.exception.code, "MEMORY_EMPTY")

            with self.assertRaises(MemoryContentError) as oversized:
                save_memory(MemoryKind.REPORT, "x" * 31)
            self.assertEqual(oversized.exception.code, "MEMORY_TOO_LARGE")
            self.assertEqual(oversized.exception.max_chars, 30)

    def test_memory_api_enforces_write_token_when_configured(self) -> None:
        client = TestClient(main_module.app)
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            main_module.os.environ,
            {"MEMORY_DIR": tmpdir, "MEMORY_WRITE_TOKEN": "secret-token"},
            clear=False,
        ):
            get_response = client.get("/memory/report")
            self.assertEqual(get_response.status_code, 200)
            self.assertTrue(get_response.json()["write_protection_enabled"])

            missing = client.put("/memory/report", json={"content": "# Report Memory\n\nrule"})
            self.assertEqual(missing.status_code, 401)

            saved = client.put(
                "/memory/report",
                json={"content": "# Report Memory\n\nrule"},
                headers={"X-Memory-Write-Token": "secret-token"},
            )
            self.assertEqual(saved.status_code, 200)
            self.assertTrue(saved.json()["backup_name"].startswith("report_memory_"))

    def test_memory_ui_serves_static_page(self) -> None:
        client = TestClient(main_module.app)

        response = client.get("/memory-ui")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("报告记忆库管理", response.text)
        self.assertIn("/memory/report", response.text)
        self.assertIn("/memory/candidates", response.text)
        self.assertIn('class="app-header"', response.text)
        self.assertIn('class="brand-shield"', response.text)
        self.assertIn("page-heading-card", response.text)
        self.assertIn("报告记忆库管理", response.text)
        self.assertIn("当前未配置写入保护", response.text)

    def test_memory_api_allows_local_writes_without_token_and_reports_unprotected_state(self) -> None:
        client = TestClient(main_module.app)
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            main_module.os.environ,
            {"MEMORY_DIR": tmpdir, "MEMORY_WRITE_TOKEN": ""},
            clear=False,
        ):
            saved = client.put("/memory/candidates", json={"content": "# Memory Candidates\n\ncandidate"})

            self.assertEqual(saved.status_code, 200)
            self.assertFalse(saved.json()["write_protection_enabled"])
            self.assertTrue(saved.json()["backup_name"].startswith("memory_candidates_"))


if __name__ == "__main__":
    unittest.main()
