from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


logger = logging.getLogger("medical_notice_analyzer.analysis_history")

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_EVENTS = 100_000
DEFAULT_MAX_ARCHIVES = 12
TERMINAL_STATUSES = {"finished", "needs_manual_review", "failed", "interrupted"}

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


class HistoryTopologyError(RuntimeError):
    pass


class HistoryCorruptionError(RuntimeError):
    pass


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _archive_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        candidate = _text(item)
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _parse_timestamp(value: Any) -> datetime | None:
    candidate = _text(value)
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _duration_ms(record: Mapping[str, Any], status: str) -> int:
    if "duration_ms" in record:
        return _safe_int(record.get("duration_ms"))
    timings = record.get("timings") if isinstance(record.get("timings"), dict) else {}
    for key in ("total_ms", "elapsed_ms", "analysis_ms", "generation_ms"):
        value = _safe_int(timings.get(key))
        if value:
            return value
    if status in TERMINAL_STATUSES:
        started = _parse_timestamp(record.get("started_at") or record.get("created_at"))
        ended = _parse_timestamp(record.get("ended_at") or record.get("updated_at"))
        if started and ended and ended >= started:
            return int((ended - started).total_seconds() * 1000)
    return 0


def _worker_count_from_environment(environ: Mapping[str, str]) -> int:
    counts = [1]
    for key in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        raw = _text(environ.get(key))
        if not raw:
            continue
        if not raw.isdigit() or int(raw) < 1:
            raise HistoryTopologyError(f"invalid {key}")
        counts.append(int(raw))
    for key in ("UVICORN_CMD_ARGS", "GUNICORN_CMD_ARGS"):
        raw = _text(environ.get(key))
        if not raw:
            continue
        match = re.search(r"(?:--workers(?:=|\s+)|-w\s+)(\d+)", raw)
        if match:
            counts.append(int(match.group(1)))
    return max(counts)


def assert_single_writer_topology(environ: Mapping[str, str] | None = None) -> None:
    worker_count = _worker_count_from_environment(environ or os.environ)
    if worker_count != 1:
        raise HistoryTopologyError(
            f"analysis history requires one application worker, configured={worker_count}"
        )


def material_identities_from_pack(pack: Mapping[str, Any]) -> list[dict[str, str]]:
    identities: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for role, field in (("primary", "primary_materials"), ("auxiliary", "auxiliary_materials")):
        materials = pack.get(field)
        if not isinstance(materials, list):
            continue
        for material in materials:
            if not isinstance(material, dict):
                continue
            menu_code = _text(material.get("menu_code"))
            articleid = _text(material.get("articleid"))
            if not menu_code or not articleid:
                continue
            identity_key = (role, menu_code, articleid)
            if identity_key in seen:
                continue
            seen.add(identity_key)
            default_id = f"{menu_code}:{articleid}"
            identities.append(
                {
                    "role": role,
                    "menu_code": menu_code,
                    "articleid": articleid,
                    "record_id": _text(material.get("record_id")) or default_id,
                    "notice_id": _text(material.get("notice_id")) or default_id,
                    "title": _text(material.get("title")),
                    "menu_name": _text(material.get("menu_name")),
                }
            )
    return identities


def enrich_record_with_pack_identity(
    record: Mapping[str, Any], pack: Mapping[str, Any] | None
) -> dict[str, Any]:
    enriched = dict(record)
    identities = enriched.get("material_identities")
    if not isinstance(identities, list) or not identities:
        identities = material_identities_from_pack(pack or {})
        enriched["material_identities"] = identities
    primary = next(
        (
            item
            for item in identities
            if isinstance(item, dict) and _text(item.get("role")) == "primary"
        ),
        next((item for item in identities if isinstance(item, dict)), {}),
    )
    if isinstance(primary, dict):
        for key in (
            "record_id",
            "notice_id",
            "menu_code",
            "menu_name",
            "articleid",
            "title",
        ):
            if not _text(enriched.get(key)):
                enriched[key] = _text(primary.get(key))
    return enriched


def _lock_for(root: Path) -> threading.RLock:
    key = os.path.normcase(str(root.resolve(strict=False)))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


