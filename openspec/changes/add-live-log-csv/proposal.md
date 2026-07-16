## Why
Log History shows transcripts, EventBoard events, and status messages in the UI, but there is no continuously updated plaintext CSV of that same stream. Users need a durable, human-readable log they can inspect mid-session without waiting for recording stop or parsing Whisper JSONL.

## What Changes
- Add a live Log History CSV writer that appends every `update_log_display` line as it appears
- Write on a background thread (queue + flush) so the Tk main thread is not blocked
- Add a Settings-tab group to enable/disable (default on) and choose the output directory (default `{xdf_folder}/CSV`)
- Persist those preferences in `live_log_csv_settings.json`

## Impact
- Affected specs: `specs/logging/spec.md`
- Affected code: `src/phologtolabstreaminglayer/features/live_log_csv.py`, `src/phologtolabstreaminglayer/logger_app.py`
