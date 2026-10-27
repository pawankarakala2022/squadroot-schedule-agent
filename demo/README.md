# Demo fixtures (all fictional: no real clubs, families or people)

- `schedule.png`: sample league game schedule photo (Riverside Youth League,
  Fall 2026). Four games; only Foxes/Badgers teams and the North/South fields
  match the demo club, the rest exercise the "reported, never guessed" path.
  Made with Windows built-in drawing (no new dependencies).
- `voice_note.txt`: transcript of the sample voice note. Record it yourself
  with any phone recorder (or speak it into the demo app in Phase 8) and send
  the audio bytes to `transcribe_audio`. This sandbox has no usable text to
  speech voices, so no recorded sample is committed.
- `voice_16k.wav` (valid), `voice_8k.wav` / `voice_stereo_16k.wav`
  (rejected formats), `schedule_tiny.png`: tiny format fixtures for the
  offline test suite. The wavs are sine tones, not speech: they exercise
  upload validation, never transcription quality.
