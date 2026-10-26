"""Understand tests: aliases, validation, one repair retry (fake chat, no network)."""

import json
from datetime import date, time

import pytest

from schedule_agent import Field, Problem, Team
from schedule_agent.errors import AgentError
from schedule_agent.understand import understand_request

TZ = "America/Toronto"


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


def _payload(requirements, **kwargs):  # type: ignore[no-untyped-def]
    body = {
        "requirements": requirements,
        "min_gap_days": 1,
        "blackout_dates": [],
        "notes": [],
    }
    body.update(kwargs)
    return json.dumps(body)


def _good_req(**over):  # type: ignore[no-untyped-def]
    req = {
        "team_alias": "T1",
        "event_type": "practice",
        "sessions_per_week": 2,
        "duration_minutes": 60,
        "allowed_days": [0, 2],
        "allowed_field_aliases": ["F1"],
        "earliest_start": "17:00",
        "latest_end": "20:00",
        "title": "",
    }
    req.update(over)
    return req


def test_aliases_mapped_no_retry_when_clean() -> None:
    calls: list = []

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        calls.append((messages, response_format))
        return _payload([_good_req()])

    rules = understand_request(_problem(), "U12 twice a week please", chat)
    assert len(calls) == 1
    assert response_format_is_strict(calls[0][1])
    (req,) = rules.requirements
    assert req.team_id == "t1"
    assert req.allowed_fields == ["f1"]
    assert req.title is None


def response_format_is_strict(response_format):  # type: ignore[no-untyped-def]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    assert response_format["json_schema"]["strict"] is True
    assert schema["additionalProperties"] is False
    return True


def test_unknowns_dropped_numbers_clamped_with_one_retry() -> None:
    first = _payload(
        [
            dict(_good_req(), team_alias="T9"),
            dict(
                _good_req(),
                sessions_per_week=99,
                duration_minutes=5,
                allowed_days=[0, 9],
                allowed_field_aliases=["F1", "F9"],
                earliest_start="25:00",
            ),
        ]
    )
    second = _payload([dict(_good_req(), sessions_per_week=99)])
    answers = [first, second]
    calls: list = []

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        calls.append(messages)
        return answers[min(len(calls) - 1, 1)]

    rules = understand_request(_problem(), "everyone every day", chat)
    assert len(calls) == 2  # exactly one repair retry
    assert [r.team_id for r in rules.requirements] == ["t1"]
    assert rules.requirements[0].sessions_per_week == 7  # clamped 0..7
    assert rules.requirements[0].duration_minutes == 60
    assert rules.notes, "drops and clamps must be reported"


def test_garbage_twice_raises_agent_error() -> None:
    calls: list = []

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        calls.append(messages)
        return "definitely not json"

    with pytest.raises(AgentError):
        understand_request(_problem(), "do something", chat)
    assert len(calls) == 2


def test_empty_request_fails_without_calling_model() -> None:
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        raise AssertionError("model must not be called")

    with pytest.raises(AgentError):
        understand_request(_problem(), "   ", chat)


def test_unusable_shape_after_retry_raises_agent_error() -> None:
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        return json.dumps({"requirements": "oops", "min_gap_days": 1, "blackout_dates": [], "notes": []})

    with pytest.raises(AgentError):
        understand_request(_problem(), "do something", chat)
