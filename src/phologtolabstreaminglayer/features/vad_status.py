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


class VadStatusMonitor:
    """Run Silero VAD on shared-hub audio and report speech/silence state changes.

    The PortAudio callback only enqueues; a daemon thread does the inference.
    `on_state(state)` fires from that thread only when the state changes.
    """

    def __init__(
        self,
        hub: Optional[Any] = None,
        on_state: Optional[Callable[[str], None]] = None,
        model_factory: Optional[Callable[[], Callable[[np.ndarray], Any]]] = None,
        *,
        sample_rate: int = SAMPLE_RATE,
        speech_threshold: float = SPEECH_THRESHOLD,
        silence_threshold: float = SILENCE_THRESHOLD,
        hangover_s: float = HANGOVER_S,
        clock_fn: Optional[Callable[[], float]] = None,
    ):
        self._hub = hub
        self.on_state = on_state
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
    """Thin persistent footer: colored dot + VAD state text."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.columnconfigure(1, weight=1)
        self._dot = tk.Label(self, text="\u25cf", font=("TkDefaultFont", 9))
        self._dot.grid(row=0, column=0, sticky=tk.W, padx=(0, 4))
        self._text = ttk.Label(self, text="")
        self._text.grid(row=0, column=1, sticky=tk.W)
        self.set_state(STATE_LOADING)

    def set_state(self, state: str) -> None:
        text, color = STATE_DISPLAY.get(state, STATE_DISPLAY[STATE_UNAVAILABLE])
        try:
            self._dot.config(foreground=color)
            self._text.config(text=text, foreground=color)
        except tk.TclError:
            pass


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
        self._vad_monitor = VadStatusMonitor(on_state=self._on_vad_state_change)
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
