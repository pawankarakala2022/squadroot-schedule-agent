# Build log

One line per piece of work: date (ET), what was built, who / what wrote it.

- 2026-10-26: Repo created after the kickoff livestream. README, AGENTS.md, build plan, license, env example. (Claude Code)
- 2026-10-26: Stable library interface for host apps added to AGENTS.md. (Claude Code)
- 2026-10-26: Phase 1 built: schedule_agent models/solver, pyproject, 13 offline pytest tests green. (Muse Code)
- 2026-10-26: Removed 17 pip temp leftovers (pip-build-tracker-*, pip-ephem-wheel-cache-*, pip-install-*, tmpvy5_b31y, tmppimy2yeicacert.pem) from repo root; 13 pytest tests still green. (Muse Code)
- 2026-10-26: Solver fairness rotation (hungriest requirement first each week), Rules.blackout_dates/notes + Requirement.title with round-trip, Proposal.shortfall_summary(), pip-/tmp- gitignores; 20 pytest tests green. (Muse Code)
- 2026-10-26: Phase 2 built: schedule_agent/checks.py (6 named checks, one result per check per event) plus tests where each check catches its own bad event and solver output passes all; 28 pytest tests green. (Muse Code)
