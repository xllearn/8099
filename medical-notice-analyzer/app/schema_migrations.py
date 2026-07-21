from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


RUN_SCHEMA_VERSION = 1
HISTORY_SCHEMA_VERSION = 2
CHECKPOINT_SCHEMA_VERSION = 1
REPORT_IR_SCHEMA_VERSION = 1


class SchemaMigrationError(ValueError):
    pass


class SchemaValidationError(SchemaMigrationError):
    pass


class UnknownSchemaVersionError(SchemaMigrationError):
    pass


def _object(value: Any, artifact: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{artifact} must be an object")
    return copy.deepcopy(dict(value))


def _version(
    value: Mapping[str, Any],
    *,
    artifact: str,
    missing_version: int,
    current_version: int,
    supported_versions: set[int],
) -> int:
    raw = value.get("schema_version", missing_version)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise SchemaValidationError(f"{artifact} schema_version is invalid")
    if raw > current_version:
        raise UnknownSchemaVersionError(
            f"{artifact} schema_version {raw} is newer than {current_version}"
        )
    if raw not in supported_versions:
        raise SchemaValidationError(f"{artifact} schema_version {raw} is unsupported")
    return raw


def upgrade_run_schema(value: Any) -> dict[str, Any]:
    upgraded = _object(value, "run")
    _version(
        upgraded,
        artifact="run",
        missing_version=0,
        current_version=RUN_SCHEMA_VERSION,
        supported_versions={0, RUN_SCHEMA_VERSION},
    )
    upgraded["schema_version"] = RUN_SCHEMA_VERSION
    return upgraded


def downgrade_run_schema(value: Any, *, target_version: int) -> dict[str, Any]:
    current = upgrade_run_schema(value)
    if target_version == RUN_SCHEMA_VERSION:
        return current
    if target_version != 0:
        raise SchemaValidationError("unsupported run downgrade target")
    current.pop("schema_version", None)
    return current


def upgrade_report_ir_schema(value: Any) -> dict[str, Any]:
    upgraded = _object(value, "ReportIR")
    _version(
        upgraded,
        artifact="ReportIR",
        missing_version=0,
        current_version=REPORT_IR_SCHEMA_VERSION,
        supported_versions={0, REPORT_IR_SCHEMA_VERSION},
    )
    upgraded["schema_version"] = REPORT_IR_SCHEMA_VERSION
    return upgraded


def downgrade_report_ir_schema(
    value: Any, *, target_version: int
) -> dict[str, Any]:
    current = upgrade_report_ir_schema(value)
    if target_version == REPORT_IR_SCHEMA_VERSION:
        return current
    if target_version != 0:
        raise SchemaValidationError("unsupported ReportIR downgrade target")
    current.pop("schema_version", None)
    return current


def _upgrade_history_item(value: Any) -> dict[str, Any]:
    upgraded = _object(value, "history item")
    _version(
        upgraded,
        artifact="history item",
        missing_version=1,
        current_version=HISTORY_SCHEMA_VERSION,
        supported_versions={1, HISTORY_SCHEMA_VERSION},
    )
    upgraded["schema_version"] = HISTORY_SCHEMA_VERSION
    return upgraded


def _downgrade_history_item(value: Any, target_version: int) -> dict[str, Any]:
    current = _upgrade_history_item(value)
    if target_version == HISTORY_SCHEMA_VERSION:
        return current
    if target_version != 1:
        raise SchemaValidationError("unsupported history item downgrade target")
    current.pop("schema_version", None)
    return current


def upgrade_history_event_schema(value: Any) -> dict[str, Any]:
    upgraded = _object(value, "history event")
    _version(
        upgraded,
        artifact="history event",
        missing_version=1,
        current_version=HISTORY_SCHEMA_VERSION,
        supported_versions={1, HISTORY_SCHEMA_VERSION},
    )
    upgraded["item"] = _upgrade_history_item(upgraded.get("item"))
    upgraded["schema_version"] = HISTORY_SCHEMA_VERSION
    return upgraded


def downgrade_history_event_schema(
    value: Any, *, target_version: int
) -> dict[str, Any]:
    current = upgrade_history_event_schema(value)
    if target_version == HISTORY_SCHEMA_VERSION:
        return current
    if target_version != 1:
        raise SchemaValidationError("unsupported history event downgrade target")
    current["item"] = _downgrade_history_item(current["item"], 1)
    current["schema_version"] = 1
    return current


def upgrade_history_index_schema(value: Any) -> dict[str, Any]:
    upgraded = _object(value, "history index")
    _version(
        upgraded,
        artifact="history index",
        missing_version=1,
        current_version=HISTORY_SCHEMA_VERSION,
        supported_versions={1, HISTORY_SCHEMA_VERSION},
    )
    runs = upgraded.get("runs")
    if not isinstance(runs, Mapping):
        raise SchemaValidationError("history index runs must be an object")
    upgraded["runs"] = {
        str(run_id): _upgrade_history_item(item)
        for run_id, item in runs.items()
    }
    upgraded["schema_version"] = HISTORY_SCHEMA_VERSION
    return upgraded


def downgrade_history_index_schema(
    value: Any, *, target_version: int
) -> dict[str, Any]:
    current = upgrade_history_index_schema(value)
    if target_version == HISTORY_SCHEMA_VERSION:
        return current
    if target_version != 1:
        raise SchemaValidationError("unsupported history index downgrade target")
    current["runs"] = {
        run_id: _downgrade_history_item(item, 1)
        for run_id, item in current["runs"].items()
    }
    current["schema_version"] = 1
    return current


def _upgrade_checkpoint_event(value: Any) -> dict[str, Any]:
    upgraded = _object(value, "checkpoint event")
    _version(
        upgraded,
        artifact="checkpoint event",
        missing_version=0,
        current_version=CHECKPOINT_SCHEMA_VERSION,
        supported_versions={0, CHECKPOINT_SCHEMA_VERSION},
    )
    upgraded["schema_version"] = CHECKPOINT_SCHEMA_VERSION
    return upgraded


def _downgrade_checkpoint_event(value: Any) -> dict[str, Any]:
    current = _upgrade_checkpoint_event(value)
    current.pop("schema_version", None)
    return current


def upgrade_checkpoint_schema(value: Any) -> dict[str, Any]:
    upgraded = _object(value, "checkpoint")
    _version(
        upgraded,
        artifact="checkpoint",
        missing_version=0,
        current_version=CHECKPOINT_SCHEMA_VERSION,
        supported_versions={0, CHECKPOINT_SCHEMA_VERSION},
    )
    events = upgraded.get("events")
    steps = upgraded.get("steps")
    requests = upgraded.get("recovery_requests")
    if not isinstance(events, list) or not isinstance(steps, Mapping) or not isinstance(
        requests, list
    ):
        raise SchemaValidationError("checkpoint collections are invalid")
    upgraded["events"] = [_upgrade_checkpoint_event(event) for event in events]
    upgraded["steps"] = {
        str(step): _upgrade_checkpoint_event(event)
        for step, event in steps.items()
    }
    upgraded["schema_version"] = CHECKPOINT_SCHEMA_VERSION
    return upgraded


def downgrade_checkpoint_schema(
    value: Any, *, target_version: int
) -> dict[str, Any]:
    current = upgrade_checkpoint_schema(value)
    if target_version == CHECKPOINT_SCHEMA_VERSION:
        return current
    if target_version != 0:
        raise SchemaValidationError("unsupported checkpoint downgrade target")
    current["events"] = [
        _downgrade_checkpoint_event(event) for event in current["events"]
    ]
    current["steps"] = {
        step: _downgrade_checkpoint_event(event)
        for step, event in current["steps"].items()
    }
    current.pop("schema_version", None)
    return current
