"""Verifiable checks: one result per check per event.

The solver enforces these rules before placing anything. This module
re-verifies any list of events -- solver output or hand-made -- so the
demo can show a green/red chip per check and a host can gate approval.
Deterministic: events are scanned in list order, checks in :data:`CHECK_NAMES`
order, so the same input always yields the same results.

Performance: the pairwise checks (field double booking, team overlap,
minimum gap) group events by field/team and sweep each group in start
order instead of comparing every event with every other event, and busy
times are bucketed by local date. Results, ordering and failure reasons
(first conflicting event index) are unchanged.
"""

from __future__ import annotations

from datetime import datetime, timedelta
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


def _overlap_partners(events: list[ProposedEvent], key: str) -> dict[int, int]:
    """Map event index -> lowest index of an overlapping same-key event.

    Groups share ``key`` (``field_id`` or ``team_id``); each group is
    swept in start order, so only events that can actually overlap are
    compared. The lowest partner index matches a full pairwise scan.
    """
    groups: dict[str, list[int]] = {}
    for i, ev in enumerate(events):
        groups.setdefault(getattr(ev, key), []).append(i)
    partners: dict[int, int] = {}
    for idxs in groups.values():
        ordered = sorted(idxs, key=lambda i: (events[i].starts_at, i))
        for pos, a in enumerate(ordered):
            ev_a = events[a]
            for b in ordered[pos + 1 :]:
                ev_b = events[b]
                if ev_b.starts_at >= ev_a.ends_at:
                    break
                if ev_a.starts_at < ev_b.ends_at:
                    if a not in partners or b < partners[a]:
                        partners[a] = b
                    if b not in partners or a < partners[b]:
                        partners[b] = a
    return partners


def _gap_partners(events: list[ProposedEvent], local_days: list, min_gap_days: int) -> dict[int, int]:
    """Map event index -> lowest index of a same-team event inside the gap."""
    if min_gap_days <= 0:
        return {}
    groups: dict[str, list[int]] = {}
    for i, ev in enumerate(events):
        groups.setdefault(ev.team_id, []).append(i)
    partners: dict[int, int] = {}
    for idxs in groups.values():
        ordered = sorted(idxs, key=lambda i: (local_days[i], i))
        for pos, a in enumerate(ordered):
            for b in ordered[pos + 1 :]:
                if (local_days[b] - local_days[a]).days >= min_gap_days:
                    break
                if a not in partners or b < partners[a]:
                    partners[a] = b
                if b not in partners or a < partners[b]:
                    partners[b] = a
    return partners


def run_checks(problem: Problem, rules: Rules, events: list[ProposedEvent]) -> list[CheckResult]:
    """Run every named check on every event.

    Returns one :class:`CheckResult` per check per event (len(events) x
    len(CHECK_NAMES)), ordered by event index then check name. Blackout
    dates merge ``Rules.blackout_dates`` with ``Problem.blackout_dates``,
    matching the solver. Busy entries for unknown resources are ignored:
    the host owns ids we never saw.
    """
    if not events:
        return []
    tz = ZoneInfo(problem.timezone)
    blackout = set(problem.blackout_dates) | set(rules.blackout_dates)
    field_by_id = {f.id: f for f in problem.fields}
    local_days = [e.starts_at.astimezone(tz).date() for e in events]

    # Busy intervals bucketed by (resource id, local date), clamped to
    # the checked events' date range: an event only inspects busy times
    # touching its own day (plus the previous day, for intervals that
    # cross midnight).
    team_busy_day: dict[tuple[str, object], list[tuple[datetime, datetime]]] = {}
    field_busy_day: dict[tuple[str, object], list[tuple[datetime, datetime]]] = {}
    known_teams = {t.id for t in problem.teams}
    known_fields = set(field_by_id)
    first_day = min(local_days) - timedelta(days=1)
    last_day = max(local_days) + timedelta(days=1)
    for b in problem.busy:
        kind, _, rid = b.resource.partition(":")
        if kind == "team" and rid in known_teams:
            target = team_busy_day
        elif kind == "field" and rid in known_fields:
            target = field_busy_day
        else:
            continue
        day = max(b.start.astimezone(tz).date(), first_day)
        last = min(b.end.astimezone(tz).date(), last_day)
        while day <= last:
            target.setdefault((rid, day), []).append((b.start, b.end))
            day += timedelta(days=1)

    field_partner = _overlap_partners(events, "field_id")
    team_partner = _overlap_partners(events, "team_id")
    gap_partner = _gap_partners(events, local_days, rules.min_gap_days)

    results: list[CheckResult] = []
    for i, ev in enumerate(events):
        results.append(_check_field_double_booking(field_partner, i, ev))
        results.append(_check_team_overlap(team_partner, i, ev))
        results.append(_check_field_hours(field_by_id, tz, i, ev))
        results.append(_check_blackout(blackout, local_days[i], i))
        results.append(_check_busy(team_busy_day, field_busy_day, local_days[i], i, ev))
        results.append(_check_minimum_gap(gap_partner, rules.min_gap_days, i, ev))
    return results


def _fail(check: str, event_index: int, reason: str) -> CheckResult:
    return CheckResult(check=check, event_index=event_index, passed=False, reason=reason)


def _pass(check: str, event_index: int) -> CheckResult:
    return CheckResult(check=check, event_index=event_index, passed=True)


def _check_field_double_booking(partners: dict[int, int], i: int, ev: ProposedEvent) -> CheckResult:
    check = "field_double_booking"
    j = partners.get(i)
    if j is not None:
        return _fail(check, i, f"Field '{ev.field_id}' is double booked: overlaps event #{j}.")
    return _pass(check, i)


def _check_team_overlap(partners: dict[int, int], i: int, ev: ProposedEvent) -> CheckResult:
    check = "team_overlap"
    j = partners.get(i)
    if j is not None:
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


def _check_blackout(blackout: set, day, i: int) -> CheckResult:  # type: ignore[no-untyped-def]
    check = "blackout_dates"
    if day in blackout:
        return _fail(check, i, f"Scheduled on blackout date {day.isoformat()}.")
    return _pass(check, i)


def _check_busy(
    team_busy_day: dict,
    field_busy_day: dict,
    local_day,
    i: int,
    ev: ProposedEvent,
) -> CheckResult:  # type: ignore[no-untyped-def]
    check = "busy"
    prev_day = local_day - timedelta(days=1)
    for bucket_day in (prev_day, local_day):
        for b_start, b_end in team_busy_day.get((ev.team_id, bucket_day), ()):
            if _overlaps(ev.starts_at, ev.ends_at, b_start, b_end):
                return _fail(check, i, f"Team '{ev.team_id}' is busy at this time.")
    for bucket_day in (prev_day, local_day):
        for b_start, b_end in field_busy_day.get((ev.field_id, bucket_day), ()):
            if _overlaps(ev.starts_at, ev.ends_at, b_start, b_end):
                return _fail(check, i, f"Field '{ev.field_id}' is busy at this time.")
    return _pass(check, i)


def _check_minimum_gap(
    partners: dict[int, int],
    min_gap_days: int,
    i: int,
    ev: ProposedEvent,
) -> CheckResult:
    check = "minimum_gap"
    j = partners.get(i)
    if j is not None:
        return _fail(
            check,
            i,
            f"Team '{ev.team_id}' sessions too close together: "
            f"event #{j} is within {min_gap_days} day(s).",
        )
    return _pass(check, i)
