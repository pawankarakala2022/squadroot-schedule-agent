"""US federal holidays, computed in code: hosts merge them as blackouts.

A club should never hold a practice on a public holiday by accident.
The host (not the model) decides that: :func:`with_us_holidays` returns
a copy of the problem with every US federal holiday inside the season
added to ``blackout_dates``, so the solver skips those days and the
``blackout_dates`` check proves it. Dates are the actual calendar
dates of the 11 federal holidays (no observed-shift), computed
deterministically for any year. No I/O, no network, no AI calls.
Clubs add local or school holidays via ``Problem.blackout_dates`` or
by naming them in the request, exactly as before.
"""

from __future__ import annotations

from datetime import date, timedelta

from .models import Problem


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th (1-based) weekday (0=Monday) of a month."""
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last weekday (0=Monday) of a month."""
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def us_federal_holidays(year: int) -> dict[date, str]:
    """Map of holiday date -> name for the 11 US federal holidays."""
    if not isinstance(year, int) or isinstance(year, bool) or year < 1:
        raise ValueError("year must be a positive int")
    return {
        date(year, 1, 1): "New Year's Day",
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, 0, 3): "Presidents Day",
        _last_weekday(year, 5, 0): "Memorial Day",
        date(year, 6, 19): "Juneteenth",
        date(year, 7, 4): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        _nth_weekday(year, 10, 0, 2): "Columbus Day",
        date(year, 11, 11): "Veterans Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving",
        date(year, 12, 25): "Christmas Day",
    }


def holiday_dates(start: date, end: date) -> list[date]:
    """Sorted US federal holiday dates inside [start, end] inclusive."""
    if end < start:
        return []
    out: list[date] = []
    for year in range(start.year, end.year + 1):
        out.extend(d for d in us_federal_holidays(year) if start <= d <= end)
    return sorted(out)


def with_us_holidays(problem: Problem) -> Problem:
    """Copy of ``problem`` with US federal holidays added as blackouts.

    The caller's problem is untouched. Existing blackout dates are
    kept; the merged list is sorted and deduplicated. The host calls
    this before planning when the club follows US general holidays.
    """
    merged = sorted(set(problem.blackout_dates) | set(holiday_dates(problem.season_start, problem.season_end)))
    return Problem(
        teams=problem.teams,
        fields=problem.fields,
        season_start=problem.season_start,
        season_end=problem.season_end,
        timezone=problem.timezone,
        busy=problem.busy,
        blackout_dates=merged,
    )
