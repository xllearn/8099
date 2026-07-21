from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ReportGenerationResult:
    success: bool
    provider: str
    provider_run_id: str = ""
    report_title: str = ""
    report_markdown: str = ""
    report_ir: dict[str, Any] | None = None
    quality_check: dict[str, Any] = field(default_factory=dict)
    remaining_issues: list[Any] = field(default_factory=list)
    generation_warnings: list[str] = field(default_factory=list)
    raw_response_hash: str = ""
    raw_response_excerpt: str = ""
    generation_failure_codes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_legacy_result(cls, value: dict[str, Any], provider: str = "dify") -> "ReportGenerationResult":
        provider_run_id = str(value.get("provider_run_id") or value.get("workflow_run_id") or "")
        status = str(value.get("status") or "").strip()
        success = bool(value.get("success", status != "failed"))
        warnings = value.get("generation_warnings") or value.get("warnings") or []
        if not isinstance(warnings, list):
            warnings = [str(warnings)]
        metadata = dict(value)
        return cls(
            success=success,
            provider=str(value.get("provider") or provider or "").strip() or "dify",
            provider_run_id=provider_run_id,
            report_title=str(value.get("report_title") or ""),
            report_markdown=str(value.get("report_markdown") or ""),
            report_ir=value.get("report_ir") if isinstance(value.get("report_ir"), dict) else None,
            quality_check=value.get("quality_check") if isinstance(value.get("quality_check"), dict) else {},
            remaining_issues=list(value.get("remaining_issues") or []),
            generation_warnings=[str(item) for item in warnings],
            raw_response_hash=str(value.get("raw_response_hash") or ""),
            raw_response_excerpt=str(value.get("raw_response_excerpt") or ""),
            generation_failure_codes=[str(item) for item in value.get("generation_failure_codes") or []],
            metadata=metadata,
        )

    def to_legacy_result(self) -> dict[str, Any]:
        result = dict(self.metadata)
        result.update(
            {
                "success": self.success,
                "provider": self.provider,
                "provider_run_id": self.provider_run_id,
                "report_title": self.report_title,
                "report_markdown": self.report_markdown,
                "report_ir": self.report_ir,
                "quality_check": dict(self.quality_check),
                "remaining_issues": list(self.remaining_issues),
                "generation_warnings": list(self.generation_warnings),
                "raw_response_hash": self.raw_response_hash,
                "raw_response_excerpt": self.raw_response_excerpt,
                "generation_failure_codes": list(self.generation_failure_codes),
            }
        )
        if self.generation_warnings or "warnings" in self.metadata:
            result["warnings"] = list(self.generation_warnings)
        if self.provider_run_id and not result.get("workflow_run_id"):
            result["workflow_run_id"] = self.provider_run_id
        if not result.get("status"):
            result["status"] = "finished" if self.success else "failed"
        return result


class ReportGenerator(Protocol):
    provider: str

    def generate(
        self,
        pack_id: str,
        run_id: str,
        pack: dict[str, Any] | None = None,
        report_memory: str = "",
        use_report_memory: bool = False,
    ) -> ReportGenerationResult:
        ...
