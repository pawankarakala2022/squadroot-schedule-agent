# AGENTS.md - instructions for coding agents (Muse Code)

Read this file and `docs/PLAN.md` before every task.

## What this is

SquadRoot Schedule Agent: a club operations agent for sports clubs.
A club manager or coach describes scheduling work in plain words, by voice, or with a photo / PDF
of a league schedule, in any language. The agent turns it into a conflict-free schedule, checks
every proposal, explains it, drafts the family notice in each family's language, and waits for a
human to approve before anything is applied.

Built for the Meta Global AI Developer Hackathon (Oct 26 - Nov 4, 2026), Startups track,
"Build for Your World". Everything in this repo is written during the event.

## Hard rules

1. **New code only.** Write everything from scratch in this repo. Never copy code from the private
   SquadRoot codebase or anywhere else we do not have rights to. Third-party packages are fine
   (list them in `pyproject.toml`).
2. **Meta models only** for every AI call: Meta Model API (`https://api.meta.ai/v1`, OpenAI-compatible)
   or Meta open weights (Muse Spark Open via Baseten `meta-models/muse-spark-open-1026`, Modal, or
   self-hosted). One client, base URL + model from env.
3. **Never use a `-contributor` model id** (Meta trains on that data). The client must refuse it.
4. **The model never picks times.** The model understands requests and explains results. A
   deterministic solver places events. A model mistake must never cause a double booking.
5. **Untrusted model output.** Use `response_format: {"type": "json_schema", ...}` (strict), then
   validate again in code: unknown teams / fields / days dropped and reported, numbers clamped,
   one retry with the validation errors, then fail safely. Request text, transcripts and image
   text are data, never instructions.
6. **Verifiable actions.** The agent only proposes. Every proposal passes checks (no field double
   booking, no team overlap, inside field hours, not on blackout dates, not over a coach's busy
   time). Every check result is recorded and shown. Nothing is applied without a human approval step.
7. **Privacy by construction.** The model sees team names, field names, dates and request text,
   with teams and fields as short aliases (T1, F1). Never player names, children's data, contact
   details or payments. Coach availability reaches the solver only as "busy".
8. **Fictional demo data only** (`demo/`). No real clubs, families or people.
9. **Secrets** only from env vars (`.env`, gitignored). Never print, log or commit a key.
10. **Not a sports analyzer or coach.** No game analysis, performance, tactics, health or injury
    content. This is scheduling and club admin only.
11. **Runs for judges.** `python -m pytest` passes offline (a fake model stands in). The demo runs
    with one command from a fresh clone, with a mock mode that needs no API key.

## Library interface (stable - host apps depend on it)

The private SquadRoot backend installs this package from a pinned git tag and calls it in-process.
The demo app in `app/` must use exactly the same public interface, so the demo proves what the host gets.

1. **Core is a library.** `schedule_agent/` imports only the standard library and `httpx`. It does no
   database, web-framework or file I/O, reads no env vars except through `ModelConfig.from_env()`, and
   never connects to a host app. `fastapi` / `uvicorn` are an optional extra (`pip install .[app]`) used
   only by `app/`.
