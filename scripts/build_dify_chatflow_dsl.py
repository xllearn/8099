from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP_NAME = "医药公告采购分析报告 Chatflow"
DEFAULT_MODEL_PROVIDER = "langgenius/tongyi/tongyi"
DEFAULT_MODEL_NAME = "qwen3-max-2025-09-23"
DEFAULT_FAST_MODEL_PROVIDER = "langgenius/tongyi/tongyi"
DEFAULT_FAST_MODEL_NAME = "qwen-plus-latest"
DEFAULT_BACKEND_BASE_URL = "http://192.168.34.88:8099"


def prompt(name: str) -> str:
    return (ROOT / "prompts" / name).read_text(encoding="utf-8")


def stable_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"medical-notice-chatflow:{name}"))


def conversation_variable(name: str, value: str = "") -> dict[str, Any]:
    return {
        "id": stable_id(f"conversation:{name}"),
        "name": name,
        "selector": ["conversation", name],
        "value_type": "string",
        "value": value,
        "description": "",
    }


def position(x: int, y: int) -> dict[str, int]:
    return {"x": x, "y": y}


def node(
    node_id: str,
    node_type: str,
    title: str,
    x: int,
    y: int,
    data: dict[str, Any],
    *,
    height: int = 90,
    width: int = 242,
) -> dict[str, Any]:
    payload = {
        "id": node_id,
        "type": "custom",
        "height": height,
        "width": width,
        "position": position(x, y),
        "positionAbsolute": position(x, y),
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "data": {
            "desc": "",
            "selected": False,
            "title": title,
            "type": node_type,
        },
    }
    payload["data"].update(data)
    return payload


def edge(
    edge_id: str,
    source: str,
    target: str,
    source_type: str,
    target_type: str,
    *,
    source_handle: str = "source",
) -> dict[str, Any]:
    return {
        "id": edge_id,
        "source": source,
        "sourceHandle": source_handle,
        "target": target,
        "targetHandle": "target",
        "type": "custom",
        "zIndex": 0,
        "data": {
            "isInIteration": False,
            "isInLoop": False,
            "sourceType": source_type,
            "targetType": target_type,
        },
    }


def model(provider: str, name: str, temperature: float) -> dict[str, Any]:
    return {
        "provider": provider,
        "name": name,
        "mode": "chat",
        "completion_params": {
            "temperature": temperature,
            "max_tokens": 8192,
            "response_format": "text",
            "thinking": False,
        },
    }


def llm_node(
    node_id: str,
    title: str,
    x: int,
    y: int,
    prompt_template: list[dict[str, str]],
    provider: str,
    model_name: str,
    *,
    temperature: float = 0.0,
) -> dict[str, Any]:
    return node(
        node_id,
        "llm",
        title,
        x,
        y,
        {
            "context": {"enabled": False, "variable_selector": []},
            "memory": {"enabled": False, "window": {"enabled": False, "size": 50}, "role_prefix": {"user": "", "assistant": ""}},
            "model": model(provider, model_name, temperature),
            "prompt_template": prompt_template,
            "retry_config": {
                "enabled": False,
                "exponential_backoff": {"enabled": False, "max_interval": 10000, "multiplier": 2},
                "max_retries": 2,
                "retry_interval": 1000,
            },
            "structured_output": {"enabled": False},
            "variables": [],
            "vision": {"enabled": False},
        },
        height=126,
    )


def http_node(node_id: str, title: str, x: int, y: int, url: str, body_value: str, read_timeout: int = 120) -> dict[str, Any]:
    return node(
        node_id,
        "http-request",
        title,
        x,
        y,
        {
            "authorization": {"config": None, "type": "no-auth"},
            "body": {"type": "json", "data": [{"key": "", "type": "text", "value": body_value}]},
            "headers": "Content-Type:application/json",
            "method": "post",
            "params": "",
            "url": url,
            "retry_config": {
                "enabled": True,
                "exponential_backoff": {"enabled": True, "max_interval": 10000, "multiplier": 2},
                "max_retries": 2,
                "retry_interval": 1000,
            },
            "ssl_verify": True,
            "timeout": {"connect": 10, "read": read_timeout, "write": 30},
            "variables": [],
        },
        height=150,
    )


def code_node(
    node_id: str,
    title: str,
    x: int,
    y: int,
    code: str,
    variables: list[dict[str, Any]],
    outputs: dict[str, Any],
) -> dict[str, Any]:
    return node(
        node_id,
        "code",
        title,
        x,
        y,
        {
            "code": code,
            "code_language": "python3",
            "outputs": outputs,
            "variables": variables,
        },
        height=112,
    )


def assigner_node(node_id: str, title: str, x: int, y: int, assignments: dict[str, list[str]]) -> dict[str, Any]:
    items = []
    for variable, selector in assignments.items():
        items.append(
            {
                "input_type": "variable",
                "operation": "over-write",
                "write_mode": "over-write",
                "value": selector,
                "variable_selector": ["conversation", variable],
            }
        )
    return node(
        node_id,
        "assigner",
        title,
        x,
        y,
        {
            "items": items,
            "isInIteration": False,
            "isInLoop": False,
            "version": "2",
        },
        height=92,
    )


