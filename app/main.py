"""Demo web app: a host-style integration of the public library interface.

Everything here uses only names exported from ``schedule_agent``: the
demo proves what a host app gets. Stateless JSON API (proposals travel
as dicts, like a host keeping them in the browser) plus one static page.
Modes: ``mock`` (no key; scripted answers clearly labelled), ``meta``
(Meta Model API via ``ModelConfig.from_env``), ``open`` (Muse Spark Open
via ``OPEN_MODEL_*`` env vars).
"""

from __future__ import annotations

import json
import os
import queue
import threading
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from time import monotonic
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from schedule_agent import (
    AvailabilityImport,
    Busy,
    ChatFn,
    Field,
    ModelConfig,
    Problem,
    Proposal,
    ProposedEvent,
    ScheduleAgent,
    ScheduleImport,
    Team,
    VoiceConfig,
    availability_to_busy,
    chat_fn,
    draft_notices,
    extract_availability,
    extract_games,
    games_to_busy,
    transcribe_audio,
)

ROOT = Path(__file__).resolve().parent.parent
CLUB_PATH = ROOT / "demo" / "club.json"
SAMPLE_TRANSCRIPT_PATH = ROOT / "demo" / "voice_note.txt"

with open(CLUB_PATH, encoding="utf-8") as handle:
    CLUB = json.load(handle)
try:
    SAMPLE_TRANSCRIPT = SAMPLE_TRANSCRIPT_PATH.read_text(encoding="utf-8").strip()
except OSError:
    SAMPLE_TRANSCRIPT = "Foxes practice twice a week after 5pm."

TEAM_NAMES = {t["id"]: t["name"] for t in CLUB["teams"]}
LANGUAGES_BY_TEAM = {t["id"]: list(t.get("languages", ["en"])) for t in CLUB["teams"]}
# Two coaches, each training two back-to-back pairs; a lone "coach" key
# in older club files still works.
COACHES: list[dict] = list(CLUB.get("coaches") or ([CLUB["coach"]] if CLUB.get("coach") else []))
TZ = CLUB["timezone"]

_DOTENV_ALLOWLIST = frozenset(
    {
        "META_MODEL_API_BASE",
        "MODEL_API_KEY",
        "META_MODEL",
        "META_TRANSCRIBE_MODEL",
        "OPEN_MODEL_API_BASE",
        "OPEN_MODEL_API_KEY",
        "OPEN_MODEL",
    }
)


def _dotenv_candidates() -> list[Path]:
    """Where a .env may live, best first: $ENV_FILE, cwd, repo root, parents.

    Plain runs use the repo root; Docker passes keys through the process
    environment (compose ``env_file``), so the file may sit in the
    working directory or a parent of the repo instead.
    """
    found: list[Path] = []
    explicit = os.environ.get("ENV_FILE") or os.environ.get("DOTENV_PATH")
    if explicit:
        found.append(Path(explicit).expanduser())
    cwd = Path.cwd()
    found += [ROOT / ".env", cwd / ".env"]
    for parent in (cwd, *cwd.parents):
        candidate = parent / ".env"
        if candidate not in found:
            found.append(candidate)
    for parent in ROOT.parents:
        candidate = parent / ".env"
        if candidate not in found:
            found.append(candidate)
    return found


def _load_dotenv() -> None:
    """Fill allowlisted vars from the first readable .env found.

    Never overrides variables already in the real environment (Docker
    compose / ``--env-file`` wins). The library reads only os.environ
    (no file I/O by design), so the demo app does the loading here.
    Runs on every mode lookup so editing .env needs no re-import.
    """
    for path in _dotenv_candidates():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            var, _, value = line.partition("=")
            var = var.strip()
            if var in _DOTENV_ALLOWLIST:
                # Strip quotes and trailing comments ("KEY=abc # comment").
                cleaned = value.strip().strip("\"'")
                cleaned = cleaned.split("#", 1)[0].strip().strip("\"'")
                if cleaned:
                    os.environ.setdefault(var, cleaned)
        return  # first readable .env wins; do not merge several files

def _mock_req(alias: str, day: int, field_alias: str) -> dict:
    """One mock practice: weekly, 60 minutes, evenings, on one field."""
    return {
        "team_alias": alias, "event_type": "practice", "sessions_per_week": 1,
        "duration_minutes": 60, "allowed_days": [day],
        "allowed_field_aliases": [field_alias], "earliest_start": "17:00",
        "latest_end": "20:00", "title": "",
    }


# Four teams, two coaches, scheduling in November only (December stays
# open but unscheduled; see scheduling_end in demo/club.json). Paired teams share a day and
# field, so the solver's earliest-first search places each pair
# back-to-back (17:00 then 18:00, same field, same coach): Coach 1 on
# Mondays, Coach 2 on Thursdays, both on North Field.
MOCK_RULES = {
    "requirements": [
        _mock_req("T1", 0, "F1"), _mock_req("T2", 0, "F1"),
        _mock_req("T3", 3, "F1"), _mock_req("T4", 3, "F1"),
    ],
    "min_gap_days": 1,
    "blackout_dates": [],
    "notes": ["Mock mode: the same scripted rules answer every request."],
}

