# Demo fixtures (all fictional: no real clubs, families or people)

- `schedule.png`: sample league game schedule photo (Riverside Youth League,
  Fall 2026). Four games; only Foxes/Badgers teams and the North/South fields
  match the demo club, the rest exercise the "reported, never guessed" path.
  Made with Windows built-in drawing (no new dependencies).
- `voice_note.txt`: transcript of the sample voice note. The transcription
  endpoint only accepts WAV (mono 16-bit, 16/24 kHz) in our live tests, so
  record with `record_voice_note.html` at the repo root (phone `.m4a` files
  are rejected by the server) and pass the file via `--voice`. Never commit
  recordings: they are gitignored.
- `voice_16k.wav` (valid), `voice_8k.wav` / `voice_stereo_16k.wav`
  (rejected formats), `schedule_tiny.png`: tiny format fixtures for the
  offline test suite. The wavs are sine tones, not speech: they exercise
  upload validation, never transcription quality.
- `family_languages.json`: languages per team (Foxes: es/en, Badgers:
  en/vi) for Phase 6 family notices. Names only, no people.
