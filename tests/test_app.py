"""Demo app tests: full mock-mode flow, no key, no network, no sockets.

Endpoint functions are called directly (the sandbox forbids even
loopback sockets, so no HTTP client can run here); the browser talks to
the same functions over HTTP.
"""

import os

import pytest

pytest.importorskip("fastapi")  # demo extra: plain installs skip these tests
from fastapi import HTTPException

import app.main as app_main
from app.main import (
    CLUB,
    _stream_kwargs,
    api_approve,
    api_cancel,
    api_notices,
    api_plan,
    api_reschedule,
    api_revise,
    api_stream,
    availability_result,
    get_calendar,
    get_club,
    index,
    make_chat,
    mock_chat,
    stream_events,
    vision_result,
    voice_result,
)

PRESETS = CLUB["presets"]


def _plan() -> dict:
    body = api_plan({"mode": "mock", "request": PRESETS["plan_request"]})
    assert body["proposal"]["events"], "mock plan must place sessions"
    assert all(c["passed"] for c in body["proposal"]["check_results"])
    assert any(s["step"] == "verify" for s in body["trace"])  # coach hook runs
    return body["proposal"]


def test_club_and_index() -> None:
    response = index()
    assert str(response.path).endswith("index.html") and response.path.exists()
    club = get_club()
    assert club["teams"] and club["fields"] and club["presets"]["plan_request"]
    assert "Wednesday" in club["coach_note"]


def test_plan_revise_approve_notices_flow() -> None:
    proposal = _plan()
    revised = api_revise({"mode": "mock", "proposal": proposal, "request": "keep it"})
    assert revised["proposal"]["events"] == proposal["events"]

    result = api_approve({"proposal": proposal})["result"]
    assert result["approved"] is True

    notices = api_notices({"mode": "mock", "events": proposal["events"]})["notices"]
    assert {n["language"] for n in notices["notices"]} == {"es", "en", "vi"}  # union of team languages
    assert all(n["verified"] for n in notices["notices"])  # mock echoes the facts


def test_rain_demo_moves_only_thursday() -> None:
    proposal = _plan()
    before = {(e["starts_at"], e["field_id"]) for e in proposal["events"]}
    assert any(e["starts_at"].startswith("2026-11-12") for e in proposal["events"])
    moved = api_reschedule(
        {"mode": "mock", "events": proposal["events"], "request": PRESETS["rain_request"]}
    )["proposal"]
    assert len(moved["events"]) == len(before)
    assert "->" in moved["summary"]
    thursday = [e for e in moved["events"] if e["starts_at"].startswith("2026-11-12")]
    assert thursday and all(e["field_id"] != "f1" for e in thursday)  # off closed North
    untouched = [e for e in moved["events"] if not e["starts_at"].startswith("2026-11-12")]
    assert all((e["starts_at"], e["field_id"]) in before for e in untouched)
    assert all(c["passed"] for c in moved["check_results"])


def test_cancel_flow() -> None:
    proposal = _plan()
    cancelled = api_cancel(
        {"mode": "mock", "events": proposal["events"], "request": PRESETS["cancel_request"]}
    )["proposal"]
    assert len(cancelled["events"]) < len(proposal["events"])
    assert "Cancelled" in cancelled["summary"]


def _collect_stream(body: dict) -> list:
    """Drive the stream event generator directly: threads + queue only, no sockets."""
    call = body.get("call", "plan")
    return list(stream_events(call, body.get("mode", "mock"), mock_chat, _stream_kwargs(call, body)))


def test_stream_endpoint_is_event_stream() -> None:
    response = api_stream({"mode": "mock", "call": "plan", "request": PRESETS["plan_request"]})
    assert response.media_type == "text/event-stream"


