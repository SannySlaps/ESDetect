#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import tifffile


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _resolve_run_dir(run_dir_arg: str) -> Path:
    run_dir = Path(run_dir_arg).expanduser().resolve()
    if not run_dir.exists():
        raise SystemExit(f"Run directory does not exist: {run_dir}")
    return run_dir


def _iter_source_tiffs(db: dict) -> list[Path]:
    data_paths = [Path(str(item)).expanduser().resolve() for item in db.get("data_path", [])]
    if not data_paths:
        raise SystemExit("suite2p_db.json does not contain any data_path entries.")
    source_dir = data_paths[0]
    if not source_dir.exists():
        raise SystemExit(f"Source TIFF directory does not exist: {source_dir}")
    tiffs = sorted(source_dir.glob("*.tif"))
    if not tiffs:
        raise SystemExit(f"No TIFF files were found in source directory: {source_dir}")
    return tiffs


def _background_suppress_stack(stack: np.ndarray, sigma: float, gain: float, q_low: float, q_high: float) -> np.ndarray:
    arr = np.asarray(stack)
    if arr.ndim == 2:
        arr = arr[np.newaxis, ...]
    arr32 = arr.astype(np.float32, copy=False)

    processed = np.empty_like(arr32, dtype=np.float32)
    for idx, frame in enumerate(arr32):
        background = cv2.GaussianBlur(frame, ksize=(0, 0), sigmaX=float(sigma), sigmaY=float(sigma), borderType=cv2.BORDER_REPLICATE)
        residual = np.clip(frame - background, 0.0, None)
        processed[idx] = residual * max(float(gain), 0.01)

    lo = float(np.percentile(processed, q_low))
    hi = float(np.percentile(processed, q_high))
    if not np.isfinite(lo):
        lo = 0.0
    if not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    scaled = np.clip((processed - lo) / (hi - lo), 0.0, 1.0)
    out = (scaled * np.iinfo(np.uint16).max).astype(np.uint16)
    return out[0] if stack.ndim == 2 else out


