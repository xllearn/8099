from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.compact_pack import canonical_sha256  # noqa: E402
from app.evidence_regression import evaluate_evidence_artifact  # noqa: E402
from app.regression_manifest import (  # noqa: E402
    attachment_identity,
    excluded_cases,
    load_manifest,
    select_cases,
    verify_record_fingerprint,
)
from scripts.run_fixed_regression import _attachment_content_verifications  # noqa: E402


DEFAULT_MANIFEST = ROOT / "tests" / "fixtures" / "8099_regression_cases.json"
ARTIFACT_SCHEMA_VERSION = "8099.s1-offline-evidence-run/v1"


class SourceAttachmentUnavailable(ValueError):
    pass


def _response_json(response: Any, operation: str) -> dict[str, Any]:
    if int(response.status_code) != 200:
        raise ValueError(f"{operation} failed with HTTP {response.status_code}: {str(response.text)[:300]}")
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError(f"{operation} returned non-object JSON")
    if body.get("success") is False:
        raise ValueError(f"{operation} returned success=false")
    return body


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_snapshot(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _json_bytes(value)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"artifact snapshot is not an object: {path.name}")
    return value


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_subset(path: Path = DEFAULT_MANIFEST, subset: str = "fixed10") -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    manifest = load_manifest(path)
    cases = select_cases(manifest, subset)
    exclusions = excluded_cases(manifest, subset)
    declared = list(manifest["cases"]) if subset == "all" else list(manifest["subsets"][subset])
    return cases, exclusions, len(declared)


def filter_cases(cases: list[dict[str, Any]], case_ids: list[str] | None) -> list[dict[str, Any]]:
    requested = [str(case_id).strip() for case_id in list(case_ids or []) if str(case_id).strip()]
    if not requested:
        return list(cases)
    by_id = {str(case.get("id") or ""): case for case in cases}
    missing = [case_id for case_id in requested if case_id not in by_id]
    if missing:
        raise ValueError(f"requested cases are not available in the selected subset: {', '.join(missing)}")
    return [by_id[case_id] for case_id in dict.fromkeys(requested)]


def execution_scope_status(
    *,
    subset: str,
    requested_case_ids: list[str],
    declared_count: int,
    selected_count: int,
    attempted_count: int,
    executed_count: int,
    failure_count: int,
) -> dict[str, Any]:
    filtered = bool([value for value in requested_case_ids if str(value).strip()])
    subset_complete = not filtered and attempted_count == selected_count
    execution_passed = (
        attempted_count == selected_count
        and int(executed_count) > 0
        and int(failure_count) == 0
    )
    return {
        "execution_scope": "case_filter" if filtered else "full_subset",
        "requested_subset": subset,
        "gate_subset": None if filtered else subset,
        "declared_case_count": int(declared_count),
        "selected_case_count": int(selected_count),
        "attempted_case_count": int(attempted_count),
        "subset_complete": subset_complete,
        "execution_passed": execution_passed,
        "gate_passed": subset_complete and execution_passed,
    }


def _manifest_attachment_key(attachment: Mapping[str, Any]) -> tuple[str, str]:
    identity = attachment_identity(attachment)
    return identity[0], identity[1]


