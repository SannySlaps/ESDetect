#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import matplotlib
import numpy as np
import tifffile

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export CaImAn-like review artifacts from a prepared Suite2p sandbox run."
    )
    parser.add_argument("--run-dir", required=True, help="Path to a prepared suite2p_sandbox run directory.")
    parser.add_argument("--start-frame", type=int, default=0, help="Frame index to start previews from.")
    parser.add_argument("--num-frames", type=int, default=300, help="Number of frames to render in previews.")
    parser.add_argument("--fps", type=float, default=20.0, help="Preview video FPS.")
    parser.add_argument("--gain", type=float, default=1.0, help="Brightness multiplier applied after normalization.")
    parser.add_argument("--q-min", type=float, default=5.0, help="Lower percentile for frame normalization.")
    parser.add_argument("--q-max", type=float, default=99.5, help="Upper percentile for frame normalization.")
    parser.add_argument(
        "--only",
        choices=["all", "motion", "overlay", "three_panel", "reconstruction"],
        default="all",
        help="Render only one preview type, or all previews.",
    )
    return parser.parse_args()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_ops(plane_dir: Path) -> dict:
    ops_path = plane_dir / "ops.npy"
    if not ops_path.exists():
        raise SystemExit(f"Missing ops.npy: {ops_path}")
    return np.load(ops_path, allow_pickle=True).item()


def _resolve_plane_dir(run_dir: Path, db: dict) -> Path:
    candidates = []
    save_path0 = db.get("save_path0")
    if save_path0:
        candidates.append(Path(str(save_path0)) / "suite2p" / "plane0")
    candidates.append(run_dir / "outputs" / "suite2p" / "plane0")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise SystemExit(
        "Suite2p plane directory not found. Checked:\n" + "\n".join(str(path) for path in candidates)
    )


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


def _load_trace_array(plane_dir: Path, name: str) -> np.ndarray | None:
    path = plane_dir / f"{name}.npy"
    if not path.exists():
        return None
    return np.load(path, allow_pickle=True)


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


def _normalize_frame(frame: np.ndarray, low: float, high: float, gain: float = 1.0) -> np.ndarray:
    scaled = np.clip((frame - low) / max(high - low, 1e-6), 0.0, 1.0)
    scaled = np.clip(scaled * max(gain, 0.01), 0.0, 1.0)
    return (scaled * 255.0).astype(np.uint8)


