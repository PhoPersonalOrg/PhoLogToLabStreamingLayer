## ADDED Requirements

### Requirement: Raw Audio Files Follow XDF
The system SHALL write crash-recoverable raw microphone WAV files for each XDF recording segment when raw audio recording is enabled, independent of live transcription.

#### Scenario: Starts and stops with XDF
- **WHEN** XDF recording starts and `record_raw_audio` is enabled
- **THEN** a 16 kHz mono PCM_16 WAV is opened at `{xdf_folder}/raw_audio/{YYYYMMDD_HHMMSS}_audio.wav` using the same timestamp as the XDF
- **AND** an atomic sidecar `{stem}.json` is created
- **WHEN** XDF recording stops
- **THEN** the WAV header is finalized and the sidecar is marked `complete`

#### Scenario: Splits with XDF
- **WHEN** the XDF is split or rotated
- **THEN** the current WAV is finalized with `AUDIO_FILE_STOPPED`
- **AND** a new WAV is opened for the new XDF timestamp with `AUDIO_FILE_STARTED`

#### Scenario: Independent of transcription
- **WHEN** live transcription is off (or is stopped mid-session)
- **THEN** raw audio capture continues for the open XDF
- **AND** stopping raw audio does not stop transcription

#### Scenario: Disabled in Settings
- **WHEN** `record_raw_audio` is disabled
- **THEN** no WAV is written for XDF start/split
- **AND** toggling the setting mid-session starts or stops only the WAV, not the XDF

#### Scenario: Interrupted recording is playable
- **WHEN** the process is killed after at least one header patch
- **THEN** the leftover WAV opens as a valid PCM file with frames up to the last patch
- **AND** the sidecar has `complete` false

### Requirement: Raw Audio LSL Sync Markers
The system SHALL emit TextLogger markers for raw audio file boundaries so post-hoc transcription can align WAV sample 0 to XDF/EEG time.

#### Scenario: File start marker
- **WHEN** the first audio block is captured into a new WAV
- **THEN** TextLogger receives `AUDIO_FILE_STARTED: {full_path}|sr=16000|ch=1|fmt=PCM_16|lsl_start={clock}`
- **AND** `lsl_start` is `pylsl.local_clock()` at that first block
- **AND** Log History shows the same line

#### Scenario: File stop marker
- **WHEN** a WAV is finalized (XDF stop, split, unexpected recorder death, or Settings disable)
- **THEN** TextLogger receives `AUDIO_FILE_STOPPED: {full_path}|frames={n}|duration_s={s}|lsl_stop={clock}`
- **AND** the marker is sent before the XDF recorder thread is joined
