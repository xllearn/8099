from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "dify-db-backups" / "workflow-8216a411-graph-before.json"
OUTPUT = ROOT / "dify-db-backups" / "workflow-8216a411-graph-enhanced.json"


def read_prompt(name: str) -> str:
    return (ROOT / "prompts" / name).read_text(encoding="utf-8")


def disable_thinking(params: dict) -> None:
    params["thinking"] = False
    params.pop("reasoning_effort", None)


def main() -> None:
    graph = json.loads(BACKUP.read_text(encoding="utf-8-sig").strip())

    system_prompt = read_prompt("report_system_prompt.md")
    user_prompt = read_prompt("report_user_prompt.md")
    qa_prompt = read_prompt("report_qa_prompt.md")
    qa_fix_prompt = read_prompt("report_qa_fix_prompt.md")

    user_prompt_workflow = user_prompt.replace("{{#history_insights#}}", "未提供历史分析稿。")
    qa_prompt_first = (
        qa_prompt.replace("{{#conversation.source_evidence#}}", "{{#fetch_node.body#}}")
        .replace("{{#conversation.history_insights#}}", "未提供历史分析稿。")
        .replace("{{#current_report_ir#}}", "{{#parse_render_node.report_ir#}}")
        .replace("{{#current_final_report#}}", "{{#parse_render_node.report_markdown#}}")
    )
    qa_fix_prompt_workflow = (
        qa_fix_prompt.replace("{{#conversation.source_evidence#}}", "{{#fetch_node.body#}}")
        .replace("{{#conversation.history_insights#}}", "未提供历史分析稿。")
        .replace("{{#current_report_ir#}}", "{{#parse_render_node.report_ir#}}")
        .replace("{{#current_final_report#}}", "{{#parse_render_node.report_markdown#}}")
        .replace("{{#qa_result#}}", "{{#qa_report_node.text#}}")
    )
    qa_prompt_second = (
        qa_prompt.replace("{{#conversation.source_evidence#}}", "{{#fetch_node.body#}}")
        .replace("{{#conversation.history_insights#}}", "未提供历史分析稿。")
        .replace("{{#current_report_ir#}}", "{{#parse_fixed_render_node.report_ir#}}")
        .replace("{{#current_final_report#}}", "{{#parse_fixed_render_node.report_markdown#}}")
    )

    nodes = {node["id"]: node for node in graph["nodes"]}
    report_node = nodes["report_node"]
    report_node["data"]["prompt_template"] = [
        {"role": "system", "text": system_prompt},
        {"role": "user", "text": user_prompt_workflow},
    ]
    report_node["data"]["desc"] = "根据公告正文、附件证据和 ReportIR 规则生成初版分析报告"
    report_params = report_node["data"]["model"].setdefault("completion_params", {})
    report_params["temperature"] = 0.1
    disable_thinking(report_params)
    nodes["fetch_node"]["data"]["body"]["data"][0]["value"] = (
        '{"url":"{{#start_node.notice_url#}}","max_attachments":25,"max_combined_chars":80000}'
    )

    base_llm = deepcopy(report_node)

    def make_llm(node_id: str, title: str, desc: str, x: int, y: int, prompt_text: str, temperature: float = 0.0) -> dict:
        node = deepcopy(base_llm)
        node["id"] = node_id
        node["data"]["title"] = title
        node["data"]["desc"] = desc
        node["data"]["selected"] = False
        node["data"]["prompt_template"] = [{"role": "user", "text": prompt_text}]
        node["data"]["model"]["completion_params"]["temperature"] = temperature
        disable_thinking(node["data"]["model"]["completion_params"])
        node["position"] = {"x": x, "y": y}
        node["positionAbsolute"] = {"x": x, "y": y}
        node["height"] = 120
        node["width"] = 244
        return node

    base_http = deepcopy(nodes["export_report_node"])

    def make_http(node_id: str, title: str, desc: str, x: int, y: int, url: str, payload_selector: str) -> dict:
        node = deepcopy(base_http)
        node["id"] = node_id
        node["data"]["title"] = title
        node["data"]["desc"] = desc
        node["data"]["url"] = url
        node["data"]["body"]["data"][0]["value"] = payload_selector
        node["position"] = {"x": x, "y": y}
        node["positionAbsolute"] = {"x": x, "y": y}
        node["height"] = 155
        node["width"] = 244
        return node

    base_code = deepcopy(nodes["build_export_payload_node"])

    def make_code(
        node_id: str,
        title: str,
        desc: str,
        x: int,
        y: int,
        code: str,
        variables: list[dict],
        outputs: dict,
    ) -> dict:
        node = deepcopy(base_code)
        node["id"] = node_id
        node["data"]["title"] = title
        node["data"]["desc"] = desc
        node["data"]["code"] = code
        node["data"]["variables"] = variables
        node["data"]["outputs"] = outputs
        node["position"] = {"x": x, "y": y}
        node["positionAbsolute"] = {"x": x, "y": y}
        node["height"] = 120
        node["width"] = 244
        return node

    string_output = {"payload": {"children": None, "type": "string"}}
    render_output = {
        "report_ir": {"children": None, "type": "string"},
        "report_markdown": {"children": None, "type": "string"},
        "render_error": {"children": None, "type": "string"},
    }
    build_render_payload_code = """def main(report: str) -> dict:
    import json
    payload = {
        "markdown": report or "",
        "strict_quality": False,
    }
    return {"payload": json.dumps(payload, ensure_ascii=False)}
"""
    parse_render_body_code = """def main(render_body: str) -> dict:
    import json
    try:
        data = json.loads(render_body or "{}")
    except Exception:
        data = {"success": False, "error": "报告渲染接口返回非 JSON"}
    success = bool(data.get("success"))
    report_ir = data.get("report_ir") or {}
    report_markdown = data.get("report_markdown") or ""
    error = data.get("error") or ""
    if not success and not report_markdown:
        report_markdown = "报告解析失败：" + (error or "ReportIR 解析失败")
    return {
        "report_ir": json.dumps(report_ir, ensure_ascii=False),
        "report_markdown": report_markdown,
        "render_error": error,
    }
"""
    build_qa_payload_code = """def main(qa_output: str, report: str, evidence: str) -> dict:
    import json
    payload = {
        "qa_output": qa_output or "",
        "report_text": report or "",
        "history_text": "",
        "evidence_text": evidence or "",
    }
    return {"payload": json.dumps(payload, ensure_ascii=False)}
"""
    build_export_payload_code = """def main(report_ir: str, qa_output: str, report_markdown: str, evidence: str) -> dict:
    import json
    try:
        parsed_report_ir = json.loads(report_ir or "{}")
    except Exception:
        parsed_report_ir = None
    payload = {
        "title": "医药公告采购分析报告",
        "markdown": "",
        "report_ir": parsed_report_ir,
        "strict_quality": False,
        "qa_output": qa_output or "",
        "report_text": report_markdown or report_ir or "",
        "history_text": "",
        "evidence_text": evidence or "",
    }
    return {"payload": json.dumps(payload, ensure_ascii=False)}
"""
    final_code = r'''def main(rendered_report: str, export_body: str) -> dict:
    import json
    final = rendered_report or ""
    download_url = ""
    qa_summary = ""
    success = True
    blocked = False
    try:
        data = json.loads(export_body or "{}")
        download_url = data.get("download_url", "") or ""
        qa_summary = data.get("qa_summary", "") or ""
        final = data.get("report_markdown", "") or final
        success = bool(data.get("success", True))
        blocked = bool(data.get("blocked", False))
    except Exception:
        pass
    if qa_summary:
        final = final + "\n\n---\n\n质检摘要：\n" + qa_summary
    if success and download_url:
        final = final + "\n\n---\n\n[下载分析报告 Word 文件](" + download_url + ")"
    elif blocked:
        final = "本次报告未导出 Word，需人工确认以下问题：\n\n" + (qa_summary or "质检未通过。") + "\n\n---\n\n" + final
    return {"final_report": final, "download_url": download_url}
'''

    new_nodes = [
        make_code(
            "build_render_payload_node",
            "构造报告解析参数",
            "把初版模型输出打包给本地 ReportIR 解析渲染接口",
            840,
            350,
            build_render_payload_code,
            [{"value_selector": ["report_node", "text"], "variable": "report"}],
            string_output,
        ),
        make_http(
            "render_report_node",
            "解析并渲染报告",
            "解析 ReportIR，返回规范化 ReportIR 和 Dify Markdown 正文",
            1090,
            142,
            "http://host.docker.internal:8099/report/render",
            "{{#build_render_payload_node.payload#}}",
        ),
        make_code(
            "parse_render_node",
            "读取渲染结果",
            "从渲染接口响应中提取规范化 ReportIR 和 Markdown",
            1340,
            350,
            parse_render_body_code,
            [{"value_selector": ["render_report_node", "body"], "variable": "render_body"}],
            render_output,
        ),
        make_llm("qa_report_node", "报告质检", "核对报告是否忠实于原文并输出固定 JSON", 1590, 142, qa_prompt_first),
        make_code(
            "build_qa_payload_node",
            "构造质检解析参数",
            "把质检 JSON、报告和原文证据打包给本地质检解析接口",
            1840,
            350,
            build_qa_payload_code,
            [
                {"value_selector": ["qa_report_node", "text"], "variable": "qa_output"},
                {"value_selector": ["parse_render_node", "report_markdown"], "variable": "report"},
                {"value_selector": ["fetch_node", "body"], "variable": "evidence"},
            ],
            string_output,
        ),
        make_http(
            "parse_qa_node",
            "解析质检结果",
            "调用本地质检解析服务，识别阻断问题和历史泄漏",
            2090,
            142,
            "http://host.docker.internal:8099/report/qa",
            "{{#build_qa_payload_node.payload#}}",
        ),
        make_llm("qa_fix_node", "质检修复", "按质检意见进行一次最小修复；无问题则原样返回", 2340, 350, qa_fix_prompt_workflow),
        make_code(
            "build_fixed_render_payload_node",
            "构造修复后解析参数",
            "把修复后 ReportIR 打包给本地解析渲染接口",
            2590,
            350,
            build_render_payload_code,
            [{"value_selector": ["qa_fix_node", "text"], "variable": "report"}],
            string_output,
        ),
        make_http(
            "render_fixed_report_node",
            "解析修复后报告",
            "解析修复后的 ReportIR，返回规范化 ReportIR 和 Markdown",
            2840,
            142,
            "http://host.docker.internal:8099/report/render",
            "{{#build_fixed_render_payload_node.payload#}}",
        ),
        make_code(
            "parse_fixed_render_node",
            "读取修复后渲染结果",
            "从修复后渲染响应中提取规范化 ReportIR 和 Markdown",
            3090,
            350,
            parse_render_body_code,
            [{"value_selector": ["render_fixed_report_node", "body"], "variable": "render_body"}],
            render_output,
        ),
        make_llm("qa_second_node", "二次质检", "对修复后的报告再次质检", 3340, 142, qa_prompt_second),
        make_code(
            "build_qa2_payload_node",
            "构造二次质检解析参数",
            "把二次质检 JSON、修复后报告和原文证据打包",
            3590,
            350,
            build_qa_payload_code,
            [
                {"value_selector": ["qa_second_node", "text"], "variable": "qa_output"},
                {"value_selector": ["parse_fixed_render_node", "report_markdown"], "variable": "report"},
                {"value_selector": ["fetch_node", "body"], "variable": "evidence"},
            ],
            string_output,
        ),
        make_http(
            "parse_qa_second_node",
            "解析二次质检结果",
            "解析二次质检；若仍有严重问题，导出接口将不生成 Word",
            3840,
            142,
            "http://host.docker.internal:8099/report/qa",
            "{{#build_qa2_payload_node.payload#}}",
        ),
    ]

    nodes["build_export_payload_node"]["position"] = {"x": 4090, "y": 350}
    nodes["build_export_payload_node"]["positionAbsolute"] = {"x": 4090, "y": 350}
    nodes["build_export_payload_node"]["data"]["code"] = build_export_payload_code
    nodes["build_export_payload_node"]["data"]["variables"] = [
        {"value_selector": ["parse_fixed_render_node", "report_ir"], "variable": "report_ir"},
        {"value_selector": ["qa_second_node", "text"], "variable": "qa_output"},
        {"value_selector": ["parse_fixed_render_node", "report_markdown"], "variable": "report_markdown"},
        {"value_selector": ["fetch_node", "body"], "variable": "evidence"},
    ]
    nodes["export_report_node"]["position"] = {"x": 4340, "y": 142}
    nodes["export_report_node"]["positionAbsolute"] = {"x": 4340, "y": 142}
    nodes["export_report_node"]["data"]["url"] = "http://host.docker.internal:8099/report/export_checked"
    nodes["export_report_node"]["data"]["title"] = "质检通过后导出 Word"
    nodes["export_report_node"]["data"]["desc"] = "质检阻断时不生成 Word，只返回问题摘要；通过时返回下载链接"
    nodes["final_report_node"]["position"] = {"x": 4590, "y": 350}
    nodes["final_report_node"]["positionAbsolute"] = {"x": 4590, "y": 350}
    nodes["final_report_node"]["data"]["code"] = final_code
    nodes["final_report_node"]["data"]["variables"] = [
        {"value_selector": ["parse_fixed_render_node", "report_markdown"], "variable": "rendered_report"},
        {"value_selector": ["export_report_node", "body"], "variable": "export_body"},
    ]
    nodes["end_node"]["position"] = {"x": 4840, "y": 142}
    nodes["end_node"]["positionAbsolute"] = {"x": 4840, "y": 142}

    existing_ids = {node["id"] for node in graph["nodes"]}
    for node in new_nodes:
        if node["id"] not in existing_ids:
            graph["nodes"].append(node)

    edge_specs = [
        ("start-to-fetch", "start_node", "fetch_node", "start", "http-request"),
        ("fetch-to-report", "fetch_node", "report_node", "http-request", "llm"),
        ("report-to-build-render-payload", "report_node", "build_render_payload_node", "llm", "code"),
        ("build-render-payload-to-render", "build_render_payload_node", "render_report_node", "code", "http-request"),
        ("render-to-parse-render", "render_report_node", "parse_render_node", "http-request", "code"),
        ("parse-render-to-qa", "parse_render_node", "qa_report_node", "code", "llm"),
        ("qa-to-build-qa-payload", "qa_report_node", "build_qa_payload_node", "llm", "code"),
        ("build-qa-payload-to-parse", "build_qa_payload_node", "parse_qa_node", "code", "http-request"),
        ("parse-qa-to-fix", "parse_qa_node", "qa_fix_node", "http-request", "llm"),
        ("fix-to-build-fixed-render-payload", "qa_fix_node", "build_fixed_render_payload_node", "llm", "code"),
        ("build-fixed-render-payload-to-render", "build_fixed_render_payload_node", "render_fixed_report_node", "code", "http-request"),
        ("render-fixed-to-parse", "render_fixed_report_node", "parse_fixed_render_node", "http-request", "code"),
        ("parse-fixed-render-to-second-qa", "parse_fixed_render_node", "qa_second_node", "code", "llm"),
        ("second-qa-to-build-payload", "qa_second_node", "build_qa2_payload_node", "llm", "code"),
        ("build-qa2-payload-to-parse", "build_qa2_payload_node", "parse_qa_second_node", "code", "http-request"),
        ("parse-second-qa-to-export-payload", "parse_qa_second_node", "build_export_payload_node", "http-request", "code"),
        ("payload-to-export", "build_export_payload_node", "export_report_node", "code", "http-request"),
        ("export-to-final", "export_report_node", "final_report_node", "http-request", "code"),
        ("final-to-end", "final_report_node", "end_node", "code", "end"),
    ]
    graph["edges"] = [
        {
            "id": edge_id,
            "source": source,
            "sourceHandle": "source",
            "target": target,
            "targetHandle": "target",
            "type": "custom",
            "zIndex": 0,
            "data": {"isInIteration": False, "isInLoop": False, "sourceType": source_type, "targetType": target_type},
        }
        for edge_id, source, target, source_type, target_type in edge_specs
    ]
    graph["viewport"] = {"x": 0, "y": 0, "zoom": 0.45}
    OUTPUT.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUTPUT}")
    print(f"nodes={len(graph['nodes'])} edges={len(graph['edges'])}")


if __name__ == "__main__":
    main()
