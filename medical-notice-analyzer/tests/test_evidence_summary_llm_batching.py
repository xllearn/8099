from __future__ import annotations

import unittest
from unittest.mock import patch

import app.evidence_summary_llm as summary


def _tables(count: int) -> list[dict[str, str]]:
    return [{"table_id": f"table-{index}"} for index in range(count)]


def _result(table_id: str) -> dict[str, object]:
    return {
        "summary": f"summary-{table_id}",
        "column_map": {"enterprise": 0},
    }


class EvidenceSummaryLlmBatchingTests(unittest.TestCase):
    def test_retries_only_missing_tables_from_partial_batch(self):
        calls: list[list[str]] = []

        def fake_request(tables: list[dict[str, str]]) -> dict[str, dict[str, object]]:
            table_ids = [table["table_id"] for table in tables]
            calls.append(table_ids)
            if len(calls) == 1:
                return {
                    table_id: _result(table_id)
                    for table_id in table_ids
                    if table_id != "table-2"
                }
            return {table_id: _result(table_id) for table_id in table_ids}

        with patch.object(summary, "_request_summaries", side_effect=fake_request):
            result = summary._request_summaries_batched(_tables(6))

        self.assertEqual(
            [
                ["table-0", "table-1", "table-2", "table-3"],
                ["table-2"],
                ["table-4", "table-5"],
            ],
            calls,
        )
        self.assertEqual(
            {f"table-{index}": _result(f"table-{index}") for index in range(6)},
            result,
        )

    def test_stops_after_completely_empty_batch(self):
        calls: list[list[str]] = []

        def fake_request(tables: list[dict[str, str]]) -> dict[str, dict[str, object]]:
            calls.append([table["table_id"] for table in tables])
            return {}

        with patch.object(summary, "_request_summaries", side_effect=fake_request):
            result = summary._request_summaries_batched(_tables(8))

        self.assertEqual({}, result)
        self.assertEqual([4], [len(batch) for batch in calls])

    def test_stops_after_later_empty_batch_without_discarding_prior_results(self):
        calls: list[list[str]] = []

        def fake_request(tables: list[dict[str, str]]) -> dict[str, dict[str, object]]:
            table_ids = [table["table_id"] for table in tables]
            calls.append(table_ids)
            if len(calls) == 1:
                return {table_id: _result(table_id) for table_id in table_ids}
            return {}

        with patch.object(summary, "_request_summaries", side_effect=fake_request):
            result = summary._request_summaries_batched(_tables(12))

        self.assertEqual(
            [
                ["table-0", "table-1", "table-2", "table-3"],
                ["table-4", "table-5", "table-6", "table-7"],
            ],
            calls,
        )
        self.assertEqual(
            {f"table-{index}": _result(f"table-{index}") for index in range(4)},
            result,
        )


if __name__ == "__main__":
    unittest.main()
