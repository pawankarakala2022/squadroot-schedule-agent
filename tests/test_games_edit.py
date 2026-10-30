"""Uploaded league games on a saved calendar: fixed events, clashes moved, no invented request."""

import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from schedule_agent import (
    Field,
    Problem,
    ProposedEvent,
    ScheduleAgent,
    ScheduleImport,
    Team,
    build_edit_proposal,
)
from schedule_agent.edits import EditPlan
from schedule_agent.vision import _clean

TZ = "America/Toronto"
ZONE = ZoneInfo(TZ)


def _problem() -> Problem:
    return Problem(
        teams=[Team(id="t1", name="Foxes"), Team(id="t2", name="Badgers")],
        fields=[
            Field(id="f1", name="North Field", open_from=time(17, 0), open_until=time(20, 0)),
            Field(id="f2", name="South Field", open_from=time(17, 0), open_until=time(20, 0)),
        ],
        season_start=date(2026, 11, 2),  # a Monday
        season_end=date(2026, 11, 29),
        timezone=TZ,
    )


def _practice(team_id: str, day: int, hour: int, field_id: str = "f1") -> ProposedEvent:
    start = datetime(2026, 11, day, hour, 0, tzinfo=ZONE)
    return ProposedEvent(team_id=team_id, field_id=field_id, event_type="practice",
                         title=f"{team_id} practice", starts_at=start, ends_at=start.replace(hour=hour + 1))


def _calendar() -> list[ProposedEvent]:
    # Foxes Mon 17:00, Badgers Mon 18:00, North Field, every week.
    return [_practice("t1", d, 17) for d in (2, 9, 16, 23)] + [_practice("t2", d, 18) for d in (2, 9, 16, 23)]


def _games(*rows: tuple[str, int, str]) -> ScheduleImport:
    """League rows as the model reads a PDF: printed ground name, "Vs" opponent."""
    raw = {"games": [{"date": f"2026-11-{day:02d}", "start": start, "home": "Foxes",
                      "away": "Vs Gamblers", "field": ground} for ground, day, start in rows], "notes": []}
    schedule, issues = _clean(raw, _problem())
    assert schedule is not None and not issues
    return schedule


def _no_model(messages, *, response_format=None):  # type: ignore[no-untyped-def]
    name = response_format["json_schema"]["name"] if response_format else "explain"
    if name == "explain":
        return "League games added."
    raise AssertionError(f"files-only games must not call the model for {name}")


def test_short_ground_name_and_versus_prefix_match() -> None:
    game = _games(("South", 7, "09:00")).games[0]
    assert (game.field_id, game.home_id, game.away) == ("f2", "t1", "Gamblers")
    assert game.blocks_time and not game.matched  # external opponent stays unmatched, never guessed


def test_ambiguous_short_ground_name_is_not_guessed() -> None:
    problem = _problem()
    problem.fields.append(Field(id="f3", name="South Park"))
    raw = {"games": [{"date": "2026-11-07", "start": "09:00", "home": "Foxes", "away": "Gamblers",
                      "field": "South"}], "notes": []}
    schedule, _ = _clean(raw, problem)
    assert schedule is not None and schedule.games[0].field_id is None


def test_games_only_on_saved_calendar_adds_fixed_games_without_model_edit_call() -> None:
    agent = ScheduleAgent(_no_model, deterministic_summary=True)
    games = _games(*[("South", d, "09:00") for d in (7, 14, 21, 28)])
    out = agent.handle(_problem(), "", events=_calendar(), games=games)
    added = [e for e in out.events if e.event_type == "game"]
    assert len(added) == 4 and {e.field_id for e in added} == {"f2"}
    assert all(e.title == "Foxes v Gamblers" for e in added)
    assert len(out.events) == 12  # 8 saved sessions untouched + 4 games
    assert all(c.passed for c in out.check_results), [c for c in out.check_results if not c.passed]
    hours = [c for c in out.check_results if c.check == "field_hours" and c.reason.startswith("League fixture")]
    assert len(hours) == 4  # 9am is outside ground hours: recorded, not blocking
    assert "Added 4 league game(s)" in out.summary
    assert not out.questions


def test_game_clashing_with_saved_practice_moves_only_that_session() -> None:
    agent = ScheduleAgent(_no_model, deterministic_summary=True)
    out = agent.handle(_problem(), "", events=_calendar(), games=_games(("North Field", 9, "17:00")))
    assert all(c.passed for c in out.check_results), [c for c in out.check_results if not c.passed]
    foxes = sorted(e.starts_at for e in out.events if e.team_id == "t1" and e.event_type == "practice")
    assert len(foxes) == 4
    assert datetime(2026, 11, 9, 17, 0, tzinfo=ZONE) not in foxes  # moved off the game
    badgers = sorted(e.starts_at for e in out.events if e.team_id == "t2")
    assert badgers == sorted(e.starts_at for e in _calendar() if e.team_id == "t2")  # untouched
    assert "Moved 1 session(s)" in out.summary


def test_reuploaded_games_are_not_added_twice() -> None:
    games = _games(("South", 7, "09:00"))
    first = build_edit_proposal(_problem(), _calendar(), EditPlan(), games=games)
    again = build_edit_proposal(_problem(), first.events, EditPlan(), games=games)
    assert len(again.events) == len(first.events) == 9
    assert all(c.passed for c in again.check_results)


def test_plan_path_league_game_outside_hours_does_not_block_approval() -> None:
    rules = json.dumps({
        "requirements": [{"team_alias": "T1", "event_type": "practice", "sessions_per_week": 1,
                          "duration_minutes": 60, "allowed_days": [], "allowed_field_aliases": [],
                          "earliest_start": "17:00", "latest_end": "20:00", "title": "",
                          "active_from": "", "active_until": ""}],
        "min_gap_days": 0, "blackout_dates": [], "blackout_ranges": [], "notes": [],
    })

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        return rules if response_format else "Planned."

    agent = ScheduleAgent(chat, deterministic_summary=True)
    out = agent.plan(_problem(), "Foxes practice weekly", games=_games(("South", 7, "09:00")))
    assert any(e.event_type == "game" for e in out.events)
    assert all(c.passed for c in out.check_results)
    agent.approve(out)


def test_demo_does_not_invent_a_request_on_a_saved_calendar() -> None:
    from app.main import _request_with_games_default

    busy = ["placeholder"]  # any blocking game
    assert _request_with_games_default({"request": "", "events": [{"x": 1}]}, busy) == ""  # type: ignore[arg-type]
    assert _request_with_games_default({"request": ""}, busy)  # type: ignore[arg-type]  # fresh plan keeps the preset
