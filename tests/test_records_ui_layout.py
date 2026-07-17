from __future__ import annotations

import re
import unittest
from pathlib import Path


RECORDS_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "records.html"


class RecordsUiLayoutTests(unittest.TestCase):
    def test_compact_two_column_material_layout_contract(self) -> None:
        html = RECORDS_HTML.read_text(encoding="utf-8")

        required = (
            'class="card context-card"',
            'id="selectionTab"',
            'id="detailTab"',
            'id="selectionPanel"',
            'id="detailPanel"',
            'id="moreFiltersBtn"',
            'id="moreFilterCount"',
            'id="moreFiltersPanel"',
            'id="totalPagesInfo"',
            'id="generateButtonGuard"',
            "请至少选择1条主材料",
            "selected-primary",
            "selected-auxiliary",
            "switchContextTab",
            "updateMoreFilterCount",
            "updateGenerateButtonState",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, html)

        removed = (
            'class="row-selector"',
            'data-action="detail"',
            'class="card detail-card"',
        )
        for marker in removed:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, html)

    def test_results_table_readability_contract(self) -> None:
        html = RECORDS_HTML.read_text(encoding="utf-8")

        self.assertIn('class="records-table"', html)
        self.assertRegex(
            html,
            re.compile(r"\.records-table\s*\{[^}]*border-spacing:\s*0 4px;", re.S),
        )
        self.assertRegex(
            html,
            re.compile(r"body\s*\{[^}]*font-size:\s*13px;", re.S),
        )
        self.assertRegex(
            html,
            re.compile(r"\.record-meta\s*\{[^}]*font-size:\s*12px;", re.S),
        )
        self.assertRegex(
            html,
            re.compile(r"label\s*\{[^}]*font-size:\s*13px;", re.S),
        )
        self.assertRegex(
            html,
            re.compile(r"\.section-title\s*\{[^}]*font-size:\s*16px;", re.S),
        )
        self.assertRegex(
            html,
            re.compile(r"thead th\s*\{[^}]*font-size:\s*13px;", re.S),
        )
        self.assertRegex(
            html,
            re.compile(r"\.tag\s*\{[^}]*font-size:\s*12px;", re.S),
        )
        self.assertIn(
            '<th class="col-area" style="width: 106px;">地区</th>',
            html,
        )
        self.assertIn(
            'title="${escapeHtml(shortDate(item.audittime))}">'
            '${escapeHtml(shortDate(item.audittime))}</td>',
            html,
        )
        self.assertNotIn(
            'title="${escapeHtml(shortDateTime(item.audittime))}">'
            '${escapeHtml(shortDateTime(item.audittime))}</td>',
            html,
        )


if __name__ == "__main__":
    unittest.main()
