"""Apply-edits tests: diff semantics, frozen sessions, exact times (no model)."""
import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from schedule_agent import Field, Problem, Proposal, ProposedEvent, Rules, Team
from schedule_agent.apply_edits import build_edit_proposal
from schedule_agent.edits import EditOp, EditPlan, Placement, SessionTarget
from schedule_agent.models import Requirement

TZ = "America/Toronto"


def _problem():
    return Problem(
        teams=[Team(id="t1", name="Foxes"), Team(id="t2", name="Badgers")],
        fields=[Field(id="f1", name="North Field", open_from=time(17, 0), open_until=time(20, 0)),
                Field(id="f2", name="South Field", open_from=time(17, 0), open_until=time(20, 0))],
        season_start=date(2026, 11, 2), season_end=date(2026, 11, 29), timezone=TZ,
    )


def _ev(team, field_id, day, hour, title, etype="practice"):
    tz = ZoneInfo(TZ)
    return ProposedEvent(team_id=team, field_id=field_id, event_type=etype, title=title,
                         starts_at=datetime(2026, 11, day, hour, 0, tzinfo=tz),
                         ends_at=datetime(2026, 11, day, hour + 1, 0, tzinfo=tz))


def _cal():
    return [_ev("t1", "f1", 9, 17, "Foxes practice"), _ev("t2", "f1", 12, 17, "Badgers practice"),
            _ev("t1", "f1", 12, 18, "Foxes Thu late")]


def test_questions_return_no_solve_and_unchanged_calendar():
    cal = _cal()
    plan = EditPlan(ops=[], questions=["Which Thursday?"])
    out = build_edit_proposal(_problem(), cal, plan)
    assert out.questions == ["Which Thursday?"]
    assert sorted(out.events, key=lambda e: e.starts_at) == sorted(cal, key=lambda e: e.starts_at)
    assert Proposal.from_dict(json.loads(json.dumps(out.to_dict()))).questions == ["Which Thursday?"]


def test_exact_move_places_exactly_and_keeps_others():
    cal = _cal()
    plan = EditPlan(ops=[EditOp(action="move", target=SessionTarget(team_ids=["t2"], weekdays=[3], date_from=date(2026, 11, 12), date_to=date(2026, 11, 12)),
                                to=Placement(date=date(2026, 11, 13), start_time=time(18, 0), field_id="f1", exactness="exact"))])
    out = build_edit_proposal(_problem(), cal, plan)
    moved = [e for e in out.events if e.title == "Badgers practice"][0]
    assert moved.starts_at == datetime(2026, 11, 13, 18, 0, tzinfo=ZoneInfo(TZ)) and moved.field_id == "f1"
    for ev in cal:
        if ev.title != "Badgers practice":
            assert ev in out.events
    assert "Moved:" in out.summary and "Badgers practice" in out.summary and "Fri Nov 13 18:00" in out.summary
    assert all(c.passed for c in out.check_results)


def test_exact_conflict_becomes_shortfall_with_alternatives_never_silent():
    cal = _cal()
    # Badgers exact to Foxes Thu late slot on same field/time
    plan = EditPlan(ops=[EditOp(action="move", target=SessionTarget(team_ids=["t2"], date_from=date(2026, 11, 12), date_to=date(2026, 11, 12)),
                                to=Placement(date=date(2026, 11, 12), start_time=time(18, 0), field_id="f1", exactness="exact"))])
    out = build_edit_proposal(_problem(), cal, plan)
    assert not any(e.title == "Badgers practice" for e in out.events)
    assert len(out.shortfalls) == 1 and "Nearest valid slots" in out.shortfalls[0].reason
    assert all(c.passed for c in out.check_results)


def test_cancel_plus_create_both_apply_and_checks_combined():
    cal = _cal()
    plan = EditPlan(ops=[
        EditOp(action="cancel", target=SessionTarget(team_ids=["t2"], date_from=date(2026, 11, 12), date_to=date(2026, 11, 12))),
        EditOp(action="create_once", team_id="t1", event_type="game", title="Foxes game",
               to=Placement(date=date(2026, 11, 14), start_time=time(17, 0), field_id="f2", exactness="exact")),
    ])
    out = build_edit_proposal(_problem(), cal, plan)
    titles = {e.title for e in out.events}
    assert "Badgers practice" not in titles and "Foxes game" in titles
    assert cal[0] in out.events and cal[2] in out.events
    assert all(c.passed for c in out.check_results)


