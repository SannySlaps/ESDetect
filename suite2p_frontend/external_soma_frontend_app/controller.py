from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from suite2p_frontend_app.controller import Suite2pController
from suite2p_frontend_app.state import RuntimeState

from .config import (
    ESDETECT_EXTRACTION_DIRNAME,
    ESDETECT_PACKAGED_DIRNAME,
    ESDETECT_SEGMENTATION_DIRNAME,
    EXPORT_SCRIPT,
    EXTERNAL_EXTRACT_SCRIPT,
    EXTERNAL_PACKAGE_SCRIPT,
    EXTERNAL_RUNS_ROOT_NAME,
    EXTERNAL_SEGMENT_SCRIPT,
    FULL_OVERLAY_SCRIPT,
)


class ExternalSomaController(Suite2pController):
    OVERLAY_PRESETS = {
        "quick_review": {"filename": "suite2p_quick_review_overlay.mp4", "fps": 20, "frame_stride": 4, "gain": 1.0},
        "presentation": {"filename": "suite2p_presentation_overlay.mp4", "fps": 30, "frame_stride": 2, "gain": 1.0},
        "full_session": {"filename": "suite2p_full_session_overlay.mp4", "fps": 20, "frame_stride": 1, "gain": 1.0},
    }

    def __init__(self, state: RuntimeState, logger: Callable[[str], None] | None = None) -> None:
        super().__init__(state, logger=logger)

    def _cleanup_esdetect_temp(self, run_dir: Path) -> Path | None:
        db_path = run_dir / "suite2p_db.json"
        if not db_path.exists():
            self.log(f"ESDetect temp cleanup skipped: missing suite2p_db.json under {run_dir}")
            return None
        db = json.loads(db_path.read_text(encoding="utf-8-sig"))
        fast_disk = str(db.get("fast_disk", "")).strip()
        if not fast_disk:
            self.log(f"ESDetect temp cleanup skipped: no fast_disk recorded in {db_path}")
            return None

        fast_disk_path = Path(fast_disk).expanduser().resolve()
        temp_root = self.default_fast_disk_root().expanduser().resolve()
        try:
            fast_disk_path.relative_to(temp_root)
        except ValueError as exc:
            raise RuntimeError(
                f"Refusing ESDetect temp cleanup outside temp root: {fast_disk_path}"
            ) from exc

        if not fast_disk_path.exists():
            self.log(f"ESDetect temp cleanup skipped: temp path does not exist: {fast_disk_path}")
            return fast_disk_path

        manifest = self._load_manifest(run_dir)
        session_path = Path(str(manifest.get("session_path", ""))).expanduser().resolve()
        if not session_path.exists():
            raise RuntimeError(f"ESDetect temp retention failed: session path from manifest does not exist: {session_path}")

        retained_root = self._analysis_root_for_session(session_path) / "retained_temp" / run_dir.name
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
            self.log(f"Retained ESDetect temp bin files under session HDD: {retained_root}")
        else:
            self.log(f"ESDetect temp cleanup found no .bin files to retain under: {fast_disk_path}")

        shutil.rmtree(fast_disk_path)
        self.log(f"Removed ESDetect SSD temp folder after retention: {fast_disk_path}")
        return retained_root if moved_any else fast_disk_path

    def default_run_root(self, session_path: str | Path) -> Path:
        session = Path(session_path).expanduser().resolve()
        return session / EXTERNAL_RUNS_ROOT_NAME

    def prepare_session(self, session_path: str, run_name: str = "") -> Path:
        session = Path(session_path).expanduser().resolve()
        if not session.exists():
            raise RuntimeError(f"Session path does not exist: {session}")

        metadata_dir = session / "metadata"
        metadata_path = metadata_dir / "session_metadata.json"
        metadata: dict[str, object] = {}
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))

        raw_dirs = sorted([p for p in session.iterdir() if p.is_dir() and p.name.startswith("raw_")])
        if not raw_dirs:
            raise RuntimeError(f"No raw_* directory found under {session}")
        raw_dir = raw_dirs[-1]
        raw_tiffs = sorted(raw_dir.glob("*.tif"))

        run_root = self.default_run_root(session)
        run_root.mkdir(parents=True, exist_ok=True)
        stem = run_name.strip() if run_name.strip() else f"{session.name}_ESDetect_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        run_dir = run_root / stem
        if run_dir.exists():
            raise RuntimeError(f"Run directory already exists: {run_dir}")
        run_dir.mkdir(parents=True, exist_ok=False)

        fs = float(metadata.get("acquired_frame_rate_hz") or metadata.get("frame_rate_hz") or 0.0)
        payload = self.default_parameter_payload()
        payload["db"]["fs"] = fs

        manifest = {
            "created_at": datetime.now().isoformat(),
            "backend": "ESDetect",
            "session_path": str(session),
            "raw_dir": str(raw_dir),
            "run_dir": str(run_dir),
            "save_path0": str(self.default_output_root(session)),
            "raw_tiffs": [str(path) for path in raw_tiffs],
            "raw_tiff_count": len(raw_tiffs),
        }
        db = {
            "fs": fs,
            "tau": payload["db"].get("tau", 0.4),
            "nplanes": payload["db"].get("nplanes", 1),
            "nchannels": payload["db"].get("nchannels", 1),
            "functional_chan": payload["db"].get("functional_chan", 1),
            "do_registration": False,
            "data_path": [str(raw_dir)],
            "save_path0": str(self.default_output_root(session)),
            "fast_disk": str(self.default_fast_disk_root() / run_dir.name),
            "external_backend": "ESDetect_trial14",
        }
        ops = dict(payload["ops"])
        ops.update(
            {
                "external_backend": "ESDetect_trial14",
                "source_image": "proposal_soma_blob_transient",
                "bg_sigma": 14.0,
                "blob_sigma": 4.5,
                "blob_weight": 1.8,
                "transient_weight": 1.4,
                "thresh_q": 93.8,
                "peak_fraction": 0.18,
                "min_area": 80,
                "max_area": 1900,
                "dilate_iters": 2,
                "motion_correct": True,
                "registration_downsample": 0.5,
                "max_shift": 15.0,
                "auto_generate_overlay_video": False,
                "auto_overlay_preset": "quick_review",
                "inner_iters": 4,
                "outer_iters": 12,
                "neuropil_coeff": 0.7,
                "baseline_percentile": 20.0,
            }
        )

        (run_dir / "session_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (run_dir / "suite2p_db.json").write_text(json.dumps(db, indent=2), encoding="utf-8")
        (run_dir / "suite2p_ops_override.json").write_text(json.dumps(ops, indent=2), encoding="utf-8")

        self.state.session_path = session
        self.state.run_dir = run_dir
        self.state.run_name = run_dir.name
        self.state.plane_dir = None
        self.set_status("ESDetect run prepared.")
        self.log(f"Prepared ESDetect run dir: {run_dir}")
        return run_dir

    def _run_logged_subprocess(self, cmd: list[str], *, cwd: Path, log_handle) -> int:
        self.log(f"Running: {' '.join(cmd)}")
        log_handle.write(f"$ {' '.join(cmd)}\n")
        log_handle.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            text = line.rstrip("\n")
            self._logger(text)
            log_handle.write(text + "\n")
        log_handle.flush()
        return proc.wait()

    def run_suite2p(self, *, quiet_launch_note: bool = False) -> None:
        run_dir = self._require_run_dir()
        session = self._session_root()
        manifest = self._load_manifest(run_dir)
        ops = json.loads((run_dir / "suite2p_ops_override.json").read_text(encoding="utf-8-sig"))
        python_exe = str(self._suite2p_python())

        seg_label = f"{run_dir.name}_segmentation"
        extract_label = f"{run_dir.name}_extract"
        package_label = f"{run_dir.name}_package"
        segmentation_dir = session / "analysis" / ESDETECT_SEGMENTATION_DIRNAME / seg_label
        extraction_dir = session / "analysis" / ESDETECT_EXTRACTION_DIRNAME / extract_label
        packaged_plane = session / "analysis" / ESDETECT_PACKAGED_DIRNAME / package_label / "plane0"
        fast_disk = Path(str(json.loads((run_dir / "suite2p_db.json").read_text(encoding="utf-8-sig")).get("fast_disk", "") or "")).expanduser()
        reg_file = fast_disk / "suite2p" / "plane0" / "data.bin" if str(fast_disk) else (segmentation_dir / "reg.bin")
        reg_file.parent.mkdir(parents=True, exist_ok=True)

        runtime_path = run_dir / "suite2p_runtime.json"
        log_path = run_dir / "suite2p_run.log"
        started_at = datetime.now()
        self.set_status("Running ESDetect pipeline...")

        with log_path.open("w", encoding="utf-8") as log_handle:
            log_handle.write(f"[{started_at.isoformat()}] Launching ESDetect pipeline.\n")

            seg_cmd = [
                python_exe,
                str(EXTERNAL_SEGMENT_SCRIPT),
                "--session",
                str(session),
                "--label",
                seg_label,
                "--source-image",
                str(ops.get("source_image", "proposal_soma_blob")),
                "--bg-sigma",
                str(ops.get("bg_sigma", 14.0)),
                "--blob-sigma",
                str(ops.get("blob_sigma", 4.5)),
                "--blob-weight",
                str(ops.get("blob_weight", 1.8)),
                "--transient-weight",
                str(ops.get("transient_weight", 1.4)),
                "--thresh-q",
                str(ops.get("thresh_q", 93.8)),
                "--peak-fraction",
                str(ops.get("peak_fraction", 0.18)),
                "--min-area",
                str(ops.get("min_area", 80)),
                "--max-area",
                str(ops.get("max_area", 1900)),
                "--dilate-iters",
                str(ops.get("dilate_iters", 2)),
                "--registration-downsample",
                str(ops.get("registration_downsample", 0.5)),
                "--max-shift",
                str(ops.get("max_shift", 15.0)),
                "--reg-file",
                str(reg_file),
            ]
            if bool(ops.get("motion_correct", True)):
                seg_cmd.append("--motion-correct")
            code = self._run_logged_subprocess(seg_cmd, cwd=EXTERNAL_SEGMENT_SCRIPT.parent, log_handle=log_handle)
            if code != 0:
                self.set_status("ESDetect segmentation failed.")
                raise RuntimeError(f"ESDetect segmentation failed with exit code {code}.")

            extract_cmd = [
                python_exe,
                str(EXTERNAL_EXTRACT_SCRIPT),
                "--segmentation-dir",
                str(segmentation_dir),
                "--label",
                extract_label,
                "--inner-iters",
                str(ops.get("inner_iters", 4)),
                "--outer-iters",
                str(ops.get("outer_iters", 12)),
                "--neuropil-coeff",
                str(ops.get("neuropil_coeff", 0.7)),
                "--baseline-percentile",
                str(ops.get("baseline_percentile", 20.0)),
            ]
            code = self._run_logged_subprocess(extract_cmd, cwd=EXTERNAL_EXTRACT_SCRIPT.parent, log_handle=log_handle)
            if code != 0:
                self.set_status("ESDetect extraction failed.")
                raise RuntimeError(f"ESDetect extraction failed with exit code {code}.")

            package_cmd = [
                python_exe,
                str(EXTERNAL_PACKAGE_SCRIPT),
                "--extraction-dir",
                str(extraction_dir),
                "--label",
                package_label,
            ]
            code = self._run_logged_subprocess(package_cmd, cwd=EXTERNAL_PACKAGE_SCRIPT.parent, log_handle=log_handle)
            if code != 0:
                self.set_status("ESDetect packaging failed.")
                raise RuntimeError(f"ESDetect packaging failed with exit code {code}.")

        active_root = self.default_output_root(session) / "suite2p"
        active_plane = active_root / "plane0"
        run_plane = run_dir / "outputs" / "suite2p" / "plane0"
        if active_root.exists():
            archive_root = session / "analysis" / "archived_outputs"
            archive_root.mkdir(parents=True, exist_ok=True)
            archive_target = archive_root / f"{session.name}_ESDetect_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.move(str(active_root), str(archive_target))
            self.log(f"Archived existing active outputs to: {archive_target}")
        active_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(packaged_plane, active_plane)
        run_plane.parent.mkdir(parents=True, exist_ok=True)
        if run_plane.exists():
            shutil.rmtree(run_plane)
        shutil.copytree(packaged_plane, run_plane)

        export_cmd = [python_exe, str(EXPORT_SCRIPT), "--run-dir", str(run_dir)]
        with log_path.open("a", encoding="utf-8") as log_handle:
            code = self._run_logged_subprocess(export_cmd, cwd=EXPORT_SCRIPT.parent, log_handle=log_handle)
            if code != 0:
                self.set_status("ESDetect artifact export failed.")
                raise RuntimeError(f"ESDetect artifact export failed with exit code {code}.")

            if bool(ops.get("auto_generate_overlay_video", False)):
                preset_name = str(ops.get("auto_overlay_preset", "quick_review") or "quick_review")
                preset = self.OVERLAY_PRESETS.get(preset_name)
                if preset is None:
                    raise RuntimeError(f"Unknown ESDetect auto overlay preset: {preset_name}")
                overlay_cmd = [
                    python_exe,
                    str(FULL_OVERLAY_SCRIPT),
                    "--plane-dir",
                    str(active_plane),
                    "--output",
                    str(active_plane / str(preset["filename"])),
                    "--fps",
                    str(preset["fps"]),
                    "--frame-stride",
                    str(preset["frame_stride"]),
                    "--gain",
                    str(preset.get("gain", 1.0)),
                ]
                code = self._run_logged_subprocess(overlay_cmd, cwd=FULL_OVERLAY_SCRIPT.parent, log_handle=log_handle)
                if code != 0:
                    self.set_status("ESDetect optional overlay export failed.")
                    raise RuntimeError(f"ESDetect optional overlay export failed with exit code {code}.")

        if run_plane.exists():
            shutil.rmtree(run_plane)
        shutil.copytree(active_plane, run_plane)

        cleaned_temp_path = self._cleanup_esdetect_temp(run_dir)

        finished_at = datetime.now()
        runtime_payload = {
            "created_at": finished_at.isoformat(),
            "backend": "ESDetect",
            "session": str(session),
            "run_dir": str(run_dir),
            "segmentation_dir": str(segmentation_dir),
            "extraction_dir": str(extraction_dir),
            "packaged_plane_dir": str(packaged_plane),
            "active_plane_dir": str(active_plane),
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": (finished_at - started_at).total_seconds(),
            "temp_cleanup": "deleted_fast_disk_after_export",
            "cleaned_temp_path": str(cleaned_temp_path) if cleaned_temp_path is not None else "",
        }
        runtime_path.write_text(json.dumps(runtime_payload, indent=2), encoding="utf-8")

        self.state.plane_dir = active_plane
        self.set_status("ESDetect pipeline finished.")
        self.log(f"ESDetect active plane: {active_plane}")

    def render_overlay_preset(self, preset_name: str) -> Path:
        preset = self.OVERLAY_PRESETS.get(preset_name)
        if preset is None:
            raise RuntimeError(f"Unknown ESDetect overlay preset: {preset_name}")
        plane_dir = self.plane_dir()
        output_path = plane_dir / str(preset["filename"])
        cmd = [
            str(self._suite2p_python()),
            str(FULL_OVERLAY_SCRIPT),
            "--plane-dir",
            str(plane_dir),
            "--output",
            str(output_path),
            "--fps",
            str(preset["fps"]),
            "--frame-stride",
            str(preset["frame_stride"]),
            "--gain",
            str(preset.get("gain", 1.0)),
        ]
        self.set_status(f"Rendering ESDetect {preset_name.replace('_', ' ')} overlay...")
        code = self._run_subprocess(cmd, cwd=FULL_OVERLAY_SCRIPT.parent)
        if code != 0:
            self.set_status("ESDetect overlay render failed.")
            raise RuntimeError(f"ESDetect overlay render failed with exit code {code}.")
        self.set_status("ESDetect overlay rendered.")
        return output_path

    def open_overlay_preset(self, preset_name: str) -> None:
        preset = self.OVERLAY_PRESETS.get(preset_name)
        if preset is None:
            raise RuntimeError(f"Unknown ESDetect overlay preset: {preset_name}")
        self._open_path(self.plane_dir() / str(preset["filename"]))
