from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping


CANONICAL_STAGE_FIELDS = (
    "prepare_ms",
    "attachment_queue_ms",
    "attachment_download_ms",
    "attachment_parse_ms",
    "evidence_build_ms",
    "compact_ms",
    "dify_ms",
    "repair_ms",
    "quality_gate_ms",
    "word_render_ms",
    "word_scan_ms",
    "word_publish_ms",
)

TIMING_FIELDS = (
    *CANONICAL_STAGE_FIELDS,
    "generation_ms",
    "local_quality_gate_ms",
    "export_check_ms",
    "total_ms",
)

OBSERVATION_STATUS_FIELD = "observation_status"
OBSERVED = "observed"
NOT_OBSERVED = "not_observed"
COLLECTION_FAILED = "collection_failed"
OBSERVATION_STATUSES = frozenset({OBSERVED, NOT_OBSERVED, COLLECTION_FAILED})

DEFAULT_TIMINGS: dict[str, Any] = {
    **{field: None for field in TIMING_FIELDS},
    OBSERVATION_STATUS_FIELD: {field: NOT_OBSERVED for field in TIMING_FIELDS},
}


def _safe_ms(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        elapsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return elapsed if elapsed >= 0 else None


def _sync_alias(
    values: dict[str, int | None],
    statuses: dict[str, str],
    left: str,
    right: str,
) -> None:
    observed = [
        value
        for field in (left, right)
        if statuses[field] == OBSERVED
        if (value := values[field]) is not None
    ]
    if observed:
        value = max(observed)
        values[left] = value
        values[right] = value
        statuses[left] = OBSERVED
        statuses[right] = OBSERVED
    elif COLLECTION_FAILED in {statuses[left], statuses[right]}:
        values[left] = None
        values[right] = None
        statuses[left] = COLLECTION_FAILED
        statuses[right] = COLLECTION_FAILED


def normalize_pipeline_timings(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    raw_statuses = source.get(OBSERVATION_STATUS_FIELD)
    explicit_statuses = raw_statuses if isinstance(raw_statuses, Mapping) else {}
    values: dict[str, int | None] = {}
    statuses: dict[str, str] = {}
    for field in TIMING_FIELDS:
        raw_value = source.get(field) if field in source else None
        elapsed = _safe_ms(raw_value) if field in source else None
        explicit = str(explicit_statuses.get(field) or "").strip()
        if explicit == OBSERVED:
            status = OBSERVED if elapsed is not None else COLLECTION_FAILED
        elif explicit in {NOT_OBSERVED, COLLECTION_FAILED}:
            elapsed = None
            status = explicit
        elif field in source and elapsed is not None:
            status = OBSERVED
        elif field in source and raw_value is not None and raw_value != "":
            status = COLLECTION_FAILED
        else:
            status = NOT_OBSERVED
        values[field] = elapsed if status == OBSERVED else None
        statuses[field] = status

    _sync_alias(values, statuses, "dify_ms", "generation_ms")
    _sync_alias(values, statuses, "quality_gate_ms", "local_quality_gate_ms")
    _sync_alias(values, statuses, "word_scan_ms", "export_check_ms")
    return {**values, OBSERVATION_STATUS_FIELD: statuses}


class PipelineTiming:
    def __init__(
        self,
        initial: Any = None,
        *,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._clock_ns = clock_ns
        self._lock = threading.Lock()
        normalized = normalize_pipeline_timings(initial)
        self._values = {field: normalized[field] for field in TIMING_FIELDS}
        self._statuses = dict(normalized[OBSERVATION_STATUS_FIELD])
        self._base_total_ms = (
            int(self._values["total_ms"] or 0)
            if self._statuses["total_ms"] == OBSERVED
            else 0
        )
        try:
            self._started_ns: int | None = int(self._clock_ns())
        except Exception:  # noqa: BLE001
            self._started_ns = None
            self._values["total_ms"] = None
            self._statuses["total_ms"] = COLLECTION_FAILED

    def _mark_failed(self, field: str) -> None:
        name = str(field or "").strip()
        if name not in TIMING_FIELDS:
            return
        with self._lock:
            self._values[name] = None
            self._statuses[name] = COLLECTION_FAILED

    @contextmanager
    def measure(self, field: str) -> Iterator[None]:
        try:
            started_ns: int | None = int(self._clock_ns())
        except Exception:  # noqa: BLE001
            started_ns = None
            self._mark_failed(field)
        try:
            yield
        finally:
            if started_ns is not None:
                self.add_elapsed(field, started_ns)

    def add_elapsed(self, field: str, started_ns: int) -> None:
        try:
            elapsed_ns = max(0, int(self._clock_ns()) - int(started_ns))
        except Exception:  # noqa: BLE001
            self._mark_failed(field)
            return
        self.add_ms(field, elapsed_ns // 1_000_000)

    def add_ms(self, field: str, elapsed_ms: Any) -> None:
        name = str(field or "").strip()
        if name not in TIMING_FIELDS:
            return
        elapsed = _safe_ms(elapsed_ms)
        if elapsed is None:
            self._mark_failed(name)
            return
        with self._lock:
            current = (
                int(self._values.get(name) or 0)
                if self._statuses.get(name) == OBSERVED
                else 0
            )
            self._values[name] = current + elapsed
            self._statuses[name] = OBSERVED

    def merge(self, value: Any) -> None:
        incoming = normalize_pipeline_timings(value)
        incoming_statuses = incoming[OBSERVATION_STATUS_FIELD]
        with self._lock:
            for field in TIMING_FIELDS:
                status = incoming_statuses[field]
                elapsed = incoming[field]
                if status == OBSERVED and elapsed is not None:
                    current = (
                        int(self._values.get(field) or 0)
                        if self._statuses.get(field) == OBSERVED
                        else 0
                    )
                    self._values[field] = max(current, elapsed)
                    self._statuses[field] = OBSERVED
                elif status == COLLECTION_FAILED:
                    self._values[field] = None
                    self._statuses[field] = COLLECTION_FAILED

    def observe(self, field: str, elapsed_ms: int) -> None:
        self.add_ms(field, elapsed_ms)

    def snapshot(self, *, finish: bool = False) -> dict[str, Any]:
        with self._lock:
            snapshot = {
                **{field: self._values.get(field) for field in TIMING_FIELDS},
                OBSERVATION_STATUS_FIELD: dict(self._statuses),
            }
        if finish:
            if self._started_ns is None:
                snapshot["total_ms"] = None
                snapshot[OBSERVATION_STATUS_FIELD]["total_ms"] = COLLECTION_FAILED
            else:
                try:
                    wall_ms = max(0, int(self._clock_ns()) - self._started_ns) // 1_000_000
                except Exception:  # noqa: BLE001
                    snapshot["total_ms"] = None
                    snapshot[OBSERVATION_STATUS_FIELD]["total_ms"] = COLLECTION_FAILED
                else:
                    snapshot["total_ms"] = max(
                        int(snapshot.get("total_ms") or 0),
                        self._base_total_ms + wall_ms,
                    )
                    snapshot[OBSERVATION_STATUS_FIELD]["total_ms"] = OBSERVED
        return normalize_pipeline_timings(snapshot)
