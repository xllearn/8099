from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.deepeval_advisory.hashing import text_sha256


class OpenAICompatibleJudgeTransport:
    """Minimal OpenAI-compatible structured-output transport."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 60,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not api_key.strip()
            or not model.strip()
        ):
            raise ValueError("judge transport configuration is invalid")
        self._endpoint = (
            base_url.rstrip("/") + "/chat/completions"
        )
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds

    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any] | None,
        context: Any,
    ) -> Any:
        from app.deepeval_advisory.judge import JudgeError, JudgeResponse

        if response_schema is None:
            raise JudgeError("response_schema_required")
        remaining = context.deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise JudgeError("deadline_exceeded")
        schema_name = str(
            response_schema.get("title")
            or response_schema.get("name")
            or "AdvisoryResponse"
        )
        request_value = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": response_schema,
                },
            },
        }
        request_bytes = json.dumps(
            request_value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint,
            data=request_bytes,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(
                request,
                timeout=min(float(self._timeout_seconds), remaining),
            ) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in {429, 503}:
                raise JudgeError(
                    f"http_{exc.code}",
                    retryable=True,
                ) from None
            raise JudgeError("http_error") from None
        except (TimeoutError, urllib.error.URLError, OSError):
            raise JudgeError("unavailable", outcome="unknown") from None
        if len(raw) > 4 * 1024 * 1024:
            raise JudgeError("response_too_large")
        try:
            value = json.loads(raw.decode("utf-8"))
            resolved_model = value["model"]
            text = value["choices"][0]["message"]["content"]
            usage = value.get("usage") or {}
            token_input = int(usage.get("prompt_tokens") or 0)
            token_output = int(usage.get("completion_tokens") or 0)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ):
            raise JudgeError("response_invalid") from None
        if resolved_model != self._model:
            raise JudgeError("model_version_mismatch")
        if not isinstance(text, str):
            raise JudgeError("response_invalid")
        return JudgeResponse(
            text=text,
            resolved_model_version=resolved_model,
            token_input=token_input,
            token_output=token_output,
            cost=0.0,
            latency_ms=max(
                0,
                int((time.monotonic() - started) * 1000),
            ),
            input_fingerprint=text_sha256(prompt),
            output_fingerprint=text_sha256(text),
        )

    async def acomplete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any] | None,
        context: Any,
    ) -> Any:
        return await asyncio.to_thread(
            self.complete,
            prompt=prompt,
            response_schema=response_schema,
            context=context,
        )
