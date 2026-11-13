# Persistent footer VAD status indicator (green while voice activity is detected)
# Copyright (C) 2026 Pho Hale. All rights reserved.

"""Small always-visible voice-activity indicator for the LoggerApp window footer.

`VadStatusMonitor` subscribes to the shared mic hub (one extra listener on the
existing 16 kHz PortAudio stream) and runs the Silero VAD ONNX model bundled
with faster-whisper on 512-sample frames. Hysteresis (threshold + hangover)
keeps the indicator green through short gaps between words.

`VadStatusBar` is the ttk footer widget; `VadStatusMixin` wires monitor state
changes onto the Tk main thread via `root.after`.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Optional, Union

import numpy as np

SAMPLE_RATE = 16000
CHANNELS = 1
VAD_FRAME_SAMPLES = 512  # Silero v6 expects 512-sample frames at 16 kHz
SPEECH_THRESHOLD = 0.5
SILENCE_THRESHOLD = 0.35
HANGOVER_S = 0.4  # stay "speech" this long after the last speech frame
IDLE_TIMEOUT_S = 0.75  # no audio for this long -> hub not running -> idle
_QUEUE_MAXSIZE = 100

# Mic level meter (footer, right of the VAD text)
METER_DB_FLOOR = -60.0  # dBFS shown as an empty bar
METER_DB_YELLOW = -12.0  # above this the fill turns yellow
METER_DB_RED = -3.0  # above this the fill turns red (near clipping)
METER_WIDTH = 120
METER_HEIGHT = 10
METER_PEAK_HOLD_S = 1.0  # peak tick holds this long, then falls with the level

STATE_LOADING = "loading"
STATE_IDLE = "idle"
STATE_SILENCE = "silence"
STATE_SPEECH = "speech"
STATE_UNAVAILABLE = "unavailable"

# state -> (footer text, dot/text color)
STATE_DISPLAY = {
    STATE_LOADING: ("VAD: loading...", "orange"),
    STATE_IDLE: ("VAD: idle (mic off)", "gray"),
    STATE_SILENCE: ("VAD: silence", "gray"),
    STATE_SPEECH: ("VAD: voice", "green"),
    STATE_UNAVAILABLE: ("VAD: unavailable", "red"),
}


def default_vad_model_factory() -> Callable[[np.ndarray], Any]:
    """Silero VAD ONNX model bundled with faster-whisper (no extra dependency)."""
    from faster_whisper.vad import get_vad_model

    return get_vad_model()


def resolve_hub(hub: Optional[Any]) -> Any:
    if hub is not None:
        return hub
    from whisper_timestamped.audio_capture import get_shared_mic_hub

    return get_shared_mic_hub()


def block_dbfs(data: np.ndarray, floor_db: float = METER_DB_FLOOR) -> float:
    """RMS level of a float32 audio block in dBFS, clamped to [floor_db, 0]."""
    arr = np.asarray(data, dtype=np.float32)
    if arr.size == 0:
        return float(floor_db)
    if arr.ndim == 2:
        arr = arr[:, 0]
    rms = float(np.sqrt(np.mean(np.square(arr, dtype=np.float64))))
    if rms <= 1e-10:
        return float(floor_db)
    db = 20.0 * float(np.log10(rms))
    return max(float(floor_db), min(0.0, db))


class MicLevelMeter(tk.Canvas):
    """Small horizontal mic level bar (dBFS) with a decaying peak-hold tick.

    Only canvas item coordinates/colors are updated per level, so drawing is
    cheap at the ~20 Hz hub block rate. All updates happen on the Tk thread.
    """

    def __init__(self, parent, width: int = METER_WIDTH, height: int = METER_HEIGHT, **kwargs):
        super().__init__(parent, width=width, height=height, highlightthickness=0, **kwargs)
        self._width = int(width)
        self._height = int(height)
        self._bg = self._resolve_bg(parent)
        self.configure(background=self._bg)
        self._track = self.create_rectangle(0, 0, self._width, self._height, fill="#d9d9d9", outline="#9a9a9a")
        self._fill = self.create_rectangle(0, 0, 0, self._height, fill="#2eaa2e", outline="")
        self._peak = self.create_line(0, 0, 0, self._height, fill="#444444", width=2, state="hidden")
        self._peak_db: Optional[float] = None
        self._peak_mono = 0.0

    @staticmethod
    def _resolve_bg(parent) -> str:
        try:
            style = ttk.Style(parent)
            bg = style.lookup("TFrame", "background")
            if bg:
                return str(bg)
        except Exception:
            pass
        return "#f0f0f0"

    def _db_to_x(self, db: float) -> int:
        frac = (max(METER_DB_FLOOR, min(0.0, db)) - METER_DB_FLOOR) / (0.0 - METER_DB_FLOOR)
        return int(round(frac * self._width))

    def set_level_db(self, db: float, now: Optional[float] = None) -> None:
        now = float(time.monotonic() if now is None else now)
        db = max(METER_DB_FLOOR, min(0.0, float(db)))
        if db >= METER_DB_RED:
            color = "#d93025"
        elif db >= METER_DB_YELLOW:
            color = "#e0a800"
        else:
            color = "#2eaa2e"
        x = self._db_to_x(db)
        try:
            self.coords(self._fill, 0, 0, x, self._height)
            self.itemconfigure(self._fill, fill=color)
            if self._peak_db is None or db >= self._peak_db or (now - self._peak_mono) >= METER_PEAK_HOLD_S:
                self._peak_db = db
                self._peak_mono = now
            if self._peak_db <= METER_DB_FLOOR:
                self.itemconfigure(self._peak, state="hidden")
            else:
                px = self._db_to_x(self._peak_db)
                self.coords(self._peak, px, 0, px, self._height)
                self.itemconfigure(self._peak, state="normal")
        except tk.TclError:
            pass


class VadStatusMonitor:
    """Run Silero VAD on shared-hub audio and report speech/silence state changes.

    The PortAudio callback only enqueues; a daemon thread does the inference.
    `on_state(state)` fires from that thread only when the state changes.
    `on_level(db)` fires once per audio block (~20 Hz) with the RMS dBFS level.
    """

    def __init__(
        self,
        hub: Optional[Any] = None,
        on_state: Optional[Callable[[str], None]] = None,
        model_factory: Optional[Callable[[], Callable[[np.ndarray], Any]]] = None,
        *,
        on_level: Optional[Callable[[float], None]] = None,
        sample_rate: int = SAMPLE_RATE,
        speech_threshold: float = SPEECH_THRESHOLD,
        silence_threshold: float = SILENCE_THRESHOLD,
        hangover_s: float = HANGOVER_S,
        clock_fn: Optional[Callable[[], float]] = None,
    ):
        self._hub = hub
        self.on_state = on_state
        self.on_level = on_level
        self._model_factory = model_factory or default_vad_model_factory
        self.sample_rate = int(sample_rate)
        self.speech_threshold = float(speech_threshold)
        self.silence_threshold = float(silence_threshold)
        self.hangover_s = float(hangover_s)
        self._clock_fn = clock_fn or time.monotonic

        self._state = STATE_LOADING
        self._subscribed = False
        self._device: Optional[Union[int, str]] = None
        self._desired_device: Optional[Union[int, str]] = None
        self._pending_resubscribe = False
        self._model: Optional[Callable[[np.ndarray], Any]] = None
        self._buffer = np.zeros(0, dtype=np.float32)
        self._last_speech_mono: Optional[float] = None
        self._last_audio_mono = 0.0
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, device: Optional[Union[int, str]] = None) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._desired_device = device
        self._queue = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._buffer = np.zeros(0, dtype=np.float32)
        self._last_speech_mono = None
        # Bar already renders "loading"; the worker emits the first real state.
        self._state = STATE_LOADING
        self._thread = threading.Thread(target=self._run_loop, name="VadStatusMonitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._unsubscribe()
        self._model = None

    def set_device(self, device: Optional[Union[int, str]]) -> None:
        """Follow the Live Audio mic selection; the worker re-subscribes."""
        self._desired_device = device
        if self._subscribed and device != self._device:
            self._pending_resubscribe = True

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        if self.on_state is not None:
            try:
                self.on_state(state)
            except Exception as e:
                print(f"Warning: VAD state callback failed: {e}")

    def _subscribe(self, device: Optional[Union[int, str]]) -> bool:
        try:
            hub = resolve_hub(self._hub)
            hub.subscribe(
                self._on_hub_audio,
                sample_rate=self.sample_rate,
                channels=CHANNELS,
                device=device,
            )
        except Exception as e:
            print(f"Warning: VAD monitor could not subscribe to mic hub: {e}")
            return False
        self._hub = hub
        self._device = device
        self._subscribed = True
        self._last_audio_mono = float(self._clock_fn())
        return True

    def _unsubscribe(self) -> None:
        if not self._subscribed:
            return
        self._subscribed = False
        hub = self._hub
        if hub is not None:
            try:
                hub.unsubscribe(self._on_hub_audio)
            except Exception:
                pass

    def _on_hub_audio(self, data: np.ndarray) -> None:
        # PortAudio thread: enqueue only, never block or infer here.
        try:
            self._queue.put_nowait(np.asarray(data, dtype=np.float32))
        except queue.Full:
            pass

    def _run_loop(self) -> None:
        if not self._subscribe(self._desired_device):
            self._set_state(STATE_UNAVAILABLE)
            return
        if not self._load_model():
            self._unsubscribe()
            self._set_state(STATE_UNAVAILABLE)
            return
        self._set_state(STATE_IDLE)

        while not self._stop_event.is_set():
            if self._pending_resubscribe:
                self._pending_resubscribe = False
                self._unsubscribe()
                self._buffer = np.zeros(0, dtype=np.float32)
                self._last_speech_mono = None
                if not self._subscribe(self._desired_device):
                    self._set_state(STATE_UNAVAILABLE)
                    return

            try:
                data = self._queue.get(timeout=0.05)
            except queue.Empty:
                self._check_idle()
                continue
            self._last_audio_mono = float(self._clock_fn())
            self._process_block(data)

        self._unsubscribe()

    def _load_model(self) -> bool:
        try:
            self._model = self._model_factory()
        except Exception as e:
            print(f"Warning: VAD model unavailable: {e}")
            self._model = None
            return False
        return True

    def _check_idle(self) -> None:
        if self._state in (STATE_SPEECH, STATE_SILENCE):
            if (float(self._clock_fn()) - self._last_audio_mono) > IDLE_TIMEOUT_S:
                self._set_state(STATE_IDLE)

    def _process_block(self, data: np.ndarray) -> None:
        if data is None or data.size == 0:
            return
        if data.ndim == 2:
            data = data[:, 0]
        if self.on_level is not None:
            try:
                self.on_level(block_dbfs(data))
            except Exception:
                pass
        self._buffer = np.concatenate((self._buffer, data))
        n_frames = self._buffer.size // VAD_FRAME_SAMPLES
        for i in range(n_frames):
            frame = self._buffer[i * VAD_FRAME_SAMPLES : (i + 1) * VAD_FRAME_SAMPLES]
            self._process_frame(frame)
        ## END for i in range(n_frames)....

        self._buffer = self._buffer[n_frames * VAD_FRAME_SAMPLES :]

    def _process_frame(self, frame: np.ndarray) -> None:
        model = self._model
        if model is None:
            return
        try:
            probs = model(frame)
        except Exception as e:
            print(f"Warning: VAD inference failed: {e}")
            return
        prob = float(np.max(probs))
        now = float(self._clock_fn())
        if prob >= self.speech_threshold:
            self._last_speech_mono = now
            self._set_state(STATE_SPEECH)
            return
        if self._state == STATE_IDLE:
            # Audio is flowing again after a gap.
            self._set_state(STATE_SILENCE)
        if self._state == STATE_SPEECH and prob < self.silence_threshold:
            # Below the release threshold: stay green only through the hangover.
            if self._last_speech_mono is None or (now - self._last_speech_mono) >= self.hangover_s:
                self._set_state(STATE_SILENCE)


class VadStatusBar(ttk.Frame):
    """Thin persistent footer: colored dot + VAD state text + mic level meter."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.columnconfigure(1, weight=1)
        self._dot = tk.Label(self, text="\u25cf", font=("TkDefaultFont", 9))
        self._dot.grid(row=0, column=0, sticky=tk.W, padx=(0, 4))
        self._text = ttk.Label(self, text="")
        self._text.grid(row=0, column=1, sticky=tk.W)
        self._meter = MicLevelMeter(self)
        self._meter.grid(row=0, column=2, sticky=tk.E, padx=(8, 0))
        self.set_state(STATE_LOADING)

    def set_state(self, state: str) -> None:
        text, color = STATE_DISPLAY.get(state, STATE_DISPLAY[STATE_UNAVAILABLE])
        try:
            self._dot.config(foreground=color)
            self._text.config(text=text, foreground=color)
        except tk.TclError:
            pass

    def set_level_db(self, db: float) -> None:
        self._meter.set_level_db(db)


