## ADDED Requirements

### Requirement: Hot-Add Newly Discovered LSL Streams
The system SHALL add newly discovered LSL inlet streams to the active XDF recording without requiring the user to stop, split, or restart the application.

#### Scenario: EEG appears after recording started
- **WHEN** XDF recording is active
- **AND** a new LSL stream is discovered (continuous discovery or Refresh Streams)
- **AND** auto-add newly discovered streams is enabled
- **THEN** the stream is selected in the stream list
- **AND** it is attached to the open recording (in-process `attach_streams`, or RCS `update` then `select all`)
- **AND** Log History records `STREAM_ADDED_TO_RECORDING` with the stream name

#### Scenario: User-deselected streams stay off
- **WHEN** the user has deselected a discovered stream
- **AND** discovery runs again
- **THEN** that stream is not re-selected solely because it is still present
- **AND** only newly appeared stream keys are auto-selected

#### Scenario: Auto-add disabled
- **WHEN** auto-add newly discovered streams is disabled in Settings
- **THEN** newly appeared streams are shown in the tree but not auto-selected or attached

### Requirement: Timed XDF Auto-Split
The system SHALL periodically close the current XDF as a complete checkpoint and continue recording into a new timestamped file while recording is active.

#### Scenario: Default interval
- **WHEN** no `recording_settings.json` override is present
- **THEN** auto-split interval is 30 minutes
- **AND** auto-add newly discovered streams is enabled

#### Scenario: In-process rotate keeps inlets
- **WHEN** auto-split or Split Recording runs on the in-process LabRecorder path
- **THEN** the current XDF is closed with stream footers
- **AND** a new `{YYYYMMDD_HHMMSS}_log.xdf` is opened
- **AND** existing inlets keep running (no stop/start gap)
- **AND** Log History records `RECORDING_SPLIT_NEW_FILE`

#### Scenario: Zero disables auto-split
- **WHEN** auto-split minutes is 0
- **THEN** no timed split is scheduled
- **AND** the user can still split manually

#### Scenario: Settings persist
- **WHEN** the user changes auto-add or auto-split minutes on the Settings tab
- **THEN** the values are saved to `recording_settings.json`

### Requirement: Close To Tray
The system SHALL keep recording when the main window is closed, hiding to the system tray instead of quitting.

#### Scenario: Window X hides to tray
- **WHEN** the user clicks the window close button
- **THEN** the window is withdrawn to the system tray
- **AND** XDF recording continues if it was active

#### Scenario: Tray Exit stops cleanly
- **WHEN** the user chooses Exit from the tray menu
- **THEN** the app stops recording, writes XDF footers, and exits

#### Scenario: SIGINT writes footers
- **WHEN** the process receives SIGINT or SIGTERM
- **THEN** the same shutdown path as Exit runs so the open XDF is closed with footers