def _capture_materials(client: Any, case: Mapping[str, Any]) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    for expected in list(case.get("materials") or []):
        menu_code = str(expected.get("menu_code") or "")
        articleid = str(expected.get("articleid") or "")
        record = _response_json(
            client.get(f"/records/{menu_code}/{articleid}", timeout=120),
            "record detail",
        )
        expected_count = int(expected.get("attachment_count") or 0)
        actual_attachments = [item for item in list(record.get("attachments") or []) if isinstance(item, Mapping)]
        expected_attachments = [item for item in list(expected.get("attachments") or []) if isinstance(item, Mapping)]
        actual_identities = {_manifest_attachment_key(item) for item in actual_attachments}
        expected_identities = {_manifest_attachment_key(item) for item in expected_attachments}
        if len(actual_identities) != len(actual_attachments) or len(expected_identities) != len(expected_attachments):
            raise ValueError(f"{menu_code}:{articleid} contains duplicate manifest attachment identities")
        missing_identities = expected_identities - actual_identities
        extra_identities = actual_identities - expected_identities
        if missing_identities or len(actual_attachments) < expected_count:
            raise SourceAttachmentUnavailable(
                f"{menu_code}:{articleid} expected attachment source is unavailable"
            )
        if extra_identities or len(actual_attachments) != expected_count:
            raise ValueError(f"{menu_code}:{articleid} contains attachments outside the fixed manifest")
        try:
            verifications = _attachment_content_verifications(record)
        except ValueError as exc:
            if "attachment content requires raw bytes or non-empty parsed text" in str(exc):
                raise SourceAttachmentUnavailable(f"{menu_code}:{articleid} source attachment unavailable") from exc
            raise
        if any(
            str(item.get("content_hash_source") or "") != "raw_bytes"
            for item in verifications.values()
            if isinstance(item, Mapping)
        ):
            raise SourceAttachmentUnavailable(
                f"{menu_code}:{articleid} source attachment raw bytes unavailable"
            )
        actual = verify_record_fingerprint(
            record,
            expected,
            verified_attachment_content=verifications,
        )
        captured.append(
            {
                "role": str(expected.get("role") or ""),
                "menu_code": menu_code,
                "articleid": articleid,
                "record_sha256": str(actual.get("record_sha256") or ""),
                "body_sha256": str(actual.get("body_sha256") or ""),
                "attachments_sha256": str(actual.get("attachments_sha256") or ""),
                "attachment_count": int(actual.get("attachment_count") or 0),
                "attachment_content_sha256": sorted(
                    str(item.get("content_sha256") or "")
                    for item in verifications.values()
                ),
            }
        )
    return captured


def run_offline_case(client: Any, case: Mapping[str, Any], artifact_dir: Path) -> dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=False)
    materials = _capture_materials(client, case)
    replay = case.get("replay") if isinstance(case.get("replay"), Mapping) else {}
    if replay.get("method") != "POST" or replay.get("path") != "/analysis/prepare":
        raise ValueError("offline evidence replay must use POST /analysis/prepare")
    prepare = _response_json(
        client.post("/analysis/prepare", json=dict(replay.get("request") or {}), timeout=300),
        "analysis prepare",
    )
    pack_id = str(prepare.get("pack_id") or "")
    full_pack = prepare.get("evidence_pack")
    if not pack_id or not isinstance(full_pack, dict):
        raise ValueError("analysis prepare did not return pack_id and evidence_pack")
    compact_pack = _response_json(
        client.get(f"/analysis/packs/{pack_id}", timeout=180),
        "analysis compact pack",
    )
    evaluation = evaluate_evidence_artifact(full_pack, compact_pack)
    full_hash = _write_snapshot(artifact_dir / "full-pack.json", full_pack)
    compact_hash = _write_snapshot(artifact_dir / "compact-pack.json", compact_pack)
    artifact: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "case_id": str(case.get("id") or ""),
        "menu_code": str(case.get("menu_code") or ""),
        "articleid": str(case.get("articleid") or ""),
        "pack_id": pack_id,
        "materials": materials,
        "snapshots": {
            "full-pack.json": full_hash,
            "compact-pack.json": compact_hash,
        },
        "evaluation": evaluation,
    }
    artifact["artifact_sha256"] = canonical_sha256(artifact)
    _write_snapshot(artifact_dir / "artifact.json", artifact)
    return artifact


