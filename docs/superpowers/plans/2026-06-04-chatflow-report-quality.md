# Chatflow Report Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved Chatflow-first report workflow so rich notice evidence produces human-depth reports, optional Word input is constrained to one integrated related-project connection, QA catches short or unsupported output, and Word export happens only after user approval.

**Architecture:** Keep `app/main.py` as the backend source of truth for fetch, render, QA parsing, local QA, and Word export. Strengthen prompts and `dify-chatflow-medical-notice-report.yml` so first generation and approved download work first; implement user feedback revision last, per the user's priority. The existing 20-node Workflow remains a compatibility asset.

**Tech Stack:** FastAPI, Pydantic, python-docx, Dify Chatflow YAML blueprint, Python `unittest`, Docker Compose.

---

## File Structure

- Modify: `app/main.py`
  - Add deterministic local QA helpers for report depth, table evidence markers, and history leakage.
  - Merge local QA results into `/report/qa` and `/report/export_checked`.
- Create: `tests/test_report_quality.py`
  - Focused tests for local QA and QA merge behavior.
- Create: `tests/test_chatflow_blueprint.py`
  - Focused tests for Chatflow branches and delayed Word export.
- Modify: `tests/test_report_export.py`
  - Extend existing prompt tests with human-report structure and conditional repair assertions.
- Modify: `prompts/report_system_prompt.md`
  - Add human-depth requirements and reference-report structure guidance.
- Modify: `prompts/report_user_prompt.md`
  - Add concrete report-depth and table-preservation instructions.
- Modify: `prompts/report_qa_prompt.md`
  - Require table-by-table evidence checks and human-report style checks.
- Modify: `prompts/report_qa_fix_prompt.md`
  - Require local repair without compressing existing valid content.
- Modify: `prompts/report_revision_prompt.md`
  - Last-phase update for user feedback branch.
- Modify: `prompts/report_history_prompt.md`
  - Reinforce that optional Word produces only limited observations.
- Modify: `dify-chatflow-medical-notice-report.yml`
  - Make it the authoritative Chatflow blueprint with `first_generate`, `approve_download`, and later `revise`.
- Modify: `README.md`, `NEXT_CHAT_HANDOFF.md`, `PROJECT_CONTEXT.md`
  - Update operational notes after implementation.

This project is not a Git repository. Replace each normal commit step with a no-Git checkpoint:

```powershell
git rev-parse --is-inside-work-tree
```

Expected: `fatal: not a git repository`. Record changed files in the final implementation summary instead of committing.

---

### Task 1: Add Local QA Failing Tests

**Files:**
- Create: `tests/test_report_quality.py`
- Modify: none
- Test: `tests/test_report_quality.py`

- [ ] **Step 1: Write tests for report depth, table evidence, history boundaries, and QA merge**

Create `tests/test_report_quality.py` with:

