from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
import re
import sys
import time
from typing import Any, Literal

from deepeval.metrics import (
    AnswerRelevancyMetric,
    FaithfulnessMetric,
    GEval,
)
from deepeval.metrics.g_eval import Rubric
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, SingleTurnParams

from app.deepeval_advisory.hashing import (
    BoundaryViolation,
    assert_safe_outbound_text,
    canonical_sha256,
)
from app.deepeval_advisory.judge import (
    JudgeCallContext,
    JudgeError,
    JudgeUsageRecord,
    JudgeUsageSnapshot,
    judge_context,
)
from app.deepeval_advisory.models import (
    AdvisoryJob,
    AdvisoryProjection,
    AdvisoryResult,
    JobStatus,
    MetricObservation,
    MetricStatus,
    ProjectionUnit,
    utc_now_iso,
)


DEEPEVAL_VERSION = "4.1.3"
METRIC_SET_VERSION = "8099-advisory-metrics-v1"
PROMPT_VERSION = "8099-advisory-prompts-v1"
RESPONSE_SCHEMA_VERSION = "8099.deepeval-result/v1"
SAFETY_PREAMBLE_VERSION = "8099-judge-safety-v1"

METRIC_IDS = (
    "claim_faithfulness_v1",
    "critical_coverage_v1",
    "attachment_state_consistency_v1",
    "answer_relevancy_v1",
)

CRITICAL_COVERAGE_EVALUATION_STEPS = (
    "逐项对照预期关键事实与实际输出，判断每一项是否被明确且准确地表达。",
    "只评估关键事实的覆盖与准确性，不评价文风、结构、相关性或其他维度。",
    "依据关键事实遗漏、矛盾或不准确的数量和重要性，按照固定量表评分。",
)
CRITICAL_COVERAGE_RUBRIC = (
    ((0, 2), "关键要求大部分缺失或相互矛盾。"),
    ((3, 5), "覆盖部分关键要求，但存在明显遗漏。"),
    ((6, 8), "覆盖主要关键要求，仅有有限遗漏。"),
    ((9, 10), "完整、准确覆盖全部关键要求。"),
)

ATTACHMENT_CONSISTENCY_EVALUATION_STEPS = (
    "对照预期附件状态与实际输出，检查附件存在、缺失、可解析性及证据来源的描述。",
    "只评估附件状态一致性，不评价其他事实、文风、结构或相关性。",
    "依据附件状态遗漏、误述或矛盾的严重程度，按照固定量表评分。",
)
ATTACHMENT_CONSISTENCY_RUBRIC = (
    ((0, 2), "附件状态描述大部分缺失、错误或相互矛盾。"),
    ((3, 5), "附件状态仅部分一致，存在明显遗漏或误述。"),
    ((6, 8), "附件状态总体一致，仅有有限遗漏或轻微误述。"),
    ((9, 10), "附件状态描述完整、准确且与预期完全一致。"),
)

_BUSINESS_TASK = (
    "请依据提供的采购公告材料，形成准确、完整且相关的分析结论。"
)
_METRIC_VERSION = "1"
_EVALUATION_ID_PATTERN = re.compile(r"^eval_[A-Za-z0-9_-]{8,80}$")
_MAX_JOB_SUBCALLS = 64
_CONTEXT_FRESH_SECONDS = 30.0
_JOB_DEADLINE_SECONDS = 120.0
_MAX_LEDGER_INTEGER = (1 << 63) - 1
_MAX_LEDGER_COST = Decimal(str(sys.float_info.max))
_OVER_BUDGET_CATEGORIES = frozenset(
    {
        "request_too_large",
        "subcall_budget_exceeded",
    }
)
_UNSAFE_REASON = (
    "Judge reason omitted because it violated the safety boundary."
)


class _MetricEvaluationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _UnitOutcome:
    status: MetricStatus
    score: float | None = None
    reason: str | None = None


