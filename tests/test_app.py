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
    assert len(club["teams"]) == 4 and len(club["coaches"]) == 2
    assert club["coaches"][0]["back_to_back"] == [["Foxes", "Badgers"]]
    assert club["coaches"][1]["back_to_back"] == [["Otters", "Wolves"]]


def test_plan_revise_approve_notices_flow() -> None:
    proposal = _plan()
    revised = api_revise({"mode": "mock", "proposal": proposal, "request": "keep it"})
    assert revised["proposal"]["events"] == proposal["events"]

    result = api_approve({"proposal": proposal})["result"]
    assert result["approved"] is True

    notices = api_notices({"mode": "mock", "events": proposal["events"]})["notices"]
    assert {n["language"] for n in notices["notices"]} == {"es", "en", "vi", "fr"}  # union of team languages
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
    assert cal["season_start"] == "2026-11-01" and cal["season_end"] == "2026-11-30"
    assert cal["team_count"] == 4 and cal["coach_count"] == 2 and cal["events"]
    assert all("2026-11-01" <= e["starts_at"][:10] <= "2026-11-30" for e in cal["events"])
    assert all(c["passed"] for c in cal["proposal"]["check_results"])
    assert {e["team_id"] for e in cal["events"]} == {f"t{i}" for i in range(1, 5)}


def test_coaches_train_back_to_back() -> None:
    """Each coach's pair runs consecutively: 17:00 then 18:00, same field."""
    events = get_calendar()["events"]
    by_start = {(e["starts_at"][:10], e["team_id"]): e for e in events}
    # A full week in mid-season: Mon Nov 9 - Fri Nov 13, 2026.
    pairs = [
        ("2026-11-09", "t1", "t2", "f1"),  # Coach 1, Monday, North
        ("2026-11-12", "t3", "t4", "f1"),  # Coach 2, Thursday, North
    ]
    for day, first, second, field_id in pairs:
        a, b = by_start[(day, first)], by_start[(day, second)]
        assert a["starts_at"][11:16] == "17:00" and a["ends_at"][11:16] == "18:00"
        assert b["starts_at"][11:16] == "18:00" and b["ends_at"][11:16] == "19:00"
        assert a["ends_at"] == b["starts_at"]  # back-to-back, no gap, no overlap
        assert a["field_id"] == b["field_id"] == field_id


def test_availability_photo_mock_blocks_time() -> None:
    result = availability_result("mock", b"fake-bytes", "image/png")
    assert result["mock"] is True and result["team_count"] == 4
    assert set(result["teams_seen"]) == {"Foxes", "Otters"}  # one team per coach
    assert all(g["matched"] for g in result["availability"]["grounds"])
    assert all(c["matched"] for c in result["availability"]["coach"])
    assert result["busy_count"] > 0 and result["busy"]
    planned = api_plan({"mode": "mock", "request": PRESETS["plan_request"],
                        "extra_busy": result["busy"]})
    assert planned["proposal"]["events"]
    assert all(c["passed"] for c in planned["proposal"]["check_results"])


def test_add_at_taken_time_asks_before_adding() -> None:
    """Badgers Thu 5pm is taken, so an 18:00 placement must ask, not silently add."""
    from datetime import datetime, time as dtime
    from zoneinfo import ZoneInfo

    from schedule_agent import Proposal, ProposedEvent, Requirement, Rules

    from app.main import _add_clarification, club_problem

    tz = ZoneInfo("America/Toronto")
    day = "2026-11-19"

    def ev(team: str, field_id: str, start: str, end: str) -> ProposedEvent:
        return ProposedEvent(team_id=team, field_id=field_id, event_type="practice",
                             title=f"{team} practice",
                             starts_at=datetime.fromisoformat(f"{day}T{start}:00").replace(tzinfo=tz),
                             ends_at=datetime.fromisoformat(f"{day}T{end}:00").replace(tzinfo=tz))

    before = [ev("t3", "f1", "17:00", "18:00")]  # Otters already at 5pm, North
    added = ev("t2", "f1", "18:00", "19:00")      # Badgers pushed to 6-7pm
    rules = Rules(requirements=[Requirement(
        team_id="t2", event_type="practice", sessions_per_week=1, duration_minutes=60,
        allowed_days=[3], earliest_start=dtime(17, 0), latest_end=dtime(20, 0))])
    question = _add_clarification(club_problem(), before, Proposal(rules=rules, events=before + [added]))
    assert question is not None
    assert "Badgers" in question["question"] and "5:00 PM" in question["question"]
    assert "6:00" in question["question"] and "Do you want to make any other changes" in question["question"]
    assert question["blockers"] and question["blockers"][0]["team"] == "Otters"

    # Same team already that day at 5pm, second session at 6pm: also ask.
    own = [ev("t2", "f1", "17:00", "18:00")]
    question2 = _add_clarification(club_problem(), own, Proposal(rules=rules, events=own + [added]))
    assert question2 is not None and "second" in question2["question"]

    # Placed exactly at the requested 5pm with a free day: no question.
    free = Proposal(rules=rules, events=[ev("t2", "f2", "17:00", "18:00")])
    assert _add_clarification(club_problem(), before, free) is None


