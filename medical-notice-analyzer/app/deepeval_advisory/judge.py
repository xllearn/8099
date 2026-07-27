from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import hmac
import json
import logging
import math
import re
import threading
import time
from typing import Any, Protocol

from deepeval.models import DeepEvalBaseLLM
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.deepeval_advisory.hashing import (
    BoundaryViolation,
    assert_safe_outbound_text,
    canonical_json_bytes,
    text_sha256,
)
from app.deepeval_advisory.models import StrictModel


_LOGGER = logging.getLogger(__name__)
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_ATTEMPTS = 3
_RETRYABLE_CATEGORIES = frozenset({"http_429", "http_503"})
_REQUEST_ID_PATTERN = re.compile(r"^req_[A-Za-z0-9_-]{8,80}$")
_EVALUATION_ID_PATTERN = re.compile(r"^eval_[A-Za-z0-9_-]{8,80}$")
_RUN_REF_PATTERN = re.compile(r"^runref_[0-9a-f]{32}$")
_METRIC_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_METRIC_VERSION_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")


class JudgeError(RuntimeError):
    def __init__(
        self,
        category: str,
        *,
        retryable: bool = False,
        outcome: str = "not_started",
    ) -> None:
        super().__init__(category)
        self.category = category
        self.retryable = retryable
        self.outcome = outcome


class JudgeResponse(StrictModel):
    text: str = Field(strict=True)
    resolved_model_version: str = Field(
        min_length=1,
        max_length=200,
        strict=True,
    )
    token_input: int = Field(default=0, ge=0, strict=True)
    token_output: int = Field(default=0, ge=0, strict=True)
    cost: float = Field(
        default=0.0,
        ge=0,
        allow_inf_nan=False,
        strict=True,
    )
    latency_ms: int = Field(default=0, ge=0, strict=True)
    input_fingerprint: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        strict=True,
    )
    output_fingerprint: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        strict=True,
    )

    @field_validator("cost", mode="before")
    @classmethod
    def require_exact_float(cls, value: Any) -> Any:
        if type(value) is not float:
            raise ValueError("cost must be a float")
        return value


class _BudgetState:
    __slots__ = ("_lock", "_used")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._used = 0

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    def consume(self, maximum: int) -> bool:
        with self._lock:
            if self._used >= maximum:
                return False
            self._used += 1
            return True


