from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from app.evidence_schema import EvidenceValidationError, fact_support_values, read_evidence_pack


STEP_DEFINITIONS = [
    ("create_run", "创建任务"),
    ("fetch_pack", "获取证据包"),
    ("generate_report", "生成报告"),
    ("quality_check", "质检报告"),
    ("revision", "修订报告"),
    ("second_quality_check", "二次质检"),
    ("final_output", "完成输出"),
]


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _safe_len(value: Any) -> int:
    return len(_text(value).strip())


def _wps_like_word_count(value: Any) -> int:
    """Approximate WPS/Word visible word count for rendered report text.

    Markdown source length over-counts headings, emphasis markers, HTML spans,
    punctuation, and whitespace. WPS-style counting is closer to counting CJK
    characters plus Latin/number words in the rendered document.
    """
    text = _text(value)
    if not text.strip():
        return 0
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[#>\-\*\+\s]+", "", text, flags=re.M)
    text = text.replace("|", " ")
    text = re.sub(r"[*_~]+", "", text)
    cjk_count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text))
    non_cjk = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", " ", text)
    word_count = len(re.findall(r"[A-Za-z0-9]+(?:[./:_-][A-Za-z0-9]+)*", non_cjk))
    return cjk_count + word_count


def _any_text_len(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value.strip())
    if isinstance(value, (int, float, bool)):
        return len(str(value))
    if isinstance(value, dict):
        return sum(_any_text_len(item) for item in value.values())
    if isinstance(value, list):
        return sum(_any_text_len(item) for item in value)
    return len(str(value).strip())


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _smooth_running_percent(created_at: Any) -> int:
    created = _parse_timestamp(created_at)
    if created is None:
        return 35
    elapsed = max(0.0, (datetime.now() - created).total_seconds())
    if elapsed <= 5:
        return int(35 + (elapsed / 5) * 10)
    if elapsed <= 20:
        return int(45 + ((elapsed - 5) / 15) * 20)
    if elapsed <= 60:
        return int(65 + ((elapsed - 20) / 40) * 17)
    return min(90, int(82 + min((elapsed - 60) / 120, 1) * 8))


def _material_content_length(material: dict[str, Any]) -> int:
    explicit = material.get("content_text_length")
    if isinstance(explicit, int) and explicit >= 0:
        return explicit
    return _safe_len(material.get("content_text"))


def _summary_length(material: dict[str, Any]) -> int:
    return _safe_len(material.get("summary")) or _safe_len(material.get("content_summary"))


UNUSABLE_ATTACHMENT_STATUSES = {
    "metadata_only",
    "auth_required",
    "auth_failed",
    "download_failed",
    "network_unreachable",
    "unsupported",
    "parse_failed",
}
USABLE_ATTACHMENT_STATUSES = {
    "stream_parsed",
    "temp_file_parsed",
    "parsed_text",
    "parsed_summary",
    "parsed_table_summary",
    "too_large_summary_only",
}


def _attachment_status_counts(materials: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "attachment_count": 0,
        "core_attachment_count": 0,
        "core_attachment_parsed_count": 0,
        "core_attachment_unparsed_count": 0,
        "parsed_attachment_count": 0,
        "metadata_only_attachment_count": 0,
        "network_unreachable_attachment_count": 0,
        "auth_failed_attachment_count": 0,
        "download_failed_attachment_count": 0,
        "parse_failed_attachment_count": 0,
        "parsed_summary_attachment_count": 0,
        "parsed_table_summary_attachment_count": 0,
        "cache_hit_success_count": 0,
        "cache_hit_failure_short_count": 0,
        "cache_expired_retry_count": 0,
        "cache_miss_count": 0,
        "force_refreshed_count": 0,
    }
    for material in materials:
        for attachment in material.get("attachments") or []:
            if not isinstance(attachment, dict):
                continue
            counts["attachment_count"] += 1
            parse_status = str(attachment.get("parse_status") or "").strip()
            parse_statuses = attachment.get("parse_statuses") if isinstance(attachment.get("parse_statuses"), list) else []
            cache_status = str(attachment.get("cache_status") or "").strip()
            if cache_status == "cache_hit_success":
                counts["cache_hit_success_count"] += 1
            elif cache_status == "cache_hit_failure_short":
                counts["cache_hit_failure_short_count"] += 1
            elif cache_status == "cache_expired_retry":
                counts["cache_expired_retry_count"] += 1
            elif cache_status == "cache_miss":
                counts["cache_miss_count"] += 1
            elif cache_status == "force_refreshed":
                counts["force_refreshed_count"] += 1
            is_core = bool(attachment.get("core_attachment"))
            is_parsed = parse_status in USABLE_ATTACHMENT_STATUSES or any(item in USABLE_ATTACHMENT_STATUSES for item in parse_statuses)
            if is_core:
                counts["core_attachment_count"] += 1
                if is_parsed:
                    counts["core_attachment_parsed_count"] += 1
                else:
                    counts["core_attachment_unparsed_count"] += 1
            if parse_status == "metadata_only":
                counts["metadata_only_attachment_count"] += 1
            elif parse_status in {"auth_required", "auth_failed"}:
                counts["auth_failed_attachment_count"] += 1
            elif parse_status == "network_unreachable":
                counts["network_unreachable_attachment_count"] += 1
                counts["download_failed_attachment_count"] += 1
            elif parse_status == "download_failed":
                counts["download_failed_attachment_count"] += 1
            elif parse_status in {"unsupported", "parse_failed"}:
                counts["parse_failed_attachment_count"] += 1
            elif is_parsed:
                counts["parsed_attachment_count"] += 1
                if parse_status == "parsed_summary" or "parsed_summary" in parse_statuses:
                    counts["parsed_summary_attachment_count"] += 1
                if parse_status == "parsed_table_summary" or "parsed_table_summary" in parse_statuses:
                    counts["parsed_table_summary_attachment_count"] += 1
            elif parse_status:
                counts["parse_failed_attachment_count"] += 1
    return counts


def _attachment_is_usable(attachment: dict[str, Any]) -> bool:
    parse_status = str(attachment.get("parse_status") or "").strip()
    parse_statuses = attachment.get("parse_statuses") if isinstance(attachment.get("parse_statuses"), list) else []
    if parse_status in UNUSABLE_ATTACHMENT_STATUSES:
        return False
    return parse_status in USABLE_ATTACHMENT_STATUSES or any(item in USABLE_ATTACHMENT_STATUSES for item in parse_statuses)


