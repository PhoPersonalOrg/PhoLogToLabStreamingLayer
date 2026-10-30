## 1. Crash-safe WAV
- [x] 1.1 Implement `RecoverableWavWriter` with periodic RIFF header patch and atomic sidecar JSON
- [x] 1.2 Tests: abandon-without-finalize still opens in `wave`; sidecar `complete` only after clean close

## 2. Shared mic
- [x] 2.1 Add refcounted `MicCaptureHub`
- [x] 2.2 `LiveTranscriber` subscribes instead of owning `InputStream`
- [x] 2.3 Hub stays open when one of two subscribers unsubscribes

## 3. LoggerApp
- [x] 3.1 Start/stop/rotate raw audio with XDF; emit `AUDIO_FILE_STARTED` / `AUDIO_FILE_STOPPED`
- [x] 3.2 Settings checkbox `record_raw_audio` (default on); Live Audio status line
- [x] 3.3 Disable Whisper duplicate WAV while XDF raw audio is enabled
- [x] 3.4 Settings roundtrip and XDF hook unit tests