def test_stream_plan_emits_steps_then_result() -> None:
    events = _collect_stream({"mode": "mock", "call": "plan", "request": PRESETS["plan_request"]})
    assert events and events[0]["type"] == "step" and events[0]["phase"] == "start"
    assert events[-1]["type"] == "result"
    assert events[-1]["proposal"]["events"], "streamed mock plan must place sessions"
    labels = [e["label"] for e in events if e["type"] == "step" and e["phase"] == "done"]
    # No "Explaining the summary" step: the demo renders English summaries
    # deterministically instead of spending a slow model call on them.
    assert labels == ["Understanding the request"]
    assert all(isinstance(e["latency_ms"], int) for e in events if e.get("phase") == "done")


def test_stream_cancel_error_is_an_event() -> None:
    events = _collect_stream({"mode": "mock", "call": "cancel", "events": [], "request": "x"})
    assert events[-1]["type"] == "error" and events[-1]["detail"]


def test_stream_rejects_unknown_call() -> None:
    with pytest.raises(HTTPException):
        _collect_stream({"mode": "mock", "call": "dance", "request": "x"})


def test_calendar_is_nov_dec_sample() -> None:
    cal = get_calendar()
    assert cal["season_start"] == "2026-11-01" and cal["season_end"] == "2026-12-31"
    assert cal["team_count"] == 2 and cal["events"]
    assert all("2026-11-01" <= e["starts_at"][:10] <= "2026-12-31" for e in cal["events"])
    assert all(c["passed"] for c in cal["proposal"]["check_results"])


def test_availability_photo_mock_blocks_time() -> None:
    result = availability_result("mock", b"fake-bytes", "image/png")
    assert result["mock"] is True and result["team_count"] == 2
    assert set(result["teams_seen"]) == {"Foxes", "Badgers"}
    assert all(g["matched"] for g in result["availability"]["grounds"])
    assert all(c["matched"] for c in result["availability"]["coach"])
    assert result["busy_count"] > 0 and result["busy"]
    planned = api_plan({"mode": "mock", "request": PRESETS["plan_request"],
                        "extra_busy": result["busy"]})
    assert planned["proposal"]["events"]
    assert all(c["passed"] for c in planned["proposal"]["check_results"])


def test_frontend_shows_current_diff_and_simple_primary() -> None:
    html = index().path.read_text(encoding="utf-8")
    assert 'id="run-btn"' in html and "Create schedule / Apply my wording" in html
    assert 'id="current-calendar"' in html and 'id="diff"' in html
    assert 'id="availability-btn"' in html and "Read ground &amp; coach photo" in html
    assert "Will be cancelled (red)" in html and "CONFLICT" in html


def test_mock_voice_and_vision() -> None:
    voice = voice_result("mock", b"fake-bytes", "note.wav", "audio/wav")
    assert voice["transcript"] and voice["mock"] is True

    vision = vision_result("mock", b"fake-bytes", "image/png")
    assert vision["schedule"]["games"] and vision["mock"] is True

    planned = api_plan({"mode": "mock", "request": PRESETS["plan_request"], "games": vision["schedule"]})
    assert all(c["passed"] for c in planned["proposal"]["check_results"])


def test_errors_are_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr(app_main, "_load_dotenv", lambda: None)  # isolate from a real .env
    with pytest.raises(HTTPException, match="MODEL_API_KEY"):
        api_plan({"mode": "meta", "request": "x"})  # no key, no network attempted
    with pytest.raises(HTTPException):
        api_notices({"mode": "mock", "events": []})


def test_dotenv_file_is_picked_for_meta(
    monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A key stored only in ROOT/.env must satisfy meta mode (no export needed)."""
    for var in ("MODEL_API_KEY", "META_MODEL_API_BASE", "META_MODEL"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / ".env").write_text("MODEL_API_KEY=test-key-123\n", encoding="utf-8")
    monkeypatch.setattr(app_main, "ROOT", tmp_path)
    try:
        assert callable(make_chat("meta"))
    finally:
        for var in ("MODEL_API_KEY", "META_MODEL_API_BASE", "META_MODEL"):
            os.environ.pop(var, None)
