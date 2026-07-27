from __future__ import annotations

import ast
import asyncio
from collections import deque
import inspect
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from deepeval.metrics import (
    AnswerRelevancyMetric,
    FaithfulnessMetric,
    GEval,
)
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, SingleTurnParams
from pydantic import BaseModel, ConfigDict

from app.deepeval_advisory import evaluator as evaluator_module
from app.deepeval_advisory.cache import AdvisoryCache, CacheError
from app.deepeval_advisory.evaluator import (
    ATTACHMENT_CONSISTENCY_EVALUATION_STEPS,
    ATTACHMENT_CONSISTENCY_RUBRIC,
    CRITICAL_COVERAGE_EVALUATION_STEPS,
    CRITICAL_COVERAGE_RUBRIC,
    DEEPEVAL_VERSION,
    METRIC_IDS,
    METRIC_SET_VERSION,
    PROMPT_VERSION,
    RESPONSE_SCHEMA_VERSION,
    SAFETY_PREAMBLE_VERSION,
    _build_metrics,
    evaluate_projection,
    metric_set_sha256,
)
from app.deepeval_advisory.hashing import (
    canonical_sha256,
    text_sha256,
)
from app.deepeval_advisory.judge import (
    DeepEvalJudgeLLM,
    FakeJudgeTransport,
    JudgeCallContext,
    JudgeError,
    JudgeResponse,
    require_judge_context,
)
from app.deepeval_advisory.models import (
    AdvisoryJob,
    AdvisoryProjection,
    EvidenceExcerpt,
    JobStatus,
    MetricStatus,
    ProjectionUnit,
    SafeLocator,
)


RUN_REF = f"runref_{'1' * 32}"
REPORT_SHA256 = "a" * 64
PROJECTION_SHA256 = "b" * 64
ADVISORY_INPUT_SHA256 = "c" * 64
PROFILE_VERSION = "fake-judge-profile-v1"
CANONICAL_TIME = "2026-07-27T12:34:56.123Z"
BUSINESS_TASK = "请依据提供的采购公告材料，形成准确、完整且相关的分析结论。"
SAFE_EVIDENCE = "公告明确说明本次采购范围和执行要求。"


class ScoreEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: float


class NoCallJudge(DeepEvalBaseLLM):
    def __init__(self, profile_version: str = PROFILE_VERSION) -> None:
        self.profile_version = profile_version
        super().__init__(model=profile_version)

    def load_model(self) -> object:
        return self

    def get_model_name(self) -> str:
        return self.profile_version

    def generate(
        self,
        prompt: str,
        schema: type[BaseModel] | None = None,
    ) -> str | BaseModel:
        raise AssertionError("stub metrics must not call the judge")

    async def a_generate(
        self,
        prompt: str,
        schema: type[BaseModel] | None = None,
    ) -> str | BaseModel:
        raise AssertionError("stub metrics must not call the judge")


class ControlledMetric:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = deque(outcomes)
        self.reason: object = None
        self.calls: list[dict[str, object]] = []

    @property
    def success(self) -> bool:
        raise AssertionError("DeepEval metric.success must never be inspected")

    async def a_measure(
        self,
        test_case: LLMTestCase,
        *,
        _show_indicator: bool = True,
        _log_metric_to_confident: bool = True,
    ) -> object:
        context = require_judge_context()
        self.calls.append(
            {
                "input": test_case.input,
                "actual_output": test_case.actual_output,
                "expected_output": test_case.expected_output,
                "retrieval_context": tuple(
                    test_case.retrieval_context or ()
                ),
                "show_indicator": _show_indicator,
                "log_metric": _log_metric_to_confident,
                "request_id": context.request_id,
                "evaluation_id": context.evaluation_id,
                "run_ref": context.run_ref,
                "metric_id": context.metric_id,
                "metric_version": context.metric_version,
                "projection_sha256": context.projection_sha256,
                "max_subcalls": context.max_subcalls,
                "fresh_until_monotonic": context.fresh_until_monotonic,
                "deadline_monotonic": context.deadline_monotonic,
            }
        )
        if not self._outcomes:
            raise AssertionError("controlled metric has no configured outcome")
        outcome = self._outcomes.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        if (
            isinstance(outcome, tuple)
            and len(outcome) == 2
        ):
            score, reason = outcome
            self.reason = reason
            return score
        self.reason = "受控且安全的评分说明。"
        return outcome


class JudgeCallingMetric(ControlledMetric):
    def __init__(
        self,
        judge: DeepEvalJudgeLLM,
        *,
        score: float = 0.75,
    ) -> None:
        super().__init__([score])
        self._judge = judge

    async def a_measure(
        self,
        test_case: LLMTestCase,
        *,
        _show_indicator: bool = True,
        _log_metric_to_confident: bool = True,
    ) -> object:
        context = require_judge_context()
        self.calls.append(
            {
                "metric_id": context.metric_id,
                "request_id": context.request_id,
                "max_subcalls": context.max_subcalls,
                "show_indicator": _show_indicator,
                "log_metric": _log_metric_to_confident,
            }
        )
        await self._judge.a_generate(
            "Evaluate the bounded advisory unit.",
            schema=ScoreEnvelope,
        )
        self.reason = "受控且安全的评分说明。"
        return self._outcomes.popleft()


