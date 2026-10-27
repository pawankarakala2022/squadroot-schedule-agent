# Changelog

## v1.0.0 (2026-10-26)

Breaking (new public names; no existing shape changed): added the Phase 4
approval gate. `ScheduleAgent.approve(proposal)` is the only path to an
`ApplyResult` (`approved=True`), granted only for drafts whose check
results all passed. The agent still applies nothing: the host saves the
approved events through its own permission and conflict checks.
