"""Verifiable checks: one result per check per event.

The solver enforces these rules before placing anything. This module
re-verifies any list of events -- solver output or hand-made -- so the
demo can show a green/red chip per check and a host can gate approval.
Deterministic: events are scanned in list order, checks in :data:`CHECK_NAMES`
order, so the same input always yields the same results.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .models import CheckResult, Problem, ProposedEvent, Rules

CHECK_NAMES: tuple[str, ...] = (
    "field_double_booking",
    "team_overlap",
    "field_hours",
    "blackout_dates",
    "busy",
    "minimum_gap",
)


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def run_checks(problem: Problem, rules: Rules, events: list[ProposedEvent]) -> list[CheckResult]:
    """Run every named check on every event.

    Returns one :class:`CheckResult` per check per event (len(events) x
    len(CHECK_NAMES)), ordered by event index then check name. Blackout
    dates merge ``Rules.blackout_dates`` with ``Problem.blackout_dates``,
    matching the solver. Busy entries for unknown resources are ignored:
    the host owns ids we never saw.
    """
    tz = ZoneInfo(problem.timezone)
    blackout = set(problem.blackout_dates) | set(rules.blackout_dates)
    field_by_id = {f.id: f for f in problem.fields}
    team_busy: dict[str, list[tuple[datetime, datetime]]] = {t.id: [] for t in problem.teams}
    field_busy: dict[str, list[tuple[datetime, datetime]]] = {f.id: [] for f in problem.fields}
    for b in problem.busy:
        kind, _, rid = b.resource.partition(":")
        if kind == "team" and rid in team_busy:
            team_busy[rid].append((b.start, b.end))
        elif kind == "field" and rid in field_busy:
            field_busy[rid].append((b.start, b.end))

    local_days = [e.starts_at.astimezone(tz).date() for e in events]
    results: list[CheckResult] = []
    for i, ev in enumerate(events):
        results.append(_check_field_double_booking(events, i, ev))
        results.append(_check_team_overlap(events, i, ev))
        results.append(_check_field_hours(field_by_id, tz, i, ev))
        results.append(_check_blackout(blackout, local_days[i], i))
        results.append(_check_busy(team_busy, field_busy, i, ev))
        results.append(_check_minimum_gap(events, local_days, rules.min_gap_days, i, ev))
    return results


def _fail(check: str, event_index: int, reason: str) -> CheckResult:
    return CheckResult(check=check, event_index=event_index, passed=False, reason=reason)


def _pass(check: str, event_index: int) -> CheckResult:
    return CheckResult(check=check, event_index=event_index, passed=True)


def _check_field_double_booking(events: list[ProposedEvent], i: int, ev: ProposedEvent) -> CheckResult:
    check = "field_double_booking"
    for j, other in enumerate(events):
        if j != i and other.field_id == ev.field_id:
            if _overlaps(ev.starts_at, ev.ends_at, other.starts_at, other.ends_at):
                return _fail(check, i, f"Field '{ev.field_id}' is double booked: overlaps event #{j}.")
    return _pass(check, i)


def _check_team_overlap(events: list[ProposedEvent], i: int, ev: ProposedEvent) -> CheckResult:
    check = "team_overlap"
    for j, other in enumerate(events):
        if j != i and other.team_id == ev.team_id:
            if _overlaps(ev.starts_at, ev.ends_at, other.starts_at, other.ends_at):
                return _fail(check, i, f"Team '{ev.team_id}' plays twice at once: overlaps event #{j}.")
    return _pass(check, i)


def _check_field_hours(field_by_id: dict, tz: ZoneInfo, i: int, ev: ProposedEvent) -> CheckResult:
    check = "field_hours"
    known = field_by_id.get(ev.field_id)
    if known is None:
        return _fail(check, i, f"Unknown field '{ev.field_id}'.")
    if known.open_from is None or known.open_until is None:
        return _pass(check, i)
    start_t = ev.starts_at.astimezone(tz).time()
    end_t = ev.ends_at.astimezone(tz).time()
    if start_t < known.open_from or end_t > known.open_until:
        return _fail(
            check,
            i,
            f"Outside {known.name} hours "
            f"({known.open_from.isoformat()}->{known.open_until.isoformat()}): "
            f"{start_t.isoformat()}->{end_t.isoformat()}.",
        )
    return _pass(check, i)


def _check_blackout(blackout: set, day, i: int) -> CheckResult:
    check = "blackout_dates"
    if day in blackout:
        return _fail(check, i, f"Scheduled on blackout date {day.isoformat()}.")
    return _pass(check, i)


def _check_busy(
    team_busy: dict[str, list[tuple[datetime, datetime]]],
    field_busy: dict[str, list[tuple[datetime, datetime]]],
    i: int,
    ev: ProposedEvent,
) -> CheckResult:
    check = "busy"
    for b_start, b_end in team_busy.get(ev.team_id, []):
        if _overlaps(ev.starts_at, ev.ends_at, b_start, b_end):
            return _fail(check, i, f"Team '{ev.team_id}' is busy at this time.")
    for b_start, b_end in field_busy.get(ev.field_id, []):
        if _overlaps(ev.starts_at, ev.ends_at, b_start, b_end):
            return _fail(check, i, f"Field '{ev.field_id}' is busy at this time.")
    return _pass(check, i)


def _check_minimum_gap(
    events: list[ProposedEvent],
    local_days: list,
    min_gap_days: int,
    i: int,
    ev: ProposedEvent,
) -> CheckResult:
    check = "minimum_gap"
    if min_gap_days > 0:
        for j, other in enumerate(events):
            if j != i and other.team_id == ev.team_id:
                if abs((local_days[i] - local_days[j]).days) < min_gap_days:
                    return _fail(
                        check,
                        i,
                        f"Team '{ev.team_id}' sessions too close together: "
                        f"event #{j} is within {min_gap_days} day(s).",
                    )
    return _pass(check, i)