def if_else_node(node_id: str, title: str, x: int, y: int, cases: list[dict[str, Any]]) -> dict[str, Any]:
    return node(
        node_id,
        "if-else",
        title,
        x,
        y,
        {"cases": cases},
        height=156,
    )


def answer_node(node_id: str, title: str, x: int, y: int, answer: str) -> dict[str, Any]:
    return node(
        node_id,
        "answer",
        title,
        x,
        y,
        {"answer": answer, "variables": []},
        height=112,
    )


def condition(selector: list[str], operator: str, value: str | None = None, *, var_type: str = "string") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": stable_id(":".join(selector) + operator + str(value)),
        "variable_selector": selector,
        "comparison_operator": operator,
        "varType": var_type,
    }
    if value is not None:
        payload["value"] = value
    return payload


STRING_OUTPUT = {"children": None, "type": "string"}
PAYLOAD_OUTPUT = {"payload": STRING_OUTPUT}


ROUTE_TURN_CODE = """def main(query: str, current_report_ir: str, repair_attempt_count: str = "") -> dict:
    import re

    text = (query or "").strip()
    compact = re.sub(r"\\s+", "", text).lower()
    current = (current_report_ir or "").strip()
    has_report = bool(current and current not in {"{}", "null", "None"})

    if not has_report:
        return {"route": "first_generate"}

    approval_phrases = [
        "确认下载",
        "可以导出",
        "同意下载",
        "下载word",
        "下载Word",
        "下载报告",
        "导出word",
        "导出Word",
        "生成word",
        "生成Word",
    ]
    revision_markers = [
        "质检",
        "needs_fix",
        "block",
        "修复建议",
        "问题统计",
        "问题明细",
        "一般问题",
        "缺失规则",
        "report_depth",
        "table_evidence",
        "修改",
        "修订",
        "调整",
        "补充",
        "删除",
        "改为",
        "缺陷",
        "报告正文",
    ]
    short_confirmation = len(text) <= 40 and text.count("\\n") <= 1
    has_approval = any(re.sub(r"\\s+", "", phrase).lower() in compact for phrase in approval_phrases)
    has_revision = any(marker.lower() in text.lower() for marker in revision_markers)
    try:
        repair_count = int((repair_attempt_count or "0").strip())
    except Exception:
        repair_count = 0

    if short_confirmation and has_approval and not has_revision:
        return {"route": "approve_download"}
    if repair_count >= 3 and has_revision:
        return {"route": "repair_limit"}
    return {"route": "revise"}
"""


BUILD_RENDER_PAYLOAD_CODE = """def main(report: str) -> dict:
    import json
    payload = {
        "markdown": report or "",
        "strict_quality": False,
    }
    return {"payload": json.dumps(payload, ensure_ascii=False)}
"""


PARSE_RENDER_CODE = """def main(render_body: str) -> dict:
    import json
    try:
        data = json.loads(render_body or "{}")
    except Exception:
        data = {"success": False, "error": "render endpoint returned non-JSON"}
    report_ir = data.get("report_ir") if data.get("success") else None
    report_markdown = data.get("report_markdown") or ""
    error = data.get("error") or ""
    if not data.get("success") and not report_markdown:
        report_markdown = "报告解析失败：" + (error or "ReportIR 解析失败")
    return {
        "report_ir": json.dumps(report_ir, ensure_ascii=False) if report_ir else "",
        "report_markdown": report_markdown,
        "render_error": error,
    }
"""


PARSE_REVISED_RENDER_CODE = """def main(render_body: str, previous_report_ir: str, previous_report_markdown: str) -> dict:
    import json
    try:
        data = json.loads(render_body or "{}")
    except Exception:
        data = {"success": False, "error": "render endpoint returned non-JSON"}
    report_ir = data.get("report_ir")
    report_markdown = data.get("report_markdown") or ""
    error = data.get("error") or ""
    if data.get("success") and report_ir and report_markdown:
        return {
            "report_ir": json.dumps(report_ir, ensure_ascii=False),
            "report_markdown": report_markdown,
            "render_error": "",
            "render_success": "true",
            "revision_note": "修订已完成。",
        }
    return {
        "report_ir": previous_report_ir or "{}",
        "report_markdown": previous_report_markdown or "",
        "render_error": error or "修订模型未返回可解析的 ReportIR",
        "render_success": "false",
        "revision_note": "本次修订未成功，已保留原报告；请重新提交修改意见或改用更稳定的修订模型。",
    }
"""


