"""Offline tests for the v4.2.0 dynamic layer. Fake chats, no network."""

import json
from datetime import date, time

from schedule_agent import (
    Field,
    Problem,
    ScheduleAgent,
    Team,
    availability_to_busy,
    extract_availability,
    holiday_dates,
    parse_availability,
    relative_date_hints,
    us_federal_holidays,
    with_us_holidays,
)
from schedule_agent.cancel import parse_cancel_filter
from schedule_agent.disruption import parse_disruption
from schedule_agent.router import parse_route_intent

TZ = "America/Toronto"


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


def _system_text(calls: list) -> str:
    messages = calls[0]
    return " ".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))


def test_relative_date_hints_are_concrete() -> None:
    hints = relative_date_hints(date(2026, 10, 28))  # a Wednesday
    assert "Today is 2026-10-28" in hints
    assert "next Tuesday 2026-11-03" in hints
    assert "next week is 2026-11-02..2026-11-08" in hints
    assert "next month is 2026-11-01..2026-11-30" in hints


def test_disruption_and_cancel_prompts_carry_hints() -> None:
    seen: list = []

    def disruption_chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        seen.append(messages)
        return json.dumps({
            "date_from": "2026-11-12", "date_to": "2026-11-12",
            "closed_field_aliases": ["F1"], "all_fields": False,
            "search_days": 7, "not_before": "", "notes": [],
        })

    parse_disruption(_problem(), "rain next Thursday, North closed", disruption_chat)
    assert "next week is" in _system_text(seen)
    assert "never translate" in _system_text(seen)

    seen.clear()

    def cancel_chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        seen.append(messages)
        return json.dumps({
            "team_aliases": [], "field_aliases": [], "weekdays": [3],
            "date_from": "", "date_to": "", "event_types": [], "notes": [],
        })

    parse_cancel_filter(_problem(), "cancel next Thursday", cancel_chat)
    assert "next week is" in _system_text(seen)


def test_router_prompt_states_rain_default() -> None:
    seen: list = []

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        seen.append(messages)
        return json.dumps({"call": "cancel", "notes": []})

    route = parse_route_intent("rain Thursday", chat, has_events=True, problem=_problem())
    assert route.call == "cancel"
    assert "no move target named" in _system_text(seen)
    assert "next week is" in _system_text(seen)


def test_us_holidays_and_merge() -> None:
    holidays = us_federal_holidays(2026)
    assert holidays[date(2026, 11, 26)] == "Thanksgiving"
    assert holidays[date(2026, 12, 25)] == "Christmas Day"
    assert len(holidays) == 11
    assert holiday_dates(date(2026, 11, 1), date(2026, 11, 30)) == [date(2026, 11, 11), date(2026, 11, 26)]
    problem = _problem()
    merged = with_us_holidays(problem)
    assert date(2026, 11, 26) in merged.blackout_dates
    assert problem.blackout_dates == []  # caller's problem untouched


def test_parse_availability_expands_thursdays() -> None:
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        assert response_format["json_schema"]["name"] == "coach_unavailability"
        return json.dumps({
            "entries": [
                {"team_alias": "T1", "weekdays": [3], "date_from": "", "date_to": "",
                 "start_time": "17:00", "end_time": "20:00"},
                {"team_alias": "T9", "weekdays": [1], "date_from": "", "date_to": "",
                 "start_time": "17:00", "end_time": "18:00"},
            ],
            "notes": [],
        })

    result = parse_availability(_problem(), "Foxes coach can't do Thursdays 5-8pm", chat)
    assert len(result.busy) == 4  # Thursdays Nov 5, 12, 19, 26
    assert all(b.resource == "team:t1" and b.reason == "coach unavailable" for b in result.busy)
    assert any("T9" in note for note in result.notes)


def test_grounds_photo_to_busy() -> None:
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        assert response_format["json_schema"]["name"] == "grounds_availability"
        return json.dumps({
            "grounds": [
                {"field": "North Field", "weekdays": [1, 3], "open_from": "17:00", "open_until": "20:00"},
                {"field": "Centre", "weekdays": [1], "open_from": "17:00", "open_until": "20:00"},
            ],
            "coach": [
                {"team": "Foxes", "weekdays": [3], "date_from": "", "date_to": "",
                 "start_time": "17:00", "end_time": "18:00"},
            ],
            "notes": [],
        })

    imported = extract_availability(_problem(), b"png-bytes", chat)
    assert imported.grounds[0].matched and imported.grounds[0].field_id == "f1"
    assert not imported.grounds[1].matched  # unknown ground kept, never guessed
    busy = availability_to_busy(imported, _problem())
    monday = [b for b in busy if b.resource == "field:f1" and b.start.date() == date(2026, 11, 2)]
    assert monday and (monday[0].end - monday[0].start).days == 1  # whole Monday blocked
    coach = [b for b in busy if b.resource == "team:t1"]
    assert len(coach) == 4 and all(b.start.hour == 17 for b in coach)


def test_language_threads_to_explain() -> None:
    seen: list = []
    rules = json.dumps({
        "requirements": [{
            "team_alias": "T1", "event_type": "practice", "sessions_per_week": 1,
            "duration_minutes": 60, "allowed_days": [0], "allowed_field_aliases": [],
            "earliest_start": "17:00", "latest_end": "20:00", "title": "",
            "active_from": "", "active_until": "",
        }],
        "min_gap_days": 1, "blackout_dates": [], "blackout_ranges": [], "notes": [],
    })

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        seen.append((messages, response_format))
        return rules if response_format is not None else "Resumen en espanol."

    agent = ScheduleAgent(chat)
    proposal = agent.plan(_problem(), "Foxes practice Mondays", language="es")
    assert proposal.summary == "Resumen en espanol."
    explain_calls = [(m, f) for m, f in seen if f is None]
    text = " ".join(str(x.get("content", "")) for msgs, _ in explain_calls for x in msgs)
    assert "in es" in text and "never translate" in text