```python
from __future__ import annotations

import unittest

from app.main import (
    CheckedExportReportRequest,
    ReportIR,
    ReportQAParseRequest,
    ReportSection,
    ReportTable,
    _quality_check_report_against_evidence,
    export_report_checked,
    parse_report_qa,
)


def rich_procurement_evidence() -> str:
    return """
# 网页正文
标题: 关于国家组织冠脉支架集中带量采购第二轮接续采购文件的公告
URL: https://example.test/notice
国家组织高值医用耗材联合采购办公室发布《“国家组织冠脉支架集中带量采购”第二轮接续采购文件》。
采购品种范围包括冠状动脉药物洗脱合金支架和冠状动脉药物洗脱不锈钢支架。
采购周期自中选结果实际执行日起至2029年6月30日。
企业报价要求包括最高有效申报价、询价基准、有效报价、拟中选产品确定和采购执行。
最高有效申报价：冠状动脉药物洗脱合金支架949元/个，冠状动脉药物洗脱不锈钢支架848元/个。
询价基准：冠状动脉药物洗脱合金支架848元/个，冠状动脉药物洗脱不锈钢支架757元/个。
非中选产品管理、价格联动、名词解释、信用评价、失信约束、取消中选资格均在采购文件中列明。

# 附件
文件名: 采购文件.docx
采购品种 | 最高有效申报价 | 询价基准
冠状动脉药物洗脱合金支架 | 949元/个 | 848元/个
冠状动脉药物洗脱不锈钢支架 | 848元/个 | 757元/个
"""


def short_procurement_report() -> ReportIR:
    return ReportIR(
        title="国采冠脉支架第二轮接续采购启动",
        notice_type="集采/接续采购类",
        lead_paragraphs=[
            "国家组织高值医用耗材联合采购办公室发布冠脉支架第二轮接续采购文件，企业需关注报价规则。"
        ],
        sections=[
            ReportSection(
                heading="报价规则",
                paragraphs=["产品申报价格不超过最高有效申报价的，可获得中选资格。"],
            )
        ],
        enterprise_tips=["企业需关注报价上限。"],
    )


class LocalReportQualityTests(unittest.TestCase):
    def test_rich_procurement_evidence_blocks_short_summary_report(self) -> None:
        qa = _quality_check_report_against_evidence(
            short_procurement_report(),
            rich_procurement_evidence(),
            history_text="",
        )

        self.assertEqual(qa.status, "needs_fix")
        self.assertTrue(any(issue.category == "report_depth" for issue in qa.issues))
        self.assertTrue(any("补充" in item for item in qa.fix_instructions))

    def test_short_simple_notice_is_not_blocked_by_depth_only(self) -> None:
        report = ReportIR(
            title="河南调整医用耗材申报挂网操作流程",
            notice_type="挂网流程调整类",
            lead_paragraphs=["河南调整申报入口，企业需按通知要求办理。"],
        )
        evidence = "河南省发布短通知，自2026年6月1日起通过全国联审通办提交申报。"

        qa = _quality_check_report_against_evidence(report, evidence, history_text="")

        self.assertFalse(any(issue.category == "report_depth" for issue in qa.issues))

    def test_table_value_absent_from_evidence_is_unsupported(self) -> None:
        report = ReportIR(
            title="国采冠脉支架第二轮接续采购启动",
            notice_type="集采/接续采购类",
            lead_paragraphs=["采购文件列明最高有效申报价。"],
            sections=[
                ReportSection(
                    heading="价格规则",
                    tables=[
                        ReportTable(
                            title="最高有效申报价表",
                            headers=["品种", "最高有效申报价"],
                            rows=[["冠状动脉药物洗脱合金支架", "999元/个"]],
                        )
                    ],
                )
            ],
        )

        qa = _quality_check_report_against_evidence(report, rich_procurement_evidence(), history_text="")

        self.assertEqual(qa.status, "needs_fix")
        self.assertTrue(any("999元/个" in issue.report_text for issue in qa.unsupported_claims))

    def test_table_value_present_in_evidence_passes_marker_check(self) -> None:
        report = ReportIR(
            title="国采冠脉支架第二轮接续采购启动",
            notice_type="集采/接续采购类",
            lead_paragraphs=[
                "国家组织高值医用耗材联合采购办公室发布采购文件，采购品种为冠状动脉药物洗脱支架。",
                "采购文件列明最高有效申报价、询价基准、拟中选产品确定和采购执行要求。",
            ],
            sections=[
                ReportSection(
                    heading="价格规则",
                    paragraphs=["采购文件列明不同采购品种的最高有效申报价和询价基准。"],
                    tables=[
                        ReportTable(
                            title="最高有效申报价表",
                            headers=["品种", "最高有效申报价", "询价基准"],
                            rows=[["冠状动脉药物洗脱合金支架", "949元/个", "848元/个"]],
                        )
                    ],
                ),
                ReportSection(heading="采购执行", paragraphs=["采购执行以正式文件要求为准。"]),
                ReportSection(heading="非中选产品管理", paragraphs=["采购文件列明非中选产品管理要求。"]),
                ReportSection(heading="名词解释", paragraphs=["采购文件列明相关名词解释。"]),
            ],
        )

        qa = _quality_check_report_against_evidence(report, rich_procurement_evidence(), history_text="")

        self.assertFalse(any("949元/个" in issue.report_text for issue in qa.unsupported_claims))

    def test_integrated_history_connection_passes_when_no_history_fact_is_copied(self) -> None:
        report = ReportIR(
            title="山东三类耗材接续采购启动",
            notice_type="集采/接续采购类",
            lead_paragraphs=[
                "山东省公共资源交易中心发布接续采购文件，企业需按申报要求参与。结合既往同类项目经验，企业仍需关注申报节奏和价格口径衔接。"
            ],
            sections=[ReportSection(heading="企业报价", paragraphs=["企业报价不得高于文件列明的相关价格口径。"])],
        )
        history = "历史稿显示：采购周期为3年，最高有效申报价为128元。"
        evidence = "山东省发布接续采购文件，企业需按申报要求参与，公告未披露历史稿价格。"

        qa = _quality_check_report_against_evidence(report, evidence, history_text=history)

        self.assertFalse(qa.history_leakage)

    def test_history_heading_and_copied_price_are_flagged(self) -> None:
        report = ReportIR(
            title="山东三类耗材接续采购启动",
            notice_type="集采/接续采购类",
            lead_paragraphs=["山东省发布接续采购文件。"],
            sections=[
                ReportSection(
                    heading="历史分析",
                    paragraphs=["结合历史项目，本次最高有效申报价为128元。"],
                )
            ],
        )
        history = "历史稿显示：最高有效申报价为128元。"
        evidence = "山东省发布接续采购文件，本次公告未披露最高有效申报价。"

        qa = _quality_check_report_against_evidence(report, evidence, history_text=history)

        self.assertEqual(qa.status, "needs_fix")
        self.assertTrue(any(issue.category == "history_heading" for issue in qa.history_leakage))
        self.assertTrue(any("128元" in issue.report_text for issue in qa.history_leakage))

    def test_parse_report_qa_merges_local_quality_issues(self) -> None:
        response = parse_report_qa(
            ReportQAParseRequest(
                qa_output='{"status":"pass","issues":[],"unsupported_claims":[],"history_leakage":[],"missing_rules":[],"language_issues":[],"fix_instructions":[],"summary":"模型认为通过"}',
                report_text="",
                report_ir=short_procurement_report(),
                evidence_text=rich_procurement_evidence(),
                history_text="",
            )
        )

        self.assertFalse(response.blocked)
        self.assertEqual(response.qa.status, "needs_fix")
        self.assertIn("report_depth", response.qa_summary)

    def test_export_checked_blocks_local_unsupported_table_value(self) -> None:
        report = ReportIR(
            title="国采冠脉支架第二轮接续采购启动",
            notice_type="集采/接续采购类",
            lead_paragraphs=["采购文件列明最高有效申报价。"],
            sections=[
                ReportSection(
                    heading="价格规则",
                    tables=[
                        ReportTable(
                            title="最高有效申报价表",
                            headers=["品种", "最高有效申报价"],
                            rows=[["冠状动脉药物洗脱合金支架", "999元/个"]],
                        )
                    ],
                )
            ],
        )

        response = export_report_checked(
            CheckedExportReportRequest(
                report_ir=report,
                qa_output='{"status":"pass","issues":[],"unsupported_claims":[],"history_leakage":[],"missing_rules":[],"language_issues":[],"fix_instructions":[],"summary":"模型认为通过"}',
                report_text="",
                evidence_text=rich_procurement_evidence(),
                history_text="",
            )
        )

        self.assertFalse(response.success)
        self.assertTrue(response.blocked)
        self.assertEqual(response.download_url, "")
        self.assertIn("999元/个", response.qa_summary)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```powershell
