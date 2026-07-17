"""Tests for XDF auto-start recording settings."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phologtolabstreaminglayer import logger_app as logger_app_mod
from phologtolabstreaminglayer.logger_app import LoggerApp


def _bare_app() -> LoggerApp:
    """Create an uninitialized LoggerApp shell for unit-testing helpers."""
    app = object.__new__(LoggerApp)
    app.auto_start_xdf_on_startup = True
    app.auto_start_attempted = False
    return app


class TestRecordingAutoStartSettings(unittest.TestCase):
    def test_default_recording_settings_enabled(self):
        app = _bare_app()
        self.assertEqual(app._default_recording_settings(), {"auto_start_on_startup": True})

    def test_load_recording_settings_missing_file_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            settings_file = Path(td) / "recording_settings.json"
            with patch.object(logger_app_mod, "_RECORDING_SETTINGS_FILE", settings_file):
                app = _bare_app()
                self.assertEqual(app.load_recording_settings(), {"auto_start_on_startup": True})

    def test_save_and_load_recording_settings_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            settings_file = Path(td) / "recording_settings.json"
            with patch.object(logger_app_mod, "_RECORDING_SETTINGS_FILE", settings_file):
                app = _bare_app()
                app.auto_start_xdf_on_startup = False
                app.save_recording_settings()

                self.assertTrue(settings_file.exists())
                data = json.loads(settings_file.read_text(encoding="utf-8"))
                self.assertEqual(data, {"auto_start_on_startup": False})

                app2 = _bare_app()
                loaded = app2.load_recording_settings()
                self.assertFalse(loaded["auto_start_on_startup"])
                app2._init_recording_settings()
                self.assertFalse(app2.auto_start_xdf_on_startup)

    def test_try_auto_start_skips_when_disabled(self):
        app = _bare_app()
        app.auto_start_xdf_on_startup = False
        started = {"count": 0}

        def _should_not_run():
            started["count"] += 1

        app.auto_start_recording = _should_not_run  # type: ignore[method-assign]
        app.is_lab_recorder_available = lambda: True  # type: ignore[method-assign]

        app._try_auto_start_after_stream_discovery()

        self.assertTrue(app.auto_start_attempted)
        self.assertEqual(started["count"], 0)

    def test_try_auto_start_runs_when_enabled(self):
        app = _bare_app()
        app.auto_start_xdf_on_startup = True
        started = {"count": 0}

        def _start():
            started["count"] += 1

        app.auto_start_recording = _start  # type: ignore[method-assign]
        app.is_lab_recorder_available = lambda: True  # type: ignore[method-assign]
        app.select_all_streams = lambda: None  # type: ignore[method-assign]
        app.get_selected_streams = lambda: ["TextLogger"]  # type: ignore[method-assign]

        app._try_auto_start_after_stream_discovery()

        self.assertTrue(app.auto_start_attempted)
        self.assertEqual(started["count"], 1)

    def test_try_auto_start_only_once(self):
        app = _bare_app()
        app.auto_start_xdf_on_startup = True
        started = {"count": 0}

        def _start():
            started["count"] += 1

        app.auto_start_recording = _start  # type: ignore[method-assign]
        app.is_lab_recorder_available = lambda: True  # type: ignore[method-assign]
        app.select_all_streams = lambda: None  # type: ignore[method-assign]
        app.get_selected_streams = lambda: ["TextLogger"]  # type: ignore[method-assign]

        app._try_auto_start_after_stream_discovery()
        app._try_auto_start_after_stream_discovery()

        self.assertEqual(started["count"], 1)


if __name__ == "__main__":
    unittest.main()
