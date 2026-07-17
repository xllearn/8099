from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


RUN_CHECKPOINT_SCHEMA_VERSION = 1
RUN_CHECKPOINT_STEPS = (
    "prepare",
    "attachments",
    "evidence",
    "compact",
    "provider",
    "repair",
    "quality_gate",
    "word_publish",
)

_RUN_ID_RE = re.compile(r"run_[A-Za-z0-9_-]{8,80}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}
_UNSET = object()


class CheckpointError(RuntimeError):
    pass


class CheckpointCorruptionError(CheckpointError):
    pass


class CheckpointTransitionError(CheckpointError):
    pass


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _lock_for(root: Path) -> threading.RLock:
    key = os.path.normcase(str(root.resolve(strict=False)))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_canonical_value(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ),
        )
    return {"type": value.__class__.__name__, "text": str(value)}


def hash_checkpoint_value(value: Any) -> str:
    encoded = json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RunCheckpointStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = _lock_for(self.root)

    def path_for(self, run_id: str) -> Path:
        if not _RUN_ID_RE.fullmatch(str(run_id or "")):
            raise ValueError("invalid run_id")
        return self.root / f"{run_id}.json"

    @staticmethod
    def _empty(run_id: str) -> dict[str, Any]:
        return {
            "schema_version": RUN_CHECKPOINT_SCHEMA_VERSION,
            "run_id": run_id,
            "updated_at": "",
            "events": [],
            "steps": {},
            "recovery_requests": [],
        }

    def _validate(self, value: Any, run_id: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise CheckpointCorruptionError("checkpoint root must be an object")
        if value.get("schema_version") != RUN_CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointCorruptionError("unsupported checkpoint schema")
        if str(value.get("run_id") or "") != run_id:
            raise CheckpointCorruptionError("checkpoint run_id mismatch")
        events = value.get("events")
        steps = value.get("steps")
        requests = value.get("recovery_requests")
        if not isinstance(events, list) or not isinstance(steps, dict) or not isinstance(
            requests, list
        ):
            raise CheckpointCorruptionError("checkpoint collections are invalid")
        event_ids: set[str] = set()
        for event in events:
            if not isinstance(event, dict):
                raise CheckpointCorruptionError("checkpoint event is invalid")
            event_id = str(event.get("event_id") or "")
            step = str(event.get("step") or "")
            status = str(event.get("status") or "")
            input_hash = str(event.get("input_sha256") or "")
            output_hash = str(event.get("output_sha256") or "")
            if (
                not _SHA256_RE.fullmatch(event_id)
                or event_id in event_ids
                or step not in RUN_CHECKPOINT_STEPS
                or status not in {"started", "completed", "failed"}
                or not _SHA256_RE.fullmatch(input_hash)
                or (status == "completed" and not _SHA256_RE.fullmatch(output_hash))
                or not str(event.get("recovery_condition") or "").strip()
            ):
                raise CheckpointCorruptionError("checkpoint event contract is invalid")
            event_ids.add(event_id)
        return value

    def _read_unlocked(self, run_id: str) -> dict[str, Any]:
        path = self.path_for(run_id)
        if not path.exists():
            return self._empty(run_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CheckpointCorruptionError("checkpoint file is unreadable") from exc
        return self._validate(value, run_id)

    def read(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            return json.loads(
                json.dumps(self._read_unlocked(run_id), ensure_ascii=False)
            )

    def _write_unlocked(self, run_id: str, value: dict[str, Any]) -> None:
        path = self.path_for(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        value["updated_at"] = _now_text()
        payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp_path.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _event_id(
        run_id: str,
        step: str,
        status: str,
        input_hash: str,
        output_hash: str,
        error_code: str,
        recovery_id: str,
    ) -> str:
        return hash_checkpoint_value(
            {
                "run_id": run_id,
                "step": step,
                "status": status,
                "input_sha256": input_hash,
                "output_sha256": output_hash,
                "error_code": error_code,
                "recovery_id": recovery_id,
            }
        )

    def _append(
        self,
        run_id: str,
        step: str,
        status: str,
        input_value: Any,
        *,
        output_value: Any = None,
        output_sha256: str = "",
        error_code: str = "",
        recovery_id: str = "",
        recovery_condition: str = "",
        allow_bootstrap: bool = False,
    ) -> dict[str, Any]:
        if step not in RUN_CHECKPOINT_STEPS:
            raise ValueError("invalid checkpoint step")
        input_hash = hash_checkpoint_value(input_value)
        output_hash = str(output_sha256 or "")
        if status == "completed" and not output_hash:
            output_hash = hash_checkpoint_value(output_value)
        event_id = self._event_id(
            run_id,
            step,
            status,
            input_hash,
            output_hash,
            str(error_code or ""),
            str(recovery_id or ""),
        )
        with self._lock:
            payload = self._read_unlocked(run_id)
            duplicate = next(
                (
                    event
                    for event in payload["events"]
                    if event.get("event_id") == event_id
                ),
                None,
            )
            if duplicate is not None:
                return dict(duplicate)

            current = payload["steps"].get(step)
            current_status = str((current or {}).get("status") or "")
            if current_status == "completed":
                raise CheckpointTransitionError(f"{step} is already completed")
            if status == "started":
                if current_status == "started":
                    raise CheckpointTransitionError(f"{step} is already running")
            elif status in {"completed", "failed"}:
                if not allow_bootstrap and current_status != "started":
                    raise CheckpointTransitionError(
                        f"{step} must be started before {status}"
                    )
                if current_status == "started" and str(
                    current.get("input_sha256") or ""
                ) != input_hash:
                    raise CheckpointTransitionError(f"{step} input changed")

            event = {
                "schema_version": RUN_CHECKPOINT_SCHEMA_VERSION,
                "event_id": event_id,
                "run_id": run_id,
                "step": step,
                "status": status,
                "input_sha256": input_hash,
                "output_sha256": output_hash,
                "error_code": str(error_code or ""),
                "recovery_id": str(recovery_id or ""),
                "recovery_condition": str(recovery_condition or "").strip()
                or {
                    "started": "resume_only_if_no_active_attempt",
                    "completed": "resume_when_input_and_output_hashes_match",
                    "failed": "resume_after_error_condition_is_resolved",
                }[status],
                "created_at": _now_text(),
            }
            payload["events"].append(event)
            payload["steps"][step] = dict(event)
            self._write_unlocked(run_id, payload)
            return dict(event)

    def start(
        self,
        run_id: str,
        step: str,
        input_value: Any,
        *,
        recovery_id: str = "",
        recovery_condition: str = "",
    ) -> dict[str, Any]:
        return self._append(
            run_id,
            step,
            "started",
            input_value,
            recovery_id=recovery_id,
            recovery_condition=recovery_condition,
        )

    def complete(
        self,
        run_id: str,
        step: str,
        input_value: Any,
        output_value: Any = None,
        *,
        output_sha256: str = "",
        recovery_id: str = "",
        recovery_condition: str = "",
        allow_bootstrap: bool = False,
    ) -> dict[str, Any]:
        return self._append(
            run_id,
            step,
            "completed",
            input_value,
            output_value=output_value,
            output_sha256=output_sha256,
            recovery_id=recovery_id,
            recovery_condition=recovery_condition,
            allow_bootstrap=allow_bootstrap,
        )

    def fail(
        self,
        run_id: str,
        step: str,
        input_value: Any,
        *,
        error_code: str,
        recovery_id: str = "",
        recovery_condition: str = "",
    ) -> dict[str, Any]:
        return self._append(
            run_id,
            step,
            "failed",
            input_value,
            error_code=error_code,
            recovery_id=recovery_id,
            recovery_condition=recovery_condition,
        )

    def resume_plan(self, run_id: str) -> dict[str, Any]:
        payload = self.read(run_id)
        steps = payload["steps"]
        provider = steps.get("provider") if isinstance(steps.get("provider"), dict) else {}
        if provider.get("status") == "started":
            return {
                "run_id": run_id,
                "blocked": True,
                "error_code": "RECOVERY_PROVIDER_OUTCOME_UNKNOWN",
                "resumed_from": "compact"
                if (steps.get("compact") or {}).get("status") == "completed"
                else "",
                "next_step": "",
                "skipped_steps": [
                    step
                    for step in RUN_CHECKPOINT_STEPS
                    if (steps.get(step) or {}).get("status") == "completed"
                ],
            }
        completed = [
            step
            for step in RUN_CHECKPOINT_STEPS
            if (steps.get(step) or {}).get("status") == "completed"
        ]
        next_step = next(
            (step for step in RUN_CHECKPOINT_STEPS if step not in completed), ""
        )
        resumed_from = completed[-1] if completed else ""
        return {
            "run_id": run_id,
            "blocked": False,
            "error_code": "",
            "resumed_from": resumed_from,
            "next_step": next_step,
            "skipped_steps": completed,
        }

    def assert_completed_hashes(
        self,
        run_id: str,
        step: str,
        *,
        input_value: Any = _UNSET,
        output_value: Any = _UNSET,
    ) -> dict[str, Any]:
        if step not in RUN_CHECKPOINT_STEPS:
            raise ValueError("invalid checkpoint step")
        snapshot = self.read(run_id)
        current = snapshot["steps"].get(step)
        if not isinstance(current, dict) or current.get("status") != "completed":
            raise CheckpointCorruptionError(f"{step} checkpoint is not completed")
        if input_value is not _UNSET and current.get(
            "input_sha256"
        ) != hash_checkpoint_value(input_value):
            raise CheckpointCorruptionError(f"{step} checkpoint input hash mismatch")
        if output_value is not _UNSET and current.get(
            "output_sha256"
        ) != hash_checkpoint_value(output_value):
            raise CheckpointCorruptionError(f"{step} checkpoint output hash mismatch")
        return dict(current)

    def record_recovery_request(
        self, run_id: str, recovery_id: str
    ) -> dict[str, Any]:
        recovery_id = str(recovery_id or "").strip()
        if not recovery_id or len(recovery_id) > 120:
            raise ValueError("invalid recovery_id")
        with self._lock:
            payload = self._read_unlocked(run_id)
            existing = next(
                (
                    item
                    for item in payload["recovery_requests"]
                    if item.get("recovery_id") == recovery_id
                ),
                None,
            )
            if existing is not None:
                return dict(existing)
            item = {
                "request_id": hash_checkpoint_value(
                    {"run_id": run_id, "recovery_id": recovery_id}
                ),
                "run_id": run_id,
                "recovery_id": recovery_id,
                "created_at": _now_text(),
            }
            payload["recovery_requests"].append(item)
            self._write_unlocked(run_id, payload)
            return dict(item)


def publish_file_once(
    store: RunCheckpointStore,
    run_id: str,
    input_value: Any,
    target: Path,
    render: Callable[[Path], None],
    *,
    validate: Callable[[Path], bool],
    recovery_id: str = "",
) -> dict[str, Any]:
    target = Path(target)
    input_hash = hash_checkpoint_value(input_value)
    snapshot = store.read(run_id)
    current = snapshot["steps"].get("word_publish") or {}
    if target.exists():
        output_hash = hash_file(target)
        if not validate(target):
            raise CheckpointCorruptionError("published Word failed validation")
        if current.get("status") == "completed":
            if current.get("input_sha256") != input_hash or current.get(
                "output_sha256"
            ) != output_hash:
                raise CheckpointCorruptionError("published Word hash mismatch")
            return {"reused": True, "output_sha256": output_hash}
        if current.get("status") != "started":
            store.start(
                run_id,
                "word_publish",
                input_value,
                recovery_id=recovery_id,
            )
        store.complete(
            run_id,
            "word_publish",
            input_value,
            output_sha256=output_hash,
            recovery_id=recovery_id,
        )
        return {"reused": True, "output_sha256": output_hash}

    if current.get("status") == "completed":
        raise CheckpointCorruptionError("published Word is missing")
    if current.get("status") != "started":
        store.start(
            run_id,
            "word_publish",
            input_value,
            recovery_id=recovery_id,
        )
    try:
        render(target)
        if not target.exists() or not validate(target):
            raise CheckpointCorruptionError("published Word failed validation")
        output_hash = hash_file(target)
        store.complete(
            run_id,
            "word_publish",
            input_value,
            output_sha256=output_hash,
            recovery_id=recovery_id,
        )
        return {"reused": False, "output_sha256": output_hash}
    except Exception:
        try:
            store.fail(
                run_id,
                "word_publish",
                input_value,
                error_code="WORD_PUBLISH_FAILED",
                recovery_id=recovery_id,
            )
        except CheckpointError:
            pass
        raise