python -m unittest tests.test_report_quality -v
```

Expected:

```text
AttributeError: module 'app.main' has no attribute '_quality_check_report_against_evidence'
```

or:

```text
TypeError: ReportQAParseRequest.__init__() got an unexpected keyword argument 'report_ir'
```

- [ ] **Step 3: No-Git checkpoint**

Run:

```powershell
git rev-parse --is-inside-work-tree
```

Expected:

```text
fatal: not a git repository
```

Record changed file: `tests/test_report_quality.py`.

---

### Task 2: Implement Local Evidence-Based QA

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_report_quality.py`

- [ ] **Step 1: Add `report_ir` to QA request models**

Modify `ReportQAParseRequest` in `app/main.py`:

```python
class ReportQAParseRequest(BaseModel):
    qa_output: str = ""
    report_text: str = ""
    report_ir: ReportIR | None = None
    history_text: str = ""
    evidence_text: str = ""
```

`CheckedExportReportRequest` already inherits `ExportReportRequest`, so it already accepts `report_ir`.

- [ ] **Step 2: Add deterministic local QA constants and helpers**

Insert this block after `_quality_check_report()` in `app/main.py`:

```python
RICH_PROCUREMENT_KEYWORDS = [
    "采购文件",
    "集中带量采购",
    "接续采购",
    "最高有效申报价",
    "询价基准",
    "拟中选",
    "中选产品",
    "协议采购量",
    "价格联动",
    "非中选产品",
    "名词解释",
    "失信约束",
]
REPORT_DEPTH_MIN_CHARS = 1200
REPORT_DEPTH_MIN_SECTIONS = 4
EVIDENCE_MARKER_RE = re.compile(
    r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
    r"|20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}"
    r"|\d+(?:,\d{3})*(?:\.\d+)?\s*(?:元/个|元|万元|%|个工作日|个|年)"
    r"|[A-Z]{2,}-[A-Z0-9-]{3,}"
)
EVIDENCE_MARKER_SKIP_WORDS = ["未披露", "暂未公布", "以正式文件为准", "计算值", "按表内数据测算"]


def _quality_check_report_against_evidence(report: ReportIR, evidence_text: str, history_text: str = "") -> ReportQA:
    qa = ReportQA()
    report_text = _report_ir_text(report)
    clean_report_text = _clean_for_history_compare(report_text)
    clean_evidence = _clean_for_history_compare(evidence_text)
    rich_evidence = _looks_like_rich_procurement_evidence(clean_evidence)

    if rich_evidence:
        char_count = len(re.sub(r"\s+", "", clean_report_text))
        section_count = _report_quality_metrics(report)["meaningful_sections"]
        if char_count < REPORT_DEPTH_MIN_CHARS:
            qa.issues.append(
                ReportQAIssue(
                    severity="major",
                    category="report_depth",
                    report_text=f"报告正文约{char_count}字，证据较丰富但报告偏短。",
                    fix_instruction=(
                        f"补充导语、关键规则小节和分主题表格；采购文件类报告正文应接近人工分析稿，"
                        f"证据丰富时建议不少于{REPORT_DEPTH_MIN_CHARS}字。"
                    ),
                )
            )
        if section_count < REPORT_DEPTH_MIN_SECTIONS:
            qa.missing_rules.append(
                f"采购文件类报告结构偏少：当前有效小节{section_count}个，建议至少{REPORT_DEPTH_MIN_SECTIONS}个。"
            )

    qa.unsupported_claims.extend(_table_cells_missing_from_evidence(report, clean_evidence))
    qa.history_leakage.extend(_detect_history_leakage(clean_report_text, history_text, evidence_text))

    if qa.issues or qa.unsupported_claims or qa.history_leakage or qa.missing_rules:
        qa.status = "needs_fix"
        qa.summary = "本地质检发现报告深度、表格证据或历史边界问题。"
        for issue in [*qa.issues, *qa.unsupported_claims, *qa.history_leakage]:
            if issue.fix_instruction:
                qa.fix_instructions.append(issue.fix_instruction)
        for rule in qa.missing_rules:
            qa.fix_instructions.append(f"补充或拆分对应规则内容：{rule}")
        qa.fix_instructions = _unique([item for item in qa.fix_instructions if item])
    return qa


def _report_quality_metrics(report: ReportIR) -> dict[str, int]:
    sections = [
        section
        for section in report.sections
        if section.heading or section.paragraphs or section.tables or section.highlights
    ]
    return {
        "lead_paragraphs": len([item for item in report.lead_paragraphs if _clean_inline_text(item)]),
        "meaningful_sections": len(sections),
        "tables": sum(len(section.tables) for section in sections),
        "table_rows": sum(len(table.rows) for section in sections for table in section.tables),
    }


def _looks_like_rich_procurement_evidence(evidence_text: str) -> bool:
    text = str(evidence_text or "")
    hits = sum(1 for keyword in RICH_PROCUREMENT_KEYWORDS if keyword in text)
    return hits >= 4 and len(re.sub(r"\s+", "", text)) >= 1500


def _table_cells_missing_from_evidence(report: ReportIR, evidence_text: str) -> list[ReportQAIssue]:
    normalized_evidence = _compact_evidence_marker(evidence_text)
    issues: list[ReportQAIssue] = []
    seen: set[str] = set()
    for section in report.sections:
        for table in section.tables:
            table_name = table.title or section.heading or "未命名表格"
            cells = [*table.headers, *(cell for row in table.rows for cell in row), *table.notes]
            for cell in cells:
                cell_text = _clean_inline_text(cell)
                if not cell_text or any(word in cell_text for word in EVIDENCE_MARKER_SKIP_WORDS):
                    continue
                for marker in _extract_evidence_markers(cell_text):
                    compact_marker = _compact_evidence_marker(marker)
                    if not compact_marker or compact_marker in seen:
                        continue
                    seen.add(compact_marker)
                    if compact_marker not in normalized_evidence:
                        issues.append(
                            ReportQAIssue(
                                severity="major",
                                category="table_evidence_mismatch",
                                report_text=f"{table_name}: {marker}",
                                source_quote="",
                                fix_instruction=f"核对表格《{table_name}》中的“{marker}”，删除无证据数值或改为原文披露内容。",
                            )
                        )
    return issues


def _extract_evidence_markers(text: str) -> list[str]:
    return _unique(match.group(0).strip() for match in EVIDENCE_MARKER_RE.finditer(str(text or "")))


def _compact_evidence_marker(text: str) -> str:
    return re.sub(r"[\s,，。；;：:（）()《》\"“”'、]", "", str(text or ""))


def _merge_local_qa(base: ReportQA, local: ReportQA) -> ReportQA:
    if local.status != "pass" and base.status == "pass":
        base.status = local.status
    base.issues.extend(local.issues)
    base.unsupported_claims.extend(local.unsupported_claims)
    base.history_leakage.extend(local.history_leakage)
    base.missing_rules = _unique([*base.missing_rules, *local.missing_rules])
    base.language_issues.extend(local.language_issues)
    base.fix_instructions = _unique([*base.fix_instructions, *local.fix_instructions])
    if local.summary:
        base.summary = f"{base.summary}\n{local.summary}".strip()
    return base
```

