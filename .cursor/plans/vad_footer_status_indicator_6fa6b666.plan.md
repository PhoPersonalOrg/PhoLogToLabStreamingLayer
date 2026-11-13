---
name: VAD footer status indicator
overview: Add a persistent footer status bar to the LoggerApp window with a small VAD indicator (green dot while voice is detected, gray on silence) driven by a lightweight Silero VAD monitor subscribed to the existing shared mic hub.
todos:
  - id: vad-feature-module
    content: "Create features/vad_status.py: VadStatusMonitor (hub subscriber + Silero ONNX + hysteresis), VadStatusBar footer widget, VadStatusMixin"
    status: completed
  - id: wire-logger-app
    content: "Wire into logger_app.py: mixin init, footer at root row 3 in setup_gui, device-change hook, cleanup in on_closing"
    status: completed
  - id: vad-tests
    content: "Add tests/test_vad_status.py: fake-model state machine, hub lifecycle, widget color smoke test"
    status: completed
  - id: verify
    content: Run pytest suite and verify indicator live in the running app
    status: completed
isProject: false
---

# Persistent VAD status indicator in window footer

## Goal

A small always-visible footer at the bottom of the `LoggerApp` window showing live voice-activity state: **green while speech is detected**, gray on silence, orange while the VAD model loads, red if unavailable. Works whether or not transcription/recording is running (it taps the shared mic hub directly).

## Key findings

- Root window grid in [logger_app.py](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoLogToLabStreamingLayer\src\phologtolabstreaminglayer\logger_app.py) `setup_gui()` uses rows 0-2 (notebook / log display / console). Footer goes in a new **row 3** with `weight=0`.
- `MicCaptureHub` (`whisper_timestamped.audio_capture`, imported lazily) already shares one 16 kHz mic stream between `RawAudioRecorder` and `LiveTranscriber`; a third listener costs nothing extra.
- No real-time VAD exists today (`vad_filter` only trims transcription chunks every ~2 s). Reuse the Silero model already bundled with faster-whisper: `faster_whisper.vad.get_vad_model()` (`silero_vad_v6.onnx`, onnxruntime CPU). Verified loadable in `.venv` — **no new dependency**.
- Feature stays entirely in PhoLogToLabStreamingLayer (the `EXTERNAL/whisper-timestamped` copy is stale; only its unchanged `audio_capture` API is used).

## Data flow

```mermaid
flowchart LR
  mic["MicCaptureHub (PortAudio, 16kHz)"] -->|"subscribe(cb)"| mon["VadStatusMonitor thread"]
  mon -->|"512-sample chunks"| silero["Silero ONNX (faster_whisper.vad)"]
  silero -->|"speech prob"| sm["threshold + hangover state machine"]
  sm -->|"state change"| after["root.after(0, ...)"]
  after --> bar["VadStatusBar footer (row 3)"]
```

## Changes

### 1. New feature module `src/phologtolabstreaminglayer/features/vad_status.py`

Follows the existing `features/*Mixin` pattern (like `recording_indicator_icon.py`):

- `VadStatusMonitor` — subscribes to `get_shared_mic_hub()` at 16 kHz mono; PortAudio callback only enqueues (never blocks). A daemon thread:
  - Lazy-loads `get_vad_model()` on first run (state `loading`); on ImportError/RuntimeError sets `unavailable` and exits cleanly.
  - Buffers audio into 512-sample frames, runs the ONNX model, applies hysteresis: prob >= 0.5 -> speech; prob < 0.35 -> silence after ~400 ms hangover (prevents flicker between words).
  - States: `loading`, `idle` (hub not running), `silence`, `speech`, `unavailable`. Fires `on_state(state)` only on change; thread-safe `set_device(idx)` re-subscribes + resets model when the Live Audio mic combo changes.
  - `stop()` unsubscribes (hub closes when last listener leaves) and joins the thread.
- `VadStatusBar(ttk.Frame)` — thin footer: small dot + word, e.g. `● Voice` / `● Silence` / `● Idle` / `● VAD unavailable`. Colors: speech `green`, silence `gray`, loading `orange`, unavailable `red`, idle `gray`. `set_state()` guards `tk.TclError`.
- `VadStatusMixin` — `init_VadStatusMixin()` (state attrs), `setup_gui_VadStatusMixin(parent, row)` (builds bar + monitor, marshals state changes via `self.root.after(0, ...)` with `_shutting_down` guard, same pattern as `_make_custom_emit` in the transcription mixin), `cleanup_VadStatusMixin()` (stop monitor).

### 2. Wire into `logger_app.py`

- Add `VadStatusMixin` to the `LoggerApp` bases; call `init_VadStatusMixin()` in `__init__` next to the other mixin inits (~line 130).
- In `setup_gui()` (~line 757): add `self.root.rowconfigure(3, weight=0)` and after the console panel call `self.setup_gui_VadStatusMixin(self.root, row=3)` (sticky E/W, small padding). Footer is visible on all tabs and survives console collapse.
- In `_on_audio_device_selected()` (~line 1158): forward `self._selected_mic_device()` to the monitor so the indicator follows the chosen mic.
- In `on_closing()` (~line 3040, near `stop_live_transcription`): call `cleanup_VadStatusMixin()` before recording teardown.

### 3. Tests — `tests/test_vad_status.py`

Mirror `tests/test_raw_audio.py` style (unittest, fake hub via `MicCaptureHub(stream_factory=...)`, no real mic):

- Fake VAD model returning scripted probabilities: silence -> `silence`; speech frame -> `speech` immediately; speech then silence -> stays green through hangover, then `silence`.
- Model factory raising -> `unavailable`, no crash; hub not running -> `idle`.
- Subscribe/unsubscribe lifecycle: monitor stop removes its listener; `set_device` re-subscribes.
- Widget smoke test with withdrawn `tk.Tk()` (pattern from `tests/test_confirm_exit.py`): `set_state("speech")` renders green.

## Verification

- `uv run pytest tests/test_vad_status.py` then full suite.
- Manual: restart `uv run logger_app` (an instance is currently running in the terminal), speak into the mic — footer dot turns green during speech and gray ~0.4 s after; stays visible when switching tabs and with transcription stopped.

## Defaults chosen (adjustable constants)

- Threshold 0.5 / release 0.35, hangover 400 ms, poll ~50 ms — snappy but flicker-free.
- Indicator runs from app launch (mic opens via hub even when transcription is off), matching "persistent".