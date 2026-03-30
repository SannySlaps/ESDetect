from posixpath import basename
import sys, os, time, json
from datetime import datetime
import numpy as np
import tifffile
import cv2
import serial
from queue import Queue, Empty
import threading
from threading import Lock

from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QPushButton, QLineEdit, QDoubleSpinBox, QSpinBox,QSlider, QComboBox, QVBoxLayout, QGridLayout, QGroupBox, QProgressBar, QCheckBox,QFileDialog, QSizePolicy, QTextEdit, QFrame, QMessageBox, QScrollArea)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from tifffile import imwrite, imread

from pymmcore_plus import CMMCorePlus
try:
    from pycromanager import Core as PycroCore
except Exception:
    PycroCore = None

import logging
import os

log_path = os.path.expanduser("~\\AppData\\Local\\pymmcore-plus\\pymmcore-plus\\logs\\pymmcore-plus.log")
logger = logging.getLogger("pymmcore-plus")
logger.handlers = []  # remove default handlers
logging.basicConfig(level=logging.DEBUG, filename=log_path, filemode="a")


class MMCoreCompatAdapter:
    """Adapter exposing MMCore-like camelCase methods over pycromanager Core."""

    def __init__(self, core):
        self._core = core

    def _call(self, camel_name, snake_name, *args, **kwargs):
        fn = getattr(self._core, camel_name, None)
        if fn is None:
            fn = getattr(self._core, snake_name, None)
        if fn is None:
            raise AttributeError(f"Core method missing: {camel_name}/{snake_name}")
        return fn(*args, **kwargs)

    def getCameraDevice(self):
        return self._call("getCameraDevice", "get_camera_device")

    def setCameraDevice(self, label):
        return self._call("setCameraDevice", "set_camera_device", label)

    def setROI(self, x, y, w, h):
        return self._call("setROI", "set_roi", x, y, w, h)

    def setExposure(self, value):
        return self._call("setExposure", "set_exposure", value)

    def setProperty(self, device, prop, value):
        return self._call("setProperty", "set_property", device, prop, value)

    def getProperty(self, device, prop):
        return self._call("getProperty", "get_property", device, prop)

    def isSequenceRunning(self):
        return self._call("isSequenceRunning", "is_sequence_running")

    def startContinuousSequenceAcquisition(self, interval_ms):
        return self._call(
            "startContinuousSequenceAcquisition",
            "start_continuous_sequence_acquisition",
            interval_ms,
        )

    def stopSequenceAcquisition(self):
        return self._call("stopSequenceAcquisition", "stop_sequence_acquisition")

    def getRemainingImageCount(self):
        return self._call("getRemainingImageCount", "get_remaining_image_count")

    def popNextImage(self):
        return self._call("popNextImage", "pop_next_image")

    def getImageWidth(self):
        return self._call("getImageWidth", "get_image_width")

    def getImageHeight(self):
        return self._call("getImageHeight", "get_image_height")

    def reset(self):
        # pycromanager Core may not expose reset; ignore if unavailable
        fn = getattr(self._core, "reset", None)
        if fn:
            return fn()
        return None

# -------------------- Load Core Thread --------------------

class LoadCoreThread(QThread):
    core_loaded = pyqtSignal(bool, object)  # success flag, core object or None

    def __init__(self, cfg_path):
        super().__init__()
        self.cfg_path = cfg_path
        self.core = None
        self.error_message = ""

    def _prepare_mm_runtime(self):
        mm_root = os.path.dirname(self.cfg_path)
        if not mm_root:
            return
        # Prepend MM root to PATH so adapter-dependent DLLs resolve consistently.
        os.environ["PATH"] = mm_root + os.pathsep + os.environ.get("PATH", "")
        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(mm_root)
        except Exception:
            pass

    def run(self):
        try:
            if not os.path.exists(self.cfg_path):
                self.error_message = f"Config file not found: {self.cfg_path}"
                self.core_loaded.emit(False, None)
                return

            self._prepare_mm_runtime()

            # Reset any existing instance
            core = CMMCorePlus.instance()
            self.core = core
            try:
                self.core.reset()
            except Exception:
                pass
            try:
                self.core.setDeviceAdapterSearchPaths([os.path.dirname(self.cfg_path)])
            except Exception:
                pass
            self.core.loadSystemConfiguration(self.cfg_path)
            cam = self.core.getCameraDevice()
            self.core_loaded.emit(True, core)
        except Exception as e:
            self.error_message = str(e)
            print(f"[ERROR] Failed to load configuration: {e}")
            self.core_loaded.emit(False, None)
    
    def stop(self):
        # safe stop/reset of any instance held by this thread
        try:
            core = CMMCorePlus.instance()
            if core is not None:
                try:
                    if getattr(core, "isSequenceRunning", lambda: False)():
                        core.stopSequenceAcquisition()
                except Exception:
                    pass
                try:
                    core.reset()
                except Exception:
                    pass
        except Exception:
            pass

# -------------------- Frame Writer Thread --------------------
class FrameWriterThread(QThread):
    log_event_signal = pyqtSignal(str, str)

    def __init__(self, queue):
        super().__init__()
        self.queue = queue
        self.running = True

    def run(self):
        while self.running:
            try:
                path, arr = self.queue.get(timeout=0.1)
                
                if not isinstance(arr, np.ndarray):
                    arr = np.array(arr, dtype=np.uint16)
                else:
                    arr = arr.astype(np.uint16)

                tifffile.imwrite(path, arr, photometric='minisblack', compression=None)
                self.queue.task_done()
                # self.log_event_signal.emit(f"Saved {path} ({len(arr)} frames)", "green")

            except Empty:
                continue
            except Exception as e:
                self.log_event_signal.emit(f"Error saving {path}: {e}", "red")

    def stop(self):
        self.running = False
        self.wait()

# -------------------- Live Preview Thread --------------------
class LivePreviewThread(QThread):
    image_ready = pyqtSignal(np.ndarray)  # throttled preview
    new_frame = pyqtSignal(np.ndarray)    # all frames for burst
    log_event_signal = pyqtSignal(str, str)

    def __init__(self, core, lock=None, preview_fps=30):
        super().__init__()
        self.core = core
        self.lock = lock
        self.running = False
        self.preview_fps = max(1, int(preview_fps))
        self.preview_enabled = True

    def set_preview_enabled(self, enabled):
        self.preview_enabled = bool(enabled)

    def run(self):
        self.running = True
        last_emit_time = time.perf_counter()
        emit_interval = 1.0 / self.preview_fps

        while self.running:
            got_any = False
            with self.lock:
                while self.core.getRemainingImageCount() > 0:
                    img = self.core.popNextImage()
                    frame = np.asarray(img, dtype=np.uint16)
                    if frame.ndim == 1:
                        try:
                            w = int(self.core.getImageWidth())
                            h = int(self.core.getImageHeight())
                            if w > 0 and h > 0 and w * h == frame.size:
                                frame = frame.reshape((h, w))
                        except Exception:
                            pass

                    # Emit to burst thread (all frames)
                    self.new_frame.emit(frame)
                    got_any = True

                    # Emit to GUI at throttled FPS
                    now = time.perf_counter()
                    if self.preview_enabled and now - last_emit_time >= emit_interval:
                        self.image_ready.emit(frame)
                        last_emit_time = now

            if not got_any:
                time.sleep(0.001)

    def stop(self):
        self.running = False

