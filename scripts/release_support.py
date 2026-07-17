from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


RELEASE_SCHEMA_VERSION = 1
RELEASE_STATES = (
    "prepared",
    "backed_up",
    "deployed",
    "verified",
    "rolled_back",
    "failed",
)
_TRANSITIONS = {
    "prepared": {"backed_up", "failed"},
    "backed_up": {"deployed", "rolled_back", "failed"},
    "deployed": {"verified", "rolled_back", "failed"},
    "verified": {"rolled_back", "failed"},
    "failed": {"rolled_back"},
    "rolled_back": set(),
}
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEY_RE = re.compile(
    r"(?:password|passwd|token|secret|api[_-]?key|credential|cookie)", re.I
)
_SECRET_VALUE_RE = re.compile(
    r"(?:password|passwd|token|secret|api[_-]?key|credential|cookie)\s*=", re.I
)
_RUNTIME_TOP_LEVEL = {
    ".env",
    "data",
    "reports",
    "site-cache",
    "deploy_backups",
    ".git",
    ".cache",
    ".pytest_cache",
    "__pycache__",
}
_MANAGED_PATHS_FILE = ".release-managed-paths.json"
_SOURCE_MARKER = ".release-source-sha"


class ReleaseError(RuntimeError):
    pass


class ReleaseStateError(ReleaseError):
    pass


class ReleaseSecurityError(ReleaseError):
    pass


class ReleaseIntegrityError(ReleaseError):
    pass


class ReleaseVerificationError(ReleaseError):
    pass


class ReleaseCommandError(ReleaseError):
    pass


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _validate_source_sha(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA1_RE.fullmatch(normalized):
        raise ReleaseIntegrityError("source SHA must be a full lowercase SHA-1")
    return normalized


def _validate_sha256(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ReleaseIntegrityError(f"{label} must be a lowercase SHA-256")
    return normalized


def _validate_safe_value(value: Any, path: str = "details") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if _SECRET_KEY_RE.search(key_text):
                raise ReleaseSecurityError(f"secret field is forbidden at {path}")
            _validate_safe_value(item, f"{path}.{key_text}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_safe_value(item, f"{path}[{index}]")
        return
    if isinstance(value, str):
        if _SECRET_VALUE_RE.search(value) or re.search(r"://[^/@\s]+@", value):
            raise ReleaseSecurityError(f"credential-like value is forbidden at {path}")
        return
    if value is not None and not isinstance(value, (bool, int, float)):
        raise ReleaseSecurityError(f"unsupported metadata value at {path}")


def _atomic_write_bytes(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, mode)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )
    _atomic_write_bytes(path, payload)


class ReleaseJournal:
    def __init__(self, path: Path, release_id: str, source_sha: str) -> None:
        self.path = Path(path)
        self.release_id = str(release_id or "").strip()
        if not self.release_id:
            raise ReleaseStateError("release_id is required")
        self.source_sha = _validate_source_sha(source_sha)

    def _event(self, state: str, details: Mapping[str, Any]) -> dict[str, Any]:
        _validate_safe_value(details)
        return {"at": _now_text(), "details": dict(details), "state": state}

    def prepare(self, **details: Any) -> dict[str, Any]:
        if self.path.exists():
            raise ReleaseStateError("release journal already exists")
        if "artifact_sha256" in details:
            details["artifact_sha256"] = _validate_sha256(
                details["artifact_sha256"], "artifact_sha256"
            )
        event = self._event("prepared", details)
        value = {
            "schema_version": RELEASE_SCHEMA_VERSION,
            "release_id": self.release_id,
            "source_sha": self.source_sha,
            "state": "prepared",
            "updated_at": event["at"],
            "events": [event],
        }
        _atomic_write_json(self.path, value)
        return value

    def read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReleaseStateError("release journal is unreadable") from exc
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != RELEASE_SCHEMA_VERSION
            or value.get("release_id") != self.release_id
            or value.get("source_sha") != self.source_sha
            or value.get("state") not in RELEASE_STATES
            or not isinstance(value.get("events"), list)
        ):
            raise ReleaseStateError("release journal contract is invalid")
        previous = ""
        for index, event in enumerate(value["events"]):
            if (
                not isinstance(event, dict)
                or event.get("state") not in RELEASE_STATES
                or not isinstance(event.get("at"), str)
                or not isinstance(event.get("details"), dict)
            ):
                raise ReleaseStateError("release journal event is invalid")
            _validate_safe_value(event["details"])
            event_state = event["state"]
            if index == 0:
                if event_state != "prepared":
                    raise ReleaseStateError("release journal must start prepared")
            elif event_state not in _TRANSITIONS.get(previous, set()):
                raise ReleaseStateError("release journal event sequence is invalid")
            previous = event_state
        if previous != value["state"]:
            raise ReleaseStateError("release journal state does not match its events")
        return value

    def transition(self, target: str, **details: Any) -> dict[str, Any]:
        target = str(target or "").strip()
        if target not in RELEASE_STATES:
            raise ReleaseStateError("unknown release state")
        value = self.read()
        current = value["state"]
        if target not in _TRANSITIONS[current]:
            raise ReleaseStateError(f"release transition {current}->{target} is forbidden")
        event = self._event(target, details)
        updated = json.loads(json.dumps(value, ensure_ascii=False))
        updated["state"] = target
        updated["updated_at"] = event["at"]
        updated["events"].append(event)
        _atomic_write_json(self.path, updated)
        return updated

    def fail(self, error_code: str) -> dict[str, Any]:
        code = str(error_code or "RELEASE_FAILED").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", code):
            code = "RELEASE_FAILED"
        current = self.read()
        if current["state"] == "failed":
            return current
        return self.transition("failed", error_code=code)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_name(value: str) -> str:
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in {"", ".."} for part in path.parts):
        raise ReleaseSecurityError("unsafe relative path")
    return path.as_posix()


