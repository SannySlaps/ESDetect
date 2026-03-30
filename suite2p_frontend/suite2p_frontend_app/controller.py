from __future__ import annotations

import smtplib
import csv
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import math
from dataclasses import asdict
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Callable

import matplotlib
import numpy as np
import openpyxl
from scipy import stats as scipy_stats
from scipy.ndimage import maximum_filter

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .config import ACQUISITION_APP_PYTHON, ACQUISITION_APP_SOURCE, DEFAULT_NOTIFICATION_SETTINGS_PATH, EXPORT_SCRIPT, PREPARE_SCRIPT, PRESETS_ROOT, RUN_SCRIPT, RUNS_ROOT, SANDBOX_ROOT, SCIENTIFICA_ROOT, SUITE2P_ENV_PYTHON
from .state import RuntimeState

RETAINED_BIN_CLEANUP_SCRIPT = SANDBOX_ROOT / "scripts" / "cleanup_retained_binaries_after_curation.py"

try:
    from suite2p import default_settings as _suite2p_default_settings
    from suite2p.detection.stats import roi_stats as _suite2p_roi_stats
    from suite2p.extraction.dcnv import oasis as _suite2p_oasis
except Exception:
    _suite2p_default_settings = None
    _suite2p_roi_stats = None
    _suite2p_oasis = None
    _vendored_suite2p = SANDBOX_ROOT / "external" / "suite2p"
    if _vendored_suite2p.exists():
        sys.path.insert(0, str(_vendored_suite2p))
        try:
            from suite2p import default_settings as _suite2p_default_settings
            from suite2p.detection.stats import roi_stats as _suite2p_roi_stats
            from suite2p.extraction.dcnv import oasis as _suite2p_oasis
        except Exception:
            _suite2p_default_settings = None
            _suite2p_roi_stats = None
            _suite2p_oasis = None


