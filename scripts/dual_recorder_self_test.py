"""Dual-recorder XDF self-test with an isolated random LSL stream.

Creates one randomly named continuous float32 outlet, records it in parallel with:

1. **Testing writer** — in-process ``labrecorder.LabRecorder`` (Python)
2. **Ground-truth writer** — App-LabRecorder ``LabRecorderCLI`` with a name predicate

Then closes the outlet and both writers, and compares the two XDF files via
``scripts.compare_two_xdfs``.

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
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

import pylsl
import pyxdf

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DURATION_S = 3.0
DEFAULT_SRATE_HZ = 20.0
CHANNEL_COUNT = 3

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


class RandomFloatOutlet:
    """Continuous float32 LSL outlet with a deterministic ramp payload."""

    def __init__(
        self,
        stream_name: str,
        source_id: str,
        *,
        channel_count: int = CHANNEL_COUNT,
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
        info.desc().append_child_value("manufacturer", "dual_recorder_self_test")
        info.desc().append_child_value("description", "Isolated dual-recorder self-test stream")
        channels = info.desc().append_child("channels")
        for i in range(channel_count):
            ch = channels.append_child("channel")
            ch.append_child_value("label", f"ch{i}")
            ch.append_child_value("unit", "a.u.")
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


def start_labrecorder_cli(
    cli_path: Path,
    xdf_path: Path,
    stream_name: str,
) -> subprocess.Popen:
    """Start LabRecorderCLI recording only the named stream; returns the process."""
    predicate = f'name="{stream_name}"'
    # Windows: pass predicate as a single argv so shell quoting is not needed.
    # cwd must be the CLI directory so adjacent lsl.dll / Qt deps resolve.
    cmd = [str(cli_path), str(xdf_path), predicate]
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


def wait_cli_recording_started(proc: subprocess.Popen, timeout_s: float = 15.0) -> None:
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


def start_python_recorder(xdf_path: Path, stream_name: str):
    """Find the named stream and start in-process LabRecorder."""
    from labrecorder import LabRecorder

    recorder = LabRecorder(filename=str(xdf_path), enable_remote_control=False)
    recorder.config.set("streams.watch_for_new_streams", False)
    streams = recorder.find_streams(timeout=5.0)
    target = [s for s in streams if s.name() == stream_name]
    if not target:
        recorder.cleanup()
        raise RuntimeError(f"Python LabRecorder could not find stream {stream_name!r}")
    print(f"  [Python] Found stream {stream_name!r}; starting recording -> {xdf_path}")
    recorder.start_recording(filename=str(xdf_path), streams=target)
    return recorder


def assert_single_stream(path: Path, expected_name: str) -> None:
    streams, _ = pyxdf.load_xdf(str(path), verbose=False)
    names = [s["info"]["name"][0] for s in streams]
    if names != [expected_name]:
        raise AssertionError(
            f"{path.name}: expected only stream {expected_name!r}, got {names!r}"
        )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Record one randomly named LSL stream with Python LabRecorder and "
            "LabRecorderCLI in parallel, then compare the XDFs."
        )
    )
    p.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_S,
        help=f"Seconds to push samples (default: {DEFAULT_DURATION_S})",
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
        help=f"Nominal sample rate Hz (default: {DEFAULT_SRATE_HZ})",
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
    stream_name = f"PhoXdfDualTest_{token}"
    source_id = f"pho_xdf_dual_{token}"

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
    print("  Dual-recorder XDF self-test")
    print(f"  {datetime.now().isoformat()}")
    print("=" * 70)
    print(f"  Stream name: {stream_name}")
    print(f"  Source id:   {source_id}")
    print(f"  Duration:    {args.duration:g}s @ {args.srate:g} Hz")
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

    outlet: Optional[RandomFloatOutlet] = None
    cli_proc: Optional[subprocess.Popen] = None
    recorder = None
    exit_code = 1

    try:
        print("\n--- Creating LSL outlet ---")
        outlet = RandomFloatOutlet(
            stream_name, source_id, nominal_srate=float(args.srate)
        )
        print("  Waiting for outlet to become discoverable ...")
        time.sleep(1.5)

        print("\n--- Starting LabRecorderCLI (GT) ---")
        cli_proc = start_labrecorder_cli(cli_path, gt_xdf, stream_name)
        wait_cli_recording_started(cli_proc)
        # Brief settle so CLI inlets are open
        time.sleep(0.5)

        print("\n--- Starting Python LabRecorder (test) ---")
        recorder = start_python_recorder(test_xdf, stream_name)
        time.sleep(0.3)

        print(f"\n--- Pushing samples for {args.duration:g}s ---")
        outlet.start()
        time.sleep(float(args.duration))
        outlet.stop()
        n_pushed = len(outlet.pushed_samples)
        print(f"  Pushed {n_pushed} samples")
        # Keep outlet alive until writers finalize so inlets do not reconnect mid-close
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

        print("\n--- Closing outlet ---")
        outlet.close()
        outlet = None

        if not test_xdf.is_file() or test_xdf.stat().st_size == 0:
            raise RuntimeError(f"Test XDF missing or empty: {test_xdf}")
        if not gt_xdf.is_file() or gt_xdf.stat().st_size == 0:
            raise RuntimeError(f"GT XDF missing or empty: {gt_xdf}")

        print(f"\n  Test XDF size: {test_xdf.stat().st_size} bytes")
        print(f"  GT XDF size:   {gt_xdf.stat().st_size} bytes")

        print("\n--- Isolation check (single stream each) ---")
        assert_single_stream(test_xdf, stream_name)
        assert_single_stream(gt_xdf, stream_name)
        print(f"  Both files contain only {stream_name!r}")

        print("\n--- Comparing XDFs ---")
        compare = _load_compare_module()
        compare_rc = compare.main(
            [
                "--test-xdf",
                str(test_xdf),
                "--gt-xdf",
                str(gt_xdf),
                "--streams",
                stream_name,
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
            # Preserve failures for inspection
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
            # Leave temp dir if keep_files; on failure we may have copied already
            if args.keep_files:
                print(f"  Temp outdir kept: {outdir}")
            else:
                tmp_ctx.cleanup()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
