# Build plan (Oct 26 - Nov 4, 2026)

Deadline: **Wed Nov 4, 9:00 PM ET**. Aim to finish features Nov 2, video Nov 3, submit Nov 4 morning.

Judging: Impact 30%, Use of Meta AI tooling 25%, Technical depth 20%, Demo 25%.
Bonus targets: Best Agentic Use-Case, Best Build on Meta Open Weight Models, Most Viral, Community Vote.

Each phase below is one or more Muse Code sessions. The quoted prompt is what to paste into `muse`.

## Phase 1 - Core data + deterministic solver (Mon Oct 26 - Tue Oct 27)

> Read AGENTS.md and docs/PLAN.md. Build phase 1: `schedule_agent/models.py` and
> `schedule_agent/solver.py`. A Problem has teams, fields (with open hours), season start/end,
> timezone, blackout dates and busy times. Rules hold requirements (team, event type, sessions per
> week, duration, allowed days, allowed fields, earliest start, latest end) and a minimum gap in days
> between a team's sessions. The solver places sessions week by week, same slot every week where
> possible, never double books a field or a team, and returns a Plan with events and shortfalls
> (each with a plain reason). Deterministic: same input, same output. Add `pyproject.toml` and
> offline pytest tests including a tight case that must produce shortfalls. No AI calls yet.

## Phase 2 - Verifiable checks (Tue Oct 27)

> Build `schedule_agent/checks.py`: run named checks on any list of proposed events (field double
> booking, team overlap, field hours, blackout dates, busy times, minimum gap) and return one result
> per check per event (passed / failed + reason). The solver's output must pass all of them; add a
> test that feeds hand-made bad events and expects each check to catch its own problem.

## Phase 3 - Meta Model API client + understand + explain (Tue Oct 27 - Wed Oct 28)

