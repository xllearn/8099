from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.deepeval_advisory.hashing import canonical_sha256
from app.deepeval_advisory.models import AdvisoryResult, JobStatus
from app.deepeval_advisory.store import (
    StoreError,
    _exclusive_create_json,
    _parse_canonical_timestamp,
    _prepare_private_directory,
    _read_json_object,
    _target_is_absent,
    _validate_evaluation_id,
    _validate_key,
)


_CACHE_FIELDS = {
    "schema_version",
    "advisory_input_sha256",
    "source_evaluation_id",
    "result_sha256",
    "result",
    "created_at",
}


class CacheError(ValueError):
    """Raised when an immutable advisory cache entry is unsafe."""


class AdvisoryCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.cache_directory = self.root / "cache"
        _prepare_private_directory(self.root)
        _prepare_private_directory(self.cache_directory)
        _prepare_private_directory(self.root / "corrupt")

    def _path(self, key: object) -> Path:
        try:
            safe_key = _validate_key(key)
        except StoreError:
            raise CacheError("cache key is invalid") from None
        return self.cache_directory / f"{safe_key}.json"

    def _completed_result(
        self,
        key: str,
        value: AdvisoryResult | Mapping[str, Any],
    ) -> AdvisoryResult:
        try:
            result = AdvisoryResult.model_validate(value)
            _validate_evaluation_id(result.evaluation_id)
            if result.status is not JobStatus.COMPLETED:
                raise CacheError("only completed results can be cached")
            if result.advisory_input_sha256 != key:
                raise CacheError("cache key does not match result")
            return result
        except CacheError:
            raise
        except (StoreError, ValidationError, TypeError, ValueError):
            raise CacheError("completed cache result is invalid") from None

    def _read_envelope(
        self,
        key: str,
    ) -> tuple[dict[str, Any], AdvisoryResult]:
        path = self._path(key)
        try:
            envelope = _read_json_object(path)
            if set(envelope) != _CACHE_FIELDS:
                raise CacheError("cache entry is invalid or unavailable")
            if envelope["schema_version"] != "8099.deepeval-cache/v1":
                raise CacheError("cache entry is invalid or unavailable")
            if envelope["advisory_input_sha256"] != key:
                raise CacheError("cache entry is invalid or unavailable")
            _validate_key(envelope["advisory_input_sha256"])
            source_evaluation_id = _validate_evaluation_id(
                envelope["source_evaluation_id"]
            )
            result_sha256 = _validate_key(envelope["result_sha256"])
            _parse_canonical_timestamp(envelope["created_at"])
            result_value = envelope["result"]
            if not isinstance(result_value, dict):
                raise CacheError("cache entry is invalid or unavailable")
            result = self._completed_result(key, result_value)
            if source_evaluation_id != result.evaluation_id:
                raise CacheError("cache entry is invalid or unavailable")
            if result_sha256 != canonical_sha256(
                result.model_dump(mode="json")
            ):
                raise CacheError("cache entry is invalid or unavailable")
            if envelope["created_at"] != result.created_at:
                raise CacheError("cache entry is invalid or unavailable")
            return envelope, result
        except CacheError:
            raise CacheError("cache entry is invalid or unavailable") from None
        except (
            StoreError,
            ValidationError,
            KeyError,
            TypeError,
            ValueError,
        ):
            raise CacheError("cache entry is invalid or unavailable") from None

    def write_completed(
        self,
        key: str,
        value: AdvisoryResult | Mapping[str, Any],
    ) -> AdvisoryResult:
        try:
            safe_key = _validate_key(key)
        except StoreError:
            raise CacheError("cache key is invalid") from None
        result = self._completed_result(safe_key, value)
        result_value = result.model_dump(mode="json")
        envelope = {
            "schema_version": "8099.deepeval-cache/v1",
            "advisory_input_sha256": safe_key,
            "source_evaluation_id": result.evaluation_id,
            "result_sha256": canonical_sha256(result_value),
            "result": result_value,
            "created_at": result.created_at,
        }
        path = self._path(safe_key)
        try:
            _exclusive_create_json(path, envelope)
        except FileExistsError:
            existing, existing_result = self._read_envelope(safe_key)
            if existing != envelope:
                raise CacheError(
                    "cache entry already exists with different content"
                ) from None
            return existing_result
        except StoreError:
            raise CacheError("cache write failed") from None
        return result

    def read(
        self,
        key: str,
        *,
        cache_mode: str = "default",
    ) -> AdvisoryResult | None:
        if cache_mode == "bypass":
            return None
        if cache_mode != "default":
            raise CacheError("cache mode is invalid")
        path = self._path(key)
        try:
            if _target_is_absent(path):
                return None
            _envelope, result = self._read_envelope(
                _validate_key(key)
            )
            return result
        except CacheError:
            raise
        except StoreError:
            raise CacheError("cache entry is invalid or unavailable") from None