class _UsageLedger:
    def __init__(self) -> None:
        self.token_input = 0
        self.token_output = 0
        self.cost = Decimal("0")
        self.latency_ms = 0
        self.accepted_response_count = 0
        self._records: list[dict[str, Any]] = []

    def note_snapshot(
        self,
        *,
        context: JudgeCallContext,
        unit_ordinal: int,
        snapshot: JudgeUsageSnapshot,
    ) -> None:
        if (
            not isinstance(snapshot, JudgeUsageSnapshot)
            or type(snapshot.response_count) is not int
            or snapshot.response_count < 0
            or type(snapshot.records) is not tuple
            or snapshot.response_count != len(snapshot.records)
        ):
            raise _MetricEvaluationError("usage ledger is invalid")

        new_accepted_response_count = _checked_integer_sum(
            self.accepted_response_count,
            snapshot.response_count,
        )
        new_token_input = _checked_integer_sum(
            self.token_input,
            snapshot.token_input,
        )
        new_token_output = _checked_integer_sum(
            self.token_output,
            snapshot.token_output,
        )
        new_latency_ms = _checked_integer_sum(
            self.latency_ms,
            snapshot.latency_ms,
        )
        if type(snapshot.cost) is not float:
            raise _MetricEvaluationError("usage ledger is invalid")
        try:
            snapshot_cost = Decimal(str(snapshot.cost))
            new_cost = self.cost + snapshot_cost
        except (InvalidOperation, TypeError, ValueError):
            raise _MetricEvaluationError("usage ledger is invalid") from None
        if (
            not snapshot_cost.is_finite()
            or snapshot_cost < 0
            or not new_cost.is_finite()
            or new_cost < 0
            or new_cost > _MAX_LEDGER_COST
        ):
            raise _MetricEvaluationError("usage ledger overflow")

        new_records: list[dict[str, Any]] = []
        record_token_input = 0
        record_token_output = 0
        record_cost = Decimal("0")
        record_latency_ms = 0
        previous_sequence = 0
        for record in snapshot.records:
            if (
                not isinstance(record, JudgeUsageRecord)
                or type(record.sequence) is not int
                or record.sequence < 1
                or record.sequence <= previous_sequence
                or type(record.cost) is not float
                or type(record.input_fingerprint) is not str
                or type(record.output_fingerprint) is not str
                or not re.fullmatch(
                    r"[0-9a-f]{64}",
                    record.input_fingerprint,
                )
                or not re.fullmatch(
                    r"[0-9a-f]{64}",
                    record.output_fingerprint,
                )
            ):
                raise _MetricEvaluationError(
                    "usage fingerprint record is invalid"
                )
            previous_sequence = record.sequence
            record_token_input = _checked_integer_sum(
                record_token_input,
                record.token_input,
            )
            record_token_output = _checked_integer_sum(
                record_token_output,
                record.token_output,
            )
            record_latency_ms = _checked_integer_sum(
                record_latency_ms,
                record.latency_ms,
            )
            try:
                item_cost = Decimal(str(record.cost))
                record_cost += item_cost
            except (InvalidOperation, TypeError, ValueError):
                raise _MetricEvaluationError(
                    "usage ledger is invalid"
                ) from None
            if (
                not item_cost.is_finite()
                or item_cost < 0
                or not record_cost.is_finite()
                or record_cost < 0
                or record_cost > _MAX_LEDGER_COST
            ):
                raise _MetricEvaluationError("usage ledger overflow")
            new_records.append(
                {
                    "ordinal": len(self._records)
                    + len(new_records)
                    + 1,
                    "request_id": context.request_id,
                    "metric_id": context.metric_id,
                    "metric_version": context.metric_version,
                    "unit_ordinal": unit_ordinal,
                    "sequence": record.sequence,
                    "input_fingerprint": record.input_fingerprint,
                    "output_fingerprint": record.output_fingerprint,
                }
            )

        if (
            record_token_input != snapshot.token_input
            or record_token_output != snapshot.token_output
            or float(record_cost) != snapshot.cost
            or record_latency_ms != snapshot.latency_ms
        ):
            raise _MetricEvaluationError("usage ledger is invalid")

        self.accepted_response_count = new_accepted_response_count
        self.token_input = new_token_input
        self.token_output = new_token_output
        self.latency_ms = new_latency_ms
        self.cost = new_cost
        self._records.extend(new_records)

    def note_snapshot_failure(self, error: JudgeError) -> None:
        if error.category == "usage_overflow":
            self.accepted_response_count += 1

    def fingerprints(self) -> tuple[str, str]:
        records = tuple(self._records)
        return (
            canonical_sha256({"kind": "input", "records": records}),
            canonical_sha256({"kind": "output", "records": records}),
        )

    def cost_float(self) -> float:
        value = float(self.cost)
        if not math.isfinite(value) or value < 0:
            raise _MetricEvaluationError("usage ledger overflow")
        return value


