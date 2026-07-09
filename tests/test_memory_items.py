from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class MemoryItemStoreTests(unittest.TestCase):
    def test_retrieval_returns_only_approved_scope_matches_in_stable_order(self) -> None:
        from app.core.memory.items import MemoryItem, MemoryItemStore, retrieve_scoped_memory_items

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryItemStore(Path(tmpdir) / "memory_items.json")
            store.save_items(
                [
                    MemoryItem(
                        id="mem_global_style",
                        title="Conservative style",
                        content="Use cautious enterprise-facing language.",
                        status="approved",
                        scopes=["global"],
                        kind="writing_style",
                    ),
                    MemoryItem(
                        id="mem_ylsf_angle",
                        title="Medical service price angle",
                        content="For medical service price notices, compare mapping and execution-risk points.",
                        status="approved",
                        scopes=["menu_code:ylsf"],
                        kind="analysis_angle",
                    ),
                    MemoryItem(
                        id="mem_lxzn_disabled",
                        title="Disabled guide",
                        content="This disabled guide must not be injected.",
                        status="disabled",
                        scopes=["global", "menu_code:ylsf"],
                        kind="quality_rule",
                    ),
                    MemoryItem(
                        id="mem_project_info",
                        title="Project information guide",
                        content="This project-information guide must not match ylsf.",
                        status="approved",
                        scopes=["menu_code:project_information"],
                        kind="analysis_angle",
                    ),
                ]
            )

            pack = {
                "primary_materials": [
                    {
                        "menu_code": "ylsf",
                        "articleid": "a1",
                        "title": "Service price notice",
                        "areaname": "Guangdong",
                        "projecttype": "medical service price",
                    }
                ],
                "auxiliary_materials": [],
            }

            matches = retrieve_scoped_memory_items(store, pack, limit=10)

        self.assertEqual([item.id for item in matches], ["mem_global_style", "mem_ylsf_angle"])

    def test_prompt_section_separates_memory_items_from_current_facts(self) -> None:
        from app.core.memory.items import MemoryItem, format_memory_items_for_prompt

        section = format_memory_items_for_prompt(
            [
                MemoryItem(
                    id="mem_quality_rule",
                    title="Quality rule",
                    content="Check whether auxiliary notices are being treated as primary facts.",
                    status="approved",
                    scopes=["global"],
                    kind="quality_rule",
                )
            ]
        )

        self.assertIn("# Structured Memory Items", section)
        self.assertIn("Memory items are style, angle, and quality guidance only.", section)
        self.assertIn("mem_quality_rule", section)
        self.assertIn("Check whether auxiliary notices", section)


if __name__ == "__main__":
    unittest.main()
