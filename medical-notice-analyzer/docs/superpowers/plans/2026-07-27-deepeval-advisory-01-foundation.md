# DeepEval Advisory Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立不接触真实 Judge、生产数据或运行服务的 DeepEval Advisory 基础层，包括严格 Schema、数据投影、安全哈希、持久任务、精确缓存、fake Judge 和 DeepEval 4.1.3 指标执行。

**Architecture:** 新代码全部位于 `app/deepeval_advisory/`，只复用轻量的 run migration、正式正文、Evidence Schema 和 Claim/A-B matcher，不导入 `app.main`。所有测试使用合成 fixture；DeepEval 依赖合约在 Python 3.11 Worker 容器内验证，主服务依赖保持不变。

**Tech Stack:** Python 3.11、Pydantic 2、DeepEval 4.1.3、HTTPX、portalocker、`unittest`、Docker。

---

## 边界

本计划完成后：

- 可以从合成 analysis run 和 Evidence Pack 生成最小 A/B Projection；
- 可以持久化 job、result、cache 和 coverage；
- 可以用 fake Judge 跑完整 DeepEval metric set；
- 不能连接真实 Judge；
- 不扫描项目 `data/`；
- 不启动 Runtime Worker；
- 不修改 `app/main.py`、`requirements.txt`、主 Dockerfile 或现有 Compose。

若实现需要导入 `app.main`，停止并重构为轻量依赖。

## 文件图

```text
Create: app/deepeval_advisory/__init__.py
Create: app/deepeval_advisory/models.py
Create: app/deepeval_advisory/settings.py
Create: app/deepeval_advisory/hashing.py
Create: app/deepeval_advisory/projection.py
Create: app/deepeval_advisory/store.py
Create: app/deepeval_advisory/cache.py
Create: app/deepeval_advisory/judge.py
Create: app/deepeval_advisory/evaluator.py
Create: requirements-deepeval.in
Create: requirements-deepeval.lock
Create: Dockerfile.deepeval
Modify: .dockerignore
Create: tests/fixtures/deepeval_advisory/unit/run_finished.json
Create: tests/fixtures/deepeval_advisory/unit/run_manual_review.json
Create: tests/fixtures/deepeval_advisory/unit/evidence_pack_v2.json
Create: tests/test_deepeval_advisory_models.py
Create: tests/test_deepeval_advisory_projection.py
Create: tests/test_deepeval_advisory_store.py
Create: tests/test_deepeval_advisory_judge.py
Create: tests/test_deepeval_advisory_evaluator.py
Create: tests/test_deepeval_advisory_deepeval_contract.py
```

### Task 1: Lock the isolated DeepEval 4.1.3 environment

**Files:**

- Create: `requirements-deepeval.in`
- Create: `requirements-deepeval.lock`
- Create: `Dockerfile.deepeval`
- Modify: `.dockerignore`
- Create: `tests/test_deepeval_advisory_deepeval_contract.py`

- [ ] **Step 1: Write the dependency contract test**

Create `tests/test_deepeval_advisory_deepeval_contract.py`:

```python
from __future__ import annotations

import inspect
import unittest

import deepeval
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, GEval
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, SingleTurnParams


class DeepEval413ContractTests(unittest.TestCase):
    def test_exact_version_is_loaded(self) -> None:
        self.assertEqual(deepeval.__version__, "4.1.3")

    def test_custom_llm_schema_helpers_exist(self) -> None:
        self.assertTrue(hasattr(DeepEvalBaseLLM, "generate_with_schema"))
        self.assertTrue(hasattr(DeepEvalBaseLLM, "a_generate_with_schema"))

    def test_metric_constructor_parameters_are_present(self) -> None:
        faithfulness = inspect.signature(FaithfulnessMetric.__init__).parameters
        relevancy = inspect.signature(AnswerRelevancyMetric.__init__).parameters
        geval = inspect.signature(GEval.__init__).parameters
        self.assertTrue({"model", "include_reason", "async_mode"} <= set(faithfulness))
        self.assertTrue({"model", "include_reason", "async_mode"} <= set(relevancy))
        self.assertTrue(
            {
                "name",
                "evaluation_params",
                "evaluation_steps",
                "rubric",
                "model",
                "async_mode",
            }
            <= set(geval)
        )

    def test_llm_test_case_accepts_required_fields(self) -> None:
        case = LLMTestCase(
            input="核验当前事实",
            actual_output="采购周期为两年。",
            expected_output="采购周期为两年。",
            retrieval_context=["项目服务期限为两年。"],
            metadata={"case_ref": "case_test"},
        )
        self.assertEqual(case.input, "核验当前事实")
        self.assertEqual(SingleTurnParams.ACTUAL_OUTPUT.value, "actual_output")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test before creating the Worker environment**

Run from `medical-notice-analyzer`:

```powershell
docker run --rm --network none `
  -v "${PWD}:/work:ro" `
  -w /work `
  python:3.11-slim `
  python -m unittest tests.test_deepeval_advisory_deepeval_contract -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'deepeval'`. This
proves the red test without running the project or a business test on the host.

- [ ] **Step 3: Create the direct dependency input**

Create `requirements-deepeval.in`:

```text
deepeval==4.1.3
httpx==0.28.1
portalocker==3.2.0
pydantic==2.11.7
pydantic-settings==2.10.1
PyYAML==6.0.2
```

Do not add `-r requirements.txt`; the main file pins incompatible `pydantic==2.10.4`.

- [ ] **Step 4: Generate a Python 3.11 hash lock**

Run:

```powershell
docker run --rm `
  -v "${PWD}:/work" `
  -w /work `
  python:3.11-slim `
  sh -lc "python -m pip install --disable-pip-version-check pip-tools==7.6.0 && python -m piptools compile --resolver=backtracking --generate-hashes --strip-extras --output-file requirements-deepeval.lock requirements-deepeval.in"