@dataclass(frozen=True, slots=True, kw_only=True)
class JudgeCallContext:
    request_id: str
    evaluation_id: str
    run_ref: str
    metric_id: str
    metric_version: str
    projection_sha256: str
    max_subcalls: int
    fresh_until_monotonic: float
    deadline_monotonic: float
    _budget: _BudgetState = field(
        default_factory=_BudgetState,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        identity_checks = (
            (
                type(self.request_id) is str
                and _REQUEST_ID_PATTERN.fullmatch(self.request_id)
            ),
            (
                type(self.evaluation_id) is str
                and _EVALUATION_ID_PATTERN.fullmatch(self.evaluation_id)
            ),
            (
                type(self.run_ref) is str
                and _RUN_REF_PATTERN.fullmatch(self.run_ref)
            ),
            (
                type(self.metric_id) is str
                and _METRIC_ID_PATTERN.fullmatch(self.metric_id)
            ),
            (
                type(self.metric_version) is str
                and _METRIC_VERSION_PATTERN.fullmatch(self.metric_version)
            ),
            (
                type(self.projection_sha256) is str
                and _SHA256_PATTERN.fullmatch(self.projection_sha256)
            ),
        )
        if not all(identity_checks):
            raise JudgeError("context_invalid")
        if type(self.max_subcalls) is not int or self.max_subcalls < 0:
            raise JudgeError("context_invalid")
        for value in (
            self.fresh_until_monotonic,
            self.deadline_monotonic,
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise JudgeError("context_invalid")

    @property
    def subcalls_used(self) -> int:
        return self._budget.used

    def _consume_subcall(self) -> None:
        if not self._budget.consume(self.max_subcalls):
            raise JudgeError("subcall_budget_exceeded")


_CURRENT_JUDGE_CONTEXT: ContextVar[JudgeCallContext | None] = ContextVar(
    "deepeval_advisory_judge_context",
    default=None,
)


def _require_fresh_context(context: JudgeCallContext) -> None:
    now = time.monotonic()
    if now >= context.deadline_monotonic:
        raise JudgeError("context_expired")
    if now >= context.fresh_until_monotonic:
        raise JudgeError("context_stale")


def set_judge_context(context: JudgeCallContext) -> Token:
    if not isinstance(context, JudgeCallContext):
        raise JudgeError("context_invalid")
    return _CURRENT_JUDGE_CONTEXT.set(context)


def reset_judge_context(token: Token) -> None:
    _CURRENT_JUDGE_CONTEXT.reset(token)


@contextmanager
def judge_context(context: JudgeCallContext) -> Iterator[JudgeCallContext]:
    token = set_judge_context(context)
    try:
        yield context
    finally:
        reset_judge_context(token)


def require_judge_context() -> JudgeCallContext:
    context = _CURRENT_JUDGE_CONTEXT.get()
    if context is None:
        raise JudgeError("context_absent")
    _require_fresh_context(context)
    return context


def validate_judge_prompt(
    prompt: str,
    context: JudgeCallContext | None = None,
    *,
    response_schema: dict[str, Any] | None = None,
) -> str:
    active_context = require_judge_context()
    if context is not None and context is not active_context:
        raise JudgeError("context_mismatch")
    if type(prompt) is not str:
        raise JudgeError("prompt_invalid")

    try:
        assert_safe_outbound_text(prompt)
        logical_request = canonical_json_bytes(
            {
                "prompt": prompt,
                "response_schema": response_schema,
            }
        )
    except BoundaryViolation:
        raise JudgeError("prompt_boundary_violation") from None
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError):
        raise JudgeError("request_invalid") from None

    if len(logical_request) > _MAX_REQUEST_BYTES:
        raise JudgeError("request_too_large")

    prompt_sha256 = text_sha256(prompt)
    active_context._consume_subcall()
    _log_judge_call(
        active_context,
        prompt_sha256=prompt_sha256,
        status="validated",
        attempt=active_context.subcalls_used,
    )
    return prompt


class JudgeTransport(Protocol):
    """Deadline-aware Judge transport boundary.

    Every transport receives ``context.deadline_monotonic``. A synchronous
    implementation must stop or return by that deadline when possible; the
    adapter rejects any late synchronous response. Async calls are additionally
    bounded by the adapter using the remaining monotonic deadline.
    """

    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        raise NotImplementedError()

    async def acomplete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        raise NotImplementedError()


def _bounded_fake_configuration_error() -> JudgeError:
    return JudgeError("fake_configuration_invalid")


def _validate_fake_key(key: str | None) -> str | None:
    if key is None:
        return None
    if type(key) is not str or not _SCHEMA_NAME_PATTERN.fullmatch(key):
        raise _bounded_fake_configuration_error()
    return key


def _render_fake_response(key: str | None, value: object) -> str:
    if key is None:
        if type(value) is not str:
            raise _bounded_fake_configuration_error()
        return value
    if type(value) is str:
        return value
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError):
        raise _bounded_fake_configuration_error() from None


def _fake_schema_key(
    response_schema: dict[str, Any] | None,
) -> str | None:
    if response_schema is None:
        return None
    if not isinstance(response_schema, Mapping):
        raise JudgeError("fake_schema_unconfigured")
    for candidate_key in ("title", "name"):
        candidate = response_schema.get(candidate_key)
        if (
            type(candidate) is str
            and _SCHEMA_NAME_PATTERN.fullmatch(candidate)
        ):
            return candidate
    raise JudgeError("fake_schema_unconfigured")


