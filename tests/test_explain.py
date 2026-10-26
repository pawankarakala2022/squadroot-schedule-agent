"""Explain tests: model text returned, failures become ModelError (fake chat)."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from schedule_agent import CheckResult, Field, Problem, Proposal, Requirement, Rules, Team
from schedule_agent.errors import ModelError
from schedule_agent.explain import explain_proposal
from schedule_agent.models import ProposedEvent

TZ = "America/Toronto"


def _problem() -> Problem:
    return Problem(
        teams=[Team(id="t1", name="U12")],
        fields=[Field(id="f1", name="North", open_from=time(17, 0), open_until=time(20, 0))],
        season_start=date(2026, 9, 7),
        season_end=date(2026, 9, 13),
        timezone=TZ,
    )


def _proposal() -> Proposal:
    tz = ZoneInfo(TZ)
    return Proposal(
        rules=Rules(
            requirements=[
                Requirement(team_id="t1", event_type="practice", sessions_per_week=1, duration_minutes=60)
            ],
            notes=["Goalie clinic could not be mapped."],
        ),
        events=[
            ProposedEvent(
                team_id="t1",
                field_id="f1",
                event_type="practice",
                title="U12 practice",
                starts_at=datetime(2026, 9, 7, 17, 0, tzinfo=tz),
                ends_at=datetime(2026, 9, 7, 18, 0, tzinfo=tz),
            )
        ],
        check_results=[CheckResult(check="busy", event_index=0, passed=True)],
    )


def test_returns_model_text_with_requested_language() -> None:
    seen: dict = {}

    def chat(messages):  # type: ignore[no-untyped-def]
        seen["messages"] = messages
        return "  Listo: un entrenamiento.  "

    text = explain_proposal(_problem(), _proposal(), chat, language="es")
    assert text == "Listo: un entrenamiento."
    blob = " ".join(m["content"] for m in seen["messages"])
    assert "es" in blob
    assert "U12" in blob and "North" in blob


def test_empty_response_is_model_error() -> None:
    def chat(messages):  # type: ignore[no-untyped-def]
        return "   "

    with pytest.raises(ModelError):
        explain_proposal(_problem(), _proposal(), chat)


def test_chat_failure_becomes_model_error() -> None:
    def chat(messages):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    with pytest.raises(ModelError):
        explain_proposal(_problem(), _proposal(), chat)