- [ ] **Step 3: Merge local QA inside `/report/export_checked`**

In `export_report_checked()`, replace:

```python
qa = _parse_qa_output(req.qa_output) if req.qa_output else ReportQA()
local_history_issues = _detect_history_leakage(req.report_text or req.markdown, req.history_text, req.evidence_text)
if local_history_issues:
    qa.history_leakage.extend(local_history_issues)
    if qa.status == "pass":
        qa.status = "needs_fix"
qa_summary = _format_qa_summary(qa)
```

with:

```python
qa = _parse_qa_output(req.qa_output) if req.qa_output else ReportQA()
report_for_local_qa = _prepare_report_for_export(req)
local_qa = _quality_check_report_against_evidence(report_for_local_qa, req.evidence_text, req.history_text)
qa = _merge_local_qa(qa, local_qa)
qa_summary = _format_qa_summary(qa)
```

Then replace later:

```python
report = _prepare_report_for_export(req)
```

with:

```python
report = report_for_local_qa
```

- [ ] **Step 4: Merge local QA inside `/report/qa`**

In `parse_report_qa()`, replace the existing local history leakage block:

```python
local_history_issues = _detect_history_leakage(req.report_text, req.history_text, req.evidence_text)
if local_history_issues:
    qa.history_leakage.extend(local_history_issues)
    if qa.status == "pass":
        qa.status = "needs_fix"
    local_summary = "本地历史泄漏检查发现风险：" + "；".join(issue.report_text for issue in local_history_issues[:3])
    qa.summary = f"{qa.summary}\n{local_summary}".strip()
```

with:

```python
if req.report_ir is not None:
    local_report = _normalize_report_ir(req.report_ir, fallback_title=DEFAULT_REPORT_TITLE)
else:
    local_report = _markdown_to_report_ir(req.report_text) if req.report_text else ReportIR()
local_qa = _quality_check_report_against_evidence(local_report, req.evidence_text, req.history_text)
qa = _merge_local_qa(qa, local_qa)
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python -m unittest tests.test_report_quality -v
```

Expected:

```text
Ran 8 tests
OK
```

- [ ] **Step 6: Run existing export tests**

Run:

```powershell
python -m unittest tests.test_report_export -v
```

Expected: all existing tests pass. If a test expects `/report/qa` to only merge history leakage, update the assertion to include the new local QA summary while keeping the original behavior.

- [ ] **Step 7: No-Git checkpoint**

Run:

```powershell
git rev-parse --is-inside-work-tree
```

Expected:

```text
fatal: not a git repository
```

Record changed files: `app/main.py`, `tests/test_report_quality.py`.

---

### Task 3: Strengthen Generation and QA Prompts

**Files:**
- Modify: `prompts/report_system_prompt.md`
- Modify: `prompts/report_user_prompt.md`
- Modify: `prompts/report_qa_prompt.md`
- Modify: `prompts/report_qa_fix_prompt.md`
- Modify: `prompts/report_history_prompt.md`
- Modify: `tests/test_report_export.py`

- [ ] **Step 1: Write failing prompt assertions**

Extend `ReportQAEnhancementTests.test_prompt_files_contain_history_revision_and_qa_constraints` in `tests/test_report_export.py` with:

```python
self.assertIn("人工行业分析稿结构基准", system_prompt)
self.assertIn("证据丰富时不要写成短摘要", system_prompt)
self.assertIn("1200", user_prompt)
self.assertIn("逐表检查", qa_prompt)
self.assertIn("表格标题、表头、关键数值、单位", qa_prompt)
self.assertIn("不得压缩已经合格的长报告", qa_fix_prompt)
self.assertIn("不写具体价格、周期、产品范围", history_prompt)
self.assertIn("参考报告只作为结构和风格参考", system_prompt)
```

- [ ] **Step 2: Run the prompt test and verify failure**

Run:

```powershell
python -m unittest tests.test_report_export.ReportQAEnhancementTests.test_prompt_files_contain_history_revision_and_qa_constraints -v
```

