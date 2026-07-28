from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from app.deepeval_advisory.models import (
    AdvisoryResult,
    JobStatus,
    MetricObservation,
    MetricStatus,
)
from app.deepeval_advisory.reporting import (
    build_safe_snapshot,
    start_reporting_server,
)
from app.deepeval_advisory.settings import AdvisorySettings, SettingsError
from app.deepeval_advisory.worker import (
    AdvisoryWorker,
    set_runtime_paused,
)


FIXTURES = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "deepeval_advisory"
    / "unit"
)


def _read_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _write_sources(
    root: Path,
    *,
    run_id: str = "run_finished",
    pack_id: str = "pack_projection_fixture",
) -> tuple[Path, Path]:
    runs = root / "runs"
    packs = root / "packs"
    runs.mkdir(exist_ok=True)
    packs.mkdir(exist_ok=True)
    run = _read_fixture("run_finished.json")
    pack = _read_fixture("evidence_pack_v2.json")
    run["run_id"] = run_id
    run["pack_id"] = pack_id
    pack["pack_id"] = pack_id
    run_path = runs / f"{run_id}.json"
    pack_path = packs / f"{pack_id}.json"
    run_path.write_text(
        json.dumps(run, ensure_ascii=False),
        encoding="utf-8",
    )
    pack_path.write_text(
        json.dumps(pack, ensure_ascii=False),
        encoding="utf-8",
    )
    return run_path, pack_path


def _settings(root: Path, *, enabled: bool) -> AdvisorySettings:
    return AdvisorySettings(
        analysis_run_dir=root / "runs",
        evidence_pack_dir=root / "packs",
        advisory_dir=root / "state",
        runtime_advisory_enabled=enabled,
        judge_model="fake-judge-profile-v1",
        http_host="127.0.0.1",
        http_port=8100,
    )


def _state_values(root: Path, directory: str) -> list[dict[str, object]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((root / "state" / directory).glob("*.json"))
    ]


def _fake_responses() -> dict[str | None, object]:
    evidence = "公告明确说明本次采购范围和执行要求。"
    claim = "报告准确说明了采购范围。"
    return {
        "Truths": {"truths": [evidence]},
        "Claims": {"claims": [claim]},
        "Verdicts": {"verdicts": [{"verdict": "yes"}]},
        "FaithfulnessScoreReason": {"reason": "报告结论由材料支持。"},
        "Statements": {"statements": [claim]},
        "AnswerRelevancyScoreReason": {"reason": "报告与任务直接相关。"},
        "ReasonScore": {"reason": "报告符合预期要求。", "score": 9.0},
    }


class MinimalRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_and_runtime_pause_still_enroll_without_writes_or_judge(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = {
                "ANALYSIS_RUN_DIR": str(root / "runs"),
                "EVIDENCE_PACK_DIR": str(root / "packs"),
                "DEEPEVAL_ADVISORY_DIR": str(root / "state"),
            }
            for override in (
                {"DEEPEVAL_SCAN_INTERVAL_SECONDS": "299"},
                {"DEEPEVAL_MAX_JUDGE_EVALUATIONS_PER_SCAN": "2"},
            ):
                with self.subTest(override=override):
                    with self.assertRaises(SettingsError):
                        AdvisorySettings.from_mapping(
                            {**base, **override}
                        )

        for enabled, paused in ((False, False), (True, True)):
            with self.subTest(enabled=enabled, paused=paused):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    run_path, pack_path = _write_sources(root)
                    before = {
                        run_path: run_path.read_bytes(),
                        pack_path: pack_path.read_bytes(),
                    }
                    settings = _settings(root, enabled=enabled)
                    if paused:
                        set_runtime_paused(settings.advisory_dir, True)

                    async def forbidden_evaluator(*_args, **_kwargs):
                        raise AssertionError("paused worker called Judge")

                    summary = await AdvisoryWorker(
                        settings,
                        evaluator=forbidden_evaluator,
                    ).run_once()
                    if not enabled and not paused:
                        from app.deepeval_advisory.store import AdvisoryStore

                        pending_job = _state_values(root, "jobs")[0]
                        AdvisoryStore(settings.advisory_dir).transition_job(
                            str(pending_job["job_id"]),
                            JobStatus.PENDING,
                        )
                        summary = await AdvisoryWorker(
                            settings,
                            evaluator=forbidden_evaluator,
                        ).run_once()

                    self.assertEqual(summary.eligible, 1)
                    self.assertEqual(summary.enrolled, 1)
                    self.assertEqual(summary.paused, 1)
                    self.assertEqual(summary.judge_evaluations, 0)
                    self.assertEqual(
                        _state_values(root, "jobs")[0]["status"],
                        "paused",
                    )
                    self.assertEqual(len(_state_values(root, "projections")), 1)
                    self.assertEqual(
                        {path: path.read_bytes() for path in before},
                        before,
                    )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_sources(
                root,
                run_id="run_fault_z_first",
                pack_id="pack_fault_z_first",
            )
            _write_sources(
                root,
                run_id="run_fault_a_next",
                pack_id="pack_fault_a_next",
            )
            _write_sources(
                root,
                run_id="run_fault_m_store",
                pack_id="pack_fault_m_store",
            )
            settings = _settings(root, enabled=False)
            worker = AdvisoryWorker(settings)
            create = worker._store.create_job
            transition = worker._store.transition_job
            create_attempts = 0
            attempts = 0

            def fail_create_once(value):
                nonlocal create_attempts
                create_attempts += 1
                if create_attempts == 1:
                    raise OSError("injected job creation failure")
                return create(value)

            def fail_once(job_id, status, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError("injected transition failure")
                return transition(job_id, status, **kwargs)

            with patch.object(
                worker._store,
                "create_job",
                side_effect=fail_create_once,
            ), patch.object(
                worker._store,
                "transition_job",
                side_effect=fail_once,
            ):
                summary = await worker.run_once()

            self.assertEqual(summary.eligible, 3)
            self.assertEqual(summary.enrolled, 2)
            self.assertEqual(summary.paused, 1)
            self.assertEqual(summary.unavailable, 1)
            self.assertEqual(summary.discovery_errors, 2)
            self.assertEqual(
                {job["status"] for job in _state_values(root, "jobs")},
                {"discovered", "paused"},
            )
            worker_state = json.loads(
                (
                    settings.advisory_dir
                    / "worker"
                    / "current.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(len(worker_state["current_job_ids"]), 2)
            self.assertEqual(worker_state["eligible"], 3)
            self.assertEqual(worker_state["enrolled"], 2)
            self.assertEqual(worker_state["discovery_errors"], 2)
            snapshot = build_safe_snapshot(settings.advisory_dir)
            self.assertEqual(snapshot["coverage"]["eligible"], 3)
            self.assertEqual(snapshot["coverage"]["enrolled"], 2)
            self.assertEqual(
                snapshot["coverage"]["percentages"]["enrolled"],
                66.67,
            )
            worker_state["eligible"] = 1
            (
                settings.advisory_dir / "worker" / "current.json"
            ).write_text(
                json.dumps(worker_state),
                encoding="utf-8",
            )
            invalid_snapshot = build_safe_snapshot(
                settings.advisory_dir
            )
            self.assertEqual(
                invalid_snapshot["coverage"]["eligible"],
                2,
            )
            self.assertGreaterEqual(
                invalid_snapshot["coverage"]["eligible"],
                invalid_snapshot["coverage"]["enrolled"],
            )
            self.assertEqual(
                invalid_snapshot["health"]["state_errors"],
                1,
            )

    async def test_fake_judge_success_persists_four_non_blocking_metrics(
        self,
    ) -> None:
        from app.deepeval_advisory.judge import FakeJudgeTransport

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_sources(root)
            budget_run_path, _ = _write_sources(
                root,
                run_id="run_budget_older_case",
                pack_id="pack_budget_older_case",
            )
            budget_run = json.loads(
                budget_run_path.read_text(encoding="utf-8")
            )
            budget_run["report_markdown"] = (
                str(budget_run["report_markdown"])
                + "\n补充说明：本项目按公告要求执行。"
            )
            budget_run_path.write_text(
                json.dumps(budget_run, ensure_ascii=False),
                encoding="utf-8",
            )
            transport = FakeJudgeTransport(
                profile_version="fake-judge-profile-v1",
                responses=_fake_responses(),
            )

            summary = await AdvisoryWorker(
                _settings(root, enabled=True),
                transport=transport,
            ).run_once()

            result = _state_values(root, "results")[0]
            self.assertEqual(summary.scored, 1)
            self.assertEqual(summary.judge_evaluations, 1)
            self.assertGreater(transport.call_count, 0)
            self.assertEqual(
                {job["status"] for job in _state_values(root, "jobs")},
                {"completed", "pending"},
            )
            self.assertEqual(len(result["metrics"]), 4)
            for name in (
                "blocking",
                "affects_deliverable",
                "affects_report_status",
                "affects_release",
                "affects_user_response",
            ):
                self.assertIs(result[name], False)

    async def test_identical_complete_input_across_runs_evaluates_once_then_caches(
        self,
    ) -> None:
        from app.deepeval_advisory.judge import FakeJudgeTransport

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_sources(
                root,
                run_id="run_cache_source_one",
                pack_id="pack_cache_source_one",
            )
            run_two = _read_fixture("run_finished.json")
            pack_two = _read_fixture("evidence_pack_v2.json")
            run_two["run_id"] = "run_cache_source_two"
            run_two["pack_id"] = "pack_cache_source_two"
            pack_two["pack_id"] = "pack_cache_source_two"
            (root / "runs" / "run_cache_source_two.json").write_text(
                json.dumps(run_two, ensure_ascii=False),
                encoding="utf-8",
            )
            (root / "packs" / "pack_cache_source_two.json").write_text(
                json.dumps(pack_two, ensure_ascii=False),
                encoding="utf-8",
            )
            transport = FakeJudgeTransport(
                profile_version="fake-judge-profile-v1",
                responses=_fake_responses(),
            )

            summary = await AdvisoryWorker(
                _settings(root, enabled=True),
                transport=transport,
            ).run_once()

            jobs = _state_values(root, "jobs")
            results = _state_values(root, "results")
            self.assertEqual(summary.judge_evaluations, 1)
            self.assertEqual(summary.cache_hits, 1)
            self.assertEqual(
                {job["status"] for job in jobs},
                {"completed", "cached"},
            )
            self.assertEqual(len({job["job_id"] for job in jobs}), 2)
            self.assertEqual(len({result["job_id"] for result in results}), 2)
            self.assertEqual(
                len({job["advisory_input_sha256"] for job in jobs}),
                1,
            )

    async def test_missing_credentials_enrolls_as_paused_and_unavailable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_sources(root)
            _write_sources(
                root,
                run_id="run_unconfigured_a_case",
                pack_id="pack_unconfigured_a_case",
            )
            missing_pack_run = _read_fixture("run_finished.json")
            missing_pack_run["run_id"] = "run_missing_pack_case"
            missing_pack_run["pack_id"] = "pack_missing_pack_case"
            (root / "runs" / "run_missing_pack_case.json").write_text(
                json.dumps(missing_pack_run, ensure_ascii=False),
                encoding="utf-8",
            )
            disabled_settings = _settings(root, enabled=False).model_copy(
                update={
                    "judge_base_url": "",
                    "judge_api_key": "",
                    "judge_model": "",
                }
            )
            await AdvisoryWorker(disabled_settings).run_once()
            settings = disabled_settings.model_copy(
                update={"runtime_advisory_enabled": True}
            )

            worker = AdvisoryWorker(settings)
            transition = worker._store.transition_job
            attempts = 0

            def fail_once(job_id, status, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError("injected transition failure")
                return transition(job_id, status, **kwargs)

            with patch.object(
                worker._store,
                "transition_job",
                side_effect=fail_once,
            ):
                summary = await worker.run_once()
            jobs = _state_values(root, "jobs")
            job = next(
                item
                for item in jobs
                if item["error_category"] == "judge_unconfigured"
            )
            snapshot = build_safe_snapshot(settings.advisory_dir)

            self.assertEqual(summary.judge_evaluations, 0)
            self.assertEqual(summary.eligible, 3)
            self.assertEqual(summary.enrolled, 3)
            self.assertEqual(summary.discovery_errors, 2)
            self.assertEqual(job["status"], "paused")
            self.assertEqual(job["error_category"], "judge_unconfigured")
            self.assertTrue(
                any(
                    item["status"] == "unavailable"
                    and item["error_category"]
                    == "evidence_projection_unavailable"
                    for item in jobs
                )
            )
            self.assertEqual(snapshot["coverage"]["enrolled"], 3)
            self.assertEqual(snapshot["coverage"]["paused"], 2)
            self.assertEqual(snapshot["coverage"]["unavailable"], 2)

    async def test_reporting_http_exposes_only_safe_aggregate_snapshot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_path, _pack_path = _write_sources(root)
            raw_run = json.loads(run_path.read_text(encoding="utf-8"))
            raw_body = str(raw_run["report_markdown"])
            raw_run_id = str(raw_run["run_id"])
            raw_pack_id = str(raw_run["pack_id"])

            async def successful_evaluator(job, _projection):
                metrics = tuple(
                    MetricObservation(
                        metric_id=metric_id,
                        metric_version="1",
                        status=MetricStatus.SCORED,
                        score=score,
                    )
                    for metric_id, score in (
                        ("claim_faithfulness_v1", 0.5),
                        ("critical_coverage_v1", 0.6),
                        ("attachment_state_consistency_v1", 0.7),
                        ("answer_relevancy_v1", 0.8),
                    )
                )
                return AdvisoryResult(
                    evaluation_id=(
                        "eval_"
                        + hashlib.sha256(job.job_id.encode()).hexdigest()[:32]
                    ),
                    job_id=job.job_id,
                    run_ref=job.run_ref,
                    advisory_input_sha256=job.advisory_input_sha256,
                    metric_set_sha256="a" * 64,
                    status=JobStatus.COMPLETED,
                    metrics=metrics,
                    judge_provider="fake",
                    resolved_model_or_profile_version="fake-judge-profile-v1",
                )

            settings = _settings(root, enabled=True)
            await AdvisoryWorker(
                settings,
                evaluator=successful_evaluator,
            ).run_once()
            scored_snapshot = build_safe_snapshot(
                settings.advisory_dir
            )
            self.assertEqual(scored_snapshot["coverage"]["scored"], 1)
            self.assertEqual(
                scored_snapshot["metrics"]["answer_relevancy_v1"]["mean"],
                0.8,
            )

            updated_run = dict(raw_run)
            updated_run["version"] = 2
            updated_run["report_markdown"] = (
                raw_body + "\n补充说明：当前版本等待评分。"
            )
            run_path.write_text(
                json.dumps(updated_run, ensure_ascii=False),
                encoding="utf-8",
            )
            await AdvisoryWorker(
                settings.model_copy(
                    update={"runtime_advisory_enabled": False}
                ),
                evaluator=successful_evaluator,
            ).run_once()
            snapshot = build_safe_snapshot(settings.advisory_dir)
            server, thread = start_reporting_server(
                settings.advisory_dir,
                host="127.0.0.1",
                port=0,
            )
            try:
                host, port = server.server_address[:2]
                payloads = {}
                for route in ("/health", "/metrics.json", "/"):
                    with urllib.request.urlopen(
                        f"http://{host}:{port}{route}",
                        timeout=3,
                    ) as response:
                        self.assertEqual(response.status, 200)
                        payloads[route] = response.read().decode("utf-8")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

            rendered = json.dumps(snapshot, ensure_ascii=False) + "".join(
                payloads.values()
            )
            self.assertEqual(snapshot["coverage"]["eligible"], 1)
            self.assertEqual(snapshot["coverage"]["enrolled"], 1)
            self.assertEqual(snapshot["coverage"]["scored"], 0)
            self.assertEqual(
                snapshot["metrics"]["answer_relevancy_v1"],
                {"sample_count": 0, "mean": None},
            )
            self.assertEqual(len(snapshot["recent"]), 1)
            self.assertEqual(snapshot["recent"][0]["report_version"], 2)
            self.assertIn("coverage", rendered)
            self.assertIn("claim_faithfulness_v1", rendered)
            self.assertRegex(rendered, r"runref_[0-9a-f]{32}")
            for forbidden in (
                raw_run_id,
                raw_pack_id,
                raw_body,
                "DEEPEVAL_JUDGE_API_KEY",
                "fake-api-key-secret",
                str(root),
                "http://",
                "https://",
            ):
                self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
