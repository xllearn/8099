from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path


def load_script_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_16case_report_regression.py"
    spec = importlib.util.spec_from_file_location("run_16case_report_regression", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class RegressionScriptTests(unittest.TestCase):
    def test_extract_report_markdown_uses_json_field_only(self) -> None:
        module = load_script_module()
        payload = {
            "success": True,
            "report_markdown": "# 报告\n\n正文内容",
            "quality_gate": {"deliverable_status": "needs_manual_review"},
        }

        markdown = module.extract_report_markdown(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        self.assertEqual(markdown, "# 报告\n\n正文内容")
        self.assertNotIn("success", markdown)
        self.assertNotIn("quality_gate", markdown)

    def test_quality_eval_summarizes_gate_and_analysis(self) -> None:
        module = load_script_module()
        diagnostics = {
            "diagnostics": {
                "quality_gate": {
                    "deliverable_status": "needs_manual_review",
                    "source_fidelity_score": 75,
                    "unsupported_fact_count": 1,
                    "analysis_depth_score": 40,
                    "summary_only_risk": True,
                    "evidence_backed_analysis_count": 0,
                    "blocking_issues": [{"code": "SUMMARY_ONLY_REPORT"}],
                },
                "coverage": {"coverage_score": 80},
            }
        }

        result = module.build_quality_eval("case01", diagnostics)

        self.assertEqual(result["case_id"], "case01")
        self.assertEqual(result["deliverable_status"], "needs_manual_review")
        self.assertEqual(result["source_fidelity_score"], 75)
        self.assertEqual(result["analysis_depth_score"], 40)
        self.assertIn("SUMMARY_ONLY_REPORT", result["blocking_issue_codes"])

    def test_quality_eval_marks_failed_run_when_diagnostics_missing(self) -> None:
        module = load_script_module()

        result = module.build_quality_eval("case16", {}, run_status="failed")

        self.assertEqual(result["deliverable_status"], "failed")
        self.assertIn("RUN_FAILED", result["blocking_issue_codes"])
        self.assertIn("DIAGNOSTICS_UNAVAILABLE", result["blocking_issue_codes"])

    def test_quality_eval_uses_run_state_when_diagnostics_missing_after_timeout_fallback(self) -> None:
        module = load_script_module()
        run_state = {
            "status": "needs_manual_review",
            "dify_error_code": "DIFY_TIMEOUT",
            "error_message": "watchdog timeout after 315.0s",
            "quality_gate": {
                "deliverable_status": "needs_manual_review",
                "source_fidelity_score": 0,
                "analysis_depth_score": 0,
                "summary_only_risk": False,
                "evidence_backed_analysis_count": 0,
                "blocking_issue_codes": ["DIFY_TIMEOUT"],
            },
        }

        result = module.build_quality_eval(
            "case15",
            {},
            run_status="needs_manual_review",
            run_state=run_state,
        )

        self.assertEqual(result["deliverable_status"], "needs_manual_review")
        self.assertEqual(result["source_fidelity_score"], 0)
        self.assertEqual(result["unsupported_fact_count"], 0)
        self.assertEqual(result["analysis_depth_score"], 0)
        self.assertIn("DIFY_TIMEOUT", result["blocking_issue_codes"])
        self.assertEqual(result["blocking_issues"][0]["code"], "DIFY_TIMEOUT")

    def test_clean_reference_text_removes_word_artifacts_and_disclaimer(self) -> None:
        module = load_script_module()
        text = (
            "PAGE 1 MERGEFORMAT\n"
            "本文基于互联网公开资料进行整理，目的在于传递分享信息，仅供读者参考之用。\n"
            "核心规则：企业需维护产品和价格。"
        )

        cleaned = module.clean_reference_text(text)

        self.assertNotIn("PAGE", cleaned)
        self.assertNotIn("MERGEFORMAT", cleaned)
        self.assertNotIn("互联网公开资料", cleaned)
        self.assertIn("核心规则", cleaned)

    def test_reference_texts_use_exact_manual_files_before_keywords(self) -> None:
        module = load_script_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            manual_dir = Path(tmpdir)
            exact = manual_dir / "exact.md"
            broad = manual_dir / "广东_其他.md"
            exact.write_text("exact reference", encoding="utf-8")
            broad.write_text("broad reference", encoding="utf-8")
            case = {"manual_files": [str(exact)], "manual_keywords": ["广东"]}

            refs = module.reference_texts_for_case(case, manual_dir)

        self.assertEqual(len(refs), 1)
        self.assertEqual(Path(refs[0][0]).name, "exact.md")
        self.assertEqual(refs[0][1], "exact reference")

    def test_failed_plus_short_case_set_keeps_failed_cases_and_adds_short_cases(self) -> None:
        module = load_script_module()
        manifest_cases = [
            {"id": "case01_1p0_original_guangdong_mzgl", "primary": [{"menu_code": "m", "articleid": "a1"}], "auxiliary": []},
            {"id": "case02_other", "primary": [{"menu_code": "m", "articleid": "a2"}], "auxiliary": []},
            {"id": "case15_2p0_two_long_primary", "primary": [{"menu_code": "m", "articleid": "a15"}], "auxiliary": []},
            {"id": "case16_2pn_complex_many_rules", "primary": [{"menu_code": "m", "articleid": "a16"}], "auxiliary": []},
        ]
        short_cases = [
            {"id": "case17_1p0_short_body_no_attachment", "primary": [{"menu_code": "m", "articleid": "s1"}], "auxiliary": []},
            {"id": "case18_1p0_short_body_attachment_led", "primary": [{"menu_code": "m", "articleid": "s2"}], "auxiliary": []},
        ]

        cases = module.build_failed_plus_short_cases(manifest_cases, short_cases)

        self.assertEqual([case["id"] for case in cases], [
            "case01_1p0_original_guangdong_mzgl",
            "case15_2p0_two_long_primary",
            "case16_2pn_complex_many_rules",
            "case17_1p0_short_body_no_attachment",
            "case18_1p0_short_body_attachment_led",
        ])

    def test_short_boundary_cases_can_be_reused_from_existing_manifest(self) -> None:
        module = load_script_module()
        manifest_cases = [
            {"id": "case18_1p0_short_body_attachment_led", "primary": [{"menu_code": "m", "articleid": "s2"}], "auxiliary": []},
            {"id": "case01_1p0_original_guangdong_mzgl", "primary": [{"menu_code": "m", "articleid": "a1"}], "auxiliary": []},
            {"id": "case17_1p0_short_body_no_attachment", "primary": [{"menu_code": "m", "articleid": "s1"}], "auxiliary": []},
        ]

        cases = module.short_boundary_cases_from_manifest(manifest_cases)

        self.assertEqual([case["id"] for case in cases], [
            "case17_1p0_short_body_no_attachment",
            "case18_1p0_short_body_attachment_led",
        ])

    def test_prepare_cases_reuses_manifest_materials_without_record_detail_fetches(self) -> None:
        module = load_script_module()

        class FailingClient:
            def json(self, *args, **kwargs):
                raise AssertionError("record detail fetch should not be needed")

        def case(case_id: str, articleid: str) -> dict:
            return {
                "id": case_id,
                "combo": "1+0",
                "name": case_id,
                "primary": [{"menu_code": "project_information", "articleid": articleid}],
                "auxiliary": [],
                "materials": {
                    "primary": [{"menu_code": "project_information", "articleid": articleid, "content_chars": 100, "attachment_count": 0}],
                    "auxiliary": [],
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "case_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "cases": [
                            case("case01_1p0_original_guangdong_mzgl", "a1"),
                            case("case15_2p0_two_long_primary", "a15"),
                            case("case16_2pn_complex_many_rules", "a16"),
                            case("case17_1p0_short_body_no_attachment", "s1"),
                            case("case18_1p0_short_body_attachment_led", "s2"),
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            cases = module.prepare_cases(FailingClient(), case_set="failed-plus-short", source_manifest=str(manifest))

        self.assertEqual(len(cases), 5)
        self.assertEqual(cases[0]["materials"]["primary"][0]["articleid"], "a1")
        self.assertEqual(cases[-1]["materials"]["primary"][0]["articleid"], "s2")

    def test_run_case_continues_after_transient_status_timeout(self) -> None:
        module = load_script_module()

        class FakeClient:
            def __init__(self) -> None:
                self.status_calls = 0

            def json(self, method, path, payload=None, timeout=60):
                if method == "POST" and path == "/analysis/prepare":
                    return {"pack_id": "pack_1"}
                if method == "POST" and path == "/analysis/run":
                    return {"run_id": "run_1"}
                if method == "GET" and path == "/analysis/runs/run_1":
                    self.status_calls += 1
                    if self.status_calls == 1:
                        raise TimeoutError("timed out")
                    return {"status": "finished", "run_id": "run_1", "pack_id": "pack_1"}
                if method == "GET" and path == "/analysis/runs/run_1/diagnostics":
                    return {"diagnostics": {"quality_gate": {"deliverable_status": "deliverable"}}}
                raise AssertionError(f"unexpected request: {method} {path}")

            def bytes(self, path, timeout=60):
                if path == "/analysis/runs/run_1/report":
                    return json.dumps({"report_markdown": "# report"}, ensure_ascii=False).encode("utf-8")
                if path == "/analysis/runs/run_1/download":
                    return b"docx"
                raise AssertionError(f"unexpected bytes request: {path}")

        original_sleep = module.time.sleep
        module.time.sleep = lambda _seconds: None
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                result = module.run_case(
                    FakeClient(),
                    {
                        "id": "case_timeout",
                        "name": "status timeout",
                        "combo": "1+0",
                        "reason": "test",
                        "primary": [{"menu_code": "m", "articleid": "a"}],
                        "auxiliary": [],
                    },
                    Path(tmpdir),
                    1,
                    1,
                )
        finally:
            module.time.sleep = original_sleep

        self.assertEqual(result["status"], "finished")
        self.assertEqual(result["quality_eval"]["deliverable_status"], "deliverable")

    def test_run_case_retries_transient_prepare_503(self) -> None:
        module = load_script_module()

        class FakeClient:
            def __init__(self) -> None:
                self.prepare_calls = 0

            def json(self, method, path, payload=None, timeout=60):
                if method == "POST" and path == "/analysis/prepare":
                    self.prepare_calls += 1
                    if self.prepare_calls == 1:
                        raise urllib.error.HTTPError(path, 503, "Service Unavailable", {}, None)
                    return {"pack_id": "pack_1"}
                if method == "POST" and path == "/analysis/run":
                    return {"run_id": "run_1"}
                if method == "GET" and path == "/analysis/runs/run_1":
                    return {"status": "finished", "run_id": "run_1", "pack_id": "pack_1"}
                if method == "GET" and path == "/analysis/runs/run_1/diagnostics":
                    return {"diagnostics": {"quality_gate": {"deliverable_status": "deliverable"}}}
                raise AssertionError(f"unexpected request: {method} {path}")

            def bytes(self, path, timeout=60):
                if path == "/analysis/runs/run_1/report":
                    return json.dumps({"report_markdown": "# report"}, ensure_ascii=False).encode("utf-8")
                if path == "/analysis/runs/run_1/download":
                    return b"docx"
                raise AssertionError(f"unexpected bytes request: {path}")

        original_sleep = module.time.sleep
        module.time.sleep = lambda _seconds: None
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                result = module.run_case(
                    FakeClient(),
                    {
                        "id": "case_retry",
                        "name": "prepare retry",
                        "combo": "1+0",
                        "reason": "test",
                        "primary": [{"menu_code": "m", "articleid": "a"}],
                        "auxiliary": [],
                    },
                    Path(tmpdir),
                    1,
                    1,
                )
        finally:
            module.time.sleep = original_sleep

        self.assertEqual(result["status"], "finished")
        self.assertEqual(result["quality_eval"]["deliverable_status"], "deliverable")

    def test_run_case_retries_transient_report_timeout(self) -> None:
        module = load_script_module()

        class FakeClient:
            def __init__(self) -> None:
                self.report_calls = 0

            def json(self, method, path, payload=None, timeout=60):
                if method == "POST" and path == "/analysis/prepare":
                    return {"pack_id": "pack_1"}
                if method == "POST" and path == "/analysis/run":
                    return {"run_id": "run_1"}
                if method == "GET" and path == "/analysis/runs/run_1":
                    return {"status": "finished", "run_id": "run_1", "pack_id": "pack_1"}
                if method == "GET" and path == "/analysis/runs/run_1/diagnostics":
                    return {"diagnostics": {"quality_gate": {"deliverable_status": "deliverable"}}}
                raise AssertionError(f"unexpected request: {method} {path}")

            def bytes(self, path, timeout=60):
                if path == "/analysis/runs/run_1/report":
                    self.report_calls += 1
                    if self.report_calls == 1:
                        raise TimeoutError("timed out")
                    return json.dumps({"report_markdown": "# report"}, ensure_ascii=False).encode("utf-8")
                if path == "/analysis/runs/run_1/download":
                    return b"docx"
                raise AssertionError(f"unexpected bytes request: {path}")

        client = FakeClient()
        original_sleep = module.time.sleep
        module.time.sleep = lambda _seconds: None
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                case_dir = Path(tmpdir)
                result = module.run_case(
                    client,
                    {
                        "id": "case_report_timeout",
                        "name": "report timeout",
                        "combo": "1+0",
                        "reason": "test",
                        "primary": [{"menu_code": "m", "articleid": "a"}],
                        "auxiliary": [],
                    },
                    case_dir,
                    1,
                    1,
                )
                report_text = (case_dir / "system_report_extracted.md").read_text(encoding="utf-8")
        finally:
            module.time.sleep = original_sleep

        self.assertEqual(result["status"], "finished")
        self.assertEqual(client.report_calls, 2)
        self.assertEqual(report_text, "# report")
        self.assertEqual(result["quality_eval"]["deliverable_status"], "deliverable")

if __name__ == "__main__":
    unittest.main()
