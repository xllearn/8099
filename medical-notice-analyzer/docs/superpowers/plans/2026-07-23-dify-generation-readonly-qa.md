# Dify Generation With Read-Only QA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change only the `.87` report workflow to generate one initial report, run one read-only semantic QA pass, and return the untouched initial report with an accurate delivery status.

**Architecture:** Remove the internal automatic revision and second-QA branch from the `.87` Dify workflow, then route the first QA result directly to the initial-result node. Keep the backend fragment fallback and local quality gate, but add explicit initial-generation validation and source/length telemetry so a model-contract fragment cannot be mistaken for a normal report. Preserve the `.88` environment, the `.87` 240000-character and 870400-byte evidence limits, and `ENABLE_EVIDENCE_SUMMARY_LLM=false`.

**Tech Stack:** Python 3, FastAPI, `unittest`, YAML-based Dify 1.14.2 workflow, Docker, Docker Compose, PowerShell, SSH, in-app browser automation.

---

## Execution boundary and fixed targets

- Worktree: `C:\Users\admin\.config\superpowers\worktrees\htmldataconclusion\fix-87-dify-near-limit-hardcode`
- Branch: `codex/new-server-87-medical-notice-analyzer-20260722`
- GitHub remote: `github-8099` -> `https://github.com/xllearn/8099.git`
- Analyzer host: `192.168.34.87:8099`
- Analyzer container: `medical-notice-analyzer-development-87`
- Compose project: `medical-notice-analyzer-development-87-r7`
- Analyzer install root: `/opt/medical-notice-analyzer`
- Current analyzer image at plan time: `medical-notice-analyzer:quality87-summary-batching-4da55b7-r1`
- Current analyzer release at plan time: `/opt/medical-notice-analyzer-releases/20260723-013721-4da55b7-summary-batching-r1`
- `.87` Dify origin: `http://192.168.34.87`
- `.87` Dify app ID: `8a588d09-3f29-45d8-9eaf-4cf1f5045958`
- Current published workflow ID at plan time: `ab6a94df-12e7-4724-8f28-5c391f03bceb`
- Current draft workflow ID at plan time: `7f329d83-555f-4c19-a47b-0bf9d037ede1`
- Zhejiang regression material: `menu_code=project_notice`, `articleid=c1d2fc19-1269-42e7-b8db-dddbe2b1e711`

Do not start the project, import application modules, run Python, run unit tests, call local HTTP endpoints, or build/run Docker on Windows. Local work is limited to reading files, applying patches, Git static checks, commits, archives, checksums, and pushing the branch. All executable verification occurs after deployment on `.87`.

Do not connect to, modify, restart, or deploy `.88` or `.86`.

The revision removal in this plan is limited to the automatic QA-triggered revision branch inside the `.87` Dify graph. Keep the backend user-initiated `/analysis/runs/{run_id}/revise` feedback API and its history behavior unchanged; it is outside this change.

At plan time, the live image/Compose revision and GitHub deployment SHA are `4da55b750a52d21c04f0c55c4ee4ac71894691e2`. That Git object is not present in the local repository, but the live release matches local implementation commit `1946995` across all 78 controlled `app/**/*.py`, `tests/**/*.py`, and workflow YAML files after CRLF/LF normalization. The release-local `.deployed_commit` contains an older stale value, so deployment provenance and rollback decisions must use the image/Compose revision label, release name, GitHub deployment SHA, and live-to-branch content hashes—not `.deployed_commit`.

### Task 1: Add the server-run regression specification

**Files:**
- Modify: `medical-notice-analyzer/tests/test_dify_human_style_workflow.py`
- Modify: `medical-notice-analyzer/tests/test_records_api.py`

- [ ] **Step 1: Specify the two-LLM Dify topology**

Add `import json` to `test_dify_human_style_workflow.py`. Replace assertions that require `revise_report` and `qa_revised_report`, then add this test:

```python
def test_workflow_has_generation_and_readonly_qa_only(self) -> None:
    path = Path(__file__).resolve().parents[1] / "dify_workflow_pack_id_human_style.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    graph = data["workflow"]["graph"]
    nodes = graph["nodes"]
    by_id = {node["id"]: node["data"] for node in nodes}
    node_ids = set(by_id)

    llm_ids = {
        node["id"]
        for node in nodes
        if node.get("data", {}).get("type") == "llm"
    }
    self.assertEqual(llm_ids, {"generate_report", "qa_report_first"})

    removed_ids = {
        "quality_gate",
        "revise_report",
        "parse_revision_json",
        "qa_revised_report",
        "parse_revised_quality_json",
        "final_revised_result",
        "end_revised",
    }
    self.assertTrue(removed_ids.isdisjoint(node_ids))

    edge_pairs = {
        (edge["source"], edge["target"])
        for edge in graph["edges"]
    }
    self.assertIn(("parse_generation_json", "qa_report_first"), edge_pairs)
    self.assertIn(("qa_report_first", "parse_quality_json"), edge_pairs)
    self.assertIn(("parse_quality_json", "final_initial_result"), edge_pairs)
    self.assertIn(("final_initial_result", "end_initial"), edge_pairs)
    self.assertFalse(
        any(source in removed_ids or target in removed_ids for source, target in edge_pairs)
    )
```

- [ ] **Step 2: Specify that failed QA cannot change the initial report**

Add this test to the same file:

```python
def test_readonly_qa_failure_keeps_initial_report_and_marks_review(self) -> None:
    path = Path(__file__).resolve().parents[1] / "dify_workflow_pack_id_human_style.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    by_id = {
        node["id"]: node["data"]
        for node in data["workflow"]["graph"]["nodes"]
    }
    namespace: dict[str, object] = {}
    exec(by_id["final_initial_result"]["code"], namespace)

    initial_markdown = "## 导语\n\n初稿正文。\n\n## 一、规则分析\n\n规则内容。"
    generation = json.dumps(
        {
            "version": 1,
            "report_title": "浙江采购公告分析",
            "report_markdown": initial_markdown,
            "generation_warnings": [],
        },
        ensure_ascii=False,
    )
    quality = json.dumps(
        {
            "passed": False,
            "round": 1,
            "issues": [
                {
                    "issue_id": "Q001",
                    "severity": "major",
                    "problem_type": "summary_only",
                }
            ],
        },
        ensure_ascii=False,
    )

    result = namespace["main"]("pack-zhejiang", generation, quality)
    result_json = json.loads(result["result"])

    self.assertEqual(result_json["status"], "needs_manual_review")
    self.assertEqual(result_json["report_markdown"], initial_markdown)
    self.assertEqual(result_json["version"], 1)
    self.assertFalse(result_json["quality_check"]["passed"])
    self.assertEqual(result_json["remaining_issues"][0]["issue_id"], "Q001")
```

Add a parser consistency test so a severe issue cannot be marked as passed:

