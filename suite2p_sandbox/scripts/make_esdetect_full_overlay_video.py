#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
import tifffile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render an ESDetect raw TIFF overlay video using the current accepted masks in a plane0 folder.")
    parser.add_argument("--plane-dir", required=True, help="Path to active plane0 output folder.")
    parser.add_argument("--output", help="Optional mp4 output path.")
    parser.add_argument("--fps", type=float, default=20.0, help="Output video frames per second.")
    parser.add_argument("--frame-stride", type=int, default=1, help="Render every Nth frame for shorter review videos.")
    parser.add_argument("--sample-files", type=int, default=8, help="Number of TIFF files to sample for intensity scaling.")
    parser.add_argument("--sample-frames", type=int, default=24, help="Frames per sampled TIFF for intensity scaling.")
    parser.add_argument("--gain", type=float, default=1.0, help="Gain multiplier after percentile normalization.")
    return parser.parse_args()


def _load_plane(plane_dir: Path) -> tuple[dict, np.ndarray, np.ndarray | None]:
    ops = np.load(plane_dir / "ops.npy", allow_pickle=True).item()
    stat = np.load(plane_dir / "stat.npy", allow_pickle=True)
    iscell_path = plane_dir / "iscell.npy"
    iscell = np.load(iscell_path, allow_pickle=True) if iscell_path.exists() else None
    return ops, stat, iscell


def _accepted_mask(iscell: np.ndarray | None, n: int) -> np.ndarray | None:
    if iscell is None:
        return None
    if iscell.ndim == 2 and iscell.shape[0] == n:
        return np.asarray(iscell[:, 0] > 0.5, dtype=bool)
    if iscell.ndim == 1 and iscell.shape[0] == n:
        return np.asarray(iscell > 0.5, dtype=bool)
    return None


def _build_roi_edges(stat: np.ndarray, Ly: int, Lx: int, accepted: np.ndarray | None) -> np.ndarray:
    mask = np.zeros((Ly, Lx), dtype=np.uint8)
    for idx, roi in enumerate(stat):
        if accepted is not None and idx < accepted.shape[0] and not bool(accepted[idx]):
            continue
        ypix = np.asarray(roi.get("ypix", []), dtype=np.int32)
        xpix = np.asarray(roi.get("xpix", []), dtype=np.int32)
        valid = (ypix >= 0) & (ypix < Ly) & (xpix >= 0) & (xpix < Lx)
        mask[ypix[valid], xpix[valid]] = 255
    return cv2.Canny(mask, 50, 150)


def _sample_intensity_bounds(tiff_paths: list[Path], sample_files: int, sample_frames: int) -> tuple[float, float]:
    if not tiff_paths:
        raise SystemExit("No TIFF files available for sampling.")
    picks = np.linspace(0, len(tiff_paths) - 1, num=min(sample_files, len(tiff_paths)), dtype=int)
    chunks: list[np.ndarray] = []
    for idx in picks:
        arr = np.asarray(tifffile.imread(tiff_paths[int(idx)]), dtype=np.float32)
        if arr.ndim == 2:
            arr = arr[np.newaxis, ...]
        step = max(1, arr.shape[0] // max(1, sample_frames))
        chunks.append(arr[::step][:sample_frames])
    sample = np.concatenate(chunks, axis=0)
    low = float(np.percentile(sample, 5.0))
    high = float(np.percentile(sample, 99.5))
    if not np.isfinite(high) or high <= low:
        high = low + 1.0
    return low, high


def _normalize_frame(frame: np.ndarray, low: float, high: float, gain: float) -> np.ndarray:
    scaled = np.clip((frame.astype(np.float32) - low) / max(high - low, 1e-6), 0.0, 1.0)
    scaled = np.clip(scaled * float(gain), 0.0, 1.0)
    return (scaled * 255.0).astype(np.uint8)


def main() -> None:
    args = parse_args()
    plane_dir = Path(args.plane_dir).expanduser().resolve()
    if not plane_dir.exists():
        raise SystemExit(f"Plane dir does not exist: {plane_dir}")

    ops, stat, iscell = _load_plane(plane_dir)
    Ly = int(ops.get("Ly", 0))
    Lx = int(ops.get("Lx", 0))
    if Ly <= 0 or Lx <= 0:
        raise SystemExit("Could not determine image dimensions from ops.npy.")

    data_paths = [Path(p).expanduser().resolve() for p in ops.get("data_path", [])]
    if not data_paths:
        raise SystemExit("ops.npy does not include data_path.")
    raw_dir = data_paths[0]
    tiff_paths = sorted(raw_dir.glob("*.tif"))
    if not tiff_paths:
        tiff_paths = sorted(raw_dir.glob("*.tiff"))
    if not tiff_paths:
        raise SystemExit(f"No TIFF files found in {raw_dir}")

    accepted = _accepted_mask(iscell, len(stat))
    edges = _build_roi_edges(stat, Ly, Lx, accepted)
    low, high = _sample_intensity_bounds(tiff_paths, int(args.sample_files), int(args.sample_frames))

    output_path = Path(args.output).expanduser().resolve() if args.output else plane_dir / "suite2p_full_session_overlay.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), float(args.fps), (Lx, Ly))
    if not writer.isOpened():
        raise SystemExit(f"Could not open video writer for {output_path}")

    total_frames = 0
    rendered_frames = 0
    roi_count = int(accepted.sum()) if accepted is not None else len(stat)
    frame_stride = max(1, int(args.frame_stride))
    for tif_path in tiff_paths:
        stack = np.asarray(tifffile.imread(tif_path), dtype=np.float32)
        if stack.ndim == 2:
            stack = stack[np.newaxis, ...]
        for frame_idx, frame in enumerate(stack):
            global_idx = total_frames + frame_idx
            if global_idx % frame_stride != 0:
                continue
            gray8 = _normalize_frame(frame, low, high, float(args.gain))
            rgb = cv2.cvtColor(gray8, cv2.COLOR_GRAY2BGR)
            rgb[edges > 0] = (0, 255, 255)
            writer.write(rgb)
            rendered_frames += 1
        total_frames += int(stack.shape[0])

    writer.release()
    print(f"Overlay saved: {output_path}")
    print(f"TIFF files used: {len(tiff_paths)}")
    print(f"Source frames scanned: {total_frames}")
    print(f"Frames rendered: {rendered_frames}")
    print(f"Frame stride: {frame_stride}")
    print(f"Accepted ROI count: {roi_count}")


if __name__ == "__main__":
    main()