def verify_case_replay(artifact_dir: Path) -> dict[str, Any]:
    artifact = _read_json(artifact_dir / "artifact.json")
    snapshots = artifact.get("snapshots") if isinstance(artifact.get("snapshots"), dict) else {}
    for filename in ("full-pack.json", "compact-pack.json"):
        expected_hash = str(snapshots.get(filename) or "")
        if not expected_hash or _file_sha256(artifact_dir / filename) != expected_hash:
            raise ValueError(f"artifact snapshot hash mismatch: {filename}")
    expected_artifact_hash = str(artifact.pop("artifact_sha256", ""))
    if not expected_artifact_hash or canonical_sha256(artifact) != expected_artifact_hash:
        raise ValueError("case artifact hash mismatch")
    full_pack = _read_json(artifact_dir / "full-pack.json")
    compact_pack = _read_json(artifact_dir / "compact-pack.json")
    evaluation = evaluate_evidence_artifact(full_pack, compact_pack)
    if evaluation.get("artifact_sha256") != (artifact.get("evaluation") or {}).get("artifact_sha256"):
        raise ValueError("evidence evaluation replay mismatch")
    return evaluation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run S1 fixed-data evidence checks without report generation")
    parser.add_argument("--base-url", default="http://127.0.0.1:8099")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--subset", choices=("fixed3", "fixed10", "all"), default="fixed10")
    parser.add_argument("--case-id", action="append", default=[], help="Run one or more available manifest cases")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cases, manifest_exclusions, declared_count = load_subset(args.manifest, args.subset)
    cases = filter_cases(cases, args.case_id)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    runtime_exclusions: list[dict[str, str]] = []
    with httpx.Client(base_url=args.base_url.rstrip("/"), follow_redirects=True) as client:
        for index, case in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] {case['id']}", flush=True)
            try:
                artifact = run_offline_case(client, case, args.output_dir / "cases" / str(case["id"]))
                replay = verify_case_replay(args.output_dir / "cases" / str(case["id"]))
                if not artifact["evaluation"]["passed"] or not replay["passed"]:
                    raise ValueError(f"evidence gate failed: {artifact['evaluation']['errors']}")
                results.append(
                    {
                        "case_id": str(case["id"]),
                        "artifact_sha256": str(artifact["artifact_sha256"]),
                        "evaluation_sha256": str(replay["artifact_sha256"]),
                        "a_b_support_rate": replay["a_b_support_rate"],
                        "c_independent_support_rate": replay["c_independent_support_rate"],
                        "mandatory_evidence_retention": replay["mandatory_evidence_retention"],
                        "compact_pack_chars": replay["compact_pack_chars"],
                    }
                )
            except SourceAttachmentUnavailable as exc:
                runtime_exclusions.append(
                    {
                        "case_id": str(case.get("id") or ""),
                        "menu_code": str(case.get("menu_code") or ""),
                        "articleid": str(case.get("articleid") or ""),
                        "code": "SOURCE_ATTACHMENT_UNAVAILABLE",
                        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "detail": str(exc),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                failures.append({"case_id": str(case.get("id") or ""), "error": f"{type(exc).__name__}: {exc}"})

    scope_status = execution_scope_status(
        subset=args.subset,
        requested_case_ids=list(args.case_id),
        declared_count=declared_count,
        selected_count=len(cases),
        attempted_count=len(results) + len(runtime_exclusions) + len(failures),
        executed_count=len(results),
        failure_count=len(failures),
    )
    summary = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "environment": "server_test",
        "subset": scope_status["gate_subset"],
        "requested_subset": args.subset,
        "requested_case_ids": list(args.case_id),
        "declared_case_count": declared_count,
        "selected_case_count": len(cases),
        "executed_case_count": len(results),
        "passed_case_count": len(results),
        "failed_case_count": len(failures),
        "excluded_cases": [*(manifest_exclusions if not args.case_id else []), *runtime_exclusions],
        "cases": results,
        "failures": failures,
        "dify_report_generation_calls": 0,
        **scope_status,
    }
    summary["artifact_sha256"] = canonical_sha256(summary)
    _write_snapshot(args.output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return 0 if scope_status["execution_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
