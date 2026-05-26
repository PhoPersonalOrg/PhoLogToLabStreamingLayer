"""Backward-compatible shim; use `uv run logger_app` or `python -m phologtolabstreaminglayer.console`."""
from phologtolabstreaminglayer.console import console_main, main

if __name__ == "__main__":
    console_main()
