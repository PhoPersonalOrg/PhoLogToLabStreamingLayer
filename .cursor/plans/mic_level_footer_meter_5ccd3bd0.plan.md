---
name: Mic level footer meter
overview: Add a small real-time mic level meter (RMS dBFS bar) to the persistent footer next to the VAD dot, reusing the existing VadStatusMonitor audio tap — no new dependency, no new thread, automatically follows the selected mic.
todos:
  - id: meter-widget
    content: Add block_dbfs + MicLevelMeter canvas widget to features/vad_status.py
    status: completed
  - id: meter-wiring
    content: Add on_level hook to VadStatusMonitor and wire meter into VadStatusBar + mixin
    status: completed
  - id: meter-tests
    content: "Extend tests/test_vad_status.py: dBFS math, level emission, widget rendering, mixin marshaling"
    status: completed
  - id: meter-verify
    content: Run pytest suite and verify meter live next to the VAD dot
    status: completed
isProject: false
---

# Real-time mic level meter in the footer

## Approach

No suitable drop-in open-source tkinter meter exists (Qt widgets don't fit this tkinter app; ttk.Progressbar looks wrong). Build a ~70-line `tk.Canvas` meter and feed it from the audio already flowing through `VadStatusMonitor` in [vad_status.py](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoLogToLabStreamingLayer\src\phologtolabstreaminglayer\features\vad_status.py). Zero new dependencies; the meter tracks the currently selected mic for free because the monitor already re-subscribes on device change, and shows empty when the mic hub is idle.

## Changes (all in `features/vad_status.py` + its test file)

### 1. Level computation + widget

- `block_dbfs(data) -> float`: `20*log10(rms)` of a float32 block, floored at -60 dB (silence). Pure numpy, ~microseconds per block.
- `MicLevelMeter(tk.Canvas)`: ~120x10 px bar, range -60..0 dBFS. Continuous fill with audio-style coloring (green below -12 dB, yellow -12..-3, red above -3 = near clipping), plus a thin peak-hold tick that decays after ~1 s. `set_level_db(db)` only updates canvas item coords (cheap at the ~20 Hz block rate); guards `tk.TclError`.

### 2. Monitor hook

Add optional `on_level: Callable[[float], None]` to `VadStatusMonitor.__init__`. In `_process_block`, before frame splitting:

```python
if self.on_level is not None:
    try:
        self.on_level(block_dbfs(data))
    except Exception:
        pass
```

Fires once per hub block (~20 Hz) from the monitor thread; VAD state logic unchanged.

### 3. Footer wiring

- `VadStatusBar`: add `MicLevelMeter` at row 0, column 2 (right-aligned; dot col 0, text col 1 keeps weight=1). Add `set_level_db(db)` forwarding to the meter.
- `VadStatusMixin.setup_gui_VadStatusMixin`: pass `on_level=self._on_vad_level` when constructing the monitor. `_on_vad_level` marshals to Tk via `root.after(0, ...)` with the existing `_shutting_down` guard (same pattern as `_on_vad_state_change`). No throttle needed at 20 Hz on a tiny canvas.
- No `logger_app.py` changes required — the footer setup call already exists.

### 4. Tests — extend `tests/test_vad_status.py`

- `block_dbfs`: full-scale square = 0 dB, 0.5-amplitude sine ~ -9 dB, zeros = -60 floor.
- Monitor emits `on_level` per pushed block (fake hub + scripted model, existing fixtures).
- Widget: `set_level_db(-6)` draws nonzero fill, floor draws empty, peak tick decays; safe after destroy.
- Mixin: level callback updates the bar via `root.update()`; suppressed while `_shutting_down`.

## Verification

- `uv run pytest tests/test_vad_status.py` then full suite.
- Relaunch `uv run logger_app`: bar moves while speaking into the selected mic, empties when silent/idle, follows the Live Audio device dropdown, and sits beside the green VAD dot.