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
        self._curation_queue_status_cache: dict[str, dict[str, object]] = {}
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
        ...