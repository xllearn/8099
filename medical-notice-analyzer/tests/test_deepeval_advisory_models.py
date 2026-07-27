from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.deepeval_advisory.models import (
    AdvisoryJob,
    AdvisoryProjection,
    AdvisoryResult,
    EvidenceExcerpt,
    JobStatus,
    MetricObservation,
    MetricStatus,
    ProjectionUnit,
    RunSnapshot,
    SafeLocator,
    utc_now_iso,
)
from app.deepeval_advisory.settings import AdvisorySettings, SettingsError


RUN_REF = f"runref_{'1' * 32}"
REPORT_SHA256 = "a" * 64
PROJECTION_SHA256 = "b" * 64
ADVISORY_INPUT_SHA256 = "c" * 64
METRIC_SET_SHA256 = "d" * 64

RUN_STATUSES = (
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
)


def valid_job_payload() -> dict[str, object]:
    return {
        "job_id": "job_12345678",
        "run_ref": RUN_REF,
        "report_version": 1,
        "report_sha256": REPORT_SHA256,
        "projection_sha256": PROJECTION_SHA256,
        "advisory_input_sha256": ADVISORY_INPUT_SHA256,
        "status": JobStatus.PENDING,
    }


def valid_result_payload() -> dict[str, object]:
    return {
        "evaluation_id": "eval_12345678",
        "job_id": "job_12345678",
        "run_ref": RUN_REF,
        "advisory_input_sha256": ADVISORY_INPUT_SHA256,
        "metric_set_sha256": METRIC_SET_SHA256,
        "status": JobStatus.COMPLETED,
        "metrics": (
            MetricObservation(
                metric_id="claim_faithfulness_v1",
                metric_version="1",
                status=MetricStatus.SCORED,
                score=0.9,
            ),
        ),
    }


def required_settings(root: Path | str = Path(".")) -> dict[str, str]:
    root_path = Path(root)
    return {
        "ANALYSIS_RUN_DIR": str(root_path / "runs"),
        "EVIDENCE_PACK_DIR": str(root_path / "packs"),
        "DEEPEVAL_ADVISORY_DIR": str(root_path / "advisory"),
    }


