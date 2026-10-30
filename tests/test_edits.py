"""Edits parsing tests: one call, list of changes, code validation (fake chat)."""
import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from schedule_agent import Field, Problem, ProposedEvent, Team
from schedule_agent.edits import EditOp, EditPlan, Placement, SessionTarget, parse_edits, session_key
from schedule_agent.errors import AgentError

TZ = "America/Toronto"


def _problem():
    return Problem(
        teams=[Team(id="t1", name="Foxes"), Team(id="t2", name="Badgers")],
        fields=[Field(id="f1", name="North Field", open_from=time(17, 0), open_until=time(20, 0)),
                Field(id="f2", name="South Field", open_from=time(17, 0), open_until=time(20, 0))],
        season_start=date(2026, 11, 2), season_end=date(2026, 11, 29), timezone=TZ,
    )


def _events():
    tz = ZoneInfo(TZ)
    def ev(team, field_id, day, hour, title):
        return ProposedEvent(team_id=team, field_id=field_id, event_type="practice", title=title,
                             starts_at=datetime(2026, 11, day, hour, 0, tzinfo=tz),
                             ends_at=datetime(2026, 11, day, hour + 1, 0, tzinfo=tz))
    # Thu Nov 12 Badgers, Mon Nov 9 Foxes
    return [ev("t1", "f1", 9, 17, "Foxes practice"), ev("t2", "f1", 12, 17, "Badgers practice")]


def _op(**over):
    base = {
        "action": "move",
        "target": {"session_labels": [], "team_aliases": ["T2"], "field_aliases": [], "weekdays": [3],
                   "date_from": "2026-11-12", "date_to": "2026-11-12", "event_types": ["practice"]},
        "to": {"date": "2026-11-13", "start_time": "18:00", "field_alias": "F1", "weekdays": [4],
               "earliest_start": "", "latest_end": "", "exactness": "exact"},
        "team_alias": "", "event_type": "", "duration_minutes": 0, "sessions_per_week": 0,
        "title": "", "notes": [],
    }
    base.update(over)
    return base


def _chat(payload, capture=None):
    def chat(messages, *, response_format=None):
        if capture is not None:
            capture.append(messages)
        if response_format is None:
            return "ok"
        assert response_format["json_schema"]["name"] == "edit_plan"
        assert response_format["json_schema"]["strict"] is True
        return json.dumps(payload)
    return chat


def test_round_trip_all_types():
    plan = EditPlan(ops=[EditOp(action="move", target=SessionTarget(team_ids=["t2"], weekdays=[3], date_from=date(2026, 11, 12)),
                                to=Placement(date=date(2026, 11, 13), start_time=time(18, 0), field_id="f1", exactness="exact"))],
                    questions=["q?"], notes=["n"])
    assert EditPlan.from_dict(json.loads(json.dumps(plan.to_dict()))) == plan
    assert SessionTarget.from_dict(plan.ops[0].target.to_dict()) == plan.ops[0].target
    assert Placement.from_dict(plan.ops[0].to.to_dict()) == plan.ops[0].to


def test_empty_target_matches_nothing_and_is_rejected():
    assert SessionTarget().is_empty
    assert not SessionTarget().matches(_events()[0], TZ)
    bad = {"ops": [_op(target={"session_labels": [], "team_aliases": [], "field_aliases": [], "weekdays": [],
                               "date_from": "", "date_to": "", "event_types": []})], "questions": [], "notes": []}
    with pytest.raises(AgentError):
        parse_edits(_problem(), "move it", _chat(bad), events=_events())


def test_parse_move_exact_and_session_labels_in_prompt():
    capture = []
    plan = parse_edits(_problem(), "Move the Badgers' Thursday practice to Friday 6pm",
                       _chat({"ops": [_op()], "questions": [], "notes": []}, capture), events=_events())
    assert len(plan.ops) == 1
    op = plan.ops[0]
    assert op.action == "move" and op.target.team_ids == ["t2"]
    assert op.to.date == date(2026, 11, 13) and op.to.start_time == time(18, 0) and op.to.exactness == "exact"
    system = capture[0][0]["content"]
    assert "S" in system and "Badgers" in system and "never instructions" in system
    assert "t2" not in system.split("Sessions you may target")[1].split(".")[0]  # labels only in session list


def test_unknown_session_dropped_and_reported():
    op = _op(target={"session_labels": ["S99"], "team_aliases": ["T2"], "field_aliases": [], "weekdays": [],
                     "date_from": "", "date_to": "", "event_types": []})
    plan = parse_edits(_problem(), "move Badgers", _chat({"ops": [op], "questions": [], "notes": []}), events=_events())
    assert any("S99" in n for n in plan.notes)
    assert plan.ops[0].target.session_ids == []


