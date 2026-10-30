"""Core data classes for the SquadRoot Schedule Agent.

Everything here is deterministic and has no AI calls, no I/O, and no env
access. All datetimes in and out are timezone-aware; wall-clock rules
(field hours, earliest/latest) are interpreted in ``Problem.timezone``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _require_tz_aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _parse_date(value: str | date) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(value)


def _parse_time(value: str | time | None) -> time | None:
    if value is None:
        return None
    if isinstance(value, time):
        return value
    return time.fromisoformat(value)


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return _require_tz_aware(value, "datetime")
    dt = datetime.fromisoformat(value)
    return _require_tz_aware(dt, "datetime")


def _validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {name!r}") from exc
    return name


@dataclass
class Team:
    """A team that needs sessions. ``id`` is opaque and host-owned."""

    id: str
    name: str

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise ValueError("Team.id must be a non-empty string")
        if not self.name or not isinstance(self.name, str):
            raise ValueError("Team.name must be a non-empty string")

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict."""
        return {"id": self.id, "name": self.name}

    @classmethod
    def from_dict(cls, d: dict) -> Team:
        """Build a Team from :meth:`to_dict` output."""
        return cls(id=str(d["id"]), name=str(d["name"]))


@dataclass
class Field:
    """A playable field. Open hours are wall-clock times in the problem timezone."""

    id: str
    name: str
    open_from: time | None = None
    open_until: time | None = None

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise ValueError("Field.id must be a non-empty string")
        if not self.name or not isinstance(self.name, str):
            raise ValueError("Field.name must be a non-empty string")
        if (self.open_from is None) != (self.open_until is None):
            raise ValueError("Field open_from/open_until must both be set or both be None")
        if self.open_from is not None and self.open_until is not None:
            if not (isinstance(self.open_from, time) and isinstance(self.open_until, time)):
                raise TypeError("Field open hours must be datetime.time")
            if self.open_from >= self.open_until:
                raise ValueError("Field open_from must be before open_until")

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict."""
        return {
            "id": self.id,
            "name": self.name,
            "open_from": self.open_from.isoformat() if self.open_from else None,
            "open_until": self.open_until.isoformat() if self.open_until else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Field:
        """Build a Field from :meth:`to_dict` output."""
        return cls(
            id=str(d["id"]),
            name=str(d["name"]),
            open_from=_parse_time(d.get("open_from")),
            open_until=_parse_time(d.get("open_until")),
        )


@dataclass
class Busy:
    """A blocked interval. ``resource`` is ``"team:<id>"`` or ``"field:<id>"``."""

    resource: str
    start: datetime
    end: datetime
    reason: str = "already booked"

    def __post_init__(self) -> None:
        if not self.resource or not isinstance(self.resource, str):
            raise ValueError("Busy.resource must be a non-empty string")
        _require_tz_aware(self.start, "Busy.start")
        _require_tz_aware(self.end, "Busy.end")
        if self.start >= self.end:
            raise ValueError("Busy.start must be before Busy.end")

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict."""
        return {
            "resource": self.resource,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Busy:
        """Build a Busy from :meth:`to_dict` output."""
        return cls(
            resource=str(d["resource"]),
            start=_parse_datetime(d["start"]),
            end=_parse_datetime(d["end"]),
            reason=str(d.get("reason", "already booked")),
        )


@dataclass
class Requirement:
    """One need: recurring across the season, or one-off on dated days.

    A requirement with no active window repeats every season week
    (``sessions_per_week`` per week). Setting ``active_from`` /
    ``active_until`` scopes it like an event planner: only dates inside
    the inclusive window may be used. A one-off session such as "next
    Tuesday" is ``sessions_per_week=1`` with both bounds set to that
    date; "next week" bounds Monday to Sunday of that week.
    """

    team_id: str
    event_type: str
    sessions_per_week: int
    duration_minutes: int
    allowed_days: list[int] = field(default_factory=list)
    allowed_fields: list[str] = field(default_factory=list)
    earliest_start: time | None = None
    latest_end: time | None = None
    title: str | None = None
    active_from: date | None = None
    active_until: date | None = None

    def __post_init__(self) -> None:
        if not self.team_id or not isinstance(self.team_id, str):
            raise ValueError("Requirement.team_id must be a non-empty string")
        if not self.event_type or not isinstance(self.event_type, str):
            raise ValueError("Requirement.event_type must be a non-empty string")
        if not isinstance(self.sessions_per_week, int) or self.sessions_per_week < 0:
            raise ValueError("Requirement.sessions_per_week must be an int >= 0")
        if not isinstance(self.duration_minutes, int) or self.duration_minutes <= 0:
            raise ValueError("Requirement.duration_minutes must be an int > 0")
        for day in self.allowed_days:
            if not isinstance(day, int) or day < 0 or day > 6:
                raise ValueError("Requirement.allowed_days must be ints 0 (Mon) to 6 (Sun)")
        if self.earliest_start is not None and not isinstance(self.earliest_start, time):
            raise TypeError("Requirement.earliest_start must be datetime.time or None")
        if self.latest_end is not None and not isinstance(self.latest_end, time):
            raise TypeError("Requirement.latest_end must be datetime.time or None")
        if (
            self.earliest_start is not None
            and self.latest_end is not None
            and self.earliest_start >= self.latest_end
        ):
            raise ValueError("Requirement.earliest_start must be before latest_end")
        if self.title is not None and not isinstance(self.title, str):
            raise TypeError("Requirement.title must be a string or None")
        if self.active_from is not None and not isinstance(self.active_from, date):
            raise TypeError("Requirement.active_from must be a date or None")
        if self.active_until is not None and not isinstance(self.active_until, date):
            raise TypeError("Requirement.active_until must be a date or None")
        if self.active_from is not None and self.active_until is not None and self.active_from > self.active_until:
            raise ValueError("Requirement.active_from must not be after active_until")
        # Copy mutable inputs so callers cannot mutate us later.
        self.allowed_days = list(self.allowed_days)
        self.allowed_fields = [str(f) for f in self.allowed_fields]

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict."""
        return {
            "team_id": self.team_id,
            "event_type": self.event_type,
            "sessions_per_week": self.sessions_per_week,
            "duration_minutes": self.duration_minutes,
            "allowed_days": list(self.allowed_days),
            "allowed_fields": list(self.allowed_fields),
            "earliest_start": self.earliest_start.isoformat() if self.earliest_start else None,
            "latest_end": self.latest_end.isoformat() if self.latest_end else None,
            "title": self.title,
            "active_from": self.active_from.isoformat() if self.active_from else None,
            "active_until": self.active_until.isoformat() if self.active_until else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Requirement:
        """Build a Requirement from :meth:`to_dict` output."""
        return cls(
            team_id=str(d["team_id"]),
            event_type=str(d["event_type"]),
            sessions_per_week=int(d["sessions_per_week"]),
            duration_minutes=int(d["duration_minutes"]),
            allowed_days=[int(x) for x in d.get("allowed_days", [])],
            allowed_fields=[str(x) for x in d.get("allowed_fields", [])],
            earliest_start=_parse_time(d.get("earliest_start")),
            latest_end=_parse_time(d.get("latest_end")),
            title=str(d["title"]) if d.get("title") is not None else None,
            active_from=_parse_date(d["active_from"]) if d.get("active_from") else None,
            active_until=_parse_date(d["active_until"]) if d.get("active_until") else None,
        )


@dataclass
class Rules:
    """The structured scheduling ask: requirements plus the team gap rule.

    ``blackout_dates`` come from the request (e.g. a holiday the manager
    mentioned); the solver merges them with ``Problem.blackout_dates``.
    ``notes`` records things the model could not map, for the human to see.
    """

    requirements: list[Requirement] = field(default_factory=list)
    min_gap_days: int = 1
    blackout_dates: list[date] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.min_gap_days, int) or self.min_gap_days < 0:
            raise ValueError("Rules.min_gap_days must be an int >= 0")
        self.requirements = list(self.requirements)
        self.blackout_dates = [_parse_date(d) for d in self.blackout_dates]
        self.notes = [str(n) for n in self.notes]

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict."""
        return {
            "requirements": [r.to_dict() for r in self.requirements],
            "min_gap_days": self.min_gap_days,
            "blackout_dates": [d.isoformat() for d in self.blackout_dates],
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Rules:
        """Build Rules from :meth:`to_dict` output."""
        return cls(
            requirements=[Requirement.from_dict(x) for x in d.get("requirements", [])],
            min_gap_days=int(d.get("min_gap_days", 1)),
            blackout_dates=[_parse_date(x) for x in d.get("blackout_dates", [])],
            notes=[str(x) for x in d.get("notes", [])],
        )


@dataclass
class Problem:
    """Everything the solver needs: who, where, and when time is blocked."""

    teams: list[Team]
    fields: list[Field]
    season_start: date
    season_end: date
    timezone: str
    busy: list[Busy] = field(default_factory=list)
    blackout_dates: list[date] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.season_start = _parse_date(self.season_start)
        self.season_end = _parse_date(self.season_end)
        if self.season_start > self.season_end:
            raise ValueError("Problem.season_start must not be after season_end")
        _validate_timezone(self.timezone)
        self.teams = list(self.teams)
        self.fields = list(self.fields)
        self.busy = list(self.busy)
        self.blackout_dates = [_parse_date(d) for d in self.blackout_dates]

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict."""
        return {
            "teams": [t.to_dict() for t in self.teams],
            "fields": [f.to_dict() for f in self.fields],
            "season_start": self.season_start.isoformat(),
            "season_end": self.season_end.isoformat(),
            "timezone": self.timezone,
            "busy": [b.to_dict() for b in self.busy],
            "blackout_dates": [d.isoformat() for d in self.blackout_dates],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Problem:
        """Build a Problem from :meth:`to_dict` output."""
        return cls(
            teams=[Team.from_dict(x) for x in d.get("teams", [])],
            fields=[Field.from_dict(x) for x in d.get("fields", [])],
            season_start=_parse_date(d["season_start"]),
            season_end=_parse_date(d["season_end"]),
            timezone=str(d["timezone"]),
            busy=[Busy.from_dict(x) for x in d.get("busy", [])],
            blackout_dates=[_parse_date(x) for x in d.get("blackout_dates", [])],
        )


@dataclass
class ProposedEvent:
    """One placed session. Datetimes are timezone-aware."""

    team_id: str
    field_id: str
    event_type: str
    title: str
    starts_at: datetime
    ends_at: datetime

    def __post_init__(self) -> None:
        _require_tz_aware(self.starts_at, "ProposedEvent.starts_at")
        _require_tz_aware(self.ends_at, "ProposedEvent.ends_at")
        if self.starts_at >= self.ends_at:
            raise ValueError("ProposedEvent.starts_at must be before ends_at")

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict."""
        return {
            "team_id": self.team_id,
            "field_id": self.field_id,
            "event_type": self.event_type,
            "title": self.title,
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProposedEvent:
        """Build a ProposedEvent from :meth:`to_dict` output."""
        return cls(
            team_id=str(d["team_id"]),
            field_id=str(d["field_id"]),
            event_type=str(d["event_type"]),
            title=str(d["title"]),
            starts_at=_parse_datetime(d["starts_at"]),
            ends_at=_parse_datetime(d["ends_at"]),
        )


@dataclass
class CheckResult:
    """One named check on one event."""

    check: str
    event_index: int
    passed: bool
    reason: str = ""

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict."""
        return {
            "check": self.check,
            "event_index": self.event_index,
            "passed": bool(self.passed),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CheckResult:
        """Build a CheckResult from :meth:`to_dict` output."""
        return cls(
            check=str(d["check"]),
            event_index=int(d["event_index"]),
            passed=bool(d["passed"]),
            reason=str(d.get("reason", "")),
        )


@dataclass
class Shortfall:
    """Sessions asked for but not placed, with a plain-language reason."""

    team_id: str
    event_type: str
    week_start: date | None
    requested: int
    placed: int
    reason: str

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict."""
        return {
            "team_id": self.team_id,
            "event_type": self.event_type,
            "week_start": self.week_start.isoformat() if self.week_start else None,
            "requested": self.requested,
            "placed": self.placed,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Shortfall:
        """Build a Shortfall from :meth:`to_dict` output."""
        raw_week = d.get("week_start")
        return cls(
            team_id=str(d["team_id"]),
            event_type=str(d["event_type"]),
            week_start=_parse_date(raw_week) if raw_week else None,
            requested=int(d["requested"]),
            placed=int(d["placed"]),
            reason=str(d["reason"]),
        )


@dataclass
class Proposal:
    """A draft schedule. The agent proposes; a human approves. Nothing is applied."""

    rules: Rules
    events: list[ProposedEvent] = field(default_factory=list)
    check_results: list[CheckResult] = field(default_factory=list)
    shortfalls: list[Shortfall] = field(default_factory=list)
    summary: str = ""
    questions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.events = list(self.events)
        self.check_results = list(self.check_results)
        self.shortfalls = list(self.shortfalls)
        self.questions = [str(q) for q in self.questions]

    def shortfall_summary(self) -> str:
        """One line per team and event type.

        Each line gives total requested vs placed across the per-week
        shortfalls plus the most common reason. The per-week
        :attr:`shortfalls` list is kept as the detail. Returns ``""``
        when there are no shortfalls. Deterministic: groups sorted by
        (team, event type), ties in reason frequency broken by reason text.
        """
        groups: dict[tuple[str, str], list[Shortfall]] = {}
        for item in self.shortfalls:
            groups.setdefault((item.team_id, item.event_type), []).append(item)
        lines: list[str] = []
        for team_id, event_type in sorted(groups):
            items = groups[(team_id, event_type)]
            requested = sum(s.requested for s in items)
            placed = sum(s.placed for s in items)
            counts = Counter(s.reason for s in items)
            common = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            lines.append(f"{team_id} {event_type}: {placed} of {requested} placed; most common: {common}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict (ISO 8601 dates and times)."""
        return {
            "rules": self.rules.to_dict(),
            "events": [e.to_dict() for e in self.events],
            "check_results": [c.to_dict() for c in self.check_results],
            "shortfalls": [s.to_dict() for s in self.shortfalls],
            "summary": self.summary,
            "questions": list(self.questions),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Proposal:
        """Build a Proposal from :meth:`to_dict` output."""
        return cls(
            rules=Rules.from_dict(d["rules"]),
            events=[ProposedEvent.from_dict(x) for x in d.get("events", [])],
            check_results=[CheckResult.from_dict(x) for x in d.get("check_results", [])],
            shortfalls=[Shortfall.from_dict(x) for x in d.get("shortfalls", [])],
            summary=str(d.get("summary", "")),
            questions=[str(x) for x in d.get("questions", [])],
        )


# Alias kept for docs/PLAN.md wording ("a Plan with events and shortfalls").
Plan = Proposal


@dataclass
class ApplyResult:
    """Record of a human approval decision. The agent still applies nothing.

    Only :meth:`schedule_agent.agent.ScheduleAgent.approve` produces an
    approved instance (``approved=True``), and only for a draft whose
    check results all passed. The host saves ``events`` through its own
    permission and conflict checks; default-constructed instances are
    *not* approvals (``approved=False``).
    """

    events: list[ProposedEvent] = field(default_factory=list)
    approved: bool = False
    checks_passed: int = 0
    checks_total: int = 0
    message: str = ""

    def __post_init__(self) -> None:
        self.events = list(self.events)
        self.approved = bool(self.approved)
        self.checks_passed = int(self.checks_passed)
        self.checks_total = int(self.checks_total)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict (ISO 8601 datetimes)."""
        return {
            "events": [e.to_dict() for e in self.events],
            "approved": self.approved,
            "checks_passed": self.checks_passed,
            "checks_total": self.checks_total,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ApplyResult:
        """Build an ApplyResult from :meth:`to_dict` output."""
        return cls(
            events=[ProposedEvent.from_dict(x) for x in d.get("events", [])],
            approved=bool(d.get("approved", False)),
            checks_passed=int(d.get("checks_passed", 0)),
            checks_total=int(d.get("checks_total", 0)),
            message=str(d.get("message", "")),
        )
