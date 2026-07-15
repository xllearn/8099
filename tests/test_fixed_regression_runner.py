from __future__ import annotations

import unittest
from pathlib import Path

import httpx


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "8099_regression_cases.json"


class FixedRegressionRunnerTests(unittest.TestCase):
    def test_safe_request_retries_stale_keepalive_disconnect(self) -> None:
        from scripts import run_fixed_regression as runner

        class RequestClient:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, method: str, path: str, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise httpx.RemoteProtocolError(
                        "Server disconnected without sending a response."
                    )
                return httpx.Response(
                    200,
                    json={"success": True},
                    request=httpx.Request(method, f"http://test{path}"),
                )

        client = RequestClient()

        response = runner._request_with_transport_retry(
            client,
            "POST",
            "/analysis/prepare",
            json={"primary_materials": []},
            timeout=300,
        )

        self.assertEqual(2, client.calls)
        self.assertEqual(200, response.status_code)

    def test_analysis_status_read_timeout_is_retryable(self) -> None:
        from scripts import run_fixed_regression as runner

        class StatusClient:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, path: str, timeout: int):
                self.calls += 1
                if self.calls == 1:
                    raise httpx.ReadTimeout("timed out")
                return httpx.Response(
                    200,
                    json={"status": "finished"},
                    request=httpx.Request("GET", f"http://test{path}"),
                )

        client = StatusClient()

        self.assertIsNone(runner._read_analysis_state(client, "run-1"))
        self.assertEqual(
            {"status": "finished"},
            runner._read_analysis_state(client, "run-1"),
        )

    def test_run_diagnostics_retries_transport_timeout_with_slow_endpoint_budget(self) -> None:
        from scripts import run_fixed_regression as runner

        class DiagnosticsClient:
            def __init__(self) -> None:
                self.calls = 0
                self.timeouts: list[int] = []

            def request(self, method: str, path: str, **kwargs):
                self.calls += 1
                self.timeouts.append(kwargs["timeout"])
                if self.calls == 1:
                    raise httpx.ReadTimeout("timed out")
                return httpx.Response(
                    200,
                    json={"success": True, "diagnostics": {}},
                    request=httpx.Request(method, f"http://test{path}"),
                )

        client = DiagnosticsClient()

        result = runner._read_run_diagnostics(client, "run-1")

        self.assertEqual({"success": True, "diagnostics": {}}, result)
        self.assertEqual(2, client.calls)
        self.assertEqual([300, 300], client.timeouts)

    def test_fixed3_manifest_contains_only_expected_project_notices(self) -> None:
        from scripts import run_fixed_regression as runner

        cases = runner.load_cases(FIXTURE_PATH)

        self.assertEqual(len(cases), 3)
        self.assertEqual({case["menu_code"] for case in cases}, {"project_notice"})
        self.assertEqual(
            {case["articleid"] for case in cases},
            {
                "803492fa-839b-4fff-aa7c-e9c1d9a3028a",
                "4695e7d8-9d04-4c5a-b198-00ed022bf413",
                "4ca0eae9-bb5f-4301-8d1a-ee4d85cc30b6",
            },
        )

    def test_runner_accepts_disabled_word_as_expected_contract(self) -> None:
        from scripts import run_fixed_regression as runner

        runner.validate_disabled_word_contract(
            {
                "word_export_available": False,
                "draft_word_export_available": False,
                "final_word_export_available": False,
                "word_download_url": "",
                "download_url": "",
                "word_filename": "",
                "word_generated": False,
                "word_exported_at": "",
            },
            {
                "run_download": 503,
                "report_export": 503,
                "report_export_checked": 503,
                "file_download": 503,
            },
            [],
        )

    def test_render_probe_uses_valid_report_ir_instead_of_generated_markdown(self) -> None:
        from scripts import run_fixed_regression as runner

        payload = runner.build_render_probe_payload()

        self.assertIn("report_ir", payload)
        self.assertNotIn("markdown", payload)
        self.assertTrue(payload["report_ir"]["title"])
        self.assertTrue(payload["report_ir"]["sections"])
        self.assertFalse(payload["strict_quality"])

    def test_runner_requires_word_export_enabled_for_b1_5(self) -> None:
        from scripts import run_fixed_regression as runner

        runner.validate_expected_word_export(True, "enabled")
        with self.assertRaises(ValueError):
            runner.validate_expected_word_export(False, "enabled")

    def test_formal_export_probe_wraps_real_run_body_and_real_evidence_pack(self) -> None:
        from scripts import run_fixed_regression as runner

        payload = runner.build_formal_export_payload(
            "# 真实报告\n\n公告明确了执行要求。",
            "真实报告",
            {"pack_id": "pack_real", "primary_materials": [{"content_text": "公告明确了执行要求。"}]},
        )

        self.assertNotIn("markdown", payload)
        self.assertIn("公告明确了执行要求。", payload["report_ir"]["lead_paragraphs"])
        self.assertIn("pack_real", payload["evidence_text"])

    def test_runner_rejects_any_download_url_or_new_docx_when_disabled(self) -> None:
        from scripts import run_fixed_regression as runner

        safe_payload = {
            "word_export_available": False,
            "draft_word_export_available": False,
            "final_word_export_available": False,
            "word_download_url": "",
            "download_url": "",
            "word_filename": "",
            "word_generated": False,
            "word_exported_at": "",
        }
        endpoint_statuses = {
            "run_download": 503,
            "report_export": 503,
            "report_export_checked": 503,
            "file_download": 503,
        }

        for field in (
            "word_download_url",
            "download_url",
            "word_filename",
            "word_file_path",
            "word_path",
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                runner.validate_disabled_word_contract(
                    {**safe_payload, field: "http://example.test/download/report.docx"},
                    endpoint_statuses,
                    [],
                )

        with self.assertRaises(ValueError):
            runner.validate_disabled_word_contract(safe_payload, endpoint_statuses, ["new-report.docx"])

    def test_runner_accepts_enabled_deliverable_word_contract(self) -> None:
        from scripts import run_fixed_regression as runner

        runner.validate_enabled_word_contract(
            {
                "deliverable": True,
                "needs_manual_review": False,
                "word_export_available": True,
                "draft_word_export_available": True,
                "final_word_export_available": True,
            },
            {
                "run_download": 200,
                "report_export": 200,
                "report_export_checked": 200,
                "file_download": 200,
            },
            ["run.docx", "export.docx", "checked.docx"],
            {"run.docx": [], "export.docx": [], "checked.docx": []},
            [],
        )

    def test_runner_accepts_enabled_manual_review_draft_contract(self) -> None:
        from scripts import run_fixed_regression as runner

        runner.validate_enabled_word_contract(
            {
                "deliverable": False,
                "needs_manual_review": True,
                "word_export_available": True,
                "draft_word_export_available": True,
                "final_word_export_available": False,
            },
            {
                "run_download": 200,
                "report_export": 200,
                "report_export_checked": 200,
                "file_download": 200,
            },
            ["run.docx"],
            {"run.docx": []},
            [],
        )

    def test_runner_rejects_enabled_word_contradiction_hits_or_staging_residue(self) -> None:
        from scripts import run_fixed_regression as runner

        payload = {
            "deliverable": True,
            "needs_manual_review": False,
            "word_export_available": True,
            "draft_word_export_available": True,
            "final_word_export_available": True,
        }
        statuses = {
            "run_download": 200,
            "report_export": 200,
            "report_export_checked": 200,
            "file_download": 200,
        }
        with self.assertRaises(ValueError):
            runner.validate_enabled_word_contract(
                {**payload, "draft_word_export_available": False},
                statuses,
                ["run.docx"],
                {"run.docx": []},
                [],
            )
        with self.assertRaises(ValueError):
            runner.validate_enabled_word_contract(
                payload,
                statuses,
                ["run.docx"],
                {"run.docx": ["需人工核验"]},
                [],
            )
        with self.assertRaises(ValueError):
            runner.validate_enabled_word_contract(
                payload,
                statuses,
                ["run.docx"],
                {"run.docx": []},
                [".word-staging-leaked"],
            )
        with self.assertRaises(ValueError):
            runner.validate_enabled_word_contract(
                payload,
                statuses,
                ["run.docx"],
                {"run.docx": []},
                [],
                formal_body_hits=["需人工核验"],
            )

    def test_docx_observation_requires_existing_report_directory(self) -> None:
        from scripts import run_fixed_regression as runner

        with self.assertRaises(ValueError):
            runner._docx_names(None)
        with self.assertRaises(ValueError):
            runner._docx_names(Path("missing-report-directory"))

    def test_s2_quality_metrics_capture_support_repair_and_new_facts(self) -> None:
        from scripts import run_fixed_regression as runner

        metrics = runner._s2_quality_metrics(
            {
                "claim_evidence_index": {
                    "metrics": {
                        "claim_count": 4,
                        "supported_claim_count": 4,
                        "unsupported_claim_count": 0,
                        "ab_support_rate": 1.0,
                        "c_independent_support_count": 0,
                    }
                },
                "vbp_quality_gate": {
                    "applicable": True,
                    "claim_count": 4,
                    "supported_claim_count": 4,
                    "claim_ab_support_rate": 1.0,
                    "c_independent_support_count": 0,
                },
                "repair_attempted": True,
                "repair_success": True,
                "repair_count": 1,
                "repair_new_fact_count": 0,
                "repair_new_fact_observed": True,
            }
        )

        self.assertTrue(metrics["observed"])
        self.assertEqual(4, metrics["claim_count"])
        self.assertEqual(4, metrics["supported_claim_count"])
        self.assertEqual(1.0, metrics["claim_ab_support_rate"])
        self.assertEqual(0, metrics["c_independent_support_count"])
        self.assertEqual(1, metrics["repair_count"])
        self.assertEqual(0, metrics["new_fact_count"])

    def test_s2_quality_metrics_reject_repair_count_above_one(self) -> None:
        from scripts import run_fixed_regression as runner

        with self.assertRaisesRegex(ValueError, "repair_count"):
            runner._s2_quality_metrics(
                {
                    "claim_evidence_index": {
                        "metrics": {
                            "claim_count": 1,
                            "supported_claim_count": 1,
                            "unsupported_claim_count": 0,
                        }
                    },
                    "repair_count": 2,
                }
            )

    def test_s2_quality_metrics_reject_hidden_observed_repair_overflow(self) -> None:
        from scripts import run_fixed_regression as runner

        with self.assertRaisesRegex(ValueError, "repair_count"):
            runner._s2_quality_metrics(
                {
                    "claim_evidence_index": {
                        "metrics": {
                            "claim_count": 1,
                            "supported_claim_count": 1,
                            "unsupported_claim_count": 0,
                        }
                    },
                    "repair_attempted": True,
                    "repair_success": True,
                    "repair_count": 1,
                    "repair_count_observed": 2,
                    "repair_count_violation": True,
                }
            )

    def test_s2_stage_rejects_sample_without_quality_observation(self) -> None:
        from scripts import run_fixed_regression as runner

        with self.assertRaisesRegex(ValueError, "S2 quality observation"):
            runner.require_s2_quality_observation(
                "S2",
                {"s2_quality_observed": False},
            )

    def test_s2_quality_metrics_require_new_fact_observation_after_repair(self) -> None:
        from scripts import run_fixed_regression as runner

        with self.assertRaisesRegex(ValueError, "new fact observation"):
            runner._s2_quality_metrics(
                {
                    "claim_evidence_index": {
                        "metrics": {
                            "claim_count": 1,
                            "supported_claim_count": 1,
                            "unsupported_claim_count": 0,
                        }
                    },
                    "repair_attempted": True,
                    "repair_success": True,
                    "repair_count": 1,
                    "repair_new_fact_count": 0,
                    "repair_new_fact_observed": False,
                }
            )


if __name__ == "__main__":
    unittest.main()
