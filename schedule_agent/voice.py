"""Audio -> text: a manager's voice note becomes request text for the agent loop.

Sends raw audio bytes to a Meta transcription model at
``{base}/asr/transcribe`` (multipart upload with a JSON ``request`` part
and an ``audio`` part), then the caller feeds the transcript into the
same agent loop (``ScheduleAgent.plan``). New code only: standard
library + httpx, no file I/O (bytes in, text out), env only via
:meth:`VoiceConfig.from_env`. Provider failures raise
:class:`ModelError` without ever including the API key; anything ending
in ``-contributor`` is refused everywhere. WAV upload verified
end-to-end (mono 16-bit PCM at 16 or 24 kHz); other containers are
passed through with a best-effort encoding hint and validated by the
server.
"""

from __future__ import annotations

import json
import os
import struct
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import AgentError, ModelError
from .llm import DEFAULT_API_BASE, _refuse_contributor

DEFAULT_TRANSCRIBE_MODEL = "muse-voice-transcribe-1.0"
TRANSCRIBE_TIMEOUT_SECONDS = 60.0
MAX_AUDIO_BYTES = 25 * 1024 * 1024
_WAV_RATES = (16000, 24000)

_MIME_BY_EXTENSION = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".webm": "audio/webm",
    ".flac": "audio/flac",
}

_ENCODING_BY_EXTENSION = {
    ".wav": "WAV",
    ".mp3": "MP3",
    ".m4a": "MP4",
    ".mp4": "MP4",
    ".ogg": "OGG",
    ".oga": "OGG",
    ".webm": "WEBM",
    ".flac": "FLAC",
}


@dataclass(frozen=True)
class VoiceConfig:
    """Base URL + transcription model + key for one Meta voice endpoint."""

    base_url: str
    voice_model: str
    api_key: str

    def __post_init__(self) -> None:
        if not self.base_url or not isinstance(self.base_url, str):
            raise ValueError("VoiceConfig.base_url must be a non-empty string")
        if not self.voice_model or not isinstance(self.voice_model, str):
            raise ValueError("VoiceConfig.voice_model must be a non-empty string")
        _refuse_contributor(self.voice_model)
        if not self.api_key or not isinstance(self.api_key, str):
            raise ValueError("VoiceConfig.api_key must be a non-empty string")

    @classmethod
    def from_env(cls) -> VoiceConfig | None:
        """Build from env, or None when no key is set.

        Reads ``META_MODEL_API_BASE`` (default https://api.meta.ai/v1),
        ``MODEL_API_KEY`` and ``META_TRANSCRIBE_MODEL`` (default
        muse-voice-transcribe-1.0).
        """
        api_key = os.environ.get("MODEL_API_KEY", "")
        if not api_key:
            return None
        base_url = os.environ.get("META_MODEL_API_BASE", "") or DEFAULT_API_BASE
        voice_model = os.environ.get("META_TRANSCRIBE_MODEL", "") or DEFAULT_TRANSCRIBE_MODEL
        return cls(base_url=base_url, voice_model=voice_model, api_key=api_key)


def _mime_type(filename: str, override: str | None) -> str:
    if override:
        return override
    lowered = filename.lower()
    for ext, mime in _MIME_BY_EXTENSION.items():
        if lowered.endswith(ext):
            return mime
    return "application/octet-stream"


def _encoding_hint(audio: bytes, filename: str) -> str:
    """Best-effort server encoding hint: sniffed WAV, else container name."""
    if len(audio) >= 12 and audio[0:4] == b"RIFF" and audio[8:12] == b"WAVE":
        return "WAV"
    lowered = filename.lower()
    for ext, encoding in _ENCODING_BY_EXTENSION.items():
        if lowered.endswith(ext):
            return encoding
    return lowered.rsplit(".", 1)[-1].upper() or "WAV"


def _wav_problem(audio: bytes) -> str | None:
    """Check WAV params client-side: None when not WAV, else '' or a reason.

    The server only takes mono 16-bit PCM WAV at 16 or 24 kHz; anything
    else fails there with a generic error, so reject it here with an
    actionable message instead.
    """
    if len(audio) < 16 or audio[0:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return None
    offset = 12
    for _ in range(16):  # walk chunks until the fmt chunk
        if offset + 8 > len(audio):
            break
        chunk_id, size = audio[offset : offset + 4], struct.unpack("<I", audio[offset + 4 : offset + 8])[0]
        if chunk_id == b"fmt " and size >= 16 and offset + 24 <= len(audio):
            pcm, channels, rate, _, _, bits = struct.unpack("<HHIIHH", audio[offset + 8 : offset + 24])
            if pcm != 1:
                return "not PCM"
            if channels != 1 or bits != 16 or rate not in _WAV_RATES:
                return f"{channels} channel(s), {bits} bit, {rate} Hz"
            return ""
        offset += 8 + size + (size % 2)
    return "unreadable header"


def transcribe_audio(
    config: VoiceConfig,
    audio: bytes,
    *,
    filename: str = "voice-note.m4a",
    mime_type: str | None = None,
    language: str | None = None,
    usage_out: dict[str, Any] | None = None,
) -> str:
    """Transcribe raw audio bytes to request text with a Meta voice model.

    ``audio`` is the file's bytes (any common phone-recorder format);
    ``filename`` picks the multipart filename and, unless ``mime_type``
    is given, the content type. ``language`` is an optional hint (e.g.
    ``"es"``). Fills ``usage_out`` with model/latency when given.
    Raises :class:`AgentError` when there is nothing to transcribe and
    :class:`ModelError` on provider failure.
    """
    _refuse_contributor(config.voice_model)
    if not isinstance(audio, (bytes, bytearray)) or len(audio) == 0:
        raise AgentError("I could not hear anything in that recording; please try again.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise AgentError("That recording is too long; please send a shorter voice note.")
    raw = bytes(audio)
    wav_problem = _wav_problem(raw)
    if wav_problem:
        raise AgentError(
            "That WAV recording won't transcribe "
            f"({wav_problem}); please send mono 16-bit audio at 16 or 24 kHz."
        )
    request_part: dict[str, str] = {"model": config.voice_model, "audioEncoding": _encoding_hint(raw, filename)}
    if language:
        request_part["language"] = language
    url = config.base_url.rstrip("/") + "/asr/transcribe"
    headers = {"Authorization": "Bearer " + config.api_key}
    files = {
        "request": (None, json.dumps(request_part), "application/json"),
        "audio": (filename, raw, _mime_type(filename, mime_type)),
    }
    last_status: int | None = None
    started = time.monotonic()
    with httpx.Client(timeout=TRANSCRIBE_TIMEOUT_SECONDS) as client:
        for _ in range(2):  # one try + one retry on 429/5xx
            try:
                response = client.post(url, files=files, headers=headers)
            except httpx.HTTPError as exc:
                raise ModelError("Assistant unavailable: could not reach the model.") from exc
            if response.status_code in (429, 500, 502, 503, 504):
                last_status = response.status_code
                continue
            if response.status_code >= 400:
                raise ModelError("Assistant unavailable: the model request failed.")
            try:
                text = response.json().get("transcript")
            except (ValueError, AttributeError) as exc:
                raise ModelError("Assistant unavailable: bad model response.") from exc
            if not isinstance(text, str) or not text.strip():
                raise ModelError("Assistant unavailable: empty model response.")
            if usage_out is not None:
                usage_out.update({"model": config.voice_model, "latency_ms": int((time.monotonic() - started) * 1000)})
            return text.strip()
    raise ModelError(f"Assistant unavailable: model busy (HTTP {last_status}).")
