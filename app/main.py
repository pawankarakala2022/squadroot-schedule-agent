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
from datetime import date, datetime, time
from time import monotonic
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from schedule_agent import (
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
    chat_fn,
    draft_notices,
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
COACH = CLUB["coach"]
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


def _load_dotenv() -> None:
    """Fill allowlisted vars from ROOT/.env without overriding real env.

    The library reads only os.environ (no file I/O by design), so the
    demo app does the .env loading here. Runs on every mode lookup so
    editing .env does not require re-importing the app.
    """
    try:
        text = (ROOT / ".env").read_text(encoding="utf-8")
    except OSError:
        return
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

MOCK_RULES = {
    "requirements": [
        {
            "team_alias": "T1",
            "event_type": "practice",
            "sessions_per_week": 2,
            "duration_minutes": 60,
            "allowed_days": [0, 2],
            "allowed_field_aliases": [],
            "earliest_start": "17:00",
            "latest_end": "20:00",
            "title": "",
        },
        {
            "team_alias": "T2",
            "event_type": "practice",
            "sessions_per_week": 1,
            "duration_minutes": 60,
            "allowed_days": [3],
            "allowed_field_aliases": [],
            "earliest_start": "17:00",
            "latest_end": "20:00",
            "title": "",
        },
    ],
    "min_gap_days": 1,
    "blackout_dates": [],
    "notes": ["Mock mode: the same scripted rules answer every request."],
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
    if name == "route_intent":
        return json.dumps({"call": mock_route_call(messages), "notes": ["Mock mode: keyword router."]})
    if name == "scheduling_rules":
        blob = " ".join(str(m.get("content", "")) for m in messages).lower()
        if "produce only the session(s) this request adds" in blob:
            return json.dumps(MOCK_ADD_GAME)
        return json.dumps(MOCK_RULES)
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
    start_t = time.fromisoformat(COACH["unavailable_from"])
    end_t = time.fromisoformat(COACH["unavailable_until"])
    weekday = int(COACH["unavailable_weekday"])
    busy: list[Busy] = []
    day = date.fromisoformat(CLUB["season_start"])
    last = date.fromisoformat(CLUB["season_end"])
    while day <= last:
        if day.weekday() == weekday:
            for team_id in COACH["teams"]:
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
        season_end=date.fromisoformat(CLUB["season_end"]),
        timezone=TZ,
        busy=_coach_busy() + list(extra_busy or []),
    )


def mock_verify_hook(problem: Problem):  # type: ignore[no-untyped-def]
    """Host-style hook: the coach's Wednesday evening is team busy time."""
    tz = ZoneInfo(problem.timezone)
    coach_teams = set(COACH["teams"])
    day = int(COACH["unavailable_weekday"])
    start_t = time.fromisoformat(COACH["unavailable_from"])
    end_t = time.fromisoformat(COACH["unavailable_until"])

    def hook(events):  # type: ignore[no-untyped-def]
        busy: list[Busy] = []
        for event in events:
            if event.team_id not in coach_teams:
                continue
            local = event.starts_at.astimezone(tz)
            if local.weekday() != day:
                continue
            evening_start = local.replace(hour=start_t.hour, minute=start_t.minute, second=0, microsecond=0)
            evening_end = local.replace(hour=end_t.hour, minute=end_t.minute, second=0, microsecond=0)
            if event.starts_at < evening_end and evening_start < event.ends_at:
                busy.append(Busy(resource=f"team:{event.team_id}", start=evening_start,
                                 end=evening_end, reason="coach unavailable"))
        return busy

    return hook


def run_agent(mode: str, call: str, _chat: ChatFn | None = None, **kwargs):  # type: ignore[no-untyped-def]
    """Run one agent call with the mock coach hook; return proposal + trace."""
    chat = _chat if _chat is not None else make_chat(mode)
    # Deterministic English summaries: no slow explain model call in the demo.
    agent = ScheduleAgent(chat, deterministic_summary=True)
    problem = kwargs.pop("problem")
    try:
        if call == "plan":
            proposal = agent.plan(problem, kwargs["request"], previous=kwargs.get("previous"),
                                  verify=mock_verify_hook(problem))
        elif call == "revise":
            proposal = agent.revise(problem, kwargs["proposal"], kwargs["request"],
                                    verify=mock_verify_hook(problem))
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
                                    verify=mock_verify_hook(problem))
        else:
            raise HTTPException(400, f"Unknown call {call!r}.")
    except HTTPException:
        raise
    except Exception as exc:
        from schedule_agent import AgentError, ModelError

        if isinstance(exc, (AgentError, ModelError)):
            raise HTTPException(400, str(exc)) from exc
        raise
    return {"proposal": proposal.to_dict(), "trace": agent.last_trace}


app = FastAPI(title="SquadRoot Schedule Agent demo")


