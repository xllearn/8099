from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, Protocol

from app.diagnostics import build_run_diagnostics
from app.evidence_index import EvidenceIndexError, build_claim_evidence_index
from app.formal_body import FormalBodyDocument
from app.formal_body_safety import scan_formal_body
from app.generation.base import ReportGenerationResult
from app.vbp_quality_gate import evaluate_vbp_quality


class QualityGateComponent(Protocol):
    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        ...


def _env_bool(name: str, default: bool = False) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    return default if not value else value in {"1", "true", "yes", "on"}


@dataclass
class ClaimEvidenceIndexComponent:
    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        if not _env_bool("ENABLE_EVIDENCE_INDEX", False):
            return result
        metadata = dict(result.metadata)
        try:
            index = build_claim_evidence_index(
                FormalBodyDocument(markdown=result.report_markdown, report_ir=result.report_ir),
                pack,
            )
            metadata["claim_evidence_index"] = index
            metadata["claim_index_error"] = ""
        except EvidenceIndexError:
            metadata["claim_evidence_index"] = None
            metadata["claim_index_error"] = "EVIDENCE_INDEX_INVALID"
        return dataclass_replace(result, metadata=metadata)


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
        safety = scan_formal_body(
            FormalBodyDocument(markdown=result.report_markdown, report_ir=result.report_ir)
        )
        metadata = dict(result.metadata)
        metadata.update(
            {
                "formal_body_present": safety.has_body,
                "body_safety_passed": safety.safe,
                "forbidden_phrase_hits": [
                    {"phrase": hit.phrase, "location": hit.location} for hit in safety.hits
                ],
            }
        )
        if safety.safe:
            return dataclass_replace(result, metadata=metadata)

        issue_id = "Q_FORMAL_BODY_EMPTY" if not safety.has_body else "Q_FORBIDDEN_PHRASE_FORMAL_BODY"
        issue = {
            "issue_id": issue_id,
            "severity": "blocker",
            "problem_type": "formal_body_safety",
            "report_text": "",
            "source_basis": "evidence_pack",
            "fix_instruction": "Formal body safety gate blocked Word publication.",
        }
        quality_check = dict(result.quality_check or {"passed": None, "issues": []})
        issues = list(quality_check.get("issues") or [])
        if not any(isinstance(item, dict) and item.get("issue_id") == issue_id for item in issues):
            issues.append(issue)
        quality_check["passed"] = False
        quality_check["issues"] = issues
        remaining_issues = list(result.remaining_issues or [])
        if not any(isinstance(item, dict) and item.get("issue_id") == issue_id for item in remaining_issues):
            remaining_issues.append(issue)
        metadata["status"] = "needs_manual_review"
        metadata["body_safety_failure_code"] = (
            "FORMAL_BODY_EMPTY" if not safety.has_body else "FORBIDDEN_PHRASE_IN_FORMAL_BODY"
        )
        return dataclass_replace(
            result,
            quality_check=quality_check,
            remaining_issues=remaining_issues,
            metadata=metadata,
        )


@dataclass
class VbpQualityGateComponent:
    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        if not _env_bool("ENABLE_VBP_QUALITY_GATE", False):
            return result
        metadata = dict(result.metadata)
        claim_index = metadata.get("claim_evidence_index")
        if not isinstance(claim_index, dict):
            try:
                claim_index = build_claim_evidence_index(
                    FormalBodyDocument(markdown=result.report_markdown, report_ir=result.report_ir),
                    pack,
                )
            except EvidenceIndexError:
                claim_index = {}
        evaluation = evaluate_vbp_quality(
            FormalBodyDocument(markdown=result.report_markdown, report_ir=result.report_ir),
            pack,
            claim_index,
        )
        metadata["vbp_quality_gate"] = evaluation
        quality_gate = dict(metadata.get("quality_gate") or {})
        quality_gate["vbp_quality_gate"] = evaluation
        if evaluation["passed"]:
            metadata["quality_gate"] = quality_gate
            return dataclass_replace(result, metadata=metadata)

        existing_codes = [str(item or "") for item in list(quality_gate.get("blocking_issue_codes") or [])]
        quality_gate["blocking_issue_codes"] = list(
            dict.fromkeys([*existing_codes, *evaluation["blocking_issue_codes"]])
        )
        quality_gate["deliverable_status"] = "needs_manual_review"
        metadata["quality_gate"] = quality_gate
        metadata["status"] = "needs_manual_review"
        metadata["quality_failure_codes"] = list(
            dict.fromkeys(
                [
                    *[str(item or "") for item in list(metadata.get("quality_failure_codes") or [])],
                    *evaluation["blocking_issue_codes"],
                ]
            )
        )

        quality_check = dict(result.quality_check or {"passed": None, "issues": []})
        issues = list(quality_check.get("issues") or [])
        remaining = list(result.remaining_issues or [])
        for code in evaluation["blocking_issue_codes"]:
            issue = {
                "issue_id": f"Q_{code}",
                "code": code,
                "severity": "blocker",
                "problem_type": "vbp_unsupported_claim" if code == "VBP_UNSUPPORTED_CLAIM" else "vbp_quality_gate",
                "report_text": "",
                "source_basis": "validated_a_b_evidence",
                "fix_instruction": "Remove unsupported content or narrow it to validated A/B evidence.",
            }
            if not any(isinstance(item, dict) and item.get("issue_id") == issue["issue_id"] for item in issues):
                issues.append(issue)
            if not any(isinstance(item, dict) and item.get("issue_id") == issue["issue_id"] for item in remaining):
                remaining.append(issue)
        quality_check["passed"] = False
        quality_check["issues"] = issues
        return dataclass_replace(
            result,
            quality_check=quality_check,
            remaining_issues=remaining,
            metadata=metadata,
        )


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
    components: list[QualityGateComponent] = field(
        default_factory=lambda: [
            ClaimEvidenceIndexComponent(),
            LocalEvidenceGate(),
            VbpQualityGateComponent(),
            ForbiddenPhraseGate(),
            StructureGate(),
            ExportGate(),
        ]
    )

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

