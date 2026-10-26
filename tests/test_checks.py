"""Checks tests: each named check catches its own problem; solver output is clean."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from schedule_agent import Busy, Field, Problem, ProposedEvent, Requirement, Rules, Team
from schedule_agent.checks import CHECK_NAMES, run_checks
from schedule_agent.solver import solve

TZ = "America/Toronto"


def _problem() -> Problem:
    tz = ZoneInfo(TZ)
    return Problem(
        teams=[Team(id="t1", name="U12"), Team(id="t2", name="U14")],
        fields=[
            Field(id="f1", name="North", open_from=time(17, 0), open_until=time(20, 0)),
            Field(id="f2", name="South", open_from=time(17, 0), open_until=time(20, 0)),
        ],
        season_start=date(2026, 9, 7),
        season_end=date(2026, 9, 13),
        timezone=TZ,
        busy=[
            Busy(
                resource="team:t1",
                start=datetime(2026, 9, 8, 17, 0, tzinfo=tz),
                end=datetime(2026, 9, 8, 18, 0, tzinfo=tz),
            )
        ],
        blackout_dates=[date(2026, 9, 9)],
    )


def _event(team: str, field: str, day: int, start: time, end: time) -> ProposedEvent:
    tz = ZoneInfo(TZ)
    return ProposedEvent(
        team_id=team,
        field_id=field,
        event_type="practice",
        title=f"{team} practice",
        starts_at=datetime(2026, 9, day, start.hour, start.minute, tzinfo=tz),
        ends_at=datetime(2026, 9, day, end.hour, end.minute, tzinfo=tz),
    )


def _rules(min_gap_days: int = 1) -> Rules:
    return Rules(requirements=[], min_gap_days=min_gap_days)


def _by_check(results):  # type: ignore[no-untyped-def]
    return {(r.check, r.event_index): r for r in results}


def test_result_count_is_checks_times_events() -> None:
    events = [_event("t1", "f1", 7, time(17, 0), time(18, 0))]
    results = run_checks(_problem(), _rules(), events)
    assert len(results) == len(CHECK_NAMES) * len(events)
    assert [r.check for r in results] == list(CHECK_NAMES)
    assert all(r.passed and r.reason == "" for r in results)


def test_field_double_booking_caught() -> None:
    events = [
        _event("t1", "f1", 7, time(17, 0), time(18, 0)),
        _event("t2", "f1", 7, time(17, 30), time(18, 30)),
    ]
    got = _by_check(run_checks(_problem(), _rules(), events))
    assert not got[("field_double_booking", 0)].passed
    assert not got[("field_double_booking", 1)].passed
    assert "double booked" in got[("field_double_booking", 0)].reason
    assert got[("team_overlap", 0)].passed
    assert got[("team_overlap", 1)].passed


def test_team_overlap_caught() -> None:
    events = [
        _event("t1", "f1", 7, time(17, 0), time(18, 0)),
        _event("t1", "f2", 7, time(17, 30), time(18, 30)),
    ]
    got = _by_check(run_checks(_problem(), _rules(), events))
    assert not got[("team_overlap", 0)].passed
    assert not got[("team_overlap", 1)].passed
    assert got[("field_double_booking", 0)].passed
    assert got[("field_double_booking", 1)].passed


def test_field_hours_caught() -> None:
    events = [_event("t1", "f1", 7, time(16, 0), time(17, 0))]
    got = _by_check(run_checks(_problem(), _rules(), events))
    assert not got[("field_hours", 0)].passed
    assert "hours" in got[("field_hours", 0)].reason
    for check in ("field_double_booking", "team_overlap", "blackout_dates", "busy", "minimum_gap"):
        assert got[(check, 0)].passed


def test_blackout_dates_caught() -> None:
    events = [_event("t1", "f1", 9, time(17, 0), time(18, 0))]
    got = _by_check(run_checks(_problem(), _rules(), events))
    assert not got[("blackout_dates", 0)].passed
    assert "2026-09-09" in got[("blackout_dates", 0)].reason
    for check in ("field_double_booking", "team_overlap", "field_hours", "busy", "minimum_gap"):
        assert got[(check, 0)].passed


def test_busy_caught() -> None:
    events = [_event("t1", "f1", 8, time(17, 30), time(18, 30))]
    got = _by_check(run_checks(_problem(), _rules(), events))
    assert not got[("busy", 0)].passed
    assert "busy" in got[("busy", 0)].reason
    for check in ("field_double_booking", "team_overlap", "field_hours", "blackout_dates", "minimum_gap"):
        assert got[(check, 0)].passed


def test_minimum_gap_caught() -> None:
    events = [
        _event("t1", "f1", 7, time(17, 0), time(18, 0)),
        _event("t1", "f2", 7, time(18, 0), time(19, 0)),
    ]
    got = _by_check(run_checks(_problem(), _rules(min_gap_days=1), events))
    assert not got[("minimum_gap", 0)].passed
    assert not got[("minimum_gap", 1)].passed
    assert got[("team_overlap", 0)].passed
    assert got[("field_double_booking", 0)].passed
    # Gap rule off: the same pair passes.
    ok = _by_check(run_checks(_problem(), _rules(min_gap_days=0), events))
    assert ok[("minimum_gap", 0)].passed
    assert ok[("minimum_gap", 1)].passed


def test_solver_output_passes_all_checks() -> None:
    problem = Problem(
        teams=[Team(id="t1", name="U12"), Team(id="t2", name="U14")],
        fields=[Field(id="f1", name="North", open_from=time(17, 0), open_until=time(20, 0))],
        season_start=date(2026, 9, 7),
        season_end=date(2026, 9, 20),
        timezone=TZ,
    )
    rules = Rules(
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
    proposal = solve(problem, rules)
    assert len(proposal.events) == 8
    results = run_checks(problem, rules, proposal.events)
    assert len(results) == len(CHECK_NAMES) * 8
    assert all(r.passed for r in results)