```python
def test_parse_quality_blocks_severe_issue_even_if_model_marks_passed(self) -> None:
    path = Path(__file__).resolve().parents[1] / "dify_workflow_pack_id_human_style.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    by_id = {
        node["id"]: node["data"]
        for node in data["workflow"]["graph"]["nodes"]
    }
    namespace: dict[str, object] = {}
    exec(by_id["parse_quality_json"]["code"], namespace)
    quality_text = json.dumps(
        {
            "passed": True,
            "round": 1,
            "issues": [
                {
                    "issue_id": "Q001",
                    "severity": "major",
                    "problem_type": "unsupported_claim",
                }
            ],
        },
        ensure_ascii=False,
    )

    parsed = namespace["main"](quality_text)
    quality = json.loads(parsed["quality_json"])

    self.assertFalse(quality["passed"])
    self.assertEqual(quality["issues"][0]["issue_id"], "Q001")
```

- [ ] **Step 3: Update the existing workflow prompt assertions**

Change the existing prompt test so only `generate_report` and `qa_report_first` must use JSON structured output and report memory:

```python
for node_id in ["generate_report", "qa_report_first"]:
    model_params = by_id[node_id]["model"]["completion_params"]
    self.assertEqual(model_params.get("response_format"), "json_object")
    self.assertTrue(by_id[node_id]["structured_output"]["enabled"])

generation_prompt = "\n".join(
    item.get("text", "")
    for item in by_id["generate_report"]["prompt_template"]
)
qa_prompt = "\n".join(
    item.get("text", "")
    for item in by_id["qa_report_first"]["prompt_template"]
)
self.assertIn("人工报告", generation_prompt)
self.assertIn("企业关注点", generation_prompt)
self.assertIn("主材料核心规则", qa_prompt)
self.assertIn("附件表格摘要", qa_prompt)
self.assertIn("只识别和记录问题", qa_prompt)
self.assertIn("不得修改 report_markdown", qa_prompt)
self.assertIn("不触发自动修订", qa_prompt)
self.assertNotIn("只有 fatal/high/major 需要自动修订", qa_prompt)
```

- [ ] **Step 4: Specify initial-generation validation and telemetry**

Add these tests to `RecordsApiTests` in `test_records_api.py`:

```python
def test_valid_initial_generation_records_source_and_lengths(self) -> None:
    markdown = "\n\n".join(
        [
            "## 导语",
            "本报告基于浙江采购公告梳理执行规则和企业影响。",
            "## 一、规则分析",
            "公告明确了产品范围、申报要求和执行安排。" * 40,
            "## 二、企业关注",
            "企业需要按材料披露的范围核对产品和需求信息。" * 20,
        ]
    )
    pack = {
        "primary_materials": [
            {
                "title": "浙江采购公告",
                "content_text": "浙江采购公告原文证据。" * 300,
                "attachments": [],
            }
        ],
        "auxiliary_materials": [],
    }
    result = {
        "status": "finished",
        "report_title": "浙江采购公告分析",
        "report_markdown": markdown,
        "quality_check": {"passed": True, "issues": []},
        "remaining_issues": [],
        "generation_warnings": [],
    }

    observed = main_module._repair_unusable_dify_result(result, pack)

    self.assertEqual(observed["candidate_source"], "initial_generation")
    self.assertEqual(observed["generation_validation_code"], "OK")
    self.assertEqual(observed["generation_report_chars"], len(markdown))
    self.assertEqual(observed["final_report_chars"], len(markdown))
    self.assertTrue(observed["qa_passed"])
    self.assertEqual(observed["qa_issue_count"], 0)
    self.assertFalse(observed["fallback_used"])
```

Add a structure test so a long introduction without a following body section is still rejected:

```python
def test_long_initial_generation_without_following_section_uses_fallback(self) -> None:
    markdown = "## 导语\n\n" + ("浙江采购公告情况说明。" * 200)
    pack = {
        "primary_materials": [
            {
                "title": "浙江采购公告",
                "content_text": "浙江采购公告原文证据。" * 300,
                "summary": "公告包含企业、产品和需求量信息。",
                "attachments": [],
            }
        ],
        "auxiliary_materials": [],
    }
    result = {
        "status": "finished",
        "report_title": "浙江采购公告分析",
        "report_markdown": markdown,
        "quality_check": {"passed": True, "issues": []},
        "remaining_issues": [],
        "generation_warnings": [],
    }

    observed = main_module._repair_unusable_dify_result(result, pack)

    self.assertEqual(observed["candidate_source"], "backend_pack_fallback")
    self.assertEqual(observed["generation_validation_code"], "MISSING_REPORT_STRUCTURE")
    self.assertEqual(observed["status"], "needs_manual_review")
    self.assertTrue(observed["fallback_used"])
```

```python
def test_contract_instruction_fragment_records_backend_fallback_source(self) -> None:
    fragment = "报告内容，其中所有双引号转义为双引号，换行符等特殊字符也要正确处理。"
    pack = {
        "primary_materials": [
            {
                "title": "浙江采购公告",
                "content_text": "浙江采购公告原文证据。" * 300,
                "summary": "公告包含企业、产品和需求量信息。",
                "attachments": [],
            }
        ],
        "auxiliary_materials": [],
    }
    result = {
        "status": "needs_manual_review",
        "report_title": "浙江采购公告分析",
        "report_markdown": fragment,
        "quality_check": {
            "passed": False,
            "issues": [{"issue_id": "Q_MODEL", "severity": "major"}],
        },
        "remaining_issues": [{"issue_id": "Q_MODEL", "severity": "major"}],
        "generation_warnings": [],
    }

    repaired = main_module._repair_unusable_dify_result(result, pack)

    self.assertEqual(repaired["candidate_source"], "backend_pack_fallback")
    self.assertEqual(repaired["generation_validation_code"], "CONTRACT_FRAGMENT")
    self.assertEqual(repaired["generation_report_chars"], len(fragment))
    self.assertEqual(repaired["final_report_chars"], len(repaired["report_markdown"]))
    self.assertTrue(repaired["fallback_used"])
    self.assertEqual(repaired["fallback_reason"], "OUTPUT_TRUNCATED")
    self.assertEqual(repaired["fallback_provider"], "backend_pack_fallback")
    self.assertIn("OUTPUT_TRUNCATED", repaired["generation_failure_codes"])
    self.assertFalse(repaired["qa_passed"])
    self.assertGreaterEqual(repaired["qa_issue_count"], 1)
```

Add one final-length refresh test:

```python
def test_local_quality_pipeline_refreshes_final_report_chars(self) -> None:
    markdown = (
        "## 导语\n\n浙江采购公告分析。\n\n"
        "## 一、规则分析\n\n公告明确产品范围和执行要求。" * 60
    )
    pack = {
        "primary_materials": [
            {
                "title": "浙江采购公告",
                "content_text": "公告明确产品范围和执行要求。" * 200,
                "attachments": [],
            }
        ],
        "auxiliary_materials": [],
    }
    result = {
        "status": "finished",
        "report_title": "浙江采购公告分析",
        "report_markdown": markdown,
        "quality_check": {"passed": True, "issues": []},
        "remaining_issues": [],
        "generation_warnings": [],
        "candidate_source": "initial_generation",
        "generation_report_chars": len(markdown),
        "final_report_chars": 0,
    }

    gated = main_module._apply_local_quality_gate_to_dify_result(result, pack)

    self.assertEqual(
        gated["final_report_chars"],
        len(gated["report_markdown"]),
    )
```

- [ ] **Step 5: Commit the server-run specification without executing it locally**

Run only static Git checks:

```powershell
git diff --check
git status --short
git add -- `
  medical-notice-analyzer/tests/test_dify_human_style_workflow.py `
  medical-notice-analyzer/tests/test_records_api.py
git diff --cached --check
git commit -m "test: specify Dify read-only QA workflow"
```

Do not run Python or `unittest` on Windows.

### Task 2: Implement initial-generation validation and observability

**Files:**
- Modify: `medical-notice-analyzer/app/main.py:5213-5243`
- Modify: `medical-notice-analyzer/app/main.py:5364-5395`

- [ ] **Step 1: Replace the fragment boolean with a reasoned validator**

Add this constant and helper immediately before `_is_fragmentary_dify_report_markdown`:

```python
DIFY_REPORT_CONTRACT_FRAGMENT_PHRASES = (
    "报告内容，其中所有双引号转义",
    "所有双引号转义为",
    "换行符等特殊字符",
    "只返回以下json结构",
    '"report_markdown":',
)


def _dify_report_validation_code(markdown: Any, pack: dict[str, Any]) -> str:
    text = str(markdown or "").strip()
    if _is_unusable_report_markdown(text):
        return "EMPTY_OR_PLACEHOLDER"

    compact = re.sub(r"\s+", "", text)
    lowered = compact.lower()
    if any(phrase in lowered for phrase in DIFY_REPORT_CONTRACT_FRAGMENT_PHRASES):
        return "CONTRACT_FRAGMENT"

    has_intro = bool(
        re.search(r"(^|\n)\s*(?:#{1,6}\s+)?导语", text)
    )
    has_first_section = bool(
        re.search(r"(^|\n)\s*(?:#{1,6}\s+)?(?:一|1)[、.．]", text)
    )
    has_later_body_section = bool(
        re.search(
            r"(^|\n)\s*(?:#{1,6}\s+)?(?:二|三|四|五|六|七|八|九|十|2|3|4|5|6|7|8|9|10)[、.．]",
            text,
        )
    )
    has_intro_or_first_section = has_intro or has_first_section
    has_report_structure = (
        has_intro and (has_first_section or has_later_body_section)
    ) or (
        has_first_section and has_later_body_section
    )
    starts_midstream = bool(re.match(r"^\s*(?:[-*]\s+|\|)", text))
    starts_after_first_section = bool(
        re.match(
            r"^\s*(?:#{1,6}\s+)?(?:二|三|四|五|六|七|八|九|十|2|3|4|5|6|7|8|9|10)[、.．]",
            text,
        )
    )
    if starts_after_first_section and not has_intro_or_first_section:
        return "STARTS_MIDSTREAM"

    if not has_report_structure:
        return "MISSING_REPORT_STRUCTURE"

    diagnostics = build_pack_diagnostics(pack)
    weighted_chars = int(
        diagnostics.get("weighted_evidence_chars")
        or diagnostics.get("total_content_chars")
        or 0
    )
    if weighted_chars >= 1000 and len(text) < 700:
        return "REPORT_TOO_SHORT"
    if len(text) < 500 and starts_midstream:
        return "REPORT_TOO_SHORT"
    return "OK"
```

Keep the compatibility predicate small:

```python
def _is_fragmentary_dify_report_markdown(
    markdown: Any,
    pack: dict[str, Any],
) -> bool:
    return _dify_report_validation_code(markdown, pack) != "OK"
```

- [ ] **Step 2: Record the initial candidate before any fallback**

Start `_repair_unusable_dify_result` with:

```python
source_markdown = str(result.get("report_markdown") or "")
validation_code = _dify_report_validation_code(source_markdown, pack)
quality_check = (
    dict(result.get("quality_check"))
    if isinstance(result.get("quality_check"), dict)
    else {"passed": None, "issues": []}
)
quality_issues = (
    list(quality_check.get("issues") or [])
    if isinstance(quality_check.get("issues"), list)
    else []
)
observed = dict(result)
observed.update(
    {
        "candidate_source": "initial_generation",
        "generation_report_chars": len(source_markdown),
        "final_report_chars": len(source_markdown),
        "generation_validation_code": validation_code,
        "qa_passed": quality_check.get("passed"),
        "qa_issue_count": len(quality_issues),
        "fallback_used": bool(result.get("fallback_used")),
    }
)
if validation_code == "OK":
    return observed
```

- [ ] **Step 3: Preserve QA issues while marking a fragment fallback**

Replace the current fallback update with:

```python
title, markdown, fallback_warnings = _fallback_report_from_pack(pack)
warnings = [
    *list(result.get("warnings") or []),
    *list(result.get("generation_warnings") or []),
    *fallback_warnings,
]
issue = {
    "issue_id": "Q_DIFY_FRAGMENTARY_REPORT",
    "severity": "high",
    "problem_type": "empty_or_fragmentary_report",
    "report_text": source_markdown,
    "source_basis": "evidence_pack",
    "fix_instruction": (
        "Dify 返回正文过短、占位或包含输出契约片段，"
        "已启用后端兜底报告，仍需人工复核。"
    ),
}
if not any(
    isinstance(item, dict)
    and item.get("issue_id") == issue["issue_id"]
    for item in quality_issues
):
    quality_issues.append(issue)
quality_check["passed"] = False
quality_check["issues"] = quality_issues
remaining_issues = [
    item
    for item in list(result.get("remaining_issues") or [])
    if isinstance(item, dict)
]
if not any(item.get("issue_id") == issue["issue_id"] for item in remaining_issues):
    remaining_issues.append(issue)
generation_failure_codes = _dedupe_strings(
    [
        *list(result.get("generation_failure_codes") or []),
        "OUTPUT_TRUNCATED",
    ]
)
observed.update(
    {
        "status": "needs_manual_review",
        "report_title": (
            title
            if _is_unusable_report_markdown(result.get("report_title"))
            else result.get("report_title") or title
        ),
        "report_markdown": markdown,
        "quality_check": quality_check,
        "generation_warnings": warnings,
        "warnings": warnings,
        "remaining_issues": remaining_issues,
        "candidate_source": "backend_pack_fallback",
        "final_report_chars": len(markdown),
        "fallback_used": True,
        "fallback_reason": "OUTPUT_TRUNCATED",
        "fallback_provider": "backend_pack_fallback",
        "generation_failure_codes": generation_failure_codes,
        "qa_passed": False,
        "qa_issue_count": len(quality_issues),
    }
)
return observed
```