def _mock_rules_from_request(messages: list) -> dict | None:
    """Request-aware mock rules: teams/days/times named in the words win.

    Mock mode is a scripted offline stand-in, not the real assistant —
    but it should still answer the request it was given (the demo
    presets name teams and days). Returns None when no demo team is
    named, so the caller falls back to the full-club script.
    """
    import re

    text = _mock_request_text(messages)
    low = text.lower()
    team_alias = {t["name"].lower(): f"T{i + 1}" for i, t in enumerate(CLUB["teams"])}
    day_names = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6, "mon": 0, "tue": 1,
        "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4,
        "sat": 5, "sun": 6, "mondays": 0, "tuesdays": 1, "wednesdays": 2,
        "thursdays": 3, "fridays": 4, "saturdays": 5, "sundays": 6,
    }
    days_by_alias: dict[str, list[int]] = {}
    pending: list[str] = []
    for match in re.finditer(r"[a-z]+", low):
        word = match.group(0)
        if word in team_alias and team_alias[word] not in pending:
            pending.append(team_alias[word])
        elif word in day_names:
            for alias in pending:
                days_by_alias.setdefault(alias, [])
                if day_names[word] not in days_by_alias[alias]:
                    days_by_alias[alias].append(day_names[word])
            pending = []
    for alias in pending:  # teams named with no day: any day
        days_by_alias.setdefault(alias, [])
    if not days_by_alias:
        return None
    sessions = 2 if ("twice" in low or "two times" in low) else 1
    dur_match = re.search(r"(\d{2,3})\s*min", low)
    duration = min(180, max(15, int(dur_match.group(1)))) if dur_match else 60
    earliest = "17:00"
    time_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", low) or re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", low)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        meridiem = time_match.group(3) if time_match.lastindex and time_match.lastindex >= 3 else None
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        earliest = f"{hour:02d}:{minute:02d}"
    latest_hour = min(23, int(earliest[:2]) + 3)
    requirements = [
        {
            "team_alias": alias, "event_type": "game" if " game" in low else "practice",
            "sessions_per_week": sessions, "duration_minutes": duration,
            "allowed_days": days, "allowed_field_aliases": [],
            "earliest_start": earliest, "latest_end": f"{latest_hour:02d}:00", "title": "",
        }
        for alias, days in sorted(days_by_alias.items())
    ]
    return {
        "requirements": requirements, "min_gap_days": 1, "blackout_dates": [],
        "notes": ["Mock mode (scripted, offline): rules read from the teams and days named in your request. Use Meta API mode for real understanding."],
    }


MOCK_DISRUPTION = {
    "date_from": "2026-11-12",
    "date_to": "2026-11-12",
    "closed_field_aliases": ["F1"],
    "all_fields": False,
    "search_days": 7,
    "not_before": "",
    "notes": ["Mock mode: always rains Thursday Nov 12 on North Field."],
}

MOCK_CANCEL = {
    "team_aliases": [],
    "field_aliases": [],
    "weekdays": [3],
    "date_from": "",
    "date_to": "",
    "event_types": [],
    "notes": ["Mock mode: always cancels Thursdays."],
}

MOCK_GAMES = {
    "games": [
        {"date": "2026-11-14", "start": "17:00", "home": "Foxes", "away": "Badgers", "field": "North Field"},
        {"date": "2026-11-14", "start": "18:00", "home": "Badgers", "away": "Foxes", "field": "South Field"},
    ],
    "notes": ["Mock mode: sample rows for the demo photo, not read from your upload."],
}

# Scripted grounds/coach photo for mock mode (model-shaped, see vision schema).
MOCK_AVAILABILITY_MODEL = {
    "grounds": [
        {"field": "North Field", "weekdays": [1, 3], "open_from": "17:00", "open_until": "20:00"},
        {"field": "South Field", "weekdays": [0, 2, 4], "open_from": "17:00", "open_until": "20:00"},
    ],
    "coach": [
        {"team": "Foxes", "weekdays": [4], "date_from": "", "date_to": "",
         "start_time": "17:00", "end_time": "18:00"},
        {"team": "Otters", "weekdays": [4], "date_from": "", "date_to": "",
         "start_time": "17:00", "end_time": "18:00"},
    ],
    "notes": ["Mock mode: sample ground hours and coach windows, not read from your upload."],
}


def _mock_request_text(messages: list) -> str:
    """Last user message text (the request), lowercased for keyword scans."""
    text = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    return str(text)


def _mock_iso_dates(text: str) -> list[str]:
    """Deterministic date reader for mock mode: ISO, M/D and Month-name D.

    Offline stand-in for the model reading dates out of a request; always
    returns YYYY-MM-DD strings in request order. Yearless dates assume 2026.
    """
    import re

    found: list[str] = []
    for match in re.finditer(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text):
        found.append(f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}")
    for match in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})\b", text):
        month, day = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            found.append(f"2026-{month:02d}-{day:02d}")
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
        "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    for match in re.finditer(r"\b([A-Za-z]+)\s+(\d{1,2})\b", text):
        month = months.get(match.group(1).lower())
        day = int(match.group(2))
        if month and 1 <= day <= 31:
            found.append(f"2026-{month:02d}-{day:02d}")
    seen: set[str] = set()
    ordered = [d for d in found if not (d in seen or seen.add(d))]  # type: ignore[func-returns-value]
    return ordered


def _mock_weekdays(text: str) -> list[int]:
    """Weekday numbers (0=Monday) named in the request, in first-seen order."""
    import re

    names = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
        "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3,
        "fri": 4, "sat": 5, "sun": 6,
    }
    days: list[int] = []
    for match in re.finditer(r"[A-Za-z]+", text.lower()):
        day = names.get(match.group(0))
        if day is not None and day not in days:
            days.append(day)
    return days


def mock_route_call(messages: list) -> str:
    """Keyword router for mock mode: same wording rules in every run, no network."""
    system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
    text = _mock_request_text(messages)
    low = text.lower()
    cancel_words = ("cancel", "drop", "remove", "delete", "call off", "called off", "scrap")
    weather_words = ("rain", "flood", "storm", "closed", "closure")
    move_words = ("postpone", "reschedul", "shift", "push back")
    has_date = bool(_mock_iso_dates(text) or _mock_weekdays(text)
                     or any(w in low for w in ("today", "tomorrow", "weekend", "next week")))
    if any(w in low for w in cancel_words):
        return "cancel"
    if "has_calendar=true" in system.lower() and any(w in low for w in ("add", "another", "extra")):
        return "add"
    # Product rule: weather with no named move target cancels; a move
    # target (move/shift/postpone ... to <date/field>) reschedules.
    import re as _re

    has_move_target = bool(_re.search(r"\b(move|shift|postpone|reschedule)\b[^.]*\bto\b", low))
    if any(w in low for w in weather_words):
        return "reschedule" if has_move_target else "cancel"
    if any(w in low for w in move_words) or ("move" in low and has_date):
        return "reschedule"
    if "move" in low or "field" in low or "ground" in low or any(
        w in low for w in ("revise", "change", "instead", "twice", "add session", "keep", "confirm")
    ):
        if "has_proposal=true" in system.lower():
            return "revise"
        return "plan"
    if "has_calendar=true" in system.lower() and has_date:
        return "reschedule"
    return "plan"