Expected: FAIL because the new exact prompt phrases are missing.

- [ ] **Step 3: Update `prompts/report_system_prompt.md`**

Add this section after `结构要求：`:

```text
人工行业分析稿结构基准：
- 参考报告只作为结构和风格参考，不得作为本次公告事实来源。
- 采购文件、集采、接续采购、价格联动类公告，如果网页和附件证据丰富，正文应接近人工行业分析稿，而不是短摘要。
- 证据丰富时不要写成短摘要；导语应有2-3段，中间应按原文规则拆分小节，必要时保留分主题表格，结尾企业关注点保持简短。
- 参考人工稿通常包含自然导语、采购品种或周期说明、价格或报价规则、拟中选或执行规则、分主题表格、企业关注点。模型不需要机械套用标题，但不能遗漏原文明确规则。
- 如果原始公告确实很短，可以短写，但必须避免补充原文没有的事实。
```

- [ ] **Step 4: Update `prompts/report_user_prompt.md`**

Add this requirement under the numbered requirements:

```text
23. 对采购文件、集采、接续采购、价格联动类公告，若输入材料证据丰富，报告正文原则上不少于1200字；不要把采购文件压缩成500字左右的摘要。若原文很短或信息有限，可以短写，但必须只基于原文。
24. 表格拆分后必须保持原表含义、单位、价格口径和适用范围；不得把省级平台最低挂网价、全国最低挂网价、省级集采最低中选价、最高有效申报价、询价基准、参考价混写。
```

- [ ] **Step 5: Update `prompts/report_qa_prompt.md`**

Add this to `质检重点：`:

```text
8. 逐表检查报告表格：核对表格标题、表头、关键数值、单位、价格口径、日期、产品名称是否能在网页正文或附件证据中找到；拆分表格不得改变原表含义。
9. 检查是否接近人工行业分析稿结构基准：自然导语、规则小节、必要表格、简短企业关注点；证据丰富时不能只是500字左右摘要。
10. 参考报告只作为结构和风格参考，不得把参考报告中的事实写入本次报告。
```

- [ ] **Step 6: Update `prompts/report_qa_fix_prompt.md`**

Add this under `修复原则：`:

```text
9. 不得压缩已经合格的长报告；只按 fix_instructions 补充缺失规则、纠正无证据表格值、删除历史泄漏或调整局部表述。
10. 如果质检指出报告过短，应优先从原始证据中补回规则小节和分主题表格，而不是增加空泛评论。
```

- [ ] **Step 7: Update `prompts/report_history_prompt.md`**

Ensure the output requirement includes:

```text
每条短句只描述可复用的观察角度，不写具体价格、周期、产品范围、企业范围、地区范围或采购量。
```

- [ ] **Step 8: Run prompt tests**

Run:

```powershell
python -m unittest tests.test_report_export.ReportQAEnhancementTests.test_prompt_files_contain_history_revision_and_qa_constraints -v
```

Expected:

```text
OK
```

- [ ] **Step 9: No-Git checkpoint**

Run:

```powershell
git rev-parse --is-inside-work-tree
```

Expected:

```text
fatal: not a git repository
```

Record changed files: prompt files and `tests/test_report_export.py`.

---

### Task 4: Update Chatflow Main Generation and Approved Download Branches

**Files:**
- Create: `tests/test_chatflow_blueprint.py`
- Modify: `dify-chatflow-medical-notice-report.yml`

- [ ] **Step 1: Write failing Chatflow blueprint tests**

Create `tests/test_chatflow_blueprint.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHATFLOW = ROOT / "dify-chatflow-medical-notice-report.yml"


class ChatflowBlueprintTests(unittest.TestCase):
    def test_chatflow_has_required_inputs_and_conversation_variables(self) -> None:
        text = CHATFLOW.read_text(encoding="utf-8")

        self.assertIn("mode: advanced-chat", text)
        self.assertIn("notice_url:", text)
        self.assertIn("required: true", text)
        self.assertIn("history_report:", text)
        self.assertIn("required: false", text)
        for variable in [
            "source_evidence",
            "history_insights",
            "current_report_ir",
            "current_final_report",
            "last_qa_summary",
            "last_export_filename",
            "revision_count",
            "download_approved",
        ]:
            self.assertIn(f"{variable}:", text)

    def test_chatflow_separates_generation_revision_and_download_intents(self) -> None:
        text = CHATFLOW.read_text(encoding="utf-8")

        self.assertIn("first_generate", text)
        self.assertIn("revise", text)
        self.assertIn("approve_download", text)
        self.assertIn("when: first_generate", text)
        self.assertIn("when: approve_download", text)

    def test_chatflow_exports_only_after_approval(self) -> None:
        text = CHATFLOW.read_text(encoding="utf-8")

        self.assertIn("export_word:", text)
        export_index = text.index("export_word:")
        approve_index = text.index("when: approve_download")
        self.assertLess(approve_index, export_index)
        self.assertIn("url: http://host.docker.internal:8099/report/export_checked", text)
        first_generate_block = text[text.index("fetch_source:") : text.index("approve_download_export_gate:")]
        self.assertNotIn("/report/export_checked", first_generate_block)

    def test_chatflow_repair_is_conditional(self) -> None:
        text = CHATFLOW.read_text(encoding="utf-8")

        self.assertIn("fix_report_once:", text)
        self.assertIn('when: parse_qa_first.body.qa.status == "needs_fix"', text)
        self.assertIn("answer_without_download:", text)
        self.assertIn("Word 下载将在你确认后生成", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run Chatflow tests and verify failure**

Run:

```powershell
python -m unittest tests.test_chatflow_blueprint -v
```

Expected: FAIL because the blueprint does not yet have `revision_count`, `download_approved`, `approve_download_export_gate`, or delayed export wording.

- [ ] **Step 3: Replace `dify-chatflow-medical-notice-report.yml` with an authoritative blueprint**

Use this structure as the file body:

```yaml
# Dify Chatflow implementation blueprint for 医药公告采购分析报告
#
# This blueprint is the main target flow. It is intentionally explicit because
# Dify DSL fields vary by version; if direct import fails, create equivalent
# nodes in Dify UI and copy prompts from prompts/.

