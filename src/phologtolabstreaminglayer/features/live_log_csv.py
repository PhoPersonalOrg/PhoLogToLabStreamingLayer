# Live Log History CSV writer (background queue + flush)
# Copyright (C) 2025 Pho Hale. All rights reserved.

"""Continuously append Log History lines to a plaintext CSV on a background thread."""

from __future__ import annotations

import csv
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Union

# Sentinel to stop the writer thread
_STOP = object()

_QUEUE_MAXSIZE = 2000


def default_live_log_csv_dir(xdf_folder: Path) -> Path:
    """Default output directory for live log CSV files."""
    return Path(xdf_folder) / "CSV"


class LiveLogCsvWriter:
    """Append Timestamp/Message rows to a session CSV via a background writer thread."""

    def __init__(self, enabled: bool = True, output_dir: Optional[Path] = None):
        self._enabled = bool(enabled)
        self._output_dir = Path(output_dir) if output_dir is not None else None
        self._queue: "queue.Queue[Union[Tuple[str, str], object]]" = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._thread: Optional[threading.Thread] = None
        self._file = None
        self._csv_writer = None
        self._session_path: Optional[Path] = None
        self._session_name: Optional[str] = None
        self._started = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def output_dir(self) -> Optional[Path]:
        return self._output_dir

    @property
    def session_path(self) -> Optional[Path]:
        return self._session_path

    @property
    def expected_session_path(self) -> Optional[Path]:
        """Path the current session file should use (even before the writer opens it)."""
        if self._output_dir is None or self._session_name is None:
            return None
        return self._output_dir / f"live_log_{self._session_name}.csv"

    def start(self, output_dir: Optional[Path] = None) -> Optional[Path]:
        """Start the writer thread and open a session file if enabled."""
        if output_dir is not None:
            self._output_dir = Path(output_dir)
        if self._session_name is None:
            self._session_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        if self._thread is not None and self._thread.is_alive():
            if self._enabled and self._output_dir is not None:
                self._request_reconfigure(self._output_dir, True)
            return self._session_path

        self._started = True
        self._thread = threading.Thread(target=self._writer_loop, name="LiveLogCsvWriter", daemon=True)
        self._thread.start()
        if self._enabled and self._output_dir is not None:
            self._request_reconfigure(self._output_dir, True)
        return self._session_path

    def append(self, timestamp: str, message: str) -> None:
        """Enqueue a row when enabled. Never blocks the caller."""
        if not self._enabled or not self._started:
            return
        try:
            self._queue.put_nowait((timestamp, message))
        except queue.Full:
            print("Warning: LiveLogCsvWriter queue full; dropping log CSV row")

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable appending. When disabled, drains then closes the file."""
        enabled = bool(enabled)
        self._enabled = enabled
        if not self._started:
            return
        self._request_reconfigure(None, enabled)

    def reconfigure(self, output_dir: Path) -> None:
        """Switch output directory; opens a new session file under the new dir."""
        self._output_dir = Path(output_dir)
        if not self._started:
            return
        if self._enabled:
            self._request_reconfigure(self._output_dir, True)

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the writer thread and close the file."""
        if not self._started:
            return
        self._started = False
        self._enabled = False
        try:
            self._queue.put(_STOP)
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _request_reconfigure(self, output_dir: Optional[Path], enabled: Optional[bool]) -> None:
        try:
            self._queue.put_nowait(("__reconfigure__", output_dir, enabled))
        except queue.Full:
            print("Warning: LiveLogCsvWriter queue full; reconfigure dropped")

    def _open_session_file(self, output_dir: Path) -> None:
        self._close_file()
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        if self._session_name is None:
            self._session_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = output_dir / f"live_log_{self._session_name}.csv"
        write_header = not path.exists() or path.stat().st_size == 0
        self._file = open(path, "a", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._file)
        if write_header:
            self._csv_writer.writerow(["Timestamp", "Message"])
            self._file.flush()
        self._session_path = path
        self._output_dir = output_dir

    def _close_file(self) -> None:
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except Exception as e:
                print(f"Warning: LiveLogCsvWriter close failed: {e}")
            self._file = None
            self._csv_writer = None

    def _writer_loop(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if item is _STOP:
                self._close_file()
                break

            if isinstance(item, tuple) and len(item) == 3 and item[0] == "__reconfigure__":
                _, output_dir, enabled = item
                if enabled is not None:
                    self._enabled = bool(enabled)
                if not self._enabled:
                    self._close_file()
                    continue
                if output_dir is not None:
                    try:
                        self._open_session_file(Path(output_dir))
                    except Exception as e:
                        print(f"Error opening live log CSV: {e}")
                        self._close_file()
                elif self._file is None and self._output_dir is not None:
                    try:
                        self._open_session_file(self._output_dir)
                    except Exception as e:
                        print(f"Error opening live log CSV: {e}")
                continue

            # Data rows already in the queue are drained even after disable; append() gates new rows.
            if self._csv_writer is None or self._file is None:
                continue

            timestamp, message = item
            try:
                self._csv_writer.writerow([timestamp, message])
                self._file.flush()
            except Exception as e:
                print(f"Error writing live log CSV row: {e}")