class LedgerTransport:
    def __init__(self, profile_version: str = PROFILE_VERSION) -> None:
        self.profile_version = profile_version
        self.contexts: list[JudgeCallContext] = []
        self.responses: list[JudgeResponse] = []

    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, object] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        raise AssertionError("the evaluator must use the async transport")

    async def acomplete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, object] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        index = len(self.responses) + 1
        text = json.dumps(
            {"score": 1.0},
            sort_keys=True,
            separators=(",", ":"),
        )
        response = JudgeResponse(
            text=text,
            resolved_model_version=self.profile_version,
            token_input=index,
            token_output=index + 1,
            cost=float(f"0.{index}"),
            latency_ms=index * 10,
            input_fingerprint=text_sha256(prompt),
            output_fingerprint=text_sha256(text),
        )
        self.contexts.append(context)
        self.responses.append(response)
        return response


def make_evidence(
    *,
    local_id: str = "e1",
    level: str = "A",
    excerpt: str = SAFE_EVIDENCE,
) -> EvidenceExcerpt:
    if level in {"A", "B"}:
        return EvidenceExcerpt(
            local_id=local_id,
            level=level,
            kind="article",
            excerpt=excerpt,
            locator=SafeLocator(kind="article", ordinal=1),
        )
    return EvidenceExcerpt.model_construct(
        local_id=local_id,
        level=level,
        kind="untrusted",
        excerpt=excerpt,
        parent_a_ids=(),
        locator=SafeLocator(kind="article", ordinal=99),
    )


def make_unit(
    ordinal: int = 1,
    *,
    text: str = "报告准确说明了采购范围。",
    evidence: tuple[EvidenceExcerpt, ...] | None = None,
    expected_facts: tuple[str, ...] = (),
    attachment_expectation: str | None = None,
) -> ProjectionUnit:
    return ProjectionUnit.model_construct(
        unit_id=f"unit_{ordinal:016x}",
        kind="claim",
        text=text,
        claim_count=1,
        evidence=(
            (make_evidence(),)
            if evidence is None
            else evidence
        ),
        expected_facts=expected_facts,
        attachment_expectation=attachment_expectation,
    )


def make_projection(
    units: tuple[ProjectionUnit, ...] | None = None,
) -> AdvisoryProjection:
    return AdvisoryProjection.model_construct(
        schema_version="8099.deepeval-projection/v1",
        projection_version="claim-ab-v1",
        run_ref=RUN_REF,
        report_version=1,
        report_sha256=REPORT_SHA256,
        units=units or (make_unit(),),
        projection_sha256=PROJECTION_SHA256,
    )


def make_job(
    *,
    evaluation_id: str | None = "eval_fixed_12345678",
) -> AdvisoryJob:
    return AdvisoryJob(
        job_id="job_evaluator_12345678",
        run_ref=RUN_REF,
        report_version=1,
        report_sha256=REPORT_SHA256,
        projection_sha256=PROJECTION_SHA256,
        advisory_input_sha256=ADVISORY_INPUT_SHA256,
        status=JobStatus.RUNNING,
        evaluation_id=evaluation_id,
        created_at=CANONICAL_TIME,
        updated_at=CANONICAL_TIME,
    )


def safe_real_responses() -> dict[str | None, object]:
    return {
        "Truths": {"truths": [SAFE_EVIDENCE]},
        "Claims": {"claims": ["报告准确说明了采购范围。"]},
        "Verdicts": {"verdicts": [{"verdict": "yes"}]},
        "FaithfulnessScoreReason": {
            "reason": "报告结论由所给材料支持。"
        },
        "Statements": {"statements": ["报告准确说明了采购范围。"]},
        "AnswerRelevancyScoreReason": {
            "reason": "报告内容与业务任务直接相关。"
        },
        "ReasonScore": {
            "reason": "报告内容与预期要求一致。",
            "score": 9.0,
        },
    }


def controlled_metric_set(
    *,
    faithfulness: list[object],
    critical: list[object],
    attachment: list[object],
    relevancy: list[object],
) -> tuple[ControlledMetric, ...]:
    return (
        ControlledMetric(faithfulness),
        ControlledMetric(critical),
        ControlledMetric(attachment),
        ControlledMetric(relevancy),
    )


def observations_by_id(result: object) -> dict[str, object]:
    return {
        observation.metric_id: observation
        for observation in result.metrics
    }


