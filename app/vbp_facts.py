from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import os
import re
import unicodedata
from typing import Any

from app.evidence_schema import (
    create_evidence_item,
    text_source_hash,
    validate_evidence_pack,
    validate_source_ref,
)
from app.report_rules.schema import load_vbp_topic_rules


VBP_FACT_EXTRACTOR_VERSION = "20260715-vbp-facts-v1"
_SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")
_DATE_RE = re.compile(r"(?P<year>20\d{2})[-年/.](?P<month>\d{1,2})[-月/.](?P<day>\d{1,2})日?")
_DURATION_RE = re.compile(r"(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>年|个月|月|天|日)")
_FIELD_FACT_TYPES = {
    "地区": "region",
    "发布机构": "institution",
    "发布时间": "publication_date",
}
_STRUCTURED_FIELDS = (
    ("areaname", "地区"),
    ("publicorg", "发布机构"),
    ("audittime", "发布时间"),
)
_SCALAR_FACT_TYPES = {"procurement_cycle", "region", "institution", "publication_date"}


def vbp_fact_extraction_enabled() -> bool:
    value = (os.getenv("ENABLE_VBP_FACT_EXTRACTION") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip("。；; ")


def _normalize_date(value: str) -> str | None:
    match = _DATE_RE.search(_normalize_text(value))
    if not match:
        return None
    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))
    try:
        datetime(year, month, day)
    except ValueError:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _normalize_duration(value: str) -> str | None:
    match = _DURATION_RE.search(_normalize_text(value))
    if not match:
        return None
    number = match.group("number").rstrip("0").rstrip(".")
    unit = match.group("unit")
    suffix = "Y" if unit == "年" else ("M" if unit in {"个月", "月"} else "D")
    return f"P{number}{suffix}"


def _normalized_topic_value(fact_type: str, raw_value: str) -> str | None:
    if fact_type == "procurement_cycle":
        return _normalize_duration(raw_value)
    return _normalize_text(raw_value) or None


def _material_is_vbp(material: dict[str, Any], rules: dict[str, Any]) -> bool:
    trigger = rules.get("trigger") if isinstance(rules.get("trigger"), dict) else {}
    menu_names = {_normalize_text(item) for item in list(trigger.get("menu_names") or []) if _normalize_text(item)}
    menu_name = _normalize_text(material.get("menu_name"))
    menu_code = _normalize_text(material.get("menu_code"))
    if menu_names and menu_name not in menu_names and menu_code != "project_notice":
        return False

    title_text = " ".join(
        _normalize_text(material.get(field))
        for field in ("title", "projecttype", "project_type")
        if _normalize_text(material.get(field))
    )
    category_text = " ".join(
        _normalize_text(material.get(field))
        for field in ("category", "projecttype", "project_type")
        if _normalize_text(material.get(field))
    )
    title_keywords = [_normalize_text(item) for item in list(trigger.get("title_keywords") or [])]
    category_keywords = [_normalize_text(item) for item in list(trigger.get("category_keywords") or [])]
    return any(keyword and keyword in title_text for keyword in title_keywords) or any(
        keyword and keyword in category_text for keyword in category_keywords
    )