@app.get("/api/club")
def get_club() -> dict:
    """Demo club data, presets and coach note for the page."""
    return {
        "club": CLUB["club"],
        "timezone": TZ,
        "season_start": CLUB["season_start"],
        "season_end": CLUB["season_end"],
        "teams": [{"id": t["id"], "name": t["name"], "languages": LANGUAGES_BY_TEAM[t["id"]]}
                  for t in CLUB["teams"]],
        "fields": CLUB["fields"],
        "coach_note": COACH["note"],
        "presets": CLUB["presets"],
    }


@app.post("/api/plan")
def api_plan(body: dict) -> dict:
    """Plan a draft from a request; extra_busy adds host-known busy; games adds league games."""
    problem = club_problem([Busy.from_dict(b) for b in body.get("extra_busy", [])])
    games = body.get("games")
    if games:
        problem = club_problem(
            [Busy.from_dict(b) for b in body.get("extra_busy", [])]
            + games_to_busy(ScheduleImport.from_dict(games), problem)
        )
    return run_agent(body.get("mode", "mock"), "plan", problem=problem,
                     request=body.get("request", ""), previous=body.get("previous"))


@app.post("/api/revise")
def api_revise(body: dict) -> dict:
    """Revise a draft; everything else is kept."""
    problem = club_problem([Busy.from_dict(b) for b in body.get("extra_busy", [])])
    return run_agent(body.get("mode", "mock"), "revise", problem=problem,
                     proposal=body.get("proposal"), request=body.get("request", ""))


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
    problem = club_problem([Busy.from_dict(b) for b in body.get("extra_busy", [])])
    games = body.get("games")
    if games:
        problem = club_problem(
            [Busy.from_dict(b) for b in body.get("extra_busy", [])]
            + games_to_busy(ScheduleImport.from_dict(games), problem)
        )
    return run_agent(body.get("mode", "mock"), "run", problem=problem,
                     request=body.get("request", ""), proposal=body.get("proposal"),
                     events=body.get("events"), previous=body.get("previous"))


_STREAM_LABELS = {
    "route_intent": "Routing the request",
    "scheduling_rules": "Understanding the request",
    "disruption": "Reading the disruption",
    "cancel_filter": "Reading the cancellation",
    "family_notices": "Drafting notices",
    "league_games": "Reading the schedule photo",
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
        problem = club_problem([Busy.from_dict(b) for b in body.get("extra_busy", [])])
        games = body.get("games")
        if games:
            problem = club_problem(
                [Busy.from_dict(b) for b in body.get("extra_busy", [])]
                + games_to_busy(ScheduleImport.from_dict(games), problem)
            )
        return {"problem": problem, "request": body.get("request", ""), "previous": body.get("previous")}
    if call == "revise":
        problem = club_problem([Busy.from_dict(b) for b in body.get("extra_busy", [])])
        return {"problem": problem, "proposal": body.get("proposal"), "request": body.get("request", "")}
    if call == "reschedule":
        problem = club_problem([Busy.from_dict(b) for b in body.get("extra_busy", [])])
        return {"problem": problem, "events": body.get("events", []), "request": body.get("request", "")}
    if call == "cancel":
        return {"problem": club_problem(), "events": body.get("events", []), "request": body.get("request", "")}
    if call == "add":
        problem = club_problem([Busy.from_dict(b) for b in body.get("extra_busy", [])])
        return {"problem": problem, "events": body.get("events", []), "request": body.get("request", "")}
    if call == "run":
        problem = club_problem([Busy.from_dict(b) for b in body.get("extra_busy", [])])
        games = body.get("games")
        if games:
            problem = club_problem(
                [Busy.from_dict(b) for b in body.get("extra_busy", [])]
                + games_to_busy(ScheduleImport.from_dict(games), problem)
            )
        return {"problem": problem, "request": body.get("request", ""),
                "proposal": body.get("proposal"), "events": body.get("events"),
                "previous": body.get("previous")}
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
        pending.put({"type": "result", "proposal": out["proposal"], "trace": out["trace"]})

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
        return {"schedule": MOCK_GAMES, "mock": True,
                "note": "Mock mode: sample rows, not read from your upload."}
    from schedule_agent import AgentError, ModelError

    try:
        schedule = extract_games(club_problem(), data, make_chat(mode),
                                 mime_type=mime_type or "image/png")
    except (AgentError, ModelError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"schedule": schedule.to_dict(), "mock": False}


@app.post("/api/vision")
async def api_vision(mode: str = Form("mock"), image: UploadFile = File(...)) -> dict:
    """Read league games from a schedule photo; mock mode returns labelled sample rows."""
    data = await image.read()
    return vision_result(mode, data, image.content_type)


@app.get("/", response_class=FileResponse)
def index() -> FileResponse:
    """Serve the single-file demo page."""
    return FileResponse(ROOT / "app" / "static" / "index.html")
