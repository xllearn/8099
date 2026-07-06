from __future__ import annotations

import time
from typing import Any

from app.core.llm import LLMProvider, LLMProviderError, LLMRequest, get_llm_provider, load_prompt_ref, parse_llm_json
from app.core.workflow.state import WorkflowBackend, make_workflow_node, utc_now_iso


LOCAL_WORKFLOW_NODE_NAMES = ["prepare", "generate", "render", "qa", "final"]
LOCAL_GENERATION_PROMPT = "local_report_generation"
LOCAL_GENERATION_PROMPT_VERSION = "v1"
LOCAL_REVIEW_WARNING = "local_engine template draft generated; manual review is required before formal use."


def run_local_workflow(pack: dict[str, Any], run_id: str, llm_provider: LLMProvider | None = None) -> dict[str, Any]:
    """Run the serial local workflow MVP with provider and prompt metadata."""
    nodes: list[dict[str, Any]] = []

    prepared = _run_node(nodes, "prepare", lambda: _prepare_local_context(pack))
    generated = _run_node(nodes, "generate", lambda: _generate_local_report(prepared, llm_provider))
    rendered = _run_node(nodes, "render", lambda: _render_local_report(generated))
    qa = _run_node(nodes, "qa", lambda: _qa_local_draft(rendered))
    _run_node(nodes, "final", lambda: None)

    return _finalize_local_result(run_id, rendered, qa, nodes)


def _run_node(nodes: list[dict[str, Any]], name: str, fn):
    started_at = utc_now_iso()
    started = time.perf_counter()
    try:
        value = fn()
    except Exception as exc:
        nodes.append(
            make_workflow_node(
                name,
                status="failed",
                started_at=started_at,
                finished_at=utc_now_iso(),
                elapsed_ms=_elapsed_ms(started),
                error=exc.__class__.__name__,
            )
        )
        raise
    nodes.append(make_workflow_node(name, status="finished", started_at=started_at, finished_at=utc_now_iso(), elapsed_ms=_elapsed_ms(started)))
    return value


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _prepare_local_context(pack: dict[str, Any]) -> dict[str, Any]:
    primary = _material_list(pack.get("primary_materials"))
    auxiliary = _material_list(pack.get("auxiliary_materials"))
    title = _first_text([*(item.get("title") for item in primary), pack.get("title"), pack.get("pack_id")])
    body = _first_text(item.get("content_text") or item.get("content") or item.get("summary") for item in primary)
    attachments = []
    for item in [*primary, *auxiliary]:
        for attachment in _material_list(item.get("attachments")):
            filename = _first_text([attachment.get("filename"), attachment.get("name"), attachment.get("title")])
            summary = _first_text([attachment.get("summary"), attachment.get("content_text"), attachment.get("parse_status")])
            if filename or summary:
                attachments.append({"filename": filename, "summary": summary})
    return {
        "pack_id": str(pack.get("pack_id") or ""),
        "title": title or "Local template analysis report",
        "body": body,
        "primary_count": len(primary),
        "auxiliary_count": len(auxiliary),
        "attachments": attachments,
        "pack_warnings": [str(item) for item in pack.get("warnings") or [] if item],
    }


def _material_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _first_text(values) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _generate_local_report(context: dict[str, Any], llm_provider: LLMProvider | None) -> dict[str, Any]:
    prompt_ref = load_prompt_ref(LOCAL_GENERATION_PROMPT, version=LOCAL_GENERATION_PROMPT_VERSION)
    provider = llm_provider or get_llm_provider()
    draft_markdown = _generate_local_markdown(context)
    request = LLMRequest(
        prompt_ref=prompt_ref,
        variables={
            "pack_id": context.get("pack_id") or "",
            "title": context.get("title") or "",
            "body": context.get("body") or "",
            "primary_count": context.get("primary_count") or 0,
            "auxiliary_count": context.get("auxiliary_count") or 0,
            "attachments": context.get("attachments") or [],
            "draft_markdown": draft_markdown,
        },
    )
    llm_result = provider.generate(request)
    model_call = llm_result.call_metadata()
    base = {
        **context,
        "prompt_refs": [prompt_ref.to_dict()],
        "model_calls": [model_call],
        "llm_provider": llm_result.provider,
        "llm_model": llm_result.model,
        "prompt_ref": prompt_ref.ref,
        "prompt_sha256": prompt_ref.sha256,
    }
    try:
        payload = parse_llm_json(llm_result)
    except LLMProviderError as exc:
        parse_error = exc.to_dict()
        model_call["json_parse_error"] = parse_error
        return {
            **base,
            "report_title": context["title"],
            "report_markdown": draft_markdown,
            "quality_issues": [{"issue_id": exc.code, "message": exc.message}],
            "warnings": [f"{exc.code}: {exc.message}"],
        }

    return {
        **base,
        "report_title": _first_text([payload.get("report_title"), payload.get("title"), context["title"]]),
        "report_markdown": _first_text([payload.get("report_markdown"), payload.get("markdown"), draft_markdown]),
        "quality_issues": [],
        "warnings": [],
    }


