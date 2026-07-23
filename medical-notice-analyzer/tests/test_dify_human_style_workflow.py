import json
import re
import unittest
from pathlib import Path

import yaml


class DifyHumanStyleWorkflowTests(unittest.TestCase):
    def _load_workflow(self) -> tuple[dict, dict, list[dict]]:
        path = Path(__file__).resolve().parents[1] / "dify_workflow_pack_id_human_style.yml"
        self.assertTrue(path.exists(), "human-style Dify workflow DSL should be generated without overwriting the source DSL")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        graph = data["workflow"]["graph"]
        return data, {node["id"]: node["data"] for node in graph["nodes"]}, graph["edges"]

    def test_human_style_workflow_yaml_updates_llm_prompts_and_json_mode(self) -> None:
        _, by_id, _ = self._load_workflow()

        start_vars = by_id["start_node"]["variables"]
        self.assertEqual(start_vars[0]["variable"], "pack_id")
        self.assertTrue(start_vars[0]["required"])
        start_by_name = {item["variable"]: item for item in start_vars}
        self.assertIn("use_report_memory", start_by_name)
        self.assertFalse(start_by_name["use_report_memory"]["required"])
        self.assertIn("report_memory", start_by_name)
        self.assertFalse(start_by_name["report_memory"]["required"])
        self.assertGreaterEqual(start_by_name["report_memory"]["max_length"], 15000)

        callback_url = by_id["fetch_evidence_pack"]["url"]
        callback_start_refs = set(re.findall(r"\{\{#start_node\.([A-Za-z0-9_]+)#\}\}", callback_url))
        self.assertEqual(callback_url, "http://192.168.34.87:8099/analysis/packs/{{#start_node.pack_id#}}")
        self.assertEqual(callback_start_refs, {"pack_id"})
        self.assertLessEqual(callback_start_refs, set(start_by_name))

        for node_id in ["generate_report", "qa_report_first"]:
            model_params = by_id[node_id]["model"]["completion_params"]
            self.assertEqual(model_params.get("response_format"), "json_object")
            self.assertTrue(by_id[node_id]["structured_output"]["enabled"])

        issue_schema = by_id["qa_report_first"]["structured_output"]["schema"]["properties"]["issues"]["items"]
        self.assertEqual(
            issue_schema["required"],
            ["issue_id", "severity", "problem_type", "report_text", "source_basis", "fix_instruction"],
        )
        self.assertEqual(issue_schema["properties"]["severity"]["enum"], ["fatal", "high", "major", "minor"])

        generation_prompt = "\n".join(item.get("text", "") for item in by_id["generate_report"]["prompt_template"])
        qa_prompt = "\n".join(item.get("text", "") for item in by_id["qa_report_first"]["prompt_template"])

        self.assertIn("人工报告", generation_prompt)
        self.assertIn("企业关注点", generation_prompt)
        self.assertIn("主材料核心规则", qa_prompt)
        self.assertIn("附件表格摘要", qa_prompt)
        self.assertIn("只识别和记录问题", qa_prompt)
        self.assertIn("不得修改 report_markdown", qa_prompt)
        self.assertIn("不触发自动修订", qa_prompt)
        self.assertNotIn("只有 fatal/high/major 需要自动修订", qa_prompt)
        for node_id in ["generate_report", "qa_report_first"]:
            prompt = "\n".join(item.get("text", "") for item in by_id[node_id]["prompt_template"])
            self.assertIn("{{#start_node.use_report_memory#}}", prompt)
            self.assertIn("{{#start_node.report_memory#}}", prompt)
            self.assertIn("不得使用 report_memory 覆盖当前公告事实", prompt)
            self.assertIn("report_memory 为空", prompt)

    def test_workflow_has_generation_and_readonly_qa_only(self) -> None:
        _, by_id, edges = self._load_workflow()

        llm_ids = {node_id for node_id, node in by_id.items() if node["type"] == "llm"}
        self.assertEqual(llm_ids, {"generate_report", "qa_report_first"})
        removed_ids = {
            "quality_gate",
            "revise_report",
            "parse_revision_json",
            "qa_revised_report",
            "parse_revised_quality_json",
            "final_revised_result",
            "end_revised",
        }
        self.assertTrue(removed_ids.isdisjoint(by_id))

        edge_pairs = {(edge["source"], edge["target"]) for edge in edges}
        self.assertTrue(all(edge["source"] in by_id and edge["target"] in by_id for edge in edges))
        self.assertTrue(
            {
                ("parse_generation_json", "qa_report_first"),
                ("qa_report_first", "parse_quality_json"),
                ("parse_quality_json", "final_initial_result"),
                ("final_initial_result", "end_initial"),
            }.issubset(edge_pairs)
        )
        self.assertFalse(any({source, target} & removed_ids for source, target in edge_pairs))

    def test_readonly_qa_failure_keeps_initial_report_and_marks_review(self) -> None:
        _, by_id, _ = self._load_workflow()
        namespace: dict[str, object] = {}
        exec(by_id["final_initial_result"]["code"], namespace)

        initial_report = "## 导语\n浙江采购公告初稿正文。"
        output = namespace["main"](
            "pack_zhejiang",
            json.dumps(
                {
                    "report_title": "浙江采购公告分析",
                    "report_markdown": initial_report,
                    "version": 1,
                    "generation_warnings": [],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "passed": False,
                    "issues": [
                        {"issue_id": "Q001", "severity": "major", "problem_type": "summary_only"},
                    ],
                },
                ensure_ascii=False,
            ),
        )
        result = json.loads(output["result"])

        self.assertEqual(result["status"], "needs_manual_review")
        self.assertEqual(result["report_markdown"].encode("utf-8"), initial_report.encode("utf-8"))
        self.assertEqual(result["version"], 1)
        self.assertFalse(result["quality_check"]["passed"])
        self.assertEqual(result["remaining_issues"][0]["issue_id"], "Q001")

    def test_parse_quality_blocks_severe_issue_even_if_model_marks_passed(self) -> None:
        _, by_id, _ = self._load_workflow()
        namespace: dict[str, object] = {}
        exec(by_id["parse_quality_json"]["code"], namespace)

        output = namespace["main"](
            json.dumps(
                {
                    "passed": True,
                    "round": 1,
                    "issues": [
                        {
                            "issue_id": "Q001",
                            "severity": "major",
                            "problem_type": "unsupported_claim",
                            "report_text": "",
                            "source_basis": "",
                            "fix_instruction": "",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        )
        quality = json.loads(output["quality_json"])

        self.assertFalse(quality["passed"])
        self.assertEqual(quality["issues"][0]["issue_id"], "Q001")

    def test_parse_quality_fails_closed_on_malformed_contracts(self) -> None:
        _, by_id, _ = self._load_workflow()
        namespace: dict[str, object] = {}
        exec(by_id["parse_quality_json"]["code"], namespace)

        valid_issue = {
            "issue_id": "Q001",
            "severity": "minor",
            "problem_type": "style",
            "report_text": "",
            "source_basis": "",
            "fix_instruction": "",
        }
        malformed_payloads = [
            {"passed": "false", "round": 1, "issues": []},
            [{"passed": True, "round": 1, "issues": []}],
            {"passed": True, "round": 1, "issues": {}},
            {"passed": True, "round": 1, "issues": ["not an object"]},
            {"passed": True, "round": 1, "issues": [{**valid_issue, "severity": "unknown"}]},
        ]
        for payload in malformed_payloads:
            output = namespace["main"](json.dumps(payload, ensure_ascii=False))
            quality = json.loads(output["quality_json"])
            self.assertFalse(quality["passed"])
            self.assertEqual(sum(issue["issue_id"] == "Q_PARSE" for issue in quality["issues"]), 1)

        severe_output = namespace["main"](
            json.dumps({"passed": True, "round": 1, "issues": [{**valid_issue, "severity": " major "}]}, ensure_ascii=False)
        )
        severe_quality = json.loads(severe_output["quality_json"])
        self.assertEqual(severe_quality["issues"][0]["severity"], "major")
        self.assertFalse(severe_quality["passed"])

        minor_output = namespace["main"](
            json.dumps({"passed": True, "round": 1, "issues": [valid_issue]}, ensure_ascii=False)
        )
        self.assertTrue(json.loads(minor_output["quality_json"])["passed"])

    def test_final_result_fails_closed_and_preserves_valid_initial_bytes(self) -> None:
        _, by_id, _ = self._load_workflow()
        namespace: dict[str, object] = {}
        exec(by_id["final_initial_result"]["code"], namespace)

        valid_quality = json.dumps({"passed": True, "round": 1, "issues": []}, ensure_ascii=False)
        malformed_generations = [
            json.dumps([], ensure_ascii=False),
            json.dumps("not an object", ensure_ascii=False),
            json.dumps({"version": "v1", "report_title": "标题", "report_markdown": "正文", "generation_warnings": []}, ensure_ascii=False),
            json.dumps({"version": 1, "report_title": "标题", "report_markdown": "   ", "generation_warnings": []}, ensure_ascii=False),
            json.dumps({"version": 1, "report_title": "标题", "report_markdown": ["正文"], "generation_warnings": []}, ensure_ascii=False),
        ]
        for generation_json in malformed_generations:
            output = namespace["main"]("pack", generation_json, valid_quality)
            result = json.loads(output["result"])
            self.assertEqual(result["status"], "needs_manual_review")
            self.assertEqual(result["version"], 1)
            self.assertTrue(any(issue["severity"] == "fatal" for issue in result["remaining_issues"]))

        quality_not_object = namespace["main"](
            "pack",
            json.dumps({"version": 1, "report_title": "标题", "report_markdown": "正文", "generation_warnings": []}, ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
        )
        quality_result = json.loads(quality_not_object["result"])
        self.assertEqual(quality_result["status"], "needs_manual_review")
        self.assertTrue(any(issue["issue_id"] == "Q_PARSE" for issue in quality_result["remaining_issues"]))

        quality_non_list = namespace["main"](
            "pack",
            json.dumps({"version": 1, "report_title": "标题", "report_markdown": "正文", "generation_warnings": []}, ensure_ascii=False),
            json.dumps({"passed": True, "issues": {}}, ensure_ascii=False),
        )
        quality_non_list_result = json.loads(quality_non_list["result"])
        self.assertEqual(quality_non_list_result["status"], "needs_manual_review")
        self.assertTrue(any(issue["issue_id"] == "Q_PARSE" for issue in quality_non_list_result["remaining_issues"]))

        initial_report = "  ## 初稿\n保留原始空白  \n"
        valid_output = namespace["main"](
            "pack",
            json.dumps(
                {
                    "version": 1,
                    "report_title": "标题",
                    "report_markdown": initial_report,
                    "generation_warnings": [],
                },
                ensure_ascii=False,
            ),
            valid_quality,
        )
        valid_result = json.loads(valid_output["result"])
        self.assertEqual(valid_result["status"], "finished")
        self.assertEqual(valid_result["report_markdown"].encode("utf-8"), initial_report.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
