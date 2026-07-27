from __future__ import annotations

import json
import os
import re
import secrets
import stat
import tempfile
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator

import portalocker
from pydantic import ValidationError

from app.deepeval_advisory.hashing import (
    canonical_json_bytes,
    canonical_sha256,
)
from app.deepeval_advisory.models import (
    AdvisoryJob,
    AdvisoryProjection,
    AdvisoryResult,
    JobStatus,
)


_MAX_STORED_JSON_BYTES = 4 * 1024 * 1024
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_JOB_ID = re.compile(r"^job_[A-Za-z0-9_-]{8,80}$")
_RUN_REF = re.compile(r"^runref_[0-9a-f]{32}$")
_EVALUATION_ID = re.compile(r"^eval_[A-Za-z0-9_-]{8,80}$")
_SAFE_OWNER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SAFE_ATTEMPT_REF = re.compile(
    r"^attempt_[A-Za-z0-9][A-Za-z0-9._-]{7,119}$"
)
_LEASE_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_CANONICAL_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)
_LAYOUT = (
    "jobs",
    "projections",
    "run-index",
    "results",
    "cache",
    "leases",
    "attempts",
    "coverage",
    "corrupt",
)
_LEASE_FIELDS = {
    "schema_version",
    "key",
    "owner",
    "lease_token",
    "acquired_at",
    "expires_at",
    "heartbeat_at",
    "request_phase",
    "evaluation_id",
    "attempt_ref",
    "issued_at",
}
_LEASE_PHASES = {"not_issued", "issued", "indeterminate"}
_INDETERMINATE_ATTEMPT_FIELDS = {
    "schema_version",
    "attempt_ref",
    "key",
    "owner",
    "evaluation_id",
    "acquired_at",
    "issued_at",
    "expired_at",
    "recovered_at",
    "request_phase",
    "error_category",
}
_ALLOWED_TRANSITIONS = {
    JobStatus.DISCOVERED: {
        JobStatus.PENDING,
        JobStatus.PAUSED,
        JobStatus.OVER_BUDGET,
        JobStatus.UNAVAILABLE,
    },
    JobStatus.PENDING: {
        JobStatus.RUNNING,
        JobStatus.PAUSED,
        JobStatus.UNAVAILABLE,
    },
    JobStatus.PAUSED: {JobStatus.PENDING},
    JobStatus.RUNNING: {
        JobStatus.COMPLETED,
        JobStatus.CACHED,
        JobStatus.PARTIAL,
        JobStatus.RETRYING,
        JobStatus.UNAVAILABLE,
        JobStatus.OVER_BUDGET,
    },
    JobStatus.RETRYING: {
        JobStatus.RUNNING,
        JobStatus.UNAVAILABLE,
    },
}
_TERMINAL_STATUSES = {
    JobStatus.COMPLETED,
    JobStatus.CACHED,
    JobStatus.PARTIAL,
    JobStatus.UNAVAILABLE,
    JobStatus.OVER_BUDGET,
    JobStatus.INDETERMINATE,
}
_UNSET = object()


class StoreError(ValueError):
    """Raised when advisory state cannot be handled safely."""


def _prepare_private_directory(path: Path) -> None:
    candidate = Path(path)
    try:
        candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise StoreError("storage directory is invalid")
        os.chmod(candidate, 0o700)
    except StoreError:
        raise
    except (OSError, TypeError, ValueError):
        raise StoreError("storage directory is invalid") from None


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except (OSError, TypeError, ValueError):
        return
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _reject_nonfinite_json(_value: str) -> None:
    raise ValueError("non-finite JSON constant")


def _open_binary_source(path: Path) -> BinaryIO:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags | no_follow)
    try:
        return os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise


def _read_json_object(
    path: Path,
    *,
    max_bytes: int = _MAX_STORED_JSON_BYTES,
) -> dict[str, Any]:
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes <= 0
    ):
        raise StoreError("stored data is invalid or unavailable")

    candidate = Path(path)
    try:
        before = candidate.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise StoreError("stored data is invalid or unavailable")
        if before.st_size <= 0 or before.st_size > max_bytes:
            raise StoreError("stored data is invalid or unavailable")

        with _open_binary_source(candidate) as source:
            opened = os.fstat(source.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise StoreError("stored data is invalid or unavailable")
            if (before.st_dev, before.st_ino) != (
                opened.st_dev,
                opened.st_ino,
            ):
                raise StoreError("stored data is invalid or unavailable")
            if opened.st_size <= 0 or opened.st_size > max_bytes:
                raise StoreError("stored data is invalid or unavailable")
            payload = source.read(max_bytes + 1)
            after = os.fstat(source.fileno())

        if (
            len(payload) != opened.st_size
            or len(payload) > max_bytes
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
        ):
            raise StoreError("stored data is invalid or unavailable")
        decoded = payload.decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            parse_constant=_reject_nonfinite_json,
        )
        if not isinstance(value, dict):
            raise StoreError("stored data is invalid or unavailable")
        return value
    except StoreError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        raise StoreError("stored data is invalid or unavailable") from None


