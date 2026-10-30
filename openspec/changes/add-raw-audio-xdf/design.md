## Context
XDF recording already auto-starts, auto-splits, and writes TextLogger markers. Live Whisper captures the same mic for transcription and optionally writes a non-crash-safe WAV. Raw archival audio must follow XDF, not Whisper.

## Goals / Non-Goals
- Goals: durable WAV aligned to XDF files; LSL start/stop markers; shared mic; interrupted files playable
- Non-Goals: LSL audio samples in XDF; 48 kHz; repairing old `live_transcripts/*.wav`

## Decisions
- Decision: 16 kHz PCM_16 WAV with ~1 s RIFF size rewrite + sidecar JSON. Playable after kill; matches Whisper for post-hoc transcription.
- Decision: Refcounted `MicCaptureHub` so transcription and raw recording can start/stop independently.
- Decision: File stem matches `{YYYYMMDD_HHMMSS}_log.xdf` → `raw_audio/{YYYYMMDD_HHMMSS}_audio.wav`.
- Alternatives considered: soundfile-until-close (current, not crash-safe); Ogg/FLAC (incomplete last page); raw PCM (not double-click playable); muxing into XDF (large, worse crash behavior).

## Risks / Trade-offs
- Dual open of the same mic → mitigated by the hub
- STARTED marker waits for the first audio block so `lsl_start` is sample-0 accurate; if the mic never delivers, STARTED is omitted and STOPPED still finalizes an empty/short file