class FakeJudgeTransport:
    def __init__(
        self,
        *,
        profile_version: str,
        responses: Mapping[str | None, object],
        failures: Mapping[
            str | None,
            Sequence[BaseException],
        ]
        | None = None,
    ) -> None:
        if (
            type(profile_version) is not str
            or not profile_version.strip()
            or len(profile_version) > 200
        ):
            raise _bounded_fake_configuration_error()
        if not isinstance(responses, Mapping):
            raise _bounded_fake_configuration_error()
        if failures is not None and not isinstance(failures, Mapping):
            raise _bounded_fake_configuration_error()

        rendered_responses: dict[str | None, str] = {}
        for raw_key, value in responses.items():
            key = _validate_fake_key(raw_key)
            rendered_responses[key] = _render_fake_response(key, value)

        failure_queues: dict[
            str | None,
            deque[Exception],
        ] = {}
        for raw_key, sequence in (failures or {}).items():
            key = _validate_fake_key(raw_key)
            if (
                not isinstance(sequence, Sequence)
                or isinstance(sequence, (str, bytes, bytearray))
                or any(
                    not isinstance(item, Exception)
                    for item in sequence
                )
            ):
                raise _bounded_fake_configuration_error()
            failure_queues[key] = deque(sequence)

        self._profile_version = profile_version
        self._responses = rendered_responses
        self._failures = failure_queues
        self._lock = threading.Lock()
        self._sync_call_count = 0
        self._async_call_count = 0

    @property
    def sync_call_count(self) -> int:
        with self._lock:
            return self._sync_call_count

    @property
    def async_call_count(self) -> int:
        with self._lock:
            return self._async_call_count

    @property
    def call_count(self) -> int:
        with self._lock:
            return self._sync_call_count + self._async_call_count

    def _execute(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any] | None,
        context: JudgeCallContext,
        is_async: bool,
    ) -> JudgeResponse:
        if type(prompt) is not str or not isinstance(
            context,
            JudgeCallContext,
        ):
            raise JudgeError("fake_call_invalid")
        key = _fake_schema_key(response_schema)

        with self._lock:
            if is_async:
                self._async_call_count += 1
            else:
                self._sync_call_count += 1
            failure_queue = self._failures.get(key)
            failure = (
                failure_queue.popleft()
                if failure_queue
                else None
            )
            response_text = self._responses.get(key)
            configured = key in self._responses

        if failure is not None:
            raise failure
        if not configured or response_text is None:
            raise JudgeError("fake_schema_unconfigured")

        return JudgeResponse(
            text=response_text,
            resolved_model_version=self._profile_version,
            token_input=len(prompt.encode("utf-8")),
            token_output=len(response_text.encode("utf-8")),
            cost=0.0,
            latency_ms=0,
            input_fingerprint=text_sha256(prompt),
            output_fingerprint=text_sha256(response_text),
        )

    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        return self._execute(
            prompt=prompt,
            response_schema=response_schema,
            context=context,
            is_async=False,
        )

    async def acomplete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        await asyncio.sleep(0)
        return self._execute(
            prompt=prompt,
            response_schema=response_schema,
            context=context,
            is_async=True,
        )


def _require_schema_class(schema: Any) -> type[BaseModel]:
    if (
        not isinstance(schema, type)
        or not issubclass(schema, BaseModel)
    ):
        raise JudgeError("response_schema_invalid")
    return schema


