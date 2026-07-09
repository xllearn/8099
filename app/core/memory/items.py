from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.report_memory import memory_dir


MEMORY_ITEMS_VERSION = 1
APPROVED_STATUS = "approved"
DISABLED_STATUS = "disabled"
VALID_STATUSES = {APPROVED_STATUS, DISABLED_STATUS}
SAFE_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True)
class MemoryItem:
    id: str
    title: str
    content: str
    status: str = DISABLED_STATUS
    scopes: list[str] = field(default_factory=list)
    kind: str = "writing_rule"
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryItem":
        return cls(
            id=str(data.get("id") or "").strip(),
            title=str(data.get("title") or "").strip(),
            content=_normalize_text(str(data.get("content") or "")),
            status=_normalize_status(str(data.get("status") or DISABLED_STATUS)),
            scopes=_normalize_scopes(data.get("scopes") or []),
            kind=str(data.get("kind") or "writing_rule").strip() or "writing_rule",
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = _normalize_status(data["status"])
        data["scopes"] = _normalize_scopes(data["scopes"])
        data["content"] = _normalize_text(data["content"])
        return data


class MemoryItemStore:
    def __init__(self, path: Path):
        self.path = path

    def load_items(self) -> list[MemoryItem]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        items = raw.get("items") if isinstance(raw, dict) else []
        if not isinstance(items, list):
            return []
        return [MemoryItem.from_dict(item) for item in items if isinstance(item, dict)]

    def save_items(self, items: list[MemoryItem]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now().isoformat(sep=" ", timespec="seconds")
        payload_items: list[dict[str, Any]] = []
        for item in items:
            _validate_item_id(item.id)
            data = item.to_dict()
            data["created_at"] = data.get("created_at") or now
            data["updated_at"] = now
            payload_items.append(data)
        payload = {"version": MEMORY_ITEMS_VERSION, "items": payload_items}
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def upsert_item(self, item: MemoryItem) -> MemoryItem:
        _validate_item_id(item.id)
        existing = self.load_items()
        now = datetime.now().isoformat(sep=" ", timespec="seconds")
        created_at = item.created_at or now
        output: list[MemoryItem] = []
        replaced = False
        saved_item = item
        for current in existing:
            if current.id == item.id:
                created_at = current.created_at or created_at
                saved_item = MemoryItem(
                    id=item.id,
                    title=item.title,
                    content=item.content,
                    status=item.status,
                    scopes=item.scopes,
                    kind=item.kind,
                    created_at=created_at,
                    updated_at=now,
                )
                output.append(saved_item)
                replaced = True
            else:
                output.append(current)
        if not replaced:
            saved_item = MemoryItem(
                id=item.id,
                title=item.title,
                content=item.content,
                status=item.status,
                scopes=item.scopes,
                kind=item.kind,
                created_at=created_at,
                updated_at=now,
            )
            output.append(saved_item)
        self.save_items(output)
        return saved_item


def memory_items_path() -> Path:
    return memory_dir() / "memory_items.json"


def retrieve_scoped_memory_items(store: MemoryItemStore, pack: dict[str, Any], *, limit: int = 8) -> list[MemoryItem]:
    context = _scope_context_from_pack(pack)
    matches: list[MemoryItem] = []
    for item in store.load_items():
        if item.status != APPROVED_STATUS:
            continue
        scopes = _normalize_scopes(item.scopes)
        if not scopes:
            continue
        if "global" in scopes or any(scope in context for scope in scopes):
            matches.append(item)
        if len(matches) >= limit:
            break
    return matches


def format_memory_items_for_prompt(items: list[MemoryItem]) -> str:
    if not items:
        return ""
    lines = [
        "# Structured Memory Items",
        "",
        "Memory items are style, angle, and quality guidance only. They are not current notice facts and must not override the evidence pack.",
    ]
    for item in items:
        lines.extend(
            [
                "",
                f"## {item.id}: {item.title}",
                f"- kind: {item.kind}",
                f"- scopes: {', '.join(item.scopes) if item.scopes else '(none)'}",
                "",
                item.content.strip(),
            ]
        )
    return "\n".join(lines).strip()


def _scope_context_from_pack(pack: dict[str, Any]) -> set[str]:
    context = {"global"}
    materials = [*(pack.get("primary_materials") or []), *(pack.get("auxiliary_materials") or [])]
    for material in materials:
        if not isinstance(material, dict):
            continue
        _add_scope_value(context, "menu_code", material.get("menu_code"))
        _add_scope_value(context, "projecttype", material.get("projecttype"))
        _add_scope_value(context, "dl_project_type", material.get("dl_project_type"))
        _add_scope_value(context, "category", material.get("category"))
        _add_scope_value(context, "area", material.get("areaname"))
        _add_scope_value(context, "policytype", material.get("policytype"))
    return context


def _add_scope_value(context: set[str], prefix: str, value: Any) -> None:
    text = str(value or "").strip()
    if text:
        context.add(_normalize_scope(f"{prefix}:{text}"))


def _normalize_status(status: str) -> str:
    value = str(status or DISABLED_STATUS).strip().lower()
    if value not in VALID_STATUSES:
        return DISABLED_STATUS
    return value


def _normalize_scopes(scopes: Any) -> list[str]:
    if not isinstance(scopes, list):
        return []
    normalized: list[str] = []
    for scope in scopes:
        text = _normalize_scope(scope)
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _normalize_scope(scope: Any) -> str:
    return str(scope or "").strip().lower()


def _normalize_text(content: str) -> str:
    return str(content or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _validate_item_id(item_id: str) -> None:
    if not SAFE_ITEM_ID_RE.match(str(item_id or "")):
        raise ValueError("memory item id must be 1-128 safe characters")
