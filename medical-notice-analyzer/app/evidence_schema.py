from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any, Iterable


EVIDENCE_SCHEMA_VERSION = 2
EVIDENCE_LEVELS = {"A", "B", "C"}
EVIDENCE_KINDS = {
    "article_field",
    "article_text",
    "attachment_text",
    "table_cell",
    "derived_fact",
    "summary",
    "guidance",
    "warning",
    "diagnostic",
    "memory",
    "history",
}
DIRECT_EVIDENCE_KINDS = {"article_field", "article_text", "attachment_text", "table_cell"}
DERIVED_EVIDENCE_KINDS = {"derived_fact"}
ARTICLE_ONLY_KINDS = {"article_field", "article_text"}
ATTACHMENT_KINDS = {"attachment_text", "table_cell"}
SOURCE_REF_FIELDS = (
    "menu_code",
    "articleid",
    "attachment_id",
    "filename",
    "page_no",
    "sheet_name",
    "table_index",
    "row",
    "column",
    "cell_range",
    "quote",
    "source_hash",
    "region",
    "bbox",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CELL_RANGE_RE = re.compile(
    r"^(?:R[1-9]\d*C[1-9]\d*(?::R[1-9]\d*C[1-9]\d*)?|[A-Z]+[1-9]\d*(?::[A-Z]+[1-9]\d*)?)$"
)


class EvidenceValidationError(ValueError):
    pass


class EvidenceSchemaVersionError(EvidenceValidationError):
    pass


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EvidenceValidationError("value is not canonical JSON data") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_text_for_hash(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(re.sub(r"[\t ]+$", "", line) for line in text.split("\n"))


def text_source_hash(value: Any) -> str:
    return hashlib.sha256(normalize_text_for_hash(value).encode("utf-8")).hexdigest()


def attachment_source_hash(content: bytes | bytearray | memoryview | None, parsed_text: Any = "") -> str:
    if content is not None:
        try:
            raw = bytes(content)
        except (TypeError, ValueError) as exc:
            raise EvidenceValidationError("attachment content must be bytes-like") from exc
        return hashlib.sha256(raw).hexdigest()
    text_only = f"TEXT_ONLY\n{normalize_text_for_hash(parsed_text)}"
    return hashlib.sha256(text_only.encode("utf-8")).hexdigest()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _positive_location(value: Any, field: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise EvidenceValidationError(f"{field} must be a one-based integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceValidationError(f"{field} must be a one-based integer") from exc
    if result < 1 or str(value).strip() not in {str(result), f"+{result}"}:
        raise EvidenceValidationError(f"{field} must be a one-based integer")
    return result


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise EvidenceValidationError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceValidationError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise EvidenceValidationError(f"{field} must be finite")
    return number


def _validate_region(value: Any, field: str) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise EvidenceValidationError(f"{field} must be an object")
    required = ("x0", "y0", "x1", "y1")
    if any(key not in value for key in required):
        raise EvidenceValidationError(f"{field} requires x0/y0/x1/y1")
    normalized = {key: _finite_number(value[key], f"{field}.{key}") for key in required}
    if normalized["x0"] < 0 or normalized["y0"] < 0:
        raise EvidenceValidationError(f"{field} coordinates cannot be negative")
    if normalized["x1"] <= normalized["x0"] or normalized["y1"] <= normalized["y0"]:
        raise EvidenceValidationError(f"{field} coordinates must be ordered")
    for dimension in ("page_width", "page_height"):
        if value.get(dimension) is not None:
            normalized[dimension] = _finite_number(value[dimension], f"{field}.{dimension}")
            if normalized[dimension] <= 0:
                raise EvidenceValidationError(f"{field}.{dimension} must be positive")
    if normalized.get("page_width") is not None and normalized["x1"] > normalized["page_width"]:
        raise EvidenceValidationError(f"{field} exceeds page width")
    if normalized.get("page_height") is not None and normalized["y1"] > normalized["page_height"]:
        raise EvidenceValidationError(f"{field} exceeds page height")
    return normalized


def validate_source_ref(value: Any, *, require_attachment: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceValidationError("source_ref must be an object")
    menu_code = _optional_text(value.get("menu_code"))
    articleid = _optional_text(value.get("articleid"))
    if not menu_code or not articleid:
        raise EvidenceValidationError("source_ref requires menu_code and articleid")
    source_hash = str(value.get("source_hash") or "")
    if not _SHA256_RE.fullmatch(source_hash):
        raise EvidenceValidationError("source_ref.source_hash must be lowercase sha256")

    attachment_id = _optional_text(value.get("attachment_id"))
    filename = _optional_text(value.get("filename"))
    if bool(attachment_id) != bool(filename):
        raise EvidenceValidationError("attachment_id and filename must be provided together")
    if require_attachment and not attachment_id:
        raise EvidenceValidationError("attachment evidence requires attachment_id and filename")

    locations = {
        field: _positive_location(value.get(field), field)
        for field in ("page_no", "table_index", "row", "column")
    }
    cell_range = _optional_text(value.get("cell_range"))
    if cell_range and not _CELL_RANGE_RE.fullmatch(cell_range):
        raise EvidenceValidationError("cell_range must use A1 or R1C1 notation")
    sheet_name = _optional_text(value.get("sheet_name"))
    has_locator = any(item is not None for item in (*locations.values(), sheet_name, cell_range, value.get("region"), value.get("bbox")))
    if has_locator and not attachment_id:
        raise EvidenceValidationError("attachment locator requires attachment identity")
    quote = str(value.get("quote") or "")
    if len(quote) > 500:
        raise EvidenceValidationError("source_ref.quote must be a short source excerpt")

    return {
        "menu_code": menu_code,
        "articleid": articleid,
        "attachment_id": attachment_id,
        "filename": filename,
        "page_no": locations["page_no"],
        "sheet_name": sheet_name,
        "table_index": locations["table_index"],
        "row": locations["row"],
        "column": locations["column"],
        "cell_range": cell_range,
        "quote": quote,
        "source_hash": source_hash,
        "region": _validate_region(value.get("region"), "region"),
        "bbox": _validate_region(value.get("bbox"), "bbox"),
    }


def _normalize_dependencies(value: Iterable[Any] | None) -> list[str]:
    dependencies = sorted({str(item or "").strip() for item in value or [] if str(item or "").strip()})
    for evidence_id in dependencies:
        if not _SHA256_RE.fullmatch(evidence_id):
            raise EvidenceValidationError("derived_from must contain lowercase sha256 evidence ids")
    return dependencies


def _copy_json(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _kind_specific_source_ref(kind: str, source_ref: dict[str, Any] | None) -> dict[str, Any] | None:
    if source_ref is None:
        return None
    normalized = validate_source_ref(source_ref, require_attachment=kind in ATTACHMENT_KINDS)
    if kind in ARTICLE_ONLY_KINDS:
        attachment_fields = (
            "attachment_id",
            "filename",
            "page_no",
            "sheet_name",
            "table_index",
            "row",
            "column",
            "cell_range",
            "region",
            "bbox",
        )
        if any(normalized.get(field) is not None for field in attachment_fields):
            raise EvidenceValidationError("article evidence cannot contain an attachment locator")
    return normalized


def create_evidence_item(
    *,
    level: str,
    kind: str,
    value: Any,
    normalized_value: Any = None,
    source_ref: dict[str, Any] | None = None,
    derived_from: Iterable[Any] | None = None,
    extractor_version: str | None = None,
    mandatory: bool = False,
) -> dict[str, Any]:
    normalized_level = str(level or "").strip().upper()
    normalized_kind = str(kind or "").strip()
    if normalized_level not in EVIDENCE_LEVELS:
        raise EvidenceValidationError("evidence level must be A, B, or C")
    if normalized_kind not in EVIDENCE_KINDS:
        raise EvidenceValidationError("unsupported evidence kind")
    copied_value = _copy_json(value)
    copied_normalized = None if normalized_value is None else _copy_json(normalized_value)
    normalized_ref = _kind_specific_source_ref(normalized_kind, source_ref)
    dependencies = _normalize_dependencies(derived_from)
    version = _optional_text(extractor_version)
    identity = {
        "level": normalized_level,
        "kind": normalized_kind,
        "value": copied_value,
        "source_ref": normalized_ref,
        "derived_from": dependencies,
        "extractor_version": version,
    }
    return {
        "evidence_id": canonical_sha256(identity),
        "level": normalized_level,
        "kind": normalized_kind,
        "value": copied_value,
        "normalized_value": copied_normalized,
        "source_ref": normalized_ref,
        "derived_from": dependencies,
        "extractor_version": version,
        "mandatory": bool(mandatory),
    }


def _validate_item_shape(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise EvidenceValidationError("evidence item must be an object")
    rebuilt = create_evidence_item(
        level=item.get("level"),
        kind=item.get("kind"),
        value=item.get("value"),
        normalized_value=item.get("normalized_value"),
        source_ref=item.get("source_ref"),
        derived_from=item.get("derived_from"),
        extractor_version=item.get("extractor_version"),
        mandatory=bool(item.get("mandatory")),
    )
    evidence_id = str(item.get("evidence_id") or "")
    if evidence_id != rebuilt["evidence_id"]:
        raise EvidenceValidationError("evidence_id does not match canonical content")
    level = rebuilt["level"]
    kind = rebuilt["kind"]
    if level == "A":
        if kind not in DIRECT_EVIDENCE_KINDS or rebuilt["source_ref"] is None:
            raise EvidenceValidationError("A evidence requires a direct kind and valid source_ref")
        if rebuilt["derived_from"]:
            raise EvidenceValidationError("A evidence cannot derive from other evidence")
    elif level == "B":
        if kind not in DERIVED_EVIDENCE_KINDS or rebuilt["source_ref"] is None:
            raise EvidenceValidationError("B evidence requires derived_fact and valid source_ref")
        if rebuilt["normalized_value"] is None:
            raise EvidenceValidationError("B evidence requires normalized_value")
        if not rebuilt["extractor_version"]:
            raise EvidenceValidationError("B evidence requires extractor_version")
        if not rebuilt["derived_from"]:
            raise EvidenceValidationError("B evidence requires derived_from")
    else:
        if rebuilt["derived_from"]:
            raise EvidenceValidationError("C evidence cannot derive from fact evidence")
    return rebuilt


def _article_evidence_text(item: dict[str, Any]) -> str:
    value = item.get("value")
    if item.get("kind") == "article_field" and isinstance(value, dict):
        return str(value.get("value") or "")
    return str(value or "")


def _validate_direct_source_hash(item: dict[str, Any]) -> None:
    if item.get("level") != "A" or item.get("kind") not in ARTICLE_ONLY_KINDS:
        return
    source_text = _article_evidence_text(item)
    source_ref = item.get("source_ref") or {}
    if source_ref.get("source_hash") != text_source_hash(source_text):
        raise EvidenceValidationError("article evidence source_hash does not match source value")
    quote = normalize_text_for_hash(source_ref.get("quote") or "")
    if quote and quote not in normalize_text_for_hash(source_text):
        raise EvidenceValidationError("article evidence quote does not match source value")


def validate_evidence_pack(pack: Any) -> dict[str, Any]:
    if not isinstance(pack, dict):
        raise EvidenceValidationError("evidence pack must be an object")
    if pack.get("evidence_schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise EvidenceSchemaVersionError("unsupported evidence schema version")
    raw_items = pack.get("evidence_items")
    if not isinstance(raw_items, list):
        raise EvidenceValidationError("evidence_items must be a list")
    items = [_validate_item_shape(item) for item in raw_items]
    index: dict[str, dict[str, Any]] = {}
    for item in items:
        evidence_id = item["evidence_id"]
        if evidence_id in index:
            raise EvidenceValidationError("duplicate evidence_id")
        index[evidence_id] = item
        _validate_direct_source_hash(item)
    for item in items:
        if item["level"] != "B":
            continue
        dependency_hashes: set[str] = set()
        for dependency in item["derived_from"]:
            source = index.get(dependency)
            if source is None or source.get("level") != "A":
                raise EvidenceValidationError("B evidence may derive only from validated A evidence")
            source_ref = source.get("source_ref") if isinstance(source.get("source_ref"), dict) else {}
            if source_ref.get("source_hash"):
                dependency_hashes.add(str(source_ref["source_hash"]))
        item_ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
        if str(item_ref.get("source_hash") or "") not in dependency_hashes:
            raise EvidenceValidationError("B evidence source_hash must reference an A dependency")
    normalized = copy.deepcopy(pack)
    normalized["evidence_schema_version"] = EVIDENCE_SCHEMA_VERSION
    normalized["evidence_items"] = items
    return normalized


def evidence_item_supports_fact(item: Any, evidence_items: Iterable[Any]) -> bool:
    try:
        validated = validate_evidence_pack(
            {
                "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
                "evidence_items": list(evidence_items),
            }
        )
    except EvidenceValidationError:
        return False
    evidence_id = str(item.get("evidence_id") or "") if isinstance(item, dict) else ""
    match = next((candidate for candidate in validated["evidence_items"] if candidate["evidence_id"] == evidence_id), None)
    return bool(match and match["level"] in {"A", "B"})


def fact_support_values(pack: dict[str, Any]) -> list[Any]:
    view = read_evidence_pack(pack)
    return [
        item["value"]
        for item in view["evidence_items"]
        if item["level"] in {"A", "B"}
    ]


def _source_ref_for_article(material: dict[str, Any], text: Any) -> dict[str, Any] | None:
    menu_code = _optional_text(material.get("menu_code"))
    articleid = _optional_text(material.get("articleid"))
    if not menu_code or not articleid:
        return None
    raw_text = str(text or "")
    return validate_source_ref(
        {
            "menu_code": menu_code,
            "articleid": articleid,
            "attachment_id": None,
            "filename": None,
            "page_no": None,
            "sheet_name": None,
            "table_index": None,
            "row": None,
            "column": None,
            "cell_range": None,
            "quote": raw_text[:500],
            "source_hash": text_source_hash(raw_text),
            "region": None,
        }
    )


def _append_unique(items: list[dict[str, Any]], seen: set[str], item: dict[str, Any]) -> None:
    if item["evidence_id"] in seen:
        return
    seen.add(item["evidence_id"])
    items.append(item)


def _adapt_v1_pack(pack: dict[str, Any]) -> dict[str, Any]:
    source = copy.deepcopy(pack)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    materials = [
        item
        for key in ("primary_materials", "auxiliary_materials")
        for item in list(source.get(key) or [])
        if isinstance(item, dict)
    ]
    for material in materials:
        content_text = str(material.get("content_text") or "")
        content_item: dict[str, Any] | None = None
        if content_text:
            ref = _source_ref_for_article(material, content_text)
            if ref:
                content_item = create_evidence_item(
                    level="A",
                    kind="article_text",
                    value=content_text,
                    source_ref=ref,
                    mandatory=material.get("material_role") != "auxiliary",
                )
                _append_unique(
                    items,
                    seen,
                    content_item,
                )
        title = str(material.get("title") or "").strip()
        if title:
            ref = _source_ref_for_article(material, title)
            if ref:
                _append_unique(
                    items,
                    seen,
                    create_evidence_item(
                        level="A",
                        kind="article_field",
                        value={"name": "标题", "value": title},
                        source_ref=ref,
                    ),
                )
        for fact in list(material.get("key_facts") or []):
            if not isinstance(fact, dict):
                continue
            fact_value = str(fact.get("value") or "").strip()
            if not fact_value:
                continue
            fact_payload = {"name": str(fact.get("name") or ""), "value": fact_value}
            if content_item is not None and normalize_text_for_hash(fact_value) in normalize_text_for_hash(content_text):
                _append_unique(
                    items,
                    seen,
                    create_evidence_item(
                        level="B",
                        kind="derived_fact",
                        value=fact_payload,
                        normalized_value=fact_payload,
                        source_ref=content_item["source_ref"],
                        derived_from=[content_item["evidence_id"]],
                        extractor_version="legacy-v1-key-fact-adapter",
                    ),
                )
            else:
                _append_unique(
                    items,
                    seen,
                    create_evidence_item(level="C", kind="summary", value=fact_payload),
                )
        for field in ("summary", "content_summary"):
            summary = str(material.get(field) or "").strip()
            if summary:
                _append_unique(items, seen, create_evidence_item(level="C", kind="summary", value=summary))
        for attachment in list(material.get("attachments") or []):
            if not isinstance(attachment, dict):
                continue
            summary = str(attachment.get("summary") or "").strip()
            if summary:
                _append_unique(
                    items,
                    seen,
                    create_evidence_item(
                        level="C",
                        kind="summary",
                        value={
                            "attachment_id": str(attachment.get("articleattid") or ""),
                            "filename": str(attachment.get("filename") or ""),
                            "summary": summary,
                        },
                    ),
                )
            for warning in list(attachment.get("warnings") or []):
                if str(warning or "").strip():
                    _append_unique(items, seen, create_evidence_item(level="C", kind="warning", value=str(warning)))
    guidance = source.get("generation_guidance")
    if isinstance(guidance, dict) and guidance:
        _append_unique(items, seen, create_evidence_item(level="C", kind="guidance", value=guidance))
    for warning in list(source.get("warnings") or []):
        if str(warning or "").strip():
            _append_unique(items, seen, create_evidence_item(level="C", kind="warning", value=str(warning)))

    view = copy.deepcopy(source)
    view["source_evidence_schema_version"] = 1
    view["evidence_schema_version"] = EVIDENCE_SCHEMA_VERSION
    view["evidence_items"] = items
    return validate_evidence_pack(view)


def read_evidence_pack(pack: Any) -> dict[str, Any]:
    if not isinstance(pack, dict):
        raise EvidenceValidationError("evidence pack must be an object")
    version = pack.get("evidence_schema_version")
    if version is None:
        return _adapt_v1_pack(pack)
    if version != EVIDENCE_SCHEMA_VERSION:
        raise EvidenceSchemaVersionError("unsupported evidence schema version")
    return validate_evidence_pack(pack)
