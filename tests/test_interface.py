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
    Disruption,
    FamilyNotices,
    Field,
    Problem,
    Proposal,
    ProposedEvent,
    Rules,
    ScheduleAgent,
    ScheduleImport,
    Team,
    draft_notices,
    extract_games,
    games_to_busy,
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


DISRUPTION_JSON = json.dumps(
    {
        "date_from": "2026-09-10",
        "date_to": "2026-09-10",
        "closed_field_aliases": ["F1"],
        "all_fields": False,
        "search_days": 7,
        "notes": [],
    }
)


def fake_disruption_chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
    if response_format is not None:
        return DISRUPTION_JSON
    return "Moved the Thursday session."


def test_reschedule_moves_only_hit_sessions() -> None:
    tz = ZoneInfo(TZ)
    problem = Problem(
        teams=[Team(id="t1", name="U12")],
        fields=[
            Field(id="f1", name="North", open_from=time(17, 0), open_until=time(20, 0)),
            Field(id="f2", name="South", open_from=time(17, 0), open_until=time(20, 0)),
        ],
        season_start=date(2026, 9, 7),
        season_end=date(2026, 9, 20),
        timezone=TZ,
    )

    def at(day: int, hour: int) -> datetime:
        return datetime(2026, 9, day, hour, 0, tzinfo=tz)

    events = [
        ProposedEvent(
            team_id="t1",
            field_id="f1",
            event_type="practice",
            title="Mon",
            starts_at=at(7, 17),
            ends_at=at(7, 18),
        ),
        ProposedEvent(
            team_id="t1",
            field_id="f1",
            event_type="practice",
            title="Thu",
            starts_at=at(10, 17),
            ends_at=at(10, 18),
        ),
    ]
    agent = ScheduleAgent(fake_disruption_chat)
    proposal = agent.reschedule(problem, events, "rain Thursday, North closed", verify=lambda draft: [])
    by_title = {e.title: e for e in proposal.events}
    assert (by_title["Mon"].starts_at, by_title["Mon"].field_id) == (at(7, 17), "f1")  # kept never moves
    assert (by_title["Thu"].starts_at, by_title["Thu"].field_id) == (at(10, 17), "f2")  # open field still used
    assert all(c.passed for c in proposal.check_results)
    assert "->" in proposal.summary  # each move listed old time/field -> new time/field
    assert agent.approve(proposal).approved is True
    assert Proposal.from_dict(json.loads(json.dumps(proposal.to_dict()))) == proposal
    assert Disruption(date_from=date(2026, 9, 10), date_to=date(2026, 9, 10), field_ids=[]).closed_ids(problem) == {
        "f1",
        "f2",
    }


def test_reschedule_next_week_with_host_pins() -> None:
    next_week_json = json.dumps(
        {
            "date_from": "2026-09-10",
            "date_to": "2026-09-10",
            "closed_field_aliases": ["F1"],
            "all_fields": False,
            "search_days": 7,
            "not_before": "2026-09-14",
            "notes": [],
        }
    )

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        if response_format is not None:
            return next_week_json
        return "Moved to next week."

    tz = ZoneInfo(TZ)
    problem = Problem(
        teams=[Team(id="t1", name="U12")],
        fields=[
            Field(id="f1", name="North", open_from=time(17, 0), open_until=time(20, 0)),
            Field(id="f2", name="South", open_from=time(17, 0), open_until=time(20, 0)),
        ],
        season_start=date(2026, 9, 7),
        season_end=date(2026, 9, 20),
        timezone=TZ,
    )
    thu = datetime(2026, 9, 10, 17, 0, tzinfo=tz)
    events = [
        ProposedEvent(
            team_id="t1",
            field_id="f1",
            event_type="practice",
            title="Thu",
            starts_at=thu,
            ends_at=datetime(2026, 9, 10, 18, 0, tzinfo=tz),
        ),
    ]
    agent = ScheduleAgent(chat)
    proposal = agent.reschedule(problem, events, "rain Thursday, move to next week")
    (moved,) = proposal.events
    assert moved.starts_at == datetime(2026, 9, 14, 17, 0, tzinfo=tz)  # Monday, back on North
    assert moved.field_id == "f1"
    pinned = agent.reschedule(
        problem, events, "rain Thursday", not_before=date(2026, 9, 14), allowed_fields=["f2"]
    )
    (south,) = pinned.events
    assert (south.starts_at, south.field_id) == (datetime(2026, 9, 14, 17, 0, tzinfo=tz), "f2")
    assert all(c.passed for c in pinned.check_results)


