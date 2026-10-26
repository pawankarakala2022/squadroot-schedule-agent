"""Model validation and JSON round-trip (offline, no AI calls)."""

from datetime import date, time

import pytest

from schedule_agent import (
    Busy,
    Field,
    Problem,
    Proposal,
    Requirement,
    Rules,
    Team,
)
from schedule_agent.models import ProposedEvent


def _problem() -> Problem:
    return Problem(
        teams=[Team(id="t1", name="U12")],
        fields=[Field(id="f1", name="North", open_from=time(17, 0), open_until=time(20, 0))],
        season_start=date(2026, 9, 7),
        season_end=date(2026, 9, 13),
        timezone="America/Toronto",
    )


def test_team_rejects_empty_id() -> None:
    with pytest.raises(ValueError):
        Team(id="", name="U12")


def test_field_open_hours_must_pair_and_order() -> None:
    with pytest.raises(ValueError):
        Field(id="f1", name="N", open_from=time(17, 0))
    with pytest.raises(ValueError):
        Field(id="f1", name="N", open_from=time(20, 0), open_until=time(17, 0))


def test_datetimes_must_be_timezone_aware() -> None:
    from datetime import datetime

    naive = datetime(2026, 9, 7, 17, 0)
    with pytest.raises(ValueError):
        Busy(resource="team:t1", start=naive, end=naive)


def test_problem_rejects_bad_season_and_timezone() -> None:
    with pytest.raises(ValueError):
        Problem(
            teams=[],
            fields=[],
            season_start=date(2026, 9, 13),
            season_end=date(2026, 9, 7),
            timezone="America/Toronto",
        )
    with pytest.raises(ValueError):
        Problem(
            teams=[],
            fields=[],
            season_start=date(2026, 9, 7),
            season_end=date(2026, 9, 13),
            timezone="Not/AZone",
        )


def test_rules_round_trip_exact() -> None:
    rules = Rules(
        requirements=[
            Requirement(
                team_id="t1",
                event_type="practice",
                sessions_per_week=2,
                duration_minutes=60,
                allowed_days=[0, 2],
                allowed_fields=["f1"],
                earliest_start=time(17, 0),
                latest_end=time(20, 0),
            )
        ],
        min_gap_days=2,
    )
    assert Rules.from_dict(rules.to_dict()) == rules


def test_proposal_round_trip_exact() -> None:
    problem = _problem()
    rules = Rules(
        requirements=[
            Requirement(team_id="t1", event_type="practice", sessions_per_week=1, duration_minutes=60)
        ]
    )
    proposal = Proposal(rules=rules, events=[], shortfalls=[], summary="")
    clone = Proposal.from_dict(proposal.to_dict())
    assert clone == proposal
    assert problem.to_dict()["timezone"] == "America/Toronto"


def test_requirement_rejects_bad_day() -> None:
    with pytest.raises(ValueError):
        Requirement(team_id="t1", event_type="practice", sessions_per_week=1, duration_minutes=60, allowed_days=[7])
