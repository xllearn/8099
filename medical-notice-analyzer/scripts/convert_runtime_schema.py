from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.schema_migrations import (  # noqa: E402
    CHECKPOINT_SCHEMA_VERSION,
    HISTORY_SCHEMA_VERSION,
    REPORT_IR_SCHEMA_VERSION,
    RUN_SCHEMA_VERSION,
    downgrade_checkpoint_schema,
    downgrade_history_event_schema,
    downgrade_history_index_schema,
    downgrade_report_ir_schema,
    downgrade_run_schema,
    upgrade_checkpoint_schema,
    upgrade_history_event_schema,
    upgrade_history_index_schema,
    upgrade_report_ir_schema,
    upgrade_run_schema,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a runtime schema into a separate rollback-safe copy"
    )
    parser.add_argument(
        "--artifact",
        required=True,
        choices=("run", "report-ir", "history-index", "history-events", "checkpoint"),
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target-version", required=True, type=int)
    return parser


def _converter(
    artifact: str, target_version: int
) -> tuple[Callable[[Any], dict[str, Any]], int]:
    if artifact == "run":
        current = RUN_SCHEMA_VERSION
        convert = (
            upgrade_run_schema
            if target_version == current
            else lambda value: downgrade_run_schema(value, target_version=target_version)
        )
    elif artifact == "report-ir":
        current = REPORT_IR_SCHEMA_VERSION
        convert = (
            upgrade_report_ir_schema
            if target_version == current
            else lambda value: downgrade_report_ir_schema(
                value, target_version=target_version
            )
        )
    elif artifact == "history-index":
        current = HISTORY_SCHEMA_VERSION
        convert = (
            upgrade_history_index_schema
            if target_version == current
            else lambda value: downgrade_history_index_schema(
                value, target_version=target_version
            )
        )
    elif artifact == "history-events":
        current = HISTORY_SCHEMA_VERSION
        convert = (
            upgrade_history_event_schema
            if target_version == current
            else lambda value: downgrade_history_event_schema(
                value, target_version=target_version
            )
        )
    else:
        current = CHECKPOINT_SCHEMA_VERSION
        convert = (
            upgrade_checkpoint_schema
            if target_version == current
            else lambda value: downgrade_checkpoint_schema(
                value, target_version=target_version
            )
        )
    return convert, current


def _convert_payload(
    artifact: str,
    raw: bytes,
    convert: Callable[[Any], dict[str, Any]],
) -> tuple[bytes, int]:
    text = raw.decode("utf-8")
    if artifact != "history-events":
        converted = convert(json.loads(text))
        return (
            json.dumps(converted, ensure_ascii=False, indent=2, sort_keys=True).encode(
                "utf-8"
            )
            + b"\n",
            1,
        )

    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(convert(json.loads(line)))
        except Exception as exc:
            raise ValueError(f"invalid history event at line {line_number}") from exc
    payload = b"".join(
        json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        + b"\n"
        for event in events
    )
    return payload, len(events)


def _atomic_create(path: Path, payload: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"output already exists: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise FileExistsError(f"output already exists: {path.name}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source = args.input.resolve(strict=True)
    target = args.output.resolve(strict=False)
    if source == target:
        raise ValueError("input and output must be different paths")
    convert, current_version = _converter(args.artifact, args.target_version)
    payload, count = _convert_payload(args.artifact, source.read_bytes(), convert)
    _atomic_create(target, payload)
    print(
        json.dumps(
            {
                "artifact": args.artifact,
                "converted_items": count,
                "current_version": current_version,
                "output_sha256": hashlib.sha256(payload).hexdigest(),
                "success": True,
                "target_version": args.target_version,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
