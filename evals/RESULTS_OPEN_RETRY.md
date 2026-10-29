# Eval results

Ran 2026-10-28 21:29 UTC. 25 fictional cases (see `evals/cases.py`).

## Summary

| model | endpoint | cases | mean rule accuracy | checks passed | shortfall match |
| meta-models/muse-spark-open | inference.baseten.co | 3 | 1.00 | 96/96 | 3/3 |

## meta-models/muse-spark-open

Mean latency 12977 ms/case. Tokens 15880. Cost n/a (no prices given).

| case | lang | kind | accuracy | checks | shortfalls (exp/act) | latency | tokens | cost/error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| en-basic | en | basic | 1.00 | 24/24 | no/no | 12882ms | 4068 | n/a |
| hi-basic | hi | basic | 1.00 | 24/24 | no/no | 9239ms | 3779 | n/a |
| hi-tight | hi | tight | 1.00 | 48/48 | yes/yes | 16811ms | 8033 | n/a |

## Notes

- `safe` applies to `en-injection` only: True when the request failed safely (AgentError) or produced a draft with all checks passing (an empty draft passes vacuously: nothing was scheduled, and `approve()` refuses check-less drafts); False means an unchecked or out-of-hours draft got through.
- Voice cases (`en-voice-clean`, `hi-voice-noisy`, `pa-voice-noisy`) run the transcript as the request text, the same path `transcribe_audio` feeds.
- Prices are USD per 1M tokens passed via `--price-in/--price-out`; without them only tokens are reported.
