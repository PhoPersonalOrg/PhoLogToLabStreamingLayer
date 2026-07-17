## ADDED Requirements

### Requirement: Configurable XDF Auto-Start
The system SHALL allow enabling or disabling automatic XDF recording start after stream discovery at launch, with the preference persisted and defaulting to enabled.

#### Scenario: Enabled by default
- **WHEN** the app launches and no `recording_settings.json` override disables auto-start
- **THEN** auto-start XDF recording on startup is enabled
- **AND** after streams are discovered the app attempts to auto-start XDF recording

#### Scenario: Settings checkbox persists
- **WHEN** the user opens the Settings tab
- **THEN** an XDF Recording group shows an Auto-start XDF recording on startup checkbox
- **WHEN** the user changes the checkbox
- **THEN** the preference is saved to `recording_settings.json`
- **AND** the change does not start or stop an in-progress recording session

#### Scenario: Disabled skips auto-start
- **WHEN** auto-start is disabled in settings
- **AND** streams are discovered at launch
- **THEN** the app does not auto-start XDF recording
- **AND** the user can still start recording manually
