#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import cv2
import matplotlib
import numpy as np
import tifffile

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _resolve_segmentation_dir(path_arg: str) -> Path:
    path = Path(path_arg).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"Segmentation directory does not exist: {path}")
    return path


def _resolve_output_dir(segmentation_dir: Path, label: str) -> Path:
    session_dir = Path(_load_json(segmentation_dir / "segmentation_manifest.json")["session_dir"])
    out_dir = session_dir / "analysis" / "ESDetect_extraction" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _resolve_frame_rate(session_dir: Path) -> float:
    metadata_path = session_dir / "metadata" / "session_metadata.json"
    metadata = _load_json(metadata_path)
    return float(metadata.get("acquired_frame_rate_hz") or metadata.get("frame_rate_hz") or 0.0)


def _resolve_raw_tiffs(raw_dir: Path) -> list[Path]:
    tiffs = sorted(raw_dir.glob("*.tif"))
    if not tiffs:
        raise SystemExit(f"No TIFF files found in {raw_dir}")
    return tiffs


def _build_neuropil_masks(masks: np.ndarray, inner_iters: int, outer_iters: int) -> np.ndarray:
    union = (masks.sum(axis=0) > 0).astype(np.uint8)
    kernel = np.ones((3, 3), np.uint8)
    neuropil_masks = np.zeros_like(masks, dtype=np.uint8)
    for idx in range(masks.shape[0]):
        roi = masks[idx].astype(np.uint8)
        inner = cv2.dilate(roi, kernel, iterations=int(inner_iters))
        outer = cv2.dilate(roi, kernel, iterations=int(outer_iters))
        ring = np.clip(outer - inner, 0, 1).astype(np.uint8)
        ring[union > 0] = 0
        neuropil_masks[idx] = ring
    return neuropil_masks


def _mask_stats(mask: np.ndarray) -> dict:
    ys, xs = np.where(mask > 0)
    if ys.size == 0:
        return {"npix": 0, "med": [0.0, 0.0], "radius": 0.0}
    med_y = float(np.median(ys))
    med_x = float(np.median(xs))
    area = int(ys.size)
    radius = float(np.sqrt(area / np.pi))
    return {"npix": area, "med": [med_y, med_x], "radius": radius}


def _extract_stack_traces(stack: np.ndarray, masks: np.ndarray) -> np.ndarray:
    n_masks = masks.shape[0]
    n_frames = stack.shape[0]
    traces = np.zeros((n_masks, n_frames), dtype=np.float32)
    for idx in range(n_masks):
        ys, xs = np.where(masks[idx] > 0)
        if ys.size == 0:
            continue
        traces[idx] = stack[:, ys, xs].mean(axis=1, dtype=np.float32)
    return traces


def _compute_dff(F: np.ndarray, Fneu: np.ndarray, neuropil_coeff: float, percentile: float) -> np.ndarray:
    corrected = F - float(neuropil_coeff) * Fneu
    baseline = np.percentile(corrected, float(percentile), axis=1, keepdims=True)
    baseline = np.maximum(baseline, 1e-6)
    return (corrected - baseline) / baseline