def test_mock_answers_the_request_it_was_given() -> None:
    """Mock is scripted, but a named team/day must not get another team's rules."""
    wolves = api_plan({"mode": "mock", "request": "Wolves practice once a week, 60 minutes, Fridays after 5pm"})
    assert wolves["proposal"]["events"]
    assert {e["team_id"] for e in wolves["proposal"]["events"]} == {"t4"}
    assert {e["starts_at"][8:10] for e in wolves["proposal"]["events"]}  # placed, Fridays below
    from datetime import date

    assert {date.fromisoformat(e["starts_at"][:10]).weekday() for e in wolves["proposal"]["events"]} == {4}
    assert any("Mock mode" in n for n in wolves["proposal"]["rules"]["notes"])


def test_modes_report_booleans_only() -> None:
    modes = get_club()["modes"]
    assert set(modes) == {"mock", "meta", "open"}
    assert modes["mock"] is True
    assert all(isinstance(v, bool) for v in modes.values())  # never key material


def test_frontend_shows_current_diff_and_simple_primary() -> None:
    html = index().path.read_text(encoding="utf-8")
    assert 'id="run-btn"' in html and "Create schedule / Apply my wording" in html
    assert 'id="current-calendar"' in html and 'id="diff"' in html
    assert 'id="availability-btn"' in html and "Read ground &amp; coach photo" in html
    assert "can be wrong" in html and "modes.meta" in html  # real mode preferred, mock warned
    assert "Mock has pre-defined questions and answers" in html and 'id="mock-note"' in html
    assert "Muse Spark via Baseten" in html and "hosted via Baseten" in html
    # Pinned tabs scroll to Plan / Review / Approve; sessions collapse by week.
    assert 'href="#plan"' in html and 'href="#review"' in html and 'href="#approve"' in html
    assert "position: sticky" in html and "Expand all sessions" in html and "Minimize sessions" in html
    assert "Week of " in html and 'details class=' in html
    assert "Will be cancelled (red)" in html and "CONFLICT" in html
    # Waiting signal: spinner banner appears and the button locks while running.
    assert 'id="working"' in html and 'class="spinner"' in html
    assert "do not press the button again" in html and "setBusy(true" in html and "setBusy(false)" in html
    # Clarification: an off-time add asks before anyone can approve it.
    assert 'id="clarify"' in html and "Quick question before you approve" in html
    assert "clarify-keep" in html and "clarify-discard" in html


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


def test_dotenv_found_via_env_file_and_compose() -> None:
    """Docker path: compose auto-loads .env; the app also honours $ENV_FILE."""
    import pathlib

    compose = pathlib.Path(__file__).parent.parent / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    assert "env_file:" in text and ".env" in text and "8000:8000" in text


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


def test_dotenv_via_env_file_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A .env outside the repo (pointed to by $ENV_FILE) also works."""
    for var in ("MODEL_API_KEY", "META_MODEL_API_BASE", "META_MODEL"):
        monkeypatch.delenv(var, raising=False)
    outside = tmp_path / "elsewhere.env"
    outside.write_text("MODEL_API_KEY=outside-key-456\n", encoding="utf-8")
    monkeypatch.setattr(app_main, "ROOT", tmp_path / "no-repo-here")
    monkeypatch.setenv("ENV_FILE", str(outside))
    try:
        assert callable(make_chat("meta"))
    finally:
        for var in ("MODEL_API_KEY", "META_MODEL_API_BASE", "META_MODEL"):
            os.environ.pop(var, None)
