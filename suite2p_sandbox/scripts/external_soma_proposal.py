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
    raw_dirs = sorted(
        [path for path in session_dir.iterdir() if path.is_dir() and path.name.startswith("raw_")]
    )
    if not raw_dirs:
        raise SystemExit(f"No raw_* directory found under {session_dir}")
    return raw_dirs[-1]


def _iter_tiffs(raw_dir: Path) -> list[Path]:
    tiffs = sorted(raw_dir.glob("*.tif"))
    if not tiffs:
        raise SystemExit(f"No TIFF files found in {raw_dir}")
    return tiffs


def _derive_output_dir(session_dir: Path, label: str) -> Path:
    base_dir = session_dir / "analysis" / "ESDetect_proposals" / label
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _accumulate_summary_images(
    tiff_paths: list[Path],
    max_tiffs: int | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    selected = tiff_paths[: max_tiffs or len(tiff_paths)]
    mean_accum = None
    sumsq_accum = None
    global_max = None
    total_frames = 0

    for path in selected:
        stack = tifffile.imread(path)
        arr = np.asarray(stack, dtype=np.float32)
        if arr.ndim == 2:
            arr = arr[np.newaxis, ...]
        if mean_accum is None:
            mean_accum = np.zeros(arr.shape[1:], dtype=np.float64)
            sumsq_accum = np.zeros(arr.shape[1:], dtype=np.float64)
            global_max = np.zeros(arr.shape[1:], dtype=np.float32)
        mean_accum += arr.sum(axis=0, dtype=np.float64)
        sumsq_accum += np.square(arr, dtype=np.float64).sum(axis=0, dtype=np.float64)
        global_max = np.maximum(global_max, arr.max(axis=0))
        total_frames += int(arr.shape[0])

    if mean_accum is None or sumsq_accum is None or global_max is None or total_frames <= 0:
        raise SystemExit("Failed to accumulate summary images from the raw TIFFs.")

    mean_img = (mean_accum / total_frames).astype(np.float32)
    var_img = np.maximum((sumsq_accum / total_frames) - np.square(mean_img, dtype=np.float64), 0.0).astype(np.float32)
    std_img = np.sqrt(var_img, dtype=np.float32)
    max_img = global_max.astype(np.float32)
    return mean_img, max_img, std_img, total_frames


def _normalize_uint8(image: np.ndarray, q_low: float = 1.0, q_high: float = 99.5) -> np.ndarray:
    lo = float(np.percentile(image, q_low))
    hi = float(np.percentile(image, q_high))
    if not np.isfinite(lo):
        lo = 0.0
    if not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    scaled = np.clip((image - lo) / (hi - lo), 0.0, 1.0)
    return (scaled * 255.0).astype(np.uint8)


def _build_proposal_images(
    mean_img: np.ndarray,
    max_img: np.ndarray,
    std_img: np.ndarray,
    bg_sigma: float,
    blob_sigma: float,
    blob_weight: float,
) -> dict[str, np.ndarray]:
    mean_bg = cv2.GaussianBlur(
        mean_img,
        ksize=(0, 0),
        sigmaX=bg_sigma,
        sigmaY=bg_sigma,
        borderType=cv2.BORDER_REPLICATE,
    )
    max_bg = cv2.GaussianBlur(
        max_img,
        ksize=(0, 0),
        sigmaX=bg_sigma,
        sigmaY=bg_sigma,
        borderType=cv2.BORDER_REPLICATE,
    )
    mean_resid = np.clip(mean_img - mean_bg, 0.0, None)
    max_resid = np.clip(max_img - max_bg, 0.0, None)

    proposal_base = 0.35 * mean_img + 0.65 * max_img
    proposal_resid = 0.35 * mean_resid + 0.65 * max_resid
    std_bg = cv2.GaussianBlur(
        std_img,
        ksize=(0, 0),
        sigmaX=bg_sigma,
        sigmaY=bg_sigma,
        borderType=cv2.BORDER_REPLICATE,
    )
    temporal_resid = np.clip(std_img - std_bg, 0.0, None)

    blur_small = cv2.GaussianBlur(
        proposal_resid,
        ksize=(0, 0),
        sigmaX=blob_sigma,
        sigmaY=blob_sigma,
        borderType=cv2.BORDER_REPLICATE,
    )
    blur_large = cv2.GaussianBlur(
        proposal_resid,
        ksize=(0, 0),
        sigmaX=max(blob_sigma * 2.5, blob_sigma + 0.5),
        sigmaY=max(blob_sigma * 2.5, blob_sigma + 0.5),
        borderType=cv2.BORDER_REPLICATE,
    )
    dog = np.clip(blur_small - blur_large, 0.0, None)
    soma_blob = proposal_resid + max(blob_weight, 0.0) * dog

    return {
        "mean_image": mean_img,
        "max_image": max_img,
        "std_image": std_img,
        "proposal_base": proposal_base,
        "proposal_residual": proposal_resid,
        "proposal_soma_blob": soma_blob,
        "proposal_temporal_residual": temporal_resid,
    }


def _detect_candidates(
    proposal: np.ndarray,
    peak_q: float,
    min_distance: int,
    max_candidates: int,
    min_area: int,
    max_area: int,
    min_circularity: float,
    split_distance: int,
    max_peaks_per_component: int,
) -> list[dict]:
    norm = _normalize_uint8(proposal)
    smooth = cv2.GaussianBlur(norm, (0, 0), sigmaX=1.2, sigmaY=1.2, borderType=cv2.BORDER_REPLICATE)
    threshold = int(np.percentile(smooth, peak_q))
    threshold = max(threshold, 1)
    binary = np.zeros_like(smooth, dtype=np.uint8)
    binary[smooth >= threshold] = 255
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)

    raw_candidates: list[dict] = []
    for label_idx in range(1, num_labels):
        area = int(stats[label_idx, cv2.CC_STAT_AREA])
        if area < min_area or area > max_area:
            continue

        component_mask = labels == label_idx
        ys, xs = np.where(component_mask)
        if ys.size == 0:
            continue

        contour_mask = component_mask.astype(np.uint8)
        contours, _ = cv2.findContours(contour_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        perimeter = float(cv2.arcLength(contours[0], True))
        circularity = 0.0 if perimeter <= 0.0 else float(4.0 * np.pi * area / (perimeter * perimeter))
        if circularity < min_circularity:
            continue

        comp_vals = smooth[component_mask].astype(np.float32)
        mean_score = float(np.mean(comp_vals))

        # Allow broad, soma-like connected regions to contribute multiple local peaks.
        component_img = np.zeros_like(smooth, dtype=np.uint8)
        component_img[component_mask] = smooth[component_mask]
        split_kernel = np.ones((max(3, 2 * int(split_distance) + 1), max(3, 2 * int(split_distance) + 1)), np.uint8)
        local_max = cv2.dilate(component_img, split_kernel)
        peak_mask = (component_img == local_max) & (component_img > 0)
        peak_ys, peak_xs = np.where(peak_mask)
        if peak_ys.size == 0:
            peak_ys, peak_xs = ys, xs

        peak_scores = smooth[peak_ys, peak_xs].astype(np.float32)
        peak_order = np.argsort(peak_scores)[::-1]
        taken_local: list[tuple[int, int]] = []
        local_dist_sq = float(split_distance * split_distance)
        peaks_added = 0

        for peak_idx in peak_order:
            y = int(peak_ys[int(peak_idx)])
            x = int(peak_xs[int(peak_idx)])
            if any((y - py) ** 2 + (x - px) ** 2 < local_dist_sq for py, px in taken_local):
                continue
            peak_score = float(smooth[y, x])
            score = peak_score * (0.6 + 0.4 * circularity) + 0.15 * mean_score
            raw_candidates.append(
                {
                    "y": y,
                    "x": x,
                    "score": score,
                    "peak_score": peak_score,
                    "mean_score": mean_score,
                    "area": area,
                    "circularity": circularity,
                    "component_id": label_idx,
                }
            )
            taken_local.append((y, x))
            peaks_added += 1
            if peaks_added >= max_peaks_per_component:
                break

    if not raw_candidates:
        return []

    order = np.argsort([cand["score"] for cand in raw_candidates])[::-1]
    picked: list[dict] = []
    taken = []
    min_dist_sq = float(min_distance * min_distance)

    for idx in order:
        candidate = raw_candidates[int(idx)]
        y = int(candidate["y"])
        x = int(candidate["x"])
        if any((y - py) ** 2 + (x - px) ** 2 < min_dist_sq for py, px in taken):
            continue
        picked.append(candidate)
        taken.append((y, x))
        if len(picked) >= max_candidates:
            break
    return picked


def _merge_rescue_candidates(
    primary_candidates: list[dict],
    rescue_candidates: list[dict],
    min_distance: int,
    max_total: int,
) -> list[dict]:
    merged = list(primary_candidates)
    min_dist_sq = float(min_distance * min_distance)
    taken = [(int(c["y"]), int(c["x"])) for c in merged]
    for candidate in rescue_candidates:
        y = int(candidate["y"])
        x = int(candidate["x"])
        if any((y - py) ** 2 + (x - px) ** 2 < min_dist_sq for py, px in taken):
            continue
        tagged = dict(candidate)
        tagged["rescue"] = True
        merged.append(tagged)
        taken.append((y, x))
        if len(merged) >= max_total:
            break
    return merged


def _draw_candidate_overlay(
    background: np.ndarray,
    candidates: list[dict],
    radius: int,
) -> np.ndarray:
    canvas = cv2.cvtColor(_normalize_uint8(background), cv2.COLOR_GRAY2BGR)
    for idx, candidate in enumerate(candidates):
        center = (int(candidate["x"]), int(candidate["y"]))
        is_rescue = bool(candidate.get("rescue"))
        color = (0, 165, 255) if is_rescue else (0, 255, 255)
        cv2.circle(canvas, center, int(radius), color, 1, lineType=cv2.LINE_AA)
        cv2.circle(canvas, center, 1, color, -1, lineType=cv2.LINE_AA)
        cv2.putText(
            canvas,
            str(idx),
            (center[0] + radius + 2, center[1]),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            color,
            1,
            lineType=cv2.LINE_AA,
        )
    return canvas


def _build_circular_masks(shape: tuple[int, int], candidates: list[dict], radius: int) -> np.ndarray:
    masks = np.zeros((len(candidates), shape[0], shape[1]), dtype=np.uint8)
    for idx, candidate in enumerate(candidates):
        center = (int(candidate["x"]), int(candidate["y"]))
        cv2.circle(masks[idx], center, int(radius), 1, -1, lineType=cv2.LINE_AA)
    return masks


def _draw_mask_overlay(background: np.ndarray, masks: np.ndarray, color: tuple[int, int, int] = (0, 255, 255)) -> np.ndarray:
    canvas = cv2.cvtColor(_normalize_uint8(background), cv2.COLOR_GRAY2BGR)
    color_arr = np.array(color, dtype=np.uint8)
    for idx in range(masks.shape[0]):
        mask = masks[idx].astype(bool)
        if not np.any(mask):
            continue
        canvas[mask] = np.clip(canvas[mask] * 0.45 + color_arr * 0.55, 0, 255).astype(np.uint8)
        contours, _ = cv2.findContours(masks[idx], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(canvas, contours, -1, color, 1, lineType=cv2.LINE_AA)
    return canvas


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prototype a raw-input, external soma proposal workflow for one session."
    )
    parser.add_argument("--session", required=True, help="Session directory, e.g. ...\\Session_002")
    parser.add_argument(
        "--label",
        default="session002_trial01",
        help="Output label under analysis\\ESDetect_proposals",
    )
    parser.add_argument(
        "--max-tiffs",
        type=int,
        default=None,
        help="Optional limit on TIFF stacks to summarize during prototyping.",
    )
    parser.add_argument("--bg-sigma", type=float, default=12.0, help="Background subtraction sigma.")
    parser.add_argument("--blob-sigma", type=float, default=3.0, help="Blob-enhancement sigma.")
    parser.add_argument("--blob-weight", type=float, default=1.5, help="Blob-enhancement weight.")
    parser.add_argument(
        "--peak-q",
        type=float,
        default=99.7,
        help="Percentile threshold for proposal image peak detection.",
    )
    parser.add_argument(
        "--min-distance",
        type=int,
        default=12,
        help="Minimum spacing in pixels between accepted candidate centers.",
    )
    parser.add_argument(
        "--candidate-radius",
        type=int,
        default=9,
        help="Display radius in pixels for candidate overlay circles.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=200,
        help="Maximum number of proposal candidates to keep.",
    )
    parser.add_argument("--min-area", type=int, default=20, help="Minimum connected-component area in pixels.")
    parser.add_argument("--max-area", type=int, default=500, help="Maximum connected-component area in pixels.")
    parser.add_argument(
        "--min-circularity",
        type=float,
        default=0.2,
        help="Minimum circularity required for a connected component to count as a soma candidate.",
    )
    parser.add_argument(
        "--split-distance",
        type=int,
        default=14,
        help="Minimum spacing in pixels between local peaks inside one bright connected region.",
    )
    parser.add_argument(
        "--max-peaks-per-component",
        type=int,
        default=2,
        help="Maximum number of candidate centers allowed from one connected bright region.",
    )
    parser.add_argument(
        "--temporal-rescue",
        action="store_true",
        help="Add a second-pass rescue proposal based on a temporal activity image.",
    )
    parser.add_argument(
        "--rescue-peak-q",
        type=float,
        default=99.4,
        help="Percentile threshold for the temporal rescue candidate detector.",
    )
    parser.add_argument(
        "--rescue-min-distance",
        type=int,
        default=14,
        help="Minimum spacing from existing candidates for temporal rescue points.",
    )
    parser.add_argument(
        "--max-rescue-candidates",
        type=int,
        default=8,
        help="Maximum temporal rescue candidates to add.",
    )
    parser.add_argument(
        "--rescue-source",
        choices=["proposal_temporal_residual", "proposal_residual", "proposal_soma_blob"],
        default="proposal_temporal_residual",
        help="Which proposal image to use for the rescue candidate tier.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    session_dir = _resolve_session_dir(args.session)
    raw_dir = _resolve_raw_dir(session_dir)
    tiff_paths = _iter_tiffs(raw_dir)
    output_dir = _derive_output_dir(session_dir, args.label)

    mean_img, max_img, std_img, total_frames = _accumulate_summary_images(
        tiff_paths=tiff_paths,
        max_tiffs=args.max_tiffs,
    )
    proposal_images = _build_proposal_images(
        mean_img=mean_img,
        max_img=max_img,
        std_img=std_img,
        bg_sigma=float(args.bg_sigma),
        blob_sigma=float(args.blob_sigma),
        blob_weight=float(args.blob_weight),
    )
    candidates = _detect_candidates(
        proposal=proposal_images["proposal_soma_blob"],
        peak_q=float(args.peak_q),
        min_distance=int(args.min_distance),
        max_candidates=int(args.max_candidates),
        min_area=int(args.min_area),
        max_area=int(args.max_area),
        min_circularity=float(args.min_circularity),
        split_distance=int(args.split_distance),
        max_peaks_per_component=int(args.max_peaks_per_component),
    )
    rescue_count = 0
    if args.temporal_rescue:
        rescue_candidates = _detect_candidates(
            proposal=proposal_images[str(args.rescue_source)],
            peak_q=float(args.rescue_peak_q),
            min_distance=int(args.rescue_min_distance),
            max_candidates=int(args.max_rescue_candidates),
            min_area=max(8, int(args.min_area) // 2),
            max_area=max(int(args.max_area), 700),
            min_circularity=max(0.05, float(args.min_circularity) * 0.6),
            split_distance=max(10, int(args.split_distance) - 2),
            max_peaks_per_component=1,
        )
        candidates = _merge_rescue_candidates(
            primary_candidates=candidates,
            rescue_candidates=rescue_candidates,
            min_distance=int(args.rescue_min_distance),
            max_total=int(args.max_candidates),
        )
        rescue_count = sum(1 for c in candidates if c.get("rescue"))

    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    npy_dir = output_dir / "arrays"
    npy_dir.mkdir(parents=True, exist_ok=True)

    for name, image in proposal_images.items():
        cv2.imwrite(str(images_dir / f"{name}.png"), _normalize_uint8(image))
        np.save(npy_dir / f"{name}.npy", image.astype(np.float32))

    overlay = _draw_candidate_overlay(
        background=proposal_images["proposal_base"],
        candidates=candidates,
        radius=int(args.candidate_radius),
    )
    cv2.imwrite(str(images_dir / "proposal_candidates_overlay.png"), overlay)
    np.save(npy_dir / "proposal_candidates.npy", np.array(candidates, dtype=object))

    primary_candidates = [candidate for candidate in candidates if not candidate.get("rescue")]
    rescue_candidates = [candidate for candidate in candidates if candidate.get("rescue")]

    circular_masks = _build_circular_masks(
        shape=proposal_images["proposal_base"].shape,
        candidates=candidates,
        radius=int(args.candidate_radius),
    )
    mask_overlay = _draw_mask_overlay(
        background=proposal_images["proposal_base"],
        masks=circular_masks,
    )
    cv2.imwrite(str(images_dir / "proposal_circular_masks_overlay.png"), mask_overlay)
    np.save(npy_dir / "proposal_circular_masks.npy", circular_masks)

    primary_masks = _build_circular_masks(
        shape=proposal_images["proposal_base"].shape,
        candidates=primary_candidates,
        radius=int(args.candidate_radius),
    )
    rescue_masks = _build_circular_masks(
        shape=proposal_images["proposal_base"].shape,
        candidates=rescue_candidates,
        radius=int(args.candidate_radius),
    )
    primary_overlay = _draw_mask_overlay(
        background=proposal_images["proposal_base"],
        masks=primary_masks,
        color=(0, 255, 255),
    )
    rescue_overlay = _draw_mask_overlay(
        background=proposal_images["proposal_base"],
        masks=rescue_masks,
        color=(0, 165, 255),
    )
    cv2.imwrite(str(images_dir / "proposal_primary_masks_overlay.png"), primary_overlay)
    cv2.imwrite(str(images_dir / "proposal_rescue_masks_overlay.png"), rescue_overlay)
    np.save(npy_dir / "proposal_primary_masks.npy", primary_masks)
    np.save(npy_dir / "proposal_rescue_masks.npy", rescue_masks)

    manifest = {
        "created_at": datetime.now().isoformat(),
        "session_dir": str(session_dir),
        "raw_dir": str(raw_dir),
        "output_dir": str(output_dir),
        "tiff_count_used": min(len(tiff_paths), args.max_tiffs or len(tiff_paths)),
        "total_frames_summarized": int(total_frames),
        "bg_sigma": float(args.bg_sigma),
        "blob_sigma": float(args.blob_sigma),
        "blob_weight": float(args.blob_weight),
        "peak_q": float(args.peak_q),
        "min_distance": int(args.min_distance),
        "candidate_radius": int(args.candidate_radius),
        "max_candidates": int(args.max_candidates),
        "min_area": int(args.min_area),
        "max_area": int(args.max_area),
        "min_circularity": float(args.min_circularity),
        "split_distance": int(args.split_distance),
        "max_peaks_per_component": int(args.max_peaks_per_component),
        "temporal_rescue": bool(args.temporal_rescue),
        "rescue_peak_q": float(args.rescue_peak_q),
        "rescue_min_distance": int(args.rescue_min_distance),
        "max_rescue_candidates": int(args.max_rescue_candidates),
        "rescue_source": str(args.rescue_source),
        "candidate_count": len(candidates),
        "rescue_candidate_count": int(rescue_count),
        "primary_candidate_count": int(len(primary_candidates)),
        "images_dir": str(images_dir),
        "arrays_dir": str(npy_dir),
        "circular_mask_radius": int(args.candidate_radius),
    }
    _write_json(output_dir / "proposal_manifest.json", manifest)
    _write_json(output_dir / "proposal_candidates.json", {"candidates": candidates})

    print(f"Session: {session_dir}")
    print(f"Raw input: {raw_dir}")
    print(f"Output directory: {output_dir}")
    print(f"TIFF stacks summarized: {manifest['tiff_count_used']}")
    print(f"Total frames summarized: {total_frames}")
    print(f"Candidate count: {len(candidates)}")
    print(f"Candidate overlay: {images_dir / 'proposal_candidates_overlay.png'}")


if __name__ == "__main__":
    main()