def _soma_blob_enhance_stack(
    stack: np.ndarray,
    sigma: float,
    gain: float,
    q_low: float,
    q_high: float,
    blob_sigma: float,
    blob_weight: float,
) -> np.ndarray:
    arr = np.asarray(stack)
    if arr.ndim == 2:
        arr = arr[np.newaxis, ...]
    arr32 = arr.astype(np.float32, copy=False)

    processed = np.empty_like(arr32, dtype=np.float32)
    short_sigma = max(float(blob_sigma), 0.5)
    long_sigma = max(float(blob_sigma) * 2.5, short_sigma + 0.5)
    blend = max(float(blob_weight), 0.0)

    for idx, frame in enumerate(arr32):
        background = cv2.GaussianBlur(
            frame,
            ksize=(0, 0),
            sigmaX=float(sigma),
            sigmaY=float(sigma),
            borderType=cv2.BORDER_REPLICATE,
        )
        residual = np.clip(frame - background, 0.0, None)

        # Difference-of-Gaussians favors compact blob-like structure over long thin processes.
        blur_small = cv2.GaussianBlur(
            residual,
            ksize=(0, 0),
            sigmaX=short_sigma,
            sigmaY=short_sigma,
            borderType=cv2.BORDER_REPLICATE,
        )
        blur_large = cv2.GaussianBlur(
            residual,
            ksize=(0, 0),
            sigmaX=long_sigma,
            sigmaY=long_sigma,
            borderType=cv2.BORDER_REPLICATE,
        )
        dog = np.clip(blur_small - blur_large, 0.0, None)
        combined = residual + blend * dog
        processed[idx] = combined * max(float(gain), 0.01)

    lo = float(np.percentile(processed, q_low))
    hi = float(np.percentile(processed, q_high))
    if not np.isfinite(lo):
        lo = 0.0
    if not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    scaled = np.clip((processed - lo) / (hi - lo), 0.0, 1.0)
    out = (scaled * np.iinfo(np.uint16).max).astype(np.uint16)
    return out[0] if stack.ndim == 2 else out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a background-suppressed TIFF input set for a prepared Suite2p run."
    )
    parser.add_argument("--run-dir", required=True, help="Prepared suite2p run directory.")
    parser.add_argument(
        "--method",
        choices=["background_subtract", "soma_blob_enhance"],
        default="background_subtract",
        help="Preprocessing recipe to apply before Suite2p detection.",
    )
    parser.add_argument(
        "--sigma",
        type=float,
        default=12.0,
        help="Gaussian sigma in pixels for low-frequency background subtraction.",
    )
    parser.add_argument(
        "--gain",
        type=float,
        default=1.5,
        help="Gain applied after background subtraction before robust normalization.",
    )
    parser.add_argument(
        "--q-low",
        type=float,
        default=0.5,
        help="Lower percentile for robust normalization of the processed stack.",
    )
    parser.add_argument(
        "--q-high",
        type=float,
        default=99.8,
        help="Upper percentile for robust normalization of the processed stack.",
    )
    parser.add_argument(
        "--blob-sigma",
        type=float,
        default=3.0,
        help="Small Gaussian sigma for blob enhancement when using soma_blob_enhance.",
    )
    parser.add_argument(
        "--blob-weight",
        type=float,
        default=1.5,
        help="Blend weight for blob enhancement when using soma_blob_enhance.",
    )
    parser.add_argument(
        "--apply-to-db",
        action="store_true",
        help="Rewrite suite2p_db.json data_path to the preprocessed TIFF directory for this run.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = _resolve_run_dir(args.run_dir)
    db_path = run_dir / "suite2p_db.json"
    if not db_path.exists():
        raise SystemExit(f"Missing suite2p_db.json: {db_path}")

    db = _load_json(db_path)
    source_tiffs = _iter_source_tiffs(db)
    source_dir = source_tiffs[0].parent

    if args.method == "background_subtract":
        method_name = f"bgsub_sigma{str(args.sigma).replace('.', 'p')}_gain{str(args.gain).replace('.', 'p')}"
    else:
        method_name = (
            f"somablob_sigma{str(args.sigma).replace('.', 'p')}"
            f"_gain{str(args.gain).replace('.', 'p')}"
            f"_blob{str(args.blob_sigma).replace('.', 'p')}"
            f"_w{str(args.blob_weight).replace('.', 'p')}"
        )
    output_dir = run_dir / "preprocessed_input" / method_name
    output_dir.mkdir(parents=True, exist_ok=True)

    written_files: list[str] = []
    for source_path in source_tiffs:
        stack = tifffile.imread(source_path)
        if args.method == "background_subtract":
            processed = _background_suppress_stack(
                stack=stack,
                sigma=float(args.sigma),
                gain=float(args.gain),
                q_low=float(args.q_low),
                q_high=float(args.q_high),
            )
        else:
            processed = _soma_blob_enhance_stack(
                stack=stack,
                sigma=float(args.sigma),
                gain=float(args.gain),
                q_low=float(args.q_low),
                q_high=float(args.q_high),
                blob_sigma=float(args.blob_sigma),
                blob_weight=float(args.blob_weight),
            )
        output_path = output_dir / source_path.name
        tifffile.imwrite(output_path, processed, photometric="minisblack")
        written_files.append(str(output_path))
        print(f"Processed {source_path.name} -> {output_path}")

    manifest = {
        "created_at": datetime.now().isoformat(),
        "run_dir": str(run_dir),
        "source_dir": str(source_dir),
        "output_dir": str(output_dir),
        "method": str(args.method),
        "sigma": float(args.sigma),
        "gain": float(args.gain),
        "blob_sigma": float(args.blob_sigma),
        "blob_weight": float(args.blob_weight),
        "q_low": float(args.q_low),
        "q_high": float(args.q_high),
        "source_tiff_count": len(source_tiffs),
        "written_tiffs": written_files,
        "db_data_path_rewritten": bool(args.apply_to_db),
    }
    _write_json(run_dir / "preprocessed_input_manifest.json", manifest)

    if args.apply_to_db:
        backup_path = run_dir / "suite2p_db.preprocessed_backup.json"
        if not backup_path.exists():
            _write_json(backup_path, db)
        db["data_path"] = [str(output_dir)]
        _write_json(db_path, db)
        print(f"Updated suite2p_db.json data_path to: {output_dir}")
        print(f"Original DB backup: {backup_path}")

    print(f"Preprocessed TIFF count: {len(written_files)}")
    print(f"Preprocessed input directory: {output_dir}")
    print(f"Manifest: {run_dir / 'preprocessed_input_manifest.json'}")


if __name__ == "__main__":
    main()
