from __future__ import annotations

import copy
from typing import Any

from app.compact_pack import canonical_sha256, mandatory_evidence_retention, stabilize_character_fields
from app.evidence_schema import (
    EvidenceValidationError,
    evidence_item_supports_fact,
    validate_evidence_pack,
)


EVIDENCE_REGRESSION_SCHEMA_VERSION = "8099.s1-evidence-regression/v1"


def _invalid_artifact(pack_id: str, error: EvidenceValidationError) -> dict[str, Any]:
    message = str(error)
    code = "EVIDENCE_SOURCE_REF_MISSING" if "source_ref" in message else "EVIDENCE_SCHEMA_INVALID"
    result = {
        "schema_version": EVIDENCE_REGRESSION_SCHEMA_VERSION,
        "pack_id": pack_id,
        "a_b_count": 0,
        "a_b_supported_count": 0,
        "a_b_support_rate": 0.0,
        "c_count": 0,
        "c_independently_supported_count": 0,
        "c_independent_support_rate": 0.0,
        "vbp_fact_count": 0,
        "vbp_fact_supported_count": 0,
        "mandatory_evidence_retention": {
            "version": "8099.mandatory-evidence-retention/v1",
            "total": 0,
            "retained": 0,
            "missing_ids": [],
            "rate": None,
            "status": "not_available",
        },
        "compact_pack_chars": 0,
        "declared_compact_pack_chars": 0,
        "hard_limit_chars": 80_000,
        "errors": [code],
        "passed": False,
    }
    result["artifact_sha256"] = canonical_sha256(copy.deepcopy(result))
    return result


