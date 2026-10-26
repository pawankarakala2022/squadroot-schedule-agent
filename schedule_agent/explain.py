"""Plan -> summary: the model explains results in the manager's language.

Takes a solved :class:`Proposal` plus its check results and asks the
model for a short, plain-words summary. The model only explains; times
were already picked by the deterministic solver.
"""

from __future__ import annotations

from .errors import ModelError
from .llm import ChatFn
from .models import Problem, Proposal


def explain_proposal(problem: Problem, proposal: Proposal, chat: ChatFn, *, language: str = "en") -> str:
    """Return a short summary of ``proposal`` in ``language``.

    Raises :class:`ModelError` when the model is unreachable or answers
    with nothing usable.
    """
    team_name = {t.id: t.name for t in problem.teams}
    field_name = {f.id: f.name for f in problem.fields}
    lines = [
        f"{len(proposal.events)} sessions placed.",
        "Sessions: "
        + (
            ", ".join(
                f"{team_name.get(e.team_id, e.team_id)} {e.event_type} "
                f"{e.starts_at.isoformat()}->{e.ends_at.isoformat()} "
                f"at {field_name.get(e.field_id, e.field_id)}"
                for e in proposal.events
            )
            or "none"
        )
        + ".",
    ]
    failed = [c for c in proposal.check_results if not c.passed]
    lines.append(f"Checks: {len(proposal.check_results) - len(failed)}/{len(proposal.check_results)} passed.")
    if proposal.shortfalls:
        lines.append("Shortfalls:\n" + proposal.shortfall_summary())
    if proposal.rules.notes:
        lines.append("Notes: " + "; ".join(proposal.rules.notes))
    facts = "\n".join(lines)
    messages = [
        {
            "role": "system",
            "content": (
                "Summarize this youth-club schedule for the manager in "
                f"{language}. Short plain words, no tactics or analysis. "
                "The facts below are final; do not invent times or fields."
            ),
        },
        {"role": "user", "content": facts},
    ]
    try:
        text = chat(messages)
    except Exception as exc:
        if isinstance(exc, ModelError):
            raise
        raise ModelError("Assistant unavailable.") from exc
    if not isinstance(text, str) or not text.strip():
        raise ModelError("Assistant unavailable: empty model response.")
    return text.strip()
