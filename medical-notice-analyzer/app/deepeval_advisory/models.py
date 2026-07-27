from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


_CANONICAL_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _canonical_timestamp(value: str) -> str:
    if not _CANONICAL_TIMESTAMP_PATTERN.fullmatch(value):
        raise ValueError("timestamp must be UTC with millisecond precision")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ValueError("timestamp must contain a real UTC datetime") from exc
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )


class RunSnapshot(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^run_[A-Za-z0-9_-]{8,80}$")
    pack_id: str = Field(pattern=r"^pack_[A-Za-z0-9_-]{8,80}$")
    run_ref: str = Field(pattern=r"^runref_[0-9a-f]{32}$")
    status: Literal[
        "created",
        "preparing",
        "running",
        "generating",
        "generated",
        "local_quality_checking",
        "repairing",
        "fallback_generating",
        "export_checking",
        "finished",
        "needs_manual_review",
        "failed",
        "interrupted",
    ]
    report_version: int = Field(ge=1)
    report_markdown: str
    report_ir: dict[str, Any] | None
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    eligible: bool
    ineligible_reason: str | None = None
    source_updated_at: str | None = None


class JobStatus(StrEnum):
    DISCOVERED = "discovered"
    PENDING = "pending"
    PAUSED = "paused"
    RUNNING = "running"
    COMPLETED = "completed"
    CACHED = "cached"
    RETRYING = "retrying"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    OVER_BUDGET = "over_budget"
    INDETERMINATE = "indeterminate"


class MetricStatus(StrEnum):
    SCORED = "scored"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class SafeLocator(StrictModel):
    kind: Literal[
        "article",
        "pdf_page",
        "paragraph",
        "sheet_cell",
        "table_cell",
    ]
    ordinal: int | None = Field(default=None, ge=1)
    table_index: int | None = Field(default=None, ge=1)
    row: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)


class EvidenceExcerpt(StrictModel):
    local_id: str = Field(pattern=r"^e[1-8]$")
    level: Literal["A", "B"]
    kind: str = Field(min_length=1, max_length=64)
    excerpt: str = Field(min_length=1, max_length=1500)
    parent_a_ids: tuple[str, ...] = Field(default=(), max_length=8)
    locator: SafeLocator | None = None


class ProjectionUnit(StrictModel):
    unit_id: str = Field(pattern=r"^unit_[0-9a-f]{16}$")
    kind: Literal["claim", "section"]
    text: str = Field(min_length=1, max_length=6000)
    claim_count: int = Field(ge=1, le=8)
    evidence: tuple[EvidenceExcerpt, ...] = Field(default=(), max_length=8)
    expected_facts: tuple[str, ...] = ()
    attachment_expectation: str | None = Field(default=None, max_length=2000)


class AdvisoryProjection(StrictModel):
    schema_version: Literal["8099.deepeval-projection/v1"] = (
        "8099.deepeval-projection/v1"
    )
    projection_version: Literal["claim-ab-v1"] = "claim-ab-v1"
    run_ref: str = Field(pattern=r"^runref_[0-9a-f]{32}$")
    report_version: int = Field(ge=1)
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    units: tuple[ProjectionUnit, ...] = Field(min_length=1)
    projection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AdvisoryJob(StrictModel):
    schema_version: Literal["8099.deepeval-job/v1"] = "8099.deepeval-job/v1"
    job_id: str = Field(pattern=r"^job_[A-Za-z0-9_-]{8,80}$")
    run_ref: str = Field(pattern=r"^runref_[0-9a-f]{32}$")
    report_version: int = Field(ge=1)
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    projection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    advisory_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: JobStatus
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    evaluation_id: str | None = None
    source_evaluation_id: str | None = None
    error_category: str | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        return _canonical_timestamp(value)

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must be on or after created_at")
        return self


