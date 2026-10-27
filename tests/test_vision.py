"""Vision tests: photo reading, exact matching, busy export (fake chat, no network)."""

import json
from datetime import date, time
from pathlib import Path

import pytest

from schedule_agent import vision
from schedule_agent.checks import run_checks
from schedule_agent.errors import AgentError
from schedule_agent.models import Field, Problem, Requirement, Rules, Team
from schedule_agent.solver import solve
from schedule_agent.vision import ScheduleImport, extract_games, games_to_busy

TZ = "America/Toronto"


def _problem() -> Problem:
    return Problem(
        teams=[Team(id="t1", name="U12 Foxes"), Team(id="t2", name="U14 Badgers")],
        fields=[
            Field(id="f1", name="North", open_from=time(17, 0), open_until=time(20, 0)),
            Field(id="f2", name="South", open_from=time(17, 0), open_until=time(20, 0)),
        ],
        season_start=date(2026, 9, 7),
        season_end=date(2026, 9, 20),
        timezone=TZ,
    )


def _payload(games, **kwargs):  # type: ignore[no-untyped-def]
    body = {"games": games, "notes": []}
    body.update(kwargs)
    return json.dumps(body)


def _row(**over):  # type: ignore[no-untyped-def]
    row = {"date": "2026-09-14", "start": "17:00", "home": "U12 Foxes", "away": "U14 Badgers", "field": "North"}
    row.update(over)
    return row


def test_extract_matches_and_round_trips() -> None:
    calls: list = []

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        calls.append((messages, response_format))
        return _payload([_row()])

    schedule = extract_games(_problem(), b"\x89PNGdata", chat)
    assert response_format_is_strict(calls[0][1])
    (game,) = schedule.games
    assert game.matched is True
    assert (game.home_id, game.away_id, game.field_id) == ("t1", "t2", "f1")
    assert ScheduleImport.from_dict(json.loads(json.dumps(schedule.to_dict()))) == schedule


def response_format_is_strict(response_format):  # type: ignore[no-untyped-def]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    assert response_format["json_schema"]["strict"] is True
    assert schema["additionalProperties"] is False
    return True


def test_fixture_png_is_valid_and_reads() -> None:
    raw = (Path(__file__).parent.parent / "demo" / "schedule_tiny.png").read_bytes()
    assert raw[:8] == bytes([137, 80, 78, 71, 13, 10, 26, 10])

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        return _payload([_row()])

    schedule = extract_games(_problem(), raw, chat, mime_type="image/png")
    assert [g.matched for g in schedule.games] == [True]


def test_image_sent_as_data_url() -> None:
    seen: list = []

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        seen.append(messages)
        return _payload([])

    extract_games(_problem(), b"bytes-here", chat, mime_type="image/jpeg")
    user_content = seen[0][1]["content"]
    urls = [part["image_url"]["url"] for part in user_content if part.get("type") == "image_url"]
    assert urls == ["data:image/jpeg;base64,Ynl0ZXMtaGVyZQ=="]


def test_close_names_never_guessed_bad_rows_kept() -> None:
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        return _payload(
            [
                _row(home="U12 Fox"),  # near miss: not our team
                _row(field="Centre"),  # unknown ground
                _row(date="someday", start="evening"),  # unreadable
            ]
        )

    schedule = extract_games(_problem(), b"img", chat)
    assert [g.matched for g in schedule.games] == [False, False, False]
    assert schedule.games[0].home_id is None and "U12 Fox" in schedule.games[0].note
    assert schedule.games[1].field_id is None and "Centre" in schedule.games[1].note
    assert schedule.games[2].date is None and schedule.games[2].start is None
    assert games_to_busy(schedule, _problem()) == []  # unmatched rows block nothing


def test_garbage_twice_raises_agent_error() -> None:
    calls: list = []

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        calls.append(messages)
        return "not json"

    with pytest.raises(AgentError):
        extract_games(_problem(), b"img", chat)
    assert len(calls) == 2


def test_bad_input_fails_without_calling_model() -> None:
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        raise AssertionError("model must not be called")

    with pytest.raises(AgentError):
        extract_games(_problem(), b"", chat)
    with pytest.raises(AgentError):
        extract_games(_problem(), b"x" * (vision.MAX_IMAGE_BYTES + 1), chat)
    with pytest.raises(AgentError):
        extract_games(_problem(), b"%PDF", chat, mime_type="application/pdf")


def test_games_to_busy_blocks_both_teams_and_field() -> None:
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        return _payload([_row()], notes=["ok"])

    schedule = extract_games(_problem(), b"img", chat)
    busy = games_to_busy(schedule, _problem(), duration_minutes=90)
    assert {(b.resource, b.start.isoformat(), b.end.isoformat()) for b in busy} == {
        ("team:t1", "2026-09-14T17:00:00-04:00", "2026-09-14T18:30:00-04:00"),
        ("team:t2", "2026-09-14T17:00:00-04:00", "2026-09-14T18:30:00-04:00"),
        ("field:f1", "2026-09-14T17:00:00-04:00", "2026-09-14T18:30:00-04:00"),
    }
    assert all(b.reason == "league game" for b in busy)
    with pytest.raises(ValueError):
        games_to_busy(schedule, _problem(), duration_minutes=0)


def test_league_games_push_practices_aside() -> None:
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        return _payload([_row()])  # U12 Foxes game Mon Sep 14 17:00 North

    problem = _problem()
    busy = games_to_busy(extract_games(problem, b"img", chat), problem)
    game_busy = next(b for b in busy if b.resource == "team:t1")
    full = Problem(
        teams=problem.teams,
        fields=problem.fields,
        season_start=problem.season_start,
        season_end=problem.season_end,
        timezone=problem.timezone,
        busy=busy,
    )
    rules = Rules(
        requirements=[
            Requirement(
                team_id="t1",
                event_type="practice",
                sessions_per_week=1,
                duration_minutes=60,
                allowed_days=[0],
                allowed_fields=["f1"],
                earliest_start=time(17, 0),
                latest_end=time(19, 0),
            )
        ]
    )
    proposal = solve(full, rules)
    assert not proposal.shortfalls
    for ev in proposal.events:
        assert not (ev.starts_at < game_busy.end and game_busy.start < ev.ends_at and ev.team_id == "t1")
    assert all(c.passed for c in run_checks(full, rules, proposal.events))
