from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.diagnostics import build_run_diagnostics
from app.evidence_schema import EVIDENCE_SCHEMA_VERSION
from app.formal_body import FormalBodyDocument
from app.formal_body_safety import FORBIDDEN_PHRASES, scan_docx, scan_formal_body
from app.layered_diagnostics import FAILURE_SCHEMA_VERSION
from app.regression_manifest import (
    MANIFEST_CONTRACT_MAX_BYTES,
    manifest_sha256 as compute_manifest_sha256,
    validate_manifest_contract,
    verify_record_fingerprint,
)


ARTIFACT_SCHEMA_VERSION = "8099.fixed-regression/v2"
EVALUATION_SCHEMA_VERSION = "8099.regression-evaluation/v1"
BASELINE_SCHEMA_VERSION = "8099.regression-baseline/v2"
EVALUATOR_VERSION = "1.5.0"
UNSUPPORTED_EVAL_VERSION = "1"
BASELINE_ENVIRONMENT = "server_test"
BASELINE_COVERAGE_MODES = ("full", "fixed3_only")
DIAGNOSTICS_SOURCE_SHA256 = hashlib.sha256(
    Path(__file__).with_name("diagnostics.py").read_bytes()
).hexdigest()
REQUIRED_JSON_SNAPSHOTS = (
    "materials",
    "prepare",
    "compact_pack",
    "run",
    "report",
    "diagnostics",
    "history",
    "history_query",
    "word_contract",
)
TIMING_FIELDS = (
    "prepare_ms",
    "generation_ms",
    "local_quality_gate_ms",
    "repair_ms",
    "export_check_ms",
    "word_export_ms",
    "total_ms",
)
WORD_ENDPOINTS = (
    "run_download",
    "report_export",
    "report_export_checked",
    "file_download",
)
REQUIRED_WORD_SNAPSHOTS = {"word_run", "word_export", "word_checked"}
FAILURE_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
SOURCE_ATTACHMENT_UNAVAILABLE = "SOURCE_ATTACHMENT_UNAVAILABLE"
CONSISTENCY_FIELDS = (
    "run_id",
    "pack_id",
    "status",
    "provider",
    "workflow_run_id",
    "compact_pack_chars",
    "failure_schema_version",
    "primary_layer",
    "primary_failure_code",
    "word_export_available",
    "draft_word_export_available",
    "final_word_export_available",
    "deliverable",
    "needs_manual_review",
)
EVALUATOR_RULES = {
    "version": EVALUATOR_VERSION,
    "forbidden_phrases": list(FORBIDDEN_PHRASES),
    "consistency_fields": list(CONSISTENCY_FIELDS),
    "required_json_snapshots": list(REQUIRED_JSON_SNAPSHOTS),
    "timing_fields": list(TIMING_FIELDS),
    "word_endpoints": list(WORD_ENDPOINTS),
    "required_word_snapshots": sorted(REQUIRED_WORD_SNAPSHOTS),
    "baseline_environments": [BASELINE_ENVIRONMENT],
    "baseline_coverage_modes": list(BASELINE_COVERAGE_MODES),
    "percentile": "nearest-rank",
    "non_deliverable_primary_failure_code_required": True,
    "unsupported_eval_version": UNSUPPORTED_EVAL_VERSION,
    "diagnostics_source_sha256": DIAGNOSTICS_SOURCE_SHA256,
    "manifest_contract_max_bytes": MANIFEST_CONTRACT_MAX_BYTES,
    "sample_material_binding_required": True,
    "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
    "failure_schema_version": FAILURE_SCHEMA_VERSION,
}
EVALUATOR_RULES_SHA256 = hashlib.sha256(
    json.dumps(
        EVALUATOR_RULES,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def nearest_rank_percentile(values: Sequence[float], percentile: float) -> float | int:
    if not values:
        return 0
    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in (0, 1]")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _bool(value: Any) -> bool:
    return value is True


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_excluded_cases(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("excluded_cases must be a list")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    seen_identities: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("excluded_cases contains an invalid item")
        normalized = {
            key: str(item.get(key) or "")
            for key in ("case_id", "menu_code", "articleid", "code", "observed_at")
        }
        identity = (normalized["menu_code"], normalized["articleid"])
        if identity in seen_identities:
            raise ValueError("excluded_cases contains duplicate material identity")
        if (
            not all(normalized.values())
            or normalized["code"] != SOURCE_ATTACHMENT_UNAVAILABLE
            or normalized["case_id"] in seen
        ):
            raise ValueError("excluded_cases contains invalid source exclusion metadata")
        seen.add(normalized["case_id"])
        seen_identities.add(identity)
        result.append(normalized)
    return sorted(result, key=lambda item: item["case_id"])


def _normalize_case_identities(value: Any, field_name: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    result: list[dict[str, str]] = []
    seen_case_ids: set[str] = set()
    seen_identities: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"{field_name} contains an invalid item")
        normalized = {
            key: str(item.get(key) or "")
            for key in ("case_id", "menu_code", "articleid")
        }
        identity = (normalized["menu_code"], normalized["articleid"])
        if not all(normalized.values()):
            raise ValueError(f"{field_name} contains an incomplete identity")
        if normalized["case_id"] in seen_case_ids or identity in seen_identities:
            raise ValueError(f"{field_name} contains a duplicate identity")
        seen_case_ids.add(normalized["case_id"])
        seen_identities.add(identity)
        result.append(normalized)
    return sorted(result, key=lambda item: item["case_id"])


def _normalize_material_bindings(
    value: Any, field_name: str
) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_name} must be a non-empty list")
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"{field_name} contains an invalid material binding")
        normalized = {
            key: str(item.get(key) or "")
            for key in ("role", "menu_code", "articleid", "record_sha256")
        }
        identity = (
            normalized["role"],
            normalized["menu_code"],
            normalized["articleid"],
        )
        record_hash = normalized["record_sha256"]
        if (
            normalized["role"] not in {"primary", "auxiliary"}
            or not normalized["menu_code"]
            or not normalized["articleid"]
            or len(record_hash) != 64
            or any(character not in "0123456789abcdef" for character in record_hash)
            or identity in seen
        ):
            raise ValueError(f"{field_name} contains an invalid material identity/hash")
        seen.add(identity)
        result.append(normalized)
    return sorted(
        result,
        key=lambda item: (item["role"], item["menu_code"], item["articleid"]),
    )


def _manifest_case_material_bindings(
    manifest_contract: Mapping[str, Any],
) -> dict[str, list[dict[str, str]]]:
    validate_manifest_contract(manifest_contract)
    result: dict[str, list[dict[str, str]]] = {}
    for case in manifest_contract["cases"]:
        case_id = str(case.get("id") or "")
        if isinstance(case.get("skip"), Mapping):
            continue
        result[case_id] = _normalize_material_bindings(
            [
                {
                    "role": material.get("role"),
                    "menu_code": material.get("menu_code"),
                    "articleid": material.get("articleid"),
                    "record_sha256": material.get("record_sha256"),
                }
                for material in case.get("materials", [])
                if isinstance(material, Mapping)
            ],
            f"manifest case {case_id} material bindings",
        )
    return result


def _manifest_identity_partition(
    manifest_contract: Mapping[str, Any], subset: str
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    validate_manifest_contract(manifest_contract)
    if subset not in {"fixed3", "fixed10"}:
        raise ValueError("manifest identity partition requires fixed3 or fixed10")
    case_by_id = {
        str(case["id"]): case for case in manifest_contract["cases"]
    }
    selected_ids = [
        str(case_id) for case_id in manifest_contract["subsets"][subset]
    ]
    declared: list[dict[str, str]] = []
    selected: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    for case_id in selected_ids:
        case = case_by_id[case_id]
        identity = {
            "case_id": case_id,
            "menu_code": str(case.get("menu_code") or ""),
            "articleid": str(case.get("articleid") or ""),
        }
        declared.append(identity)
        skip = case.get("skip")
        if isinstance(skip, Mapping):
            excluded.append(
                {
                    **identity,
                    "code": str(skip.get("code") or ""),
                    "observed_at": str(skip.get("observed_at") or ""),
                }
            )
        else:
            selected.append(identity)
    return (
        _normalize_case_identities(declared, "manifest declared cases"),
        _normalize_case_identities(selected, "manifest selected cases"),
        _normalize_excluded_cases(excluded),
    )


def _quality_passed(run: Mapping[str, Any]) -> bool | None:
    value = run.get("quality_passed")
    if value in (True, False):
        return value
    quality_check = run.get("quality_check")
    if isinstance(quality_check, Mapping) and quality_check.get("passed") in (True, False):
        return bool(quality_check.get("passed"))
    return None


def quality_status_from_run(run: Mapping[str, Any]) -> str:
    status = str(run.get("status") or run.get("run_status") or "")
    if status not in {"finished", "failed", "needs_manual_review", "interrupted"}:
        return "pending"
    if _bool(run.get("deliverable")) and _quality_passed(run) is True:
        return "passed"
    if _bool(run.get("needs_manual_review")):
        return "needs_manual_review"
    return "failed"


def _history_query_forbidden_fields(value: Any) -> list[str]:
    forbidden = {
        "evidence_pack",
        "formal_body",
        "memory",
        "memory_items",
        "report_ir",
        "report_markdown",
    }
    found: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                field = str(key)
                if field in forbidden:
                    found.add(field)
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return sorted(found)


def compare_run_history(
    run: Mapping[str, Any],
    history: Mapping[str, Any],
    *,
    word_downloaded: bool,
) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    for field in CONSISTENCY_FIELDS:
        run_value = run.get(field)
        history_value = history.get(field)
        if field == "status":
            run_value = run.get("status") or run.get("run_status")
        if run_value != history_value:
            mismatches.append(
                {"field": field, "run": run_value, "history": history_value}
            )
    expected_quality = quality_status_from_run(run)
    if history.get("quality_status") != expected_quality:
        mismatches.append(
            {
                "field": "quality_status",
                "run": expected_quality,
                "history": history.get("quality_status"),
            }
        )
    expected_quality_passed = _quality_passed(run)
    if history.get("quality_passed") != expected_quality_passed:
        mismatches.append(
            {
                "field": "quality_passed",
                "run": expected_quality_passed,
                "history": history.get("quality_passed"),
            }
        )
    quality_gate = run.get("quality_gate")
    expected_gate_status = (
        str(quality_gate.get("deliverable_status") or "")
        if isinstance(quality_gate, Mapping)
        else ""
    )
    actual_gate_status = str(history.get("quality_gate_status") or "")
    if actual_gate_status != expected_gate_status:
        mismatches.append(
            {
                "field": "quality_gate_status",
                "run": expected_gate_status,
                "history": actual_gate_status,
            }
        )
    expected_download_available = _bool(run.get("word_export_available")) or _bool(
        run.get("draft_word_export_available")
    )
    if history.get("word_download_available") is not expected_download_available:
        mismatches.append(
            {
                "field": "word_download_available",
                "run": expected_download_available,
                "history": history.get("word_download_available"),
            }
        )
    if history.get("word_generated") is not expected_download_available:
        mismatches.append(
            {
                "field": "word_generated",
                "run": expected_download_available,
                "history": history.get("word_generated"),
            }
        )
    return {"consistent": not mismatches, "mismatches": mismatches}


def _flatten_phrase_hits(sample: Mapping[str, Any]) -> list[str]:
    hits = [str(item) for item in sample.get("forbidden_phrase_hits", []) if item]
    word_hits = sample.get("word_scan_hits")
    if isinstance(word_hits, Mapping):
        for values in word_hits.values():
            if isinstance(values, list):
                hits.extend(str(item) for item in values if item)
    return hits


def validate_sample_contract(sample: Mapping[str, Any]) -> None:
    required = (
        "sample_id",
        "case_id",
        "status",
        "provider",
        "workflow_run_id",
        "provider_run_id",
        "compact_pack_chars",
        "input_strategy",
        "evidence_pack_sha256",
        "timings",
        "quality_status",
        "unsupported_fact_count",
        "deliverable",
        "needs_manual_review",
        "word_export_available",
        "draft_word_export_available",
        "final_word_export_available",
        "history_consistency",
        "identity_hash_complete",
        "metrics_complete",
        "snapshot_replayable",
        "report_chars",
        "report_section_count",
        "elapsed_seconds",
        "material_bindings",
    )
    missing = [key for key in required if key not in sample]
    if missing:
        raise ValueError(f"sample is missing metrics: {', '.join(missing)}")
    deliverable = _bool(sample.get("deliverable"))
    code = sample.get("primary_failure_code")
    if not deliverable:
        if (
            not isinstance(code, str)
            or not FAILURE_CODE_PATTERN.fullmatch(code.strip())
        ):
            raise ValueError(
                "non-deliverable sample requires one scalar primary_failure_code"
            )
    elif str(code or "").strip():
        raise ValueError("deliverable sample must not have primary_failure_code")
    secondary = sample.get("secondary_failure_codes")
    if not isinstance(secondary, list) or not all(
        isinstance(item, str) and FAILURE_CODE_PATTERN.fullmatch(item.strip())
        for item in secondary
    ):
        raise ValueError("secondary_failure_codes must be a list of scalar codes")
    if str(code or "").strip() in secondary:
        raise ValueError("primary_failure_code must not be repeated as secondary")
    for field in ("quality_failure_codes", "generation_failure_codes"):
        values = sample.get(field)
        if not isinstance(values, list) or not all(
            isinstance(item, str) and FAILURE_CODE_PATTERN.fullmatch(item.strip())
            for item in values
        ):
            raise ValueError(f"{field} must be a list of scalar codes")
    if str(sample.get("provider") or "") == "dify" and not str(
        sample.get("workflow_run_id") or ""
    ).strip():
        raise ValueError("Dify sample requires workflow_run_id")
    if _int(sample.get("compact_pack_chars")) <= 0:
        raise ValueError("sample requires positive compact_pack_chars")
    evidence_hash = str(sample.get("evidence_pack_sha256") or "")
    if len(evidence_hash) != 64 or any(char not in "0123456789abcdef" for char in evidence_hash):
        raise ValueError("sample requires evidence_pack_sha256")
    timings = sample.get("timings")
    if not isinstance(timings, Mapping) or any(field not in timings for field in TIMING_FIELDS):
        raise ValueError("sample timings are incomplete")
    _normalize_material_bindings(sample.get("material_bindings"), "sample material bindings")


def _metric_summary(samples: Sequence[Mapping[str, Any]], failures: Sequence[Any]) -> dict[str, Any]:
    for sample in samples:
        validate_sample_contract(sample)
    elapsed = [_float(sample.get("elapsed_seconds")) for sample in samples]
    compact = [_int(sample.get("compact_pack_chars")) for sample in samples]
    failure_codes = Counter(
        str(sample.get("primary_failure_code") or "")
        for sample in samples
        if str(sample.get("primary_failure_code") or "")
    )
    providers = Counter(str(sample.get("provider") or "") for sample in samples)
    statuses = Counter(str(sample.get("status") or "") for sample in samples)
    phrase_hits = sum(len(_flatten_phrase_hits(sample)) for sample in samples)
    return {
        "sample_count": len(samples) + len(failures),
        "completed_sample_count": len(samples),
        "case_count": len({str(sample.get("case_id") or "") for sample in samples}),
        "passed_count": sum(_bool(sample.get("passed")) for sample in samples),
        "failed_count": len(failures) + sum(not _bool(sample.get("passed")) for sample in samples),
        "deliverable_count": sum(_bool(sample.get("deliverable")) for sample in samples),
        "needs_manual_review_count": sum(
            _bool(sample.get("needs_manual_review")) for sample in samples
        ),
        "unsupported_fact_count": sum(
            _int(sample.get("unsupported_fact_count")) for sample in samples
        ),
        "forbidden_phrase_hit_count": phrase_hits,
        "state_contradiction_count": sum(
            not bool((sample.get("history_consistency") or {}).get("consistent"))
            for sample in samples
        ),
        "missing_workflow_run_id_count": sum(
            str(sample.get("provider") or "") == "dify"
            and not str(sample.get("workflow_run_id") or "").strip()
            for sample in samples
        ),
        "identity_hash_complete_count": sum(
            _bool(sample.get("identity_hash_complete")) for sample in samples
        ),
        "metrics_complete_count": sum(_bool(sample.get("metrics_complete")) for sample in samples),
        "replayable_snapshot_count": sum(
            _bool(sample.get("snapshot_replayable")) for sample in samples
        ),
        "failure_code_counts": dict(sorted(failure_codes.items())),
        "provider_counts": dict(sorted(providers.items())),
        "status_counts": dict(sorted(statuses.items())),
        "elapsed_seconds": {
            "min": min(elapsed) if elapsed else 0,
            "max": max(elapsed) if elapsed else 0,
            "mean": round(sum(elapsed) / len(elapsed), 3) if elapsed else 0,
            "p50": nearest_rank_percentile(elapsed, 0.50),
            "p95": nearest_rank_percentile(elapsed, 0.95),
        },
        "compact_pack_chars": {
            "min": min(compact) if compact else 0,
            "max": max(compact) if compact else 0,
            "mean": round(sum(compact) / len(compact), 3) if compact else 0,
        },
    }


def evaluate_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    if artifact.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ValueError(f"artifact schema_version must be {ARTIFACT_SCHEMA_VERSION}")
    samples = artifact.get("samples")
    failures = artifact.get("failures", [])
    if not isinstance(samples, list) or not isinstance(failures, list):
        raise ValueError("artifact samples and failures must be lists")
    manifest_contract = artifact.get("manifest_contract")
    if not isinstance(manifest_contract, Mapping):
        raise ValueError("artifact is missing manifest_contract")
    validate_manifest_contract(manifest_contract)
    frozen_manifest_contract = json.loads(
        json.dumps(manifest_contract, ensure_ascii=False)
    )
    computed_manifest_sha256 = compute_manifest_sha256(frozen_manifest_contract)
    if str(artifact.get("manifest_sha256") or "") != computed_manifest_sha256:
        raise ValueError("artifact manifest_contract hash mismatch")
    normalized_samples = sorted(
        (dict(sample) for sample in samples if isinstance(sample, Mapping)),
        key=lambda item: str(item.get("sample_id") or ""),
    )
    if len(normalized_samples) != len(samples):
        raise ValueError("artifact contains an invalid sample")
    excluded = _normalize_excluded_cases(artifact.get("excluded_cases"))
    declared_cases = _normalize_case_identities(
        artifact.get("declared_cases"), "declared_cases"
    )
    selected_cases = _normalize_case_identities(
        artifact.get("selected_cases"), "selected_cases"
    )
    selected_case_count = _int(artifact.get("selected_case_count")) or len(
        {str(sample.get("case_id") or "") for sample in normalized_samples}
    )
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "evaluator_rules_sha256": EVALUATOR_RULES_SHA256,
        "unsupported_eval_version": UNSUPPORTED_EVAL_VERSION,
        "manifest_version": str(artifact.get("manifest_version") or ""),
        "manifest_sha256": computed_manifest_sha256,
        "manifest_contract": frozen_manifest_contract,
        "stage": str(artifact.get("stage") or ""),
        "environment": str(artifact.get("environment") or ""),
        "subset": str(artifact.get("subset") or ""),
        "repeat": _int(artifact.get("repeat")),
        "declared_case_count": _int(artifact.get("declared_case_count")),
        "selected_case_count": selected_case_count,
        "declared_cases": declared_cases,
        "selected_cases": selected_cases,
        "excluded_cases": excluded,
        "metrics": _metric_summary(normalized_samples, failures),
        "samples": [
            {
                "sample_id": str(sample.get("sample_id") or ""),
                "case_id": str(sample.get("case_id") or ""),
                "attempt": _int(sample.get("attempt")),
                "passed": _bool(sample.get("passed")),
                "status": str(sample.get("status") or ""),
                "provider": str(sample.get("provider") or ""),
                "workflow_run_id": str(sample.get("workflow_run_id") or ""),
                "provider_run_id": str(sample.get("provider_run_id") or ""),
                "elapsed_seconds": _float(sample.get("elapsed_seconds")),
                "compact_pack_chars": _int(sample.get("compact_pack_chars")),
                "input_strategy": str(sample.get("input_strategy") or ""),
                "evidence_pack_sha256": str(sample.get("evidence_pack_sha256") or ""),
                "timings": {
                    field: _int((sample.get("timings") or {}).get(field))
                    for field in TIMING_FIELDS
                },
                "quality_status": str(sample.get("quality_status") or ""),
                "quality_passed": sample.get("quality_passed"),
                "unsupported_fact_count": _int(sample.get("unsupported_fact_count")),
                "deliverable": _bool(sample.get("deliverable")),
                "needs_manual_review": _bool(sample.get("needs_manual_review")),
                "primary_failure_code": str(sample.get("primary_failure_code") or ""),
                "secondary_failure_codes": list(sample.get("secondary_failure_codes") or []),
                "quality_failure_codes": list(sample.get("quality_failure_codes") or []),
                "generation_failure_codes": list(sample.get("generation_failure_codes") or []),
                "word_export_available": _bool(sample.get("word_export_available")),
                "draft_word_export_available": _bool(
                    sample.get("draft_word_export_available")
                ),
                "final_word_export_available": _bool(
                    sample.get("final_word_export_available")
                ),
                "word_download_available": _bool(sample.get("word_download_available")),
                "word_generated": _bool(sample.get("word_generated")),
                "forbidden_phrase_hits": list(sample.get("forbidden_phrase_hits") or []),
                "word_scan_hits": dict(sample.get("word_scan_hits") or {}),
                "history_consistency": dict(sample.get("history_consistency") or {}),
                "identity_hash_complete": _bool(sample.get("identity_hash_complete")),
                "metrics_complete": _bool(sample.get("metrics_complete")),
                "snapshot_replayable": _bool(sample.get("snapshot_replayable")),
                "material_bindings": _normalize_material_bindings(
                    sample.get("material_bindings"),
                    f"sample {sample.get('sample_id')} material bindings",
                ),
                "report_chars": _int(sample.get("report_chars")),
                "report_section_count": _int(sample.get("report_section_count")),
            }
            for sample in normalized_samples
        ],
    }


def verify_snapshot_replay(root: Path, artifact: Mapping[str, Any]) -> dict[str, int]:
    root = Path(root).resolve()
    verified = 0
    total = 0
    for sample in artifact.get("samples", []):
        if not isinstance(sample, Mapping):
            raise ValueError("artifact contains an invalid sample")
        snapshots = sample.get("snapshots")
        if not isinstance(snapshots, Mapping):
            raise ValueError("sample snapshots must be an object")
        for reference in snapshots.values():
            total += 1
            if not isinstance(reference, Mapping):
                raise ValueError("snapshot reference must be an object")
            path = (root / str(reference.get("path") or "")).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError("snapshot path escapes artifact root") from exc
            if not path.is_file():
                raise ValueError(f"snapshot is missing: {path.name}")
            if sha256_file(path) != str(reference.get("sha256") or ""):
                raise ValueError(f"snapshot hash mismatch: {path.name}")
            if path.suffix.lower() == ".json":
                json.loads(path.read_text(encoding="utf-8"))
            verified += 1
    return {"verified": verified, "total": total}


def _snapshot_path(
    root: Path,
    snapshots: Mapping[str, Any],
    name: str,
) -> Path:
    reference = snapshots.get(name)
    if not isinstance(reference, Mapping):
        raise ValueError(f"sample is missing required snapshot: {name}")
    path = (root / str(reference.get("path") or "")).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"snapshot path escapes artifact root: {name}") from exc
    if not path.is_file():
        raise ValueError(f"snapshot is missing: {name}")
    if sha256_file(path) != str(reference.get("sha256") or ""):
        raise ValueError(f"snapshot hash mismatch: {name}")
    return path