- [ ] **Step 4: Refresh final length after the existing backend safety pipeline**

Update `_apply_local_quality_gate_to_dify_result` without changing its repair or gate components:

```python
def _apply_local_quality_gate_to_dify_result(
    result: dict[str, Any],
    pack: dict[str, Any],
) -> dict[str, Any]:
    generation_result = ReportGenerationResult.from_legacy_result(
        result,
        provider=str(result.get("provider") or "dify"),
    )
    repaired_result = ForbiddenPhraseRepairer().run(generation_result, pack)
    gated = QualityGate().run(repaired_result, pack).to_legacy_result()
    gated["final_report_chars"] = len(str(gated.get("report_markdown") or ""))
    return gated
```

This step only refreshes telemetry after the already-existing safety pipeline. It does not add a new repair action or enable unsupported-fact repair.

- [ ] **Step 5: Perform static review and commit**

Run:

```powershell
git diff --check
git diff -- `
  medical-notice-analyzer/app/main.py `
  medical-notice-analyzer/tests/test_records_api.py
git add -- medical-notice-analyzer/app/main.py
git diff --cached --check
git commit -m "fix: validate initial Dify report candidates"
```

Do not execute application code locally.

### Task 3: Reduce the Dify workflow to generation plus read-only QA

**Files:**
- Modify: `medical-notice-analyzer/dify_workflow_pack_id_human_style.yml`

- [ ] **Step 1: Update workflow identity and QA wording**

Change the DSL version from `0.1.5` to `0.1.6`. Update the app description to state that the workflow performs one generation and one read-only QA pass.

In the first QA prompt, replace the automatic-revision sentence with:

```text
severity 只能使用 fatal、high、major、minor。质检只识别和记录问题，不得修改 report_markdown。passed=false 时报告进入 needs_manual_review，不触发自动修订。
```

The QA output contract becomes:

```json
{
  "passed": false,
  "round": 1,
  "issues": [
    {
      "issue_id": "Q001",
      "severity": "high",
      "problem_type": "unsupported_claim",
      "report_text": "",
      "source_basis": "",
      "fix_instruction": ""
    }
  ]
}
```

Remove `revision_instruction` from the first-QA structured-output schema and required fields.

- [ ] **Step 2: Make Parse Quality JSON read-only**

Its Python code must return only normalized QA data:

```python
def main(quality_text: str) -> dict:
    import json
    import re

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

    try:
        data = extract_json(quality_text)
    except Exception as exc:
        data = {
            "passed": False,
            "round": 1,
            "issues": [
                {
                    "issue_id": "Q_PARSE",
                    "severity": "fatal",
                    "problem_type": "quality_json_parse_failed",
                    "report_text": "",
                    "source_basis": "",
                    "fix_instruction": str(exc),
                }
            ],
        }
    if not isinstance(data, dict):
        data = {}
    issues = data.get("issues")
    if not isinstance(issues, list):
        issues = []
    blocking = [
        issue
        for issue in issues
        if isinstance(issue, dict)
        and str(issue.get("severity") or "").lower()
        in {"fatal", "high", "major"}
    ]
    data["passed"] = bool(data.get("passed")) and not blocking
    data["round"] = 1
    data["issues"] = issues
    return {
        "quality_json": json.dumps(data, ensure_ascii=False),
        "issues_json": json.dumps(issues, ensure_ascii=False),
    }
```

Its declared outputs must be only `quality_json` and `issues_json`.

- [ ] **Step 3: Make Final Result preserve the initial report**

Use this node code:

```python
def main(pack_id: str, generation_json: str, quality_json: str) -> dict:
    import json

    try:
        generation = json.loads(generation_json or "{}")
    except Exception:
        generation = {}
    try:
        quality = json.loads(quality_json or "{}")
    except Exception:
        quality = {
            "passed": False,
            "round": 1,
            "issues": [
                {
                    "issue_id": "Q_PARSE",
                    "severity": "fatal",
                    "problem_type": "quality_json_parse_failed",
                }
            ],
        }
    issues = quality.get("issues") if isinstance(quality, dict) else []
    if not isinstance(issues, list):
        issues = []
    passed = bool(quality.get("passed"))
    remaining = [] if passed else issues
    result = {
        "status": "finished" if passed else "needs_manual_review",
        "pack_id": pack_id,
        "report_title": generation.get("report_title") or "",
        "report_markdown": generation.get("report_markdown") or "",
        "version": int(generation.get("version") or 1),
        "quality_check": quality,
        "generation_warnings": generation.get("generation_warnings") or [],
        "remaining_issues": remaining,
    }
    return {
        "result": json.dumps(result, ensure_ascii=False),
        "status": result["status"],
        "pack_id": pack_id,
        "report_title": result["report_title"],
        "report_markdown": result["report_markdown"],
        "version": str(result["version"]),
        "quality_check": json.dumps(quality, ensure_ascii=False),
        "generation_warnings": json.dumps(
            result["generation_warnings"],
            ensure_ascii=False,
        ),
        "remaining_issues": json.dumps(remaining, ensure_ascii=False),
    }
```

- [ ] **Step 4: Remove revision nodes and make one direct edge**

Delete these node IDs:

```text
quality_gate
revise_report
parse_revision_json
qa_revised_report
parse_revised_quality_json
final_revised_result
end_revised
```

Delete every edge whose source or target is one of those IDs. Add:

```yaml
- data:
    isInIteration: false
    isInLoop: false
    sourceType: code
    targetType: code
  id: parse-quality-to-final-initial
  source: parse_quality_json
  sourceHandle: source
  target: final_initial_result
  targetHandle: target
  type: custom
  zIndex: 0
```

Keep the initial and error end nodes. Do not change the `.87` callback URL:

```text
http://192.168.34.87:8099/analysis/packs/{{#start_node.pack_id#}}
```

- [ ] **Step 5: Perform static graph review and commit**

Use text-only checks:

```powershell
rg -n "Revise Report JSON|Parse Revision JSON|Quality Check Round 2|Parse Revised Quality JSON|Final Revised Result" `
  medical-notice-analyzer/dify_workflow_pack_id_human_style.yml
rg -n "Generate Report JSON|Quality Check Round 1|Parse Quality JSON|Final Result" `
  medical-notice-analyzer/dify_workflow_pack_id_human_style.yml
git diff --check
```

Expected: the first search has no matches; the second search shows exactly one of each retained node title.

