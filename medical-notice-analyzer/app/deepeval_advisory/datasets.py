from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from app.deepeval_advisory.hashing import (
    BoundaryViolation,
    assert_safe_outbound_text,
    canonical_sha256,
)
from app.deepeval_advisory.models import AdvisoryProjection, StrictModel


MAX_DATASET_JSON_BYTES = 1024 * 1024
MAX_DATASET_JSONL_BYTES = 16 * 1024 * 1024
MAX_DATASET_JSONL_LINE_BYTES = 256 * 1024

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
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_ANY_URL = re.compile(r"\b(?:https?|ftp)://", re.IGNORECASE)
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
        _ANY_URL.search(scanned)
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


def _validate_unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


class MetricLabels(StrictModel):
    claim_faithfulness_v1: MetricObservation
    critical_coverage_v1: MetricObservation
    attachment_state_consistency_v1: MetricObservation
    answer_relevancy_v1: MetricObservation


class DatasetCase(StrictModel):
    schema_version: Literal["8099.deepeval-case/v1"] = (
        "8099.deepeval-case/v1"
    )
    case_ref: str = Field(pattern=_CASE_REF_PATTERN)
    source_group_ref: str = Field(pattern=_SOURCE_GROUP_REF_PATTERN)
    split: DatasetSplit
    risk_tier: Literal["high", "standard"]
    risk_tags: tuple[RiskTag, ...] = Field(default=(), max_length=8)
    material_identity_sha256: str = Field(pattern=_HASH_PATTERN)
    source_content_sha256: str = Field(pattern=_HASH_PATTERN)
    projection: AdvisoryProjection
    case_sha256: str = Field(pattern=_HASH_PATTERN)

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
            or candidate.as_posix() != value
            or candidate.suffix != ".json"
            or any(
                _SAFE_PATH_COMPONENT.fullmatch(part) is None
                for part in candidate.parts
            )
        ):
            raise ValueError("relative_path must be a safe relative path")
        return value


class DatasetManifest(StrictModel):
    schema_version: Literal["8099.deepeval-dataset-manifest/v1"] = (
        "8099.deepeval-dataset-manifest/v1"
    )
    dataset_id: str = Field(pattern=_SAFE_SLUG_PATTERN)
    dataset_version: Literal["fixed10/v1", "calibration100/v1"]
    projection_version: Literal["claim-ab-v1"] = "claim-ab-v1"
    rubric_version: str = Field(pattern=_VERSION_TOKEN_PATTERN)
    entries: tuple[DatasetEntry, ...] = Field(min_length=1)
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


class GoldenLabel(StrictModel):
    schema_version: Literal["8099.deepeval-golden/v1"] = (
        "8099.deepeval-golden/v1"
    )
    case_ref: str = Field(pattern=_CASE_REF_PATTERN)
    sample_ref: str | None = Field(default=None, pattern=_SAMPLE_REF_PATTERN)
    human_review_refs: tuple[str, ...] = Field(min_length=1)
    human_adjudication_refs: tuple[str, ...] = ()
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


def _open_binary_source(path: Path) -> BinaryIO:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        return os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise


def _read_regular_bytes(path: Path, *, max_bytes: int) -> bytes:
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes <= 0
    ):
        raise DatasetError("file_size")
    candidate = Path(path)
    try:
        before = candidate.lstat()
    except (OSError, TypeError, ValueError):
        raise DatasetError("file_unavailable") from None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise DatasetError("file_type")
    if before.st_size <= 0 or before.st_size > max_bytes:
        raise DatasetError("file_size")

    try:
        with _open_binary_source(candidate) as source:
            opened = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or (before.st_dev, before.st_ino)
                != (opened.st_dev, opened.st_ino)
            ):
                raise DatasetError("file_changed")
            if opened.st_size <= 0 or opened.st_size > max_bytes:
                raise DatasetError("file_size")
            payload = source.read(max_bytes + 1)
            after_open = os.fstat(source.fileno())
            after_path = candidate.lstat()
    except DatasetError:
        raise
    except (OSError, TypeError, ValueError):
        raise DatasetError("file_changed") from None

    if (
        len(payload) != opened.st_size
        or len(payload) > max_bytes
        or after_open.st_size != opened.st_size
        or after_open.st_mtime_ns != opened.st_mtime_ns
        or stat.S_ISLNK(after_path.st_mode)
        or not stat.S_ISREG(after_path.st_mode)
        or (after_path.st_dev, after_path.st_ino)
        != (opened.st_dev, opened.st_ino)
        or after_path.st_size != opened.st_size
        or after_path.st_mtime_ns != opened.st_mtime_ns
    ):
        raise DatasetError("file_changed")
    return payload


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


