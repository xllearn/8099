from __future__ import annotations

import html
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.deepeval_advisory.models import (
    AdvisoryJob,
    AdvisoryResult,
    JobStatus,
    MetricStatus,
    utc_now_iso,
)


_METRIC_IDS = (
    "claim_faithfulness_v1",
    "critical_coverage_v1",
    "attachment_state_consistency_v1",
    "answer_relevancy_v1",
)
_JOB_ID_PATTERN = re.compile(r"^job_[A-Za-z0-9_-]{8,80}$")


def _json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def build_safe_snapshot(state_dir: Path) -> dict[str, Any]:
    root = Path(state_dir)
    state_errors = 0
    worker_value = _json_object(root / "worker" / "current.json") or {}
    inventory_present = "current_job_ids" in worker_value
    current_job_ids: tuple[str, ...] = ()
    if inventory_present:
        raw_inventory = worker_value.get("current_job_ids")
        if (
            isinstance(raw_inventory, list)
            and all(
                type(job_id) is str
                and _JOB_ID_PATTERN.fullmatch(job_id)
                for job_id in raw_inventory
            )
            and len(raw_inventory) == len(set(raw_inventory))
        ):
            current_job_ids = tuple(raw_inventory)
        else:
            state_errors += 1
    current_job_id_set = set(current_job_ids)

    latest: dict[tuple[str, int], AdvisoryJob] = {}
    for path in sorted((root / "jobs").glob("*.json")):
        if inventory_present and path.stem not in current_job_id_set:
            continue
        value = _json_object(path)
        try:
            job = AdvisoryJob.model_validate(value)
        except (ValidationError, TypeError):
            state_errors += 1
            continue
        identity = (job.run_ref, job.report_version)
        current = latest.get(identity)
        if current is None or (
            job.updated_at,
            job.job_id,
        ) > (
            current.updated_at,
            current.job_id,
        ):
            latest[identity] = job
    if inventory_present:
        stored_current_ids = {
            job.job_id for job in latest.values()
        }
        state_errors += len(
            current_job_id_set - stored_current_ids
        )

    results: dict[str, AdvisoryResult] = {}
    for path in sorted((root / "results").glob("*.json")):
        if inventory_present and path.stem not in current_job_id_set:
            continue
        value = _json_object(path)
        try:
            result = AdvisoryResult.model_validate(value)
        except (ValidationError, TypeError):
            state_errors += 1
            continue
        results[result.job_id] = result

    discovery_errors = worker_value.get("discovery_errors", 0)
    if type(discovery_errors) is not int or discovery_errors < 0:
        discovery_errors = 0
        state_errors += 1

    enrolled = len(latest)
    if inventory_present:
        eligible_value = worker_value.get("eligible")
        if (
            type(eligible_value) is int
            and eligible_value >= enrolled
        ):
            eligible = eligible_value
        else:
            eligible = enrolled
            state_errors += 1
    else:
        eligible_value = worker_value.get("eligible", enrolled)
        eligible = (
            eligible_value
            if type(eligible_value) is int
            and eligible_value >= enrolled
            else enrolled
        )
    cached = sum(
        job.status is JobStatus.CACHED for job in latest.values()
    )
    paused = sum(
        job.status is JobStatus.PAUSED for job in latest.values()
    )
    unavailable = sum(
        job.status
        in {
            JobStatus.UNAVAILABLE,
            JobStatus.OVER_BUDGET,
            JobStatus.PARTIAL,
            JobStatus.INDETERMINATE,
        }
        or (
            job.status is JobStatus.PAUSED
            and job.error_category == "judge_unconfigured"
        )
        for job in latest.values()
    )
    scored_jobs: list[tuple[AdvisoryJob, AdvisoryResult]] = []
    for job in latest.values():
        result = results.get(job.job_id)
        if (
            result is not None
            and job.status in {JobStatus.COMPLETED, JobStatus.CACHED}
            and result.status is job.status
            and result.job_id == job.job_id
            and result.run_ref == job.run_ref
            and result.advisory_input_sha256
            == job.advisory_input_sha256
        ):
            scored_jobs.append((job, result))
    scored = len(scored_jobs)

    denominator = eligible or 1
    coverage_values = {
        "eligible": eligible,
        "enrolled": enrolled,
        "scored": scored,
        "cached": cached,
        "paused": paused,
        "unavailable": unavailable,
    }
    coverage = {
        **coverage_values,
        "percentages": {
            name: round(value * 100 / denominator, 2)
            for name, value in coverage_values.items()
        },
    }

    metric_values: dict[str, list[float]] = {
        metric_id: [] for metric_id in _METRIC_IDS
    }
    for _job, result in scored_jobs:
        for observation in result.metrics:
            if (
                observation.metric_id in metric_values
                and observation.status is MetricStatus.SCORED
                and observation.score is not None
            ):
                metric_values[observation.metric_id].append(
                    observation.score
                )
    metrics = {
        metric_id: {
            "sample_count": len(values),
            "mean": (
                round(sum(values) / len(values), 6)
                if values
                else None
            ),
        }
        for metric_id, values in metric_values.items()
    }

    ordered_jobs = sorted(
        latest.values(),
        key=lambda job: (job.updated_at, job.job_id),
        reverse=True,
    )
    recent = []
    for job in ordered_jobs[:10]:
        availability = (
            "unavailable"
            if (
                job.status is JobStatus.PAUSED
                and job.error_category == "judge_unconfigured"
            )
            else job.status.value
        )
        recent.append(
            {
                "run_ref": job.run_ref,
                "report_version": job.report_version,
                "status": availability,
            }
        )

    degraded = bool(
        discovery_errors or state_errors or unavailable
    )
    return {
        "schema_version": "8099.deepeval-safe-report/v1",
        "generated_at": utc_now_iso(),
        "health": {
            "status": "degraded" if degraded else "ok",
            "discovery_errors": discovery_errors,
            "state_errors": state_errors,
        },
        "coverage": coverage,
        "metrics": metrics,
        "judge": {
            "evaluations": sum(
                job.status is JobStatus.COMPLETED
                for job in latest.values()
            ),
            "cache_saved_evaluations": cached,
        },
        "recent": recent,
    }


