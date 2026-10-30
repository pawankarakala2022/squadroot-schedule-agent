"""Plan -> summary: the model explains results in the manager's language.

Takes a solved :class:`Proposal` plus its check results and asks the
model for a short, plain-words summary. The model only explains; times
were already picked by the deterministic solver.

The facts sent to the model are aggregated into recurring session
patterns (team, type, weekday, time, field, count, date range) instead
of one line per session: a full season is hundreds of sessions, and
listing each one made this the largest, slowest model call in the loop
for no benefit -- the host already displays the calendar itself.
"""

from __future__ import annotations

from .errors import ModelError
from .llm import ChatFn
from .models import Problem, Proposal

MAX_PATTERN_LINES = 80
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _session_patterns(problem: Problem, proposal: Proposal) -> list[str]:
    """One deterministic line per recurring pattern, capped with a count of the rest."""
    team_name = {t.id: t.name for t in problem.teams}
    field_name = {f.id: f.name for f in problem.fields}
    groups: dict[tuple, dict] = {}
    for e in proposal.events:
        key = (
            e.team_id,
            e.event_type,
            e.title,
            e.starts_at.weekday(),
            e.starts_at.time(),
            e.ends_at.time(),
            e.field_id,
        )
        group = groups.get(key)
        if group is None:
            groups[key] = group = {"count": 0, "first": e.starts_at.date(), "last": e.starts_at.date()}
        group["count"] += 1
        day = e.starts_at.date()
        group["first"] = min(group["first"], day)
        group["last"] = max(group["last"], day)
    ordered = sorted(
        groups.items(),
        key=lambda kv: (
            team_name.get(kv[0][0], kv[0][0]),
            kv[0][1],
            kv[0][3],
            kv[0][4],
            field_name.get(kv[0][6], kv[0][6]),
            kv[0][2] or "",
        ),
    )
    lines: list[str] = []
    covered = 0
    for (team_id, event_type, title, weekday, start_t, end_t, field_id), group in ordered[:MAX_PATTERN_LINES]:
        label = f"{team_name.get(team_id, team_id)} {event_type}"
        if title:
            label += f" '{title}'"
        lines.append(
            f"{label} {_WEEKDAYS[weekday]} {start_t.strftime('%H:%M')}-{end_t.strftime('%H:%M')} "
            f"at {field_name.get(field_id, field_id)} x{group['count']} "
            f"({group['first'].isoformat()}..{group['last'].isoformat()})"
        )
        covered += group["count"]
    remaining = len(proposal.events) - covered
    if remaining > 0:
        lines.append(f"+ {remaining} more sessions in other patterns (see the calendar for each date).")
    return lines


def summarize_proposal(problem: Problem, proposal: Proposal, *, total_line: str | None = None) -> str:
    """Deterministic English summary from the checked facts (no model call).

    Same facts the model would be given -- session patterns, check
    counts, shortfalls, notes -- rendered directly. Hosts that enable
    it skip the slowest call in the loop; team and field names are
    copied exactly, and nothing can be invented.

    Shortfalls and failed checks come first so a conflict is seen
    before the detail; the session count (``total_line``, or
    ``"N sessions placed."`` for a fresh plan) goes last. Callers
    whose proposal mostly keeps a saved calendar pass a ``total_line``
    such as ``"Nothing changed. 18 existing session(s) unchanged."``
    so kept sessions are never reported as newly placed.
    """
    failed = [c for c in proposal.check_results if not c.passed]
    sections: list[str] = []
    if proposal.shortfalls:
        sections.append("Shortfalls:\n" + proposal.shortfall_summary())
    if failed:
        sections.append(
            "Failed checks:\n"
            + "\n".join(f"- {c.check} on event #{c.event_index}: {c.reason}" for c in failed[:5])
        )
    if proposal.events:
        sections.append("\n".join("- " + pattern + "." for pattern in _session_patterns(problem, proposal)))
    sections.append(
        f"Checks: {len(proposal.check_results) - len(failed)}/{len(proposal.check_results)} passed."
    )
    if proposal.rules.notes:
        sections.append("Notes: " + "; ".join(proposal.rules.notes))
    sections.append(total_line if total_line is not None else f"{len(proposal.events)} sessions placed.")
    return "\n\n".join(sections)


def explain_proposal(
    problem: Problem, proposal: Proposal, chat: ChatFn, *, language: str = "en", total_line: str | None = None
) -> str:
    """Return a short summary of ``proposal`` in ``language``.

    Raises :class:`ModelError` when the model is unreachable or answers
    with nothing usable. ``total_line`` replaces the default
    ``"N sessions placed."`` fact for proposals that keep a saved
    calendar, so kept sessions are not described as newly placed.
    """
    lines: list[str] = []
    if proposal.shortfalls:
        lines.append("Shortfalls:\n" + proposal.shortfall_summary())
    lines.append(total_line if total_line is not None else f"{len(proposal.events)} sessions placed.")
    if proposal.events:
        lines.append("Session patterns: " + "; ".join(_session_patterns(problem, proposal)) + ".")
    else:
        lines.append("Sessions: none.")
    failed = [c for c in proposal.check_results if not c.passed]
    lines.append(f"Checks: {len(proposal.check_results) - len(failed)}/{len(proposal.check_results)} passed.")
    if proposal.rules.notes:
        lines.append("Notes: " + "; ".join(proposal.rules.notes))
    facts = "\n".join(lines)
    messages = [
        {
            "role": "system",
            "content": (
                "Summarize this sports club schedule for the manager in "
                f"{language}. At most 3 short sentences. Short plain words, no tactics or analysis. "
                "Copy every team and field name exactly as given (English proper names); "
                "never translate or transliterate a name. "
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
