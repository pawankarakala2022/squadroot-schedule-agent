"""Worked-examples bank + EditPlan eval scoring (offline, fake chat)."""
import json
from collections import Counter

from evals.edit_cases import EDIT_CASES, edit_events, edit_problem, run_edit_suite, score_edit_case
from schedule_agent.edit_examples import EXAMPLES, edit_plan_accuracy, select_examples
from schedule_agent.edits import _plan_to_model_json


def test_bank_about_30_cases_all_languages_and_kinds():
    assert 28 <= len(EXAMPLES) <= 32
    assert len({e.id for e in EXAMPLES}) == len(EXAMPLES)
    counts = Counter(e.language for e in EXAMPLES)
    assert set(counts) == {"en", "es", "hi", "pa", "vi"}
    assert all(v >= 4 for v in counts.values())
    kinds = {e.kind for e in EXAMPLES}
    assert {"move", "block", "multi", "create", "selected", "injection", "change_rule"} <= kinds
    # Every currently-failing phrasing is present.
    text = " ".join(e.request for e in EXAMPLES)
    assert "Ignore all previous instructions" in text
    assert any(len(e.plan.ops) == 2 for e in EXAMPLES)  # several changes in one request
    assert any(e.plan.questions for e in EXAMPLES)


def test_select_examples_same_language_first_and_four():
    chosen = select_examples("Mueve la práctica de Badgers al viernes", language="es")
    assert len(chosen) == 4 and chosen[0].language == "es"
    en = select_examples("Move the Badgers Thursday practice to Friday 6pm", language="en")
    assert len(en) == 4 and en[0].language == "en" and en[0].plan.ops[0].action == "move"
    assert select_examples("anything", language="en") == select_examples("anything", language="en")


def test_edit_plan_accuracy_perfect_and_penalises():
    ex = next(e for e in EXAMPLES if e.id == "en-move-thu-fri-exact")
    assert edit_plan_accuracy(ex.plan, ex.plan) == 1.0
    other = next(e for e in EXAMPLES if e.id == "en-cancel-plus-create")
    assert edit_plan_accuracy(other.plan, ex.plan) < 0.6
    injection = next(e for e in EXAMPLES if e.kind == "injection")
    assert edit_plan_accuracy(injection.plan, injection.plan) == 1.0


def test_score_edit_case_with_fake_returning_expected_json():
    problem = edit_problem()
    by_id = {e.id: e for e in EDIT_CASES}

    def chat(messages, *, response_format=None):
        # Return the move example's plan in model (alias) form.
        return json.dumps(_plan_to_model_json(by_id["en-move-thu-fri-exact"].plan, problem))

    score = score_edit_case(by_id["en-move-thu-fri-exact"], chat)
    assert score.accuracy is not None and score.accuracy > 0.8
    scores = run_edit_suite(chat)
    assert len(scores) == len(EDIT_CASES)
