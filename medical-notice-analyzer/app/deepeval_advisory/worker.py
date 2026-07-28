from __future__ import annotations

import asyncio
import inspect
import json
import os
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.deepeval_advisory.hashing import (
    advisory_input_sha256,
    canonical_sha256,
)
from app.deepeval_advisory.models import AdvisoryJob, JobStatus
from app.deepeval_advisory.projection import (
    ProjectionError,
    build_projection,
    load_run_snapshot,
)
from app.deepeval_advisory.settings import AdvisorySettings


_DEEPEVAL_VERSION = "4.1.3"
_ADAPTER_VERSION = "8099-judge-adapter-v1"
_METRIC_SET_VERSION = "8099-advisory-metrics-v1"
_PROMPT_VERSION = "8099-advisory-prompts-v1"
_RESPONSE_SCHEMA_VERSION = "8099.deepeval-result/v1"
_SAFETY_PREAMBLE_VERSION = "8099-judge-safety-v1"
_METRIC_IDS = (
    "claim_faithfulness_v1",
    "critical_coverage_v1",
    "attachment_state_consistency_v1",
    "answer_relevancy_v1",
)
_PAUSE_RELATIVE_PATH = Path("control") / "runtime-paused"
_HMAC_KEY_RELATIVE_PATH = Path("control") / "run-ref.key"


@dataclass(slots=True)
class WorkerSummary:
    eligible: int = 0
    enrolled: int = 0
    scored: int = 0
    cached: int = 0
    paused: int = 0
    unavailable: int = 0
    discovery_errors: int = 0
    judge_evaluations: int = 0
    cache_hits: int = 0


def set_runtime_paused(state_dir: Path, paused: bool) -> None:
    control = Path(state_dir) / "control"
    control.mkdir(parents=True, exist_ok=True)
    marker = control / "runtime-paused"
    if paused:
        try:
            marker.touch(exist_ok=True)
        except OSError as exc:
            raise RuntimeError("unable to pause advisory runtime") from exc
    else:
        try:
            marker.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError("unable to resume advisory runtime") from exc


def runtime_is_paused(state_dir: Path) -> bool:
    return (Path(state_dir) / _PAUSE_RELATIVE_PATH).is_file()


def _metric_set_sha256() -> str:
    return canonical_sha256(
        {
            "metric_set_version": _METRIC_SET_VERSION,
            "metrics": _METRIC_IDS,
            "prompt_version": _PROMPT_VERSION,
            "response_schema_version": _RESPONSE_SCHEMA_VERSION,
            "safety_preamble_version": _SAFETY_PREAMBLE_VERSION,
        }
    )


def _projection_input_sha256(projection: Any) -> str:
    return canonical_sha256(
        {
            "schema_version": projection.schema_version,
            "projection_version": projection.projection_version,
            "report_sha256": projection.report_sha256,
            "units": [
                unit.model_dump(mode="json")
                for unit in projection.units
            ],
        }
    )


def _api_key(settings: AdvisorySettings) -> str:
    value = settings.judge_api_key
    getter = getattr(value, "get_secret_value", None)
    return getter() if callable(getter) else str(value)


def _load_or_create_hmac_key(state_dir: Path) -> bytes:
    path = Path(state_dir) / _HMAC_KEY_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        value = secrets.token_hex(32)
        try:
            with path.open("x", encoding="ascii", newline="\n") as handle:
                handle.write(value + "\n")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except FileExistsError:
            value = path.read_text(encoding="ascii").strip()
    try:
        key = bytes.fromhex(value)
    except ValueError as exc:
        raise RuntimeError("advisory run reference key is invalid") from exc
    if len(key) != 32:
        raise RuntimeError("advisory run reference key is invalid")
    return key


