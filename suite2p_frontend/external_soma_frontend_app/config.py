from __future__ import annotations

from pathlib import Path

from suite2p_frontend_app.config import (
    ACQUISITION_APP_PYTHON,
    ACQUISITION_APP_SOURCE,
    DEFAULT_NOTIFICATION_SETTINGS_PATH,
    EXPORT_SCRIPT,
    PRESETS_ROOT,
    SANDBOX_ROOT,
    SUITE2P_ENV_PYTHON,
)


APP_NAME = "ESDetect Frontend"
APP_SUBTITLE = "Custom frontend using Suite2p and the external soma detector ESDetect."
DEFAULT_ESDETECT_PRESET = PRESETS_ROOT / "ESDetect_recall_first_v3.json"
EXTERNAL_SEGMENT_SCRIPT = SANDBOX_ROOT / "scripts" / "external_soma_segment.py"
EXTERNAL_EXTRACT_SCRIPT = SANDBOX_ROOT / "scripts" / "external_soma_extract.py"
EXTERNAL_PACKAGE_SCRIPT = SANDBOX_ROOT / "scripts" / "external_soma_package.py"
FULL_OVERLAY_SCRIPT = SANDBOX_ROOT / "scripts" / "make_esdetect_full_overlay_video.py"
EXTERNAL_RUNS_ROOT_NAME = "ESDetect_runs"
ESDETECT_SEGMENTATION_DIRNAME = "ESDetect_segmentation"
ESDETECT_EXTRACTION_DIRNAME = "ESDetect_extraction"
ESDETECT_PACKAGED_DIRNAME = "ESDetect_packaged"
