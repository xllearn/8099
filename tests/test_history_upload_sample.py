from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from scripts.create_history_upload_sample import write_history_upload_sample


class HistoryUploadSampleTests(unittest.TestCase):
    def test_writes_uploadable_word_with_linked_project_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history-sample.docx"
            write_history_upload_sample(path)

            self.assertTrue(path.exists())
            with ZipFile(path) as zf:
                document_xml = zf.read("word/document.xml").decode("utf-8")

        self.assertIn("广东麻醉管路等三类耗材带量联动采购串联分析材料", document_xml)
        self.assertIn("血流导向密网支架", document_xml)
        self.assertIn("SZGGZYHCDL202501", document_xml)
        self.assertIn("SZGGZYHCDL202601", document_xml)
        self.assertNotIn("<think>", document_xml)


if __name__ == "__main__":
    unittest.main()