class AdvisoryEvaluatorDefinitionTests(unittest.TestCase):
    def test_fixed_metric_and_contract_versions_and_hash_are_exact(
        self,
    ) -> None:
        self.assertEqual(DEEPEVAL_VERSION, "4.1.3")
        self.assertEqual(METRIC_SET_VERSION, "8099-advisory-metrics-v1")
        self.assertEqual(PROMPT_VERSION, "8099-advisory-prompts-v1")
        self.assertEqual(
            RESPONSE_SCHEMA_VERSION,
            "8099.deepeval-result/v1",
        )
        self.assertEqual(
            SAFETY_PREAMBLE_VERSION,
            "8099-judge-safety-v1",
        )
        self.assertEqual(
            METRIC_IDS,
            (
                "claim_faithfulness_v1",
                "critical_coverage_v1",
                "attachment_state_consistency_v1",
                "answer_relevancy_v1",
            ),
        )
        self.assertEqual(
            metric_set_sha256(),
            canonical_sha256(
                {
                    "metric_set_version": METRIC_SET_VERSION,
                    "metrics": METRIC_IDS,
                    "prompt_version": PROMPT_VERSION,
                    "response_schema_version": RESPONSE_SCHEMA_VERSION,
                    "safety_preamble_version": SAFETY_PREAMBLE_VERSION,
                }
            ),
        )

    def test_metric_constructors_use_exact_safe_parameters(
        self,
    ) -> None:
        judge = NoCallJudge()
        faithfulness = object()
        critical = object()
        attachment = object()
        relevancy = object()

        with (
            patch.object(
                evaluator_module,
                "FaithfulnessMetric",
                return_value=faithfulness,
            ) as faithfulness_constructor,
            patch.object(
                evaluator_module,
                "AnswerRelevancyMetric",
                return_value=relevancy,
            ) as relevancy_constructor,
            patch.object(
                evaluator_module,
                "GEval",
                side_effect=(critical, attachment),
            ) as geval_constructor,
        ):
            metrics = _build_metrics(judge)

        self.assertEqual(
            metrics,
            (faithfulness, critical, attachment, relevancy),
        )
        common = {
            "threshold": 0.0,
            "model": judge,
            "include_reason": True,
            "async_mode": True,
            "strict_mode": False,
            "verbose_mode": False,
        }
        faithfulness_constructor.assert_called_once_with(**common)
        relevancy_constructor.assert_called_once_with(**common)
        self.assertEqual(geval_constructor.call_count, 2)

        critical_kwargs = geval_constructor.call_args_list[0].kwargs
        attachment_kwargs = geval_constructor.call_args_list[1].kwargs
        for metric_id, kwargs, steps, rubric in (
            (
                METRIC_IDS[1],
                critical_kwargs,
                CRITICAL_COVERAGE_EVALUATION_STEPS,
                CRITICAL_COVERAGE_RUBRIC,
            ),
            (
                METRIC_IDS[2],
                attachment_kwargs,
                ATTACHMENT_CONSISTENCY_EVALUATION_STEPS,
                ATTACHMENT_CONSISTENCY_RUBRIC,
            ),
        ):
            with self.subTest(metric_id=metric_id):
                self.assertEqual(kwargs["name"], metric_id)
                self.assertEqual(
                    kwargs["evaluation_params"],
                    [
                        SingleTurnParams.ACTUAL_OUTPUT,
                        SingleTurnParams.EXPECTED_OUTPUT,
                    ],
                )
                self.assertEqual(kwargs["evaluation_steps"], list(steps))
                self.assertEqual(
                    tuple(
                        (
                            item.score_range,
                            item.expected_outcome,
                        )
                        for item in kwargs["rubric"]
                    ),
                    rubric,
                )
                self.assertIs(kwargs["model"], judge)
                self.assertEqual(kwargs["threshold"], 0.0)
                self.assertTrue(kwargs["async_mode"])
                self.assertFalse(kwargs["strict_mode"])
                self.assertFalse(kwargs["verbose_mode"])
                self.assertNotIn("criteria", kwargs)

        self.assertEqual(
            tuple(item[0] for item in CRITICAL_COVERAGE_RUBRIC),
            ((0, 2), (3, 5), (6, 8), (9, 10)),
        )
        self.assertEqual(
            tuple(item[0] for item in ATTACHMENT_CONSISTENCY_RUBRIC),
            ((0, 2), (3, 5), (6, 8), (9, 10)),
        )
        for _score_range, expected_outcome in (
            CRITICAL_COVERAGE_RUBRIC
            + ATTACHMENT_CONSISTENCY_RUBRIC
        ):
            self.assertIsInstance(expected_outcome, str)
            self.assertGreaterEqual(len(expected_outcome), 12)
            self.assertTrue(re.search(r"[\u4e00-\u9fff]", expected_outcome))

    def test_real_metric_types_keep_fixed_steps_and_verbose_disabled(
        self,
    ) -> None:
        judge = NoCallJudge()
        metrics = _build_metrics(judge)

        self.assertIsInstance(metrics[0], FaithfulnessMetric)
        self.assertIsInstance(metrics[1], GEval)
        self.assertIsInstance(metrics[2], GEval)
        self.assertIsInstance(metrics[3], AnswerRelevancyMetric)
        for metric in metrics:
            self.assertFalse(metric.verbose_mode)
            self.assertTrue(metric.async_mode)
            self.assertFalse(metric.strict_mode)
            self.assertEqual(metric.threshold, 0.0)
        self.assertEqual(
            metrics[1].evaluation_steps,
            list(CRITICAL_COVERAGE_EVALUATION_STEPS),
        )
        self.assertEqual(
            metrics[2].evaluation_steps,
            list(ATTACHMENT_CONSISTENCY_EVALUATION_STEPS),
        )

    def test_public_api_is_one_exact_async_path_without_evaluate_or_success(
        self,
    ) -> None:
        signature = inspect.signature(evaluate_projection)
        self.assertTrue(inspect.iscoroutinefunction(evaluate_projection))
        self.assertEqual(
            tuple(signature.parameters),
            (
                "job",
                "projection",
                "judge",
                "judge_provider",
                "profile_version",
                "cache_mode",
            ),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in signature.parameters.values()
            )
        )

        source = Path(evaluator_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        ]
        self.assertFalse(
            any(
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "evaluate"
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "evaluate"
                )
                for node in calls
            )
        )
        self.assertTrue(
            any(
                isinstance(node, ast.Await)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "a_measure"
                for node in ast.walk(tree)
            )
        )
        self.assertFalse(
            any(
                isinstance(node, ast.Attribute)
                and node.attr == "success"
                for node in ast.walk(tree)
            )
        )
        imported_modules = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        self.assertNotIn("AdvisoryCache", imported_modules)
        self.assertNotIn("AdvisoryStore", imported_modules)


class AdvisoryEvaluatorBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        asyncio.get_running_loop().slow_callback_duration = 10.0

    async def test_real_deepeval_metrics_integrate_with_fake_transport(
        self,
    ) -> None:
        projection = make_projection(
            (
                make_unit(
                    expected_facts=("采购范围必须被明确说明。",),
                    attachment_expectation="附件存在且内容可解析。",
                ),
            )
        )
        transport = FakeJudgeTransport(
            profile_version=PROFILE_VERSION,
            responses=safe_real_responses(),
        )
        judge = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )

        result = await evaluate_projection(
            job=make_job(),
            projection=projection,
            judge=judge,
            judge_provider="fake",
            profile_version=PROFILE_VERSION,
            cache_mode="use",
        )

        self.assertEqual(result.status, JobStatus.COMPLETED)
        self.assertEqual(
            tuple(item.metric_id for item in result.metrics),
            METRIC_IDS,
        )
        self.assertTrue(
            all(
                item.status is MetricStatus.SCORED
                for item in result.metrics
            )
        )
        self.assertEqual(
            tuple(item.score for item in result.metrics),
            (1.0, 0.9, 0.9, 1.0),
        )
        self.assertEqual(transport.sync_call_count, 0)
        self.assertEqual(transport.async_call_count, 9)
        self.assertGreater(result.token_input, 0)
        self.assertGreater(result.token_output, 0)
        self.assertRegex(result.input_fingerprint, r"^[0-9a-f]{64}$")
        self.assertRegex(result.output_fingerprint, r"^[0-9a-f]{64}$")

    async def test_one_case_per_unit_contains_only_approved_business_fields(
        self,
    ) -> None:
        evidence_a = make_evidence(
            local_id="e1",
            level="A",
            excerpt="甲级材料摘录。",
        )
        evidence_b = make_evidence(
            local_id="e2",
            level="B",
            excerpt="乙级材料摘录。",
        )
        evidence_c = make_evidence(
            local_id="e3",
            level="C",
            excerpt="C级材料不得外发。",
        )
        units = (
            make_unit(
                1,
                text="第一项报告结论。",
                evidence=(evidence_a, evidence_b, evidence_c),
                expected_facts=("必须事实甲。", "必须事实乙。"),
                attachment_expectation="附件存在且可解析。",
            ),
            make_unit(
                2,
                text="第二项报告结论。",
                evidence=(evidence_b,),
                expected_facts=("必须事实丙。",),
                attachment_expectation="附件不存在。",
            ),
        )
        projection = make_projection(units)
        metrics = controlled_metric_set(
            faithfulness=[0.8, 0.8],
            critical=[0.8, 0.8],
            attachment=[0.8, 0.8],
            relevancy=[0.8, 0.8],
        )
        constructor_kwargs: list[dict[str, object]] = []
        real_case = LLMTestCase

        def capture_case(**kwargs: object) -> LLMTestCase:
            constructor_kwargs.append(dict(kwargs))
            return real_case(**kwargs)

        with (
            patch.object(
                evaluator_module,
                "_build_metrics",
                return_value=metrics,
            ),
            patch.object(
                evaluator_module,
                "LLMTestCase",
                side_effect=capture_case,
            ),
        ):
            result = await evaluate_projection(
                job=make_job(),
                projection=projection,
                judge=NoCallJudge(),
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="use",
            )

        self.assertEqual(result.status, JobStatus.COMPLETED)
        self.assertEqual(len(constructor_kwargs), len(units))
        for ordinal, kwargs in enumerate(constructor_kwargs):
            self.assertEqual(kwargs["input"], BUSINESS_TASK)
            self.assertEqual(kwargs["actual_output"], units[ordinal].text)
            self.assertIsNone(kwargs["expected_output"])
            self.assertEqual(
                kwargs["retrieval_context"],
                [
                    item.excerpt
                    for item in units[ordinal].evidence
                    if item.level in {"A", "B"}
                ],
            )
            self.assertNotIn("context", kwargs)
            self.assertNotIn("metadata", kwargs)
            serialized = repr(kwargs)
            for forbidden in (
                "C级材料不得外发",
                "unit_",
                "runref_",
                "e1",
                "e2",
                "e3",
                "ordinal",
                "locator",
            ):
                self.assertNotIn(forbidden, serialized)

        faithfulness, critical, attachment, relevancy = metrics
        self.assertEqual(
            tuple(item["expected_output"] for item in faithfulness.calls),
            (None, None),
        )
        self.assertEqual(
            tuple(item["expected_output"] for item in critical.calls),
            ("必须事实甲。\n必须事实乙。", "必须事实丙。"),
        )
        self.assertEqual(
            tuple(item["expected_output"] for item in attachment.calls),
            ("附件存在且可解析。", "附件不存在。"),
        )
        self.assertEqual(
            tuple(item["expected_output"] for item in relevancy.calls),
            (None, None),
        )
        for metric in metrics:
            for metric_call in metric.calls:
                self.assertFalse(metric_call["show_indicator"])
                self.assertFalse(metric_call["log_metric"])
                self.assertEqual(metric_call["run_ref"], RUN_REF)
                self.assertEqual(
                    metric_call["projection_sha256"],
                    PROJECTION_SHA256,
                )
                self.assertEqual(metric_call["metric_version"], "1")
                self.assertGreater(
                    metric_call["deadline_monotonic"],
                    metric_call["fresh_until_monotonic"],
                )

    async def test_not_applicable_logic_is_exact(self) -> None:
        units = (
            make_unit(
                1,
                evidence=(make_evidence(level="A"),),
                expected_facts=(),
                attachment_expectation=None,
            ),
            make_unit(
                2,
                evidence=(make_evidence(level="C"),),
                expected_facts=(),
                attachment_expectation="不可信来源中的附件预期。",
            ),
        )
        metrics = controlled_metric_set(
            faithfulness=[0.8, 0.6],
            critical=[],
            attachment=[],
            relevancy=[0.7, 0.5],
        )
        with patch.object(
            evaluator_module,
            "_build_metrics",
            return_value=metrics,
        ):
            result = await evaluate_projection(
                job=make_job(),
                projection=make_projection(units),
                judge=NoCallJudge(),
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="use",
            )

        by_id = observations_by_id(result)
        self.assertEqual(result.status, JobStatus.COMPLETED)
        self.assertEqual(
            by_id[METRIC_IDS[0]].status,
            MetricStatus.SCORED,
        )
        self.assertEqual(
            by_id[METRIC_IDS[1]].status,
            MetricStatus.NOT_APPLICABLE,
        )
        self.assertEqual(
            by_id[METRIC_IDS[2]].status,
            MetricStatus.NOT_APPLICABLE,
        )
        self.assertEqual(
            by_id[METRIC_IDS[3]].status,
            MetricStatus.SCORED,
        )
        self.assertEqual(len(metrics[1].calls), 0)
        self.assertEqual(len(metrics[2].calls), 0)

    async def test_scores_are_independent_normalized_and_mean_only_when_complete(
        self,
    ) -> None:
        units = (
            make_unit(
                1,
                expected_facts=("事实甲。",),
                attachment_expectation="附件存在。",
            ),
            make_unit(
                2,
                expected_facts=("事实乙。",),
                attachment_expectation="附件缺失。",
            ),
        )
        metrics = controlled_metric_set(
            faithfulness=[0.2, 0.8],
            critical=[7.0, 9.0],
            attachment=[1.0, 10.0],
            relevancy=[0.4, 0.6],
        )
        with patch.object(
            evaluator_module,
            "_build_metrics",
            return_value=metrics,
        ):
            result = await evaluate_projection(
                job=make_job(),
                projection=make_projection(units),
                judge=NoCallJudge(),
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="use",
            )

        self.assertEqual(result.status, JobStatus.COMPLETED)
        self.assertEqual(
            tuple(item.score for item in result.metrics),
            (0.5, 0.8, 1.0, 0.5),
        )
        dumped = result.model_dump(mode="json")
        for forbidden in (
            "aggregate_score",
            "overall_score",
            "threshold",
            "passed",
            "pass",
            "failed",
            "release_decision",
            "release",
            "decision",
        ):
            self.assertNotIn(forbidden, dumped)
        self.assertFalse(result.blocking)
        self.assertFalse(result.affects_deliverable)
        self.assertFalse(result.affects_report_status)
        self.assertFalse(result.affects_release)
        self.assertFalse(result.affects_user_response)

    async def test_reason_is_bounded_and_unsafe_content_is_replaced(
        self,
    ) -> None:
        unsafe = (
            "authorization: Bearer private-token "
            "C:\\private\\report.txt "
            + ("x" * 800)
        )
        metrics = controlled_metric_set(
            faithfulness=[(0.8, unsafe)],
            critical=[],
            attachment=[],
            relevancy=[(0.9, "安" * 800)],
        )
        unit = make_unit(
            evidence=(make_evidence(),),
            expected_facts=(),
            attachment_expectation=None,
        )
        with patch.object(
            evaluator_module,
            "_build_metrics",
            return_value=metrics,
        ):
            result = await evaluate_projection(
                job=make_job(),
                projection=make_projection((unit,)),
                judge=NoCallJudge(),
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="use",
            )

        by_id = observations_by_id(result)
        unsafe_reason = by_id[METRIC_IDS[0]].bounded_reason
        long_reason = by_id[METRIC_IDS[3]].bounded_reason
        self.assertEqual(
            unsafe_reason,
            "Judge reason omitted because it violated the safety boundary.",
        )
        self.assertNotIn("private-token", unsafe_reason)
        self.assertNotIn("report.txt", unsafe_reason)
        self.assertEqual(len(long_reason), 500)

    async def test_judge_and_metric_errors_are_explicit_without_zero_scores(
        self,
    ) -> None:
        unit = make_unit(
            expected_facts=("事实甲。",),
            attachment_expectation=None,
        )
        metrics = controlled_metric_set(
            faithfulness=[JudgeError("provider_unavailable")],
            critical=[11.0],
            attachment=[],
            relevancy=[0.6],
        )
        with patch.object(
            evaluator_module,
            "_build_metrics",
            return_value=metrics,
        ):
            result = await evaluate_projection(
                job=make_job(),
                projection=make_projection((unit,)),
                judge=NoCallJudge(),
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="use",
            )

        by_id = observations_by_id(result)
        judge_failure = by_id[METRIC_IDS[0]]
        metric_failure = by_id[METRIC_IDS[1]]
        self.assertEqual(result.status, JobStatus.PARTIAL)
        self.assertEqual(
            judge_failure.status,
            MetricStatus.UNAVAILABLE,
        )
        self.assertIsNone(judge_failure.score)
        self.assertIsNone(judge_failure.error_category)
        self.assertEqual(metric_failure.status, MetricStatus.ERROR)
        self.assertIsNone(metric_failure.score)
        self.assertEqual(metric_failure.error_category, "metric_error")
        self.assertEqual(by_id[METRIC_IDS[3]].score, 0.6)

    async def test_any_judge_error_makes_the_whole_metric_unavailable(
        self,
    ) -> None:
        units = (
            make_unit(1, expected_facts=("事实甲。",)),
            make_unit(2, expected_facts=("事实乙。",)),
        )
        metrics = controlled_metric_set(
            faithfulness=[0.8, 0.8],
            critical=[
                JudgeError("provider_unavailable"),
                RuntimeError("private metric failure"),
            ],
            attachment=[],
            relevancy=[0.7, 0.7],
        )
        with patch.object(
            evaluator_module,
            "_build_metrics",
            return_value=metrics,
        ):
            result = await evaluate_projection(
                job=make_job(),
                projection=make_projection(units),
                judge=NoCallJudge(),
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="use",
            )

        critical = observations_by_id(result)[METRIC_IDS[1]]
        self.assertEqual(critical.status, MetricStatus.UNAVAILABLE)
        self.assertIsNone(critical.score)
        self.assertIsNone(critical.error_category)

    async def test_status_matrix_and_completed_only_cache_validation(
        self,
    ) -> None:
        unit = make_unit(
            expected_facts=("事实甲。",),
            attachment_expectation="附件存在。",
        )

        async def run(
            outcomes: tuple[list[object], ...],
            *,
            max_subcalls: int | None = None,
        ) -> object:
            metrics = controlled_metric_set(
                faithfulness=outcomes[0],
                critical=outcomes[1],
                attachment=outcomes[2],
                relevancy=outcomes[3],
            )
            context = (
                patch.object(
                    evaluator_module,
                    "_MAX_JOB_SUBCALLS",
                    max_subcalls,
                )
                if max_subcalls is not None
                else patch.object(
                    evaluator_module,
                    "_MAX_JOB_SUBCALLS",
                    evaluator_module._MAX_JOB_SUBCALLS,
                )
            )
            with (
                patch.object(
                    evaluator_module,
                    "_build_metrics",
                    return_value=metrics,
                ),
                context,
            ):
                return await evaluate_projection(
                    job=make_job(),
                    projection=make_projection((unit,)),
                    judge=NoCallJudge(),
                    judge_provider="fake",
                    profile_version=PROFILE_VERSION,
                    cache_mode="use",
                )

        completed = await run(([0.8], [0.7], [0.9], [0.6]))
        partial = await run(
            (
                [0.8],
                [JudgeError("provider_unavailable")],
                [0.9],
                [0.6],
            )
        )
        unavailable = await run(
            (
                [JudgeError("provider_unavailable")],
                [RuntimeError("private metric failure")],
                [JudgeError("provider_unavailable")],
                [JudgeError("provider_unavailable")],
            )
        )
        over_budget = await run(
            (
                [JudgeError("request_too_large")],
                [JudgeError("request_too_large")],
                [JudgeError("request_too_large")],
                [JudgeError("request_too_large")],
            )
        )

        self.assertEqual(completed.status, JobStatus.COMPLETED)
        self.assertEqual(partial.status, JobStatus.PARTIAL)
        self.assertEqual(unavailable.status, JobStatus.UNAVAILABLE)
        self.assertEqual(over_budget.status, JobStatus.OVER_BUDGET)

        with tempfile.TemporaryDirectory() as directory:
            cache = AdvisoryCache(Path(directory))
            self.assertEqual(
                cache.write_completed(
                    completed.advisory_input_sha256,
                    completed,
                ),
                completed,
            )
            for result in (partial, unavailable, over_budget):
                with self.subTest(status=result.status):
                    with self.assertRaises(CacheError):
                        cache.write_completed(
                            result.advisory_input_sha256,
                            result,
                        )

    async def test_over_budget_is_never_used_after_partial_scoring(
        self,
    ) -> None:
        unit = make_unit(
            expected_facts=("事实甲。",),
            attachment_expectation="附件存在。",
        )
        metrics = controlled_metric_set(
            faithfulness=[0.8],
            critical=[JudgeError("subcall_budget_exceeded")],
            attachment=[JudgeError("subcall_budget_exceeded")],
            relevancy=[JudgeError("subcall_budget_exceeded")],
        )
        with patch.object(
            evaluator_module,
            "_build_metrics",
            return_value=metrics,
        ):
            result = await evaluate_projection(
                job=make_job(),
                projection=make_projection((unit,)),
                judge=NoCallJudge(),
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="use",
            )

        self.assertEqual(result.status, JobStatus.PARTIAL)

    async def test_total_budget_carries_across_metric_contexts(
        self,
    ) -> None:
        transport = LedgerTransport()
        judge = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        metrics = tuple(JudgeCallingMetric(judge) for _ in METRIC_IDS)
        unit = make_unit(
            expected_facts=("事实甲。",),
            attachment_expectation="附件存在。",
        )
        with (
            patch.object(
                evaluator_module,
                "_build_metrics",
                return_value=metrics,
            ),
            patch.object(evaluator_module, "_MAX_JOB_SUBCALLS", 2),
        ):
            result = await evaluate_projection(
                job=make_job(),
                projection=make_projection((unit,)),
                judge=judge,
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="use",
            )

        self.assertEqual(len(transport.responses), 2)
        self.assertEqual(
            tuple(metric.calls[0]["max_subcalls"] for metric in metrics),
            (2, 1, 0, 0),
        )
        self.assertEqual(result.status, JobStatus.PARTIAL)
        self.assertEqual(
            tuple(item.status for item in result.metrics),
            (
                MetricStatus.SCORED,
                MetricStatus.SCORED,
                MetricStatus.UNAVAILABLE,
                MetricStatus.UNAVAILABLE,
            ),
        )

    async def test_ledger_aggregates_exact_usage_and_paired_fingerprints(
        self,
    ) -> None:
        transport = LedgerTransport()
        judge = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        metrics = tuple(JudgeCallingMetric(judge) for _ in METRIC_IDS)
        unit = make_unit(
            expected_facts=("事实甲。",),
            attachment_expectation="附件存在。",
        )
        with patch.object(
            evaluator_module,
            "_build_metrics",
            return_value=metrics,
        ):
            result = await evaluate_projection(
                job=make_job(),
                projection=make_projection((unit,)),
                judge=judge,
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="use",
            )

        self.assertEqual(result.status, JobStatus.COMPLETED)
        self.assertEqual(result.token_input, 10)
        self.assertEqual(result.token_output, 14)
        self.assertEqual(result.cost, 1.0)
        self.assertEqual(result.latency_ms, 100)
        self.assertEqual(result.retry_count, 0)

        paired_records = tuple(
            {
                "ordinal": ordinal,
                "request_id": context.request_id,
                "metric_id": context.metric_id,
                "metric_version": context.metric_version,
                "unit_ordinal": 1,
                "sequence": 1,
                "input_fingerprint": response.input_fingerprint,
                "output_fingerprint": response.output_fingerprint,
            }
            for ordinal, (context, response) in enumerate(
                zip(transport.contexts, transport.responses, strict=True),
                start=1,
            )
        )
        self.assertEqual(
            result.input_fingerprint,
            canonical_sha256(
                {"kind": "input", "records": paired_records}
            ),
        )
        self.assertEqual(
            result.output_fingerprint,
            canonical_sha256(
                {"kind": "output", "records": paired_records}
            ),
        )
        serialized = repr(paired_records)
        self.assertNotIn(BUSINESS_TASK, serialized)
        self.assertNotIn(SAFE_EVIDENCE, serialized)
        self.assertNotIn("Evaluate the bounded advisory unit.", serialized)
        self.assertNotIn('{"score":1.0}', serialized)

    async def test_cache_modes_are_identical_and_perform_no_cache_io(
        self,
    ) -> None:
        projection = make_projection()

        def new_metrics(_judge: DeepEvalBaseLLM) -> tuple[ControlledMetric, ...]:
            return controlled_metric_set(
                faithfulness=[0.8],
                critical=[],
                attachment=[],
                relevancy=[0.6],
            )

        with (
            patch.object(
                evaluator_module,
                "_build_metrics",
                side_effect=new_metrics,
            ),
            patch.object(
                evaluator_module,
                "utc_now_iso",
                return_value=CANONICAL_TIME,
            ),
            patch(
                "builtins.open",
                side_effect=AssertionError("cache I/O is forbidden"),
            ) as open_call,
        ):
            use = await evaluate_projection(
                job=make_job(),
                projection=projection,
                judge=NoCallJudge(),
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="use",
            )
            bypass = await evaluate_projection(
                job=make_job(),
                projection=projection,
                judge=NoCallJudge(),
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="bypass",
            )

        self.assertEqual(use, bypass)
        open_call.assert_not_called()
        with self.assertRaises(ValueError):
            await evaluate_projection(
                job=make_job(),
                projection=projection,
                judge=NoCallJudge(),
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="default",
            )

    async def test_evaluation_and_request_ids_are_safe_and_deterministic(
        self,
    ) -> None:
        metrics_first = controlled_metric_set(
            faithfulness=[0.8],
            critical=[],
            attachment=[],
            relevancy=[0.6],
        )
        metrics_second = controlled_metric_set(
            faithfulness=[0.8],
            critical=[],
            attachment=[],
            relevancy=[0.6],
        )
        projection = make_projection()
        invalid_persisted_id_job = make_job(evaluation_id="unsafe id/path")

        with patch.object(
            evaluator_module,
            "_build_metrics",
            side_effect=(metrics_first, metrics_second),
        ):
            first = await evaluate_projection(
                job=invalid_persisted_id_job,
                projection=projection,
                judge=NoCallJudge(),
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="use",
            )
            second = await evaluate_projection(
                job=invalid_persisted_id_job,
                projection=projection,
                judge=NoCallJudge(),
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="bypass",
            )

        self.assertEqual(first.evaluation_id, second.evaluation_id)
        self.assertRegex(first.evaluation_id, r"^eval_[A-Za-z0-9_-]{8,80}$")
        first_request_ids = tuple(
            metric_call["request_id"]
            for metric in metrics_first
            for metric_call in metric.calls
        )
        second_request_ids = tuple(
            metric_call["request_id"]
            for metric in metrics_second
            for metric_call in metric.calls
        )
        self.assertEqual(first_request_ids, second_request_ids)
        self.assertEqual(len(set(first_request_ids)), 2)
        self.assertTrue(
            all(
                re.fullmatch(r"req_[A-Za-z0-9_-]{8,80}", request_id)
                for request_id in first_request_ids
            )
        )

    async def test_profile_and_projection_mismatches_fail_before_metrics(
        self,
    ) -> None:
        projection = make_projection()
        with self.assertRaises(ValueError):
            await evaluate_projection(
                job=make_job(),
                projection=projection,
                judge=NoCallJudge("different-profile"),
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="use",
            )

        mismatched_projection = AdvisoryProjection.model_construct(
            **{
                **projection.model_dump(),
                "projection_sha256": "d" * 64,
            }
        )
        with self.assertRaises(ValueError):
            await evaluate_projection(
                job=make_job(),
                projection=mismatched_projection,
                judge=NoCallJudge(),
                judge_provider="fake",
                profile_version=PROFILE_VERSION,
                cache_mode="use",
            )


if __name__ == "__main__":
    unittest.main()
