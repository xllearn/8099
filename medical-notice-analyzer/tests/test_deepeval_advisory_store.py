from __future__ import annotations

import json
import math
import multiprocessing
import os
import stat
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Empty
from typing import Any
from unittest.mock import patch

from app.deepeval_advisory.cache import AdvisoryCache, CacheError
from app.deepeval_advisory.hashing import (
    canonical_json_bytes,
    canonical_sha256,
)
from app.deepeval_advisory.models import (
    AdvisoryJob,
    AdvisoryProjection,
    AdvisoryResult,
    JobStatus,
    MetricObservation,
    MetricStatus,
)
from app.deepeval_advisory.store import AdvisoryStore, StoreError


RUN_REF = "runref_" + "a" * 32
SECOND_RUN_REF = "runref_" + "b" * 32
ADVISORY_KEY = "d" * 64
SECOND_ADVISORY_KEY = "e" * 64
CREATED_AT = "2026-07-27T01:02:03.004Z"


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(
            2026,
            7,
            27,
            1,
            2,
            3,
            4000,
            tzinfo=timezone.utc,
        )

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


def make_job(
    *,
    job_id: str = "job_12345678",
    run_ref: str = RUN_REF,
    report_version: int = 1,
    advisory_key: str = ADVISORY_KEY,
    status: JobStatus = JobStatus.PENDING,
    created_at: str = CREATED_AT,
    evaluation_id: str | None = None,
) -> AdvisoryJob:
    return AdvisoryJob(
        job_id=job_id,
        run_ref=run_ref,
        report_version=report_version,
        report_sha256="b" * 64,
        projection_sha256="c" * 64,
        advisory_input_sha256=advisory_key,
        status=status,
        created_at=created_at,
        updated_at=created_at,
        evaluation_id=evaluation_id,
    )


def _scored_metric() -> MetricObservation:
    return MetricObservation(
        metric_id="faithfulness",
        metric_version="v1",
        status=MetricStatus.SCORED,
        score=0.875,
        evidence_references=("e1",),
        unsupported_spans=((4, 9),),
    )


def _error_metric() -> MetricObservation:
    return MetricObservation(
        metric_id="coverage",
        metric_version="v1",
        status=MetricStatus.ERROR,
        error_category="judge_unavailable",
    )


def make_result(
    *,
    status: JobStatus = JobStatus.COMPLETED,
    evaluation_id: str = "eval_source_12345678",
    job_id: str = "job_source_12345678",
    run_ref: str = RUN_REF,
    advisory_key: str = ADVISORY_KEY,
) -> AdvisoryResult:
    if status in {JobStatus.COMPLETED, JobStatus.CACHED}:
        metrics = (_scored_metric(),)
    elif status is JobStatus.PARTIAL:
        metrics = (_scored_metric(), _error_metric())
    elif status is JobStatus.UNAVAILABLE:
        metrics = (_error_metric(),)
    else:
        metrics = ()
    return AdvisoryResult(
        evaluation_id=evaluation_id,
        job_id=job_id,
        run_ref=run_ref,
        advisory_input_sha256=advisory_key,
        metric_set_sha256="f" * 64,
        status=status,
        metrics=metrics,
        judge_provider="fake-provider",
        resolved_model_or_profile_version="fake-model-v1",
        token_input=123,
        token_output=45,
        cost=0.0125,
        latency_ms=321,
        retry_count=1,
        input_fingerprint="input-fingerprint",
        output_fingerprint="output-fingerprint",
        created_at=CREATED_AT,
    )


def make_projection() -> AdvisoryProjection:
    value: dict[str, Any] = {
        "schema_version": "8099.deepeval-projection/v1",
        "projection_version": "claim-ab-v1",
        "run_ref": RUN_REF,
        "report_version": 1,
        "report_sha256": "b" * 64,
        "units": [
            {
                "unit_id": "unit_" + "1" * 16,
                "kind": "claim",
                "text": "A bounded claim",
                "claim_count": 1,
                "evidence": [],
                "expected_facts": [],
                "attachment_expectation": None,
            }
        ],
    }
    value["projection_sha256"] = canonical_sha256(value)
    return AdvisoryProjection.model_validate(value)


