# Crash-recoverable raw microphone WAV writer (follows XDF recording)
# Copyright (C) 2026 Pho Hale. All rights reserved.

"""16-bit PCM WAV with periodic RIFF header patches and an atomic sidecar JSON."""

from __future__ import annotations

import json
import os
import queue
import struct
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

import numpy as np

try:
    from pylsl import local_clock as _lsl_local_clock
except Exception:  # pragma: no cover - pylsl optional for unit tests
    def _lsl_local_clock() -> float:
        return time.time()


RAW_AUDIO_SUBDIR = "raw_audio"
PCM_FORMAT = "PCM_16"
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
SAMPWIDTH = 2
DEFAULT_HEADER_PATCH_INTERVAL_S = 1.0
_QUEUE_MAXSIZE = 100


def default_raw_audio_dir(xdf_folder: Union[str, Path]) -> Path:
    """Directory for raw WAVs next to an XDF folder."""
    return Path(xdf_folder) / RAW_AUDIO_SUBDIR


def wav_path_for_xdf_filename(xdf_filename: Union[str, Path]) -> Path:
    """Map `{YYYYMMDD_HHMMSS}_log.xdf` to `{parent}/raw_audio/{YYYYMMDD_HHMMSS}_audio.wav`."""
    xdf_path = Path(xdf_filename)
    stem = xdf_path.stem
    suffix = "_log"
    if stem.endswith(suffix):
        ts = stem[: -len(suffix)]
    else:
        ts = stem
    return default_raw_audio_dir(xdf_path.parent) / f"{ts}_audio.wav"


def format_audio_file_started(info: Dict[str, Any]) -> str:
    path = info.get("path", "")
    sr = int(info.get("sample_rate", DEFAULT_SAMPLE_RATE) or DEFAULT_SAMPLE_RATE)
    ch = int(info.get("channels", DEFAULT_CHANNELS) or DEFAULT_CHANNELS)
    fmt = info.get("fmt", PCM_FORMAT)
    lsl = info.get("start_lsl_clock")
    lsl_part = "" if lsl is None else f"|lsl_start={lsl}"
    return f"AUDIO_FILE_STARTED: {path}|sr={sr}|ch={ch}|fmt={fmt}{lsl_part}"


def format_audio_file_stopped(info: Dict[str, Any]) -> str:
    path = info.get("path", "")
    frames = int(info.get("frames_written", 0) or 0)
    sr = int(info.get("sample_rate", DEFAULT_SAMPLE_RATE) or DEFAULT_SAMPLE_RATE)
    duration = (frames / sr) if sr else 0.0
    lsl = info.get("stop_lsl_clock")
    lsl_part = "" if lsl is None else f"|lsl_stop={lsl}"
    return f"AUDIO_FILE_STOPPED: {path}|frames={frames}|duration_s={duration:.3f}{lsl_part}"


def _write_wav_header(file_obj, sample_rate: int, channels: int, sampwidth: int, data_size: int) -> None:
    byte_rate = sample_rate * channels * sampwidth
    block_align = channels * sampwidth
    riff_size = 36 + data_size
    file_obj.seek(0)
    file_obj.write(b"RIFF")
    file_obj.write(struct.pack("<I", riff_size))
    file_obj.write(b"WAVE")
    file_obj.write(b"fmt ")
    file_obj.write(struct.pack("<I", 16))
    file_obj.write(struct.pack("<HHIIHH", 1, channels, sample_rate, byte_rate, block_align, sampwidth * 8))
    file_obj.write(b"data")
    file_obj.write(struct.pack("<I", data_size))


