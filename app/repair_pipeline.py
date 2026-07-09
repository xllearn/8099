from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from app.generation.base import ReportGenerationResult


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
        return result


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
    repairers: list[RepairComponent] = field(default_factory=lambda: [UnsupportedFactRepairer(), ForbiddenPhraseRepairer(), StructureRepairer()])

    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        current = result
        for repairer in self.repairers:
            current = repairer.run(current, pack)
        return current
