"""Offline tests for Phase 7 evals: inventory, scoring math, fake end-to-end. No network."""

import json
from collections import Counter
from datetime import date, time

import pytest

from evals.cases import CASES, EvalCase
from evals.runner import CaseScore, main, render_markdown, rules_accuracy, run_suite, score_case
from schedule_agent import Problem, Requirement, Rules


def _rules() -> Rules:
    return Rules(
        requirements=[
            Requirement(
                team_id="t1",
                event_type="practice",
                sessions_per_week=2,
                duration_minutes=60,
                allowed_days=[0, 2],
                allowed_fields=[],
                earliest_start=time(17, 0),
                latest_end=time(20, 0),
            )
        ],
        min_gap_days=1,
    )


def test_25_unique_cases_cover_languages_and_kinds() -> None:
    assert len(CASES) == 25
    assert len({c.id for c in CASES}) == 25
    assert Counter(c.language for c in CASES) == {"en": 8, "es": 5, "hi": 4, "pa": 4, "vi": 4}
    kinds = {c.kind for c in CASES}
    assert {"basic", "tight", "blackout", "revision", "injection", "voice"} <= kinds
    assert sum(1 for c in CASES if c.expected is None) == 1  # injection scores safety only
    assert all(c.followup is None or c.kind == "revision" for c in CASES)
    assert all(c.problem.teams and c.problem.fields for c in CASES)


def test_expected_rules_are_sane() -> None:
    for case in CASES:
        if case.expected is None:
            continue
        assert case.expected.requirements, case.id
        for req in case.expected.requirements:
            assert req.team_id in {t.id for t in case.problem.teams}, case.id
            assert all(f in {x.id for x in case.problem.fields} for f in req.allowed_fields), case.id
            assert 0 <= req.sessions_per_week <= 7, case.id


def test_rules_accuracy_math() -> None:
    assert rules_accuracy(_rules(), _rules()) == 1.0
    off = _rules()
    off.requirements[0].sessions_per_week = 3
    assert 0.0 < rules_accuracy(off, _rules()) < 1.0
    missing = Rules(requirements=[], min_gap_days=1)
    assert rules_accuracy(missing, _rules()) < rules_accuracy(off, _rules())
    extra = _rules()
    extra.requirements.append(
        Requirement(team_id="t9", event_type="game", sessions_per_week=1,
                    duration_minutes=30, allowed_days=[5], allowed_fields=["fx"])
    )
    assert rules_accuracy(extra, _rules()) < 1.0  # invented requirements penalized


def _fake_chat(expected_json: str):  # type: ignore[no-untyped-def]
    def chat(messages, *, response_format=None, usage_out=None):  # type: ignore[no-untyped-def]
        if usage_out is not None:
            usage_out.update({"model": "fake", "prompt_tokens": 10, "completion_tokens": 20,
                              "total_tokens": 30})
        if response_format is not None:
            return expected_json
        return "Fake summary."

    return chat


def _expected_json(case: EvalCase) -> str:
    assert case.expected is not None
    out = {"requirements": [], "min_gap_days": case.expected.min_gap_days,
           "blackout_dates": [d.isoformat() for d in case.expected.blackout_dates], "notes": []}
    teams = {t.id: f"T{i + 1}" for i, t in enumerate(case.problem.teams)}
    fields = {f.id: f"F{i + 1}" for i, f in enumerate(case.problem.fields)}
    for req in case.expected.requirements:
        out["requirements"].append(
            {
                "team_alias": teams[req.team_id],
                "event_type": req.event_type,
                "sessions_per_week": req.sessions_per_week,
                "duration_minutes": req.duration_minutes,
                "allowed_days": list(req.allowed_days),
                "allowed_field_aliases": [a for a, fid in fields.items() if fid in req.allowed_fields],
                "earliest_start": req.earliest_start.isoformat() if req.earliest_start else "",
                "latest_end": req.latest_end.isoformat() if req.latest_end else "",
                "title": req.title or "",
            }
        )
    return json.dumps(out)


def test_end_to_end_fake_scores_perfect_basic_case() -> None:
    case = next(c for c in CASES if c.id == "en-basic")
    score = score_case(case, _fake_chat(_expected_json(case)), "fake-model")
    assert score.rule_accuracy == 1.0
    assert score.checks_passed == score.checks_total > 0
    assert score.shortfall_match is True
    assert score.prompt_tokens == 20  # understand + explain calls summed
    assert score.error is None


def test_end_to_end_fake_injection_refusal_is_safe() -> None:
    case = next(c for c in CASES if c.id == "en-injection")

    def refusing(messages, *, response_format=None, usage_out=None):  # type: ignore[no-untyped-def]
        return "not json at all"

    score = score_case(case, refusing, "fake-model")
    assert score.rule_accuracy is None
    assert score.safe is True
    assert score.error is not None and score.error.startswith("AgentError")


def test_run_suite_and_render_markdown() -> None:
    cases = [c for c in CASES if c.id in ("en-basic", "en-injection")]
    by_id = {c.id: c for c in cases}

    def chat(messages, *, response_format=None, usage_out=None):  # type: ignore[no-untyped-def]
        from schedule_agent.errors import AgentError

        if response_format is not None:
            name = response_format["json_schema"]["name"]
            if name == "scheduling_rules":
                raise AgentError("nope")
            return _expected_json(by_id["en-basic"])
        return "Fake summary."

    scores = run_suite(cases, chat, "fake-model")
    assert [s.case_id for s in scores] == ["en-basic", "en-injection"]
    report = render_markdown({"fake-model": scores}, {"fake-model": "fake-host"}, None, None)
    assert "| en-basic |" in report and "| en-injection |" in report
    assert "n/a (no prices given)" in report


def test_unknown_case_id_fails_fast_without_keys() -> None:
    with pytest.raises(SystemExit, match="Unknown case ids"):
        main(["--cases", "no-such-case", "--out", "nul"])


def test_case_scores_round_trip() -> None:
    score = CaseScore(
        case_id="en-basic", language="en", kind="basic", model="m",
        rule_accuracy=1.0, checks_passed=6, checks_total=6, shortfalls=0,
        shortfall_match=True, safe=None, latency_ms=5, prompt_tokens=10,
        completion_tokens=20, total_tokens=30, cost_usd=None,
        actual_rules="t1 practice 1x/wk 60min",
    )
    assert CaseScore.from_dict(json.loads(json.dumps(score.to_dict()))) == score
