1. **Analyze LabRecorder's XDF Specification and Writing Logic**:
    - The `.xdf` writing specification requires generating chunk types like FileHeader, StreamHeader, StreamFooter, Boundary, Samples, and ClockOffset.
    - Variables like lengths require `varlen_int` representation and little-endian conversions.
2. **Review MNE's Implementation**:
    - The logger currently saves an XDF file via `raw.save(fif_filename)` via MNE or PyXDF, but in MNE, `.save()` typically writes `.fif` or throws, hence why `fif_filename` renaming occurs. Saving actual `.xdf` compliant with LabRecorder requires explicit encoding.
3. **Write a Python module `xdf_writer.py`**:
    - Re-implement the equivalent C++ `XDFWriter` chunk generation in Python.
    - Write chunks according to standard: `FileHeader`, `StreamHeader`, `Samples`.
4. **Integration**:
    - Replace `save_xdf_file` in `logger_app.py` to use our custom Python XDF exporter to export `self.recorded_data`.
5. **Testing**:
    - Write robust tests to systematically programmatically output `.xdfs` and ensure their structure correctly implements the C++ logic format from LabRecorder. Use `pyxdf` to test that they are loadable.
6. **Pre-commit**: Complete pre-commit steps.
