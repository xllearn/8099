from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


REPORT_IR_METADATA_FIELDS = (
    "title",
    "suggested_filename",
    "notice_type",
    "publish_date",
    "source_agency",
    "document_name",
    "disclaimer",
)


@dataclass(frozen=True)
class FormalBodyDocument:
    markdown: str = ""
    report_ir: dict[str, Any] | None = None

    def has_content(self) -> bool:
        return markdown_has_body(self.markdown) or report_ir_has_body(self.report_ir)

    def text_segments(self) -> Iterable[tuple[str, str]]:
        if self.markdown:
            yield "markdown", self.markdown
        if self.report_ir:
            yield from iter_report_ir_text(self.report_ir)


def markdown_has_body(markdown: str) -> bool:
    in_fence = False
    for raw_line in str(markdown or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if line.startswith("#"):
            continue
        if line.startswith("|") and _is_markdown_separator(line):
            continue
        visible = line.strip("|*-_`> ")
        if visible:
            return True
    return False


def report_ir_has_body(report_ir: dict[str, Any] | None) -> bool:
    if not isinstance(report_ir, dict):
        return False
    if _has_nonempty_list(report_ir.get("lead_paragraphs")):
        return True
    if _has_nonempty_list(report_ir.get("enterprise_tips")):
        return True
    sections = report_ir.get("sections")
    if not isinstance(sections, list):
        return False
    for section in sections:
        if not isinstance(section, dict):
            continue
        if _has_nonempty_list(section.get("paragraphs")) or _has_nonempty_list(section.get("highlights")):
            return True
        tables = section.get("tables")
        if not isinstance(tables, list):
            continue
        for table in tables:
            if not isinstance(table, dict):
                continue
            headers = table.get("headers")
            rows = table.get("rows")
            if _has_nonempty_list(headers) and isinstance(rows, list) and any(
                isinstance(row, list) and any(str(cell or "").strip() for cell in row)
                for row in rows
            ):
                return True
    return False


def iter_report_ir_text(report_ir: dict[str, Any]) -> Iterable[tuple[str, str]]:
    for field in REPORT_IR_METADATA_FIELDS:
        value = report_ir.get(field)
        if value is not None and str(value).strip():
            yield f"report_ir.{field}", str(value)

    for field in ("lead_paragraphs", "enterprise_tips"):
        values = report_ir.get(field)
        if isinstance(values, list):
            for index, value in enumerate(values):
                if str(value or "").strip():
                    yield f"report_ir.{field}[{index}]", str(value)

    sections = report_ir.get("sections")
    if not isinstance(sections, list):
        return
    for section_index, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        heading = section.get("heading")
        if str(heading or "").strip():
            yield f"report_ir.sections[{section_index}].heading", str(heading)
        for field in ("paragraphs", "highlights"):
            values = section.get(field)
            if isinstance(values, list):
                for value_index, value in enumerate(values):
                    if str(value or "").strip():
                        yield f"report_ir.sections[{section_index}].{field}[{value_index}]", str(value)
        tables = section.get("tables")
        if not isinstance(tables, list):
            continue
        for table_index, table in enumerate(tables):
            if not isinstance(table, dict):
                continue
            title = table.get("title")
            if str(title or "").strip():
                yield f"report_ir.sections[{section_index}].tables[{table_index}].title", str(title)
            for field in ("headers", "notes"):
                values = table.get(field)
                if isinstance(values, list):
                    for value_index, value in enumerate(values):
                        if str(value or "").strip():
                            yield (
                                f"report_ir.sections[{section_index}].tables[{table_index}].{field}[{value_index}]",
                                str(value),
                            )
            rows = table.get("rows")
            if isinstance(rows, list):
                for row_index, row in enumerate(rows):
                    if not isinstance(row, list):
                        continue
                    for column_index, value in enumerate(row):
                        if str(value or "").strip():
                            yield (
                                f"report_ir.sections[{section_index}].tables[{table_index}].rows[{row_index}][{column_index}]",
                                str(value),
                            )


def _has_nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and any(str(item or "").strip() for item in value)


def _is_markdown_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(cell and set(cell) <= {"-", ":", " "} for cell in cells)
