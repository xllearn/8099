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
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict, Field, ValidationError

import app.deepeval_advisory.judge as judge_module
from app.deepeval_advisory.hashing import (
    canonical_json_bytes,
    text_sha256,
)
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


class LargeSchemaEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(description="d" * 8192)


def exact_request_prompt(
    response_schema: dict[str, object] | None,
    *,
    multibyte: bool = False,
) -> str:
    empty_size = len(
        canonical_json_bytes(
            {
                "prompt": "",
                "response_schema": response_schema,
            }
        )
    )
    remaining = 65536 - empty_size
    if remaining < 0:
        raise AssertionError("test schema exceeds the request budget")
    if multibyte:
        prompt = ("界" * (remaining // 3)) + ("a" * (remaining % 3))
    else:
        prompt = "a" * remaining
    if (
        len(
            canonical_json_bytes(
                {
                    "prompt": prompt,
                    "response_schema": response_schema,
                }
            )
        )
        != 65536
    ):
        raise AssertionError("test prompt is not exactly 64 KiB")
    return prompt


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


def require_usage_snapshot(
    test_case: unittest.TestCase,
    context: JudgeCallContext,
) -> object:
    test_case.assertTrue(
        hasattr(judge_module, "JudgeUsageSnapshot"),
        "JudgeUsageSnapshot must be public",
    )
    test_case.assertTrue(
        hasattr(context, "usage_snapshot"),
        "JudgeCallContext.usage_snapshot() must be public",
    )
    snapshot = context.usage_snapshot()
    test_case.assertIsInstance(
        snapshot,
        judge_module.JudgeUsageSnapshot,
    )
    return snapshot


class UsageMetadataTransport:
    def __init__(
        self,
        metadata: list[dict[str, int | float]],
        *,
        responses: dict[str | None, object] | None = None,
    ) -> None:
        self._fake = make_fake(responses=responses)
        self._metadata = tuple(metadata)
        self._lock = threading.Lock()
        self._metadata_index = 0

    @property
    def sync_call_count(self) -> int:
        return self._fake.sync_call_count

    @property
    def async_call_count(self) -> int:
        return self._fake.async_call_count

    def _attach_metadata(self, response: JudgeResponse) -> JudgeResponse:
        with self._lock:
            if self._metadata_index >= len(self._metadata):
                raise AssertionError("missing test response metadata")
            metadata = self._metadata[self._metadata_index]
            self._metadata_index += 1
        return response.model_copy(update=metadata)

    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, object] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        return self._attach_metadata(
            self._fake.complete(
                prompt=prompt,
                response_schema=response_schema,
                context=context,
            )
        )

    async def acomplete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, object] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        return self._attach_metadata(
            await self._fake.acomplete(
                prompt=prompt,
                response_schema=response_schema,
                context=context,
            )
        )


class TamperedFingerprintTransport:
    def __init__(self, field_name: str) -> None:
        self._field_name = field_name
        self._fake = make_fake(
            responses={
                "ScoreEnvelope": "RESPONSE_BODY_SENTINEL not-json"
            },
        )

    @property
    def sync_call_count(self) -> int:
        return self._fake.sync_call_count

    @property
    def async_call_count(self) -> int:
        return self._fake.async_call_count

    def _tamper(self, response: JudgeResponse) -> JudgeResponse:
        current = getattr(response, self._field_name)
        replacement = "0" * 64 if current != "0" * 64 else "1" * 64
        return response.model_copy(
            update={self._field_name: replacement},
        )

    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, object] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        return self._tamper(
            self._fake.complete(
                prompt=prompt,
                response_schema=response_schema,
                context=context,
            )
        )

    async def acomplete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, object] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        return self._tamper(
            await self._fake.acomplete(
                prompt=prompt,
                response_schema=response_schema,
                context=context,
            )
        )


class ClockAdvancingSyncTransport:
    def __init__(self, clock: list[float], late_time: float) -> None:
        self._clock = clock
        self._late_time = late_time
        self._fake = make_fake()

    @property
    def sync_call_count(self) -> int:
        return self._fake.sync_call_count

    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, object] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        response = self._fake.complete(
            prompt=prompt,
            response_schema=response_schema,
            context=context,
        )
        self._clock[0] = self._late_time
        return response

    async def acomplete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, object] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        return await self._fake.acomplete(
            prompt=prompt,
            response_schema=response_schema,
            context=context,
        )


