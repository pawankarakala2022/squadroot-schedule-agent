"""Solver tests: placement, rules respected, shortfalls, determinism (offline)."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from schedule_agent import (
    Busy,
    Field,
    Problem,
    Requirement,
    Rules,
    Team,
)
from schedule_agent.solver import solve

TZ = "America/Toronto"


def _basic_problem() -> Problem:
    return Problem(
        teams=[Team(id="t1", name="U12"), Team(id="t2", name="U14")],
        fields=[Field(id="f1", name="North", open_from=time(17, 0), open_until=time(20, 0))],
        season_start=date(2026, 9, 7),  # a Monday
        season_end=date(2026, 9, 20),  # two full weeks
        timezone=TZ,
    )


def _basic_rules() -> Rules:
    return Rules(
        requirements=[
            Requirement(
                team_id="t1",
                event_type="practice",
                sessions_per_week=2,
                duration_minutes=60,
                allowed_days=[0, 1, 2, 3],
                allowed_fields=["f1"],
                earliest_start=time(17, 0),
                latest_end=time(20, 0),
            ),
            Requirement(
                team_id="t2",
                event_type="practice",
                sessions_per_week=2,
                duration_minutes=60,
                allowed_days=[0, 1, 2, 3],
                allowed_fields=["f1"],
                earliest_start=time(17, 0),
                latest_end=time(20, 0),
            ),
        ],
        min_gap_days=1,
    )


def test_places_all_sessions_without_double_booking() -> None:
    proposal = solve(_basic_problem(), _basic_rules())
    assert not proposal.shortfalls
    assert len(proposal.events) == 8  # 2 teams x 2 per week x 2 weeks
    for ev in proposal.events:
        assert ev.starts_at.tzinfo is not None
        assert ev.ends_at > ev.starts_at
    # No field or team overlap.
    for i, a in enumerate(proposal.events):
        for b in proposal.events[i + 1 :]:
            overlap = a.starts_at < b.ends_at and b.starts_at < a.ends_at
            if a.field_id == b.field_id or a.team_id == b.team_id:
                assert not overlap


def test_same_slot_each_week_where_possible() -> None:
    proposal = solve(_basic_problem(), _basic_rules())
    first_week = sorted(e.starts_at for e in proposal.events if e.starts_at.date() < date(2026, 9, 14))
    second_week = sorted(e.starts_at for e in proposal.events if e.starts_at.date() >= date(2026, 9, 14))
    first_slots = sorted((d.weekday(), d.time()) for d in first_week)
    second_slots = sorted((d.weekday(), d.time()) for d in second_week)
    assert first_slots == second_slots


def test_respects_busy_blackout_and_hours() -> None:
    tz = ZoneInfo(TZ)
    problem = Problem(
        teams=[Team(id="t1", name="U12")],
        fields=[Field(id="f1", name="North", open_from=time(17, 0), open_until=time(18, 0))],
        season_start=date(2026, 9, 7),
        season_end=date(2026, 9, 13),
        timezone=TZ,
        busy=[Busy(resource="field:f1", start=datetime(2026, 9, 7, 17, 0, tzinfo=tz), end=datetime(2026, 9, 7, 18, 0, tzinfo=tz))],
        blackout_dates=[date(2026, 9, 8)],
    )
    rules = Rules(
        requirements=[
            Requirement(
                team_id="t1",
                event_type="practice",
                sessions_per_week=2,
                duration_minutes=60,
                allowed_days=[0, 1, 2],
                earliest_start=time(17, 0),
                latest_end=time(18, 0),
            )
        ]
    )
    proposal = solve(problem, rules)
    for ev in proposal.events:
        assert ev.starts_at.date() != date(2026, 9, 8)
        assert not (ev.starts_at.date() == date(2026, 9, 7) and ev.starts_at.time() == time(17, 0))
        assert time(17, 0) <= ev.starts_at.time()
        assert ev.ends_at.time() <= time(18, 0)


def test_minimum_gap_blocks_same_day() -> None:
    problem = Problem(
        teams=[Team(id="t1", name="U12")],
        fields=[Field(id="f1", name="North", open_from=time(17, 0), open_until=time(20, 0))],
        season_start=date(2026, 9, 7),
        season_end=date(2026, 9, 13),
        timezone=TZ,
    )
    rules = Rules(
        requirements=[
            Requirement(
                team_id="t1",
                event_type="practice",
                sessions_per_week=2,
                duration_minutes=60,
                allowed_days=[0],  # Monday only, but gap 1 forbids two in one day
            )
        ],
        min_gap_days=1,
    )
    proposal = solve(problem, rules)
    assert len(proposal.events) == 1
    assert len(proposal.shortfalls) == 1
    assert proposal.shortfalls[0].requested == 2
    assert proposal.shortfalls[0].placed == 1


def test_tight_field_produces_shortfalls_with_reasons() -> None:
    problem = Problem(
        teams=[Team(id="t1", name="U12"), Team(id="t2", name="U14")],
        fields=[Field(id="f1", name="North", open_from=time(17, 0), open_until=time(18, 0))],
        season_start=date(2026, 9, 7),
        season_end=date(2026, 9, 13),
        timezone=TZ,
    )
    # One 60-min slot per Monday (17:00 only) but 4 sessions asked.
    rules = Rules(
        requirements=[
            Requirement(team_id="t1", event_type="practice", sessions_per_week=2, duration_minutes=60, allowed_days=[0]),
            Requirement(team_id="t2", event_type="practice", sessions_per_week=2, duration_minutes=60, allowed_days=[0]),
        ],
        min_gap_days=0,
    )
    proposal = solve(problem, rules)
    assert len(proposal.events) == 1
    assert proposal.shortfalls
    total_short = sum(s.requested - s.placed for s in proposal.shortfalls)
    assert total_short == 3
    for s in proposal.shortfalls:
        assert s.reason


def test_deterministic_same_input_same_output() -> None:
    first = solve(_basic_problem(), _basic_rules())
    second = solve(_basic_problem(), _basic_rules())
    assert first.to_dict() == second.to_dict()
