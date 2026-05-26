import tkinter as tk
from tkinter import messagebox
import sys
import argparse
from pathlib import Path
from phologtolabstreaminglayer.startup_timing import mark
from phologtolabstreaminglayer.features.hide_console import auto_hide_console

mark("console.py import begin")
from phologtolabstreaminglayer.logger_app import LoggerApp
mark("LoggerApp import complete")


def main(xdf_folder: Path, unsafe: bool = False):
    """unsafe: bool = False skips the singleton lock check on startup, the user should first confirmt that there aren't multiple instances running."""
    mark("main() begin")
    if not unsafe and LoggerApp.is_instance_running():
        messagebox.showerror("Instance Already Running", "Another instance of LSL Logger is already running.\nOnly one instance can run at a time.\nTo override this safety check, launch with --unsafe.")
        sys.exit(1)

    root = tk.Tk()
    mark("tk.Tk() created")
    app = LoggerApp(root, xdf_folder=xdf_folder)
    mark("LoggerApp.__init__ complete")

    if not unsafe and not app.acquire_singleton_lock():
        messagebox.showerror("Startup Error", "Failed to acquire singleton lock.\nAnother instance may be running.\nTo override this safety check, launch with --unsafe.")
        root.destroy()
        sys.exit(1)

    def on_closing():
        if app is not None:
            app.on_closing()
        else:
            root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_closing)

    mark("entering mainloop")
    root.mainloop()


def console_main():
    """Console script entry point for uv run logger_app"""
    mark("console_main() begin")
    auto_hide_console()

    parser = argparse.ArgumentParser(description='PhoLogToLabStreamingLayer')
    parser.add_argument('--unsafe', action='store_true', help='Override safety checks and allow multiple instances')
    args = parser.parse_args()
    unsafe = args.unsafe

    _default_xdf_folder = Path(r'E:\Dropbox (Personal)\Databases\UnparsedData\PhoLogToLabStreamingLayer_logs')
    main(xdf_folder=_default_xdf_folder, unsafe=unsafe)


if __name__ == "__main__":
    console_main()
