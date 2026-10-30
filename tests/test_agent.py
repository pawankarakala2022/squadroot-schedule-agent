"""Agent loop tests: plan/revise, verify re-solves, trace (fake chat, no network)."""

import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from schedule_agent import ApplyResult, CheckResult, Proposal, ScheduleAgent
from schedule_agent.agent import MAX_VERIFY_ROUNDS
from schedule_agent.errors import AgentError, ModelError
from schedule_agent.models import Busy, Field, Problem, Shortfall, Team

TZ = "America/Toronto"


def _problem() -> Problem:
    return Problem(
        teams=[Team(id="t1", name="U12"), Team(id="t2", name="U14")],
        fields=[
            Field(id="f1", name="North", open_from=time(17, 0), open_until=time(20, 0)),
            Field(id="f2", name="South", open_from=time(17, 0), open_until=time(20, 0)),
        ],
        season_start=date(2026, 9, 7),
        season_end=date(2026, 9, 20),
        timezone=TZ,
    )


def _payload(requirements, **kwargs):  # type: ignore[no-untyped-def]
    body = {"requirements": requirements, "min_gap_days": 1, "blackout_dates": [], "notes": []}
    body.update(kwargs)
    return json.dumps(body)


def _req(alias: str = "T1", **over):  # type: ignore[no-untyped-def]
    req = {
        "team_alias": alias,
        "event_type": "practice",
        "sessions_per_week": 1,
        "duration_minutes": 60,
        "allowed_days": [0],
        "allowed_field_aliases": ["F1"],
        "earliest_start": "17:00",
        "latest_end": "19:00",
        "title": "",
    }
    req.update(over)
    return req


def _chat(payload: str | None = None):  # type: ignore[no-untyped-def]
    """Fake: rules JSON for understand calls, plain text for explain calls."""

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        if response_format is not None:
            return payload or _payload([_req()])
        return "Explained."

    return chat


def _overlaps(ev, busy: Busy) -> bool:  # type: ignore[no-untyped-def]
    return ev.starts_at < busy.end and busy.start < ev.ends_at


def test_plan_happy_path_trace_shape() -> None:
    agent = ScheduleAgent(_chat())
    proposal = agent.plan(_problem(), "U12 practice Mondays after 5pm")
    assert isinstance(proposal, Proposal)
    assert len(proposal.events) == 2  # 2 weeks x 1 session
    assert all(c.passed for c in proposal.check_results)
    assert proposal.summary == "Explained."
    assert [s["step"] for s in agent.last_trace] == ["understand", "rules", "solve", "checks", "verify", "explain"]
    verify = next(s for s in agent.last_trace if s["step"] == "verify")
    assert verify["reported"] == 0 and verify["re_solve"] is False  # no hook: accepted as-is
    json.dumps(agent.last_trace)  # every step must be display-safe


def test_plan_includes_uploaded_games_as_fixed_events() -> None:
    from datetime import time as dtime

    from schedule_agent import GameImport, ScheduleImport

    games = ScheduleImport(games=[
        GameImport(date=date(2026, 9, 14), start=dtime(17, 0), home="U12", away="Greyhawks Comets",
                   field="North", home_id="t1", away_id=None, field_id="f1",
                   note="unknown team 'Greyhawks Comets'"),
    ])
    agent = ScheduleAgent(_chat())
    proposal = agent.plan(_problem(), "U12 practice Mondays after 5pm", games=games)
    game_events = [e for e in proposal.events if e.event_type == "game"]
    assert len(game_events) == 1
    assert game_events[0].title == "U12 v Greyhawks Comets"
    assert game_events[0].starts_at.date() == date(2026, 9, 14)
    assert len(proposal.events) == 3  # 2 Monday practices + the fixed game
    assert all(c.passed for c in proposal.check_results)
    assert any(s.get("step") == "games" and s["events"] == 1 for s in agent.last_trace)
    assert agent.approve(proposal).approved is True


