"""gui/protocol_page.py — Protocol editor tab.

Lets the user create, load, edit, and save behavioural protocols as .json files.
Protocols are stored in the folder chosen in the Settings dialog
(shared_states.get_protocols_path()).

Section layout (inside a QScrollArea):
  ── Session ─────────────────────────────────────────────────────
  ── Rewards ─────────────────────────────────────────────────────
      • number of rewards (dropdown 1-16)
      • per-reward config  (dynamic table: duration, probability)
      • distribution       (fixed port map  OR  random + spacing)
      • sporadic switching (two rewards trade lickports mid-session)
      • delay              (release OR equalise, from session start)
      • LED activation     (mode dropdown + optional neighbor count)
      • Screens         (mode: none / static / dynamic)
  ── Trial ───────────────────────────────────────────────────────
  ── Sounds ──────────────────────────────────────────────────────
      • a tone at trial start and/or trial end
  ── Intertrial Interval ──────────────────────────────────────────
      • Fixed time  OR  Fixed region  OR  Random region
      • each region carries its own beamer light/shadow mode
      • Region settings shown as live overlay on the camera preview

Protocols are read strictly — every key in DEFAULT_PROTOCOL must be present, so a
malformed file raises KeyError at load rather than silently running with defaults.
"""

import json
import os

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import pyqtSignal