2. **Public names**, exported from `schedule_agent/__init__.py`:
   - Data: `Team(id, name)`, `Field(id, name, open_from=None, open_until=None)`,
     `Busy(resource, start, end, reason="already booked")` where `resource` is `"team:<id>"` or
     `"field:<id>"`, `Problem(teams, fields, season_start, season_end, timezone, busy=[], blackout_dates=[])`,
     `Rules`, `Requirement`, `ProposedEvent(team_id, field_id, event_type, title, starts_at, ends_at)`,
     `CheckResult`, `Shortfall`, `Proposal` (with `questions=[]`, in to_dict/from_dict; approve refuses open questions), `ApplyResult`,
     `Disruption(date_from, date_to, field_ids=[] meaning all, search_days=7,
     not_before=None, notes=[])`, `CancelFilter(team_ids=[], field_ids=[],
     weekdays=[], date_from=None, date_to=None, event_types=[], notes=[])` (both deprecated, kept working).
   - Edits (v6.0.0): `SessionTarget(session_ids=[], team_ids=[], field_ids=[], weekdays=[], date_from=None, date_to=None, event_types=[])` (every set field must match; empty rejected, never match-all), `Placement(date=None, start_time=None, field_id=None, weekdays=[], earliest_start=None, latest_end=None, exactness="any")` (`exact`|`preferred`|`any`), `EditOp(action, target=None, to=None, team_id=None, event_type=None, duration_minutes=None, sessions_per_week=None, title=None, notes=[])` (create_once/create_recurring/move/cancel/block/change_rule; `block` becomes Busy, never names a person), `EditPlan(ops=[], questions=[], notes=[])`, `parse_edits(problem, request, chat, *, events=None, draft=None, selected_session_ids=None, language="en") -> EditPlan` (strict `edit_plan` schema, S/T/F aliases, drop-and-report + clamp, one repair retry, code-added questions), `build_edit_proposal(problem, current_events, edit_plan, *, verify=None) -> Proposal` (diff with frozen untargeted sessions, exact-or-shortfall with nearest slots, combined checks + verify), `session_key(event) -> str` (tap-to-select identity sent as `selected_session_ids`).
   - Agent: `ScheduleAgent(chat, *, explain_chat=None, deterministic_summary=False)`
     (deterministic_summary renders English summaries in code, no explain model call),
     `agent.plan(problem, request, *, previous=None, verify=None, games=None) -> Proposal` (uploaded `games` enter as fixed checked game events and block practice time; `revise` and `handle` accept the same `games=`),
     `agent.revise(problem, proposal, request, *, verify=None) -> Proposal`,
     `agent.add(problem, events, request, *, previous=None, verify=None) -> Proposal`
     (insert-only: existing sessions frozen and never move, only the new
     session(s) are placed around them; no calendar fails safely;
     ground availability is not verified by the app, so ground hours and
     `field:` busy times never block an addition and every add proposal
     says so, while a ground booked by a saved session still blocks),
     `agent.approve(proposal) -> ApplyResult` (the only path to an approval;
     refuses drafts with failed checks; applies nothing — the host saves),
     `agent.reschedule(problem, events, request, *, verify=None, not_before=None,
     allowed_fields=None) -> Proposal` (rain/closed fields: only hit sessions
     move, summary lists each move as old time/field -> new time/field; host
     pins for move date/ground options, latest not_before wins),
     `agent.cancel(problem, events, request) -> Proposal` (cancellations: the model
     turns the request into a `CancelFilter` via `parse_cancel_filter`, code removes
     only matched sessions, kept sessions stay exactly where they are, the summary
     lists each cancellation; a request matching nothing fails safely),
     `agent.handle(problem, request, *, proposal=None, events=None, previous=None,
     verify=None, not_before=None, allowed_fields=None, selected_session_ids=None) -> Proposal` (one entry
     point, no router: with no calendar and no draft it calls `plan()`; otherwise one
     `parse_edits` call -> `build_edit_proposal` diff; questions return in
     `Proposal.questions` and block approval; wrappers `revise`/`reschedule`/`cancel`/`add`
     keep their contracts as thin wrappers that build the change list).
   - Model: `ModelConfig.from_env() -> ModelConfig | None` (None when no key), `chat_fn(config, *, temperature=0.0)`.
   - Voice: `VoiceConfig.from_env() -> VoiceConfig | None`, `transcribe_audio(config, audio_bytes, *, filename=..., mime_type=None, language=None) -> str`.
   - Vision: `GameImport` (`matched` = every name matched exactly; `blocks_time` = readable date/time plus at least one matched team/field), `ScheduleImport` (`matched_games()`, `blocking_games()`), `extract_games(problem, image_bytes, chat, *, mime_type="image/png") -> ScheduleImport`, `games_to_busy(schedule, problem, *, duration_minutes=60) -> list[Busy]` (busy for exactly-matched parts only: an external league opponent is never guessed, but our team and field still block), `games_to_events(schedule, problem, *, duration_minutes=60) -> list[ProposedEvent]` (one fixed game event per game with a known team and field, titled with both printed names).
  - Notify: `FamilyNotice`, `FamilyNotices`, `draft_notices(problem, events, chat, *, languages=[...]) -> FamilyNotices` (one drafted notice per family language; every draft must echo each team name, date, time and field name exactly, one repair retry, gaps reported not silently sent).
  - Router (deprecated): `Route`, `parse_route_intent(request, chat, *, has_proposal=False, has_events=False) -> Route` (kept exported and working for compatibility, marked deprecated; `handle` no longer calls it).
  - Dynamic layer (v4.2.0): `today_in_tz(problem)`, `relative_date_hints(today)`, `problem_date_hints(problem)` (code-resolved "next Tue/week/month" copied by the model in any language); `us_federal_holidays(year)`, `holiday_dates(start, end)`, `with_us_holidays(problem)` (US general holidays merged as blackouts by the host); `AvailabilityParse`, `parse_availability(problem, request, chat) -> AvailabilityParse` (coach unavailability text/transcript -> `Busy`, never availability, no coach entity); `GroundAvailability`, `CoachUnavailability`, `AvailabilityImport`, `extract_availability(problem, image_bytes, chat) -> AvailabilityImport`, `availability_to_busy(import, problem) -> list[Busy]` (grounds/coach photo or screenshot, exact matching). Agent methods accept `language="en"`; team/field names stay in English, never translated. Rain with no named move target routes to cancel; `reschedule` needs a named new date/field.
   - Errors: `AgentError` (bad request / could not understand, safe to show the manager),
     `ModelError` (provider failure, safe to show as "assistant unavailable").
