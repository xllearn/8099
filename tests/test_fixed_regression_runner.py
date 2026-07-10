from __future__ import annotations

import unittest
from pathlib import Path


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "8099_regression_cases.json"


class FixedRegressionRunnerTests(unittest.TestCase):
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

        for field in ("word_download_url", "download_url", "word_filename"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                runner.validate_disabled_word_contract(
                    {**safe_payload, field: "http://example.test/download/report.docx"},
                    endpoint_statuses,
                    [],
                )

        with self.assertRaises(ValueError):
            runner.validate_disabled_word_contract(safe_payload, endpoint_statuses, ["new-report.docx"])

    def test_docx_observation_requires_existing_report_directory(self) -> None:
        from scripts import run_fixed_regression as runner

        with self.assertRaises(ValueError):
            runner._docx_names(None)
        with self.assertRaises(ValueError):
            runner._docx_names(Path("missing-report-directory"))


if __name__ == "__main__":
    unittest.main()
