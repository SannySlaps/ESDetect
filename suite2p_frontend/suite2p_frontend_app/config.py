from __future__ import annotations

import sys
import os
import platform
from pathlib import Path


if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS")).resolve()
    APP_ROOT = _BUNDLE_ROOT / "suite2p_frontend"
    INTEGRATION_ROOT = _BUNDLE_ROOT
else:
    APP_ROOT = Path(__file__).resolve().parents[1]
    INTEGRATION_ROOT = APP_ROOT.parent
SANDBOX_ROOT = INTEGRATION_ROOT / "suite2p_sandbox"
RUNS_ROOT = SANDBOX_ROOT / "runs"


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _default_scientifica_root() -> Path:
    override = os.environ.get("SCIENTIFICA_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    windows_default = Path(r"D:\Scientifica")
    if windows_default.exists():
        return windows_default
    return Path.home() / "Scientifica"


def _default_notification_settings_path() -> Path:
    override = os.environ.get("CAIMAN_NOTIFICATION_SETTINGS", "").strip()
    if override:
        return Path(override).expanduser()
    windows_default = Path(r"C:\Users\tur83376\CaImAn\notification_settings.json")
    if windows_default.exists():
        return windows_default
    return Path.home() / "CaImAn" / "notification_settings.json"


def _default_suite2p_python() -> Path:
    override = os.environ.get("SUITE2P_ENV_PYTHON", "").strip()
    if override:
        return Path(override).expanduser()
    candidates = [
        Path.home() / "AppData" / "Local" / "miniconda3" / "envs" / "suite2p" / "python.exe",
        Path.home() / "miniconda3" / "envs" / "suite2p" / "bin" / "python",
        Path.home() / "opt" / "miniconda3" / "envs" / "suite2p" / "bin" / "python",
        Path("/opt/homebrew/Caskroom/miniconda/base/envs/suite2p/bin/python"),
        Path("/usr/local/Caskroom/miniconda/base/envs/suite2p/bin/python"),
    ]
    return _first_existing(candidates) or candidates[0]


SCIENTIFICA_ROOT = _default_scientifica_root()
PRESETS_ROOT = SCIENTIFICA_ROOT / "suite2p_information" / "parameter_presets"
DEFAULT_NOTIFICATION_SETTINGS_PATH = _default_notification_settings_path()
SUITE2P_ENV_PYTHON = _default_suite2p_python()

ACQUISITION_APP_SOURCE = INTEGRATION_ROOT / "Acquisition and Stim" / "Calcium_Imaging_copy.py"
if platform.system() == "Windows":
    _acquisition_python = INTEGRATION_ROOT / "Acquisition and Stim" / "venv" / "Scripts" / "python.exe"
else:
    _acquisition_python = INTEGRATION_ROOT / "Acquisition and Stim" / "venv" / "bin" / "python"
ACQUISITION_APP_PYTHON = _acquisition_python if _acquisition_python.exists() else Path(sys.executable)

PREPARE_SCRIPT = SANDBOX_ROOT / "scripts" / "prepare_suite2p_session.py"
RUN_SCRIPT = SANDBOX_ROOT / "scripts" / "run_suite2p_session.py"
EXPORT_SCRIPT = SANDBOX_ROOT / "scripts" / "export_suite2p_artifacts.py"
APP_NAME = "Suite2p Frontend"