Then commit:

```powershell
git add -- `
  medical-notice-analyzer/dify_workflow_pack_id_human_style.yml `
  medical-notice-analyzer/tests/test_dify_human_style_workflow.py
git diff --cached --check
git commit -m "fix: keep Dify quality review read-only"
```

### Task 4: Review scope and push the isolated branch

**Files:** none.

- [ ] **Step 1: Confirm branch, commits, and file scope**

Run:

```powershell
git status --short --branch
git log -8 --oneline --decorate
git diff github-8099/codex/new-server-87-medical-notice-analyzer-20260722...HEAD --name-only
git grep -l -E "sk-[A-Za-z0-9]{20,}" -- medical-notice-analyzer
git grep -n '@app.post("/analysis/runs/{run_id}/revise")' -- medical-notice-analyzer/app/main.py
```

Expected:

- Branch is `codex/new-server-87-medical-notice-analyzer-20260722`.
- No `.env`, credential file, runtime data, report, or temporary file is tracked.
- The secret-pattern command produces no file names.
- No `.88` or legacy-Dify deployment file changed.
- The last command still finds the user-initiated revision route in `app/main.py`.

- [ ] **Step 2: Push without merging**

Run:

```powershell
git push github-8099 codex/new-server-87-medical-notice-analyzer-20260722
```

Confirm the remote branch SHA equals local `HEAD`. Do not create or merge a pull request.

### Task 5: Revalidate and back up the live `.87` state

**Files:**
- Create remotely: a UTC timestamp-derived `pre-readonly-qa` directory under `/opt/medical-notice-analyzer/deploy_backups/`
- Create remotely: a UTC timestamp-derived `new87-readonly-qa` directory under `/data/dify-1.14.2/docker/backups/`

- [ ] **Step 1: Apply the drift stop condition**

Before any write, read the analyzer container and Dify IDs again. Continue only if:

```text
analyzer container = medical-notice-analyzer-development-87
Compose project = medical-notice-analyzer-development-87-r7
current image = medical-notice-analyzer:quality87-summary-batching-4da55b7-r1
Dify app id = 8a588d09-3f29-45d8-9eaf-4cf1f5045958
published workflow id = ab6a94df-12e7-4724-8f28-5c391f03bceb
```

If any value changed, stop before backup/deployment, compare the new live release against the implementation branch, and regenerate the exact Compose and rollback lists. Do not reuse stale IDs or layer ordering.

Use a three-way provenance check instead of expecting the intentionally modified `HEAD` to equal the old live source:

```text
1. Compare the live release with local baseline commit 1946995 across all controlled
   app/**/*.py, tests/**/*.py, and workflow YAML files after CRLF/LF normalization.
2. Confirm that normalized live-to-1946995 hashes still match for all 78 files.
3. Review git diff 1946995..HEAD and confirm every changed path and hunk is an
   intentional part of this approved read-only-QA change.
```

If the normalized live baseline changed, or `1946995..HEAD` contains an unrelated change, stop and reconcile before building. Do not compare the old live source directly with the final modified `HEAD` and mistake intended changes or CRLF/LF differences for drift.

Do not treat `/opt/medical-notice-analyzer-releases/20260723-013721-4da55b7-summary-batching-r1/.deployed_commit` as authoritative: it is known to be stale at plan time. Record the container image, image/Compose revision label, release directory, GitHub deployment SHA, and normalized content-hash comparison in the new backup manifest.

- [ ] **Step 2: Back up the analyzer**

Run inside the `.87` SSH session:

```bash
set -euo pipefail
MN_STAMP=$(date -u +%Y%m%d-%H%M%S)
MN_BACKUP="/opt/medical-notice-analyzer/deploy_backups/${MN_STAMP}-pre-readonly-qa"
install -d -m 0700 "$MN_BACKUP"
install -m 0600 \
  /opt/medical-notice-analyzer/.env.development-87 \
  "$MN_BACKUP/.env.development-87"
cp -a \
  /opt/medical-notice-analyzer/docker-compose.yml \
  /opt/medical-notice-analyzer/docker-compose.s4-runtime.yml \
  /opt/medical-notice-analyzer/docker-compose.development-87.yml \
  /opt/medical-notice-analyzer-releases/20260721-185251-4c5e646a93d7/docker-compose.release-4c5e646a93d7-r4.yml \
  /opt/medical-notice-analyzer-releases/20260722-e3f1311-dify-near-limit-hardcode-r1/docker-compose.release-e3f1311-r1.yml \
  /opt/medical-notice-analyzer-releases/20260722-7c580f7-quality87-240k-summary-r1/docker-compose.release-7c580f7-r1.yml \
  /opt/medical-notice-analyzer-releases/20260723-013721-4da55b7-summary-batching-r1/docker-compose.release-4da55b7-r1.yml \
  /opt/medical-notice-analyzer-releases/20260723-013721-4da55b7-summary-batching-r1/docker-compose.runtime-summary-disabled.yml \
  "$MN_BACKUP/"
cp -a \
  /opt/medical-notice-analyzer-releases/20260723-013721-4da55b7-summary-batching-r1 \
  "$MN_BACKUP/current-release"
docker inspect medical-notice-analyzer-development-87 \
  --format 'image={{.Config.Image}} id={{.Id}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{end}} labels={{json .Config.Labels}}' \
  > "$MN_BACKUP/container.safe.txt"
docker image inspect medical-notice-analyzer:quality87-summary-batching-4da55b7-r1 \
  --format 'id={{.Id}} tags={{json .RepoTags}} created={{.Created}} size={{.Size}}' \
  > "$MN_BACKUP/image.safe.txt"
{
  printf '%s\n' 'live_release=/opt/medical-notice-analyzer-releases/20260723-013721-4da55b7-summary-batching-r1'
  printf '%s\n' 'live_revision=4da55b750a52d21c04f0c55c4ee4ac71894691e2'
  printf '%s\n' 'github_deployment_sha=4da55b750a52d21c04f0c55c4ee4ac71894691e2'
  printf '%s\n' 'normalized_baseline_commit=1946995'
  printf '%s\n' 'normalized_controlled_files=78'
  printf '%s\n' 'normalized_live_to_baseline=all_match'
  printf '%s\n' 'release_deployed_commit_authoritative=false'
} > "$MN_BACKUP/provenance.txt"
chmod -R go-rwx "$MN_BACKUP"
sha256sum \
  "$MN_BACKUP"/*.yml \
  "$MN_BACKUP"/.env.development-87 \
  "$MN_BACKUP"/container.safe.txt \
  "$MN_BACKUP"/image.safe.txt \
  "$MN_BACKUP"/provenance.txt \
  > "$MN_BACKUP/SHA256SUMS"
```

Do not print the environment file.

- [ ] **Step 3: Export both Dify draft and published DSL**