def _evidence_level(weighted_evidence_chars: int) -> tuple[str, str]:
    if weighted_evidence_chars < 1000:
        return "low", "600-1000字"
    if weighted_evidence_chars < 5000:
        return "medium", "1000-1800字"
    if weighted_evidence_chars < 12000:
        return "high", "1800-3000字"
    return "very_high", "3000字以上，但避免堆砌原文"


def _diagnosis(code: str, level: str, message: str) -> dict[str, str]:
    return {"code": code, "level": level, "message": message}


def _material_summary_chars(material: dict[str, Any]) -> int:
    return _safe_len(material.get("content_summary")) or _safe_len(material.get("summary"))


def _primary_key_fact_chars(material: dict[str, Any]) -> int:
    fields = [
        "key_facts",
        "important_passages",
        "policy_rules",
        "price_rules",
        "time_requirements",
        "product_scope",
        "enterprise_requirements",
        "execution_requirements",
    ]
    return sum(_any_text_len(material.get(field)) for field in fields)


def _auxiliary_relevant_chars(material: dict[str, Any]) -> int:
    return (
        _any_text_len(material.get("relevant_snippets"))
        + _any_text_len(material.get("usable_points"))
        + _any_text_len(material.get("comparison_points"))
    )


def _attachment_text_chars(materials: list[dict[str, Any]]) -> tuple[int, int]:
    summary_chars = 0
    table_chars = 0
    for material in materials:
        for attachment in material.get("attachments") or []:
            if not isinstance(attachment, dict) or not _attachment_is_usable(attachment):
                continue
            summary_chars += _safe_len(attachment.get("summary"))
            summary_chars += _any_text_len(attachment.get("key_facts"))
            summary_chars += _any_text_len(attachment.get("important_sections"))
            for table in attachment.get("table_summaries") or []:
                if not isinstance(table, dict):
                    continue
                table_chars += _any_text_len(
                    {
                        "summary": table.get("summary"),
                        "business_value": table.get("business_value"),
                        "headers": table.get("headers"),
                        "key_columns": table.get("key_columns"),
                    }
                )
    return summary_chars, table_chars


def _json_chars(value: Any) -> int:
    try:
        return len(json.dumps(value or {}, ensure_ascii=False))
    except TypeError:
        return 0


def _material_brief(material: dict[str, Any], role: str) -> dict[str, Any]:
    attachments = [item for item in material.get("attachments") or [] if isinstance(item, dict)]
    attachment_summary_chars, attachment_table_summary_chars = _attachment_text_chars([material])
    brief = {
        "menu_code": material.get("menu_code") or "",
        "articleid": material.get("articleid") or "",
        "title": material.get("title") or "",
        "content_text_length": _material_content_length(material),
        "summary_length": _summary_length(material),
        "attachment_count": len(attachments),
        "attachment_summary_chars": attachment_summary_chars,
        "attachment_table_summary_chars": attachment_table_summary_chars,
        "auth_required_count": sum(1 for item in attachments if item.get("parse_status") in {"auth_required", "auth_failed"}),
        "parse_failed_count": sum(1 for item in attachments if item.get("parse_status") in {"download_failed", "unsupported", "parse_failed"}),
    }
    if role == "primary":
        brief["key_fact_count"] = len(material.get("key_facts") or [])
        brief["primary_attachment_evidence_chars"] = attachment_summary_chars + attachment_table_summary_chars
        brief["attachment_led"] = _is_attachment_led_primary(material)
    else:
        brief["usable_point_count"] = len(material.get("usable_points") or [])
    return brief


def _is_attachment_led_primary(material: dict[str, Any]) -> bool:
    if _material_content_length(material) >= 800:
        return False
    has_usable_core_attachment = any(
        isinstance(attachment, dict)
        and bool(attachment.get("core_attachment"))
        and _attachment_is_usable(attachment)
        for attachment in material.get("attachments") or []
    )
    if not has_usable_core_attachment:
        return False
    summary_chars, table_chars = _attachment_text_chars([material])
    return summary_chars + table_chars >= 800


