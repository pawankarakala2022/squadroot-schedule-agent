# SquadRoot Schedule Agent

A club operations agent for volunteer-run community sports clubs, built on Meta's Muse models.

A club manager describes scheduling work in plain words, by voice, or with a photo of the league's
game schedule, in any language. The agent drafts a conflict-free schedule, proves every event passes
its checks, explains the result, drafts the family notice in each family's language, and waits for a
human to approve before anything changes.

> Status: in progress for the Meta Global AI Developer Hackathon (Oct 26 - Nov 4, 2026).

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

Coming soon (one command, with a mock mode that needs no API key).

## License

MIT. See `LICENSE`.
