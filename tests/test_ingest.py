"""Unified intake tests: text, voice, images, PDF tables (fake chat, no network)."""

import json
import zlib
from datetime import date, time

import pytest

from schedule_agent import (
    AgentError,
    Attachment,
    Field,
    IngestedRequest,
    Problem,
    ScheduleAgent,
    Team,
    extract_pdf_tables,
    ingest_request,
)
from schedule_agent.vision import availability_to_busy, merge_availability

TZ = "America/Toronto"


def _problem() -> Problem:
    return Problem(
        teams=[Team(id="t1", name="Foxes"), Team(id="t2", name="Badgers")],
        fields=[
            Field(id="f1", name="North Field", open_from=time(17, 0), open_until=time(22, 0)),
            Field(id="f2", name="South Field", open_from=time(17, 0), open_until=time(22, 0)),
        ],
        season_start=date(2026, 11, 2),  # a Monday
        season_end=date(2026, 11, 15),
        timezone=TZ,
    )


def _games_payload(rows=None):  # type: ignore[no-untyped-def]
    return json.dumps({"games": rows or [], "notes": []})


def _avail_payload(grounds=None, coach=None):  # type: ignore[no-untyped-def]
    return json.dumps({"grounds": grounds or [], "coach": coach or [], "notes": []})


_RULES = json.dumps(
    {
        "requirements": [
            {
                "team_alias": "T1", "event_type": "practice", "sessions_per_week": 1,
                "duration_minutes": 60, "allowed_days": [], "allowed_field_aliases": [],
                "earliest_start": "17:00", "latest_end": "20:00", "title": "",
                "active_from": "", "active_until": "",
            }
        ],
        "min_gap_days": 1, "blackout_dates": [], "blackout_ranges": [], "notes": [],
    }
)

_GAME_ROW = {"date": "2026-11-03", "start": "17:00", "home": "Foxes", "away": "Greyhawks", "field": "North Field"}
_NORTH_TUE = {"field": "North Field", "weekdays": [1], "open_from": "17:00", "open_until": "20:00"}
_NORTH_THU = {"field": "North Field", "weekdays": [3], "open_from": "17:00", "open_until": "20:00"}


def _chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
    if response_format is None:
        return "Foxes practice once a week after 5pm."
    name = response_format["json_schema"]["name"]
    if name == "league_games":
        return _games_payload([_GAME_ROW])
    if name == "grounds_availability":
        return _avail_payload([_NORTH_TUE])
    if name == "scheduling_rules":
        return _RULES
    return "{}"


def _pdf(rows: list[list[str]], *, compress: bool = False) -> bytes:
    """Smallest text-layer PDF: one positioned string per cell."""
    ops: list[str] = ["BT /F1 10 Tf"]
    y = 700
    for row in rows:
        x = 50
        for cell in row:
            safe = cell.replace("\\", "").replace("(", "").replace(")", "")
            ops.append(f"1 0 0 1 {x} {y} Tm ({safe}) Tj")
            x += 90 + 8 * len(cell)
        y -= 20
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    raw = zlib.compress(stream) if compress else stream
    filt = " /Filter /FlateDecode" if compress else ""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(raw)).encode() + filt.encode() + b" >>\nstream\n" + raw + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    for i, body in enumerate(objects, start=1):
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    out += b"trailer << /Root 1 0 R >>\n%%EOF\n"
    return bytes(out)


GROUND_ROWS = [
    ["Field", "Days", "Open", "Close"],
    ["North Field", "Tue Thu", "17:00", "20:00"],
]
GAME_ROWS = [
    ["Date", "Start", "Home", "Away", "Field"],
    ["2026-11-03", "17:00", "Foxes", "Greyhawks", "North Field"],
]


def test_text_only_passes_through() -> None:
    got = ingest_request(_problem(), text="Foxes Mondays after 5pm", chat=_chat)
    assert got.text == "Foxes Mondays after 5pm"
    assert got.sources == [{"kind": "text", "filename": "", "detail": "typed request"}]
    assert got.games.games == [] and got.availability.grounds == []
    assert IngestedRequest.from_dict(got.to_dict()) == got


def test_text_file_attachment_appends_to_request() -> None:
    note = Attachment.text("note.txt", "Badgers Thursdays after 5pm")
    got = ingest_request(_problem(), text="Foxes Mondays", attachments=[note], chat=_chat)
    assert got.text == "Foxes Mondays\nBadgers Thursdays after 5pm"
    assert got.sources[-1]["kind"] == "text"


def test_voice_attachment_is_transcribed_to_text() -> None:
    heard: list = []

    def fake_transcribe(audio: bytes, filename: str) -> str:
        heard.append((audio, filename))
        return "Foxes practice Mondays after 5pm"

    clip = Attachment.audio("note.wav", b"RIFFfake", mime_type="audio/wav")
    got = ingest_request(_problem(), attachments=[clip], chat=_chat, transcribe_fn=fake_transcribe)
    assert got.text == "Foxes practice Mondays after 5pm"
    assert heard == [(b"RIFFfake", "note.wav")]
    assert got.sources[0]["kind"] == "audio"


def test_voice_without_transcriber_fails_safely() -> None:
    clip = Attachment.audio("note.wav", b"RIFFfake", mime_type="audio/wav")
    with pytest.raises(AgentError, match="transcribe|Voice"):
        ingest_request(_problem(), attachments=[clip], chat=_chat)


