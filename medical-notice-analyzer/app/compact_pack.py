from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Iterable

from app.evidence_schema import validate_evidence_pack


COMPACT_POLICY_VERSION = "20260715-vbp-evidence-preservation-v1"
MANDATORY_RETENTION_VERSION = "8099.mandatory-evidence-retention/v1"
DEFAULT_HARD_LIMIT = 80_000
MANDATORY_FIELDS_LIMIT = 79_000


class CompactPolicyError(ValueError):
    def __init__(self, code: str, message: str, *, metrics: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.metrics = copy.deepcopy(metrics or {})


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True))


def secondary_compression_plan(first_pass_chars: int, *, hard_limit: int = DEFAULT_HARD_LIMIT) -> dict[str, Any]:
    size = max(0, int(first_pass_chars))
    limit = max(1, int(hard_limit))
    if size <= limit:
        return {
            "required": False,
            "tier": "none",
            "target_floor_chars": size,
            "target_ceiling_chars": size,
        }
    if size <= limit + 10_000:
        tier = "light"
        floor = limit - 2_000
        ceiling = limit - 500
    elif size <= 150_000:
        tier = "medium"
        floor = limit - 10_000
        ceiling = limit - 1_000
    else:
        tier = "strong"
        floor = limit - 5_000
        ceiling = limit - 1_000
    return {
        "required": True,
        "tier": tier,
        "target_floor_chars": max(1_000, floor),
        "target_ceiling_chars": max(1_000, ceiling),
    }


def _evidence_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("evidence_items")
    return [copy.deepcopy(item) for item in list(value or []) if isinstance(item, dict)]


def mandatory_evidence_retention(before: Any, after: Any) -> dict[str, Any]:
    before_ids = {
        str(item.get("evidence_id") or "")
        for item in _evidence_items(before)
        if item.get("mandatory") and item.get("evidence_id")
    }
    after_ids = {
        str(item.get("evidence_id") or "")
        for item in _evidence_items(after)
        if item.get("evidence_id")
    }
    missing = sorted(before_ids - after_ids)
    retained = len(before_ids) - len(missing)
    return {
        "version": MANDATORY_RETENTION_VERSION,
        "total": len(before_ids),
        "retained": retained,
        "missing_ids": missing,
        "rate": None if not before_ids else round(retained / len(before_ids), 6),
        "status": "not_applicable" if not before_ids else "measured",
    }


def _protected_evidence_ids(items: Iterable[dict[str, Any]]) -> set[str]:
    protected = {
        str(item.get("evidence_id") or "")
        for item in items
        if item.get("mandatory") and item.get("evidence_id")
    }
    for item in items:
        if item.get("level") != "B":
            continue
        evidence_id = str(item.get("evidence_id") or "")
        if evidence_id:
            protected.add(evidence_id)
        protected.update(str(value) for value in list(item.get("derived_from") or []) if str(value))
    return protected


def protected_evidence_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    materialized = _evidence_items(list(items))
    protected_ids = _protected_evidence_ids(materialized)
    return [
        copy.deepcopy(item)
        for item in materialized
        if str(item.get("evidence_id") or "") in protected_ids
    ]


def _mandatory_dependency_closure(items: Iterable[dict[str, Any]]) -> set[str]:
    materialized = [item for item in items if isinstance(item, dict)]
    index = {
        str(item.get("evidence_id") or ""): item
        for item in materialized
        if item.get("evidence_id")
    }
    closure = {
        evidence_id
        for evidence_id, item in index.items()
        if item.get("mandatory")
    }
    pending = list(closure)
    while pending:
        current = index.get(pending.pop()) or {}
        for dependency in list(current.get("derived_from") or []):
            dependency_id = str(dependency or "")
            if dependency_id and dependency_id in index and dependency_id not in closure:
                closure.add(dependency_id)
                pending.append(dependency_id)
    return closure


def _optional_priority(item: dict[str, Any], index: int) -> tuple[int, int, int]:
    level = str(item.get("level") or "")
    kind = str(item.get("kind") or "")
    if level == "A":
        kind_rank = {"article_text": 0, "article_field": 1, "table_cell": 2}.get(kind, 3)
        return (0, kind_rank, index)
    if level == "B":
        return (1, 0, index)
    return (2, 0, index)


def stabilize_character_fields(pack: dict[str, Any]) -> int:
    previous = -1
    current = json_chars(pack)
    for _ in range(8):
        pack["compact_pack_chars"] = current
        pack["final_dify_input_chars"] = current
        next_size = json_chars(pack)
        if next_size == current or next_size == previous:
            current = next_size
            pack["compact_pack_chars"] = current
            pack["final_dify_input_chars"] = current
            return current
        previous = current
        current = next_size
    pack["compact_pack_chars"] = current
    pack["final_dify_input_chars"] = current
    return current


