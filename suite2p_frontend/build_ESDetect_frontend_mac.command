#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if ! python -m PyInstaller --version >/dev/null 2>&1; then
  echo "PyInstaller not found in the active environment. Install with: pip install pyinstaller"
  exit 1
fi

python -m PyInstaller --noconfirm "./ESDetectFrontend.spec"

echo
echo "Build complete."
echo "App bundle: $SCRIPT_DIR/dist/ESDetect/ESDetect.app"
