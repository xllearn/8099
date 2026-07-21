from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = "a" * 40
IMAGE_REF = "medical-notice-analyzer:s4-test"


def support_module():
    return importlib.import_module("scripts.release_support")


def script_module(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    if not path.exists():
        raise AssertionError(f"scripts/{name}.py must be implemented")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"scripts/{name}.py must be importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_install_root(root: Path) -> Path:
    install = root / "install"
    (install / "app").mkdir(parents=True)
    (install / "data" / "analysis_history").mkdir(parents=True)
    (install / "reports").mkdir(parents=True)
    (install / "deploy_backups").mkdir(parents=True)
    (install / "app" / "main.py").write_text("print('old code')\n", encoding="utf-8")
    (install / "README.md").write_text("old readme\n", encoding="utf-8")
    (install / ".env").write_text("PRIVATE_VALUE=fixture-only\n", encoding="utf-8")
    (install / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (install / "docker-compose.runtime.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (install / ".deployed_commit").write_text(SOURCE_SHA + "\n", encoding="utf-8")
    (install / "data" / "analysis_history" / "events.jsonl").write_text(
        '{"event_id":"old"}\n', encoding="utf-8"
    )
    (install / "reports" / "old.docx").write_bytes(b"old-report")
    (install / "deploy_backups" / "keep.txt").write_text(
        "must not enter code backup", encoding="utf-8"
    )
    return install


class ReleaseJournalTests(unittest.TestCase):
    def test_state_machine_is_atomic_ordered_and_closed(self) -> None:
        module = support_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "release-state.json"
            journal = module.ReleaseJournal(path, "release-1", SOURCE_SHA)

            journal.prepare(artifact_sha256="b" * 64, image_ref=IMAGE_REF)
            journal.transition("backed_up", backup_sha256="c" * 64)
            journal.transition("deployed", deployed_sha=SOURCE_SHA)
            journal.transition("verified", health_status=200)
            state = json.loads(path.read_text(encoding="utf-8"))
            temporary = list(path.parent.glob("*.tmp"))

        self.assertEqual("verified", state["state"])
        self.assertEqual(
            ["prepared", "backed_up", "deployed", "verified"],
            [event["state"] for event in state["events"]],
        )
        self.assertEqual([], temporary)

    def test_state_machine_rejects_skips_and_duplicate_success(self) -> None:
        module = support_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = module.ReleaseJournal(
                Path(tmpdir) / "release-state.json", "release-2", SOURCE_SHA
            )
            journal.prepare(artifact_sha256="b" * 64, image_ref=IMAGE_REF)

            with self.assertRaises(module.ReleaseStateError):
                journal.transition("deployed", deployed_sha=SOURCE_SHA)
            journal.fail("BACKUP_FAILED")
            with self.assertRaises(module.ReleaseStateError):
                journal.transition("verified", health_status=200)
            final_state = journal.read()["state"]

        self.assertEqual("failed", final_state)

    def test_secret_fields_are_rejected_and_never_written(self) -> None:
        module = support_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "release-state.json"
            journal = module.ReleaseJournal(path, "release-3", SOURCE_SHA)
            with self.assertRaises(module.ReleaseSecurityError):
                journal.prepare(
                    artifact_sha256="b" * 64,
                    image_ref=IMAGE_REF,
                    token="must-not-be-written",
                )

            exists = path.exists()

        self.assertFalse(exists)

    def test_atomic_state_write_failure_preserves_last_valid_state(self) -> None:
        module = support_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "release-state.json"
            journal = module.ReleaseJournal(path, "release-4", SOURCE_SHA)
            journal.prepare(artifact_sha256="b" * 64, image_ref=IMAGE_REF)
            before = path.read_bytes()

            with patch.object(module.os, "replace", side_effect=OSError("injected")):
                with self.assertRaises(OSError):
                    journal.transition("backed_up", backup_sha256="c" * 64)
            after = path.read_bytes()
            temporary = list(path.parent.glob("*.tmp"))

        self.assertEqual(before, after)
        self.assertEqual([], temporary)

    def test_tampered_journal_event_chain_is_rejected(self) -> None:
        module = support_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "release-state.json"
            journal = module.ReleaseJournal(path, "release-tampered", SOURCE_SHA)
            journal.prepare(artifact_sha256="b" * 64, image_ref=IMAGE_REF)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["state"] = "verified"
            value["events"].append(
                {"at": "2026-07-17T00:00:00.000Z", "details": {}, "state": "verified"}
            )
            path.write_text(json.dumps(value), encoding="utf-8")

            with self.assertRaises(module.ReleaseStateError):
                journal.read()


class BackupAndRollbackTests(unittest.TestCase):
    @staticmethod
    def image_exporter(image_ref: str, output: Path) -> None:
        output.write_bytes(("image:" + image_ref).encode("utf-8"))

    def test_backup_covers_code_config_image_and_data_with_verified_hashes(self) -> None:
        module = support_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            install = create_install_root(root)
            backup = root / "backup"

            manifest = module.create_server_backup(
                install,
                backup,
                source_sha=SOURCE_SHA,
                image_ref=IMAGE_REF,
                runtime_compose="docker-compose.runtime.yml",
                image_exporter=self.image_exporter,
            )
            verification = module.verify_sha256sums(backup)
            files = set(path.name for path in backup.iterdir())
            manifest_text = (backup / "backup-manifest.json").read_text(
                encoding="utf-8"
            )

        self.assertEqual(
            {
                "code.tar.gz",
                "config.tar.gz",
                "data.tar.gz",
                "image.tar",
                "backup-manifest.json",
                "SHA256SUMS",
            },
            files,
        )
        self.assertTrue(verification["valid"])
        self.assertEqual(5, verification["verified_files"])
        self.assertEqual(SOURCE_SHA, manifest["source_sha"])
        self.assertNotIn("fixture-only", manifest_text)
        self.assertNotIn("PRIVATE_VALUE", manifest_text)

    def test_backup_failure_leaves_no_complete_or_partial_backup(self) -> None:
        module = support_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            install = create_install_root(root)
            backup = root / "backup"

            def fail_export(_image_ref: str, _output: Path) -> None:
                raise OSError("injected image export failure")

            with self.assertRaises(OSError):
                module.create_server_backup(
                    install,
                    backup,
                    source_sha=SOURCE_SHA,
                    image_ref=IMAGE_REF,
                    runtime_compose="docker-compose.runtime.yml",
                    image_exporter=fail_export,
                )
            partials = list(root.glob(".backup.*.partial"))

        self.assertFalse(backup.exists())
        self.assertEqual([], partials)

    def test_rollback_restores_all_components_without_deleting_new_user_data(self) -> None:
        module = support_module()
        loaded_images: list[bytes] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            install = create_install_root(root)
            backup = root / "backup"
            module.create_server_backup(
                install,
                backup,
                source_sha=SOURCE_SHA,
                image_ref=IMAGE_REF,
                runtime_compose="docker-compose.runtime.yml",
                image_exporter=self.image_exporter,
            )

            (install / "app" / "main.py").write_text("new broken code\n", encoding="utf-8")
            (install / ".env").write_text("PRIVATE_VALUE=changed\n", encoding="utf-8")
            (install / "data" / "analysis_history" / "events.jsonl").write_text(
                '{"event_id":"changed"}\n', encoding="utf-8"
            )
            new_user_file = install / "data" / "new-user-data.json"
            new_user_file.write_text('{"keep":true}\n', encoding="utf-8")

            manifest = module.restore_server_backup(
                backup,
                install,
                image_loader=lambda image: loaded_images.append(image.read_bytes()),
            )
            code_probe = (install / "app" / "main.py").read_text(encoding="utf-8")
            config_probe = (install / ".env").read_text(encoding="utf-8")
            data_probe = (
                install / "data" / "analysis_history" / "events.jsonl"
            ).read_text(encoding="utf-8")
            preserved_new_data = new_user_file.exists()

        self.assertEqual(SOURCE_SHA, manifest["source_sha"])
        self.assertEqual("print('old code')\n", code_probe)
        self.assertEqual("PRIVATE_VALUE=fixture-only\n", config_probe)
        self.assertEqual('{"event_id":"old"}\n', data_probe)
        self.assertTrue(preserved_new_data)
        self.assertEqual([("image:" + IMAGE_REF).encode("utf-8")], loaded_images)

    def test_tampered_backup_is_rejected_before_any_restore_write(self) -> None:
        module = support_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            install = create_install_root(root)
            backup = root / "backup"
            module.create_server_backup(
                install,
                backup,
                source_sha=SOURCE_SHA,
                image_ref=IMAGE_REF,
                runtime_compose="docker-compose.runtime.yml",
                image_exporter=self.image_exporter,
            )
            before = (install / "app" / "main.py").read_bytes()
            with (backup / "code.tar.gz").open("ab") as handle:
                handle.write(b"tampered")

            with self.assertRaises(module.ReleaseIntegrityError):
                module.restore_server_backup(
                    backup, install, image_loader=lambda _image: None
                )
            after = (install / "app" / "main.py").read_bytes()

        self.assertEqual(before, after)

    def test_tar_path_traversal_is_rejected(self) -> None:
        module = support_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archive = root / "unsafe.tar"
            payload = root / "payload.txt"
            payload.write_text("unsafe", encoding="utf-8")
            with tarfile.open(archive, "w") as handle:
                handle.add(payload, arcname="../escaped.txt")

            with self.assertRaises(module.ReleaseSecurityError):
                module.extract_tar_safely(archive, root / "target")

        self.assertFalse((root / "escaped.txt").exists())


class ReleaseExecutionTests(unittest.TestCase):
    def test_execute_release_reaches_verified_only_after_health(self) -> None:
        module = support_module()
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = module.ReleaseJournal(
                Path(tmpdir) / "state.json", "release-5", SOURCE_SHA
            )
            result = module.execute_release(
                journal,
                artifact_sha256="b" * 64,
                image_ref=IMAGE_REF,
                backup=lambda: calls.append("backup") or {"sha256": "c" * 64},
                deploy=lambda: calls.append("deploy") or {"sha": SOURCE_SHA},
                verify=lambda: calls.append("verify") or {"status_code": 200},
            )

        self.assertEqual(["backup", "deploy", "verify"], calls)
        self.assertEqual("verified", result["state"])

    def test_execute_release_marks_health_failure_failed_not_verified(self) -> None:
        module = support_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = module.ReleaseJournal(
                Path(tmpdir) / "state.json", "release-6", SOURCE_SHA
            )

            with self.assertRaises(module.ReleaseVerificationError):
                module.execute_release(
                    journal,
                    artifact_sha256="b" * 64,
                    image_ref=IMAGE_REF,
                    backup=lambda: {"sha256": "c" * 64},
                    deploy=lambda: {"sha": SOURCE_SHA},
                    verify=lambda: {"status_code": 503},
                )
            state = journal.read()

        self.assertEqual("failed", state["state"])
        self.assertNotIn("verified", [event["state"] for event in state["events"]])

    def test_health_check_retries_bounded_startup_failures_then_succeeds(self) -> None:
        module = support_module()

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size: int) -> bytes:
                return b'{"status":"ok"}'

        failures = [
            urllib.error.URLError("not ready"),
            urllib.error.URLError("not ready"),
            Response(),
        ]
        sleeps: list[float] = []
        with patch.object(module.urllib.request, "urlopen", side_effect=failures) as opened:
            result = module.health_check(
                "http://127.0.0.1:8099/health",
                timeout_seconds=0.1,
                max_attempts=3,
                retry_delay_seconds=0.25,
                sleeper=sleeps.append,
            )

        self.assertEqual(200, result["status_code"])
        self.assertEqual(3, opened.call_count)
        self.assertEqual([0.25, 0.25], sleeps)

    def test_health_check_stops_after_bounded_failures(self) -> None:
        module = support_module()
        sleeps: list[float] = []
        with patch.object(
            module.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("not ready"),
        ) as opened:
            with self.assertRaises(module.ReleaseVerificationError):
                module.health_check(
                    "http://127.0.0.1:8099/health",
                    timeout_seconds=0.1,
                    max_attempts=3,
                    retry_delay_seconds=0.25,
                    sleeper=sleeps.append,
                )

        self.assertEqual(3, opened.call_count)
        self.assertEqual([0.25, 0.25], sleeps)


class ReleaseCliTests(unittest.TestCase):
    def test_s4_runtime_compose_preserves_s3_gates_and_enables_recovery(self) -> None:
        path = ROOT / "docker-compose.s4-runtime.yml"
        self.assertTrue(path.is_file())
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        service = value["services"]["medical-notice-analyzer"]
        environment = service["environment"]

        self.assertEqual("${S4_IMAGE:?S4_IMAGE is required}", service["image"])
        self.assertEqual("${S4_GIT_SHA:?S4_GIT_SHA is required}", environment["APP_GIT_SHA"])
        self.assertEqual("1", environment["WEB_CONCURRENCY"])
        self.assertEqual("1", environment["UVICORN_WORKERS"])
        self.assertEqual("true", environment["ENABLE_VBP_QUALITY_GATE"])
        self.assertEqual("true", environment["ENABLE_STRICT_DELIVERY_GATE"])
        self.assertEqual("true", environment["ENABLE_RUN_CHECKPOINTS"])
        self.assertEqual("true", environment["ENABLE_RUN_RECOVERY"])

    def test_clis_are_importable_default_dry_run_and_have_no_secret_arguments(self) -> None:
        deploy = script_module("deploy_8099")
        rollback = script_module("rollback_8099")
        push = script_module("push_with_dulwich")

        deploy_args = deploy.build_parser().parse_args(
            [
                "--repo",
                str(ROOT),
                "--sha",
                SOURCE_SHA,
                "--install-root",
                "/opt/medical-notice-analyzer",
            ]
        )
        rollback_args = rollback.build_parser().parse_args(
            [
                "--backup",
                "/tmp/backup",
                "--install-root",
                "/opt/medical-notice-analyzer",
            ]
        )
        push_actions = {action.dest for action in push.build_parser()._actions}

        self.assertFalse(deploy_args.execute)
        self.assertFalse(rollback_args.execute)
        self.assertTrue({"repo", "branch", "remote"}.issubset(push_actions))
        self.assertTrue(
            {"password", "token", "api_key", "db_password"}.isdisjoint(
                push_actions
            )
        )

    def test_push_helper_stops_on_dirty_tree_and_never_prints_credentials(self) -> None:
        push = script_module("push_with_dulwich")

        class Backend:
            def __init__(self, dirty: bool) -> None:
                self.dirty = dirty
                self.pushed = False

            def is_clean(self, _repo: Path) -> bool:
                return not self.dirty

            def local_sha(self, _repo: Path, _branch: str) -> str:
                return SOURCE_SHA

            def push(self, *_args, **_kwargs) -> None:
                self.pushed = True

            def remote_sha(self, *_args, **_kwargs) -> str:
                return SOURCE_SHA

        dirty = Backend(dirty=True)
        with self.assertRaises(Exception):
            push.push_branch(
                ROOT,
                "codex/test",
                "https://github.com/example/repo.git",
                username="user",
                password="super-secret-token",
                backend=dirty,
            )
        self.assertFalse(dirty.pushed)

        clean = Backend(dirty=False)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = push.push_branch(
                ROOT,
                "codex/test",
                "https://github.com/example/repo.git",
                username="user",
                password="super-secret-token",
                backend=clean,
            )

        self.assertEqual(SOURCE_SHA, result)
        self.assertTrue(clean.pushed)
        self.assertNotIn("super-secret-token", output.getvalue())

    def test_artifact_creation_refuses_to_overwrite_existing_output(self) -> None:
        module = support_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output = root / "release.tar"
            output.write_bytes(b"existing-artifact")

            with patch.object(
                module, "run_checked", side_effect=AssertionError("git must not run")
            ):
                with self.assertRaises(module.ReleaseIntegrityError):
                    module.create_git_artifact(root, SOURCE_SHA, output)
            preserved = output.read_bytes()

        self.assertEqual(b"existing-artifact", preserved)

    def test_artifact_fsync_uses_a_writable_file_descriptor(self) -> None:
        import fcntl

        module = support_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo = root / "repo"
            repo.mkdir()
            output = root / "release.tar"

            def fake_git(command, **_kwargs):
                if command[1:3] == ("status", "--porcelain"):
                    return SimpleNamespace(stdout=b"")
                if command[1:3] == ("rev-parse", "HEAD"):
                    return SimpleNamespace(stdout=(SOURCE_SHA + "\n").encode("ascii"))
                if command[1] == "archive":
                    archive_path = Path(command[command.index("--output") + 1])
                    with tarfile.open(archive_path, "w") as archive:
                        info = tarfile.TarInfo("README.md")
                        payload = b"fixture\n"
                        info.size = len(payload)
                        archive.addfile(info, io.BytesIO(payload))
                    return SimpleNamespace(stdout=b"")
                raise AssertionError(f"unexpected git command: {command}")

            real_fsync = os.fsync

            def require_writable(fd: int) -> None:
                access_mode = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
                self.assertIn(access_mode, {os.O_WRONLY, os.O_RDWR})
                real_fsync(fd)

            with patch.object(module, "run_checked", side_effect=fake_git), patch.object(
                module.os, "fsync", side_effect=require_writable
            ):
                digest = module.create_git_artifact(repo, SOURCE_SHA, output)

        self.assertEqual(64, len(digest))

    def test_rollback_requires_exact_nonempty_image_revision(self) -> None:
        rollback = script_module("rollback_8099")
        manifest = {"image_ref": IMAGE_REF, "source_sha": SOURCE_SHA}

        with patch.object(
            rollback,
            "run_checked",
            return_value=SimpleNamespace(stdout=b""),
        ):
            with self.assertRaises(Exception):
                rollback._verify_restored_image(manifest)

    def test_rollback_rejects_bad_image_before_writing_install_root(self) -> None:
        module = support_module()
        rollback = script_module("rollback_8099")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            install = create_install_root(root)
            backup = root / "backup"
            module.create_server_backup(
                install,
                backup,
                source_sha=SOURCE_SHA,
                image_ref=IMAGE_REF,
                runtime_compose="docker-compose.runtime.yml",
                image_exporter=BackupAndRollbackTests.image_exporter,
            )
            current_code = "print('current release remains')\n"
            (install / "app" / "main.py").write_text(current_code, encoding="utf-8")
            state_path = root / "release-state.json"
            journal = module.ReleaseJournal(state_path, "release-bad-image", "b" * 40)
            journal.prepare(artifact_sha256="c" * 64, image_ref="candidate:image")
            journal.fail("HEALTH_CHECK_FAILED")
            args = SimpleNamespace(
                backup=backup,
                install_root=install,
                state_file=state_path,
                health_url="http://127.0.0.1:8099/health",
            )

            with patch.object(rollback, "docker_image_loader"), patch.object(
                rollback,
                "_verify_restored_image",
                side_effect=module.ReleaseIntegrityError("bad image revision"),
            ):
                with self.assertRaises(module.ReleaseIntegrityError):
                    rollback._execute(args)
            after = (install / "app" / "main.py").read_text(encoding="utf-8")

        self.assertEqual(current_code, after)


if __name__ == "__main__":
    unittest.main()
