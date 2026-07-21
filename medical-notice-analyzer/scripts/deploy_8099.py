from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release_support import (  # noqa: E402
    ReleaseError,
    ReleaseIntegrityError,
    ReleaseJournal,
    build_and_deploy_image,
    create_git_artifact,
    create_server_backup,
    docker_image_exporter,
    execute_release,
    health_check,
    install_code_artifact,
    sha256_file,
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build, back up, deploy, and verify one clean 8099 release"
    )
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--artifact-sha256", default="")
    parser.add_argument("--current-image", default="")
    parser.add_argument("--image", default="")
    parser.add_argument(
        "--current-runtime-compose",
        default="docker-compose.s3fix-d6777fc-runtime.yml",
    )
    parser.add_argument(
        "--runtime-compose", default="docker-compose.s4-runtime.yml"
    )
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--health-url", default="http://127.0.0.1:8099/health")
    parser.add_argument("--execute", action="store_true")
    return parser


def _plan(args: argparse.Namespace) -> dict[str, object]:
    return {
        "execute": bool(args.execute),
        "source_sha": args.sha,
        "install_root": str(args.install_root),
        "runtime_compose": args.runtime_compose,
        "steps": ["prepared", "backed_up", "deployed", "verified"],
    }


def _execute(args: argparse.Namespace) -> dict[str, object]:
    if os.name == "nt":
        raise ReleaseError("deployment execution is allowed only on the server")
    install_root = args.install_root.resolve(strict=True)
    artifact = args.artifact.resolve(strict=True) if args.artifact else None
    if artifact is None:
        raise ReleaseIntegrityError("--artifact is required for server execution")
    artifact_sha = args.artifact_sha256.strip().lower()
    if not artifact_sha or sha256_file(artifact) != artifact_sha:
        raise ReleaseIntegrityError("artifact SHA-256 is missing or mismatched")
    if not args.current_image:
        raise ReleaseIntegrityError("--current-image is required for backup")
    marker = install_root / ".deployed_commit"
    if not marker.is_file():
        raise ReleaseIntegrityError("current deployment marker is missing")
    current_sha = marker.read_text(encoding="ascii").strip()
    image_ref = args.image or f"medical-notice-analyzer:s4-{args.sha[:12]}"
    backup = args.backup or (
        install_root / "deploy_backups" / f"{_timestamp()}-s4-predeploy"
    )
    state_file = args.state_file or backup.with_name(
        f"{backup.name}-release-state.json"
    )
    journal = ReleaseJournal(state_file, f"s4-{args.sha[:12]}", args.sha)

    def backup_step() -> dict[str, object]:
        return create_server_backup(
            install_root,
            backup,
            source_sha=current_sha,
            image_ref=args.current_image,
            runtime_compose=args.current_runtime_compose,
            image_exporter=docker_image_exporter,
        )

    def deploy_step() -> dict[str, object]:
        install_code_artifact(
            artifact,
            install_root,
            expected_sha=args.sha,
            expected_artifact_sha256=artifact_sha,
        )
        return build_and_deploy_image(
            install_root=install_root,
            source_sha=args.sha,
            image_ref=image_ref,
            runtime_compose=args.runtime_compose,
        )

    state = execute_release(
        journal,
        artifact_sha256=artifact_sha,
        image_ref=image_ref,
        backup=backup_step,
        deploy=deploy_step,
        verify=lambda: health_check(args.health_url),
    )
    return {
        "backup": str(backup),
        "deployment_sha": args.sha,
        "image": image_ref,
        "state": state["state"],
        "state_file": str(state_file),
    }


def create_artifact(repo: Path, sha: str, output: Path) -> dict[str, str]:
    digest = create_git_artifact(repo, sha, output)
    return {"artifact": str(output), "artifact_sha256": digest, "source_sha": sha}


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
