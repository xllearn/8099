from __future__ import annotations

import copy
import json
import os
import re
from typing import Any

import httpx


_ALLOWED_LLM_ORIGIN = "https://api.deepseek.com"
_DEFAULT_DIRECT_MAX_CHARS = 80_000
_DEFAULT_LLM_CHUNK_CHARS = 55_000
_DEFAULT_LLM_OUTPUT_CHARS = 14_000
_DEFAULT_FINAL_MAX_CHARS = 80_000


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


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def _clean_text(value: Any) -> str:
    text = str("" if value is None else value)
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        compact = re.sub(r"\s+", "", line)
        if re.fullmatch(r"(?:第?\s*\d+\s*页(?:\s*/\s*共?\s*\d+\s*页)?|page\s*\d+(?:\s*of\s*\d+)?)", line, re.I):
            continue
        if len(compact) <= 100 and compact in seen:
            continue
        seen.add(compact)
        lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _fact_text(value: Any) -> str:
    if isinstance(value, dict):
        name = _clean_text(value.get("name") or value.get("field") or "")
        fact_value = _clean_text(value.get("value") or value.get("text") or "")
        if name and fact_value:
            return f"{name}：{fact_value}"
        return fact_value or name
    return _clean_text(value)


def _row_text(value: Any) -> str:
    if isinstance(value, dict):
        parts = [
            f"{_clean_text(key)}：{_clean_text(item)}"
            for key, item in value.items()
            if _clean_text(key) and _clean_text(item)
        ]
        return "；".join(parts)
    if isinstance(value, list):
        return "；".join(item for item in (_clean_text(part) for part in value) if item)
    return _clean_text(value)


def _clean_section(value: Any) -> str:
    if isinstance(value, dict):
        title = _clean_text(value.get("title") or value.get("heading") or "")
        body = _clean_text(value.get("summary") or value.get("text") or value.get("content") or "")
        if title and body:
            return f"{title}：{body}"
        return body or title
    return _clean_text(value)


def _clean_table(table: dict[str, Any]) -> dict[str, Any]:
    headers = [_clean_text(item) for item in list(table.get("headers") or []) if _clean_text(item)]
    rows = [
        text
        for text in (_row_text(item) for item in list(table.get("row_values") or []))
        if text
    ]
    result = {
        "headers": headers,
        "row_count": int(table.get("rows") or table.get("row_count") or 0),
        "summary": _clean_text(table.get("semantic_summary") or table.get("summary") or ""),
        "business_value": _clean_text(table.get("business_value") or ""),
        "rows": list(dict.fromkeys(rows)),
    }
    return {key: value for key, value in result.items() if value not in ("", [], 0)}


def _clean_attachment(attachment: dict[str, Any]) -> dict[str, Any]:
    result = {
        "filename": _clean_text(attachment.get("filename") or ""),
        "summary": _clean_text(attachment.get("summary") or ""),
        "key_facts": list(
            dict.fromkeys(
                fact
                for fact in (_fact_text(item) for item in list(attachment.get("key_facts") or []))
                if fact
            )
        ),
        "important_sections": list(
            dict.fromkeys(
                section
                for section in (
                    _clean_section(item)
                    for item in list(attachment.get("important_sections") or [])
                )
                if section
            )
        ),
        "structured_tables": [
            cleaned
            for cleaned in (
                _clean_table(table)
                for table in list(attachment.get("table_summaries") or [])
                if isinstance(table, dict)
            )
            if cleaned
        ],
    }
    return {key: value for key, value in result.items() if value not in ("", [])}


def _clean_material(material: dict[str, Any], role: str) -> dict[str, Any]:
    result = {
        "role": role,
        "title": _clean_text(material.get("title") or ""),
        "published_at": _clean_text(material.get("audittime") or ""),
        "region": _clean_text(material.get("areaname") or ""),
        "publisher": _clean_text(material.get("publicorg") or ""),
        "project_phase": _clean_text(material.get("projectphase") or ""),
        "project_type": _clean_text(material.get("projecttype") or ""),
        "category": _clean_text(material.get("category") or ""),
        "body": _clean_text(material.get("content_text") or ""),
        "attachments": [
            cleaned
            for cleaned in (
                _clean_attachment(attachment)
                for attachment in list(material.get("attachments") or [])
                if isinstance(attachment, dict)
            )
            if cleaned
        ],
    }
    return {key: value for key, value in result.items() if value not in ("", [])}


