# Report Long-Term Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent, manually governed report-memory system that can guide Dify report generation without overriding current notice evidence or breaking existing clients.

**Architecture:** Store formal and candidate memory as UTF-8 Markdown under the existing `/app/data` persistent volume. Isolate file safety, validation, backup, hashing, and token checks in `app/report_memory.py`; expose narrow FastAPI APIs and a standalone management page; pass an immutable per-run memory snapshot to Dify as a separate optional variable. Keep candidate memory outside every generation path and preserve existing `/analysis/run` behavior when the new request field is absent.

**Tech Stack:** Python 3, FastAPI, Pydantic, HTML/CSS/vanilla JavaScript, Dify Workflow YAML, Docker Compose, Python `unittest`.

---

## File Structure

- Create: `app/report_memory.py`
  - Own default templates, safe fixed paths, validation, backup rotation, atomic writes, hashes, and write-token verification.
- Create: `app/static/memory.html`
  - Standalone two-tab memory editor.
- Create: `tests/test_report_memory.py`
  - Unit tests for initialization, validation, backup, atomic writes, and backup rotation.
- Modify: `app/main.py`
  - Add memory API models/routes, `/memory-ui`, generation switch, run snapshot metadata, Dify payload, logging, and revision behavior.
- Modify: `app/static/records.html`
  - Add the memory-management link and generation toggle; explicitly send `use_report_memory`.
- Modify: `app/static/analysis_run.html`
  - Show requested/applied/read-failed memory state without displaying memory content.
- Modify: `app/diagnostics.py`
  - Add a bounded `report_memory` diagnostics section.
- Modify: `tests/test_records_api.py`
  - Cover APIs, write protection, UI routes, generation compatibility, memory metadata, Dify payload, read failure, and diagnostics.
- Modify: `tests/test_dify_human_style_workflow.py`
  - Assert the Dify start variable and all four LLM nodes use the memory constraints.
- Modify: `dify_workflow_pack_id_human_style.yml`
  - Add optional `report_memory` input and prompt constraints to generation, QA, revision, and second QA.
- Modify: `docker-compose.yml`
  - Add memory configuration without adding a second volume.
- Modify: `.env.example`
  - Document non-secret limits and the secret write-token variable.
- Modify: `.gitignore`
  - Ignore `data/memory/` and generated memory backups.
- Modify: `scripts/run_16case_report_regression.py`
  - Accept and record a memory-on/off option without changing default behavior.
- Modify: `tests/test_16case_regression_script.py`
  - Verify the new regression option and summary fields.
- Create: `docs/report-memory-operations.md`
  - Document administration, Dify publication, backup, rollback, and verification.

## Locked Decisions

- `memory_candidates.md` is never read by report generation code.
- `/analysis/run` defaults `use_report_memory` to `false` for backward compatibility.
- The records UI defaults its switch to enabled and explicitly sends `true`.
- Formal memory is limited to 20,000 characters and is never silently truncated.
- Empty or whitespace-only saves are rejected.
- Saving requires a valid `X-Memory-Write-Token` only when `MEMORY_WRITE_TOKEN` is configured; production deployment must configure it.
- Complete memory text is not stored in run JSON, diagnostics, logs, or report Markdown.
- A generation request reads memory once and passes that immutable string to the background thread.
- A later user-feedback revision is a new operation: if the original run requested memory, it re-reads the current approved formal memory and records the revision memory hash in the revision entry.
- Candidate auto-generation and automatic merge are excluded from this implementation.

---

### Task 1: Build the Memory File Service with TDD

**Files:**
- Create: `tests/test_report_memory.py`
- Create: `app/report_memory.py`

- [ ] **Step 1: Write failing initialization and metadata tests**

Create `tests/test_report_memory.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.report_memory import (
    MemoryContentError,
    MemoryKind,
    read_memory,
    save_memory,
)


class ReportMemoryServiceTests(unittest.TestCase):
    def test_read_creates_default_report_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ", {"MEMORY_DIR": tmpdir}, clear=False
        ):
            result = read_memory(MemoryKind.REPORT)

            path = Path(tmpdir) / "report_memory.md"
            self.assertTrue(path.exists())
            self.assertEqual(path.read_text(encoding="utf-8"), result.content)
            self.assertIn("# Report Memory", result.content)
            self.assertIn("不得覆盖当前公告事实", result.content)
            self.assertEqual(result.chars, len(result.content))
            self.assertEqual(len(result.sha256), 64)

    def test_read_creates_default_candidate_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ", {"MEMORY_DIR": tmpdir}, clear=False
        ):
            result = read_memory(MemoryKind.CANDIDATES)

            self.assertTrue((Path(tmpdir) / "memory_candidates.md").exists())
            self.assertIn("# Memory Candidates", result.content)
            self.assertIn("候选经验默认不直接影响正式报告生成", result.content)
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_report_memory -v
```

Expected: import failure because `app.report_memory` does not exist.

- [ ] **Step 3: Add the service types, templates, and read path**

Create `app/report_memory.py` with these public interfaces:

