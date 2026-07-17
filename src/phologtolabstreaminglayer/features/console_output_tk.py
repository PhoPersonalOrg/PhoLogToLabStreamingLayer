# Tkinter-native console output widget for capturing stdout/stderr
# Copyright (C) 2025 Pho Hale. All rights reserved.

"""
A tkinter-native console output widget that captures stdout/stderr and displays
it in a collapsible panel with auto-scroll and line limiting.

This module provides:
- TkTextStream: A thread-safe text stream wrapper for stdout/stderr capture
- ConsoleOutputFrame: A collapsible frame widget for displaying console output
"""

import sys
import tkinter as tk
from tkinter import ttk, scrolledtext
from typing import Optional, Callable, TextIO
import threading
import queue

# Try to import hide_console to check if console was hidden
try:
    from phologtolabstreaminglayer.features.hide_console import is_console_hidden, get_original_stdout, get_original_stderr
except ImportError:
    # Fallback if module not available
    def is_console_hidden() -> bool:
        return False
    def get_original_stdout() -> Optional[TextIO]:
        return None
    def get_original_stderr() -> Optional[TextIO]:
        return None


class TkTextStream:
    """A thread-safe text stream that captures writes and forwards them to a callback.
    
    This class wraps stdout/stderr to capture all output while still passing it through
    to the original stream. Uses a queue for thread-safe communication.
    
    Attributes:
        source: The source identifier for this stream ("stdout" or "stderr").
    """
    
    def __init__(self, original_stream: Optional[TextIO], source: str = "stdout", write_callback: Optional[Callable[[str, str], None]] = None, pass_through: bool = True):
        """
        Initialize the text stream wrapper.
        
        Args:
            original_stream: The original stream to pass output through to (can be None).
            source: Source identifier ("stdout", "stderr", or custom).
            write_callback: Optional callback function called on every write with (text, source).
            pass_through: Whether to pass output through to original_stream. Defaults to True.
                          Set to False when console was hidden to avoid hangs.
        """
        self._original_stream = original_stream
        self._source = source
        self._write_callback = write_callback
        self._pass_through = pass_through
        self._buffer = ""
        self._lock = threading.Lock()


    @property
    def source(self) -> str:
        """Return the source identifier for this stream."""
        return self._source


    def set_callback(self, callback: Optional[Callable[[str, str], None]]):
        """Set or update the write callback."""
        with self._lock:
            self._write_callback = callback


    def write(self, text: str) -> int:
        """Write text to the stream."""
        if not text:
            return 0
        
        # Pass through to original stream first (if enabled)
        if self._pass_through and self._original_stream is not None:
            try:
                self._original_stream.write(text)
            except Exception:
                pass  # Don't let passthrough errors break the capture
        
        # Fire callback if registered
        with self._lock:
            callback = self._write_callback
        
        if callback is not None:
            try:
                callback(text, self._source)
            except Exception:
                pass  # Don't let callback errors break the stream
        
        return len(text)


    def flush(self):
        """Flush the stream."""
        if self._pass_through and self._original_stream is not None:
            try:
                self._original_stream.flush()
            except Exception:
                pass


    def isatty(self) -> bool:
        """Check if this is a TTY."""
        return False


    def readable(self) -> bool:
        """Check if stream is readable."""
        return False


    def writable(self) -> bool:
        """Check if stream is writable."""
        return True


    def seekable(self) -> bool:
        """Check if stream is seekable."""
        return False


    @property
    def encoding(self) -> str:
        """Return the encoding of the stream."""
        if self._original_stream is not None and hasattr(self._original_stream, 'encoding'):
            return self._original_stream.encoding
        return 'utf-8'


    @property
    def errors(self) -> Optional[str]:
        """Return the error handling mode."""
        if self._original_stream is not None and hasattr(self._original_stream, 'errors'):
            return self._original_stream.errors
        return 'replace'


