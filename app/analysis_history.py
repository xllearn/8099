from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


logger = logging.getLogger("medical_notice_analyzer.analysis_history")
_STORE_LOCK = threading.RLock()
_TERMINAL_STATUSES = {"finished", "needs_manual_review", "failed", "interrupted"}


def _now_text() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        candidate = _text(item)
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _parse_timestamp(value: Any) -> datetime | None:
    candidate = _text(value)
    if not candidate:
        return None
    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _duration_ms(record: dict[str, Any], status: str) -> int:
    explicit = _safe_int(record.get("duration_ms"))
    if explicit:
        return explicit
    if status in _TERMINAL_STATUSES:
        started = _parse_timestamp(record.get("started_at") or record.get("created_at"))
        ended = _parse_timestamp(record.get("ended_at") or record.get("updated_at"))
        if started and ended and ended >= started:
            elapsed = int((ended - started).total_seconds() * 1000)
            if elapsed:
                return elapsed
    timings = record.get("timings") if isinstance(record.get("timings"), dict) else {}
    return sum(_safe_int(value) for value in timings.values())


class AnalysisHistoryStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.events_path = self.root / "events.jsonl"
        self.index_path = self.root / "index.json"

    def _empty_index(self) -> dict[str, Any]:
        return {"version": 1, "updated_at": "", "runs": {}}

    def _read_index_unlocked(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return self._empty_index()
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("analysis_history_index_read_failed error_type=%s", exc.__class__.__name__)
            return self._empty_index()
        if not isinstance(data, dict) or not isinstance(data.get("runs"), dict):
            return self._empty_index()
        data.setdefault("version", 1)
        data.setdefault("updated_at", "")
        return data

    def _write_index_unlocked(self, index: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        index["version"] = 1
        index["updated_at"] = _now_text()
        temporary = self.index_path.with_name(
            f"{self.index_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.index_path)

    def _append_event_unlocked(self, event: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")

    def _history_item(
        self,
        record: dict[str, Any],
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous = existing or {}

        def prefer(key: str, default: Any = "") -> Any:
            value = record.get(key)
            if value not in (None, "", []):
                return value
            return previous.get(key, default)

        menu_code = _text(prefer("menu_code"))
        articleid = _text(prefer("articleid"))
        record_id = _text(prefer("record_id"))
        if not record_id and menu_code and articleid:
            record_id = f"{menu_code}:{articleid}"
        notice_id = _text(prefer("notice_id")) or record_id
        status = _text(prefer("status", "created")) or "created"
        created_at = _text(prefer("created_at"))
        updated_at = _text(prefer("updated_at")) or created_at
        ended_at = _text(prefer("ended_at"))
        if not ended_at and status in _TERMINAL_STATUSES:
            ended_at = updated_at
        deliverable = bool(record.get("deliverable", previous.get("deliverable", False)))
        needs_manual_review = bool(
            record.get("needs_manual_review", previous.get("needs_manual_review", False))
        )
        quality_status = "passed" if deliverable else (
            "failed" if status in _TERMINAL_STATUSES else "pending"
        )
        word_download_url = _text(prefer("word_download_url"))
        word_exported_at = _text(prefer("word_exported_at"))
        item = {
            "run_id": _text(prefer("run_id")),
            "pack_id": _text(prefer("pack_id")),
            "record_id": record_id,
            "notice_id": notice_id,
            "title": _text(prefer("title")) or _text(prefer("report_title")),
            "menu_name": _text(prefer("menu_name")),
            "menu_code": menu_code,
            "articleid": articleid,
            "created_at": created_at,
            "updated_at": updated_at,
            "started_at": _text(prefer("started_at")) or created_at,
            "ended_at": ended_at,
            "status": status,
            "quality_status": quality_status,
            "workflow_run_id": _text(prefer("workflow_run_id")),
            "provider_run_id": _text(prefer("provider_run_id")),
            "provider": _text(prefer("provider", "dify")) or "dify",
            "primary_failure_code": _text(prefer("primary_failure_code")),
            "secondary_failure_codes": _string_list(prefer("secondary_failure_codes", [])),
            "quality_failure_codes": _string_list(prefer("quality_failure_codes", [])),
            "generation_failure_codes": _string_list(prefer("generation_failure_codes", [])),
            "word_download_url": word_download_url,
            "word_exported_at": word_exported_at,
            "word_generated": bool(word_download_url or word_exported_at),
            "word_export_available": bool(
                record.get("word_export_available", previous.get("word_export_available", False))
            ),
            "draft_word_export_available": bool(
                record.get(
                    "draft_word_export_available",
                    previous.get("draft_word_export_available", False),
                )
            ),
            "final_word_export_available": bool(
                record.get(
                    "final_word_export_available",
                    previous.get("final_word_export_available", False),
                )
            ),
            "compact_pack_chars": _safe_int(prefer("compact_pack_chars", 0)),
            "input_strategy": _text(prefer("input_strategy")),
            "deliverable": deliverable,
            "needs_manual_review": needs_manual_review,
            "fallback_used": bool(record.get("fallback_used", previous.get("fallback_used", False))),
            "fallback_reason": _text(prefer("fallback_reason")),
            "repair_attempted": bool(
                record.get("repair_attempted", previous.get("repair_attempted", False))
            ),
            "repair_success": bool(record.get("repair_success", previous.get("repair_success", False))),
            "manual_review_reason_summary": _text(prefer("manual_review_reason_summary")),
            "final_blocking_reason": _text(prefer("final_blocking_reason")),
        }
        item["duration_ms"] = _duration_ms({**record, **item}, status)
        return item

    def _record(self, event_name: str, record: dict[str, Any]) -> dict[str, Any]:
        run_id = _text(record.get("run_id"))
        if not run_id:
            raise ValueError("analysis history requires run_id")
        with _STORE_LOCK:
            index = self._read_index_unlocked()
            previous = index["runs"].get(run_id)
            previous_item = previous if isinstance(previous, dict) else None
            item = self._history_item(record, previous_item)
            event = {
                "event": event_name,
                "event_at": _now_text(),
                "run_id": run_id,
                "pack_id": item["pack_id"],
                "status": item["status"],
                "deliverable": item["deliverable"],
                "primary_failure_code": item["primary_failure_code"],
            }
            self._append_event_unlocked(event)
            index["runs"][run_id] = item
            self._write_index_unlocked(index)
            return item

    def record_run_created(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._record("run_created", record)

    def record_run_updated(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._record("run_updated", record)

    def record_word_exported(self, record: dict[str, Any], download_url: str) -> dict[str, Any]:
        exported_at = _now_text()
        payload = {
            **record,
            "word_download_url": download_url,
            "word_exported_at": exported_at,
            "word_export_available": True,
        }
        item = self._record("word_exported", payload)
        return item

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with _STORE_LOCK:
            item = self._read_index_unlocked()["runs"].get(run_id)
            return dict(item) if isinstance(item, dict) else None

    def list_runs(
        self,
        *,
        articleid: str = "",
        record_id: str = "",
        notice_id: str = "",
        pack_id: str = "",
        status: str = "",
        provider: str = "",
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        with _STORE_LOCK:
            values = list(self._read_index_unlocked()["runs"].values())
        filters = {
            "articleid": _text(articleid),
            "record_id": _text(record_id),
            "notice_id": _text(notice_id),
            "pack_id": _text(pack_id),
            "status": _text(status),
            "provider": _text(provider),
        }
        items = [dict(item) for item in values if isinstance(item, dict)]
        for key, expected in filters.items():
            if expected:
                items = [item for item in items if _text(item.get(key)) == expected]
        query_text = _text(query).casefold()
        if query_text:
            items = [
                item
                for item in items
                if query_text
                in " ".join(
                    _text(item.get(key))
                    for key in ("title", "run_id", "pack_id", "record_id", "notice_id")
                ).casefold()
            ]
        items.sort(
            key=lambda item: (
                _text(item.get("updated_at")),
                _text(item.get("created_at")),
                _text(item.get("run_id")),
            ),
            reverse=True,
        )
        total = len(items)
        return items[max(0, offset) : max(0, offset) + max(1, limit)], total

    def rebuild(self, records: list[dict[str, Any]]) -> int:
        runs: dict[str, dict[str, Any]] = {}
        for record in records:
            run_id = _text(record.get("run_id"))
            if not run_id:
                continue
            runs[run_id] = self._history_item(record)
        with _STORE_LOCK:
            self._write_index_unlocked({"version": 1, "updated_at": "", "runs": runs})
            self._append_event_unlocked(
                {"event": "history_rebuilt", "event_at": _now_text(), "total": len(runs)}
            )
        return len(runs)
