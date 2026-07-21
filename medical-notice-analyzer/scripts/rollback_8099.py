from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release_support import (  # noqa: E402
    ReleaseError,
    ReleaseIntegrityError,
    ReleaseJournal,
    docker_image_loader,
    health_check,
    load_backup_manifest,
    restore_server_backup,
    run_checked,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify and restore one complete 8099 release backup"
    )
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--health-url", default="http://127.0.0.1:8099/health")
    parser.add_argument("--execute", action="store_true")
    return parser


def _plan(args: argparse.Namespace) -> dict[str, object]:
    return {
        "backup": str(args.backup),
        "execute": bool(args.execute),
        "install_root": str(args.install_root),
        "steps": ["verify_hashes", "restore", "health", "rolled_back"],
    }


def _load_journal(path: Path) -> ReleaseJournal:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseIntegrityError("release state file is unreadable") from exc
    if not isinstance(value, dict):
        raise ReleaseIntegrityError("release state file is invalid")
    return ReleaseJournal(path, value.get("release_id"), value.get("source_sha"))


def _start_restored_service(install_root: Path, manifest: dict[str, object]) -> None:
    runtime = install_root / str(manifest.get("runtime_compose") or "")
    if not runtime.is_file():
        raise ReleaseIntegrityError("restored runtime compose file is missing")
    run_checked(
        (
            "docker",
            "compose",
            "-f",
            str(install_root / "docker-compose.yml"),
            "-f",
            str(runtime),
            "up",
            "-d",
            "--no-build",
        ),
        cwd=install_root,
    )


def _verify_restored_image(manifest: dict[str, object]) -> None:
    result = run_checked(
        (
            "docker",
            "image",
            "inspect",
            str(manifest.get("image_ref") or ""),
            "--format",
            "{{ index .Config.Labels \"org.opencontainers.image.revision\" }}",
        )
    )
    revision = result.stdout.decode("utf-8", errors="replace").strip()
    if revision != manifest.get("source_sha"):
        raise ReleaseIntegrityError("restored image revision is inconsistent")


def _execute(args: argparse.Namespace) -> dict[str, object]:
    if os.name == "nt":
        raise ReleaseError("rollback execution is allowed only on the server")
    if args.state_file is None:
        raise ReleaseIntegrityError("--state-file is required for rollback execution")
    backup = args.backup.resolve(strict=True)
    install_root = args.install_root.resolve(strict=True)
    journal = _load_journal(args.state_file.resolve(strict=True))
    try:
        expected_manifest = load_backup_manifest(backup)

        def load_verified_image(image: Path) -> None:
            docker_image_loader(image)
            _verify_restored_image(expected_manifest)

        manifest = restore_server_backup(
            backup, install_root, image_loader=load_verified_image
        )
        marker = install_root / ".deployed_commit"
        if (
            not marker.is_file()
            or marker.read_text(encoding="ascii").strip()
            != manifest.get("source_sha")
        ):
            raise ReleaseIntegrityError("restored code marker is inconsistent")
        _start_restored_service(install_root, manifest)
        verification = health_check(args.health_url)
        if not 200 <= int(verification["status_code"]) < 300:
            raise ReleaseIntegrityError("restored service health check failed")
        state = journal.transition(
            "rolled_back", restored_sha=str(manifest["source_sha"])
        )
    except Exception:
        try:
            if journal.read()["state"] not in {"failed", "rolled_back"}:
                journal.fail("ROLLBACK_FAILED")
        except Exception:
            pass
        raise
    return {
        "image": manifest["image_ref"],
        "restored_sha": manifest["source_sha"],
        "state": state["state"],
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.execute:
        print(json.dumps(_plan(args), sort_keys=True))
        return 0
    try:
        result = _execute(args)
    except ReleaseError as exc:
        print(
            json.dumps(
                {"error_code": exc.__class__.__name__.upper(), "success": False},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"success": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