PARSE_HISTORY_CODE = """def main(raw_history: str, extracted_text: str) -> dict:
    import re

    def strip_thinking(text: str) -> str:
        patterns = [
            r"<think\\b[^>]*>[\\s\\S]*?</think>",
            r"<思考\\b[^>]*>[\\s\\S]*?</思考>",
            r"```(?:thinking|think|思考)[\\s\\S]*?```",
        ]
        for pattern in patterns:
            text = re.sub(pattern, "", text or "", flags=re.I)
        return text.strip()

    def clean_line(line: str) -> str:
        line = re.sub(r"^\\s*[-*•\\d.、]+\\s*", "", line or "").strip()
        line = re.sub(r"\\d+(?:\\.\\d+)?\\s*(?:%|元/[^，。；\\s]+|元|万元|年|月|日)", "相关数值", line)
        line = re.sub(r"20\\d{2}[-年./]\\d{1,2}(?:[-月./]\\d{1,2}日?)?", "相关时间", line)
        return line.strip(" ；;。")

    cleaned = strip_thinking(raw_history or "")
    match = re.search(r"<history_insights\\b[^>]*>([\\s\\S]*?)</history_insights>", cleaned, flags=re.I)
    if match:
        body = match.group(1)
        lines = [clean_line(line) for line in body.splitlines()]
    else:
        lines = [clean_line(line) for line in cleaned.splitlines()]

    useful = []
    for line in lines:
        if not line or "history_insights" in line or "提示词" in line or "模型" in line:
            continue
        if any(keyword in line for keyword in ("历史", "同类", "价格联动", "最低价", "非中选", "供应保障", "信用", "报量", "协议采购量", "企业")):
            useful.append(line)

    if not useful:
        source_lines = [clean_line(line) for line in re.split(r"[\\r\\n]+", extracted_text or "")]
        source = "\\n".join(line for line in source_lines if line)
        fallback = []
        if any(keyword in source for keyword in ("最低", "价格联动", "价格体系")):
            fallback.append("结合既往同类项目经验，可关注最低价约束、价格联动和跨区域价格体系管理。")
        if "非中选" in source:
            fallback.append("从历史项目看，非中选产品挂网路径和院内使用空间需要在企业关注点中提示。")
        if any(keyword in source for keyword in ("协议采购量", "报量", "使用比例")):
            fallback.append("与此前同类采购文件相比，报量、协议采购量和中选产品使用要求通常会影响供应保障安排。")
        if any(keyword in source for keyword in ("供应", "信用", "断供")):
            fallback.append("结合既往同类项目经验，供应保障和信用评价是执行期风险管理重点。")
        useful = fallback

    if not useful:
        return {"history_insights": ""}
    bullets = []
    for line in useful[:6]:
        if not re.match(r"^(结合既往同类项目经验|从历史项目看|与此前同类采购文件相比)", line):
            line = "结合既往同类项目经验，" + line
        bullets.append("- " + line.rstrip("。") + "。")
    return {"history_insights": "<history_insights>\\n" + "\\n".join(bullets) + "\\n</history_insights>"}
"""


BUILD_QA_PAYLOAD_CODE = """def main(qa_output: str, report_ir: str, report_markdown: str, evidence: str, history: str) -> dict:
    import json
    MAX_PAYLOAD_CHARS = 110000

    def truncate_payload_fields(payload):
        field_limits = [
            ("evidence_text", 60000),
            ("history_text", 12000),
            ("report_text", 16000),
            ("qa_output", 8000),
        ]
        for key, limit in field_limits:
            value = payload.get(key)
            if isinstance(value, str) and len(value) > limit:
                payload[key] = value[:limit] + "\\n[内容过长，已截断]"
        while len(json.dumps(payload, ensure_ascii=False)) > MAX_PAYLOAD_CHARS:
            changed = False
            for key, _limit in field_limits:
                value = payload.get(key)
                if isinstance(value, str) and len(value) > 2000:
                    payload[key] = value[: max(2000, int(len(value) * 0.75))] + "\\n[内容过长，已截断]"
                    changed = True
                    break
            if not changed:
                break
        return payload

    try:
        parsed_report_ir = json.loads(report_ir or "{}")
    except Exception:
        parsed_report_ir = None
    payload = {
        "qa_output": qa_output or "",
        "report_ir": parsed_report_ir,
        "report_text": report_markdown or report_ir or "",
        "history_text": history or "",
        "evidence_text": evidence or "",
    }
    payload = truncate_payload_fields(payload)
    return {"payload": json.dumps(payload, ensure_ascii=False)}
"""


RESET_REPAIR_STATE_CODE = """def main() -> dict:
    return {"repair_attempt_count": "0"}
"""


UPDATE_REPAIR_STATE_CODE = """def main(qa_status: str, previous_repair_attempt_count: str) -> dict:
    status = (qa_status or "pass").strip().lower().replace("-", "_")
    try:
        previous = int((previous_repair_attempt_count or "0").strip())
    except Exception:
        previous = 0
    count = previous + 1 if status == "needs_fix" else 0
    guard_notice = ""
    if count >= 3 and status == "needs_fix":
        guard_notice = (
            "\\n\\n已达到修复上限（连续 3 次 needs_fix）。请不要继续粘贴同类修复建议；"
            "建议重新生成，或人工核对附件证据后再处理。"
        )
    return {
        "repair_attempt_count": str(count),
        "repair_guard_notice": guard_notice,
    }
"""


