from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dify_run_stats.ps1"
README = ROOT / "README.md"
HANDOFF = ROOT / "NEXT_CHAT_HANDOFF.md"


class DifyRunStatsScriptTests(unittest.TestCase):
    def test_script_reads_total_and_node_level_dify_metrics(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("param(", text)
        self.assertIn("$WorkflowRunId", text)
        self.assertIn("$Latest", text)
        self.assertIn("workflow_runs", text)
        self.assertIn("workflow_node_executions", text)
        self.assertIn("wr.total_tokens", text)
        self.assertIn("wr.elapsed_time", text)
        self.assertIn("wne.elapsed_time", text)
        self.assertIn("execution_metadata", text)
        self.assertIn("total_tokens", text)
        self.assertIn("$LASTEXITCODE", text)
        self.assertIn("docker exec failed", text)
        self.assertIn("Dify workflow run total", text)
        self.assertIn("Dify node totals", text)

    def test_docs_explain_dify_builtin_token_and_timing_stats(self) -> None:
        docs = README.read_text(encoding="utf-8")
        if HANDOFF.exists():
            docs += "\n" + HANDOFF.read_text(encoding="utf-8")

        self.assertIn("Dify 内置 token 与耗时统计", docs)
        self.assertIn("scripts\\dify_run_stats.ps1", docs)
        self.assertIn("workflow_runs.total_tokens", docs)
        self.assertIn("workflow_node_executions.execution_metadata", docs)
        self.assertIn("workflow_node_executions.elapsed_time", docs)


if __name__ == "__main__":
    unittest.main()
