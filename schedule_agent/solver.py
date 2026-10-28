"""Deterministic placement: the model never picks times, this solver does.

Week-by-week greedy search. Same input always gives the same output: every
loop runs in sorted order and there is no randomness. A field or a team is
never double booked, and every wall-clock rule (field hours, earliest/latest,
blackout dates, busy times, minimum gap) is enforced before a slot is taken.
Anything that does not fit becomes a Shortfall with a plain reason.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import (
    Problem,
    Proposal,
    ProposedEvent,
    Requirement,
    Rules,
    Shortfall,
)

SLOT_STEP_MINUTES = 30


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def _weeks(start: date, end: date) -> list[list[date]]:
    """Split [start, end] into 7-day chunks starting at ``start``."""
    weeks: list[list[date]] = []
    cursor = start
    while cursor <= end:
        chunk = [cursor + timedelta(days=i) for i in range(7) if cursor + timedelta(days=i) <= end]
        weeks.append(chunk)
        cursor += timedelta(days=7)
    return weeks


def _to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _day_grid(window_start: time, window_end: time, duration: timedelta) -> list[time]:
    """Half-hour grid starts (00:00, 00:30, ...) fitting inside the window."""
    out: list[time] = []
    length = int(duration.total_seconds() // 60)
    end_minutes = _to_minutes(window_end)
    # A window ending at 23:59 means end of day: allow slots ending at midnight.
    if window_end.hour == 23 and window_end.minute == 59:
        end_minutes = 24 * 60
    for minutes in range(0, 24 * 60, SLOT_STEP_MINUTES):
        slot = time(minutes // 60, minutes % 60)
        if minutes >= _to_minutes(window_start) and minutes + length <= end_minutes:
            out.append(slot)
    return out


def _window(req: Requirement, field_open_from: time | None, field_open_until: time | None) -> tuple[time, time]:
    start = req.earliest_start or time(0, 0)
    end = req.latest_end or time(23, 59, 59)
    if field_open_from is not None and field_open_from > start:
        start = field_open_from
    if field_open_until is not None and field_open_until < end:
        end = field_open_until
    # time(23,59,59) acts as end-of-day; clamp grid end to midnight.
    if end == time(23, 59, 59):
        end = time(23, 59)
    return start, end


def _fits(
    req: Requirement,
    min_gap_days: int,
    blackout: set[date],
    field_id: str,
    start_dt: datetime,
    end_dt: datetime,
    team_day: dict[tuple[str, date], list[ProposedEvent]],
    field_day: dict[tuple[str, date], list[ProposedEvent]],
    team_busy_day: dict[tuple[str, date], list[tuple[datetime, datetime]]],
    field_busy_day: dict[tuple[str, date], list[tuple[datetime, datetime]]],
) -> bool:
    """Fit test against day-bucketed indexes only (never a full-season scan).

    Placed/busy intervals are bucketed by local date, so a candidate only
    ever inspects events and busy times that can touch its own day (plus
    the previous day, for intervals crossing midnight) and, for the gap
    rule, the team's events within ``min_gap_days``. The accept/reject
    answer is identical to scanning every placed event and busy time.
    """
    day = start_dt.date()
    if day in blackout:
        return False
    if req.allowed_days and start_dt.weekday() not in req.allowed_days:
        return False
    if req.allowed_fields and field_id not in req.allowed_fields:
        return False
    # Wall-clock comparisons on naive times.
    start_t = start_dt.time()
    end_t = end_dt.time()
    if req.earliest_start is not None and start_t < req.earliest_start:
        return False
    if req.latest_end is not None and end_t > req.latest_end:
        return False
    prev_day = day - timedelta(days=1)
    for bucket_day in (prev_day, day):
        for ev in team_day.get((req.team_id, bucket_day), ()):
            if _overlaps(start_dt, end_dt, ev.starts_at, ev.ends_at):
                return False
        for ev in field_day.get((field_id, bucket_day), ()):
            if _overlaps(start_dt, end_dt, ev.starts_at, ev.ends_at):
                return False
    if min_gap_days > 0:
        for offset in range(-(min_gap_days - 1), min_gap_days):
            for ev in team_day.get((req.team_id, day + timedelta(days=offset)), ()):
                if abs((day - ev.starts_at.date()).days) < min_gap_days:
                    return False
    for bucket_day in (prev_day, day):
        for b_start, b_end in team_busy_day.get((req.team_id, bucket_day), ()):
            if _overlaps(start_dt, end_dt, b_start, b_end):
                return False
        for b_start, b_end in field_busy_day.get((field_id, bucket_day), ()):
            if _overlaps(start_dt, end_dt, b_start, b_end):
                return False
    return True


def _event_title(req: Requirement, team_name: dict[str, str]) -> str:
    """Per-requirement title when set, else the default team + event type."""
    if req.title:
        return req.title
    return f"{team_name.get(req.team_id, req.team_id)} {req.event_type}"


def solve(problem: Problem, rules: Rules) -> Proposal:
    """Place every requirement week by week. Never raises for tight space.

    Returns a Proposal with events sorted by start time and one Shortfall per
    requirement per week where fewer sessions were placed than asked for.
    Fairness: each week requirements go in order of most sessions short so
    far, then team id, so a short season rotates the pain instead of always
    starving the same teams. Blackout dates from the request
    (``Rules.blackout_dates``) are merged with ``Problem.blackout_dates``.
    """
    tz = ZoneInfo(problem.timezone)
    team_ids = {t.id for t in problem.teams}
    team_name = {t.id: t.name for t in problem.teams}
    field_ids = sorted(f.id for f in problem.fields)
    field_by_id = {f.id: f for f in problem.fields}
    blackout = set(problem.blackout_dates) | set(rules.blackout_dates)

    # Busy intervals bucketed by (resource id, local date): a candidate
    # slot only ever checks busy times touching its own day, however many
    # the host supplied (ground-complement busy runs to hundreds).
    team_busy_day: dict[tuple[str, date], list[tuple[datetime, datetime]]] = {}
    field_busy_day: dict[tuple[str, date], list[tuple[datetime, datetime]]] = {}
    known_team_ids = {t.id for t in problem.teams}
    known_field_ids = {f.id for f in problem.fields}
    for b in problem.busy:
        kind, _, rid = b.resource.partition(":")
        if kind == "team" and rid in known_team_ids:
            target = team_busy_day
        elif kind == "field" and rid in known_field_ids:
            target = field_busy_day
        else:
            continue  # Unknown resources are ignored: the host owns ids we never saw.
        first = max(b.start.astimezone(tz).date(), problem.season_start)
        last = min(b.end.astimezone(tz).date(), problem.season_end)
        day = first
        while day <= last:
            target.setdefault((rid, day), []).append((b.start, b.end))
            day += timedelta(days=1)

    placed: list[ProposedEvent] = []
    team_day: dict[tuple[str, date], list[ProposedEvent]] = {}
    field_day: dict[tuple[str, date], list[ProposedEvent]] = {}
    grid_cache: dict[tuple[time, time, int], list[time]] = {}

    def _place(ev: ProposedEvent) -> None:
        placed.append(ev)
        day = ev.starts_at.date()
        team_day.setdefault((ev.team_id, day), []).append(ev)
        field_day.setdefault((ev.field_id, day), []).append(ev)

    def _grid(window_start: time, window_end: time, duration: timedelta) -> list[time]:
        key = (window_start, window_end, int(duration.total_seconds() // 60))
        grid = grid_cache.get(key)
        if grid is None:
            grid = grid_cache[key] = _day_grid(window_start, window_end, duration)
        return grid
    shortfalls: list[Shortfall] = []
    preferred: dict[int, list[tuple[int, time, str]]] = {}
    short_so_far: dict[int, int] = {i: 0 for i in range(len(rules.requirements))}

    def _week_overlaps(req: Requirement, week_dates: list[date]) -> bool:
        """True when at least one date in the week falls inside the requirement window."""
        if req.active_from is None and req.active_until is None:
            return True
        return any(
            (req.active_from is None or d >= req.active_from)
            and (req.active_until is None or d <= req.active_until)
            for d in week_dates
        )

    def _in_window(req: Requirement, d: date) -> bool:
        return (req.active_from is None or d >= req.active_from) and (
            req.active_until is None or d <= req.active_until
        )

    for week_dates in _weeks(problem.season_start, problem.season_end):
        week_start = week_dates[0]
        by_weekday = {}
        for d in week_dates:
            by_weekday.setdefault(d.weekday(), d)
        # Fairness: hungriest requirement first, then team id (deterministic).
        week_order = sorted(
            range(len(rules.requirements)),
            key=lambda i: (-short_so_far[i], rules.requirements[i].team_id, rules.requirements[i].event_type, i),
        )
        for idx in week_order:
            req = rules.requirements[idx]
            if req.sessions_per_week == 0:
                continue
            if not _week_overlaps(req, week_dates):
                continue  # event-planner window: this week is out of scope, no shortfall
            if req.team_id not in team_ids:
                shortfalls.append(
                    Shortfall(
                        team_id=req.team_id,
                        event_type=req.event_type,
                        week_start=week_start,
                        requested=req.sessions_per_week,
                        placed=0,
                        reason=f"Unknown team '{req.team_id}': nothing placed for week starting {week_start.isoformat()}.",
                    )
                )
                short_so_far[idx] += req.sessions_per_week
                continue
            valid_fields = [f for f in (req.allowed_fields or field_ids) if f in field_by_id]
            valid_fields.sort()
            if not valid_fields:
                shortfalls.append(
                    Shortfall(
                        team_id=req.team_id,
                        event_type=req.event_type,
                        week_start=week_start,
                        requested=req.sessions_per_week,
                        placed=0,
                        reason=f"No known fields for {team_name.get(req.team_id, req.team_id)} in week starting {week_start.isoformat()}.",
                    )
                )
                short_so_far[idx] += req.sessions_per_week
                continue
            valid_days = sorted(set(req.allowed_days)) if req.allowed_days else list(range(7))
            duration = timedelta(minutes=req.duration_minutes)
            made = 0

            # 1) Reuse the rhythm found in the first placed week, where possible.
            for weekday, slot_t, fid in preferred.get(idx, []):
                if made >= req.sessions_per_week:
                    break
                d = by_weekday.get(weekday)
                if d is None or d in blackout or fid not in valid_fields or not _in_window(req, d):
                    continue
                start_dt = datetime.combine(d, slot_t, tzinfo=tz)
                end_dt = start_dt + duration
                if _fits(req, rules.min_gap_days, blackout, fid, start_dt, end_dt, team_day, field_day, team_busy_day, field_busy_day):
                    _place(
                        ProposedEvent(
                            team_id=req.team_id,
                            field_id=fid,
                            event_type=req.event_type,
                            title=_event_title(req, team_name),
                            starts_at=start_dt,
                            ends_at=end_dt,
                        )
                    )
                    made += 1

            # 2) Greedy search in deterministic order: date, field, time.
            # The candidate list is built once per requirement-week (same
            # order as before); each extra session rescans it, but every
            # probe is now a day-bucket lookup instead of a season scan.
            candidates: list[tuple[date, str, time]] = []
            for d in sorted(week_dates):
                if d.weekday() not in valid_days or d in blackout or not _in_window(req, d):
                    continue
                for fid in valid_fields:
                    f = field_by_id[fid]
                    w_start, w_end = _window(req, f.open_from, f.open_until)
                    if w_start >= w_end:
                        continue
                    for slot_t in _grid(w_start, w_end, duration):
                        candidates.append((d, fid, slot_t))
            while made < req.sessions_per_week:
                found = None
                for d, fid, slot_t in candidates:
                    start_dt = datetime.combine(d, slot_t, tzinfo=tz)
                    end_dt = start_dt + duration
                    if _fits(
                        req,
                        rules.min_gap_days,
                        blackout,
                        fid,
                        start_dt,
                        end_dt,
                        team_day,
                        field_day,
                        team_busy_day,
                        field_busy_day,
                    ):
                        found = (fid, start_dt, end_dt)
                        break
                if found is None:
                    break
                fid, start_dt, end_dt = found
                _place(
                    ProposedEvent(
                        team_id=req.team_id,
                        field_id=fid,
                        event_type=req.event_type,
                        title=_event_title(req, team_name),
                        starts_at=start_dt,
                        ends_at=end_dt,
                    )
                )
                made += 1

            if idx not in preferred and made > 0:
                week_events = [
                    e
                    for d in week_dates
                    for e in team_day.get((req.team_id, d), [])
                    if e.event_type == req.event_type
                ]
                preferred[idx] = sorted((e.starts_at.weekday(), e.starts_at.time(), e.field_id) for e in week_events)

            short_so_far[idx] += req.sessions_per_week - made
            if made < req.sessions_per_week:
                shortfalls.append(
                    Shortfall(
                        team_id=req.team_id,
                        event_type=req.event_type,
                        week_start=week_start,
                        requested=req.sessions_per_week,
                        placed=made,
                        reason=(
                            f"Only {made} of {req.sessions_per_week} {req.event_type} sessions placed "
                            f"for {team_name.get(req.team_id, req.team_id)} in week starting {week_start.isoformat()}: "
                            "no free field/team slot inside hours, busy times and gap rule."
                        ),
                    )
                )

    placed.sort(key=lambda e: (e.starts_at, e.team_id, e.field_id))
    return Proposal(rules=rules, events=placed, check_results=[], shortfalls=shortfalls, summary="")
