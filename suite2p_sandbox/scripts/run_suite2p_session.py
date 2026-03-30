#!/usr/bin/env python
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from suite2p import default_settings, run_s2p


SCRIPT_DIR = Path(__file__).resolve().parent


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _resolve_run_dir(run_dir_arg: str) -> Path:
    run_dir = Path(run_dir_arg).expanduser().resolve()
    if not run_dir.exists():
        raise SystemExit(f"Run directory does not exist: {run_dir}")
    return run_dir


def _validate_required_paths(db: dict) -> None:
    data_paths = [Path(p) for p in db.get("data_path", [])]
    if not data_paths:
        raise SystemExit("suite2p_db.json is missing data_path.")
    missing = [str(p) for p in data_paths if not p.exists()]
    if missing:
        raise SystemExit("Missing Suite2p data_path entries:\n" + "\n".join(missing))

    for key in ("save_path0", "fast_disk"):
        value = db.get(key, "")
        if not value:
            raise SystemExit(f"suite2p_db.json is missing {key}.")
        Path(value).mkdir(parents=True, exist_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Suite2p using a prepared sandbox run folder.")
    parser.add_argument(
        "--run-dir",
        required=True,
        help="Path to a prepared suite2p_sandbox run directory.",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Validate inputs and print the resolved config without launching Suite2p.",
    )
    parser.add_argument(
        "--quiet-launch-note",
        action="store_true",
        help="Suppress the human-facing 'Launching Suite2p...' log note.",
    )
    return parser.parse_args()


class _Tee:
    def __init__(self, *streams) -> None:
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def _run_post_exports(run_dir: Path) -> None:
    export_script = SCRIPT_DIR / "export_suite2p_artifacts.py"
    if not export_script.exists():
        print(f"Post-run export script missing, skipping exports: {export_script}")
        return

    import subprocess

    cmd = [sys.executable, str(export_script), "--run-dir", str(run_dir)]
    print("Exporting Suite2p review artifacts...")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"Suite2p export step failed with exit code {result.returncode}.")
    else:
        print("Suite2p export step finished.")


def main() -> None:
    args = parse_args()
    run_dir = _resolve_run_dir(args.run_dir)

    db_path = run_dir / "suite2p_db.json"
    ops_override_path = run_dir / "suite2p_ops_override.json"
    manifest_path = run_dir / "session_manifest.json"

    for required in (db_path, ops_override_path, manifest_path):
        if not required.exists():
            raise SystemExit(f"Required file missing: {required}")

    db = _load_json(db_path)
    settings_override = _load_json(ops_override_path)
    manifest = _load_json(manifest_path)

    _validate_required_paths(db)

    settings = default_settings()
    settings.update(settings_override)

    print(f"Suite2p sandbox run: {run_dir}")
    print(f"Session: {manifest.get('session_path', '')}")
    print(f"Data path(s): {db.get('data_path', [])}")
    print(f"Save path: {db.get('save_path0', '')}")
    print(f"Fast disk: {db.get('fast_disk', '')}")
    print(f"Settings overrides: {settings_override}")

    if args.print_only:
        print("Print-only mode: Suite2p was not launched.")
        return

    log_path = run_dir / "suite2p_run.log"
    runtime_path = run_dir / "suite2p_runtime.json"
    started_at = datetime.now()
    start_ts = time.time()
    runtime_payload = {
        "run_dir": str(run_dir),
        "started_at": started_at.isoformat(),
        "status": "running",
    }
    _write_json(runtime_path, runtime_payload)

    with log_path.open("a", encoding="utf-8") as log_file:
        tee = _Tee(sys.stdout, log_file)
        try:
            with contextlib.redirect_stdout(tee), contextlib.redirect_stderr(tee):
                if not args.quiet_launch_note:
                    print(f"[{started_at.isoformat()}] Launching Suite2p...")
                run_s2p(settings=settings, db=db)
                elapsed = time.time() - start_ts
                finished_at = datetime.now()
                runtime_payload.update(
                    {
                        "finished_at": finished_at.isoformat(),
                        "elapsed_seconds": round(elapsed, 3),
                        "elapsed_minutes": round(elapsed / 60.0, 3),
                        "status": "completed",
                    }
                )
                _write_json(runtime_path, runtime_payload)
                print(f"[{finished_at.isoformat()}] Suite2p run finished.")
                _run_post_exports(run_dir)
        except Exception:
            finished_at = datetime.now()
            elapsed = time.time() - start_ts
            runtime_payload.update(
                {
                    "finished_at": finished_at.isoformat(),
                    "elapsed_seconds": round(elapsed, 3),
                    "elapsed_minutes": round(elapsed / 60.0, 3),
                    "status": "failed",
                }
            )
            _write_json(runtime_path, runtime_payload)
            raise


if __name__ == "__main__":
    main()