class AdvisoryModelTests(unittest.TestCase):
    def test_result_is_permanently_non_blocking(self) -> None:
        result = AdvisoryResult(**valid_result_payload())

        self.assertFalse(result.blocking)
        self.assertFalse(result.affects_deliverable)
        self.assertFalse(result.affects_report_status)
        self.assertFalse(result.affects_release)
        self.assertFalse(result.affects_user_response)
        for forbidden_field in (
            "passed",
            "threshold",
            "decision",
            "deliverable_override",
            "report_status",
            "release_override",
        ):
            with self.subTest(field=forbidden_field):
                self.assertNotIn(forbidden_field, result.model_dump())

    def test_result_rejects_blocking_override_and_unknown_fields(self) -> None:
        plan_payload = {
            **valid_result_payload(),
            "blocking": True,
            "release_decision": "deny",
        }
        with self.assertRaises(ValidationError):
            AdvisoryResult.model_validate(plan_payload)

        for false_only_field in (
            "blocking",
            "affects_deliverable",
            "affects_report_status",
            "affects_release",
            "affects_user_response",
        ):
            with self.subTest(false_only_field=false_only_field):
                with self.assertRaises(ValidationError):
                    AdvisoryResult.model_validate(
                        {
                            **valid_result_payload(),
                            false_only_field: True,
                        }
                    )

        for unknown_field in (
            "passed",
            "threshold",
            "decision",
            "deliverable_override",
            "report_status",
            "release_override",
        ):
            with self.subTest(unknown_field=unknown_field):
                with self.assertRaises(ValidationError):
                    AdvisoryResult.model_validate(
                        {
                            **valid_result_payload(),
                            unknown_field: "not-allowed",
                        }
                    )

    def test_job_rejects_invalid_transition_input(self) -> None:
        with self.assertRaises(ValidationError):
            AdvisoryJob(
                job_id="job_12345678",
                run_ref=RUN_REF,
                report_version=0,
                report_sha256="x",
                projection_sha256="y",
                advisory_input_sha256="z",
                status=JobStatus.PENDING,
            )

    def test_run_snapshot_accepts_exact_status_literals(self) -> None:
        for status in RUN_STATUSES:
            with self.subTest(status=status):
                snapshot = RunSnapshot(
                    run_id="run_12345678",
                    pack_id="pack_12345678",
                    run_ref=RUN_REF,
                    status=status,
                    report_version=1,
                    report_markdown="# Report",
                    report_ir=None,
                    report_sha256=REPORT_SHA256,
                    eligible=True,
                )
                self.assertEqual(snapshot.status, status)

        with self.assertRaises(ValidationError):
            RunSnapshot(
                run_id="run_12345678",
                pack_id="pack_12345678",
                run_ref=RUN_REF,
                status="unknown",
                report_version=1,
                report_markdown="# Report",
                report_ir=None,
                report_sha256=REPORT_SHA256,
                eligible=True,
            )

    def test_job_and_metric_status_literals_are_exact(self) -> None:
        self.assertEqual(
            tuple(status.value for status in JobStatus),
            (
                "discovered",
                "pending",
                "paused",
                "running",
                "completed",
                "cached",
                "retrying",
                "partial",
                "unavailable",
                "over_budget",
                "indeterminate",
            ),
        )
        self.assertEqual(
            tuple(status.value for status in MetricStatus),
            ("scored", "not_applicable", "unavailable", "error"),
        )

    def test_projection_models_enforce_bounded_safe_contract(self) -> None:
        locator = SafeLocator(kind="table_cell", table_index=1, row=2, column=3)
        excerpt = EvidenceExcerpt(
            local_id="e1",
            level="A",
            kind="notice_body",
            excerpt="A bounded excerpt.",
            locator=locator,
        )
        unit = ProjectionUnit(
            unit_id="unit_1234567890abcdef",
            kind="claim",
            text="A supported claim.",
            claim_count=1,
            evidence=(excerpt,),
            expected_facts=("A supported fact.",),
        )
        projection = AdvisoryProjection(
            run_ref=RUN_REF,
            report_version=1,
            report_sha256=REPORT_SHA256,
            units=(unit,),
            projection_sha256=PROJECTION_SHA256,
        )

        self.assertEqual(
            projection.schema_version,
            "8099.deepeval-projection/v1",
        )
        self.assertEqual(projection.projection_version, "claim-ab-v1")
        with self.assertRaises(ValidationError):
            AdvisoryProjection(
                run_ref=RUN_REF,
                report_version=1,
                report_sha256=REPORT_SHA256,
                units=(),
                projection_sha256=PROJECTION_SHA256,
            )

    def test_models_are_frozen_and_reject_extra_fields(self) -> None:
        job = AdvisoryJob(**valid_job_payload())

        with self.assertRaises(ValidationError):
            job.status = JobStatus.RUNNING
        with self.assertRaises(ValidationError):
            AdvisoryJob.model_validate(
                {
                    **valid_job_payload(),
                    "release_decision": "allow",
                }
            )

    def test_utc_now_iso_is_utc_with_milliseconds_and_z_suffix(self) -> None:
        self.assertRegex(
            utc_now_iso(),
            re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"),
        )


