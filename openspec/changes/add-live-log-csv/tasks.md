## 1. Implementation
- [x] 1.1 Add `LiveLogCsvWriter` (queue + background flush, start/stop/reconfigure)
- [x] 1.2 Hook writer from `update_log_display`; start/stop with app lifecycle
- [x] 1.3 Replace Settings placeholder with Live Log CSV controls + `live_log_csv_settings.json`

## 2. Testing
- [x] 2.1 Verify continuous append for manual, EventBoard, and transcript lines
- [x] 2.2 Verify enable/disable and output-directory Browse behavior
