from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)


_MAX_RUN_BYTES = 64 * 1024 * 1024
_MAX_PACK_BYTES = 256 * 1024 * 1024


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
    max_judge_evaluations_per_scan: int = Field(
        default=1,
        ge=1,
        le=100,
    )
    worker_concurrency: int = Field(default=1, ge=1, le=4)
    lease_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    lease_heartbeat_seconds: int = Field(default=30, ge=5, le=300)
    max_run_bytes: int = Field(
        default=4 * 1024 * 1024,
        ge=1024,
        le=_MAX_RUN_BYTES,
    )
    max_pack_bytes: int = Field(
        default=32 * 1024 * 1024,
        ge=1024,
        le=_MAX_PACK_BYTES,
    )
    judge_base_url: str = ""
    judge_api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    judge_model: str = ""
    judge_timeout_seconds: int = Field(default=60, ge=1, le=120)
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8100, ge=1, le=65535)
    locale: str = "zh-CN"

    @field_validator(
        "analysis_run_dir",
        "evidence_pack_dir",
        "advisory_dir",
        mode="before",
    )
    @classmethod
    def normalize_absolute_path(cls, value: Any) -> Path:
        try:
            path = Path(value).expanduser()
        except TypeError as exc:
            raise ValueError("path setting must be path-like") from exc
        if not path.is_absolute():
            raise ValueError("path setting must be absolute")
        return path.resolve()

    @model_validator(mode="after")
    def validate_directory_and_lease_relationships(self) -> Self:
        named_paths = (
            ("analysis_run_dir", self.analysis_run_dir),
            ("evidence_pack_dir", self.evidence_pack_dir),
            ("advisory_dir", self.advisory_dir),
        )
        for index, (left_name, left_path) in enumerate(named_paths):
            for right_name, right_path in named_paths[index + 1 :]:
                if (
                    left_path == right_path
                    or left_path in right_path.parents
                    or right_path in left_path.parents
                ):
                    raise ValueError(
                        f"{left_name} and {right_name} must not overlap"
                    )
        if self.lease_heartbeat_seconds * 3 > self.lease_ttl_seconds:
            raise ValueError(
                "lease heartbeat must be no more than one third of lease TTL"
            )
        return self

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
        try:
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
                max_judge_evaluations_per_scan=_int(
                    mapping,
                    "DEEPEVAL_MAX_JUDGE_EVALUATIONS_PER_SCAN",
                    1,
                    1,
                    100,
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
                    mapping,
                    "DEEPEVAL_MAX_RUN_BYTES",
                    4 * 1024 * 1024,
                    1024,
                    _MAX_RUN_BYTES,
                ),
                max_pack_bytes=_int(
                    mapping,
                    "DEEPEVAL_MAX_PACK_BYTES",
                    32 * 1024 * 1024,
                    1024,
                    _MAX_PACK_BYTES,
                ),
                judge_base_url=str(
                    mapping.get("DEEPEVAL_JUDGE_BASE_URL", "")
                ).strip(),
                judge_api_key=SecretStr(
                    str(mapping.get("DEEPEVAL_JUDGE_API_KEY", "")).strip()
                ),
                judge_model=str(
                    mapping.get("DEEPEVAL_JUDGE_MODEL", "")
                ).strip(),
                judge_timeout_seconds=_int(
                    mapping,
                    "DEEPEVAL_JUDGE_TIMEOUT_SECONDS",
                    60,
                    1,
                    120,
                ),
                http_host=str(
                    mapping.get("DEEPEVAL_ADVISORY_HOST", "127.0.0.1")
                ).strip(),
                http_port=_int(
                    mapping,
                    "DEEPEVAL_ADVISORY_PORT",
                    8100,
                    1,
                    65535,
                ),
                locale=str(
                    mapping.get("DEEPEVAL_ADVISORY_LOCALE", "zh-CN")
                ).strip(),
            )
        except ValidationError as exc:
            messages = "; ".join(
                str(error["msg"]) for error in exc.errors()
            )
            raise SettingsError(f"invalid settings: {messages}") from exc
