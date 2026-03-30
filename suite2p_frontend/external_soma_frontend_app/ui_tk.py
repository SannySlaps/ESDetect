from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from suite2p_frontend_app.ui_tk import Suite2pFrontendApp as BaseSuite2pFrontendApp

from .config import APP_NAME, APP_SUBTITLE, DEFAULT_ESDETECT_PRESET
from .controller import ExternalSomaController


class ExternalSomaFrontendApp(BaseSuite2pFrontendApp):
    CONTROLLER_CLASS = ExternalSomaController
    APP_TITLE = APP_NAME
    HEADER_SUBTITLE = APP_SUBTITLE

    def _init_shared_vars(self) -> None:
        super()._init_shared_vars()
        self._vars["esdetect_overlay_preset"] = tk.StringVar(value="quick_review")
        self._vars["esdetect_overlay_generate_during_run"] = tk.BooleanVar(value=False)
        self._vars["show_esdetect_overlay_previews"] = tk.BooleanVar(value=True)
        self._vars["show_esdetect_motion_params"] = tk.BooleanVar(value=False)
        self._vars["show_esdetect_extraction_params"] = tk.BooleanVar(value=False)
        self._vars["show_esdetect_help_workflow"] = tk.BooleanVar(value=True)
        self._vars["param_ext_source_image"] = tk.StringVar(value="proposal_soma_blob_transient")
        self._vars["param_ext_bg_sigma"] = tk.StringVar(value="14.0")
        self._vars["param_ext_blob_sigma"] = tk.StringVar(value="4.5")
        self._vars["param_ext_blob_weight"] = tk.StringVar(value="1.8")
        self._vars["param_ext_transient_weight"] = tk.StringVar(value="1.4")
        self._vars["param_ext_thresh_q"] = tk.StringVar(value="93.8")
        self._vars["param_ext_peak_fraction"] = tk.StringVar(value="0.18")
        self._vars["param_ext_min_area"] = tk.StringVar(value="80")
        self._vars["param_ext_max_area"] = tk.StringVar(value="1900")
        self._vars["param_ext_dilate_iters"] = tk.StringVar(value="2")
        self._vars["param_ext_motion_correct"] = tk.BooleanVar(value=True)
        self._vars["param_ext_registration_downsample"] = tk.StringVar(value="0.5")
        self._vars["param_ext_max_shift"] = tk.StringVar(value="15.0")
        self._vars["param_ext_inner_iters"] = tk.StringVar(value="4")
        self._vars["param_ext_outer_iters"] = tk.StringVar(value="12")
        self._vars["param_ext_neuropil_coeff"] = tk.StringVar(value="0.7")
        self._vars["param_ext_baseline_percentile"] = tk.StringVar(value="20.0")

    def _build_analysis_parameters_tab(self, frame: ttk.Frame) -> None:
        super()._build_analysis_parameters_tab(frame)
        if "parameter_preset_path" in self._vars:
            self._vars["parameter_preset_path"].set(str(DEFAULT_ESDETECT_PRESET))

    def _extend_analysis_parameters_tab(self, content: ttk.Frame, *, start_row: int) -> None:
        motion_toggle = ttk.Frame(content)
        motion_toggle.grid(row=start_row, column=0, columnspan=2, sticky="ew", pady=(12, 6))
        ttk.Button(motion_toggle, text="Show ESDetect Motion Correction Parameters", command=lambda: self._toggle_section("show_esdetect_motion_params", motion_box, motion_toggle, "ESDetect Motion Correction Parameters")).pack(anchor="w")
        motion_box = ttk.LabelFrame(content, text="Motion Correction Parameters", padding=12)
        motion_box.grid(row=start_row + 1, column=0, columnspan=2, sticky="ew")
        motion_box.columnconfigure(1, weight=1)
        motion_box.columnconfigure(3, weight=1)
        ttk.Checkbutton(motion_box, text="Rigid Motion Correction", variable=self._vars["param_ext_motion_correct"]).grid(row=0, column=0, sticky="w", pady=6)
        self._param_entry_2col(motion_box, 0, 1, "registration_downsample", "param_ext_registration_downsample")
        self._param_entry_2col(motion_box, 1, 0, "max_shift", "param_ext_max_shift")

        extraction_toggle = ttk.Frame(content)
        extraction_toggle.grid(row=start_row + 2, column=0, columnspan=2, sticky="ew", pady=(12, 6))
        ttk.Button(extraction_toggle, text="Show ESDetect Detection / Extraction Parameters", command=lambda: self._toggle_section("show_esdetect_extraction_params", box, extraction_toggle, "ESDetect Detection / Extraction Parameters")).pack(anchor="w")
        box = ttk.LabelFrame(content, text="Extraction Parameters", padding=12)
        box.grid(row=start_row + 3, column=0, columnspan=2, sticky="ew")
        box.columnconfigure(1, weight=1)
        box.columnconfigure(3, weight=1)
        self._param_entry_2col(box, 0, 0, "source_image", "param_ext_source_image")
        self._param_entry_2col(box, 0, 1, "bg_sigma", "param_ext_bg_sigma")
        self._param_entry_2col(box, 1, 0, "blob_sigma", "param_ext_blob_sigma")
        self._param_entry_2col(box, 1, 1, "blob_weight", "param_ext_blob_weight")
        self._param_entry_2col(box, 2, 0, "transient_weight", "param_ext_transient_weight")
        self._param_entry_2col(box, 2, 1, "thresh_q", "param_ext_thresh_q")
        self._param_entry_2col(box, 3, 0, "peak_fraction", "param_ext_peak_fraction")
        self._param_entry_2col(box, 3, 1, "min_area", "param_ext_min_area")
        self._param_entry_2col(box, 4, 0, "max_area", "param_ext_max_area")
        self._param_entry_2col(box, 4, 1, "dilate_iters", "param_ext_dilate_iters")
        self._param_entry_2col(box, 5, 0, "inner_iters", "param_ext_inner_iters")
        self._param_entry_2col(box, 5, 1, "outer_iters", "param_ext_outer_iters")
        self._param_entry_2col(box, 6, 0, "neuropil_coeff", "param_ext_neuropil_coeff")
        self._param_entry_2col(box, 6, 1, "baseline_percentile", "param_ext_baseline_percentile")
        motion_box.grid_remove()
        box.grid_remove()

    def _extend_preview_tab_top(self, content: ttk.Frame, *, start_row: int) -> int:
        toggle = ttk.Frame(content)
        toggle.grid(row=start_row, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Button(
            toggle,
            text="Hide ESDetect Overlay Preview",
            command=lambda: self._toggle_section("show_esdetect_overlay_previews", box, toggle, "ESDetect Overlay Preview"),
        ).pack(anchor="w")
        box = ttk.LabelFrame(content, text="ESDetect Optional Overlay Videos", padding=12)
        box.grid(row=start_row + 1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        box.columnconfigure(1, weight=1)
        ttk.Label(
            box,
            text=(
                "Optional ESDetect-only review videos. These are not generated during normal runs.\n"
                "Use a preset when you want a shorter review movie or a presentation-friendly overlay."
            ),
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        ttk.Label(box, text="Preset").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Combobox(
            box,
            textvariable=self._vars["esdetect_overlay_preset"],
            values=("quick_review", "presentation", "full_session"),
            state="readonly",
        ).grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Checkbutton(
            box,
            text="Generate selected overlay automatically during run",
            variable=self._vars["esdetect_overlay_generate_during_run"],
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 6))
        actions = ttk.Frame(box)
        actions.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self._make_action_button(actions, "Render Selected ESDetect Overlay", self._render_selected_esdetect_overlay, background=True).grid(row=0, column=0, padx=(0, 8), pady=6)
        self._make_action_button(actions, "Open Selected ESDetect Overlay", self._open_selected_esdetect_overlay).grid(row=0, column=1, padx=(0, 8), pady=6)
        ttk.Label(
            box,
            text=(
                "Presets:\n"
                "- quick_review: shorter, faster review movie\n"
                "- presentation: smoother, nicer playback\n"
                "- full_session: exact full-session overlay when needed"
            ),
            justify="left",
        ).grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        return start_row + 2

    def _apply_additional_parameter_payload(self, payload: dict) -> None:
        ops = payload["ops"]
        self._vars["param_ext_motion_correct"].set(bool(ops.get("motion_correct", True)))
        self._vars["esdetect_overlay_generate_during_run"].set(bool(ops.get("auto_generate_overlay_video", False)))
        self._vars["esdetect_overlay_preset"].set(str(ops.get("auto_overlay_preset", "quick_review")))
        self._vars["param_ext_registration_downsample"].set(str(ops.get("registration_downsample", "0.5")))
        self._vars["param_ext_max_shift"].set(str(ops.get("max_shift", "15.0")))
        self._vars["param_ext_source_image"].set(str(ops.get("source_image", "proposal_soma_blob_transient")))
        self._vars["param_ext_bg_sigma"].set(str(ops.get("bg_sigma", "14.0")))
        self._vars["param_ext_blob_sigma"].set(str(ops.get("blob_sigma", "4.5")))
        self._vars["param_ext_blob_weight"].set(str(ops.get("blob_weight", "1.8")))
        self._vars["param_ext_transient_weight"].set(str(ops.get("transient_weight", "1.4")))
        self._vars["param_ext_thresh_q"].set(str(ops.get("thresh_q", "93.8")))
        self._vars["param_ext_peak_fraction"].set(str(ops.get("peak_fraction", "0.18")))
        self._vars["param_ext_min_area"].set(str(ops.get("min_area", "80")))
        self._vars["param_ext_max_area"].set(str(ops.get("max_area", "1900")))
        self._vars["param_ext_dilate_iters"].set(str(ops.get("dilate_iters", "2")))
        self._vars["param_ext_inner_iters"].set(str(ops.get("inner_iters", "4")))
        self._vars["param_ext_outer_iters"].set(str(ops.get("outer_iters", "12")))
        self._vars["param_ext_neuropil_coeff"].set(str(ops.get("neuropil_coeff", "0.7")))
        self._vars["param_ext_baseline_percentile"].set(str(ops.get("baseline_percentile", "20.0")))

    def _extend_parameter_payload(self, payload: dict, *, to_number) -> None:
        payload["ops"].update(
            {
                "motion_correct": bool(self._vars["param_ext_motion_correct"].get()),
                "auto_generate_overlay_video": bool(self._vars["esdetect_overlay_generate_during_run"].get()),
                "auto_overlay_preset": self._vars["esdetect_overlay_preset"].get().strip() or "quick_review",
                "registration_downsample": to_number(self._vars["param_ext_registration_downsample"].get()),
                "max_shift": to_number(self._vars["param_ext_max_shift"].get()),
                "source_image": self._vars["param_ext_source_image"].get().strip(),
                "bg_sigma": to_number(self._vars["param_ext_bg_sigma"].get()),
                "blob_sigma": to_number(self._vars["param_ext_blob_sigma"].get()),
                "blob_weight": to_number(self._vars["param_ext_blob_weight"].get()),
                "transient_weight": to_number(self._vars["param_ext_transient_weight"].get()),
                "thresh_q": to_number(self._vars["param_ext_thresh_q"].get()),
                "peak_fraction": to_number(self._vars["param_ext_peak_fraction"].get()),
                "min_area": to_number(self._vars["param_ext_min_area"].get()),
                "max_area": to_number(self._vars["param_ext_max_area"].get()),
                "dilate_iters": to_number(self._vars["param_ext_dilate_iters"].get()),
                "inner_iters": to_number(self._vars["param_ext_inner_iters"].get()),
                "outer_iters": to_number(self._vars["param_ext_outer_iters"].get()),
                "neuropil_coeff": to_number(self._vars["param_ext_neuropil_coeff"].get()),
                "baseline_percentile": to_number(self._vars["param_ext_baseline_percentile"].get()),
            }
        )

    def _render_selected_esdetect_overlay(self) -> None:
        preset = self._vars["esdetect_overlay_preset"].get().strip()
        if not preset:
            raise RuntimeError("Select an ESDetect overlay preset first.")
        self.controller.render_overlay_preset(preset)

    def _open_selected_esdetect_overlay(self) -> None:
        preset = self._vars["esdetect_overlay_preset"].get().strip()
        if not preset:
            raise RuntimeError("Select an ESDetect overlay preset first.")
        self.controller.open_overlay_preset(preset)

    def _extend_help_tab(self, content: ttk.Frame, *, start_row: int) -> None:
        toggle = ttk.Frame(content)
        toggle.grid(row=start_row, column=0, sticky="ew", pady=(18, 6))
        ttk.Button(toggle, text="Hide ESDetect Downstream Workflow", command=lambda: self._toggle_section("show_esdetect_help_workflow", box, toggle, "ESDetect Downstream Workflow")).pack(anchor="w")
        box = ttk.LabelFrame(content, text="ESDetect Downstream Workflow", padding=12)
        box.grid(row=start_row + 1, column=0, sticky="ew")
        text = (
            "Use this after an ESDetect batch completes.\n\n"
            "1. Open a finished session\n"
            "- Load the session and confirm it points to analysis\\outputs\\suite2p\\plane0.\n\n"
            "2. Inspect the standard preview artifacts\n"
            "- Check motion, overlay, three-panel, and reconstruction previews.\n\n"
            "3. Open ROI Curation\n"
            "- Load the active curation payload.\n"
            "- Review accepted contours, rejected contours, and trace behavior.\n\n"
            "4. Curate accepted and rejected bins\n"
            "- Move ROIs between accepted and rejected as needed.\n\n"
            "5. Handle rare misses manually\n"
            "- Manually add a missed soma or delete a bad ROI.\n\n"
            "6. Save curation labels\n"
            "- Save the current accepted and rejected state back to iscell.npy.\n\n"
            "7. Finalize ROI edits\n"
            "- Run Finalize ROI Edits to push the edited plane0 state back into the active outputs.\n\n"
            "8. Regenerate exports and artifacts if needed\n"
            "- Use Export Artifacts to refresh previews, overlays, QC outputs, and summaries.\n\n"
            "9. Export trace and session summaries\n"
            "- Build Session Summary CSV and Build Event Summary CSV when needed.\n\n"
            "10. Export downstream package\n"
            "- Use Export Downstream Package when the session is ready for hand-off or later analysis.\n\n"
            "11. Generate extra ESDetect overlay videos only when useful\n"
            "- In Video Previews, use quick_review, presentation, or full_session only for sessions that need extra review.\n\n"
            "12. Repeat across sessions\n"
            "- Once one session looks good, move to the next finished session from the batch.\n\n"
            "Working rule:\n"
            "- If the session looks broadly good, curate and move on.\n"
            "- If it has a rare miss, manually add it.\n"
            "- If it has a systematic failure pattern, revisit ESDetect tuning."
        )
        label = tk.Label(
            box,
            text=text,
            justify="left",
            anchor="nw",
            bg=self.colors["bg"],
            fg=self.colors["text"],
            wraplength=980,
            font=("TkDefaultFont", 11),
        )
        label.grid(row=0, column=0, sticky="nw")


def launch() -> None:
    app = ExternalSomaFrontendApp()
    app.mainloop()
