from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import DEFAULT_NOTIFICATION_SETTINGS_PATH


@dataclass
class NotificationConfig:
    enabled: bool = False
    notify_on_success: bool = True
    notify_on_failure: bool = True
    notify_per_batch_session: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    use_tls: bool = True
    sender_email: str = ""
    recipient_email: str = ""
    username: str = ""
    password: str = ""
    settings_path: str = str(DEFAULT_NOTIFICATION_SETTINGS_PATH)


@dataclass
class RuntimeState:
    session_path: Path | None = None
    run_dir: Path | None = None
    plane_dir: Path | None = None
    snapshot_dir: Path | None = None
    run_name: str = ""
    acquisition_metadata: dict[str, Any] | None = None
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
