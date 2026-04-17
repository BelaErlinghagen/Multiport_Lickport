"""gui/protocol_page.py — Protocol editor tab.

Lets the user create, load, edit, and save behavioural protocols as .json files.
Protocols are stored in shared_states.protocols_path.

Section layout (inside a QScrollArea):
  ── Session ─────────────────────────────────────────────────────
  ── Rewards ─────────────────────────────────────────────────────
      • number of rewards (dropdown 1-16)
      • per-reward config  (dynamic table: duration, probability)
      • distribution       (fixed port map  OR  random + spacing)
      • LED activation     (mode dropdown + optional neighbor count)
      • Beamer placeholder
      • Screens placeholder
  ── Trial ───────────────────────────────────────────────────────
"""

import json
import os

from PyQt5 import QtCore, QtGui, QtWidgets


class ProtocolPage(QtWidgets.QWidget):
    """Full-featured protocol editor."""

    # ── Default protocol ──────────────────────────────────────────────────────
    DEFAULT_PROTOCOL = {
        "session": {"type": "time", "length": 600},
        "rewards": {
            "count": 2,
            "configs": [
                {"id": 1, "duration_ms": 500, "probability": 1.0},
                {"id": 2, "duration_ms": 500, "probability": 1.0},
            ],
            "distribution": {
                "type": "fixed",
                "fixed_map": {"1": 1, "2": 9},
                "min_spacing": 4,
            },
            "led_mode": "reward_only",
            "led_neighbors": 1,
        },
        "trial": {
            "end_type": "time",
            "duration_s": 30,
        },
        "beamer": None,
        "screens": None,
    }

    _LED_MODE_LABELS = {
        "none":         "None (LEDs off during trial)",
        "reward_only":  "Reward locations only",
        "neighbors":    "Reward + N neighbours",
        "all":          "All LEDs",
    }
    _LED_MODE_KEYS = {v: k for k, v in _LED_MODE_LABELS.items()}

    def __init__(self):
        super().__init__()
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)

        self._current_path: str | None = None

        # Dynamic section state — populated by rebuild helpers
        self._reward_rows: list[tuple[QtWidgets.QSpinBox,
                                      QtWidgets.QDoubleSpinBox]] = []
        self._fixed_port_combos: list[QtWidgets.QComboBox] = []
        self._min_spacing_spin: QtWidgets.QSpinBox | None = None
        self._spacing_warn_lbl: QtWidgets.QLabel | None   = None

        self._build_ui()
        self._apply_protocol(self.DEFAULT_PROTOCOL)

    # ── Background ────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        QtGui.QPainter(self).fillRect(event.rect(), QtGui.QColor("#2b2b2b"))

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── File toolbar ──────────────────────────────────────────
        file_bar = QtWidgets.QWidget()
        file_bar.setAutoFillBackground(True)
        file_layout = QtWidgets.QHBoxLayout(file_bar)
        file_layout.setContentsMargins(8, 6, 8, 6)

        self._path_label = QtWidgets.QLabel("No file loaded")
        self._path_label.setStyleSheet("color:#888; font-size:10px;")
        self._path_label.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        file_layout.addWidget(self._path_label)

        for label, slot in [("Browse…", self._load_protocol),
                             ("New",     self._new_protocol),
                             ("Save",    self._save_protocol)]:
            btn = QtWidgets.QPushButton(label)
            btn.setFixedHeight(26)
            btn.clicked.connect(slot)
            file_layout.addWidget(btn)

        root.addWidget(file_bar)
        root.addWidget(self._make_separator())

        # ── Scroll area ───────────────────────────────────────────
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)

        content = QtWidgets.QWidget()
        self._scroll_layout = QtWidgets.QVBoxLayout(content)
        self._scroll_layout.setContentsMargins(10, 10, 10, 20)
        self._scroll_layout.setSpacing(6)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        sl = self._scroll_layout

        # ── Session ───────────────────────────────────────────────
        sl.addWidget(self._section_label("Session"))
        sess_row = QtWidgets.QHBoxLayout()
        sess_row.addWidget(QtWidgets.QLabel("Type:"))
        self._sess_type_combo = QtWidgets.QComboBox()
        self._sess_type_combo.addItems(["Time", "Trials"])
        self._sess_type_combo.setFixedWidth(90)
        sess_row.addWidget(self._sess_type_combo)

        sess_row.addSpacing(16)
        sess_row.addWidget(QtWidgets.QLabel("Length:"))
        self._sess_length_spin = QtWidgets.QSpinBox()
        self._sess_length_spin.setRange(1, 86400)
        self._sess_length_spin.setValue(600)
        self._sess_length_spin.setFixedWidth(80)
        sess_row.addWidget(self._sess_length_spin)
        self._sess_unit_lbl = QtWidgets.QLabel("seconds")
        sess_row.addWidget(self._sess_unit_lbl)
        sess_row.addStretch()
        sl.addLayout(sess_row)

        self._sess_type_combo.currentTextChanged.connect(self._update_sess_unit)
        sl.addWidget(self._make_separator())

        # ── Rewards ───────────────────────────────────────────────
        sl.addWidget(self._section_label("Rewards"))

        count_row = QtWidgets.QHBoxLayout()
        count_row.addWidget(QtWidgets.QLabel("Number of rewards:"))
        self._count_combo = QtWidgets.QComboBox()
        self._count_combo.addItems([str(i) for i in range(1, 17)])
        self._count_combo.setFixedWidth(60)
        count_row.addWidget(self._count_combo)
        count_row.addStretch()
        sl.addLayout(count_row)

        # Reward config container (dynamic)
        sl.addWidget(self._sublabel("Per-reward parameters"))
        self._reward_config_container = QtWidgets.QWidget()
        self._reward_config_container.setLayout(QtWidgets.QVBoxLayout())
        self._reward_config_container.layout().setContentsMargins(0, 0, 0, 0)
        self._reward_config_container.layout().setSpacing(2)
        sl.addWidget(self._reward_config_container)

        # Distribution
        sl.addSpacing(4)
        dist_row = QtWidgets.QHBoxLayout()
        dist_row.addWidget(self._sublabel("Distribution:"))
        self._dist_type_combo = QtWidgets.QComboBox()
        self._dist_type_combo.addItems(["Fixed", "Random"])
        self._dist_type_combo.setFixedWidth(90)
        dist_row.addWidget(self._dist_type_combo)
        dist_row.addStretch()
        sl.addLayout(dist_row)

        # Distribution container (dynamic)
        self._dist_container = QtWidgets.QWidget()
        self._dist_container.setLayout(QtWidgets.QVBoxLayout())
        self._dist_container.layout().setContentsMargins(8, 0, 0, 0)
        self._dist_container.layout().setSpacing(2)
        sl.addWidget(self._dist_container)

        # LED activation
        sl.addSpacing(4)
        led_row = QtWidgets.QHBoxLayout()
        led_row.addWidget(self._sublabel("LED activation:"))
        self._led_mode_combo = QtWidgets.QComboBox()
        self._led_mode_combo.addItems(list(self._LED_MODE_LABELS.values()))
        self._led_mode_combo.setMinimumWidth(200)
        led_row.addWidget(self._led_mode_combo)
        led_row.addStretch()
        sl.addLayout(led_row)

        self._neighbor_row = QtWidgets.QWidget()
        nb_layout = QtWidgets.QHBoxLayout(self._neighbor_row)
        nb_layout.setContentsMargins(0, 0, 0, 0)
        nb_layout.addWidget(QtWidgets.QLabel("    Neighbours on each side:"))
        self._led_neighbors_spin = QtWidgets.QSpinBox()
        self._led_neighbors_spin.setRange(1, 7)
        self._led_neighbors_spin.setValue(1)
        self._led_neighbors_spin.setFixedWidth(60)
        nb_layout.addWidget(self._led_neighbors_spin)
        nb_layout.addStretch()
        sl.addWidget(self._neighbor_row)

        self._led_mode_combo.currentTextChanged.connect(self._update_led_visibility)

        # Beamer + Screens placeholders
        sl.addSpacing(4)
        sl.addWidget(self._sublabel("Beamer"))
        beamer_ph = QtWidgets.QLabel("Beamer control — not yet connected (placeholder)")
        beamer_ph.setStyleSheet("color:#555; font-style:italic; margin-left:8px;")
        sl.addWidget(beamer_ph)

        sl.addSpacing(4)
        sl.addWidget(self._sublabel("Screens"))
        screens_ph = QtWidgets.QLabel("Screen control — not yet connected (placeholder)")
        screens_ph.setStyleSheet("color:#555; font-style:italic; margin-left:8px;")
        sl.addWidget(screens_ph)

        sl.addWidget(self._make_separator())

        # ── Trial ─────────────────────────────────────────────────
        sl.addWidget(self._section_label("Trial"))

        trial_cond_row = QtWidgets.QHBoxLayout()
        trial_cond_row.addWidget(QtWidgets.QLabel("End condition:"))
        self._trial_end_combo = QtWidgets.QComboBox()
        self._trial_end_combo.addItems(["Fixed time", "All rewards collected"])
        self._trial_end_combo.setFixedWidth(160)
        trial_cond_row.addWidget(self._trial_end_combo)
        trial_cond_row.addStretch()
        sl.addLayout(trial_cond_row)

        # Time row
        self._trial_time_widget = QtWidgets.QWidget()
        tt_layout = QtWidgets.QHBoxLayout(self._trial_time_widget)
        tt_layout.setContentsMargins(0, 0, 0, 0)
        tt_layout.addWidget(QtWidgets.QLabel("Duration:"))
        self._trial_dur_spin = QtWidgets.QSpinBox()
        self._trial_dur_spin.setRange(1, 3600)
        self._trial_dur_spin.setValue(30)
        self._trial_dur_spin.setFixedWidth(80)
        tt_layout.addWidget(self._trial_dur_spin)
        tt_layout.addWidget(QtWidgets.QLabel("seconds"))
        tt_layout.addStretch()
        sl.addWidget(self._trial_time_widget)

        self._trial_end_combo.currentTextChanged.connect(self._update_trial_widgets)

        # Connect reward count and distribution type to rebuilds
        self._count_combo.currentIndexChanged.connect(self._rebuild_reward_section)
        self._dist_type_combo.currentIndexChanged.connect(self._rebuild_dist_section)

        sl.addStretch()

    # ── Layout helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _section_label(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet("color:#aaa; font-size:11px; font-weight:bold;")
        return lbl

    @staticmethod
    def _sublabel(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet("color:#888; font-size:10px; font-weight:bold;")
        return lbl

    @staticmethod
    def _make_separator() -> QtWidgets.QFrame:
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet("color:#444;")
        return line

    @staticmethod
    def _clear_widget(w: QtWidgets.QWidget):
        """Remove all child widgets from *w*'s layout without deleting them."""
        layout = w.layout()
        if not layout:
            return
        while layout.count():
            item = layout.takeAt(0)
            child = item.widget()
            if child:
                child.setParent(None)

    # ── Dynamic section builders ──────────────────────────────────────────────

    def _rebuild_reward_section(self):
        """Rebuild both the reward-config table and the distribution panel."""
        self._rebuild_reward_config()
        self._rebuild_dist_section()

    def _rebuild_reward_config(self):
        """Rebuild the per-reward parameter table (duration + probability)."""
        self._clear_widget(self._reward_config_container)
        container_layout = self._reward_config_container.layout()
        self._reward_rows = []
        n = int(self._count_combo.currentText())

        # Header row
        hdr = QtWidgets.QWidget()
        hdr_l = QtWidgets.QHBoxLayout(hdr)
        hdr_l.setContentsMargins(0, 0, 0, 0)
        for text, width in [("Reward", 55), ("Duration (ms)", 110), ("Probability", 100)]:
            lbl = QtWidgets.QLabel(text)
            lbl.setFixedWidth(width)
            lbl.setStyleSheet("color:#666; font-size:10px;")
            hdr_l.addWidget(lbl)
        hdr_l.addStretch()
        container_layout.addWidget(hdr)

        for i in range(n):
            row_w = QtWidgets.QWidget()
            row_l = QtWidgets.QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(4)

            rid_lbl = QtWidgets.QLabel(str(i + 1))
            rid_lbl.setFixedWidth(55)
            rid_lbl.setStyleSheet("color:#ccc;")
            row_l.addWidget(rid_lbl)

            dur_spin = QtWidgets.QSpinBox()
            dur_spin.setRange(1, 30000)
            dur_spin.setValue(500)
            dur_spin.setFixedWidth(100)
            row_l.addWidget(dur_spin)

            prob_spin = QtWidgets.QDoubleSpinBox()
            prob_spin.setRange(0.0, 1.0)
            prob_spin.setSingleStep(0.05)
            prob_spin.setDecimals(2)
            prob_spin.setValue(1.0)
            prob_spin.setFixedWidth(90)
            row_l.addWidget(prob_spin)

            row_l.addStretch()
            container_layout.addWidget(row_w)
            self._reward_rows.append((dur_spin, prob_spin))

    def _rebuild_dist_section(self):
        """Rebuild the distribution panel (fixed port map OR random spacing)."""
        self._clear_widget(self._dist_container)
        dist_layout = self._dist_container.layout()
        n    = int(self._count_combo.currentText())
        mode = self._dist_type_combo.currentText()

        self._fixed_port_combos  = []
        self._min_spacing_spin   = None
        self._spacing_warn_lbl   = None
        self._exclude_prev_chk   = None

        if mode == "Fixed":
            # One row per reward: "Reward N → Port [combo]"
            hdr = QtWidgets.QWidget()
            hdr_l = QtWidgets.QHBoxLayout(hdr)
            hdr_l.setContentsMargins(0, 0, 0, 0)
            for text, width in [("Reward", 58), ("Port (1-16)", 90)]:
                lbl = QtWidgets.QLabel(text)
                lbl.setFixedWidth(width)
                lbl.setStyleSheet("color:#666; font-size:10px;")
                hdr_l.addWidget(lbl)
            hdr_l.addStretch()
            dist_layout.addWidget(hdr)

            used_ports: set[int] = set()
            for i in range(n):
                row_w = QtWidgets.QWidget()
                row_l = QtWidgets.QHBoxLayout(row_w)
                row_l.setContentsMargins(0, 0, 0, 0)
                row_l.setSpacing(4)

                rid_lbl = QtWidgets.QLabel(str(i + 1))
                rid_lbl.setFixedWidth(58)
                rid_lbl.setStyleSheet("color:#ccc;")
                row_l.addWidget(rid_lbl)

                port_combo = QtWidgets.QComboBox()
                port_combo.addItems([str(p) for p in range(1, 17)])
                # Default: spread rewards evenly if possible
                default_port = ((i * (16 // max(n, 1))) % 16) + 1
                while default_port in used_ports:
                    default_port = (default_port % 16) + 1
                port_combo.setCurrentText(str(default_port))
                used_ports.add(default_port)
                port_combo.setFixedWidth(80)
                row_l.addWidget(port_combo)
                row_l.addStretch()
                dist_layout.addWidget(row_w)
                self._fixed_port_combos.append(port_combo)

        else:  # Random
            sp_row = QtWidgets.QWidget()
            sp_l = QtWidgets.QHBoxLayout(sp_row)
            sp_l.setContentsMargins(0, 0, 0, 0)
            sp_l.addWidget(QtWidgets.QLabel("Min spacing between rewards:"))
            self._min_spacing_spin = QtWidgets.QSpinBox()
            self._min_spacing_spin.setRange(1, 16)
            self._min_spacing_spin.setValue(4)
            self._min_spacing_spin.setFixedWidth(60)
            sp_l.addWidget(self._min_spacing_spin)
            sp_l.addStretch()
            dist_layout.addWidget(sp_row)

            self._spacing_warn_lbl = QtWidgets.QLabel("")
            self._spacing_warn_lbl.setStyleSheet("color:#aaa; font-size:10px;")
            dist_layout.addWidget(self._spacing_warn_lbl)

            self._exclude_prev_chk = QtWidgets.QCheckBox(
                "Exclude reward locations used in previous sessions")
            self._exclude_prev_chk.setStyleSheet("font-size:10px;")
            dist_layout.addWidget(self._exclude_prev_chk)

            self._min_spacing_spin.valueChanged.connect(self._update_spacing_warning)
            self._update_spacing_warning()

    def _update_spacing_warning(self):
        if self._min_spacing_spin is None or self._spacing_warn_lbl is None:
            return
        spacing = self._min_spacing_spin.value()
        n       = int(self._count_combo.currentText())
        max_ok  = 16 // spacing
        if n > max_ok:
            self._spacing_warn_lbl.setText(
                f"⚠  Only {max_ok} reward(s) fit with spacing {spacing} — reduce count or spacing")
            self._spacing_warn_lbl.setStyleSheet("color:#e06c00; font-size:10px;")
        else:
            self._spacing_warn_lbl.setText(
                f"✓  {n} reward(s) fit with spacing {spacing}  (max {max_ok})")
            self._spacing_warn_lbl.setStyleSheet("color:#aaa; font-size:10px;")

    # ── Conditional widget visibility ─────────────────────────────────────────

    def _update_sess_unit(self):
        self._sess_unit_lbl.setText(
            "seconds" if self._sess_type_combo.currentText() == "Time" else "trials")

    def _update_led_visibility(self):
        self._neighbor_row.setVisible(
            self._led_mode_combo.currentText() == self._LED_MODE_LABELS["neighbors"])

    def _update_trial_widgets(self):
        self._trial_time_widget.setVisible(
            self._trial_end_combo.currentText() == "Fixed time")

    # ── Protocol dict I/O ─────────────────────────────────────────────────────

    def build_protocol_dict(self) -> dict:
        """Read current widget values and return a complete protocol dict."""
        n = int(self._count_combo.currentText())

        configs = []
        for i, (dur_spin, prob_spin) in enumerate(self._reward_rows):
            configs.append({
                "id": i + 1,
                "duration_ms": dur_spin.value(),
                "probability": round(prob_spin.value(), 4),
            })

        dist_type = "fixed" if self._dist_type_combo.currentText() == "Fixed" else "random"
        if dist_type == "fixed":
            fixed_map = {
                str(i + 1): int(combo.currentText())
                for i, combo in enumerate(self._fixed_port_combos)
            }
            dist = {"type": "fixed", "fixed_map": fixed_map, "min_spacing": 4}
        else:
            spacing          = self._min_spacing_spin.value() if self._min_spacing_spin else 4
            exclude_previous = (self._exclude_prev_chk.isChecked()
                                if self._exclude_prev_chk else False)
            dist = {
                "type":             "random",
                "min_spacing":      spacing,
                "exclude_previous": exclude_previous,
            }

        led_key = self._LED_MODE_KEYS.get(
            self._led_mode_combo.currentText(), "reward_only")

        sess_type = "time" if self._sess_type_combo.currentText() == "Time" else "trials"
        end_type  = ("time" if self._trial_end_combo.currentText() == "Fixed time"
                     else "all_rewards")

        return {
            "session": {
                "type":   sess_type,
                "length": self._sess_length_spin.value(),
            },
            "rewards": {
                "count":         n,
                "configs":       configs,
                "distribution":  dist,
                "led_mode":      led_key,
                "led_neighbors": self._led_neighbors_spin.value(),
            },
            "trial": {
                "end_type":   end_type,
                "duration_s": self._trial_dur_spin.value(),
            },
            "beamer":  None,
            "screens": None,
        }

    def _apply_protocol(self, d: dict):
        """Populate all widgets from a protocol dict.

        Rebuilds dynamic sections explicitly to avoid spurious signal chaining.
        """
        # ── Session ──────────────────────────────────────────────
        sess = d.get("session", {})
        self._sess_type_combo.blockSignals(True)
        self._sess_type_combo.setCurrentText(
            "Time" if sess.get("type", "time") == "time" else "Trials")
        self._sess_type_combo.blockSignals(False)
        self._sess_length_spin.setValue(int(sess.get("length", 600)))
        self._update_sess_unit()

        # ── Reward count → triggers rebuild ──────────────────────
        rw    = d.get("rewards", {})
        count = int(rw.get("count", 2))
        self._count_combo.blockSignals(True)
        self._count_combo.setCurrentText(str(count))
        self._count_combo.blockSignals(False)

        # Rebuild dynamic sections manually (no signal)
        self._rebuild_reward_config()

        # Populate reward config rows
        configs = {cfg["id"]: cfg for cfg in rw.get("configs", [])}
        for i, (dur_spin, prob_spin) in enumerate(self._reward_rows):
            cfg = configs.get(i + 1, {})
            dur_spin.setValue(int(cfg.get("duration_ms", 500)))
            prob_spin.setValue(float(cfg.get("probability", 1.0)))

        # ── Distribution ─────────────────────────────────────────
        dist = rw.get("distribution", {})
        dist_type = dist.get("type", "fixed")
        self._dist_type_combo.blockSignals(True)
        self._dist_type_combo.setCurrentText(
            "Fixed" if dist_type == "fixed" else "Random")
        self._dist_type_combo.blockSignals(False)

        self._rebuild_dist_section()

        if dist_type == "fixed":
            fixed_map = dist.get("fixed_map", {})
            for i, combo in enumerate(self._fixed_port_combos):
                port = int(fixed_map.get(str(i + 1), 1))
                combo.setCurrentText(str(port))
        else:
            if self._min_spacing_spin is not None:
                self._min_spacing_spin.setValue(int(dist.get("min_spacing", 4)))
            if self._exclude_prev_chk is not None:
                self._exclude_prev_chk.setChecked(
                    bool(dist.get("exclude_previous", False)))
            self._update_spacing_warning()

        # ── LED ──────────────────────────────────────────────────
        led_mode_key = rw.get("led_mode", "reward_only")
        self._led_mode_combo.blockSignals(True)
        self._led_mode_combo.setCurrentText(
            self._LED_MODE_LABELS.get(led_mode_key,
                                      self._LED_MODE_LABELS["reward_only"]))
        self._led_mode_combo.blockSignals(False)
        self._led_neighbors_spin.setValue(int(rw.get("led_neighbors", 1)))
        self._update_led_visibility()

        # ── Trial ────────────────────────────────────────────────
        trial    = d.get("trial", {})
        end_type = trial.get("end_type", "time")
        self._trial_end_combo.blockSignals(True)
        self._trial_end_combo.setCurrentText(
            "Fixed time" if end_type in ("time", "fixed") else "All rewards collected")
        self._trial_end_combo.blockSignals(False)
        self._trial_dur_spin.setValue(int(trial.get("duration_s", 30)))
        self._update_trial_widgets()

    # ── File operations ───────────────────────────────────────────────────────

    def _protocols_dir(self) -> str:
        try:
            from shared_states import protocols_path
            os.makedirs(protocols_path, exist_ok=True)
            return protocols_path
        except Exception:
            return os.path.expanduser("~")

    def _load_protocol(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Protocol", self._protocols_dir(), "JSON files (*.json)")
        if not path:
            return
        try:
            with open(path, "r") as f:
                d = json.load(f)
            self._apply_protocol(d)
            self._current_path = path
            self._path_label.setText(os.path.basename(path))
            self._path_label.setStyleSheet("color:#ccc; font-size:10px;")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Load error", str(e))

    def _new_protocol(self):
        self._apply_protocol(self.DEFAULT_PROTOCOL)
        self._current_path = None
        self._path_label.setText("No file loaded")
        self._path_label.setStyleSheet("color:#888; font-size:10px;")

    def _save_protocol(self):
        init_dir  = (os.path.dirname(self._current_path)
                     if self._current_path else self._protocols_dir())
        init_name = (os.path.basename(self._current_path)
                     if self._current_path else "protocol.json")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Protocol",
            os.path.join(init_dir, init_name),
            "JSON files (*.json)")
        if not path:
            return
        if not path.endswith(".json"):
            path += ".json"
        try:
            with open(path, "w") as f:
                json.dump(self.build_protocol_dict(), f, indent=2)
            self._current_path = path
            self._path_label.setText(os.path.basename(path))
            self._path_label.setStyleSheet("color:#ccc; font-size:10px;")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Save error", str(e))
