"""Router tests: any wording in, one API call out. Fake chats, no network."""

import json
from datetime import date, time

import pytest

from schedule_agent import (
    AgentError,
    Field,
    Problem,
    Proposal,
    ScheduleAgent,
    Team,
)
from schedule_agent.router import Route, parse_route_intent


def _problem() -> Problem:
    return Problem(
        teams=[Team(id="t1", name="Foxes"), Team(id="t2", name="Badgers")],
        fields=[
            Field(id="f1", name="North Field", open_from=time(17, 0), open_until=time(20, 0)),
            Field(id="f2", name="South Field", open_from=time(17, 0), open_until=time(20, 0)),
        ],
        season_start=date(2026, 10, 1),
        season_end=date(2026, 12, 31),
        timezone="America/Toronto",
    )


def _route_chat(call: str):  # type: ignore[no-untyped-def]
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        assert response_format is not None
        return json.dumps({"call": call, "notes": []})

    return chat


@pytest.mark.parametrize("call", ["plan", "revise", "reschedule", "cancel"])
def test_parse_route_intent_each_call(call: str) -> None:
    route = parse_route_intent("whatever the wording", _route_chat(call))
    assert isinstance(route, Route)
    assert route.call == call
    assert Route.from_dict(route.to_dict()) == route


def test_parse_route_intent_retries_bad_call() -> None:
    seen: list[str] = []

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        seen.append(str(messages[-1].get("content", ""))[:60])
        if len(seen) == 1:
            return json.dumps({"call": "dance", "notes": []})
        return json.dumps({"call": "plan", "notes": []})

    assert parse_route_intent("schedule us", chat).call == "plan"
    assert len(seen) == 2  # one repair retry


def test_parse_route_intent_empty_request_fails() -> None:
    with pytest.raises(AgentError):
        parse_route_intent("   ", _route_chat("plan"))


RULES_JSON = json.dumps(
    {
        "requirements": [
            {
                "team_alias": "T2",
                "event_type": "practice",
                "sessions_per_week": 1,
                "duration_minutes": 60,
                "allowed_days": [3],
                "allowed_field_aliases": [],
                "earliest_start": "17:00",
                "latest_end": "20:00",
                "title": "",
            }
        ],
        "min_gap_days": 1,
        "blackout_dates": [],
        "blackout_ranges": [],
        "notes": [],
    }
)


def _combined_chat(route_call: str):  # type: ignore[no-untyped-def]
    """One chat serving the router plus whichever downstream schema runs."""

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        if response_format is None:
            return "summary"
        name = response_format["json_schema"]["name"]
        if name == "route_intent":
            return json.dumps({"call": route_call, "notes": []})
        if name == "scheduling_rules":
            return RULES_JSON
        if name == "disruption":
            return json.dumps(
                {
                    "date_from": "2026-10-29",
                    "date_to": "2026-10-29",
                    "closed_field_aliases": [],
                    "all_fields": True,
                    "search_days": 7,
                    "not_before": "2026-10-30",
                    "notes": [],
                }
            )
        if name == "cancel_filter":
            return json.dumps(
                {
                    "team_aliases": ["T2"],
                    "field_aliases": [],
                    "weekdays": [],
                    "date_from": "",
                    "date_to": "",
                    "event_types": ["practice"],
                    "notes": [],
                }
            )
        return "summary"

    return chat


def test_handle_plans_from_scratch() -> None:
    agent = ScheduleAgent(_combined_chat("plan"))
    proposal = agent.handle(_problem(), "Badgers practice Thursdays after 5pm")
    assert isinstance(proposal, Proposal) and proposal.events
    assert all(e.team_id == "t2" for e in proposal.events)
    assert agent.last_trace and agent.last_trace[0]["step"] == "route"


def test_handle_moves_dated_session() -> None:
    agent = ScheduleAgent(_combined_chat("plan"))
    planned = agent.handle(_problem(), "Badgers practice Thursdays after 5pm")
    assert any(e.starts_at.date() == date(2026, 10, 29) for e in planned.events)

    mover = ScheduleAgent(_combined_chat("reschedule"))
    moved = mover.handle(_problem(), "Move thu practice from 10/29 to friday 10/30",
                         events=planned.events)
    days = sorted(e.starts_at.date() for e in moved.events)
    assert date(2026, 10, 29) not in days  # hit session left Thursday...
    assert date(2026, 10, 30) in days  # ...and landed Friday
    assert all(c.passed for c in moved.check_results)


def test_handle_cancels_named_team() -> None:
    agent = ScheduleAgent(_combined_chat("plan"))
    planned = agent.handle(_problem(), "Badgers practice Thursdays after 5pm")
    canceller = ScheduleAgent(_combined_chat("cancel"))
    out = canceller.handle(_problem(), "Cancel Badgers practices", events=planned.events)
    assert len(out.events) == 0
    assert "Cancelled" in out.summary


def test_handle_cancel_without_calendar_fails_safe() -> None:
    agent = ScheduleAgent(_combined_chat("cancel"))
    with pytest.raises(AgentError, match="no calendar yet"):
        agent.handle(_problem(), "Cancel Badgers practices")


def test_handle_revise_without_draft_falls_back_to_plan() -> None:
    agent = ScheduleAgent(_combined_chat("revise"))
    proposal = agent.handle(_problem(), "move to Field 2")
    assert isinstance(proposal, Proposal) and proposal.events


def test_api_run_routes_any_wording() -> None:
    from app.main import _stream_kwargs, api_run

    planned = api_run({"mode": "mock", "request": "Badgers practice once a week, 60 minutes, Thursdays after 5pm"})
    events = planned["proposal"]["events"]
    assert events
    moved = api_run({"mode": "mock", "request": "Move thu practice from 10/29 to friday 10/30",
                     "proposal": planned["proposal"], "events": events})
    days = sorted(e["starts_at"][:10] for e in moved["proposal"]["events"])
    assert "2026-10-29" not in days
    assert "2026-10-30" in days
    kwargs = _stream_kwargs("run", {"mode": "mock", "request": "Cancel Badgers",
                                    "proposal": planned["proposal"], "events": events})
    assert kwargs["events"] == events and kwargs["proposal"] == planned["proposal"]


def test_handle_mock_end_to_end_move() -> None:
    """The demo path: mock chat routes and moves 10/29 -> 10/30 with no key."""
    from app.main import mock_chat

    agent = ScheduleAgent(mock_chat)
    planned = agent.handle(_problem(), "Badgers practice once a week, 60 minutes, Thursdays after 5pm")
    assert any(e.starts_at.date() == date(2026, 10, 29) for e in planned.events)
    moved = agent.handle(_problem(), "Move thu practice from 10/29 to friday 10/30",
                         proposal=planned, events=planned.events)
    days = sorted(e.starts_at.date() for e in moved.events)
    assert date(2026, 10, 29) not in days
    assert date(2026, 10, 30) in days
