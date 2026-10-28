"""Performance regression tests: large tight seasons must stay fast.

The shapes below used to take tens of seconds (the solver scanned every
placed event and busy time for every candidate slot, and checks compared
every event pair). Thresholds are generous (slow CI headroom); the point
is to catch an algorithmic regression back to season-wide scans.
"""

from __future__ import annotations

import time
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from schedule_agent import Field, Problem, Proposal, ProposedEvent, Requirement, Rules, Team
from schedule_agent.checks import run_checks
from schedule_agent.explain import explain_proposal
from schedule_agent.solver import solve

TZ = "America/Toronto"


def _large_tight_problem() -> tuple[Problem, Rules]:
    teams = [Team(id=f"t{i}", name=f"Team{i}") for i in range(80)]
    fields = [
        Field(id=f"f{j}", name=f"Field{j}", open_from=dtime(17, 0), open_until=dtime(21, 0))
        for j in range(8)
    ]
    start = date(2026, 9, 1)
    problem = Problem(
        teams=teams,
        fields=fields,
        season_start=start,
        season_end=start + timedelta(days=24 * 7 - 1),
        timezone=TZ,
    )
    rules = Rules(
        requirements=[
            Requirement(
                team_id=t.id,
                event_type="practice",
                sessions_per_week=2,
                duration_minutes=60,
                allowed_days=[1, 3],
                earliest_start=dtime(17, 0),
                latest_end=dtime(21, 0),
            )
            for t in teams
        ],
        min_gap_days=1,
    )
    return problem, rules


def test_large_tight_season_solves_and_checks_quickly() -> None:
    problem, rules = _large_tight_problem()
    started = time.monotonic()
    proposal = solve(problem, rules)
    results = run_checks(problem, rules, proposal.events)
    elapsed = time.monotonic() - started
    assert len(proposal.events) > 1000
    assert proposal.shortfalls  # this shape is deliberately over-subscribed
    assert all(r.passed for r in results)
    assert elapsed < 10.0, f"solve+checks took {elapsed:.1f}s (was ~60s before day-bucket indexing)"


def test_explain_facts_stay_compact_for_a_full_season() -> None:
    tz = ZoneInfo(TZ)
    problem = Problem(
        teams=[Team(id="t1", name="Foxes")],
        fields=[Field(id="f1", name="North Field", open_from=dtime(17, 0), open_until=dtime(20, 0))],
        season_start=date(2026, 9, 1),
        season_end=date(2027, 5, 31),
        timezone=TZ,
    )
    events = []
    day = date(2026, 9, 1)  # a Tuesday
    for _ in range(40):
        events.append(
            ProposedEvent(
                team_id="t1",
                field_id="f1",
                event_type="practice",
                title="Foxes practice",
                starts_at=datetime.combine(day, dtime(17, 0), tzinfo=tz),
                ends_at=datetime.combine(day, dtime(18, 0), tzinfo=tz),
            )
        )
        day += timedelta(days=7)
    captured: dict = {}

    def chat(messages):  # type: ignore[no-untyped-def]
        captured["blob"] = " ".join(m.get("content", "") for m in messages)
        return "Done."

    text = explain_proposal(problem, Proposal(rules=Rules(), events=events), chat)
    assert text == "Done."
    assert "Foxes" in captured["blob"] and "North Field" in captured["blob"]
    assert "x40" in captured["blob"]
    assert len(captured["blob"]) < 3000  # one pattern line, not 40 session lines
