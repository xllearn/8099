from __future__ import annotations

import httpx
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import app.main as main_module
from app.main import (
    AnalyzeV2Request,
    CheckedExportReportRequest,
    DownloadedFile,
    RenderReportRequest,
    ReportIR,
    ReportSection,
    ReportTable,
    export_report_checked,
)


def sample_report_ir() -> ReportIR:
    return ReportIR(
        title="Medical procurement notice report",
        suggested_filename="medical-procurement-notice-report",
        notice_type="procurement",
        publish_date="2026-06-10",
        source_agency="Procurement Agency",
        document_name="Procurement Notice",
        lead_paragraphs=[
            "The notice discloses procurement scope, reporting requirements, and execution rules for suppliers."
        ],
        sections=[
            ReportSection(
                heading="Procurement Rules",
                paragraphs=[
                    "Suppliers should follow the disclosed reporting path, deadline, and price requirements."
                ],
                tables=[
                    ReportTable(
                        title="Key Schedule",
                        headers=["Item", "Requirement"],
                        rows=[["Registration", "Submit before the disclosed deadline"]],
                    )
                ],
            )
        ],
        enterprise_tips=["Check registration documents and price evidence before submission."],
        disclaimer="",
    )


class DeploymentConfigTests(unittest.TestCase):
    def test_health_reports_deployment_diagnostics_without_secrets(self) -> None:
        response = main_module.health()

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["service"], "medical-notice-analyzer")
        self.assertIn("version", response)
        self.assertIn("public_base_url_configured", response)
        self.assertIn("report_dir_configured", response)
        self.assertNotIn("FIRECRAWL_API_KEY", response)
        self.assertNotIn("ELIAN_QX_COOKIE", response)

    def test_download_url_uses_public_base_url_and_defaults_to_company_backend(self) -> None:
        old_base_url = os.environ.get("PUBLIC_BASE_URL")
        try:
            os.environ.pop("PUBLIC_BASE_URL", None)
            self.assertEqual(
                main_module._download_url("report.docx"),
                "http://192.168.34.88:8099/download/report.docx",
            )

            os.environ["PUBLIC_BASE_URL"] = "http://example.internal:18099/"
            self.assertEqual(
                main_module._download_url("report.docx"),
                "http://example.internal:18099/download/report.docx",
            )
        finally:
            if old_base_url is None:
                os.environ.pop("PUBLIC_BASE_URL", None)
            else:
                os.environ["PUBLIC_BASE_URL"] = old_base_url