class MetricObservation(StrictModel):
    metric_id: str = Field(min_length=1, max_length=80)
    metric_version: str = Field(min_length=1, max_length=40)
    status: MetricStatus
    score: float | None = Field(default=None, ge=0, le=1)
    bounded_reason: str | None = Field(default=None, max_length=500)
    evidence_references: tuple[str, ...] = ()
    unsupported_spans: tuple[tuple[int, int], ...] = ()
    error_category: str | None = None

    @model_validator(mode="after")
    def validate_status_fields(self) -> Self:
        if self.status is MetricStatus.SCORED:
            if self.score is None:
                raise ValueError("scored metrics require a score")
            if self.error_category is not None:
                raise ValueError("scored metrics cannot have an error category")
            return self

        if self.score is not None:
            raise ValueError(f"{self.status.value} metrics cannot have a score")
        if (
            self.status is MetricStatus.NOT_APPLICABLE
            and self.error_category is not None
        ):
            raise ValueError(
                "not_applicable metrics cannot have an error category"
            )
        if self.status is MetricStatus.ERROR and not (
            self.error_category and self.error_category.strip()
        ):
            raise ValueError("error metrics require an error category")
        return self


class AdvisoryResult(StrictModel):
    schema_version: Literal["8099.deepeval-result/v1"] = (
        "8099.deepeval-result/v1"
    )
    purpose: Literal["advisory_observability"] = "advisory_observability"
    blocking: Literal[False] = False
    affects_deliverable: Literal[False] = False
    affects_report_status: Literal[False] = False
    affects_release: Literal[False] = False
    affects_user_response: Literal[False] = False
    evaluation_id: str = Field(pattern=r"^eval_[A-Za-z0-9_-]{8,80}$")
    job_id: str = Field(pattern=r"^job_[A-Za-z0-9_-]{8,80}$")
    run_ref: str = Field(pattern=r"^runref_[0-9a-f]{32}$")
    advisory_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metric_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: JobStatus
    metrics: tuple[MetricObservation, ...]
    judge_provider: str = ""
    resolved_model_or_profile_version: str = ""
    token_input: int = Field(default=0, ge=0)
    token_output: int = Field(default=0, ge=0)
    cost: float = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0, le=2)
    input_fingerprint: str = ""
    output_fingerprint: str = ""
    created_at: str = Field(default_factory=utc_now_iso)

    @field_validator("created_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        return _canonical_timestamp(value)

    @model_validator(mode="after")
    def validate_terminal_invariants(self) -> Self:
        terminal_statuses = {
            JobStatus.COMPLETED,
            JobStatus.CACHED,
            JobStatus.PARTIAL,
            JobStatus.UNAVAILABLE,
            JobStatus.OVER_BUDGET,
            JobStatus.INDETERMINATE,
        }
        if self.status not in terminal_statuses:
            raise ValueError("advisory results require a terminal status")

        metric_statuses = tuple(metric.status for metric in self.metrics)
        if self.status in {JobStatus.COMPLETED, JobStatus.CACHED}:
            if not self.metrics:
                raise ValueError(
                    "completed and cached results require metrics"
                )
            if any(
                status
                not in {MetricStatus.SCORED, MetricStatus.NOT_APPLICABLE}
                for status in metric_statuses
            ):
                raise ValueError(
                    "completed and cached metrics must be final observations"
                )
            self._require_judge_identity()
        elif self.status is JobStatus.PARTIAL:
            if MetricStatus.SCORED not in metric_statuses:
                raise ValueError("partial results require a scored metric")
            if not any(
                status in {MetricStatus.UNAVAILABLE, MetricStatus.ERROR}
                for status in metric_statuses
            ):
                raise ValueError(
                    "partial results require an unavailable or error metric"
                )
            self._require_judge_identity()
        elif MetricStatus.SCORED in metric_statuses:
            raise ValueError(f"{self.status.value} results cannot be scored")
        return self

    def _require_judge_identity(self) -> None:
        if not self.judge_provider.strip():
            raise ValueError("judge_provider must be nonblank")
        if not self.resolved_model_or_profile_version.strip():
            raise ValueError(
                "resolved_model_or_profile_version must be nonblank"
            )
