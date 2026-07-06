from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


class WorkflowRunStoreTests(unittest.TestCase):
    def test_workflow_run_model_serializes_p0_persistence_shell_fields(self) -> None:
        from app.core.workflow.state import (
            WorkflowBackend,
            WorkflowNode,
            WorkflowNodeStatus,
            WorkflowRun,
            WorkflowRunStatus,
        )

        run = WorkflowRun(
            run_id="run_20260703_model1",
            pack_id="pack_synthetic1",
            status=WorkflowRunStatus.RUNNING,
            backend=WorkflowBackend.DIFY_LEGACY,
            nodes=[
                WorkflowNode(
                    name="dify_legacy_workflow",
                    status=WorkflowNodeStatus.RUNNING,
                    elapsed_ms=12,
                )
            ],
            error="",
        )

        record = run.to_dict()

        self.assertEqual(record["run_id"], "run_20260703_model1")
        self.assertEqual(record["pack_id"], "pack_synthetic1")
        self.assertEqual(record["status"], "running")
        self.assertEqual(record["backend"], "dify_legacy")
        self.assertEqual(record["workflow_backend"], "dify_legacy")
        self.assertEqual(record["nodes"][0]["name"], "dify_legacy_workflow")
        self.assertEqual(record["nodes"][0]["status"], "running")
        self.assertEqual(record["nodes"][0]["elapsed_ms"], 12)
        self.assertIn("created_at", record)
        self.assertIn("started_at", record)
        self.assertIn("updated_at", record)
        self.assertIn("artifacts", record)

    def test_workflow_package_exports_persistence_shell_models(self) -> None:
        from app.core.workflow import (
            WorkflowBackend,
            WorkflowNode,
            WorkflowNodeStatus,
            WorkflowRun,
            WorkflowRunStatus,
        )

        run = WorkflowRun(
            run_id="run_20260703_export1",
            pack_id="pack_synthetic1",
            status=WorkflowRunStatus.CREATED,
            backend=WorkflowBackend.DIFY_LEGACY,
            nodes=[WorkflowNode("dify_legacy_workflow", status=WorkflowNodeStatus.PENDING)],
        )

        self.assertEqual(run.to_dict()["nodes"][0]["status"], "pending")

    def test_synthetic_evidence_pack_fixture_can_anchor_run_metadata(self) -> None:
        from app.core.workflow import WorkflowBackend, WorkflowRun, WorkflowRunStatus
        from app.core.workflow.store import WorkflowRunStore

        fixture_path = Path(__file__).resolve().parent / "fixtures" / "synthetic_evidence_pack_basic.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkflowRunStore(Path(tmpdir))
            run = WorkflowRun(
                run_id="run_20260703_fixture1",
                pack_id=fixture["pack_id"],
                status=WorkflowRunStatus.CREATED,
                backend=WorkflowBackend.DIFY_LEGACY,
            )

            store.write_run(run.to_dict())

            saved = store.read_run("run_20260703_fixture1")
            self.assertEqual(saved["pack_id"], "pack_synthetic_basic")
            self.assertEqual(saved["backend"], "dify_legacy")
            self.assertEqual(saved["status"], "created")

    def test_write_run_atomically_and_reads_legacy_backend_node(self) -> None:
        from app.core.workflow.state import WorkflowBackend, WorkflowRunStatus, make_workflow_node
        from app.core.workflow.store import WorkflowRunStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkflowRunStore(Path(tmpdir))
            record = {
                "run_id": "run_20260703_store1",
                "pack_id": "pack_public_fixture1",
                "status": WorkflowRunStatus.RUNNING.value,
                "backend": WorkflowBackend.DIFY_LEGACY.value,
                "workflow_backend": WorkflowBackend.DIFY_LEGACY.value,
                "nodes": [make_workflow_node("dify_legacy_workflow", status="running")],
                "error": "",
            }

            store.write_run(record)

            path = Path(tmpdir) / "run_20260703_store1.json"
            self.assertTrue(path.exists())
            self.assertFalse(path.with_suffix(".json.tmp").exists())

            saved = store.read_run("run_20260703_store1")
            self.assertEqual(saved["backend"], "dify_legacy")
            self.assertEqual(saved["workflow_backend"], "dify_legacy")
            self.assertEqual(saved["status"], "running")
            self.assertEqual(saved["nodes"][0]["name"], "dify_legacy_workflow")
            self.assertEqual(saved["nodes"][0]["status"], "running")
            self.assertIn("started_at", saved["nodes"][0])
            self.assertIn("elapsed_ms", saved["nodes"][0])

    def test_update_run_merges_patch_and_keeps_valid_json_under_concurrent_updates(self) -> None:
        from app.core.workflow.state import WorkflowBackend, WorkflowRunStatus
        from app.core.workflow.store import WorkflowRunStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkflowRunStore(Path(tmpdir))
            store.write_run(
                {
                    "run_id": "run_20260703_concurrent1",
                    "pack_id": "pack_synthetic1",
                    "status": WorkflowRunStatus.RUNNING.value,
                    "backend": WorkflowBackend.DIFY_LEGACY.value,
                    "workflow_backend": WorkflowBackend.DIFY_LEGACY.value,
                    "nodes": [],
                    "update_count": 0,
                }
            )

            def update_once(index: int) -> None:
                store.update_run("run_20260703_concurrent1", {"last_update": index})

            threads = [threading.Thread(target=update_once, args=(index,)) for index in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            path = Path(tmpdir) / "run_20260703_concurrent1.json"
            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            self.assertEqual(parsed["run_id"], "run_20260703_concurrent1")
            self.assertIn("last_update", parsed)

    def test_read_corrupt_run_json_raises_clear_store_error(self) -> None:
        from app.core.workflow.store import WorkflowRunStore, WorkflowRunStoreError

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run_20260703_broken1.json"
            path.write_text("{not valid json", encoding="utf-8")
            store = WorkflowRunStore(Path(tmpdir))

            with self.assertRaises(WorkflowRunStoreError) as caught:
                store.read_run("run_20260703_broken1")

            self.assertEqual(caught.exception.code, "RUN_JSON_CORRUPT")
            self.assertIn("run_20260703_broken1", caught.exception.message)

    def test_rejects_invalid_run_id_before_touching_filesystem(self) -> None:
        from app.core.workflow.store import WorkflowRunStore, WorkflowRunStoreError

        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkflowRunStore(Path(tmpdir))

            with self.assertRaises(WorkflowRunStoreError) as caught:
                store.read_run("../bad")

            self.assertEqual(caught.exception.code, "RUN_ID_INVALID")

    def test_atomic_write_retries_transient_windows_replace_lock(self) -> None:
        from app.core.workflow.state import WorkflowBackend, WorkflowRunStatus
        from app.core.workflow.store import WorkflowRunStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkflowRunStore(Path(tmpdir))
            real_replace = os.replace
            calls = {"count": 0}

            def flaky_replace(src: str | Path, dst: str | Path) -> None:
                calls["count"] += 1
                if calls["count"] == 1:
                    raise PermissionError("target file is temporarily locked")
                real_replace(src, dst)

            with patch("app.core.workflow.store.os.replace", side_effect=flaky_replace):
                store.write_run(
                    {
                        "run_id": "run_20260703_retry1",
                        "pack_id": "pack_synthetic_retry",
                        "status": WorkflowRunStatus.RUNNING.value,
                        "backend": WorkflowBackend.DIFY_LEGACY.value,
                        "workflow_backend": WorkflowBackend.DIFY_LEGACY.value,
                        "nodes": [],
                    }
                )

            self.assertEqual(calls["count"], 2)
            self.assertEqual(store.read_run("run_20260703_retry1")["pack_id"], "pack_synthetic_retry")


if __name__ == "__main__":
    unittest.main()
