# Changelog

## v4.2.0 (2026-10-28)

Additive, backward compatible (no existing name or shape changed; new
keyword arguments default to prior behavior): the dynamic layer.
Code-resolved relative dates (`dates.py`: `today_in_tz`,
`relative_date_hints`, `problem_date_hints`) now anchor plan, revise,
reschedule, cancel and routing prompts, so "next Tuesday / next week /
next month" copy concrete dates in any language. `ScheduleAgent`
methods accept `language="en"` and thread it to understanding,
routing, disruption, cancel and explain; team and field names stay
English proper names, never translated, in every prompt and summary.
US general holidays (`holidays.py`: `us_federal_holidays`,
`holiday_dates`, `with_us_holidays`) merge into blackout dates in
code. Coach unavailability by text or voice transcript
(`availability.py`: `AvailabilityParse`, `parse_availability`) expands
to `Busy` intervals (unavailability only, no coach entity). Grounds
and coach photos/screenshots (`vision.py`: `GroundAvailability`,
`CoachUnavailability`, `AvailabilityImport`, `extract_availability`,
`availability_to_busy`) read "field available Tue/Thu 5-8pm" notices
with exact matching, never guessed. Router rule: rain or a closed
field with no named move target now routes to cancel; reschedule
needs a named new date or field (explicit `reschedule()` unchanged).

## v4.1.0 (2026-10-28)

Additive, backward compatible (no existing name or shape changed): one
entry point for any wording. `Route` + `parse_route_intent(request,
chat)` (model picks plan / revise / reschedule / cancel from the request
plus has_proposal / has_calendar context; strict json_schema, one repair
retry) and `ScheduleAgent.handle(problem, request, *, proposal=None,
events=None, previous=None, verify=None, ...)`: routes then dispatches to
the matching call. Cancel/reschedule with no calendar fail safely ("no
calendar yet"); revise with no draft falls back to plan. Demo: `POST
/api/run` plus a Smart Run button (streamed steps included); mock mode
reads explicit dates/teams/fields out of the request so moves like
10/29 -> 10/30 work offline.

## v4.0.0 (2026-10-27)

Breaking (Requirement shape grew, old dicts still parse): event-planner
dates. `Requirement` gains `active_from` / `active_until` (YYYY-MM-DD,
empty means unset, JSON round-trip; dicts without the keys parse as
unbounded). The solver only places dates inside the window and skips
out-of-scope weeks with no shortfall, so "next Tuesday" is one session
on that date instead of every Tuesday. `understand_request` anchors
relative dates to today in the problem timezone, tells the model a game
is a one-off fixture while a dateless practice recurs all season, and
validates the window (bad or empty ranges dropped and reported). The
agent's previous-rules context renders the window so revises keep it.

## v3.2.0 (2026-10-27)

Additive, backward compatible: cancellations. `CancelFilter` (team /
field / weekday / date-range / session-type allow-lists with `matches`
in the problem timezone, JSON round-trip) plus `parse_cancel_filter`
(model report to validated filter: T/F aliases, unknown ids dropped
and reported, unselective filters rejected, one repair retry) and
`ScheduleAgent.cancel(problem, events, request)`: removes only matched
sessions, kept sessions stay exactly where they are, checks run once
on the remainder, the summary lists each cancellation. A request
matching nothing fails safely with `AgentError`; approval still goes
through `approve()`. No existing name or shape changed.

## v3.1.0 (2026-10-27)

Additive, backward compatible: Phase 6 family notices.
`FamilyNotice` / `FamilyNotices` (one draft per requested language with
a per-draft verified flag and missing-fact list, JSON round-trip) plus
`draft_notices(problem, events, chat, *, languages=[...])`: the model
words one short notice per language from the approved sessions, then
code verifies every draft echoes each team name, date (YYYY-MM-DD),
start/end time (HH:MM) and field name exactly, even in non-Latin
scripts. One repair retry listing the missing facts, then anything
still missing is reported in notes for the human, never silently sent.
Empty language tags, duplicates and unknown model rows are dropped and
reported; at most 10 languages per call. No existing name or shape
changed.

## v3.0.0 (2026-10-26)

Breaking (new public names; no existing shape changed): Phase 5
multimodal input. `VoiceConfig` + `transcribe_audio(config, audio_bytes)`
send a voice note to the Meta transcription model and return request
text for the same agent loop. `GameImport` / `ScheduleImport` +
`extract_games(problem, image_bytes, chat)` read league games from a
schedule photo (strict json_schema, exact name matching, unmatched rows
kept and reported, never guessed) and `games_to_busy(...)` turns matched
games into Busy intervals so practices fit around them. Demo fixtures in
`demo/` (all fictional).

## v2.1.0 (2026-10-26)

Additive, backward compatible (old Disruption dicts still parse,
existing calls unchanged): move date/ground options for rescheduling.
`Disruption.not_before` (model parses "move to next week" and similar
timing words; empty means unset) pushes the solver search window later.
`ScheduleAgent.reschedule()` gains host pins `not_before=` (a date or
YYYY-MM-DD string; latest of model/host wins) and `allowed_fields=`
(unknown ids dropped and reported; empty means any open ground). Pins
are reported in the proposal notes; an inverted window yields named
shortfalls instead of an error. Coach stays a time dimension: moved
times must fit coach availability via Problem.busy and the verify hook.

## v2.0.0 (2026-10-26)

Breaking (new public names; no existing shape changed): Phase 4b rain-out
rescheduling. `Disruption` (dates, closed field ids with empty meaning
all, `search_days`, notes, JSON round-trip) plus `parse_disruption`
(model report to validated `Disruption`: F-aliases, unknown fields
dropped and reported, one repair retry) and
`ScheduleAgent.reschedule(problem, events, request, *, verify=None)`:
only hit sessions move (duration, type, title kept), kept sessions are
frozen, closed fields get whole-day busy, the summary lists each move as
old time/field -> new time/field, and shortfalls name unmovable
sessions. No existing name or shape changed.

## v1.0.0 (2026-10-26)

Breaking (new public names; no existing shape changed): added the Phase 4
approval gate. `ScheduleAgent.approve(proposal)` is the only path to an
`ApplyResult` (`approved=True`), granted only for drafts whose check
results all passed. The agent still applies nothing: the host saves the
approved events through its own permission and conflict checks.
