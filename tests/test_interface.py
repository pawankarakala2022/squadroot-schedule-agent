"""Contract test for the stable host-facing interface (AGENTS.md).

Uses only the public names exported from ``schedule_agent``: a host app
gets exactly this. Fake chat plus a host verify hook, no network. This
test must keep passing; never weaken it to make a change fit.
"""

import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from schedule_agent import (
    ApplyResult,
    Busy,
    Field,
    Problem,
    Proposal,
    Rules,
    ScheduleAgent,
    Team,
)

TZ = "America/Toronto"

RULES_JSON = json.dumps(
    {
        "requirements": [
            {
                "team_alias": "T1",
                "event_type": "practice",
                "sessions_per_week": 1,
                "duration_minutes": 60,
                "allowed_days": [0],
                "allowed_field_aliases": ["F1"],
                "earliest_start": "17:00",
                "latest_end": "19:00",
                "title": "",
            }
        ],
        "min_gap_days": 1,
        "blackout_dates": [],
        "notes": [],
    }
)


def fake_chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
    if response_format is not None:
        return RULES_JSON
    return "U12 has one Monday practice each week."


def _problem() -> Problem:
    return Problem(
        teams=[Team(id="t1", name="U12")],
        fields=[Field(id="f1", name="North", open_from=time(17, 0), open_until=time(20, 0))],
        season_start=date(2026, 9, 7),  # a Monday
        season_end=date(2026, 9, 20),
        timezone=TZ,
    )


_BUSY = Busy(
    resource="team:t1",
    start=datetime(2026, 9, 7, 17, 0, tzinfo=ZoneInfo(TZ)),
    end=datetime(2026, 9, 7, 18, 0, tzinfo=ZoneInfo(TZ)),
    reason="coach unavailable",
)


def _verify(events):  # type: ignore[no-untyped-def]
    """Host hook: report the coach's unavailability only while a draft hits it."""
    hitting = [e for e in events if e.team_id == "t1" and e.starts_at < _BUSY.end and _BUSY.start < e.ends_at]
    return [_BUSY] if hitting else []


def test_plan_verify_and_json_round_trip() -> None:
    agent = ScheduleAgent(fake_chat)
    proposal = agent.plan(_problem(), "U12 practice once a week on Mondays after 5pm", verify=_verify)
    assert isinstance(proposal, Proposal)
    assert len(proposal.events) == 2  # one Monday per week, moved off the coach's busy hour
    assert all(c.passed for c in proposal.check_results)
    assert not proposal.shortfalls
    for event in proposal.events:
        assert not (event.team_id == "t1" and event.starts_at < _BUSY.end and _BUSY.start < event.ends_at)
    assert "U12" in proposal.summary
    assert [s["step"] for s in agent.last_trace] == [
        "understand",
        "rules",
        "solve",
        "checks",
        "verify",
        "solve",
        "checks",
        "verify",
        "explain",
    ]
    json.dumps(agent.last_trace)  # trace must be display-safe
    assert Proposal.from_dict(json.loads(json.dumps(proposal.to_dict()))) == proposal
    assert Rules.from_dict(proposal.rules.to_dict()) == proposal.rules


def test_revise_keeps_everything_else() -> None:
    agent = ScheduleAgent(fake_chat)
    problem = _problem()
    proposal = agent.plan(problem, "U12 practice Mondays", verify=_verify)
    revised = agent.revise(problem, proposal, "keep it, just confirm", verify=_verify)
    assert isinstance(revised, Proposal)
    assert revised.events == proposal.events
    assert Proposal.from_dict(json.loads(json.dumps(revised.to_dict()))) == revised


def test_approve_is_the_only_path_to_apply_result() -> None:
    agent = ScheduleAgent(fake_chat)
    proposal = agent.plan(_problem(), "U12 practice Mondays", verify=_verify)
    result = agent.approve(proposal)
    assert isinstance(result, ApplyResult)
    assert result.approved is True
    assert result.events == proposal.events
    assert ApplyResult().approved is False  # default construction is not an approval
    assert ApplyResult.from_dict(json.loads(json.dumps(result.to_dict()))) == result


def test_plan_accepts_previous_rules_and_dict() -> None:
    agent = ScheduleAgent(fake_chat)
    problem = _problem()
    first = agent.plan(problem, "U12 practice Mondays", verify=_verify)
    from_rules = agent.plan(problem, "same as before", previous=first.rules, verify=_verify)
    from_dict = agent.plan(problem, "same as before", previous=first.rules.to_dict(), verify=_verify)
    assert from_rules.events == first.events == from_dict.events
