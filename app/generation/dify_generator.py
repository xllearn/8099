from __future__ import annotations

from typing import Any, Callable

from app.generation.base import ReportGenerationResult


class DifyReportGenerator:
    provider = "dify"

    def __init__(self, workflow_callable: Callable[..., dict[str, Any]]) -> None:
        self._workflow_callable = workflow_callable

    def generate(
        self,
        pack_id: str,
        run_id: str,
        pack: dict[str, Any] | None = None,
        report_memory: str = "",
        use_report_memory: bool = False,
    ) -> ReportGenerationResult:
        try:
            raw = self._workflow_callable(
                pack_id,
                run_id,
                pack,
                report_memory,
                use_report_memory=use_report_memory,
            )
        except TypeError as exc:
            if "positional" not in str(exc) and "argument" not in str(exc):
                raise
            raw = self._workflow_callable(pack_id, run_id, pack)
        return ReportGenerationResult.from_legacy_result(raw if isinstance(raw, dict) else {}, provider=self.provider)

