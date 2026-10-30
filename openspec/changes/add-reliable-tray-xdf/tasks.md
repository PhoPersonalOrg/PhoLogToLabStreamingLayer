## 1. lab-recorder-python
- [x] 1.1 Flush after every XDF chunk write
- [x] 1.2 Register `streams=` into StreamManager at start; add `attach_streams()`
- [x] 1.3 Add `rotate_file()` that keeps inlets running
- [x] 1.4 Tests for flush, mid-recording attach, and rotate

## 2. LoggerApp recording
- [x] 2.1 Auto-select newly discovered streams and attach them while recording
- [x] 2.2 Timed auto-split + manual Split via `rotate_file` (RCS stop+start fallback)
- [x] 2.3 Settings: auto-add checkbox and split-interval spinbox
- [x] 2.4 Lengthen stop join timeout; call `is_recording()` correctly

## 3. Tray shell
- [x] 3.1 Window X minimizes to tray; Exit runs `on_closing`
- [x] 3.2 Tray tooltip shows recording health
- [x] 3.3 SIGINT/SIGTERM run `on_closing` so XDF footers are written

## 4. Tests
- [x] 4.1 Settings roundtrip for new keys
- [x] 4.2 Auto-add vs user-deselected stream keys