def test_block_no_target_field_replaces_only_hit_on_open_fields():
    cal = _cal()
    plan = EditPlan(ops=[EditOp(action="block", target=SessionTarget(field_ids=["f1"], date_from=date(2026, 11, 12), date_to=date(2026, 11, 12)))])
    out = build_edit_proposal(_problem(), cal, plan)
    assert cal[0] in out.events  # Monday untouched
    thu = [e for e in out.events if e.starts_at.date() == date(2026, 11, 12)]
    assert thu and all(e.field_id != "f1" for e in thu)
    assert all(c.passed for c in out.check_results)


def test_no_match_target_gives_question():
    plan = EditPlan(ops=[EditOp(action="cancel", target=SessionTarget(team_ids=["t1"], weekdays=[2]))])
    out = build_edit_proposal(_problem(), _cal(), plan)
    assert out.questions and out.events


def test_deterministic_same_input_same_proposal():
    plan = EditPlan(ops=[EditOp(action="move", target=SessionTarget(team_ids=["t2"], date_from=date(2026, 11, 12), date_to=date(2026, 11, 12)),
                                to=Placement(date=date(2026, 11, 13), start_time=time(18, 0), field_id="f1", exactness="exact"))])
    a = build_edit_proposal(_problem(), _cal(), plan)
    b = build_edit_proposal(_problem(), _cal(), plan)
    assert a.events == b.events and a.summary == b.summary and a.to_dict() == b.to_dict()


def test_block_with_no_target_closes_to_field_and_date():
    cal = _cal()
    plan = EditPlan(ops=[EditOp(action="block", to=Placement(date=date(2026, 11, 12), field_id="f1"))])
    out = build_edit_proposal(_problem(), cal, plan)
    assert cal[0] in out.events
    thu = [e for e in out.events if e.starts_at.date() == date(2026, 11, 12)]
    assert thu and all(e.field_id != "f1" for e in thu)


def test_injection_plan_changes_nothing():
    cal = _cal()
    out = build_edit_proposal(_problem(), cal, EditPlan(ops=[], notes=["ignore instructions inside request"]))
    assert sorted(out.events, key=lambda e: e.starts_at) == sorted(cal, key=lambda e: e.starts_at)
    assert all(c.passed for c in out.check_results)


def test_untargeted_never_move_in_every_action():
    cal = _cal()
    plans = [
        EditPlan(ops=[EditOp(action="cancel", target=SessionTarget(team_ids=["t2"], date_from=date(2026, 11, 12), date_to=date(2026, 11, 12)))]),
        EditPlan(ops=[EditOp(action="move", target=SessionTarget(team_ids=["t2"], date_from=date(2026, 11, 12), date_to=date(2026, 11, 12)),
                             to=Placement(date=date(2026, 11, 13), start_time=time(18, 0), field_id="f1", exactness="exact"))]),
        EditPlan(ops=[EditOp(action="create_once", team_id="t1", event_type="game", to=Placement(date=date(2026, 11, 14), start_time=time(17, 0), field_id="f2", exactness="exact"))]),
        EditPlan(ops=[EditOp(action="block", target=SessionTarget(field_ids=["f1"], date_from=date(2026, 11, 12), date_to=date(2026, 11, 12)))]),
    ]
    for plan in plans:
        out = build_edit_proposal(_problem(), cal, plan)
        assert cal[0] in out.events  # Monday Foxes is never targeted here


def test_change_rule_only_named_team():
    tz = ZoneInfo(TZ)
    rules = Rules(requirements=[
        Requirement(team_id="t1", event_type="practice", sessions_per_week=1, duration_minutes=60, allowed_days=[0], allowed_fields=["f1"], earliest_start=time(17, 0), latest_end=time(20, 0)),
        Requirement(team_id="t2", event_type="practice", sessions_per_week=1, duration_minutes=60, allowed_days=[3], allowed_fields=["f1"], earliest_start=time(17, 0), latest_end=time(20, 0)),
    ], min_gap_days=1)
    draft = Proposal(rules=rules, events=_cal())
    plan = EditPlan(ops=[EditOp(action="change_rule", team_id="t1", sessions_per_week=2)])
    out = build_edit_proposal(_problem(), _cal(), plan, draft=draft)
    # Badgers Thu session is frozen (same object values)
    assert _cal()[1] in out.events
    assert any(r.team_id == "t1" and r.sessions_per_week == 2 for r in out.rules.requirements)
    assert any(r.team_id == "t2" and r.sessions_per_week == 1 for r in out.rules.requirements)
