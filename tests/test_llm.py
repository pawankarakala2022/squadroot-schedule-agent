"""LLM client tests: contributor refusal, retry, failures (offline via MockTransport)."""

import functools
import json
from types import SimpleNamespace

import httpx
import pytest

from schedule_agent import llm
from schedule_agent.errors import ModelError
from schedule_agent.llm import ModelConfig, chat_fn

_RealClient = httpx.Client


def _factory(handler):  # type: ignore[no-untyped-def]
    # partial() wraps the original class: patching httpx.Client with a
    # plain factory would recurse, since llm.httpx IS the httpx module.
    return functools.partial(_RealClient, transport=httpx.MockTransport(handler))


def _ok(content: str = "hi"):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return handler


def _config() -> ModelConfig:
    return ModelConfig(base_url="https://api.meta.ai/v1", model="muse-spark-1.3", api_key="secret")


def test_contributor_model_refused() -> None:
    with pytest.raises(ValueError):
        ModelConfig(base_url="https://api.meta.ai/v1", model="muse-spark-1.3-contributor", api_key="k")
    fake = SimpleNamespace(base_url="https://api.meta.ai/v1", model="anything-contributor", api_key="k")
    with pytest.raises(ValueError):
        chat_fn(fake)  # type: ignore[arg-type]


def test_from_env_none_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert ModelConfig.from_env() is None


def test_from_env_reads_vars_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "k")
    monkeypatch.delenv("META_MODEL_API_BASE", raising=False)
    monkeypatch.delenv("META_MODEL", raising=False)
    config = ModelConfig.from_env()
    assert config is not None
    assert config.base_url == "https://api.meta.ai/v1"
    assert config.model == "muse-spark-1.3"


def test_from_env_refuses_contributor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "k")
    monkeypatch.setenv("META_MODEL", "muse-spark-1.3-contributor")
    with pytest.raises(ValueError):
        ModelConfig.from_env()


def test_chat_posts_and_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    monkeypatch.setattr(llm.httpx, "Client", _factory(handler))
    capture: dict = {}
    text = chat_fn(_config())(
        [{"role": "user", "content": "hi"}],
        response_format={"type": "json_schema", "json_schema": {"name": "x"}},
        usage_out=capture,
    )
    assert text == "hello"
    assert capture["model"] == "muse-spark-1.3"
    assert isinstance(capture["latency_ms"], int)
    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer secret"
    assert seen["body"]["model"] == "muse-spark-1.3"
    assert seen["body"]["temperature"] == 0.0
    assert seen["body"]["response_format"]["type"] == "json_schema"


def test_chat_retries_once_on_500(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json={"error": "busy"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "recovered"}}]})

    monkeypatch.setattr(llm.httpx, "Client", _factory(handler))
    assert chat_fn(_config())([{"role": "user", "content": "hi"}]) == "recovered"
    assert calls["n"] == 2


def test_chat_gives_up_after_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    monkeypatch.setattr(llm.httpx, "Client", _factory(handler))
    with pytest.raises(ModelError):
        chat_fn(_config())([{"role": "user", "content": "hi"}])


def test_chat_network_error_is_model_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(llm.httpx, "Client", _factory(handler))
    with pytest.raises(ModelError):
        chat_fn(_config())([{"role": "user", "content": "hi"}])


def test_chat_reuses_one_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """One chat_fn keeps a single pooled client: no TCP+TLS handshake per call."""
    built = {"n": 0}
    real_factory = _factory(_ok("hi"))

    def counting(*args, **kwargs):  # type: ignore[no-untyped-def]
        built["n"] += 1
        return real_factory(*args, **kwargs)

    monkeypatch.setattr(llm.httpx, "Client", counting)
    chat = chat_fn(_config())
    assert chat([{"role": "user", "content": "hi"}]) == "hi"
    assert chat([{"role": "user", "content": "hi"}]) == "hi"
    assert built["n"] == 1


def test_request_timeout_from_env_default_clamp_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from schedule_agent.llm import MAX_TIMEOUT_SECONDS, MIN_TIMEOUT_SECONDS, request_timeout_from_env

    monkeypatch.delenv("META_MODEL_TIMEOUT_SECONDS", raising=False)
    assert request_timeout_from_env() == llm.REQUEST_TIMEOUT_SECONDS == 120.0
    monkeypatch.setenv("META_MODEL_TIMEOUT_SECONDS", "45")
    assert request_timeout_from_env() == 45.0
    monkeypatch.setenv("META_MODEL_TIMEOUT_SECONDS", "100000")
    assert request_timeout_from_env() == MAX_TIMEOUT_SECONDS
    monkeypatch.setenv("META_MODEL_TIMEOUT_SECONDS", "1")
    assert request_timeout_from_env() == MIN_TIMEOUT_SECONDS
    for bad in ("junk", "", "nan", "inf"):
        monkeypatch.setenv("META_MODEL_TIMEOUT_SECONDS", bad)
        assert request_timeout_from_env() == llm.REQUEST_TIMEOUT_SECONDS


def test_from_env_carries_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "k")
    monkeypatch.delenv("META_MODEL_TIMEOUT_SECONDS", raising=False)
    assert ModelConfig.from_env() is not None
    config = ModelConfig.from_env()
    assert config is not None and config.timeout_seconds == 120.0
    monkeypatch.setenv("META_MODEL_TIMEOUT_SECONDS", "60")
    config = ModelConfig.from_env()
    assert config is not None and config.timeout_seconds == 60.0


def test_config_rejects_bad_timeout() -> None:
    for bad in (0, -1.5, float("nan"), float("inf"), "60", True):
        with pytest.raises(ValueError):
            ModelConfig(
                base_url="https://api.meta.ai/v1", model="muse-spark-1.3", api_key="k",
                timeout_seconds=bad,  # type: ignore[arg-type]
            )


def test_chat_uses_configured_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}
    real_factory = _factory(_ok("hi"))

    def capturing(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen["timeout"] = kwargs.get("timeout")
        return real_factory(*args, **kwargs)

    monkeypatch.setattr(llm.httpx, "Client", capturing)
    config = ModelConfig(
        base_url="https://api.meta.ai/v1", model="muse-spark-1.3", api_key="k", timeout_seconds=60.0
    )
    assert chat_fn(config)([{"role": "user", "content": "hi"}]) == "hi"
    assert isinstance(seen["timeout"], httpx.Timeout)
    assert seen["timeout"].read == 60.0
    assert seen["timeout"].connect == llm.CONNECT_TIMEOUT_SECONDS


def test_chat_bad_shape_and_empty_are_model_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm.httpx, "Client", _factory(lambda request: httpx.Response(200, json={"nope": True}))
    )
    with pytest.raises(ModelError):
        chat_fn(_config())([{"role": "user", "content": "hi"}])
    monkeypatch.setattr(llm.httpx, "Client", _factory(_ok("   ")))
    with pytest.raises(ModelError):
        chat_fn(_config())([{"role": "user", "content": "hi"}])