def mock_disruption(messages: list) -> dict:
    """Request-aware mock disruption: explicit dates/fields win, else the Nov 12 demo."""
    text = _mock_request_text(messages)
    dates = _mock_iso_dates(text)
    if not dates:
        return dict(MOCK_DISRUPTION)
    low = text.lower()
    aliases: list[str] = []
    if "north" in low:
        aliases.append("F1")
    if "south" in low:
        aliases.append("F2")
    all_fields = not aliases
    not_before = ""
    if len(dates) >= 2 and any(w in low for w in (" to ", "->", "or later", "not before", "after")):
        not_before = dates[-1]
    return {
        "date_from": dates[0],
        "date_to": dates[0],
        "closed_field_aliases": aliases,
        "all_fields": all_fields,
        "search_days": 7,
        "not_before": not_before,
        "notes": ["Mock mode: dates/fields read from your request, no model call."],
    }


def mock_cancel(messages: list) -> dict:
    """Request-aware mock cancel filter: names/days/dates in the request win."""
    text = _mock_request_text(messages)
    low = text.lower()
    teams = [t for t in ("T1", "T2") if t.lower() in low]
    for alias, name in (("T1", "foxes"), ("T2", "badgers")):
        if name in low and alias not in teams:
            teams.append(alias)
    weekdays = _mock_weekdays(text)
    dates = _mock_iso_dates(text)
    types: list[str] = []
    if "practice" in low:
        types.append("practice")
    elif "game" in low:
        types.append("game")
    if teams or weekdays or dates or types:
        return {
            "team_aliases": sorted(teams),
            "field_aliases": [],
            "weekdays": weekdays,
            "date_from": dates[0] if dates else "",
            "date_to": dates[0] if dates else "",
            "event_types": types,
            "notes": ["Mock mode: filter read from your request, no model call."],
        }
    return dict(MOCK_CANCEL)


def _mock_time_str(text: str) -> str:
    import re

    matches = list(re.finditer(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text.lower()))
    if matches:
        m = matches[-1]
        hour = int(m.group(1)) % 12
        if m.group(3) == "pm":
            hour += 12
        return f"{hour:02d}:{int(m.group(2) or 0):02d}"
    m2 = list(re.finditer(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text))
    return f"{int(m2[-1].group(1)):02d}:{m2[-1].group(2)}" if m2 else ""


def _edit_empty_target() -> dict:
    return {"session_labels": [], "team_aliases": [], "field_aliases": [], "weekdays": [], "date_from": "", "date_to": "", "event_types": []}


def _edit_empty_to() -> dict:
    return {"date": "", "start_time": "", "field_alias": "", "weekdays": [], "earliest_start": "", "latest_end": "", "exactness": "any"}


def mock_edit_plan(messages: list) -> dict:
    """Scripted EditPlan for mock mode: demo preset requests, no network.

    Replaces the old keyword router + per-verb parsers on the handle
    path. The wording rules read teams/days/dates/times named in the
    request; anything else becomes a question, never a guess.
    """
    import re

    text = _mock_request_text(messages)
    system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
    low = text.lower()
    dates = _mock_iso_dates(text)
    weekdays = _mock_weekdays(text)
    time_str = _mock_time_str(text)
    team_aliases = []
    for i, team in enumerate(CLUB["teams"]):
        if team["name"].lower() in low and f"T{i + 1}" not in team_aliases:
            team_aliases.append(f"T{i + 1}")
    # Generic problems (tests) may use Foxes/Badgers even if club data differs.
    for alias, name in (("T1", "foxes"), ("T2", "badgers"), ("T3", "otters"), ("T4", "wolves")):
        if name in low and alias not in team_aliases:
            team_aliases.append(alias)
    field_aliases = []
    if "north" in low:
        field_aliases.append("F1")
    if "south" in low:
        field_aliases.append("F2")
    selected = re.findall(r"\bS\d+\b", system.split("selected these sessions:")[-1].split(".")[0]) if "selected these sessions:" in system else []
    ops: list[dict] = []

    def op(action, target=None, to=None, team_alias="", event_type="", duration=0, sessions=0, title=""):
        return {
            "action": action, "target": target or _edit_empty_target(), "to": to or _edit_empty_to(),
            "team_alias": team_alias, "event_type": event_type, "duration_minutes": duration,
            "sessions_per_week": sessions, "title": title, "notes": ["Mock mode: scripted edit, no model call."],
        }

    if "ignore" in low and ("instruction" in low or "previous" in low):
        return {"ops": [], "questions": [], "notes": ["Mock mode: instructions inside the request are data, not followed."]}
    has_cancel = any(w in low for w in ("cancel", "drop", "remove", "call off"))
    has_add = bool(re.search(r"\b(add|another|extra)\b", low)) and any(w in low for w in ("game", "practice", "session"))
    has_move_word = "move" in low or "shift" in low or "postpone" in low
    has_weather = any(w in low for w in ("rain", "flood", "storm", "closed", "closure"))
    # Several changes in one request: cancel plus add both become ops.
    if has_cancel:
        target = _edit_empty_target()
        target["team_aliases"] = list(team_aliases)
        target["weekdays"] = weekdays[:1] if weekdays and not dates else []
        if dates:
            target["date_from"] = target["date_to"] = dates[0]
        if "practice" in low:
            target["event_types"] = ["practice"]
        elif "game" in low:
            target["event_types"] = ["game"]
        if selected and ("this" in low or "that" in low):
            target["session_labels"] = selected
            target["team_aliases"] = []
            target["weekdays"] = []
        ops.append(op("cancel", target=target))
    if has_weather and not has_move_word:
        target = _edit_empty_target()
        target["field_aliases"] = list(field_aliases)
        if dates:
            target["date_from"] = target["date_to"] = dates[0]
        elif weekdays:
            target["weekdays"] = weekdays[:1]
        ops.append(op("block", target=target))
    elif has_move_word:
        target = _edit_empty_target()
        to = _edit_empty_to()
        if selected and ("this" in low or "that" in low):
            target["session_labels"] = selected
        else:
            target["team_aliases"] = list(team_aliases)
            if len(dates) >= 1:
                target["date_from"] = target["date_to"] = dates[0]
            elif weekdays:
                target["weekdays"] = weekdays[:1]
        if len(dates) >= 2:
            to["date"] = dates[1]
        elif len(weekdays) >= 2:
            to["weekdays"] = [weekdays[1]]
        elif dates and has_weather:
            pass
        if time_str:
            to["start_time"] = time_str
            to["exactness"] = "exact" if (to["date"] or to["weekdays"]) else "preferred"
        if field_aliases and len(field_aliases) > len(target["field_aliases"]):
            to["field_alias"] = field_aliases[-1]
        ops.append(op("move", target=target, to=to))
    if has_add and not has_move_word:
        to = _edit_empty_to()
        if dates:
            to["date"] = dates[-1]
        if weekdays:
            to["weekdays"] = weekdays[-1:]
        if time_str:
            to["start_time"] = time_str
            to["exactness"] = "exact" if (to["date"] or to["weekdays"]) else "any"
        ops.append(op(
            "create_once", to=to, team_alias=team_aliases[0] if team_aliases else "T1",
            event_type="game" if "game" in low else "practice", duration=60,
            title="vs Falcons" if "falcon" in low else "",
        ))
    if not ops and ("twice" in low or "field" in low or "ground" in low) and team_aliases:
        to = _edit_empty_to()
        if field_aliases:
            to["field_alias"] = field_aliases[-1]
        ops.append(op("change_rule", team_alias=team_aliases[0], sessions=2 if "twice" in low else 0, to=to))
    if not ops:
        return {"ops": [], "questions": ["Which session or team did you mean? Please name the team, day or date."],
                "notes": ["Mock mode: scripted edit could not read that request."]}
    return {"ops": ops, "questions": [], "notes": ["Mock mode: scripted EditPlan (offline demo answers, can be wrong)."]}