def write_sha256sums(root: Path, names: Sequence[str]) -> Path:
    root = Path(root)
    lines: list[str] = []
    for raw_name in sorted(set(names)):
        name = _safe_relative_name(raw_name)
        path = root / name
        if not path.is_file():
            raise ReleaseIntegrityError(f"backup artifact is missing: {name}")
        lines.append(f"{sha256_file(path)}  {name}\n")
    output = root / "SHA256SUMS"
    _atomic_write_bytes(output, "".join(lines).encode("ascii"))
    return output


def verify_sha256sums(root: Path) -> dict[str, Any]:
    root = Path(root)
    checksum_path = root / "SHA256SUMS"
    try:
        lines = checksum_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ReleaseIntegrityError("SHA256SUMS is unreadable") from exc
    if not lines:
        raise ReleaseIntegrityError("SHA256SUMS is empty")
    verified = 0
    for line in lines:
        if "  " not in line:
            raise ReleaseIntegrityError("SHA256SUMS line is invalid")
        expected, raw_name = line.split("  ", 1)
        expected = _validate_sha256(expected, "backup checksum")
        name = _safe_relative_name(raw_name)
        path = root / name
        if not path.is_file() or sha256_file(path) != expected:
            raise ReleaseIntegrityError(f"backup checksum mismatch: {name}")
        verified += 1
    return {"valid": True, "verified_files": verified}


def _tar_paths(root: Path, names: Sequence[str], output: Path) -> None:
    with tarfile.open(output, "w:gz", dereference=True) as archive:
        for name in sorted(set(names)):
            path = root / name
            if path.exists():
                archive.add(path, arcname=name, recursive=True)
    try:
        os.chmod(output, 0o600)
    except OSError:
        pass


def extract_tar_safely(archive_path: Path, target: Path) -> None:
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:*") as archive:
        members = archive.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or any(part == ".." for part in path.parts)
                or member.issym()
                or member.islnk()
                or member.isdev()
            ):
                raise ReleaseSecurityError("backup archive contains an unsafe member")
        archive.extractall(target)


def _config_names(install_root: Path) -> list[str]:
    candidates = [".env", ".deployed_commit", _MANAGED_PATHS_FILE]
    candidates.extend(path.name for path in install_root.glob("docker-compose*.yml"))
    return sorted({name for name in candidates if (install_root / name).is_file()})


