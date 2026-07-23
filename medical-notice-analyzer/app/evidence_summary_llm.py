from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from app.attachment_parser import resolve_table_column_map, table_column_map_is_usable


_ALLOWED_ORIGIN = "http://192.168.34.97"
_ALLOWED_FIELDS = {
    "enterprise",
    "product",
    "specification",
    "registration_cert",
    "medical_insurance_code",
    "price",
    "purchase_volume",
    "selected_status",
    "region",
    "group",
}
_MAX_AMBIGUOUS_TABLES = 40
_MAX_MODEL_TABLES_PER_REQUEST = 4
_MAX_MODEL_ROWS_PER_TABLE = 40
_MAX_PROJECTED_ROWS_PER_TABLE = 40
_MAX_MODEL_INPUT_CHARS = 60_000
_FORBIDDEN_SUMMARY_PATTERNS = (
    r"附件",
    r"详见",
    r"查阅(?:原)?附件",
    r"参考(?:原)?附件",
    r"以(?:原始)?附件为准",
    r"附件清单",
    r"附录",
    r"页码",
    r"第\s*\d+\s*页",
    r"表号",
    r"第\s*\d+\s*表",
    r"行列号",
    r"列索引",
    r"table-\d+",
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def _table_cell_rows(pack: dict[str, Any]) -> dict[tuple[str, int, int], list[list[str]]]:
    grouped: dict[tuple[str, int, int], dict[int, dict[int, str]]] = {}
    for item in list(pack.get("evidence_items") or []):
        if not isinstance(item, dict) or item.get("kind") != "table_cell":
            continue
        source_ref = item.get("source_ref")
        if not isinstance(source_ref, dict):
            continue
        try:
            row_index = int(source_ref.get("row"))
            column_index = int(source_ref.get("column"))
            page_no = int(source_ref.get("page_no") or 0)
            table_index = int(source_ref.get("table_index") or 0)
        except (TypeError, ValueError):
            continue
        attachment_id = str(source_ref.get("attachment_id") or "")
        if not attachment_id or row_index < 0 or column_index < 0:
            continue
        key = (attachment_id, page_no, table_index)
        grouped.setdefault(key, {}).setdefault(row_index, {})[column_index] = str(
            "" if item.get("value") is None else item.get("value")
        )

    result: dict[tuple[str, int, int], list[list[str]]] = {}
    for key, indexed_rows in grouped.items():
        rows: list[list[str]] = []
        for row_index in sorted(indexed_rows):
            indexed_values = indexed_rows[row_index]
            width = max(indexed_values, default=-1) + 1
            rows.append([indexed_values.get(column, "") for column in range(width)])
        result[key] = rows
    return result


def _table_rows(
    table: dict[str, Any],
    *,
    attachment_id: str,
    fallback_rows: dict[tuple[str, int, int], list[list[str]]],
) -> list[list[str]]:
    source_rows = table.get("source_rows")
    if isinstance(source_rows, list) and source_rows:
        return [
            [str("" if value is None else value) for value in row]
            for row in source_rows
            if isinstance(row, list)
        ]
    try:
        page_no = int(table.get("page_no") or 0)
        table_index = int(table.get("table_index") or 0)
    except (TypeError, ValueError):
        return []
    return [list(row) for row in fallback_rows.get((attachment_id, page_no, table_index), [])]


def _project_rows(rows: list[list[str]], column_map: dict[str, int]) -> list[dict[str, str]]:
    projected: list[dict[str, str]] = []
    for row in rows:
        item = {
            field: str(row[index]).strip()
            for field, index in column_map.items()
            if field in _ALLOWED_FIELDS and 0 <= index < len(row) and str(row[index]).strip()
        }
        if item:
            projected.append(item)
        if len(projected) >= _MAX_PROJECTED_ROWS_PER_TABLE:
            break
    return projected


def _trim_cell(value: Any) -> str:
    return re.sub(r"\s+", " ", str("" if value is None else value)).strip()[:300]


def _model_table_payload(table_id: str, table: dict[str, Any], rows: list[list[str]]) -> dict[str, Any]:
    headers = [_trim_cell(value) for value in list(table.get("headers") or [])]
    return {
        "table_id": table_id,
        "columns": [
            {"index": index, "header": header}
            for index, header in enumerate(headers)
        ],
        "rows": [
            [_trim_cell(value) for value in row]
            for row in rows[:_MAX_MODEL_ROWS_PER_TABLE]
        ],
        "rule_summary": _trim_cell(table.get("summary")),
        "field_stats": table.get("field_stats") if isinstance(table.get("field_stats"), dict) else {},
    }


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("model response must be a JSON object")
    return value


def _summary_is_self_contained(summary: str) -> bool:
    return not any(re.search(pattern, summary, flags=re.IGNORECASE) for pattern in _FORBIDDEN_SUMMARY_PATTERNS)


def _request_summaries(tables: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not _env_bool("ENABLE_EVIDENCE_SUMMARY_LLM") or not tables:
        return {}
    base_url = (os.getenv("EVIDENCE_SUMMARY_LLM_BASE_URL") or _ALLOWED_ORIGIN).strip().rstrip("/")
    chat_path = (os.getenv("EVIDENCE_SUMMARY_LLM_CHAT_PATH") or "/v1/chat/completions").strip()
    model = (os.getenv("EVIDENCE_SUMMARY_LLM_MODEL") or "elian-deepseek-v4-flash").strip()
    api_key = (os.getenv("EVIDENCE_SUMMARY_LLM_API_KEY") or "").strip()
    if base_url != _ALLOWED_ORIGIN or not chat_path.startswith("/") or "://" in chat_path:
        return {}
    if not model or not api_key:
        return {}

    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You summarize ambiguous medical-procurement tables and map column indexes only. "
                    "Use only the supplied rows. Never invent, correct, total, or replace enterprise names, "
                    "product names, quantities, prices, or units. Return one JSON object with a tables array. "
                    "Each item must contain table_id, a concise Chinese summary, and column_map. "
                    "column_map values are zero-based indexes and keys may only be enterprise, product, specification, "
                    "registration_cert, medical_insurance_code, price, purchase_volume, selected_status, region, or group. "
                    "The summary must stand on its own and must not mention table IDs, pages, attachments, appendices, "
                    "source tracing, or instruct the reader to consult another file."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"tables": tables}, ensure_ascii=False, separators=(",", ":")),
            },
        ],
    }
    try:
        with httpx.Client(
            timeout=max(5.0, _env_float("EVIDENCE_SUMMARY_LLM_TIMEOUT_SECONDS", 90.0)),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = client.post(
                f"{base_url}{chat_path}",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        parsed = _parse_json_object(content)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return {}

    requested = {str(table.get("table_id") or ""): table for table in tables}
    result: dict[str, dict[str, Any]] = {}
    for item in list(parsed.get("tables") or []):
        if not isinstance(item, dict):
            continue
        table_id = str(item.get("table_id") or "")
        original = requested.get(table_id)
        if original is None:
            continue
        columns = list(original.get("columns") or [])
        width = len(columns)
        summary = str(item.get("summary") or "").strip()[:1200]
        raw_map = item.get("column_map")
        column_map: dict[str, int] = {}
        if isinstance(raw_map, dict):
            for field, raw_index in raw_map.items():
                if str(field) not in _ALLOWED_FIELDS:
                    continue
                try:
                    index = int(raw_index)
                except (TypeError, ValueError):
                    continue
                if 0 <= index < width:
                    column_map[str(field)] = index
        if (
            summary
            and _summary_is_self_contained(summary)
            and column_map
            and len(set(column_map.values())) == len(column_map)
        ):
            result[table_id] = {"summary": summary, "column_map": column_map}
    return result


def _request_summaries_batched(tables: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for start in range(0, len(tables), _MAX_MODEL_TABLES_PER_REQUEST):
        batch = tables[start : start + _MAX_MODEL_TABLES_PER_REQUEST]
        expected = {
            str(table.get("table_id") or "")
            for table in batch
            if str(table.get("table_id") or "")
        }
        batch_results = _request_summaries(batch)
        accepted = {
            table_id: value
            for table_id, value in batch_results.items()
            if table_id in expected
        }
        result.update(accepted)
        if not accepted:
            break

        missing = expected - accepted.keys()
        for table in batch:
            table_id = str(table.get("table_id") or "")
            if table_id not in missing:
                continue
            retry_results = _request_summaries([table])
            if table_id in retry_results:
                result[table_id] = retry_results[table_id]
    return result


def enrich_evidence_pack_summaries(pack: dict[str, Any]) -> dict[str, Any]:
    guidance = pack.setdefault("generation_guidance", {})
    if isinstance(guidance, dict):
        guidance["self_contained_report_rule"] = (
            "报告正文必须自包含；核心企业、产品、规格、需求量和价格事实不得引导读者查阅原附件。"
        )
        guidance["large_row_detail_rule"] = (
            "逐行明细过多时，只在正文展示部分有代表性的原始行并说明展示口径；不得生成附录。"
        )
        guidance["source_trace_visibility_rule"] = (
            "页码、表号、行列号和列索引仅供内部证据处理，不得写入报告正文。"
        )
    fallback_rows = _table_cell_rows(pack)
    ambiguous: list[tuple[str, dict[str, Any], list[list[str]]]] = []
    table_number = 0
    for material in [*(pack.get("primary_materials") or []), *(pack.get("auxiliary_materials") or [])]:
        if not isinstance(material, dict):
            continue
        for attachment in list(material.get("attachments") or []):
            if not isinstance(attachment, dict):
                continue
            attachment_id = str(attachment.get("articleattid") or "")
            for table in list(attachment.get("table_summaries") or []):
                if not isinstance(table, dict):
                    continue
                table_id = f"table-{table_number}"
                table_number += 1
                headers = [str("" if value is None else value) for value in list(table.get("headers") or [])]
                if "rows" not in table and "row_count" in table:
                    table["rows"] = table.get("row_count") or 0
                if "columns_count" not in table and "column_count" in table:
                    table["columns_count"] = table.get("column_count") or 0
                resolved, detected_status = resolve_table_column_map(headers)
                stored_map = table.get("resolved_column_map")
                if isinstance(stored_map, dict):
                    for field, raw_index in stored_map.items():
                        try:
                            index = int(raw_index)
                        except (TypeError, ValueError):
                            continue
                        if str(field) in _ALLOWED_FIELDS and 0 <= index < len(headers):
                            resolved[str(field)] = index
                stored_status = str(table.get("mapping_status") or detected_status)
                mapping_status = (
                    "clear"
                    if stored_status == "clear" and table_column_map_is_usable(resolved)
                    else "ambiguous"
                )
                rows = _table_rows(table, attachment_id=attachment_id, fallback_rows=fallback_rows)
                if rows and [str(value) for value in rows[0]] == headers:
                    rows = rows[1:]
                table["resolved_column_map"] = resolved
                table["mapping_status"] = mapping_status
                if mapping_status == "clear":
                    table["semantic_summary"] = str(table.get("summary") or "")
                    table["semantic_column_map"] = resolved
                    table["semantic_summary_source"] = "rules"
                    table["row_values"] = _project_rows(rows, resolved)
                elif rows and len(ambiguous) < _MAX_AMBIGUOUS_TABLES:
                    ambiguous.append((table_id, table, rows))

    request_tables: list[dict[str, Any]] = []
    selected: list[tuple[str, dict[str, Any], list[list[str]]]] = []
    for table_id, table, rows in ambiguous:
        candidate = _model_table_payload(table_id, table, rows)
        next_tables = [*request_tables, candidate]
        if len(json.dumps(next_tables, ensure_ascii=False, separators=(",", ":"))) > _MAX_MODEL_INPUT_CHARS:
            break
        request_tables = next_tables
        selected.append((table_id, table, rows))

    model_results = _request_summaries_batched(request_tables)
    if request_tables and _env_bool("ENABLE_EVIDENCE_SUMMARY_LLM") and not model_results:
        warnings = pack.setdefault("warnings", [])
        if "EVIDENCE_SUMMARY_LLM_UNAVAILABLE" not in warnings:
            warnings.append("EVIDENCE_SUMMARY_LLM_UNAVAILABLE")
    for table_id, table, rows in selected:
        result = model_results.get(table_id)
        if not result:
            continue
        model_map = result["column_map"]
        rule_map = table.get("resolved_column_map") if isinstance(table.get("resolved_column_map"), dict) else {}
        conflicts = {
            field
            for field, index in rule_map.items()
            if field in model_map and model_map[field] != index
        }
        if conflicts:
            continue
        column_map = {**model_map, **rule_map}
        if not table_column_map_is_usable(column_map):
            continue
        table["semantic_summary"] = result["summary"] or str(table.get("summary") or "")
        table["semantic_column_map"] = column_map
        table["semantic_summary_source"] = "deepseek"
        table["row_values"] = _project_rows(rows, column_map)
    return pack
