"""Tests for auto-selecting newly discovered LSL streams and attaching while recording."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from phologtolabstreaminglayer.logger_app import LoggerApp


class _FakeStreamInfo:
    def __init__(self, name: str, source_id: str = "src"):
        self._name = name
        self._source_id = source_id

    def name(self) -> str:
        return self._name

    def source_id(self) -> str:
        return self._source_id


def _discovery_app() -> LoggerApp:
    app = object.__new__(LoggerApp)
    app.auto_add_new_streams = True
    app.recording = False
    app._shutting_down = False
    app._stream_discovery_lock = threading.Lock()
    app.discovered_streams = {}
    app.selected_streams = set()
    app._attach_lock = threading.Lock()
    app.lab_recorder = None
    app.lab_recorder_service_client = None
    app.xdf_filename = r"C:\tmp\20260101_000000_log.xdf"
    app._next_split_datetime = None
    app.system_tray = None
    return app


class TestStreamAutoAdd(unittest.TestCase):
    def test_new_keys_are_auto_selected(self):
        app = _discovery_app()
        info_a = _FakeStreamInfo("TextLogger")
        new_keys, infos = app._apply_discovered_streams({"TextLogger_src": info_a})
        self.assertEqual(new_keys, {"TextLogger_src"})
        self.assertIn("TextLogger_src", app.selected_streams)
        self.assertEqual(infos, [info_a])

    def test_user_deselected_stream_is_not_reselected(self):
        app = _discovery_app()
        info_a = _FakeStreamInfo("EEG")
        app._apply_discovered_streams({"EEG_src": info_a})
        app.selected_streams.discard("EEG_src")

        new_keys, infos = app._apply_discovered_streams({"EEG_src": info_a})
        self.assertEqual(new_keys, set())
        self.assertEqual(infos, [])
        self.assertNotIn("EEG_src", app.selected_streams)

    def test_new_stream_auto_selected_while_old_deselected_stays_off(self):
        app = _discovery_app()
        info_a = _FakeStreamInfo("TextLogger")
        info_b = _FakeStreamInfo("EEG")
        app._apply_discovered_streams({"TextLogger_src": info_a})
        app.selected_streams.discard("TextLogger_src")

        new_keys, infos = app._apply_discovered_streams(
            {"TextLogger_src": info_a, "EEG_src": info_b}
        )
        self.assertEqual(new_keys, {"EEG_src"})
        self.assertNotIn("TextLogger_src", app.selected_streams)
        self.assertIn("EEG_src", app.selected_streams)
        self.assertEqual(infos, [info_b])

    def test_auto_add_disabled_does_not_select_or_attach_infos(self):
        app = _discovery_app()
        app.auto_add_new_streams = False
        info_a = _FakeStreamInfo("EEG")
        new_keys, infos = app._apply_discovered_streams({"EEG_src": info_a})
        self.assertEqual(new_keys, {"EEG_src"})
        self.assertEqual(infos, [])
        self.assertNotIn("EEG_src", app.selected_streams)

    def test_disconnected_stream_removed_from_selection(self):
        app = _discovery_app()
        info_a = _FakeStreamInfo("EEG")
        app._apply_discovered_streams({"EEG_src": info_a})
        self.assertIn("EEG_src", app.selected_streams)
        new_keys, infos = app._apply_discovered_streams({})
        self.assertEqual(new_keys, set())
        self.assertEqual(infos, [])
        self.assertNotIn("EEG_src", app.selected_streams)

    def test_schedule_attach_skipped_when_not_recording(self):
        app = _discovery_app()
        app.recording = False
        started = {"n": 0}

        class _FakeThread:
            def __init__(self, *args, **kwargs):
                started["n"] += 1

            def start(self):
                pass

        with patch("phologtolabstreaminglayer.logger_app.threading.Thread", _FakeThread):
            app._schedule_attach_new_streams([_FakeStreamInfo("EEG")])
        self.assertEqual(started["n"], 0)

    def test_schedule_attach_starts_worker_while_recording(self):
        app = _discovery_app()
        app.recording = True
        started = {"n": 0}

        class _FakeThread:
            def __init__(self, *args, **kwargs):
                started["n"] += 1

            def start(self):
                pass

        with patch("phologtolabstreaminglayer.logger_app.threading.Thread", _FakeThread):
            app._schedule_attach_new_streams([_FakeStreamInfo("EEG")])
        self.assertEqual(started["n"], 1)

    def test_attach_in_process_calls_labrecorder_attach_streams(self):
        app = _discovery_app()
        app.recording = True
        attached = {"infos": None}

        class _FakeRecorder:
            def is_recording(self):
                return True

            def attach_streams(self, stream_infos):
                attached["infos"] = list(stream_infos)
                return len(stream_infos)

        app.lab_recorder = _FakeRecorder()
        app.is_lab_recorder_service_available = lambda: False  # type: ignore[method-assign]
        app.root = type("Root", (), {"after": staticmethod(lambda _ms, fn: fn())})()
        app.update_log_display = lambda *args, **kwargs: None  # type: ignore[method-assign]
        app.send_lsl_message = lambda *args, **kwargs: None  # type: ignore[method-assign]

        info = _FakeStreamInfo("EEG")
        app._attach_streams_to_open_recording([info])
        self.assertEqual(attached["infos"], [info])

    def test_attach_rcs_sends_update_and_select_all(self):
        app = _discovery_app()
        app.recording = True
        commands = []

        class _FakeClient:
            def send_command(self, command):
                commands.append(command)

        app.lab_recorder_service_client = _FakeClient()
        app.is_lab_recorder_service_available = lambda: True  # type: ignore[method-assign]
        app.root = type("Root", (), {"after": staticmethod(lambda _ms, fn: fn())})()
        app.update_log_display = lambda *args, **kwargs: None  # type: ignore[method-assign]
        app.send_lsl_message = lambda *args, **kwargs: None  # type: ignore[method-assign]

        with patch("phologtolabstreaminglayer.logger_app.time.sleep", lambda _s: None):
            app._attach_streams_to_open_recording([_FakeStreamInfo("EEG")])
        self.assertEqual(commands, ["update", "select all"])

    def test_attach_not_invoked_when_not_recording(self):
        app = _discovery_app()
        app.recording = False
        called = {"n": 0}

        class _FakeRecorder:
            def is_recording(self):
                return False

            def attach_streams(self, stream_infos):
                called["n"] += 1

        app.lab_recorder = _FakeRecorder()
        app._attach_streams_to_open_recording([_FakeStreamInfo("EEG")])
        self.assertEqual(called["n"], 0)

    def test_split_recording_rotates_in_process(self):
        app = _discovery_app()
        app.recording = True
        app._split_in_progress = False
        app._shutting_down = False
        rotated = {"path": None}

        class _FakeRecorder:
            def is_recording(self):
                return True

            def rotate_file(self, path):
                rotated["path"] = path

        class _DummyWidget:
            def config(self, **kwargs):
                return None

        app.lab_recorder = _FakeRecorder()
        app.is_lab_recorder_service_available = lambda: False  # type: ignore[method-assign]
        app._common_initiate_recording = lambda allow_prompt_user_for_filename=False: (r"C:\tmp\new.xdf", (None, None))  # type: ignore[method-assign]
        app.xdf_filename = r"C:\tmp\new.xdf"
        app.send_lsl_message = lambda *args, **kwargs: None  # type: ignore[method-assign]
        app.update_log_display = lambda *args, **kwargs: None  # type: ignore[method-assign]
        app.start_taskbar_overlay_flash = lambda: None  # type: ignore[method-assign]
        app._schedule_auto_split = lambda: None  # type: ignore[method-assign]
        app._update_tray_tooltip = lambda: None  # type: ignore[method-assign]
        app.recording_status_label = _DummyWidget()
        app.start_recording_button = _DummyWidget()
        app.stop_recording_button = _DummyWidget()
        app.split_recording_button = _DummyWidget()
        app.status_info_label = _DummyWidget()

        app.split_recording()
        self.assertEqual(rotated["path"], r"C:\tmp\new.xdf")
        self.assertTrue(app.recording)
        self.assertFalse(app._split_in_progress)

    def test_split_recording_skipped_when_idle(self):
        app = _discovery_app()
        app.recording = False
        app._split_in_progress = False
        called = {"n": 0}
        app._rotate_in_process_recording = lambda: called.__setitem__("n", called["n"] + 1)  # type: ignore[method-assign]
        app.split_recording()
        self.assertEqual(called["n"], 0)

    def test_recording_status_title_idle_and_recording(self):
        app = _discovery_app()
        self.assertEqual(app._recording_status_title(), "LSL Logger: Not recording")
        app.recording = True
        app.selected_streams = {"TextLogger_src", "EEG_src"}
        title = app._recording_status_title()
        self.assertIn("Recording 2 streams", title)
        self.assertIn("20260101_000000_log.xdf", title)


if __name__ == "__main__":
    unittest.main()