def _require_safe_root(root: Path) -> Path:
    candidate = Path(root)
    try:
        info = candidate.lstat()
    except (OSError, TypeError, ValueError):
        raise DatasetError("root_invalid") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DatasetError("root_invalid")
    return candidate


def _case_path(root: Path, relative_path: str) -> Path:
    try:
        validated = DatasetEntry.validate_relative_path(relative_path)
    except (TypeError, ValueError):
        raise DatasetError("path_invalid") from None
    current = root
    parts = PurePosixPath(validated).parts
    for part in parts[:-1]:
        current = current / part
        try:
            info = current.lstat()
        except (OSError, TypeError, ValueError):
            raise DatasetError("path_invalid") from None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise DatasetError("path_invalid")
    return current / parts[-1]


def load_dataset_manifest(path: Path) -> DatasetManifest:
    payload = _read_regular_bytes(Path(path), max_bytes=MAX_DATASET_JSON_BYTES)
    value = _decode_json_object(payload)
    try:
        return DatasetManifest.model_validate(value)
    except ValidationError:
        raise DatasetError("manifest_invalid") from None


def load_dataset_case(root: Path, entry: DatasetEntry) -> DatasetCase:
    safe_root = _require_safe_root(Path(root))
    if not isinstance(entry, DatasetEntry):
        raise DatasetError("path_invalid")
    path = _case_path(safe_root, entry.relative_path)
    payload = _read_regular_bytes(path, max_bytes=MAX_DATASET_JSON_BYTES)
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


def iter_jsonl(
    path: Path,
    model_type: type[StrictModel],
) -> Iterator[StrictModel]:
    if (
        not isinstance(model_type, type)
        or not issubclass(model_type, StrictModel)
    ):
        raise DatasetError("jsonl_model_invalid")
    payload = _read_regular_bytes(Path(path), max_bytes=MAX_DATASET_JSONL_BYTES)
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


def validate_dataset_tree(root: Path) -> DatasetValidation:
    try:
        safe_root = _require_safe_root(Path(root))
        manifest = load_dataset_manifest(safe_root / "manifest.json")
    except DatasetError as exc:
        return DatasetValidation(
            valid=False,
            error_categories=(exc.category,),
            checked_entry_count=0,
        )

    categories: set[IntegrityErrorCategory] = set()
    checked_entry_count = 0
    for entry in manifest.entries:
        try:
            load_dataset_case(safe_root, entry)
        except DatasetError as exc:
            categories.add(exc.category)
        else:
            checked_entry_count += 1
    ordered = tuple(sorted(categories, key=_ERROR_ORDER.__getitem__))
    return DatasetValidation(
        valid=not ordered,
        error_categories=ordered,
        checked_entry_count=checked_entry_count,
    )


__all__ = [
    "MAX_DATASET_JSON_BYTES",
    "MAX_DATASET_JSONL_BYTES",
    "MAX_DATASET_JSONL_LINE_BYTES",
    "DatasetCase",
    "DatasetEntry",
    "DatasetError",
    "DatasetManifest",
    "DatasetValidation",
    "GoldenLabel",
    "HumanAdjudication",
    "HumanReview",
    "MetricLabels",
    "Prelabel",
    "iter_jsonl",
    "load_dataset_case",
    "load_dataset_manifest",
    "validate_dataset_tree",
]