class AdvisoryWorker:
    def __init__(
        self,
        settings: AdvisorySettings,
        *,
        transport: Any | None = None,
        evaluator: Callable[[Any, Any], Awaitable[Any]] | None = None,
    ) -> None:
        from app.deepeval_advisory.cache import AdvisoryCache
        from app.deepeval_advisory.store import AdvisoryStore

        self.settings = settings
        self._transport = transport
        self._evaluator = evaluator
        self._store = AdvisoryStore(settings.advisory_dir)
        self._cache = AdvisoryCache(settings.advisory_dir)
        self._hmac_key = _load_or_create_hmac_key(
            settings.advisory_dir
        )

    def _configured(self) -> bool:
        if self._evaluator is not None or self._transport is not None:
            return True
        return bool(
            self.settings.judge_base_url.strip()
            and _api_key(self.settings).strip()
            and self.settings.judge_model.strip()
        )

    def _identity(self) -> tuple[str, str]:
        if self._evaluator is not None:
            return (
                "injected",
                self.settings.judge_model.strip()
                or "injected-profile-v1",
            )
        if self._transport is not None:
            return (
                "fake",
                self.settings.judge_model.strip()
                or "fake-profile-v1",
            )
        return (
            "openai_compatible",
            self.settings.judge_model.strip() or "unconfigured",
        )

    def _job_for(self, snapshot: Any, projection: Any) -> AdvisoryJob:
        provider, profile = self._identity()
        input_sha256 = advisory_input_sha256(
            report_sha256=snapshot.report_sha256,
            evidence_projection_sha256=_projection_input_sha256(
                projection
            ),
            metric_set_sha256=_metric_set_sha256(),
            deepeval_version=_DEEPEVAL_VERSION,
            adapter_version=_ADAPTER_VERSION,
            judge_provider=provider,
            immutable_judge_model_or_profile_version=profile,
            prompt_version=_PROMPT_VERSION,
            response_schema_version=_RESPONSE_SCHEMA_VERSION,
            safety_preamble_version=_SAFETY_PREAMBLE_VERSION,
            locale=self.settings.locale,
        )
        job_digest = canonical_sha256(
            {
                "run_ref": snapshot.run_ref,
                "report_version": snapshot.report_version,
                "report_sha256": snapshot.report_sha256,
                "advisory_input_sha256": input_sha256,
            }
        )
        job_id = f"job_{job_digest[:32]}"
        try:
            return self._store.read_job(job_id)
        except Exception:
            return self._store.create_job(
                AdvisoryJob(
                    job_id=job_id,
                    run_ref=snapshot.run_ref,
                    report_version=snapshot.report_version,
                    report_sha256=snapshot.report_sha256,
                    projection_sha256=projection.projection_sha256,
                    advisory_input_sha256=input_sha256,
                    status=JobStatus.DISCOVERED,
                )
            )

    def _projection_unavailable_job(self, snapshot: Any) -> AdvisoryJob:
        provider, profile = self._identity()
        projection_sha256 = canonical_sha256(
            {
                "sentinel": "evidence_projection_unavailable",
                "report_sha256": snapshot.report_sha256,
            }
        )
        input_sha256 = advisory_input_sha256(
            report_sha256=snapshot.report_sha256,
            evidence_projection_sha256=projection_sha256,
            metric_set_sha256=_metric_set_sha256(),
            deepeval_version=_DEEPEVAL_VERSION,
            adapter_version=_ADAPTER_VERSION,
            judge_provider=provider,
            immutable_judge_model_or_profile_version=profile,
            prompt_version=_PROMPT_VERSION,
            response_schema_version=_RESPONSE_SCHEMA_VERSION,
            safety_preamble_version=_SAFETY_PREAMBLE_VERSION,
            locale=self.settings.locale,
        )
        job_id = "job_" + canonical_sha256(
            {
                "run_ref": snapshot.run_ref,
                "report_version": snapshot.report_version,
                "report_sha256": snapshot.report_sha256,
                "advisory_input_sha256": input_sha256,
            }
        )[:32]
        try:
            job = self._store.read_job(job_id)
        except Exception:
            job = self._store.create_job(
                AdvisoryJob(
                    job_id=job_id,
                    run_ref=snapshot.run_ref,
                    report_version=snapshot.report_version,
                    report_sha256=snapshot.report_sha256,
                    projection_sha256=projection_sha256,
                    advisory_input_sha256=input_sha256,
                    status=JobStatus.DISCOVERED,
                )
            )
        if job.status is JobStatus.DISCOVERED:
            job = self._store.transition_job(
                job.job_id,
                JobStatus.UNAVAILABLE,
                error_category="evidence_projection_unavailable",
            )
        return job

    @staticmethod
    def _evaluation_id(job: AdvisoryJob) -> str:
        return "eval_" + canonical_sha256(
            {
                "job_id": job.job_id,
                "run_ref": job.run_ref,
                "report_version": job.report_version,
                "projection_sha256": job.projection_sha256,
                "advisory_input_sha256": job.advisory_input_sha256,
            }
        )[:32]

    async def _evaluate(self, job: AdvisoryJob, projection: Any) -> Any:
        if self._evaluator is not None:
            result = self._evaluator(job, projection)
            return await result if inspect.isawaitable(result) else result

        from app.deepeval_advisory.evaluator import evaluate_projection
        from app.deepeval_advisory.judge import DeepEvalJudgeLLM

        provider, profile = self._identity()
        transport = self._transport
        if transport is None:
            from app.deepeval_advisory.transport import (
                OpenAICompatibleJudgeTransport,
            )

            transport = OpenAICompatibleJudgeTransport(
                base_url=self.settings.judge_base_url,
                api_key=_api_key(self.settings),
                model=profile,
                timeout_seconds=self.settings.judge_timeout_seconds,
            )
        judge = DeepEvalJudgeLLM(
            transport,
            profile_version=profile,
            adapter_version=_ADAPTER_VERSION,
        )
        return await evaluate_projection(
            job=job,
            projection=projection,
            judge=judge,
            judge_provider=provider,
            profile_version=profile,
            cache_mode="use",
        )

    async def run_once(self) -> WorkerSummary:
        from app.deepeval_advisory.cache import CacheError
        from app.deepeval_advisory.store import StoreError

        summary = WorkerSummary()
        enrolled_jobs: list[tuple[str, str]] = []
        paused = (
            not self.settings.runtime_advisory_enabled
            or runtime_is_paused(self.settings.advisory_dir)
        )
        for run_path in sorted(
            self.settings.analysis_run_dir.glob("run_*.json"),
            reverse=True,
        ):
            try:
                snapshot = load_run_snapshot(
                    run_path,
                    self._hmac_key,
                    max_bytes=self.settings.max_run_bytes,
                )
            except ProjectionError:
                summary.discovery_errors += 1
                continue
            if not snapshot.eligible:
                continue
            summary.eligible += 1
            pack_path = (
                self.settings.evidence_pack_dir
                / f"{snapshot.pack_id}.json"
            )
            try:
                projection = build_projection(
                    snapshot,
                    pack_path,
                    max_bytes=self.settings.max_pack_bytes,
                )
                self._store.write_projection(projection)
                job = self._job_for(snapshot, projection)
            except ProjectionError:
                summary.discovery_errors += 1
                try:
                    self._projection_unavailable_job(snapshot)
                    summary.enrolled += 1
                    summary.unavailable += 1
                except (StoreError, OSError):
                    pass
                continue
            except (StoreError, OSError):
                summary.discovery_errors += 1
                continue
            summary.enrolled += 1
            enrolled_jobs.append(
                (job.job_id, projection.projection_sha256)
            )

        for job_id, projection_sha256 in enrolled_jobs:
            try:
                job = self._store.read_job(job_id)
                projection = self._store.read_projection(
                    projection_sha256
                )
            except StoreError:
                summary.discovery_errors += 1
                continue
            if job.status in {JobStatus.COMPLETED, JobStatus.CACHED}:
                summary.scored += 1
                if job.status is JobStatus.CACHED:
                    summary.cached += 1
                continue
            if job.status in {
                JobStatus.UNAVAILABLE,
                JobStatus.OVER_BUDGET,
                JobStatus.PARTIAL,
                JobStatus.INDETERMINATE,
            }:
                summary.unavailable += 1
                continue
            if job.status is JobStatus.RUNNING:
                continue
            if paused:
                if job.status is JobStatus.DISCOVERED:
                    job = self._store.transition_job(
                        job.job_id,
                        JobStatus.PAUSED,
                    )
                elif job.status is JobStatus.PENDING:
                    job = self._store.transition_job(
                        job.job_id,
                        JobStatus.PAUSED,
                    )
                summary.paused += 1
                continue
            if not self._configured():
                if job.status is JobStatus.DISCOVERED:
                    job = self._store.transition_job(
                        job.job_id,
                        JobStatus.PAUSED,
                        error_category="judge_unconfigured",
                    )
                elif job.status is JobStatus.PAUSED:
                    job = self._store.transition_job(
                        job.job_id,
                        JobStatus.PENDING,
                    )
                    job = self._store.transition_job(
                        job.job_id,
                        JobStatus.PAUSED,
                        error_category="judge_unconfigured",
                    )
                elif job.status is JobStatus.PENDING:
                    job = self._store.transition_job(
                        job.job_id,
                        JobStatus.PAUSED,
                        error_category="judge_unconfigured",
                    )
                summary.paused += 1
                summary.unavailable += 1
                continue
            try:
                if job.status is JobStatus.PAUSED:
                    job = self._store.transition_job(
                        job.job_id,
                        JobStatus.PENDING,
                        error_category=None,
                    )
                elif job.status is JobStatus.DISCOVERED:
                    job = self._store.transition_job(
                        job.job_id,
                        JobStatus.PENDING,
                    )
                if job.status not in {
                    JobStatus.PENDING,
                    JobStatus.RUNNING,
                }:
                    summary.unavailable += 1
                    continue

                try:
                    cached = self._cache.read(
                        job.advisory_input_sha256
                    )
                except CacheError:
                    cached = None
                if cached is not None:
                    if job.status is JobStatus.PENDING:
                        job = self._store.transition_job(
                            job.job_id,
                            JobStatus.RUNNING,
                            evaluation_id=self._evaluation_id(job),
                        )
                    self._store.record_cache_hit(
                        job.job_id,
                        cached,
                        evaluation_id=self._evaluation_id(job),
                    )
                    summary.scored += 1
                    summary.cached += 1
                    summary.cache_hits += 1
                    continue

                if (
                    summary.judge_evaluations
                    >= self.settings.max_judge_evaluations_per_scan
                ):
                    continue
                if job.status is JobStatus.PENDING:
                    job = self._store.transition_job(
                        job.job_id,
                        JobStatus.RUNNING,
                        evaluation_id=self._evaluation_id(job),
                    )
                if job.status is not JobStatus.RUNNING:
                    summary.unavailable += 1
                    continue
                summary.judge_evaluations += 1
                result = await self._evaluate(job, projection)
                self._store.write_result(result)
                self._store.transition_job(
                    job.job_id,
                    result.status,
                    evaluation_id=result.evaluation_id,
                )
                if result.status is JobStatus.COMPLETED:
                    summary.scored += 1
                    try:
                        self._cache.write_completed(
                            job.advisory_input_sha256,
                            result,
                        )
                    except CacheError:
                        summary.discovery_errors += 1
                else:
                    summary.unavailable += 1
            except Exception:
                summary.discovery_errors += 1
                summary.unavailable += 1
                try:
                    current = self._store.read_job(job.job_id)
                    if current.status is JobStatus.RUNNING:
                        self._store.transition_job(
                            job.job_id,
                            JobStatus.UNAVAILABLE,
                            error_category="evaluation_failed",
                        )
                except Exception:
                    pass

        self._write_summary(summary)
        return summary

    def _write_summary(self, summary: WorkerSummary) -> None:
        directory = self.settings.advisory_dir / "worker"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / "current.json"
            temporary = directory / f".current-{os.getpid()}.tmp"
            temporary.write_text(
                json.dumps(
                    asdict(summary),
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, target)
        except OSError:
            return

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                pass
            await asyncio.sleep(self.settings.scan_interval_seconds)