def _cross_supported_a_b(
    full_items: list[dict[str, Any]],
    compact_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    full_index = {str(item.get("evidence_id") or ""): item for item in full_items}
    compact_ids = {str(item.get("evidence_id") or "") for item in compact_items}
    supported: list[dict[str, Any]] = []
    for item in compact_items:
        if item.get("level") not in {"A", "B"}:
            continue
        evidence_id = str(item.get("evidence_id") or "")
        if full_index.get(evidence_id) != item:
            continue
        if item.get("level") == "B" and any(
            str(dependency or "") not in compact_ids
            for dependency in list(item.get("derived_from") or [])
        ):
            continue
        if evidence_item_supports_fact(item, compact_items):
            supported.append(item)
    return supported


def _fact_projection(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(fact.get(key))
        for key in (
            "evidence_id",
            "fact_type",
            "value",
            "normalized_value",
            "source_ref",
            "derived_from",
            "extractor_version",
            "mandatory",
        )
    }


def _fact_index(value: Any) -> tuple[dict[str, dict[str, Any]], bool]:
    if value is None:
        return {}, True
    if not isinstance(value, list) or any(not isinstance(fact, dict) for fact in value):
        return {}, False
    result: dict[str, dict[str, Any]] = {}
    for fact in value:
        evidence_id = str(fact.get("evidence_id") or "")
        if not evidence_id or evidence_id in result:
            return {}, False
        result[evidence_id] = _fact_projection(fact)
    return result, True


def _vbp_fact_support(
    full_facts: Any,
    compact_facts: Any,
    full_items: list[dict[str, Any]],
    compact_items: list[dict[str, Any]],
) -> tuple[int, int, int, bool]:
    full_fact_index, full_valid = _fact_index(full_facts)
    compact_fact_index, compact_valid = _fact_index(compact_facts)
    full_item_index = {str(item.get("evidence_id") or ""): item for item in full_items}
    compact_item_index = {str(item.get("evidence_id") or ""): item for item in compact_items}
    fact_ids = set(full_fact_index) | set(compact_fact_index)
    supported = 0
    c_fact_count = 0
    for evidence_id in sorted(fact_ids):
        full_fact = full_fact_index.get(evidence_id)
        compact_fact = compact_fact_index.get(evidence_id)
        fact = full_fact or compact_fact or {}
        dependency_ids = [str(value or "") for value in list(fact.get("derived_from") or [])]
        if any(
            (full_item_index.get(candidate_id) or compact_item_index.get(candidate_id) or {}).get("level") == "C"
            for candidate_id in [evidence_id, *dependency_ids]
        ):
            c_fact_count += 1
        if not full_fact or full_fact != compact_fact:
            continue
        full_item = full_item_index.get(evidence_id)
        compact_item = compact_item_index.get(evidence_id)
        if not full_item or full_item != compact_item or full_item.get("level") != "B":
            continue
        item_value = full_item.get("value") if isinstance(full_item.get("value"), dict) else {}
        item_normalized = full_item.get("normalized_value") if isinstance(full_item.get("normalized_value"), dict) else {}
        if (
            str(item_value.get("fact_type") or "") != str(full_fact.get("fact_type") or "")
            or item_value.get("value") != full_fact.get("value")
            or item_normalized.get("value") != full_fact.get("normalized_value")
            or full_item.get("source_ref") != full_fact.get("source_ref")
            or list(full_item.get("derived_from") or []) != dependency_ids
            or str(full_item.get("extractor_version") or "") != str(full_fact.get("extractor_version") or "")
            or bool(full_item.get("mandatory")) != bool(full_fact.get("mandatory"))
        ):
            continue
        if any(
            not full_item_index.get(dependency_id)
            or full_item_index.get(dependency_id) != compact_item_index.get(dependency_id)
            or (full_item_index.get(dependency_id) or {}).get("level") != "A"
            for dependency_id in dependency_ids
        ):
            continue
        supported += 1
    return len(fact_ids), supported, c_fact_count, full_valid and compact_valid


def evaluate_evidence_artifact(full_pack: dict[str, Any], compact_pack: dict[str, Any]) -> dict[str, Any]:
    pack_id = str(full_pack.get("pack_id") or compact_pack.get("pack_id") or "")
    try:
        full = validate_evidence_pack(full_pack)
        compact = validate_evidence_pack(compact_pack)
    except EvidenceValidationError as exc:
        return _invalid_artifact(pack_id, exc)
    compact_items = list(compact["evidence_items"])
    a_b_items = [item for item in compact_items if item.get("level") in {"A", "B"}]
    c_items = [item for item in compact_items if item.get("level") == "C"]
    full_items = list(full["evidence_items"])
    supported_a_b_evidence = _cross_supported_a_b(full_items, compact_items)
    vbp_fact_count, vbp_fact_supported_count, c_supported_count, vbp_facts_valid = _vbp_fact_support(
        full.get("vbp_facts"),
        compact.get("vbp_facts"),
        full_items,
        compact_items,
    )
    a_b_count = len(a_b_items) + vbp_fact_count
    a_b_supported_count = len(supported_a_b_evidence) + vbp_fact_supported_count
    a_b_rate = round(a_b_supported_count / a_b_count, 6) if a_b_count else 0.0
    c_rate = round(c_supported_count / len(c_items), 6) if c_items else 0.0
    retention = mandatory_evidence_retention(full, compact)
    declared_compact_chars = int(compact_pack.get("compact_pack_chars") or 0)
    declared_final_chars = int(compact_pack.get("final_dify_input_chars") or 0)
    recomputed = copy.deepcopy(compact_pack)
    compact_chars = stabilize_character_fields(recomputed)
    hard_limit = int(compact_pack.get("hard_limit_chars") or 80_000)
    errors: list[str] = []
    if not a_b_count:
        errors.append("A_B_EVIDENCE_MISSING")
    elif a_b_rate != 1.0:
        errors.append("A_B_SUPPORT_BELOW_100")
    if len(supported_a_b_evidence) != len(a_b_items):
        errors.append("A_B_EVIDENCE_UNSUPPORTED")
    if c_rate != 0.0:
        errors.append("C_INDEPENDENT_SUPPORT_NONZERO")
    if not vbp_facts_valid:
        errors.append("VBP_FACT_SCHEMA_INVALID")
    if vbp_fact_count != vbp_fact_supported_count:
        errors.append("VBP_FACT_UNSUPPORTED")
    if (
        declared_compact_chars <= 0
        or declared_final_chars <= 0
        or declared_compact_chars != compact_chars
        or declared_final_chars != compact_chars
    ):
        errors.append("COMPACT_CHAR_COUNT_MISMATCH")
    if compact_chars <= 0 or compact_chars > hard_limit:
        errors.append("COMPACT_LIMIT_EXCEEDED")
    if retention["rate"] is not None and float(retention["rate"]) < 0.9:
        errors.append("MANDATORY_EVIDENCE_RETENTION_BELOW_90")

    result = {
        "schema_version": EVIDENCE_REGRESSION_SCHEMA_VERSION,
        "pack_id": str(full.get("pack_id") or compact.get("pack_id") or ""),
        "a_b_count": a_b_count,
        "a_b_supported_count": a_b_supported_count,
        "evidence_a_b_count": len(a_b_items),
        "evidence_a_b_supported_count": len(supported_a_b_evidence),
        "a_b_support_rate": a_b_rate,
        "c_count": len(c_items),
        "c_independently_supported_count": c_supported_count,
        "c_independent_support_rate": c_rate,
        "vbp_fact_count": vbp_fact_count,
        "vbp_fact_supported_count": vbp_fact_supported_count,
        "mandatory_evidence_retention": retention,
        "compact_pack_chars": compact_chars,
        "declared_compact_pack_chars": declared_compact_chars,
        "declared_final_dify_input_chars": declared_final_chars,
        "hard_limit_chars": hard_limit,
        "errors": errors,
        "passed": not errors,
    }
    result["artifact_sha256"] = canonical_sha256(copy.deepcopy(result))
    return result
