from __future__ import annotations

import queue
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import numpy as np
from PIL import Image, ImageTk

from .config import ACQUISITION_APP_SOURCE, APP_NAME, PRESETS_ROOT, RUNS_ROOT, SANDBOX_ROOT, SCIENTIFICA_ROOT
from .controller import Suite2pController
from .state import RuntimeState


class Suite2pFrontendApp(tk.Tk):
    CONTROLLER_CLASS = Suite2pController
    APP_TITLE = APP_NAME
    HEADER_SUBTITLE = "CaImAn-style desktop frontend for the Suite2p sandbox backend."

    def __init__(self) -> None:
        super().__init__()
        self.title(self.APP_TITLE)
        self.geometry("1380x900")
        self.minsize(1180, 760)
        self._busy = False
        self._batch_stop_requested = False
        self._event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._action_buttons: list[ttk.Button] = []
        self.state_obj = RuntimeState()
        self.controller = self.CONTROLLER_CLASS(self.state_obj, logger=self._threadsafe_log)
        self.controller.set_status_callback(self._threadsafe_status)
        self._vars: dict[str, tk.Variable] = {}
        self._configure_theme()
        self._build_ui()
        self._bind_global_scrollwheel()
        self.controller.load_notification_settings()
        self._load_parameter_defaults()
        self._load_notification_vars()
        self._log(f"Suite2p sandbox root: {SANDBOX_ROOT}")
        self.after(100, self._process_event_queue)

    def _configure_theme(self) -> None:
        self.colors = {
            "bg": "#111417",
            "panel": "#1a2026",
            "panel_alt": "#222a33",
            "panel_soft": "#161b21",
            "text": "#f5f7fa",
            "muted": "#aab4bf",
            "accent": "#5dd6c0",
            "accent_active": "#7ae4d1",
            "border": "#2e3945",
            "entry": "#0e1217",
            "button_text": "#08110f",
        }
        self.configure(bg=self.colors["bg"])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=self.colors["bg"], foreground=self.colors["text"])
        style.configure("TFrame", background=self.colors["bg"])
        style.configure("TLabel", background=self.colors["bg"], foreground=self.colors["text"])
        style.configure("Header.TFrame", background=self.colors["panel_soft"])
        style.configure("HeaderTitle.TLabel", background=self.colors["panel_soft"], foreground=self.colors["text"], font=("TkDefaultFont", 18, "bold"))
        style.configure("HeaderSub.TLabel", background=self.colors["panel_soft"], foreground=self.colors["muted"], font=("TkDefaultFont", 10))
        style.configure(
            "TButton",
            background=self.colors["accent"],
            foreground=self.colors["button_text"],
            padding=(12, 7),
            borderwidth=0,
            font=("TkDefaultFont", 10, "bold"),
        )
        style.map(
            "TButton",
            background=[("active", self.colors["accent_active"]), ("disabled", self.colors["panel_alt"])],
            foreground=[("disabled", self.colors["muted"])],
        )
        style.configure(
            "TEntry",
            fieldbackground=self.colors["entry"],
            background=self.colors["entry"],
            foreground=self.colors["text"],
            insertcolor=self.colors["text"],
            bordercolor=self.colors["border"],
            padding=6,
        )
        style.configure("TNotebook", background=self.colors["bg"], borderwidth=0, tabmargins=(8, 8, 8, 0))
        style.configure("TNotebook.Tab", background=self.colors["panel"], foreground=self.colors["muted"], padding=(16, 10), borderwidth=0, font=("TkDefaultFont", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", self.colors["panel_alt"]), ("active", self.colors["panel_alt"])], foreground=[("selected", self.colors["text"]), ("active", self.colors["text"])])
        style.configure("TLabelFrame", background=self.colors["bg"], foreground=self.colors["text"], bordercolor=self.colors["border"], relief="solid")
        style.configure("TLabelFrame.Label", background=self.colors["bg"], foreground=self.colors["text"], font=("TkDefaultFont", 10, "bold"))
        style.configure(
            "TCombobox",
            fieldbackground=self.colors["entry"],
            background=self.colors["entry"],
            foreground=self.colors["text"],
            arrowcolor=self.colors["accent"],
            bordercolor=self.colors["border"],
            lightcolor=self.colors["border"],
            darkcolor=self.colors["border"],
            padding=4,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", self.colors["entry"]), ("disabled", self.colors["panel_alt"])],
            background=[("readonly", self.colors["entry"]), ("disabled", self.colors["panel_alt"])],
            foreground=[("readonly", self.colors["text"]), ("disabled", self.colors["muted"])],
            arrowcolor=[("active", self.colors["accent_active"]), ("readonly", self.colors["accent"]), ("disabled", self.colors["muted"])],
            bordercolor=[("focus", self.colors["accent"]), ("readonly", self.colors["border"])],
        )
        self.option_add("*TCombobox*Listbox.background", self.colors["entry"])
        self.option_add("*TCombobox*Listbox.foreground", self.colors["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", self.colors["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", self.colors["button_text"])
        style.configure(
            "TProgressbar",
            troughcolor="#000000",
            background=self.colors["accent"],
            bordercolor=self.colors["border"],
            lightcolor=self.colors["accent"],
            darkcolor=self.colors["accent"],
        )
        style.configure(
            "Curation.Horizontal.TScale",
            background=self.colors["bg"],
            troughcolor=self.colors["panel_alt"],
            bordercolor=self.colors["border"],
            lightcolor=self.colors["accent"],
            darkcolor=self.colors["accent"],
        )
        style.configure(
            "Treeview",
            background="#000000",
            fieldbackground="#000000",
            foreground=self.colors["accent"],
            bordercolor=self.colors["border"],
            rowheight=24,
        )
        style.map(
            "Treeview",
            background=[("selected", self.colors["accent"])],
            foreground=[("selected", "#000000")],
        )
        style.configure(
            "Treeview.Heading",
            background="#000000",
            foreground=self.colors["accent"],
            bordercolor=self.colors["border"],
            font=("TkDefaultFont", 10, "bold"),
        )
        style.map(
            "Treeview.Heading",
            background=[("active", self.colors["panel_alt"])],
            foreground=[("active", self.colors["accent_active"])],
        )

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._init_shared_vars()

        header = ttk.Frame(self, style="Header.TFrame", padding=(20, 16))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=self.APP_TITLE, style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text=self.HEADER_SUBTITLE,
            style="HeaderSub.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        body = ttk.Frame(self, padding=(12, 12))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(body)
        notebook.grid(row=0, column=0, sticky="nsew")
        self.notebook = notebook
        self.tabs: dict[str, ttk.Frame] = {}
        for name in ["Acquisition", "Run Manager", "Analysis Parameters", "Video Previews", "Post-Run", "ROI Curation", "Portable Transfer", "Notifications", "Definitions", "Help"]:
            frame = ttk.Frame(notebook, padding=16)
            notebook.add(frame, text=name)
            self.tabs[name] = frame

        self._build_acquisition_tab(self.tabs["Acquisition"])
        self._build_analysis_parameters_tab(self.tabs["Analysis Parameters"])
        self._build_definitions_tab(self.tabs["Definitions"])
        self._build_batch_tab(self.tabs["Run Manager"])
        self._build_preview_tab(self.tabs["Video Previews"])
        self._build_curation_tab(self.tabs["ROI Curation"])
        self._build_post_run_tab(self.tabs["Post-Run"])
        self._build_portable_transfer_tab(self.tabs["Portable Transfer"])
        self._build_notifications_tab(self.tabs["Notifications"])
        self._build_help_tab(self.tabs["Help"])
        self._bind_curation_shortcuts()

        footer = ttk.Frame(self, padding=(12, 8))
        footer.grid(row=2, column=0, sticky="ew")
        self._vars["show_status_log"] = tk.BooleanVar(value=True)
        toggle_row = ttk.Frame(footer)
        toggle_row.pack(fill="x")
        self.status_toggle_btn = ttk.Button(toggle_row, text="Hide Progress / Status", command=self._toggle_status_log)
        self.status_toggle_btn.pack(anchor="w")

        self.status_log_container = ttk.Frame(footer)
        self.status_log_container.pack(fill="x", expand=False, pady=(8, 0))
        status_frame = ttk.LabelFrame(self.status_log_container, text="Status")
        status_frame.pack(fill="x")
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(status_frame, textvariable=self.status_var).pack(anchor="w", padx=10, pady=(8, 4))
        ttk.Label(status_frame, text="Progress").pack(anchor="w", padx=10, pady=(0, 4))
        self.progress_bar = ttk.Progressbar(status_frame, mode="indeterminate")
        self.progress_bar.pack(fill="x", padx=10, pady=(0, 10))

        log_frame = ttk.LabelFrame(self.status_log_container, text="Log")
        log_frame.pack(fill="x", expand=False, pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=8, bg=self.colors["entry"], fg=self.colors["text"], insertbackground=self.colors["text"], relief="flat", wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        self.log_scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 8), pady=8)
        self.log_text.configure(yscrollcommand=self.log_scrollbar.set)

    def _init_shared_vars(self) -> None:
        self._vars["session_path"] = tk.StringVar()
        self._vars["run_name"] = tk.StringVar()
        self._vars["run_dir"] = tk.StringVar()
        self._vars["output_dir"] = tk.StringVar()
        self._vars["input_root"] = tk.StringVar()
        self._vars["output_root"] = tk.StringVar()
        self._vars["run_skip_existing"] = tk.BooleanVar(value=False)
        self._vars["run_archive_existing"] = tk.BooleanVar(value=False)
        self._vars["run_cleanup_temp"] = tk.BooleanVar(value=False)
        self._vars["batch_skip_no_soma"] = tk.BooleanVar(value=False)
        self._vars["batch_skip_completed"] = tk.BooleanVar(value=False)
        self._vars["transfer_master_root"] = tk.StringVar(value=str(SCIENTIFICA_ROOT))
        self._vars["transfer_portable_root"] = tk.StringVar()
        self._vars["transfer_require_outputs"] = tk.BooleanVar(value=True)
        self._vars["transfer_overwrite_existing"] = tk.BooleanVar(value=True)
        self._vars["transfer_unfinished_only"] = tk.BooleanVar(value=True)

    def _build_analysis_setup_tab(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Input Root (Session Folder)").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(frame, textvariable=self._vars["session_path"]).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(frame, text="Browse", command=self._browse_session).grid(row=0, column=2, padx=(8, 0), pady=6, sticky="w")

        ttk.Label(frame, text="Optional Run Name").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(frame, textvariable=self._vars["run_name"]).grid(row=1, column=1, sticky="ew", pady=6)

        ttk.Label(frame, text="Input Root").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=6)
        input_entry = ttk.Entry(frame, textvariable=self._vars["input_root"])
        input_entry.grid(row=2, column=1, sticky="ew", pady=6)
        input_entry.configure(state="readonly")

        ttk.Label(frame, text="Output Root (HDD / Session Analysis)").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=6)
        output_entry = ttk.Entry(frame, textvariable=self._vars["output_root"])
        output_entry.grid(row=3, column=1, sticky="ew", pady=6)
        output_entry.configure(state="readonly")

        blurb = (
            "Use the same session folder structure you already use for CaImAn.\n"
            "This tab is the Suite2p equivalent of the CaImAn analysis setup area.\n"
            "Input Root is the session folder. Output Root is the session analysis output folder on the HDD."
        )
        ttk.Label(frame, text=blurb, justify="left").grid(row=4, column=0, columnspan=3, sticky="w", pady=(14, 0))

        options = ttk.Frame(frame)
        options.grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Checkbutton(
            options,
            text="Skip run if this session already has Suite2p outputs",
            variable=self._vars["run_skip_existing"],
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Checkbutton(
            options,
            text="Archive existing outputs before overwrite",
            variable=self._vars["run_archive_existing"],
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=4)

        actions = ttk.Frame(frame)
        actions.grid(row=6, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self._make_action_button(actions, "Prepare Suite2p Run", self._prepare_run, background=True).grid(row=0, column=0, padx=(0, 8))
        self._make_action_button(actions, "Run From Session", self._run_from_session, background=True).grid(row=0, column=1, padx=(0, 8))

    def _make_scrollable_tab(self, frame: ttk.Frame, *, columns: int = 1) -> ttk.Frame:
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        outer = ttk.Frame(frame)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, bg=self.colors["bg"], highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas)
        for idx in range(columns):
            content.columnconfigure(idx, weight=1)
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        outer.bind("<Configure>", lambda e: canvas.itemconfigure(canvas_window, width=e.width - scrollbar.winfo_width()))
        return content

    def _build_acquisition_tab(self, frame: ttk.Frame) -> None:
        frame = self._make_scrollable_tab(frame)
        blurb = (
            "Acquisition remains a separate application.\n"
            "Use this tab to launch the acquisition app.\n"
            "Session loading for Suite2p happens from Run Manager / Post-Run."
        )
        ttk.Label(frame, text=blurb, justify="left").grid(row=0, column=0, sticky="ew", pady=(0, 12))

        actions = ttk.Frame(frame)
        actions.grid(row=1, column=0, sticky="w", pady=(0, 10))
        self._make_action_button(actions, "Launch Acquisition App", self._launch_acquisition_app).grid(row=0, column=0, padx=(0, 8), pady=6)

        help_text = (
            "Workflow:\n"
            "1. Launch the acquisition app\n"
            "2. Acquire into a Session_<NNN> folder\n"
            "3. Load/import sessions from Run Manager or Post-Run using the normal Suite2p workflow\n\n"
            f"Acquisition app source:\n{ACQUISITION_APP_SOURCE}"
        )
        ttk.Label(frame, text=help_text, justify="left", wraplength=1000).grid(row=2, column=0, sticky="ew")

    def _build_batch_tab(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self._vars["batch_skip_existing"] = tk.BooleanVar(value=True)
        self._vars["batch_archive_existing"] = tk.BooleanVar(value=False)
        self._vars["batch_cleanup_temp"] = tk.BooleanVar(value=False)
        self._vars["batch_parent_root"] = tk.StringVar()
        self._vars["batch_stop_requested"] = tk.BooleanVar(value=False)

        outer = ttk.Frame(frame)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, bg=self.colors["bg"], highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas)
        content.columnconfigure(0, weight=1)
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        outer.bind("<Configure>", lambda e: canvas.itemconfigure(canvas_window, width=e.width - scrollbar.winfo_width()))

        blurb = (
            "Use this tab to run one session or a queued set of sessions.\n"
            "The current Analysis Parameters are applied to every queued session."
        )
        ttk.Label(content, text=blurb, justify="left").grid(row=0, column=0, sticky="w", pady=(0, 12))
        self._vars["show_batch_organizer"] = tk.BooleanVar(value=True)

        current_box = ttk.LabelFrame(content, text="Current Session", padding=12)
        current_box.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        current_box.columnconfigure(1, weight=1)

        ttk.Label(current_box, text="Session Folder").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(current_box, textvariable=self._vars["session_path"]).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(current_box, text="Browse", command=self._browse_session).grid(row=0, column=2, padx=(8, 0), pady=6, sticky="w")

        ttk.Label(current_box, text="Optional Run Name").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(current_box, textvariable=self._vars["run_name"]).grid(row=1, column=1, sticky="ew", pady=6)

        ttk.Label(current_box, text="Resolved Input Root").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=6)
        input_entry = ttk.Entry(current_box, textvariable=self._vars["input_root"])
        input_entry.grid(row=2, column=1, sticky="ew", pady=6)
        input_entry.configure(state="readonly")

        ttk.Label(current_box, text="Resolved Output Root").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=6)
        output_entry = ttk.Entry(current_box, textvariable=self._vars["output_root"])
        output_entry.grid(row=3, column=1, sticky="ew", pady=6)
        output_entry.configure(state="readonly")

        run_options = ttk.Frame(current_box)
        run_options.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            run_options,
            text="Skip run if this session already has Suite2p outputs",
            variable=self._vars["run_skip_existing"],
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Checkbutton(
            run_options,
            text="Archive existing outputs before overwrite",
            variable=self._vars["run_archive_existing"],
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Checkbutton(
            run_options,
            text="Clean temp after session / artifact export",
            variable=self._vars["run_cleanup_temp"],
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=4)

        run_actions = ttk.Frame(current_box)
        run_actions.grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self._make_action_button(run_actions, "Run From Session", self._run_from_session, background=True).grid(row=0, column=0, padx=(0, 8), pady=6)

        batch_toggle = ttk.Frame(content)
        batch_toggle.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(batch_toggle, text="Hide Batch Organizer", command=lambda: self._toggle_section("show_batch_organizer", queue_box, batch_toggle, "Batch Organizer")).pack(anchor="w")
        queue_box = ttk.LabelFrame(content, text="Queued Sessions And Batch Results", padding=12)
        queue_box.grid(row=3, column=0, sticky="nsew")
        queue_box.columnconfigure(0, weight=1)
        queue_box.rowconfigure(0, weight=1)

        queue_pane = ttk.Panedwindow(queue_box, orient=tk.HORIZONTAL)
        queue_pane.grid(row=0, column=0, sticky="nsew")

        queue_list_box = ttk.LabelFrame(queue_pane, text="Queued Sessions", padding=8)
        queue_list_box.columnconfigure(0, weight=1)
        queue_list_box.rowconfigure(0, weight=1)
        self.batch_listbox = tk.Listbox(
            queue_list_box,
            bg=self.colors["entry"],
            fg=self.colors["text"],
            selectbackground=self.colors["accent"],
            selectforeground=self.colors["button_text"],
            relief="flat",
            activestyle="none",
            xscrollcommand=lambda *args: batch_x_scroll.set(*args),
        )
        self.batch_listbox.grid(row=0, column=0, sticky="nsew")
        batch_scroll = ttk.Scrollbar(queue_list_box, orient="vertical", command=self.batch_listbox.yview)
        batch_scroll.grid(row=0, column=1, sticky="ns")
        batch_x_scroll = ttk.Scrollbar(queue_list_box, orient="horizontal", command=self.batch_listbox.xview)
        batch_x_scroll.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.batch_listbox.configure(yscrollcommand=batch_scroll.set, xscrollcommand=batch_x_scroll.set)

        results_box = ttk.LabelFrame(queue_pane, text="Batch Results", padding=8)
        results_box.columnconfigure(0, weight=1)
        results_box.rowconfigure(0, weight=1)
        self.batch_tree = ttk.Treeview(results_box, columns=("animal", "slice", "session", "status"), show="headings", height=14)
        self.batch_tree.heading("animal", text="Animal ID")
        self.batch_tree.heading("slice", text="Slice")
        self.batch_tree.heading("session", text="Session")
        self.batch_tree.heading("status", text="Status")
        self.batch_tree.column("animal", width=120, anchor="w", stretch=False)
        self.batch_tree.column("slice", width=110, anchor="w", stretch=False)
        self.batch_tree.column("session", width=230, anchor="w", stretch=True)
        self.batch_tree.column("status", width=110, anchor="center")
        self.batch_tree.grid(row=0, column=0, sticky="nsew")
        batch_tree_scroll = ttk.Scrollbar(results_box, orient="vertical", command=self.batch_tree.yview)
        batch_tree_scroll.grid(row=0, column=1, sticky="ns")
        batch_tree_x_scroll = ttk.Scrollbar(results_box, orient="horizontal", command=self.batch_tree.xview)
        batch_tree_x_scroll.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.batch_tree.configure(yscrollcommand=batch_tree_scroll.set, xscrollcommand=batch_tree_x_scroll.set)

        queue_pane.add(queue_list_box, weight=1)
        queue_pane.add(results_box, weight=2)

        controls = ttk.Frame(content)
        controls.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        controls.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            controls,
            text="Skip sessions that already have Suite2p outputs",
            variable=self._vars["batch_skip_existing"],
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Checkbutton(
            controls,
            text="Archive existing outputs before overwrite",
            variable=self._vars["batch_archive_existing"],
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Checkbutton(
            controls,
            text="Retain SSD temp .bin on session HDD after each successful session",
            variable=self._vars["batch_cleanup_temp"],
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Checkbutton(
            controls,
            text="Skip sessions marked no_soma when loading from parent",
            variable=self._vars["batch_skip_no_soma"],
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Checkbutton(
            controls,
            text="Skip sessions marked completed when loading from parent",
            variable=self._vars["batch_skip_completed"],
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=6)

        self._make_action_button(controls, "Add Session", self._batch_add_session).grid(row=5, column=0, padx=(0, 8), pady=6)
        self._make_action_button(controls, "Remove Selected", self._batch_remove_selected).grid(row=5, column=1, padx=(0, 8), pady=6)
        self._make_action_button(controls, "Clear Queue", self._batch_clear).grid(row=5, column=2, padx=(0, 8), pady=6)

        ttk.Label(controls, text="Parent Folder").grid(row=6, column=0, sticky="w", pady=6)
        ttk.Entry(controls, textvariable=self._vars["batch_parent_root"]).grid(row=6, column=1, sticky="ew", pady=6)
        ttk.Button(controls, text="Browse", command=self._batch_browse_parent).grid(row=6, column=2, padx=(8, 0), pady=6, sticky="w")
        self._make_action_button(controls, "Load Sessions From Parent", self._batch_load_from_parent).grid(row=7, column=0, padx=(0, 8), pady=6)
        self._make_action_button(controls, "Run Batch Preflight", self._run_batch_preflight, background=True).grid(row=7, column=1, padx=(0, 8), pady=6)
        self._make_action_button(controls, "Run Batch Sequentially", self._run_batch_sequentially, background=True).grid(row=7, column=2, padx=(0, 8), pady=6)
        self.batch_stop_btn = ttk.Button(controls, text="Stop After Current Session", command=self._request_batch_stop)
        self.batch_stop_btn.grid(row=7, column=3, padx=(0, 8), pady=6)
        self.batch_stop_btn.configure(state=tk.DISABLED)

        note = (
            "Batch overwrite behavior:\n"
            "- With skip enabled, existing session outputs are left untouched.\n"
            "- With skip disabled, rerunning a session replaces that session's durable Suite2p outputs under analysis\\outputs.\n"
            "- If archive is enabled, the old suite2p outputs are moved into that session's analysis\\archived_outputs folder first.\n"
            "- If SSD temp retention is enabled, successful sessions have their .bin files moved into analysis\\retained_temp before the SSD temp folder is removed.\n"
            "- Preflight checks raw TIFFs, metadata, output writability, and free space before batch starts."
        )
        ttk.Label(content, text=note, justify="left").grid(row=5, column=0, sticky="w", pady=(12, 0))

    def _build_analysis_parameters_tab(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        outer = ttk.Frame(frame)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, bg=self.colors["bg"], highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        outer.bind("<Configure>", lambda e: canvas.itemconfigure(canvas_window, width=e.width - scrollbar.winfo_width()))

        parameter_blurb = (
            "These are the Suite2p settings we can tune directly.\n"
            "ROI detection and soma-size-related knobs live under Biological Parameters."
        )
        ttk.Label(content, text=parameter_blurb, justify="left").grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))

        self._vars["param_fs"] = tk.StringVar()
        self._vars["param_tau"] = tk.StringVar()
        self._vars["param_nplanes"] = tk.StringVar()
        self._vars["param_nchannels"] = tk.StringVar()
        self._vars["param_functional_chan"] = tk.StringVar()
        self._vars["param_do_registration"] = tk.BooleanVar(value=True)
        self._vars["param_nonrigid"] = tk.BooleanVar(value=False)
        self._vars["param_batch_size"] = tk.StringVar()
        self._vars["param_maxregshift"] = tk.StringVar()
        self._vars["param_maxregshiftNR"] = tk.StringVar()
        self._vars["param_smooth_sigma"] = tk.StringVar()
        self._vars["param_snr_thresh"] = tk.StringVar()
        self._vars["param_1Preg"] = tk.BooleanVar(value=False)
        self._vars["param_pre_smooth"] = tk.StringVar()
        self._vars["param_spatial_taper"] = tk.StringVar()
        self._vars["param_roidetect"] = tk.BooleanVar(value=True)
        self._vars["param_sparse_mode"] = tk.BooleanVar(value=True)
        self._vars["param_anatomical_only"] = tk.StringVar()
        self._vars["param_denoise"] = tk.StringVar()
        self._vars["param_diameter"] = tk.StringVar()
        self._vars["param_threshold_scaling"] = tk.StringVar()
        self._vars["param_spatial_scale"] = tk.StringVar()
        self._vars["param_max_overlap"] = tk.StringVar()
        self._vars["param_soma_crop"] = tk.BooleanVar(value=True)
        self._vars["param_cellprob_threshold"] = tk.StringVar()
        self._vars["parameter_preset_path"] = tk.StringVar(value=str(PRESETS_ROOT / "default_suite2p_params.json"))

        self._vars["show_runtime_params"] = tk.BooleanVar(value=True)
        self._vars["show_motion_params"] = tk.BooleanVar(value=True)
        self._vars["show_detection_params"] = tk.BooleanVar(value=True)
        self._vars["show_bio_params"] = tk.BooleanVar(value=True)
        self._vars["show_preset_params"] = tk.BooleanVar(value=False)

        runtime_toggle = ttk.Frame(content)
        runtime_toggle.grid(row=1, column=0, sticky="ew", pady=(0, 6), padx=(0, 12))
        ttk.Button(runtime_toggle, text="Hide Runtime / Acquisition Parameters", command=lambda: self._toggle_section("show_runtime_params", runtime_box, runtime_toggle, "Runtime / Acquisition Parameters")).pack(anchor="w")
        runtime_box = ttk.LabelFrame(content, text="Runtime / Acquisition Parameters", padding=12)
        runtime_box.grid(row=2, column=0, sticky="nsew", pady=(0, 12), padx=(0, 12))
        runtime_box.columnconfigure(1, weight=1)
        runtime_box.columnconfigure(3, weight=1)
        self._param_entry_2col(runtime_box, 0, 0, "frame_rate", "param_fs")
        self._param_entry_2col(runtime_box, 0, 1, "Decay Tau", "param_tau")
        self._param_entry_2col(runtime_box, 1, 0, "nplanes", "param_nplanes")
        self._param_entry_2col(runtime_box, 1, 1, "nchannels", "param_nchannels")
        self._param_entry_2col(runtime_box, 2, 0, "functional_chan", "param_functional_chan")
        ttk.Checkbutton(runtime_box, text="Do Registration", variable=self._vars["param_do_registration"]).grid(row=3, column=0, sticky="w", pady=6)
        ttk.Checkbutton(runtime_box, text="Nonrigid Registration", variable=self._vars["param_nonrigid"]).grid(row=3, column=2, sticky="w", pady=6)

        motion_toggle = ttk.Frame(content)
        motion_toggle.grid(row=1, column=1, sticky="ew", pady=(0, 6))
        ttk.Button(motion_toggle, text="Hide Motion Correction Parameters", command=lambda: self._toggle_section("show_motion_params", motion_box, motion_toggle, "Motion Correction Parameters")).pack(anchor="w")
        motion_box = ttk.LabelFrame(content, text="Motion Correction Parameters", padding=12)
        motion_box.grid(row=2, column=1, sticky="nsew", pady=(0, 12))
        motion_box.columnconfigure(1, weight=1)
        motion_box.columnconfigure(3, weight=1)
        self._param_entry_2col(motion_box, 0, 0, "batch_size", "param_batch_size")
        self._param_entry_2col(motion_box, 0, 1, "maxregshift", "param_maxregshift")
        self._param_entry_2col(motion_box, 1, 0, "maxregshiftNR", "param_maxregshiftNR")
        self._param_entry_2col(motion_box, 1, 1, "smooth_sigma", "param_smooth_sigma")
        self._param_entry_2col(motion_box, 2, 0, "snr_thresh", "param_snr_thresh")
        self._param_entry_2col(motion_box, 2, 1, "pre_smooth", "param_pre_smooth")
        self._param_entry_2col(motion_box, 3, 0, "spatial_taper", "param_spatial_taper")
        ttk.Checkbutton(motion_box, text="1Preg", variable=self._vars["param_1Preg"]).grid(row=3, column=2, sticky="w", pady=6)

        detection_toggle = ttk.Frame(content)
        detection_toggle.grid(row=3, column=0, sticky="ew", pady=(0, 6), padx=(0, 12))
        ttk.Button(detection_toggle, text="Hide Detection Parameters", command=lambda: self._toggle_section("show_detection_params", detection_box, detection_toggle, "Detection Parameters")).pack(anchor="w")
        detection_box = ttk.LabelFrame(content, text="Detection Parameters", padding=12)
        detection_box.grid(row=4, column=0, sticky="nsew", pady=(0, 12), padx=(0, 12))
        detection_box.columnconfigure(1, weight=1)
        detection_box.columnconfigure(3, weight=1)
        ttk.Checkbutton(detection_box, text="ROI Detect", variable=self._vars["param_roidetect"]).grid(row=0, column=0, sticky="w", pady=6)
        ttk.Checkbutton(detection_box, text="Sparse Mode", variable=self._vars["param_sparse_mode"]).grid(row=0, column=2, sticky="w", pady=6)
        self._param_entry_2col(detection_box, 1, 0, "anatomical_only", "param_anatomical_only")
        self._param_entry_2col(detection_box, 1, 1, "denoise", "param_denoise")

        bio_toggle = ttk.Frame(content)
        bio_toggle.grid(row=3, column=1, sticky="ew", pady=(0, 6))
        ttk.Button(bio_toggle, text="Hide Biological Parameters", command=lambda: self._toggle_section("show_bio_params", bio_box, bio_toggle, "Biological Parameters")).pack(anchor="w")
        bio_box = ttk.LabelFrame(content, text="Biological Parameters", padding=12)
        bio_box.grid(row=4, column=1, sticky="nsew")
        bio_box.columnconfigure(1, weight=1)
        bio_box.columnconfigure(3, weight=1)
        self._param_entry_2col(bio_box, 0, 0, "ROI Diameter / Soma Size", "param_diameter")
        self._param_entry_2col(bio_box, 0, 1, "threshold_scaling", "param_threshold_scaling")
        self._param_entry_2col(bio_box, 1, 0, "spatial_scale", "param_spatial_scale")
        self._param_entry_2col(bio_box, 1, 1, "max_overlap", "param_max_overlap")
        self._param_entry_2col(bio_box, 2, 0, "cellprob_threshold", "param_cellprob_threshold")
        ttk.Checkbutton(bio_box, text="soma_crop", variable=self._vars["param_soma_crop"]).grid(row=2, column=2, sticky="w", pady=6)

        actions = ttk.Frame(content)
        actions.grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self._make_action_button(actions, "Load Parameters From Run", self._load_parameters_from_run).grid(row=0, column=0, padx=(0, 8))
        self._make_action_button(actions, "Save Parameters To Run", self._save_parameters_to_run).grid(row=0, column=1)

        preset_toggle = ttk.Frame(content)
        preset_toggle.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(12, 6))
        ttk.Button(preset_toggle, text="Show Parameter Presets", command=lambda: self._toggle_section("show_preset_params", preset_box, preset_toggle, "Parameter Presets")).pack(anchor="w")
        preset_box = ttk.LabelFrame(content, text="Parameter Presets", padding=12)
        preset_box.grid(row=7, column=0, columnspan=2, sticky="ew")
        preset_box.columnconfigure(1, weight=1)
        ttk.Label(preset_box, text="Parameter Preset").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(preset_box, textvariable=self._vars["parameter_preset_path"]).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(preset_box, text="Browse", command=self._browse_parameter_preset).grid(row=0, column=2, padx=(8, 0), pady=6, sticky="w")
        preset_actions = ttk.Frame(preset_box)
        preset_actions.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self._make_action_button(preset_actions, "Load Parameters", self._load_parameter_preset).grid(row=0, column=0, padx=(0, 8))
        self._make_action_button(preset_actions, "Save Parameters", self._save_parameter_preset).grid(row=0, column=1)
        preset_box.grid_remove()
        self._extend_analysis_parameters_tab(content, start_row=8)

    def _extend_analysis_parameters_tab(self, content: ttk.Frame, *, start_row: int) -> None:
        return

    def _param_entry(self, parent: ttk.Frame, row: int, label: str, var_name: str) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(parent, textvariable=self._vars[var_name]).grid(row=row, column=1, sticky="ew", pady=6)

    def _param_entry_2col(self, parent: ttk.Frame, row: int, slot: int, label: str, var_name: str) -> None:
        base_col = slot * 2
        padx = (0, 8) if slot == 0 else (16, 8)
        ttk.Label(parent, text=label).grid(row=row, column=base_col, sticky="w", padx=padx, pady=6)
        ttk.Entry(parent, textvariable=self._vars[var_name]).grid(row=row, column=base_col + 1, sticky="ew", pady=6)

    def _build_definitions_tab(self, frame: ttk.Frame) -> None:
        frame = self._make_scrollable_tab(frame)
        definitions = (
            "Runtime / Acquisition Parameters\n"
            "- frame_rate: acquired frame rate used by Suite2p for timing and deconvolution.\n"
            "- Decay Tau: expected calcium decay constant in seconds.\n"
            "- nplanes: number of imaging planes. For your workflow this is usually 1.\n"
            "- nchannels: total recorded channels in the TIFF data.\n"
            "- functional_chan: which channel contains the calcium signal.\n"
            "- Do Registration: enables motion correction / registration.\n"
            "- Nonrigid Registration: allows local blockwise corrections beyond rigid shifts.\n\n"
            "Motion Correction Parameters\n"
            "- batch_size: frames processed per registration batch.\n"
            "- maxregshift: maximum rigid shift as a fraction of frame size.\n"
            "- maxregshiftNR: maximum nonrigid shift in pixels.\n"
            "- smooth_sigma: spatial smoothing before registration.\n"
            "- snr_thresh: signal threshold used by registration heuristics.\n"
            "- 1Preg: one-photon style registration behavior.\n"
            "- pre_smooth: extra smoothing before nonrigid estimation.\n"
            "- spatial_taper: edge tapering to reduce boundary artifacts.\n\n"
            "Detection Parameters\n"
            "- ROI Detect: enables Suite2p ROI detection.\n"
            "- Sparse Mode: lighter ROI representation and faster extraction path.\n"
            "- anatomical_only: emphasize anatomy over activity when detecting ROIs.\n"
            "- denoise: enable Suite2p denoising options when supported.\n\n"
            "Biological Parameters\n"
            "- ROI Diameter / Soma Size: primary size prior for somatic ROI footprints.\n"
            "- threshold_scaling: raises or lowers detection strictness.\n"
            "- spatial_scale: detection scale prior; helps bias toward smaller or larger structures.\n"
            "- max_overlap: how much neighboring ROIs are allowed to overlap.\n"
            "- cellprob_threshold: threshold used when deciding cell-like ROIs.\n"
            "- soma_crop: keeps ROI extraction focused more tightly around soma-like regions.\n"
        )
        text = tk.Text(
            frame,
            wrap="word",
            bg=self.colors["entry"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief="flat",
            height=24,
        )
        text.grid(row=0, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        text.insert("1.0", definitions)
        text.configure(state="disabled")

    def _build_preview_tab(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        outer = ttk.Frame(frame)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, bg=self.colors["bg"], highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        outer.bind("<Configure>", lambda e: canvas.itemconfigure(canvas_window, width=e.width - scrollbar.winfo_width()))

        blurb = "Each preview can have its own rendering settings, so you can tune them independently."
        ttk.Label(content, text=blurb, justify="left").grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        for prefix in ("motion", "overlay", "three_panel", "reconstruction"):
            self._vars[f"{prefix}_start_frame"] = tk.StringVar(value="0")
            self._vars[f"{prefix}_num_frames"] = tk.StringVar(value="300")
            self._vars[f"{prefix}_fps"] = tk.StringVar(value="20")
            self._vars[f"{prefix}_gain"] = tk.StringVar(value="1.0")
            self._vars[f"{prefix}_q_min"] = tk.StringVar(value="5.0")
            self._vars[f"{prefix}_q_max"] = tk.StringVar(value="99.5")

        preview_row = self._extend_preview_tab_top(content, start_row=1)

        motion_box = self._build_preview_controls_box(content, preview_row, 0, 1, "Motion Preview", "motion", self._rerender_motion_preview, self._open_motion_preview)
        overlay_box = self._build_preview_controls_box(content, preview_row, 1, 1, "ROI Overlay Preview", "overlay", self._rerender_overlay_preview, self._open_overlay_preview)
        three_panel_box = self._build_preview_controls_box(content, preview_row + 1, 0, 1, "3-Panel Preview", "three_panel", self._rerender_three_panel_preview, self._open_three_panel_preview)
        reconstruction_box = self._build_preview_controls_box(content, preview_row + 1, 1, 1, "Reconstruction Preview", "reconstruction", self._rerender_reconstruction_preview, self._open_reconstruction_preview)
        for box in (motion_box, overlay_box, three_panel_box, reconstruction_box):
            box.columnconfigure(1, weight=1)
            box.columnconfigure(3, weight=1)
            box.columnconfigure(5, weight=1)

        actions = ttk.Frame(content)
        actions.grid(row=preview_row + 2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._make_action_button(actions, "Re-render All Previews", self._export_preview_artifacts, background=True).grid(row=0, column=0, padx=(0, 8), pady=6)
        self._extend_preview_tab(content, start_row=preview_row + 3)

    def _build_preview_controls_box(self, parent: ttk.Frame, row: int, column: int, columnspan: int, title: str, prefix: str, rerender_cmd, open_cmd) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text=title, padding=12)
        box.grid(row=row, column=column, columnspan=columnspan, sticky="ew", pady=(0, 12), padx=(0, 12) if column == 0 and columnspan == 1 else (0, 0))
        ttk.Label(box, text="Start Frame").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(box, textvariable=self._vars[f"{prefix}_start_frame"]).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Label(box, text="Num Frames").grid(row=0, column=2, sticky="w", padx=(16, 8), pady=6)
        ttk.Entry(box, textvariable=self._vars[f"{prefix}_num_frames"]).grid(row=0, column=3, sticky="ew", pady=6)
        ttk.Label(box, text="FPS").grid(row=0, column=4, sticky="w", padx=(16, 8), pady=6)
        ttk.Entry(box, textvariable=self._vars[f"{prefix}_fps"]).grid(row=0, column=5, sticky="ew", pady=6)
        ttk.Label(box, text="Gain").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(box, textvariable=self._vars[f"{prefix}_gain"]).grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Label(box, text="q_min").grid(row=1, column=2, sticky="w", padx=(16, 8), pady=6)
        ttk.Entry(box, textvariable=self._vars[f"{prefix}_q_min"]).grid(row=1, column=3, sticky="ew", pady=6)
        ttk.Label(box, text="q_max").grid(row=1, column=4, sticky="w", padx=(16, 8), pady=6)
        ttk.Entry(box, textvariable=self._vars[f"{prefix}_q_max"]).grid(row=1, column=5, sticky="ew", pady=6)
        actions = ttk.Frame(box)
        actions.grid(row=2, column=0, columnspan=6, sticky="w", pady=(8, 0))
        self._make_action_button(actions, f"Re-render {title}", rerender_cmd, background=True).grid(row=0, column=0, padx=(0, 8), pady=6)
        self._make_action_button(actions, f"Open {title}", open_cmd).grid(row=0, column=1, padx=(0, 8), pady=6)
        return box

    def _extend_preview_tab(self, content: ttk.Frame, *, start_row: int) -> None:
        return

    def _extend_preview_tab_top(self, content: ttk.Frame, *, start_row: int) -> int:
        return start_row

    def _build_curation_tab(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self._vars["curation_bg_mode"] = tk.StringVar(value="mean")
        self._vars["curation_filter_mode"] = tk.StringVar(value="all")
        self._vars["curation_show_overlay"] = tk.BooleanVar(value=True)
        self._vars["curation_review_focus"] = tk.StringVar(value="all")
        self._vars["curation_zoom_to_selected"] = tk.BooleanVar(value=False)
        self._vars["curation_image_saturation"] = tk.DoubleVar(value=1.0)
        self._vars["curation_saturation"] = tk.DoubleVar(value=0.35)
        self._vars["curation_sort_mode"] = tk.StringVar(value="uncertain_first")
        self._vars["curation_auto_advance"] = tk.BooleanVar(value=True)
        self._vars["curation_parent_root"] = tk.StringVar()
        self._vars["curation_session_status"] = tk.StringVar(value="not_started")
        self._vars["curation_session_notes"] = tk.StringVar()
        self._vars["curation_jump_index"] = tk.StringVar()
        self._vars["curation_manual_mode"] = tk.BooleanVar(value=False)
        self._vars["curation_manual_diameter"] = tk.DoubleVar(value=14.0)
        self._vars["curation_hide_excluded_sessions"] = tk.BooleanVar(value=False)
        self._vars["curation_hide_completed_sessions"] = tk.BooleanVar(value=False)
        self._vars["show_curation_queue"] = tk.BooleanVar(value=False)
        self._vars["show_curation_status"] = tk.BooleanVar(value=False)
        self._vars["show_curation_snapshots"] = tk.BooleanVar(value=False)
        self._vars["show_curation_assistance"] = tk.BooleanVar(value=False)
        self._vars["show_curation_session_viewer"] = tk.BooleanVar(value=False)
        self._vars["show_curation_roi_list"] = tk.BooleanVar(value=False)
        self._vars["show_curation_hotkeys"] = tk.BooleanVar(value=False)
        self.curation_status_var = tk.StringVar(value="Load a session in Post-Run, then use this tab to review ROIs.")
        self.curation_roi_info_var = tk.StringVar(value="No ROI selected.")
        self.curation_counts_var = tk.StringVar(value="Visible: 0 | Accepted: 0 | Rejected: 0")
        self.curation_source_var = tk.StringVar(value="Source: active plane0")
        self.curation_queue_var = tk.StringVar(value="Queue: no sessions loaded")
        self.curation_selection_var = tk.StringVar(value="Merge set: 0 selected")
        self.curation_assistance_var = tk.StringVar(value="Assistance: load curation data to see suggestions.")
        self._curation_payload: dict[str, object] | None = None
        self._curation_selected_index: int | None = None
        self._curation_merge_selection: set[int] = set()
        self._curation_session_queue: list[str] = []
        self._curation_queue_index: int | None = None
        self._curation_saved_iscell: np.ndarray | None = None
        self._curation_visible_indices_cache: list[int] | None = None
        self._curation_review_order_cache: list[int] | None = None
        self._curation_photo = None
        self._curation_selection_photo = None
        self._curation_render_cache_key: tuple[object, ...] | None = None
        self._curation_render_cache_image = None
        self._curation_render_cache_hit_map: np.ndarray | None = None
        self._curation_render_cache_shape: tuple[int, int] | None = None
        self._curation_render_cache_origin: tuple[int, int] = (0, 0)
        self._curation_render_cache_display_size: tuple[int, int] = (1, 1)
        self._curation_render_cache_view_offset: tuple[int, int] = (0, 0)
        self._curation_render_cache_view_scale: float = 1.0
        self._curation_hit_map: np.ndarray | None = None
        self._curation_image_shape: tuple[int, int] | None = None
        self._curation_display_size: tuple[int, int] = (1, 1)
        self._curation_crop_origin: tuple[int, int] = (0, 0)
        self._curation_view_offset: tuple[int, int] = (0, 0)
        self._curation_view_scale: float = 1.0
        self._curation_manual_zoom_factor: float = 1.0
        self._curation_manual_zoom_center: tuple[float, float] | None = None
        self._curation_manual_hover_center: tuple[float, float] | None = None
        self._curation_manual_drag_center: tuple[float, float] | None = None
        self._curation_manual_preview_diameter: float | None = None

        outer = ttk.Frame(frame)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, bg=self.colors["bg"], highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        layout = ttk.Frame(canvas)
        layout.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=layout, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        outer.bind("<Configure>", lambda e: canvas.itemconfigure(canvas_window, width=e.width - scrollbar.winfo_width()))

        layout.columnconfigure(0, weight=0)
        layout.columnconfigure(1, weight=3)
        layout.columnconfigure(2, weight=1)
        layout.rowconfigure(2, weight=1)

        ttk.Label(
            layout,
            text="ROI Curation: review ROIs on the projection, inspect traces, curate with Keep/Reject, then save and finalize back into the pipeline.",
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        ttk.Label(layout, textvariable=self.curation_source_var, justify="left").grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        left = ttk.LabelFrame(layout, text="Display + Actions", padding=12)
        left.grid(row=2, column=0, sticky="nsw", padx=(0, 12))
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text="Background").grid(row=0, column=0, sticky="w", pady=(0, 6))
        bg_box = ttk.Frame(left)
        bg_box.grid(row=1, column=0, sticky="w", pady=(0, 10))
        for idx, (label, value) in enumerate((("Mean", "mean"), ("Max", "max"), ("Correlation", "corr"))):
            ttk.Radiobutton(bg_box, text=label, variable=self._vars["curation_bg_mode"], value=value, command=self._render_curation_view).grid(row=idx, column=0, sticky="w", pady=2)

        ttk.Label(left, text="Overlay").grid(row=2, column=0, sticky="w", pady=(0, 6))
        filter_box = ttk.Frame(left)
        filter_box.grid(row=3, column=0, sticky="w", pady=(0, 10))
        for idx, (label, value) in enumerate((("All (D)", "all"), ("Accepted (A)", "accepted"), ("Rejected (J)", "rejected"))):
            ttk.Radiobutton(filter_box, text=label, variable=self._vars["curation_filter_mode"], value=value, command=self._render_curation_view).grid(row=idx, column=0, sticky="w", pady=2)
        ttk.Checkbutton(left, text="Show ROI Overlay", variable=self._vars["curation_show_overlay"], command=self._render_curation_view).grid(row=4, column=0, sticky="w", pady=(0, 10))

        ttk.Label(left, text="Image Saturation").grid(row=5, column=0, sticky="w", pady=(0, 6))
        ttk.Scale(left, from_=0.5, to=2.0, orient="horizontal", variable=self._vars["curation_image_saturation"], command=lambda _v: self._render_curation_view(), style="Curation.Horizontal.TScale").grid(row=6, column=0, sticky="ew", pady=(0, 10))

        ttk.Label(left, text="Overlay Opacity").grid(row=7, column=0, sticky="w", pady=(0, 6))
        ttk.Scale(left, from_=0.05, to=0.9, orient="horizontal", variable=self._vars["curation_saturation"], command=lambda _v: self._render_curation_view(), style="Curation.Horizontal.TScale").grid(row=8, column=0, sticky="ew", pady=(0, 10))

        ttk.Checkbutton(left, text="Zoom to selected ROI", variable=self._vars["curation_zoom_to_selected"], command=self._render_curation_view).grid(row=9, column=0, sticky="w", pady=(0, 6))
        self._make_action_button(left, "Reset View", self._reset_curation_zoom).grid(row=10, column=0, sticky="ew", pady=(0, 10))
        ttk.Checkbutton(left, text="Auto-advance after Keep/Reject", variable=self._vars["curation_auto_advance"]).grid(row=11, column=0, sticky="w", pady=(0, 10))
        ttk.Checkbutton(left, text="Manual Add Mode (N)", variable=self._vars["curation_manual_mode"]).grid(row=12, column=0, sticky="w", pady=(0, 6))
        ttk.Label(left, text="Manual ROI Diameter").grid(row=13, column=0, sticky="w", pady=(0, 6))
        ttk.Scale(left, from_=6.0, to=40.0, orient="horizontal", variable=self._vars["curation_manual_diameter"], style="Curation.Horizontal.TScale").grid(row=14, column=0, sticky="ew", pady=(0, 10))

        canvas_frame = ttk.LabelFrame(layout, text="Projection", padding=8)
        canvas_frame.grid(row=2, column=1, sticky="nsew")
        canvas_frame.columnconfigure(0, weight=1)
        canvas_frame.rowconfigure(0, weight=1)
        self.curation_canvas = tk.Canvas(canvas_frame, bg="#000000", highlightthickness=0, bd=0)
        self.curation_canvas.grid(row=0, column=0, sticky="nsew")
        self.curation_canvas.bind("<Configure>", lambda _e: self._render_curation_view())
        self.curation_canvas.bind("<Button-1>", self._on_curation_canvas_click)
        self.curation_canvas.bind("<Control-Button-1>", lambda event: self._on_curation_canvas_click(event, ctrl_override=True))
        self.curation_canvas.bind("<B1-Motion>", self._on_curation_canvas_drag)
        self.curation_canvas.bind("<ButtonRelease-1>", self._on_curation_canvas_release)
        self.curation_canvas.bind("<Motion>", self._on_curation_canvas_motion)
        self.curation_canvas.bind("<Control-MouseWheel>", self._on_curation_zoom_wheel, add="+")
        self.curation_canvas.bind("<Control-Button-4>", self._on_curation_zoom_wheel, add="+")
        self.curation_canvas.bind("<Control-Button-5>", self._on_curation_zoom_wheel, add="+")

        right = ttk.LabelFrame(layout, text="Selected ROI", padding=12)
        right.grid(row=2, column=2, sticky="nsew", padx=(12, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(5, weight=1)
        ttk.Label(right, textvariable=self.curation_counts_var, justify="left").grid(row=0, column=0, sticky="nw")
        ttk.Label(right, textvariable=self.curation_roi_info_var, justify="left").grid(row=1, column=0, sticky="nw", pady=(10, 0))

        action_box = ttk.LabelFrame(right, text="Actions", padding=8)
        action_box.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        action_box.columnconfigure(0, weight=1)
        action_box.columnconfigure(1, weight=1)
        self._make_action_button(action_box, "Load Curation Data", self._load_curation_data).grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=4)
        self._make_action_button(action_box, "Finalize Into Pipeline", self._finalize_roi_edits, background=True).grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=4)
        self._make_action_button(action_box, "Open Selected ROI Trace", self._open_selected_roi_trace_window).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=4)
        ttk.Label(action_box, text="").grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=4)

        panel_box = ttk.LabelFrame(right, text="Panels", padding=8)
        panel_box.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        panel_box.columnconfigure(0, weight=1)
        panel_box.columnconfigure(1, weight=1)
        hotkeys_btn = ttk.Button(panel_box, text="Show Hotkeys")
        hotkeys_btn.configure(command=lambda: self._toggle_section("show_curation_hotkeys", hotkey_box, hotkeys_btn, "Hotkeys"))
        hotkeys_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=3)
        roi_list_btn = ttk.Button(panel_box, text="Show ROI List / Merge Set")
        roi_list_btn.configure(command=lambda: self._toggle_section("show_curation_roi_list", list_box, roi_list_btn, "ROI List / Merge Set"))
        roi_list_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=3)
        snapshots_btn = ttk.Button(panel_box, text="Show Snapshots")
        snapshots_btn.configure(command=lambda: self._toggle_section("show_curation_snapshots", snapshot_box, snapshots_btn, "Snapshots"))
        snapshots_btn.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=3)
        assistance_btn = ttk.Button(panel_box, text="Show Assistance")
        assistance_btn.configure(command=lambda: self._toggle_section("show_curation_assistance", assist_box, assistance_btn, "Assistance"))
        assistance_btn.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=3)
        session_status_btn = ttk.Button(panel_box, text="Show Session Status")
        session_status_btn.configure(command=lambda: self._toggle_section("show_curation_status", status_box, session_status_btn, "Session Status"))
        session_status_btn.grid(row=2, column=0, columnspan=2, sticky="ew", pady=3)
        session_viewer_btn = ttk.Button(panel_box, text="Show Session Viewer")
        session_viewer_btn.configure(command=lambda: self._toggle_section("show_curation_session_viewer", viewer_box, session_viewer_btn, "Session Viewer"))
        session_viewer_btn.grid(row=3, column=0, columnspan=2, sticky="ew", pady=3)

        hotkey_box = ttk.LabelFrame(right, text="Hotkeys", padding=8)
        hotkey_box.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(
            hotkey_box,
            justify="left",
            text=(
                "Navigation\n"
                "Left / Right: previous / next ROI\n"
                "Shift+Left / Shift+Right: previous / next session\n\n"
                "Curation\n"
                "K: keep ROI\n"
                "R: reject ROI\n"
                "S: save ROI labels\n"
                "M: merge selected ROIs\n"
                "N: manual add mode on/off\n"
                "Delete: delete selected manual ROI(s)\n\n"
                "Overlay\n"
                "A: accepted\n"
                "J: rejected\n"
                "D: all overlays"
            ),
        ).grid(row=0, column=0, sticky="w")
        hotkey_box.grid_remove()

        list_box = ttk.LabelFrame(right, text="ROI List / Merge Set", padding=8)
        list_box.grid(row=5, column=0, sticky="nsew", pady=(8, 0))
        list_box.columnconfigure(0, weight=1)
        list_box.rowconfigure(0, weight=1)
        self.curation_roi_listbox = tk.Listbox(list_box, height=14, exportselection=False, selectmode="extended", bg=self.colors["entry"], fg=self.colors["text"], selectbackground=self.colors["accent"], selectforeground=self.colors["button_text"])
        self.curation_roi_listbox.grid(row=0, column=0, sticky="nsew")
        roi_list_scroll = ttk.Scrollbar(list_box, orient="vertical", command=self.curation_roi_listbox.yview)
        roi_list_scroll.grid(row=0, column=1, sticky="ns")
        self.curation_roi_listbox.configure(yscrollcommand=roi_list_scroll.set)
        self.curation_roi_listbox.bind("<<ListboxSelect>>", self._on_curation_list_select)
        ttk.Label(list_box, textvariable=self.curation_selection_var, justify="left").grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        list_box.grid_remove()

        snapshot_box = ttk.LabelFrame(right, text="Snapshots", padding=8)
        snapshot_box.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        snapshot_box.columnconfigure(0, weight=1)
        self.curation_snapshot_listbox = tk.Listbox(snapshot_box, height=6, exportselection=False, bg=self.colors["entry"], fg=self.colors["text"], selectbackground=self.colors["accent"], selectforeground=self.colors["button_text"])
        self.curation_snapshot_listbox.grid(row=0, column=0, sticky="ew")
        snap_scroll = ttk.Scrollbar(snapshot_box, orient="vertical", command=self.curation_snapshot_listbox.yview)
        snap_scroll.grid(row=0, column=1, sticky="ns")
        self.curation_snapshot_listbox.configure(yscrollcommand=snap_scroll.set)
        self.curation_snapshot_listbox.bind("<<ListboxSelect>>", lambda _e: self._sync_selected_snapshot_var(from_curation=True))
        snapshot_box.grid_remove()

        assist_box = ttk.LabelFrame(right, text="Assistance", padding=8)
        assist_box.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        assist_box.columnconfigure(0, weight=1)
        ttk.Label(assist_box, textvariable=self.curation_assistance_var, justify="left", wraplength=280).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(assist_box, text="Merge Candidates").grid(row=1, column=0, sticky="w")
        self.curation_merge_suggestion_listbox = tk.Listbox(assist_box, height=4, exportselection=False, bg=self.colors["entry"], fg=self.colors["text"], selectbackground=self.colors["accent"], selectforeground=self.colors["button_text"])
        self.curation_merge_suggestion_listbox.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(assist_box, text="Missing Soma Candidates").grid(row=3, column=0, sticky="w")
        self.curation_soma_candidate_listbox = tk.Listbox(assist_box, height=4, exportselection=False, bg=self.colors["entry"], fg=self.colors["text"], selectbackground=self.colors["accent"], selectforeground=self.colors["button_text"])
        self.curation_soma_candidate_listbox.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        assist_actions = ttk.Frame(assist_box)
        assist_actions.grid(row=5, column=0, sticky="w")
        self._make_action_button(assist_actions, "Refresh Assist", self._refresh_curation_assistance).grid(row=0, column=0, padx=(0, 8), pady=4)
        self._make_action_button(assist_actions, "Select Merge Pair", self._curation_select_merge_pair).grid(row=0, column=1, padx=(0, 8), pady=4)
        self._make_action_button(assist_actions, "Jump To Soma", self._curation_jump_soma_candidate).grid(row=1, column=0, padx=(0, 8), pady=4)
        self._make_action_button(assist_actions, "Add Candidate ROI", self._curation_add_soma_candidate, background=True).grid(row=1, column=1, padx=(0, 8), pady=4)
        self._make_action_button(assist_actions, "Apply Suggestion Preview", self._curation_apply_suggestion_preview).grid(row=2, column=0, padx=(0, 8), pady=4)
        self._make_action_button(assist_actions, "Reset Working Labels", self._curation_reset_working_labels).grid(row=2, column=1, padx=(0, 8), pady=4)
        assist_box.grid_remove()

        viewer_box = ttk.LabelFrame(right, text="Session Viewer", padding=8)
        viewer_box.grid(row=8, column=0, sticky="ew", pady=(8, 0))
        viewer_box.columnconfigure(0, weight=1)
        ttk.Label(
            viewer_box,
            text=(
                "Use the overlay preview as a temporal reference while curating.\n"
                "This helps spot missed cells before manual ROI add."
            ),
            justify="left",
            wraplength=280,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        viewer_actions = ttk.Frame(viewer_box)
        viewer_actions.grid(row=1, column=0, sticky="w")
        self._make_action_button(viewer_actions, "Open Overlay Preview", self._open_overlay_preview).grid(row=0, column=0, padx=(0, 8), pady=4)
        viewer_box.grid_remove()

        status_box = ttk.LabelFrame(right, text="Session Status", padding=8)
        status_box.grid(row=9, column=0, sticky="ew", pady=(8, 0))
        status_box.columnconfigure(0, weight=1)
        status_box.columnconfigure(1, weight=1)
        ttk.Combobox(
            status_box,
            textvariable=self._vars["curation_session_status"],
            values=("not_started", "in_progress", "completed", "failed", "no_soma"),
            state="readonly",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Entry(status_box, textvariable=self._vars["curation_session_notes"]).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._make_action_button(status_box, "Save Session Status", self._curation_save_session_status).grid(row=2, column=0, padx=(0, 8), pady=2, sticky="w")
        self._make_action_button(status_box, "Mark Completed", self._curation_mark_session_completed, background=True).grid(row=2, column=1, pady=2, sticky="w")
        self._make_action_button(status_box, "Mark No Soma", self._curation_mark_session_no_soma).grid(row=3, column=0, padx=(0, 8), pady=2, sticky="w")
        self._make_action_button(status_box, "Mark Failed", self._curation_mark_session_failed).grid(row=3, column=1, pady=2, sticky="w")
        status_box.grid_remove()

        ttk.Label(right, textvariable=self.curation_status_var, justify="left", wraplength=280).grid(row=10, column=0, sticky="nw", pady=(12, 0))

        queue_toggle = ttk.Frame(layout)
        queue_toggle.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        ttk.Button(queue_toggle, text="Show Session Queue", command=lambda: self._toggle_section("show_curation_queue", queue_box, queue_toggle, "Session Queue")).pack(anchor="w")
        queue_box = ttk.LabelFrame(layout, text="Session Queue", padding=12)
        queue_box.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        queue_box.columnconfigure(0, weight=1)
        ttk.Entry(queue_box, textvariable=self._vars["curation_parent_root"]).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        queue_browse = ttk.Frame(queue_box)
        queue_browse.grid(row=1, column=0, sticky="w", pady=(0, 10))
        ttk.Button(queue_browse, text="Browse Parent", command=self._curation_browse_parent).grid(row=0, column=0, padx=(0, 8))
        self._make_action_button(queue_browse, "Load Queue", self._curation_load_queue).grid(row=0, column=1, padx=(0, 8))
        self._make_action_button(queue_browse, "Load Selected Session", self._curation_load_selected_session, background=True).grid(row=0, column=2)
        self._make_action_button(queue_browse, "Previous Session", self._curation_queue_prev_session).grid(row=1, column=0, padx=(0, 8), pady=(8, 0))
        self._make_action_button(queue_browse, "Next Session", self._curation_queue_next_session, background=True).grid(row=1, column=1, padx=(0, 8), pady=(8, 0))
        ttk.Checkbutton(
            queue_browse,
            text="Hide Excluded Sessions",
            variable=self._vars["curation_hide_excluded_sessions"],
            command=self._curation_load_queue,
        ).grid(row=1, column=2, padx=(12, 0), pady=(8, 0), sticky="w")
        ttk.Checkbutton(
            queue_browse,
            text="Hide Completed Sessions",
            variable=self._vars["curation_hide_completed_sessions"],
            command=self._curation_load_queue,
        ).grid(row=2, column=2, padx=(12, 0), pady=(6, 0), sticky="w")
        ttk.Label(queue_box, textvariable=self.curation_queue_var, justify="left").grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.curation_queue_listbox = tk.Listbox(queue_box, height=6, exportselection=False, bg=self.colors["entry"], fg=self.colors["text"], selectbackground=self.colors["accent"], selectforeground=self.colors["button_text"])
        self.curation_queue_listbox.grid(row=3, column=0, sticky="ew")
        self.curation_queue_listbox.bind("<<ListboxSelect>>", self._on_curation_queue_select)
        queue_box.grid_remove()

    def _load_curation_data(self) -> None:
        self._ensure_loaded_run()
        self._curation_payload = self.controller.load_curation_payload()
        self._curation_saved_iscell = np.array(np.asarray(self._curation_payload["iscell"]), copy=True)
        self._curation_selected_index = None
        self._curation_merge_selection = set()
        self._curation_manual_zoom_factor = 1.0
        self._curation_manual_zoom_center = None
        self._invalidate_curation_index_cache()
        self._invalidate_curation_render_cache()
        source = self.state_obj.snapshot_dir if self.state_obj.snapshot_dir is not None else self._curation_payload["plane_dir"]
        source_label = "snapshot" if self.state_obj.snapshot_dir is not None else "active plane0"
        self.curation_source_var.set(f"Source: {source_label} | {source}")
        self.curation_status_var.set(f"Loaded ROI curation data from: {self._curation_payload['plane_dir']}")
        self._update_curation_roi_info()
        self._refresh_curation_roi_list()
        self._refresh_curation_assistance()
        self._refresh_snapshot_list(for_curation=True)
        self._render_curation_view()
        self._load_curation_session_status()

    def _curation_browse_parent(self) -> None:
        path = filedialog.askdirectory(title="Select parent folder to search for Session_### folders")
        if path:
            self._vars["curation_parent_root"].set(path)

    def _session_has_curation_outputs(self, session_path: Path) -> bool:
        plane_dir = self.controller.default_plane_dir(session_path)
        required = ("stat.npy", "iscell.npy", "ops.npy")
        return all((plane_dir / name).exists() for name in required)

    def _refresh_curation_queue_list(self) -> None:
        if not hasattr(self, "curation_queue_listbox"):
            return
        self.curation_queue_listbox.delete(0, tk.END)
        total = len(self._curation_session_queue)
        if total == 0:
            self.curation_queue_var.set("Queue: no sessions loaded")
            self._curation_queue_index = None
            return
        current_session = str(self.state_obj.session_path) if self.state_obj.session_path is not None else ""
        selected_index = self._curation_queue_index
        for idx, session in enumerate(self._curation_session_queue):
            animal, slice_name, session_name = self._session_parts(session)
            status_payload = self.controller.load_session_curation_status(session)
            status = str(status_payload.get("status", "not_started"))
            prefix = "-> " if session == current_session else "   "
            label = f"{prefix}{idx + 1:02d}/{total} | {status} | {animal} | {slice_name} | {session_name}"
            self.curation_queue_listbox.insert(tk.END, label)
            if selected_index is None and session == current_session:
                selected_index = idx
        if selected_index is None:
            selected_index = 0
        self._curation_queue_index = selected_index
        self.curation_queue_listbox.selection_set(selected_index)
        self.curation_queue_listbox.see(selected_index)
        self.curation_queue_var.set(f"Queue: session {selected_index + 1} / {total}")

    def _curation_load_queue(self) -> None:
        parent_text = self._vars["curation_parent_root"].get().strip()
        if not parent_text:
            raise ValueError("Choose a parent folder before loading a curation queue.")
        parent = Path(parent_text).expanduser().resolve()
        if not parent.exists():
            raise ValueError(f"Parent folder does not exist: {parent}")
        sessions: list[str] = []
        hide_excluded = bool(self._vars["curation_hide_excluded_sessions"].get())
        hide_completed = bool(self._vars["curation_hide_completed_sessions"].get())
        for candidate in sorted(parent.rglob("Session_*")):
            if not (candidate.is_dir() and candidate.name.lower().startswith("session_") and self._session_has_curation_outputs(candidate)):
                continue
            if hide_excluded or hide_completed:
                status_payload = self.controller.load_session_curation_status(candidate)
                status = str(status_payload.get("status", "not_started") or "not_started").strip().lower()
                if status == "no_soma":
                    if hide_excluded:
                        continue
                if status == "completed" and hide_completed:
                    continue
            sessions.append(str(candidate))
        if not sessions:
            if hide_excluded or hide_completed:
                raise ValueError(
                    f"No curation-ready Suite2p sessions were found under: {parent} after applying the active queue filters."
                )
            raise ValueError(f"No Suite2p sessions with plane0 outputs were found under: {parent}")
        self._curation_session_queue = sessions
        self._curation_queue_index = 0
        self._refresh_curation_queue_list()
        active_filters: list[str] = []
        if hide_excluded:
            active_filters.append("excluded hidden")
        if hide_completed:
            active_filters.append("completed hidden")
        suffix = f" ({', '.join(active_filters)})" if active_filters else ""
        self.curation_status_var.set(f"Loaded curation queue with {len(sessions)} sessions from {parent}{suffix}.")

    def _on_curation_queue_select(self, _event=None) -> None:
        if not hasattr(self, "curation_queue_listbox"):
            return
        selection = self.curation_queue_listbox.curselection()
        if not selection:
            return
        self._curation_queue_index = selection[0]
        total = len(self._curation_session_queue)
        if total:
            self.curation_queue_var.set(f"Queue: session {self._curation_queue_index + 1} / {total}")

    def _curation_load_selected_session(self) -> None:
        if not self._curation_session_queue:
            raise ValueError("Load a curation queue first.")
        if self._curation_queue_index is None:
            self._curation_queue_index = 0
        session_path = self._curation_session_queue[self._curation_queue_index]
        self.controller.load_latest_from_session(session_path)
        self._refresh_view()
        self._load_curation_data()
        self._refresh_curation_queue_list()

    def _curation_queue_prev_session(self) -> None:
        if not self._curation_session_queue:
            raise ValueError("Load a curation queue first.")
        if self._curation_queue_index is None:
            self._curation_queue_index = 0
        else:
            self._curation_queue_index = (self._curation_queue_index - 1) % len(self._curation_session_queue)
        self._curation_load_selected_session()

    def _curation_queue_next_session(self) -> None:
        if not self._curation_session_queue:
            raise ValueError("Load a curation queue first.")
        if self._curation_queue_index is None:
            self._curation_queue_index = 0
        else:
            self._curation_queue_index = (self._curation_queue_index + 1) % len(self._curation_session_queue)
        self._curation_load_selected_session()

    def _set_curation_filter(self, mode: str) -> None:
        self._vars["curation_filter_mode"].set(mode)
        self._invalidate_curation_index_cache()
        self._refresh_curation_roi_list()
        self._update_curation_roi_info()
        self._render_curation_view()

    def _invalidate_curation_index_cache(self) -> None:
        self._curation_visible_indices_cache = None
        self._curation_review_order_cache = None

    def _invalidate_curation_render_cache(self) -> None:
        self._curation_render_cache_key = None
        self._curation_render_cache_image = None
        self._curation_render_cache_hit_map = None
        self._curation_render_cache_shape = None
        self._curation_render_cache_origin = (0, 0)
        self._curation_render_cache_display_size = (1, 1)
        self._curation_render_cache_view_offset = (0, 0)
        self._curation_render_cache_view_scale = 1.0

    def _reset_curation_zoom(self) -> None:
        self._curation_manual_zoom_factor = 1.0
        self._curation_manual_zoom_center = None
        self._invalidate_curation_render_cache()
        self._render_curation_view()

    def _on_curation_zoom_wheel(self, event) -> str:
        steps = -1 if getattr(event, "num", None) == 4 else 1 if getattr(event, "num", None) == 5 else self._wheel_steps(event)
        if steps == 0:
            return "break"
        self._adjust_curation_manual_zoom(steps=steps, canvas_x=int(event.x), canvas_y=int(event.y))
        return "break"

    def _adjust_curation_manual_zoom(self, *, steps: int, canvas_x: int, canvas_y: int) -> None:
        if not self._curation_payload:
            return
        image = self._curation_background_image()
        if image.ndim != 2:
            return

        anchor = self._canvas_to_full_coords(canvas_x, canvas_y)
        if anchor is None:
            if self._curation_manual_zoom_center is not None:
                anchor = self._curation_manual_zoom_center
            else:
                anchor = (image.shape[0] / 2.0, image.shape[1] / 2.0)

        zoom_multiplier = 1.15 ** (-steps)
        new_factor = float(np.clip(self._curation_manual_zoom_factor * zoom_multiplier, 0.35, 12.0))
        if 0.995 <= new_factor <= 1.005:
            new_factor = 1.0
        self._curation_manual_zoom_factor = new_factor
        if new_factor > 1.0:
            self._curation_manual_zoom_center = anchor
        else:
            self._curation_manual_zoom_center = None
        self._vars["curation_zoom_to_selected"].set(False)
        self._invalidate_curation_render_cache()
        self._render_curation_view()

    def _current_curation_accepted(self) -> np.ndarray:
        if not self._curation_payload:
            return np.zeros((0,), dtype=bool)
        iscell = np.asarray(self._curation_payload["iscell"])
        if iscell.ndim == 2:
            return iscell[:, 0] > 0.5
        return iscell > 0.5

    def _visible_curation_indices(self) -> list[int]:
        if not self._curation_payload:
            return []
        if self._curation_visible_indices_cache is not None:
            return list(self._curation_visible_indices_cache)
        stat = np.asarray(self._curation_payload["stat"], dtype=object)
        suggestions = self._curation_payload.get("suggestions", {})
        suggested_keep = np.asarray(suggestions.get("suggested_keep", np.zeros(len(stat), dtype=bool)))
        suggestion_conf = np.asarray(suggestions.get("confidence", np.zeros(len(stat), dtype=np.float32)))
        suggestion_available = np.asarray(suggestions.get("available", np.zeros(len(stat), dtype=bool)))
        indices = self._curation_indices_for_navigation(
            stat=stat,
            suggestion_available=suggestion_available,
            suggested_keep=suggested_keep,
            suggestion_conf=suggestion_conf,
            include_filter=True,
            include_focus=True,
        )
        self._curation_visible_indices_cache = list(indices)
        return indices

    def _curation_indices_for_navigation(
        self,
        *,
        stat: np.ndarray,
        suggestion_available: np.ndarray,
        suggested_keep: np.ndarray,
        suggestion_conf: np.ndarray,
        include_filter: bool,
        include_focus: bool,
    ) -> list[int]:
        accepted = self._current_curation_accepted()
        indices = list(range(len(stat)))

        if include_filter:
            filter_mode = self._vars["curation_filter_mode"].get()
            if filter_mode == "accepted":
                indices = [idx for idx in indices if accepted[idx]]
            elif filter_mode == "rejected":
                indices = [idx for idx in indices if not accepted[idx]]

        if include_focus:
            focus_mode = self._vars["curation_review_focus"].get()
            if focus_mode == "suggested_keep":
                indices = [idx for idx in indices if idx < suggestion_available.shape[0] and suggestion_available[idx] and bool(suggested_keep[idx])]
            elif focus_mode == "suggested_reject":
                indices = [idx for idx in indices if idx < suggestion_available.shape[0] and suggestion_available[idx] and not bool(suggested_keep[idx])]
            elif focus_mode == "uncertain":
                indices = [
                    idx for idx in indices
                    if idx < suggestion_available.shape[0]
                    and suggestion_available[idx]
                    and abs(float(suggestion_conf[idx]) - 0.5) <= 0.15
                ]

        sort_mode = self._vars["curation_sort_mode"].get()
        if sort_mode == "area_desc":
            indices.sort(key=lambda idx: len(np.asarray(stat[idx]["ypix"])), reverse=True)
        elif sort_mode == "area_asc":
            indices.sort(key=lambda idx: len(np.asarray(stat[idx]["ypix"])))
        elif sort_mode == "compactness":
            def compact_key(idx: int) -> float:
                value = stat[idx].get("compact", 0.0)
                try:
                    return float(value)
                except Exception:
                    return 0.0
            indices.sort(key=compact_key, reverse=True)
        elif sort_mode == "suggest_keep_first":
            indices.sort(
                key=lambda idx: (
                    idx < suggestion_available.shape[0] and suggestion_available[idx] and bool(suggested_keep[idx]),
                    float(suggestion_conf[idx]) if idx < suggestion_conf.shape[0] else 0.0,
                ),
                reverse=True,
            )
        elif sort_mode == "uncertain_first":
            indices.sort(
                key=lambda idx: (
                    abs(float(suggestion_conf[idx]) - 0.5) if idx < suggestion_conf.shape[0] else 1.0,
                    idx,
                )
            )
        elif sort_mode == "confidence_desc":
            indices.sort(key=lambda idx: float(suggestion_conf[idx]) if idx < suggestion_conf.shape[0] else 0.0, reverse=True)
        return indices

    def _review_order_curation_indices(self) -> list[int]:
        if not self._curation_payload:
            return []
        if self._curation_review_order_cache is not None:
            return list(self._curation_review_order_cache)
        stat = np.asarray(self._curation_payload["stat"], dtype=object)
        suggestions = self._curation_payload.get("suggestions", {})
        suggested_keep = np.asarray(suggestions.get("suggested_keep", np.zeros(len(stat), dtype=bool)))
        suggestion_conf = np.asarray(suggestions.get("confidence", np.zeros(len(stat), dtype=np.float32)))
        suggestion_available = np.asarray(suggestions.get("available", np.zeros(len(stat), dtype=bool)))
        indices = self._curation_indices_for_navigation(
            stat=stat,
            suggestion_available=suggestion_available,
            suggested_keep=suggested_keep,
            suggestion_conf=suggestion_conf,
            include_filter=False,
            include_focus=False,
        )
        self._curation_review_order_cache = list(indices)
        return indices

    def _curation_apply_suggestion_preview(self) -> None:
        if not self._curation_payload:
            raise ValueError("Load curation data first.")
        suggestions = self._curation_payload.get("suggestions", {})
        available = np.asarray(suggestions.get("available", np.zeros(0, dtype=bool)))
        suggested_keep = np.asarray(suggestions.get("suggested_keep", np.zeros(0, dtype=bool)))
        confidence = np.asarray(suggestions.get("confidence", np.zeros(0, dtype=np.float32)))
        if available.size == 0 or not np.any(available):
            raise ValueError("No learned suggestions are available yet.")
        iscell = np.asarray(self._curation_payload["iscell"])
        applied = 0
        threshold = 0.70
        for idx in range(min(len(available), len(suggested_keep), len(confidence))):
            if not available[idx]:
                continue
            conf = float(confidence[idx])
            strength = max(conf, 1.0 - conf)
            if strength < threshold:
                continue
            value = 1.0 if bool(suggested_keep[idx]) else 0.0
            if iscell.ndim == 2:
                iscell[idx, 0] = value
            else:
                iscell[idx] = value
            applied += 1
        if applied == 0:
            self.curation_status_var.set("No high-confidence suggestion preview was applied. Try sorting by uncertainty first.")
        else:
            self.curation_status_var.set(
                f"Applied suggestion preview to {applied} ROI(s). Review and Save ROI Labels only if it looks right."
            )
        self._refresh_curation_roi_list()
        self._update_curation_roi_info()
        self._render_curation_view()

    def _curation_reset_working_labels(self) -> None:
        if not self._curation_payload or self._curation_saved_iscell is None:
            raise ValueError("Load curation data first.")
        self._curation_payload["iscell"] = np.array(self._curation_saved_iscell, copy=True)
        self.curation_status_var.set("Reset working ROI labels to the last loaded/saved state.")
        self._refresh_curation_roi_list()
        self._update_curation_roi_info()
        self._render_curation_view()

    def _refresh_curation_assistance(self) -> None:
        if not hasattr(self, "curation_merge_suggestion_listbox"):
            return
        self.curation_merge_suggestion_listbox.delete(0, tk.END)
        self.curation_soma_candidate_listbox.delete(0, tk.END)
        if not self._curation_payload:
            self.curation_assistance_var.set("Assistance: load curation data to see suggestions.")
            return
        assistance = self._curation_payload.get("assistance", {})
        merge_candidates = list(assistance.get("merge_candidates", []))
        soma_candidates = list(assistance.get("soma_candidates", []))
        for item in merge_candidates:
            pair = item.get("pair", ["?", "?"])
            self.curation_merge_suggestion_listbox.insert(
                tk.END,
                f"{pair[0]} + {pair[1]} | dist={float(item.get('distance', 0.0)):.1f} | score={float(item.get('score', 0.0)):.2f}",
            )
        for item in soma_candidates:
            self.curation_soma_candidate_listbox.insert(
                tk.END,
                f"({int(item.get('center_y', 0))}, {int(item.get('center_x', 0))}) | score={float(item.get('score', 0.0)):.1f}",
            )
        self.curation_assistance_var.set(
            f"Merge candidates: {len(merge_candidates)} | Missing soma candidates: {len(soma_candidates)}"
        )

    def _curation_select_merge_pair(self) -> None:
        if not self._curation_payload:
            raise ValueError("Load curation data first.")
        selection = self.curation_merge_suggestion_listbox.curselection()
        if not selection:
            raise ValueError("Select a suggested merge pair first.")
        assistance = self._curation_payload.get("assistance", {})
        merge_candidates = list(assistance.get("merge_candidates", []))
        pair = merge_candidates[selection[0]].get("pair", [])
        if len(pair) != 2:
            raise ValueError("Selected merge suggestion is invalid.")
        self._vars["curation_review_focus"].set("all")
        self._set_curation_filter("all")
        visible = self._visible_curation_indices()
        self.curation_roi_listbox.selection_clear(0, tk.END)
        for roi_index in pair:
            if roi_index in visible:
                list_index = visible.index(int(roi_index))
                self.curation_roi_listbox.selection_set(list_index)
                self.curation_roi_listbox.see(list_index)
        self._curation_selected_index = int(pair[0])
        self._update_curation_selection_info()
        self._update_curation_roi_info()
        if self._vars["curation_zoom_to_selected"].get():
            self._render_curation_view()
        else:
            self._draw_curation_selection_overlay()
        self.curation_status_var.set(f"Selected suggested merge pair: {pair[0]} + {pair[1]}. Press M to merge if it looks right.")

    def _curation_jump_soma_candidate(self) -> None:
        if not self._curation_payload:
            raise ValueError("Load curation data first.")
        selection = self.curation_soma_candidate_listbox.curselection()
        if not selection:
            raise ValueError("Select a soma candidate first.")
        assistance = self._curation_payload.get("assistance", {})
        soma_candidates = list(assistance.get("soma_candidates", []))
        candidate = soma_candidates[selection[0]]
        y = float(candidate.get("center_y", 0.0))
        x = float(candidate.get("center_x", 0.0))
        stat = np.asarray(self._curation_payload["stat"], dtype=object)
        if len(stat):
            dists = []
            for idx, roi in enumerate(stat):
                med = roi.get("med", (0.0, 0.0))
                dists.append((float(np.hypot(float(med[0]) - y, float(med[1]) - x)), idx))
            dists.sort(key=lambda item: item[0])
            nearest_idx = int(dists[0][1])
            self._curation_selected_index = nearest_idx
            self._update_curation_roi_info()
            if self._vars["curation_zoom_to_selected"].get():
                self._render_curation_view()
            else:
                self._draw_curation_selection_overlay()
        self.curation_status_var.set(
            f"Soma candidate at ({int(y)}, {int(x)}). Use Add Candidate ROI if you want to place a manual ROI there."
        )

    def _curation_add_soma_candidate(self) -> None:
        if not self._curation_payload:
            raise ValueError("Load curation data first.")
        selection = self.curation_soma_candidate_listbox.curselection()
        if not selection:
            raise ValueError("Select a soma candidate first.")
        assistance = self._curation_payload.get("assistance", {})
        soma_candidates = list(assistance.get("soma_candidates", []))
        candidate = soma_candidates[selection[0]]
        self._curation_add_manual_roi(float(candidate.get("center_y", 0.0)), float(candidate.get("center_x", 0.0)))

    def _refresh_curation_roi_list(self) -> None:
        if not hasattr(self, "curation_roi_listbox"):
            return
        self.curation_roi_listbox.delete(0, tk.END)
        if not self._curation_payload:
            self.curation_selection_var.set("Merge set: 0 selected")
            return
        stat = np.asarray(self._curation_payload["stat"], dtype=object)
        accepted = self._current_curation_accepted()
        suggestions = self._curation_payload.get("suggestions", {})
        suggested_keep = np.asarray(suggestions.get("suggested_keep", np.zeros(len(stat), dtype=bool)))
        suggestion_conf = np.asarray(suggestions.get("confidence", np.zeros(len(stat), dtype=np.float32)))
        suggestion_available = np.asarray(suggestions.get("available", np.zeros(len(stat), dtype=bool)))
        for idx in self._visible_curation_indices():
            roi = stat[idx]
            area = len(np.asarray(roi["ypix"]))
            compact = roi.get("compact", None)
            compact_text = ""
            if compact is not None:
                try:
                    compact_text = f" | c={float(compact):.2f}"
                except Exception:
                    compact_text = ""
            suggest_text = ""
            if idx < suggestion_available.shape[0] and suggestion_available[idx]:
                label = "K" if bool(suggested_keep[idx]) else "R"
                suggest_text = f" | sug={label}:{float(suggestion_conf[idx]):.2f}"
            self.curation_roi_listbox.insert(tk.END, f"{idx:03d} | {'A' if accepted[idx] else 'R'} | area={area}{compact_text}{suggest_text}")
        self._select_curation_list_index(self._curation_selected_index)
        self._update_curation_selection_info()

    def _select_curation_list_index(self, roi_index: int | None) -> None:
        if not hasattr(self, "curation_roi_listbox"):
            return
        if not self._vars["show_curation_roi_list"].get():
            return
        if roi_index is None:
            self.curation_roi_listbox.selection_clear(0, tk.END)
            return
        visible = self._visible_curation_indices()
        if roi_index not in visible:
            return
        list_index = visible.index(roi_index)
        existing = tuple(self.curation_roi_listbox.curselection())
        if len(existing) > 1 and list_index in existing:
            self.curation_roi_listbox.see(list_index)
            return
        self.curation_roi_listbox.selection_clear(0, tk.END)
        self.curation_roi_listbox.selection_set(list_index)
        self.curation_roi_listbox.see(list_index)

    def _load_curation_session_status(self) -> None:
        if self.state_obj.session_path is None:
            self._vars["curation_session_status"].set("not_started")
            self._vars["curation_session_notes"].set("")
            return
        payload = self.controller.load_session_curation_status(self.state_obj.session_path)
        self._vars["curation_session_status"].set(str(payload.get("status", "not_started")))
        self._vars["curation_session_notes"].set(str(payload.get("notes", "")))

    def _curation_save_session_status(self) -> None:
        self._ensure_loaded_run()
        status = self._vars["curation_session_status"].get().strip() or "not_started"
        notes = self._vars["curation_session_notes"].get().strip()
        path = self.controller.save_session_curation_status(status, notes=notes)
        self.curation_status_var.set(f"Saved session status '{status}' to {path}.")
        self._refresh_curation_queue_list()

    def _curation_mark_session_completed(self) -> None:
        self._vars["curation_session_status"].set("completed")
        self._curation_save_session_status()

    def _curation_mark_session_no_soma(self) -> None:
        self._vars["curation_session_status"].set("no_soma")
        self._curation_save_session_status()

    def _curation_mark_session_failed(self) -> None:
        self._vars["curation_session_status"].set("failed")
        self._curation_save_session_status()

    def _update_curation_roi_info(self) -> None:
        if not self._curation_payload:
            self.curation_counts_var.set("Visible: 0 | Accepted: 0 | Rejected: 0")
            self.curation_roi_info_var.set("No curation dataset loaded.")
            return
        stat = np.asarray(self._curation_payload["stat"], dtype=object)
        accepted = self._current_curation_accepted()
        visible = self._visible_curation_indices()
        self.curation_counts_var.set(
            f"Visible: {len(visible)} | Accepted: {int(accepted.sum())} | Rejected: {len(stat) - int(accepted.sum())}"
        )
        if self._curation_selected_index is None or self._curation_selected_index >= len(stat):
            self.curation_roi_info_var.set(
                f"ROIs: {len(stat)}\n"
                f"Accepted: {int(accepted.sum())}\n"
                f"Rejected: {len(stat) - int(accepted.sum())}\n\n"
                "Click an ROI to inspect it."
            )
            return
        idx = self._curation_selected_index
        roi = stat[idx]
        area = len(np.asarray(roi["ypix"]))
        med = roi.get("med", ("?", "?"))
        compact = roi.get("compact", None)
        status = "Accepted" if accepted[idx] else "Rejected"
        suggestions = self._curation_payload.get("suggestions", {})
        suggested_keep = np.asarray(suggestions.get("suggested_keep", np.zeros(len(stat), dtype=bool)))
        suggestion_conf = np.asarray(suggestions.get("confidence", np.zeros(len(stat), dtype=np.float32)))
        suggestion_available = np.asarray(suggestions.get("available", np.zeros(len(stat), dtype=bool)))
        lines = [
            f"ROI Index: {idx}",
            f"Status: {status}",
            f"Centroid: {med}",
            f"Area: {area} px",
        ]
        if compact is not None:
            try:
                lines.append(f"Compactness: {float(compact):.3f}")
            except Exception:
                pass
        if idx < suggestion_available.shape[0] and suggestion_available[idx]:
            label = "Keep" if bool(suggested_keep[idx]) else "Reject"
            lines.append(f"Suggested: {label} ({float(suggestion_conf[idx]):.2f})")
        if idx in visible:
            lines.append(f"Visible Rank: {visible.index(idx) + 1} / {len(visible)}")
        self.curation_roi_info_var.set("\n".join(lines))
        self._select_curation_list_index(idx)

    def _render_curation_trace(self) -> None:
        return

    def _draw_roi_trace_canvas(self, canvas: tk.Canvas, roi_index: int) -> None:
        width = max(1, int(canvas.winfo_width() or 520))
        height = max(1, int(canvas.winfo_height() or 260))
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill="#000000", outline="")

        if not self._curation_payload:
            canvas.create_text(width // 2, height // 2, text="Load curation data first.", fill=self.colors["muted"])
            return

        traces = self._curation_payload.get("traces", {})
        series = [
            ("F", traces.get("F"), "#5dd6c0"),
            ("Fneu", traces.get("Fneu"), "#ff9078"),
            ("dF/F", traces.get("corrected"), "#f8dc64"),
        ]
        plotted: list[tuple[str, np.ndarray, str]] = []
        for name, data, color in series:
            if data is None:
                continue
            arr = np.asarray(data)
            if arr.ndim != 2 or roi_index >= arr.shape[0]:
                continue
            plotted.append((name, np.asarray(arr[roi_index], dtype=float), color))

        if not plotted:
            canvas.create_text(width // 2, height // 2, text="No trace arrays available for this ROI.", fill=self.colors["muted"])
            return

        ops = self._curation_payload.get("ops", {})
        fs_raw = ops.get("fs", None)
        try:
            fs = float(fs_raw) if fs_raw is not None else None
        except Exception:
            fs = None
        frame_count = int(plotted[0][1].shape[0]) if plotted else 0

        min_v = min(float(np.nanmin(trace)) for _name, trace, _color in plotted)
        max_v = max(float(np.nanmax(trace)) for _name, trace, _color in plotted)
        if not np.isfinite(min_v) or not np.isfinite(max_v) or max_v <= min_v:
            min_v, max_v = 0.0, 1.0

        pad_x = 14
        top_pad = 26
        bottom_pad = 36
        canvas.create_rectangle(pad_x, top_pad, width - pad_x, height - bottom_pad, outline="#2e3945")
        for _name, trace, color in plotted:
            n = trace.shape[0]
            if n < 2:
                continue
            max_points = max(64, min(n, width - 2 * pad_x))
            if n > max_points:
                sample_idx = np.linspace(0, n - 1, num=max_points, dtype=np.int32)
                trace_to_plot = trace[sample_idx]
                denom = max(1, max_points - 1)
            else:
                trace_to_plot = trace
                denom = max(1, n - 1)
            points: list[float] = []
            for i, value in enumerate(trace_to_plot):
                x = pad_x + (i / denom) * (width - 2 * pad_x)
                y = height - bottom_pad - ((float(value) - min_v) / (max_v - min_v)) * (height - top_pad - bottom_pad)
                points.extend([x, y])
            canvas.create_line(points, fill=color, width=1.6, smooth=False)

        legend_x = pad_x + 4
        for name, _trace, color in plotted:
            canvas.create_text(legend_x, 8, text=name, fill=color, anchor="nw")
            legend_x += 70

        axis_y = height - bottom_pad
        tick_bottom = axis_y + 4
        tick_top = axis_y - 4
        tick_positions = [0.0, 0.25, 0.5, 0.75, 1.0]
        last_index = max(0, frame_count - 1)

        for frac in tick_positions:
            frame_index = int(round(frac * last_index)) if frame_count > 1 else 0
            x = pad_x if frame_count <= 1 else pad_x + (frame_index / max(1, last_index)) * (width - 2 * pad_x)
            canvas.create_line(x, tick_top, x, tick_bottom, fill=self.colors["muted"], width=1)
            label = f"{(frame_index / fs):.1f} s" if fs and fs > 0 else str(frame_index)
            anchor = "s"
            if frac == 0.0:
                anchor = "sw"
            elif frac == 1.0:
                anchor = "se"
            canvas.create_text(x, height - 8, text=label, fill=self.colors["muted"], anchor=anchor)

    def _open_selected_roi_trace_window(self) -> None:
        if not self._curation_payload:
            raise ValueError("Load curation data first.")
        if self._curation_selected_index is None:
            raise ValueError("Select an ROI first.")

        idx = int(self._curation_selected_index)
        window = tk.Toplevel(self)
        window.title(f"{APP_NAME} - ROI {idx} Trace")
        window.configure(bg=self.colors["bg"])
        window.geometry("560x320")

        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        ttk.Label(frame, text=f"ROI {idx} Trace Viewer").grid(row=0, column=0, sticky="w", pady=(0, 8))
        canvas = tk.Canvas(frame, width=520, height=260, bg="#000000", highlightthickness=0, bd=0)
        canvas.grid(row=1, column=0, sticky="nsew")
        self._draw_roi_trace_canvas(canvas, idx)


    def _curation_background_image(self) -> np.ndarray:
        if not self._curation_payload:
            raise RuntimeError("No curation payload loaded.")
        ops = self._curation_payload["ops"]
        mode = self._vars["curation_bg_mode"].get()
        key_map = {"mean": "meanImg", "max": "max_proj", "corr": "Vcorr"}
        image = np.asarray(ops.get(key_map[mode]))
        if image.ndim != 2:
            raise RuntimeError(f"Selected background image is not available: {mode}")
        return image

    def _render_curation_view(self) -> None:
        if not hasattr(self, "curation_canvas"):
            return
        canvas_w = max(1, self.curation_canvas.winfo_width())
        canvas_h = max(1, self.curation_canvas.winfo_height())
        self.curation_canvas.delete("all")
        if not self._curation_payload:
            self.curation_canvas.create_text(canvas_w // 2, canvas_h // 2, text="Load curation data to begin.", fill=self.colors["muted"])
            return

        stat = np.asarray(self._curation_payload["stat"], dtype=object)
        accepted = self._current_curation_accepted()
        filter_mode = self._vars["curation_filter_mode"].get()
        image = self._curation_background_image().astype(np.float32)

        if self._vars["curation_zoom_to_selected"].get() and self._curation_selected_index is not None:
            roi = stat[self._curation_selected_index]
            ypix = np.asarray(roi["ypix"], dtype=np.int32)
            xpix = np.asarray(roi["xpix"], dtype=np.int32)
            if ypix.size and xpix.size:
                pad = 40
                y0 = max(0, int(ypix.min()) - pad)
                y1 = min(image.shape[0], int(ypix.max()) + pad + 1)
                x0 = max(0, int(xpix.min()) - pad)
                x1 = min(image.shape[1], int(xpix.max()) + pad + 1)
            else:
                y0, y1, x0, x1 = 0, image.shape[0], 0, image.shape[1]
        elif self._curation_manual_zoom_factor > 1.0:
            center_y, center_x = self._curation_manual_zoom_center or (image.shape[0] / 2.0, image.shape[1] / 2.0)
            crop_h = max(20, int(round(image.shape[0] / self._curation_manual_zoom_factor)))
            crop_w = max(20, int(round(image.shape[1] / self._curation_manual_zoom_factor)))
            y0 = int(round(center_y - crop_h / 2.0))
            x0 = int(round(center_x - crop_w / 2.0))
            y0 = max(0, min(y0, image.shape[0] - crop_h))
            x0 = max(0, min(x0, image.shape[1] - crop_w))
            y1 = min(image.shape[0], y0 + crop_h)
            x1 = min(image.shape[1], x0 + crop_w)
        else:
            y0, y1, x0, x1 = 0, image.shape[0], 0, image.shape[1]

        accepted_signature = hash(np.asarray(accepted, dtype=np.uint8).tobytes())
        render_key = (
            str(self._curation_payload.get("plane_dir", "")),
            self._vars["curation_bg_mode"].get(),
            float(self._vars["curation_image_saturation"].get()),
            float(self._vars["curation_saturation"].get()),
            bool(self._vars["curation_show_overlay"].get()),
            filter_mode,
            bool(self._vars["curation_zoom_to_selected"].get()),
            int(self._curation_selected_index) if self._curation_selected_index is not None else -1,
            int(y0), int(y1), int(x0), int(x1),
            int(canvas_w), int(canvas_h),
            accepted_signature,
        )

        if render_key != self._curation_render_cache_key:
            img_min = float(np.percentile(image, 2))
            img_max = float(np.percentile(image, 99.5))
            if img_max <= img_min:
                img_max = img_min + 1.0
            base = np.clip((image - img_min) / (img_max - img_min), 0.0, 1.0)
            image_gain = float(self._vars["curation_image_saturation"].get())
            base = np.clip(base * image_gain, 0.0, 1.0)
            base_rgb = np.stack([base, base, base], axis=-1)

            hit_map = np.full(image.shape, -1, dtype=np.int32)
            overlay = np.zeros_like(base_rgb)
            alpha = np.zeros(image.shape, dtype=np.float32)
            base_alpha = float(self._vars["curation_saturation"].get())
            for idx, roi in enumerate(stat):
                roi_is_accepted = bool(accepted[idx])
                if filter_mode == "accepted" and not roi_is_accepted:
                    continue
                if filter_mode == "rejected" and roi_is_accepted:
                    continue
                ypix = np.asarray(roi["ypix"], dtype=np.int32)
                xpix = np.asarray(roi["xpix"], dtype=np.int32)
                valid = (ypix >= 0) & (ypix < image.shape[0]) & (xpix >= 0) & (xpix < image.shape[1])
                if not np.any(valid):
                    continue
                ypix = ypix[valid]
                xpix = xpix[valid]
                hit_map[ypix, xpix] = idx
                if not self._vars["curation_show_overlay"].get():
                    continue
                color = np.array([0.0, 0.84, 0.75], dtype=np.float32) if roi_is_accepted else np.array([1.0, 0.45, 0.38], dtype=np.float32)
                overlay[ypix, xpix] = color
                alpha[ypix, xpix] = base_alpha

            composed = base_rgb * (1.0 - alpha[..., None]) + overlay * alpha[..., None]
            cropped = composed[y0:y1, x0:x1]
            cropped_hit_map = hit_map[y0:y1, x0:x1]
            display_h, display_w = cropped.shape[:2]
            scale = min(canvas_w / max(1, display_w), canvas_h / max(1, display_h))
            if not self._vars["curation_zoom_to_selected"].get() and self._curation_manual_zoom_factor < 1.0:
                scale *= max(self._curation_manual_zoom_factor, 1e-3)
            scale = max(scale, 1e-6)
            target_w = max(1, int(display_w * scale))
            target_h = max(1, int(display_h * scale))
            image_u8 = (np.clip(cropped, 0.0, 1.0) * 255).astype(np.uint8)
            pil_image = Image.fromarray(image_u8, mode="RGB").resize((target_w, target_h), Image.Resampling.BILINEAR)
            self._curation_render_cache_image = ImageTk.PhotoImage(pil_image)
            self._curation_render_cache_hit_map = cropped_hit_map
            self._curation_render_cache_shape = cropped_hit_map.shape
            self._curation_render_cache_origin = (y0, x0)
            self._curation_render_cache_display_size = (target_w, target_h)
            self._curation_render_cache_view_offset = (
                max(0, (canvas_w - target_w) // 2),
                max(0, (canvas_h - target_h) // 2),
            )
            self._curation_render_cache_view_scale = scale
            self._curation_render_cache_key = render_key

        self._curation_photo = self._curation_render_cache_image
        self._curation_hit_map = self._curation_render_cache_hit_map
        self._curation_image_shape = self._curation_render_cache_shape
        self._curation_crop_origin = self._curation_render_cache_origin
        self._curation_display_size = self._curation_render_cache_display_size
        self._curation_view_offset = self._curation_render_cache_view_offset
        self._curation_view_scale = self._curation_render_cache_view_scale
        offset_x, offset_y = self._curation_view_offset
        target_w, target_h = self._curation_display_size
        self.curation_canvas.create_image(offset_x, offset_y, anchor="nw", image=self._curation_photo, tags=("curation_base",))
        self.curation_canvas.create_rectangle(
            offset_x,
            offset_y,
            offset_x + target_w,
            offset_y + target_h,
            outline=self.colors["border"],
            width=1,
            tags=("curation_frame",),
        )
        self._draw_curation_selection_overlay()

    def _draw_curation_selection_overlay(self) -> None:
        if not hasattr(self, "curation_canvas"):
            return
        self.curation_canvas.delete("curation_selection")
        if not self._curation_payload:
            return
        stat = np.asarray(self._curation_payload["stat"], dtype=object)
        y0, x0 = self._curation_crop_origin
        img_h, img_w = self._curation_image_shape or (0, 0)
        scale = max(self._curation_view_scale, 1e-6)
        offset_x, offset_y = self._curation_view_offset
        merge_selected = sorted(idx for idx in self._curation_merge_selection if 0 <= idx < len(stat))
        highlight_indices = set(merge_selected)
        if self._curation_selected_index is not None and 0 <= self._curation_selected_index < len(stat):
            highlight_indices.add(self._curation_selected_index)
        if highlight_indices and img_h > 0 and img_w > 0:
            overlay_rgba = np.zeros((img_h, img_w, 4), dtype=np.uint8)
            for idx in sorted(highlight_indices):
                roi = stat[idx]
                ypix = np.asarray(roi.get("ypix", []), dtype=np.int32)
                xpix = np.asarray(roi.get("xpix", []), dtype=np.int32)
                if ypix.size == 0 or xpix.size == 0:
                    continue
                valid = (
                    (ypix >= y0) & (ypix < y0 + img_h) &
                    (xpix >= x0) & (xpix < x0 + img_w)
                )
                if not np.any(valid):
                    continue
                ypix_local = ypix[valid] - y0
                xpix_local = xpix[valid] - x0
                overlay_rgba[ypix_local, xpix_local, 0] = 255
                overlay_rgba[ypix_local, xpix_local, 1] = 214
                overlay_rgba[ypix_local, xpix_local, 2] = 51
                overlay_rgba[ypix_local, xpix_local, 3] = 145

            if np.any(overlay_rgba[..., 3] > 0):
                selection_img = Image.fromarray(overlay_rgba, mode="RGBA").resize(
                    self._curation_display_size,
                    Image.Resampling.NEAREST,
                )
                self._curation_selection_photo = ImageTk.PhotoImage(selection_img)
                self.curation_canvas.create_image(
                    offset_x,
                    offset_y,
                    anchor="nw",
                    image=self._curation_selection_photo,
                    tags=("curation_selection",),
                )
        self._draw_curation_manual_preview()

    def _draw_curation_manual_preview(self) -> None:
        if not hasattr(self, "curation_canvas"):
            return
        self.curation_canvas.delete("curation_manual_preview")
        if not self._vars["curation_manual_mode"].get():
            return
        center = self._curation_manual_drag_center or self._curation_manual_hover_center
        if center is None:
            return
        diameter = float(
            self._curation_manual_preview_diameter
            if self._curation_manual_preview_diameter is not None
            else self._vars["curation_manual_diameter"].get()
        )
        if diameter <= 0:
            return
        mapped = self._full_to_canvas_coords(center[0], center[1])
        if mapped is None:
            return
        canvas_x, canvas_y = mapped
        radius = max(2.0, (diameter / 2.0) * max(self._curation_view_scale, 1e-6))
        self.curation_canvas.create_oval(
            canvas_x - radius,
            canvas_y - radius,
            canvas_x + radius,
            canvas_y + radius,
            outline="#ffd633",
            width=2,
            dash=(3, 2),
            tags=("curation_manual_preview",),
        )
        self.curation_canvas.create_oval(
            canvas_x - 2,
            canvas_y - 2,
            canvas_x + 2,
            canvas_y + 2,
            fill="#ffd633",
            outline="",
            tags=("curation_manual_preview",),
        )

    def _canvas_to_full_coords(self, canvas_x: int, canvas_y: int) -> tuple[float, float] | None:
        if self._curation_image_shape is None:
            return None
        disp_w, disp_h = self._curation_display_size
        offset_x, offset_y = self._curation_view_offset
        rel_x = canvas_x - offset_x
        rel_y = canvas_y - offset_y
        if rel_x < 0 or rel_y < 0 or rel_x >= disp_w or rel_y >= disp_h:
            return None
        img_h, img_w = self._curation_image_shape
        x = min(img_w - 1, max(0, int(rel_x / max(1, disp_w) * img_w)))
        y = min(img_h - 1, max(0, int(rel_y / max(1, disp_h) * img_h)))
        full_y = self._curation_crop_origin[0] + y
        full_x = self._curation_crop_origin[1] + x
        return float(full_y), float(full_x)

    def _full_to_canvas_coords(self, full_y: float, full_x: float) -> tuple[float, float] | None:
        img_h, img_w = self._curation_image_shape or (0, 0)
        y0, x0 = self._curation_crop_origin
        if full_y < y0 or full_y >= y0 + img_h or full_x < x0 or full_x >= x0 + img_w:
            return None
        local_y = full_y - y0 + 0.5
        local_x = full_x - x0 + 0.5
        offset_x, offset_y = self._curation_view_offset
        scale = max(self._curation_view_scale, 1e-6)
        return offset_x + local_x * scale, offset_y + local_y * scale

    def _on_curation_canvas_click(self, event, ctrl_override: bool | None = None) -> None:
        if self._curation_hit_map is None or self._curation_image_shape is None:
            return
        full_coords = self._canvas_to_full_coords(event.x, event.y)
        if full_coords is None:
            return
        full_y, full_x = full_coords
        if self._vars["curation_manual_mode"].get():
            self._curation_manual_drag_center = (full_y, full_x)
            self._curation_manual_hover_center = (full_y, full_x)
            self._curation_manual_preview_diameter = float(self._vars["curation_manual_diameter"].get())
            self._draw_curation_selection_overlay()
            return
        img_h, img_w = self._curation_image_shape
        y = int(full_y - self._curation_crop_origin[0])
        x = int(full_x - self._curation_crop_origin[1])
        idx = int(self._curation_hit_map[y, x])
        if idx < 0:
            idx = self._nearest_curation_roi_index(full_y, full_x, event.x, event.y)
            if idx < 0:
                return
        ctrl_pressed = ctrl_override if ctrl_override is not None else bool(event.state & 0x0004)
        if ctrl_pressed:
            if idx in self._curation_merge_selection:
                self._curation_merge_selection.discard(idx)
            else:
                self._curation_merge_selection.add(idx)
        else:
            self._curation_merge_selection = {idx}
        self._curation_selected_index = idx
        self._sync_curation_listbox_from_merge_selection()
        self._update_curation_selection_info()
        self._update_curation_roi_info()
        if self._vars["curation_zoom_to_selected"].get():
            self._render_curation_view()
        else:
            self._draw_curation_selection_overlay()

    def _on_curation_canvas_motion(self, event) -> None:
        if not self._vars["curation_manual_mode"].get():
            return
        if self._curation_manual_drag_center is not None:
            return
        full_coords = self._canvas_to_full_coords(event.x, event.y)
        self._curation_manual_hover_center = full_coords
        self._curation_manual_preview_diameter = float(self._vars["curation_manual_diameter"].get())
        self._draw_curation_selection_overlay()

    def _on_curation_canvas_drag(self, event) -> None:
        if not self._vars["curation_manual_mode"].get():
            return
        if self._curation_manual_drag_center is None:
            return
        full_coords = self._canvas_to_full_coords(event.x, event.y)
        if full_coords is None:
            return
        center_y, center_x = self._curation_manual_drag_center
        current_y, current_x = full_coords
        radius = float(np.hypot(current_y - center_y, current_x - center_x))
        base_diameter = float(self._vars["curation_manual_diameter"].get())
        self._curation_manual_preview_diameter = max(base_diameter, radius * 2.0)
        self._draw_curation_selection_overlay()

    def _on_curation_canvas_release(self, event) -> None:
        if not self._vars["curation_manual_mode"].get():
            return
        if self._curation_manual_drag_center is None:
            return
        center_y, center_x = self._curation_manual_drag_center
        diameter = float(
            self._curation_manual_preview_diameter
            if self._curation_manual_preview_diameter is not None
            else self._vars["curation_manual_diameter"].get()
        )
        self._curation_manual_drag_center = None
        self._curation_manual_hover_center = (center_y, center_x)
        self._curation_manual_preview_diameter = float(self._vars["curation_manual_diameter"].get())
        try:
            self._curation_add_manual_roi(center_y, center_x, diameter=diameter)
        except Exception as exc:
            self.curation_status_var.set(f"Manual ROI add failed: {exc}")
            self._draw_curation_selection_overlay()

    def _nearest_curation_roi_index(self, full_y: int, full_x: int, canvas_x: int, canvas_y: int) -> int:
        if not self._curation_payload:
            return -1
        stat = np.asarray(self._curation_payload["stat"], dtype=object)
        best_idx = -1
        best_dist = float("inf")
        y0, x0 = self._curation_crop_origin
        img_h, img_w = self._curation_image_shape or (0, 0)
        scale = max(self._curation_view_scale, 1e-6)
        offset_x, offset_y = self._curation_view_offset
        max_canvas_dist = 14.0
        for idx, roi in enumerate(stat):
            med = roi.get("med", None)
            if med is None or len(med) < 2:
                continue
            try:
                cy = float(med[0])
                cx = float(med[1])
            except Exception:
                continue
            if cy < y0 or cy >= y0 + img_h or cx < x0 or cx >= x0 + img_w:
                continue
            local_y = cy - float(y0)
            local_x = cx - float(x0)
            screen_x = offset_x + (local_x + 0.5) * scale
            screen_y = offset_y + (local_y + 0.5) * scale
            dist = float(np.hypot(screen_y - float(canvas_y), screen_x - float(canvas_x)))
            if dist < best_dist:
                best_dist = dist
                best_idx = idx
        return best_idx if best_dist <= max_canvas_dist else -1

    def _curation_add_manual_roi(self, center_y: float, center_x: float, diameter: float | None = None) -> None:
        if not self._curation_payload:
            raise ValueError("Load curation data first.")
        if diameter is None:
            diameter = float(self._vars["curation_manual_diameter"].get())
        result = self.controller.append_manual_curation_roi(center_y, center_x, diameter)
        self._load_curation_data()
        self._curation_selected_index = int(result["new_index"])
        self._update_curation_roi_info()
        self._render_curation_view()
        self.curation_status_var.set(
            f"Added manual ROI {result['new_index']} at ({result['center_y']:.1f}, {result['center_x']:.1f}) "
            f"with diameter {result['diameter']:.1f}. Save ROI Labels or Finalize when ready."
        )

    def _curation_delete_selected_manual_rois(self) -> None:
        if not self._curation_payload:
            raise ValueError("Load curation data first.")
        selected = self._selected_curation_roi_indices()
        if not selected and self._curation_selected_index is not None:
            selected = [int(self._curation_selected_index)]
        if not selected:
            raise ValueError("Select at least one manual ROI to delete.")

        result = self.controller.delete_manual_curation_rois(selected)
        deleted = set(int(idx) for idx in result["deleted_indices"])
        old_selected = self._curation_selected_index
        self._load_curation_data()
        self._curation_merge_selection = set()

        review_order = self._review_order_curation_indices()
        if review_order:
            if old_selected is not None:
                survivors = [idx for idx in review_order if idx not in deleted]
                self._curation_selected_index = survivors[0] if survivors else review_order[0]
            else:
                self._curation_selected_index = review_order[0]
        else:
            self._curation_selected_index = None

        self._update_curation_selection_info()
        self._update_curation_roi_info()
        if self._vars["curation_zoom_to_selected"].get():
            self._render_curation_view()
        else:
            self._draw_curation_selection_overlay()
        deleted_text = ", ".join(str(idx) for idx in result["deleted_indices"])
        self.curation_status_var.set(
            f"Deleted manual ROI(s) {deleted_text}. {result['remaining_count']} ROI(s) remain in this plane."
        )

    def _on_curation_list_select(self, _event=None) -> None:
        if not self._curation_payload or not hasattr(self, "curation_roi_listbox"):
            return
        selection = self.curation_roi_listbox.curselection()
        if not selection:
            self._curation_merge_selection = set()
            self._update_curation_selection_info()
            return
        visible = self._visible_curation_indices()
        list_index = selection[-1]
        if list_index >= len(visible):
            self._curation_merge_selection = set()
            self._update_curation_selection_info()
            return
        self._curation_merge_selection = set()
        for selected_list_index in selection:
            if 0 <= selected_list_index < len(visible):
                self._curation_merge_selection.add(visible[selected_list_index])
        self._curation_selected_index = visible[list_index]
        self._update_curation_selection_info()
        self._update_curation_roi_info()
        if self._vars["curation_zoom_to_selected"].get():
            self._render_curation_view()
        else:
            self._draw_curation_selection_overlay()

    def _selected_curation_roi_indices(self) -> list[int]:
        if self._curation_merge_selection:
            return sorted(self._curation_merge_selection)
        if not self._curation_payload or not hasattr(self, "curation_roi_listbox"):
            return []
        visible = self._visible_curation_indices()
        selected: list[int] = []
        for list_index in self.curation_roi_listbox.curselection():
            if 0 <= list_index < len(visible):
                selected.append(visible[list_index])
        return sorted(set(selected))

    def _sync_curation_listbox_from_merge_selection(self) -> None:
        if not hasattr(self, "curation_roi_listbox") or not self._curation_payload:
            return
        if not self._vars["show_curation_roi_list"].get():
            return
        visible = self._visible_curation_indices()
        self.curation_roi_listbox.selection_clear(0, tk.END)
        for roi_index in sorted(self._curation_merge_selection):
            if roi_index in visible:
                list_index = visible.index(roi_index)
                self.curation_roi_listbox.selection_set(list_index)
                self.curation_roi_listbox.see(list_index)

    def _update_curation_selection_info(self) -> None:
        selected = self._selected_curation_roi_indices()
        if not selected:
            self.curation_selection_var.set("Merge set: 0 selected")
            return
        preview = ", ".join(str(idx) for idx in selected[:4])
        if len(selected) > 4:
            preview += ", ..."
        self.curation_selection_var.set(f"Merge set: {len(selected)} selected ({preview})")

    def _curation_merge_selected(self) -> None:
        if not self._curation_payload:
            raise ValueError("Load curation data first.")
        selected = self._selected_curation_roi_indices()
        if len(selected) < 2:
            raise ValueError("Select at least two ROIs in the ROI List to merge.")
        result = self.controller.merge_curation_rois(selected)
        self._load_curation_data()
        self._curation_selected_index = int(result["new_index"])
        self._update_curation_roi_info()
        self._render_curation_view()
        merged_text = ", ".join(str(idx) for idx in result["merged_indices"])
        self.curation_status_var.set(
            f"Merged ROIs {merged_text} into ROI {result['new_index']}. "
            "The source ROIs were marked rejected; Finalize Into Pipeline when you're ready."
        )

    def _curation_jump_to_roi(self) -> None:
        if not self._curation_payload:
            raise ValueError("Load curation data first.")
        raw = self._vars["curation_jump_index"].get().strip()
        if not raw:
            raise ValueError("Enter an ROI index to jump to.")
        idx = int(raw)
        stat = np.asarray(self._curation_payload["stat"], dtype=object)
        if idx < 0 or idx >= len(stat):
            raise ValueError(f"ROI index out of range: {idx}")
        self._curation_selected_index = idx
        self._set_curation_filter("all")
        self._update_curation_roi_info()
        if self._vars["curation_zoom_to_selected"].get():
            self._render_curation_view()
        else:
            self._draw_curation_selection_overlay()

    def _curation_prev_roi(self) -> None:
        if not self._curation_payload:
            self._load_curation_data()
            return
        review_order = self._review_order_curation_indices()
        if not review_order:
            return
        if self._curation_selected_index not in review_order:
            self._curation_selected_index = review_order[0]
        else:
            pos = review_order.index(self._curation_selected_index)
            self._curation_selected_index = review_order[(pos - 1) % len(review_order)]
        self._update_curation_roi_info()
        if self._vars["curation_zoom_to_selected"].get():
            self._render_curation_view()
        else:
            self._draw_curation_selection_overlay()

    def _curation_next_roi(self) -> None:
        if not self._curation_payload:
            self._load_curation_data()
            return
        review_order = self._review_order_curation_indices()
        if not review_order:
            return
        if self._curation_selected_index not in review_order:
            self._curation_selected_index = review_order[0]
        else:
            pos = review_order.index(self._curation_selected_index)
            self._curation_selected_index = review_order[(pos + 1) % len(review_order)]
        self._update_curation_roi_info()
        if self._vars["curation_zoom_to_selected"].get():
            self._render_curation_view()
        else:
            self._draw_curation_selection_overlay()

    def _curation_mark_keep(self) -> None:
        self._curation_set_selected(True)

    def _curation_mark_reject(self) -> None:
        self._curation_set_selected(False)

    def _curation_set_selected(self, accepted_value: bool) -> None:
        if not self._curation_payload or self._curation_selected_index is None:
            raise ValueError("Select an ROI in the curation view first.")
        current_idx = self._curation_selected_index
        iscell = np.asarray(self._curation_payload["iscell"])
        if iscell.ndim == 2:
            iscell[current_idx, 0] = 1.0 if accepted_value else 0.0
        else:
            iscell[current_idx] = 1.0 if accepted_value else 0.0
        self._invalidate_curation_index_cache()
        self.curation_status_var.set(f"ROI {current_idx} marked as {'accepted' if accepted_value else 'rejected'}. Save ROI Labels to persist.")
        self._refresh_curation_roi_list()

        if self._vars["curation_auto_advance"].get():
            visible = self._visible_curation_indices()
            filter_mode = self._vars["curation_filter_mode"].get()
            if filter_mode == "all" and visible:
                if current_idx in visible:
                    pos = visible.index(current_idx)
                    self._curation_selected_index = visible[(pos + 1) % len(visible)]
                else:
                    self._curation_selected_index = visible[0]
            elif visible:
                self._curation_selected_index = visible[min(len(visible) - 1, 0)]
            else:
                self._curation_selected_index = None

        self._update_curation_roi_info()
        if self._vars["curation_zoom_to_selected"].get():
            self._render_curation_view()
        else:
            self._draw_curation_selection_overlay()

    def _curation_save_labels(self) -> None:
        if not self._curation_payload:
            raise ValueError("Load curation data first.")
        accepted = self._current_curation_accepted().astype(np.float32)
        path = self.controller.save_curation_iscell(accepted)
        self.curation_status_var.set(f"Saved ROI labels to {path}. You can now Finalize Into Pipeline.")
        self._refresh_view()

    def _bind_curation_shortcuts(self) -> None:
        self.bind_all("<KeyPress>", self._on_curation_keypress, add="+")

    def _curation_tab_active(self) -> bool:
        try:
            current = self.notebook.select()
            return str(self.tabs["ROI Curation"]) == current
        except Exception:
            return False

    def _reload_curation_if_active(self) -> None:
        if self._curation_tab_active():
            try:
                self._load_curation_data()
            except Exception:
                pass

    def _on_curation_keypress(self, event) -> None:
        if not self._curation_tab_active():
            return
        focus = self.focus_get()
        if focus is not None and isinstance(focus, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox)):
            return

        keysym = str(event.keysym)
        lower = str(event.keysym).lower()
        state = int(getattr(event, "state", 0))
        shift_pressed = bool(state & 0x1)

        try:
            if keysym == "Escape":
                if self._curation_manual_drag_center is not None:
                    self._curation_manual_drag_center = None
                    self._curation_manual_preview_diameter = float(self._vars["curation_manual_diameter"].get())
                    self.curation_status_var.set("Cancelled pending manual ROI placement.")
                    self._draw_curation_selection_overlay()
                elif self._vars["curation_manual_mode"].get():
                    self._curation_manual_hover_center = None
                    self._draw_curation_selection_overlay()
                else:
                    return
            elif lower == "k":
                self._curation_mark_keep()
            elif lower == "n":
                self._vars["curation_manual_mode"].set(not self._vars["curation_manual_mode"].get())
                mode = "ON" if self._vars["curation_manual_mode"].get() else "OFF"
                if not self._vars["curation_manual_mode"].get():
                    self._curation_manual_drag_center = None
                    self._curation_manual_hover_center = None
                    self._curation_manual_preview_diameter = None
                    self._draw_curation_selection_overlay()
                self.curation_status_var.set(
                    f"Manual ROI mode {mode}. Click and release on the projection to place a circular ROI."
                )
            elif lower == "r":
                self._curation_mark_reject()
            elif lower == "m":
                self._curation_merge_selected()
            elif lower == "s":
                self._curation_save_labels()
            elif keysym == "Delete":
                self._curation_delete_selected_manual_rois()
            elif lower == "a":
                self._set_curation_filter("accepted")
            elif lower == "j":
                self._set_curation_filter("rejected")
            elif lower == "d":
                self._set_curation_filter("all")
            elif keysym == "Left" and shift_pressed:
                self._curation_queue_prev_session()
            elif keysym == "Right" and shift_pressed:
                self._curation_queue_next_session()
            elif keysym == "Left":
                self._curation_prev_roi()
            elif keysym == "Right":
                self._curation_next_roi()
            else:
                return
        except Exception:
            return
        return "break"

    def _build_post_run_tab(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        outer = ttk.Frame(frame)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, bg=self.colors["bg"], highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        outer.bind("<Configure>", lambda e: canvas.itemconfigure(canvas_window, width=e.width - scrollbar.winfo_width()))

        self._vars["project_scan_root"] = tk.StringVar()
        self._vars["selected_snapshot_dir"] = tk.StringVar()
        self._vars["show_postrun_loading"] = tk.BooleanVar(value=True)
        self._vars["show_postrun_loader_status"] = tk.BooleanVar(value=False)
        self._vars["show_postrun_snapshots"] = tk.BooleanVar(value=True)
        self._vars["show_postrun_review"] = tk.BooleanVar(value=True)
        self._vars["show_postrun_exports"] = tk.BooleanVar(value=True)
        self._vars["show_postrun_project"] = tk.BooleanVar(value=True)

        blurb = (
            "This is the post-run recovery, export, and project-summary area.\n"
            "Use it to reopen prior results, inspect exports, and build higher-level summaries."
        )
        ttk.Label(content, text=blurb, justify="left").grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))

        ttk.Label(content, text="Session Loader").grid(row=1, column=0, columnspan=2, sticky="w", padx=(0, 12), pady=(0, 6))
        loading_box = ttk.Frame(content)
        loading_box.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 12), padx=(0, 12))
        loading_box.columnconfigure(1, weight=1)
        ttk.Label(loading_box, text="Session Path").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(loading_box, textvariable=self._vars["run_dir"]).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(loading_box, text="Browse", command=self._browse_run_dir).grid(row=0, column=2, padx=(8, 0), pady=6, sticky="w")
        load_actions = ttk.Frame(loading_box)
        load_actions.grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self._make_action_button(load_actions, "Load Session", self._load_from_session, background=True).grid(row=0, column=0, padx=(0, 8), pady=6)

        loader_status_toggle = ttk.Frame(loading_box)
        loader_status_toggle.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 6))
        ttk.Button(
            loader_status_toggle,
            text="Show Session Load Details",
            command=lambda: self._toggle_section("show_postrun_loader_status", loader_status_box, loader_status_toggle, "Session Load Details"),
        ).pack(anchor="w")
        loader_status_box = ttk.LabelFrame(loading_box, text="Session Load Details", padding=10)
        loader_status_box.grid(row=3, column=0, columnspan=3, sticky="ew")
        self.review_info = tk.StringVar(value="No run loaded yet.")
        ttk.Label(loader_status_box, textvariable=self.review_info, justify="left").grid(row=0, column=0, sticky="w")
        loader_status_box.grid_remove()

        snapshot_toggle = ttk.Frame(content)
        snapshot_toggle.grid(row=3, column=0, sticky="ew", pady=(0, 6), padx=(0, 12))
        ttk.Button(snapshot_toggle, text="Hide ROI Snapshots", command=lambda: self._toggle_section("show_postrun_snapshots", snapshot_box, snapshot_toggle, "ROI Snapshots")).pack(anchor="w")
        snapshot_box = ttk.LabelFrame(content, text="ROI Snapshots", padding=12)
        snapshot_box.grid(row=4, column=0, sticky="nsew", pady=(0, 12), padx=(0, 12))
        snapshot_box.columnconfigure(0, weight=1)
        snapshot_box.columnconfigure(1, weight=0)
        self.snapshot_listbox = tk.Listbox(snapshot_box, height=5, exportselection=False)
        self.snapshot_listbox.grid(row=0, column=0, sticky="nsew")
        snapshot_scroll = ttk.Scrollbar(snapshot_box, orient="vertical", command=self.snapshot_listbox.yview)
        snapshot_scroll.grid(row=0, column=1, sticky="ns")
        self.snapshot_listbox.configure(yscrollcommand=snapshot_scroll.set)
        self.snapshot_listbox.bind("<<ListboxSelect>>", lambda _e: self._sync_selected_snapshot_var())
        snapshot_actions = ttk.Frame(snapshot_box)
        snapshot_actions.grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self._make_action_button(snapshot_actions, "Refresh Snapshots", self._refresh_snapshot_list).grid(row=0, column=0, padx=(0, 8), pady=4)
        self._make_action_button(snapshot_actions, "Load Snapshot For Review", self._load_selected_snapshot, background=True).grid(row=0, column=1, padx=(0, 8), pady=4)
        self._make_action_button(snapshot_actions, "Use Active plane0", self._use_active_plane0).grid(row=0, column=2, padx=(0, 8), pady=4)
        self._make_action_button(snapshot_actions, "Promote Snapshot To Active", self._promote_selected_snapshot, background=True).grid(row=1, column=0, padx=(0, 8), pady=4)
        self._make_action_button(snapshot_actions, "Open Snapshot Folder", self._open_selected_snapshot_folder).grid(row=1, column=1, padx=(0, 8), pady=4)

        review_toggle = ttk.Frame(content)
        review_toggle.grid(row=3, column=1, sticky="ew", pady=(0, 6))
        ttk.Button(review_toggle, text="Hide Review / QC", command=lambda: self._toggle_section("show_postrun_review", review_box, review_toggle, "Review / QC")).pack(anchor="w")
        review_box = ttk.LabelFrame(content, text="Review / QC", padding=12)
        review_box.grid(row=4, column=1, sticky="nsew", pady=(0, 12))
        review_box.columnconfigure(0, weight=1)

        figure_box = ttk.LabelFrame(review_box, text="Figures", padding=10)
        figure_box.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        figure_actions = ttk.Frame(figure_box)
        figure_actions.grid(row=0, column=0, sticky="w")
        self._make_action_button(figure_actions, "Open Trace Preview Figure", self._open_trace_preview_figure).grid(row=0, column=0, padx=(0, 8), pady=6)
        self._make_action_button(figure_actions, "Open Mean Projection", self._open_mean_projection).grid(row=0, column=1, padx=(0, 8), pady=6)
        self._make_action_button(figure_actions, "Open Max Projection", self._open_max_projection).grid(row=0, column=2, padx=(0, 8), pady=6)
        self._make_action_button(figure_actions, "Open ROI Size Summary", self._open_roi_size_summary).grid(row=1, column=0, padx=(0, 8), pady=6)
        self._make_action_button(figure_actions, "Open Correlation Image", self._open_correlation_image).grid(row=1, column=1, padx=(0, 8), pady=6)
        self._make_action_button(figure_actions, "Open Static Overlay", self._open_static_overlay_image).grid(row=1, column=2, padx=(0, 8), pady=6)
        self._make_action_button(figure_actions, "Open Accepted Contours", self._open_accepted_contour_figure).grid(row=2, column=0, padx=(0, 8), pady=6)
        self._make_action_button(figure_actions, "Open Rejected Contours", self._open_rejected_contour_figure).grid(row=2, column=1, padx=(0, 8), pady=6)
        self._make_action_button(figure_actions, "Open Accepted ROI Fill Overlay", self._open_accepted_fill_overlay_image).grid(row=2, column=2, padx=(0, 8), pady=6)

        report_box = ttk.LabelFrame(review_box, text="Reports", padding=10)
        report_box.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        report_actions = ttk.Frame(report_box)
        report_actions.grid(row=0, column=0, sticky="w")
        self._make_action_button(report_actions, "Open Summary JSON", self._open_summary_json).grid(row=0, column=0, padx=(0, 8), pady=6)
        self._make_action_button(report_actions, "Open QC Report", self._open_qc_report).grid(row=0, column=1, padx=(0, 8), pady=6)

        review_actions = ttk.LabelFrame(review_box, text="Actions", padding=10)
        review_actions.grid(row=2, column=0, sticky="ew")
        action_buttons = ttk.Frame(review_actions)
        action_buttons.grid(row=0, column=0, sticky="w")
        self._make_action_button(action_buttons, "Launch Suite2p GUI", self._open_suite2p_gui, background=True).grid(row=0, column=0, padx=(0, 8), pady=6)
        self._make_action_button(action_buttons, "Inspect Component", self._inspect_component).grid(row=0, column=1, padx=(0, 8), pady=6)
        self._make_action_button(action_buttons, "Finalize ROI Edits", self._finalize_roi_edits, background=True).grid(row=0, column=2, padx=(0, 8), pady=6)
        self._make_action_button(action_buttons, "Save Curation Snapshot", self._save_curation_snapshot).grid(row=1, column=0, padx=(0, 8), pady=6)

        export_toggle = ttk.Frame(content)
        export_toggle.grid(row=5, column=0, sticky="ew", pady=(0, 6), padx=(0, 12))
        ttk.Button(export_toggle, text="Hide Export Tools", command=lambda: self._toggle_section("show_postrun_exports", export_box, export_toggle, "Export Tools")).pack(anchor="w")
        export_box = ttk.LabelFrame(content, text="Export Tools", padding=12)
        export_box.grid(row=6, column=0, sticky="nsew", pady=(0, 12), padx=(0, 12))
        export_box.columnconfigure(0, weight=1)

        build_box = ttk.LabelFrame(export_box, text="Build", padding=10)
        build_box.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        build_actions = ttk.Frame(build_box)
        build_actions.grid(row=0, column=0, sticky="w")
        self._make_action_button(build_actions, "Export Artifacts", self._export_artifacts).grid(row=0, column=0, padx=(0, 8), pady=6)
        self._make_action_button(build_actions, "Build Session Summary CSV", self._export_session_summary_csv).grid(row=0, column=1, padx=(0, 8), pady=6)
        self._make_action_button(build_actions, "Build Event Summary CSV", self._export_event_summary_csv).grid(row=0, column=2, padx=(0, 8), pady=6)

        trace_box = ttk.LabelFrame(export_box, text="Trace CSVs", padding=10)
        trace_box.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        trace_actions = ttk.Frame(trace_box)
        trace_actions.grid(row=0, column=0, sticky="w")
        self._make_action_button(trace_actions, "Open Accepted Trace CSV", self._open_accepted_trace_csv).grid(row=0, column=0, padx=(0, 8), pady=6)
        self._make_action_button(trace_actions, "Open Rejected Trace CSV", self._open_rejected_trace_csv).grid(row=0, column=1, padx=(0, 8), pady=6)

        output_box = ttk.LabelFrame(export_box, text="Package / Folders", padding=10)
        output_box.grid(row=2, column=0, sticky="ew")
        output_actions = ttk.Frame(output_box)
        output_actions.grid(row=0, column=0, sticky="w")
        self._make_action_button(output_actions, "Export Downstream Package", self._export_downstream_package).grid(row=0, column=0, padx=(0, 8), pady=6)
        self._make_action_button(output_actions, "Open Run Folder", self._open_run_folder).grid(row=0, column=1, padx=(0, 8), pady=6)
        self._make_action_button(output_actions, "Open plane0 Folder", self._open_plane_folder).grid(row=0, column=2, padx=(0, 8), pady=6)

        project_toggle = ttk.Frame(content)
        project_toggle.grid(row=5, column=1, sticky="ew", pady=(0, 6))
        ttk.Button(project_toggle, text="Hide Project Tools", command=lambda: self._toggle_section("show_postrun_project", project_box, project_toggle, "Project Tools")).pack(anchor="w")
        project_box = ttk.LabelFrame(content, text="Project Tools", padding=12)
        project_box.grid(row=6, column=1, sticky="nsew", pady=(0, 12))
        project_box.columnconfigure(1, weight=1)
        ttk.Label(
            project_box,
            text="Project / Parent Folder",
        ).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(project_box, textvariable=self._vars["project_scan_root"]).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(project_box, text="Browse", command=self._browse_project_scan_root).grid(row=0, column=2, padx=(8, 0), pady=6, sticky="w")
        ttk.Label(
            project_box,
            text="Point this to a parent folder that contains multiple exported Suite2p session summaries. "
                 "The project tools can backfill artifacts across sessions and aggregate session-level outputs into grouped workbook/plot/report views.",
            justify="left",
            wraplength=420,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 8))
        project_actions = ttk.Frame(project_box)
        project_actions.grid(row=2, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self._make_action_button(project_actions, "Export Artifacts Across Sessions", self._export_artifacts_across_sessions, background=True).grid(row=0, column=0, padx=(0, 8), pady=6)
        self._make_action_button(project_actions, "Export Summary CSVs Across Sessions", self._export_summaries_across_sessions, background=True).grid(row=0, column=1, padx=(0, 8), pady=6)
        self._make_action_button(project_actions, "Build Project Summary Workbook", self._build_project_summary_workbook, background=True).grid(row=0, column=2, padx=(0, 8), pady=6)
        self._make_action_button(project_actions, "Generate Project Summary Plots", self._generate_project_summary_plots, background=True).grid(row=1, column=0, padx=(0, 8), pady=6)
        self._make_action_button(project_actions, "Generate Project Summary Report", self._generate_project_summary_report, background=True).grid(row=1, column=1, padx=(0, 8), pady=6)
        self._make_action_button(project_actions, "Preview Retained Binary Cleanup", self._preview_retained_binary_cleanup).grid(row=2, column=0, padx=(0, 8), pady=6)
        self._make_action_button(project_actions, "Apply Retained Binary Cleanup", self._apply_retained_binary_cleanup, background=True).grid(row=2, column=1, padx=(0, 8), pady=6)

    def _build_portable_transfer_tab(self, frame: ttk.Frame) -> None:
        content = self._make_scrollable_tab(frame, columns=1)

        blurb = (
            "Use this tab to move processed session folders between your desktop master dataset and a portable drive.\n"
            "The transfer preserves each Session_### folder's relative path under the chosen root so the portable copy stays organized.\n"
            "This is meant for the Windows processing -> portable drive -> Mac curation -> import back workflow."
        )
        ttk.Label(content, text=blurb, justify="left", wraplength=1000).grid(row=0, column=0, sticky="ew", pady=(0, 12))

        paths_box = ttk.LabelFrame(content, text="Transfer Roots", padding=12)
        paths_box.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        paths_box.columnconfigure(1, weight=1)
        ttk.Label(paths_box, text="Desktop / Master Project Root").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(paths_box, textvariable=self._vars["transfer_master_root"]).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(paths_box, text="Browse", command=self._browse_transfer_master_root).grid(row=0, column=2, padx=(8, 0), pady=6, sticky="w")
        ttk.Label(paths_box, text="Portable Project Root").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(paths_box, textvariable=self._vars["transfer_portable_root"]).grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Button(paths_box, text="Browse", command=self._browse_transfer_portable_root).grid(row=1, column=2, padx=(8, 0), pady=6, sticky="w")

        options_box = ttk.LabelFrame(content, text="Transfer Options", padding=12)
        options_box.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        ttk.Checkbutton(
            options_box,
            text="Only include sessions that already have Suite2p outputs",
            variable=self._vars["transfer_require_outputs"],
        ).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Checkbutton(
            options_box,
            text="Overwrite destination session folders if they already exist",
            variable=self._vars["transfer_overwrite_existing"],
        ).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Checkbutton(
            options_box,
            text="Only include unfinished sessions (skip completed and no_soma)",
            variable=self._vars["transfer_unfinished_only"],
        ).grid(row=2, column=0, sticky="w", pady=4)

        export_box = ttk.LabelFrame(content, text="Desktop -> Portable", padding=12)
        export_box.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(
            export_box,
            text="Copy session folders from the desktop / master root to the portable drive. "
                 "This is the step to use before Mac-side ROI curation and downstream work.",
            justify="left",
            wraplength=1000,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        export_actions = ttk.Frame(export_box)
        export_actions.grid(row=1, column=0, sticky="w")
        self._make_action_button(export_actions, "Preview Export To Portable", self._preview_export_to_portable).grid(row=0, column=0, padx=(0, 8), pady=6)
        self._make_action_button(export_actions, "Export Sessions To Portable", self._export_sessions_to_portable, background=True).grid(row=0, column=1, padx=(0, 8), pady=6)

        import_box = ttk.LabelFrame(content, text="Portable -> Desktop", padding=12)
        import_box.grid(row=4, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(
            import_box,
            text="Update the desktop / master root from the portable drive after Mac-side curation or downstream work. "
                 "Completed sessions are moved off the portable drive after a successful update; unfinished sessions are copied back and kept on the portable drive.",
            justify="left",
            wraplength=1000,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        import_actions = ttk.Frame(import_box)
        import_actions.grid(row=1, column=0, sticky="w")
        self._make_action_button(import_actions, "Preview Import Back To Desktop", self._preview_import_from_portable).grid(row=0, column=0, padx=(0, 8), pady=6)
        self._make_action_button(import_actions, "Import Sessions Back To Desktop", self._import_sessions_from_portable, background=True).grid(row=0, column=1, padx=(0, 8), pady=6)

    def _build_help_tab(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        outer = ttk.Frame(frame)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, bg=self.colors["bg"], highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas)
        content.columnconfigure(0, weight=1)
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        outer.bind("<Configure>", lambda e: canvas.itemconfigure(canvas_window, width=e.width - scrollbar.winfo_width()))

        self._vars["show_help_reference"] = tk.BooleanVar(value=False)
        summary = (
            "Use the Help tab as reference when you need it, not as something you have to read every time.\n"
            "For day-to-day use: Run Manager launches work, Analysis Parameters sets the backend behavior, "
            "Post-Run reloads and exports sessions, and ROI Curation is where you refine the result."
        )
        ttk.Label(content, text=summary, justify="left", wraplength=1000).grid(row=0, column=0, sticky="ew", pady=(0, 12))
        help_toggle = ttk.Frame(content)
        help_toggle.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(help_toggle, text="Show Detailed Workflow Reference", command=lambda: self._toggle_section("show_help_reference", help_box, help_toggle, "Detailed Workflow Reference")).pack(anchor="w")
        help_box = ttk.LabelFrame(content, text="Detailed Workflow Reference", padding=12)
        help_box.grid(row=2, column=0, sticky="ew")
        text = (
            "Suite2p Workflow Reference\n\n"
            "Big Picture\n\n"
            "The Suite2p app now supports three connected modes of work:\n"
            "1. running Suite2p on a single session or a batch of sessions\n"
            "2. reviewing and exporting post-run artifacts\n"
            "3. revising ROI decisions later and folding those revisions back into the pipeline\n\n"
            "The workflow is no longer just:\n"
            "- run once\n"
            "- inspect once\n"
            "- done\n\n"
            "It is now closer to:\n"
            "- run\n"
            "- inspect\n"
            "- revise\n"
            "- snapshot\n"
            "- promote\n"
            "- regenerate outputs\n"
            "- continue downstream analysis\n\n"
            "Core Structure\n\n"
            "Main tabs:\n"
            "1. Run Manager\n"
            "2. Analysis Parameters\n"
            "3. Video Previews\n"
            "4. Post-Run\n\n"
            "Supporting tabs:\n"
            "- Notifications\n"
            "- Definitions\n"
            "- Help\n\n"
            "1. Run Manager\n\n"
            "This is the execution tab. It handles:\n"
            "- one-off session runs\n"
            "- queued batch runs\n"
            "- preflight checks\n"
            "- stop-after-current-session behavior\n"
            "- session queue management\n\n"
            "For a single run:\n"
            "- choose a Session Folder\n"
            "- set overwrite/archive behavior if needed\n"
            "- click Run From Session\n\n"
            "For a batch:\n"
            "- add sessions manually or from a parent folder\n"
            "- run preflight\n"
            "- launch sequential processing\n"
            "- track results in the batch panel\n\n"
            "This tab is for orchestration, not curation.\n\n"
            "2. Analysis Parameters\n\n"
            "This tab controls Suite2p parameters that affect:\n"
            "- registration\n"
            "- ROI detection\n"
            "- soma sizing behavior\n"
            "- thresholding\n"
            "- overlap rules\n"
            "- sparse mode / denoising style\n\n"
            "Examples:\n"
            "- diameter\n"
            "- threshold_scaling\n"
            "- nonrigid\n"
            "- maxregshift\n"
            "- max_overlap\n"
            "- soma_crop\n\n"
            "Two classes of change matter:\n"
            "1. analysis-parameter changes\n"
            "- require rerunning Suite2p\n"
            "2. curation changes\n"
            "- happen after the run\n"
            "- usually accept/reject or ROI-state adjustments\n"
            "- can propagate through exports without rerunning the full analysis\n\n"
            "3. Video Previews\n\n"
            "This tab controls:\n"
            "- Motion Preview\n"
            "- ROI Overlay Preview\n"
            "- 3-Panel Preview\n"
            "- Reconstruction Preview\n\n"
            "Each preview has independent settings for:\n"
            "- start frame\n"
            "- number of frames\n"
            "- fps\n"
            "- gain\n"
            "- q_min\n"
            "- q_max\n\n"
            "Current source behavior:\n"
            "- if the registered .bin exists, previews render from the original movie source\n"
            "- if the .bin is gone but motion_preview.mp4 exists, Overlay and 3-Panel can fall back to that saved preview source\n"
            "- Reconstruction Preview rebuilds from saved Suite2p outputs and does not need the .bin\n\n"
            "4. Post-Run\n\n"
            "This is the main post-analysis hub. It is organized around:\n"
            "- Load / Recovery for reopening past sessions\n"
            "- ROI Snapshots for revision checkpoints\n"
            "- Review / QC for figures, reports, and edit actions\n"
            "- Export Tools for rebuilding artifacts and summaries\n"
            "- Project Tools for across-session exports and summaries\n\n"
            "Run Outputs\n\n"
            "Durable Suite2p outputs live under:\n"
            "- Session_###\\analysis\\outputs\\suite2p\\plane0\n\n"
            "Prepared run metadata lives under:\n"
            "- Session_###\\suite2p_runs\\...\n\n"
            "So:\n"
            "- suite2p_runs = run history / configuration / metadata\n"
            "- analysis\\outputs\\suite2p\\plane0 = current active durable output set\n\n"
            "Post-Run Layout\n\n"
            "Load / Recovery:\n"
            "- enter a Session Path\n"
            "- click Load Session\n"
            "- this is the top section because it is the main way to reopen past work\n\n"
            "ROI Snapshots:\n"
            "- Refresh Snapshots\n"
            "- Load Snapshot For Review\n"
            "- Use Active plane0\n"
            "- Promote Snapshot To Active\n"
            "- Open Snapshot Folder\n\n"
            "Review / QC:\n"
            "- Figures\n"
            "- Reports\n"
            "- Actions\n\n"
            "Export Tools:\n"
            "- Build\n"
            "- Trace CSVs\n"
            "- Package / Folders\n\n"
            "Review Artifacts Available\n\n"
            "Static artifacts:\n"
            "- contours\n"
            "- accepted contours\n"
            "- rejected contours\n"
            "- mean projection\n"
            "- max projection\n"
            "- correlation image\n"
            "- static overlay image\n"
            "- trace preview\n"
            "- ROI size summary\n"
            "- QC summary\n"
            "- run summary JSON\n\n"
            "Trace exports:\n"
            "- accepted F traces CSV\n"
            "- accepted dF/F traces CSV\n"
            "- rejected F traces CSV\n"
            "- rejected dF/F traces CSV\n\n"
            "Video artifacts:\n"
            "- motion preview\n"
            "- ROI overlay preview\n"
            "- 3-panel preview\n"
            "- reconstruction preview\n\n"
            "What Reconstruction Preview Actually Is\n\n"
            "Reconstruction Preview is:\n"
            "- a synthetic activity visualization derived from Suite2p ROI footprints and traces\n\n"
            "It is not:\n"
            "- raw video\n"
            "- motion-corrected video\n"
            "- a true CNMF generative reconstruction\n\n"
            "In practice it shows:\n"
            "- where the accepted ROIs are\n"
            "- how active they are over time\n"
            "- overlaid onto a faint mean-image background\n\n"
            "ROI Curation Workflow\n\n"
            "Current curation model:\n"
            "- ROI Curation is now the main custom curation workspace\n"
            "- use Suite2p GUI only when you specifically want its native tools\n\n"
            "What ROI Curation can do:\n"
            "- click ROIs directly on the projection\n"
            "- Keep / Reject ROI\n"
            "- merge selected ROIs\n"
            "- manually add circular ROIs\n"
            "- delete manually added ROIs only\n"
            "- review one session at a time or work from a parent-folder queue\n"
            "- save curation labels and finalize them back into analysis outputs\n\n"
            "Main ROI Curation workflow:\n"
            "1. load the session in Post-Run with Load Session\n"
            "2. go to ROI Curation\n"
            "3. click Load Curation Data\n"
            "4. curate ROIs on the projection\n"
            "5. press S or click Save ROI Labels if you changed accept/reject state\n"
            "6. click Finalize Into Pipeline when you want the analysis-side outputs rebuilt\n"
            "7. optionally save a curation snapshot\n\n"
            "How to use your edited ROIs\n\n"
            "Save ROI Labels:\n"
            "- writes the current accepted/rejected state to iscell.npy\n"
            "- this is required after Keep / Reject editing\n\n"
            "Finalize Into Pipeline / Finalize ROI Edits:\n"
            "- rebuilds the analysis-facing outputs from the current active plane0\n"
            "- refreshes review artifacts\n"
            "- rebuilds accepted/rejected trace CSVs\n"
            "- rebuilds the session summary CSV\n"
            "- attempts the event summary CSV if metadata allows\n\n"
            "In practice, the edited ROIs start affecting downstream biology when you:\n"
            "1. save the ROI labels and/or perform merge/manual-add/delete edits\n"
            "2. finalize into the pipeline\n\n"
            "What changes biologically:\n"
            "- accepted cell count\n"
            "- which traces are included in accepted outputs\n"
            "- accepted/rejected overlays and summaries\n"
            "- merged/manual ROIs can also change the actual Suite2p ROI arrays\n\n"
            "What does not require snapshots:\n"
            "- normal editing\n"
            "- save ROI labels\n"
            "- finalize into pipeline\n\n"
            "Snapshots are optional version checkpoints only.\n\n"
            "Curation Snapshots\n\n"
            "Snapshots live under:\n"
            "- Session_###\\analysis\\outputs\\suite2p_curated_snapshots\\curated_snapshot_YYYYMMDD_HHMMSS\n\n"
            "A snapshot is:\n"
            "- a timestamped copy of the current plane0 output state\n\n"
            "Purpose:\n"
            "- preserve a curated ROI set\n"
            "- allow later comparison\n"
            "- restore or review an older edited state\n"
            "- keep version history without immediately overwriting the active state\n\n"
            "Snapshot Operations\n\n"
            "The ROI Snapshots section supports:\n"
            "- Refresh Snapshots\n"
            "- Load Snapshot For Review\n"
            "- Use Active plane0\n"
            "- Promote Snapshot To Active\n"
            "- Open Snapshot Folder\n\n"
            "Load Snapshot For Review means:\n"
            "- use this saved ROI state as the current review/analysis source\n"
            "- affect artifact opening, ROI-state-based analyses, and summary inspection\n"
            "- do not overwrite active outputs\n\n"
            "Use Active plane0 means:\n"
            "- go back to the current working output set\n"
            "- modify no files\n\n"
            "Promote Snapshot To Active means:\n"
            "- make this snapshot the working plane0 output state\n"
            "- back up the current active plane0 first\n"
            "- then copy the selected snapshot into active plane0\n\n"
            "How Curation Fits Into the Pipeline\n\n"
            "Pipeline A: initial run\n"
            "1. choose session\n"
            "2. set analysis parameters\n"
            "3. Run From Session\n"
            "4. review artifacts\n"
            "5. export if acceptable\n\n"
            "Pipeline B: custom ROI curation\n"
            "1. load the session in Post-Run with Load Session\n"
            "2. open ROI Curation\n"
            "3. Load Curation Data\n"
            "4. edit ROIs directly on the projection\n"
            "5. Save ROI Labels if needed\n"
            "6. Finalize Into Pipeline\n"
            "7. optionally Save Curation Snapshot\n\n"
            "Pipeline C: compare / revisit revisions later\n"
            "1. load the session in Post-Run with Load Session\n"
            "2. Refresh Snapshots\n"
            "3. Load Snapshot For Review\n"
            "4. inspect the older ROI state\n"
            "5. if desired, Promote Snapshot To Active\n"
            "6. regenerate/export from the promoted state\n\n"
            "What Gets Rebuilt After Revision\n\n"
            "Always rebuildable from saved outputs:\n"
            "- contours\n"
            "- accepted/rejected contours\n"
            "- mean projection\n"
            "- max projection\n"
            "- correlation image\n"
            "- static overlay image\n"
            "- trace preview\n"
            "- trace CSVs\n"
            "- QC summary\n"
            "- reconstruction preview\n"
            "- session/event summary CSVs\n\n"
            "Rebuildable as videos if suitable source exists:\n"
            "- overlay preview\n"
            "- 3-panel preview\n\n"
            "Best case:\n"
            "- temp .bin still exists\n\n"
            "Fallback case:\n"
            "- motion_preview.mp4 exists, so Overlay and 3-Panel can be rebuilt from the saved preview video\n\n"
            "Static Overlay\n\n"
            "Static Overlay is:\n"
            "- meanImg + current ROI overlay\n\n"
            "It gives you:\n"
            "- a durable visual confirmation of the current ROI set\n"
            "- something that can always be rebuilt after promotion from saved outputs alone\n\n"
            "Revised Video Behavior\n\n"
            "Overlay Preview:\n"
            "- ideal source: registered movie\n"
            "- fallback source: saved motion preview\n"
            "- current ROI set is redrawn onto it\n\n"
            "3-Panel Preview:\n"
            "- ideal source: raw + motion-corrected movie\n"
            "- fallback source: saved motion preview\n"
            "- the right panel is rebuilt from the current ROI mask\n\n"
            "Reconstruction Preview:\n"
            "- built from ROI traces and footprints\n"
            "- current ROI outlines are also drawn on it\n"
            "- no .bin required\n\n"
            "Recommended Real-World Workflow\n\n"
            "Phase 1: parameter tuning\n"
            "1. pick a representative session\n"
            "2. adjust biological parameters in Analysis Parameters\n"
            "3. run that one session\n"
            "4. inspect static overlay, contours, accepted/rejected contours, reconstruction preview, and 3-panel preview\n"
            "5. if detection is wrong, change parameters and rerun\n\n"
            "Phase 2: production running\n"
            "1. lock a good parameter set\n"
            "2. batch preflight\n"
            "3. run batch sequentially\n"
            "4. inspect outputs session by session as needed\n\n"
            "Phase 3: revision / curation\n"
            "1. load the finished session in Post-Run with Load Session\n"
            "2. open ROI Curation\n"
            "3. revise Keep / Reject, merge, or manual ROI edits\n"
            "4. Save ROI Labels if you changed accept/reject state\n"
            "5. Finalize Into Pipeline\n"
            "6. Save Curation Snapshot if you want a checkpoint\n\n"
            "Phase 4: version control of curation\n"
            "1. if you make another revision later, save another snapshot\n"
            "2. compare snapshots by loading them for review\n"
            "3. promote the preferred snapshot to active\n"
            "4. export updated artifacts/package\n\n"
            "Phase 5: downstream packaging\n"
            "1. export artifacts\n"
            "2. build session/event summary CSVs\n"
            "3. export downstream package\n"
            "4. optionally generate project summary workbook/plots/report\n\n"
            "What Is Necessary vs Optional\n\n"
            "Important operations:\n"
            "- Run From Session\n"
            "- Load Session\n"
            "- Load Curation Data\n"
            "- Save ROI Labels\n"
            "- Finalize Into Pipeline / Finalize ROI Edits\n"
            "- Save Curation Snapshot\n"
            "- Load Snapshot For Review\n"
            "- Promote Snapshot To Active\n"
            "- Export Artifacts\n\n"
            "Helpful but secondary:\n"
            "- manual preview rerenders\n"
            "- downstream package export\n"
            "- project-level summary tools\n"
            "- notifications\n"
            "- snapshot folder opening\n\n"
            "Best Mental Model\n\n"
            "- Analysis Parameters changes the algorithm\n"
            "- Run Manager executes the algorithm\n"
            "- Post-Run reopens, inspects, and exports the result\n"
            "- ROI Curation edits the result\n"
            "- Save ROI Labels writes accept/reject labels to disk\n"
            "- Finalize Into Pipeline rebuilds the analysis-facing outputs from the current edited plane0\n"
            "- Snapshots version the revisions\n"
            "- Promote chooses which revision becomes current\n"
            "- Export regenerates deliverables from that chosen state\n\n"
            "Where This Is Strong Now\n\n"
            "- rerunnable session/batch execution\n"
            "- richer review artifact generation\n"
            "- custom ROI curation inside the frontend\n"
            "- versioned curation snapshots\n"
            "- promotion of curated states into active outputs\n"
            "- partial preview regeneration even after temp cleanup\n"
            "- downstream packaging and summary generation\n\n"
            "Remaining Conceptual Limitation\n\n"
            "- ROI Curation is strongest for projection-based review, keep/reject, merge, and simple manual circular ROIs\n"
            "- it is not yet a full freeform ROI editor\n\n"
            "Recommended Standard Operating Pattern\n\n"
            "1. tune parameters on a representative session\n"
            "2. batch run with those settings\n"
            "3. use Post-Run as the curation hub\n"
            "4. save snapshots after meaningful ROI revisions\n"
            "5. promote only the curated state you actually want as final\n"
            "6. export downstream only after that promotion/finalization step\n"
        )
        label = tk.Label(
            help_box,
            text=text,
            justify="left",
            anchor="nw",
            bg=self.colors["bg"],
            fg=self.colors["text"],
            wraplength=1000,
            font=("TkDefaultFont", 11),
        )
        label.grid(row=0, column=0, sticky="nw")
        help_box.grid_remove()
        self._extend_help_tab(content, start_row=3)

    def _extend_help_tab(self, content: ttk.Frame, *, start_row: int) -> None:
        return None

    def _build_notifications_tab(self, frame: ttk.Frame) -> None:
        frame = self._make_scrollable_tab(frame, columns=3)
        frame.columnconfigure(1, weight=1)
        self._vars["notify_enabled"] = tk.BooleanVar(value=False)
        self._vars["notify_on_success"] = tk.BooleanVar(value=True)
        self._vars["notify_on_failure"] = tk.BooleanVar(value=True)
        self._vars["notify_per_batch_session"] = tk.BooleanVar(value=False)
        self._vars["smtp_host"] = tk.StringVar()
        self._vars["smtp_port"] = tk.StringVar(value="587")
        self._vars["sender_email"] = tk.StringVar()
        self._vars["recipient_email"] = tk.StringVar()
        self._vars["smtp_username"] = tk.StringVar()
        self._vars["smtp_password"] = tk.StringVar()
        self._vars["smtp_use_tls"] = tk.BooleanVar(value=True)
        self._vars["notification_settings_path"] = tk.StringVar()

        ttk.Label(frame, text="Enable email notifications for background Suite2p actions.").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        ttk.Checkbutton(frame, text="Enable email notifications", variable=self._vars["notify_enabled"]).grid(row=1, column=0, columnspan=2, sticky="w", pady=6)
        ttk.Checkbutton(frame, text="Notify on success", variable=self._vars["notify_on_success"]).grid(row=2, column=0, sticky="w", pady=6)
        ttk.Checkbutton(frame, text="Notify on failure", variable=self._vars["notify_on_failure"]).grid(row=2, column=1, sticky="w", pady=6)
        ttk.Checkbutton(frame, text="Notify per batch session", variable=self._vars["notify_per_batch_session"]).grid(row=3, column=0, columnspan=2, sticky="w", pady=6)

        rows = [
            ("SMTP host", "smtp_host"),
            ("SMTP port", "smtp_port"),
            ("Sender email", "sender_email"),
            ("Recipient email", "recipient_email"),
            ("SMTP username", "smtp_username"),
            ("SMTP password / app password", "smtp_password"),
            ("Settings file", "notification_settings_path"),
        ]
        start_row = 4
        for offset, (label, var_name) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=start_row + offset, column=0, sticky="w", padx=(0, 8), pady=6)
            show = "*" if "password" in var_name else None
            ttk.Entry(frame, textvariable=self._vars[var_name], show=show).grid(row=start_row + offset, column=1, sticky="ew", pady=6)

        ttk.Checkbutton(frame, text="Use TLS", variable=self._vars["smtp_use_tls"]).grid(row=11, column=0, sticky="w", pady=6)
        actions = ttk.Frame(frame)
        actions.grid(row=12, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self._make_action_button(actions, "Save Notification Settings", self._save_notification_settings).grid(row=0, column=0, padx=(0, 8))
        self._make_action_button(actions, "Send Test Email", self._send_test_email).grid(row=0, column=1, padx=(0, 8))

    def _make_action_button(self, parent: ttk.Frame, text: str, command, background: bool = False) -> ttk.Button:
        if background:
            btn = ttk.Button(parent, text=text, command=lambda c=command: self._run_action_background(text, c))
        else:
            btn = ttk.Button(parent, text=text, command=lambda c=command: self._run_action(text, c))
        self._action_buttons.append(btn)
        return btn

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for button in self._action_buttons:
            button.configure(state=state)
        try:
            self.batch_stop_btn.configure(state=(tk.NORMAL if busy else tk.DISABLED))
        except Exception:
            pass
        if busy:
            try:
                self.progress_bar.start(10)
            except Exception:
                pass
        else:
            try:
                self.progress_bar.stop()
            except Exception:
                pass

    def _run_action(self, label: str, func) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self.status_var.set(f"Running: {label}")
        try:
            func()
            self.status_var.set(f"Completed: {label}")
            self._refresh_view()
        except Exception as exc:
            self.status_var.set(f"Failed: {label}")
            self._log(traceback.format_exc().rstrip())
            messagebox.showerror(APP_NAME, str(exc))
        finally:
            self._set_busy(False)

    def _run_action_background(self, label: str, func) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self.status_var.set(f"Running: {label}")

        def worker() -> None:
            try:
                result = func()
                self._event_queue.put(("complete", (label, result)))
            except Exception as exc:
                self._event_queue.put(("error", (label, exc, traceback.format_exc())))

        threading.Thread(target=worker, daemon=True).start()

    def _process_event_queue(self) -> None:
        try:
            while True:
                event, payload = self._event_queue.get_nowait()
                if event == "log":
                    self._log(str(payload))
                elif event == "status":
                    self.status_var.set(str(payload))
                elif event == "batch_status":
                    session_path, status, detail = payload
                    self._update_batch_result_row(str(session_path), str(status))
                    self._sync_notification_state()
                    self.controller.notify_batch_session_result(str(session_path), str(status), str(detail))
                elif event == "complete":
                    label, _result = payload
                    self.status_var.set(f"Completed: {label}")
                    self._set_busy(False)
                    self._refresh_view()
                    self._sync_notification_state()
                    self.controller.notify_action_result(label, success=True)
                elif event == "error":
                    label, exc, tb = payload
                    self.status_var.set(f"Failed: {label}")
                    self._log(tb.rstrip())
                    self._set_busy(False)
                    self._sync_notification_state()
                    self.controller.notify_action_result(label, success=False, detail=str(exc))
                    messagebox.showerror(APP_NAME, str(exc))
        except queue.Empty:
            pass
        self.after(100, self._process_event_queue)

    def _log(self, message: str) -> None:
        self.log_text.insert("end", f"{message}\n")
        self.log_text.see("end")
        self.log_text.update_idletasks()

    def _threadsafe_log(self, message: str) -> None:
        self._event_queue.put(("log", message))

    def _threadsafe_status(self, message: str) -> None:
        self._event_queue.put(("status", message))

    def _browse_session(self) -> None:
        path = filedialog.askdirectory(title="Select session folder")
        if path:
            self._vars["session_path"].set(path)
            self._refresh_storage_paths()

    def _browse_run_dir(self) -> None:
        session_text = self._vars["session_path"].get().strip()
        if session_text:
            initial_path = self.controller.default_run_root(session_text)
        else:
            initial_path = RUNS_ROOT if RUNS_ROOT.exists() else Path.cwd()
        initial = str(initial_path)
        path = filedialog.askdirectory(title="Select Suite2p run directory", initialdir=initial)
        if path:
            self._vars["run_dir"].set(path)

    def _launch_acquisition_app(self) -> None:
        self.controller.launch_acquisition_app()

    def _import_acquisition_session(self) -> None:
        initial = self._vars["session_path"].get().strip() or str(SCIENTIFICA_ROOT)
        session_path = filedialog.askdirectory(title="Select acquisition Session_<NNN> folder", initialdir=initial)
        if not session_path:
            return
        self._apply_imported_acquisition_session(self.controller.import_acquisition_session(session_path))

    def _import_next_acquisition_session(self) -> None:
        self._apply_imported_acquisition_session(self.controller.import_next_acquisition_session())

    def _apply_imported_acquisition_session(self, payload: dict[str, str]) -> None:
        session_path = payload.get("session_path", "").strip()
        if session_path:
            self._vars["session_path"].set(session_path)
            self._vars["run_dir"].set(session_path)
            self._refresh_storage_paths()
        self._refresh_acquisition_summary()

    def _refresh_acquisition_summary(self) -> None:
        if not hasattr(self, "acquisition_summary_var"):
            return
        payload = self.state_obj.acquisition_metadata or {}
        if not payload:
            self.acquisition_summary_var.set("No acquisition session imported yet.")
            return
        session_path = str(payload.get("session_path", ""))
        session_name = str(payload.get("session_id", "")) or (Path(session_path).name if session_path else "")
        lines = [
            f"Imported Session: {session_name}",
            f"Session Path: {session_path}",
        ]
        if payload.get("project_name"):
            lines.append(f"Project: {payload.get('project_name', '')}")
        condition_bits = [str(payload.get("condition_subtype", "")).strip(), str(payload.get("condition_type", "")).strip()]
        condition_bits = [bit for bit in condition_bits if bit]
        if condition_bits:
            lines.append(f"Condition: {' / '.join(condition_bits)}")
        animal = str(payload.get("animal_id", "")).strip()
        slice_number = str(payload.get("slice_number", "")).strip()
        if animal or slice_number:
            slice_label = f"Slice_{slice_number}" if slice_number else ""
            lines.append(f"Animal / Slice: {' / '.join([bit for bit in [animal, slice_label] if bit])}")
        frame_rate = payload.get("acquired_frame_rate_hz") or payload.get("frame_rate_hz")
        if frame_rate:
            lines.append(f"Acquired Frame Rate: {frame_rate}")
        self.acquisition_summary_var.set("\n".join(lines))

    def _browse_output_dir(self) -> None:
        initial = self._vars["output_dir"].get().strip()
        if not initial:
            initial = self._vars["run_dir"].get().strip()
        if not initial:
            initial = str(Path.cwd())
        path = filedialog.askdirectory(title="Select Suite2p output folder", initialdir=initial)
        if path:
            self._vars["output_dir"].set(path)

    def _browse_parameter_preset(self) -> None:
        PRESETS_ROOT.mkdir(parents=True, exist_ok=True)
        path = filedialog.askopenfilename(
            title="Select Suite2p parameter preset",
            initialdir=str(PRESETS_ROOT),
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
        )
        if path:
            self._vars["parameter_preset_path"].set(path)

    def _browse_project_scan_root(self) -> None:
        path = filedialog.askdirectory(title="Select project / parent folder")
        if path:
            self._vars["project_scan_root"].set(path)

    def _browse_transfer_master_root(self) -> None:
        initial = self._vars["transfer_master_root"].get().strip() or self._vars["project_scan_root"].get().strip() or str(SCIENTIFICA_ROOT)
        path = filedialog.askdirectory(title="Select desktop / master root", initialdir=initial)
        if path:
            self._vars["transfer_master_root"].set(path)

    def _browse_transfer_portable_root(self) -> None:
        initial = self._vars["transfer_portable_root"].get().strip() or self._vars["transfer_master_root"].get().strip() or str(SCIENTIFICA_ROOT)
        path = filedialog.askdirectory(title="Select portable drive root", initialdir=initial)
        if path:
            self._vars["transfer_portable_root"].set(path)

    def _batch_add_session(self) -> None:
        path = filedialog.askdirectory(title="Select session folder to add to batch queue")
        if path:
            self._batch_append(path)

    def _batch_append(self, session_path: str) -> None:
        resolved = str(Path(session_path).expanduser().resolve())
        existing = list(self.batch_listbox.get(0, "end"))
        if resolved not in existing:
            self.batch_listbox.insert("end", resolved)
            self._sync_batch_results_table()

    def _batch_remove_selected(self) -> None:
        selection = list(self.batch_listbox.curselection())
        for idx in reversed(selection):
            self.batch_listbox.delete(idx)
        self._sync_batch_results_table()

    def _batch_clear(self) -> None:
        self.batch_listbox.delete(0, "end")
        self._sync_batch_results_table()

    def _batch_browse_parent(self) -> None:
        path = filedialog.askdirectory(title="Select project / parent folder to search for Session_### folders")
        if path:
            self._vars["batch_parent_root"].set(path)

    def _batch_load_from_parent(self) -> None:
        parent_text = self._vars["batch_parent_root"].get().strip()
        if not parent_text:
            raise ValueError("Choose a parent folder before loading sessions.")
        parent = Path(parent_text).expanduser().resolve()
        if not parent.exists():
            raise ValueError(f"Parent folder does not exist: {parent}")
        skip_no_soma = bool(self._vars["batch_skip_no_soma"].get())
        skip_completed = bool(self._vars["batch_skip_completed"].get())
        count = 0
        for candidate in sorted(parent.rglob("Session_*")):
            if self.controller.is_valid_session_folder(candidate):
                if skip_no_soma or skip_completed:
                    status_payload = self.controller.load_session_curation_status(candidate)
                    status = str(status_payload.get("status", "not_started") or "not_started").strip().lower()
                    if status == "no_soma" and skip_no_soma:
                        continue
                    if status == "completed" and skip_completed:
                        continue
                self._batch_append(str(candidate))
                count += 1
        filters: list[str] = []
        if skip_no_soma:
            filters.append("no_soma skipped")
        if skip_completed:
            filters.append("completed skipped")
        suffix = f" ({', '.join(filters)})" if filters else ""
        self._log(f"Added {count} sessions from project / parent folder: {parent}{suffix}")

    def _queued_sessions(self) -> list[str]:
        return list(self.batch_listbox.get(0, "end"))

    def _session_parts(self, session_path: str) -> tuple[str, str, str]:
        path = Path(session_path)
        session_name = path.name
        slice_name = path.parent.name if path.parent.name.lower().startswith("slice_") else ""
        animal_id = ""
        if slice_name and len(path.parents) >= 2:
            animal_id = path.parents[1].name
        return animal_id, slice_name, session_name or str(path)

    def _sync_batch_results_table(self) -> None:
        queued = self._queued_sessions()
        desired = {str(Path(session).expanduser().resolve()) for session in queued}
        existing_items = set(self.batch_tree.get_children(""))

        for item in list(existing_items):
            if item not in desired:
                self.batch_tree.delete(item)

        for session in queued:
            session_id = str(Path(session).expanduser().resolve())
            if session_id not in existing_items:
                animal_id, slice_name, session_name = self._session_parts(session_id)
                self.batch_tree.insert("", "end", iid=session_id, values=(animal_id, slice_name, session_name, "pending"))

    def _update_batch_result_row(self, session_path: str, status: str) -> None:
        session_id = str(Path(session_path).expanduser().resolve())
        animal_id, slice_name, session_name = self._session_parts(session_id)
        if self.batch_tree.exists(session_id):
            self.batch_tree.item(session_id, values=(animal_id, slice_name, session_name, status))
            return
        self.batch_tree.insert("", "end", iid=session_id, values=(animal_id, slice_name, session_name, status))

    def _threadsafe_batch_status(self, session_path: str, status: str, detail: str = "") -> None:
        self._event_queue.put(("batch_status", (session_path, status, detail)))

    def _execute_batch_preflight(self, *, require_all_clear: bool) -> dict:
        sessions = self._queued_sessions()
        if not sessions:
            raise ValueError("Add at least one session to the batch queue first.")
        report = self.controller.validate_batch_sessions(
            sessions,
            skip_existing=bool(self._vars["batch_skip_existing"].get()),
            archive_existing=bool(self._vars["batch_archive_existing"].get()),
        )
        report_path = self.controller.save_preflight_report(report)
        self.controller.log_preflight_report(report)
        if report.get("all_clear"):
            self._log(f"Batch preflight passed. Report saved to: {report_path}")
        else:
            self._log(f"Batch preflight found blocking issues. Report saved to: {report_path}")
            if require_all_clear:
                raise RuntimeError(
                    "Batch preflight found blocking issues. Fix the reported sessions before starting the batch."
                )
        return report

    def _run_batch_preflight(self) -> None:
        self._execute_batch_preflight(require_all_clear=False)

    def _run_batch_sequentially(self) -> None:
        sessions = self._queued_sessions()
        if not sessions:
            raise ValueError("Add at least one session to the batch queue first.")
        self._execute_batch_preflight(require_all_clear=True)
        self._batch_stop_requested = False
        self._vars["batch_stop_requested"].set(False)
        self._sync_batch_results_table()
        for session in sessions:
            self._update_batch_result_row(session, "pending")
        payload = self._current_parameter_payload()
        results = self.controller.run_batch_sessions(
            sessions,
            parameter_payload=payload,
            skip_existing=bool(self._vars["batch_skip_existing"].get()),
            archive_existing=bool(self._vars["batch_archive_existing"].get()),
            cleanup_temp_after_success=bool(self._vars["batch_cleanup_temp"].get()),
            should_stop=lambda: self._batch_stop_requested,
            on_session_status=self._threadsafe_batch_status,
        )
        completed = sum(1 for item in results if item.get("status") == "completed")
        skipped = sum(1 for item in results if item.get("status") == "skipped")
        cleaned = sum(1 for item in results if item.get("cleaned_temp"))
        self._log(f"Batch finished. Completed: {completed}. Skipped: {skipped}. Temp cleaned: {cleaned}.")

    def _request_batch_stop(self) -> None:
        self._batch_stop_requested = True
        self._vars["batch_stop_requested"].set(True)
        self._log("Batch stop requested. The app will stop after the current session finishes.")
        self.status_var.set("Stop requested: finishing current session before stopping batch.")

    def _prepare_run(self) -> None:
        run_dir = self.controller.prepare_session(
            self._vars["session_path"].get(),
            self._vars["run_name"].get(),
        )
        self._vars["run_dir"].set(str(run_dir))
        self._refresh_storage_paths()
        self._load_parameters_from_run()

    def _run_from_session(self) -> None:
        result = self.controller.run_session_pipeline_with_payload(
            self._vars["session_path"].get(),
            run_name=self._vars["run_name"].get(),
            parameter_payload=self._current_parameter_payload(),
            skip_existing=bool(self._vars["run_skip_existing"].get()),
            archive_existing=bool(self._vars["run_archive_existing"].get()),
            cleanup_temp_after_success=bool(self._vars["run_cleanup_temp"].get()),
        )
        if result.get("status") == "skipped":
            self._log(f"Run skipped for session: {result.get('session')}")
            return
        run_dir = result.get("run_dir", "")
        if run_dir:
            self._vars["run_dir"].set(str(run_dir))
        self._refresh_storage_paths()
        self._load_parameters_from_run()

    def _load_run_dir(self) -> None:
        target = self._vars["run_dir"].get().strip()
        loaded = self.controller.load_review_target(target)
        if self.state_obj.run_dir is not None:
            self._vars["run_dir"].set(str(self.state_obj.run_dir))
        if self.state_obj.plane_dir is not None:
            self._vars["output_dir"].set(str(self.state_obj.plane_dir))
        if self.state_obj.session_path is not None:
            self._vars["session_path"].set(str(self.state_obj.session_path))

    def _load_output_dir(self) -> None:
        plane_dir = self.controller.load_output_dir(self._vars["output_dir"].get())
        self._vars["output_dir"].set(str(plane_dir))
        if self.state_obj.run_dir is not None:
            self._vars["run_dir"].set(str(self.state_obj.run_dir))
        self._load_parameters_from_run()

    def _load_from_session(self) -> None:
        session_target = self._vars["run_dir"].get().strip() or self._vars["session_path"].get().strip()
        if not session_target:
            raise ValueError("Choose or enter a Session folder first.")
        plane_dir = self.controller.load_latest_from_session(session_target)
        self._vars["output_dir"].set(str(plane_dir))
        if self.state_obj.run_dir is not None:
            self._vars["run_dir"].set(str(self.state_obj.run_dir))
        if self.state_obj.session_path is not None:
            self._vars["session_path"].set(str(self.state_obj.session_path))
        self._refresh_acquisition_summary()
        self._refresh_storage_paths()
        self._load_parameters_from_run()

    def _load_parameters_from_run(self) -> None:
        self._ensure_loaded_run()
        payload = self.controller.parameter_payload()
        self._apply_parameter_payload(payload)

    def _load_parameter_defaults(self) -> None:
        payload = self.controller.default_parameter_payload()
        self._apply_parameter_payload(payload)

    def _apply_parameter_payload(self, payload: dict) -> None:
        db = payload["db"]
        ops = payload["ops"]
        self._vars["param_fs"].set(str(db.get("fs", "")))
        self._vars["param_tau"].set(str(db.get("tau", "")))
        self._vars["param_nplanes"].set(str(db.get("nplanes", "")))
        self._vars["param_nchannels"].set(str(db.get("nchannels", "")))
        self._vars["param_functional_chan"].set(str(db.get("functional_chan", "")))
        self._vars["param_do_registration"].set(bool(db.get("do_registration", True)))
        self._vars["param_nonrigid"].set(bool(ops.get("nonrigid", False)))
        self._vars["param_batch_size"].set(str(ops.get("batch_size", "")))
        self._vars["param_maxregshift"].set(str(ops.get("maxregshift", "")))
        self._vars["param_maxregshiftNR"].set(str(ops.get("maxregshiftNR", "")))
        self._vars["param_smooth_sigma"].set(str(ops.get("smooth_sigma", "")))
        self._vars["param_snr_thresh"].set(str(ops.get("snr_thresh", "")))
        self._vars["param_1Preg"].set(bool(ops.get("1Preg", False)))
        self._vars["param_pre_smooth"].set(str(ops.get("pre_smooth", "")))
        self._vars["param_spatial_taper"].set(str(ops.get("spatial_taper", "")))
        self._vars["param_roidetect"].set(bool(ops.get("roidetect", True)))
        self._vars["param_sparse_mode"].set(bool(ops.get("sparse_mode", True)))
        self._vars["param_anatomical_only"].set(str(ops.get("anatomical_only", "")))
        self._vars["param_denoise"].set(str(ops.get("denoise", "")))
        self._vars["param_diameter"].set(str(ops.get("diameter", "")))
        self._vars["param_threshold_scaling"].set(str(ops.get("threshold_scaling", "")))
        self._vars["param_spatial_scale"].set(str(ops.get("spatial_scale", "")))
        self._vars["param_max_overlap"].set(str(ops.get("max_overlap", "")))
        self._vars["param_soma_crop"].set(bool(ops.get("soma_crop", True)))
        self._vars["param_cellprob_threshold"].set(str(ops.get("cellprob_threshold", "")))
        self._apply_additional_parameter_payload(payload)

    def _apply_additional_parameter_payload(self, payload: dict) -> None:
        return

    def _current_parameter_payload(self) -> dict:
        def _to_number(value: str):
            value = value.strip()
            if value == "":
                return value
            if value.startswith("[") and value.endswith("]"):
                try:
                    parsed = __import__("json").loads(value)
                except Exception as exc:
                    raise ValueError(f"Could not parse list value: {value}") from exc
                if not isinstance(parsed, list):
                    raise ValueError(f"Expected a list value, got: {value}")
                return parsed
            if "." in value:
                return float(value)
            return int(value)

        payload = {
            "db": {
                "fs": _to_number(self._vars["param_fs"].get()),
                "tau": _to_number(self._vars["param_tau"].get()),
                "nplanes": _to_number(self._vars["param_nplanes"].get()),
                "nchannels": _to_number(self._vars["param_nchannels"].get()),
                "functional_chan": _to_number(self._vars["param_functional_chan"].get()),
                "do_registration": bool(self._vars["param_do_registration"].get()),
            },
            "ops": {
                "nonrigid": bool(self._vars["param_nonrigid"].get()),
                "batch_size": _to_number(self._vars["param_batch_size"].get()),
                "maxregshift": _to_number(self._vars["param_maxregshift"].get()),
                "maxregshiftNR": _to_number(self._vars["param_maxregshiftNR"].get()),
                "smooth_sigma": _to_number(self._vars["param_smooth_sigma"].get()),
                "snr_thresh": _to_number(self._vars["param_snr_thresh"].get()),
                "1Preg": bool(self._vars["param_1Preg"].get()),
                "pre_smooth": _to_number(self._vars["param_pre_smooth"].get()),
                "spatial_taper": _to_number(self._vars["param_spatial_taper"].get()),
                "roidetect": bool(self._vars["param_roidetect"].get()),
                "sparse_mode": bool(self._vars["param_sparse_mode"].get()),
                "anatomical_only": _to_number(self._vars["param_anatomical_only"].get()),
                "denoise": _to_number(self._vars["param_denoise"].get()),
                "diameter": _to_number(self._vars["param_diameter"].get()),
                "threshold_scaling": _to_number(self._vars["param_threshold_scaling"].get()),
                "spatial_scale": _to_number(self._vars["param_spatial_scale"].get()),
                "max_overlap": _to_number(self._vars["param_max_overlap"].get()),
                "soma_crop": bool(self._vars["param_soma_crop"].get()),
                "cellprob_threshold": _to_number(self._vars["param_cellprob_threshold"].get()),
            },
        }
        self._extend_parameter_payload(payload, to_number=_to_number)
        return payload

    def _extend_parameter_payload(self, payload: dict, *, to_number) -> None:
        return

    def _load_parameter_preset(self) -> None:
        payload = self.controller.load_parameter_preset(self._vars["parameter_preset_path"].get())
        self._apply_parameter_payload(payload)

    def _save_parameter_preset(self) -> None:
        path = self.controller.save_parameter_preset(
            self._vars["parameter_preset_path"].get(),
            self._current_parameter_payload(),
        )
        self._vars["parameter_preset_path"].set(str(path))

    def _load_notification_vars(self) -> None:
        cfg = self.state_obj.notifications
        self._vars["notify_enabled"].set(cfg.enabled)
        self._vars["notify_on_success"].set(cfg.notify_on_success)
        self._vars["notify_on_failure"].set(cfg.notify_on_failure)
        self._vars["notify_per_batch_session"].set(cfg.notify_per_batch_session)
        self._vars["smtp_host"].set(cfg.smtp_host)
        self._vars["smtp_port"].set(str(cfg.smtp_port))
        self._vars["sender_email"].set(cfg.sender_email)
        self._vars["recipient_email"].set(cfg.recipient_email)
        self._vars["smtp_username"].set(cfg.username)
        self._vars["smtp_password"].set(cfg.password)
        self._vars["smtp_use_tls"].set(cfg.use_tls)
        self._vars["notification_settings_path"].set(cfg.settings_path)

    def _sync_notification_state(self) -> None:
        cfg = self.state_obj.notifications
        cfg.enabled = bool(self._vars["notify_enabled"].get())
        cfg.notify_on_success = bool(self._vars["notify_on_success"].get())
        cfg.notify_on_failure = bool(self._vars["notify_on_failure"].get())
        cfg.notify_per_batch_session = bool(self._vars["notify_per_batch_session"].get())
        cfg.smtp_host = self._vars["smtp_host"].get().strip()
        cfg.smtp_port = int(self._vars["smtp_port"].get().strip() or "587")
        cfg.sender_email = self._vars["sender_email"].get().strip()
        cfg.recipient_email = self._vars["recipient_email"].get().strip()
        cfg.username = self._vars["smtp_username"].get().strip()
        cfg.password = self._vars["smtp_password"].get()
        cfg.use_tls = bool(self._vars["smtp_use_tls"].get())
        cfg.settings_path = self._vars["notification_settings_path"].get().strip()

    def _save_notification_settings(self) -> None:
        self._sync_notification_state()
        path = self.controller.save_notification_settings()
        self._vars["notification_settings_path"].set(str(path))

    def _send_test_email(self) -> None:
        self._sync_notification_state()
        self.controller.send_test_email()

    def _save_parameters_to_run(self) -> None:
        self._ensure_loaded_run()
        self.controller.save_parameter_payload(self._current_parameter_payload())
        self._refresh_storage_paths()

    def _open_runs_root(self) -> None:
        session_text = self._vars["session_path"].get().strip()
        if session_text:
            target = self.controller.default_run_root(session_text)
        else:
            target = RUNS_ROOT
        target.mkdir(parents=True, exist_ok=True)
        self.controller._open_path(target)

    def _run_suite2p(self) -> None:
        self._ensure_loaded_run()
        self.controller.run_suite2p()

    def _export_artifacts(self) -> None:
        self._ensure_loaded_run()
        self.controller.export_artifacts()

    def _export_preview_artifacts(self) -> None:
        self._ensure_loaded_run()
        self.controller.export_artifacts(self._preview_options("motion"), only="motion")
        self.controller.export_artifacts(self._preview_options("overlay"), only="overlay")
        self.controller.export_artifacts(self._preview_options("three_panel"), only="three_panel")
        self.controller.export_artifacts(self._preview_options("reconstruction"), only="reconstruction")

    def _preview_options(self, prefix: str) -> dict:
        def _to_number(value: str):
            value = value.strip()
            if value == "":
                return None
            if "." in value:
                return float(value)
            return int(value)

        return {
            "start_frame": _to_number(self._vars[f"{prefix}_start_frame"].get()),
            "num_frames": _to_number(self._vars[f"{prefix}_num_frames"].get()),
            "fps": _to_number(self._vars[f"{prefix}_fps"].get()),
            "gain": _to_number(self._vars[f"{prefix}_gain"].get()),
            "q_min": _to_number(self._vars[f"{prefix}_q_min"].get()),
            "q_max": _to_number(self._vars[f"{prefix}_q_max"].get()),
        }

    def _rerender_motion_preview(self) -> None:
        self._ensure_loaded_run()
        self.controller.export_artifacts(self._preview_options("motion"), only="motion")

    def _rerender_overlay_preview(self) -> None:
        self._ensure_loaded_run()
        self.controller.export_artifacts(self._preview_options("overlay"), only="overlay")

    def _rerender_three_panel_preview(self) -> None:
        self._ensure_loaded_run()
        self.controller.export_artifacts(self._preview_options("three_panel"), only="three_panel")

    def _rerender_reconstruction_preview(self) -> None:
        self._ensure_loaded_run()
        self.controller.export_artifacts(self._preview_options("reconstruction"), only="reconstruction")

    def _open_run_folder(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_run_folder()

    def _open_plane_folder(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_plane_folder()

    def _open_suite2p_gui(self) -> None:
        self.controller.open_suite2p_gui()

    def _open_motion_preview(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_motion_preview()

    def _open_overlay_preview(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_overlay_preview()

    def _open_three_panel_preview(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_three_panel_preview()

    def _open_reconstruction_preview(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_reconstruction_preview()

    def _open_contour_figure(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_contour_figure()

    def _open_accepted_contour_figure(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_accepted_contour_figure()

    def _open_rejected_contour_figure(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_rejected_contour_figure()

    def _open_mean_projection(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_mean_projection()

    def _open_max_projection(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_max_projection()

    def _open_correlation_image(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_correlation_image()

    def _open_static_overlay_image(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_static_overlay_image()

    def _open_accepted_fill_overlay_image(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_accepted_fill_overlay_image()

    def _open_trace_preview_figure(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_trace_preview_figure()

    def _open_roi_size_summary(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_roi_size_summary()

    def _open_qc_report(self) -> None:
        self._ensure_loaded_run()
        self.controller.open_qc_report()

    def _open_summary_json(self) -> None:
        self._ensure_loaded_run()
        self.controller._open_path(self.controller.summary_path())

    def _export_session_summary_csv(self) -> None:
        self._ensure_loaded_run()
        path = self.controller.export_session_summary_csv()
        self.controller._open_path(path)

    def _export_event_summary_csv(self) -> None:
        self._ensure_loaded_run()
        path = self.controller.export_event_summary_csv()
        self.controller._open_path(path)

    def _open_accepted_trace_csv(self) -> None:
        self._ensure_loaded_run()
        self.controller._open_path(self.controller.plane_dir() / "suite2p_accepted_dff_traces.csv")

    def _open_rejected_trace_csv(self) -> None:
        self._ensure_loaded_run()
        self.controller._open_path(self.controller.plane_dir() / "suite2p_rejected_dff_traces.csv")

    def _export_downstream_package(self) -> None:
        self._ensure_loaded_run()
        path = self.controller.export_downstream_package()
        self.controller._open_path(path)

    def _inspect_component(self) -> None:
        self._ensure_loaded_run()
        component_index = simpledialog.askinteger(APP_NAME, "Component index to inspect:", minvalue=0)
        if component_index is None:
            return
        path = self.controller.inspect_component(component_index)
        self.controller._open_path(path)

    def _finalize_roi_edits(self) -> None:
        self._ensure_loaded_run()
        self.controller.finalize_roi_edits()
        self._reload_curation_if_active()

    def _save_curation_snapshot(self) -> None:
        self._ensure_loaded_run()
        path = self.controller.save_curation_snapshot()
        self._refresh_snapshot_list()
        self._reload_curation_if_active()
        self.controller._open_path(path)

    def _sync_selected_snapshot_var(self, *, from_curation: bool = False) -> None:
        listbox_name = "curation_snapshot_listbox" if from_curation and hasattr(self, "curation_snapshot_listbox") else "snapshot_listbox"
        if not hasattr(self, listbox_name):
            return
        listbox = getattr(self, listbox_name)
        selection = listbox.curselection()
        if not selection:
            self._vars["selected_snapshot_dir"].set("")
            return
        self._vars["selected_snapshot_dir"].set(listbox.get(selection[0]))

    def _selected_snapshot_path(self) -> Path:
        self._ensure_loaded_run()
        if not self._vars["selected_snapshot_dir"].get().strip():
            self._refresh_snapshot_list()
        snapshot_dir = self._vars["selected_snapshot_dir"].get().strip()
        if not snapshot_dir:
            raise ValueError("No curation snapshots were found for the loaded session.")
        return Path(snapshot_dir).resolve()

    def _refresh_snapshot_list(self, *, for_curation: bool = False) -> None:
        try:
            self._ensure_loaded_run()
        except Exception:
            if hasattr(self, "snapshot_listbox"):
                self.snapshot_listbox.delete(0, tk.END)
            if hasattr(self, "curation_snapshot_listbox"):
                self.curation_snapshot_listbox.delete(0, tk.END)
            self._vars["selected_snapshot_dir"].set("")
            return
        snapshots = self.controller.list_curation_snapshots()
        current = self._vars["selected_snapshot_dir"].get().strip()
        active_snapshot = str(self.state_obj.snapshot_dir) if self.state_obj.snapshot_dir is not None else ""
        targets = []
        if hasattr(self, "snapshot_listbox"):
            targets.append(self.snapshot_listbox)
        if hasattr(self, "curation_snapshot_listbox"):
            targets.append(self.curation_snapshot_listbox)
        for listbox in targets:
            listbox.delete(0, tk.END)
        chosen_index = None
        for idx, path in enumerate(snapshots):
            path_str = str(path)
            for listbox in targets:
                listbox.insert(tk.END, path_str)
            if current and path_str == current:
                chosen_index = idx
            elif not current and active_snapshot and path_str == active_snapshot:
                chosen_index = idx
        if chosen_index is not None:
            for listbox in targets:
                listbox.selection_set(chosen_index)
                listbox.see(chosen_index)
                self._vars["selected_snapshot_dir"].set(listbox.get(chosen_index))
        elif snapshots:
            for listbox in targets:
                listbox.selection_set(0)
                listbox.see(0)
            self._vars["selected_snapshot_dir"].set(str(snapshots[0]))
        else:
            self._vars["selected_snapshot_dir"].set("")

    def _load_selected_snapshot(self) -> None:
        path = self.controller.load_curation_snapshot(self._selected_snapshot_path())
        self._vars["output_dir"].set(str(path))
        self._refresh_snapshot_list()
        self._reload_curation_if_active()

    def _use_active_plane0(self) -> None:
        self._ensure_loaded_run()
        path = self.controller.use_active_plane0()
        self._vars["output_dir"].set(str(path))
        self._refresh_snapshot_list()
        self._reload_curation_if_active()

    def _promote_selected_snapshot(self) -> None:
        path = self.controller.promote_curation_snapshot(self._selected_snapshot_path())
        self._vars["output_dir"].set(str(path))
        self._refresh_snapshot_list()
        self._reload_curation_if_active()

    def _open_selected_snapshot_folder(self) -> None:
        path = self._selected_snapshot_path()
        self.controller._open_path(path)

    def _build_project_summary_workbook(self) -> None:
        root = self._vars["project_scan_root"].get().strip()
        if not root:
            raise ValueError("Set a Project / Parent Folder first.")
        path = self.controller.build_project_summary_workbook(root)
        self.controller._open_path(path)

    def _export_artifacts_across_sessions(self) -> None:
        root = self._vars["project_scan_root"].get().strip()
        if not root:
            raise ValueError("Set a Project / Parent Folder first.")
        self.controller.export_artifacts_across_sessions(root)

    def _export_summaries_across_sessions(self) -> None:
        root = self._vars["project_scan_root"].get().strip()
        if not root:
            raise ValueError("Set a Project / Parent Folder first.")
        result = self.controller.export_summaries_across_sessions(root, include_event=True)
        processed = len(result.get("processed_sessions", []))
        skipped = len(result.get("skipped_sessions", []))
        failed = len(result.get("failed_sessions", []))
        self.status_var.set(
            f"Exported summary CSVs across sessions. Processed: {processed}. Skipped: {skipped}. Failed: {failed}."
        )

    def _generate_project_summary_plots(self) -> None:
        root = self._vars["project_scan_root"].get().strip()
        if not root:
            raise ValueError("Set a Project / Parent Folder first.")
        paths = self.controller.generate_project_summary_plots(root)
        if paths:
            self.controller._open_path(paths[0])

    def _generate_project_summary_report(self) -> None:
        root = self._vars["project_scan_root"].get().strip()
        if not root:
            raise ValueError("Set a Project / Parent Folder first.")
        path = self.controller.generate_project_summary_report(root)
        self.controller._open_path(path)

    def _preview_retained_binary_cleanup(self) -> None:
        root = self._vars["project_scan_root"].get().strip()
        if not root:
            raise ValueError("Set a Project / Parent Folder first.")
        report = self.controller.cleanup_retained_binaries_after_curation(root, apply=False)
        eligible = int(report.get("deleted_run_dir_count", 0))
        self.status_var.set(f"Previewed retained binary cleanup. Eligible retained run dirs: {eligible}.")

    def _apply_retained_binary_cleanup(self) -> None:
        root = self._vars["project_scan_root"].get().strip()
        if not root:
            raise ValueError("Set a Project / Parent Folder first.")
        if not messagebox.askyesno(
            "Apply Retained Binary Cleanup",
            "Delete retained binary folders for sessions marked completed under the selected Project / Parent Folder?",
        ):
            return
        report = self.controller.cleanup_retained_binaries_after_curation(root, apply=True)
        deleted = int(report.get("deleted_run_dir_count", 0))
        self.status_var.set(f"Applied retained binary cleanup. Deleted retained run dirs: {deleted}.")

    def _transfer_roots(self) -> tuple[str, str]:
        source_root = self._vars["transfer_master_root"].get().strip()
        portable_root = self._vars["transfer_portable_root"].get().strip()
        if not source_root:
            raise ValueError("Set the Desktop / Master Root first.")
        if not portable_root:
            raise ValueError("Set the Portable Drive Root first.")
        return source_root, portable_root

    def _preview_export_to_portable(self) -> None:
        master_root, portable_root = self._transfer_roots()
        report = self.controller.preview_session_transfer(
            master_root,
            portable_root,
            require_outputs=bool(self._vars["transfer_require_outputs"].get()),
            unfinished_only=bool(self._vars["transfer_unfinished_only"].get()),
        )
        count = int(report.get("session_count", 0))
        existing = int(report.get("existing_destination_count", 0))
        self.status_var.set(f"Previewed export to portable. Sessions: {count}. Existing destination folders: {existing}.")
        messagebox.showinfo(
            "Preview Export To Portable",
            f"Sessions to copy: {count}\n"
            f"Destination folders that already exist: {existing}\n\n"
            f"Source: {report.get('source_root')}\n"
            f"Target: {report.get('target_root')}",
        )

    def _export_sessions_to_portable(self) -> None:
        master_root, portable_root = self._transfer_roots()
        overwrite = bool(self._vars["transfer_overwrite_existing"].get())
        if overwrite and not messagebox.askyesno(
            "Export Sessions To Portable",
            "Overwrite any existing session folders on the portable drive that match the copied sessions?",
        ):
            return
        result = self.controller.transfer_sessions_between_roots(
            master_root,
            portable_root,
            overwrite_existing=overwrite,
            require_outputs=bool(self._vars["transfer_require_outputs"].get()),
            unfinished_only=bool(self._vars["transfer_unfinished_only"].get()),
        )
        copied = len(result.get("copied_sessions", []))
        skipped = len(result.get("skipped_sessions", []))
        failed = len(result.get("failed_sessions", []))
        self.status_var.set(f"Exported sessions to portable. Copied: {copied}. Skipped: {skipped}. Failed: {failed}.")

    def _preview_import_from_portable(self) -> None:
        master_root, portable_root = self._transfer_roots()
        report = self.controller.preview_session_transfer(
            portable_root,
            master_root,
            require_outputs=bool(self._vars["transfer_require_outputs"].get()),
            unfinished_only=False,
        )
        count = int(report.get("session_count", 0))
        existing = int(report.get("existing_destination_count", 0))
        self.status_var.set(f"Previewed import from portable. Sessions: {count}. Existing destination folders: {existing}.")
        messagebox.showinfo(
            "Preview Import Back To Desktop",
            f"Sessions to copy: {count}\n"
            f"Destination folders that already exist: {existing}\n\n"
            f"Source: {report.get('source_root')}\n"
            f"Target: {report.get('target_root')}",
        )

    def _import_sessions_from_portable(self) -> None:
        master_root, portable_root = self._transfer_roots()
        overwrite = bool(self._vars["transfer_overwrite_existing"].get())
        if overwrite and not messagebox.askyesno(
            "Import Sessions Back To Desktop",
            "Overwrite any existing session folders under the desktop / master root that match the imported sessions?",
        ):
            return
        result = self.controller.transfer_sessions_between_roots(
            portable_root,
            master_root,
            overwrite_existing=overwrite,
            require_outputs=bool(self._vars["transfer_require_outputs"].get()),
            unfinished_only=False,
            move_completed_after_copy=True,
        )
        copied = len(result.get("copied_sessions", []))
        moved = len(result.get("moved_sessions", []))
        skipped = len(result.get("skipped_sessions", []))
        failed = len(result.get("failed_sessions", []))
        self.status_var.set(f"Updated desktop from portable. Copied: {copied}. Moved completed: {moved}. Skipped: {skipped}. Failed: {failed}.")

    def _ensure_loaded_run(self) -> None:
        run_dir = self._vars["run_dir"].get().strip()
        if run_dir:
            try:
                resolved_run = str(Path(run_dir).resolve())
            except Exception:
                resolved_run = run_dir
            current_run = str(self.state_obj.run_dir) if self.state_obj.run_dir is not None else ""
            current_plane = str(self.state_obj.plane_dir) if self.state_obj.plane_dir is not None else ""
            if self.state_obj.run_dir is None or (resolved_run != current_run and resolved_run != current_plane):
                self.controller.load_review_target(run_dir)
                return
        output_dir = self._vars["output_dir"].get().strip()
        if output_dir and self.state_obj.plane_dir is None:
            self.controller.load_output_dir(output_dir)

    def _refresh_view(self) -> None:
        if self.state_obj.run_dir is not None:
            self._vars["run_dir"].set(str(self.state_obj.run_dir))
        if self.state_obj.plane_dir is not None:
            self._vars["output_dir"].set(str(self.state_obj.plane_dir))
        if self.state_obj.session_path is not None:
            self._vars["session_path"].set(str(self.state_obj.session_path))
        self._refresh_storage_paths()
        lines = []
        if self.state_obj.run_dir is not None:
            lines.append(f"Run dir: {self.state_obj.run_dir}")
        if self.state_obj.plane_dir is not None:
            lines.append(f"plane0: {self.state_obj.plane_dir}")
        if self.state_obj.snapshot_dir is not None:
            lines.append("Source: loaded curation snapshot")
            lines.append(f"Snapshot: {self.state_obj.snapshot_dir}")
        elif self.state_obj.plane_dir is not None:
            lines.append("Source: active plane0")
        try:
            artifacts = self.controller.generated_artifacts() if self.state_obj.run_dir else {}
            if self.state_obj.run_dir is None and self.state_obj.plane_dir is not None:
                artifacts = self.controller.generated_artifacts()
            for label, path in artifacts.items():
                lines.append(f"{label}: {'ready' if path.exists() else 'not ready'}")
        except Exception as exc:
            lines.append(str(exc))
        self.review_info.set("\n".join(lines) if lines else "No run loaded yet.")
        if hasattr(self, "snapshot_listbox") and self.state_obj.run_dir is not None:
            try:
                self._refresh_snapshot_list()
            except Exception:
                pass

    def _refresh_storage_paths(self) -> None:
        session_text = self._vars["session_path"].get().strip()
        self._vars["input_root"].set(session_text)

        output_root = ""
        if session_text:
            try:
                output_root = str(self.controller.default_output_root(session_text))
            except Exception:
                output_root = ""
        run_dir_text = self._vars["run_dir"].get().strip()
        if run_dir_text:
            db_path = Path(run_dir_text) / "suite2p_db.json"
            if db_path.exists():
                try:
                    import json

                    db = json.loads(db_path.read_text(encoding="utf-8"))
                    db_output_root = str(db.get("save_path0", "")).strip()
                    if db_output_root:
                        output_root = db_output_root
                except Exception:
                    pass
        if not output_root and self.state_obj.plane_dir is not None:
            output_root = str(self.state_obj.plane_dir.parent.parent)
        self._vars["output_root"].set(output_root)

    def _toggle_status_log(self) -> None:
        visible = bool(self._vars["show_status_log"].get())
        if visible:
            self.status_log_container.pack_forget()
            self._vars["show_status_log"].set(False)
            self.status_toggle_btn.configure(text="Show Progress / Status")
        else:
            self.status_log_container.pack(fill="x", expand=False, pady=(8, 0))
            self._vars["show_status_log"].set(True)
            self.status_toggle_btn.configure(text="Hide Progress / Status")

    def _toggle_section(self, var_name: str, section: ttk.LabelFrame, toggle_row: tk.Widget, label: str) -> None:
        visible = bool(self._vars[var_name].get())
        if isinstance(toggle_row, ttk.Button):
            button = toggle_row
        else:
            children = toggle_row.winfo_children()
            if not children:
                return
            button = children[0]
        if visible:
            section.grid_remove()
            self._vars[var_name].set(False)
            button.configure(text=f"Show {label}")
        else:
            section.grid()
            self._vars[var_name].set(True)
            button.configure(text=f"Hide {label}")

    def _bind_global_scrollwheel(self) -> None:
        self.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.bind_all("<Shift-MouseWheel>", self._on_shift_mousewheel, add="+")
        self.bind_all("<Button-4>", self._on_linux_mousewheel, add="+")
        self.bind_all("<Button-5>", self._on_linux_mousewheel, add="+")
        self.bind_all("<Shift-Button-4>", self._on_linux_shift_mousewheel, add="+")
        self.bind_all("<Shift-Button-5>", self._on_linux_shift_mousewheel, add="+")

    def _widget_under_pointer(self, event: tk.Event) -> tk.Misc | None:
        widget = self.winfo_containing(event.x_root, event.y_root)
        if widget is None:
            widget = event.widget
        return widget

    def _scrollable_ancestor(self, widget: tk.Misc | None, *, horizontal: bool = False) -> tk.Misc | None:
        axis_method = "xview_scroll" if horizontal else "yview_scroll"
        current = widget
        while current is not None:
            if hasattr(current, axis_method):
                return current
            parent_name = current.winfo_parent()
            if not parent_name:
                break
            try:
                current = current.nametowidget(parent_name)
            except Exception:
                break
        return None

    def _scroll_widget(self, widget: tk.Misc | None, steps: int, *, horizontal: bool = False) -> str | None:
        if widget is None or steps == 0:
            return None
        target = self._scrollable_ancestor(widget, horizontal=horizontal)
        if target is None:
            return None
        try:
            if horizontal:
                target.xview_scroll(steps, "units")
            else:
                target.yview_scroll(steps, "units")
            return "break"
        except Exception:
            return None

    def _wheel_steps(self, event: tk.Event) -> int:
        delta = getattr(event, "delta", 0)
        if delta:
            return -max(1, int(abs(delta) / 120)) if delta > 0 else max(1, int(abs(delta) / 120))
        return 0

    def _on_mousewheel(self, event: tk.Event) -> str | None:
        return self._scroll_widget(self._widget_under_pointer(event), self._wheel_steps(event), horizontal=False)

    def _on_shift_mousewheel(self, event: tk.Event) -> str | None:
        return self._scroll_widget(self._widget_under_pointer(event), self._wheel_steps(event), horizontal=True)

    def _on_linux_mousewheel(self, event: tk.Event) -> str | None:
        steps = -1 if getattr(event, "num", None) == 4 else 1
        return self._scroll_widget(self._widget_under_pointer(event), steps, horizontal=False)

    def _on_linux_shift_mousewheel(self, event: tk.Event) -> str | None:
        steps = -1 if getattr(event, "num", None) == 4 else 1
        return self._scroll_widget(self._widget_under_pointer(event), steps, horizontal=True)


def launch() -> None:
    app = Suite2pFrontendApp()
    app.mainloop()
