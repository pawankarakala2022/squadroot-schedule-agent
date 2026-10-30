"""Unified intake: text, voice, screenshots and PDF tables in, one request out.

A manager reaches the agent four ways -- typed words, a voice note
(speech to text), screenshots/photos, and PDF documents whose tables
hold the actual ground schedules. This module normalizes all of them
into a single :class:`IngestedRequest`: the request text (typed words
plus transcripts plus any text files), the league games and the
grounds/coach availability read from every attachment, merged and
de-duplicated. From there the existing design takes over unchanged:
:meth:`ScheduleAgent.handle_ingested` feeds the text to the usual
understand/edit flow, the games in as fixed events, and the
availability in as ``Busy`` times. The model never picks times here
either; files and transcripts are data, never instructions.

PDF support reads the embedded text layer in code (standard library
only): positioned strings are grouped back into table rows, then the
same strict model schemas and exact-match validation used for photos
read the table. A scanned/image-only PDF has no text layer: it fails
safely with a message asking for a screenshot or a rendered page image
instead of guessing. New code only; bytes in, data out, no file I/O.
"""

from __future__ import annotations

import base64
import re
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import AgentError
from .llm import ChatFn
from .models import Busy, Problem
from .vision import (
    MAX_IMAGE_BYTES,
    AvailabilityImport,
    ScheduleImport,
    availability_to_busy,
    extract_availability,
    extract_availability_from_text,
    extract_games,
    extract_games_from_text,
    games_to_busy,
    merge_availability,
    merge_schedules,
)
from .voice import MAX_AUDIO_BYTES, VoiceConfig, transcribe_audio

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_TEXT_BYTES = 100 * 1024
MAX_ATTACHMENTS = 12

# Host-injected speech-to-text: (audio bytes, filename) -> transcript.
TranscribeFn = Callable[[bytes, str], str]


def _sniff_kind(data: bytes, filename: str, mime_type: str) -> str:
    """Classify one attachment: magic bytes first, then mime, then extension."""
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or data[:6] in (b"GIF87a", b"GIF89a"):
        return "image"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image"
    if (data[:4] == b"RIFF" and data[8:12] == b"WAVE") or data[:3] == b"ID3" or data[4:8] == b"ftyp":
        return "audio"
    mime = (mime_type or "").split(";")[0].strip().lower()
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("text/"):
        return "text"
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return "pdf"
    if name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
        return "image"
    if name.endswith((".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac", ".mp4")):
        return "audio"
    if name.endswith((".txt", ".md", ".csv", ".tsv")):
        return "text"
    return "unknown"


@dataclass(frozen=True)
class Attachment:
    """One uploaded file: name, declared mime type and raw bytes.

    ``kind`` is derived from the bytes themselves (magic first), so a
    mislabelled upload is still handled as what it actually is.
    """

    filename: str
    mime_type: str
    data: bytes

    @property
    def kind(self) -> str:
        """One of ``text``, ``audio``, ``image``, ``pdf``, ``unknown``."""
        return _sniff_kind(bytes(self.data), self.filename, self.mime_type)

    @classmethod
    def text(cls, filename: str, text: str) -> Attachment:
        """Build a text attachment from a string."""
        return cls(filename=filename, mime_type="text/plain", data=text.encode("utf-8"))

    @classmethod
    def image(cls, filename: str, data: bytes, mime_type: str = "image/png") -> Attachment:
        """Build an image attachment from photo/screenshot bytes."""
        return cls(filename=filename, mime_type=mime_type, data=bytes(data))

    @classmethod
    def pdf(cls, filename: str, data: bytes) -> Attachment:
        """Build a PDF attachment from document bytes."""
        return cls(filename=filename, mime_type="application/pdf", data=bytes(data))

    @classmethod
    def audio(cls, filename: str, data: bytes, mime_type: str = "audio/wav") -> Attachment:
        """Build an audio attachment from recording bytes."""
        return cls(filename=filename, mime_type=mime_type, data=bytes(data))


@dataclass
class PdfDocument:
    """Tables read from a PDF text layer: rows per page, in reading order."""

    pages: list[list[list[str]]] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        """Number of pages that carried text."""
        return len(self.pages)

    def rows(self) -> list[list[str]]:
        """Every table row on every page, in order."""
        return [row for page in self.pages for row in page]

    @property
    def text(self) -> str:
        """The tables as pipe-joined lines, for the model to read as data."""
        return "\n".join(" | ".join(cells) for cells in self.rows())


