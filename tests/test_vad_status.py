"""Tests for the persistent footer VAD status indicator.

Covers the monitor state machine (fake Silero model + fake hub, no real mic),
hub subscribe/unsubscribe lifecycle, device switching, and the footer widget.
"""

from __future__ import annotations

import time
import tkinter as tk
import unittest

import numpy as np

from phologtolabstreaminglayer.features.vad_status import (
    METER_DB_FLOOR,
    STATE_IDLE,
    STATE_SILENCE,
    STATE_SPEECH,
    STATE_UNAVAILABLE,
    MicLevelMeter,
    VadStatusBar,
    VadStatusMixin,
    VadStatusMonitor,
    block_dbfs,
)
from phologtolabstreaminglayer.logger_app import LoggerApp
from whisper_timestamped.audio_capture import MicCaptureHub


class _FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


class _ScriptedVadModel:
    """Returns scripted speech probabilities; holds the last value once exhausted."""

    def __init__(self, probs):
        self.probs = list(probs)
        self.calls = 0

    def __call__(self, frame):
        idx = min(self.calls, len(self.probs) - 1)
        self.calls += 1
        return np.array([self.probs[idx]], dtype=np.float32)


class _FakeClock:
    def __init__(self, start: float = 100.0):
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _push_block(hub: MicCaptureHub, n_samples: int = 512, data=None) -> None:
    """Feed the hub one VAD frame via its PortAudio-style callback."""
    if data is None:
        data = np.zeros(n_samples, dtype=np.float32)
    data = np.asarray(data, dtype=np.float32)
    hub._audio_callback(data.reshape(-1, 1), data.size, None, None)


class TestVadStatusMonitor(unittest.TestCase):
    def _make_monitor(self, probs, clock=None):
        hub = MicCaptureHub(stream_factory=lambda **kw: _FakeStream(**kw))
        model = _ScriptedVadModel(probs)
        states = []
        monitor = VadStatusMonitor(
            hub=hub,
            on_state=states.append,
            model_factory=lambda: model,
            clock_fn=clock or _FakeClock(),
        )
        return hub, model, states, monitor

    def test_silence_speech_hangover_then_silence(self):
        clock = _FakeClock()
        hub, model, states, monitor = self._make_monitor([0.05], clock=clock)
        monitor.start()
        try:
            self.assertTrue(_wait_for(lambda: STATE_IDLE in states))
            self.assertEqual(hub.listener_count, 1)

            # Silence frame -> silence
            _push_block(hub)
            self.assertTrue(_wait_for(lambda: STATE_SILENCE in states))

            # Speech frame -> green immediately
            model.probs = [0.9]
            _push_block(hub)
            self.assertTrue(_wait_for(lambda: STATE_SPEECH in states))

            # Silence within the hangover window -> stays green
            clock.advance(0.1)
            model.probs = [0.05]
            calls_before = model.calls
            _push_block(hub)
            self.assertTrue(_wait_for(lambda: model.calls > calls_before))
            self.assertEqual(monitor.state, STATE_SPEECH)

            # Silence past the hangover window -> gray
            clock.advance(0.5)
            calls_before = model.calls
            _push_block(hub)
            self.assertTrue(_wait_for(lambda: model.calls > calls_before))
            self.assertTrue(_wait_for(lambda: monitor.state == STATE_SILENCE))

            self.assertEqual(states, [STATE_IDLE, STATE_SILENCE, STATE_SPEECH, STATE_SILENCE])
        finally:
            monitor.stop()
        self.assertEqual(hub.listener_count, 0)
        self.assertFalse(monitor.is_running)

    def test_audio_gap_returns_to_idle_then_recovers(self):
        clock = _FakeClock()
        hub, model, states, monitor = self._make_monitor([0.05], clock=clock)
        monitor.start()
        try:
            self.assertTrue(_wait_for(lambda: STATE_SILENCE in states or STATE_IDLE in states))
            _push_block(hub)
            self.assertTrue(_wait_for(lambda: monitor.state == STATE_SILENCE))

            # No audio for longer than the idle timeout -> idle (mic off)
            clock.advance(1.0)
            self.assertTrue(_wait_for(lambda: monitor.state == STATE_IDLE))

            # Audio resumes -> silence again
            _push_block(hub)
            self.assertTrue(_wait_for(lambda: monitor.state == STATE_SILENCE))
        finally:
            monitor.stop()

    def test_hysteresis_band_keeps_state(self):
        hub, model, states, monitor = self._make_monitor([0.9])
        monitor.start()
        try:
            self.assertTrue(_wait_for(lambda: STATE_IDLE in states))
            _push_block(hub)
            self.assertTrue(_wait_for(lambda: monitor.state == STATE_SPEECH))

            # Between release (0.35) and speech (0.5) thresholds: keep speech.
            model.probs = [0.4]
            calls_before = model.calls
            _push_block(hub)
            self.assertTrue(_wait_for(lambda: model.calls > calls_before))
            self.assertEqual(monitor.state, STATE_SPEECH)
        finally:
            monitor.stop()

    def test_model_factory_failure_reports_unavailable(self):
        hub = MicCaptureHub(stream_factory=lambda **kw: _FakeStream(**kw))
        states = []

        def _broken_factory():
            raise RuntimeError("no VAD model")

        monitor = VadStatusMonitor(hub=hub, on_state=states.append, model_factory=_broken_factory)
        monitor.start()
        try:
            self.assertTrue(_wait_for(lambda: STATE_UNAVAILABLE in states))
            self.assertTrue(_wait_for(lambda: not monitor.is_running))
            self.assertEqual(hub.listener_count, 0)
        finally:
            monitor.stop()

    def test_subscribe_failure_reports_unavailable(self):
        class _BrokenHub:
            def subscribe(self, *a, **k):
                raise RuntimeError("no mic")

        states = []
        monitor = VadStatusMonitor(
            hub=_BrokenHub(),
            on_state=states.append,
            model_factory=lambda: _ScriptedVadModel([0.05]),
        )
        monitor.start()
        try:
            self.assertTrue(_wait_for(lambda: STATE_UNAVAILABLE in states))
        finally:
            monitor.stop()

    def test_set_device_resubscribes(self):
        hub, model, states, monitor = self._make_monitor([0.05])
        monitor.start(device=None)
        try:
            self.assertTrue(_wait_for(lambda: STATE_IDLE in states))
            self.assertIsNone(hub.device)

            monitor.set_device(3)
            self.assertTrue(_wait_for(lambda: hub.device == 3))
            self.assertEqual(hub.listener_count, 1)
            self.assertTrue(monitor.is_running)
        finally:
            monitor.stop()
        self.assertEqual(hub.listener_count, 0)

    def test_stop_is_idempotent(self):
        hub, model, states, monitor = self._make_monitor([0.05])
        monitor.start()
        self.assertTrue(_wait_for(lambda: STATE_IDLE in states))
        monitor.stop()
        monitor.stop()
        self.assertEqual(hub.listener_count, 0)