def _checked_integer_sum(current: int, addition: int) -> int:
    if (
        type(addition) is not int
        or addition < 0
        or addition > _MAX_LEDGER_INTEGER
    ):
        raise _MetricEvaluationError("usage ledger is invalid")
    result = current + addition
    if result > _MAX_LEDGER_INTEGER:
        raise _MetricEvaluationError("usage ledger overflow")
    return result


def metric_set_sha256() -> str:
    return canonical_sha256(
        {
            "metric_set_version": METRIC_SET_VERSION,
            "metrics": METRIC_IDS,
            "prompt_version": PROMPT_VERSION,
            "response_schema_version": RESPONSE_SCHEMA_VERSION,
            "safety_preamble_version": SAFETY_PREAMBLE_VERSION,
        }
    )


def _rubrics(
    definitions: tuple[tuple[tuple[int, int], str], ...],
) -> list[Rubric]:
    return [
        Rubric(
            score_range=score_range,
            expected_outcome=expected_outcome,
        )
        for score_range, expected_outcome in definitions
    ]


def _build_metrics(judge: DeepEvalBaseLLM) -> tuple[Any, ...]:
    faithfulness = FaithfulnessMetric(
        threshold=0.0,
        model=judge,
        include_reason=True,
        async_mode=True,
        strict_mode=False,
        verbose_mode=False,
    )
    critical_coverage = GEval(
        name=METRIC_IDS[1],
        evaluation_params=[
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.EXPECTED_OUTPUT,
        ],
        evaluation_steps=list(CRITICAL_COVERAGE_EVALUATION_STEPS),
        rubric=_rubrics(CRITICAL_COVERAGE_RUBRIC),
        model=judge,
        threshold=0.0,
        async_mode=True,
        strict_mode=False,
        verbose_mode=False,
    )
    attachment_consistency = GEval(
        name=METRIC_IDS[2],
        evaluation_params=[
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.EXPECTED_OUTPUT,
        ],
        evaluation_steps=list(
            ATTACHMENT_CONSISTENCY_EVALUATION_STEPS
        ),
        rubric=_rubrics(ATTACHMENT_CONSISTENCY_RUBRIC),
        model=judge,
        threshold=0.0,
        async_mode=True,
        strict_mode=False,
        verbose_mode=False,
    )
    answer_relevancy = AnswerRelevancyMetric(
        threshold=0.0,
        model=judge,
        include_reason=True,
        async_mode=True,
        strict_mode=False,
        verbose_mode=False,
    )
    return (
        faithfulness,
        critical_coverage,
        attachment_consistency,
        answer_relevancy,
    )


