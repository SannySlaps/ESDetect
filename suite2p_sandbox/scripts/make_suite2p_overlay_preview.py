#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a short Suite2p ROI-overlay preview video from a plane0 output folder."
    )
    parser.add_argument("--plane-dir", required=True, help="Path to a Suite2p plane0 output folder.")
    parser.add_argument("--start-frame", type=int, default=0, help="Frame index to start from.")
    parser.add_argument("--num-frames", type=int, default=600, help="Number of frames to render.")
    parser.add_argument("--fps", type=float, default=20.0, help="Output preview frames per second.")
    parser.add_argument(
        "--output",
        help="Optional output mp4 path. Defaults to suite2p_overlay_preview.mp4 inside the plane folder.",
    )
    return parser.parse_args()


def _load_ops(plane_dir: Path) -> dict:
    ops_path = plane_dir / "ops.npy"
    if not ops_path.exists():
        raise SystemExit(f"Missing ops.npy: {ops_path}")
    return np.load(ops_path, allow_pickle=True).item()


def _load_stat(plane_dir: Path) -> np.ndarray:
    stat_path = plane_dir / "stat.npy"
    if not stat_path.exists():
        raise SystemExit(f"Missing stat.npy: {stat_path}")
    return np.load(stat_path, allow_pickle=True)


def _load_iscell(plane_dir: Path, n_stat: int) -> np.ndarray | None:
    iscell_path = plane_dir / "iscell.npy"
    if not iscell_path.exists():
        return None
    iscell = np.load(iscell_path, allow_pickle=True)
    if iscell.ndim >= 2 and iscell.shape[0] == n_stat:
        return iscell[:, 0].astype(bool)
    if iscell.ndim == 1 and iscell.shape[0] == n_stat:
        return iscell.astype(bool)
    return None


def _build_roi_mask(stat: np.ndarray, Ly: int, Lx: int, iscell: np.ndarray | None) -> np.ndarray:
    mask = np.zeros((Ly, Lx), dtype=np.uint8)
    for idx, roi in enumerate(stat):
        if iscell is not None and idx < len(iscell) and not bool(iscell[idx]):
            continue
        ypix = np.asarray(roi["ypix"], dtype=np.int32)
        xpix = np.asarray(roi["xpix"], dtype=np.int32)
        valid = (ypix >= 0) & (ypix < Ly) & (xpix >= 0) & (xpix < Lx)
        mask[ypix[valid], xpix[valid]] = 255
    return mask


def _iter_registered_frames(reg_file: Path, Ly: int, Lx: int, start_frame: int, num_frames: int) -> np.ndarray:
    if not reg_file.exists():
        raise SystemExit(f"Registered binary not found: {reg_file}")
    frame_size = Ly * Lx
    data = np.memmap(reg_file, mode="r", dtype=np.int16)
    total_frames = data.size // frame_size
    if total_frames <= 0:
        raise SystemExit(f"No frames found in registered movie: {reg_file}")
    start = max(0, min(start_frame, total_frames - 1))
    stop = min(total_frames, start + max(1, num_frames))
    movie = data[start * frame_size : stop * frame_size].reshape((stop - start, Ly, Lx))
    return np.asarray(movie, dtype=np.float32)


def _normalize_frame(frame: np.ndarray, low: float, high: float) -> np.ndarray:
    scaled = np.clip((frame - low) / max(high - low, 1e-6), 0.0, 1.0)
    return (scaled * 255.0).astype(np.uint8)


def main() -> None:
    args = parse_args()
    plane_dir = Path(args.plane_dir).expanduser().resolve()
    if not plane_dir.exists():
        raise SystemExit(f"Plane dir does not exist: {plane_dir}")

    ops = _load_ops(plane_dir)
    stat = _load_stat(plane_dir)
    Ly = int(ops["Ly"])
    Lx = int(ops["Lx"])
    reg_file = Path(str(ops["reg_file"]))
    iscell = _load_iscell(plane_dir, len(stat))
    roi_mask = _build_roi_mask(stat, Ly, Lx, iscell)

    movie = _iter_registered_frames(reg_file, Ly, Lx, args.start_frame, args.num_frames)
    low = float(np.percentile(movie, 5))
    high = float(np.percentile(movie, 99.5))

    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else plane_dir / "suite2p_overlay_preview.mp4"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        float(args.fps),
        (Lx, Ly),
    )
    if not writer.isOpened():
        raise SystemExit(f"Could not open video writer for {output_path}")

    roi_edges = cv2.Canny(roi_mask, 50, 150)

    for frame in movie:
        gray8 = _normalize_frame(frame, low, high)
        rgb = cv2.cvtColor(gray8, cv2.COLOR_GRAY2BGR)
        rgb[roi_edges > 0] = (0, 255, 255)
        writer.write(rgb)

    writer.release()
    print(f"Overlay preview saved: {output_path}")
    print(f"Frames rendered: {movie.shape[0]}")
    print(f"ROI count used: {int(np.count_nonzero(roi_mask))} pixels across {len(stat)} detected ROIs")


if __name__ == "__main__":
    main()
