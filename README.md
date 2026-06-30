# Enhanced LSL Logger App
a complete but minimal python app that allows the user to take timestampped notes to labstreaminglayer and have them simultaneously saved out to file (.xdf/.csv)

This enhanced version of the LSL Logger App includes system tray functionality and global hotkey support for quick log entries.

## New Features

### 1. System Tray Integration
- **Minimize to Tray**: Click the "Minimize to Tray" button on the Recording tab to hide the main window while the app keeps running
- **Close (X)**: Closes the application completely (stops recording, transcription, and tray)
- **Tray Icon**: The app continues running in the background with a system tray icon when minimized
- **Tray Menu**: Right-click the tray icon for options:
  - Show App: Restore the main window
  - Quick Log: Open the quick log entry popover
  - Exit: Completely close the application
- **Double-click**: Double-click the tray icon to restore the main window

### 2. Global Hotkey Support
- **Hotkey**: `Ctrl+Alt+L` can summon a quick log entry popover (disabled by default; set `GlobalHotkeyMixin.should_register_global_hotkey = True` on `LoggerApp` to enable)
- **Quick Log via tray**: Right-click the tray icon and choose **Quick Log** (always available)
- **Popover Features**:
  - Appears centered on the active monitor
  - Always on top
  - Auto-focused text entry
  - Press `Enter` to log and close
  - Press `Escape` to cancel
  - Clean, minimal interface

### 3. Enhanced Window Management
- **Minimize to Tray**: Use the **Minimize to Tray** button to run in the background; the window **X** button quits the app
- **Toggle Button**: The minimize button toggles between "Minimize to Tray" and "Restore from Tray"
- **Background Operation**: App continues recording and logging while minimized to tray

## Installation

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the App**:
   ```bash
   python logger_app.py
   ```

   Or use the batch file:
   ```bash
   install_and_run.bat
   ```

## Usage

### Basic Operation
1. Start the app - it will auto-start recording if LSL inlet is available
2. Use the main interface for detailed logging and recording management
3. Minimize to tray when you want the app to run in the background

### Quick Logging
1. Use **Quick Log** from the system tray menu (or enable the global hotkey; see above)
2. Type your log message
3. Press `Enter` to log and close, or `Escape` to cancel

### System Tray
1. Right-click the tray icon for options
2. Double-click to restore the main window
3. Use "Exit" to completely close the application

## Dependencies

- `pylsl`: Lab Streaming Layer support
- `pyxdf`: XDF file handling
- `numpy`: Numerical operations
- `mne`: MNE-Python for data processing
- `pystray`: System tray functionality
- `Pillow`: Image processing for tray icon
- `keyboard`: Global hotkey support
- `pyautogui`: Screen positioning
- `pywin32`: Windows API access

## Icons

Theme-aware icons are loaded from the `icons/` folder when present:

- **Dark theme** (default): `LogToLabStreamingLayerIcon.png`, `.ico`, `.icns`, or `.svg`
- **Light theme**: `LogToLabStreamingLayerIcon_Light.png`, `.ico`, or `.icns`

The repository ships with `LogToLabStreamingLayerIcon.svg` only. If PNG/ICO files are missing, the app and tray use a simple drawn **L** fallback icon.

The application detects system theme on Windows (registry) and uses a heuristic elsewhere.

## Notes

- Global hotkey `Ctrl+Alt+L` is **off by default**; enable via `should_register_global_hotkey` on `LoggerApp` if needed
- Quick Log is always available from the system tray menu
- The app continues recording LSL data while minimized to tray
- All existing functionality (XDF recording, LSL streaming) remains intact
- Closing the main window (X) exits the app; use **Minimize to Tray** to keep running in the background
- System tray icon shows a simple "L" design when PNG assets are not installed

## Troubleshooting

- **Hotkey not working**: Global hotkey is disabled by default; set `LoggerApp.should_register_global_hotkey = True` before tray setup, or use tray **Quick Log**. If enabled, ensure no other app uses `Ctrl+Alt+L`
- **Tray icon not visible**: Check if your system tray is hidden or collapsed
- **Permission errors**: Some features may require administrator privileges on Windows
- **Dependencies**: Ensure all packages are properly installed with `pip install -r requirements.txt`


# EventBoard Functionality - Enhanced

The Logger App now includes an enhanced EventBoard feature with support for both instantaneous and toggleable events, plus time offset capabilities.

## New Features

### 1. **Two Button Types**
- **Instantaneous Events**: Traditional one-click events (e.g., "Start Task", "Error")
- **Toggleable Events**: Can be toggled ON/OFF to indicate state changes (e.g., "Focus Mode", "Distracted")

### 2. **Time Offset Support**
- **Small dropdown** next to each button (20% of button width)
- **Retroactive logging**: Enter time offsets like "5s", "2m", "1h" to log events that occurred in the past
- **Default units**: Seconds if no unit specified
- **Unintrusive design**: Placeholder text "0s" that disappears when clicked

### 3. **Visual Indicators**
- **Toggleable buttons**: Show 🔘 when OFF, 🔴 when ON
- **Button relief**: Raised when OFF, sunken when ON
- **Color coding**: Maintained for easy identification

## Configuration

The EventBoard configuration now supports button types:

```json
{
  "eventboard_config": {
    "title": "Event Board",
    "buttons": [
      {
        "id": "button_1_1",
        "row": 1,
        "col": 1,
        "text": "Start Task",
        "event_name": "TASK_START",
        "color": "#4CAF50",
        "type": "instantaneous"
      },
      {
        "id": "button_2_1",
        "row": 2,
        "col": 1,
        "text": "Focus Mode",
        "event_name": "FOCUS_MODE",
        "color": "#607D8B",
        "type": "toggleable"
      }
    ]
  }
}
```

