from __future__ import annotations

import copy
import re
from typing import Any

from app.evidence_index import CLAIM_INDEX_SCHEMA_VERSION, normalize_claim_text
from app.evidence_schema import (
    EvidenceValidationError,
    canonical_sha256,
    read_evidence_pack,
    validate_source_ref,
)
from app.formal_body import FormalBodyDocument
from app.report_rules.schema import load_vbp_topic_rules


VBP_QUALITY_GATE_VERSION = "20260715-vbp-quality-v1"
_FAILURE_PRIORITY = (
    "VBP_RULE_CONFLICT",
    "VBP_EVIDENCE_INDEX_INVALID",
    "VBP_C_LEVEL_FACT_USED",
    "VBP_FACT_SOURCE_REF_INVALID",
    "VBP_UNSUPPORTED_CLAIM",
    "VBP_REQUIRED_SECTION_MISSING",
    "VBP_REQUIRED_TOPIC_MISSING",
)


def _material_is_vbp(material: dict[str, Any], rules: dict[str, Any]) -> bool:
    trigger = rules.get("trigger") if isinstance(rules.get("trigger"), dict) else {}
    menu_names = {normalize_claim_text(item) for item in list(trigger.get("menu_names") or [])}
    menu_name = normalize_claim_text(material.get("menu_name"))
    menu_code = normalize_claim_text(material.get("menu_code"))
    if menu_names and menu_name not in menu_names and menu_code != "project_notice":
        return False
    title = normalize_claim_text(material.get("title"))
    category = normalize_claim_text(
        " ".join(
            str(material.get(field) or "")
            for field in ("category", "projecttype", "project_type")
        )
    )
    title_keywords = [normalize_claim_text(item) for item in list(trigger.get("title_keywords") or [])]
    category_keywords = [normalize_claim_text(item) for item in list(trigger.get("category_keywords") or [])]
    return any(keyword and keyword in title for keyword in title_keywords) or any(
        keyword and keyword in category for keyword in category_keywords
    )


def _pack_is_vbp(pack: dict[str, Any], rules: dict[str, Any]) -> bool:
    return any(
        isinstance(material, dict) and _material_is_vbp(material, rules)
        for material in list(pack.get("primary_materials") or [])
    )


def _headings(document: FormalBodyDocument) -> list[str]:
    headings = [
        normalize_claim_text(match.group(1))
        for match in re.finditer(r"^\s*#{1,6}\s+(.+?)\s*$", document.markdown, flags=re.M)
    ]
    report_ir = document.report_ir
    if isinstance(report_ir, dict):
        for section in list(report_ir.get("sections") or []):
            if isinstance(section, dict) and str(section.get("heading") or "").strip():
                headings.append(normalize_claim_text(section["heading"]))
    return list(dict.fromkeys(item for item in headings if item))


def _add_issue(issues: list[dict[str, Any]], code: str, **details: Any) -> None:
    payload = {"code": code, **copy.deepcopy(details)}
    key = canonical_sha256(payload)
    if any(item.get("issue_key") == key for item in issues):
        return
    payload["issue_key"] = key
    issues.append(payload)


