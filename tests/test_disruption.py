"""Disruption + reschedule tests: parsing, hit-only moves, tight shortfalls (fake chat)."""

import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from schedule_agent import Disruption, ProposedEvent, ScheduleAgent
from schedule_agent.disruption import parse_disruption
from schedule_agent.errors import AgentError
from schedule_agent.models import Field, Problem, Team

TZ = "America/Toronto"
# Thu 2026-09-10 is the rain day in these fixtures (Mon 2026-09-07 + 3).
THU = date(2026, 9, 10)


def _problem() -> Problem:
    return Problem(
        teams=[Team(id="t1", name="U12"), Team(id="t2", name="U14")],
        fields=[
            Field(id="f1", name="North", open_from=time(17, 0), open_until=time(20, 0)),
            Field(id="f2", name="South", open_from=time(17, 0), open_until=time(20, 0)),
        ],
        season_start=date(2026, 9, 7),
        season_end=date(2026, 9, 27),
        timezone=TZ,
    )


def _event(team: str, field: str, day: date, hour: int, title: str) -> ProposedEvent:
    tz = ZoneInfo(TZ)
    start = datetime(day.year, day.month, day.day, hour, 0, tzinfo=tz)
    return ProposedEvent(
        team_id=team,
        field_id=field,
        event_type="practice",
        title=title,
        starts_at=start,
        ends_at=start.replace(hour=hour + 1),
    )


def _calendar() -> list[ProposedEvent]:
    return [
        _event("t1", "f1", date(2026, 9, 7), 17, "U12 Mon practice"),  # kept (Monday)
        _event("t1", "f1", THU, 17, "U12 Thu practice"),  # hit (Thu, North closed)
        _event("t2", "f1", THU, 18, "U14 Thu practice"),  # hit (Thu, North closed)
        _event("t2", "f2", THU, 17, "U14 Thu South"),  # kept (Thu, South open)
        _event("t1", "f1", date(2026, 9, 11), 17, "U12 Fri practice"),  # kept (Friday)
    ]


def _payload(**over):  # type: ignore[no-untyped-def]
    body = {
        "date_from": "2026-09-10",
        "date_to": "2026-09-10",
        "closed_field_aliases": ["F1"],
        "all_fields": False,
        "search_days": 7,
        "notes": [],
    }
    body.update(over)
    return json.dumps(body)


def _chat(payload: str | None = None):  # type: ignore[no-untyped-def]
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        if response_format is not None:
            return payload if payload is not None else _payload()
        return "Explained."

    return chat


def test_parse_maps_aliases_and_round_trips() -> None:
    disruption = parse_disruption(_problem(), "rain Thursday, North closed", _chat())
    assert disruption.date_from == disruption.date_to == THU
    assert disruption.field_ids == ["f1"]
    assert disruption.search_days == 7
    assert Disruption.from_dict(json.loads(json.dumps(disruption.to_dict()))) == disruption


def test_parse_drops_unknown_fields_and_clamps() -> None:
    disruption = parse_disruption(
        _problem(), "rain, North and Centre closed, search 99 days", _chat(_payload(closed_field_aliases=["F1", "F9"], search_days=99))
    )
    assert disruption.field_ids == ["f1"]
    assert disruption.search_days == 14
    assert any("F9" in n for n in disruption.notes)


def test_parse_not_before_mapped_empty_and_bad() -> None:
    mapped = parse_disruption(_problem(), "rain, move to next week", _chat(_payload(not_before="2026-09-14")))
    assert mapped.not_before == date(2026, 9, 14)
    assert Disruption.from_dict(json.loads(json.dumps(mapped.to_dict()))) == mapped

    unset = parse_disruption(_problem(), "rain Thursday", _chat(_payload(not_before="")))
    assert unset.not_before is None

    lenient = parse_disruption(_problem(), "rain Thursday", _chat(_payload(not_before="someday")))
    assert lenient.not_before is None
    assert any("not_before" in n for n in lenient.notes)


def test_disruption_old_dict_without_not_before() -> None:
    old = {
        "date_from": "2026-09-10",
        "date_to": "2026-09-10",
        "field_ids": ["f1"],
        "search_days": 7,
        "notes": [],
    }
    assert Disruption.from_dict(old).not_before is None


def test_parse_garbage_twice_raises_agent_error() -> None:
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        return "not json"

    with pytest.raises(AgentError):
        parse_disruption(_problem(), "rain or something", chat)


