from __future__ import annotations

import json
from typing import Any

from app.layered_diagnostics import make_layer_result


PROVIDER_FAILURE_CODES = {
    "HTTP_ERROR",
    "WORKFLOW_ID_MISSING",
    "WORKFLOW_FAILED",
    "OUTPUT_EMPTY",
    "OUTPUT_TRUNCATED",
    "OUTPUT_SCHEMA_INVALID",
    "TIMEOUT",
}
LEGACY_PROVIDER_CODE_MAP = {
    "DIFY_TIMEOUT": "TIMEOUT",
    "DIFY_WORKFLOW_FAILED": "WORKFLOW_FAILED",
    "DIFY_INVALID_RESPONSE": "OUTPUT_SCHEMA_INVALID",
    "GENERATION_JSON_PARSE_FAILED": "OUTPUT_SCHEMA_INVALID",
    "DIFY_OUTPUT_EMPTY": "OUTPUT_EMPTY",
    "DIFY_FRAGMENTARY_REPORT": "OUTPUT_TRUNCATED",
    "DIFY_HTTP_ERROR": "HTTP_ERROR",
    "DIFY_REQUEST_FAILED": "HTTP_ERROR",
    "DIFY_NOT_CONFIGURED": "HTTP_ERROR",
}
_FAILED_WORKFLOW_STATUSES = {"failed", "stopped", "cancelled", "canceled"}
_TRUNCATED_REASONS = {"length", "max_tokens", "token_limit", "truncated"}


class ProviderResponseError(ValueError):
    def __init__(self, code: str, stage: dict[str, Any]):
        super().__init__(f"provider response rejected: {code}")
        self.code = code
        self.stage = stage


def canonical_provider_failure_code(value: Any) -> str:
    code = str(value or "").strip().upper()
    if code in PROVIDER_FAILURE_CODES:
        return code
    return LEGACY_PROVIDER_CODE_MAP.get(code, "UNCLASSIFIED")


def _provider_failure(code: str, *, response: Any = None, metrics: dict[str, Any] | None = None) -> ProviderResponseError:
    canonical = canonical_provider_failure_code(code)
    stage = make_layer_result(
        "provider",
        status="blocked",
        code=canonical,
        output_value=response,
        metrics=metrics,
    )
    return ProviderResponseError(canonical, stage)


def _parse_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_output_object(outputs: Any) -> tuple[dict[str, Any] | None, bool]:
    if isinstance(outputs, dict):
        root = outputs
    elif isinstance(outputs, str):
        parsed = _parse_object(outputs)
        return parsed, parsed is None
    else:
        return None, outputs not in (None, {})
    if "report_markdown" in root:
        return root, False
    malformed_nested = False
    for key in ("result", "output", "answer", "text", "report"):
        if key not in root:
            continue
        parsed = _parse_object(root.get(key))
        if parsed and "report_markdown" in parsed:
            return parsed, False
        if isinstance(root.get(key), str) and str(root.get(key)).strip():
            malformed_nested = True
    for candidate in root.values():
        parsed = _parse_object(candidate)
        if parsed and "report_markdown" in parsed:
            return parsed, False
    return None, malformed_nested or bool(root)


def _is_truncated(data: dict[str, Any], output: dict[str, Any]) -> bool:
    if output.get("truncated") is True or data.get("truncated") is True:
        return True
    for value in (
        output.get("finish_reason"),
        output.get("stop_reason"),
        data.get("finish_reason"),
        data.get("stop_reason"),
    ):
        if str(value or "").strip().lower() in _TRUNCATED_REASONS:
            return True
    return False


def extract_provider_output(response: Any) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if not isinstance(response, dict):
        raise _provider_failure("OUTPUT_SCHEMA_INVALID", response=response)
    data_value = response.get("data", {})
    if not isinstance(data_value, dict):
        raise _provider_failure("OUTPUT_SCHEMA_INVALID", response=response)
    data = data_value
    workflow_status = str(data.get("status") or response.get("status") or "").strip().lower()
    if workflow_status in _FAILED_WORKFLOW_STATUSES:
        raise _provider_failure(
            "WORKFLOW_FAILED",
            response=response,
            metrics={"workflow_status": workflow_status},
        )
    workflow_id = str(
        response.get("workflow_run_id")
        or data.get("id")
        or data.get("workflow_run_id")
        or ""
    ).strip()
    if not workflow_id:
        raise _provider_failure(
            "WORKFLOW_ID_MISSING",
            response=response,
            metrics={"workflow_status": workflow_status},
        )
    outputs = data.get("outputs") if "outputs" in data else response.get("outputs")
    if outputs in (None, "", {}):
        raise _provider_failure(
            "OUTPUT_EMPTY",
            response=response,
            metrics={"workflow_status": workflow_status},
        )
    output, malformed = _extract_output_object(outputs)
    if output is None:
        code = "OUTPUT_SCHEMA_INVALID" if malformed else "OUTPUT_EMPTY"
        raise _provider_failure(
            code,
            response=response,
            metrics={"workflow_status": workflow_status},
        )
    report_markdown = str(output.get("report_markdown") or "")
    if not report_markdown.strip():
        raise _provider_failure(
            "OUTPUT_EMPTY",
            response=response,
            metrics={"workflow_status": workflow_status},
        )
    if _is_truncated(data, output):
        raise _provider_failure(
            "OUTPUT_TRUNCATED",
            response=response,
            metrics={"workflow_status": workflow_status},
        )
    stage = make_layer_result(
        "provider",
        status="ok",
        output_value=response,
        metrics={"workflow_status": workflow_status},
    )
    return workflow_id, output, stage


def provider_stage_from_exception(exc: BaseException) -> dict[str, Any]:
    error_type = exc.__class__.__name__
    is_timeout = isinstance(exc, TimeoutError) or "timeout" in error_type.lower()
    return make_layer_result(
        "provider",
        status="blocked",
        code="TIMEOUT" if is_timeout else "HTTP_ERROR",
        metrics={"error_type": error_type},
    )