def test_image_reads_games_and_availability() -> None:
    shot = Attachment.image("grounds.png", b"\x89PNGdata")
    got = ingest_request(_problem(), text="plan", attachments=[shot], chat=_chat)
    assert [g.matched for g in got.games.games] == [False]  # Greyhawks is external
    assert got.games.games[0].blocks_time is True
    assert [g.matched for g in got.availability.grounds] == [True]
    assert got.availability.grounds[0].weekdays == [1]


def test_pdf_tables_extracted_from_text_layer() -> None:
    for compress in (False, True):
        doc = extract_pdf_tables(_pdf(GROUND_ROWS, compress=compress))
        assert doc.page_count == 1
        assert doc.rows()[1] == GROUND_ROWS[1]
        assert "North Field" in doc.text


def test_pdf_ground_table_understood_like_a_photo() -> None:
    # type: ignore[no-untyped-def]
    def chat(messages, *, response_format=None):
        if response_format is not None and response_format["json_schema"]["name"] == "grounds_availability":
            assert "North Field" in messages[-1]["content"]  # table text, not an image
            return _avail_payload([_NORTH_TUE, _NORTH_THU])
        return _chat(messages, response_format=response_format)

    pdf = Attachment.pdf("grounds.pdf", _pdf(GROUND_ROWS))
    got = ingest_request(_problem(), text="plan", attachments=[pdf], chat=chat)
    assert [sorted(g.weekdays) for g in got.availability.grounds] == [[1], [3]]
    assert got.sources[0]["kind"] == "pdf"


def test_pdf_game_table_becomes_blocking_games() -> None:
    pdf = Attachment.pdf("games.pdf", _pdf(GAME_ROWS))
    got = ingest_request(_problem(), text="plan", attachments=[pdf], chat=_chat)
    assert got.games.blocking_games()[0].home_id == "t1"
    assert got.games.games[0].away_id is None  # external opponent, never guessed


def test_scanned_pdf_without_text_layer_fails_safely() -> None:
    scanned = b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n%%EOF\n"
    with pytest.raises(AgentError, match="no readable text|clearer|screenshot"):
        extract_pdf_tables(scanned)
    with pytest.raises(AgentError):
        ingest_request(_problem(), text="plan", attachments=[Attachment.pdf("scan.pdf", scanned)], chat=_chat)


def test_multiple_screenshots_merge_grounds_by_field() -> None:
    """Two partial ground photos (Tue, Thu) must open both days, not block each other."""
    calls = {"n": 0}

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        name = response_format["json_schema"]["name"] if response_format else ""
        if name == "grounds_availability":
            calls["n"] += 1
            return _avail_payload([_NORTH_TUE] if calls["n"] == 1 else [_NORTH_THU])
        if name == "league_games":
            return _games_payload([])
        return _chat(messages, response_format=response_format)

    shots = [Attachment.image("a.png", b"a"), Attachment.image("b.png", b"b")]
    got = ingest_request(_problem(), text="plan", attachments=shots, chat=chat)
    merged = merge_availability([got.availability])
    busy = availability_to_busy(merged, _problem())
    from datetime import datetime
    from zoneinfo import ZoneInfo

    def covers(day: str, hour: int) -> bool:
        moment = datetime.fromisoformat(f"{day}T{hour:02d}:00").replace(tzinfo=ZoneInfo(TZ))
        return any(b.resource == "field:f1" and b.start <= moment < b.end for b in busy)

    assert not covers("2026-11-03", 18)  # Tuesday open window survives the Thu-only photo
    assert not covers("2026-11-05", 18)  # Thursday open window survives the Tue-only photo
    assert covers("2026-11-02", 18)  # Monday was never opened: blocked


def test_duplicate_games_and_busy_deduped() -> None:
    shots = [Attachment.image("a.png", b"a"), Attachment.image("a-copy.png", b"a")]
    got = ingest_request(_problem(), text="plan", attachments=shots, chat=_chat)
    assert len(got.games.games) == 1
    assert len(got.busy(_problem())) == len({(b.resource, b.start, b.end) for b in got.busy(_problem())})


def test_handle_ingested_end_to_end() -> None:
    pdf = Attachment.pdf("grounds.pdf", _pdf(GROUND_ROWS))

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        name = response_format["json_schema"]["name"] if response_format else ""
        if name == "grounds_availability":
            south_tue = {"field": "South Field", "weekdays": [1], "open_from": "17:00", "open_until": "20:00"}
            south_thu = {"field": "South Field", "weekdays": [3], "open_from": "17:00", "open_until": "20:00"}
            return _avail_payload([_NORTH_TUE, _NORTH_THU, south_tue, south_thu])
        return _chat(messages, response_format=response_format)

    problem = _problem()
    ingested = ingest_request(problem, text="Foxes practice once a week after 5pm",
                              attachments=[pdf], chat=chat)
    agent = ScheduleAgent(chat, deterministic_summary=True)
    proposal = agent.handle_ingested(problem, ingested)
    assert proposal.events  # practice placed on an open day (Tue/Thu)
    assert all(e.starts_at.weekday() in (1, 3) for e in proposal.events if e.event_type == "practice")
    assert any(e.event_type == "game" for e in proposal.events)  # uploaded game kept fixed
    assert all(c.passed for c in proposal.check_results)
    assert agent.approve(proposal).approved is True
    assert any("grounds.pdf" in n for n in proposal.rules.notes)


def test_empty_and_unsupported_input_fails_safely() -> None:
    with pytest.raises(AgentError):
        ingest_request(_problem(), chat=_chat)
    with pytest.raises(AgentError, match="supported|Unsupported"):
        ingest_request(_problem(), text="plan",
                       attachments=[Attachment(filename="x.zip", mime_type="application/zip", data=b"PK")],
                       chat=_chat)