PARSE_QA_CODE = """def main(qa_body: str) -> dict:
    import json
    try:
        data = json.loads(qa_body or "{}")
    except Exception:
        data = {"success": False, "qa_summary": "质检接口返回非 JSON"}
    qa = data.get("qa") or {}
    summary = data.get("qa_summary") or qa.get("summary") or ""
    status = qa.get("status") or data.get("status") or ""
    return {
        "qa_summary": summary,
        "qa_status": status,
        "qa_body": json.dumps(data, ensure_ascii=False),
    }
"""


BUILD_EMPTY_HISTORY_CODE = """def main() -> dict:
    return {"history_insights": ""}
"""


PARSE_SOURCE_EVIDENCE_CODE = """def main(fetch_body: str) -> dict:
    import json

    raw = fetch_body or ""
    try:
        data = json.loads(raw or "{}")
    except Exception:
        # fallback raw body
        return {"evidence_text": raw}

    evidence = data.get("evidence_for_llm") if isinstance(data, dict) else ""
    if isinstance(evidence, str) and evidence.strip():
        return {"evidence_text": evidence}

    # fallback raw body
    return {"evidence_text": raw}
"""


BUILD_EXPORT_PAYLOAD_CODE = """def main(report_ir: str, report_markdown: str, evidence: str, history: str) -> dict:
    import json
    MAX_PAYLOAD_CHARS = 110000

    def truncate_payload_fields(payload):
        field_limits = [
            ("evidence_text", 60000),
            ("history_text", 12000),
            ("report_text", 16000),
        ]
        for key, limit in field_limits:
            value = payload.get(key)
            if isinstance(value, str) and len(value) > limit:
                payload[key] = value[:limit] + "\\n[内容过长，已截断]"
        while len(json.dumps(payload, ensure_ascii=False)) > MAX_PAYLOAD_CHARS:
            changed = False
            for key, _limit in field_limits:
                value = payload.get(key)
                if isinstance(value, str) and len(value) > 2000:
                    payload[key] = value[: max(2000, int(len(value) * 0.75))] + "\\n[内容过长，已截断]"
                    changed = True
                    break
            if not changed:
                break
        return payload

    try:
        parsed_report_ir = json.loads(report_ir or "{}")
    except Exception:
        parsed_report_ir = None
    qa_output = {
        "status": "pass",
        "issues": [],
        "unsupported_claims": [],
        "history_leakage": [],
        "missing_rules": [],
        "language_issues": [],
        "fix_instructions": [],
        "summary": "用户确认后导出 Word",
    }
    payload = {
        "report_ir": parsed_report_ir,
        "qa_output": json.dumps(qa_output, ensure_ascii=False),
        "report_text": report_markdown or report_ir or "",
        "history_text": history or "",
        "evidence_text": evidence or "",
        "strict_quality": False,
    }
    payload = truncate_payload_fields(payload)
    return {"payload": json.dumps(payload, ensure_ascii=False)}
"""


PARSE_EXPORT_CODE = """def main(export_body: str) -> dict:
    import json
    try:
        data = json.loads(export_body or "{}")
    except Exception:
        data = {"success": False, "error": "export endpoint returned non-JSON"}
    success = bool(data.get("success"))
    blocked = bool(data.get("blocked"))
    download_url = data.get("download_url") or ""
    filename = data.get("filename") or ""
    qa_summary = data.get("qa_summary") or data.get("error") or ""
    if success and download_url:
        answer = "已生成 Word。\\n下载链接：%s\\n文件名：%s" % (download_url, filename)
    elif blocked:
        answer = "Word 导出失败：\\n%s" % (qa_summary or "导出接口未返回下载链接")
    else:
        answer = "Word 导出失败：%s" % (qa_summary or "请先生成并确认报告")
    return {
        "answer": answer,
        "download_url": download_url,
        "filename": filename,
        "qa_summary": qa_summary,
    }
"""


