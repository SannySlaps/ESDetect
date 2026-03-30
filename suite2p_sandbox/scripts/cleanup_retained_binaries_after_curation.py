#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class RetainedCleanupEntry:
    session: str
    curation_status: str
    retained_root: str
    deleted_paths: list[str]
    skipped_reason: str


def _is_valid_session_folder(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    if not path.name.lower().startswith("session_"):
        return False
    metadata_file = path / "metadata" / "session_metadata.json"
    raw_dirs = [child for child in path.iterdir() if child.is_dir() and child.name.startswith("raw_")]
    return metadata_file.exists() and bool(raw_dirs)


def _find_sessions(root: Path) -> list[Path]:
    sessions: list[Path] = []
    for candidate in sorted(root.rglob("Session_*")):
        if _is_valid_session_folder(candidate):
            sessions.append(candidate.resolve())
    return sessions


def _load_curation_status(session: Path) -> str:
    status_path = session / "analysis" / "suite2p_curation_status.json"
    if not status_path.exists():
        return "not_started"
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return "not_started"
    return str(payload.get("status", "not_started") or "not_started").strip() or "not_started"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Delete retained Suite2p/ESDetect .bin files after curation, but only for sessions "
            "explicitly marked completed. Default is dry-run."
        )
    )
    parser.add_argument("--root", required=True, help="Project or cohort root to scan for Session_* folders.")
    parser.add_argument("--report-json", default="", help="Optional path to save a cleanup report JSON.")
    parser.add_argument("--apply", action="store_true", help="Actually delete retained_temp content.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Root path does not exist: {root}")

    sessions = _find_sessions(root)
    if not sessions:
        raise SystemExit(f"No valid Session_* folders found under: {root}")

    entries: list[RetainedCleanupEntry] = []
    deleted_dir_count = 0
    deleted_bin_count = 0

    for session in sessions:
        curation_status = _load_curation_status(session).lower()
        retained_root = session / "analysis" / "retained_temp"
        deleted_paths: list[str] = []
        skipped_reason = ""

        if curation_status != "completed":
            skipped_reason = f"curation_status_{curation_status}"
        elif not retained_root.exists():
            skipped_reason = "no_retained_temp"
        else:
            run_dirs = sorted([path for path in retained_root.iterdir() if path.is_dir()], key=lambda p: p.name)
            if not run_dirs:
                skipped_reason = "no_retained_run_dirs"
            else:
                for run_dir in run_dirs:
                    bin_files = list(run_dir.rglob("*.bin"))
                    if bin_files:
                        deleted_bin_count += len(bin_files)
                    deleted_paths.append(str(run_dir))
                    if args.apply:
                        shutil.rmtree(run_dir)
                        deleted_dir_count += 1

        entries.append(
            RetainedCleanupEntry(
                session=str(session),
                curation_status=curation_status,
                retained_root=str(retained_root),
                deleted_paths=deleted_paths,
                skipped_reason=skipped_reason,
            )
        )

    report = {
        "created_at": datetime.now().isoformat(),
        "root": str(root),
        "apply": bool(args.apply),
        "session_count": len(sessions),
        "deleted_run_dir_count": deleted_dir_count if args.apply else sum(len(entry.deleted_paths) for entry in entries),
        "deleted_bin_count_estimate": deleted_bin_count,
        "entries": [asdict(entry) for entry in entries],
    }

    if args.report_json:
        report_path = Path(args.report_json).expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report saved: {report_path}")

    print(f"Sessions scanned: {len(sessions)}")
    print(f"Apply mode: {bool(args.apply)}")
    print(
        "Eligible retained run dirs: "
        f"{sum(len(entry.deleted_paths) for entry in entries)}"
    )
    print(f"Estimated .bin files in eligible dirs: {deleted_bin_count}")
    for entry in entries:
        session_name = Path(entry.session).name
        if entry.deleted_paths:
            print(
                f"[{session_name}] retained_temp: "
                f"{'delete' if args.apply else 'would delete'} {len(entry.deleted_paths)} run dir(s)"
            )
        else:
            print(f"[{session_name}] retained_temp: skipped ({entry.skipped_reason})")


if __name__ == "__main__":
    main()