def _target_is_absent(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    except (OSError, TypeError, ValueError):
        raise StoreError("stored data is invalid or unavailable") from None
    return False


def _validate_replace_target(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except (OSError, TypeError, ValueError):
        raise StoreError("stored data operation failed") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise StoreError("stored data operation failed")


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    candidate = Path(path)
    _prepare_private_directory(candidate.parent)
    try:
        payload = canonical_json_bytes(dict(value))
    except (TypeError, ValueError):
        raise StoreError("stored data operation failed") from None
    if not payload or len(payload) > _MAX_STORED_JSON_BYTES:
        raise StoreError("stored data operation failed")

    _validate_replace_target(candidate)
    temporary: Path | None = None
    descriptor: int | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{candidate.name}.",
            suffix=".tmp",
            dir=candidate.parent,
        )
        temporary = Path(temporary_name)
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            written = handle.write(payload)
            if written != len(payload):
                raise OSError("short JSON write")
            handle.flush()
            os.fsync(handle.fileno())
        _validate_replace_target(candidate)
        os.replace(temporary, candidate)
        temporary = None
        _fsync_directory(candidate.parent)
    except StoreError:
        raise
    except (OSError, TypeError, ValueError):
        raise StoreError("stored data operation failed") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _exclusive_create_json(path: Path, value: Mapping[str, Any]) -> None:
    candidate = Path(path)
    _prepare_private_directory(candidate.parent)
    try:
        payload = canonical_json_bytes(dict(value))
    except (TypeError, ValueError):
        raise StoreError("stored data operation failed") from None
    if not payload or len(payload) > _MAX_STORED_JSON_BYTES:
        raise StoreError("stored data operation failed")

    guard = candidate.parent / f".{candidate.name}.exclusive.lock"
    temporary_prefix = f".{candidate.name}.exclusive."
    with _portalocker_guard(guard):
        removed_orphan = False
        try:
            for orphan in candidate.parent.glob(
                f"{temporary_prefix}*.tmp"
            ):
                info = orphan.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(
                    info.st_mode
                ):
                    raise StoreError("stored data operation failed")
                orphan.unlink()
                removed_orphan = True
            if removed_orphan:
                _fsync_directory(candidate.parent)
            if not _target_is_absent(candidate):
                raise FileExistsError(str(candidate))
        except FileExistsError:
            raise
        except StoreError:
            raise
        except (OSError, TypeError, ValueError):
            raise StoreError("stored data operation failed") from None

        descriptor: int | None = None
        temporary: Path | None = None
        removed_temporary = False
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=temporary_prefix,
                suffix=".tmp",
                dir=candidate.parent,
            )
            temporary = Path(temporary_name)
            os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                written = handle.write(payload)
                if written != len(payload):
                    raise OSError("short JSON write")
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, candidate)
            _fsync_directory(candidate.parent)
        except FileExistsError:
            raise
        except (OSError, TypeError, ValueError):
            raise StoreError("stored data operation failed") from None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                    removed_temporary = True
                except OSError:
                    pass
            if removed_temporary:
                _fsync_directory(candidate.parent)


def _open_private_guard_file(path: Path) -> BinaryIO:
    _prepare_private_directory(path.parent)
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("guard is not a regular file")
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        path_info = path.lstat()
        if (
            stat.S_ISLNK(path_info.st_mode)
            or not stat.S_ISREG(path_info.st_mode)
            or (path_info.st_dev, path_info.st_ino)
            != (info.st_dev, info.st_ino)
        ):
            raise OSError("guard identity changed")
        handle = os.fdopen(descriptor, "a+b", buffering=0)
        descriptor = None
        return handle
    except (OSError, TypeError, ValueError):
        raise StoreError("storage lock is unavailable") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


@contextmanager
def _portalocker_guard(path: Path) -> Iterator[None]:
    handle = _open_private_guard_file(path)
    locked = False
    try:
        try:
            portalocker.lock(handle, portalocker.LOCK_EX)
            locked = True
            opened = os.fstat(handle.fileno())
            current = path.lstat()
            if (
                stat.S_ISLNK(current.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino)
                != (opened.st_dev, opened.st_ino)
            ):
                raise OSError("guard identity changed after lock")
        except (OSError, TypeError, ValueError, portalocker.LockException):
            raise StoreError("storage lock is unavailable") from None
        yield
    finally:
        if locked:
            try:
                portalocker.unlock(handle)
            except Exception:
                pass
        try:
            handle.close()
        except OSError:
            pass


