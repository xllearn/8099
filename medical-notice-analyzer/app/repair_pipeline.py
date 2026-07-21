from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
import hashlib
import json
import os
import re
from typing import Any, Callable, Protocol

from app.evidence_index import build_claim_evidence_index
from app.formal_body import FormalBodyDocument
from app.formal_body_safety import sanitize_formal_body, scan_formal_body
from app.generation.base import ReportGenerationResult
from app.vbp_quality_gate import evaluate_vbp_quality


CONTROLLED_REPAIR_VERSION = "20260716-delete-narrow-residual-v3"
CONTROLLED_REPAIR_FAILURE_CODE = "CONTROLLED_REPAIR_FAILED"
_STRUCTURAL_QUALITY_CODES = frozenset(
    {"VBP_REQUIRED_SECTION_MISSING", "VBP_REQUIRED_TOPIC_MISSING"}
)
_INPUT_CLAIM_TEXTS_KEY = "_repair_input_claim_texts"
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])")
_CLAUSE_SPLIT_RE = re.compile(r"(?<!\d)[，,](?!\d)")
_MARKDOWN_SENTENCE_LOCATION_RE = re.compile(r"^markdown\.line\[(\d+)]\.sentence\[(\d+)]$")
_MARKDOWN_TABLE_LOCATION_RE = re.compile(r"^markdown\.table\[(\d+)]\.row\[(\d+)]$")
_REPORT_IR_LIST_LOCATION_RE = re.compile(
    r"^report_ir\.(lead_paragraphs|enterprise_tips)\[(\d+)]\.sentence\[(\d+)]$"
)
_REPORT_IR_SECTION_LIST_LOCATION_RE = re.compile(
    r"^report_ir\.sections\[(\d+)]\.(paragraphs|highlights)\[(\d+)]\.sentence\[(\d+)]$"
)
_REPORT_IR_TABLE_LOCATION_RE = re.compile(
    r"^report_ir\.sections\[(\d+)]\.tables\[(\d+)]\.rows\[(\d+)]$"
)
_WORD_LOCATOR_FIELDS = (
    "word_download_url",
    "download_url",
    "word_filename",
    "word_file_path",
    "word_path",
    "word_exported_at",
)
_WORD_BOOLEAN_FIELDS = (
    "word_generated",
    "word_download_available",
    "word_export_available",
    "draft_word_export_available",
    "final_word_export_available",
    "deliverable",
)


class RepairComponent(Protocol):
    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        ...


def _env_bool(name: str, default: bool = False) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    return default if not value else value in {"1", "true", "yes", "on"}


def strict_delivery_gate_enabled() -> bool:
    return _env_bool("ENABLE_STRICT_DELIVERY_GATE", False)


def controlled_repair_enabled() -> bool:
    return _env_bool("ENABLE_UNSUPPORTED_FACT_REPAIR", False) and strict_delivery_gate_enabled()


def parse_repair_count(value: Any) -> tuple[int, bool]:
    if value is None or value == "":
        return 0, False
    if isinstance(value, bool):
        count = int(value)
    elif isinstance(value, int):
        count = value
    elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        count = int(value.strip())
    else:
        return 0, True
    return (count, False) if count >= 0 else (0, True)