def test_parse_empty_request_fails_without_calling_model() -> None:
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        raise AssertionError("model must not be called")

    with pytest.raises(AgentError):
        parse_disruption(_problem(), "   ", chat)


def test_reschedule_moves_only_hit_sessions() -> None:
    agent = ScheduleAgent(_chat())
    before = {e.title: (e.starts_at, e.field_id) for e in _calendar()}
    proposal = agent.reschedule(_problem(), _calendar(), "rain Thursday, North closed")
    assert len(proposal.events) == 5  # every session still has a slot
    assert not proposal.shortfalls
    assert all(c.passed for c in proposal.check_results)
    by_title = {e.title: e for e in proposal.events}
    for title in ("U12 Mon practice", "U14 Thu South", "U12 Fri practice"):
        assert (by_title[title].starts_at, by_title[title].field_id) == before[title]
    moved = [by_title["U12 Thu practice"], by_title["U14 Thu practice"]]
    assert all(e.field_id == "f2" for e in moved)  # open field still used
    assert all(e.starts_at.date() == THU for e in moved)  # same-day move onto open field
    assert all(e.event_type == "practice" for e in moved)
    assert by_title["U12 Thu practice"].title == "U12 Thu practice"  # title kept
    assert "->" in proposal.summary  # moves listed old time/field -> new time/field
    assert "2026-09-10T17:00:00-04:00" in proposal.summary
    steps = [s["step"] for s in agent.last_trace]
    assert steps[0] == "disruption" and "reschedule" in steps and steps[-1] == "explain"
    json.dumps(agent.last_trace)


def test_reschedule_nothing_on_closed_fields_during_disruption() -> None:
    agent = ScheduleAgent(_chat())
    proposal = agent.reschedule(_problem(), _calendar(), "rain Thursday, North closed")
    for ev in proposal.events:
        if ev.starts_at.date() == THU:
            assert ev.field_id != "f1"


def test_reschedule_tight_case_shortfall_names_session() -> None:
    tz = ZoneInfo(TZ)
    problem = Problem(
        teams=[Team(id="t1", name="U12")],
        fields=[Field(id="f1", name="North", open_from=time(17, 0), open_until=time(18, 0))],
        season_start=date(2026, 9, 7),
        season_end=date(2026, 9, 27),
        timezone=TZ,
    )
    hit = _event("t1", "f1", THU, 17, "U12 Thu practice")
    kept = _event("t1", "f1", date(2026, 9, 11), 17, "U12 Fri practice")

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        if response_format is not None:
            return _payload(search_days=1)  # window Thu..Fri only
        return "Explained."

    proposal = ScheduleAgent(chat).reschedule(problem, [hit, kept], "rain Thursday, North closed")
    assert len(proposal.events) == 1  # only the kept Friday session survives
    assert proposal.events[0].title == "U12 Fri practice"
    (short,) = proposal.shortfalls
    assert "U12 Thu practice" in short.reason  # names the session that could not move
    assert short.requested == 1 and short.placed == 0
    assert proposal.shortfall_summary().startswith("t1 practice: 0 of 1 placed")


def test_reschedule_verify_hook_busy_respected() -> None:
    from schedule_agent.models import Busy

    tz = ZoneInfo(TZ)
    calls: list = []
    coach_busy = Busy(
        resource="team:t1",
        start=datetime(2026, 9, 10, 17, 0, tzinfo=tz),
        end=datetime(2026, 9, 10, 19, 0, tzinfo=tz),
        reason="coach unavailable",
    )

    def verify(draft):  # type: ignore[no-untyped-def]
        calls.append(list(draft))
        if len(calls) == 1:
            return [coach_busy]
        return []

    agent = ScheduleAgent(_chat())
    proposal = agent.reschedule(_problem(), _calendar(), "rain Thursday, North closed", verify=verify)
    assert len(calls) == 2
    by_title = {e.title: e for e in proposal.events}
    moved = by_title["U12 Thu practice"]
    assert not (moved.starts_at < coach_busy.end and coach_busy.start < moved.ends_at)
    assert moved.starts_at == datetime(2026, 9, 10, 19, 0, tzinfo=tz)  # Thu 19:00, South
    assert moved.field_id == "f2"
    assert all(c.passed for c in proposal.check_results)