def _code_names(install_root: Path, config_names: Sequence[str]) -> list[str]:
    excluded = _RUNTIME_TOP_LEVEL | set(config_names)
    return sorted(
        path.name
        for path in install_root.iterdir()
        if path.name not in excluded and not path.name.startswith(".s4-")
    )


def create_server_backup(
    install_root: Path,
    backup_dir: Path,
    *,
    source_sha: str,
    image_ref: str,
    runtime_compose: str,
    image_exporter: Callable[[str, Path], None],
) -> dict[str, Any]:
    install_root = Path(install_root).resolve(strict=True)
    backup_dir = Path(backup_dir).resolve(strict=False)
    source_sha = _validate_source_sha(source_sha)
    runtime_name = _safe_relative_name(runtime_compose)
    if "/" in runtime_name or not (install_root / runtime_name).is_file():
        raise ReleaseIntegrityError("runtime compose file is missing")
    if backup_dir.exists():
        raise ReleaseIntegrityError("backup destination already exists")
    backup_dir.parent.mkdir(parents=True, exist_ok=True)
    partial = backup_dir.parent / f".{backup_dir.name}.{uuid.uuid4().hex}.partial"
    partial.mkdir(mode=0o700)
    try:
        config_names = _config_names(install_root)
        code_names = _code_names(install_root, config_names)
        data_names = [
            name for name in ("data", "reports") if (install_root / name).exists()
        ]
        _tar_paths(install_root, code_names, partial / "code.tar.gz")
        _tar_paths(install_root, config_names, partial / "config.tar.gz")
        _tar_paths(install_root, data_names, partial / "data.tar.gz")
        image_path = partial / "image.tar"
        image_exporter(str(image_ref), image_path)
        if not image_path.is_file() or image_path.stat().st_size <= 0:
            raise ReleaseIntegrityError("image backup is empty")
        try:
            os.chmod(image_path, 0o600)
        except OSError:
            pass
        manifest = {
            "schema_version": 1,
            "source_sha": source_sha,
            "image_ref": str(image_ref),
            "runtime_compose": runtime_name,
            "created_at": _now_text(),
            "code_top_level": code_names,
            "config_top_level": config_names,
            "data_top_level": data_names,
            "artifacts": {
                "code": "code.tar.gz",
                "config": "config.tar.gz",
                "data": "data.tar.gz",
                "image": "image.tar",
            },
        }
        _validate_safe_value(manifest)
        _atomic_write_json(partial / "backup-manifest.json", manifest)
        write_sha256sums(
            partial,
            (
                "code.tar.gz",
                "config.tar.gz",
                "data.tar.gz",
                "image.tar",
                "backup-manifest.json",
            ),
        )
        verify_sha256sums(partial)
        os.replace(partial, backup_dir)
        result = dict(manifest)
        result["sha256"] = sha256_file(backup_dir / "SHA256SUMS")
        return result
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _copy_contents(source: Path, target: Path, *, merge: bool) -> None:
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            if merge:
                shutil.copytree(item, destination, dirs_exist_ok=True)
            else:
                _remove_path(destination)
                shutil.copytree(item, destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)


def _read_managed_paths(install_root: Path) -> list[str]:
    path = install_root / _MANAGED_PATHS_FILE
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseIntegrityError("managed paths file is invalid") from exc
    if not isinstance(value, list):
        raise ReleaseIntegrityError("managed paths file is invalid")
    names: list[str] = []
    for item in value:
        name = _safe_relative_name(str(item))
        if "/" in name or name in _RUNTIME_TOP_LEVEL:
            raise ReleaseSecurityError("managed path is unsafe")
        names.append(name)
    return sorted(set(names))


