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
    api_add,
    api_approve,
    api_cancel,
    api_notices,
    api_plan,
    api_reschedule,
    api_revise,
    api_stream,
    availability_result,
    club_problem,
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
    from datetime import date

    cal = get_calendar()
    assert cal["season_start"] == "2026-11-01" and cal["season_end"] == "2026-12-31"
    assert cal["scheduling_end"] == "2026-11-30"
    assert cal["team_count"] == 4 and cal["coach_count"] == 2 and cal["events"]
    assert all("2026-11-01" <= e["starts_at"][:10] <= "2026-11-30" for e in cal["events"])
    assert all(not s["week_start"].startswith("2026-12") for s in cal["proposal"]["shortfalls"])  # no December shortfall noise
    problem = club_problem()
    assert problem.season_end == date(2026, 11, 30)  # solver never places in December
    assert date(2026, 12, 1) in problem.blackout_dates and date(2026, 12, 31) in problem.blackout_dates
    assert all(f.open_from is not None and f.open_until is not None for f in problem.fields)  # grounds stay open
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


def test_add_accepts_when_ground_reports_unavailable() -> None:
    """Ground busy from a photo must not block an explicit add in the demo."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from schedule_agent import Busy

    tz = ZoneInfo("America/Toronto")
    busy = [
        Busy(resource=f"field:{fid}", start=datetime(2026, 11, 1, 0, 0, tzinfo=tz),
             end=datetime(2026, 11, 1, 23, 59, tzinfo=tz), reason="ground unavailable").to_dict()
        for fid in ("f1", "f2")
    ]
    proposal = _plan()
    added = api_add({"mode": "mock", "events": proposal["events"],
                     "request": "Add a game", "extra_busy": busy})
    assert len(added["proposal"]["events"]) == len(proposal["events"]) + 1
    assert not added["proposal"]["shortfalls"]
    assert "Ground availability is not verified" in added["proposal"]["summary"]
    assert all(c["passed"] for c in added["proposal"]["check_results"])


def test_add_at_taken_time_asks_before_adding() -> None:
    """Badgers Thu 5pm is taken, so an 18:00 placement must ask (library questions)."""
    from datetime import date as ddate, datetime, time as dtime
    from zoneinfo import ZoneInfo

    from schedule_agent import EditOp, EditPlan, Placement, ProposedEvent, build_edit_proposal

    from app.main import api_run, club_problem

    tz = ZoneInfo("America/Toronto")
    day = "2026-11-19"

    def ev(team: str, field_id: str, start: str, end: str) -> ProposedEvent:
        return ProposedEvent(team_id=team, field_id=field_id, event_type="practice",
                             title=f"{team} practice",
                             starts_at=datetime.fromisoformat(f"{day}T{start}:00").replace(tzinfo=tz),
                             ends_at=datetime.fromisoformat(f"{day}T{end}:00").replace(tzinfo=tz))

    before = [ev("t3", "f1", "17:00", "18:00")]  # Otters already at 5pm, North
    plan = EditPlan(ops=[EditOp(
        action="create_once", team_id="t2", event_type="practice", duration_minutes=60, title="Badgers practice",
        to=Placement(date=ddate(2026, 11, 19), weekdays=[3], field_id="f1",
                     earliest_start=dtime(17, 0), latest_end=dtime(20, 0), exactness="any"),
    )])
    proposal = build_edit_proposal(club_problem(), before, plan)
    assert proposal.questions, "a shifted add must ask, not silently add"
    question = proposal.questions[0]
    assert "Badgers" in question and "5:00 PM" in question and "6:00" in question
    assert "Do you want to make any other changes" in question

    # The demo surfaces the same library questions (no app-side clarification).
    result = api_run({"mode": "mock", "request": "Move the Badgers Thursday practice to Friday 6pm",
                      "events": [e.to_dict() for e in before]})
    assert "questions" in result and "understood" in result

    # Placed exactly at the requested 5pm with a free day: no question.
    free_plan = EditPlan(ops=[EditOp(
        action="create_once", team_id="t2", event_type="practice", duration_minutes=60, title="Badgers practice",
        to=Placement(date=ddate(2026, 11, 19), field_id="f2", earliest_start=dtime(17, 0), latest_end=dtime(20, 0), exactness="any"),
    )])
    assert build_edit_proposal(club_problem(), before, free_plan).questions == []


def test_demo_run_shows_understood_and_sends_selected() -> None:
    from app.main import api_run

    proposal = _plan()
    events = proposal["events"]
    sid = events[0].get("session_id")
    # Calendar payload carries tap-to-select ids (library session keys).
    cal = get_calendar()
    assert all(e.get("session_id") for e in cal["events"])
    moved = api_run({"mode": "mock", "request": "Move the Badgers Thursday practice to Friday 6pm",
                     "events": events, "selected_session_ids": [sid] if sid else []})
    assert moved["understood"], "What I understood chips must be present"
    assert "move" in moved["understood"][0]["text"] and moved["understood"][0]["exactness"] in ("exact", "preferred", "any")
    assert "questions" in moved and "clarification" in moved
    questioned = api_run({"mode": "mock", "request": "do something vague", "events": events})
    assert questioned["proposal"]["questions"] or questioned["questions"]


def test_mock_answers_the_request_it_was_given() -> None:
    """Mock is scripted, but a named team/day must not get another team's rules."""
    wolves = api_plan({"mode": "mock", "request": "Wolves practice once a week, 60 minutes, Fridays after 5pm"})
    assert wolves["proposal"]["events"]
    assert {e["team_id"] for e in wolves["proposal"]["events"]} == {"t4"}
    assert {e["starts_at"][8:10] for e in wolves["proposal"]["events"]}  # placed, Fridays below
    from datetime import date

    assert {date.fromisoformat(e["starts_at"][:10]).weekday() for e in wolves["proposal"]["events"]} == {4}
    assert any("Mock mode" in n for n in wolves["proposal"]["rules"]["notes"])