```python
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path


class MemoryKind(str, Enum):
    REPORT = "report"
    CANDIDATES = "candidates"


class MemoryContentError(ValueError):
    def __init__(self, code: str, message: str, *, chars: int = 0, max_chars: int = 0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.chars = chars
        self.max_chars = max_chars


@dataclass(frozen=True)
class MemoryDocument:
    kind: MemoryKind
    content: str
    chars: int
    max_chars: int
    updated_at: str
    sha256: str
    write_protection_enabled: bool


@dataclass(frozen=True)
class MemorySaveResult:
    document: MemoryDocument
    backup_name: str


REPORT_MEMORY_TEMPLATE = """# Report Memory

本文件为医药公告分析报告生成的正式长期规则库。
所有规则应经过人工确认后写入。
本文件只能指导报告写法、分析角度和质量检查，不得覆盖当前公告事实。

## 1. 事实依据优先级

生成报告时必须遵守以下优先级：

当前主材料正文 > 当前主材料附件 > 当前辅助材料正文/附件 > 用户本次要求 > report_memory.md > 历史分析材料。

report_memory.md 只能指导写作方式、分析角度和质检标准，不得覆盖当前公告中的事实、时间、价格、品种、企业和规则。

## 2. 报告写作风格

- 报告应采用专业、克制、接近人工分析稿的语气。
- 不要简单复述公告原文，应在严格依据原文的基础上提炼企业关注点。
- 不使用“显然”“必然”“唯一”“一定导致”等过度判断词。
- 对未明确的信息，应写“公告未明确”“需以官方后续通知为准”，不得自行补充。

## 3. 不同公告类型分析重点

### 挂网申报类公告

- 申报范围和条件。
- 企业与产品资质要求。
- 申报时间、审核流程和材料递交要求。
- 价格填报要求和企业操作风险。

### 集采 / 带量采购类公告

- 采购品种、采购主体、报量范围和采购周期。
- 申报价、最高有效申报价、参考价和拟中选规则。
- 价格联动、协议量分配和非中选产品处理。
- 企业申报、报价和履约风险。

## 4. 附件处理规则

- 附件解析成功时，应优先提取品种、产品、企业、价格、规格型号、申报时间、采购量和注册证等结构化信息。
- Excel 附件不应把全部行塞入报告，应按品类、价格、企业和规则进行摘要。
- PDF / Word 附件应提取标题、章节、关键条款和表格内容。
- 附件解析失败时，相关内容必须进入人工复核提示。
- 如果附件仅获得文件名、大小、类型和 URL 等元数据，不得推断附件内的清单、企业、产品或价格。

## 5. 表格生成规则

- 不建议使用一个大表承载全部公告信息。
- 表格应按主题拆分，例如品种范围、价格规则、时间节点、企业操作事项、中选规则和风险提示。
- 原文未明确的数据不得在表格中补齐。
- 表格中的数字、时间、价格和比例必须与原文一致。
- 表格不能替代正文分析，表格后应有简短解释。

## 6. 常见错误提醒

- 不得把“最高有效申报价”写成“中选价”。
- 不得把“需求量”直接写成“采购量”，除非公告明确说明。
- 不得把“注册证层级”的报价规则误写成“企业层级”。
- 不得把“拟中选”写成“正式中选”，除非公告明确为中选结果。
- 不得把辅助材料内容当成主材料的新政策要求。
- 附件仅解析到元数据时，不得推断附件内的具体产品、企业或价格。

## 7. 报告质量门禁

- 检查报告是否为空或明显过短。
- 检查是否大段复制公告原文。
- 检查主材料标题、发布时间、地区、项目类型和项目阶段。
- 检查关键时间节点和附件解析状态。
- 检查是否存在原文未明确的推断。
- 检查是否把辅助材料内容误当成主材料要求。
- 检查价格、数量、时间和比例是否与原文一致。
- 检查是否出现与当前公告无关的历史内容。

## 8. 固定声明

本报告基于公开公告及附件内容整理，仅供企业内部政策研判参考，具体申报、报价、执行要求以官方平台最终发布内容为准。
"""


CANDIDATE_MEMORY_TEMPLATE = """# Memory Candidates

本文件记录由模型、质检节点或用户反馈产生的候选经验。
候选经验默认不直接影响正式报告生成。
经人工审核确认后，方可合并进 report_memory.md。

## Candidate-YYYYMMDD-001

### 来源
用户反馈 / 质检节点 / 生成节点 / 附件解析模块

### 触发场景
说明经验来源和产生原因。

### 候选规则
写出建议沉淀的通用规则。

### 建议归类
报告写作风格 / 附件处理规则 / 表格规则 / 常见错误提醒 / 质量门禁 / 公告类型分析重点

### 适用范围
全部公告 / 集采类 / 挂网申报类 / 价格联动类 / 含附件公告

### 风险
说明是否可能误伤其他类型公告。

### 审核状态
待审核 / 已采纳 / 已拒绝

### 审核意见
人工审核时填写。
"""


def memory_dir() -> Path:
    configured = (os.getenv("MEMORY_DIR") or "").strip()
    return Path(configured) if configured else Path.cwd() / "data" / "memory"


def _memory_path(kind: MemoryKind) -> Path:
    filename = "report_memory.md" if kind is MemoryKind.REPORT else "memory_candidates.md"
    return memory_dir() / filename


def _max_chars(kind: MemoryKind) -> int:
    name = "REPORT_MEMORY_MAX_CHARS" if kind is MemoryKind.REPORT else "MEMORY_CANDIDATES_MAX_CHARS"
    default = 20_000 if kind is MemoryKind.REPORT else 100_000
    try:
        return max(1, int(os.getenv(name) or default))
    except ValueError:
        return default


def _template(kind: MemoryKind) -> str:
    return REPORT_MEMORY_TEMPLATE if kind is MemoryKind.REPORT else CANDIDATE_MEMORY_TEMPLATE


def _document(kind: MemoryKind, path: Path, content: str) -> MemoryDocument:
    stat = path.stat()
    return MemoryDocument(
        kind=kind,
        content=content,
        chars=len(content),
        max_chars=_max_chars(kind),
        updated_at=datetime.fromtimestamp(stat.st_mtime).isoformat(sep=" ", timespec="seconds"),
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        write_protection_enabled=bool((os.getenv("MEMORY_WRITE_TOKEN") or "").strip()),
    )


def read_memory(kind: MemoryKind) -> MemoryDocument:
    path = _memory_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_template(kind), encoding="utf-8", newline="\n")
    return _document(kind, path, path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run initialization tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_report_memory -v
```

Expected: two tests pass.

- [ ] **Step 5: Add failing save, backup, limit, and rotation tests**

Append tests that:

```python
    def test_save_backs_up_old_content_and_replaces_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ", {"MEMORY_DIR": tmpdir}, clear=False
        ):
            original = read_memory(MemoryKind.REPORT)
            saved = save_memory(MemoryKind.REPORT, "# Report Memory\n\n新规则。")

            self.assertNotEqual(original.sha256, saved.document.sha256)
            self.assertEqual(saved.document.content, "# Report Memory\n\n新规则。")
            backup = Path(tmpdir) / "backups" / saved.backup_name
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_text(encoding="utf-8"), original.content)

    def test_save_rejects_blank_and_oversized_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {"MEMORY_DIR": tmpdir, "REPORT_MEMORY_MAX_CHARS": "20"},
            clear=False,
        ):
            read_memory(MemoryKind.REPORT)
            with self.assertRaises(MemoryContentError) as blank:
                save_memory(MemoryKind.REPORT, "   \n")
            self.assertEqual(blank.exception.code, "MEMORY_EMPTY")

            with self.assertRaises(MemoryContentError) as oversized:
                save_memory(MemoryKind.REPORT, "x" * 21)
            self.assertEqual(oversized.exception.code, "MEMORY_TOO_LARGE")
            self.assertEqual(oversized.exception.max_chars, 20)

    def test_backup_rotation_keeps_configured_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {"MEMORY_DIR": tmpdir, "MEMORY_BACKUP_KEEP_COUNT": "2"},
            clear=False,
        ):
            read_memory(MemoryKind.REPORT)
            save_memory(MemoryKind.REPORT, "# Report Memory\n\n版本 1")
            save_memory(MemoryKind.REPORT, "# Report Memory\n\n版本 2")
            save_memory(MemoryKind.REPORT, "# Report Memory\n\n版本 3")

            backups = list((Path(tmpdir) / "backups").glob("report_memory_*.bak.md"))
            self.assertEqual(len(backups), 2)
```

- [ ] **Step 6: Implement validation, unique backup names, rotation, and atomic save**

Add:

```python
def _backup_keep_count() -> int:
    try:
        return max(1, int(os.getenv("MEMORY_BACKUP_KEEP_COUNT") or 50))
    except ValueError:
        return 50


def _validate_content(kind: MemoryKind, content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        raise MemoryContentError("MEMORY_EMPTY", "memory content must not be empty")
    max_chars = _max_chars(kind)
    if len(normalized) > max_chars:
        raise MemoryContentError(
            "MEMORY_TOO_LARGE",
            "memory content exceeds configured limit",
            chars=len(normalized),
            max_chars=max_chars,
        )
    return normalized


def _next_backup_path(kind: MemoryKind) -> Path:
    backup_dir = memory_dir() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    prefix = "report_memory" if kind is MemoryKind.REPORT else "memory_candidates"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = backup_dir / f"{prefix}_{stamp}.bak.md"
    sequence = 1
    while candidate.exists():
        candidate = backup_dir / f"{prefix}_{stamp}_{sequence:03d}.bak.md"
        sequence += 1
    return candidate


def _rotate_backups(kind: MemoryKind) -> None:
    prefix = "report_memory" if kind is MemoryKind.REPORT else "memory_candidates"
    backups = sorted(
        (memory_dir() / "backups").glob(f"{prefix}_*.bak.md"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old in backups[_backup_keep_count():]:
        old.unlink(missing_ok=True)


def save_memory(kind: MemoryKind, content: str) -> MemorySaveResult:
    normalized = _validate_content(kind, content)
    path = _memory_path(kind)
    current = read_memory(kind)
    backup = _next_backup_path(kind)
    shutil.copy2(path, backup)

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(normalized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    _rotate_backups(kind)
    return MemorySaveResult(document=_document(kind, path, normalized), backup_name=backup.name)
```

Remove the unused `current` binding after confirming backup behavior remains covered.

- [ ] **Step 7: Run memory service tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_report_memory -v
```

Expected: all memory service tests pass.

- [ ] **Step 8: Commit**

```powershell
git add app/report_memory.py tests/test_report_memory.py
git commit -m "Add persistent report memory service"
```

---

### Task 2: Add Memory APIs and Write Protection

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_records_api.py`

- [ ] **Step 1: Write failing API tests**

Add tests covering:

```python
    def test_memory_api_initializes_reads_and_saves_with_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            main_module.os.environ,
            {"MEMORY_DIR": tmpdir, "MEMORY_WRITE_TOKEN": "test-secret"},
            clear=False,
        ):
            get_response = self.client.get("/memory/report")
            self.assertEqual(get_response.status_code, 200)
            self.assertIn("# Report Memory", get_response.json()["content"])
            self.assertTrue(get_response.json()["write_protection_enabled"])

            denied = self.client.put("/memory/report", json={"content": "# Report Memory\n\n规则"})
            self.assertEqual(denied.status_code, 403)

            saved = self.client.put(
                "/memory/report",
                headers={"X-Memory-Write-Token": "test-secret"},
                json={"content": "# Report Memory\n\n规则"},
            )
            self.assertEqual(saved.status_code, 200)
            self.assertTrue(saved.json()["backup_name"].startswith("report_memory_"))

    def test_candidate_memory_api_is_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            main_module.os.environ,
            {"MEMORY_DIR": tmpdir, "MEMORY_WRITE_TOKEN": ""},
            clear=False,
        ):
            response = self.client.get("/memory/candidates")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["kind"], "candidates")
            self.assertIn("候选经验默认不直接影响正式报告生成", response.json()["content"])

    def test_memory_api_rejects_blank_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            main_module.os.environ,
            {"MEMORY_DIR": tmpdir, "MEMORY_WRITE_TOKEN": ""},
            clear=False,
        ):
            response = self.client.put("/memory/report", json={"content": "  "})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["error"]["code"], "MEMORY_EMPTY")
```

- [ ] **Step 2: Run tests and confirm 404 failures**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_memory_api_initializes_reads_and_saves_with_token tests.test_records_api.RecordsApiTests.test_candidate_memory_api_is_separate tests.test_records_api.RecordsApiTests.test_memory_api_rejects_blank_content -v
```

Expected: failures because `/memory/report` and `/memory/candidates` do not exist.

- [ ] **Step 3: Add imports and API models**

In `app/main.py`, import:

```python
import secrets

from app.report_memory import (
    MemoryContentError,
    MemoryDocument,
    MemoryKind,
    read_memory,
    save_memory,
)
```

Add:

```python
class MemoryUpdateRequest(BaseModel):
    content: str


class MemoryResponse(BaseModel):
    success: bool = True
    kind: str
    content: str
    chars: int
    max_chars: int
    updated_at: str
    sha256: str
    write_protection_enabled: bool
    backup_name: str = ""
