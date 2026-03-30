#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class CleanupEntry:
    session: str
    category: str
    kept: list[str]
    deleted: list[str]


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


def _sorted_dirs(root: Path, predicate) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        [path for path in root.iterdir() if path.is_dir() and predicate(path)],
        key=lambda p: (p.stat().st_mtime, p.name),
        reverse=True,
    )


def _match_archive(session_name: str):
    prefix = f"{session_name}_"
    return lambda path: path.name.startswith(prefix)


def _match_run(session_name: str):
    prefix = f"{session_name}_ESDetect_"
    return lambda path: path.name.startswith(prefix)


def _match_segmentation(session_name: str):
    prefix = f"{session_name}_ESDetect_"
    return lambda path: path.name.startswith(prefix) and path.name.endswith("_segmentation")


def _match_extraction(session_name: str):
    prefix = f"{session_name}_ESDetect_"
    return lambda path: path.name.startswith(prefix) and path.name.endswith("_extract")


def _match_packaged(session_name: str):
    prefix = f"{session_name}_ESDetect_"
    return lambda path: path.name.startswith(prefix) and path.name.endswith("_package")


def _prune_group(
    *,
    session: Path,
    category: str,
    candidates: list[Path],
    keep: int,
    apply: bool,
) -> CleanupEntry:
    kept = candidates[:keep] if keep > 0 else []
    deleted = candidates[keep:] if keep > 0 else candidates
    if apply:
        for path in deleted:
            shutil.rmtree(path)
    return CleanupEntry(
        session=str(session),
        category=category,
        kept=[str(path) for path in kept],
        deleted=[str(path) for path in deleted],
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prune older ESDetect intermediates per session while keeping the active plane0 untouched. "
            "By default this is a dry run."
        )
    )
    parser.add_argument("--root", required=True, help="Project or cohort root to scan for Session_* folders.")
    parser.add_argument("--keep-archives", type=int, default=1, help="How many archived output folders to keep per session.")
    parser.add_argument("--keep-runs", type=int, default=1, help="How many ESDetect_runs folders to keep per session.")
    parser.add_argument("--keep-segmentation", type=int, default=1, help="How many ESDetect_segmentation folders to keep per session.")
    parser.add_argument("--keep-extraction", type=int, default=1, help="How many ESDetect_extraction folders to keep per session.")
    parser.add_argument("--keep-packaged", type=int, default=1, help="How many ESDetect_packaged folders to keep per session.")
    parser.add_argument("--report-json", default="", help="Optional path to save a cleanup report JSON.")
    parser.add_argument("--apply", action="store_true", help="Actually delete folders. Without this flag, reports only.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Root path does not exist: {root}")

    sessions = _find_sessions(root)
    if not sessions:
        raise SystemExit(f"No valid Session_* folders found under: {root}")

    entries: list[CleanupEntry] = []
    deleted_total = 0
    for session in sessions:
        session_name = session.name
        archives_root = session / "analysis" / "archived_outputs"
        runs_root = session / "ESDetect_runs"
        seg_root = session / "analysis" / "ESDetect_segmentation"
        ext_root = session / "analysis" / "ESDetect_extraction"
        pkg_root = session / "analysis" / "ESDetect_packaged"

        groups = [
            ("archived_outputs", _sorted_dirs(archives_root, _match_archive(session_name)), int(args.keep_archives)),
            ("ESDetect_runs", _sorted_dirs(runs_root, _match_run(session_name)), int(args.keep_runs)),
            ("ESDetect_segmentation", _sorted_dirs(seg_root, _match_segmentation(session_name)), int(args.keep_segmentation)),
            ("ESDetect_extraction", _sorted_dirs(ext_root, _match_extraction(session_name)), int(args.keep_extraction)),
            ("ESDetect_packaged", _sorted_dirs(pkg_root, _match_packaged(session_name)), int(args.keep_packaged)),
        ]
        for category, candidates, keep_count in groups:
            entry = _prune_group(
                session=session,
                category=category,
                candidates=candidates,
                keep=keep_count,
                apply=bool(args.apply),
            )
            deleted_total += len(entry.deleted)
            entries.append(entry)

    report = {
        "created_at": datetime.now().isoformat(),
        "root": str(root),
        "apply": bool(args.apply),
        "session_count": len(sessions),
        "deleted_folder_count": deleted_total,
        "entries": [asdict(entry) for entry in entries],
    }

    if args.report_json:
        report_path = Path(args.report_json).expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report saved: {report_path}")

    print(f"Sessions scanned: {len(sessions)}")
    print(f"Apply mode: {bool(args.apply)}")
    print(f"Folders selected for deletion: {deleted_total}")
    for entry in entries:
        if entry.deleted:
            print(f"[{Path(entry.session).name}] {entry.category}: delete {len(entry.deleted)} | keep {len(entry.kept)}")


if __name__ == "__main__":
    main()
