@echo off
REM Script to build the PyOxidizer executable on Windows
REM Ensure you have PyOxidizer installed (`cargo install pyoxidizer` or via pre-built binaries)
SET DIR=%~dp0
cd "%DIR%\.."

echo Building PyOxidizer executable for Windows...
pyoxidizer build --release
if %errorlevel% neq 0 (
    echo PyOxidizer build failed.
    exit /b %errorlevel%
)

echo Build complete. The executable can be found in build\x86_64-pc-windows-msvc\release\install\
exit /b 0