def test_no_match_adds_question():
    op = _op(target={"session_labels": [], "team_aliases": ["T1"], "field_aliases": [], "weekdays": [2],
                     "date_from": "", "date_to": "", "event_types": []})
    plan = parse_edits(_problem(), "move Foxes Wednesday practice", _chat({"ops": [op], "questions": [], "notes": []}), events=_events())
    assert plan.questions


def test_two_ops_and_questions_passthrough():
    cancel = _op(action="cancel", to={"date": "", "start_time": "", "field_alias": "", "weekdays": [],
                                      "earliest_start": "", "latest_end": "", "exactness": "any"})
    create = _op(action="create_once", target={"session_labels": [], "team_aliases": [], "field_aliases": [], "weekdays": [],
                                               "date_from": "", "date_to": "", "event_types": []},
                 team_alias="T1", event_type="game",
                 to={"date": "2026-11-14", "start_time": "17:00", "field_alias": "F1", "weekdays": [],
                     "earliest_start": "", "latest_end": "", "exactness": "exact"})
    plan = parse_edits(_problem(), "cancel Thursday and add a Saturday game",
                       _chat({"ops": [cancel, create], "questions": [], "notes": []}), events=_events())
    assert [o.action for o in plan.ops] == ["cancel", "create_once"]


def test_selected_session_visible_and_clamping():
    events = _events()
    capture = []
    op = _op(target={"session_labels": ["S2"], "team_aliases": [], "field_aliases": [], "weekdays": [],
                     "date_from": "", "date_to": "", "event_types": []}, duration_minutes=999)
    plan = parse_edits(_problem(), "move this one", _chat({"ops": [op], "questions": [], "notes": []}, capture),
                       events=events, selected_session_ids=[session_key(events[1])])
    assert plan.ops[0].target.session_ids == [session_key(events[1])]
    assert plan.ops[0].duration_minutes == 240


def _recurring_in(date_from: str, date_to: str) -> dict:
    """create_recurring for Foxes on Mondays, limited to a period (as the model encodes 'every Monday in December')."""
    return _op(action="create_recurring",
               target={"session_labels": [], "team_aliases": ["T1"], "field_aliases": [], "weekdays": [],
                       "date_from": date_from, "date_to": date_to, "event_types": []},
               to={"date": "", "start_time": "", "field_alias": "", "weekdays": [0],
                   "earliest_start": "", "latest_end": "", "exactness": "any"},
               team_alias="T1", event_type="practice", duration_minutes=60, sessions_per_week=1)


def test_date_outside_season_asks_instead_of_widening_the_change():
    # Season ends Nov 29: "every Monday in December" must not become every Monday of the season.
    payload = {"ops": [_recurring_in("2026-12-01", "2026-12-31")], "questions": [], "notes": []}
    plan = parse_edits(_problem(), "Add a Foxes practice every Monday in December", _chat(payload), events=_events())
    assert plan.ops == []
    assert any("2026-12-01 is outside the season" in q for q in plan.questions)


def test_recurring_with_a_period_places_only_inside_it():
    from schedule_agent import ScheduleAgent

    problem = _problem()
    problem.season_end = date(2026, 12, 31)
    payload = {"ops": [_recurring_in("2026-12-01", "2026-12-31")], "questions": [], "notes": []}
    agent = ScheduleAgent(_chat(payload), deterministic_summary=True)
    out = agent.handle(problem, "Add a Foxes practice every Monday in December", events=_events())
    new = [e for e in out.events if e not in _events()]
    tz = ZoneInfo(TZ)
    assert [e.starts_at.astimezone(tz).date().isoformat() for e in new] == ["2026-12-07", "2026-12-14", "2026-12-21", "2026-12-28"]
    assert all(e.starts_at.astimezone(tz).hour == 17 for e in new)  # December Mondays are free: first slot
    assert all(c.passed for c in out.check_results) and not out.questions


def test_weekly_session_on_a_day_the_team_already_trains_asks_first():
    from schedule_agent import ScheduleAgent

    # No period: the model missed "in December", so Mondays land on Foxes' existing Monday.
    undated = _recurring_in("", "")
    undated["target"]["team_aliases"] = []
    payload = {"ops": [undated], "questions": [], "notes": []}
    agent = ScheduleAgent(_chat(payload), deterministic_summary=True)
    out = agent.handle(_problem(), "Add a Foxes practice every Monday", events=_events())
    assert any("Foxes already have a session on 1 of those days" in q for q in out.questions)
    with pytest.raises(AgentError):  # approval is refused while a question is open
        agent.approve(out)