# -------------------- Simulated Preview Thread --------------------
class SimulatedPreviewThread(QThread):
    image_ready = pyqtSignal(np.ndarray)
    new_frame = pyqtSignal(np.ndarray)
    log_event_signal = pyqtSignal(str, str)

    def __init__(self, preview_fps=30, frame_shape=(600, 600)):
        super().__init__()
        self.running = False
        self.preview_fps = max(1, int(preview_fps))
        self.frame_shape = frame_shape
        self._phase = 0.0

    def run(self):
        self.running = True
        emit_interval = 1.0 / self.preview_fps
        next_t = time.perf_counter()
        h, w = self.frame_shape
        y, x = np.mgrid[0:h, 0:w]

        while self.running:
            self._phase += 0.08
            blob_x = int((np.sin(self._phase) * 0.4 + 0.5) * (w - 1))
            blob_y = int((np.cos(self._phase * 0.7) * 0.4 + 0.5) * (h - 1))
            dist2 = (x - blob_x) ** 2 + (y - blob_y) ** 2
            blob = np.exp(-dist2 / (2 * (max(12, w // 20) ** 2)))
            stripes = 0.12 * (1.0 + np.sin((x + self._phase * 35) * 0.04))
            noise = np.random.normal(0.0, 0.02, size=(h, w))
            img = 0.10 + 0.75 * blob + stripes + noise
            img = np.clip(img, 0.0, 1.0)
            frame = (img * 65535).astype(np.uint16)

            self.new_frame.emit(frame)
            self.image_ready.emit(frame)

            next_t += emit_interval
            sleep_s = next_t - time.perf_counter()
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                next_t = time.perf_counter()

    def stop(self):
        self.running = False

# -------------------- Burst Thread --------------------
class BurstThread(QThread):
    burst_done = pyqtSignal(int, object)      # burst_index, frames
    burst_started = pyqtSignal(int)
    log_event_signal = pyqtSignal(str, str)

    def __init__(self, burst_index, duration_s):
        super().__init__()
        self.burst_index = burst_index
        self.duration_s = duration_s
        self.frames = []
        self._stop_event = threading.Event()

    def collect_frame(self, frame):
        """Connect this to LivePreviewThread.new_frame"""
        self.frames.append(frame)

    def run(self):
        self.burst_started.emit(self.burst_index)
        start_time = time.time()
        while (time.time() - start_time) < self.duration_s and not self._stop_event.is_set():
            time.sleep(0.001)  # just wait; frames are collected via signal

        self.burst_done.emit(self.burst_index, self.frames)

    def stop(self):
        self._stop_event.set()

# -------------------- Live Preview Window --------------------
class LivePreviewWindow(QWidget):
    def __init__(self, core, lock=None):
        super().__init__()
        self.core = core
        self.camera_lock = lock
        self.setWindowTitle("Live Preview")
        self.label = QLabel("Live Preview")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout()
        layout.addWidget(self.label)
        self.setLayout(layout)
        self.setMinimumSize(800, 600)

    def update_frame(self, arr):
        # Convert to 8-bit grayscale
        arr8 = ((arr - arr.min()) / (np.ptp(arr) + 1e-6) * 255).astype(np.uint8)
        qimg = QImage(arr8.data(), arr.shape[1], arr.shape[0], QImage.Format_Grayscale8)
        pixmap = QPixmap.fromImage(qimg)
        self.label.setPixmap(pixmap.scaled(self.label.width(), self.label.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.label.repaint()
        QApplication.processEvents()

# -------------------- Collapsible GroupBox --------------------
class NoScrollComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class NoScrollSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class NoScrollDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class CollapsibleGroupBox(QWidget):
    def __init__(self, title):
        super().__init__()

        # Toggle button
        self.toggle_btn = QPushButton(title)
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(True)
        self.toggle_btn.setFlat(True)
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                text-align: left;
                font-weight: bold;
                background-color: transparent;
                border: none;
                padding-left: 5px;
            }
        """)
        self.toggle_btn.clicked.connect(self.on_toggle)

        # Content frame with border
        self.content_frame = QFrame()
        self.content_frame.setFrameShape(QFrame.StyledPanel)
        self.content_frame.setStyleSheet("""
            QFrame {
                border: 2px solid white;
                border-radius: 5px;
                margin-top: 5px;
                background-color: #2e2e2e;
            }
        """)
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(5,5,5,5)
        self.content_frame.setLayout(self.content_layout)

        # Main layout
        main_layout = QVBoxLayout()
        main_layout.addWidget(self.toggle_btn)
        main_layout.addWidget(self.content_frame)
        main_layout.setContentsMargins(0,0,0,0)
        main_layout.setSpacing(0)
        self.setLayout(main_layout)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.content_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def set_layout(self, layout):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.content_layout.addLayout(layout)

    def add_widget(self, widget):
        self.content_layout.addWidget(widget)

    def on_toggle(self):
        self.content_frame.setVisible(self.toggle_btn.isChecked())
        self.adjustSize()  # force the parent layout to recalc height
    # Force layout recalculation
        self.content_frame.updateGeometry()

# -------------------- Main GUI --------------------
class LiveImagingGUI(QWidget):
    def __init__(self, cfg_path):
            super().__init__()
            self.cfg_path = cfg_path
            self.setWindowTitle("CaImAn-Ac")
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
                "danger": "#ff6b6b",
                "warning": "#f3c969",
            }

            self.settings_file = "stim_gui_settings.json"
            self.settings = {}
            self.apply_dark_mode()

            self.last_frame = None
            self.disp_min = None
            self.disp_max = None
            self.live_window = None
            self.live_thread = None
            self.core = None
            self.camera_lock = Lock()
            self.default_clear_mode = "Pre-Sequence"
            self.default_clear_cycles = 2
            self.is_loading_core = False
            self.simulated_mode = False
            self.camera_runtime_ready = False
            self.using_running_mm = False
            self.frame_w = None
            self.frame_h = None
            self.suspend_live_updates = False

            self.log_queue = Queue()
            self.log_timer = QTimer(self)
            self.log_timer.timeout.connect(self.flush_log_queue)
            self.log_timer.start(50)

            self.burst_job_queue = None
            self.writer_thread = None

            self.arduino = None
            self.experiment_timer = None

            self.burst_index = 0
            self.burst_duration_s = 2.0
            self.pause_between_bursts_s = 8.0
            self.burst_active = False
            self.experiment_running = False
            self.experiment_stopped = False
            self.current_burst_frames = []
            self.current_burst_idx = 0
            self.current_burst_capacity = 0
            self.current_burst_count = 0
            self.last_burst_fps = 0.0
            self.experiment_stream_connected = False
            self.total_frames = 0
            self.frames_taken = 0
            self.burst_queue = None
            self.writer_thread = None
            self.prestart_timer = None
            self.prestart_remaining_s = 0
            self.pending_acquisition_mode = None
            self.continuous_recording = False
            self.continuous_frames_buffer = []
            self.continuous_chunk_size = 600
            self.continuous_chunk_index = 0
            self.continuous_save_folder = None
            self.title_folder = None
            self.session_folder = None
            self.session_id = None
            self.raw_folder = None
            self.metadata_folder = None
            self.analysis_folder = None
            self.analysis_working_folder = None
            self.analysis_run_packets_folder = None
            self.continuous_frame_counter = 0
            self.hist_bins = 128
            self.hist_auto_percentile_low = 0.1
            self.hist_auto_percentile_high = 99.9
            self.ttl_lock = Lock()
            self.event_lock = Lock()
            self.stim_events_path = None
            self.session_start_epoch = None

            self.build_ui()
            self.apply_theme_overrides()
            self.resize(1720, 980)
            self.load_settings()
            self.show()

    def _normalize_frame_shape(self, frame):
        arr = np.asarray(frame, dtype=np.uint16)
        if arr.ndim == 1:
            n = arr.size
            if self.frame_w and self.frame_h and (self.frame_w * self.frame_h == n):
                return arr.reshape((self.frame_h, self.frame_w))
            side = int(np.sqrt(n))
            if side * side == n:
                return arr.reshape((side, side))
            if n % 600 == 0:
                return arr.reshape((n // 600, 600))
            return arr.reshape((1, n))
        if arr.ndim > 2:
            arr = np.squeeze(arr)
            if arr.ndim > 2:
                arr = arr[..., 0]
        return arr

    def _safe_token(self, value, fallback):
        text = str(value or "").strip()
        if not text:
            text = fallback
        cleaned = []
        for ch in text:
            if ch.isalnum() or ch in ("_", "-"):
                cleaned.append(ch)
            elif ch.isspace():
                cleaned.append("_")
        token = "".join(cleaned).strip("_")
        return token or fallback

    def _genotype_folder_name(self):
        genotype = self.genotype_combo.currentText().strip() if hasattr(self, "genotype_combo") else ""
        if genotype == "Other":
            other = self._safe_token(self.genotype_other_edit.text(), "Other")
            return f"Other_{other}"
        return self._safe_token(genotype, "UnknownGenotype")

    def _next_session_name(self, parent_dir):
        os.makedirs(parent_dir, exist_ok=True)
        existing = []
        for name in os.listdir(parent_dir):
            if not name.startswith("Session_"):
                continue
            suffix = name.split("_", 1)[-1]
            if suffix.isdigit():
                existing.append(int(suffix))
        next_idx = (max(existing) + 1) if existing else 1
        return f"Session_{next_idx:03d}"

    def _prepare_session_structure(self, acquisition_mode):
        base_folder = self.save_path_edit.text() or "."
        if "OneDrive" in base_folder:
            self.log_event(
                "Save path is under OneDrive. Local non-synced path is recommended for max throughput.",
                "orange",
            )

        project_name = self._safe_token(self.project_name_edit.text(), "Project")
        genotype_folder = self._genotype_folder_name()
        age_group = self._safe_token(self.age_group_combo.currentText(), "UnknownAge")
        sex = self._safe_token(self.sex_combo.currentText(), "UnknownSex")
        animal_id = self._safe_token(self.mouse_id_edit.text(), "Animal")
        slice_folder = f"Slice_{int(self.slice_spin.value())}" if hasattr(self, "slice_spin") else "Slice_1"
        title_folder = os.path.join(
            base_folder,
            project_name,
            genotype_folder,
            age_group,
            sex,
            animal_id,
            slice_folder,
        )

        session_name = self._next_session_name(title_folder)
        session_folder = os.path.join(title_folder, session_name)
        timestamp_token = datetime.now().strftime("%Y%m%d_%H%M%S")
        raw_folder = os.path.join(session_folder, f"raw_{timestamp_token}")
        metadata_folder = os.path.join(session_folder, "metadata")
        analysis_folder = os.path.join(session_folder, "analysis")
        working_folder = os.path.join(analysis_folder, "working")
        run_packets_folder = os.path.join(analysis_folder, "run_packets")

        for path in (
            raw_folder,
            metadata_folder,
            working_folder,
            run_packets_folder,
        ):
            os.makedirs(path, exist_ok=True)

        self.title_folder = title_folder
        self.session_folder = session_folder
        self.raw_folder = raw_folder
        self.metadata_folder = metadata_folder
        self.analysis_folder = analysis_folder
        self.analysis_working_folder = working_folder
        self.analysis_run_packets_folder = run_packets_folder
        self.session_id = session_name
        self.session_timestamp_token = timestamp_token

        self._write_session_metadata(acquisition_mode)
        self._initialize_stim_events()
        return raw_folder

    def _write_session_metadata(self, acquisition_mode):
        metadata_folder = getattr(self, "metadata_folder", None)
        session_folder = getattr(self, "session_folder", None)
        raw_folder = getattr(self, "raw_folder", None)
        analysis_folder = getattr(self, "analysis_folder", None)
        session_id = getattr(self, "session_id", "")
        timestamp_token = getattr(self, "session_timestamp_token", "")
        if not all((metadata_folder, session_folder, raw_folder, analysis_folder)):
            return

        fps_text = self.fps_combo.currentText() if hasattr(self, "fps_combo") else "0"
        try:
            frame_rate_hz = float(fps_text)
        except Exception:
            frame_rate_hz = 0.0

        camera_device = ""
        try:
            camera_device = self._resolve_camera_label()
        except Exception:
            camera_device = ""

        if acquisition_mode == "burst_ttl":
            condition_type = "electrical_ttl"
        elif acquisition_mode == "burst_manual":
            condition_type = "manual_burst"
        else:
            condition_type = "manual_acquisition"

        payload = {
            "schema_version": 1,
            "project_name": self.project_name_edit.text().strip(),
            "condition_subtype": self.condition_subtype_edit.text().strip(),
            "genotype": self.genotype_combo.currentText().strip() if hasattr(self, "genotype_combo") else "",
            "genotype_other": self.genotype_other_edit.text().strip() if hasattr(self, "genotype_other_edit") else "",
            "age_group": self.age_group_combo.currentText().strip() if hasattr(self, "age_group_combo") else "",
            "sex": self.sex_combo.currentText().strip() if hasattr(self, "sex_combo") else "",
            "animal_id": self.mouse_id_edit.text().strip(),
            "slice_number": int(self.slice_spin.value()) if hasattr(self, "slice_spin") else None,
            "session_id": session_id,
            "session_timestamp": datetime.now().isoformat(timespec="seconds"),
            "session_folder": session_folder,
            "raw_folder": os.path.basename(raw_folder),
            "raw_path": raw_folder,
            "metadata_path": metadata_folder,
            "analysis_path": analysis_folder,
            "analysis_working_path": getattr(self, "analysis_working_folder", ""),
            "analysis_run_packets_path": getattr(self, "analysis_run_packets_folder", ""),
            "input_mode": "folder_tiffs",
            "acquisition_mode": acquisition_mode,
            "frame_rate_hz": frame_rate_hz,
            "acquired_frame_rate_hz": getattr(self, "acquired_frame_rate_hz", None),
            "pre_acquisition_offset_s": int(self.prestart_delay_spin.value()) if hasattr(self, "prestart_delay_spin") else 0,
            "exposure_ms": float(self.exp_spin.value()) if hasattr(self, "exp_spin") else 0.0,
            "camera_device": camera_device,
            "mm_config_path": self.cfg_path,
            "preview_enabled": bool(self.preview_cb.isChecked()) if hasattr(self, "preview_cb") else True,
            "operator": "",
            "experiment_type": self.expt_type_edit.text().strip(),
            "final_titer": self.final_titer_edit.text().strip(),
            "condition_type": condition_type,
            "kcl_present_at_start": bool(self.kcl_present_cb.isChecked()) if hasattr(self, "kcl_present_cb") else False,
            "kcl_concentration_mM": float(self.kcl_concentration_spin.value()) if hasattr(self, "kcl_concentration_spin") and self.kcl_present_cb.isChecked() else None,
            "drug_name": self.drug_name_edit.text().strip() if hasattr(self, "drug_name_edit") else "",
            "drug_concentration": self.drug_concentration_edit.text().strip() if hasattr(self, "drug_concentration_edit") else "",
            "drug_present_at_start": bool(self.drug_present_cb.isChecked()) if hasattr(self, "drug_present_cb") else False,
            "ttl_enabled": bool(self.run_trigger_cb.isChecked()) if acquisition_mode != "continuous" else False,
            "ttl_mode": self.ttl_mode_combo.currentText() if hasattr(self, "ttl_mode_combo") else "",
            "ttl_frequency_hz": int(self.ttl_frequency_spin.value()) if hasattr(self, "ttl_frequency_spin") else None,
            "ttl_duration_ms": int(self.ttl_duration_spin.value()) if hasattr(self, "ttl_duration_spin") else None,
            "trigger_delay_ms": int(self.trigger_time_spin.value()) if hasattr(self, "trigger_time_spin") else None,
            "notes": "",
            "raw_timestamp_token": timestamp_token,
        }

        out_path = os.path.join(metadata_folder, "session_metadata.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def _update_acquired_frame_rate(self, measured_fps):
        try:
            measured_fps = float(measured_fps)
        except Exception:
            return
        if measured_fps <= 0:
            return
        self.acquired_frame_rate_hz = measured_fps
        acquisition_mode = "continuous" if getattr(self, "continuous_recording", False) else "burst_ttl"
        self._write_session_metadata(acquisition_mode)

    def _initialize_stim_events(self):
        metadata_folder = getattr(self, "metadata_folder", None)
        session_id = getattr(self, "session_id", "")
        if not metadata_folder:
            return
        self.stim_events_path = os.path.join(metadata_folder, "stim_events.json")
        payload = {
            "schema_version": 1,
            "session_id": session_id,
            "events": [],
        }
        with open(self.stim_events_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def _relative_event_time_s(self):
        if self.session_start_epoch is None:
            return None
        return round(max(0.0, time.time() - self.session_start_epoch), 3)

    def _append_stim_event(
        self,
        event_type,
        label,
        frame_index=None,
        concentration_mM=None,
        duration_s=None,
        ttl_channel=None,
        ttl_pulse_width_ms=None,
        notes="",
        extra=None,
    ):
        if not self.stim_events_path:
            return
        payload = None
        with self.event_lock:
            try:
                with open(self.stim_events_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception:
                payload = {
                    "schema_version": 1,
                    "session_id": getattr(self, "session_id", ""),
                    "events": [],
                }
            events = payload.setdefault("events", [])
            event = {
                "event_id": len(events) + 1,
                "event_type": event_type,
                "timestamp_s": self._relative_event_time_s(),
                "frame_index": frame_index,
                "label": label,
                "concentration_mM": concentration_mM,
                "duration_s": duration_s,
                "ttl_channel": ttl_channel,
                "ttl_pulse_width_ms": ttl_pulse_width_ms,
                "notes": notes,
            }
            if extra:
                event.update(extra)
            events.append(event)
            with open(self.stim_events_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)

    def _prepare_mm_runtime(self):
        mm_root = os.path.dirname(self.cfg_path)
        if not mm_root:
            return
        os.environ["PATH"] = mm_root + os.pathsep + os.environ.get("PATH", "")
        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(mm_root)
        except Exception:
            pass

    def on_core_loaded(self, success, core):
        self.is_loading_core = False
        self.load_core.setEnabled(True)

        if not success:
            err = getattr(self.core_thread, "error_message", "Unknown error")
            self.log_event(f"Core failed to load: {err}", "red")
            QMessageBox.critical(
                self,
                "Camera Load Failed",
                f"Could not initialize camera from config:\n{self.cfg_path}\n\nError:\n{err}",
            )
            self.live_btn.setEnabled(False)
            self.start_btn.setEnabled(False)
            return

        self.core = core
        self.simulated_mode = False
        self.using_running_mm = False
        self.camera_runtime_ready = False
        self.log_event("Core loaded successfully (camera start deferred)", "green")

        self.live_btn.setEnabled(True)
        self.start_btn.setEnabled(True)

    def _resolve_camera_label(self):
        try:
            cam = self.core.getCameraDevice()
            if cam:
                return str(cam)
        except Exception:
            pass
        try:
            cam = self.core.getProperty("Core", "Camera")
            if cam and str(cam).lower() not in ("undefined", "null"):
                return str(cam)
        except Exception:
            pass
        return ""

    def ensure_camera_runtime_started(self):
        if self.core is None or self.simulated_mode:
            return False
        if self.camera_runtime_ready:
            return True
        try:
            cam = self._resolve_camera_label()
            if not cam:
                self.log_event(
                    "Camera basic setup failed: no active camera in running MM. "
                    "Load your hardware config in MM first.",
                    "red",
                )
                return False
            if not self.using_running_mm:
                self.core.setCameraDevice(cam)
            if hasattr(self.core, "isSequenceRunning") and self.core.isSequenceRunning():
                self.core.stopSequenceAcquisition()
                time.sleep(0.05)
            self.core.setROI(0, 0, 600, 600)
            self.core.setExposure(self.exp_spin.value())
            try:
                self.frame_w = int(self.core.getImageWidth())
                self.frame_h = int(self.core.getImageHeight())
            except Exception:
                self.frame_w = None
                self.frame_h = None
        except Exception as e:
            self.log_event(f"Camera basic setup failed: {e}", "red")
            return False

        try:
            self.core.setProperty(cam, "CircularBufferEnabled", "ON")
            self.core.setProperty(cam, "CircularBufferFrameCount", 2000)
            self.core.setProperty(cam, "ClearMode", "Pre-Sequence")
            self.core.setProperty(cam, "ClearCycles", 2)
        except Exception as e:
            self.log_event(f"Warning setting camera properties: {e}", "orange")

        try:
            if not (hasattr(self.core, "isSequenceRunning") and self.core.isSequenceRunning()):
                self.core.startContinuousSequenceAcquisition(0)
        except Exception as e:
            self.log_event(f"Could not start continuous sequence acquisition: {e}", "red")
            return False

        self.camera_runtime_ready = True
        self.log_event("Camera runtime started", "green")
        return True

    def apply_exposure_now(self):
        if self.core is None or self.simulated_mode:
            return
        try:
            self.core.setExposure(self.exp_spin.value())
        except Exception as e:
            self.log_event(f"Could not apply exposure immediately: {e}", "orange")

    def request_core_load(self):
        if self.is_loading_core:
            self.log_event("Camera load already in progress", "orange")
            return
        if self.simulated_mode and self.live_thread and self.live_thread.isRunning():
            self.live_thread.stop()
            self.live_thread.wait()
            self.simulated_mode = False
            self.log_event("Stopped simulated camera mode", "yellow")
        if not os.path.exists(self.cfg_path):
            msg = f"Config file not found:\n{self.cfg_path}"
            self.log_event(msg, "red")
            QMessageBox.critical(self, "Missing Config", msg)
            return
        if self.core_thread and self.core_thread.isRunning():
            self.log_event("Core thread is already running", "orange")
            return

        self.is_loading_core = True
        self.load_core.setEnabled(False)
        self.set_overlay("LOADING CAMERA...", color="orange")
        self.log_event(f"Loading camera config: {self.cfg_path}", "yellow")
        self._prepare_mm_runtime()
        # Load on GUI thread to avoid cross-thread MMCore access issues.
        QTimer.singleShot(0, self.load_core_sync)

    def connect_running_mm(self):
        if PycroCore is None:
            QMessageBox.critical(
                self,
                "Missing Dependency",
                "pycromanager is not installed in this environment.",
            )
            return
        if self.simulated_mode and self.live_thread and self.live_thread.isRunning():
            self.live_thread.stop()
            self.live_thread.wait()
            self.simulated_mode = False
            self.log_event("Stopped simulated camera mode", "yellow")

        try:
            self.set_overlay("CONNECTING TO MM...", color="orange")
            remote_core = PycroCore(timeout=5000)
            self.core = MMCoreCompatAdapter(remote_core)
            self.using_running_mm = True
            self.camera_runtime_ready = False
            cam = self._resolve_camera_label()
            if not cam:
                self.log_event(
                    "Connected to MM, but no active camera label was found. "
                    "In Micro-Manager, load your configuration and set the camera.",
                    "red",
                )
                QMessageBox.warning(
                    self,
                    "No Camera In MM",
                    "Connected to Micro-Manager but no active camera is set.\n"
                    "Load configuration in MM and ensure camera is selected.",
                )
                return
            try:
                self.frame_w = int(self.core.getImageWidth())
                self.frame_h = int(self.core.getImageHeight())
            except Exception:
                self.frame_w = None
                self.frame_h = None
            self.log_event(f"Connected to running MM. Camera: {cam}", "green")
            self.set_overlay("MM CONNECTED", color="green")
            self.live_btn.setEnabled(True)
            self.start_btn.setEnabled(True)
        except Exception as e:
            self.log_event(f"Failed to connect to running MM: {e}", "red")
            QMessageBox.critical(
                self,
                "Connection Failed",
                "Could not connect to running Micro-Manager.\n"
                "Make sure MM is open and pycro server is available.",
            )

    def load_core_sync(self):
        try:
            core = CMMCorePlus.instance()
            try:
                core.reset()
            except Exception:
                pass
            core.loadSystemConfiguration(self.cfg_path)
            self.on_core_loaded(True, core)
        except Exception as e:
            if getattr(self, "core_thread", None) is not None:
                self.core_thread.error_message = str(e)
            self.on_core_loaded(False, None)

    def start_simulated_camera(self):
        try:
            if self.is_loading_core:
                self.log_event("Wait for camera loading to finish", "orange")
                return
            if self.live_thread and self.live_thread.isRunning():
                self.live_thread.stop()
                self.live_thread.wait()

            fps = int(self.fps_combo.currentText())
            self.live_thread = SimulatedPreviewThread(preview_fps=fps, frame_shape=(600, 600))
            self.live_thread.image_ready.connect(self.update_live_frame)
            self.live_thread.log_event_signal.connect(self.log_event)
            self.live_thread.start()
            self.simulated_mode = True

            if self.live_window is None:
                self.live_window = LivePreviewWindow(core=None, lock=None)
            self.live_window.show()
            self.set_overlay("SIM CAMERA ON", "orange")
            self.log_event("Simulated camera started", "yellow")
        except Exception as e:
            self.log_event(f"Simulated camera failed: {e}", "red")
            QMessageBox.critical(self, "Sim Camera Error", str(e))
# -------------------- Collapsible / Group Widgets --------------------
    def build_ui(self):
    # File Saving
        self.file_group = CollapsibleGroupBox("File Saving")
        file_layout = QGridLayout()
        file_layout.setHorizontalSpacing(10)
        file_layout.setVerticalSpacing(8)
        for col, stretch in enumerate((0, 3, 0, 1, 0, 1)):
            file_layout.setColumnStretch(col, stretch)
        self.save_path_edit = QLineEdit()
        file_layout.addWidget(self.save_path_edit, 0, 1, 1, 4)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_folder)
        file_layout.addWidget(browse_btn, 0, 5)
        self.project_name_edit = QLineEdit("Stim_Exp")
        file_layout.addWidget(self.project_name_edit, 1, 1)
        self.file_group.set_layout(file_layout)
        lbl = QLabel("Mouse ID")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        file_layout.addWidget(lbl, 1, 2)
        self.mouse_id_edit = QLineEdit("Mouse_001")
        file_layout.addWidget(self.mouse_id_edit, 1, 3)
        lbl = QLabel("Slice #")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        file_layout.addWidget(lbl, 1, 4)
        self.slice_spin = NoScrollSpinBox()
        self.slice_spin.setRange(1, 99)
        self.slice_spin.setValue(1)
        file_layout.addWidget(self.slice_spin, 1, 5)
        lbl = QLabel("Condition / Experiment subtype")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        file_layout.addWidget(lbl, 2, 0)
        self.condition_subtype_edit = QLineEdit("GCamp")
        self.expt_type_edit = self.condition_subtype_edit
        file_layout.addWidget(self.expt_type_edit, 2, 1)
        lbl = QLabel("Final Titer")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        file_layout.addWidget(lbl, 2, 2)
        self.final_titer_edit = QLineEdit("e11")
        file_layout.addWidget(self.final_titer_edit, 2, 3)
        lbl = QLabel("Genotype")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        file_layout.addWidget(lbl, 3, 0)
        self.genotype_combo = NoScrollComboBox()
        self.genotype_combo.addItems(["APOE2", "APOE3", "APOE4", "Other"])
        self.genotype_combo.setCurrentText("APOE3")
        file_layout.addWidget(self.genotype_combo, 3, 1)
        lbl = QLabel("Genotype Other")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        file_layout.addWidget(lbl, 3, 2)
        self.genotype_other_edit = QLineEdit("")
        file_layout.addWidget(self.genotype_other_edit, 3, 3, 1, 3)
        lbl = QLabel("Age Group")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        file_layout.addWidget(lbl, 4, 0)
        self.age_group_combo = NoScrollComboBox()
        self.age_group_combo.addItems(["Young", "Aged"])
        self.age_group_combo.setCurrentText("Young")
        file_layout.addWidget(self.age_group_combo, 4, 1)
        lbl = QLabel("Sex")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        file_layout.addWidget(lbl, 4, 2)
        self.sex_combo = NoScrollComboBox()
        self.sex_combo.addItems(["Male", "Female"])
        self.sex_combo.setCurrentText("Male")
        file_layout.addWidget(self.sex_combo, 4, 3, 1, 3)
        lbl = QLabel("KCl Conc. (mM)")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        file_layout.addWidget(lbl, 5, 0)
        self.kcl_concentration_spin = NoScrollDoubleSpinBox()
        self.kcl_concentration_spin.setRange(0.0, 1000.0)
        self.kcl_concentration_spin.setDecimals(2)
        self.kcl_concentration_spin.setValue(0.0)
        file_layout.addWidget(self.kcl_concentration_spin, 5, 1)
        lbl = QLabel("KCl at Start")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        file_layout.addWidget(lbl, 5, 2)
        self.kcl_present_cb = QCheckBox("Present")
        self.kcl_present_cb.setChecked(False)
        file_layout.addWidget(self.kcl_present_cb, 5, 3, 1, 3)
        lbl = QLabel("Drug Name")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        file_layout.addWidget(lbl, 6, 0)
        self.drug_name_edit = QLineEdit("")
        file_layout.addWidget(self.drug_name_edit, 6, 1)
        lbl = QLabel("Drug at Start")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        file_layout.addWidget(lbl, 6, 2)
        self.drug_present_cb = QCheckBox("Present")
        self.drug_present_cb.setChecked(False)
        file_layout.addWidget(self.drug_present_cb, 6, 3)
        lbl = QLabel("Drug Conc.")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        file_layout.addWidget(lbl, 6, 4)
        self.drug_concentration_edit = QLineEdit("")
        file_layout.addWidget(self.drug_concentration_edit, 6, 5)
        lbl = QLabel("Save Folder")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        file_layout.addWidget(lbl, 0, 0)
        lbl = QLabel("Project Name")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        file_layout.addWidget(lbl, 1, 0)

# Acquisition Settings
        self.acq_group = CollapsibleGroupBox("Acquisition Settings")
        acq_layout = QGridLayout()
        acq_layout.setHorizontalSpacing(10)
        acq_layout.setVerticalSpacing(8)
        for col, stretch in enumerate((0, 2, 0, 2, 0, 2)):
            acq_layout.setColumnStretch(col, stretch)

        lbl = QLabel("Total Duration (min)")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        acq_layout.addWidget(lbl, 0, 0)
        self.total_time_spin = NoScrollDoubleSpinBox()
        self.total_time_spin.setValue(5)
        acq_layout.addWidget(self.total_time_spin, 0, 1)

        lbl = QLabel("Trigger Time (ms)")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        acq_layout.addWidget(lbl, 0, 2)
        self.trigger_time_spin = NoScrollDoubleSpinBox()
        self.trigger_time_spin.setRange(0, 10000)  # 0 ms to 10,000 ms
        self.trigger_time_spin.setValue(1000)
        acq_layout.addWidget(self.trigger_time_spin, 0, 3, 1, 3)



        lbl = QLabel("Burst Duration (s)")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")    
        acq_layout.addWidget(lbl, 1, 0)
        self.burst_duration_spin = NoScrollDoubleSpinBox()
        self.burst_duration_spin.setRange(0.1, 60.0)   # 0.1s to 60s
        self.burst_duration_spin.setDecimals(1)
        self.burst_duration_spin.setValue(2.0)         # default 2 seconds
        self.burst_duration_spin.setSuffix(" s")
        acq_layout.addWidget(self.burst_duration_spin, 1, 1)

        lbl = QLabel("Wait interval (s)")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        acq_layout.addWidget(lbl, 1, 2)
        self.wait_interval_spin = NoScrollDoubleSpinBox()
        self.wait_interval_spin.setRange(0.0, 600.0)   # up to 10 minutes if needed
        self.wait_interval_spin.setDecimals(1)
        self.wait_interval_spin.setValue(8.0)          # default 8 seconds
        self.wait_interval_spin.setSuffix(" s")
        acq_layout.addWidget(self.wait_interval_spin, 1, 3, 1, 3)

        lbl = QLabel("Exposure")    
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        acq_layout.addWidget(lbl, 2, 0)
        self.exp_spin = NoScrollDoubleSpinBox()
        self.exp_spin.setRange(0,1000)
        self.exp_spin.setValue(10)
        acq_layout.addWidget(self.exp_spin, 2, 1)

        lbl = QLabel("Frames Per Second")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        acq_layout.addWidget(lbl, 2, 2)
        self.fps_combo = NoScrollComboBox()
        self.fps_combo.addItems(["5", "10", "15", "30", "60"])
        self.fps_combo.setCurrentText("60")
        acq_layout.addWidget(self.fps_combo, 2, 3)
        lbl = QLabel("Pre-start Delay")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        acq_layout.addWidget(lbl, 2, 4)
        self.prestart_delay_spin = NoScrollSpinBox()
        self.prestart_delay_spin.setRange(0, 600)
        self.prestart_delay_spin.setValue(0)
        self.prestart_delay_spin.setSuffix(" s")
        acq_layout.addWidget(self.prestart_delay_spin, 2, 5)

        lbl = QLabel("Number of Bursts (0=auto)")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        acq_layout.addWidget(lbl, 3, 0)
        self.num_bursts_spin = NoScrollSpinBox()
        self.num_bursts_spin.setRange(0, 100000)
        self.num_bursts_spin.setValue(0)
        acq_layout.addWidget(self.num_bursts_spin, 3, 1)
        lbl = QLabel("Acquisition Mode")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        acq_layout.addWidget(lbl, 3, 2)
        self.acquisition_mode_combo = NoScrollComboBox()
        self.acquisition_mode_combo.addItems(["Continuous", "Burst (No TTL)", "Burst + TTL"])
        self.acquisition_mode_combo.setCurrentText("Burst + TTL")
        self.acquisition_mode_combo.currentTextChanged.connect(self._sync_acquisition_mode_ui)
        acq_layout.addWidget(self.acquisition_mode_combo, 3, 3, 1, 3)

        self.fps_status_label = QLabel("")
        self.fps_status_label.setProperty("noBorder", True)
        self.fps_status_label.setStyleSheet("QLabel[noBorder='true'] { border:none; font-weight: 600; }")
        acq_layout.addWidget(self.fps_status_label, 4, 0, 1, 6)

        self.acq_group.set_layout(acq_layout)
# Camera Controls
        self.camera_group = CollapsibleGroupBox("Camera Controls")
        cam_layout = QGridLayout()
        cam_layout.setHorizontalSpacing(10)
        cam_layout.setVerticalSpacing(8)
        self.hist_label = QLabel("Histogram")
        self.hist_label.setMinimumHeight(110)
        self.hist_label.setStyleSheet("background-color:#1e1e1e; border:1px solid #666;")
        cam_layout.addWidget(self.hist_label, 0, 1, 1, 3)
        self.hist_stats_label = QLabel("min: -  max: -  mean: -")
        self.hist_stats_label.setStyleSheet("color:#cfcfcf;")
        cam_layout.addWidget(self.hist_stats_label, 0, 4, 1, 2)
        self.level_low_slider = QSlider(Qt.Horizontal)
        self.level_low_slider.setRange(0, 65535)
        self.level_low_slider.setValue(500)
        cam_layout.addWidget(self.level_low_slider, 1, 1, 1, 4)
        self.level_high_slider = QSlider(Qt.Horizontal)
        self.level_high_slider.setRange(1, 65535)
        self.level_high_slider.setValue(60000)
        cam_layout.addWidget(self.level_high_slider, 2, 1, 1, 4)
        self.auto_levels_btn = QPushButton("Auto")
        self.auto_levels_btn.clicked.connect(self.auto_levels_from_frame)
        cam_layout.addWidget(self.auto_levels_btn, 1, 5)
        self.full_levels_btn = QPushButton("Full")
        self.full_levels_btn.clicked.connect(self.full_levels)
        cam_layout.addWidget(self.full_levels_btn, 2, 5)
        self.zoom_combo = NoScrollComboBox()
        self.zoom_combo.addItems(["50%", "100%", "200%", "300%", "400%"])
        self.zoom_combo.setCurrentText("100%")
        cam_layout.addWidget(self.zoom_combo, 3, 1, 1, 2)
        self.load_core = QPushButton("Connect Running MM")
        self.load_core.clicked.connect(self.connect_running_mm)
        cam_layout.addWidget(self.load_core, 4, 0)
        self.live_btn = QPushButton("Toggle Live Preview")
        self.live_btn.clicked.connect(self.toggle_live)
        cam_layout.addWidget(self.live_btn, 4, 1)
        self.start_btn = QPushButton("Start Acquisition")
        self.start_btn.clicked.connect(self.start_selected_acquisition)
        cam_layout.addWidget(self.start_btn, 4, 2)
        self.stop_btn = QPushButton("Stop Acquisition")
        self.stop_btn.clicked.connect(self.stop_selected_acquisition)
        cam_layout.addWidget(self.stop_btn, 4, 3)
        self.start_record_btn = QPushButton("Start Recording")
        self.start_record_btn.clicked.connect(self.start_continuous_recording)
        cam_layout.addWidget(self.start_record_btn, 5, 0, 1, 2)
        self.stop_record_btn = QPushButton("Stop Recording")
        self.stop_record_btn.clicked.connect(self.stop_continuous_recording)
        cam_layout.addWidget(self.stop_record_btn, 5, 2, 1, 2)
        self.camera_group.set_layout(cam_layout)
        self.start_record_btn.setVisible(False)
        self.stop_record_btn.setVisible(False)
        self._sync_acquisition_mode_ui()
        lbl = QLabel("Histogram")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        cam_layout.addWidget(lbl, 0, 0)
        lbl = QLabel("Black Level")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        cam_layout.addWidget(lbl, 1, 0)
        lbl = QLabel("White Level")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        cam_layout.addWidget(lbl, 2, 0)
        lbl = QLabel("Zoom")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        cam_layout.addWidget(lbl, 3, 0)

        self.core_thread = LoadCoreThread(self.cfg_path)
        self.core_thread.core_loaded.connect(self.on_core_loaded)

    # Arduino Controls
        self.arduino_group = CollapsibleGroupBox("Arduino Controls")
        ar_layout = QGridLayout()
        ar_layout.setHorizontalSpacing(10)
        ar_layout.setVerticalSpacing(8)
        for col, stretch in enumerate((0, 3, 0, 3)):
            ar_layout.setColumnStretch(col, stretch)
        self.serial_edit = QLineEdit("COM5"); ar_layout.addWidget(self.serial_edit,0,1)
        self.baud_combo = NoScrollComboBox(); self.baud_combo.addItems(["57600","9600","115200","250000"]); self.baud_combo.setCurrentText("57600"); ar_layout.addWidget(self.baud_combo,0,3)
        self.run_trigger_cb = QCheckBox("Send TTL"); self.run_trigger_cb.setCheckable(True); self.run_trigger_cb.setChecked(True)
        ar_layout.addWidget(self.run_trigger_cb,2,2)
        test_ttl_btn = QPushButton("Send TTL Now"); test_ttl_btn.clicked.connect(self.test_ttl); ar_layout.addWidget(test_ttl_btn,2,3)

        lbl = QLabel("Arduino Port")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        ar_layout.addWidget(lbl, 0, 0)
        lbl = QLabel("Baud Rate")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        ar_layout.addWidget(lbl, 0, 2)

        lbl = QLabel("Pulse Frequency (Hz)")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        ar_layout.addWidget(lbl,1,0)
        self.ttl_frequency_spin = NoScrollSpinBox()
        self.ttl_frequency_spin.setRange(1, 1000) # Hz
        self.ttl_frequency_spin.setValue(40)
        ar_layout.addWidget(self.ttl_frequency_spin,1,1)

        lbl = QLabel("Train Duration (ms)")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        ar_layout.addWidget(lbl,1,2)
        self.ttl_duration_spin = NoScrollSpinBox()
        self.ttl_duration_spin.setRange(1, 5000) # ms
        self.ttl_duration_spin.setValue(300)
        ar_layout.addWidget(self.ttl_duration_spin,1,3)

        lbl = QLabel("Pulse Mode")
        lbl.setProperty("noBorder", True)
        lbl.setStyleSheet("QLabel[noBorder='true'] { border:none }")
        ar_layout.addWidget(lbl,2,0)
        self.ttl_mode_combo = NoScrollComboBox()
        self.ttl_mode_combo.addItems(["Single Pulse", "Train"])
        ar_layout.addWidget(self.ttl_mode_combo,2,1)
        self.arduino_group.set_layout(ar_layout)

        self.info_group = QGroupBox("")
        info_layout = QGridLayout()
        self.overlay_label = QLabel("READY"); self.overlay_label.setAlignment(Qt.AlignCenter)
        self.overlay_label.setStyleSheet("color: green; font-weight: bold; font-size:20px")
        info_layout.addWidget(self.overlay_label,1,0,1,2)
        self.progressbar = QProgressBar(); self.progressbar.setRange(0,100)
        info_layout.addWidget(self.progressbar,2,0,1,1)
        self.timer_label = QLabel("00:00 / 00:00"); self.timer_label.setAlignment(Qt.AlignCenter); info_layout.addWidget(self.timer_label,2,1)
        self.info_group.setLayout(info_layout)

# -------------------- Log --------------------
        self.log_group = CollapsibleGroupBox("Log")
        log_layout = QVBoxLayout()
        self.log_group.toggle_btn.setStyleSheet("""
        QPushButton {
            text-align: left;
            font-weight: bold;
            font-size: 12px;
            color: #f0f0f0;
            border: none;
            border-bottom: 2px solid #f0f0f0;  /* underline */
            padding-bottom: 4px;               /* spacing below the text */
            padding-left: 5px;
            background-color: transparent;
            }
        """)
        self.log_group.content_frame.setStyleSheet("""QFrame {border: none;background-color: #444;}""")
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        #self.log_text.setStyleSheet("background-color:#444; color:#f0f0f0; font-family: monospace;")
        self.log_text.setMaximumHeight(150)
        self.log_text.setProperty("noBorder", True)
        self.log_text.setStyleSheet("""QTextEdit[noBorder='true'] {border: none;background-color: #444;color: #f0f0f0;font-family: monospace;}""")
        log_layout.addWidget(self.log_text)
        self.log_group.set_layout(log_layout)

        for grid in (file_layout, acq_layout, cam_layout, ar_layout, info_layout):
            try:
                col_count = grid.columnCount()
            except Exception:
                col_count = 0
            for col in range(col_count):
                grid.setColumnStretch(col, 0)
            if grid is file_layout:
                grid.setColumnStretch(1, 1)
            elif grid is acq_layout:
                grid.setColumnStretch(1, 1)
                grid.setColumnStretch(3, 1)
                grid.setColumnStretch(5, 1)
            elif grid is cam_layout:
                grid.setColumnStretch(1, 1)
                grid.setColumnStretch(2, 1)
                grid.setColumnStretch(3, 1)
                grid.setColumnStretch(4, 1)
            elif grid is ar_layout:
                grid.setColumnStretch(1, 1)
                grid.setColumnStretch(3, 1)

        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(8, 8, 8, 8)
        self.main_layout.setSpacing(6)
        self.main_layout.addWidget(self.file_group)
        self.main_layout.addWidget(self.arduino_group)
        self.main_layout.addWidget(self.acq_group)
        self.main_layout.addWidget(self.camera_group)
        self.main_layout.addWidget(self.info_group)
        self.main_layout.addWidget(self.log_group)
        self.main_layout.addStretch(1)

        self.content_widget = QWidget()
        self.content_widget.setLayout(self.main_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setWidget(self.content_widget)

        self.root_layout = QVBoxLayout()
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)
        self.root_layout.addWidget(self.scroll_area)
        self.setLayout(self.root_layout)


    # Connect sliders & zoom to live update
        self.level_low_slider.valueChanged.connect(lambda _: self.update_live_display())
        self.level_high_slider.valueChanged.connect(lambda _: self.update_live_display())
        self.zoom_combo.currentTextChanged.connect(lambda _: self.update_live_display())
        self.exp_spin.valueChanged.connect(lambda _: self.apply_exposure_now())
        self.exp_spin.valueChanged.connect(lambda _: self.update_fps_guidance())
        self.fps_combo.currentTextChanged.connect(lambda _: self.update_fps_guidance())
        self.update_fps_guidance()

        print("[DEBUG] UI built successfully")

    # -------------------- Dark Mode --------------------
    def apply_dark_mode(self):
        c = self.colors
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {c["bg"]};
                color: {c["text"]};
                border: none;
            }}
            QLabel {{
                background-color: {c["bg"]};
                color: {c["text"]};
                border: none;
            }}
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit {{
                background-color: {c["entry"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                border-radius: 4px;
                padding: 4px 6px;
            }}
            QComboBox::drop-down {{
                border: none;
                background-color: {c["panel_alt"]};
                width: 22px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {c["panel_alt"]};
                color: {c["text"]};
                selection-background-color: {c["accent"]};
                selection-color: {c["button_text"]};
                border: 1px solid {c["border"]};
            }}
            QPushButton {{
                background-color: {c["accent"]};
                color: {c["button_text"]};
                border: 1px solid {c["accent"]};
                border-radius: 4px;
                padding: 6px 10px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {c["accent_active"]};
                border-color: {c["accent_active"]};
            }}
            QPushButton:disabled {{
                background-color: {c["panel_alt"]};
                color: {c["muted"]};
                border-color: {c["border"]};
            }}
            QCheckBox {{
                color: {c["text"]};
                spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid {c["border"]};
                background: {c["entry"]};
            }}
            QCheckBox::indicator:checked {{
                background: {c["accent"]};
                border: 1px solid {c["accent"]};
            }}
            QProgressBar {{
                background-color: {c["panel_alt"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                border-radius: 4px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background-color: {c["accent"]};
            }}
            QGroupBox {{
                background-color: {c["bg"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: 600;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: {c["text"]};
            }}
        """)

    def update_fps_guidance(self):
        if not hasattr(self, "fps_status_label") or not hasattr(self, "exp_spin") or not hasattr(self, "fps_combo"):
            return
        try:
            exposure_ms = float(self.exp_spin.value())
        except Exception:
            exposure_ms = 0.0
        try:
            target_fps = float(self.fps_combo.currentText())
        except Exception:
            target_fps = 0.0

        estimated_max_fps = 0.0 if exposure_ms <= 0 else (1000.0 / exposure_ms)
        if target_fps <= 0 or estimated_max_fps <= 0:
            color = self.colors["muted"]
            msg = "Estimated max FPS from exposure: n/a"
        else:
            ratio = estimated_max_fps / target_fps
            if ratio >= 1.15:
                color = self.colors["accent"]
                state = "Exposure supports target FPS"
            elif ratio >= 1.0:
                color = self.colors["warning"]
                state = "Exposure is borderline for target FPS"
            else:
                color = self.colors["danger"]
                state = "Target FPS is unlikely with current exposure"
            msg = f"Estimated max FPS from exposure: {estimated_max_fps:.1f} | {state}"
        self.fps_status_label.setText(msg)
        self.fps_status_label.setStyleSheet(
            f"QLabel[noBorder='true'] {{ border:none; font-weight: 600; color: {color}; }}"
        )

    def apply_theme_overrides(self):
        c = self.colors

        group_header_style = f"""
            QPushButton {{
                text-align: left;
                font-weight: bold;
                font-size: 12px;
                color: {c["text"]};
                border: none;
                border-bottom: 2px solid {c["border"]};
                padding: 6px 5px 5px 8px;
                background-color: transparent;
            }}
        """
        group_content_style = f"QFrame {{ border: none; background-color: {c['panel']}; }}"

        for group in (
            getattr(self, "file_group", None),
            getattr(self, "acq_group", None),
            getattr(self, "camera_group", None),
            getattr(self, "arduino_group", None),
            getattr(self, "log_group", None),
        ):
            if group is not None:
                group.toggle_btn.setStyleSheet(group_header_style)
                group.content_frame.setStyleSheet(group_content_style)

        self.hist_label.setStyleSheet(
            f"background-color:{c['panel_soft']}; border:1px solid {c['border']};"
        )
        self.hist_stats_label.setStyleSheet(f"color:{c['muted']};")

        self.info_group.setStyleSheet(
            f"QGroupBox {{ background-color: {c['panel_soft']}; border: 1px solid {c['border']}; border-radius: 6px; }}"
        )
        self.overlay_label.setStyleSheet(
            f"color: {c['accent']}; font-weight: bold; font-size:20px"
        )
        self.timer_label.setStyleSheet(f"color: {c['text']};")

        self.log_text.setStyleSheet(
            f"QTextEdit[noBorder='true'] {{"
            f"border: none;"
            f"background-color: {c['panel']};"
            f"color: {c['text']};"
            f"font-family: Consolas, 'Courier New', monospace;"
            f"}}"
        )
    # ---------------------Log events--------------------------
    def set_overlay(self, text, color="red"):
        self.overlay_label.setText(text)
        self.overlay_label.setStyleSheet(f"color: {color}; font-size: 20px; font-weight: bold;")

    def log_event(self, msg, color="white"):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]  # include milliseconds
        self.log_text.append(f"<span style='color:{color}'>{timestamp} - {msg}</span>")

    def flush_log_queue(self):
        while not self.log_queue.empty():
            try:
                ts, msg, color = self.log_queue.get_nowait()  # expects exactly 3
                self.log_text.append(f"<span style='color:{color}'>{ts} - {msg}</span>")
            except Exception as e:
                print("Log flush error:", e)

    # -------------------- Folder & Arduino --------------------
    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self,"Select Save Folder")
        if folder: self.save_path_edit.setText(folder)

    def test_ttl(self):
        if not self.run_trigger_cb.isChecked():
            self.log_event("Enable 'Send TTL' to send manual pulses", "orange")
            return
        self.send_ttl_threaded(
            frequency_hz=self.ttl_frequency_spin.value(),
            duration_ms=self.ttl_duration_spin.value(),
            mode=self.ttl_mode_combo.currentText(),
        )

    def send_ttl_threaded(self, frequency_hz=40, duration_ms=300, mode="Train"):
        if not self.arduino:
            self.open_arduino()
        if not self.arduino:
            return

        def ttl_worker():
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            try:
                with self.ttl_lock:
                    self._append_stim_event(
                        "ttl_burst",
                        mode.lower().replace(" ", "_"),
                        frame_index=self.frames_taken if self.frames_taken else None,
                        duration_s=round(duration_ms / 1000.0, 3),
                        ttl_channel="ArduinoTTL",
                        ttl_pulse_width_ms=1.0 if mode == "Single Pulse" else duration_ms,
                        extra={
                            "ttl_frequency_hz": frequency_hz,
                            "ttl_mode": mode,
                        },
                    )
                    if mode == "Single Pulse":
                        # Send one TTL pulse (1 ms)
                        self.arduino.write(b'H')
                        time.sleep(0.001)
                        self.arduino.write(b'L')
                        self.log_queue.put((ts, f"{mode} sent successfully", "yellow"))
                    else:
                        # Calculate interval between pulses
                        interval = 1.0 / max(1, frequency_hz)
                        pulses = max(1, int(duration_ms / 1000 * frequency_hz))
                        for _ in range(pulses):
                            self.arduino.write(b'H')
                            time.sleep(0.001)  # pulse width 1 ms
                            self.arduino.write(b'L')
                            if interval > 0.001:
                                time.sleep(interval - 0.001)
                        self.log_queue.put((ts, f"{mode} sent {pulses} pulses at {frequency_hz} Hz for {duration_ms} ms successfully", "yellow"))

            except Exception as e:
                self.set_overlay("TTL ERROR", color="red")
                QTimer.singleShot(500, lambda: self.set_overlay("READY", color="green"))
                self.log_event(f"Error sending TTL pulse: {e}", color="red")

        threading.Thread(target=ttl_worker, daemon=True).start()

    def open_arduino(self):
        if self.run_trigger_cb.isChecked():
            try:
                self.arduino = serial.Serial(self.serial_edit.text(),int(self.baud_combo.currentText()),timeout=1)
            except Exception as e:
                self.log_event(f"Arduino error: {e}"); self.arduino=None
        else: 
            self.log_event("Arduino not enabled", color = "red")
            QTimer.singleShot(500, lambda: self.set_overlay("READY", color = "green"))

        # -------------------- Live Preview --------------------
    def toggle_live(self):
        if self.core is None and not self.simulated_mode:
            self.log_event("Cannot start live preview: core not ready", color="red")
            return
        if self.core is not None and not self.simulated_mode:
            if not self.ensure_camera_runtime_started():
                return

        if self.live_window and self.live_window.isVisible():
            self.live_window.hide()
            self.set_overlay("LIVE OFF", "red")
            return

        if self.live_window is None:
            self.live_window = LivePreviewWindow(core=self.core, lock=self.camera_lock)
        self.live_window.show()
        self.set_overlay("LIVE ON", "green")

        if self.live_thread is None or not self.live_thread.isRunning():
            fps = int(self.fps_combo.currentText())
            if self.simulated_mode:
                self.live_thread = SimulatedPreviewThread(preview_fps=fps, frame_shape=(600, 600))
            else:
                self.live_thread = LivePreviewThread(core=self.core, lock=self.camera_lock, preview_fps=fps)
            self.live_thread.image_ready.connect(self.update_live_frame)
            self.live_thread.log_event_signal.connect(self.log_event)
            self.live_thread.start()

    def start_live(self):
        if self.core is None:
            self.log_event("Cannot start live: core not ready", "red")
            return
        if not self.ensure_camera_runtime_started():
            return
        if not self.live_window:
            self.live_window = LivePreviewWindow(core=self.core, lock=self.camera_lock)
        self.live_window.show()

        if self.live_thread and self.live_thread.isRunning():
            self.live_thread.stop()
            self.live_thread.wait()

        fps = int(self.fps_combo.currentText())
        self.live_thread = LivePreviewThread(self.core, lock=self.camera_lock, preview_fps=fps)
        self.live_thread.image_ready.connect(self.update_live_frame)
        self.live_thread.start()
        self.overlay_label.setText("LIVE ON")

    def update_live_frame(self, arr):
        if self.suspend_live_updates:
            return
        self.last_frame = self._normalize_frame_shape(arr)
        self.update_live_display()

    def full_levels(self):
        self.level_low_slider.setValue(0)
        self.level_high_slider.setValue(65535)
        self.update_live_display()

    def auto_levels_from_frame(self):
        if getattr(self, "last_frame", None) is None:
            return
        arr = self.last_frame.astype(np.float32)
        lo = int(np.percentile(arr, self.hist_auto_percentile_low))
        hi = int(np.percentile(arr, self.hist_auto_percentile_high))
        lo = max(0, min(65534, lo))
        hi = max(lo + 1, min(65535, hi))
        self.level_low_slider.setValue(lo)
        self.level_high_slider.setValue(hi)
        self.update_live_display()

    def update_live_display(self):
        if self.suspend_live_updates:
            return
        if getattr(self, "last_frame", None) is None:
            return
        if self.live_window is None or not hasattr(self.live_window, "label"):
            return

        arr = self.last_frame.astype(np.float32)

        low_val = int(self.level_low_slider.value())
        high_val = int(self.level_high_slider.value())
        if high_val <= low_val:
            high_val = min(65535, low_val + 1)
            self.level_high_slider.blockSignals(True)
            self.level_high_slider.setValue(high_val)
            self.level_high_slider.blockSignals(False)
        rng = max(high_val - low_val, 1e-6)
        norm = np.clip((arr - low_val) / rng, 0.0, 1.0)

        arr8 = (norm * 255.0).astype(np.uint8)
        self.update_histogram(arr)

        qimg = QImage(arr8.copy(), arr8.shape[1], arr8.shape[0], QImage.Format_Grayscale8)
        pixmap = QPixmap.fromImage(qimg)

        zoom = int(self.zoom_combo.currentText()[:-1]) / 100.0
        if abs(zoom - 1.0) < 1e-6:
            # Treat 100% as "fit preview window without distortion"
            w, h = self.live_window.label.width(), self.live_window.label.height()
            self.live_window.label.setPixmap(
                pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.FastTransformation)
            )
        else:
            w, h = int(pixmap.width() * zoom), int(pixmap.height() * zoom)
            self.live_window.label.setPixmap(
                pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.FastTransformation)
            )

    def update_histogram(self, arr16):
        if self.suspend_live_updates:
            return
        if arr16 is None or arr16.size == 0:
            return
        hist_h, hist_w = 100, 320
        canvas = np.full((hist_h, hist_w), 28, dtype=np.uint8)
        sample = np.asarray(arr16)
        if sample.ndim == 1:
            sample = sample.reshape((1, sample.size))
        elif sample.ndim > 2:
            sample = np.squeeze(sample)
            if sample.ndim > 2:
                sample = sample[..., 0]
        sample = sample[::2, ::2]
        hist, _ = np.histogram(sample, bins=self.hist_bins, range=(0, 65536))
        hist = hist.astype(np.float32)
        if hist.max() > 0:
            hist /= hist.max()

        bin_w = max(1, hist_w // self.hist_bins)
        for i, v in enumerate(hist):
            x0 = i * bin_w
            x1 = min(hist_w, x0 + bin_w)
            y = int((1.0 - v) * (hist_h - 1))
            canvas[y:hist_h, x0:x1] = 210

        low_x = int((self.level_low_slider.value() / 65535.0) * (hist_w - 1))
        high_x = int((self.level_high_slider.value() / 65535.0) * (hist_w - 1))
        canvas[:, max(0, low_x - 1):min(hist_w, low_x + 1)] = 90
        canvas[:, max(0, high_x - 1):min(hist_w, high_x + 1)] = 255

        qimg = QImage(canvas.data, hist_w, hist_h, hist_w, QImage.Format_Grayscale8)
        self.hist_label.setPixmap(QPixmap.fromImage(qimg.copy()))
        self.hist_stats_label.setText(
            f"min: {int(sample.min())}  max: {int(sample.max())}  mean: {float(sample.mean()):.1f}"
        )

    def _sync_acquisition_mode_ui(self):
        mode = self.acquisition_mode_combo.currentText() if hasattr(self, "acquisition_mode_combo") else "Burst + TTL"
        ttl_enabled = mode == "Burst + TTL"
        if hasattr(self, "run_trigger_cb"):
            self.run_trigger_cb.setChecked(ttl_enabled)
            self.run_trigger_cb.setEnabled(mode != "Continuous")
        if hasattr(self, "trigger_time_spin"):
            # Keep this editable in all modes so users can stage TTL timing
            # before switching modes without the control feeling "broken".
            self.trigger_time_spin.setEnabled(True)
        if hasattr(self, "ttl_frequency_spin"):
            self.ttl_frequency_spin.setEnabled(ttl_enabled)
        if hasattr(self, "ttl_duration_spin"):
            self.ttl_duration_spin.setEnabled(ttl_enabled)
        if hasattr(self, "ttl_mode_combo"):
            self.ttl_mode_combo.setEnabled(ttl_enabled)

    def _execute_selected_acquisition(self, mode):
        if mode == "Continuous":
            self.start_continuous_recording()
            return
        self.run_trigger_cb.setChecked(mode == "Burst + TTL")
        self.start_experiment()

    def _update_prestart_countdown(self):
        if self.prestart_timer is None:
            return
        self.prestart_remaining_s -= 1
        if self.prestart_remaining_s > 0:
            self.set_overlay(f"START IN {self.prestart_remaining_s}", color="yellow")
            return

        mode = self.pending_acquisition_mode or "Continuous"
        self.prestart_timer.stop()
        self.prestart_timer.deleteLater()
        self.prestart_timer = None
        self.pending_acquisition_mode = None
        self.prestart_remaining_s = 0
        self.start_btn.setEnabled(True)
        self.set_overlay("STARTING...", color="blue")
        self.log_event(f"Pre-start countdown complete. Starting {mode.lower()} acquisition.", "blue")
        QTimer.singleShot(0, lambda: self._execute_selected_acquisition(mode))

    def _start_prestart_countdown(self, mode, delay_s):
        if self.continuous_recording or self.experiment_running:
            self.log_event("An acquisition is already active", "orange")
            return
        if self.prestart_timer is not None:
            self.log_event("A pre-start countdown is already active", "orange")
            return

        self.pending_acquisition_mode = mode
        self.prestart_remaining_s = int(delay_s)
        self.set_overlay(f"START IN {self.prestart_remaining_s}", color="yellow")
        self.log_event(f"Pre-start countdown started for {mode.lower()}: {self.prestart_remaining_s} s", "yellow")
        self.start_btn.setEnabled(False)
        self.prestart_timer = QTimer(self)
        self.prestart_timer.timeout.connect(self._update_prestart_countdown)
        self.prestart_timer.start(1000)

    def start_selected_acquisition(self):
        mode = self.acquisition_mode_combo.currentText() if hasattr(self, "acquisition_mode_combo") else "Burst + TTL"
        delay_s = int(self.prestart_delay_spin.value()) if hasattr(self, "prestart_delay_spin") else 0
        if delay_s > 0:
            self._start_prestart_countdown(mode, delay_s)
            return
        self._execute_selected_acquisition(mode)

    def stop_selected_acquisition(self):
        if self.prestart_timer is not None:
            self.prestart_timer.stop()
            self.prestart_timer.deleteLater()
            self.prestart_timer = None
            self.pending_acquisition_mode = None
            self.prestart_remaining_s = 0
            self.start_btn.setEnabled(True)
            self.set_overlay("READY", color="green")
            self.log_event("Pre-start countdown canceled", "orange")
            return
        if self.continuous_recording:
            self.stop_continuous_recording()
            return
        if self.experiment_running:
            self.stop_experiment()
            return
        self.log_event("No active acquisition to stop", "orange")
    # -------------------- Experiment --------------------
    def start_experiment(self):
        if self.continuous_recording:
            self.log_event("Stop continuous recording before starting experiment", "orange")
            return

        if self.arduino is None and self.run_trigger_cb.isChecked():
            self.open_arduino()
            self.arduino.flushInput()  # Clear any previous input
            self.arduino.flushOutput()  # Clear any previous output
            if self.arduino is None:
                self.log_event("Cannot start experiment: Arduino not connected", "red")
                return
            
        if not self.core and not self.simulated_mode:
            self.log_event("Cannot start experiment: core not ready", "red")
            return
        if self.core is not None and not self.simulated_mode:
            if not self.ensure_camera_runtime_started():
                return
        
        if not self.record_cb.isChecked():
            self.record_cb.setChecked(True)
            self.log_event("Recording enabled for experiment", "yellow")
        
        burst_mode = "burst_ttl" if self.run_trigger_cb.isChecked() else "burst_manual"
        self._prepare_session_structure(burst_mode)
        self.acquired_frame_rate_hz = None
        self.session_start_epoch = time.time()
        self._append_stim_event(
            "manual_annotation",
            "acquisition_start",
            notes="Burst acquisition session started",
        )

        self.experiment_duration_s = float(self.total_time_spin.value()) * 60.0  # convert to seconds
        self.burst_duration_s = float(self.burst_duration_spin.value())
        self.pause_between_bursts_s = float(self.wait_interval_spin.value())
        self.start_time = time.time()
        self.experiment_running = True
        self.suspend_live_updates = True
        self.burst_index = 0
        self.burst_active = False
        self.current_burst_frames = []
        self.current_burst_idx = 0
        self.target_fps = int(self.fps_combo.currentText())
        cycle_s = self.burst_duration_s + self.pause_between_bursts_s
        requested_bursts = int(self.num_bursts_spin.value())
        if requested_bursts > 0:
            self.total_bursts = requested_bursts
            self.log_event(f"Using fixed burst count: {self.total_bursts}", "yellow")
        else:
            self.total_bursts = int(self.experiment_duration_s / cycle_s)
            self.log_event(
                f"Using auto burst count from duration: {self.total_bursts} bursts",
                "yellow",
            )
        self.burst_job_queue = Queue(maxsize=2000)
        self.writer_thread = FrameWriterThread(self.burst_job_queue)
        self.writer_thread.log_event_signal.connect(self.log_event)
        self.writer_thread.start()

        if self.core is not None:
            cam = self.core.getCameraDevice()
            try:
                self.core.setProperty(cam,"ClearMode", "Never")
                self.core.setProperty(cam,"ClearCycles", 2)
            except Exception:
                pass

        if self.live_thread is None or not self.live_thread.isRunning():
            fps = int(self.fps_combo.currentText())
            if self.simulated_mode:
                self.live_thread = SimulatedPreviewThread(preview_fps=fps, frame_shape=(600, 600))
            else:
                self.live_thread = LivePreviewThread(self.core, lock=self.camera_lock, preview_fps=fps)
            self.live_thread.image_ready.connect(self.update_live_frame)
            self.live_thread.log_event_signal.connect(self.log_event)
            self.live_thread.start()

        if not self.experiment_stream_connected:
            self.live_thread.new_frame.connect(self.on_experiment_frame)
            self.experiment_stream_connected = True
        if hasattr(self.live_thread, "set_preview_enabled"):
            self.live_thread.set_preview_enabled(False)

        if self.live_window is None or not self.live_window.isVisible():
            self.live_window = LivePreviewWindow(core=self.core, lock=self.camera_lock)
            self.live_window.show()

        self.set_overlay("EXPERIMENT IN PROGRESS...", color="blue")
        QTimer.singleShot(0, self.start_burst_and_ttl)

    def on_burst_started(self, burst_idx):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_queue.put((ts, f"Burst {burst_idx} started", "green"))
        self._append_stim_event(
            "burst_start",
            f"burst_{burst_idx}",
            frame_index=self.frames_taken if self.frames_taken else None,
            duration_s=float(self.burst_duration_spin.value()),
        )

    def on_experiment_frame(self, frame):
        if not self.experiment_running or not self.burst_active:
            return
        f = self._normalize_frame_shape(frame)
        if self.current_burst_count < self.current_burst_capacity:
            self.current_burst_frames[self.current_burst_count] = f
        else:
            self.current_burst_frames.append(f)
            self.current_burst_capacity += 1
        self.current_burst_count += 1

    def start_burst_and_ttl(self):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        ttl_freq = self.ttl_frequency_spin.value()
        ttl_duration = self.ttl_duration_spin.value()
        burst_number = self.burst_index + 1
        self.burst_index = burst_number
        burst_duration = float(self.burst_duration_spin.value())
        ttl_delay_ms = int(self.trigger_time_spin.value())

        if not self.experiment_running:
            return

        if burst_number == 1:
            self.log_queue.put((ts, f"Burst 1 scheduled to start immediately (TTL in {ttl_delay_ms} ms)", "orange"))
        self.current_burst_idx = burst_number
        est_fps = max(60.0, self.last_burst_fps if self.last_burst_fps > 0 else 80.0)
        self.current_burst_capacity = int(burst_duration * est_fps * 1.3) + 16
        self.current_burst_frames = [None] * self.current_burst_capacity
        self.current_burst_count = 0
        self.burst_active = True
        self.on_burst_started(burst_number)
        QTimer.singleShot(ttl_delay_ms,lambda: self.send_ttl_threaded(frequency_hz=ttl_freq,duration_ms=ttl_duration,mode=self.ttl_mode_combo.currentText()))
        QTimer.singleShot(int(burst_duration * 1000), lambda idx=burst_number: self.end_current_burst(idx))

    def end_current_burst(self, burst_idx):
        if not self.experiment_running:
            return
        if not self.burst_active:
            return
        self.burst_active = False
        frames_array = [f for f in self.current_burst_frames[:self.current_burst_count] if f is not None]
        self.current_burst_frames = []
        self.current_burst_capacity = 0
        self.current_burst_count = 0
        self._append_stim_event(
            "burst_end",
            f"burst_{burst_idx}",
            frame_index=self.frames_taken if self.frames_taken else None,
            duration_s=float(self.burst_duration_spin.value()),
        )
        self.on_burst_done(burst_idx, frames_array)

    def on_burst_done(self, burst_idx, frames_array):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        frames_array = [self._normalize_frame_shape(f) for f in frames_array]
        burst_seconds = max(0.001, float(self.burst_duration_spin.value()))
        self.last_burst_fps = len(frames_array) / burst_seconds
        self._update_acquired_frame_rate(self.last_burst_fps)
        self.log_queue.put((ts, f"Burst {burst_idx} done, {len(frames_array)} frames captured ({self.last_burst_fps:.1f} fps)", "green"))

    # Save burst to disk
        save_folder = self.raw_folder or self.session_folder
        os.makedirs(save_folder, exist_ok=True)
        out_path = os.path.join(save_folder, f"burst_{burst_idx:03d}.tif")
    
    # Queue the array to the writer
        self.burst_job_queue.put((out_path, frames_array))
        self.log_queue.put((ts, f"Burst {burst_idx} for Mouse {self.mouse_id_edit.text()} saved to: {save_folder}", "green"))
        burst_interval = float(self.wait_interval_spin.value()) * 1000
        ttl_delay_ms = int(self.trigger_time_spin.value())
        burst_number = self.burst_index + 1

        if self.experiment_running and self.burst_index < self.total_bursts:
            self.log_queue.put((ts, f"Burst {burst_number} scheduled in {(burst_interval) / 1000:.1f} s (TTL after {ttl_delay_ms} ms)" , "orange"))
            QTimer.singleShot(int(burst_interval), self.start_burst_and_ttl)
        elif self.experiment_running:
            self.finish_experiment()
            
    def finish_experiment(self):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.stop_experiment()
        self.log_queue.put((ts, "All bursts completed", "orange"))

    def stop_experiment(self):
        self.experiment_running = False
        self.experiment_stopped = True
        self.suspend_live_updates = False
        self.burst_active = False
        self.current_burst_frames = []
        self.current_burst_capacity = 0
        self.current_burst_count = 0

        if self.core is not None:
            cam = self.core.getCameraDevice()
            self.core.setProperty(cam,"ClearMode", "Pre-Exposure")
            self.core.setProperty(cam,"ClearCycles", 2)

        if self.burst_job_queue is not None:
            start = time.time()
            while getattr(self.burst_job_queue, "unfinished_tasks", 0) > 0 and (time.time() - start) < 30:
                time.sleep(0.05)
        if self.writer_thread and self.writer_thread.isRunning():
            self.writer_thread.stop()

        if self.experiment_stream_connected and self.live_thread is not None:
            try:
                self.live_thread.new_frame.disconnect(self.on_experiment_frame)
            except Exception:
                pass
            self.experiment_stream_connected = False
        if self.live_thread is not None and hasattr(self.live_thread, "set_preview_enabled"):
            self.live_thread.set_preview_enabled(True)

        self._append_stim_event(
            "manual_annotation",
            "acquisition_stop",
            notes="Burst acquisition session stopped",
        )
        self.set_overlay("EXPERIMENT STOPPED", color="red")
        QTimer.singleShot(2000, lambda: self.set_overlay("READY", color="green"))

    def start_continuous_recording(self):
        if self.experiment_running:
            self.log_event("Experiment is running. Use Stop Experiment first.", "orange")
            return
        if self.continuous_recording:
            self.log_event("Continuous recording already active", "orange")
            return
        if self.live_thread is None or not self.live_thread.isRunning():
            self.log_event("Cannot record: live preview thread is not running", "red")
            return

        self.continuous_save_folder = self._prepare_session_structure("continuous")
        self.acquired_frame_rate_hz = None
        self.session_start_epoch = time.time()
        self._append_stim_event(
            "manual_annotation",
            "acquisition_start",
            notes="Continuous acquisition session started",
        )

        if self.writer_thread is None or not self.writer_thread.isRunning():
            self.burst_job_queue = Queue(maxsize=2000)
            self.writer_thread = FrameWriterThread(self.burst_job_queue)
            self.writer_thread.log_event_signal.connect(self.log_event)
            self.writer_thread.start()

        self.continuous_frames_buffer = []
        self.continuous_chunk_index = 0
        self.continuous_frame_counter = 0
        self.continuous_recording = True
        self.live_thread.new_frame.connect(self.on_continuous_frame)
        self.set_overlay("RECORDING", color="red")
        self.log_event(f"Continuous recording started: {self.continuous_save_folder}", "green")

    def on_continuous_frame(self, frame):
        if not self.continuous_recording:
            return

        self.continuous_frames_buffer.append(self._normalize_frame_shape(frame))
        self.continuous_frame_counter += 1
        if len(self.continuous_frames_buffer) >= self.continuous_chunk_size:
            self.flush_continuous_recording_chunk()

    def flush_continuous_recording_chunk(self):
        if not self.continuous_frames_buffer:
            return
        if self.burst_job_queue is None:
            return

        chunk_path = os.path.join(
            self.continuous_save_folder,
            f"continuous_{self.continuous_chunk_index:04d}.tif"
        )
        frames_to_save = self.continuous_frames_buffer
        self.continuous_frames_buffer = []
        self.continuous_chunk_index += 1
        self.burst_job_queue.put((chunk_path, frames_to_save))

    def stop_continuous_recording(self):
        if not self.continuous_recording:
            self.log_event("Continuous recording is not active", "orange")
            return

        self.continuous_recording = False
        try:
            self.live_thread.new_frame.disconnect(self.on_continuous_frame)
        except Exception:
            pass

        self.flush_continuous_recording_chunk()
        if self.burst_job_queue is not None:
            start = time.time()
            while getattr(self.burst_job_queue, "unfinished_tasks", 0) > 0 and (time.time() - start) < 30:
                time.sleep(0.05)
        elapsed_s = max(0.001, time.time() - getattr(self, "session_start_epoch", time.time()))
        measured_fps = float(self.continuous_frame_counter) / elapsed_s if self.continuous_frame_counter > 0 else 0.0
        self._update_acquired_frame_rate(measured_fps)
        self.set_overlay("READY", color="green")
        self._append_stim_event(
            "manual_annotation",
            "acquisition_stop",
            notes="Continuous acquisition session stopped",
        )
        self.log_event(
            f"Continuous recording stopped. Saved {self.continuous_frame_counter} frames to {self.continuous_save_folder} ({measured_fps:.1f} fps)",
            "green",
        )

    def core_reset(self):
        # stop acquisition safely
        try:
            if getattr(self, "core", None) and getattr(self.core, "isSequenceRunning", lambda: False)():
                self.core.stopSequenceAcquisition()
        except Exception:
            pass

        if getattr(self, "core", None):
            try:
                self.core.reset()
            except Exception as e:
                self.log_event(f"Error resetting core: {e}", "red")
            # purge the attribute and recreate a fresh instance
            del self.core
            self.core = None

        # restart core thread fresh
        try:
            if getattr(self, "core_thread", None) and self.core_thread.isRunning():
                self.core_thread.stop()
            self.core_thread = LoadCoreThread(self.cfg_path)
            self.core_thread.core_loaded.connect(self.on_core_loaded)
            self.request_core_load()
        except Exception as e:
            self.log_event(f"Failed to restart core thread: {e}", "red")
        self.log_event("Core Refreshed", "red")


    # -------------------- Settings --------------------
    def load_settings(self):
        try:
            with open(self.settings_file, "r") as f:
                settings = json.load(f)
            self.settings = settings
            self.save_path_edit.setText(settings.get("save_path", ""))
            self.mouse_id_edit.setText(settings.get("mouse_id", "Mouse_001"))
            self.expt_type_edit.setText(settings.get("expt_type", "GCamp"))
            self.final_titer_edit.setText(settings.get("final_titer", "e11"))
            self.genotype_combo.setCurrentText(settings.get("genotype", "APOE3"))
            self.genotype_other_edit.setText(settings.get("genotype_other", ""))
            self.age_group_combo.setCurrentText(settings.get("age_group", "Young"))
            self.sex_combo.setCurrentText(settings.get("sex", "Male"))
            self.slice_spin.setValue(settings.get("slice_number", 1))
            self.kcl_present_cb.setChecked(settings.get("kcl_present_at_start", False))
            self.kcl_concentration_spin.setValue(float(settings.get("kcl_concentration_mM") or 0.0))
            self.drug_name_edit.setText(settings.get("drug_name", ""))
            self.drug_present_cb.setChecked(settings.get("drug_present_at_start", False))
            self.drug_concentration_edit.setText(settings.get("drug_concentration", ""))
            self.level_low_slider.setValue(settings.get("level_low", 500))
            self.level_high_slider.setValue(settings.get("level_high", 60000))
            self.project_name_edit.setText(settings.get("project_name", settings.get("expt_name", "Stim_Exp")))
            self.condition_subtype_edit.setText(settings.get("condition_subtype", settings.get("expt_type", "GCamp")))
            self.total_time_spin.setValue(settings.get("total_time", 5))
            self.wait_interval_spin.setValue(settings.get("wait_interval", 10))
            self.num_bursts_spin.setValue(settings.get("num_bursts", 0))
            self.exp_spin.setValue(settings.get("exp", 10))
            self.prestart_delay_spin.setValue(int(settings.get("pre_acquisition_offset_s", 0)))
            self.zoom_combo.setCurrentText(settings.get("zoom", "100%"))
            # self.batch_size_spin.setValue(settings.get("batch_size", 500))
            self.trigger_time_spin.setValue(settings.get("trigger_time", 2))
            self.serial_edit.setText(settings.get("arduino_port", "COM5"))
            self.baud_combo.setCurrentText(settings.get("baud_rate", "57600"))
        except FileNotFoundError:
            self.log_event("Settings file not found, using defaults.")        

    def save_settings(self):
        """Save current GUI settings to JSON file."""
        self.settings.update({
            "save_path": self.save_path_edit.text(),
            "project_name": self.project_name_edit.text(),
            "condition_subtype": self.condition_subtype_edit.text(),
            "expt_name": self.project_name_edit.text(),
            "mouse_id": self.mouse_id_edit.text(),
            "expt_type": self.condition_subtype_edit.text(),
            "final_titer": self.final_titer_edit.text(),
            "genotype": self.genotype_combo.currentText(),
            "genotype_other": self.genotype_other_edit.text(),
            "age_group": self.age_group_combo.currentText(),
            "sex": self.sex_combo.currentText(),
            "slice_number": self.slice_spin.value(),
            "kcl_present_at_start": self.kcl_present_cb.isChecked(),
            "kcl_concentration_mM": self.kcl_concentration_spin.value(),
            "drug_name": self.drug_name_edit.text(),
            "drug_present_at_start": self.drug_present_cb.isChecked(),
            "drug_concentration": self.drug_concentration_edit.text(),
            "total_time": self.total_time_spin.value(),
            "wait_interval": self.wait_interval_spin.value(),
            "num_bursts": self.num_bursts_spin.value(),
            #"batch_size": self.batch_size_spin.value(),
            "trigger_time": self.trigger_time_spin.value(),
            "pre_acquisition_offset_s": self.prestart_delay_spin.value(),
            "level_low": self.level_low_slider.value(),
            "level_high": self.level_high_slider.value(),
            "zoom": self.zoom_combo.currentText(),
            "arduino_port": self.serial_edit.text(),
            "baud_rate": self.baud_combo.currentText(),
            "send_ttl": self.run_trigger_cb.isChecked(),
            "record": self.record_cb.isChecked(),
            "exp": self.exp_spin.value()
        })

        try:
            with open(self.settings_file, "w") as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            self.log_event(f"Error saving settings: {e}")

    def closeEvent(self, event):
    # Stop pending pre-start countdown
        if self.prestart_timer is not None:
            self.prestart_timer.stop()
            self.prestart_timer.deleteLater()
            self.prestart_timer = None
            self.pending_acquisition_mode = None
            self.prestart_remaining_s = 0

    # Stop continuous recording cleanly
        if self.continuous_recording:
            self.stop_continuous_recording()

    # Stop live preview thread
        if self.live_thread and self.live_thread.isRunning():
            self.live_thread.stop()
            self.live_thread.wait(2000)
            self.live_thread = None

    # Stop writer thread
        if self.writer_thread and self.writer_thread.isRunning():
            self.writer_thread.stop()
            self.writer_thread.wait(2000)
            self.writer_thread = None

    # Close Arduino
        if getattr(self, "arduino", None) and getattr(self.arduino, "is_open", False):
            self.arduino.close()

    # Close live window
        if self.live_window:
            self.live_window.close()

    # Reset camera core to release all devices
        if getattr(self, "core", None):
            try:
                self.core.stopSequenceAcquisition()
                self.core.reset()
            except Exception as e:
                self.log_event(f"Error resetting core: {e}", "red")
            del self.core
            self.core = None

    # Save settings
        self.save_settings()
        event.accept()
        super().closeEvent(event)

# -------------------- Main --------------------
if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()  # Windows-safe

    cfg_path = "C:\\Program Files\\Micro-Manager-2.0\\Scientifica.cfg"  # Adjust  as needed

    app = QApplication(sys.argv)
    if not os.path.exists(cfg_path):
        QMessageBox.critical(
            None,
            "Missing Config",
            f"Could not find Micro-Manager config file:\n{cfg_path}\n\nUpdate cfg_path in the script.",
        )
        sys.exit(1)

    gui = LiveImagingGUI(cfg_path)
    gui.show()

    # core_thread = LoadCoreThread(cfg_path)
    # core_thread.core_loaded.connect(gui.on_core_loaded)
    # core_thread.start()

    def cleanup():
        # Safe stop live thread
        if getattr(gui, "live_thread", None) and gui.live_thread.isRunning():
            gui.live_thread.stop()
            gui.live_thread.wait()

        # Safe stop writer thread
        if getattr(gui, "writer_thread", None) and gui.writer_thread.isRunning():
            gui.writer_thread.stop()
            gui.writer_thread.wait()

    app.aboutToQuit.connect(cleanup)
    sys.exit(app.exec_())