MOCK_ADD_GAME = {
    "requirements": [
        {
            "team_alias": "T1",
            "event_type": "game",
            "sessions_per_week": 1,
            "duration_minutes": 60,
            "allowed_days": [6],
            "allowed_field_aliases": [],
            "earliest_start": "17:00",
            "latest_end": "20:00",
            "title": "vs Falcons",
            "active_from": "2026-11-01",
            "active_until": "2026-11-01",
        },
    ],
    "min_gap_days": 1,
    "blackout_dates": [],
    "blackout_ranges": [],
    "notes": ["Mock mode: one scripted game added, existing sessions kept."],
}


def mock_chat(messages: list, *, response_format=None, usage_out=None) -> str:
    """Scripted stand-in: valid shapes, clearly labelled, no network."""
    if response_format is None:
        user_text = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        return "Mock summary: " + str(user_text)[:400]
    name = response_format["json_schema"]["name"]
    if name == "edit_plan":
        return json.dumps(mock_edit_plan(messages))
    if name == "route_intent":
        return json.dumps({"call": mock_route_call(messages), "notes": ["Mock mode: keyword router."]})
    if name == "scheduling_rules":
        blob = " ".join(str(m.get("content", "")) for m in messages).lower()
        if "produce only the session(s) this request adds" in blob:
            return json.dumps(MOCK_ADD_GAME)
        return json.dumps(_mock_rules_from_request(messages) or MOCK_RULES)
    if name == "disruption":
        return json.dumps(mock_disruption(messages))
    if name == "cancel_filter":
        return json.dumps(mock_cancel(messages))
    if name == "family_notices":
        system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        marker = "Languages: "
        langs = ["en"]
        if marker in system:
            chunk = system.split(marker, 1)[1].split(". ", 1)[0]  # up to the sentence end
            langs = [part.strip(" .") for part in chunk.split(",")]
            langs = [lang for lang in langs if lang]
        facts = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        return json.dumps(
            {
                "notices": [
                    {"language": lang, "text": f"Mock notice ({lang}): {facts}"} for lang in langs
                ],
                "notes": ["Mock mode: the facts echoed back, not a real translation."],
            }
        )
    if name == "league_games":
        return json.dumps(MOCK_GAMES)
    if name == "grounds_availability":
        return json.dumps(MOCK_AVAILABILITY_MODEL)
    return "Mock reply."


_CHAT_CACHE: dict[tuple, ChatFn] = {}
_CHAT_LOCK = threading.Lock()


def _cached_chat(key: tuple, build: Callable[[], ChatFn]) -> ChatFn:
    """One chat (and its pooled HTTP client) per mode+credentials, shared across requests."""
    chat = _CHAT_CACHE.get(key)
    if chat is None:
        with _CHAT_LOCK:
            chat = _CHAT_CACHE.get(key)
            if chat is None:
                chat = build()
                _CHAT_CACHE[key] = chat
    return chat


def make_chat(mode: str) -> ChatFn:
    """Chat function for a UI mode; 400 when the mode needs a missing key."""
    if mode == "mock":
        return mock_chat
    if mode == "meta":
        _load_dotenv()
        config = ModelConfig.from_env()
        if config is None:
            raise HTTPException(400, "Meta API mode needs MODEL_API_KEY in .env (or use Mock mode).")
        return _cached_chat(("meta", config.base_url, config.model, config.api_key),
                            lambda: chat_fn(config))
    if mode == "open":
        _load_dotenv()
        base = os.environ.get("OPEN_MODEL_API_BASE", "")
        key = os.environ.get("OPEN_MODEL_API_KEY", "")
        model = os.environ.get("OPEN_MODEL", "") or "meta-models/muse-spark-open"
        if not base or not key:
            raise HTTPException(400, "Open-weights mode needs OPEN_MODEL_API_BASE/KEY in .env (or use Mock mode).")
        return _cached_chat(("open", base, model, key),
                            lambda: chat_fn(ModelConfig(base_url=base, model=model, api_key=key)))
    raise HTTPException(400, f"Unknown mode {mode!r}; use mock, meta or open.")