Open only `http://192.168.34.87` in the authenticated Dify Studio browser session. Verify the app name is `医药公告采购分析报告_pack_id_人工风格` and app ID is `8a588d09-3f29-45d8-9eaf-4cf1f5045958`.

Export with `include_secret=false`:

```text
Draft:
/console/api/apps/8a588d09-3f29-45d8-9eaf-4cf1f5045958/export?include_secret=false

Published:
/console/api/apps/8a588d09-3f29-45d8-9eaf-4cf1f5045958/export?include_secret=false&workflow_id=ab6a94df-12e7-4724-8f28-5c391f03bceb
```

Save the YAML `data` fields as:

```text
draft.before.yml
published-ab6a94df-12e7-4724-8f28-5c391f03bceb.before.yml
```

Transfer them to a new directory under `/data/dify-1.14.2/docker/backups/`, set directory mode `0700`, file mode `0600`, verify both are nonempty, and create `SHA256SUMS`.

The draft is newer than the published workflow. Diff them before import. If the draft has unrelated unpublished changes, preserve the backup and base the new controlled DSL on the active published workflow rather than silently discarding or publishing unrelated draft changes.

### Task 6: Build and deploy the committed backend only to `.87`

**Files:**
- Create remotely: a UTC timestamp and committed short-SHA release directory under `/opt/medical-notice-analyzer-releases/`
- Create remotely: one release Compose overlay inside that release directory

- [ ] **Step 1: Create and checksum the exact Git archive on Windows**

Run from the target worktree:

```powershell
$mnCommit = (git rev-parse HEAD).Trim()
$mnShort = $mnCommit.Substring(0, 7)
$mnArchive = Join-Path $env:TEMP "medical-notice-analyzer-$mnShort-readonly-qa.tar.gz"
$mnMetadata = Join-Path $env:TEMP "medical-notice-analyzer-$mnShort-readonly-qa.env"
git archive --format=tar.gz -o $mnArchive HEAD:medical-notice-analyzer
$mnHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $mnArchive).Hash.ToLowerInvariant()
$mnMetadataText = (@(
  "MN_COMMIT=$mnCommit"
  "MN_ARCHIVE_SHA256=$mnHash"
  "MN_ARCHIVE_NAME=medical-notice-analyzer-$mnShort-readonly-qa.tar.gz"
) -join "`n") + "`n"
[System.IO.File]::WriteAllText(
  $mnMetadata,
  $mnMetadataText,
  [System.Text.Encoding]::ASCII
)
Write-Output "commit=$mnCommit archive_sha256=$mnHash"
scp -o BindAddress=10.8.56.9 $mnArchive root@192.168.34.87:/opt/medical-notice-analyzer-releases/
scp -o BindAddress=10.8.56.9 $mnMetadata root@192.168.34.87:/opt/medical-notice-analyzer-releases/.readonly-qa-release.env
```

Do not include uncommitted files.

- [ ] **Step 2: Create the immutable release and image on `.87`**

Run over SSH:

```bash
set -euo pipefail
chmod 0600 /opt/medical-notice-analyzer-releases/.readonly-qa-release.env
source /opt/medical-notice-analyzer-releases/.readonly-qa-release.env
[[ "$MN_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
  echo "invalid commit metadata" >&2
  exit 1
}
[[ "$MN_ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "invalid archive checksum metadata" >&2
  exit 1
}
[[ "$MN_ARCHIVE_NAME" =~ ^medical-notice-analyzer-[0-9a-f]{7}-readonly-qa\.tar\.gz$ ]] || {
  echo "invalid archive name metadata" >&2
  exit 1
}
MN_SHORT=${MN_COMMIT:0:7}
MN_STAMP=$(date -u +%Y%m%d-%H%M%S)
MN_RELEASE="/opt/medical-notice-analyzer-releases/${MN_STAMP}-${MN_SHORT}-readonly-qa-r1"
MN_ARCHIVE="/opt/medical-notice-analyzer-releases/$MN_ARCHIVE_NAME"
MN_IMAGE="medical-notice-analyzer:quality87-readonly-qa-${MN_SHORT}-r1"
install -d -m 0700 "$MN_RELEASE/source"
printf '%s  %s\n' "$MN_ARCHIVE_SHA256" "$MN_ARCHIVE" | sha256sum -c -
tar -xzf "$MN_ARCHIVE" -C "$MN_RELEASE/source"
cp \
  /opt/medical-notice-analyzer-releases/20260723-013721-4da55b7-summary-batching-r1/source/Dockerfile.offline-overlay-r2 \
  "$MN_RELEASE/source/Dockerfile.offline-overlay-r2"
docker build \
  --file "$MN_RELEASE/source/Dockerfile.offline-overlay-r2" \
  --build-arg BASE_IMAGE=medical-notice-analyzer:development-87-689393f \
  --label "org.opencontainers.image.revision=$MN_COMMIT" \
  --tag "$MN_IMAGE" \
  "$MN_RELEASE/source"
```

The executor must compare `sha256sum "$MN_ARCHIVE"` with the recorded Windows hash before extraction. A mismatch stops deployment.

- [ ] **Step 3: Create the release overlay**

Create the concrete overlay from the validated shell values:

```bash
MN_NEW_OVERLAY="$MN_RELEASE/docker-compose.release-${MN_SHORT}-r1.yml"
{
  printf '%s\n' 'services:'
  printf '%s\n' '  medical-notice-analyzer:'
  printf '    image: "%s"\n' "$MN_IMAGE"
  printf '%s\n' '    environment:'
  printf '      APP_GIT_SHA: "%s"\n' "$MN_COMMIT"
  printf '%s\n' '    labels:'
  printf '      org.opencontainers.image.revision: "%s"\n' "$MN_COMMIT"
  printf '%s\n' '      com.xllearn.medical-notice-analyzer.release: "readonly-qa-r1"'
} > "$MN_NEW_OVERLAY"
chmod 0600 "$MN_NEW_OVERLAY"
```

Do not place credentials or `ENABLE_EVIDENCE_SUMMARY_LLM` in this overlay.

- [ ] **Step 4: Validate and recreate only the analyzer service**

Use the existing eight layers in this order, keeping the summary-disabled layer:

```bash
MN_PROJECT=medical-notice-analyzer-development-87-r7
MN_ENV_FILE=/opt/medical-notice-analyzer/.env.development-87
MN_COMMON_ARGS=(
  -p "$MN_PROJECT" --env-file "$MN_ENV_FILE"
  -f /opt/medical-notice-analyzer/docker-compose.yml
  -f /opt/medical-notice-analyzer/docker-compose.s4-runtime.yml
  -f /opt/medical-notice-analyzer/docker-compose.development-87.yml
  -f /opt/medical-notice-analyzer-releases/20260721-185251-4c5e646a93d7/docker-compose.release-4c5e646a93d7-r4.yml
  -f /opt/medical-notice-analyzer-releases/20260722-e3f1311-dify-near-limit-hardcode-r1/docker-compose.release-e3f1311-r1.yml
  -f /opt/medical-notice-analyzer-releases/20260722-7c580f7-quality87-240k-summary-r1/docker-compose.release-7c580f7-r1.yml
  -f /opt/medical-notice-analyzer-releases/20260723-013721-4da55b7-summary-batching-r1/docker-compose.release-4da55b7-r1.yml
  -f /opt/medical-notice-analyzer-releases/20260723-013721-4da55b7-summary-batching-r1/docker-compose.runtime-summary-disabled.yml
)
docker compose "${MN_COMMON_ARGS[@]}" -f "$MN_NEW_OVERLAY" config --quiet
docker compose "${MN_COMMON_ARGS[@]}" -f "$MN_NEW_OVERLAY" \
  up -d --no-deps --force-recreate medical-notice-analyzer
```

Do not run `down`, `prune`, remove an old image, or touch another service.

- [ ] **Step 5: Verify health and fixed runtime boundaries**

Wait until Docker reports `healthy`, then verify:

```bash
docker inspect medical-notice-analyzer-development-87 \
  --format 'image={{.Config.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{end}} restart_count={{.RestartCount}}'
curl --fail --silent --show-error http://127.0.0.1:8099/health
curl --fail --silent --show-error http://127.0.0.1:8099/openapi.json \
  | grep -Fq '"/analysis/runs/{run_id}/revise"'
docker exec medical-notice-analyzer-development-87 \
  sh -lc '
    test "$ENABLE_EVIDENCE_SUMMARY_LLM" = "false" &&
    test "$ENABLE_STRICT_DELIVERY_GATE" = "true" &&
    test "$ENABLE_UNSUPPORTED_FACT_REPAIR" = "false"
  '
```

Read diagnostics and confirm:

```text
DIFY_PLATFORM_STRING_MAX_CHARS=400000
DIFY_FULL_INPUT_MAX_CHARS=160000
DIFY_SAFE_COMPACT_MAX_CHARS=220000
DIFY_EVIDENCE_PACK_HARD_MAX_CHARS=240000
DIFY_EVIDENCE_PACK_HARD_MAX_BYTES=870400
```

Check only the presence of model/Dify keys; never print their values.

- [ ] **Step 6: Run targeted tests inside the deployed `.87` image**

Run:

```bash
docker exec medical-notice-analyzer-development-87 \
  python -m unittest \
  tests.test_dify_human_style_workflow.DifyHumanStyleWorkflowTests.test_workflow_has_generation_and_readonly_qa_only \
  tests.test_dify_human_style_workflow.DifyHumanStyleWorkflowTests.test_readonly_qa_failure_keeps_initial_report_and_marks_review \
  tests.test_dify_human_style_workflow.DifyHumanStyleWorkflowTests.test_parse_quality_blocks_severe_issue_even_if_model_marks_passed \
  tests.test_records_api.RecordsApiTests.test_valid_initial_generation_records_source_and_lengths \
  tests.test_records_api.RecordsApiTests.test_long_initial_generation_without_following_section_uses_fallback \
  tests.test_records_api.RecordsApiTests.test_contract_instruction_fragment_records_backend_fallback_source \
  tests.test_records_api.RecordsApiTests.test_local_quality_pipeline_refreshes_final_report_chars \
  -v
```

Expected: seven tests pass. If any test fails, restore the prior analyzer Compose chain before publishing Dify.

### Task 7: Import and publish the read-only QA workflow in the existing `.87` Dify app

**Files:**
- Publish: `medical-notice-analyzer/dify_workflow_pack_id_human_style.yml`

- [ ] **Step 1: Verify the candidate against the active published DSL**

Compare the source-controlled candidate with the backed-up active published DSL. Ignore only volatile app/workflow IDs and visual positions. Confirm the only functional graph changes are:

- removal of automatic revision, revision parsing, second QA, revised final result, and revised end;
- direct `parse_quality_json -> final_initial_result`;
- read-only QA prompt/contract;
- final status based on first-QA `passed`;
- retained `.87` evidence-pack callback and model settings.

Any unrelated functional difference stops publication.

- [ ] **Step 2: Overwrite only the existing app draft**

In the authenticated browser session at `http://192.168.34.87`, use the Dify Studio overwrite/import action to upload the exact committed file:

```text
medical-notice-analyzer/dify_workflow_pack_id_human_style.yml
```

The target console endpoint is:

```text
POST /console/api/apps/imports
```

Confirm in the browser network record that the request uses `mode=yaml-content` and `app_id=8a588d09-3f29-45d8-9eaf-4cf1f5045958`. Use the browser session so credentials remain in the browser. Do not print or save session tokens. If Dify returns `pending`, confirm only that same import through:

```text
POST /console/api/apps/imports/{import_id}/confirm
```

Verify the returned `app_id` is still `8a588d09-3f29-45d8-9eaf-4cf1f5045958`. A different app ID stops the operation because it would require a new workflow API key.

- [ ] **Step 3: Inspect and debug the draft**

In Dify Studio, verify the reachable path is:

```text
Start
-> Fetch Evidence Pack
-> Parse Evidence Pack
-> Evidence Pack OK?
-> Generate Report JSON
-> Parse Generation JSON
-> Quality Check Round 1
-> Parse Quality JSON
-> Final Result
-> End Initial
```

Verify the error path remains. Confirm there are exactly two LLM nodes and no revision or second-QA node.

Run one Dify draft debug call with a valid `.87` pack ID. Inspect the trace:

- `Generate Report JSON` executes once.
- `Quality Check Round 1` executes once.
- The final report equals `Parse Generation JSON.report_markdown`.
- Failed QA produces `needs_manual_review`.
- No removed node executes.

- [ ] **Step 4: Publish without restarting Dify**

Publish the same app with:

```json
{
  "marked_name": "生成只读质检",
  "marked_comment": "移除自动修订和二次质检，首轮质检只标记状态"
}
```

Target:

```text
POST /console/api/apps/8a588d09-3f29-45d8-9eaf-4cf1f5045958/workflows/publish
```

Record the new published workflow ID and export its no-secret DSL into the Dify backup directory. Do not restart Dify or the analyzer; the existing app ID and API key remain valid.

### Task 8: Run the two Zhejiang browser regressions and decide acceptance

**Files:** no source changes.

- [ ] **Step 1: Prepare the same Zhejiang material twice**

For each attempt use the browser at:

```text
http://192.168.34.87:8099/records-ui
```

Search `浙江省`, select only:

```text
menu_code=project_notice
articleid=c1d2fc19-1269-42e7-b8db-dddbe2b1e711
```

Start a fresh prepare/run cycle each time. Do not reuse a previous run ID.