def _validate_inputs(
    *,
    job: AdvisoryJob,
    projection: AdvisoryProjection,
    judge: DeepEvalBaseLLM,
    judge_provider: str,
    profile_version: str,
    cache_mode: str,
) -> None:
    if cache_mode not in {"use", "bypass"}:
        raise ValueError("cache_mode must be 'use' or 'bypass'")
    if (
        not isinstance(job, AdvisoryJob)
        or not isinstance(projection, AdvisoryProjection)
        or not isinstance(judge, DeepEvalBaseLLM)
    ):
        raise ValueError("advisory evaluator input is invalid")
    if (
        type(judge_provider) is not str
        or not judge_provider.strip()
        or type(profile_version) is not str
        or not profile_version.strip()
    ):
        raise ValueError("judge identity is invalid")
    try:
        resolved_profile = judge.get_model_name()
    except Exception:
        raise ValueError("judge profile is unavailable") from None
    if resolved_profile != profile_version:
        raise ValueError("judge profile version does not match")
    if (
        job.run_ref != projection.run_ref
        or job.report_version != projection.report_version
        or job.report_sha256 != projection.report_sha256
        or job.projection_sha256 != projection.projection_sha256
    ):
        raise ValueError("job and projection do not match")


def _evaluation_id(job: AdvisoryJob) -> str:
    persisted = job.evaluation_id
    if (
        type(persisted) is str
        and _EVALUATION_ID_PATTERN.fullmatch(persisted)
    ):
        return persisted
    digest = canonical_sha256(
        {
            "job_id": job.job_id,
            "run_ref": job.run_ref,
            "report_version": job.report_version,
            "projection_sha256": job.projection_sha256,
            "advisory_input_sha256": job.advisory_input_sha256,
        }
    )
    return f"eval_{digest[:32]}"


def _request_id(
    *,
    evaluation_id: str,
    metric_id: str,
    unit_ordinal: int,
) -> str:
    digest = canonical_sha256(
        {
            "evaluation_id": evaluation_id,
            "metric_id": metric_id,
            "metric_version": _METRIC_VERSION,
            "unit_ordinal": unit_ordinal,
        }
    )
    return f"req_{digest[:32]}"


def _make_cases(
    projection: AdvisoryProjection,
) -> list[LLMTestCase]:
    return [
        LLMTestCase(
            input=_BUSINESS_TASK,
            actual_output=unit.text,
            expected_output=None,
            retrieval_context=[
                evidence.excerpt
                for evidence in unit.evidence
                if evidence.level in {"A", "B"}
            ],
        )
        for unit in projection.units
    ]


def _has_attachment_expectation(unit: ProjectionUnit) -> bool:
    return bool(
        type(unit.attachment_expectation) is str
        and unit.attachment_expectation.strip()
        and any(
            evidence.level in {"A", "B"}
            for evidence in unit.evidence
        )
    )


def _applicable_unit_ordinals(
    metric_id: str,
    projection: AdvisoryProjection,
) -> tuple[int, ...]:
    if metric_id == METRIC_IDS[0]:
        return tuple(
            ordinal
            for ordinal, unit in enumerate(
                projection.units,
                start=1,
            )
            if any(
                evidence.level in {"A", "B"}
                for evidence in unit.evidence
            )
        )
    if metric_id == METRIC_IDS[3]:
        return tuple(range(1, len(projection.units) + 1))
    if metric_id == METRIC_IDS[1]:
        return tuple(
            ordinal
            for ordinal, unit in enumerate(
                projection.units,
                start=1,
            )
            if unit.expected_facts
        )
    if metric_id == METRIC_IDS[2]:
        return tuple(
            ordinal
            for ordinal, unit in enumerate(
                projection.units,
                start=1,
            )
            if _has_attachment_expectation(unit)
        )
    raise _MetricEvaluationError("metric definition is invalid")


def _expected_output(
    metric_id: str,
    unit: ProjectionUnit,
) -> str | None:
    if metric_id == METRIC_IDS[1]:
        return "\n".join(unit.expected_facts)
    if metric_id == METRIC_IDS[2]:
        return unit.attachment_expectation
    return None


def _normalize_score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(
        value,
        (int, float, Decimal),
    ):
        raise _MetricEvaluationError("metric score is invalid")
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        raise _MetricEvaluationError("metric score is invalid") from None
    if not math.isfinite(score):
        raise _MetricEvaluationError("metric score is invalid")
    if 0 <= score <= 1:
        return score
    if 1 < score <= 10:
        return score / 10
    raise _MetricEvaluationError("metric score is invalid")


