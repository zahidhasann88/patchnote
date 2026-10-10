"""OpenAI-compatible chat completions client (works with Ollama too)."""

from __future__ import annotations

import json
from typing import Any

import httpx


class LLMError(Exception):
    """The LLM HTTP call failed or returned unusable data."""


class LLMClient:
    def __init__(
        self,
        client: httpx.Client,
        *,
        base_url: str,
        api_key: str | None,
        timeout: float = 60.0,
        temperature: float = 0.2,
    ) -> None:
        self._http = client
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature

    def complete_json(self, messages: list[dict[str, str]], *, model: str) -> Any:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        try:
            response = self._http.post(url, headers=headers, json=body, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {type(exc).__name__}") from exc
        # Some local servers (older Ollama) reject response_format. Retry once.
        if response.status_code in {400, 422} and "response_format" in (response.text or ""):
            body.pop("response_format", None)
            try:
                response = self._http.post(url, headers=headers, json=body, timeout=self.timeout)
            except httpx.HTTPError as exc:
                raise LLMError(f"LLM request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise LLMError(f"LLM HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise LLMError("LLM returned non-JSON") from exc
        content = _extract_content(payload)
        try:
            parsed: Any = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"LLM content is not JSON: {exc}") from exc
        return parsed


def _extract_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise LLMError("LLM payload is not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("LLM payload missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise LLMError("LLM choice is not an object")
    message = first.get("message") or {}
    if not isinstance(message, dict):
        raise LLMError("LLM message is not an object")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LLMError("LLM message content is empty")
    # Some models wrap JSON in markdown fences.
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return text
