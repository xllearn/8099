from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import socket
from typing import Any
from urllib.parse import urlsplit


class BoundaryViolation(ValueError):
    """Raised when outbound Judge material crosses the approved boundary."""


_BOUNDARY_MESSAGE = "outbound text violates the safety boundary"
_FORBIDDEN_CREDENTIAL = re.compile(
    r"\b(?:authorization|cookie|api[\s_-]*key|password|dsn|"
    r"access[\s_-]*token|refresh[\s_-]*token|client[\s_-]*secret|"
    r"secret[\s_-]*key|private[\s_-]*key)\b",
    re.IGNORECASE,
)
_BEARER_TOKEN = re.compile(
    r"\bbearer\s+[A-Za-z0-9._~+/=-]+",
    re.IGNORECASE,
)
_TOKEN_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9_-])[\"']?"
    r"(?:[A-Za-z][A-Za-z0-9_-]*[_-])?"
    r"(?:token|secret|credential)[\"']?\s*[:=]\s*[\"']?"
    r"[A-Za-z0-9._~+/=-]{4,}",
    re.IGNORECASE,
)
_PEM_PRIVATE_KEY_HEADER = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----",
    re.IGNORECASE,
)
_JWT_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*"
    r"(?![A-Za-z0-9_-])",
)
_PROVIDER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-[A-Za-z0-9_-]{4,}|"
    r"ghp_[A-Za-z0-9_]{4,}|"
    r"github_pat_[A-Za-z0-9_]{4,}|"
    r"xox[baprs]-[A-Za-z0-9_-]{4,}"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_WINDOWS_DRIVE_PATH = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]",
)
_WINDOWS_UNC_OR_DEVICE_PATH = re.compile(
    r"\\\\(?:[?.]\\|[^\\/\s]+\\[^\\/\s]+)",
)
_SENSITIVE_UNIX_PATH = re.compile(
    r"(?<![A-Za-z0-9])/"
    r"(?:app|opt|var|home|root|data|etc|usr|srv|mnt|tmp|run|proc|sys|dev)"
    r"(?:/|$)",
    re.IGNORECASE,
)
_HTTP_URL = re.compile(
    r"\bhttps?://[^\s<>'\"]+",
    re.IGNORECASE,
)
_INTERNAL_HOST_SUFFIXES = (
    ".corp",
    ".home",
    ".internal",
    ".intranet",
    ".lan",
    ".local",
    ".localhost",
)
_URL_TRAILING_PUNCTUATION = ".,;:!?)]}，。；：！？）】》"


def _validate_json_value(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int, float)):
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            _validate_json_value(nested)
        return
    if isinstance(value, (list, tuple)):
        for nested in value:
            _validate_json_value(nested)
        return
    raise TypeError("canonical JSON contains a non-JSON value")


def canonical_json_bytes(value: Any) -> bytes:
    _validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def text_sha256(value: str) -> str:
    normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def hmac_reference(key: bytes, namespace: str, source_id: str) -> str:
    if len(key) < 32:
        raise ValueError("HMAC key must contain at least 32 bytes")
    digest = hmac.new(
        key,
        f"{namespace}\0{source_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    prefix = {"run": "runref", "pack": "packref"}.get(namespace, "ref")
    return f"{prefix}_{digest}"


def _parse_ip_address(
    hostname: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        pass

    try:
        packed = socket.inet_aton(hostname)
    except OSError:
        return None
    return ipaddress.IPv4Address(packed)


def _is_internal_url(value: str) -> bool:
    candidate = value.rstrip(_URL_TRAILING_PUNCTUATION)
    try:
        hostname = urlsplit(candidate).hostname
    except ValueError:
        return True
    if not hostname:
        return True

    hostname = hostname.rstrip(".").lower()
    if hostname == "localhost":
        return True
    if hostname.endswith(_INTERNAL_HOST_SUFFIXES):
        return True

    address = _parse_ip_address(hostname)
    if address is None:
        return "." not in hostname
    return not address.is_global


def assert_safe_outbound_text(value: str) -> None:
    for pattern in (
        _FORBIDDEN_CREDENTIAL,
        _BEARER_TOKEN,
        _TOKEN_ASSIGNMENT,
        _PEM_PRIVATE_KEY_HEADER,
        _JWT_TOKEN,
        _PROVIDER_TOKEN,
        _WINDOWS_DRIVE_PATH,
        _WINDOWS_UNC_OR_DEVICE_PATH,
        _SENSITIVE_UNIX_PATH,
    ):
        if pattern.search(value):
            raise BoundaryViolation(_BOUNDARY_MESSAGE)

    for match in _HTTP_URL.finditer(value):
        if _is_internal_url(match.group(0)):
            raise BoundaryViolation(_BOUNDARY_MESSAGE)


def advisory_input_sha256(
    *,
    report_sha256: str,
    evidence_projection_sha256: str,
    metric_set_sha256: str,
    deepeval_version: str,
    adapter_version: str,
    judge_provider: str,
    immutable_judge_model_or_profile_version: str,
    prompt_version: str,
    response_schema_version: str,
    safety_preamble_version: str,
    locale: str,
) -> str:
    return canonical_sha256(
        {
            "report_sha256": report_sha256,
            "evidence_projection_sha256": evidence_projection_sha256,
            "metric_set_sha256": metric_set_sha256,
            "deepeval_version": deepeval_version,
            "adapter_version": adapter_version,
            "judge_provider": judge_provider,
            "immutable_judge_model_or_profile_version": (
                immutable_judge_model_or_profile_version
            ),
            "prompt_version": prompt_version,
            "response_schema_version": response_schema_version,
            "safety_preamble_version": safety_preamble_version,
            "locale": locale,
        }
    )