app:
  mode: advanced-chat
  name: 医药公告采购分析报告 Chatflow
  description: 首轮输入公告链接生成报告；可选上传关联项目 Word；质检通过后展示；用户确认后导出 Word。

inputs:
  notice_url:
    type: text-input
    required: true
    label: 公告链接
  history_report:
    type: file
    required: false
    label: 关联项目分析稿 Word（可选）
    allowed_extensions:
      - .doc
      - .docx

conversation_variables:
  source_evidence: ""
  history_insights: ""
  current_report_ir: ""
  current_final_report: ""
  last_qa_summary: ""
  last_export_filename: ""
  revision_count: 0
  download_approved: false

nodes:
  classify_turn:
    type: llm
    purpose: 判断本轮用户意图。
    output_values:
      - first_generate
      - revise
      - approve_download
    rules: |
      first_generate: 会话没有 current_report_ir，或用户提供新的 notice_url。
      approve_download: 用户明确表示“确认”“同意”“可以下载”“导出 Word”“下载 Word”。
      revise: 用户对已生成报告提出修改意见。

  fetch_source:
    type: http-request
    when: first_generate
    method: POST
    url: http://host.docker.internal:8099/analyze
    body: '{"url":"{{notice_url}}","max_attachments":25,"max_combined_chars":180000}'
    save_to_conversation:
      source_evidence: '{{fetch_source.body.evidence_for_llm}}'

  extract_history_doc:
    type: document-extractor
    when: first_generate and history_report exists
    input_file: '{{history_report}}'

  summarize_history:
    type: llm
    when: first_generate and history_report exists
    prompt_file: prompts/report_history_prompt.md
    input:
      history_doc_extractor.text: '{{extract_history_doc.text}}'
    save_to_conversation:
      history_insights: '{{summarize_history.text}}'

  generate_report:
    type: llm
    when: first_generate
    system_prompt_file: prompts/report_system_prompt.md
    user_prompt_file: prompts/report_user_prompt.md
    inputs:
      notice_url: '{{notice_url}}'
      source_evidence: '{{conversation.source_evidence}}'
      history_insights: '{{conversation.history_insights}}'
    output: '<report_ir>...</report_ir>'

  render_report:
    type: http-request
    when: first_generate
    method: POST
    url: http://host.docker.internal:8099/report/render
    body: |
      {
        "markdown": {{generate_report.text | json}},
        "strict_quality": false
      }
    save_to_conversation:
      current_report_ir: '{{render_report.body.report_ir}}'
      current_final_report: '{{render_report.body.report_markdown}}'

  qa_report_first:
    type: llm
    when: first_generate
    prompt_file: prompts/report_qa_prompt.md
    inputs:
      source_evidence: '{{conversation.source_evidence}}'
      history_insights: '{{conversation.history_insights}}'
      current_report_ir: '{{conversation.current_report_ir}}'
      current_final_report: '{{conversation.current_final_report}}'

  parse_qa_first:
    type: http-request
    when: first_generate
    method: POST
    url: http://host.docker.internal:8099/report/qa
    body: |
      {
        "qa_output": {{qa_report_first.text | json}},
        "report_ir": {{conversation.current_report_ir | json}},
        "report_text": {{conversation.current_final_report | json}},
        "history_text": {{conversation.history_insights | json}},
        "evidence_text": {{conversation.source_evidence | json}}
      }
    save_to_conversation:
      last_qa_summary: '{{parse_qa_first.body.qa_summary}}'

  fix_report_once:
    type: llm
    when: parse_qa_first.body.qa.status == "needs_fix"
    prompt_file: prompts/report_qa_fix_prompt.md
    inputs:
      source_evidence: '{{conversation.source_evidence}}'
      history_insights: '{{conversation.history_insights}}'
      current_report_ir: '{{conversation.current_report_ir}}'
      current_final_report: '{{conversation.current_final_report}}'
      qa_result: '{{qa_report_first.text}}'

  render_fixed_report:
    type: http-request
    when: fix_report_once executed
    method: POST
    url: http://host.docker.internal:8099/report/render
    body: |
      {
        "markdown": {{fix_report_once.text | json}},
        "strict_quality": false
      }
    save_to_conversation:
      current_report_ir: '{{render_fixed_report.body.report_ir}}'
      current_final_report: '{{render_fixed_report.body.report_markdown}}'

  qa_report_second:
    type: llm
    when: fix_report_once executed
    prompt_file: prompts/report_qa_prompt.md
    inputs:
      source_evidence: '{{conversation.source_evidence}}'
      history_insights: '{{conversation.history_insights}}'
      current_report_ir: '{{conversation.current_report_ir}}'
      current_final_report: '{{conversation.current_final_report}}'

  parse_qa_second:
    type: http-request
    when: fix_report_once executed
    method: POST
    url: http://host.docker.internal:8099/report/qa
    body: |
      {
        "qa_output": {{qa_report_second.text | json}},
        "report_ir": {{conversation.current_report_ir | json}},
        "report_text": {{conversation.current_final_report | json}},
        "history_text": {{conversation.history_insights | json}},
        "evidence_text": {{conversation.source_evidence | json}}
      }
    save_to_conversation:
      last_qa_summary: '{{parse_qa_second.body.qa_summary}}'

  answer_without_download:
    type: answer
    when: first_generate
    text: |
      {{conversation.current_final_report}}

      ---

      质检摘要：
      {{conversation.last_qa_summary}}

      Word 下载将在你确认后生成。若需要调整报告，请直接提出修改意见；若认可，请回复“确认下载 Word”。

  approve_download_export_gate:
    type: code
    when: approve_download
    code: |
      def main(current_report_ir: str) -> dict:
          approved = bool(current_report_ir)
          return {"download_approved": approved}
    save_to_conversation:
      download_approved: '{{approve_download_export_gate.download_approved}}'

  export_word:
    type: http-request
    when: approve_download
    method: POST
    url: http://host.docker.internal:8099/report/export_checked
    body: |
      {
        "report_ir": {{conversation.current_report_ir | json}},
        "qa_output": "{\"status\":\"pass\",\"issues\":[],\"unsupported_claims\":[],\"history_leakage\":[],\"missing_rules\":[],\"language_issues\":[],\"fix_instructions\":[],\"summary\":\"用户确认后导出\"}",
        "report_text": {{conversation.current_final_report | json}},
        "history_text": {{conversation.history_insights | json}},
        "evidence_text": {{conversation.source_evidence | json}},
        "strict_quality": true
      }
    save_to_conversation:
      last_export_filename: '{{export_word.body.filename}}'

  answer_with_download:
    type: answer
    when: approve_download
    text: |
      已生成 Word。

      下载链接：{{export_word.body.download_url}}
      文件名：{{export_word.body.filename}}
