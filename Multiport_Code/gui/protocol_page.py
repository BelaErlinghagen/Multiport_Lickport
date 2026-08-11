"""Protocol editor tab.

Lets the user create, load, edit, and save behavioural protocols as .json
files, stored in the folder chosen in the Settings dialog
(shared_states.get_protocols_path()).

Section layout (inside a QScrollArea):
  ── Session ─────────────────────────────────────────────────────
  ── Rewards ─────────────────────────────────────────────────────
      • number of rewards (dropdown 1-16)
      • per-reward config  (dynamic table: volume, probability)
      • distribution       (fixed port map  OR  random + spacing)
      • sporadic switching (two rewards trade lickports mid-session)
      • delay              (release OR equalise, from session start)
      • LED activation     (mode dropdown + optional neighbor count)
      • Screens         (mode: none / static / dynamic)
  ── Trial ───────────────────────────────────────────────────────
  ── Sounds ──────────────────────────────────────────────────────
      • a tone at trial start and/or trial end
  ── Beamer ──────────────────────────────────────────────────────
      • projection mode (light / shadow / lit background) + the lit field
      • session-wide: it governs the whole session, not just the ITI
  ── Intertrial Interval ──────────────────────────────────────────
      • Fixed time  OR  Fixed region  OR  Random region
      • each region defines the target sphere the beamer projects
      • Region settings shown as live overlay on the camera preview

Protocols are read strictly: every key in DEFAULT_PROTOCOL must be present,
so a malformed file raises KeyError at load instead of silently running with
defaults. That's also why a protocol with pump-on durations (`duration_ms`)
instead of volumes (`volume_ul`) is rejected outright rather than converted
— a pump's output isn't proportional to how long it runs, so there's no
honest way to translate one into the other; those files must be rebuilt.

The beamer block is the one exception: older files predate it entirely, and
it *can* be translated honestly from the per-region `shadow` flag it
replaced — see beamer_controls.normalise_protocol_beamer, run here before
any widget is touched.

Rewards are set in µL. How many pulses that costs depends on the per-pump
calibration (pump_calibration.py), so each volume carries a live hint — the
one place a badly-rounding volume can be caught before an animal is in the arena.
"""

import json
import os

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import pyqtSignal

import shared_states
from beamer_controls import (BEAMER_MODE_KEYS, BEAMER_MODE_LABELS,
                             normalise_protocol_beamer)
