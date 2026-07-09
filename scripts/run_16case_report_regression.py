from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://192.168.34.88:8099"
TERMINAL_STATUSES = {"finished", "failed", "needs_manual_review"}
STOPWORDS = set(
    "关于 通知 公告 公开 征求 意见 医用 耗材 医疗 药品 采购 项目 分析 工作 开展 广东 河南 吉林 天津 江苏 宁夏 重庆 新疆 甘肃 省 市 自治区 的 和 与 有关 相关".split()
)

ORIGINAL_CASES: list[dict[str, Any]] = [
    {
        "id": "case01_1p0_original_guangdong_mzgl",
        "combo": "1+0",
        "name": "广东麻醉管路三类耗材带量联动采购文件原始公告",
        "primary": [{"menu_code": "project_information", "articleid": "28296323-9aa5-4fa0-81c0-36f6c3e18adc"}],
        "auxiliary": [],
        "manual_keywords": ["广东", "麻醉管路"],
        "reason": "保留原 8 组：验证 Dify 缺少正文时的后端修复能力。",
    },
    {
        "id": "case02_1p0_original_henan_flow",
        "combo": "1+0",
        "name": "河南调整医用耗材申报挂网操作流程原始公告",
        "primary": [{"menu_code": "project_information", "articleid": "9fd07cf1-9360-455e-871c-578c3d270a72"}],
        "auxiliary": [],
        "manual_keywords": ["河南", "申报挂网操作流程"],
        "reason": "保留原 8 组：验证短正文流程公告的日期和规则支撑。",
    },
    {
        "id": "case03_1p0_project_analysis_jilin",
        "combo": "1+0",
        "name": "吉林体外诊断试剂三省挂网价格 project_analysis 替代样本",
        "primary": [{"menu_code": "project_analysis", "articleid": "984b429c-4799-4ea5-b94a-3f2f768c3e0e"}],
        "auxiliary": [],
        "manual_keywords": ["吉林", "三省挂网价格"],
        "reason": "保留原 8 组：验证片段化输出修复，使用用户允许的 project_analysis 替代对比。",
    },
    {
        "id": "case04_1p0_project_analysis_jiangsu",
        "combo": "1+0",
        "name": "江苏药品和医用耗材挂网价格专项治理 project_analysis 替代样本",
        "primary": [{"menu_code": "project_analysis", "articleid": "43fad507-2f99-4645-b65b-a2f629abcc0f"}],
        "auxiliary": [],
        "manual_keywords": ["江苏", "挂网价格专项治理"],
        "reason": "保留原 8 组：验证质检 JSON 稳定性和药品/耗材治理类分析。",
    },
    {
        "id": "case05_1p1_tianjin_with_ningxia_aux",
        "combo": "1+1",
        "name": "天津耗材挂网采购征求意见 + 宁夏挂网采购实施方案辅助",
        "primary": [{"menu_code": "project_analysis", "articleid": "1c93cac2-5f9a-4f7e-9d60-c7a69b8ae792"}],
        "auxiliary": [{"menu_code": "project_analysis", "articleid": "067ef84a-f5c2-4c27-aa21-84a8d72ed540"}],
        "manual_keywords": ["天津", "宁夏"],
        "reason": "保留原 8 组：验证 1+n 主辅边界。",
    },
    {
        "id": "case06_1p2_guangdong_bone_with_aux",
        "combo": "1+2",
        "name": "广东骨科集采数据核对 + 新疆/重庆辅助",
        "primary": [{"menu_code": "project_analysis", "articleid": "ffe7b586-afc7-4eec-9ea9-211d4172bde9"}],
        "auxiliary": [
            {"menu_code": "project_analysis", "articleid": "5b546741-d2d4-49a9-a25d-84f7542ff121"},
            {"menu_code": "project_analysis", "articleid": "f9f3bd85-ef12-4eda-9b03-98e52d583da1"},
        ],
        "manual_keywords": ["广东", "骨科", "新疆", "重庆"],
        "reason": "保留原 8 组：验证多辅助材料不污染主结论。",
    },
    {
        "id": "case07_2p0_jilin_tianjin",
        "combo": "2+0",
        "name": "吉林三省挂网价格 + 天津耗材挂网采购征求意见",
        "primary": [
            {"menu_code": "project_analysis", "articleid": "984b429c-4799-4ea5-b94a-3f2f768c3e0e"},
            {"menu_code": "project_analysis", "articleid": "1c93cac2-5f9a-4f7e-9d60-c7a69b8ae792"},
        ],
        "auxiliary": [],
        "manual_keywords": ["吉林", "天津"],
        "reason": "保留原 8 组：验证 2+0 双主材料结构。",
    },
    {
        "id": "case08_2p2_guangdong_henan_aux_gansu_chongqing",
        "combo": "2+2",
        "name": "广东麻醉管路 + 河南挂网流程，辅以甘肃/重庆",
        "primary": [
            {"menu_code": "project_information", "articleid": "28296323-9aa5-4fa0-81c0-36f6c3e18adc"},
            {"menu_code": "project_information", "articleid": "9fd07cf1-9360-455e-871c-578c3d270a72"},
        ],
        "auxiliary": [
            {"menu_code": "project_analysis", "articleid": "877ad10a-cc28-4fc1-8d90-5e489045c5db"},
            {"menu_code": "project_analysis", "articleid": "f9f3bd85-ef12-4eda-9b03-98e52d583da1"},
        ],
        "manual_keywords": ["广东", "麻醉管路", "河南", "甘肃", "重庆"],
        "reason": "保留原 8 组：验证 2+n 复杂组合和兜底质量。",
    },
]


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def json(self, method: str, path: str, payload: Any | None = None, timeout: int = 60) -> dict[str, Any]:
        data = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def bytes(self, path: str, timeout: int = 60) -> bytes:
        with urllib.request.urlopen(self.base_url + path, timeout=timeout) as response:
            return response.read()


def safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", value)
    return text.strip("_")[:90] or "case"


def extract_report_markdown(body: bytes) -> str:
    data = json.loads(body.decode("utf-8"))
    return str(data.get("report_markdown") or "")


def markdown_visible_text(text: str) -> str:
    value = re.sub(r"<[^>]+>", "", text or "")
    value = re.sub(r"```.*?```", " ", value, flags=re.S)
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"[#>*_`~-]+", " ", value)
    value = value.replace("|", " ")
    return re.sub(r"\s+", " ", value).strip()


def tokens(text: str) -> list[str]:
    visible = markdown_visible_text(text)
    values = re.findall(r"[A-Za-z0-9_]{3,}|[\u4e00-\u9fff]{2,}", visible)
    return [item for item in values if item not in STOPWORDS]


def docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            names = ["word/document.xml"] + [name for name in zf.namelist() if name.startswith(("word/header", "word/footer"))]
            parts = []
            for name in names:
                if name not in zf.namelist():
                    continue
                xml = zf.read(name).decode("utf-8", errors="ignore")
                xml = re.sub(r"<w:tab\b[^>]*/>", " ", xml)
                xml = re.sub(r"</w:p>", "\n", xml)
                text = re.sub(r"<[^>]+>", "", xml)
                text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
                parts.append(text)
            return re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip()
    except Exception as exc:  # noqa: BLE001
        return f"[DOCX_PARSE_FAILED: {exc.__class__.__name__}]"


def reference_file_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return docx_text(path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="gb18030", errors="ignore")
    except Exception as exc:  # noqa: BLE001
        return f"[REFERENCE_PARSE_FAILED: {exc.__class__.__name__}]"


def clean_reference_text(text: str) -> str:
    value = text or ""
    value = re.sub(r"\bPAGE\s+\d+\b", " ", value, flags=re.I)
    value = re.sub(r"\bMERGEFORMAT\b", " ", value, flags=re.I)
    disclaimer_patterns = [
        r"本文基于互联网公开资料进行整理.*?(?:\n|$)",
        r"本网站不保证信息的准确性.*?(?:\n|$)",
        r"本公司及其雇员一概毋须.*?(?:\n|$)",
    ]
    for pattern in disclaimer_patterns:
        value = re.sub(pattern, " ", value, flags=re.S)
    return re.sub(r"\s+", " ", value).strip()