def _response_schema_for(
    schema: type[BaseModel] | None,
) -> dict[str, Any] | None:
    if schema is None:
        return None
    schema_class = _require_schema_class(schema)
    try:
        response_schema = schema_class.model_json_schema()
    except Exception:
        raise JudgeError("response_schema_invalid") from None
    if not isinstance(response_schema, dict):
        raise JudgeError("response_schema_invalid")
    return response_schema


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def parse_judge_response(
    response: JudgeResponse,
    schema: type[BaseModel] | None,
    *,
    expected_model_version: str | None = None,
) -> str | BaseModel:
    if not isinstance(response, JudgeResponse):
        raise JudgeError(
            "unavailable",
            outcome="unknown",
        )
    if (
        expected_model_version is not None
        and response.resolved_model_version != expected_model_version
    ):
        raise JudgeError(
            "model_version_mismatch",
            outcome="completed",
        )
    if schema is None:
        return response.text

    try:
        schema_class = _require_schema_class(schema)
        decoded = json.loads(
            response.text,
            parse_constant=_reject_json_constant,
        )
        return schema_class.model_validate(decoded, strict=True)
    except JudgeError:
        raise
    except (
        json.JSONDecodeError,
        ValidationError,
        TypeError,
        ValueError,
        RecursionError,
    ):
        raise JudgeError(
            "response_schema_invalid",
            retryable=False,
            outcome="completed",
        ) from None


def _verify_response_integrity(
    response: JudgeResponse,
    guarded_prompt: str,
) -> None:
    if not isinstance(response, JudgeResponse):
        raise JudgeError(
            "unavailable",
            outcome="unknown",
        )
    try:
        input_fingerprint = text_sha256(guarded_prompt)
        output_fingerprint = text_sha256(response.text)
    except UnicodeEncodeError:
        raise JudgeError(
            "response_integrity_invalid",
            outcome="completed",
        ) from None
    if not (
        hmac.compare_digest(
            response.input_fingerprint,
            input_fingerprint,
        )
        and hmac.compare_digest(
            response.output_fingerprint,
            output_fingerprint,
        )
    ):
        raise JudgeError(
            "response_integrity_invalid",
            retryable=False,
            outcome="completed",
        )


def _require_context_after_response(
    expected_context: JudgeCallContext,
) -> None:
    try:
        active_context = require_judge_context()
    except JudgeError as error:
        category = (
            "deadline_exceeded"
            if error.category == "context_expired"
            else "context_invalid_after_response"
        )
        raise JudgeError(
            category,
            retryable=False,
            outcome="unknown",
        ) from None
    if active_context is not expected_context:
        raise JudgeError(
            "context_invalid_after_response",
            retryable=False,
            outcome="unknown",
        )


def _log_judge_call(
    context: JudgeCallContext,
    *,
    prompt_sha256: str,
    status: str,
    attempt: int,
) -> None:
    _LOGGER.info(
        "judge_call request_id=%s evaluation_id=%s run_ref=%s "
        "metric_id=%s metric_version=%s projection_sha256=%s "
        "prompt_sha256=%s status=%s attempt=%d",
        context.request_id,
        context.evaluation_id,
        context.run_ref,
        context.metric_id,
        context.metric_version,
        context.projection_sha256,
        prompt_sha256,
        status,
        attempt,
    )


def _may_retry(error: JudgeError, attempt: int) -> bool:
    return (
        attempt < _MAX_ATTEMPTS
        and error.category in _RETRYABLE_CATEGORIES
        and error.retryable is True
        and error.outcome == "not_started"
    )