class AdvisorySettingsTests(unittest.TestCase):
    def test_settings_default_both_execution_flags_to_false(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = AdvisorySettings.from_mapping(required_settings(root))

        self.assertFalse(settings.runtime_advisory_enabled)
        self.assertFalse(settings.scheduled_evaluation_enabled)
        self.assertEqual(settings.scan_interval_seconds, 30)
        self.assertEqual(settings.worker_concurrency, 1)
        self.assertEqual(settings.lease_ttl_seconds, 900)
        self.assertEqual(settings.lease_heartbeat_seconds, 30)
        self.assertEqual(settings.max_run_bytes, 4 * 1024 * 1024)
        self.assertEqual(settings.max_pack_bytes, 32 * 1024 * 1024)

    def test_settings_reject_ambiguous_boolean(self) -> None:
        for name in (
            "DEEPEVAL_RUNTIME_ADVISORY_ENABLED",
            "DEEPEVAL_SCHEDULED_EVALUATION_ENABLED",
        ):
            with self.subTest(name=name):
                with self.assertRaises(SettingsError):
                    AdvisorySettings.from_mapping(
                        {
                            **required_settings(),
                            name: "enabled",
                        }
                    )

    def test_settings_accept_only_case_insensitive_boolean_literals(self) -> None:
        settings = AdvisorySettings.from_mapping(
            {
                **required_settings(),
                "DEEPEVAL_RUNTIME_ADVISORY_ENABLED": "TrUe",
                "DEEPEVAL_SCHEDULED_EVALUATION_ENABLED": "FALSE",
            }
        )
        self.assertTrue(settings.runtime_advisory_enabled)
        self.assertFalse(settings.scheduled_evaluation_enabled)

        for ambiguous in ("1", "yes", "on", "none"):
            with self.subTest(ambiguous=ambiguous):
                with self.assertRaises(SettingsError):
                    AdvisorySettings.from_mapping(
                        {
                            **required_settings(),
                            "DEEPEVAL_RUNTIME_ADVISORY_ENABLED": ambiguous,
                        }
                    )

    def test_settings_parse_all_exposed_integer_values_strictly(self) -> None:
        settings = AdvisorySettings.from_mapping(
            {
                **required_settings(),
                "DEEPEVAL_SCAN_INTERVAL_SECONDS": "5",
                "DEEPEVAL_WORKER_CONCURRENCY": "4",
                "DEEPEVAL_LEASE_TTL_SECONDS": "3600",
                "DEEPEVAL_LEASE_HEARTBEAT_SECONDS": "300",
                "DEEPEVAL_MAX_RUN_BYTES": "1024",
                "DEEPEVAL_MAX_PACK_BYTES": "2048",
            }
        )
        self.assertEqual(settings.scan_interval_seconds, 5)
        self.assertEqual(settings.worker_concurrency, 4)
        self.assertEqual(settings.lease_ttl_seconds, 3600)
        self.assertEqual(settings.lease_heartbeat_seconds, 300)
        self.assertEqual(settings.max_run_bytes, 1024)
        self.assertEqual(settings.max_pack_bytes, 2048)

        for name in (
            "DEEPEVAL_SCAN_INTERVAL_SECONDS",
            "DEEPEVAL_WORKER_CONCURRENCY",
            "DEEPEVAL_LEASE_TTL_SECONDS",
            "DEEPEVAL_LEASE_HEARTBEAT_SECONDS",
            "DEEPEVAL_MAX_RUN_BYTES",
            "DEEPEVAL_MAX_PACK_BYTES",
        ):
            with self.subTest(name=name):
                with self.assertRaises(SettingsError):
                    AdvisorySettings.from_mapping(
                        {
                            **required_settings(),
                            name: "1.0",
                        }
                    )

    def test_settings_enforce_integer_bounds_and_required_paths(self) -> None:
        invalid_bounds = {
            "DEEPEVAL_SCAN_INTERVAL_SECONDS": "4",
            "DEEPEVAL_WORKER_CONCURRENCY": "5",
            "DEEPEVAL_LEASE_TTL_SECONDS": "59",
            "DEEPEVAL_LEASE_HEARTBEAT_SECONDS": "301",
            "DEEPEVAL_MAX_RUN_BYTES": "1023",
            "DEEPEVAL_MAX_PACK_BYTES": "1023",
        }
        for name, value in invalid_bounds.items():
            with self.subTest(name=name):
                with self.assertRaises(SettingsError):
                    AdvisorySettings.from_mapping(
                        {
                            **required_settings(),
                            name: value,
                        }
                    )

        with self.assertRaises(SettingsError):
            AdvisorySettings.from_mapping(
                {
                    **required_settings(),
                    "ANALYSIS_RUN_DIR": " ",
                }
            )

    def test_settings_are_frozen_and_reject_extra_model_fields(self) -> None:
        settings = AdvisorySettings.from_mapping(required_settings())

        with self.assertRaises(ValidationError):
            settings.worker_concurrency = 2
        with self.assertRaises(ValidationError):
            AdvisorySettings.model_validate(
                {
                    **settings.model_dump(),
                    "unknown": True,
                }
            )


if __name__ == "__main__":
    unittest.main()
