import pytest
import pyxdf
import os
import tempfile
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pylsl
from labrecorder import LabRecorder

@pytest.fixture
def temp_xdf_dir():
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir

def test_xdf_completeness_spec(temp_xdf_dir):
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
    xdf_filename = os.path.join(temp_xdf_dir, "test_recording.xdf")

    # Select our streams
    streams = recorder.find_streams()
    text_stream = next((s for s in streams if s.name() == 'TextLogger'), None)
    event_stream = next((s for s in streams if s.name() == 'EventBoard'), None)

    assert text_stream is not None, "TextLogger stream not found"
    assert event_stream is not None, "EventBoard stream not found"

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
    assert os.path.exists(xdf_filename), f"XDF file was not created at {xdf_filename}"

    # Load and verify XDF structure using pyxdf
    loaded_streams, fileheader = pyxdf.load_xdf(xdf_filename)

    # We should have exactly 2 streams
    assert len(loaded_streams) == 2, f"Expected 2 streams, found {len(loaded_streams)}"

    # Find the streams in the loaded data
    loaded_text_stream = next((s for s in loaded_streams if s['info']['name'][0] == 'TextLogger'), None)
    loaded_event_stream = next((s for s in loaded_streams if s['info']['name'][0] == 'EventBoard'), None)

    assert loaded_text_stream is not None, "TextLogger stream missing from XDF"
    assert loaded_event_stream is not None, "EventBoard stream missing from XDF"

    # Verify content of TextLogger stream
    assert len(loaded_text_stream['time_series']) >= 1
    assert loaded_text_stream['time_series'][0][0] == 'Hello from Python'
    assert loaded_text_stream['info']['type'][0] == 'Markers'

    # Verify content of EventBoard stream
    assert len(loaded_event_stream['time_series']) >= 3
    event_messages = [item[0] for item in loaded_event_stream['time_series']]

    assert 'TASK_START|Start Task|2024-01-15T10:30:45.123456' in event_messages
    assert 'FOCUS_MODE_START|Focus Mode|2024-01-15T10:30:45.123456|TOGGLE:True' in event_messages
    assert 'FOCUS_MODE_END|Focus Mode|2024-01-15T10:35:20.789012|TOGGLE:False' in event_messages

def test_xdf_reliability_missing_stream(temp_xdf_dir):
    """
    Test recording reliability when a stream is missing.
    """
    recorder = LabRecorder()
    xdf_filename = os.path.join(temp_xdf_dir, "test_reliability.xdf")

    # Start recording with no streams - should raise an exception
    with pytest.raises(RuntimeError):
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
    assert os.path.exists(xdf_filename)