class ProtocolPage(QtWidgets.QWidget):
    """Full-featured protocol editor."""

    # Emitted whenever the ITI region overlay should change.
    # Carries a dict (fixed_region / random_region) or None to clear.
    overlay_changed = pyqtSignal(object)

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
                "exclude_previous": False,
            },
            "led_mode": "reward_only",
            "led_neighbors": 1,
            "switching": {"enabled": False, "probability": 0.0},
            "delay": {
                "enabled": False,
                "mode": "release",            # "release" | "equalise"
                "duration_type": "fixed",     # "fixed" | "random"
                "duration_s": 10.0,
                "duration_max_s": 30.0,
                # Applied to every reward once an "equalise" delay elapses.
                "probability": 1.0,
                "duration_ms": 500,
            },
        },
        "trial": {
            "end_type": "time",
            "duration_s": 30,
        },
        "sounds": {
            "trial_start": {"enabled": False, "frequency_hz": 1000,
                            "volume": 1.0, "duration_s": 0.5},
            "trial_end":   {"enabled": False, "frequency_hz": 1000,
                            "volume": 1.0, "duration_s": 0.5},
        },
        "intertrial": {
            "type": "time",
            "duration_s": 5.0,
            "region": {
                "x_cm": 0.0, "y_cm": 0.0, "diameter_cm": 6.0,
                "brightness": 100, "color": [255, 255, 255],
                "shadow": False,
                "duration_type": "fixed",
                "duration_s": 2.0,
                "duration_max_s": 3.0,
            },
            "random_region": {
                "diameter_cm": 6.0,
                "brightness": 100, "color": [255, 255, 255],
                "shadow": False,
                "margin_x_cm": 0.0, "margin_y_cm": 0.0, "margin_radius_cm": 10.0,
                "duration_type": "fixed",
                "duration_s": 2.0,
                "duration_max_s": 3.0,
            },
        },
        "screens": {
            "mode":      "none",              # "none" | "static" | "dynamic"
            # static: one entry per screen
            "trial":     ["black", "black"],
            "iti":       ["black", "black"],
            "randomize": False,
            # dynamic: one entry per reward
            "dynamic": [
                {"id": 1, "trial": "black", "iti": "black"},
                {"id": 2, "trial": "black", "iti": "black"},
            ],
        },
    }

    # Touch-screen patterns (keys match screen_controls.normalize_pattern).
    _N_SCREENS = 2
    _PATTERN_LABELS = {
        "black":   "Black",
        "circles": "White circles",
        "zigzag":  "Black zigzag",
    }
    _PATTERN_KEYS = {v: k for k, v in _PATTERN_LABELS.items()}

    _LED_MODE_LABELS = {
        "none":         "None (LEDs off during trial)",
        "reward_only":  "Reward locations only",
        "neighbors":    "Reward + N neighbours",
        "all":          "All LEDs",
    }
    _LED_MODE_KEYS = {v: k for k, v in _LED_MODE_LABELS.items()}

    _SCREEN_MODE_LABELS = {
        "none":    "None (screens stay black)",
        "static":  "Static (pattern per screen)",
        "dynamic": "Dynamic (pattern follows the reward)",
    }
    _SCREEN_MODE_KEYS = {v: k for k, v in _SCREEN_MODE_LABELS.items()}

    _DELAY_MODE_LABELS = {
        "release":  "Release (rewards inert until the delay passes)",
        "equalise": "Equalise (all rewards share values after the delay)",
    }
    _DELAY_MODE_KEYS = {v: k for k, v in _DELAY_MODE_LABELS.items()}

    # Fixed/random timing choices, shared by the ITI dwell and the reward delay.
    # The second entry contains an en-dash (U+2013) — keep it in one place so the
    # save/load round-trip can never disagree with the widget text.
    _DWELL_LABELS = ["Fixed", "Random (0 – max)"]

    _SOUND_KEYS = ("trial_start", "trial_end")
    _SOUND_LABELS = {"trial_start": "Play a tone at trial start",
                     "trial_end":   "Play a tone at trial end"}

    def __init__(self, beamer_queue=None):
        super().__init__()
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)

        self._current_path: str | None = None
        self.beamer_queue = beamer_queue     # None if the beamer process isn't wired in
        self._beamer_calib = None            # lazy BeamerCalibration (cm ↔ camera mapping)

        # Beamer sphere appearance for the two region menus (persist across rebuilds)
        self._reg_color = QtGui.QColor(255, 255, 255)
        self._rnd_color = QtGui.QColor(255, 255, 255)
        # Per-region light/shadow mode. Persisted here (like the colours above) so the
        # setting survives the widget teardown when the user switches ITI type.
        self._reg_shadow = False
        self._rnd_shadow = False

        # Dynamic section state — populated by rebuild helpers
        self._reward_rows: list[tuple[QtWidgets.QSpinBox,
                                      QtWidgets.QDoubleSpinBox]] = []
        self._fixed_port_combos: list[QtWidgets.QComboBox] = []
        self._min_spacing_spin: QtWidgets.QSpinBox | None = None
        self._spacing_warn_lbl: QtWidgets.QLabel | None   = None

        # Sporadic reward switching (static; set once in _build_ui)
        self._switch_enabled_chk: QtWidgets.QCheckBox | None = None
        self._switch_prob_spin: QtWidgets.QDoubleSpinBox | None = None
        self._switch_body_w: QtWidgets.QWidget | None = None

        # Reward delay (static; set once in _build_ui)
        self._delay_enabled_chk = self._delay_mode_combo = None
        self._delay_dur_type = self._delay_dur_spin = self._delay_dur_max_spin = None
        self._delay_dur_fixed_w = self._delay_dur_max_w = None
        self._delay_eq_prob_spin = self._delay_eq_dur_spin = self._delay_eq_w = None
        self._delay_body_w = None

        # Trial sounds — one entry per _SOUND_KEYS, each a dict of widget refs
        self._sound_w: dict[str, dict] = {}

        # Touch-screen patterns
        self._screens_mode_combo: QtWidgets.QComboBox | None  = None
        self._screens_static_w: QtWidgets.QWidget | None      = None
        self._screens_dynamic_w: QtWidgets.QWidget | None     = None
        self._screens_dyn_warn: QtWidgets.QLabel | None       = None
        self._screens_random_chk: QtWidgets.QCheckBox | None  = None
        self._screen_trial_combos: list[QtWidgets.QComboBox]  = []
        self._screen_iti_combos: list[QtWidgets.QComboBox]    = []
        # One (trial, iti) combo pair per reward — rebuilt when the count changes
        self._screen_dyn_rows: list[tuple[QtWidgets.QComboBox,
                                          QtWidgets.QComboBox]] = []

        # ITI widget refs (nullable; reset each time _rebuild_iti_section runs)
        self._iti_type_combo: QtWidgets.QComboBox | None = None        # static (set once in _build_ui)
        self._iti_time_spin: QtWidgets.QDoubleSpinBox | None = None
        # Fixed region (cm-based beamer sphere + toggles + dwell)
        self._iti_reg_x_spin = self._iti_reg_y_spin = self._iti_reg_diam_spin = None
        self._iti_reg_bright = self._iti_reg_color_btn = None
        self._iti_reg_beamer_chk = self._iti_reg_contour_chk = None
        self._iti_reg_shadow_chk = None
        self._iti_reg_dur_type = self._iti_reg_dur_spin = self._iti_reg_dur_max_spin = None
        self._iti_reg_dur_fixed_w = self._iti_reg_dur_max_w = None
        # Random region (same sphere fields + margin + toggles + dwell)
        self._iti_rnd_diam_spin = self._iti_rnd_bright = self._iti_rnd_color_btn = None
        self._iti_rnd_margin_x_spin = self._iti_rnd_margin_y_spin = None
        self._iti_rnd_margin_radius_spin = None
        self._iti_rnd_beamer_chk = self._iti_rnd_contour_chk = self._iti_rnd_margin_chk = None
        self._iti_rnd_shadow_chk = None
        self._iti_rnd_dur_type = self._iti_rnd_dur_spin = self._iti_rnd_dur_max_spin = None
        self._iti_rnd_dur_fixed_w = self._iti_rnd_dur_max_w = None

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

        # Sporadic switching — rewards trade lickports partway through the session
        sl.addSpacing(4)
        sl.addWidget(self._sublabel("Sporadic switching"))
        self._switch_enabled_chk = QtWidgets.QCheckBox(
            "Rewards trade lickports during the session")
        self._switch_enabled_chk.setStyleSheet("margin-left:8px;")
        sl.addWidget(self._switch_enabled_chk)

        self._switch_body_w = QtWidgets.QWidget()
        sw_layout = QtWidgets.QVBoxLayout(self._switch_body_w)
        sw_layout.setContentsMargins(8, 0, 0, 0)
        sw_layout.setSpacing(2)
        self._switch_prob_spin = QtWidgets.QDoubleSpinBox()
        self._switch_prob_spin.setRange(0.0, 1.0)
        self._switch_prob_spin.setSingleStep(0.05)
        self._switch_prob_spin.setDecimals(2)
        self._switch_prob_spin.setValue(0.0)
        self._switch_prob_spin.setFixedWidth(90)
        sw_layout.addWidget(self._labeled_row("Probability per trial:",
                                              self._switch_prob_spin))
        switch_note = QtWidgets.QLabel(
            "Rolled once at the start of every trial after the first. On a hit two "
            "rewards (a random pair when there are more than two) swap lickports for "
            "the rest of the session — the ports in use never change, only which "
            "reward sits at each.")
        switch_note.setWordWrap(True)
        switch_note.setStyleSheet("color:#777; font-size:9px;")
        sw_layout.addWidget(switch_note)
        sl.addWidget(self._switch_body_w)
        self._switch_enabled_chk.toggled.connect(self._update_switch_visibility)

        # Delay — gates or equalises the rewards for a while after the session starts
        sl.addSpacing(4)
        sl.addWidget(self._sublabel("Delay"))
        self._delay_enabled_chk = QtWidgets.QCheckBox(
            "Delay the reward regime after the session starts")
        self._delay_enabled_chk.setStyleSheet("margin-left:8px;")
        sl.addWidget(self._delay_enabled_chk)

        self._delay_body_w = QtWidgets.QWidget()
        dl_layout = QtWidgets.QVBoxLayout(self._delay_body_w)
        dl_layout.setContentsMargins(8, 0, 0, 0)
        dl_layout.setSpacing(2)

        self._delay_mode_combo = QtWidgets.QComboBox()
        self._delay_mode_combo.addItems(list(self._DELAY_MODE_LABELS.values()))
        self._delay_mode_combo.setMinimumWidth(320)
        dl_layout.addWidget(self._labeled_row("Mode:", self._delay_mode_combo))

        self._delay_dur_type = QtWidgets.QComboBox()
        self._delay_dur_type.addItems(self._DWELL_LABELS)
        self._delay_dur_type.setFixedWidth(160)
        dl_layout.addWidget(self._labeled_row("Duration type:", self._delay_dur_type))

        # Session-scale, so this needs a wider range than the ITI dwell helper offers.
        self._delay_dur_spin = self._make_delay_spin(10.0)
        self._delay_dur_max_spin = self._make_delay_spin(30.0)
        self._delay_dur_fixed_w = self._labeled_row("Duration:", self._delay_dur_spin)
        self._delay_dur_max_w = self._labeled_row("Max duration:", self._delay_dur_max_spin)
        dl_layout.addWidget(self._delay_dur_fixed_w)
        dl_layout.addWidget(self._delay_dur_max_w)

        self._delay_eq_w = QtWidgets.QWidget()
        eq_layout = QtWidgets.QVBoxLayout(self._delay_eq_w)
        eq_layout.setContentsMargins(0, 0, 0, 0)
        eq_layout.setSpacing(2)
        self._delay_eq_prob_spin = QtWidgets.QDoubleSpinBox()
        self._delay_eq_prob_spin.setRange(0.0, 1.0)
        self._delay_eq_prob_spin.setSingleStep(0.05)
        self._delay_eq_prob_spin.setDecimals(2)
        self._delay_eq_prob_spin.setValue(1.0)
        self._delay_eq_prob_spin.setFixedWidth(90)
        self._delay_eq_dur_spin = QtWidgets.QSpinBox()
        self._delay_eq_dur_spin.setRange(1, 30000)
        self._delay_eq_dur_spin.setValue(500)
        self._delay_eq_dur_spin.setFixedWidth(100)
        eq_layout.addWidget(self._labeled_row("Equalised probability:",
                                              self._delay_eq_prob_spin))
        eq_layout.addWidget(self._labeled_row("Equalised duration (ms):",
                                              self._delay_eq_dur_spin))
        dl_layout.addWidget(self._delay_eq_w)

        delay_note = QtWidgets.QLabel(
            "Release: the reward ports are inert until the delay passes — a lick does "
            "nothing and does not use the reward up.\n"
            "Equalise: until the delay passes each reward uses its own probability and "
            "duration; afterwards every reward uses the equalised values above.\n"
            "The clock runs once from the start of the session, not once per trial.")
        delay_note.setWordWrap(True)
        delay_note.setStyleSheet("color:#777; font-size:9px;")
        dl_layout.addWidget(delay_note)
        sl.addWidget(self._delay_body_w)

        self._delay_enabled_chk.toggled.connect(self._update_delay_visibility)
        self._delay_mode_combo.currentTextChanged.connect(self._update_delay_visibility)
        self._delay_dur_type.currentTextChanged.connect(self._update_delay_visibility)

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

        # Screens — none, a fixed pattern per screen, or patterns that follow rewards
        sl.addSpacing(4)
        sl.addWidget(self._sublabel("Screens"))
        scr_mode_row = QtWidgets.QHBoxLayout()
        scr_mode_row.addWidget(QtWidgets.QLabel("    Mode:"))
        self._screens_mode_combo = QtWidgets.QComboBox()
        self._screens_mode_combo.addItems(list(self._SCREEN_MODE_LABELS.values()))
        self._screens_mode_combo.setMinimumWidth(260)
        scr_mode_row.addWidget(self._screens_mode_combo)
        scr_mode_row.addStretch()
        sl.addLayout(scr_mode_row)

        # -- Static: one pattern per screen, for the trial and for the ITI
        self._screen_trial_combos = []
        self._screen_iti_combos   = []
        self._screens_static_w = QtWidgets.QWidget()
        scr_layout = QtWidgets.QVBoxLayout(self._screens_static_w)
        scr_layout.setContentsMargins(8, 0, 0, 0)
        scr_layout.setSpacing(2)
        for phase, combos in (("Trial", self._screen_trial_combos),
                              ("ITI",   self._screen_iti_combos)):
            row = QtWidgets.QHBoxLayout()
            lbl = QtWidgets.QLabel(f"{phase}:")
            lbl.setFixedWidth(40)
            row.addWidget(lbl)
            for s in range(self._N_SCREENS):
                row.addWidget(QtWidgets.QLabel(f"Screen {s + 1}"))
                combo = QtWidgets.QComboBox()
                combo.addItems(list(self._PATTERN_LABELS.values()))
                combo.setFixedWidth(120)
                row.addWidget(combo)
                combos.append(combo)
            row.addStretch()
            wrapper = QtWidgets.QWidget()
            wrapper.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            scr_layout.addWidget(wrapper)

        self._screens_random_chk = QtWidgets.QCheckBox(
            "Randomise which screen shows which trial pattern (each trial)")
        scr_layout.addWidget(self._screens_random_chk)
        screens_note = QtWidgets.QLabel(
            "Patterns are set at the start of each trial and each intertrial "
            "interval, and both screens are blanked at the end of the session.")
        screens_note.setWordWrap(True)
        screens_note.setStyleSheet("color:#777; font-size:9px;")
        scr_layout.addWidget(screens_note)
        sl.addWidget(self._screens_static_w)

        # -- Dynamic: a pattern per reward; rows rebuilt when the count changes
        self._screens_dynamic_w = QtWidgets.QWidget()
        dyn_layout = QtWidgets.QVBoxLayout(self._screens_dynamic_w)
        dyn_layout.setContentsMargins(8, 0, 0, 0)
        dyn_layout.setSpacing(2)
        sl.addWidget(self._screens_dynamic_w)

        self._screens_mode_combo.currentTextChanged.connect(
            self._update_screens_visibility)

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

        sl.addWidget(self._make_separator())

        # ── Sounds ────────────────────────────────────────────────
        sl.addWidget(self._section_label("Sounds"))
        for key in self._SOUND_KEYS:
            sl.addWidget(self._build_sound_block(key))

        sl.addWidget(self._make_separator())

        # ── Intertrial Interval ───────────────────────────────────
        sl.addWidget(self._section_label("Intertrial Interval"))

        iti_type_row = QtWidgets.QHBoxLayout()
        iti_type_row.addWidget(QtWidgets.QLabel("Type:"))
        self._iti_type_combo = QtWidgets.QComboBox()
        self._iti_type_combo.addItems(["Fixed time", "Fixed region", "Random region"])
        self._iti_type_combo.setFixedWidth(150)
        iti_type_row.addWidget(self._iti_type_combo)
        iti_type_row.addStretch()
        sl.addLayout(iti_type_row)

        self._iti_container = QtWidgets.QWidget()
        self._iti_container.setLayout(QtWidgets.QVBoxLayout())
        self._iti_container.layout().setContentsMargins(8, 2, 0, 0)
        self._iti_container.layout().setSpacing(4)
        sl.addWidget(self._iti_container)

        self._iti_type_combo.currentTextChanged.connect(self._rebuild_iti_section)

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

    def _build_sound_block(self, key: str) -> QtWidgets.QWidget:
        """One enable checkbox + frequency/length/volume row for a trial tone.

        Mirrors the speaker test in the Cleaning/Testing tab, including the volume
        being an *overdrive factor* shown as a percentage: 100 % is a clean sine and
        higher clips, which is how the quiet speakers get driven hard.
        """
        block = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        chk = QtWidgets.QCheckBox(self._SOUND_LABELS[key])
        chk.setStyleSheet("margin-left:8px;")
        layout.addWidget(chk)

        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(8, 0, 0, 0)
        body_layout.setSpacing(2)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Frequency:"))
        freq = QtWidgets.QSpinBox()
        freq.setRange(20, 20000)
        freq.setValue(1000)
        freq.setSuffix(" Hz")
        freq.setFixedWidth(90)
        row.addWidget(freq)
        row.addSpacing(12)
        row.addWidget(QtWidgets.QLabel("Length:"))
        length = QtWidgets.QDoubleSpinBox()
        length.setRange(0.05, 30.0)
        length.setSingleStep(0.1)
        length.setDecimals(2)
        length.setValue(0.5)
        length.setSuffix(" s")
        length.setFixedWidth(80)
        row.addWidget(length)
        row.addStretch()
        row_w = QtWidgets.QWidget()
        row_w.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        body_layout.addWidget(row_w)

        vol_row = QtWidgets.QHBoxLayout()
        vol_row.addWidget(QtWidgets.QLabel("Volume (overdrive):"))
        vol = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        vol.setRange(0, 1000)
        vol.setValue(100)
        vol_lbl = QtWidgets.QLabel("100 %")
        vol_lbl.setFixedWidth(48)
        vol.valueChanged.connect(lambda v, l=vol_lbl: l.setText(f"{v} %"))
        vol_row.addWidget(vol)
        vol_row.addWidget(vol_lbl)
        vol_row_w = QtWidgets.QWidget()
        vol_row_w.setLayout(vol_row)
        vol_row.setContentsMargins(0, 0, 0, 0)
        body_layout.addWidget(vol_row_w)

        layout.addWidget(body)
        chk.toggled.connect(body.setEnabled)
        body.setEnabled(chk.isChecked())

        self._sound_w[key] = {"chk": chk, "freq": freq, "len": length,
                              "vol": vol, "vol_lbl": vol_lbl, "body": body}
        return block

    # ── Dynamic section builders ──────────────────────────────────────────────

    def _rebuild_reward_section(self):
        """Rebuild the reward-config table, the distribution panel and the
        per-reward screen rows — all three are sized by the reward count."""
        self._rebuild_reward_config()
        self._rebuild_dist_section()
        self._rebuild_screens_dynamic()

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

    def _rebuild_screens_dynamic(self):
        """Rebuild the per-reward pattern rows used by the Dynamic screen mode.

        One (trial, ITI) pattern pair per reward. The screens themselves are pinned
        to the lickports rewards 1 and 2 start on, so each screen shows the pattern
        of whichever reward currently sits at its port.
        """
        self._clear_widget(self._screens_dynamic_w)
        self._screen_dyn_rows = []
        # _clear_widget reparents everything away, so the warning label has to be
        # recreated here rather than built once.
        self._screens_dyn_warn = None

        layout = self._screens_dynamic_w.layout()
        n = int(self._count_combo.currentText())

        hdr = QtWidgets.QWidget()
        hdr_l = QtWidgets.QHBoxLayout(hdr)
        hdr_l.setContentsMargins(0, 0, 0, 0)
        for text, width in [("Reward", 58), ("Trial pattern", 124), ("ITI pattern", 124)]:
            lbl = QtWidgets.QLabel(text)
            lbl.setFixedWidth(width)
            lbl.setStyleSheet("color:#666; font-size:10px;")
            hdr_l.addWidget(lbl)
        hdr_l.addStretch()
        layout.addWidget(hdr)

        for i in range(n):
            row_w = QtWidgets.QWidget()
            row_l = QtWidgets.QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(4)

            rid_lbl = QtWidgets.QLabel(str(i + 1))
            rid_lbl.setFixedWidth(58)
            rid_lbl.setStyleSheet("color:#ccc;")
            row_l.addWidget(rid_lbl)

            trial_combo = QtWidgets.QComboBox()
            trial_combo.addItems(list(self._PATTERN_LABELS.values()))
            trial_combo.setFixedWidth(120)
            row_l.addWidget(trial_combo)

            iti_combo = QtWidgets.QComboBox()
            iti_combo.addItems(list(self._PATTERN_LABELS.values()))
            iti_combo.setFixedWidth(120)
            row_l.addWidget(iti_combo)

            row_l.addStretch()
            layout.addWidget(row_w)
            self._screen_dyn_rows.append((trial_combo, iti_combo))

        self._screens_dyn_warn = QtWidgets.QLabel("")
        self._screens_dyn_warn.setWordWrap(True)
        layout.addWidget(self._screens_dyn_warn)
        if n < self._N_SCREENS:
            self._screens_dyn_warn.setText(
                f"⚠  Only reward 1 anchors a screen — screen 2 stays black all "
                f"session. Use {self._N_SCREENS} or more rewards to drive both.")
            self._screens_dyn_warn.setStyleSheet("color:#e06c00; font-size:10px;")
        else:
            self._screens_dyn_warn.setText(
                "Screen 1 is pinned to the lickport reward 1 starts on, screen 2 to "
                "reward 2's. Each screen shows the pattern of whichever reward is at "
                "its port, so the patterns follow the rewards when they swap. Rewards "
                "beyond the second never anchor a screen, but their pattern does show "
                "if they swap onto one of those two ports.")
            self._screens_dyn_warn.setStyleSheet("color:#777; font-size:9px;")

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

    def _update_screens_visibility(self):
        """Show the panel belonging to the selected screen mode.

        setVisible rather than setEnabled: with three modes, greying out the two
        inactive panels would leave a large block of dead space.
        """
        mode = self._SCREEN_MODE_KEYS[self._screens_mode_combo.currentText()]
        self._screens_static_w.setVisible(mode == "static")
        self._screens_dynamic_w.setVisible(mode == "dynamic")

    def _update_switch_visibility(self):
        self._switch_body_w.setVisible(self._switch_enabled_chk.isChecked())

    def _update_delay_visibility(self):
        """Show the delay body, and within it the timing row and equalised values
        that match the selected mode."""
        self._delay_body_w.setVisible(self._delay_enabled_chk.isChecked())
        fixed = self._delay_dur_type.currentText() == self._DWELL_LABELS[0]
        self._delay_dur_fixed_w.setVisible(fixed)
        self._delay_dur_max_w.setVisible(not fixed)
        self._delay_eq_w.setVisible(
            self._DELAY_MODE_KEYS[self._delay_mode_combo.currentText()] == "equalise")

    # ── ITI section ───────────────────────────────────────────────────────────

    @staticmethod
    def _make_delay_spin(val: float) -> QtWidgets.QDoubleSpinBox:
        """Duration spin for the reward delay — session-scale, so it needs a wider
        range than the ITI dwell helper's 600 s ceiling."""
        sb = QtWidgets.QDoubleSpinBox()
        sb.setRange(0.0, 86400.0)
        sb.setSingleStep(1.0)
        sb.setDecimals(1)
        sb.setValue(val)
        sb.setSuffix(" s")
        sb.setFixedWidth(110)
        return sb

    @staticmethod
    def _make_norm_spin(val: float = 0.5, lo: float = 0.0,
                        hi: float = 1.0) -> QtWidgets.QDoubleSpinBox:
        """Return a QDoubleSpinBox for normalised (0-1) coordinates."""
        sb = QtWidgets.QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setSingleStep(0.01)
        sb.setDecimals(2)
        sb.setValue(val)
        sb.setFixedWidth(80)
        return sb

    @staticmethod
    def _make_dur_spin(val: float = 2.0, suffix: str = " s") -> QtWidgets.QDoubleSpinBox:
        sb = QtWidgets.QDoubleSpinBox()
        sb.setRange(0.1, 600)
        sb.setSingleStep(0.5)
        sb.setDecimals(1)
        sb.setValue(val)
        sb.setSuffix(suffix)
        sb.setFixedWidth(90)
        return sb

    @staticmethod
    def _make_cm_spin(val: float = 0.0, lo: float = -100.0,
                      hi: float = 100.0) -> QtWidgets.QDoubleSpinBox:
        """Return a QDoubleSpinBox for arena centimetres (matches the Test-Sphere menu)."""
        sb = QtWidgets.QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setSingleStep(0.5)
        sb.setDecimals(1)
        sb.setValue(val)
        sb.setSuffix(" cm")
        sb.setFixedWidth(90)
        return sb

    @staticmethod
    def _make_bright_slider() -> QtWidgets.QSlider:
        s = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        s.setRange(0, 100)
        s.setValue(100)
        s.setFixedWidth(120)
        return s

    @staticmethod
    def _calib_hint() -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel("Preview & contour need a completed beamer calibration.")
        lbl.setStyleSheet("color:#777; font-size:9px;")
        return lbl

    @staticmethod
    def _labeled_row(label: str, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        """Return a QWidget containing a right-padded label + widget on one row."""
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lbl = QtWidgets.QLabel(label)
        lbl.setFixedWidth(140)
        lay.addWidget(lbl)
        lay.addWidget(widget)
        lay.addStretch()
        return w

    def _rebuild_iti_section(self):
        """Rebuild the ITI container to match the currently selected ITI type."""
        self._clear_widget(self._iti_container)
        layout = self._iti_container.layout()

        # Reset all nullable refs
        self._iti_time_spin = None
        self._iti_reg_x_spin = self._iti_reg_y_spin = self._iti_reg_diam_spin = None
        self._iti_reg_bright = self._iti_reg_color_btn = None
        self._iti_reg_beamer_chk = self._iti_reg_contour_chk = None
        self._iti_reg_shadow_chk = None
        self._iti_reg_dur_type = self._iti_reg_dur_spin = self._iti_reg_dur_max_spin = None
        self._iti_reg_dur_fixed_w = self._iti_reg_dur_max_w = None
        self._iti_rnd_diam_spin = self._iti_rnd_bright = self._iti_rnd_color_btn = None
        self._iti_rnd_margin_x_spin = self._iti_rnd_margin_y_spin = None
        self._iti_rnd_margin_radius_spin = None
        self._iti_rnd_beamer_chk = self._iti_rnd_contour_chk = self._iti_rnd_margin_chk = None
        self._iti_rnd_shadow_chk = None
        self._iti_rnd_dur_type = self._iti_rnd_dur_spin = self._iti_rnd_dur_max_spin = None
        self._iti_rnd_dur_fixed_w = self._iti_rnd_dur_max_w = None

        iti_type = self._iti_type_combo.currentText() if self._iti_type_combo else "Fixed time"

        if iti_type == "Fixed time":
            self._iti_time_spin = self._make_dur_spin(5.0)
            layout.addWidget(self._labeled_row("Duration:", self._iti_time_spin))
            note = QtWidgets.QLabel(
                "Light and shadow are a property of the ITI target region, so there "
                "is no beamer mode to set here — the beamer stays dark for the whole "
                "session. Pick Fixed region or Random region to use Shadow.")
            note.setWordWrap(True)
            note.setStyleSheet("color:#777; font-size:9px;")
            layout.addWidget(note)

        elif iti_type == "Fixed region":
            self._iti_reg_x_spin    = self._make_cm_spin(0.0)
            self._iti_reg_y_spin    = self._make_cm_spin(0.0)
            self._iti_reg_diam_spin = self._make_cm_spin(6.0, 0.1, 200.0)
            layout.addWidget(self._labeled_row("Position X (cm):", self._iti_reg_x_spin))
            layout.addWidget(self._labeled_row("Position Y (cm):", self._iti_reg_y_spin))
            layout.addWidget(self._labeled_row("Diameter (cm):",   self._iti_reg_diam_spin))

            self._iti_reg_bright = self._make_bright_slider()
            layout.addWidget(self._labeled_row("Brightness:", self._iti_reg_bright))
            self._iti_reg_color_btn = QtWidgets.QPushButton()
            self._iti_reg_color_btn.setFixedWidth(44)
            self._update_swatch(self._iti_reg_color_btn, self._reg_color)
            self._iti_reg_color_btn.clicked.connect(self._pick_reg_color)
            layout.addWidget(self._labeled_row("Colour:", self._iti_reg_color_btn))

            self._iti_reg_beamer_chk  = QtWidgets.QCheckBox("Beamer on")
            self._iti_reg_contour_chk = QtWidgets.QCheckBox("Contour on")
            layout.addWidget(self._toggle_row(self._iti_reg_beamer_chk, self._iti_reg_contour_chk))
            layout.addWidget(self._calib_hint())

            layout.addWidget(self._sublabel("Beamer mode"))
            self._iti_reg_shadow_chk = QtWidgets.QCheckBox(
                "Shadow (dark sphere on a lit field)")
            self._iti_reg_shadow_chk.setChecked(self._reg_shadow)
            layout.addWidget(self._iti_reg_shadow_chk)
            shadow_note = QtWidgets.QLabel(
                "Light: dark during trials, bright sphere marks the ITI target.\n"
                "Shadow: whole area lit during trials, dark sphere marks the ITI target.")
            shadow_note.setWordWrap(True)
            shadow_note.setStyleSheet("color:#777; font-size:9px;")
            layout.addWidget(shadow_note)
            self._iti_reg_shadow_chk.toggled.connect(self._reg_shadow_toggled)

            layout.addWidget(self._sublabel("Dwell time"))
            self._iti_reg_dur_type = QtWidgets.QComboBox()
            self._iti_reg_dur_type.addItems(self._DWELL_LABELS)
            self._iti_reg_dur_type.setFixedWidth(160)
            layout.addWidget(self._labeled_row("Duration type:", self._iti_reg_dur_type))
            self._iti_reg_dur_spin     = self._make_dur_spin(2.0)
            self._iti_reg_dur_max_spin = self._make_dur_spin(3.0)
            self._iti_reg_dur_fixed_w  = self._labeled_row("Duration:", self._iti_reg_dur_spin)
            self._iti_reg_dur_max_w    = self._labeled_row("Max duration:", self._iti_reg_dur_max_spin)
            layout.addWidget(self._iti_reg_dur_fixed_w)
            layout.addWidget(self._iti_reg_dur_max_w)
            self._iti_reg_dur_type.currentTextChanged.connect(self._update_iti_dur_visibility)

            for w in (self._iti_reg_x_spin, self._iti_reg_y_spin, self._iti_reg_diam_spin):
                w.valueChanged.connect(self._reg_live)
            self._iti_reg_bright.valueChanged.connect(self._reg_live)
            self._iti_reg_contour_chk.toggled.connect(self._emit_overlay)
            self._iti_reg_beamer_chk.toggled.connect(self._reg_beamer_toggled)
            self._update_iti_dur_visibility()

        else:  # Random region
            self._iti_rnd_diam_spin = self._make_cm_spin(6.0, 0.1, 200.0)
            layout.addWidget(self._labeled_row("Diameter (cm):", self._iti_rnd_diam_spin))
            self._iti_rnd_bright = self._make_bright_slider()
            layout.addWidget(self._labeled_row("Brightness:", self._iti_rnd_bright))
            self._iti_rnd_color_btn = QtWidgets.QPushButton()
            self._iti_rnd_color_btn.setFixedWidth(44)
            self._update_swatch(self._iti_rnd_color_btn, self._rnd_color)
            self._iti_rnd_color_btn.clicked.connect(self._pick_rnd_color)
            layout.addWidget(self._labeled_row("Colour:", self._iti_rnd_color_btn))

            layout.addWidget(self._sublabel("Outer margin (projection area for random targets)"))
            self._iti_rnd_margin_x_spin      = self._make_cm_spin(0.0)
            self._iti_rnd_margin_y_spin      = self._make_cm_spin(0.0)
            self._iti_rnd_margin_radius_spin = self._make_cm_spin(10.0, 0.1, 200.0)
            layout.addWidget(self._labeled_row("Outer margin centre X (cm):", self._iti_rnd_margin_x_spin))
            layout.addWidget(self._labeled_row("Outer margin centre Y (cm):", self._iti_rnd_margin_y_spin))
            layout.addWidget(self._labeled_row("Outer margin radius (cm):",   self._iti_rnd_margin_radius_spin))

            self._iti_rnd_beamer_chk  = QtWidgets.QCheckBox("Beamer on")
            self._iti_rnd_contour_chk = QtWidgets.QCheckBox("Contour on")
            self._iti_rnd_margin_chk  = QtWidgets.QCheckBox("Show outer margin")
            layout.addWidget(self._toggle_row(self._iti_rnd_beamer_chk,
                                              self._iti_rnd_contour_chk,
                                              self._iti_rnd_margin_chk))
            note = QtWidgets.QLabel(
                "Beamer on shows the target sphere (its diameter). The outer margin "
                "is only drawn on the camera (Show outer margin) — never projected.")
            note.setWordWrap(True)
            note.setStyleSheet("color:#777; font-size:9px;")
            layout.addWidget(note)
            layout.addWidget(self._calib_hint())

            layout.addWidget(self._sublabel("Beamer mode"))
            self._iti_rnd_shadow_chk = QtWidgets.QCheckBox(
                "Shadow (dark sphere on a lit field)")
            self._iti_rnd_shadow_chk.setChecked(self._rnd_shadow)
            layout.addWidget(self._iti_rnd_shadow_chk)
            rnd_shadow_note = QtWidgets.QLabel(
                "Light: dark during trials, bright sphere marks the ITI target.\n"
                "Shadow: whole area lit during trials, dark sphere marks the ITI target.")
            rnd_shadow_note.setWordWrap(True)
            rnd_shadow_note.setStyleSheet("color:#777; font-size:9px;")
            layout.addWidget(rnd_shadow_note)
            self._iti_rnd_shadow_chk.toggled.connect(self._rnd_shadow_toggled)

            layout.addWidget(self._sublabel("Dwell time"))
            self._iti_rnd_dur_type = QtWidgets.QComboBox()
            self._iti_rnd_dur_type.addItems(self._DWELL_LABELS)
            self._iti_rnd_dur_type.setFixedWidth(160)
            layout.addWidget(self._labeled_row("Duration type:", self._iti_rnd_dur_type))
            self._iti_rnd_dur_spin     = self._make_dur_spin(2.0)
            self._iti_rnd_dur_max_spin = self._make_dur_spin(3.0)
            self._iti_rnd_dur_fixed_w  = self._labeled_row("Duration:", self._iti_rnd_dur_spin)
            self._iti_rnd_dur_max_w    = self._labeled_row("Max duration:", self._iti_rnd_dur_max_spin)
            layout.addWidget(self._iti_rnd_dur_fixed_w)
            layout.addWidget(self._iti_rnd_dur_max_w)
            self._iti_rnd_dur_type.currentTextChanged.connect(self._update_iti_dur_visibility)

            # Target appearance (diameter/brightness/colour) re-projects the sphere;
            # the outer-margin controls only redraw the camera overlay — the beamer
            # never projects the margin.
            self._iti_rnd_diam_spin.valueChanged.connect(self._rnd_live)
            self._iti_rnd_bright.valueChanged.connect(self._rnd_live)
            for w in (self._iti_rnd_margin_x_spin, self._iti_rnd_margin_y_spin,
                      self._iti_rnd_margin_radius_spin):
                w.valueChanged.connect(self._emit_overlay)
            self._iti_rnd_contour_chk.toggled.connect(self._emit_overlay)
            self._iti_rnd_margin_chk.toggled.connect(self._emit_overlay)
            self._iti_rnd_beamer_chk.toggled.connect(self._refresh_rnd_beamer)
            self._update_iti_dur_visibility()

        # Switching type clears any live beamer preview from the previous menu.
        self._send_beamer({"cmd": "clear"})
        self._emit_overlay()

    @staticmethod
    def _toggle_row(*checkboxes) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        for c in checkboxes:
            lay.addWidget(c)
        lay.addStretch()
        return w

    def _update_iti_dur_visibility(self):
        """Show either the fixed or max-duration row based on the dwell-type combo."""
        # Fixed region
        if self._iti_reg_dur_type is not None:
            fixed = self._iti_reg_dur_type.currentText() == "Fixed"
            if self._iti_reg_dur_fixed_w:
                self._iti_reg_dur_fixed_w.setVisible(fixed)
            if self._iti_reg_dur_max_w:
                self._iti_reg_dur_max_w.setVisible(not fixed)
        # Random region
        if self._iti_rnd_dur_type is not None:
            fixed = self._iti_rnd_dur_type.currentText() == "Fixed"
            if self._iti_rnd_dur_fixed_w:
                self._iti_rnd_dur_fixed_w.setVisible(fixed)
            if self._iti_rnd_dur_max_w:
                self._iti_rnd_dur_max_w.setVisible(not fixed)

    # ── Beamer preview + coordinate mapping ───────────────────────────────────

    def _send_beamer(self, cmd: dict):
        if self.beamer_queue is None:
            return
        try:
            self.beamer_queue.put_nowait(cmd)
        except Exception:
            pass

    # Shadow is a per-region setting, so each preview reads its own checkbox. The
    # persisted flag is the fallback for when the panel isn't currently built.
    def _reg_shadow_on(self) -> bool:
        return bool(self._iti_reg_shadow_chk.isChecked()
                    if self._iti_reg_shadow_chk is not None else self._reg_shadow)

    def _rnd_shadow_on(self) -> bool:
        return bool(self._iti_rnd_shadow_chk.isChecked()
                    if self._iti_rnd_shadow_chk is not None else self._rnd_shadow)

    def _reg_shadow_toggled(self, on: bool):
        self._reg_shadow = bool(on)
        self._reg_live()      # re-project immediately if the beamer preview is on

    def _rnd_shadow_toggled(self, on: bool):
        self._rnd_shadow = bool(on)
        self._refresh_rnd_beamer()

    @staticmethod
    def _eff_color(base: QtGui.QColor, bright: QtWidgets.QSlider | None) -> list:
        b = (bright.value() / 100.0) if bright is not None else 1.0
        return [int(base.red() * b), int(base.green() * b), int(base.blue() * b)]

    @staticmethod
    def _update_swatch(btn: QtWidgets.QPushButton, color: QtGui.QColor):
        btn.setStyleSheet(
            f"background: rgb({color.red()},{color.green()},{color.blue()}); "
            f"border:1px solid #888;")

    def _calib(self):
        """Fresh BeamerCalibration (re-reads the JSON) or None if unavailable."""
        try:
            from beamer_controls import BeamerCalibration
            return BeamerCalibration()
        except Exception:
            return None

    def _region_norm(self, calib, x_cm, y_cm, diam_cm):
        """cm circle → normalised camera {x, y, radius}, or None if uncalibrated."""
        if calib is None:
            return None
        res = calib.cm_to_dlc(x_cm, y_cm, diam_cm)
        if res is None:
            return None
        u, v, r = res
        return {"x": u, "y": v, "radius": r}

    def _project_reg_sphere(self):
        if self._iti_reg_x_spin is None:
            return
        self._send_beamer({
            "cmd":         "sphere",
            "x_cm":        self._iti_reg_x_spin.value(),
            "y_cm":        self._iti_reg_y_spin.value(),
            "diameter_cm": self._iti_reg_diam_spin.value(),
            "shadow":      self._reg_shadow_on(),
            "color":       self._eff_color(self._reg_color, self._iti_reg_bright),
        })

    def _project_rnd_sphere(self):
        # Preview the target sphere at the arena centre, sized by its diameter.
        # Position is fixed (independent of the outer margin) so margin edits never
        # move or resize the projected sphere.
        if self._iti_rnd_diam_spin is None:
            return
        self._send_beamer({
            "cmd":         "sphere",
            "x_cm":        0.0,
            "y_cm":        0.0,
            "diameter_cm": self._iti_rnd_diam_spin.value(),
            "shadow":      self._rnd_shadow_on(),
            "color":       self._eff_color(self._rnd_color, self._iti_rnd_bright),
        })

    def _refresh_rnd_beamer(self, *_):
        """Live beamer preview for the random region: the target sphere only.
        The outer margin is a camera overlay and is never projected by the beamer."""
        if self._iti_rnd_beamer_chk and self._iti_rnd_beamer_chk.isChecked():
            self._project_rnd_sphere()
        else:
            self._send_beamer({"cmd": "clear"})

    def _reg_live(self, *_):
        self._emit_overlay()
        if self._iti_reg_beamer_chk and self._iti_reg_beamer_chk.isChecked():
            self._project_reg_sphere()

    def _reg_beamer_toggled(self, on):
        if on:
            self._project_reg_sphere()
        else:
            self._send_beamer({"cmd": "clear"})

    def _pick_reg_color(self):
        c = QtWidgets.QColorDialog.getColor(self._reg_color, self, "Sphere colour")
        if c.isValid():
            self._reg_color = c
            if self._iti_reg_color_btn:
                self._update_swatch(self._iti_reg_color_btn, c)
            self._reg_live()

    def _rnd_live(self, *_):
        self._emit_overlay()
        self._refresh_rnd_beamer()

    def _pick_rnd_color(self):
        c = QtWidgets.QColorDialog.getColor(self._rnd_color, self, "Sphere colour")
        if c.isValid():
            self._rnd_color = c
            if self._iti_rnd_color_btn:
                self._update_swatch(self._iti_rnd_color_btn, c)
            self._rnd_live()

    def _emit_overlay(self, *_):
        """Emit the camera overlay for the ITI region contour / projection margin.

        Regions are specified in cm; they are mapped to normalised camera coords
        via the beamer↔camera calibration so the drawn contour lands where the
        beamer projects. Overlay dict shape: {"target": {x,y,radius},
        "margin": {x,y,radius}} (either key optional); None clears it.
        """
        if self._iti_type_combo is None:
            return
        iti_type = self._iti_type_combo.currentText()
        calib = self._calib()
        overlay: dict = {}

        if iti_type == "Fixed region" and self._iti_reg_x_spin is not None:
            if self._iti_reg_contour_chk and self._iti_reg_contour_chk.isChecked():
                t = self._region_norm(calib, self._iti_reg_x_spin.value(),
                                      self._iti_reg_y_spin.value(),
                                      self._iti_reg_diam_spin.value())
                if t:
                    overlay["target"] = t

        elif iti_type == "Random region" and self._iti_rnd_diam_spin is not None:
            if self._iti_rnd_contour_chk and self._iti_rnd_contour_chk.isChecked():
                # Target contour at the arena centre — matches the beamer preview.
                t = self._region_norm(calib, 0.0, 0.0, self._iti_rnd_diam_spin.value())
                if t:
                    overlay["target"] = t
            if self._iti_rnd_margin_chk and self._iti_rnd_margin_chk.isChecked():
                m = self._region_norm(calib, self._iti_rnd_margin_x_spin.value(),
                                      self._iti_rnd_margin_y_spin.value(),
                                      2.0 * self._iti_rnd_margin_radius_spin.value())
                if m:
                    overlay["margin"] = m

        self.overlay_changed.emit(overlay if overlay else None)

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
            # min_spacing / exclude_previous only apply to a random distribution, but
            # both are written either way so switching type and back keeps the values.
            dist = {"type": "fixed", "fixed_map": fixed_map,
                    "min_spacing": 4, "exclude_previous": False}
        else:
            spacing          = self._min_spacing_spin.value() if self._min_spacing_spin else 4
            exclude_previous = (self._exclude_prev_chk.isChecked()
                                if self._exclude_prev_chk else False)
            dist = {
                "type":             "random",
                "min_spacing":      spacing,
                "exclude_previous": exclude_previous,
            }

        led_key = self._LED_MODE_KEYS[self._led_mode_combo.currentText()]

        sess_type = "time" if self._sess_type_combo.currentText() == "Time" else "trials"
        end_type  = ("time" if self._trial_end_combo.currentText() == "Fixed time"
                     else "all_rewards")

        # ── Intertrial ────────────────────────────────────────────
        iti_type = self._iti_type_combo.currentText() if self._iti_type_combo else "Fixed time"
        # Always include all three sub-configs so round-trips preserve values
        # when the user switches type and back.
        def _get(spin, fallback):
            return spin.value() if spin is not None else fallback

        def _bright(slider, fallback=100):
            return slider.value() if slider is not None else fallback

        def _color(c):
            return [c.red(), c.green(), c.blue()]

        def _chk(box, fallback):
            return bool(box.isChecked()) if box is not None else bool(fallback)

        iti = {
            "type":      {"Fixed time": "time",
                          "Fixed region": "fixed_region",
                          "Random region": "random_region"}[iti_type],
            "duration_s": _get(self._iti_time_spin, 5.0),
            "region": {
                "x_cm":        _get(self._iti_reg_x_spin, 0.0),
                "y_cm":        _get(self._iti_reg_y_spin, 0.0),
                "diameter_cm": _get(self._iti_reg_diam_spin, 6.0),
                "brightness":  _bright(self._iti_reg_bright),
                "color":       _color(self._reg_color),
                "shadow":      _chk(self._iti_reg_shadow_chk, self._reg_shadow),
                "duration_type": ("fixed" if (self._iti_reg_dur_type is None or
                                              self._iti_reg_dur_type.currentText()
                                              == self._DWELL_LABELS[0])
                                  else "random"),
                "duration_s":     _get(self._iti_reg_dur_spin, 2.0),
                "duration_max_s": _get(self._iti_reg_dur_max_spin, 3.0),
            },
            "random_region": {
                "diameter_cm":   _get(self._iti_rnd_diam_spin, 6.0),
                "brightness":    _bright(self._iti_rnd_bright),
                "color":         _color(self._rnd_color),
                "shadow":        _chk(self._iti_rnd_shadow_chk, self._rnd_shadow),
                "margin_x_cm":      _get(self._iti_rnd_margin_x_spin, 0.0),
                "margin_y_cm":      _get(self._iti_rnd_margin_y_spin, 0.0),
                "margin_radius_cm": _get(self._iti_rnd_margin_radius_spin, 10.0),
                "duration_type": ("fixed" if (self._iti_rnd_dur_type is None or
                                              self._iti_rnd_dur_type.currentText()
                                              == self._DWELL_LABELS[0])
                                  else "random"),
                "duration_s":     _get(self._iti_rnd_dur_spin, 2.0),
                "duration_max_s": _get(self._iti_rnd_dur_max_spin, 3.0),
            },
        }

        # ── Screens ───────────────────────────────────────────────
        # Every mode's settings are written whatever the mode, so switching between
        # them and back never loses a pattern.
        def _patterns(combos):
            return [self._PATTERN_KEYS[c.currentText()] for c in combos]

        screens = {
            "mode":      self._SCREEN_MODE_KEYS[self._screens_mode_combo.currentText()],
            "trial":     _patterns(self._screen_trial_combos),
            "iti":       _patterns(self._screen_iti_combos),
            "randomize": bool(self._screens_random_chk.isChecked()),
            "dynamic":   [{"id":    i + 1,
                           "trial": self._PATTERN_KEYS[t.currentText()],
                           "iti":   self._PATTERN_KEYS[it.currentText()]}
                          for i, (t, it) in enumerate(self._screen_dyn_rows)],
        }

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
                "switching": {
                    "enabled":     bool(self._switch_enabled_chk.isChecked()),
                    "probability": round(self._switch_prob_spin.value(), 4),
                },
                "delay": {
                    "enabled":       bool(self._delay_enabled_chk.isChecked()),
                    "mode":          self._DELAY_MODE_KEYS[
                        self._delay_mode_combo.currentText()],
                    "duration_type": ("fixed" if self._delay_dur_type.currentText()
                                      == self._DWELL_LABELS[0] else "random"),
                    "duration_s":     self._delay_dur_spin.value(),
                    "duration_max_s": self._delay_dur_max_spin.value(),
                    "probability":    round(self._delay_eq_prob_spin.value(), 4),
                    "duration_ms":    self._delay_eq_dur_spin.value(),
                },
            },
            "trial": {
                "end_type":   end_type,
                "duration_s": self._trial_dur_spin.value(),
            },
            "sounds": {
                key: {
                    "enabled":      bool(w["chk"].isChecked()),
                    "frequency_hz": w["freq"].value(),
                    "volume":       round(w["vol"].value() / 100.0, 3),
                    "duration_s":   w["len"].value(),
                }
                for key, w in self._sound_w.items()
            },
            "intertrial": iti,
            "screens": screens,
        }

    def _apply_protocol(self, d: dict):
        """Populate all widgets from a protocol dict.

        Rebuilds dynamic sections explicitly to avoid spurious signal chaining.

        The dict is read strictly — a missing key raises KeyError rather than
        silently substituting a default, so a malformed protocol is caught at load
        instead of quietly running an experiment the user did not configure. The
        `is not None` widget guards below are a different thing: they test whether
        the sub-widgets for the *selected* ITI type currently exist.
        """
        # ── Session ──────────────────────────────────────────────
        sess = d["session"]
        self._sess_type_combo.blockSignals(True)
        self._sess_type_combo.setCurrentText(
            "Time" if sess["type"] == "time" else "Trials")
        self._sess_type_combo.blockSignals(False)
        self._sess_length_spin.setValue(int(sess["length"]))
        self._update_sess_unit()

        # ── Reward count → triggers rebuild ──────────────────────
        rw    = d["rewards"]
        count = int(rw["count"])
        self._count_combo.blockSignals(True)
        self._count_combo.setCurrentText(str(count))
        self._count_combo.blockSignals(False)

        # Rebuild dynamic sections manually (no signal). The dynamic screen rows are
        # sized by the reward count too, and must exist before the screens block
        # below populates them.
        self._rebuild_reward_config()
        self._rebuild_screens_dynamic()

        # Populate reward config rows
        configs = {cfg["id"]: cfg for cfg in rw["configs"]}
        for i, (dur_spin, prob_spin) in enumerate(self._reward_rows):
            cfg = configs[i + 1]
            dur_spin.setValue(int(cfg["duration_ms"]))
            prob_spin.setValue(float(cfg["probability"]))

        # ── Distribution ─────────────────────────────────────────
        dist = rw["distribution"]
        dist_type = dist["type"]
        self._dist_type_combo.blockSignals(True)
        self._dist_type_combo.setCurrentText(
            "Fixed" if dist_type == "fixed" else "Random")
        self._dist_type_combo.blockSignals(False)

        self._rebuild_dist_section()

        if dist_type == "fixed":
            fixed_map = dist["fixed_map"]
            for i, combo in enumerate(self._fixed_port_combos):
                combo.setCurrentText(str(int(fixed_map[str(i + 1)])))
        else:
            if self._min_spacing_spin is not None:
                self._min_spacing_spin.setValue(int(dist["min_spacing"]))
            if self._exclude_prev_chk is not None:
                self._exclude_prev_chk.setChecked(bool(dist["exclude_previous"]))
            self._update_spacing_warning()

        # ── LED ──────────────────────────────────────────────────
        self._led_mode_combo.blockSignals(True)
        self._led_mode_combo.setCurrentText(self._LED_MODE_LABELS[rw["led_mode"]])
        self._led_mode_combo.blockSignals(False)
        self._led_neighbors_spin.setValue(int(rw["led_neighbors"]))
        self._update_led_visibility()

        # ── Sporadic switching ───────────────────────────────────
        sw = rw["switching"]
        self._switch_enabled_chk.blockSignals(True)
        self._switch_enabled_chk.setChecked(bool(sw["enabled"]))
        self._switch_enabled_chk.blockSignals(False)
        self._switch_prob_spin.setValue(float(sw["probability"]))
        self._update_switch_visibility()

        # ── Delay ────────────────────────────────────────────────
        dl = rw["delay"]
        for widget in (self._delay_enabled_chk, self._delay_mode_combo,
                       self._delay_dur_type):
            widget.blockSignals(True)
        self._delay_enabled_chk.setChecked(bool(dl["enabled"]))
        self._delay_mode_combo.setCurrentText(self._DELAY_MODE_LABELS[dl["mode"]])
        self._delay_dur_type.setCurrentText(
            self._DWELL_LABELS[0] if dl["duration_type"] == "fixed"
            else self._DWELL_LABELS[1])
        for widget in (self._delay_enabled_chk, self._delay_mode_combo,
                       self._delay_dur_type):
            widget.blockSignals(False)
        self._delay_dur_spin.setValue(float(dl["duration_s"]))
        self._delay_dur_max_spin.setValue(float(dl["duration_max_s"]))
        self._delay_eq_prob_spin.setValue(float(dl["probability"]))
        self._delay_eq_dur_spin.setValue(int(dl["duration_ms"]))
        self._update_delay_visibility()

        # ── Trial ────────────────────────────────────────────────
        trial = d["trial"]
        self._trial_end_combo.blockSignals(True)
        self._trial_end_combo.setCurrentText(
            "Fixed time" if trial["end_type"] == "time" else "All rewards collected")
        self._trial_end_combo.blockSignals(False)
        self._trial_dur_spin.setValue(int(trial["duration_s"]))
        self._update_trial_widgets()

        # ── Sounds ───────────────────────────────────────────────
        for key, w in self._sound_w.items():
            snd = d["sounds"][key]
            w["chk"].blockSignals(True)
            w["chk"].setChecked(bool(snd["enabled"]))
            w["chk"].blockSignals(False)
            w["freq"].setValue(int(snd["frequency_hz"]))
            w["len"].setValue(float(snd["duration_s"]))
            w["vol"].setValue(int(round(float(snd["volume"]) * 100)))
            w["body"].setEnabled(w["chk"].isChecked())

        # ── Screens ──────────────────────────────────────────────
        screens = d["screens"]
        self._screens_mode_combo.blockSignals(True)
        self._screens_mode_combo.setCurrentText(
            self._SCREEN_MODE_LABELS[screens["mode"]])
        self._screens_mode_combo.blockSignals(False)
        for key, combos in (("trial", self._screen_trial_combos),
                            ("iti",   self._screen_iti_combos)):
            patterns = screens[key]
            for i, combo in enumerate(combos):
                combo.setCurrentText(self._PATTERN_LABELS[patterns[i]])
        self._screens_random_chk.setChecked(bool(screens["randomize"]))
        dyn = {row["id"]: row for row in screens["dynamic"]}
        for i, (trial_combo, iti_combo) in enumerate(self._screen_dyn_rows):
            row = dyn[i + 1]
            trial_combo.setCurrentText(self._PATTERN_LABELS[row["trial"]])
            iti_combo.setCurrentText(self._PATTERN_LABELS[row["iti"]])
        self._update_screens_visibility()

        # ── Intertrial ───────────────────────────────────────────
        iti = d["intertrial"]
        _iti_label = {"time": "Fixed time", "fixed_region": "Fixed region",
                      "random_region": "Random region"}[iti["type"]]
        self._iti_type_combo.blockSignals(True)
        self._iti_type_combo.setCurrentText(_iti_label)
        self._iti_type_combo.blockSignals(False)

        # Colour and shadow state persist independently of which region widgets
        # exist, so a save→load round-trip keeps both regions' settings. These must
        # be assigned before _rebuild_iti_section, which seeds the widgets from them.
        reg = iti["region"]
        rnd = iti["random_region"]
        rc = reg["color"]
        nc = rnd["color"]
        self._reg_color = QtGui.QColor(int(rc[0]), int(rc[1]), int(rc[2]))
        self._rnd_color = QtGui.QColor(int(nc[0]), int(nc[1]), int(nc[2]))
        self._reg_shadow = bool(reg["shadow"])
        self._rnd_shadow = bool(rnd["shadow"])

        self._rebuild_iti_section()   # builds sub-widgets for the selected type

        # Populate sub-widgets from dict (they exist now that _rebuild ran)
        if self._iti_time_spin is not None:
            self._iti_time_spin.setValue(float(iti["duration_s"]))
        if self._iti_reg_x_spin is not None:
            self._iti_reg_x_spin.setValue(float(reg["x_cm"]))
            self._iti_reg_y_spin.setValue(float(reg["y_cm"]))
            self._iti_reg_diam_spin.setValue(float(reg["diameter_cm"]))
            self._iti_reg_bright.setValue(int(reg["brightness"]))
            self._update_swatch(self._iti_reg_color_btn, self._reg_color)
            self._iti_reg_shadow_chk.setChecked(self._reg_shadow)
            self._iti_reg_dur_type.setCurrentText(
                self._DWELL_LABELS[0] if reg["duration_type"] == "fixed"
                else self._DWELL_LABELS[1])
            self._iti_reg_dur_spin.setValue(float(reg["duration_s"]))
            self._iti_reg_dur_max_spin.setValue(float(reg["duration_max_s"]))
            self._update_iti_dur_visibility()
        if self._iti_rnd_diam_spin is not None:
            self._iti_rnd_diam_spin.setValue(float(rnd["diameter_cm"]))
            self._iti_rnd_bright.setValue(int(rnd["brightness"]))
            self._update_swatch(self._iti_rnd_color_btn, self._rnd_color)
            self._iti_rnd_shadow_chk.setChecked(self._rnd_shadow)
            self._iti_rnd_margin_x_spin.setValue(float(rnd["margin_x_cm"]))
            self._iti_rnd_margin_y_spin.setValue(float(rnd["margin_y_cm"]))
            self._iti_rnd_margin_radius_spin.setValue(float(rnd["margin_radius_cm"]))
            self._iti_rnd_dur_type.setCurrentText(
                self._DWELL_LABELS[0] if rnd["duration_type"] == "fixed"
                else self._DWELL_LABELS[1])
            self._iti_rnd_dur_spin.setValue(float(rnd["duration_s"]))
            self._iti_rnd_dur_max_spin.setValue(float(rnd["duration_max_s"]))
            self._update_iti_dur_visibility()
        self._emit_overlay()

    # ── File operations ───────────────────────────────────────────────────────

    def _protocols_dir(self) -> str:
        try:
            from shared_states import get_protocols_path
            protocols_path = get_protocols_path()
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