def _coach_busy() -> list[Busy]:
    """Tracked coach unavailability as up-front Problem.busy (the SaaS way).

    SquadRoot already tracks this, so the solver knows before the first
    solve instead of discovering it through a verify re-solve. The mock
    verify hook below stays as a safety net for busy times learned later.
    """
    from datetime import datetime as _dt, timedelta as _td

    tz = ZoneInfo(TZ)
    busy: list[Busy] = []
    day = date.fromisoformat(CLUB["season_start"])
    last = date.fromisoformat(CLUB["season_end"])
    while day <= last:
        for coach in COACHES:
            if day.weekday() != int(coach["unavailable_weekday"]):
                continue
            start_t = time.fromisoformat(coach["unavailable_from"])
            end_t = time.fromisoformat(coach["unavailable_until"])
            for team_id in coach["teams"]:
                busy.append(
                    Busy(
                        resource=f"team:{team_id}",
                        start=_dt.combine(day, start_t, tzinfo=tz),
                        end=_dt.combine(day, end_t, tzinfo=tz),
                        reason="coach unavailable",
                    )
                )
        day += _td(days=1)
    return busy


def _scheduling_end() -> date:
    """Last date the solver may place on (displayed season may run later)."""
    return date.fromisoformat(CLUB.get("scheduling_end") or CLUB["season_end"])


def _unscheduled_dates() -> list[date]:
    """Displayed-season dates after scheduling_end: grounds open, no sessions.

    Returned as blackout dates so nothing is ever placed there (a late
    reschedule search crossing into December is blocked too), while the
    fields keep their normal open hours. The solver's own season ends
    at scheduling_end, so these dates produce no shortfalls.
    """
    day = _scheduling_end() + timedelta(days=1)
    last = date.fromisoformat(CLUB["season_end"])
    out: list[date] = []
    while day <= last:
        out.append(day)
        day += timedelta(days=1)
    return out


def club_problem(extra_busy: list[Busy] | None = None) -> Problem:
    """Rebuild the demo Problem from demo/club.json plus optional game busy."""
    return Problem(
        teams=[Team(id=t["id"], name=t["name"]) for t in CLUB["teams"]],
        fields=[
            Field(
                id=f["id"],
                name=f["name"],
                open_from=time.fromisoformat(f["open_from"]) if f.get("open_from") else None,
                open_until=time.fromisoformat(f["open_until"]) if f.get("open_until") else None,
            )
            for f in CLUB["fields"]
        ],
        season_start=date.fromisoformat(CLUB["season_start"]),
        season_end=_scheduling_end(),
        timezone=TZ,
        busy=_coach_busy() + list(extra_busy or []),
        blackout_dates=_unscheduled_dates(),
    )


def mock_verify_hook(problem: Problem):  # type: ignore[no-untyped-def]
    """Host-style hook: each coach's unavailable evening is team busy time."""
    tz = ZoneInfo(problem.timezone)
    windows = [
        (set(c["teams"]), int(c["unavailable_weekday"]),
         time.fromisoformat(c["unavailable_from"]), time.fromisoformat(c["unavailable_until"]))
        for c in COACHES
    ]

    def hook(events):  # type: ignore[no-untyped-def]
        busy: list[Busy] = []
        for event in events:
            local = event.starts_at.astimezone(tz)
            for coach_teams, day, start_t, end_t in windows:
                if event.team_id not in coach_teams or local.weekday() != day:
                    continue
                evening_start = local.replace(hour=start_t.hour, minute=start_t.minute, second=0, microsecond=0)
                evening_end = local.replace(hour=end_t.hour, minute=end_t.minute, second=0, microsecond=0)
                if event.starts_at < evening_end and evening_start < event.ends_at:
                    busy.append(Busy(resource=f"team:{event.team_id}", start=evening_start,
                                     end=evening_end, reason="coach unavailable"))
        return busy

    return hook


def _before_events(kwargs: dict) -> list:  # type: ignore[no-untyped-def]
    """Saved-calendar events a call started from (dicts or ProposedEvents)."""
    raw = kwargs.get("events")
    if not raw and isinstance(kwargs.get("proposal"), dict):
        raw = kwargs["proposal"].get("events")
    out = []
    for item in raw or []:
        try:
            out.append(item if hasattr(item, "starts_at") else ProposedEvent.from_dict(item))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _understood(problem, trace, before) -> list:  # type: ignore[no-untyped-def]
    """'What I understood' chips: one per edit op in the agent trace."""
    step = next((s for s in reversed(trace or []) if s.get("step") == "edit_plan"), None)
    if not step:
        return []
    team_names = {t.id: t.name for t in problem.teams}
    field_names = {f.id: f.name for f in problem.fields}
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    before_by_key = {}
    for e in before:
        try:
            from schedule_agent import session_key as _sk
            before_by_key[_sk(e)] = e
        except Exception:
            pass
    chips = []
    for op in step.get("ops_detail") or []:
        target = op.get("target") or {}
        bits = []
        for sid in target.get("session_ids") or []:
            ev = before_by_key.get(sid)
            bits.append(ev.title if ev is not None else str(sid).split("|")[0])
        bits += [team_names.get(x, x) for x in target.get("team_ids") or []]
        bits += [field_names.get(x, x) for x in target.get("field_ids") or []]
        bits += [days[d] for d in target.get("weekdays") or [] if isinstance(d, int) and 0 <= d <= 6]
        if target.get("date_from"):
            bits.append(str(target["date_from"]))
        bits += [str(x) for x in target.get("event_types") or []]
        if op.get("team_id"):
            bits.append(team_names.get(op["team_id"], op["team_id"]))
        to = op.get("to") or {}
        place = []
        if to.get("date"):
            place.append(str(to["date"]))
        place += [days[d] for d in to.get("weekdays") or [] if isinstance(d, int) and 0 <= d <= 6]
        if to.get("start_time"):
            place.append(str(to["start_time"])[:5])
        if to.get("field_id"):
            place.append(field_names.get(to["field_id"], to["field_id"]))
        exactness = to.get("exactness") or "any"
        target_text = " ".join(bits) if bits else "new session"
        place_text = " ".join(place) if place else "solver picks"
        text = f"{op.get('action')} \u00b7 {target_text} \u00b7 -> {place_text} \u00b7 {exactness}"
        chips.append({"action": op.get("action"), "target": target_text, "placement": place_text,
                      "exactness": exactness, "text": text})
    return chips


