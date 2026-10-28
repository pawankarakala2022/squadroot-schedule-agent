"""Add-to-calendar tests: existing sessions frozen, only new ones placed (fake chats)."""

import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from schedule_agent import AgentError, Field, Problem, ProposedEvent, ScheduleAgent, Team

TZ = "America/Toronto"


def _problem() -> Problem:
    return Problem(
        teams=[Team(id="t1", name="Foxes"), Team(id="t2", name="Badgers")],
        fields=[
            Field(id="f1", name="North Field", open_from=time(17, 0), open_until=time(20, 0)),
            Field(id="f2", name="South Field", open_from=time(17, 0), open_until=time(20, 0)),
        ],
        season_start=date(2026, 9, 7),  # Monday
        season_end=date(2026, 9, 20),
        timezone=TZ,
    )


def _event(team: str, field: str, day: int, title: str = "Foxes practice") -> ProposedEvent:
    tz = ZoneInfo(TZ)
    return ProposedEvent(
        team_id=team,
        field_id=field,
        event_type="practice",
        title=title,
        starts_at=datetime(2026, 9, day, 17, 0, tzinfo=tz),
        ends_at=datetime(2026, 9, day, 18, 0, tzinfo=tz),
    )


GAME_RULES = json.dumps(
    {
        "requirements": [
            {
                "team_alias": "T1",
                "event_type": "game",
                "sessions_per_week": 1,
                "duration_minutes": 60,
                "allowed_days": [3],
                "allowed_field_aliases": [],
                "earliest_start": "17:00",
                "latest_end": "18:00",
                "title": "vs Falcons",
                "active_from": "2026-09-10",
                "active_until": "2026-09-10",
            }
        ],
        "min_gap_days": 1,
        "blackout_dates": [],
        "blackout_ranges": [],
        "notes": [],
    }
)


def _chat(route: str | None = None):  # type: ignore[no-untyped-def]
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        if response_format is None:
            return "Explained."
        name = response_format["json_schema"]["name"]
        if name == "route_intent":
            return json.dumps({"call": route or "add", "notes": []})
        return GAME_RULES

    return chat


def test_add_places_only_new_session_and_keeps_existing() -> None:
    existing = [_event("t1", "f1", 7), _event("t1", "f1", 14)]
    agent = ScheduleAgent(_chat())
    proposal = agent.add(_problem(), existing, "Add a game with Falcons on Thursday Sep 10")
    assert len(proposal.events) == 3
    for ev in existing:
        assert ev in proposal.events  # exact same sessions, unmoved
    added = [e for e in proposal.events if e.event_type == "game"]
    assert len(added) == 1 and added[0].starts_at.date() == date(2026, 9, 10)
    assert "Added 1 session(s)" in proposal.summary
    assert all(c.passed for c in proposal.check_results)
    assert [s["step"] for s in agent.last_trace] == [
        "understand", "rules", "solve", "checks", "verify", "explain",
    ]


def test_add_with_no_free_slot_shortfalls_and_keeps_existing() -> None:
    # Foxes already occupy Thu 17:00 on both fields via team/field freeze:
    # the same-team busy block makes the 17:00-18:00 game unplaceable.
    existing = [_event("t1", "f1", 10), _event("t2", "f2", 10, title="Badgers practice")]
    agent = ScheduleAgent(_chat())
    proposal = agent.add(_problem(), existing, "Add a game with Falcons on Thursday Sep 10")
    assert proposal.events == sorted(existing, key=lambda e: (e.starts_at, e.team_id, e.field_id))
    assert len(proposal.shortfalls) == 1
    assert "No sessions added." in proposal.summary


def test_add_without_calendar_fails_safe() -> None:
    agent = ScheduleAgent(_chat())
    with pytest.raises(AgentError, match="no calendar yet"):
        agent.add(_problem(), [], "Add a game next Sunday")


def test_handle_routes_add_and_freezes_calendar() -> None:
    existing = [_event("t1", "f1", 7), _event("t1", "f1", 14)]
    agent = ScheduleAgent(_chat(route="add"))
    proposal = agent.handle(_problem(), "Add a game with Falcons on Thursday Sep 10", events=existing)
    assert len(proposal.events) == 3
    assert all(ev in proposal.events for ev in existing)
    assert agent.last_trace[0]["step"] == "route"


def test_fast_route_skips_model_for_unambiguous_plan_and_add() -> None:
    calls: list[str] = []

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        if response_format is not None:
            calls.append(response_format["json_schema"]["name"])
            return GAME_RULES
        calls.append("explain")
        return "Explained."

    planner = ScheduleAgent(chat)
    planner.handle(_problem(), "Foxes practice twice a week, 60 minutes, Mondays and Wednesdays after 5pm")
    assert "route_intent" not in calls  # fast path, no 5-8s router call
    assert planner.last_trace[0] == {
        "step": "route", "call": "plan", "source": "fast_path", "latency_ms": 0,
    }

    calls.clear()
    adder = ScheduleAgent(chat)
    adder.handle(
        _problem(), "Add a game with Falcons on Thursday Sep 10", events=[_event("t1", "f1", 7)]
    )
    assert "route_intent" not in calls


def test_deterministic_summary_skips_explain_model_call() -> None:
    plain_calls = {"n": 0}

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        if response_format is not None:
            return GAME_RULES
        plain_calls["n"] += 1
        return "Explained."

    agent = ScheduleAgent(chat, deterministic_summary=True)
    proposal = agent.add(
        _problem(), [_event("t1", "f1", 7)], "Add a game with Falcons on Thursday Sep 10"
    )
    assert plain_calls["n"] == 0  # no explain model call for English
    assert "sessions placed." in proposal.summary and "Checks:" in proposal.summary
    explain_step = next(s for s in agent.last_trace if s["step"] == "explain")
    assert explain_step["source"] == "deterministic"

    # Non-English still uses the model.
    spanish = ScheduleAgent(chat, deterministic_summary=True)
    out = spanish.add(
        _problem(), [_event("t1", "f1", 7)], "Add a game with Falcons on Thursday Sep 10", language="es"
    )
    assert plain_calls["n"] == 1 and "Explained." in out.summary
