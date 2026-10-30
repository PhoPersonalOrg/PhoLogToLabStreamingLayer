## Why
Tray-mode recording is not trustworthy today: XDF is a one-shot snapshot of streams present at Start, so connecting EEG later requires quitting the app. Samples are not flushed until close, and the window X button fully quits instead of hiding to tray.

## What Changes
- Hot-add newly discovered LSL streams into the open XDF (in-process attach, RCS `update`/`select all`)
- Timed auto-split of complete XDF checkpoint files (default 30 minutes; 0 disables), using in-process `rotate_file` to avoid a stop/start gap
- Flush XDF chunks as they are written
- Window close hides to tray; only tray Exit / `quit_app` stops recording and writes footers
- Persist `auto_add_new_streams` and `auto_split_minutes` in `recording_settings.json`

## Impact
- Affected specs: `specs/logging/spec.md`
- Affected code: `src/phologtolabstreaminglayer/logger_app.py`, `src/phologtolabstreaminglayer/console.py`, lab-recorder-python (`labrecorder/recorder.py`, `labrecorder/xdf/writer.py`), PhoPyLSLhelper (`SingletonInstanceMixin` SIGINT/SIGTERM)
