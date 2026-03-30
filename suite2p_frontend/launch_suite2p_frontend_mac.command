#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_MAIN="$SCRIPT_DIR/suite2p_frontend_app/main.py"

if command -v conda >/dev/null 2>&1; then
  eval "$(conda shell.bash hook)"
  if conda env list | awk '{print $1}' | grep -qx "suite2p"; then
    conda activate suite2p
  fi
fi

PYTHON_BIN="${SUITE2P_ENV_PYTHON:-python}"
exec "$PYTHON_BIN" "$APP_MAIN"