def _uploaded_games() -> dict:
    """The reported upload: our teams vs external Greyhawks opponents, Nov 14."""
    rows = [
        ("2026-11-14", "17:00", "Foxes", "t1", "Greyhawks Comets", "North Field", "f1"),
        ("2026-11-14", "17:00", "Otters", "t3", "Greyhawks Ravens", "South Field", "f2"),
        ("2026-11-14", "18:00", "Badgers", "t2", "Greyhawks Lynx", "North Field", "f1"),
        ("2026-11-14", "18:00", "Wolves", "t4", "Greyhawks Thunder", "South Field", "f2"),
    ]
    return {"games": [
        {"date": d, "start": s, "home": home, "away": away, "field": field,
         "home_id": hid, "away_id": None, "field_id": fid, "matched": False,
         "blocks_time": True, "note": f"unknown team {away!r}; row kept, never guessed."}
        for d, s, home, hid, away, field, fid in rows
    ], "notes": []}


def test_plan_around_uploaded_games_with_no_typed_request() -> None:
    """'Plan around these games' with an empty box plans the standard practices."""
    planned = api_plan({"mode": "mock", "request": "", "games": _uploaded_games()})
    assert planned["proposal"]["events"]
    assert planned["proposal"]["shortfalls"] == []  # no spurious Nov 29 Thu shortfall
    assert all(c["passed"] for c in planned["proposal"]["check_results"])
    # The Nov 14 games themselves are in the proposal, not just busy time.
    games = [e for e in planned["proposal"]["events"] if e["event_type"] == "game"]
    assert [(g["starts_at"][:16], g["title"], g["field_id"]) for g in games] == [
        ("2026-11-14T17:00", "Foxes v Greyhawks Comets", "f1"),
        ("2026-11-14T17:00", "Otters v Greyhawks Ravens", "f2"),
        ("2026-11-14T18:00", "Badgers v Greyhawks Lynx", "f1"),
        ("2026-11-14T18:00", "Wolves v Greyhawks Thunder", "f2"),
    ]
    assert len(planned["proposal"]["events"]) == 22  # 18 practices + 4 games
    assert "Greyhawks Comets" in planned["proposal"]["summary"]

    streamed = _collect_stream({"mode": "mock", "call": "plan", "request": "", "games": _uploaded_games()})
    assert streamed[-1]["type"] == "result" and streamed[-1]["proposal"]["events"]


def test_uploaded_games_actually_block_their_slot() -> None:
    """A Saturday Foxes practice must not land in the Foxes game slot."""
    planned = api_plan({"mode": "mock", "request": "Foxes practice once a week, 60 minutes, Saturdays after 5pm",
                        "games": _uploaded_games()})
    assert all(c["passed"] for c in planned["proposal"]["check_results"])
    nov14 = [e for e in planned["proposal"]["events"] if e["starts_at"].startswith("2026-11-14")]
    practices = [e for e in nov14 if e["event_type"] == "practice"]
    assert practices and all(not e["starts_at"].startswith("2026-11-14T17:00") for e in practices)
    assert any(e["event_type"] == "game" and e["starts_at"].startswith("2026-11-14T17:00") for e in nov14)


def test_plan_around_games_that_matched_nothing_fails_clearly() -> None:
    games = {"games": [{"date": "2026-11-14", "start": "17:00", "home": "Strangers",
                        "away": "Visitors", "field": "Elsewhere", "home_id": None,
                        "away_id": None, "field_id": None, "matched": False,
                        "blocks_time": False, "note": "unknown"}], "notes": []}
    with pytest.raises(HTTPException, match="None of those games matched"):
        api_plan({"mode": "mock", "request": "", "games": games})


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
    # Review order: shortfalls at the top (red on conflict), checks at the bottom.
    assert html.index('id="shortfalls"') < html.index('id="diff"') < html.index('id="summary"')
    assert html.index('id="calendar"') < html.index('id="checks"')
    assert "alert-red" in html and "Conflict — could not be placed" in html
    assert "Grounds stay open in December" in html and "no sessions are scheduled there" in html
    # Waiting signal: spinner banner appears and the button locks while running.
    assert 'id="working"' in html and 'class="spinner"' in html
    assert "do not press the button again" in html and "setBusy(true" in html and "setBusy(false)" in html
    # Questions come from the library (proposal.questions); the app just shows them.
    assert 'id="questions"' in html and "Quick question before you approve" in html
    assert "question-answer" in html and "question-send" in html
    # What I understood: one chip per change, before the result detail.
    assert 'id="understood"' in html and "What I understood" in html
    assert html.index('id="understood"') < html.index('id="diff"')
    # Tap-to-select sessions: selected ids are sent with /api/run.
    assert "data-session" in html and "selected_session_ids: S.selected" in html and "Tap to select" in html


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

def test_spanish_names_stay_english_and_language_detected() -> None:
    from app.main import _english_club_names, _request_language

    assert _english_club_names("Los Zorros en Campo Norte") == "Foxes en North Field"
    assert _request_language({"request": "Los Zorros practican los jueves"}) == "es"
    assert _request_language({"request": "Foxes practice on Thursdays"}) == "en"
    html = index().path.read_text(encoding="utf-8")
    assert "correctedRequest" in html and "correct your request in place" in html
