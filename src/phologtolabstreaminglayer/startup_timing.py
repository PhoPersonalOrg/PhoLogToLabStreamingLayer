"""Optional startup phase timing (enable with PHOLOG_STARTUP_TIMING=1)."""
import os
import time

_START = time.perf_counter()
_LAST = _START


def is_enabled() -> bool:
    flag = os.environ.get("PHOLOG_STARTUP_TIMING", "").lower()
    return flag in ("1", "true", "yes")


def mark(label: str) -> None:
    global _LAST
    if not is_enabled():
        return
    now = time.perf_counter()
    print(f"[startup +{now - _START:.3f}s (+{now - _LAST:.3f}s)] {label}", flush=True)
    _LAST = now