```

Expected:

- `requirements-deepeval.lock` is created;
- it contains `deepeval==4.1.3`;
- it contains `pydantic==2.11.7`;
- every resolved package line contains hashes.

- [ ] **Step 5: Protect the build context and create the test image**

Before the first build, add these rules to `.dockerignore`:

```text
data
data/**
reports
reports/**
site-cache
artifacts
artifacts/**
secrets
secrets/**
deepeval-advisory-data
*.docx
```

Create the dependency-only first version of `Dockerfile.deepeval`:

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEEPEVAL_TELEMETRY_OPT_OUT=1
ENV DEEPEVAL_DISABLE_DOTENV=1
ENV DEEPEVAL_NO_INSPECT_PROMPT=1
ENV ENABLE_DEEPEVAL_CACHE=0

RUN groupadd --gid 10001 advisory \
    && useradd --uid 10001 --gid advisory --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin advisory \
    && install -d -o 10001 -g 10001 -m 0700 /state

COPY requirements-deepeval.lock /opt/advisory/requirements-deepeval.lock
RUN python -m pip install --disable-pip-version-check --no-cache-dir \
    --require-hashes -r /opt/advisory/requirements-deepeval.lock

WORKDIR /work
USER 10001:10001
ENTRYPOINT ["python"]
```

Build it:

```powershell
docker build -f Dockerfile.deepeval `
  -t medical-notice-analyzer-deepeval:foundation-test .
```

- [ ] **Step 6: Run the contract test in the network-disabled test image**

```powershell
docker run --rm --network none `
  -v "${PWD}:/work:ro" `
  -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_deepeval_contract -v
```

Expected: 4 tests PASS.

Tasks 2–7 must use this exact image pattern with `--network none` and the
repository mounted read-only. Do not run project or business tests directly on
the host.

- [ ] **Step 7: Verify the main dependency file did not change**

Run:

```powershell
git diff -- requirements.txt
```

Expected: no output.

- [ ] **Step 8: Commit**

```powershell
git add requirements-deepeval.in requirements-deepeval.lock Dockerfile.deepeval .dockerignore tests/test_deepeval_advisory_deepeval_contract.py
git commit -m "build: lock isolated DeepEval advisory dependencies"
```

### Task 2: Define strict Advisory models and settings

**Files:**

- Create: `app/deepeval_advisory/__init__.py`
- Create: `app/deepeval_advisory/models.py`
- Create: `app/deepeval_advisory/settings.py`
- Create: `tests/test_deepeval_advisory_models.py`

- [ ] **Step 1: Write failing model tests**

Create `tests/test_deepeval_advisory_models.py` with these tests:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.deepeval_advisory.models import (
    AdvisoryJob,
    AdvisoryResult,
    JobStatus,
    MetricObservation,
    MetricStatus,
)
from app.deepeval_advisory.settings import AdvisorySettings, SettingsError


class AdvisoryModelTests(unittest.TestCase):
    def test_result_is_permanently_non_blocking(self) -> None:
        result = AdvisoryResult(
            evaluation_id="eval_12345678",
            job_id="job_12345678",
            run_ref="runref_12345678",
            advisory_input_sha256="a" * 64,
            metric_set_sha256="b" * 64,
            status=JobStatus.COMPLETED,
            metrics=(
                MetricObservation(
                    metric_id="claim_faithfulness_v1",
                    metric_version="1",
                    status=MetricStatus.SCORED,
                    score=0.9,
                ),
            ),
        )
        self.assertFalse(result.blocking)
        self.assertFalse(result.affects_deliverable)
        self.assertFalse(result.affects_report_status)
        self.assertFalse(result.affects_release)
        self.assertFalse(result.affects_user_response)
        self.assertNotIn("passed", result.model_dump())
        self.assertNotIn("threshold", result.model_dump())

    def test_result_rejects_blocking_override_and_unknown_fields(self) -> None:
        payload = {
            "evaluation_id": "eval_12345678",
            "job_id": "job_12345678",
            "run_ref": "runref_12345678",
            "advisory_input_sha256": "a" * 64,
            "metric_set_sha256": "b" * 64,
            "status": "completed",
            "metrics": [],
            "blocking": True,
            "release_decision": "deny",
        }
        with self.assertRaises(ValidationError):
            AdvisoryResult.model_validate(payload)

    def test_job_rejects_invalid_transition_input(self) -> None:
        with self.assertRaises(ValidationError):
            AdvisoryJob(
                job_id="job_12345678",
                run_ref="runref_12345678",
                report_version=0,
                report_sha256="x",
                projection_sha256="y",
                advisory_input_sha256="z",
                status=JobStatus.PENDING,
            )

    def test_settings_default_both_execution_flags_to_false(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = AdvisorySettings.from_mapping(
                {
                    "ANALYSIS_RUN_DIR": str(root / "runs"),
                    "EVIDENCE_PACK_DIR": str(root / "packs"),
                    "DEEPEVAL_ADVISORY_DIR": str(root / "advisory"),
                }
            )
        self.assertFalse(settings.runtime_advisory_enabled)
        self.assertFalse(settings.scheduled_evaluation_enabled)
        self.assertEqual(settings.worker_concurrency, 1)

    def test_settings_reject_ambiguous_boolean(self) -> None:
        with self.assertRaises(SettingsError):
            AdvisorySettings.from_mapping(
                {
                    "ANALYSIS_RUN_DIR": "runs",
                    "EVIDENCE_PACK_DIR": "packs",
                    "DEEPEVAL_ADVISORY_DIR": "advisory",
                    "DEEPEVAL_RUNTIME_ADVISORY_ENABLED": "enabled",
                }
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
docker run --rm --network none `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_models -v
```

Expected: FAIL because `app.deepeval_advisory` does not exist.

- [ ] **Step 3: Implement the strict models**

Create an empty `app/deepeval_advisory/__init__.py`.

Create `app/deepeval_advisory/models.py` with these public contracts:

```python
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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


class MetricObservation(StrictModel):
    metric_id: str = Field(min_length=1, max_length=80)
    metric_version: str = Field(min_length=1, max_length=40)
    status: MetricStatus
    score: float | None = Field(default=None, ge=0, le=1)
    bounded_reason: str | None = Field(default=None, max_length=500)
    evidence_references: tuple[str, ...] = ()
    unsupported_spans: tuple[tuple[int, int], ...] = ()
    error_category: str | None = None


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
```

These are the exact persisted v1 fields. Adding a field requires a schema-version or
approved design amendment. No decision, threshold, pass/fail, deliverable,
report-status or release-override field is permitted.

- [ ] **Step 4: Implement strict settings**

Create `app/deepeval_advisory/settings.py`:

```python
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class SettingsError(ValueError):
    """Raised when Advisory configuration is invalid."""


def _bool(mapping: Mapping[str, str], name: str, default: bool) -> bool:
    raw = str(mapping.get(name, "")).strip().lower()
    if not raw:
        return default
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise SettingsError(f"{name} must be true or false")


def _int(
    mapping: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = str(mapping.get(name, "")).strip()
    try:
        value = default if not raw else int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise SettingsError(f"{name} must be between {minimum} and {maximum}")
    return value


class AdvisorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_run_dir: Path
    evidence_pack_dir: Path
    advisory_dir: Path
    runtime_advisory_enabled: bool = False
    scheduled_evaluation_enabled: bool = False
    scan_interval_seconds: int = Field(default=30, ge=5, le=3600)
    worker_concurrency: int = Field(default=1, ge=1, le=4)
    lease_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    lease_heartbeat_seconds: int = Field(default=30, ge=5, le=300)
    max_run_bytes: int = Field(default=4 * 1024 * 1024, ge=1024)
    max_pack_bytes: int = Field(default=32 * 1024 * 1024, ge=1024)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "AdvisorySettings":
        required = (
            "ANALYSIS_RUN_DIR",
            "EVIDENCE_PACK_DIR",
            "DEEPEVAL_ADVISORY_DIR",
        )
        missing = [name for name in required if not str(mapping.get(name, "")).strip()]
        if missing:
            raise SettingsError(f"missing settings: {','.join(missing)}")
        return cls(
            analysis_run_dir=Path(mapping["ANALYSIS_RUN_DIR"]),
            evidence_pack_dir=Path(mapping["EVIDENCE_PACK_DIR"]),
            advisory_dir=Path(mapping["DEEPEVAL_ADVISORY_DIR"]),
            runtime_advisory_enabled=_bool(
                mapping, "DEEPEVAL_RUNTIME_ADVISORY_ENABLED", False
            ),
            scheduled_evaluation_enabled=_bool(
                mapping, "DEEPEVAL_SCHEDULED_EVALUATION_ENABLED", False
            ),
            scan_interval_seconds=_int(
                mapping, "DEEPEVAL_SCAN_INTERVAL_SECONDS", 30, 5, 3600
            ),
            worker_concurrency=_int(
                mapping, "DEEPEVAL_WORKER_CONCURRENCY", 1, 1, 4
            ),
            lease_ttl_seconds=_int(
                mapping, "DEEPEVAL_LEASE_TTL_SECONDS", 900, 60, 3600
            ),
            lease_heartbeat_seconds=_int(
                mapping, "DEEPEVAL_LEASE_HEARTBEAT_SECONDS", 30, 5, 300
            ),
        )
```

- [ ] **Step 5: Run tests**

Run:

```powershell
docker run --rm --network none `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_models -v
```

Expected: 5 tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/deepeval_advisory/__init__.py app/deepeval_advisory/models.py app/deepeval_advisory/settings.py tests/test_deepeval_advisory_models.py
git commit -m "feat: define advisory contracts and settings"
```

### Task 3: Add canonical hashing and the egress boundary guard

**Files:**

- Create: `app/deepeval_advisory/hashing.py`
- Modify: `app/deepeval_advisory/models.py`
- Create: `tests/test_deepeval_advisory_projection.py`

- [ ] **Step 1: Write failing hash and boundary tests**

Start `tests/test_deepeval_advisory_projection.py` with:

```python
from __future__ import annotations

import unittest

from app.deepeval_advisory.hashing import (
    BoundaryViolation,
    advisory_input_sha256,
    assert_safe_outbound_text,
    canonical_sha256,
    hmac_reference,
)


class AdvisoryHashingTests(unittest.TestCase):
    def test_canonical_hash_ignores_dictionary_order(self) -> None:
        self.assertEqual(
            canonical_sha256({"b": 2, "a": 1}),
            canonical_sha256({"a": 1, "b": 2}),
        )

    def test_hmac_reference_does_not_expose_source_identifier(self) -> None:
        ref = hmac_reference(b"k" * 32, "run", "run_internal_123")
        self.assertRegex(ref, r"^runref_[0-9a-f]{32}$")
        self.assertNotIn("internal", ref)

    def test_cache_key_changes_for_each_versioned_input(self) -> None:
        base = {
            "report_sha256": "a" * 64,
            "evidence_projection_sha256": "b" * 64,
            "metric_set_sha256": "c" * 64,
            "deepeval_version": "4.1.3",
            "adapter_version": "adapter-v1",
            "judge_provider": "fake",
            "immutable_judge_model_or_profile_version": "fake-v1",
            "prompt_version": "prompt-v1",
            "response_schema_version": "result-v1",
            "safety_preamble_version": "safety-v1",
            "locale": "zh-CN",
        }
        first = advisory_input_sha256(**base)
        second = advisory_input_sha256(**{**base, "prompt_version": "prompt-v2"})
        self.assertNotEqual(first, second)

    def test_guard_rejects_secrets_paths_and_internal_urls(self) -> None:
        blocked = (
            "Authorization: Bearer secret",
            r"C:\private\report.json",
            "/app/data/evidence_packs/pack.json",
            "http://192.168.34.87/internal",
        )
        for value in blocked:
            with self.subTest(value=value), self.assertRaises(BoundaryViolation):
                assert_safe_outbound_text(value)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
docker run --rm --network none `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_projection.AdvisoryHashingTests -v
```

Expected: FAIL because `hashing.py` does not exist.

- [ ] **Step 3: Implement hashing and outbound checks**

Create `app/deepeval_advisory/hashing.py`:

```python
from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any


class BoundaryViolation(ValueError):
    """Raised when outbound Judge material crosses the approved boundary."""


_FORBIDDEN_TEXT = (
    re.compile(r"(?i)\b(?:authorization|cookie|api[_ -]?key|password|dsn)\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+"),
    re.compile(r"(?i)\b[a-z]:\\"),
    re.compile(r"(?:^|\s)/(?:app|opt|var|home|root|data)/"),
    re.compile(
        r"(?i)https?://(?:localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|"
        r"192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)"
    ),
)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def text_sha256(value: str) -> str:
    normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def hmac_reference(key: bytes, namespace: str, source_id: str) -> str:
    if len(key) < 32:
        raise ValueError("HMAC key must contain at least 32 bytes")
    digest = hmac.new(
        key,
        f"{namespace}\0{source_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    prefix = {"run": "runref", "pack": "packref"}.get(namespace, "ref")
    return f"{prefix}_{digest}"


def assert_safe_outbound_text(value: str) -> None:
    text = str(value or "")
    for pattern in _FORBIDDEN_TEXT:
        if pattern.search(text):
            raise BoundaryViolation("outbound text violates the safety boundary")


def advisory_input_sha256(
    *,
    report_sha256: str,
    evidence_projection_sha256: str,
    metric_set_sha256: str,
    deepeval_version: str,
    adapter_version: str,
    judge_provider: str,
    immutable_judge_model_or_profile_version: str,
    prompt_version: str,
    response_schema_version: str,
    safety_preamble_version: str,
    locale: str,
) -> str:
    return canonical_sha256(
        {
            "report_sha256": report_sha256,
            "evidence_projection_sha256": evidence_projection_sha256,
            "metric_set_sha256": metric_set_sha256,
            "deepeval_version": deepeval_version,
            "adapter_version": adapter_version,
            "judge_provider": judge_provider,
            "immutable_judge_model_or_profile_version": (
                immutable_judge_model_or_profile_version
            ),
            "prompt_version": prompt_version,
            "response_schema_version": response_schema_version,
            "safety_preamble_version": safety_preamble_version,
            "locale": locale,
        }
    )
```

- [ ] **Step 4: Run tests**

```powershell
docker run --rm --network none `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_projection.AdvisoryHashingTests -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/deepeval_advisory/hashing.py tests/test_deepeval_advisory_projection.py
git commit -m "feat: add advisory hashes and boundary guard"
```

### Task 4: Build deterministic run and A/B projection

**Files:**

- Create: `app/deepeval_advisory/projection.py`
- Create: `tests/fixtures/deepeval_advisory/unit/run_finished.json`
- Create: `tests/fixtures/deepeval_advisory/unit/run_manual_review.json`
- Create: `tests/fixtures/deepeval_advisory/unit/evidence_pack_v2.json`
- Modify: `tests/test_deepeval_advisory_projection.py`

- [ ] **Step 1: Add failing eligibility and projection tests**

Append tests that construct fixtures with:

```python
run_finished = {
    "schema_version": 1,
    "run_id": "run_20260727_12345678",
    "pack_id": "pack_20260727_12345678",
    "status": "finished",
    "run_status": "finished",
    "version": 1,
    "report_markdown": "## 采购规则\n\n项目服务期限为两年。",
    "report_ir": None,
}

run_manual_review = {
    **run_finished,
    "run_id": "run_20260727_abcdefgh",
    "status": "needs_manual_review",
    "run_status": "needs_manual_review",
}
```

The v2 Evidence Pack must contain:

- one A `article_text` item with a valid source hash and quote;
- one B `derived_fact` item derived from that A item;
- one C `summary` item containing a unique marker such as `C_ONLY_SECRET_MARKER`.

Add these assertions:

```python
from pathlib import Path

from app.deepeval_advisory.projection import (
    ProjectionError,
    build_projection,
    build_projection_from_values,
    build_run_snapshot_from_value,
    load_run_snapshot,
)


class AdvisoryProjectionTests(unittest.TestCase):
    def test_finished_and_manual_review_with_body_are_eligible(self) -> None:
        fixtures = Path("tests/fixtures/deepeval_advisory/unit")
        for name in ("run_finished.json", "run_manual_review.json"):
            snapshot = load_run_snapshot(fixtures / name, b"h" * 32)
            self.assertTrue(snapshot.eligible)

    def test_failed_empty_and_technical_only_runs_are_ineligible(self) -> None:
        fixtures = Path("tests/fixtures/deepeval_advisory/unit")
        for status, body in (
            ("created", "尚未完成的正文"),
            ("running", "尚未完成的正文"),
            ("failed", "正式正文"),
            ("interrupted", "正式正文"),
            ("finished", ""),
            ("needs_manual_review", "由于本次自动生成结果未形成完整正文，供人工复核"),
        ):
            with self.subTest(status=status, body=body):
                changed = build_run_snapshot_from_value(
                    {
                        **run_finished,
                        "status": status,
                        "run_status": status,
                        "report_markdown": body,
                    },
                    b"h" * 32,
                )
                self.assertFalse(changed.eligible)

    def test_projection_contains_only_a_b_and_safe_locator(self) -> None:
        fixtures = Path("tests/fixtures/deepeval_advisory/unit")
        snapshot = load_run_snapshot(fixtures / "run_finished.json", b"h" * 32)
        projection = build_projection(
            snapshot,
            fixtures / "evidence_pack_v2.json",
        )
        serialized = projection.model_dump_json()
        self.assertNotIn("C_ONLY_SECRET_MARKER", serialized)
        self.assertNotIn("menu_code", serialized)
        self.assertNotIn("articleid", serialized)
        self.assertNotIn("filename", serialized)
        self.assertTrue(
            all(
                evidence.level in {"A", "B"}
                for unit in projection.units
                for evidence in unit.evidence
            )
        )

    def test_unknown_pack_schema_and_over_budget_input_fail_closed(self) -> None:
        with self.assertRaises(ProjectionError):
            build_projection_from_values(
                run_value=run_finished,
                pack_value={"evidence_schema_version": 999, "evidence_items": []},
                hmac_key=b"h" * 32,
            )
```

Do not add a helper that serializes arbitrary run or pack dictionaries into the Projection.

- [ ] **Step 2: Run the projection tests**

```powershell
docker run --rm --network none `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_projection.AdvisoryProjectionTests -v
```

Expected: FAIL because projection functions do not exist.

- [ ] **Step 3: Implement strict file loading and eligibility**

Create `app/deepeval_advisory/projection.py` with these exact public call
contracts:

- `load_run_snapshot(path: Path, hmac_key: bytes, *, max_bytes: int = 4 * 1024 * 1024) -> RunSnapshot`
- `build_run_snapshot_from_value(run_value: dict[str, Any], hmac_key: bytes) -> RunSnapshot`
- `build_projection(snapshot: RunSnapshot, pack_path: Path, *, max_bytes: int = 32 * 1024 * 1024) -> AdvisoryProjection`
- `build_projection_from_values(*, run_value: dict[str, Any], pack_value: dict[str, Any], hmac_key: bytes) -> AdvisoryProjection`

Implement them with these exact rules:

1. Reject symlinks and non-regular files.
2. Reject files over the configured byte limit before `read_text`.
3. Decode UTF-8 and parse a JSON object.
4. Call `upgrade_run_schema`; reject future run versions.
5. Require filename/content run ID match for file-backed loads.
6. Require `status == run_status`; do not choose one when they disagree.
7. Derive body presence with `FormalBodyDocument`, not the persisted boolean.
8. Mark every nonterminal status ineligible with `not_ready`; exclude `failed`,
   `interrupted`, empty bodies and bodies dominated by
   `TECHNICAL_BODY_PHRASES`.
9. Hash only normalized `report_markdown` and `report_ir`.
10. Generate `run_ref` with HMAC and never put raw `run_id` in a Projection.
11. Load Evidence with `read_evidence_pack`; reject future versions.
12. Use `build_claim_evidence_index` to identify Claim/A-B matches.
13. Construct one claim-level unit per deterministic sorted Claim for v1.
14. For B evidence, include its referenced A parents in the same unit.
15. Map raw evidence IDs to `e1` through `e8`.
16. Locator output may contain only enum and positive numeric positions.
17. Prefer the validated `source_ref.quote`; use a scalar `value` only when it is at most 1,500 characters.
18. Reject instead of truncating when Claim, Evidence count, Evidence excerpt or total JSON exceeds the approved limits.
19. Calculate `projection_sha256` from the Projection payload with that field omitted.

All four functions must contain working fail-closed implementations before the
tests run. Do not leave placeholder bodies or add permissive fallback or
arbitrary dictionary serialization.

- [ ] **Step 4: Add a source immutability test**

Hash both fixture files, call `build_projection`, and assert their SHA-256 values are unchanged. This proves projection is read-only.

- [ ] **Step 5: Run projection tests**

```powershell
docker run --rm --network none `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_projection -v
```

Expected: all hashing and projection tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/deepeval_advisory/projection.py tests/test_deepeval_advisory_projection.py tests/fixtures/deepeval_advisory/unit
git commit -m "feat: project formal reports onto bounded A-B evidence"
```

### Task 5: Implement atomic job store, lease and exact success cache

**Files:**

- Create: `app/deepeval_advisory/store.py`
- Create: `app/deepeval_advisory/cache.py`
- Create: `tests/test_deepeval_advisory_store.py`

- [ ] **Step 1: Write failing store tests**

Create `tests/test_deepeval_advisory_store.py` covering:

```python
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from app.deepeval_advisory.cache import AdvisoryCache
from app.deepeval_advisory.models import AdvisoryJob, JobStatus
from app.deepeval_advisory.store import AdvisoryStore


class AdvisoryStoreTests(unittest.TestCase):
    def make_job(self) -> AdvisoryJob:
        return AdvisoryJob(
            job_id="job_12345678",
            run_ref="runref_" + "a" * 32,
            report_version=1,
            report_sha256="b" * 64,
            projection_sha256="c" * 64,
            advisory_input_sha256="d" * 64,
            status=JobStatus.PENDING,
        )

    def test_create_job_is_idempotent_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AdvisoryStore(Path(directory))
            first = store.create_job(self.make_job())
            second = store.create_job(self.make_job())
            self.assertEqual(first, second)
            self.assertEqual(len(list((Path(directory) / "jobs").glob("*.json"))), 1)

    def test_two_threads_share_one_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AdvisoryStore(Path(directory))
            outcomes: list[bool] = []

            def acquire(owner: str) -> None:
                outcomes.append(
                    store.try_acquire_lease(
                        key="d" * 64,
                        owner=owner,
                        ttl_seconds=300,
                    )
                )

            threads = [
                threading.Thread(target=acquire, args=("worker-a",)),
                threading.Thread(target=acquire, args=("worker-b",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sorted(outcomes), [False, True])

    def test_only_completed_valid_result_is_cached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = AdvisoryCache(Path(directory))
            cache.write_completed("d" * 64, {"status": "completed", "value": 1})
            self.assertEqual(cache.read("d" * 64)["value"], 1)
            with self.assertRaises(ValueError):
                cache.write_completed(
                    "e" * 64,
                    {"status": "unavailable", "value": 0},
                )

    def test_coverage_rebuild_uses_latest_job_per_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AdvisoryStore(Path(directory))
            store.create_job(self.make_job())
            coverage = store.rebuild_coverage()
            self.assertEqual(coverage["eligible_reports"], 1)
            self.assertEqual(coverage["enrolled_reports"], 1)
```

Also test:

- atomic write failure leaves no visible partial JSON;
- stale pre-request lease can be reclaimed;
- stale post-`issued_at` lease becomes `indeterminate`, not automatically retried;
- a cache envelope with a mismatched integrity hash is rejected;
- `partial`, `unavailable`, `over_budget` and errors are never cached.

- [ ] **Step 2: Run tests and verify failure**

```powershell
docker run --rm --network none `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_store -v
```

Expected: FAIL because store/cache modules do not exist.

- [ ] **Step 3: Implement the worker-owned layout**

`AdvisoryStore` must create:

```text
jobs/
projections/
run-index/
results/
cache/
leases/
attempts/
coverage/
corrupt/
```

Use one internal atomic writer with:

```python
def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(value)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
```

Job creation must use exclusive create semantics. State replacement may use the atomic writer only after validating an allowed transition:

```text
discovered -> pending|paused|over_budget|unavailable
pending -> running|paused|unavailable
paused -> pending
running -> completed|cached|partial|retrying|unavailable|over_budget
retrying -> running|unavailable
```

No terminal state may transition back to running.

- [ ] **Step 4: Implement cross-process leases**

Use `portalocker.Lock` on a per-key guard file. Within the lock:

- read and strictly validate the lease;
- acquire when absent;
- reclaim only when expired and `request_phase == "not_issued"`;
- when expired after `issued_at`, write an `indeterminate` attempt and do not issue a new provider call;
- write `owner`, `acquired_at`, `expires_at`, `heartbeat_at`, `request_phase`, `evaluation_id` and `attempt_ref`;
- require owner match for heartbeat and release.

Do not use the thread-only locks from `compact_cache.py`, `run_checkpoints.py` or `analysis_history.py`.

- [ ] **Step 5: Implement immutable success cache**

The cache envelope is:

```json
{
  "schema_version": "8099.deepeval-cache/v1",
  "advisory_input_sha256": "64-lowercase-hex",
  "source_evaluation_id": "eval_identifier",
  "result_sha256": "64-lowercase-hex",
  "result": {},
  "created_at": "UTC timestamp"
}
```

Rules:

- cache key must equal the requested `advisory_input_sha256`;
- only a strictly validated `completed` result can be written;
- the result SHA must match on read;
- entries are immutable;
- a cache hit creates a per-run `cached` result pointing to the source evaluation;
- Nightly/Weekly callers pass `cache_mode="bypass"` and never call `read`.

- [ ] **Step 6: Run tests**

```powershell
docker run --rm --network none `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_store -v
```

Expected: all store, lease, recovery, cache and coverage tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add app/deepeval_advisory/store.py app/deepeval_advisory/cache.py tests/test_deepeval_advisory_store.py
git commit -m "feat: persist advisory jobs leases and success cache"
```

### Task 6: Implement fake Judge and DeepEvalBaseLLM adapter

**Files:**

- Create: `app/deepeval_advisory/judge.py`
- Create: `tests/test_deepeval_advisory_judge.py`

- [ ] **Step 1: Write failing adapter tests**

Create tests for:

- `load_model()` returns the injected transport;
- `get_model_name()` returns the immutable profile version;
- `generate(prompt, schema=Model)` returns a validated Model instance;
- `a_generate` uses the transport's true async method;
- invalid JSON and unknown response fields become `response_schema_invalid`;
- outbound forbidden path/secret is rejected before transport invocation;
- request JSON over 64 KiB is rejected;
- retries are limited to two and only for explicit `not_started` 429/503;
- `outcome_unknown` is not retried;
- logs contain request/evaluation IDs and hashes, never Prompt or content.

Use this strict test schema:

```python
from pydantic import BaseModel, ConfigDict


class ScoreEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: float
    reason: str
```

- [ ] **Step 2: Run the adapter tests in the Worker environment**

```powershell
docker run --rm `
  --network none `
  -v "${PWD}:/work:ro" `
  -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_judge -v
```

Expected: FAIL because `judge.py` does not exist.

- [ ] **Step 3: Implement a transport protocol and fake**

`judge.py` must define:

```python
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
    text: str
    resolved_model_version: str
    token_input: int = 0
    token_output: int = 0
    cost: float = 0
    latency_ms: int = 0
    input_fingerprint: str
    output_fingerprint: str


class JudgeTransport(Protocol):
    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        raise NotImplementedError

    async def acomplete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any] | None,
        context: JudgeCallContext,
    ) -> JudgeResponse:
        raise NotImplementedError
```

The committed Python file must keep `raise NotImplementedError` only in this
protocol. It must include a deterministic `FakeJudgeTransport` whose outputs are
selected by the requested Pydantic schema name.

- [ ] **Step 4: Implement `DeepEvalJudgeLLM`**

Required behavior:

```python
class DeepEvalJudgeLLM(DeepEvalBaseLLM):
    def __init__(
        self,
        transport: JudgeTransport,
        *,
        profile_version: str,
        adapter_version: str = "8099-judge-adapter-v1",
    ) -> None:
        self._transport = transport
        self._profile_version = profile_version
        self._adapter_version = adapter_version
        super().__init__(model=profile_version)

    def load_model(self) -> JudgeTransport:
        return self._transport

    def get_model_name(self) -> str:
        return self._profile_version

    def generate(self, prompt: str, schema=None):
        context = require_judge_context()
        guarded_prompt = validate_judge_prompt(prompt, context)
        response = self._transport.complete(
            prompt=guarded_prompt,
            response_schema=None if schema is None else schema.model_json_schema(),
            context=context,
        )
        return parse_judge_response(response, schema)

    async def a_generate(self, prompt: str, schema=None):
        context = require_judge_context()
        guarded_prompt = validate_judge_prompt(prompt, context)
        response = await self._transport.acomplete(
            prompt=guarded_prompt,
            response_schema=None if schema is None else schema.model_json_schema(),
            context=context,
        )
        return parse_judge_response(response, schema)
```

`JudgeCallContext` must be stored in a `contextvars.ContextVar`, not a process-global mutable current run. It contains only pseudonymous refs, metric/version, Projection hash, budget counters and request identity.

`validate_judge_prompt` must:

- call the boundary guard;
- enforce UTF-8 request size at most 64 KiB;
- enforce the per-job subcall budget;
- reject absent/stale context;
- never log Prompt;
- hash the Prompt for audit.

Do not implement a real HTTP transport in this task.

- [ ] **Step 5: Run tests**

Run the same one-shot container command from Step 2.

Expected: all adapter and fake transport tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/deepeval_advisory/judge.py tests/test_deepeval_advisory_judge.py
git commit -m "feat: adapt fake judge to DeepEval schema calls"
```

### Task 7: Execute the fixed metric set without gating

**Files:**

- Create: `app/deepeval_advisory/evaluator.py`
- Create: `tests/test_deepeval_advisory_evaluator.py`

- [ ] **Step 1: Write failing evaluator tests**

Tests must prove:

1. The metric IDs are exactly:

```text
claim_faithfulness_v1
critical_coverage_v1
attachment_state_consistency_v1
answer_relevancy_v1
```

2. `critical_coverage_v1` is `not_applicable` when `expected_facts` is empty.
3. `attachment_state_consistency_v1` is `not_applicable` when no A/B attachment expectation exists.
4. Applicable metrics produce independent scores.
5. No overall score, threshold, pass/fail or release decision exists.
6. Judge error is `unavailable`, never score 0.
7. Only a fully completed result is eligible for cache.
8. DeepEval's internal `metric.success` value is ignored.
9. `_log_metric_to_confident=False`, `_show_indicator=False`, `verbose_mode=False`.
10. Runtime and scheduled callers use the same `evaluate_projection` function.

- [ ] **Step 2: Run tests and verify failure**

```powershell
docker run --rm `
  --network none `
  -v "${PWD}:/work:ro" `
  -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_evaluator -v
```

Expected: FAIL because `evaluator.py` does not exist.

- [ ] **Step 3: Implement immutable metric definitions**

`evaluator.py` must expose:

```python
DEEPEVAL_VERSION = "4.1.3"
METRIC_SET_VERSION = "8099-advisory-metrics-v1"
PROMPT_VERSION = "8099-advisory-prompts-v1"
RESPONSE_SCHEMA_VERSION = "8099.deepeval-result/v1"
SAFETY_PREAMBLE_VERSION = "8099-judge-safety-v1"


def metric_set_sha256() -> str:
    return canonical_sha256(
        {
            "metric_set_version": METRIC_SET_VERSION,
            "metrics": (
                "claim_faithfulness_v1",
                "critical_coverage_v1",
                "attachment_state_consistency_v1",
                "answer_relevancy_v1",
            ),
            "prompt_version": PROMPT_VERSION,
            "response_schema_version": RESPONSE_SCHEMA_VERSION,
            "safety_preamble_version": SAFETY_PREAMBLE_VERSION,
        }
    )
```

Construct DeepEval metrics with `threshold=0.0` solely because the library requires a threshold. Never persist or inspect `metric.success`.

Use:

```python
FaithfulnessMetric(
    threshold=0.0,
    model=judge,
    include_reason=True,
    async_mode=True,
    strict_mode=False,
    verbose_mode=False,
)

AnswerRelevancyMetric(
    threshold=0.0,
    model=judge,
    include_reason=True,
    async_mode=True,
    strict_mode=False,
    verbose_mode=False,
)
```

For each GEval, supply fixed `evaluation_steps` and a fixed `Rubric`; do not ask the model to generate evaluation steps. Use only the dimensions named by the metric:

```python
Rubric(score_range=(0, 2), expected_outcome="关键要求大部分缺失或相互矛盾。")
Rubric(score_range=(3, 5), expected_outcome="覆盖部分关键要求，但存在明显遗漏。")
Rubric(score_range=(6, 8), expected_outcome="覆盖主要关键要求，仅有有限遗漏。")
Rubric(score_range=(9, 10), expected_outcome="完整、准确覆盖全部关键要求。")
```

`critical_coverage_v1` uses only `ACTUAL_OUTPUT` and `EXPECTED_OUTPUT`.

`attachment_state_consistency_v1` uses only `ACTUAL_OUTPUT` and `EXPECTED_OUTPUT`, where expected output is the bounded A/B attachment expectation.

- [ ] **Step 4: Implement one evaluation path**

Implement the exact asynchronous public call contract
`evaluate_projection(*, job: AdvisoryJob, projection: AdvisoryProjection, judge:
DeepEvalBaseLLM, judge_provider: str, profile_version: str, cache_mode:
Literal["use", "bypass"]) -> AdvisoryResult`.

Its working body must:

1. creates one `LLMTestCase` per Projection unit;
2. sets `input` to the explicit business task;
3. sets `actual_output` to unit text;
4. sets `retrieval_context` to A/B excerpts only;
5. sets `expected_output` only from approved expected facts/attachment expectation;
6. calls applicable metrics with `await metric.a_measure(...)`;
7. normalizes each score to `[0,1]`;
8. sanitizes and caps reason to 500 characters;
9. records failures as explicit error status;
10. aggregates Token/cost/latency from the Judge ledger;
11. computes fingerprints;
12. emits no aggregate quality decision.

Result status:

```text
completed   all applicable metrics scored; not_applicable is allowed
partial     at least one applicable metric scored and another is unavailable/error
unavailable no applicable metric produced a score
over_budget boundary rejected before any Judge call
```

- [ ] **Step 5: Disable DeepEval side effects in the Worker environment**

The Docker environment later must set:

```text
DEEPEVAL_TELEMETRY_OPT_OUT=1
DEEPEVAL_DISABLE_DOTENV=1
DEEPEVAL_NO_INSPECT_PROMPT=1
ENABLE_DEEPEVAL_CACHE=0
```

The evaluator must call metrics directly rather than `deepeval.evaluate`, and must pass `_log_metric_to_confident=False`.

- [ ] **Step 6: Run tests**

Run the one-shot container command from Step 2.

Expected: all evaluator tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add app/deepeval_advisory/evaluator.py tests/test_deepeval_advisory_evaluator.py
git commit -m "feat: score advisory projections with DeepEval"
```

### Task 8: Package and verify the foundation image

**Files:**

- Modify: `Dockerfile.deepeval`
- Modify: `.dockerignore`
- Modify: tests from Tasks 1–7 as needed

- [ ] **Step 1: Write an image isolation test**

Add a test that inspects both requirement files:

```python
def test_main_requirements_do_not_contain_deepeval(self) -> None:
    main = Path("requirements.txt").read_text(encoding="utf-8").lower()
    worker = Path("requirements-deepeval.in").read_text(encoding="utf-8").lower()
    self.assertNotIn("deepeval", main)
    self.assertIn("deepeval==4.1.3", worker)
```

Add a Dockerfile text test asserting:

- it uses `requirements-deepeval.lock`;
- it does not copy `requirements.txt`;
- it runs as a non-root user;
- it has no 8099 port or `uvicorn app.main`.

- [ ] **Step 2: Re-verify and extend `.dockerignore`**

Require at least:

```text
data
data/**
reports
reports/**
site-cache
deepeval-advisory-data
artifacts
artifacts/**
secrets
secrets/**
*.docx
```

This preserves the Task 1 build boundary and prevents runtime reports, Evidence
Packs, secrets and Advisory results from entering the final build context.

- [ ] **Step 3: Replace the dependency-only Dockerfile with the final Worker Dockerfile**

Replace `Dockerfile.deepeval` with:

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEEPEVAL_TELEMETRY_OPT_OUT=1
ENV DEEPEVAL_DISABLE_DOTENV=1
ENV DEEPEVAL_NO_INSPECT_PROMPT=1
ENV ENABLE_DEEPEVAL_CACHE=0

WORKDIR /app

RUN groupadd --gid 10001 advisory \
    && useradd --uid 10001 --gid advisory --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin advisory \
    && install -d -o 10001 -g 10001 -m 0700 /state

COPY requirements-deepeval.lock ./requirements-deepeval.lock
RUN python -m pip install --disable-pip-version-check --no-cache-dir \
    --require-hashes -r requirements-deepeval.lock

COPY app/__init__.py ./app/__init__.py
COPY app/evidence_schema.py ./app/evidence_schema.py
COPY app/evidence_index.py ./app/evidence_index.py
COPY app/formal_body.py ./app/formal_body.py
COPY app/diagnostics.py ./app/diagnostics.py
COPY app/schema_migrations.py ./app/schema_migrations.py
COPY app/report_rules ./app/report_rules
COPY app/deepeval_advisory ./app/deepeval_advisory
COPY tests ./tests

USER 10001:10001

CMD ["python", "-m", "unittest", "tests.test_deepeval_advisory_deepeval_contract", "tests.test_deepeval_advisory_judge", "tests.test_deepeval_advisory_evaluator", "-v"]
```

This foundation image runs tests by default. The Runtime plan will replace the Compose command with the worker CLI without changing the main image.

- [ ] **Step 4: Run pure tests in the isolated test image**

```powershell
docker run --rm --network none `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest `
  tests.test_deepeval_advisory_models `
  tests.test_deepeval_advisory_projection `
  tests.test_deepeval_advisory_store -v
```

Expected: all pure tests PASS.

- [ ] **Step 5: Build and run the isolated DeepEval image**

```powershell
docker build -f Dockerfile.deepeval -t medical-notice-analyzer-deepeval:foundation-test .
docker run --rm --network none medical-notice-analyzer-deepeval:foundation-test
```

Expected:

- contract, Judge and evaluator tests PASS;
- no network request reaches a real Judge;
- the container exits 0.

- [ ] **Step 6: Verify repository scope**

```powershell
git diff --check
git diff --name-only
git diff -- requirements.txt app/main.py Dockerfile docker-compose.yml
```

Expected:

- `git diff --check` has no errors;
- the last command has no output;
- changes are limited to the files listed in this plan.

- [ ] **Step 7: Commit**

```powershell
git add Dockerfile.deepeval .dockerignore app/deepeval_advisory tests/test_deepeval_advisory_*.py tests/fixtures/deepeval_advisory/unit requirements-deepeval.in requirements-deepeval.lock
git commit -m "test: verify isolated DeepEval advisory foundation"
```

## Foundation exit checklist

- [ ] Main `requirements.txt` and Dockerfile contain no DeepEval.
- [ ] DeepEval is exactly 4.1.3 in a Python 3.11 Worker image.
- [ ] No real Provider, credential, production run or network call was used.
- [ ] `finished` and `needs_manual_review` with body are eligible.
- [ ] failed/interrupted/empty/technical-only are excluded.
- [ ] Projection contains only Claim text and Evidence A/B.
- [ ] Evidence C, raw IDs, paths and internal URLs are rejected.
- [ ] Job, lease, result and cache writes are atomic and integrity checked.
- [ ] Only complete valid results are cached.
- [ ] DeepEval output contains scores but no business/release decision.
- [ ] All tests pass with fresh output.