def test_plan_verify_round_resolves_host_busy() -> None:
    tz = ZoneInfo(TZ)
    busy = Busy(
        resource="team:t1",
        start=datetime(2026, 9, 7, 17, 0, tzinfo=tz),
        end=datetime(2026, 9, 7, 18, 0, tzinfo=tz),
        reason="coach unavailable",
    )
    calls: list = []

    def verify(events):  # type: ignore[no-untyped-def]
        calls.append(list(events))
        return [busy] if any(_overlaps(e, busy) for e in events) else []

    agent = ScheduleAgent(_chat())
    proposal = agent.plan(_problem(), "U12 practice Mondays", verify=verify)
    assert len(calls) == 2  # draft, then re-solved draft accepted
    assert len(proposal.events) == 2
    assert all(not _overlaps(e, busy) for e in proposal.events)
    assert all(c.passed for c in proposal.check_results)
    verifies = [s for s in agent.last_trace if s["step"] == "verify"]
    assert [v["round"] for v in verifies] == [0, 1]
    assert verifies[0]["re_solve"] is True
    assert verifies[1]["reported"] == 0


def test_verify_capped_at_three_resolves() -> None:
    calls: list = []

    def verify(events):  # type: ignore[no-untyped-def]
        calls.append(list(events))
        first = events[0]  # always blame the first draft event: always something new
        return [Busy(resource="team:t1", start=first.starts_at, end=first.ends_at)]

    agent = ScheduleAgent(_chat())
    agent.plan(_problem(), "U12 practice Mondays", verify=verify)
    assert len(calls) == MAX_VERIFY_ROUNDS + 1  # initial draft + 3 re-solves, then stop
    verifies = [s for s in agent.last_trace if s["step"] == "verify"]
    assert len(verifies) == MAX_VERIFY_ROUNDS + 1
    assert verifies[-1]["re_solve"] is False  # cap hit with busy still pending


def test_verify_echo_stops_early_without_extra_solves() -> None:
    tz = ZoneInfo(TZ)
    busy = Busy(
        resource="team:t1",
        start=datetime(2026, 9, 7, 17, 0, tzinfo=tz),
        end=datetime(2026, 9, 7, 18, 0, tzinfo=tz),
    )
    calls: list = []

    def verify(events):  # type: ignore[no-untyped-def]
        calls.append(list(events))
        return [busy]  # host keeps echoing the same busy time

    agent = ScheduleAgent(_chat())
    proposal = agent.plan(_problem(), "U12 practice Mondays", verify=verify)
    assert len(calls) == 2  # second report adds nothing new, so no third solve
    assert len([s for s in agent.last_trace if s["step"] == "solve"]) == 2
    assert all(not _overlaps(e, busy) for e in proposal.events)


def test_revise_sends_previous_context_and_keeps_events() -> None:
    seen: list = []

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        seen.append(messages)
        if response_format is not None:
            return _payload([_req()])
        return "Explained."

    agent = ScheduleAgent(chat)
    problem = _problem()
    proposal = agent.plan(problem, "U12 practice Mondays")
    assert not any("Previous schedule context" in str(m) for m in seen)
    revised = agent.revise(problem, proposal, "keep it, just confirm")
    assert revised.events == proposal.events
    understand_messages = seen[-2]  # revise's understand call (repair retry would add more)
    assert any("Previous schedule context" in str(m) and "T1" in str(m) for m in understand_messages)
    as_dict = agent.revise(problem, proposal.to_dict(), "keep it, just confirm")
    assert as_dict.events == proposal.events


def test_bad_previous_and_proposal_raise_agent_error() -> None:
    agent = ScheduleAgent(_chat())
    problem = _problem()
    with pytest.raises(AgentError):
        agent.plan(problem, "U12 Mondays", previous=42)  # type: ignore[arg-type]
    with pytest.raises(AgentError):
        agent.plan(problem, "U12 Mondays", previous={"requirements": "oops"})
    with pytest.raises(AgentError):
        agent.revise(problem, "nope", "move it")  # type: ignore[arg-type]


def test_empty_request_raises_without_calling_model() -> None:
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        raise AssertionError("model must not be called")

    with pytest.raises(AgentError):
        ScheduleAgent(chat).plan(_problem(), "   ")