def _text_leaf_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for child in value.values():
            values.extend(_text_leaf_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(_text_leaf_values(child))
    elif isinstance(value, str) and value:
        values.append(value)
    return values


def _base_generation_payload(pack: dict[str, Any]) -> dict[str, Any]:
    primary = [
        _clean_material(item, "primary")
        for item in list(pack.get("primary_materials") or [])
        if isinstance(item, dict)
    ]
    auxiliary = [
        _clean_material(item, "auxiliary")
        for item in list(pack.get("auxiliary_materials") or [])
        if isinstance(item, dict)
    ]
    return {
        "pack_variant": "generation_payload",
        "input_strategy": "direct_clean",
        "primary_materials": primary,
        "auxiliary_materials": auxiliary,
        "generation_guidance": {
            "report_must_be_self_contained": True,
            "do_not_ask_reader_to_consult_source_files": True,
            "do_not_show_internal_metadata_or_source_locations": True,
            "do_not_create_appendices": True,
            "large_detail_policy": "逐行明细过多时在正文展示有代表性的部分，并明确展示口径。",
            "fact_policy": "只使用输入中的事实；企业、产品、规格、数量、价格和时间不得推测或改写。",
            "target_report_length": "3000字以上，但避免堆砌原文。",
        },
    }


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    pending = text
    while pending:
        if len(pending) <= limit:
            chunks.append(pending)
            break
        cut = pending.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = pending.rfind("。", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(pending[:cut].strip())
        pending = pending[cut:].strip()
    return [chunk for chunk in chunks if chunk]


def _payload_chunks(payload: dict[str, Any], limit: int) -> list[str]:
    documents: list[str] = []
    for index, material in enumerate(
        [*list(payload.get("primary_materials") or []), *list(payload.get("auxiliary_materials") or [])],
        start=1,
    ):
        role = "主材料" if material.get("role") == "primary" else "辅助材料"
        documents.append(
            f"{role}{index}\n"
            + json.dumps(material, ensure_ascii=False, separators=(",", ":"))
        )
    chunks: list[str] = []
    current = ""
    for document in documents:
        for part in _split_text(document, limit):
            candidate = f"{current}\n\n{part}".strip() if current else part
            if current and len(candidate) > limit:
                chunks.append(current)
                current = part
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def _request_long_summary(chunk: str, output_chars: int) -> str:
    if not _env_bool("ENABLE_LONG_EVIDENCE_LLM", False):
        return ""
    base_url = (os.getenv("EVIDENCE_SUMMARY_LLM_BASE_URL") or _ALLOWED_LLM_ORIGIN).strip().rstrip("/")
    chat_path = (os.getenv("EVIDENCE_SUMMARY_LLM_CHAT_PATH") or "/v1/chat/completions").strip()
    model = (os.getenv("EVIDENCE_SUMMARY_LLM_MODEL") or "deepseek-v4-flash").strip()
    api_key = (os.getenv("EVIDENCE_SUMMARY_LLM_API_KEY") or "").strip()
    if base_url != _ALLOWED_LLM_ORIGIN or not chat_path.startswith("/") or "://" in chat_path:
        return ""
    if not model or not api_key:
        return ""
    payload = {
        "model": model,
        "temperature": 0,
        "thinking": {"type": "disabled"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "你只负责压缩医疗采购公告证据。严格使用输入事实，生成自包含的中文证据摘要。"
                    "必须保留企业、产品、规格、注册证、医保编码、采购量/需求量、价格、时间、申报与执行要求。"
                    "不得补全、纠错、推测或合计；不得要求读者查阅原公告、附件或附录；"
                    "不得输出证据ID、页码、表号、行列号、内部字段名或JSON；直接输出分段纯文本。"
                ),
            },
            {
                "role": "user",
                "content": f"请将以下证据压缩到不超过约{output_chars}个中文字符：\n\n{chunk}",
            },
        ],
    }
    try:
        with httpx.Client(
            timeout=max(10.0, _env_float("LONG_EVIDENCE_LLM_TIMEOUT_SECONDS", 180.0)),
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
        summary = _clean_text(body["choices"][0]["message"]["content"])
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return ""
    return summary[:output_chars]


def _rule_compress(payload: dict[str, Any], max_chars: int) -> dict[str, Any]:
    compressed = copy.deepcopy(payload)
    compressed["input_strategy"] = "rule_compressed_fallback"
    compressed["compression_degraded"] = True
    compressed["generation_warnings"] = ["LONG_EVIDENCE_LLM_UNAVAILABLE"]
    materials = [
        *list(compressed.get("primary_materials") or []),
        *list(compressed.get("auxiliary_materials") or []),
    ]
    body_budget = max(800, (max_chars - 8_000) // max(1, len(materials)))
    for material in materials:
        material["body"] = str(material.get("body") or "")[:body_budget]
        for attachment in list(material.get("attachments") or []):
            attachment["summary"] = str(attachment.get("summary") or "")[:2_500]
            attachment["key_facts"] = list(attachment.get("key_facts") or [])[:30]
            attachment["important_sections"] = list(attachment.get("important_sections") or [])[:12]
            for table in list(attachment.get("structured_tables") or []):
                table["rows"] = list(table.get("rows") or [])[:20]
    serialized = json.dumps(compressed, ensure_ascii=False, separators=(",", ":"))
    while len(serialized) > max_chars:
        candidates: list[tuple[int, Any, Any]] = []

        def collect(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if isinstance(value, str) and len(value) > 200:
                        candidates.append((len(value), node, key))
                    else:
                        collect(value)
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    if isinstance(value, str) and len(value) > 200:
                        candidates.append((len(value), node, index))
                    else:
                        collect(value)

        collect(compressed)
        if not candidates:
            break
        _, parent, key = max(candidates, key=lambda item: item[0])
        parent[key] = parent[key][: max(200, len(parent[key]) * 3 // 4)]
        serialized = json.dumps(compressed, ensure_ascii=False, separators=(",", ":"))
    return compressed


def prepare_generation_payload(pack: dict[str, Any]) -> dict[str, Any]:
    direct_limit = max(10_000, _env_int("DIFY_DIRECT_INPUT_MAX_CHARS", _DEFAULT_DIRECT_MAX_CHARS))
    final_limit = max(10_000, _env_int("DIFY_GENERATION_PAYLOAD_MAX_CHARS", _DEFAULT_FINAL_MAX_CHARS))
    payload = _base_generation_payload(pack)
    unique_text = list(dict.fromkeys(_text_leaf_values(payload)))
    effective_chars = sum(len(value) for value in unique_text)
    payload["effective_content_chars"] = effective_chars
    payload["generation_payload_chars"] = len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    hard_char_limit = max(
        final_limit,
        _env_int("DIFY_EVIDENCE_PACK_HARD_MAX_CHARS", 240_000),
    )
    hard_byte_limit = max(
        16_384,
        _env_int("DIFY_EVIDENCE_PACK_HARD_MAX_BYTES", 870_400),
    )
    direct_serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if (
        effective_chars <= direct_limit
        and len(direct_serialized) <= hard_char_limit
        and len(direct_serialized.encode("utf-8")) <= hard_byte_limit
    ):
        payload["compact_pack_chars"] = len(direct_serialized)
        payload["hard_limit_chars"] = hard_char_limit
        payload["hard_limit_bytes"] = hard_byte_limit
        return payload

    chunk_limit = max(10_000, _env_int("LONG_EVIDENCE_LLM_CHUNK_CHARS", _DEFAULT_LLM_CHUNK_CHARS))
    output_limit = max(2_000, _env_int("LONG_EVIDENCE_LLM_OUTPUT_CHARS", _DEFAULT_LLM_OUTPUT_CHARS))
    chunks = _payload_chunks(payload, chunk_limit)
    per_chunk_output = max(2_000, min(output_limit, (final_limit - 5_000) // max(1, len(chunks))))
    summaries = [_request_long_summary(chunk, per_chunk_output) for chunk in chunks]
    if chunks and all(summaries):
        compressed = {
            "pack_variant": "generation_payload",
            "input_strategy": "llm_compressed",
            "effective_content_chars": effective_chars,
            "compressed_evidence": summaries,
            "generation_guidance": copy.deepcopy(payload["generation_guidance"]),
            "generation_warnings": [],
        }
        serialized = json.dumps(compressed, ensure_ascii=False, separators=(",", ":"))
        if len(serialized) <= final_limit:
            compressed["generation_payload_chars"] = len(serialized)
            compressed["compact_pack_chars"] = len(serialized)
            compressed["hard_limit_chars"] = hard_char_limit
            compressed["hard_limit_bytes"] = hard_byte_limit
            return compressed

    fallback = _rule_compress(payload, final_limit)
    fallback["effective_content_chars"] = effective_chars
    fallback["generation_payload_chars"] = len(
        json.dumps(fallback, ensure_ascii=False, separators=(",", ":"))
    )
    fallback["compact_pack_chars"] = fallback["generation_payload_chars"]
    fallback["hard_limit_chars"] = hard_char_limit
    fallback["hard_limit_bytes"] = hard_byte_limit
    return fallback