# The header may not cross ``endobj`` (so it is the stream's own dictionary),
# and the EOL before ``endstream`` is optional (ReportLab omits it).
_STREAM_RE = re.compile(
    rb"<<((?:(?!endobj).)*?)>>\s*stream\r?\n(.*?)(?:\r?\n)?endstream", re.DOTALL
)
_FILTER_RE = re.compile(rb"/(ASCII85Decode|A85|ASCIIHexDecode|AHx|FlateDecode|Fl)\b")
_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _flate(raw: bytes) -> bytes:
    """zlib stream, falling back to a raw deflate body."""
    try:
        return zlib.decompress(raw)
    except zlib.error:
        return zlib.decompress(raw, -15)


def _ascii85(raw: bytes) -> bytes:
    """PDF ASCII85 body (``~>`` terminated, whitespace allowed)."""
    body = re.sub(rb"\s+", b"", raw)
    if body.startswith(b"<~"):
        body = body[2:]
    end = body.find(b"~>")
    return base64.a85decode(body if end == -1 else body[:end])


def _ascii_hex(raw: bytes) -> bytes:
    """PDF ASCIIHex body (``>`` terminated, odd digit count padded)."""
    digits = re.sub(rb"\s+", b"", raw).split(b">", 1)[0]
    if len(digits) % 2:
        digits += b"0"
    return bytes.fromhex(digits.decode("ascii"))


_DECODERS: dict[bytes, Callable[[bytes], bytes]] = {
    b"ASCII85Decode": _ascii85, b"A85": _ascii85,
    b"ASCIIHexDecode": _ascii_hex, b"AHx": _ascii_hex,
    b"FlateDecode": _flate, b"Fl": _flate,
}


def _pdf_streams(data: bytes) -> list[bytes]:
    """Decoded content streams (Flate / ASCII85 / ASCIIHex chains or plain), in file order."""
    out: list[bytes] = []
    for match in _STREAM_RE.finditer(data):
        header, raw = match.group(1), match.group(2)
        try:
            for name in _FILTER_RE.findall(header):
                raw = _DECODERS[name](raw)
        except (zlib.error, ValueError):
            continue
        out.append(raw)
    return out


