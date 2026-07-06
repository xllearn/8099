from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any


_RUN_ID_RE = re.compile(r"run_[A-Za-z0-9_-]{8,80}")
_GLOBAL_LOCK = threading.Lock()
_RUN_LOCKS: dict[str, threading.Lock] = {}


class WorkflowRunStoreError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class WorkflowRunStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def write_run(self, record: dict[str, Any]) -> None:
        run_id = self._validated_run_id(str(record.get("run_id") or ""))
        with self._lock_for(run_id):
            self._atomic_write(self._path_for(run_id), record)

    def read_run(self, run_id: str) -> dict[str, Any]:
        safe_run_id = self._validated_run_id(run_id)
        path = self._path_for(safe_run_id)
        if not path.exists():
            raise WorkflowRunStoreError("RUN_NOT_FOUND", f"workflow run not found: {safe_run_id}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise WorkflowRunStoreError("RUN_JSON_CORRUPT", f"workflow run JSON is corrupt: {safe_run_id}") from exc
        except OSError as exc:
            raise WorkflowRunStoreError("RUN_READ_FAILED", f"workflow run read failed: {safe_run_id}") from exc
        if not isinstance(value, dict):
            raise WorkflowRunStoreError("RUN_JSON_INVALID", f"workflow run JSON root is invalid: {safe_run_id}")
        return value

    def update_run(self, run_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        safe_run_id = self._validated_run_id(run_id)
        with self._lock_for(safe_run_id):
            current = self.read_run(safe_run_id)
            current.update(patch)
            self._atomic_write(self._path_for(safe_run_id), current)
            return current

    def _path_for(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"

    def _atomic_write(self, path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._replace_with_retry(tmp_path, path)
        except OSError as exc:
            raise WorkflowRunStoreError("RUN_WRITE_FAILED", f"workflow run write failed: {path.name}") from exc
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _validated_run_id(run_id: str) -> str:
        if not _RUN_ID_RE.fullmatch(run_id or ""):
            raise WorkflowRunStoreError("RUN_ID_INVALID", "workflow run id is invalid")
        return run_id

    @staticmethod
    def _replace_with_retry(tmp_path: Path, path: Path) -> None:
        for attempt in range(5):
            try:
                os.replace(tmp_path, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))

    @staticmethod
    def _lock_for(run_id: str) -> threading.Lock:
        with _GLOBAL_LOCK:
            lock = _RUN_LOCKS.get(run_id)
            if lock is None:
                lock = threading.Lock()
                _RUN_LOCKS[run_id] = lock
            return lock
