from __future__ import annotations

import hashlib
import io
import json
import os
import re
from typing import Any

import pdfplumber


PDF_TABLE_RULE_VERSION = "20260715-text-pdf-v1"
PDF_TABLE_ENGINE = "pdfplumber"
OCR_LOW_TEXT_THRESHOLD = 200


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def rule_version() -> str:
    return (os.getenv("PDF_TABLE_RULE_VERSION") or PDF_TABLE_RULE_VERSION).strip() or PDF_TABLE_RULE_VERSION


def ocr_enabled() -> bool:
    return _env_bool("ENABLE_PDF_OCR", False) and _env_bool("ENABLE_IMAGE_TABLE_OCR", False)


def is_ocr_candidate(*, is_scanned: bool, text_chars: int, key_table_readable: bool) -> bool:
    if not is_scanned:
        return False
    return int(text_chars) < OCR_LOW_TEXT_THRESHOLD or not key_table_readable


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _matrix_value(matrix: list[list[Any]], row: int, column: int) -> Any:
    if row >= len(matrix) or column >= len(matrix[row]):
        return None
    return matrix[row][column]


def _normalized_matrix(matrix: Any) -> list[list[str]]:
    if not isinstance(matrix, list):
        return []
    result: list[list[str]] = []
    for row in matrix:
        if not isinstance(row, (list, tuple)):
            continue
        result.append([str(value).strip() if value is not None else "" for value in row])
    return result


