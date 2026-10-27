# Demo with Meta API Muse Spark

How to run the demo app against the live Meta Muse Spark model.
Mock mode needs no key. Use this file when you want real model answers.

## What Muse Spark does here

The model never picks times. A deterministic solver places events.

- `muse-spark-1.3` (Meta Model API): request to `Rules`, rain request to
  `Disruption`, cancel request to `CancelFilter`, schedule photo to games,
  plan to summary, family notices.
- `muse-voice-transcribe-1.0`: voice note bytes to request text.
- `meta-models/muse-spark-open-1026` (Baseten): same agent on open weights.

Code: [llm.py](../schedule_agent/llm.py), [voice.py](../schedule_agent/voice.py),
[vision.py](../schedule_agent/vision.py), [demo app](../app/main.py).

## 1. Get a key

1. Go to `dev.meta.ai`, create an API key.
2. Use a spend-limited key for demos.

## 2. Configure `.env`

```powershell
Copy-Item .env.example .env
```

Edit `.env`:

```text
META_MODEL_API_BASE=https://api.meta.ai/v1
MODEL_API_KEY=YOUR_KEY_HERE
META_MODEL=muse-spark-1.3
META_TRANSCRIBE_MODEL=muse-voice-transcribe-1.0
AGENT_MODE=mock
```

For the open-weights switch, also fill in:

```text
OPEN_MODEL_API_BASE=https://...
OPEN_MODEL_API_KEY=YOUR_OPEN_KEY_HERE
OPEN_MODEL=meta-models/muse-spark-open-1026
```

Rules:

- Never use a model id ending in `-contributor`. The client refuses it.
- Never commit `.env`. It is gitignored.
- Never print or log the key. Provider errors say `Assistant unavailable`.

## 3. Install and test

```powershell
.venv\Scripts\python -m pip install -e ".[app]"
.venv\Scripts\python -m pytest
.venv\Scripts\python -m app
```

Then open `http://127.0.0.1:8000`.

## 4. Use it in the demo page

1. Set the `Model` dropdown to `Meta API (muse-spark)`.
2. Click `Plan preset`, then `Plan`.
3. Check the `Steps (trace)`: it shows the model id, latency and tokens.
4. Try `Revise draft`, the `Rain demo` buttons, and `Draft family notices`.
5. Click `Approve` last. Approval only records agreement. The agent applies nothing.

Mock mode answers are labelled `Mock mode: ...`.
If the trace says `muse-spark-1.3`, the call was live.

## 5. Voice and photo

- Voice: record with [record_voice_note.html](../record_voice_note.html).
  Send 16 kHz mono WAV. Phone `.m4a` is rejected by the server.
  In `Meta API` mode the page calls `POST /api/voice`, which runs
  `transcribe_audio` (`POST {base}/asr/transcribe`).
- Photo: upload a PNG or JPEG of the league schedule.
  PDF pages must be converted to images first (max 10 MB).
  In `Meta API` mode the page calls `POST /api/vision`, which runs
  `extract_games`. Unmatched team or field names are kept with
  `matched=false` and a note. They are never guessed.

## 6. Demo script (fictional Riverside club)

- Plan: `Foxes practice twice a week, 60 minutes, Mondays and Wednesdays after 5pm. Badgers practice once a week, 60 minutes, Thursdays after 5pm.`
- Rain: `Rain Thursday November 12, North Field is closed.`
- Cancel: `Cancel all Thursday sessions.`
- Expected: checks show green chips, shortfalls list what did not fit,
  rain moves only hit sessions as `old time/field -> new time/field`.

## 7. `.env` not picked up? (fixed)

The library reads only `os.environ`. The demo app in [main.py](../app/main.py)
now loads allowlisted keys from `.env` on every `meta` / `open` / voice call,
without overriding real environment variables. So editing `.env` works without
restarting `python -m app`. If you still see
`Meta API mode needs MODEL_API_KEY in .env`, check the key is on one line as
`MODEL_API_KEY=...` with no quotes needed.

## 8. If it fails

| Symptom | Fix |
|---|---|
| `Meta API mode needs MODEL_API_KEY in .env` | Fill `MODEL_API_KEY`, restart `python -m app`. |
| `Assistant unavailable` | Check key, quota, network. Mock mode still works. |
| `That WAV recording won't transcribe` | Re-record mono 16-bit at 16 or 24 kHz. |
| `Malformed request` on voice | You sent `.m4a`/`.mp3`. Send WAV. |
| `Please send a photo` on vision | Convert PDF page to PNG/JPEG first. |
| `Unknown mode` | Use `mock`, `meta` or `open`. |

## 8. Cost hint

One live Phase 3 smoke test used 249 total tokens.
One 25-case eval run on `muse-spark-1.3` used about 121k tokens.
Watch the `trace` token counts during your demo.
