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
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
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


def mock_chat(messages: list, *, response_format=None, usage_out=None) -> str:
    """Scripted stand-in: valid shapes, clearly labelled, no network."""
    if response_format is None:
        user_text = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        return "Mock summary: " + str(user_text)[:400]
    name = response_format["json_schema"]["name"]
    if name == "scheduling_rules":
        return json.dumps(MOCK_RULES)
    if name == "disruption":
        return json.dumps(MOCK_DISRUPTION)
    if name == "cancel_filter":
        return json.dumps(MOCK_CANCEL)
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


def make_chat(mode: str) -> ChatFn:
    """Chat function for a UI mode; 400 when the mode needs a missing key."""
    if mode == "mock":
        return mock_chat
    if mode == "meta":
        _load_dotenv()
        config = ModelConfig.from_env()
        if config is None:
            raise HTTPException(400, "Meta API mode needs MODEL_API_KEY in .env (or use Mock mode).")
        return chat_fn(config)
    if mode == "open":
        _load_dotenv()
        base = os.environ.get("OPEN_MODEL_API_BASE", "")
        key = os.environ.get("OPEN_MODEL_API_KEY", "")
        model = os.environ.get("OPEN_MODEL", "") or "meta-models/muse-spark-open-1026"
        if not base or not key:
            raise HTTPException(400, "Open-weights mode needs OPEN_MODEL_API_BASE/KEY in .env (or use Mock mode).")
        return chat_fn(ModelConfig(base_url=base, model=model, api_key=key))
    raise HTTPException(400, f"Unknown mode {mode!r}; use mock, meta or open.")


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
        busy=list(extra_busy or []),
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


def run_agent(mode: str, call: str, **kwargs):  # type: ignore[no-untyped-def]
    """Run one agent call with the mock coach hook; return proposal + trace."""
    chat = make_chat(mode)
    agent = ScheduleAgent(chat)
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
        return {"transcript": SAMPLE_TRANSCRIPT, "mock": True,
                "note": "Mock mode: sample transcript from demo/voice_note.txt."}
    if mode != "meta":
        raise HTTPException(400, "Voice transcription needs the Meta API key (or use Mock mode).")
    _load_dotenv()
    config = VoiceConfig.from_env()
    if config is None:
        raise HTTPException(400, "Voice needs MODEL_API_KEY in .env (or use Mock mode).")
    from schedule_agent import ModelError

    try:
        text = transcribe_audio(config, data, filename=filename, mime_type=mime_type)
    except ModelError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"transcript": text, "mock": False}


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
