"""Tests for console capture shutdown / Tk deadlock avoidance."""

from __future__ import annotations

import inspect
import threading
import tkinter as tk
import unittest

from phologtolabstreaminglayer.features.console_output_tk import ConsoleOutputFrame
from phologtolabstreaminglayer.logger_app import LoggerApp


class TestConsoleOutputShutdown(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.frame = ConsoleOutputFrame(
            self.root,
            self.root,
            capture_stdout=False,
            capture_stderr=False,
            initial_visible=False,
            pass_through=False,
        )

    def tearDown(self):
        try:
            self.frame.restore_streams()
        except Exception:
            pass
        self.root.destroy()

    def test_on_text_written_does_not_hold_update_lock_during_after(self):
        """Regression: holding _update_lock across root.after() deadlocks close.

        Background threads print while MainThread is in WM_DELETE_WINDOW /
        on_closing. If after() waits for the Tcl lock held by MainThread, and
        MainThread then prints and waits for _update_lock, the app hangs.
        """
        frame = self.frame
        lock_held_during_after: list[bool] = []

        def fake_after(_ms, _callback):
            acquired = frame._update_lock.acquire(blocking=False)
            lock_held_during_after.append(acquired)
            if acquired:
                frame._update_lock.release()

        frame._root.after = fake_after  # type: ignore[method-assign]
        frame._on_text_written("hello\n", "stdout")

        self.assertEqual(
            lock_held_during_after,
            [True],
            "after() must run without holding _update_lock",
        )

    def test_restore_streams_stops_scheduling_after(self):
        frame = self.frame
        after_calls: list[object] = []

        def fake_after(ms, callback):
            after_calls.append((ms, callback))

        frame._root.after = fake_after  # type: ignore[method-assign]
        frame.restore_streams()
        frame._on_text_written("should be ignored\n", "stdout")

        self.assertEqual(after_calls, [])
        self.assertTrue(frame._shutting_down)

    def test_on_closing_restores_console_before_stop_work(self):
        """on_closing must detach stdout capture before stop_* which print."""
        src = inspect.getsource(LoggerApp.on_closing)
        restore_idx = src.index("console_output_frame.restore_streams()")
        stop_transcription_idx = src.index("self.stop_live_transcription()")
        stop_recording_idx = src.index("self.stop_recording()")
        self.assertLess(restore_idx, stop_transcription_idx)
        self.assertLess(restore_idx, stop_recording_idx)

    def test_concurrent_writes_during_blocked_after_do_not_deadlock(self):
        """Worker blocked in after(); another writer must not wait on _update_lock."""
        frame = self.frame
        after_entered = threading.Event()
        release_after = threading.Event()
        second_write_done = threading.Event()

        def blocking_after(_ms, _callback):
            after_entered.set()
            release_after.wait(timeout=10.0)

        frame._root.after = blocking_after  # type: ignore[method-assign]

        worker = threading.Thread(
            target=lambda: frame._on_text_written("from-worker\n", "stdout"),
            daemon=True,
        )
        worker.start()
        self.assertTrue(after_entered.wait(timeout=2.0), "worker never entered after()")

        def second_write():
            frame._on_text_written("from-second\n", "stdout")
            second_write_done.set()

        second = threading.Thread(target=second_write, daemon=True)
        second.start()
        self.assertTrue(
            second_write_done.wait(timeout=1.0),
            "second write deadlocked: _update_lock held across after()",
        )

        release_after.set()
        worker.join(timeout=2.0)
        second.join(timeout=2.0)
        self.assertFalse(worker.is_alive())
        self.assertFalse(second.is_alive())


if __name__ == "__main__":
    unittest.main()
