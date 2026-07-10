from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "tests" / "fixtures" / "8099_regression_cases.json"
TERMINAL_STATUSES = {"finished", "failed", "needs_manual_review"}
WORD_FLAGS = (
    "word_export_available",
    "draft_word_export_available",
    "final_word_export_available",
)
WORD_LOCATORS = ("word_download_url", "download_url", "word_filename")
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


def load_cases(path: Path = DEFAULT_MANIFEST) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("fixed regression manifest must contain a non-empty list")
    cases: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in data:
        if not isinstance(raw, dict):
            raise ValueError("each fixed regression case must be an object")
        case = dict(raw)
        menu_code = str(case.get("menu_code") or "").strip()
        articleid = str(case.get("articleid") or "").strip()
        primary = case.get("primary")
        auxiliary = case.get("auxiliary")
        if not case.get("id") or not case.get("name") or not menu_code or not articleid:
            raise ValueError("fixed regression case is missing identity fields")
        if not isinstance(primary, list) or not primary or not isinstance(auxiliary, list):
            raise ValueError(f"fixed regression case {case['id']} has invalid materials")
        identity = (menu_code, articleid)
        if identity in seen:
            raise ValueError(f"duplicate fixed regression identity: {identity}")
        seen.add(identity)
        cases.append(case)
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


def forbidden_phrase_hits(text: str) -> list[str]:
    return [phrase for phrase in FORBIDDEN_PHRASES if phrase in text]


def build_render_probe_payload() -> dict[str, Any]:
    return {
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


def _docx_names(path: Path | None) -> set[str]:
    if path is None:
        raise ValueError("--report-dir is required for Word file observation")
    if not path.is_dir():
        raise ValueError(f"report directory does not exist: {path}")
    return {item.name for item in path.glob("*.docx") if item.is_file()}


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
    timeout_seconds: int,
    poll_seconds: float,
) -> dict[str, Any]:
    started = time.monotonic()
    before_docx = _docx_names(report_dir)
    prepare = _require_success(
        client.post(
            "/analysis/prepare",
            json={
                "primary_materials": case["primary"],
                "auxiliary_materials": case["auxiliary"],
                "force_refresh_attachments": False,
            },
            timeout=300,
        ),
        "analysis prepare",
    )
    expected_attachment_count = int(case.get("expected_attachment_count") or 0)
    if int(prepare.get("attachment_count") or 0) != expected_attachment_count:
        raise ValueError(
            f"attachment count mismatch: expected {expected_attachment_count}, got {prepare.get('attachment_count')}"
        )
    pack_id = str(prepare.get("pack_id") or "")
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

    report_payload = _require_success(
        client.get(f"/analysis/runs/{run_id}/report", timeout=120),
        "analysis report",
    )
    report_markdown = str(report_payload.get("report_markdown") or "").strip()
    if not report_markdown:
        raise ValueError("analysis report body is empty")

    render_response = client.post(
        "/report/render",
        json=build_render_probe_payload(),
        timeout=120,
    )
    render = _require_success(render_response, "report render")
    if not str(render.get("report_markdown") or "").strip():
        raise ValueError("report render returned an empty body")

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

    after_docx = _docx_names(report_dir)
    created_docx = sorted(after_docx - before_docx)
    endpoint_statuses = {name: response.status_code for name, response in endpoint_responses.items()}
    validate_disabled_word_contract(state, endpoint_statuses, created_docx)
    hits = forbidden_phrase_hits(report_markdown)
    return {
        "case_id": case["id"],
        "case_name": case["name"],
        "menu_code": case["menu_code"],
        "articleid": case["articleid"],
        "pack_id": pack_id,
        "run_id": run_id,
        "status": str(state.get("status") or state.get("run_status") or ""),
        "provider": str(state.get("provider") or ""),
        "workflow_run_id": str(state.get("workflow_run_id") or ""),
        "compact_pack_chars": int(state.get("compact_pack_chars") or 0),
        "deliverable": bool(state.get("deliverable")),
        "needs_manual_review": bool(state.get("needs_manual_review")),
        "word_export_available": bool(state.get("word_export_available")),
        "draft_word_export_available": bool(state.get("draft_word_export_available")),
        "final_word_export_available": bool(state.get("final_word_export_available")),
        "endpoint_statuses": endpoint_statuses,
        "created_docx": created_docx,
        "report_chars": len(report_markdown),
        "forbidden_phrase_hits": hits,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fixed 8099 Word-fuse regression set")
    parser.add_argument("--base-url", default="http://127.0.0.1:8099")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--case-timeout-seconds", type=int, default=1200)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    args = parser.parse_args()

    output_dir = args.output_dir or (
        Path(tempfile.gettempdir()) / f"8099-fixed-regression-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    cases = load_cases(args.manifest)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    with httpx.Client(base_url=args.base_url.rstrip("/"), follow_redirects=True) as client:
        health = _require_success(client.get("/health", timeout=30), "health")
        if health.get("word_export_enabled") is not False:
            raise ValueError("health does not report word_export_enabled=false")
        for index, case in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] {case['id']}", flush=True)
            try:
                result = run_case(
                    client,
                    case,
                    args.report_dir,
                    args.case_timeout_seconds,
                    args.poll_seconds,
                )
                results.append(result)
            except Exception as exc:  # noqa: BLE001
                failures.append({"case_id": str(case.get("id") or ""), "error": f"{type(exc).__name__}: {exc}"})

    summary = {
        "base_url": args.base_url,
        "manifest": str(args.manifest),
        "total": len(cases),
        "passed": len(results),
        "failed": len(failures),
        "results": results,
        "failures": failures,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
