"""Run the logger app with startup timing enabled (non-interactive smoke test)."""
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PHOLOG_STARTUP_TIMING"] = "1"
    env["DEBUG"] = "1"
    cmd = [sys.executable, str(repo_root / "logger_app.py"), "--unsafe"]
    print("Running with PHOLOG_STARTUP_TIMING=1 (close the window to finish)...", flush=True)
    return subprocess.call(cmd, cwd=str(repo_root), env=env)


if __name__ == "__main__":
    raise SystemExit(main())
