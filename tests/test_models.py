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


def test_rules_new_fields_round_trip_exact() -> None:
    rules = Rules(
        requirements=[
            Requirement(
                team_id="t1",
                event_type="practice",
                sessions_per_week=1,
                duration_minutes=60,
                title="U12 Monday Practice",
            )
        ],
        min_gap_days=1,
        blackout_dates=[date(2026, 12, 25)],
        notes=["Could not map 'goalie clinic' to a known event type."],
    )
    assert Rules.from_dict(rules.to_dict()) == rules


def test_rules_from_dict_without_new_fields_uses_defaults() -> None:
    old = {
        "requirements": [
            {
                "team_id": "t1",
                "event_type": "practice",
                "sessions_per_week": 1,
                "duration_minutes": 60,
                "allowed_days": [],
                "allowed_fields": [],
                "earliest_start": None,
                "latest_end": None,
            }
        ],
        "min_gap_days": 1,
    }
    rules = Rules.from_dict(old)
    assert rules.blackout_dates == []
    assert rules.notes == []
    assert rules.requirements[0].title is None


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


def test_shortfall_summary_one_line_per_team_and_type() -> None:
    from schedule_agent import Shortfall

    rules = Rules(requirements=[])
    proposal = Proposal(
        rules=rules,
        shortfalls=[
            Shortfall(team_id="t2", event_type="game", week_start=date(2026, 9, 7), requested=1, placed=0, reason="B"),
            Shortfall(team_id="t1", event_type="practice", week_start=date(2026, 9, 7), requested=2, placed=1, reason="A"),
            Shortfall(team_id="t1", event_type="practice", week_start=date(2026, 9, 14), requested=2, placed=1, reason="A"),
            Shortfall(team_id="t1", event_type="practice", week_start=date(2026, 9, 21), requested=2, placed=0, reason="C"),
        ],
    )
    assert proposal.shortfalls  # per-week detail is kept
    lines = proposal.shortfall_summary().splitlines()
    assert len(lines) == 2  # one line per (team, event type)
    assert lines[0].startswith("t1 practice: 2 of 6 placed")
    assert "A" in lines[0]  # most common reason (2 of 3 weeks)
    assert lines[1].startswith("t2 game: 0 of 1 placed")
    assert "B" in lines[1]


def test_shortfall_summary_empty_without_shortfalls() -> None:
    proposal = Proposal(rules=Rules(requirements=[]))
    assert proposal.shortfall_summary() == ""