def _process_acquire_lease(
    root: str,
    owner: str,
    ready: Any,
    start: Any,
    outcomes: Any,
) -> None:
    try:
        store = AdvisoryStore(Path(root))
        ready.put(owner)
        if not start.wait(10):
            outcomes.put(("error", "start timeout"))
            return
        outcomes.put(
            (
                "ok",
                store.try_acquire_lease(
                    ADVISORY_KEY,
                    owner,
                    ttl_seconds=300,
                ),
            )
        )
    except Exception as exc:  # pragma: no cover - surfaced in parent process
        outcomes.put(("error", type(exc).__name__))


class AdvisoryStoreLayoutAndAtomicityTests(unittest.TestCase):
    def test_store_creates_only_the_private_worker_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            AdvisoryStore(root)

            self.assertEqual(
                {path.name for path in root.iterdir()},
                {
                    "attempts",
                    "cache",
                    "corrupt",
                    "coverage",
                    "jobs",
                    "leases",
                    "projections",
                    "results",
                    "run-index",
                },
            )
            if os.name == "posix":
                for path in root.iterdir():
                    with self.subTest(path=path.name):
                        self.assertEqual(
                            stat.S_IMODE(path.stat().st_mode),
                            0o700,
                        )

    def test_atomic_writer_failure_leaves_no_target_partial_or_temp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AdvisoryStore(root)

            with patch(
                "app.deepeval_advisory.store.os.replace",
                side_effect=OSError("PRIVATE_REPLACE_FAILURE"),
            ):
                with self.assertRaises(StoreError) as raised:
                    store.rebuild_coverage()

            self.assertEqual(str(raised.exception), "stored data operation failed")
            coverage = root / "coverage"
            self.assertFalse((coverage / "current.json").exists())
            self.assertEqual(list(coverage.glob(".*.tmp")), [])
            self.assertEqual(list(coverage.glob("*.tmp")), [])

    def test_atomic_writer_rejects_symlink_target_without_touching_referent(
        self,
    ) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AdvisoryStore(root)
            referent = root / "referent.json"
            referent.write_text('{"untouched":true}', encoding="utf-8")
            target = root / "coverage" / "current.json"
            try:
                target.symlink_to(referent)
            except OSError as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")

            with self.assertRaises(StoreError):
                store.rebuild_coverage()

            self.assertEqual(
                referent.read_text(encoding="utf-8"),
                '{"untouched":true}',
            )

    def test_strict_reader_rejects_corruption_without_echoing_content(
        self,
    ) -> None:
        secret = "PRIVATE_CORRUPT_JOB_CONTENT"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AdvisoryStore(root)
            path = root / "jobs" / "job_12345678.json"
            path.write_text(
                json.dumps({"private": secret, "status": math.nan}),
                encoding="utf-8",
            )

            with self.assertRaises(StoreError) as raised:
                store.read_job("job_12345678")

            self.assertEqual(
                str(raised.exception),
                "stored data is invalid or unavailable",
            )
            self.assertNotIn(secret, str(raised.exception))