```

- [ ] **Step 4: Add response conversion, token verification, and error mapping**

Add:

```python
def _memory_response(document: MemoryDocument, backup_name: str = "") -> MemoryResponse:
    return MemoryResponse(
        kind=document.kind.value,
        content=document.content,
        chars=document.chars,
        max_chars=document.max_chars,
        updated_at=document.updated_at,
        sha256=document.sha256,
        write_protection_enabled=document.write_protection_enabled,
        backup_name=backup_name,
    )


def _require_memory_write_token(request: Request) -> None:
    expected = (os.getenv("MEMORY_WRITE_TOKEN") or "").strip()
    if not expected:
        return
    supplied = (request.headers.get("X-Memory-Write-Token") or "").strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="memory write token is invalid")


def _memory_content_error(exc: MemoryContentError) -> JSONResponse:
    return _analysis_error(
        422,
        exc.code,
        exc.message,
        json.dumps({"chars": exc.chars, "max_chars": exc.max_chars}, ensure_ascii=False),
    )
```

- [ ] **Step 5: Add GET and PUT routes**

Add:

```python
@app.get("/memory/report", response_model=MemoryResponse)
def get_report_memory() -> MemoryResponse:
    return _memory_response(read_memory(MemoryKind.REPORT))


@app.put("/memory/report", response_model=MemoryResponse)
def put_report_memory(req: MemoryUpdateRequest, request: Request):
    _require_memory_write_token(request)
    try:
        result = save_memory(MemoryKind.REPORT, req.content)
    except MemoryContentError as exc:
        return _memory_content_error(exc)
    return _memory_response(result.document, result.backup_name)


@app.get("/memory/candidates", response_model=MemoryResponse)
def get_candidate_memory() -> MemoryResponse:
    return _memory_response(read_memory(MemoryKind.CANDIDATES))


@app.put("/memory/candidates", response_model=MemoryResponse)
def put_candidate_memory(req: MemoryUpdateRequest, request: Request):
    _require_memory_write_token(request)
    try:
        result = save_memory(MemoryKind.CANDIDATES, req.content)
    except MemoryContentError as exc:
        return _memory_content_error(exc)
    return _memory_response(result.document, result.backup_name)
```

Do not implement `/memory/candidates/append` in this version.

- [ ] **Step 6: Run API tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v
```

Expected: all existing and new records API tests pass.

- [ ] **Step 7: Commit**

```powershell
git add app/main.py tests/test_records_api.py
git commit -m "Expose governed report memory APIs"
```

---

### Task 3: Build the Standalone Memory Management Page

**Files:**
- Create: `app/static/memory.html`
- Modify: `app/main.py`
- Modify: `app/static/records.html`
- Modify: `tests/test_records_api.py`

- [ ] **Step 1: Add failing route and static-contract tests**

Add:

```python
    def test_memory_ui_serves_standalone_editor(self) -> None:
        response = self.client.get("/memory-ui")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("正式长期记忆", response.text)
        self.assertIn("候选记忆", response.text)
        self.assertIn("/memory/report", response.text)
        self.assertIn("/memory/candidates", response.text)
        self.assertIn("X-Memory-Write-Token", response.text)
        self.assertNotIn("/analysis/run", response.text)

    def test_records_ui_links_memory_manager_without_embedding_editor(self) -> None:
        response = self.client.get("/records-ui")

        self.assertIn('href="/memory-ui"', response.text)
        self.assertNotIn('id="reportMemoryEditor"', response.text)
```

- [ ] **Step 2: Run tests and confirm route failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_memory_ui_serves_standalone_editor tests.test_records_api.RecordsApiTests.test_records_ui_links_memory_manager_without_embedding_editor -v
```

Expected: `/memory-ui` returns 404 and the records page lacks the link.

- [ ] **Step 3: Create `app/static/memory.html`**

Build one complete page with:

- Header with “报告长期记忆” and a back link to `/records-ui`.
- Two semantic tab buttons.
- One stable editor panel reused for the selected kind.
- `textarea` with fixed responsive height.
- Character counter, max-character indicator, updated time, hash prefix, and protection state.
- Session-only token input (`sessionStorage.setItem("memoryWriteToken", value)`).
- Save button with `window.confirm`.
- Fetch helpers that call GET and PUT endpoints.
- Warning when write protection is disabled.
- No display or logging of the token after requests.

The save request must be:

```javascript
const headers = { 'Content-Type': 'application/json' };
const token = sessionStorage.getItem('memoryWriteToken') || '';
if (token) headers['X-Memory-Write-Token'] = token;
const response = await fetch(endpoint, {
  method: 'PUT',
  headers,
  body: JSON.stringify({ content: editor.value }),
});
```

The page must reject an empty editor before sending and must not trim non-empty Markdown before save.

- [ ] **Step 4: Add the UI route**

Add:

```python
@app.get("/memory-ui")
def memory_ui():
    path = Path(__file__).resolve().parent / "static" / "memory.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="memory UI not found")
    return FileResponse(path, media_type="text/html; charset=utf-8")
```

- [ ] **Step 5: Add only a navigation link to the records page**

Add a quiet toolbar link:

```html
<a class="secondary nav-link" href="/memory-ui">长期记忆管理</a>
```

Do not add editors, candidate controls, or memory contents to `records.html`.

- [ ] **Step 6: Run route tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_memory_ui_serves_standalone_editor tests.test_records_api.RecordsApiTests.test_records_ui_links_memory_manager_without_embedding_editor -v
```

Expected: both pass.

- [ ] **Step 7: Commit**

```powershell
git add app/static/memory.html app/static/records.html app/main.py tests/test_records_api.py
git commit -m "Add report memory management UI"
```

---

### Task 4: Add the Generation Toggle and Immutable Run Snapshot

**Files:**
- Modify: `app/main.py`
- Modify: `app/static/records.html`
- Modify: `tests/test_records_api.py`

- [ ] **Step 1: Add failing backward-compatibility and snapshot tests**

Add tests for these exact behaviors:

