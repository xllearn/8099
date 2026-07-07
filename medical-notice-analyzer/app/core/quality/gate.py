from __future__ import annotations

import json
from typing import Any


def analysis_run_export_precheck(record: dict[str, Any], quality_gate: dict[str, Any] | None = None) -> dict[str, Any]:
    report_markdown = str(record.get("report_markdown") or "").strip()
    if not report_markdown:
        return _blocked("REPORT_NOT_READY", "report is not ready")

    effective_gate = quality_gate if isinstance(quality_gate, dict) else _dict_value(record, "quality_gate")
    quality_check = _dict_value(record, "quality_check")
    blocking_issues = _collect_blocking_issues(record, quality_check, effective_gate)
    return {
        "allowed": True,
        "code": "",
        "message": "",
        "detail": "",
        "quality_gate": effective_gate,
        "blocking_issues": blocking_issues,
    }


def _blocked(
    code: str,
    message: str,
    blocking_issues: list[dict[str, Any]] | None = None,
    quality_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issues = blocking_issues or []
    detail = {
        "blocking_issues": issues,
        "quality_gate": quality_gate or {},
    }
    return {
        "allowed": False,
        "code": code,
        "message": message,
        "detail": json.dumps(detail, ensure_ascii=False, default=str),
        "quality_gate": quality_gate or {},
        "blocking_issues": issues,
    }


def _dict_value(record: dict[str, Any], key: str) -> dict[str, Any]:
    value = record.get(key)
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _collect_blocking_issues(
    record: dict[str, Any],
    quality_check: dict[str, Any],
    quality_gate: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for item in _list_value(quality_gate.get("blocking_issues")):
        issues.append(_issue_dict(item))
    for item in _list_value(quality_check.get("issues")):
        issues.append(_issue_dict(item))
    for item in _list_value(record.get("remaining_issues")):
        issues.append(_issue_dict(item))
    return _dedupe_issues(issues)


def _issue_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    return {"message": str(item)}


def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for issue in issues:
        key = json.dumps(issue, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result