class VadStatusMixin:
    """Wire a `VadStatusMonitor` to a persistent `VadStatusBar` footer.

    Requires: self.root (tk.Tk). Call `init_VadStatusMixin()` in __init__,
    `setup_gui_VadStatusMixin(parent, row)` from setup_gui, and
    `cleanup_VadStatusMixin()` from on_closing.
    """

    def init_VadStatusMixin(self):
        self.vad_status_bar: Optional[VadStatusBar] = None
        self._vad_monitor: Optional[VadStatusMonitor] = None

    def setup_gui_VadStatusMixin(self, parent, row: int = 3):
        """Create the footer bar and start the monitor (mic follows Live Audio combo)."""
        self.vad_status_bar = VadStatusBar(parent)
        self.vad_status_bar.grid(row=row, column=0, sticky=(tk.W, tk.E), padx=10, pady=(0, 4))
        device = None
        selected_mic = getattr(self, "_selected_mic_device", None)
        if callable(selected_mic):
            try:
                device = selected_mic()
            except Exception:
                device = None
        self._vad_monitor = VadStatusMonitor(
            on_state=self._on_vad_state_change,
            on_level=self._on_vad_level,
        )
        self._vad_monitor.start(device=device)
        return self.vad_status_bar

    def _on_vad_state_change(self, state: str) -> None:
        """Called from the monitor thread; marshal to the Tk main thread."""
        root = getattr(self, "root", None)
        if root is None:
            return

        def _update():
            if getattr(self, "_shutting_down", False):
                return
            bar = getattr(self, "vad_status_bar", None)
            if bar is not None:
                bar.set_state(state)

        try:
            root.after(0, _update)
        except Exception:
            pass

    def _on_vad_level(self, db: float) -> None:
        """Called from the monitor thread (~20 Hz); marshal to the Tk main thread."""
        root = getattr(self, "root", None)
        if root is None:
            return

        def _update():
            if getattr(self, "_shutting_down", False):
                return
            bar = getattr(self, "vad_status_bar", None)
            if bar is not None:
                bar.set_level_db(db)

        try:
            root.after(0, _update)
        except Exception:
            pass

    def set_vad_device(self, device: Optional[Union[int, str]]) -> None:
        monitor = getattr(self, "_vad_monitor", None)
        if monitor is not None:
            monitor.set_device(device)

    def cleanup_VadStatusMixin(self):
        monitor = getattr(self, "_vad_monitor", None)
        if monitor is not None:
            try:
                monitor.stop()
            except Exception as e:
                print(f"Warning: VAD monitor stop failed: {e}")
            self._vad_monitor = None
