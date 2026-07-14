from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any, Mapping


MANIFEST_SCHEMA_VERSION = "8099.regression-manifest/v2"
SHA256_PATTERN_LENGTH = 64
SOURCE_ATTACHMENT_UNAVAILABLE = "SOURCE_ATTACHMENT_UNAVAILABLE"
MANIFEST_SOURCE_MAX_BYTES = 256 * 1024
MANIFEST_CONTRACT_MAX_BYTES = 128 * 1024
MANIFEST_CONTRACT_FORBIDDEN_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "content_bytes",
        "content_bytes_base64",
        "cookie",
        "evidence_pack",
        "file_bytes",
        "formal_body",
        "memory",
        "memory_items",
        "password",
        "raw_bytes",
        "report_ir",
        "report_markdown",
        "secret",
        "token",
    }
)
RECORD_FIELDS = (
    "menu_code",
    "articleid",
    "title",
    "audittime",
    "updatetime",
    "content_text",
)
ATTACHMENT_FIELDS = (
    "articleattid",
    "filename",
    "fileext",
    "filesize",
    "uploadtime",
    "sortnum",
    "source_url",
)


def _text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def normalize_source_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n"))


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def manifest_sha256(manifest: Mapping[str, Any]) -> str:
    return sha256_json(manifest)


