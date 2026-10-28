"""Offline tests for cancellations: schedule_agent/cancel.py + agent.cancel. Fake chat, no network."""

import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from schedule_agent import (
    Field,
    Problem,
    Proposal,
    ProposedEvent,
    ScheduleAgent,
    Team,
)
from schedule_agent.cancel import CancelFilter, parse_cancel_filter
from schedule_agent.errors import AgentError, ModelError

TZ = "America/Toronto"
TZINFO = ZoneInfo(TZ)


def _problem() -> Problem:
    return Problem(
        teams=[Team(id="t1", name="Foxes"), Team(id="t2", name="Badgers")],
        fields=[
            Field(id="f1", name="North Field", open_from=time(17, 0), open_until=time(20, 0)),
            Field(id="f2", name="South Field", open_from=time(17, 0), open_until=time(20, 0)),
        ],
        season_start=date(2026, 11, 2),  # a Monday
        season_end=date(2026, 11, 15),
        timezone=TZ,
    )


def _events() -> list[ProposedEvent]:
    def at(day: int, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 11, day, hour, minute, tzinfo=TZINFO)

    return [
        ProposedEvent(team_id="t1", field_id="f1", event_type="practice", title="Mon",
                      starts_at=at(2, 17), ends_at=at(2, 18)),
        ProposedEvent(team_id="t1", field_id="f1", event_type="practice", title="Fri",
                      starts_at=at(6, 17), ends_at=at(6, 18)),
        ProposedEvent(team_id="t2", field_id="f2", event_type="game", title="Fri game",
                      starts_at=at(6, 18), ends_at=at(6, 19)),
        ProposedEvent(team_id="t2", field_id="f1", event_type="practice", title="Mon late",
                      starts_at=at(9, 18), ends_at=at(9, 19)),
    ]


def _filter_json(**overrides: object) -> str:
    payload = {
        "team_aliases": [],
        "field_aliases": [],
        "weekdays": [],
        "date_from": "",
        "date_to": "",
        "event_types": [],
        "notes": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def _chat_script(responses: list[str]):  # type: ignore[no-untyped-def]
    calls: list[list[dict]] = []

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        calls.append(messages)
        if response_format is None:
            return "Remaining sessions stay as scheduled."  # explain step: plain words
        assert response_format["json_schema"]["name"] == "cancel_filter"
        assert response_format["json_schema"]["strict"] is True
        return responses.pop(0)

    chat.calls = calls  # type: ignore[attr-defined]
    return chat


def test_cancel_friday_practices_keeps_rest() -> None:
    chat = _chat_script([_filter_json(weekdays=[4], event_types=["practice"])])
    agent = ScheduleAgent(chat)
    proposal = agent.cancel(_problem(), _events(), "cancel all Friday practices")
    assert [e.title for e in proposal.events] == ["Mon", "Fri game", "Mon late"]  # original order kept
    assert all(c.passed for c in proposal.check_results)
    assert not proposal.shortfalls
    assert "Cancelled 1 session(s)" in proposal.summary
    assert "Fri" in proposal.summary and "Kept 3 session(s) unchanged" in proposal.summary
    assert [s["step"] for s in agent.last_trace] == ["cancel_filter", "cancel", "checks", "explain"]
    assert agent.approve(proposal).approved is True


def test_cancel_by_team_and_date_range() -> None:
    chat = _chat_script([_filter_json(team_aliases=["T1"], date_from="2026-11-06", date_to="2026-11-06")])
    proposal = ScheduleAgent(chat).cancel(_problem(), _events(), "drop Foxes sessions on Nov 6")
    assert [e.title for e in proposal.events] == ["Mon", "Fri game", "Mon late"]


def test_no_match_fails_safely() -> None:
    chat = _chat_script([_filter_json(team_aliases=["T2"], weekdays=[2])])  # Badgers have no Wednesday session
    with pytest.raises(AgentError, match="No sessions matched"):
        ScheduleAgent(chat).cancel(_problem(), _events(), "cancel Badgers Monday")
    assert len(chat.calls) == 1


def test_no_match_error_echoes_the_filter() -> None:
    chat = _chat_script([_filter_json(team_aliases=["T2"], event_types=["session"])])
    with pytest.raises(AgentError, match="Badgers") as exc_info:
        ScheduleAgent(chat).cancel(_problem(), _events(), "Badgers cancel next session")
    assert "'session'" in str(exc_info.value)


def test_unselective_filter_rejected_after_retry() -> None:
    chat = _chat_script([_filter_json(), _filter_json()])
    with pytest.raises(AgentError, match="could not understand"):
        ScheduleAgent(chat).cancel(_problem(), _events(), "cancel")
    assert len(chat.calls) == 2  # exactly one repair retry


def test_unknown_alias_dropped_and_reported() -> None:
    chat = _chat_script([_filter_json(team_aliases=["T9", "T2"])])
    proposal = ScheduleAgent(chat).cancel(_problem(), _events(), "cancel all Badgers sessions")
    assert [e.title for e in proposal.events] == ["Mon", "Fri"]  # kept order stable
    assert any("Unknown team" in note for note in proposal.rules.notes)


def test_inverted_dates_swapped_with_note() -> None:
    filt = parse_cancel_filter(
        _problem(), "x", _chat_script([_filter_json(date_from="2026-11-10", date_to="2026-11-03")])
    )
    assert (filt.date_from, filt.date_to) == (date(2026, 11, 3), date(2026, 11, 10))
    assert any("swapped" in note for note in filt.notes)


def test_bad_json_twice_raises_agent_error() -> None:
    chat = _chat_script(["not json", "{broken"])
    with pytest.raises(AgentError):
        ScheduleAgent(chat).cancel(_problem(), _events(), "cancel Friday")
    assert len(chat.calls) == 2


def test_provider_failure_becomes_model_error() -> None:
    def down(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        raise ModelError("boom")

    with pytest.raises(ModelError):
        ScheduleAgent(down).cancel(_problem(), _events(), "cancel Friday")


def test_filter_matches_dimensions() -> None:
    filt = CancelFilter(team_ids=["t1"], field_ids=["f1"], weekdays=[4],
                        date_from=date(2026, 11, 6), date_to=date(2026, 11, 6),
                        event_types=["Practice"])
    friday_foxes = _events()[1]
    assert filt.matches(friday_foxes, TZ) is True  # type matches case-insensitively
    assert filt.matches(_events()[0], TZ) is False  # Monday
    assert filt.matches(_events()[2], TZ) is False  # wrong team, field, type
    assert CancelFilter().is_selective is False
    assert CancelFilter(weekdays=[0]).is_selective is True
    assert CancelFilter(date_to=date(2026, 11, 6)).is_selective is True


def test_filter_round_trip() -> None:
    filt = CancelFilter(team_ids=["t1"], weekdays=[4, 0], date_from=date(2026, 11, 6),
                        event_types=["practice"], notes=["n"])
    assert CancelFilter.from_dict(json.loads(json.dumps(filt.to_dict()))) == filt


def test_dict_events_accepted() -> None:
    chat = _chat_script([_filter_json(weekdays=[4], event_types=["practice"])])
    raw = [e.to_dict() for e in _events()]
    proposal = ScheduleAgent(chat).cancel(_problem(), raw, "cancel all Friday practices")
    assert [e.title for e in proposal.events] == ["Mon", "Fri game", "Mon late"]
    assert Proposal.from_dict(json.loads(json.dumps(proposal.to_dict()))) == proposal
