## Why
Live Whisper already writes a 16 kHz WAV, but only while transcription is running, and `soundfile` does not rewrite the RIFF header until close — a crash leaves an unplayable file. Post-hoc offline transcription needs durable raw audio that follows XDF start/stop/split, independent of Whisper, with LSL markers for sync.

## What Changes
- Record crash-recoverable 16-bit PCM WAV files whenever XDF recording is active (even if Whisper is off)
- Patch WAV headers about once per second and write an atomic sidecar JSON so interrupted files stay playable
- Name files `{xdf_folder}/raw_audio/{YYYYMMDD_HHMMSS}_audio.wav` to match the XDF timestamp
- Emit `AUDIO_FILE_STARTED` / `AUDIO_FILE_STOPPED` on TextLogger (and Log History)
- Share one refcounted mic capture hub so stopping transcription does not kill XDF audio, and vice versa
- Persist `record_raw_audio` (default on) in `recording_settings.json`

## Impact
- Affected specs: `specs/logging/spec.md`
- Affected code: `src/phologtolabstreaminglayer/features/raw_audio.py`, `src/phologtolabstreaminglayer/logger_app.py`, `whisper-timestamped/whisper_timestamped/audio_capture.py`, `whisper-timestamped/whisper_timestamped/live.py`