```

- [ ] **Step 4: Run Chatflow tests**

Run:

```powershell
python -m unittest tests.test_chatflow_blueprint -v
```

Expected:

```text
Ran 4 tests
OK
```

- [ ] **Step 5: No-Git checkpoint**

Run:

```powershell
git rev-parse --is-inside-work-tree
```

Expected:

```text
fatal: not a git repository
```

Record changed files: `dify-chatflow-medical-notice-report.yml`, `tests/test_chatflow_blueprint.py`.

---

### Task 5: Verify Main Generation, QA, and Approved Download Together

**Files:**
- Modify: none unless a previous task reveals a mismatch
- Test: all Python unittest files

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m unittest tests.test_report_quality tests.test_chatflow_blueprint -v
```

Expected:

```text
OK
```

- [ ] **Step 2: Run full local unit test suite**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected:

```text
OK
```

- [ ] **Step 3: Rebuild and run container tests**

Run:

```powershell
docker compose up -d --build
docker exec medical-notice-analyzer python -m unittest discover -s tests -v
```

Expected:

```text
OK
```

- [ ] **Step 4: Run health check**

Run:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8099/health
```

Expected response body includes:

```text
{"status":"ok"}
```

- [ ] **Step 5: No-Git checkpoint**

Run:

```powershell
git rev-parse --is-inside-work-tree
```

Expected:

```text
fatal: not a git repository
```

Record test results and changed files in the final implementation summary.

---

### Task 6: Update Operational Documentation

**Files:**
- Modify: `README.md`
- Modify: `PROJECT_CONTEXT.md`
- Modify: `NEXT_CHAT_HANDOFF.md`

- [ ] **Step 1: Update README main-flow description**

In `README.md`, add a section named `新版 Chatflow 主流程`:

```markdown
## 新版 Chatflow 主流程

新版主流程建议使用 `dify-chatflow-medical-notice-report.yml` 搭建 Dify advanced-chat 应用。

- 首轮必须输入公告 URL。
- 可选上传关联项目 Word，系统只将其提炼为串联分析参考，不作为本次公告事实来源。
- 系统先抓取网页正文和附件，再生成 ReportIR，由后端渲染 Markdown。
- 质检会检查事实依据、表格一致性、历史泄漏和人工分析稿结构接近度。
- 质检合格后先展示报告和质检摘要，不立即导出 Word。
- 用户确认下载后才调用 `/report/export_checked` 生成 Word。
- 用户反馈修订分支排在后续阶段实现；当前先完成生成质量、质检和确认后下载。
```

- [ ] **Step 2: Update handoff files**

In `PROJECT_CONTEXT.md` and `NEXT_CHAT_HANDOFF.md`, add:

```markdown
## 新版目标状态

当前设计已批准：主流程转向 Dify Chatflow。20 节点 Workflow 保留为兼容版本。优先完成：报告深度增强、可选关联 Word、质检、质检通过后展示、用户确认后导出 Word。用户反馈后修订分支可最后实现。
```

- [ ] **Step 3: Run documentation grep checks**

Run:

```powershell
rg -n "新版 Chatflow 主流程|新版目标状态|用户确认后" README.md PROJECT_CONTEXT.md NEXT_CHAT_HANDOFF.md
```

Expected: each modified document contains the new status text.

- [ ] **Step 4: No-Git checkpoint**

Run:

```powershell
git rev-parse --is-inside-work-tree
```

Expected:

```text
fatal: not a git repository
```

Record changed files: `README.md`, `PROJECT_CONTEXT.md`, `NEXT_CHAT_HANDOFF.md`.

---

### Task 7: Implement User Feedback Revision Branch Last

This task is intentionally last because the user said revision after feedback can be deferred until other functionality is complete.

**Files:**
- Modify: `dify-chatflow-medical-notice-report.yml`
- Modify: `prompts/report_revision_prompt.md`
- Modify: `tests/test_chatflow_blueprint.py`

- [ ] **Step 1: Add failing revision branch assertions**

Extend `tests/test_chatflow_blueprint.py`:

```python
    def test_chatflow_revision_branch_uses_existing_evidence_and_does_not_export(self) -> None:
        text = CHATFLOW.read_text(encoding="utf-8")

        self.assertIn("revise_report:", text)
        self.assertIn("when: revise", text)
        self.assertIn("source_evidence: '{{conversation.source_evidence}}'", text)
        self.assertIn("current_report_ir: '{{conversation.current_report_ir}}'", text)
        self.assertIn("current_final_report: '{{conversation.current_final_report}}'", text)
        revise_block = text[text.index("revise_report:") : text.index("approve_download_export_gate:")]
        self.assertNotIn("/report/export_checked", revise_block)
