"""Meta model client: OpenAI-compatible chat over httpx, Meta models only.

One client for both the Meta Model API and Meta open-weights endpoints:
base URL + model come from env. Anything ending in ``-contributor`` is
refused everywhere, since Meta trains on that data. Provider failures
raise :class:`ModelError` without ever including the API key.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import ModelError

DEFAULT_API_BASE = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"
REQUEST_TIMEOUT_SECONDS = 120.0
CONNECT_TIMEOUT_SECONDS = 5.0
RETRY_BACKOFF_SECONDS = 0.5

# Env override for the model-answer wait budget; clamped so a typo
# cannot hang a worker (or shrink the budget to nothing).
TIMEOUT_ENV_VAR = "META_MODEL_TIMEOUT_SECONDS"
MIN_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 300.0


def request_timeout_from_env() -> float:
    """Model-answer wait budget in seconds from env, or the default.

    Reads ``META_MODEL_TIMEOUT_SECONDS``; a missing or unparsable value
    falls back to ``REQUEST_TIMEOUT_SECONDS``, and anything outside
    5..300 seconds is clamped into that range.
    """
    try:
        value = float(os.environ.get(TIMEOUT_ENV_VAR, ""))
    except (TypeError, ValueError):
        return REQUEST_TIMEOUT_SECONDS
    if not math.isfinite(value):
        return REQUEST_TIMEOUT_SECONDS
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, value))

# Message list + optional OpenAI-style response_format -> assistant text.
ChatFn = Callable[..., str]


def _refuse_contributor(model: str) -> str:
    if model.strip().lower().endswith("-contributor"):
        raise ValueError("Contributor models must never be used (Meta trains on that data).")
    return model


@dataclass(frozen=True)
class ModelConfig:
    """Base URL + model + key for one Meta chat endpoint."""

    base_url: str
    model: str
    api_key: str
    timeout_seconds: float = REQUEST_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not self.base_url or not isinstance(self.base_url, str):
            raise ValueError("ModelConfig.base_url must be a non-empty string")
        if not self.model or not isinstance(self.model, str):
            raise ValueError("ModelConfig.model must be a non-empty string")
        _refuse_contributor(self.model)
        if not self.api_key or not isinstance(self.api_key, str):
            raise ValueError("ModelConfig.api_key must be a non-empty string")
        timeout = self.timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("ModelConfig.timeout_seconds must be a positive number of seconds")

    @classmethod
    def from_env(cls) -> ModelConfig | None:
        """Build from env, or None when no key is set.

        Reads ``META_MODEL_API_BASE`` (default https://api.meta.ai/v1),
        ``MODEL_API_KEY``, ``META_MODEL`` (default muse-spark-1.3) and
        ``META_MODEL_TIMEOUT_SECONDS`` (default 120, clamped to 5..300).
        """
        api_key = os.environ.get("MODEL_API_KEY", "")
        if not api_key:
            return None
        base_url = os.environ.get("META_MODEL_API_BASE", "") or DEFAULT_API_BASE
        model = os.environ.get("META_MODEL", "") or DEFAULT_MODEL
        return cls(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=request_timeout_from_env(),
        )


def chat_fn(config: ModelConfig, *, temperature: float = 0.0) -> ChatFn:
    """Return a chat callable bound to ``config``.

    The callable takes ``messages`` (``[{"role": ..., "content": ...}]``),
    an optional OpenAI-style ``response_format`` (e.g. strict
    ``json_schema``), and an optional ``usage_out`` dict that is filled
    with ``model`` / ``latency_ms`` / token counts for cost tracing, and
    returns the assistant's text. Retries once on HTTP 429 / 5xx. Raises
    :class:`ModelError` on provider failure and :class:`ValueError` if
    the model id is a ``-contributor`` one.
    """
    _refuse_contributor(config.model)
    state: dict[str, Any] = {}
    lock = threading.Lock()

    def _client() -> httpx.Client:
        """One pooled client per chat function: connections are kept alive across calls."""
        client = state.get("client")
        if client is None:
            with lock:
                client = state.get("client")
                if client is None:
                    # Connect/pool failures fail in seconds; only waiting
                    # for the model's answer gets the full read budget.
                    timeout = httpx.Timeout(
                        config.timeout_seconds,
                        connect=CONNECT_TIMEOUT_SECONDS,
                        pool=CONNECT_TIMEOUT_SECONDS,
                    )
                    state["client"] = client = httpx.Client(timeout=timeout)
        return client

    def chat(
        messages: list[dict[str, str]],
        *,
        response_format: Mapping[str, Any] | None = None,
        usage_out: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = dict(response_format)
        url = config.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": "Bearer " + config.api_key}
        last_status: int | None = None
        started = time.monotonic()
        client = _client()
        for attempt in range(2):  # one try + one retry on 429/5xx
            try:
                response = client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise ModelError("Assistant unavailable: could not reach the model.") from exc
            if response.status_code in (429, 500, 502, 503, 504):
                last_status = response.status_code
                if attempt == 0:
                    time.sleep(RETRY_BACKOFF_SECONDS)  # brief backoff, not an instant hammer
                continue
            if response.status_code >= 400:
                raise ModelError("Assistant unavailable: the model request failed.")
            try:
                data = response.json()
                text = data["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise ModelError("Assistant unavailable: bad model response.") from exc
            if not isinstance(text, str) or not text.strip():
                raise ModelError("Assistant unavailable: empty model response.")
            if usage_out is not None:
                usage = data.get("usage") if isinstance(data, dict) else None
                usage = usage if isinstance(usage, dict) else {}
                usage_out.update(
                    {
                        "model": data.get("model", config.model) if isinstance(data, dict) else config.model,
                        "latency_ms": int((time.monotonic() - started) * 1000),
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                    }
                )
            return text
        raise ModelError(f"Assistant unavailable: model busy (HTTP {last_status}).")

    return chat
