"""EditPlan eval cases: the worked-examples bank reused as evals.

Scored separately from solving (see ``score_edit_case``): a good
score means ``parse_edits`` understood the request; whether the
solver then places the diff is scored by the rule evals in
``evals/runner.py``. All cases are fictional, in en/es/hi/pa/vi.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from schedule_agent import Field, Problem, ProposedEvent, Team
from schedule_agent.edit_examples import EXAMPLES, EditExample, edit_plan_accuracy
from schedule_agent.edits import parse_edits
from schedule_agent.errors import AgentError, ModelError

TZ = "America/Toronto"

# Reuse the very same pairs as the few-shot bank.
EDIT_CASES: list[EditExample] = list(EXAMPLES)
CASES = EDIT_CASES


def edit_problem() -> Problem:
    """The fictional club the bank is written against (Nov 2026)."""
    return Problem(
        teams=[Team(id="t1", name="Foxes"), Team(id="t2", name="Badgers"),
               Team(id="t3", name="Otters"), Team(id="t4", name="Wolves")],
        fields=[Field(id="f1", name="North Field", open_from=time(17, 0), open_until=time(20, 0)),
                Field(id="f2", name="South Field", open_from=time(17, 0), open_until=time(20, 0))],
        season_start=date(2026, 11, 2), season_end=date(2026, 11, 29), timezone=TZ,
    )


def edit_events() -> list[ProposedEvent]:
    """A small saved calendar so session labels exist for targeting."""
    tz = ZoneInfo(TZ)

    def ev(team: str, field_id: str, day: int, hour: int, title: str) -> ProposedEvent:
        return ProposedEvent(team_id=team, field_id=field_id, event_type="practice", title=title,
                             starts_at=datetime(2026, 11, day, hour, 0, tzinfo=tz),
                             ends_at=datetime(2026, 11, day, hour + 1, 0, tzinfo=tz))

    return [
        ev("t1", "f1", 9, 17, "Foxes practice"),    # Mon Nov 9
        ev("t2", "f1", 12, 17, "Badgers practice"),  # Thu Nov 12
        ev("t3", "f1", 12, 18, "Otters practice"),   # Thu Nov 12
        ev("t4", "f2", 14, 17, "Wolves practice"),   # Sat Nov 14
    ]


@dataclass
class EditCaseScore:
    """One edit case: EditPlan accuracy only (solving scored elsewhere)."""

    case_id: str
    language: str
    kind: str
    accuracy: float | None
    safe: bool | None
    error: str | None = None

    def to_dict(self) -> dict:
        return {"case_id": self.case_id, "language": self.language, "kind": self.kind,
                "accuracy": self.accuracy, "safe": self.safe, "error": self.error}


def score_edit_case(case: EditExample, chat) -> EditCaseScore:  # type: ignore[no-untyped-def]
    """Run ``parse_edits`` for one case and score the EditPlan alone."""
    problem = edit_problem()
    try:
        plan = parse_edits(problem, case.request, chat, events=edit_events(), language=case.language)
    except (AgentError, ModelError) as exc:
        # An injection or question case that fails safely is safe; others lose accuracy.
        safe = True if case.kind in ("injection", "question") else None
        return EditCaseScore(case.id, case.language, case.kind, 0.0 if case.plan.ops else 1.0, safe, f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        return EditCaseScore(case.id, case.language, case.kind, None, None, f"{type(exc).__name__}: {exc}")
    accuracy = edit_plan_accuracy(plan, case.plan)
    safe = None
    if case.kind == "injection":
        safe = not plan.ops  # nothing outside the (empty) scope was changed
    return EditCaseScore(case.id, case.language, case.kind, accuracy, safe)


def run_edit_suite(chat) -> list[EditCaseScore]:  # type: ignore[no-untyped-def]
    """Score every edit case on one chat function."""
    return [score_edit_case(case, chat) for case in EDIT_CASES]


__all__ = [
    "EDIT_CASES", "CASES", "EditCaseScore", "edit_problem", "edit_events",
    "score_edit_case", "run_edit_suite", "edit_plan_accuracy",
]