```

- [ ] **Step 2: Run revision branch test and verify failure**

Run:

```powershell
python -m unittest tests.test_chatflow_blueprint.ChatflowBlueprintTests.test_chatflow_revision_branch_uses_existing_evidence_and_does_not_export -v
```

Expected: FAIL because `revise_report` is not yet in the blueprint.

- [ ] **Step 3: Strengthen `prompts/report_revision_prompt.md`**

Add:

```text
8. 用户反馈修订只能基于 source_evidence、当前 ReportIR、当前 Markdown 和用户反馈；不得新增网页或附件没有的事实。
9. 除非用户明确要求删除，否则保留已经通过质检的规则小节、表格和企业关注点。
10. 修订后仍只输出 <report_ir> 结构化 JSON，由后端渲染 Markdown；不要输出 <final_report>。
```

Also remove any requirement that still says revision must output `<final_report>`.

- [ ] **Step 4: Add revision nodes before approve-download in Chatflow YAML**

Insert before `approve_download_export_gate`:

```yaml
  revise_report:
    type: llm
    when: revise
    prompt_file: prompts/report_revision_prompt.md
    inputs:
      source_evidence: '{{conversation.source_evidence}}'
      history_insights: '{{conversation.history_insights}}'
      current_report_ir: '{{conversation.current_report_ir}}'
      current_final_report: '{{conversation.current_final_report}}'
      user_request: '{{sys.query}}'

  render_revised_report:
    type: http-request
    when: revise
    method: POST
    url: http://host.docker.internal:8099/report/render
    body: |
      {
        "markdown": {{revise_report.text | json}},
        "strict_quality": false
      }
    save_to_conversation:
      current_report_ir: '{{render_revised_report.body.report_ir}}'
      current_final_report: '{{render_revised_report.body.report_markdown}}'

  qa_revised_report:
    type: llm
    when: revise
    prompt_file: prompts/report_qa_prompt.md
    inputs:
      source_evidence: '{{conversation.source_evidence}}'
      history_insights: '{{conversation.history_insights}}'
      current_report_ir: '{{conversation.current_report_ir}}'
      current_final_report: '{{conversation.current_final_report}}'

  parse_qa_revised:
    type: http-request
    when: revise
    method: POST
    url: http://host.docker.internal:8099/report/qa
    body: |
      {
        "qa_output": {{qa_revised_report.text | json}},
        "report_ir": {{conversation.current_report_ir | json}},
        "report_text": {{conversation.current_final_report | json}},
        "history_text": {{conversation.history_insights | json}},
        "evidence_text": {{conversation.source_evidence | json}}
      }
    save_to_conversation:
      last_qa_summary: '{{parse_qa_revised.body.qa_summary}}'

  answer_revised_without_download:
    type: answer
    when: revise
    text: |
      {{conversation.current_final_report}}

      ---

      质检摘要：
      {{conversation.last_qa_summary}}

      修订已完成。Word 下载仍需你确认后生成。
```

- [ ] **Step 5: Run revision branch tests**

Run:

```powershell
python -m unittest tests.test_chatflow_blueprint -v
```

Expected:

```text
OK
```

- [ ] **Step 6: Run prompt regression test**

Run:

```powershell
python -m unittest tests.test_report_export.ReportQAEnhancementTests.test_prompt_files_contain_history_revision_and_qa_constraints -v
```

Expected:

```text
OK
```

- [ ] **Step 7: No-Git checkpoint**

Run:

```powershell
git rev-parse --is-inside-work-tree
```

Expected:

```text
fatal: not a git repository
```

Record changed files: `dify-chatflow-medical-notice-report.yml`, `prompts/report_revision_prompt.md`, `tests/test_chatflow_blueprint.py`.

---

### Task 8: Final Verification and Handoff Update

**Files:**
- Modify: `NEXT_CHAT_HANDOFF.md`
- Modify: `PROJECT_CONTEXT.md`
- Test: full unit and container test suite

- [ ] **Step 1: Run all local tests**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected:

```text
OK
```

- [ ] **Step 2: Rebuild container**

Run:

```powershell
docker compose up -d --build
```

Expected: `medical-notice-analyzer` container is recreated or remains running.

- [ ] **Step 3: Run container tests**

Run:

```powershell
docker exec medical-notice-analyzer python -m unittest discover -s tests -v
```

Expected:

```text
OK
```

- [ ] **Step 4: Run health check**

Run:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8099/health
```

Expected response body includes:

```text
{"status":"ok"}
```

- [ ] **Step 5: Update handoff with final test evidence**

In `NEXT_CHAT_HANDOFF.md`, update the "最近验证结果" section by copying the exact two-line unittest summary printed by the container test command. For example, if the command prints `Ran 40 tests` followed by `OK`, write:

```text
Ran 40 tests
OK
```

Also add:

```text
新版 Chatflow 蓝本已包含 first_generate、revise、approve_download 三分支。主功能优先级为：生成质量与本地质检、可选关联 Word、确认后导出 Word；用户反馈修订分支已作为最后阶段实现。
```

- [ ] **Step 6: No-Git checkpoint**

Run:

```powershell
git rev-parse --is-inside-work-tree
```

Expected:

```text
fatal: not a git repository
```

Record all changed files and all verification outputs in the final summary.