def test_reschedule_honors_not_before_next_week() -> None:
    tz = ZoneInfo(TZ)

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        if response_format is not None:
            return _payload(not_before="2026-09-14")  # "move to next week"
        return "Explained."

    proposal = ScheduleAgent(chat).reschedule(_problem(), _calendar(), "rain Thursday, move to next week")
    by_title = {e.title: e for e in proposal.events}
    assert by_title["U12 Thu practice"].starts_at == datetime(2026, 9, 14, 17, 0, tzinfo=tz)
    assert by_title["U12 Thu practice"].field_id == "f1"  # North usable again next week
    assert by_title["U14 Thu practice"].starts_at == datetime(2026, 9, 14, 18, 0, tzinfo=tz)
    assert not proposal.shortfalls
    assert all(c.passed for c in proposal.check_results)
    assert any("on or after 2026-09-14" in n for n in proposal.rules.notes)


def test_reschedule_host_pins_date_and_grounds() -> None:
    tz = ZoneInfo(TZ)
    proposal = ScheduleAgent(_chat()).reschedule(
        _problem(),
        _calendar(),
        "rain Thursday, North closed",
        not_before="2026-09-14",
        allowed_fields=["f2"],
    )
    by_title = {e.title: e for e in proposal.events}
    assert by_title["U12 Thu practice"].starts_at == datetime(2026, 9, 14, 17, 0, tzinfo=tz)
    assert by_title["U12 Thu practice"].field_id == "f2"
    assert by_title["U14 Thu practice"].field_id == "f2"
    assert all(c.passed for c in proposal.check_results)
    assert any("Only these grounds" in n and "South" in n for n in proposal.rules.notes)


def test_reschedule_latest_not_before_wins() -> None:
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        if response_format is not None:
            return _payload(not_before="2026-09-14")
        return "Explained."

    proposal = ScheduleAgent(chat).reschedule(
        _problem(), _calendar(), "rain Thursday", not_before=date(2026, 9, 10)
    )
    by_title = {e.title: e for e in proposal.events}
    assert by_title["U12 Thu practice"].starts_at.date() == date(2026, 9, 14)


def test_reschedule_inverted_window_shortfalls_safely() -> None:
    agent = ScheduleAgent(_chat())
    before = {e.title: (e.starts_at, e.field_id) for e in _calendar()}
    proposal = agent.reschedule(_problem(), _calendar(), "rain Thursday", not_before=date(2026, 10, 1))
    assert len(proposal.events) == 3  # only the kept sessions survive
    assert len(proposal.shortfalls) == 2
    assert "U12 Thu practice" in proposal.shortfalls[0].reason
    assert "U14 Thu practice" in proposal.shortfalls[1].reason
    for ev in proposal.events:
        assert (ev.starts_at, ev.field_id) == before[ev.title]
    assert all(c.passed for c in proposal.check_results)
    assert proposal.summary.startswith("No sessions needed moving.")


def test_reschedule_unknown_pin_dropped_and_reported() -> None:
    proposal = ScheduleAgent(_chat()).reschedule(
        _problem(), _calendar(), "rain Thursday", allowed_fields=["f2", "f9"]
    )
    by_title = {e.title: e for e in proposal.events}
    assert by_title["U12 Thu practice"].field_id == "f2"
    assert any("f9" in n for n in proposal.rules.notes)


def test_reschedule_bad_pins_raise_loudly() -> None:
    agent = ScheduleAgent(_chat())
    with pytest.raises(ValueError):
        agent.reschedule(_problem(), _calendar(), "rain Thursday", not_before="tomorrow")
    with pytest.raises(TypeError):
        agent.reschedule(_problem(), _calendar(), "rain Thursday", not_before=42)
    with pytest.raises(TypeError):
        agent.reschedule(_problem(), _calendar(), "rain Thursday", allowed_fields="f2")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        agent.reschedule(_problem(), _calendar(), "rain Thursday", allowed_fields=["f2", 42])  # type: ignore[list-item]


def test_reschedule_is_deterministic() -> None:
    problem = _problem()
    agent_a, agent_b = ScheduleAgent(_chat()), ScheduleAgent(_chat())
    first = agent_a.reschedule(problem, _calendar(), "rain Thursday, North closed")
    second = agent_b.reschedule(problem, _calendar(), "rain Thursday, North closed")
    assert first.events == second.events
    assert first.summary == second.summary


def test_reschedule_bad_calendar_raises_agent_error() -> None:
    agent = ScheduleAgent(_chat())
    with pytest.raises(AgentError):
        agent.reschedule(_problem(), "not a list", "rain Thursday")  # type: ignore[arg-type]
    with pytest.raises(AgentError):
        agent.reschedule(_problem(), [{"bogus": 1}], "rain Thursday")  # type: ignore[list-item]
