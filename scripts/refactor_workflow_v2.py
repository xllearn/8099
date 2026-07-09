from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import yaml


SRC = Path(os.environ["WORKFLOW_SRC"])
OUT = Path(os.environ["WORKFLOW_OUT"])


def zh(value: str) -> str:
    return value.encode("utf-8").decode("unicode_escape")


def node_map(data: dict) -> dict[str, dict]:
    return {node["id"]: node for node in data["workflow"]["graph"]["nodes"]}


def edge(edge_id: str, source: str, target: str, source_type: str, target_type: str, handle: str = "source") -> dict:
    return {
        "data": {"isInIteration": False, "isInLoop": False, "sourceType": source_type, "targetType": target_type},
        "id": edge_id,
        "source": source,
        "sourceHandle": handle,
        "target": target,
        "targetHandle": "target",
        "type": "custom",
        "zIndex": 0,
    }


def condition(selector: list[str], value: object, var_type: str = "string") -> dict:
    return {
        "comparison_operator": "is",
        "id": str(uuid4()),
        "value": value,
        "varType": var_type,
        "variable_selector": selector,
    }


def base_node(node_id: str, title: str, kind: str, x: int, y: int, height: int = 95) -> dict:
    return {
        "data": {"desc": "", "selected": False, "title": title, "type": kind},
        "height": height,
        "id": node_id,
        "position": {"x": x, "y": y},
        "positionAbsolute": {"x": x, "y": y},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": 242,
    }


def code_node(node_id: str, title: str, code: str, variables: list[dict], outputs: dict, x: int, y: int) -> dict:
    node = base_node(node_id, title, "code", x, y)
    node["data"].update({"code": code, "code_language": "python3", "variables": variables, "outputs": outputs})
    return node


def if_node(node_id: str, title: str, cases: list[dict], x: int, y: int) -> dict:
    node = base_node(node_id, title, "if-else", x, y, 125 + max(0, len(cases) - 1) * 48)
    node["data"]["cases"] = cases
    return node


def add_or_replace(nodes: list[dict], by_id: dict[str, dict], new_node: dict) -> None:
    if new_node["id"] in by_id:
        old = by_id[new_node["id"]]
        old.clear()
        old.update(new_node)
    else:
        nodes.append(new_node)
        by_id[new_node["id"]] = new_node


def replace_prompt_evidence(node: dict, append_rule: bool) -> None:
    for item in node["data"].get("prompt_template", []):
        if item.get("role") != "user":
            continue
        text = str(item.get("text") or "")
        text = text.replace(
            "{{#fetch_node.body#}}",
            "{{#parse_analyze_node.llm_input_text#}}\n\nstructured evidence_pack:\n{{#parse_analyze_node.evidence_pack#}}",
        )
        if append_rule:
            text += (
                "\n\nHard rule: generate ReportIR only from evidence_pack and llm_input_text. "
                "Do not add facts from outside knowledge. If the source does not disclose a fact, write "
                "\"file not disclosed\", \"to be clarified by later notice\", or \"subject to official documents\"."
            )
        item["text"] = text


def remove_nodes_and_edges(graph: dict, remove_node_ids: set[str]) -> None:
    graph["nodes"][:] = [node for node in graph["nodes"] if node.get("id") not in remove_node_ids]
    graph["edges"][:] = [
        item
        for item in graph["edges"]
        if item.get("source") not in remove_node_ids and item.get("target") not in remove_node_ids
    ]


def ensure_edge(edges: list[dict], new_edge: dict) -> None:
    source = new_edge["source"]
    target = new_edge["target"]
    handle = new_edge.get("sourceHandle", "source")
    for item in edges:
        if item.get("source") == source and item.get("target") == target and item.get("sourceHandle", "source") == handle:
            return
    edges.append(new_edge)


