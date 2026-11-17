"""Dual-recorder XDF self-test with isolated random LSL streams.

Creates multiple randomly named outlets covering string markers, continuous
float32, and irregular int16 — each with ``phopylslhelper`` sync metadata and
channel/custom desc fields — then records them in parallel with:

1. **Testing writer** — in-process ``labrecorder.LabRecorder`` (Python)
2. **Ground-truth writer** — App-LabRecorder ``LabRecorderCLI`` (name predicates)

After both writers close, asserts stream-set isolation, metadata round-trip,
and sample agreement via ``scripts.compare_two_xdfs``.

Usage::

    .\\.venv\\Scripts\\python.exe scripts\\dual_recorder_self_test.py
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

import numpy as np
import pylsl
import pyxdf

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DURATION_S = 16.0
DEFAULT_SRATE_HZ = 20.0
FLOAT_CHANNEL_COUNT = 3
INT16_CHANNEL_COUNT = 2
MARKER_PUSH_INTERVAL_S = 0.15

# Fallback CLI locations when sibling-of-LabRecorder.exe is missing (single-file builds).
# Prefer the VS Release build tree (ships next to lsl.dll); install/bin may be incomplete.
_CLI_FALLBACKS = (
    Path(
        r"C:/Users/pho/repos/EmotivEpoc/ACTIVE_DEV/App-LabRecorder/"
        r"out/build/win-vs-release/Release/LabRecorderCLI.exe"
    ),
    Path(
        r"C:/Users/pho/repos/EmotivEpoc/ACTIVE_DEV/App-LabRecorder/"
        r"out/install/win-vs-release/bin/LabRecorderCLI.exe"
    ),
)

MARKER_ENTRIES = [
    "Session started: dual XDF self-test",
    "Event: stimulus_onset",
    "Note: custom metadata check",
    "Event: stimulus_offset",
    "Session ended: dual XDF self-test",
]


def _load_compare_module():
    """Load compare_two_xdfs.py without requiring scripts/ to be a package."""
    path = REPO_ROOT / "scripts" / "compare_two_xdfs.py"
    mod_name = "compare_two_xdfs"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load compare module from {path}")
    mod = importlib.util.module_from_spec(spec)
    # dataclasses need the module registered before exec_module
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def resolve_labrecorder_cli(cli_arg: Optional[str] = None) -> Path:
    """Resolve LabRecorderCLI.exe from CLI, env, sibling of LabRecorder.exe, or fallbacks."""
    if cli_arg:
        return Path(cli_arg)
    env_cli = os.environ.get("LABRECORDER_CLI")
    if env_cli:
        return Path(env_cli)

    try:
        from labrecorder.launch_and_control_external_cpp_labrecorder_app import (
            ExternalLabRecorderInstance,
        )

        gui_exe = ExternalLabRecorderInstance.resolve_exe_path()
        sibling = gui_exe.parent / "LabRecorderCLI.exe"
        if sibling.is_file():
            return sibling
    except Exception:
        pass

    for candidate in _CLI_FALLBACKS:
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(
        "LabRecorderCLI.exe not found. Set LABRECORDER_CLI or pass --labrecorder-cli. "
        f"Tried sibling of LABRECORDER_EXE and: {[str(p) for p in _CLI_FALLBACKS]}"
    )


def _readable_dt_str(dt: datetime) -> str:
    try:
        from phopylslhelper.general_helpers import readable_dt_str

        return readable_dt_str(dt)
    except Exception:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %I:%M:%S %p")


def attach_custom_metadata(
    info: pylsl.StreamInfo,
    *,
    token: str,
    stream_kind: str,
    stream_start_dt: datetime,
    stream_start_lsl: float,
    extra: Optional[Dict[str, str]] = None,
) -> None:
    """Embed manufacturer/desc + phopylslhelper sync + dual-test custom tags."""
    desc = info.desc()
    desc.append_child_value("manufacturer", "dual_recorder_self_test")
    desc.append_child_value(
        "description", f"Isolated dual-recorder self-test ({stream_kind})"
    )
    custom = desc.append_child("dual_recorder_self_test")
    custom.append_child_value("token", token)
    custom.append_child_value("stream_kind", stream_kind)
    custom.append_child_value("schema", "1")
    if extra:
        for k, v in extra.items():
            custom.append_child_value(k, v)

    phopylsl = desc.append_child("phopylslhelper")
    phopylsl.append_child_value("version", "1.0.3")
    phopylsl.append_child_value(
        "stream_start_datetime", _readable_dt_str(stream_start_dt)
    )
    phopylsl.append_child_value(
        "stream_start_lsl_local_offset_seconds", str(stream_start_lsl)
    )


def _desc_first(stream: Dict[str, Any]) -> Dict[str, Any]:
    desc_list = stream.get("info", {}).get("desc") or [{}]
    raw = desc_list[0] if desc_list else {}
    return dict(raw) if isinstance(raw, dict) else {}


def _unwrap(value: Any) -> Any:
    if isinstance(value, list) and len(value) == 1:
        return _unwrap(value[0])
    return value


def _child_dict(desc: Dict[str, Any], key: str) -> Dict[str, Any]:
    raw = _unwrap(desc.get(key, {}))
    return dict(raw) if isinstance(raw, dict) else {}


def _child_value(node: Dict[str, Any], key: str) -> Optional[str]:
    if key not in node:
        return None
    return str(_unwrap(node[key]))


def channel_labels_from_stream(stream: Dict[str, Any]) -> List[str]:
    desc = _desc_first(stream)
    channels = _unwrap(desc.get("channels"))
    if not isinstance(channels, dict):
        return []
    ch_list = channels.get("channel", [])
    if isinstance(ch_list, dict):
        ch_list = [ch_list]
    labels: List[str] = []
    for ch in ch_list or []:
        if not isinstance(ch, dict):
            continue
        lab = ch.get("label")
        if lab is not None:
            labels.append(str(_unwrap(lab)))
    return labels


class MarkerOutlet:
    """Irregular string marker outlet."""

    def __init__(
        self,
        stream_name: str,
        source_id: str,
        *,
        token: str,
        stream_start_dt: datetime,
        stream_start_lsl: float,
    ) -> None:
        self.stream_name = stream_name
        self.source_id = source_id
        info = pylsl.StreamInfo(
            name=stream_name,
            type="Markers",
            channel_count=1,
            nominal_srate=pylsl.IRREGULAR_RATE,
            channel_format=pylsl.cf_string,
            source_id=source_id,
        )
        attach_custom_metadata(
            info,
            token=token,
            stream_kind="markers",
            stream_start_dt=stream_start_dt,
            stream_start_lsl=stream_start_lsl,
            extra={"channel_format": "string"},
        )
        channels = info.desc().append_child("channels")
        ch = channels.append_child("channel")
        ch.append_child_value("label", "msg")
        ch.append_child_value("type", "Marker")
        self.outlet = pylsl.StreamOutlet(info)
        self.pushed: List[str] = []
        self.pushed_timestamps: List[float] = []

    def push_entries(
        self, entries: Sequence[str], interval: float = MARKER_PUSH_INTERVAL_S
    ) -> None:
        for entry in entries:
            ts = pylsl.local_clock()
            self.outlet.push_sample([entry], timestamp=ts)
            self.pushed.append(entry)
            self.pushed_timestamps.append(ts)
            time.sleep(interval)

    def close(self) -> None:
        self.outlet = None  # type: ignore[assignment]


class FloatOutlet:
    """Continuous float32 outlet with deterministic ramp."""

    def __init__(
        self,
        stream_name: str,
        source_id: str,
        *,
        token: str,
        stream_start_dt: datetime,
        stream_start_lsl: float,
        channel_count: int = FLOAT_CHANNEL_COUNT,
        nominal_srate: float = DEFAULT_SRATE_HZ,
    ) -> None:
        self.stream_name = stream_name
        self.source_id = source_id
        self.channel_count = channel_count
        self.nominal_srate = nominal_srate
        info = pylsl.StreamInfo(
            name=stream_name,
            type="EEG",
            channel_count=channel_count,
            nominal_srate=nominal_srate,
            channel_format=pylsl.cf_float32,
            source_id=source_id,
        )
        attach_custom_metadata(
            info,
            token=token,
            stream_kind="float32_continuous",
            stream_start_dt=stream_start_dt,
            stream_start_lsl=stream_start_lsl,
            extra={"channel_format": "float32", "nominal_srate": str(nominal_srate)},
        )
        channels = info.desc().append_child("channels")
        for i in range(channel_count):
            ch = channels.append_child("channel")
            ch.append_child_value("label", f"Fz{i}" if i == 0 else f"ch{i}")
            ch.append_child_value("unit", "uV")
            ch.append_child_value("type", "EEG")
        self.outlet = pylsl.StreamOutlet(info)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.pushed_samples: List[List[float]] = []
        self.pushed_timestamps: List[float] = []

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._producer, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def close(self) -> None:
        self.stop()
        self.outlet = None  # type: ignore[assignment]

    def _producer(self) -> None:
        interval = 1.0 / self.nominal_srate
        counter = 0
        while not self._stop.is_set():
            sample = [float(counter + c) for c in range(self.channel_count)]
            ts = pylsl.local_clock()
            self.outlet.push_sample(sample, timestamp=ts)
            self.pushed_samples.append(sample)
            self.pushed_timestamps.append(ts)
            counter += 1
            self._stop.wait(interval)


class Int16Outlet:
    """Irregular int16 outlet co-pushed with marker entries."""

    def __init__(
        self,
        stream_name: str,
        source_id: str,
        *,
        token: str,
        stream_start_dt: datetime,
        stream_start_lsl: float,
        channel_count: int = INT16_CHANNEL_COUNT,
    ) -> None:
        self.stream_name = stream_name
        self.source_id = source_id
        self.channel_count = channel_count
        info = pylsl.StreamInfo(
            name=stream_name,
            type="Markers",
            channel_count=channel_count,
            nominal_srate=pylsl.IRREGULAR_RATE,
            channel_format=pylsl.cf_int16,
            source_id=source_id,
        )
        attach_custom_metadata(
            info,
            token=token,
            stream_kind="int16_irregular",
            stream_start_dt=stream_start_dt,
            stream_start_lsl=stream_start_lsl,
            extra={"channel_format": "int16"},
        )
        channels = info.desc().append_child("channels")
        for label in ("index", "payload_len"):
            ch = channels.append_child("channel")
            ch.append_child_value("label", label)
            ch.append_child_value("type", "Marker")
        self.outlet = pylsl.StreamOutlet(info)
        self.pushed_samples: List[List[int]] = []
        self.pushed_timestamps: List[float] = []

    def push_entry(self, idx: int, entry: str, ts: float) -> None:
        sample = [idx % 32768, len(entry) % 32768]
        self.outlet.push_sample(sample, timestamp=ts)
        self.pushed_samples.append(sample)
        self.pushed_timestamps.append(ts)

    def close(self) -> None:
        self.outlet = None  # type: ignore[assignment]


def start_labrecorder_cli(
    cli_path: Path,
    xdf_path: Path,
    stream_names: Sequence[str],
) -> subprocess.Popen:
    """Start LabRecorderCLI recording only the named streams; returns the process."""
    predicates = [f'name="{name}"' for name in stream_names]
    cmd = [str(cli_path), str(xdf_path), *predicates]
    print(f"  [CLI] Starting: {cmd}")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(cli_path.parent),
    )
    return proc


def wait_cli_recording_started(proc: subprocess.Popen, timeout_s: float = 20.0) -> None:
    """Block until CLI prints that recording started, or raise on early exit."""
    deadline = time.time() + timeout_s
    assert proc.stdout is not None
    lines: List[str] = []
    while time.time() < deadline:
        if proc.poll() is not None:
            rest = proc.stdout.read() or ""
            raise RuntimeError(
                "LabRecorderCLI exited early "
                f"(code={proc.returncode}). Output:\n" + "\n".join(lines) + rest
            )
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.05)
            continue
        line = line.rstrip("\n")
        lines.append(line)
        print(f"  [CLI] {line}")
        if "Starting the recording" in line:
            return
    raise TimeoutError(
        "Timed out waiting for LabRecorderCLI to start recording. "
        f"Output so far:\n" + "\n".join(lines)
    )


def stop_labrecorder_cli(proc: subprocess.Popen, timeout_s: float = 30.0) -> int:
    """Send Enter to CLI stdin so it finalizes the XDF, then wait."""
    try:
        if proc.stdin is not None and proc.poll() is None:
            proc.stdin.write("\n")
            proc.stdin.flush()
            proc.stdin.close()
    except (BrokenPipeError, OSError) as exc:
        print(f"  [CLI] Warning writing stdin: {exc}")
    try:
        out, _ = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate(timeout=5.0)
        print("  [CLI] Killed after timeout waiting for exit")
        return proc.returncode or 1
    if out:
        for line in out.splitlines():
            print(f"  [CLI] {line}")
    print(f"  [CLI] Exited with code {proc.returncode}")
    return int(proc.returncode or 0)


def start_python_recorder(xdf_path: Path, stream_names: Set[str]):
    """Find the named streams and start in-process LabRecorder."""
    from labrecorder import LabRecorder

    recorder = LabRecorder(filename=str(xdf_path), enable_remote_control=False)
    recorder.config.set("streams.watch_for_new_streams", False)
    streams = recorder.find_streams(timeout=5.0)
    target = [s for s in streams if s.name() in stream_names]
    found = {s.name() for s in target}
    missing = stream_names - found
    if missing:
        recorder.cleanup()
        raise RuntimeError(f"Python LabRecorder missing streams: {sorted(missing)}")
    print(f"  [Python] Found {sorted(found)}; starting recording -> {xdf_path}")
    recorder.start_recording(filename=str(xdf_path), streams=target)
    return recorder


def load_streams_by_name(path: Path) -> Dict[str, Dict[str, Any]]:
    streams, _ = pyxdf.load_xdf(
        str(path),
        synchronize_clocks=True,
        handle_clock_resets=True,
        dejitter_timestamps=False,
        verbose=False,
    )
    out: Dict[str, Dict[str, Any]] = {}
    for s in streams:
        name = str(s["info"]["name"][0])
        out[name] = s
    return out


def assert_stream_set(path: Path, expected_names: Set[str]) -> Dict[str, Dict[str, Any]]:
    by_name = load_streams_by_name(path)
    got = set(by_name)
    if got != expected_names:
        raise AssertionError(
            f"{path.name}: expected streams {sorted(expected_names)}, got {sorted(got)}"
        )
    return by_name


def assert_metadata_roundtrip(
    by_name: Dict[str, Dict[str, Any]],
    *,
    label: str,
    token: str,
    expected_kinds: Dict[str, str],
    expected_channel_labels: Dict[str, List[str]],
) -> None:
    """Verify custom + phopylslhelper desc fields survived into the XDF."""
    print(f"\n--- Metadata check ({label}) ---")
    for name, kind in expected_kinds.items():
        stream = by_name[name]
        desc = _desc_first(stream)
        manufacturer = _child_value(desc, "manufacturer")
        if manufacturer != "dual_recorder_self_test":
            raise AssertionError(
                f"{label}/{name}: manufacturer={manufacturer!r}, "
                f"expected 'dual_recorder_self_test'"
            )

        custom = _child_dict(desc, "dual_recorder_self_test")
        got_token = _child_value(custom, "token")
        got_kind = _child_value(custom, "stream_kind")
        if got_token != token:
            raise AssertionError(
                f"{label}/{name}: custom token={got_token!r}, expected {token!r}"
            )
        if got_kind != kind:
            raise AssertionError(
                f"{label}/{name}: stream_kind={got_kind!r}, expected {kind!r}"
            )

        phopylsl = _child_dict(desc, "phopylslhelper")
        sdt = _child_value(phopylsl, "stream_start_datetime")
        soff = _child_value(phopylsl, "stream_start_lsl_local_offset_seconds")
        ver = _child_value(phopylsl, "version")
        if not sdt:
            raise AssertionError(f"{label}/{name}: missing phopylslhelper stream_start_datetime")
        if soff is None:
            raise AssertionError(
                f"{label}/{name}: missing phopylslhelper stream_start_lsl_local_offset_seconds"
            )
        if ver != "1.0.3":
            raise AssertionError(f"{label}/{name}: phopylslhelper version={ver!r}")

        # Round-trip through EasyTimeSyncParsingMixin
        try:
            from phopylslhelper.easy_time_sync import (
                EasyTimeSyncParsingMixin,
                lsl_stream_timestamps_to_unix_seconds,
            )

            parsed = EasyTimeSyncParsingMixin.parse_and_add_lsl_outlet_info_from_desc(
                desc_info_dict=desc,
                stream_info_dict={},
                should_fail_on_missing=False,
                should_return_datetime_timezone_UTC=True,
            )
            if parsed.get("stream_start_datetime") is None:
                raise AssertionError(
                    f"{label}/{name}: EasyTimeSyncParsingMixin failed to parse stream_start_datetime"
                )
            ts = np.asarray(stream.get("time_stamps", []), dtype=float)
            if ts.size:
                unix = lsl_stream_timestamps_to_unix_seconds(stream, ts[:1])
                if not np.isfinite(unix[0]):
                    raise AssertionError(f"{label}/{name}: unix conversion non-finite")
        except ImportError:
            print(f"  [{label}] phopylslhelper not importable — skip parse check")

        labels = channel_labels_from_stream(stream)
        expected_labels = expected_channel_labels[name]
        if labels != expected_labels:
            raise AssertionError(
                f"{label}/{name}: channel labels {labels!r} != {expected_labels!r}"
            )

        fmt = str(stream["info"].get("channel_format", ["?"])[0])
        stype = str(stream["info"]["type"][0])
        n = int(np.asarray(stream.get("time_stamps", [])).size)
        print(
            f"  {name}: kind={got_kind} type={stype} fmt={fmt} n={n} "
            f"labels={labels} sync_dt={sdt!r} sync_off={soff}"
        )


def assert_marker_payloads(
    test_by_name: Dict[str, Dict[str, Any]],
    gt_by_name: Dict[str, Dict[str, Any]],
    marker_name: str,
    expected: Sequence[str],
) -> None:
    def _samples(stream: Dict[str, Any]) -> List[str]:
        out: List[str] = []
        for row in stream.get("time_series") or []:
            if isinstance(row, (list, tuple, np.ndarray)):
                out.append(str(row[0]))
            else:
                out.append(str(row))
        return out

    test_msgs = _samples(test_by_name[marker_name])
    gt_msgs = _samples(gt_by_name[marker_name])
    if test_msgs != list(expected):
        raise AssertionError(f"Test markers {test_msgs!r} != expected {list(expected)!r}")
    if gt_msgs != list(expected):
        raise AssertionError(f"GT markers {gt_msgs!r} != expected {list(expected)!r}")
    print(f"  Marker payloads match ({len(expected)} entries)")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Record randomly named multi-type LSL streams with Python LabRecorder "
            "and LabRecorderCLI in parallel, then compare XDFs and metadata."
        )
    )
    p.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_S,
        help=f"Seconds to push continuous samples (default: {DEFAULT_DURATION_S})",
    )
    p.add_argument(
        "--outdir",
        type=Path,
        default=None,
        help="Output directory for XDFs (default: system temp dir)",
    )
    p.add_argument(
        "--labrecorder-cli",
        type=Path,
        default=None,
        help="Path to LabRecorderCLI.exe (or set LABRECORDER_CLI)",
    )
    p.add_argument(
        "--tol-ms",
        type=float,
        default=1.0,
        help="Timestamp match tolerance forwarded to compare_two_xdfs (default: 1.0)",
    )
    p.add_argument(
        "--srate",
        type=float,
        default=DEFAULT_SRATE_HZ,
        help=f"Nominal float stream sample rate Hz (default: {DEFAULT_SRATE_HZ})",
    )
    p.add_argument(
        "--keep-files",
        action="store_true",
        help="Keep XDFs even when using the default temp outdir",
    )
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    token = uuid.uuid4().hex[:8]
    marker_name = f"PhoXdfDualTest_{token}_Markers"
    float_name = f"PhoXdfDualTest_{token}_Float"
    int16_name = f"PhoXdfDualTest_{token}_Int16"
    stream_names = {marker_name, float_name, int16_name}
    expected_kinds = {
        marker_name: "markers",
        float_name: "float32_continuous",
        int16_name: "int16_irregular",
    }
    expected_channel_labels = {
        marker_name: ["msg"],
        float_name: ["Fz0", "ch1", "ch2"],
        int16_name: ["index", "payload_len"],
    }

    tmp_ctx = None
    if args.outdir is not None:
        outdir = args.outdir.resolve()
        outdir.mkdir(parents=True, exist_ok=True)
    else:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="dual_recorder_self_test_")
        outdir = Path(tmp_ctx.name)

    test_xdf = outdir / f"dual_test_{stamp}.xdf"
    gt_xdf = outdir / f"dual_gt_{stamp}.xdf"

    print("=" * 70)
    print("  Dual-recorder XDF self-test (multi-type + metadata)")
    print(f"  {datetime.now().isoformat()}")
    print("=" * 70)
    print(f"  Token:       {token}")
    print(f"  Streams:     {sorted(stream_names)}")
    print(f"  Duration:    {args.duration:g}s @ {args.srate:g} Hz (float)")
    print(f"  Test XDF:    {test_xdf}")
    print(f"  GT XDF:      {gt_xdf}")

    try:
        cli_path = resolve_labrecorder_cli(
            str(args.labrecorder_cli) if args.labrecorder_cli else None
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if tmp_ctx is not None:
            tmp_ctx.cleanup()
        return 2
    print(f"  LabRecorderCLI: {cli_path}")

    marker_outlet: Optional[MarkerOutlet] = None
    float_outlet: Optional[FloatOutlet] = None
    int16_outlet: Optional[Int16Outlet] = None
    cli_proc: Optional[subprocess.Popen] = None
    recorder = None
    exit_code = 1

    try:
        stream_start_dt = datetime.now(timezone.utc)
        stream_start_lsl = float(pylsl.local_clock())

        print("\n--- Creating LSL outlets ---")
        marker_outlet = MarkerOutlet(
            marker_name,
            f"pho_xdf_dual_{token}_markers",
            token=token,
            stream_start_dt=stream_start_dt,
            stream_start_lsl=stream_start_lsl,
        )
        float_outlet = FloatOutlet(
            float_name,
            f"pho_xdf_dual_{token}_float",
            token=token,
            stream_start_dt=stream_start_dt,
            stream_start_lsl=stream_start_lsl,
            nominal_srate=float(args.srate),
        )
        int16_outlet = Int16Outlet(
            int16_name,
            f"pho_xdf_dual_{token}_int16",
            token=token,
            stream_start_dt=stream_start_dt,
            stream_start_lsl=stream_start_lsl,
        )
        print("  Waiting for outlets to become discoverable ...")
        time.sleep(1.5)

        print("\n--- Starting LabRecorderCLI (GT) ---")
        cli_proc = start_labrecorder_cli(cli_path, gt_xdf, sorted(stream_names))
        wait_cli_recording_started(cli_proc)
        time.sleep(0.5)

        print("\n--- Starting Python LabRecorder (test) ---")
        recorder = start_python_recorder(test_xdf, stream_names)
        time.sleep(0.3)

        print(f"\n--- Pushing samples for {args.duration:g}s ---")
        float_outlet.start()
        # Interleave markers + int16 during the continuous window
        t0 = time.time()
        for idx, entry in enumerate(MARKER_ENTRIES):
            ts = pylsl.local_clock()
            marker_outlet.outlet.push_sample([entry], timestamp=ts)
            marker_outlet.pushed.append(entry)
            marker_outlet.pushed_timestamps.append(ts)
            int16_outlet.push_entry(idx, entry, ts)
            print(f"  [Marker] {entry!r}")
            remaining = float(args.duration) - (time.time() - t0)
            if remaining <= 0:
                break
            time.sleep(min(MARKER_PUSH_INTERVAL_S, max(0.0, remaining / 2)))
        # Finish remaining continuous duration
        elapsed = time.time() - t0
        if elapsed < float(args.duration):
            time.sleep(float(args.duration) - elapsed)
        float_outlet.stop()
        print(
            f"  Pushed markers={len(marker_outlet.pushed)} "
            f"float={len(float_outlet.pushed_samples)} "
            f"int16={len(int16_outlet.pushed_samples)}"
        )
        time.sleep(0.5)

        print("\n--- Stopping Python LabRecorder ---")
        recorder.stop_recording()
        recorder.cleanup()
        recorder = None
        time.sleep(0.3)

        print("\n--- Stopping LabRecorderCLI ---")
        cli_rc = stop_labrecorder_cli(cli_proc)
        cli_proc = None
        if cli_rc not in (0, None):
            print(f"  WARNING: LabRecorderCLI return code {cli_rc}")

        print("\n--- Closing outlets ---")
        marker_outlet.close()
        float_outlet.close()
        int16_outlet.close()
        marker_outlet = float_outlet = int16_outlet = None

        if not test_xdf.is_file() or test_xdf.stat().st_size == 0:
            raise RuntimeError(f"Test XDF missing or empty: {test_xdf}")
        if not gt_xdf.is_file() or gt_xdf.stat().st_size == 0:
            raise RuntimeError(f"GT XDF missing or empty: {gt_xdf}")

        print(f"\n  Test XDF size: {test_xdf.stat().st_size} bytes")
        print(f"  GT XDF size:   {gt_xdf.stat().st_size} bytes")

        print("\n--- Isolation check (exact stream set) ---")
        test_by_name = assert_stream_set(test_xdf, stream_names)
        gt_by_name = assert_stream_set(gt_xdf, stream_names)
        print(f"  Both files contain exactly {sorted(stream_names)}")

        assert_metadata_roundtrip(
            test_by_name,
            label="TEST",
            token=token,
            expected_kinds=expected_kinds,
            expected_channel_labels=expected_channel_labels,
        )
        assert_metadata_roundtrip(
            gt_by_name,
            label="GT",
            token=token,
            expected_kinds=expected_kinds,
            expected_channel_labels=expected_channel_labels,
        )

        print("\n--- Marker payload check ---")
        n_mark = len(test_by_name[marker_name]["time_stamps"])
        expected_markers = list(MARKER_ENTRIES[:n_mark])
        if n_mark < len(MARKER_ENTRIES):
            print(
                f"  NOTE: captured {n_mark}/{len(MARKER_ENTRIES)} markers "
                "(duration truncated mid-push)"
            )
        assert_marker_payloads(test_by_name, gt_by_name, marker_name, expected_markers)

        print("\n--- Comparing XDFs ---")
        compare = _load_compare_module()
        compare_rc = compare.main(
            [
                "--test-xdf",
                str(test_xdf),
                "--gt-xdf",
                str(gt_xdf),
                "--streams",
                ",".join(sorted(stream_names)),
                "--tol-ms",
                str(args.tol_ms),
            ]
        )
        if compare_rc != 0:
            print("COMPARE FAIL", file=sys.stderr)
            exit_code = 1
        else:
            print("\nSELF-TEST PASS")
            exit_code = 0

        if args.outdir is not None or args.keep_files:
            print(f"\n  Kept files in: {outdir}")
        elif exit_code != 0 and tmp_ctx is not None:
            keep = Path(tempfile.gettempdir()) / f"dual_recorder_fail_{stamp}"
            keep.mkdir(parents=True, exist_ok=True)
            import shutil

            for p in (test_xdf, gt_xdf):
                if p.is_file():
                    shutil.copy2(p, keep / p.name)
            print(f"\n  Failure artifacts copied to: {keep}")

    except Exception as exc:
        print(f"\nSELF-TEST ERROR: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        for outlet in (marker_outlet, float_outlet, int16_outlet):
            if outlet is not None:
                try:
                    outlet.close()
                except Exception:
                    pass
        if recorder is not None:
            try:
                if getattr(recorder, "is_recording", lambda: False)():
                    recorder.stop_recording()
                recorder.cleanup()
            except Exception:
                pass
        if cli_proc is not None and cli_proc.poll() is None:
            try:
                stop_labrecorder_cli(cli_proc)
            except Exception:
                try:
                    cli_proc.kill()
                except Exception:
                    pass
        if tmp_ctx is not None and exit_code == 0 and not args.keep_files:
            tmp_ctx.cleanup()
        elif tmp_ctx is not None and (exit_code != 0 or args.keep_files):
            if args.keep_files:
                print(f"  Temp outdir kept: {outdir}")
            else:
                tmp_ctx.cleanup()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
