from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class DeepSeekError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class DeepSeekClient:
    """OpenAI-compatible client for DeepSeek chat completions."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ):
        self.api_key = api_key if api_key is not None else settings.DEEPSEEK_API_KEY
        self.base_url = (base_url or settings.DEEPSEEK_BASE_URL).rstrip("/")
        self.model = model or settings.DEEPSEEK_MODEL
        if timeout is None:
            timeout = float(getattr(settings, "DEEPSEEK_TIMEOUT", 180) or 180)
        # Connect stays shorter; read must cover long reasoning on deepseek-v4-pro.
        self.timeout = httpx.Timeout(timeout, connect=30.0)

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.4,
        max_tokens: int | None = None,
    ) -> str:
        if not self.api_key:
            raise DeepSeekError("DEEPSEEK_API_KEY is not configured")

        if max_tokens is None:
            max_tokens = int(getattr(settings, "DEEPSEEK_MAX_TOKENS", 4096) or 4096)

        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        response = None
        last_exc: Exception | None = None
        for attempt in range(1, 3):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(url, json=payload, headers=headers)
                break
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning(
                    "DeepSeek timeout on attempt %s/%s: %s",
                    attempt,
                    2,
                    type(exc).__name__,
                )
                if attempt == 2:
                    raise DeepSeekError(str(exc)) from exc
            except httpx.HTTPError as exc:
                logger.warning("DeepSeek request failed: %s", type(exc).__name__)
                raise DeepSeekError(str(exc)) from exc

        if response is None:
            raise DeepSeekError(str(last_exc or "DeepSeek request failed"))

        if response.status_code >= 400:
            raise DeepSeekError(
                f"API error {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
            )

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DeepSeekError("Unexpected DeepSeek response shape") from exc

        if not isinstance(content, str) or not content.strip():
            raise DeepSeekError("Empty DeepSeek response")
        return content.strip()