def build_quality_eval(
    case_id: str,
    diagnostics: dict[str, Any],
    run_status: str = "",
    run_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    diag = diagnostics.get("diagnostics") if isinstance(diagnostics.get("diagnostics"), dict) else diagnostics
    gate = diag.get("quality_gate") if isinstance(diag.get("quality_gate"), dict) else {}
    state = run_state if isinstance(run_state, dict) else {}
    state_gate = state.get("quality_gate") if isinstance(state.get("quality_gate"), dict) else {}
    if not gate and state_gate:
        gate = state_gate
    coverage = diag.get("coverage") if isinstance(diag.get("coverage"), dict) else {}
    evidence = diag.get("evidence") if isinstance(diag.get("evidence"), dict) else {}
    blocking = gate.get("blocking_issues") if isinstance(gate.get("blocking_issues"), list) else []
    if not blocking and isinstance(gate.get("blocking_issue_codes"), list):
        blocking = [
            {
                "code": str(code),
                "level": "error",
                "message": str(state.get("error_message") or state.get("dify_error_message") or code),
            }
            for code in gate.get("blocking_issue_codes", [])
            if str(code).strip()
        ]
    deliverable_status = str(gate.get("deliverable_status") or "")
    if not deliverable_status and run_status == "failed":
        deliverable_status = "failed"
        blocking = [
            {
                "code": "RUN_FAILED",
                "level": "error",
                "message": "分析任务失败，未生成可交付报告。",
            },
            {
                "code": "DIAGNOSTICS_UNAVAILABLE",
                "level": "error",
                "message": "诊断信息不可用或获取超时，需人工排查失败原因。",
            },
        ]
    elif not deliverable_status and run_status == "needs_manual_review":
        deliverable_status = "needs_manual_review"

    dify_error_code = str(state.get("dify_error_code") or "").strip()
    if dify_error_code and all(str(item.get("code") or "") != dify_error_code for item in blocking if isinstance(item, dict)):
        blocking.append(
            {
                "code": dify_error_code,
                "level": "error",
                "message": str(state.get("error_message") or state.get("dify_error_message") or dify_error_code),
            }
        )
    source_fidelity_score = gate.get("source_fidelity_score")
    unsupported_fact_count = gate.get("unsupported_fact_count")
    analysis_depth_score = gate.get("analysis_depth_score")
    evidence_backed_analysis_count = gate.get("evidence_backed_analysis_count")
    if dify_error_code:
        source_fidelity_score = 0 if source_fidelity_score is None else source_fidelity_score
        unsupported_fact_count = 0 if unsupported_fact_count is None else unsupported_fact_count
        analysis_depth_score = 0 if analysis_depth_score is None else analysis_depth_score
        evidence_backed_analysis_count = 0 if evidence_backed_analysis_count is None else evidence_backed_analysis_count
    return {
        "case_id": case_id,
        "deliverable_status": deliverable_status,
        "source_fidelity_score": source_fidelity_score,
        "unsupported_fact_count": unsupported_fact_count,
        "analysis_depth_score": analysis_depth_score,
        "summary_only_risk": gate.get("summary_only_risk"),
        "evidence_backed_analysis_count": evidence_backed_analysis_count,
        "coverage_score": coverage.get("coverage_score"),
        "input_strategy": evidence.get("input_strategy"),
        "generation_mode": evidence.get("generation_mode"),
        "target_report_length": evidence.get("target_report_length"),
        "blocking_issue_codes": [str(item.get("code") or "") for item in blocking if isinstance(item, dict)],
        "blocking_issues": blocking,
    }


def reference_texts_for_case(case: dict[str, Any], manual_dir: Path) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    exact_files = [Path(item) for item in case.get("manual_files") or [] if str(item).strip()]
    for path in exact_files:
        candidate = path if path.is_absolute() else manual_dir / path
        if candidate.exists():
            results.append((str(candidate), clean_reference_text(reference_file_text(candidate))))
    if results:
        return results
    if manual_dir.exists():
        for path in manual_dir.glob("*.docx"):
            if any(keyword and keyword in path.name for keyword in case.get("manual_keywords", [])):
                results.append((str(path), clean_reference_text(docx_text(path))))
    return results


def compare_with_reference(case: dict[str, Any], report_markdown: str, manual_dir: Path) -> dict[str, Any]:
    refs = reference_texts_for_case(case, manual_dir)
    reference = "\n\n".join(text for _, text in refs)
    report_tokens = Counter(tokens(report_markdown))
    reference_tokens = Counter(tokens(reference))
    overlap = sum((report_tokens & reference_tokens).values())
    union = sum((report_tokens | reference_tokens).values()) or 1
    return {
        "manual_files": [name for name, _ in refs],
        "system_visible_chars": len(markdown_visible_text(report_markdown)),
        "manual_visible_chars": len(markdown_visible_text(reference)),
        "token_overlap_ratio": round(overlap / union, 4),
        "manual_high_frequency_terms_missing_in_system": [item for item, _ in reference_tokens.most_common(80) if item not in report_tokens][:30],
        "system_high_frequency_terms_not_in_manual": [item for item, _ in report_tokens.most_common(80) if item not in reference_tokens][:30],
    }


def record_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item.get("menu_code") or ""), str(item.get("articleid") or "")


def fetch_record_detail(client: ApiClient, ref: dict[str, str]) -> dict[str, Any]:
    menu = urllib.parse.quote(str(ref["menu_code"]), safe="")
    article = urllib.parse.quote(str(ref["articleid"]), safe="")
    return client.json("GET", f"/records/{menu}/{article}", timeout=60)


