from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Iterable

from app.evidence_schema import canonical_json


FAILURE_SCHEMA_VERSION = "8099.failure-attribution/v1"
DIAGNOSTIC_LAYERS = (
    "attachment_parse",
    "compact",
    "provider",
    "cleanup",
    "quality_gate",
)
TERMINAL_RUN_STATUSES = {"finished", "needs_manual_review", "failed", "interrupted"}
LAYER_FAILURE_CODES = {
    "attachment_parse": {
        "ATTACHMENT_REQUIRED_UNAVAILABLE",
        "ATTACHMENT_PARSE_FAILED",
        "UNCLASSIFIED",
    },
    "compact": {
        "COMPACT_MANDATORY_FIELDS_OVER_LIMIT",
        "COMPACT_PROVENANCE_LOST",
        "COMPACT_LIMIT_EXCEEDED",
        "UNCLASSIFIED",
    },
    "provider": {
        "HTTP_ERROR",
        "WORKFLOW_ID_MISSING",
        "WORKFLOW_FAILED",
        "OUTPUT_EMPTY",
        "OUTPUT_TRUNCATED",
        "OUTPUT_SCHEMA_INVALID",
        "TIMEOUT",
        "UNCLASSIFIED",
    },
    "cleanup": {
        "CLEANUP_OUTPUT_EMPTY",
        "CLEANUP_OUTPUT_TRUNCATED",
        "CLEANUP_SCHEMA_INVALID",
        "UNCLASSIFIED",
    },
    "quality_gate": {
        "VBP_RULE_CONFLICT",
        "VBP_EVIDENCE_INDEX_INVALID",
        "VBP_C_LEVEL_FACT_USED",
        "VBP_FACT_SOURCE_REF_INVALID",
        "VBP_UNSUPPORTED_CLAIM",
        "VBP_REQUIRED_SECTION_MISSING",
        "VBP_REQUIRED_TOPIC_MISSING",
        "UNSUPPORTED_FACT",
        "SUMMARY_ONLY_REPORT",
        "REPORT_TOO_SHORT",
        "REPORT_STRUCTURE_TOO_THIN",
        "FORBIDDEN_PHRASE_IN_REPORT",
        "FORBIDDEN_PHRASE_IN_FORMAL_BODY",
        "FORMAL_BODY_EMPTY",
        "LOCAL_QUALITY_GATE_FAILED",
        "MODEL_QA_BLOCKED",
        "EXPORT_GATE_BLOCKED",
        "UNCLASSIFIED",
    },
}
LAYER_STATUSES = {"not_run", "ok", "blocked", "skipped"}
LAYER_CODE_PRIORITY = {
    "attachment_parse": (
        "ATTACHMENT_REQUIRED_UNAVAILABLE",
        "ATTACHMENT_PARSE_FAILED",
        "UNCLASSIFIED",
    ),
    "compact": (
        "COMPACT_MANDATORY_FIELDS_OVER_LIMIT",
        "COMPACT_PROVENANCE_LOST",
        "COMPACT_LIMIT_EXCEEDED",
        "UNCLASSIFIED",
    ),
    "provider": (
        "HTTP_ERROR",
        "TIMEOUT",
        "WORKFLOW_FAILED",
        "WORKFLOW_ID_MISSING",
        "OUTPUT_EMPTY",
        "OUTPUT_TRUNCATED",
        "OUTPUT_SCHEMA_INVALID",
        "UNCLASSIFIED",
    ),
    "cleanup": (
        "CLEANUP_OUTPUT_EMPTY",
        "CLEANUP_OUTPUT_TRUNCATED",
        "CLEANUP_SCHEMA_INVALID",
        "UNCLASSIFIED",
    ),
    "quality_gate": (
        "VBP_RULE_CONFLICT",
        "VBP_EVIDENCE_INDEX_INVALID",
        "VBP_C_LEVEL_FACT_USED",
        "VBP_FACT_SOURCE_REF_INVALID",
        "VBP_UNSUPPORTED_CLAIM",
        "VBP_REQUIRED_SECTION_MISSING",
        "VBP_REQUIRED_TOPIC_MISSING",
        "UNSUPPORTED_FACT",
        "SUMMARY_ONLY_REPORT",
        "REPORT_TOO_SHORT",
        "REPORT_STRUCTURE_TOO_THIN",
        "FORMAL_BODY_EMPTY",
        "FORBIDDEN_PHRASE_IN_FORMAL_BODY",
        "FORBIDDEN_PHRASE_IN_REPORT",
        "EXPORT_GATE_BLOCKED",
        "MODEL_QA_BLOCKED",
        "LOCAL_QUALITY_GATE_FAILED",
        "UNCLASSIFIED",
    ),
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_METRIC_TERMS = {
    "attachment",
    "auth",
    "body",
    "content",
    "cookie",
    "key",
    "memory",
    "password",
    "prompt",
    "report",
    "response",
    "secret",
    "text",
    "token",
    "word",
}


class FailureAttributionError(ValueError):
    pass


def _canonical_code(layer: str, value: Any) -> str:
    code = str(value or "").strip().upper()
    if not code:
        return ""
    return code if code in LAYER_FAILURE_CODES[layer] else "UNCLASSIFIED"


def artifact_fingerprint(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {
            "kind": "bytes",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "chars": 0,
        }
    if isinstance(value, str):
        serialized = value
        kind = "text"
    else:
        serialized = canonical_json(value)
        kind = "json"
    return {
        "kind": kind,
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "bytes": len(serialized.encode("utf-8")),
        "chars": len(serialized),
    }


def _safe_metrics(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for raw_key in sorted(value, key=lambda item: str(item)):
        key = str(raw_key or "").strip()
        lowered = key.lower()
        if not key or any(term in lowered for term in _SENSITIVE_METRIC_TERMS):
            continue
        metric = value[raw_key]
        if isinstance(metric, bool) or metric is None or isinstance(metric, (int, float)):
            result[key] = metric
        elif isinstance(metric, str) and len(metric) <= 120:
            result[key] = metric
    return result


def _empty_layer(layer: str) -> dict[str, Any]:
    return {
        "layer": layer,
        "status": "not_run",
        "code": "",
        "input_artifact": None,
        "output_artifact": None,
        "metrics": {},
    }


def make_layer_result(
    layer: str,
    *,
    status: str,
    code: str = "",
    input_value: Any = None,
    output_value: Any = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_layer = str(layer or "").strip()
    if normalized_layer not in DIAGNOSTIC_LAYERS:
        raise FailureAttributionError("unknown diagnostic layer")
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in LAYER_STATUSES:
        raise FailureAttributionError("unknown layer status")
    normalized_code = _canonical_code(normalized_layer, code) if normalized_status == "blocked" else ""
    if normalized_status == "blocked" and not normalized_code:
        normalized_code = "UNCLASSIFIED"
    return {
        "layer": normalized_layer,
        "status": normalized_status,
        "code": normalized_code,
        "input_artifact": artifact_fingerprint(input_value),
        "output_artifact": artifact_fingerprint(output_value),
        "metrics": _safe_metrics(metrics),
    }


def _stage_rank(stage: dict[str, Any]) -> tuple[int, int, str]:
    rank = {"blocked": 0, "ok": 1, "skipped": 2, "not_run": 3}
    layer = str(stage.get("layer") or "")
    code = str(stage.get("code") or "")
    priorities = LAYER_CODE_PRIORITY.get(layer, ())
    code_rank = priorities.index(code) if code in priorities else len(priorities)
    return rank.get(str(stage.get("status") or "not_run"), 4), code_rank, code


def _normalize_fingerprint(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not _SHA256_RE.fullmatch(str(value.get("sha256") or "")):
        raise FailureAttributionError("invalid artifact fingerprint")
    kind = str(value.get("kind") or "")
    if kind not in {"bytes", "text", "json"}:
        raise FailureAttributionError("invalid artifact kind")
    try:
        byte_count = max(0, int(value.get("bytes") or 0))
        char_count = max(0, int(value.get("chars") or 0))
    except (TypeError, ValueError) as exc:
        raise FailureAttributionError("invalid artifact length") from exc
    return {
        "kind": kind,
        "sha256": str(value["sha256"]),
        "bytes": byte_count,
        "chars": char_count,
    }


def _normalize_existing_layer(layer: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _empty_layer(layer)
    status = str(value.get("status") or "not_run").strip().lower()
    if status not in LAYER_STATUSES:
        raise FailureAttributionError("unknown layer status")
    code = _canonical_code(layer, value.get("code")) if status == "blocked" else ""
    if status == "blocked" and not code:
        code = "UNCLASSIFIED"
    return {
        "layer": layer,
        "status": status,
        "code": code,
        "input_artifact": _normalize_fingerprint(value.get("input_artifact")),
        "output_artifact": _normalize_fingerprint(value.get("output_artifact")),
        "metrics": _safe_metrics(value.get("metrics")),
    }


def _ordered_layers(stages: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {layer: [] for layer in DIAGNOSTIC_LAYERS}
    for stage in stages:
        if not isinstance(stage, dict):
            raise FailureAttributionError("layer result must be an object")
        layer = str(stage.get("layer") or "")
        if layer not in grouped:
            raise FailureAttributionError("unknown diagnostic layer")
        grouped[layer].append(_normalize_existing_layer(layer, stage))
    result: dict[str, dict[str, Any]] = {}
    for layer in DIAGNOSTIC_LAYERS:
        candidates = grouped[layer]
        result[layer] = sorted(candidates, key=_stage_rank)[0] if candidates else _empty_layer(layer)
    return result


def _failure_fields(
    *,
    run_status: str,
    deliverable: bool,
    layers: dict[str, dict[str, Any]],
) -> tuple[str, str, list[str]]:
    status = str(run_status or "").strip().lower()
    if status not in TERMINAL_RUN_STATUSES or deliverable:
        return "", "", []
    blocked = [layers[layer] for layer in DIAGNOSTIC_LAYERS if layers[layer]["status"] == "blocked"]
    if not blocked:
        fallback = dict(layers["quality_gate"])
        fallback.update({"status": "blocked", "code": "UNCLASSIFIED"})
        layers["quality_gate"] = fallback
        blocked = [fallback]
    primary = blocked[0]
    secondary = [stage["code"] for stage in blocked[1:] if stage["code"] and stage["code"] != primary["code"]]
    return primary["layer"], primary["code"], secondary


def build_failure_attribution(
    *,
    run_status: str,
    deliverable: bool,
    stages: Iterable[dict[str, Any]],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del existing
    layers = _ordered_layers(stages)
    primary_layer, primary_code, secondary = _failure_fields(
        run_status=run_status,
        deliverable=bool(deliverable),
        layers=layers,
    )
    return {
        "failure_schema_version": FAILURE_SCHEMA_VERSION,
        "primary_layer": primary_layer,
        "primary_failure_code": primary_code,
        "secondary_failure_codes": secondary,
        "layers": layers,
    }


def validate_failure_attribution(
    value: Any,
    *,
    run_status: str,
    deliverable: bool,
) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("failure_schema_version") != FAILURE_SCHEMA_VERSION:
        raise FailureAttributionError("unsupported failure attribution schema")
    raw_layers = value.get("layers")
    if not isinstance(raw_layers, dict):
        raise FailureAttributionError("layers must be an object")
    layers = {
        layer: _normalize_existing_layer(layer, raw_layers.get(layer))
        for layer in DIAGNOSTIC_LAYERS
    }
    primary_layer, primary_code, secondary = _failure_fields(
        run_status=run_status,
        deliverable=bool(deliverable),
        layers=layers,
    )
    normalized = {
        "failure_schema_version": FAILURE_SCHEMA_VERSION,
        "primary_layer": primary_layer,
        "primary_failure_code": primary_code,
        "secondary_failure_codes": secondary,
        "layers": layers,
    }
    if normalized != value:
        raise FailureAttributionError("failure attribution is not canonical")
    return copy.deepcopy(normalized)
