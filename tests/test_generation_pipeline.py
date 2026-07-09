import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import main as main_module
from app.generation.base import ReportGenerationResult
from app.generation.dify_generator import DifyReportGenerator
from app.quality_gate import QualityGate
from app.repair_pipeline import RepairPipeline


class GenerationPipelineTests(unittest.TestCase):
    def test_dify_result_is_normalized_to_provider_result(self) -> None:
        raw = {
            "workflow_run_id": "workflow-123",
            "status": "finished",
            "report_title": "测试报告",
            "report_markdown": "## 核心结论\n\n报告正文。",
            "quality_check": {"passed": True, "issues": []},
            "remaining_issues": [],
            "generation_warnings": ["warn"],
            "raw_response_hash": "abc123",
            "raw_response_excerpt": "excerpt",
        }

        result = ReportGenerationResult.from_legacy_result(raw, provider="dify")
        legacy = result.to_legacy_result()

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "dify")
        self.assertEqual(result.provider_run_id, "workflow-123")
        self.assertEqual(legacy["workflow_run_id"], "workflow-123")
        self.assertEqual(legacy["provider_run_id"], "workflow-123")
        self.assertEqual(legacy["report_markdown"], raw["report_markdown"])

    def test_dify_generator_wraps_legacy_workflow_callable(self) -> None:
        calls = []

        def fake_call(pack_id: str, run_id: str, pack: dict, report_memory: str, use_report_memory: bool = False) -> dict:
            calls.append((pack_id, run_id, pack, report_memory, use_report_memory))
            return {
                "workflow_run_id": "workflow-456",
                "status": "finished",
                "report_title": "Dify 报告",
                "report_markdown": "## 分析\n\n正文。",
                "quality_check": {"passed": True, "issues": []},
            }

        generator = DifyReportGenerator(fake_call)
        result = generator.generate(
            pack_id="pack-1",
            run_id="run-1",
            pack={"pack_id": "pack-1"},
            report_memory="style",
            use_report_memory=True,
        )

        self.assertEqual(generator.provider, "dify")
        self.assertEqual(result.provider, "dify")
        self.assertEqual(result.provider_run_id, "workflow-456")
        self.assertEqual(calls, [("pack-1", "run-1", {"pack_id": "pack-1"}, "style", True)])

    def test_quality_gate_and_repair_pipeline_accept_any_provider_result(self) -> None:
        provider_result = ReportGenerationResult(
            success=True,
            provider="test-provider",
            provider_run_id="provider-run-1",
            report_title="通用报告",
            report_markdown="## 结论\n\n正文。",
            quality_check={"passed": True, "issues": []},
        )
        pack = {"pack_id": "pack-1", "primary_materials": [{"title": "材料", "content_text": "正文"}]}

        repaired = RepairPipeline().run(provider_result, pack)
        gated = QualityGate().run(repaired, pack)

        self.assertEqual(repaired.provider, "test-provider")
        self.assertEqual(gated.provider, "test-provider")
        self.assertIn("quality_gate", gated.metadata)

    def test_background_run_uses_configured_generator_abstraction(self) -> None:
        pack = {
            "pack_id": "pack_abstract",
            "primary_materials": [{"title": "主材料", "content_text": "主材料正文。" * 80, "attachments": []}],
            "auxiliary_materials": [],
            "warnings": [],
        }
        record = {
            "success": True,
            "run_id": "run_abstract",
            "pack_id": "pack_abstract",
            "status": "running",
            "workflow_run_id": "",
            "report_title": "",
            "report_markdown": "",
            "quality_check": {"passed": None, "issues": []},
            "generation_warnings": [],
            "warnings": [],
            "remaining_issues": [],
            "version": 1,
        }

        def fake_generate(pack_id: str, run_id: str, pack: dict | None, report_memory: str, use_report_memory: bool = False) -> ReportGenerationResult:
            return ReportGenerationResult(
                success=True,
                provider="dify",
                provider_run_id="workflow-from-generator",
                report_title="抽象生成报告",
                report_markdown="## 核心结论\n\n主材料正文。",
                quality_check={"passed": True, "issues": []},
                metadata={"status": "finished", "workflow_run_id": "workflow-from-generator"},
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "runs"
            pack_dir = root / "packs"
            with patch.object(main_module, "_analysis_run_dir", return_value=run_dir, create=True), patch.object(
                main_module, "_database_evidence_pack_dir", return_value=pack_dir, create=True
            ), patch.object(main_module, "_configured_report_generator") as generator_factory, patch.dict(
                os.environ,
                {"REPORT_GENERATOR": "dify", "NATIVE_GENERATOR_SHADOW": "false", "DIFY_GENERATOR_FALLBACK": "false"},
                clear=False,
            ):
                fake_generator = unittest.mock.Mock()
                fake_generator.generate.side_effect = fake_generate
                fake_generator.provider = "dify"
                generator_factory.return_value = fake_generator
                main_module._write_database_evidence_pack(pack)
                main_module._write_analysis_run(record)

                main_module._execute_analysis_run_background("pack_abstract", "run_abstract")
                saved = main_module._read_analysis_run("run_abstract")

        self.assertEqual(saved["provider"], "dify")
        self.assertEqual(saved["workflow_run_id"], "workflow-from-generator")
        self.assertEqual(saved["provider_run_id"], "workflow-from-generator")
        self.assertGreater(len(saved["report_markdown"]), 0)
        fake_generator.generate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
