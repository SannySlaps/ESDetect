from __future__ import annotations

import importlib.util
import os
import platform
import subprocess
import sys
from pathlib import Path


def _can_import_suite2p() -> bool:
    return importlib.util.find_spec("suite2p") is not None


def _maybe_relaunch_in_suite2p_env() -> None:
    if _can_import_suite2p():
        return
    if os.environ.get("SUITE2P_FRONTEND_RELAUNCH") == "1":
        return
    candidates = [
        Path.home() / "AppData" / "Local" / "miniconda3" / "envs" / "suite2p" / "python.exe",
        Path.home() / "miniconda3" / "envs" / "suite2p" / "bin" / "python",
        Path.home() / "opt" / "miniconda3" / "envs" / "suite2p" / "bin" / "python",
        Path("/opt/homebrew/Caskroom/miniconda/base/envs/suite2p/bin/python"),
        Path("/usr/local/Caskroom/miniconda/base/envs/suite2p/bin/python"),
    ]
    fallback_python = next((path for path in candidates if path.exists()), None)
    if fallback_python is None:
        return
    child_env = os.environ.copy()
    child_env["SUITE2P_FRONTEND_RELAUNCH"] = "1"
    subprocess.Popen([str(fallback_python), str(Path(__file__).resolve())], cwd=str(Path(__file__).resolve().parent), env=child_env)
    raise SystemExit(0)


_maybe_relaunch_in_suite2p_env()

if __package__:
    from .ui_tk import launch
else:
    package_root = Path(__file__).resolve().parent.parent
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))
    from suite2p_frontend_app.ui_tk import launch


if __name__ == "__main__":
    launch()
