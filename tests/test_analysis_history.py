from __future__ import annotations

import importlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module


REQUIRED_HISTORY_FIELDS = {
    "run_id",
    "pack_id",
    "material_identities",
    "record_id",
    "notice_id",
    "menu_code",
    "articleid",
    "started_at",
    "ended_at",
    "duration_ms",
    "provider",
    "workflow_run_id",
    "compact_pack_chars",
    "quality_status",
    "quality_passed",
    "quality_gate_status",
    "primary_failure_code",
    "word_generated",
    "word_download_available",
    "draft_word_export_available",
    "final_word_export_available",
    "deliverable",
    "needs_manual_review",
    "revision",
    "latest_event_id",
}


def history_record(
    run_id: str = "run_history0001",
    *,
    articleid: str = "article-1",
    status: str = "needs_manual_review",
) -> dict[str, object]:
    terminal = status in {"finished", "needs_manual_review", "failed", "interrupted"}
    deliverable = status == "finished"
    return {
        "success": status != "failed",
        "run_id": run_id,
        "pack_id": f"pack-{articleid}",
        "status": status,
        "created_at": "2026-07-14 08:00:00",
        "updated_at": "2026-07-14 08:01:30" if terminal else "2026-07-14 08:00:00",
        "started_at": "2026-07-14 08:00:00",
        "ended_at": "2026-07-14 08:01:30" if terminal else "",
        "provider": "dify",
        "workflow_run_id": f"workflow-{articleid}",
        "compact_pack_chars": 78326,
        "quality_check": {"passed": True if deliverable else (False if terminal else None), "issues": []},
        "quality_gate": {"deliverable_status": "deliverable" if deliverable else ("needs_manual_review" if terminal else "")},
        "primary_failure_code": "" if deliverable else ("UNSUPPORTED_FACT" if terminal else ""),
        "secondary_failure_codes": ["LOCAL_QUALITY_GATE_FAILED"] if terminal and not deliverable else [],
        "draft_word_export_available": terminal,
        "final_word_export_available": deliverable,
        "word_export_available": terminal,
        "deliverable": deliverable,
        "needs_manual_review": terminal and not deliverable,
        "report_title": "History report",
        "report_markdown": "# History report\n\nSECRET REPORT BODY THAT MUST NOT ENTER HISTORY",
        "report_ir": {"title": "SECRET REPORT IR"},
        "memory": "SECRET MEMORY",
        "memory_items": [{"content": "SECRET MEMORY ITEM"}],
        "material_identities": [
            {
                "role": "primary",
                "menu_code": "project_notice",
                "articleid": articleid,
                "record_id": f"project_notice:{articleid}",
                "notice_id": f"project_notice:{articleid}",
                "title": f"Notice {articleid}",
            },
            {
                "role": "auxiliary",
                "menu_code": "policy_notice",
                "articleid": f"aux-{articleid}",
                "record_id": f"policy_notice:aux-{articleid}",
                "notice_id": f"policy_notice:aux-{articleid}",
                "title": f"Auxiliary {articleid}",
            },
        ],
        "record_id": f"project_notice:{articleid}",
        "notice_id": f"project_notice:{articleid}",
        "menu_code": "project_notice",
        "menu_name": "项目公告",
        "articleid": articleid,
        "title": f"Notice {articleid}",
    }


class AnalysisHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main_module.app)

    def history_module(self):
        spec = importlib.util.find_spec("app.analysis_history")
        self.assertIsNotNone(spec, "app.analysis_history must be implemented")
        module = importlib.import_module("app.analysis_history")
        self.assertTrue(hasattr(module, "AnalysisHistoryStore"))
        return module

    def make_store(self, root: Path, **kwargs):
        module = self.history_module()
        return module.AnalysisHistoryStore(root, enforce_single_worker=False, **kwargs)

    def test_event_and_index_capture_required_fields_without_body_or_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(Path(tmpdir) / "history")
            item = store.record_run(history_record())
            events = store.read_events()
            index_text = store.index_path.read_text(encoding="utf-8")
            events_text = store.events_path.read_text(encoding="utf-8")

        self.assertTrue(REQUIRED_HISTORY_FIELDS.issubset(item))
        self.assertEqual(item["duration_ms"], 90000)
        self.assertEqual(item["quality_status"], "needs_manual_review")
        self.assertEqual(item["revision"], 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_id"], item["latest_event_id"])
        self.assertEqual(events[0]["revision"], 1)
        self.assertNotIn("report_markdown", events[0]["item"])
        self.assertNotIn("report_ir", events[0]["item"])
        for secret in ("SECRET REPORT BODY", "SECRET REPORT IR", "SECRET MEMORY"):
            self.assertNotIn(secret, index_text)
            self.assertNotIn(secret, events_text)

    def test_revision_is_monotonic_and_event_ids_are_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(Path(tmpdir) / "history")
            first = store.record_run(history_record(status="running"))
            second = store.record_run(history_record(status="finished"))
            events = store.read_events()

        self.assertEqual((first["revision"], second["revision"]), (1, 2))
        self.assertEqual([event["revision"] for event in events], [1, 2])
        self.assertEqual(len({event["event_id"] for event in events}), 2)
        self.assertEqual(second["duration_ms"], 90000)

    def test_32_threads_do_not_lose_events_or_duplicate_event_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = Path(tmpdir) / "history"
            store = self.make_store(history_dir)

            def write(number: int) -> None:
                self.make_store(history_dir).record_run(
                    history_record(
                        f"run_thread{number:04d}",
                        articleid=f"article-{number}",
                        status="running",
                    )
                )

            with ThreadPoolExecutor(max_workers=32) as executor:
                list(executor.map(write, range(32)))

            events = store.read_events()
            items, total = store.list_runs(page=1, page_size=100)

        self.assertEqual(len(events), 32)
        self.assertEqual(total, 32)
        self.assertEqual(len(items), 32)
        self.assertEqual(len({event["event_id"] for event in events}), 32)

    def test_disabled_switch_causes_zero_history_writes(self) -> None:
        self.history_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "runs"
            history_dir = root / "history"
            with patch.object(main_module, "_analysis_run_dir", return_value=run_dir), patch.object(
                main_module, "_analysis_history_dir", return_value=history_dir, create=True
            ), patch.dict(main_module.os.environ, {"ENABLE_ANALYSIS_HISTORY": "false"}, clear=False):
                main_module._write_analysis_run(history_record())
                response = self.client.get("/analysis/history")

            self.assertTrue((run_dir / "run_history0001.json").exists())
            self.assertFalse(history_dir.exists())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["enabled"], False)
        self.assertEqual(response.json()["total"], 0)

    def test_history_write_failure_does_not_block_run_persistence(self) -> None:
        module = self.history_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "runs"
            history_dir = root / "history"
            with patch.object(main_module, "_analysis_run_dir", return_value=run_dir), patch.object(
                main_module, "_analysis_history_dir", return_value=history_dir, create=True
            ), patch.dict(main_module.os.environ, {"ENABLE_ANALYSIS_HISTORY": "true"}, clear=False), patch.object(
                module.AnalysisHistoryStore,
                "record_run",
                side_effect=OSError("history disk unavailable"),
            ):
                main_module._write_analysis_run(history_record(status="finished"))
                saved = main_module._read_analysis_run("run_history0001")

        self.assertEqual(saved["status"], "finished")

    def test_run_status_is_not_published_before_history_write_completes(self) -> None:
        record = history_record(status="finished")
        history_started = threading.Event()
        release_history = threading.Event()

        def blocking_history_write(_record: dict[str, object]) -> None:
            history_started.set()
            self.assertTrue(release_history.wait(timeout=5))

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            main_module, "_analysis_run_dir", return_value=Path(tmpdir) / "runs"
        ), patch.object(main_module, "_record_analysis_history", side_effect=blocking_history_write):
            writer = threading.Thread(target=main_module._write_analysis_run, args=(record,))
            writer.start()
            self.assertTrue(history_started.wait(timeout=5))
            self.assertFalse(main_module._analysis_run_path(str(record["run_id"])).exists())
            release_history.set()
            writer.join(timeout=5)
            self.assertFalse(writer.is_alive())
            self.assertTrue(main_module._analysis_run_path(str(record["run_id"])).exists())

    def test_damaged_tail_is_quarantined_and_next_event_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(Path(tmpdir) / "history")
            store.record_run(history_record("run_tail0001", status="running"))
            with store.events_path.open("ab") as handle:
                handle.write(b'{"event_id":"partial')
            store.record_run(history_record("run_tail0002", articleid="article-2", status="running"))
            events = store.read_events()
            quarantined = list(store.corrupt_dir.glob("events-tail-*.bin"))
            quarantined_bytes = quarantined[0].read_bytes()

        self.assertEqual([event["run_id"] for event in events], ["run_tail0001", "run_tail0002"])
        self.assertEqual(len(quarantined), 1)
        self.assertIn(b'"partial', quarantined_bytes)

    def test_index_can_be_rebuilt_from_jsonl_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(Path(tmpdir) / "history")
            store.record_run(history_record("run_rebuild0001", status="running"))
            store.record_run(history_record("run_rebuild0001", status="finished"))
            store.record_run(history_record("run_rebuild0002", articleid="article-2", status="running"))
            store.index_path.unlink()
            result = store.rebuild_index_from_events()
            first = store.get_run("run_rebuild0001")

        self.assertEqual(result["total_events"], 3)
        self.assertEqual(result["total_runs"], 2)
        self.assertIsNotNone(first)
        self.assertEqual(first["revision"], 2)
        self.assertEqual(first["status"], "finished")
        self.assertEqual(first["duration_ms"], 90000)

    def test_index_rebuild_repairs_legacy_zero_terminal_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(Path(tmpdir) / "history")
            store.record_run(history_record("run_legacyduration", status="finished"))
            events = store.read_events()
            events[0]["item"]["duration_ms"] = 0
            store.events_path.write_text(
                "\n".join(
                    json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                    for event in events
                )
                + "\n",
                encoding="utf-8",
            )
            store.index_path.unlink()

            store.rebuild_index_from_events()
            item = store.get_run("run_legacyduration")

        self.assertIsNotNone(item)
        self.assertEqual(item["duration_ms"], 90000)

    def test_rebuild_cli_uses_run_json_and_pack_identity(self) -> None:
        self.history_module()
        cli_spec = importlib.util.find_spec("scripts.rebuild_analysis_history")
        self.assertIsNotNone(cli_spec, "history rebuild CLI must be implemented")
        cli = importlib.import_module("scripts.rebuild_analysis_history")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "analysis_runs"
            pack_dir = root / "evidence_packs"
            history_dir = root / "analysis_history"
            run_dir.mkdir()
            pack_dir.mkdir()
            record = history_record("run_cli0001", articleid="", status="finished")
            record["material_identities"] = []
            record["record_id"] = ""
            record["notice_id"] = ""
            record["menu_code"] = ""
            record["articleid"] = ""
            (run_dir / "run_cli0001.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            pack = {
                "pack_id": record["pack_id"],
                "primary_materials": [
                    {"menu_code": "project_notice", "articleid": "from-pack", "title": "Pack notice"}
                ],
                "auxiliary_materials": [],
            }
            (pack_dir / f"{record['pack_id']}.json").write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = cli.main(
                    [
                        "--history-dir",
                        str(history_dir),
                        "--run-dir",
                        str(run_dir),
                        "--evidence-pack-dir",
                        str(pack_dir),
                    ]
                )
            index = json.loads((history_dir / "index.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["total_runs"], 1)
        self.assertEqual(index["runs"]["run_cli0001"]["articleid"], "from-pack")
        self.assertNotIn("report_markdown", index["runs"]["run_cli0001"])

    def test_rebuild_cli_is_directly_executable_from_repo_root(self) -> None:
        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, str(root / "scripts" / "rebuild_analysis_history.py"), "--help"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--history-dir", completed.stdout)

    def test_rotation_by_event_count_respects_archive_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(
                Path(tmpdir) / "history",
                max_events=2,
                max_bytes=10_000_000,
                max_archives=2,
            )
            for number in range(7):
                store.record_run(
                    history_record(
                        f"run_rotate{number:04d}", articleid=f"rotate-{number}", status="running"
                    )
                )
            archives = store.archive_paths()
            _, total = store.list_runs(page=1, page_size=100)
            last_archive_event_count = len(store.read_events(paths=archives[-1:]))

        self.assertEqual(total, 7)
        self.assertEqual(len(archives), 2)
        self.assertLessEqual(last_archive_event_count, 2)

    def test_rotation_by_size_uses_50mb_default_and_rotates_when_overridden(self) -> None:
        module = self.history_module()
        self.assertEqual(module.DEFAULT_MAX_BYTES, 50 * 1024 * 1024)
        self.assertEqual(module.DEFAULT_MAX_EVENTS, 100000)
        self.assertEqual(module.DEFAULT_MAX_ARCHIVES, 12)
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(
                Path(tmpdir) / "history",
                max_bytes=256,
                max_events=100000,
                max_archives=12,
            )
            store.record_run(history_record("run_bytes0001", status="running"))
            store.record_run(history_record("run_bytes0002", articleid="article-2", status="running"))
            archives = store.archive_paths()

        self.assertEqual(len(archives), 1)

    def test_api_filters_materials_and_paginates_without_report_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = Path(tmpdir) / "history"
            store = self.make_store(history_dir)
            store.record_run(history_record("run_api0001", articleid="article-1", status="running"))
            store.record_run(history_record("run_api0002", articleid="article-2", status="finished"))
            store.record_run(history_record("run_api0003", articleid="article-2", status="failed"))
            with patch.object(main_module, "_analysis_history_dir", return_value=history_dir, create=True), patch.dict(
                main_module.os.environ,
                {"ENABLE_ANALYSIS_HISTORY": "true", "WEB_CONCURRENCY": "1"},
                clear=False,
            ):
                first_page = self.client.get(
                    "/analysis/history",
                    params={
                        "articleid": "article-2",
                        "provider": "dify",
                        "page": 1,
                        "page_size": 1,
                    },
                )
                second_page = self.client.get(
                    "/analysis/history",
                    params={"articleid": "article-2", "page": 2, "page_size": 1},
                )
                auxiliary_match = self.client.get(
                    "/analysis/history",
                    params={"record_id": "policy_notice:aux-article-1"},
                )
                finished = self.client.get(
                    "/analysis/history",
                    params={"status": "finished", "q": "Notice article-2"},
                )

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.json()["total"], 2)
        self.assertEqual(len(first_page.json()["items"]), 1)
        self.assertEqual(second_page.json()["total"], 2)
        self.assertNotEqual(
            first_page.json()["items"][0]["run_id"], second_page.json()["items"][0]["run_id"]
        )
        self.assertEqual(auxiliary_match.json()["total"], 1)
        self.assertEqual(finished.json()["total"], 1)
        self.assertNotIn("report_markdown", json.dumps(first_page.json(), ensure_ascii=False))
        self.assertNotIn("SECRET REPORT BODY", json.dumps(first_page.json(), ensure_ascii=False))

    def test_multiworker_topology_is_rejected_without_blocking_run_save(self) -> None:
        module = self.history_module()
        with self.assertRaises(module.HistoryTopologyError):
            module.assert_single_writer_topology({"WEB_CONCURRENCY": "2"})
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "runs"
            history_dir = root / "history"
            with patch.object(main_module, "_analysis_run_dir", return_value=run_dir), patch.object(
                main_module, "_analysis_history_dir", return_value=history_dir, create=True
            ), patch.dict(
                main_module.os.environ,
                {"ENABLE_ANALYSIS_HISTORY": "true", "WEB_CONCURRENCY": "2"},
                clear=False,
            ):
                main_module._write_analysis_run(history_record(status="finished"))
                response = self.client.get("/analysis/history")

            self.assertTrue((run_dir / "run_history0001.json").exists())
            self.assertFalse(history_dir.exists())

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "ANALYSIS_HISTORY_SINGLE_WORKER_REQUIRED")

    def test_word_truth_table_is_preserved_in_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(Path(tmpdir) / "history")
            with patch.dict(main_module.os.environ, {"ENABLE_WORD_EXPORT": "true"}, clear=False):
                no_body = main_module._normalize_analysis_run_schema(
                    {
                        **history_record("run_word0001", status="finished"),
                        "report_markdown": "",
                        "report_ir": None,
                        "word_download_url": "/download/unsafe.docx",
                    }
                )
                manual = main_module._normalize_analysis_run_schema(
                    history_record(
                        "run_word0002", articleid="article-2", status="needs_manual_review"
                    )
                )
                final = main_module._normalize_analysis_run_schema(
                    history_record("run_word0003", articleid="article-3", status="finished")
                )
            no_body_history = store.record_run(no_body)
            manual_history = store.record_run(manual)
            final_history = store.record_run(final)

        self.assertFalse(no_body["draft_word_export_available"])
        self.assertFalse(no_body["final_word_export_available"])
        self.assertFalse(no_body["deliverable"])
        self.assertEqual(no_body["word_download_url"], "")
        self.assertFalse(no_body_history["word_download_available"])
        self.assertEqual(no_body_history.get("word_download_url", ""), "")
        self.assertTrue(manual_history["draft_word_export_available"])
        self.assertFalse(manual_history["final_word_export_available"])
        self.assertFalse(manual_history["deliverable"])
        self.assertTrue(manual_history["needs_manual_review"])
        self.assertTrue(final_history["draft_word_export_available"])
        self.assertTrue(final_history["final_word_export_available"])
        self.assertTrue(final_history["deliverable"])
        self.assertFalse(final_history["needs_manual_review"])

    def test_successful_b15_download_records_generated_word_without_changing_truth_table(self) -> None:
        self.history_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "runs"
            history_dir = root / "history"
            report_dir = root / "reports"
            record = main_module._normalize_analysis_run_schema(
                history_record("run_download0001", status="needs_manual_review")
            )
            with patch.object(main_module, "_analysis_run_dir", return_value=run_dir), patch.object(
                main_module, "_analysis_history_dir", return_value=history_dir, create=True
            ), patch.object(main_module, "REPORT_DIR", report_dir), patch.dict(
                main_module.os.environ,
                {
                    "ENABLE_ANALYSIS_HISTORY": "true",
                    "ENABLE_WORD_EXPORT": "true",
                    "WEB_CONCURRENCY": "1",
                },
                clear=False,
            ):
                main_module._write_analysis_run(record)
                response = self.client.get("/analysis/runs/run_download0001/download")
                detail = self.client.get("/analysis/history/run_download0001")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        item = detail.json()["item"]
        self.assertTrue(item["word_generated"])
        self.assertTrue(item["word_download_available"])
        self.assertEqual(item["word_download_url"], "/analysis/runs/run_download0001/download")
        self.assertTrue(item["draft_word_export_available"])
        self.assertFalse(item["final_word_export_available"])
        self.assertFalse(item["deliverable"])
        self.assertTrue(item["needs_manual_review"])

    def test_history_api_hides_word_locators_when_global_export_fuse_is_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = Path(tmpdir) / "history"
            store = self.make_store(history_dir)
            record = history_record("run_fusedhistory1", status="finished")
            store.record_run(record)
            store.record_word_downloaded(
                record,
                download_url="/analysis/runs/run_fusedhistory1/download",
                filename="report.docx",
            )
            with patch.object(main_module, "_analysis_history_dir", return_value=history_dir), patch.dict(
                main_module.os.environ,
                {
                    "ENABLE_ANALYSIS_HISTORY": "true",
                    "ENABLE_WORD_EXPORT": "false",
                    "WEB_CONCURRENCY": "1",
                },
                clear=False,
            ):
                listing = self.client.get("/analysis/history")
                detail = self.client.get("/analysis/history/run_fusedhistory1")

        for item in (listing.json()["items"][0], detail.json()["item"]):
            self.assertFalse(item["word_generated"])
            self.assertFalse(item["word_download_available"])
            self.assertFalse(item["word_export_available"])
            self.assertFalse(item["draft_word_export_available"])
            self.assertFalse(item["final_word_export_available"])
            self.assertEqual(item["word_download_url"], "")
            self.assertEqual(item["word_filename"], "")


if __name__ == "__main__":
    unittest.main()
