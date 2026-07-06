from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class WorkflowBackend(str, Enum):
    DIFY_LEGACY = "dify_legacy"
    LOCAL_ENGINE = "local_engine"


class WorkflowRunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    FINISHED = "finished"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowNodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _enum_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value or "")


@dataclass
class WorkflowNode:
    name: str
    status: WorkflowNodeStatus | str = WorkflowNodeStatus.PENDING
    started_at: str | None = None
    finished_at: str = ""
    elapsed_ms: int = 0
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        node = {
            "name": self.name,
            "status": _enum_value(self.status) or WorkflowNodeStatus.PENDING.value,
            "started_at": self.started_at if self.started_at is not None else utc_now_iso(),
            "finished_at": self.finished_at or "",
            "elapsed_ms": int(self.elapsed_ms or 0),
            "error": self.error or "",
        }
        if self.extra:
            node.update(self.extra)
        return node


@dataclass
class WorkflowRun:
    run_id: str
    pack_id: str
    status: WorkflowRunStatus | str = WorkflowRunStatus.CREATED
    backend: WorkflowBackend | str = WorkflowBackend.DIFY_LEGACY
    nodes: list[WorkflowNode | dict[str, Any]] = field(default_factory=list)
    workflow_run_id: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    started_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    error: str = ""
    artifacts: dict[str, Any] = field(
        default_factory=lambda: {
            "report_markdown_path": "",
            "report_ir_path": "",
            "qa_result_path": "",
        }
    )
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at or utc_now_iso()
        backend = _enum_value(self.backend) or WorkflowBackend.DIFY_LEGACY.value
        record = {
            "run_id": self.run_id,
            "pack_id": self.pack_id,
            "status": _enum_value(self.status) or WorkflowRunStatus.CREATED.value,
            "backend": backend,
            "workflow_backend": backend,
            "workflow_run_id": self.workflow_run_id or "",
            "created_at": created_at,
            "started_at": self.started_at or created_at,
            "updated_at": self.updated_at or created_at,
            "finished_at": self.finished_at or "",
            "error": self.error or "",
            "artifacts": dict(self.artifacts or {}),
            "nodes": [node.to_dict() if isinstance(node, WorkflowNode) else dict(node) for node in self.nodes],
        }
        if self.extra:
            record.update(self.extra)
        return record


def make_workflow_node(
    name: str,
    *,
    status: str | WorkflowNodeStatus = WorkflowNodeStatus.PENDING,
    started_at: str | None = None,
    finished_at: str | None = None,
    elapsed_ms: int = 0,
    error: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    node = WorkflowNode(
        name=name,
        status=status,
        started_at=started_at,
        finished_at=finished_at or "",
        elapsed_ms=elapsed_ms,
        error=error,
        extra=extra or {},
    ).to_dict()
    return node
