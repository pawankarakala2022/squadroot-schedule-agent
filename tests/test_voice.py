"""Voice tests: config, multipart upload, retry, failures (offline via MockTransport)."""

import functools
import io
import wave

import httpx
import pytest

from schedule_agent import voice
from schedule_agent.errors import AgentError, ModelError
from schedule_agent.voice import VoiceConfig, transcribe_audio

_RealClient = httpx.Client


def _wav(rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * rate)
    return buf.getvalue()


def _factory(handler):  # type: ignore[no-untyped-def]
    return functools.partial(_RealClient, transport=httpx.MockTransport(handler))


def _config(**over):  # type: ignore[no-untyped-def]
    kwargs = {"base_url": "https://api.meta.ai/v1", "voice_model": "muse-voice-transcribe-1.0", "api_key": "secret"}
    kwargs.update(over)
    return VoiceConfig(**kwargs)  # type: ignore[arg-type]


def test_contributor_model_refused() -> None:
    with pytest.raises(ValueError):
        VoiceConfig(base_url="https://api.meta.ai/v1", voice_model="x-contributor", api_key="k")
    with pytest.raises(ValueError):
        transcribe_audio(_config(voice_model="x-contributor"), b"data")


def test_from_env_none_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert VoiceConfig.from_env() is None


def test_from_env_reads_vars_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "k")
    monkeypatch.delenv("META_MODEL_API_BASE", raising=False)
    monkeypatch.delenv("META_TRANSCRIBE_MODEL", raising=False)
    config = VoiceConfig.from_env()
    assert config is not None
    assert config.base_url == "https://api.meta.ai/v1"
    assert config.voice_model == "muse-voice-transcribe-1.0"


def test_transcribe_posts_multipart_and_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["content_type"] = request.headers.get("content-type")
        body = request.content.decode("latin-1")
        seen["has_request"] = (
            'name="request"' in body
            and '"muse-voice-transcribe-1.0"' in body
            and '"audioEncoding": "WAV"' in body
            and '"language": "es"' in body
        )
        seen["has_audio"] = 'name="audio"; filename="note.wav"' in body
        return httpx.Response(200, json={"transcript": "  U12 twice a week  ", "turns": []})

    monkeypatch.setattr(voice.httpx, "Client", _factory(handler))
    capture: dict = {}
    text = transcribe_audio(_config(), b"\x00\x01audio", filename="note.wav", language="es", usage_out=capture)
    assert text == "U12 twice a week"
    assert seen["url"].endswith("/asr/transcribe")
    assert seen["auth"] == "Bearer secret"
    assert seen["content_type"].startswith("multipart/form-data")
    assert seen["has_request"] and seen["has_audio"]
    assert capture["model"] == "muse-voice-transcribe-1.0"
    assert isinstance(capture["latency_ms"], int)


def test_valid_wav_passes_but_wrong_rate_fails_early(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"transcript": "ok"})

    monkeypatch.setattr(voice.httpx, "Client", _factory(handler))
    assert transcribe_audio(_config(), _wav(16000), filename="note.wav") == "ok"
    assert transcribe_audio(_config(), _wav(24000), filename="note.wav") == "ok"

    def no_calls(request: httpx.Request) -> httpx.Response:
        raise AssertionError("model must not be called")

    monkeypatch.setattr(voice.httpx, "Client", _factory(no_calls))
    with pytest.raises(AgentError):
        transcribe_audio(_config(), _wav(8000), filename="note.wav")


def test_transcribe_retries_once_on_500(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json={"error": "busy"})
        return httpx.Response(200, json={"transcript": "recovered"})

    monkeypatch.setattr(voice.httpx, "Client", _factory(handler))
    assert transcribe_audio(_config(), b"data") == "recovered"
    assert calls["n"] == 2


def test_transcribe_failures_are_model_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        voice.httpx, "Client", _factory(lambda request: httpx.Response(503, json={"error": "down"}))
    )
    with pytest.raises(ModelError):
        transcribe_audio(_config(), b"data")
    monkeypatch.setattr(voice.httpx, "Client", _factory(lambda request: httpx.Response(200, json={"nope": True})))
    with pytest.raises(ModelError):
        transcribe_audio(_config(), b"data")
    monkeypatch.setattr(
        voice.httpx, "Client", _factory(lambda request: httpx.Response(200, json={"transcript": "   "}))
    )
    with pytest.raises(ModelError):
        transcribe_audio(_config(), b"data")


def test_empty_and_oversize_audio_raise_agent_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("model must not be called")

    monkeypatch.setattr(voice.httpx, "Client", _factory(handler))
    with pytest.raises(AgentError):
        transcribe_audio(_config(), b"")
    with pytest.raises(AgentError):
        transcribe_audio(_config(), b"x" * (voice.MAX_AUDIO_BYTES + 1))