class EvidencePackV2Tests(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_v2_returns_evidence_pack_for_html_page(self) -> None:
        async def fake_fetch(_: str) -> dict[str, str]:
            return {
                "html": """
                <html><head><title>Notice Title</title></head>
                <body><article><p>Published: 2026-06-10</p><p>Procurement rule text.</p></article></body></html>
                """,
                "final_url": "https://example.com/notice",
            }

        async def no_site_detail(_: str, __: list[str]) -> dict[str, str] | None:
            return None

        async def no_firecrawl(*_: object) -> str:
            return ""

        original_fetch = main_module._fetch_page
        original_site_detail = main_module._try_known_site_detail
        original_firecrawl = main_module._try_firecrawl
        try:
            main_module._fetch_page = fake_fetch
            main_module._try_known_site_detail = no_site_detail
            main_module._try_firecrawl = no_firecrawl
            response = await main_module.analyze_v2(
                AnalyzeV2Request(url="https://example.com/notice", max_attachments=0)
            )
        finally:
            main_module._fetch_page = original_fetch
            main_module._try_known_site_detail = original_site_detail
            main_module._try_firecrawl = original_firecrawl

        self.assertTrue(response.success, response.message)
        self.assertEqual(response.fetch_status["main_page"], "success")
        self.assertEqual(response.evidence_pack["source_url"], "https://example.com/notice")
        self.assertIn("Procurement rule text", response.llm_input_text)
        self.assertTrue(response.evidence_id)
        self.assertEqual(response.evidence_id, response.content_hash)

    async def test_analyze_v2_fetch_failure_returns_json_without_exception(self) -> None:
        async def failing_fetch(_: str) -> dict[str, str]:
            request = httpx.Request("GET", "https://example.com/blocked")
            response = httpx.Response(403, request=request)
            raise httpx.HTTPStatusError("Forbidden", request=request, response=response)

        async def no_site_detail(_: str, __: list[str]) -> dict[str, str] | None:
            return None

        async def no_firecrawl(*_: object) -> str:
            return ""

        original_fetch = main_module._fetch_page
        original_site_detail = main_module._try_known_site_detail
        original_firecrawl = main_module._try_firecrawl
        original_load_cached_page = main_module._load_cached_page
        try:
            main_module._fetch_page = failing_fetch
            main_module._try_known_site_detail = no_site_detail
            main_module._try_firecrawl = no_firecrawl
            main_module._load_cached_page = lambda *_: None
            response = await main_module.analyze_v2(
                AnalyzeV2Request(url="https://example.com/blocked", max_attachments=0)
            )
        finally:
            main_module._fetch_page = original_fetch
            main_module._try_known_site_detail = original_site_detail
            main_module._try_firecrawl = original_firecrawl
            main_module._load_cached_page = original_load_cached_page

        self.assertFalse(response.success)
        self.assertEqual(response.error_type, "fetch_forbidden")
        self.assertEqual(response.fetch_status["main_page"], "failed")
        self.assertEqual(response.llm_input_text, "")
        self.assertIn("无法抓取", response.message)

    async def test_analyze_v2_large_csv_uses_table_summary_not_full_rows(self) -> None:
        async def fake_fetch(_: str) -> dict[str, str]:
            return {
                "html": '<html><body><h1>Large CSV Notice</h1><a href="/large.csv">attachment</a></body></html>',
                "final_url": "https://example.com/notice",
            }

        async def no_site_detail(_: str, __: list[str]) -> dict[str, str] | None:
            return None

        async def no_firecrawl(*_: object) -> str:
            return ""

        async def fake_download(url: str) -> DownloadedFile:
            rows = ["product,company,price,category"]
            rows.extend(f"product-{i},company-{i % 17},{i % 100 + 0.5},cat-{i % 5}" for i in range(30050))
            return DownloadedFile(url=url, filename="large.csv", content_type="text/csv", content=("\n".join(rows)).encode())

        original_fetch = main_module._fetch_page
        original_site_detail = main_module._try_known_site_detail
        original_firecrawl = main_module._try_firecrawl
        original_download = main_module._download_attachment
        original_discover = main_module._discover_attachment_links
        try:
            main_module._fetch_page = fake_fetch
            main_module._try_known_site_detail = no_site_detail
            main_module._try_firecrawl = no_firecrawl
            main_module._download_attachment = fake_download
            main_module._discover_attachment_links = lambda *_: [{"url": "https://example.com/large.csv", "text": "large"}]
            response = await main_module.analyze_v2(
                AnalyzeV2Request(url="https://example.com/notice", max_attachments=1, max_combined_chars=20_000)
            )
        finally:
            main_module._fetch_page = original_fetch
            main_module._try_known_site_detail = original_site_detail
            main_module._try_firecrawl = original_firecrawl
            main_module._download_attachment = original_download
            main_module._discover_attachment_links = original_discover

        self.assertTrue(response.success, response.message)
        table_summary = response.evidence_pack["tables_summary"][0]
        self.assertEqual(table_summary["row_count"], 30051)
        self.assertLessEqual(len(table_summary["important_rows"]), 50)
        self.assertIn("大表已结构化摘要", "\n".join(table_summary["warnings"]))
        self.assertLessEqual(len(response.llm_input_text), 20_000)
        self.assertNotIn("product-30049", response.llm_input_text)


class ReportV2GateTests(unittest.TestCase):
    def test_render_v2_parses_report_ir_tag_and_returns_warnings(self) -> None:
        raw = """
        Before text.
        <report_ir>{"title":"Test Medical Notice","suggested_filename":"test","notice_type":"notice","publish_date":"2026-06-10","source_agency":"agency","document_name":"doc","lead_paragraphs":["lead paragraph with enough text"],"sections":[{"heading":"Rules","paragraphs":["rule paragraph with enough text"],"tables":[]}],"enterprise_tips":[],"disclaimer":""}</report_ir>
        After text.
        """

        response = main_module.render_report_v2(RenderReportRequest(markdown=raw, strict_quality=True))

        self.assertTrue(response.success, response.error)
        self.assertIn("# Test Medical Notice", response.report_markdown)
        self.assertEqual(response.quality_warnings, [])

    def test_render_v2_coerces_enterprise_tip_objects_to_strings(self) -> None:
        raw = """
        <report_ir>{"title":"Test Medical Notice","lead_paragraphs":["lead paragraph with enough text"],"sections":[{"heading":"Rules","paragraphs":["rule paragraph with enough text"],"tables":[]}],"enterprise_tips":[{"tip":"Check price evidence."}]}</report_ir>
        """

        response = main_module.render_report_v2(RenderReportRequest(markdown=raw, strict_quality=True))

        self.assertTrue(response.success, response.error)
        self.assertEqual(response.report_ir.enterprise_tips, ["Check price evidence."])

    def test_qa_parse_response_exposes_status_and_needs_fix(self) -> None:
        response = main_module.parse_report_qa(
            main_module.ReportQAParseRequest(
                qa_output='{"status":"needs_fix","issues":[{"severity":"major","category":"fact","report_text":"x"}],"unsupported_claims":[],"history_leakage":[],"missing_rules":[],"language_issues":[],"fix_instructions":["fix x"],"summary":"major issue"}'
            )
        )

        self.assertEqual(response.status, "needs_fix")
        self.assertFalse(response.blocked)
        self.assertTrue(response.needs_fix)
        self.assertEqual(len(response.issues), 1)

    def test_qa_parse_blocker_sets_block(self) -> None:
        response = main_module.parse_report_qa(
            main_module.ReportQAParseRequest(
                qa_output='{"status":"pass","issues":[{"severity":"blocker","category":"fabrication","report_text":"x"}],"unsupported_claims":[],"history_leakage":[],"missing_rules":[],"language_issues":[],"fix_instructions":[],"summary":"blocker"}'
            )
        )

        self.assertEqual(response.status, "block")
        self.assertTrue(response.blocked)

    def test_qa_parse_clean_output_passes(self) -> None:
        response = main_module.parse_report_qa(
            main_module.ReportQAParseRequest(
                qa_output='{"status":"pass","issues":[],"unsupported_claims":[],"history_leakage":[],"missing_rules":[],"language_issues":[],"fix_instructions":[],"summary":"ok"}'
            )
        )

        self.assertEqual(response.status, "pass")
        self.assertFalse(response.blocked)
        self.assertFalse(response.needs_fix)

    def test_export_checked_blocks_when_strict_and_qa_status_not_pass(self) -> None:
        response = export_report_checked(
            CheckedExportReportRequest(
                report_ir=sample_report_ir(),
                qa_status="needs_fix",
                qa_result={"summary": "needs manual confirmation"},
                strict_quality=True,
            )
        )

        self.assertFalse(response.success)
        self.assertTrue(response.blocked)
        self.assertEqual(response.download_url, "")
        self.assertIn("needs manual confirmation", response.qa_summary)

    def test_export_checked_exports_when_qa_status_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(main_module, "REPORT_DIR", Path(tmpdir)):
            response = export_report_checked(
                CheckedExportReportRequest(
                    report_ir=sample_report_ir(),
                    qa_status="pass",
                    qa_result={"summary": "ok"},
                    strict_quality=True,
                )
            )

        self.assertTrue(response.success)
        self.assertFalse(response.blocked)
        self.assertIn("/download/", response.download_url)


class WorkflowYamlV2Tests(unittest.TestCase):
    def test_latest_pack_id_workflow_yaml_is_parseable(self) -> None:
        workflow_path = Path(__file__).resolve().parents[1] / "dify_workflow_pack_id_human_style.yml"

        data = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        node_ids = {node["id"] for node in data["workflow"]["graph"]["nodes"]}

        self.assertEqual(data["app"]["mode"], "workflow")
        self.assertEqual(data["app"]["name"], "医药公告采购分析报告_pack_id_人工风格")
        self.assertIn("start_node", node_ids)
        self.assertIn("fetch_evidence_pack", node_ids)
        self.assertIn("generate_report", node_ids)
        self.assertIn("qa_report_first", node_ids)
        self.assertIn("revise_report", node_ids)


if __name__ == "__main__":
    unittest.main()
