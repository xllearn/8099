from __future__ import annotations

import shutil
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dify_workflow_pack_id.yml"
TARGET = ROOT / "dify_workflow_pack_id_manual_style.yml"


GENERATE_SYSTEM = """你是医药器械采购政策分析报告撰写助手。请严格基于 evidence_pack 生成中文行业分析报告，只输出一个合法 JSON 对象，不要输出思考过程、分析草稿、调试信息、Markdown 代码围栏或 JSON 之外的解释文字。

证据使用规则：
1. primary_materials 是主材料，决定报告主题、主体结构和主要结论。
2. auxiliary_materials 只能用于历史对比、背景补充、关联说明，不能覆盖或替代主材料。
3. 不得编造 evidence_pack 中不存在的事实、数字、时间、主体名称、价格规则或企业影响。
4. attachments 如果 parse_status 为 metadata_only，只能说明“附件仅有元数据”，不能假装读取了附件全文。
5. evidence_pack.warnings 必须被遵守，正文为空、正文过短、附件未解析等情况不能作为强证据使用。

报告风格：
1. 参考人工医药器械政策分析稿，语言专业、克制、清晰，不要写成短摘要。
2. 主标题和各章节标题应自然、有行业分析感，不要机械堆字段。
3. 重要规则优先使用 Markdown 表格表达，特别是挂网状态、申报要求、价格联动、暂停/撤销挂网、时间节点、企业影响、适用情形等内容。
4. 表格使用标准 Markdown 表格，不要使用 HTML 表格。
5. 重点风险、处罚后果、暂停/撤销挂网、申报截止、价格联动、企业必须执行事项，用 <red>...</red> 包裹。
6. 重要背景、企业关注点、流程节点、政策变化提示，用 <blue>...</blue> 包裹。
7. 每篇报告必须至少使用 2 处 <blue>...</blue> 重点标记；只有当材料明确包含处罚后果、暂停/撤销挂网、申报截止、价格联动、强约束或企业必须执行事项时，才使用 <red>...</red> 标记。
8. report_markdown 中可以包含 Markdown 标题、列表、表格、<red>/<blue> 标记；不得包含 JSON、质检过程、调试信息或模型思考。
9. 表格列名和单元格内容必须来自 evidence_pack 的明确事实；不得为了让表格更完整而补写解释性列、示例项目、市场影响或推断内容。
10. 如果证据只给出类别清单，只能生成“序号/类别”或“类别”表，不得自行增加“涵盖内容”“影响分析”“适用产品”等列。
"""


GENERATE_USER = """请根据以下 evidence_pack 生成中文医药器械政策分析报告。

evidence_pack:
{{#parse_evidence_pack.evidence_pack_json#}}

只返回以下 JSON 结构，字段名不得变化：
{
  "version": 1,
  "report_title": "",
  "report_markdown": "",
  "primary_material_ids": [],
  "auxiliary_material_ids": [],
  "used_key_facts": [],
  "generation_warnings": []
}

report_markdown 写作要求：
- 使用中文。
- 使用 Markdown。
- 主材料决定报告主题和主体内容。
- 辅助材料只能用于背景、历史对比、关联政策说明。
- 证据充分时应包含导语、政策/项目核心内容、关键规则、企业关注点、后续影响或执行提示。
- 只要材料中存在可结构化的规则、状态、条件、时间节点、企业后果，应优先生成 Markdown 表格。
- 表格不得自行扩写证据未披露的细项。比如证据只列出“临床量表评估、中医外治、口腔种植”等类别时，表格只能列类别，不能补写“各类量表评估项目”“贴敷、熏洗”“种植体植入及修复”等未披露内容。
- 表格示例格式：
  | 挂网状态 | 序号 | 对应情形 |
  | --- | --- | --- |
  | 暂停挂网 | 1 | 注册证到期的，对应产品暂停挂网。 |
  | 暂停挂网 | 2 | <red>未进行价格联动的带量采购非中选产品暂停挂网。</red> |
- 对重点风险和强约束使用 <red>...</red>。
- 对重要关注点和提示使用 <blue>...</blue>。
- report_markdown 必须至少包含 2 处 <blue>...</blue>。只有证据明确出现风险、处罚、暂停/撤销挂网、申报截止、价格联动或企业必须执行事项时，才使用 <red>...</red>。
- 不要把未解析附件内容当作依据。
- 不要把 JSON 对象本身写入 report_markdown。
"""