def build_dsl(
    app_name: str,
    model_provider: str,
    model_name: str,
    fast_model_provider: str,
    fast_model_name: str,
    backend_base_url: str = DEFAULT_BACKEND_BASE_URL,
) -> dict[str, Any]:
    backend_base_url = backend_base_url.rstrip("/")
    system_prompt = prompt("report_system_prompt.md")
    report_prompt = prompt("report_user_prompt.md")
    report_prompt = report_prompt.replace("{{#history_insights#}}", "{{#conversation.history_insights#}}")
    report_prompt = report_prompt.replace("{{#fetch_node.body#}}", "{{#conversation.source_evidence#}}")
    history_prompt = prompt("report_history_prompt.md")
    revision_prompt = prompt("report_revision_prompt.md")
    qa_prompt = prompt("report_qa_prompt.md")

    def qa_prompt_for(report_ir_selector: str, markdown_selector: str) -> str:
        return (
            qa_prompt.replace("{{#current_report_ir#}}", report_ir_selector)
            .replace("{{#current_final_report#}}", markdown_selector)
        )

    nodes = [
        node(
            "start_node",
            "start",
            "Start",
            30,
            320,
            {
                "variables": [
                    {
                        "label": "公告 URL",
                        "max_length": 2048,
                        "options": [],
                        "required": True,
                        "type": "text-input",
                        "variable": "notice_url",
                    },
                    {
                        "label": "关联项目分析稿 Word（可选）",
                        "options": [],
                        "required": False,
                        "type": "file",
                        "variable": "history_report",
                        "allowed_file_types": ["document"],
                        "allowed_file_extensions": [".docx", ".doc"],
                        "allowed_file_upload_methods": ["local_file"],
                    },
                ]
            },
            height=132,
        ),
        code_node(
            "classify_turn",
            "Classify Turn",
            330,
            320,
            ROUTE_TURN_CODE,
            [
                {"value_selector": ["sys", "query"], "variable": "query"},
                {"value_selector": ["conversation", "current_report_ir"], "variable": "current_report_ir"},
                {"value_selector": ["conversation", "repair_attempt_count"], "variable": "repair_attempt_count"},
            ],
            {"route": STRING_OUTPUT},
        ),
        if_else_node(
            "route_turn",
            "Route Turn",
            630,
            320,
            [
                {
                    "case_id": "approve",
                    "logical_operator": "and",
                    "conditions": [condition(["classify_turn", "route"], "contains", "approve_download")],
                },
                {
                    "case_id": "first",
                    "logical_operator": "and",
                    "conditions": [condition(["classify_turn", "route"], "contains", "first_generate")],
                },
                {
                    "case_id": "repair_limit",
                    "logical_operator": "and",
                    "conditions": [condition(["classify_turn", "route"], "contains", "repair_limit")],
                },
            ],
        ),
        if_else_node(
            "history_file_gate",
            "History File?",
            630,
            420,
            [
                {
                    "case_id": "has_history",
                    "logical_operator": "and",
                    "conditions": [condition(["start_node", "history_report"], "exists", var_type="file")],
                }
            ],
        ),
        node(
            "history_doc_extractor",
            "document-extractor",
            "Extract History Word",
            930,
            270,
            {
                "is_array_file": False,
                "variable_selector": ["start_node", "history_report"],
            },
        ),
        llm_node(
            "summarize_history",
            "Summarize History",
            1230,
            270,
            [{"role": "user", "text": history_prompt}],
            fast_model_provider,
            fast_model_name,
            temperature=0.0,
        ),
        code_node(
            "parse_history",
            "Parse History Insights",
            1530,
            270,
            PARSE_HISTORY_CODE,
            [
                {"value_selector": ["summarize_history", "text"], "variable": "raw_history"},
                {"value_selector": ["history_doc_extractor", "text"], "variable": "extracted_text"},
            ],
            {"history_insights": STRING_OUTPUT},
        ),
        assigner_node("assign_history", "Save History", 1830, 270, {"history_insights": ["parse_history", "history_insights"]}),
        code_node("empty_history", "Clear History", 930, 540, BUILD_EMPTY_HISTORY_CODE, [], {"history_insights": STRING_OUTPUT}),
        assigner_node("assign_empty_history", "Save Empty History", 1230, 540, {"history_insights": ["empty_history", "history_insights"]}),
        http_node(
            "fetch_source",
            "Fetch Notice",
            2130,
            420,
            f"{backend_base_url}/analyze",
            '{"url":"{{#start_node.notice_url#}}","max_attachments":25,"max_combined_chars":60000}',
            read_timeout=180,
        ),
        code_node(
            "parse_source_evidence",
            "Parse Source Evidence",
            2430,
            420,
            PARSE_SOURCE_EVIDENCE_CODE,
            [{"value_selector": ["fetch_source", "body"], "variable": "fetch_body"}],
            {"evidence_text": STRING_OUTPUT},
        ),
        assigner_node("assign_source", "Save Evidence", 2730, 420, {"source_evidence": ["parse_source_evidence", "evidence_text"]}),
        llm_node(
            "generate_report",
            "Generate Report",
            3030,
            420,
            [{"role": "system", "text": system_prompt}, {"role": "user", "text": report_prompt}],
            model_provider,
            model_name,
            temperature=0.1,
        ),
        code_node("build_render_payload", "Build Render Payload", 3330, 420, BUILD_RENDER_PAYLOAD_CODE, [{"value_selector": ["generate_report", "text"], "variable": "report"}], PAYLOAD_OUTPUT),
        http_node("render_report", "Render Report", 3630, 420, f"{backend_base_url}/report/render", "{{#build_render_payload.payload#}}"),
        code_node(
            "parse_render",
            "Parse Render",
            3930,
            420,
            PARSE_RENDER_CODE,
            [{"value_selector": ["render_report", "body"], "variable": "render_body"}],
            {"report_ir": STRING_OUTPUT, "report_markdown": STRING_OUTPUT, "render_error": STRING_OUTPUT},
        ),
        llm_node(
            "qa_report_first",
            "QA Report",
            4230,
            420,
            [{"role": "user", "text": qa_prompt_for("{{#parse_render.report_ir#}}", "{{#parse_render.report_markdown#}}")}],
            fast_model_provider,
            fast_model_name,
            temperature=0.0,
        ),
        code_node(
            "build_qa_payload",
            "Build QA Payload",
            4530,
            420,
            BUILD_QA_PAYLOAD_CODE,
            [
                {"value_selector": ["qa_report_first", "text"], "variable": "qa_output"},
                {"value_selector": ["parse_render", "report_ir"], "variable": "report_ir"},
                {"value_selector": ["parse_render", "report_markdown"], "variable": "report_markdown"},
                {"value_selector": ["conversation", "source_evidence"], "variable": "evidence"},
                {"value_selector": ["conversation", "history_insights"], "variable": "history"},
            ],
            PAYLOAD_OUTPUT,
        ),
        http_node("parse_qa_first", "Parse QA", 4830, 420, f"{backend_base_url}/report/qa", "{{#build_qa_payload.payload#}}"),
        code_node(
            "read_qa_first",
            "Read QA",
            5130,
            420,
            PARSE_QA_CODE,
            [{"value_selector": ["parse_qa_first", "body"], "variable": "qa_body"}],
            {"qa_summary": STRING_OUTPUT, "qa_status": STRING_OUTPUT, "qa_body": STRING_OUTPUT},
        ),
        code_node("reset_repair_state", "Reset Repair State", 5280, 560, RESET_REPAIR_STATE_CODE, [], {"repair_attempt_count": STRING_OUTPUT}),
        assigner_node(
            "assign_initial_report",
            "Save Current Report",
            5430,
            420,
            {
                "current_report_ir": ["parse_render", "report_ir"],
                "current_final_report": ["parse_render", "report_markdown"],
                "last_qa_summary": ["read_qa_first", "qa_summary"],
                "repair_attempt_count": ["reset_repair_state", "repair_attempt_count"],
            },
        ),
        answer_node(
            "answer_initial",
            "Answer Report",
            5730,
            420,
            "{{#conversation.current_final_report#}}\n\n---\n\n质检摘要：\n{{#conversation.last_qa_summary#}}\n\nWord 下载将在你确认后生成。若需要调整报告，请直接提出修改意见；若认可，请回复“确认下载 Word”。",
        ),
        llm_node(
            "revise_report",
            "Revise Report",
            630,
            760,
            [{"role": "user", "text": revision_prompt}],
            model_provider,
            model_name,
            temperature=0.0,
        ),
        code_node("build_revised_render_payload", "Build Revised Render Payload", 930, 760, BUILD_RENDER_PAYLOAD_CODE, [{"value_selector": ["revise_report", "text"], "variable": "report"}], PAYLOAD_OUTPUT),
        http_node("render_revised_report", "Render Revised Report", 1230, 760, f"{backend_base_url}/report/render", "{{#build_revised_render_payload.payload#}}"),
        code_node(
            "parse_revised_render",
            "Parse Revised Render",
            1530,
            760,
            PARSE_REVISED_RENDER_CODE,
            [
                {"value_selector": ["render_revised_report", "body"], "variable": "render_body"},
                {"value_selector": ["conversation", "current_report_ir"], "variable": "previous_report_ir"},
                {"value_selector": ["conversation", "current_final_report"], "variable": "previous_report_markdown"},
            ],
            {
                "report_ir": STRING_OUTPUT,
                "report_markdown": STRING_OUTPUT,
                "render_error": STRING_OUTPUT,
                "render_success": STRING_OUTPUT,
                "revision_note": STRING_OUTPUT,
            },
        ),
        llm_node(
            "qa_revised_report",
            "QA Revised Report",
            1830,
            760,
            [{"role": "user", "text": qa_prompt_for("{{#parse_revised_render.report_ir#}}", "{{#parse_revised_render.report_markdown#}}")}],
            fast_model_provider,
            fast_model_name,
            temperature=0.0,
        ),
        code_node(
            "build_revised_qa_payload",
            "Build Revised QA Payload",
            2130,
            760,
            BUILD_QA_PAYLOAD_CODE,
            [
                {"value_selector": ["qa_revised_report", "text"], "variable": "qa_output"},
                {"value_selector": ["parse_revised_render", "report_ir"], "variable": "report_ir"},
                {"value_selector": ["parse_revised_render", "report_markdown"], "variable": "report_markdown"},
                {"value_selector": ["conversation", "source_evidence"], "variable": "evidence"},
                {"value_selector": ["conversation", "history_insights"], "variable": "history"},
            ],
            PAYLOAD_OUTPUT,
        ),
        http_node("parse_qa_revised", "Parse Revised QA", 2430, 760, f"{backend_base_url}/report/qa", "{{#build_revised_qa_payload.payload#}}"),
        code_node(
            "read_qa_revised",
            "Read Revised QA",
            2730,
            760,
            PARSE_QA_CODE,
            [{"value_selector": ["parse_qa_revised", "body"], "variable": "qa_body"}],
            {"qa_summary": STRING_OUTPUT, "qa_status": STRING_OUTPUT, "qa_body": STRING_OUTPUT},
        ),
        code_node(
            "update_repair_state",
            "Update Repair State",
            2880,
            900,
            UPDATE_REPAIR_STATE_CODE,
            [
                {"value_selector": ["read_qa_revised", "qa_status"], "variable": "qa_status"},
                {"value_selector": ["conversation", "repair_attempt_count"], "variable": "previous_repair_attempt_count"},
            ],
            {"repair_attempt_count": STRING_OUTPUT, "repair_guard_notice": STRING_OUTPUT},
        ),
        assigner_node(
            "assign_revised_report",
            "Save Revised Report",
            3030,
            760,
            {
                "current_report_ir": ["parse_revised_render", "report_ir"],
                "current_final_report": ["parse_revised_render", "report_markdown"],
                "last_qa_summary": ["read_qa_revised", "qa_summary"],
                "repair_attempt_count": ["update_repair_state", "repair_attempt_count"],
            },
        ),
        answer_node(
            "answer_revised",
            "Answer Revised Report",
            3330,
            760,
            "{{#parse_revised_render.revision_note#}}\n\n{{#conversation.current_final_report#}}\n\n---\n\n质检摘要：\n{{#conversation.last_qa_summary#}}{{#update_repair_state.repair_guard_notice#}}\n\nWord 下载仍需你确认后生成。",
        ),
        answer_node(
            "answer_repair_limit",
            "Answer Repair Limit",
            930,
            620,
            "已达到修复上限（连续 3 次 needs_fix）。请不要继续粘贴同类修复建议；建议重新生成，或人工核对附件证据后再处理。若认可当前报告，可回复“确认下载 Word”继续导出。",
        ),
        code_node(
            "build_export_payload",
            "Build Export Payload",
            630,
            80,
            BUILD_EXPORT_PAYLOAD_CODE,
            [
                {"value_selector": ["conversation", "current_report_ir"], "variable": "report_ir"},
                {"value_selector": ["conversation", "current_final_report"], "variable": "report_markdown"},
                {"value_selector": ["conversation", "source_evidence"], "variable": "evidence"},
                {"value_selector": ["conversation", "history_insights"], "variable": "history"},
            ],
            PAYLOAD_OUTPUT,
        ),
        http_node("export_word", "Export Word", 930, 80, f"{backend_base_url}/report/export_checked", "{{#build_export_payload.payload#}}"),
        code_node(
            "parse_export",
            "Parse Export",
            1230,
            80,
            PARSE_EXPORT_CODE,
            [{"value_selector": ["export_word", "body"], "variable": "export_body"}],
            {"answer": STRING_OUTPUT, "download_url": STRING_OUTPUT, "filename": STRING_OUTPUT, "qa_summary": STRING_OUTPUT},
        ),
        assigner_node("assign_export_state", "Save Export Filename", 1530, 80, {"last_export_filename": ["parse_export", "filename"]}),
        answer_node("answer_download", "Answer Download", 1830, 80, "{{#parse_export.answer#}}"),
    ]

    edges = [
        edge("start-to-classify", "start_node", "classify_turn", "start", "code"),
        edge("classify-to-route", "classify_turn", "route_turn", "code", "if-else"),
        edge("route_approve-to-export_word", "route_turn", "build_export_payload", "if-else", "code", source_handle="approve"),
        edge("route_first-to-history_file_gate", "route_turn", "history_file_gate", "if-else", "if-else", source_handle="first"),
        edge("route_repair_limit-to-answer", "route_turn", "answer_repair_limit", "if-else", "answer", source_handle="repair_limit"),
        edge("route_revise-to-revise_report", "route_turn", "revise_report", "if-else", "llm", source_handle="false"),
        edge("history-has-to-extract", "history_file_gate", "history_doc_extractor", "if-else", "document-extractor", source_handle="has_history"),
        edge("history-empty-to-clear", "history_file_gate", "empty_history", "if-else", "code", source_handle="false"),
        edge("extract-to-summarize", "history_doc_extractor", "summarize_history", "document-extractor", "llm"),
        edge("summarize-to-parse-history", "summarize_history", "parse_history", "llm", "code"),
        edge("parse-history-to-assign-history", "parse_history", "assign_history", "code", "assigner"),
        edge("empty-history-to-assign", "empty_history", "assign_empty_history", "code", "assigner"),
        edge("assign-history-to-fetch", "assign_history", "fetch_source", "assigner", "http-request"),
        edge("assign-empty-history-to-fetch", "assign_empty_history", "fetch_source", "assigner", "http-request"),
        edge("fetch-to-parse-source", "fetch_source", "parse_source_evidence", "http-request", "code"),
        edge("parse-source-to-assign-source", "parse_source_evidence", "assign_source", "code", "assigner"),
        edge("assign-source-to-generate", "assign_source", "generate_report", "assigner", "llm"),
        edge("generate-to-build-render", "generate_report", "build_render_payload", "llm", "code"),
        edge("build-render-to-render", "build_render_payload", "render_report", "code", "http-request"),
        edge("render-to-parse", "render_report", "parse_render", "http-request", "code"),
        edge("parse-to-qa", "parse_render", "qa_report_first", "code", "llm"),
        edge("qa-to-build-payload", "qa_report_first", "build_qa_payload", "llm", "code"),
        edge("qa-payload-to-parse", "build_qa_payload", "parse_qa_first", "code", "http-request"),
        edge("parse-qa-to-read", "parse_qa_first", "read_qa_first", "http-request", "code"),
        edge("read-qa-to-reset-repair", "read_qa_first", "reset_repair_state", "code", "code"),
        edge("reset-repair-to-assign", "reset_repair_state", "assign_initial_report", "code", "assigner"),
        edge("assign-to-answer-initial", "assign_initial_report", "answer_initial", "assigner", "answer"),
        edge("revise-to-build-render", "revise_report", "build_revised_render_payload", "llm", "code"),
        edge("build-revised-to-render", "build_revised_render_payload", "render_revised_report", "code", "http-request"),
        edge("render-revised-to-parse", "render_revised_report", "parse_revised_render", "http-request", "code"),
        edge("parse-revised-to-qa", "parse_revised_render", "qa_revised_report", "code", "llm"),
        edge("qa-revised-to-build", "qa_revised_report", "build_revised_qa_payload", "llm", "code"),
        edge("build-revised-qa-to-parse", "build_revised_qa_payload", "parse_qa_revised", "code", "http-request"),
        edge("parse-revised-qa-to-read", "parse_qa_revised", "read_qa_revised", "http-request", "code"),
        edge("read-revised-to-update-repair", "read_qa_revised", "update_repair_state", "code", "code"),
        edge("update-repair-to-assign", "update_repair_state", "assign_revised_report", "code", "assigner"),
        edge("assign-revised-to-answer", "assign_revised_report", "answer_revised", "assigner", "answer"),
        edge("export-payload-to-export", "build_export_payload", "export_word", "code", "http-request"),
        edge("export-to-parse", "export_word", "parse_export", "http-request", "code"),
        edge("parse-export-to-assign", "parse_export", "assign_export_state", "code", "assigner"),
        edge("assign-export-to-answer", "assign_export_state", "answer_download", "assigner", "answer"),
    ]

    return {
        "version": "0.6.0",
        "kind": "app",
        "app": {
            "name": app_name,
            "mode": "advanced-chat",
            "icon": "📄",
            "icon_background": "#E0F2FE",
            "icon_type": "emoji",
            "description": "首轮输入公告 URL，可选上传历史 Word；生成后支持用户反馈修订；用户确认后才导出 Word。",
            "use_icon_as_answer_icon": False,
        },
        "dependencies": [],
        "workflow": {
            "conversation_variables": [
                conversation_variable("source_evidence"),
                conversation_variable("history_insights"),
                conversation_variable("current_report_ir"),
                conversation_variable("current_final_report"),
                conversation_variable("last_qa_summary"),
                conversation_variable("repair_attempt_count"),
                conversation_variable("last_export_filename"),
            ],
            "environment_variables": [],
            "features": {
                "file_upload": {
                    "enabled": True,
                    "allowed_file_types": ["document"],
                    "allowed_file_extensions": [".docx", ".doc"],
                    "allowed_file_upload_methods": ["local_file"],
                    "number_limits": 1,
                    "fileUploadConfig": {
                        "file_size_limit": 15,
                        "batch_count_limit": 1,
                        "file_upload_limit": 1,
                        "workflow_file_upload_limit": 1,
                        "image_file_size_limit": 10,
                        "video_file_size_limit": 100,
                        "audio_file_size_limit": 50,
                        "image_file_batch_limit": 1,
                        "single_chunk_attachment_limit": 10,
                        "attachment_image_file_size_limit": 2,
                    },
                    "image": {"enabled": False, "number_limits": 0, "transfer_methods": []},
                },
                "opening_statement": "输入公告 URL，可选上传关联项目 Word。生成后可直接提出修改意见，确认后再导出 Word。",
                "suggested_questions": [],
                "suggested_questions_after_answer": {"enabled": False},
                "speech_to_text": {"enabled": False},
                "text_to_speech": {"enabled": False, "language": "", "voice": ""},
                "retriever_resource": {"enabled": False},
                "sensitive_word_avoidance": {"enabled": False},
            },
            "graph": {
                "nodes": nodes,
                "edges": edges,
                "viewport": {"x": 0, "y": 0, "zoom": 0.42},
            },
            "rag_pipeline_variables": [],
        },
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Build a Dify advanced-chat DSL for the medical notice analyzer.")
    parser.add_argument("--app-name", default=DEFAULT_APP_NAME)
    parser.add_argument("--model-provider", default=DEFAULT_MODEL_PROVIDER)
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--fast-model-provider", default=DEFAULT_FAST_MODEL_PROVIDER)
    parser.add_argument("--fast-model-name", default=DEFAULT_FAST_MODEL_NAME)
    parser.add_argument("--backend-base-url", default=DEFAULT_BACKEND_BASE_URL)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args()

    dsl = build_dsl(
        args.app_name,
        args.model_provider,
        args.model_name,
        args.fast_model_provider,
        args.fast_model_name,
        args.backend_base_url,
    )
    text = json.dumps(dsl, ensure_ascii=False, indent=2)

    if args.output:
        args.output.write_text(text, encoding="utf-8")
    if args.stdout or not args.output:
        print(text)


if __name__ == "__main__":
    main()
