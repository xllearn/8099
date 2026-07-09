from __future__ import annotations

import json
import subprocess
import sys
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_dify_chatflow_dsl.py"
IMPORTER = ROOT / "scripts" / "import_dify_chatflow.ps1"
README = ROOT / "README.md"


def load_builder_module():
    spec = spec_from_file_location("build_dify_chatflow_dsl", BUILDER)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load builder module")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_code_node(code: str, **kwargs):
    namespace: dict[str, object] = {}
    exec(code, namespace)  # noqa: S102 - test executes trusted local Dify code snippets.
    return namespace["main"](**kwargs)


class DifyChatflowImporterTests(unittest.TestCase):
    def _build_dsl(self) -> dict:
        result = subprocess.run(
            [sys.executable, str(BUILDER), "--stdout"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(result.stdout)

    def test_builder_emits_importable_advanced_chat_dsl(self) -> None:
        dsl = self._build_dsl()

        self.assertEqual(dsl["version"], "0.6.0")
        self.assertEqual(dsl["kind"], "app")
        self.assertEqual(dsl["app"]["mode"], "advanced-chat")
        self.assertEqual(dsl["workflow"]["graph"]["nodes"][0]["data"]["type"], "start")
        self.assertEqual(dsl["workflow"]["graph"]["nodes"][0]["data"]["variables"][0]["variable"], "notice_url")
        self.assertEqual(dsl["workflow"]["graph"]["nodes"][0]["data"]["variables"][1]["variable"], "history_report")
        self.assertIn(".docx", dsl["workflow"]["features"]["file_upload"]["allowed_file_extensions"])

    def test_builder_includes_chatflow_state_and_required_branches(self) -> None:
        dsl = self._build_dsl()
        node_types = {node["id"]: node["data"]["type"] for node in dsl["workflow"]["graph"]["nodes"]}
        conversation_vars = {item["name"] for item in dsl["workflow"]["conversation_variables"]}
        edge_ids = {edge["id"] for edge in dsl["workflow"]["graph"]["edges"]}

        self.assertIn("source_evidence", conversation_vars)
        self.assertIn("history_insights", conversation_vars)
        self.assertIn("current_report_ir", conversation_vars)
        self.assertIn("current_final_report", conversation_vars)
        self.assertEqual(node_types["classify_turn"], "code")
        self.assertEqual(node_types["route_turn"], "if-else")
        self.assertEqual(node_types["history_doc_extractor"], "document-extractor")
        self.assertEqual(node_types["revise_report"], "llm")
        self.assertEqual(node_types["export_word"], "http-request")
        self.assertEqual(node_types["answer_initial"], "answer")
        self.assertEqual(node_types["answer_revised"], "answer")
        self.assertEqual(node_types["answer_download"], "answer")
        self.assertIn("route_approve-to-export_word", edge_ids)
        self.assertIn("route_revise-to-revise_report", edge_ids)
        self.assertIn("route_first-to-history_file_gate", edge_ids)

    def test_route_classifier_treats_pasted_qa_defects_as_revision_not_download(self) -> None:
        builder = load_builder_module()

        result = run_code_node(
            builder.ROUTE_TURN_CODE,
            query=(
                "质检状态：needs_fix\n"
                "一般问题明细：report_depth: 报告正文偏短。\n"
                "修复建议：补充导语和企业关注点。\n"
                "Word 下载将在你确认后生成。若认可，请回复“确认下载 Word”。"
            ),
            current_report_ir='{"title":"河南报告"}',
        )

        self.assertEqual(result["route"], "revise")

    def test_route_classifier_allows_short_explicit_download_confirmation(self) -> None:
        builder = load_builder_module()

        result = run_code_node(
            builder.ROUTE_TURN_CODE,
            query="确认下载 Word",
            current_report_ir='{"title":"河南报告"}',
        )

        self.assertEqual(result["route"], "approve_download")

    def test_route_classifier_stops_repeated_needs_fix_after_three_repairs(self) -> None:
        builder = load_builder_module()

        result = run_code_node(
            builder.ROUTE_TURN_CODE,
            query="质检状态：needs_fix\n修复建议：核对表格中的58元。",
            current_report_ir='{"title":"兵团报告"}',
            repair_attempt_count="3",
        )

        self.assertEqual(result["route"], "repair_limit")

        download_result = run_code_node(
            builder.ROUTE_TURN_CODE,
            query="确认下载 Word",
            current_report_ir='{"title":"兵团报告"}',
            repair_attempt_count="3",
        )

        self.assertEqual(download_result["route"], "approve_download")

    def test_builder_sets_anthropic_required_max_tokens_on_llm_nodes(self) -> None:
        dsl = self._build_dsl()
        llm_nodes = [
            node
            for node in dsl["workflow"]["graph"]["nodes"]
            if node["data"]["type"] == "llm"
        ]

        self.assertGreater(len(llm_nodes), 0)
        for node in llm_nodes:
            params = node["data"]["model"].get("completion_params", {})
            self.assertIn("max_tokens", params, node["id"])
            self.assertIsInstance(params["max_tokens"], int, node["id"])
            self.assertGreater(params["max_tokens"], 0, node["id"])

    def test_code_node_outputs_use_dify_supported_types(self) -> None:
        dsl = self._build_dsl()
        supported_types = {"string", "number", "object", "array[string]", "array[number]", "array[object]"}
        code_nodes = [
            node
            for node in dsl["workflow"]["graph"]["nodes"]
            if node["data"]["type"] == "code"
        ]

        self.assertGreater(len(code_nodes), 0)
        for node in code_nodes:
            for output_name, output_schema in (node["data"].get("outputs") or {}).items():
                self.assertIn(output_schema.get("type"), supported_types, f"{node['id']}.{output_name}")

    def test_builder_uses_quality_model_for_generation_and_fast_model_for_qa(self) -> None:
        dsl = self._build_dsl()
        llm_by_id = {
            node["id"]: node["data"]["model"]["name"]
            for node in dsl["workflow"]["graph"]["nodes"]
            if node["data"]["type"] == "llm"
        }
        provider_by_id = {
            node["id"]: node["data"]["model"]["provider"]
            for node in dsl["workflow"]["graph"]["nodes"]
            if node["data"]["type"] == "llm"
        }

        self.assertEqual(provider_by_id["generate_report"], "langgenius/tongyi/tongyi")
        self.assertEqual(provider_by_id["revise_report"], "langgenius/tongyi/tongyi")
        self.assertEqual(provider_by_id["summarize_history"], "langgenius/tongyi/tongyi")
        self.assertEqual(provider_by_id["qa_report_first"], "langgenius/tongyi/tongyi")
        self.assertEqual(provider_by_id["qa_revised_report"], "langgenius/tongyi/tongyi")
        self.assertEqual(llm_by_id["generate_report"], "qwen3-max-2025-09-23")
        self.assertEqual(llm_by_id["revise_report"], "qwen3-max-2025-09-23")
        self.assertEqual(llm_by_id["summarize_history"], "qwen-plus-latest")
        self.assertEqual(llm_by_id["qa_report_first"], "qwen-plus-latest")
        self.assertEqual(llm_by_id["qa_revised_report"], "qwen-plus-latest")

    def test_import_script_calls_dify_service_layer_and_publishes(self) -> None:
        text = IMPORTER.read_text(encoding="utf-8")

        self.assertIn("build_dify_chatflow_dsl.py", text)
        self.assertIn("docker cp", text)
        self.assertIn("AppDslService", text)
        self.assertIn("ImportMode.YAML_CONTENT", text)
        self.assertIn("publish_workflow", text)
        self.assertIn('DifyApp.mode == "advanced-chat"', text)
        self.assertIn("workflow_id = workflow.id", text)

    def test_readme_points_to_retained_dify_workflow(self) -> None:
        docs = README.read_text(encoding="utf-8")

        self.assertIn("dify_workflow_pack_id_human_style.yml", docs)

    def test_history_parser_removes_thinking_and_falls_back_to_document_insights(self) -> None:
        builder = load_builder_module()

        result = run_code_node(
            builder.PARSE_HISTORY_CODE,
            raw_history="<think>只输出了思考过程</think>",
            extracted_text=(
                "广东项目的关联性\n"
                "均采用全国最低价格约束机制；\n"
                "均强化价格联动管理；\n"
                "均对非中选产品设置严格挂网条件。\n"
                "麻醉呼吸管路平均价格下降18%-25%。"
            ),
        )

        insights = result["history_insights"]
        self.assertIn("<history_insights>", insights)
        self.assertNotIn("<think>", insights)
        self.assertIn("价格联动", insights)
        self.assertIn("非中选产品", insights)
        self.assertNotIn("18%-25%", insights)

    def test_revised_render_parser_preserves_previous_report_when_render_fails(self) -> None:
        builder = load_builder_module()

        result = run_code_node(
            builder.PARSE_REVISED_RENDER_CODE,
            render_body='{"success":false,"report_ir":null,"report_markdown":"","error":"模型输出为空，无法导出报告"}',
            previous_report_ir='{"title":"旧报告","sections":[{"heading":"名词解释","paragraphs":["带量最低价指原文价格。"],"tables":[],"highlights":[]}]}',
            previous_report_markdown="# 旧报告\n\n## 名词解释\n\n带量最低价指原文价格。",
        )

        self.assertEqual(result["render_success"], "false")
        self.assertIn("旧报告", result["report_ir"])
        self.assertEqual(result["report_markdown"], "# 旧报告\n\n## 名词解释\n\n带量最低价指原文价格。")
        self.assertIn("已保留原报告", result["revision_note"])

    def test_initial_render_parser_keeps_report_state_empty_when_render_fails(self) -> None:
        builder = load_builder_module()

        result = run_code_node(
            builder.PARSE_RENDER_CODE,
            render_body='{"success":false,"report_ir":null,"report_markdown":"","error":"ReportIR JSON 解析失败"}',
        )

        self.assertEqual(result["report_ir"], "")
        self.assertIn("ReportIR JSON 解析失败", result["report_markdown"])
        self.assertIn("ReportIR JSON 解析失败", result["render_error"])


if __name__ == "__main__":
    unittest.main()