def build_pack_diagnostics(pack: dict[str, Any], dify_pack: dict[str, Any] | None = None) -> dict[str, Any]:
    primary = [item for item in (pack.get("primary_materials") or []) if isinstance(item, dict)]
    auxiliary = [item for item in (pack.get("auxiliary_materials") or []) if isinstance(item, dict)]
    materials = [*primary, *auxiliary]

    primary_content_chars = sum(_material_content_length(item) for item in primary)
    primary_summary_chars = sum(_material_summary_chars(item) for item in primary)
    primary_key_fact_chars = sum(_primary_key_fact_chars(item) for item in primary)
    primary_attachment_summary_chars, primary_attachment_table_summary_chars = _attachment_text_chars(primary)

    auxiliary_content_chars = sum(_material_content_length(item) for item in auxiliary)
    auxiliary_summary_chars = sum(_material_summary_chars(item) for item in auxiliary)
    auxiliary_relevant_snippet_chars = sum(_auxiliary_relevant_chars(item) for item in auxiliary)
    auxiliary_attachment_summary_chars, auxiliary_attachment_table_summary_chars = _attachment_text_chars(auxiliary)

    raw_total_content_chars = (
        primary_content_chars
        + primary_summary_chars
        + primary_key_fact_chars
        + primary_attachment_summary_chars
        + primary_attachment_table_summary_chars
        + auxiliary_content_chars
        + auxiliary_summary_chars
        + auxiliary_relevant_snippet_chars
        + auxiliary_attachment_summary_chars
        + auxiliary_attachment_table_summary_chars
    )
    weighted_evidence_chars = int(
        primary_content_chars * 1.0
        + primary_key_fact_chars * 1.0
        + primary_attachment_summary_chars * 0.7
        + primary_attachment_table_summary_chars * 0.6
        + auxiliary_relevant_snippet_chars * 0.5
        + auxiliary_summary_chars * 0.3
        + auxiliary_content_chars * 0.2
        + auxiliary_attachment_summary_chars * 0.3
        + auxiliary_attachment_table_summary_chars * 0.3
    )
    dify_compact_pack_chars = _json_chars(dify_pack) if dify_pack is not None else _json_chars(pack if pack.get("dify_compacted") else {})
    full_pack_chars = _json_chars(pack)
    attachment_counts = _attachment_status_counts(materials)
    core_unparsed_names = [
        str(attachment.get("filename") or attachment.get("articleattid") or "")
        for material in materials
        for attachment in material.get("attachments") or []
        if isinstance(attachment, dict)
        and attachment.get("core_attachment")
        and not _attachment_is_usable(attachment)
    ]
    warnings = list(pack.get("warnings") or [])
    evidence_level, suggested_length = _evidence_level(weighted_evidence_chars)
    parsed_count = attachment_counts["parsed_attachment_count"]
    attachment_count = attachment_counts["attachment_count"]
    unparsed_count = attachment_count - parsed_count
    attachment_led_primary_count = sum(1 for item in primary if _is_attachment_led_primary(item))
    short_primary_without_attachment_evidence = [
        item for item in primary if _material_content_length(item) < 800 and not _is_attachment_led_primary(item)
    ]

    diagnosis: list[dict[str, str]] = []
    if attachment_led_primary_count:
        diagnosis.append(
            _diagnosis(
                "ATTACHMENT_LED_PRIMARY_MATERIAL",
                "normal",
                "存在主材料正文较短但核心附件已解析的材料，报告应以附件摘要和表格结构作为主体依据。",
            )
        )
    if short_primary_without_attachment_evidence:
        diagnosis.append(
            _diagnosis(
                "EVIDENCE_PRIMARY_TOO_SHORT",
                "warning",
                "主材料正文较短，即使辅助材料较多，也不宜过度扩写报告。",
            )
        )
    if core_unparsed_names:
        diagnosis.append(
            _diagnosis(
                "CORE_ATTACHMENT_NOT_PARSED",
                "warning",
                "存在核心附件未解析，产品、企业、价格或中选结果分析可能受限。",
            )
        )
    if attachment_count > 0 and parsed_count == 0:
        diagnosis.append(
            _diagnosis(
                "ATTACHMENTS_NOT_PARSED",
                "warning",
                "当前附件均未形成可用摘要，报告不能引用附件正文或表格内容。",
            )
        )
    elif parsed_count > 0:
        diagnosis.append(
            _diagnosis(
                "CORE_ATTACHMENT_SUMMARY_ONLY" if attachment_counts["core_attachment_parsed_count"] else "ATTACHMENTS_PARSED_SUMMARY_ONLY",
                "normal",
                "附件已解析为摘要或表格结构，传给 Dify 的是结构化摘要而非附件全文。",
            )
        )
    if attachment_counts["cache_hit_success_count"]:
        diagnosis.append(_diagnosis("ATTACHMENT_PARSE_CACHE_HIT", "normal", "存在附件解析结果命中成功缓存，附件依据更稳定。"))
    if attachment_counts["cache_expired_retry_count"]:
        diagnosis.append(_diagnosis("ATTACHMENT_PARSE_CACHE_EXPIRED", "normal", "存在附件缓存过期并重新解析。"))
    if attachment_counts["cache_hit_failure_short_count"]:
        diagnosis.append(_diagnosis("ATTACHMENT_PARSE_FAILED_SHORT_CACHED", "warning", "存在短期失败缓存，到期后会重新尝试解析。"))
    if auxiliary_content_chars > max(primary_content_chars * 2, 2000) and auxiliary_relevant_snippet_chars < auxiliary_content_chars * 0.25:
        diagnosis.append(
            _diagnosis(
                "AUXILIARY_TOO_DOMINANT",
                "warning",
                "辅助材料字数明显超过主材料，应避免报告反客为主。",
            )
        )
    if dify_compact_pack_chars > 75000:
        diagnosis.append(
            _diagnosis(
                "DIFY_PACK_NEAR_LIMIT",
                "warning",
                "传给 Dify 的精简证据包接近 80000 字符限制，后续可能需要进一步压缩。",
            )
        )
    if dify_compact_pack_chars and full_pack_chars > dify_compact_pack_chars * 1.2:
        diagnosis.append(
            _diagnosis(
                "EVIDENCE_COMPACTED_FOR_DIFY",
                "warning",
                "完整 evidence_pack 已压缩为 Dify 精简版，报告生成基于保留优先级后的结构化证据。",
            )
        )
    if dify_pack and dify_pack.get("compression_applied") and core_unparsed_names:
        diagnosis.append(_diagnosis("CORE_ATTACHMENT_OMITTED_RISK", "warning", "存在核心附件不可用或受压缩影响，相关明细分析需保守。"))
    if warnings:
        diagnosis.append(
            _diagnosis(
                "PACK_WARNINGS_EXIST",
                "warning",
                "evidence_pack 存在 warning，生成报告时应遵守材料限制。",
            )
        )
    if not diagnosis:
        diagnosis.append(_diagnosis("OK", "normal", "附件解析和证据包压缩状态正常。"))

    omitted_content = list(dify_pack.get("omitted_content") or []) if dify_pack else []
    omitted_content_summary = [
        {
            "type": item.get("type", ""),
            "reason": item.get("reason", ""),
            "risk": item.get("risk", ""),
            "manual_review": bool(item.get("manual_review")),
            "affects_primary_detail": bool(item.get("affects_primary_detail")),
            "affects_core_attachment_detail": bool(item.get("affects_core_attachment_detail")),
            "affects_auxiliary_detail": bool(item.get("affects_auxiliary_detail")),
        }
        for item in omitted_content[:12]
        if isinstance(item, dict)
    ]
    needs_manual_review_reasons: list[str] = []
    if core_unparsed_names:
        needs_manual_review_reasons.append("存在核心附件未解析，附件明细分析受限。")
    for item in omitted_content:
        if isinstance(item, dict) and item.get("manual_review"):
            reason = str(item.get("reason") or item.get("type") or "存在需人工复核的省略内容")
            if reason not in needs_manual_review_reasons:
                needs_manual_review_reasons.append(reason)
    if dify_pack and dify_pack.get("input_strategy") in {"safe_compact", "table_heavy", "staged_generation"} and dify_pack.get("compression_applied"):
        needs_manual_review_reasons.append("Dify 输入发生压缩，需关注被省略内容是否影响交付质量。")

    original_pack_chars = int(dify_pack.get("original_pack_chars") or full_pack_chars) if dify_pack else full_pack_chars
    compact_pack_chars = int(dify_pack.get("compact_pack_chars") or dify_compact_pack_chars) if dify_pack else dify_compact_pack_chars
    final_dify_input_chars = int(dify_pack.get("final_dify_input_chars") or compact_pack_chars) if dify_pack else compact_pack_chars
    strategy_basis_chars = int(dify_pack.get("strategy_basis_chars") or dify_pack.get("dify_relevant_input_chars") or 0) if dify_pack else 0
    compression_ratio = None
    if final_dify_input_chars:
        compression_ratio = round(original_pack_chars / final_dify_input_chars, 4)

    return {
        "primary_count": len(primary),
        "auxiliary_count": len(auxiliary),
        "primary_material_count": len(primary),
        "auxiliary_material_count": len(auxiliary),
        "primary_content_chars": primary_content_chars,
        "primary_summary_chars": primary_summary_chars,
        "primary_key_fact_chars": primary_key_fact_chars,
        "primary_attachment_summary_chars": primary_attachment_summary_chars,
        "primary_attachment_table_summary_chars": primary_attachment_table_summary_chars,
        "auxiliary_content_chars": auxiliary_content_chars,
        "auxiliary_summary_chars": auxiliary_summary_chars,
        "auxiliary_relevant_snippet_chars": auxiliary_relevant_snippet_chars,
        "auxiliary_attachment_summary_chars": auxiliary_attachment_summary_chars,
        "auxiliary_attachment_table_summary_chars": auxiliary_attachment_table_summary_chars,
        "raw_total_content_chars": raw_total_content_chars,
        "dify_compact_pack_chars": dify_compact_pack_chars,
        "dify_input_chars": dify_compact_pack_chars,
        "full_pack_chars": full_pack_chars,
        "weighted_evidence_chars": weighted_evidence_chars,
        "total_content_chars": raw_total_content_chars,
        "primary_materials": [_material_brief(item, "primary") for item in primary],
        "auxiliary_materials": [_material_brief(item, "auxiliary") for item in auxiliary],
        "attachment_led_primary_count": attachment_led_primary_count,
        **attachment_counts,
        "core_attachment_unparsed_names": core_unparsed_names,
        "attachment_analysis_impact": bool(core_unparsed_names),
        "compression_applied": bool(dify_pack.get("compression_applied")) if dify_pack else False,
        "detail_preserved": bool(dify_pack.get("detail_preserved", True)) if dify_pack else True,
        "primary_detail_preserved": bool(dify_pack.get("primary_detail_preserved", True)) if dify_pack else True,
        "core_attachment_detail_preserved": bool(dify_pack.get("core_attachment_detail_preserved", True)) if dify_pack else True,
        "auxiliary_detail_preserved": bool(dify_pack.get("auxiliary_detail_preserved", True)) if dify_pack else True,
        "full_row_level_detail_preserved": bool(dify_pack.get("full_row_level_detail_preserved", True)) if dify_pack else True,
        "compression_reason": list(dify_pack.get("compression_reason") or []) if dify_pack else [],
        "input_strategy": str(dify_pack.get("input_strategy") or "") if dify_pack else "",
        "input_strategy_type": str(dify_pack.get("input_strategy_type") or "") if dify_pack else "",
        "input_strategy_description": str(dify_pack.get("input_strategy_description") or "") if dify_pack else "",
        "strategy_basis": str(dify_pack.get("strategy_basis") or "") if dify_pack else "",
        "hard_limit_chars": int(dify_pack.get("hard_limit_chars") or 0) if dify_pack else 0,
        "full_input_max_chars": int(dify_pack.get("full_input_max_chars") or 0) if dify_pack else 0,
        "safe_compact_max_chars": int(dify_pack.get("safe_compact_max_chars") or 0) if dify_pack else 0,
        "strategy_thresholds": dict(dify_pack.get("strategy_thresholds") or {}) if dify_pack else {},
        "evidence_budget": dict(dify_pack.get("evidence_budget") or {}) if dify_pack else {},
        "generation_mode": str(((dify_pack.get("generation_guidance") or {}) if dify_pack else {}).get("generation_mode") or ""),
        "target_report_length": str(((dify_pack.get("generation_guidance") or {}) if dify_pack else {}).get("target_report_length") or ""),
        "original_pack_chars": original_pack_chars,
        "strategy_basis_chars": strategy_basis_chars,
        "dify_relevant_input_chars": int(dify_pack.get("dify_relevant_input_chars") or strategy_basis_chars) if dify_pack else 0,
        "final_dify_input_chars": final_dify_input_chars,
        "compact_pack_chars": compact_pack_chars,
        "compression_ratio": dify_pack.get("compression_ratio", compression_ratio) if dify_pack else compression_ratio,
        "compression_strategy": list(dify_pack.get("compression_strategy") or []) if dify_pack else [],
        "omitted_content": omitted_content,
        "omitted_content_count": len(omitted_content),
        "omitted_content_summary": omitted_content_summary,
        "unavailable_core_attachment_count": attachment_counts["core_attachment_unparsed_count"],
        "attachment_led": bool(dify_pack.get("attachment_led")) if dify_pack else bool(attachment_led_primary_count),
        "table_heavy": bool(dify_pack.get("table_heavy")) if dify_pack else False,
        "primary_evidence_score": int(dify_pack.get("primary_evidence_score") or 0) if dify_pack else 0,
        "attachment_evidence_score": int(dify_pack.get("attachment_evidence_score") or 0) if dify_pack else 0,
        "auxiliary_evidence_score": int(dify_pack.get("auxiliary_evidence_score") or 0) if dify_pack else 0,
        "table_evidence_score": int(dify_pack.get("table_evidence_score") or 0) if dify_pack else 0,
        "needs_manual_review": bool(needs_manual_review_reasons),
        "needs_manual_review_reasons": needs_manual_review_reasons,
        "unparsed_attachment_count": unparsed_count,
        "key_fact_count": sum(len(item.get("key_facts") or []) for item in primary) + len(pack.get("combined_key_facts") or []),
        "warnings_count": len(warnings),
        "summary_chars": primary_summary_chars + auxiliary_summary_chars,
        "estimated_evidence_level": evidence_level,
        "suggested_report_length": suggested_length,
        "diagnosis_basis": [
            "主材料正文是报告主体依据。",
            "主材料附件摘要是重要补充依据。",
            "辅助材料正文不能与主材料正文等权。",
            "辅助材料主要用于历史对比、背景说明和同类项目参照。",
            "未解析附件不能作为报告正文依据。",
            "Dify 使用精简证据包，完整证据包保留在后端。",
            "input_strategy 表示策略选择依据，不等同于是否实际压缩。",
            "compression_applied 表示是否发生实际内容压缩、省略、移除或截断。",
            "strategy_basis_chars 是策略判断用字符数，通常等于 dify_relevant_input_chars。",
            "final_dify_input_chars 是最终传给 Dify 的 compact JSON 字符数。",
            "attachment_led 是附件主导型生成策略，不代表一定压缩。",
        ],
        "diagnosis": diagnosis,
    }

