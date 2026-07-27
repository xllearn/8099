from __future__ import annotations

import json
import os
import re
import stat
import unicodedata
from pathlib import Path
from typing import Any, BinaryIO

from pydantic import ValidationError

from app.deepeval_advisory.hashing import (
    BoundaryViolation,
    assert_safe_outbound_text,
    canonical_json_bytes,
    canonical_sha256,
    hmac_reference,
)
from app.deepeval_advisory.models import (
    AdvisoryProjection,
    EvidenceExcerpt,
    ProjectionUnit,
    RunSnapshot,
    SafeLocator,
)
from app.diagnostics import TECHNICAL_BODY_PHRASES
from app.evidence_index import EvidenceIndexError, build_claim_evidence_index
from app.evidence_schema import EvidenceValidationError, read_evidence_pack
from app.formal_body import FormalBodyDocument
from app.schema_migrations import (
    SchemaMigrationError,
    upgrade_report_ir_schema,
    upgrade_run_schema,
)


MAX_CLAIM_TEXT_CHARS = 6_000
MAX_UNIQUE_EVIDENCE = 8
MAX_EVIDENCE_EXCERPT_CHARS = 8_000
MAX_PROJECTION_JSON_BYTES = 64 * 1024

_ELIGIBLE_STATUSES = {"finished", "needs_manual_review"}
_NONTERMINAL_STATUSES = {
    "created",
    "preparing",
    "running",
    "generating",
    "generated",
    "local_quality_checking",
    "repairing",
    "fallback_generating",
    "export_checking",
}
_TECHNICAL_MARKDOWN = re.compile(r"[#>*_`|~\-\s]+")


class ProjectionError(ValueError):
    """Raised when a safe, bounded advisory projection cannot be built."""


def _reject_nonfinite_json(_value: str) -> None:
    raise ValueError("non-finite JSON constant")


def _open_binary_source(path: Path) -> BinaryIO:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        return path.open("rb")

    flags = os.O_RDONLY | no_follow
    flags |= getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        return os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise


def _load_json_object(path: Path, *, max_bytes: int) -> dict[str, Any]:
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes <= 0
    ):
        raise ProjectionError("invalid file byte budget")

    candidate = Path(path)
    try:
        before = candidate.lstat()
    except (OSError, ValueError, TypeError):
        raise ProjectionError("projection source is unavailable") from None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ProjectionError("projection source must be a regular file")
    if before.st_size <= 0 or before.st_size > max_bytes:
        raise ProjectionError("projection source violates the byte budget")

    try:
        with _open_binary_source(candidate) as source:
            opened = os.fstat(source.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise ProjectionError("projection source must remain regular")
            if (before.st_dev, before.st_ino) != (
                opened.st_dev,
                opened.st_ino,
            ):
                raise ProjectionError("projection source changed during open")
            if opened.st_size <= 0 or opened.st_size > max_bytes:
                raise ProjectionError("projection source violates the byte budget")
            payload = source.read(max_bytes + 1)
            after = os.fstat(source.fileno())
    except ProjectionError:
        raise
    except (OSError, ValueError, TypeError):
        raise ProjectionError("projection source could not be read") from None

    if (
        len(payload) != opened.st_size
        or len(payload) > max_bytes
        or after.st_size != opened.st_size
        or after.st_mtime_ns != opened.st_mtime_ns
    ):
        raise ProjectionError("projection source changed during read")
    try:
        decoded = payload.decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            parse_constant=_reject_nonfinite_json,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ProjectionError("projection source is not valid UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise ProjectionError("projection source JSON must be an object")
    return value


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_json_strings(value: Any) -> Any:
    if isinstance(value, str):
        return _normalize_newlines(value)
    if isinstance(value, dict):
        return {
            key: _normalize_json_strings(nested)
            for key, nested in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_json_strings(nested) for nested in value]
    return value


def _canonical_report_ir(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProjectionError("report_ir must be an object or null")
    try:
        upgraded = upgrade_report_ir_schema(value)
    except SchemaMigrationError:
        raise ProjectionError(
            "report_ir schema is invalid or unsupported"
        ) from None
    try:
        normalized = _normalize_json_strings(upgraded)
        return json.loads(canonical_json_bytes(normalized).decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ProjectionError("report_ir is not canonical JSON data") from None


def _technical_body_dominated(document: FormalBodyDocument) -> bool:
    visible = "\n".join(
        str(text)
        for _, text in document.text_segments()
        if str(text or "").strip()
    )
    compact = _TECHNICAL_MARKDOWN.sub("", visible)
    if not compact:
        return False

    spans: list[tuple[int, int]] = []
    for phrase in TECHNICAL_BODY_PHRASES:
        marker = _TECHNICAL_MARKDOWN.sub("", str(phrase or ""))
        if not marker:
            continue
        start = 0
        while True:
            index = compact.find(marker, start)
            if index < 0:
                break
            spans.append((index, index + len(marker)))
            start = index + max(1, len(marker))
    if not spans:
        return False

    covered = 0
    current_start, current_end = sorted(spans)[0]
    for start, end in sorted(spans)[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        covered += current_end - current_start
        current_start, current_end = start, end
    covered += current_end - current_start
    return covered / len(compact) >= 0.35


def _eligibility(
    *,
    status: str,
    document: FormalBodyDocument,
) -> tuple[bool, str | None]:
    if status in _NONTERMINAL_STATUSES:
        return False, "not_ready"
    if status == "failed":
        return False, "failed"
    if status == "interrupted":
        return False, "interrupted"
    if status not in _ELIGIBLE_STATUSES:
        return False, "unsupported_status"
    if not document.has_content():
        return False, "empty_body"
    if _technical_body_dominated(document):
        return False, "technical_body"
    return True, None


def build_run_snapshot_from_value(
    run_value: dict[str, Any],
    hmac_key: bytes,
) -> RunSnapshot:
    if not isinstance(run_value, dict):
        raise ProjectionError("run snapshot source must be an object")
    try:
        run = upgrade_run_schema(run_value)
    except SchemaMigrationError:
        raise ProjectionError("run schema is invalid or unsupported") from None

    status = run.get("status")
    run_status = run.get("run_status")
    if (
        not isinstance(status, str)
        or not isinstance(run_status, str)
        or status != run_status
    ):
        raise ProjectionError("run status fields must agree")
    run_id = run.get("run_id")
    pack_id = run.get("pack_id")
    if not isinstance(run_id, str) or not isinstance(pack_id, str):
        raise ProjectionError("run identities are invalid")
    version = run.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        raise ProjectionError("report version must be a positive integer")
    report_markdown = run.get("report_markdown")
    if not isinstance(report_markdown, str):
        raise ProjectionError("report_markdown must be text")

    normalized_markdown = _normalize_newlines(report_markdown)
    report_ir = _canonical_report_ir(run.get("report_ir"))
    document = FormalBodyDocument(
        markdown=normalized_markdown,
        report_ir=report_ir,
    )
    eligible, ineligible_reason = _eligibility(
        status=status,
        document=document,
    )
    report_sha256 = canonical_sha256(
        {
            "report_markdown": normalized_markdown,
            "report_ir": report_ir,
        }
    )
    try:
        run_ref = hmac_reference(hmac_key, "run", run_id)
        return RunSnapshot(
            run_id=run_id,
            pack_id=pack_id,
            run_ref=run_ref,
            status=status,
            report_version=version,
            report_markdown=normalized_markdown,
            report_ir=report_ir,
            report_sha256=report_sha256,
            eligible=eligible,
            ineligible_reason=ineligible_reason,
            source_updated_at=(
                run.get("updated_at")
                if isinstance(run.get("updated_at"), str)
                else None
            ),
        )
    except (TypeError, ValueError, ValidationError):
        raise ProjectionError("run snapshot validation failed") from None


def load_run_snapshot(
    path: Path,
    hmac_key: bytes,
    *,
    max_bytes: int = 4 * 1024 * 1024,
) -> RunSnapshot:
    value = _load_json_object(path, max_bytes=max_bytes)
    snapshot = build_run_snapshot_from_value(value, hmac_key)
    if Path(path).stem != snapshot.run_id:
        raise ProjectionError("run filename does not match its content identity")
    return snapshot


def _scalar_excerpt(value: Any) -> str:
    if isinstance(value, str):
        excerpt = _normalize_newlines(value)
    elif value is None or isinstance(value, (dict, list, tuple)):
        raise ProjectionError("evidence requires a scalar excerpt")
    elif isinstance(value, (bool, int, float)):
        try:
            excerpt = canonical_json_bytes(value).decode("utf-8")
        except (TypeError, ValueError):
            raise ProjectionError("evidence scalar is invalid") from None
    else:
        raise ProjectionError("evidence requires a scalar excerpt")
    if not excerpt or len(excerpt) > 1_500:
        raise ProjectionError("evidence excerpt violates its size limit")
    return excerpt


def _evidence_excerpt(item: dict[str, Any]) -> str:
    source_ref = item.get("source_ref")
    if not isinstance(source_ref, dict):
        raise ProjectionError("A/B evidence requires a validated source")
    quote = source_ref.get("quote")
    if isinstance(quote, str) and quote.strip():
        excerpt = _normalize_newlines(quote)
        if len(excerpt) > 1_500:
            raise ProjectionError("evidence excerpt violates its size limit")
        return excerpt
    return _scalar_excerpt(item.get("value"))


def _positive_location(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ProjectionError("evidence locator is invalid")
    return value


def _safe_locator(
    evidence_kind: str,
    source_ref: dict[str, Any],
) -> SafeLocator:
    page = _positive_location(source_ref.get("page_no"))
    table_index = _positive_location(source_ref.get("table_index"))
    row = _positive_location(source_ref.get("row"))
    column = _positive_location(source_ref.get("column"))
    try:
        if evidence_kind == "table_cell":
            if table_index is not None:
                return SafeLocator(
                    kind="table_cell",
                    table_index=table_index,
                    row=row,
                    column=column,
                )
            if (
                source_ref.get("sheet_name") is not None
                and row is not None
                and column is not None
            ):
                return SafeLocator(
                    kind="sheet_cell",
                    row=row,
                    column=column,
                )
        if evidence_kind in {"attachment_text", "derived_fact", "table_cell"} and page is not None:
            return SafeLocator(kind="pdf_page", ordinal=page)
        if evidence_kind not in {
            "article_field",
            "article_text",
            "attachment_text",
            "table_cell",
            "derived_fact",
        }:
            raise ProjectionError("evidence locator kind is unsupported")
        return SafeLocator(kind="article")
    except ValidationError:
        raise ProjectionError("evidence locator validation failed") from None


def _validated_pack(
    snapshot: RunSnapshot,
    pack_value: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(pack_value, dict):
        raise ProjectionError("evidence pack source must be an object")
    try:
        canonical_json_bytes(pack_value)
        pack = read_evidence_pack(pack_value)
    except (EvidenceValidationError, KeyError, TypeError, ValueError):
        raise ProjectionError("evidence pack schema is invalid or unsupported") from None
    pack_id = pack.get("pack_id")
    if (
        not isinstance(pack_id, str)
        or not pack_id.strip()
        or pack_id != snapshot.pack_id
    ):
        raise ProjectionError("evidence pack identity does not match the run")
    return pack


def _check_snapshot(snapshot: RunSnapshot) -> FormalBodyDocument:
    if not snapshot.eligible or snapshot.status not in _ELIGIBLE_STATUSES:
        raise ProjectionError("run snapshot is not eligible")
    document = FormalBodyDocument(
        markdown=snapshot.report_markdown,
        report_ir=snapshot.report_ir,
    )
    if not document.has_content() or _technical_body_dominated(document):
        raise ProjectionError("run snapshot has no eligible formal body")
    expected_report_hash = canonical_sha256(
        {
            "report_markdown": _normalize_newlines(snapshot.report_markdown),
            "report_ir": _canonical_report_ir(snapshot.report_ir),
        }
    )
    if expected_report_hash != snapshot.report_sha256:
        raise ProjectionError("run snapshot report hash is inconsistent")
    return document


def _raw_identity_tokens(
    snapshot: RunSnapshot,
    pack: dict[str, Any],
) -> set[str]:
    tokens = {snapshot.run_id, snapshot.pack_id}
    for item in list(pack.get("evidence_items") or []):
        if not isinstance(item, dict):
            continue
        evidence_id = item.get("evidence_id")
        if isinstance(evidence_id, str):
            tokens.add(evidence_id)
        source_ref = item.get("source_ref")
        if not isinstance(source_ref, dict):
            continue
        for field in (
            "menu_code",
            "articleid",
            "attachment_id",
            "filename",
            "source_hash",
        ):
            value = source_ref.get(field)
            if isinstance(value, str):
                tokens.add(value)
    return {
        normalized
        for token in tokens
        if (
            normalized := unicodedata.normalize(
                "NFKC",
                _normalize_newlines(token),
            ).strip()
        )
    }


def _contains_raw_identity(
    serialized_projection: str,
    token: str,
) -> bool:
    normalized_projection = unicodedata.normalize(
        "NFKC",
        _normalize_newlines(serialized_projection),
    )
    normalized_token = unicodedata.normalize(
        "NFKC",
        _normalize_newlines(token),
    ).strip()
    if not normalized_token:
        return False
    encoded_token = json.dumps(
        normalized_token,
        ensure_ascii=False,
    )[1:-1]
    pattern = re.compile(
        rf"(?<![\w-]){re.escape(encoded_token)}(?![\w-])"
    )
    return pattern.search(normalized_projection) is not None


def _projection_from_snapshot_and_pack(
    snapshot: RunSnapshot,
    pack_value: dict[str, Any],
) -> AdvisoryProjection:
    document = _check_snapshot(snapshot)
    pack = _validated_pack(snapshot, pack_value)
    try:
        claim_index = build_claim_evidence_index(document, pack)
    except (
        EvidenceIndexError,
        EvidenceValidationError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise ProjectionError("claim evidence indexing failed") from None
    if not isinstance(claim_index, dict):
        raise ProjectionError("claim evidence index is invalid")

    raw_items = pack.get("evidence_items")
    if not isinstance(raw_items, list):
        raise ProjectionError("validated evidence pack has no item list")
    item_by_id = {
        str(item.get("evidence_id")): item
        for item in raw_items
        if isinstance(item, dict) and item.get("level") in {"A", "B"}
    }
    claims = claim_index.get("claims")
    if not isinstance(claims, list) or not all(
        isinstance(claim, dict)
        for claim in claims
    ):
        raise ProjectionError("claim evidence index is invalid")
    sorted_claims = sorted(
        claims,
        key=lambda claim: (
            str(claim.get("normalized_text") or ""),
            str(claim.get("claim_id") or ""),
            str(claim.get("text") or ""),
        ),
    )

    units: list[ProjectionUnit] = []
    unique_evidence_ids: set[str] = set()
    total_claim_chars = 0
    total_excerpt_chars = 0
    for claim in sorted_claims:
        if not isinstance(claim, dict):
            raise ProjectionError("claim evidence index contains an invalid claim")
        claim_id = claim.get("claim_id")
        text = claim.get("text")
        if not isinstance(claim_id, str) or not isinstance(text, str) or not text:
            raise ProjectionError("claim evidence index contains an invalid claim")
        text = _normalize_newlines(text)
        total_claim_chars += len(text)
        if len(text) > MAX_CLAIM_TEXT_CHARS or total_claim_chars > MAX_CLAIM_TEXT_CHARS:
            raise ProjectionError("claim text exceeds the approved budget")
        try:
            assert_safe_outbound_text(text)
        except BoundaryViolation:
            raise ProjectionError("claim text violates the outbound boundary") from None

        matched_ids = claim.get("evidence_ids")
        if not isinstance(matched_ids, list):
            raise ProjectionError("claim evidence ids are invalid")
        selected: dict[str, dict[str, Any]] = {}
        for evidence_id in matched_ids:
            if not isinstance(evidence_id, str):
                raise ProjectionError("claim evidence id is invalid")
            item = item_by_id.get(evidence_id)
            if item is None:
                raise ProjectionError("claim references unavailable A/B evidence")
            selected[evidence_id] = item
            if item.get("level") == "B":
                dependencies = item.get("derived_from")
                if not isinstance(dependencies, list):
                    raise ProjectionError("B evidence dependencies are invalid")
                for parent_id in dependencies:
                    parent = item_by_id.get(str(parent_id))
                    if parent is None or parent.get("level") != "A":
                        raise ProjectionError("B evidence parent is unavailable")
                    selected[str(parent_id)] = parent

        ordered_items = sorted(
            selected.values(),
            key=lambda item: (
                0 if item.get("level") == "A" else 1,
                str(item.get("evidence_id") or ""),
            ),
        )
        if len(ordered_items) > MAX_UNIQUE_EVIDENCE:
            raise ProjectionError("claim evidence exceeds the approved count")
        local_ids = {
            str(item["evidence_id"]): f"e{index}"
            for index, item in enumerate(ordered_items, start=1)
        }
        unique_evidence_ids.update(local_ids)
        if len(unique_evidence_ids) > MAX_UNIQUE_EVIDENCE:
            raise ProjectionError("projection evidence exceeds the approved count")

        excerpts: list[EvidenceExcerpt] = []
        for item in ordered_items:
            evidence_id = str(item["evidence_id"])
            excerpt = _evidence_excerpt(item)
            total_excerpt_chars += len(excerpt)
            if total_excerpt_chars > MAX_EVIDENCE_EXCERPT_CHARS:
                raise ProjectionError("evidence excerpts exceed the approved budget")
            try:
                assert_safe_outbound_text(excerpt)
            except BoundaryViolation:
                raise ProjectionError(
                    "evidence excerpt violates the outbound boundary"
                ) from None
            source_ref = item.get("source_ref")
            if not isinstance(source_ref, dict):
                raise ProjectionError("A/B evidence requires a validated source")
            parent_ids: tuple[str, ...] = ()
            if item.get("level") == "B":
                dependencies = item.get("derived_from")
                if not isinstance(dependencies, list):
                    raise ProjectionError("B evidence dependencies are invalid")
                try:
                    parent_ids = tuple(
                        local_ids[str(parent_id)]
                        for parent_id in sorted(dependencies)
                    )
                except KeyError:
                    raise ProjectionError("B evidence parent was not localized") from None
            try:
                excerpts.append(
                    EvidenceExcerpt(
                        local_id=local_ids[evidence_id],
                        level=str(item.get("level")),
                        kind=str(item.get("kind") or ""),
                        excerpt=excerpt,
                        parent_a_ids=parent_ids,
                        locator=_safe_locator(
                            str(item.get("kind") or ""),
                            source_ref,
                        ),
                    )
                )
            except ValidationError:
                raise ProjectionError("evidence excerpt validation failed") from None

        unit_id = "unit_" + canonical_sha256(
            {
                "kind": "claim",
                "claim_id": claim_id,
            }
        )[:16]
        try:
            units.append(
                ProjectionUnit(
                    unit_id=unit_id,
                    kind="claim",
                    text=text,
                    claim_count=1,
                    evidence=tuple(excerpts),
                    expected_facts=(),
                    attachment_expectation=None,
                )
            )
        except ValidationError:
            raise ProjectionError("projection unit validation failed") from None

    if not units:
        raise ProjectionError("eligible report contains no projectable claims")
    serialized_units = canonical_json_bytes(
        [unit.model_dump(mode="json") for unit in units]
    ).decode("utf-8")
    if any(
        _contains_raw_identity(serialized_units, token)
        for token in _raw_identity_tokens(snapshot, pack)
    ):
        raise ProjectionError("projection material contains a raw source identity")
    payload = {
        "schema_version": "8099.deepeval-projection/v1",
        "projection_version": "claim-ab-v1",
        "run_ref": snapshot.run_ref,
        "report_version": snapshot.report_version,
        "report_sha256": snapshot.report_sha256,
        "units": [unit.model_dump(mode="json") for unit in units],
    }
    projection_sha256 = canonical_sha256(payload)
    try:
        projection = AdvisoryProjection(
            **payload,
            projection_sha256=projection_sha256,
        )
    except ValidationError:
        raise ProjectionError("advisory projection validation failed") from None

    serialized = canonical_json_bytes(projection.model_dump(mode="json"))
    if len(serialized) > MAX_PROJECTION_JSON_BYTES:
        raise ProjectionError("advisory projection exceeds the JSON byte budget")
    try:
        assert_safe_outbound_text(serialized.decode("utf-8"))
    except BoundaryViolation:
        raise ProjectionError("advisory projection violates the outbound boundary") from None
    return projection


def build_projection(
    snapshot: RunSnapshot,
    pack_path: Path,
    *,
    max_bytes: int = 32 * 1024 * 1024,
) -> AdvisoryProjection:
    pack_value = _load_json_object(pack_path, max_bytes=max_bytes)
    return _projection_from_snapshot_and_pack(snapshot, pack_value)


def build_projection_from_values(
    *,
    run_value: dict[str, Any],
    pack_value: dict[str, Any],
    hmac_key: bytes,
) -> AdvisoryProjection:
    snapshot = build_run_snapshot_from_value(run_value, hmac_key)
    return _projection_from_snapshot_and_pack(snapshot, pack_value)