def _generate_local_markdown(context: dict[str, Any]) -> str:
    title = context["title"]
    lines = [
        f"# {title}",
        "",
        "## Notice Highlights",
        "",
        f"This local draft is generated from {context['primary_count']} primary material(s) and {context['auxiliary_count']} auxiliary material(s).",
    ]
    if context["body"]:
        lines.extend(["", f"Core material summary: {context['body']}"])
    if context["attachments"]:
        lines.extend(["", "## Attachment Clues", ""])
        for attachment in context["attachments"][:8]:
            filename = attachment.get("filename") or "unnamed attachment"
            summary = attachment.get("summary") or "no summary"
            lines.append(f"- {filename}: {summary}")
    lines.extend(
        [
            "",
            "## Preliminary Analysis",
            "",
            "The local template keeps notice title, body summary, and attachment clues for preview. Key facts, enterprise impact, and action timing still require manual review against the original evidence.",
        ]
    )
    return "\n".join(lines).strip()


def _render_local_report(generated: dict[str, Any]) -> dict[str, Any]:
    rendered = dict(generated)
    rendered["report_markdown"] = str(rendered.get("report_markdown") or "").strip()
    return rendered


def _qa_local_draft(rendered: dict[str, Any]) -> dict[str, Any]:
    markdown = str(rendered.get("report_markdown") or "").strip()
    issues = list(rendered.get("quality_issues") or [])
    if not markdown:
        issues.append({"issue_id": "LOCAL_EMPTY_REPORT", "message": "local_engine did not produce report text"})
    return {"passed": bool(markdown) and not issues, "issues": issues}


def _finalize_local_result(
    run_id: str,
    rendered: dict[str, Any],
    quality_check: dict[str, Any],
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    warnings = [
        LOCAL_REVIEW_WARNING,
        *list(rendered.get("warnings") or []),
        *list(rendered.get("pack_warnings") or []),
    ]
    model_calls = list(rendered.get("model_calls") or [])
    prompt_refs = list(rendered.get("prompt_refs") or [])
    result: dict[str, Any] = {
        "success": True,
        "backend": WorkflowBackend.LOCAL_ENGINE.value,
        "workflow_backend": WorkflowBackend.LOCAL_ENGINE.value,
        "workflow_run_id": f"local-{run_id}",
        "status": "finished" if quality_check.get("passed") else "needs_manual_review",
        "report_title": rendered.get("report_title") or rendered.get("title") or "Local template analysis report",
        "report_markdown": rendered.get("report_markdown") or "",
        "quality_check": quality_check,
        "quality_gate": {
            "deliverable_status": "needs_manual_review",
            "reason": "local_engine_mvp_template_draft",
        },
        "generation_warnings": warnings,
        "warnings": warnings,
        "remaining_issues": list(quality_check.get("issues") or []),
        "version": 1,
        "nodes": list(nodes),
    }
    if model_calls:
        first_call = model_calls[0]
        result.update(
            {
                "llm_provider": first_call.get("provider") or "",
                "llm_model": first_call.get("model") or "",
                "prompt_ref": first_call.get("prompt_ref") or "",
                "prompt_sha256": first_call.get("prompt_sha256") or "",
                "model_calls": model_calls,
            }
        )
    if prompt_refs:
        result["prompt_refs"] = prompt_refs
    return result
