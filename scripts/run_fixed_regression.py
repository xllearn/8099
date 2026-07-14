from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.formal_body import FormalBodyDocument  # noqa: E402
from app.formal_body_safety import scan_docx, scan_formal_body  # noqa: E402
from app.attachment_fetcher import (  # noqa: E402
    DEFAULT_ATTACHMENT_DOWNLOAD_BASE_URL,
    build_attachment_auth_headers,
)
from app.offline_quality_evaluator import (  # noqa: E402
    ARTIFACT_SCHEMA_VERSION,
    TIMING_FIELDS,
    compare_run_history,
    sha256_file,
    sha256_json,
    validate_sample_contract,
    verify_snapshot_replay,
)
from app.regression_manifest import (  # noqa: E402
    attachment_identity,
    build_manifest_contract,
    excluded_cases,
    load_manifest as load_regression_manifest,
    manifest_sha256,
    normalize_source_text,
    select_cases,
    verify_record_fingerprint,
)

DEFAULT_MANIFEST = ROOT / "tests" / "fixtures" / "8099_regression_cases.json"
TERMINAL_STATUSES = {"finished", "failed", "needs_manual_review"}
WORD_FLAGS = (
    "word_export_available",
    "draft_word_export_available",
    "final_word_export_available",
)
WORD_LOCATORS = (
    "word_download_url",
    "download_url",
    "word_filename",
    "word_file_path",
    "word_path",
)
WORD_METADATA = ("word_exported_at",)
WORD_ENDPOINTS = (
    "run_download",
    "report_export",
    "report_export_checked",
    "file_download",
)
FORBIDDEN_PHRASES = (
    "原文未披露",
    "需人工核验",
    "需人工复核",
    "证据不足",
    "无法确认",
    "请核验",
    "建议人工确认",
    "资料未显示",
    "未在原文中找到",
    "根据有限信息",
    "以上内容需复核",
    "待确认",
    "待核实",
)

def _text_only_attachment_hash(attachment: Mapping[str, Any]) -> dict[str, Any]:
    parsed_text = normalize_source_text(attachment.get("parsed_text"))
    if not parsed_text.strip():
        raise ValueError("attachment content requires raw bytes or non-empty parsed text")
    content = b"TEXT_ONLY\n" + parsed_text.encode("utf-8")
    return {
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_hash_source": "text_only",
        "content_length": len(parsed_text.encode("utf-8")),
        "verifier": "normalized_text_sha256_v1",
    }


def _attachment_content_hash(attachment: Mapping[str, Any]) -> dict[str, Any]:
    identity = tuple(
        str(attachment.get(field) or "")
        for field in ("articleattid", "filename", "filesize", "uploadtime")
    )
    articleattid = identity[0].strip()
    if not articleattid:
        return _text_only_attachment_hash(attachment)

    base_url = (
        os.getenv("ATTACHMENT_DOWNLOAD_BASE_URL")
        or DEFAULT_ATTACHMENT_DOWNLOAD_BASE_URL
    ).strip()
    url = (
        base_url.format(articleattid=articleattid)
        if "{articleattid}" in base_url
        else f"{base_url}{articleattid}"
    )
    headers, _ = build_attachment_auth_headers()
    timeout = float((os.getenv("ATTACHMENT_REQUEST_TIMEOUT") or "60").strip() or 60)
    try:
        max_download_mb = float(
            (os.getenv("ATTACHMENT_MAX_DOWNLOAD_MB") or "50").strip() or 50
        )
    except ValueError:
        max_download_mb = 50.0
    max_download_bytes = max(1, int(max_download_mb * 1024 * 1024))
    try:
        digest = hashlib.sha256()
        content_length = 0
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
            trust_env=False,
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" in content_type:
                    raise ValueError("attachment endpoint returned HTML")
                for chunk in response.iter_bytes():
                    digest.update(chunk)
                    content_length += len(chunk)
                    if content_length > max_download_bytes:
                        raise ValueError("attachment exceeds configured download limit")
        if content_length <= 0:
            raise ValueError("attachment endpoint returned empty content")
        result = {
            "content_sha256": digest.hexdigest(),
            "content_hash_source": "raw_bytes",
            "content_length": content_length,
            "verifier": "stream_sha256_v1",
        }
    except (httpx.HTTPError, OSError, ValueError):
        result = _text_only_attachment_hash(attachment)
    return dict(result)


