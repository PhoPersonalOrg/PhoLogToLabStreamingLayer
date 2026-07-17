## Why
XDF recording currently always auto-starts once LSL streams are discovered. Users need a Settings control to disable that behavior without losing the convenient default-on launch flow.

## What Changes
- Add a Settings-tab checkbox: Auto-start XDF recording on startup (default enabled)
- Persist preference in `recording_settings.json`
- Gate `_try_auto_start_after_stream_discovery` when disabled

## Impact
- Affected specs: `specs/logging/spec.md`
- Affected code: `src/phologtolabstreaminglayer/logger_app.py`