def enrich_material(client: ApiClient, ref: dict[str, str]) -> dict[str, Any]:
    detail = fetch_record_detail(client, ref)
    content = str(detail.get("content_text") or detail.get("content") or "")
    return {
        "table_name": ref["menu_code"],
        "menu_code": ref["menu_code"],
        "articleid": ref["articleid"],
        "title": detail.get("title", ""),
        "areaname": detail.get("areaname", ""),
        "projecttype": detail.get("projecttype", ""),
        "category": detail.get("category", ""),
        "content_chars": len(markdown_visible_text(content)),
        "attachment_count": len(detail.get("attachments") or []),
        "has_history_analysis": ref["menu_code"] == "project_analysis",
    }


def collect_candidates(client: ApiClient, used: set[tuple[str, str]], keyword: str = "", max_pages: int = 4) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for page in range(1, max_pages + 1):
        query = {"page": page, "page_size": 50, "menu_code": "project_information"}
        if keyword:
            query["keyword"] = keyword
        path = "/records?" + urllib.parse.urlencode(query)
        data = client.json("GET", path, timeout=60)
        for item in data.get("items") or []:
            key = record_key(item)
            if key in used or key in seen or not key[0] or not key[1]:
                continue
            seen.add(key)
            ref = {"menu_code": key[0], "articleid": key[1]}
            try:
                meta = enrich_material(client, ref)
            except Exception:  # noqa: BLE001
                continue
            meta["ref"] = ref
            items.append(meta)
    items.sort(key=lambda item: (item.get("content_chars", 0), item.get("attachment_count", 0), item.get("title", "")), reverse=True)
    return items


def choose(candidates: list[dict[str, Any]], predicate, fallback_index: int = 0) -> dict[str, Any]:
    for item in candidates:
        if predicate(item):
            return item
    if not candidates:
        raise RuntimeError("no candidates available for sampling")
    return candidates[min(fallback_index, len(candidates) - 1)]


def build_additional_cases(client: ApiClient, used: set[tuple[str, str]]) -> list[dict[str, Any]]:
    general = collect_candidates(client, used, "", max_pages=5)
    drug = collect_candidates(client, used, "药品", max_pages=3)
    consumable = collect_candidates(client, used, "耗材", max_pages=3)
    listing = collect_candidates(client, used, "挂网", max_pages=3)

    def mark_used(*items: dict[str, Any]) -> None:
        for item in items:
            used.add(record_key(item["ref"]))

    case09 = choose(general, lambda item: item["content_chars"] >= 2000 and item["attachment_count"] <= 1)
    mark_used(case09)
    case10 = choose([item for item in general if record_key(item["ref"]) not in used], lambda item: item["content_chars"] <= 800 and item["attachment_count"] >= 2)
    mark_used(case10)
    case11 = choose([item for item in drug if record_key(item["ref"]) not in used], lambda item: True)
    mark_used(case11)
    case12 = choose([item for item in consumable if record_key(item["ref"]) not in used], lambda item: True)
    mark_used(case12)
    short_main = choose([item for item in general if record_key(item["ref"]) not in used], lambda item: item["content_chars"] <= 900)
    long_aux = choose([item for item in general if record_key(item["ref"]) not in used and record_key(item["ref"]) != record_key(short_main["ref"])], lambda item: item["content_chars"] >= 1800)
    mark_used(short_main, long_aux)
    same_theme = [item for item in listing if record_key(item["ref"]) not in used]
    main14 = choose(same_theme, lambda item: True)
    aux14 = [item for item in same_theme if record_key(item["ref"]) != record_key(main14["ref"])][:2]
    while len(aux14) < 2:
        aux14.append(choose([item for item in general if record_key(item["ref"]) not in used and record_key(item["ref"]) != record_key(main14["ref"])], lambda item: True, fallback_index=len(aux14)))
    mark_used(main14, *aux14)
    long_pair_pool = [item for item in general if record_key(item["ref"]) not in used and item["content_chars"] >= 1800]
    first15 = choose(long_pair_pool, lambda item: True)
    second15 = choose([item for item in long_pair_pool if record_key(item["ref"]) != record_key(first15["ref"])], lambda item: True)
    mark_used(first15, second15)
    complex_pool = [item for item in general if record_key(item["ref"]) not in used]
    first16 = choose(complex_pool, lambda item: item["attachment_count"] >= 1)
    second16 = choose([item for item in complex_pool if record_key(item["ref"]) != record_key(first16["ref"])], lambda item: item["attachment_count"] >= 1)
    aux16 = [item for item in complex_pool if record_key(item["ref"]) not in {record_key(first16["ref"]), record_key(second16["ref"])}][:2]
    mark_used(first16, second16, *aux16)

    specs = [
        ("case09_1p0_long_text_few_attachments", "1+0", "长正文、少附件原始公告", [case09], [], "新增：验证长正文规则拆解和非摘要化分析。"),
        ("case10_1p0_short_text_many_attachments", "1+0", "短正文、多附件原始公告", [case10], [], "新增：验证附件表格和附件规则覆盖。"),
        ("case11_1p0_drug_notice", "1+0", "药品类公告", [case11], [], "新增：验证系统不只适配耗材公告。"),
        ("case12_1p0_consumable_notice", "1+0", "耗材类公告", [case12], [], "新增：与药品类公告形成对照。"),
        ("case13_1p1_short_main_long_aux", "1+1", "主材料短、辅助材料长", [short_main], [long_aux], "新增：验证辅助材料不能压过主材料。"),
        ("case14_1p2_same_theme_cross_region_aux", "1+2", "同主题不同省份辅助材料", [main14], aux14, "新增：验证跨省对比只能作为背景。"),
        ("case15_2p0_two_long_primary", "2+0", "两个正文都较长的主材料", [first15, second15], [], "新增：验证双主材料分析结构。"),
        ("case16_2pn_complex_many_rules", "2+n", "附件多、规则多、时间节点多的复杂组合", [first16, second16], aux16, "新增：验证复杂上限场景和质量门禁。"),
    ]
    cases = []
    for case_id, combo, name, primary_items, aux_items, reason in specs:
        cases.append(
            {
                "id": case_id,
                "combo": combo,
                "name": name,
                "primary": [item["ref"] for item in primary_items],
                "auxiliary": [item["ref"] for item in aux_items],
                "manual_keywords": [str(item.get("areaname") or "") for item in [*primary_items, *aux_items] if item.get("areaname")],
                "reason": reason,
                "selection_metadata": {
                    "primary": [{key: value for key, value in item.items() if key != "ref"} for item in primary_items],
                    "auxiliary": [{key: value for key, value in item.items() if key != "ref"} for item in aux_items],
                },
            }
        )
    return cases


