from typing import Dict, List, Tuple, Optional, Callable, Union, Any
from copy import deepcopy
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import pylsl
import pyxdf
from datetime import datetime, timedelta
import pytz
import os
import threading
import time
import numpy as np
import json
import pickle
import mne
from pathlib import Path
import pystray
from PIL import Image, ImageDraw
import sys
from phopylslhelper.general_helpers import unwrap_single_element_listlike_if_needed, readable_dt_str, from_readable_dt_str, localize_datetime_to_timezone, tz_UTC, tz_Eastern, _default_tz
from phopylslhelper.easy_time_sync import EasyTimeSyncParsingMixin
from phopylslhelper.mixins.app_helpers import SingletonInstanceMixin, AppThemeMixin, SystemTrayAppMixin
from phologtolabstreaminglayer.startup_timing import mark

mark("logger_app.py import begin")
from whisper_timestamped.mixins.live_whisper_transcription import LiveWhisperTranscriptionAppMixin
mark("live whisper mixin import complete")
from labrecorder import LabRecorder
from phologtolabstreaminglayer.features.global_hotkey import GlobalHotkeyMixin
from phologtolabstreaminglayer.features.recording_indicator_icon import RecordingIndicatorIconMixin
from phologtolabstreaminglayer.features.console_output_tk import ConsoleOutputFrame

mark("logger_app.py imports complete")

# program_lock_port = int(os.environ.get("LIVE_WHISPER_LOCK_PORT", 13372))
# program_lock_port = int(os.environ.get("PHO_LOGTOLABSTREAMINGLAYER_LOCK_PORT", 13379))  # No longer needed - using file-based locking

_DEFAULT_XDF_FOLDER_RAW = Path(r'E:\Dropbox (Personal)\Databases\UnparsedData\PhoLogToLabStreamingLayer_logs')
# _default_xdf_folder = Path('/media/halechr/MAX/cloud/University of Michigan Dropbox/Pho Hale/Personal/LabRecordedTextLog').resolve() ## Lab computer
_resolved_default_xdf_folder: Optional[Path] = None


def get_default_xdf_folder() -> Path:
    global _resolved_default_xdf_folder
    if _resolved_default_xdf_folder is None:
        _resolved_default_xdf_folder = _DEFAULT_XDF_FOLDER_RAW.resolve()
    return _resolved_default_xdf_folder