class TestVadStatusBar(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_speech_renders_green(self):
        bar = VadStatusBar(self.root)
        bar.set_state(STATE_SPEECH)
        self.assertEqual(str(bar._dot.cget("foreground")), "green")
        self.assertIn("voice", str(bar._text.cget("text")).lower())

    def test_silence_and_idle_render_gray(self):
        bar = VadStatusBar(self.root)
        for state in (STATE_SILENCE, STATE_IDLE):
            bar.set_state(state)
            self.assertEqual(str(bar._dot.cget("foreground")), "gray")

    def test_unavailable_and_unknown_render_red(self):
        bar = VadStatusBar(self.root)
        for state in (STATE_UNAVAILABLE, "bogus-state"):
            bar.set_state(state)
            self.assertEqual(str(bar._dot.cget("foreground")), "red")

    def test_footer_forwards_level_to_meter(self):
        bar = VadStatusBar(self.root)
        bar.set_level_db(-6.0)
        x2 = bar._meter.coords(bar._meter._fill)[2]
        self.assertGreater(x2, 0)
        bar.set_level_db(METER_DB_FLOOR)
        self.assertEqual(bar._meter.coords(bar._meter._fill)[2], 0)


class _MixinStub(VadStatusMixin):
    def __init__(self, root):
        self.root = root
        self._shutting_down = False
        self.init_VadStatusMixin()


class TestVadStatusMixin(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.stub = _MixinStub(self.root)
        self.stub.vad_status_bar = VadStatusBar(self.root)

    def tearDown(self):
        self.root.destroy()

    def test_state_change_marshals_to_bar(self):
        self.stub._on_vad_state_change(STATE_SPEECH)
        self.root.update()
        self.assertEqual(str(self.stub.vad_status_bar._dot.cget("foreground")), "green")

    def test_no_update_while_shutting_down(self):
        self.stub._on_vad_state_change(STATE_SPEECH)
        self.root.update()
        self.stub._shutting_down = True
        self.stub._on_vad_state_change(STATE_SILENCE)
        self.root.update()
        self.assertEqual(str(self.stub.vad_status_bar._dot.cget("foreground")), "green")

    def test_set_vad_device_forwards_to_monitor(self):
        seen = []

        class _Mon:
            def set_device(self, device):
                seen.append(device)

        self.stub._vad_monitor = _Mon()
        self.stub.set_vad_device(5)
        self.assertEqual(seen, [5])

    def test_cleanup_stops_monitor(self):
        stopped = []

        class _Mon:
            def stop(self):
                stopped.append(True)

        self.stub._vad_monitor = _Mon()
        self.stub.cleanup_VadStatusMixin()
        self.assertEqual(stopped, [True])
        self.assertIsNone(self.stub._vad_monitor)

    def test_level_marshals_to_meter(self):
        self.stub._on_vad_level(-6.0)
        self.root.update()
        meter = self.stub.vad_status_bar._meter
        self.assertGreater(meter.coords(meter._fill)[2], 0)

    def test_level_suppressed_while_shutting_down(self):
        self.stub._on_vad_level(-6.0)
        self.root.update()
        meter = self.stub.vad_status_bar._meter
        x_before = meter.coords(meter._fill)[2]
        self.stub._shutting_down = True
        self.stub._on_vad_level(METER_DB_FLOOR)
        self.root.update()
        self.assertEqual(meter.coords(meter._fill)[2], x_before)


class TestBlockDbfs(unittest.TestCase):
    def test_full_scale_square_is_zero_db(self):
        data = np.ones(1600, dtype=np.float32)
        self.assertAlmostEqual(block_dbfs(data), 0.0, places=5)

    def test_half_amplitude_sine_is_about_minus_nine_db(self):
        t = np.arange(16000, dtype=np.float32) / 16000.0
        data = 0.5 * np.sin(2 * np.pi * 220.0 * t)
        self.assertAlmostEqual(block_dbfs(data), 20.0 * np.log10(0.5 / np.sqrt(2.0)), delta=0.1)

    def test_silence_and_empty_hit_floor(self):
        self.assertEqual(block_dbfs(np.zeros(1600, dtype=np.float32)), METER_DB_FLOOR)
        self.assertEqual(block_dbfs(np.zeros(0, dtype=np.float32)), METER_DB_FLOOR)

    def test_stereo_uses_first_channel(self):
        data = np.stack([np.ones(800, dtype=np.float32), np.zeros(800, dtype=np.float32)], axis=1)
        self.assertAlmostEqual(block_dbfs(data), 0.0, places=5)


class TestMicLevelMeter(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.meter = MicLevelMeter(self.root)

    def tearDown(self):
        self.root.destroy()

    def test_loud_level_draws_fill(self):
        self.meter.set_level_db(-6.0, now=100.0)
        x2 = self.meter.coords(self.meter._fill)[2]
        self.assertGreater(x2, 0)
        self.assertLessEqual(x2, self.meter._width)
        self.assertEqual(str(self.meter.itemcget(self.meter._fill, "fill")), "#e0a800")

    def test_floor_draws_empty_and_hides_peak(self):
        self.meter.set_level_db(-6.0, now=100.0)
        self.meter.set_level_db(METER_DB_FLOOR, now=102.0)
        self.assertEqual(self.meter.coords(self.meter._fill)[2], 0)
        self.assertEqual(str(self.meter.itemcget(self.meter._peak, "state")), "hidden")

    def test_near_clipping_is_red(self):
        self.meter.set_level_db(-1.0, now=100.0)
        self.assertEqual(str(self.meter.itemcget(self.meter._fill, "fill")), "#d93025")

    def test_peak_holds_then_decays(self):
        self.meter.set_level_db(-6.0, now=100.0)
        peak_x = self.meter.coords(self.meter._peak)[0]
        self.assertGreater(peak_x, 0)
        # Quieter block within the hold window: tick stays at the peak.
        self.meter.set_level_db(-30.0, now=100.5)
        self.assertEqual(self.meter.coords(self.meter._peak)[0], peak_x)
        self.assertEqual(str(self.meter.itemcget(self.meter._peak, "state")), "normal")
        # After the hold window: tick falls to the current level.
        self.meter.set_level_db(-30.0, now=101.5)
        self.assertLess(self.meter.coords(self.meter._peak)[0], peak_x)

    def test_set_level_after_destroy_is_safe(self):
        self.meter.destroy()
        self.meter.set_level_db(-6.0)  # must not raise


class TestVadMonitorLevel(unittest.TestCase):
    def test_on_level_emitted_per_block(self):
        hub = MicCaptureHub(stream_factory=lambda **kw: _FakeStream(**kw))
        levels = []
        monitor = VadStatusMonitor(
            hub=hub,
            model_factory=lambda: _ScriptedVadModel([0.05]),
            on_level=levels.append,
        )
        monitor.start()
        try:
            self.assertTrue(_wait_for(lambda: monitor.state == STATE_IDLE))
            t = np.arange(512, dtype=np.float32) / 16000.0
            block = 0.5 * np.sin(2 * np.pi * 220.0 * t)
            _push_block(hub, data=block)
            self.assertTrue(_wait_for(lambda: len(levels) >= 1))
            expected = 20.0 * np.log10(0.5 / np.sqrt(2.0))
            self.assertAlmostEqual(levels[0], expected, delta=0.2)
        finally:
            monitor.stop()


class TestLoggerAppVadWiring(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_logger_app_inherits_mixin(self):
        self.assertTrue(issubclass(LoggerApp, VadStatusMixin))

    def test_device_selection_updates_vad_monitor(self):
        app = object.__new__(LoggerApp)
        app.audio_device_var = tk.StringVar(value="7: Test Mic")
        app.saved_mic_device_name = None
        app.save_live_audio_settings = lambda: None
        seen = []
        app.set_vad_device = lambda device: seen.append(device)
        app._on_audio_device_selected()
        self.assertEqual(seen, [7])
        self.assertEqual(app.saved_mic_device_name, "Test Mic")


if __name__ == "__main__":
    unittest.main()
