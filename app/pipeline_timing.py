from __future__ import annotations

import time
import threading
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

DEFAULT_TIMINGS = {field: 0 for field in TIMING_FIELDS}


def _safe_ms(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def normalize_pipeline_timings(value: Any) -> dict[str, int]:
    normalized = dict(DEFAULT_TIMINGS)
    if isinstance(value, Mapping):
        for key, raw in value.items():
            name = str(key or "").strip()
            if name:
                normalized[name] = _safe_ms(raw)
    dify_ms = max(normalized["dify_ms"], normalized["generation_ms"])
    quality_gate_ms = max(normalized["quality_gate_ms"], normalized["local_quality_gate_ms"])
    word_scan_ms = max(normalized["word_scan_ms"], normalized["export_check_ms"])
    normalized.update(
        {
            "dify_ms": dify_ms,
            "generation_ms": dify_ms,
            "quality_gate_ms": quality_gate_ms,
            "local_quality_gate_ms": quality_gate_ms,
            "word_scan_ms": word_scan_ms,
            "export_check_ms": word_scan_ms,
        }
    )
    return normalized


class PipelineTiming:
    def __init__(
        self,
        initial: Any = None,
        *,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._clock_ns = clock_ns
        self._started_ns = self._clock_ns()
        self._values = normalize_pipeline_timings(initial)
        self._base_total_ms = self._values["total_ms"]
        self._lock = threading.Lock()

    @contextmanager
    def measure(self, field: str) -> Iterator[None]:
        started_ns = self._clock_ns()
        try:
            yield
        finally:
            self.add_elapsed(field, started_ns)

    def add_elapsed(self, field: str, started_ns: int) -> None:
        elapsed_ns = max(0, self._clock_ns() - int(started_ns))
        self.add_ms(field, elapsed_ns // 1_000_000)

    def add_ms(self, field: str, elapsed_ms: Any) -> None:
        name = str(field or "").strip()
        if not name:
            return
        with self._lock:
            self._values[name] = _safe_ms(self._values.get(name)) + _safe_ms(elapsed_ms)

    def merge(self, value: Any) -> None:
        incoming = normalize_pipeline_timings(value)
        with self._lock:
            for field, elapsed_ms in incoming.items():
                self._values[field] = max(_safe_ms(self._values.get(field)), elapsed_ms)

    def observe(self, field: str, elapsed_ms: int) -> None:
        self.add_ms(field, elapsed_ms)

    def snapshot(self, *, finish: bool = False) -> dict[str, int]:
        with self._lock:
            normalized = normalize_pipeline_timings(dict(self._values))
        if finish:
            wall_ms = max(0, self._clock_ns() - self._started_ns) // 1_000_000
            normalized["total_ms"] = max(normalized["total_ms"], self._base_total_ms + wall_ms)
        return normalized