def _eligible_primary_materials(pack: dict[str, Any], rules: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for item in list(pack.get("primary_materials") or []):
        if not isinstance(item, dict) or not _material_is_vbp(item, rules):
            continue
        identity = (str(item.get("menu_code") or ""), str(item.get("articleid") or ""))
        if all(identity):
            result[identity] = item
    return result


def _article_source_ref(material: dict[str, Any], raw_value: str) -> dict[str, Any]:
    return validate_source_ref(
        {
            "menu_code": material.get("menu_code"),
            "articleid": material.get("articleid"),
            "attachment_id": None,
            "filename": None,
            "page_no": None,
            "sheet_name": None,
            "table_index": None,
            "row": None,
            "column": None,
            "cell_range": None,
            "quote": raw_value[:500],
            "source_hash": text_source_hash(raw_value),
            "region": None,
        }
    )


def _structured_field_items(material: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for source_field, evidence_name in _STRUCTURED_FIELDS:
        raw_value = str(material.get(source_field) or "").strip()
        if not raw_value:
            continue
        items.append(
            create_evidence_item(
                level="A",
                kind="article_field",
                value={"name": evidence_name, "value": raw_value},
                source_ref=_article_source_ref(material, raw_value),
                extractor_version=VBP_FACT_EXTRACTOR_VERSION,
                mandatory=True,
            )
        )
    return items


def _candidate(
    source: dict[str, Any],
    *,
    fact_type: str,
    raw_value: str,
    normalized_value: str,
    label: str,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source_ref = copy.deepcopy(source["source_ref"])
    if source.get("kind") != "table_cell":
        source_ref["quote"] = raw_value[:500]
    source_ref = validate_source_ref(
        source_ref,
        require_attachment=source.get("kind") == "table_cell",
    )
    material_key = f"{source_ref['menu_code']}:{source_ref['articleid']}"
    if fact_type in _SCALAR_FACT_TYPES:
        fact_key = f"{material_key}:{fact_type}"
    else:
        normalized_key = json.dumps(normalized_value, ensure_ascii=False, sort_keys=True)
        fact_key = f"{material_key}:{fact_type}:{hashlib.sha256(normalized_key.encode('utf-8')).hexdigest()}"
    return {
        "fact_type": fact_type,
        "fact_key": fact_key,
        "label": label,
        "value": raw_value,
        "normalized_value": normalized_value,
        "source": source,
        "sources": copy.deepcopy(sources or [source]),
        "source_ref": source_ref,
    }


def _article_field_candidates(source: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    value = source.get("value")
    if not isinstance(value, dict):
        return [], []
    name = _normalize_text(value.get("name"))
    raw_value = str(value.get("value") or "").strip()
    fact_type = _FIELD_FACT_TYPES.get(name)
    if not fact_type or not raw_value:
        return [], []
    normalized = _normalize_date(raw_value) if fact_type == "publication_date" else _normalize_text(raw_value)
    if normalized is None:
        return [], [
            {
                "code": "VBP_FACT_INVALID_DATE",
                "fact_type": fact_type,
                "source_evidence_id": str(source.get("evidence_id") or ""),
            }
        ]
    return (
        [
            _candidate(
                source,
                fact_type=fact_type,
                raw_value=raw_value,
                normalized_value=normalized,
                label=name,
            )
        ],
        [],
    )


def _text_candidates(
    source: dict[str, Any],
    topics: list[dict[str, Any]],
    forbidden_phrases: list[str],
) -> list[dict[str, Any]]:
    text = str(source.get("value") or "")
    candidates: list[dict[str, Any]] = []
    for sentence_match in _SENTENCE_RE.finditer(text):
        raw_sentence = sentence_match.group(0)
        normalized_sentence = _normalize_text(raw_sentence)
        if not normalized_sentence:
            continue
        if any(phrase and phrase in normalized_sentence for phrase in forbidden_phrases):
            continue
        for topic in topics:
            fact_type = str(topic.get("fact_type") or topic.get("topic_id") or "").strip()
            for label in list(topic.get("labels") or []):
                clean_label = _normalize_text(label)
                if clean_label and clean_label in normalized_sentence:
                    normalized_value = _normalized_topic_value(fact_type, raw_sentence)
                    if normalized_value is None:
                        continue
                    candidates.append(
                        _candidate(
                            source,
                            fact_type=fact_type,
                            raw_value=raw_sentence,
                            normalized_value=normalized_value,
                            label=clean_label,
                        )
                    )
    return candidates


def _table_row_candidates(
    sources: list[dict[str, Any]],
    topics: list[dict[str, Any]],
    forbidden_phrases: list[str],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for source in sources:
        source_ref = source.get("source_ref") if isinstance(source.get("source_ref"), dict) else {}
        identity = (
            source_ref.get("menu_code"),
            source_ref.get("articleid"),
            source_ref.get("attachment_id"),
            source_ref.get("page_no"),
            source_ref.get("sheet_name"),
            source_ref.get("table_index"),
            source_ref.get("row"),
        )
        grouped.setdefault(identity, []).append(source)

    candidates: list[dict[str, Any]] = []
    for row_sources in grouped.values():
        ordered = sorted(
            row_sources,
            key=lambda item: (
                int((item.get("source_ref") or {}).get("column") or 0),
                str(item.get("evidence_id") or ""),
            ),
        )
        raw_row = " | ".join(str(item.get("value") or "").strip() for item in ordered if str(item.get("value") or "").strip())
        normalized_row = _normalize_text(raw_row)
        if not normalized_row or any(phrase and phrase in normalized_row for phrase in forbidden_phrases):
            continue
        for topic in topics:
            fact_type = str(topic.get("fact_type") or topic.get("topic_id") or "").strip()
            for label in list(topic.get("labels") or []):
                clean_label = _normalize_text(label)
                if not clean_label or clean_label not in normalized_row:
                    continue
                normalized_value = _normalized_topic_value(fact_type, raw_row)
                if normalized_value is None:
                    continue
                source = next(
                    (item for item in ordered if clean_label in _normalize_text(item.get("value"))),
                    ordered[0],
                )
                candidates.append(
                    _candidate(
                        source,
                        fact_type=fact_type,
                        raw_value=raw_row,
                        normalized_value=normalized_value,
                        label=clean_label,
                        sources=ordered,
                    )
                )
    return candidates


def _candidate_to_evidence(candidate: dict[str, Any]) -> dict[str, Any]:
    source = candidate["source"]
    value = {
        "fact_type": candidate["fact_type"],
        "label": candidate["label"],
        "value": candidate["value"],
    }
    normalized = {
        "fact_type": candidate["fact_type"],
        "value": candidate["normalized_value"],
    }
    return create_evidence_item(
        level="B",
        kind="derived_fact",
        value=value,
        normalized_value=normalized,
        source_ref=candidate["source_ref"],
        derived_from=[item["evidence_id"] for item in candidate.get("sources") or [source]],
        extractor_version=VBP_FACT_EXTRACTOR_VERSION,
        mandatory=bool(source.get("mandatory")),
    )


def enrich_vbp_facts(pack: dict[str, Any], *, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    validated = validate_evidence_pack(pack)
    loaded_rules = copy.deepcopy(rules if rules is not None else load_vbp_topic_rules())
    primary = _eligible_primary_materials(validated, loaded_rules)
    topics = [item for item in list(loaded_rules.get("topics") or []) if isinstance(item, dict)]
    forbidden_phrases = [
        _normalize_text(item)
        for item in list(loaded_rules.get("forbidden_phrases") or [])
        if _normalize_text(item)
    ]
    evidence_items = [
        copy.deepcopy(item)
        for item in validated["evidence_items"]
        if item.get("extractor_version") != VBP_FACT_EXTRACTOR_VERSION
    ]
    seen_ids = {str(item.get("evidence_id") or "") for item in evidence_items}
    for material in primary.values():
        for item in _structured_field_items(material):
            if item["evidence_id"] not in seen_ids:
                evidence_items.append(item)
                seen_ids.add(item["evidence_id"])

    candidates: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for source in evidence_items:
        if source.get("level") != "A" or not isinstance(source.get("source_ref"), dict):
            continue
        source_ref = source["source_ref"]
        identity = (str(source_ref.get("menu_code") or ""), str(source_ref.get("articleid") or ""))
        if identity not in primary:
            continue
        if source.get("kind") == "article_field":
            field_candidates, field_diagnostics = _article_field_candidates(source)
            candidates.extend(field_candidates)
            diagnostics.extend(field_diagnostics)
        elif source.get("kind") in {"article_text", "attachment_text"}:
            candidates.extend(_text_candidates(source, topics, forbidden_phrases))

    table_sources = [
        source
        for source in evidence_items
        if source.get("level") == "A"
        and source.get("kind") == "table_cell"
        and isinstance(source.get("source_ref"), dict)
        and (
            str(source["source_ref"].get("menu_code") or ""),
            str(source["source_ref"].get("articleid") or ""),
        ) in primary
    ]
    candidates.extend(_table_row_candidates(table_sources, topics, forbidden_phrases))

    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for candidate in candidates:
        normalized_key = json.dumps(candidate["normalized_value"], ensure_ascii=False, sort_keys=True)
        grouped.setdefault(candidate["fact_key"], {}).setdefault(normalized_key, []).append(candidate)

    accepted: list[dict[str, Any]] = []
    for fact_key in sorted(grouped):
        values = grouped[fact_key]
        first = next(iter(next(iter(values.values()))))
        if len(values) > 1:
            diagnostics.append(
                {
                    "code": "VBP_FACT_CONFLICT",
                    "fact_type": first["fact_type"],
                    "fact_key": fact_key,
                    "candidate_count": sum(len(group) for group in values.values()),
                    "distinct_value_count": len(values),
                    "source_evidence_ids": sorted(
                        {
                            str(item["source"].get("evidence_id") or "")
                            for group in values.values()
                            for item in group
                        }
                    ),
                }
            )
            continue
        selected = sorted(
            next(iter(values.values())),
            key=lambda item: (
                str(item["source"].get("evidence_id") or ""),
                item["label"],
                item["value"],
            ),
        )[0]
        accepted.append(selected)

    facts: list[dict[str, Any]] = []
    for candidate in sorted(
        accepted,
        key=lambda item: (item["fact_type"], item["fact_key"], item["normalized_value"]),
    ):
        evidence = _candidate_to_evidence(candidate)
        evidence_items.append(evidence)
        facts.append(
            {
                "evidence_id": evidence["evidence_id"],
                "fact_type": candidate["fact_type"],
                "value": candidate["value"],
                "normalized_value": candidate["normalized_value"],
                "source_ref": copy.deepcopy(evidence["source_ref"]),
                "derived_from": copy.deepcopy(evidence["derived_from"]),
                "extractor_version": VBP_FACT_EXTRACTOR_VERSION,
                "mandatory": bool(evidence.get("mandatory")),
            }
        )

    result = copy.deepcopy(validated)
    result["evidence_items"] = evidence_items
    result["vbp_facts"] = facts
    result["vbp_fact_diagnostics"] = diagnostics
    result["vbp_fact_extractor_version"] = VBP_FACT_EXTRACTOR_VERSION
    result["vbp_fact_rules_version"] = str(loaded_rules.get("version") or "")
    return validate_evidence_pack(result)
