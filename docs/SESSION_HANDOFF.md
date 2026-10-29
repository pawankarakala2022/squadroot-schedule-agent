# Session handoff (compact) — SquadRoot Schedule Agent

Saved 2026-10-28 21:44 ET. Read this + `AGENTS.md` + `docs/PLAN.md` to resume.
Full evaluation detail: `docs/JUDGING_EVALUATION.md` (local only, gitignored — do not
link it from public files).

## Project in one line

Club-ops scheduling agent for the Meta Global AI Developer Hackathon (Oct 26–Nov 4 2026,
deadline Nov 4 21:00 ET), Startups track. Core invention: **the model never picks times** —
deterministic solver places events, code checks every draft, human approves, agent applies
nothing. Privacy by construction (model sees only T1/F1 aliases; coach = busy intervals).

## Judging (from user + docs/PLAN.md)

- Weights: Impact 30%, Meta AI use 25%, Technical depth 20%, Demo 25%.
  Bonuses: Best Agentic Use-Case, Best Open Weight Models, Most Viral, Community Vote.
- Stage 1: pass/fail — theme fit + reasonable Meta Model API and/or open-weights use,
  built during hackathon.
- Stage 2: survivors scored 0–10 on the same rubric.
- Stage 3: top 5 per track; judges watch the 3-min videos live, same rubric.
- Hackathon note (user-quoted): **no hosted/public app required.** Repo must run live from
  the public GitHub setup instructions + 3-min video. Hosted versions must not need
  judges' own accounts — demo account, replay mode, or sample data + README instructions.

## Brutally honest evaluation (agreed with user)

- Extraordinary core (verifiable agent, privacy, multilingual/multimodal), but wins are
  decided by evidence + video, not architecture.
- Estimates today: Stage 1 pass ~90%; Top 5 ~10–15% as-was, ~35–45% if remaining gaps
  close; track win <5% as-was, ~10–15% after; Best Agentic is the best category.
- Stage-2 score estimate: ~6.9/10 now (Impact 7, Meta 7, Tech 9, Demo 5) → ~8.8 potential.
- Full reasoning: `docs/JUDGING_EVALUATION.md`.

## User constraints (do not violate)

- **No real clubs exist yet.** SquadRoot (pre-existing private product, not in this repo)
  has a dev account with demo clubs; still waiting for first customer club. Never invent
  testimonials/customers. Impact must be: distribution via SquadRoot + demo-club sample
  data + coach/manager feedback sessions (labelled as feedback) + quantified manual pain.
- SquadRoot demo clubs may seed sample data (teams/fields/shape only; no player/contact/
  payment data). If ever hosted publicly: mock-only, **no live keys** — demo endpoints
  take `mode` from the request body (`app/main.py`), so hosted live keys = public can burn
  the API budget. Key itself never leaks server-side; usage does.
- Secrets only in `.env` (gitignored); never print/log/commit keys.
- User commits themselves; assistant leaves changes uncommitted unless told `commit`.
  No push without request.

## Done (Steps 1–4 of the action plan; commits 2a2cd94, 7d79d08)

1. **Baseline frozen.** `python -m pytest` → 182 passed (incl. contract test 9 passed);
   versions match 4.3.0 (`pyproject.toml`, `schedule_agent/__init__.py`); mock smoke
   27 events, 162/162 checks, trace `understand→rules→solve→checks→verify→explain`.
2. **Stage-1 proof in README.** New section "For judges — 60-second proof" (live eval +
   live trace) and "How judges should run this" (venv creation, replay/mock no-account,
   live mode with judge's own key, sample media). Captured live trace: muse-spark-1.3,
   9,468 ms, 2,348 tokens, 27 events, 162/162, solve+checks ~0 ms.
3. **Open-weights eval done.** Baseten base `https://inference.baseten.co/v1`; correct slug
   is **`meta-models/muse-spark-open`** (the `-1026` id 404s there — fixed in `.env`,
   `.env.example`, `docs/META_API_DEMO.md`, `app/main.py` fallback). Strict json_schema is
   accepted. Results merged in `evals/RESULTS.md`:
   - muse-spark-1.3 (api.meta.ai): accuracy 1.00, 834/834 checks, 25/25 shortfalls, 22.5 s/case, 113,633 tokens.
   - muse-spark-open (Baseten): accuracy 0.94, 834/834, 25/25, 15.9 s/case, 168,237 tokens.
   - Injection case safe on both. 3 open cases first hit the 30 s client read timeout
     (en-basic, hi-basic, hi-tight), all passed 1.00 on one re-run (raw runs in
     `evals/RESULTS_OPEN.md`, `evals/results_open_retry.json`). Only misses: open model
     duplicated a requirement in es/pa/vi blackout cases (0.56 rule accuracy, no
     placement effect). `.env` already holds working Baseten + Meta keys — do not print.
4. **Fresh-clone verification done.** Clean clone (committed HEAD): 182 pytest green;
   mock 27 events 162/162; live muse-spark-1.3 27 events 162/162 (13.0 s, 2,493 tokens).
   Caveat: from-scratch `pip install` could not run in that sandbox (PyPI unreachable) —
   **re-run the install step once on a normal network before submitting.** Scratch clone
   was deleted. Fixed broken README link to the gitignored judging file.

## Current git state

- HEAD `7d79d08` ("Demo verification") contains Steps 1–3 + judging-guidance docs.
- One uncommitted change (user's): `README.md` adds `.venv\Scripts\python -m pip install pytest`
  to the setup commands — note `pytest` is not in `pyproject.toml` deps/`[app]` extra,
  so that line (or adding pytest to an extra) is required for judges to run tests.

## Remaining steps (in order)

5. **Impact without customers** (user-led): seed demo from SquadRoot demo clubs; get 3–5
   coaches/managers/parents to run the demo 15 min and quote reactions (labelled feedback);
   time one manual schedule + one rain-out re-plan for an on-camera before/after number.
6. **Rehearse hero flow** (Nov 1–2): injected/bad request caught red → Spanish voice note →
   schedule photo import → rain-out moves only hit sessions → notices in 3 languages →
   Approve → eval table (both models) on screen. Must fit 3 min; use v4.3 fast routes
   (deterministic English summaries, coding fast-path routing, insert-only adds).
7. **Video Nov 3** per `docs/PLAN.md` outline ("Video outline (3:00)"). Open on the caught
   failure, not an intro. Captions on.
8. **Submit Nov 4 morning:** re-run fresh-clone install on normal network; repo public and
   links/video open logged out; contributors listed; submit early, then post gallery link.

Cut order if time slips: new features → extra eval languages → video polish.
Never cut: fresh-clone live run, video, tests.

## Resume prompt for a new session

"Read docs/SESSION_HANDOFF.md, AGENTS.md, docs/PLAN.md and docs/JUDGING_EVALUATION.md.
Continue from Remaining steps; next is Step 5/6 unless I say otherwise. Run pytest before
reporting done; leave changes uncommitted unless I say commit."