def build_run_progress(record: dict[str, Any]) -> dict[str, Any]:
    status = str(record.get("status") or "running")
    created_at = record.get("created_at") or None
    updated_at = record.get("updated_at") or None
    version = int(record.get("version") or 1)
    quality_check = record.get("quality_check") if isinstance(record.get("quality_check"), dict) else {}

    steps = [{"key": key, "name": name, "status": "pending", "timestamp": None} for key, name in STEP_DEFINITIONS]

    def set_step(key: str, step_status: str, timestamp: Any = None) -> None:
        for step in steps:
            if step["key"] == key:
                step["status"] = step_status
                step["timestamp"] = timestamp
                return

    if status in {"finished", "needs_manual_review"}:
        set_step("create_run", "finished", created_at)
        set_step("fetch_pack", "finished", created_at)
        set_step("generate_report", "finished", None)
        set_step("quality_check", "finished" if quality_check.get("passed") is not None else "skipped", None)
        if version > 1:
            set_step("revision", "finished", None)
            set_step("second_quality_check", "finished" if quality_check.get("passed") is not None else "skipped", None)
        else:
            set_step("revision", "skipped", None)
            set_step("second_quality_check", "skipped", None)
        set_step("final_output", "finished", updated_at)
        return {"percent": 100, "current_step": "完成输出", "steps": steps}

    if status == "failed":
        set_step("create_run", "finished", created_at)
        set_step("fetch_pack", "finished", created_at)
        set_step("generate_report", "failed", updated_at)
        return {"percent": 100, "current_step": "生成失败", "steps": steps}

    set_step("create_run", "finished", created_at)
    set_step("fetch_pack", "finished", created_at)
    set_step("generate_report", "running", None)
    return {"percent": _smooth_running_percent(created_at), "current_step": "Dify 工作流执行中", "steps": steps}


