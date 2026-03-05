---
name: Default transcription device auto
overview: Confirm and document that the default processing device is "auto" and that auto already prefers CUDA when available; optionally add a short UI hint so users do not need to manually select cuda.
todos: []
isProject: false
---

# Default transcription device: keep "auto" and document CUDA preference

## Current behavior (no bug)

- **Mixin default**: In [live_whisper_transcription.py](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\whisper-timestamped\whisper_timestamped\mixins\live_whisper_transcription.py), `setup_transcription_config()` already sets `device=None` (line 198). The Settings dialog shows this as `"auto"` via `self.transcription_config.device or "auto"` (line 398).
- **Runtime "auto"**: In [live.py](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\whisper-timestamped\whisper_timestamped\live.py), `LiveTranscriber.__init`__ already implements auto as “prefer CUDA when available” (lines 104–105):

```python
  if self.cfg.device is None:
      self.cfg.device = "cuda" if TORCH_AVAILABLE and torch.cuda.is_available() else "cpu"
  

```

So the default is already "auto", and auto already prefers CUDA when available. No logic change is required.

## Recommended changes (documentation and UX only)

1. **Mixin: document the default**
  In `setup_transcription_config()` in [live_whisper_transcription.py](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\whisper-timestamped\whisper_timestamped\mixins\live_whisper_transcription.py), add a one-line comment next to `device=None` stating that "auto" is implemented in `LiveTranscriber` and prefers CUDA when available.
2. **Settings dialog: clarify "auto"**
  In `show_transcription_settings()` in the same file, add a short hint under the "Processing Device" dropdown (e.g. a second label or a single-line note) so users see that **Auto uses CUDA when available, otherwise CPU**. That way they know they do not need to manually choose "cuda" for the default to use the GPU.

No edits to [live.py](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\whisper-timestamped\whisper_timestamped\live.py) are needed; behavior is already correct.

## If you still see CPU when CUDA is available

If the app uses CPU even though a GPU is present, possible causes are:

- **PyTorch not installed or not built with CUDA**: `TORCH_AVAILABLE` or `torch.cuda.is_available()` is false. Installing a CUDA-enabled PyTorch would fix that; the existing auto logic would then pick `"cuda"`.
- **faster-whisper**: The code uses `faster_whisper.WhisperModel`, which may use a different backend (e.g. CTranslate2). The plan above only documents the current `live.py` behavior; if faster_whisper ignores `device` or uses its own logic, that would be a separate investigation.

## Summary


| Item               | Action                                                                                    |
| ------------------ | ----------------------------------------------------------------------------------------- |
| Default value      | Already `device=None` ("auto"); no code change.                                           |
| Auto = prefer CUDA | Already implemented in `live.py`; no code change.                                         |
| Comment in mixin   | Add one line documenting that auto prefers CUDA (in LiveTranscriber).                     |
| Settings UI        | Add a short hint under Processing Device: "Auto uses CUDA when available, otherwise CPU." |