def run_agent(mode: str, call: str, _chat: ChatFn | None = None, **kwargs):  # type: ignore[no-untyped-def]
    """Run one agent call with the mock coach hook; return proposal + trace."""
    chat = _chat if _chat is not None else make_chat(mode)
    # Deterministic English summaries: no slow explain model call in the demo.
    agent = ScheduleAgent(chat, deterministic_summary=True)
    problem = kwargs.pop("problem")
    try:
        if call == "plan":
            proposal = agent.plan(problem, kwargs["request"], previous=kwargs.get("previous"),
                                  verify=mock_verify_hook(problem), games=kwargs.get("games"))
        elif call == "revise":
            proposal = agent.revise(problem, kwargs["proposal"], kwargs["request"],
                                    verify=mock_verify_hook(problem), games=kwargs.get("games"))
        elif call == "reschedule":
            proposal = agent.reschedule(problem, kwargs["events"], kwargs["request"],
                                        verify=mock_verify_hook(problem))
        elif call == "cancel":
            proposal = agent.cancel(problem, kwargs["events"], kwargs["request"])
        elif call == "add":
            proposal = agent.add(problem, kwargs["events"], kwargs["request"],
                                 verify=mock_verify_hook(problem))
        elif call == "run":
            proposal = agent.handle(problem, kwargs["request"], proposal=kwargs.get("proposal"),
                                    events=kwargs.get("events"), previous=kwargs.get("previous"),
                                    verify=mock_verify_hook(problem), games=kwargs.get("games"),
                                    selected_session_ids=kwargs.get("selected_session_ids"))
        else:
            raise HTTPException(400, f"Unknown call {call!r}.")
    except HTTPException:
        raise
    except Exception as exc:
        from schedule_agent import AgentError, ModelError

        if isinstance(exc, (AgentError, ModelError)):
            raise HTTPException(400, str(exc)) from exc
        raise
    return {"proposal": proposal.to_dict(), "trace": agent.last_trace,
            "questions": list(proposal.questions), "understood": _understood(problem, agent.last_trace, _before_events(kwargs)),
            "clarification": None}


app = FastAPI(title="SquadRoot Schedule Agent demo")


def available_modes() -> dict:
    """Which model modes can actually run (booleans only, never key values)."""
    _load_dotenv()
    return {
        "mock": True,
        "meta": ModelConfig.from_env() is not None,
        "open": bool(os.environ.get("OPEN_MODEL_API_BASE") and os.environ.get("OPEN_MODEL_API_KEY")),
    }


@app.get("/api/club")
def get_club() -> dict:
    """Demo club data, presets and coach notes for the page."""
    coaches = [
        {
            "id": c.get("id", ""), "label": c.get("label", "Coach"),
            "teams": [TEAM_NAMES.get(t, t) for t in c["teams"]],
            "back_to_back": [[TEAM_NAMES.get(t, t) for t in pair] for pair in c.get("back_to_back", [])],
            "note": c["note"],
        }
        for c in COACHES
    ]
    return {
        "club": CLUB["club"],
        "timezone": TZ,
        "season_start": CLUB["season_start"],
        "season_end": CLUB["season_end"],
        "scheduling_end": _scheduling_end().isoformat(),
        "teams": [{"id": t["id"], "name": t["name"], "languages": LANGUAGES_BY_TEAM[t["id"]]}
                  for t in CLUB["teams"]],
        "fields": CLUB["fields"],
        "coaches": coaches,
        "coach_note": " ".join(c["note"] for c in COACHES),
        "presets": CLUB["presets"],
        "modes": available_modes(),
    }


def demo_calendar() -> dict:
    """Deterministic sample calendar for the demo season (mock plan, no key)."""
    problem = club_problem()
    agent = ScheduleAgent(mock_chat, deterministic_summary=True)
    proposal = agent.plan(problem, CLUB["presets"]["plan_request"], verify=mock_verify_hook(problem))
    from schedule_agent import session_key as _session_key
    return {
        "events": [dict(e.to_dict(), session_id=_session_key(e)) for e in proposal.events],
        "proposal": proposal.to_dict(),
        "trace": agent.last_trace,
        "season_start": CLUB["season_start"],
        "season_end": CLUB["season_end"],
        "scheduling_end": _scheduling_end().isoformat(),
        "team_count": len(CLUB["teams"]),
        "field_count": len(CLUB["fields"]),
        "coach_count": len(COACHES),
    }


@app.get("/api/calendar")
def get_calendar() -> dict:
    """Current sample schedule (November 2026 sessions), shown on page load."""
    return demo_calendar()


def _problem_with_games(body: dict) -> tuple[Problem, list[Busy]]:
    """Base club problem plus the game busy ``body['games']`` would create.

    The agent receives the games themselves (as fixed game events), so
    the returned problem deliberately does not carry their busy -- the
    busy list is only for deciding the default request text.
    """
    extra = [Busy.from_dict(b) for b in body.get("extra_busy", [])]
    problem = club_problem(extra)
    games = body.get("games")
    if not games:
        return problem, []
    return problem, games_to_busy(ScheduleImport.from_dict(games), problem)


def _request_with_games_default(body: dict, game_busy: list[Busy]) -> str:
    """Request text for plan/run: uploaded games alone are a valid ask.

    "Plan around these games" sends no typed request; with at least one
    blocking game the club's standard practice request is planned around
    them instead of failing with "please describe the request". Games
    that matched nothing block nothing, so that case fails clearly.
    """
    request = body.get("request") or ""
    if isinstance(request, str) and request.strip():
        return request
    if game_busy:
        return str(CLUB["presets"]["plan_request"])
    if body.get("games"):
        raise HTTPException(
            400,
            "None of those games matched a team or field in this club, so there is "
            "nothing to plan around. Check the photo names, or type the practice request.",
        )
    return request if isinstance(request, str) else ""


@app.post("/api/plan")
def api_plan(body: dict) -> dict:
    """Plan a draft from a request; extra_busy adds host-known busy; games adds league games."""
    problem, game_busy = _problem_with_games(body)
    return run_agent(body.get("mode", "mock"), "plan", problem=problem,
                     request=_request_with_games_default(body, game_busy), previous=body.get("previous"),
                     games=body.get("games"))


