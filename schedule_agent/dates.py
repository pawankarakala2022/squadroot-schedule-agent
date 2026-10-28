"""Relative dates resolved in code: the model copies, never computes.

Managers write "next Tuesday", "next week" or "next month" in any
language. Date arithmetic in the model drifts (week boundaries, month
ends, timezones), so code computes the concrete dates in
``Problem.timezone`` and states them in the prompt: today, tomorrow,
this week, next week, each coming weekday, and next month. The model
then only has to copy a YYYY-MM-DD date into its strict JSON, whatever
language the request used. No AI calls, no I/O.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .models import Problem

_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def today_in_tz(problem: Problem) -> date:
    """Today's date in the problem timezone (season start as fallback)."""
    try:
        return datetime.now(ZoneInfo(problem.timezone)).date()
    except Exception:
        return problem.season_start


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def relative_date_hints(today: date) -> str:
    """One deterministic line of concrete dates for model prompts.

    Gives: today, tomorrow, the next occurrence of every weekday
    (strictly after today), this week and next week as Monday..Sunday
    ranges, and next month as a first..last range. All dates are
    YYYY-MM-DD so the model can copy them verbatim in any language.
    """
    this_monday = _monday(today)
    this_sunday = this_monday + timedelta(days=6)
    next_monday = this_monday + timedelta(days=7)
    next_sunday = next_monday + timedelta(days=6)
    next_month_first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    next_month_last = (next_month_first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    coming = []
    for weekday in range(7):
        delta = (weekday - today.weekday()) % 7 or 7
        coming.append(f"next {_DAY_NAMES[weekday]} {today + timedelta(days=delta)}")
    return (
        f"Today is {today.isoformat()} ({_DAY_NAMES[today.weekday()]}); "
        f"tomorrow is {(today + timedelta(days=1)).isoformat()}; "
        + "; ".join(coming)
        + f"; this week is {this_monday.isoformat()}..{this_sunday.isoformat()}; "
        f"next week is {next_monday.isoformat()}..{next_sunday.isoformat()}; "
        f"next month is {next_month_first.isoformat()}..{next_month_last.isoformat()}. "
        "Resolve 'next <day>', 'next week' and 'next month' to these dates exactly."
    )


def problem_date_hints(problem: Problem) -> str:
    """Concrete relative dates for ``problem`` (today in its timezone)."""
    return relative_date_hints(today_in_tz(problem))