def _json_snapshot(
    root: Path,
    snapshots: Mapping[str, Any],
    name: str,
) -> dict[str, Any]:
    path = _snapshot_path(root, snapshots, name)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"snapshot must contain an object: {name}")
    return value


def _material_hashes_complete(materials_snapshot: Mapping[str, Any]) -> bool:
    materials = materials_snapshot.get("materials")
    if not isinstance(materials, list) or not materials:
        return False
    for item in materials:
        if not isinstance(item, Mapping):
            return False
        expected = item.get("expected")
        actual = item.get("actual")
        record = item.get("record")
        if not all(isinstance(value, Mapping) for value in (expected, actual, record)):
            return False
        actual_attachments = actual.get("attachments")
        record_attachments = record.get("attachments")
        if not isinstance(actual_attachments, list) or not isinstance(record_attachments, list):
            return False
        verification_items = item.get("attachment_content_verification", [])
        if not isinstance(verification_items, list):
            return False
        verifications: dict[tuple[str, str, str], Mapping[str, Any]] = {}
        for verification in verification_items:
            if not isinstance(verification, Mapping):
                return False
            identity = verification.get("identity")
            if not isinstance(identity, list) or len(identity) != 3:
                return False
            identity_key = tuple(str(part or "") for part in identity)
            if identity_key in verifications or verification.get("verifier") not in {
                "stream_sha256_v1",
                "normalized_text_sha256_v1",
            }:
                return False
            verifications[identity_key] = verification
        if record_attachments and not verifications:
            return False
        try:
            rebuilt = verify_record_fingerprint(
                record,
                expected,
                verified_attachment_content=verifications,
            )
        except ValueError:
            return False
        if dict(actual) != rebuilt:
            return False
    return True


