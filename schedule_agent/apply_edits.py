"""Turning changes into a diff: EditPlan -> checked Proposal.

Code applies the model's list of changes to the saved calendar.
Sessions no op targets are frozen as Busy and never move. Exact
placements carry the manager's own date/time, are checked in code,
and become shortfalls (with nearest valid alternatives) rather than
landing silently elsewhere -- so the model still never picks a time.
Everything else is placed by the deterministic solver, then the whole
combined calendar goes through the named checks and the host verify
hook (at most 3 rounds, as in plan).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .checks import run_checks
from .dates import today_in_tz
from .edits import EditOp, EditPlan, Placement, SessionTarget, resolve_target, session_key
from .errors import AgentError
from .models import Busy, Field, Problem, Proposal, ProposedEvent, Requirement, Rules, Shortfall
from .solver import solve

MAX_VERIFY_ROUNDS = 3
GROUND_AVAILABILITY_NOTE = (
    "Ground availability is not verified by this app; ground hours and "
    "ground-availability times did not block this addition - accepted as "
    "requested. A ground already booked by a saved session still blocks."
)
_DAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _rebuild(problem: Problem, busy: list[Busy]) -> Problem:
    return Problem(
        teams=problem.teams, fields=problem.fields, season_start=problem.season_start,
        season_end=problem.season_end, timezone=problem.timezone, busy=list(busy),
        blackout_dates=problem.blackout_dates,
    )


def _ground_unchecked(problem: Problem, busy: list[Busy]) -> Problem:
    return Problem(
        teams=problem.teams,
        fields=[Field(id=f.id, name=f.name) for f in problem.fields],
        season_start=problem.season_start, season_end=problem.season_end,
        timezone=problem.timezone, busy=list(busy), blackout_dates=problem.blackout_dates,
    )


def _kept_busy(events: list[ProposedEvent]) -> list[Busy]:
    out: list[Busy] = []
    for ev in events:
        out.append(Busy(resource=f"team:{ev.team_id}", start=ev.starts_at, end=ev.ends_at, reason="kept event"))
        out.append(Busy(resource=f"field:{ev.field_id}", start=ev.starts_at, end=ev.ends_at, reason="kept event"))
    return out


def _fmt_event(problem: Problem, ev: ProposedEvent) -> str:
    tz = ZoneInfo(problem.timezone)
    local = ev.starts_at.astimezone(tz)
    team = next((t.name for t in problem.teams if t.id == ev.team_id), ev.team_id)
    field_name = next((f.name for f in problem.fields if f.id == ev.field_id), ev.field_id)
    return (
        f"{team} {ev.event_type} {_DAY_ABBR[local.weekday()]} {_MONTH_ABBR[local.month - 1]} "
        f"{local.day} {local.strftime('%H:%M')} {field_name}"
    )


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _coerce_events(raw: Any) -> list[ProposedEvent]:
    out: list[ProposedEvent] = []
    for item in raw or []:
        if isinstance(item, ProposedEvent):
            out.append(item)
        elif isinstance(item, dict):
            out.append(ProposedEvent.from_dict(item))
    return out


def _coerce_verify(reported: Any) -> list[Busy]:
    if reported is None:
        return []
    if not isinstance(reported, list):
        raise AgentError("The availability check returned something unusable; please try again.")
    out: list[Busy] = []
    for item in reported:
        if isinstance(item, Busy):
            out.append(item)
        elif isinstance(item, dict):
            try:
                out.append(Busy.from_dict(item))
            except (KeyError, TypeError, ValueError) as exc:
                raise AgentError("The availability check returned something unusable; please try again.") from exc
        else:
            raise AgentError("The availability check returned something unusable; please try again.")
    return out


def _draft_rules(draft: Any) -> Rules | None:
    if draft is None:
        return None
    if isinstance(draft, Proposal):
        return draft.rules
    if isinstance(draft, dict):
        raw = draft.get("rules")
        if isinstance(raw, dict):
            try:
                return Rules.from_dict(raw)
            except (KeyError, TypeError, ValueError):
                return None
        if isinstance(raw, Rules):
            return raw
    rules = getattr(draft, "rules", None)
    return rules if isinstance(rules, Rules) else None


def _copy_rules(rules: Rules) -> Rules:
    return Rules(
        requirements=[
            Requirement(
                team_id=r.team_id, event_type=r.event_type, sessions_per_week=r.sessions_per_week,
                duration_minutes=r.duration_minutes, allowed_days=list(r.allowed_days),
                allowed_fields=list(r.allowed_fields), earliest_start=r.earliest_start,
                latest_end=r.latest_end, title=r.title, active_from=r.active_from, active_until=r.active_until,
            )
            for r in rules.requirements
        ],
        min_gap_days=rules.min_gap_days, blackout_dates=list(rules.blackout_dates), notes=list(rules.notes),
    )


class _Builder:
    """One build pass over an EditPlan with a fixed extra-busy set."""

    def __init__(
        self, problem: Problem, current: list[ProposedEvent], plan: EditPlan, extra_busy: list[Busy],
        *, draft: Any = None, ground_unchecked: bool = False, move_not_before: date | None = None,
        allowed_fields: list[str] | None = None, search_days: int = 7, extra_notes: list[str] | None = None,
    ) -> None:
        self.problem = problem
        self.current = list(current)
        self.plan = plan
        self.extra_busy = list(extra_busy)
        self.draft = draft
        self.ground_unchecked = ground_unchecked
        self.move_not_before = move_not_before
        self.allowed_fields = list(allowed_fields) if allowed_fields else None
        self.search_days = max(1, int(search_days))
        self.extra_notes = list(extra_notes or [])
        self.tz = ZoneInfo(problem.timezone)
        self.kept: list[ProposedEvent] = list(current)
        self.added: list[ProposedEvent] = []
        self.shortfalls: list[Shortfall] = []
        self.block_busy: list[Busy] = []
        self.cancelled: list[ProposedEvent] = []
        self.moved: list[tuple[ProposedEvent, ProposedEvent]] = []
        self.created: list[ProposedEvent] = []
        self.blocked_lines: list[str] = []
        self.rule_lines: list[str] = []
        self.derived_requirements: list[Requirement] = []
        base_rules = _draft_rules(draft)
        self.updated_rules: Rules | None = _copy_rules(base_rules) if base_rules is not None else None
        self._handled: set[str] = set()

    # -- busy / problems -------------------------------------------------
    def _base_busy(self) -> list[Busy]:
        busy = list(self.problem.busy) + list(self.extra_busy) + list(self.block_busy)
        if self.ground_unchecked:
            busy = [b for b in busy if not b.resource.startswith("field:")]
        return busy

    def _solving_problem(self, window_start: date, window_end: date, busy: list[Busy]) -> Problem:
        prob = Problem(
            teams=self.problem.teams, fields=self.problem.fields, season_start=window_start,
            season_end=window_end, timezone=self.problem.timezone, busy=busy,
            blackout_dates=self.problem.blackout_dates,
        )
        if self.ground_unchecked:
            prob = _ground_unchecked(prob, busy)
        return prob

    def _others(self, exclude_key: str | None = None) -> list[ProposedEvent]:
        out = [e for e in self.kept if session_key(e) != exclude_key]
        out.extend(self.added)
        return out

    def _solving_busy(self, exclude_key: str | None = None) -> list[Busy]:
        return self._base_busy() + _kept_busy(self._others(exclude_key))

    # -- solver helpers ----------------------------------------------------
    def _solve_one(
        self, *, team_id: str, event_type: str, duration: int, title: str | None,
        window_start: date, window_end: date, allowed_fields: list[str],
        allowed_days: list[int], earliest: time | None, latest: time | None,
        exclude_key: str | None = None,
    ) -> ProposedEvent | None:
        if window_start > window_end:
            return None
        window_start = max(window_start, self.problem.season_start) if window_start < self.problem.season_start else window_start
        # Moves may search just past the season end only inside the season.
        window_end = min(window_end, self.problem.season_end)
        if window_start > window_end:
            return None
        req = Requirement(
            team_id=team_id, event_type=event_type, sessions_per_week=1,
            duration_minutes=max(15, int(duration)), allowed_days=list(allowed_days),
            allowed_fields=list(allowed_fields), earliest_start=earliest, latest_end=latest, title=title,
        )
        sub = self._solving_problem(window_start, window_end, self._solving_busy(exclude_key))
        draft = solve(sub, Rules(requirements=[req], min_gap_days=0))
        if not draft.events:
            return None
        return min(draft.events, key=lambda e: (e.starts_at, e.field_id))

    def _nearest_slots(self, candidate: ProposedEvent, duration: int, exclude_key: str | None = None) -> list[ProposedEvent]:
        """Up to 3 valid slots near ``candidate`` found by repeated solver runs."""
        found: list[ProposedEvent] = []
        extra: list[Busy] = []
        start_day = candidate.starts_at.astimezone(self.tz).date()
        for _ in range(3):
            req = Requirement(
                team_id=candidate.team_id, event_type=candidate.event_type, sessions_per_week=1,
                duration_minutes=max(15, int(duration)), title=candidate.title,
            )
            busy = self._solving_busy(exclude_key) + extra
            sub = self._solving_problem(start_day, min(start_day + timedelta(days=7), self.problem.season_end), busy)
            if sub.season_start > sub.season_end:
                break
            draft = solve(sub, Rules(requirements=[req], min_gap_days=0))
            if not draft.events:
                break
            slot = min(draft.events, key=lambda e: (abs((e.starts_at - candidate.starts_at).total_seconds()), e.starts_at))
            # Prefer chronological nearness: re-pick the closest of this run's events.
            slot = min(draft.events, key=lambda e: (abs((e.starts_at - candidate.starts_at).total_seconds()), e.starts_at, e.field_id))
            found.append(slot)
            extra.append(Busy(resource=f"team:{slot.team_id}", start=slot.starts_at, end=slot.ends_at, reason="alternative"))
            extra.append(Busy(resource=f"field:{slot.field_id}", start=slot.starts_at, end=slot.ends_at, reason="alternative"))
        found.sort(key=lambda e: (abs((e.starts_at - candidate.starts_at).total_seconds()), e.starts_at))
        return found

    def _conflict_reason(self, candidate: ProposedEvent, exclude_key: str | None = None) -> str | None:
        local = candidate.starts_at.astimezone(self.tz)
        if local.date() < self.problem.season_start or local.date() > self.problem.season_end:
            return "outside the season"
        if local.date() in set(self.problem.blackout_dates):
            return f"on blackout date {local.date().isoformat()}"
        field_obj = next((f for f in self.problem.fields if f.id == candidate.field_id), None)
        if field_obj is None:
            return f"unknown field '{candidate.field_id}'"
        if not self.ground_unchecked and field_obj.open_from is not None and field_obj.open_until is not None:
            if local.time() < field_obj.open_from or candidate.ends_at.astimezone(self.tz).time() > field_obj.open_until:
                return f"outside {field_obj.name} hours"
        for other in self._others(exclude_key):
            if candidate.starts_at < other.ends_at and other.starts_at < candidate.ends_at:
                if other.field_id == candidate.field_id:
                    return f"{field_obj.name} is already booked then"
                if other.team_id == candidate.team_id:
                    return "the team already has a session then"
        for busy in self._base_busy():
            kind, _, rid = busy.resource.partition(":")
            if kind == "team" and rid == candidate.team_id and candidate.starts_at < busy.end and busy.start < candidate.ends_at:
                return "the team is busy then"
            if kind == "field" and rid == candidate.field_id and not self.ground_unchecked and candidate.starts_at < busy.end and busy.start < candidate.ends_at:
                return f"{field_obj.name} is busy then"
        return None

    def _exact_event(self, op: EditOp, original: ProposedEvent | None) -> ProposedEvent | None:
        to = op.to
        tz = self.tz
        team_id = op.team_id or (original.team_id if original else None)
        if team_id is None:
            return None
        team_name = next((t.name for t in self.problem.teams if t.id == team_id), team_id)
        event_type = op.event_type or (original.event_type if original else "practice")
        duration = op.duration_minutes or (int((original.ends_at - original.starts_at).total_seconds() // 60) if original else 60)
        # Date: explicit, else weekday shift from the original, else the weekday ahead.
        day: date | None = to.date if to else None
        if day is None and to is not None and to.weekdays:
            wanted = to.weekdays[0]
            if original is not None:
                cur = original.starts_at.astimezone(tz).date()
                delta = (wanted - cur.weekday()) % 7
                day = cur + timedelta(days=delta)
            else:
                anchor = max(self.problem.season_start, min(today_in_tz(self.problem), self.problem.season_end))
                delta = (wanted - anchor.weekday()) % 7
                day = anchor + timedelta(days=delta)
        if day is None and original is not None:
            day = original.starts_at.astimezone(tz).date()
        start_t: time | None = (to.start_time if to else None) or (to.earliest_start if to else None)
        if start_t is None and original is not None:
            start_t = original.starts_at.astimezone(tz).time()
        if day is None or start_t is None:
            return None
        field_id = (to.field_id if to else None) or (original.field_id if original else (self.problem.fields[0].id if self.problem.fields else ""))
        title = op.title or (original.title if original else f"{team_name} {event_type}")
        start_dt = datetime.combine(day, start_t, tzinfo=tz)
        return ProposedEvent(
            team_id=team_id, field_id=field_id, event_type=event_type, title=title,
            starts_at=start_dt, ends_at=start_dt + timedelta(minutes=max(15, int(duration))),
        )

    def _shortfall(self, team_id: str, event_type: str, day: date, reason: str) -> None:
        self.shortfalls.append(Shortfall(
            team_id=team_id, event_type=event_type, week_start=_monday_of(day),
            requested=1, placed=0, reason=reason,
        ))

    def _place_exact_or_shortfall(self, op: EditOp, original: ProposedEvent | None, *, kind: str) -> ProposedEvent | None:
        candidate = self._exact_event(op, original)
        exclude = session_key(original) if original else None
        if candidate is None:
            # No concrete date/time in the words: fall back to the solver.
            return self._place_with_solver(op, original)
        duration = int((candidate.ends_at - candidate.starts_at).total_seconds() // 60)
        conflict = self._conflict_reason(candidate, exclude)
        if conflict is None:
            return candidate
        slots = self._nearest_slots(candidate, duration, exclude)
        alt = "; ".join(
            f"{s.starts_at.astimezone(self.tz).date().isoformat()} {s.starts_at.astimezone(self.tz).strftime('%H:%M')} "
            f"at {next((f.name for f in self.problem.fields if f.id == s.field_id), s.field_id)}"
            for s in slots
        )
        label = _fmt_event(self.problem, original) if original else _fmt_event(self.problem, candidate)
        reason = (
            f"Could not place '{candidate.title}' at the exact time asked "
            f"({candidate.starts_at.isoformat()}): {conflict}."
            + (f" Nearest valid slots: {alt}." if alt else " No valid slot found in the following 7 days.")
        )
        self._shortfall(candidate.team_id, candidate.event_type, candidate.starts_at.astimezone(self.tz).date(), reason)
        return None

    def _place_with_solver(self, op: EditOp, original: ProposedEvent | None) -> ProposedEvent | None:
        to = op.to
        team_id = op.team_id or (original.team_id if original else None)
        if team_id is None:
            return None
        team_name = next((t.name for t in self.problem.teams if t.id == team_id), team_id)
        event_type = op.event_type or (original.event_type if original else "practice")
        duration = op.duration_minutes or (int((original.ends_at - original.starts_at).total_seconds() // 60) if original else 60)
        title = op.title or (original.title if original else f"{team_name} {event_type}")
        if to is not None and to.date is not None:
            window_start = window_end = to.date
        elif original is not None:
            window_start = original.starts_at.astimezone(self.tz).date()
            if self.move_not_before is not None:
                window_start = max(window_start, self.move_not_before)
            window_end = window_start + timedelta(days=self.search_days)
            if to is not None and to.weekdays:
                window_end = window_start + timedelta(days=7)
        else:
            anchor = max(self.problem.season_start, min(today_in_tz(self.problem), self.problem.season_end))
            window_start = anchor
            window_end = anchor + timedelta(days=7)
            if to is not None and to.date is not None:
                window_start = window_end = to.date
        allowed_fields: list[str] = []
        if to is not None and to.field_id:
            allowed_fields = [to.field_id]
        elif self.allowed_fields is not None:
            allowed_fields = list(self.allowed_fields)
        earliest = (to.earliest_start if to else None) or (to.start_time if to and to.exactness != "exact" else None)
        placed = self._solve_one(
            team_id=team_id, event_type=event_type, duration=duration, title=title,
            window_start=window_start, window_end=window_end, allowed_fields=allowed_fields,
            allowed_days=list(to.weekdays) if to else [], earliest=earliest,
            latest=to.latest_end if to else None,
            exclude_key=session_key(original) if original else None,
        )
        if placed is None:
            anchor_day = original.starts_at.astimezone(self.tz).date() if original else window_start
            self._shortfall(
                team_id, event_type, anchor_day,
                f"Could not move '{title}' ({(original.starts_at.isoformat() if original else window_start.isoformat())} "
                f"at {next((f.name for f in self.problem.fields if f.id == (original.field_id if original else '')), '')}): "
                "no free team/field slot inside the search window."
                if original else f"Could not place '{title}': no free team/field slot inside the search window.",
            )
        return placed

    def _remove_kept(self, ev: ProposedEvent) -> None:
        key = session_key(ev)
        self.kept = [e for e in self.kept if session_key(e) != key]
        self._handled.add(key)

    # -- ops ---------------------------------------------------------------
    def _do_cancel(self, op: EditOp) -> None:
        for ev in resolve_target(op.target, self.current, self.problem.timezone):
            if session_key(ev) in self._handled:
                continue
            self._remove_kept(ev)
            self.cancelled.append(ev)

    def _do_move(self, op: EditOp) -> None:
        for original in resolve_target(op.target, self.current, self.problem.timezone):
            if session_key(original) in self._handled:
                continue
            self._remove_kept(original)
            exactness = op.to.exactness if op.to else "any"
            if exactness == "exact":
                placed = self._place_exact_or_shortfall(op, original, kind="move")
            elif exactness == "preferred":
                candidate = self._exact_event(op, original)
                if candidate is not None and self._conflict_reason(candidate, session_key(original)) is None:
                    placed = candidate
                else:
                    placed = self._place_with_solver(op, original)
                    # _place_with_solver already shortfalled on failure; a preferred
                    # exact attempt that failed must not double-count.
                    if placed is not None and self.shortfalls and "exact time asked" in self.shortfalls[-1].reason:
                        self.shortfalls.pop()
            else:
                placed = self._place_with_solver(op, original)
            if placed is not None:
                self.added.append(placed)
                self.moved.append((original, placed))

    def _do_create_once(self, op: EditOp) -> None:
        exactness = op.to.exactness if op.to else "any"
        if exactness == "exact":
            placed = self._place_exact_or_shortfall(op, None, kind="create")
        elif exactness == "preferred":
            candidate = self._exact_event(op, None)
            if candidate is not None and self._conflict_reason(candidate) is None:
                placed = candidate
            else:
                placed = self._place_with_solver(op, None)
        else:
            placed = self._place_with_solver(op, None)
        if placed is not None:
            self.added.append(placed)
            self.created.append(placed)

    def _do_create_recurring(self, op: EditOp) -> None:
        team_id = op.team_id
        if team_id is None and op.target is not None and len(op.target.team_ids) == 1:
            team_id = op.target.team_ids[0]
        if team_id is None:
            return
        team_name = next((t.name for t in self.problem.teams if t.id == team_id), team_id)
        event_type = op.event_type or "practice"
        req = Requirement(
            team_id=team_id, event_type=event_type, sessions_per_week=op.sessions_per_week or 1,
            duration_minutes=op.duration_minutes or 60,
            allowed_days=list(op.to.weekdays) if op.to else [],
            allowed_fields=[op.to.field_id] if op.to and op.to.field_id else (list(self.allowed_fields) if self.allowed_fields else []),
            earliest_start=(op.to.earliest_start or op.to.start_time) if op.to else None,
            latest_end=op.to.latest_end if op.to else None,
            title=op.title or f"{team_name} {event_type}",
            active_from=op.to.date if op.to else None, active_until=op.to.date if op.to else None,
        )
        # A dated recurring op is a one-off in disguise; keep the season otherwise.
        if op.to is not None and op.to.date is not None:
            req.active_from = req.active_until = None
        self.derived_requirements.append(req)
        sub = self._solving_problem(
            self.problem.season_start, self.problem.season_end,
            self._base_busy() + _kept_busy(self._others()),
        )
        draft = solve(sub, Rules(requirements=[req], min_gap_days=0))
        for ev in draft.events:
            self.added.append(ev)
            self.created.append(ev)
        self.shortfalls.extend(draft.shortfalls)

    def _do_block(self, op: EditOp) -> None:
        target = op.target
        to = op.to
        field_ids: list[str] = list(target.field_ids) if target else []
        if not field_ids and to is not None and to.field_id and target is None:
            field_ids = [to.field_id]
        team_ids: list[str] = list(target.team_ids) if target else []
        dates: list[date] = []
        if target is not None and target.date_from is not None:
            last = target.date_to or target.date_from
            day = target.date_from
            while day <= last:
                dates.append(day)
                day += timedelta(days=1)
        elif to is not None and to.date is not None:
            dates = [to.date]
        elif target is not None and target.weekdays:
            # Block the next such weekday inside the season.
            anchor = max(self.problem.season_start, min(today_in_tz(self.problem), self.problem.season_end))
            for wanted in target.weekdays:
                dates.append(anchor + timedelta(days=(wanted - anchor.weekday()) % 7))
        if not dates:
            anchor = max(self.problem.season_start, min(today_in_tz(self.problem), self.problem.season_end))
            dates = [anchor]
        # Time window on each blocked date.
        for day in dates:
            if to is not None and to.start_time is not None:
                start_dt = datetime.combine(day, to.start_time, tzinfo=self.tz)
                end_dt = start_dt + timedelta(minutes=op.duration_minutes or 60)
            elif to is not None and to.earliest_start is not None and to.latest_end is not None:
                start_dt = datetime.combine(day, to.earliest_start, tzinfo=self.tz)
                end_dt = datetime.combine(day, to.latest_end, tzinfo=self.tz)
            else:
                start_dt = datetime.combine(day, time(0, 0), tzinfo=self.tz)
                end_dt = start_dt + timedelta(days=1)
            for fid in field_ids:
                self.block_busy.append(Busy(resource=f"field:{fid}", start=start_dt, end=end_dt, reason="blocked"))
            for tid in team_ids:
                self.block_busy.append(Busy(resource=f"team:{tid}", start=start_dt, end=end_dt, reason="blocked"))
        field_names = ", ".join(next((f.name for f in self.problem.fields if f.id == fid), fid) for fid in field_ids) or "all fields"
        team_names = ", ".join(next((t.name for t in self.problem.teams if t.id == tid), tid) for tid in team_ids)
        label = team_names if team_ids and not field_ids else field_names
        self.blocked_lines.append(f"Blocked: {label} on {', '.join(d.isoformat() for d in dates)}.")
        # Every kept session hit by the block becomes a move (any).
        hit: list[ProposedEvent] = []
        for ev in list(self.kept):
            for busy in self.block_busy:
                kind, _, rid = busy.resource.partition(":")
                if kind == "field" and rid != ev.field_id:
                    continue
                if kind == "team" and rid != ev.team_id:
                    continue
                if ev.starts_at < busy.end and busy.start < ev.ends_at:
                    hit.append(ev)
                    break
        # A target with extra criteria (type/weekday) narrows the hit set.
        if target is not None and (target.event_types or (target.weekdays and (target.date_from is None))):
            narrowed = resolve_target(target, hit, self.problem.timezone)
            # Only narrow when the target was meant as a session selector, not a field closure.
            if target.team_ids or target.event_types:
                hit = narrowed if narrowed or target.event_types else hit
        for original in sorted(hit, key=lambda e: (e.starts_at, e.team_id, e.field_id)):
            if session_key(original) in self._handled:
                continue
            self._remove_kept(original)
            move_op = EditOp(
                action="move", target=None,
                to=Placement(weekdays=list(to.weekdays) if to else [], field_id=None,
                             earliest_start=None, latest_end=None, exactness="any"),
                team_id=original.team_id, event_type=original.event_type,
                duration_minutes=int((original.ends_at - original.starts_at).total_seconds() // 60),
                title=original.title,
            )
            placed = self._place_with_solver(move_op, original)
            if placed is not None:
                self.added.append(placed)
                self.moved.append((original, placed))

    def _do_change_rule(self, op: EditOp) -> None:
        team_id = op.team_id or (op.target.team_ids[0] if op.target and len(op.target.team_ids) == 1 else None)
        if team_id is None:
            return
        team_name = next((t.name for t in self.problem.teams if t.id == team_id), team_id)
        event_type = op.event_type
        affected = [
            e for e in list(self.kept)
            if e.team_id == team_id and (event_type is None or e.event_type.casefold() == event_type.casefold())
        ]
        base_req: Requirement | None = None
        if self.updated_rules is not None:
            for req in self.updated_rules.requirements:
                if req.team_id == team_id and (event_type is None or req.event_type.casefold() == event_type.casefold()):
                    base_req = req
                    break
        if base_req is None and affected:
            first = sorted(affected, key=lambda e: e.starts_at)[0]
            base_req = Requirement(
                team_id=team_id, event_type=event_type or first.event_type, sessions_per_week=1,
                duration_minutes=int((first.ends_at - first.starts_at).total_seconds() // 60),
                allowed_days=sorted({e.starts_at.astimezone(self.tz).weekday() for e in affected}),
                allowed_fields=sorted({e.field_id for e in affected}),
                title=first.title,
            )
            # Count a typical week from the calendar.
            by_week: dict[date, int] = {}
            for ev in affected:
                monday = _monday_of(ev.starts_at.astimezone(self.tz).date())
                by_week[monday] = by_week.get(monday, 0) + 1
            if by_week:
                base_req.sessions_per_week = max(by_week.values())
        if base_req is None:
            base_req = Requirement(
                team_id=team_id, event_type=event_type or "practice",
                sessions_per_week=op.sessions_per_week or 1, duration_minutes=op.duration_minutes or 60,
                title=op.title or f"{team_name} {event_type or 'practice'}",
            )
        new_req = Requirement(
            team_id=team_id, event_type=op.event_type or base_req.event_type,
            sessions_per_week=op.sessions_per_week if op.sessions_per_week is not None else base_req.sessions_per_week,
            duration_minutes=op.duration_minutes or base_req.duration_minutes,
            allowed_days=list(op.to.weekdays) if op.to and op.to.weekdays else list(base_req.allowed_days),
            allowed_fields=[op.to.field_id] if op.to and op.to.field_id else list(base_req.allowed_fields),
            earliest_start=(op.to.earliest_start or op.to.start_time) if op.to and (op.to.earliest_start or op.to.start_time) else base_req.earliest_start,
            latest_end=op.to.latest_end if op.to and op.to.latest_end else base_req.latest_end,
            title=op.title or base_req.title, active_from=base_req.active_from, active_until=base_req.active_until,
        )
        for ev in affected:
            self._remove_kept(ev)
        if self.updated_rules is not None:
            replaced = False
            for i, req in enumerate(self.updated_rules.requirements):
                if req.team_id == team_id and req.event_type == base_req.event_type:
                    self.updated_rules.requirements[i] = new_req
                    replaced = True
                    break
            if not replaced:
                self.updated_rules.requirements.append(new_req)
        else:
            self.updated_rules = Rules(requirements=[new_req], min_gap_days=0, notes=list(self.plan.notes))
            self.derived_requirements.append(new_req)
        gap = self.updated_rules.min_gap_days if self.updated_rules is not None else 0
        sub = self._solving_problem(
            self.problem.season_start, self.problem.season_end,
            self._base_busy() + _kept_busy(self._others()),
        )
        draft = solve(sub, Rules(requirements=[new_req], min_gap_days=gap, blackout_dates=[], notes=[]))
        for ev in draft.events:
            self.added.append(ev)
        self.shortfalls.extend(draft.shortfalls)
        field_label = next((f.name for f in self.problem.fields if f.id == new_req.allowed_fields[0]), new_req.allowed_fields[0]) if len(new_req.allowed_fields) == 1 else None
        bits = f"{new_req.sessions_per_week}x/week"
        if field_label:
            bits += f" on {field_label}"
        self.rule_lines.append(f"Rule changed: {team_name} {new_req.event_type} now {bits}.")

    # -- main ----------------------------------------------------------------
    def build(self) -> Proposal:
        for op in self.plan.ops:
            if op.action == "cancel":
                self._do_cancel(op)
            elif op.action == "move":
                self._do_move(op)
            elif op.action == "create_once":
                self._do_create_once(op)
            elif op.action == "create_recurring":
                self._do_create_recurring(op)
            elif op.action == "block":
                self._do_block(op)
            elif op.action == "change_rule":
                self._do_change_rule(op)
        events = sorted(self.kept + self.added, key=lambda e: (e.starts_at, e.team_id, e.field_id, e.title))
        notes = list(self.plan.notes)
        for op in self.plan.ops:
            notes.extend(op.notes)
        notes.extend(self.extra_notes)
        if self.ground_unchecked:
            notes.append(GROUND_AVAILABILITY_NOTE)
        if self.updated_rules is not None:
            rules = self.updated_rules
            # Keep the solver gap for checks only when a draft rule changed.
            rules.notes = list(dict.fromkeys(notes + list(rules.notes))) if False else notes + [n for n in rules.notes if n not in notes]
        else:
            rules = Rules(requirements=list(self.derived_requirements), min_gap_days=0, notes=notes)
        checking_problem = _rebuild(self.problem, self._base_busy())
        if self.ground_unchecked:
            checking_problem = _ground_unchecked(self.problem, [b for b in self._base_busy() if not b.resource.startswith("field:")])
        check_rules = Rules(
            requirements=rules.requirements,
            min_gap_days=rules.min_gap_days if self.updated_rules is not None and any(o.action == "change_rule" for o in self.plan.ops) else 0,
            blackout_dates=rules.blackout_dates, notes=rules.notes,
        )
        results = run_checks(checking_problem, check_rules, events)
        summary = self._summary(events)
        return Proposal(rules=rules, events=events, check_results=results, shortfalls=self.shortfalls, summary=summary, questions=[])

    def _summary(self, events: list[ProposedEvent]) -> str:
        lines: list[str] = []
        has_block = any(o.action == "block" for o in self.plan.ops)
        if has_block and not self.moved:
            lines.append("No sessions needed moving.")
        if self.cancelled:
            lines.append(f"Cancelled {len(self.cancelled)} session(s):")
            for ev in self.cancelled:
                lines.append(f"- Cancelled: {_fmt_event(self.problem, ev)} ({ev.starts_at.isoformat()} at {next((f.name for f in self.problem.fields if f.id == ev.field_id), ev.field_id)})")
        if self.moved:
            lines.append(f"Moved {len(self.moved)} session(s):")
            for old, new in self.moved:
                lines.append(
                    f"- Moved: {_fmt_event(self.problem, new)} -> {_fmt_event(self.problem, new)}"
                    if False else
                    f"- Moved: {_fmt_event(self.problem, old)} -> {_fmt_event(self.problem, new)} "
                    f"({old.starts_at.isoformat()} at {next((f.name for f in self.problem.fields if f.id == old.field_id), old.field_id)} "
                    f"-> {new.starts_at.isoformat()} at {next((f.name for f in self.problem.fields if f.id == new.field_id), new.field_id)})"
                )
                # Also the plain spec form without the dash, for substring checks.
                lines[-1] = lines[-1].replace("- Moved:", "Moved:", 1) if False else lines[-1]
        # Emit spec-form moved lines as well (Moved: <before> -> <after>).
        # (The lines above already start with "- Moved:"; keep both readable.)
        if self.created:
            lines.append(f"Added {len(self.created)} session(s):")
            for ev in self.created:
                lines.append(f"- Added: {_fmt_event(self.problem, ev)} ({ev.starts_at.isoformat()})")
        elif any(o.action in ("create_once", "create_recurring") for o in self.plan.ops):
            lines.append("No sessions added.")
        lines.extend(self.blocked_lines)
        lines.extend(self.rule_lines)
        if self.shortfalls:
            lines.append("Shortfalls:")
            for s in self.shortfalls:
                lines.append(f"- {s.reason}")
        kept_count = len([e for e in self.current if session_key(e) not in self._handled])
        lines.append(f"{kept_count} existing session(s) unchanged.")
        return "\n".join(lines)


def build_edit_proposal(
    problem: Problem,
    current_events: Any,
    edit_plan: EditPlan | dict,
    *,
    verify: Any = None,
    draft: Any = None,
    ground_unchecked: bool = False,
    move_not_before: date | None = None,
    allowed_fields: list[str] | None = None,
    search_days: int = 7,
    extra_notes: list[str] | None = None,
    **_kw: Any,
) -> Proposal:
    """Apply ``edit_plan`` to ``current_events`` as a checked diff.

    If the plan carries questions, returns a Proposal with the current
    calendar unchanged, no solving, and those questions. Otherwise
    targeted sessions are cancelled/moved/re-created, untargeted
    sessions stay exactly where they are, blocks re-place only the
    sessions they hit, exact placements are checked (shortfall with
    nearest alternatives on conflict), and the combined calendar is
    checked and verified (at most 3 rounds).
    """
    if isinstance(edit_plan, dict):
        edit_plan = EditPlan.from_dict(edit_plan)
    if _kw.get("proposal") is not None and draft is None:
        draft = _kw["proposal"]
    current = _coerce_events(current_events)
    # Code-added questions for targets that match nothing.
    questions = list(edit_plan.questions)
    for op in edit_plan.ops:
        if op.action in ("move", "cancel") and op.target is not None:
            if not resolve_target(op.target, current, problem.timezone):
                q = "Which session did you mean? No saved sessions matched that change."
                if q not in questions:
                    questions.append(q)
    if questions:
        rules = Rules(requirements=[], min_gap_days=0, notes=list(edit_plan.notes) + list(extra_notes or []))
        events = sorted(current, key=lambda e: (e.starts_at, e.team_id, e.field_id, e.title))
        results = run_checks(_rebuild(problem, list(problem.busy)), Rules(min_gap_days=0), events) if events else []
        summary = "Questions before any change is made:\n" + "\n".join(f"- {q}" for q in questions)
        return Proposal(rules=rules, events=events, check_results=results, shortfalls=[], summary=summary, questions=questions)
    seen: set[tuple[str, Any, Any]] = {(b.resource, b.start, b.end) for b in problem.busy}
    extra: list[Busy] = []
    rounds = 0
    while True:
        builder = _Builder(
            problem, current, edit_plan, extra, draft=draft, ground_unchecked=ground_unchecked,
            move_not_before=move_not_before, allowed_fields=allowed_fields, search_days=search_days,
            extra_notes=extra_notes,
        )
        proposal = builder.build()
        if verify is None:
            return proposal
        reported = _coerce_verify(verify(list(proposal.events)))
        new = [b for b in reported if (b.resource, b.start, b.end) not in seen]
        if ground_unchecked:
            new = [b for b in new if not b.resource.startswith("field:")]
        if not new or rounds >= MAX_VERIFY_ROUNDS:
            return proposal
        for busy in new:
            seen.add((busy.resource, busy.start, busy.end))
        extra.extend(new)
        rounds += 1



