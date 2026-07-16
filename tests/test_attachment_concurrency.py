from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import app.main as main_module
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
            {"ENABLE_CONCURRENT_ATTACHMENT_PARSE": "true", "ATTACHMENT_PARSE_CONCURRENCY": "3"},
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
