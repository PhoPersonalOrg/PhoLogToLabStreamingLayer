"""Compare temporal overlap of two simultaneously recorded XDF files.

Loads a testing XDF (PhoLogToLabStreamingLayer) and a ground-truth XDF
(App-LabRecorder), finds the shared LSL ``local_clock`` time window per stream,
and reports sample-level agreement metrics.

Default paths come from the first data row of
``data/PhoLogToLabStreamingLayer_and_LabRecorder_XDF_File_map.csv``.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pyxdf

try:
    from phopylslhelper.easy_time_sync import lsl_stream_timestamps_to_unix_seconds
except ImportError:  # pragma: no cover - optional for wall-clock labels
    lsl_stream_timestamps_to_unix_seconds = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAP_CSV = (
    REPO_ROOT
    / "data"
    / "PhoLogToLabStreamingLayer_and_LabRecorder_XDF_File_map.csv"
)

# Validation gate defaults (continuous streams)
MIN_MATCH_RATE = 0.99
MAX_RMSE_GATE = 1e6  # generous; real EEG values are much smaller when equal


@dataclass
class StreamSummary:
    name: str
    stream_type: str
    n_samples: int
    n_channels: int
    nominal_srate: float
    t0: Optional[float]
    t1: Optional[float]

    @property
    def duration(self) -> Optional[float]:
        if self.t0 is None or self.t1 is None:
            return None
        return float(self.t1 - self.t0)


@dataclass
class ContinuousCompareResult:
    name: str
    t_lo: float
    t_hi: float
    n_test: int
    n_gt: int
    n_matched: int
    match_rate: float
    mean_abs_dt: float
    max_abs_dt: float
    rmse_per_ch: np.ndarray
    max_abs_err_per_ch: np.ndarray
    corr_per_ch: np.ndarray
    exact_equal_frac: float
    channel_labels: List[str] = field(default_factory=list)

    @property
    def rmse_max(self) -> float:
        if self.rmse_per_ch.size == 0:
            return float("nan")
        return float(np.nanmax(self.rmse_per_ch))


@dataclass
class MarkerCompareResult:
    name: str
    t_lo: float
    t_hi: float
    n_test: int
    n_gt: int
    n_matched: int
    n_value_equal: int
    unmatched_test: int
    unmatched_gt: int
    mean_abs_dt: float
    max_abs_dt: float


def _default_map_csv() -> Path:
    return DEFAULT_MAP_CSV


def load_paths_from_map_csv(map_csv: Path, row_index: int = 0) -> Tuple[Path, Path]:
    """Return (test_xdf, gt_xdf) from the map CSV."""
    with map_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Empty or header-less map CSV: {map_csv}")
        # Normalize header keys (strip whitespace)
        field_map = {h.strip(): h for h in reader.fieldnames}
        test_key = field_map.get("PhoLogToLabStreamingLayer_XDF")
        gt_key = field_map.get("LabRecorder_XDF")
        if test_key is None or gt_key is None:
            raise ValueError(
                f"Map CSV must have PhoLogToLabStreamingLayer_XDF and "
                f"LabRecorder_XDF columns; got {reader.fieldnames!r}"
            )
        rows = list(reader)
    if not rows:
        raise ValueError(f"No data rows in map CSV: {map_csv}")
    if row_index < 0 or row_index >= len(rows):
        raise IndexError(f"row_index {row_index} out of range (n={len(rows)})")
    row = rows[row_index]
    test_path = Path(row[test_key].strip().strip('"'))
    gt_path = Path(row[gt_key].strip().strip('"'))
    return test_path, gt_path


def load_xdf(path: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    streams, header = pyxdf.load_xdf(
        str(path),
        synchronize_clocks=True,
        handle_clock_resets=True,
        dejitter_timestamps=False,
        verbose=False,
    )
    return streams, header


def stream_name(stream: Dict[str, Any]) -> str:
    return str(stream["info"]["name"][0])


def stream_type(stream: Dict[str, Any]) -> str:
    return str(stream["info"]["type"][0])


def stream_channel_count(stream: Dict[str, Any]) -> int:
    return int(stream["info"]["channel_count"][0])


def stream_nominal_srate(stream: Dict[str, Any]) -> float:
    return float(stream["info"]["nominal_srate"][0])


def stream_timestamps(stream: Dict[str, Any]) -> np.ndarray:
    ts = stream.get("time_stamps")
    if ts is None or len(ts) == 0:
        return np.asarray([], dtype=float)
    return np.asarray(ts, dtype=float)


def stream_channel_labels(stream: Dict[str, Any]) -> List[str]:
    labels: List[str] = []
    try:
        desc = stream["info"].get("desc") or [None]
        chans = (desc[0] or {}).get("channels") if desc[0] else None
        if chans and chans[0]:
            for ch in chans[0].get("channel", []) or []:
                lab = ch.get("label")
                if lab:
                    labels.append(str(lab[0]))
    except (TypeError, KeyError, IndexError, AttributeError):
        pass
    n_ch = stream_channel_count(stream)
    if len(labels) != n_ch:
        labels = [f"ch{i}" for i in range(n_ch)]
    return labels


def summarize_stream(stream: Dict[str, Any]) -> StreamSummary:
    ts = stream_timestamps(stream)
    n = int(ts.size)
    return StreamSummary(
        name=stream_name(stream),
        stream_type=stream_type(stream),
        n_samples=n,
        n_channels=stream_channel_count(stream),
        nominal_srate=stream_nominal_srate(stream),
        t0=float(ts[0]) if n else None,
        t1=float(ts[-1]) if n else None,
    )


def index_streams_by_name(
    streams: Sequence[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for s in streams:
        name = stream_name(s)
        if name in out:
            # Prefer the copy with more samples if duplicates exist
            if stream_timestamps(s).size > stream_timestamps(out[name]).size:
                out[name] = s
        else:
            out[name] = s
    return out


def is_continuous_stream(stream: Dict[str, Any]) -> bool:
    """Treat nominal_srate > 0 as continuous; else inspect time_series dtype."""
    if stream_nominal_srate(stream) > 0:
        return True
    ts = stream.get("time_series")
    if ts is None or len(ts) == 0:
        return False
    arr = np.asarray(ts)
    return np.issubdtype(arr.dtype, np.number)


def slice_to_window(
    timestamps: np.ndarray,
    series: np.ndarray,
    t_lo: float,
    t_hi: float,
) -> Tuple[np.ndarray, np.ndarray]:
    if timestamps.size == 0:
        return timestamps, series
    mask = (timestamps >= t_lo) & (timestamps <= t_hi)
    return timestamps[mask], series[mask]


def time_series_as_2d(stream: Dict[str, Any]) -> np.ndarray:
    """Return (n_samples, n_channels) float array for continuous streams."""
    raw = stream.get("time_series")
    if raw is None or len(raw) == 0:
        n_ch = stream_channel_count(stream)
        return np.zeros((0, n_ch), dtype=float)
    arr = np.asarray(raw)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    return arr.astype(float, copy=False)


def marker_payloads(stream: Dict[str, Any]) -> List[str]:
    raw = stream.get("time_series")
    if raw is None:
        return []
    out: List[str] = []
    for row in raw:
        if isinstance(row, (list, tuple, np.ndarray)):
            out.append("|".join(str(x) for x in row))
        else:
            out.append(str(row))
    return out


def nearest_match_indices(
    query_ts: np.ndarray,
    ref_ts: np.ndarray,
    tol_s: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """For each query stamp, find nearest ref index within tol_s.

    Returns (ref_indices, abs_dt) with ref_indices == -1 when unmatched.
    """
    n = query_ts.size
    indices = np.full(n, -1, dtype=int)
    abs_dt = np.full(n, np.nan, dtype=float)
    if n == 0 or ref_ts.size == 0:
        return indices, abs_dt
    # searchsorted insertion points
    pos = np.searchsorted(ref_ts, query_ts, side="left")
    for i, p in enumerate(pos):
        candidates: List[Tuple[float, int]] = []
        if p < ref_ts.size:
            candidates.append((abs(ref_ts[p] - query_ts[i]), p))
        if p > 0:
            candidates.append((abs(ref_ts[p - 1] - query_ts[i]), p - 1))
        if not candidates:
            continue
        best_dt, best_j = min(candidates, key=lambda x: x[0])
        if best_dt <= tol_s:
            indices[i] = best_j
            abs_dt[i] = best_dt
    return indices, abs_dt


def compare_continuous(
    name: str,
    test: Dict[str, Any],
    gt: Dict[str, Any],
    tol_s: float,
) -> Optional[ContinuousCompareResult]:
    test_ts = stream_timestamps(test)
    gt_ts = stream_timestamps(gt)
    if test_ts.size == 0 or gt_ts.size == 0:
        return None
    t_lo = max(float(test_ts[0]), float(gt_ts[0]))
    t_hi = min(float(test_ts[-1]), float(gt_ts[-1]))
    if t_hi <= t_lo:
        return None

    test_data = time_series_as_2d(test)
    gt_data = time_series_as_2d(gt)
    # Expand by tol so edge samples with a nearest neighbor just outside [t_lo, t_hi]
    # are still considered (same LSL stamps can differ by tens of µs across recorders).
    test_ts_w, test_data_w = slice_to_window(
        test_ts, test_data, t_lo - tol_s, t_hi + tol_s
    )
    gt_ts_w, gt_data_w = slice_to_window(gt_ts, gt_data, t_lo - tol_s, t_hi + tol_s)

    idx, abs_dt = nearest_match_indices(test_ts_w, gt_ts_w, tol_s)
    matched = idx >= 0
    n_matched = int(matched.sum())
    n_test = int(test_ts_w.size)
    match_rate = float(n_matched / n_test) if n_test else 0.0

    n_ch = min(test_data_w.shape[1], gt_data_w.shape[1]) if n_test and gt_data_w.size else 0
    labels = stream_channel_labels(test)[:n_ch]

    if n_matched == 0 or n_ch == 0:
        empty = np.asarray([], dtype=float)
        return ContinuousCompareResult(
            name=name,
            t_lo=t_lo,
            t_hi=t_hi,
            n_test=n_test,
            n_gt=int(gt_ts_w.size),
            n_matched=n_matched,
            match_rate=match_rate,
            mean_abs_dt=float(np.nanmean(abs_dt[matched])) if n_matched else float("nan"),
            max_abs_dt=float(np.nanmax(abs_dt[matched])) if n_matched else float("nan"),
            rmse_per_ch=empty,
            max_abs_err_per_ch=empty,
            corr_per_ch=empty,
            exact_equal_frac=0.0,
            channel_labels=labels,
        )

    test_m = test_data_w[matched, :n_ch]
    gt_m = gt_data_w[idx[matched], :n_ch]
    diff = test_m - gt_m
    rmse = np.sqrt(np.mean(diff**2, axis=0))
    max_abs = np.max(np.abs(diff), axis=0)
    corr = np.full(n_ch, np.nan, dtype=float)
    for c in range(n_ch):
        a = test_m[:, c]
        b = gt_m[:, c]
        if a.size < 2 or np.std(a) == 0 or np.std(b) == 0:
            corr[c] = np.nan
        else:
            corr[c] = float(np.corrcoef(a, b)[0, 1])
    exact = float(np.mean(np.all(np.isclose(test_m, gt_m, rtol=0.0, atol=1e-9), axis=1)))

    return ContinuousCompareResult(
        name=name,
        t_lo=t_lo,
        t_hi=t_hi,
        n_test=n_test,
        n_gt=int(gt_ts_w.size),
        n_matched=n_matched,
        match_rate=match_rate,
        mean_abs_dt=float(np.mean(abs_dt[matched])),
        max_abs_dt=float(np.max(abs_dt[matched])),
        rmse_per_ch=rmse,
        max_abs_err_per_ch=max_abs,
        corr_per_ch=corr,
        exact_equal_frac=exact,
        channel_labels=labels,
    )


def compare_markers(
    name: str,
    test: Dict[str, Any],
    gt: Dict[str, Any],
    tol_s: float,
) -> Optional[MarkerCompareResult]:
    test_ts = stream_timestamps(test)
    gt_ts = stream_timestamps(gt)
    if test_ts.size == 0 and gt_ts.size == 0:
        return None
    if test_ts.size == 0 or gt_ts.size == 0:
        # One recorder never wrote samples for this stream — no overlap to compare.
        return None

    t_lo = max(float(test_ts[0]), float(gt_ts[0]))
    t_hi = min(float(test_ts[-1]), float(gt_ts[-1]))
    if t_hi + tol_s < t_lo - tol_s:
        return None

    test_payloads = np.asarray(marker_payloads(test), dtype=object)
    gt_payloads = np.asarray(marker_payloads(gt), dtype=object)

    # Expand by tol so near-identical edge markers are not dropped by µs-level t0/t1 skew
    test_mask = (test_ts >= t_lo - tol_s) & (test_ts <= t_hi + tol_s)
    gt_mask = (gt_ts >= t_lo - tol_s) & (gt_ts <= t_hi + tol_s)
    test_ts_w = test_ts[test_mask]
    gt_ts_w = gt_ts[gt_mask]
    test_pay_w = test_payloads[test_mask]
    gt_pay_w = gt_payloads[gt_mask]

    idx, abs_dt = nearest_match_indices(test_ts_w, gt_ts_w, tol_s)
    matched = idx >= 0
    n_matched = int(matched.sum())
    n_value_equal = 0
    if n_matched:
        n_value_equal = int(
            np.sum(test_pay_w[matched] == gt_pay_w[idx[matched]])
        )

    # GT events with no test match
    gt_used = set(int(i) for i in idx[matched])
    unmatched_gt = int(gt_ts_w.size - len(gt_used))

    return MarkerCompareResult(
        name=name,
        t_lo=t_lo,
        t_hi=t_hi,
        n_test=int(test_ts_w.size),
        n_gt=int(gt_ts_w.size),
        n_matched=n_matched,
        n_value_equal=n_value_equal,
        unmatched_test=int((~matched).sum()),
        unmatched_gt=unmatched_gt,
        mean_abs_dt=float(np.mean(abs_dt[matched])) if n_matched else float("nan"),
        max_abs_dt=float(np.max(abs_dt[matched])) if n_matched else float("nan"),
    )


def print_inventory(label: str, path: Path, streams: Sequence[Dict[str, Any]]) -> None:
    print(f"\n=== {label}: {path.name} ===")
    print(f"    path: {path}")
    for s in streams:
        sm = summarize_stream(s)
        if sm.n_samples == 0:
            print(f"  {sm.name!r} type={sm.stream_type!r} EMPTY")
            continue
        print(
            f"  {sm.name!r} type={sm.stream_type!r} ch={sm.n_channels} "
            f"srate={sm.nominal_srate:g} n={sm.n_samples} "
            f"t0={sm.t0:.6f} t1={sm.t1:.6f} dur={sm.duration:.3f}s"
        )


def format_unix_hint(stream: Dict[str, Any], t_lsl: float) -> str:
    if lsl_stream_timestamps_to_unix_seconds is None:
        return ""
    ts = stream_timestamps(stream)
    if ts.size == 0:
        return ""
    try:
        unix = lsl_stream_timestamps_to_unix_seconds(stream, np.asarray([t_lsl]))
        return f" (unix≈{float(unix[0]):.3f})"
    except Exception:
        return ""


def plot_overlap(
    name: str,
    test: Dict[str, Any],
    gt: Dict[str, Any],
    result: ContinuousCompareResult,
    channel_index: int = 0,
) -> None:
    import matplotlib.pyplot as plt

    test_ts = stream_timestamps(test)
    gt_ts = stream_timestamps(gt)
    test_data = time_series_as_2d(test)
    gt_data = time_series_as_2d(gt)
    test_ts_w, test_data_w = slice_to_window(
        test_ts, test_data, result.t_lo, result.t_hi
    )
    gt_ts_w, gt_data_w = slice_to_window(gt_ts, gt_data, result.t_lo, result.t_hi)
    ch = min(channel_index, test_data_w.shape[1] - 1, gt_data_w.shape[1] - 1)
    label = (
        result.channel_labels[ch]
        if ch < len(result.channel_labels)
        else f"ch{ch}"
    )
    fig, ax = plt.subplots(figsize=(12, 4))
    # Relative time from overlap start for readability
    ax.plot(gt_ts_w - result.t_lo, gt_data_w[:, ch], label=f"GT {label}", alpha=0.8)
    ax.plot(
        test_ts_w - result.t_lo,
        test_data_w[:, ch],
        label=f"Test {label}",
        alpha=0.8,
        linestyle="--",
    )
    ax.set_xlabel("Time since overlap start (s)")
    ax.set_ylabel(label)
    ax.set_title(f"{name} overlap [{result.t_lo:.3f}, {result.t_hi:.3f}]")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    plt.show()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Compare temporal overlap of PhoLog (test) and LabRecorder (GT) XDF files."
        )
    )
    p.add_argument(
        "--map-csv",
        type=Path,
        default=_default_map_csv(),
        help=f"Path pair CSV (default: {DEFAULT_MAP_CSV})",
    )
    p.add_argument(
        "--map-row",
        type=int,
        default=0,
        help="0-based data row in the map CSV (default: 0)",
    )
    p.add_argument(
        "--test-xdf",
        type=Path,
        default=None,
        help="Testing XDF path (overrides map CSV)",
    )
    p.add_argument(
        "--gt-xdf",
        type=Path,
        default=None,
        help="Ground-truth XDF path (overrides map CSV)",
    )
    p.add_argument(
        "--streams",
        type=str,
        default=None,
        help="Comma-separated stream names to compare (default: all common)",
    )
    p.add_argument(
        "--tol-ms",
        type=float,
        default=1.0,
        help="Nearest-timestamp match tolerance in milliseconds (default: 1.0)",
    )
    p.add_argument(
        "--min-match-rate",
        type=float,
        default=MIN_MATCH_RATE,
        help=f"Fail if continuous match rate below this (default: {MIN_MATCH_RATE})",
    )
    p.add_argument(
        "--max-rmse",
        type=float,
        default=MAX_RMSE_GATE,
        help=f"Fail if any continuous channel RMSE exceeds this (default: {MAX_RMSE_GATE})",
    )
    p.add_argument(
        "--plot",
        action="store_true",
        help="Show matplotlib overlay for the first continuous compared stream",
    )
    p.add_argument(
        "--plot-stream",
        type=str,
        default=None,
        help="Stream name to plot when --plot is set (default: first continuous)",
    )
    p.add_argument(
        "--plot-channel",
        type=int,
        default=0,
        help="Channel index for --plot (default: 0)",
    )
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    tol_s = float(args.tol_ms) / 1000.0

    if args.test_xdf is not None and args.gt_xdf is not None:
        test_path = args.test_xdf.resolve()
        gt_path = args.gt_xdf.resolve()
    elif args.test_xdf is not None or args.gt_xdf is not None:
        print(
            "Provide both --test-xdf and --gt-xdf, or neither (use --map-csv).",
            file=sys.stderr,
        )
        return 2
    else:
        map_csv = args.map_csv.resolve()
        if not map_csv.is_file():
            print(f"Map CSV not found: {map_csv}", file=sys.stderr)
            return 2
        test_path, gt_path = load_paths_from_map_csv(map_csv, row_index=args.map_row)
        print(f"Map CSV: {map_csv} (row {args.map_row})")

    if not test_path.is_file():
        print(f"Test XDF not found: {test_path}", file=sys.stderr)
        return 2
    if not gt_path.is_file():
        print(f"GT XDF not found: {gt_path}", file=sys.stderr)
        return 2

    print(f"Loading test XDF: {test_path}")
    test_streams, _test_header = load_xdf(test_path)
    print(f"Loading GT XDF:   {gt_path}")
    gt_streams, _gt_header = load_xdf(gt_path)

    print_inventory("TEST", test_path, test_streams)
    print_inventory("GT", gt_path, gt_streams)

    test_by_name = index_streams_by_name(test_streams)
    gt_by_name = index_streams_by_name(gt_streams)
    common = sorted(set(test_by_name) & set(gt_by_name))
    only_test = sorted(set(test_by_name) - set(gt_by_name))
    only_gt = sorted(set(gt_by_name) - set(test_by_name))

    print("\n=== Stream set diff ===")
    print(f"  common ({len(common)}): {common}")
    print(f"  only test ({len(only_test)}): {only_test}")
    print(f"  only GT   ({len(only_gt)}): {only_gt}")

    if args.streams:
        wanted = {s.strip() for s in args.streams.split(",") if s.strip()}
        missing = wanted - set(common)
        if missing:
            print(f"  requested streams not in both files: {sorted(missing)}")
        names = [n for n in common if n in wanted]
    else:
        names = common

    continuous_results: List[ContinuousCompareResult] = []
    marker_results: List[MarkerCompareResult] = []
    plot_candidate: Optional[Tuple[str, Dict[str, Any], Dict[str, Any], ContinuousCompareResult]] = None

    print(f"\n=== Overlap comparison (tol={args.tol_ms:g} ms) ===")
    for name in names:
        test_s = test_by_name[name]
        gt_s = gt_by_name[name]
        if stream_timestamps(test_s).size == 0 and stream_timestamps(gt_s).size == 0:
            print(f"\n[{name}] both empty — skip")
            continue

        if is_continuous_stream(test_s) or is_continuous_stream(gt_s):
            result = compare_continuous(name, test_s, gt_s, tol_s=tol_s)
            if result is None:
                print(f"\n[{name}] no temporal overlap — skip")
                continue
            continuous_results.append(result)
            unix_lo = format_unix_hint(test_s, result.t_lo)
            print(
                f"\n[{name}] CONTINUOUS overlap "
                f"[{result.t_lo:.6f}, {result.t_hi:.6f}] "
                f"dur={result.t_hi - result.t_lo:.3f}s{unix_lo}"
            )
            print(
                f"  samples: test={result.n_test} gt={result.n_gt} "
                f"matched={result.n_matched} match_rate={result.match_rate:.4f}"
            )
            print(
                f"  |Δt|: mean={result.mean_abs_dt * 1e3:.4f} ms "
                f"max={result.max_abs_dt * 1e3:.4f} ms"
            )
            print(f"  exact_equal_frac={result.exact_equal_frac:.6f}")
            if result.rmse_per_ch.size:
                print(
                    f"  RMSE max={result.rmse_max:.6g}  "
                    f"mean={float(np.nanmean(result.rmse_per_ch)):.6g}"
                )
                print(
                    f"  max|err| max={float(np.nanmax(result.max_abs_err_per_ch)):.6g}"
                )
                finite_corr = result.corr_per_ch[np.isfinite(result.corr_per_ch)]
                if finite_corr.size:
                    print(
                        f"  Pearson r: min={float(np.min(finite_corr)):.6f} "
                        f"median={float(np.median(finite_corr)):.6f}"
                    )
                # Compact per-channel line
                for i, lab in enumerate(result.channel_labels):
                    r = (
                        f"{result.corr_per_ch[i]:.4f}"
                        if np.isfinite(result.corr_per_ch[i])
                        else "nan"
                    )
                    print(
                        f"    {lab}: RMSE={result.rmse_per_ch[i]:.6g} "
                        f"max|e|={result.max_abs_err_per_ch[i]:.6g} r={r}"
                    )
            if plot_candidate is None:
                plot_candidate = (name, test_s, gt_s, result)
            if args.plot_stream and name == args.plot_stream:
                plot_candidate = (name, test_s, gt_s, result)
        else:
            result_m = compare_markers(name, test_s, gt_s, tol_s=tol_s)
            if result_m is None:
                print(f"\n[{name}] no temporal overlap — skip")
                continue
            marker_results.append(result_m)
            print(
                f"\n[{name}] MARKERS overlap "
                f"[{result_m.t_lo:.6f}, {result_m.t_hi:.6f}] "
                f"dur={result_m.t_hi - result_m.t_lo:.3f}s"
            )
            print(
                f"  events: test={result_m.n_test} gt={result_m.n_gt} "
                f"matched={result_m.n_matched} value_equal={result_m.n_value_equal}"
            )
            print(
                f"  unmatched: test={result_m.unmatched_test} "
                f"gt={result_m.unmatched_gt}"
            )
            if result_m.n_matched:
                print(
                    f"  |Δt|: mean={result_m.mean_abs_dt * 1e3:.4f} ms "
                    f"max={result_m.max_abs_dt * 1e3:.4f} ms"
                )

    # Global overlap from continuous streams (or markers)
    print("\n=== Summary ===")
    if continuous_results:
        glo = max(r.t_lo for r in continuous_results)
        ghi = min(r.t_hi for r in continuous_results)
        if ghi > glo:
            print(f"  continuous streams common window: [{glo:.6f}, {ghi:.6f}] "
                  f"({ghi - glo:.3f}s)")
        print(f"  continuous streams compared: {len(continuous_results)}")
    print(f"  marker streams compared: {len(marker_results)}")

    failed = False
    for r in continuous_results:
        if r.n_test == 0:
            continue
        if r.match_rate < args.min_match_rate:
            print(
                f"  FAIL {r.name}: match_rate={r.match_rate:.4f} "
                f"< {args.min_match_rate}"
            )
            failed = True
        if r.rmse_per_ch.size and float(np.nanmax(r.rmse_per_ch)) > args.max_rmse:
            print(
                f"  FAIL {r.name}: RMSE max={float(np.nanmax(r.rmse_per_ch)):.6g} "
                f"> {args.max_rmse}"
            )
            failed = True
    if not continuous_results and not marker_results:
        print("  FAIL: no streams compared")
        failed = True
    if not failed:
        print("  PASS: agreement gates satisfied")

    if args.plot:
        target = plot_candidate
        if args.plot_stream:
            target = None
            for r in continuous_results:
                if r.name == args.plot_stream:
                    target = (
                        r.name,
                        test_by_name[r.name],
                        gt_by_name[r.name],
                        r,
                    )
                    break
            if target is None:
                print(
                    f"Cannot plot stream {args.plot_stream!r}: not compared as continuous.",
                    file=sys.stderr,
                )
        if target is not None:
            plot_overlap(
                target[0],
                target[1],
                target[2],
                target[3],
                channel_index=args.plot_channel,
            )
        else:
            print("No continuous stream available to plot.", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