def _annotate_candidate(
    candidate: dict[str, Any],
    *,
    baseline_items: list[dict[str, Any]],
    omitted_count: int,
    omitted_ids: list[str],
) -> dict[str, Any]:
    candidate["secondary_compression"] = True
    candidate["compact_policy_version"] = COMPACT_POLICY_VERSION
    candidate["evidence_items_omitted_count"] = omitted_count
    candidate["evidence_items_omitted_sha256"] = canonical_sha256(sorted(omitted_ids))
    candidate["mandatory_evidence_retention"] = mandatory_evidence_retention(
        baseline_items,
        candidate.get("evidence_items") or [],
    )
    stabilize_character_fields(candidate)
    return candidate


def annotate_secondary_compression(
    pack: dict[str, Any],
    *,
    baseline_items: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    result = copy.deepcopy(pack)
    baseline = _evidence_items(list(baseline_items))
    retained_ids = {str(item.get("evidence_id") or "") for item in _evidence_items(result)}
    omitted_ids = [
        str(item.get("evidence_id") or "")
        for item in baseline
        if str(item.get("evidence_id") or "") not in retained_ids
    ]
    return _annotate_candidate(
        result,
        baseline_items=baseline,
        omitted_count=len(omitted_ids),
        omitted_ids=omitted_ids,
    )


def reduce_optional_evidence(
    pack: dict[str, Any],
    *,
    target_ceiling_chars: int,
    baseline_items: Iterable[dict[str, Any]] | None = None,
    mandatory_fields_limit_chars: int = MANDATORY_FIELDS_LIMIT,
) -> dict[str, Any]:
    target = max(1_000, int(target_ceiling_chars))
    original = copy.deepcopy(pack)
    stabilize_character_fields(original)
    if original["compact_pack_chars"] <= target:
        return original

    validated = validate_evidence_pack(original)
    items = _evidence_items(validated)
    baseline = _evidence_items(list(baseline_items)) if baseline_items is not None else copy.deepcopy(items)
    mandatory_ids = _mandatory_dependency_closure(baseline)
    mandatory_items = [item for item in baseline if str(item.get("evidence_id") or "") in mandatory_ids]
    mandatory_chars = json_chars({"evidence_schema_version": 2, "evidence_items": mandatory_items})
    mandatory_limit = min(max(1_000, int(mandatory_fields_limit_chars)), target)
    if mandatory_chars > mandatory_limit:
        raise CompactPolicyError(
            "COMPACT_MANDATORY_FIELDS_OVER_LIMIT",
            "mandatory evidence fields exceed the compact input allowance",
            metrics={
                "mandatory_chars": mandatory_chars,
                "mandatory_limit_chars": mandatory_limit,
                "target_ceiling_chars": target,
            },
        )

    protected_ids = _protected_evidence_ids(items)
    indexed = list(enumerate(items))
    optional = sorted(
        [(index, item) for index, item in indexed if str(item.get("evidence_id") or "") not in protected_ids],
        key=lambda pair: _optional_priority(pair[1], pair[0]),
    )

    def candidate_for(optional_count: int) -> dict[str, Any]:
        selected_ids = {
            str(item.get("evidence_id") or "")
            for _, item in optional[:optional_count]
        } | protected_ids
        retained = [item for item in items if str(item.get("evidence_id") or "") in selected_ids]
        candidate = copy.deepcopy(original)
        candidate["evidence_items"] = retained
        retained_ids = {str(item.get("evidence_id") or "") for item in retained}
        omitted_ids = [
            str(item.get("evidence_id") or "")
            for item in baseline
            if str(item.get("evidence_id") or "") not in retained_ids
        ]
        candidate = _annotate_candidate(
            candidate,
            baseline_items=baseline,
            omitted_count=len(omitted_ids),
            omitted_ids=omitted_ids,
        )
        validate_evidence_pack(candidate)
        return candidate

    minimum = candidate_for(0)
    if minimum["compact_pack_chars"] > target:
        raise CompactPolicyError(
            "COMPACT_LIMIT_EXCEEDED",
            "protected evidence and compact structure exceed the target ceiling",
            metrics={
                "protected_chars": minimum["compact_pack_chars"],
                "target_ceiling_chars": target,
            },
        )

    low = 0
    high = len(optional)
    best = minimum
    while low <= high:
        midpoint = (low + high) // 2
        candidate = candidate_for(midpoint)
        if candidate["compact_pack_chars"] <= target:
            best = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best