def main() -> None:
    data = yaml.safe_load(SRC.read_text(encoding="utf-8"))
    data["version"] = "0.1.5"
    graph = data["workflow"]["graph"]
    nodes = graph["nodes"]
    edges = graph["edges"]

    # Keep the generated DSL conservative. These nodes were part of an earlier
    # aggressive branch rewrite and are intentionally stripped if the script is
    # run on that output instead of the original backup.
    remove_nodes_and_edges(
        graph,
        {
            "fetch_success_gate",
            "fetch_failed_message_node",
            "fetch_failed_end_node",
            "qa_status_gate",
            "qa_block_message_node",
            "qa_block_end_node",
            "build_export_payload_initial_node",
            "export_report_initial_node",
            "final_report_initial_node",
            "initial_export_end_node",
            "qa_second_status_gate",
            "qa_second_block_message_node",
            "qa_second_block_end_node",
        },
    )
    nodes = graph["nodes"]
    edges = graph["edges"]
    by_id = node_map(data)

    file_upload = data["workflow"].setdefault("features", {}).setdefault("file_upload", {})
    file_upload.update(
        {
            "enabled": True,
            "allowed_file_types": ["document"],
            "allowed_file_extensions": [".PDF", ".DOC", ".DOCX", ".XLS", ".XLSX", ".CSV"],
            "allowed_file_upload_methods": ["local_file"],
            "number_limits": 10,
        }
    )
    file_upload.setdefault("fileUploadConfig", {})["workflow_file_upload_limit"] = 10

    start = by_id["start_node"]
    variables = start["data"].setdefault("variables", [])
    if not any(item.get("variable") == "supplemental_files" for item in variables):
        variables.append(
            {
                "label": "Supplemental files",
                "options": [],
                "required": False,
                "type": "file",
                "variable": "supplemental_files",
                "allowed_file_types": ["document"],
                "allowed_file_extensions": [".PDF", ".DOC", ".DOCX", ".XLS", ".XLSX", ".CSV"],
                "allowed_file_upload_methods": ["local_file"],
            }
        )
    start["height"] = max(start.get("height", 108), 132)

    fetch = by_id["fetch_node"]
    fetch["data"]["url"] = "http://host.docker.internal:8099/analyze_v2"
    fetch["data"]["body"]["data"][0]["value"] = json.dumps(
        {"url": "{{#start_node.notice_url#}}", "max_attachments": 25, "max_combined_chars": 80000, "evidence_mode": True},
        ensure_ascii=False,
        separators=(",", ":"),
    )

    for node_id in ["report_node", "qa_report_node", "qa_fix_node", "qa_second_node"]:
        by_id[node_id]["data"].setdefault("model", {}).setdefault("completion_params", {})["temperature"] = 0
    replace_prompt_evidence(by_id["report_node"], True)
    for node_id in ["qa_report_node", "qa_fix_node", "qa_second_node"]:
        replace_prompt_evidence(by_id[node_id], False)

    for node_id in ["build_render_payload_node", "build_fixed_render_payload_node"]:
        by_id[node_id]["data"]["code"] = (
            "def main(report: str) -> dict:\n"
            "    import json\n"
            "    return {'payload': json.dumps({'markdown': report or '', 'strict_quality': True}, ensure_ascii=False)}\n"
        )

    parse_analyze_code = (
        "def main(fetch_body: str) -> dict:\n"
        "    import json\n"
        "    try:\n"
        "        data = json.loads(fetch_body or '{}')\n"
        "    except Exception as exc:\n"
        "        data = {'success': False, 'message': 'fetch result parse failed: ' + str(exc), 'evidence_pack': {}, 'llm_input_text': ''}\n"
        "    success = bool(data.get('success'))\n"
        "    evidence_obj = data.get('evidence_pack') or {}\n"
        "    evidence_pack = json.dumps(evidence_obj, ensure_ascii=False)\n"
        "    llm_input_text = data.get('llm_input_text') or evidence_pack\n"
        "    status = data.get('fetch_status') or {}\n"
        "    warnings = status.get('warnings') or evidence_obj.get('warnings') or []\n"
        "    message = data.get('message') or ''\n"
        "    if not success and not message:\n"
        "        message = 'Fetch or parse failed. Please confirm the URL is public or upload source files.'\n"
        "    fetch_summary = 'Fetch succeeded' if success else ('Fetch failed: ' + message)\n"
        "    if warnings:\n"
        "        fetch_summary += '\\n' + '\\n'.join('- ' + str(item) for item in warnings[:10])\n"
        "    return {'fetch_success': success, 'evidence_pack': evidence_pack, 'llm_input_text': llm_input_text, 'evidence_id': data.get('evidence_id') or '', 'error_type': data.get('error_type') or '', 'error_message': message, 'fetch_summary': fetch_summary}\n"
    )
    add_or_replace(
        nodes,
        by_id,
        code_node(
            "parse_analyze_node",
            "Parse analyze_v2",
            parse_analyze_code,
            [{"value_selector": ["fetch_node", "body"], "variable": "fetch_body"}],
            {
                "fetch_success": {"type": "boolean", "children": None},
                "evidence_pack": {"type": "string", "children": None},
                "llm_input_text": {"type": "string", "children": None},
                "evidence_id": {"type": "string", "children": None},
                "error_type": {"type": "string", "children": None},
                "error_message": {"type": "string", "children": None},
                "fetch_summary": {"type": "string", "children": None},
            },
            790,
            218,
        ),
    )
    export_code = (
        "def main(report_ir: str, report_markdown: str, qa_status: str, qa_summary: str, evidence: str) -> dict:\n"
        "    import json\n"
        "    try:\n"
        "        parsed_report_ir = json.loads(report_ir or '{}')\n"
        "    except Exception:\n"
        "        parsed_report_ir = None\n"
        "    payload = {'title': 'Medical Notice Report', 'markdown': '', 'report_ir': parsed_report_ir, 'report_text': report_markdown or report_ir or '', 'qa_status': qa_status or 'pass', 'qa_result': {'status': qa_status or 'pass', 'summary': qa_summary or ''}, 'strict_quality': True, 'evidence_text': evidence or ''}\n"
        "    return {'payload': json.dumps(payload, ensure_ascii=False)}\n"
    )

    by_id["build_export_payload_node"]["data"]["code"] = export_code
    by_id["build_export_payload_node"]["data"]["variables"] = [
        {"value_selector": ["parse_fixed_render_node", "report_ir"], "variable": "report_ir"},
        {"value_selector": ["parse_fixed_render_node", "report_markdown"], "variable": "report_markdown"},
        {"value_selector": ["parse_qa_second_node", "status"], "variable": "qa_status"},
        {"value_selector": ["parse_qa_second_node", "qa_summary"], "variable": "qa_summary"},
        {"value_selector": ["parse_analyze_node", "llm_input_text"], "variable": "evidence"},
    ]
    by_id["final_report_node"]["data"]["variables"] = [{"value_selector": ["parse_fixed_render_node", "report_markdown"], "variable": "rendered_report"}, {"value_selector": ["export_report_node", "body"], "variable": "export_body"}]

    for node_id in ["build_qa_payload_node", "build_qa2_payload_node"]:
        for variable in by_id[node_id]["data"].get("variables", []):
            if variable.get("variable") == "evidence":
                variable["value_selector"] = ["parse_analyze_node", "llm_input_text"]

    remove_pairs = {("fetch_node", "report_node")}
    edges[:] = [item for item in edges if (item.get("source"), item.get("target")) not in remove_pairs]
    ensure_edge(edges, edge("fetch-to-parse-analyze", "fetch_node", "parse_analyze_node", "http-request", "code"))
    ensure_edge(edges, edge("parse-analyze-to-report", "parse_analyze_node", "report_node", "code", "llm"))
    ensure_edge(edges, edge("parse-qa-to-fix", "parse_qa_node", "qa_fix_node", "http-request", "llm"))
    ensure_edge(edges, edge("parse-second-qa-to-build-export", "parse_qa_second_node", "build_export_payload_node", "http-request", "code"))
    ensure_edge(edges, edge("build-export-to-export", "build_export_payload_node", "export_report_node", "code", "http-request"))
    ensure_edge(edges, edge("export-to-final", "export_report_node", "final_report_node", "http-request", "code"))
    ensure_edge(edges, edge("final-to-end", "final_report_node", "end_node", "code", "end"))

    OUT.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")
    print(f"updated {OUT} nodes={len(nodes)} edges={len(edges)}")


if __name__ == "__main__":
    main()