def load_backup_manifest(backup_dir: Path) -> dict[str, Any]:
    backup_dir = Path(backup_dir).resolve(strict=True)
    verify_sha256sums(backup_dir)
    try:
        manifest = json.loads(
            (backup_dir / "backup-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseIntegrityError("backup manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ReleaseIntegrityError("backup manifest contract is invalid")
    _validate_source_sha(manifest.get("source_sha"))
    if not str(manifest.get("image_ref") or "").strip():
        raise ReleaseIntegrityError("backup image reference is missing")
    runtime = _safe_relative_name(str(manifest.get("runtime_compose") or ""))
    if "/" in runtime:
        raise ReleaseSecurityError("backup runtime compose path is unsafe")
    for field in ("code_top_level", "config_top_level", "data_top_level"):
        values = manifest.get(field)
        if not isinstance(values, list):
            raise ReleaseIntegrityError(f"backup {field} is invalid")
        for value in values:
            name = _safe_relative_name(str(value))
            if "/" in name:
                raise ReleaseSecurityError(f"backup {field} contains an unsafe path")
    _validate_safe_value(manifest)
    return manifest


def restore_server_backup(
    backup_dir: Path,
    install_root: Path,
    *,
    image_loader: Callable[[Path], None],
) -> dict[str, Any]:
    backup_dir = Path(backup_dir).resolve(strict=True)
    install_root = Path(install_root).resolve(strict=True)
    manifest = load_backup_manifest(backup_dir)
    current_managed = _read_managed_paths(install_root)
    with tempfile.TemporaryDirectory(
        prefix=".s4-restore-", dir=str(install_root.parent)
    ) as tmpdir:
        staging = Path(tmpdir)
        code = staging / "code"
        config = staging / "config"
        data = staging / "data"
        extract_tar_safely(backup_dir / "code.tar.gz", code)
        extract_tar_safely(backup_dir / "config.tar.gz", config)
        extract_tar_safely(backup_dir / "data.tar.gz", data)
        image_loader(backup_dir / "image.tar")

        code_names = {
            _safe_relative_name(str(name))
            for name in manifest.get("code_top_level", [])
        }
        for name in sorted(code_names | set(current_managed)):
            if "/" in name or name in _RUNTIME_TOP_LEVEL:
                raise ReleaseSecurityError("backup code path is unsafe")
            _remove_path(install_root / name)
        _remove_path(install_root / _MANAGED_PATHS_FILE)
        _copy_contents(code, install_root, merge=False)
        _copy_contents(config, install_root, merge=False)
        _copy_contents(data, install_root, merge=True)
    return manifest


def execute_release(
    journal: ReleaseJournal,
    *,
    artifact_sha256: str,
    image_ref: str,
    backup: Callable[[], Mapping[str, Any]],
    deploy: Callable[[], Mapping[str, Any]],
    verify: Callable[[], Mapping[str, Any]],
) -> dict[str, Any]:
    journal.prepare(
        artifact_sha256=_validate_sha256(artifact_sha256, "artifact_sha256"),
        image_ref=image_ref,
    )
    try:
        backup_result = dict(backup())
        backup_sha = _validate_sha256(backup_result.get("sha256"), "backup_sha256")
        journal.transition("backed_up", backup_sha256=backup_sha)
        deploy_result = dict(deploy())
        deployed_sha = _validate_source_sha(deploy_result.get("sha"))
        if deployed_sha != journal.source_sha:
            raise ReleaseIntegrityError("deployed SHA does not match release SHA")
        journal.transition("deployed", deployed_sha=deployed_sha)
        verification = dict(verify())
        status_code = int(verification.get("status_code") or 0)
        if status_code < 200 or status_code >= 300:
            raise ReleaseVerificationError("health verification failed")
        return journal.transition("verified", health_status=status_code)
    except Exception as exc:
        try:
            if journal.read()["state"] not in {"failed", "verified", "rolled_back"}:
                journal.fail(
                    "HEALTH_CHECK_FAILED"
                    if isinstance(exc, ReleaseVerificationError)
                    else "RELEASE_STEP_FAILED"
                )
        except Exception:
            pass
        raise


def run_checked(
    command: Sequence[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            [str(part) for part in command],
            cwd=str(cwd) if cwd else None,
            env=dict(env) if env is not None else None,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        executable = Path(str(command[0])).name if command else "command"
        return_code = getattr(exc, "returncode", "unavailable")
        raise ReleaseCommandError(
            f"{executable} failed with return code {return_code}"
        ) from exc


def create_git_artifact(repo: Path, source_sha: str, output: Path) -> str:
    repo = Path(repo).resolve(strict=True)
    output = Path(output).resolve(strict=False)
    source_sha = _validate_source_sha(source_sha)
    if output.exists():
        raise ReleaseIntegrityError("artifact output already exists")
    status = run_checked(("git", "status", "--porcelain"), cwd=repo).stdout
    if status.strip():
        raise ReleaseStateError("artifact source worktree is not clean")
    head = run_checked(("git", "rev-parse", "HEAD"), cwd=repo).stdout.decode().strip()
    if head != source_sha:
        raise ReleaseIntegrityError("artifact source HEAD does not match expected SHA")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        run_checked(
            ("git", "archive", "--format=tar", "--output", str(temporary), source_sha),
            cwd=repo,
        )
        with tarfile.open(temporary, "a") as archive:
            payload = (source_sha + "\n").encode("ascii")
            info = tarfile.TarInfo(_SOURCE_MARKER)
            info.size = len(payload)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(payload))
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return sha256_file(output)


def install_code_artifact(
    artifact: Path,
    install_root: Path,
    *,
    expected_sha: str,
    expected_artifact_sha256: str,
) -> list[str]:
    artifact = Path(artifact).resolve(strict=True)
    install_root = Path(install_root).resolve(strict=True)
    expected_sha = _validate_source_sha(expected_sha)
    if sha256_file(artifact) != _validate_sha256(
        expected_artifact_sha256, "artifact_sha256"
    ):
        raise ReleaseIntegrityError("release artifact checksum mismatch")
    with tempfile.TemporaryDirectory(
        prefix=".s4-deploy-", dir=str(install_root.parent)
    ) as tmpdir:
        staging = Path(tmpdir)
        extract_tar_safely(artifact, staging)
        marker = staging / _SOURCE_MARKER
        if not marker.is_file() or marker.read_text(encoding="ascii").strip() != expected_sha:
            raise ReleaseIntegrityError("release artifact source marker mismatch")
        marker.unlink()
        managed = sorted(
            item.name for item in staging.iterdir() if item.name not in _RUNTIME_TOP_LEVEL
        )
        if not managed:
            raise ReleaseIntegrityError("release artifact contains no managed files")
        for name in managed:
            if "/" in _safe_relative_name(name):
                raise ReleaseSecurityError("release managed path is unsafe")
            _remove_path(install_root / name)
        _copy_contents(staging, install_root, merge=False)
        _atomic_write_json(install_root / _MANAGED_PATHS_FILE, managed)
        _atomic_write_bytes(
            install_root / ".deployed_commit", (expected_sha + "\n").encode("ascii")
        )
    return managed


def docker_image_exporter(image_ref: str, output: Path) -> None:
    run_checked(("docker", "image", "save", "-o", str(output), image_ref))


def docker_image_loader(image: Path) -> None:
    run_checked(("docker", "image", "load", "-i", str(image)))


def health_check(url: str, timeout_seconds: float = 20.0) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            response.read(1024 * 1024)
    except Exception as exc:
        raise ReleaseVerificationError("health endpoint request failed") from exc
    return {"status_code": status}


def build_and_deploy_image(
    *,
    install_root: Path,
    source_sha: str,
    image_ref: str,
    runtime_compose: str,
) -> dict[str, Any]:
    install_root = Path(install_root).resolve(strict=True)
    source_sha = _validate_source_sha(source_sha)
    runtime_path = install_root / _safe_relative_name(runtime_compose)
    if not runtime_path.is_file():
        raise ReleaseIntegrityError("runtime compose file is missing after artifact install")
    run_checked(
        (
            "docker",
            "build",
            "--label",
            f"org.opencontainers.image.revision={source_sha}",
            "--tag",
            image_ref,
            str(install_root),
        )
    )
    environment = os.environ.copy()
    environment["S4_IMAGE"] = image_ref
    environment["S4_GIT_SHA"] = source_sha
    run_checked(
        (
            "docker",
            "compose",
            "-f",
            str(install_root / "docker-compose.yml"),
            "-f",
            str(runtime_path),
            "up",
            "-d",
            "--no-build",
        ),
        cwd=install_root,
        env=environment,
    )
    return {"sha": source_sha}