```python
    def test_analysis_run_defaults_memory_off_for_old_clients(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            main_module, "_analysis_run_dir", return_value=main_module.Path(tmpdir)
        ), patch.object(
            main_module, "_read_database_evidence_pack", return_value={"pack_id": "pack_old"}
        ), patch.object(main_module, "_execute_analysis_run_background"):
            response = self.client.post("/analysis/run", json={"pack_id": "pack_old"})

            self.assertEqual(response.status_code, 200)
            record = main_module._read_analysis_run(response.json()["run_id"])
            self.assertFalse(record["report_memory_requested"])
            self.assertFalse(record["report_memory_applied"])
            self.assertEqual(record["report_memory_chars"], 0)

    def test_analysis_run_reads_memory_once_and_passes_snapshot_to_background(self) -> None:
        captured = {}

        def fake_background(pack_id, run_id, report_memory_snapshot):
            captured["pack_id"] = pack_id
            captured["run_id"] = run_id
            captured["snapshot"] = report_memory_snapshot

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            main_module.os.environ, {"MEMORY_DIR": tmpdir}, clear=False
        ), patch.object(
            main_module, "_analysis_run_dir", return_value=main_module.Path(tmpdir) / "runs"
        ), patch.object(
            main_module, "_read_database_evidence_pack", return_value={"pack_id": "pack_memory"}
        ), patch.object(
            main_module.threading, "Thread"
        ) as thread_cls:
            main_module.save_memory(main_module.MemoryKind.REPORT, "# Report Memory\n\n已确认规则")
            response = self.client.post(
                "/analysis/run",
                json={"pack_id": "pack_memory", "use_report_memory": True},
            )

            args = thread_cls.call_args.kwargs["args"]
            captured["snapshot"] = args[2]
            self.assertEqual(captured["snapshot"], "# Report Memory\n\n已确认规则")
            record = main_module._read_analysis_run(response.json()["run_id"])
            self.assertTrue(record["report_memory_requested"])
            self.assertTrue(record["report_memory_applied"])
            self.assertEqual(record["report_memory_chars"], len(captured["snapshot"]))
            self.assertEqual(len(record["report_memory_sha256"]), 64)
```

Also add a read-failure test by patching `read_memory` to raise `OSError`; assert the request still returns `running`, metadata marks failure, and the background snapshot is empty.

- [ ] **Step 2: Run tests and confirm model/signature failures**

Run the three new tests. Expected: missing request field behavior and old two-argument background signature failures.

- [ ] **Step 3: Extend the request and response models**

Change:

```python
class AnalysisRunRequest(BaseModel):
    pack_id: str = Field(min_length=1)
    use_report_memory: bool = False
```

Add optional fields to `AnalysisRunResponse`:

```python
report_memory_requested: bool = False
report_memory_applied: bool = False
memory_read_failed: bool = False
```

- [ ] **Step 4: Resolve the snapshot before creating the background thread**

Add:

```python
def _resolve_report_memory_snapshot(requested: bool) -> tuple[str, dict[str, Any], list[str]]:
    metadata = {
        "report_memory_requested": bool(requested),
        "report_memory_applied": False,
        "report_memory_chars": 0,
        "report_memory_sha256": "",
        "memory_read_failed": False,
    }
    if not requested:
        return "", metadata, []
    try:
        document = read_memory(MemoryKind.REPORT)
    except Exception as exc:  # noqa: BLE001
        logger.warning("report_memory_read_failed error_type=%s", exc.__class__.__name__)
        metadata["memory_read_failed"] = True
        return "", metadata, ["长期记忆读取失败，本次报告已按不使用长期记忆继续生成。"]
    metadata.update(
        {
            "report_memory_applied": True,
            "report_memory_chars": document.chars,
            "report_memory_sha256": document.sha256,
        }
    )
    return document.content, metadata, []
```

In `/analysis/run`, merge metadata into the initial record, merge warning text, and start:

```python
threading.Thread(
    target=_execute_analysis_run_background,
    args=(pack_id, run_id, report_memory_snapshot),
    daemon=True,
).start()
```

- [ ] **Step 5: Change background and Dify call signatures**

Use:

```python
def _execute_analysis_run_background(
    pack_id: str,
    run_id: str,
    report_memory_snapshot: str = "",
) -> None:
```

and:

```python
def _call_dify_workflow(
    pack_id: str,
    run_id: str,
    pack: dict[str, Any] | None = None,
    report_memory: str = "",
) -> dict[str, Any]:
```

Call:

```python
result = _call_dify_workflow(
    pack_id,
    run_id,
    pack_for_policy,
    report_memory_snapshot,
)
```

- [ ] **Step 6: Update existing test doubles**

Every `fake_call(pack_id, run_id, pack=None)` in `tests/test_records_api.py` must accept:

```python
def fake_call(pack_id: str, run_id: str, pack: dict | None = None, report_memory: str = ""):
```

Do not weaken assertions unrelated to memory.

- [ ] **Step 7: Add the records-page toggle**

Add a switch near the “生成分析报告” action:

```html
<label class="switch-row memory-switch">
  <input id="useReportMemory" type="checkbox" checked>
  使用长期记忆
</label>
```

Change the request body:

```javascript
body: JSON.stringify({
  pack_id: packId,
  use_report_memory: $('useReportMemory')?.checked !== false,
}),
```

- [ ] **Step 8: Run focused and full API tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```powershell
git add app/main.py app/static/records.html tests/test_records_api.py
git commit -m "Add governed memory to report generation"
```

---

