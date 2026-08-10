"""Cleaning/Testing tab: manual controls for every actuator (beamer test
sphere + calibration, speaker tone test, screen patterns, LEDs, pumps, BNC),
plus an automated pump-flushing cycle. See CleaningPage for the panel and
BeamerCalibrationDialog for the beamer calibration wizard."""
import json
import math
import os
import time
from collections import deque

import numpy as np
from PyQt5 import QtWidgets, QtGui, QtCore

import shared_states
from beamer_controls import BEAMER_MODE_KEYS, BEAMER_MODE_LABELS
from hardware_state import BEAMER_LIT_MODES
from pump_calibration import PumpCalibration
from speaker_controls import SpeakerControls
from gui.pump_calibration_dialog import PumpCalibrationDialog
from gui.camera_calibration_dialog import CameraCalibrationDialog


class CleaningPage(QtWidgets.QWidget):
    """Cleaning / testing panel.

    Section order (top -> bottom):
      1. Beamer        — Test Sphere controls + Calibration wizard (via beamer_queue)
      2. Speaker       — tone test (frequency / length / overdrive volume)
      3. Screens       — pattern test for the two HDMI screens (via screen_queue)
      4. ALL OFF safety button  <- sits directly above LED controls
      5. LEDs          — manual toggle controls
      6. Pumps         — manual dose in µL + calibration wizard
      7. BNC           — manual pulse controls
      8. Automated Cleaning Cycle

    The pump block asks for a volume, not a duration, and delivers it as the
    same pulse train a session uses — so pressing a port here directly tests
    whether that pump still matches its calibration. The cleaning cycle below
    it stays in seconds, since flushing a line is not a reward.
    """

    _BTN_SIZE = 44   # px, square grid buttons

    # Projection modes for the test sphere. Shared with the protocol editor, so a
    # mode tested here is the same one a protocol stores.
    _BEAMER_MODE_LABELS = BEAMER_MODE_LABELS
    _BEAMER_MODE_KEYS   = BEAMER_MODE_KEYS

    def __init__(self, command_queue, beamer_queue=None, screen_queue=None,
                 undistort_enabled=None, undistort_reload=None):
        super().__init__()
        # WA_OpaquePaintEvent: Qt skips its background pre-fill; paintEvent
        # below fills every pixel itself instead.
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.command_queue = command_queue
        self.beamer_queue = beamer_queue   # None if the beamer process isn't wired in
        self.screen_queue = screen_queue   # None if the screen process isn't wired in
        # Camera-process flags the lens wizard needs: it measures on raw frames, so it
        # has to be able to switch the correction off and then make the camera reload.
        self.undistort_enabled = undistort_enabled
        self.undistort_reload = undistort_reload
        self.frame_provider = None         # set by run_gui: object with latest_frame()
        self._sphere_color = QtGui.QColor(255, 255, 255)  # Test Sphere colour
        self._bg_color     = QtGui.QColor(255, 255, 255)  # lit-field colour
        self._sphere_shown = False         # is a Test Sphere currently projected?
        self.led_state = {i: False for i in range(1, 17)}

        # Speaker tone generator (headphone jack). Init failure must not break the
        # rest of the panel, so guard it and disable playback if unavailable.
        try:
            self.speaker = SpeakerControls()
        except Exception as exc:
            print(f"[CleaningPage] speaker init failed: {exc}")
            self.speaker = None

        # Cleaning-cycle state
        self._cleaning_active = False
        self._pump_queue: deque = deque()
        self._active_pumps: dict = {}   # {pump_id: expected_end_time}
        self._cleaning_timer = QtCore.QTimer(self)
        self._cleaning_timer.timeout.connect(self._cleaning_tick)

        # Manual dose state, with its own timer/queue — deliberately separate
        # from the cleaning cycle's, since _stop_cleaning stops that timer and
        # clears its queues, which would otherwise cancel a manual dose too.
        self.pump_calib = PumpCalibration()
        self._manual_pending: dict = {}   # {pump_id: pulses left to fire}
        self._manual_timer = QtCore.QTimer(self)
        self._manual_timer.timeout.connect(self._manual_tick)

        # Everything scrolls, like the Protocol and Experiment tabs. Not
        # cosmetic: laid out flat, all eight sections push the page's minimum
        # height past the control monitor's work area, and a window that
        # can't fit its target monitor gets silently placed on another one
        # (the touch panels) instead. Adding a section here must never
        # reintroduce that.
        outer = QtWidgets.QVBoxLayout(self)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        content = QtWidgets.QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        root = QtWidgets.QVBoxLayout(content)
        root.setSpacing(10)
        root.setContentsMargins(10, 10, 10, 10)

        # ── 1. Beamer ─────────────────────────────────────────────
        root.addWidget(self._section_label("Beamer"))

        connected = self.beamer_queue is not None
        beamer_status_row = QtWidgets.QHBoxLayout()
        beamer_dot = QtWidgets.QLabel("●")
        beamer_dot.setStyleSheet(
            f"color:{'#1a9e1a' if connected else '#666'}; font-size:14px;"
        )
        beamer_status_row.addWidget(beamer_dot)
        beamer_status_row.addWidget(
            QtWidgets.QLabel("Connected" if connected else "Not connected")
        )
        beamer_status_row.addStretch()
        calib_btn = QtWidgets.QPushButton("Calibration")
        calib_btn.clicked.connect(self._open_beamer_calibration)
        beamer_status_row.addWidget(calib_btn)
        # The lens wizard lives beside the beamer's because it is driven *by* the
        # beamer — it projects the calibration pattern instead of using a printed one.
        lens_btn = QtWidgets.QPushButton("Camera lens…")
        lens_btn.clicked.connect(self._open_camera_calibration)
        beamer_status_row.addWidget(lens_btn)
        root.addLayout(beamer_status_row)

        # Test Sphere — project a sphere at an arena position (cm), in any of the
        # three projection modes a protocol can use.
        sphere_row = QtWidgets.QHBoxLayout()
        sphere_row.addWidget(QtWidgets.QLabel("Sphere  x:"))
        self.beamer_x = QtWidgets.QDoubleSpinBox()
        self.beamer_x.setRange(-100.0, 100.0)
        self.beamer_x.setSuffix(" cm")
        sphere_row.addWidget(self.beamer_x)
        sphere_row.addWidget(QtWidgets.QLabel("y:"))
        self.beamer_y = QtWidgets.QDoubleSpinBox()
        self.beamer_y.setRange(-100.0, 100.0)
        self.beamer_y.setSuffix(" cm")
        sphere_row.addWidget(self.beamer_y)
        sphere_row.addWidget(QtWidgets.QLabel("Ø:"))
        self.beamer_diam = QtWidgets.QDoubleSpinBox()
        self.beamer_diam.setRange(0.1, 200.0)
        self.beamer_diam.setSuffix(" cm")
        self.beamer_diam.setValue(10.0)
        sphere_row.addWidget(self.beamer_diam)
        root.addLayout(sphere_row)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("Mode:"))
        self.beamer_mode = QtWidgets.QComboBox()
        self.beamer_mode.addItems(list(self._BEAMER_MODE_LABELS.values()))
        self.beamer_mode.currentTextChanged.connect(self._on_beamer_mode_changed)
        mode_row.addWidget(self.beamer_mode)
        mode_row.addStretch()
        root.addLayout(mode_row)

        # Brightness + colour of the sphere (applies live while projecting).
        style_row = QtWidgets.QHBoxLayout()
        style_row.addWidget(QtWidgets.QLabel("Sphere:"))
        self.beamer_brightness = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.beamer_brightness.setRange(0, 100)
        self.beamer_brightness.setValue(100)
        self.beamer_brightness.valueChanged.connect(self._on_beamer_style_changed)
        style_row.addWidget(self.beamer_brightness)
        self._bright_label = QtWidgets.QLabel("100 %")
        self._bright_label.setFixedWidth(44)
        style_row.addWidget(self._bright_label)
        self._color_btn = QtWidgets.QPushButton()
        self._color_btn.setFixedWidth(44)
        self._color_btn.clicked.connect(self._pick_beamer_color)
        style_row.addWidget(self._color_btn)
        root.addLayout(style_row)

        # Same pair for the constant lit field, hidden in Light mode where the
        # background is always black.
        self._bg_row_w = QtWidgets.QWidget()
        bg_row = QtWidgets.QHBoxLayout(self._bg_row_w)
        bg_row.setContentsMargins(0, 0, 0, 0)
        bg_row.addWidget(QtWidgets.QLabel("Background:"))
        self.beamer_bg_brightness = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.beamer_bg_brightness.setRange(0, 100)
        self.beamer_bg_brightness.setValue(30)
        self.beamer_bg_brightness.valueChanged.connect(self._on_beamer_style_changed)
        bg_row.addWidget(self.beamer_bg_brightness)
        self._bg_bright_label = QtWidgets.QLabel("30 %")
        self._bg_bright_label.setFixedWidth(44)
        bg_row.addWidget(self._bg_bright_label)
        self._bg_color_btn = QtWidgets.QPushButton()
        self._bg_color_btn.setFixedWidth(44)
        self._bg_color_btn.clicked.connect(self._pick_beamer_bg_color)
        bg_row.addWidget(self._bg_color_btn)
        root.addWidget(self._bg_row_w)
        self._update_color_swatch()
        self._on_beamer_mode_changed()

        sphere_btn_row = QtWidgets.QHBoxLayout()
        project_btn = QtWidgets.QPushButton("Project")
        project_btn.clicked.connect(self._project_test_sphere)
        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_beamer)
        sphere_btn_row.addWidget(project_btn)
        sphere_btn_row.addWidget(clear_btn)
        root.addLayout(sphere_btn_row)
        root.addWidget(self._separator())

        # ── 2. Speaker ────────────────────────────────────────────
        root.addWidget(self._section_label("Speaker"))

        spk_row = QtWidgets.QHBoxLayout()
        spk_row.addWidget(QtWidgets.QLabel("Frequency:"))
        self.spk_freq = QtWidgets.QSpinBox()
        self.spk_freq.setRange(20, 20000)
        self.spk_freq.setValue(1000)
        self.spk_freq.setSuffix(" Hz")
        self.spk_freq.setFixedWidth(90)
        spk_row.addWidget(self.spk_freq)
        spk_row.addSpacing(12)
        spk_row.addWidget(QtWidgets.QLabel("Length:"))
        self.spk_length = QtWidgets.QDoubleSpinBox()
        self.spk_length.setRange(0.05, 30.0)
        self.spk_length.setSingleStep(0.1)
        self.spk_length.setDecimals(2)
        self.spk_length.setValue(0.5)
        self.spk_length.setSuffix(" s")
        self.spk_length.setFixedWidth(80)
        spk_row.addWidget(self.spk_length)
        spk_row.addStretch()
        root.addLayout(spk_row)

        # Volume is an overdrive factor: 100 % = clean sine, higher clips → louder.
        vol_row = QtWidgets.QHBoxLayout()
        vol_row.addWidget(QtWidgets.QLabel("Volume (overdrive):"))
        self.spk_volume = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.spk_volume.setRange(0, 1000)
        self.spk_volume.setValue(100)
        self._spk_vol_lbl = QtWidgets.QLabel("100 %")
        self._spk_vol_lbl.setFixedWidth(48)
        self.spk_volume.valueChanged.connect(
            lambda v: self._spk_vol_lbl.setText(f"{v} %"))
        vol_row.addWidget(self.spk_volume)
        vol_row.addWidget(self._spk_vol_lbl)
        root.addLayout(vol_row)

        spk_btn_row = QtWidgets.QHBoxLayout()
        play_btn = QtWidgets.QPushButton("Play tone")
        play_btn.clicked.connect(self._play_speaker)
        spk_stop_btn = QtWidgets.QPushButton("Stop")
        spk_stop_btn.clicked.connect(self._stop_speaker)
        spk_btn_row.addWidget(play_btn)
        spk_btn_row.addWidget(spk_stop_btn)
        root.addLayout(spk_btn_row)
        root.addWidget(self._separator())

        # ── 3. Screens ────────────────────────────────────────────
        root.addWidget(self._section_label("Screens"))

        # "Connected" means the screen process is running *and* the displays it
        # was told to use (shared_states.screen_indices) actually exist — a missing
        # display leaves its pattern window hidden, so commands go nowhere.
        n_configured = len(getattr(shared_states, "screen_indices", []) or [])
        n_attached = self._attached_screen_count()
        screens_connected = self.screen_queue is not None and n_attached > 0
        screens_status_row = QtWidgets.QHBoxLayout()
        screens_dot = QtWidgets.QLabel("●")
        screens_dot.setStyleSheet(
            f"color:{'#1a9e1a' if screens_connected and n_attached == n_configured else '#b8860b' if screens_connected else '#666'};"
            " font-size:14px;")
        screens_status_row.addWidget(screens_dot)
        if self.screen_queue is None:
            screens_text = "Not connected  (screen process not running)"
        else:
            screens_text = f"{n_attached} / {n_configured} HDMI screen(s) attached"
        screens_status_row.addWidget(QtWidgets.QLabel(screens_text))
        screens_status_row.addStretch()
        root.addLayout(screens_status_row)

        # One row per screen; each button overwrites that screen with its pattern.
        screen_patterns = [("Black", "black"),
                           ("White circles", "circles"),
                           ("Black zigzag", "zigzag")]
        for screen_num in range(1, max(2, n_configured) + 1):
            screen_row = QtWidgets.QHBoxLayout()
            screen_row.addWidget(QtWidgets.QLabel(f"Screen {screen_num}:"))
            for label, pattern in screen_patterns:
                btn = QtWidgets.QPushButton(label)
                btn.setEnabled(self.screen_queue is not None)
                btn.clicked.connect(
                    lambda _, n=screen_num, p=pattern: self._set_screen_pattern(n, p))
                screen_row.addWidget(btn)
            screen_row.addStretch()
            root.addLayout(screen_row)

        root.addWidget(self._separator())

        # ── 4. ALL OFF + LEDs ─────────────────────────────────────
        all_off_btn = QtWidgets.QPushButton("ALL OFF")
        all_off_btn.setFixedHeight(36)
        all_off_btn.setStyleSheet(
            "QPushButton { background:#8b0000; color:#fff; border-radius:4px; font-weight:bold; }"
            "QPushButton:hover { background:#b00000; }"
        )
        all_off_btn.clicked.connect(self._all_off)
        root.addWidget(all_off_btn)

        root.addWidget(self._section_label("LEDs"))
        self._led_buttons: dict = {}
        led_grid = self._make_grid()
        for i in range(1, 17):
            btn = QtWidgets.QPushButton(str(i))
            btn.setFixedSize(self._BTN_SIZE, self._BTN_SIZE)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, n=i: self._toggle_led(n, checked))
            self._led_buttons[i] = btn
            led_grid.addWidget(btn, (i - 1) // 8, (i - 1) % 8)
        root.addLayout(led_grid)
        root.addWidget(self._separator())

        # ── 5. Pumps — manual dose ────────────────────────────────
        # In µL, not ms, so this tests a pump the same way a protocol would
        # use it: the pump is never held on, only pulsed, exactly as a
        # session delivers a reward.
        pump_header = QtWidgets.QHBoxLayout()
        pump_header.addWidget(self._section_label("Pumps — Manual Dose"))
        pump_header.addStretch()
        pump_header.addWidget(QtWidgets.QLabel("Volume (µL):"))
        self.pump_volume = QtWidgets.QDoubleSpinBox()
        self.pump_volume.setRange(0.1, 50.0)
        self.pump_volume.setSingleStep(0.5)
        self.pump_volume.setDecimals(2)
        self.pump_volume.setValue(5.0)
        self.pump_volume.setFixedWidth(90)
        self.pump_volume.valueChanged.connect(lambda _v: self._update_pump_buttons())
        pump_header.addWidget(self.pump_volume)

        self._calib_btn = QtWidgets.QPushButton("Calibrate pumps…")
        self._calib_btn.setFixedHeight(26)
        self._calib_btn.clicked.connect(self._open_pump_calibration)
        pump_header.addWidget(self._calib_btn)
        root.addLayout(pump_header)

        pump_grid = self._make_grid()
        self._pump_buttons = {}
        for i in range(1, 17):
            btn = QtWidgets.QPushButton(str(i))
            btn.setFixedSize(self._BTN_SIZE, self._BTN_SIZE)
            btn.clicked.connect(lambda _, n=i: self._pulse_pump(n))
            pump_grid.addWidget(btn, (i - 1) // 8, (i - 1) % 8)
            self._pump_buttons[i] = btn
        root.addLayout(pump_grid)

        self._pump_hint = QtWidgets.QLabel("")
        self._pump_hint.setStyleSheet("color:#888; font-size:10px;")
        self._pump_hint.setWordWrap(True)
        root.addWidget(self._pump_hint)
        root.addWidget(self._separator())

        # ── 6. BNC ────────────────────────────────────────────────
        bnc_header = QtWidgets.QHBoxLayout()
        bnc_header.addWidget(self._section_label("BNC"))
        bnc_header.addStretch()
        bnc_header.addWidget(QtWidgets.QLabel("Duration (ms):"))
        self.bnc_duration = QtWidgets.QSpinBox()
        # Same 16-bit ceiling as the pump duration above.
        self.bnc_duration.setRange(1, 32767)
        self.bnc_duration.setValue(100)
        self.bnc_duration.setFixedWidth(80)
        bnc_header.addWidget(self.bnc_duration)
        root.addLayout(bnc_header)

        bnc_row = QtWidgets.QHBoxLayout()
        for i in range(1, 5):
            btn = QtWidgets.QPushButton(f"BNC {i}")
            btn.setFixedHeight(self._BTN_SIZE)
            btn.clicked.connect(lambda _, n=i: self._pulse_bnc(n))
            bnc_row.addWidget(btn)
        root.addLayout(bnc_row)
        root.addWidget(self._separator())

        # ── 7. Automated Cleaning Cycle ───────────────────────────
        root.addWidget(self._section_label("Automated Cleaning Cycle"))

        # Duration slider
        slider_row = QtWidgets.QHBoxLayout()
        slider_row.addWidget(QtWidgets.QLabel("Pump on-time:"))
        self._clean_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._clean_slider.setRange(1, 30)
        self._clean_slider.setValue(5)
        self._clean_slider.setTickInterval(5)
        self._clean_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
        self._clean_dur_label = QtWidgets.QLabel("5 s")
        self._clean_dur_label.setFixedWidth(36)
        self._clean_slider.valueChanged.connect(
            lambda v: self._clean_dur_label.setText(f"{v} s")
        )
        slider_row.addWidget(self._clean_slider)
        slider_row.addWidget(self._clean_dur_label)
        root.addLayout(slider_row)

        # Start / stop buttons
        clean_btn_row = QtWidgets.QHBoxLayout()
        self._start_clean_btn = QtWidgets.QPushButton("START CLEANING")
        self._start_clean_btn.setFixedHeight(34)
        self._start_clean_btn.setStyleSheet(
            "QPushButton { background:#1a6b1a; color:#fff; border-radius:4px; font-weight:bold; }"
            "QPushButton:hover { background:#247a24; }"
            "QPushButton:disabled { background:#333; color:#666; }"
        )
        self._start_clean_btn.clicked.connect(self._start_cleaning)

        self._stop_clean_btn = QtWidgets.QPushButton("EMERGENCY STOP")
        self._stop_clean_btn.setFixedHeight(34)
        self._stop_clean_btn.setEnabled(False)
        self._stop_clean_btn.setStyleSheet(
            "QPushButton { background:#8b0000; color:#fff; border-radius:4px; font-weight:bold; }"
            "QPushButton:hover { background:#b00000; }"
            "QPushButton:disabled { background:#333; color:#666; }"
        )
        self._stop_clean_btn.clicked.connect(self._stop_cleaning)
        clean_btn_row.addWidget(self._start_clean_btn)
        clean_btn_row.addWidget(self._stop_clean_btn)
        root.addLayout(clean_btn_row)

        # Progress bar + active-pump readout
        self._clean_progress = QtWidgets.QProgressBar()
        self._clean_progress.setRange(0, 16)
        self._clean_progress.setValue(0)
        self._clean_progress.setFormat("Idle")
        self._clean_progress.setTextVisible(True)
        root.addWidget(self._clean_progress)

        self._clean_status = QtWidgets.QLabel("")
        self._clean_status.setStyleSheet("color:#aaa; font-size:10px;")
        root.addWidget(self._clean_status)

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
    def _make_grid() -> QtWidgets.QGridLayout:
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(4)
        return grid

    # ── Queue helper ──────────────────────────────────────────────

    def _send(self, cmd: str):
        try:
            self.command_queue.put_nowait(cmd)
        except Exception:
            pass  # queue full — drop silently

    # ── Beamer control ────────────────────────────────────────────

    def _send_beamer(self, cmd: dict):
        if self.beamer_queue is None:
            return
        try:
            self.beamer_queue.put_nowait(cmd)
        except Exception:
            pass  # queue full — drop silently

    def set_frame_provider(self, provider):
        """Give the calibration dialog a live camera feed (object w/ latest_frame())."""
        self.frame_provider = provider

    @staticmethod
    def _scaled(color, slider):
        """A QColor scaled by a 0–100 slider → [r, g, b] (0–255)."""
        b = slider.value() / 100.0
        return [int(color.red() * b), int(color.green() * b), int(color.blue() * b)]

    def _effective_sphere_color(self):
        """The target sphere's colour, scaled by its brightness slider."""
        return self._scaled(self._sphere_color, self.beamer_brightness)

    def _effective_bg_color(self):
        """The constant lit field's colour, scaled by its brightness slider."""
        return self._scaled(self._bg_color, self.beamer_bg_brightness)

    def _beamer_mode_key(self):
        return self._BEAMER_MODE_KEYS[self.beamer_mode.currentText()]

    def _update_color_swatch(self):
        for btn, c in ((self._color_btn, self._sphere_color),
                       (self._bg_color_btn, self._bg_color)):
            btn.setStyleSheet(
                f"background: rgb({c.red()},{c.green()},{c.blue()}); "
                f"border:1px solid #888;")

    def _pick_beamer_color(self):
        c = QtWidgets.QColorDialog.getColor(self._sphere_color, self, "Sphere colour")
        if c.isValid():
            self._sphere_color = c
            self._update_color_swatch()
            self._on_beamer_style_changed()

    def _pick_beamer_bg_color(self):
        c = QtWidgets.QColorDialog.getColor(self._bg_color, self, "Background colour")
        if c.isValid():
            self._bg_color = c
            self._update_color_swatch()
            self._on_beamer_style_changed()

    def _on_beamer_style_changed(self, *_):
        self._bright_label.setText(f"{self.beamer_brightness.value()} %")
        self._bg_bright_label.setText(f"{self.beamer_bg_brightness.value()} %")
        # Re-project live only if a sphere is currently on the beamer.
        if self._sphere_shown:
            self._project_test_sphere()

    def _on_beamer_mode_changed(self, *_):
        # The background only exists in the two lit modes; in Light it is black.
        self._bg_row_w.setVisible(self._beamer_mode_key() in BEAMER_LIT_MODES)
        self._on_beamer_style_changed()

    def _project_test_sphere(self):
        self._send_beamer({
            "cmd":         "sphere",
            "x_cm":        self.beamer_x.value(),
            "y_cm":        self.beamer_y.value(),
            "diameter_cm": self.beamer_diam.value(),
            "mode":        self._beamer_mode_key(),
            "color":       self._effective_sphere_color(),
            "field_color": self._effective_bg_color(),
        })
        self._sphere_shown = True

    def _clear_beamer(self):
        self._send_beamer({"cmd": "clear"})
        self._sphere_shown = False

    def _open_beamer_calibration(self):
        self._sphere_shown = False
        BeamerCalibrationDialog(self.beamer_queue, self.frame_provider, self).exec_()

    def _open_camera_calibration(self):
        """Measure the camera's fisheye distortion off the projected dot grid."""
        # The wizard drives the beamer itself and needs the arena dark apart from its
        # own dots, so anything this page is projecting has to go first.
        self._sphere_shown = False
        self._send_beamer({"cmd": "clear"})
        CameraCalibrationDialog(self.beamer_queue, self.frame_provider,
                                self.undistort_enabled, self.undistort_reload,
                                self).exec_()

    # ── Speaker control ───────────────────────────────────────────

    def _play_speaker(self):
        if self.speaker is None:
            return
        # Slider is a percentage; 100 % → overdrive factor 1.0 (clean sine).
        self.speaker.produce_sound(
            self.spk_length.value(),
            self.spk_freq.value(),
            self.spk_volume.value() / 100.0,
        )

    def _stop_speaker(self):
        if self.speaker is not None:
            self.speaker.stop()

    # ── Screen control ────────────────────────────────────────────

    @staticmethod
    def _attached_screen_count() -> int:
        """How many of shared_states.screen_indices exist as real displays.

        The screen process leaves the window hidden for an index that isn't
        there, so this is what the pattern buttons can actually reach.
        """
        indices = getattr(shared_states, "screen_indices", []) or []
        n_displays = len(QtWidgets.QApplication.screens())
        return sum(1 for i in indices if 0 <= int(i) < n_displays)

    def _send_screen(self, cmd: dict):
        if self.screen_queue is None:
            return
        try:
            self.screen_queue.put_nowait(cmd)
        except Exception:
            pass  # queue full — drop silently

    def _set_screen_pattern(self, screen_num: int, pattern: str):
        """Overwrite screen *screen_num* (1-based) with *pattern*."""
        self._send_screen({"screen_id": screen_num, "pattern_id": pattern})

    # ── Manual control actions ────────────────────────────────────

    def _toggle_led(self, led_id: int, on: bool):
        self.led_state[led_id] = on
        self._send(f"LED:{led_id}:{'ON' if on else 'OFF'}")
        self._led_buttons[led_id].setStyleSheet(
            "QPushButton { background:#007acc; border-radius:4px; }" if on else ""
        )

    # ── Manual dose (volume → pulse train) ────────────────────────

    _MAX_CONCURRENT_PUMPS = 3   # same ceiling the cleaning cycle respects

    def _pulse_pump(self, pump_id: int):
        """Queue the pulses that deliver the requested volume at *pump_id*.

        The dose is a train of shared_states.pump_pulse_ms pulses at
        pump_refractory_ms — the same cadence a session uses — so what fires
        here is exactly what a mouse would get for that volume in a protocol.
        """
        plan = self.pump_calib.pulses_for(pump_id, self.pump_volume.value())
        if plan is None:
            QtWidgets.QMessageBox.information(
                self, "Not calibrated",
                f"Pump {pump_id} has no measured µL/pulse, so a volume cannot be "
                "converted into pulses.\n\nUse “Calibrate pumps…” first.")
            return
        n_pulses, actual_ul = plan
        # Re-clicking a port restarts its dose rather than stacking two trains.
        self._manual_pending[pump_id] = n_pulses
        self._pump_hint.setText(
            f"Pump {pump_id}: {n_pulses} × {int(shared_states.pump_pulse_ms)} ms "
            f"≈ {actual_ul:.2f} µL "
            f"(≈ {self.pump_calib.min_delivery_s(n_pulses):.1f} s)")
        if not self._manual_timer.isActive():
            self._manual_tick()   # first pulse without waiting out an interval
            self._manual_timer.start(int(shared_states.pump_refractory_ms))

    def _manual_tick(self):
        """Fire one pulse for each pending pump, up to the concurrency cap."""
        if not self._manual_pending:
            self._manual_timer.stop()
            return
        for pump_id in list(self._manual_pending)[:self._MAX_CONCURRENT_PUMPS]:
            self._send(f"MOS:{pump_id}:ON:{int(shared_states.pump_pulse_ms)}")
            self._manual_pending[pump_id] -= 1
            if self._manual_pending[pump_id] <= 0:
                del self._manual_pending[pump_id]
        if not self._manual_pending:
            self._manual_timer.stop()

    def _stop_manual_doses(self):
        """Cancel every queued manual pulse. Nothing may fire after this returns."""
        self._manual_timer.stop()
        self._manual_pending.clear()

    def _update_pump_buttons(self):
        """Grey out pumps with no calibration and show what the volume costs."""
        volume = self.pump_volume.value()
        uncalibrated = []
        for pump_id, btn in self._pump_buttons.items():
            plan = self.pump_calib.pulses_for(pump_id, volume)
            btn.setEnabled(plan is not None)
            if plan is None:
                uncalibrated.append(pump_id)
                btn.setToolTip(f"Pump {pump_id} is not calibrated — "
                               "run “Calibrate pumps…”")
            else:
                n, actual = plan
                btn.setToolTip(f"Pump {pump_id}: {n} pulses ≈ {actual:.2f} µL")
        if uncalibrated:
            self._pump_hint.setText(
                "Not calibrated: " + ", ".join(str(p) for p in uncalibrated)
                + " — those pumps cannot deliver a volume yet.")
        elif not self._manual_pending:
            self._pump_hint.setText("")

    def _open_pump_calibration(self):
        """Run the calibration wizard, with nothing else driving the pumps."""
        # Two writers on the same pumps would corrupt a measurement, and the wizard
        # counts every pulse it fires — so anything else pumping has to stop first.
        if self._cleaning_active:
            self._stop_cleaning()
        self._stop_manual_doses()
        dlg = PumpCalibrationDialog(self.command_queue, self)
        dlg.exec_()
        self.pump_calib.reload(force=True)
        self._update_pump_buttons()

    def showEvent(self, event):
        super().showEvent(event)
        # Cheap — PumpCalibration.reload() short-circuits on an unchanged mtime.
        self.pump_calib.reload()
        self._update_pump_buttons()

    def _pulse_bnc(self, bnc_id: int):
        self._send(f"BNC:{bnc_id}:PULSE:{self.bnc_duration.value()}")

    def _all_off(self):
        """Immediately turn off all LEDs and all pumps.

        Cancels queued work as well as the hardware — otherwise a manual
        dose or cleaning cycle still in flight would switch a pump straight
        back on moments later.
        """
        self._stop_manual_doses()
        if self._cleaning_active:
            self._stop_cleaning()
        for i in range(1, 17):
            self._send(f"LED:{i}:OFF")
            self._send(f"MOS:{i}:OFF:0")
            self.led_state[i] = False
            self._led_buttons[i].setChecked(False)
            self._led_buttons[i].setStyleSheet("")

    # ── Automated cleaning cycle ──────────────────────────────────

    def _start_cleaning(self):
        if self._cleaning_active:
            return
        self._cleaning_active = True
        self._pump_queue = deque(range(1, 17))
        self._active_pumps = {}

        self._start_clean_btn.setEnabled(False)
        self._stop_clean_btn.setEnabled(True)
        self._clean_slider.setEnabled(False)
        self._clean_progress.setValue(0)
        self._clean_progress.setFormat("Running…  %v / 16")
        self._clean_status.setText("Starting cleaning cycle…")

        # Fire immediately, then every 250 ms to poll for pump expiry
        self._cleaning_tick()
        self._cleaning_timer.start(250)

    def _cleaning_tick(self):
        """Called every 250 ms while the cleaning cycle is running.

        Removes pumps whose time has elapsed (on the Python-side clock)
        and fills empty slots up to the 3-concurrent limit.
        """
        now = time.monotonic()
        dur_s = self._clean_slider.value()
        dur_ms = dur_s * 1000

        # Release slots for pumps that should be finished
        expired = [pid for pid, end in self._active_pumps.items() if now >= end]
        for pid in expired:
            del self._active_pumps[pid]

        # Fill available slots from the queue
        while len(self._active_pumps) < 3 and self._pump_queue:
            pid = self._pump_queue.popleft()
            self._send(f"MOS:{pid}:ON:{dur_ms}")
            self._active_pumps[pid] = now + dur_s

        # Update UI
        done = 16 - len(self._pump_queue) - len(self._active_pumps)
        self._clean_progress.setValue(done)
        if self._active_pumps:
            self._clean_status.setText(
                f"Active pumps: {sorted(self._active_pumps)}"
            )

        # Finished?
        if not self._pump_queue and not self._active_pumps:
            self._cleaning_timer.stop()
            self._cleaning_active = False
            self._clean_progress.setFormat("Complete!  16 / 16")
            self._clean_progress.setValue(16)
            self._clean_status.setText("All pumps cleaned.")
            self._start_clean_btn.setEnabled(True)
            self._stop_clean_btn.setEnabled(False)
            self._clean_slider.setEnabled(True)

    def _stop_cleaning(self):
        """Emergency stop: halt the cycle and turn off every pump immediately."""
        self._cleaning_timer.stop()
        self._cleaning_active = False
        self._pump_queue.clear()
        self._active_pumps.clear()

        for i in range(1, 17):
            self._send(f"MOS:{i}:OFF:0")

        self._clean_progress.setFormat("Stopped")
        self._clean_status.setText("Cleaning stopped — all pumps off.")
        self._start_clean_btn.setEnabled(True)
        self._stop_clean_btn.setEnabled(False)
        self._clean_slider.setEnabled(True)


class _CalibFeed(QtWidgets.QWidget):
    """Camera feed with draggable colour markers for beamer↔camera calibration.

    Markers are stored in frame-fraction coordinates (0–1) so they map directly
    to DLC-normalised positions: the preview frame shown here is the same cropped
    region DLC runs on, so a fraction of one is a fraction of the other. The
    parent reads markers_uv() to build the point correspondences.
    """

    _GRAB_PX = 20   # click tolerance (px) for grabbing a marker

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self.setMinimumSize(340, 300)
        self._pixmap = None
        self._colors = [QtGui.QColor(*c) for c in colors]
        # Start markers spread horizontally near the centre.
        n = len(colors)
        self._markers = [[0.35 + 0.3 * i / max(1, n - 1), 0.5] for i in range(n)]
        self._drag = None

    def set_frame(self, frame):
        if frame is not None:
            h, w = frame.shape[:2]
            qimg = QtGui.QImage(frame.tobytes(), w, h, w,
                                QtGui.QImage.Format_Grayscale8)
            self._pixmap = QtGui.QPixmap.fromImage(qimg)
        self.update()

    def markers_uv(self):
        return [tuple(m) for m in self._markers]

    def set_marker(self, idx, u, v):
        self._markers[idx] = [min(1.0, max(0.0, u)), min(1.0, max(0.0, v))]
        self.update()

    # ── geometry: map between frame-fraction and widget pixels ────────────────

    def _frame_rect(self):
        """(off_x, off_y, w, h) of the letterboxed frame within the widget."""
        if self._pixmap is None or self._pixmap.width() == 0:
            side = min(self.width(), self.height())
            return ((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale = min(self.width() / pw, self.height() / ph)
        w, h = pw * scale, ph * scale
        return ((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _uv_to_xy(self, u, v):
        ox, oy, w, h = self._frame_rect()
        return (ox + u * w, oy + v * h)

    def _xy_to_uv(self, x, y):
        ox, oy, w, h = self._frame_rect()
        if w <= 0 or h <= 0:
            return (0.0, 0.0)
        return ((x - ox) / w, (y - oy) / h)

    # ── painting + dragging ───────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#111"))
        ox, oy, w, h = self._frame_rect()
        if self._pixmap is not None:
            painter.drawPixmap(
                int(ox), int(oy),
                self._pixmap.scaled(int(w), int(h),
                                    QtCore.Qt.IgnoreAspectRatio,
                                    QtCore.Qt.SmoothTransformation),
            )
        else:
            painter.setPen(QtGui.QColor("#aaa"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "Waiting for camera…")

        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        for i, (u, v) in enumerate(self._markers):
            x, y = self._uv_to_xy(u, v)
            painter.setPen(QtGui.QPen(QtGui.QColor("#000"), 2))
            painter.setBrush(self._colors[i])
            painter.drawEllipse(QtCore.QPointF(x, y), 8, 8)
            painter.setPen(QtGui.QPen(QtGui.QColor("#fff"), 1))
            painter.drawLine(QtCore.QPointF(x - 11, y), QtCore.QPointF(x + 11, y))
            painter.drawLine(QtCore.QPointF(x, y - 11), QtCore.QPointF(x, y + 11))
        painter.end()

    def mousePressEvent(self, ev):
        best, best_d = None, self._GRAB_PX
        for i, (u, v) in enumerate(self._markers):
            x, y = self._uv_to_xy(u, v)
            d = ((x - ev.pos().x()) ** 2 + (y - ev.pos().y()) ** 2) ** 0.5
            if d <= best_d:
                best, best_d = i, d
        self._drag = best

    def mouseMoveEvent(self, ev):
        if self._drag is not None:
            u, v = self._xy_to_uv(ev.pos().x(), ev.pos().y())
            self.set_marker(self._drag, u, v)

    def mouseReleaseEvent(self, ev):
        self._drag = None


class BeamerCalibrationDialog(QtWidgets.QDialog):
    """Stepped wizard that calibrates the beamer's coordinate frame.

    Each step drives the live projection through *beamer_queue*:
      1. Intro.
      2. Centre & orientation — locate the true centre of the projection area
         (the beamer is not screen-centred) and set the axis directions.
      3. Projection area — the max usable radius, sized from the true centre.
      4. Diameter measurement — circles projected from the true centre -> px_per_cm
         via through-origin least squares.
      5. Camera mapping — 2 cm points projected inside the boundary; the user drags
         colour markers onto them in a live camera feed -> a DLC<->beamer affine.
      6. Finish -> writes beamer_calibration.json and reloads the projector.

    Sends raw-pixel ("sphere_px") commands throughout, since it runs before
    any cm scale exists; only the final saved JSON is in cm terms.
    """

    _MARKER_PX = 40   # small sphere used as the origin/probe marker
    _DLC_COLORS = [(230, 50, 50), (50, 195, 90), (70, 130, 240)]
    _DLC_NAMES = ["Red", "Green", "Blue"]

    def __init__(self, beamer_queue, frame_provider=None, parent=None):
        super().__init__(parent)
        self.beamer_queue = beamer_queue
        self.frame_provider = frame_provider
        self.setWindowTitle("Beamer Calibration")
        self.setModal(True)
        self.setMinimumSize(480, 560)

        # ── Beamer screen size (read from the GUI's own QApplication) ─────────
        idx = int(getattr(shared_states, "beamer_screen_index", 1))
        screens = QtWidgets.QApplication.screens()
        if 0 <= idx < len(screens):
            geo = screens[idx].geometry()
            self._screen_w, self._screen_h = geo.width(), geo.height()
        else:
            self._screen_w, self._screen_h = 1920, 1080
        self._center = (self._screen_w / 2.0, self._screen_h / 2.0)

        # ── Calibration state ────────────────────────────────────────────────
        self._diam_presets = [int(f * self._screen_h) for f in (0.2, 0.4, 0.6, 0.8)]
        self._px_per_cm = None
        self._proj_diam_px = int(0.5 * self._screen_h)
        self._origin_px = [self._center[0], self._center[1]]
        self._x_sign = 1
        self._y_sign = 1
        self._probe = (0.0, 0.0)   # cm point currently shown on the centre page
        self._cam_to_beamer = None  # 2×3 affine from the camera-mapping step

        # Timer that pushes live frames into the camera-mapping feed.
        self._feed_timer = QtCore.QTimer(self)
        self._feed_timer.timeout.connect(self._update_feed)

        # ── Build pages + nav ────────────────────────────────────────────────
        self._pages = []           # parallel list of ("kind", meta) per stacked page
        self.stack = QtWidgets.QStackedWidget()
        self._build_pages()

        self.back_btn = QtWidgets.QPushButton("Back")
        self.next_btn = QtWidgets.QPushButton("Next")
        cancel_btn = QtWidgets.QPushButton("Cancel")
        self.back_btn.clicked.connect(self._back)
        self.next_btn.clicked.connect(self._next)
        cancel_btn.clicked.connect(self.reject)
        nav = QtWidgets.QHBoxLayout()
        nav.addWidget(cancel_btn)
        nav.addStretch()
        nav.addWidget(self.back_btn)
        nav.addWidget(self.next_btn)

        root = QtWidgets.QVBoxLayout(self)
        root.addWidget(self.stack)
        root.addLayout(nav)

        self._goto(0)

    # ── Queue helper ──────────────────────────────────────────────────────────

    def _send(self, cmd: dict):
        if self.beamer_queue is None:
            return
        try:
            self.beamer_queue.put_nowait(cmd)
        except Exception:
            pass

    # ── Page construction ─────────────────────────────────────────────────────

    def _add_page(self, kind, meta, widget):
        self._pages.append((kind, meta))
        self.stack.addWidget(widget)

    def _build_pages(self):
        lens = getattr(shared_states, "beamer_lens_distance_cm", 196)

        # intro
        intro = QtWidgets.QLabel(
            "This wizard calibrates the beamer.\n\n"
            "You will (1) find the true centre of the projection area, (2) set the "
            "usable area, (3) measure a few circles with a ruler, and (4) map the "
            "camera/DeepLabCut coordinates onto the beamer.\n\n"
            f"The beamer lens is {lens} cm from the projection surface.\n\n"
            "Have a ruler / tape measure ready, then press Next."
        )
        intro.setWordWrap(True)
        self._add_page("intro", None, intro)

        # centre & orientation
        coord = QtWidgets.QWidget()
        clay = QtWidgets.QVBoxLayout(coord)
        clbl = QtWidgets.QLabel(
            "Move the marker onto the true centre of the projection area with the "
            "arrows (the beamer is not perfectly centred). Then press “Test +x/+y”: "
            "if the marker moves the wrong way, toggle Flip X / Flip Y until +x is "
            "to the right and +y is toward the front."
        )
        clbl.setWordWrap(True)
        clay.addWidget(clbl)

        pad = QtWidgets.QGridLayout()
        up, down = QtWidgets.QPushButton("▲"), QtWidgets.QPushButton("▼")
        left, right = QtWidgets.QPushButton("◀"), QtWidgets.QPushButton("▶")
        for b in (up, down, left, right):
            b.setFixedSize(40, 40)
        up.clicked.connect(lambda: self._nudge_origin(0, -1))
        down.clicked.connect(lambda: self._nudge_origin(0, 1))
        left.clicked.connect(lambda: self._nudge_origin(-1, 0))
        right.clicked.connect(lambda: self._nudge_origin(1, 0))
        pad.addWidget(up, 0, 1)
        pad.addWidget(left, 1, 0)
        pad.addWidget(right, 1, 2)
        pad.addWidget(down, 2, 1)
        clay.addLayout(pad)

        srow = QtWidgets.QHBoxLayout()
        srow.addWidget(QtWidgets.QLabel("Step:"))
        self._step_spin = QtWidgets.QSpinBox()
        self._step_spin.setRange(1, 200)
        self._step_spin.setValue(10)
        self._step_spin.setSuffix(" px")
        srow.addWidget(self._step_spin)
        srow.addStretch()
        clay.addLayout(srow)

        frow = QtWidgets.QHBoxLayout()
        self._flip_x = QtWidgets.QCheckBox("Flip X")
        self._flip_y = QtWidgets.QCheckBox("Flip Y")
        self._flip_x.toggled.connect(self._on_flip)
        self._flip_y.toggled.connect(self._on_flip)
        frow.addWidget(self._flip_x)
        frow.addWidget(self._flip_y)
        origin_btn = QtWidgets.QPushButton("Show centre")
        test_btn = QtWidgets.QPushButton("Test +x/+y")
        origin_btn.clicked.connect(lambda: self._set_probe(0.0, 0.0))
        test_btn.clicked.connect(lambda: self._set_probe(5.0, 5.0))
        frow.addWidget(origin_btn)
        frow.addWidget(test_btn)
        clay.addLayout(frow)
        clay.addStretch()
        self._add_page("coord", None, coord)

        # projection area
        area = QtWidgets.QWidget()
        alay = QtWidgets.QVBoxLayout(area)
        albl = QtWidgets.QLabel(
            "Adjust the slider until the projected white circle fills the usable "
            "projection area (the arena floor), then press Next. The circle is "
            "centred on the true centre from the previous step."
        )
        albl.setWordWrap(True)
        alay.addWidget(albl)
        arow = QtWidgets.QHBoxLayout()
        arow.addWidget(QtWidgets.QLabel("Size:"))
        self._area_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._area_slider.setRange(int(0.05 * self._screen_h), int(self._screen_h))
        self._area_slider.setValue(self._proj_diam_px)
        self._area_slider.valueChanged.connect(self._on_area_slider)
        arow.addWidget(self._area_slider)
        self._area_label = QtWidgets.QLabel("")
        self._area_label.setFixedWidth(130)
        arow.addWidget(self._area_label)
        alay.addLayout(arow)
        alay.addStretch()
        self._add_page("area", None, area)

        # diameter measurement
        self._diam_inputs = []
        for k, diam_px in enumerate(self._diam_presets):
            page = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(page)
            lbl = QtWidgets.QLabel(
                f"Circle {k + 1} of {len(self._diam_presets)}\n\n"
                "A white circle is projected from the true centre. Measure its "
                "diameter on the projection surface and enter it below."
            )
            lbl.setWordWrap(True)
            lay.addWidget(lbl)
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel("Measured diameter:"))
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(0.0, 500.0)
            spin.setDecimals(2)
            spin.setSuffix(" cm")
            row.addWidget(spin)
            row.addStretch()
            lay.addLayout(row)
            lay.addStretch()
            self._diam_inputs.append(spin)
            self._add_page("diameter", k, page)

        # camera / DLC mapping
        dlc = QtWidgets.QWidget()
        dlay = QtWidgets.QVBoxLayout(dlc)
        dlbl = QtWidgets.QLabel(
            "Map the camera to the beamer. Use the buttons to light each 2 cm "
            "point in turn, find it in the live feed, and drag the matching "
            "colour marker onto it. Do all three, then press Next."
        )
        dlbl.setWordWrap(True)
        dlay.addWidget(dlbl)
        self._feed = _CalibFeed(self._DLC_COLORS)
        dlay.addWidget(self._feed, stretch=1)
        locate_row = QtWidgets.QHBoxLayout()
        locate_row.addWidget(QtWidgets.QLabel("Light point:"))
        for k, name in enumerate(self._DLC_NAMES):
            btn = QtWidgets.QPushButton(name)
            r, g, b = self._DLC_COLORS[k]
            btn.setStyleSheet(f"color:#000; background: rgb({r},{g},{b}); font-weight:bold;")
            btn.clicked.connect(lambda _, i=k: self._locate_point(i))
            locate_row.addWidget(btn)
        off_btn = QtWidgets.QPushButton("Off")
        off_btn.clicked.connect(lambda: self._send({"cmd": "clear"}))
        locate_row.addWidget(off_btn)
        dlay.addLayout(locate_row)
        self._add_page("dlc", None, dlc)

        # finish
        fin = QtWidgets.QWidget()
        flay = QtWidgets.QVBoxLayout(fin)
        self._finish_label = QtWidgets.QLabel("")
        self._finish_label.setWordWrap(True)
        flay.addWidget(self._finish_label)
        flay.addStretch()
        self._add_page("finish", None, fin)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _goto(self, index):
        index = max(0, min(index, len(self._pages) - 1))
        self._feed_timer.stop()          # stop live feed unless the new page is DLC
        self.stack.setCurrentIndex(index)
        self.back_btn.setEnabled(index > 0)
        self.next_btn.setText("Finish" if index == len(self._pages) - 1 else "Next")
        self._on_page_enter(index)

    def _back(self):
        self._goto(self.stack.currentIndex() - 1)

    def _next(self):
        idx = self.stack.currentIndex()
        kind, meta = self._pages[idx]
        if kind == "diameter":
            if self._diam_inputs[meta].value() <= 0:
                QtWidgets.QMessageBox.warning(
                    self, "Measurement needed",
                    "Enter the measured diameter (cm) before continuing.",
                )
                return
            # Recompute the scale once the last circle has been measured.
            if meta == len(self._diam_presets) - 1:
                self._compute_px_per_cm()
        elif kind == "dlc":
            self._compute_affine()
        if idx == len(self._pages) - 1:
            self._finish()
            return
        self._goto(idx + 1)

    def _on_page_enter(self, index):
        kind, meta = self._pages[index]
        if kind == "intro":
            self._send({"cmd": "clear"})
        elif kind == "coord":
            self._project_probe()
        elif kind == "area":
            self._on_area_slider(self._area_slider.value())
        elif kind == "diameter":
            self._send({"cmd": "sphere_px",
                        "cx": self._origin_px[0], "cy": self._origin_px[1],
                        "diameter_px": self._diam_presets[meta], "mode": "light"})
        elif kind == "dlc":
            self._update_feed()
            self._feed_timer.start(60)
            self._locate_point(0)        # light the first point as a starting hint
        elif kind == "finish":
            self._send({"cmd": "clear"})
            self._finish_label.setText(self._summary_text())

    # ── Calibration maths / live projection ───────────────────────────────────

    def _compute_px_per_cm(self):
        # Through-origin least squares fit  px = k·cm  →  k = Σ(px·cm)/Σ(cm²).
        num = den = 0.0
        for diam_px, spin in zip(self._diam_presets, self._diam_inputs):
            cm = spin.value()
            if cm > 0:
                num += diam_px * cm
                den += cm * cm
        self._px_per_cm = (num / den) if den > 0 else (self._screen_h / 20.0)

    def _on_area_slider(self, value):
        self._proj_diam_px = value
        cm = (value / self._px_per_cm) if self._px_per_cm else None
        self._area_label.setText(f"{value} px" + (f"  ≈ {cm:.1f} cm" if cm else ""))
        self._send({"cmd": "sphere_px",
                    "cx": self._origin_px[0], "cy": self._origin_px[1],
                    "diameter_px": value, "mode": "light"})

    def _nudge_origin(self, dx, dy):
        step = self._step_spin.value()
        self._origin_px[0] += dx * step
        self._origin_px[1] += dy * step
        self._project_probe()

    def _on_flip(self):
        self._x_sign = -1 if self._flip_x.isChecked() else 1
        self._y_sign = -1 if self._flip_y.isChecked() else 1
        self._project_probe()

    def _set_probe(self, x_cm, y_cm):
        self._probe = (x_cm, y_cm)
        self._project_probe()

    def _project_probe(self):
        ppc = self._px_per_cm or (self._screen_h / 20.0)
        x_cm, y_cm = self._probe
        cx = self._origin_px[0] + x_cm * ppc * self._x_sign
        cy = self._origin_px[1] + y_cm * ppc * self._y_sign
        self._send({"cmd": "sphere_px", "cx": cx, "cy": cy,
                    "diameter_px": self._MARKER_PX, "mode": "light"})

    # ── Camera / DLC mapping ───────────────────────────────────────────────────

    def _update_feed(self):
        if self.frame_provider is not None:
            self._feed.set_frame(self.frame_provider.latest_frame())

    def _dlc_beamer_points(self):
        """Three beamer-pixel points on a triangle inside the projection boundary."""
        r = 0.6 * (self._proj_diam_px / 2.0)
        pts = []
        for k in range(3):
            ang = math.radians(90 + k * 120)   # 90°, 210°, 330°
            pts.append((self._origin_px[0] + r * math.cos(ang),
                        self._origin_px[1] - r * math.sin(ang)))
        return pts

    def _locate_point(self, k):
        """Light only beamer point k (2 cm) so the user can find it in the feed.

        Projected white, not the marker colour — the tracking camera is
        grayscale, so white is most reliably visible. Lighting one point at
        a time is what disambiguates them; colour only labels the correspondence.
        """
        bx, by = self._dlc_beamer_points()[k]
        diam = max(6.0, 2.0 * (self._px_per_cm or (self._screen_h / 20.0)))
        self._send({"cmd": "sphere_px", "cx": bx, "cy": by,
                    "diameter_px": diam, "mode": "light"})

    def _compute_affine(self):
        """Solve the affine mapping normalised camera coords (u,v) → beamer px."""
        uv = self._feed.markers_uv()
        pts = self._dlc_beamer_points()
        try:
            A = np.array([[u, v, 1.0] for (u, v) in uv])
            bx = np.array([p[0] for p in pts])
            by = np.array([p[1] for p in pts])
            cx = np.linalg.solve(A, bx)      # [a, b, c]
            cy = np.linalg.solve(A, by)      # [d, e, f]
            self._cam_to_beamer = [list(map(float, cx)), list(map(float, cy))]
        except Exception as exc:
            self._cam_to_beamer = None
            QtWidgets.QMessageBox.warning(
                self, "Camera mapping",
                "Could not solve the camera↔beamer mapping (the three points may "
                f"be in a line). It will be left uncalibrated.\n\n{exc}",
            )

    # ── Finish / teardown ─────────────────────────────────────────────────────

    def _summary_text(self):
        ppc = self._px_per_cm or 0.0
        proj_cm = (self._proj_diam_px / ppc) if ppc else 0.0
        mapped = "yes" if self._cam_to_beamer else "no"
        return (
            "Calibration summary:\n\n"
            f"• Scale: {ppc:.2f} px/cm\n"
            f"• Centre: ({self._origin_px[0]:.0f}, {self._origin_px[1]:.0f}) px\n"
            f"• Axis signs: x={self._x_sign:+d}, y={self._y_sign:+d}\n"
            f"• Projection area: {self._proj_diam_px} px diameter"
            + (f"  ≈ {proj_cm:.1f} cm\n" if ppc else "\n")
            + f"• Camera→beamer mapping: {mapped}\n"
            + "\nPress Finish to save."
        )

    def _finish(self):
        data = {
            "screen_index":         int(getattr(shared_states, "beamer_screen_index", 1)),
            "screen_size_px":       [self._screen_w, self._screen_h],
            "lens_distance_cm":     getattr(shared_states, "beamer_lens_distance_cm", 196),
            "px_per_cm":            self._px_per_cm,
            "origin_px":            [self._origin_px[0], self._origin_px[1]],
            "x_sign":               self._x_sign,
            "y_sign":               self._y_sign,
            "projection_radius_px": self._proj_diam_px / 2.0,
            "camera_to_beamer":     self._cam_to_beamer,
            "measurements": [
                {"diameter_px": d, "measured_cm": s.value()}
                for d, s in zip(self._diam_presets, self._diam_inputs)
            ],
        }
        try:
            path = shared_states.beamer_calibration_path
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                json.dump(data, fh, indent=2)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Save failed", f"Could not write calibration:\n{exc}"
            )
            return
        self._send({"cmd": "reload_calibration"})
        self._send({"cmd": "clear"})
        self.accept()

    def reject(self):
        # Blank the projection if the user cancels partway through.
        self._feed_timer.stop()
        self._send({"cmd": "clear"})
        super().reject()

    def closeEvent(self, event):
        self._feed_timer.stop()
        self._send({"cmd": "clear"})
        super().closeEvent(event)
