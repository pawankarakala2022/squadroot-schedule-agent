# Changelog

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
