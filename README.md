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

## Checking a voice request end to end

`manual_phase5_check.py` is the scratch manual check (untracked, repo root):

```powershell
.venv\Scripts\python manual_phase5_check.py                      # offline fakes
.venv\Scripts\python manual_phase5_check.py --live               # real models
.venv\Scripts\python manual_phase5_check.py --live --voice NOTE.wav  # your recording
```

Record with `record_voice_note.html` (16 kHz mono WAV, the only format the
transcription endpoint accepted in our live tests; phone `.m4a` is rejected).
Reading the output:

- `live transcript:` is your voice transcribed. Proof: it changes with each
  recording. The script prints it but feeds it nowhere: turning "add a game"
  speech into a created event is not built yet. Unknown teams (e.g. Falcons)
  are dropped and reported, and one-off games are not expressible in `Rules`,
  which only describes recurring sessions — so today that transcript creates
  nothing. An empty `REQUIREMENTS` list plus explanatory `notes` is the
  expected, safe result.
- `offline voice (empty audio)` is a deliberate self-test that feeds zero
  bytes to prove empty input fails safely. It never touches your file.

## Two model clients, how to tell them apart

- Chat (`schedule_agent/llm.py`, `chat_fn` → `POST /chat/completions`):
  used by understand, explain, and vision.
- Voice (`schedule_agent/voice.py`, `VoiceConfig` → `POST /asr/transcribe`
  with `request` + `audio` parts, `transcript` in the reply): used only by
  `transcribe_audio`.

Verify: pass `usage_out={}` — voice fills `muse-voice-transcribe-1.0`,
chat fills `muse-spark-1.3`. Or break one key: the other path keeps working.

## License

MIT. See `LICENSE`.
