"""Provider-agnostic chat completion client.

All supported providers expose an OpenAI-compatible REST surface, so we can
share one client and one `chat()` entry point. Selection is driven entirely by
env vars — no code changes required to switch backends.
"""

from __future__ import annotations

from typing import Any, Optional

import structlog
from openai import OpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import Settings, get_settings

log = structlog.get_logger(__name__)


class LLMConfigError(RuntimeError):
    """Raised when the LLM provider is misconfigured (missing key, etc)."""


class LLMCallError(RuntimeError):
    """Raised when the LLM call ultimately fails after retries."""


def _client_for(settings: Settings) -> OpenAI:
    api_key = settings.llm_api_key()
    if not api_key:
        raise LLMConfigError(
            f"Missing API key for LLM provider '{settings.llm_provider}'. "
            "Set the matching *_API_KEY env var."
        )
    return OpenAI(
        api_key=api_key,
        base_url=settings.llm_endpoint(),
        timeout=settings.llm_timeout_seconds,
        max_retries=0,  # tenacity handles retries below
    )


def chat(
    messages: list[dict[str, str]],
    *,
    json_mode: bool = False,
    temperature: Optional[float] = None,
    model: Optional[str] = None,
) -> str:
    """Send a chat completion request and return the assistant text.

    Args:
        messages: list of `{role, content}` dicts.
        json_mode: when True, request JSON output (`response_format`) where supported.
        temperature: optional override of the configured temperature.
        model: optional override of the configured model name.

    Returns:
        The assistant message content as a string.

    Raises:
        LLMConfigError: if the provider is not configured properly.
        LLMCallError: if all retries are exhausted.
    """
    settings = get_settings()
    client = _client_for(settings)
    model_name = model or settings.llm_model
    temp = temperature if temperature is not None else settings.llm_temperature

    @retry(
        reraise=True,
        stop=stop_after_attempt(max(1, settings.llm_max_retries)),
        wait=wait_exponential(multiplier=1.5, min=2, max=30),
        retry=retry_if_exception_type(Exception),
    )
    def _call() -> str:
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": temp,
        }
        if json_mode:
            # All supported providers accept response_format on their OpenAI-compat
            # endpoints (DeepSeek, OpenRouter, Kimi, Qwen, Gemini). If a specific
            # deployment rejects it, surface the error via tenacity (callers may
            # also fall back to plain text + manual JSON parse).
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "llm.call_failed",
                provider=settings.llm_provider,
                model=model_name,
                error=str(exc),
            )
            raise

        choices = getattr(resp, "choices", None) or []
        if not choices:
            raise LLMCallError("LLM returned no choices")
        content = choices[0].message.content or ""
        return content.strip()

    try:
        return _call()
    except LLMConfigError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LLMCallError(
            f"LLM call to provider '{settings.llm_provider}' failed: {exc}"
        ) from exc