def test_vision_games_become_busy_for_plan() -> None:
    vision_json = json.dumps(
        {
            "games": [
                {"date": "2026-09-14", "start": "17:00", "home": "U12", "away": "U14", "field": "North"},
                {"date": "2026-09-14", "start": "18:00", "home": "U12 Cubs", "away": "U14", "field": "North"},
            ],
            "notes": [],
        }
    )

    def multimodal(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        if response_format is not None:
            if response_format["json_schema"]["name"] == "league_games":
                return vision_json
            return RULES_JSON
        return "Scheduled around the league games."

    agent = ScheduleAgent(multimodal)
    problem = Problem(
        teams=[Team(id="t1", name="U12"), Team(id="t2", name="U14")],
        fields=[Field(id="f1", name="North", open_from=time(17, 0), open_until=time(20, 0))],
        season_start=date(2026, 9, 7),
        season_end=date(2026, 9, 20),
        timezone=TZ,
    )
    schedule = extract_games(problem, b"fake-png-bytes", multimodal)
    assert len(schedule.games) == 2
    assert schedule.games[0].matched is True
    assert schedule.games[1].matched is False  # unknown team reported, never guessed
    assert ScheduleImport.from_dict(json.loads(json.dumps(schedule.to_dict()))) == schedule
    game_busy = next(b for b in games_to_busy(schedule, problem) if b.resource == "team:t1")
    with_games = Problem(
        teams=problem.teams,
        fields=problem.fields,
        season_start=problem.season_start,
        season_end=problem.season_end,
        timezone=problem.timezone,
        busy=games_to_busy(schedule, problem),
    )
    proposal = agent.plan(with_games, "U12 practice once a week on Mondays after 5pm", verify=lambda draft: [])
    assert len(proposal.events) == 2
    for event in proposal.events:
        assert not (event.team_id == "t1" and event.starts_at < game_busy.end and game_busy.start < event.ends_at)
    assert all(c.passed for c in proposal.check_results)


def test_approved_change_drafts_verified_family_notices() -> None:
    agent = ScheduleAgent(fake_chat)
    proposal = agent.plan(_problem(), "U12 practice Mondays", verify=_verify)
    approved = agent.approve(proposal)
    assert approved.approved is True

    def notices_chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        facts = messages[-1]["content"]
        payload = {
            "notices": [
                {"language": "es", "text": "Hola familias: " + facts},
                {"language": "en", "text": "Hi families: " + facts},
            ],
            "notes": [],
        }
        return json.dumps(payload)

    bundle = draft_notices(_problem(), approved.events, notices_chat, languages=["es", "en"])
    assert isinstance(bundle, FamilyNotices)
    assert bundle.all_verified  # echoing the facts block echoes every exact token
    assert [n.language for n in bundle.notices] == ["es", "en"]
    assert FamilyNotices.from_dict(json.loads(json.dumps(bundle.to_dict()))) == bundle


def test_plan_accepts_previous_rules_and_dict() -> None:
    agent = ScheduleAgent(fake_chat)
    problem = _problem()
    first = agent.plan(problem, "U12 practice Mondays", verify=_verify)
    from_rules = agent.plan(problem, "same as before", previous=first.rules, verify=_verify)
    from_dict = agent.plan(problem, "same as before", previous=first.rules.to_dict(), verify=_verify)
    assert from_rules.events == first.events == from_dict.events
