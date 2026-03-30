#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt


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


def _roi_bbox(mask: np.ndarray, pad: int, shape: tuple[int, int]) -> tuple[int, int, int, int]:
    ys, xs = np.where(mask > 0)
    if ys.size == 0:
        return 0, shape[0], 0, shape[1]
    y0 = max(0, int(ys.min()) - pad)
    y1 = min(shape[0], int(ys.max()) + pad + 1)
    x0 = max(0, int(xs.min()) - pad)
    x1 = min(shape[1], int(xs.max()) + pad + 1)
    return y0, y1, x0, x1


def _draw_mask_panel(ax: plt.Axes, background: np.ndarray, roi_mask: np.ndarray, neuropil_mask: np.ndarray, title: str) -> None:
    base = np.stack([background, background, background], axis=-1)
    roi = roi_mask.astype(bool)
    neu = neuropil_mask.astype(bool)
    if np.any(neu):
        base[neu] = 0.65 * base[neu] + 0.35 * np.array([0.2, 0.45, 1.0], dtype=np.float32)
    if np.any(roi):
        base[roi] = 0.35 * base[roi] + 0.65 * np.array([1.0, 0.78, 0.15], dtype=np.float32)
    ax.imshow(np.clip(base, 0.0, 1.0))
    ax.set_title(title, fontsize=9)
    ax.axis("off")


def _trace_stats(trace: np.ndarray) -> dict[str, float]:
    trace = np.asarray(trace, dtype=np.float32)
    if trace.size == 0:
        return {"mean": 0.0, "std": 0.0, "peak": 0.0}
    return {
        "mean": float(np.mean(trace)),
        "std": float(np.std(trace)),
        "peak": float(np.max(trace)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build ROI-by-ROI review figures for external soma extraction outputs.")
    parser.add_argument("--extraction-dir", required=True, help="Path to an ESDetect_extraction folder.")
    parser.add_argument("--label", default="review01", help="Review output label under the extraction directory.")
    parser.add_argument("--rois-per-page", type=int, default=6, help="Number of ROI rows per review page.")
    parser.add_argument(
        "--sort-by",
        choices=["roi_index", "peak_dff", "std_dff", "area"],
        default="peak_dff",
        help="How to sort ROIs in the review pages.",
    )
    parser.add_argument("--crop-pad", type=int, default=20, help="Padding in pixels around each ROI crop.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    extraction_dir = _resolve_extraction_dir(args.extraction_dir)
    manifest = _load_json(extraction_dir / "extraction_manifest.json")
    seg_manifest = _load_json(Path(manifest["segmentation_dir"]) / "segmentation_manifest.json")

    out_dir = extraction_dir / args.label
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    arrays_dir = extraction_dir / "arrays"
    F = np.load(arrays_dir / "F.npy", allow_pickle=True)
    Fneu = np.load(arrays_dir / "Fneu.npy", allow_pickle=True)
    dff = np.load(arrays_dir / "dff.npy", allow_pickle=True)
    roi_masks = np.load(arrays_dir / "roi_masks.npy", allow_pickle=True).astype(np.uint8)
    neuropil_masks = np.load(arrays_dir / "neuropil_masks.npy", allow_pickle=True).astype(np.uint8)

    seg_arrays = Path(manifest["segmentation_dir"]) / "arrays"
    mean_image = np.load(seg_arrays / "mean_image.npy", allow_pickle=True)
    proposal_base = np.load(seg_arrays / "proposal_base.npy", allow_pickle=True)
    mean_norm = _safe_normalize(mean_image)
    proposal_norm = _safe_normalize(proposal_base)
    fs = float(manifest.get("frame_rate_hz", 0.0) or 0.0)

    rows = []
    for idx in range(roi_masks.shape[0]):
        area = int(np.sum(roi_masks[idx] > 0))
        dff_stats = _trace_stats(dff[idx])
        rows.append(
            {
                "roi_index": int(idx),
                "area": area,
                "mean_dff": dff_stats["mean"],
                "std_dff": dff_stats["std"],
                "peak_dff": dff_stats["peak"],
            }
        )

    key_name = str(args.sort_by)
    rows.sort(key=lambda row: row[key_name], reverse=(key_name != "roi_index"))

    rois_per_page = max(1, int(args.rois_per_page))
    page_count = int(np.ceil(len(rows) / rois_per_page)) if rows else 0

    for page_idx in range(page_count):
        batch = rows[page_idx * rois_per_page:(page_idx + 1) * rois_per_page]
        fig, axes = plt.subplots(len(batch), 3, figsize=(12, 2.7 * len(batch)))
        if len(batch) == 1:
            axes = np.asarray([axes])
        for row_idx, row in enumerate(batch):
            roi_idx = int(row["roi_index"])
            y0, y1, x0, x1 = _roi_bbox(roi_masks[roi_idx], int(args.crop_pad), roi_masks.shape[1:])

            _draw_mask_panel(
                axes[row_idx, 0],
                mean_norm[y0:y1, x0:x1],
                roi_masks[roi_idx, y0:y1, x0:x1],
                neuropil_masks[roi_idx, y0:y1, x0:x1],
                title=f"ROI {roi_idx} mean view",
            )
            _draw_mask_panel(
                axes[row_idx, 1],
                proposal_norm[y0:y1, x0:x1],
                roi_masks[roi_idx, y0:y1, x0:x1],
                neuropil_masks[roi_idx, y0:y1, x0:x1],
                title=f"ROI {roi_idx} proposal view",
            )

            x = np.arange(dff.shape[1], dtype=np.float32)
            if fs > 0:
                x = x / fs
            ax = axes[row_idx, 2]
            ax.plot(x, dff[roi_idx], color="#d97706", linewidth=0.8, label="dF/F")
            ax.plot(x, F[roi_idx], color="#22c55e", linewidth=0.5, alpha=0.35, label="F")
            ax.plot(x, Fneu[roi_idx], color="#60a5fa", linewidth=0.5, alpha=0.35, label="Fneu")
            ax.set_title(
                f"ROI {roi_idx} traces | area={row['area']} peak_dff={row['peak_dff']:.2f}",
                fontsize=9,
            )
            ax.set_xlabel("Time (s)" if fs > 0 else "Frame")
            ax.set_ylabel("Signal")
            ax.grid(alpha=0.2, linewidth=0.4)
            if row_idx == 0:
                ax.legend(loc="upper right", fontsize=7, frameon=False)
        fig.tight_layout()
        fig.savefig(images_dir / f"trace_review_page_{page_idx + 1:02d}.png", dpi=160)
        plt.close(fig)

    review_manifest = {
        "created_at": datetime.now().isoformat(),
        "extraction_dir": str(extraction_dir),
        "segmentation_dir": str(manifest["segmentation_dir"]),
        "session_dir": str(manifest["session_dir"]),
        "output_dir": str(out_dir),
        "sort_by": key_name,
        "roi_count": int(roi_masks.shape[0]),
        "page_count": int(page_count),
        "rois_per_page": rois_per_page,
        "source_image": seg_manifest.get("source_image", ""),
        "rows": rows,
    }
    _write_json(out_dir / "trace_review_manifest.json", review_manifest)

    print(f"Extraction directory: {extraction_dir}")
    print(f"Output directory: {out_dir}")
    print(f"ROI count: {roi_masks.shape[0]}")
    print(f"Pages written: {page_count}")
    if page_count:
        print(f"First page: {images_dir / 'trace_review_page_01.png'}")


if __name__ == "__main__":
    main()