def _body_hash(document: FormalBodyDocument) -> str:
    payload = {
        "markdown": document.markdown,
        "report_ir": document.report_ir,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _markdown_table_row_lines(lines: list[str]) -> dict[tuple[int, int], int]:
    locations: dict[tuple[int, int], int] = {}
    table_index = -1
    row_index = 0
    table_open = False
    for line_index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            table_open = False
            continue
        if not line.startswith("|"):
            table_open = False
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and all(cell and set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        next_line = lines[line_index + 1].strip() if line_index + 1 < len(lines) else ""
        next_cells = (
            [cell.strip() for cell in next_line.strip("|").split("|")]
            if next_line.startswith("|")
            else []
        )
        if next_cells and all(cell and set(cell) <= {"-", ":", " "} for cell in next_cells):
            table_index += 1
            row_index = 0
            table_open = True
            continue
        if table_open:
            locations[(max(table_index, 0), row_index)] = line_index
            row_index += 1
    return locations


def _delete_markdown_locations(markdown: str, locations: set[str]) -> str:
    lines = str(markdown or "").splitlines()
    sentence_targets: dict[int, set[int]] = {}
    table_targets: set[tuple[int, int]] = set()
    for location in locations:
        sentence_match = _MARKDOWN_SENTENCE_LOCATION_RE.fullmatch(location)
        if sentence_match:
            sentence_targets.setdefault(int(sentence_match.group(1)), set()).add(
                int(sentence_match.group(2))
            )
            continue
        table_match = _MARKDOWN_TABLE_LOCATION_RE.fullmatch(location)
        if table_match:
            table_targets.add((int(table_match.group(1)), int(table_match.group(2))))

    removed_line_indexes = {
        line_index
        for table_location, line_index in _markdown_table_row_lines(lines).items()
        if table_location in table_targets
    }
    output: list[str] = []
    for line_index, line in enumerate(lines):
        if line_index in removed_line_indexes:
            continue
        targets = sentence_targets.get(line_index)
        if targets:
            parts = _SENTENCE_SPLIT_RE.split(line)
            line = "".join(part for index, part in enumerate(parts) if index not in targets)
        output.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()


def _delete_sentences(value: Any, sentence_indexes: set[int]) -> str:
    parts = _SENTENCE_SPLIT_RE.split(str(value or ""))
    return "".join(part for index, part in enumerate(parts) if index not in sentence_indexes).strip()


def _delete_report_ir_locations(report_ir: dict[str, Any] | None, locations: set[str]) -> dict[str, Any] | None:
    if not isinstance(report_ir, dict):
        return None
    cleaned = copy.deepcopy(report_ir)
    root_targets: dict[tuple[str, int], set[int]] = {}
    section_targets: dict[tuple[int, str, int], set[int]] = {}
    table_targets: set[tuple[int, int, int]] = set()
    for location in locations:
        root_match = _REPORT_IR_LIST_LOCATION_RE.fullmatch(location)
        if root_match:
            root_targets.setdefault((root_match.group(1), int(root_match.group(2))), set()).add(
                int(root_match.group(3))
            )
            continue
        section_match = _REPORT_IR_SECTION_LIST_LOCATION_RE.fullmatch(location)
        if section_match:
            section_targets.setdefault(
                (int(section_match.group(1)), section_match.group(2), int(section_match.group(3))),
                set(),
            ).add(int(section_match.group(4)))
            continue
        table_match = _REPORT_IR_TABLE_LOCATION_RE.fullmatch(location)
        if table_match:
            table_targets.add(
                (int(table_match.group(1)), int(table_match.group(2)), int(table_match.group(3)))
            )

    for field in ("lead_paragraphs", "enterprise_tips"):
        values = cleaned.get(field)
        if not isinstance(values, list):
            continue
        updated: list[str] = []
        for value_index, value in enumerate(values):
            text = _delete_sentences(value, root_targets.get((field, value_index), set()))
            if text:
                updated.append(text)
        cleaned[field] = updated

    sections = cleaned.get("sections")
    if not isinstance(sections, list):
        return cleaned
    for section_index, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        for field in ("paragraphs", "highlights"):
            values = section.get(field)
            if not isinstance(values, list):
                continue
            updated = []
            for value_index, value in enumerate(values):
                text = _delete_sentences(
                    value,
                    section_targets.get((section_index, field, value_index), set()),
                )
                if text:
                    updated.append(text)
            section[field] = updated
        tables = section.get("tables")
        if not isinstance(tables, list):
            continue
        for table_index, table in enumerate(tables):
            if not isinstance(table, dict) or not isinstance(table.get("rows"), list):
                continue
            table["rows"] = [
                row
                for row_index, row in enumerate(table["rows"])
                if (section_index, table_index, row_index) not in table_targets
            ]
    return cleaned


def _delete_unsupported_claims(
    document: FormalBodyDocument,
    claims: list[dict[str, Any]],
) -> FormalBodyDocument:
    locations = {
        str(location)
        for claim in claims
        for location in list(claim.get("locations") or [])
        if str(location)
    }
    return FormalBodyDocument(
        markdown=_delete_markdown_locations(document.markdown, locations),
        report_ir=_delete_report_ir_locations(document.report_ir, locations),
    )


def _replace_sentence_text(value: Any, source: str, replacement: str) -> str:
    text = str(value or "")
    to_full_width = str.maketrans({",": "，", ":": "：", "(": "（", ")": "）"})
    to_ascii = str.maketrans({"，": ",", "：": ":", "（": "(", "）": ")"})
    candidates = (source, source.translate(to_full_width), source.translate(to_ascii))
    for candidate in dict.fromkeys(candidates):
        if candidate and candidate in text:
            return text.replace(candidate, replacement, 1)
    return text


def _replace_markdown_sentences(
    markdown: str,
    replacements: dict[str, tuple[str, str]],
) -> tuple[str, set[str]]:
    lines = str(markdown or "").splitlines()
    changed_locations: set[str] = set()
    for location, (source, replacement) in replacements.items():
        match = _MARKDOWN_SENTENCE_LOCATION_RE.fullmatch(location)
        if not match:
            continue
        line_index = int(match.group(1))
        sentence_index = int(match.group(2))
        if line_index >= len(lines):
            continue
        parts = _SENTENCE_SPLIT_RE.split(lines[line_index])
        if sentence_index >= len(parts):
            continue
        original = parts[sentence_index]
        parts[sentence_index] = _replace_sentence_text(original, source, replacement)
        if parts[sentence_index] != original:
            changed_locations.add(location)
        lines[line_index] = "".join(parts)
    return "\n".join(lines), changed_locations


def _replace_report_ir_sentences(
    report_ir: dict[str, Any] | None,
    replacements: dict[str, tuple[str, str]],
) -> tuple[dict[str, Any] | None, set[str]]:
    if not isinstance(report_ir, dict):
        return None, set()
    cleaned = copy.deepcopy(report_ir)
    changed_locations: set[str] = set()
    for location, (source, replacement) in replacements.items():
        root_match = _REPORT_IR_LIST_LOCATION_RE.fullmatch(location)
        if root_match:
            values = cleaned.get(root_match.group(1))
            value_index = int(root_match.group(2))
            sentence_index = int(root_match.group(3))
        else:
            section_match = _REPORT_IR_SECTION_LIST_LOCATION_RE.fullmatch(location)
            if not section_match:
                continue
            sections = cleaned.get("sections")
            section_index = int(section_match.group(1))
            if not isinstance(sections, list) or section_index >= len(sections):
                continue
            section = sections[section_index]
            if not isinstance(section, dict):
                continue
            values = section.get(section_match.group(2))
            value_index = int(section_match.group(3))
            sentence_index = int(section_match.group(4))
        if not isinstance(values, list) or value_index >= len(values):
            continue
        parts = _SENTENCE_SPLIT_RE.split(str(values[value_index] or ""))
        if sentence_index >= len(parts):
            continue
        original = parts[sentence_index]
        parts[sentence_index] = _replace_sentence_text(original, source, replacement)
        if parts[sentence_index] != original:
            changed_locations.add(location)
        values[value_index] = "".join(parts)
    return cleaned, changed_locations


def _supported_narrowing(
    claim: dict[str, Any],
    pack: dict[str, Any],
) -> str:
    source = str(claim.get("text") or "")
    fragments = [item.strip() for item in _CLAUSE_SPLIT_RE.split(source) if item.strip()]
    if len(fragments) < 2:
        return ""
    supported: list[str] = []
    for fragment in fragments:
        index = build_claim_evidence_index(
            FormalBodyDocument(markdown=fragment),
            pack,
        )
        fragment_claims = [
            item for item in list(index.get("claims") or []) if isinstance(item, dict)
        ]
        if fragment_claims and all(bool(item.get("supported")) for item in fragment_claims):
            supported.append(fragment)
    if not supported:
        return ""
    joined = "；".join(supported)
    candidates = [
        joined,
        *sorted(supported, key=lambda item: (-len(item), fragments.index(item))),
    ]
    for candidate in dict.fromkeys(candidates):
        if not candidate or candidate == source:
            continue
        index = build_claim_evidence_index(
            FormalBodyDocument(markdown=candidate),
            pack,
        )
        candidate_claims = [
            item for item in list(index.get("claims") or []) if isinstance(item, dict)
        ]
        if candidate_claims and all(
            bool(item.get("supported")) for item in candidate_claims
        ):
            return candidate
    return ""


def _repair_unsupported_claims(
    document: FormalBodyDocument,
    claims: list[dict[str, Any]],
    pack: dict[str, Any],
) -> tuple[FormalBodyDocument, set[str]]:
    replacements: dict[str, tuple[str, str]] = {}
    claim_locations: dict[str, set[str]] = {}
    for claim in claims:
        replacement = _supported_narrowing(claim, pack)
        if not replacement:
            continue
        claim_id = str(claim.get("claim_id") or "")
        source = str(claim.get("text") or "")
        sentence_locations = [
            str(location)
            for location in list(claim.get("locations") or [])
            if _MARKDOWN_SENTENCE_LOCATION_RE.fullmatch(str(location))
            or _REPORT_IR_LIST_LOCATION_RE.fullmatch(str(location))
            or _REPORT_IR_SECTION_LIST_LOCATION_RE.fullmatch(str(location))
        ]
        if not sentence_locations:
            continue
        claim_locations[claim_id] = set(sentence_locations)
        for location in sentence_locations:
            replacements[location] = (source, replacement)

    narrowed_markdown, markdown_changes = _replace_markdown_sentences(
        document.markdown,
        replacements,
    )
    narrowed_report_ir, report_ir_changes = _replace_report_ir_sentences(
        document.report_ir,
        replacements,
    )
    changed_locations = markdown_changes | report_ir_changes
    narrowed_claim_ids = {
        claim_id
        for claim_id, locations in claim_locations.items()
        if locations & changed_locations
    }
    narrowed = FormalBodyDocument(
        markdown=narrowed_markdown,
        report_ir=narrowed_report_ir,
    )
    remaining = [
        claim
        for claim in claims
        if str(claim.get("claim_id") or "") not in narrowed_claim_ids
    ]
    return _delete_unsupported_claims(narrowed, remaining), narrowed_claim_ids


def _clear_word_publication(metadata: dict[str, Any]) -> None:
    for field in _WORD_LOCATOR_FIELDS:
        metadata[field] = ""
    for field in _WORD_BOOLEAN_FIELDS:
        metadata[field] = False


def _mark_repair_failure(
    result: ReportGenerationResult,
    failure_code: str,
    *,
    error_type: str = "",
    metadata_updates: dict[str, Any] | None = None,
) -> ReportGenerationResult:
    metadata = dict(result.metadata)
    metadata.pop(_INPUT_CLAIM_TEXTS_KEY, None)
    if metadata_updates:
        metadata.update(copy.deepcopy(metadata_updates))
    existing_codes = [str(item or "") for item in list(metadata.get("quality_failure_codes") or [])]
    existing_codes.append(CONTROLLED_REPAIR_FAILURE_CODE)
    quality_gate = dict(metadata.get("quality_gate") or {})
    quality_gate["deliverable_status"] = "needs_manual_review"
    quality_gate["blocking_issue_codes"] = list(
        dict.fromkeys(
            [
                *[str(item or "") for item in list(quality_gate.get("blocking_issue_codes") or [])],
                CONTROLLED_REPAIR_FAILURE_CODE,
            ]
        )
    )
    metadata.update(
        {
            "status": "needs_manual_review",
            "repair_attempted": True,
            "repair_success": False,
            "repair_count": 1,
            "repair_finalized": True,
            "repair_version": CONTROLLED_REPAIR_VERSION,
            "repair_failure_code": failure_code,
            "repair_error_type": error_type,
            "quality_failure_codes": list(dict.fromkeys(existing_codes)),
            "quality_gate": quality_gate,
        }
    )
    _clear_word_publication(metadata)

    issue = {
        "issue_id": "Q_CONTROLLED_REPAIR_FAILED",
        "code": CONTROLLED_REPAIR_FAILURE_CODE,
        "severity": "blocker",
        "problem_type": "controlled_repair",
        "report_text": "",
        "source_basis": "validated_a_b_evidence",
        "fix_instruction": "Block publication because controlled repair did not validate.",
    }
    quality_check = dict(result.quality_check or {"passed": None, "issues": []})
    issues = list(quality_check.get("issues") or [])
    if not any(isinstance(item, dict) and item.get("issue_id") == issue["issue_id"] for item in issues):
        issues.append(issue)
    quality_check["passed"] = False
    quality_check["issues"] = issues
    remaining = list(result.remaining_issues or [])
    if not any(isinstance(item, dict) and item.get("issue_id") == issue["issue_id"] for item in remaining):
        remaining.append(issue)
    return replace(result, quality_check=quality_check, remaining_issues=remaining, metadata=metadata)


def fail_closed_controlled_repair(
    result: ReportGenerationResult,
    failure_code: str,
    *,
    error_type: str = "",
) -> ReportGenerationResult:
    return _mark_repair_failure(result, failure_code, error_type=error_type)


@dataclass
class UnsupportedFactRepairer:
    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        if not controlled_repair_enabled():
            return result
        metadata = dict(result.metadata)
        repair_count, repair_count_invalid = parse_repair_count(metadata.get("repair_count"))
        if repair_count_invalid:
            return _mark_repair_failure(
                result,
                "CONTROLLED_REPAIR_COUNT_INVALID",
            )
        if repair_count >= 1:
            return result

        document = FormalBodyDocument(markdown=result.report_markdown, report_ir=result.report_ir)
        try:
            claim_index = build_claim_evidence_index(document, pack)
        except Exception as exc:  # noqa: BLE001
            return _mark_repair_failure(
                result,
                "CONTROLLED_REPAIR_INDEX_FAILED",
                error_type=exc.__class__.__name__,
            )
        unsupported = [
            claim
            for claim in list(claim_index.get("claims") or [])
            if isinstance(claim, dict) and not bool(claim.get("supported"))
        ]
        if not unsupported:
            return result

        before_hash = _body_hash(document)
        try:
            repaired_document, narrowed_claim_ids = _repair_unsupported_claims(
                document, unsupported, pack
            )
            after_hash = _body_hash(repaired_document)
        except Exception as exc:  # noqa: BLE001
            return _mark_repair_failure(
                result,
                "CONTROLLED_REPAIR_DELETE_FAILED",
                error_type=exc.__class__.__name__,
            )
        actions = [
            {
                "action": (
                    "narrow_unsupported_claim"
                    if str(claim.get("claim_id") or "") in narrowed_claim_ids
                    else "delete_unsupported_claim"
                ),
                "claim_id": str(claim.get("claim_id") or ""),
                "locations": sorted(str(item) for item in list(claim.get("locations") or [])),
                "before_sha256": before_hash,
                "after_sha256": after_hash,
            }
            for claim in sorted(unsupported, key=lambda item: str(item.get("claim_id") or ""))
        ]
        if before_hash == after_hash:
            return _mark_repair_failure(
                result,
                "CONTROLLED_REPAIR_DELETE_FAILED",
                metadata_updates={"repair_actions": actions},
            )
        metadata.update(
            {
                "repair_attempted": True,
                "repair_success": False,
                "repair_count": 1,
                "repair_finalized": False,
                "repair_version": CONTROLLED_REPAIR_VERSION,
                "repair_failure_code": "",
                "repair_error_type": "",
                "repair_actions": actions,
                "repair_input_sha256": before_hash,
                "repair_output_sha256": after_hash,
                "repair_input_claim_ids": sorted(
                    str(claim.get("claim_id") or "")
                    for claim in list(claim_index.get("claims") or [])
                    if isinstance(claim, dict) and str(claim.get("claim_id") or "")
                ),
                _INPUT_CLAIM_TEXTS_KEY: sorted(
                    {
                        str(claim.get("normalized_text") or "")
                        for claim in list(claim_index.get("claims") or [])
                        if isinstance(claim, dict)
                        and str(claim.get("normalized_text") or "")
                    }
                ),
                "repair_new_fact_count": None,
                "repair_new_fact_observed": False,
            }
        )
        return replace(
            result,
            report_markdown=repaired_document.markdown,
            report_ir=repaired_document.report_ir,
            metadata=metadata,
        )


@dataclass
class ForbiddenPhraseRepairer:
    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        safety = sanitize_formal_body(
            FormalBodyDocument(markdown=result.report_markdown, report_ir=result.report_ir)
        )
        metadata = dict(result.metadata)
        metadata.update(
            {
                "formal_body_present": safety.has_body,
                "body_safety_passed": safety.safe,
                "forbidden_phrase_hits": [
                    {"phrase": hit.phrase, "location": hit.location} for hit in safety.hits
                ],
                "forbidden_phrases_removed": list(safety.removed_phrases),
            }
        )
        return replace(
            result,
            report_markdown=safety.document.markdown,
            report_ir=safety.document.report_ir,
            metadata=metadata,
        )


@dataclass
class StructureRepairer:
    legacy_repair: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None

    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        if self.legacy_repair is None:
            return result
        repaired = self.legacy_repair(result.to_legacy_result(), pack)
        return ReportGenerationResult.from_legacy_result(repaired if isinstance(repaired, dict) else result.to_legacy_result(), provider=result.provider)


@dataclass
class RepairPipeline:
    repairers: list[RepairComponent] = field(
        default_factory=lambda: [UnsupportedFactRepairer(), StructureRepairer(), ForbiddenPhraseRepairer()]
    )

    def run(self, result: ReportGenerationResult, pack: dict[str, Any]) -> ReportGenerationResult:
        current = result
        for repairer in self.repairers:
            try:
                current = repairer.run(current, pack)
            except Exception as exc:  # noqa: BLE001
                if bool(current.metadata.get("repair_attempted")):
                    return _mark_repair_failure(
                        current,
                        "CONTROLLED_REPAIR_PIPELINE_FAILED",
                        error_type=exc.__class__.__name__,
                    )
                raise
        metadata = current.metadata
        if bool(metadata.get("repair_attempted")) and not bool(metadata.get("repair_finalized")):
            return _finalize_controlled_repair(current, pack)
        return current


def _finalize_controlled_repair(
    result: ReportGenerationResult,
    pack: dict[str, Any],
) -> ReportGenerationResult:
    document = FormalBodyDocument(markdown=result.report_markdown, report_ir=result.report_ir)
    residual_actions: list[dict[str, Any]] = []
    try:
        claim_index = build_claim_evidence_index(document, pack)
        residual_claims = [
            claim
            for claim in list(claim_index.get("claims") or [])
            if isinstance(claim, dict) and not bool(claim.get("supported"))
        ]
        residual_narrowed_claim_ids: set[str] = set()
        if residual_claims:
            before_hash = _body_hash(document)
            cleaned_document, residual_narrowed_claim_ids = _repair_unsupported_claims(
                document,
                residual_claims,
                pack,
            )
            after_hash = _body_hash(cleaned_document)
            if before_hash != after_hash:
                residual_actions = [
                    {
                        "action": (
                            "narrow_residual_unsupported_claim"
                            if str(claim.get("claim_id") or "")
                            in residual_narrowed_claim_ids
                            else "delete_residual_unsupported_claim"
                        ),
                        "claim_id": str(claim.get("claim_id") or ""),
                        "locations": sorted(
                            str(item) for item in list(claim.get("locations") or [])
                        ),
                        "before_sha256": before_hash,
                        "after_sha256": after_hash,
                    }
                    for claim in sorted(
                        residual_claims,
                        key=lambda item: str(item.get("claim_id") or ""),
                    )
                ]
                document = cleaned_document
                claim_index = build_claim_evidence_index(document, pack)
        vbp_gate = evaluate_vbp_quality(document, pack, claim_index)
        safety = scan_formal_body(document)
    except Exception as exc:  # noqa: BLE001
        return _mark_repair_failure(
            result,
            "CONTROLLED_REPAIR_VALIDATION_FAILED",
            error_type=exc.__class__.__name__,
        )

    validated_result = replace(
        result,
        report_markdown=document.markdown,
        report_ir=document.report_ir,
    )

    metadata_updates = {
        "claim_evidence_index": claim_index,
        "vbp_quality_gate": vbp_gate,
        "formal_body_present": safety.has_body,
        "body_safety_passed": safety.safe,
        "forbidden_phrase_hits": [
            {"phrase": hit.phrase, "location": hit.location} for hit in safety.hits
        ],
        "repair_actions": [
            *list(result.metadata.get("repair_actions") or []),
            *residual_actions,
        ],
    }
    input_claim_ids = {
        str(item)
        for item in list(result.metadata.get("repair_input_claim_ids") or [])
        if str(item)
    }
    output_claim_ids = {
        str(claim.get("claim_id") or "")
        for claim in list(claim_index.get("claims") or [])
        if isinstance(claim, dict) and str(claim.get("claim_id") or "")
    }
    input_claim_texts = {
        str(item)
        for item in list(result.metadata.get(_INPUT_CLAIM_TEXTS_KEY) or [])
        if str(item)
    }
    narrowed_claim_ids = sorted(
        str(claim.get("claim_id") or "")
        for claim in list(claim_index.get("claims") or [])
        if isinstance(claim, dict)
        and str(claim.get("claim_id") or "") not in input_claim_ids
        and str(claim.get("normalized_text") or "")
        and any(
            str(claim.get("normalized_text") or "") in input_text
            for input_text in input_claim_texts
        )
    )
    new_claim_ids = sorted(
        output_claim_ids - input_claim_ids - set(narrowed_claim_ids)
    )
    metadata_updates.update(
        {
            "repair_new_fact_count": len(new_claim_ids),
            "repair_new_claim_ids": new_claim_ids,
            "repair_narrowed_claim_ids": narrowed_claim_ids,
            "repair_new_fact_observed": True,
        }
    )
    if not safety.has_body:
        return _mark_repair_failure(
            validated_result,
            "CONTROLLED_REPAIR_OUTPUT_EMPTY",
            metadata_updates=metadata_updates,
        )
    if not safety.safe:
        return _mark_repair_failure(
            validated_result,
            "CONTROLLED_REPAIR_SAFETY_FAILED",
            metadata_updates=metadata_updates,
        )
    if int(claim_index.get("metrics", {}).get("unsupported_claim_count") or 0) > 0:
        return _mark_repair_failure(
            validated_result,
            "CONTROLLED_REPAIR_UNSUPPORTED_REMAINS",
            metadata_updates=metadata_updates,
        )
    if new_claim_ids:
        return _mark_repair_failure(
            validated_result,
            "CONTROLLED_REPAIR_NEW_FACT_DETECTED",
            metadata_updates=metadata_updates,
        )
    gate_codes = (
        [
            str(item)
            for item in list(vbp_gate.get("blocking_issue_codes") or [])
            if str(item)
        ]
        if vbp_gate.get("passed") is not True
        else []
    )
    if vbp_gate.get("passed") is not True:
        evidence_safety_codes = [
            code for code in gate_codes if code not in _STRUCTURAL_QUALITY_CODES
        ]
        if not gate_codes or evidence_safety_codes:
            return _mark_repair_failure(
                validated_result,
                "CONTROLLED_REPAIR_VBP_GATE_FAILED",
                metadata_updates={
                    **metadata_updates,
                    "quality_failure_codes": list(
                        dict.fromkeys(
                            [
                                *[
                                    str(item)
                                    for item in list(
                                        result.metadata.get("quality_failure_codes") or []
                                    )
                                    if str(item)
                                ],
                                *gate_codes,
                            ]
                        )
                    ),
                },
            )

    metadata = dict(result.metadata)
    metadata.pop(_INPUT_CLAIM_TEXTS_KEY, None)
    metadata.update(metadata_updates)
    metadata.update(
        {
            "repair_attempted": True,
            "repair_success": True,
            "repair_count": 1,
            "repair_finalized": True,
            "repair_version": CONTROLLED_REPAIR_VERSION,
            "repair_failure_code": "",
            "repair_error_type": "",
            "repair_new_fact_count": len(new_claim_ids),
            "repair_new_claim_ids": new_claim_ids,
            "repair_narrowed_claim_ids": narrowed_claim_ids,
            "repair_new_fact_observed": True,
            "quality_failure_codes": list(
                dict.fromkeys(
                    [
                        *[
                            str(item)
                            for item in list(
                                result.metadata.get("quality_failure_codes") or []
                            )
                            if str(item)
                        ],
                        *gate_codes,
                    ]
                )
            ),
        }
    )
    return replace(validated_result, metadata=metadata)