- [ ] **Step 2: Wait for both terminal states**

Capture both run IDs and wait until each is one of:

```text
finished
needs_manual_review
failed
```

For each run, read `/analysis/runs/{run_id}`, `/analysis/runs/{run_id}/report`, and `/analysis/runs/{run_id}/diagnostics`. Record only IDs, counts, sizes, durations, hashes, status, and bounded issue summaries. Explicitly record `pack_id`, `compact_pack_chars`, `candidate_source`, `generation_validation_code`, `generation_report_chars`, `final_report_chars`, QA passed/issue count, final status, and Dify total duration.

On `.87`, fetch each exact compact pack and compute a content-only SHA-256 without printing the pack. Set the two shell variables to the exact `pack_id` values captured above:

```bash
read -r -p 'first pack_id: ' MN_PACK_ONE
read -r -p 'second pack_id: ' MN_PACK_TWO
[[ "$MN_PACK_ONE" =~ ^[0-9a-f-]{36}$ && "$MN_PACK_TWO" =~ ^[0-9a-f-]{36}$ ]] || {
  echo "invalid pack_id" >&2
  exit 1
}
docker exec -i medical-notice-analyzer-development-87 \
  python - "$MN_PACK_ONE" "$MN_PACK_TWO" <<'PY'
import copy
import hashlib
import json
import sys
from urllib.request import urlopen

for pack_id in sys.argv[1:]:
    with urlopen(
        f"http://127.0.0.1:8099/analysis/packs/{pack_id}",
        timeout=30,
    ) as response:
        pack = json.load(response)
    declared_chars = int(
        pack.get("compact_pack_chars")
        or pack.get("final_dify_input_chars")
        or 0
    )
    stable = copy.deepcopy(pack)
    stable.pop("pack_id", None)
    stable.pop("created_at", None)
    canonical = json.dumps(
        stable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    print(
        f"pack_id={pack_id} compact_pack_chars={declared_chars} "
        f"content_sha256={hashlib.sha256(canonical).hexdigest()}"
    )
PY
```

This prints only identifiers, lengths, and hashes. The two `compact_pack_chars` values and the two normalized content hashes must match. If either differs, stop the causal comparison and investigate evidence-pack drift before attributing report differences to Dify.

- [ ] **Step 3: Verify source and workflow behavior**

Both runs must satisfy:

```text
candidate_source = initial_generation
generation_validation_code = OK
fallback_used = false
fallback_provider = ""
generation_report_chars = final_report_chars
no OUTPUT_TRUNCATED
no Q_DIFY_FRAGMENTARY_REPORT
the two compact_pack_chars values are equal
the two normalized compact-pack content_sha256 values are equal
```

Use the Dify trace for each workflow run to verify only `Generate Report JSON` and `Quality Check Round 1` executed as LLM nodes. If QA failed, the final report must still equal the initial-generation report byte-for-byte.

- [ ] **Step 4: Verify report quality and self-containment**

For both formal reports verify:

- no raw or malformed JSON;
- no prompt/escaping instructions;
- no appendix;
- no page, table, row, column, or cell trace;
- no instruction to consult the original attachment;
- no technical implementation note;
- report has an introduction and substantive analysis sections;
- strict local quality gate still runs;
- unsupported-fact automatic repair remains disabled.

Report the visible-text length for each run. Length difference alone is not a failure if both are complete initial-generation reports, but a materially short or summary-only report must remain `needs_manual_review`.

- [ ] **Step 5: Report technical and business acceptance separately**

Technical acceptance passes only if:

1. the published graph has exactly two LLM calls;
2. no internal revision or second QA is reachable;
3. QA never changes the initial report;
4. both Zhejiang runs avoid revision-fragment fallback;
5. both runs use equal compact-pack character counts and equal normalized content hashes;
6. `.87` remains healthy with the 240000-character/870400-byte policy;
7. `ENABLE_EVIDENCE_SUMMARY_LLM=false`, `ENABLE_STRICT_DELIVERY_GATE=true`, and `ENABLE_UNSUPPORTED_FACT_REPAIR=false`;
8. the user-initiated `/analysis/runs/{run_id}/revise` route remains registered.

Business acceptance passes only if both reports are self-contained, evidence-backed, structurally complete, and free of malformed JSON or attachment-reading instructions. A `needs_manual_review` state or substantive quality defect must be reported honestly rather than called fully accepted.

### Task 9: Roll back on any blocking failure

**Files:** no source changes.

- [ ] **Step 1: Roll back Dify when graph publication or live generation fails**

Restore published workflow `ab6a94df-12e7-4724-8f28-5c391f03bceb` to draft through:

```text
POST /console/api/apps/8a588d09-3f29-45d8-9eaf-4cf1f5045958/workflows/ab6a94df-12e7-4724-8f28-5c391f03bceb/restore
```

Then publish that restored draft. Export the rollback result and record its new published workflow ID.

- [ ] **Step 2: Roll back the analyzer when health or server tests fail**

Run the original eight-layer Compose chain without the new overlay:

```bash
MN_PROJECT=medical-notice-analyzer-development-87-r7
MN_ENV_FILE=/opt/medical-notice-analyzer/.env.development-87
MN_COMMON_ARGS=(
  -p "$MN_PROJECT" --env-file "$MN_ENV_FILE"
  -f /opt/medical-notice-analyzer/docker-compose.yml
  -f /opt/medical-notice-analyzer/docker-compose.s4-runtime.yml
  -f /opt/medical-notice-analyzer/docker-compose.development-87.yml
  -f /opt/medical-notice-analyzer-releases/20260721-185251-4c5e646a93d7/docker-compose.release-4c5e646a93d7-r4.yml
  -f /opt/medical-notice-analyzer-releases/20260722-e3f1311-dify-near-limit-hardcode-r1/docker-compose.release-e3f1311-r1.yml
  -f /opt/medical-notice-analyzer-releases/20260722-7c580f7-quality87-240k-summary-r1/docker-compose.release-7c580f7-r1.yml
  -f /opt/medical-notice-analyzer-releases/20260723-013721-4da55b7-summary-batching-r1/docker-compose.release-4da55b7-r1.yml
  -f /opt/medical-notice-analyzer-releases/20260723-013721-4da55b7-summary-batching-r1/docker-compose.runtime-summary-disabled.yml
)
docker compose "${MN_COMMON_ARGS[@]}" config --quiet
docker compose "${MN_COMMON_ARGS[@]}" \
  up -d --no-deps --force-recreate medical-notice-analyzer
```

Confirm the image returns to:

```text
medical-notice-analyzer:quality87-summary-batching-4da55b7-r1
```

Confirm Docker health and `/health` return to normal and `ENABLE_EVIDENCE_SUMMARY_LLM=false` remains effective. Do not delete the failed release, backup, old image, run history, or reports.
