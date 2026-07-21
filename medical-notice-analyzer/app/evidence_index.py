from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable

from app.evidence_schema import EvidenceValidationError, canonical_sha256, read_evidence_pack
from app.formal_body import FormalBodyDocument
from app.report_rules.schema import load_vbp_topic_rules


CLAIM_INDEX_SCHEMA_VERSION = "claim-evidence-index.v1"
MATCHER_VERSION = "20260715-exact-context-v1"

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])")
_MARKDOWN_PREFIX_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)")
_DATE_RE = re.compile(r"20\d{2}\s*(?:年|[-/.])\s*\d{1,2}\s*(?:月|[-/.])\s*\d{1,2}\s*日?")
_VALUE_RE = re.compile(
    r"(?<!\d)\d+(?:,\d{3})*(?:\.\d+)?\s*(?:万元|元(?:/[^，。；;\s|]+)?|%|％|年|个月|月|个工作日|工作日|日|天|个|件|家|份|批|轮)(?!\d)"
)
_REGIONS = (
    "北京市",
    "天津市",
    "上海市",
    "重庆市",
    "河北省",
    "山西省",
    "辽宁省",
    "吉林省",
    "黑龙江省",
    "江苏省",
    "浙江省",
    "安徽省",
    "福建省",
    "江西省",
    "山东省",
    "河南省",
    "湖北省",
    "湖南省",
    "广东省",
    "海南省",
    "四川省",
    "贵州省",
    "云南省",
    "陕西省",
    "甘肃省",
    "青海省",
    "台湾省",
    "内蒙古自治区",
    "广西壮族自治区",
    "西藏自治区",
    "宁夏回族自治区",
    "新疆维吾尔自治区",
    "香港特别行政区",
    "澳门特别行政区",
)
_INSTITUTION_SUFFIXES = (
    "国家医疗保障局",
    "医疗保障局",
    "医保局",
    "公共资源交易中心",
    "医药集中采购平台",
    "采购中心",
    "交易中心",
)
_CLAIM_HINTS = (
    "采购",
    "申报",
    "报价",
    "价格",
    "中选",
    "执行",
    "协议",
    "周期",
    "范围",
    "企业",
    "医疗机构",
    "风险",
    "建议",
    "应",
    "须",
    "明确",
    "为",
)


class EvidenceIndexError(ValueError):
    pass


def normalize_claim_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s+", "", text)
    return text.strip("。！？!?；;，,：:|*-_`> ")


def _display_claim_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip("。！？!?；;，,：:|*-_`> ")


def _looks_like_claim(text: str) -> bool:
    compact = normalize_claim_text(text)
    if len(compact) < 2:
        return False
    return bool(_hard_markers(compact) or len(compact) >= 9 or any(item in compact for item in _CLAIM_HINTS))


def _markdown_claims(markdown: str) -> Iterable[tuple[str, str]]:
    in_fence = False
    table_index = -1
    table_row_index = 0
    table_headers: list[str] | None = None
    lines = str(markdown or "").splitlines()
    for line_index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line:
            table_headers = None
            continue
        if line.startswith("#"):
            table_headers = None
            continue
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if cells and all(cell and set(cell) <= {"-", ":", " "} for cell in cells):
                continue
            next_line = lines[line_index + 1].strip() if line_index + 1 < len(lines) else ""
            next_cells = [cell.strip() for cell in next_line.strip("|").split("|")] if next_line.startswith("|") else []
            if next_cells and all(cell and set(cell) <= {"-", ":", " "} for cell in next_cells):
                table_index += 1
                table_row_index = 0
                table_headers = cells
                continue
            contextual_cells = []
            for column_index, cell in enumerate(cells):
                if not cell:
                    continue
                header = table_headers[column_index] if table_headers and column_index < len(table_headers) else ""
                contextual_cells.append(f"{header}: {cell}" if header else cell)
            text = " | ".join(contextual_cells)
            location = f"markdown.table[{max(table_index, 0)}].row[{table_row_index}]"
            table_row_index += 1
            if _looks_like_claim(text):
                yield location, text
            continue
        table_headers = None
        line = _MARKDOWN_PREFIX_RE.sub("", line)
        for sentence_index, sentence in enumerate(_SENTENCE_SPLIT_RE.split(line)):
            text = _display_claim_text(sentence)
            if _looks_like_claim(text):
                yield f"markdown.line[{line_index}].sentence[{sentence_index}]", text