def build_short_boundary_cases(client: ApiClient, used: set[tuple[str, str]]) -> list[dict[str, Any]]:
    general = collect_candidates(client, used, "", max_pages=6)
    short_no_attachment = choose(general, lambda item: item["content_chars"] <= 600 and item["attachment_count"] == 0)
    used.add(record_key(short_no_attachment["ref"]))
    short_attachment_led = choose(
        [item for item in general if record_key(item["ref"]) not in used],
        lambda item: item["content_chars"] <= 800 and item["attachment_count"] >= 1,
    )
    used.add(record_key(short_attachment_led["ref"]))
    specs = [
        (
            "case17_1p0_short_body_no_attachment",
            "1+0",
            "正文短且无附件公告",
            short_no_attachment,
            "新增定向复测：验证输入少时不乱编、不硬扩写，仍给出克制的证据支撑分析。",
        ),
        (
            "case18_1p0_short_body_attachment_led",
            "1+0",
            "正文短但主要内容在附件中的公告",
            short_attachment_led,
            "新增定向复测：验证附件表格、产品/价格或附件规则能进入报告。",
        ),
    ]
    return [
        {
            "id": case_id,
            "combo": combo,
            "name": name,
            "primary": [item["ref"]],
            "auxiliary": [],
            "manual_keywords": [str(item.get("areaname") or ""), str(item.get("title") or "")[:12]],
            "reason": reason,
            "selection_metadata": {"primary": [{key: value for key, value in item.items() if key != "ref"}], "auxiliary": []},
        }
        for case_id, combo, name, item, reason in specs
    ]