class AnalysisHistoryStore:
    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_events: int = DEFAULT_MAX_EVENTS,
        max_archives: int = DEFAULT_MAX_ARCHIVES,
        enforce_single_worker: bool = True,
    ) -> None:
        if enforce_single_worker:
            assert_single_writer_topology()
        if max_bytes < 1 or max_events < 1 or max_archives < 1:
            raise ValueError("history retention limits must be positive")
        self.root = Path(root)
        self.events_path = self.root / "events.jsonl"
        self.index_path = self.root / "index.json"
        self.corrupt_dir = self.root / "corrupt"
        self.max_bytes = int(max_bytes)
        self.max_events = int(max_events)
        self.max_archives = int(max_archives)
        self._lock = _lock_for(self.root)

    @staticmethod
    def _empty_index() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "updated_at": "",
            "last_event_id": "",
            "active_event_count": 0,
            "runs": {},
        }

    def archive_paths(self) -> list[Path]:
        return sorted(self.root.glob("events.*.jsonl")) if self.root.exists() else []

    def _atomic_write_bytes(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_index_unlocked(self, index: dict[str, Any]) -> None:
        payload = dict(index)
        payload["schema_version"] = 1
        payload["updated_at"] = _now_text()
        self._atomic_write_bytes(
            self.index_path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
        )

    def _quarantine_bytes_unlocked(self, prefix: str, data: bytes) -> Path:
        path = self.corrupt_dir / f"{prefix}-{_archive_stamp()}-{uuid.uuid4().hex}.bin"
        self._atomic_write_bytes(path, data)
        return path

    def _recover_corrupt_tail_unlocked(self) -> dict[str, Any]:
        if not self.events_path.exists():
            return {"recovered_fragments": 0, "quarantine_path": ""}
        data = self.events_path.read_bytes()
        if not data:
            return {"recovered_fragments": 0, "quarantine_path": ""}
        lines = data.splitlines(keepends=True)
        offset = 0
        for index, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            if not stripped:
                offset += len(raw_line)
                continue
            try:
                json.loads(stripped.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                if index != len(lines) - 1:
                    raise HistoryCorruptionError("invalid JSONL event before file tail") from exc
                fragment = data[offset:]
                quarantine = self._quarantine_bytes_unlocked("events-tail", fragment)
                self._atomic_write_bytes(self.events_path, data[:offset])
                logger.warning(
                    "analysis_history_tail_recovered bytes=%s quarantine=%s",
                    len(fragment),
                    quarantine,
                )
                return {"recovered_fragments": 1, "quarantine_path": str(quarantine)}
            offset += len(raw_line)
        if data and not data.endswith(b"\n"):
            with self.events_path.open("ab") as handle:
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
        return {"recovered_fragments": 0, "quarantine_path": ""}

    def recover_corrupt_tail(self) -> dict[str, Any]:
        with self._lock:
            return self._recover_corrupt_tail_unlocked()

    def _read_events_unlocked(self, paths: Sequence[Path]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for path in paths:
            if not path.exists():
                continue
            for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not raw_line.strip():
                    continue
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise HistoryCorruptionError(
                        f"invalid event in {path.name}:{line_number}"
                    ) from exc
                if not isinstance(event, dict):
                    raise HistoryCorruptionError(
                        f"non-object event in {path.name}:{line_number}"
                    )
                events.append(event)
        return events

    def read_events(self, paths: Sequence[Path] | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if paths is None:
                self._recover_corrupt_tail_unlocked()
                selected = [*self.archive_paths()]
                if self.events_path.exists():
                    selected.append(self.events_path)
            else:
                selected = [Path(path) for path in paths]
            return self._read_events_unlocked(selected)

    def _tail_event_id_unlocked(self) -> str:
        candidates = [self.events_path, *reversed(self.archive_paths())]
        for path in candidates:
            if not path.exists() or path.stat().st_size == 0:
                continue
            with path.open("rb") as handle:
                size = handle.seek(0, os.SEEK_END)
                handle.seek(max(0, size - 65536))
                tail = handle.read()
            for raw_line in reversed(tail.splitlines()):
                if not raw_line.strip():
                    continue
                try:
                    event = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(event, dict):
                    return _text(event.get("event_id"))
        return ""

    def _read_index_file_unlocked(self) -> dict[str, Any] | None:
        if not self.index_path.exists():
            return None
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            try:
                raw = self.index_path.read_bytes()
                quarantine = self._quarantine_bytes_unlocked("index", raw)
                logger.warning(
                    "analysis_history_index_quarantined error_type=%s quarantine=%s",
                    exc.__class__.__name__,
                    quarantine,
                )
            except OSError:
                logger.warning(
                    "analysis_history_index_read_failed error_type=%s", exc.__class__.__name__
                )
            return None
        if not isinstance(data, dict) or not isinstance(data.get("runs"), dict):
            return None
        data.setdefault("schema_version", 1)
        data.setdefault("updated_at", "")
        data.setdefault("last_event_id", "")
        data.setdefault("active_event_count", 0)
        return data

    def _read_index_unlocked(self) -> dict[str, Any]:
        self._recover_corrupt_tail_unlocked()
        index = self._read_index_file_unlocked()
        event_paths = self.archive_paths()
        if self.events_path.exists() and self.events_path.stat().st_size:
            event_paths.append(self.events_path)
        if index is None:
            if event_paths:
                self._rebuild_index_from_events_unlocked()
                return self._read_index_file_unlocked() or self._empty_index()
            return self._empty_index()
        tail_event_id = self._tail_event_id_unlocked()
        if tail_event_id and tail_event_id != _text(index.get("last_event_id")):
            self._rebuild_index_from_events_unlocked(base_runs=index.get("runs"))
            return self._read_index_file_unlocked() or self._empty_index()
        return index

    def _append_event_unlocked(self, encoded_event: bytes) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("ab") as handle:
            handle.write(encoded_event)
            handle.flush()
            os.fsync(handle.fileno())

    def _prune_archives_unlocked(self) -> None:
        archives = self.archive_paths()
        for path in archives[: max(0, len(archives) - self.max_archives)]:
            path.unlink()

    def _rotate_if_needed_unlocked(
        self, index: dict[str, Any], next_event_bytes: int
    ) -> None:
        if not self.events_path.exists() or self.events_path.stat().st_size == 0:
            return
        active_count = _safe_int(index.get("active_event_count"))
        current_bytes = self.events_path.stat().st_size
        if active_count < self.max_events and current_bytes + next_event_bytes <= self.max_bytes:
            return
        archive = self.root / f"events.{_archive_stamp()}.{uuid.uuid4().hex}.jsonl"
        os.replace(self.events_path, archive)
        index["active_event_count"] = 0
        self._prune_archives_unlocked()
        logger.info("analysis_history_events_rotated archive=%s", archive.name)

    @staticmethod
    def _material_identities(record: Mapping[str, Any], previous: Mapping[str, Any]) -> list[dict[str, str]]:
        source = record.get("material_identities")
        if not isinstance(source, list) or not source:
            source = previous.get("material_identities")
        identities: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        if isinstance(source, list):
            for raw in source:
                if not isinstance(raw, dict):
                    continue
                role = _text(raw.get("role")) or "primary"
                menu_code = _text(raw.get("menu_code"))
                articleid = _text(raw.get("articleid"))
                if not menu_code or not articleid:
                    continue
                key = (role, menu_code, articleid)
                if key in seen:
                    continue
                seen.add(key)
                default_id = f"{menu_code}:{articleid}"
                identities.append(
                    {
                        "role": role,
                        "menu_code": menu_code,
                        "articleid": articleid,
                        "record_id": _text(raw.get("record_id")) or default_id,
                        "notice_id": _text(raw.get("notice_id")) or default_id,
                        "title": _text(raw.get("title")),
                        "menu_name": _text(raw.get("menu_name")),
                    }
                )
        if identities:
            return identities
        menu_code = _text(record.get("menu_code") or previous.get("menu_code"))
        articleid = _text(record.get("articleid") or previous.get("articleid"))
        if not menu_code or not articleid:
            return []
        default_id = f"{menu_code}:{articleid}"
        return [
            {
                "role": "primary",
                "menu_code": menu_code,
                "articleid": articleid,
                "record_id": _text(record.get("record_id") or previous.get("record_id"))
                or default_id,
                "notice_id": _text(record.get("notice_id") or previous.get("notice_id"))
                or default_id,
                "title": _text(record.get("title") or previous.get("title")),
                "menu_name": _text(record.get("menu_name") or previous.get("menu_name")),
            }
        ]

    def _history_item(
        self,
        record: Mapping[str, Any],
        previous: Mapping[str, Any] | None = None,
        *,
        revision: int,
        event_id: str,
        word_download_url: str = "",
        word_filename: str = "",
    ) -> dict[str, Any]:
        old = previous or {}

        def prefer(key: str, default: Any = "") -> Any:
            value = record.get(key)
            if value not in (None, "", []):
                return value
            return old.get(key, default)

        identities = self._material_identities(record, old)
        primary = next(
            (item for item in identities if item.get("role") == "primary"),
            identities[0] if identities else {},
        )
        status = _text(prefer("status", prefer("run_status", "created"))) or "created"
        created_at = _text(prefer("created_at"))
        updated_at = _text(prefer("updated_at")) or created_at
        started_at = _text(prefer("started_at")) or created_at
        ended_at = _text(prefer("ended_at"))
        if not ended_at and status in TERMINAL_STATUSES:
            ended_at = updated_at
        quality_check = record.get("quality_check")
        if not isinstance(quality_check, dict):
            quality_check = old.get("quality_check") if isinstance(old.get("quality_check"), dict) else {}
        quality_gate = record.get("quality_gate")
        if not isinstance(quality_gate, dict):
            quality_gate = old.get("quality_gate") if isinstance(old.get("quality_gate"), dict) else {}
        quality_passed = quality_check.get("passed")
        if quality_passed not in (True, False):
            quality_passed = None
        quality_gate_status = _text(quality_gate.get("deliverable_status"))
        deliverable = bool(record.get("deliverable", old.get("deliverable", False)))
        needs_manual_review = bool(
            record.get("needs_manual_review", old.get("needs_manual_review", False))
        )
        if status not in TERMINAL_STATUSES:
            quality_status = "pending"
        elif deliverable and quality_passed is True:
            quality_status = "passed"
        elif needs_manual_review or quality_gate_status == "needs_manual_review":
            quality_status = "needs_manual_review"
        else:
            quality_status = "failed"
        draft_available = bool(
            record.get(
                "draft_word_export_available",
                old.get("draft_word_export_available", False),
            )
        )
        final_available = bool(
            record.get(
                "final_word_export_available",
                old.get("final_word_export_available", False),
            )
        )
        word_export_available = bool(
            record.get("word_export_available", old.get("word_export_available", False))
        )
        word_download_available = bool(word_export_available or draft_available)
        generated = bool(record.get("word_generated", old.get("word_generated", False)))
        if word_download_url:
            generated = True
        resolved_download_url = _text(
            word_download_url or record.get("word_download_url") or old.get("word_download_url")
        )
        resolved_filename = _text(
            word_filename or record.get("word_filename") or old.get("word_filename")
        )
        if not word_download_available:
            resolved_download_url = ""
            resolved_filename = ""
        record_id = _text(record.get("record_id") or old.get("record_id") or primary.get("record_id"))
        notice_id = _text(record.get("notice_id") or old.get("notice_id") or primary.get("notice_id"))
        menu_code = _text(record.get("menu_code") or old.get("menu_code") or primary.get("menu_code"))
        articleid = _text(record.get("articleid") or old.get("articleid") or primary.get("articleid"))
        item = {
            "run_id": _text(prefer("run_id")),
            "pack_id": _text(prefer("pack_id")),
            "material_identities": identities,
            "record_id": record_id,
            "notice_id": notice_id or record_id,
            "menu_code": menu_code,
            "menu_name": _text(record.get("menu_name") or old.get("menu_name") or primary.get("menu_name")),
            "articleid": articleid,
            "title": _text(record.get("title") or old.get("title") or primary.get("title") or prefer("report_title")),
            "created_at": created_at,
            "updated_at": updated_at,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_ms": _duration_ms({**old, **record, "started_at": started_at, "ended_at": ended_at}, status),
            "status": status,
            "quality_status": quality_status,
            "quality_passed": quality_passed,
            "quality_gate_status": quality_gate_status,
            "provider": _text(prefer("provider", "dify")) or "dify",
            "workflow_run_id": _text(prefer("workflow_run_id")),
            "provider_run_id": _text(prefer("provider_run_id")),
            "compact_pack_chars": _safe_int(prefer("compact_pack_chars", 0)),
            "primary_failure_code": _text(prefer("primary_failure_code")),
            "secondary_failure_codes": _string_list(prefer("secondary_failure_codes", [])),
            "quality_failure_codes": _string_list(prefer("quality_failure_codes", [])),
            "generation_failure_codes": _string_list(prefer("generation_failure_codes", [])),
            "blocking_issue_codes": _string_list(prefer("blocking_issue_codes", [])),
            "word_generated": generated,
            "word_download_available": word_download_available,
            "word_download_url": resolved_download_url,
            "word_filename": resolved_filename,
            "word_export_available": word_export_available,
            "draft_word_export_available": draft_available,
            "final_word_export_available": final_available,
            "deliverable": deliverable,
            "needs_manual_review": needs_manual_review,
            "revision": revision,
            "latest_event_id": event_id,
        }
        return item

    def record_run(
        self,
        record: Mapping[str, Any],
        *,
        event_type: str = "",
        word_download_url: str = "",
        word_filename: str = "",
    ) -> dict[str, Any]:
        run_id = _text(record.get("run_id"))
        if not run_id:
            raise ValueError("analysis history requires run_id")
        with self._lock:
            index = self._read_index_unlocked()
            previous = index["runs"].get(run_id)
            old = previous if isinstance(previous, dict) else None
            revision = _safe_int(old.get("revision") if old else 0) + 1
            event_id = uuid.uuid4().hex
            item = self._history_item(
                record,
                old,
                revision=revision,
                event_id=event_id,
                word_download_url=word_download_url,
                word_filename=word_filename,
            )
            resolved_event_type = event_type or ("run_created" if old is None else "run_updated")
            event = {
                "schema_version": 1,
                "event_id": event_id,
                "event_type": resolved_event_type,
                "event_at": _now_text(),
                "run_id": run_id,
                "revision": revision,
                "item": item,
            }
            encoded = json.dumps(
                event, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8") + b"\n"
            self._rotate_if_needed_unlocked(index, len(encoded))
            self._append_event_unlocked(encoded)
            index["runs"][run_id] = item
            index["last_event_id"] = event_id
            index["active_event_count"] = _safe_int(index.get("active_event_count")) + 1
            self._write_index_unlocked(index)
            return copy.deepcopy(item)

    def record_word_downloaded(
        self,
        record: Mapping[str, Any],
        *,
        download_url: str,
        filename: str,
    ) -> dict[str, Any]:
        return self.record_run(
            record,
            event_type="word_downloaded",
            word_download_url=download_url,
            word_filename=filename,
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._read_index_unlocked()["runs"].get(run_id)
            return copy.deepcopy(item) if isinstance(item, dict) else None

    @staticmethod
    def _identity_matches(item: Mapping[str, Any], key: str, expected: str) -> bool:
        if _text(item.get(key)) == expected:
            return True
        identities = item.get("material_identities")
        if not isinstance(identities, list):
            return False
        return any(
            isinstance(identity, dict) and _text(identity.get(key)) == expected
            for identity in identities
        )

    def list_runs(
        self,
        *,
        articleid: str = "",
        record_id: str = "",
        notice_id: str = "",
        menu_code: str = "",
        pack_id: str = "",
        status: str = "",
        provider: str = "",
        query: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            values = list(self._read_index_unlocked()["runs"].values())
        items = [copy.deepcopy(item) for item in values if isinstance(item, dict)]
        identity_filters = {
            "articleid": _text(articleid),
            "record_id": _text(record_id),
            "notice_id": _text(notice_id),
            "menu_code": _text(menu_code),
        }
        for key, expected in identity_filters.items():
            if expected:
                items = [item for item in items if self._identity_matches(item, key, expected)]
        direct_filters = {
            "pack_id": _text(pack_id),
            "status": _text(status),
            "provider": _text(provider),
        }
        for key, expected in direct_filters.items():
            if expected:
                items = [item for item in items if _text(item.get(key)) == expected]
        query_text = _text(query).casefold()
        if query_text:
            filtered: list[dict[str, Any]] = []
            for item in items:
                identities = item.get("material_identities")
                identity_text = " ".join(
                    " ".join(
                        _text(identity.get(key))
                        for key in ("title", "record_id", "notice_id", "menu_code", "articleid")
                    )
                    for identity in identities or []
                    if isinstance(identity, dict)
                )
                haystack = " ".join(
                    [
                        *(
                            _text(item.get(key))
                            for key in (
                                "title",
                                "run_id",
                                "pack_id",
                                "record_id",
                                "notice_id",
                                "menu_code",
                                "articleid",
                                "workflow_run_id",
                            )
                        ),
                        identity_text,
                    ]
                ).casefold()
                if query_text in haystack:
                    filtered.append(item)
            items = filtered
        items.sort(
            key=lambda item: (
                _text(item.get("updated_at")),
                _text(item.get("created_at")),
                _text(item.get("run_id")),
            ),
            reverse=True,
        )
        total = len(items)
        safe_page = max(1, int(page))
        safe_page_size = max(1, int(page_size))
        start = (safe_page - 1) * safe_page_size
        return items[start : start + safe_page_size], total

    def _rebuild_index_from_events_unlocked(
        self, base_runs: Mapping[str, Any] | None = None
    ) -> dict[str, int]:
        self._recover_corrupt_tail_unlocked()
        paths = self.archive_paths()
        if self.events_path.exists():
            paths.append(self.events_path)
        events = self._read_events_unlocked(paths)
        runs = {
            _text(run_id): copy.deepcopy(item)
            for run_id, item in (base_runs or {}).items()
            if _text(run_id) and isinstance(item, dict)
        }
        last_event_id = ""
        for event in events:
            event_id = _text(event.get("event_id"))
            run_id = _text(event.get("run_id"))
            item = event.get("item")
            if not event_id or not run_id or not isinstance(item, dict):
                raise HistoryCorruptionError("history event is missing identity or item")
            last_event_id = event_id
            current = runs.get(run_id)
            current_revision = _safe_int(current.get("revision") if isinstance(current, dict) else 0)
            event_revision = _safe_int(event.get("revision"))
            if event_revision >= current_revision:
                runs[run_id] = copy.deepcopy(item)
        active_events = 0
        if self.events_path.exists():
            active_events = len(self._read_events_unlocked([self.events_path]))
        self._write_index_unlocked(
            {
                "schema_version": 1,
                "updated_at": "",
                "last_event_id": last_event_id,
                "active_event_count": active_events,
                "runs": runs,
            }
        )
        return {"total_events": len(events), "total_runs": len(runs)}

    def rebuild_index_from_events(self) -> dict[str, int]:
        with self._lock:
            return self._rebuild_index_from_events_unlocked()

    def rebuild_from_run_dir(
        self,
        run_dir: Path,
        *,
        evidence_pack_dir: Path | None = None,
    ) -> dict[str, int]:
        runs: dict[str, dict[str, Any]] = {}
        skipped = 0
        with self._lock:
            for path in sorted(Path(run_dir).glob("run_*.json")):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(record, dict):
                        raise ValueError("run JSON must be an object")
                    pack: dict[str, Any] | None = None
                    pack_id = _text(record.get("pack_id"))
                    if evidence_pack_dir and pack_id:
                        pack_path = Path(evidence_pack_dir) / f"{pack_id}.json"
                        if pack_path.exists():
                            loaded_pack = json.loads(pack_path.read_text(encoding="utf-8"))
                            if isinstance(loaded_pack, dict):
                                pack = loaded_pack
                    enriched = enrich_record_with_pack_identity(record, pack)
                    run_id = _text(enriched.get("run_id"))
                    if not run_id:
                        raise ValueError("run JSON is missing run_id")
                    runs[run_id] = self._history_item(
                        enriched,
                        revision=0,
                        event_id="",
                    )
                except (OSError, ValueError, json.JSONDecodeError):
                    skipped += 1
                    logger.warning("analysis_history_rebuild_run_skipped path=%s", path)
            result = self._rebuild_index_from_events_unlocked(base_runs=runs)
        return {
            "total_runs": result["total_runs"],
            "total_events": result["total_events"],
            "skipped": skipped,
        }
