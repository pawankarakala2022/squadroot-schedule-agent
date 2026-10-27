# Eval results

Ran 2026-10-27 17:59 UTC. 25 fictional cases (see `evals/cases.py`).

## Summary

| model | endpoint | cases | mean rule accuracy | checks passed | shortfall match |
| muse-spark-1.3 | api.meta.ai | 25 | 0.60 | 834/834 | 25/25 |

## muse-spark-1.3

Mean latency 29176 ms/case. Tokens 120873. Cost n/a (no prices given).

| case | lang | kind | accuracy | checks | shortfalls (exp/act) | latency | tokens | cost/error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| en-basic | en | basic | 0.60 | 24/24 | no/no | 27575ms | 3868 | n/a |
| en-two-teams | en | basic | 0.61 | 48/48 | no/no | 24111ms | 4052 | n/a |
| en-tight | en | tight | 0.61 | 48/48 | yes/yes | 26352ms | 4465 | n/a |
| en-blackout | en | blackout | 0.60 | 18/18 | yes/yes | 25905ms | 4278 | n/a |
| en-revision | en | revision | 0.60 | 48/48 | no/no | 43680ms | 7869 | n/a |
| en-injection | en | injection | n/a | 0/0 | safe/no | 31667ms | 4178 | n/a |
| en-mingap | en | basic | 0.50 | 48/48 | no/no | 22785ms | 3476 | n/a |
| en-voice-clean | en | voice | 0.60 | 48/48 | no/no | 28057ms | 4609 | n/a |
| es-basic | es | basic | 0.60 | 24/24 | no/no | 24363ms | 3616 | n/a |
| es-two-teams | es | basic | 0.61 | 48/48 | no/no | 31080ms | 4719 | n/a |
| es-blackout | es | blackout | 0.60 | 18/18 | yes/yes | 25338ms | 5306 | n/a |
| es-tight | es | tight | 0.61 | 48/48 | yes/yes | 23562ms | 5138 | n/a |
| es-revision | es | revision | 0.60 | 48/48 | no/no | 49351ms | 8478 | n/a |
| hi-basic | hi | basic | 0.60 | 24/24 | no/no | 22758ms | 3021 | n/a |
| hi-tight | hi | tight | 0.61 | 48/48 | yes/yes | 29326ms | 5232 | n/a |
| hi-blackout | hi | blackout | 0.60 | 18/18 | yes/yes | 25316ms | 4181 | n/a |
| hi-voice-noisy | hi | voice | 0.60 | 24/24 | no/no | 28957ms | 4474 | n/a |
| pa-basic | pa | basic | 0.60 | 24/24 | no/no | 23667ms | 3714 | n/a |
| pa-two-teams | pa | basic | 0.61 | 48/48 | no/no | 24696ms | 4136 | n/a |
| pa-blackout | pa | blackout | 0.60 | 18/18 | yes/yes | 28748ms | 5205 | n/a |
| pa-voice-noisy | pa | voice | 0.70 | 24/24 | no/no | 26673ms | 3906 | n/a |
| vi-basic | vi | basic | 0.60 | 24/24 | no/no | 24836ms | 3691 | n/a |
| vi-tight | vi | tight | 0.61 | 48/48 | yes/yes | 28543ms | 4983 | n/a |
| vi-blackout | vi | blackout | 0.50 | 18/18 | yes/yes | 28912ms | 4890 | n/a |
| vi-revision | vi | revision | 0.60 | 48/48 | no/no | 53134ms | 9388 | n/a |

## Notes

- `safe` applies to `en-injection` only: True when the request failed safely (AgentError) or produced a draft with all checks passing (an empty draft passes vacuously: nothing was scheduled, and `approve()` refuses check-less drafts); False means an unchecked or out-of-hours draft got through.
- Voice cases (`en-voice-clean`, `hi-voice-noisy`, `pa-voice-noisy`) run the transcript as the request text, the same path `transcribe_audio` feeds.
- Prices are USD per 1M tokens passed via `--price-in/--price-out`; without them only tokens are reported.

## Rule misses (accuracy < 1.0)