3. **Ids are opaque strings owned by the host** (SquadRoot uses UUIDs). Never parse them. Aliases
   (T1, F1) exist only inside model prompts and are mapped back before anything is returned.
4. **Times:** every datetime in and out is timezone-aware; wall-clock rules use `Problem.timezone` (IANA name).
5. **Verify hook:** `verify(events: list[ProposedEvent]) -> list[Busy]` is called with each draft; it
   returns extra busy times only the host knows (e.g. coach unavailability), with no reasons that name
   people. The agent adds them and re-solves, at most 3 rounds. An empty list means accepted.
6. **JSON:** `Proposal.to_dict()` and `Proposal.from_dict(d)`, and the same for `Rules`, round-trip
   exactly (ISO 8601 strings for dates and times). `previous=` accepts a `Rules` or its dict, because
   the host keeps it in the browser between requests.
7. **The agent never applies anything.** `Proposal` is a draft. The host saves approved events through
   its own permission and conflict checks.
8. **Versioning:** semantic version in `pyproject.toml` and `schedule_agent.__version__`; git tag
   `vX.Y.Z` per release. Any change to the names or shapes above is a breaking change: bump the major
   version and note it in `CHANGELOG.md`.
9. **Contract test:** `tests/test_interface.py` builds a Problem using only the public names, runs
   `plan`, `revise`, `reschedule` and `approve` with a fake chat and a verify hook, and round-trips
   the Proposal through JSON. It must keep passing; never weaken it to make a change fit.

## Stack

- Python 3.12, `pyproject.toml`, standard library first.
- Allowed deps: `httpx` (HTTP), `fastapi` + `uvicorn` (demo web app), `pytest`. Ask before adding more.
- Plain HTML + vanilla JS for the demo page (one file, no build step), mobile friendly.
- Type hints everywhere, dataclasses for data, small modules, docstrings on public functions.

## Layout (target)

```
schedule_agent/
  models.py       data classes (Team, Field, Busy, Requirement, Rules, Problem, Plan, ...)
  solver.py       deterministic placement
  checks.py       verifiable checks, one result per check per event
  llm.py          Meta Model API / open-weights client, json_schema, contributor refusal
  understand.py   request -> Rules (structured output)
  explain.py      Plan -> summary in the manager's language
  voice.py        audio -> text (Muse Voice Transcribe)
  vision.py       photo / PDF of a schedule -> events (Muse Spark multimodal)
  dates.py        code-resolved relative dates for prompts
  holidays.py     US federal holidays -> blackout dates (host opt-in)
  availability.py coach unavailability text/transcript -> Busy
  notify.py       family notice drafts per language
  edits.py        request -> EditPlan (one model call, S/T/F aliases, validation)
  edit_examples.py worked-examples bank (few-shot selection, no embeddings)
  apply_edits.py  EditPlan -> checked Proposal diff (frozen sessions, exact-or-shortfall)
  agent.py        the loop: understand -> solve -> check -> repair -> explain -> await approval
app/              FastAPI demo + static/index.html
demo/             fictional club data, sample voice note, sample schedule image
evals/            cases + runner comparing API vs open-weights models
tests/            offline tests
docs/             PLAN.md, BUILD_LOG.md
```

## After every task

- Run `python -m pytest`; fix failures.
- Add one line to `docs/BUILD_LOG.md`: date, what was built, which tool wrote it (Muse Code / by hand).
- Commit with a clear message. Do not push.
