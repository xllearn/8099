from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import main as main_module
from app.analysis_history import AnalysisHistoryStore
from app.run_checkpoints import CheckpointCorruptionError, RunCheckpointStore


def schema_module():
    spec = importlib.util.find_spec("app.schema_migrations")
    if spec is None:
        raise AssertionError("app.schema_migrations must be implemented")
    return importlib.import_module("app.schema_migrations")


def schema_cli_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "convert_runtime_schema.py"
    if not path.exists():
        raise AssertionError("scripts/convert_runtime_schema.py must be implemented")
    spec = importlib.util.spec_from_file_location("convert_runtime_schema", path)
    if spec is None or spec.loader is None:
        raise AssertionError("schema conversion CLI must be importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def legacy_history_item(run_id: str = "run_schema_history01") -> dict:
    return {
        "run_id": run_id,
        "pack_id": "pack-schema-history",
        "status": "finished",
        "provider": "dify",
        "quality_status": "passed",
        "draft_word_export_available": True,
        "final_word_export_available": True,
        "deliverable": True,
        "needs_manual_review": False,
        "revision": 1,
        "latest_event_id": "event-schema-1",
    }


def legacy_history_event(run_id: str = "run_schema_history01") -> dict:
    return {
        "schema_version": 1,
        "event_id": "event-schema-1",
        "event_type": "run_created",
        "event_at": "2026-07-17T00:00:00.000Z",
        "run_id": run_id,
        "revision": 1,
        "item": legacy_history_item(run_id),
    }


class SchemaMigrationPureFunctionTests(unittest.TestCase):
    def test_explicit_current_versions_match_persistent_artifacts(self) -> None:
        module = schema_module()

        self.assertEqual(1, module.RUN_SCHEMA_VERSION)
        self.assertEqual(2, module.HISTORY_SCHEMA_VERSION)
        self.assertEqual(1, module.CHECKPOINT_SCHEMA_VERSION)
        self.assertEqual(1, module.REPORT_IR_SCHEMA_VERSION)

    def test_legacy_run_upgrade_is_pure_idempotent_and_deterministic(self) -> None:
        module = schema_module()
        legacy = {
            "run_id": "run_schema_run0001",
            "pack_id": "pack-schema-run",
            "status": "finished",
            "report_markdown": "# Report\n\nSupported content.",
        }
        before = copy.deepcopy(legacy)

        first = module.upgrade_run_schema(legacy)
        second = module.upgrade_run_schema(first)

        self.assertEqual(before, legacy)
        self.assertEqual(first, second)
        self.assertEqual(module.RUN_SCHEMA_VERSION, first["schema_version"])
        self.assertEqual(before["report_markdown"], first["report_markdown"])

    def test_run_downgrade_round_trip_preserves_business_fields(self) -> None:
        module = schema_module()
        current = module.upgrade_run_schema(
            {
                "run_id": "run_schema_run0002",
                "pack_id": "pack-schema-run",
                "status": "needs_manual_review",
                "workflow_run_id": "workflow-schema-2",
                "deliverable": False,
            }
        )

        legacy = module.downgrade_run_schema(current, target_version=0)
        round_trip = module.upgrade_run_schema(legacy)

        self.assertNotIn("schema_version", legacy)
        self.assertEqual(current, round_trip)
        self.assertEqual(legacy, module.downgrade_run_schema(current, target_version=0))

    def test_unknown_high_versions_are_rejected_without_mutating_input(self) -> None:
        module = schema_module()
        artifacts = (
            (module.upgrade_run_schema, {"schema_version": 99, "run_id": "run_schema_high01"}),
            (module.upgrade_report_ir_schema, {"schema_version": 99, "title": "Report"}),
            (
                module.upgrade_history_index_schema,
                {"schema_version": 99, "runs": {}},
            ),
            (
                module.upgrade_history_event_schema,
                {"schema_version": 99, "item": {}},
            ),
            (
                module.upgrade_checkpoint_schema,
                {
                    "schema_version": 99,
                    "run_id": "run_schema_high02",
                    "events": [],
                    "steps": {},
                    "recovery_requests": [],
                },
            ),
        )
        for upgrade, artifact in artifacts:
            with self.subTest(upgrade=upgrade.__name__):
                before = copy.deepcopy(artifact)
                with self.assertRaises(module.UnknownSchemaVersionError):
                    upgrade(artifact)
                self.assertEqual(before, artifact)

    def test_report_ir_upgrade_and_downgrade_do_not_change_formal_content(self) -> None:
        module = schema_module()
        legacy = {
            "title": "Schema report",
            "lead_paragraphs": ["Supported lead."],
            "sections": [
                {
                    "heading": "Core",
                    "paragraphs": ["Supported paragraph."],
                    "tables": [],
                    "highlights": [],
                }
            ],
        }

        current = module.upgrade_report_ir_schema(legacy)
        downgraded = module.downgrade_report_ir_schema(current, target_version=0)

        self.assertEqual(legacy, downgraded)
        self.assertEqual(module.REPORT_IR_SCHEMA_VERSION, current["schema_version"])

    def test_history_v1_to_v2_round_trip_preserves_run_state(self) -> None:
        module = schema_module()
        event_v1 = legacy_history_event()
        index_v1 = {
            "schema_version": 1,
            "updated_at": "",
            "last_event_id": event_v1["event_id"],
            "active_event_count": 1,
            "runs": {event_v1["run_id"]: event_v1["item"]},
        }

        event_v2 = module.upgrade_history_event_schema(event_v1)
        index_v2 = module.upgrade_history_index_schema(index_v1)
        event_round_trip = module.downgrade_history_event_schema(
            event_v2, target_version=1
        )
        index_round_trip = module.downgrade_history_index_schema(
            index_v2, target_version=1
        )

        self.assertEqual(event_v1, event_round_trip)
        self.assertEqual(index_v1, index_round_trip)
        self.assertEqual(2, event_v2["item"]["schema_version"])
        self.assertEqual(2, index_v2["runs"][event_v1["run_id"]]["schema_version"])

    def test_checkpoint_legacy_round_trip_is_deterministic(self) -> None:
        module = schema_module()
        legacy = {
            "run_id": "run_schema_checkpoint01",
            "updated_at": "",
            "events": [],
            "steps": {},
            "recovery_requests": [],
        }

        current = module.upgrade_checkpoint_schema(legacy)
        downgraded = module.downgrade_checkpoint_schema(current, target_version=0)

        self.assertEqual(legacy, downgraded)
        self.assertEqual(module.CHECKPOINT_SCHEMA_VERSION, current["schema_version"])


class SchemaIntegrationTests(unittest.TestCase):
    def test_run_write_uses_current_schema_and_unknown_file_is_preserved(self) -> None:
        module = schema_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "runs"
            run_id = "run_schema_run0003"
            with patch.object(main_module, "_analysis_run_dir", return_value=run_dir), patch.object(
                main_module, "_analysis_history_enabled", return_value=False
            ):
                main_module._write_analysis_run(
                    {
                        "run_id": run_id,
                        "pack_id": "pack-schema-run",
                        "status": "running",
                    }
                )
                path = run_dir / f"{run_id}.json"
                written = json.loads(path.read_text(encoding="utf-8"))

                unknown = {**written, "schema_version": 99}
                path.write_text(json.dumps(unknown), encoding="utf-8")
                before = path.read_bytes()
                with self.assertRaises(HTTPException) as caught:
                    main_module._read_analysis_run(run_id)
                after = path.read_bytes()

        self.assertEqual(module.RUN_SCHEMA_VERSION, written["schema_version"])
        self.assertEqual(500, caught.exception.status_code)
        self.assertEqual(before, after)

    def test_report_ir_model_defaults_current_and_rejects_unknown_high_version(self) -> None:
        module = schema_module()
        report = main_module.ReportIR.model_validate(
            {"title": "Schema report", "lead_paragraphs": ["Supported lead."]}
        )

        self.assertEqual(
            module.REPORT_IR_SCHEMA_VERSION, report.model_dump()["schema_version"]
        )
        with self.assertRaises(Exception):
            main_module.ReportIR.model_validate(
                {"schema_version": 99, "title": "Unknown report"}
            )

    def test_missing_history_index_rebuilds_mixed_v1_events_as_v2(self) -> None:
        module = schema_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            event = legacy_history_event("run_schema_history_v1")
            current_event = module.upgrade_history_event_schema(
                legacy_history_event("run_schema_history_v2")
            )
            (root / "events.jsonl").write_text(
                "".join(
                    (
                        json.dumps(event, ensure_ascii=False) + "\n",
                        json.dumps(current_event, ensure_ascii=False) + "\n",
                    )
                ),
                encoding="utf-8",
            )
            store = AnalysisHistoryStore(root, enforce_single_worker=False)

            item = store.get_run(event["run_id"])
            current_item = store.get_run(current_event["run_id"])
            index = json.loads((root / "index.json").read_text(encoding="utf-8"))

        self.assertEqual(module.HISTORY_SCHEMA_VERSION, item["schema_version"])
        self.assertEqual(
            module.HISTORY_SCHEMA_VERSION, current_item["schema_version"]
        )
        self.assertEqual(module.HISTORY_SCHEMA_VERSION, index["schema_version"])
        self.assertEqual(
            module.HISTORY_SCHEMA_VERSION,
            index["runs"][event["run_id"]]["schema_version"],
        )
        self.assertEqual(
            module.HISTORY_SCHEMA_VERSION,
            index["runs"][current_event["run_id"]]["schema_version"],
        )

    def test_corrupt_tail_and_index_recover_to_current_history_schema(self) -> None:
        module = schema_module()
        for corruption in ("tail", "index"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                event = legacy_history_event(f"run_schema_{corruption}01")
                event_bytes = (json.dumps(event, ensure_ascii=False) + "\n").encode(
                    "utf-8"
                )
                (root / "events.jsonl").write_bytes(
                    event_bytes + (b'{"broken"' if corruption == "tail" else b"")
                )
                if corruption == "index":
                    (root / "index.json").write_text('{"broken"', encoding="utf-8")
                store = AnalysisHistoryStore(root, enforce_single_worker=False)

                item = store.get_run(event["run_id"])
                index = json.loads((root / "index.json").read_text(encoding="utf-8"))
                quarantined = list((root / "corrupt").glob("*.bin"))

            self.assertEqual(module.HISTORY_SCHEMA_VERSION, item["schema_version"])
            self.assertEqual(module.HISTORY_SCHEMA_VERSION, index["schema_version"])
            self.assertEqual(1, len(quarantined))

    def test_unknown_history_index_version_is_not_overwritten_or_quarantined(self) -> None:
        module = schema_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            root.mkdir(parents=True, exist_ok=True)
            path = root / "index.json"
            path.write_text(
                json.dumps({"schema_version": 99, "runs": {}}), encoding="utf-8"
            )
            before = path.read_bytes()
            store = AnalysisHistoryStore(root, enforce_single_worker=False)

            with self.assertRaises(module.UnknownSchemaVersionError):
                store.list_runs()
            after = path.read_bytes()
            quarantine = list((root / "corrupt").glob("*"))

        self.assertEqual(before, after)
        self.assertEqual([], quarantine)

    def test_unknown_history_event_version_preserves_source_and_creates_no_index(self) -> None:
        module = schema_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            event = legacy_history_event("run_schema_history_high")
            event["schema_version"] = 99
            path = root / "events.jsonl"
            path.write_text(json.dumps(event) + "\n", encoding="utf-8")
            before = path.read_bytes()
            store = AnalysisHistoryStore(root, enforce_single_worker=False)

            with self.assertRaises(module.UnknownSchemaVersionError):
                store.list_runs()
            after = path.read_bytes()
            index_exists = (root / "index.json").exists()
            quarantine = list((root / "corrupt").glob("*"))

        self.assertEqual(before, after)
        self.assertFalse(index_exists)
        self.assertEqual([], quarantine)

    def test_structurally_invalid_history_index_is_quarantined_and_rebuilt(self) -> None:
        module = schema_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            event = legacy_history_event("run_schema_history_structure")
            (root / "events.jsonl").write_text(
                json.dumps(event) + "\n", encoding="utf-8"
            )
            (root / "index.json").write_text(
                json.dumps({"schema_version": 2, "runs": []}), encoding="utf-8"
            )
            store = AnalysisHistoryStore(root, enforce_single_worker=False)

            item = store.get_run(event["run_id"])
            index = json.loads((root / "index.json").read_text(encoding="utf-8"))
            quarantined = list((root / "corrupt").glob("*.bin"))

        self.assertEqual(module.HISTORY_SCHEMA_VERSION, item["schema_version"])
        self.assertEqual(module.HISTORY_SCHEMA_VERSION, index["schema_version"])
        self.assertEqual(1, len(quarantined))

    def test_legacy_checkpoint_file_is_read_as_current_without_rewriting_source(self) -> None:
        module = schema_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RunCheckpointStore(Path(tmpdir))
            path = store.path_for("run_schema_checkpoint02")
            path.parent.mkdir(parents=True, exist_ok=True)
            legacy = {
                "run_id": "run_schema_checkpoint02",
                "updated_at": "",
                "events": [],
                "steps": {},
                "recovery_requests": [],
            }
            path.write_text(json.dumps(legacy), encoding="utf-8")
            before = path.read_bytes()

            current = store.read("run_schema_checkpoint02")
            after = path.read_bytes()

        self.assertEqual(module.CHECKPOINT_SCHEMA_VERSION, current["schema_version"])
        self.assertEqual(before, after)

    def test_unknown_checkpoint_version_is_rejected_without_rewriting_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RunCheckpointStore(Path(tmpdir))
            run_id = "run_schema_checkpoint_high"
            path = store.path_for(run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 99,
                        "run_id": run_id,
                        "updated_at": "",
                        "events": [],
                        "steps": {},
                        "recovery_requests": [],
                    }
                ),
                encoding="utf-8",
            )
            before = path.read_bytes()

            with self.assertRaises(CheckpointCorruptionError):
                store.read(run_id)
            after = path.read_bytes()
            temporary = list(path.parent.glob("*.tmp"))

        self.assertEqual(before, after)
        self.assertEqual([], temporary)

    def test_report_ir_schema_version_does_not_change_rendered_markdown(self) -> None:
        legacy = {
            "title": "Schema report",
            "lead_paragraphs": ["Supported lead."],
            "sections": [
                {
                    "heading": "Core",
                    "paragraphs": ["Supported paragraph."],
                    "tables": [],
                    "highlights": [],
                }
            ],
        }
        current = {"schema_version": 1, **legacy}

        legacy_markdown = main_module._report_ir_to_markdown(
            main_module._report_ir_from_json(json.dumps(legacy))
        )
        current_markdown = main_module._report_ir_to_markdown(
            main_module._report_ir_from_json(json.dumps(current))
        )

        self.assertEqual(legacy_markdown, current_markdown)


