"""Tests for Live Audio preferred-mic selection (C920 webcam) and persistence."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from whisper_timestamped.mixins.live_whisper_transcription import (
    LiveWhisperTranscriptionAppMixin,
)

from phologtolabstreaminglayer import logger_app as logger_app_mod
from phologtolabstreaminglayer.logger_app import (
    LoggerApp,
    audio_device_name_from_label,
    preferred_audio_device_label,
)


# Labels matching the Live Audio dropdown screenshot (C920 appears twice).
_SCREENSHOT_DEVICE_LIST = [
    "Default",
    "0: Microsoft Sound Mapper - Input",
    "1: Microphone (Jabra Evolve2 30)",
    "2: Microphone (Virtual Desktop Aud",
    "3: Microphone (HD Pro Webcam C920)",
    "9: Primary Sound Capture Driver",
    "10: Microphone (Jabra Evolve2 30)",
    "11: Microphone (Virtual Desktop Audio)",
    "12: Microphone (HD Pro Webcam C920)",
    "22: Microphone (Virtual Desktop Audio)",
]


class TestPreferredAudioDeviceLabel(unittest.TestCase):
    def test_picks_first_c920_from_screenshot_list(self):
        self.assertEqual(
            preferred_audio_device_label(_SCREENSHOT_DEVICE_LIST),
            "3: Microphone (HD Pro Webcam C920)",
        )

    def test_falls_back_to_default_when_c920_absent(self):
        device_list = [
            "Default",
            "0: Microsoft Sound Mapper - Input",
            "1: Microphone (Jabra Evolve2 30)",
        ]
        self.assertEqual(preferred_audio_device_label(device_list), "Default")

    def test_empty_list_falls_back_to_default(self):
        self.assertEqual(preferred_audio_device_label([]), "Default")


class TestAudioDeviceNameFromLabel(unittest.TestCase):
    def test_strips_index_prefix(self):
        self.assertEqual(
            audio_device_name_from_label("3: Microphone (HD Pro Webcam C920)"),
            "Microphone (HD Pro Webcam C920)",
        )

    def test_passes_default_through(self):
        self.assertEqual(audio_device_name_from_label("Default"), "Default")


class TestRefreshAudioDevicesPrefersC920(unittest.TestCase):
    def _bare_app(
        self,
        device_list=_SCREENSHOT_DEVICE_LIST,
        current="Default",
        saved_mic_device_name=None,
    ) -> LoggerApp:
        app = object.__new__(LoggerApp)
        app.audio_device_var = MagicMock()
        app.audio_device_var.get.return_value = current
        app.audio_device_combo = {"values": tuple(device_list)}
        app.saved_mic_device_name = saved_mic_device_name
        return app

    def test_overrides_default_selection_with_first_c920(self):
        app = self._bare_app()
        with patch.object(LiveWhisperTranscriptionAppMixin, "refresh_audio_devices"):
            app.refresh_audio_devices()

        app.audio_device_var.set.assert_called_once_with(
            "3: Microphone (HD Pro Webcam C920)"
        )

    def test_keeps_non_default_user_selection(self):
        app = self._bare_app(current="1: Microphone (Jabra Evolve2 30)")
        with patch.object(LiveWhisperTranscriptionAppMixin, "refresh_audio_devices"):
            app.refresh_audio_devices()

        app.audio_device_var.set.assert_not_called()

    def test_falls_back_to_default_when_c920_missing(self):
        app = self._bare_app(
            device_list=[
                "Default",
                "0: Microsoft Sound Mapper - Input",
                "1: Microphone (Jabra Evolve2 30)",
            ]
        )
        with patch.object(LiveWhisperTranscriptionAppMixin, "refresh_audio_devices"):
            app.refresh_audio_devices()

        app.audio_device_var.set.assert_called_once_with("Default")

    def test_saved_default_stays_default_even_when_c920_present(self):
        app = self._bare_app(saved_mic_device_name="Default")
        with patch.object(LiveWhisperTranscriptionAppMixin, "refresh_audio_devices"):
            app.refresh_audio_devices()

        app.audio_device_var.set.assert_called_once_with("Default")

    def test_saved_jabra_name_picks_first_matching_label(self):
        app = self._bare_app(saved_mic_device_name="Microphone (Jabra Evolve2 30)")
        with patch.object(LiveWhisperTranscriptionAppMixin, "refresh_audio_devices"):
            app.refresh_audio_devices()

        app.audio_device_var.set.assert_called_once_with(
            "1: Microphone (Jabra Evolve2 30)"
        )

    def test_saved_name_absent_falls_back_to_c920(self):
        app = self._bare_app(saved_mic_device_name="Microphone (Some Other Mic)")
        with patch.object(LiveWhisperTranscriptionAppMixin, "refresh_audio_devices"):
            app.refresh_audio_devices()

        app.audio_device_var.set.assert_called_once_with(
            "3: Microphone (HD Pro Webcam C920)"
        )

    def test_saved_name_and_c920_absent_falls_back_to_default(self):
        app = self._bare_app(
            device_list=[
                "Default",
                "0: Microsoft Sound Mapper - Input",
            ],
            saved_mic_device_name="Microphone (Some Other Mic)",
        )
        with patch.object(LiveWhisperTranscriptionAppMixin, "refresh_audio_devices"):
            app.refresh_audio_devices()

        app.audio_device_var.set.assert_called_once_with("Default")


class TestLiveAudioSettingsRoundtrip(unittest.TestCase):
    def _bare_app(self) -> LoggerApp:
        app = object.__new__(LoggerApp)
        app.saved_mic_device_name = None
        return app

    def test_missing_file_defaults_to_unset(self):
        with tempfile.TemporaryDirectory() as td:
            settings_file = Path(td) / "live_audio_settings.json"
            with patch.object(logger_app_mod, "_LIVE_AUDIO_SETTINGS_FILE", settings_file):
                app = self._bare_app()
                self.assertEqual(app.load_live_audio_settings(), {"mic_device_name": None})

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            settings_file = Path(td) / "live_audio_settings.json"
            with patch.object(logger_app_mod, "_LIVE_AUDIO_SETTINGS_FILE", settings_file):
                app = self._bare_app()
                app.saved_mic_device_name = "Microphone (Jabra Evolve2 30)"
                app.save_live_audio_settings()

                self.assertTrue(settings_file.exists())
                data = json.loads(settings_file.read_text(encoding="utf-8"))
                self.assertEqual(
                    data, {"mic_device_name": "Microphone (Jabra Evolve2 30)"}
                )

                app2 = self._bare_app()
                loaded = app2.load_live_audio_settings()
                self.assertEqual(
                    loaded["mic_device_name"], "Microphone (Jabra Evolve2 30)"
                )
                app2._init_live_audio_settings()
                self.assertEqual(
                    app2.saved_mic_device_name, "Microphone (Jabra Evolve2 30)"
                )

    def test_save_and_load_default_sentinel(self):
        with tempfile.TemporaryDirectory() as td:
            settings_file = Path(td) / "live_audio_settings.json"
            with patch.object(logger_app_mod, "_LIVE_AUDIO_SETTINGS_FILE", settings_file):
                app = self._bare_app()
                app.saved_mic_device_name = "Default"
                app.save_live_audio_settings()

                app2 = self._bare_app()
                app2._init_live_audio_settings()
                self.assertEqual(app2.saved_mic_device_name, "Default")

    def test_on_audio_device_selected_persists_name_not_index(self):
        with tempfile.TemporaryDirectory() as td:
            settings_file = Path(td) / "live_audio_settings.json"
            with patch.object(logger_app_mod, "_LIVE_AUDIO_SETTINGS_FILE", settings_file):
                app = self._bare_app()
                app.audio_device_var = MagicMock()
                app.audio_device_var.get.return_value = (
                    "3: Microphone (HD Pro Webcam C920)"
                )
                app._on_audio_device_selected()

                self.assertEqual(
                    app.saved_mic_device_name, "Microphone (HD Pro Webcam C920)"
                )
                data = json.loads(settings_file.read_text(encoding="utf-8"))
                self.assertEqual(
                    data, {"mic_device_name": "Microphone (HD Pro Webcam C920)"}
                )
