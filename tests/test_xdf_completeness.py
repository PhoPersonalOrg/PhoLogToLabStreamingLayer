import os
import tempfile
import time
import unittest

import pylsl
import pyxdf
from labrecorder import LabRecorder


class TestXdfCompleteness(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.temp_xdf_dir = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_xdf_completeness_spec(self):
        """
        Test suite for testing XDF recording completeness against the official OpenSpec requirements.
        This simulates recording and checks the generated file structure.
        """
        # Create LSL streams based on the spec

        # 1. TextLogger stream
        info_text = pylsl.StreamInfo('TextLogger', 'Markers', 1, 0, 'string', 'textlogger_001')
        outlet_text = pylsl.StreamOutlet(info_text)

        # 2. EventBoard stream
        info_event = pylsl.StreamInfo('EventBoard', 'Markers', 1, 0, 'string', 'eventboard_001')
        outlet_event = pylsl.StreamOutlet(info_event)

        # Wait a bit for streams to be resolvable
        time.sleep(1.0)

        # Use labrecorder to record
        recorder = LabRecorder()
        xdf_filename = os.path.join(self.temp_xdf_dir, "test_recording.xdf")

        # Select our streams
        streams = recorder.find_streams()
        text_stream = next((s for s in streams if s.name() == 'TextLogger'), None)
        event_stream = next((s for s in streams if s.name() == 'EventBoard'), None)

        self.assertIsNotNone(text_stream, "TextLogger stream not found")
        self.assertIsNotNone(event_stream, "EventBoard stream not found")

        # Start recording
        recorder.start_recording(xdf_filename, [text_stream, event_stream])

        # Wait for recorder to start - IMPORTANT: allow labrecorder to open the file properly
        time.sleep(2.0)

        # Push samples corresponding to scenarios

        # TextLogger: Python sends text over LSL
        outlet_text.push_sample(['Hello from Python'])
        time.sleep(0.5)

        # EventBoard: Python emits instantaneous event
        outlet_event.push_sample(['TASK_START|Start Task|2024-01-15T10:30:45.123456'])
        time.sleep(0.5)

        # EventBoard: Python emits toggle start/end events
        outlet_event.push_sample(['FOCUS_MODE_START|Focus Mode|2024-01-15T10:30:45.123456|TOGGLE:True'])
        time.sleep(1.0)
        outlet_event.push_sample(['FOCUS_MODE_END|Focus Mode|2024-01-15T10:35:20.789012|TOGGLE:False'])
        time.sleep(1.0)

        # Stop recording - give it time to flush everything
        recorder.stop_recording()
        time.sleep(2.0)

        # Destroy outlets to ensure streams close
        del outlet_text
        del outlet_event
        time.sleep(0.5)

        # Verify file exists
        self.assertTrue(os.path.exists(xdf_filename), f"XDF file was not created at {xdf_filename}")

        # Load and verify XDF structure using pyxdf
        loaded_streams, fileheader = pyxdf.load_xdf(xdf_filename)

        # We should have exactly 2 streams
        self.assertEqual(len(loaded_streams), 2, f"Expected 2 streams, found {len(loaded_streams)}")

        # Find the streams in the loaded data
        loaded_text_stream = next((s for s in loaded_streams if s['info']['name'][0] == 'TextLogger'), None)
        loaded_event_stream = next((s for s in loaded_streams if s['info']['name'][0] == 'EventBoard'), None)

        self.assertIsNotNone(loaded_text_stream, "TextLogger stream missing from XDF")
        self.assertIsNotNone(loaded_event_stream, "EventBoard stream missing from XDF")

        # Verify content of TextLogger stream
        self.assertGreaterEqual(len(loaded_text_stream['time_series']), 1)
        self.assertEqual(loaded_text_stream['time_series'][0][0], 'Hello from Python')
        self.assertEqual(loaded_text_stream['info']['type'][0], 'Markers')

        # Verify content of EventBoard stream
        self.assertGreaterEqual(len(loaded_event_stream['time_series']), 3)
        event_messages = [item[0] for item in loaded_event_stream['time_series']]

        self.assertIn('TASK_START|Start Task|2024-01-15T10:30:45.123456', event_messages)
        self.assertIn('FOCUS_MODE_START|Focus Mode|2024-01-15T10:30:45.123456|TOGGLE:True', event_messages)
        self.assertIn('FOCUS_MODE_END|Focus Mode|2024-01-15T10:35:20.789012|TOGGLE:False', event_messages)

    def test_xdf_reliability_missing_stream(self):
        """
        Test recording reliability when a stream is missing.
        """
        recorder = LabRecorder()
        xdf_filename = os.path.join(self.temp_xdf_dir, "test_reliability.xdf")

        # Start recording with no streams - should raise an exception
        with self.assertRaises(RuntimeError):
            recorder.start_recording(xdf_filename, [])

        # Start recording with one stream, then have it disconnect
        info_temp = pylsl.StreamInfo('TempStream', 'Markers', 1, 0, 'string', 'temp_001')
        outlet_temp = pylsl.StreamOutlet(info_temp)
        time.sleep(1.0)

        streams = recorder.find_streams()
        temp_stream = next((s for s in streams if s.name() == 'TempStream'), None)

        recorder.start_recording(xdf_filename, [temp_stream])
        time.sleep(1.0)

        # Disconnect stream
        del outlet_temp
        time.sleep(1.0)

        # Should stop gracefully
        recorder.stop_recording()

        # Verify file was still created
        self.assertTrue(os.path.exists(xdf_filename))


if __name__ == "__main__":
    unittest.main()