def _annotate_panel(panel: np.ndarray, label: str) -> np.ndarray:
    outlined = panel.copy()
    cv2.putText(outlined, label, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (8, 8, 8), 5, cv2.LINE_AA)
    cv2.putText(outlined, label, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
    return outlined


def _annotate_timestamp(frame: np.ndarray, frame_index: int, fr: float) -> np.ndarray:
    annotated = frame.copy()
    seconds = float(frame_index) / max(float(fr), 1e-6)
    timer_txt = f"{seconds:7.2f} s"
    text_y = annotated.shape[0] - 15
    (txt_w, _), _ = cv2.getTextSize(timer_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
    text_x = max(10, (annotated.shape[1] - txt_w) // 2)
    cv2.putText(annotated, timer_txt, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (8, 8, 8), 5, cv2.LINE_AA)
    cv2.putText(annotated, timer_txt, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (80, 255, 180), 2, cv2.LINE_AA)
    return annotated


def _read_video_frames(video_path: Path, start_frame: int, num_frames: int) -> np.ndarray:
    if not video_path.exists():
        raise SystemExit(f"Preview video not found: {video_path}")
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise SystemExit(f"Could not open preview video: {video_path}")
    frames: list[np.ndarray] = []
    try:
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        start = max(0, min(start_frame, max(0, total_frames - 1))) if total_frames > 0 else max(0, start_frame)
        if start > 0:
            capture.set(cv2.CAP_PROP_POS_FRAMES, start)
        while len(frames) < max(1, num_frames):
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(frame)
    finally:
        capture.release()
    if not frames:
        raise SystemExit(f"No frames could be read from preview video: {video_path}")
    return np.asarray(frames)


def _roi_edges_image(stat: np.ndarray, Ly: int, Lx: int, iscell: np.ndarray | None) -> np.ndarray:
    roi_mask = _build_roi_mask(stat, Ly, Lx, iscell)
    return cv2.Canny(roi_mask, 50, 150)


def _overlay_roi_edges(frame: np.ndarray, roi_edges: np.ndarray, color: tuple[int, int, int] = (0, 255, 255)) -> np.ndarray:
    outlined = frame.copy()
    outlined[roi_edges > 0] = color
    return outlined


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


def _iter_raw_frames(raw_tiffs: list[str], start_frame: int, num_frames: int) -> np.ndarray:
    frames: list[np.ndarray] = []
    skip_remaining = max(0, start_frame)
    needed = max(1, num_frames)

    for tiff_path in raw_tiffs:
        if needed <= 0:
            break
        path = Path(tiff_path)
        if not path.exists():
            continue
        with tifffile.TiffFile(path) as tif:
            arr = tif.asarray()
        if arr.ndim == 2:
            arr = arr[np.newaxis, ...]
        if skip_remaining >= arr.shape[0]:
            skip_remaining -= arr.shape[0]
            continue
        start = skip_remaining
        take = min(needed, arr.shape[0] - start)
        frames.append(np.asarray(arr[start : start + take], dtype=np.float32))
        needed -= take
        skip_remaining = 0

    if not frames:
        raise SystemExit("Could not read raw TIFF frames for motion preview.")
    return np.concatenate(frames, axis=0)


def _write_video(frames: np.ndarray, output_path: Path, fps: float) -> None:
    height, width = frames.shape[1], frames.shape[2]
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), float(fps), (width, height))
    if not writer.isOpened():
        raise SystemExit(f"Could not open video writer for {output_path}")
    try:
        for frame in frames:
            writer.write(frame)
    finally:
        writer.release()


def _make_overlay_preview(
    plane_dir: Path,
    ops: dict,
    stat: np.ndarray,
    iscell: np.ndarray | None,
    start_frame: int,
    num_frames: int,
    fps: float,
    gain: float,
    q_min: float,
    q_max: float,
) -> Path:
    Ly = int(ops["Ly"])
    Lx = int(ops["Lx"])
    reg_file = Path(str(ops["reg_file"]))
    roi_edges = _roi_edges_image(stat, Ly, Lx, iscell)
    rendered: list[np.ndarray] = []
    fr = float(ops.get("fs", fps))
    try:
        movie = _iter_registered_frames(reg_file, Ly, Lx, start_frame, num_frames)
        low = float(np.percentile(movie, q_min))
        high = float(np.percentile(movie, q_max))
        for offset, frame in enumerate(movie):
            gray8 = _normalize_frame(frame, low, high, gain)
            rgb = cv2.cvtColor(gray8, cv2.COLOR_GRAY2BGR)
            rgb = _overlay_roi_edges(rgb, roi_edges)
            rgb = _annotate_panel(rgb, "Motion Corrected + ROI")
            rgb = _annotate_timestamp(rgb, start_frame + offset, fr)
            rendered.append(rgb)
    except SystemExit:
        motion_preview_path = plane_dir / "suite2p_motion_preview.mp4"
        preview_frames = _read_video_frames(motion_preview_path, start_frame, num_frames)
        for offset, frame in enumerate(preview_frames):
            mid = frame.shape[1] // 2
            reg_rgb = frame[:, mid:, :].copy()
            reg_rgb = _overlay_roi_edges(reg_rgb, roi_edges)
            reg_rgb = _annotate_panel(reg_rgb, "Motion Corrected + ROI")
            reg_rgb = _annotate_timestamp(reg_rgb, start_frame + offset, fr)
            rendered.append(reg_rgb)

    output_path = plane_dir / "suite2p_overlay_preview.mp4"
    _write_video(np.asarray(rendered), output_path, fps)
    return output_path


def _make_motion_preview(
    plane_dir: Path,
    raw_tiffs: list[str],
    ops: dict,
    start_frame: int,
    num_frames: int,
    fps: float,
    gain: float,
    q_min: float,
    q_max: float,
) -> Path:
    Ly = int(ops["Ly"])
    Lx = int(ops["Lx"])
    reg_file = Path(str(ops["reg_file"]))
    raw_movie = _iter_raw_frames(raw_tiffs, start_frame, num_frames)
    reg_movie = _iter_registered_frames(reg_file, Ly, Lx, start_frame, num_frames)
    n_frames = min(raw_movie.shape[0], reg_movie.shape[0])
    raw_movie = raw_movie[:n_frames]
    reg_movie = reg_movie[:n_frames]

    raw_low = float(np.percentile(raw_movie, q_min))
    raw_high = float(np.percentile(raw_movie, q_max))
    reg_low = float(np.percentile(reg_movie, q_min))
    reg_high = float(np.percentile(reg_movie, q_max))

    rendered: list[np.ndarray] = []
    fr = float(ops.get("fs", fps))
    for idx in range(n_frames):
        raw8 = _normalize_frame(raw_movie[idx], raw_low, raw_high, gain)
        reg8 = _normalize_frame(reg_movie[idx], reg_low, reg_high, gain)
        raw_rgb = _annotate_panel(cv2.cvtColor(raw8, cv2.COLOR_GRAY2BGR), "Raw")
        reg_rgb = _annotate_panel(cv2.cvtColor(reg8, cv2.COLOR_GRAY2BGR), "Motion Corrected")
        combined = np.concatenate([raw_rgb, reg_rgb], axis=1)
        combined = _annotate_timestamp(combined, start_frame + idx, fr)
        rendered.append(combined)

    output_path = plane_dir / "suite2p_motion_preview.mp4"
    _write_video(np.asarray(rendered), output_path, fps)
    return output_path


def _make_three_panel_preview(
    plane_dir: Path,
    raw_tiffs: list[str],
    ops: dict,
    stat: np.ndarray,
    iscell: np.ndarray | None,
    start_frame: int,
    num_frames: int,
    fps: float,
    gain: float,
    q_min: float,
    q_max: float,
) -> Path:
    Ly = int(ops["Ly"])
    Lx = int(ops["Lx"])
    reg_file = Path(str(ops["reg_file"]))
    roi_mask = _build_roi_mask(stat, Ly, Lx, iscell)
    roi_mask_bool = roi_mask > 0
    roi_edges = _roi_edges_image(stat, Ly, Lx, iscell)
    fr = float(ops.get("fs", fps))

    rendered: list[np.ndarray] = []
    try:
        raw_movie = _iter_raw_frames(raw_tiffs, start_frame, num_frames)
        reg_movie = _iter_registered_frames(reg_file, Ly, Lx, start_frame, num_frames)
        n_frames = min(raw_movie.shape[0], reg_movie.shape[0])
        raw_movie = raw_movie[:n_frames]
        reg_movie = reg_movie[:n_frames]

        raw_low = float(np.percentile(raw_movie, q_min))
        raw_high = float(np.percentile(raw_movie, q_max))
        reg_low = float(np.percentile(reg_movie, q_min))
        reg_high = float(np.percentile(reg_movie, q_max))

        for idx in range(n_frames):
            raw8 = _normalize_frame(raw_movie[idx], raw_low, raw_high, gain)
            reg8 = _normalize_frame(reg_movie[idx], reg_low, reg_high, gain)

            raw_rgb = _annotate_panel(cv2.cvtColor(raw8, cv2.COLOR_GRAY2BGR), "Raw")
            reg_rgb = cv2.cvtColor(reg8, cv2.COLOR_GRAY2BGR)
            reg_rgb = _overlay_roi_edges(reg_rgb, roi_edges)
            reg_rgb = _annotate_panel(reg_rgb, "Motion Corrected + ROI")
            roi_only = np.zeros_like(reg8)
            roi_only[roi_mask_bool] = reg8[roi_mask_bool]
            roi_only_rgb = cv2.cvtColor(roi_only, cv2.COLOR_GRAY2BGR)
            roi_only_rgb = _overlay_roi_edges(roi_only_rgb, roi_edges)
            roi_only_rgb = _annotate_panel(roi_only_rgb, "ROI Only")

            combined = np.concatenate([raw_rgb, reg_rgb, roi_only_rgb], axis=1)
            combined = _annotate_timestamp(combined, start_frame + idx, fr)
            rendered.append(combined)
    except SystemExit:
        motion_preview_path = plane_dir / "suite2p_motion_preview.mp4"
        preview_frames = _read_video_frames(motion_preview_path, start_frame, num_frames)
        for idx, frame in enumerate(preview_frames):
            mid = frame.shape[1] // 2
            raw_rgb = frame[:, :mid, :].copy()
            reg_base = frame[:, mid:, :].copy()
            reg_rgb = _overlay_roi_edges(reg_base, roi_edges)
            reg_rgb = _annotate_panel(reg_rgb, "Motion Corrected + ROI")
            roi_only_rgb = np.zeros_like(reg_rgb)
            gray_reg = cv2.cvtColor(reg_base, cv2.COLOR_BGR2GRAY)
            roi_only_rgb[roi_mask_bool] = cv2.cvtColor(gray_reg, cv2.COLOR_GRAY2BGR)[roi_mask_bool]
            roi_only_rgb = _overlay_roi_edges(roi_only_rgb, roi_edges)
            roi_only_rgb = _annotate_panel(roi_only_rgb, "ROI Only")
            combined = np.concatenate([raw_rgb, reg_rgb, roi_only_rgb], axis=1)
            combined = _annotate_timestamp(combined, start_frame + idx, fr)
            rendered.append(combined)

    output_path = plane_dir / "suite2p_three_panel_preview.mp4"
    _write_video(np.asarray(rendered), output_path, fps)
    return output_path


def _make_contour_figure(plane_dir: Path, ops: dict, stat: np.ndarray, iscell: np.ndarray | None) -> Path:
    return _make_filtered_contour_figure(
        plane_dir / "suite2p_contours.png",
        "Suite2p ROI Overlay on meanImg",
        ops,
        stat,
        iscell,
        mode="accepted",
    )


def _make_filtered_contour_figure(
    output_path: Path,
    title: str,
    ops: dict,
    stat: np.ndarray,
    iscell: np.ndarray | None,
    *,
    mode: str,
) -> Path:
    mean_img = np.asarray(ops.get("meanImg"))
    if mean_img.ndim != 2:
        raise SystemExit("ops.npy does not contain a usable meanImg for contour export.")

    fig, ax = plt.subplots(figsize=(8, 8), dpi=150)
    ax.imshow(mean_img, cmap="gray")
    for idx, roi in enumerate(stat):
        if iscell is not None and idx < len(iscell):
            accepted = bool(iscell[idx])
            if mode == "accepted" and not accepted:
                continue
            if mode == "rejected" and accepted:
                continue
        ypix = np.asarray(roi["ypix"])
        xpix = np.asarray(roi["xpix"])
        if ypix.size == 0 or xpix.size == 0:
            continue
        color = "#00ffd0" if mode != "rejected" else "#ff8b7a"
        ax.scatter(xpix, ypix, s=0.2, c=color, alpha=0.45)

    ax.set_title(title)
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    return output_path


def _make_static_overlay_image(plane_dir: Path, ops: dict, stat: np.ndarray, iscell: np.ndarray | None) -> Path:
    mean_img = np.asarray(ops.get("meanImg"))
    if mean_img.ndim != 2:
        raise SystemExit("ops.npy does not contain a usable meanImg for static overlay export.")

    fig, ax = plt.subplots(figsize=(8, 8), dpi=150)
    ax.imshow(mean_img, cmap="gray")
    for idx, roi in enumerate(stat):
        accepted = True
        if iscell is not None and idx < len(iscell):
            accepted = bool(iscell[idx])
        ypix = np.asarray(roi["ypix"], dtype=np.int32)
        xpix = np.asarray(roi["xpix"], dtype=np.int32)
        if ypix.size == 0 or xpix.size == 0:
            continue
        color = "#00ffd0" if accepted else "#ff8b7a"
        ax.scatter(xpix, ypix, s=0.18, c=color, alpha=0.42)

    ax.set_title("Suite2p Static ROI Overlay on meanImg")
    ax.set_axis_off()
    fig.tight_layout()
    output_path = plane_dir / "suite2p_static_overlay.png"
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    return output_path


def _make_accepted_fill_overlay_image(plane_dir: Path, ops: dict, stat: np.ndarray, iscell: np.ndarray | None) -> Path:
    mean_img = np.asarray(ops.get("meanImg"))
    if mean_img.ndim != 2:
        raise SystemExit("ops.npy does not contain a usable meanImg for accepted ROI fill overlay export.")

    fill = np.zeros((*mean_img.shape, 4), dtype=np.float32)
    fill_color = np.array([0.0, 1.0, 0.8156863, 0.38], dtype=np.float32)

    for idx, roi in enumerate(stat):
        accepted = True
        if iscell is not None and idx < len(iscell):
            accepted = bool(iscell[idx])
        if not accepted:
            continue
        ypix = np.asarray(roi["ypix"], dtype=np.int32)
        xpix = np.asarray(roi["xpix"], dtype=np.int32)
        if ypix.size == 0 or xpix.size == 0:
            continue
        valid = (
            (ypix >= 0) & (ypix < mean_img.shape[0]) &
            (xpix >= 0) & (xpix < mean_img.shape[1])
        )
        if not np.any(valid):
            continue
        fill[ypix[valid], xpix[valid]] = fill_color

    fig, ax = plt.subplots(figsize=(8, 8), dpi=150)
    ax.imshow(mean_img, cmap="gray")
    ax.imshow(fill)
    ax.set_title("Suite2p Accepted ROI Fill Overlay")
    ax.set_axis_off()
    fig.tight_layout()
    output_path = plane_dir / "suite2p_accepted_fill_overlay.png"
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    return output_path


def _save_projection_image(image: np.ndarray, output_path: Path, title: str) -> Path:
    fig, ax = plt.subplots(figsize=(8, 8), dpi=150)
    ax.imshow(image, cmap="gray")
    ax.set_title(title)
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    return output_path


def _make_projection_exports(plane_dir: Path, ops: dict) -> dict[str, str]:
    generated: dict[str, str] = {}

    mean_img = np.asarray(ops.get("meanImg"))
    if mean_img.ndim == 2:
        generated["mean_projection"] = str(
            _save_projection_image(mean_img, plane_dir / "suite2p_mean_projection.png", "Suite2p Mean Projection")
        )

    max_proj = np.asarray(ops.get("max_proj"))
    if max_proj.ndim == 2:
        generated["max_projection"] = str(
            _save_projection_image(max_proj, plane_dir / "suite2p_max_projection.png", "Suite2p Max Projection")
        )

    corr_img = np.asarray(ops.get("Vcorr"))
    if corr_img.ndim == 2:
        generated["correlation_image"] = str(
            _save_projection_image(corr_img, plane_dir / "suite2p_correlation_image.png", "Suite2p Correlation Image")
        )

    return generated


def _roi_areas(stat: np.ndarray) -> np.ndarray:
    return np.asarray([len(np.asarray(roi["ypix"])) for roi in stat], dtype=np.int32)


def _make_roi_size_figure(plane_dir: Path, stat: np.ndarray, iscell: np.ndarray | None) -> Path:
    areas = _roi_areas(stat)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), dpi=150)
    axes[0].hist(areas, bins=min(30, max(10, len(areas) // 3)), color="#5dd6c0", alpha=0.85)
    axes[0].set_title("ROI Area Distribution")
    axes[0].set_xlabel("Pixels per ROI")
    axes[0].set_ylabel("Count")

    if iscell is not None and len(iscell) == len(areas):
        accepted = areas[iscell]
        rejected = areas[~iscell]
        bins = min(30, max(10, len(areas) // 3))
        if accepted.size:
            axes[1].hist(accepted, bins=bins, alpha=0.7, label="Accepted", color="#80ffb4")
        if rejected.size:
            axes[1].hist(rejected, bins=bins, alpha=0.7, label="Rejected", color="#ff8b7a")
        axes[1].legend(loc="best")
    else:
        axes[1].hist(areas, bins=min(30, max(10, len(areas) // 3)), color="#8ec5ff", alpha=0.85)
    axes[1].set_title("Accepted vs Rejected ROI Areas")
    axes[1].set_xlabel("Pixels per ROI")
    axes[1].set_ylabel("Count")

    fig.tight_layout()
    output_path = plane_dir / "suite2p_roi_size_summary.png"
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    return output_path


def _make_trace_preview_figure(
    plane_dir: Path,
    ops: dict,
    stat: np.ndarray,
    iscell: np.ndarray | None,
) -> Path:
    F = _load_trace_array(plane_dir, "F")
    Fneu = _load_trace_array(plane_dir, "Fneu")
    spks = _load_trace_array(plane_dir, "spks")
    if F is None:
        raise SystemExit(f"Missing F.npy: {plane_dir / 'F.npy'}")

    n_components = F.shape[0]
    if iscell is not None and len(iscell) == n_components and np.any(iscell):
        indices = np.flatnonzero(iscell)[:6]
    else:
        indices = np.arange(min(6, n_components))
    fr = float(ops.get("fs", 1.0))
    t = np.arange(F.shape[1], dtype=np.float32) / max(fr, 1e-6)

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), dpi=150, sharex=True)
    fig.suptitle("Suite2p Trace Preview", fontsize=12)
    for idx in indices:
        axes[0].plot(t, F[idx], linewidth=0.8, alpha=0.9, label=f"ROI {idx}")
        if Fneu is not None and idx < Fneu.shape[0]:
            axes[1].plot(t, Fneu[idx], linewidth=0.8, alpha=0.9, label=f"ROI {idx}")
        if spks is not None and idx < spks.shape[0]:
            axes[2].plot(t, spks[idx], linewidth=0.8, alpha=0.9, label=f"ROI {idx}")

    axes[0].set_title("F")
    axes[1].set_title("Fneu")
    axes[2].set_title("spks")
    for ax in axes:
        ax.set_ylabel("Signal")
        ax.grid(alpha=0.2)
    axes[2].set_xlabel("Time (s)")
    if len(indices) <= 6:
        axes[0].legend(loc="upper right", fontsize=7, ncol=2)

    fig.tight_layout()
    output_path = plane_dir / "suite2p_trace_preview.png"
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    return output_path


def _corrected_traces(F: np.ndarray, Fneu: np.ndarray | None) -> np.ndarray:
    corrected = np.asarray(F, dtype=float)
    if Fneu is not None:
        corrected = corrected - 0.7 * np.asarray(Fneu, dtype=float)
    baselines = np.percentile(corrected, 20, axis=1, keepdims=True)
    baselines = np.maximum(baselines, 1e-3)
    return (corrected - baselines) / baselines


def _make_reconstruction_preview(
    plane_dir: Path,
    ops: dict,
    stat: np.ndarray,
    iscell: np.ndarray | None,
    start_frame: int,
    num_frames: int,
    fps: float,
    gain: float,
    q_min: float,
    q_max: float,
) -> Path:
    F = _load_trace_array(plane_dir, "F")
    if F is None:
        raise SystemExit(f"Missing F.npy: {plane_dir / 'F.npy'}")
    Fneu = _load_trace_array(plane_dir, "Fneu")
    traces = _corrected_traces(F, Fneu)
    if traces.ndim != 2 or traces.shape[0] == 0:
        raise SystemExit("Could not build reconstruction preview from Suite2p traces.")

    Ly = int(ops["Ly"])
    Lx = int(ops["Lx"])
    total_frames = traces.shape[1]
    start = max(0, min(start_frame, total_frames - 1))
    stop = min(total_frames, start + max(1, num_frames))
    traces = traces[:, start:stop]

    mean_img = np.asarray(ops.get("meanImg"))
    if mean_img.ndim == 2:
        mean_low = float(np.percentile(mean_img, q_min))
        mean_high = float(np.percentile(mean_img, q_max))
        mean_rgb = cv2.cvtColor(_normalize_frame(mean_img, mean_low, mean_high, gain), cv2.COLOR_GRAY2BGR)
    else:
        mean_rgb = np.zeros((Ly, Lx, 3), dtype=np.uint8)

    all_indices = np.arange(F.shape[0], dtype=int)
    if iscell is not None and len(iscell) == F.shape[0]:
        roi_indices = all_indices[iscell]
    else:
        roi_indices = all_indices
    if roi_indices.size == 0:
        raise SystemExit("No accepted ROIs available to build reconstruction preview.")

    trace_subset = traces[roi_indices]
    p20 = np.percentile(trace_subset, 20, axis=1, keepdims=True)
    p99 = np.percentile(trace_subset, 99, axis=1, keepdims=True)
    scale = np.maximum(p99 - p20, 1e-6)
    norm_traces = np.clip((trace_subset - p20) / scale, 0.0, 1.0)

    rendered: list[np.ndarray] = []
    fr = float(ops.get("fs", fps))
    roi_edges = _roi_edges_image(stat, Ly, Lx, iscell)
    frame_count = norm_traces.shape[1]
    for frame_idx in range(frame_count):
        recon = np.zeros((Ly, Lx), dtype=np.float32)
        for local_idx, roi_idx in enumerate(roi_indices):
            amp = float(norm_traces[local_idx, frame_idx])
            if amp <= 0.0:
                continue
            roi = stat[int(roi_idx)]
            ypix = np.asarray(roi["ypix"], dtype=np.int32)
            xpix = np.asarray(roi["xpix"], dtype=np.int32)
            lam = np.asarray(roi.get("lam", np.ones_like(ypix, dtype=np.float32)), dtype=np.float32)
            valid = (ypix >= 0) & (ypix < Ly) & (xpix >= 0) & (xpix < Lx)
            if not np.any(valid):
                continue
            recon[ypix[valid], xpix[valid]] += lam[valid] * amp

        recon8 = _normalize_frame(recon, float(np.percentile(recon, q_min)), float(np.percentile(recon, q_max)), gain)
        heat = cv2.applyColorMap(recon8, cv2.COLORMAP_TURBO)
        composite = cv2.addWeighted(mean_rgb, 0.35, heat, 0.65, 0.0)
        composite = _overlay_roi_edges(composite, roi_edges)
        composite = _annotate_panel(composite, "Reconstructed Activity")
        composite = _annotate_timestamp(composite, start + frame_idx, fr)
        rendered.append(composite)

    output_path = plane_dir / "suite2p_reconstruction_preview.mp4"
    _write_video(np.asarray(rendered), output_path, fps)
    return output_path


def _write_trace_csv(
    output_path: Path,
    label_prefix: str,
    traces: np.ndarray,
    time_s: np.ndarray,
    indices: np.ndarray,
) -> Path:
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        header = ["time_s"] + [f"{label_prefix}_{int(idx)}" for idx in indices]
        writer.writerow(header)
        for frame_idx, t in enumerate(time_s):
            row = [f"{float(t):.6f}"]
            row.extend(f"{float(traces[idx, frame_idx]):.6f}" for idx in indices)
            writer.writerow(row)
    return output_path


def _make_trace_csv_exports(plane_dir: Path, ops: dict, iscell: np.ndarray | None) -> dict[str, str]:
    F = _load_trace_array(plane_dir, "F")
    if F is None:
        return {}
    Fneu = _load_trace_array(plane_dir, "Fneu")
    dff = _corrected_traces(F, Fneu)
    fr = float(ops.get("fs", 1.0))
    time_s = np.arange(F.shape[1], dtype=float) / max(fr, 1e-6)

    all_indices = np.arange(F.shape[0], dtype=int)
    if iscell is not None and len(iscell) == F.shape[0]:
        accepted = all_indices[iscell]
        rejected = all_indices[~iscell]
    else:
        accepted = all_indices
        rejected = np.asarray([], dtype=int)

    generated: dict[str, str] = {}
    export_specs = [
        ("accepted_f_csv", plane_dir / "suite2p_accepted_F_traces.csv", "roi", F, accepted),
        ("accepted_dff_csv", plane_dir / "suite2p_accepted_dff_traces.csv", "roi", dff, accepted),
        ("rejected_f_csv", plane_dir / "suite2p_rejected_F_traces.csv", "roi", F, rejected),
        ("rejected_dff_csv", plane_dir / "suite2p_rejected_dff_traces.csv", "roi", dff, rejected),
    ]
    for key, path, prefix, trace_matrix, indices in export_specs:
        if indices.size == 0:
            continue
        generated[key] = str(_write_trace_csv(path, prefix, trace_matrix, time_s, indices))
    return generated


def _make_qc_report(
    run_dir: Path,
    plane_dir: Path,
    manifest: dict,
    runtime: dict,
    ops: dict,
    stat: np.ndarray,
    iscell: np.ndarray | None,
) -> Path:
    areas = _roi_areas(stat)
    accepted_count = int(np.count_nonzero(iscell)) if iscell is not None else None
    lines = [
        "# Suite2p QC Summary",
        "",
        f"- Session: `{manifest.get('session_path', '')}`",
        f"- Run Dir: `{run_dir}`",
        f"- Output Dir: `{plane_dir}`",
        f"- Frames: `{int(ops.get('nframes', 0))}`",
        f"- Frame Shape: `{int(ops.get('Ly', 0))} x {int(ops.get('Lx', 0))}`",
        f"- Total ROIs: `{len(stat)}`",
        f"- Accepted ROIs: `{accepted_count if accepted_count is not None else 'unknown'}`",
        f"- Mean ROI area (pixels): `{float(np.mean(areas)):.1f}`" if len(areas) else "- Mean ROI area (pixels): `n/a`",
        f"- Median ROI area (pixels): `{float(np.median(areas)):.1f}`" if len(areas) else "- Median ROI area (pixels): `n/a`",
    ]
    runtime_seconds = runtime.get("duration_seconds")
    if runtime_seconds is not None:
        lines.append(f"- Runtime (s): `{runtime_seconds}`")
    lines.extend(
        [
            "",
            "## Generated Review Files",
            "",
            "- `suite2p_motion_preview.mp4`",
            "- `suite2p_overlay_preview.mp4`",
            "- `suite2p_three_panel_preview.mp4`",
            "- `suite2p_reconstruction_preview.mp4`",
            "- `suite2p_mean_projection.png`",
            "- `suite2p_max_projection.png`",
            "- `suite2p_correlation_image.png`",
            "- `suite2p_static_overlay.png`",
            "- `suite2p_accepted_fill_overlay.png`",
            "- `suite2p_contours.png`",
            "- `suite2p_accepted_contours.png`",
            "- `suite2p_rejected_contours.png`",
            "- `suite2p_trace_preview.png`",
            "- `suite2p_roi_size_summary.png`",
            "- `suite2p_accepted_F_traces.csv`",
            "- `suite2p_accepted_dff_traces.csv`",
            "- `suite2p_rejected_F_traces.csv`",
            "- `suite2p_rejected_dff_traces.csv`",
            "- `suite2p_run_summary.json`",
        ]
    )
    output_path = plane_dir / "suite2p_qc_summary.md"
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _make_summary(
    run_dir: Path,
    plane_dir: Path,
    manifest: dict,
    runtime: dict,
    ops: dict,
    stat: np.ndarray,
    iscell: np.ndarray | None,
    generated_files: dict[str, str],
) -> Path:
    accepted_count = int(np.count_nonzero(iscell)) if iscell is not None else None
    summary = {
        "run_dir": str(run_dir),
        "plane_dir": str(plane_dir),
        "session_path": manifest.get("session_path"),
        "raw_tiff_count": manifest.get("raw_tiff_count"),
        "nframes": int(ops.get("nframes", 0)),
        "frame_shape": [int(ops.get("Ly", 0)), int(ops.get("Lx", 0))],
        "roi_count_total": int(len(stat)),
        "roi_count_accepted": accepted_count,
        "runtime": runtime,
        "generated_files": generated_files,
        "reference_caiman_fit": manifest.get("latest_reference_fit_hdf5"),
        "reference_caiman_checkpoint": manifest.get("latest_reference_checkpoint"),
    }
    output_path = plane_dir / "suite2p_run_summary.json"
    _write_json(output_path, summary)
    return output_path


def main() -> None:
    args = parse_args()
    run_dir = Path(args.run_dir).expanduser().resolve()
    if not run_dir.exists():
        raise SystemExit(f"Run directory does not exist: {run_dir}")

    manifest = _load_json(run_dir / "session_manifest.json")
    db = _load_json(run_dir / "suite2p_db.json")
    runtime_path = run_dir / "suite2p_runtime.json"
    runtime = _load_json(runtime_path) if runtime_path.exists() else {}

    plane_dir = _resolve_plane_dir(run_dir, db)

    ops = _load_ops(plane_dir)
    stat = _load_stat(plane_dir)
    iscell = _load_iscell(plane_dir, len(stat))

    generated_files: dict[str, str] = {}
    def _attempt_preview(key: str, render_fn) -> None:
        try:
            generated_files[key] = str(render_fn())
        except SystemExit as exc:
            if args.only == "all":
                print(f"Skipped {key}: {exc}")
                return
            raise

    if args.only in {"all", "overlay"}:
        _attempt_preview(
            "overlay_preview",
            lambda: _make_overlay_preview(
                plane_dir, ops, stat, iscell, args.start_frame, args.num_frames, args.fps, args.gain, args.q_min, args.q_max
            ),
        )
    if args.only in {"all", "motion"}:
        _attempt_preview(
            "motion_preview",
            lambda: _make_motion_preview(
                plane_dir,
                manifest.get("raw_tiffs", []),
                ops,
                args.start_frame,
                args.num_frames,
                args.fps,
                args.gain,
                args.q_min,
                args.q_max,
            ),
        )
    if args.only in {"all", "three_panel"}:
        _attempt_preview(
            "three_panel_preview",
            lambda: _make_three_panel_preview(
                plane_dir,
                manifest.get("raw_tiffs", []),
                ops,
                stat,
                iscell,
                args.start_frame,
                args.num_frames,
                args.fps,
                args.gain,
                args.q_min,
                args.q_max,
            ),
        )
    if args.only in {"all", "reconstruction"}:
        _attempt_preview(
            "reconstruction_preview",
            lambda: _make_reconstruction_preview(
                plane_dir,
                ops,
                stat,
                iscell,
                args.start_frame,
                args.num_frames,
                args.fps,
                args.gain,
                args.q_min,
                args.q_max,
            ),
        )
    generated_files.update(_make_projection_exports(plane_dir, ops))
    generated_files["static_overlay"] = str(_make_static_overlay_image(plane_dir, ops, stat, iscell))
    generated_files["accepted_fill_overlay"] = str(_make_accepted_fill_overlay_image(plane_dir, ops, stat, iscell))
    generated_files["contour_figure"] = str(_make_contour_figure(plane_dir, ops, stat, iscell))
    generated_files["accepted_contour_figure"] = str(
        _make_filtered_contour_figure(
            plane_dir / "suite2p_accepted_contours.png",
            "Suite2p Accepted ROI Overlay",
            ops,
            stat,
            iscell,
            mode="accepted",
        )
    )
    generated_files["rejected_contour_figure"] = str(
        _make_filtered_contour_figure(
            plane_dir / "suite2p_rejected_contours.png",
            "Suite2p Rejected ROI Overlay",
            ops,
            stat,
            iscell,
            mode="rejected",
        )
    )
    generated_files["trace_preview_figure"] = str(_make_trace_preview_figure(plane_dir, ops, stat, iscell))
    generated_files["roi_size_summary"] = str(_make_roi_size_figure(plane_dir, stat, iscell))
    generated_files.update(_make_trace_csv_exports(plane_dir, ops, iscell))
    generated_files["summary_json"] = str(
        _make_summary(run_dir, plane_dir, manifest, runtime, ops, stat, iscell, generated_files)
    )
    generated_files["qc_report"] = str(_make_qc_report(run_dir, plane_dir, manifest, runtime, ops, stat, iscell))

    print("Suite2p export artifacts created:")
    for key, value in generated_files.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
