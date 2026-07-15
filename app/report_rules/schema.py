from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


ALLOWED_PRESENTATIONS = {"bullet", "table", "dedicated_section"}
DEFAULT_VBP_RULES_PATH = Path(__file__).resolve().parent / "vbp_topic_rules.yml"


class VbpTopicRulesError(ValueError):
    """Raised when the VBP topic rules file is missing required schema fields."""


def _env_bool(name: str, default: bool = False) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _require_non_empty_string(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise VbpTopicRulesError(f"{field} must be a non-empty string")


def _require_string_list(value: Any, field: str, *, allow_empty: bool = True) -> None:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise VbpTopicRulesError(f"{field} must be a list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise VbpTopicRulesError(f"{field} must contain only non-empty strings")


def validate_vbp_topic_rules(rules: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(rules, dict):
        raise VbpTopicRulesError("rules must be a mapping")

    _require_non_empty_string(rules.get("version"), "version")

    trigger = rules.get("trigger")
    if not isinstance(trigger, dict):
        raise VbpTopicRulesError("trigger must be a mapping")
    _require_string_list(trigger.get("menu_names"), "trigger.menu_names", allow_empty=False)
    _require_string_list(trigger.get("title_keywords", []), "trigger.title_keywords")
    _require_string_list(trigger.get("category_keywords", []), "trigger.category_keywords")

    topics = rules.get("topics")
    if not isinstance(topics, list) or not topics:
        raise VbpTopicRulesError("topics must be a non-empty list")

    seen_topic_ids: set[str] = set()
    for index, topic in enumerate(topics):
        prefix = f"topics[{index}]"
        if not isinstance(topic, dict):
            raise VbpTopicRulesError(f"{prefix} must be a mapping")
        for field in ("topic_id", "presentation", "failure_code"):
            _require_non_empty_string(topic.get(field), f"{prefix}.{field}")
        if topic.get("fact_type") is not None:
            _require_non_empty_string(topic.get("fact_type"), f"{prefix}.fact_type")
        _require_string_list(topic.get("labels", []), f"{prefix}.labels")
        presentation = str(topic["presentation"]).strip()
        if presentation not in ALLOWED_PRESENTATIONS:
            raise VbpTopicRulesError(f"{prefix}.presentation must be one of {sorted(ALLOWED_PRESENTATIONS)}")
        topic_id = str(topic["topic_id"]).strip()
        if topic_id in seen_topic_ids:
            raise VbpTopicRulesError(f"{prefix}.topic_id is duplicated: {topic_id}")
        seen_topic_ids.add(topic_id)

    _require_string_list(rules.get("forbidden_phrases", []), "forbidden_phrases")
    _require_string_list(rules.get("raw_object_patterns", []), "raw_object_patterns")

    report_structure = rules.get("report_structure")
    if not isinstance(report_structure, dict):
        raise VbpTopicRulesError("report_structure must be a mapping")
    _require_non_empty_string(report_structure.get("version"), "report_structure.version")
    required_sections = report_structure.get("required_sections")
    if not isinstance(required_sections, list) or not required_sections:
        raise VbpTopicRulesError("report_structure.required_sections must be a non-empty list")
    seen_section_ids: set[str] = set()
    for index, section in enumerate(required_sections):
        prefix = f"report_structure.required_sections[{index}]"
        if not isinstance(section, dict):
            raise VbpTopicRulesError(f"{prefix} must be a mapping")
        _require_non_empty_string(section.get("section_id"), f"{prefix}.section_id")
        _require_string_list(section.get("labels"), f"{prefix}.labels", allow_empty=False)
        section_id = str(section["section_id"]).strip()
        if section_id in seen_section_ids:
            raise VbpTopicRulesError(f"{prefix}.section_id is duplicated: {section_id}")
        seen_section_ids.add(section_id)
    return rules


def load_vbp_topic_rules(path: str | Path | None = None) -> dict[str, Any]:
    rules_path = Path(path) if path is not None else DEFAULT_VBP_RULES_PATH
    try:
        data = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise VbpTopicRulesError(f"failed to read VBP topic rules: {rules_path}") from exc
    return validate_vbp_topic_rules(data)


def load_vbp_topic_rules_if_enabled(path: str | Path | None = None) -> dict[str, Any] | None:
    if not _env_bool("ENABLE_VBP_PROJECT_OPTIMIZATION", True):
        return None
    return load_vbp_topic_rules(path)