def _sanitize_reason(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        return "Judge reason unavailable."
    normalized = " ".join(value.split())
    if not normalized:
        return None
    try:
        assert_safe_outbound_text(normalized)
    except BoundaryViolation:
        return _UNSAFE_REASON
    return normalized[:500]


def _mean_score(outcomes: list[_UnitOutcome]) -> float:
    if not outcomes or any(
        outcome.status is not MetricStatus.SCORED
        or outcome.score is None
        for outcome in outcomes
    ):
        raise _MetricEvaluationError("metric aggregation is incomplete")
    total = sum(
        (Decimal(str(outcome.score)) for outcome in outcomes),
        Decimal("0"),
    )
    mean = float(total / Decimal(len(outcomes)))
    if not math.isfinite(mean) or not 0 <= mean <= 1:
        raise _MetricEvaluationError("metric aggregation is invalid")
    return mean


def _aggregate_reason(outcomes: list[_UnitOutcome]) -> str | None:
    reasons = [
        outcome.reason
        for outcome in outcomes
        if outcome.reason
    ]
    if not reasons:
        return None
    return _sanitize_reason(" ".join(reasons))


def _observation_from_outcomes(
    metric_id: str,
    outcomes: list[_UnitOutcome],
) -> MetricObservation:
    if not outcomes:
        return MetricObservation(
            metric_id=metric_id,
            metric_version=_METRIC_VERSION,
            status=MetricStatus.NOT_APPLICABLE,
        )
    if any(
        outcome.status is MetricStatus.UNAVAILABLE
        for outcome in outcomes
    ):
        return MetricObservation(
            metric_id=metric_id,
            metric_version=_METRIC_VERSION,
            status=MetricStatus.UNAVAILABLE,
        )
    if any(
        outcome.status is MetricStatus.ERROR
        for outcome in outcomes
    ):
        return MetricObservation(
            metric_id=metric_id,
            metric_version=_METRIC_VERSION,
            status=MetricStatus.ERROR,
            error_category="metric_error",
        )
    return MetricObservation(
        metric_id=metric_id,
        metric_version=_METRIC_VERSION,
        status=MetricStatus.SCORED,
        score=_mean_score(outcomes),
        bounded_reason=_aggregate_reason(outcomes),
    )


def _result_status(
    observations: tuple[MetricObservation, ...],
    *,
    over_budget_rejected: bool,
    accepted_response_count: int,
) -> JobStatus:
    statuses = tuple(item.status for item in observations)
    has_score = MetricStatus.SCORED in statuses
    has_failure = any(
        status in {MetricStatus.UNAVAILABLE, MetricStatus.ERROR}
        for status in statuses
    )
    if has_score and has_failure:
        return JobStatus.PARTIAL
    if has_score:
        return JobStatus.COMPLETED
    if over_budget_rejected and accepted_response_count == 0:
        return JobStatus.OVER_BUDGET
    return JobStatus.UNAVAILABLE


async def evaluate_projection(
    *,
    job: AdvisoryJob,
    projection: AdvisoryProjection,
    judge: DeepEvalBaseLLM,
    judge_provider: str,
    profile_version: str,
    cache_mode: Literal["use", "bypass"],
) -> AdvisoryResult:
    _validate_inputs(
        job=job,
        projection=projection,
        judge=judge,
        judge_provider=judge_provider,
        profile_version=profile_version,
        cache_mode=cache_mode,
    )
    evaluation_id = _evaluation_id(job)
    cases = _make_cases(projection)
    metrics = _build_metrics(judge)
    if len(metrics) != len(METRIC_IDS):
        raise _MetricEvaluationError("metric set is invalid")

    ledger = _UsageLedger()
    remaining_subcalls = _MAX_JOB_SUBCALLS
    deadline_monotonic = time.monotonic() + _JOB_DEADLINE_SECONDS
    observations: list[MetricObservation] = []
    over_budget_rejected = False

    for metric_id, metric in zip(METRIC_IDS, metrics, strict=True):
        outcomes: list[_UnitOutcome] = []
        applicable_ordinals = _applicable_unit_ordinals(
            metric_id,
            projection,
        )
        for unit_ordinal in applicable_ordinals:
            unit = projection.units[unit_ordinal - 1]
            test_case = cases[unit_ordinal - 1]
            test_case.expected_output = _expected_output(metric_id, unit)
            now = time.monotonic()
            context = JudgeCallContext(
                request_id=_request_id(
                    evaluation_id=evaluation_id,
                    metric_id=metric_id,
                    unit_ordinal=unit_ordinal,
                ),
                evaluation_id=evaluation_id,
                run_ref=job.run_ref,
                metric_id=metric_id,
                metric_version=_METRIC_VERSION,
                projection_sha256=projection.projection_sha256,
                max_subcalls=remaining_subcalls,
                fresh_until_monotonic=min(
                    now + _CONTEXT_FRESH_SECONDS,
                    deadline_monotonic - 0.001,
                ),
                deadline_monotonic=deadline_monotonic,
            )

            raw_score: object = None
            reason: str | None = None
            judge_failure: JudgeError | None = None
            metric_failure = False
            usage_failure = False
            try:
                with judge_context(context):
                    raw_score = await metric.a_measure(
                        test_case,
                        _show_indicator=False,
                        _log_metric_to_confident=False,
                    )
                reason = _sanitize_reason(
                    getattr(metric, "reason", None)
                )
            except JudgeError as error:
                judge_failure = error
                if error.category in _OVER_BUDGET_CATEGORIES:
                    over_budget_rejected = True
            except Exception:
                metric_failure = True
            finally:
                remaining_subcalls = max(
                    0,
                    remaining_subcalls - context.subcalls_used,
                )
                try:
                    snapshot = context.usage_snapshot()
                    ledger.note_snapshot(
                        context=context,
                        unit_ordinal=unit_ordinal,
                        snapshot=snapshot,
                    )
                except JudgeError as error:
                    ledger.note_snapshot_failure(error)
                    usage_failure = True
                except _MetricEvaluationError:
                    usage_failure = True

            if judge_failure is not None:
                outcomes.append(
                    _UnitOutcome(status=MetricStatus.UNAVAILABLE)
                )
            elif metric_failure or usage_failure:
                outcomes.append(
                    _UnitOutcome(status=MetricStatus.ERROR)
                )
            else:
                try:
                    outcomes.append(
                        _UnitOutcome(
                            status=MetricStatus.SCORED,
                            score=_normalize_score(raw_score),
                            reason=reason,
                        )
                    )
                except _MetricEvaluationError:
                    outcomes.append(
                        _UnitOutcome(status=MetricStatus.ERROR)
                    )

        observations.append(
            _observation_from_outcomes(metric_id, outcomes)
        )

    result_observations = tuple(observations)
    status = _result_status(
        result_observations,
        over_budget_rejected=over_budget_rejected,
        accepted_response_count=ledger.accepted_response_count,
    )
    input_fingerprint, output_fingerprint = ledger.fingerprints()
    try:
        cost = ledger.cost_float()
    except _MetricEvaluationError:
        cost = 0.0
        if status is JobStatus.COMPLETED:
            status = JobStatus.UNAVAILABLE

    return AdvisoryResult(
        evaluation_id=evaluation_id,
        job_id=job.job_id,
        run_ref=job.run_ref,
        advisory_input_sha256=job.advisory_input_sha256,
        metric_set_sha256=metric_set_sha256(),
        status=status,
        metrics=result_observations,
        judge_provider=judge_provider,
        resolved_model_or_profile_version=profile_version,
        token_input=ledger.token_input,
        token_output=ledger.token_output,
        cost=cost,
        latency_ms=ledger.latency_ms,
        retry_count=0,
        input_fingerprint=input_fingerprint,
        output_fingerprint=output_fingerprint,
        created_at=utc_now_iso(),
    )


runtime_evaluate_projection = evaluate_projection
scheduled_evaluate_projection = evaluate_projection