class DeepEvalJudgeLLM(DeepEvalBaseLLM):
    def __init__(
        self,
        transport: JudgeTransport,
        *,
        profile_version: str,
        adapter_version: str = "8099-judge-adapter-v1",
    ) -> None:
        if (
            type(profile_version) is not str
            or not profile_version.strip()
            or type(adapter_version) is not str
            or not adapter_version.strip()
        ):
            raise JudgeError("configuration_invalid")
        self._transport = transport
        self._profile_version = profile_version
        self._adapter_version = adapter_version
        super().__init__(model=profile_version)

    @property
    def profile_version(self) -> str:
        return self._profile_version

    @property
    def adapter_version(self) -> str:
        return self._adapter_version

    def load_model(self) -> JudgeTransport:
        return self._transport

    def get_model_name(self) -> str:
        return self._profile_version

    def generate(
        self,
        prompt: str,
        schema: type[BaseModel] | None = None,
    ) -> str | BaseModel:
        context = require_judge_context()
        response_schema = _response_schema_for(schema)

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            guarded_prompt = validate_judge_prompt(
                prompt,
                context,
                response_schema=response_schema,
            )
            prompt_sha256 = text_sha256(guarded_prompt)
            _log_judge_call(
                context,
                prompt_sha256=prompt_sha256,
                status="attempt",
                attempt=attempt,
            )
            try:
                response = self._transport.complete(
                    prompt=guarded_prompt,
                    response_schema=response_schema,
                    context=context,
                )
                _require_context_after_response(context)
                _verify_response_integrity(response, guarded_prompt)
                result = parse_judge_response(
                    response,
                    schema,
                    expected_model_version=self._profile_version,
                )
            except JudgeError as error:
                if _may_retry(error, attempt):
                    _log_judge_call(
                        context,
                        prompt_sha256=prompt_sha256,
                        status="retry",
                        attempt=attempt,
                    )
                    continue
                _log_judge_call(
                    context,
                    prompt_sha256=prompt_sha256,
                    status="failed",
                    attempt=attempt,
                )
                raise
            except Exception:
                _log_judge_call(
                    context,
                    prompt_sha256=prompt_sha256,
                    status="unavailable",
                    attempt=attempt,
                )
                raise JudgeError(
                    "unavailable",
                    outcome="unknown",
                ) from None

            _log_judge_call(
                context,
                prompt_sha256=prompt_sha256,
                status="completed",
                attempt=attempt,
            )
            return result

        raise JudgeError("unavailable", outcome="unknown")

    async def a_generate(
        self,
        prompt: str,
        schema: type[BaseModel] | None = None,
    ) -> str | BaseModel:
        context = require_judge_context()
        response_schema = _response_schema_for(schema)

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            guarded_prompt = validate_judge_prompt(
                prompt,
                context,
                response_schema=response_schema,
            )
            prompt_sha256 = text_sha256(guarded_prompt)
            _log_judge_call(
                context,
                prompt_sha256=prompt_sha256,
                status="attempt",
                attempt=attempt,
            )
            try:
                remaining_seconds = (
                    context.deadline_monotonic - time.monotonic()
                )
                if remaining_seconds <= 0:
                    raise JudgeError("context_expired")
                try:
                    response = await asyncio.wait_for(
                        self._transport.acomplete(
                            prompt=guarded_prompt,
                            response_schema=response_schema,
                            context=context,
                        ),
                        timeout=remaining_seconds,
                    )
                except TimeoutError:
                    if time.monotonic() < context.deadline_monotonic:
                        raise
                    raise JudgeError(
                        "deadline_exceeded",
                        retryable=False,
                        outcome="unknown",
                    ) from None
                _require_context_after_response(context)
                _verify_response_integrity(response, guarded_prompt)
                result = parse_judge_response(
                    response,
                    schema,
                    expected_model_version=self._profile_version,
                )
            except JudgeError as error:
                if _may_retry(error, attempt):
                    _log_judge_call(
                        context,
                        prompt_sha256=prompt_sha256,
                        status="retry",
                        attempt=attempt,
                    )
                    continue
                _log_judge_call(
                    context,
                    prompt_sha256=prompt_sha256,
                    status="failed",
                    attempt=attempt,
                )
                raise
            except Exception:
                _log_judge_call(
                    context,
                    prompt_sha256=prompt_sha256,
                    status="unavailable",
                    attempt=attempt,
                )
                raise JudgeError(
                    "unavailable",
                    outcome="unknown",
                ) from None

            _log_judge_call(
                context,
                prompt_sha256=prompt_sha256,
                status="completed",
                attempt=attempt,
            )
            return result

        raise JudgeError("unavailable", outcome="unknown")

    generate._is_deepeval_observed = True
    a_generate._is_deepeval_observed = True