@app.post("/api/revise")
def api_revise(body: dict) -> dict:
    """Revise a draft; everything else is kept."""
    problem = club_problem([Busy.from_dict(b) for b in body.get("extra_busy", [])])
    return run_agent(body.get("mode", "mock"), "revise", problem=problem,
                     proposal=body.get("proposal"), request=body.get("request", ""),
                     games=body.get("games"))


@app.post("/api/reschedule")
def api_reschedule(body: dict) -> dict:
    """Move only the sessions hit by a disruption."""
    problem = club_problem([Busy.from_dict(b) for b in body.get("extra_busy", [])])
    return run_agent(body.get("mode", "mock"), "reschedule", problem=problem,
                     events=body.get("events", []), request=body.get("request", ""))


@app.post("/api/cancel")
def api_cancel(body: dict) -> dict:
    """Cancel the sessions matching a request."""
    problem = club_problem()
    return run_agent(body.get("mode", "mock"), "cancel", problem=problem,
                     events=body.get("events", []), request=body.get("request", ""))


@app.post("/api/add")
def api_add(body: dict) -> dict:
    """Add new session(s) to a saved calendar; existing sessions never move."""
    problem = club_problem([Busy.from_dict(b) for b in body.get("extra_busy", [])])
    return run_agent(body.get("mode", "mock"), "add", problem=problem,
                     events=body.get("events", []), request=body.get("request", ""))


@app.post("/api/run")
def api_run(body: dict) -> dict:
    """Route any wording to the right call (plan/revise/reschedule/cancel) and run it.

    One entry point for the app: send the request plus whatever the host
    already has (proposal draft and/or saved calendar events) and the
    router picks the call. Plan-first safety applies: cancel/reschedule
    with no calendar fails with "no calendar yet".
    """
    problem, game_busy = _problem_with_games(body)
    return run_agent(body.get("mode", "mock"), "run", problem=problem,
                     request=_request_with_games_default(body, game_busy), proposal=body.get("proposal"),
                     events=body.get("events"), previous=body.get("previous"),
                     games=body.get("games"), selected_session_ids=body.get("selected_session_ids"))


_STREAM_LABELS = {
    "route_intent": "Routing the request",
    "edit_plan": "Understanding the changes",
    "scheduling_rules": "Understanding the request",
    "disruption": "Reading the disruption",
    "cancel_filter": "Reading the cancellation",
    "family_notices": "Drafting notices",
    "league_games": "Reading the schedule photo",
    "grounds_availability": "Reading the grounds photo",
}


def _stream_label(response_format: object) -> str:
    """Human label for a model call, from its strict-schema name (or plain explain)."""
    if response_format is None:
        return "Explaining the summary"
    name = ""
    if isinstance(response_format, dict):
        try:
            name = str(response_format["json_schema"]["name"])
        except (KeyError, TypeError):
            name = ""
    return _STREAM_LABELS.get(name, "Calling the model")


def _stream_kwargs(call: str, body: dict) -> dict:
    """run_agent kwargs for a stream call; mirrors the /api/plan|revise|reschedule|cancel|run bodies."""
    if call == "plan":
        problem, game_busy = _problem_with_games(body)
        return {"problem": problem, "request": _request_with_games_default(body, game_busy),
                "previous": body.get("previous"), "games": body.get("games")}
    if call == "revise":
        problem = club_problem([Busy.from_dict(b) for b in body.get("extra_busy", [])])
        return {"problem": problem, "proposal": body.get("proposal"), "request": body.get("request", ""),
                "games": body.get("games")}
    if call == "reschedule":
        problem = club_problem([Busy.from_dict(b) for b in body.get("extra_busy", [])])
        return {"problem": problem, "events": body.get("events", []), "request": body.get("request", "")}
    if call == "cancel":
        return {"problem": club_problem(), "events": body.get("events", []), "request": body.get("request", "")}
    if call == "add":
        problem = club_problem([Busy.from_dict(b) for b in body.get("extra_busy", [])])
        return {"problem": problem, "events": body.get("events", []), "request": body.get("request", "")}
    if call == "run":
        problem, game_busy = _problem_with_games(body)
        return {"problem": problem, "request": _request_with_games_default(body, game_busy),
                "proposal": body.get("proposal"), "events": body.get("events"),
                "previous": body.get("previous"), "games": body.get("games"), "selected_session_ids": body.get("selected_session_ids")}
    raise HTTPException(400, f"Unknown call {call!r}; use plan, revise, reschedule, cancel, add or run.")


def stream_events(call: str, mode: str, base: ChatFn, kwargs: dict):  # type: ignore[no-untyped-def]
    """Yield step start/done dicts, then one result (or error) dict, for an agent call.

    The agent runs on a worker thread while this generator drains its queued
    step events: plain threads and a queue, no event loop, no extra sockets.
    """
    pending: queue.Queue = queue.Queue()

    def chat(messages: list, *, response_format=None, usage_out=None):  # type: ignore[no-untyped-def]
        label = _stream_label(response_format)
        pending.put({"type": "step", "phase": "start", "label": label})
        started = monotonic()
        try:
            if usage_out is not None:
                return base(messages, response_format=response_format, usage_out=usage_out)
            return base(messages, response_format=response_format)
        finally:
            pending.put({"type": "step", "phase": "done", "label": label,
                         "latency_ms": int((monotonic() - started) * 1000)})

    def work() -> None:
        try:
            out = run_agent(mode, call, _chat=chat, **kwargs)
        except HTTPException as exc:
            pending.put({"type": "error", "detail": exc.detail})
            return
        except Exception as exc:
            from schedule_agent import AgentError, ModelError

            if isinstance(exc, (AgentError, ModelError)):
                pending.put({"type": "error", "detail": str(exc)})
            else:
                pending.put({"type": "error", "detail": f"Unexpected error: {exc}"})
            return
        pending.put({"type": "result", "proposal": out["proposal"], "trace": out["trace"],
                     "clarification": out.get("clarification"), "questions": out.get("questions"), "understood": out.get("understood")})

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    while True:
        evt = pending.get()
        yield evt
        if evt.get("type") in ("result", "error"):
            break
    worker.join()