class RecoverableWavWriter:
    """Write PCM_16 WAV and periodically patch RIFF sizes so a kill stays playable."""

    def __init__(
        self,
        path: Union[str, Path],
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        header_patch_interval_s: float = DEFAULT_HEADER_PATCH_INTERVAL_S,
        extra_meta: Optional[Dict[str, Any]] = None,
    ):
        self.path = Path(path)
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.sampwidth = SAMPWIDTH
        self.header_patch_interval_s = float(header_patch_interval_s)
        self.sidecar_path = self.path.with_suffix(".json")
        self._extra_meta: Dict[str, Any] = dict(extra_meta or {})
        self._lock = threading.Lock()
        self._frames_written = 0
        self._last_patch_monotonic = 0.0
        self._complete = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "wb")
        _write_wav_header(self._file, self.sample_rate, self.channels, self.sampwidth, 0)
        self._file.flush()
        self._write_sidecar(complete=False)

    @property
    def frames_written(self) -> int:
        return self._frames_written

    @property
    def bytes_written(self) -> int:
        return self._frames_written * self.channels * self.sampwidth

    def update_meta(self, **kwargs: Any) -> None:
        with self._lock:
            self._extra_meta.update(kwargs)
            self._write_sidecar(complete=self._complete)

    def write_float32(self, data: np.ndarray) -> None:
        if data is None:
            return
        arr = np.asarray(data)
        if arr.size == 0:
            return
        if arr.ndim == 2:
            arr = arr[:, 0]
        clipped = np.clip(arr.astype(np.float32, copy=False), -1.0, 1.0)
        pcm = (clipped * 32767.0).astype(np.int16)
        payload = pcm.tobytes()
        now = time.monotonic()
        with self._lock:
            if self._file is None:
                return
            self._file.seek(0, os.SEEK_END)
            self._file.write(payload)
            self._frames_written += int(pcm.size)
            interval = self.header_patch_interval_s
            if interval <= 0.0 or (now - self._last_patch_monotonic) >= interval:
                self._patch_header_unlocked()
                self._write_sidecar(complete=False)

    def patch_header(self) -> None:
        with self._lock:
            self._patch_header_unlocked()
            self._write_sidecar(complete=self._complete)

    def close(self, complete: bool = True) -> Dict[str, Any]:
        with self._lock:
            if self._file is not None:
                self._patch_header_unlocked()
                try:
                    self._file.flush()
                    os.fsync(self._file.fileno())
                except Exception:
                    pass
                try:
                    self._file.close()
                except Exception:
                    pass
                self._file = None
            self._complete = bool(complete)
            if complete:
                self._extra_meta.setdefault("stop_datetime", datetime.now().isoformat())
            self._write_sidecar(complete=self._complete)
            return self._snapshot_unlocked()

    def detach_for_crash_test(self) -> None:
        """Close the OS handle after a header patch without marking complete (kill analogue)."""
        with self._lock:
            self._patch_header_unlocked()
            if self._file is not None:
                self._file.flush()
                try:
                    os.fsync(self._file.fileno())
                except Exception:
                    pass
                self._file.close()
                self._file = None
            self._complete = False
            self._write_sidecar(complete=False)

    def _patch_header_unlocked(self) -> None:
        if self._file is None:
            return
        data_size = self._frames_written * self.channels * self.sampwidth
        _write_wav_header(self._file, self.sample_rate, self.channels, self.sampwidth, data_size)
        self._file.seek(0, os.SEEK_END)
        self._file.flush()
        self._last_patch_monotonic = time.monotonic()

    def _snapshot_unlocked(self) -> Dict[str, Any]:
        try:
            resolved = str(self.path.resolve())
        except Exception:
            resolved = str(self.path)
        snap: Dict[str, Any] = {
            "path": resolved,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "fmt": PCM_FORMAT,
            "frames_written": self._frames_written,
            "bytes_written": self.bytes_written,
            "complete": self._complete,
        }
        snap.update(self._extra_meta)
        return snap

    def _write_sidecar(self, complete: bool) -> None:
        payload = self._snapshot_unlocked()
        payload["complete"] = bool(complete)
        tmp = self.sidecar_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.sidecar_path)
        except Exception as e:
            print(f"Warning: raw audio sidecar write failed: {e}")
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass


class RawAudioRecorder:
    """Subscribe to MicCaptureHub and write a recoverable WAV on a background thread."""

    def __init__(
        self,
        hub: Optional[Any] = None,
        clock_fn: Optional[Callable[[], float]] = None,
        on_started: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self._hub = hub
        self._clock_fn = clock_fn or _lsl_local_clock
        self.on_started = on_started
        self._writer: Optional[RecoverableWavWriter] = None
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lsl_start: Optional[float] = None
        self._start_datetime: Optional[datetime] = None
        self.path: Optional[Path] = None
        self.sample_rate = DEFAULT_SAMPLE_RATE
        self.channels = DEFAULT_CHANNELS
        self._mic_device = None
        self._started_emitted = False

    @property
    def is_recording(self) -> bool:
        return self._writer is not None

    def start(
        self,
        path: Union[str, Path],
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        mic_device: Optional[Union[int, str]] = None,
        header_patch_interval_s: float = DEFAULT_HEADER_PATCH_INTERVAL_S,
    ) -> Path:
        if self.is_recording:
            return self.path  # type: ignore[return-value]
        self.path = Path(path)
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self._mic_device = mic_device
        self._stop_event.clear()
        self._lsl_start = None
        self._started_emitted = False
        self._start_datetime = datetime.now()
        self._queue = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._writer = RecoverableWavWriter(
            self.path,
            sample_rate=self.sample_rate,
            channels=self.channels,
            header_patch_interval_s=header_patch_interval_s,
            extra_meta={"start_datetime": self._start_datetime.isoformat()},
        )
        self._thread = threading.Thread(target=self._writer_loop, name="RawAudioWriter", daemon=True)
        self._thread.start()
        try:
            hub = self._require_hub()
            hub.subscribe(
                self._on_hub_audio,
                sample_rate=self.sample_rate,
                channels=self.channels,
                device=mic_device,
            )
        except Exception:
            self._stop_event.set()
            if self._thread is not None:
                self._thread.join(timeout=2.0)
                self._thread = None
            if self._writer is not None:
                self._writer.close(complete=False)
                self._writer = None
            raise
        return self.path

    def stop(self) -> Optional[Dict[str, Any]]:
        if not self.is_recording:
            return None
        hub = self._hub
        if hub is not None:
            try:
                hub.unsubscribe(self._on_hub_audio)
            except Exception:
                pass
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        stop_lsl = float(self._clock_fn())
        writer = self._writer
        self._writer = None
        info: Dict[str, Any] = {
            "path": str(self.path) if self.path is not None else "",
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "fmt": PCM_FORMAT,
            "frames_written": 0,
            "start_lsl_clock": self._lsl_start,
            "stop_lsl_clock": stop_lsl,
        }
        if writer is not None:
            writer.update_meta(stop_lsl_clock=stop_lsl, stop_datetime=datetime.now().isoformat())
            closed = writer.close(complete=True)
            info.update(closed)
            info["stop_lsl_clock"] = stop_lsl
        return info

    def _require_hub(self) -> Any:
        if self._hub is not None:
            return self._hub
        from whisper_timestamped.audio_capture import get_shared_mic_hub

        self._hub = get_shared_mic_hub()
        return self._hub

    def _on_hub_audio(self, data: np.ndarray) -> None:
        try:
            self._queue.put_nowait(np.asarray(data))
        except queue.Full:
            pass

    def _writer_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                data = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            self._handle_block(data)
        while True:
            try:
                data = self._queue.get_nowait()
            except queue.Empty:
                break
            self._handle_block(data)

    def _handle_block(self, data: np.ndarray) -> None:
        writer = self._writer
        if writer is None:
            return
        if not self._started_emitted:
            self._lsl_start = float(self._clock_fn())
            writer.update_meta(start_lsl_clock=self._lsl_start)
            self._started_emitted = True
            if self.on_started is not None:
                try:
                    self.on_started(self._started_info())
                except Exception as e:
                    print(f"Warning: raw audio on_started callback failed: {e}")
        writer.write_float32(data)

    def _started_info(self) -> Dict[str, Any]:
        path = ""
        if self.path is not None:
            try:
                path = str(self.path.resolve())
            except Exception:
                path = str(self.path)
        return {
            "path": path,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "fmt": PCM_FORMAT,
            "start_lsl_clock": self._lsl_start,
            "start_datetime": None if self._start_datetime is None else self._start_datetime.isoformat(),
        }