### Button Properties
- `type`: Either "instantaneous" or "toggleable"
- All other properties remain the same (id, row, col, text, event_name, color)

## Time Offset Usage

### Supported Formats
- `5s` - 5 seconds ago
- `2m` - 2 minutes ago  
- `1h` - 1 hour ago
- `30` - 30 seconds ago (default unit is seconds)
- `1.5m` - 1.5 minutes ago

### How to Use
1. Click in the time offset field next to any button
2. Type the desired offset (e.g., "5s")
3. Press **Enter** to log the event with the time offset, or click the button
4. The event will be timestamped as if it occurred that many seconds/minutes/hours ago

## LSL Event Format

### Instantaneous Events
```
EVENT_NAME|BUTTON_TEXT|TIMESTAMP
```

### Toggleable Events
```
EVENT_NAME_START|BUTTON_TEXT|TIMESTAMP|TOGGLE:True
EVENT_NAME_END|BUTTON_TEXT|TIMESTAMP|TOGGLE:False
```

### Example Messages
```
TASK_START|Start Task|2024-01-15T10:30:45.123456
FOCUS_MODE_START|Focus Mode|2024-01-15T10:30:45.123456|TOGGLE:True
FOCUS_MODE_END|Focus Mode|2024-01-15T10:35:20.789012|TOGGLE:False
```

## GUI Layout

Each button cell now contains:
- **Main button** (80% width): The actual event button
- **Time offset field** (20% width): Small entry field for time offsets

### Button States

#### Instantaneous Buttons
- Always show the same appearance
- Single click sends one event

#### Toggleable Buttons
- **OFF state**: 🔘 Button Text (raised relief)
- **ON state**: 🔴 Button Text (sunken relief)
- Click toggles between states
- Each state change sends a separate LSL event

## Usage Examples

### Example 1: Logging a Memory Lapse 5 Seconds Ago
1. Click in the time offset field next to "Memory Lapse" button
2. Type "5s"
3. Press **Enter** or click the "Memory Lapse" button
4. Event is logged as occurring 5 seconds ago

### Example 2: Toggling Focus Mode
1. Click "Focus Mode" button (shows 🔴 Focus Mode, sunken)
2. LSL event: `FOCUS_MODE_START|Focus Mode|TIMESTAMP|TOGGLE:True`
3. Click again (shows 🔘 Focus Mode, raised)
4. LSL event: `FOCUS_MODE_END|Focus Mode|TIMESTAMP|TOGGLE:False`

### Example 3: Logging a Break 2 Minutes Ago
1. Click in time offset field next to "Break Time" button
2. Type "2m"
3. Press **Enter** or click "Break Time" button
4. Event is logged as starting 2 minutes ago

## Testing

Use the updated `test_eventboard.py` script to monitor events:

```bash
python test_eventboard.py
```

The script now displays:
- Event name and button text
- LSL timestamp and event timestamp
- Toggle state (for toggleable events)
- Time offset information

## Integration

The enhanced EventBoard integrates seamlessly with existing features:
- **Recording**: All events (instantaneous and toggleable) are recorded
- **Logging**: Visual feedback in main application log
- **LSL**: Separate stream with enhanced event format
- **Time accuracy**: Precise timestamping with offset support

## Troubleshooting

- **Time offset not working**: Ensure format is correct (e.g., "5s", "2m", "1h")
- **Toggle buttons not changing**: Check that button type is set to "toggleable" in config
- **Events not received**: Verify LSL outlet is created successfully (check console output)
- **Time offset field not responding**: Click in the field to activate it, type offset, then press Enter or click button




# Running
```bash
source .venv/bin/activate
python main.py 
python logger_app.py

```

## Installing `liblsl` binaries
https://labstreaminglayer.readthedocs.io/dev/lib_dev.html
```bash
git clone https://github.com/CommanderPho/PhoLogToLabStreamingLayer.git
cd PhoLogToLabStreamingLayer/
uv sync
source .venv/bin/activate


mkdir lib
cd lib
git clone --depth=1 https://github.com/sccn/liblsl.git
cd liblsl/
mkdir build
cd build/
cmake ..
make
sudo make install

```


# Installing Tk/KTinker on macOS
```bash
brew install tcl-tk
export LDFLAGS="-L$(brew --prefix tcl-tk)/lib"
export CPPFLAGS="-I$(brew --prefix tcl-tk)/include"
export PKG_CONFIG_PATH="$(brew --prefix tcl-tk)/lib/pkgconfig"
export PATH="$(brew --prefix tcl-tk)/bin:$PATH"

pyenv uninstall 3.9.13
pyenv install 3.9.13

```


## Output LabStreamingLayer Steams
```python
# 'TextLogger' -  User-Arbitrary timestampped messages
  info = pylsl.StreamInfo(
      name='TextLogger',
      type='Markers',
      channel_count=1,
      nominal_srate=pylsl.IRREGULAR_RATE,
      channel_format=pylsl.cf_string,
      source_id='textlogger_001'
  )

## 'EventBoard' - specific events
  info = pylsl.StreamInfo(
      name='EventBoard',
      type='Markers',
      channel_count=1,
      nominal_srate=pylsl.IRREGULAR_RATE,
      channel_format=pylsl.cf_string,
      source_id='eventboard_001'
  )
  
```


## Building for Agents/AI
```bash

uv lock
uv sync --all-extras
source .venv/bin/activate
uv tool install --editable .


# curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install git+https://github.com/PhoPersonalOrg/uv-deps-switcher.git
uv-deps-switcher --generate-templates
uv-deps-switcher external --checkout-dest "./EXTERNAL" --yes
uv lock
uv sync --all-extras


source .venv/bin/activate



```