class ConsoleOutputFrame(ttk.Frame):
    """A collapsible frame widget that displays captured stdout/stderr output.
    
    Features:
        - Collapsible panel with toggle button
        - Auto-scroll with toggle control
        - Line limit to prevent memory issues
        - Thread-safe text updates via tkinter's after() method
        - Clear button
    
    Args:
        parent: Parent widget.
        root: The root Tk window (needed for thread-safe after() calls).
        capture_stdout: Whether to capture sys.stdout. Defaults to True.
        capture_stderr: Whether to capture sys.stderr. Defaults to True.
        max_lines: Maximum number of lines to retain. Defaults to 10000.
        initial_visible: Whether the console panel is initially visible. Defaults to False.
        height: Height of the text area in lines. Defaults to 10.
        pass_through: Whether to pass output through to original streams. Defaults to None (auto-detect).
                      If None, automatically detects if console was hidden and disables passthrough.
    """
    
    def __init__(self, parent, root: tk.Tk, capture_stdout: bool = True, capture_stderr: bool = True, max_lines: int = 10000, initial_visible: bool = False, height: int = 10, pass_through: Optional[bool] = None):
        super().__init__(parent)
        
        self._root = root
        self._max_lines = max_lines
        self._auto_scroll = True
        self._visible = initial_visible
        self._height = height
        self._shutting_down = False
        
        # Auto-detect pass_through if not specified
        if pass_through is None:
            # If console was hidden, don't pass through to avoid hangs
            pass_through = not is_console_hidden()
        self._pass_through = pass_through
        
        # Store original streams - prefer streams from hide_console if available
        original_stdout = get_original_stdout()
        original_stderr = get_original_stderr()
        self._original_stdout = original_stdout if original_stdout is not None else sys.stdout
        self._original_stderr = original_stderr if original_stderr is not None else sys.stderr
        self._stdout_stream: Optional[TkTextStream] = None
        self._stderr_stream: Optional[TkTextStream] = None
        self._capture_stdout = capture_stdout
        self._capture_stderr = capture_stderr
        
        # Thread-safe queue for text updates
        self._text_queue: queue.Queue = queue.Queue()
        self._update_scheduled = False
        self._update_lock = threading.Lock()
        
        self._setup_ui()
        self._setup_streams()


    def _setup_ui(self):
        """Set up the user interface."""
        self.columnconfigure(0, weight=1)
        
        # Toggle bar (always visible)
        self._toggle_frame = ttk.Frame(self)
        self._toggle_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(5, 0))
        self._toggle_frame.columnconfigure(1, weight=1)
        
        # Toggle button
        self._toggle_btn = ttk.Button(self._toggle_frame, text="▶ Console Output", width=20, command=self.toggle_visibility)
        self._toggle_btn.grid(row=0, column=0, sticky=tk.W)
        
        # Spacer
        ttk.Frame(self._toggle_frame).grid(row=0, column=1, sticky=(tk.W, tk.E))
        
        # Content frame (collapsible)
        self._content_frame = ttk.Frame(self)
        self._content_frame.columnconfigure(0, weight=1)
        self._content_frame.rowconfigure(1, weight=1)
        
        # Toolbar with controls
        toolbar = ttk.Frame(self._content_frame)
        toolbar.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(5, 2))
        
        clear_btn = ttk.Button(toolbar, text="Clear", command=self.clear)
        clear_btn.grid(row=0, column=0, padx=(0, 5))
        
        self._auto_scroll_var = tk.BooleanVar(value=True)
        auto_scroll_cb = ttk.Checkbutton(toolbar, text="Auto-scroll", variable=self._auto_scroll_var, command=self._on_auto_scroll_toggled)
        auto_scroll_cb.grid(row=0, column=1)
        
        # Text display area
        self._text_area = scrolledtext.ScrolledText(self._content_frame, height=self._height, wrap=tk.WORD, state=tk.DISABLED)
        self._text_area.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(2, 0))
        
        # Configure tags for stdout/stderr coloring
        self._text_area.tag_configure("stderr", foreground="red")
        self._text_area.tag_configure("stdout", foreground="")
        
        # Set initial visibility
        if self._visible:
            self._content_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
            self._toggle_btn.configure(text="▼ Console Output")
            self.rowconfigure(1, weight=1)
        else:
            self.rowconfigure(1, weight=0)


    def _setup_streams(self):
        """Set up stdout/stderr redirection."""
        if self._capture_stdout:
            self._stdout_stream = TkTextStream(self._original_stdout, source="stdout", write_callback=self._on_text_written, pass_through=self._pass_through)
            sys.stdout = self._stdout_stream
        
        if self._capture_stderr:
            self._stderr_stream = TkTextStream(self._original_stderr, source="stderr", write_callback=self._on_text_written, pass_through=self._pass_through)
            sys.stderr = self._stderr_stream


    def _on_text_written(self, text: str, source: str):
        """Handle text written from captured streams (called from any thread)."""
        if self._shutting_down:
            return
        
        # Queue the text for thread-safe processing
        try:
            self._text_queue.put_nowait((text, source))
        except queue.Full:
            pass  # Drop if queue is full
        
        # Schedule UI update if not already scheduled.
        # Never call root.after() while holding _update_lock: on Windows, after()
        # from a worker can block on the Tcl lock held by MainThread (e.g. during
        # WM_DELETE_WINDOW / on_closing). If MainThread then prints into this
        # callback and waits for _update_lock, the process deadlocks (Not Responding).
        should_schedule = False
        with self._update_lock:
            if not self._update_scheduled:
                self._update_scheduled = True
                should_schedule = True

        if should_schedule:
            try:
                self._root.after(10, self._process_text_queue)
            except Exception:
                with self._update_lock:
                    self._update_scheduled = False


    def _process_text_queue(self):
        """Process queued text updates (called on main thread)."""
        if self._shutting_down:
            return
        
        with self._update_lock:
            self._update_scheduled = False
        
        # Process all queued items
        items_to_process = []
        try:
            while True:
                items_to_process.append(self._text_queue.get_nowait())
        except queue.Empty:
            pass
        
        if not items_to_process:
            return
        
        try:
            self._text_area.configure(state=tk.NORMAL)
            
            for text, source in items_to_process:
                tag = "stderr" if source == "stderr" else "stdout"
                self._text_area.insert(tk.END, text, tag)
            
            # Limit buffer size
            self._enforce_line_limit()
            
            self._text_area.configure(state=tk.DISABLED)
            
            # Auto-scroll if enabled
            if self._auto_scroll:
                self._text_area.see(tk.END)
                
        except tk.TclError:
            # Widget is being destroyed
            pass


    def _enforce_line_limit(self):
        """Remove old lines if buffer exceeds max_lines."""
        try:
            line_count = int(self._text_area.index('end-1c').split('.')[0])
            if line_count > self._max_lines:
                # Calculate how many lines to remove
                lines_to_remove = line_count - self._max_lines
                self._text_area.delete('1.0', f'{lines_to_remove + 1}.0')
        except (tk.TclError, ValueError):
            pass


    def toggle_visibility(self):
        """Toggle the visibility of the console output panel."""
        self._visible = not self._visible
        
        if self._visible:
            self._content_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
            self._toggle_btn.configure(text="▼ Console Output")
            self.rowconfigure(1, weight=1)
        else:
            self._content_frame.grid_forget()
            self._toggle_btn.configure(text="▶ Console Output")
            self.rowconfigure(1, weight=0)


    def set_visible(self, visible: bool):
        """Set the visibility of the console output panel."""
        if visible != self._visible:
            self.toggle_visibility()


    @property
    def is_visible(self) -> bool:
        """Return whether the console panel is currently visible."""
        return self._visible


    def _on_auto_scroll_toggled(self):
        """Handle auto-scroll checkbox toggle."""
        self._auto_scroll = self._auto_scroll_var.get()


    def clear(self):
        """Clear the text display."""
        try:
            self._text_area.configure(state=tk.NORMAL)
            self._text_area.delete('1.0', tk.END)
            self._text_area.configure(state=tk.DISABLED)
        except tk.TclError:
            pass


    def append_text(self, text: str, source: str = "manual"):
        """Public method to append text programmatically.
        
        Args:
            text: The text to append.
            source: Source identifier for the text. Defaults to "manual".
        """
        self._on_text_written(text, source)


    def set_capture(self, stdout: bool, stderr: bool):
        """Enable or disable stream capture at runtime.
        
        Args:
            stdout: Whether to capture stdout.
            stderr: Whether to capture stderr.
        """
        # Handle stdout
        if stdout and not self._capture_stdout:
            self._stdout_stream = TkTextStream(self._original_stdout, source="stdout", write_callback=self._on_text_written, pass_through=self._pass_through)
            sys.stdout = self._stdout_stream
            self._capture_stdout = True
        elif not stdout and self._capture_stdout:
            if sys.stdout is self._stdout_stream:
                sys.stdout = self._original_stdout
            self._stdout_stream = None
            self._capture_stdout = False
        
        # Handle stderr
        if stderr and not self._capture_stderr:
            self._stderr_stream = TkTextStream(self._original_stderr, source="stderr", write_callback=self._on_text_written, pass_through=self._pass_through)
            sys.stderr = self._stderr_stream
            self._capture_stderr = True
        elif not stderr and self._capture_stderr:
            if sys.stderr is self._stderr_stream:
                sys.stderr = self._original_stderr
            self._stderr_stream = None
            self._capture_stderr = False


    def restore_streams(self):
        """Restore original stdout/stderr streams."""
        self._shutting_down = True
        
        if self._stdout_stream is not None:
            self._stdout_stream.set_callback(None)
            if sys.stdout is self._stdout_stream:
                sys.stdout = self._original_stdout
        
        if self._stderr_stream is not None:
            self._stderr_stream.set_callback(None)
            if sys.stderr is self._stderr_stream:
                sys.stderr = self._original_stderr


    def destroy(self):
        """Handle widget destruction."""
        self.restore_streams()
        super().destroy()