> Build `schedule_agent/llm.py`: OpenAI-compatible chat client with httpx, env `META_MODEL_API_BASE`
> (default https://api.meta.ai/v1), `MODEL_API_KEY`, `META_MODEL` (default muse-spark-1.3). Refuse any
> model id ending in `-contributor`. Support `response_format` json_schema (strict:
> additionalProperties false, every key required, no recursion), timeouts, one retry on 5xx/429.
> Build `understand.py` (request in any language -> Rules, teams and fields as aliases T1/F1, then
> code validation: unknown ids dropped and reported, numbers clamped, one repair retry with the
> errors) and `explain.py` (Plan + check results -> short summary in the request's language).
> Tests use a fake chat function; no network.

Smoke test the real API once with a tiny request and note token usage in BUILD_LOG.

## Phase 4 - The agent loop (Wed Oct 28, after the Agents 102 session)

> Build `schedule_agent/agent.py`: plan(problem, request, previous_rules=None, verify=None) runs
> understand -> solve -> checks -> (if a host verify hook reports new busy times, add them and
> re-solve, max 3 rounds) -> explain. Returns a Proposal with rules, events, check results, shortfalls,
> summary, and a trace of every step (inputs/outputs sizes, model id, latency, tokens) for display.
> revise(proposal, "move U12 to Field 2") keeps everything else. approve(proposal) is the only way to
> produce an ApplyResult. Nothing is applied before approval.

## Phase 4b - Rain-outs and closed fields (Thu Oct 29, morning)

The calendar is already set and it rains Thursday: move only the hit sessions to free slots on the
fields that are still open, keep everything else where it is.

> Add `agent.reschedule(problem, events, request, *, verify=None) -> Proposal` for disruptions (rain,
> field closed). The model turns the request ("rain Thursday, North and South closed") into a
> `Disruption(date_from, date_to, field_ids or all, search_days=7)` with a strict json_schema; unknown
> fields are dropped and reported. Code then: finds the events hit; turns every other event into Busy
> for its team and field; adds whole-day Busy on the closed fields only (other fields stay usable);
> re-places only the hit sessions with the existing solver inside the search window, keeping each
> event's duration, type and title. Shortfalls name the sessions that could not be moved. The Proposal
> lists each move as old time/field -> new time/field. Coach busy times (Problem.busy and the verify
> hook) apply as in plan(). Add it to the AGENTS.md library interface and tests/test_interface.py.
> Tests: kept events never move, closed fields never used, open fields still used, nothing placed
> inside the disruption, deterministic, verify hook busy respected. Fake chat, no network.

## Phase 5 - Multimodal input (Thu Oct 29 - Fri Oct 30)

> Build `voice.py`: audio file -> text with muse-voice-transcribe-1.0, then into the same agent loop.
> Build `vision.py`: photo or PDF page of a league game schedule -> structured games (date, time,
> home/away, field) with json_schema, matched to our teams/fields with validation; unmatched rows
> reported, never guessed. Imported games become busy times so practices fit around them.
> Add fictional sample files in demo/ (record a short voice note; make a schedule image).

## Phase 6 - Family notices in each family's language (Fri Oct 30)

> Build `notify.py`: for an approved change, draft the notice for each language the team's families
> use (demo data lists languages per team, no names). Same facts in every language: verify dates,
> times and field names appear exactly in every draft; regenerate once if not.

## Review backlog (from the 2026-10-27 voice-capability review)

Voice input already feeds the whole loop (transcript -> plan / revise /
reschedule / notices), but the review found gaps. Cancellations are
being built now (`agent.cancel` + `CancelFilter`). Evaluate each item
below when scoping the next phase; build only what fits, in this order.
The "if time runs short" cut order at the bottom still applies.

1. Coach-availability parsing by voice ("coach can't do Thursdays
   anymore") -> validated `Busy` list, same strict json_schema +
   drop-and-report pattern as `parse_disruption`. Coach stays a time
   dimension only: no coach entity, no names.
2. Post-approval edits: a voice change to the live calendar is a fresh
   `plan` / `reschedule` / `cancel` + re-approve (the host saves).
   Document the flow; do not invent an apply path.
3. Multi-intent voice notes ("create X, move Y, tell families"):
   plan -> approve -> `draft_notices` chained by the host/demo, with the
   transcript visible in the agent trace.
4. Clarification turn: a vague request currently fails with "please
   rephrase". Consider one follow-up question before failing.
5. Voice approval stays out of scope: approval is an explicit human
   action in the host; a voice "yeah, go ahead" has no speaker
   verification and must never approve.
6. "My team" scoping: the host narrows the `Problem` to the speaker's
   teams before the model sees it (host-side, needed for the demo).
7. Voice eval cases: clean transcripts plus noisy ones (misheard team /
   field names must be dropped and reported, never guessed).

## Phase 7 - Open weights + evals (Sat Oct 31 - Sun Nov 1)

> Build `evals/`: 25 fictional cases (English, Spanish, Hindi, Punjabi, Vietnamese; tight fields;
> blackout weeks; revisions; a prompt-injection request) with expected rules. Runner scores rule
> accuracy, check pass rate, shortfalls, latency, cost per run, for muse-spark-1.3 (Meta API) and
> muse-spark-open-1026 (Baseten). Write results to evals/RESULTS.md.

## Phase 8 - Demo web app (Sun Nov 1 - Mon Nov 2)

> Build `app/` with FastAPI and one static index.html: pick the demo club, type / speak / upload a
> schedule, watch the agent's steps live (trace), see the week calendar, every check as a green /
> red chip, shortfalls, the summary, family notice drafts, then Approve. Model switch: Meta API /
> open weights / mock. Mock mode works with no key. Mobile friendly. `python -m app` starts it.
> Demo club data (fictional) includes: one coach who coaches two teams plus a weekly unavailable
> evening, sent as team Busy through a mock verify hook so the trace shows a re-solve round; a set
> calendar for the rain-out demo with a "Rain day" button that runs reschedule().

Deploy the demo somewhere public (e.g. Railway / Render / Fly) with a spend-limited key.

## Phase 9 - Ship (Tue Nov 3 - Wed Nov 4)

- README: what it does, how to run in 3 commands, "Built during the hackathon" list, eval table.
- 3-minute video (see below). Record Nov 3.
- Fresh clone test on another folder: install, pytest, mock demo.
- Repo public, video and live link open logged out, contributors listed. Submit.
- Post on X; share the gallery link with clubs.

## Video outline (3:00)

- 0:00 One line: "SquadRoot is our existing club app. Everything you see here was built this week, in a
  new open-source repo." Then the problem: a volunteer manager spends a weekend on the field schedule.
- 0:20 Voice note in Spanish -> plan in seconds, checks all green.
- 1:00 Photo of the league game schedule -> practices re-flow around games.
- 1:30 A bad request / injection attempt -> checks catch it, agent explains.
- 2:00 Rain-out by voice: "rain Thursday, North closed" -> only those sessions move, coach busy
  times respected; family notices in 3 languages; Approve.
- 2:20 Same run on Muse Spark Open (open weights), eval table.
- 2:45 Who it helps and what's next (inside SquadRoot as a hosted service).

## If time runs short, cut in this order

Phase 6 (notices) -> vision PDF (keep photo) -> eval languages -> Phase 4b (rain-outs). Never cut: checks, approval step, mock mode, tests.
