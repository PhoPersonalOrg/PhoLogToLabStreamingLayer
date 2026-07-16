## ADDED Requirements

### Requirement: Live Log History CSV
The system SHALL continuously append Log History lines to a plaintext CSV session file when live log CSV is enabled.

#### Scenario: Enabled by default at launch
- **WHEN** the app launches and no `live_log_csv_settings.json` override disables the feature
- **THEN** live log CSV is enabled
- **AND** a session file `live_log_YYYYMMDD_HHMMSS.csv` is created under the configured output directory (default `{xdf_folder}/CSV`)
- **AND** the file has header columns `Timestamp, Message`

#### Scenario: Continuous append mirrors Log History
- **WHEN** a message is shown in Log History via `update_log_display`
- **THEN** a matching CSV row is enqueued for background write
- **AND** the row is flushed to disk without waiting for recording stop or app exit

#### Scenario: Settings toggle and path
- **WHEN** the user opens the Settings tab
- **THEN** a Live Log CSV group shows an enable checkbox and output directory control
- **WHEN** the user changes enable state or browses to a new directory
- **THEN** the writer updates immediately
- **AND** preferences are persisted to `live_log_csv_settings.json`

#### Scenario: Disabled stops new rows
- **WHEN** live log CSV is disabled
- **THEN** further Log History lines are not appended to the CSV
- **AND** the writer drains any queued rows then closes the file cleanly