def _fingerprint(matrix: list[list[str]]) -> str:
    canonical = [[_normalized_text(value) for value in row] for row in matrix]
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bbox_tuple(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _region(value: Any, page_width: float, page_height: float) -> dict[str, float] | None:
    bbox = _bbox_tuple(value)
    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox
    x0 = min(max(x0, 0.0), page_width)
    x1 = min(max(x1, 0.0), page_width)
    y0 = min(max(y0, 0.0), page_height)
    y1 = min(max(y1, 0.0), page_height)
    if x1 <= x0 or y1 <= y0:
        return None
    return {
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "page_width": page_width,
        "page_height": page_height,
    }


def _fallback_cell_bbox(
    table_bbox: tuple[float, float, float, float],
    row: int,
    column: int,
    row_count: int,
    column_count: int,
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = table_bbox
    row_height = (y1 - y0) / max(row_count, 1)
    column_width = (x1 - x0) / max(column_count, 1)
    return (
        x0 + column * column_width,
        y0 + row * row_height,
        x0 + (column + 1) * column_width,
        y0 + (row + 1) * row_height,
    )


def _cell_range(coordinates: list[tuple[int, int]]) -> str:
    rows = [row for row, _ in coordinates]
    columns = [column for _, column in coordinates]
    start = f"R{min(rows) + 1}C{min(columns) + 1}"
    end = f"R{max(rows) + 1}C{max(columns) + 1}"
    return start if start == end else f"{start}:{end}"


def _table_record(
    table: Any,
    *,
    page_no: int,
    table_index: int,
    page_width: float,
    page_height: float,
    source_hash: str,
) -> dict[str, Any]:
    matrix = _normalized_matrix(table.extract())
    row_count = len(matrix)
    column_count = max((len(row) for row in matrix), default=0)
    table_bbox = _bbox_tuple(getattr(table, "bbox", None)) or (0.0, 0.0, page_width, page_height)
    table_rows = list(getattr(table, "rows", []) or [])
    grouped: dict[tuple[float, float, float, float], dict[str, Any]] = {}

    for row_index in range(row_count):
        row_bboxes = list(getattr(table_rows[row_index], "cells", []) or []) if row_index < len(table_rows) else []
        for column_index in range(column_count):
            raw_value = _matrix_value(matrix, row_index, column_index)
            bbox = _bbox_tuple(row_bboxes[column_index]) if column_index < len(row_bboxes) else None
            bbox = bbox or _fallback_cell_bbox(table_bbox, row_index, column_index, row_count, column_count)
            key = tuple(round(value, 4) for value in bbox)
            group = grouped.setdefault(key, {"coordinates": [], "values": [], "bbox": bbox})
            group["coordinates"].append((row_index, column_index))
            if raw_value is not None and str(raw_value).strip():
                group["values"].append(str(raw_value).strip())

    cells: list[dict[str, Any]] = []
    for group in grouped.values():
        if not group["values"]:
            continue
        coordinates = list(group["coordinates"])
        value = group["values"][0]
        row_index = min(row for row, _ in coordinates)
        column_index = min(column for _, column in coordinates)
        cells.append(
            {
                "value": value,
                "page_no": page_no,
                "table_index": table_index,
                "row": row_index + 1,
                "column": column_index + 1,
                "cell_range": _cell_range(coordinates),
                "quote": value[:500],
                "source_hash": source_hash,
                "region": _region(group["bbox"], page_width, page_height),
                "engine": PDF_TABLE_ENGINE,
            }
        )

    return {
        "page_no": page_no,
        "table_index": table_index,
        "region": _region(table_bbox, page_width, page_height),
        "matrix": matrix,
        "cells": cells,
        "fingerprint": _fingerprint(matrix),
        "engine": PDF_TABLE_ENGINE,
    }


def _dedupe_key(table: dict[str, Any]) -> tuple[Any, ...]:
    matrix = _normalized_matrix(table.get("matrix"))
    fingerprint = str(table.get("fingerprint") or _fingerprint(matrix))
    region = table.get("region") if isinstance(table.get("region"), dict) else {}
    location = tuple(round(float(region.get(key) or 0.0), 2) for key in ("x0", "y0", "x1", "y1"))
    return int(table.get("page_no") or 0), fingerprint, location


def deduplicate_tables(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for table in tables:
        key = _dedupe_key(table)
        if key in seen:
            continue
        seen.add(key)
        copied = dict(table)
        copied["fingerprint"] = key[1]
        result.append(copied)
    return result


def _diagnostic(code: str, *, page_no: int | None = None, error: Exception | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "page_no": page_no,
        "error_type": error.__class__.__name__ if error is not None else "",
    }


def extract_pdf_tables(
    content: bytes,
    *,
    max_pages: int | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    page_limit = max(1, int(max_pages if max_pages is not None else _env_int("ATTACHMENT_PDF_MAX_PAGES", 120)))
    character_limit = max(1, int(max_chars if max_chars is not None else _env_int("ATTACHMENT_PDF_MAX_EXTRACT_CHARS", 60000)))
    source_hash = hashlib.sha256(content).hexdigest()
    diagnostics: list[dict[str, Any]] = []
    pages_output: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    text_parts: list[str] = []
    total_chars = 0
    truncated = False

    try:
        document_context = pdfplumber.open(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "text": "",
            "pages": [],
            "tables": [],
            "cells": [],
            "diagnostics": [_diagnostic("PDF_DOCUMENT_OPEN_FAILED", error=exc)],
            "truncated": False,
            "source_hash": source_hash,
            "rule_version": rule_version(),
        }

    try:
        with document_context as document:
            document_pages = list(document.pages)
            if len(document_pages) > page_limit:
                diagnostics.append(_diagnostic("PDF_PAGE_LIMIT_REACHED"))
                truncated = True
            for fallback_page_no, page in enumerate(document_pages[:page_limit], start=1):
                page_no = int(getattr(page, "page_number", fallback_page_no) or fallback_page_no)
                page_width = max(float(getattr(page, "width", 0.0) or 0.0), 1.0)
                page_height = max(float(getattr(page, "height", 0.0) or 0.0), 1.0)
                page_diagnostics: list[dict[str, Any]] = []
                try:
                    page_text = str(page.extract_text() or "")
                except Exception as exc:  # noqa: BLE001
                    page_text = ""
                    item = _diagnostic("PDF_PAGE_TEXT_EXTRACTION_FAILED", page_no=page_no, error=exc)
                    diagnostics.append(item)
                    page_diagnostics.append(item)

                remaining = max(character_limit - total_chars, 0)
                if len(page_text) > remaining:
                    page_text = page_text[:remaining]
                    item = _diagnostic("PDF_CHARACTER_LIMIT_REACHED", page_no=page_no)
                    diagnostics.append(item)
                    page_diagnostics.append(item)
                    truncated = True
                total_chars += len(page_text)
                if page_text:
                    text_parts.append(page_text)

                try:
                    found_tables = list(page.find_tables() or [])
                    for table_index, table in enumerate(found_tables, start=1):
                        try:
                            tables.append(
                                _table_record(
                                    table,
                                    page_no=page_no,
                                    table_index=table_index,
                                    page_width=page_width,
                                    page_height=page_height,
                                    source_hash=source_hash,
                                )
                            )
                        except Exception as exc:  # noqa: BLE001
                            item = _diagnostic("PDF_TABLE_EXTRACTION_FAILED", page_no=page_no, error=exc)
                            diagnostics.append(item)
                            page_diagnostics.append(item)
                except Exception as exc:  # noqa: BLE001
                    item = _diagnostic("PDF_PAGE_TABLE_EXTRACTION_FAILED", page_no=page_no, error=exc)
                    diagnostics.append(item)
                    page_diagnostics.append(item)

                pages_output.append(
                    {
                        "page_no": page_no,
                        "text_chars": len(_normalized_text(page_text)),
                        "page_width": page_width,
                        "page_height": page_height,
                        "status": "partial" if page_diagnostics else "parsed",
                        "diagnostics": page_diagnostics,
                    }
                )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "text": "\n".join(text_parts),
            "pages": pages_output,
            "tables": [],
            "cells": [],
            "diagnostics": [*diagnostics, _diagnostic("PDF_DOCUMENT_READ_FAILED", error=exc)],
            "truncated": truncated,
            "source_hash": source_hash,
            "rule_version": rule_version(),
        }

    unique_tables = deduplicate_tables(tables)
    cells = [cell for table in unique_tables for cell in table.get("cells") or []]
    degraded = bool(diagnostics)
    return {
        "status": "partial" if degraded else "parsed",
        "text": "\n".join(text_parts),
        "pages": pages_output,
        "tables": unique_tables,
        "cells": cells,
        "diagnostics": diagnostics,
        "truncated": truncated,
        "source_hash": source_hash,
        "rule_version": rule_version(),
    }