def _normalized_field_name(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _is_forbidden_manifest_field(value: Any) -> bool:
    field = _normalized_field_name(value)
    if field in MANIFEST_CONTRACT_FORBIDDEN_KEYS:
        return True
    if "base64" in field or field.endswith("_bytes") or field.startswith("bytes_"):
        return True
    if field.endswith(("_api_key", "_password", "_secret", "_token")):
        return True
    return field in {
        "content_text",
        "document_body",
        "full_text",
        "parsed_text",
        "raw_text",
        "report_body",
    }


def _validate_manifest_export_safety(manifest: Mapping[str, Any]) -> None:
    if len(canonical_json_bytes(manifest)) > MANIFEST_SOURCE_MAX_BYTES:
        raise ValueError("manifest is too large to export safely")

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if _is_forbidden_manifest_field(key):
                    raise ValueError(f"manifest contains forbidden sensitive field: {key}")
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(manifest)


def _contract_attachment(attachment: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        *ATTACHMENT_FIELDS,
        "filepath_sha256",
        "metadata_sha256",
        "content_hash_source",
        "content_sha256",
        "content_length",
    )
    return {field: attachment.get(field, "") for field in fields}


def _contract_material(material: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "role",
        "menu_code",
        "articleid",
        "title",
        "captured_at",
        "title_sha256",
        "body_sha256",
        "attachments_sha256",
        "record_sha256",
        "attachment_count",
    )
    return {
        **{field: material.get(field, "") for field in fields},
        "attachments": [
            _contract_attachment(item)
            for item in material.get("attachments", [])
            if isinstance(item, Mapping)
        ],
    }


def _contract_replay(replay: Mapping[str, Any]) -> dict[str, Any]:
    request = replay.get("request")
    if not isinstance(request, Mapping):
        request = {}

    def identities(field: str) -> list[dict[str, str]]:
        values = request.get(field)
        if not isinstance(values, list):
            return []
        return [
            {
                "menu_code": str(item.get("menu_code") or ""),
                "articleid": str(item.get("articleid") or ""),
            }
            for item in values
            if isinstance(item, Mapping)
        ]

    return {
        "method": replay.get("method", ""),
        "path": replay.get("path", ""),
        "request": {
            "primary_materials": identities("primary_materials"),
            "auxiliary_materials": identities("auxiliary_materials"),
        },
    }


def build_manifest_contract(manifest: Mapping[str, Any]) -> dict[str, Any]:
    validate_manifest(manifest)
    _validate_manifest_export_safety(manifest)
    cases: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        projected = {
            field: case.get(field, "")
            for field in (
                "id",
                "name",
                "menu_code",
                "articleid",
                "title",
                "captured_at",
                "record_sha256",
                "expected_attachment_count",
            )
        }
        projected["capability_tags"] = list(case.get("capability_tags") or [])
        projected["materials"] = [
            _contract_material(item)
            for item in case.get("materials", [])
            if isinstance(item, Mapping)
        ]
        projected["replay"] = _contract_replay(case.get("replay") or {})
        if isinstance(case.get("skip"), Mapping):
            projected["skip"] = {
                "code": str(case["skip"].get("code") or ""),
                "observed_at": str(case["skip"].get("observed_at") or ""),
            }
        cases.append(projected)
    contract = {
        "schema_version": manifest["schema_version"],
        "manifest_version": manifest["manifest_version"],
        "captured_at": manifest["captured_at"],
        "hash_algorithm": manifest["hash_algorithm"],
        "cases": cases,
        "subsets": {
            "fixed3": list(manifest["subsets"]["fixed3"]),
            "fixed10": list(manifest["subsets"]["fixed10"]),
        },
    }
    validate_manifest_contract(contract)
    return contract


def _require_exact_keys(
    mapping: Mapping[str, Any], allowed: set[str], context: str
) -> None:
    actual = {str(key) for key in mapping}
    if actual != allowed:
        raise ValueError(f"{context} contains non-contract fields")


def validate_manifest_contract(contract: Mapping[str, Any]) -> None:
    payload = canonical_json_bytes(contract)
    if len(payload) > MANIFEST_CONTRACT_MAX_BYTES:
        raise ValueError("manifest contract is too large")
    _validate_manifest_export_safety(contract)
    _require_exact_keys(
        contract,
        {
            "schema_version",
            "manifest_version",
            "captured_at",
            "hash_algorithm",
            "cases",
            "subsets",
        },
        "manifest contract",
    )
    cases = contract.get("cases")
    if not isinstance(cases, list):
        raise ValueError("manifest contract cases must be a list")
    for case_index, case in enumerate(cases):
        if not isinstance(case, Mapping):
            raise ValueError("manifest contract case must be an object")
        case_keys = {
            "id",
            "name",
            "menu_code",
            "articleid",
            "title",
            "captured_at",
            "record_sha256",
            "expected_attachment_count",
            "capability_tags",
            "materials",
            "replay",
        }
        if "skip" in case:
            case_keys.add("skip")
        _require_exact_keys(case, case_keys, f"manifest contract case {case_index}")
        if "skip" in case:
            skip = case.get("skip")
            if not isinstance(skip, Mapping):
                raise ValueError("manifest contract skip must be an object")
            _require_exact_keys(skip, {"code", "observed_at"}, "manifest contract skip")
        materials = case.get("materials")
        if not isinstance(materials, list):
            raise ValueError("manifest contract materials must be a list")
        for material_index, material in enumerate(materials):
            if not isinstance(material, Mapping):
                raise ValueError("manifest contract material must be an object")
            _require_exact_keys(
                material,
                {
                    "role",
                    "menu_code",
                    "articleid",
                    "title",
                    "captured_at",
                    "title_sha256",
                    "body_sha256",
                    "attachments_sha256",
                    "record_sha256",
                    "attachment_count",
                    "attachments",
                },
                f"manifest contract material {case_index}/{material_index}",
            )
            attachments = material.get("attachments")
            if not isinstance(attachments, list):
                raise ValueError("manifest contract attachments must be a list")
            expected_attachment_keys = {
                *ATTACHMENT_FIELDS,
                "filepath_sha256",
                "metadata_sha256",
                "content_hash_source",
                "content_sha256",
                "content_length",
            }
            for attachment in attachments:
                if not isinstance(attachment, Mapping):
                    raise ValueError("manifest contract attachment must be an object")
                _require_exact_keys(
                    attachment,
                    expected_attachment_keys,
                    "manifest contract attachment",
                )
        replay = case.get("replay")
        if not isinstance(replay, Mapping):
            raise ValueError("manifest contract replay must be an object")
        _require_exact_keys(replay, {"method", "path", "request"}, "manifest contract replay")
        request = replay.get("request")
        if not isinstance(request, Mapping):
            raise ValueError("manifest contract request must be an object")
        _require_exact_keys(
            request,
            {"primary_materials", "auxiliary_materials"},
            "manifest contract request",
        )
        for field in ("primary_materials", "auxiliary_materials"):
            values = request.get(field)
            if not isinstance(values, list):
                raise ValueError("manifest contract request identities must be a list")
            for identity in values:
                if not isinstance(identity, Mapping):
                    raise ValueError("manifest contract identity must be an object")
                _require_exact_keys(
                    identity,
                    {"menu_code", "articleid"},
                    "manifest contract identity",
                )
    subsets = contract.get("subsets")
    if not isinstance(subsets, Mapping):
        raise ValueError("manifest contract subsets must be an object")
    _require_exact_keys(subsets, {"fixed3", "fixed10"}, "manifest contract subsets")
    validate_manifest(contract)


def attachment_identity(raw: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        _text(raw.get("articleattid")),
        _text(raw.get("filename")),
        _text(raw.get("source_url") or raw.get("filepath")),
    )


def _attachment_fingerprint(
    raw: Mapping[str, Any], verified_content: Mapping[str, Any] | None = None
) -> dict[str, str]:
    metadata = {field: _text(raw.get(field)) for field in ATTACHMENT_FIELDS}
    filepath = _text(raw.get("filepath"))
    if isinstance(verified_content, Mapping):
        content_sha256 = str(verified_content.get("content_sha256") or "")
        content_hash_source = str(verified_content.get("content_hash_source") or "")
        try:
            content_length = int(verified_content.get("content_length") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("verified attachment content has invalid length") from exc
        if (
            len(content_sha256) != SHA256_PATTERN_LENGTH
            or any(character not in "0123456789abcdef" for character in content_sha256)
            or content_hash_source not in {"raw_bytes", "text_only"}
            or content_length <= 0
        ):
            raise ValueError("verified attachment content is invalid")
    elif isinstance(raw.get("content_bytes"), (bytes, bytearray, memoryview)):
        content_bytes = raw.get("content_bytes")
        content = bytes(content_bytes)
        content_hash_source = "raw_bytes"
        content_length = len(content)
        content_sha256 = sha256_bytes(content)
    else:
        parsed_text = normalize_source_text(raw.get("parsed_text"))
        if not parsed_text.strip():
            raise ValueError("attachment content requires raw bytes or non-empty parsed text")
        content = b"TEXT_ONLY\n" + parsed_text.encode("utf-8")
        content_hash_source = "text_only"
        content_length = len(parsed_text.encode("utf-8"))
        content_sha256 = sha256_bytes(content)
    return {
        **metadata,
        "filepath_sha256": sha256_bytes(filepath.encode("utf-8")),
        "metadata_sha256": sha256_json({**metadata, "filepath": filepath}),
        "content_sha256": content_sha256,
        "content_hash_source": content_hash_source,
        "content_length": str(content_length),
    }


def build_record_fingerprint(
    record: Mapping[str, Any],
    *,
    verified_attachment_content: Mapping[
        tuple[str, str, str], Mapping[str, Any]
    ]
    | None = None,
) -> dict[str, Any]:
    normalized = {
        field: (
            normalize_source_text(record.get(field))
            if field == "content_text"
            else _text(record.get(field))
        )
        for field in RECORD_FIELDS
    }
    raw_attachments = [
        item for item in record.get("attachments", []) if isinstance(item, Mapping)
    ]
    identities = [attachment_identity(item) for item in raw_attachments]
    if len(set(identities)) != len(identities):
        raise ValueError("record contains duplicate attachment identities")
    if verified_attachment_content is not None and set(verified_attachment_content) != set(
        identities
    ):
        raise ValueError("verified attachment content does not match record attachments")
    attachments = [
        _attachment_fingerprint(
            item,
            verified_attachment_content.get(identity)
            if verified_attachment_content is not None
            else None,
        )
        for item, identity in zip(raw_attachments, identities)
    ]
    attachments.sort(
        key=lambda item: (
            item["articleattid"],
            item["filename"],
            item.get("source_url") or item["filepath_sha256"],
        )
    )
    canonical_record = {**normalized, "attachments": attachments}
    return {
        "menu_code": normalized["menu_code"],
        "articleid": normalized["articleid"],
        "title": normalized["title"],
        "title_sha256": sha256_bytes(normalized["title"].encode("utf-8")),
        "body_sha256": sha256_bytes(normalized["content_text"].encode("utf-8")),
        "attachments_sha256": sha256_json(attachments),
        "record_sha256": sha256_json(canonical_record),
        "attachment_count": len(attachments),
        "attachments": attachments,
    }


def verify_record_fingerprint(
    record: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    verified_attachment_content: Mapping[
        tuple[str, str, str], Mapping[str, Any]
    ]
    | None = None,
) -> dict[str, Any]:
    actual = build_record_fingerprint(
        record, verified_attachment_content=verified_attachment_content
    )
    compared_fields = (
        "menu_code",
        "articleid",
        "title_sha256",
        "body_sha256",
        "attachments_sha256",
        "record_sha256",
        "attachment_count",
    )
    mismatches = [
        field for field in compared_fields if actual.get(field) != expected.get(field)
    ]
    if mismatches:
        raise ValueError(f"record fingerprint mismatch: {', '.join(mismatches)}")
    return actual


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == SHA256_PATTERN_LENGTH and all(
        character in "0123456789abcdef" for character in text
    )


def _require_text(mapping: Mapping[str, Any], key: str, context: str) -> str:
    value = _text(mapping.get(key))
    if not value:
        raise ValueError(f"{context} is missing {key}")
    return value


def _validate_material(
    material: Mapping[str, Any], context: str, *, allow_unavailable: bool = False
) -> None:
    role = _require_text(material, "role", context)
    if role not in {"primary", "auxiliary"}:
        raise ValueError(f"{context} has invalid role")
    for key in ("menu_code", "articleid", "title", "captured_at"):
        _require_text(material, key, context)
    for key in ("title_sha256", "body_sha256"):
        if not _is_sha256(material.get(key)):
            raise ValueError(f"{context} is missing valid {key}")
    attachments = material.get("attachments")
    if not isinstance(attachments, list):
        raise ValueError(f"{context} has invalid attachments")
    has_unavailable_attachment = False
    for index, attachment in enumerate(attachments):
        if not isinstance(attachment, Mapping):
            raise ValueError(f"{context} attachment {index} is invalid")
        _require_text(attachment, "articleattid", f"{context} attachment {index}")
        _require_text(attachment, "filename", f"{context} attachment {index}")
        if not _is_sha256(attachment.get("filepath_sha256")):
            raise ValueError(f"{context} attachment {index} is missing filepath hash")
        if not _is_sha256(attachment.get("metadata_sha256")):
            raise ValueError(f"{context} attachment {index} is missing hash")
        content_hash_source = attachment.get("content_hash_source")
        if content_hash_source == "unavailable":
            has_unavailable_attachment = True
            if (
                not allow_unavailable
                or str(attachment.get("content_sha256") or "")
                or str(attachment.get("content_length") or "0") != "0"
            ):
                raise ValueError(
                    f"{context} attachment {index} has invalid unavailable marker"
                )
        else:
            if not _is_sha256(attachment.get("content_sha256")):
                raise ValueError(f"{context} attachment {index} is missing content hash")
            if content_hash_source not in {"raw_bytes", "text_only"}:
                raise ValueError(
                    f"{context} attachment {index} has invalid content hash source"
                )
    if has_unavailable_attachment:
        if material.get("attachments_sha256") or material.get("record_sha256"):
            raise ValueError(
                f"{context} unavailable attachment requires empty aggregate hashes"
            )
    else:
        for key in ("attachments_sha256", "record_sha256"):
            if not _is_sha256(material.get(key)):
                raise ValueError(f"{context} is missing valid {key}")


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"manifest schema_version must be {MANIFEST_SCHEMA_VERSION}")
    for key in ("manifest_version", "captured_at"):
        _require_text(manifest, key, "manifest")
    if manifest.get("hash_algorithm") != "sha256":
        raise ValueError("manifest hash_algorithm must be sha256")
    cases = manifest.get("cases")
    subsets = manifest.get("subsets")
    if not isinstance(cases, list) or not cases:
        raise ValueError("manifest cases must be a non-empty list")
    if not isinstance(subsets, Mapping):
        raise ValueError("manifest subsets must be an object")

    case_ids: set[str] = set()
    identities: set[tuple[str, str]] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, Mapping):
            raise ValueError(f"manifest case {index} must be an object")
        context = f"manifest case {index}"
        case_id = _require_text(case, "id", context)
        _require_text(case, "name", context)
        menu_code = _require_text(case, "menu_code", context)
        articleid = _require_text(case, "articleid", context)
        _require_text(case, "title", context)
        _require_text(case, "captured_at", context)
        if case_id in case_ids:
            raise ValueError(f"duplicate manifest case id: {case_id}")
        if (menu_code, articleid) in identities:
            raise ValueError(f"duplicate manifest identity: {menu_code}/{articleid}")
        case_ids.add(case_id)
        identities.add((menu_code, articleid))
        skip = case.get("skip")
        if skip is not None and (
            not isinstance(skip, Mapping)
            or skip.get("code") != SOURCE_ATTACHMENT_UNAVAILABLE
            or not _text(skip.get("observed_at"))
        ):
            raise ValueError(f"{context} has invalid skip metadata")
        if skip is None:
            if not _is_sha256(case.get("record_sha256")):
                raise ValueError(f"{context} is missing record hash")
        elif case.get("record_sha256"):
            raise ValueError(
                f"{context} unavailable source requires empty aggregate record hash"
            )
        tags = case.get("capability_tags")
        if not isinstance(tags, list) or not all(_text(tag) for tag in tags):
            raise ValueError(f"{context} has invalid capability_tags")
        materials = case.get("materials")
        if not isinstance(materials, list) or not materials:
            raise ValueError(f"{context} has invalid materials")
        unavailable_attachment_found = False
        for material_index, material in enumerate(materials):
            if not isinstance(material, Mapping):
                raise ValueError(f"{context} material {material_index} is invalid")
            _validate_material(
                material,
                f"{context} material {material_index}",
                allow_unavailable=skip is not None,
            )
            unavailable_attachment_found = unavailable_attachment_found or any(
                isinstance(attachment, Mapping)
                and attachment.get("content_hash_source") == "unavailable"
                for attachment in material.get("attachments", [])
            )
        if skip is not None and not unavailable_attachment_found:
            raise ValueError(f"{context} skip requires an unavailable attachment")
        primary = next(
            (item for item in materials if item.get("role") == "primary"), None
        )
        if not isinstance(primary, Mapping):
            raise ValueError(f"{context} requires a primary role")
        if (
            _text(primary.get("menu_code")) != menu_code
            or _text(primary.get("articleid")) != articleid
            or primary.get("record_sha256") != case.get("record_sha256")
        ):
            raise ValueError(f"{context} primary identity/hash mismatch")
        replay = case.get("replay")
        if not isinstance(replay, Mapping):
            raise ValueError(f"{context} is missing replay information")
        if replay.get("method") != "POST" or replay.get("path") != "/analysis/prepare":
            raise ValueError(f"{context} has invalid replay method/path")
        request = replay.get("request")
        if not isinstance(request, Mapping):
            raise ValueError(f"{context} replay request is missing")
        if not isinstance(request.get("primary_materials"), list) or not isinstance(
            request.get("auxiliary_materials"), list
        ):
            raise ValueError(f"{context} replay request has invalid material roles")
        material_roles = {
            role: [
                (_text(item.get("menu_code")), _text(item.get("articleid")))
                for item in materials
                if item.get("role") == role
            ]
            for role in ("primary", "auxiliary")
        }
        replay_roles: dict[str, list[tuple[str, str]]] = {}
        for role, request_key in (
            ("primary", "primary_materials"),
            ("auxiliary", "auxiliary_materials"),
        ):
            replay_identities: list[tuple[str, str]] = []
            for request_index, item in enumerate(request[request_key]):
                if not isinstance(item, Mapping):
                    raise ValueError(
                        f"{context} replay {request_key} item {request_index} is invalid"
                    )
                replay_identities.append(
                    (
                        _require_text(
                            item,
                            "menu_code",
                            f"{context} replay {request_key} item {request_index}",
                        ),
                        _require_text(
                            item,
                            "articleid",
                            f"{context} replay {request_key} item {request_index}",
                        ),
                    )
                )
            replay_roles[role] = replay_identities
        if replay_roles != material_roles:
            raise ValueError(f"{context} replay identities do not match material roles")

    for subset, expected_count in (("fixed3", 3), ("fixed10", 10)):
        selected = subsets.get(subset)
        if not isinstance(selected, list) or len(selected) != expected_count:
            raise ValueError(f"manifest subset {subset} must contain {expected_count} cases")
        if len(set(selected)) != len(selected) or not set(selected) <= case_ids:
            raise ValueError(f"manifest subset {subset} references invalid cases")
    if not set(subsets["fixed3"]) <= set(subsets["fixed10"]):
        raise ValueError("manifest fixed3 must be a subset of fixed10")
    skipped_ids = {
        str(case["id"]) for case in cases if isinstance(case.get("skip"), Mapping)
    }
    if set(subsets["fixed3"]) & skipped_ids:
        raise ValueError("manifest fixed3 cannot contain skipped cases")


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("fixed regression manifest must be an object")
    validate_manifest(data)
    return data


