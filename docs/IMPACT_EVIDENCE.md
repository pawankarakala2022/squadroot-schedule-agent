# Impact evidence kit (Step 5 — user-led)

Purpose: collect real impact evidence without inventing customers. No real
clubs use this yet, so nothing in this file is evidence until a session
below actually happens and is filled in. Never copy a filled row into the
README or the video without the person's permission (last column).

Label everything collected here as **feedback sessions on fictional demo
data**, never as customers, pilots, or testimonials.

## 1. Seed the demo from SquadRoot demo clubs (shape only)

Copy structure only from a SquadRoot demo club into `demo/club.json`.
Teams, fields, and season shape may carry over; nothing about people may.

| SquadRoot demo club | `demo/club.json` field | Carry over? |
|---|---|---|
| Team display name | `teams[].name` | Yes (demo clubs only) |
| Team id | `teams[].id` | Replace with `t1`, `t2`, … |
| Field display name + hours | `fields[].name`, `open_from`, `open_until` | Yes |
| Field id | `fields[].id` | Replace with `f1`, `f2`, … |
| Season dates + timezone | `season_start`, `season_end`, `timezone` | Yes |
| Family languages per team | `teams[].languages` | Counts/codes only |
| Player, parent, contact, payment data | — | **Never** |
| Coach name or contact | — | **Never** (coach = busy intervals only) |

After seeding, re-run the demo presets and `python -m pytest` before
using the club in a session. Keep the note "Fictional demo data" in place
unless every name is from a SquadRoot demo club and still fictional.

## 2. Fifteen-minute feedback session (3–5 people)

Recruit volunteer coaches, managers, or parents. Target mix: at least one
person who currently makes a club schedule by hand. Run on the demo club
(mock mode is fine; say so). Do not lead: read the tasks, then stay quiet.

| Minute | Do this |
|---|---|
| 0–2 | "This drafts schedules and checks them; a human approves. The data is fictional." Show nothing else. |
| 2–7 | Tasks T1–T3 below, one at a time. Time them. Note every hesitation. |
| 7–12 | "Rain Thursday, North Field closed — fix it." Then free explore. |
| 12–15 | Ask Q1–Q4 verbatim. Write answers down word for word. |

Tasks (read aloud, do not paraphrase):

- T1: "Get a season practice schedule for both teams."
- T2: "Move only the sessions hit by rain on Thursday Nov 12, North closed."
- T3: "Draft the family notices for the change."

Questions (ask in this order, record verbatim):

- Q1: "How do you do this today, and how long does it take?"
- Q2: "What part of what you just saw would you trust least?"
- Q3: "What would have to be true for your club to use this?"
- Q4: "May we quote you? With what name and role?"

Capture log (one row per person; leave blank until a session happens):

| Date | Role (coach/manager/parent) | Name as approved for quoting | Tasks done (T1–T3, with times) | Q1 answer (verbatim) | Best verbatim quote | Permission to quote? (yes/no + exact attribution) |
|---|---|---|---|---|---|---|
| | | | | | | |

Rules:

- A quote without the last column filled is not usable anywhere.
- Paraphrases go in notes, never inside quotation marks.
- If someone declines, record "declined" and use nothing from them.

## 3. Manual before/after timing (the on-camera number)

Same fixture for both sides: the demo club (2 teams, 2 fields,
Oct 1–Dec 31, coach unavailable Wednesdays 17:00–20:00). The person
timing must not have rehearsed the fixture.

Manual run (stopwatch, spreadsheet or paper — the person's usual tool):

1. Start the clock when the plan request (the demo plan preset text) is
   revealed. Stop when the schedule is conflict-free **and** the person
   has checked: no field double-booking, no team overlap, inside field
   hours, no blackout dates, not over the coach's Wednesday busy time.
2. Rain re-plan: reveal "Rain Thursday Nov 12, North Field is closed."
   Start the clock. Stop when only the hit sessions have moved and the
   same checks pass again.

Agent run (same day, same fixture, live Meta API mode):

1. Time from pressing the run button to the checked proposal on screen.
   Also record the trace `latency_ms` values (model time vs solve/check
   time) so the claim separates waiting from computing.
2. Rain: time the reschedule the same way.

Results (leave blank until measured; report medians if run more than once):

| Run | Manual time | Errors caught only on re-check | Agent wall-clock | Agent trace model ms | Agent solve+check ms |
|---|---|---|---|---|---|
| Season schedule | | | | | |
| Rain re-plan | | | | | |

Claim rule: the video and README may only say "a volunteer took X to do
this by hand; the agent drafted it in Y, with every session checked" —
with X and Y from this table, the fixture named, and the session labelled
as a feedback session. No table, no number.

## 4. What Step 5 produces (checklist)

- [ ] Demo club seeded from a SquadRoot demo club (shape only) or kept as-is
- [ ] 3–5 feedback sessions logged above, permissions recorded
- [ ] Manual + agent timing rows filled from real stopwatch runs
- [ ] Usable quotes selected (permission column = yes for each)
- [ ] README/video claims updated to match this file — nothing stronger