class AdvisoryJobPersistenceTests(unittest.TestCase):
    def test_create_job_is_idempotent_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AdvisoryStore(root)
            job = make_job()

            first = store.create_job(job)
            before = (root / "jobs" / f"{job.job_id}.json").read_bytes()
            second = store.create_job(job.model_dump(mode="json"))
            after = (root / "jobs" / f"{job.job_id}.json").read_bytes()

            self.assertEqual(first, job)
            self.assertEqual(second, job)
            self.assertEqual(before, after)
            self.assertEqual(len(list((root / "jobs").glob("*.json"))), 1)
            if os.name == "posix":
                self.assertEqual(
                    stat.S_IMODE(
                        (root / "jobs" / f"{job.job_id}.json").stat().st_mode
                    ),
                    0o600,
                )

            different = make_job(status=JobStatus.PAUSED)
            with self.assertRaises(StoreError):
                store.create_job(different)
            self.assertEqual(
                (root / "jobs" / f"{job.job_id}.json").read_bytes(),
                before,
            )

    def test_exclusive_create_race_has_one_winner_and_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AdvisoryStore(Path(directory))
            barrier = threading.Barrier(2)
            outcomes: list[tuple[str, JobStatus | str]] = []

            def create(status: JobStatus) -> None:
                barrier.wait()
                try:
                    value = store.create_job(make_job(status=status))
                    outcomes.append(("created", value.status))
                except StoreError as exc:
                    outcomes.append(("conflict", str(exc)))

            threads = [
                threading.Thread(target=create, args=(JobStatus.PENDING,)),
                threading.Thread(target=create, args=(JobStatus.PAUSED,)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)

            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(
                sorted(kind for kind, _value in outcomes),
                ["conflict", "created"],
            )
            stored = store.read_job("job_12345678")
            self.assertIn(stored.status, {JobStatus.PENDING, JobStatus.PAUSED})
            self.assertEqual(len(list((Path(directory) / "jobs").glob("*.json"))), 1)

    def test_transition_matrix_is_exact_and_terminal_states_never_reopen(
        self,
    ) -> None:
        allowed = {
            JobStatus.DISCOVERED: {
                JobStatus.PENDING,
                JobStatus.PAUSED,
                JobStatus.OVER_BUDGET,
                JobStatus.UNAVAILABLE,
            },
            JobStatus.PENDING: {
                JobStatus.RUNNING,
                JobStatus.PAUSED,
                JobStatus.UNAVAILABLE,
            },
            JobStatus.PAUSED: {JobStatus.PENDING},
            JobStatus.RUNNING: {
                JobStatus.COMPLETED,
                JobStatus.CACHED,
                JobStatus.PARTIAL,
                JobStatus.RETRYING,
                JobStatus.UNAVAILABLE,
                JobStatus.OVER_BUDGET,
            },
            JobStatus.RETRYING: {
                JobStatus.RUNNING,
                JobStatus.UNAVAILABLE,
            },
        }
        statuses = tuple(JobStatus)
        with tempfile.TemporaryDirectory() as directory:
            store = AdvisoryStore(Path(directory))
            counter = 0
            for source in statuses:
                for target in statuses:
                    counter += 1
                    job_id = f"job_matrix_{counter:04d}"
                    store.create_job(
                        make_job(job_id=job_id, status=source)
                    )
                    if target in allowed.get(source, set()):
                        transitioned = store.transition_job(job_id, target)
                        self.assertEqual(transitioned.status, target)
                        self.assertEqual(store.read_job(job_id), transitioned)
                    else:
                        with self.assertRaises(StoreError):
                            store.transition_job(job_id, target)
                        self.assertEqual(store.read_job(job_id).status, source)

    def test_projection_and_result_persistence_are_strict_and_immutable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AdvisoryStore(Path(directory))
            projection = make_projection()
            result = make_result()

            self.assertEqual(store.write_projection(projection), projection)
            self.assertEqual(
                store.write_projection(projection.model_dump(mode="json")),
                projection,
            )
            self.assertEqual(
                store.read_projection(projection.projection_sha256),
                projection,
            )
            self.assertEqual(store.write_result(result), result)
            self.assertEqual(store.write_result(result), result)
            self.assertEqual(store.read_result(result.job_id), result)

            conflicting = make_result(
                evaluation_id="eval_other_12345678",
                job_id=result.job_id,
            )
            with self.assertRaises(StoreError):
                store.write_result(conflicting)
            self.assertEqual(store.read_result(result.job_id), result)

    def test_unsafe_identifiers_are_rejected_before_path_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AdvisoryStore(Path(directory))
            for unsafe in ("../job_12345678", "", "job/slash"):
                with self.subTest(unsafe=unsafe), self.assertRaises(StoreError):
                    store.read_job(unsafe)
            for unsafe in ("../" + "d" * 64, "D" * 64, "d" * 63):
                with self.subTest(unsafe=unsafe), self.assertRaises(StoreError):
                    store.read_projection(unsafe)


class AdvisoryLeaseTests(unittest.TestCase):
    def test_absent_lease_is_acquired_and_unexpired_lease_is_shared_once(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AdvisoryStore(Path(directory))
            barrier = threading.Barrier(2)
            outcomes: list[bool] = []

            def acquire(owner: str) -> None:
                barrier.wait()
                outcomes.append(
                    store.try_acquire_lease(
                        ADVISORY_KEY,
                        owner,
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
                thread.join(10)

            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(sorted(outcomes), [False, True])
            lease = store.read_lease(ADVISORY_KEY)
            self.assertIn(lease["owner"], {"worker-a", "worker-b"})
            self.assertEqual(lease["request_phase"], "not_issued")

    @unittest.skipUnless(
        os.name == "posix",
        "cross-process lease test runs in the Linux foundation image",
    )
    def test_two_processes_share_one_portalocker_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = multiprocessing.get_context("spawn")
            ready = context.Queue()
            start = context.Event()
            outcomes = context.Queue()
            processes = [
                context.Process(
                    target=_process_acquire_lease,
                    args=(directory, owner, ready, start, outcomes),
                )
                for owner in ("process-a", "process-b")
            ]
            for process in processes:
                process.start()
            try:
                self.assertEqual(
                    {ready.get(timeout=10), ready.get(timeout=10)},
                    {"process-a", "process-b"},
                )
                start.set()
                results = [outcomes.get(timeout=10), outcomes.get(timeout=10)]
            except Empty as exc:
                self.fail(f"lease process did not report: {exc}")
            finally:
                for process in processes:
                    process.join(10)
                    if process.is_alive():
                        process.terminate()
                        process.join(5)

            self.assertEqual(
                [process.exitcode for process in processes],
                [0, 0],
            )
            self.assertTrue(all(kind == "ok" for kind, _value in results))
            self.assertEqual(
                sorted(value for _kind, value in results),
                [False, True],
            )

    def test_stale_not_issued_lease_can_be_reclaimed(self) -> None:
        clock = MutableClock()
        with tempfile.TemporaryDirectory() as directory:
            store = AdvisoryStore(Path(directory), clock=clock)
            self.assertTrue(
                store.try_acquire_lease(
                    ADVISORY_KEY,
                    "worker-a",
                    ttl_seconds=10,
                )
            )
            clock.advance(11)

            self.assertTrue(
                store.try_acquire_lease(
                    ADVISORY_KEY,
                    "worker-b",
                    ttl_seconds=20,
                )
            )
            lease = store.read_lease(ADVISORY_KEY)
            self.assertEqual(lease["owner"], "worker-b")
            self.assertEqual(lease["request_phase"], "not_issued")
            self.assertIsNone(lease["issued_at"])

    def test_stale_issued_lease_becomes_one_indeterminate_attempt_no_retry(
        self,
    ) -> None:
        clock = MutableClock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AdvisoryStore(root, clock=clock)
            self.assertTrue(
                store.try_acquire_lease(
                    ADVISORY_KEY,
                    "worker-a",
                    ttl_seconds=10,
                )
            )
            issued = store.mark_request_issued(
                ADVISORY_KEY,
                "worker-a",
                "eval_issued_12345678",
                "attempt_issued_12345678",
            )
            self.assertEqual(issued["request_phase"], "issued")
            clock.advance(11)

            self.assertFalse(
                store.try_acquire_lease(
                    ADVISORY_KEY,
                    "worker-b",
                    ttl_seconds=10,
                )
            )
            recovered = store.read_lease(ADVISORY_KEY)
            self.assertEqual(recovered["request_phase"], "indeterminate")
            attempts = list((root / "attempts").glob("*.json"))
            self.assertEqual(len(attempts), 1)
            first_attempt = attempts[0].read_bytes()

            clock.advance(30)
            self.assertFalse(
                store.try_acquire_lease(
                    ADVISORY_KEY,
                    "worker-c",
                    ttl_seconds=10,
                )
            )
            self.assertEqual(
                list((root / "attempts").glob("*.json")),
                attempts,
            )
            self.assertEqual(attempts[0].read_bytes(), first_attempt)

    def test_release_expired_issued_lease_preserves_indeterminate_attempt(
        self,
    ) -> None:
        clock = MutableClock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AdvisoryStore(root, clock=clock)
            self.assertTrue(
                store.try_acquire_lease(
                    ADVISORY_KEY,
                    "worker-a",
                    ttl_seconds=10,
                )
            )
            store.mark_request_issued(
                ADVISORY_KEY,
                "worker-a",
                "eval_release_12345678",
                "attempt_release_12345678",
            )
            clock.advance(11)

            with self.assertRaises(StoreError):
                store.heartbeat_lease(
                    ADVISORY_KEY,
                    "worker-a",
                    ttl_seconds=10,
                )
            with self.assertRaises(StoreError):
                store.mark_request_issued(
                    ADVISORY_KEY,
                    "worker-a",
                    "eval_release_12345678",
                    "attempt_release_12345678",
                )
            self.assertEqual(
                store.read_lease(ADVISORY_KEY)["request_phase"],
                "issued",
            )

            self.assertFalse(
                store.release_lease(ADVISORY_KEY, "worker-a")
            )
            lease_path = root / "leases" / f"{ADVISORY_KEY}.json"
            self.assertTrue(lease_path.is_file())
            self.assertEqual(
                store.read_lease(ADVISORY_KEY)["request_phase"],
                "indeterminate",
            )
            attempts = list((root / "attempts").glob("*.json"))
            self.assertEqual(len(attempts), 1)
            first_attempt = attempts[0].read_bytes()

            self.assertFalse(
                store.release_lease(ADVISORY_KEY, "worker-a")
            )
            self.assertFalse(
                store.try_acquire_lease(
                    ADVISORY_KEY,
                    "worker-b",
                    ttl_seconds=10,
                )
            )
            self.assertEqual(
                list((root / "attempts").glob("*.json")),
                attempts,
            )
            self.assertEqual(attempts[0].read_bytes(), first_attempt)
            self.assertTrue(lease_path.is_file())

    def test_heartbeat_mark_issued_and_release_require_owner_match(self) -> None:
        clock = MutableClock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AdvisoryStore(root, clock=clock)
            self.assertTrue(
                store.try_acquire_lease(
                    ADVISORY_KEY,
                    "worker-a",
                    ttl_seconds=10,
                )
            )
            for operation in (
                lambda: store.heartbeat_lease(
                    ADVISORY_KEY,
                    "worker-b",
                    ttl_seconds=10,
                ),
                lambda: store.mark_request_issued(
                    ADVISORY_KEY,
                    "worker-b",
                    "eval_issued_12345678",
                    "attempt_issued_12345678",
                ),
                lambda: store.release_lease(ADVISORY_KEY, "worker-b"),
            ):
                with self.subTest(operation=operation), self.assertRaises(
                    StoreError
                ):
                    operation()

            clock.advance(5)
            heartbeat = store.heartbeat_lease(
                ADVISORY_KEY,
                "worker-a",
                ttl_seconds=10,
            )
            self.assertEqual(
                heartbeat["heartbeat_at"],
                "2026-07-27T01:02:08.004Z",
            )
            self.assertEqual(
                heartbeat["expires_at"],
                "2026-07-27T01:02:18.004Z",
            )
            store.mark_request_issued(
                ADVISORY_KEY,
                "worker-a",
                "eval_issued_12345678",
                "attempt_issued_12345678",
            )
            self.assertTrue(store.release_lease(ADVISORY_KEY, "worker-a"))
            self.assertFalse((root / "leases" / f"{ADVISORY_KEY}.json").exists())

    def test_corrupt_lease_fails_closed_without_content_echo(self) -> None:
        secret = "PRIVATE_CORRUPT_LEASE_CONTENT"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AdvisoryStore(root)
            self.assertTrue(
                store.try_acquire_lease(
                    ADVISORY_KEY,
                    "worker-a",
                    ttl_seconds=10,
                )
            )
            lease_path = root / "leases" / f"{ADVISORY_KEY}.json"
            lease_path.write_text(
                json.dumps(
                    {
                        "schema_version": "bad",
                        "private": secret,
                    }
                ),
                encoding="utf-8",
            )
            before = lease_path.read_bytes()

            with self.assertRaises(StoreError) as raised:
                store.try_acquire_lease(
                    ADVISORY_KEY,
                    "worker-b",
                    ttl_seconds=10,
                )

            self.assertEqual(
                str(raised.exception),
                "stored data is invalid or unavailable",
            )
            self.assertNotIn(secret, str(raised.exception))
            self.assertEqual(lease_path.read_bytes(), before)

    def test_lease_key_owner_and_ttl_are_strictly_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AdvisoryStore(Path(directory))
            for key in ("d" * 63, "D" * 64, "../" + "d" * 64):
                with self.subTest(key=key), self.assertRaises(StoreError):
                    store.try_acquire_lease(key, "worker-a", ttl_seconds=10)
            for owner in ("", " ", "../worker", "worker/slash"):
                with self.subTest(owner=owner), self.assertRaises(StoreError):
                    store.try_acquire_lease(
                        ADVISORY_KEY,
                        owner,
                        ttl_seconds=10,
                    )
            for ttl in (0, -1, True, 1.5, float("inf")):
                with self.subTest(ttl=ttl), self.assertRaises(StoreError):
                    store.try_acquire_lease(
                        ADVISORY_KEY,
                        "worker-a",
                        ttl_seconds=ttl,  # type: ignore[arg-type]
                    )


class AdvisoryCacheTests(unittest.TestCase):
    def test_only_completed_strict_result_is_cached_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = AdvisoryCache(root)
            completed = make_result()

            written = cache.write_completed(
                ADVISORY_KEY,
                completed.model_dump(mode="json"),
            )
            loaded = cache.read(ADVISORY_KEY)

            self.assertEqual(written, completed)
            self.assertEqual(loaded, completed)
            envelope = json.loads(
                (root / "cache" / f"{ADVISORY_KEY}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                set(envelope),
                {
                    "schema_version",
                    "advisory_input_sha256",
                    "source_evaluation_id",
                    "result_sha256",
                    "result",
                    "created_at",
                },
            )
            self.assertEqual(
                envelope["schema_version"],
                "8099.deepeval-cache/v1",
            )
            self.assertEqual(
                envelope["result_sha256"],
                canonical_sha256(completed.model_dump(mode="json")),
            )
            if os.name == "posix":
                self.assertEqual(
                    stat.S_IMODE(
                        (root / "cache" / f"{ADVISORY_KEY}.json").stat().st_mode
                    ),
                    0o600,
                )

    def test_every_noncompleted_result_and_ad_hoc_value_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = AdvisoryCache(root)
            for index, status in enumerate(
                (
                    JobStatus.CACHED,
                    JobStatus.PARTIAL,
                    JobStatus.UNAVAILABLE,
                    JobStatus.OVER_BUDGET,
                    JobStatus.INDETERMINATE,
                )
            ):
                key = f"{index + 1:064x}"
                with self.subTest(status=status), self.assertRaises(CacheError):
                    cache.write_completed(
                        key,
                        make_result(
                            status=status,
                            advisory_key=key,
                            evaluation_id=f"eval_status_{index:08d}",
                            job_id=f"job_status_{index:08d}",
                        ),
                    )
            with self.assertRaises(CacheError):
                cache.write_completed(
                    SECOND_ADVISORY_KEY,
                    {
                        "status": "completed",
                        "value": 1,
                    },
                )
            self.assertEqual(list((root / "cache").glob("*.json")), [])

    def test_cache_is_immutable_exclusive_and_identical_write_is_idempotent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = AdvisoryCache(root)
            original = make_result()

            cache.write_completed(ADVISORY_KEY, original)
            path = root / "cache" / f"{ADVISORY_KEY}.json"
            before = path.read_bytes()
            cache.write_completed(ADVISORY_KEY, original)
            self.assertEqual(path.read_bytes(), before)

            conflict = make_result(
                evaluation_id="eval_conflict_12345678",
                job_id="job_conflict_12345678",
            )
            with self.assertRaises(CacheError):
                cache.write_completed(ADVISORY_KEY, conflict)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(cache.read(ADVISORY_KEY), original)

    def test_cache_integrity_key_source_schema_and_result_are_verified(
        self,
    ) -> None:
        secret = "PRIVATE_CACHE_CORRUPTION"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = AdvisoryCache(root)
            original = make_result()
            cache.write_completed(ADVISORY_KEY, original)
            path = root / "cache" / f"{ADVISORY_KEY}.json"
            valid = json.loads(path.read_text(encoding="utf-8"))
            corruptions = (
                ("result_sha256", "0" * 64),
                ("advisory_input_sha256", SECOND_ADVISORY_KEY),
                ("source_evaluation_id", "eval_wrong_12345678"),
                ("schema_version", "8099.deepeval-cache/v999"),
            )
            for field, value in corruptions:
                envelope = dict(valid)
                envelope[field] = value
                envelope["private"] = secret
                path.write_bytes(canonical_json_bytes(envelope))
                with self.subTest(field=field), self.assertRaises(
                    CacheError
                ) as raised:
                    cache.read(ADVISORY_KEY)
                self.assertEqual(
                    str(raised.exception),
                    "cache entry is invalid or unavailable",
                )
                self.assertNotIn(secret, str(raised.exception))

            envelope = dict(valid)
            envelope["result"] = dict(envelope["result"])
            envelope["result"]["status"] = "partial"
            envelope["result_sha256"] = canonical_sha256(envelope["result"])
            path.write_bytes(canonical_json_bytes(envelope))
            with self.assertRaises(CacheError):
                cache.read(ADVISORY_KEY)

    def test_cache_hash_is_checked_against_raw_result_before_coercion(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = AdvisoryCache(root)
            result_value = make_result().model_dump(mode="json")
            result_value["cost"] = 0.0
            original = AdvisoryResult.model_validate(result_value)
            cache.write_completed(ADVISORY_KEY, original)
            path = root / "cache" / f"{ADVISORY_KEY}.json"
            envelope = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(envelope["result"]["cost"], 0.0)
            original_hash = envelope["result_sha256"]

            envelope["result"]["cost"] = 0
            path.write_bytes(canonical_json_bytes(envelope))

            self.assertEqual(envelope["result_sha256"], original_hash)
            with self.assertRaises(CacheError):
                cache.read(ADVISORY_KEY)

    def test_bypass_returns_none_without_any_storage_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = AdvisoryCache(Path(directory))
            with patch(
                "app.deepeval_advisory.cache._read_json_object",
                side_effect=AssertionError("cache read must not occur"),
            ) as reader:
                self.assertIsNone(
                    cache.read("../unsafe-key", cache_mode="bypass")
                )
            reader.assert_not_called()

    def test_cache_hit_records_current_cached_job_and_separate_result(self) -> None:
        clock = MutableClock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AdvisoryStore(root, clock=clock)
            cache = AdvisoryCache(root)
            source = make_result()
            cache.write_completed(ADVISORY_KEY, source)
            cached_source = cache.read(ADVISORY_KEY)
            self.assertIsNotNone(cached_source)
            current_job = make_job(
                job_id="job_current_12345678",
                run_ref=SECOND_RUN_REF,
                status=JobStatus.RUNNING,
            )
            store.create_job(current_job)

            current_result = store.record_cache_hit(
                current_job.job_id,
                cached_source,
                evaluation_id="eval_current_12345678",
            )

            self.assertEqual(current_result.status, JobStatus.CACHED)
            self.assertEqual(current_result.job_id, current_job.job_id)
            self.assertEqual(current_result.run_ref, current_job.run_ref)
            self.assertEqual(
                current_result.advisory_input_sha256,
                current_job.advisory_input_sha256,
            )
            self.assertEqual(current_result.metrics, source.metrics)
            self.assertEqual(current_result.judge_provider, source.judge_provider)
            self.assertEqual(
                current_result.resolved_model_or_profile_version,
                source.resolved_model_or_profile_version,
            )
            self.assertEqual(current_result.token_input, source.token_input)
            self.assertEqual(current_result.token_output, source.token_output)
            self.assertEqual(
                current_result.input_fingerprint,
                source.input_fingerprint,
            )
            self.assertEqual(
                current_result.output_fingerprint,
                source.output_fingerprint,
            )
            persisted = store.read_result(current_job.job_id)
            self.assertEqual(persisted, current_result)
            current = store.read_job(current_job.job_id)
            self.assertEqual(current.status, JobStatus.CACHED)
            self.assertEqual(
                current.source_evaluation_id,
                source.evaluation_id,
            )
            self.assertEqual(
                current.evaluation_id,
                current_result.evaluation_id,
            )
            self.assertNotEqual(
                current_result.evaluation_id,
                source.evaluation_id,
            )
            self.assertTrue(
                (root / "cache" / f"{ADVISORY_KEY}.json").is_file()
            )
            self.assertTrue(
                (root / "results" / f"{current_job.job_id}.json").is_file()
            )


class AdvisoryCoverageTests(unittest.TestCase):
    def test_latest_job_per_run_version_drives_deterministic_coverage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AdvisoryStore(root)
            store.create_job(
                make_job(
                    job_id="job_old_completed",
                    status=JobStatus.COMPLETED,
                    created_at="2026-07-27T01:00:00.000Z",
                )
            )
            latest_unavailable = make_job(
                job_id="job_new_unavailable",
                status=JobStatus.UNAVAILABLE,
                created_at="2026-07-27T02:00:00.000Z",
            )
            store.create_job(latest_unavailable)
            store.create_job(
                make_job(
                    job_id="job_cached_other_run",
                    run_ref=SECOND_RUN_REF,
                    status=JobStatus.CACHED,
                    created_at="2026-07-27T03:00:00.000Z",
                )
            )
            store.create_job(
                make_job(
                    job_id="job_completed_v2",
                    report_version=2,
                    status=JobStatus.COMPLETED,
                    created_at="2026-07-27T04:00:00.000Z",
                )
            )
            store.create_job(
                make_job(
                    job_id="job_pending_third_run",
                    run_ref="runref_" + "c" * 32,
                    status=JobStatus.PENDING,
                    created_at="2026-07-27T05:00:00.000Z",
                )
            )

            first = store.rebuild_coverage()
            first_bytes = (root / "coverage" / "current.json").read_bytes()
            second = store.rebuild_coverage()
            second_bytes = (root / "coverage" / "current.json").read_bytes()

            self.assertEqual(first, second)
            self.assertEqual(first_bytes, second_bytes)
            self.assertEqual(first_bytes, canonical_json_bytes(first))
            self.assertEqual(first["eligible_reports"], 4)
            self.assertEqual(first["enrolled_reports"], 4)
            self.assertEqual(first["terminal_reports"], 3)
            self.assertEqual(first["scored_reports"], 1)
            self.assertEqual(first["cached_reports"], 1)
            self.assertEqual(first["valid_score_reports"], 2)
            self.assertEqual(first["unavailable_reports"], 1)
            self.assertEqual(first["pending_reports"], 1)
            self.assertEqual(first["completed_reports"], 1)
            self.assertEqual(
                store.read_latest_job(RUN_REF, 1),
                latest_unavailable,
            )

            index = root / "run-index" / RUN_REF / "v00000001"
            history = [
                path
                for path in index.glob("*.json")
                if path.name != "latest.json"
            ]
            self.assertEqual(len(history), 2)
            self.assertTrue((index / "latest.json").is_file())
            self.assertEqual(len(list((root / "jobs").glob("*.json"))), 5)
            if os.name == "posix":
                self.assertEqual(
                    stat.S_IMODE(
                        (root / "coverage" / "current.json").stat().st_mode
                    ),
                    0o600,
                )


if __name__ == "__main__":
    unittest.main()
