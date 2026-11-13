---
name: confirm-exit-dialog
overview: Replace the silent minimize-to-tray behavior on window close with a "Confirm exit" dialog offering "Quit app" and "Minimize to system tray", reusing the existing clean-shutdown path.
todos:
  - id: add-confirm-exit-dialog
    content: Add LoggerApp.confirm_exit() modal dialog (Quit app / Minimize to system tray) in logger_app.py
    status: completed
  - id: wire-window-close
    content: Route WM_DELETE_WINDOW through confirm_exit in console.py; update on_closing docstring
    status: completed
  - id: tests-and-verify
    content: Add tests/test_confirm_exit.py, run unittest, and smoke-test the close dialog manually
    status: completed
isProject: false
---

# Confirm Exit Dialog on Window Close

## Current behavior
- Window X is handled in [src/phologtolabstreaminglayer/console.py](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoLogToLabStreamingLayer\src\phologtolabstreaminglayer\console.py) (lines 31-36): `on_window_close()` calls `app.minimize_to_tray()` directly.
- Clean shutdown already exists: `LoggerApp.on_closing()` in [src/phologtolabstreaminglayer/logger_app.py](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoLogToLabStreamingLayer\src\phologtolabstreaminglayer\logger_app.py) (line 2937), reached from tray Exit via `quit_app()` (line 679). It stops recording/transcription, releases the singleton lock, and destroys root.

## Changes

### 1. Add `LoggerApp.confirm_exit()` in `logger_app.py` (next to `quit_app`, ~line 679)
Tk 8.6 `messagebox.askyesno` cannot relabel Yes/No buttons, so use a small modal `tk.Toplevel` with two `ttk.Button`s (consistent with the app's ttk theme):
- Title "Confirm exit", message "Quit the app, or minimize it to the system tray?", buttons "Quit app" and "Minimize to system tray".
- "Quit app" -> `self.quit_app()` (existing shutdown path); "Minimize to system tray" -> `self.minimize_to_tray()`.
- Dismissing the dialog (its X or Escape) -> minimize to tray (safe default; "Minimize to system tray" button gets initial focus).
- Guards: return early if `self._closing` / `self._shutting_down`; `_exit_prompt_open` flag prevents stacking dialogs on repeated X clicks. Dialog is `transient(self.root)`, `grab_set()`, centered over the main window, destroyed before invoking the chosen action.

Core shape:

```python
def confirm_exit(self):
    if getattr(self, "_closing", False) or getattr(self, "_shutting_down", False):
        return
    if getattr(self, "_exit_prompt_open", False):
        return
    self._exit_prompt_open = True
    dialog = tk.Toplevel(self.root)
    dialog.title("Confirm exit")
    ...
    def choose(action):
        self._exit_prompt_open = False
        dialog.destroy()
        if action == "quit":
            self.quit_app()
        else:
            self.minimize_to_tray()
    ttk.Button(buttons, text="Quit app", command=lambda: choose("quit"))
    ttk.Button(buttons, text="Minimize to system tray", command=lambda: choose("minimize"))
```

### 2. Wire the close handler in `console.py`
Change `on_window_close()` to call `app.confirm_exit()` instead of `app.minimize_to_tray()`. Covers both `uv run logger_app` and the packaged exe (root `logger_app.py` shim uses the same entry).

### 3. Update stale docstring
`on_closing()` docstring (line 2938) currently says "Window X hides to tray instead" -> update to "Window X prompts Quit / Minimize to tray".

## Unchanged
- Tray menu Exit, the "Minimize to Tray" button, and SIGINT/SIGTERM handlers keep quitting/minimizing directly with no prompt.
- No changes to the shared `SystemTrayAppMixin` in PhoPyLSLhelper; change stays local to this app.

## Verification
- New `tests/test_confirm_exit.py` (unittest, real `tk.Tk()` like existing `tests/test_console_output_shutdown.py`, with a minimal stub app exposing `root`, `quit_app`, `minimize_to_tray`): invoke each dialog button via `.invoke()` and assert routing; Escape/dismiss minimizes; re-entrancy guard blocks a second dialog; no prompt during shutdown. Run `uv run python -m unittest tests.test_confirm_exit -v`.
- Manual smoke: launch app, click X -> dialog appears; "Quit app" exits cleanly (recording stops, lock released); "Minimize to system tray" hides to tray and restores via tray icon.