@app.post("/api/stream")
def api_stream(body: dict):  # type: ignore[no-untyped-def]
    """Stream an agent call as server-sent events: step start/done, then the result.

    Same work as /api/plan|revise|reschedule|cancel|run (body adds "call"),
    but the page can show each model call starting and finishing instead of
    one frozen wait.
    """
    call = body.get("call", "plan")
    mode = body.get("mode", "mock")
    base = make_chat(mode)  # 400 early when a key is missing, before streaming starts
    kwargs = _stream_kwargs(call, body)  # 400 early on an unknown call

    def gen():  # type: ignore[no-untyped-def]
        for evt in stream_events(call, mode, base, kwargs):
            yield "data: " + json.dumps(evt) + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/approve")
def api_approve(body: dict) -> dict:
    """Approve a checked draft (the agent still applies nothing)."""
    from schedule_agent import AgentError

    agent = ScheduleAgent(mock_chat)
    try:
        result = agent.approve(body.get("proposal"))
    except AgentError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"result": result.to_dict(), "trace": agent.last_trace}


@app.post("/api/notices")
def api_notices(body: dict) -> dict:
    """Draft one family notice per language used by the teams in the events."""
    from schedule_agent import AgentError, ModelError

    mode = body.get("mode", "mock")
    events = [ProposedEvent.from_dict(e) for e in body.get("events", [])]
    if not events:
        raise HTTPException(400, "There are no sessions to announce yet.")
    langs: list[str] = []
    for event in events:
        for lang in LANGUAGES_BY_TEAM.get(event.team_id, ["en"]):
            if lang not in langs:
                langs.append(lang)
    try:
        notices = draft_notices(club_problem(), events, make_chat(mode), languages=langs)
    except (AgentError, ModelError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"notices": notices.to_dict(), "languages": langs}


def voice_result(mode: str, data: bytes, filename: str, mime_type: str | None) -> dict:
    """Transcribe voice bytes; mock mode returns the checked-in sample transcript."""
    if not data:
        raise HTTPException(400, "No audio received.")
    if mode == "mock":
        return {"transcript": SAMPLE_TRANSCRIPT, "mock": True, "latency_ms": 0,
                "note": "Mock mode: sample transcript from demo/voice_note.txt."}
    if mode != "meta":
        raise HTTPException(400, "Voice transcription needs the Meta API key (or use Mock mode).")
    _load_dotenv()
    config = VoiceConfig.from_env()
    if config is None:
        raise HTTPException(400, "Voice needs MODEL_API_KEY in .env (or use Mock mode).")
    from schedule_agent import ModelError

    usage: dict = {}
    try:
        text = transcribe_audio(config, data, filename=filename, mime_type=mime_type, usage_out=usage)
    except ModelError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Surface the split: transcription time vs the agent run that follows.
    return {"transcript": text, "mock": False,
            "latency_ms": usage.get("latency_ms"), "model": usage.get("model")}


@app.post("/api/voice")
async def api_voice(mode: str = Form("mock"), audio: UploadFile = File(...)) -> dict:
    """Transcribe a voice note; mock mode returns the checked-in sample transcript."""
    data = await audio.read()
    return voice_result(mode, data, audio.filename or "note", audio.content_type)


def vision_result(mode: str, data: bytes, mime_type: str | None) -> dict:
    """Read league games from photo bytes; mock mode returns labelled sample rows."""
    if not data:
        raise HTTPException(400, "No image received.")
    if mode == "mock":
        schedule = {"games": [dict(g, matched=True, blocks_time=True) for g in MOCK_GAMES["games"]],
                    "notes": list(MOCK_GAMES["notes"])}
        return {"schedule": schedule, "mock": True,
                "note": "Mock mode: sample rows, not read from your upload."}
    from schedule_agent import AgentError, ModelError

    try:
        schedule = extract_games(club_problem(), data, make_chat(mode),
                                 mime_type=mime_type or "image/png")
    except (AgentError, ModelError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"schedule": schedule.to_dict(), "mock": False}


def availability_result(mode: str, data: bytes, mime_type: str | None) -> dict:
    """Read ground hours + coach unavailability from a photo; mock is labelled."""
    if not data:
        raise HTTPException(400, "No image received.")
    problem = club_problem()
    if mode == "mock":
        imported = extract_availability(problem, data, mock_chat,
                                        mime_type=mime_type or "image/png")
        busy = availability_to_busy(imported, problem)
        return _availability_payload(imported, busy, mock=True,
                                     note="Mock mode: sample ground hours and coach windows, not read from your upload.")
    from schedule_agent import AgentError, ModelError

    try:
        imported = extract_availability(problem, data, make_chat(mode),
                                        mime_type=mime_type or "image/png")
    except (AgentError, ModelError) as exc:
        raise HTTPException(400, str(exc)) from exc
    busy = availability_to_busy(imported, problem)
    return _availability_payload(imported, busy, mock=False, note="")


def _availability_payload(imported: AvailabilityImport, busy: list[Busy], *, mock: bool, note: str) -> dict:
    """UI-ready availability: matched flags, team count and solver busy times."""
    payload = imported.to_dict()
    for row in payload["grounds"]:
        row["matched"] = bool(row.get("field_id") and row.get("weekdays")
                              and row.get("open_from") and row.get("open_until"))
    for row in payload["coach"]:
        row["matched"] = bool(row.get("team_id") and row.get("start") and row.get("end"))
    teams_seen = sorted({r["team"] for r in payload["coach"] if r.get("team")})
    return {
        "availability": payload,
        "busy": [b.to_dict() for b in busy],
        "busy_count": len(busy),
        "team_count": len(CLUB["teams"]),
        "teams_seen": teams_seen,
        "mock": mock,
        "note": note,
    }


@app.post("/api/vision")
async def api_vision(mode: str = Form("mock"), image: UploadFile = File(...)) -> dict:
    """Read league games from a schedule photo; mock mode returns labelled sample rows."""
    data = await image.read()
    return vision_result(mode, data, image.content_type)


@app.post("/api/availability")
async def api_availability(mode: str = Form("mock"), image: UploadFile = File(...)) -> dict:
    """Read ground booking hours and coach unavailability from a photo."""
    data = await image.read()
    return availability_result(mode, data, image.content_type)


@app.get("/", response_class=FileResponse)
def index() -> FileResponse:
    """Serve the single-file demo page."""
    return FileResponse(ROOT / "app" / "static" / "index.html")