def _literal_string(content: str, start: int) -> tuple[str, int]:
    """Read one ``( ... )`` PDF string at ``start``; return (text, next index)."""
    out: list[str] = []
    i = start + 1
    depth = 1
    while i < len(content) and depth:
        ch = content[i]
        if ch == "\\" and i + 1 < len(content):
            octal = re.match(r"[0-7]{1,3}", content[i + 1 : i + 4])
            if octal is not None:  # \ddd: one byte, e.g. \267 is a middle dot
                out.append(chr(int(octal.group(0), 8) & 0xFF))
                i += 1 + len(octal.group(0))
                continue
            nxt = content[i + 1]
            if nxt in "\r\n":  # backslash-EOL: line continuation, no character
                i += 3 if content[i + 1 : i + 3] == "\r\n" else 2
                continue
            out.append({"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}.get(nxt, nxt))
            i += 2
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                i += 1
                break
        out.append(ch)
        i += 1
    return "".join(out), i


def _hex_string(content: str, start: int) -> tuple[str, int]:
    """Read one ``< ... >`` hex string at ``start``; return (text, next index)."""
    end = content.find(">", start)
    if end == -1:
        return "", len(content)
    digits = re.sub(r"\s+", "", content[start + 1 : end])
    if len(digits) % 2:
        digits += "0"
    try:
        raw = bytes.fromhex(digits)
    except ValueError:
        return "", end + 1
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace"), end + 1
    return raw.decode("latin-1"), end + 1


def _content_items(content: str) -> list[tuple[float, float, str]]:
    """Positioned strings from one content stream: (x, y, text) in draw order."""
    items: list[tuple[float, float, str]] = []
    x = y = 0.0
    numbers: list[float] = []
    i = 0
    while i < len(content):
        ch = content[i]
        if ch == "(":
            text, i = _literal_string(content, i)
            items.append((x, y, text))
            numbers = []
            continue
        if ch == "<" and not content.startswith("<<", i):
            text, i = _hex_string(content, i)
            if text:
                items.append((x, y, text))
            numbers = []
            continue
        number = _NUMBER_RE.match(content, i)
        if number is not None:
            numbers.append(float(number.group(0)))
            i = number.end()
            continue
        operator = re.match(r"[A-Za-z'\"*]{1,3}", content[i:])
        if operator is not None:
            op = operator.group(0)
            i += len(op)
            if op == "Tm" and len(numbers) >= 6:
                x, y = numbers[-2], numbers[-1]
            elif op in ("Td", "TD") and len(numbers) >= 2:
                x += numbers[-2]
                y += numbers[-1]
            elif op in ("'", '"') and items:
                pass  # string already captured when its literal was scanned
            numbers = []
            continue
        i += 1
    return items


def _rows_from_items(items: list[tuple[float, float, str]]) -> list[list[str]]:
    """Group positioned strings into table rows (same line, left to right)."""
    placed = sorted((item for item in items if item[2].strip()), key=lambda it: (-it[1], it[0]))
    lines: list[tuple[float, list[tuple[float, str]]]] = []
    for ix, iy, text in placed:
        if lines and abs(iy - lines[-1][0]) <= 3.0:
            lines[-1][1].append((ix, text))
        else:
            lines.append((iy, [(ix, text)]))
    rows: list[list[str]] = []
    for _y, cells in lines:
        cells.sort(key=lambda c: c[0])
        row: list[str] = []
        last_end = -1e18
        for cx, text in cells:
            clean = " ".join(text.split())
            if not clean:
                continue
            if row and cx - last_end <= 12.0:
                row[-1] = (row[-1] + " " + clean).strip()
            else:
                row.append(clean)
            last_end = cx + len(clean) * 5.0
        if row:
            rows.append(row)
    return rows


def extract_pdf_tables(data: bytes) -> PdfDocument:
    """Read tables from a PDF's embedded text layer, in code (no model call).

    Handles the born-digital documents clubs actually receive (league
    and grounds tables exported from Word/Excel/browsers), compressed
    or not. Raises :class:`AgentError` for non-PDF bytes, oversize
    files, and scanned/image-only PDFs with no readable text -- send a
    screenshot or a rendered page image for those instead of guessing.
    """
    if not isinstance(data, (bytes, bytearray)) or not bytes(data).startswith(b"%PDF"):
        raise AgentError("That file is not a PDF. Please send a PDF, a screenshot, text or a voice note.")
    if len(data) > MAX_PDF_BYTES:
        raise AgentError("That PDF is too large; please send a smaller one.")
    pages: list[list[list[str]]] = []
    for stream in _pdf_streams(bytes(data)):
        try:
            content = stream.decode("latin-1")
        except UnicodeDecodeError:
            continue
        if " Tj" not in content and " TJ" not in content and "Tj" not in content:
            continue
        rows = _rows_from_items(_content_items(content))
        if rows:
            pages.append(rows)
    if not pages:
        raise AgentError(
            "That PDF has no readable text (it looks scanned or image-only), so its tables "
            "could not be read. Please send a screenshot of the table, or the PDF page as an image."
        )
    return PdfDocument(pages=pages)


@dataclass
class IngestedRequest:
    """Everything one manager message carried, normalized for the agent.

    ``text`` is the request words (typed + transcribed + text files).
    ``games`` / ``availability`` are the merged, de-duplicated imports
    from every photo and document. ``sources`` records what each input
    was, for the trace; ``notes`` records what was read or dropped, for
    the manager. Nothing here has been applied.
    """

    text: str = ""
    games: ScheduleImport = field(default_factory=ScheduleImport)
    availability: AvailabilityImport = field(default_factory=AvailabilityImport)
    sources: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def availability_busy(self, problem: Problem) -> list[Busy]:
        """Solver busy times from the merged grounds/coach availability."""
        return availability_to_busy(self.availability, problem)

    def busy(self, problem: Problem, *, include_games: bool = True) -> list[Busy]:
        """All solver busy times from this intake, de-duplicated and sorted."""
        out = list(self.availability_busy(problem))
        if include_games:
            out += games_to_busy(self.games, problem)
        seen: set[tuple] = set()
        unique: list[Busy] = []
        for busy in out:
            key = (busy.resource, busy.start, busy.end)
            if key not in seen:
                seen.add(key)
                unique.append(busy)
        unique.sort(key=lambda b: (b.start, b.resource, b.end))
        return unique

    def problem_with_busy(self, problem: Problem, *, include_games: bool = True) -> Problem:
        """Copy of ``problem`` carrying this intake's busy times as well."""
        merged = list(problem.busy)
        have = {(b.resource, b.start, b.end) for b in merged}
        for busy in self.busy(problem, include_games=include_games):
            if (busy.resource, busy.start, busy.end) not in have:
                merged.append(busy)
        return Problem(
            teams=problem.teams, fields=problem.fields,
            season_start=problem.season_start, season_end=problem.season_end,
            timezone=problem.timezone, busy=merged, blackout_dates=problem.blackout_dates,
        )

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict."""
        return {
            "text": self.text,
            "games": self.games.to_dict(),
            "availability": self.availability.to_dict(),
            "sources": [dict(s) for s in self.sources],
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, d: dict) -> IngestedRequest:
        """Build an IngestedRequest from :meth:`to_dict` output."""
        if not isinstance(d, dict):
            raise AgentError("The uploaded information could not be read; please send it again.")
        try:
            return cls(
                text=str(d.get("text", "")),
                games=ScheduleImport.from_dict(d.get("games") or {}),
                availability=AvailabilityImport.from_dict(d.get("availability") or {}),
                sources=[{str(k): str(v) for k, v in s.items()} for s in d.get("sources", []) if isinstance(s, dict)],
                notes=[str(n) for n in d.get("notes", [])],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentError("The uploaded information could not be read; please send it again.") from exc


def ingest_request(
    problem: Problem,
    *,
    text: str | None = None,
    attachments: list[Attachment] | None = None,
    chat: ChatFn,
    voice_config: VoiceConfig | None = None,
    transcribe_fn: TranscribeFn | None = None,
    language: str = "en",
) -> IngestedRequest:
    """Normalize typed text, voice, screenshots and PDFs into one request.

    Text arrives as-is; voice attachments are transcribed to text
    (``transcribe_fn`` wins, else ``transcribe_audio`` with
    ``voice_config``); text files append to the request; every photo is
    read for both league games and grounds/coach availability; every
    PDF's tables are extracted in code and read the same way. All
    imports merge with exact duplicates removed. Raises
    :class:`AgentError` when there is nothing usable, a file kind is
    unsupported, or a PDF has no readable text.
    """
    if text is not None and not isinstance(text, str):
        raise AgentError("Please type the request, say it, or upload it.")
    files = list(attachments or [])
    if len(files) > MAX_ATTACHMENTS:
        raise AgentError(f"Too many files at once; please send at most {MAX_ATTACHMENTS}.")
    parts: list[str] = []
    if text and text.strip():
        parts.append(text.strip())
    sources: list[dict[str, str]] = [
        {"kind": "text", "filename": "", "detail": "typed request"}
    ] if parts else []
    notes: list[str] = []
    schedules: list[ScheduleImport] = []
    availabilities: list[AvailabilityImport] = []

    for item in files:
        if not isinstance(item, Attachment):
            raise AgentError("An upload could not be read; please send it again.")
        kind = item.kind
        name = item.filename or kind
        raw = bytes(item.data)
        if kind == "unknown" or not raw:
            raise AgentError(
                f"Unsupported file {name!r}. Supported: text, voice recordings, "
                "screenshots/photos (PNG/JPEG) and PDFs with readable tables."
            )
        if kind == "text":
            if len(raw) > MAX_TEXT_BYTES:
                raise AgentError(f"That text file {name!r} is too large.")
            try:
                words = raw.decode("utf-8").strip()
            except UnicodeDecodeError as exc:
                raise AgentError(f"That text file {name!r} could not be read as text.") from exc
            if words:
                parts.append(words)
            sources.append({"kind": "text", "filename": name, "detail": "text file"})
        elif kind == "audio":
            if len(raw) > MAX_AUDIO_BYTES:
                raise AgentError("That recording is too long; please send a shorter voice note.")
            if transcribe_fn is not None:
                heard = transcribe_fn(raw, name)
            elif voice_config is not None:
                heard = transcribe_audio(voice_config, raw, filename=name, mime_type=item.mime_type or None)
            else:
                raise AgentError(
                    "Voice notes need transcription configured (Meta voice model) "
                    "before they can be understood. Please type the request or try again later."
                )
            if not isinstance(heard, str) or not heard.strip():
                raise AgentError("I could not hear anything in that recording; please try again.")
            parts.append(heard.strip())
            sources.append({"kind": "audio", "filename": name, "detail": "transcribed voice note"})
            notes.append(f"Read {name} (voice): transcribed to text.")
        elif kind == "image":
            if len(raw) > MAX_IMAGE_BYTES:
                raise AgentError(f"That photo {name!r} is too large; please send a smaller one.")
            mime = item.mime_type if (item.mime_type or "").startswith("image/") else "image/png"
            schedules.append(extract_games(problem, raw, chat, mime_type=mime, language=language))
            availabilities.append(extract_availability(problem, raw, chat, mime_type=mime, language=language))
            sources.append({"kind": "image", "filename": name, "detail": "screenshot read for games and availability"})
            notes.append(f"Read {name} (screenshot): games and ground/coach availability extracted.")
        else:  # pdf
            doc = extract_pdf_tables(raw)
            table_text = doc.text
            schedules.append(extract_games_from_text(problem, table_text, chat, language=language))
            availabilities.append(extract_availability_from_text(problem, table_text, chat, language=language))
            sources.append({
                "kind": "pdf", "filename": name,
                "detail": f"PDF tables: {doc.page_count} page(s), {len(doc.rows())} row(s)",
            })
            notes.append(f"Read {name} (PDF): {len(doc.rows())} table row(s) extracted.")

    games = merge_schedules(schedules)
    availability = merge_availability(availabilities)
    request_text = "\n".join(parts).strip()
    if not request_text and not games.games and not availability.grounds and not availability.coach:
        raise AgentError("Please type the request, say it, or upload a schedule, photo or PDF.")
    return IngestedRequest(
        text=request_text, games=games, availability=availability, sources=sources, notes=notes
    )