### Task 5: Pass Formal Memory to Dify and Preserve Revision Boundaries

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_records_api.py`

- [ ] **Step 1: Add failing Dify payload tests**

Patch the HTTP post helper and assert:

```python
self.assertEqual(payload["inputs"]["pack_id"], "pack_test")
self.assertEqual(payload["inputs"]["report_memory"], "# Report Memory\n\n规则")
```

Add a second assertion for disabled memory:

```python
self.assertEqual(payload["inputs"]["report_memory"], "")
```

Add a candidate-isolation test that writes a unique marker to `memory_candidates.md`, runs with formal memory enabled, and asserts the marker is absent from the payload.

- [ ] **Step 2: Run tests and confirm missing payload field**

Expected: `KeyError: 'report_memory'`.

- [ ] **Step 3: Add the independent Dify variable**

Change:

```python
payload = {
    "inputs": {
        "pack_id": pack_id,
        "report_memory": report_memory,
    },
    "response_mode": config["response_mode"],
    "user": config["user"],
}
```

Extend the call-start log with `report_memory_chars=%s`, passing only `len(report_memory)`.

- [ ] **Step 4: Apply current formal memory to later user-feedback revision only when requested**

Before `_call_dify_revision_workflow`, resolve a fresh snapshot only if:

```python
memory_requested = bool(record.get("report_memory_requested"))
```

Extend `_call_dify_revision_workflow` with:

```python
report_memory: str = "",
```

and include it in `payload["inputs"]`.

Add these fields to `revision_record`:

```python
"report_memory_applied": bool(report_memory),
"report_memory_chars": len(report_memory),
"report_memory_sha256": hashlib.sha256(report_memory.encode("utf-8")).hexdigest() if report_memory else "",
"memory_read_failed": revision_memory_failed,
```

If re-read fails, continue revision with an empty variable and warning; do not expose memory text.

- [ ] **Step 5: Run payload and revision tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add app/main.py tests/test_records_api.py
git commit -m "Pass formal memory to Dify workflows"
```

---

### Task 6: Add Bounded Diagnostics and Report-Page Status

**Files:**
- Modify: `app/diagnostics.py`
- Modify: `app/static/analysis_run.html`
- Modify: `tests/test_records_api.py`

- [ ] **Step 1: Add failing diagnostics tests**

Construct a run record with memory metadata and assert:

```python
memory = diagnostics["report_memory"]
self.assertTrue(memory["requested"])
self.assertTrue(memory["applied"])
self.assertEqual(memory["chars"], 1234)
self.assertEqual(memory["sha256_prefix"], "abcdef123456")
self.assertFalse(memory["read_failed"])
self.assertNotIn("content", memory)
```

Add static assertions for:

```text
长期记忆
report_memory_requested
report_memory_applied
memory_read_failed
```

- [ ] **Step 2: Run tests and confirm missing diagnostics section**

Run the new focused tests. Expected: missing `report_memory` key and missing HTML labels.

- [ ] **Step 3: Add diagnostics output**

In `build_run_diagnostics`, add:

```python
"report_memory": {
    "requested": bool(record.get("report_memory_requested")),
    "applied": bool(record.get("report_memory_applied")),
    "chars": int(record.get("report_memory_chars") or 0),
    "sha256_prefix": str(record.get("report_memory_sha256") or "")[:12],
    "read_failed": bool(record.get("memory_read_failed")),
},
```

Never include the full memory content.

- [ ] **Step 4: Add a compact side card to `analysis_run.html`**

Display:

- 请求使用
- 实际应用
- 字符数
- 读取失败
- 版本摘要

Use existing `yesNo`, `escapeHtml`, and card styles. If `read_failed=true`, show:

```text
长期记忆读取失败，本次报告已按未使用长期记忆继续生成。
```

Do not add memory content to report Markdown, diagnostics JSON rendering, or Word export.

- [ ] **Step 5: Run diagnostics and full API tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add app/diagnostics.py app/static/analysis_run.html tests/test_records_api.py
git commit -m "Expose safe report memory diagnostics"
```

---

### Task 7: Update the Dify Workflow DSL

**Files:**
- Modify: `dify_workflow_pack_id_human_style.yml`
- Modify: `tests/test_dify_human_style_workflow.py`

- [ ] **Step 1: Add failing structural and prompt tests**

Extend the workflow test:

```python
start_by_name = {item["variable"]: item for item in start_vars}
self.assertIn("report_memory", start_by_name)
self.assertFalse(start_by_name["report_memory"]["required"])
self.assertGreaterEqual(start_by_name["report_memory"]["max_length"], 20000)

for node_id in ["generate_report", "qa_report_first", "revise_report", "qa_revised_report"]:
    prompt = "\n".join(
        item.get("text", "") for item in by_id[node_id]["prompt_template"]
    )
    self.assertIn("{{#start_node.report_memory#}}", prompt)
    self.assertIn("不得使用 report_memory 覆盖当前公告事实", prompt)
    self.assertIn("report_memory 为空", prompt)
```

- [ ] **Step 2: Run the workflow test and confirm failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_dify_human_style_workflow -v
```

Expected: `report_memory` is absent from start variables and prompts.

- [ ] **Step 3: Add the optional Start variable**

Under `start_node.data.variables`, add:

```yaml
- label: report_memory
  max_length: 20000
  options: []
  required: false
  type: paragraph
  variable: report_memory
```

Use the Dify-supported multiline input type accepted by the current server version; if import validation rejects `paragraph`, inspect an exported multiline variable from the production Dify version and use that exact type before proceeding.

- [ ] **Step 4: Add one identical memory boundary block to all four LLM prompts**

Insert:

```text
【长期记忆使用规则】

report_memory 是经过人工确认的报告写作和质量规则，只能指导报告风格、分析角度、附件处理、表格组织、常见错误规避和质量检查。

必须遵守：
当前主材料正文 > 当前主材料附件 > 当前辅助材料正文/附件 > 用户本次要求 > report_memory > 历史分析材料。

不得使用 report_memory 覆盖当前公告事实。
不得根据 report_memory 编造当前公告未出现的日期、价格、品种、企业、产品、数量、比例、地区、机构或规则。
如果 report_memory 与 evidence_pack 冲突，以 evidence_pack 中当前主材料和附件为准。
如果 report_memory 为空，则忽略长期记忆。

report_memory:
{{#start_node.report_memory#}}
```

For QA nodes, add:

```text
若报告把 report_memory 中的通用规则误写为当前公告事实，必须列为 unsupported_claim 或 over_inference。
```

- [ ] **Step 5: Run YAML tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_dify_human_style_workflow -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```powershell
git add dify_workflow_pack_id_human_style.yml tests/test_dify_human_style_workflow.py
git commit -m "Add formal memory to Dify report workflow"
```

---