class SchemaConversionCliTests(unittest.TestCase):
    def test_cli_downgrades_run_to_separate_atomic_copy(self) -> None:
        module = schema_module()
        cli = schema_cli_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "run.json"
            target = root / "run.v0.json"
            current = module.upgrade_run_schema(
                {
                    "run_id": "run_schema_cli0001",
                    "pack_id": "pack-schema-cli",
                    "status": "finished",
                    "report_markdown": "# Report\n\nSupported content.",
                }
            )
            source.write_text(json.dumps(current), encoding="utf-8")
            before = source.read_bytes()

            result = cli.main(
                [
                    "--artifact",
                    "run",
                    "--input",
                    str(source),
                    "--output",
                    str(target),
                    "--target-version",
                    "0",
                ]
            )
            converted = json.loads(target.read_text(encoding="utf-8"))
            temp_files = list(root.glob("*.tmp"))
            after_source = source.read_bytes()

        self.assertEqual(0, result)
        self.assertNotIn("schema_version", converted)
        self.assertEqual(before, after_source)
        self.assertEqual([], temp_files)

    def test_cli_converts_history_jsonl_and_round_trips_to_current(self) -> None:
        module = schema_module()
        cli = schema_cli_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "events.v2.jsonl"
            target = root / "events.v1.jsonl"
            event_v2 = module.upgrade_history_event_schema(legacy_history_event())
            source.write_text(json.dumps(event_v2) + "\n", encoding="utf-8")
            before = source.read_bytes()

            result = cli.main(
                [
                    "--artifact",
                    "history-events",
                    "--input",
                    str(source),
                    "--output",
                    str(target),
                    "--target-version",
                    "1",
                ]
            )
            event_v1 = json.loads(target.read_text(encoding="utf-8").strip())
            round_trip = module.upgrade_history_event_schema(event_v1)
            after_source = source.read_bytes()

        self.assertEqual(0, result)
        self.assertEqual(event_v2, round_trip)
        self.assertEqual(before, after_source)

    def test_cli_failure_preserves_source_and_leaves_no_partial_output(self) -> None:
        cli = schema_cli_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "events.corrupt.jsonl"
            target = root / "events.v1.jsonl"
            source.write_text('{"schema_version":2}\n{"broken"', encoding="utf-8")
            before = source.read_bytes()

            with self.assertRaises(Exception):
                cli.main(
                    [
                        "--artifact",
                        "history-events",
                        "--input",
                        str(source),
                        "--output",
                        str(target),
                        "--target-version",
                        "1",
                    ]
                )
            temp_files = list(root.glob("*.tmp"))
            after_source = source.read_bytes()
            target_exists = target.exists()

        self.assertEqual(before, after_source)
        self.assertFalse(target_exists)
        self.assertEqual([], temp_files)

    def test_cli_supports_rollback_copies_for_all_persistent_artifacts(self) -> None:
        module = schema_module()
        cli = schema_cli_module()
        artifacts = (
            (
                "report-ir",
                module.upgrade_report_ir_schema({"title": "Supported report"}),
                0,
            ),
            (
                "history-index",
                module.upgrade_history_index_schema(
                    {
                        "schema_version": 1,
                        "runs": {"run_schema_cli_index": legacy_history_item()},
                    }
                ),
                1,
            ),
            (
                "checkpoint",
                module.upgrade_checkpoint_schema(
                    {
                        "run_id": "run_schema_cli_checkpoint",
                        "updated_at": "",
                        "events": [],
                        "steps": {},
                        "recovery_requests": [],
                    }
                ),
                0,
            ),
        )
        for artifact, value, target_version in artifacts:
            with self.subTest(artifact=artifact), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                source = root / "source.json"
                target = root / "rollback.json"
                source.write_text(json.dumps(value), encoding="utf-8")
                before = source.read_bytes()

                result = cli.main(
                    [
                        "--artifact",
                        artifact,
                        "--input",
                        str(source),
                        "--output",
                        str(target),
                        "--target-version",
                        str(target_version),
                    ]
                )
                converted = json.loads(target.read_text(encoding="utf-8"))

                self.assertEqual(0, result)
                self.assertIsInstance(converted, dict)
                self.assertEqual(before, source.read_bytes())
                self.assertEqual([], list(root.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
