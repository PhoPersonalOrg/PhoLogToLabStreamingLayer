#!/bin/bash
# Script to build the PyOxidizer executable on macOS and Linux
# Ensure you have PyOxidizer installed (`cargo install pyoxidizer` or via pre-built binaries)

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR/.."

echo "Building PyOxidizer executable..."
pyoxidizer build --release
if [ $? -ne 0 ]; then
    echo "PyOxidizer build failed."
    exit 1
fi

echo "Build complete. The executable can be found in the build/ directory."
exit 0