def _validate_pattern(
    value: object,
    pattern: re.Pattern[str],
    message: str,
) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise StoreError(message)
    return value


def _validate_job_id(value: object) -> str:
    return _validate_pattern(value, _JOB_ID, "job identifier is invalid")


def _validate_run_ref(value: object) -> str:
    return _validate_pattern(value, _RUN_REF, "run reference is invalid")


def _validate_key(value: object) -> str:
    return _validate_pattern(value, _HEX_SHA256, "storage key is invalid")


def _validate_owner(value: object) -> str:
    return _validate_pattern(value, _SAFE_OWNER, "lease owner is invalid")


def _validate_lease_token(value: object) -> str:
    return _validate_pattern(
        value,
        _LEASE_TOKEN,
        "lease token is invalid",
    )


def _validate_evaluation_id(value: object) -> str:
    return _validate_pattern(
        value,
        _EVALUATION_ID,
        "evaluation identifier is invalid",
    )


def _validate_attempt_ref(value: object) -> str:
    return _validate_pattern(
        value,
        _SAFE_ATTEMPT_REF,
        "attempt reference is invalid",
    )


def _validate_report_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise StoreError("report version is invalid")
    return value


def _validate_ttl(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StoreError("lease TTL is invalid")
    try:
        timedelta(seconds=value)
    except (OverflowError, TypeError, ValueError):
        raise StoreError("lease TTL is invalid") from None
    return value


def _parse_canonical_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not _CANONICAL_TIMESTAMP.fullmatch(value):
        raise StoreError("stored data is invalid or unavailable")
    try:
        return datetime.strptime(
            value,
            "%Y-%m-%dT%H:%M:%S.%fZ",
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        raise StoreError("stored data is invalid or unavailable") from None


def _format_canonical_timestamp(value: datetime) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise StoreError("clock value is invalid")
    utc = value.astimezone(timezone.utc)
    milliseconds = utc.microsecond // 1000
    return f"{utc:%Y-%m-%dT%H:%M:%S}.{milliseconds:03d}Z"


def _coerce_job(value: AdvisoryJob | Mapping[str, Any]) -> AdvisoryJob:
    try:
        job = AdvisoryJob.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        raise StoreError("job is invalid") from None
    _validate_job_id(job.job_id)
    _validate_run_ref(job.run_ref)
    _validate_report_version(job.report_version)
    _validate_key(job.report_sha256)
    _validate_key(job.projection_sha256)
    _validate_key(job.advisory_input_sha256)
    if job.evaluation_id is not None:
        _validate_evaluation_id(job.evaluation_id)
    if job.source_evaluation_id is not None:
        _validate_evaluation_id(job.source_evaluation_id)
    return job


def _coerce_projection(
    value: AdvisoryProjection | Mapping[str, Any],
) -> AdvisoryProjection:
    try:
        projection = AdvisoryProjection.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        raise StoreError("projection is invalid") from None
    _validate_run_ref(projection.run_ref)
    _validate_report_version(projection.report_version)
    _validate_key(projection.report_sha256)
    _validate_key(projection.projection_sha256)
    payload = projection.model_dump(mode="json")
    stored_digest = payload.pop("projection_sha256")
    if canonical_sha256(payload) != stored_digest:
        raise StoreError("projection is invalid")
    return projection


def _coerce_result(
    value: AdvisoryResult | Mapping[str, Any],
) -> AdvisoryResult:
    try:
        result = AdvisoryResult.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        raise StoreError("result is invalid") from None
    _validate_evaluation_id(result.evaluation_id)
    _validate_job_id(result.job_id)
    _validate_run_ref(result.run_ref)
    _validate_key(result.advisory_input_sha256)
    _validate_key(result.metric_set_sha256)
    return result


def _strict_job_from_file(path: Path) -> AdvisoryJob:
    try:
        job = _coerce_job(_read_json_object(path))
    except StoreError:
        raise StoreError("stored data is invalid or unavailable") from None
    if path.name not in {f"{job.job_id}.json", "latest.json"}:
        raise StoreError("stored data is invalid or unavailable")
    return job


def _strict_projection_from_file(path: Path) -> AdvisoryProjection:
    try:
        projection = _coerce_projection(_read_json_object(path))
    except StoreError:
        raise StoreError("stored data is invalid or unavailable") from None
    if path.name != f"{projection.projection_sha256}.json":
        raise StoreError("stored data is invalid or unavailable")
    return projection


def _strict_result_from_file(path: Path) -> AdvisoryResult:
    try:
        result = _coerce_result(_read_json_object(path))
    except StoreError:
        raise StoreError("stored data is invalid or unavailable") from None
    if path.name != f"{result.job_id}.json":
        raise StoreError("stored data is invalid or unavailable")
    return result


class AdvisoryStore:
    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(root)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        _prepare_private_directory(self.root)
        for name in _LAYOUT:
            _prepare_private_directory(self.root / name)

    def _now_datetime(self) -> datetime:
        try:
            value = self._clock()
        except Exception:
            raise StoreError("clock value is invalid") from None
        _format_canonical_timestamp(value)
        return value.astimezone(timezone.utc)

    def _now(self) -> str:
        return _format_canonical_timestamp(self._now_datetime())

    def _job_path(self, job_id: object) -> Path:
        return self.root / "jobs" / f"{_validate_job_id(job_id)}.json"

    def _result_path(self, job_id: object) -> Path:
        return self.root / "results" / f"{_validate_job_id(job_id)}.json"

    def _projection_path(self, key: object) -> Path:
        return self.root / "projections" / f"{_validate_key(key)}.json"

    def _lease_path(self, key: object) -> Path:
        return self.root / "leases" / f"{_validate_key(key)}.json"

    def _lease_guard_path(self, key: object) -> Path:
        return self.root / "leases" / f"{_validate_key(key)}.lock"

    def _job_guard_path(self, job_id: object) -> Path:
        return self.root / "jobs" / f".{_validate_job_id(job_id)}.lock"

    def _index_directory(self, run_ref: object, report_version: object) -> Path:
        safe_run_ref = _validate_run_ref(run_ref)
        safe_version = _validate_report_version(report_version)
        return (
            self.root
            / "run-index"
            / safe_run_ref
            / f"v{safe_version:08d}"
        )

    def create_job(
        self,
        value: AdvisoryJob | Mapping[str, Any],
    ) -> AdvisoryJob:
        job = _coerce_job(value)
        path = self._job_path(job.job_id)
        try:
            _exclusive_create_json(path, job.model_dump(mode="json"))
            stored = job
        except FileExistsError:
            stored = self.read_job(job.job_id)
            if stored != job:
                raise StoreError(
                    "job already exists with different content"
                ) from None
        self._record_run_index(stored)
        return stored

    def read_job(self, job_id: str) -> AdvisoryJob:
        return _strict_job_from_file(self._job_path(job_id))

    def transition_job(
        self,
        job_id: str,
        status: JobStatus | str,
        *,
        evaluation_id: str | None | object = _UNSET,
        source_evaluation_id: str | None | object = _UNSET,
        error_category: str | None | object = _UNSET,
    ) -> AdvisoryJob:
        safe_job_id = _validate_job_id(job_id)
        try:
            target_status = JobStatus(status)
        except (TypeError, ValueError):
            raise StoreError("job status is invalid") from None

        with _portalocker_guard(self._job_guard_path(safe_job_id)):
            current = self.read_job(safe_job_id)
            if target_status not in _ALLOWED_TRANSITIONS.get(
                current.status,
                set(),
            ):
                raise StoreError("job transition is not allowed")
            payload = current.model_dump(mode="json")
            payload["status"] = target_status.value
            payload["updated_at"] = self._now()
            if evaluation_id is not _UNSET:
                if evaluation_id is not None:
                    _validate_evaluation_id(evaluation_id)
                payload["evaluation_id"] = evaluation_id
            if source_evaluation_id is not _UNSET:
                if source_evaluation_id is not None:
                    _validate_evaluation_id(source_evaluation_id)
                payload["source_evaluation_id"] = source_evaluation_id
            if error_category is not _UNSET:
                if error_category is not None and not isinstance(
                    error_category,
                    str,
                ):
                    raise StoreError("error category is invalid")
                payload["error_category"] = error_category
            updated = _coerce_job(payload)
            _atomic_write_json(
                self._job_path(safe_job_id),
                updated.model_dump(mode="json"),
            )
        self._record_run_index(updated)
        return updated

    def write_projection(
        self,
        value: AdvisoryProjection | Mapping[str, Any],
    ) -> AdvisoryProjection:
        projection = _coerce_projection(value)
        path = self._projection_path(projection.projection_sha256)
        try:
            _exclusive_create_json(
                path,
                projection.model_dump(mode="json"),
            )
        except FileExistsError:
            stored = self.read_projection(projection.projection_sha256)
            if stored != projection:
                raise StoreError(
                    "projection already exists with different content"
                ) from None
        return projection

    def read_projection(self, projection_sha256: str) -> AdvisoryProjection:
        return _strict_projection_from_file(
            self._projection_path(projection_sha256)
        )

    def write_result(
        self,
        value: AdvisoryResult | Mapping[str, Any],
    ) -> AdvisoryResult:
        result = _coerce_result(value)
        path = self._result_path(result.job_id)
        try:
            _exclusive_create_json(path, result.model_dump(mode="json"))
        except FileExistsError:
            stored = self.read_result(result.job_id)
            if stored != result:
                raise StoreError(
                    "result already exists with different content"
                ) from None
        return result

    def read_result(self, job_id: str) -> AdvisoryResult:
        return _strict_result_from_file(self._result_path(job_id))

    def _record_run_index(self, job: AdvisoryJob) -> None:
        directory = self._index_directory(job.run_ref, job.report_version)
        _prepare_private_directory(directory)
        guard = directory / ".index.lock"
        with _portalocker_guard(guard):
            with _portalocker_guard(self._job_guard_path(job.job_id)):
                canonical = self.read_job(job.job_id)
                if (
                    canonical.job_id != job.job_id
                    or canonical.run_ref != job.run_ref
                    or canonical.report_version != job.report_version
                    or canonical.created_at != job.created_at
                ):
                    raise StoreError(
                        "stored data is invalid or unavailable"
                    )

                history_path = directory / f"{canonical.job_id}.json"
                if _target_is_absent(history_path):
                    try:
                        _exclusive_create_json(
                            history_path,
                            canonical.model_dump(mode="json"),
                        )
                    except FileExistsError:
                        pass
                else:
                    existing = _strict_job_from_file(history_path)
                    if (
                        existing.job_id != canonical.job_id
                        or existing.run_ref != canonical.run_ref
                        or existing.report_version
                        != canonical.report_version
                        or existing.created_at != canonical.created_at
                    ):
                        raise StoreError(
                            "stored data is invalid or unavailable"
                        )
                    if existing != canonical:
                        _atomic_write_json(
                            history_path,
                            canonical.model_dump(mode="json"),
                        )

                indexed: list[AdvisoryJob] = []
                for path in sorted(directory.glob("*.json")):
                    if path.name == "latest.json":
                        continue
                    candidate = _strict_job_from_file(path)
                    if (
                        candidate.run_ref != canonical.run_ref
                        or candidate.report_version
                        != canonical.report_version
                    ):
                        raise StoreError(
                            "stored data is invalid or unavailable"
                        )
                    indexed.append(candidate)
                if not indexed:
                    raise StoreError(
                        "stored data is invalid or unavailable"
                    )
                latest = max(
                    indexed,
                    key=lambda candidate: (
                        candidate.created_at,
                        candidate.job_id,
                    ),
                )
                _atomic_write_json(
                    directory / "latest.json",
                    latest.model_dump(mode="json"),
                )

    def read_latest_job(
        self,
        run_ref: str,
        report_version: int,
    ) -> AdvisoryJob:
        safe_run_ref = _validate_run_ref(run_ref)
        safe_version = _validate_report_version(report_version)
        path = (
            self._index_directory(safe_run_ref, safe_version)
            / "latest.json"
        )
        latest = _strict_job_from_file(path)
        if (
            latest.run_ref != safe_run_ref
            or latest.report_version != safe_version
        ):
            raise StoreError("stored data is invalid or unavailable")
        return latest

    def _validate_lease(
        self,
        value: Mapping[str, Any],
        *,
        expected_key: str,
    ) -> dict[str, Any]:
        try:
            if set(value) != _LEASE_FIELDS:
                raise StoreError("stored data is invalid or unavailable")
            if value["schema_version"] != "8099.deepeval-lease/v1":
                raise StoreError("stored data is invalid or unavailable")
            key = _validate_key(value["key"])
            if key != expected_key:
                raise StoreError("stored data is invalid or unavailable")
            _validate_owner(value["owner"])
            _validate_lease_token(value["lease_token"])
            acquired = _parse_canonical_timestamp(value["acquired_at"])
            heartbeat = _parse_canonical_timestamp(value["heartbeat_at"])
            expires = _parse_canonical_timestamp(value["expires_at"])
            phase = value["request_phase"]
            if phase not in _LEASE_PHASES:
                raise StoreError("stored data is invalid or unavailable")
            if not acquired <= heartbeat <= expires:
                raise StoreError("stored data is invalid or unavailable")

            evaluation_id = value["evaluation_id"]
            attempt_ref = value["attempt_ref"]
            issued_at = value["issued_at"]
            if phase == "not_issued":
                if any(
                    item is not None
                    for item in (evaluation_id, attempt_ref, issued_at)
                ):
                    raise StoreError(
                        "stored data is invalid or unavailable"
                    )
            else:
                _validate_evaluation_id(evaluation_id)
                _validate_attempt_ref(attempt_ref)
                issued = _parse_canonical_timestamp(issued_at)
                if not acquired <= issued <= heartbeat:
                    raise StoreError(
                        "stored data is invalid or unavailable"
                    )
            return dict(value)
        except StoreError:
            raise StoreError("stored data is invalid or unavailable") from None
        except (KeyError, TypeError, ValueError):
            raise StoreError("stored data is invalid or unavailable") from None

    def read_lease(self, key: str) -> dict[str, Any]:
        safe_key = _validate_key(key)
        value = _read_json_object(self._lease_path(safe_key))
        return self._validate_lease(value, expected_key=safe_key)

    def _new_lease(
        self,
        key: str,
        owner: str,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        now = self._now_datetime()
        acquired_at = _format_canonical_timestamp(now)
        return {
            "schema_version": "8099.deepeval-lease/v1",
            "key": key,
            "owner": owner,
            "lease_token": secrets.token_hex(16),
            "acquired_at": acquired_at,
            "expires_at": _format_canonical_timestamp(
                now + timedelta(seconds=ttl_seconds)
            ),
            "heartbeat_at": acquired_at,
            "request_phase": "not_issued",
            "evaluation_id": None,
            "attempt_ref": None,
            "issued_at": None,
        }

    def try_acquire_lease(
        self,
        key: str,
        owner: str,
        ttl_seconds: int,
    ) -> bool:
        safe_key = _validate_key(key)
        safe_owner = _validate_owner(owner)
        safe_ttl = _validate_ttl(ttl_seconds)
        lease_path = self._lease_path(safe_key)
        with _portalocker_guard(self._lease_guard_path(safe_key)):
            if _target_is_absent(lease_path):
                lease = self._new_lease(safe_key, safe_owner, safe_ttl)
                _atomic_write_json(lease_path, lease)
                return True

            lease = self._validate_lease(
                _read_json_object(lease_path),
                expected_key=safe_key,
            )
            now = self._now_datetime()
            expires = _parse_canonical_timestamp(lease["expires_at"])
            if now < expires:
                return False
            if lease["request_phase"] == "not_issued":
                replacement = self._new_lease(
                    safe_key,
                    safe_owner,
                    safe_ttl,
                )
                _atomic_write_json(lease_path, replacement)
                return True
            self._record_indeterminate_attempt(
                lease,
                recovered_at=_format_canonical_timestamp(now),
            )
            if lease["request_phase"] == "issued":
                lease["request_phase"] = "indeterminate"
                _atomic_write_json(lease_path, lease)
            return False

    def _record_indeterminate_attempt(
        self,
        lease: Mapping[str, Any],
        *,
        recovered_at: str,
    ) -> None:
        attempt_ref = _validate_attempt_ref(lease["attempt_ref"])
        safe_recovered_at = _format_canonical_timestamp(
            _parse_canonical_timestamp(recovered_at)
        )
        acquired_at = _format_canonical_timestamp(
            _parse_canonical_timestamp(lease["acquired_at"])
        )
        issued_at = _format_canonical_timestamp(
            _parse_canonical_timestamp(lease["issued_at"])
        )
        expired_at = _format_canonical_timestamp(
            _parse_canonical_timestamp(lease["expires_at"])
        )
        if not acquired_at <= issued_at <= expired_at <= safe_recovered_at:
            raise StoreError("stored data is invalid or unavailable")
        value = {
            "schema_version": "8099.deepeval-attempt/v1",
            "attempt_ref": attempt_ref,
            "key": _validate_key(lease["key"]),
            "owner": _validate_owner(lease["owner"]),
            "evaluation_id": _validate_evaluation_id(
                lease["evaluation_id"]
            ),
            "acquired_at": acquired_at,
            "issued_at": issued_at,
            "expired_at": expired_at,
            "recovered_at": safe_recovered_at,
            "request_phase": "indeterminate",
            "error_category": "outcome_unknown",
        }
        path = self.root / "attempts" / f"{attempt_ref}.json"
        try:
            _exclusive_create_json(path, value)
        except FileExistsError:
            existing = _read_json_object(path)
            if set(existing) != _INDETERMINATE_ATTEMPT_FIELDS:
                raise StoreError(
                    "stored data is invalid or unavailable"
                ) from None
            existing_recovered_at = _format_canonical_timestamp(
                _parse_canonical_timestamp(existing["recovered_at"])
            )
            if not (
                expired_at
                <= existing_recovered_at
                <= safe_recovered_at
            ):
                raise StoreError(
                    "stored data is invalid or unavailable"
                ) from None
            existing_without_recovery = dict(existing)
            existing_without_recovery.pop("recovered_at")
            value_without_recovery = dict(value)
            value_without_recovery.pop("recovered_at")
            if existing_without_recovery != value_without_recovery:
                raise StoreError(
                    "stored data is invalid or unavailable"
                ) from None

    def heartbeat_lease(
        self,
        key: str,
        owner: str,
        ttl_seconds: int,
        *,
        lease_token: str,
    ) -> dict[str, Any]:
        safe_key = _validate_key(key)
        safe_owner = _validate_owner(owner)
        safe_ttl = _validate_ttl(ttl_seconds)
        safe_lease_token = _validate_lease_token(lease_token)
        with _portalocker_guard(self._lease_guard_path(safe_key)):
            lease = self.read_lease(safe_key)
            if (
                lease["owner"] != safe_owner
                or lease["lease_token"] != safe_lease_token
            ):
                raise StoreError("lease holder does not match")
            now = self._now_datetime()
            if now >= _parse_canonical_timestamp(lease["expires_at"]):
                raise StoreError("lease is expired")
            if lease["request_phase"] == "indeterminate":
                raise StoreError("lease is indeterminate")
            lease["heartbeat_at"] = _format_canonical_timestamp(now)
            lease["expires_at"] = _format_canonical_timestamp(
                now + timedelta(seconds=safe_ttl)
            )
            validated = self._validate_lease(
                lease,
                expected_key=safe_key,
            )
            _atomic_write_json(self._lease_path(safe_key), validated)
            return validated

    def mark_request_issued(
        self,
        key: str,
        owner: str,
        evaluation_id: str,
        attempt_ref: str,
        *,
        lease_token: str,
    ) -> dict[str, Any]:
        safe_key = _validate_key(key)
        safe_owner = _validate_owner(owner)
        safe_evaluation_id = _validate_evaluation_id(evaluation_id)
        safe_attempt_ref = _validate_attempt_ref(attempt_ref)
        safe_lease_token = _validate_lease_token(lease_token)
        with _portalocker_guard(self._lease_guard_path(safe_key)):
            lease = self.read_lease(safe_key)
            if (
                lease["owner"] != safe_owner
                or lease["lease_token"] != safe_lease_token
            ):
                raise StoreError("lease holder does not match")
            now = self._now_datetime()
            if now >= _parse_canonical_timestamp(lease["expires_at"]):
                raise StoreError("lease is expired")
            if lease["request_phase"] == "issued":
                if (
                    lease["evaluation_id"] == safe_evaluation_id
                    and lease["attempt_ref"] == safe_attempt_ref
                ):
                    return lease
                raise StoreError("lease request is already issued")
            if lease["request_phase"] != "not_issued":
                raise StoreError("lease is indeterminate")
            issued_at = _format_canonical_timestamp(now)
            lease.update(
                {
                    "heartbeat_at": issued_at,
                    "request_phase": "issued",
                    "evaluation_id": safe_evaluation_id,
                    "attempt_ref": safe_attempt_ref,
                    "issued_at": issued_at,
                }
            )
            validated = self._validate_lease(
                lease,
                expected_key=safe_key,
            )
            _atomic_write_json(self._lease_path(safe_key), validated)
            return validated

    def release_lease(
        self,
        key: str,
        owner: str,
        *,
        lease_token: str,
    ) -> bool:
        safe_key = _validate_key(key)
        safe_owner = _validate_owner(owner)
        safe_lease_token = _validate_lease_token(lease_token)
        lease_path = self._lease_path(safe_key)
        with _portalocker_guard(self._lease_guard_path(safe_key)):
            if _target_is_absent(lease_path):
                return False
            lease = self.read_lease(safe_key)
            if (
                lease["owner"] != safe_owner
                or lease["lease_token"] != safe_lease_token
            ):
                raise StoreError("lease holder does not match")
            if lease["request_phase"] == "indeterminate":
                recovered_at = _format_canonical_timestamp(
                    self._now_datetime()
                )
                self._record_indeterminate_attempt(
                    lease,
                    recovered_at=recovered_at,
                )
                return False
            if lease["request_phase"] == "issued":
                recovered_at = _format_canonical_timestamp(
                    self._now_datetime()
                )
                if (
                    _parse_canonical_timestamp(recovered_at)
                    >= _parse_canonical_timestamp(lease["expires_at"])
                ):
                    self._record_indeterminate_attempt(
                        lease,
                        recovered_at=recovered_at,
                    )
                    lease["request_phase"] = "indeterminate"
                    _atomic_write_json(lease_path, lease)
                    return False
            try:
                info = lease_path.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(
                    info.st_mode
                ):
                    raise StoreError(
                        "stored data is invalid or unavailable"
                    )
                lease_path.unlink()
                _fsync_directory(lease_path.parent)
            except StoreError:
                raise
            except (OSError, TypeError, ValueError):
                raise StoreError("stored data operation failed") from None
            return True

    def record_cache_hit(
        self,
        job_id: str,
        source: AdvisoryResult | Mapping[str, Any] | None,
        *,
        evaluation_id: str,
    ) -> AdvisoryResult:
        safe_job_id = _validate_job_id(job_id)
        safe_evaluation_id = _validate_evaluation_id(evaluation_id)
        if source is None:
            raise StoreError("cached source result is invalid")
        cached_source = _coerce_result(source)
        if cached_source.status is not JobStatus.COMPLETED:
            raise StoreError("cached source result is invalid")

        with _portalocker_guard(self._job_guard_path(safe_job_id)):
            current = self.read_job(safe_job_id)
            if (
                current.advisory_input_sha256
                != cached_source.advisory_input_sha256
            ):
                raise StoreError("cached source result is invalid")

            result_value = cached_source.model_dump(mode="json")
            result_value.update(
                {
                    "evaluation_id": safe_evaluation_id,
                    "job_id": current.job_id,
                    "run_ref": current.run_ref,
                    "advisory_input_sha256": (
                        current.advisory_input_sha256
                    ),
                    "status": JobStatus.CACHED.value,
                    "created_at": current.created_at,
                }
            )
            expected_result = _coerce_result(result_value)
            if (
                current.status is JobStatus.CACHED
                and current.evaluation_id == safe_evaluation_id
                and current.source_evaluation_id
                == cached_source.evaluation_id
            ):
                cached_result = self.read_result(safe_job_id)
                if cached_result != expected_result:
                    raise StoreError(
                        "stored data is invalid or unavailable"
                    )
                cached_job = current
            elif current.status is JobStatus.RUNNING:
                job_value = current.model_dump(mode="json")
                job_value.update(
                    {
                        "status": JobStatus.CACHED.value,
                        "updated_at": self._now(),
                        "evaluation_id": safe_evaluation_id,
                        "source_evaluation_id": (
                            cached_source.evaluation_id
                        ),
                    }
                )
                cached_job = _coerce_job(job_value)
                cached_result = self.write_result(expected_result)
                _atomic_write_json(
                    self._job_path(safe_job_id),
                    cached_job.model_dump(mode="json"),
                )
            else:
                raise StoreError("job transition is not allowed")
        self._record_run_index(cached_job)
        return cached_result

    def rebuild_coverage(self) -> dict[str, Any]:
        latest: dict[tuple[str, int], AdvisoryJob] = {}
        for path in sorted((self.root / "jobs").glob("*.json")):
            job = _strict_job_from_file(path)
            identity = (job.run_ref, job.report_version)
            existing = latest.get(identity)
            if existing is None or (
                job.created_at,
                job.job_id,
            ) > (
                existing.created_at,
                existing.job_id,
            ):
                latest[identity] = job

        status_counts = {
            status.value: 0
            for status in JobStatus
        }
        for job in latest.values():
            status_counts[job.status.value] += 1
        eligible = len(latest)
        terminal = sum(
            status_counts[status.value]
            for status in _TERMINAL_STATUSES
        )
        completed = status_counts[JobStatus.COMPLETED.value]
        cached = status_counts[JobStatus.CACHED.value]
        valid_score_reports = 0
        for job in latest.values():
            if job.status not in {
                JobStatus.COMPLETED,
                JobStatus.CACHED,
            }:
                continue
            try:
                result = self.read_result(job.job_id)
            except StoreError:
                continue
            if (
                job.evaluation_id is not None
                and result.evaluation_id == job.evaluation_id
                and result.job_id == job.job_id
                and result.run_ref == job.run_ref
                and result.advisory_input_sha256
                == job.advisory_input_sha256
                and result.status is job.status
            ):
                valid_score_reports += 1
        coverage: dict[str, Any] = {
            "schema_version": "8099.deepeval-coverage/v1",
            "coverage_scope": "latest_job_per_run_ref_report_version",
            "eligible_reports": eligible,
            "enrolled_reports": eligible,
            "terminal_reports": terminal,
            "scored_reports": completed,
            "cached_reports": cached,
            "valid_score_reports": valid_score_reports,
            "status_counts": status_counts,
        }
        for status in JobStatus:
            coverage[f"{status.value}_reports"] = status_counts[
                status.value
            ]
        _atomic_write_json(
            self.root / "coverage" / "current.json",
            coverage,
        )
        return coverage
