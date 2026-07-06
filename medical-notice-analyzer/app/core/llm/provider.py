from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class PromptRef:
    name: str
    version: str
    path: str
    sha256: str

    @property
    def ref(self) -> str:
        return f"{self.name}:{self.version}"

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "version": self.version,
            "ref": self.ref,
            "path": self.path,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class LLMRequest:
    prompt_ref: PromptRef
    variables: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResult:
    provider: str
    model: str
    prompt_ref: PromptRef
    raw_output: str
    elapsed_ms: int = 0
    usage: dict[str, Any] = field(default_factory=dict)

    def call_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_ref": self.prompt_ref.ref,
            "prompt_name": self.prompt_ref.name,
            "prompt_version": self.prompt_ref.version,
            "prompt_sha256": self.prompt_ref.sha256,
            "elapsed_ms": self.elapsed_ms,
            "usage": dict(self.usage),
        }


class LLMProvider(Protocol):
    provider: str
    model: str

    def generate(self, request: LLMRequest) -> LLMResult:
        ...


class LLMProviderError(Exception):
    def __init__(self, code: str, message: str, *, raw_output: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.raw_output = raw_output
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "raw_output": self.raw_output,
        }


class MockLLMProvider:
    provider = "mock"

    def __init__(self, response: str | None = None, model: str = "mock-local-report-v1") -> None:
        self.response = response
        self.model = model

    def generate(self, request: LLMRequest) -> LLMResult:
        started = time.perf_counter()
        raw_output = self.response if self.response is not None else self._default_response(request.variables)
        return LLMResult(
            provider=self.provider,
            model=self.model,
            prompt_ref=request.prompt_ref,
            raw_output=raw_output,
            elapsed_ms=max(0, int((time.perf_counter() - started) * 1000)),
            usage={"mock": True},
        )

    def _default_response(self, variables: dict[str, Any]) -> str:
        title = str(variables.get("title") or "Local mock report").strip()
        markdown = str(variables.get("draft_markdown") or "").strip()
        if not markdown:
            markdown = f"# {title}\n\nLocal mock provider generated an empty fallback draft."
        return json.dumps({"report_title": title, "report_markdown": markdown}, ensure_ascii=False)


def prompt_root() -> Path:
    configured = os.getenv("PROMPT_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "prompts"


def load_prompt_ref(name: str, version: str = "v1") -> PromptRef:
    path = prompt_root() / name / f"{version}.md"
    if not path.exists():
        raise LLMProviderError(
            "PROMPT_NOT_FOUND",
            f"prompt file not found: {name}:{version}",
            detail=str(path),
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return PromptRef(name=name, version=version, path=str(path), sha256=digest)


def load_prompt_text(prompt_ref: PromptRef) -> str:
    return Path(prompt_ref.path).read_text(encoding="utf-8")


def get_llm_provider(name: str | None = None) -> LLMProvider:
    provider_name = (name or os.getenv("LLM_PROVIDER") or "mock").strip().lower()
    if provider_name in {"mock", "local_mock"}:
        return MockLLMProvider()
    raise LLMProviderError("LLM_PROVIDER_UNSUPPORTED", f"unsupported LLM provider: {provider_name}")


def parse_llm_json(result: LLMResult | str) -> dict[str, Any]:
    raw_output = result.raw_output if isinstance(result, LLMResult) else str(result)
    text = _strip_json_fence(raw_output)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMProviderError(
            "LLM_JSON_PARSE_ERROR",
            "LLM provider returned invalid JSON",
            raw_output=raw_output,
            detail=str(exc),
        ) from exc
    if not isinstance(parsed, dict):
        raise LLMProviderError(
            "LLM_JSON_PARSE_ERROR",
            "LLM provider returned invalid JSON object",
            raw_output=raw_output,
        )
    return parsed


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text
