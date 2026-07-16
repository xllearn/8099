from __future__ import annotations

import hashlib
import importlib
import json
import multiprocessing
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import app.main as main_module
from app import attachment_cache
from app.pipeline_timing import PipelineTiming


class AttachmentConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            {
                "articleattid": f"att-{index}",
                "filename": f"attachment-{index}.pdf",
                "fileext": ".pdf",
                "sortnum": index,
            }
            for index in range(6)
        ]

    @staticmethod
    def _result(row: dict[str, object]) -> dict[str, object]:
        return {
            "articleattid": row["articleattid"],
            "filename": row["filename"],
            "sortnum": row["sortnum"],
            "parse_status": "parsed_summary",
            "summary": f"summary-{row['sortnum']}",
            "evidence_items": [
                {
                    "evidence_id": f"evidence-{row['articleattid']}",
                    "source_ref": {"attachment_id": row["articleattid"]},
                }
            ],
        }

    def _run_with_probe(self, *, enabled: bool, concurrency: int) -> tuple[list[dict[str, object]], int, dict[str, int]]:
        lock = threading.Lock()
        active = 0
        maximum = 0

        def process(row, _options=None, _timing=None):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                index = int(row["sortnum"])
                time.sleep(0.01 * (len(self.rows) - index))
                return self._result(row)
            finally:
                with lock:
                    active -= 1

        env = {
            "ENABLE_CONCURRENT_ATTACHMENT_PARSE": "true" if enabled else "false",
            "ATTACHMENT_PARSE_CONCURRENCY": str(concurrency),
            "ENABLE_ATTACHMENT_TASK_PROCESS_ISOLATION": "false",
        }
        timing = PipelineTiming()
        with patch.dict(os.environ, env, clear=False), patch.object(
            main_module,
            "_database_attachment_metadata",
            side_effect=process,
        ):
            results = main_module._parse_database_attachments_bounded(self.rows, {}, timing)
        return results, maximum, timing.snapshot(finish=True)

    def test_switch_off_is_serial_and_switch_on_respects_configured_limit(self) -> None:
        serial, serial_maximum, _ = self._run_with_probe(enabled=False, concurrency=2)
        parallel, parallel_maximum, _ = self._run_with_probe(enabled=True, concurrency=2)

        self.assertEqual(serial_maximum, 1)
        self.assertGreater(parallel_maximum, 1)
        self.assertLessEqual(parallel_maximum, 2)
        self.assertEqual(parallel, serial)

    def test_configured_concurrency_above_three_is_hard_capped(self) -> None:
        _, parallel_maximum, _ = self._run_with_probe(enabled=True, concurrency=99)

        self.assertEqual(3, parallel_maximum)

    def test_parallel_completion_keeps_attachment_identity_order_and_hash(self) -> None:
        serial, _, _ = self._run_with_probe(enabled=False, concurrency=3)
        parallel, _, _ = self._run_with_probe(enabled=True, concurrency=3)

        expected_ids = [str(row["articleattid"]) for row in self.rows]
        self.assertEqual([item["articleattid"] for item in parallel], expected_ids)
        serial_hash = hashlib.sha256(json.dumps(serial, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        parallel_hash = hashlib.sha256(json.dumps(parallel, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.assertEqual(parallel_hash, serial_hash)

    def test_one_attachment_failure_is_degraded_without_cancelling_others(self) -> None:
        processed: list[str] = []
        lock = threading.Lock()

        def process(row, _options=None, _timing=None):
            attachment_id = str(row["articleattid"])
            with lock:
                processed.append(attachment_id)
            if attachment_id == "att-2":
                raise RuntimeError("fixture failure must not escape")
            return self._result(row)

        with patch.dict(
            os.environ,
            {
                "ENABLE_CONCURRENT_ATTACHMENT_PARSE": "true",
                "ATTACHMENT_PARSE_CONCURRENCY": "3",
                "ENABLE_ATTACHMENT_TASK_PROCESS_ISOLATION": "false",
            },
            clear=False,
        ), patch.object(main_module, "_database_attachment_metadata", side_effect=process):
            results = main_module._parse_database_attachments_bounded(self.rows, {}, PipelineTiming())

        self.assertCountEqual(processed, [str(row["articleattid"]) for row in self.rows])
        self.assertEqual([item["articleattid"] for item in results], [row["articleattid"] for row in self.rows])
        failed = results[2]
        self.assertEqual(failed["parse_status"], "parse_failed")
        self.assertEqual(failed["download_status"], "download_failed")
        self.assertIn("ATTACHMENT_PROCESSING_FAILED:RuntimeError", failed["warnings"])
        self.assertNotIn("fixture failure must not escape", json.dumps(failed, ensure_ascii=False))

    def test_executor_threads_are_released_after_batch(self) -> None:
        self._run_with_probe(enabled=True, concurrency=3)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if not any(thread.name.startswith("attachment-parse") for thread in threading.enumerate()):
                break
            time.sleep(0.01)
        self.assertFalse(any(thread.name.startswith("attachment-parse") for thread in threading.enumerate()))

    def test_attachment_timeout_cancels_task_without_cache_or_background_residue(self) -> None:
        lock = threading.Lock()
        active = 0
        cached_ids: list[str] = []

        def process(row, options=None, _timing=None):
            nonlocal active
            options = options or {}
            with lock:
                active += 1
            try:
                if row["articleattid"] == "att-0":
                    deadline = time.monotonic() + 0.2
                    while time.monotonic() < deadline:
                        cancel_event = options.get("_cancel_event")
                        if cancel_event is not None and cancel_event.is_set():
                            raise TimeoutError("cooperative attachment cancellation")
                        time.sleep(0.002)
                else:
                    time.sleep(0.005)
                return {**self._result(row), "_cache_write_pending": True}
            finally:
                with lock:
                    active -= 1

        def store(attachment, result):
            cached_ids.append(str(result["articleattid"]))

        with patch.dict(
            os.environ,
            {
                "ENABLE_CONCURRENT_ATTACHMENT_PARSE": "true",
                "ATTACHMENT_PARSE_CONCURRENCY": "99",
                "ATTACHMENT_TASK_TIMEOUT_SECONDS": "0.03",
                "ENABLE_ATTACHMENT_TASK_PROCESS_ISOLATION": "false",
            },
            clear=False,
        ), patch.object(
            main_module, "_database_attachment_metadata", side_effect=process
        ), patch.object(main_module, "store_cached_result", side_effect=store):
            results = main_module._parse_database_attachments_bounded(
                self.rows, {}, PipelineTiming()
            )

        self.assertEqual(0, active)
        self.assertEqual("parse_failed", results[0]["parse_status"])
        self.assertEqual("timed_out", results[0]["task_status"])
        self.assertIn("ATTACHMENT_PROCESSING_TIMEOUT", results[0]["warnings"])
        self.assertEqual(
            [f"att-{index}" for index in range(1, 6)],
            sorted(cached_ids, key=lambda value: int(value.split("-")[1])),
        )
        self.assertFalse(
            any(thread.name.startswith("attachment-parse") for thread in threading.enumerate())
        )

    def test_uncooperative_attachment_process_is_killed_at_deadline_without_temp_residue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module_name = "blocking_attachment_worker_fixture"
            (root / f"{module_name}.py").write_text(
                "import time\n"
                "def run(row, options):\n"
                "    time.sleep(60)\n"
                "    return {'result': row, 'timings': {}}\n",
                encoding="utf-8",
            )
            sys.path.insert(0, str(root))
            try:
                process_module = importlib.import_module("app.attachment_task_process")
                active_before = {child.pid for child in multiprocessing.active_children()}
                started = time.monotonic()
                with patch.dict(
                    os.environ,
                    {"ATTACHMENT_TEMP_DIR": str(root / "task-temp")},
                    clear=False,
                ), self.assertRaises(process_module.AttachmentTaskProcessTimeout):
                    process_module.run_attachment_task_isolated(
                        {"articleattid": "att-blocked"},
                        {},
                        worker_path=f"{module_name}:run",
                        timeout_seconds=0.1,
                    )
                elapsed = time.monotonic() - started
            finally:
                sys.path.remove(str(root))
                sys.modules.pop(module_name, None)

            self.assertLess(elapsed, 2.0)
            self.assertEqual(
                active_before,
                {child.pid for child in multiprocessing.active_children()},
            )
            task_temp = root / "task-temp"
            self.assertTrue(task_temp.is_dir())
            self.assertEqual([], list(task_temp.iterdir()))

    def test_process_isolation_preserves_attachment_identity_and_releases_children(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "ENABLE_CONCURRENT_ATTACHMENT_PARSE": "true",
                "ATTACHMENT_PARSE_CONCURRENCY": "3",
                "ATTACHMENT_TASK_TIMEOUT_SECONDS": "30",
                "ENABLE_ATTACHMENT_TASK_PROCESS_ISOLATION": "true",
                "ATTACHMENT_TEMP_DIR": tmpdir,
            },
            clear=False,
        ):
            results = main_module._parse_database_attachments_bounded(
                self.rows[:3],
                {"enable_download": False},
                PipelineTiming(),
            )

        self.assertEqual(
            ["att-0", "att-1", "att-2"],
            [result["articleattid"] for result in results],
        )
        self.assertTrue(all(result["download_status"] == "metadata_only" for result in results))
        self.assertFalse(
            any(child.name == "attachment-task-process" for child in multiprocessing.active_children())
        )

    def test_attachment_cache_write_is_atomic_and_cleans_temporary_file(self) -> None:
        attachment = {
            "articleattid": "att-cache",
            "filename": "cache.pdf",
            "parse_status": "parsed_summary",
        }
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "ENABLE_ATTACHMENT_PARSE_CACHE": "true",
                "ATTACHMENT_PARSE_CACHE_DIR": tmpdir,
            },
            clear=False,
        ):
            attachment_cache.store_cached_result(attachment, attachment)
            self.assertEqual(1, len(list(Path(tmpdir).glob("*.json"))))
            self.assertEqual([], list(Path(tmpdir).glob("*.tmp")))

            with patch("app.attachment_cache.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    attachment_cache.store_cached_result(
                        {**attachment, "articleattid": "att-cache-failed"},
                        {**attachment, "articleattid": "att-cache-failed"},
                    )

            self.assertEqual([], list(Path(tmpdir).glob("*.tmp")))
            self.assertEqual(1, len(list(Path(tmpdir).glob("*.json"))))

    def test_shared_pipeline_timing_does_not_lose_concurrent_updates(self) -> None:
        timing = PipelineTiming()
        with ThreadPoolExecutor(max_workers=32) as executor:
            futures = [executor.submit(timing.add_ms, "attachment_parse_ms", 1) for _ in range(2000)]
            for future in futures:
                future.result()

        snapshot = timing.snapshot()
        self.assertEqual(snapshot["attachment_parse_ms"], 2000)
        self.assertIn("attachment_queue_ms", snapshot)
        self.assertIsNone(snapshot["attachment_queue_ms"])
        self.assertEqual(
            "not_observed",
            snapshot["observation_status"]["attachment_queue_ms"],
        )


if __name__ == "__main__":
    unittest.main()