def build_failed_plus_short_cases(manifest_cases: list[dict[str, Any]], short_cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted_prefixes = (
        "case01_1p0_original_guangdong_mzgl",
        "case15_2p0_two_long_primary",
        "case16_2pn_complex_many_rules",
    )
    by_id = {str(case.get("id") or ""): case for case in manifest_cases}
    selected: list[dict[str, Any]] = []
    for prefix in wanted_prefixes:
        match = next((case for case_id, case in by_id.items() if case_id.startswith(prefix)), None)
        if match is None:
            raise RuntimeError(f"source manifest missing required failed case: {prefix}")
        selected.append(dict(match))
    selected.extend(dict(case) for case in short_cases)
    return selected


SHORT_BOUNDARY_PREFIXES = (
    "case17_1p0_short_body_no_attachment",
    "case18_1p0_short_body_attachment_led",
)


def short_boundary_cases_from_manifest(manifest_cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for prefix in SHORT_BOUNDARY_PREFIXES:
        match = next((case for case in manifest_cases if str(case.get("id") or "").startswith(prefix)), None)
        if match is None:
            return []
        selected.append(dict(match))
    return selected


def load_manifest_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list):
        raise RuntimeError(f"manifest has no cases list: {path}")
    return [dict(case) for case in cases if isinstance(case, dict)]


def latest_16case_manifest() -> Path | None:
    desktop = Path.home() / "Desktop"
    candidates = sorted(desktop.glob("medical_notice_report_test_16cases_*/case_manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def latest_failed_plus_short_manifest() -> Path | None:
    desktop = Path.home() / "Desktop"
    candidates = sorted(
        desktop.glob("medical_notice_report_test_failed_plus_short_*/case_manifest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def prepare_cases(client: ApiClient, case_set: str = "16", source_manifest: str = "") -> list[dict[str, Any]]:
    manifest_cases: list[dict[str, Any]] | None = None
    manifest_path = Path(source_manifest) if source_manifest else None
    if manifest_path and manifest_path.exists():
        manifest_cases = load_manifest_cases(manifest_path)
    elif case_set == "failed-plus-short":
        latest = latest_16case_manifest()
        if latest:
            manifest_cases = load_manifest_cases(latest)
    used_source = manifest_cases if manifest_cases is not None else ORIGINAL_CASES
    used = {record_key(ref) for case in used_source for ref in [*case.get("primary", []), *case.get("auxiliary", [])]}
    if case_set == "failed-plus-short":
        if manifest_cases is None:
            raise RuntimeError("failed-plus-short case set requires a 16-case manifest")
        short_cases = short_boundary_cases_from_manifest(manifest_cases)
        if not short_cases:
            latest_failed_manifest = latest_failed_plus_short_manifest()
            if latest_failed_manifest:
                short_cases = short_boundary_cases_from_manifest(load_manifest_cases(latest_failed_manifest))
        if not short_cases:
            short_cases = build_short_boundary_cases(client, used)
        cases = build_failed_plus_short_cases(manifest_cases, short_cases)
    elif manifest_cases is not None:
        cases = [dict(case) for case in manifest_cases]
    else:
        cases = [dict(case) for case in ORIGINAL_CASES]
        cases.extend(build_additional_cases(client, used))
    for case in cases:
        existing_materials = case.get("materials") if isinstance(case.get("materials"), dict) else {}
        if isinstance(existing_materials.get("primary"), list) and isinstance(existing_materials.get("auxiliary"), list):
            case["materials"] = existing_materials
        else:
            case["materials"] = {
                "primary": [enrich_material(client, ref) for ref in case["primary"]],
                "auxiliary": [enrich_material(client, ref) for ref in case["auxiliary"]],
            }
    return cases


def write_markdown_review(case: dict[str, Any], result: dict[str, Any], path: Path) -> None:
    quality = result.get("quality_eval", {})
    comparison = result.get("comparison", {})
    lines = [
        f"# {case['name']} 对比记录",
        "",
        f"- 组合：{case['combo']}",
        f"- 选择原因：{case['reason']}",
        f"- 状态：{result.get('status', '')}",
        f"- 可交付状态：{quality.get('deliverable_status', '')}",
        f"- 原文遵循评分：{quality.get('source_fidelity_score', '')}",
        f"- 未支撑硬事实：{quality.get('unsupported_fact_count', '')}",
        f"- 分析深度评分：{quality.get('analysis_depth_score', '')}",
        f"- 有依据分析句：{quality.get('evidence_backed_analysis_count', '')}",
        f"- 摘要化风险：{quality.get('summary_only_risk', '')}",
        f"- 系统字数：{comparison.get('system_visible_chars', '')}",
        f"- 人工字数：{comparison.get('manual_visible_chars', '')}",
        f"- 关键词重合度：{comparison.get('token_overlap_ratio', '')}",
        "",
        "## 阻断问题",
    ]
    codes = quality.get("blocking_issue_codes") or []
    lines.extend([f"- {code}" for code in codes] or ["- 无"])
    lines.extend(
        [
            "",
            "## 人工/替代对比差距",
            f"- 人工文件：{'; '.join(Path(item).name for item in comparison.get('manual_files', [])) or '未匹配'}",
            f"- 人工高频但系统缺失：{', '.join(comparison.get('manual_high_frequency_terms_missing_in_system', [])[:20]) or '无'}",
            f"- 系统高频但人工较少：{', '.join(comparison.get('system_high_frequency_terms_not_in_manual', [])[:20]) or '无'}",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}


def json_with_retries(
    client: ApiClient,
    method: str,
    path: str,
    payload: Any | None = None,
    timeout: int = 60,
    attempts: int = 3,
    retry_log: Path | None = None,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return client.json(method, path, payload, timeout=timeout)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in TRANSIENT_HTTP_STATUS_CODES or attempt >= attempts:
                raise
        except TimeoutError as exc:
            last_error = exc
            if attempt >= attempts:
                raise
        if retry_log is not None and last_error is not None:
            retry_log.write_text(f"attempt={attempt} {last_error.__class__.__name__}: {last_error}", encoding="utf-8")
        time.sleep(min(5 * attempt, 20))
    if last_error is not None:
        raise last_error
    raise RuntimeError("request retry failed without an error")


def bytes_with_retries(
    client: ApiClient,
    path: str,
    timeout: int = 60,
    attempts: int = 3,
    retry_log: Path | None = None,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return client.bytes(path, timeout=timeout)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in TRANSIENT_HTTP_STATUS_CODES or attempt >= attempts:
                raise
        except TimeoutError as exc:
            last_error = exc
            if attempt >= attempts:
                raise
        if retry_log is not None and last_error is not None:
            retry_log.write_text(f"attempt={attempt} {last_error.__class__.__name__}: {last_error}", encoding="utf-8")
        time.sleep(min(5 * attempt, 20))
    if last_error is not None:
        raise last_error
    raise RuntimeError("request retry failed without an error")


def run_case(client: ApiClient, case: dict[str, Any], case_dir: Path, index: int, total: int) -> dict[str, Any]:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "case.json").write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
    prepare = json_with_retries(
        client,
        "POST",
        "/analysis/prepare",
        {"primary_materials": case["primary"], "auxiliary_materials": case["auxiliary"], "force_refresh_attachments": False},
        timeout=240,
        retry_log=case_dir / "prepare_retry_error.txt",
    )
    (case_dir / "prepare_response.json").write_text(json.dumps(prepare, ensure_ascii=False, indent=2), encoding="utf-8")
    pack_id = str(prepare.get("pack_id") or "")
    run = json_with_retries(client, "POST", "/analysis/run", {"pack_id": pack_id}, timeout=60, retry_log=case_dir / "run_retry_error.txt")
    (case_dir / "run_start_response.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    run_id = str(run.get("run_id") or "")
    started = time.time()
    state: dict[str, Any] = {}
    while True:
        try:
            state = client.json("GET", f"/analysis/runs/{urllib.parse.quote(run_id)}", timeout=60)
        except TimeoutError as exc:
            (case_dir / "status_poll_error.txt").write_text(f"{exc.__class__.__name__}: {exc}", encoding="utf-8")
            if time.time() - started > 1200:
                raise
            print(f"[{index}/{total}] {case['id']} status_poll_timeout={exc}", flush=True)
            time.sleep(10)
            continue
        (case_dir / "run_latest.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        status = str(state.get("status") or "")
        if status in TERMINAL_STATUSES:
            break
        if time.time() - started > 1200:
            raise TimeoutError(f"run timed out: {run_id}")
        print(f"[{index}/{total}] {case['id']} status={status}", flush=True)
        time.sleep(10)

    report_markdown = ""
    try:
        body = bytes_with_retries(
            client,
            f"/analysis/runs/{urllib.parse.quote(run_id)}/report",
            timeout=120,
            attempts=3,
            retry_log=case_dir / "report_retry_error.txt",
        )
        (case_dir / "report_response.json").write_bytes(body)
        report_markdown = extract_report_markdown(body)
        (case_dir / "system_report_extracted.md").write_text(report_markdown, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        (case_dir / "system_report_error.txt").write_text(f"{exc.__class__.__name__}: {exc}", encoding="utf-8")

    diagnostics: dict[str, Any] = {}
    try:
        diagnostics = client.json("GET", f"/analysis/runs/{urllib.parse.quote(run_id)}/diagnostics", timeout=60)
        (case_dir / "run_diagnostics.json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        (case_dir / "diagnostics_error.txt").write_text(f"{exc.__class__.__name__}: {exc}", encoding="utf-8")
    quality_eval = build_quality_eval(case["id"], diagnostics, str(state.get("status") or ""), run_state=state)
    (case_dir / "quality_eval.json").write_text(json.dumps(quality_eval, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        docx = bytes_with_retries(
            client,
            f"/analysis/runs/{urllib.parse.quote(run_id)}/download",
            timeout=120,
            attempts=2,
            retry_log=case_dir / "download_retry_error.txt",
        )
        (case_dir / "report.docx").write_bytes(docx)
    except Exception as exc:  # noqa: BLE001
        (case_dir / "download_error.txt").write_text(f"{exc.__class__.__name__}: {exc}", encoding="utf-8")

    comparison = compare_with_reference(case, report_markdown, Path.home() / "Desktop" / "人工分析")
    (case_dir / "comparison_review.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {
        "case_id": case["id"],
        "case_name": case["name"],
        "combo": case["combo"],
        "pack_id": pack_id,
        "run_id": run_id,
        "status": str(state.get("status") or ""),
        "quality_eval": quality_eval,
        "comparison": comparison,
    }
    write_markdown_review(case, result, case_dir / "comparison_review.md")
    return result


def write_final_review(results: list[dict[str, Any]], output_dir: Path) -> None:
    lines = [
        "# 16 组报告质量回归测试结论",
        "",
        "- 本轮只使用 `/analysis/runs/{run_id}/report` 中的 `report_markdown` 作为系统报告正文。",
        "- 新增检测维度：原文遵循、未支撑硬事实、分析深度、有依据分析句、摘要化风险。",
        "",
        "| 序号 | 组合 | 样本 | 状态 | 可交付 | 原文遵循 | 未支撑事实 | 分析深度 | 有依据分析句 | 摘要化 | 系统字数 | 人工字数 | 重合度 |",
        "|---:|---|---|---|---|---:|---:|---:|---:|---|---:|---:|---:|",
    ]
    for idx, item in enumerate(results, start=1):
        quality = item.get("quality_eval", {})
        comparison = item.get("comparison", {})
        lines.append(
            f"| {idx} | {item.get('combo','')} | {item.get('case_name','')} | {item.get('status','')} | "
            f"{quality.get('deliverable_status','')} | {quality.get('source_fidelity_score','')} | "
            f"{quality.get('unsupported_fact_count','')} | {quality.get('analysis_depth_score','')} | "
            f"{quality.get('evidence_backed_analysis_count','')} | {quality.get('summary_only_risk','')} | "
            f"{comparison.get('system_visible_chars','')} | {comparison.get('manual_visible_chars','')} | "
            f"{comparison.get('token_overlap_ratio','')} |"
        )
    deliverable = sum(1 for item in results if item.get("quality_eval", {}).get("deliverable_status") == "deliverable")
    needs_review = sum(1 for item in results if item.get("quality_eval", {}).get("deliverable_status") == "needs_manual_review")
    failed = sum(1 for item in results if item.get("quality_eval", {}).get("deliverable_status") == "failed")
    lines.extend(
        [
            "",
            "## 汇总",
            "",
            f"- 可交付：{deliverable}",
            f"- 需人工复核：{needs_review}",
            f"- 失败：{failed}",
            "",
            "## 优先复核",
        ]
    )
    for item in results:
        quality = item.get("quality_eval", {})
        if quality.get("deliverable_status") != "deliverable":
            codes = ", ".join(quality.get("blocking_issue_codes") or []) or "无质量码"
            lines.append(f"- {item.get('case_id')}: {item.get('case_name')}，{quality.get('deliverable_status')}，{codes}")
    (output_dir / "final_review.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("REPORT_TEST_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--case-set", choices=["16", "failed-plus-short"], default="16")
    parser.add_argument("--source-manifest", default="")
    args = parser.parse_args()
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    default_dir_name = (
        f"medical_notice_report_test_failed_plus_short_{timestamp}"
        if args.case_set == "failed-plus-short"
        else f"medical_notice_report_test_16cases_{timestamp}"
    )
    output_dir = Path(args.output_dir) if args.output_dir else Path.home() / "Desktop" / default_dir_name
    output_dir.mkdir(parents=True, exist_ok=True)
    client = ApiClient(args.base_url)
    cases = prepare_cases(client, case_set=args.case_set, source_manifest=args.source_manifest)
    (output_dir / "case_manifest.json").write_text(
        json.dumps(
            {
                "base_url": args.base_url,
                "case_set": args.case_set,
                "source_manifest": args.source_manifest,
                "created_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
                "cases": cases,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    results = []
    for index, case in enumerate(cases, start=1):
        case_dir = output_dir / f"case{index:02d}_{safe_name(case['id'])}"
        print(f"[{index}/{len(cases)}] start {case['id']} {case['name']}", flush=True)
        try:
            results.append(run_case(client, case, case_dir, index, len(cases)))
        except Exception as exc:  # noqa: BLE001
            error = {"case_id": case["id"], "case_name": case["name"], "combo": case["combo"], "status": "script_error", "error": f"{exc.__class__.__name__}: {exc}", "quality_eval": {"deliverable_status": "failed"}, "comparison": {}}
            (case_dir / "script_error.txt").write_text(error["error"], encoding="utf-8")
            results.append(error)
            print(f"[{index}/{len(cases)}] error {case['id']} {error['error']}", flush=True)
    (output_dir / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "summary_review.json").write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_final_review(results, output_dir)
    print(output_dir, flush=True)


if __name__ == "__main__":
    main()
