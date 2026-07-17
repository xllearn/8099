from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
