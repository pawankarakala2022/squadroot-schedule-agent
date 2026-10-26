# AGENTS.md - instructions for coding agents (Muse Code)

Read this file and `docs/PLAN.md` before every task.

## What this is

SquadRoot Schedule Agent: a club operations agent for volunteer-run community sports clubs.
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
  notify.py       family notice drafts per language
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