def _report_ir_claims(report_ir: dict[str, Any] | None) -> Iterable[tuple[str, str]]:
    if not isinstance(report_ir, dict):
        return
    for field in ("lead_paragraphs", "enterprise_tips"):
        values = report_ir.get(field)
        if isinstance(values, list):
            for index, value in enumerate(values):
                for sentence_index, sentence in enumerate(_SENTENCE_SPLIT_RE.split(str(value or ""))):
                    text = _display_claim_text(sentence)
                    if _looks_like_claim(text):
                        yield f"report_ir.{field}[{index}].sentence[{sentence_index}]", text
    sections = report_ir.get("sections")
    if not isinstance(sections, list):
        return
    for section_index, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        for field in ("paragraphs", "highlights"):
            values = section.get(field)
            if not isinstance(values, list):
                continue
            for value_index, value in enumerate(values):
                for sentence_index, sentence in enumerate(_SENTENCE_SPLIT_RE.split(str(value or ""))):
                    text = _display_claim_text(sentence)
                    if _looks_like_claim(text):
                        yield (
                            f"report_ir.sections[{section_index}].{field}[{value_index}].sentence[{sentence_index}]",
                            text,
                        )
        tables = section.get("tables")
        if not isinstance(tables, list):
            continue
        for table_index, table in enumerate(tables):
            if not isinstance(table, dict):
                continue
            headers = [str(item or "").strip() for item in list(table.get("headers") or [])]
            rows = table.get("rows")
            if not isinstance(rows, list):
                continue
            for row_index, row in enumerate(rows):
                if not isinstance(row, list):
                    continue
                cells = []
                for column_index, value in enumerate(row):
                    text = str(value or "").strip()
                    if not text:
                        continue
                    header = headers[column_index] if column_index < len(headers) else ""
                    cells.append(f"{header}: {text}" if header else text)
                row_text = " | ".join(cells)
                if _looks_like_claim(row_text):
                    yield f"report_ir.sections[{section_index}].tables[{table_index}].rows[{row_index}]", row_text


def _claim_id(normalized_text: str) -> str:
    payload = f"{CLAIM_INDEX_SCHEMA_VERSION}\n{normalized_text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _flatten_strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        result: list[str] = []
        for key in sorted(value):
            result.extend(_flatten_strings(value[key]))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_flatten_strings(item))
        return result
    text = str(value or "").strip()
    return [text] if text else []


def _hard_markers(value: str) -> tuple[str, ...]:
    text = unicodedata.normalize("NFKC", str(value or ""))
    markers: list[str] = []
    for pattern in (_DATE_RE, _VALUE_RE):
        markers.extend(normalize_claim_text(match.group(0)).replace("％", "%") for match in pattern.finditer(text))
    for region in _REGIONS:
        if region in text:
            markers.append(region)
            for suffix in _INSTITUTION_SUFFIXES:
                combined = region + suffix
                if combined in text:
                    markers.append(combined)
    for suffix in _INSTITUTION_SUFFIXES:
        if suffix in text:
            markers.append(suffix)
    return tuple(dict.fromkeys(marker for marker in markers if marker))


def _topic_terms(rules: dict[str, Any]) -> tuple[str, ...]:
    terms: list[str] = []
    for topic in list(rules.get("topics") or []):
        if not isinstance(topic, dict):
            continue
        terms.extend(normalize_claim_text(item) for item in list(topic.get("labels") or []))
    terms.extend(normalize_claim_text(item) for item in _CLAIM_HINTS)
    return tuple(dict.fromkeys(term for term in terms if len(term) >= 2))


