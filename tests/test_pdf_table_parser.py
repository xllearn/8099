from __future__ import annotations

import copy
import hashlib
import importlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import app.attachment_parser as attachment_parser
import app.main as main_module
from app.attachment_cache import FAILURE_STATUSES, cache_key
from app.attachment_fetcher import AttachmentDownloadResult
from app.evidence_schema import EvidenceValidationError, read_evidence_pack, validate_evidence_pack


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "pdf_tables"


class _FakeRow:
    def __init__(self, cells):
        self.cells = cells


class _FakeTable:
    def __init__(self, matrix, row_cells, bbox=(10.0, 20.0, 190.0, 120.0)):
        self._matrix = matrix
        self.rows = [_FakeRow(cells) for cells in row_cells]
        self.bbox = bbox

    def extract(self):
        return copy.deepcopy(self._matrix)


class _FakePage:
    def __init__(self, page_number, text, tables=None, table_error=None):
        self.page_number = page_number
        self.width = 200.0
        self.height = 300.0
        self._text = text
        self._tables = list(tables or [])
        self._table_error = table_error

    def extract_text(self):
        return self._text

    def find_tables(self):
        if self._table_error:
            raise self._table_error
        return self._tables


class _FakePdf:
    def __init__(self, pages):
        self.pages = list(pages)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _table(matrix, *, bbox=(10.0, 20.0, 190.0, 120.0)):
    rows = len(matrix)
    columns = max((len(row) for row in matrix), default=0)
    row_height = (bbox[3] - bbox[1]) / max(rows, 1)
    column_width = (bbox[2] - bbox[0]) / max(columns, 1)
    row_cells = []
    for row_index in range(rows):
        cells = []
        for column_index in range(columns):
            cells.append(
                (
                    bbox[0] + column_index * column_width,
                    bbox[1] + row_index * row_height,
                    bbox[0] + (column_index + 1) * column_width,
                    bbox[1] + (row_index + 1) * row_height,
                )
            )
        row_cells.append(cells)
    return _FakeTable(matrix, row_cells, bbox=bbox)


