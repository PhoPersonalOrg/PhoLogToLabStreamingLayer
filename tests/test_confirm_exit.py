"""Tests for the confirm-exit dialog shown when closing the main window.

Window X must prompt with "Quit app" / "Minimize to system tray" instead of
silently hiding to the tray. Quit routes through the existing clean-shutdown
path (quit_app -> on_closing); dismissing the dialog minimizes to tray.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import unittest

from phologtolabstreaminglayer.logger_app import LoggerApp


class _StubExitApp:
    """Minimal app exposing only what LoggerApp.confirm_exit needs."""

    confirm_exit = LoggerApp.confirm_exit
    _dismiss_exit_prompt = LoggerApp._dismiss_exit_prompt

    def __init__(self, root: tk.Tk):
        self.root = root
        self.quit_calls = 0
        self.minimize_calls = 0

    def quit_app(self):
        self.quit_calls += 1

    def minimize_to_tray(self):
        self.minimize_calls += 1


def _find_button(widget: tk.Misc, text: str) -> ttk.Button | None:
    """Find a ttk.Button by its label anywhere in the widget tree."""
    if isinstance(widget, ttk.Button) and str(widget.cget("text")) == text:
        return widget
    for child in widget.winfo_children():
        found = _find_button(child, text)
        if found is not None:
            return found
    return None


class TestConfirmExit(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = _StubExitApp(self.root)

    def tearDown(self):
        self.root.destroy()

    def _dialog(self) -> tk.Toplevel:
        dialog = getattr(self.app, "_exit_prompt_dialog", None)
        self.assertIsInstance(dialog, tk.Toplevel, "confirm_exit should open a dialog")
        return dialog

    def test_dialog_has_both_buttons(self):
        self.app.confirm_exit()
        dialog = self._dialog()
        self.assertEqual(dialog.title(), "Confirm exit")
        self.assertIsNotNone(_find_button(dialog, "Quit app"))
        self.assertIsNotNone(_find_button(dialog, "Minimize to system tray"))

    def test_quit_button_routes_to_quit_app(self):
        self.app.confirm_exit()
        dialog = self._dialog()
        quit_button = _find_button(dialog, "Quit app")
        self.assertIsNotNone(quit_button)
        quit_button.invoke()
        self.assertEqual(self.app.quit_calls, 1)
        self.assertEqual(self.app.minimize_calls, 0)
        self.assertFalse(self.app._exit_prompt_open)
        self.assertIsNone(self.app._exit_prompt_dialog)

    def test_minimize_button_routes_to_minimize_to_tray(self):
        self.app.confirm_exit()
        dialog = self._dialog()
        minimize_button = _find_button(dialog, "Minimize to system tray")
        self.assertIsNotNone(minimize_button)
        minimize_button.invoke()
        self.assertEqual(self.app.quit_calls, 0)
        self.assertEqual(self.app.minimize_calls, 1)
        self.assertFalse(self.app._exit_prompt_open)

    def test_escape_minimizes_to_tray(self):
        self.app.confirm_exit()
        dialog = self._dialog()
        # Escape must be wired to the dismiss handler (registered command name
        # embeds the method name).
        self.assertTrue(dialog.bind("<Escape>"), "dialog must bind <Escape>")
        self.assertTrue(
            any("_dismiss_exit_prompt" in c for c in dialog._tclCommands),
            "Escape must route to _dismiss_exit_prompt",
        )
        # Dismissing minimizes instead of quitting. Invoke the handler directly:
        # synthetic key delivery depends on OS focus and is flaky in test runs.
        self.app._dismiss_exit_prompt()
        self.assertEqual(self.app.quit_calls, 0)
        self.assertEqual(self.app.minimize_calls, 1)
        self.assertFalse(self.app._exit_prompt_open)

    def test_dialog_close_button_minimizes_to_tray(self):
        self.app.confirm_exit()
        dialog = self._dialog()
        # The dialog's X must be wired to the dismiss handler.
        name = dialog.protocol("WM_DELETE_WINDOW")
        self.assertTrue(name, "dialog must register a WM_DELETE_WINDOW handler")
        self.assertIn("_dismiss_exit_prompt", name)
        # Dismissing minimizes instead of quitting. Invoke the handler directly:
        # synthetic WM events depend on OS focus and are flaky in test runs.
        self.app._dismiss_exit_prompt()
        self.assertEqual(self.app.quit_calls, 0)
        self.assertEqual(self.app.minimize_calls, 1)
        self.assertFalse(self.app._exit_prompt_open)

    def test_repeated_close_does_not_stack_dialogs(self):
        self.app.confirm_exit()
        first_dialog = self._dialog()
        self.app.confirm_exit()
        self.assertIs(self.app._exit_prompt_dialog, first_dialog)
        toplevels = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertEqual(len(toplevels), 1)

    def test_no_prompt_while_shutting_down(self):
        self.app._shutting_down = True
        self.app.confirm_exit()
        self.assertIsNone(getattr(self.app, "_exit_prompt_dialog", None))
        self.assertEqual(self.app.quit_calls, 0)
        self.assertEqual(self.app.minimize_calls, 0)

    def test_no_prompt_while_closing(self):
        self.app._closing = True
        self.app.confirm_exit()
        self.assertIsNone(getattr(self.app, "_exit_prompt_dialog", None))
        self.assertEqual(self.app.quit_calls, 0)
        self.assertEqual(self.app.minimize_calls, 0)

    def test_can_prompt_again_after_dismiss(self):
        self.app.confirm_exit()
        minimize_button = _find_button(self._dialog(), "Minimize to system tray")
        minimize_button.invoke()
        self.app.confirm_exit()
        dialog = self._dialog()
        self.assertTrue(dialog.winfo_exists())


if __name__ == "__main__":
    unittest.main()