class Suite2pController:
    SESSION_MANAGED_DB_KEYS = {"fs"}
    MIN_TEMP_FREE_BYTES = 10 * 1024**3
    MIN_OUTPUT_FREE_BYTES = 2 * 1024**3
    SESSION_NOTIFICATION_ACTIONS = {"Run From Session", "Run Suite2p"}

    def __init__(self, state: RuntimeState, logger: Callable[[str], None] | None = None) -> None:
        self.state = state
        self._logger = logger or print
        self._status_callback: Callable[[str], None] | None = None

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._logger(f"[{timestamp}] {message}")

    def set_status_callback(self, callback: Callable[[str], None] | None) -> None:
        self._status_callback = callback

    def set_status(self, message: str) -> None:
        if self._status_callback is not None:
            self._status_callback(message)

    def load_notification_settings(self) -> None:
        settings_path = Path(self.state.notifications.settings_path or DEFAULT_NOTIFICATION_SETTINGS_PATH)
        self.state.notifications.settings_path = str(settings_path)
        if not settings_path.exists():
            return
        try:
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.log(f"Could not read notification settings: {exc}")
            return
        for key, value in payload.items():
            if hasattr(self.state.notifications, key):
                setattr(self.state.notifications, key, value)
        self.log(f"Loaded notification settings: {settings_path}")

    def save_notification_settings(self) -> Path:
        settings_path = Path(self.state.notifications.settings_path or DEFAULT_NOTIFICATION_SETTINGS_PATH)
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self.state.notifications)
        settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.log(f"Saved notification settings: {settings_path}")
        return settings_path

    def _send_email_notification(self, subject: str, body: str) -> None:
        cfg = self.state.notifications
        if not cfg.enabled:
            return
        required = {
            "smtp_host": cfg.smtp_host,
            "sender_email": cfg.sender_email,
            "recipient_email": cfg.recipient_email,
        }
        missing = [key for key, value in required.items() if not str(value).strip()]
        if missing:
            raise RuntimeError(f"Notification settings incomplete: missing {', '.join(missing)}")
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg.sender_email
        msg["To"] = cfg.recipient_email
        msg.set_content(body)
        with smtplib.SMTP(cfg.smtp_host, int(cfg.smtp_port), timeout=30) as server:
            if cfg.use_tls:
                server.starttls()
            if str(cfg.username).strip():
                server.login(cfg.username, cfg.password)
            server.send_message(msg)

    def send_test_email(self) -> None:
        subject = "Suite2p Frontend notification test"
        body = (
            "Suite2p Frontend sandbox email test\n\n"
            f"Time: {datetime.now().isoformat()}\n"
            f"Session: {self.state.session_path or 'none loaded'}\n"
            f"Run dir: {self.state.run_dir or 'none loaded'}\n"
        )
        self._send_email_notification(subject, body)
        self.log(f"Sent test email to {self.state.notifications.recipient_email}")

    def notify_action_result(self, action_name: str, *, success: bool, detail: str = "") -> None:
        cfg = self.state.notifications
        if not cfg.enabled:
            return
        if action_name not in self.SESSION_NOTIFICATION_ACTIONS:
            return
        if success and not cfg.notify_on_success:
            return
        if not success and not cfg.notify_on_failure:
            return

        context = Path(self.state.session_path).name if self.state.session_path else "Suite2p Frontend"
        status_text = "completed" if success else "failed"
        subject = f"{context}: {action_name} {status_text}"
        lines = [
            f"Action: {action_name}",
            f"Status: {status_text}",
            f"Time: {datetime.now().isoformat()}",
        ]
        if self.state.session_path is not None:
            lines.append(f"Session: {self.state.session_path}")
        if self.state.run_dir is not None:
            lines.append(f"Run dir: {self.state.run_dir}")
        if self.state.plane_dir is not None:
            lines.append(f"Outputs: {self.state.plane_dir}")
        if detail.strip():
            lines.extend(["", detail.strip()])
        try:
            self._send_email_notification(subject, "\n".join(lines) + "\n")
            self.log(f"{action_name} {status_text} email sent to {cfg.recipient_email}")
        except Exception as exc:
            self.log(f"{action_name} email skipped: {exc}")

    def notify_batch_session_result(self, session_path: str, status: str, detail: str = "") -> None:
        cfg = self.state.notifications
        if not cfg.enabled or not cfg.notify_per_batch_session:
            return
        if status == "completed" and not cfg.notify_on_success:
            return
        if status == "skipped" and not cfg.notify_on_success:
            return
        if status == "failed" and not cfg.notify_on_failure:
            return
        if status not in {"completed", "skipped", "failed"}:
            return

        session_obj = Path(session_path).expanduser().resolve()
        session_name = session_obj.name
        slice_label = ""
        mouse_label = ""
        for parent in session_obj.parents:
            if not slice_label and parent.name.lower().startswith("slice_"):
                slice_suffix = parent.name.split("_", 1)[1] if "_" in parent.name else parent.name
                slice_label = f"Slice {slice_suffix}"
                continue
            if slice_label and not mouse_label:
                mouse_label = f"Mouse {parent.name}"
                break

        progress_text = ""
        detail_text = str(detail).strip()
        detail_lines = [line for line in detail_text.splitlines() if line.strip()]
        if detail_lines and detail_lines[0].lower().startswith("batch progress:"):
            progress_text = detail_lines[0].split(":", 1)[1].strip()

        extra_detail_lines = detail_lines[1:] if progress_text and detail_lines else detail_lines
        failure_reason = ""
        if status == "failed" and extra_detail_lines:
            failure_reason = extra_detail_lines[0].strip()
            failure_reason = failure_reason.replace("\n", " ").replace("\r", " ")
            if len(failure_reason) > 80:
                failure_reason = failure_reason[:77].rstrip() + "..."

        subject_prefix = f"[{progress_text}] " if progress_text else ""
        subject = f"{subject_prefix}Batch session {status}"
        lines = [
            "Suite2p batch session update",
            "",
        ]
        summary_line_parts = []
        if progress_text:
            summary_line_parts.append(f"[{progress_text}]")
        if mouse_label:
            summary_line_parts.append(mouse_label)
        if slice_label:
            summary_line_parts.append(slice_label)
        summary_line_parts.append(session_name)
        summary_line_parts.append(f"batch session {status}")
        if status == "failed" and failure_reason:
            summary_line_parts.append(f": {failure_reason}")
        lines.extend(
            [
                " ".join(summary_line_parts),
                "",
                f"Status: {status}",
                f"Time: {datetime.now().isoformat()}",
            ]
        )
        if progress_text:
            lines.append(f"Batch progress: {progress_text}")
        if mouse_label:
            lines.append(f"Mouse: {mouse_label.replace('Mouse ', '')}")
        if slice_label:
            lines.append(f"Slice: {slice_label.replace('Slice ', '')}")
        if extra_detail_lines:
            lines.extend(["", "\n".join(extra_detail_lines)])
        try:
            self._send_email_notification(subject, "\n".join(lines) + "\n")
            self.log(f"Batch session {status} email sent for {session_name}")
        except Exception as exc:
            self.log(f"Batch session email skipped for {session_name}: {exc}")

    def _suite2p_python(self) -> Path:
        if SUITE2P_ENV_PYTHON.exists():
            return SUITE2P_ENV_PYTHON
        current = Path(sys.executable)
        return current

    def launch_acquisition_app(self) -> str:
        app_path = ACQUISITION_APP_SOURCE
        if not app_path.exists():
            raise FileNotFoundError(f"Acquisition app not found: {app_path}")
        python_path = ACQUISITION_APP_PYTHON
        if not python_path.exists():
            raise FileNotFoundError(f"Acquisition app interpreter not found: {python_path}")
        subprocess.Popen([str(python_path), str(app_path)], cwd=str(app_path.parent))
        self.log(f"Launched acquisition app: {app_path}")
        return str(app_path)

    def import_acquisition_session(self, session_root: str | Path) -> dict[str, str]:
        session_path = Path(session_root).expanduser().resolve()
        if not session_path.exists():
            raise FileNotFoundError(f"Session folder not found: {session_path}")

        metadata_dir = session_path / "metadata"
        metadata_path = metadata_dir / "session_metadata.json"
        payload: dict[str, object] = {}
        if metadata_path.exists():
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))

        raw_path = Path(str(payload.get("raw_path", ""))).expanduser() if payload.get("raw_path") else None
        if raw_path is None or not raw_path.exists():
            raw_candidates = sorted(session_path.glob("raw_*"), key=lambda p: p.stat().st_mtime, reverse=True)
            raw_path = raw_candidates[0] if raw_candidates else None

        self.state.session_path = session_path
        self.state.run_dir = None
        self.state.plane_dir = None
        self.state.snapshot_dir = None
        self.state.run_name = ""
        if payload:
            payload = payload.copy()
            payload["session_path"] = str(session_path)
        else:
            payload = {"session_id": session_path.name, "session_path": str(session_path)}
        self.state.acquisition_metadata = payload

        self.log(f"Imported acquisition session: {session_path}")
        if raw_path is not None:
            tif_paths = sorted(list(raw_path.glob("*.tif")) + list(raw_path.glob("*.tiff")))
            self.log(f"Raw TIFF source: {raw_path} ({len(tif_paths)} files)")
        else:
            self.log(f"Imported session has no detected raw_<timestamp> folder under: {session_path}")
        self.set_status("Acquisition session imported.")
        return {
            "session_path": str(session_path),
            "session_id": str(payload.get("session_id", session_path.name)),
            "project_name": str(payload.get("project_name", "")),
            "condition_subtype": str(payload.get("condition_subtype", "")),
            "condition_type": str(payload.get("condition_type", "")),
            "animal_id": str(payload.get("animal_id", "")),
            "slice_number": str(payload.get("slice_number", "")),
            "frame_rate_hz": str(payload.get("acquired_frame_rate_hz") or payload.get("frame_rate_hz") or ""),
        }

    def _next_session_path(self) -> Path:
        if self.state.session_path is None:
            raise RuntimeError("No acquisition session is active. Import a session first.")
        current_session = Path(self.state.session_path).expanduser().resolve()
        slice_root = current_session.parent
        sessions = sorted(
            [p for p in slice_root.glob("Session_*") if p.is_dir()],
            key=lambda p: self._natural_key(p.name),
        )
        if current_session not in sessions:
            raise RuntimeError(f"Current session is not under a standard Session_<NNN> folder: {current_session}")
        current_index = sessions.index(current_session)
        if current_index + 1 >= len(sessions):
            raise RuntimeError(f"No next session found after {current_session.name} in {slice_root}")
        return sessions[current_index + 1]

    def import_next_acquisition_session(self) -> dict[str, str]:
        next_session = self._next_session_path()
        self.log(f"Importing next session based on numbering: {next_session.name}")
        return self.import_acquisition_session(next_session)

    def _run_subprocess(self, cmd: list[str], cwd: Path | None = None) -> int:
        self.log(f"Running: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            self._logger(line.rstrip("\n"))
        return proc.wait()

    def prepare_session(self, session_path: str, run_name: str = "") -> Path:
        session = Path(session_path).expanduser().resolve()
        if not session.exists():
            raise RuntimeError(f"Session path does not exist: {session}")
        cmd = [str(self._suite2p_python()), str(PREPARE_SCRIPT), "--session", str(session)]
        if run_name.strip():
            cmd.extend(["--run-name", run_name.strip()])
        self.set_status("Preparing Suite2p run...")
        code = self._run_subprocess(cmd, cwd=PREPARE_SCRIPT.parent)
        if code != 0:
            raise RuntimeError(f"Prepare Suite2p run failed with exit code {code}.")

        self.state.session_path = session
        self.state.run_dir = self._latest_run_for_session(session)
        if self.state.run_dir is None:
            raise RuntimeError("Prepared run folder could not be found after script finished.")
        self.state.run_name = self.state.run_dir.name
        self.set_status("Suite2p run prepared.")
        self.log(f"Prepared run dir: {self.state.run_dir}")
        try:
            db = json.loads((self.state.run_dir / "suite2p_db.json").read_text(encoding="utf-8"))
            if "fs" in db:
                self.log(f"Frame rate loaded for Suite2p: {db['fs']} Hz")
        except Exception:
            pass
        return self.state.run_dir

    def default_run_root(self, session_path: str | Path) -> Path:
        session = Path(session_path).expanduser().resolve()
        return session / "suite2p_runs"

    def default_info_root(self, session_path: str | Path) -> Path:
        return SCIENTIFICA_ROOT / "suite2p_information"

    def default_fast_disk_root(self) -> Path:
        return Path.home() / "suite2p" / "temp"

    def run_session_pipeline(self, session_path: str, run_name: str = "") -> Path:
        run_dir = self.prepare_session(session_path, run_name)
        self.run_suite2p()
        return run_dir

    def session_has_outputs(self, session_path: str | Path) -> bool:
        plane_dir = self.default_plane_dir(session_path)
        return (plane_dir / "ops.npy").exists()

    def run_session_pipeline_with_payload(
        self,
        session_path: str,
        *,
        run_name: str = "",
        parameter_payload: dict | None = None,
        skip_existing: bool = True,
        archive_existing: bool = False,
        cleanup_temp_after_success: bool = False,
    ) -> dict[str, str]:
        session = Path(session_path).expanduser().resolve()
        if skip_existing and self.session_has_outputs(session):
            self.log(f"Skipping existing Suite2p outputs for session: {session}")
            return {"session": str(session), "status": "skipped"}

        archive_path = ""
        if archive_existing and self.session_has_outputs(session):
            archive_path = str(self.archive_existing_outputs(session))

        run_dir = self.prepare_session(str(session), run_name)
        if parameter_payload is not None:
            self.save_parameter_payload(parameter_payload, preserve_session_managed=True)
        self.run_suite2p(quiet_launch_note=True)
        cleaned_temp = ""
        if cleanup_temp_after_success:
            cleaned = self.cleanup_run_temp(run_dir)
            if cleaned is not None:
                cleaned_temp = str(cleaned)
        result = {"session": str(session), "status": "completed", "run_dir": str(run_dir)}
        if archive_path:
            result["archived_to"] = archive_path
        if cleaned_temp:
            result["cleaned_temp"] = cleaned_temp
        return result

    def run_batch_sessions(
        self,
        sessions: list[str],
        *,
        parameter_payload: dict | None = None,
        skip_existing: bool = True,
        archive_existing: bool = False,
        cleanup_temp_after_success: bool = False,
        should_stop: Callable[[], bool] | None = None,
        on_session_status: Callable[[str, str, str], None] | None = None,
    ) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        total = len(sessions)
        for index, session in enumerate(sessions, start=1):
            progress_detail = f"Batch progress: {index}/{total}"
            self.set_status(f"Batch {index}/{total}: {Path(session).name}")
            self.log(f"Batch {index}/{total}: {session}")
            if on_session_status is not None:
                on_session_status(session, "running", progress_detail)
            try:
                result = self.run_session_pipeline_with_payload(
                    session,
                    parameter_payload=parameter_payload,
                    skip_existing=skip_existing,
                    archive_existing=archive_existing,
                    cleanup_temp_after_success=cleanup_temp_after_success,
                )
            except Exception as exc:
                if on_session_status is not None:
                    on_session_status(session, "failed", f"{progress_detail}\n{exc}")
                raise
            results.append(result)
            if on_session_status is not None:
                on_session_status(session, result.get("status", "completed"), progress_detail)
            if should_stop is not None and should_stop():
                self.log("Batch stop requested. Stopping after current session.")
                self.set_status("Batch stopped after current session.")
                break
        self.set_status("Batch analysis finished.")
        return results

    def _raw_dir_for_session(self, session_path: Path) -> Path | None:
        raw_dirs = sorted(session_path.glob("raw_*"))
        return raw_dirs[0] if raw_dirs else None

    def _metadata_file_for_session(self, session_path: Path) -> Path:
        return session_path / "metadata" / "session_metadata.json"

    def is_valid_session_folder(self, session_path: str | Path) -> bool:
        session = Path(session_path).expanduser().resolve()
        if not session.exists() or not session.is_dir():
            return False
        if not session.name.lower().startswith("session_"):
            return False
        raw_dir = self._raw_dir_for_session(session)
        metadata_file = self._metadata_file_for_session(session)
        return bool(raw_dir and raw_dir.exists() and metadata_file.exists())

    def _raw_tiff_paths(self, raw_dir: Path | None) -> list[Path]:
        if raw_dir is None or not raw_dir.exists():
            return []
        return sorted(raw_dir.glob("*.tif"))

    def _dir_size_bytes(self, path: Path) -> int:
        total = 0
        if not path.exists():
            return total
        if path.is_file():
            return path.stat().st_size
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
        return total

    def _is_path_writable(self, root: Path) -> tuple[bool, str]:
        try:
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix="suite2p_write_test_", dir=root, delete=False) as handle:
                probe = Path(handle.name)
            probe.unlink(missing_ok=True)
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _format_gb(self, byte_count: int) -> str:
        return f"{byte_count / (1024**3):.2f} GB"

    def _preflight_report_root(self) -> Path:
        root = self.default_info_root(Path.cwd())
        report_root = root / "preflight_reports"
        report_root.mkdir(parents=True, exist_ok=True)
        return report_root

    def validate_batch_sessions(
        self,
        sessions: list[str],
        *,
        skip_existing: bool = True,
        archive_existing: bool = False,
    ) -> dict:
        if not sessions:
            raise RuntimeError("No sessions were provided for preflight validation.")

        temp_root = self.default_fast_disk_root()
        temp_root.mkdir(parents=True, exist_ok=True)
        temp_usage = shutil.disk_usage(temp_root)
        temp_free = temp_usage.free

        session_reports: list[dict] = []
        blocking_failures = 0

        for session_text in sessions:
            session = Path(session_text).expanduser().resolve()
            output_root = self.default_output_root(session)
            raw_dir = self._raw_dir_for_session(session)
            raw_tiffs = self._raw_tiff_paths(raw_dir)
            metadata_file = self._metadata_file_for_session(session)
            existing_output_root = output_root / "suite2p"
            existing_output_size = self._dir_size_bytes(existing_output_root) if existing_output_root.exists() else 0
            raw_total_bytes = sum(path.stat().st_size for path in raw_tiffs if path.exists())
            estimated_temp_bytes = max(raw_total_bytes, self.MIN_TEMP_FREE_BYTES)
            estimated_output_bytes = self.MIN_OUTPUT_FREE_BYTES
            if archive_existing and existing_output_size and not skip_existing:
                estimated_output_bytes += existing_output_size

            writable, writable_detail = self._is_path_writable(output_root)
            output_usage = shutil.disk_usage(output_root)
            output_free = output_usage.free

            errors: list[str] = []
            warnings: list[str] = []

            if not session.exists():
                errors.append(f"Session folder does not exist: {session}")
            if raw_dir is None or not raw_dir.exists():
                errors.append("No raw_* folder found.")
            elif not raw_tiffs:
                errors.append(f"No TIFF files found in raw folder: {raw_dir}")
            if not metadata_file.exists():
                errors.append(f"Missing session metadata: {metadata_file}")
            if not writable:
                errors.append(f"Output root is not writable: {output_root} ({writable_detail})")
            if temp_free < estimated_temp_bytes:
                errors.append(
                    "Not enough SSD temp space: "
                    f"need about {self._format_gb(estimated_temp_bytes)}, have {self._format_gb(temp_free)}."
                )
            if output_free < estimated_output_bytes:
                errors.append(
                    "Not enough HDD output space: "
                    f"need about {self._format_gb(estimated_output_bytes)}, have {self._format_gb(output_free)}."
                )
            if existing_output_root.exists() and skip_existing:
                warnings.append("Existing Suite2p outputs found. This session will be skipped with current batch settings.")
            if raw_total_bytes == 0 and raw_tiffs:
                warnings.append("Raw TIFF size estimate came out as 0 bytes; temp-space estimate may be understated.")

            status = "pass"
            if errors:
                status = "fail"
                blocking_failures += 1
            elif warnings:
                status = "warn"

            session_reports.append(
                {
                    "session": str(session),
                    "status": status,
                    "raw_dir": str(raw_dir) if raw_dir else "",
                    "raw_tiff_count": len(raw_tiffs),
                    "raw_tiff_bytes": raw_total_bytes,
                    "metadata_file": str(metadata_file),
                    "output_root": str(output_root),
                    "existing_output_root": str(existing_output_root) if existing_output_root.exists() else "",
                    "existing_output_bytes": existing_output_size,
                    "ssd_temp_root": str(temp_root),
                    "ssd_temp_free_bytes": temp_free,
                    "ssd_temp_required_bytes": estimated_temp_bytes,
                    "hdd_free_bytes": output_free,
                    "hdd_required_bytes": estimated_output_bytes,
                    "errors": errors,
                    "warnings": warnings,
                }
            )

        report = {
            "created_at": datetime.now().isoformat(),
            "session_count": len(sessions),
            "blocking_failures": blocking_failures,
            "all_clear": blocking_failures == 0,
            "ssd_temp_root": str(temp_root),
            "ssd_temp_free_bytes": temp_free,
            "sessions": session_reports,
        }
        return report

    def save_preflight_report(self, report: dict) -> Path:
        report_root = self._preflight_report_root()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = report_root / f"suite2p_batch_preflight_{ts}.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        self.log(f"Saved batch preflight report: {path}")
        return path

    def log_preflight_report(self, report: dict) -> None:
        total = report.get("session_count", 0)
        failures = report.get("blocking_failures", 0)
        self.log(f"Batch preflight checked {total} session(s). Blocking failures: {failures}.")
        for item in report.get("sessions", []):
            session_name = Path(item["session"]).name
            self.log(
                f"[Preflight] {session_name}: {item['status']} | "
                f"TIFFs={item['raw_tiff_count']} | "
                f"SSD need {self._format_gb(item['ssd_temp_required_bytes'])} / have {self._format_gb(item['ssd_temp_free_bytes'])} | "
                f"HDD need {self._format_gb(item['hdd_required_bytes'])} / have {self._format_gb(item['hdd_free_bytes'])}"
            )
            for warning in item.get("warnings", []):
                self.log(f"  warning: {warning}")
            for error in item.get("errors", []):
                self.log(f"  error: {error}")

    def _load_manifest(self, run_dir: Path) -> dict:
        manifest_path = run_dir / "session_manifest.json"
        if not manifest_path.exists():
            raise RuntimeError(f"Missing session manifest: {manifest_path}")
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def _analysis_root_for_session(self, session_root: Path) -> Path:
        return session_root / "analysis"

    def cleanup_run_temp(self, run_dir: str | Path) -> Path | None:
        path = Path(run_dir).expanduser().resolve()
        db_path = path / "suite2p_db.json"
        if not db_path.exists():
            self.log(f"Temp cleanup skipped: missing suite2p_db.json under {path}")
            return None
        db = json.loads(db_path.read_text(encoding="utf-8"))
        fast_disk = str(db.get("fast_disk", "")).strip()
        if not fast_disk:
            self.log(f"Temp cleanup skipped: no fast_disk recorded in {db_path}")
            return None
        fast_disk_path = Path(fast_disk).expanduser().resolve()
        if not fast_disk_path.exists():
            self.log(f"Temp cleanup skipped: fast_disk path does not exist: {fast_disk_path}")
            return fast_disk_path

        manifest = self._load_manifest(path)
        session_path = Path(str(manifest.get("session_path", ""))).expanduser().resolve()
        if not session_path.exists():
            raise RuntimeError(f"Temp retention failed: session path from manifest does not exist: {session_path}")

        retained_root = self._analysis_root_for_session(session_path) / "retained_temp" / path.name
        retained_root.mkdir(parents=True, exist_ok=True)

        moved_any = False
        for bin_path in sorted(fast_disk_path.rglob("*.bin")):
            relative = bin_path.relative_to(fast_disk_path)
            destination = retained_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                destination.unlink()
            shutil.move(str(bin_path), str(destination))
            moved_any = True

        if moved_any:
            self.log(f"Retained Suite2p temp bin files under session HDD: {retained_root}")
        else:
            self.log(f"Temp cleanup found no .bin files to retain under: {fast_disk_path}")

        shutil.rmtree(fast_disk_path)
        self.log(f"Removed Suite2p SSD temp folder after retention: {fast_disk_path}")
        return retained_root if moved_any else fast_disk_path

    def default_parameter_payload(self) -> dict:
        defaults = {
            "db": {
                "fs": 50.0,
                "tau": 0.4,
                "nplanes": 1,
                "nchannels": 1,
                "functional_chan": 1,
                "do_registration": True,
            },
            "ops": {
                "nonrigid": False,
                "batch_size": 500,
                "maxregshift": 0.1,
                "maxregshiftNR": 5,
                "smooth_sigma": 1.15,
                "snr_thresh": 1.2,
                "1Preg": False,
                "pre_smooth": 2,
                "spatial_taper": 40,
                "roidetect": True,
                "sparse_mode": True,
                "anatomical_only": 0,
                "denoise": 0,
                "diameter": 0,
                "threshold_scaling": 1.0,
                "spatial_scale": 0,
                "max_overlap": 0.75,
                "soma_crop": True,
                "cellprob_threshold": 0.0,
            },
        }
        if _suite2p_default_settings is not None:
            try:
                suite_defaults = _suite2p_default_settings()
                defaults["ops"].update(
                    {
                        "anatomical_only": suite_defaults.get("anatomical_only", defaults["ops"]["anatomical_only"]),
                        "denoise": suite_defaults.get("denoise", defaults["ops"]["denoise"]),
                        "batch_size": suite_defaults.get("batch_size", defaults["ops"]["batch_size"]),
                        "maxregshift": suite_defaults.get("maxregshift", defaults["ops"]["maxregshift"]),
                        "maxregshiftNR": suite_defaults.get("maxregshiftNR", defaults["ops"]["maxregshiftNR"]),
                        "smooth_sigma": suite_defaults.get("smooth_sigma", defaults["ops"]["smooth_sigma"]),
                        "snr_thresh": suite_defaults.get("snr_thresh", defaults["ops"]["snr_thresh"]),
                        "1Preg": suite_defaults.get("1Preg", defaults["ops"]["1Preg"]),
                        "pre_smooth": suite_defaults.get("pre_smooth", defaults["ops"]["pre_smooth"]),
                        "spatial_taper": suite_defaults.get("spatial_taper", defaults["ops"]["spatial_taper"]),
                        "diameter": suite_defaults.get("diameter", defaults["ops"]["diameter"]),
                        "threshold_scaling": suite_defaults.get("threshold_scaling", defaults["ops"]["threshold_scaling"]),
                        "spatial_scale": suite_defaults.get("spatial_scale", defaults["ops"]["spatial_scale"]),
                        "max_overlap": suite_defaults.get("max_overlap", defaults["ops"]["max_overlap"]),
                        "soma_crop": suite_defaults.get("soma_crop", defaults["ops"]["soma_crop"]),
                        "cellprob_threshold": suite_defaults.get("cellprob_threshold", defaults["ops"]["cellprob_threshold"]),
                    }
                )
            except Exception:
                pass
        return defaults

    def parameter_payload(self) -> dict:
        payload = self.default_parameter_payload()
        run_dir = self._require_run_dir()
        db_path = run_dir / "suite2p_db.json"
        ops_path = run_dir / "suite2p_ops_override.json"
        if db_path.exists():
            db = json.loads(db_path.read_text(encoding="utf-8"))
            for key in payload["db"]:
                if key in db:
                    payload["db"][key] = db[key]
        if ops_path.exists():
            ops = json.loads(ops_path.read_text(encoding="utf-8"))
            for key in payload["ops"]:
                if key in ops:
                    payload["ops"][key] = ops[key]
        return payload

    def save_parameter_payload(self, payload: dict, *, preserve_session_managed: bool = False) -> None:
        run_dir = self._require_run_dir()
        db_path = run_dir / "suite2p_db.json"
        ops_path = run_dir / "suite2p_ops_override.json"
        if not db_path.exists() or not ops_path.exists():
            raise RuntimeError("Run directory is missing suite2p_db.json or suite2p_ops_override.json.")

        db = json.loads(db_path.read_text(encoding="utf-8"))
        ops = json.loads(ops_path.read_text(encoding="utf-8"))

        db_payload = dict(payload.get("db", {}))
        if preserve_session_managed:
            for key in self.SESSION_MANAGED_DB_KEYS:
                db_payload.pop(key, None)

        for key, value in db_payload.items():
            db[key] = value
        for key, value in payload.get("ops", {}).items():
            ops[key] = value

        db_path.write_text(json.dumps(db, indent=2), encoding="utf-8")
        ops_path.write_text(json.dumps(ops, indent=2), encoding="utf-8")
        self.log(f"Saved parameters to: {db_path}")
        self.log(f"Saved parameters to: {ops_path}")
        if preserve_session_managed and self.SESSION_MANAGED_DB_KEYS:
            preserved = ", ".join(sorted(self.SESSION_MANAGED_DB_KEYS))
            self.log(f"Preserved session-managed metadata parameters: {preserved}")

    def save_parameter_preset(self, preset_path: str | Path, payload: dict) -> Path:
        path = Path(preset_path).expanduser()
        if not path.is_absolute():
            path = PRESETS_ROOT / path
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.log(f"Saved Suite2p parameter preset: {path}")
        return path

    def load_parameter_preset(self, preset_path: str | Path) -> dict:
        path = Path(preset_path).expanduser()
        if not path.is_absolute():
            path = PRESETS_ROOT / path
        if not path.exists():
            raise RuntimeError(f"Parameter preset does not exist: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.log(f"Loaded Suite2p parameter preset: {path}")
        return payload

    def _latest_run_for_session(self, session_path: Path) -> Path | None:
        candidates: list[Path] = []
        roots = [self.default_run_root(session_path), RUNS_ROOT]
        for root in roots:
            if not root.exists():
                continue
            for run_dir in root.iterdir():
                if not run_dir.is_dir():
                    continue
                manifest_path = run_dir / "session_manifest.json"
                if not manifest_path.exists():
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if manifest.get("session_path") == str(session_path):
                    candidates.append(run_dir)
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    def default_output_root(self, session_path: str | Path) -> Path:
        session = Path(session_path).expanduser().resolve()
        return session / "analysis" / "outputs"

    def archive_existing_outputs(self, session_path: str | Path) -> Path:
        session = Path(session_path).expanduser().resolve()
        current_root = self.default_output_root(session) / "suite2p"
        if not current_root.exists():
            raise RuntimeError(f"No existing Suite2p outputs found to archive under: {current_root}")
        archive_root = session / "analysis" / "archived_outputs"
        archive_root.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = archive_root / f"{session.name}_suite2p_{ts}"
        shutil.move(str(current_root), str(target))
        self.log(f"Archived existing Suite2p outputs to: {target}")
        return target

    def default_plane_dir(self, session_path: str | Path) -> Path:
        return self.default_output_root(session_path) / "suite2p" / "plane0"

    def _latest_output_for_session(self, session_path: Path) -> Path | None:
        candidates: list[Path] = []

        current_plane = self.default_plane_dir(session_path)
        if (current_plane / "ops.npy").exists():
            candidates.append(current_plane)

        legacy_root = session_path / "analysis" / "suite2p_sandbox"
        if legacy_root.exists():
            for run_dir in legacy_root.iterdir():
                if not run_dir.is_dir():
                    continue
                plane_dir = run_dir / "outputs" / "suite2p" / "plane0"
                if (plane_dir / "ops.npy").exists():
                    candidates.append(plane_dir)

        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    def load_latest_from_session(self, session_path: str) -> Path:
        session = Path(session_path).expanduser().resolve()
        if not session.exists():
            raise RuntimeError(f"Session path does not exist: {session}")

        latest_run = self._latest_run_for_session(session)
        latest_output = self._latest_output_for_session(session)

        if latest_run is not None:
            try:
                db = json.loads((latest_run / "suite2p_db.json").read_text(encoding="utf-8"))
                save_path0 = db.get("save_path0")
                if save_path0:
                    plane_dir = Path(str(save_path0)) / "suite2p" / "plane0"
                    if plane_dir.exists():
                        self.load_run_dir(str(latest_run))
                        self.state.plane_dir = plane_dir
                        self.log(f"Loaded latest Suite2p session result from run dir: {latest_run}")
                        self.set_status("Loaded latest Suite2p result from session.")
                        return plane_dir
            except Exception:
                pass

        if latest_output is not None:
            plane_dir = self.load_output_dir(str(latest_output))
            self.state.session_path = session
            self.log(f"Loaded latest Suite2p session result from outputs: {latest_output}")
            self.set_status("Loaded latest Suite2p result from session.")
            return plane_dir

        raise RuntimeError(f"No Suite2p outputs were found under session: {session}")

    def load_run_dir(self, run_dir: str) -> Path:
        path = Path(run_dir).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"Run directory does not exist: {path}")
        self.state.run_dir = path
        self.state.plane_dir = None
        self.state.snapshot_dir = None
        try:
            manifest = self._load_manifest(path)
            session_path = manifest.get("session_path")
            self.state.session_path = Path(session_path) if session_path else None
        except Exception:
            pass
        self.state.run_name = path.name
        self.log(f"Loaded run dir: {path}")
        self.set_status("Suite2p run loaded.")
        return path

    def load_review_target(self, target_path: str) -> Path:
        path = Path(target_path).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"Selected path does not exist: {path}")

        if path.is_dir() and (path / "session_manifest.json").exists():
            return self.load_run_dir(str(path))

        try:
            return self.load_output_dir(str(path))
        except Exception:
            pass

        session_markers = [
            path.name.lower().startswith("session_"),
            (path / "analysis" / "outputs").exists(),
            (path / "suite2p_runs").exists(),
        ]
        if any(session_markers):
            return self.load_latest_from_session(str(path))

        raise RuntimeError(
            "Could not determine how to load the selected path. "
            "Choose a Session folder, a prepared Suite2p run directory, or a Suite2p output folder."
        )

    def load_output_dir(self, output_dir: str) -> Path:
        path = Path(output_dir).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"Output directory does not exist: {path}")

        plane_dir = self._normalize_plane_dir(path)
        self.state.plane_dir = plane_dir
        self.state.snapshot_dir = None
        inferred_run_dir = self._infer_run_dir_from_plane_dir(plane_dir)
        self.state.run_dir = inferred_run_dir
        inferred_session = self._infer_session_from_path(plane_dir)
        if inferred_session is not None:
            self.state.session_path = inferred_session
        if inferred_run_dir is not None:
            try:
                manifest = self._load_manifest(inferred_run_dir)
                session_path = manifest.get("session_path")
                self.state.session_path = Path(session_path) if session_path else None
            except Exception:
                pass
            self.state.run_name = inferred_run_dir.name
        else:
            self.state.run_name = plane_dir.parent.parent.name
        self.log(f"Loaded Suite2p output dir: {plane_dir}")
        self.set_status("Suite2p output folder loaded.")
        return plane_dir

    def run_suite2p(self, *, quiet_launch_note: bool = False) -> None:
        run_dir = self._require_run_dir()
        self.set_status("Running Suite2p...")
        cmd = [str(self._suite2p_python()), str(RUN_SCRIPT), "--run-dir", str(run_dir)]
        if quiet_launch_note:
            cmd.append("--quiet-launch-note")
        code = self._run_subprocess(cmd, cwd=RUN_SCRIPT.parent)
        if code != 0:
            self.set_status("Suite2p run failed.")
            raise RuntimeError(f"Suite2p run failed with exit code {code}.")
        self.set_status("Suite2p run finished.")

    def export_artifacts(self, preview_options: dict | None = None, only: str | None = None) -> None:
        if self.state.snapshot_dir is not None:
            raise RuntimeError(
                "Artifact export is disabled while a curation snapshot is loaded for review. "
                "Promote the snapshot to active plane0 first if you want refreshed preview outputs."
            )
        run_dir = self._require_run_dir()
        self.set_status("Exporting Suite2p artifacts...")
        cmd = [str(self._suite2p_python()), str(EXPORT_SCRIPT), "--run-dir", str(run_dir)]
        if only:
            cmd.extend(["--only", only])
        if preview_options:
            option_map = {
                "start_frame": "--start-frame",
                "num_frames": "--num-frames",
                "fps": "--fps",
                "gain": "--gain",
                "q_min": "--q-min",
                "q_max": "--q-max",
            }
            for key, flag in option_map.items():
                value = preview_options.get(key)
                if value is None or value == "":
                    continue
                cmd.extend([flag, str(value)])
        code = self._run_subprocess(cmd, cwd=EXPORT_SCRIPT.parent)
        if code != 0:
            self.set_status("Suite2p export failed.")
            raise RuntimeError(f"Suite2p export failed with exit code {code}.")
        self.set_status("Suite2p artifacts exported.")

    def open_run_folder(self) -> None:
        run_dir = self._require_run_dir()
        self._open_path(run_dir)

    def open_plane_folder(self) -> None:
        self._open_path(self.plane_dir())

    def open_suite2p_gui(self) -> None:
        env_python = self._suite2p_python()
        stat_path = None
        try:
            plane_dir = self.plane_dir()
            candidate = plane_dir / "stat.npy"
            if candidate.exists():
                stat_path = candidate
        except Exception:
            stat_path = None

        if stat_path is not None:
            inline = (
                "from suite2p import gui; "
                f"gui.run(statfile=r'''{stat_path}''')"
            )
            cmd = [str(env_python), "-c", inline]
            subprocess.Popen(cmd, cwd=str(RUN_SCRIPT.parent))
            self.log(f"Launched Suite2p GUI with preloaded stat.npy: {stat_path}")
            return

        cmd = [str(env_python), "-m", "suite2p"]
        subprocess.Popen(cmd, cwd=str(RUN_SCRIPT.parent))
        self.log("Launched Suite2p GUI without a preloaded dataset.")

    def open_motion_preview(self) -> None:
        self._open_path(self.plane_dir() / "suite2p_motion_preview.mp4")

    def open_overlay_preview(self) -> None:
        self._open_path(self.plane_dir() / "suite2p_overlay_preview.mp4")

    def open_three_panel_preview(self) -> None:
        self._open_path(self.plane_dir() / "suite2p_three_panel_preview.mp4")

    def open_reconstruction_preview(self) -> None:
        self._open_path(self.plane_dir() / "suite2p_reconstruction_preview.mp4")

    def open_contour_figure(self) -> None:
        self._open_path(self.plane_dir() / "suite2p_contours.png")

    def open_accepted_contour_figure(self) -> None:
        self._open_path(self.plane_dir() / "suite2p_accepted_contours.png")

    def open_rejected_contour_figure(self) -> None:
        self._open_path(self.plane_dir() / "suite2p_rejected_contours.png")

    def open_mean_projection(self) -> None:
        self._open_path(self.plane_dir() / "suite2p_mean_projection.png")

    def open_max_projection(self) -> None:
        self._open_path(self.plane_dir() / "suite2p_max_projection.png")

    def open_correlation_image(self) -> None:
        self._open_path(self.plane_dir() / "suite2p_correlation_image.png")

    def open_static_overlay_image(self) -> None:
        self._open_path(self.plane_dir() / "suite2p_static_overlay.png")

    def open_accepted_fill_overlay_image(self) -> None:
        self._open_path(self.plane_dir() / "suite2p_accepted_fill_overlay.png")

    def open_trace_preview_figure(self) -> None:
        self._open_path(self.plane_dir() / "suite2p_trace_preview.png")

    def open_roi_size_summary(self) -> None:
        self._open_path(self.plane_dir() / "suite2p_roi_size_summary.png")

    def open_qc_report(self) -> None:
        self._open_path(self.plane_dir() / "suite2p_qc_summary.md")

    def summary_path(self) -> Path:
        return self.plane_dir() / "suite2p_run_summary.json"

    def runtime_path(self) -> Path:
        run_dir = self._require_run_dir()
        return run_dir / "suite2p_runtime.json"

    def log_path(self) -> Path:
        run_dir = self._require_run_dir()
        return run_dir / "suite2p_run.log"

    def plane_dir(self) -> Path:
        if self.state.plane_dir is not None and self.state.plane_dir.exists():
            return self.state.plane_dir
        run_dir = self._require_run_dir()
        candidate = run_dir / "outputs" / "suite2p" / "plane0"
        if candidate.exists():
            self.state.plane_dir = candidate
            return candidate
        manifest = self._load_manifest(run_dir)
        db = json.loads((run_dir / "suite2p_db.json").read_text(encoding="utf-8"))
        save_path0 = db.get("save_path0")
        if save_path0:
            candidate = Path(str(save_path0)) / "suite2p" / "plane0"
            if candidate.exists():
                self.state.plane_dir = candidate
                return candidate
        raise RuntimeError("Suite2p plane0 output folder was not found yet.")

    def active_plane_dir(self) -> Path:
        run_dir = self._require_run_dir()
        candidate = run_dir / "outputs" / "suite2p" / "plane0"
        if candidate.exists():
            return candidate
        manifest = self._load_manifest(run_dir)
        db = json.loads((run_dir / "suite2p_db.json").read_text(encoding="utf-8"))
        save_path0 = db.get("save_path0")
        if save_path0:
            candidate = Path(str(save_path0)) / "suite2p" / "plane0"
            if candidate.exists():
                return candidate
        raise RuntimeError("Active Suite2p plane0 output folder was not found.")

    def generated_artifacts(self) -> dict[str, Path]:
        plane_dir = self.plane_dir()
        artifacts = {
            "motion_preview": plane_dir / "suite2p_motion_preview.mp4",
            "overlay_preview": plane_dir / "suite2p_overlay_preview.mp4",
            "three_panel_preview": plane_dir / "suite2p_three_panel_preview.mp4",
            "reconstruction_preview": plane_dir / "suite2p_reconstruction_preview.mp4",
            "mean_projection": plane_dir / "suite2p_mean_projection.png",
            "max_projection": plane_dir / "suite2p_max_projection.png",
            "correlation_image": plane_dir / "suite2p_correlation_image.png",
            "static_overlay": plane_dir / "suite2p_static_overlay.png",
            "accepted_fill_overlay": plane_dir / "suite2p_accepted_fill_overlay.png",
            "contours": plane_dir / "suite2p_contours.png",
            "accepted_contours": plane_dir / "suite2p_accepted_contours.png",
            "rejected_contours": plane_dir / "suite2p_rejected_contours.png",
            "trace_preview": plane_dir / "suite2p_trace_preview.png",
            "roi_size_summary": plane_dir / "suite2p_roi_size_summary.png",
            "accepted_f_csv": plane_dir / "suite2p_accepted_F_traces.csv",
            "accepted_dff_csv": plane_dir / "suite2p_accepted_dff_traces.csv",
            "rejected_f_csv": plane_dir / "suite2p_rejected_F_traces.csv",
            "rejected_dff_csv": plane_dir / "suite2p_rejected_dff_traces.csv",
            "qc_report": plane_dir / "suite2p_qc_summary.md",
            "summary": plane_dir / "suite2p_run_summary.json",
        }
        if self.state.run_dir is not None:
            artifacts["runtime"] = self.runtime_path()
            artifacts["log"] = self.log_path()
        return artifacts

    def load_curation_payload(self) -> dict[str, object]:
        plane_dir = self.plane_dir()
        stat_path = plane_dir / "stat.npy"
        iscell_path = plane_dir / "iscell.npy"
        ops_path = plane_dir / "ops.npy"
        if not stat_path.exists() or not iscell_path.exists() or not ops_path.exists():
            raise RuntimeError(f"plane0 is missing one or more curation files under {plane_dir}")

        stat = np.load(stat_path, allow_pickle=True)
        iscell = np.load(iscell_path, allow_pickle=True)
        ops = np.load(ops_path, allow_pickle=True).item()

        traces: dict[str, np.ndarray | None] = {}
        for key, filename in (("F", "F.npy"), ("Fneu", "Fneu.npy"), ("spks", "spks.npy")):
            path = plane_dir / filename
            traces[key] = np.load(path, allow_pickle=True) if path.exists() else None
        if traces["F"] is not None:
            traces["corrected"] = self._corrected_traces(traces["F"], traces["Fneu"])
        else:
            traces["corrected"] = None
        suggestions = self._curation_suggestions(
            plane_dir=plane_dir,
            stat=stat,
            traces=traces,
            iscell=iscell,
        )
        assistance = self._curation_assistance(
            stat=stat,
            ops=ops,
            iscell=iscell,
            suggestions=suggestions,
        )
        return {
            "plane_dir": plane_dir,
            "stat": stat,
            "iscell": iscell,
            "ops": ops,
            "traces": traces,
            "suggestions": suggestions,
            "assistance": assistance,
        }

    def save_curation_iscell(self, accepted_mask: np.ndarray) -> Path:
        plane_dir = self.plane_dir()
        iscell_path = plane_dir / "iscell.npy"
        if not iscell_path.exists():
            raise RuntimeError(f"iscell.npy not found under {plane_dir}")

        existing = np.load(iscell_path, allow_pickle=True)
        accepted_mask = np.asarray(accepted_mask, dtype=np.float32).reshape(-1)
        if existing.ndim == 2:
            if existing.shape[0] != accepted_mask.shape[0]:
                raise RuntimeError("Accepted mask length does not match existing iscell.npy")
            updated = existing.copy()
            updated[:, 0] = accepted_mask
        else:
            if existing.shape[0] != accepted_mask.shape[0]:
                raise RuntimeError("Accepted mask length does not match existing iscell.npy")
            updated = accepted_mask

        np.save(iscell_path, updated)
        self._append_curation_history(
            plane_dir=plane_dir,
            stat=np.load(plane_dir / "stat.npy", allow_pickle=True),
            traces={
                "F": np.load(plane_dir / "F.npy", allow_pickle=True) if (plane_dir / "F.npy").exists() else None,
                "Fneu": np.load(plane_dir / "Fneu.npy", allow_pickle=True) if (plane_dir / "Fneu.npy").exists() else None,
                "spks": np.load(plane_dir / "spks.npy", allow_pickle=True) if (plane_dir / "spks.npy").exists() else None,
                "corrected": self._corrected_traces(
                    np.load(plane_dir / "F.npy", allow_pickle=True),
                    np.load(plane_dir / "Fneu.npy", allow_pickle=True) if (plane_dir / "Fneu.npy").exists() else None,
                ) if (plane_dir / "F.npy").exists() else None,
            },
            accepted_mask=accepted_mask,
        )
        self.log(f"Saved curated ROI labels: {iscell_path}")
        return iscell_path

    def _curation_history_path(self) -> Path:
        root = self.default_info_root(Path.cwd()) / "roi_curation"
        root.mkdir(parents=True, exist_ok=True)
        return root / "roi_curation_history.jsonl"

    def _roi_feature_rows(
        self,
        *,
        plane_dir: Path,
        stat: np.ndarray,
        traces: dict[str, np.ndarray | None],
        accepted_mask: np.ndarray | None = None,
    ) -> list[dict[str, object]]:
        corrected = traces.get("corrected")
        rows: list[dict[str, object]] = []
        session_path = ""
        if self.state.session_path is not None:
            session_path = str(self.state.session_path)
        elif self.state.run_dir is not None:
            try:
                session_path = str(self._session_root())
            except Exception:
                session_path = ""
        for idx, roi in enumerate(np.asarray(stat, dtype=object)):
            ypix = np.asarray(roi.get("ypix", []))
            feature_row: dict[str, object] = {
                "session_path": session_path,
                "plane_dir": str(plane_dir),
                "roi_index": int(idx),
                "area": float(ypix.size),
                "compact": float(roi.get("compact", 0.0) or 0.0),
                "radius": float(roi.get("radius", 0.0) or 0.0),
                "aspect_ratio": float(roi.get("aspect_ratio", 0.0) or 0.0),
                "footprint": float(roi.get("footprint", 0.0) or 0.0),
                "skew": float(roi.get("skew", 0.0) or 0.0),
                "std": float(roi.get("std", 0.0) or 0.0),
                "manual_roi": bool(roi.get("manual_roi", False)),
            }
            if corrected is not None and np.asarray(corrected).ndim == 2 and idx < np.asarray(corrected).shape[0]:
                trace = np.asarray(corrected[idx], dtype=float)
                feature_row.update(
                    {
                        "mean_dff": float(np.nanmean(trace)),
                        "peak_dff": float(np.nanmax(trace)),
                        "std_dff": float(np.nanstd(trace)),
                    }
                )
            else:
                feature_row.update({"mean_dff": 0.0, "peak_dff": 0.0, "std_dff": 0.0})
            if accepted_mask is not None and idx < accepted_mask.shape[0]:
                feature_row["accepted"] = bool(accepted_mask[idx] > 0.5)
            rows.append(feature_row)
        return rows

    def _append_curation_history(
        self,
        *,
        plane_dir: Path,
        stat: np.ndarray,
        traces: dict[str, np.ndarray | None],
        accepted_mask: np.ndarray,
    ) -> Path:
        history_path = self._curation_history_path()
        timestamp = datetime.now().isoformat()
        rows = self._roi_feature_rows(
            plane_dir=plane_dir,
            stat=stat,
            traces=traces,
            accepted_mask=np.asarray(accepted_mask, dtype=np.float32).reshape(-1),
        )
        with history_path.open("a", encoding="utf-8") as handle:
            for row in rows:
                payload = dict(row)
                payload["saved_at"] = timestamp
                handle.write(json.dumps(payload) + "\n")
        self.log(f"Updated curation learning history: {history_path}")
        return history_path

    def _curation_suggestions(
        self,
        *,
        plane_dir: Path,
        stat: np.ndarray,
        traces: dict[str, np.ndarray | None],
        iscell: np.ndarray,
    ) -> dict[str, np.ndarray]:
        history_path = self._curation_history_path()
        n_rois = len(np.asarray(stat, dtype=object))
        default = {
            "suggested_keep": np.full(n_rois, False, dtype=bool),
            "confidence": np.zeros(n_rois, dtype=np.float32),
            "available": np.full(n_rois, False, dtype=bool),
        }
        if not history_path.exists():
            return default

        feature_names = [
            "area", "compact", "radius", "aspect_ratio", "footprint",
            "skew", "std", "mean_dff", "peak_dff", "std_dff", "manual_roi",
        ]
        rows: list[dict[str, object]] = []
        try:
            with history_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    if str(row.get("plane_dir", "")) == str(plane_dir):
                        continue
                    if "accepted" not in row:
                        continue
                    rows.append(row)
        except Exception:
            return default

        keep_rows = [row for row in rows if bool(row.get("accepted"))]
        reject_rows = [row for row in rows if not bool(row.get("accepted"))]
        if len(keep_rows) < 5 or len(reject_rows) < 5:
            return default

        def _matrix(source_rows: list[dict[str, object]]) -> np.ndarray:
            return np.asarray(
                [
                    [float(row.get(name, 0.0) or 0.0) for name in feature_names]
                    for row in source_rows
                ],
                dtype=np.float32,
            )

        keep_X = _matrix(keep_rows)
        reject_X = _matrix(reject_rows)
        current_rows = self._roi_feature_rows(plane_dir=plane_dir, stat=stat, traces=traces)
        current_X = _matrix(current_rows)

        all_X = np.vstack([keep_X, reject_X])
        mu = all_X.mean(axis=0)
        sigma = all_X.std(axis=0)
        sigma[sigma < 1e-6] = 1.0

        keep_Z = (keep_X - mu) / sigma
        reject_Z = (reject_X - mu) / sigma
        current_Z = (current_X - mu) / sigma

        keep_center = keep_Z.mean(axis=0)
        reject_center = reject_Z.mean(axis=0)
        keep_dist = np.linalg.norm(current_Z - keep_center[np.newaxis, :], axis=1)
        reject_dist = np.linalg.norm(current_Z - reject_center[np.newaxis, :], axis=1)
        delta = reject_dist - keep_dist
        confidence = 1.0 / (1.0 + np.exp(-delta))

        return {
            "suggested_keep": delta > 0,
            "confidence": confidence.astype(np.float32),
            "available": np.full(n_rois, True, dtype=bool),
        }

    def _curation_assistance(
        self,
        *,
        stat: np.ndarray,
        ops: dict[str, object],
        iscell: np.ndarray,
        suggestions: dict[str, np.ndarray],
    ) -> dict[str, list[dict[str, object]]]:
        return {
            "merge_candidates": self._merge_candidate_suggestions(stat=stat, iscell=iscell),
            "soma_candidates": self._missing_soma_candidates(stat=stat, ops=ops, suggestions=suggestions),
        }

    def _merge_candidate_suggestions(self, *, stat: np.ndarray, iscell: np.ndarray) -> list[dict[str, object]]:
        stat_arr = np.asarray(stat, dtype=object)
        if iscell.ndim == 2:
            accepted = np.asarray(iscell[:, 0] > 0.5, dtype=bool)
        else:
            accepted = np.asarray(iscell > 0.5, dtype=bool)
        accepted_indices = [idx for idx in range(len(stat_arr)) if accepted[idx] and int(stat_arr[idx].get("inmerge", -1)) < 0]
        suggestions: list[dict[str, object]] = []
        for pos, idx_a in enumerate(accepted_indices):
            med_a = stat_arr[idx_a].get("med", (0.0, 0.0))
            rad_a = float(stat_arr[idx_a].get("radius", 0.0) or 0.0)
            for idx_b in accepted_indices[pos + 1:]:
                med_b = stat_arr[idx_b].get("med", (0.0, 0.0))
                rad_b = float(stat_arr[idx_b].get("radius", 0.0) or 0.0)
                dist = float(np.hypot(float(med_a[0]) - float(med_b[0]), float(med_a[1]) - float(med_b[1])))
                threshold = max(12.0, (rad_a + rad_b) * 2.5)
                if dist > threshold:
                    continue
                suggestions.append(
                    {
                        "pair": [int(idx_a), int(idx_b)],
                        "distance": dist,
                        "score": max(0.0, 1.0 - dist / max(threshold, 1e-6)),
                    }
                )
        suggestions.sort(key=lambda item: (-float(item["score"]), float(item["distance"])))
        return suggestions[:20]

    def _missing_soma_candidates(
        self,
        *,
        stat: np.ndarray,
        ops: dict[str, object],
        suggestions: dict[str, np.ndarray],
    ) -> list[dict[str, object]]:
        max_img = np.asarray(ops.get("max_proj"))
        if max_img.ndim != 2:
            return []
        Ly, Lx = max_img.shape
        exclusion = np.zeros((Ly, Lx), dtype=bool)
        for roi in np.asarray(stat, dtype=object):
            ypix = np.asarray(roi.get("ypix", []), dtype=np.int32)
            xpix = np.asarray(roi.get("xpix", []), dtype=np.int32)
            valid = (ypix >= 0) & (ypix < Ly) & (xpix >= 0) & (xpix < Lx)
            exclusion[ypix[valid], xpix[valid]] = True

        peak_window = 9
        local_max = maximum_filter(max_img, size=peak_window, mode="nearest")
        peak_mask = (max_img == local_max) & (~exclusion)
        threshold = float(np.percentile(max_img[~exclusion], 99.0)) if np.any(~exclusion) else float(np.max(max_img))
        peak_mask &= max_img >= threshold
        ys, xs = np.nonzero(peak_mask)
        if ys.size == 0:
            return []

        existing_centers = np.asarray(
            [[float(roi.get("med", (0.0, 0.0))[0]), float(roi.get("med", (0.0, 0.0))[1])] for roi in np.asarray(stat, dtype=object)],
            dtype=np.float32,
        ) if len(np.asarray(stat, dtype=object)) else np.zeros((0, 2), dtype=np.float32)
        base_diameter = ops.get("diameter", [12.0, 12.0])
        if isinstance(base_diameter, (int, float)):
            search_radius = float(base_diameter)
        else:
            try:
                search_radius = float(np.mean(base_diameter))
            except Exception:
                search_radius = 12.0

        candidates: list[dict[str, object]] = []
        for y, x in sorted(zip(ys, xs), key=lambda item: float(max_img[item[0], item[1]]), reverse=True):
            if existing_centers.size:
                dists = np.hypot(existing_centers[:, 0] - float(y), existing_centers[:, 1] - float(x))
                if np.any(dists < max(6.0, search_radius)):
                    continue
            candidates.append(
                {
                    "center_y": int(y),
                    "center_x": int(x),
                    "score": float(max_img[y, x]),
                }
            )
            if len(candidates) >= 20:
                break
        return candidates

    def find_registered_binary_path(self) -> Path:
        plane_dir = self.plane_dir()
        ops_path = plane_dir / "ops.npy"
        candidates: list[Path] = []
        if ops_path.exists():
            try:
                ops = np.load(ops_path, allow_pickle=True).item()
                for key in ("reg_file", "raw_file"):
                    value = str(ops.get(key, "") or "").strip()
                    if value:
                        candidates.append(Path(value).expanduser())
            except Exception:
                pass

        if self.state.run_dir is not None:
            run_dir = Path(self.state.run_dir)
            db_path = run_dir / "suite2p_db.json"
            if db_path.exists():
                try:
                    db = json.loads(db_path.read_text(encoding="utf-8"))
                    fast_disk = str(db.get("fast_disk", "") or "").strip()
                    if fast_disk:
                        fast_disk_path = Path(fast_disk).expanduser()
                        candidates.extend(
                            [
                                fast_disk_path / "suite2p" / "plane0" / "data.bin",
                                fast_disk_path / "data.bin",
                            ]
                        )
                except Exception:
                    pass

        try:
            session_root = self._session_root()
            retained_root = self._analysis_root_for_session(session_root) / "retained_temp"
            if retained_root.exists():
                retained_bins = sorted(retained_root.rglob("data.bin"), key=lambda p: p.stat().st_mtime, reverse=True)
                candidates.extend(retained_bins)
        except Exception:
            pass

        candidates.extend([plane_dir / "data.bin", plane_dir.parent / "data.bin"])
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.exists():
                return candidate.resolve()
        raise RuntimeError(
            "Registered binary `data.bin` was not found for this session. "
            "Manual ROI extraction needs the current run's binary or a retained temp binary under analysis\\retained_temp."
        )

    def append_manual_curation_roi(self, center_y: float, center_x: float, diameter: float) -> dict[str, object]:
        if self.state.snapshot_dir is not None:
            raise RuntimeError("Manual ROI add only applies to active plane0 outputs, not a loaded snapshot.")

        plane_dir = self.plane_dir()
        stat_path = plane_dir / "stat.npy"
        iscell_path = plane_dir / "iscell.npy"
        f_path = plane_dir / "F.npy"
        fneu_path = plane_dir / "Fneu.npy"
        spks_path = plane_dir / "spks.npy"
        ops_path = plane_dir / "ops.npy"
        required = [stat_path, iscell_path, f_path, ops_path]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise RuntimeError("Cannot add manual ROI because required files are missing:\n" + "\n".join(missing))

        stat = np.load(stat_path, allow_pickle=True)
        iscell = np.load(iscell_path, allow_pickle=True)
        F = np.load(f_path, allow_pickle=True)
        Fneu = np.load(fneu_path, allow_pickle=True) if fneu_path.exists() else None
        spks = np.load(spks_path, allow_pickle=True) if spks_path.exists() else None
        ops = np.load(ops_path, allow_pickle=True).item()

        Ly = int(ops.get("Ly", 0))
        Lx = int(ops.get("Lx", 0))
        if Ly <= 0 or Lx <= 0:
            raise RuntimeError("Could not determine image dimensions from ops.npy for manual ROI creation.")

        radius = max(2.0, float(diameter) / 2.0)
        yy, xx = np.meshgrid(np.arange(Ly), np.arange(Lx), indexing="ij")
        dist2 = (yy - float(center_y)) ** 2 + (xx - float(center_x)) ** 2
        roi_mask = dist2 <= radius**2
        if not np.any(roi_mask):
            raise RuntimeError("Manual ROI mask came out empty; try a larger diameter or click inside the image.")
        ypix, xpix = np.nonzero(roi_mask)
        lam = np.full(ypix.shape[0], 1.0 / max(1, ypix.shape[0]), dtype=np.float32)

        exclusion = np.zeros((Ly, Lx), dtype=bool)
        for roi in np.asarray(stat, dtype=object):
            ry = np.asarray(roi["ypix"], dtype=np.int32)
            rx = np.asarray(roi["xpix"], dtype=np.int32)
            valid = (ry >= 0) & (ry < Ly) & (rx >= 0) & (rx < Lx)
            exclusion[ry[valid], rx[valid]] = True
        exclusion[ypix, xpix] = True
        neuropil_mask = (dist2 >= (radius * 1.5) ** 2) & (dist2 <= (radius * 3.0) ** 2) & (~exclusion)
        neu_y, neu_x = np.nonzero(neuropil_mask)

        reg_file = self.find_registered_binary_path()
        frame_count = int(reg_file.stat().st_size // np.dtype(np.int16).itemsize // max(1, Ly * Lx))
        if frame_count <= 0:
            raise RuntimeError(f"Registered binary appears empty: {reg_file}")
        frames = np.memmap(reg_file, mode="r", dtype=np.int16, shape=(frame_count, Ly, Lx))
        F_new = np.asarray(frames[:, ypix, xpix], dtype=np.float32).mean(axis=1)
        if neu_y.size:
            Fneu_new = np.asarray(frames[:, neu_y, neu_x], dtype=np.float32).mean(axis=1)
        else:
            Fneu_new = np.zeros_like(F_new)

        neucoeff = float(ops.get("neucoeff", 0.7) or 0.7)
        dF = F_new - neucoeff * Fneu_new
        if _suite2p_oasis is not None:
            tau = float(ops.get("tau", 1.0) or 1.0)
            fs = float(ops.get("fs", 1.0) or 1.0)
            batch_size = int(ops.get("batch_size", 500) or 500)
            spks_new = _suite2p_oasis(
                F=np.asarray(dF[np.newaxis, :], dtype=np.float32),
                batch_size=max(1, batch_size),
                tau=max(1e-6, tau),
                fs=max(1e-6, fs),
            )
        else:
            spks_new = np.maximum(np.diff(dF, prepend=dF[0]), 0.0)[np.newaxis, :].astype(np.float32)

        centered_y = ypix.astype(np.float32) - float(center_y)
        centered_x = xpix.astype(np.float32) - float(center_x)
        distances = np.sqrt(centered_y**2 + centered_x**2)
        theta = np.linspace(0.0, 2.0 * math.pi, 40, endpoint=False)
        stat_new = {
            "ypix": ypix.astype(np.int32),
            "xpix": xpix.astype(np.int32),
            "lam": lam.astype(np.float32),
            "med": (float(center_y), float(center_x)),
            "npix": int(ypix.size),
            "soma_crop": np.ones(ypix.size, dtype=bool),
            "npix_soma": int(ypix.size),
            "mrs": float(np.mean(distances)) if distances.size else 0.0,
            "mrs0": max(1e-6, radius / 2.0),
            "compact": max(1.0, (float(np.mean(distances)) if distances.size else 0.0) / max(1e-6, radius / 2.0)),
            "radius": float(radius),
            "aspect_ratio": 1.0,
            "footprint": 0.0,
            "npix_norm": 1.0,
            "npix_norm_no_crop": 1.0,
            "overlap": np.zeros(ypix.size, dtype=bool),
            "skew": float(np.nan_to_num(scipy_stats.skew(dF), nan=0.0)),
            "std": float(np.std(dF)),
            "imerge": np.asarray([], dtype=np.int32),
            "inmerge": -1,
            "manual_roi": True,
            "ycirc": np.clip(np.round(float(center_y) + radius * np.sin(theta)).astype(np.int32), 0, Ly - 1),
            "xcirc": np.clip(np.round(float(center_x) + radius * np.cos(theta)).astype(np.int32), 0, Lx - 1),
            "yext": np.asarray([int(ypix.min()), int(ypix.max())], dtype=np.int32),
            "xext": np.asarray([int(xpix.min()), int(xpix.max())], dtype=np.int32),
        }
        if "iplane" in np.asarray(stat, dtype=object)[0]:
            stat_new["iplane"] = np.asarray(stat, dtype=object)[0]["iplane"]

        updated_stat = np.concatenate((stat, np.array([stat_new], dtype=object)), axis=0)
        if iscell.ndim == 2:
            new_iscell_row = np.zeros((1, iscell.shape[1]), dtype=iscell.dtype)
            new_iscell_row[0, 0] = 1.0
            if iscell.shape[1] > 1:
                new_iscell_row[0, 1] = 1.0
            updated_iscell = np.concatenate((iscell, new_iscell_row), axis=0)
        else:
            updated_iscell = np.concatenate((iscell, np.asarray([1.0], dtype=iscell.dtype)), axis=0)
        updated_F = np.concatenate((F, F_new[np.newaxis, :]), axis=0)
        updated_Fneu = np.concatenate((Fneu, Fneu_new[np.newaxis, :]), axis=0) if Fneu is not None else None
        updated_spks = np.concatenate((spks, spks_new), axis=0) if spks is not None else spks_new

        np.save(stat_path, updated_stat)
        np.save(iscell_path, updated_iscell)
        np.save(f_path, updated_F)
        if updated_Fneu is not None:
            np.save(fneu_path, updated_Fneu)
        if updated_spks is not None:
            np.save(spks_path, updated_spks)

        new_index = len(updated_stat) - 1
        self.log(f"Added manual Suite2p ROI {new_index} at ({center_y:.1f}, {center_x:.1f}) using {reg_file}")
        return {
            "plane_dir": plane_dir,
            "new_index": new_index,
            "center_y": float(center_y),
            "center_x": float(center_x),
            "diameter": float(diameter),
            "binary_path": reg_file,
        }

    def delete_manual_curation_rois(self, roi_indices: list[int]) -> dict[str, object]:
        if self.state.snapshot_dir is not None:
            raise RuntimeError("Manual ROI deletion only applies to active plane0 outputs, not a loaded snapshot.")

        unique_indices = sorted({int(idx) for idx in roi_indices})
        if not unique_indices:
            raise ValueError("Select at least one manual ROI to delete.")

        plane_dir = self.plane_dir()
        stat_path = plane_dir / "stat.npy"
        iscell_path = plane_dir / "iscell.npy"
        f_path = plane_dir / "F.npy"
        fneu_path = plane_dir / "Fneu.npy"
        spks_path = plane_dir / "spks.npy"
        required = [stat_path, iscell_path, f_path]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise RuntimeError("Cannot delete manual ROIs because required files are missing:\n" + "\n".join(missing))

        stat = np.load(stat_path, allow_pickle=True)
        iscell = np.load(iscell_path, allow_pickle=True)
        F = np.load(f_path, allow_pickle=True)
        Fneu = np.load(fneu_path, allow_pickle=True) if fneu_path.exists() else None
        spks = np.load(spks_path, allow_pickle=True) if spks_path.exists() else None

        n_rois = len(stat)
        if any(idx < 0 or idx >= n_rois for idx in unique_indices):
            raise ValueError("One or more selected ROIs are out of range for this plane.")

        non_manual = [idx for idx in unique_indices if not bool(stat[idx].get("manual_roi", False))]
        if non_manual:
            joined = ", ".join(str(idx) for idx in non_manual[:6])
            raise RuntimeError(
                "Delete Selected Manual ROI only works on manually added ROIs. "
                f"These selected ROI(s) are not manual: {joined}"
            )

        keep_mask = np.ones(n_rois, dtype=bool)
        keep_mask[unique_indices] = False
        remaining_count = int(np.count_nonzero(keep_mask))
        if remaining_count <= 0:
            raise RuntimeError("Refusing to delete every ROI in the plane. Leave at least one ROI and use reject if needed.")

        index_map = {old_idx: new_idx for new_idx, old_idx in enumerate(np.flatnonzero(keep_mask))}

        updated_stat = np.asarray(stat[keep_mask], dtype=object)
        for roi in updated_stat:
            inmerge = int(roi.get("inmerge", -1) or -1)
            roi["inmerge"] = int(index_map.get(inmerge, -1))
            imerge = np.asarray(roi.get("imerge", []), dtype=np.int32).reshape(-1)
            if imerge.size:
                remapped = [index_map[idx] for idx in imerge.tolist() if idx in index_map]
                roi["imerge"] = np.asarray(remapped, dtype=np.int32)
            else:
                roi["imerge"] = np.asarray([], dtype=np.int32)

        updated_iscell = np.asarray(iscell[keep_mask])
        updated_F = np.asarray(F[keep_mask])
        updated_Fneu = np.asarray(Fneu[keep_mask]) if Fneu is not None else None
        updated_spks = np.asarray(spks[keep_mask]) if spks is not None else None

        np.save(stat_path, updated_stat)
        np.save(iscell_path, updated_iscell)
        np.save(f_path, updated_F)
        if updated_Fneu is not None:
            np.save(fneu_path, updated_Fneu)
        if updated_spks is not None:
            np.save(spks_path, updated_spks)

        deleted_text = ", ".join(str(idx) for idx in unique_indices)
        self.log(f"Deleted manual Suite2p ROI(s): {deleted_text}")
        return {
            "plane_dir": plane_dir,
            "deleted_indices": unique_indices,
            "remaining_count": remaining_count,
        }

    def merge_curation_rois(self, roi_indices: list[int]) -> dict[str, object]:
        unique_indices = sorted({int(idx) for idx in roi_indices})
        if len(unique_indices) < 2:
            raise ValueError("Select at least two ROIs to merge.")

        plane_dir = self.plane_dir()
        stat_path = plane_dir / "stat.npy"
        iscell_path = plane_dir / "iscell.npy"
        f_path = plane_dir / "F.npy"
        fneu_path = plane_dir / "Fneu.npy"
        spks_path = plane_dir / "spks.npy"
        ops_path = plane_dir / "ops.npy"
        required = [stat_path, iscell_path, f_path, ops_path]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise RuntimeError("Cannot merge ROIs because required files are missing:\n" + "\n".join(missing))

        stat = np.load(stat_path, allow_pickle=True)
        iscell = np.load(iscell_path, allow_pickle=True)
        F = np.load(f_path, allow_pickle=True)
        Fneu = np.load(fneu_path, allow_pickle=True) if fneu_path.exists() else None
        spks = np.load(spks_path, allow_pickle=True) if spks_path.exists() else None
        ops = np.load(ops_path, allow_pickle=True).item()

        if any(idx < 0 or idx >= len(stat) for idx in unique_indices):
            raise ValueError("One or more selected ROIs are out of range for this plane.")

        ypix = np.concatenate([np.asarray(stat[idx]["ypix"], dtype=np.int32) for idx in unique_indices])
        xpix = np.concatenate([np.asarray(stat[idx]["xpix"], dtype=np.int32) for idx in unique_indices])
        lam = np.concatenate([np.asarray(stat[idx]["lam"], dtype=np.float32) for idx in unique_indices])
        if ypix.size == 0 or xpix.size == 0:
            raise RuntimeError("The selected ROIs do not contain any pixels to merge.")

        ipix = np.column_stack((ypix, xpix))
        _, keep_idx = np.unique(ipix, return_index=True, axis=0)
        keep_idx = np.sort(keep_idx)
        ypix = ypix[keep_idx]
        xpix = xpix[keep_idx]
        lam = lam[keep_idx]
        lam_sum = float(np.sum(lam))
        if lam_sum <= 0:
            lam = np.full_like(lam, 1.0 / max(1, lam.size), dtype=np.float32)
        else:
            lam = (lam / lam_sum).astype(np.float32)

        merged_stat = {
            "ypix": ypix,
            "xpix": xpix,
            "lam": lam,
            "imerge": np.asarray(unique_indices, dtype=np.int32),
            "inmerge": -1,
        }
        first_roi = stat[unique_indices[0]]
        if "iplane" in first_roi:
            merged_stat["iplane"] = first_roi["iplane"]
        if "chan2_prob" in first_roi:
            merged_stat["chan2_prob"] = -1

        merged_F = np.asarray(F[unique_indices], dtype=np.float32).mean(axis=0)
        merged_Fneu = np.asarray(Fneu[unique_indices], dtype=np.float32).mean(axis=0) if Fneu is not None else None

        batch_size = int(ops.get("batch_size", 500) or 500)
        tau = float(ops.get("tau", 1.0) or 1.0)
        fs = float(ops.get("fs", 1.0) or 1.0)
        neucoeff = float(ops.get("neucoeff", 0.7) or 0.7)
        dF = merged_F - neucoeff * merged_Fneu if merged_Fneu is not None else merged_F.copy()
        merged_stat["med"] = (float(np.median(ypix)), float(np.median(xpix)))
        merged_stat["npix"] = int(ypix.size)
        merged_stat["soma_crop"] = np.ones(ypix.size, dtype=bool)
        merged_stat["npix_soma"] = int(ypix.size)
        centered_y = ypix.astype(np.float32) - float(merged_stat["med"][0])
        centered_x = xpix.astype(np.float32) - float(merged_stat["med"][1])
        if centered_y.size:
            cov = np.cov(np.vstack((centered_y, centered_x)))
            evals = np.sort(np.linalg.eigvalsh(cov))
            major = float(np.sqrt(max(evals[-1], 1e-6)))
            minor = float(np.sqrt(max(evals[0], 1e-6)))
        else:
            major = minor = 1.0
        merged_stat["radius"] = max(1.0, float(np.sqrt(ypix.size / np.pi)))
        merged_stat["aspect_ratio"] = 2.0 * major / max(0.01, major + minor)
        distances = np.sqrt(centered_y**2 + centered_x**2)
        merged_stat["mrs"] = float(np.mean(distances)) if distances.size else 0.0
        merged_stat["mrs0"] = max(1e-6, float(merged_stat["radius"]) / 2.0)
        merged_stat["compact"] = max(1.0, float(merged_stat["mrs"]) / max(1e-6, float(merged_stat["mrs0"])))
        merged_stat["footprint"] = float(np.mean([float(np.asarray(stat[idx].get("footprint", 0.0)).mean()) for idx in unique_indices]))
        merged_stat["npix_norm"] = 1.0
        merged_stat["npix_norm_no_crop"] = 1.0
        merged_stat["overlap"] = np.zeros(ypix.size, dtype=bool)
        merged_stat["skew"] = float(np.nan_to_num(scipy_stats.skew(dF), nan=0.0))
        merged_stat["std"] = float(np.std(dF))
        theta = np.linspace(0.0, 2.0 * np.pi, 40, endpoint=False)
        merged_stat["ycirc"] = np.clip(np.round(merged_stat["med"][0] + merged_stat["radius"] * np.sin(theta)).astype(np.int32), 0, int(ops.get("Ly", 1)) - 1)
        merged_stat["xcirc"] = np.clip(np.round(merged_stat["med"][1] + merged_stat["radius"] * np.cos(theta)).astype(np.int32), 0, int(ops.get("Lx", 1)) - 1)
        merged_stat["yext"] = np.asarray([int(ypix.min()), int(ypix.max())], dtype=np.int32)
        merged_stat["xext"] = np.asarray([int(xpix.min()), int(xpix.max())], dtype=np.int32)

        merged_spks = None
        if spks is not None:
            if _suite2p_oasis is not None:
                merged_spks = _suite2p_oasis(
                    F=np.asarray(dF[np.newaxis, :], dtype=np.float32),
                    batch_size=max(1, batch_size),
                    tau=max(1e-6, tau),
                    fs=max(1e-6, fs),
                )
            else:
                merged_spks = np.asarray(spks[unique_indices], dtype=np.float32).mean(axis=0, keepdims=True)

        updated_stat = np.concatenate((stat, np.array([merged_stat], dtype=object)), axis=0)
        new_index = len(updated_stat) - 1
        for idx in unique_indices:
            updated_stat[idx]["inmerge"] = new_index

        updated_F = np.concatenate((F, merged_F[np.newaxis, :]), axis=0)
        updated_Fneu = np.concatenate((Fneu, merged_Fneu[np.newaxis, :]), axis=0) if merged_Fneu is not None else None
        updated_spks = np.concatenate((spks, merged_spks), axis=0) if spks is not None and merged_spks is not None else spks

        if iscell.ndim == 2:
            updated_iscell = np.concatenate((iscell, np.asarray(iscell[unique_indices[0]][np.newaxis, :], dtype=iscell.dtype)), axis=0)
            updated_iscell[unique_indices, 0] = 0.0
            updated_iscell[new_index, 0] = 1.0 if np.any(iscell[unique_indices, 0] > 0.5) else 0.0
        else:
            updated_iscell = np.concatenate((iscell, np.asarray([iscell[unique_indices[0]]], dtype=iscell.dtype)), axis=0)
            updated_iscell[unique_indices] = 0.0
            updated_iscell[new_index] = 1.0 if np.any(iscell[unique_indices] > 0.5) else 0.0

        np.save(stat_path, updated_stat)
        np.save(iscell_path, updated_iscell)
        np.save(f_path, updated_F)
        if updated_Fneu is not None:
            np.save(fneu_path, updated_Fneu)
        if updated_spks is not None:
            np.save(spks_path, updated_spks)

        self.log(
            "Merged Suite2p ROIs "
            f"{unique_indices} into ROI {new_index} under {plane_dir}"
        )
        return {
            "plane_dir": plane_dir,
            "merged_indices": unique_indices,
            "new_index": new_index,
            "accepted": bool(updated_iscell[new_index, 0] > 0.5) if np.ndim(updated_iscell) == 2 else bool(updated_iscell[new_index] > 0.5),
        }

    def curation_status_path(self, session_path: str | Path | None = None) -> Path:
        session = Path(session_path).expanduser().resolve() if session_path is not None else self._session_root().resolve()
        return self._analysis_root_for_session(session) / "suite2p_curation_status.json"

    def load_session_curation_status(self, session_path: str | Path | None = None) -> dict[str, object]:
        status_path = self.curation_status_path(session_path)
        if not status_path.exists():
            return {"status": "not_started", "updated_at": "", "notes": ""}
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            return {"status": "not_started", "updated_at": "", "notes": ""}
        return {
            "status": str(payload.get("status", "not_started") or "not_started"),
            "updated_at": str(payload.get("updated_at", "") or ""),
            "notes": str(payload.get("notes", "") or ""),
        }

    def save_session_curation_status(self, status: str, *, notes: str = "", session_path: str | Path | None = None) -> Path:
        status_path = self.curation_status_path(session_path)
        status_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": str(status).strip() or "not_started",
            "updated_at": datetime.now().isoformat(),
            "notes": str(notes).strip(),
        }
        status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.log(f"Saved session curation status: {status_path}")
        return status_path

    def _summary_status_context(self, session_path: str | Path | None = None) -> dict[str, object]:
        status_payload = self.load_session_curation_status(session_path)
        status = str(status_payload.get("status", "not_started") or "not_started").strip() or "not_started"
        notes = str(status_payload.get("notes", "") or "").strip()
        excluded = status == "no_soma"
        reason = "no_soma" if excluded else ""
        return {
            "curation_status": status,
            "excluded_from_analysis": excluded,
            "analysis_exclusion_reason": reason,
            "curation_notes": notes,
        }

    def _session_root(self) -> Path:
        if self.state.session_path is not None:
            return self.state.session_path
        run_dir = self._require_run_dir()
        manifest = self._load_manifest(run_dir)
        session_path = manifest.get("session_path")
        if not session_path:
            raise RuntimeError("Session path could not be resolved from the current Suite2p run.")
        session = Path(session_path)
        self.state.session_path = session
        return session

    def _session_metadata_payload(self) -> dict[str, object]:
        meta_path = self._session_root() / "metadata" / "session_metadata.json"
        if not meta_path.exists():
            return {}
        return json.loads(meta_path.read_text(encoding="utf-8"))

    def _stim_events_payload(self) -> dict[str, object]:
        stim_path = self._session_root() / "metadata" / "stim_events.json"
        if not stim_path.exists():
            return {"events": []}
        return json.loads(stim_path.read_text(encoding="utf-8"))

    def _usable_stim_events(self, fr: float) -> list[dict[str, object]]:
        payload = self._stim_events_payload()
        events = payload.get("events", [])
        usable: list[dict[str, object]] = []
        for idx, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            event_time_s = event.get("event_time_s", event.get("time_s", event.get("timestamp_s")))
            if event_time_s is None:
                continue
            try:
                event_time_s = float(event_time_s)
            except Exception:
                continue
            usable.append(
                {
                    "event_id": event.get("event_id", idx),
                    "event_label": event.get("event_label", event.get("label", f"event_{idx+1}")),
                    "event_type": event.get("event_type", event.get("type", "")),
                    "event_time_s": event_time_s,
                    "event_duration_s": event.get("event_duration_s", event.get("duration_s", "")),
                    "event_concentration_mM": event.get("event_concentration_mM", event.get("concentration_mM", "")),
                    "event_notes": event.get("event_notes", event.get("notes", "")),
                }
            )
        return usable

    def _analysis_exports_root(self) -> Path:
        root = self.default_output_root(self._session_root()) / "post_run_analysis"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _snapshot_runtime_state(self) -> dict[str, object]:
        return {
            "session_path": self.state.session_path,
            "run_dir": self.state.run_dir,
            "plane_dir": self.state.plane_dir,
            "snapshot_dir": self.state.snapshot_dir,
            "run_name": self.state.run_name,
            "acquisition_metadata": self.state.acquisition_metadata.copy() if isinstance(self.state.acquisition_metadata, dict) else self.state.acquisition_metadata,
        }

    def _restore_runtime_state(self, snapshot: dict[str, object]) -> None:
        self.state.session_path = snapshot.get("session_path")  # type: ignore[assignment]
        self.state.run_dir = snapshot.get("run_dir")  # type: ignore[assignment]
        self.state.plane_dir = snapshot.get("plane_dir")  # type: ignore[assignment]
        self.state.snapshot_dir = snapshot.get("snapshot_dir")  # type: ignore[assignment]
        self.state.run_name = str(snapshot.get("run_name", "") or "")
        self.state.acquisition_metadata = snapshot.get("acquisition_metadata")  # type: ignore[assignment]

    def _curation_snapshots_root(self) -> Path:
        root = self.default_output_root(self._session_root()) / "suite2p_curated_snapshots"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def list_curation_snapshots(self) -> list[Path]:
        try:
            root = self._curation_snapshots_root()
        except Exception:
            return []
        snapshots = [path for path in root.iterdir() if path.is_dir() and path.name.startswith("curated_snapshot_")]
        snapshots.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return snapshots

    def load_curation_snapshot(self, snapshot_dir: str | Path) -> Path:
        path = Path(snapshot_dir).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise RuntimeError(f"Curation snapshot does not exist: {path}")
        required = [path / "ops.npy", path / "stat.npy", path / "iscell.npy", path / "F.npy"]
        missing = [str(item) for item in required if not item.exists()]
        if missing:
            raise RuntimeError("Selected snapshot is missing required Suite2p files:\n" + "\n".join(missing))
        inferred_session = self._infer_session_from_path(path)
        if inferred_session is not None:
            self.state.session_path = inferred_session
        self.state.snapshot_dir = path
        self.state.plane_dir = path
        self.log(f"Loaded Suite2p curation snapshot for review: {path}")
        self.set_status("Loaded Suite2p curation snapshot.")
        return path

    def use_active_plane0(self) -> Path:
        plane_dir = self.active_plane_dir()
        self.state.snapshot_dir = None
        self.state.plane_dir = plane_dir
        self.log(f"Switched Post-Run source back to active plane0: {plane_dir}")
        self.set_status("Using active Suite2p plane0 outputs.")
        return plane_dir

    def promote_curation_snapshot(self, snapshot_dir: str | Path | None = None) -> Path:
        if snapshot_dir is None:
            snapshot_path = self.state.snapshot_dir
        else:
            snapshot_path = Path(snapshot_dir).expanduser().resolve()
        if snapshot_path is None:
            raise RuntimeError("No curation snapshot is currently selected.")
        if not snapshot_path.exists():
            raise RuntimeError(f"Curation snapshot does not exist: {snapshot_path}")

        active_plane = self.active_plane_dir()
        backup_root = self._curation_snapshots_root()
        backup_dir = backup_root / f"pre_promote_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        backed_up_files: list[str] = []
        for item in active_plane.iterdir():
            if not item.is_file():
                continue
            shutil.copy2(item, backup_dir / item.name)
            backed_up_files.append(item.name)
        backup_manifest = {
            "created_at": datetime.now().isoformat(),
            "session_path": str(self._session_root()),
            "run_dir": str(self._require_run_dir()),
            "active_plane_dir": str(active_plane),
            "backup_dir": str(backup_dir),
            "source_snapshot_dir": str(snapshot_path),
            "copied_files": sorted(backed_up_files),
        }
        (backup_dir / "pre_promote_backup_manifest.json").write_text(json.dumps(backup_manifest, indent=2), encoding="utf-8")

        promoted_files: list[str] = []
        for item in snapshot_path.iterdir():
            if not item.is_file():
                continue
            shutil.copy2(item, active_plane / item.name)
            promoted_files.append(item.name)

        self.state.snapshot_dir = None
        self.state.plane_dir = active_plane
        self.log(f"Promoted Suite2p curation snapshot to active plane0: {snapshot_path}")
        self.log(f"Backed up prior active plane0 to: {backup_dir}")
        self.set_status("Promoted curation snapshot to active Suite2p outputs.")
        return active_plane

    def _summary_exports_root(self) -> Path:
        root = self._analysis_exports_root() / "summaries"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _project_exports_root(self, root: str | Path | None = None) -> Path:
        if root is None:
            root_path = self._session_root().parent
        else:
            root_path = Path(root).expanduser().resolve()
        export_root = root_path / "suite2p_project_summary"
        export_root.mkdir(parents=True, exist_ok=True)
        return export_root

    def _suite2p_data(self) -> dict[str, object]:
        plane_dir = self.plane_dir()
        ops = np.load(plane_dir / "ops.npy", allow_pickle=True).item()
        stat = np.load(plane_dir / "stat.npy", allow_pickle=True)
        F = np.load(plane_dir / "F.npy", allow_pickle=True)
        Fneu = np.load(plane_dir / "Fneu.npy", allow_pickle=True) if (plane_dir / "Fneu.npy").exists() else None
        spks = np.load(plane_dir / "spks.npy", allow_pickle=True) if (plane_dir / "spks.npy").exists() else None
        iscell = None
        iscell_path = plane_dir / "iscell.npy"
        if iscell_path.exists():
            raw = np.load(iscell_path, allow_pickle=True)
            if raw.ndim >= 2:
                iscell = raw[:, 0].astype(bool)
            else:
                iscell = raw.astype(bool)
        return {"plane_dir": plane_dir, "ops": ops, "stat": stat, "F": F, "Fneu": Fneu, "spks": spks, "iscell": iscell}

    def _corrected_traces(self, F: np.ndarray, Fneu: np.ndarray | None) -> np.ndarray:
        corrected = np.asarray(F, dtype=float)
        if Fneu is not None:
            corrected = corrected - 0.7 * np.asarray(Fneu, dtype=float)
        baselines = np.percentile(corrected, 20, axis=1, keepdims=True)
        baselines = np.maximum(baselines, 1e-3)
        return (corrected - baselines) / baselines

    def _roi_areas(self, stat: np.ndarray) -> np.ndarray:
        return np.asarray([len(np.asarray(roi["ypix"])) for roi in stat], dtype=float)

    def export_session_summary_csv(self) -> Path:
        data = self._suite2p_data()
        meta = self._session_metadata_payload()
        status_context = self._summary_status_context()
        ops = data["ops"]
        stat = data["stat"]
        F = data["F"]
        Fneu = data["Fneu"]
        iscell = data["iscell"]
        fr = float(ops.get("fs", 1.0))
        traces = self._corrected_traces(F, Fneu)
        areas = self._roi_areas(stat)
        output_path = self._summary_exports_root() / "suite2p_session_summary.csv"
        fieldnames = [
            "project_name", "condition_subtype", "condition_type", "animal_id", "age_group", "genotype", "sex",
            "slice_number", "session_id", "acquired_frame_rate_hz", "summary_row_type", "cell_id", "accepted_component",
            "roi_area_pixels", "baseline_dff", "mean_dff", "peak_dff", "std_dff",
            "curation_status", "excluded_from_analysis", "analysis_exclusion_reason", "curation_notes",
            "source_model",
        ]
        rows: list[dict[str, object]] = []
        for idx in range(traces.shape[0]):
            trace = np.asarray(traces[idx], dtype=float)
            baseline = float(np.nanmedian(trace[: max(1, int(fr))]))
            rows.append(
                {
                    "project_name": meta.get("project_name", ""),
                    "condition_subtype": meta.get("condition_subtype", ""),
                    "condition_type": meta.get("condition_type", ""),
                    "animal_id": meta.get("animal_id", ""),
                    "age_group": meta.get("age_group", ""),
                    "genotype": meta.get("genotype", ""),
                    "sex": meta.get("sex", ""),
                    "slice_number": meta.get("slice_number", ""),
                    "session_id": meta.get("session_id", ""),
                    "acquired_frame_rate_hz": meta.get("acquired_frame_rate_hz", meta.get("frame_rate_hz", "")),
                    "summary_row_type": "roi",
                    "cell_id": idx,
                    "accepted_component": bool(iscell[idx]) if iscell is not None and idx < len(iscell) else "",
                    "roi_area_pixels": float(areas[idx]) if idx < len(areas) else "",
                    "baseline_dff": baseline,
                    "mean_dff": float(np.nanmean(trace)),
                    "peak_dff": float(np.nanmax(trace)),
                    "std_dff": float(np.nanstd(trace)),
                    "curation_status": status_context["curation_status"],
                    "excluded_from_analysis": bool(status_context["excluded_from_analysis"]),
                    "analysis_exclusion_reason": status_context["analysis_exclusion_reason"],
                    "curation_notes": status_context["curation_notes"],
                    "source_model": "suite2p",
                }
            )
        if not rows:
            rows.append(
                {
                    "project_name": meta.get("project_name", ""),
                    "condition_subtype": meta.get("condition_subtype", ""),
                    "condition_type": meta.get("condition_type", ""),
                    "animal_id": meta.get("animal_id", ""),
                    "age_group": meta.get("age_group", ""),
                    "genotype": meta.get("genotype", ""),
                    "sex": meta.get("sex", ""),
                    "slice_number": meta.get("slice_number", ""),
                    "session_id": meta.get("session_id", ""),
                    "acquired_frame_rate_hz": meta.get("acquired_frame_rate_hz", meta.get("frame_rate_hz", "")),
                    "summary_row_type": "session_status",
                    "cell_id": "",
                    "accepted_component": "",
                    "roi_area_pixels": "",
                    "baseline_dff": "",
                    "mean_dff": "",
                    "peak_dff": "",
                    "std_dff": "",
                    "curation_status": status_context["curation_status"],
                    "excluded_from_analysis": bool(status_context["excluded_from_analysis"]),
                    "analysis_exclusion_reason": status_context["analysis_exclusion_reason"],
                    "curation_notes": status_context["curation_notes"],
                    "source_model": "suite2p",
                }
            )
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        self.log(f"Saved session summary CSV: {output_path}")
        return output_path

    def export_event_summary_csv(self) -> Path:
        data = self._suite2p_data()
        meta = self._session_metadata_payload()
        status_context = self._summary_status_context()
        ops = data["ops"]
        F = data["F"]
        Fneu = data["Fneu"]
        iscell = data["iscell"]
        fr = float(ops.get("fs", 1.0))
        events = self._usable_stim_events(fr)
        if not events:
            raise RuntimeError("No usable stimulation events were found in stim_events.json for this session.")
        traces = self._corrected_traces(F, Fneu)
        pre_frames = max(1, int(round(5.0 * fr)))
        post_frames = max(1, int(round(30.0 * fr)))
        output_path = self._summary_exports_root() / "suite2p_event_summary.csv"
        fieldnames = [
            "project_name", "animal_id", "session_id", "summary_row_type", "cell_id", "accepted_component", "event_id",
            "event_label", "event_type", "event_time_s", "baseline_dff", "peak_dff", "peak_delta_dff",
            "auc_dff", "curation_status", "excluded_from_analysis", "analysis_exclusion_reason",
            "curation_notes", "source_model",
        ]
        rows: list[dict[str, object]] = []
        for event in events:
            event_frame = int(round(float(event["event_time_s"]) * fr))
            for idx in range(traces.shape[0]):
                trace = np.asarray(traces[idx], dtype=float)
                baseline_slice = trace[max(0, event_frame - pre_frames):event_frame]
                response_slice = trace[event_frame:min(trace.shape[0], event_frame + post_frames)]
                if baseline_slice.size == 0 or response_slice.size == 0:
                    continue
                baseline = float(np.nanmedian(baseline_slice))
                peak = float(np.nanmax(response_slice))
                delta = float(peak - baseline)
                auc = float(np.trapz(np.clip(response_slice - baseline, 0.0, None), dx=1.0 / max(fr, 1e-6)))
                rows.append(
                    {
                        "project_name": meta.get("project_name", ""),
                        "animal_id": meta.get("animal_id", ""),
                        "session_id": meta.get("session_id", ""),
                        "summary_row_type": "event",
                        "cell_id": idx,
                        "accepted_component": bool(iscell[idx]) if iscell is not None and idx < len(iscell) else "",
                        "event_id": event.get("event_id", ""),
                        "event_label": event.get("event_label", ""),
                        "event_type": event.get("event_type", ""),
                        "event_time_s": event.get("event_time_s", ""),
                        "baseline_dff": baseline,
                        "peak_dff": peak,
                        "peak_delta_dff": delta,
                        "auc_dff": auc,
                        "curation_status": status_context["curation_status"],
                        "excluded_from_analysis": bool(status_context["excluded_from_analysis"]),
                        "analysis_exclusion_reason": status_context["analysis_exclusion_reason"],
                        "curation_notes": status_context["curation_notes"],
                        "source_model": "suite2p",
                    }
                )
        if not rows:
            rows.append(
                {
                    "project_name": meta.get("project_name", ""),
                    "animal_id": meta.get("animal_id", ""),
                    "session_id": meta.get("session_id", ""),
                    "summary_row_type": "session_status",
                    "cell_id": "",
                    "accepted_component": "",
                    "event_id": "",
                    "event_label": "",
                    "event_type": "",
                    "event_time_s": "",
                    "baseline_dff": "",
                    "peak_dff": "",
                    "peak_delta_dff": "",
                    "auc_dff": "",
                    "curation_status": status_context["curation_status"],
                    "excluded_from_analysis": bool(status_context["excluded_from_analysis"]),
                    "analysis_exclusion_reason": status_context["analysis_exclusion_reason"],
                    "curation_notes": status_context["curation_notes"],
                    "source_model": "suite2p",
                }
            )
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        self.log(f"Saved event summary CSV: {output_path}")
        return output_path

    def export_summaries_across_sessions(self, root: str | Path, *, include_event: bool = True) -> dict[str, object]:
        root_path = Path(root).expanduser().resolve()
        if not root_path.exists():
            raise RuntimeError(f"Project / Parent Folder does not exist: {root_path}")

        session_dirs = [path for path in root_path.rglob("Session_*") if self.is_valid_session_folder(path)]
        session_dirs.sort()
        if not session_dirs:
            raise RuntimeError(f"No Session_### folders were found under: {root_path}")

        processed: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        state_snapshot = self._snapshot_runtime_state()
        try:
            for session_dir in session_dirs:
                if not self.session_has_outputs(session_dir):
                    skipped.append({"session": str(session_dir), "reason": "missing_suite2p_outputs"})
                    continue
                try:
                    self.load_latest_from_session(str(session_dir))
                    session_csv = self.export_session_summary_csv()
                    result = {
                        "session": str(session_dir),
                        "session_summary_csv": str(session_csv),
                    }
                    if include_event:
                        try:
                            event_csv = self.export_event_summary_csv()
                            result["event_summary_csv"] = str(event_csv)
                        except Exception as exc:
                            result["event_summary_csv"] = ""
                            result["event_summary_note"] = str(exc)
                    processed.append(result)
                except Exception as exc:
                    failed.append({"session": str(session_dir), "reason": str(exc)})
        finally:
            self._restore_runtime_state(state_snapshot)

        self.log(
            "Project summary export finished. "
            f"Processed: {len(processed)}. Skipped: {len(skipped)}. Failed: {len(failed)}."
        )
        return {
            "root": str(root_path),
            "processed_sessions": processed,
            "skipped_sessions": skipped,
            "failed_sessions": failed,
            "include_event": bool(include_event),
        }

    def _discover_transfer_sessions(
        self,
        root: Path,
        *,
        require_outputs: bool = True,
        unfinished_only: bool = False,
    ) -> list[Path]:
        session_dirs = [path for path in root.rglob("Session_*") if self.is_valid_session_folder(path)]
        session_dirs.sort()
        if require_outputs:
            session_dirs = [path for path in session_dirs if self.session_has_outputs(path)]
        if unfinished_only:
            session_dirs = [
                path
                for path in session_dirs
                if str(self.load_session_curation_status(path).get("status", "not_started")).strip().lower()
                not in {"completed", "no_soma"}
            ]
        return session_dirs

    def preview_session_transfer(
        self,
        source_root: str | Path,
        target_root: str | Path,
        *,
        require_outputs: bool = True,
        unfinished_only: bool = False,
    ) -> dict[str, object]:
        source = Path(source_root).expanduser().resolve()
        target = Path(target_root).expanduser().resolve()
        if not source.exists():
            raise RuntimeError(f"Source root does not exist: {source}")
        if source == target:
            raise RuntimeError("Source root and target root must be different.")

        sessions = self._discover_transfer_sessions(
            source,
            require_outputs=require_outputs,
            unfinished_only=unfinished_only,
        )
        if not sessions:
            raise RuntimeError(
                "No valid Session_### folders were found under the selected source root."
                if not require_outputs
                else "No valid Session_### folders with Suite2p outputs were found under the selected source root."
            )

        planned: list[dict[str, str]] = []
        existing = 0
        for session in sessions:
            rel = session.relative_to(source)
            destination = target / rel
            if destination.exists():
                existing += 1
            planned.append(
                {
                    "session": str(session),
                    "relative_path": str(rel),
                    "destination": str(destination),
                    "destination_exists": "true" if destination.exists() else "false",
                }
            )

        preview = {
            "source_root": str(source),
            "target_root": str(target),
            "session_count": len(planned),
            "existing_destination_count": existing,
            "require_outputs": bool(require_outputs),
            "unfinished_only": bool(unfinished_only),
            "planned_sessions": planned,
        }
        self.log(
            f"Transfer preview ready. Sessions: {len(planned)}. "
            f"Existing destination folders: {existing}. Source: {source} -> Target: {target}"
        )
        return preview

    def transfer_sessions_between_roots(
        self,
        source_root: str | Path,
        target_root: str | Path,
        *,
        overwrite_existing: bool = True,
        require_outputs: bool = True,
        unfinished_only: bool = False,
        move_completed_after_copy: bool = False,
    ) -> dict[str, object]:
        preview = self.preview_session_transfer(
            source_root,
            target_root,
            require_outputs=require_outputs,
            unfinished_only=unfinished_only,
        )
        source = Path(preview["source_root"])
        target = Path(preview["target_root"])
        target.mkdir(parents=True, exist_ok=True)

        copied: list[dict[str, str]] = []
        moved: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []

        for item in preview["planned_sessions"]:
            session = Path(str(item["session"]))
            rel = Path(str(item["relative_path"]))
            destination = target / rel
            destination_parent = destination.parent
            destination_parent.mkdir(parents=True, exist_ok=True)

            try:
                if destination.exists():
                    if not overwrite_existing:
                        failed.append({"session": str(session), "destination": str(destination), "reason": "destination_exists_and_overwrite_disabled"})
                        continue
                    try:
                        destination.relative_to(target)
                    except Exception as exc:
                        raise RuntimeError(f"Refusing to overwrite path outside target root: {destination}") from exc
                    shutil.rmtree(destination)
                shutil.copytree(session, destination)
                copied.append({"session": str(session), "destination": str(destination)})
                if move_completed_after_copy:
                    status = str(self.load_session_curation_status(session).get("status", "not_started")).strip().lower()
                    if status == "completed":
                        try:
                            session.relative_to(source)
                        except Exception as exc:
                            raise RuntimeError(f"Refusing to remove source path outside source root: {session}") from exc
                        shutil.rmtree(session)
                        moved.append({"session": str(session), "destination": str(destination), "status": status})
            except Exception as exc:
                failed.append({"session": str(session), "destination": str(destination), "reason": str(exc)})

        result = {
            "source_root": str(source),
            "target_root": str(target),
            "copied_sessions": copied,
            "moved_sessions": moved,
            "skipped_sessions": skipped,
            "failed_sessions": failed,
            "overwrite_existing": bool(overwrite_existing),
            "require_outputs": bool(require_outputs),
            "unfinished_only": bool(unfinished_only),
            "move_completed_after_copy": bool(move_completed_after_copy),
        }
        self.log(
            "Session transfer finished. "
            f"Copied: {len(copied)}. Moved: {len(moved)}. Skipped: {len(skipped)}. Failed: {len(failed)}."
        )
        return result

    def export_downstream_package(self) -> Path:
        session = self._session_root()
        plane_dir = self.plane_dir()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        package_root = self._analysis_exports_root() / f"downstream_package_{ts}"
        package_root.mkdir(parents=True, exist_ok=True)

        candidates = [
            plane_dir / "suite2p_motion_preview.mp4",
            plane_dir / "suite2p_overlay_preview.mp4",
            plane_dir / "suite2p_three_panel_preview.mp4",
            plane_dir / "suite2p_reconstruction_preview.mp4",
            plane_dir / "suite2p_mean_projection.png",
            plane_dir / "suite2p_max_projection.png",
            plane_dir / "suite2p_correlation_image.png",
            plane_dir / "suite2p_static_overlay.png",
            plane_dir / "suite2p_contours.png",
            plane_dir / "suite2p_accepted_contours.png",
            plane_dir / "suite2p_rejected_contours.png",
            plane_dir / "suite2p_trace_preview.png",
            plane_dir / "suite2p_roi_size_summary.png",
            plane_dir / "suite2p_accepted_F_traces.csv",
            plane_dir / "suite2p_accepted_dff_traces.csv",
            plane_dir / "suite2p_rejected_F_traces.csv",
            plane_dir / "suite2p_rejected_dff_traces.csv",
            plane_dir / "suite2p_qc_summary.md",
            plane_dir / "suite2p_run_summary.json",
            self._summary_exports_root() / "suite2p_session_summary.csv",
            self._summary_exports_root() / "suite2p_event_summary.csv",
            session / "metadata" / "session_metadata.json",
            session / "metadata" / "stim_events.json",
        ]
        copied = 0
        for path in candidates:
            if path.exists():
                shutil.copy2(path, package_root / path.name)
                copied += 1
        manifest = {
            "session_path": str(session),
            "plane_dir": str(plane_dir),
            "package_root": str(package_root),
            "copied_files": sorted([p.name for p in package_root.iterdir() if p.is_file()]),
        }
        (package_root / "downstream_package_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self.log(f"Built downstream package with {copied} file(s): {package_root}")
        return package_root

    def finalize_roi_edits(self) -> dict[str, str]:
        if self.state.snapshot_dir is not None:
            raise RuntimeError(
                "Finalize ROI Edits only applies to the active plane0 outputs. "
                "Switch back to active plane0 or promote the loaded snapshot first."
            )
        self._require_run_dir()
        plane_dir = self.plane_dir()
        required = [plane_dir / "ops.npy", plane_dir / "stat.npy", plane_dir / "iscell.npy", plane_dir / "F.npy"]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise RuntimeError("Cannot finalize ROI edits because required Suite2p outputs are missing:\n" + "\n".join(missing))

        self.export_artifacts()
        session_csv = self.export_session_summary_csv()
        event_csv = None
        try:
            event_csv = self.export_event_summary_csv()
        except Exception as exc:
            self.log(f"Event summary export skipped during finalize ROI edits: {exc}")
        result = {
            "plane_dir": str(plane_dir),
            "session_summary_csv": str(session_csv),
        }
        if event_csv is not None:
            result["event_summary_csv"] = str(event_csv)
        self.log("Finalized Suite2p ROI edits and refreshed post-run artifacts.")
        return result

    def save_curation_snapshot(self) -> Path:
        plane_dir = self.plane_dir()
        snapshot_root = self._curation_snapshots_root()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_dir = snapshot_root / f"curated_snapshot_{ts}"
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        copied_files: list[str] = []
        for item in plane_dir.iterdir():
            if not item.is_file():
                continue
            target = snapshot_dir / item.name
            shutil.copy2(item, target)
            copied_files.append(item.name)

        manifest = {
            "created_at": datetime.now().isoformat(),
            "session_path": str(self._session_root()),
            "run_dir": str(self._require_run_dir()),
            "plane_dir": str(plane_dir),
            "snapshot_dir": str(snapshot_dir),
            "copied_files": sorted(copied_files),
        }
        (snapshot_dir / "curation_snapshot_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self.log(f"Saved Suite2p curation snapshot: {snapshot_dir}")
        return snapshot_dir

    def build_project_summary_workbook(self, root: str | Path) -> Path:
        root_path = Path(root).expanduser().resolve()
        session_csvs = sorted(root_path.rglob("suite2p_session_summary.csv"))
        event_csvs = sorted(root_path.rglob("suite2p_event_summary.csv"))
        export_root = self._project_exports_root(root_path)
        workbook_path = export_root / "suite2p_project_summary.xlsx"
        workbook = openpyxl.Workbook()
        default_sheet = workbook.active
        workbook.remove(default_sheet)

        def _append_csv_to_sheet(csv_paths: list[Path], sheet_name: str) -> None:
            sheet = workbook.create_sheet(sheet_name)
            header_written = False
            for csv_path in csv_paths:
                with csv_path.open("r", newline="", encoding="utf-8") as handle:
                    reader = csv.reader(handle)
                    header = next(reader, None)
                    if header is None:
                        continue
                    if not header_written:
                        sheet.append(header)
                        header_written = True
                    for row in reader:
                        sheet.append(row)
            if not header_written:
                sheet.append(["no_data"])

        _append_csv_to_sheet(session_csvs, "SessionSummary")
        _append_csv_to_sheet(event_csvs, "EventSummary")
        workbook.save(workbook_path)
        self.log(f"Built project summary workbook: {workbook_path}")
        return workbook_path

    def export_artifacts_across_sessions(self, root: str | Path) -> dict[str, object]:
        root_path = Path(root).expanduser().resolve()
        if not root_path.exists():
            raise RuntimeError(f"Project / Parent Folder does not exist: {root_path}")

        session_dirs = [path for path in root_path.rglob("Session_*") if path.is_dir()]
        session_dirs.sort()
        if not session_dirs:
            raise RuntimeError(f"No Session_### folders were found under: {root_path}")

        processed: list[str] = []
        skipped: list[str] = []
        failed: list[dict[str, str]] = []

        for session_dir in session_dirs:
            latest_run = self._latest_run_for_session(session_dir)
            if latest_run is None:
                skipped.append(str(session_dir))
                continue
            cmd = [str(self._suite2p_python()), str(EXPORT_SCRIPT), "--run-dir", str(latest_run)]
            code = self._run_subprocess(cmd, cwd=EXPORT_SCRIPT.parent)
            if code != 0:
                failed.append({"session": str(session_dir), "run_dir": str(latest_run), "reason": f"exit_code_{code}"})
                continue
            processed.append(str(session_dir))

        summary = {
            "root": str(root_path),
            "processed_sessions": processed,
            "skipped_sessions": skipped,
            "failed_sessions": failed,
        }
        self.log(
            "Project artifact export finished. "
            f"Processed: {len(processed)}. Skipped: {len(skipped)}. Failed: {len(failed)}."
        )
        return summary

    def cleanup_retained_binaries_after_curation(
        self,
        root: str | Path,
        *,
        apply: bool = False,
        report_json: str | Path | None = None,
    ) -> dict[str, object]:
        root_path = Path(root).expanduser().resolve()
        if not root_path.exists():
            raise RuntimeError(f"Project / Parent Folder does not exist: {root_path}")
        if not RETAINED_BIN_CLEANUP_SCRIPT.exists():
            raise RuntimeError(f"Cleanup script not found: {RETAINED_BIN_CLEANUP_SCRIPT}")

        if report_json is None or not str(report_json).strip():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = "apply" if apply else "preview"
            report_json = (
                SCIENTIFICA_ROOT / "suite2p_information" / "cleanup_reports"
                / f"retained_bin_cleanup_{suffix}_{stamp}.json"
            )
        report_path = Path(report_json).expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(self._suite2p_python()),
            str(RETAINED_BIN_CLEANUP_SCRIPT),
            "--root",
            str(root_path),
            "--report-json",
            str(report_path),
        ]
        if apply:
            cmd.append("--apply")

        code = self._run_subprocess(cmd, cwd=RETAINED_BIN_CLEANUP_SCRIPT.parent)
        if code != 0:
            raise RuntimeError(
                f"Retained binary cleanup {'apply' if apply else 'preview'} failed with exit code {code}."
            )
        if not report_path.exists():
            raise RuntimeError(f"Retained binary cleanup did not produce a report: {report_path}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.log(
            f"Retained binary cleanup {'applied' if apply else 'previewed'}: "
            f"{report.get('deleted_run_dir_count', 0)} eligible retained run dir(s). Report: {report_path}"
        )
        return report

    def generate_project_summary_plots(self, root: str | Path) -> list[Path]:
        root_path = Path(root).expanduser().resolve()
        export_root = self._project_exports_root(root_path)
        session_csvs = sorted(root_path.rglob("suite2p_session_summary.csv"))
        if not session_csvs:
            raise RuntimeError("No Suite2p session summary CSVs were found under the selected root.")

        labels: list[str] = []
        roi_counts: list[int] = []
        accepted_counts: list[int] = []
        excluded_sessions: list[str] = []
        for csv_path in session_csvs:
            with csv_path.open("r", newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            if not rows:
                continue
            session_id = rows[0].get("session_id", csv_path.parent.name)
            excluded = str(rows[0].get("excluded_from_analysis", "")).lower() == "true"
            if excluded:
                excluded_sessions.append(session_id)
                continue
            roi_rows = [row for row in rows if str(row.get("summary_row_type", "roi")).lower() == "roi"]
            labels.append(session_id)
            roi_counts.append(len(roi_rows))
            accepted_counts.append(sum(str(r.get("accepted_component", "")).lower() == "true" for r in roi_rows))

        if not labels:
            raise RuntimeError(
                "All session summary CSVs under the selected root are currently marked excluded from analysis."
            )

        x = np.arange(len(labels))
        fig, ax = plt.subplots(figsize=(max(8, len(labels) * 1.1), 5), dpi=150)
        ax.bar(x - 0.18, roi_counts, width=0.36, label="Total ROIs", color="#5dd6c0")
        ax.bar(x + 0.18, accepted_counts, width=0.36, label="Accepted", color="#80ffb4")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_ylabel("Count")
        ax.set_title("Suite2p Session ROI Counts")
        ax.legend()
        if excluded_sessions:
            ax.text(
                0.99,
                0.98,
                f"Excluded sessions: {len(excluded_sessions)}",
                transform=ax.transAxes,
                ha="right",
                va="top",
                fontsize=9,
                color="#cccccc",
                bbox={"facecolor": "#1d1d1d", "edgecolor": "#444444", "alpha": 0.9, "boxstyle": "round,pad=0.3"},
            )
        fig.tight_layout()
        roi_plot = export_root / "suite2p_project_roi_counts.png"
        fig.savefig(roi_plot, bbox_inches="tight")
        plt.close(fig)
        self.log(f"Generated project summary plot: {roi_plot}")
        return [roi_plot]

    def generate_project_summary_report(self, root: str | Path) -> Path:
        root_path = Path(root).expanduser().resolve()
        export_root = self._project_exports_root(root_path)
        session_csvs = sorted(root_path.rglob("suite2p_session_summary.csv"))
        event_csvs = sorted(root_path.rglob("suite2p_event_summary.csv"))
        lines = [
            "# Suite2p Project Summary",
            "",
            f"- Scan root: `{root_path}`",
            f"- Session summary files: `{len(session_csvs)}`",
            f"- Event summary files: `{len(event_csvs)}`",
        ]
        total_rows = 0
        total_accepted = 0
        excluded_session_files: list[tuple[Path, str]] = []
        included_session_files: list[Path] = []
        for csv_path in session_csvs:
            with csv_path.open("r", newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            if not rows:
                included_session_files.append(csv_path)
                continue
            excluded = str(rows[0].get("excluded_from_analysis", "")).lower() == "true"
            reason = str(rows[0].get("analysis_exclusion_reason", "") or "")
            if excluded:
                excluded_session_files.append((csv_path, reason or "excluded"))
                continue
            included_session_files.append(csv_path)
            roi_rows = [row for row in rows if str(row.get("summary_row_type", "roi")).lower() == "roi"]
            total_rows += len(roi_rows)
            total_accepted += sum(str(r.get("accepted_component", "")).lower() == "true" for r in roi_rows)
        lines.extend(
            [
                f"- Included session summary files: `{len(included_session_files)}`",
                f"- Excluded session summary files: `{len(excluded_session_files)}`",
                f"- Total ROI rows across included sessions: `{total_rows}`",
                f"- Total accepted ROI rows across included sessions: `{total_accepted}`",
                "",
                "## Included session summary files",
                "",
            ]
        )
        lines.extend([f"- `{p}`" for p in included_session_files] or ["- none"])
        lines.extend(
            [
                "",
                "## Excluded session summary files",
                "",
            ]
        )
        lines.extend([f"- `{path}` ({reason})" for path, reason in excluded_session_files] or ["- none"])
        report_path = export_root / "suite2p_project_summary.md"
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.log(f"Generated project summary report: {report_path}")
        return report_path

    def inspect_component(self, component_index: int) -> Path:
        data = self._suite2p_data()
        plane_dir = data["plane_dir"]
        ops = data["ops"]
        stat = data["stat"]
        F = data["F"]
        Fneu = data["Fneu"]
        spks = data["spks"]
        idx = int(component_index)
        if idx < 0 or idx >= len(stat):
            raise RuntimeError(f"Component index out of range: {idx}")
        mean_img = np.asarray(ops.get("meanImg"))
        if mean_img.ndim != 2:
            raise RuntimeError("meanImg is not available for component inspection.")
        roi = stat[idx]
        ypix = np.asarray(roi["ypix"])
        xpix = np.asarray(roi["xpix"])
        t = np.arange(F.shape[1], dtype=float) / max(float(ops.get("fs", 1.0)), 1e-6)

        fig, axes = plt.subplots(4, 1, figsize=(12, 10), dpi=150)
        axes[0].imshow(mean_img, cmap="gray")
        axes[0].scatter(xpix, ypix, s=1.0, c="#00ffd0", alpha=0.7)
        axes[0].set_title(f"Component {idx} Footprint")
        axes[0].set_axis_off()
        axes[1].plot(t, F[idx], linewidth=0.8)
        axes[1].set_title("F")
        if Fneu is not None and idx < Fneu.shape[0]:
            axes[2].plot(t, Fneu[idx], linewidth=0.8)
        axes[2].set_title("Fneu")
        if spks is not None and idx < spks.shape[0]:
            axes[3].plot(t, spks[idx], linewidth=0.8)
        axes[3].set_title("spks")
        for ax in axes[1:]:
            ax.grid(alpha=0.2)
            ax.set_xlabel("Time (s)")
        fig.tight_layout()
        output_path = plane_dir / f"suite2p_component_{idx:04d}_inspection.png"
        fig.savefig(output_path, bbox_inches="tight")
        plt.close(fig)
        self.log(f"Saved component inspection figure: {output_path}")
        return output_path

    def _open_path(self, path: Path) -> None:
        if not path.exists():
            raise RuntimeError(f"Path does not exist yet: {path}")
        system = platform.system()
        if system == "Windows":
            os.startfile(str(path))
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        self.log(f"Opened: {path}")

    def _require_run_dir(self) -> Path:
        if self.state.run_dir is None:
            raise RuntimeError("No Suite2p run directory is selected yet.")
        return self.state.run_dir

    def _normalize_plane_dir(self, path: Path) -> Path:
        if path.name == "plane0" and (path / "ops.npy").exists():
            return path
        candidate = path / "plane0"
        if candidate.exists() and (candidate / "ops.npy").exists():
            return candidate
        candidate = path / "suite2p" / "plane0"
        if candidate.exists() and (candidate / "ops.npy").exists():
            return candidate
        raise RuntimeError(
            "Could not resolve a Suite2p plane0 folder from the selected path. "
            "Choose plane0, suite2p, or outputs."
        )

    def _infer_run_dir_from_plane_dir(self, plane_dir: Path) -> Path | None:
        for parent in [plane_dir.parent, plane_dir.parent.parent, plane_dir.parent.parent.parent]:
            if (parent / "session_manifest.json").exists():
                return parent
        return None

    def _infer_session_from_path(self, path: Path) -> Path | None:
        resolved = path.expanduser().resolve()
        if resolved.is_dir() and resolved.name.lower().startswith("session_"):
            return resolved
        for candidate in [resolved, *resolved.parents]:
            if candidate.name.lower().startswith("session_"):
                return candidate
        return None
