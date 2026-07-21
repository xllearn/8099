import unittest
from pathlib import Path

import yaml


class DifyHumanStyleWorkflowTests(unittest.TestCase):
    def test_human_style_workflow_yaml_updates_llm_prompts_and_json_mode(self) -> None:
        path = Path(__file__).resolve().parents[1] / "dify_workflow_pack_id_human_style.yml"
        self.assertTrue(path.exists(), "human-style Dify workflow DSL should be generated without overwriting the source DSL")

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        nodes = data["workflow"]["graph"]["nodes"]
        by_id = {node["id"]: node["data"] for node in nodes}

        start_vars = by_id["start_node"]["variables"]
        self.assertEqual(start_vars[0]["variable"], "pack_id")
        self.assertTrue(start_vars[0]["required"])
        start_by_name = {item["variable"]: item for item in start_vars}
        self.assertIn("use_report_memory", start_by_name)
        self.assertFalse(start_by_name["use_report_memory"]["required"])
        self.assertIn("report_memory", start_by_name)
        self.assertFalse(start_by_name["report_memory"]["required"])
        self.assertGreaterEqual(start_by_name["report_memory"]["max_length"], 15000)
        self.assertEqual(
            by_id["fetch_evidence_pack"]["url"],
            "http://192.168.34.87:8099/analysis/packs/{{#start_node.pack_id#}}?run_id={{#start_node.run_id#}}",
        )

        for node_id in ["generate_report", "qa_report_first", "revise_report", "qa_revised_report"]:
            model_params = by_id[node_id]["model"]["completion_params"]
            self.assertEqual(model_params.get("response_format"), "json_object")
            self.assertTrue(by_id[node_id]["structured_output"]["enabled"])

        generation_prompt = "\n".join(item.get("text", "") for item in by_id["generate_report"]["prompt_template"])
        qa_prompt = "\n".join(item.get("text", "") for item in by_id["qa_report_first"]["prompt_template"])
        revision_prompt = "\n".join(item.get("text", "") for item in by_id["revise_report"]["prompt_template"])

        self.assertIn("人工报告", generation_prompt)
        self.assertIn("企业关注点", generation_prompt)
        self.assertIn("正文较短但核心附件已解析", generation_prompt)
        self.assertNotIn("资料说明：", generation_prompt)
        self.assertNotIn("末尾资料说明", generation_prompt)
        self.assertNotIn("声  明", generation_prompt)
        self.assertIn("Markdown 表格", generation_prompt)
        self.assertIn("主材料核心规则", qa_prompt)
        self.assertIn("附件表格摘要", qa_prompt)
        self.assertIn("声明或资料说明", qa_prompt)
        self.assertIn("只补齐缺失项", revision_prompt)
        for node_id in ["generate_report", "qa_report_first", "revise_report", "qa_revised_report"]:
            prompt = "\n".join(item.get("text", "") for item in by_id[node_id]["prompt_template"])
            self.assertIn("{{#start_node.use_report_memory#}}", prompt)
            self.assertIn("{{#start_node.report_memory#}}", prompt)
            self.assertIn("不得使用 report_memory 覆盖当前公告事实", prompt)
            self.assertIn("report_memory 为空", prompt)


if __name__ == "__main__":
    unittest.main()
