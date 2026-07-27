from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class SettingsError(ValueError):
    """Raised when Advisory configuration is invalid."""


def _bool(mapping: Mapping[str, str], name: str, default: bool) -> bool:
    raw = str(mapping.get(name, "")).strip().lower()
    if not raw:
        return default
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise SettingsError(f"{name} must be true or false")


def _int(
    mapping: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    raw = str(mapping.get(name, "")).strip()
    if not raw:
        value = default
    elif raw.isascii() and raw.isdecimal():
        value = int(raw)
    else:
        raise SettingsError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            raise SettingsError(f"{name} must be at least {minimum}")
        raise SettingsError(f"{name} must be between {minimum} and {maximum}")
    return value


class AdvisorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_run_dir: Path
    evidence_pack_dir: Path
    advisory_dir: Path
    runtime_advisory_enabled: bool = False
    scheduled_evaluation_enabled: bool = False
    scan_interval_seconds: int = Field(default=30, ge=5, le=3600)
    worker_concurrency: int = Field(default=1, ge=1, le=4)
    lease_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    lease_heartbeat_seconds: int = Field(default=30, ge=5, le=300)
    max_run_bytes: int = Field(default=4 * 1024 * 1024, ge=1024)
    max_pack_bytes: int = Field(default=32 * 1024 * 1024, ge=1024)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "AdvisorySettings":
        required = (
            "ANALYSIS_RUN_DIR",
            "EVIDENCE_PACK_DIR",
            "DEEPEVAL_ADVISORY_DIR",
        )
        missing = [
            name for name in required if not str(mapping.get(name, "")).strip()
        ]
        if missing:
            raise SettingsError(f"missing settings: {','.join(missing)}")
        return cls(
            analysis_run_dir=Path(mapping["ANALYSIS_RUN_DIR"]),
            evidence_pack_dir=Path(mapping["EVIDENCE_PACK_DIR"]),
            advisory_dir=Path(mapping["DEEPEVAL_ADVISORY_DIR"]),
            runtime_advisory_enabled=_bool(
                mapping, "DEEPEVAL_RUNTIME_ADVISORY_ENABLED", False
            ),
            scheduled_evaluation_enabled=_bool(
                mapping, "DEEPEVAL_SCHEDULED_EVALUATION_ENABLED", False
            ),
            scan_interval_seconds=_int(
                mapping, "DEEPEVAL_SCAN_INTERVAL_SECONDS", 30, 5, 3600
            ),
            worker_concurrency=_int(
                mapping, "DEEPEVAL_WORKER_CONCURRENCY", 1, 1, 4
            ),
            lease_ttl_seconds=_int(
                mapping, "DEEPEVAL_LEASE_TTL_SECONDS", 900, 60, 3600
            ),
            lease_heartbeat_seconds=_int(
                mapping, "DEEPEVAL_LEASE_HEARTBEAT_SECONDS", 30, 5, 300
            ),
            max_run_bytes=_int(
                mapping, "DEEPEVAL_MAX_RUN_BYTES", 4 * 1024 * 1024, 1024
            ),
            max_pack_bytes=_int(
                mapping, "DEEPEVAL_MAX_PACK_BYTES", 32 * 1024 * 1024, 1024
            ),
        )
