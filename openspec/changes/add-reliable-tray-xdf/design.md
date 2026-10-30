## Context
LoggerApp records via in-process `LabRecorder` (or Windows LabRecorderService RCS). Start currently snapshots `get_selected_streams()` once. Discovery/refresh only update the tree.

## Goals / Non-Goals
- Goals: hot-add late LSL inlets into the open XDF; periodic complete XDF checkpoints; tray hide-on-close; flush so a kill loses at most the in-flight buffer
- Non-Goals: size-based split; dual-writer overlap; replacing live CSV

## Decisions
- Hot-add uses XDF `StreamHeader` mid-file (`SimpleXDFWriter.add_stream`). Late streams have a later first timestamp; pyxdf loads this.
- Timed split uses `LabRecorder.rotate_file()` (keep inlets, swap writer) so EEG is not dropped for the old stop+start join. RCS path still stop+start.
- Do **not** set StreamManager `select_all_active`. That would re-select streams the user unchecked on every watch cycle. LoggerApp discovery calls `attach_streams()` for newly appeared keys only.
- Window X → `minimize_to_tray`. `on_closing` remains the only full shutdown (tray Exit, SIGINT).

## Risks / Trade-offs
- Rotate drain can delay the writer thread by one lock hold; samples stay in `_data_buffers`.
- RCS `select all` after `update` may re-include streams the C++ UI had deselected; in-process path honors LoggerApp selection.
