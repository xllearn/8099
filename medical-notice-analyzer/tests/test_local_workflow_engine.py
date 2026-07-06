from __future__ import annotations

import json
import unittest
from pathlib import Path


class LocalWorkflowEngineTests(unittest.TestCase):
    def _load_pack(self) -> dict:
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "synthetic_evidence_pack_basic.json"
        return json.loads(fixture_path.read_text(encoding="utf-8"))

    def test_local_engine_runs_serial_nodes_and_returns_draft_report(self) -> None:
        from app.core.workflow.engine import run_local_workflow

        pack = self._load_pack()

        result = run_local_workflow(pack, run_id="run_20260706_local1")

        self.assertEqual(result["status"], "finished")
        self.assertEqual(result["backend"], "local_engine")
        self.assertEqual(result["workflow_backend"], "local_engine")
        self.assertEqual(result["workflow_run_id"], "local-run_20260706_local1")
        self.assertEqual([node["name"] for node in result["nodes"]], ["prepare", "generate", "render", "qa", "final"])
        self.assertTrue(all(node["status"] == "finished" for node in result["nodes"]))
        self.assertTrue(all(node["started_at"] for node in result["nodes"]))
        self.assertTrue(all(node["finished_at"] for node in result["nodes"]))
        self.assertIn("Synthetic medical consumables procurement notice", result["report_title"])
        self.assertIn("Synthetic medical consumables procurement notice", result["report_markdown"])
        self.assertIn("synthetic-table-summary.xlsx", result["report_markdown"])
        self.assertTrue(result["quality_check"]["passed"])
        self.assertIn("local_engine", "\n".join(result["generation_warnings"]))

    def test_local_engine_records_provider_model_and_prompt_hash_metadata(self) -> None:
        from app.core.llm import MockLLMProvider
        from app.core.workflow.engine import run_local_workflow

        provider = MockLLMProvider(
            response=json.dumps(
                {
                    "report_title": "Provider generated synthetic report",
                    "report_markdown": "# Provider generated synthetic report\n\nThis body came from the mock LLM provider.",
                }
            )
        )

        result = run_local_workflow(self._load_pack(), run_id="run_20260706_llm1", llm_provider=provider)

        self.assertEqual(result["llm_provider"], "mock")
        self.assertEqual(result["llm_model"], "mock-local-report-v1")
        self.assertEqual(result["prompt_ref"], "local_report_generation:v1")
        self.assertEqual(len(result["prompt_sha256"]), 64)
        self.assertEqual(result["prompt_refs"][0]["sha256"], result["prompt_sha256"])
        self.assertEqual(result["model_calls"][0]["provider"], "mock")
        self.assertEqual(result["model_calls"][0]["model"], "mock-local-report-v1")
        self.assertEqual(result["model_calls"][0]["prompt_sha256"], result["prompt_sha256"])
        self.assertIn("mock LLM provider", result["report_markdown"])

    def test_local_engine_converts_invalid_provider_json_to_manual_review(self) -> None:
        from app.core.llm import MockLLMProvider
        from app.core.workflow.engine import run_local_workflow

        result = run_local_workflow(
            self._load_pack(),
            run_id="run_20260706_badjson",
            llm_provider=MockLLMProvider(response="{not valid json"),
        )

        self.assertEqual(result["status"], "needs_manual_review")
        self.assertFalse(result["quality_check"]["passed"])
        self.assertEqual(result["remaining_issues"][0]["issue_id"], "LLM_JSON_PARSE_ERROR")
        self.assertEqual(result["model_calls"][0]["json_parse_error"]["code"], "LLM_JSON_PARSE_ERROR")
        self.assertIn("Synthetic medical consumables procurement notice", result["report_markdown"])


if __name__ == "__main__":
    unittest.main()