def _material_bindings_from_snapshot(
    materials_snapshot: Mapping[str, Any], expected_case_id: str
) -> list[dict[str, str]]:
    if str(materials_snapshot.get("case_id") or "") != expected_case_id:
        raise ValueError("materials snapshot case_id does not match sample case_id")
    if not _material_hashes_complete(materials_snapshot):
        raise ValueError("materials snapshot identity/hash verification failed")
    materials = materials_snapshot.get("materials")
    return _normalize_material_bindings(
        [
            {
                "role": item.get("role"),
                "menu_code": (item.get("actual") or {}).get("menu_code"),
                "articleid": (item.get("actual") or {}).get("articleid"),
                "record_sha256": (item.get("actual") or {}).get("record_sha256"),
            }
            for item in materials
            if isinstance(item, Mapping) and isinstance(item.get("actual"), Mapping)
        ],
        f"sample {expected_case_id} material bindings",
    )


def _word_contract_mismatches(
    run: Mapping[str, Any],
    word_contract: Mapping[str, Any],
    word_snapshots: Sequence[str],
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    draft = _bool(run.get("draft_word_export_available"))
    final = _bool(run.get("final_word_export_available"))
    export = _bool(run.get("word_export_available"))
    deliverable = _bool(run.get("deliverable"))
    manual = _bool(run.get("needs_manual_review"))
    if draft != export:
        mismatches.append({"field": "word_export_available", "run": export, "expected": draft})
    if final != (deliverable and not manual):
        mismatches.append(
            {
                "field": "final_word_export_available",
                "run": final,
                "expected": deliverable and not manual,
            }
        )
    endpoint_statuses = word_contract.get("endpoint_statuses")
    if not isinstance(endpoint_statuses, Mapping):
        mismatches.append({"field": "word_endpoint_statuses", "run": None, "expected": "object"})
    else:
        expected_status = 200 if draft else 503
        for endpoint in WORD_ENDPOINTS:
            if _int(endpoint_statuses.get(endpoint)) != expected_status:
                mismatches.append(
                    {
                        "field": f"{endpoint}_status",
                        "run": endpoint_statuses.get(endpoint),
                        "expected": expected_status,
                    }
                )
    snapshot_names = set(word_snapshots)
    if draft:
        missing_snapshots = sorted(REQUIRED_WORD_SNAPSHOTS - snapshot_names)
        if missing_snapshots:
            mismatches.append(
                {
                    "field": "word_snapshots",
                    "run": sorted(snapshot_names),
                    "expected": sorted(REQUIRED_WORD_SNAPSHOTS),
                }
            )
    elif snapshot_names:
        mismatches.append(
            {"field": "word_snapshots", "run": sorted(snapshot_names), "expected": []}
        )
    if not draft:
        exposed = [
            field
            for field in (
                "word_download_url",
                "download_url",
                "word_filename",
                "word_file_path",
                "word_path",
            )
            if str(run.get(field) or "").strip()
        ]
        if exposed:
            mismatches.append(
                {"field": "word_download_locators", "run": exposed, "expected": []}
            )
    staging = word_contract.get("staging_artifacts")
    if not isinstance(staging, list) or staging:
        mismatches.append(
            {"field": "word_staging_artifacts", "run": staging, "expected": []}
        )
    return mismatches


def _report_section_count(report_ir: Any, markdown: str) -> int:
    if isinstance(report_ir, Mapping):
        sections = report_ir.get("sections")
        if isinstance(sections, list):
            return len(sections)
    return len(re.findall(r"(?m)^\s*#{1,6}\s+\S", markdown))


def _derive_sample_from_snapshots(
    root: Path,
    sample: Mapping[str, Any],
) -> dict[str, Any]:
    snapshots = sample.get("snapshots")
    if not isinstance(snapshots, Mapping):
        raise ValueError("sample snapshots must be an object")
    for name in REQUIRED_JSON_SNAPSHOTS:
        if name not in snapshots:
            raise ValueError(f"sample is missing required snapshot: {name}")
    materials = _json_snapshot(root, snapshots, "materials")
    prepare = _json_snapshot(root, snapshots, "prepare")
    compact_pack = _json_snapshot(root, snapshots, "compact_pack")
    run = _json_snapshot(root, snapshots, "run")
    report = _json_snapshot(root, snapshots, "report")
    reported_diagnostics = _json_snapshot(root, snapshots, "diagnostics")
    history = _json_snapshot(root, snapshots, "history")
    history_query = _json_snapshot(root, snapshots, "history_query")
    word_contract = _json_snapshot(root, snapshots, "word_contract")
    case_id = str(sample.get("case_id") or "")
    material_bindings = _material_bindings_from_snapshot(materials, case_id)

    evidence_pack = prepare.get("evidence_pack")
    if not isinstance(evidence_pack, dict) or not evidence_pack:
        raise ValueError("prepare snapshot is missing evidence_pack")
    report_markdown = str(
        report.get("report_markdown") or run.get("report_markdown") or ""
    ).strip()
    report_ir = report.get("report_ir")
    if not isinstance(report_ir, Mapping):
        report_ir = run.get("report_ir")
    if not report_markdown and not isinstance(report_ir, Mapping):
        raise ValueError("snapshot report has no FormalBody")

    run_id = str(run.get("run_id") or "")
    pack_id = str(run.get("pack_id") or "")
    if run_id != str(sample.get("run_id") or ""):
        raise ValueError("sample run_id does not match run snapshot")
    if pack_id != str(sample.get("pack_id") or "") or pack_id != str(
        prepare.get("pack_id") or evidence_pack.get("pack_id") or ""
    ):
        raise ValueError("sample pack_id does not match prepare/run snapshots")

    merged_run = dict(run)
    merged_run["report_markdown"] = report_markdown
    if isinstance(report_ir, Mapping):
        merged_run["report_ir"] = dict(report_ir)
    recomputed_diagnostics = build_run_diagnostics(
        merged_run,
        evidence_pack,
        compact_pack,
    )
    quality_gate = recomputed_diagnostics.get("quality_gate")
    if not isinstance(quality_gate, Mapping):
        raise ValueError("offline quality diagnostics did not return quality_gate")

    formal_scan = scan_formal_body(
        FormalBodyDocument(
            markdown=report_markdown,
            report_ir=dict(report_ir) if isinstance(report_ir, Mapping) else None,
        )
    )
    formal_hits = list(dict.fromkeys(hit.phrase for hit in formal_scan.hits))
    word_scan_hits: dict[str, list[str]] = {}
    word_snapshot_names = sorted(
        name
        for name in snapshots
        if str(name).startswith("word_") and name != "word_contract"
    )
    for name in word_snapshot_names:
        path = _snapshot_path(root, snapshots, name)
        if path.suffix.lower() != ".docx":
            raise ValueError(f"Word snapshot is not DOCX: {name}")
        word_scan_hits[path.name] = list(
            dict.fromkeys(hit.phrase for hit in scan_docx(path))
        )

    endpoint_statuses = word_contract.get("endpoint_statuses")
    word_downloaded = isinstance(endpoint_statuses, Mapping) and _int(
        endpoint_statuses.get("run_download")
    ) == 200
    consistency = compare_run_history(
        run,
        history,
        word_downloaded=word_downloaded,
    )
    items = history_query.get("items")
    if not isinstance(items, list) or not any(
        isinstance(item, Mapping) and item.get("run_id") == run_id for item in items
    ):
        consistency["mismatches"].append(
            {"field": "history_query", "run": run_id, "history": "missing"}
        )
    for field in _history_query_forbidden_fields(history_query):
        consistency["mismatches"].append(
            {
                "field": f"history_query.{field}",
                "run": "absent",
                "history": "exposed",
            }
        )
    consistency["mismatches"].extend(
        _word_contract_mismatches(run, word_contract, word_snapshot_names)
    )
    compact_snapshot_chars = _int(
        compact_pack.get("compact_pack_chars")
        or compact_pack.get("final_dify_input_chars")
    )
    if compact_snapshot_chars != _int(run.get("compact_pack_chars")):
        consistency["mismatches"].append(
            {
                "field": "compact_pack_chars",
                "run": run.get("compact_pack_chars"),
                "history": compact_snapshot_chars,
            }
        )
    if str(compact_pack.get("input_strategy") or "") != str(
        run.get("input_strategy") or ""
    ):
        consistency["mismatches"].append(
            {
                "field": "input_strategy",
                "run": run.get("input_strategy"),
                "history": compact_pack.get("input_strategy"),
            }
        )
    recomputed_unsupported = _int(quality_gate.get("unsupported_fact_count"))
    run_quality_gate = run.get("quality_gate")
    diagnostics_body = reported_diagnostics.get("diagnostics")
    if not isinstance(diagnostics_body, Mapping):
        diagnostics_body = reported_diagnostics
    reported_quality_gate = diagnostics_body.get("quality_gate")
    for source, value in (
        (
            "run_quality_gate",
            _int(run_quality_gate.get("unsupported_fact_count"))
            if isinstance(run_quality_gate, Mapping)
            else None,
        ),
        (
            "diagnostics_quality_gate",
            _int(reported_quality_gate.get("unsupported_fact_count"))
            if isinstance(reported_quality_gate, Mapping)
            else None,
        ),
    ):
        if value != recomputed_unsupported:
            consistency["mismatches"].append(
                {
                    "field": f"{source}.unsupported_fact_count",
                    "run": value,
                    "history": recomputed_unsupported,
                }
            )
    recomputed_gate_status = str(quality_gate.get("deliverable_status") or "")
    if isinstance(run_quality_gate, Mapping) and str(
        run_quality_gate.get("deliverable_status") or ""
    ) != recomputed_gate_status:
        consistency["mismatches"].append(
            {
                "field": "quality_gate.deliverable_status",
                "run": run_quality_gate.get("deliverable_status"),
                "history": recomputed_gate_status,
            }
        )
    consistency["consistent"] = not consistency["mismatches"]

    timings_value = word_contract.get("timings")
    if not isinstance(timings_value, Mapping):
        timings_value = {}
    timings = {field: _int(timings_value.get(field)) for field in TIMING_FIELDS}
    quality_status = quality_status_from_run(run)
    quality_passed = _quality_passed(run)
    provider = str(run.get("provider") or "").strip()
    workflow_run_id = str(run.get("workflow_run_id") or "").strip()
    compact_pack_chars = _int(run.get("compact_pack_chars"))
    input_strategy = str(run.get("input_strategy") or "").strip()
    evidence_pack_sha256 = sha256_json(evidence_pack)
    metrics_complete = bool(
        provider
        and str(run.get("status") or run.get("run_status") or "").strip()
        and quality_status
        and compact_pack_chars > 0
        and input_strategy
        and evidence_pack_sha256
        and (provider != "dify" or workflow_run_id)
        and timings["total_ms"] > 0
    )
    derived = {
        **dict(sample),
        "passed": True,
        "status": str(run.get("status") or run.get("run_status") or ""),
        "provider": provider,
        "workflow_run_id": workflow_run_id,
        "provider_run_id": str(run.get("provider_run_id") or workflow_run_id),
        "compact_pack_chars": compact_pack_chars,
        "input_strategy": input_strategy,
        "evidence_pack_sha256": evidence_pack_sha256,
        "timings": timings,
        "quality_status": quality_status,
        "quality_passed": quality_passed,
        "unsupported_fact_count": recomputed_unsupported,
        "primary_failure_code": run.get("primary_failure_code") or "",
        "secondary_failure_codes": list(run.get("secondary_failure_codes") or []),
        "quality_failure_codes": list(run.get("quality_failure_codes") or []),
        "generation_failure_codes": list(run.get("generation_failure_codes") or []),
        "deliverable": _bool(run.get("deliverable")),
        "needs_manual_review": _bool(run.get("needs_manual_review")),
        "word_export_available": _bool(run.get("word_export_available")),
        "draft_word_export_available": _bool(
            run.get("draft_word_export_available")
        ),
        "final_word_export_available": _bool(
            run.get("final_word_export_available")
        ),
        "word_download_available": _bool(history.get("word_download_available")),
        "word_generated": _bool(history.get("word_generated")),
        "forbidden_phrase_hits": formal_hits,
        "word_scan_hits": word_scan_hits,
        "staging_artifacts": list(word_contract.get("staging_artifacts") or []),
        "history_consistency": consistency,
        "identity_hash_complete": _material_hashes_complete(materials),
        "metrics_complete": metrics_complete,
        "snapshot_replayable": True,
        "material_bindings": material_bindings,
        "report_chars": len(report_markdown),
        "report_section_count": _report_section_count(report_ir, report_markdown),
        "elapsed_seconds": round(timings["total_ms"] / 1000, 3),
    }
    return derived


def evaluate_artifact_from_snapshots(
    root: Path,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(root).resolve()
    replay = verify_snapshot_replay(root, artifact)
    samples = artifact.get("samples")
    if not isinstance(samples, list):
        raise ValueError("artifact samples must be a list")
    derived_artifact = dict(artifact)
    derived_artifact["samples"] = [
        _derive_sample_from_snapshots(root, sample)
        for sample in samples
        if isinstance(sample, Mapping)
    ]
    if len(derived_artifact["samples"]) != len(samples):
        raise ValueError("artifact contains an invalid sample")
    evaluation = evaluate_artifact(derived_artifact)
    evaluation["snapshot_verification"] = replay
    return evaluation


def _validate_evaluation_for_freeze(evaluation: Mapping[str, Any]) -> None:
    if evaluation.get("schema_version") != EVALUATION_SCHEMA_VERSION:
        raise ValueError("evaluation schema_version mismatch")
    metrics = evaluation.get("metrics")
    samples = evaluation.get("samples")
    if not isinstance(metrics, Mapping) or not isinstance(samples, list):
        raise ValueError("evaluation is missing metrics or samples")
    environment = str(evaluation.get("environment") or "")
    if environment != BASELINE_ENVIRONMENT:
        raise ValueError(
            f"baseline evaluation environment must be {BASELINE_ENVIRONMENT}"
        )
    subset = str(evaluation.get("subset") or "")
    if str(evaluation.get("stage") or "").strip().upper() != "S0":
        raise ValueError("baseline evaluation stage must be S0")
    manifest_contract = evaluation.get("manifest_contract")
    if not isinstance(manifest_contract, Mapping):
        raise ValueError("evaluation is missing manifest_contract")
    validate_manifest_contract(manifest_contract)
    if compute_manifest_sha256(manifest_contract) != str(
        evaluation.get("manifest_sha256") or ""
    ):
        raise ValueError("evaluation manifest_contract hash mismatch")
    expected_repeat = {"fixed3": 3, "fixed10": 1}.get(subset)
    if expected_repeat is None:
        raise ValueError("baseline only accepts fixed3 or fixed10 evaluations")
    if _int(evaluation.get("repeat")) != expected_repeat:
        raise ValueError(f"{subset} baseline requires repeat={expected_repeat}")
    expected_cases = _int(evaluation.get("selected_case_count"))
    exclusions = _normalize_excluded_cases(evaluation.get("excluded_cases"))
    declared_cases = _normalize_case_identities(
        evaluation.get("declared_cases"), "declared_cases"
    )
    selected_cases = _normalize_case_identities(
        evaluation.get("selected_cases"), "selected_cases"
    )
    declared_count = _int(evaluation.get("declared_case_count"))
    manifest_declared, manifest_selected, manifest_excluded = _manifest_identity_partition(
        manifest_contract, subset
    )
    if declared_cases != manifest_declared:
        raise ValueError(f"{subset} declared identity differs from manifest identity")
    if selected_cases != manifest_selected:
        raise ValueError(f"{subset} selected identity differs from manifest identity")
    if exclusions != manifest_excluded:
        raise ValueError(f"{subset} excluded identity differs from manifest identity")
    if subset == "fixed3":
        if expected_cases != 3 or exclusions:
            raise ValueError("fixed3 baseline requires 3 selected cases and no exclusions")
    elif expected_cases < 1 or expected_cases + len(exclusions) != 10:
        raise ValueError(
            "fixed10 baseline requires 10 declared cases including explicit source exclusions"
        )
    required_declared_count = 3 if subset == "fixed3" else 10
    if declared_count != required_declared_count or len(declared_cases) != declared_count:
        raise ValueError(f"{subset} declared case identities are incomplete")
    if len(selected_cases) != expected_cases:
        raise ValueError(f"{subset} selected case identities are incomplete")
    declared_by_id = {item["case_id"]: item for item in declared_cases}
    selected_by_id = {item["case_id"]: item for item in selected_cases}
    excluded_by_id = {item["case_id"]: item for item in exclusions}
    if set(selected_by_id) & set(excluded_by_id):
        raise ValueError(f"{subset} selected and excluded case identities overlap")
    if set(selected_by_id) | set(excluded_by_id) != set(declared_by_id):
        raise ValueError(f"{subset} declared identity coverage is incomplete")
    for case_id, item in selected_by_id.items():
        declared = declared_by_id[case_id]
        if (item["menu_code"], item["articleid"]) != (
            declared["menu_code"],
            declared["articleid"],
        ):
            raise ValueError(f"{subset} selected identity differs from declared identity")
    for case_id, item in excluded_by_id.items():
        declared = declared_by_id[case_id]
        if (item["menu_code"], item["articleid"]) != (
            declared["menu_code"],
            declared["articleid"],
        ):
            raise ValueError(f"{subset} excluded identity differs from declared identity")
    expected_samples = expected_cases * expected_repeat
    if (
        _int(metrics.get("sample_count")) != expected_samples
        or _int(metrics.get("completed_sample_count")) != expected_samples
        or len(samples) != expected_samples
    ):
        raise ValueError(f"{subset} baseline requires {expected_samples} completed samples")
    if _int(metrics.get("case_count")) != expected_cases:
        raise ValueError(f"{subset} baseline requires {expected_cases} unique cases")
    required_zero = (
        "failed_count",
        "forbidden_phrase_hit_count",
        "state_contradiction_count",
        "missing_workflow_run_id_count",
    )
    failed = [field for field in required_zero if _int(metrics.get(field)) != 0]
    if failed:
        raise ValueError(f"{subset} evaluation failed gates: {', '.join(failed)}")
    if _int(metrics.get("identity_hash_complete_count")) != expected_samples:
        raise ValueError(f"{subset} identity hashes are incomplete")
    if _int(metrics.get("metrics_complete_count")) != expected_samples:
        raise ValueError(f"{subset} metrics are incomplete")
    if _int(metrics.get("replayable_snapshot_count")) != expected_samples:
        raise ValueError(f"{subset} snapshots are not fully replayable")
    sample_ids = [str(sample.get("sample_id") or "") for sample in samples]
    if not all(sample_ids) or len(set(sample_ids)) != expected_samples:
        raise ValueError(f"{subset} sample ids must be unique")
    attempts_by_case: dict[str, set[int]] = {}
    manifest_bindings = _manifest_case_material_bindings(manifest_contract)
    for sample in samples:
        case_id = str(sample.get("case_id") or "")
        actual_bindings = _normalize_material_bindings(
            sample.get("material_bindings"),
            f"sample {case_id} material bindings",
        )
        if actual_bindings != manifest_bindings.get(case_id):
            raise ValueError(
                f"sample {case_id} material binding differs from manifest identity/hash"
            )
        attempts_by_case.setdefault(case_id, set()).add(_int(sample.get("attempt")))
    if set(attempts_by_case) != set(selected_by_id):
        raise ValueError(f"{subset} samples differ from selected case identities")
    if len(attempts_by_case) != expected_cases or "" in attempts_by_case:
        raise ValueError(f"{subset} baseline requires {expected_cases} unique cases")
    expected_attempts = {1, 2, 3} if subset == "fixed3" else {1}
    if any(attempts != expected_attempts for attempts in attempts_by_case.values()):
        raise ValueError(f"{subset} attempts are incomplete")


def freeze_baseline(
    *,
    baseline_id: str,
    manifest_sha256: str,
    evaluations: Iterable[Mapping[str, Any]],
    coverage_mode: str = "full",
) -> dict[str, Any]:
    if coverage_mode not in BASELINE_COVERAGE_MODES:
        raise ValueError(f"unsupported baseline coverage mode: {coverage_mode}")
    selected = sorted(
        (dict(item) for item in evaluations),
        key=lambda item: (str(item.get("environment") or ""), str(item.get("subset") or "")),
    )
    if not selected:
        raise ValueError("at least one evaluation is required")
    if any(item.get("evaluator_rules_sha256") != EVALUATOR_RULES_SHA256 for item in selected):
        raise ValueError("evaluation rules hash mismatch")
    if any(str(item.get("manifest_sha256") or "") != manifest_sha256 for item in selected):
        raise ValueError("evaluation manifest hash mismatch")
    for item in selected:
        _validate_evaluation_for_freeze(item)
    keys = [
        (str(item.get("environment") or ""), str(item.get("subset") or ""))
        for item in selected
    ]
    if any(not environment for environment, _ in keys) or len(set(keys)) != len(keys):
        raise ValueError("baseline evaluations require unique environment/subset pairs")
    required_pairs = {(BASELINE_ENVIRONMENT, "fixed3")}
    if coverage_mode == "full":
        required_pairs.add((BASELINE_ENVIRONMENT, "fixed10"))
    if set(keys) != required_pairs:
        expected = "fixed3/fixed10" if coverage_mode == "full" else "fixed3 only"
        raise ValueError(f"baseline requires server_test {expected} evaluations")
    quality_subset = "fixed10" if coverage_mode == "full" else "fixed3"
    quality_selected = [item for item in selected if item.get("subset") == quality_subset]
    fixed3_selected = [item for item in selected if item.get("subset") == "fixed3"]
    quality_exclusions = [
        _normalize_excluded_cases(item.get("excluded_cases")) for item in quality_selected
    ]
    metrics = _metric_summary(list(quality_selected[0]["samples"]), [])
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "baseline_id": str(baseline_id),
        "coverage_mode": coverage_mode,
        "required_subsets": ["fixed3", "fixed10"] if coverage_mode == "full" else ["fixed3"],
        "waived_subsets": [] if coverage_mode == "full" else ["fixed10"],
        "manifest_sha256": manifest_sha256,
        "evaluator_version": EVALUATOR_VERSION,
        "evaluator_rules_sha256": EVALUATOR_RULES_SHA256,
        "unsupported_eval_version": UNSUPPORTED_EVAL_VERSION,
        "excluded_cases": quality_exclusions[0],
        "metrics": metrics,
        "quality": [
            {
                "environment": str(item.get("environment") or ""),
                "subset": str(item.get("subset") or ""),
                "selected_case_count": _int(item.get("selected_case_count")),
                "metrics": item.get("metrics"),
            }
            for item in quality_selected
        ],
        "performance": [
            {
                "environment": str(item.get("environment") or ""),
                "subset": str(item.get("subset") or ""),
                "selected_case_count": _int(item.get("selected_case_count")),
                "metrics": item.get("metrics"),
            }
            for item in selected
            if item.get("subset") == "fixed3"
        ],
        "evaluations": [
            {
                "environment": str(item.get("environment") or ""),
                "subset": str(item.get("subset") or ""),
                "selected_case_count": _int(item.get("selected_case_count")),
                "excluded_cases": _normalize_excluded_cases(item.get("excluded_cases")),
                "metrics": item.get("metrics"),
            }
            for item in selected
        ],
    }


def compare_evaluation_to_baseline(
    evaluation: Mapping[str, Any], baseline: Mapping[str, Any]
) -> dict[str, Any]:
    _validate_evaluation_for_freeze(evaluation)
    if baseline.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise ValueError("baseline schema_version mismatch")
    if evaluation.get("evaluator_rules_sha256") != baseline.get("evaluator_rules_sha256"):
        raise ValueError("baseline evaluator rules hash mismatch")
    if evaluation.get("manifest_sha256") != baseline.get("manifest_sha256"):
        raise ValueError("baseline manifest hash mismatch")
    current = evaluation.get("metrics")
    frozen = baseline.get("metrics")
    if not isinstance(current, Mapping) or not isinstance(frozen, Mapping):
        raise ValueError("baseline comparison requires metrics")

    invariants = {
        "failed_count": _int(current.get("failed_count")) == 0,
        "forbidden_phrase_hit_count": _int(current.get("forbidden_phrase_hit_count")) == 0,
        "state_contradiction_count": _int(current.get("state_contradiction_count")) == 0,
        "missing_workflow_run_id_count": _int(current.get("missing_workflow_run_id_count")) == 0,
        "identity_hash_complete": _int(current.get("identity_hash_complete_count"))
        == _int(current.get("completed_sample_count")),
        "metrics_complete": _int(current.get("metrics_complete_count"))
        == _int(current.get("completed_sample_count")),
        "snapshot_replayable": _int(current.get("replayable_snapshot_count"))
        == _int(current.get("completed_sample_count")),
        "source_exclusions": (
            _normalize_excluded_cases(evaluation.get("excluded_cases"))
            == _normalize_excluded_cases(baseline.get("excluded_cases"))
            if evaluation.get("subset") == "fixed10"
            else not _normalize_excluded_cases(evaluation.get("excluded_cases"))
        ),
    }
    quality_reference = frozen
    quality: dict[str, Any] = {"checked": False, "reason": "fixed3_is_performance_only"}
    if evaluation.get("subset") == "fixed10":
        matches = [
            item
            for item in baseline.get("quality", [])
            if isinstance(item, Mapping)
            and item.get("subset") == "fixed10"
            and item.get("environment") == evaluation.get("environment")
        ]
        if not matches or not isinstance(matches[0].get("metrics"), Mapping):
            quality = {"checked": False, "reason": "no_matching_fixed10_baseline"}
        else:
            quality_reference = matches[0]["metrics"]
            quality = {
                "checked": True,
                "sample_count": _int(current.get("completed_sample_count"))
                == _int(quality_reference.get("completed_sample_count")),
                "unsupported_fact_count": _int(current.get("unsupported_fact_count"))
                <= _int(quality_reference.get("unsupported_fact_count")),
                "deliverable_count": _int(current.get("deliverable_count"))
                >= _int(quality_reference.get("deliverable_count")),
                "needs_manual_review_count": _int(current.get("needs_manual_review_count"))
                <= _int(quality_reference.get("needs_manual_review_count")),
            }
    quality_deltas = {
        key: _int(current.get(key)) - _int(quality_reference.get(key))
        for key in (
            "deliverable_count",
            "needs_manual_review_count",
            "unsupported_fact_count",
            "forbidden_phrase_hit_count",
            "state_contradiction_count",
        )
    }
    performance: dict[str, Any] = {"checked": False, "reason": "no_matching_fixed3_baseline"}
    if evaluation.get("subset") == "fixed3":
        matches = [
            item
            for item in baseline.get("performance", [])
            if isinstance(item, Mapping)
            and item.get("subset") == "fixed3"
            and item.get("environment") == evaluation.get("environment")
        ]
        if matches:
            baseline_p95 = _float(
                ((matches[0].get("metrics") or {}).get("elapsed_seconds") or {}).get("p95")
            )
            current_p95 = _float((current.get("elapsed_seconds") or {}).get("p95"))
            limit = max(baseline_p95 * 1.30, baseline_p95 + 30.0)
            performance = {
                "checked": True,
                "reason": "within_limit" if current_p95 <= limit else "p95_regression",
                "baseline_p95": baseline_p95,
                "current_p95": current_p95,
                "limit": round(limit, 3),
                "passed": current_p95 <= limit,
            }
    performance_passed = (
        performance.get("checked") is True and performance.get("passed") is True
        if evaluation.get("subset") == "fixed3"
        else True
    )
    quality_passed = (
        quality.get("checked") is True
        and all(
            value is True
            for key, value in quality.items()
            if key not in {"checked", "reason"}
        )
        if evaluation.get("subset") == "fixed10"
        else True
    )
    return {
        "baseline_id": str(baseline.get("baseline_id") or ""),
        "passed": all(invariants.values()) and performance_passed and quality_passed,
        "invariants": invariants,
        "quality": quality,
        "quality_deltas": quality_deltas,
        "performance": performance,
    }


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return data