def _match_score(claim: str, evidence_text: str, terms: tuple[str, ...]) -> int:
    if not claim or not evidence_text:
        return 0
    if claim in evidence_text:
        return 100
    if len(evidence_text) <= 500 and evidence_text in claim:
        return 95
    markers = _hard_markers(claim)
    if markers and not all(marker in evidence_text for marker in markers):
        return 0
    shared_terms = [term for term in terms if term in claim and term in evidence_text]
    if markers and shared_terms:
        return 80 + min(10, len(shared_terms))
    if markers:
        return 0
    longest = max((len(term) for term in shared_terms), default=0)
    if len(shared_terms) >= 2 or longest >= 4:
        return 60 + min(20, len(shared_terms) * 3)
    return 0


def _evidence_entries(pack: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        view = read_evidence_pack(pack)
    except EvidenceValidationError as exc:
        raise EvidenceIndexError("evidence pack validation failed") from exc
    entries: list[dict[str, Any]] = []
    for item in list(view.get("evidence_items") or []):
        if not isinstance(item, dict) or item.get("level") not in {"A", "B"}:
            continue
        source_ref = item.get("source_ref")
        if not isinstance(source_ref, dict):
            raise EvidenceIndexError("A/B evidence requires source_ref")
        texts = _flatten_strings(item.get("value"))
        texts.extend(_flatten_strings(item.get("normalized_value")))
        texts.extend(_flatten_strings(source_ref.get("quote")))
        searchable = normalize_claim_text(" ".join(texts))
        entries.append(
            {
                "evidence_id": str(item.get("evidence_id") or ""),
                "level": str(item.get("level") or ""),
                "source_ref": copy.deepcopy(source_ref),
                "searchable": searchable,
            }
        )
    return sorted(entries, key=lambda item: item["evidence_id"])


def _dedupe_refs(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        ref = copy.deepcopy(entry["source_ref"])
        key = json.dumps(ref, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        refs.append(ref)
    return refs


def build_claim_evidence_index(
    document: FormalBodyDocument,
    pack: dict[str, Any],
    *,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    loaded_rules = copy.deepcopy(rules if rules is not None else load_vbp_topic_rules())
    terms = _topic_terms(loaded_rules)
    evidence = _evidence_entries(pack)
    grouped: dict[str, dict[str, Any]] = {}
    for location, text in [*_markdown_claims(document.markdown), *_report_ir_claims(document.report_ir)]:
        normalized = normalize_claim_text(text)
        if not normalized:
            continue
        claim = grouped.setdefault(
            normalized,
            {
                "claim_id": _claim_id(normalized),
                "text": _display_claim_text(text),
                "normalized_text": normalized,
                "locations": [],
            },
        )
        if location not in claim["locations"]:
            claim["locations"].append(location)

    claims: list[dict[str, Any]] = []
    for normalized in sorted(grouped):
        claim = grouped[normalized]
        scored = [
            (score, entry)
            for entry in evidence
            if (score := _match_score(normalized, entry["searchable"], terms)) > 0
        ]
        best_score = max((score for score, _ in scored), default=0)
        matched = [entry for score, entry in scored if score == best_score or score >= 90]
        matched = sorted({entry["evidence_id"]: entry for entry in matched}.values(), key=lambda item: item["evidence_id"])
        claim.update(
            {
                "supported": bool(matched),
                "evidence_ids": [entry["evidence_id"] for entry in matched],
                "source_refs": _dedupe_refs(matched),
                "support_levels": sorted({entry["level"] for entry in matched}),
                "match_score": best_score,
            }
        )
        claims.append(claim)

    supported = sum(1 for claim in claims if claim["supported"])
    claim_count = len(claims)
    return {
        "schema_version": CLAIM_INDEX_SCHEMA_VERSION,
        "matcher_version": MATCHER_VERSION,
        "rules_version": str(loaded_rules.get("version") or ""),
        "rules_hash": canonical_sha256(loaded_rules),
        "claims": claims,
        "metrics": {
            "claim_count": claim_count,
            "supported_claim_count": supported,
            "unsupported_claim_count": claim_count - supported,
            "ab_support_rate": (supported / claim_count) if claim_count else 1.0,
            "c_independent_support_count": 0,
        },
    }
