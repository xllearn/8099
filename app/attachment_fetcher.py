from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx


DEFAULT_ATTACHMENT_DOWNLOAD_BASE_URL = "https://qx.eliancloud.cn/Common/EmailFileDownLoad?AttID="


@dataclass
class AttachmentDownloadResult:
    download_status: str
    auth_mode: str
    content: bytes | None = None
    content_type: str = ""
    filename: str = ""
    warnings: list[str] | None = None


def _bool_env(name: str, default: bool = False) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def build_attachment_auth_headers(user_cookie: str = "", user_headers: dict[str, str] | None = None) -> tuple[dict[str, str], str]:
    headers: dict[str, str] = {"User-Agent": "medical-notice-analyzer/0.1"}
    auth_mode = "none"
    configured_headers = (os.getenv("ATTACHMENT_HEADERS_JSON") or "").strip()
    if configured_headers:
        try:
            parsed = json.loads(configured_headers)
            if isinstance(parsed, dict):
                headers.update({str(key): str(value) for key, value in parsed.items()})
        except json.JSONDecodeError:
            headers["X-Attachment-Headers-Invalid"] = "1"

    configured_cookie = (os.getenv("ATTACHMENT_COOKIE") or os.getenv("ELIAN_QX_COOKIE") or "").strip()
    if configured_cookie:
        headers["Cookie"] = configured_cookie
        auth_mode = "configured_cookie"

    if user_headers:
        headers.update({str(key): str(value) for key, value in user_headers.items()})
        auth_mode = "user_headers"
    if user_cookie:
        headers["Cookie"] = user_cookie
        auth_mode = "user_cookie"
    return headers, auth_mode


def _looks_like_login_page(response: httpx.Response) -> bool:
    location = response.headers.get("location", "").lower()
    if "login" in location:
        return True
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type:
        return False
    text = response.text[:2000].lower()
    return "login" in text or "登录" in text or "signin" in text


def fetch_attachment_bytes(
    attachment: dict[str, Any],
    *,
    enable_download: bool | None = None,
    user_cookie: str = "",
    user_headers: dict[str, str] | None = None,
) -> AttachmentDownloadResult:
    warnings: list[str] = []
    download_enabled = _bool_env("ENABLE_ATTACHMENT_DOWNLOAD", True) if enable_download is None else enable_download
    if not download_enabled:
        return AttachmentDownloadResult("metadata_only", "none", warnings=["附件下载未启用"])

    articleattid = str(attachment.get("articleattid") or "").strip()
    if not articleattid:
        return AttachmentDownloadResult("download_failed", "none", warnings=["附件缺少 articleattid"])

    headers, auth_mode = build_attachment_auth_headers(user_cookie=user_cookie, user_headers=user_headers)
    base_url = (os.getenv("ATTACHMENT_DOWNLOAD_BASE_URL") or DEFAULT_ATTACHMENT_DOWNLOAD_BASE_URL).strip()
    url = f"{base_url}{articleattid}"
    timeout = _float_env("ATTACHMENT_REQUEST_TIMEOUT", 60)
    max_download_bytes = int(_float_env("ATTACHMENT_MAX_DOWNLOAD_MB", 50) * 1024 * 1024)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = client.get(url)
            if response.status_code in {401, 403} or _looks_like_login_page(response):
                status = "auth_failed" if auth_mode != "none" else "download_failed"
                return AttachmentDownloadResult(status, auth_mode, warnings=["attachment download was rejected or returned a login page"])
            response.raise_for_status()
            content = response.content
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        return AttachmentDownloadResult("network_unreachable", auth_mode, warnings=["attachment download endpoint is unreachable or timed out"])
    except httpx.HTTPError as exc:
        return AttachmentDownloadResult("download_failed", auth_mode, warnings=[f"附件下载失败: {exc.__class__.__name__}"])

    if len(content) > max_download_bytes:
        return AttachmentDownloadResult("download_failed", auth_mode, warnings=["附件超过最大下载大小限制"])

    return AttachmentDownloadResult(
        "downloaded",
        auth_mode,
        content=content,
        content_type=response.headers.get("content-type", ""),
        filename=str(attachment.get("filename") or ""),
        warnings=warnings,
    )