class BlockingAsyncTransport:
    def __init__(self, *, suppress_cancellation: bool = False) -> None:
        self.suppress_cancellation = suppress_cancellation
        self.sync_call_count = 0
        self.async_call_count = 0
        self.started = asyncio.Event()
        self.cancelled = False
        self._release = asyncio.Event()

    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, object] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        self.sync_call_count += 1
        raise AssertionError("sync transport must not be called")

    async def acomplete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, object] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        self.async_call_count += 1
        self.started.set()
        try:
            await self._release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            if not self.suppress_cancellation:
                raise

        response_text = (
            '{"reason":"LATE_RESPONSE_SENTINEL","score":0.5}'
        )
        return JudgeResponse(
            text=response_text,
            resolved_model_version=PROFILE_VERSION,
            token_input=1,
            token_output=1,
            cost=0.0,
            latency_ms=0,
            input_fingerprint=text_sha256(prompt),
            output_fingerprint=text_sha256(response_text),
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
        protocol_doc = JudgeTransport.__doc__ or ""
        self.assertIn("deadline_monotonic", protocol_doc)
        self.assertIn("synchronous", protocol_doc)

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


class JudgeUsageLedgerTests(unittest.TestCase):
    def test_initial_snapshot_is_immutable_safe_and_zeroed(self) -> None:
        snapshot = require_usage_snapshot(self, make_context())

        self.assertEqual(
            {item.name for item in fields(snapshot)},
            {
                "token_input",
                "token_output",
                "cost",
                "latency_ms",
                "response_count",
                "input_fingerprints",
                "output_fingerprints",
            },
        )
        self.assertEqual(snapshot.token_input, 0)
        self.assertEqual(snapshot.token_output, 0)
        self.assertEqual(snapshot.cost, 0.0)
        self.assertTrue(math.isfinite(snapshot.cost))
        self.assertEqual(snapshot.latency_ms, 0)
        self.assertEqual(snapshot.response_count, 0)
        self.assertEqual(snapshot.input_fingerprints, ())
        self.assertEqual(snapshot.output_fingerprints, ())
        with self.assertRaises(FrozenInstanceError):
            snapshot.response_count = 1
        with self.assertRaises(FrozenInstanceError):
            snapshot.input_fingerprints += ("0" * 64,)

    def test_one_sync_response_is_recorded_exactly_once(self) -> None:
        metadata = {
            "token_input": 11,
            "token_output": 7,
            "cost": 0.125,
            "latency_ms": 23,
        }
        transport = UsageMetadataTransport([metadata])
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context()
        prompt = "one bounded sync call"

        with judge_context(context):
            response_text = adapter.generate(prompt)

        snapshot = require_usage_snapshot(self, context)
        self.assertEqual(response_text, "plain deterministic result")
        self.assertEqual(snapshot.token_input, 11)
        self.assertEqual(snapshot.token_output, 7)
        self.assertEqual(snapshot.cost, 0.125)
        self.assertEqual(snapshot.latency_ms, 23)
        self.assertEqual(snapshot.response_count, 1)
        self.assertEqual(
            snapshot.input_fingerprints,
            (text_sha256(prompt),),
        )
        self.assertEqual(
            snapshot.output_fingerprints,
            (text_sha256(response_text),),
        )

    def test_multiple_calls_aggregate_and_preserve_fingerprint_order(
        self,
    ) -> None:
        transport = UsageMetadataTransport(
            [
                {
                    "token_input": 3,
                    "token_output": 5,
                    "cost": 0.25,
                    "latency_ms": 7,
                },
                {
                    "token_input": 11,
                    "token_output": 13,
                    "cost": 0.5,
                    "latency_ms": 17,
                },
            ]
        )
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context()
        prompts = ("first bounded call", "second bounded call")

        with judge_context(context):
            outputs = tuple(adapter.generate(prompt) for prompt in prompts)

        snapshot = require_usage_snapshot(self, context)
        self.assertEqual(snapshot.token_input, 14)
        self.assertEqual(snapshot.token_output, 18)
        self.assertEqual(snapshot.cost, 0.75)
        self.assertTrue(math.isfinite(snapshot.cost))
        self.assertEqual(snapshot.latency_ms, 24)
        self.assertEqual(snapshot.response_count, 2)
        self.assertEqual(
            snapshot.input_fingerprints,
            tuple(text_sha256(prompt) for prompt in prompts),
        )
        self.assertEqual(
            snapshot.output_fingerprints,
            tuple(text_sha256(output) for output in outputs),
        )

    def test_schema_invalid_paid_response_is_still_recorded(self) -> None:
        response_text = "USAGE_SCHEMA_SENTINEL not-json"
        transport = UsageMetadataTransport(
            [
                {
                    "token_input": 29,
                    "token_output": 31,
                    "cost": 0.75,
                    "latency_ms": 37,
                }
            ],
            responses={"ScoreEnvelope": response_text},
        )
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context()
        prompt = "bounded schema-invalid call"

        with judge_context(context):
            with self.assertRaises(JudgeError) as raised:
                adapter.generate(prompt, schema=ScoreEnvelope)

        snapshot = require_usage_snapshot(self, context)
        self.assertEqual(raised.exception.category, "response_schema_invalid")
        self.assertEqual(snapshot.response_count, 1)
        self.assertEqual(snapshot.token_input, 29)
        self.assertEqual(snapshot.token_output, 31)
        self.assertEqual(snapshot.cost, 0.75)
        self.assertEqual(snapshot.latency_ms, 37)
        self.assertEqual(snapshot.input_fingerprints, (text_sha256(prompt),))
        self.assertEqual(
            snapshot.output_fingerprints,
            (text_sha256(response_text),),
        )

    def test_fingerprint_or_model_mismatch_metadata_is_not_recorded(
        self,
    ) -> None:
        cases = (
            (
                TamperedFingerprintTransport("output_fingerprint"),
                "response_integrity_invalid",
            ),
            (
                make_fake(profile_version="unexpected-profile"),
                "model_version_mismatch",
            ),
        )
        for transport, category in cases:
            with self.subTest(category=category):
                adapter = DeepEvalJudgeLLM(
                    transport,
                    profile_version=PROFILE_VERSION,
                )
                context = make_context()
                with judge_context(context):
                    with self.assertRaises(JudgeError) as raised:
                        adapter.generate(
                            "bounded rejected metadata",
                            schema=ScoreEnvelope,
                        )

                self.assertEqual(raised.exception.category, category)
                snapshot = require_usage_snapshot(self, context)
                self.assertEqual(
                    snapshot,
                    judge_module.JudgeUsageSnapshot(),
                )

    def test_not_started_retries_only_record_the_actual_response(self) -> None:
        transport = make_fake(
            failures={
                None: [
                    JudgeError(
                        "http_429",
                        retryable=True,
                        outcome="not_started",
                    ),
                    JudgeError(
                        "http_503",
                        retryable=True,
                        outcome="not_started",
                    ),
                ]
            }
        )
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context(max_subcalls=3)
        prompt = "bounded retry usage"

        with judge_context(context):
            response_text = adapter.generate(prompt)

        snapshot = require_usage_snapshot(self, context)
        self.assertEqual(transport.sync_call_count, 3)
        self.assertEqual(context.subcalls_used, 3)
        self.assertEqual(snapshot.response_count, 1)
        self.assertEqual(snapshot.token_input, len(prompt.encode("utf-8")))
        self.assertEqual(
            snapshot.token_output,
            len(response_text.encode("utf-8")),
        )
        self.assertEqual(snapshot.input_fingerprints, (text_sha256(prompt),))
        self.assertEqual(
            snapshot.output_fingerprints,
            (text_sha256(response_text),),
        )

    def test_snapshot_and_logs_never_retain_prompt_or_response_content(
        self,
    ) -> None:
        prompt = "USAGE_PROMPT_SENTINEL bounded-content"
        response_text = "USAGE_RESPONSE_SENTINEL token=do-not-retain"
        transport = make_fake(responses={None: response_text})
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context()
        logger = logging.getLogger("app.deepeval_advisory.judge")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        previous_level = logger.level
        previous_propagate = logger.propagate
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        try:
            with judge_context(context):
                self.assertEqual(adapter.generate(prompt), response_text)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
            logger.propagate = previous_propagate

        snapshot = require_usage_snapshot(self, context)
        retained = repr(snapshot)
        logs = stream.getvalue()
        for sentinel in (
            "USAGE_PROMPT_SENTINEL",
            "USAGE_RESPONSE_SENTINEL",
            "do-not-retain",
        ):
            self.assertNotIn(sentinel, retained)
            self.assertNotIn(sentinel, logs)


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


class JudgeAsyncUsageLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_async_response_is_recorded_exactly_once(self) -> None:
        transport = UsageMetadataTransport(
            [
                {
                    "token_input": 41,
                    "token_output": 43,
                    "cost": 1.25,
                    "latency_ms": 47,
                }
            ]
        )
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context()
        prompt = "one bounded async call"

        with judge_context(context):
            response_text = await adapter.a_generate(prompt)

        snapshot = require_usage_snapshot(self, context)
        self.assertEqual(transport.sync_call_count, 0)
        self.assertEqual(transport.async_call_count, 1)
        self.assertEqual(snapshot.token_input, 41)
        self.assertEqual(snapshot.token_output, 43)
        self.assertEqual(snapshot.cost, 1.25)
        self.assertEqual(snapshot.latency_ms, 47)
        self.assertEqual(snapshot.response_count, 1)
        self.assertEqual(snapshot.input_fingerprints, (text_sha256(prompt),))
        self.assertEqual(
            snapshot.output_fingerprints,
            (text_sha256(response_text),),
        )

    async def test_concurrent_contexts_have_isolated_usage_ledgers(
        self,
    ) -> None:
        adapter = DeepEvalJudgeLLM(
            make_fake(),
            profile_version=PROFILE_VERSION,
        )
        contexts = (
            make_context(request_id="req_usage_one1"),
            make_context(request_id="req_usage_two2"),
        )
        prompts = ("first isolated prompt", "second isolated prompt")

        async def call(
            context: JudgeCallContext,
            prompt: str,
        ) -> object:
            with judge_context(context):
                await asyncio.sleep(0)
                await adapter.a_generate(prompt)
                await asyncio.sleep(0)
                return require_usage_snapshot(self, context)

        snapshots = await asyncio.gather(
            *(call(context, prompt) for context, prompt in zip(contexts, prompts))
        )

        for snapshot, prompt in zip(snapshots, prompts):
            self.assertEqual(snapshot.response_count, 1)
            self.assertEqual(
                snapshot.input_fingerprints,
                (text_sha256(prompt),),
            )
        self.assertNotEqual(
            snapshots[0].input_fingerprints,
            snapshots[1].input_fingerprints,
        )


class JudgePromptGuardTests(unittest.TestCase):
    def test_utf8_size_limit_allows_exactly_64_kib_and_rejects_one_more(
        self,
    ) -> None:
        exact = exact_request_prompt(None)
        too_large = exact + "a"
        exact_context = make_context(max_subcalls=1)
        large_context = make_context(max_subcalls=1)

        with judge_context(exact_context):
            self.assertEqual(
                validate_judge_prompt(
                    exact,
                    exact_context,
                    response_schema=None,
                ),
                exact,
            )
        self.assertEqual(exact_context.subcalls_used, 1)

        with judge_context(large_context):
            with self.assertRaises(JudgeError) as raised:
                validate_judge_prompt(
                    too_large,
                    large_context,
                    response_schema=None,
                )
        self.assertEqual(raised.exception.category, "request_too_large")
        self.assertEqual(large_context.subcalls_used, 0)

    def test_utf8_size_limit_counts_multibyte_bytes(self) -> None:
        exact = exact_request_prompt(None, multibyte=True)
        too_large = exact + "界"
        self.assertEqual(
            len(
                canonical_json_bytes(
                    {"prompt": exact, "response_schema": None}
                )
            ),
            65536,
        )
        self.assertEqual(
            len(
                canonical_json_bytes(
                    {"prompt": too_large, "response_schema": None}
                )
            ),
            65539,
        )

        exact_context = make_context(max_subcalls=1)
        with judge_context(exact_context):
            self.assertEqual(
                validate_judge_prompt(
                    exact,
                    exact_context,
                    response_schema=None,
                ),
                exact,
            )

        large_context = make_context(max_subcalls=1)
        with judge_context(large_context):
            with self.assertRaises(JudgeError) as raised:
                validate_judge_prompt(
                    too_large,
                    large_context,
                    response_schema=None,
                )
        self.assertEqual(raised.exception.category, "request_too_large")
        self.assertEqual(large_context.subcalls_used, 0)

    def test_large_pydantic_schema_counts_toward_request_budget(
        self,
    ) -> None:
        response_schema = LargeSchemaEnvelope.model_json_schema()
        exact = exact_request_prompt(response_schema)
        exact_transport = make_fake(
            responses={"LargeSchemaEnvelope": {"value": "bounded"}},
        )
        exact_adapter = DeepEvalJudgeLLM(
            exact_transport,
            profile_version=PROFILE_VERSION,
        )
        exact_context = make_context(max_subcalls=1)

        with judge_context(exact_context):
            result = exact_adapter.generate(
                exact,
                schema=LargeSchemaEnvelope,
            )
        self.assertEqual(result, LargeSchemaEnvelope(value="bounded"))
        self.assertEqual(exact_transport.sync_call_count, 1)

        large_transport = make_fake(
            responses={"LargeSchemaEnvelope": {"value": "bounded"}},
        )
        large_adapter = DeepEvalJudgeLLM(
            large_transport,
            profile_version=PROFILE_VERSION,
        )
        large_context = make_context(max_subcalls=1)
        with judge_context(large_context):
            with self.assertRaises(JudgeError) as raised:
                large_adapter.generate(
                    exact + "a",
                    schema=LargeSchemaEnvelope,
                )

        self.assertEqual(raised.exception.category, "request_too_large")
        self.assertEqual(large_transport.sync_call_count, 0)
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
        responses = {
            "ScoreEnvelope": SAFE_SCORE,
            None: "deterministic\r\nplain result",
        }
        first = make_fake(responses=responses)
        second = make_fake(responses=responses)
        context = make_context()
        arguments = {
            "prompt": "deterministic\r\nbounded prompt",
            "response_schema": ScoreEnvelope.model_json_schema(),
            "context": context,
        }

        first_sync = first.complete(**arguments)
        second_sync = second.complete(**arguments)
        first_async = await first.acomplete(**arguments)
        text_response = first.complete(
            prompt=arguments["prompt"],
            response_schema=None,
            context=context,
        )

        self.assertEqual(first_sync, second_sync)
        self.assertEqual(first_sync, first_async)
        self.assertEqual(first_sync.resolved_model_version, PROFILE_VERSION)
        self.assertEqual(first_sync.latency_ms, 0)
        self.assertEqual(first_sync.cost, 0.0)
        self.assertEqual(
            first_sync.input_fingerprint,
            text_sha256(arguments["prompt"]),
        )
        self.assertEqual(
            first_sync.output_fingerprint,
            text_sha256(first_sync.text),
        )
        self.assertEqual(
            text_response.output_fingerprint,
            text_sha256(text_response.text),
        )

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


class DeepEvalTraceIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_methods_are_marked_unwrapped_judge_functions(
        self,
    ) -> None:
        for method_name in ("generate", "a_generate"):
            method = getattr(DeepEvalJudgeLLM, method_name)
            with self.subTest(method=method_name):
                self.assertIs(
                    getattr(method, "_is_deepeval_observed", None),
                    True,
                )
                self.assertEqual(
                    Path(method.__code__.co_filename).name,
                    "judge.py",
                )
                self.assertFalse(hasattr(method, "__wrapped__"))

    async def test_internal_trace_harness_captures_controls_not_judge_data(
        self,
    ) -> None:
        import deepeval.tracing.tracing as tracing
        from deepeval.tracing.context import current_span_context

        captures: list[dict[str, object]] = []

        class CapturingObserver:
            def __init__(self, *args, **kwargs) -> None:
                self.result = None
                self.record = {
                    "args": args,
                    "kwargs": kwargs,
                    "result": None,
                }

            def __enter__(self):
                captures.append(self.record)
                return self

            def __exit__(self, exc_type, exc, traceback) -> bool:
                self.record["result"] = self.result
                return False

        with (
            patch.object(tracing, "Observer", CapturingObserver),
            patch.object(
                tracing,
                "get_settings",
                return_value=SimpleNamespace(
                    CONFIDENT_TRACE_INTERNAL=True,
                ),
            ),
        ):
            parent_token = current_span_context.set(object())
            try:

                @tracing.observe(
                    type="llm",
                    _drop_if_root=True,
                    _internal=True,
                )
                def sync_control(value: str) -> str:
                    return "CONTROL_SYNC_RESULT"

                @tracing.observe(
                    type="llm",
                    _drop_if_root=True,
                    _internal=True,
                )
                async def async_control(value: str) -> str:
                    return "CONTROL_ASYNC_RESULT"

                self.assertEqual(
                    sync_control("CONTROL_SYNC_PROMPT"),
                    "CONTROL_SYNC_RESULT",
                )
                self.assertEqual(
                    await async_control("CONTROL_ASYNC_PROMPT"),
                    "CONTROL_ASYNC_RESULT",
                )

                transport = make_fake(
                    responses={
                        "ScoreEnvelope": {
                            "score": 0.75,
                            "reason": "TRACE_RESULT_SENTINEL",
                        }
                    },
                )
                adapter = DeepEvalJudgeLLM(
                    transport,
                    profile_version=PROFILE_VERSION,
                )
                context = make_context(max_subcalls=2)
                with judge_context(context):
                    sync_result = adapter.generate(
                        "TRACE_PROMPT_SENTINEL sync bounded",
                        schema=ScoreEnvelope,
                    )
                    async_result = await adapter.a_generate(
                        "TRACE_PROMPT_SENTINEL async bounded",
                        schema=ScoreEnvelope,
                    )
                self.assertEqual(
                    sync_result.reason,
                    "TRACE_RESULT_SENTINEL",
                )
                self.assertEqual(
                    async_result.reason,
                    "TRACE_RESULT_SENTINEL",
                )
            finally:
                current_span_context.reset(parent_token)

        self.assertEqual(len(captures), 2)
        serialized_captures = repr(captures)
        self.assertIn("CONTROL_SYNC_PROMPT", serialized_captures)
        self.assertIn("CONTROL_ASYNC_PROMPT", serialized_captures)
        self.assertNotIn("TRACE_PROMPT_SENTINEL", serialized_captures)
        self.assertNotIn("TRACE_RESULT_SENTINEL", serialized_captures)


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

    def test_sync_fingerprint_mismatch_is_rejected_before_parsing(
        self,
    ) -> None:
        for field_name in (
            "input_fingerprint",
            "output_fingerprint",
        ):
            with self.subTest(field=field_name):
                transport = TamperedFingerprintTransport(field_name)
                adapter = DeepEvalJudgeLLM(
                    transport,
                    profile_version=PROFILE_VERSION,
                )
                context = make_context(max_subcalls=3)
                with judge_context(context):
                    with self.assertRaises(JudgeError) as raised:
                        adapter.generate(
                            "bounded integrity prompt",
                            schema=ScoreEnvelope,
                        )

                self.assertEqual(
                    raised.exception.category,
                    "response_integrity_invalid",
                )
                self.assertIs(raised.exception.retryable, False)
                self.assertEqual(raised.exception.outcome, "completed")
                self.assertEqual(
                    str(raised.exception),
                    "response_integrity_invalid",
                )
                self.assertNotIn(
                    "RESPONSE_BODY_SENTINEL",
                    str(raised.exception),
                )
                self.assertEqual(transport.sync_call_count, 1)
                self.assertEqual(context.subcalls_used, 1)

    def test_late_sync_response_is_rejected_after_transport_returns(
        self,
    ) -> None:
        clock = [100.0]
        transport = ClockAdvancingSyncTransport(clock, late_time=102.0)
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context(
            max_subcalls=3,
            fresh_until_monotonic=110.0,
            deadline_monotonic=101.0,
        )

        with patch.object(
            judge_module,
            "time",
            SimpleNamespace(monotonic=lambda: clock[0]),
        ):
            with judge_context(context):
                with self.assertRaises(JudgeError) as raised:
                    adapter.generate(
                        "bounded deadline prompt",
                        schema=ScoreEnvelope,
                    )

        self.assertEqual(raised.exception.category, "deadline_exceeded")
        self.assertIs(raised.exception.retryable, False)
        self.assertEqual(raised.exception.outcome, "unknown")
        self.assertEqual(str(raised.exception), "deadline_exceeded")
        self.assertEqual(transport.sync_call_count, 1)
        self.assertEqual(context.subcalls_used, 1)

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
    async def test_async_fingerprint_mismatch_is_not_retried(self) -> None:
        transport = TamperedFingerprintTransport("output_fingerprint")
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context(max_subcalls=3)

        with judge_context(context):
            with self.assertRaises(JudgeError) as raised:
                await adapter.a_generate(
                    "bounded async integrity prompt",
                    schema=ScoreEnvelope,
                )

        self.assertEqual(
            raised.exception.category,
            "response_integrity_invalid",
        )
        self.assertIs(raised.exception.retryable, False)
        self.assertEqual(raised.exception.outcome, "completed")
        self.assertNotIn(
            "RESPONSE_BODY_SENTINEL",
            str(raised.exception),
        )
        self.assertEqual(transport.sync_call_count, 0)
        self.assertEqual(transport.async_call_count, 1)
        self.assertEqual(context.subcalls_used, 1)

    async def test_async_transport_is_bounded_by_remaining_deadline(
        self,
    ) -> None:
        transport = BlockingAsyncTransport()
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        now = time.monotonic()
        context = make_context(
            max_subcalls=3,
            fresh_until_monotonic=now + 5,
            deadline_monotonic=now + 1.0,
        )

        with judge_context(context):
            with self.assertRaises(JudgeError) as raised:
                await asyncio.wait_for(
                    adapter.a_generate(
                        "bounded async deadline prompt",
                        schema=ScoreEnvelope,
                    ),
                    timeout=2.5,
                )

        self.assertEqual(raised.exception.category, "deadline_exceeded")
        self.assertIs(raised.exception.retryable, False)
        self.assertEqual(raised.exception.outcome, "unknown")
        self.assertEqual(transport.sync_call_count, 0)
        self.assertEqual(transport.async_call_count, 1)
        self.assertTrue(transport.cancelled)
        self.assertEqual(context.subcalls_used, 1)

    async def test_async_late_response_after_timeout_is_not_accepted(
        self,
    ) -> None:
        transport = BlockingAsyncTransport(suppress_cancellation=True)
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        now = time.monotonic()
        context = make_context(
            max_subcalls=3,
            fresh_until_monotonic=now + 5,
            deadline_monotonic=now + 0.2,
        )

        with judge_context(context):
            with self.assertRaises(JudgeError) as raised:
                await asyncio.wait_for(
                    adapter.a_generate(
                        "bounded async late response prompt",
                        schema=ScoreEnvelope,
                    ),
                    timeout=0.8,
                )

        self.assertEqual(raised.exception.category, "deadline_exceeded")
        self.assertIs(raised.exception.retryable, False)
        self.assertEqual(raised.exception.outcome, "unknown")
        self.assertNotIn(
            "LATE_RESPONSE_SENTINEL",
            str(raised.exception),
        )
        self.assertEqual(transport.async_call_count, 1)
        self.assertTrue(transport.cancelled)
        self.assertEqual(context.subcalls_used, 1)

    async def test_transport_timeout_error_before_deadline_is_unavailable(
        self,
    ) -> None:
        transport = make_fake(
            failures={
                "ScoreEnvelope": [
                    TimeoutError("PROVIDER_TIMEOUT_BODY_SENTINEL")
                ]
            },
        )
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        context = make_context(max_subcalls=3)

        with judge_context(context):
            with self.assertRaises(JudgeError) as raised:
                await adapter.a_generate(
                    "bounded provider timeout prompt",
                    schema=ScoreEnvelope,
                )

        self.assertEqual(raised.exception.category, "unavailable")
        self.assertEqual(raised.exception.outcome, "unknown")
        self.assertNotIn(
            "PROVIDER_TIMEOUT_BODY_SENTINEL",
            str(raised.exception),
        )
        self.assertEqual(transport.async_call_count, 1)
        self.assertEqual(context.subcalls_used, 1)

    async def test_external_async_cancellation_is_preserved(self) -> None:
        transport = BlockingAsyncTransport()
        adapter = DeepEvalJudgeLLM(
            transport,
            profile_version=PROFILE_VERSION,
        )
        now = time.monotonic()
        context = make_context(
            max_subcalls=3,
            fresh_until_monotonic=now + 10,
            deadline_monotonic=now + 5,
        )

        with judge_context(context):
            task = asyncio.create_task(
                adapter.a_generate(
                    "bounded externally cancelled prompt",
                    schema=ScoreEnvelope,
                )
            )
            await asyncio.wait_for(transport.started.wait(), timeout=0.5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(transport.async_call_count, 1)
        self.assertTrue(transport.cancelled)
        self.assertEqual(context.subcalls_used, 1)

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