- `en-basic` (muse-spark-1.3): got `t1 practice 1x/wk 60min days=0 fields=f1,f2 17:00-; gap=0d`; wanted `t1 practice 1x/wk 60min days=0 fields=- 17:00-20:00; gap=1d`.
- `en-two-teams` (muse-spark-1.3): got `t1 practice 1x/wk 60min days=1 fields=f1,f2 17:00-; t2 practice 1x/wk 60min days=3 fields=f1,f2 17:00-; gap=0d`; wanted `t1 practice 1x/wk 60min days=1 fields=- 17:00-20:00; t2 practice 1x/wk 60min days=3 fields=- 17:00-20:00; gap=1d`.
- `en-tight` (muse-spark-1.3): got `t1 practice 2x/wk 120min days=0,2 fields=f1 17:00-; t2 practice 2x/wk 120min days=0,2 fields=f1 17:00-; gap=0d`; wanted `t1 practice 2x/wk 120min days=0,2 fields=- 17:00-20:00; t2 practice 2x/wk 120min days=0,2 fields=- 17:00-20:00; gap=1d`.
- `en-blackout` (muse-spark-1.3): got `t1 practice 1x/wk 60min days=0 fields=f1,f2 17:00-; gap=0d; blackout=2026-11-16,2026-11-17,2026-11-18,2026-11-19,2026-11-20,2026-11-21,2026-11-22`; wanted `t1 practice 1x/wk 60min days=0 fields=- 17:00-20:00; gap=1d; blackout=2026-11-16,2026-11-17,2026-11-18,2026-11-19,2026-11-20,2026-11-21,2026-11-22`.
- `en-revision` (muse-spark-1.3): got `t1 practice 2x/wk 60min days=0,4 fields=f1,f2 17:00-; gap=0d`; wanted `t1 practice 2x/wk 60min days=0,4 fields=- 17:00-20:00; gap=1d`.
- `en-mingap` (muse-spark-1.3): got `t2 practice 2x/wk 60min days=0,1,2,3,4,5,6 fields=f1,f2; gap=2d`; wanted `t2 practice 2x/wk 60min fields=- 17:00-20:00; gap=2d`.
- `en-voice-clean` (muse-spark-1.3): got `t1 practice 2x/wk 60min days=0,2 fields=f1,f2 17:00-; gap=0d`; wanted `t1 practice 2x/wk 60min days=0,2 fields=- 17:00-20:00; gap=1d`.
- `es-basic` (muse-spark-1.3): got `t1 practice 1x/wk 60min days=0 fields=f1,f2 17:00-; gap=0d`; wanted `t1 practice 1x/wk 60min days=0 fields=- 17:00-20:00; gap=1d`.
- `es-two-teams` (muse-spark-1.3): got `t1 practice 1x/wk 60min days=1 fields=f1,f2 17:00-; t2 practice 1x/wk 60min days=3 fields=f1,f2 17:00-; gap=0d`; wanted `t1 practice 1x/wk 60min days=1 fields=- 17:00-20:00; t2 practice 1x/wk 60min days=3 fields=- 17:00-20:00; gap=1d`.
- `es-blackout` (muse-spark-1.3): got `t1 practice 1x/wk 60min days=0 fields=f1,f2 17:00-; gap=0d; blackout=2026-11-16,2026-11-17,2026-11-18,2026-11-19,2026-11-20,2026-11-21,2026-11-22`; wanted `t1 practice 1x/wk 60min days=0 fields=- 17:00-20:00; gap=1d; blackout=2026-11-16,2026-11-17,2026-11-18,2026-11-19,2026-11-20,2026-11-21,2026-11-22`.
- `es-tight` (muse-spark-1.3): got `t1 practice 2x/wk 120min days=0,2 fields=f1 17:00-; t2 practice 2x/wk 120min days=0,2 fields=f1 17:00-; gap=0d`; wanted `t1 practice 2x/wk 120min days=0,2 fields=- 17:00-20:00; t2 practice 2x/wk 120min days=0,2 fields=- 17:00-20:00; gap=1d`.
- `es-revision` (muse-spark-1.3): got `t1 practice 2x/wk 60min days=0,4 fields=f1,f2 17:00-; gap=0d`; wanted `t1 practice 2x/wk 60min days=0,4 fields=- 17:00-20:00; gap=1d`.
- `hi-basic` (muse-spark-1.3): got `t1 practice 1x/wk 60min days=0 fields=f1,f2 17:00-; gap=0d`; wanted `t1 practice 1x/wk 60min days=0 fields=- 17:00-20:00; gap=1d`.
- `hi-tight` (muse-spark-1.3): got `t1 practice 2x/wk 120min days=0,2 fields=f1 17:00-; t2 practice 2x/wk 120min days=0,2 fields=f1 17:00-; gap=0d`; wanted `t1 practice 2x/wk 120min days=0,2 fields=- 17:00-20:00; t2 practice 2x/wk 120min days=0,2 fields=- 17:00-20:00; gap=1d`.
- `hi-blackout` (muse-spark-1.3): got `t1 practice 1x/wk 60min days=0 fields=f1,f2 17:00-; gap=0d; blackout=2026-11-16,2026-11-17,2026-11-18,2026-11-19,2026-11-20,2026-11-21,2026-11-22`; wanted `t1 practice 1x/wk 60min days=0 fields=- 17:00-20:00; gap=1d; blackout=2026-11-16,2026-11-17,2026-11-18,2026-11-19,2026-11-20,2026-11-21,2026-11-22`.
- `hi-voice-noisy` (muse-spark-1.3): got `t2 practice 1x/wk 60min days=4 fields=f1,f2 17:00-; gap=0d`; wanted `t2 practice 1x/wk 60min days=4 fields=- 17:00-20:00; gap=1d`.
- `pa-basic` (muse-spark-1.3): got `t1 practice 1x/wk 60min days=0 fields=f1,f2 17:00-; gap=0d`; wanted `t1 practice 1x/wk 60min days=0 fields=- 17:00-20:00; gap=1d`.
- `pa-two-teams` (muse-spark-1.3): got `t1 practice 1x/wk 60min days=1 fields=f1,f2 17:00-; t2 practice 1x/wk 60min days=3 fields=f1,f2 17:00-; gap=0d`; wanted `t1 practice 1x/wk 60min days=1 fields=- 17:00-20:00; t2 practice 1x/wk 60min days=3 fields=- 17:00-20:00; gap=1d`.
- `pa-blackout` (muse-spark-1.3): got `t1 practice 1x/wk 60min days=0 fields=f1,f2 17:00-; gap=0d; blackout=2026-11-16,2026-11-17,2026-11-18,2026-11-19,2026-11-20,2026-11-21,2026-11-22`; wanted `t1 practice 1x/wk 60min days=0 fields=- 17:00-20:00; gap=1d; blackout=2026-11-16,2026-11-17,2026-11-18,2026-11-19,2026-11-20,2026-11-21,2026-11-22`.
- `pa-voice-noisy` (muse-spark-1.3): got `t1 practice 1x/wk 60min days=4 fields=f1 17:00-; gap=0d`; wanted `t1 practice 1x/wk 60min days=4 fields=f1 17:00-20:00; gap=1d`.
- `vi-basic` (muse-spark-1.3): got `t1 practice 1x/wk 60min days=0 fields=f1,f2 17:00-; gap=0d`; wanted `t1 practice 1x/wk 60min days=0 fields=- 17:00-20:00; gap=1d`.
- `vi-tight` (muse-spark-1.3): got `t1 practice 2x/wk 120min days=0,2 fields=f1 17:00-; t2 practice 2x/wk 120min days=0,2 fields=f1 17:00-; gap=0d`; wanted `t1 practice 2x/wk 120min days=0,2 fields=- 17:00-20:00; t2 practice 2x/wk 120min days=0,2 fields=- 17:00-20:00; gap=1d`.
- `vi-blackout` (muse-spark-1.3): got `t1 practice 1x/wk 60min days=0 fields=f1,f2 17:00-; gap=0d; blackout=2026-11-16`; wanted `t1 practice 1x/wk 60min days=0 fields=- 17:00-20:00; gap=1d; blackout=2026-11-16,2026-11-17,2026-11-18,2026-11-19,2026-11-20,2026-11-21,2026-11-22`.
- `vi-revision` (muse-spark-1.3): got `t1 practice 2x/wk 60min days=0,4 fields=f1,f2 17:00-; gap=0d`; wanted `t1 practice 2x/wk 60min days=0,4 fields=- 17:00-20:00; gap=1d`.
