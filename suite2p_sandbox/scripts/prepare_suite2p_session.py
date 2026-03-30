#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SANDBOX_ROOT = SCRIPT_DIR.parent


def _latest(paths: list[Path]) -> Path | None:
    existing = [p for p in paths if p.exists()]
    existing.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return existing[0] if existing else None


def _build_run_name(session_path: Path) -> str:
    parts = session_path.parts
    tail = "_".join(parts[-5:]) if len(parts) >= 5 else session_path.name
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{tail}_suite2p_{ts}"


def _discover(session_path: Path) -> dict:
    raw_dirs = sorted(session_path.glob("raw_*"))
    raw_dir = raw_dirs[0] if raw_dirs else None
    raw_tiffs = sorted(raw_dir.glob("*.tif")) if raw_dir else []
    metadata_file = session_path / "metadata" / "session_metadata.json"
    if not metadata_file.exists():
        metadata_file = None

    analysis_dir = session_path / "analysis"
    if not analysis_dir.exists():
        analysis_dir = None

    manifest_files = sorted(analysis_dir.glob("run_packets/*/manifest.json")) if analysis_dir else []
    fit_hdf5_files = sorted((analysis_dir / "working" / "models").glob("analysis_cnmf_fit_*.hdf5")) if analysis_dir else []
    checkpoint_files = sorted((analysis_dir / "working" / "checkpoints").glob("checkpoint_*.json")) if analysis_dir else []
    figure_files = sorted(analysis_dir.glob("run_packets/*/outputs/figures/*.png")) if analysis_dir else []

    latest_manifest = _latest(manifest_files)
    latest_fit = _latest(fit_hdf5_files)
    latest_checkpoint = _latest(checkpoint_files)

    return {
        "session_path": str(session_path),
        "raw_dir": str(raw_dir) if raw_dir else None,
        "raw_tiff_count": len(raw_tiffs),
        "raw_tiffs": [str(p) for p in raw_tiffs],
        "metadata_file": str(metadata_file) if metadata_file else None,
        "analysis_dir": str(analysis_dir) if analysis_dir else None,
        "latest_reference_manifest": str(latest_manifest) if latest_manifest else None,
        "latest_reference_fit_hdf5": str(latest_fit) if latest_fit else None,
        "latest_reference_checkpoint": str(latest_checkpoint) if latest_checkpoint else None,
        "available_reference_figures": [str(p) for p in figure_files],
    }


def _session_frame_rate(info: dict) -> float:
    metadata_file = info.get("metadata_file")
    if metadata_file:
        path = Path(str(metadata_file))
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                for key in ("acquired_frame_rate_hz", "frame_rate_hz"):
                    value = payload.get(key)
                    if value not in (None, ""):
                        return float(value)
            except Exception:
                pass
    return 50.0


def _default_save_path(session_path: Path, run_name: str) -> Path:
    analysis_dir = session_path / "analysis"
    if analysis_dir.exists():
        # Keep Suite2p durable outputs in the session analysis tree rather than
        # under a run-packet-like nested folder. Suite2p itself will create
        # `suite2p/plane0` beneath this root.
        return analysis_dir / "outputs"
    return SANDBOX_ROOT / "runs" / run_name / "outputs"


def _default_run_root(session_path: Path) -> Path:
    return session_path / "suite2p_runs"


def _default_info_root(session_path: Path) -> Path:
    return Path(r"D:\Scientifica\suite2p_information")


def _default_fast_disk(run_name: str) -> Path:
    # Keep fast temporary files off OneDrive. Prefer a local SSD-backed temp area.
    return Path.home() / "suite2p" / "temp" / run_name


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a Suite2p sandbox run for one session.")
    parser.add_argument("--session", required=True, help="Absolute path to the session folder.")
    parser.add_argument("--run-name", help="Optional custom run folder name.")
    args = parser.parse_args()

    session_path = Path(args.session).expanduser().resolve()
    if not session_path.exists():
        raise SystemExit(f"Session path does not exist: {session_path}")

    runs_dir = _default_run_root(session_path)
    info_root = _default_info_root(session_path)
    runs_dir.mkdir(parents=True, exist_ok=True)
    info_root.mkdir(parents=True, exist_ok=True)
    run_name = args.run_name or _build_run_name(session_path)
    run_dir = runs_dir / run_name
    if run_dir.exists():
        raise SystemExit(f"Run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)

    info = _discover(session_path)
    timestamp = datetime.now().isoformat()

    manifest = {
        "created_at": timestamp,
        "sandbox_root": str(SANDBOX_ROOT),
        "run_dir": str(run_dir),
        **info,
        "status": "prepared",
        "next_step": "Install suite2p in a separate env, then run a Session_004 proof-of-concept.",
    }
    _write_json(run_dir / "session_manifest.json", manifest)

    source_lines = [
        f"session_path: {info['session_path']}",
        f"raw_dir: {info['raw_dir'] or ''}",
        f"metadata_file: {info['metadata_file'] or ''}",
        f"analysis_dir: {info['analysis_dir'] or ''}",
        "",
        "[raw_tiffs]",
        *info["raw_tiffs"],
        "",
        "[reference_figures]",
        *info["available_reference_figures"],
    ]
    (run_dir / "source_paths.txt").write_text("\n".join(source_lines) + "\n", encoding="utf-8")

    notes = """# Suite2p Run Notes

## Goal

Compare Suite2p against the current CaImAn Session_004 workflow.

## Questions

- Does Suite2p detect the central somata more cleanly?
- Is runtime more acceptable on this workstation?
- How much post-processing would we need to recreate the review experience we like from CaImAn?
"""
    (run_dir / "notes.md").write_text(notes, encoding="utf-8")

    info_readme = """# Suite2p Information

This folder stores shared Suite2p support information on the HDD.

## Intended contents

- `archived_outputs/`
  - previous Suite2p outputs moved aside before overwrite
- markdown summaries and notes
- other small support files that help document the Suite2p workflow

Per-session run metadata folders live under each session:

- `Session_###/suite2p_runs/`
"""
    readme_path = info_root / "README.md"
    if not readme_path.exists():
        readme_path.write_text(info_readme, encoding="utf-8")

    raw_dir = info["raw_dir"]
    session_fs = _session_frame_rate(info)
    save_path = _default_save_path(session_path, run_name)
    fast_disk = _default_fast_disk(run_name)

    suite2p_db = {
        "data_path": [raw_dir] if raw_dir else [],
        "save_path0": str(save_path),
        "fast_disk": str(fast_disk),
        "look_one_level_down": False,
        "nplanes": 1,
        "nchannels": 1,
        "functional_chan": 1,
        "fs": session_fs,
        "tau": 0.4,
        "do_registration": True,
    }
    _write_json(run_dir / "suite2p_db.json", suite2p_db)

    suite2p_ops_override = {
        "roidetect": True,
        "do_registration": True,
        "nonrigid": False,
        "sparse_mode": True,
        "anatomical_only": 0,
        "denoise": 0
    }
    _write_json(run_dir / "suite2p_ops_override.json", suite2p_ops_override)

    save_path.mkdir(parents=True, exist_ok=True)
    fast_disk.mkdir(parents=True, exist_ok=True)

    print(f"Prepared Suite2p sandbox run: {run_dir}")
    print(f"Manifest: {run_dir / 'session_manifest.json'}")
    print(f"Raw TIFF count: {info['raw_tiff_count']}")


if __name__ == "__main__":
    main()
