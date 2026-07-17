from __future__ import annotations

import importlib
import importlib.util
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from docx import Document
from fastapi.testclient import TestClient

from app import main as main_module
from app.evidence_schema import create_evidence_item, text_source_hash
from app.generation.base import ReportGenerationResult


EXPECTED_STEPS = (
    "prepare",
    "attachments",
    "evidence",
    "compact",
    "provider",
    "repair",
    "quality_gate",
    "word_publish",
)


def checkpoint_module():
    spec = importlib.util.find_spec("app.run_checkpoints")
    if spec is None:
        raise AssertionError("app.run_checkpoints must be implemented")
    return importlib.import_module("app.run_checkpoints")


class RunCheckpointStoreTests(unittest.TestCase):
    def make_store(self, root: Path):
        return checkpoint_module().RunCheckpointStore(root)

    def test_versioned_checkpoint_contract_lists_every_s4_step(self) -> None:
        module = checkpoint_module()

        self.assertEqual(1, module.RUN_CHECKPOINT_SCHEMA_VERSION)
        self.assertEqual(EXPECTED_STEPS, module.RUN_CHECKPOINT_STEPS)

    def test_checkpoint_persists_only_hashes_and_safe_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(Path(tmpdir))
            secret_input = {
                "report_markdown": "SECRET REPORT BODY",
                "attachment_text": "SECRET ATTACHMENT FULL TEXT",
                "api_key": "SECRET API KEY",
                "database_password": "SECRET DATABASE PASSWORD",
            }
            secret_output = {"report_ir": {"title": "SECRET REPORT IR"}}

            store.start("run_checkpoint0001", "provider", secret_input)
            store.complete(
                "run_checkpoint0001",
                "provider",
                secret_input,
                secret_output,
                recovery_id="recovery-1",
            )
            raw = store.path_for("run_checkpoint0001").read_text(encoding="utf-8")
            payload = json.loads(raw)

        self.assertEqual(1, payload["schema_version"])
        self.assertEqual("run_checkpoint0001", payload["run_id"])
        self.assertEqual(2, len(payload["events"]))
        self.assertNotIn("payload", raw)
        for secret in (
            "SECRET REPORT BODY",
            "SECRET ATTACHMENT FULL TEXT",
            "SECRET API KEY",
            "SECRET DATABASE PASSWORD",
            "SECRET REPORT IR",
        ):
            self.assertNotIn(secret, raw)
        for event in payload["events"]:
            self.assertRegex(event["input_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(event["recovery_condition"])
            if event["status"] == "completed":
                self.assertRegex(event["output_sha256"], r"^[0-9a-f]{64}$")

    def test_duplicate_event_is_idempotent_and_status_cannot_regress(self) -> None:
        module = checkpoint_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(Path(tmpdir))
            first = store.start("run_checkpoint0002", "compact", {"pack_id": "p1"})
            duplicate = store.start("run_checkpoint0002", "compact", {"pack_id": "p1"})
            complete = store.complete(
                "run_checkpoint0002",
                "compact",
                {"pack_id": "p1"},
                {"compact_pack_chars": 78000},
            )
            duplicate_complete = store.complete(
                "run_checkpoint0002",
                "compact",
                {"pack_id": "p1"},
                {"compact_pack_chars": 78000},
            )
            with self.assertRaises(module.CheckpointTransitionError):
                store.fail(
                    "run_checkpoint0002",
                    "compact",
                    {"pack_id": "p1"},
                    error_code="LATE_FAILURE",
                )
            snapshot = store.read("run_checkpoint0002")

        self.assertEqual(first["event_id"], duplicate["event_id"])
        self.assertEqual(complete["event_id"], duplicate_complete["event_id"])
        self.assertEqual(2, len(snapshot["events"]))
        self.assertEqual("completed", snapshot["steps"]["compact"]["status"])

    def test_concurrent_duplicate_writes_create_one_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            def write_once(_: int) -> str:
                return self.make_store(root).start(
                    "run_checkpoint0003",
                    "provider",
                    {"pack_id": "p3"},
                    recovery_id="recovery-same",
                )["event_id"]

            with ThreadPoolExecutor(max_workers=16) as executor:
                event_ids = list(executor.map(write_once, range(32)))
            snapshot = self.make_store(root).read("run_checkpoint0003")
            temp_files = list(root.rglob("*.tmp"))

        self.assertEqual(1, len(set(event_ids)))
        self.assertEqual(1, len(snapshot["events"]))
        self.assertEqual([], temp_files)

    def test_latest_trusted_checkpoint_and_resume_plan_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(Path(tmpdir))
            run_id = "run_checkpoint0004"
            for step in ("prepare", "attachments", "evidence", "compact", "provider"):
                store.complete(
                    run_id,
                    step,
                    {"step": step, "input": "stable"},
                    {"step": step, "output": "stable"},
                    allow_bootstrap=True,
                )
            first = store.resume_plan(run_id)
            second = store.resume_plan(run_id)

        self.assertEqual(first, second)
        self.assertEqual("provider", first["resumed_from"])
        self.assertEqual("repair", first["next_step"])
        self.assertEqual(
            ["prepare", "attachments", "evidence", "compact", "provider"],
            first["skipped_steps"],
        )

    def test_incomplete_provider_is_unknown_and_must_not_be_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(Path(tmpdir))
            store.start(
                "run_checkpoint0005",
                "provider",
                {"pack_id": "p5"},
                recovery_id="recovery-provider",
            )
            plan = store.resume_plan("run_checkpoint0005")

        self.assertTrue(plan["blocked"])
        self.assertEqual("RECOVERY_PROVIDER_OUTCOME_UNKNOWN", plan["error_code"])
        self.assertEqual("", plan["next_step"])

    def test_corrupt_checkpoint_is_fail_closed_and_original_is_preserved(self) -> None:
        module = checkpoint_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(Path(tmpdir))
            path = store.path_for("run_checkpoint0006")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"schema_version":1,"events":[', encoding="utf-8")
            before = path.read_bytes()

            with self.assertRaises(module.CheckpointCorruptionError):
                store.read("run_checkpoint0006")
            with self.assertRaises(module.CheckpointCorruptionError):
                store.start("run_checkpoint0006", "prepare", {"pack_id": "p6"})

            self.assertEqual(before, path.read_bytes())

    def test_checkpointed_file_publish_reuses_verified_output(self) -> None:
        module = checkpoint_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            store = self.make_store(root / "checkpoints")
            target = root / "report.docx"
            render = Mock(side_effect=lambda path: Path(path).write_bytes(b"safe-word"))

            first = module.publish_file_once(
                store,
                "run_checkpoint0007",
                {"formal_body_sha256": "a" * 64},
                target,
                render,
                validate=lambda path: Path(path).read_bytes() == b"safe-word",
            )
            second = module.publish_file_once(
                store,
                "run_checkpoint0007",
                {"formal_body_sha256": "a" * 64},
                target,
                render,
                validate=lambda path: Path(path).read_bytes() == b"safe-word",
            )

        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(1, render.call_count)
        self.assertEqual(first["output_sha256"], second["output_sha256"])

    def test_checkpoint_write_failure_does_not_block_wrapped_main_flow(self) -> None:
        callback = Mock(side_effect=OSError("disk unavailable"))
        executed: list[str] = []

        result = main_module._checkpoint_nonblocking(
            "run_checkpoint0008",
            "provider",
            callback,
            on_failure=lambda: executed.append("main-flow-continued"),
        )

        self.assertFalse(result)
        self.assertEqual(["main-flow-continued"], executed)


class RunRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main_module.app)

    @staticmethod
    def pack(pack_id: str) -> dict:
        evidence_text = "公告明确申报时间和采购范围。"
        return {
            "pack_id": pack_id,
            "primary_materials": [
                {
                    "menu_code": "project_notice",
                    "articleid": "checkpoint-article",
                    "title": "Checkpoint notice",
                    "content_text": "公告明确申报时间和采购范围。" * 80,
                    "attachments": [
                        {
                            "attachment_id": "attachment-1",
                            "filename": "rules.pdf",
                            "content_hash": "b" * 64,
                            "parsed_summary": "附件列明采购规则。",
                        }
                    ],
                }
            ],
            "auxiliary_materials": [],
            "evidence_schema_version": 2,
            "evidence_items": [
                create_evidence_item(
                    level="A",
                    kind="article_text",
                    value=evidence_text,
                    source_ref={
                        "menu_code": "project_notice",
                        "articleid": "checkpoint-article",
                        "quote": evidence_text,
                        "source_hash": text_source_hash(evidence_text),
                    },
                    mandatory=True,
                )
            ],
            "warnings": [],
        }

    @staticmethod
    def running_record(run_id: str, pack_id: str) -> dict:
        return {
            "success": True,
            "run_id": run_id,
            "pack_id": pack_id,
            "status": "running",
            "workflow_run_id": "",
            "report_title": "",
            "report_markdown": "",
            "quality_check": {"passed": None, "issues": []},
            "generation_warnings": [],
            "warnings": [],
            "remaining_issues": [],
            "version": 1,
        }

    def test_analysis_run_bootstraps_prepare_attachment_and_evidence_checkpoints(self) -> None:
        module = checkpoint_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pack = self.pack("pack_checkpoint_bootstrap")
            with patch.dict(
                main_module.os.environ,
                {
                    "ENABLE_RUN_CHECKPOINTS": "true",
                    "ANALYSIS_CHECKPOINT_DIR": str(root / "checkpoints"),
                },
                clear=False,
            ), patch.object(
                main_module, "_analysis_run_dir", return_value=root / "runs"
            ), patch.object(
                main_module, "_read_database_evidence_pack", return_value=pack
            ), patch.object(
                main_module, "_execute_analysis_run_background", return_value=None
            ):
                response = self.client.post(
                    "/analysis/run", json={"pack_id": pack["pack_id"]}
                )
                run_id = response.json()["run_id"]
                snapshot = module.RunCheckpointStore(root / "checkpoints").read(run_id)

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {"prepare": "completed", "attachments": "completed", "evidence": "completed"},
            {
                step: snapshot["steps"][step]["status"]
                for step in ("prepare", "attachments", "evidence")
            },
        )
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("公告明确申报时间", serialized)
        self.assertNotIn("附件列明采购规则", serialized)

    def test_checkpoint_switch_off_causes_zero_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pack = self.pack("pack_checkpoint_disabled")
            with patch.dict(
                main_module.os.environ,
                {
                    "ENABLE_RUN_CHECKPOINTS": "false",
                    "ANALYSIS_CHECKPOINT_DIR": str(root / "checkpoints"),
                },
                clear=False,
            ), patch.object(
                main_module, "_analysis_run_dir", return_value=root / "runs"
            ), patch.object(
                main_module, "_read_database_evidence_pack", return_value=pack
            ), patch.object(
                main_module, "_execute_analysis_run_background", return_value=None
            ):
                response = self.client.post(
                    "/analysis/run", json={"pack_id": pack["pack_id"]}
                )

            checkpoint_files = list((root / "checkpoints").glob("*"))

        self.assertEqual(200, response.status_code)
        self.assertEqual([], checkpoint_files)

    def test_enabled_background_records_compact_provider_repair_and_quality(self) -> None:
        module = checkpoint_module()
        generator = Mock()
        generator.generate.return_value = ReportGenerationResult(
            success=True,
            provider="dify",
            provider_run_id="workflow-checkpoint",
            report_title="Checkpoint report",
            report_markdown="## 核心结论\n\n公告明确申报时间和采购范围。",
            quality_check={"passed": True, "issues": []},
            metadata={"status": "finished", "workflow_run_id": "workflow-checkpoint"},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_id = "run_checkpoint0011"
            pack_id = "pack_checkpoint0011"
            pack = self.pack(pack_id)
            with patch.dict(
                main_module.os.environ,
                {
                    "ENABLE_RUN_CHECKPOINTS": "true",
                    "ANALYSIS_CHECKPOINT_DIR": str(root / "checkpoints"),
                },
                clear=False,
            ), patch.object(
                main_module, "_analysis_run_dir", return_value=root / "runs"
            ), patch.object(
                main_module, "_read_database_evidence_pack", return_value=pack
            ), patch.object(
                main_module, "_configured_report_generator", return_value=generator
            ):
                main_module._write_analysis_run(self.running_record(run_id, pack_id))
                main_module._bootstrap_analysis_checkpoints(run_id, pack)
                main_module._execute_analysis_run_background(pack_id, run_id)
                saved = main_module._read_analysis_run(run_id)
                snapshot = module.RunCheckpointStore(root / "checkpoints").read(run_id)

        self.assertIn(saved["status"], {"finished", "needs_manual_review"})
        self.assertEqual(1, generator.generate.call_count)
        for step in ("compact", "provider", "repair", "quality_gate"):
            self.assertEqual("completed", snapshot["steps"][step]["status"])

    def test_completed_provider_checkpoint_skips_provider_on_resume(self) -> None:
        module = checkpoint_module()
        provider = Mock(side_effect=AssertionError("provider must not run twice"))
        provider_result = {
            "success": True,
            "provider": "dify",
            "workflow_run_id": "workflow-complete",
            "status": "finished",
            "report_title": "Safe report",
            "report_markdown": "# Safe report\n\nSupported content.",
            "quality_check": {"passed": True, "issues": []},
        }
        record = {
            "run_id": "run_checkpoint0009",
            "pack_id": "pack-9",
            "status": "interrupted",
            "provider_invocation_state": "completed",
            "provider_result": provider_result,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store = module.RunCheckpointStore(Path(tmpdir))
            store.complete(
                record["run_id"],
                "provider",
                {"pack_id": record["pack_id"]},
                provider_result,
                allow_bootstrap=True,
            )
            with patch.object(main_module, "_analysis_checkpoint_store", return_value=store), patch.object(
                main_module, "_configured_report_generator", return_value=provider
            ):
                recovered = main_module._recover_analysis_result(record)

        provider.assert_not_called()
        self.assertEqual(provider_result, recovered["provider_result"])
        self.assertEqual("provider", recovered["resumed_from"])

    def test_recovery_after_completed_provider_finishes_without_second_provider_call(self) -> None:
        module = checkpoint_module()
        provider = Mock(side_effect=AssertionError("provider must not run twice"))
        provider_result = {
            "success": True,
            "provider": "dify",
            "workflow_run_id": "workflow-resume",
            "status": "finished",
            "report_title": "Recovered report",
            "report_markdown": "## 核心结论\n\n公告明确申报时间和采购范围。",
            "quality_check": {"passed": True, "issues": []},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_id = "run_checkpoint0012"
            pack_id = "pack_checkpoint0012"
            pack = self.pack(pack_id)
            _, provider_input = main_module._input_pipeline_stages(pack)
            provider_input = provider_input or {"pack_id": pack_id}
            store = module.RunCheckpointStore(root / "checkpoints")
            store.complete(
                run_id,
                "provider",
                provider_input,
                provider_result,
                allow_bootstrap=True,
            )
            record = {
                **self.running_record(run_id, pack_id),
                "status": "interrupted",
                "provider_invocation_state": "completed",
                "provider_result": provider_result,
            }
            with patch.dict(
                main_module.os.environ,
                {
                    "ENABLE_RUN_CHECKPOINTS": "true",
                    "ANALYSIS_CHECKPOINT_DIR": str(root / "checkpoints"),
                },
                clear=False,
            ), patch.object(
                main_module, "_analysis_run_dir", return_value=root / "runs"
            ), patch.object(
                main_module, "_read_database_evidence_pack", return_value=pack
            ), patch.object(
                main_module, "_configured_report_generator", return_value=provider
            ):
                main_module._write_analysis_run(record)
                main_module._resume_analysis_run_background(
                    run_id, "recovery-provider-complete"
                )
                saved = main_module._read_analysis_run(run_id)

        provider.assert_not_called()
        self.assertIn(saved["status"], {"finished", "needs_manual_review"})
        self.assertEqual("workflow-resume", saved["workflow_run_id"])

    def test_recovery_rejects_changed_provider_input_hash(self) -> None:
        module = checkpoint_module()
        provider_result = {
            "success": True,
            "provider": "dify",
            "workflow_run_id": "workflow-stale-input",
            "status": "finished",
            "report_title": "Recovered report",
            "report_markdown": "# Recovered report\n\nSupported content.",
            "quality_check": {"passed": True, "issues": []},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_id = "run_checkpoint0022"
            pack_id = "pack_checkpoint0022"
            original_pack = self.pack(pack_id)
            changed_pack = json.loads(json.dumps(original_pack, ensure_ascii=False))
            changed_pack["primary_materials"][0]["title"] = "Changed checkpoint notice"
            changed_value = f"{changed_pack['evidence_items'][0]['value']} changed"
            changed_pack["evidence_items"][0] = create_evidence_item(
                level="A",
                kind="article_text",
                value=changed_value,
                source_ref={
                    **changed_pack["evidence_items"][0]["source_ref"],
                    "quote": changed_value,
                    "source_hash": text_source_hash(changed_value),
                },
                mandatory=True,
            )
            provider_input = main_module._compact_evidence_pack_for_dify_uncached(
                original_pack
            )
            changed_provider_input = (
                main_module._compact_evidence_pack_for_dify_uncached(changed_pack)
            )
            self.assertNotEqual(
                module.hash_checkpoint_value(provider_input),
                module.hash_checkpoint_value(changed_provider_input),
            )
            store = module.RunCheckpointStore(root / "checkpoints")
            store.complete(
                run_id,
                "provider",
                provider_input,
                provider_result,
                allow_bootstrap=True,
            )
            record = {
                **self.running_record(run_id, pack_id),
                "status": "interrupted",
                "provider_invocation_state": "completed",
                "provider_result": provider_result,
            }
            with patch.dict(
                main_module.os.environ,
                {
                    "ENABLE_RUN_CHECKPOINTS": "true",
                    "ENABLE_COMPACT_CACHE": "false",
                    "ANALYSIS_CHECKPOINT_DIR": str(root / "checkpoints"),
                },
                clear=False,
            ), patch.object(
                main_module, "_analysis_run_dir", return_value=root / "runs"
            ), patch.object(
                main_module,
                "_read_database_evidence_pack",
                return_value=changed_pack,
            ), patch.object(
                main_module, "_configured_report_generator"
            ) as provider:
                main_module._write_analysis_run(record)
                main_module._resume_analysis_run_background(
                    run_id, "recovery-stale-input"
                )
                saved = main_module._read_analysis_run(run_id)

        provider.assert_not_called()
        self.assertEqual("interrupted", saved["status"])
        self.assertEqual(
            "RECOVERY_CHECKPOINT_CORRUPT", saved["recovery_error_code"]
        )

    def test_completed_repair_checkpoint_is_not_executed_twice_on_resume(self) -> None:
        module = checkpoint_module()
        provider_result = {
            "success": True,
            "provider": "dify",
            "workflow_run_id": "workflow-repair-resume",
            "status": "finished",
            "report_title": "Recovered report",
            "report_markdown": "## 核心结论\n\n公告明确申报时间和采购范围。",
            "quality_check": {"passed": True, "issues": []},
        }
        repaired_result = {**provider_result, "repair_count": 1, "repair_success": True}
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_id = "run_checkpoint0016"
            pack_id = "pack_checkpoint0016"
            pack = self.pack(pack_id)
            _, provider_input = main_module._input_pipeline_stages(pack)
            provider_input = provider_input or {"pack_id": pack_id}
            store = module.RunCheckpointStore(root / "checkpoints")
            store.complete(
                run_id,
                "provider",
                provider_input,
                provider_result,
                allow_bootstrap=True,
            )
            store.complete(
                run_id,
                "repair",
                provider_result,
                repaired_result,
                allow_bootstrap=True,
            )
            record = {
                **self.running_record(run_id, pack_id),
                "status": "interrupted",
                "provider_invocation_state": "completed",
                "provider_result": provider_result,
                "resume_stage": "repair",
                "resume_stage_result": repaired_result,
            }
            with patch.dict(
                main_module.os.environ,
                {
                    "ENABLE_RUN_CHECKPOINTS": "true",
                    "ANALYSIS_CHECKPOINT_DIR": str(root / "checkpoints"),
                },
                clear=False,
            ), patch.object(
                main_module, "_analysis_run_dir", return_value=root / "runs"
            ), patch.object(
                main_module, "_read_database_evidence_pack", return_value=pack
            ), patch.object(
                main_module.RepairPipeline,
                "run",
                side_effect=AssertionError("repair must not run twice"),
            ) as repair:
                main_module._write_analysis_run(record)
                main_module._resume_analysis_run_background(
                    run_id, "recovery-repair-complete"
                )
                saved = main_module._read_analysis_run(run_id)

        repair.assert_not_called()
        self.assertIn(saved["status"], {"finished", "needs_manual_review"})
        self.assertLessEqual(int(saved.get("repair_count") or 0), 1)

    def test_checkpoint_write_failure_does_not_block_generation(self) -> None:
        module = checkpoint_module()
        generator = Mock()
        generator.generate.return_value = ReportGenerationResult(
            success=True,
            provider="dify",
            provider_run_id="workflow-checkpoint-failure",
            report_title="Checkpoint report",
            report_markdown="## 核心结论\n\n公告明确申报时间和采购范围。",
            quality_check={"passed": True, "issues": []},
            metadata={"status": "finished"},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_id = "run_checkpoint0017"
            pack_id = "pack_checkpoint0017"
            pack = self.pack(pack_id)
            broken_store = Mock(spec=module.RunCheckpointStore)
            broken_store.start.side_effect = OSError("checkpoint disk unavailable")
            broken_store.complete.side_effect = OSError("checkpoint disk unavailable")
            broken_store.fail.side_effect = OSError("checkpoint disk unavailable")
            with patch.dict(
                main_module.os.environ,
                {"ENABLE_RUN_CHECKPOINTS": "true"},
                clear=False,
            ), patch.object(
                main_module, "_analysis_run_dir", return_value=root / "runs"
            ), patch.object(
                main_module, "_read_database_evidence_pack", return_value=pack
            ), patch.object(
                main_module, "_configured_report_generator", return_value=generator
            ), patch.object(
                main_module, "_analysis_checkpoint_store", return_value=broken_store
            ):
                main_module._write_analysis_run(self.running_record(run_id, pack_id))
                main_module._execute_analysis_run_background(pack_id, run_id)
                saved = main_module._read_analysis_run(run_id)

        self.assertEqual(1, generator.generate.call_count)
        self.assertIn(saved["status"], {"finished", "needs_manual_review"})

    def test_checkpoint_generating_state_remains_watchdog_eligible(self) -> None:
        record = {
            **self.running_record("run_checkpoint0018", "pack_checkpoint0018"),
            "status": "generating",
            "created_at": "2026-01-01 00:00:00",
        }
        with patch.object(
            main_module, "_analysis_watchdog_timeout_seconds", return_value=1
        ), patch.object(
            main_module, "_read_database_evidence_pack", return_value=self.pack(record["pack_id"])
        ), patch.object(
            main_module, "_save_analysis_timeout_fallback_if_running"
        ) as fallback, patch.object(
            main_module, "_read_analysis_run", return_value={**record, "status": "needs_manual_review"}
        ):
            main_module._maybe_finalize_timed_out_analysis_run(record)

        fallback.assert_called_once()

    def test_unknown_provider_outcome_is_fail_closed_by_recovery_api(self) -> None:
        module = checkpoint_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_id = "run_checkpoint0013"
            pack_id = "pack_checkpoint0013"
            store = module.RunCheckpointStore(root / "checkpoints")
            store.start(run_id, "provider", {"pack_id": pack_id})
            record = {
                **self.running_record(run_id, pack_id),
                "status": "interrupted",
                "provider_invocation_state": "started",
            }
            with patch.dict(
                main_module.os.environ,
                {
                    "ENABLE_RUN_CHECKPOINTS": "true",
                    "ENABLE_RUN_RECOVERY": "true",
                    "ANALYSIS_CHECKPOINT_DIR": str(root / "checkpoints"),
                },
                clear=False,
            ), patch.object(
                main_module, "_analysis_run_dir", return_value=root / "runs"
            ), patch.object(
                main_module, "_configured_report_generator"
            ) as provider:
                main_module._write_analysis_run(record)
                response = self.client.post(
                    f"/analysis/runs/{run_id}/recover",
                    json={"recovery_id": "unknown-provider-outcome"},
                )

        self.assertEqual(409, response.status_code)
        self.assertEqual(
            "RECOVERY_PROVIDER_OUTCOME_UNKNOWN", response.json()["error"]["code"]
        )
        provider.assert_not_called()

    def test_completed_provider_marker_without_checkpoint_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_id = "run_checkpoint0019"
            pack_id = "pack_checkpoint0019"
            with patch.dict(
                main_module.os.environ,
                {
                    "ENABLE_RUN_CHECKPOINTS": "true",
                    "ENABLE_RUN_RECOVERY": "true",
                    "ANALYSIS_CHECKPOINT_DIR": str(root / "checkpoints"),
                },
                clear=False,
            ), patch.object(
                main_module, "_analysis_run_dir", return_value=root / "runs"
            ), patch.object(
                main_module, "_resume_analysis_run_background"
            ) as resume:
                main_module._write_analysis_run(
                    {
                        **self.running_record(run_id, pack_id),
                        "status": "interrupted",
                        "provider_invocation_state": "completed",
                        "provider_result": {
                            "success": True,
                            "provider": "dify",
                            "workflow_run_id": "workflow-checkpoint-missing",
                        },
                    }
                )
                response = self.client.post(
                    f"/analysis/runs/{run_id}/recover",
                    json={"recovery_id": "missing-provider-checkpoint"},
                )

        self.assertEqual(409, response.status_code)
        self.assertEqual(
            "RECOVERY_PROVIDER_CHECKPOINT_MISSING",
            response.json()["error"]["code"],
        )
        resume.assert_not_called()

    def test_recovery_request_rereads_run_under_lock_before_starting_thread(self) -> None:
        module = checkpoint_module()
        run_id = "run_checkpoint0020"
        pack_id = "pack_checkpoint0020"
        stale = {
            **self.running_record(run_id, pack_id),
            "status": "interrupted",
        }
        current = {
            **stale,
            "active_recovery_id": "recovery-same",
            "recovery_status": "running",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            store = module.RunCheckpointStore(Path(tmpdir) / "checkpoints")
            with patch.dict(
                main_module.os.environ,
                {
                    "ENABLE_RUN_CHECKPOINTS": "true",
                    "ENABLE_RUN_RECOVERY": "true",
                },
                clear=False,
            ), patch.object(
                main_module, "_analysis_checkpoint_store", return_value=store
            ), patch.object(
                main_module, "_read_analysis_run", side_effect=[stale, current]
            ) as read_run, patch.object(
                main_module, "_resume_analysis_run_background"
            ) as resume:
                response = self.client.post(
                    f"/analysis/runs/{run_id}/recover",
                    json={"recovery_id": "recovery-same"},
                )

        self.assertEqual(200, response.status_code)
        self.assertEqual(2, read_run.call_count)
        resume.assert_not_called()

    def test_second_recovery_id_is_rejected_while_recovery_is_running(self) -> None:
        module = checkpoint_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_id = "run_checkpoint0021"
            pack_id = "pack_checkpoint0021"
            store = module.RunCheckpointStore(root / "checkpoints")
            record = {
                **self.running_record(run_id, pack_id),
                "status": "interrupted",
                "active_recovery_id": "recovery-first",
                "recovery_status": "running",
            }
            with patch.dict(
                main_module.os.environ,
                {
                    "ENABLE_RUN_CHECKPOINTS": "true",
                    "ENABLE_RUN_RECOVERY": "true",
                },
                clear=False,
            ), patch.object(
                main_module, "_analysis_checkpoint_store", return_value=store
            ), patch.object(
                main_module, "_read_analysis_run", return_value=record
            ), patch.object(
                main_module, "_resume_analysis_run_background"
            ) as resume:
                response = self.client.post(
                    f"/analysis/runs/{run_id}/recover",
                    json={"recovery_id": "recovery-second"},
                )

        self.assertEqual(409, response.status_code)
        self.assertEqual(
            "RUN_RECOVERY_IN_PROGRESS", response.json()["error"]["code"]
        )
        resume.assert_not_called()

    def test_terminal_recovery_request_is_idempotent_and_starts_no_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_id = "run_checkpoint0014"
            with patch.dict(
                main_module.os.environ,
                {
                    "ENABLE_RUN_CHECKPOINTS": "true",
                    "ENABLE_RUN_RECOVERY": "true",
                    "ANALYSIS_CHECKPOINT_DIR": str(root / "checkpoints"),
                },
                clear=False,
            ), patch.object(
                main_module, "_analysis_run_dir", return_value=root / "runs"
            ), patch.object(main_module.threading, "Thread") as thread:
                main_module._write_analysis_run(
                    {
                        **self.running_record(run_id, "pack-checkpoint-14"),
                        "status": "finished",
                        "report_markdown": "# Report\n\nSupported content.",
                        "quality_check": {"passed": True, "issues": []},
                    }
                )
                first = self.client.post(
                    f"/analysis/runs/{run_id}/recover",
                    json={"recovery_id": "terminal-recovery"},
                )
                second = self.client.post(
                    f"/analysis/runs/{run_id}/recover",
                    json={"recovery_id": "terminal-recovery"},
                )

        self.assertEqual(200, first.status_code)
        self.assertEqual(first.json(), second.json())
        self.assertTrue(first.json()["already_terminal"])
        thread.assert_not_called()

    def test_word_download_records_completed_checkpoint_and_reuses_file(self) -> None:
        module = checkpoint_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_id = "run_checkpoint0015"
            calls: list[str] = []

            def fake_markdown_to_docx(markdown: str, path: Path, title: str) -> None:
                calls.append(title)
                document = Document()
                document.add_paragraph(markdown)
                document.save(path)

            with patch.dict(
                main_module.os.environ,
                {
                    "ENABLE_WORD_EXPORT": "true",
                    "ENABLE_RUN_CHECKPOINTS": "true",
                    "ANALYSIS_CHECKPOINT_DIR": str(root / "checkpoints"),
                },
                clear=False,
            ), patch.object(
                main_module, "_analysis_run_dir", return_value=root / "runs"
            ), patch.object(
                main_module, "REPORT_DIR", root / "reports"
            ), patch.object(
                main_module, "_markdown_to_docx", side_effect=fake_markdown_to_docx
            ):
                main_module._write_analysis_run(
                    {
                        **self.running_record(run_id, "pack-checkpoint-15"),
                        "status": "finished",
                        "report_title": "Checkpoint Word",
                        "report_markdown": "# Checkpoint Word\n\nSupported content.",
                        "quality_check": {"passed": True, "issues": []},
                    }
                )
                first = self.client.get(f"/analysis/runs/{run_id}/download")
                second = self.client.get(f"/analysis/runs/{run_id}/download")
                snapshot = module.RunCheckpointStore(root / "checkpoints").read(run_id)
                temp_files = list(root.rglob("*.tmp"))

        self.assertEqual(200, first.status_code)
        self.assertEqual(first.content, second.content)
        self.assertEqual(1, len(calls))
        self.assertEqual("completed", snapshot["steps"]["word_publish"]["status"])
        self.assertEqual([], temp_files)

    def test_same_recovery_request_is_idempotent(self) -> None:
        module = checkpoint_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            store = module.RunCheckpointStore(Path(tmpdir))
            first = store.record_recovery_request(
                "run_checkpoint0010",
                "recovery-request-1",
            )
            second = store.record_recovery_request(
                "run_checkpoint0010",
                "recovery-request-1",
            )
            snapshot = store.read("run_checkpoint0010")

        self.assertEqual(first, second)
        self.assertEqual(1, len(snapshot["recovery_requests"]))


if __name__ == "__main__":
    unittest.main()