### Task 8: Add Configuration, Ignore Rules, and Operations Documentation

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `.gitignore`
- Create: `docs/report-memory-operations.md`

- [ ] **Step 1: Add configuration**

In `docker-compose.yml`:

```yaml
      MEMORY_DIR: ${MEMORY_DIR:-/app/data/memory}
      REPORT_MEMORY_MAX_CHARS: ${REPORT_MEMORY_MAX_CHARS:-20000}
      MEMORY_CANDIDATES_MAX_CHARS: ${MEMORY_CANDIDATES_MAX_CHARS:-100000}
      MEMORY_BACKUP_KEEP_COUNT: ${MEMORY_BACKUP_KEEP_COUNT:-50}
      MEMORY_WRITE_TOKEN: ${MEMORY_WRITE_TOKEN:-}
```

Do not add another volume because `./data:/app/data` already persists memory.

- [ ] **Step 2: Document configuration without secrets**

Add to `.env.example`:

```dotenv
# Persistent governed report memory.
MEMORY_DIR=/app/data/memory
REPORT_MEMORY_MAX_CHARS=20000
MEMORY_CANDIDATES_MAX_CHARS=100000
MEMORY_BACKUP_KEEP_COUNT=50
# Required in production. Do not commit the real value.
MEMORY_WRITE_TOKEN=
```

- [ ] **Step 3: Ignore runtime memory data**

Add:

```gitignore
data/memory/
*.bak.md
```

- [ ] **Step 4: Write operations documentation**

`docs/report-memory-operations.md` must cover:

- `/memory-ui` usage.
- Difference between formal and candidate memory.
- Production token setup.
- Persistent paths and backup rotation.
- Dify variable and prompt publication.
- Verification with memory enabled and disabled.
- Rollback order: restore Dify version, restore backend code archive, preserve `/opt/medical-notice-analyzer/data/memory`.
- Privacy rule: do not put credentials, personal information, unpublished company data, or current-notice facts into formal memory.

- [ ] **Step 5: Run configuration checks**

Run:

```powershell
docker compose config
rg -n "MEMORY_DIR|MEMORY_WRITE_TOKEN|data/memory" docker-compose.yml .env.example .gitignore docs/report-memory-operations.md
```

Expected: Compose parses successfully and each configuration term is documented.

- [ ] **Step 6: Commit**

```powershell
git add docker-compose.yml .env.example .gitignore docs/report-memory-operations.md
git commit -m "Document report memory operations"
```

---

### Task 9: Extend the Regression Runner Without Changing Defaults

**Files:**
- Modify: `scripts/run_16case_report_regression.py`
- Modify: `tests/test_16case_regression_script.py`

- [ ] **Step 1: Add failing argument and request tests**

Add tests asserting:

- Default generated request remains `{"pack_id": ...}` or explicitly uses `false`.
- `--use-report-memory` sends `true`.
- Each case summary records:

```json
{
  "use_report_memory": true,
  "report_memory_applied": true,
  "report_memory_chars": 1234,
  "memory_read_failed": false
}
```

- [ ] **Step 2: Run regression script tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_16case_regression_script -v
```

Expected: failure because the argument and fields do not exist.

- [ ] **Step 3: Add the CLI option**

Use:

```python
parser.add_argument(
    "--use-report-memory",
    action="store_true",
    help="Explicitly enable formal report memory for each /analysis/run request.",
)
```

Build the request:

```python
run_payload = {
    "pack_id": pack_id,
    "use_report_memory": bool(args.use_report_memory),
}
```

Copy the four bounded metadata fields from the run status into each case result and top-level summary.

- [ ] **Step 4: Run script tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_16case_regression_script -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add scripts/run_16case_report_regression.py tests/test_16case_regression_script.py
git commit -m "Add memory mode to report regression runner"
```

---

### Task 10: Run Local Verification and Browser Checks

**Files:**
- Modify: none unless verification reveals a defect.

- [ ] **Step 1: Run all local tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Run formatting and diff checks**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only intended tracked changes and pre-existing ignored/untracked files.

- [ ] **Step 3: Build the Docker image**

Run:

```powershell
docker compose build
```

Expected: image builds successfully.

- [ ] **Step 4: Run the full suite inside the built container**

Run:

```powershell
docker compose run --rm medical-notice-analyzer python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 5: Start the local service**

Run:

```powershell
docker compose up -d
```

Expected: `medical-notice-analyzer` is running on an available configured port.

- [ ] **Step 6: Verify APIs**

Run:

```powershell
Invoke-RestMethod http://127.0.0.1:8099/health
Invoke-RestMethod http://127.0.0.1:8099/memory/report
Invoke-RestMethod http://127.0.0.1:8099/memory/candidates
```

Expected: health is `ok`, both memory responses contain initialized templates, and candidate content remains separate.

- [ ] **Step 7: Verify UI with the in-app browser**

Check desktop and mobile widths:

- `/memory-ui` loads both tabs.
- Token remains session-only.
- Save confirmation appears.
- Character counter and warning state update.
- `/records-ui` links to memory management.
- Generation switch defaults on.
- Existing material selection remains usable with no overlap.
- `/analysis-runs/{run_id}` displays memory status without content.

- [ ] **Step 8: Commit any verification-only fixes**

If no fixes were needed, do not create an empty commit. If fixes were needed:

```powershell
git add -u
git commit -m "Fix report memory verification issues"
```

---

### Task 11: Publish Dify to a Testable Version and Run Five A/B Cases

**Files:**
- Modify: no source files unless test evidence exposes a defect.
- Output: timestamped desktop test directory.

- [ ] **Step 1: Export or record the currently published Dify version**

Before changing Dify:

- Export the current production workflow YAML.
- Record application name, published time, workflow version, and API endpoint.
- Store the backup outside the Git repository in the existing deployment backup location.

- [ ] **Step 2: Import the updated DSL without overwriting the only rollback copy**

Import `dify_workflow_pack_id_human_style.yml` as a new draft/version. Confirm:

- `pack_id` remains required.
- `report_memory` is optional.
- All four LLM nodes include the boundary block.
- End outputs are unchanged.

- [ ] **Step 3: Publish the Dify draft**

Publish only after a manual Dify test succeeds with:

```json
{
  "pack_id": "the pack_id returned by the immediately preceding POST /analysis/prepare check",
  "report_memory": ""
}
```

Then test with a short safe memory rule and confirm the report does not copy the rule as a notice fact.

- [ ] **Step 4: Run five cases with memory disabled**

Use the agreed representative cases:

- Short body, no attachment.
- Short body, attachment-led.
- Normal `1+0`.
- `1+n`.
- Complex `2+n`.

Run with the regression script default, which must keep memory disabled.

- [ ] **Step 5: Run the same five cases with memory enabled**

Run with:

```powershell
$ts = Get-Date -Format 'yyyyMMddHHmmss'
.\.venv\Scripts\python.exe scripts\run_16case_report_regression.py --case-set failed-plus-short --use-report-memory --output-dir "$env:USERPROFILE\Desktop\report_memory_ab_on_$ts"
```

Run the disabled side with the same source manifest:

```powershell
$ts = Get-Date -Format 'yyyyMMddHHmmss'
.\.venv\Scripts\python.exe scripts\run_16case_report_regression.py --case-set failed-plus-short --output-dir "$env:USERPROFILE\Desktop\report_memory_ab_off_$ts"
```

Use the same automatically selected latest 16-case source manifest for both commands so the material IDs remain identical.

- [ ] **Step 6: Compare A/B results**

Block deployment if enabled memory causes any of:

- More failed or empty reports.
- New unsupported hard facts.
- Lower source-fidelity classification.
- Auxiliary material promoted to a primary conclusion.
- Candidate-memory text appearing in output.
- Technical memory text appearing in report Markdown.
- Material increase in timeout rate.

Record report length, analysis depth, attachment coverage, unsupported facts, memory-applied state, and Dify elapsed time.

- [ ] **Step 7: Fix locally and repeat if blocked**

Any source-code fix returns to Task 10. Any prompt/DSL fix repeats the Dify draft validation and both A/B runs.

---

### Task 12: Back Up Production, Deploy, and Run Full Regression

**Files:**
- Modify: server deployment only after every previous task passes.

- [ ] **Step 1: Confirm current local and server state**

Run locally:

```powershell
git status --short --branch
git rev-parse HEAD
Invoke-RestMethod http://192.168.34.88:8099/health
```

Record the commit SHA and health response.

- [ ] **Step 2: Back up production code and persistent data**

On `192.168.34.88`, create:

```text
/opt/medical-notice-analyzer/deploy_backups/code_before_report_memory_YYYYMMDD_HHMMSS.tar.gz
/opt/medical-notice-analyzer/deploy_backups/memory_before_report_memory_YYYYMMDD_HHMMSS.tar.gz
```

The code archive excludes `.env`, `data`, reports, caches, and previous backups. The memory archive contains `/opt/medical-notice-analyzer/data/memory` if it already exists; if not, record that it was absent.

- [ ] **Step 3: Configure the production write token**

Generate a random 64-hex token with an approved secret-management method and add `MEMORY_WRITE_TOKEN` plus `MEMORY_DIR=/app/data/memory` only to the server `.env`. Do not place an example token value in the plan, terminal output, source tree, or deployment archive.

Do not print the token in terminal summaries, logs, commits, or test artifacts.

- [ ] **Step 4: Deploy without overwriting persistent memory**

Build the deployment archive with exclusions for:

```text
.env
.venv
.cache
.pytest_cache
reports
site-cache
data
dify-db-backups
*.log
```

Extract into `/opt/medical-notice-analyzer`, then:

```bash
docker compose build
docker compose up -d
docker compose ps
```

- [ ] **Step 5: Run production container tests**

```bash
docker compose exec -T medical-notice-analyzer python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 6: Verify production endpoints and persistence**

Verify:

```text
GET http://192.168.34.88:8099/health
GET http://192.168.34.88:8099/memory/report
GET http://192.168.34.88:8099/memory/candidates
GET http://192.168.34.88:8099/memory-ui
```

Perform one authenticated save of formal memory, restart the container, and confirm the same SHA-256 remains.

- [ ] **Step 7: Run the five production A/B cases**

Repeat Task 11 against the production backend and published Dify version.

- [ ] **Step 8: Run the complete 16-case regression with memory enabled**

Generate a new timestamped desktop directory and confirm:

- 16 cases complete.
- No empty report.
- Each case records requested/applied/read-failed memory state.
- `final_review.md` compares memory-enabled results with the latest pre-memory baseline.
- Unsupported fact count does not increase.
- Source fidelity does not materially decline.
- Failures and timeouts do not increase.

- [ ] **Step 9: Roll back on acceptance failure**

Rollback order:

1. Restore the previous published Dify workflow.
2. Restore the server code archive.
3. Keep `/opt/medical-notice-analyzer/data/memory` intact unless the memory file itself is corrupt.
4. Rebuild/restart the old backend.
5. Re-run health and one known report.

- [ ] **Step 10: Commit final evidence documentation**

Update `docs/report-memory-operations.md` with non-secret deployment date, Dify published version, test counts, and regression directory name:

```powershell
git add docs/report-memory-operations.md
git commit -m "Record report memory deployment verification"
```

---

## Final Acceptance Checklist

- [ ] Formal and candidate memory initialize under persistent storage.
- [ ] Formal and candidate memory can be viewed and edited independently.
- [ ] Formal saves create bounded backups and cannot be blank or oversized.
- [ ] Production writes require a secret token.
- [ ] Old `/analysis/run` clients remain memory-off.
- [ ] Records UI defaults memory on and explicitly sends the flag.
- [ ] Memory read failure continues report generation.
- [ ] Candidate memory never enters Dify.
- [ ] Full memory text never appears in run JSON, diagnostics, logs, report Markdown, or Word.
- [ ] Dify generation and both QA rounds enforce evidence-over-memory priority.
- [ ] Local and container test suites pass.
- [ ] Five A/B cases meet fidelity and stability gates.
- [ ] Server code and Dify workflow have rollback copies.
- [ ] Full 16-case production regression passes acceptance criteria.