from hardware_state import BEAMER_LIT_MODES
from pump_calibration import PumpCalibration


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
                {"id": 1, "volume_ul": 5.0, "probability": 1.0},
                {"id": 2, "volume_ul": 5.0, "probability": 1.0},
            ],
            "distribution": {
                "type": "fixed",
                "fixed_map": {"1": 1, "2": 9},
                "min_spacing": 4,
                "exclude_previous": False,
                "reuse_previous": False,
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
                "volume_ul": 5.0,
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
            # brightness/color describe the *target sphere*; the field behind it is
            # the session-wide beamer block below.
            "region": {
                "x_cm": 0.0, "y_cm": 0.0, "diameter_cm": 6.0,
                "brightness": 100, "color": [255, 255, 255],
                "duration_type": "fixed",
                "duration_s": 2.0,
                "duration_max_s": 3.0,
            },
            "random_region": {
                "diameter_cm": 6.0,
                "brightness": 100, "color": [255, 255, 255],
                "margin_x_cm": 0.0, "margin_y_cm": 0.0, "margin_radius_cm": 10.0,
                "duration_type": "fixed",
                "duration_s": 2.0,
                "duration_max_s": 3.0,
            },
        },
        "beamer": {
            # Session-wide, not an ITI setting: in the two lit modes the field is on
            # for the whole session and only the target comes and goes on top of it.
            "mode": "light",              # "light" | "shadow" | "lit_background"
            # The constant lit field. Left dimmer than the sphere by default —
            # a projector is additive, so a lit_background sphere is only visible
            # if it out-shines the field it sits on.
            "background_color": [255, 255, 255],
            "background_brightness": 30,
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
        # Four TTL outputs. ids 1-2 are Arduino 1's BNC 1-2, ids 3-4 Arduino 2's —
        # see SerialControls._BNC_MAP, which deliberately exempts BNC from the
        # lickport lookup tables. Each output owns an ordered list of triggers and
        # several may be active at once (e.g. a session-start marker plus a
        # during-trial train). A trigger row is
        #     {"type": ..., "frequency_hz": ..., "pulse_ms": ...}
        # and always carries both values whatever its type, so flipping a row
        # between a single pulse and a train never loses the other one.
        "bnc": {
            "outputs": [
                {"id": 1, "enabled": False, "triggers": []},
                {"id": 2, "enabled": False, "triggers": []},
                {"id": 3, "enabled": False, "triggers": []},
                {"id": 4, "enabled": False, "triggers": []},
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

    # Beamer projection modes. Shared with the Cleaning tab's test sphere, so the
    # two menus can never offer different labels for the same stored key.
    _BEAMER_MODE_LABELS = BEAMER_MODE_LABELS
    _BEAMER_MODE_KEYS   = BEAMER_MODE_KEYS

    # The two modes that keep the arena lit; both show the background controls and
    # hold their field between targets.
    _BEAMER_LIT_MODES = BEAMER_LIT_MODES

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

    # ── Reward volume ─────────────────────────────────────────────────────────
    # 50 µL is far above any sensible single reward; the cap is there to stop a
    # typo, not to express a policy. What actually constrains a volume is the
    # pump_max_pulses ceiling and how long the animal must lick to collect it —
    # both of which the live hint next to each spin box reports.
    _VOL_MIN, _VOL_MAX, _VOL_STEP = 0.1, 50.0, 0.5
    # Flag a rounding error worse than this. A strong pump at 2 µL/pulse turns a
    # requested 1 µL into 2 µL, and the editor is the only place to catch that
    # before an animal is in the arena.
    _VOL_ROUND_WARN = 0.10

    # ── BNC outputs ───────────────────────────────────────────────────────────
    _BNC_COUNT = 4
    # Which physical connector each protocol id reaches (matches serial_controls).
    _BNC_PORT_LABELS = {1: "Arduino 1 · BNC 1", 2: "Arduino 1 · BNC 2",
                        3: "Arduino 2 · BNC 1", 4: "Arduino 2 · BNC 2"}

    _BNC_SINGLE_TRIGGERS = ("start_of_session", "end_of_session",
                            "start_of_trial",   "end_of_trial")
    _BNC_TRAIN_TRIGGERS  = ("entire_session", "during_trial", "during_intertrial")
    _BNC_TRIGGER_LABELS = {
        "start_of_session":  "Single pulse — session start",
        "end_of_session":    "Single pulse — session end",
        "start_of_trial":    "Single pulse — trial start",
        "end_of_trial":      "Single pulse — trial end",
        "entire_session":    "Train — whole session",
        "during_trial":      "Train — during trials",
        "during_intertrial": "Train — during intertrials",
    }
    _BNC_TRIGGER_KEYS = {v: k for k, v in _BNC_TRIGGER_LABELS.items()}
    _BNC_TRIGGER_DEFAULT = {"type": "start_of_trial",
                            "frequency_hz": 1.0, "pulse_ms": 10}

    # The firmware parses the duration into a 16-bit int; 32768 and above wrap
    # negative and the off-timer never fires.
    _BNC_MAX_PULSE_MS = 32767
    # Pulses are generated on the PC and travel GUI queue → serial thread → UART,
    # which costs 5-25 ms of jitter. Past ~20 Hz that is a large fraction of the
    # period and the train stops being a train.
    _BNC_MAX_HZ = 20.0
    _BNC_JITTER_MS = 25    # amber-warning margin between pulse width and period

    def __init__(self, beamer_queue=None):
        super().__init__()
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)

        self._current_path: str | None = None
        self.beamer_queue = beamer_queue     # None if the beamer process isn't wired in
        self._beamer_calib = None            # lazy BeamerCalibration (cm ↔ camera mapping)

        # Beamer sphere appearance for the two region menus (persist across rebuilds)
        self._reg_color = QtGui.QColor(255, 255, 255)
        self._rnd_color = QtGui.QColor(255, 255, 255)
        # Constant lit field, for the two lit projection modes. Lives in the Beamer
        # section, which is built once, but is kept here beside the sphere colours it
        # is compared against.
        self._bg_color = QtGui.QColor(255, 255, 255)

        # Per-pump µL/pulse, used only for the live hints beside each volume. The
        # editor must stay usable on an uncalibrated rig — PumpCalibration never
        # raises, and the hints simply say "not calibrated" instead.
        self._pump_calib = PumpCalibration()

        # Dynamic section state — populated by rebuild helpers
        # (volume spin, probability spin, hint label) per reward
        self._reward_rows: list[tuple[QtWidgets.QDoubleSpinBox,
                                      QtWidgets.QDoubleSpinBox,
                                      QtWidgets.QLabel]] = []
        self._fixed_port_combos: list[QtWidgets.QComboBox] = []
        self._min_spacing_spin: QtWidgets.QSpinBox | None = None
        self._spacing_warn_lbl: QtWidgets.QLabel | None   = None
        self._exclude_prev_chk: QtWidgets.QCheckBox | None = None
        self._reuse_prev_chk: QtWidgets.QCheckBox | None   = None

        # Sporadic reward switching (static; set once in _build_ui)
        self._switch_enabled_chk: QtWidgets.QCheckBox | None = None
        self._switch_prob_spin: QtWidgets.QDoubleSpinBox | None = None
        self._switch_body_w: QtWidgets.QWidget | None = None

        # Reward delay (static; set once in _build_ui)
        self._delay_enabled_chk = self._delay_mode_combo = None
        self._delay_dur_type = self._delay_dur_spin = self._delay_dur_max_spin = None
        self._delay_dur_fixed_w = self._delay_dur_max_w = None
        self._delay_eq_prob_spin = self._delay_eq_vol_spin = self._delay_eq_w = None
        self._delay_eq_hint_lbl = None
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

        # BNC outputs — one block per output, each holding a variable-length list of
        # trigger rows. Unlike the reward and screen tables (sized by a count) these
        # are add/remove lists, so rows are appended and detached individually.
        self._bnc_blocks: list[dict] = []   # {"chk", "body", "container", "rows"}

        # ITI widget refs (nullable; reset each time _rebuild_iti_section runs)
        self._iti_type_combo: QtWidgets.QComboBox | None = None        # static (set once in _build_ui)
        self._iti_time_spin: QtWidgets.QDoubleSpinBox | None = None
        # Fixed region (cm-based beamer sphere + toggles + dwell)
        self._iti_reg_x_spin = self._iti_reg_y_spin = self._iti_reg_diam_spin = None
        self._iti_reg_bright = self._iti_reg_color_btn = None
        self._iti_reg_beamer_chk = self._iti_reg_contour_chk = None
        self._iti_reg_sphere_note = None
        self._iti_reg_dur_type = self._iti_reg_dur_spin = self._iti_reg_dur_max_spin = None
        self._iti_reg_dur_fixed_w = self._iti_reg_dur_max_w = None
        # Random region (same sphere fields + margin + toggles + dwell)
        self._iti_rnd_diam_spin = self._iti_rnd_bright = self._iti_rnd_color_btn = None
        self._iti_rnd_margin_x_spin = self._iti_rnd_margin_y_spin = None
        self._iti_rnd_margin_radius_spin = None
        self._iti_rnd_beamer_chk = self._iti_rnd_contour_chk = self._iti_rnd_margin_chk = None
        self._iti_rnd_sphere_note = None
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
        self._delay_eq_vol_spin = self._make_volume_spin()
        self._delay_eq_hint_lbl = QtWidgets.QLabel()
        self._delay_eq_hint_lbl.setStyleSheet("color:#888; font-size:10px;")
        self._delay_eq_vol_spin.valueChanged.connect(
            lambda _v: self._update_volume_hint(self._delay_eq_vol_spin,
                                                self._delay_eq_hint_lbl))
        eq_layout.addWidget(self._labeled_row("Equalised probability:",
                                              self._delay_eq_prob_spin))
        eq_vol_row = self._labeled_row("Equalised volume (µL):",
                                       self._delay_eq_vol_spin)
        # Index 2 = right after the label and the spin box, before _labeled_row's
        # trailing stretch, so the hint sits next to the value it describes.
        eq_vol_row.layout().insertWidget(2, self._delay_eq_hint_lbl)
        eq_layout.addWidget(eq_vol_row)
        dl_layout.addWidget(self._delay_eq_w)

        delay_note = QtWidgets.QLabel(
            "Release: the reward ports are inert until the delay passes — a lick does "
            "nothing and does not use the reward up.\n"
            "Equalise: until the delay passes each reward uses its own probability and "
            "volume; afterwards every reward uses the equalised values above. A reward "
            "already being delivered when the delay elapses finishes at its old "
            "volume.\n"
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
        self._dist_type_combo.currentIndexChanged.connect(self._dist_type_changed)

        sl.addWidget(self._make_separator())

        # ── Sounds ────────────────────────────────────────────────
        sl.addWidget(self._section_label("Sounds"))
        for key in self._SOUND_KEYS:
            sl.addWidget(self._build_sound_block(key))

        sl.addWidget(self._make_separator())

        # ── Beamer ────────────────────────────────────────────────
        # Above the ITI section on purpose: the mode governs the whole session, so it
        # must be reachable for every ITI type — including Fixed time, which projects
        # no target but can still run the arena lit throughout.
        sl.addWidget(self._section_label("Beamer"))

        beamer_mode_row = QtWidgets.QHBoxLayout()
        beamer_mode_row.addWidget(QtWidgets.QLabel("Mode:"))
        self._beamer_mode_combo = QtWidgets.QComboBox()
        self._beamer_mode_combo.addItems(list(self._BEAMER_MODE_LABELS.values()))
        self._beamer_mode_combo.setMinimumWidth(280)
        beamer_mode_row.addWidget(self._beamer_mode_combo)
        beamer_mode_row.addStretch()
        sl.addLayout(beamer_mode_row)

        beamer_note = QtWidgets.QLabel(
            "Light: arena dark, the ITI target is a bright sphere.\n"
            "Shadow: arena lit all session, the ITI target is a dark sphere.\n"
            "Lit background: arena lit all session, the ITI target is a brighter "
            "sphere on top of it.\n"
            "In both lit modes the field stays on during trials — only the target "
            "comes and goes.")
        beamer_note.setWordWrap(True)
        beamer_note.setStyleSheet("color:#777; font-size:9px;")
        sl.addWidget(beamer_note)

        # Background controls — only meaningful when the arena is lit.
        self._beamer_bg_w = QtWidgets.QWidget()
        bg_layout = QtWidgets.QVBoxLayout(self._beamer_bg_w)
        bg_layout.setContentsMargins(8, 2, 0, 0)
        bg_layout.setSpacing(4)
        self._beamer_bg_bright = self._make_bright_slider()
        self._beamer_bg_bright.setValue(30)
        bg_layout.addWidget(self._labeled_row("Background brightness:",
                                              self._beamer_bg_bright))
        self._beamer_bg_color_btn = QtWidgets.QPushButton()
        self._beamer_bg_color_btn.setFixedWidth(44)
        self._update_swatch(self._beamer_bg_color_btn, self._bg_color)
        self._beamer_bg_color_btn.clicked.connect(self._pick_bg_color)
        bg_layout.addWidget(self._labeled_row("Background colour:",
                                              self._beamer_bg_color_btn))
        self._beamer_contrast_lbl = QtWidgets.QLabel()
        self._beamer_contrast_lbl.setWordWrap(True)
        bg_layout.addWidget(self._beamer_contrast_lbl)
        sl.addWidget(self._beamer_bg_w)

        self._beamer_mode_combo.currentTextChanged.connect(self._beamer_mode_changed)
        self._beamer_bg_bright.valueChanged.connect(self._beamer_bg_changed)

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

        sl.addWidget(self._make_separator())

        # ── BNC outputs ───────────────────────────────────────────
        # Last, because the triggers reference the trial and intertrial phases
        # defined by the sections above.
        sl.addWidget(self._section_label("BNC Outputs"))
        bnc_note = QtWidgets.QLabel(
            "TTL pulses on the four BNC connectors. Each output can carry several "
            "triggers — a marker pulse and a train may run on the same connector, "
            "though two pulses landing together read as one edge downstream.\n"
            "Pulse timing is generated on the PC and is accurate to roughly "
            "±25 ms, so trains above ~10 Hz jitter noticeably. The pulse must "
            "always be shorter than the interval between pulses: re-triggering a "
            "line that is still high only extends the pulse, so it would never "
            "return low.")
        bnc_note.setWordWrap(True)
        bnc_note.setStyleSheet("color:#777; font-size:9px;")
        sl.addWidget(bnc_note)
        for bnc_id in range(1, self._BNC_COUNT + 1):
            sl.addWidget(self._build_bnc_block(bnc_id))

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

    # ── BNC outputs ───────────────────────────────────────────────────────────

    def _build_bnc_block(self, bnc_id: int) -> QtWidgets.QWidget:
        """Enable checkbox + add/remove trigger list for one BNC output."""
        block = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        chk = QtWidgets.QCheckBox(f"BNC {bnc_id}   ({self._BNC_PORT_LABELS[bnc_id]})")
        chk.setStyleSheet("margin-left:8px;")
        layout.addWidget(chk)

        body = QtWidgets.QWidget()
        body_l = QtWidgets.QVBoxLayout(body)
        body_l.setContentsMargins(8, 0, 0, 0)
        body_l.setSpacing(2)

        container = QtWidgets.QWidget()
        container.setLayout(QtWidgets.QVBoxLayout())
        container.layout().setContentsMargins(0, 0, 0, 0)
        container.layout().setSpacing(2)
        body_l.addWidget(container)

        add_btn = QtWidgets.QPushButton("+ Add trigger")
        add_btn.setFixedWidth(120)
        add_btn.clicked.connect(lambda _, i=bnc_id: self._bnc_add_trigger(i))
        body_l.addWidget(add_btn)
        layout.addWidget(body)

        # Index is bnc_id - 1, so the handlers can address blocks by id.
        self._bnc_blocks.append({"chk": chk, "body": body,
                                 "container": container, "rows": []})
        chk.toggled.connect(body.setVisible)
        body.setVisible(chk.isChecked())
        return block

    def _bnc_add_trigger(self, bnc_id: int, trigger: dict | None = None):
        """Append one trigger row to BNC *bnc_id*.

        The frequency spin is built for every row whatever the trigger type and
        merely hidden for the single-pulse types, so a row switched to a single
        pulse and back keeps its frequency — the same reason the screens block
        writes all three modes' settings on every save.
        """
        trig = dict(self._BNC_TRIGGER_DEFAULT if trigger is None else trigger)
        block = self._bnc_blocks[bnc_id - 1]

        row_w = QtWidgets.QWidget()
        row_l = QtWidgets.QHBoxLayout(row_w)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(4)

        type_combo = QtWidgets.QComboBox()
        type_combo.addItems(list(self._BNC_TRIGGER_LABELS.values()))
        type_combo.setCurrentText(self._BNC_TRIGGER_LABELS[trig["type"]])
        type_combo.setMinimumWidth(230)
        row_l.addWidget(type_combo)

        freq = QtWidgets.QDoubleSpinBox()
        freq.setRange(0.1, self._BNC_MAX_HZ)
        freq.setDecimals(2)
        freq.setSingleStep(0.5)
        freq.setValue(float(trig["frequency_hz"]))
        freq.setSuffix(" Hz")
        freq.setFixedWidth(90)
        freq_w = self._labeled_row("Frequency:", freq)
        row_l.addWidget(freq_w)

        row_l.addWidget(QtWidgets.QLabel("Pulse:"))
        pulse = QtWidgets.QSpinBox()
        pulse.setRange(1, self._BNC_MAX_PULSE_MS)
        pulse.setValue(int(trig["pulse_ms"]))
        pulse.setSuffix(" ms")
        pulse.setFixedWidth(100)
        row_l.addWidget(pulse)

        rm = QtWidgets.QPushButton("−")
        rm.setFixedWidth(28)
        row_l.addWidget(rm)
        row_l.addStretch()

        warn = QtWidgets.QLabel("")
        warn.setWordWrap(True)

        row_holder = QtWidgets.QWidget()
        hl = QtWidgets.QVBoxLayout(row_holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        hl.addWidget(row_w)
        hl.addWidget(warn)

        row = {"w": row_holder, "type": type_combo, "freq": freq,
               "freq_w": freq_w, "pulse": pulse, "warn": warn}
        block["rows"].append(row)
        block["container"].layout().addWidget(row_holder)

        rm.clicked.connect(lambda _, i=bnc_id, r=row: self._bnc_remove_trigger(i, r))
        type_combo.currentTextChanged.connect(lambda _, r=row: self._bnc_row_changed(r))
        freq.valueChanged.connect(lambda _, r=row: self._bnc_row_changed(r))
        pulse.valueChanged.connect(lambda _, r=row: self._bnc_row_changed(r))
        self._bnc_row_changed(row)

    def _bnc_remove_trigger(self, bnc_id: int, row: dict):
        """Drop one trigger row. Unlike the count-driven tables these rows come and
        go one at a time, so they are deleted rather than just detached."""
        block = self._bnc_blocks[bnc_id - 1]
        if row in block["rows"]:
            block["rows"].remove(row)
        row["w"].setParent(None)
        row["w"].deleteLater()

    def _bnc_row_changed(self, row: dict):
        """Show the frequency only for trains, and warn when the pulse is too wide.

        Re-triggering a pin that is already high does not make an edge — the
        firmware simply moves the off-time out. So a pulse at least as long as the
        period latches the line high for the whole train.
        """
        key = self._BNC_TRIGGER_KEYS[row["type"].currentText()]
        is_train = key in self._BNC_TRAIN_TRIGGERS
        row["freq_w"].setVisible(is_train)
        if not is_train:
            row["warn"].setText("")
            return

        period_ms = 1000.0 / row["freq"].value()
        pulse_ms = row["pulse"].value()
        if pulse_ms >= period_ms:
            row["warn"].setText(
                f"⚠  Pulse {pulse_ms} ms ≥ period {period_ms:.0f} ms — the line "
                f"would stay high for the whole train. Shorten the pulse or lower "
                f"the frequency.")
            row["warn"].setStyleSheet("color:#d04040; font-size:10px;")
        elif pulse_ms > period_ms - self._BNC_JITTER_MS:
            row["warn"].setText(
                f"⚠  Only {period_ms - pulse_ms:.0f} ms of gap — PC timing jitter "
                f"is ±{self._BNC_JITTER_MS} ms, so some pulses may merge.")
            row["warn"].setStyleSheet("color:#e06c00; font-size:10px;")
        else:
            row["warn"].setText(
                f"{period_ms:.0f} ms period, {pulse_ms} ms high "
                f"({100 * pulse_ms / period_ms:.0f} % duty).")
            row["warn"].setStyleSheet("color:#777; font-size:9px;")

    def _bnc_errors(self) -> list:
        """Blocking problems in the BNC block (empty when it is fine)."""
        errs = []
        for i, block in enumerate(self._bnc_blocks, start=1):
            if not block["chk"].isChecked():
                continue          # a disabled output is never emitted
            for n, row in enumerate(block["rows"], start=1):
                key = self._BNC_TRIGGER_KEYS[row["type"].currentText()]
                if key not in self._BNC_TRAIN_TRIGGERS:
                    continue
                period_ms = 1000.0 / row["freq"].value()
                if row["pulse"].value() >= period_ms:
                    errs.append(
                        f"BNC {i}, trigger {n}: pulse {row['pulse'].value()} ms is "
                        f"not shorter than the {period_ms:.0f} ms period.")
        return errs

    def _reward_errors(self) -> list:
        """Blocking problems in the reward block (empty when it is fine)."""
        errs = []
        # Two rewards on one lickport collapse into one: the state machine inverts
        # the {reward: port} map to decide which reward a lick belongs to, so the
        # later reward silently wins the port and the earlier one is never
        # delivered — while an "all rewards collected" trial still waits for both
        # and can therefore never end.
        if self._dist_type_combo.currentText() == "Fixed":
            seen = {}
            for i, combo in enumerate(self._fixed_port_combos, start=1):
                port = int(combo.currentText())
                if port in seen:
                    errs.append(f"Rewards {seen[port]} and {i} are both on lickport "
                                f"{port} — give each reward its own port.")
                else:
                    seen[port] = i
        return errs

    # ── Dynamic section builders ──────────────────────────────────────────────

    def _rebuild_reward_section(self):
        """Rebuild the reward-config table, the distribution panel and the
        per-reward screen rows — all three are sized by the reward count."""
        self._rebuild_reward_config()
        self._rebuild_dist_section()
        self._rebuild_screens_dynamic()

    def _rebuild_reward_config(self):
        """Rebuild the per-reward parameter table (volume + probability + hint)."""
        self._clear_widget(self._reward_config_container)
        container_layout = self._reward_config_container.layout()
        self._reward_rows = []
        n = int(self._count_combo.currentText())

        # Header row
        hdr = QtWidgets.QWidget()
        hdr_l = QtWidgets.QHBoxLayout(hdr)
        hdr_l.setContentsMargins(0, 0, 0, 0)
        for text, width in [("Reward", 55), ("Volume (µL)", 110), ("Probability", 100)]:
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

            vol_spin = self._make_volume_spin()
            row_l.addWidget(vol_spin)

            prob_spin = QtWidgets.QDoubleSpinBox()
            prob_spin.setRange(0.0, 1.0)
            prob_spin.setSingleStep(0.05)
            prob_spin.setDecimals(2)
            prob_spin.setValue(1.0)
            prob_spin.setFixedWidth(90)
            row_l.addWidget(prob_spin)

            # What that volume actually costs in pulses and licking time — see
            # _volume_hint. Recomputed live so a badly-rounding volume is visible
            # while it is being typed, not after a session has run.
            hint = QtWidgets.QLabel()
            hint.setStyleSheet("color:#888; font-size:10px;")
            vol_spin.valueChanged.connect(
                lambda _v, s=vol_spin, h=hint: self._update_volume_hint(s, h))
            row_l.addWidget(hint)

            row_l.addStretch()
            container_layout.addWidget(row_w)
            self._reward_rows.append((vol_spin, prob_spin, hint))
            self._update_volume_hint(vol_spin, hint)

    def _rebuild_dist_section(self):
        """Rebuild the distribution panel (fixed port map OR random spacing)."""
        # The widgets are destroyed and remade below, so anything the user typed
        # into them has to be carried over by hand — a rebuild is triggered by
        # changing the reward count, which must not quietly reset these.
        prev_spacing = (self._min_spacing_spin.value()
                        if self._min_spacing_spin is not None else 4)
        prev_exclude = (self._exclude_prev_chk.isChecked()
                        if self._exclude_prev_chk is not None else False)
        prev_reuse   = (self._reuse_prev_chk.isChecked()
                        if self._reuse_prev_chk is not None else False)

        self._clear_widget(self._dist_container)
        dist_layout = self._dist_container.layout()
        n    = int(self._count_combo.currentText())
        mode = self._dist_type_combo.currentText()

        self._fixed_port_combos  = []
        self._min_spacing_spin   = None
        self._spacing_warn_lbl   = None
        self._exclude_prev_chk   = None
        self._reuse_prev_chk     = None

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
                # Which port a reward sits on decides which pump delivers it, and
                # pumps differ — so moving a reward changes its pulse count.
                port_combo.currentIndexChanged.connect(
                    lambda _i: self._refresh_volume_hints(reload=False))
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
            self._min_spacing_spin.setValue(prev_spacing)
            self._min_spacing_spin.setFixedWidth(60)
            sp_l.addWidget(self._min_spacing_spin)
            sp_l.addStretch()
            dist_layout.addWidget(sp_row)

            self._spacing_warn_lbl = QtWidgets.QLabel("")
            self._spacing_warn_lbl.setStyleSheet("color:#aaa; font-size:10px;")
            dist_layout.addWidget(self._spacing_warn_lbl)

            self._reuse_prev_chk = QtWidgets.QCheckBox(
                "Use the same reward locations as the last session")
            self._reuse_prev_chk.setStyleSheet("font-size:10px;")
            dist_layout.addWidget(self._reuse_prev_chk)

            reuse_note = QtWidgets.QLabel(
                "Resolved per mouse when the session starts, from that mouse's last "
                "logged session. Only the lickports are reused — volumes, "
                "probabilities and the reward count come from this protocol. Falls "
                "back to a fresh random layout if the mouse has no usable previous "
                "session.")
            reuse_note.setWordWrap(True)
            reuse_note.setStyleSheet("color:#888; font-size:10px; margin-left:18px;")
            dist_layout.addWidget(reuse_note)

            self._exclude_prev_chk = QtWidgets.QCheckBox(
                "Exclude reward locations used in previous sessions")
            self._exclude_prev_chk.setStyleSheet("font-size:10px;")
            dist_layout.addWidget(self._exclude_prev_chk)

            # The two are opposites — reusing last session's ports while excluding
            # them is unsatisfiable, so ticking one clears and disables the other.
            self._reuse_prev_chk.toggled.connect(
                lambda on: (self._exclude_prev_chk.setChecked(False) if on else None,
                            self._exclude_prev_chk.setEnabled(not on)))
            self._exclude_prev_chk.toggled.connect(
                lambda on: (self._reuse_prev_chk.setChecked(False) if on else None,
                            self._reuse_prev_chk.setEnabled(not on)))

            # Set after wiring, so ticking one still clears/disables the other.
            self._reuse_prev_chk.setChecked(prev_reuse)
            self._exclude_prev_chk.setChecked(prev_exclude and not prev_reuse)

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

    # ── Reward volume helpers ─────────────────────────────────────────────────

    def _make_volume_spin(self, val: float = 5.0) -> QtWidgets.QDoubleSpinBox:
        """Return the µL spin box used for every reward volume in this editor."""
        sb = QtWidgets.QDoubleSpinBox()
        sb.setRange(self._VOL_MIN, self._VOL_MAX)
        sb.setSingleStep(self._VOL_STEP)
        sb.setDecimals(2)
        sb.setValue(val)
        sb.setSuffix(" µL")
        sb.setFixedWidth(100)
        return sb

    def _volume_hint(self, volume_ul: float) -> tuple:
        """(text, is_warning) describing what *volume_ul* costs on this rig.

        A volume only becomes a number of pulses once a pump is calibrated, and
        which pump depends on the distribution: a fixed map names its ports, a
        random one could land anywhere, so the hint reports the range across every
        calibrated port rather than pretending to know.
        """
        ports = self._configured_ports()
        plans = [p for p in (self._pump_calib.pulses_for(port, volume_ul)
                             for port in ports) if p is not None]
        if not plans:
            return ("no pump calibration — run the wizard on the Cleaning/Testing tab",
                    True)

        pulse_counts = sorted({n for n, _ in plans})
        actuals      = sorted({a for _, a in plans})
        n_txt = (f"{pulse_counts[0]}" if len(pulse_counts) == 1
                 else f"{pulse_counts[0]}–{pulse_counts[-1]}")
        a_txt = (f"{actuals[0]:.2f}" if len(actuals) == 1
                 else f"{actuals[0]:.2f}–{actuals[-1]:.2f}")
        secs  = self._pump_calib.min_delivery_s(pulse_counts[-1])

        # Rounding to whole pulses is the only way a volume can be delivered, so a
        # large gap between what was asked for and what is deliverable is worth
        # shouting about rather than silently honouring.
        worst = max(abs(a - volume_ul) for _, a in plans) / max(volume_ul, 1e-9)
        plural = "pulse" if pulse_counts == [1] else "pulses"
        text = f"≈ {a_txt} µL · {n_txt} {plural} · ≥{secs:.1f} s of licking"
        if len(plans) < len(ports):
            text += f"  ({len(ports) - len(plans)} port(s) uncalibrated)"
            return text, True
        return text, worst > self._VOL_ROUND_WARN

    def _update_volume_hint(self, spin, label):
        """Refresh one volume hint label from its spin box."""
        text, warn = self._volume_hint(spin.value())
        label.setText(text)
        label.setStyleSheet("color:#c8a000; font-size:10px;" if warn
                            else "color:#888; font-size:10px;")

    def _configured_ports(self) -> list:
        """The lickports this protocol could use — the fixed map, or all 16.

        A random distribution draws its ports at session start, so every port has to
        be calibrated for it to be startable; saying so here is more useful than a
        hint that only becomes wrong later.
        """
        # getattr: the hints are built by _rebuild_reward_config, which _build_ui
        # can reach before the distribution panel exists.
        combo = getattr(self, "_dist_type_combo", None)
        if (combo is not None and combo.currentText() == "Fixed"
                and self._fixed_port_combos):
            return [int(c.currentText()) for c in self._fixed_port_combos]
        return list(range(1, 17))

    def _refresh_volume_hints(self, reload: bool = True):
        """Update every volume hint, optionally re-reading the calibration file.

        Reloaded when the tab is shown, so the hints follow a wizard run on the
        Cleaning/Testing tab without the user having to restart the app; not
        reloaded when only the reward layout moved.
        """
        if reload:
            self._pump_calib.reload()
        for vol_spin, _prob, hint in self._reward_rows:
            self._update_volume_hint(vol_spin, hint)
        if self._delay_eq_vol_spin is not None and self._delay_eq_hint_lbl is not None:
            self._update_volume_hint(self._delay_eq_vol_spin, self._delay_eq_hint_lbl)

    def showEvent(self, event):
        super().showEvent(event)
        # Cheap: PumpCalibration.reload() short-circuits on an unchanged mtime.
        self._refresh_volume_hints()

    def _dist_type_changed(self):
        """The distribution panel changed shape — rebuild it, then re-hint.

        A random distribution can put a reward on any lickport, so the hint has to
        widen from one port's pulse count to the range across all sixteen.
        """
        self._rebuild_dist_section()
        self._refresh_volume_hints(reload=False)

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
        self._iti_reg_sphere_note = None
        self._iti_reg_dur_type = self._iti_reg_dur_spin = self._iti_reg_dur_max_spin = None
        self._iti_reg_dur_fixed_w = self._iti_reg_dur_max_w = None
        self._iti_rnd_diam_spin = self._iti_rnd_bright = self._iti_rnd_color_btn = None
        self._iti_rnd_margin_x_spin = self._iti_rnd_margin_y_spin = None
        self._iti_rnd_margin_radius_spin = None
        self._iti_rnd_beamer_chk = self._iti_rnd_contour_chk = self._iti_rnd_margin_chk = None
        self._iti_rnd_sphere_note = None
        self._iti_rnd_dur_type = self._iti_rnd_dur_spin = self._iti_rnd_dur_max_spin = None
        self._iti_rnd_dur_fixed_w = self._iti_rnd_dur_max_w = None

        iti_type = self._iti_type_combo.currentText() if self._iti_type_combo else "Fixed time"

        if iti_type == "Fixed time":
            self._iti_time_spin = self._make_dur_spin(5.0)
            layout.addWidget(self._labeled_row("Duration:", self._iti_time_spin))
            note = QtWidgets.QLabel(
                "A fixed-time ITI has no target region, so the beamer projects no "
                "sphere at any point in the session. The projection mode set in the "
                "Beamer section above still applies: in either lit mode the arena "
                "stays lit for the whole session, with nothing on it.")
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
            layout.addWidget(self._labeled_row("Sphere brightness:", self._iti_reg_bright))
            self._iti_reg_color_btn = QtWidgets.QPushButton()
            self._iti_reg_color_btn.setFixedWidth(44)
            self._update_swatch(self._iti_reg_color_btn, self._reg_color)
            self._iti_reg_color_btn.clicked.connect(self._pick_reg_color)
            layout.addWidget(self._labeled_row("Sphere colour:", self._iti_reg_color_btn))
            self._iti_reg_sphere_note = self._sphere_appearance_note()
            layout.addWidget(self._iti_reg_sphere_note)

            self._iti_reg_beamer_chk  = QtWidgets.QCheckBox("Beamer on")
            self._iti_reg_contour_chk = QtWidgets.QCheckBox("Contour on")
            layout.addWidget(self._toggle_row(self._iti_reg_beamer_chk, self._iti_reg_contour_chk))
            layout.addWidget(self._calib_hint())

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
            layout.addWidget(self._labeled_row("Sphere brightness:", self._iti_rnd_bright))
            self._iti_rnd_color_btn = QtWidgets.QPushButton()
            self._iti_rnd_color_btn.setFixedWidth(44)
            self._update_swatch(self._iti_rnd_color_btn, self._rnd_color)
            self._iti_rnd_color_btn.clicked.connect(self._pick_rnd_color)
            layout.addWidget(self._labeled_row("Sphere colour:", self._iti_rnd_color_btn))
            self._iti_rnd_sphere_note = self._sphere_appearance_note()
            layout.addWidget(self._iti_rnd_sphere_note)

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

        # Switching type drops any live target preview from the previous menu, back
        # to whatever the current mode shows between targets.
        self._send_beamer(self._beamer_baseline_cmd())
        self._update_beamer_mode_visibility()
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

    # ── Beamer mode ───────────────────────────────────────────────────────────

    def _beamer_mode(self) -> str:
        """The selected projection mode. Session-wide, so both region previews and
        the fixed-time menu read this one control."""
        return self._BEAMER_MODE_KEYS[self._beamer_mode_combo.currentText()]

    def _bg_eff_color(self) -> list:
        """The constant lit field's colour, scaled by its brightness."""
        return self._eff_color(self._bg_color, self._beamer_bg_bright)

    def _beamer_baseline_cmd(self) -> dict:
        """What the beamer shows between targets, mirroring the state machine's
        _beamer_baseline: blank in light mode, the lit field in the two lit modes
        (a diameter-0 sphere, so the field itself never switches)."""
        mode = self._beamer_mode()
        if mode not in self._BEAMER_LIT_MODES:
            return {"cmd": "clear"}
        field = self._bg_eff_color()
        return {"cmd": "sphere", "x_cm": 0.0, "y_cm": 0.0, "diameter_cm": 0.0,
                "mode": mode, "color": field, "field_color": field}

    def _beamer_mode_changed(self, *_):
        self._update_beamer_mode_visibility()
        self._refresh_beamer_preview()

    def _beamer_bg_changed(self, *_):
        self._update_contrast_warning()
        self._refresh_beamer_preview()

    def _pick_bg_color(self):
        color = QtWidgets.QColorDialog.getColor(self._bg_color, self, "Background colour")
        if color.isValid():
            self._bg_color = color
            self._update_swatch(self._beamer_bg_color_btn, color)
            self._beamer_bg_changed()

    def _refresh_beamer_preview(self):
        """Re-send whatever the live preview should currently be showing.

        Routed through the two region refreshers so that the "Beamer on" checkbox
        keeps deciding between the target and the bare baseline.
        """
        if self._iti_reg_beamer_chk is not None:
            self._reg_beamer_toggled(self._iti_reg_beamer_chk.isChecked())
        elif self._iti_rnd_beamer_chk is not None:
            self._refresh_rnd_beamer()
        else:
            self._send_beamer(self._beamer_baseline_cmd())

    def _update_beamer_mode_visibility(self):
        """Show the background controls only when the arena is actually lit, and note
        on the region panels when the sphere's own colour has no effect."""
        lit = self._beamer_mode() in self._BEAMER_LIT_MODES
        self._beamer_bg_w.setVisible(lit)
        self._update_contrast_warning()

        # In shadow mode the target is a hole punched in the field, so its colour and
        # brightness are not used — grey them out rather than let them look live.
        shadow = self._beamer_mode() == "shadow"
        for bright, btn, note in (
                (self._iti_reg_bright, self._iti_reg_color_btn, self._iti_reg_sphere_note),
                (self._iti_rnd_bright, self._iti_rnd_color_btn, self._iti_rnd_sphere_note)):
            if bright is not None:
                bright.setEnabled(not shadow)
            if btn is not None:
                btn.setEnabled(not shadow)
            if note is not None:
                note.setVisible(shadow)

    def _update_contrast_warning(self):
        """Warn when a lit_background sphere would be invisible.

        The projector is additive: the sphere is drawn *on top of* the lit field, so
        it only reads as a target if it is brighter than the field behind it. This is
        a live hint, not a save block — the rig, the floor and the camera decide what
        "visible enough" means, and only the experimenter can judge that.
        """
        if self._beamer_mode() != "lit_background":
            self._beamer_contrast_lbl.setVisible(False)
            return
        self._beamer_contrast_lbl.setVisible(True)
        bg = self._luminance(self._bg_eff_color())
        # Whichever region menu is up owns the sphere; with none built (fixed-time
        # ITI) there is no sphere to compare against.
        sphere = None
        if self._iti_reg_bright is not None:
            sphere = self._eff_color(self._reg_color, self._iti_reg_bright)
        elif self._iti_rnd_bright is not None:
            sphere = self._eff_color(self._rnd_color, self._iti_rnd_bright)
        if sphere is None:
            self._beamer_contrast_lbl.setText(
                "This ITI projects no sphere — the arena is simply lit for the "
                "whole session.")
            self._beamer_contrast_lbl.setStyleSheet("color:#aaa; font-size:10px;")
            return
        sph = self._luminance(sphere)
        if sph <= bg:
            self._beamer_contrast_lbl.setText(
                "⚠  The sphere is no brighter than the background — it will be "
                "invisible. Lower the background brightness or raise the sphere's.")
            self._beamer_contrast_lbl.setStyleSheet("color:#e06c00; font-size:10px;")
        else:
            self._beamer_contrast_lbl.setText(
                f"✓  Sphere out-shines the background ({sph:.0f} vs {bg:.0f} of 255).")
            self._beamer_contrast_lbl.setStyleSheet("color:#aaa; font-size:10px;")

    @staticmethod
    def _luminance(rgb) -> float:
        """Perceived brightness of an [r, g, b] triple (Rec. 709), 0–255."""
        return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]

    @staticmethod
    def _sphere_appearance_note() -> QtWidgets.QLabel:
        """The 'sphere colour is unused in shadow mode' hint, shown on both regions."""
        note = QtWidgets.QLabel(
            "In Shadow mode the target is a hole in the lit field, so its colour and "
            "brightness are not used — set the field in the Beamer section instead.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#777; font-size:9px;")
        return note

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
            "mode":        self._beamer_mode(),
            "color":       self._eff_color(self._reg_color, self._iti_reg_bright),
            "field_color": self._bg_eff_color(),
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
            "mode":        self._beamer_mode(),
            "color":       self._eff_color(self._rnd_color, self._iti_rnd_bright),
            "field_color": self._bg_eff_color(),
        })

    def _refresh_rnd_beamer(self, *_):
        """Live beamer preview for the random region: the target sphere only.
        The outer margin is a camera overlay and is never projected by the beamer."""
        if self._iti_rnd_beamer_chk and self._iti_rnd_beamer_chk.isChecked():
            self._project_rnd_sphere()
        else:
            # Not "clear": in a lit mode the field is what a session shows between
            # targets, so the preview has to fall back to it rather than go dark.
            self._send_beamer(self._beamer_baseline_cmd())

    def _reg_live(self, *_):
        self._emit_overlay()
        self._update_contrast_warning()   # sphere brightness feeds the contrast check
        if self._iti_reg_beamer_chk and self._iti_reg_beamer_chk.isChecked():
            self._project_reg_sphere()

    def _reg_beamer_toggled(self, on):
        if on:
            self._project_reg_sphere()
        else:
            self._send_beamer(self._beamer_baseline_cmd())

    def _pick_reg_color(self):
        c = QtWidgets.QColorDialog.getColor(self._reg_color, self, "Sphere colour")
        if c.isValid():
            self._reg_color = c
            if self._iti_reg_color_btn:
                self._update_swatch(self._iti_reg_color_btn, c)
            self._reg_live()

    def _rnd_live(self, *_):
        self._emit_overlay()
        self._update_contrast_warning()   # sphere brightness feeds the contrast check
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
        for i, (vol_spin, prob_spin, _hint) in enumerate(self._reward_rows):
            configs.append({
                "id": i + 1,
                "volume_ul": round(vol_spin.value(), 3),
                "probability": round(prob_spin.value(), 4),
            })

        dist_type = "fixed" if self._dist_type_combo.currentText() == "Fixed" else "random"
        if dist_type == "fixed":
            fixed_map = {
                str(i + 1): int(combo.currentText())
                for i, combo in enumerate(self._fixed_port_combos)
            }
            # min_spacing / exclude_previous / reuse_previous only apply to a random
            # distribution, but all are written either way so switching type and
            # back keeps the values.
            dist = {"type": "fixed", "fixed_map": fixed_map,
                    "min_spacing": 4, "exclude_previous": False,
                    "reuse_previous": False}
        else:
            spacing          = self._min_spacing_spin.value() if self._min_spacing_spin else 4
            exclude_previous = (self._exclude_prev_chk.isChecked()
                                if self._exclude_prev_chk else False)
            reuse_previous   = (self._reuse_prev_chk.isChecked()
                                if self._reuse_prev_chk else False)
            dist = {
                "type":             "random",
                "min_spacing":      spacing,
                "exclude_previous": exclude_previous,
                "reuse_previous":   reuse_previous,
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

        # ── Beamer ────────────────────────────────────────────────
        # The background is written whatever the mode, so switching to Light and back
        # never loses the field settings.
        beamer = {
            "mode":                  self._beamer_mode(),
            "background_color":      _color(self._bg_color),
            "background_brightness": _bright(self._beamer_bg_bright, 30),
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

        # ── BNC ───────────────────────────────────────────────────
        # Every row writes both frequency_hz and pulse_ms whatever its type, so
        # flipping a row between single pulse and train never loses a value.
        bnc = {
            "outputs": [
                {
                    "id":       i + 1,
                    "enabled":  bool(block["chk"].isChecked()),
                    "triggers": [
                        {"type":         self._BNC_TRIGGER_KEYS[r["type"].currentText()],
                         "frequency_hz": round(r["freq"].value(), 3),
                         "pulse_ms":     int(r["pulse"].value())}
                        for r in block["rows"]
                    ],
                }
                for i, block in enumerate(self._bnc_blocks)
            ],
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
                    "volume_ul":      round(self._delay_eq_vol_spin.value(), 3),
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
            "beamer": beamer,
            "screens": screens,
            "bnc": bnc,
        }

    # Raised by _require_volumes so _load_protocol can tell "this file predates
    # volume rewards" apart from "this file is broken in some other way" and say so.
    class LegacyProtocolError(Exception):
        pass

    @staticmethod
    def _require_volumes(d: dict):
        """Reject a protocol that still sets rewards as pump-on durations.

        There is deliberately no conversion. A pump's output is not proportional to
        how long it is energised — most of a short pulse is the startup transient —
        so any ms → µL guess would be a fabricated number driving a real experiment.
        """
        try:
            legacy = ("duration_ms" in d["rewards"]["delay"]
                      or any("duration_ms" in c for c in d["rewards"]["configs"]))
        except (KeyError, TypeError):
            return          # not this problem; the strict read below will report it
        if legacy:
            raise ProtocolPage.LegacyProtocolError(
                "This protocol predates volume-based rewards: it sets pump "
                "durations (duration_ms) where a volume (volume_ul) is now "
                "required.\n\nMilliseconds cannot be converted to microlitres — a "
                "pump's output is not proportional to how long it runs — so the "
                "reward volumes have to be entered again.\n\nStart from New and "
                "re-enter the settings, then save over the old file.")

    def _apply_protocol(self, d: dict):
        """Populate all widgets from a protocol dict.

        Rebuilds dynamic sections explicitly to avoid spurious signal chaining.

        The dict is read strictly — a missing key raises KeyError rather than
        silently substituting a default, so a malformed protocol is caught at load
        instead of quietly running an experiment the user did not configure. The
        `is not None` widget guards below are a different thing: they test whether
        the sub-widgets for the *selected* ITI type currently exist.
        """
        # Probed before touching any widget: this method applies top-down, so a
        # KeyError partway through would otherwise leave the editor holding a mix
        # of the old and new protocol, which the user could then save.
        self._require_volumes(d)
        # Unlike volumes, the beamer block *can* be derived from what it replaced, so
        # older files are upgraded here rather than rejected — also before any
        # widget is touched, for the same reason.
        normalise_protocol_beamer(d)

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
        for i, (vol_spin, prob_spin, hint) in enumerate(self._reward_rows):
            cfg = configs[i + 1]
            vol_spin.setValue(float(cfg["volume_ul"]))
            prob_spin.setValue(float(cfg["probability"]))
            self._update_volume_hint(vol_spin, hint)

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
            if self._reuse_prev_chk is not None:
                # .get(): protocols saved before this option existed have no such
                # key, and they are still perfectly valid files.
                self._reuse_prev_chk.setChecked(bool(dist.get("reuse_previous", False)))
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
        self._delay_eq_vol_spin.setValue(float(dl["volume_ul"]))
        self._update_volume_hint(self._delay_eq_vol_spin, self._delay_eq_hint_lbl)
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

        # ── BNC ──────────────────────────────────────────────────
        outputs = {o["id"]: o for o in d["bnc"]["outputs"]}
        for bnc_id, block in enumerate(self._bnc_blocks, start=1):
            out = outputs[bnc_id]
            for row in list(block["rows"]):
                self._bnc_remove_trigger(bnc_id, row)
            block["chk"].blockSignals(True)
            block["chk"].setChecked(bool(out["enabled"]))
            block["chk"].blockSignals(False)
            block["body"].setVisible(bool(out["enabled"]))
            for trig in out["triggers"]:
                self._bnc_add_trigger(bnc_id, trig)

        # ── Intertrial ───────────────────────────────────────────
        iti = d["intertrial"]
        _iti_label = {"time": "Fixed time", "fixed_region": "Fixed region",
                      "random_region": "Random region"}[iti["type"]]
        self._iti_type_combo.blockSignals(True)
        self._iti_type_combo.setCurrentText(_iti_label)
        self._iti_type_combo.blockSignals(False)

        # Sphere colours persist independently of which region widgets exist, so a
        # save→load round-trip keeps both regions' settings. These must be assigned
        # before _rebuild_iti_section, which seeds the widgets from them.
        reg = iti["region"]
        rnd = iti["random_region"]
        rc = reg["color"]
        nc = rnd["color"]
        self._reg_color = QtGui.QColor(int(rc[0]), int(rc[1]), int(rc[2]))
        self._rnd_color = QtGui.QColor(int(nc[0]), int(nc[1]), int(nc[2]))

        # ── Beamer ───────────────────────────────────────────────
        # Applied before the ITI rebuild too: the rebuild's tail projects the current
        # baseline and greys the sphere controls per mode, both of which read these.
        beamer = d["beamer"]
        bc = beamer["background_color"]
        self._bg_color = QtGui.QColor(int(bc[0]), int(bc[1]), int(bc[2]))
        self._update_swatch(self._beamer_bg_color_btn, self._bg_color)
        self._beamer_bg_bright.blockSignals(True)
        self._beamer_bg_bright.setValue(int(beamer["background_brightness"]))
        self._beamer_bg_bright.blockSignals(False)
        self._beamer_mode_combo.blockSignals(True)
        self._beamer_mode_combo.setCurrentText(
            self._BEAMER_MODE_LABELS[beamer["mode"]])
        self._beamer_mode_combo.blockSignals(False)

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
            self._iti_rnd_margin_x_spin.setValue(float(rnd["margin_x_cm"]))
            self._iti_rnd_margin_y_spin.setValue(float(rnd["margin_y_cm"]))
            self._iti_rnd_margin_radius_spin.setValue(float(rnd["margin_radius_cm"]))
            self._iti_rnd_dur_type.setCurrentText(
                self._DWELL_LABELS[0] if rnd["duration_type"] == "fixed"
                else self._DWELL_LABELS[1])
            self._iti_rnd_dur_spin.setValue(float(rnd["duration_s"]))
            self._iti_rnd_dur_max_spin.setValue(float(rnd["duration_max_s"]))
            self._update_iti_dur_visibility()
        # Re-run now that the sphere brightnesses are in: _rebuild_iti_section's own
        # call ran against the widgets' construction defaults.
        self._update_beamer_mode_visibility()
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
        except self.LegacyProtocolError as e:
            QtWidgets.QMessageBox.warning(self, "Outdated protocol", str(e))
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Load error", str(e))

    def _new_protocol(self):
        self._apply_protocol(self.DEFAULT_PROTOCOL)
        self._current_path = None
        self._path_label.setText("No file loaded")
        self._path_label.setStyleSheet("color:#888; font-size:10px;")

    def _save_protocol(self):
        # A train whose pulse is not shorter than its period would leave the BNC
        # latched high for the whole train, and two rewards sharing a lickport can
        # never both be collected — refuse to write either out.
        errors = self._reward_errors() + self._bnc_errors()
        if errors:
            QtWidgets.QMessageBox.warning(
                self, "Protocol settings",
                "This protocol cannot be saved:\n\n• " + "\n• ".join(errors))
            return

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
