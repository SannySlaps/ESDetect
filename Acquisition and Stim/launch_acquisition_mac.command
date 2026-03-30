#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_MAIN="$SCRIPT_DIR/Calcium_Imaging_copy.py"

if command -v conda >/dev/null 2>&1; then
  eval "$(conda shell.bash hook)"
fi

PYTHON_BIN="${ACQUISITION_APP_PYTHON:-python}"
exec "$PYTHON_BIN" "$APP_MAIN"
