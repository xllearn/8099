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


class MemoryTokenError(PermissionError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


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

本文件是医药公告分析报告生成的正式长期记忆。内容必须来自人工确认，只能指导报告写法、分析角度、附件处理、表格组织、常见错误规避和质量检查。

## Evidence Priority

当前主材料正文 > 当前主材料附件 > 当前辅助材料正文/附件 > 用户本次要求 > report_memory > 历史分析材料。

不得使用 report_memory 覆盖当前公告事实。不得根据 report_memory 编造当前公告未出现的日期、价格、品种、企业、产品、数量、比例、地区、机构或规则。

## Writing Rules

- 报告使用专业、克制、分析型语气。
- 不大段照搬公告原文，应在严格依据原文的基础上提炼企业关注点。
- 对未明确的信息写“公告未明确”或“以官方后续通知为准”，不得自行补充。
- 避免“显然”“必然”“唯一”“一定导致”等过度判断。

## Attachment Rules

- 附件解析成功时，优先提取品种、产品、企业、价格、规格型号、申报时间、采购量和注册证等结构化信息。
- Excel 附件不把全部行塞入报告，应按品类、价格、企业和规则摘要化。
- 附件仅有元数据或解析失败时，不得推断附件内的清单、企业、产品或价格。

## Quality Rules

- 检查是否把辅助材料当成主材料事实。
- 检查价格、数量、时间、比例是否有当前公告依据。
- 检查是否存在原文未明确的推断。
"""


CANDIDATE_MEMORY_TEMPLATE = """# Memory Candidates

本文件记录候选长期记忆。候选内容默认不参与报告生成，也不会进入 Dify payload。经人工审核确认后，才可手动整理进 report_memory.md。

## Candidate-YYYYMMDD-001

### Source

用户反馈 / 质检节点 / 生成节点 / 附件解析模块

### Candidate Rule

在这里记录待审核经验。

### Review Status

待审核
"""


def memory_dir() -> Path:
    configured = (os.getenv("MEMORY_DIR") or "").strip()
    return Path(configured) if configured else Path.cwd() / "data" / "memory"


def write_protection_enabled() -> bool:
    return bool((os.getenv("MEMORY_WRITE_TOKEN") or "").strip())


def require_write_token(provided_token: str | None) -> None:
    expected = (os.getenv("MEMORY_WRITE_TOKEN") or "").strip()
    if not expected:
        return
    if not provided_token:
        raise MemoryTokenError("MEMORY_TOKEN_MISSING", "memory write token is required")
    if provided_token != expected:
        raise MemoryTokenError("MEMORY_TOKEN_INVALID", "memory write token is invalid")


def _memory_path(kind: MemoryKind) -> Path:
    filename = "report_memory.md" if kind is MemoryKind.REPORT else "memory_candidates.md"
    return memory_dir() / filename


def _max_chars(kind: MemoryKind) -> int:
    name = "REPORT_MEMORY_MAX_CHARS" if kind is MemoryKind.REPORT else "MEMORY_CANDIDATES_MAX_CHARS"
    default = 15000 if kind is MemoryKind.REPORT else 100000
    try:
        return max(1, int(os.getenv(name) or default))
    except ValueError:
        return default


def _backup_keep_count() -> int:
    try:
        return max(1, int(os.getenv("MEMORY_BACKUP_KEEP_COUNT") or 50))
    except ValueError:
        return 50


def _template(kind: MemoryKind) -> str:
    return REPORT_MEMORY_TEMPLATE if kind is MemoryKind.REPORT else CANDIDATE_MEMORY_TEMPLATE


def _normalize_content(content: str) -> str:
    return str(content or "").replace("\r\n", "\n").replace("\r", "\n")


def _document(kind: MemoryKind, path: Path, content: str) -> MemoryDocument:
    stat = path.stat()
    return MemoryDocument(
        kind=kind,
        content=content,
        chars=len(content),
        max_chars=_max_chars(kind),
        updated_at=datetime.fromtimestamp(stat.st_mtime).isoformat(sep=" ", timespec="seconds"),
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        write_protection_enabled=write_protection_enabled(),
    )


def read_memory(kind: MemoryKind) -> MemoryDocument:
    path = _memory_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_template(kind), encoding="utf-8", newline="\n")
    content = _normalize_content(path.read_text(encoding="utf-8"))
    return _document(kind, path, content)


def _validate_content(kind: MemoryKind, content: str) -> str:
    normalized = _normalize_content(content)
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
    backup_dir = memory_dir()
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
    backup_dir = memory_dir()
    if not backup_dir.exists():
        return
    backups = sorted(backup_dir.glob(f"{prefix}_*.bak.md"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in backups[_backup_keep_count() :]:
        old.unlink(missing_ok=True)


def save_memory(kind: MemoryKind, content: str) -> MemorySaveResult:
    normalized = _validate_content(kind, content)
    path = _memory_path(kind)
    current = read_memory(kind)
    backup = _next_backup_path(kind)
    shutil.copy2(path, backup)

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(normalized)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise

    _rotate_backups(kind)
    document = _document(kind, path, normalized)
    if document.sha256 == current.sha256:
        return MemorySaveResult(document=document, backup_name=backup.name)
    return MemorySaveResult(document=document, backup_name=backup.name)