def _html(snapshot: dict[str, Any]) -> bytes:
    coverage = snapshot["coverage"]
    metric_rows = "".join(
        "<tr>"
        f"<td>{html.escape(metric_id)}</td>"
        f"<td>{value['sample_count']}</td>"
        f"<td>{'—' if value['mean'] is None else value['mean']}</td>"
        "</tr>"
        for metric_id, value in snapshot["metrics"].items()
    )
    recent_rows = "".join(
        "<tr>"
        f"<td>{html.escape(item['run_ref'])}</td>"
        f"<td>{item['report_version']}</td>"
        f"<td>{html.escape(item['status'])}</td>"
        "</tr>"
        for item in snapshot["recent"]
    )
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="30">
<title>DeepEval advisory</title>
<style>body{{font-family:sans-serif;max-width:960px;margin:2rem auto}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:.45rem;border:1px solid #ccc}}
.cards{{display:flex;gap:.7rem;flex-wrap:wrap}}.card{{padding:.7rem;border:1px solid #ccc}}</style>
</head><body><h1>DeepEval advisory</h1>
<p>Status: {html.escape(snapshot['health']['status'])}</p>
<div class="cards">
{''.join(f"<div class='card'>{html.escape(name)}: {value}</div>" for name, value in coverage.items() if name != 'percentages')}
</div>
<h2>Metrics</h2><table><tr><th>Metric</th><th>Samples</th><th>Mean</th></tr>
{metric_rows}</table>
<h2>Recent safe references</h2><table><tr><th>Run reference</th><th>Version</th><th>Status</th></tr>
{recent_rows}</table>
<p>Judge evaluations: {snapshot['judge']['evaluations']}; cache saved: {snapshot['judge']['cache_saved_evaluations']}</p>
</body></html>"""
    return body.encode("utf-8")


def start_reporting_server(
    state_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8100,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    root = Path(state_dir)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            snapshot = build_safe_snapshot(root)
            if self.path == "/health":
                payload = json.dumps(
                    snapshot["health"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            elif self.path == "/metrics.json":
                payload = json.dumps(
                    snapshot,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            elif self.path == "/":
                payload = _html(snapshot)
                content_type = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'",
            )
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="deepeval-advisory-reporting",
        daemon=True,
    )
    thread.start()
    return server, thread
