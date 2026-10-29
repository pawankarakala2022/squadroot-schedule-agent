# SquadRoot Schedule Agent

A club operations agent for sports clubs, built on Meta's Muse models.

A club manager describes scheduling work in plain words, by voice, or with a photo of the league's
game schedule, in any language. The agent drafts a conflict-free schedule, proves every event passes
its checks, explains the result, drafts the family notice in each family's language, and waits for a
human to approve before anything changes.

> Status: in progress for the Meta Global AI Developer Hackathon (Oct 26 - Nov 4, 2026).

## For judges — 60-second proof

This is not a mock-only project. The mock mode exists so anyone can run it without a key;
the evidence below is from live Meta models.

- **Live eval (2026-10-28):** `muse-spark-1.3` at `api.meta.ai` ran all 25 fictional cases
  (English, Spanish, Hindi, Punjabi, Vietnamese; tight fields, blackouts, revisions, a
  prompt-injection request): mean rule accuracy **1.00**, **834/834** checks passed,
  **25/25** shortfall matches, injection case safe. Full table: [evals/RESULTS.md](evals/RESULTS.md).
- **Live trace (captured 2026-10-28, demo club preset request, one Meta API call):**

  ```text
  understand  model=muse-spark-1.3  latency_ms=9468  total_tokens=2348
  solve       latency_ms=0        checks  latency_ms=0
  result      27 events placed, 162/162 checks passed, shortfalls named (never hidden)
  ```

  The solver and checks are deterministic code and take ~0 ms at club scale; only the
  `understand` step calls the model, and the model never picks times.
- **Meta models in the loop:** Muse Spark (understanding, schedule-photo reading,
  explanations, family notices), Muse Voice Transcribe (voice notes), and Muse Spark Open
  (open weights, same agent). The client refuses any `-contributor` model id.
- **Offline verification:** `python -m pytest` → **182 passed**, including the public-interface
  contract test (`tests/test_interface.py`) that a host app depends on.
- **Open weights (2026-10-28):** the same 25 cases on Muse Spark Open via Baseten
  (`meta-models/muse-spark-open` at `inference.baseten.co`): mean rule accuracy **0.94**,
  **834/834** checks, **25/25** shortfalls, mean latency 15.9s/case. The only misses were a
  duplicated requirement in three blackout cases; placements were unaffected. Both models
  are side by side in [evals/RESULTS.md](evals/RESULTS.md).
- **How this was judged internally:** a three-stage judging evaluation (pass/fail
  viability, 0–10 rubric scoring, live-video final) was run against this repo; its gap
  checklist drove the work above. The remaining gaps are deployment-free: feedback from
  volunteer managers and the 3-minute demo video.

## What was built during the hackathon

**Everything in this repository** was written during the event (from Oct 26, 2026). `docs/BUILD_LOG.md`
records what was built each day and the git history shows when.

