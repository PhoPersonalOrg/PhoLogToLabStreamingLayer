"""Tests for crash-recoverable WAV writing and XDF-tied raw audio helpers."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from phologtolabstreaminglayer.features.raw_audio import (
    RawAudioRecorder,
    RecoverableWavWriter,
    format_audio_file_started,
    format_audio_file_stopped,
    wav_path_for_xdf_filename,
)
from phologtolabstreaminglayer.logger_app import LoggerApp
from whisper_timestamped.audio_capture import MicCaptureHub


class TestWavPath(unittest.TestCase):
    def test_wav_path_strips_log_suffix(self):
        xdf = Path(r"E:\logs\20261030_112615_log.xdf")
        wav = wav_path_for_xdf_filename(xdf)
        self.assertEqual(wav.name, "20261030_112615_audio.wav")
        self.assertEqual(wav.parent.name, "raw_audio")
        self.assertEqual(wav.parent.parent, xdf.parent)

    def test_markers_include_clocks(self):
        started = format_audio_file_started(
            {
                "path": r"C:\logs\raw_audio\a.wav",
                "sample_rate": 16000,
                "channels": 1,
                "fmt": "PCM_16",
                "start_lsl_clock": 12.5,
            }
        )
        self.assertIn("AUDIO_FILE_STARTED:", started)
        self.assertIn("sr=16000", started)
        self.assertIn("lsl_start=12.5", started)
        stopped = format_audio_file_stopped(
            {
                "path": r"C:\logs\raw_audio\a.wav",
                "frames_written": 16000,
                "sample_rate": 16000,
                "stop_lsl_clock": 13.0,
            }
        )
        self.assertIn("AUDIO_FILE_STOPPED:", stopped)
        self.assertIn("frames=16000", stopped)
        self.assertIn("duration_s=1.000", stopped)
        self.assertIn("lsl_stop=13.0", stopped)


class TestRecoverableWavWriter(unittest.TestCase):
    def test_abandon_without_finalize_is_playable(self):
        n = 8000
        samples = np.linspace(-0.5, 0.5, n, dtype=np.float32)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "clip.wav"
            writer = RecoverableWavWriter(path, sample_rate=16000, header_patch_interval_s=0.0)
            writer.write_float32(samples)
            sidecar_before = json.loads(writer.sidecar_path.read_text(encoding="utf-8"))
            self.assertFalse(sidecar_before["complete"])
            writer.detach_for_crash_test()

            with wave.open(str(path), "rb") as wf:
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getframerate(), 16000)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertEqual(wf.getnframes(), n)

            sidecar = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertFalse(sidecar["complete"])
            self.assertEqual(sidecar["frames_written"], n)

    def test_clean_close_marks_sidecar_complete(self):
        samples = np.zeros(100, dtype=np.float32)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "clip.wav"
            writer = RecoverableWavWriter(path, sample_rate=16000, header_patch_interval_s=0.0)
            writer.write_float32(samples)
            info = writer.close(complete=True)
            self.assertTrue(info["complete"])
            sidecar = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertTrue(sidecar["complete"])
            self.assertEqual(sidecar["frames_written"], 100)
            with wave.open(str(path), "rb") as wf:
                self.assertEqual(wf.getnframes(), 100)


def _bare_app() -> LoggerApp:
    app = object.__new__(LoggerApp)
    app.auto_start_xdf_on_startup = True
    app.auto_add_new_streams = True
    app.auto_split_minutes = 30
    app.record_raw_audio = True
    app.recording = False
    app._shutting_down = False
    app._raw_audio_recorder = None
    app.xdf_filename = None
    app.transcription_config = None
    app.root = None
    app.system_tray = None
    app._next_split_datetime = None
    app._stream_discovery_lock = threading.Lock()
    app.selected_streams = set()
    return app


class _FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


class TestRawAudioRecorder(unittest.TestCase):
    def test_first_block_emits_started_and_stop_finalizes(self):
        hub = MicCaptureHub(stream_factory=lambda **kw: _FakeStream(**kw))
        started = []
        rec = RawAudioRecorder(hub=hub, clock_fn=lambda: 1.25, on_started=started.append)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sess_audio.wav"
            rec.start(path, header_patch_interval_s=0.0)
            rec._on_hub_audio(np.ones(160, dtype=np.float32))
            deadline = time.time() + 2.0
            while time.time() < deadline and len(started) == 0:
                time.sleep(0.02)

            self.assertEqual(len(started), 1)
            self.assertEqual(started[0]["start_lsl_clock"], 1.25)
            info = rec.stop()
            self.assertIsNotNone(info)
            self.assertGreaterEqual(info["frames_written"], 160)
            self.assertTrue(info["complete"])
            with wave.open(str(path), "rb") as wf:
                self.assertGreaterEqual(wf.getnframes(), 160)
            sidecar = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertTrue(sidecar["complete"])


class TestLoggerAppRawAudioHooks(unittest.TestCase):
    def test_start_skipped_when_disabled(self):
        app = _bare_app()
        app.record_raw_audio = False
        app.xdf_filename = str(Path("C:/tmp/20260101_000000_log.xdf"))
        started = {"n": 0}

        class Rec:
            is_recording = False
            path = None

            def start(self, *a, **k):
                started["n"] += 1

        app._raw_audio_recorder = Rec()
        app._start_raw_audio_if_enabled()
        self.assertEqual(started["n"], 0)

    def test_start_calls_recorder_with_matching_stem(self):
        app = _bare_app()
        app.record_raw_audio = True
        with tempfile.TemporaryDirectory() as td:
            xdf = Path(td) / "20261030_112615_log.xdf"
            xdf.write_bytes(b"")
            app.xdf_filename = str(xdf)
            started = []

            class Rec:
                is_recording = False
                path = None

                def start(self, path, **kwargs):
                    self.is_recording = True
                    self.path = Path(path)
                    started.append(Path(path))
                    return self.path

            rec = Rec()
            app._raw_audio_recorder = rec
            app._refresh_raw_audio_status = lambda: None
            app._start_raw_audio_if_enabled()
            self.assertEqual(len(started), 1)
            self.assertEqual(started[0].name, "20261030_112615_audio.wav")
            self.assertEqual(started[0].parent.name, "raw_audio")

    def test_stop_emits_stopped_marker(self):
        app = _bare_app()
        messages = []
        logs = []

        class Rec:
            is_recording = True
            path = Path("clip.wav")

            def stop(self):
                self.is_recording = False
                return {
                    "path": r"C:\logs\raw_audio\clip.wav",
                    "frames_written": 32000,
                    "sample_rate": 16000,
                    "channels": 1,
                    "stop_lsl_clock": 9.0,
                }

        app._raw_audio_recorder = Rec()
        app.send_lsl_message = lambda m: messages.append(m)
        app.update_log_display = lambda m, ts=None: logs.append(m)
        app._refresh_raw_audio_status = lambda: None
        app._update_tray_tooltip = lambda: None
        app._stop_raw_audio()
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0].startswith("AUDIO_FILE_STOPPED:"))
        self.assertIn("frames=32000", messages[0])
        self.assertEqual(logs, messages)

    def test_rotate_stops_then_starts(self):
        app = _bare_app()
        order = []
        app._stop_raw_audio = lambda: order.append("stop")
        app._start_raw_audio_if_enabled = lambda: order.append("start")
        app._common_initiate_recording = lambda allow_prompt_user_for_filename=False: (
            "new.xdf",
            (None, None),
        )
        app.xdf_filename = "old.xdf"
        app._shutting_down = True
        app.lab_recorder = type("R", (), {"rotate_file": staticmethod(lambda f: order.append("rotate"))})()
        app.update_log_display = lambda *a, **k: None
        app.send_lsl_message = lambda m: order.append("lsl")
        app.start_taskbar_overlay_flash = lambda: None
        app._rotate_in_process_recording()
        self.assertEqual(order[0], "stop")
        self.assertIn("rotate", order)
        self.assertEqual(order[-1], "start")
        self.assertLess(order.index("stop"), order.index("rotate"))
        self.assertLess(order.index("rotate"), order.index("start"))

    def test_unexpected_stop_finalizes_wav(self):
        app = _bare_app()
        stopped = {"n": 0}
        app._stop_raw_audio = lambda: stopped.__setitem__("n", stopped["n"] + 1)
        app.update_log_display = lambda *a, **k: None
        app._update_tray_tooltip = lambda: None
        app._on_recorder_stopped_unexpectedly()
        self.assertEqual(stopped["n"], 1)


class TestRecordingSettingsRawAudio(unittest.TestCase):
    def test_default_includes_record_raw_audio(self):
        from phologtolabstreaminglayer import logger_app as logger_app_mod

        app = _bare_app()
        defaults = app._default_recording_settings()
        self.assertTrue(defaults["record_raw_audio"])

    def test_roundtrip_record_raw_audio(self):
        from phologtolabstreaminglayer import logger_app as logger_app_mod

        with tempfile.TemporaryDirectory() as td:
            settings_file = Path(td) / "recording_settings.json"
            with patch.object(logger_app_mod, "_RECORDING_SETTINGS_FILE", settings_file):
                app = _bare_app()
                app.auto_start_xdf_on_startup = True
                app.auto_add_new_streams = True
                app.auto_split_minutes = 30
                app.record_raw_audio = False
                app.save_recording_settings()
                app2 = _bare_app()
                loaded = app2.load_recording_settings()
                self.assertFalse(loaded["record_raw_audio"])
                app2._init_recording_settings()
                self.assertFalse(app2.record_raw_audio)

    def test_legacy_file_fills_record_raw_audio(self):
        from phologtolabstreaminglayer import logger_app as logger_app_mod

        with tempfile.TemporaryDirectory() as td:
            settings_file = Path(td) / "recording_settings.json"
            settings_file.write_text('{"auto_start_on_startup": true}', encoding="utf-8")
            with patch.object(logger_app_mod, "_RECORDING_SETTINGS_FILE", settings_file):
                app = _bare_app()
                loaded = app.load_recording_settings()
                self.assertTrue(loaded["record_raw_audio"])


if __name__ == "__main__":
    unittest.main()