def _plot_preview(dff: np.ndarray, fs: float, out_path: Path) -> None:
    n = min(6, dff.shape[0])
    if n <= 0:
        return
    fig, axes = plt.subplots(n, 1, figsize=(10, 1.8 * n), sharex=True)
    if n == 1:
        axes = [axes]
    x = np.arange(dff.shape[1], dtype=np.float32)
    if fs > 0:
        x = x / fs
    for idx in range(n):
        axes[idx].plot(x, dff[idx], color="#d97706", linewidth=0.8)
        axes[idx].set_ylabel(f"ROI {idx}")
    axes[-1].set_xlabel("Time (s)" if fs > 0 else "Frame")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prototype trace extraction from external soma segmentation masks.")
    parser.add_argument("--segmentation-dir", required=True, help="Path to an ESDetect_segmentation trial folder.")
    parser.add_argument("--label", default="trial14_extract01", help="Output label under analysis\\ESDetect_extraction")
    parser.add_argument("--mask-file", default="watershed_masks.npy", help="Mask array filename inside the segmentation arrays folder.")
    parser.add_argument("--inner-iters", type=int, default=4, help="Inner dilation iterations before neuropil ring starts.")
    parser.add_argument("--outer-iters", type=int, default=12, help="Outer dilation iterations for neuropil ring.")
    parser.add_argument("--neuropil-coeff", type=float, default=0.7, help="Neuropil subtraction coefficient for dF/F.")
    parser.add_argument("--baseline-percentile", type=float, default=20.0, help="Baseline percentile for dF/F.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    segmentation_dir = _resolve_segmentation_dir(args.segmentation_dir)
    manifest = _load_json(segmentation_dir / "segmentation_manifest.json")
    session_dir = Path(manifest["session_dir"])
    raw_dir = Path(manifest["raw_dir"])
    out_dir = _resolve_output_dir(segmentation_dir, args.label)
    arrays_dir = out_dir / "arrays"
    images_dir = out_dir / "images"
    arrays_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    mask_path = segmentation_dir / "arrays" / args.mask_file
    masks = np.load(mask_path).astype(np.uint8)
    neuropil_masks = _build_neuropil_masks(
        masks=masks,
        inner_iters=int(args.inner_iters),
        outer_iters=int(args.outer_iters),
    )
    tiff_paths = _resolve_raw_tiffs(raw_dir)
    fs = _resolve_frame_rate(session_dir)

    F_parts = []
    Fneu_parts = []
    total_frames = 0
    for tif_path in tiff_paths:
        stack = tifffile.imread(tif_path)
        arr = np.asarray(stack, dtype=np.float32)
        if arr.ndim == 2:
            arr = arr[np.newaxis, ...]
        F_parts.append(_extract_stack_traces(arr, masks))
        Fneu_parts.append(_extract_stack_traces(arr, neuropil_masks))
        total_frames += int(arr.shape[0])

    F = np.concatenate(F_parts, axis=1) if F_parts else np.zeros((masks.shape[0], 0), dtype=np.float32)
    Fneu = np.concatenate(Fneu_parts, axis=1) if Fneu_parts else np.zeros((masks.shape[0], 0), dtype=np.float32)
    dff = _compute_dff(
        F=F,
        Fneu=Fneu,
        neuropil_coeff=float(args.neuropil_coeff),
        percentile=float(args.baseline_percentile),
    )

    np.save(arrays_dir / "F.npy", F)
    np.save(arrays_dir / "Fneu.npy", Fneu)
    np.save(arrays_dir / "dff.npy", dff)
    np.save(arrays_dir / "roi_masks.npy", masks)
    np.save(arrays_dir / "neuropil_masks.npy", neuropil_masks)

    roi_stats = [_mask_stats(mask) for mask in masks]
    extraction_manifest = {
        "created_at": datetime.now().isoformat(),
        "session_dir": str(session_dir),
        "segmentation_dir": str(segmentation_dir),
        "raw_dir": str(raw_dir),
        "output_dir": str(out_dir),
        "frame_rate_hz": fs,
        "roi_count": int(masks.shape[0]),
        "total_frames": int(total_frames),
        "inner_iters": int(args.inner_iters),
        "outer_iters": int(args.outer_iters),
        "neuropil_coeff": float(args.neuropil_coeff),
        "baseline_percentile": float(args.baseline_percentile),
        "mask_file": str(mask_path.name),
        "roi_stats": roi_stats,
    }
    _write_json(out_dir / "extraction_manifest.json", extraction_manifest)
    _plot_preview(dff=dff, fs=fs, out_path=images_dir / "dff_preview.png")

    print(f"Segmentation source: {segmentation_dir}")
    print(f"Output directory: {out_dir}")
    print(f"ROI count: {masks.shape[0]}")
    print(f"Total frames: {total_frames}")
    print(f"Frame rate (Hz): {fs}")
    print(f"F.npy: {arrays_dir / 'F.npy'}")
    print(f"Fneu.npy: {arrays_dir / 'Fneu.npy'}")
    print(f"dF/F preview: {images_dir / 'dff_preview.png'}")


if __name__ == "__main__":
    main()
