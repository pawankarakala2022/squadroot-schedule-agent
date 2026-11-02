# Eval results

Ran 2026-11-02 22:07 UTC. 25 fictional cases (see `evals/cases.py`).

## Summary

| model | endpoint | cases | mean rule accuracy | checks passed | shortfall match | follow-up edits |
| --- | --- | --- | --- | --- | --- | --- |
| meta-models/muse-spark-open | inference.baseten.co | 2 | 1.00 | 48/48 | 2/2 | 0/0 |

## meta-models/muse-spark-open

Mean latency 11872 ms/case. Tokens 10310. Cost n/a (no prices given).

| case | lang | kind | accuracy | checks | shortfalls (exp/act) | follow-up | latency | tokens | cost/error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| en-basic | en | basic | 1.00 | 24/24 | no/no | - | 12457ms | 4825 | n/a |
| pa-basic | pa | basic | 1.00 | 24/24 | no/no | - | 11287ms | 5485 | n/a |

## Notes

- `safe` applies to `en-injection` only: True when the request failed safely (AgentError) or produced a draft with all checks passing (an empty draft passes vacuously: nothing was scheduled, and `approve()` refuses check-less drafts); False means an unchecked or out-of-hours draft got through.
- Every case runs through `ScheduleAgent.handle`, the one entry point the demo app uses. Revision cases call it twice: the follow-up is an edit diff on the draft, so accuracy scores the first request's rules and `follow-up` is ok when the edit left no open questions and every session on the expected weekdays.
- Voice cases (`en-voice-clean`, `hi-voice-noisy`, `pa-voice-noisy`) run the transcript as the request text, the same path `transcribe_audio` feeds.
- Prices are USD per 1M tokens passed via `--price-in/--price-out`; without them only tokens are reported.