def test_explain_chat_separate_from_understand() -> None:
    def explain(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        assert response_format is None
        return "EXPLAINED"

    proposal = ScheduleAgent(_chat(), explain_chat=explain).plan(_problem(), "U12 Mondays")
    assert proposal.summary == "EXPLAINED"


def test_model_error_propagates() -> None:
    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        raise ModelError("down")

    with pytest.raises(ModelError):
        ScheduleAgent(chat).plan(_problem(), "U12 Mondays")


def test_usage_out_tokens_land_in_trace() -> None:
    def chat(messages, *, response_format=None, usage_out=None):  # type: ignore[no-untyped-def]
        if usage_out is not None:
            usage_out.update({"model": "fake-m", "prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8})
        if response_format is not None:
            return _payload([_req()])
        return "Explained."

    agent = ScheduleAgent(chat)
    agent.plan(_problem(), "U12 Mondays")
    understand = next(s for s in agent.last_trace if s["step"] == "understand")
    assert understand["model"] == "fake-m"
    assert understand["total_tokens"] == 8


def test_malformed_verify_hook_fails_safely() -> None:
    agent = ScheduleAgent(_chat())
    with pytest.raises(AgentError):
        agent.plan(_problem(), "U12 Mondays", verify=lambda events: ["nope"])  # type: ignore[return-value]
    with pytest.raises(AgentError):
        agent.plan(_problem(), "U12 Mondays", verify=lambda events: "nope")  # type: ignore[return-value]
    ok = agent.plan(_problem(), "U12 Mondays", verify=lambda events: None)  # type: ignore[return-value]
    assert len(ok.events) == 2


def test_constructor_rejects_non_callable() -> None:
    with pytest.raises(TypeError):
        ScheduleAgent("chat")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ScheduleAgent(_chat(), explain_chat=42)  # type: ignore[arg-type]


def test_approve_checked_draft_returns_apply_result() -> None:
    agent = ScheduleAgent(_chat())
    proposal = agent.plan(_problem(), "U12 Mondays")
    before = proposal.to_dict()
    result = agent.approve(proposal)
    assert isinstance(result, ApplyResult)
    assert result.approved is True
    assert result.events == proposal.events
    assert result.checks_passed == result.checks_total == len(proposal.check_results) > 0
    assert ApplyResult.from_dict(json.loads(json.dumps(result.to_dict()))) == result
    assert proposal.to_dict() == before  # approve changes nothing
    assert agent.last_trace[-1]["step"] == "approve"


def test_approve_refuses_failing_or_unchecked_drafts() -> None:
    agent = ScheduleAgent(_chat())
    proposal = agent.plan(_problem(), "U12 Mondays")
    failing = Proposal(
        rules=proposal.rules,
        events=proposal.events,
        check_results=proposal.check_results
        + [CheckResult(check="busy", event_index=0, passed=False, reason="Team 't1' is busy at this time.")],
        shortfalls=proposal.shortfalls,
        summary=proposal.summary,
    )
    with pytest.raises(AgentError):
        agent.approve(failing)
    with pytest.raises(AgentError):
        agent.approve(Proposal(rules=proposal.rules))  # no check results at all
    with pytest.raises(AgentError):
        agent.approve("nope")  # type: ignore[arg-type]
    assert ApplyResult().approved is False  # default construction is not an approval


def test_approve_accepts_dict_and_shortfalls_do_not_block() -> None:
    agent = ScheduleAgent(_chat())
    proposal = agent.plan(_problem(), "U12 Mondays")
    proposal.shortfalls.append(
        Shortfall(
            team_id="t1",
            event_type="practice",
            week_start=date(2026, 9, 7),
            requested=2,
            placed=1,
            reason="Only 1 of 2 placed.",
        )
    )
    result = agent.approve(proposal.to_dict())
    assert result.approved is True
    assert result.events == proposal.events


def test_plan_is_deterministic() -> None:
    problem = _problem()
    agent_a, agent_b = ScheduleAgent(_chat()), ScheduleAgent(_chat())
    first = agent_a.plan(problem, "U12 Mondays")
    second = agent_b.plan(problem, "U12 Mondays")
    assert first.events == second.events
    assert first.summary == second.summary
    timeless = lambda trace: [{k: v for k, v in step.items() if k != "latency_ms"} for step in trace]
    assert timeless(agent_a.last_trace) == timeless(agent_b.last_trace)