class LoggerApp(RecordingIndicatorIconMixin, GlobalHotkeyMixin, AppThemeMixin, SystemTrayAppMixin, SingletonInstanceMixin, LiveWhisperTranscriptionAppMixin, EasyTimeSyncParsingMixin):
    # Class variable to track if an instance is already running
    # _SingletonInstanceMixin_env_lock_port_variable_name: str = "LIVE_WHISPER_LOCK_PORT"
    _SingletonInstanceMixin_env_lock_file_name: str = "PHO_LOGTOLABSTREAMINGLAYER_LOCK_FILE"

    # _instance_running = False

    # _default_xdf_folder = Path(r'E:\Dropbox (Personal)\Databases\UnparsedData\PhoLogToLabStreamingLayer_logs').resolve()
    xdf_folder: Path = None # Path('/media/halechr/MAX/cloud/University of Michigan Dropbox/Pho Hale/Personal/LabRecordedTextLog').resolve() ## Lab computer
    
    def __init__(self, root, xdf_folder=None):

        self.init_SingletonInstanceMixin()

        self.root = root
        self.root.title("LSL Logger with XDF Recording")
        self.root.geometry("900x720") # WxH
        
        self.stream_names = ['TextLogger', 'EventBoard', 'WhisperLiveLogger'] # : List[str]

        # Set application icon
        self.setup_app_icon()
        self.xdf_folder = (xdf_folder or get_default_xdf_folder())

        # Recording state
        self.recording = False
        self.recording_thread = None
        # self.inlet = None
        self.inlets = {}
        self.outlets = {}

        self.recorded_data = []
        # self.recording_start_lsl_local_offset = None
        # self.recording_start_datetime = None

        self.init_EasyTimeSyncParsingMixin()
        # Live transcription state
        self.init_LiveWhisperTranscriptionAppMixin()

        # System tray and hotkey state
        self.init_SystemTrayAppMixin()
        
        # Global hotkey state
        self.init_GlobalHotkeyMixin()
        
        # Windows taskbar recording indicator
        self.init_RecordingIndicatorIconMixin()
        
        # # Singleton lock socket
        # self._lock_socket = None
        
        # Shutdown flag to prevent GUI updates during shutdown
        self._shutting_down = False
        
        # Timestamp tracking for text entry
        self.main_text_start_editing_timestamp = None
        self.popover_text_timestamp = None
        
        # EventBoard configuration and outlet
        self.eventboard_config = None
        self.eventboard_outlet = None
        self.eventboard_buttons = {}
        self.eventboard_toggle_states = {}  # Track toggle states
        self.eventboard_time_offsets = {}   # Track time offset dropdowns
        
        # Lab-recorder integration
        self.lab_recorder: Optional[LabRecorder] = None
        self.lab_recorder_service_client = None
        self.discovered_streams: Dict[str, pylsl.StreamInfo] = {}
        self.selected_streams: set = set()
        self._stream_discovery_lock = threading.Lock()  # Lock for thread-safe access to discovered_streams and selected_streams
        self.stream_monitor_thread: Optional[threading.Thread] = None
        self.stream_discovery_active = False
        self.auto_start_attempted = False  # Track if we've tried to auto-start recording
        
        self.capture_stream_start_timestamps() ## `EasyTimeSyncParsingMixin`: capture timestamps for use in LSL streams
        self.capture_recording_start_timestamps() ## capture timestamps for use in LSL streams

        # Load EventBoard configuration
        self.load_eventboard_config()
        
        # Create GUI elements first
        self.setup_gui()
        mark("setup_gui complete")
        
        # Check for recovery files after the UI is responsive
        self.root.after(100, self.check_for_recovery)
        
        # Create LSL outlets in background thread to avoid blocking GUI
        threading.Thread(target=self.setup_lsl_outlet, daemon=True).start()

        ## setup transcirption (model load runs in a background thread inside the mixin)
        self.root.after(200, self.auto_start_live_transcription)

        # Setup system tray and global hotkey
        self.setup_SystemTrayAppMixin()
        
        # Initialize lab-recorder integration
        self.init_lab_recorder()
        
        # Start stream discovery after a short delay to allow outlets to be created
        self.root.after(2000, self.start_stream_discovery)
        mark("LoggerApp.__init__ end")


    @property
    def eventboard_outlet(self) -> Optional[pylsl.StreamOutlet]:
        """The eventboard_outlet property."""
        return self.outlets['EventBoard']
    @eventboard_outlet.setter
    def eventboard_outlet(self, value):
        self.outlets['EventBoard'] = value    

    @property
    def outlet_TextLogger(self) -> Optional[pylsl.StreamOutlet]:
        """The outlet_TextLogger property."""
        return self.outlets['TextLogger']
    @outlet_TextLogger.setter
    def outlet_TextLogger(self, value):
        self.outlets['TextLogger'] = value

    @property
    def has_any_inlets(self) -> bool:
        """The has_any_inlets property."""
        if self._shutting_down:
            return False
        return (self.inlets is not None) and (len(self.inlets) > 0)


    def parse_time_offset(self, time_str):
        """Parse time offset string (e.g., '5s', '2m', '1h') to seconds"""
        if not time_str or not time_str.strip():
            return 0

        time_str = time_str.strip().lower()

        # Extract number and unit
        import re
        match = re.match(r'^(\d+(?:\.\d+)?)\s*([smh]?)$', time_str)

        if not match:
            return 0

        value = float(match.group(1))
        unit = match.group(2) or 's'  # Default to seconds if no unit

        # Convert to seconds
        if unit == 's':
            return value
        elif unit == 'm':
            return value * 60
        elif unit == 'h':
            return value * 3600
        else:
            return 0



    # ---------------------------------------------------------------------------- #
    #                               Recording Methods                              #
    # ---------------------------------------------------------------------------- #

    def setup_recording_inlet(self):
        """Setup inlet to record our own stream

        Modifies: self.inlets

        """

        # self.inlets = {}

        stream_names: List[str] = self.stream_names # ['TextLogger', 'EventBoard', 'WhisperLiveLogger']
        were_any_success: bool = False
        for a_stream_name in stream_names:
            should_remove_stream: bool = False
            try:
                # Look for our own stream
                found_streams = pylsl.resolve_byprop('name', a_stream_name, timeout=2.0)
                if found_streams:
                    self.inlets[a_stream_name] = pylsl.StreamInlet(found_streams[0])
                    print("Recording inlet created successfully")
                    were_any_success = True                        
                else:
                    print(f"Could not find '{a_stream_name}' stream for recording")
                    should_remove_stream = True

            except Exception as e:
                print(f"Error creating recording inlet for stream named '{a_stream_name}': {e}")
                should_remove_stream = True

            if (a_stream_name in self.inlets) and should_remove_stream:
                _a_removed_stream = self.inlets.pop(a_stream_name)
                if _a_removed_stream is not None:
                    print(f'WARN: removed stream named "{a_stream_name}" from self.inlets.')

        ## END for a_stream_name in stream_names...
        # Update LSL status label when inlets are successfully set up
        if were_any_success and self.inlets:
            connected_streams = ', '.join(self.inlets.keys())
            try:
                if not self._shutting_down:
                    self.root.after(0, lambda streams=connected_streams: self.lsl_status_label.config(text=f"LSL Status: Ready - {streams}", foreground="green"))
            except tk.TclError:
                pass  # GUI is being destroyed
        # Note: Auto-start recording is now triggered after streams are discovered
        # (see stream_discovery_worker for when new streams are found)


    def setup_lsl_outlet(self):
        """Create an LSL outlet for sending messages

        Modifies: self.inlets

        """
        stream_setup_fn_dict: Dict = {
            'TextLogger': self.setup_TextLogger_outlet,
            'EventBoard': self.setup_eventboard_outlet,
            'WhisperLiveLogger': self.setup_lsl_outlet_LiveWhisperTranscriptionAppMixin,
        }
        were_any_success: bool = False
        print(f'setup_lsl_outlet():')
        print(f'\tstream_setup_fn_dict: {stream_setup_fn_dict}')
        for a_stream_name, a_setup_fn in stream_setup_fn_dict.items():
            try:
                # Create stream info
                a_setup_fn() ## just setup
                were_any_success = True
            
                # Update LSL status label safely
                try:
                    if not self._shutting_down:
                        self.root.after(0, lambda name=a_stream_name: self.lsl_status_label.config(text=f"LSL Status: Connected - {name}", foreground="green"))
                except tk.TclError:
                    pass  # GUI is being destroyed
                print(f'\tfinished: "{a_stream_name}" setup.')
                
            except Exception as e:
                print(f'\terror in "{a_stream_name}" setup: {e}')
                try:
                    if not self._shutting_down:
                        self.root.after(0, lambda name=a_stream_name, err=str(e): self.lsl_status_label.config(text=f"LSL Status: Error - {name} - {err}", foreground="red"))
                except tk.TclError:
                    pass  # GUI is being destroyed

        ## END for a_stream_name, a_setup_fn in stream_setup_fn_dict...
        print(f'done.')
        if not were_any_success:
            print('setup_lsl_outlet(): no LSL outlets were created successfully')

        if were_any_success:
            # Setup inlet for recording our own stream (with delay to allow outlet to be discovered)
            # Run in background thread to avoid blocking GUI, but schedule GUI updates on main thread
            def delayed_setup_inlet():
                time.sleep(1.0)  # Wait for outlets to be discoverable
                self.setup_recording_inlet()
            threading.Thread(target=delayed_setup_inlet, daemon=True).start()
   

    def setup_TextLogger_outlet(self):
        """Create an LSL outlet for sending messages"""
        try:
            # Create stream info
            info = pylsl.StreamInfo(
                name='TextLogger',
                type='Markers',
                channel_count=1,
                nominal_srate=pylsl.IRREGULAR_RATE,
                channel_format=pylsl.cf_string,
                source_id='textlogger_001'
            )
            
            # Add some metadata
            info.desc().append_child_value("manufacturer", "PhoLogToLabStreamingLayer")
            info.desc().append_child_value("version", "2.1")
            info.desc().append_child_value("description", "TextLogger user entered text logs events")

            ## add a custom timestamp field to the stream info:
            info = self.EasyTimeSyncParsingMixin_add_lsl_outlet_info(info=info)

            # Create outlet
            self.outlet_TextLogger = pylsl.StreamOutlet(info)
            print("TextLogger LSL outlet created successfully")

        except Exception as e:
            print(f"Error creating TextLogger LSL outlet: {e}")
            self.outlet_TextLogger = None
            try:
                if not self._shutting_down:
                    self.lsl_status_label.config(text=f"LSL Status: Error - {str(e)}", foreground="red")
            except tk.TclError:
                pass  # GUI is being destroyed
            

    def setup_eventboard_outlet(self):
        """Create an LSL outlet for EventBoard events"""
        try:
            # Create stream info for EventBoard
            info = pylsl.StreamInfo(
                name='EventBoard',
                type='Markers',
                channel_count=1,
                nominal_srate=pylsl.IRREGULAR_RATE,
                channel_format=pylsl.cf_string,
                source_id='eventboard_001'
            )
            
            # Add some metadata
            info.desc().append_child_value("manufacturer", "PhoLogToLabStreamingLayer")
            info.desc().append_child_value("version", "2.1")
            info.desc().append_child_value("description", "EventBoard button events")

            ## add a custom timestamp field to the stream info:
            info = self.EasyTimeSyncParsingMixin_add_lsl_outlet_info(info=info)
            
            # assert (self.recording_start_lsl_local_offset is not None), f"recording_start_lsl_local_offset is None"
            # # if self.recording_start_lsl_local_offset is not None:
            # info.desc().append_child_value("recording_start_lsl_local_offset_seconds", str(self.recording_start_lsl_local_offset))

            # ## add a custom timestamp field to the stream info:
            # assert (self.recording_start_datetime is not None), f"recording_start_datetime is None"
            # # if self.recording_start_datetime is not None:
            # info.desc().append_child_value("recording_start_datetime", readable_dt_str(self.recording_start_datetime))
            
            # Create outlet
            self.outlets['EventBoard'] = pylsl.StreamOutlet(info)
            print("EventBoard LSL outlet created successfully")
            
        except Exception as e:
            print(f"Error creating EventBoard LSL outlet: {e}")
            self.outlets['EventBoard'] = None
    

    # ==================================================================================================================================================================================================================================================================================== #
    # Other GUI/Status Methods                                                                                                                                                                                                                                                             #
    # ==================================================================================================================================================================================================================================================================================== #

    def ensure_focus(self):
        """Ensure the text entry field has focus"""
        if self.hotkey_popover and self.quick_log_entry:
            self.quick_log_entry.focus_force()
            self.quick_log_entry.select_range(0, tk.END)
    
    def on_main_text_change(self, event=None):
        """Track when user first types in main text field"""
        if self.main_text_start_editing_timestamp is None:
            self.main_text_start_editing_timestamp = datetime.now()
    
    def on_popover_text_change(self, event=None):
        """Track when user first types in popover text field"""
        if self.popover_text_timestamp is None:
            self.popover_text_timestamp = datetime.now()
    
    def on_main_text_clear(self, event=None):
        """Reset timestamp when main text field is cleared"""
        if event and event.keysym in ['BackSpace', 'Delete']:
            # Check if field is now empty
            if not self.text_entry.get().strip():
                self.main_text_start_editing_timestamp = None
    
    def on_popover_text_clear(self, event=None):
        """Reset timestamp when popover text field is cleared"""
        if event and event.keysym in ['BackSpace', 'Delete']:
            # Check if field is now empty
            if not self.quick_log_entry.get().strip():
                self.popover_text_timestamp = None
    
    def get_main_text_timestamp(self):
        """Get the timestamp when user first started typing in main field"""
        if self.main_text_start_editing_timestamp:
            ## check if we were previously editing (meaning the self.main_text_timestamp) was set to a previous datetime:
            timestamp = deepcopy(self.main_text_start_editing_timestamp)
            self.main_text_start_editing_timestamp = None  # Reset for next entry
            return timestamp
        else:
            ## otherwise return the current datetime 
            return datetime.now()
    
    def get_popover_text_timestamp(self):
        """Get the timestamp when user first started typing in popover field"""
        if self.popover_text_timestamp:
            timestamp = deepcopy(self.popover_text_timestamp)
            self.popover_text_timestamp = None  # Reset for next entry
            return timestamp
        return datetime.now()
    
    def center_popover_on_active_monitor(self):
        """Center the popover on the currently active monitor"""
        try:
            # Get screen dimensions and center the popover
            screen_width = self.hotkey_popover.winfo_screenwidth()
            screen_height = self.hotkey_popover.winfo_screenheight()
            x = (screen_width - 400) // 2
            y = (screen_height - 150) // 2
            self.hotkey_popover.geometry(f"+{x}+{y}")
                
        except Exception as e:
            print(f"Error centering popover: {e}")
            # Fallback to screen center
            screen_width = self.hotkey_popover.winfo_screenwidth()
            screen_height = self.hotkey_popover.winfo_screenheight()
            x = (screen_width - 400) // 2
            y = (screen_height - 150) // 2
            self.hotkey_popover.geometry(f"+{x}+{y}")
    
    def quick_log_and_close(self):
        """Log the message and close the popover"""
        message = self.quick_log_entry.get().strip()
        if message:
            # Send LSL message
            self.send_lsl_message(message)
            
            # Update main app display if visible
            if not self.is_minimized:
                # Use the timestamp when user first started typing in popover
                timestamp = self.get_popover_text_timestamp().strftime("%Y-%m-%d %H:%M:%S")
                self.update_log_display(message, timestamp)
            
            # Clear entry
            self.quick_log_entry.delete(0, tk.END)
        
        # Close popover
        self.close_hotkey_popover()
    
    def close_hotkey_popover(self):
        """Close the hotkey popover"""
        if self.hotkey_popover:
            self.hotkey_popover.destroy()
            self.hotkey_popover = None
    

    # SystemTrayAppMixin _________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________ #
    def setup_system_tray(self):
        """Setup system tray icon and menu"""
        try:
            # Create a simple icon (you can replace this with a custom icon file)
            icon_image = self.create_tray_icon()

            # Create system tray menu
            menu = pystray.Menu(
                pystray.MenuItem("Show App", self.on_tray_activate, default=True),
                pystray.MenuItem("Quick Log", self.show_hotkey_popover),
                pystray.MenuItem("Exit", self.quit_app)
            )

            # Create system tray icon
            self.system_tray = pystray.Icon(
                "logger_app",
                icon_image,
                "LSL Logger",
                menu
            )

            # Add click/activation handlers to show app
            # Single left-click handler (if supported by backend)
            try:
                self.system_tray.on_clicked = self.on_tray_clicked
            except Exception as _e:
                # Fallback silently if backend does not support on_clicked
                pass

            # Double-click/activate handler
            self.system_tray.on_activate = self.on_tray_activate ## double-clicking should restore only when hidden

            # Start system tray in a separate thread
            threading.Thread(target=self.system_tray.run, daemon=True).start()

        except Exception as e:
            print(f"Error setting up system tray: {e}")

    def on_tray_clicked(self, icon=None, button=None, pressed=None, *args, **kwargs):
        """Handle single left-click on the tray icon to restore app when hidden"""
        try:
            # Normalize button value across backends ('left', 'LEFT', 1, etc.)
            btn = str(button).lower() if isinstance(button, str) else button
            is_left = (btn in ('left', 'button.left')) or (btn == 1)

            # Only act on press (avoid triggering twice on press/release)
            is_pressed = (pressed is True) or (pressed is None)

            if is_left and is_pressed:
                if getattr(self, 'is_minimized', False) or not self.root.winfo_viewable():
                    self.root.after(0, self.restore_from_tray)
                else:
                    # If already visible, just bring to front on single click
                    self.root.after(0, self.show_app)
        except Exception as e:
            print(f"Error handling tray click: {e}")

    def on_tray_activate(self, icon=None, item=None):
        """Handle tray icon activation (double-click on Windows) to restore app only if hidden"""
        try:
            # Restore only when minimized/hidden
            if getattr(self, 'is_minimized', False) or not self.root.winfo_viewable():
                # Perform Tkinter operations on the main thread
                self.root.after(0, self.restore_from_tray)
        except Exception as e:
            print(f"Error handling tray activation: {e}")

    def create_tray_icon(self):
        """Create icon for the system tray from PNG file based on system theme"""
        try:
            # Use the same theme detection as the main icon
            icon_filename = self.get_theme_appropriate_icon()
            icon_path = Path("icons") / icon_filename

            if icon_path.exists():
                # Load and resize the PNG icon for system tray
                image = Image.open(str(icon_path))
                # Resize to appropriate size for system tray (16x16 or 32x32)
                image = image.resize((16, 16), Image.Resampling.LANCZOS)
                return image
            else:
                print(f"Tray icon file not found: {icon_path}, using default")
                return self.create_default_tray_icon()
        except Exception as e:
            print(f"Error loading tray icon: {e}, using default")
            return self.create_default_tray_icon()

    def create_default_tray_icon(self):
        """Create a simple default icon for the system tray"""
        # Create a 16x16 icon with a simple design
        width = 16
        height = 16

        # Create image with a dark background
        image = Image.new('RGB', (width, height), color='#2c3e50')
        draw = ImageDraw.Draw(image)

        # Draw a simple "L" shape in white
        draw.rectangle([2, 2, 6, 14], fill='white')  # Vertical line
        draw.rectangle([2, 10, 12, 14], fill='white')  # Horizontal line

        return image

    def show_app(self):
        """Show the main application window"""
        self.is_minimized = False
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
    
    def minimize_to_tray(self):
        """Minimize the app to system tray"""
        self.is_minimized = True
        self.root.withdraw()  # Hide the window
        try:
            if not self._shutting_down:
                self.minimize_button.config(text="Restore from Tray")
        except tk.TclError:
            pass  # GUI is being destroyed
    
    def restore_from_tray(self):
        """Restore the app from system tray"""
        self.is_minimized = False
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        try:
            if not self._shutting_down:
                self.minimize_button.config(text="Minimize to Tray")
        except tk.TclError:
            pass  # GUI is being destroyed
    

    # Application State
    def toggle_minimize(self):
        """Toggle between minimize and restore"""
        if self.is_minimized:
            self.restore_from_tray()
        else:
            self.minimize_to_tray()
    
    def quit_app(self):
        """Quit the application completely"""
        self.on_closing()


    # Resume _____________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________ #
    def setup_gui(self):
        """Create the GUI elements"""
        # Configure root grid - three rows: notebook (top), log display (middle), console output (bottom)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)  # Notebook row
        self.root.rowconfigure(1, weight=1)  # Log display row
        self.root.rowconfigure(2, weight=0)  # Console output row (collapsible, no weight)

        # Create tabbed notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Create tabs
        recording_tab = ttk.Frame(self.notebook, padding="10")
        live_audio_tab = ttk.Frame(self.notebook, padding="10")
        eventboard_tab = ttk.Frame(self.notebook, padding="10")
        manual_tab = ttk.Frame(self.notebook, padding="10")
        settings_tab = ttk.Frame(self.notebook, padding="10")

        self.notebook.add(recording_tab, text="Recording")
        self.notebook.add(live_audio_tab, text="Live Audio")
        self.notebook.add(eventboard_tab, text="EventBoard")
        self.notebook.add(manual_tab, text="Manual Log")
        self.notebook.add(settings_tab, text="Settings")

        # Configure tab grids
        for tab in (recording_tab, live_audio_tab, eventboard_tab, manual_tab, settings_tab):
            tab.columnconfigure(0, weight=1)

        # ------------------------- Recording Tab -------------------------
        # LSL Status label
        self.lsl_status_label = ttk.Label(recording_tab, text="LSL Status: Initializing...", foreground="orange")
        self.lsl_status_label.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))

        # Recording control frame
        recording_frame = ttk.LabelFrame(recording_tab, text="XDF Recording", padding="5")
        recording_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        recording_frame.columnconfigure(1, weight=1)

        # Recording status
        self.recording_status_label = ttk.Label(recording_frame, text="Not Recording", foreground="red")
        self.recording_status_label.grid(row=0, column=0, sticky=tk.W, padx=(0, 10))

        # Recording buttons
        self.start_recording_button = ttk.Button(recording_frame, text="Start Recording", command=self.start_recording)
        self.start_recording_button.grid(row=0, column=1, padx=5)

        self.stop_recording_button = ttk.Button(recording_frame, text="Stop Recording", command=self.stop_recording, state="disabled")
        self.stop_recording_button.grid(row=0, column=2, padx=5)

        # Split Recording button
        self.split_recording_button = ttk.Button(recording_frame, text="Split Recording", command=self.split_recording, state="disabled")
        self.split_recording_button.grid(row=0, column=3, padx=5)

        # Minimize to Tray button
        self.minimize_button = ttk.Button(recording_frame, text="Minimize to Tray", command=self.toggle_minimize)
        self.minimize_button.grid(row=0, column=4, padx=5)

        # Stream Monitor within Recording tab
        self.setup_stream_monitor_gui(recording_tab, row=2)

        # ------------------------- Live Audio Tab -------------------------
        self.setup_gui_LiveWhisperTranscriptionAppMixin(live_audio_tab, row=0)

        # ------------------------- EventBoard Tab -------------------------
        self.setup_eventboard_gui(eventboard_tab, row=0)

        # ------------------------- Manual Log Tab -------------------------
        next_row: int = 0
        input_frame = ttk.Frame(manual_tab)
        input_frame.grid(row=next_row, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        next_row = next_row + 1
        input_frame.columnconfigure(1, weight=1)

        ttk.Label(input_frame, text="Message:").grid(row=0, column=0, sticky=tk.W, padx=(0, 10))

        # Text input box
        self.text_entry = tk.Entry(input_frame, width=50)
        self.text_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(0, 10))
        self.text_entry.bind('<Return>', lambda event: self.log_message())
        self.text_entry.bind('<Key>', self.on_main_text_change)  # Track first keystroke
        self.text_entry.bind('<BackSpace>', self.on_main_text_clear)
        self.text_entry.bind('<Delete>', self.on_main_text_clear)

        # Log button
        self.log_button = ttk.Button(input_frame, text="Log", command=self.log_message)
        self.log_button.grid(row=0, column=2)

        # Bottom frame for status info
        bottom_frame = ttk.Frame(manual_tab)
        bottom_frame.grid(row=next_row, column=0, sticky=(tk.W, tk.E))
        next_row = next_row + 1
        bottom_frame.columnconfigure(0, weight=1)

        # Status info
        self.status_info_label = ttk.Label(bottom_frame, text="Ready")
        self.status_info_label.grid(row=0, column=0, sticky=tk.E)

        # Focus on text entry
        self.text_entry.focus()

        # ------------------------- Settings Tab -------------------------
        ttk.Label(settings_tab, text="Settings will appear here.").grid(row=0, column=0, sticky=tk.W, pady=(0, 10))

        # Keyboard shortcuts for tab switching (Ctrl+1..5)
        def _select_tab(index: int):
            try:
                self.notebook.select(index)
            except tk.TclError:
                pass
        self.root.bind_all('<Control-1>', lambda e: _select_tab(0))
        self.root.bind_all('<Control-2>', lambda e: _select_tab(1))
        self.root.bind_all('<Control-3>', lambda e: _select_tab(2))
        self.root.bind_all('<Control-4>', lambda e: _select_tab(3))
        self.root.bind_all('<Control-5>', lambda e: _select_tab(4))

        # Default to Recording tab
        self.notebook.select(0)

        # ------------------------- Top-Level Log Display Area -------------------------
        # Create frame for log display at root level (bottom half of window)
        log_display_frame = ttk.Frame(self.root, padding="10")
        log_display_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        log_display_frame.columnconfigure(0, weight=1)
        log_display_frame.rowconfigure(1, weight=1)  # ScrolledText row

        # Log History label
        ttk.Label(log_display_frame, text="Log History:").grid(row=0, column=0, sticky=(tk.W, tk.N), pady=(0, 5))

        # Scrolled text widget for log history
        self.log_display = scrolledtext.ScrolledText(log_display_frame, height=15, width=70)
        self.log_display.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Bottom frame for clear button
        log_bottom_frame = ttk.Frame(log_display_frame)
        log_bottom_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(5, 0))
        log_bottom_frame.columnconfigure(0, weight=1)

        # Clear log button
        ttk.Button(log_bottom_frame, text="Clear Log Display", command=self.clear_log_display).grid(row=0, column=0, sticky=tk.W)
        
        # ------------------------- Console Output Panel (Collapsible) -------------------------
        # Create collapsible console output frame for stdout/stderr capture
        self.console_output_frame = ConsoleOutputFrame(self.root, root=self.root, capture_stdout=True, capture_stderr=True, max_lines=10000, initial_visible=False, height=8)
        self.console_output_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=(0, 10))
    

    def setup_stream_monitor_gui(self, parent, row: int = 2):
        """Setup Stream Monitor GUI for displaying discovered LSL streams"""
        # Stream Monitor frame
        stream_frame = ttk.LabelFrame(parent, text="LSL Stream Monitor", padding="10")
        stream_frame.grid(row=row, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        stream_frame.columnconfigure(0, weight=1)
        
        # Stream list with scrollbar
        list_frame = ttk.Frame(stream_frame)
        list_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        
        # Create Treeview for stream display
        columns = ('Select', 'Type', 'Channels', 'Rate', 'Status')
        self.stream_tree = ttk.Treeview(list_frame, columns=columns, show='tree headings', height=6)
        
        # Configure column headings and widths
        self.stream_tree.heading('#0', text='Name')
        self.stream_tree.column('#0', width=120, minwidth=100)
        
        for col in columns:
            self.stream_tree.heading(col, text=col)
            if col == 'Select':
                self.stream_tree.column(col, width=60, minwidth=60)
            elif col == 'Type':
                self.stream_tree.column(col, width=80, minwidth=60)
            elif col == 'Channels':
                self.stream_tree.column(col, width=70, minwidth=50)
            elif col == 'Rate':
                self.stream_tree.column(col, width=70, minwidth=50)
            elif col == 'Status':
                self.stream_tree.column(col, width=80, minwidth=60)
        
        # Add scrollbar
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.stream_tree.yview)
        self.stream_tree.configure(yscrollcommand=scrollbar.set)
        
        # Grid the treeview and scrollbar
        self.stream_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        
        # Bind selection events
        self.stream_tree.bind('<Button-1>', self.on_stream_tree_click)
        
        # Control buttons frame
        button_frame = ttk.Frame(stream_frame)
        button_frame.grid(row=1, column=0, sticky=(tk.W, tk.E))
        
        # Stream control buttons
        ttk.Button(button_frame, text="Refresh Streams", command=self.refresh_streams).grid(row=0, column=0, padx=(0, 5))
        ttk.Button(button_frame, text="Select All", command=self.select_all_streams).grid(row=0, column=1, padx=5)
        ttk.Button(button_frame, text="Select None", command=self.select_no_streams).grid(row=0, column=2, padx=5)
        ttk.Button(button_frame, text="Auto-Select Own", command=self.auto_select_own_streams).grid(row=0, column=3, padx=(5, 0))
        
        # Stream info label
        self.stream_info_label = ttk.Label(stream_frame, text="No streams discovered yet")
        self.stream_info_label.grid(row=2, column=0, sticky=tk.W, pady=(5, 0))
        
        # Initialize stream tracking
        self.stream_tree_items = {}  # Maps stream_key to tree item id
    
    # Eventboard methods _________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________ #

    def load_eventboard_config(self):
        """Load EventBoard configuration from file"""
        config_file = Path("eventboard_config.json")

        if not config_file.exists():
            print(f"EventBoard config file not found: {config_file}")
            print("Using default configuration")
            self.eventboard_config = self.get_default_eventboard_config()
            return

        try:
            with open(config_file, 'r') as f:
                config_data = json.load(f)
                self.eventboard_config = config_data.get('eventboard_config', {})
                print(f"EventBoard configuration loaded from {config_file}")
        except Exception as e:
            print(f"Error loading EventBoard config: {e}")
            print("Using default configuration")
            self.eventboard_config = self.get_default_eventboard_config()

    def get_default_eventboard_config(self):
        """Get default EventBoard configuration"""
        return {
            "title": "Event Board",
            "buttons": [
                {"id": f"button_{i}_{j}", "row": i, "col": j, "text": f"Button {i}-{j}", 
                 "event_name": f"EVENT_{i}_{j}", "color": "#2196F3", "type": "instantaneous"}
                for i in range(1, 4) for j in range(1, 6)
            ]
        }

    def setup_eventboard_gui(self, parent, row: int=2):
        """Setup EventBoard GUI with 3x5 grid of buttons and time offset dropdowns"""
        if not self.eventboard_config:
            return
        
        # EventBoard frame
        eventboard_frame = ttk.LabelFrame(parent, text=self.eventboard_config.get('title', 'Event Board'), padding="10")
        eventboard_frame.grid(row=row, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # Configure grid for 3 rows and 5 columns (each cell has button + dropdown)
        for i in range(3):
            eventboard_frame.rowconfigure(i, weight=1)
        for j in range(5):
            eventboard_frame.columnconfigure(j, weight=1)
        
        # Create buttons based on configuration
        buttons = self.eventboard_config.get('buttons', [])
        for button_config in buttons:
            row = button_config.get('row', 1) - 1  # Convert to 0-based indexing
            col = button_config.get('col', 1) - 1  # Convert to 0-based indexing
            text = button_config.get('text', 'Button')
            event_name = button_config.get('event_name', 'UNKNOWN_EVENT')
            color = button_config.get('color', '#2196F3')
            button_type = button_config.get('type', 'instantaneous')
            button_id = button_config.get('id', f'button_{row}_{col}')
            
            # Create container frame for button and dropdown with integrated styling
            cell_frame = tk.Frame(eventboard_frame, bg=color, relief="raised", bd=2)
            cell_frame.grid(row=row, column=col, sticky=(tk.W, tk.E, tk.N, tk.S), padx=2, pady=2)
            cell_frame.columnconfigure(0, weight=4)  # Button takes 80% of width
            cell_frame.columnconfigure(1, weight=1)  # Dropdown takes 20% of width
            cell_frame.rowconfigure(0, weight=1)
            
            # Create button with custom styling (no border to integrate with container)
            button_text = text
            if button_type == 'toggleable':
                button_text = f"🔘 {text}"  # Add indicator for toggleable buttons
            
            button = tk.Button(
                cell_frame,
                text=button_text,
                font=("Arial", 9, "bold"),
                bg=color,
                fg="white",
                relief="flat",  # Flat relief to integrate with container
                bd=0,  # No border
                padx=5,
                pady=5,
                command=lambda e=event_name, t=text, bt=button_type, bid=button_id: self.on_eventboard_button_click(e, t, bt, bid)
            )
            
            button.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(0, 1))
            
            # Create time offset entry with matching styling
            time_offset_var = tk.StringVar()
            time_offset_entry = tk.Entry(
                cell_frame,
                textvariable=time_offset_var,
                font=("Arial", 8),
                width=4,
                justify='center',
                bg=color,  # Match button background
                fg="white",  # Match button text color
                relief="flat",  # Flat relief to integrate
                bd=0,  # No border
                insertbackground="white"  # White cursor for visibility
            )
            time_offset_entry.grid(row=0, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(1, 0))
            
            # Add placeholder text
            time_offset_entry.insert(0, "0s")
            time_offset_entry.config(fg='lightgray')  # Light gray for better visibility on colored background
            
            # Bind events for placeholder behavior and Enter key
            time_offset_entry.bind('<FocusIn>', lambda e, entry=time_offset_entry: self.on_time_offset_focus_in(entry))
            time_offset_entry.bind('<FocusOut>', lambda e, entry=time_offset_entry: self.on_time_offset_focus_out(entry))
            time_offset_entry.bind('<Key>', lambda e, entry=time_offset_entry: self.on_time_offset_key(entry))
            time_offset_entry.bind('<Return>', lambda e, entry=time_offset_entry, bid=button_id: self.on_time_offset_enter(entry, bid))
            
            # Store references
            self.eventboard_buttons[button_id] = button
            self.eventboard_time_offsets[button_id] = time_offset_var
            
            # Store original color for toggleable buttons
            if button_type == 'toggleable':
                self.eventboard_toggle_states[button_id] = False
                # Store the original color for later restoration
                if not hasattr(self, 'eventboard_original_colors'):
                    self.eventboard_original_colors = {}
                self.eventboard_original_colors[button_id] = color
    
    def on_time_offset_focus_in(self, entry):
        """Handle focus in on time offset entry"""
        if entry.get() == "0s":
            entry.delete(0, tk.END)
            entry.config(fg='white')  # White text for active input
    
    def on_time_offset_focus_out(self, entry):
        """Handle focus out on time offset entry"""
        if not entry.get().strip():
            entry.insert(0, "0s")
            entry.config(fg='lightgray')  # Light gray for placeholder
        else:
            entry.config(fg='white')  # White text for actual content
    
    def on_time_offset_key(self, entry):
        """Handle key press in time offset entry"""
        entry.config(fg='white')  # White text when typing
    
    def on_time_offset_enter(self, entry, button_id):
        """Handle Enter key press in time offset entry - trigger button click"""
        # Get the button and trigger its click
        button = self.eventboard_buttons.get(button_id)
        if button:
            # Get button configuration to determine type and event details
            buttons = self.eventboard_config.get('buttons', [])
            button_config = next((b for b in buttons if b.get('id') == button_id), None)
            
            if button_config:
                event_name = button_config.get('event_name', 'UNKNOWN_EVENT')
                button_text = button_config.get('text', 'Button')
                button_type = button_config.get('type', 'instantaneous')
                
                # Trigger the button click
                self.on_eventboard_button_click(event_name, button_text, button_type, button_id)
        
        # Clear the field and reset to placeholder
        entry.delete(0, tk.END)
        entry.insert(0, "0s")
        entry.config(fg='lightgray')  # Reset to placeholder color
        
        # Release focus from the entry field
        entry.master.focus_set()  # Focus the container frame instead
    
    def on_eventboard_button_click(self, event_name, button_text, button_type, button_id):
        """Handle EventBoard button click"""
        try:
            # Get time offset
            time_offset_str = self.eventboard_time_offsets.get(button_id, tk.StringVar()).get()
            time_offset_seconds = self.parse_time_offset(time_offset_str)
            
            # Calculate actual timestamp (current time - offset)
            actual_timestamp = datetime.now() - timedelta(seconds=time_offset_seconds)
            
            if button_type == 'toggleable':
                # Toggle the state
                current_state = self.eventboard_toggle_states.get(button_id, False)
                new_state = not current_state
                self.eventboard_toggle_states[button_id] = new_state
                
                # Update button appearance with enhanced visual feedback
                button = self.eventboard_buttons[button_id]
                original_color = self.eventboard_original_colors.get(button_id, "#2196F3")  # Default fallback
                
                if new_state:
                    # ON state - red border with original background
                    button.config(
                        text=f"🔴 {button_text}",
                        font=("Arial", 10, "bold"),  # Slightly larger, bolder font
                        bg=original_color,  # Keep original background color
                        highlightthickness=3,  # Thicker highlight border
                        highlightbackground="#FF4444",  # Red border
                        highlightcolor="#FF4444"  # Red border when focused
                    )
                    # Update container frame to show pressed state with red border
                    button.master.config(
                        relief="sunken",
                        bd=3,  # Thicker border for active state
                        bg=original_color,  # Keep original background
                        highlightthickness=3,  # Thicker highlight border
                        highlightbackground="#FF4444",  # Red border
                        highlightcolor="#FF4444"  # Red border when focused
                    )
                    event_suffix = "_START"
                else:
                    # OFF state - normal appearance
                    button.config(
                        text=f"🔘 {button_text}",
                        font=("Arial", 9, "bold"),  # Normal font size
                        bg=original_color,  # Original button color
                        highlightthickness=0,  # No highlight border
                        highlightbackground=original_color,
                        highlightcolor=original_color
                    )
                    # Update container frame to show normal state
                    button.master.config(
                        relief="raised",
                        bd=2,  # Normal border thickness
                        bg=original_color,  # Original color
                        highlightthickness=0,  # No highlight border
                        highlightbackground=original_color,
                        highlightcolor=original_color
                    )
                    event_suffix = "_END"
                
                # Update time offset entry field to match active state
                time_offset_entry = None
                for widget in button.master.winfo_children():
                    if isinstance(widget, tk.Entry):
                        time_offset_entry = widget
                        break
                
                if time_offset_entry:
                    if new_state:
                        # Active state - red border with original background
                        time_offset_entry.config(
                            bg=original_color,  # Keep original background
                            fg="white",
                            highlightthickness=2,  # Thicker highlight border
                            highlightbackground="#FF4444",  # Red border
                            highlightcolor="#FF4444"  # Red border when focused
                        )
                    else:
                        # Normal state - match original button color
                        time_offset_entry.config(
                            bg=original_color, 
                            fg="white",
                            highlightthickness=0,  # No highlight border
                            highlightbackground=original_color,
                            highlightcolor=original_color
                        )
                
                # Send LSL event with toggle state
                self.send_eventboard_message(f"{event_name}{event_suffix}", button_text, actual_timestamp, new_state)
                
                # Update log display
                log_message = f"EventBoard: {button_text} {'ON' if new_state else 'OFF'} ({event_name}{event_suffix})"
                if time_offset_seconds > 0:
                    log_message += f" [offset: -{time_offset_str}]"
                
            else:
                # Instantaneous event
                self.send_eventboard_message(event_name, button_text, actual_timestamp, None)
                
                # Update log display
                log_message = f"EventBoard: {button_text} ({event_name})"
                if time_offset_seconds > 0:
                    log_message += f" [offset: -{time_offset_str}]"
            
            # Update log display
            timestamp = actual_timestamp.strftime("%Y-%m-%d %H:%M:%S")
            self.update_log_display(log_message, timestamp)
            
            print(f"EventBoard button clicked: {button_text} -> {event_name} (type: {button_type})")
            
        except Exception as e:
            print(f"Error handling EventBoard button click: {e}")
            messagebox.showerror("EventBoard Error", f"Failed to send event: {str(e)}")
    
    def send_eventboard_message(self, event_name, button_text, timestamp=None, toggle_state=None):
        """Send EventBoard message via LSL"""
        if self.eventboard_outlet:
            try:
                # Use provided timestamp or current time
                if timestamp is None:
                    timestamp = datetime.now()
                
                # Create event message with timestamp, button info, and toggle state
                event_message = f"{event_name}|{button_text}|{timestamp.isoformat()}"
                
                # Add toggle state if provided
                if toggle_state is not None:
                    event_message += f"|TOGGLE:{toggle_state}"
                
                self.eventboard_outlet.push_sample([event_message])
                print(f"EventBoard LSL message sent: {event_message}")
            except Exception as e:
                print(f"Error sending EventBoard LSL message: {e}")
                raise
        else:
            print("EventBoard LSL outlet not available")
            raise Exception("EventBoard LSL outlet not available")

    def user_select_xdf_folder_if_needed(self) -> Path:
        """Ensures the self.xdf_folder is valid, otherwise forces the user to select a valid one. returns the valid folder.
        """
        default_xdf_folder = get_default_xdf_folder()
        print(f'user_select_xdf_folder_if_needed(): self.xdf_folder: "{self.xdf_folder}", type: {type(self.xdf_folder)}\n\tself.xdf_folder.exists(): {self.xdf_folder.exists()}\n\tdefault_xdf_folder.is_dir(): {default_xdf_folder.is_dir()}')
        if (self.xdf_folder is not None) and isinstance(self.xdf_folder, str):
            self.xdf_folder = Path(self.xdf_folder).resolve()
        print(f'(self.xdf_folder is not None) and (self.xdf_folder.exists()) and (self.xdf_folder.is_dir()): {(self.xdf_folder is not None) and (self.xdf_folder.exists()) and (self.xdf_folder.is_dir())}')
        if (self.xdf_folder is not None) and (self.xdf_folder.exists()) and (self.xdf_folder.is_dir()):
            ## already had valid folder, just return it
            return self.xdf_folder
        else:
            ## try to get the default first
            if (default_xdf_folder is not None) and (default_xdf_folder.exists()) and (default_xdf_folder.is_dir()):
                self.xdf_folder = default_xdf_folder
            else:
                ## prompt user with GUI:
                print(f'default_xdf_folder: "{default_xdf_folder.as_posix()}"')
                self.xdf_folder = Path(filedialog.askdirectory(initialdir=str(self.xdf_folder), title="Select output XDF Folder - PhoLogToLabStreamingLayer_logs")).resolve()

            assert self.xdf_folder.exists(), f"XDF folder does not exist: {self.xdf_folder}"
            assert self.xdf_folder.is_dir(), f"XDF folder is not a directory: {self.xdf_folder}"
            self.update_log_display(f"XDF folder selected: {self.xdf_folder}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            print(f"XDF folder selected: {self.xdf_folder}")
            return self.xdf_folder


    # ---------------------------------------------------------------------------- #
    #                               Recording Methods                              #
    # ---------------------------------------------------------------------------- #
    def _common_capture_recording_start_timestamps(self):
        """Common code for capturing recording start timestamps"""
        # self.recording_start_datetime = datetime.now()
        # self.recording_start_datetime = datetime.now(datetime.timezone.utc)
        # self.recording_start_lsl_local_offset = pylsl.local_clock()        
        self.capture_recording_start_timestamps()
        return (self.recording_start_datetime, self.recording_start_lsl_local_offset)


    def _common_initiate_recording(self, allow_prompt_user_for_filename: bool = False):
        """Common code for initiating recording
        called by `self.start_recording()` and `self.auto_start_recording()`, and also by `self.split_recording()`

        Usage:

            new_filename, (new_recording_start_datetime, new_recording_start_lsl_local_offset) = self._common_initiate_recording(allow_prompt_user_for_filename=True)

        """
        ## Capture recording timestamps:
        self._common_capture_recording_start_timestamps()
    
        # Create default filename with timestamp
        current_timestamp = self.recording_start_datetime.strftime("%Y%m%d_%H%M%S")
        default_filename = f"{current_timestamp}_log.xdf"
        
        # Ensure the default directory exists
        self.xdf_folder = self.user_select_xdf_folder_if_needed()
        self.xdf_folder.mkdir(parents=True, exist_ok=True)
        assert self.xdf_folder.exists(), f"XDF folder does not exist: {self.xdf_folder}"
        assert self.xdf_folder.is_dir(), f"XDF folder is not a directory: {self.xdf_folder}"

        # Set new filename directly
        if allow_prompt_user_for_filename:
            filename = filedialog.asksaveasfilename(initialdir=str(self.xdf_folder), initialfile=default_filename, defaultextension=".xdf", filetypes=[("XDF files", "*.xdf"), ("All files", "*.*")], title="Save XDF Recording As")
        else:
            filename = str(self.xdf_folder / default_filename)

        if not filename:
            return
        
        self.recording = True
        self.recorded_data = [] ## clear recorded data
        
        self.xdf_filename = filename
        
        # Create backup file for crash recovery
        self.backup_filename = str(Path(filename).with_suffix('.backup.json'))
        return self.xdf_filename, (self.recording_start_datetime, self.recording_start_lsl_local_offset)




    def start_recording(self):
        """Start XDF recording using LabRecorder or fallback to legacy method"""
        # Check if we have streams to record
        if self.is_lab_recorder_available() and not self.is_lab_recorder_service_available():
            selected_streams = self.get_selected_streams()
            if not selected_streams:
                messagebox.showerror("Error", "No LSL streams selected for recording")
                return
        elif not self.has_any_inlets:
            messagebox.showerror("Error", "No LSL inlet available for recording")
            return
        
        # Create default filename with timestamp
        new_filename, (new_recording_start_datetime, new_recording_start_lsl_local_offset) = self._common_initiate_recording(allow_prompt_user_for_filename=False)

        # Update GUI
        try:
            if not self._shutting_down:
                self.recording_status_label.config(text="Recording...", foreground="green")
                self.start_recording_button.config(state="disabled")
                self.stop_recording_button.config(state="normal")
                self.split_recording_button.config(state="normal")  # Enable split button
                self.status_info_label.config(text=f"Recording to: {os.path.basename(new_filename)}")
        except tk.TclError:
            pass  # GUI is being destroyed
        
        # Start recording thread
        self.recording_thread = threading.Thread(target=self.recording_worker, daemon=True)
        self.recording_thread.start()
        
        # Start taskbar overlay flashing
        self.start_taskbar_overlay_flash()
        
        recording_method = "LabRecorder" if self.is_lab_recorder_available() else "Legacy"
        self.update_log_display(f"XDF Recording started ({recording_method})", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


    def _try_auto_start_after_stream_discovery(self):
        """Try to auto-start recording after streams are discovered (called from stream discovery)"""
        if self.auto_start_attempted:
            return  # Already tried
        
        self.auto_start_attempted = True
        
        # Auto-select own streams for recording
        if self.is_lab_recorder_available():
            self.select_all_streams()
            selected_streams = self.get_selected_streams()
            if selected_streams:
                # Streams are available and selected, try to start
                self.auto_start_recording()
            else:
                print("Cannot auto-start recording: no own streams found to select")
        elif self.has_any_inlets:
            # Legacy mode with inlets
            self.auto_start_recording()
        else:
            print("Cannot auto-start recording: no streams or inlets available")


    def auto_start_recording(self):
        """Automatically start recording on app launch if streams are available"""
        # Check if we have streams to record
        if self.is_lab_recorder_available():
            # Auto-select own streams for recording (in case not already selected)
            self.select_all_streams()
            selected_streams = self.get_selected_streams()
            if not selected_streams:
                print("Cannot auto-start recording: no streams selected")
                return
        elif not self.has_any_inlets:
            print("Cannot auto-start recording: no inlet available")
            return
        
        try:
            # Create default filename with timestamp
            new_filename, (new_recording_start_datetime, new_recording_start_lsl_local_offset) = self._common_initiate_recording(allow_prompt_user_for_filename=False)
            
            # Update GUI
            try:
                if not self._shutting_down:
                    self.recording_status_label.config(text="Recording...", foreground="green")
                    self.start_recording_button.config(state="disabled")
                    self.stop_recording_button.config(state="normal")
                    self.split_recording_button.config(state="normal")  # Enable split button
                    self.status_info_label.config(text=f"Auto-recording to: {os.path.basename(self.xdf_filename)}")
            except tk.TclError:
                pass  # GUI is being destroyed
            
            # Start recording thread
            self.recording_thread = threading.Thread(target=self.recording_worker, daemon=True)
            self.recording_thread.start()
            
            # Start taskbar overlay flashing
            self.start_taskbar_overlay_flash()
            
            # Log the auto-start event both in GUI and via LSL
            self.update_log_display("XDF Recording auto-started", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            auto_start_message = f"RECORDING_AUTO_STARTED: {new_filename}"
            self.send_lsl_message(auto_start_message)  # Send via LSL
            print(f"Auto-started recording to: {self.xdf_filename}")

        except Exception as e:
            print(f"Error auto-starting recording: {e}")
            self.update_log_display(f"Auto-start failed: {str(e)}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


    def recording_worker(self):
        """Background thread for recording LSL data using LabRecorder with robust error handling"""
        if not self.is_lab_recorder_available():
            print("LabRecorder not available, falling back to legacy recording")
            self.legacy_recording_worker()
            return
        if self.is_lab_recorder_service_available():
            self.service_rcs_recording_worker()
            return
        try:
            # Configure LabRecorder with selected streams
            selected_stream_infos = self.get_selected_streams()
            if not selected_stream_infos:
                print("No streams selected for recording")
                # Notify user via GUI
                if not self._shutting_down:
                    self.root.after(0, lambda: self.update_log_display(
                        "Recording failed: No streams selected", 
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    ))
                return
            
            # Start LabRecorder recording with retry mechanism
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    self.lab_recorder.start_recording(
                        filename=self.xdf_filename,
                        streams=selected_stream_infos
                    )
                    print(f"LabRecorder started recording to: {self.xdf_filename}")
                    break
                except Exception as e:
                    print(f"LabRecorder start attempt {attempt + 1} failed: {e}")
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(1.0)  # Wait before retry
            
            # Monitor recording while active
            consecutive_errors = 0
            max_consecutive_errors = 10
            
            while self.recording and not self._shutting_down:
                try:
                    # Check if LabRecorder is still recording
                    if hasattr(self.lab_recorder, 'is_recording') and not self.lab_recorder.is_recording:
                        print("LabRecorder stopped recording unexpectedly")
                        # Notify user via GUI
                        if not self._shutting_down:
                            self.root.after(0, lambda: self.update_log_display(
                                "Warning: LabRecorder stopped unexpectedly", 
                                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            ))
                        break
                    
                    # Reset error counter on successful check
                    consecutive_errors = 0
                    
                    # Sleep briefly to avoid busy waiting
                    time.sleep(0.1)
                    
                except Exception as e:
                    consecutive_errors += 1
                    print(f"Error monitoring LabRecorder (attempt {consecutive_errors}): {e}")
                    
                    if consecutive_errors >= max_consecutive_errors:
                        print("Too many consecutive monitoring errors. Stopping recording.")
                        break
                    
                    time.sleep(0.5)  # Wait longer on error
            
            # Stop LabRecorder when done
            try:
                if hasattr(self.lab_recorder, 'stop_recording'):
                    self.lab_recorder.stop_recording()
                    print("LabRecorder recording stopped")
            except Exception as e:
                print(f"Error stopping LabRecorder: {e}")
                
        except Exception as e:
            print(f"Critical error in LabRecorder recording: {e}")
            # Notify user via GUI
            if not self._shutting_down:
                self.root.after(0, lambda: self.update_log_display(
                    f"LabRecorder error: {str(e)[:100]}... Falling back to legacy recording", 
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ))
            # Fall back to legacy recording
            self.legacy_recording_worker()


    def service_rcs_recording_worker(self):
        """Record via external LabRecorderService (C++ engine) using RCS."""
        try:
            self._service_rcs_start_recording()
            print(f"LabRecorderService recording started: {self.xdf_filename}")
            while self.recording and not self._shutting_down:
                time.sleep(0.2)
        except Exception as e:
            print(f"LabRecorderService recording error: {e}")
            if not self._shutting_down:
                self.root.after(0, lambda: self.update_log_display(f"LabRecorderService error: {str(e)[:100]}", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        finally:
            try:
                self._service_rcs_stop_recording()
            except Exception as e:
                print(f"Error stopping LabRecorderService recording: {e}")
    
    def legacy_recording_worker(self):
        """Legacy background thread for recording LSL data with incremental backup"""
        sample_count = 0
        
        while self.recording and self.has_any_inlets and not self._shutting_down:
            # Check shutdown and inlets before accessing
            if self._shutting_down or self.inlets is None:
                break
            
            ## loop through streams
            should_save_backup: bool = False
            for a_stream_name, an_inlet in self.inlets.items():
                try:
                    sample, timestamp = an_inlet.pull_sample(timeout=1.0)
                    if sample:
                        data_point = {
                            'sample': sample,
                            'timestamp': timestamp,
                            'stream_name': a_stream_name,
                        }
                        self.recorded_data.append(data_point)
                        sample_count += 1
                        
                        # Auto-save every 10 samples to backup file
                        if sample_count % 10 == 0:
                            should_save_backup = True
                                    
                except Exception as e:
                    print(f"Error in legacy recording worker: {e}")
                    break

            ## END for a_stream_name, a_setup_fn in stream_setup_fn_dict...
            if should_save_backup:
                self.save_backup()


    def stop_recording(self):
        """Stop XDF recording and save file"""
        if not self.recording:
            return
        
        self.recording = False

        # Stop taskbar overlay flashing
        self.stop_taskbar_overlay_flash()

        # Log the stop event via LSL before saving
        stop_message = f"RECORDING_STOPPED: {os.path.basename(self.xdf_filename)}"
        self.send_lsl_message(stop_message)
   
        # Wait for recording thread to finish
        if self.recording_thread and self.recording_thread.is_alive():
            self.recording_thread.join(timeout=2.0)
        
        # Handle file saving based on recording method
        if self.is_lab_recorder_service_available() or self.is_lab_recorder_available():
            print(f"LabRecorder XDF file saved: {self.xdf_filename}")
        else:
            # Legacy method - save XDF file using MNE
            self.save_xdf_file()
            
            # Clean up backup file
            try:
                if hasattr(self, 'backup_filename') and os.path.exists(self.backup_filename):
                    os.remove(self.backup_filename)
            except Exception as e:
                print(f"Error removing backup file: {e}")
        
        # Update GUI
        try:
            if not self._shutting_down:
                self.recording_status_label.config(text="Not Recording", foreground="red")
                self.start_recording_button.config(state="normal")
                self.stop_recording_button.config(state="disabled")
                self.split_recording_button.config(state="disabled")  # Disable split button
                self.status_info_label.config(text="Ready")
        except tk.TclError:
            pass  # GUI is being destroyed
        
        recording_method = "LabRecorder" if self.is_lab_recorder_available() else "Legacy"
        self.update_log_display(f"XDF Recording stopped and saved ({recording_method})", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


    def split_recording(self):
        """Split recording into a new file - stop current and start new"""
        if not self.recording:
            return
        
        try:
            # Stop current recording (this will save the current data)
            self.stop_recording()
            
            # Wait a moment for the stop to complete
            self.root.after(100, self.start_new_split_recording)
            
        except Exception as e:
            print(f"Error splitting recording: {e}")
            self.update_log_display(f"Split recording failed: {str(e)}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


    def start_new_split_recording(self):
        """Start new recording after split"""
        if self.is_lab_recorder_available() and not self.is_lab_recorder_service_available():
            selected_streams = self.get_selected_streams()
            if not selected_streams:
                print("Cannot split recording: no LSL streams selected")
                return
        elif not self.is_lab_recorder_available() and not self.has_any_inlets:
            print("Cannot split recording: no inlet available")
            return
        
        try:
            # Create new filename with timestamp
            # Set new filename directly
            new_filename, (new_recording_start_datetime, new_recording_start_lsl_local_offset) = self._common_initiate_recording(allow_prompt_user_for_filename=False)
            
            # Update GUI
            try:
                if not self._shutting_down:
                    self.recording_status_label.config(text="Recording...", foreground="green")
                    self.start_recording_button.config(state="disabled")
                    self.stop_recording_button.config(state="normal")
                    self.split_recording_button.config(state="normal")
                    self.status_info_label.config(text=f"Split to: {os.path.basename(self.xdf_filename)}")
            except tk.TclError:
                pass  # GUI is being destroyed
            
            # Start recording thread
            self.recording_thread = threading.Thread(target=self.recording_worker, daemon=True)
            self.recording_thread.start()
            
            self.update_log_display(f"Recording split to new file: {new_filename}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            print(f"Split recording to new file: {self.xdf_filename}")
            
            # Log the split event both in GUI and via LSL
            split_message = f"RECORDING_SPLIT_NEW_FILE: {new_filename}"
            self.send_lsl_message(split_message)  # Send via LSL
            self.update_log_display(f"Recording split to new file: {new_filename}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            print(f"Split recording to new file: {self.xdf_filename}")
            
        except Exception as e:
            print(f"Error starting new split recording: {e}")
            self.update_log_display(f"Split restart failed: {str(e)}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


    # ---------------------------------------------------------------------------- #
    #                             Backups and Recovery                             #
    # ---------------------------------------------------------------------------- #
    def save_backup(self):
        """Save current data to backup file"""
        try:
            backup_data = {
                'recorded_data': self.recorded_data,
                'recording_start_time': self.recording_start_lsl_local_offset,
                'sample_count': len(self.recorded_data)
            }
            
            with open(self.backup_filename, 'w') as f:
                json.dump(backup_data, f, default=str)
                
        except Exception as e:
            print(f"Error saving backup: {e}")

    def check_for_recovery(self):
        """Check for backup files and offer recovery on startup"""
        self.xdf_folder = self.user_select_xdf_folder_if_needed()
        backup_files = list(self.xdf_folder.glob('*.backup.json'))
        
        if backup_files:
            response = messagebox.askyesno(
                "Recovery Available", 
                f"Found {len(backup_files)} backup file(s) from previous sessions. "
                "Would you like to recover them?"
            )
            
            if response:
                for backup_file in backup_files:
                    self.recover_from_backup(backup_file)

    def recover_from_backup(self, backup_file):
        """Recover data from backup file"""
        try:
            self.xdf_folder = self.user_select_xdf_folder_if_needed()
            with open(backup_file, 'r') as f:
                backup_data = json.load(f)
            
            # Ask user for recovery filename
            original_name = backup_file.stem.replace('.backup', '')
            recovery_filename = filedialog.asksaveasfilename(
                initialdir=str(self.xdf_folder),
                initialfile=f"{original_name}_recovered.xdf",
                defaultextension=".xdf",
                filetypes=[("XDF files", "*.xdf"), ("All files", "*.*")],
                title="Save Recovered XDF As"
            )
            
            if recovery_filename:
                # Restore data
                self.recorded_data = backup_data['recorded_data']
                self.xdf_filename = recovery_filename
                
                # Save as XDF
                self.save_xdf_file()
                
                # Remove backup file
                os.remove(backup_file)
                
                messagebox.showinfo("Recovery Complete", 
                    f"Recovered {len(self.recorded_data)} samples to {recovery_filename}")
                
        except Exception as e:
            messagebox.showerror("Recovery Error", f"Failed to recover from backup: {str(e)}")

    # ---------------------------------------------------------------------------- #
    #                              Save/Write Methods                              #
    # ---------------------------------------------------------------------------- #
    def save_xdf_file(self):
        """Save recorded data using MNE
        
        Seems highly incorrect but does load and display kinda reasonably in MNELAB
        
        Also calls `self.save_events_csv(...)` to export CSVs, and this DOES work and outputs the correct timestamps as of 2025-10-18.

        """
        if not self.recorded_data:
            messagebox.showwarning("Warning", "No data to save")
            return
        
        try:
            # Extract messages and timestamps
            messages = []
            timestamps = []
            
            recording_start_datetime = deepcopy(self.recording_start_datetime)
            recording_start_lsl_local_offset = deepcopy(self.recording_start_lsl_local_offset)


            for data_point in self.recorded_data:
                message = data_point['sample'][0] if data_point['sample'] else ''
                timestamp = data_point['timestamp']
                messages.append(message)
                timestamps.append(timestamp)
            
            # Convert timestamps to relative times (from first sample)
            if timestamps:
                first_timestamp_offset = recording_start_lsl_local_offset # a seocnds offset
                # first_timestamp_offset = timestamps[0] # a seocnds offset
                relative_ts_offset_sec = [ts - first_timestamp_offset for ts in timestamps]
            else:
                relative_ts_offset_sec = []
            
            # Create annotations (MNE's way of handling markers/events)
            # Set orig_time=None to avoid timing conflicts
            annotations = mne.Annotations(
                onset=relative_ts_offset_sec,
                duration=[0.0] * len(relative_ts_offset_sec),  # Instantaneous events
                description=messages,
                orig_time=None  # This fixes the timing conflict
            )
            
            # Create a minimal info structure for the markers
            info = mne.create_info(
                ch_names=['TextLogger_Markers'],
                sfreq=1000, # Dummy sampling rate for the minimal channel, `pylsl.IRREGULAR_RATE` does not work (Error: "Failed to save file: sfreq must be positive")
                ch_types=['misc']
            )
            
            # Create raw object with minimal dummy data
            # We need at least some data points to create a valid Raw object
            if len(timestamps) > 0:
                # Create dummy data spanning the recording duration
                duration = relative_ts_offset_sec[-1] if relative_ts_offset_sec else 1.0 # the last timestamp in seconds (recording length) or arbitrarily 1.0 if no samples
                n_samples = int(duration * 1000) + 1000  # Add buffer
                dummy_data = np.zeros((1, n_samples))
            else:
                dummy_data = np.zeros((1, 1000))  # Minimum 1 second of data
            
            raw = mne.io.RawArray(dummy_data, info)
            
            # Set measurement date to match the first timestamp
            if timestamps:
                # raw.set_meas_date(timestamps[0])
                # raw.set_meas_date(recording_start_datetime.astimezone(pytz.timezone("UTC"))) ## this seems better?!, but probably should be None
                raw.set_meas_date(recording_start_datetime.astimezone(pytz.timezone("UTC")).strftime('%Y-%m-%d %H:%M:%S.%f')) ## this seems better?!, but probably should be None
                # raw.set_meas_date(None)
            
            raw.set_annotations(annotations)
            
            # Add metadata to the raw object
            raw.info['description'] = 'TextLogger LSL Stream Recording'
            raw.info['experimenter'] = 'PhoLogToLabStreamingLayer'

            # Determine output filename and format
            if self.xdf_filename.endswith('.xdf'):
                # Save as FIF (MNE's native format)
                fif_filename = self.xdf_filename.replace('.xdf', '.fif')
                raw.save(fif_filename, overwrite=True)
                actual_filename = fif_filename
                file_type = "FIF"
            else:
                # Use the original filename
                raw.save(self.xdf_filename, overwrite=True)
                actual_filename = self.xdf_filename
                file_type = "FIF"
            
            # Also save a CSV for easy reading

            
            if isinstance(actual_filename, str):
                actual_filename = Path(actual_filename).resolve()

            _default_CSV_folder = actual_filename.parent.joinpath('CSV')
            _default_CSV_folder.mkdir(parents=True, exist_ok=True)
            print(f'_default_CSV_folder: "{_default_CSV_folder}"')

            csv_filename: str = actual_filename.name.replace('.fif', '_events.csv')
            csv_filepath: Path = _default_CSV_folder.joinpath(csv_filename).resolve()

            self.save_events_csv(csv_filepath, messages, timestamps, recording_start_datetime=recording_start_datetime, recording_start_lsl_local_offset=recording_start_lsl_local_offset)
            
            _status_str: str = (f"{file_type} file saved: '{actual_filename}'\n"
                f"Events CSV saved: '{csv_filepath}'\n"
                f"Recorded {len(self.recorded_data)} samples")
            self.update_log_display(_status_str, timestamp=None)
            # messagebox.showinfo("Success", _status_str)
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save file: {str(e)}")
            print(f"Detailed error: {e}")
            import traceback
            traceback.print_exc()


    def save_events_csv(self, csv_filename, messages, timestamps, recording_start_datetime: datetime, recording_start_lsl_local_offset: float):
        """Save events as CSV for easy reading"""
        try:
            import csv
            
            with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['Timestamp', 'LSL_Time', 'LSL_Time_Offset', 'Message'])

                initial_lsl_time = recording_start_lsl_local_offset # timestamps[0]
                
                for i, (message, lsl_time) in enumerate(zip(messages, timestamps)):
                    ## compute relative LSL offset:
                    relative_lsl_time_sec: float = lsl_time - initial_lsl_time
                    assert (relative_lsl_time_sec >= 0), f"Relative LSL time is negative: {relative_lsl_time_sec}"

                    # Convert LSL timestamp to readable datetime
                    readable_datetime: datetime = (recording_start_datetime + timedelta(seconds=relative_lsl_time_sec)).astimezone(pytz.timezone("US/Eastern")) ## using timedelta(seconds=lsl_time) was clearly wrong (off by several days)
                    readable_datetime_str: str = readable_datetime.strftime("%Y-%m-%d %I:%M:%S.%f %p") ## 12H AM/PM format
                    # readable_datetime_str = readable_datetime.strftime('%Y-%m-%d %H:%M:%S.%f') ## 24H format
                    # readable_time = datetime.fromtimestamp(lsl_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] ## Sadly this writes datetimes like "1/3/1970  11:35:25 AM", which are completely wrong (both date and time components not even close)
                    writer.writerow([readable_datetime_str, lsl_time, relative_lsl_time_sec, message])
                    
        except Exception as e:
            print(f"Error saving CSV: {e}")


    def log_message(self):
        """Handle log button click"""
        message = self.text_entry.get().strip()
        
        if not message:
            messagebox.showwarning("Warning", "Please enter a message to log.")
            return
        
        # Use the timestamp when user first started typing
        timestamp = self.get_main_text_timestamp().strftime("%Y-%m-%d %H:%M:%S") # I'm guessing this is wrong too
        
        # Send LSL message
        self.send_lsl_message(message)
        
        # Update display
        self.update_log_display(message, timestamp)
        
        # Clear text entry
        self.text_entry.delete(0, tk.END)
        self.text_entry.focus()
    

    def send_lsl_message(self, message):
        """Send message via LSL"""
        if self.outlet_TextLogger:
            try:
                # Send message with timestamp
                self.outlet_TextLogger.push_sample([message])
                print(f"LSL message sent: {message}")
            except Exception as e:
                print(f"Error sending LSL message: {e}")
                messagebox.showerror("LSL Error", f"Failed to send LSL message: {str(e)}")
        else:
            print("LSL outlet not available")
    

    def update_log_display(self, message, timestamp=None):
        """Update the log display area"""
        # Don't update GUI if app is shutting down
        if self._shutting_down:
            return
            
        if timestamp is None:
            ## get now as the timestamp
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            log_entry = f"[{timestamp}] {message}\n"
            self.log_display.insert(tk.END, log_entry)
            self.log_display.see(tk.END)  # Auto-scroll to bottom
        except tk.TclError:
            # GUI is being destroyed, ignore the error
            pass
    
    def clear_log_display(self):
        """Clear the log display area"""
        self.log_display.delete(1.0, tk.END)
    
    # ---------------------------------------------------------------------------- #
    #                          Lab-Recorder Integration                            #
    # ---------------------------------------------------------------------------- #
    
    def init_lab_recorder(self):
        """Initialize lab-recorder for XDF recording (Windows service RCS or in-process fallback)."""
        try:
            self.lab_recorder_service_client = None
            try:
                from labrecorder.service.client import get_service_rcs_client, is_service_available
                if is_service_available():
                    self.lab_recorder_service_client = get_service_rcs_client()
                    print("Using LabRecorderService via RCS")
                    return True
            except ImportError:
                pass
            if self.lab_recorder is None:
                self.lab_recorder = LabRecorder(enable_remote_control=False)
                print("LabRecorder initialized successfully")
            return True
        except Exception as e:
            print(f"Error initializing LabRecorder: {e}")
            return False


    def is_lab_recorder_service_available(self) -> bool:
        return self.lab_recorder_service_client is not None


    def _build_rcs_filename_command(self, xdf_path: str) -> str:
        p = Path(xdf_path)
        root = p.parent.as_posix()
        template = p.name
        return f"filename {{root:{root}}}{{template:{template}}}"


    def _service_rcs_start_recording(self) -> None:
        client = self.lab_recorder_service_client
        if client is None:
            raise RuntimeError("LabRecorderService RCS client not available")
        client.send_command("update")
        time.sleep(2.0)
        client.send_command("select all")
        client.send_command(self._build_rcs_filename_command(self.xdf_filename))
        client.send_command("start")
        path = client.get_recording_path()
        if path is not None:
            self.xdf_filename = str(path)


    def _service_rcs_stop_recording(self) -> None:
        client = self.lab_recorder_service_client
        if client is not None:
            client.request_stop()
    

    def cleanup_lab_recorder(self):
        """Clean up lab-recorder resources"""
        try:
            if self.is_lab_recorder_service_available() and self.recording:
                self._service_rcs_stop_recording()
            if self.lab_recorder_service_client is not None:
                self.lab_recorder_service_client = None
                print("LabRecorderService cleaned up successfully")
            if self.lab_recorder is not None:
                # Stop any active recording
                if hasattr(self.lab_recorder, 'is_recording') and self.lab_recorder.is_recording:
                    self.lab_recorder.stop_recording()
                self.lab_recorder = None
                print("LabRecorder cleaned up successfully")
        except Exception as e:
            print(f"Error cleaning up LabRecorder: {e}")
    

    def is_lab_recorder_available(self) -> bool:
        """Check if lab-recorder (service RCS or in-process) is available."""
        return self.is_lab_recorder_service_available() or self.lab_recorder is not None
    

    def start_stream_discovery(self):
        """Start continuous stream discovery in background thread"""
        if self.stream_discovery_active:
            return
        
        self.stream_discovery_active = True
        self.stream_monitor_thread = threading.Thread(target=self.stream_discovery_worker, daemon=True)
        self.stream_monitor_thread.start()
        print("Stream discovery started")
    

    def stop_stream_discovery(self):
        """Stop stream discovery"""
        self.stream_discovery_active = False
        if self.stream_monitor_thread and self.stream_monitor_thread.is_alive():
            self.stream_monitor_thread.join(timeout=2.0)
        print("Stream discovery stopped")
    

    def stream_discovery_worker(self):
        """Background worker for continuous stream discovery with robust error handling"""
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while self.stream_discovery_active and not self._shutting_down:
            try:
                # Discover all available LSL streams
                streams = pylsl.resolve_streams(wait_time=1.0)
                
                # Reset error counter on successful discovery
                consecutive_errors = 0
                
                # Update discovered streams dictionary
                new_discovered = {}
                for stream in streams:
                    try:
                        stream_key = f"{stream.name()}_{stream.source_id()}"
                        new_discovered[stream_key] = stream
                    except Exception as e:
                        print(f"Error processing stream {stream}: {e}")
                        continue
                
                # Check for changes and notify of disconnections
                # Use lock to safely compare and update discovered_streams
                new_streams = set()
                with self._stream_discovery_lock:
                    if self._shutting_down:
                        break
                    if new_discovered != self.discovered_streams:
                        # Detect disconnected streams
                        disconnected_streams = set(self.discovered_streams.keys()) - set(new_discovered.keys())
                        if disconnected_streams:
                            print(f"Streams disconnected: {disconnected_streams}")
                            # Remove disconnected streams from selection
                            for stream_key in disconnected_streams:
                                self.selected_streams.discard(stream_key)
                        
                        # Detect new streams
                        new_streams = set(new_discovered.keys()) - set(self.discovered_streams.keys())
                        if new_streams:
                            print(f"New streams discovered: {new_streams}")
                        
                        self.discovered_streams = new_discovered
                
                # Schedule GUI update on main thread (outside lock to avoid blocking)
                if not self._shutting_down:
                    self.root.after(0, self.update_stream_display)
                    
                    # Try to auto-start recording if we haven't already and streams are available
                    if not self.auto_start_attempted and new_streams:
                        # Auto-select own streams and try to start recording
                        self.root.after(500, self._try_auto_start_after_stream_discovery)
                
                # Wait before next discovery cycle
                time.sleep(2.0)
                
            except Exception as e:
                consecutive_errors += 1
                print(f"Error in stream discovery (attempt {consecutive_errors}): {e}")
                
                # If too many consecutive errors, increase wait time and potentially stop
                if consecutive_errors >= max_consecutive_errors:
                    print(f"Too many consecutive stream discovery errors ({consecutive_errors}). Stopping discovery.")
                    self.stream_discovery_active = False
                    # Notify user via GUI
                    if not self._shutting_down:
                        self.root.after(0, lambda: self.update_log_display(
                            "Stream discovery stopped due to repeated errors", 
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        ))
                    break
                
                # Exponential backoff for errors
                wait_time = min(30.0, 2.0 ** consecutive_errors)
                time.sleep(wait_time)
    

    def update_stream_display(self):
        """Update the GUI stream display"""
        if self._shutting_down:
            return
        
        # Update the tree view display
        if hasattr(self, 'stream_tree'):
            self.update_stream_tree_display()
        
        # Also print to console for debugging
        # Removed repeated "Discovered X streams" console logs
        # with self._stream_discovery_lock:
        #     if self.discovered_streams:
        #         print(f"Discovered {len(self.discovered_streams)} streams:")
        #         for stream_key, stream in self.discovered_streams.items():
        #             print(f"  - {stream.name()} ({stream.type()}) - {stream.channel_count()} channels @ {stream.nominal_srate()}Hz")
    

    def get_discovered_streams(self) -> Dict[str, pylsl.StreamInfo]:
        """Get currently discovered streams"""
        with self._stream_discovery_lock:
            return self.discovered_streams.copy()
    

    def select_stream(self, stream_key: str, selected: bool = True):
        """Select or deselect a stream for recording"""
        with self._stream_discovery_lock:
            if selected:
                self.selected_streams.add(stream_key)
            else:
                self.selected_streams.discard(stream_key)
        print(f"Stream {stream_key} {'selected' if selected else 'deselected'}")
    

    def get_selected_streams(self) -> List[pylsl.StreamInfo]:
        """Get list of selected stream info objects"""
        selected = []
        with self._stream_discovery_lock:
            for stream_key in self.selected_streams:
                try:
                    if stream_key in self.discovered_streams:
                        selected.append(self.discovered_streams[stream_key])
                except KeyError:
                    # Stream was removed between iteration and access, skip it
                    continue
        return selected
    

    def auto_select_own_streams(self):
        """Automatically select the application's own streams"""
        with self._stream_discovery_lock:
            for stream_key, stream in self.discovered_streams.items():
                stream_name = stream.name()
                if stream_name in self.stream_names:  # TextLogger, EventBoard, WhisperLiveLogger
                    self.selected_streams.add(stream_key)
        self.update_stream_tree_display()
    

    def on_stream_tree_click(self, event):
        """Handle clicks on the stream tree"""
        item = self.stream_tree.identify('item', event.x, event.y)
        if item:
            # Find the stream_key for this item
            stream_key = None
            for key, tree_item in self.stream_tree_items.items():
                if tree_item == item:
                    stream_key = key
                    break
            
            if stream_key:
                # Toggle selection
                currently_selected = stream_key in self.selected_streams
                self.select_stream(stream_key, not currently_selected)
                self.update_stream_tree_display()
    

    def refresh_streams(self):
        """Manually refresh stream discovery with error handling"""
        try:
            # Update GUI to show refreshing status
            if hasattr(self, 'stream_info_label'):
                self.stream_info_label.config(text="Refreshing streams...")
            
            # Discover all available LSL streams
            streams = pylsl.resolve_streams(wait_time=2.0)
            
            # Update discovered streams dictionary
            new_discovered = {}
            for stream in streams:
                try:
                    stream_key = f"{stream.name()}_{stream.source_id()}"
                    new_discovered[stream_key] = stream
                except Exception as e:
                    print(f"Error processing stream during refresh: {e}")
                    continue
            
            with self._stream_discovery_lock:
                self.discovered_streams = new_discovered
            self.update_stream_tree_display()
            
            # Update status
            self.update_log_display(f"Stream refresh completed: {len(new_discovered)} streams found", 
                                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            
        except Exception as e:
            error_msg = f"Error refreshing streams: {e}"
            print(error_msg)
            self.update_log_display(error_msg, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            
            # Update GUI to show error status
            if hasattr(self, 'stream_info_label'):
                self.stream_info_label.config(text="Stream refresh failed")
    

    def select_all_streams(self):
        """Select all discovered streams"""
        with self._stream_discovery_lock:
            for stream_key in self.discovered_streams.keys():
                self.selected_streams.add(stream_key)
        self.update_stream_tree_display()
    

    def select_no_streams(self):
        """Deselect all streams"""
        with self._stream_discovery_lock:
            self.selected_streams.clear()
        self.update_stream_tree_display()
    

    def update_stream_tree_display(self):
        """Update the stream tree display with current streams"""
        if self._shutting_down:
            return
        
        # Clear existing items
        for item in self.stream_tree.get_children():
            self.stream_tree.delete(item)
        self.stream_tree_items.clear()

        # Add current streams (copy data while holding lock, then process outside lock)
        streams_snapshot = {}
        selected_snapshot = set()
        with self._stream_discovery_lock:
            streams_snapshot = self.discovered_streams.copy()
            selected_snapshot = self.selected_streams.copy()
        
        for stream_key, stream in streams_snapshot.items():
            try:
                # Determine selection status
                is_selected = stream_key in selected_snapshot
                checkbox_str_rep: str = "☑" if is_selected else "☐"
                
                # Get stream info
                name = stream.name()
                stream_type = stream.type()
                channels = str(stream.channel_count())
                rate = f"{stream.nominal_srate():.0f}Hz" if stream.nominal_srate() > 0 else "Irregular"
                status = "Connected"
                
                # Insert item into tree with name in column #0 and checkbox in Select column
                item_id = self.stream_tree.insert('', 'end', text=name, 
                                                values=(checkbox_str_rep, stream_type, channels, rate, status))
                self.stream_tree_items[stream_key] = item_id
                    
            except Exception as e:
                print(f"Error updating stream display for {stream_key}: {e}")
        
        # Update info label (use snapshot data)
        total_streams = len(streams_snapshot)
        selected_count = len(selected_snapshot)
        self.stream_info_label.config(text=f"Streams: {total_streams} discovered, {selected_count} selected")
    

    def on_closing(self):
        """Handle window closing"""
        # Set shutdown flag to prevent GUI updates
        self._shutting_down = True
        
        # Stop transcription if active
        if self.transcription_active:
            self.stop_live_transcription()

        # Stop recording if active
        if self.recording:
            self.stop_recording()
        
        # Stop stream discovery
        self.stop_stream_discovery()
        
        # Wait for threads to fully stop before cleaning up resources
        # Check if recording thread is still alive after stop_recording
        if hasattr(self, 'recording_thread') and self.recording_thread and self.recording_thread.is_alive():
            print("Warning: Recording thread did not stop cleanly within timeout")
        
        # Check if stream discovery thread is still alive after stop_stream_discovery
        if hasattr(self, 'stream_monitor_thread') and self.stream_monitor_thread and self.stream_monitor_thread.is_alive():
            print("Warning: Stream discovery thread did not stop cleanly within timeout")
        
        # Clean up global hotkey
        self.cleanup_GlobalHotkeyMixin()
        
        # Restore stdout/stderr streams from console output capture
        if hasattr(self, 'console_output_frame') and self.console_output_frame is not None:
            self.console_output_frame.restore_streams()
        
        # Clean up system tray
        if self.system_tray:
            threading.Thread(target=self.system_tray.stop, daemon=True).start()
        
        # Clean up lab-recorder
        self.cleanup_lab_recorder()
        
        # Stop taskbar overlay flashing
        self.stop_taskbar_overlay_flash()
        
        # Clean up LSL resources (only after threads have stopped or been confirmed stopped)
        if hasattr(self, 'outlets') and self.outlets:
            # for an_outlet_name, an_outlet in self.outlets:
            outlet_names: List[str] = list(self.outlets.keys())
            for an_outlet_name in outlet_names:
                self.outlets.pop(an_outlet_name)
            self.outlets = None
            del self.outlets

        if hasattr(self, 'inlets') and self.inlets is not None:
            a_names: List[str] = list(self.inlets.keys())
            for a_name in a_names:
                self.inlets.pop(a_name)
            self.inlets = None
            del self.inlets


        # Release singleton lock
        self.release_singleton_lock()
        
        self.root.destroy()



# def main():
#     # Check if another instance is already running
#     if LoggerApp.is_instance_running():
#         messagebox.showerror("Instance Already Running", 
#                            "Another instance of LSL Logger is already running.\n"
#                            "Only one instance can run at a time.")
#         sys.exit(1)
    
#     root = tk.Tk()
#     app = LoggerApp(root)
    
#     # Try to acquire the singleton lock
#     if not app.acquire_singleton_lock():
#         messagebox.showerror("Startup Error", 
#                            "Failed to acquire singleton lock.\n"
#                            "Another instance may be running.")
#         root.destroy()
#         sys.exit(1)
    
#     # # Handle window closing - minimize to tray instead of closing
#     # def on_closing():
#     #     app.minimize_to_tray()
#     # root.protocol("WM_DELETE_WINDOW", on_closing)
    
#     # Start the GUI
#     root.mainloop()


