#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _resolve_extraction_dir(path_arg: str) -> Path:
    path = Path(path_arg).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"Extraction directory does not exist: {path}")
    return path


def _safe_normalize(image: np.ndarray) -> np.ndarray:
    image = np.asarray(image, dtype=np.float32)
    lo = float(np.percentile(image, 2.0))
    hi = float(np.percentile(image, 99.5))
    if not np.isfinite(lo):
        lo = float(np.nanmin(image))
    if not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    return np.clip((image - lo) / (hi - lo), 0.0, 1.0)


def _uint8_png(image: np.ndarray) -> np.ndarray:
    return (_safe_normalize(image) * 255.0).astype(np.uint8)


def _roi_as_stat(mask: np.ndarray, lam_source: np.ndarray, roi_index: int) -> dict[str, object]:
    ys, xs = np.where(mask > 0)
    area = int(ys.size)
    if area == 0:
        return {
            "ypix": np.asarray([], dtype=np.int32),
            "xpix": np.asarray([], dtype=np.int32),
            "lam": np.asarray([], dtype=np.float32),
            "med": [0.0, 0.0],
            "radius": 0.0,
            "aspect_ratio": 1.0,
            "compact": 0.0,
            "footprint": 0.0,
            "npix": 0,
            "npix_soma": 0,
            "npix_norm": 0.0,
            "npix_norm_no_crop": 0.0,
            "overlap": np.asarray([], dtype=bool),
            "skew": 0.0,
            "std": 0.0,
            "snr": 0.0,
            "soma_crop": True,
            "manual_roi": False,
            "inmerge": -1,
            "imerge": np.asarray([], dtype=np.int32),
            "roi_index": int(roi_index),
        }

    vals = np.asarray(lam_source[ys, xs], dtype=np.float32)
    vals = np.clip(vals, 1e-6, None)
    lam = vals / max(float(vals.sum()), 1e-6)

    center_y = float(np.median(ys))
    center_x = float(np.median(xs))
    radius = float(np.sqrt(area / np.pi))

    yy = ys.astype(np.float32) - center_y
    xx = xs.astype(np.float32) - center_x
    cov = np.cov(np.vstack([yy, xx])) if area > 1 else np.eye(2, dtype=np.float32)
    eigvals = np.sort(np.real(np.linalg.eigvals(cov)))[::-1]
    major = float(np.sqrt(max(eigvals[0], 1e-6)))
    minor = float(np.sqrt(max(eigvals[-1], 1e-6)))
    aspect_ratio = float(major / max(minor, 1e-6))

    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    perimeter = float(sum(cv2.arcLength(cnt, True) for cnt in contours)) if contours else 0.0
    compact = float((perimeter ** 2) / max(4.0 * np.pi * area, 1e-6)) if perimeter > 0 else 0.0

    theta = np.linspace(0.0, 2.0 * np.pi, 32, endpoint=False)
    ycirc = np.clip(np.round(center_y + radius * np.sin(theta)).astype(np.int32), 0, mask.shape[0] - 1)
    xcirc = np.clip(np.round(center_x + radius * np.cos(theta)).astype(np.int32), 0, mask.shape[1] - 1)

    return {
        "ypix": ys.astype(np.int32),
        "xpix": xs.astype(np.int32),
        "lam": lam.astype(np.float32),
        "med": [center_y, center_x],
        "radius": radius,
        "aspect_ratio": aspect_ratio,
        "compact": compact,
        "footprint": 2.0,
        "npix": area,
        "npix_soma": area,
        "npix_norm": 1.0,
        "npix_norm_no_crop": 1.0,
        "overlap": np.zeros(area, dtype=bool),
        "skew": float(0.0),
        "std": float(np.std(vals)),
        "snr": float(np.mean(vals) / max(np.std(vals), 1e-6)),
        "soma_crop": True,
        "manual_roi": False,
        "inmerge": -1,
        "imerge": np.asarray([], dtype=np.int32),
        "ycirc": ycirc,
        "xcirc": xcirc,
        "roi_index": int(roi_index),
    }


def _make_overlay(background: np.ndarray, masks: np.ndarray) -> np.ndarray:
    base = np.stack([_safe_normalize(background)] * 3, axis=-1)
    canvas = (base * 255.0).astype(np.uint8)
    color = np.array([0, 215, 190], dtype=np.uint8)
    for mask in masks:
        region = mask.astype(bool)
        if not np.any(region):
            continue
        canvas[region] = np.clip(0.45 * canvas[region] + 0.55 * color, 0, 255).astype(np.uint8)
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(canvas, contours, -1, (0, 255, 255), 1, lineType=cv2.LINE_AA)
    return canvas