**Pre-existing, not in this repo:** [SquadRoot](https://squadroot.com), our club and team management app
(private codebase, runs as a hosted service). This agent is designed to plug into it, but it runs and can be
verified entirely from this repo, with fictional demo data.

## Meta models used

| Model | Used for |
|---|---|
| Muse Spark (Meta Model API) | understanding requests, reading schedule images, explaining plans, family notices |
| Muse Voice Transcribe | voice notes from the field |
| Muse Spark Open (open weights, via Baseten) | the same agent on open weights; side-by-side evals |
| Muse Code | coding agent used to build this repo |

## Run it

Requires Python 3.12+ (a real installer — the zip/embeddable Python has no
`pip` or `venv` and cannot be used). From a fresh clone:

```powershell
py -3.12 -m venv .venv                            # once; `py -0p` lists your Pythons
.venv\Scripts\python -m pip install -e ".[app,test]"
.venv\Scripts\python -m pytest                    # offline tests (fake models stand in)
.venv\Scripts\python -m app                       # demo web app, then open http://127.0.0.1:8000
```

Always run `.venv\Scripts\python`, never bare `python` — bare `python` is
whatever is first on `PATH` and will report every library as missing.
Sanity check: `.venv\Scripts\python -c "import httpx, fastapi, uvicorn, pytest; print('ok')"`.
If the venv itself is broken (e.g. created from the zip Python), delete
`.venv` and recreate it with the commands above.
What gets installed, and why: `httpx` (the library's only runtime
dependency) plus `tzdata` on Windows only (Windows Python has no system
timezone database for `zoneinfo`), `fastapi` + `uvicorn` + `python-multipart` (the `[app]`
demo extra; multipart is required by the voice/photo upload endpoints),
`pytest` (the `[test]` extra). Everything else is the Python standard library;
pip resolves transitive dependencies automatically. There is no
separate requirements file — `pyproject.toml` is the source of truth.

### Run it with Docker (no local Python needed)

```powershell
docker build -t squadroot-schedule-agent .
docker run --rm squadroot-schedule-agent python -m pytest -q   # offline tests
docker run --rm -p 8000:8000 squadroot-schedule-agent          # demo at http://127.0.0.1:8000
```

Mock mode needs no key. For live mode add `--env-file .env` to the demo
command (never commit `.env`).

### How judges should run this

- **Replay / sample-data mode (no key, no account):** run the commands above, leave the
  `Model` dropdown on `Mock`, and use the preset buttons. Everything runs locally with
  fictional sample data (`demo/club.json`); mock answers are labelled `Mock mode: ...`.
  Nothing is sent to any API and there is nothing to log in to.
- **Live mode (repo runs live):** copy `.env.example` to `.env` and fill in
  `MODEL_API_KEY` (Meta Model API; get a key at `dev.meta.ai`). Restart
  `.venv\Scripts\python -m app`, set the dropdown to `Meta API (muse-spark)`, and run the same presets.
  The `Steps (trace)` panel then shows the live model id, latency and tokens. For the
  open-weights switch, also fill in `OPEN_MODEL_API_BASE` / `OPEN_MODEL_API_KEY` (Baseten)
  and pick `Open weights`. Step-by-step live setup: [docs/META_API_DEMO.md](docs/META_API_DEMO.md).
- **Sample media included:** `demo/schedule.png` (league schedule photo for the vision
  upload) and `demo/voice_note.txt` (the voice-note script; record it as 16 kHz mono WAV
  with `record_voice_note.html`). Voice and photo also run live in `Meta API` mode.

## Checking a voice request end to end

`manual_phase5_check.py` is the scratch manual check (untracked, repo root):

```powershell
.venv\Scripts\python manual_phase5_check.py                      # offline fakes
.venv\Scripts\python manual_phase5_check.py --live               # real models
.venv\Scripts\python manual_phase5_check.py --live --voice NOTE.wav  # your recording
```

Record with `record_voice_note.html` (16 kHz mono WAV, the only format the
transcription endpoint accepted in our live tests; phone `.m4a` is rejected).
Reading the output:

- `live transcript:` is your voice transcribed. Proof: it changes with each
  recording. The script prints it but feeds it nowhere: turning "add a game"
  speech into a created event is not built yet. Unknown teams (e.g. Falcons)
  are dropped and reported, and one-off games are not expressible in `Rules`,
  which only describes recurring sessions — so today that transcript creates
  nothing. An empty `REQUIREMENTS` list plus explanatory `notes` is the
  expected, safe result.
- `offline voice (empty audio)` is a deliberate self-test that feeds zero
  bytes to prove empty input fails safely. It never touches your file.

## Two model clients, how to tell them apart

- Chat (`schedule_agent/llm.py`, `chat_fn` → `POST /chat/completions`):
  used by understand, explain, and vision.
- Voice (`schedule_agent/voice.py`, `VoiceConfig` → `POST /asr/transcribe`
  with `request` + `audio` parts, `transcript` in the reply): used only by
  `transcribe_audio`.

Verify: pass `usage_out={}` — voice fills `muse-voice-transcribe-1.0`,
chat fills `muse-spark-1.3`. Or break one key: the other path keeps working.

## Calling it from a SaaS app

The core is an in-process Python library. A host SaaS installs it from a
pinned git tag and calls the same public interface the demo app uses.

```python
from schedule_agent import ModelConfig, chat_fn, ScheduleAgent

config = ModelConfig.from_env()  # META_MODEL_API_BASE + MODEL_API_KEY + META_MODEL
agent = ScheduleAgent(chat_fn(config))

proposal = agent.handle(
    problem, request_text,
    proposal=draft_or_none,
    events=saved_calendar_or_none,
    verify=my_verify_hook,  # (events) -> list[Busy]: host-only busy times
)
result = agent.approve(proposal)  # human approval; refuses failed checks, applies nothing
# The host saves result.events through its own permission and conflict checks.
```

Build `problem` from your own database (`Team(id, name)`, `Field(id, name, ...)`,
`Busy`, blackout dates, timezone). The model only sees team/field aliases
(`T1`, `F1`), dates, and request text; private availability reaches the
deterministic solver only as `Busy` intervals returned by `verify`.

Every call returns a draft `Proposal`: parsed `rules`, placed `events`
(timezone-aware `ProposedEvent` rows), one `check_results` entry per check per
event, `shortfalls` with plain-language reasons, a `summary` in the manager's
language, plus a JSON-safe step log on `agent.last_trace`. `Proposal.to_dict()`
and `Proposal.from_dict(d)` round-trip exactly, so drafts can live in the
browser between requests. Related calls are `plan`, `revise`, `reschedule`,
`cancel`, plus `extract_games`/`games_to_busy` (schedule photos), `transcribe_audio`
(voice), and `draft_notices` (one family notice per language).

The demo in `app/` is a reference host: it rebuilds the `Problem` from
fictional `demo/club.json`, uses a scripted mock chat and a mock coach-busy
`verify` hook, and exposes thin endpoints (`/api/plan`, `/api/revise`,
`/api/reschedule`, `/api/cancel`, `/api/run`, `/api/approve`, `/api/notices`,
`/api/voice`, `/api/vision`, `/api/add`). A real SaaS replaces five seams and keeps the
agent calls identical:

| Aspect | Demo app | Real SaaS |
|---|---|---|
| Problem source | JSON fixture | Query DB, map to data classes |
| Model chat | Scripted mock | `chat_fn(ModelConfig.from_env())` |
| Hidden busy | Mock coach hook | Real `verify` hook |
| Draft storage | Dicts in browser | Same dicts in own session store |
| Save | Nothing applied | Own save after `approve()` |

## Dynamic layer notes (v4.2.0) — required reading for hosts

- **Holidays (USA):** call `with_us_holidays(problem)` before planning to block the 11 US federal holidays as blackout dates. Add local/school holidays via `Problem.blackout_dates` or in the request.
- **Grounds / coach photo:** `extract_availability(problem, image_bytes, chat)` reads a phone screenshot ("field available Tue/Thu 5–8pm; coach unavailable Thursdays"); convert with `availability_to_busy(import, problem)`. Unmatched names are reported, never guessed.
- **Coach unavailability:** `parse_availability(problem, request_or_transcript, chat)` expands a text/voice note into `Busy` intervals. Unavailability only — never availability, never a coach name. Add the result to `Problem.busy` or return it from `verify`.
- **Rain default:** rain or a closed field with no named move target cancels. `reschedule` needs a named new date or field ("move Thursday to Friday"). Explicit `reschedule()` calls still move.
- **Adding a session:** "Add a game next Sunday" against a saved calendar is insert-only (`agent.add`, or routed by `handle`): existing sessions are frozen and never move; only the new session is placed, with a named shortfall if no slot is free. It is not a re-plan.
- **Team names stay in English** in every language; never translate or transliterate them. Pass `language="es"` (etc.) to `handle`/`plan`/`revise`/`reschedule`/`cancel` for summaries in the manager's language.
- **Relative dates** ("next Tue/week/month") are resolved in code in the club timezone; the model only copies concrete dates.
- **Host still owns:** login, per-club tenant isolation, upload size/type checks, audit log, and the final save-time conflict check. Do not deploy `app/` as the SaaS.

## Performance notes (v4.3.0) — if a request feels slow

- **Read `agent.last_trace` first.** Every solve/check step now carries `latency_ms`, as does every model call. At club scale the solver and checks are milliseconds to ~2s even for 100 teams; a step near 30,000ms is one model call hitting the client read timeout, not the solver. The voice endpoint also returns transcription `latency_ms`, so voice time and agent time are separate numbers.
- **Budget model calls, not the solver.** Measured live: two sample requests took 21.5s and 42.1s, all of it route + understand + explain (solve/checks 0ms), and follow-ups burn the most completion tokens. Three cuts are built in:
  - **Fast routing:** unambiguous English fresh practice/game requests, and explicit "add a game/practice" against a calendar, route in code with no model call (trace step `route` with `source: "fast_path"`). Cancel/move/weather/negation wording and other languages still go to the model.
  - **Deterministic English summaries:** build the agent with `ScheduleAgent(chat, deterministic_summary=True)` to render English summaries in code and skip the explain call entirely. Other languages keep the model explain. The demo app does this.
  - **Insert-only adds:** `agent.add(...)` / a routed "add a game…" understands only the new session (no previous-rules re-emit, the token-heaviest prompt) and places just it.
  With all three, both measured sample flows need exactly **one model call each** (understand). When the host UI already knows the intent, calling `plan`/`add`/`reschedule`/`cancel` directly still saves the router call on non-fast-path wording.
- **Explain stays small when the model does run.** Facts are recurring session patterns (max 80 lines), not one line per session, and the prompt caps the reply at 3 short sentences.
- **Pass coach unavailability up front** in `Problem.busy` (parse it with `parse_availability` / `availability_to_busy`). Discovering it through the verify hook costs an extra solve round and turns blocked weekdays into surprise shortfalls after the first draft.
- **Verify rounds multiply solve+check only** (max 3 re-solves, no model calls in the loop); a hook that returns new busy times every round is the usual cause of repeated work — return only genuinely new intervals.
- **Provider ceiling:** connect/pool failures fail in 5s; waiting for a model answer is capped at 30s (`REQUEST_TIMEOUT_SECONDS`). If traces repeatedly show ~30s model steps after the cuts above, the fix is provider/model choice, not solver tuning.

## License

MIT. See `LICENSE`.
