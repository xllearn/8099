from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Protocol

from app.generation.base import ReportGenerationResult
from app.formal_body import FormalBodyDocument
from app.formal_body_safety import sanitize_formal_body


class RepairComponent(Protocol):
    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        ...


@dataclass
class UnsupportedFactRepairer:
    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        return result


@dataclass
class ForbiddenPhraseRepairer:
    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        safety = sanitize_formal_body(
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
                "forbidden_phrases_removed": list(safety.removed_phrases),
            }
        )
        return replace(
            result,
            report_markdown=safety.document.markdown,
            report_ir=safety.document.report_ir,
            metadata=metadata,
        )


@dataclass
class StructureRepairer:
    legacy_repair: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None

    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        if self.legacy_repair is None:
            return result
        repaired = self.legacy_repair(result.to_legacy_result(), pack)
        return ReportGenerationResult.from_legacy_result(repaired if isinstance(repaired, dict) else result.to_legacy_result(), provider=result.provider)


@dataclass
class RepairPipeline:
    repairers: list[RepairComponent] = field(
        default_factory=lambda: [UnsupportedFactRepairer(), StructureRepairer(), ForbiddenPhraseRepairer()]
    )

    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        current = result
        for repairer in self.repairers:
            current = repairer.run(current, pack)
        return current
