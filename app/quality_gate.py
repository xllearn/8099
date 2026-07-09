from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.diagnostics import build_run_diagnostics
from app.generation.base import ReportGenerationResult


class QualityGateComponent(Protocol):
    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        ...


@dataclass
class LocalEvidenceGate:
    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        legacy = result.to_legacy_result()
        diagnostics = build_run_diagnostics(legacy, pack)
        quality_gate = diagnostics.get("quality_gate") if isinstance(diagnostics.get("quality_gate"), dict) else {}
        metadata = dict(result.metadata)
        metadata["quality_gate"] = quality_gate
        if quality_gate.get("deliverable_status") != "needs_manual_review":
            return dataclass_replace(result, metadata=metadata)

        issue = {
            "issue_id": "Q_LOCAL_QUALITY_GATE",
            "severity": "high",
            "problem_type": "local_quality_gate",
            "report_text": "",
            "source_basis": "evidence_pack",
            "fix_instruction": "Local quality gate blocked automatic delivery.",
            "quality_gate": quality_gate,
        }
        quality_check = dict(result.quality_check or {"passed": None, "issues": []})
        issues = list(quality_check.get("issues") or [])
        if not any(isinstance(item, dict) and item.get("issue_id") == issue["issue_id"] for item in issues):
            issues.append(issue)
        quality_check["passed"] = False
        quality_check["issues"] = issues
        remaining_issues = list(result.remaining_issues or [])
        if not any(isinstance(item, dict) and item.get("issue_id") == issue["issue_id"] for item in remaining_issues):
            remaining_issues.append(issue)
        warnings = list(result.generation_warnings or [])
        warning = "Local quality gate blocked automatic delivery."
        if warning not in warnings:
            warnings.append(warning)
        metadata["status"] = "needs_manual_review"
        return dataclass_replace(
            result,
            quality_check=quality_check,
            remaining_issues=remaining_issues,
            generation_warnings=warnings,
            metadata=metadata,
        )


@dataclass
class ForbiddenPhraseGate:
    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        return result


@dataclass
class StructureGate:
    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        return result


@dataclass
class ExportGate:
    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        return result


@dataclass
class QualityGate:
    components: list[QualityGateComponent] = field(default_factory=lambda: [LocalEvidenceGate(), ForbiddenPhraseGate(), StructureGate(), ExportGate()])

    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        current = result
        for component in self.components:
            current = component.run(current, pack)
        return current


def dataclass_replace(result: ReportGenerationResult, **changes: Any) -> ReportGenerationResult:
    data = {
        "success": result.success,
        "provider": result.provider,
        "provider_run_id": result.provider_run_id,
        "report_title": result.report_title,
        "report_markdown": result.report_markdown,
        "report_ir": result.report_ir,
        "quality_check": result.quality_check,
        "remaining_issues": result.remaining_issues,
        "generation_warnings": result.generation_warnings,
        "raw_response_hash": result.raw_response_hash,
        "raw_response_excerpt": result.raw_response_excerpt,
        "generation_failure_codes": result.generation_failure_codes,
        "metadata": result.metadata,
    }
    data.update(changes)
    return ReportGenerationResult(**data)