QA_PROMPT = """你是中文医药器械政策分析报告质检助手。请核对报告是否忠实于 evidence_pack。不要输出思考过程，只返回一个合法 JSON 对象，不要使用 Markdown 代码围栏。

质检重点：
1. 是否存在 evidence_pack 中没有依据的事实。
2. 日期、数字、价格、主体名称、项目阶段、适用范围是否错误。
3. 是否混淆主材料和辅助材料。
4. 是否把 metadata_only 附件当作已读取全文。
5. 是否存在过度推断、绝对化表述或营销化语言。
6. report_markdown 是否混入 JSON、调试信息、提示词或模型思考过程。
7. 对存在明确规则、状态、条件、时间节点、企业后果的内容，报告是否尽量用表格表达；如果完全没有表格且证据中有明显规则清单，应列为 major。
8. 红色标记 <red>...</red> 是否只用于关键风险、处罚后果、暂停/撤销挂网、申报截止、价格联动、企业必须执行事项；蓝色标记 <blue>...</blue> 是否只用于重要关注点或政策变化提示。
9. 颜色标记不得改变事实，不得标注无依据内容。
10. 表格是否自行补写了证据未披露的解释性列、示例项目、市场影响或适用范围；如有，应列为 high 或 major。
11. report_markdown 是否完全没有 <blue> 标记；如果没有，应列为 minor。若 <red> 标记用于普通背景、一般时间安排或非风险内容，也应列为 minor，除非该标记改变了事实含义。

severity 只能使用 fatal、high、major、minor。
只有 fatal/high/major 需要自动修订，minor 不触发自动修订。

evidence_pack:
{{#parse_evidence_pack.evidence_pack_json#}}

generation_warnings:
{{#parse_generation_json.generation_warnings_json#}}

report_markdown:
{{REPORT_MARKDOWN}}

返回 JSON：
{
  "passed": false,
  "round": ROUND_NUMBER,
  "issues": [
    {
      "issue_id": "Q001",
      "severity": "high",
      "problem_type": "unsupported_claim",
      "report_text": "",
      "source_basis": "",
      "fix_instruction": ""
    }
  ],
  "revision_instruction": "请只修订 issues 指出的问题，不要重写无问题段落。"
}
"""


REVISE_PROMPT = """你是中文医药器械政策分析报告修订助手。请只根据 quality_check.issues 修订报告，不要重写无问题段落，不要新增 evidence_pack 中不存在的内容。不要输出思考过程，只返回一个合法 JSON 对象，不要使用 Markdown 代码围栏。

evidence_pack:
{{#parse_evidence_pack.evidence_pack_json#}}

previous report_title:
{{#parse_generation_json.report_title#}}

previous report_markdown:
{{#parse_generation_json.report_markdown#}}

quality_check.issues:
{{#parse_quality_json.issues_json#}}

revision_instruction:
{{#parse_quality_json.revision_instruction#}}

修订要求：
- 输出完整修订后的 report_markdown，不要只输出片段。
- 保留人工分析稿风格。
- 保留必要 Markdown 表格。
- 修订表格时只能保留 evidence_pack 明确披露的字段和内容；如果原报告表格包含未披露的解释性列或细项，应删除该列或改成只列原文类别/规则。
- 对关键风险、处罚后果、暂停/撤销挂网、申报截止、价格联动、企业必须执行事项，用 <red>...</red> 包裹。
- 对重要关注点和政策变化提示，用 <blue>...</blue> 包裹。
- 修订后 report_markdown 必须至少包含 2 处 <blue>...</blue>。只有证据明确出现风险、处罚、暂停/撤销挂网、申报截止、价格联动或企业必须执行事项时，才使用 <red>...</red>。
- 不要把未解析附件内容当作依据。
- 不要把 JSON、质检过程、调试信息写入 report_markdown。

返回 JSON：
{
  "version": 2,
  "report_title": "",
  "report_markdown": "",
  "fixed_issue_ids": [],
  "revision_notes": ""
}
"""


JSON_EXTRACT_HELPER = r'''
    def extract_json(text):
        text = (text or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        if start < 0:
            return {}
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:index + 1])
        return json.loads(text[start:])
'''


def _replace_extract_json(code: str) -> str:
    start = code.find("    def extract_json(text):")
    end = code.find("\n    try:", start)
    if start < 0 or end < 0:
        return code
    return code[:start] + JSON_EXTRACT_HELPER.strip("\n") + code[end:]


def main() -> None:
    data = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    for node in data["workflow"]["graph"]["nodes"]:
        node_data = node.get("data", {})
        title = node_data.get("title")
        if title == "Generate Report JSON":
            for msg in node_data.get("prompt_template", []):
                if msg.get("role") == "system":
                    msg["text"] = GENERATE_SYSTEM
                elif msg.get("role") == "user":
                    msg["text"] = GENERATE_USER
        elif title == "Quality Check Round 1":
            for msg in node_data.get("prompt_template", []):
                msg["text"] = QA_PROMPT.replace("{{REPORT_MARKDOWN}}", "{{#parse_generation_json.report_markdown#}}").replace(
                    "ROUND_NUMBER", "1"
                )
        elif title == "Quality Check Round 2":
            for msg in node_data.get("prompt_template", []):
                msg["text"] = QA_PROMPT.replace("{{REPORT_MARKDOWN}}", "{{#parse_revision_json.report_markdown#}}").replace(
                    "ROUND_NUMBER", "2"
                )
        elif title == "Revise Report JSON":
            for msg in node_data.get("prompt_template", []):
                msg["text"] = REVISE_PROMPT
        elif title in {"Parse Generation JSON", "Parse Revision JSON", "Parse Quality JSON", "Parse Revised Quality JSON"}:
            node_data["code"] = _replace_extract_json(str(node_data.get("code") or ""))

    data["app"]["description"] = (
        "Input pack_id, fetch evidence_pack, generate Chinese report with manual-report style tables and red/blue highlights."
    )
    data["app"]["name"] = "Medical Notice Report Pack ID Manual Style"

    TARGET.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    downloads_target = Path.home() / "Downloads" / TARGET.name
    shutil.copyfile(TARGET, downloads_target)
    print(TARGET)
    print(downloads_target)


if __name__ == "__main__":
    main()
