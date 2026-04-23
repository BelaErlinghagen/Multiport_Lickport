import json
import os
from multiprocessing import Process, Value

from PyQt5 import QtWidgets, QtGui, QtCore

from data_saving import saving_process


class ExperimentPage(QtWidgets.QWidget):
    """Experiment control panel.

    Section order (top → bottom):
      1. Experiment Info  — Cohort / Mouse / Session editable comboboxes
                            (auto-populated by scanning data_path from shared_states)
      2. Recording        — Session duration, Start/Stop buttons,
                            Camera/Sensor/DLC checkboxes,
                            wired to data_saving.saving_process + state machine
      3. Protocol         — placeholder
    """

    def __init__(self, data_sources: dict):
        super().__init__()
        # WA_OpaquePaintEvent: Qt skips its background pre-fill before paintEvent.
        # Our paintEvent fills every pixel, so the widget is never uninitialized.
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)

        self._data_sources = data_sources

        # State-machine flags (Value objects from main.py via data_sources).
        # Stored as instance attrs so the GC does not destroy them while in use.
        self._sm_active    = data_sources.get("sm_active")
        self._sm_stop      = data_sources.get("sm_stop")
        self._session_done = data_sources.get("session_done")
        self._protocol_queue    = data_sources.get("protocol_queue")
        self._loaded_protocol   = None   # dict set by _browse_protocol()
        self._loaded_protocol_path = None  # filesystem path of loaded file

        # Saving-process handles.  All Value objects must be kept alive here:
        # if they go out of scope the GC calls sem_unlink, destroying the POSIX
        # semaphore before the spawned child can open it (FileNotFoundError in
        # SemLock._rebuild).
        self._saving_proc    = None
        self._saving_running = None
        self._cam_flag       = None
        self._sensor_flag    = None
        self._dlc_flag       = None

        # Timer that polls session_done so recording stops automatically when
        # the state machine finishes the session.
        self._sm_done_timer = QtCore.QTimer(self)
        self._sm_done_timer.setInterval(500)   # check every 500 ms
        self._sm_done_timer.timeout.connect(self._check_session_done)

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(10, 10, 10, 10)

        # ── 1. Experiment Info ────────────────────────────────────
        root.addWidget(self._section_label("Experiment Info"))

        form = QtWidgets.QFormLayout()
        form.setSpacing(6)

        self._cohort_cb  = self._make_id_combo()
        self._mouse_cb   = self._make_id_combo()
        self._session_cb = self._make_id_combo()

        form.addRow("Cohort ID:",  self._cohort_cb)
        form.addRow("Mouse ID:",   self._mouse_cb)
        form.addRow("Session ID:", self._session_cb)
        root.addLayout(form)

        # Populate cohort list on construction; cascade on change
        self._populate_cohorts()
        self._cohort_cb.currentTextChanged.connect(self._on_cohort_changed)
        self._mouse_cb.currentTextChanged.connect(self._on_mouse_changed)

        root.addWidget(self._separator())

        # ── 2. Selected Protocol ──────────────────────────────────
        root.addWidget(self._section_label("Selected Protocol"))

        proto_row = QtWidgets.QHBoxLayout()
        self._proto_path_lbl = QtWidgets.QLabel("No protocol selected")
        self._proto_path_lbl.setStyleSheet("color:#888; font-size:10px;")
        self._proto_path_lbl.setWordWrap(True)
        proto_browse_btn = QtWidgets.QPushButton("Browse…")
        proto_browse_btn.setFixedWidth(70)
        proto_browse_btn.clicked.connect(self._browse_protocol)
        proto_row.addWidget(self._proto_path_lbl, 1)
        proto_row.addWidget(proto_browse_btn)
        root.addLayout(proto_row)

        # Summary box (hidden until a protocol is loaded)
        self._proto_summary = QtWidgets.QFrame()
        self._proto_summary.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self._proto_summary.setStyleSheet(
            "QFrame { background:#222; border:1px solid #444; border-radius:4px; }"
        )
        summary_layout = QtWidgets.QVBoxLayout(self._proto_summary)
        summary_layout.setContentsMargins(8, 6, 8, 6)
        summary_layout.setSpacing(2)
        self._sum_session = QtWidgets.QLabel()
        self._sum_rewards = QtWidgets.QLabel()
        self._sum_trial   = QtWidgets.QLabel()
        for lbl in (self._sum_session, self._sum_rewards, self._sum_trial):
            lbl.setStyleSheet("color:#ccc; font-size:10px;")
            summary_layout.addWidget(lbl)
        self._proto_summary.setVisible(False)
        root.addWidget(self._proto_summary)

        root.addWidget(self._separator())

        # ── 3. Recording ─────────────────────────────────────────
        root.addWidget(self._section_label("Recording"))

        # Data-stream checkboxes
        flag_row = QtWidgets.QHBoxLayout()
        self._cam_chk    = QtWidgets.QCheckBox("Camera")
        self._sensor_chk = QtWidgets.QCheckBox("Sensors")
        self._dlc_chk    = QtWidgets.QCheckBox("DeepLabCut")
        self._cam_chk.setChecked(True)
        self._sensor_chk.setChecked(True)
        for chk in (self._cam_chk, self._sensor_chk, self._dlc_chk):
            flag_row.addWidget(chk)
        flag_row.addStretch()
        root.addLayout(flag_row)

        # Start / Stop buttons
        btn_row = QtWidgets.QHBoxLayout()

        self._start_btn = QtWidgets.QPushButton("START RECORDING")
        self._start_btn.setFixedHeight(36)
        self._start_btn.setStyleSheet(
            "QPushButton { background:#1a6b1a; color:#fff; border-radius:4px; font-weight:bold; }"
            "QPushButton:hover { background:#247a24; }"
            "QPushButton:disabled { background:#333; color:#666; }"
        )
        self._start_btn.clicked.connect(self._start_recording)

        self._stop_btn = QtWidgets.QPushButton("STOP RECORDING")
        self._stop_btn.setFixedHeight(36)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet(
            "QPushButton { background:#8b0000; color:#fff; border-radius:4px; font-weight:bold; }"
            "QPushButton:hover { background:#b00000; }"
            "QPushButton:disabled { background:#333; color:#666; }"
        )
        self._stop_btn.clicked.connect(self._stop_recording)

        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._stop_btn)
        root.addLayout(btn_row)

        self._rec_status = QtWidgets.QLabel("Idle")
        self._rec_status.setStyleSheet("color:#aaa; font-size:10px;")
        root.addWidget(self._rec_status)

        root.addStretch()

    # ── Background ────────────────────────────────────────────────

    def paintEvent(self, event):
        QtGui.QPainter(self).fillRect(event.rect(), QtGui.QColor("#2b2b2b"))

    # ── Layout helpers ────────────────────────────────────────────

    @staticmethod
    def _section_label(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet("color:#aaa; font-size:11px; font-weight:bold;")
        return lbl

    @staticmethod
    def _separator() -> QtWidgets.QFrame:
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet("color:#444;")
        return line

    @staticmethod
    def _make_id_combo() -> QtWidgets.QComboBox:
        cb = QtWidgets.QComboBox()
        cb.setEditable(True)
        cb.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        return cb

    # ── Directory scanning ────────────────────────────────────────

    @staticmethod
    def _list_subdirs(path: str) -> list:
        """Return sorted list of immediate sub-directory names under path."""
        try:
            return sorted(
                d for d in os.listdir(path)
                if os.path.isdir(os.path.join(path, d))
            )
        except OSError:
            return []

    def _data_path(self) -> str:
        try:
            from shared_states import data_path
            return data_path
        except Exception:
            return ""

    def _populate_cohorts(self):
        current = self._cohort_cb.currentText()
        self._cohort_cb.blockSignals(True)
        self._cohort_cb.clear()
        self._cohort_cb.addItems(self._list_subdirs(self._data_path()))
        idx = self._cohort_cb.findText(current)
        if idx >= 0:
            self._cohort_cb.setCurrentIndex(idx)
        else:
            self._cohort_cb.setCurrentText(current)
        self._cohort_cb.blockSignals(False)
        self._on_cohort_changed(self._cohort_cb.currentText())

    def _on_cohort_changed(self, cohort: str):
        current = self._mouse_cb.currentText()
        self._mouse_cb.blockSignals(True)
        self._mouse_cb.clear()
        path = os.path.join(self._data_path(), cohort)
        self._mouse_cb.addItems(self._list_subdirs(path))
        idx = self._mouse_cb.findText(current)
        if idx >= 0:
            self._mouse_cb.setCurrentIndex(idx)
        else:
            self._mouse_cb.setCurrentText(current)
        self._mouse_cb.blockSignals(False)
        self._on_mouse_changed(self._mouse_cb.currentText())

    def _on_mouse_changed(self, mouse: str):
        current = self._session_cb.currentText()
        self._session_cb.blockSignals(True)
        self._session_cb.clear()
        path = os.path.join(self._data_path(), self._cohort_cb.currentText(), mouse)
        self._session_cb.addItems(self._list_subdirs(path))
        idx = self._session_cb.findText(current)
        if idx >= 0:
            self._session_cb.setCurrentIndex(idx)
        else:
            self._session_cb.setCurrentText(current)
        self._session_cb.blockSignals(False)

    # ── Recording controls ────────────────────────────────────────

    def _set_id_widgets_enabled(self, enabled: bool):
        for w in (self._cohort_cb, self._mouse_cb, self._session_cb,
                  self._cam_chk, self._sensor_chk, self._dlc_chk):
            w.setEnabled(enabled)

    def _browse_protocol(self):
        """Open a file dialog to pick a protocol JSON; load and show a summary."""
        try:
            from shared_states import protocols_path
            start_dir = protocols_path
        except Exception:
            start_dir = ""

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select Protocol", start_dir, "JSON (*.json)"
        )
        if not path:
            return

        try:
            with open(path, "r") as fh:
                self._loaded_protocol = json.load(fh)
        except Exception as exc:
            self._proto_path_lbl.setText(f"Error loading: {exc}")
            self._proto_summary.setVisible(False)
            return

        self._loaded_protocol_path = path
        self._proto_path_lbl.setText(path)
        self._show_protocol_summary()

    def _show_protocol_summary(self):
        """Populate and reveal the summary box from self._loaded_protocol."""
        d = self._loaded_protocol
        if d is None:
            self._proto_summary.setVisible(False)
            return

        # Session line
        sess = d.get("session", {})
        s_type   = sess.get("type", "?")
        s_length = sess.get("length", "?")
        unit     = "s" if s_type == "time" else "trials"
        self._sum_session.setText(f"Session: {s_type},  {s_length} {unit}")

        # Rewards line
        rew  = d.get("rewards", {})
        cnt  = rew.get("count", "?")
        dist = rew.get("distribution", {}).get("type", "?")
        self._sum_rewards.setText(f"Rewards: {cnt}  ({dist} distribution)")

        # Trial line
        trial  = d.get("trial", {})
        t_type = trial.get("end_type", "?")
        if t_type == "time":
            t_detail = f"{trial.get('duration_s', '?')} s per trial"
        else:
            t_detail = "ends when all rewards collected"
        self._sum_trial.setText(f"Trial: {t_detail}")

        self._proto_summary.setVisible(True)

    def _start_recording(self):
        cohort  = self._cohort_cb.currentText().strip()
        mouse   = self._mouse_cb.currentText().strip()
        session = self._session_cb.currentText().strip()
        if not cohort or not mouse or not session:
            self._rec_status.setText("Fill in Cohort, Mouse and Session IDs first.")
            return

        if self._loaded_protocol is None:
            self._rec_status.setText("Load a protocol first.")
            return

        # Attach session metadata so the SM can write the mouse log.
        protocol_to_send = dict(self._loaded_protocol)
        protocol_to_send["_meta"] = {
            "cohort_id":     cohort,
            "mouse_id":      mouse,
            "session_id":    session,
            "protocol_path": self._loaded_protocol_path or "",
        }

        # Drain any stale entry from a previous run, then push the current protocol.
        if self._protocol_queue is not None:
            while not self._protocol_queue.empty():
                try:
                    self._protocol_queue.get_nowait()
                except Exception:
                    pass
            self._protocol_queue.put_nowait(protocol_to_send)

        # Start saving process
        ds = self._data_sources
        self._saving_running = Value('b', True)
        self._cam_flag    = Value('b', self._cam_chk.isChecked())
        self._sensor_flag = Value('b', self._sensor_chk.isChecked())
        self._dlc_flag    = Value('b', self._dlc_chk.isChecked())

        self._saving_proc = Process(
            target=saving_process,
            args=(
                ds.get("dlc_queue"),      # full-res frames for camera saving
                ds.get("sensor_array"),
                ds.get("pose_queue"),     # DLC pose estimates for CSV
                ds.get("timestamp_value"),
                cohort, mouse, session,
                self._cam_flag, self._sensor_flag, self._dlc_flag,
                self._saving_running,
            ),
            daemon=True,
        )
        self._saving_proc.start()

        # Activate the state machine (it will read protocol from the queue)
        if self._sm_active is not None:
            self._sm_active.value = True

        # Poll every 500 ms to detect when the SM signals session complete
        self._sm_done_timer.start()

        self._set_id_widgets_enabled(False)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._rec_status.setText(f"Recording  {cohort} / {mouse} / {session}")

    def _stop_recording(self):
        # Stop polling and halt the state machine immediately
        self._sm_done_timer.stop()
        if self._sm_stop is not None:
            self._sm_stop.value = True

        if self._saving_running is not None:
            self._saving_running.value = False
        # Give the saving thread ~500 ms to flush the current row, then clean up
        QtCore.QTimer.singleShot(500, self._finalize_stop)

    def _finalize_stop(self):
        if self._saving_proc and self._saving_proc.is_alive():
            self._saving_proc.terminate()
            self._saving_proc.join(timeout=1)
        self._saving_proc    = None
        self._saving_running = None
        self._cam_flag       = None
        self._sensor_flag    = None
        self._dlc_flag       = None

        self._set_id_widgets_enabled(True)
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._rec_status.setText("Idle")

    def _check_session_done(self):
        """Called every 500 ms while recording is active.

        If the state machine signals that its session ended naturally,
        automatically stop the recording.
        """
        if self._session_done is not None and self._session_done.value:
            self._sm_done_timer.stop()
            self._rec_status.setText("Session complete — stopping recording…")
            self._stop_recording()