def _known_ab_evidence(pack: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    try:
        view = read_evidence_pack(pack)
    except EvidenceValidationError:
        return {}, []
    items = [
        item
        for item in list(view.get("evidence_items") or [])
        if isinstance(item, dict) and item.get("level") in {"A", "B"}
    ]
    return {str(item.get("evidence_id") or ""): item for item in items}, items


def _valid_claim_support(
    claim: dict[str, Any],
    known_ab: dict[str, dict[str, Any]],
    issues: list[dict[str, Any]],
) -> bool:
    claim_id = str(claim.get("claim_id") or "")
    if not bool(claim.get("supported")):
        _add_issue(issues, "VBP_UNSUPPORTED_CLAIM", claim_id=claim_id)
        return False
    levels = {str(item or "") for item in list(claim.get("support_levels") or [])}
    evidence_ids = [str(item or "") for item in list(claim.get("evidence_ids") or []) if str(item or "")]
    if not levels or not evidence_ids:
        _add_issue(issues, "VBP_FACT_SOURCE_REF_INVALID", claim_id=claim_id)
        return False
    if not levels <= {"A", "B"}:
        _add_issue(issues, "VBP_C_LEVEL_FACT_USED", claim_id=claim_id)
        return False
    matched = [known_ab.get(evidence_id) for evidence_id in evidence_ids]
    if any(item is None for item in matched):
        _add_issue(issues, "VBP_C_LEVEL_FACT_USED", claim_id=claim_id)
        return False
    expected_refs = {
        canonical_sha256(item["source_ref"])
        for item in matched
        if isinstance(item, dict) and isinstance(item.get("source_ref"), dict)
    }
    refs = list(claim.get("source_refs") or [])
    if not refs:
        _add_issue(issues, "VBP_FACT_SOURCE_REF_INVALID", claim_id=claim_id)
        return False
    try:
        actual_refs = {canonical_sha256(validate_source_ref(ref)) for ref in refs}
    except EvidenceValidationError:
        _add_issue(issues, "VBP_FACT_SOURCE_REF_INVALID", claim_id=claim_id)
        return False
    if not actual_refs or not actual_refs <= expected_refs:
        _add_issue(issues, "VBP_FACT_SOURCE_REF_INVALID", claim_id=claim_id)
        return False
    return True


def _topic_evidence_ids(
    pack: dict[str, Any],
    known_ab: dict[str, dict[str, Any]],
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for fact in list(pack.get("vbp_facts") or []):
        if not isinstance(fact, dict):
            continue
        fact_type = str(fact.get("fact_type") or "").strip()
        evidence_id = str(fact.get("evidence_id") or "").strip()
        if fact_type and evidence_id in known_ab:
            result.setdefault(fact_type, set()).add(evidence_id)
    for evidence_id, item in known_ab.items():
        if item.get("level") != "B" or not isinstance(item.get("value"), dict):
            continue
        fact_type = str(item["value"].get("fact_type") or "").strip()
        if fact_type:
            result.setdefault(fact_type, set()).add(evidence_id)
    return result


def evaluate_vbp_quality(
    document: FormalBodyDocument,
    pack: dict[str, Any],
    claim_index: dict[str, Any],
    *,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    loaded_rules = copy.deepcopy(rules if rules is not None else load_vbp_topic_rules())
    base = {
        "gate_version": VBP_QUALITY_GATE_VERSION,
        "rules_version": str(loaded_rules.get("version") or ""),
        "rules_hash": canonical_sha256(loaded_rules),
        "applicable": _pack_is_vbp(pack, loaded_rules),
    }
    if not base["applicable"]:
        return {
            **base,
            "status": "not_applicable",
            "passed": True,
            "primary_failure_code": "",
            "blocking_issue_codes": [],
            "blocking_issues": [],
            "missing_section_ids": [],
            "applicable_topic_ids": [],
            "missing_topic_ids": [],
            "claim_count": 0,
            "supported_claim_count": 0,
            "unsupported_claim_count": 0,
            "claim_ab_support_rate": 1.0,
            "c_independent_support_count": 0,
            "rejected_c_support_count": 0,
        }

    issues: list[dict[str, Any]] = []
    known_ab, _ = _known_ab_evidence(pack)
    if not known_ab:
        _add_issue(issues, "VBP_EVIDENCE_INDEX_INVALID")
    if not isinstance(claim_index, dict) or claim_index.get("schema_version") != CLAIM_INDEX_SCHEMA_VERSION:
        _add_issue(issues, "VBP_EVIDENCE_INDEX_INVALID")
        claims: list[dict[str, Any]] = []
    else:
        raw_claims = claim_index.get("claims")
        claims = [item for item in raw_claims if isinstance(item, dict)] if isinstance(raw_claims, list) else []
        if not isinstance(raw_claims, list):
            _add_issue(issues, "VBP_EVIDENCE_INDEX_INVALID")

    valid_claim_ids: set[str] = set()
    rejected_c_support_count = 0
    for claim in claims:
        before = len([item for item in issues if item.get("code") == "VBP_C_LEVEL_FACT_USED"])
        if _valid_claim_support(claim, known_ab, issues):
            valid_claim_ids.add(str(claim.get("claim_id") or ""))
        after = len([item for item in issues if item.get("code") == "VBP_C_LEVEL_FACT_USED"])
        rejected_c_support_count += max(0, after - before)

    headings = _headings(document)
    missing_sections: list[str] = []
    structure = loaded_rules.get("report_structure") if isinstance(loaded_rules.get("report_structure"), dict) else {}
    for section in list(structure.get("required_sections") or []):
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("section_id") or "").strip()
        labels = [normalize_claim_text(item) for item in list(section.get("labels") or [])]
        if section_id and not any(label and any(label in heading for heading in headings) for label in labels):
            missing_sections.append(section_id)
    if missing_sections:
        _add_issue(issues, "VBP_REQUIRED_SECTION_MISSING", section_ids=sorted(missing_sections))

    topic_ids = _topic_evidence_ids(pack, known_ab)
    applicable_topics: list[str] = []
    missing_topics: list[str] = []
    valid_claims = [claim for claim in claims if str(claim.get("claim_id") or "") in valid_claim_ids]
    for topic in list(loaded_rules.get("topics") or []):
        if not isinstance(topic, dict) or not bool(topic.get("required_when_evidence_present")):
            continue
        fact_type = str(topic.get("fact_type") or topic.get("topic_id") or "").strip()
        if not fact_type or not topic_ids.get(fact_type):
            continue
        applicable_topics.append(fact_type)
        labels = [normalize_claim_text(item) for item in list(topic.get("labels") or [])]
        covered = any(
            bool(set(str(item or "") for item in list(claim.get("evidence_ids") or [])) & topic_ids[fact_type])
            or any(label and label in str(claim.get("normalized_text") or "") for label in labels)
            for claim in valid_claims
        )
        if not covered:
            missing_topics.append(fact_type)
    if missing_topics:
        _add_issue(issues, "VBP_REQUIRED_TOPIC_MISSING", topic_ids=sorted(missing_topics))

    if any(
        isinstance(item, dict) and str(item.get("code") or "") == "VBP_FACT_CONFLICT"
        for item in list(pack.get("vbp_fact_diagnostics") or [])
    ):
        _add_issue(issues, "VBP_RULE_CONFLICT")

    codes = [code for code in _FAILURE_PRIORITY if any(item.get("code") == code for item in issues)]
    supported_count = len(valid_claim_ids)
    claim_count = len(claims)
    return {
        **base,
        "status": "passed" if not codes else "blocked",
        "passed": not codes,
        "primary_failure_code": codes[0] if codes else "",
        "blocking_issue_codes": codes,
        "blocking_issues": sorted(issues, key=lambda item: (_FAILURE_PRIORITY.index(item["code"]), item["issue_key"])),
        "missing_section_ids": sorted(missing_sections),
        "applicable_topic_ids": sorted(applicable_topics),
        "missing_topic_ids": sorted(missing_topics),
        "claim_count": claim_count,
        "supported_claim_count": supported_count,
        "unsupported_claim_count": sum(1 for claim in claims if not bool(claim.get("supported"))),
        "claim_ab_support_rate": (supported_count / claim_count) if claim_count else 1.0,
        "c_independent_support_count": 0,
        "rejected_c_support_count": rejected_c_support_count,
    }
