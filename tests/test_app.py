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
    # Plan the season fills November; every other call may use the whole season, December included.
    assert club_problem(plan_window=True).season_end == date(2026, 11, 30)
    problem = club_problem()
    assert problem.season_end == date(2026, 12, 31) and not problem.blackout_dates
    assert all(f.open_from is not None and f.open_until is not None for f in problem.fields)
    # The plan's rules keep the November window, so revising the draft stays in November.
    assert all(r["active_until"] == "2026-11-30" for r in _plan()["rules"]["requirements"])
    assert all(c["passed"] for c in cal["proposal"]["check_results"])
    assert {e["team_id"] for e in cal["events"]} == {f"t{i}" for i in range(1, 5)}



def test_demo_grounds_open_weekend_days_all_season() -> None:
    """Both demo grounds open 09:00-20:00 Sat/Sun every week; weekdays stay 17:00-20:00."""
    from datetime import datetime, time, timedelta
    from zoneinfo import ZoneInfo

    problem = club_problem()
    tz = ZoneInfo(problem.timezone)
    closed = [b for b in problem.busy if b.resource.startswith("field:")]
    assert all(f.open_from == time(9) and f.open_until == time(20) for f in problem.fields)
    day = problem.season_start
    while day <= problem.season_end:
        for f in problem.fields:
            mine = [b for b in closed if b.resource == f"field:{f.id}" and b.start.date() == day]
            if day.weekday() >= 5:
                assert not mine, day  # open all day Sat/Sun, every week of the season
            else:
                assert [(b.start, b.end) for b in mine] == [
                    (datetime.combine(day, time(9), tzinfo=tz), datetime.combine(day, time(17), tzinfo=tz))]
        day += timedelta(days=1)

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


def test_frontend_three_buttons_plan_run_approve() -> None:
    html = index().path.read_text(encoding="utf-8")
    # Exactly three primary actions, in order: Plan the season, Run, Approve.
    assert html.count('class="primary"') == 3
    assert html.index('id="plan-btn"') < html.index('id="run-btn"') < html.index('id="approve-btn"')
    assert ">Plan the season</button>" in html and ">Run</button>" in html and ">Approve</button>" in html
    assert "presets.plan_request" in html  # Plan uses the club's standard rules
    # Plan shows what it will build before it runs, and a new season is reviewed as its weekly pattern.
    assert html.index('id="plan-preview"') < html.index('id="plan-btn"') and "What will be built" in html
    assert "renderPlanPreview()" in html and "patternHtml(S.events)" in html and "Check the weekly pattern" in html
    assert 'fetch("/api/calendar")' in html and "The schedule it will create" in html
    # Review compares with the approved schedule, so the red list is everything Approve saves.
    assert "S.base = S.current.slice();" in html
    # Notices cover only what changed since the last approval, for the affected teams.
    assert 'post("/api/notices", { events: changed })' in html and "CANCELLED: " in html and "moved from" in html
    # The sample league games include one on a practice slot, so the demo shows a clash being moved.
    assert 'game("2026-11-16", "17:00:00", "North Field")' in html
    # Who did what: the model line names the model calls; times come from the solver.
    assert 'id="by-model"' in html and "placed by the solver and checked in code" in html
    # One request box: typing, speaking and attaching all feed Run.
    assert 'id="request"' in html and 'id="record-btn"' in html and 'id="ingest-files"' in html
    assert 'addEventListener("change", readFiles)' in html  # attaching reads at once, no extra button
    assert 'id="ingest-btn"' not in html and 'id="record-stop"' not in html
    # Speak records 16 kHz mono WAV (the format the endpoint takes), never WebM.
    assert "voice-note.wav" in html and "toWav16k" in html and "MediaRecorder" not in html
    # Real model preferred when a key exists; mock clearly labelled.
    assert "modes.meta" in html and 'id="mock-note"' in html
    # Mock locks free input and offers only requests it answers correctly.
    assert 'id="mock-request"' in html and '$("request").disabled = mock' in html
    assert '$("ingest-files").disabled = mock' in html and "payload.games = mockGames()" in html
    for preset in ("Rain Thursday November 12, North Field is closed.", "Cancel all Thursday sessions."):
        assert 'value="' + preset + '"' in html
    # Result: changes in red, checks, questions block approval, details folded away.
    assert 'id="diff"' in html and 'tr class=\\"" + cls' in html and "cancelled" in html
    assert 'id="checks"' in html and "Conflicts — fix these before approving" in html and "CONFLICT" in html
    assert 'id="questions"' in html and "question-answer" in html and "question-send" in html
    assert "Week of " in html and "<summary>Full schedule</summary>" in html
    assert html.index('id="questions"') < html.index('id="diff"') < html.index('id="checks"')
    # Waiting signal: spinner banner, buttons lock while running.
    assert 'id="working"' in html and 'class="spinner"' in html and "setBusy(true" in html and "setBusy(false)" in html
    # Approve drafts the family notices itself (no separate button).
    assert "/api/notices" in html and 'id="notices-btn"' not in html
    # Nothing extra: no tabs, examples, reload, expand/minimize, trace or understood chips.
    for gone in ('href="#plan"', "preset-rain", "reset-btn", "Expand all sessions", 'id="trace"', 'id="understood"', "Tap to select"):
        assert gone not in html, gone


def test_mock_voice_and_vision() -> None:
    voice = voice_result("mock", b"fake-bytes", "note.wav", "audio/wav")
    assert voice["transcript"] and voice["mock"] is True

    vision = vision_result("mock", b"fake-bytes", "image/png")
    assert vision["schedule"]["games"] and vision["mock"] is True

    planned = api_plan({"mode": "mock", "request": PRESETS["plan_request"], "games": vision["schedule"]})
    assert all(c["passed"] for c in planned["proposal"]["check_results"])


def test_voice_bad_recording_is_400_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recording the transcriber rejects up front is a readable 400."""
    from schedule_agent import AgentError

    monkeypatch.setattr(app_main, "_load_dotenv", lambda: None)  # isolate from a real .env
    monkeypatch.setenv("MODEL_API_KEY", "k")

    def reject(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AgentError("That WAV recording won't transcribe (stereo); please send mono audio.")

    monkeypatch.setattr(app_main, "transcribe_audio", reject)
    with pytest.raises(HTTPException) as caught:
        voice_result("meta", b"RIFFfake", "note.wav", "audio/wav")
    assert caught.value.status_code == 400
    assert "mono" in str(caught.value.detail)


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
    assert "correctedRequest(a)" in html and "ES_NAMES" in html  # answers correct the request in place
