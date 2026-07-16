from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping


COMPACT_CACHE_SCHEMA_VERSION = 1
DEFAULT_COMPACT_CACHE_DIR = Path("/app/data/compact_cache")
DEFAULT_COMPACT_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_COMPACT_CACHE_MAX_ENTRY_BYTES = 2 * 1024 * 1024

_CACHE_LOCKS = tuple(threading.Lock() for _ in range(64))
_NON_CONTENT_PACK_FIELDS = frozenset({"timings"})


class CompactCacheError(RuntimeError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CompactCacheError("compact cache input is not canonical JSON") from exc


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def compact_cache_enabled() -> bool:
    return (os.getenv("ENABLE_COMPACT_CACHE") or "").strip().lower() in {"1", "true", "yes", "on"}


def compact_content_view(pack: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in pack.items() if key not in _NON_CONTENT_PACK_FIELDS}


def compact_cache_key(
    pack: Mapping[str, Any],
    *,
    max_chars: int,
    version_context: Mapping[str, Any],
) -> str:
    payload = {
        "cache_schema_version": COMPACT_CACHE_SCHEMA_VERSION,
        "max_chars": int(max_chars),
        "version_context": dict(version_context),
        "evidence_pack": compact_content_view(pack),
    }
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


class CompactCache:
    def __init__(
        self,
        root: Path,
        *,
        ttl_seconds: int = DEFAULT_COMPACT_CACHE_TTL_SECONDS,
        max_entry_bytes: int = DEFAULT_COMPACT_CACHE_MAX_ENTRY_BYTES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root)
        self.ttl_seconds = max(0, int(ttl_seconds))
        self.max_entry_bytes = max(1024, int(max_entry_bytes))
        self.clock = clock

    @classmethod
    def from_environment(cls) -> "CompactCache":
        root = Path((os.getenv("COMPACT_CACHE_DIR") or "").strip() or DEFAULT_COMPACT_CACHE_DIR)
        return cls(
            root,
            ttl_seconds=_env_int("COMPACT_CACHE_TTL_SECONDS", DEFAULT_COMPACT_CACHE_TTL_SECONDS),
            max_entry_bytes=_env_int("COMPACT_CACHE_MAX_ENTRY_BYTES", DEFAULT_COMPACT_CACHE_MAX_ENTRY_BYTES),
        )

    def entry_path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def _discard(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _read(self, key: str) -> tuple[dict[str, Any] | None, str]:
        path = self.entry_path(key)
        if not path.is_file():
            return None, "miss"
        try:
            if path.stat().st_size > self.max_entry_bytes:
                self._discard(path)
                return None, "corrupt"
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            self._discard(path)
            return None, "corrupt"
        if not isinstance(envelope, dict):
            self._discard(path)
            return None, "corrupt"
        if envelope.get("schema_version") != COMPACT_CACHE_SCHEMA_VERSION or envelope.get("cache_key") != key:
            self._discard(path)
            return None, "corrupt"
        created_at = envelope.get("created_at_epoch")
        if not isinstance(created_at, (int, float)):
            self._discard(path)
            return None, "corrupt"
        if self.ttl_seconds and self.clock() - float(created_at) > self.ttl_seconds:
            self._discard(path)
            return None, "expired"
        value = envelope.get("value")
        if not isinstance(value, dict):
            self._discard(path)
            return None, "corrupt"
        expected_hash = str(envelope.get("content_sha256") or "")
        actual_hash = hashlib.sha256(_canonical_bytes(value)).hexdigest()
        if expected_hash != actual_hash:
            self._discard(path)
            return None, "corrupt"
        return value, "hit"

    def _write(self, key: str, value: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        envelope = {
            "schema_version": COMPACT_CACHE_SCHEMA_VERSION,
            "cache_key": key,
            "created_at_epoch": self.clock(),
            "content_sha256": hashlib.sha256(_canonical_bytes(value)).hexdigest(),
            "value": value,
        }
        payload = _canonical_bytes(envelope)
        if len(payload) > self.max_entry_bytes:
            raise CompactCacheError("compact cache entry exceeds configured size")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{key[:12]}-",
            suffix=".tmp",
            dir=self.root,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.entry_path(key))
        finally:
            temporary_path.unlink(missing_ok=True)

    def get_or_compute(
        self,
        pack: Mapping[str, Any],
        *,
        max_chars: int,
        version_context: Mapping[str, Any],
        compute: Callable[[], dict[str, Any]],
    ) -> tuple[dict[str, Any], str, str]:
        key = compact_cache_key(pack, max_chars=max_chars, version_context=version_context)
        lock = _CACHE_LOCKS[int(key[:8], 16) % len(_CACHE_LOCKS)]
        with lock:
            try:
                cached, read_status = self._read(key)
            except Exception:  # noqa: BLE001
                cached, read_status = None, "read_failed"
            if cached is not None:
                return cached, "hit", key

            value = compute()
            if not isinstance(value, dict):
                raise TypeError("compact computation must return a dictionary")
            try:
                self._write(key, value)
            except Exception:  # noqa: BLE001
                return value, "write_failed", key
            rebuilt_status = {
                "expired": "expired_rebuilt",
                "corrupt": "corrupt_rebuilt",
                "read_failed": "read_failed_rebuilt",
            }.get(read_status, "miss")
            return value, rebuilt_status, key