def _attachment_content_verifications(
    record: Mapping[str, Any],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    verifications: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in record.get("attachments", []):
        if not isinstance(item, Mapping):
            continue
        identity = attachment_identity(item)
        if identity in verifications:
            raise ValueError("record contains duplicate attachment identities")
        verifications[identity] = _attachment_content_hash(item)
    return verifications


def load_cases(
    path: Path = DEFAULT_MANIFEST, subset: str = "fixed3"
) -> list[dict[str, Any]]:
    manifest = load_regression_manifest(path)
    cases = select_cases(manifest, subset)
    for case in cases:
        request = case["replay"]["request"]
        case["primary"] = list(request["primary_materials"])
        case["auxiliary"] = list(request["auxiliary_materials"])
    return cases


def validate_disabled_word_contract(
    run_payload: dict[str, Any],
    endpoint_statuses: dict[str, int],
    created_docx: list[str],
) -> None:
    enabled_flags = [field for field in WORD_FLAGS if run_payload.get(field) is not False]
    if enabled_flags:
        raise ValueError(f"Word flags are not forced false: {', '.join(enabled_flags)}")
    exposed_locators = [field for field in WORD_LOCATORS if str(run_payload.get(field) or "").strip()]
    if exposed_locators:
        raise ValueError(f"Word download locator is exposed: {', '.join(exposed_locators)}")
    exposed_metadata = [field for field in WORD_METADATA if str(run_payload.get(field) or "").strip()]
    if exposed_metadata or run_payload.get("word_generated") not in {None, False}:
        raise ValueError("Word generation metadata is exposed while export is disabled")
    bad_statuses = {
        name: endpoint_statuses.get(name)
        for name in WORD_ENDPOINTS
        if endpoint_statuses.get(name) != 503
    }
    if bad_statuses:
        raise ValueError(f"Word endpoint fuse status mismatch: {bad_statuses}")
    if created_docx:
        raise ValueError(f"Word files were created while export was disabled: {created_docx}")


def validate_expected_word_export(actual: bool, expected: str) -> None:
    if expected == "enabled" and not actual:
        raise ValueError("P0.2-b1.5 requires word_export_enabled=true")
    if expected == "disabled" and actual:
        raise ValueError("Expected Word export to remain disabled")


def validate_enabled_word_contract(
    run_payload: dict[str, Any],
    endpoint_statuses: dict[str, int],
    created_docx: list[str],
    word_scan_hits: dict[str, list[str]],
    staging_artifacts: list[str],
    formal_body_hits: list[str] | None = None,
) -> None:
    if formal_body_hits:
        raise ValueError(f"Forbidden phrases found in formal body: {formal_body_hits}")
    if run_payload.get("word_export_available") is not True:
        raise ValueError("Word export is not available for a safe formal body")
    if run_payload.get("draft_word_export_available") is not True:
        raise ValueError("Draft Word must be available for every safe formal body")

    deliverable = run_payload.get("deliverable") is True
    needs_manual_review = run_payload.get("needs_manual_review") is True
    final_available = run_payload.get("final_word_export_available") is True
    if deliverable:
        if needs_manual_review or not final_available:
            raise ValueError("Deliverable Word state is contradictory")
    elif needs_manual_review:
        if final_available:
            raise ValueError("Manual-review draft exposed final Word")
    else:
        raise ValueError("Safe Word state is neither deliverable nor manual-review draft")

    bad_statuses = {
        name: endpoint_statuses.get(name)
        for name in WORD_ENDPOINTS
        if endpoint_statuses.get(name) != 200
    }
    if bad_statuses:
        raise ValueError(f"Enabled Word endpoint status mismatch: {bad_statuses}")
    if not created_docx:
        raise ValueError("No Word files were created while export was enabled")
    unscanned = [name for name in created_docx if name not in word_scan_hits]
    if unscanned:
        raise ValueError(f"Created Word files were not scanned: {unscanned}")
    unsafe = {name: hits for name, hits in word_scan_hits.items() if hits}
    if unsafe:
        raise ValueError(f"Forbidden phrases found in Word outputs: {unsafe}")
    if staging_artifacts:
        raise ValueError(f"Word staging artifacts remain: {staging_artifacts}")


def forbidden_phrase_hits(text: str) -> list[str]:
    result = scan_formal_body(FormalBodyDocument(markdown=text))
    return list(dict.fromkeys(hit.phrase for hit in result.hits))


def build_render_probe_payload() -> dict[str, Any]:
    payload = {
        "report_ir": {
            "title": "Word publication fuse render probe",
            "suggested_filename": "word-publication-fuse-render-probe",
            "lead_paragraphs": ["This structured report verifies that rendering remains available."],
            "sections": [
                {
                    "heading": "Render status",
                    "paragraphs": ["The render endpoint remains independent from public Word download access."],
                    "tables": [],
                }
            ],
        },
        "strict_quality": False,
    }
    return payload


def build_formal_export_payload(
    report_markdown: str,
    report_title: str,
    evidence_pack: dict[str, Any],
) -> dict[str, Any]:
    paragraphs = [line.strip() for line in str(report_markdown or "").splitlines() if line.strip()]
    return {
        "report_ir": {
            "title": str(report_title or "分析报告").strip() or "分析报告",
            "lead_paragraphs": paragraphs,
            "sections": [],
            "enterprise_tips": [],
        },
        "evidence_text": json.dumps(evidence_pack, ensure_ascii=False),
        "strict_quality": False,
    }


def _docx_names(path: Path | None) -> set[str]:
    if path is None:
        raise ValueError("--report-dir is required for Word file observation")
    if not path.is_dir():
        raise ValueError(f"report directory does not exist: {path}")
    return {item.name for item in path.glob("*.docx") if item.is_file()}


def _word_staging_artifacts(path: Path | None) -> list[str]:
    if path is None or not path.is_dir():
        return []
    return sorted(item.name for item in path.glob(".word-staging-*") if item.exists())


def _save_and_scan_docx(content: bytes, path: Path) -> list[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return list(dict.fromkeys(hit.phrase for hit in scan_docx(path)))


def _download_path(download_url: str) -> str:
    path = urlsplit(str(download_url or "")).path
    if not path.startswith("/download/"):
        raise ValueError("Word export did not return a valid download URL")
    return path


def _json_body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise ValueError(f"{response.request.method} {response.request.url.path} returned non-JSON") from exc
    if not isinstance(body, dict):
        raise ValueError(f"{response.request.method} {response.request.url.path} returned non-object JSON")
    return body


def _require_success(response: httpx.Response, operation: str) -> dict[str, Any]:
    if response.status_code != 200:
        raise ValueError(f"{operation} failed with HTTP {response.status_code}: {response.text[:300]}")
    body = _json_body(response)
    if body.get("success") is False:
        raise ValueError(f"{operation} returned success=false: {body}")
    return body


def _write_json_snapshot(
    root: Path, relative_path: Path, value: Mapping[str, Any] | list[Any]
) -> dict[str, str]:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"path": relative_path.as_posix(), "sha256": sha256_file(path)}


def _write_json_atomically(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _capture_and_verify_materials(
    client: httpx.Client,
    case: Mapping[str, Any],
    output_root: Path,
    sample_dir: Path,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    captured: list[dict[str, Any]] = []
    for expected in case["materials"]:
        menu_code = str(expected["menu_code"])
        articleid = str(expected["articleid"])
        detail = _require_success(
            client.get(f"/records/{menu_code}/{articleid}", timeout=120),
            "record detail",
        )
        verifications = _attachment_content_verifications(detail)
        actual = verify_record_fingerprint(
            detail,
            expected,
            verified_attachment_content=verifications,
        )
        captured.append(
            {
                "role": str(expected["role"]),
                "expected": dict(expected),
                "actual": actual,
                "record": detail,
                "attachment_content_verification": [
                    {"identity": list(identity), **verification}
                    for identity, verification in sorted(verifications.items())
                ],
            }
        )
    reference = _write_json_snapshot(
        output_root,
        sample_dir / "materials.json",
        {"case_id": case["id"], "materials": captured},
    )
    return reference, captured


def _quality_metrics(state: Mapping[str, Any]) -> tuple[str, bool | None, int]:
    quality_gate = state.get("quality_gate")
    if not isinstance(quality_gate, Mapping):
        quality_gate = {}
    quality_check = state.get("quality_check")
    if not isinstance(quality_check, Mapping):
        quality_check = {}
    quality_passed = state.get("quality_passed")
    if quality_passed not in (True, False):
        quality_passed = quality_check.get("passed")
    if quality_passed not in (True, False):
        quality_passed = None
    if state.get("deliverable") is True and quality_passed is True:
        quality_status = "passed"
    elif state.get("needs_manual_review") is True:
        quality_status = "needs_manual_review"
    else:
        quality_status = "failed"
    return (
        quality_status,
        quality_passed,
        int(quality_gate.get("unsupported_fact_count") or 0),
    )


def _history_for_run(
    client: httpx.Client,
    run_id: str,
    case: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    detail = _require_success(
        client.get(f"/analysis/history/{run_id}", timeout=60),
        "analysis history detail",
    )
    item = detail.get("item")
    if not isinstance(item, dict):
        raise ValueError("analysis history detail did not return item")
    listing = _require_success(
        client.get(
            "/analysis/history",
            params={
                "menu_code": case["menu_code"],
                "articleid": case["articleid"],
                "page": 1,
                "page_size": 200,
            },
            timeout=60,
        ),
        "analysis history list",
    )
    items = listing.get("items")
    if not isinstance(items, list) or not any(
        isinstance(candidate, dict) and candidate.get("run_id") == run_id
        for candidate in items
    ):
        raise ValueError("analysis history list does not contain the completed run")
    return item, listing


def _assert_disabled_response(response: httpx.Response, operation: str) -> None:
    if response.status_code != 503:
        raise ValueError(f"{operation} returned HTTP {response.status_code}, expected 503")
    body = _json_body(response)
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    if error.get("code") != "WORD_EXPORT_DISABLED":
        raise ValueError(f"{operation} did not return WORD_EXPORT_DISABLED")
    if "/download/" in response.text:
        raise ValueError(f"{operation} exposed a download URL while disabled")


def run_case(
    client: httpx.Client,
    case: dict[str, Any],
    report_dir: Path | None,
    output_root: Path,
    artifact_dir: Path,
    word_export_enabled: bool,
    timeout_seconds: int,
    poll_seconds: float,
    attempt: int = 1,
) -> dict[str, Any]:
    started = time.monotonic()
    snapshots: dict[str, dict[str, str]] = {}
    material_reference, captured_materials = _capture_and_verify_materials(
        client,
        case,
        output_root,
        artifact_dir.relative_to(output_root),
    )
    snapshots["materials"] = material_reference
    before_docx = _docx_names(report_dir)
    replay_request = dict(case["replay"]["request"])
    prepare_started = time.monotonic()
    prepare = _require_success(
        client.post(
            "/analysis/prepare",
            json=replay_request,
            timeout=300,
        ),
        "analysis prepare",
    )
    prepare_ms = int((time.monotonic() - prepare_started) * 1000)
    sample_relative = artifact_dir.relative_to(output_root)
    snapshots["prepare"] = _write_json_snapshot(
        output_root, sample_relative / "prepare.json", prepare
    )
    expected_attachment_count = int(case.get("expected_attachment_count") or 0)
    if int(prepare.get("attachment_count") or 0) != expected_attachment_count:
        raise ValueError(
            f"attachment count mismatch: expected {expected_attachment_count}, got {prepare.get('attachment_count')}"
        )
    pack_id = str(prepare.get("pack_id") or "")
    compact_pack = _require_success(
        client.get(f"/analysis/packs/{pack_id}", timeout=120),
        "analysis compact pack",
    )
    snapshots["compact_pack"] = _write_json_snapshot(
        output_root,
        sample_relative / "compact-pack.json",
        compact_pack,
    )
    analysis_started = time.monotonic()
    run_start = _require_success(
        client.post("/analysis/run", json={"pack_id": pack_id}, timeout=60),
        "analysis run",
    )
    run_id = str(run_start.get("run_id") or "")
    if not run_id:
        raise ValueError("analysis run did not return run_id")

    deadline = time.monotonic() + timeout_seconds
    state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = _require_success(client.get(f"/analysis/runs/{run_id}", timeout=60), "analysis status")
        if str(state.get("status") or state.get("run_status") or "") in TERMINAL_STATUSES:
            break
        time.sleep(poll_seconds)
    else:
        raise TimeoutError(f"analysis run timed out after {timeout_seconds}s: {run_id}")
    analysis_elapsed_ms = int((time.monotonic() - analysis_started) * 1000)

    report_payload = _require_success(
        client.get(f"/analysis/runs/{run_id}/report", timeout=120),
        "analysis report",
    )
    diagnostics = _require_success(
        client.get(f"/analysis/runs/{run_id}/diagnostics", timeout=120),
        "analysis diagnostics",
    )
    snapshots["run"] = _write_json_snapshot(
        output_root, sample_relative / "run.json", state
    )
    snapshots["report"] = _write_json_snapshot(
        output_root, sample_relative / "report.json", report_payload
    )
    snapshots["diagnostics"] = _write_json_snapshot(
        output_root, sample_relative / "diagnostics.json", diagnostics
    )
    report_markdown = str(report_payload.get("report_markdown") or "").strip()
    if not report_markdown:
        raise ValueError("analysis report body is empty")

    word_started = time.monotonic()
    render_response = client.post(
        "/report/render",
        json=build_render_probe_payload(),
        timeout=120,
    )
    render = _require_success(render_response, "report render")
    if not str(render.get("report_markdown") or "").strip():
        raise ValueError("report render returned an empty body")

    word_scan_hits: dict[str, list[str]] = {}
    if word_export_enabled:
        export_payload = build_formal_export_payload(
            report_markdown,
            str(state.get("report_title") or report_payload.get("report_title") or "分析报告"),
            prepare.get("evidence_pack") if isinstance(prepare.get("evidence_pack"), dict) else {},
        )
        run_download = client.get(f"/analysis/runs/{run_id}/download", timeout=120)
        export_response = client.post(
            "/report/export",
            json=export_payload,
            timeout=120,
        )
        checked_response = client.post(
            "/report/export_checked",
            json={**export_payload, "qa_status": "pass"},
            timeout=120,
        )
        export_body = _require_success(export_response, "report export")
        checked_body = _require_success(checked_response, "checked report export")
        export_download = client.get(_download_path(str(export_body.get("download_url") or "")), timeout=120)
        checked_download = client.get(_download_path(str(checked_body.get("download_url") or "")), timeout=120)
        downloads = {
            "run.docx": run_download,
            "export.docx": export_download,
            "checked.docx": checked_download,
        }
        for name, response in downloads.items():
            if response.status_code != 200:
                raise ValueError(f"{name} download failed with HTTP {response.status_code}: {response.text[:300]}")
            downloaded_path = artifact_dir / name
            word_scan_hits[name] = _save_and_scan_docx(response.content, downloaded_path)
            snapshots[f"word_{Path(name).stem}"] = {
                "path": downloaded_path.relative_to(output_root).as_posix(),
                "sha256": sha256_file(downloaded_path),
            }
        endpoint_responses = {
            "run_download": run_download,
            "report_export": export_response,
            "report_export_checked": checked_response,
            "file_download": export_download if checked_download.status_code == 200 else checked_download,
        }
    else:
        endpoint_responses = {
            "run_download": client.get(f"/analysis/runs/{run_id}/download", timeout=60),
            "report_export": client.post(
                "/report/export",
                json={"markdown": report_markdown, "strict_quality": False},
                timeout=60,
            ),
            "report_export_checked": client.post(
                "/report/export_checked",
                json={
                    "markdown": report_markdown,
                    "qa_status": "pass",
                    "strict_quality": False,
                },
                timeout=60,
            ),
            "file_download": client.get("/download/word-fuse-probe.docx", timeout=60),
        }
        for name, response in endpoint_responses.items():
            _assert_disabled_response(response, name)
    word_export_ms = int((time.monotonic() - word_started) * 1000)

    after_docx = _docx_names(report_dir)
    created_docx = sorted(after_docx - before_docx)
    if word_export_enabled and report_dir is not None:
        for name in created_docx:
            word_scan_hits[name] = list(
                dict.fromkeys(hit.phrase for hit in scan_docx(report_dir / name))
            )
    endpoint_statuses = {name: response.status_code for name, response in endpoint_responses.items()}
    staging_artifacts = _word_staging_artifacts(report_dir)
    formal_scan = scan_formal_body(
        FormalBodyDocument(
            markdown=report_markdown,
            report_ir=state.get("report_ir") if isinstance(state.get("report_ir"), dict) else None,
        )
    )
    hits = list(dict.fromkeys(hit.phrase for hit in formal_scan.hits))
    if word_export_enabled:
        validate_enabled_word_contract(
            state,
            endpoint_statuses,
            created_docx,
            word_scan_hits,
            staging_artifacts,
            formal_body_hits=hits,
        )
    else:
        validate_disabled_word_contract(state, endpoint_statuses, created_docx)
    history, history_listing = _history_for_run(client, run_id, case)
    snapshots["history"] = _write_json_snapshot(
        output_root, sample_relative / "history.json", history
    )
    snapshots["history_query"] = _write_json_snapshot(
        output_root, sample_relative / "history-query.json", history_listing
    )
    history_consistency = compare_run_history(
        state,
        history,
        word_downloaded=endpoint_statuses.get("run_download") == 200,
    )
    if not history_consistency["consistent"]:
        raise ValueError(
            f"run/history state mismatch: {history_consistency['mismatches']}"
        )
    quality_status, quality_passed, unsupported_fact_count = _quality_metrics(state)
    evidence_pack = (
        prepare.get("evidence_pack")
        if isinstance(prepare.get("evidence_pack"), dict)
        else {}
    )
    provider = str(state.get("provider") or "").strip()
    workflow_run_id = str(state.get("workflow_run_id") or "").strip()
    provider_run_id = str(state.get("provider_run_id") or workflow_run_id).strip()
    compact_pack_chars = int(state.get("compact_pack_chars") or 0)
    input_strategy = str(state.get("input_strategy") or "").strip()
    state_timings = state.get("timings") if isinstance(state.get("timings"), Mapping) else {}
    total_ms = int((time.monotonic() - started) * 1000)
    timings = {
        "prepare_ms": prepare_ms,
        "generation_ms": int(state_timings.get("generation_ms") or analysis_elapsed_ms),
        "local_quality_gate_ms": int(state_timings.get("local_quality_gate_ms") or 0),
        "repair_ms": int(state_timings.get("repair_ms") or 0),
        "export_check_ms": int(state_timings.get("export_check_ms") or 0),
        "word_export_ms": word_export_ms,
        "total_ms": total_ms,
    }
    timings = {field: max(0, int(timings.get(field) or 0)) for field in TIMING_FIELDS}
    metrics_complete = bool(
        provider
        and str(state.get("status") or state.get("run_status") or "").strip()
        and quality_status
        and compact_pack_chars > 0
        and input_strategy
        and evidence_pack
        and (provider != "dify" or workflow_run_id)
        and timings["total_ms"] > 0
    )
    if not metrics_complete:
        raise ValueError("run result metrics are incomplete")
    report_ir = report_payload.get("report_ir")
    if not isinstance(report_ir, Mapping):
        report_ir = state.get("report_ir")
    report_section_count = (
        len(report_ir.get("sections") or [])
        if isinstance(report_ir, Mapping) and isinstance(report_ir.get("sections"), list)
        else sum(1 for line in report_markdown.splitlines() if line.lstrip().startswith("#"))
    )
    snapshots["word_contract"] = _write_json_snapshot(
        output_root,
        sample_relative / "word-contract.json",
        {
            "word_export_enabled": word_export_enabled,
            "endpoint_statuses": endpoint_statuses,
            "created_docx": created_docx,
            "staging_artifacts": staging_artifacts,
            "download_locators": {
                field: state.get(field)
                for field in WORD_LOCATORS
            },
            "timings": timings,
        },
    )
    result = {
        "sample_id": f"{case['id']}-attempt-{attempt}",
        "case_id": case["id"],
        "attempt": attempt,
        "passed": True,
        "case_name": case["name"],
        "menu_code": case["menu_code"],
        "articleid": case["articleid"],
        "pack_id": pack_id,
        "run_id": run_id,
        "status": str(state.get("status") or state.get("run_status") or ""),
        "provider": provider,
        "workflow_run_id": workflow_run_id,
        "provider_run_id": provider_run_id,
        "compact_pack_chars": compact_pack_chars,
        "input_strategy": input_strategy,
        "evidence_pack_sha256": sha256_json(evidence_pack),
        "timings": timings,
        "quality_status": quality_status,
        "quality_passed": quality_passed,
        "unsupported_fact_count": unsupported_fact_count,
        "primary_failure_code": state.get("primary_failure_code") or "",
        "secondary_failure_codes": state.get("secondary_failure_codes") or [],
        "quality_failure_codes": state.get("quality_failure_codes") or [],
        "generation_failure_codes": state.get("generation_failure_codes") or [],
        "deliverable": bool(state.get("deliverable")),
        "needs_manual_review": bool(state.get("needs_manual_review")),
        "word_export_available": bool(state.get("word_export_available")),
        "draft_word_export_available": bool(state.get("draft_word_export_available")),
        "final_word_export_available": bool(state.get("final_word_export_available")),
        "word_download_available": bool(history.get("word_download_available")),
        "word_generated": bool(history.get("word_generated")),
        "endpoint_statuses": endpoint_statuses,
        "created_docx": created_docx,
        "word_scan_hits": word_scan_hits,
        "staging_artifacts": staging_artifacts,
        "report_chars": len(report_markdown),
        "report_section_count": report_section_count,
        "forbidden_phrase_hits": hits,
        "history_consistency": history_consistency,
        "identity_hash_complete": len(captured_materials) == len(case["materials"]),
        "material_bindings": sorted(
            (
                {
                    "role": str(item.get("role") or ""),
                    "menu_code": str((item.get("actual") or {}).get("menu_code") or ""),
                    "articleid": str((item.get("actual") or {}).get("articleid") or ""),
                    "record_sha256": str(
                        (item.get("actual") or {}).get("record_sha256") or ""
                    ),
                }
                for item in captured_materials
            ),
            key=lambda item: (
                item["role"],
                item["menu_code"],
                item["articleid"],
            ),
        ),
        "metrics_complete": metrics_complete,
        "snapshot_replayable": True,
        "snapshots": snapshots,
        "elapsed_seconds": round(total_ms / 1000, 3),
    }
    validate_sample_contract(result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the fixed 8099 Word-fuse regression set")
    parser.add_argument("--base-url", default="http://127.0.0.1:8099")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--subset", choices=("fixed3", "fixed10", "all"), default="fixed3")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--stage", default="manual")
    parser.add_argument(
        "--environment",
        choices=("server_test",),
        default="server_test",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--case-timeout-seconds", type=int, default=1200)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument(
        "--expect-word-export",
        choices=("enabled", "disabled", "auto"),
        default="enabled",
    )
    return parser


def validate_run_matrix(stage: str, subset: str, repeat: int) -> None:
    if repeat < 1:
        raise ValueError("--repeat must be at least 1")
    if str(stage).strip().upper() != "S0":
        return
    expected_repeat = {"fixed3": 3, "fixed10": 1}.get(subset)
    if expected_repeat is None or repeat != expected_repeat:
        raise ValueError("S0 regression matrix requires fixed3 x3 or fixed10 x1")


def _case_identity(case: Mapping[str, Any]) -> dict[str, str]:
    return {
        "case_id": str(case.get("id") or ""),
        "menu_code": str(case.get("menu_code") or ""),
        "articleid": str(case.get("articleid") or ""),
    }


def _declared_cases(manifest: Mapping[str, Any], subset: str) -> list[dict[str, str]]:
    by_id = {str(case.get("id") or ""): case for case in manifest.get("cases", [])}
    if subset == "all":
        selected_ids = list(by_id)
    else:
        selected_ids = [str(case_id) for case_id in manifest["subsets"][subset]]
    return [_case_identity(by_id[case_id]) for case_id in selected_ids]


def main() -> int:
    args = build_parser().parse_args()
    validate_run_matrix(args.stage, args.subset, args.repeat)

    output_dir = args.output_dir or (
        Path(tempfile.gettempdir())
        / f"8099-{args.subset}-regression-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    manifest = load_regression_manifest(args.manifest)
    manifest_contract = build_manifest_contract(manifest)
    cases = load_cases(args.manifest, args.subset)
    exclusions = excluded_cases(manifest, args.subset)
    declared_cases = _declared_cases(manifest, args.subset)
    selected_cases = [_case_identity(case) for case in cases]
    frozen_manifest_sha256 = manifest_sha256(manifest_contract)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    with httpx.Client(base_url=args.base_url.rstrip("/"), follow_redirects=True) as client:
        health = _require_success(client.get("/health", timeout=30), "health")
        word_export_enabled = health.get("word_export_enabled") is True
        validate_expected_word_export(word_export_enabled, args.expect_word_export)
        expected_samples = len(cases) * args.repeat
        sample_index = 0
        for attempt in range(1, args.repeat + 1):
            for case in cases:
                sample_index += 1
                print(
                    f"[{sample_index}/{expected_samples}] {case['id']} attempt={attempt}",
                    flush=True,
                )
                try:
                    result = run_case(
                        client,
                        case,
                        args.report_dir,
                        output_dir,
                        output_dir / "cases" / str(case["id"]) / f"attempt-{attempt}",
                        word_export_enabled,
                        args.case_timeout_seconds,
                        args.poll_seconds,
                        attempt,
                    )
                    results.append(result)
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        {
                            "sample_id": f"{case['id']}-attempt-{attempt}",
                            "case_id": str(case.get("id") or ""),
                            "attempt": attempt,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

    summary = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "base_url": args.base_url,
        "manifest": str(args.manifest),
        "manifest_version": manifest["manifest_version"],
        "manifest_sha256": frozen_manifest_sha256,
        "manifest_contract": manifest_contract,
        "stage": args.stage,
        "environment": args.environment,
        "subset": args.subset,
        "repeat": args.repeat,
        "declared_case_count": len(declared_cases),
        "selected_case_count": len(cases),
        "declared_cases": declared_cases,
        "selected_cases": selected_cases,
        "excluded_cases": exclusions,
        "started_at": started_at,
        "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "total": len(cases) * args.repeat,
        "passed": len(results),
        "failed": len(failures),
        "word_export_enabled": word_export_enabled,
        "samples": results,
        "failures": failures,
    }
    summary["snapshot_verification"] = verify_snapshot_replay(output_dir, summary)
    _write_json_atomically(output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