def _report_may_be_truncated(markdown: str) -> bool:
    text = markdown.strip()
    if not text:
        return False
    if text.count("```") % 2 == 1:
        return True
    if text.count("{") > text.count("}") or text.count("[") > text.count("]"):
        return True
    end = text[-1]
    return end not in "。！？.!?)）】》\"'"


def _version_items(record: dict[str, Any], final_chars: int) -> list[dict[str, Any]]:
    versions = record.get("versions")
    if isinstance(versions, list) and versions:
        result = []
        for item in versions:
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "version": item.get("version") or len(result) + 1,
                    "type": item.get("type") or "generation",
                    "chars": int(item.get("chars") or _safe_len(item.get("report_markdown"))),
                }
            )
        if result:
            return result
    version = int(record.get("version") or 1)
    return [{"version": version, "type": "final_output" if version > 1 else "initial_generation", "chars": final_chars}]


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def _normalize_for_match(value: Any) -> str:
    text = _flatten_text(value)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", "", text)
    text = text.replace("-", "").replace("/", "").replace(".", "")
    return text.lower()


def _coverage_terms(value: Any) -> list[str]:
    text = _flatten_text(value)
    terms: list[str] = []
    terms.extend(re.findall(r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d+(?:\.\d+)?%?", text))
    for chunk in re.split(r"[，。；;：:\s、（）()【】\[\]<>《》]|包括|涉及|包含|为|按|和|及|以及|或|等", text):
        chunk = chunk.strip()
        if 2 <= len(chunk) <= 18:
            terms.append(chunk)
    for keyword in [
        "价格",
        "中选价",
        "申报价",
        "产品",
        "企业",
        "挂网",
        "采购",
        "执行",
        "配送",
        "医疗机构",
        "暂停",
        "撤销",
        "信用",
        "注册证",
        "医保编码",
    ]:
        if keyword in text:
            terms.append(keyword)
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        normalized = _normalize_for_match(term)
        if len(normalized) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        result.append(term)
    return result[:12]


def _is_covered(report_markdown: str, evidence: Any) -> bool:
    report = _normalize_for_match(report_markdown)
    evidence_text = _normalize_for_match(evidence)
    if not evidence_text:
        return True
    if len(evidence_text) <= 24 and evidence_text in report:
        return True
    terms = _coverage_terms(evidence)
    if not terms:
        return False
    matched = sum(1 for term in terms if _normalize_for_match(term) in report)
    required = 1 if len(terms) <= 2 else 2
    return matched >= required


def _sentence_with_keywords(text: str, keywords: list[str]) -> str:
    for sentence in re.split(r"[。！？!?]\s*", text or ""):
        if any(keyword in sentence for keyword in keywords):
            return sentence.strip()
    return ""


def _coverage_preview(value: Any, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", _flatten_text(value)).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _coverage_entry(material: dict[str, Any], label: str, evidence: Any, category: str, report_markdown: str) -> dict[str, Any]:
    covered = _is_covered(report_markdown, evidence)
    return {
        "label": label,
        "category": category,
        "covered": covered,
        "menu_code": material.get("menu_code") or "",
        "articleid": material.get("articleid") or "",
        "title": material.get("title") or "",
        "evidence_preview": _coverage_preview(evidence),
    }


def _material_coverage_entries(material: dict[str, Any], report_markdown: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for label, key in [
        ("发布时间", "audittime"),
        ("地区", "areaname"),
        ("发布机构", "publicorg"),
    ]:
        if material.get(key):
            entries.append(_coverage_entry(material, label, material.get(key), "metadata", report_markdown))

    for label, key in [
        ("价格规则", "price_rules"),
        ("时间节点", "time_requirements"),
        ("产品范围", "product_scope"),
        ("企业要求", "enterprise_requirements"),
        ("执行要求", "execution_requirements"),
        ("主材料核心规则", "policy_rules"),
    ]:
        if material.get(key):
            entries.append(_coverage_entry(material, label, material.get(key), "structured_rule", report_markdown))

    risk_sentence = _sentence_with_keywords(
        _text(material.get("content_text")),
        ["暂停", "撤销", "取消", "信用", "逾期", "不予", "不得", "处罚", "处理"],
    )
    if risk_sentence:
        entries.append(_coverage_entry(material, "暂停/撤销/信用风险", risk_sentence, "risk", report_markdown))

    for attachment in material.get("attachments") or []:
        if not isinstance(attachment, dict) or not _attachment_is_usable(attachment):
            continue
        if attachment.get("summary") or attachment.get("key_facts"):
            entries.append(
                _coverage_entry(
                    material,
                    "附件摘要",
                    {
                        "filename": attachment.get("filename"),
                        "summary": attachment.get("summary"),
                        "key_facts": attachment.get("key_facts"),
                    },
                    "attachment",
                    report_markdown,
                )
            )
        for table in attachment.get("table_summaries") or []:
            if not isinstance(table, dict):
                continue
            entries.append(
                _coverage_entry(
                    material,
                    "附件表格摘要",
                    {
                        "filename": attachment.get("filename"),
                        "sheet_name": table.get("sheet_name"),
                        "headers": table.get("headers"),
                        "key_columns": table.get("key_columns"),
                        "summary": table.get("summary"),
                        "business_value": table.get("business_value"),
                    },
                    "attachment_table",
                    report_markdown,
                )
            )
    return entries


def _build_report_coverage(record: dict[str, Any], pack: dict[str, Any], pack_diag: dict[str, Any], report_chars: int) -> dict[str, Any]:
    report_markdown = _text(record.get("report_markdown"))
    primary = [item for item in (pack.get("primary_materials") or []) if isinstance(item, dict)]
    all_entries: list[dict[str, Any]] = []
    material_coverage: list[dict[str, Any]] = []
    for material in primary:
        entries = _material_coverage_entries(material, report_markdown)
        all_entries.extend(entries)
        total = len(entries)
        covered = sum(1 for item in entries if item["covered"])
        material_coverage.append(
            {
                "menu_code": material.get("menu_code") or "",
                "articleid": material.get("articleid") or "",
                "title": material.get("title") or "",
                "coverage_score": int(round((covered / total) * 100)) if total else 100,
                "covered_count": covered,
                "missing_count": total - covered,
                "missing_labels": [item["label"] for item in entries if not item["covered"]],
            }
        )

    covered_items = [item for item in all_entries if item["covered"]]
    missing_items = [item for item in all_entries if not item["covered"]]
    total_items = len(all_entries)
    coverage_score = int(round((len(covered_items) / total_items) * 100)) if total_items else 100
    missing_critical = [item for item in missing_items if item["category"] in {"structured_rule", "risk", "attachment_table"}]
    suggested_min = 1200 if pack_diag.get("estimated_evidence_level") in {"high", "very_high"} else 800
    is_short_by_coverage = bool(missing_critical and (coverage_score < 80 or report_chars < suggested_min))

    attachment_items = [item for item in all_entries if item["category"] in {"attachment", "attachment_table"}]
    attachment_missing = [item for item in attachment_items if not item["covered"]]
    return {
        "coverage_score": coverage_score,
        "covered_items": covered_items,
        "missing_items": missing_items,
        "main_material_coverage": material_coverage,
        "attachment_coverage": {
            "total_items": len(attachment_items),
            "covered_items": len(attachment_items) - len(attachment_missing),
            "missing_items": len(attachment_missing),
            "missing_labels": [item["label"] for item in attachment_missing],
        },
        "is_report_too_short_by_coverage": is_short_by_coverage,
    }


SOURCE_FACT_PATTERNS = [
    re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"),
    re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"),
    re.compile(r"\d+(?:\.\d+)?\s*(?:元/个|元|万元|%|％|个工作日|工作日|日|天)"),
    re.compile(r"[\u4e00-\u9fff]{2,18}(?:省|市|自治区|医保局|交易中心|药监局|卫生健康委|采购中心)"),
]

ANALYSIS_KEYWORDS = [
    "影响",
    "风险",
    "建议",
    "关注",
    "需要",
    "应当",
    "应",
    "需",
    "提示",
    "判断",
    "变化",
    "衔接",
    "应对",
]

ANALYSIS_GROUNDING_TERMS = [
    "医用耗材",
    "药品",
    "集采",
    "挂网",
    "申报",
    "采购",
    "执行",
    "产品范围",
    "执行时间",
    "医保",
    "支付",
    "价格",
    "报价",
    "中选",
    "企业",
    "医疗机构",
]

TECHNICAL_BODY_PHRASES = [
    "由于本次自动生成结果未形成完整正文",
    "供人工复核",
    "Dify",
    "evidence_pack",
    "OCR",
    "元数据",
    "自动生成结果",
    "兜底报告",
]


def _compact_marker(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _report_visible_text(markdown: str) -> str:
    text = _text(markdown)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[#>\-\*\+\s]+", "", text, flags=re.M)
    text = text.replace("|", " ")
    text = re.sub(r"[*_~]+", "", text)
    return text


def _pack_evidence_text(pack: dict[str, Any]) -> str:
    if not pack:
        return ""
    try:
        values = fact_support_values(pack)
    except EvidenceValidationError:
        return ""
    return json.dumps(values, ensure_ascii=False, default=str)


def _extract_source_fact_markers(text: str) -> list[str]:
    markers: list[str] = []
    seen: set[str] = set()
    for pattern in SOURCE_FACT_PATTERNS:
        for match in pattern.findall(text):
            marker = str(match).strip()
            compact = _compact_marker(marker)
            if len(compact) < 2 or compact in seen:
                continue
            seen.add(compact)
            markers.append(marker)
    return markers


def _source_fidelity_issues(report_text: str, evidence_text: str) -> list[dict[str, Any]]:
    compact_evidence = _compact_marker(evidence_text)
    issues: list[dict[str, Any]] = []
    for marker in _extract_source_fact_markers(report_text):
        if _compact_marker(marker) not in compact_evidence:
            issues.append(
                {
                    "code": "UNSUPPORTED_FACT",
                    "level": "error",
                    "message": f"报告中的硬事实未在原文证据中找到：{marker}",
                    "report_text": marker,
                }
            )
    return issues


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[。！？!?；;\n]+", text) if item.strip()]


def _analysis_depth(markdown: str, evidence_text: str) -> dict[str, Any]:
    report_text = _report_visible_text(markdown)
    compact_evidence = _compact_marker(evidence_text)
    sentences = _sentences(report_text)
    analysis_sentences = [
        sentence for sentence in sentences if any(keyword in sentence for keyword in ANALYSIS_KEYWORDS)
    ]
    backed_count = 0
    for sentence in analysis_sentences:
        sentence_markers = _extract_source_fact_markers(sentence)
        marker_supported = all(_compact_marker(marker) in compact_evidence for marker in sentence_markers)
        term_supported = any(term in sentence and _compact_marker(term) in compact_evidence for term in ANALYSIS_GROUNDING_TERMS)
        if marker_supported and term_supported:
            backed_count += 1
    analysis_heading_count = len(re.findall(r"(?:^|\n)\s*#{0,6}\s*(?:影响分析|风险提示|企业建议|企业关注|操作建议|分析提示|规则解读)", markdown))
    score = min(100, backed_count * 25 + min(len(analysis_sentences), 3) * 10 + analysis_heading_count * 15)
    summary_only_risk = backed_count < 2 or score < 50
    return {
        "analysis_depth_score": int(score),
        "summary_only_risk": bool(summary_only_risk),
        "evidence_backed_analysis_count": int(backed_count),
        "analysis_sentence_count": len(analysis_sentences),
    }


def _build_ab_quality_coverage(pack: dict[str, Any], report_markdown: str) -> dict[str, Any]:
    try:
        view = read_evidence_pack(pack)
    except EvidenceValidationError:
        return {"is_report_too_short_by_coverage": False, "validated_ab_only": True}
    markers: list[str] = []
    seen: set[str] = set()
    for item in view.get("evidence_items") or []:
        if not isinstance(item, dict) or item.get("level") not in {"A", "B"} or not item.get("mandatory"):
            continue
        for marker in _extract_source_fact_markers(json.dumps(item.get("value"), ensure_ascii=False, default=str)):
            compact = _compact_marker(marker)
            if compact and compact not in seen:
                seen.add(compact)
                markers.append(marker)
    report_text = _compact_marker(_report_visible_text(report_markdown))
    missing = [marker for marker in markers if _compact_marker(marker) not in report_text]
    return {
        "is_report_too_short_by_coverage": bool(missing),
        "validated_ab_only": True,
        "validated_marker_count": len(markers),
        "missing_validated_marker_count": len(missing),
    }


def _issue_text(issue: Any) -> str:
    if isinstance(issue, dict):
        return json.dumps(issue, ensure_ascii=False, default=str)
    return str(issue or "")


def _has_quality_blocker(record: dict[str, Any]) -> bool:
    quality_check = record.get("quality_check") if isinstance(record.get("quality_check"), dict) else {}
    if quality_check.get("passed") is False:
        return True
    issues = []
    for key in ("issues", "remaining_issues"):
        value = quality_check.get(key) if key == "issues" else record.get(key)
        if isinstance(value, list):
            issues.extend(value)
    blocker_terms = [
        "unsupported_claim",
        "unsupported_claims",
        "over_inference",
        "missing_table",
        "missing_core_rule",
        "quality_json_parse_failed",
        "fabrication",
        "table_evidence_mismatch",
    ]
    return any(any(term in _issue_text(issue) for term in blocker_terms) for issue in issues)


def _build_quality_gate(record: dict[str, Any], pack: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    markdown = _text(record.get("report_markdown"))
    try:
        evidence_values = fact_support_values(pack)
    except EvidenceValidationError:
        evidence_values = []
    has_material_evidence = bool(evidence_values)
    if not has_material_evidence:
        has_report = bool(markdown.strip())
        return {
            "source_fidelity_score": 100,
            "unsupported_fact_count": 0,
            "analysis_depth_score": 0,
            "summary_only_risk": False,
            "evidence_backed_analysis_count": 0,
            "analysis_sentence_count": 0,
            "deliverable_status": "failed" if str(record.get("status") or "").lower() == "failed" or not has_report else "needs_manual_review",
            "blocking_issues": (
                [
                    {
                        "code": "LOCAL_QUALITY_GATE_FAILED",
                        "level": "error",
                        "message": "No validated A/B evidence is available for automatic delivery.",
                    }
                ]
                if has_report
                else []
            ),
        }
    evidence_text = json.dumps(evidence_values, ensure_ascii=False, default=str)
    blocking_issues: list[dict[str, Any]] = []
    source_issues = _source_fidelity_issues(_report_visible_text(markdown), evidence_text)
    blocking_issues.extend(source_issues)
    analysis = _analysis_depth(markdown, evidence_text)
    if analysis["summary_only_risk"]:
        blocking_issues.append(
            {
                "code": "SUMMARY_ONLY_REPORT",
                "level": "warning",
                "message": "报告缺少有原文支撑的影响、风险或建议类分析，存在摘要化风险。",
            }
        )
    if coverage.get("is_report_too_short_by_coverage"):
        blocking_issues.append(
            {
                "code": "MISSING_CORE_COVERAGE",
                "level": "warning",
                "message": "报告遗漏主材料核心规则、风险后果或附件表格摘要。",
            }
        )
    technical_hits = [phrase for phrase in TECHNICAL_BODY_PHRASES if phrase in markdown]
    if technical_hits:
        blocking_issues.append(
            {
                "code": "TECHNICAL_NOTE_IN_REPORT_BODY",
                "level": "error",
                "message": "正式报告正文包含技术说明或兜底提示。",
                "phrases": technical_hits,
            }
        )
    if _has_quality_blocker(record):
        blocking_issues.append(
            {
                "code": "QUALITY_CHECK_BLOCKED",
                "level": "error",
                "message": "模型或本地质检已发现阻断交付的问题。",
            }
        )

    unsupported_fact_count = len(source_issues)
    source_fidelity_score = max(0, 100 - unsupported_fact_count * 25 - (20 if technical_hits else 0))
    status = str(record.get("status") or "").lower()
    if status == "failed" or not markdown.strip():
        deliverable_status = "failed"
    elif blocking_issues:
        deliverable_status = "needs_manual_review"
    else:
        deliverable_status = "deliverable"
    return {
        "source_fidelity_score": int(source_fidelity_score),
        "unsupported_fact_count": int(unsupported_fact_count),
        "analysis_depth_score": analysis["analysis_depth_score"],
        "summary_only_risk": analysis["summary_only_risk"],
        "evidence_backed_analysis_count": analysis["evidence_backed_analysis_count"],
        "analysis_sentence_count": analysis["analysis_sentence_count"],
        "deliverable_status": deliverable_status,
        "blocking_issues": blocking_issues,
    }


def build_run_diagnostics(record: dict[str, Any], pack: dict[str, Any] | None = None, dify_pack: dict[str, Any] | None = None) -> dict[str, Any]:
    pack_diag = build_pack_diagnostics(pack or {}, dify_pack)
    markdown = _text(record.get("report_markdown"))
    report_chars = _wps_like_word_count(markdown)
    quality_check = record.get("quality_check") if isinstance(record.get("quality_check"), dict) else {}
    issues = quality_check.get("issues") if isinstance(quality_check.get("issues"), list) else []
    generation_warnings = record.get("generation_warnings") if isinstance(record.get("generation_warnings"), list) else []
    remaining_issues = record.get("remaining_issues") if isinstance(record.get("remaining_issues"), list) else []
    versions = _version_items(record, report_chars)
    first_version_chars = next((int(item.get("chars") or 0) for item in versions if int(item.get("version") or 0) == 1), 0)
    coverage = _build_report_coverage(record, pack or {}, pack_diag, report_chars)
    quality_gate = _build_quality_gate(
        record,
        pack or {},
        _build_ab_quality_coverage(pack or {}, markdown),
    )

    diagnosis = [item for item in pack_diag.get("diagnosis", []) if item.get("code") != "OK"]
    evidence_level = pack_diag["estimated_evidence_level"]
    weighted_evidence_chars = int(pack_diag.get("weighted_evidence_chars") or 0)
    if evidence_level == "medium" and report_chars < 800:
        diagnosis.append(
            _diagnosis(
                "REPORT_TOO_SHORT_FOR_WEIGHTED_EVIDENCE",
                "warning",
                "加权证据量达到中等水平，但最终报告明显偏短，建议检查 Dify 生成节点是否只读取 summary，或 max_tokens 是否过小。",
            )
        )
    if evidence_level in {"high", "very_high"} and report_chars < 1200:
        diagnosis.append(
            _diagnosis(
                "REPORT_TOO_SHORT_FOR_WEIGHTED_EVIDENCE",
                "warning",
                "加权证据量较高，但最终报告明显偏短，建议检查 Dify 生成节点是否只读取 summary，或 max_tokens 是否过小。",
            )
        )
    if first_version_chars and report_chars and report_chars < first_version_chars * 0.65:
        diagnosis.append(
            _diagnosis(
                "REVISION_SHRANK_REPORT",
                "warning",
                "修订后报告长度明显小于初稿，建议检查质检修订节点是否过度删减。",
            )
        )
    if weighted_evidence_chars > 5000 and report_chars < 800 and pack_diag.get("summary_chars", 0) < pack_diag["raw_total_content_chars"] * 0.35:
        diagnosis.append(
            _diagnosis(
                "ONLY_SUMMARY_USED",
                "warning",
                "原文正文较多但报告较短，可能只使用了 summary，建议检查 Dify 节点输入变量。",
            )
        )
    if _report_may_be_truncated(markdown):
        diagnosis.append(
            _diagnosis(
                "OUTPUT_MAY_BE_TRUNCATED",
                "warning",
                "报告结尾或结构存在截断迹象，建议检查模型最大输出长度或 Dify 返回解析。",
            )
        )
    if coverage["is_report_too_short_by_coverage"]:
        diagnosis.append(
            _diagnosis(
                "REPORT_MISSING_CORE_COVERAGE",
                "warning",
                "报告遗漏主材料核心规则、风险后果或附件表格摘要，建议补齐后再判断字数是否足够。",
            )
        )
    if quality_gate["deliverable_status"] == "needs_manual_review":
        diagnosis.append(
            _diagnosis(
                "QUALITY_GATE_NEEDS_MANUAL_REVIEW",
                "warning",
                "报告未通过原文遵循或分析深度门禁，不能直接标记为可交付。",
            )
        )
    if not diagnosis:
        diagnosis.append(_diagnosis("OK", "normal", "报告长度与证据包信息量基本匹配。"))

    return {
        "evidence": {
            "primary_content_chars": pack_diag["primary_content_chars"],
            "primary_summary_chars": pack_diag["primary_summary_chars"],
            "primary_key_fact_chars": pack_diag["primary_key_fact_chars"],
            "primary_attachment_summary_chars": pack_diag["primary_attachment_summary_chars"],
            "primary_attachment_table_summary_chars": pack_diag["primary_attachment_table_summary_chars"],
            "auxiliary_content_chars": pack_diag["auxiliary_content_chars"],
            "auxiliary_summary_chars": pack_diag["auxiliary_summary_chars"],
            "auxiliary_relevant_snippet_chars": pack_diag["auxiliary_relevant_snippet_chars"],
            "auxiliary_attachment_summary_chars": pack_diag["auxiliary_attachment_summary_chars"],
            "auxiliary_attachment_table_summary_chars": pack_diag["auxiliary_attachment_table_summary_chars"],
            "raw_total_content_chars": pack_diag["raw_total_content_chars"],
            "dify_compact_pack_chars": pack_diag["dify_compact_pack_chars"],
            "weighted_evidence_chars": pack_diag["weighted_evidence_chars"],
            "total_content_chars": pack_diag["raw_total_content_chars"],
            "estimated_evidence_level": pack_diag["estimated_evidence_level"],
            "suggested_report_length": pack_diag["suggested_report_length"],
            "input_strategy": pack_diag.get("input_strategy", ""),
            "generation_mode": pack_diag.get("generation_mode", ""),
            "target_report_length": pack_diag.get("target_report_length", ""),
        },
        "report": {
            "report_title_exists": bool(_text(record.get("report_title")).strip()),
            "report_markdown_chars": report_chars,
            "quality_passed": quality_check.get("passed"),
            "issues_count": len(issues),
            "generation_warnings_count": len(generation_warnings),
            "remaining_issues_count": len(remaining_issues),
        },
        "versions": versions,
        "coverage": coverage,
        "quality_gate": quality_gate,
        "report_memory": {
            "requested": bool(record.get("use_report_memory")),
            "applied": bool(record.get("report_memory_applied")),
            "chars": int(record.get("report_memory_chars") or 0),
            "hash_prefix": str(record.get("report_memory_hash") or "")[:12],
            "read_failed": bool(record.get("memory_read_failed")),
            "truncated": bool(record.get("report_memory_truncated")),
            "used_memory_ids": list(record.get("used_memory_ids") or []),
            "memory_item_count": int(record.get("memory_item_count") or 0),
            "memory_item_chars": int(record.get("memory_item_chars") or 0),
            "memory_items_read_failed": bool(record.get("memory_items_read_failed")),
        },
        "diagnosis": diagnosis,
    }