def _vector_table_pdf() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    commands = [
        "0.5 w",
        "10 190 m 190 190 l S",
        "10 160 m 190 160 l S",
        "10 130 m 190 130 l S",
        "10 130 m 10 190 l S",
        "100 130 m 100 190 l S",
        "190 130 m 190 190 l S",
        "BT /F1 8 Tf 15 175 Td (Product) Tj ET",
        "BT /F1 8 Tf 105 175 Td (Code) Tj ET",
        "BT /F1 8 Tf 15 145 Td (Stent A) Tj ET",
        "BT /F1 8 Tf 105 145 Td (00123) Tj ET",
    ]
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class PdfTableParserTests(unittest.TestCase):
    def parser(self):
        try:
            return importlib.import_module("app.pdf_table_parser")
        except ModuleNotFoundError:
            self.fail("structured PDF table capability is missing: app.pdf_table_parser")

    @staticmethod
    def source_context():
        return {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "attachment_id": "attachment-1",
            "filename": "table.pdf",
        }

    @staticmethod
    def basic_matrix():
        return [
            ["Product", "Code", "Rate"],
            ["Stent A", "00123", "15%"],
            ["Stent B", "-2.50", "20 mg"],
        ]

    def extract(self, pages, content=b"deterministic-pdf", **kwargs):
        parser = self.parser()
        with patch.object(parser.pdfplumber, "open", return_value=_FakePdf(pages)):
            return parser.extract_pdf_tables(content, **kwargs)

    def evidence_item(self, context, cell):
        factory = getattr(main_module, "_pdf_table_cell_evidence_item", None)
        if factory is None:
            self.fail("PDF table cells are not connected to EvidenceItem v2")
        return factory(context, cell)

    def test_text_pdf_cells_match_ground_truth(self):
        truth = json.loads((FIXTURE_DIR / "text_table_ground_truth.json").read_text(encoding="utf-8"))
        result = self.extract([_FakePage(1, "digital text", [_table(self.basic_matrix())])])
        values = [cell["value"] for cell in result["cells"]]
        matches = sum(value in values for value in truth["non_empty_cells"])
        self.assertGreaterEqual(matches / len(truth["non_empty_cells"]), 0.95)

    def test_numeric_cells_preserve_exact_tokens_and_units(self):
        truth = json.loads((FIXTURE_DIR / "text_table_ground_truth.json").read_text(encoding="utf-8"))
        result = self.extract([_FakePage(1, "digital text", [_table(self.basic_matrix())])])
        values = {cell["value"] for cell in result["cells"]}
        self.assertEqual(set(truth["numeric_cells"]), set(truth["numeric_cells"]) & values)

    def test_real_pdfplumber_extracts_vector_table(self):
        result = self.parser().extract_pdf_tables(_vector_table_pdf())
        values = {cell["value"] for cell in result["cells"]}
        self.assertEqual({"Product", "Code", "Stent A", "00123"}, values)

    def test_structured_pdf_attachment_exposes_cells_and_c_summaries(self):
        content = _vector_table_pdf()
        with patch.dict(
            os.environ,
            {"ENABLE_STRUCTURED_PDF_TABLES": "true", "ENABLE_PDF_OCR": "false"},
            clear=False,
        ), patch.object(attachment_parser, "_ocr_pdf_text") as ocr:
            result = attachment_parser.parse_attachment_bytes(content, "table.pdf", ".pdf", len(content))

        self.assertIn("parsed_pdf_table_cells", result["parse_statuses"])
        self.assertEqual(4, len(result["pdf_table_cells"]))
        self.assertTrue(result["table_summaries"])
        self.assertTrue(all(item["evidence_level"] == "C" for item in result["table_summaries"]))
        ocr.assert_not_called()

    def test_source_refs_are_one_based_and_hash_valid(self):
        content = b"pdf-source-bytes"
        result = self.extract([_FakePage(1, "digital text", [_table(self.basic_matrix())])], content=content)
        expected_hash = hashlib.sha256(content).hexdigest()
        self.assertTrue(result["cells"])
        for cell in result["cells"]:
            self.assertGreaterEqual(cell["page_no"], 1)
            self.assertGreaterEqual(cell["table_index"], 1)
            self.assertGreaterEqual(cell["row"], 1)
            self.assertGreaterEqual(cell["column"], 1)
            self.assertRegex(cell["cell_range"], r"^R[1-9]\d*C[1-9]\d*(?::R[1-9]\d*C[1-9]\d*)?$")
            self.assertEqual(expected_hash, cell["source_hash"])

    def test_merged_cells_emit_single_value_with_covering_range(self):
        merged_bbox = (10.0, 20.0, 190.0, 60.0)
        table = _FakeTable(
            [["Merged title", None], ["A", "1"]],
            [
                [merged_bbox, merged_bbox],
                [(10.0, 60.0, 100.0, 120.0), (100.0, 60.0, 190.0, 120.0)],
            ],
        )
        result = self.extract([_FakePage(1, "digital text", [table])])
        merged = [cell for cell in result["cells"] if cell["value"] == "Merged title"]
        self.assertEqual(1, len(merged))
        self.assertEqual("R1C1:R1C2", merged[0]["cell_range"])

    def test_multi_page_tables_preserve_page_and_table_identity(self):
        pages = [
            _FakePage(1, "page one", [_table([["Header", "A"], ["Row", "1"]])]),
            _FakePage(2, "page two", [_table([["Header", "A"], ["Row", "2"]])]),
        ]
        result = self.extract(pages)
        self.assertEqual({1, 2}, {table["page_no"] for table in result["tables"]})
        self.assertTrue(all(table["table_index"] == 1 for table in result["tables"]))

    def test_region_coordinates_stay_inside_page_bounds(self):
        result = self.extract([_FakePage(1, "digital text", [_table(self.basic_matrix())])])
        for cell in result["cells"]:
            region = cell["region"]
            self.assertGreaterEqual(region["x0"], 0)
            self.assertGreaterEqual(region["y0"], 0)
            self.assertLessEqual(region["x1"], region["page_width"])
            self.assertLessEqual(region["y1"], region["page_height"])

    def test_same_table_from_text_and_ocr_is_deduplicated(self):
        parser = self.parser()
        base = {
            "page_no": 1,
            "table_index": 1,
            "region": {"x0": 10, "y0": 20, "x1": 190, "y1": 120, "page_width": 200, "page_height": 300},
            "matrix": self.basic_matrix(),
            "cells": [],
        }
        text_table = {**copy.deepcopy(base), "engine": "pdfplumber"}
        ocr_table = {**copy.deepcopy(base), "engine": "ocr"}
        self.assertEqual(1, len(parser.deduplicate_tables([text_table, ocr_table])))

    def test_equal_headers_on_distinct_pages_are_not_deduplicated(self):
        parser = self.parser()
        tables = [
            {"page_no": 1, "table_index": 1, "region": None, "matrix": [["Header"], ["A"]], "cells": []},
            {"page_no": 2, "table_index": 1, "region": None, "matrix": [["Header"], ["B"]], "cells": []},
        ]
        self.assertEqual(2, len(parser.deduplicate_tables(tables)))

    def test_duplicate_table_does_not_displace_later_unique_table(self):
        parser = self.parser()
        duplicate = {"page_no": 1, "table_index": 1, "region": None, "matrix": [["A"]], "cells": []}
        unique = {"page_no": 1, "table_index": 2, "region": None, "matrix": [["B"]], "cells": []}
        result = parser.deduplicate_tables([duplicate, copy.deepcopy(duplicate), unique])
        self.assertEqual([[["A"]], [["B"]]], [table["matrix"] for table in result])

    def test_single_page_table_failure_preserves_other_pages_and_text(self):
        pages = [
            _FakePage(1, "page one", [_table([["A"]])]),
            _FakePage(2, "page two", table_error=RuntimeError("table extraction failed")),
        ]
        result = self.extract(pages)
        self.assertEqual("partial", result["status"])
        self.assertIn("page one", result["text"])
        self.assertIn("page two", result["text"])
        self.assertEqual([1], [table["page_no"] for table in result["tables"]])
        self.assertIn("PDF_PAGE_TABLE_EXTRACTION_FAILED", {item["code"] for item in result["diagnostics"]})

    def test_partial_structured_pdf_is_explicitly_degraded_not_cached_as_success(self):
        parsed = {
            "status": "partial",
            "text": "usable page text",
            "pages": [{"page_no": 1, "text_chars": 16}],
            "tables": [],
            "cells": [],
            "diagnostics": [{"code": "PDF_PAGE_TABLE_EXTRACTION_FAILED", "page_no": 1}],
            "rule_version": "20260715-text-pdf-v1",
            "table_summaries": [],
        }
        with patch.dict(os.environ, {"ENABLE_STRUCTURED_PDF_TABLES": "true"}, clear=False), patch.object(
            attachment_parser,
            "_parse_structured_pdf",
            return_value=parsed,
        ):
            result = attachment_parser.parse_attachment_bytes(b"%PDF-partial", "partial.pdf", ".pdf", 12)
        self.assertIn("parsed_text", result["parse_statuses"])
        self.assertEqual("partial_parse", result["parse_statuses"][-1])
        self.assertIn("partial_parse", FAILURE_STATUSES)

    def test_failed_structured_pdf_with_residual_text_remains_failed(self):
        parsed = {
            "status": "failed",
            "text": "residual diagnostic text",
            "pages": [],
            "tables": [],
            "cells": [],
            "diagnostics": [{"code": "PDF_DOCUMENT_OPEN_FAILED"}],
            "rule_version": "20260715-text-pdf-v1",
            "table_summaries": [],
        }
        with patch.dict(os.environ, {"ENABLE_STRUCTURED_PDF_TABLES": "true"}, clear=False), patch.object(
            attachment_parser,
            "_parse_structured_pdf",
            return_value=parsed,
        ):
            result = attachment_parser.parse_attachment_bytes(b"%PDF-failed", "failed.pdf", ".pdf", 11)
        self.assertIn("parsed_text", result["parse_statuses"])
        self.assertEqual("parse_failed", result["parse_statuses"][-1])

    def test_corrupt_pdf_returns_structured_failure_without_fake_cells(self):
        parser = self.parser()
        with patch.object(parser.pdfplumber, "open", side_effect=ValueError("broken")):
            result = parser.extract_pdf_tables(b"broken-pdf")
        self.assertEqual("failed", result["status"])
        self.assertEqual([], result["cells"])
        self.assertEqual("PDF_DOCUMENT_OPEN_FAILED", result["diagnostics"][0]["code"])

    def test_corrupt_structured_pdf_attachment_remains_failed(self):
        parser = self.parser()
        with patch.dict(os.environ, {"ENABLE_STRUCTURED_PDF_TABLES": "true"}, clear=False), patch.object(
            parser.pdfplumber,
            "open",
            side_effect=ValueError("broken"),
        ):
            result = attachment_parser.parse_attachment_bytes(b"broken-pdf", "broken.pdf", ".pdf", 10)
        self.assertEqual("parse_failed", result["parse_statuses"][-1])
        self.assertEqual([], result["pdf_table_cells"])

    def test_empty_small_pdf_is_not_marked_parsed_text_success(self):
        with patch.object(attachment_parser, "_parse_pdf", return_value=""):
            with patch.dict(os.environ, {"ENABLE_STRUCTURED_PDF_TABLES": "false", "ENABLE_PDF_OCR": "false"}, clear=False):
                result = attachment_parser.parse_attachment_bytes(b"%PDF-empty", "empty.pdf", ".pdf", 10)
        self.assertIn("parse_failed", result["parse_statuses"])
        self.assertNotIn("parsed_text", result["parse_statuses"])

    def test_ocr_is_disabled_by_default(self):
        parser = self.parser()
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(parser.ocr_enabled())

    def test_explicit_false_never_calls_ocr(self):
        with patch.dict(os.environ, {"ENABLE_PDF_OCR": "false"}, clear=False):
            with patch.object(attachment_parser.subprocess, "run") as run:
                text, warnings = attachment_parser._ocr_pdf_text(b"%PDF")
        self.assertEqual("", text)
        self.assertTrue(warnings)
        run.assert_not_called()

    def test_all_ocr_switches_still_require_confirmed_candidate(self):
        with patch.dict(
            os.environ,
            {"ENABLE_PDF_OCR": "true", "ENABLE_IMAGE_TABLE_OCR": "true"},
            clear=False,
        ), patch.object(attachment_parser.shutil, "which", return_value="/usr/bin/tool"), patch.object(
            attachment_parser.subprocess,
            "run",
        ) as run:
            text, warnings = attachment_parser._ocr_pdf_text(b"%PDF")
        self.assertEqual("", text)
        self.assertTrue(warnings)
        run.assert_not_called()

    def test_short_digital_page_does_not_trigger_ocr(self):
        parser = self.parser()
        self.assertFalse(parser.is_ocr_candidate(is_scanned=False, text_chars=10, key_table_readable=False))

    def test_scanned_page_199_chars_is_ocr_candidate(self):
        parser = self.parser()
        self.assertTrue(parser.is_ocr_candidate(is_scanned=True, text_chars=199, key_table_readable=True))

    def test_scanned_page_200_chars_is_not_low_text_candidate(self):
        parser = self.parser()
        self.assertFalse(parser.is_ocr_candidate(is_scanned=True, text_chars=200, key_table_readable=True))

    def test_scanned_unreadable_key_table_is_candidate_even_with_200_or_more_chars(self):
        parser = self.parser()
        self.assertTrue(parser.is_ocr_candidate(is_scanned=True, text_chars=800, key_table_readable=False))

    def test_ocr_cells_obey_same_numeric_and_source_ref_rules(self):
        cell = {
            "value": "00123",
            "page_no": 1,
            "table_index": 1,
            "row": 1,
            "column": 1,
            "cell_range": "R1C1",
            "quote": "00123",
            "source_hash": hashlib.sha256(b"pdf").hexdigest(),
            "region": {"x0": 0, "y0": 0, "x1": 10, "y1": 10, "page_width": 100, "page_height": 100},
            "engine": "ocr",
        }
        with self.assertRaises(EvidenceValidationError):
            self.evidence_item(self.source_context(), cell)

    def test_database_prepare_emits_pdf_cells_as_a_level_evidence_items(self):
        content = b"pdf-database-content"
        cell = {
            "value": "15%",
            "page_no": 1,
            "table_index": 1,
            "row": 2,
            "column": 3,
            "cell_range": "R2C3",
            "quote": "15%",
            "source_hash": hashlib.sha256(content).hexdigest(),
            "region": {"x0": 10, "y0": 20, "x1": 30, "y1": 40, "page_width": 100, "page_height": 100},
            "engine": "pdfplumber",
        }
        material = {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "title": "Notice",
            "content": "<p>Digital source body with enough content for the database pack.</p>",
        }
        attachment = {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "articleattid": "attachment-1",
            "filename": "table.pdf",
            "fileext": ".pdf",
            "filesize": len(content),
        }
        parsed = {
            "parse_statuses": ["parsed_text", "parsed_pdf_table_cells", "parsed_summary"],
            "text_length": 12,
            "summary": "summary",
            "key_facts": [],
            "important_sections": [],
            "table_summaries": [{"summary": "table summary", "evidence_level": "C"}],
            "warnings": [],
            "pdf_table_cells": [cell],
            "pdf_table_pages": [{"page_no": 1, "status": "parsed"}],
            "pdf_table_diagnostics": [],
            "pdf_table_rule_version": "20260715-text-pdf-v1",
        }
        request = main_module.SelectionPreviewRequest(
            primary_materials=[main_module.MaterialRef(menu_code="project_notice", articleid="article-1")]
        )
        with patch.dict(
            os.environ,
            {
                "ENABLE_STRUCTURED_PDF_TABLES": "true",
                "ENABLE_ATTACHMENT_PARSE_CACHE": "false",
            },
            clear=False,
        ), patch.object(
            main_module,
            "_fetch_database_material_rows",
            return_value={("project_notice", "article-1"): material},
        ), patch.object(
            main_module,
            "_fetch_database_attachments",
            return_value={("project_notice", "article-1"): [attachment]},
        ), patch.object(
            main_module,
            "fetch_attachment_bytes",
            return_value=AttachmentDownloadResult("downloaded", "none", content=content),
        ), patch.object(
            main_module,
            "parse_attachment_bytes",
            return_value=parsed,
        ), patch.object(main_module, "_write_database_evidence_pack"):
            pack = main_module._build_database_evidence_pack(request)

        items = [item for item in pack["evidence_items"] if item["kind"] == "table_cell"]
        self.assertEqual(1, len(items))
        self.assertEqual("A", items[0]["level"])
        self.assertEqual("15%", items[0]["value"])
        self.assertEqual("attachment-1", items[0]["source_ref"]["attachment_id"])
        table_summary = pack["primary_materials"][0]["attachments"][0]["table_summaries"][0]
        self.assertEqual("C", table_summary["evidence_level"])

    def test_pdf_a_evidence_uses_material_and_attachment_identity(self):
        cell = {
            "value": "Stent A",
            "page_no": 2,
            "table_index": 1,
            "row": 3,
            "column": 1,
            "cell_range": "R3C1",
            "quote": "Stent A",
            "source_hash": hashlib.sha256(b"pdf").hexdigest(),
            "region": {"x0": 10, "y0": 20, "x1": 30, "y1": 40, "page_width": 100, "page_height": 100},
            "engine": "pdfplumber",
        }
        item = self.evidence_item(self.source_context(), cell)
        ref = item["source_ref"]
        self.assertEqual("project_notice", ref["menu_code"])
        self.assertEqual("article-1", ref["articleid"])
        self.assertEqual("attachment-1", ref["attachment_id"])
        self.assertEqual("table.pdf", ref["filename"])

    def test_v1_pack_remains_readable_without_in_place_rewrite(self):
        legacy = {"primary_materials": [{"menu_code": "m", "articleid": "a", "content_text": "source"}]}
        before = copy.deepcopy(legacy)
        read_evidence_pack(legacy)
        self.assertEqual(before, legacy)

    def test_feature_flags_off_preserve_previous_pack_shape(self):
        with patch.dict(os.environ, {"ENABLE_STRUCTURED_PDF_TABLES": "false", "ENABLE_PDF_OCR": "false"}, clear=False):
            with patch.object(attachment_parser, "_parse_pdf", return_value="legacy text"):
                result = attachment_parser.parse_attachment_bytes(b"%PDF", "legacy.pdf", ".pdf", 4)
        self.assertNotIn("pdf_table_cells", result)
        self.assertNotIn("parsed_pdf_table_cells", result["parse_statuses"])

    def test_cache_key_changes_with_content_and_parser_rule_versions(self):
        base = {"articleattid": "att-1", "filename": "table.pdf", "filesize": 10, "uploadtime": "t"}
        with patch.dict(os.environ, {"PDF_TABLE_RULE_VERSION": "v1"}, clear=False):
            first = cache_key({**base, "content_sha256": "a" * 64})
        with patch.dict(os.environ, {"PDF_TABLE_RULE_VERSION": "v2"}, clear=False):
            second = cache_key({**base, "content_sha256": "b" * 64})
        self.assertNotEqual(first, second)

    def test_cache_key_changes_with_ocr_configuration(self):
        base = {"articleattid": "att-1", "filename": "table.pdf", "filesize": 10, "uploadtime": "t"}
        with patch.dict(os.environ, {"ENABLE_PDF_OCR": "false", "ATTACHMENT_PDF_OCR_DPI": "160"}, clear=False):
            disabled = cache_key(base)
        with patch.dict(os.environ, {"ENABLE_PDF_OCR": "true", "ATTACHMENT_PDF_OCR_DPI": "240"}, clear=False):
            enabled = cache_key(base)
        self.assertNotEqual(disabled, enabled)

    def test_page_and_character_caps_produce_explicit_degradation(self):
        pages = [_FakePage(1, "12345", []), _FakePage(2, "67890", [])]
        result = self.extract(pages, max_pages=1, max_chars=3)
        codes = {item["code"] for item in result["diagnostics"]}
        self.assertIn("PDF_PAGE_LIMIT_REACHED", codes)
        self.assertIn("PDF_CHARACTER_LIMIT_REACHED", codes)
        self.assertTrue(result["truncated"])


if __name__ == "__main__":
    unittest.main()