def select_cases(manifest: Mapping[str, Any], subset: str) -> list[dict[str, Any]]:
    validate_manifest(manifest)
    if subset == "all":
        selected_ids = [str(case["id"]) for case in manifest["cases"]]
    else:
        selected = manifest["subsets"].get(subset)
        if not isinstance(selected, list):
            raise ValueError(f"unknown regression subset: {subset}")
        selected_ids = [str(case_id) for case_id in selected]
    by_id = {str(case["id"]): case for case in manifest["cases"]}
    return [
        copy.deepcopy(by_id[case_id])
        for case_id in selected_ids
        if not isinstance(by_id[case_id].get("skip"), Mapping)
    ]


def excluded_cases(manifest: Mapping[str, Any], subset: str) -> list[dict[str, str]]:
    validate_manifest(manifest)
    if subset == "all":
        selected_ids = [str(case["id"]) for case in manifest["cases"]]
    else:
        selected = manifest["subsets"].get(subset)
        if not isinstance(selected, list):
            raise ValueError(f"unknown regression subset: {subset}")
        selected_ids = [str(case_id) for case_id in selected]
    by_id = {str(case["id"]): case for case in manifest["cases"]}
    result: list[dict[str, str]] = []
    for case_id in selected_ids:
        case = by_id[case_id]
        skip = case.get("skip")
        if not isinstance(skip, Mapping):
            continue
        result.append(
            {
                "case_id": case_id,
                "menu_code": str(case.get("menu_code") or ""),
                "articleid": str(case.get("articleid") or ""),
                "code": str(skip.get("code") or ""),
                "observed_at": str(skip.get("observed_at") or ""),
            }
        )
    return result
