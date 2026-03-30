#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import tifffile


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _resolve_session_dir(session_arg: str) -> Path:
    session_dir = Path(session_arg).expanduser().resolve()
    if not session_dir.exists():
        raise SystemExit(f"Session directory does not exist: {session_dir}")
    return session_dir


def _resolve_raw_dir(session_dir: Path) -> Path:
    raw_dirs = sorted([path for path in session_dir.iterdir() if path.is_dir() and path.name.startswith("raw_")])
    if not raw_dirs:
        raise SystemExit(f"No raw_* directory found under {session_dir}")
    return raw_dirs[-1]


def _iter_tiffs(raw_dir: Path) -> list[Path]:
    tiffs = sorted(raw_dir.glob("*.tif"))
    if not tiffs:
        raise SystemExit(f"No TIFF files found in {raw_dir}")
    return tiffs


def _derive_output_dir(session_dir: Path, label: str) -> Path:
    out_dir = session_dir / "analysis" / "ESDetect_segmentation" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _estimate_template(tiff_paths: list[Path], sample_files: int = 3, sample_frames: int = 40) -> np.ndarray:
    picks = np.linspace(0, len(tiff_paths) - 1, num=min(sample_files, len(tiff_paths)), dtype=int)
    frames: list[np.ndarray] = []
    for idx in picks:
        arr = np.asarray(tifffile.imread(tiff_paths[int(idx)]), dtype=np.float32)
        if arr.ndim == 2:
            arr = arr[np.newaxis, ...]
        step = max(1, arr.shape[0] // max(1, sample_frames))
        frames.append(arr[::step][:sample_frames])
    template = np.mean(np.concatenate(frames, axis=0), axis=0, dtype=np.float32)
    return template.astype(np.float32)


def _register_frame(frame: np.ndarray, template: np.ndarray, *, downsample: float, max_shift: float) -> tuple[np.ndarray, tuple[float, float]]:
    src = np.asarray(frame, dtype=np.float32)
    ref = np.asarray(template, dtype=np.float32)
    if downsample and 0.0 < downsample < 1.0:
        ref_small = cv2.resize(ref, None, fx=downsample, fy=downsample, interpolation=cv2.INTER_AREA)
        src_small = cv2.resize(src, None, fx=downsample, fy=downsample, interpolation=cv2.INTER_AREA)
        shift, _ = cv2.phaseCorrelate(ref_small, src_small)
        dx = float(shift[0]) / float(downsample)
        dy = float(shift[1]) / float(downsample)
    else:
        shift, _ = cv2.phaseCorrelate(ref, src)
        dx = float(shift[0])
        dy = float(shift[1])
    dx = float(np.clip(dx, -max_shift, max_shift))
    dy = float(np.clip(dy, -max_shift, max_shift))
    mat = np.array([[1.0, 0.0, -dx], [0.0, 1.0, -dy]], dtype=np.float32)
    registered = cv2.warpAffine(src, mat, (src.shape[1], src.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return registered, (dx, dy)


def _accumulate_summary_images(
    tiff_paths: list[Path],
    *,
    motion_correct: bool,
    registration_downsample: float,
    max_shift: float,
    reg_file_path: Path | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, float]]:
    mean_accum = None
    max_img = None
    mean_sq_accum = None
    topk_stack = None
    total_frames = 0
    topk = 24
    template = _estimate_template(tiff_paths) if motion_correct else None
    shifts: list[tuple[float, float]] = []
    reg_handle = reg_file_path.open("wb") if reg_file_path is not None else None
    for path in tiff_paths:
        stack = tifffile.imread(path)
        arr = np.asarray(stack, dtype=np.float32)
        if arr.ndim == 2:
            arr = arr[np.newaxis, ...]
        if motion_correct and template is not None:
            reg_frames = np.empty_like(arr, dtype=np.float32)
            for idx in range(arr.shape[0]):
                reg_frames[idx], shift = _register_frame(
                    arr[idx],
                    template,
                    downsample=float(registration_downsample),
                    max_shift=float(max_shift),
                )
                shifts.append(shift)
            arr = reg_frames
        if reg_handle is not None:
            np.clip(np.rint(arr), np.iinfo(np.int16).min, np.iinfo(np.int16).max).astype(np.int16).tofile(reg_handle)
        if mean_accum is None:
            mean_accum = np.zeros(arr.shape[1:], dtype=np.float64)
            max_img = np.zeros(arr.shape[1:], dtype=np.float32)
            mean_sq_accum = np.zeros(arr.shape[1:], dtype=np.float64)
        mean_accum += arr.sum(axis=0, dtype=np.float64)
        max_img = np.maximum(max_img, arr.max(axis=0))
        mean_sq_accum += np.square(arr, dtype=np.float64).sum(axis=0, dtype=np.float64)
        arr_sort = np.sort(arr, axis=0)
        topk_local = arr_sort[-min(topk, arr.shape[0]) :]
        if topk_stack is None:
            topk_stack = topk_local
        else:
            merged = np.concatenate([topk_stack, topk_local], axis=0)
            merged.sort(axis=0)
            topk_stack = merged[-topk:]
        total_frames += int(arr.shape[0])
    if mean_accum is None or max_img is None or mean_sq_accum is None or total_frames <= 0 or topk_stack is None:
        raise SystemExit("Failed to summarize TIFFs.")
    mean_img = (mean_accum / total_frames).astype(np.float32)
    variance = np.maximum(mean_sq_accum / total_frames - np.square(mean_img, dtype=np.float64), 0.0)
    std_img = np.sqrt(variance, dtype=np.float64).astype(np.float32)
    event_img = np.percentile(topk_stack, 90.0, axis=0).astype(np.float32)
    summary = {
        "motion_corrected": bool(motion_correct),
        "total_frames": int(total_frames),
        "mean_abs_shift": float(np.mean([abs(dx) + abs(dy) for dx, dy in shifts])) if shifts else 0.0,
        "max_abs_shift": float(np.max([max(abs(dx), abs(dy)) for dx, dy in shifts])) if shifts else 0.0,
        "reg_file": str(reg_file_path) if reg_file_path is not None else "",
    }
    if reg_handle is not None:
        reg_handle.close()
    return mean_img, max_img.astype(np.float32), std_img, event_img, summary


def _normalize_uint8(image: np.ndarray, q_low: float = 1.0, q_high: float = 99.5) -> np.ndarray:
    lo = float(np.percentile(image, q_low))
    hi = float(np.percentile(image, q_high))
    if not np.isfinite(lo):
        lo = 0.0
    if not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    scaled = np.clip((image - lo) / (hi - lo), 0.0, 1.0)
    return (scaled * 255.0).astype(np.uint8)


def _normalize_float(image: np.ndarray, q_low: float = 1.0, q_high: float = 99.5) -> np.ndarray:
    lo = float(np.percentile(image, q_low))
    hi = float(np.percentile(image, q_high))
    if not np.isfinite(lo):
        lo = 0.0
    if not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    return np.clip((image - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)


def _build_proposal_images(
    mean_img: np.ndarray,
    max_img: np.ndarray,
    std_img: np.ndarray,
    event_img: np.ndarray,
    bg_sigma: float,
    blob_sigma: float,
    blob_weight: float,
    transient_weight: float,
) -> dict[str, np.ndarray]:
    mean_bg = cv2.GaussianBlur(mean_img, (0, 0), sigmaX=bg_sigma, sigmaY=bg_sigma, borderType=cv2.BORDER_REPLICATE)
    max_bg = cv2.GaussianBlur(max_img, (0, 0), sigmaX=bg_sigma, sigmaY=bg_sigma, borderType=cv2.BORDER_REPLICATE)
    mean_resid = np.clip(mean_img - mean_bg, 0.0, None)
    max_resid = np.clip(max_img - max_bg, 0.0, None)
    proposal_base = 0.35 * mean_img + 0.65 * max_img
    proposal_residual = 0.35 * mean_resid + 0.65 * max_resid
    std_bg = cv2.GaussianBlur(std_img, (0, 0), sigmaX=bg_sigma, sigmaY=bg_sigma, borderType=cv2.BORDER_REPLICATE)
    std_resid = np.clip(std_img - std_bg, 0.0, None)
    blur_small = cv2.GaussianBlur(proposal_residual, (0, 0), sigmaX=blob_sigma, sigmaY=blob_sigma, borderType=cv2.BORDER_REPLICATE)
    blur_large = cv2.GaussianBlur(
        proposal_residual,
        (0, 0),
        sigmaX=max(blob_sigma * 2.5, blob_sigma + 0.5),
        sigmaY=max(blob_sigma * 2.5, blob_sigma + 0.5),
        borderType=cv2.BORDER_REPLICATE,
    )
    dog = np.clip(blur_small - blur_large, 0.0, None)
    proposal_soma_blob = proposal_residual + max(blob_weight, 0.0) * dog
    event_bg = cv2.GaussianBlur(event_img, (0, 0), sigmaX=bg_sigma, sigmaY=bg_sigma, borderType=cv2.BORDER_REPLICATE)
    event_resid = np.clip(event_img - event_bg, 0.0, None)
    transient_norm = 0.35 * _normalize_float(std_resid) + 0.25 * _normalize_float(max_resid) + 0.40 * _normalize_float(event_resid)
    proposal_transient = transient_norm.astype(np.float32)
    proposal_soma_blob_transient = (_normalize_float(proposal_soma_blob) + max(transient_weight, 0.0) * proposal_transient).astype(np.float32)
    return {
        "mean_image": mean_img,
        "max_image": max_img,
        "std_image": std_img,
        "event_image": event_img,
        "proposal_base": proposal_base,
        "proposal_residual": proposal_residual,
        "proposal_soma_blob": proposal_soma_blob,
        "proposal_transient": proposal_transient,
        "proposal_soma_blob_transient": proposal_soma_blob_transient,
    }


def _segment_with_watershed(
    proposal: np.ndarray,
    thresh_q: float,
    min_area: int,
    max_area: int,
    peak_fraction: float,
    dilate_iters: int,
) -> np.ndarray:
    norm = _normalize_uint8(proposal)
    smooth = cv2.GaussianBlur(norm, (0, 0), sigmaX=1.2, sigmaY=1.2, borderType=cv2.BORDER_REPLICATE)
    threshold = int(np.percentile(smooth, thresh_q))
    threshold = max(threshold, 1)
    binary = np.zeros_like(smooth, dtype=np.uint8)
    binary[smooth >= threshold] = 255
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    dist = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    sure_fg = np.zeros_like(binary)
    if np.max(dist) > 0:
        sure_fg[dist >= float(peak_fraction) * float(np.max(dist))] = 255
    sure_fg = cv2.morphologyEx(sure_fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    sure_bg = cv2.dilate(binary, np.ones((3, 3), np.uint8), iterations=1)
    unknown = cv2.subtract(sure_bg, sure_fg)

    _, markers = cv2.connectedComponents(sure_fg)
    markers = markers + 1
    markers[unknown == 255] = 0
    ws_input = cv2.cvtColor(smooth, cv2.COLOR_GRAY2BGR)
    markers = cv2.watershed(ws_input, markers)

    region_ids = sorted([idx for idx in np.unique(markers) if idx > 1])
    masks = []
    for region_id in region_ids:
        mask = (markers == region_id).astype(np.uint8)
        if dilate_iters > 0:
            mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=int(dilate_iters))
        area = int(mask.sum())
        if area < min_area or area > max_area:
            continue
        masks.append(mask)
    if not masks:
        return np.zeros((0, smooth.shape[0], smooth.shape[1]), dtype=np.uint8)
    return np.stack(masks, axis=0)


def _merge_masks(primary: np.ndarray, extras: list[np.ndarray]) -> np.ndarray:
    if primary.size == 0 and not extras:
        return np.zeros((0, 0, 0), dtype=np.uint8)
    masks = [primary[idx].astype(np.uint8) for idx in range(primary.shape[0])] if primary.size else []
    masks.extend(mask.astype(np.uint8) for mask in extras if np.any(mask))
    if not masks:
        shape = primary.shape[1:] if primary.ndim == 3 else (0, 0)
        return np.zeros((0, shape[0], shape[1]), dtype=np.uint8)
    return np.stack(masks, axis=0)


def _transient_hotspot_rescue(
    transient_map: np.ndarray,
    existing_masks: np.ndarray,
    *,
    min_area: int,
    max_area: int,
) -> tuple[list[np.ndarray], list[dict[str, float]]]:
    if transient_map.size == 0:
        return [], []

    occupied = np.zeros(transient_map.shape, dtype=np.uint8)
    if existing_masks.size:
        occupied = (existing_masks.max(axis=0) > 0).astype(np.uint8)

    candidate_map = transient_map.copy().astype(np.float32)
    candidate_map[occupied > 0] = 0.0
    candidate_map = cv2.GaussianBlur(candidate_map, (0, 0), sigmaX=2.0, sigmaY=2.0, borderType=cv2.BORDER_REPLICATE)

    nonzero = candidate_map[candidate_map > 0]
    if nonzero.size == 0:
        return [], []

    threshold = float(np.percentile(nonzero, 99.5))
    binary = np.zeros(candidate_map.shape, dtype=np.uint8)
    binary[candidate_map >= threshold] = 255
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    num_labels, labels, stats, cents = cv2.connectedComponentsWithStats(binary, 8)
    rescue_masks: list[np.ndarray] = []
    rescue_info: list[dict[str, float]] = []
    min_rescue_area = max(20, min_area // 2)
    max_rescue_area = max(max_area, min_area)

    for idx in range(1, num_labels):
        area = int(stats[idx, cv2.CC_STAT_AREA])
        if area < min_rescue_area or area > max_rescue_area:
            continue
        region = (labels == idx).astype(np.uint8)
        region = cv2.dilate(region, np.ones((3, 3), np.uint8), iterations=2)
        final_area = int(region.sum())
        if final_area < min_rescue_area or final_area > max_rescue_area:
            continue
        overlap = float((region.astype(bool) & occupied.astype(bool)).sum()) / max(1, final_area)
        if overlap > 0.15:
            continue
        rescue_masks.append(region)
        rescue_info.append(
            {
                "label": int(idx),
                "area": final_area,
                "threshold": threshold,
                "centroid_x": float(cents[idx][0]),
                "centroid_y": float(cents[idx][1]),
                "peak_value": float(candidate_map[labels == idx].max()),
            }
        )

    return rescue_masks, rescue_info


def _draw_mask_overlay(background: np.ndarray, masks: np.ndarray) -> np.ndarray:
    canvas = cv2.cvtColor(_normalize_uint8(background), cv2.COLOR_GRAY2BGR)
    color_arr = np.array([0, 255, 255], dtype=np.uint8)
    for idx in range(masks.shape[0]):
        mask = masks[idx].astype(bool)
        if not np.any(mask):
            continue
        canvas[mask] = np.clip(canvas[mask] * 0.45 + color_arr * 0.55, 0, 255).astype(np.uint8)
        contours, _ = cv2.findContours(masks[idx], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(canvas, contours, -1, (0, 255, 255), 1, lineType=cv2.LINE_AA)
    return canvas


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prototype residual/blob-based soma segmentation with watershed.")
    parser.add_argument("--session", required=True, help="Session directory, e.g. ...\\Session_002")
    parser.add_argument("--label", default="session002_trial01_somablob_watershed", help="Output label under analysis\\ESDetect_segmentation")
    parser.add_argument(
        "--source-image",
        choices=["proposal_soma_blob", "proposal_residual", "proposal_transient", "proposal_soma_blob_transient"],
        default="proposal_soma_blob_transient",
    )
    parser.add_argument("--bg-sigma", type=float, default=12.0)
    parser.add_argument("--blob-sigma", type=float, default=3.0)
    parser.add_argument("--blob-weight", type=float, default=1.5)
    parser.add_argument("--transient-weight", type=float, default=1.4, help="Weight for transient-sensitive proposal contribution.")
    parser.add_argument("--thresh-q", type=float, default=93.8, help="Percentile threshold for segmentation seed mask.")
    parser.add_argument("--peak-fraction", type=float, default=0.33, help="Distance-transform fraction for watershed markers.")
    parser.add_argument("--min-area", type=int, default=80)
    parser.add_argument("--max-area", type=int, default=900)
    parser.add_argument("--dilate-iters", type=int, default=0, help="Optional post-watershed dilation iterations.")
    parser.add_argument("--motion-correct", action="store_true", help="Apply rigid phase-correlation motion correction before summary building.")
    parser.add_argument("--registration-downsample", type=float, default=0.5, help="Downsample factor for rigid registration estimation.")
    parser.add_argument("--max-shift", type=float, default=15.0, help="Maximum absolute shift in pixels allowed during rigid registration.")
    parser.add_argument("--reg-file", help="Optional output path for motion-corrected registered movie binary.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    session_dir = _resolve_session_dir(args.session)
    raw_dir = _resolve_raw_dir(session_dir)
    tiff_paths = _iter_tiffs(raw_dir)
    out_dir = _derive_output_dir(session_dir, args.label)
    images_dir = out_dir / "images"
    arrays_dir = out_dir / "arrays"
    images_dir.mkdir(parents=True, exist_ok=True)
    arrays_dir.mkdir(parents=True, exist_ok=True)
    reg_file_path = Path(args.reg_file).expanduser().resolve() if args.reg_file else (out_dir / "reg.bin")
    reg_file_path.parent.mkdir(parents=True, exist_ok=True)

    mean_img, max_img, std_img, event_img, registration_summary = _accumulate_summary_images(
        tiff_paths,
        motion_correct=bool(args.motion_correct),
        registration_downsample=float(args.registration_downsample),
        max_shift=float(args.max_shift),
        reg_file_path=reg_file_path,
    )
    proposal_images = _build_proposal_images(
        mean_img=mean_img,
        max_img=max_img,
        std_img=std_img,
        event_img=event_img,
        bg_sigma=float(args.bg_sigma),
        blob_sigma=float(args.blob_sigma),
        blob_weight=float(args.blob_weight),
        transient_weight=float(args.transient_weight),
    )
    source = proposal_images[str(args.source_image)]
    masks = _segment_with_watershed(
        proposal=source,
        thresh_q=float(args.thresh_q),
        min_area=int(args.min_area),
        max_area=int(args.max_area),
        peak_fraction=float(args.peak_fraction),
        dilate_iters=int(args.dilate_iters),
    )
    rescue_masks, rescue_info = _transient_hotspot_rescue(
        proposal_images["proposal_transient"],
        masks,
        min_area=int(args.min_area),
        max_area=int(args.max_area),
    )
    masks = _merge_masks(masks, rescue_masks)
    overlay = _draw_mask_overlay(proposal_images["proposal_base"], masks)

    for name, image in proposal_images.items():
        cv2.imwrite(str(images_dir / f"{name}.png"), _normalize_uint8(image))
        np.save(arrays_dir / f"{name}.npy", image.astype(np.float32))
    cv2.imwrite(str(images_dir / "watershed_masks_overlay.png"), overlay)
    np.save(arrays_dir / "watershed_masks.npy", masks)
    if rescue_masks:
        np.save(arrays_dir / "rescue_masks.npy", np.stack(rescue_masks, axis=0))
    else:
        np.save(arrays_dir / "rescue_masks.npy", np.zeros((0, source.shape[0], source.shape[1]), dtype=np.uint8))

    manifest = {
        "created_at": datetime.now().isoformat(),
        "session_dir": str(session_dir),
        "raw_dir": str(raw_dir),
        "output_dir": str(out_dir),
        "raw_tiffs": [str(path) for path in tiff_paths],
        "source_image": str(args.source_image),
        "bg_sigma": float(args.bg_sigma),
        "blob_sigma": float(args.blob_sigma),
        "blob_weight": float(args.blob_weight),
        "transient_weight": float(args.transient_weight),
        "thresh_q": float(args.thresh_q),
        "peak_fraction": float(args.peak_fraction),
        "min_area": int(args.min_area),
        "max_area": int(args.max_area),
        "dilate_iters": int(args.dilate_iters),
        "motion_correct": bool(args.motion_correct),
        "registration_downsample": float(args.registration_downsample),
        "max_shift": float(args.max_shift),
        "mask_count": int(masks.shape[0]),
        "rescue_mask_count": int(len(rescue_masks)),
        "rescue_masks": rescue_info,
        "registration_summary": registration_summary,
    }
    _write_json(out_dir / "segmentation_manifest.json", manifest)

    print(f"Session: {session_dir}")
    print(f"Output directory: {out_dir}")
    print(f"Source image: {args.source_image}")
    print(f"Mask count: {masks.shape[0]}")
    print(f"Overlay: {images_dir / 'watershed_masks_overlay.png'}")


if __name__ == "__main__":
    main()
