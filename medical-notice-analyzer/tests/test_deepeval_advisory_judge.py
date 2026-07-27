from __future__ import annotations

import ast
import asyncio
from dataclasses import FrozenInstanceError, fields
import hashlib
import inspect
import io
import logging
import math
from pathlib import Path
import time
import unittest

from pydantic import BaseModel, ConfigDict, ValidationError

from app.deepeval_advisory.judge import (
    DeepEvalJudgeLLM,
    FakeJudgeTransport,
    JudgeCallContext,
    JudgeError,
    JudgeResponse,
    JudgeTransport,
    judge_context,
    parse_judge_response,
    require_judge_context,
    reset_judge_context,
    set_judge_context,
    validate_judge_prompt,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JUDGE_PATH = PROJECT_ROOT / "app" / "deepeval_advisory" / "judge.py"
PROFILE_VERSION = "judge-profile-2026-07-27"
SAFE_SCORE = {"score": 0.75, "reason": "supported"}


class ScoreEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float
    reason: str


class AlternateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: str


def make_context(
    *,
    request_id: str = "req_12345678",
    evaluation_id: str = "eval_12345678",
    run_ref: str = f"runref_{'a' * 32}",
    metric_id: str = "faithfulness",
    metric_version: str = "v1",
    projection_sha256: str = "b" * 64,
    max_subcalls: int = 8,
    fresh_until_monotonic: float | None = None,
    deadline_monotonic: float | None = None,
) -> JudgeCallContext:
    now = time.monotonic()
    return JudgeCallContext(
        request_id=request_id,
        evaluation_id=evaluation_id,
        run_ref=run_ref,
        metric_id=metric_id,
        metric_version=metric_version,
        projection_sha256=projection_sha256,
        max_subcalls=max_subcalls,
        fresh_until_monotonic=(
            now + 30
            if fresh_until_monotonic is None
            else fresh_until_monotonic
        ),
        deadline_monotonic=(
            now + 60
            if deadline_monotonic is None
            else deadline_monotonic
        ),
    )


def make_fake(
    *,
    profile_version: str = PROFILE_VERSION,
    responses: dict[str | None, object] | None = None,
    failures: dict[str | None, list[BaseException]] | None = None,
) -> FakeJudgeTransport:
    return FakeJudgeTransport(
        profile_version=profile_version,
        responses=(
            {
                "ScoreEnvelope": SAFE_SCORE,
                "AlternateEnvelope": {"verdict": "bounded"},
                None: "plain deterministic result",
            }
            if responses is None
            else responses
        ),
        failures={} if failures is None else failures,
    )


class JudgeModelContractTests(unittest.TestCase):
    def test_judge_error_has_exact_bounded_contract(self) -> None:
        signature = inspect.signature(JudgeError.__init__)
        parameters = list(signature.parameters.values())

        self.assertTrue(issubclass(JudgeError, RuntimeError))
        self.assertEqual(
            [parameter.name for parameter in parameters],
            ["self", "category", "retryable", "outcome"],
        )
        self.assertEqual(
            parameters[2].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertEqual(
            parameters[3].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIs(parameters[2].default, False)
        self.assertEqual(parameters[3].default, "not_started")

        error = JudgeError(
            "http_503",
            retryable=True,
            outcome="not_started",
        )
        self.assertEqual(error.category, "http_503")
        self.assertIs(error.retryable, True)
        self.assertEqual(error.outcome, "not_started")
        self.assertEqual(str(error), "http_503")
        self.assertNotIn("provider", str(error))
        self.assertNotIn("prompt", str(error))

    def test_judge_response_is_strict_frozen_and_forbids_extra_fields(
        self,
    ) -> None:
        response = JudgeResponse(
            text="{}",
            resolved_model_version=PROFILE_VERSION,
            token_input=1,
            token_output=2,
            cost=0.0,
            latency_ms=3,
            input_fingerprint="1" * 64,
            output_fingerprint="2" * 64,
        )

        with self.assertRaises(ValidationError):
            JudgeResponse.model_validate(
                {
                    **response.model_dump(),
                    "provider_body": "must-not-be-retained",
                }
            )
        with self.assertRaises(ValidationError):
            response.text = "changed"

        strict_cases = (
            ("text", 7),
            ("resolved_model_version", 7),
            ("token_input", 1.0),
            ("token_output", "2"),
            ("cost", 1),
            ("latency_ms", True),
        )
        for field_name, value in strict_cases:
            with self.subTest(field=field_name):
                payload = response.model_dump()
                payload[field_name] = value
                with self.assertRaises(ValidationError):
                    JudgeResponse.model_validate(payload)

    def test_judge_response_rejects_negative_nonfinite_and_bad_fingerprints(
        self,
    ) -> None:
        baseline = {
            "text": "{}",
            "resolved_model_version": PROFILE_VERSION,
            "token_input": 0,
            "token_output": 0,
            "cost": 0.0,
            "latency_ms": 0,
            "input_fingerprint": "1" * 64,
            "output_fingerprint": "2" * 64,
        }
        invalid_cases = (
            ("token_input", -1),
            ("token_output", -1),
            ("cost", -0.1),
            ("latency_ms", -1),
            ("cost", math.nan),
            ("cost", math.inf),
            ("cost", -math.inf),
            ("input_fingerprint", "not-a-sha"),
            ("output_fingerprint", "A" * 64),
        )
        for field_name, value in invalid_cases:
            with self.subTest(field=field_name, value=value):
                payload = {**baseline, field_name: value}
                with self.assertRaises(ValidationError):
                    JudgeResponse.model_validate(payload)

    def test_transport_protocol_has_only_exact_keyword_arguments(self) -> None:
        for method_name in ("complete", "acomplete"):
            method = getattr(JudgeTransport, method_name)
            signature = inspect.signature(method)
            parameters = list(signature.parameters.values())

            self.assertEqual(
                [parameter.name for parameter in parameters],
                ["self", "prompt", "response_schema", "context"],
            )
            for parameter in parameters[1:]:
                self.assertEqual(
                    parameter.kind,
                    inspect.Parameter.KEYWORD_ONLY,
                )

        self.assertTrue(inspect.iscoroutinefunction(JudgeTransport.acomplete))


class JudgeContextContractTests(unittest.TestCase):
    def test_context_is_frozen_and_has_only_safe_fields(self) -> None:
        context = make_context()
        field_names = {field.name for field in fields(context)}

        self.assertEqual(
            field_names,
            {
                "request_id",
                "evaluation_id",
                "run_ref",
                "metric_id",
                "metric_version",
                "projection_sha256",
                "max_subcalls",
                "fresh_until_monotonic",
                "deadline_monotonic",
                "_budget",
            },
        )
        self.assertTrue(
            field_names.isdisjoint(
                {
                    "run_id",
                    "pack_id",
                    "report",
                    "evidence",
                    "prompt",
                    "secret",
                    "lease_token",
                }
            )
        )
        with self.assertRaises(FrozenInstanceError):
            context.request_id = "req_other123"
        self.assertNotIn("_budget", repr(context))

    def test_set_reset_and_nested_contexts_restore_the_parent(self) -> None:
        outer = make_context(request_id="req_outer123")
        inner = make_context(request_id="req_inner123")

        token = set_judge_context(outer)
        try:
            self.assertIs(require_judge_context(), outer)
            with judge_context(inner):
                self.assertIs(require_judge_context(), inner)
            self.assertIs(require_judge_context(), outer)
        finally:
            reset_judge_context(token)

        with self.assertRaises(JudgeError) as raised:
            require_judge_context()
        self.assertEqual(raised.exception.category, "context_absent")

    def test_context_rejects_absent_stale_and_expired_state(self) -> None:
        with self.assertRaises(JudgeError) as absent:
            require_judge_context()
        self.assertEqual(absent.exception.category, "context_absent")

        now = time.monotonic()
        stale = make_context(
            fresh_until_monotonic=now - 1,
            deadline_monotonic=now + 30,
        )
        stale_token = set_judge_context(stale)
        try:
            with self.assertRaises(JudgeError) as stale_error:
                require_judge_context()
        finally:
            reset_judge_context(stale_token)
        self.assertEqual(stale_error.exception.category, "context_stale")

        expired = make_context(
            fresh_until_monotonic=now + 30,
            deadline_monotonic=now - 1,
        )
        expired_token = set_judge_context(expired)
        try:
            with self.assertRaises(JudgeError) as expired_error:
                require_judge_context()
        finally:
            reset_judge_context(expired_token)
        self.assertEqual(expired_error.exception.category, "context_expired")

    def test_context_rejects_raw_or_malformed_identity(self) -> None:
        invalid_overrides = (
            {"run_ref": "run_12345678"},
            {"request_id": "request contains spaces"},
            {"evaluation_id": "eval-short"},
            {"projection_sha256": "not-a-sha"},
            {"metric_id": "metric/unsafe"},
            {"metric_version": "version unsafe"},
        )
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(JudgeError) as raised:
                    make_context(**overrides)
                self.assertEqual(raised.exception.category, "context_invalid")


class JudgeAsyncContextContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_tasks_have_isolated_context_and_budget(
        self,
    ) -> None:
        ready = asyncio.Event()
        waiting = 0
        waiting_lock = asyncio.Lock()

        async def worker(context: JudgeCallContext) -> tuple[str, int]:
            nonlocal waiting
            token = set_judge_context(context)
            try:
                async with waiting_lock:
                    waiting += 1
                    if waiting == 2:
                        ready.set()
                await ready.wait()
                await asyncio.sleep(0)
                active = require_judge_context()
                validate_judge_prompt("bounded task prompt", active)
                await asyncio.sleep(0)
                return active.request_id, active.subcalls_used
            finally:
                reset_judge_context(token)

        first = make_context(
            request_id="req_task_one1",
            max_subcalls=1,
        )
        second = make_context(
            request_id="req_task_two2",
            max_subcalls=1,
        )
        results = await asyncio.gather(worker(first), worker(second))

        self.assertEqual(
            set(results),
            {("req_task_one1", 1), ("req_task_two2", 1)},
        )
        with self.assertRaises(JudgeError) as raised:
            require_judge_context()
        self.assertEqual(raised.exception.category, "context_absent")


class JudgePromptGuardTests(unittest.TestCase):
    def test_utf8_size_limit_allows_exactly_64_kib_and_rejects_one_more(
        self,
    ) -> None:
        exact = "a" * 65536
        too_large = exact + "a"
        exact_context = make_context(max_subcalls=1)
        large_context = make_context(max_subcalls=1)

        with judge_context(exact_context):
            self.assertEqual(
                validate_judge_prompt(exact, exact_context),
                exact,
            )
        self.assertEqual(exact_context.subcalls_used, 1)

        with judge_context(large_context):
            with self.assertRaises(JudgeError) as raised:
                validate_judge_prompt(too_large, large_context)
        self.assertEqual(raised.exception.category, "prompt_too_large")
        self.assertEqual(large_context.subcalls_used, 0)

    def test_utf8_size_limit_counts_multibyte_bytes(self) -> None:
        exact = ("界" * 21845) + "a"
        too_large = exact + "界"
        self.assertEqual(len(exact.encode("utf-8")), 65536)
        self.assertEqual(len(too_large.encode("utf-8")), 65539)

        exact_context = make_context(max_subcalls=1)
        with judge_context(exact_context):
            self.assertEqual(
                validate_judge_prompt(exact, exact_context),
                exact,
            )

        large_context = make_context(max_subcalls=1)
        with judge_context(large_context):
            with self.assertRaises(JudgeError) as raised:
                validate_judge_prompt(too_large, large_context)
        self.assertEqual(raised.exception.category, "prompt_too_large")
        self.assertEqual(large_context.subcalls_used, 0)

    def test_prompt_requires_string_and_active_matching_context(self) -> None:
        context = make_context()
        with judge_context(context):
            with self.assertRaises(JudgeError) as not_text:
                validate_judge_prompt(b"bytes are not accepted", context)
            self.assertEqual(not_text.exception.category, "prompt_invalid")

            other = make_context(request_id="req_other123")
            with self.assertRaises(JudgeError) as mismatch:
                validate_judge_prompt("bounded prompt", other)
            self.assertEqual(mismatch.exception.category, "context_mismatch")

        with self.assertRaises(JudgeError) as absent:
            validate_judge_prompt("bounded prompt", context)
        self.assertEqual(absent.exception.category, "context_absent")

    def test_forbidden_secret_and_path_stop_before_transport(self) -> None:
        forbidden_prompts = (
            "api_key=forbidden-value",
            r"read C:\private\report.txt before scoring",
        )
        for prompt in forbidden_prompts:
            with self.subTest(prompt_kind=prompt[:4]):
                transport = make_fake()
                adapter = DeepEvalJudgeLLM(
                    transport,
                    profile_version=PROFILE_VERSION,
                )
                context = make_context()
                with judge_context(context):
                    with self.assertRaises(JudgeError) as raised:
                        adapter.generate(prompt, schema=ScoreEnvelope)

                self.assertEqual(
                    raised.exception.category,
                    "prompt_boundary_violation",
                )
                self.assertEqual(transport.sync_call_count, 0)
                self.assertEqual(context.subcalls_used, 0)

    def test_budget_is_consumed_once_per_provider_attempt(self) -> None:
        transport = make_fake()
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context(max_subcalls=1)

        with judge_context(context):
            result = adapter.generate("bounded prompt", schema=ScoreEnvelope)
            self.assertEqual(result.score, 0.75)
            with self.assertRaises(JudgeError) as raised:
                adapter.generate("second bounded prompt", schema=ScoreEnvelope)

        self.assertEqual(raised.exception.category, "subcall_budget_exceeded")
        self.assertEqual(transport.sync_call_count, 1)
        self.assertEqual(context.subcalls_used, 1)

    def test_direct_validation_audits_only_the_prompt_fingerprint(
        self,
    ) -> None:
        logger = logging.getLogger("app.deepeval_advisory.judge")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        previous_level = logger.level
        previous_propagate = logger.propagate
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        prompt = "DIRECT_EVIDENCE_SENTINEL bounded content"
        context = make_context(max_subcalls=1)
        try:
            with judge_context(context):
                validate_judge_prompt(prompt, context)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
            logger.propagate = previous_propagate

        logs = stream.getvalue()
        expected_fingerprint = hashlib.sha256(
            prompt.encode("utf-8")
        ).hexdigest()
        self.assertIn(f"prompt_sha256={expected_fingerprint}", logs)
        self.assertIn("status=validated", logs)
        self.assertIn("attempt=1", logs)
        self.assertNotIn("DIRECT_EVIDENCE_SENTINEL", logs)
        self.assertNotIn("bounded content", logs)


class FakeJudgeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_responses_are_selected_by_schema_title_and_none(
        self,
    ) -> None:
        transport = make_fake()
        context = make_context()

        score_response = transport.complete(
            prompt="same prompt",
            response_schema=ScoreEnvelope.model_json_schema(),
            context=context,
        )
        alternate_response = transport.complete(
            prompt="same prompt",
            response_schema=AlternateEnvelope.model_json_schema(),
            context=context,
        )
        text_response = await transport.acomplete(
            prompt="same prompt",
            response_schema=None,
            context=context,
        )

        self.assertEqual(
            ScoreEnvelope.model_validate_json(score_response.text),
            ScoreEnvelope(**SAFE_SCORE),
        )
        self.assertEqual(
            AlternateEnvelope.model_validate_json(alternate_response.text),
            AlternateEnvelope(verdict="bounded"),
        )
        self.assertEqual(text_response.text, "plain deterministic result")
        self.assertEqual(transport.sync_call_count, 2)
        self.assertEqual(transport.async_call_count, 1)

    async def test_acomplete_is_true_async_and_never_calls_complete(
        self,
    ) -> None:
        transport = make_fake()
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context()

        with judge_context(context):
            coroutine = adapter.a_generate(
                "bounded async prompt",
                schema=ScoreEnvelope,
            )
            self.assertTrue(inspect.isawaitable(coroutine))
            result = await coroutine

        self.assertEqual(result, ScoreEnvelope(**SAFE_SCORE))
        self.assertEqual(transport.sync_call_count, 0)
        self.assertEqual(transport.async_call_count, 1)

    async def test_fake_metadata_and_fingerprints_are_deterministic(
        self,
    ) -> None:
        first = make_fake()
        second = make_fake()
        context = make_context()
        arguments = {
            "prompt": "deterministic bounded prompt",
            "response_schema": ScoreEnvelope.model_json_schema(),
            "context": context,
        }

        first_sync = first.complete(**arguments)
        second_sync = second.complete(**arguments)
        first_async = await first.acomplete(**arguments)

        self.assertEqual(first_sync, second_sync)
        self.assertEqual(first_sync, first_async)
        self.assertEqual(first_sync.resolved_model_version, PROFILE_VERSION)
        self.assertEqual(first_sync.latency_ms, 0)
        self.assertEqual(first_sync.cost, 0.0)
        self.assertRegex(first_sync.input_fingerprint, r"^[0-9a-f]{64}$")
        self.assertRegex(first_sync.output_fingerprint, r"^[0-9a-f]{64}$")

    async def test_unknown_schema_and_invalid_configuration_fail_bounded(
        self,
    ) -> None:
        transport = make_fake(responses={"ScoreEnvelope": SAFE_SCORE})
        context = make_context()

        with self.assertRaises(JudgeError) as unknown:
            await transport.acomplete(
                prompt="bounded prompt",
                response_schema=AlternateEnvelope.model_json_schema(),
                context=context,
            )
        self.assertEqual(unknown.exception.category, "fake_schema_unconfigured")
        self.assertEqual(str(unknown.exception), "fake_schema_unconfigured")

        with self.assertRaises(JudgeError) as invalid:
            make_fake(
                responses={"ScoreEnvelope": object()},
            )
        self.assertEqual(
            invalid.exception.category,
            "fake_configuration_invalid",
        )

        with self.assertRaises(JudgeError) as invalid_failures:
            FakeJudgeTransport(
                profile_version=PROFILE_VERSION,
                responses={"ScoreEnvelope": SAFE_SCORE},
                failures=object(),
            )
        self.assertEqual(
            invalid_failures.exception.category,
            "fake_configuration_invalid",
        )


class DeepEvalJudgeLLMTests(unittest.TestCase):
    def test_load_model_name_and_adapter_version_are_immutable(self) -> None:
        transport = make_fake()
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )

        self.assertIs(adapter.load_model(), transport)
        self.assertIs(adapter.model, transport)
        self.assertEqual(adapter.get_model_name(), PROFILE_VERSION)
        self.assertEqual(
            adapter.adapter_version,
            "8099-judge-adapter-v1",
        )
        with self.assertRaises(AttributeError):
            adapter.profile_version = "changed"

    def test_generate_returns_validated_schema_and_schema_none_returns_text(
        self,
    ) -> None:
        transport = make_fake()
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context(max_subcalls=2)

        with judge_context(context):
            structured = adapter.generate(
                "bounded structured prompt",
                schema=ScoreEnvelope,
            )
            plain = adapter.generate("bounded plain prompt", schema=None)

        self.assertEqual(structured, ScoreEnvelope(**SAFE_SCORE))
        self.assertEqual(plain, "plain deterministic result")
        self.assertEqual(transport.sync_call_count, 2)

    def test_non_pydantic_schema_is_rejected_before_transport(self) -> None:
        transport = make_fake()
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context()

        with judge_context(context):
            with self.assertRaises(JudgeError) as raised:
                adapter.generate("bounded prompt", schema=dict)

        self.assertEqual(raised.exception.category, "response_schema_invalid")
        self.assertEqual(raised.exception.outcome, "not_started")
        self.assertEqual(transport.sync_call_count, 0)
        self.assertEqual(context.subcalls_used, 0)

    def test_invalid_json_unknown_missing_and_wrong_types_are_bounded(
        self,
    ) -> None:
        invalid_values = (
            "not-json",
            {"score": 0.4, "reason": "ok", "unknown": True},
            {"score": 0.4},
            {"score": "0.4", "reason": "wrong type"},
        )
        for value in invalid_values:
            with self.subTest(value=value):
                transport = make_fake(
                    responses={"ScoreEnvelope": value},
                )
                adapter = DeepEvalJudgeLLM(
                    transport,
                    profile_version=PROFILE_VERSION,
                )
                context = make_context()

                with judge_context(context):
                    with self.assertRaises(JudgeError) as raised:
                        adapter.generate(
                            "bounded prompt",
                            schema=ScoreEnvelope,
                        )

                self.assertEqual(
                    raised.exception.category,
                    "response_schema_invalid",
                )
                self.assertIs(raised.exception.retryable, False)
                self.assertEqual(raised.exception.outcome, "completed")
                self.assertEqual(str(raised.exception), "response_schema_invalid")
                self.assertEqual(transport.sync_call_count, 1)

    def test_parse_schema_none_and_strict_schema(self) -> None:
        response = JudgeResponse(
            text='{"score":0.5,"reason":"bounded"}',
            resolved_model_version=PROFILE_VERSION,
            token_input=1,
            token_output=1,
            cost=0.0,
            latency_ms=0,
            input_fingerprint="1" * 64,
            output_fingerprint="2" * 64,
        )

        self.assertEqual(
            parse_judge_response(
                response,
                None,
                expected_model_version=PROFILE_VERSION,
            ),
            response.text,
        )
        self.assertEqual(
            parse_judge_response(
                response,
                ScoreEnvelope,
                expected_model_version=PROFILE_VERSION,
            ),
            ScoreEnvelope(score=0.5, reason="bounded"),
        )

    def test_resolved_model_mismatch_is_bounded_and_not_retried(self) -> None:
        transport = make_fake(profile_version="unexpected-profile")
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context()

        with judge_context(context):
            with self.assertRaises(JudgeError) as raised:
                adapter.generate("bounded prompt", schema=ScoreEnvelope)

        self.assertEqual(raised.exception.category, "model_version_mismatch")
        self.assertIs(raised.exception.retryable, False)
        self.assertEqual(raised.exception.outcome, "completed")
        self.assertEqual(str(raised.exception), "model_version_mismatch")
        self.assertEqual(transport.sync_call_count, 1)

    def test_only_not_started_retryable_429_and_503_are_retried(self) -> None:
        retryable_cases = ("http_429", "http_503")
        for category in retryable_cases:
            with self.subTest(category=category):
                transport = make_fake(
                    failures={
                        "ScoreEnvelope": [
                            JudgeError(
                                category,
                                retryable=True,
                                outcome="not_started",
                            )
                        ]
                    }
                )
                adapter = DeepEvalJudgeLLM(
                    transport,
                    profile_version=PROFILE_VERSION,
                )
                context = make_context()
                with judge_context(context):
                    result = adapter.generate(
                        "bounded retry prompt",
                        schema=ScoreEnvelope,
                    )
                self.assertEqual(result, ScoreEnvelope(**SAFE_SCORE))
                self.assertEqual(transport.sync_call_count, 2)
                self.assertEqual(context.subcalls_used, 2)

        no_retry_cases = (
            JudgeError(
                "http_429",
                retryable=False,
                outcome="not_started",
            ),
            JudgeError(
                "http_503",
                retryable=True,
                outcome="completed",
            ),
            JudgeError(
                "http_503",
                retryable=True,
                outcome="unknown",
            ),
            JudgeError(
                "http_500",
                retryable=True,
                outcome="not_started",
            ),
        )
        for error in no_retry_cases:
            with self.subTest(
                category=error.category,
                retryable=error.retryable,
                outcome=error.outcome,
            ):
                transport = make_fake(
                    failures={"ScoreEnvelope": [error]},
                )
                adapter = DeepEvalJudgeLLM(
                    transport,
                    profile_version=PROFILE_VERSION,
                )
                context = make_context()
                with judge_context(context):
                    with self.assertRaises(JudgeError) as raised:
                        adapter.generate(
                            "bounded no retry prompt",
                            schema=ScoreEnvelope,
                        )
                self.assertIs(raised.exception, error)
                self.assertEqual(transport.sync_call_count, 1)
                self.assertEqual(context.subcalls_used, 1)

    def test_retries_never_exceed_three_total_attempts(self) -> None:
        failures = [
            JudgeError(
                "http_503",
                retryable=True,
                outcome="not_started",
            )
            for _ in range(3)
        ]
        transport = make_fake(
            failures={"ScoreEnvelope": failures},
        )
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context(max_subcalls=8)

        with judge_context(context):
            with self.assertRaises(JudgeError) as raised:
                adapter.generate("bounded retry prompt", schema=ScoreEnvelope)

        self.assertEqual(raised.exception.category, "http_503")
        self.assertEqual(transport.sync_call_count, 3)
        self.assertEqual(context.subcalls_used, 3)

    def test_retry_attempts_cannot_bypass_subcall_budget(self) -> None:
        failures = [
            JudgeError(
                "http_429",
                retryable=True,
                outcome="not_started",
            )
            for _ in range(2)
        ]
        transport = make_fake(
            failures={"ScoreEnvelope": failures},
        )
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context(max_subcalls=2)

        with judge_context(context):
            with self.assertRaises(JudgeError) as raised:
                adapter.generate("bounded retry prompt", schema=ScoreEnvelope)

        self.assertEqual(raised.exception.category, "subcall_budget_exceeded")
        self.assertEqual(transport.sync_call_count, 2)
        self.assertEqual(context.subcalls_used, 2)

    def test_unexpected_transport_exception_is_bounded_without_retry(
        self,
    ) -> None:
        transport = make_fake(
            failures={
                "ScoreEnvelope": [
                    RuntimeError(
                        "provider body PROMPT_SENTINEL credential=SENTINEL"
                    )
                ]
            },
        )
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context()

        with judge_context(context):
            with self.assertRaises(JudgeError) as raised:
                adapter.generate("bounded prompt", schema=ScoreEnvelope)

        self.assertEqual(raised.exception.category, "unavailable")
        self.assertEqual(raised.exception.outcome, "unknown")
        self.assertEqual(str(raised.exception), "unavailable")
        self.assertNotIn("SENTINEL", str(raised.exception))
        self.assertEqual(transport.sync_call_count, 1)

    def test_logs_contain_only_safe_identity_hash_status_and_attempt(
        self,
    ) -> None:
        logger = logging.getLogger("app.deepeval_advisory.judge")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        previous_level = logger.level
        previous_propagate = logger.propagate
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        try:
            transport = make_fake()
            adapter = DeepEvalJudgeLLM(
                transport,
                profile_version=PROFILE_VERSION,
            )
            context = make_context()
            prompt = "EVIDENCE_SENTINEL bounded content"
            with judge_context(context):
                adapter.generate(prompt, schema=ScoreEnvelope)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
            logger.propagate = previous_propagate

        logs = stream.getvalue()
        self.assertIn("request_id=req_12345678", logs)
        self.assertIn("evaluation_id=eval_12345678", logs)
        self.assertIn(f"run_ref=runref_{'a' * 32}", logs)
        self.assertIn("metric_id=faithfulness", logs)
        self.assertIn("metric_version=v1", logs)
        self.assertIn(f"projection_sha256={'b' * 64}", logs)
        self.assertRegex(logs, r"prompt_sha256=[0-9a-f]{64}")
        self.assertIn("status=attempt", logs)
        self.assertIn("attempt=1", logs)
        self.assertNotIn("EVIDENCE_SENTINEL", logs)
        self.assertNotIn("bounded content", logs)
        self.assertNotIn("api_key", logs)
        self.assertNotIn("credential", logs)
        self.assertNotIn(PROFILE_VERSION, logs)

        allowed_keys = {
            "request_id",
            "evaluation_id",
            "run_ref",
            "metric_id",
            "metric_version",
            "projection_sha256",
            "prompt_sha256",
            "status",
            "attempt",
        }
        for line in logs.splitlines():
            parts = line.split()
            self.assertEqual(parts[0], "judge_call")
            self.assertEqual(
                {part.split("=", 1)[0] for part in parts[1:]},
                allowed_keys,
            )


class DeepEvalJudgeAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_retry_matrix_and_budget_match_sync_contract(
        self,
    ) -> None:
        transport = make_fake(
            failures={
                "ScoreEnvelope": [
                    JudgeError(
                        "http_503",
                        retryable=True,
                        outcome="not_started",
                    )
                ]
            }
        )
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context(max_subcalls=2)

        with judge_context(context):
            result = await adapter.a_generate(
                "bounded async retry prompt",
                schema=ScoreEnvelope,
            )

        self.assertEqual(result, ScoreEnvelope(**SAFE_SCORE))
        self.assertEqual(transport.sync_call_count, 0)
        self.assertEqual(transport.async_call_count, 2)
        self.assertEqual(context.subcalls_used, 2)

    async def test_async_outcome_unknown_is_not_retried(self) -> None:
        error = JudgeError(
            "http_503",
            retryable=True,
            outcome="unknown",
        )
        transport = make_fake(
            failures={"ScoreEnvelope": [error]},
        )
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context()

        with judge_context(context):
            with self.assertRaises(JudgeError) as raised:
                await adapter.a_generate(
                    "bounded async prompt",
                    schema=ScoreEnvelope,
                )

        self.assertIs(raised.exception, error)
        self.assertEqual(transport.sync_call_count, 0)
        self.assertEqual(transport.async_call_count, 1)


class JudgeProductionSourceContractTests(unittest.TestCase):
    def test_only_protocol_methods_raise_not_implemented(self) -> None:
        tree = ast.parse(JUDGE_PATH.read_text(encoding="utf-8"))
        protocol = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "JudgeTransport"
        )
        protocol_methods = {
            node.name: node
            for node in protocol.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        expected = {"complete", "acomplete"}
        self.assertEqual(set(protocol_methods), expected)
        for method_name in expected:
            method = protocol_methods[method_name]
            self.assertEqual(len(method.body), 1)
            statement = method.body[0]
            self.assertIsInstance(statement, ast.Raise)
            exception = statement.exc
            self.assertIsInstance(exception, ast.Call)
            self.assertIsInstance(exception.func, ast.Name)
            self.assertEqual(exception.func.id, "NotImplementedError")

        all_not_implemented_lines = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise):
                continue
            exception = node.exc
            if isinstance(exception, ast.Call):
                exception = exception.func
            if (
                isinstance(exception, ast.Name)
                and exception.id == "NotImplementedError"
            ):
                all_not_implemented_lines.append(node.lineno)

        self.assertEqual(
            sorted(all_not_implemented_lines),
            sorted(
                protocol_methods[name].body[0].lineno
                for name in expected
            ),
        )

    def test_production_adapter_has_no_http_transport_import(self) -> None:
        tree = ast.parse(JUDGE_PATH.read_text(encoding="utf-8"))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0]
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        self.assertTrue(
            imported_roots.isdisjoint(
                {"aiohttp", "httpx", "requests", "urllib"}
            )
        )


if __name__ == "__main__":
    unittest.main()