def _eventiness_score(trace: np.ndarray) -> float:
    arr = np.asarray(trace, dtype=np.float32)
    if arr.size == 0:
        return 0.0
    peak = float(np.percentile(arr, 99.5))
    baseline = float(np.percentile(arr, 20.0))
    spread = float(np.std(arr))
    if spread <= 1e-6:
        return 0.0
    threshold = baseline + max(0.10, 1.5 * spread)
    return float(np.mean(arr > threshold))


def _initial_iscell_and_reject_report(stat: np.ndarray, dff: np.ndarray) -> tuple[np.ndarray, list[dict[str, object]]]:
    max_peaks = np.max(dff, axis=1) if dff.size else np.zeros(len(stat), dtype=np.float32)
    peak_scale = max(float(np.percentile(max_peaks, 95.0)), 1e-6) if len(max_peaks) else 1.0
    rows: list[dict[str, object]] = []
    accepted = np.ones(len(stat), dtype=np.float32)
    confidence = np.clip(max_peaks / peak_scale, 0.05, 1.0).astype(np.float32) if len(max_peaks) else np.zeros(0, dtype=np.float32)

    for idx, roi in enumerate(stat):
        area = int(roi.get("npix", 0))
        aspect_ratio = float(roi.get("aspect_ratio", 1.0))
        compact = float(roi.get("compact", 0.0))
        radius = float(roi.get("radius", 0.0))
        trace = dff[idx] if idx < dff.shape[0] else np.zeros(0, dtype=np.float32)
        peak_dff = float(max_peaks[idx]) if idx < len(max_peaks) else 0.0
        trace_std = float(np.std(trace)) if trace.size else 0.0
        eventiness = _eventiness_score(trace)

        reasons: list[str] = []
        severe = False

        if area < 120:
            reasons.append("too_small")
        if area < 80:
            severe = True
        if aspect_ratio > 2.4:
            reasons.append("elongated")
        if aspect_ratio > 3.2:
            severe = True
        if compact > 3.4:
            reasons.append("noncompact")
        if compact > 5.0:
            severe = True
        if peak_dff < 0.12 and trace_std < 0.035:
            reasons.append("weak_trace")
        if peak_dff < 0.08 and trace_std < 0.02:
            severe = True
        if eventiness < 0.002 and peak_dff < 0.18:
            reasons.append("low_eventiness")
        if radius < 4.0:
            reasons.append("tiny_radius")

        reject_score = len(reasons) + (2 if severe else 0)
        suggest_reject = severe or reject_score >= 2
        if suggest_reject:
            accepted[idx] = 0.0
            confidence[idx] = min(float(confidence[idx]) if idx < len(confidence) else 0.1, 0.45)

        rows.append(
            {
                "roi_index": int(idx),
                "accepted_initial": bool(not suggest_reject),
                "suggest_reject": bool(suggest_reject),
                "reasons": reasons,
                "reject_score": int(reject_score),
                "area_px": area,
                "radius": radius,
                "aspect_ratio": aspect_ratio,
                "compact": compact,
                "peak_dff": peak_dff,
                "trace_std": trace_std,
                "eventiness": eventiness,
                "confidence": float(confidence[idx]) if idx < len(confidence) else 0.0,
            }
        )

    iscell = np.column_stack([accepted, confidence]).astype(np.float32)
    return iscell, rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package external soma extraction outputs into a minimal Suite2p-style plane folder.")
    parser.add_argument("--extraction-dir", required=True, help="Path to an ESDetect_extraction folder.")
    parser.add_argument("--label", default="trial14_plane0", help="Output label under analysis\\ESDetect_packaged")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    extraction_dir = _resolve_extraction_dir(args.extraction_dir)
    extraction_manifest = _load_json(extraction_dir / "extraction_manifest.json")
    segmentation_dir = Path(extraction_manifest["segmentation_dir"])
    segmentation_manifest = _load_json(segmentation_dir / "segmentation_manifest.json")
    session_dir = Path(extraction_manifest["session_dir"])

    out_plane = session_dir / "analysis" / "ESDetect_packaged" / str(args.label) / "plane0"
    out_plane.mkdir(parents=True, exist_ok=True)

    extraction_arrays = extraction_dir / "arrays"
    seg_arrays = segmentation_dir / "arrays"
    F = np.load(extraction_arrays / "F.npy", allow_pickle=True).astype(np.float32)
    Fneu = np.load(extraction_arrays / "Fneu.npy", allow_pickle=True).astype(np.float32)
    dff = np.load(extraction_arrays / "dff.npy", allow_pickle=True).astype(np.float32)
    masks = np.load(extraction_arrays / "roi_masks.npy", allow_pickle=True).astype(np.uint8)
    mean_img = np.load(seg_arrays / "mean_image.npy", allow_pickle=True).astype(np.float32)
    max_img = np.load(seg_arrays / "max_image.npy", allow_pickle=True).astype(np.float32)
    proposal_base = np.load(seg_arrays / "proposal_base.npy", allow_pickle=True).astype(np.float32)
    proposal_residual = np.load(seg_arrays / "proposal_residual.npy", allow_pickle=True).astype(np.float32)
    proposal_blob = np.load(seg_arrays / "proposal_soma_blob.npy", allow_pickle=True).astype(np.float32)

    stat = np.asarray(
        [_roi_as_stat(mask, proposal_blob, idx) for idx, mask in enumerate(masks)],
        dtype=object,
    )
    iscell, reject_report = _initial_iscell_and_reject_report(stat, dff)
    spks = np.clip(np.diff(dff, axis=1, prepend=dff[:, :1]), 0.0, None).astype(np.float32)

    ops = {
        "Ly": int(mean_img.shape[0]),
        "Lx": int(mean_img.shape[1]),
        "fs": float(extraction_manifest.get("frame_rate_hz", 0.0) or 0.0),
        "nframes": int(extraction_manifest.get("total_frames", F.shape[1])),
        "nrois": int(masks.shape[0]),
        "meanImg": mean_img,
        "max_proj": max_img,
        "Vcorr": proposal_residual,
        "meanImgE": _safe_normalize(proposal_blob).astype(np.float32),
        "refImg": mean_img.astype(np.int16),
        "yrange": [0, int(mean_img.shape[0])],
        "xrange": [0, int(mean_img.shape[1])],
        "save_path": str(out_plane),
        "save_path0": str(out_plane.parent.parent),
        "data_path": [str(extraction_manifest["raw_dir"])],
        "reg_file": str(segmentation_manifest.get("registration_summary", {}).get("reg_file", "")),
        "roidetect": False,
        "do_registration": False,
        "nonrigid": False,
        "sparse_mode": True,
        "anatomical_only": 1,
        "diameter": [float(np.median([roi["radius"] for roi in stat]) * 2.0)] * 2,
        "max_overlap": 0.5,
        "soma_crop": True,
        "version": "external_soma_package_v1",
        "date_proc": datetime.now().isoformat(),
        "source_image": segmentation_manifest.get("source_image", ""),
        "external_segmentation_dir": str(segmentation_dir),
        "external_extraction_dir": str(extraction_dir),
    }

    np.save(out_plane / "stat.npy", stat)
    np.save(out_plane / "iscell.npy", iscell)
    np.save(out_plane / "ops.npy", ops, allow_pickle=True)
    np.save(out_plane / "F.npy", F)
    np.save(out_plane / "Fneu.npy", Fneu)
    np.save(out_plane / "spks.npy", spks)
    np.save(out_plane / "dff.npy", dff)
    np.save(out_plane / "roi_masks.npy", masks)
    _write_json(out_plane / "esdetect_reject_report.json", {"rows": reject_report})

    cv2.imwrite(str(out_plane / "suite2p_mean_projection.png"), _uint8_png(mean_img))
    cv2.imwrite(str(out_plane / "suite2p_max_projection.png"), _uint8_png(max_img))
    cv2.imwrite(str(out_plane / "suite2p_correlation_image.png"), _uint8_png(proposal_residual))
    cv2.imwrite(str(out_plane / "suite2p_static_overlay.png"), _make_overlay(proposal_base, masks))
    cv2.imwrite(str(out_plane / "suite2p_accepted_fill_overlay.png"), _make_overlay(mean_img, masks))

    package_manifest = {
        "created_at": datetime.now().isoformat(),
        "session_dir": str(session_dir),
        "segmentation_dir": str(segmentation_dir),
        "extraction_dir": str(extraction_dir),
        "output_plane_dir": str(out_plane),
        "roi_count": int(masks.shape[0]),
        "accepted_initial_count": int(np.count_nonzero(iscell[:, 0] > 0.5)),
        "rejected_initial_count": int(np.count_nonzero(iscell[:, 0] <= 0.5)),
        "frame_count": int(F.shape[1]),
        "frame_rate_hz": float(ops["fs"]),
        "files_written": [
            "stat.npy",
            "iscell.npy",
            "ops.npy",
            "F.npy",
            "Fneu.npy",
            "spks.npy",
            "dff.npy",
            "roi_masks.npy",
            "esdetect_reject_report.json",
            "suite2p_mean_projection.png",
            "suite2p_max_projection.png",
            "suite2p_correlation_image.png",
            "suite2p_static_overlay.png",
            "suite2p_accepted_fill_overlay.png",
        ],
    }
    _write_json(out_plane / "external_soma_package_manifest.json", package_manifest)

    print(f"Extraction directory: {extraction_dir}")
    print(f"Output plane dir: {out_plane}")
    print(f"ROI count: {masks.shape[0]}")
    print(f"Files written: {len(package_manifest['files_written'])}")


if __name__ == "__main__":
    main()
