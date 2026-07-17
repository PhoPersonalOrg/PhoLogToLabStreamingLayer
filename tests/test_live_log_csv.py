"""Tests for LiveLogCsvWriter continuous background CSV append."""

from __future__ import annotations

import csv
import tempfile
import time
import unittest
from pathlib import Path

from phologtolabstreaminglayer.features.live_log_csv import LiveLogCsvWriter, default_live_log_csv_dir


def _wait_for(predicate, timeout_s: float = 2.0, interval_s: float = 0.05) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return False


class TestLiveLogCsv(unittest.TestCase):
    def test_default_live_log_csv_dir(self):
        self.assertEqual(default_live_log_csv_dir(Path("E:/logs")), Path("E:/logs/CSV"))

    def test_continuous_append_and_flush(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp_path = Path(self._tmpdir.name)

        out = tmp_path / "CSV"
        writer = LiveLogCsvWriter(enabled=True, output_dir=out)
        writer.start(out)

        self.assertTrue(
            _wait_for(lambda: writer.session_path is not None and writer.session_path.exists())
        )

        writer.append("2026-07-16 15:00:00", "manual hello")
        writer.append("2026-07-16 15:00:01", "EventBoard: Go (EVENT_GO)")
        writer.append("2026-07-16 15:00:02", "[TRANSCRIBED] Start recording.")

        def _has_three_data_rows() -> bool:
            path = writer.session_path
            if path is None or not path.exists():
                return False
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            return len(rows) >= 4 and rows[0] == ["Timestamp", "Message"]

        self.assertTrue(_wait_for(_has_three_data_rows))

        with open(writer.session_path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[1][1], "manual hello")
        self.assertEqual(rows[2][1], "EventBoard: Go (EVENT_GO)")
        self.assertEqual(rows[3][1], "[TRANSCRIBED] Start recording.")

        writer.stop()

    def test_disable_stops_new_rows_then_reenable(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp_path = Path(self._tmpdir.name)

        out = tmp_path / "CSV"
        writer = LiveLogCsvWriter(enabled=True, output_dir=out)
        writer.start(out)
        self.assertTrue(_wait_for(lambda: writer.session_path is not None))

        writer.append("t1", "before disable")
        self.assertTrue(
            _wait_for(lambda: writer.session_path.read_text(encoding="utf-8").count("before disable") == 1)
        )

        writer.set_enabled(False)
        time.sleep(0.3)
        writer.append("t2", "while disabled")
        time.sleep(0.3)
        text = writer.session_path.read_text(encoding="utf-8")
        self.assertNotIn("while disabled", text)

        writer.set_enabled(True)
        self.assertTrue(
            _wait_for(lambda: writer.session_path is not None and writer.session_path.exists())
        )
        writer.append("t3", "after reenable")
        self.assertTrue(
            _wait_for(lambda: "after reenable" in writer.session_path.read_text(encoding="utf-8"))
        )

        writer.stop()

    def test_reconfigure_writes_to_new_directory(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp_path = Path(self._tmpdir.name)

        out1 = tmp_path / "A"
        out2 = tmp_path / "B"
        writer = LiveLogCsvWriter(enabled=True, output_dir=out1)
        writer.start(out1)
        self.assertTrue(_wait_for(lambda: writer.session_path is not None))

        writer.append("t1", "in A")
        self.assertTrue(_wait_for(lambda: "in A" in writer.session_path.read_text(encoding="utf-8")))
        path_a = writer.session_path

        writer.reconfigure(out2)
        self.assertTrue(
            _wait_for(lambda: writer.session_path is not None and writer.session_path.parent == out2)
        )

        writer.append("t2", "in B")
        self.assertTrue(_wait_for(lambda: "in B" in writer.session_path.read_text(encoding="utf-8")))
        self.assertTrue(path_a.exists())
        self.assertNotIn("in B", path_a.read_text(encoding="utf-8"))

        writer.stop()


if __name__ == "__main__":
    unittest.main()
