# Changelog

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
