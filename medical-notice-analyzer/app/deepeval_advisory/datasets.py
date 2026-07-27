from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import time
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Literal, NamedTuple, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from app.deepeval_advisory.hashing import (
    BoundaryViolation,
    assert_safe_outbound_text,
    canonical_json_bytes,
    canonical_sha256,
)
from app.deepeval_advisory.models import AdvisoryProjection, StrictModel


MAX_DATASET_JSON_BYTES = 1024 * 1024
MAX_DATASET_JSONL_BYTES = 16 * 1024 * 1024
MAX_DATASET_JSONL_LINE_BYTES = 256 * 1024
MAX_DATASET_TREE_DEPTH = 8
MAX_DATASET_MANIFEST_ENTRIES = 128
MAX_DATASET_TREE_MEMBERS = (
    1 + MAX_DATASET_TREE_DEPTH * MAX_DATASET_MANIFEST_ENTRIES
)
FIXED10_SOURCE_SCHEMA_VERSION = "8099.deepeval-fixed10-source/v1"
FIXED10_RUBRIC_VERSION = "advisory-rubric-v1"

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_CASE_REF_PATTERN = r"^case_[0-9a-f]{32}$"
_SOURCE_GROUP_REF_PATTERN = r"^source_group_[0-9a-f]{32}$"
_SAMPLE_REF_PATTERN = r"^sample_[0-9a-f]{32}$"
_REVIEW_REF_PATTERN = r"^review_[0-9a-f]{32}$"
_ADJUDICATION_REF_PATTERN = r"^adjudication_[0-9a-f]{32}$"
_REVIEWER_REF_PATTERN = r"^reviewer_[0-9a-f]{32}$"
_SAFE_SLUG_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,63}$"
_VERSION_TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_CANONICAL_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)
_SAFE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")
_FIXED10_SOURCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_URI_LOCATOR = re.compile(
    r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*:(?=\S)",
)
_RAW_IDENTITY_NAME = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:run_id|pack_id|articleid|menu_code)"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_RAW_RUN_OR_PACK_ID = re.compile(
    r"(?<![A-Za-z0-9_-])(?:run|pack)_(?!ref_)[A-Za-z0-9_-]{8,80}"
    r"(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_RAW_UUID = re.compile(
    r"(?<![0-9A-Fa-f])"
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
    r"(?![0-9A-Fa-f])"
)
_KNOWN_RAW_MENU_CODE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:"
    r"project_notice|project_information|project_analysis|"
    r"policy_interpretation|yb_drg|ylsf|lxzn"
    r")(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_RAW_FILENAME = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"[^\s<>:\"'\\/]{1,120}\."
    r"(?:pdf|docx?|xlsx?|csv|tsv|jsonl?|md|txt|zip|rar|7z)"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_TRAVERSAL_OR_RELATIVE_FILE_PATH = re.compile(
    r"(?:^|[\s\"'(])(?:\.\.?[\\/]|"
    r"[A-Za-z0-9._-]+[\\/][A-Za-z0-9._\\/-]+\."
    r"(?:pdf|docx?|xlsx?|csv|tsv|jsonl?|md|txt|zip|rar|7z))",
    re.IGNORECASE,
)
_FULL_SOURCE_MATERIAL = re.compile(
    r"(?:report_markdown|report_ir|evidence_pack|evidence_items|"
    r"full[\s_-]+report|full[\s_-]+evidence[\s_-]+pack|"
    r"完整报告|完整\s*Evidence\s*Pack)",
    re.IGNORECASE,
)
_CHAIN_OF_THOUGHT = re.compile(
    r"(?:chain[\s_-]+of[\s_-]+thought|step[\s_-]+by[\s_-]+step|"
    r"<think>|</think>|思维链|推理过程|逐步推理|逐步分析)",
    re.IGNORECASE,
)
_FORBIDDEN_CASE_KEYS = frozenset(
    {
        "run_id",
        "pack_id",
        "articleid",
        "menu_code",
        "filename",
        "file_name",
        "path",
        "file_path",
        "url",
        "report_markdown",
        "report_ir",
        "evidence_pack",
        "evidence_items",
    }
)


MetricObservation = Literal[
    "consistent",
    "mixed",
    "inconsistent",
    "not_applicable",
]
RiskTag = Literal[
    "date",
    "amount",
    "entity",
    "procurement_scope",
    "attachment_state",
    "evidence_c_boundary",
    "memory_boundary",
    "unsupported_key_conclusion",
]
DatasetSplit = Literal["fixed", "calibration", "validation"]
IntegrityErrorCategory = Literal[
    "root_invalid",
    "file_unavailable",
    "file_type",
    "file_size",
    "file_changed",
    "json_invalid",
    "manifest_invalid",
    "path_invalid",
    "tree_unexpected",
    "tree_limit",
    "file_hash_mismatch",
    "case_invalid",
    "entry_metadata_mismatch",
    "jsonl_invalid",
    "jsonl_model_invalid",
]

_ERROR_ORDER = {
    category: index
    for index, category in enumerate(
        (
            "root_invalid",
            "file_unavailable",
            "file_type",
            "file_size",
            "file_changed",
            "json_invalid",
            "manifest_invalid",
            "path_invalid",
            "tree_unexpected",
            "tree_limit",
            "file_hash_mismatch",
            "case_invalid",
            "entry_metadata_mismatch",
            "jsonl_invalid",
            "jsonl_model_invalid",
        )
    )
}


class DatasetError(ValueError):
    """A bounded deterministic dataset-integrity failure."""

    def __init__(self, category: IntegrityErrorCategory):
        self.category = category
        super().__init__(f"{category}: dataset integrity validation failed")


class Fixed10FreezeError(ValueError):
    """A bounded failure while freezing an immutable fixed10 dataset."""

    def __init__(self, category: str):
        self.category = category
        super().__init__(f"{category}: fixed10 dataset freeze failed")


def _canonical_timestamp(value: str) -> str:
    if not _CANONICAL_TIMESTAMP_PATTERN.fullmatch(value):
        raise ValueError("timestamp must be canonical UTC")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ValueError("timestamp must be canonical UTC") from exc
    return value


def _assert_safe_content(value: str, *, conclusion_only: bool = False) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        raise ValueError("content must be nonblank")
    scanned = unicodedata.normalize("NFKC", normalized)
    try:
        assert_safe_outbound_text(scanned)
    except BoundaryViolation:
        raise ValueError("content violates the dataset safety boundary") from None
    if (
        _URI_LOCATOR.search(scanned)
        or _RAW_IDENTITY_NAME.search(scanned)
        or _RAW_RUN_OR_PACK_ID.search(scanned)
        or _RAW_UUID.search(scanned)
        or _KNOWN_RAW_MENU_CODE.search(scanned)
        or _RAW_FILENAME.search(scanned)
        or _TRAVERSAL_OR_RELATIVE_FILE_PATH.search(scanned)
        or _FULL_SOURCE_MATERIAL.search(scanned)
        or _CHAIN_OF_THOUGHT.search(scanned)
    ):
        raise ValueError("content violates the dataset safety boundary")
    if conclusion_only and "\n" in normalized:
        raise ValueError("reason must contain one conclusion-only paragraph")
    return normalized.strip()


def _walk_raw_case_boundary(value: Any, *, key: str | None = None) -> None:
    if key is not None:
        normalized_key = unicodedata.normalize("NFKC", key).casefold()
        if normalized_key in _FORBIDDEN_CASE_KEYS:
            raise ValueError("case violates the dataset safety boundary")
    if isinstance(value, dict):
        for nested_key, nested in value.items():
            if not isinstance(nested_key, str):
                raise ValueError("case violates the dataset safety boundary")
            _walk_raw_case_boundary(nested, key=nested_key)
        return
    if isinstance(value, (list, tuple)):
        for nested in value:
            _walk_raw_case_boundary(nested)
        return
    if isinstance(value, str):
        if key == "level" and value == "C":
            raise ValueError("case violates the dataset safety boundary")
        _assert_safe_content(value)


def _assert_projection_boundary(projection: AdvisoryProjection) -> None:
    _walk_raw_case_boundary(projection.model_dump(mode="json"))


def _verify_projection_hash(projection: AdvisoryProjection) -> None:
    payload = projection.model_dump(mode="json")
    digest = payload.pop("projection_sha256")
    if canonical_sha256(payload) != digest:
        raise ValueError("projection integrity hash is inconsistent")


def _validate_raw_projection(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("raw projection must be an object")
    if set(AdvisoryProjection.model_fields).difference(value):
        raise ValueError("raw projection omits required fields")
    digest = value.get("projection_sha256")
    payload = dict(value)
    payload.pop("projection_sha256", None)
    try:
        expected = canonical_sha256(payload)
    except (TypeError, ValueError):
        raise ValueError("raw projection integrity hash is invalid") from None
    if not isinstance(digest, str) or digest != expected:
        raise ValueError("raw projection integrity hash is inconsistent")


def _validate_unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


class MetricLabels(StrictModel):
    claim_faithfulness_v1: MetricObservation
    critical_coverage_v1: MetricObservation
    attachment_state_consistency_v1: MetricObservation
    answer_relevancy_v1: MetricObservation


class _RawSelfHashedModel(StrictModel):
    _self_hash_field: ClassVar[str]
    _integrity_label: ClassVar[str]

    @classmethod
    def _validate_raw_content(cls, value: dict[str, Any]) -> None:
        return None

    @model_validator(mode="before")
    @classmethod
    def validate_raw_self_hash(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise ValueError("self-hashed contract requires a raw object")
        missing = set(cls.model_fields).difference(value)
        if missing:
            raise ValueError("self-hashed contract omits required fields")
        cls._validate_raw_content(value)
        digest = value.get(cls._self_hash_field)
        payload = dict(value)
        payload.pop(cls._self_hash_field, None)
        try:
            expected = canonical_sha256(payload)
        except (TypeError, ValueError):
            raise ValueError(
                f"{cls._integrity_label} integrity hash is invalid"
            ) from None
        if not isinstance(digest, str) or digest != expected:
            raise ValueError(
                f"{cls._integrity_label} integrity hash is inconsistent"
            )
        return value

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        try:
            if isinstance(json_data, str):
                decoded = json_data
            else:
                decoded = bytes(json_data).decode("utf-8", errors="strict")
            value = json.loads(
                decoded,
                parse_constant=_reject_nonfinite_json,
                object_pairs_hook=_unique_json_object,
            )
        except (
            UnicodeError,
            json.JSONDecodeError,
            RecursionError,
            TypeError,
            ValueError,
        ):
            raise ValueError("self-hashed JSON is invalid") from None
        if not isinstance(value, dict):
            raise ValueError("self-hashed JSON must be an object")
        return cls.model_validate(
            value,
            strict=strict,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )


class DatasetCase(_RawSelfHashedModel):
    _self_hash_field = "case_sha256"
    _integrity_label = "case"

    schema_version: Literal["8099.deepeval-case/v1"]
    case_ref: str = Field(pattern=_CASE_REF_PATTERN)
    source_group_ref: str = Field(pattern=_SOURCE_GROUP_REF_PATTERN)
    split: DatasetSplit
    risk_tier: Literal["high", "standard"]
    risk_tags: tuple[RiskTag, ...] = Field(max_length=8)
    material_identity_sha256: str = Field(pattern=_HASH_PATTERN)
    source_content_sha256: str = Field(pattern=_HASH_PATTERN)
    projection: AdvisoryProjection
    case_sha256: str = Field(pattern=_HASH_PATTERN)

    @classmethod
    def _validate_raw_content(cls, value: dict[str, Any]) -> None:
        _walk_raw_case_boundary(value)
        _validate_raw_projection(value.get("projection"))

    @model_validator(mode="after")
    def validate_case_contract(self) -> Self:
        _assert_projection_boundary(self.projection)
        _verify_projection_hash(self.projection)
        _validate_unique(self.risk_tags, "risk_tags")
        if self.risk_tags and self.risk_tier != "high":
            raise ValueError("nonempty risk_tags require risk_tier=high")
        payload = self.model_dump(mode="json")
        digest = payload.pop("case_sha256")
        if canonical_sha256(payload) != digest:
            raise ValueError("case integrity hash is inconsistent")
        return self


class Fixed10SourceExport(StrictModel):
    schema_version: Literal["8099.deepeval-fixed10-source/v1"]
    case_ref: str = Field(pattern=_CASE_REF_PATTERN)
    source_group_ref: str = Field(pattern=_SOURCE_GROUP_REF_PATTERN)
    risk_tier: Literal["high", "standard"]
    risk_tags: tuple[RiskTag, ...] = Field(max_length=8)
    material_identity_sha256: str = Field(pattern=_HASH_PATTERN)
    source_content_sha256: str = Field(pattern=_HASH_PATTERN)
    projection: AdvisoryProjection
    fixed3_rank: Literal[1, 2, 3] | None

    @model_validator(mode="before")
    @classmethod
    def validate_raw_export(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise ValueError("fixed10 source export must be a raw object")
        if set(cls.model_fields).difference(value):
            raise ValueError("fixed10 source export omits required fields")
        _walk_raw_case_boundary(value)
        _validate_raw_projection(value.get("projection"))
        return value

    @model_validator(mode="after")
    def validate_export_contract(self) -> Self:
        _assert_projection_boundary(self.projection)
        _verify_projection_hash(self.projection)
        _validate_unique(self.risk_tags, "risk_tags")
        if self.risk_tags and self.risk_tier != "high":
            raise ValueError("nonempty risk_tags require risk_tier=high")
        return self


class DatasetEntry(StrictModel):
    case_ref: str = Field(pattern=_CASE_REF_PATTERN)
    relative_path: str = Field(min_length=1, max_length=240)
    file_sha256: str = Field(pattern=_HASH_PATTERN)
    source_group_ref: str = Field(pattern=_SOURCE_GROUP_REF_PATTERN)
    split: DatasetSplit
    risk_tier: Literal["high", "standard"]

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if (
            not value
            or "\\" in value
            or _WINDOWS_DRIVE.match(value)
            or value.startswith("/")
        ):
            raise ValueError("relative_path must be a safe relative path")
        candidate = PurePosixPath(value)
        if (
            candidate.is_absolute()
            or any(part in {"", ".", ".."} for part in candidate.parts)
            or len(candidate.parts) > MAX_DATASET_TREE_DEPTH
            or candidate.as_posix() != value
            or candidate.suffix != ".json"
            or any(
                _SAFE_PATH_COMPONENT.fullmatch(part) is None
                for part in candidate.parts
            )
        ):
            raise ValueError("relative_path must be a safe relative path")
        return value


class DatasetManifest(_RawSelfHashedModel):
    _self_hash_field = "manifest_sha256"
    _integrity_label = "manifest"

    schema_version: Literal["8099.deepeval-dataset-manifest/v1"]
    dataset_id: str = Field(pattern=_SAFE_SLUG_PATTERN)
    dataset_version: Literal["fixed10/v1", "calibration100/v1"]
    projection_version: Literal["claim-ab-v1"]
    rubric_version: str = Field(pattern=_VERSION_TOKEN_PATTERN)
    entries: tuple[DatasetEntry, ...] = Field(
        min_length=1,
        max_length=MAX_DATASET_MANIFEST_ENTRIES,
    )
    subsets: dict[str, tuple[str, ...]]
    declared_count: int = Field(ge=0)
    runnable_count: int = Field(ge=0)
    exclusion_count: int = Field(ge=0)
    manifest_sha256: str = Field(pattern=_HASH_PATTERN)

    @field_validator("subsets")
    @classmethod
    def validate_subsets(
        cls,
        value: dict[str, tuple[str, ...]],
    ) -> dict[str, tuple[str, ...]]:
        for name, refs in value.items():
            if re.fullmatch(_SAFE_SLUG_PATTERN, name) is None:
                raise ValueError("subset name is invalid")
            _validate_unique(refs, "subset case refs")
            for case_ref in refs:
                if re.fullmatch(_CASE_REF_PATTERN, case_ref) is None:
                    raise ValueError("subset case ref is invalid")
        return value

    @model_validator(mode="after")
    def validate_manifest_contract(self) -> Self:
        expected_dataset_id = self.dataset_version.partition("/")[0]
        if self.dataset_id != expected_dataset_id:
            raise ValueError("dataset_id and dataset_version must agree")
        case_refs = tuple(entry.case_ref for entry in self.entries)
        relative_paths = tuple(entry.relative_path for entry in self.entries)
        if len(case_refs) != len(set(case_refs)):
            raise ValueError("duplicate case_ref is not allowed")
        if len(relative_paths) != len(set(relative_paths)):
            raise ValueError("duplicate relative_path is not allowed")
        if self.runnable_count != len(self.entries):
            raise ValueError("runnable_count must equal the entry count")
        if self.declared_count != self.runnable_count + self.exclusion_count:
            raise ValueError("declared_count is inconsistent")
        known_refs = set(case_refs)
        if any(
            case_ref not in known_refs
            for refs in self.subsets.values()
            for case_ref in refs
        ):
            raise ValueError("subset references an unknown case")
        payload = self.model_dump(mode="json")
        digest = payload.pop("manifest_sha256")
        if canonical_sha256(payload) != digest:
            raise ValueError("manifest integrity hash is inconsistent")
        return self


class _BoundedReasonModel(StrictModel):
    @field_validator("bounded_reason", check_fields=False)
    @classmethod
    def validate_bounded_reason(cls, value: str) -> str:
        return _assert_safe_content(value, conclusion_only=True)


class Prelabel(_BoundedReasonModel):
    schema_version: Literal["8099.deepeval-prelabel/v1"] = (
        "8099.deepeval-prelabel/v1"
    )
    case_ref: str = Field(pattern=_CASE_REF_PATTERN)
    sample_ref: str | None = Field(default=None, pattern=_SAMPLE_REF_PATTERN)
    agent_id: Literal["agent-a", "agent-b"]
    input_sha256: str = Field(pattern=_HASH_PATTERN)
    rubric_sha256: str = Field(pattern=_HASH_PATTERN)
    prompt_sha256: str = Field(pattern=_HASH_PATTERN)
    metric_labels: MetricLabels
    bounded_reason: str = Field(min_length=1, max_length=500)


class HumanReview(_BoundedReasonModel):
    schema_version: Literal["8099.deepeval-human-review/v1"] = (
        "8099.deepeval-human-review/v1"
    )
    review_ref: str = Field(pattern=_REVIEW_REF_PATTERN)
    case_ref: str = Field(pattern=_CASE_REF_PATTERN)
    sample_ref: str | None = Field(default=None, pattern=_SAMPLE_REF_PATTERN)
    reviewer_ref: str = Field(pattern=_REVIEWER_REF_PATTERN)
    review_reason: tuple[
        Literal["disagreement", "high_risk", "validation"],
        ...,
    ] = Field(min_length=1, max_length=3)
    metric_labels: MetricLabels
    bounded_reason: str = Field(min_length=1, max_length=500)
    reviewed_at: str
    provenance: Literal["human_supplied"]

    @field_validator("review_reason")
    @classmethod
    def validate_review_reason(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _validate_unique(value, "review_reason")

    @field_validator("reviewed_at")
    @classmethod
    def validate_reviewed_at(cls, value: str) -> str:
        return _canonical_timestamp(value)


class HumanAdjudication(_BoundedReasonModel):
    schema_version: Literal["8099.deepeval-human-adjudication/v1"] = (
        "8099.deepeval-human-adjudication/v1"
    )
    adjudication_ref: str = Field(pattern=_ADJUDICATION_REF_PATTERN)
    case_ref: str = Field(pattern=_CASE_REF_PATTERN)
    sample_ref: str | None = Field(default=None, pattern=_SAMPLE_REF_PATTERN)
    human_review_refs: tuple[str, ...] = Field(min_length=1)
    metric_labels: MetricLabels
    bounded_reason: str = Field(min_length=1, max_length=500)
    adjudicated_at: str
    provenance: Literal["human_supplied"]

    @field_validator("human_review_refs")
    @classmethod
    def validate_human_review_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        _validate_unique(value, "human_review_refs")
        if any(
            re.fullmatch(_REVIEW_REF_PATTERN, review_ref) is None
            for review_ref in value
        ):
            raise ValueError("human_review_refs contains an invalid reference")
        return value

    @field_validator("adjudicated_at")
    @classmethod
    def validate_adjudicated_at(cls, value: str) -> str:
        return _canonical_timestamp(value)


class GoldenLabel(_RawSelfHashedModel):
    _self_hash_field = "golden_sha256"
    _integrity_label = "golden"

    schema_version: Literal["8099.deepeval-golden/v1"]
    case_ref: str = Field(pattern=_CASE_REF_PATTERN)
    sample_ref: str | None = Field(pattern=_SAMPLE_REF_PATTERN)
    human_review_refs: tuple[str, ...] = Field(min_length=1)
    human_adjudication_refs: tuple[str, ...]
    metric_labels: MetricLabels
    golden_sha256: str = Field(pattern=_HASH_PATTERN)

    @field_validator("human_review_refs")
    @classmethod
    def validate_golden_review_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        _validate_unique(value, "human_review_refs")
        if any(
            re.fullmatch(_REVIEW_REF_PATTERN, review_ref) is None
            for review_ref in value
        ):
            raise ValueError("human_review_refs contains an invalid reference")
        return value

    @field_validator("human_adjudication_refs")
    @classmethod
    def validate_golden_adjudication_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        _validate_unique(value, "human_adjudication_refs")
        if any(
            re.fullmatch(_ADJUDICATION_REF_PATTERN, ref) is None
            for ref in value
        ):
            raise ValueError(
                "human_adjudication_refs contains an invalid reference"
            )
        return value

    @model_validator(mode="after")
    def validate_golden_hash(self) -> Self:
        payload = self.model_dump(mode="json")
        digest = payload.pop("golden_sha256")
        if canonical_sha256(payload) != digest:
            raise ValueError("golden integrity hash is inconsistent")
        return self


class DatasetValidation(StrictModel):
    schema_version: Literal["8099.deepeval-dataset-validation/v1"] = (
        "8099.deepeval-dataset-validation/v1"
    )
    valid: bool
    error_categories: tuple[IntegrityErrorCategory, ...] = ()
    checked_entry_count: int = Field(ge=0)
    labeling_ready: Literal[False] = False
    release_decision: None = None

    @model_validator(mode="after")
    def validate_integrity_only_semantics(self) -> Self:
        _validate_unique(self.error_categories, "error_categories")
        if self.valid != (not self.error_categories):
            raise ValueError("valid must reflect integrity errors only")
        return self


def _reject_nonfinite_json(_value: str) -> None:
    raise ValueError("non-finite JSON constant")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _supports_function(
    functions: set[Any],
    expected_name: str,
) -> bool:
    return any(
        getattr(function, "__name__", None) == expected_name
        for function in functions
    )


def _require_anchored_io() -> None:
    if (
        os.name != "posix"
        or not getattr(os, "O_NOFOLLOW", 0)
        or not getattr(os, "O_DIRECTORY", 0)
        or not _supports_function(os.supports_dir_fd, "open")
        or not _supports_function(os.supports_dir_fd, "stat")
        or not _supports_function(os.supports_follow_symlinks, "stat")
        or not _supports_function(os.supports_fd, "scandir")
    ):
        raise DatasetError("root_invalid")


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _file_flags() -> int:
    return (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )


def _close_descriptors(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError:
            pass


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _same_stat_state(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        _same_file(first, second)
        and first.st_size == second.st_size
        and first.st_mtime_ns == second.st_mtime_ns
        and first.st_ctime_ns == second.st_ctime_ns
    )


class _AnchoredRead(NamedTuple):
    payload: bytes
    payload_sha256: str
    stat_result: os.stat_result


@contextmanager
def _open_directory_path(path: Path) -> Iterator[int]:
    _require_anchored_io()
    candidate = Path(path)
    try:
        absolute = Path(os.path.abspath(os.fspath(candidate)))
        components = absolute.parts
    except (OSError, TypeError, ValueError):
        raise DatasetError("root_invalid") from None
    if not components or components[0] != os.path.sep:
        raise DatasetError("root_invalid")

    descriptors: list[int] = []
    try:
        anchor = os.open(os.path.sep, _directory_flags())
        descriptors.append(anchor)
        opened = os.fstat(anchor)
        if not stat.S_ISDIR(opened.st_mode):
            raise DatasetError("root_invalid")

        current = anchor
        for component in components[1:]:
            if component in {"", ".", ".."}:
                raise DatasetError("root_invalid")
            before = os.stat(
                component,
                dir_fd=current,
                follow_symlinks=False,
            )
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise DatasetError("root_invalid")
            child = os.open(
                component,
                _directory_flags(),
                dir_fd=current,
            )
            descriptors.append(child)
            after = os.fstat(child)
            if not stat.S_ISDIR(after.st_mode) or not _same_stat_state(
                before,
                after,
            ):
                raise DatasetError("root_invalid")
            current = child
        yield current
    except DatasetError:
        raise
    except (OSError, TypeError, ValueError):
        raise DatasetError("root_invalid") from None
    finally:
        _close_descriptors(descriptors)


def _validated_relative_parts(relative_path: str) -> tuple[str, ...]:
    try:
        validated = DatasetEntry.validate_relative_path(relative_path)
    except (TypeError, ValueError):
        raise DatasetError("path_invalid") from None
    parts = PurePosixPath(validated).parts
    if not parts:
        raise DatasetError("path_invalid")
    return parts


@contextmanager
def _open_relative_parent(
    root_descriptor: int,
    relative_path: str,
) -> Iterator[tuple[int, str]]:
    _require_anchored_io()
    parts = _validated_relative_parts(relative_path)
    descriptors: list[int] = []
    current = root_descriptor
    try:
        for component in parts[:-1]:
            before = os.stat(
                component,
                dir_fd=current,
                follow_symlinks=False,
            )
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise DatasetError("path_invalid")
            child = os.open(
                component,
                _directory_flags(),
                dir_fd=current,
            )
            descriptors.append(child)
            after = os.fstat(child)
            if not stat.S_ISDIR(after.st_mode) or not _same_stat_state(
                before,
                after,
            ):
                raise DatasetError("path_invalid")
            current = child
        yield current, parts[-1]
    except DatasetError:
        raise
    except (OSError, TypeError, ValueError):
        raise DatasetError("path_invalid") from None
    finally:
        _close_descriptors(descriptors)


def _read_regular_at(
    parent_descriptor: int,
    name: str,
    *,
    max_bytes: int,
) -> _AnchoredRead:
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes <= 0
    ):
        raise DatasetError("file_size")
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\x00" in name
    ):
        raise DatasetError("path_invalid")
    try:
        before = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        raise DatasetError("file_unavailable") from None
    except (OSError, TypeError, ValueError):
        raise DatasetError("file_changed") from None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise DatasetError("file_type")
    if before.st_size <= 0 or before.st_size > max_bytes:
        raise DatasetError("file_size")

    descriptor: int | None = None
    try:
        descriptor = os.open(
            name,
            _file_flags(),
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_stat_state(
            before,
            opened,
        ):
            raise DatasetError("file_changed")
        if opened.st_size <= 0 or opened.st_size > max_bytes:
            raise DatasetError("file_size")
        chunks: list[bytes] = []
        total = 0
        while total <= max_bytes:
            chunk = os.read(
                descriptor,
                min(64 * 1024, max_bytes + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        payload = b"".join(chunks)
        after_open = os.fstat(descriptor)
    except DatasetError:
        raise
    except (OSError, TypeError, ValueError):
        raise DatasetError("file_changed") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

    if (
        len(payload) != opened.st_size
        or len(payload) > max_bytes
        or not _same_stat_state(opened, after_open)
    ):
        raise DatasetError("file_changed")
    return _AnchoredRead(
        payload=payload,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        stat_result=after_open,
    )


def _read_path_bytes(path: Path, *, max_bytes: int) -> bytes:
    candidate = Path(path)
    if not candidate.name:
        raise DatasetError("path_invalid")
    with _open_directory_path(candidate.parent) as parent_descriptor:
        return _read_regular_at(
            parent_descriptor,
            candidate.name,
            max_bytes=max_bytes,
        ).payload


def _decode_json_object(payload: bytes) -> dict[str, Any]:
    try:
        decoded = payload.decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            parse_constant=_reject_nonfinite_json,
            object_pairs_hook=_unique_json_object,
        )
    except (
        UnicodeError,
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        raise DatasetError("json_invalid") from None
    if not isinstance(value, dict):
        raise DatasetError("json_invalid")
    return value


def _parse_manifest_payload(payload: bytes) -> DatasetManifest:
    value = _decode_json_object(payload)
    try:
        return DatasetManifest.model_validate(value)
    except ValidationError:
        raise DatasetError("manifest_invalid") from None


def _load_manifest_at(
    root_descriptor: int,
) -> tuple[DatasetManifest, str]:
    anchored_read = _read_regular_at(
        root_descriptor,
        "manifest.json",
        max_bytes=MAX_DATASET_JSON_BYTES,
    )
    return (
        _parse_manifest_payload(anchored_read.payload),
        anchored_read.payload_sha256,
    )


def load_dataset_manifest(path: Path) -> DatasetManifest:
    payload = _read_path_bytes(Path(path), max_bytes=MAX_DATASET_JSON_BYTES)
    return _parse_manifest_payload(payload)


def _parse_case_payload(payload: bytes, entry: DatasetEntry) -> DatasetCase:
    value = _decode_json_object(payload)
    try:
        _walk_raw_case_boundary(value)
    except (BoundaryViolation, TypeError, ValueError):
        raise DatasetError("case_invalid") from None
    if hashlib.sha256(payload).hexdigest() != entry.file_sha256:
        raise DatasetError("file_hash_mismatch")
    try:
        case = DatasetCase.model_validate(value)
    except ValidationError:
        raise DatasetError("case_invalid") from None
    if (
        case.case_ref != entry.case_ref
        or case.source_group_ref != entry.source_group_ref
        or case.split != entry.split
        or case.risk_tier != entry.risk_tier
    ):
        raise DatasetError("entry_metadata_mismatch")
    return case


def _load_dataset_case_at(
    root_descriptor: int,
    entry: DatasetEntry,
) -> tuple[DatasetCase, str]:
    with _open_relative_parent(
        root_descriptor,
        entry.relative_path,
    ) as (parent_descriptor, name):
        anchored_read = _read_regular_at(
            parent_descriptor,
            name,
            max_bytes=MAX_DATASET_JSON_BYTES,
        )
    return (
        _parse_case_payload(anchored_read.payload, entry),
        anchored_read.payload_sha256,
    )


def load_dataset_case(root: Path, entry: DatasetEntry) -> DatasetCase:
    if not isinstance(entry, DatasetEntry):
        raise DatasetError("path_invalid")
    with _open_directory_path(Path(root)) as root_descriptor:
        case, _payload_sha256 = _load_dataset_case_at(root_descriptor, entry)
        return case


def iter_jsonl(
    path: Path,
    model_type: type[StrictModel],
) -> Iterator[StrictModel]:
    if (
        not isinstance(model_type, type)
        or not issubclass(model_type, StrictModel)
    ):
        raise DatasetError("jsonl_model_invalid")
    payload = _read_path_bytes(Path(path), max_bytes=MAX_DATASET_JSONL_BYTES)
    try:
        decoded = payload.decode("utf-8", errors="strict")
    except UnicodeError:
        raise DatasetError("json_invalid") from None
    lines = decoded.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        raise DatasetError("jsonl_invalid")
    for raw_line in lines:
        line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
        if not line or len(line.encode("utf-8")) > MAX_DATASET_JSONL_LINE_BYTES:
            raise DatasetError("jsonl_invalid")
        try:
            value = json.loads(
                line,
                parse_constant=_reject_nonfinite_json,
                object_pairs_hook=_unique_json_object,
            )
        except (
            json.JSONDecodeError,
            RecursionError,
            TypeError,
            ValueError,
        ):
            raise DatasetError("jsonl_invalid") from None
        if not isinstance(value, dict):
            raise DatasetError("jsonl_invalid")
        try:
            yield model_type.model_validate(value)
        except ValidationError:
            raise DatasetError("jsonl_model_invalid") from None


TreeSnapshotEntry = tuple[str, int, int, int, int, int, int, str | None]


def _expected_tree_members(
    manifest: DatasetManifest,
) -> tuple[set[str], set[str]]:
    expected_files = {"manifest.json"}
    expected_directories: set[str] = set()
    for entry in manifest.entries:
        parts = _validated_relative_parts(entry.relative_path)
        expected_files.add("/".join(parts))
        for length in range(1, len(parts)):
            expected_directories.add("/".join(parts[:length]))
    return expected_files, expected_directories


def _inventory_directory(
    directory_descriptor: int,
    *,
    prefix: tuple[str, ...],
    expected_files: set[str],
    expected_directories: set[str],
    categories: set[IntegrityErrorCategory],
    snapshot: list[TreeSnapshotEntry],
    member_count: list[int],
) -> None:
    try:
        with os.scandir(directory_descriptor) as iterator:
            for entry in iterator:
                member_count[0] += 1
                if member_count[0] > MAX_DATASET_TREE_MEMBERS:
                    categories.add("tree_limit")
                    return
                name = entry.name
                if (
                    not isinstance(name, str)
                    or name in {"", ".", ".."}
                    or "/" in name
                ):
                    categories.add("tree_unexpected")
                    continue
                relative_parts = (*prefix, name)
                relative_path = "/".join(relative_parts)
                if len(relative_parts) > MAX_DATASET_TREE_DEPTH:
                    categories.add("tree_limit")
                    continue
                try:
                    before = os.stat(
                        name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                except (OSError, TypeError, ValueError):
                    categories.add("tree_unexpected")
                    continue

                file_type = stat.S_IFMT(before.st_mode)
                if stat.S_ISLNK(before.st_mode):
                    snapshot.append(
                        (
                            relative_path,
                            file_type,
                            before.st_dev,
                            before.st_ino,
                            before.st_size,
                            before.st_mtime_ns,
                            before.st_ctime_ns,
                            None,
                        )
                    )
                    categories.add("tree_unexpected")
                    continue
                if stat.S_ISREG(before.st_mode):
                    payload_sha256: str | None = None
                    snapshot_stat = before
                    if relative_path in expected_files:
                        try:
                            anchored_read = _read_regular_at(
                                directory_descriptor,
                                name,
                                max_bytes=MAX_DATASET_JSON_BYTES,
                            )
                        except DatasetError as exc:
                            categories.add(exc.category)
                        else:
                            payload_sha256 = anchored_read.payload_sha256
                            snapshot_stat = anchored_read.stat_result
                            if not _same_stat_state(
                                before,
                                snapshot_stat,
                            ):
                                categories.add("file_changed")
                    else:
                        categories.add("tree_unexpected")
                    snapshot.append(
                        (
                            relative_path,
                            file_type,
                            snapshot_stat.st_dev,
                            snapshot_stat.st_ino,
                            snapshot_stat.st_size,
                            snapshot_stat.st_mtime_ns,
                            snapshot_stat.st_ctime_ns,
                            payload_sha256,
                        )
                    )
                    if (
                        before.st_size <= 0
                        or before.st_size > MAX_DATASET_JSON_BYTES
                    ):
                        categories.add("file_size")
                    continue
                if not stat.S_ISDIR(before.st_mode):
                    snapshot.append(
                        (
                            relative_path,
                            file_type,
                            before.st_dev,
                            before.st_ino,
                            before.st_size,
                            before.st_mtime_ns,
                            before.st_ctime_ns,
                            None,
                        )
                    )
                    categories.add("tree_unexpected")
                    continue

                snapshot.append(
                    (
                        relative_path,
                        file_type,
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                        before.st_ctime_ns,
                        None,
                    )
                )
                if relative_path not in expected_directories:
                    categories.add("tree_unexpected")
                    continue
                child_descriptor: int | None = None
                try:
                    child_descriptor = os.open(
                        name,
                        _directory_flags(),
                        dir_fd=directory_descriptor,
                    )
                    opened = os.fstat(child_descriptor)
                    if not stat.S_ISDIR(opened.st_mode) or not _same_stat_state(
                        before,
                        opened,
                    ):
                        categories.add("tree_unexpected")
                        continue
                    _inventory_directory(
                        child_descriptor,
                        prefix=relative_parts,
                        expected_files=expected_files,
                        expected_directories=expected_directories,
                        categories=categories,
                        snapshot=snapshot,
                        member_count=member_count,
                    )
                    if "tree_limit" in categories:
                        return
                except (OSError, TypeError, ValueError):
                    categories.add("tree_unexpected")
                finally:
                    if child_descriptor is not None:
                        try:
                            os.close(child_descriptor)
                        except OSError:
                            pass
    except (OSError, TypeError, ValueError):
        categories.add("tree_unexpected")


def _inventory_tree(
    root_descriptor: int,
    manifest: DatasetManifest,
) -> tuple[set[IntegrityErrorCategory], tuple[TreeSnapshotEntry, ...]]:
    expected_files, expected_directories = _expected_tree_members(manifest)
    categories: set[IntegrityErrorCategory] = set()
    snapshot: list[TreeSnapshotEntry] = []
    if (
        len(expected_files) + len(expected_directories)
        > MAX_DATASET_TREE_MEMBERS
    ):
        categories.add("tree_limit")
    else:
        try:
            _inventory_directory(
                root_descriptor,
                prefix=(),
                expected_files=expected_files,
                expected_directories=expected_directories,
                categories=categories,
                snapshot=snapshot,
                member_count=[0],
            )
        except RecursionError:
            categories.add("tree_limit")
    observed_files = {
        path
        for path, file_type, *_rest in snapshot
        if file_type == stat.S_IFREG
    }
    observed_directories = {
        path
        for path, file_type, *_rest in snapshot
        if file_type == stat.S_IFDIR
    }
    if (
        not expected_files.issubset(observed_files)
        or not expected_directories.issubset(observed_directories)
    ):
        categories.add("tree_unexpected")
    return categories, tuple(sorted(snapshot, key=lambda item: item[0]))


def _snapshot_file_digests(
    snapshot: tuple[TreeSnapshotEntry, ...],
) -> dict[str, str]:
    return {
        path: payload_sha256
        for path, file_type, *_metadata, payload_sha256 in snapshot
        if file_type == stat.S_IFREG and payload_sha256 is not None
    }


def validate_dataset_tree(root: Path) -> DatasetValidation:
    categories: set[IntegrityErrorCategory] = set()
    checked_entry_count = 0
    try:
        with _open_directory_path(Path(root)) as root_descriptor:
            manifest, manifest_payload_sha256 = _load_manifest_at(
                root_descriptor
            )
            inventory_categories, before_snapshot = _inventory_tree(
                root_descriptor,
                manifest,
            )
            categories.update(inventory_categories)
            before_digests = _snapshot_file_digests(before_snapshot)
            if (
                before_digests.get("manifest.json")
                != manifest_payload_sha256
            ):
                categories.add("file_changed")
            for entry in manifest.entries:
                try:
                    _case, case_payload_sha256 = _load_dataset_case_at(
                        root_descriptor,
                        entry,
                    )
                except DatasetError as exc:
                    categories.add(exc.category)
                else:
                    checked_entry_count += 1
                    if (
                        before_digests.get(entry.relative_path)
                        != case_payload_sha256
                    ):
                        categories.add("file_changed")
            if "tree_limit" not in categories:
                inventory_categories, after_snapshot = _inventory_tree(
                    root_descriptor,
                    manifest,
                )
                categories.update(inventory_categories)
                if (
                    "tree_limit" not in inventory_categories
                    and before_snapshot != after_snapshot
                ):
                    categories.add("file_changed")
    except DatasetError as exc:
        categories.add(exc.category)
    except RecursionError:
        categories.add("tree_limit")

    ordered = tuple(sorted(categories, key=_ERROR_ORDER.__getitem__))
    return DatasetValidation(
        valid=not ordered,
        error_categories=ordered,
        checked_entry_count=checked_entry_count,
    )


class _Fixed10Tree(NamedTuple):
    manifest: bytes
    cases: dict[str, bytes]


def _freeze_absolute_path(path: Path | os.PathLike[str] | str) -> Path:
    try:
        raw_path = os.fspath(path)
        if (
            not isinstance(raw_path, str)
            or not raw_path
            or "\x00" in raw_path
            or any(part in {".", ".."} for part in Path(raw_path).parts)
        ):
            raise ValueError
        absolute = Path(os.path.abspath(raw_path))
    except (OSError, TypeError, ValueError):
        raise Fixed10FreezeError("path_invalid") from None
    if not absolute.is_absolute():
        raise Fixed10FreezeError("path_invalid")
    return absolute


def _require_freeze_io() -> None:
    try:
        _require_anchored_io()
    except DatasetError:
        raise Fixed10FreezeError("platform_unsupported") from None
    required_dir_fd_functions = ("mkdir", "rmdir", "unlink")
    if any(
        not _supports_function(os.supports_dir_fd, name)
        for name in required_dir_fd_functions
    ):
        raise Fixed10FreezeError("platform_unsupported")


@contextmanager
def _open_or_create_directory_path(path: Path) -> Iterator[int]:
    _require_freeze_io()
    absolute = _freeze_absolute_path(path)
    components = absolute.parts
    if not components or components[0] != os.path.sep:
        raise Fixed10FreezeError("path_invalid")

    descriptors: list[int] = []
    created: list[tuple[int, str, int, int]] = []
    succeeded = False
    try:
        anchor = os.open(os.path.sep, _directory_flags())
        descriptors.append(anchor)
        current = anchor
        for component in components[1:]:
            if component in {"", ".", ".."}:
                raise Fixed10FreezeError("path_invalid")
            made_directory = False
            try:
                before = os.stat(
                    component,
                    dir_fd=current,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=current)
                    os.fsync(current)
                    made_directory = True
                except FileExistsError:
                    pass
                before = os.stat(
                    component,
                    dir_fd=current,
                    follow_symlinks=False,
                )
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise Fixed10FreezeError("path_invalid")
            child = os.open(
                component,
                _directory_flags(),
                dir_fd=current,
            )
            descriptors.append(child)
            opened = os.fstat(child)
            if not stat.S_ISDIR(opened.st_mode) or not _same_stat_state(
                before,
                opened,
            ):
                raise Fixed10FreezeError("path_invalid")
            if made_directory:
                created.append(
                    (current, component, opened.st_dev, opened.st_ino)
                )
            current = child
        yield current
        succeeded = True
    except Fixed10FreezeError:
        raise
    except (OSError, TypeError, ValueError):
        raise Fixed10FreezeError("path_invalid") from None
    finally:
        if not succeeded:
            for parent_descriptor, name, device, inode in reversed(created):
                try:
                    observed = os.stat(
                        name,
                        dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                    if (
                        stat.S_ISDIR(observed.st_mode)
                        and observed.st_dev == device
                        and observed.st_ino == inode
                    ):
                        os.rmdir(name, dir_fd=parent_descriptor)
                        os.fsync(parent_descriptor)
                except OSError:
                    pass
        _close_descriptors(descriptors)


def _directory_names_at(
    directory_descriptor: int,
    *,
    maximum: int,
) -> tuple[str, ...]:
    names: list[str] = []
    try:
        with os.scandir(directory_descriptor) as iterator:
            for entry in iterator:
                if not isinstance(entry.name, str):
                    raise Fixed10FreezeError("tree_invalid")
                names.append(entry.name)
                if len(names) > maximum:
                    raise Fixed10FreezeError("tree_invalid")
    except Fixed10FreezeError:
        raise
    except (OSError, TypeError, ValueError):
        raise Fixed10FreezeError("tree_invalid") from None
    return tuple(sorted(names))


def _read_fixed10_source_exports(
    source_dir: Path,
) -> tuple[Fixed10SourceExport, ...]:
    try:
        with _open_directory_path(source_dir) as source_descriptor:
            names = _directory_names_at(source_descriptor, maximum=10)
            if len(names) != 10:
                raise Fixed10FreezeError("source_count")

            exports: list[Fixed10SourceExport] = []
            first_reads: dict[str, _AnchoredRead] = {}
            for name in names:
                if (
                    len(name) > 132
                    or unicodedata.normalize("NFC", name) != name
                    or _FIXED10_SOURCE_NAME.fullmatch(name) is None
                ):
                    raise Fixed10FreezeError("source_name")
                anchored = _read_regular_at(
                    source_descriptor,
                    name,
                    max_bytes=MAX_DATASET_JSON_BYTES,
                )
                value = _decode_json_object(anchored.payload)
                if canonical_json_bytes(value) != anchored.payload:
                    raise Fixed10FreezeError("source_noncanonical")
                try:
                    export = Fixed10SourceExport.model_validate(value)
                except ValidationError:
                    raise Fixed10FreezeError("source_invalid") from None
                first_reads[name] = anchored
                exports.append(export)

            if _directory_names_at(source_descriptor, maximum=10) != names:
                raise Fixed10FreezeError("source_changed")
            for name in names:
                repeated = _read_regular_at(
                    source_descriptor,
                    name,
                    max_bytes=MAX_DATASET_JSON_BYTES,
                )
                initial = first_reads[name]
                if (
                    repeated.payload != initial.payload
                    or not _same_stat_state(
                        repeated.stat_result,
                        initial.stat_result,
                    )
                ):
                    raise Fixed10FreezeError("source_changed")
    except Fixed10FreezeError:
        raise
    except DatasetError:
        raise Fixed10FreezeError("source_invalid") from None

    case_refs = tuple(export.case_ref for export in exports)
    material_hashes = tuple(
        export.material_identity_sha256 for export in exports
    )
    content_hashes = tuple(
        export.source_content_sha256 for export in exports
    )
    if (
        len(set(case_refs)) != 10
        or len(set(material_hashes)) != 10
        or len(set(content_hashes)) != 10
    ):
        raise Fixed10FreezeError("source_duplicate")
    ranks = sorted(
        export.fixed3_rank
        for export in exports
        if export.fixed3_rank is not None
    )
    if ranks != [1, 2, 3]:
        raise Fixed10FreezeError("fixed3_invalid")
    return tuple(sorted(exports, key=lambda export: export.case_ref))


def _build_fixed10_tree(
    exports: tuple[Fixed10SourceExport, ...],
) -> _Fixed10Tree:
    cases: dict[str, bytes] = {}
    entries: list[dict[str, Any]] = []
    ranks: dict[int, str] = {}
    for export in exports:
        case_payload: dict[str, Any] = {
            "schema_version": "8099.deepeval-case/v1",
            "case_ref": export.case_ref,
            "source_group_ref": export.source_group_ref,
            "split": "fixed",
            "risk_tier": export.risk_tier,
            "risk_tags": list(export.risk_tags),
            "material_identity_sha256": export.material_identity_sha256,
            "source_content_sha256": export.source_content_sha256,
            "projection": export.projection.model_dump(mode="json"),
        }
        case_payload["case_sha256"] = canonical_sha256(case_payload)
        try:
            case = DatasetCase.model_validate(case_payload)
        except ValidationError:
            raise Fixed10FreezeError("case_invalid") from None
        case_bytes = canonical_json_bytes(case.model_dump(mode="json"))
        case_name = f"{case.case_ref}.json"
        cases[case_name] = case_bytes
        entries.append(
            {
                "case_ref": case.case_ref,
                "relative_path": f"cases/{case_name}",
                "file_sha256": hashlib.sha256(case_bytes).hexdigest(),
                "source_group_ref": case.source_group_ref,
                "split": "fixed",
                "risk_tier": case.risk_tier,
            }
        )
        if export.fixed3_rank is not None:
            ranks[export.fixed3_rank] = export.case_ref

    ordered_refs = [entry["case_ref"] for entry in entries]
    manifest_payload: dict[str, Any] = {
        "schema_version": "8099.deepeval-dataset-manifest/v1",
        "dataset_id": "fixed10",
        "dataset_version": "fixed10/v1",
        "projection_version": "claim-ab-v1",
        "rubric_version": FIXED10_RUBRIC_VERSION,
        "entries": entries,
        "subsets": {
            "fixed10": ordered_refs,
            "fixed3": [ranks[rank] for rank in (1, 2, 3)],
        },
        "declared_count": 10,
        "runnable_count": 10,
        "exclusion_count": 0,
    }
    manifest_payload["manifest_sha256"] = canonical_sha256(manifest_payload)
    try:
        manifest = DatasetManifest.model_validate(manifest_payload)
    except (KeyError, ValidationError):
        raise Fixed10FreezeError("manifest_invalid") from None
    manifest_bytes = canonical_json_bytes(manifest.model_dump(mode="json"))
    return _Fixed10Tree(manifest=manifest_bytes, cases=cases)


@contextmanager
def _fixed10_lock(
    parent_descriptor: int,
    lock_name: str,
    *,
    timeout_seconds: float,
) -> Iterator[None]:
    try:
        import fcntl
    except ImportError:
        raise Fixed10FreezeError("platform_unsupported") from None

    deadline = time.monotonic() + timeout_seconds
    descriptor: int | None = None
    locked = False
    try:
        flags = (
            os.O_RDWR
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0)
        )
        created = False
        try:
            descriptor = os.open(
                lock_name,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent_descriptor,
            )
            created = True
        except FileExistsError:
            before = os.stat(
                lock_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            descriptor = os.open(
                lock_name,
                flags,
                dir_fd=parent_descriptor,
            )
            opened = os.fstat(descriptor)
            if not _same_stat_state(before, opened):
                raise Fixed10FreezeError("lock_invalid")

        opened = os.fstat(descriptor)
        observed = os.stat(
            lock_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or not _same_stat_state(opened, observed)
            or opened.st_nlink != 1
            or opened.st_size != 0
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) & 0o077
        ):
            raise Fixed10FreezeError("lock_invalid")
        if created:
            os.fsync(descriptor)
            os.fsync(parent_descriptor)

        while True:
            try:
                fcntl.flock(
                    descriptor,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
                locked = True
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                if time.monotonic() >= deadline:
                    raise Fixed10FreezeError("lock_timeout") from None
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        yield
    except Fixed10FreezeError:
        raise
    except (OSError, TypeError, ValueError):
        raise Fixed10FreezeError("lock_failed") from None
    finally:
        if descriptor is not None:
            try:
                if locked:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            except OSError:
                pass


def _write_exclusive_at(
    parent_descriptor: int,
    name: str,
    payload: bytes,
) -> None:
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\x00" in name
        or not isinstance(payload, bytes)
        or not payload
    ):
        raise OSError(errno.EINVAL, "invalid exclusive write")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short exclusive write")
            offset += written
        os.fsync(descriptor)
        opened = os.fstat(descriptor)
        observed = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size != len(payload)
            or not _same_stat_state(opened, observed)
        ):
            raise OSError(errno.EIO, "exclusive write changed")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _open_child_directory_at(
    parent_descriptor: int,
    name: str,
) -> tuple[int, os.stat_result]:
    before = os.stat(
        name,
        dir_fd=parent_descriptor,
        follow_symlinks=False,
    )
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise Fixed10FreezeError("tree_invalid")
    descriptor = os.open(
        name,
        _directory_flags(),
        dir_fd=parent_descriptor,
    )
    opened = os.fstat(descriptor)
    if not stat.S_ISDIR(opened.st_mode) or not _same_stat_state(
        before,
        opened,
    ):
        os.close(descriptor)
        raise Fixed10FreezeError("tree_invalid")
    return descriptor, opened


def _tree_matches_at(
    root_descriptor: int,
    expected: _Fixed10Tree,
) -> bool:
    cases_descriptor: int | None = None
    try:
        root_before = os.fstat(root_descriptor)
        expected_root_names = (
            "cases",
            "manifest.json",
        )
        if (
            _directory_names_at(root_descriptor, maximum=2)
            != expected_root_names
        ):
            return False
        manifest = _read_regular_at(
            root_descriptor,
            "manifest.json",
            max_bytes=MAX_DATASET_JSON_BYTES,
        )
        if manifest.payload != expected.manifest:
            return False
        cases_descriptor, _cases_stat = _open_child_directory_at(
            root_descriptor,
            "cases",
        )
        expected_names = tuple(sorted(expected.cases))
        if _directory_names_at(
            cases_descriptor,
            maximum=len(expected_names),
        ) != expected_names:
            return False
        first_case_reads: dict[str, _AnchoredRead] = {}
        for name in expected_names:
            observed = _read_regular_at(
                cases_descriptor,
                name,
                max_bytes=MAX_DATASET_JSON_BYTES,
            )
            if observed.payload != expected.cases[name]:
                return False
            first_case_reads[name] = observed
        if _directory_names_at(
            cases_descriptor,
            maximum=len(expected_names),
        ) != expected_names:
            return False
        for name in expected_names:
            repeated = _read_regular_at(
                cases_descriptor,
                name,
                max_bytes=MAX_DATASET_JSON_BYTES,
            )
            initial = first_case_reads[name]
            if (
                repeated.payload != initial.payload
                or not _same_stat_state(
                    repeated.stat_result,
                    initial.stat_result,
                )
            ):
                return False
        cases_after = os.fstat(cases_descriptor)
        if not _same_stat_state(_cases_stat, cases_after):
            return False
        repeated_manifest = _read_regular_at(
            root_descriptor,
            "manifest.json",
            max_bytes=MAX_DATASET_JSON_BYTES,
        )
        if (
            repeated_manifest.payload != manifest.payload
            or not _same_stat_state(
                repeated_manifest.stat_result,
                manifest.stat_result,
            )
        ):
            return False
        if (
            _directory_names_at(root_descriptor, maximum=2)
            != expected_root_names
        ):
            return False
        return _same_stat_state(root_before, os.fstat(root_descriptor))
    except (DatasetError, Fixed10FreezeError, OSError, TypeError, ValueError):
        return False
    finally:
        if cases_descriptor is not None:
            try:
                os.close(cases_descriptor)
            except OSError:
                pass


def _existing_fixed10_status(
    parent_descriptor: int,
    output_name: str,
    expected: _Fixed10Tree,
) -> str | None:
    try:
        before = os.stat(
            output_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None
    except (OSError, TypeError, ValueError):
        raise Fixed10FreezeError("output_invalid") from None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise Fixed10FreezeError("output_invalid")

    output_descriptor: int | None = None
    try:
        output_descriptor = os.open(
            output_name,
            _directory_flags(),
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(output_descriptor)
        if not stat.S_ISDIR(opened.st_mode) or not _same_stat_state(
            before,
            opened,
        ):
            raise Fixed10FreezeError("output_invalid")
        if not _tree_matches_at(output_descriptor, expected):
            raise Fixed10FreezeError("output_conflict")
        after = os.stat(
            output_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if not _same_stat_state(opened, after):
            raise Fixed10FreezeError("output_changed")
        return "unchanged"
    except Fixed10FreezeError:
        raise
    except (OSError, TypeError, ValueError):
        raise Fixed10FreezeError("output_invalid") from None
    finally:
        if output_descriptor is not None:
            try:
                os.close(output_descriptor)
            except OSError:
                pass

def _cleanup_owned_stage_at(
    parent_descriptor: int,
    stage_name: str,
    stage_identity: os.stat_result | None,
    case_names: tuple[str, ...],
) -> None:
    if stage_identity is None:
        return
    stage_descriptor: int | None = None
    cases_descriptor: int | None = None
    try:
        observed = os.stat(
            stage_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(observed.st_mode)
            or not _same_file(observed, stage_identity)
        ):
            return
        stage_descriptor = os.open(
            stage_name,
            _directory_flags(),
            dir_fd=parent_descriptor,
        )
        if not _same_file(os.fstat(stage_descriptor), stage_identity):
            return
        root_names = _directory_names_at(stage_descriptor, maximum=2)
        if any(name not in {"cases", "manifest.json"} for name in root_names):
            return

        if "cases" in root_names:
            cases_descriptor, _cases_identity = _open_child_directory_at(
                stage_descriptor,
                "cases",
            )
            observed_case_names = _directory_names_at(
                cases_descriptor,
                maximum=len(case_names),
            )
            if any(name not in case_names for name in observed_case_names):
                return
            for name in observed_case_names:
                os.unlink(name, dir_fd=cases_descriptor)
            os.fsync(cases_descriptor)
            os.close(cases_descriptor)
            cases_descriptor = None
            os.rmdir("cases", dir_fd=stage_descriptor)

        if "manifest.json" in root_names:
            os.unlink("manifest.json", dir_fd=stage_descriptor)
        os.fsync(stage_descriptor)
        os.close(stage_descriptor)
        stage_descriptor = None
        os.rmdir(stage_name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except (
        DatasetError,
        Fixed10FreezeError,
        FileNotFoundError,
        OSError,
        TypeError,
        ValueError,
    ):
        pass
    finally:
        for descriptor in (cases_descriptor, stage_descriptor):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _rename_noreplace_at(
    parent_descriptor: int,
    source_name: str,
    destination_name: str,
) -> None:
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = renameat2(
            parent_descriptor,
            os.fsencode(source_name),
            parent_descriptor,
            os.fsencode(destination_name),
            1,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        raise Fixed10FreezeError("platform_unsupported") from None
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise Fixed10FreezeError("output_conflict")
    if error_number in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
        raise Fixed10FreezeError("platform_unsupported")
    raise OSError(error_number, "atomic no-replace publication failed")


def _publish_fixed10_tree(
    parent_descriptor: int,
    output_name: str,
    expected: _Fixed10Tree,
) -> str:
    stage_name = ""
    stage_identity: os.stat_result | None = None
    stage_descriptor: int | None = None
    cases_descriptor: int | None = None
    published = False
    case_names = tuple(sorted(expected.cases))
    try:
        for attempt in range(32):
            candidate = (
                f".{output_name}.freeze-stage-{os.getpid()}-{attempt}"
            )
            try:
                os.mkdir(
                    candidate,
                    mode=0o700,
                    dir_fd=parent_descriptor,
                )
            except FileExistsError:
                continue
            stage_name = candidate
            stage_identity = os.stat(
                stage_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            os.fsync(parent_descriptor)
            break
        if not stage_name or stage_identity is None:
            raise Fixed10FreezeError("staging_unavailable")

        before = stage_identity
        stage_descriptor = os.open(
            stage_name,
            _directory_flags(),
            dir_fd=parent_descriptor,
        )
        opened_stage = os.fstat(stage_descriptor)
        if not stat.S_ISDIR(opened_stage.st_mode) or not _same_stat_state(
            before,
            opened_stage,
        ):
            raise Fixed10FreezeError("staging_changed")
        stage_identity = opened_stage

        os.mkdir("cases", mode=0o700, dir_fd=stage_descriptor)
        cases_descriptor, _cases_identity = _open_child_directory_at(
            stage_descriptor,
            "cases",
        )
        for name in case_names:
            _write_exclusive_at(
                cases_descriptor,
                name,
                expected.cases[name],
            )
        os.fsync(cases_descriptor)
        _write_exclusive_at(
            stage_descriptor,
            "manifest.json",
            expected.manifest,
        )
        os.fsync(stage_descriptor)
        if not _tree_matches_at(stage_descriptor, expected):
            raise Fixed10FreezeError("staging_invalid")

        observed = os.stat(
            stage_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if not _same_file(observed, stage_identity):
            raise Fixed10FreezeError("staging_changed")
        os.fsync(parent_descriptor)
        _rename_noreplace_at(
            parent_descriptor,
            stage_name,
            output_name,
        )
        published = True
        published_stat = os.stat(
            output_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if not _same_file(published_stat, stage_identity):
            raise Fixed10FreezeError("output_changed")
        os.fsync(parent_descriptor)
        return "created"
    finally:
        for descriptor in (cases_descriptor, stage_descriptor):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if stage_name and not published:
            _cleanup_owned_stage_at(
                parent_descriptor,
                stage_name,
                stage_identity,
                case_names,
            )


def freeze_fixed10_dataset(
    source_dir: Path,
    output_dir: Path,
    *,
    lock_timeout_seconds: float = 10.0,
) -> str:
    if (
        isinstance(lock_timeout_seconds, bool)
        or not isinstance(lock_timeout_seconds, (int, float))
        or not (0.0 < float(lock_timeout_seconds) <= 300.0)
    ):
        raise Fixed10FreezeError("timeout_invalid")
    timeout = float(lock_timeout_seconds)

    source = _freeze_absolute_path(source_dir)
    output = _freeze_absolute_path(output_dir)
    output_name = output.name
    if (
        not output_name
        or output_name in {".", ".."}
        or len(output_name) > 64
        or _SAFE_PATH_COMPONENT.fullmatch(output_name) is None
    ):
        raise Fixed10FreezeError("output_name")
    try:
        if Path(os.path.commonpath((source, output))) == source:
            raise Fixed10FreezeError("path_overlap")
    except ValueError:
        raise Fixed10FreezeError("path_invalid") from None

    try:
        exports = _read_fixed10_source_exports(source)
        expected = _build_fixed10_tree(exports)
        with _open_or_create_directory_path(output.parent) as parent_descriptor:
            lock_name = f".{output_name}.freeze-lock"
            with _fixed10_lock(
                parent_descriptor,
                lock_name,
                timeout_seconds=timeout,
            ):
                status = _existing_fixed10_status(
                    parent_descriptor,
                    output_name,
                    expected,
                )
                if status is not None:
                    return status
                return _publish_fixed10_tree(
                    parent_descriptor,
                    output_name,
                    expected,
                )
    except Fixed10FreezeError:
        raise
    except (
        DatasetError,
        ValidationError,
        KeyError,
        OSError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        raise Fixed10FreezeError("operation_failed") from None


__all__ = [
    "MAX_DATASET_JSON_BYTES",
    "MAX_DATASET_JSONL_BYTES",
    "MAX_DATASET_JSONL_LINE_BYTES",
    "MAX_DATASET_TREE_DEPTH",
    "MAX_DATASET_MANIFEST_ENTRIES",
    "MAX_DATASET_TREE_MEMBERS",
    "FIXED10_RUBRIC_VERSION",
    "FIXED10_SOURCE_SCHEMA_VERSION",
    "DatasetCase",
    "DatasetEntry",
    "DatasetError",
    "DatasetManifest",
    "DatasetValidation",
    "Fixed10FreezeError",
    "Fixed10SourceExport",
    "GoldenLabel",
    "HumanAdjudication",
    "HumanReview",
    "MetricLabels",
    "Prelabel",
    "freeze_fixed10_dataset",
    "iter_jsonl",
    "load_dataset_case",
    "load_dataset_manifest",
    "validate_dataset_tree",
]
