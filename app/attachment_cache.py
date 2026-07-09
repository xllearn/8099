from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


SUCCESS_STATUSES = {
    "stream_parsed",
    "temp_file_parsed",
    "parsed_text",
    "parsed_summary",
    "parsed_table_summary",
    "too_large_summary_only",
    "unsupported",
}
FAILURE_STATUSES = {"download_failed", "network_unreachable", "parse_failed", "temp_file_cleanup_failed"}
PARSER_CACHE_VERSION = "20260616-doc-libreoffice-v2"


def _bool_env(name: str, default: bool = False) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def cache_enabled() -> bool:
    return _bool_env("ENABLE_ATTACHMENT_PARSE_CACHE", True)


def cache_dir() -> Path:
    return Path((os.getenv("ATTACHMENT_PARSE_CACHE_DIR") or "/app/data/attachment_parse_cache").strip())


def cache_key(attachment: dict[str, Any]) -> str:
    payload = {
        "parser_version": (os.getenv("ATTACHMENT_PARSER_VERSION") or PARSER_CACHE_VERSION).strip(),
        "articleattid": str(attachment.get("articleattid") or ""),
        "filename": str(attachment.get("filename") or ""),
        "filesize": str(attachment.get("filesize") or ""),
        "uploadtime": str(attachment.get("uploadtime") or ""),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _path_for_key(key: str) -> Path:
    return cache_dir() / f"{key}.json"


def _ttl_seconds(status: str, failure_count: int = 0) -> int:
    if status in {"download_failed", "network_unreachable"}:
        if failure_count >= 3:
            return 2 * 60 * 60
        return int(_float_env("ATTACHMENT_PARSE_CACHE_FAILURE_TTL_MINUTES", 10) * 60)
    if status in {"parse_failed", "temp_file_cleanup_failed"}:
        if failure_count >= 3:
            return 2 * 60 * 60
        return int(_float_env("ATTACHMENT_PARSE_CACHE_PARSE_FAILURE_TTL_MINUTES", 30) * 60)
    return int(_float_env("ATTACHMENT_PARSE_CACHE_SUCCESS_TTL_DAYS", 30) * 24 * 60 * 60)


def load_cached_result(attachment: dict[str, Any], *, force_refresh: bool = False) -> tuple[dict[str, Any] | None, str]:
    if not cache_enabled() or force_refresh:
        return None, "force_refreshed" if force_refresh else "cache_disabled"
    key = cache_key(attachment)
    path = _path_for_key(key)
    if not path.exists():
        return None, "cache_miss"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        path.unlink(missing_ok=True)
        return None, "cache_miss"
    now = int(time.time())
    next_retry_at = int(payload.get("next_retry_at") or 0)
    expires_at = int(payload.get("expires_at") or 0)
    if next_retry_at and now >= next_retry_at:
        return None, "cache_expired_retry"
    if expires_at and now >= expires_at:
        path.unlink(missing_ok=True)
        return None, "cache_expired_retry"
    result = payload.get("result") if isinstance(payload.get("result"), dict) else None
    if not result:
        path.unlink(missing_ok=True)
        return None, "cache_miss"
    os.utime(path, None)
    status = str(result.get("parse_status") or result.get("download_status") or "")
    if status in FAILURE_STATUSES:
        result["cache_status"] = "cache_hit_failure_short"
        result["retry_after_seconds"] = max(0, next_retry_at - now)
    else:
        result["cache_status"] = "cache_hit_success"
        result["retry_after_seconds"] = 0
    return result, result["cache_status"]


def store_cached_result(attachment: dict[str, Any], result: dict[str, Any]) -> None:
    if not cache_enabled():
        return
    key = cache_key(attachment)
    path = _path_for_key(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    status = str(result.get("parse_status") or result.get("download_status") or "")
    existing_failure_count = 0
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            existing_failure_count = int(existing.get("failure_count") or 0)
        except Exception:  # noqa: BLE001
            existing_failure_count = 0
    failure_count = existing_failure_count + 1 if status in FAILURE_STATUSES else 0
    ttl = _ttl_seconds(status, failure_count)
    now = int(time.time())
    payload = {
        "cache_key": key,
        "articleattid": str(attachment.get("articleattid") or ""),
        "filename": str(attachment.get("filename") or ""),
        "parse_status": status,
        "cache_policy": "short_negative_cache" if status in FAILURE_STATUSES else "success_cache",
        "failure_count": failure_count,
        "created_at": now,
        "expires_at": now + ttl,
        "next_retry_at": now + ttl if status in FAILURE_STATUSES else 0,
        "retry_after_seconds": ttl if status in FAILURE_STATUSES else 0,
        "result": result,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def cleanup_cache() -> dict[str, int]:
    directory = cache_dir()
    if not directory.exists():
        return {"deleted_files": 0, "freed_bytes": 0}
    now = int(time.time())
    deleted = 0
    freed = 0
    files = [path for path in directory.glob("*.json") if path.is_file()]
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            expires_at = int(payload.get("expires_at") or 0)
        except Exception:  # noqa: BLE001
            expires_at = 0
        if expires_at and now >= expires_at:
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            deleted += 1
            freed += size
    max_bytes = int(_float_env("ATTACHMENT_PARSE_CACHE_MAX_MB", 500) * 1024 * 1024)
    files = sorted([path for path in directory.glob("*.json") if path.is_file()], key=lambda item: item.stat().st_mtime)
    total = sum(path.stat().st_size for path in files)
    for path in files:
        if total <= max_bytes:
            break
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        total -= size
        deleted += 1
        freed += size
    return {"deleted_files": deleted, "freed_bytes": freed}
