import time
from collections import deque
from PyQt5 import QtWidgets, QtGui, QtCore


class CleaningPage(QtWidgets.QWidget):
    """Cleaning / testing panel.

    Section order (top → bottom):
      1. Beamer        — placeholder, hardware not yet connected
      2. Screens       — placeholder, hardware not yet connected
      3. ALL OFF safety button  ← sits directly above LED controls
      4. LEDs          — manual toggle controls
      5. Pumps         — manual pulse controls
      6. BNC           — manual pulse controls
      7. Automated Cleaning Cycle
    """

    _BTN_SIZE = 44   # px, square grid buttons

    def __init__(self, command_queue):
        super().__init__()
        # WA_OpaquePaintEvent: Qt skips its background pre-fill before paintEvent.
        # Our paintEvent then fills every pixel, so the widget is never uninitialized.
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.command_queue = command_queue
        self.led_state = {i: False for i in range(1, 17)}

        # Cleaning-cycle state
        self._cleaning_active = False
        self._pump_queue: deque = deque()
        self._active_pumps: dict = {}   # {pump_id: expected_end_time}
        self._cleaning_timer = QtCore.QTimer(self)
        self._cleaning_timer.timeout.connect(self._cleaning_tick)

        # Root layout fills the entire widget — no scroll area
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(10, 10, 10, 10)

        # ── 1. Beamer (placeholder) ───────────────────────────────
        root.addWidget(self._section_label("Beamer"))

        beamer_status_row = QtWidgets.QHBoxLayout()
        beamer_dot = QtWidgets.QLabel("●")
        beamer_dot.setStyleSheet("color:#666; font-size:14px;")
        beamer_status_row.addWidget(beamer_dot)
        beamer_status_row.addWidget(QtWidgets.QLabel("Not connected"))
        beamer_status_row.addStretch()
        beamer_connect = QtWidgets.QPushButton("Connect")
        beamer_connect.setEnabled(False)
        beamer_status_row.addWidget(beamer_connect)
        root.addLayout(beamer_status_row)

        beamer_ctrl_row = QtWidgets.QHBoxLayout()
        beamer_ctrl_row.addWidget(QtWidgets.QLabel("Pattern:"))
        beamer_pattern = QtWidgets.QComboBox()
        beamer_pattern.addItems(["Solid white", "Checkerboard", "Gradient", "Dark"])
        beamer_pattern.setEnabled(False)
        beamer_ctrl_row.addWidget(beamer_pattern)
        beamer_test = QtWidgets.QPushButton("Test Pattern")
        beamer_test.setEnabled(False)
        beamer_ctrl_row.addWidget(beamer_test)
        root.addLayout(beamer_ctrl_row)
        root.addWidget(self._separator())

        # ── 3. Screens (placeholder) ──────────────────────────────
        root.addWidget(self._section_label("Screens"))

        screens_status_row = QtWidgets.QHBoxLayout()
        screens_dot = QtWidgets.QLabel("●")
        screens_dot.setStyleSheet("color:#666; font-size:14px;")
        screens_status_row.addWidget(screens_dot)
        screens_status_row.addWidget(QtWidgets.QLabel("Not connected  (2 screens)"))
        screens_status_row.addStretch()
        screens_connect = QtWidgets.QPushButton("Connect")
        screens_connect.setEnabled(False)
        screens_status_row.addWidget(screens_connect)
        root.addLayout(screens_status_row)

        for screen_num in range(1, 3):
            screen_row = QtWidgets.QHBoxLayout()
            screen_row.addWidget(QtWidgets.QLabel(f"Screen {screen_num}:"))
            screen_pattern = QtWidgets.QComboBox()
            screen_pattern.addItems(
                ["Blank", "Grating 45°", "Grating 90°", "Noise", "Full flash"]
            )
            screen_pattern.setEnabled(False)
            screen_row.addWidget(screen_pattern)
            screen_test = QtWidgets.QPushButton("Test")
            screen_test.setEnabled(False)
            screen_row.addWidget(screen_test)
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

        # ── 5. Pumps — manual pulse ───────────────────────────────
        pump_header = QtWidgets.QHBoxLayout()
        pump_header.addWidget(self._section_label("Pumps — Manual Pulse"))
        pump_header.addStretch()
        pump_header.addWidget(QtWidgets.QLabel("Duration (ms):"))
        self.pump_duration = QtWidgets.QSpinBox()
        self.pump_duration.setRange(1, 60000)
        self.pump_duration.setValue(100)
        self.pump_duration.setFixedWidth(80)
        pump_header.addWidget(self.pump_duration)
        root.addLayout(pump_header)

        pump_grid = self._make_grid()
        for i in range(1, 17):
            btn = QtWidgets.QPushButton(str(i))
            btn.setFixedSize(self._BTN_SIZE, self._BTN_SIZE)
            btn.clicked.connect(lambda _, n=i: self._pulse_pump(n))
            pump_grid.addWidget(btn, (i - 1) // 8, (i - 1) % 8)
        root.addLayout(pump_grid)
        root.addWidget(self._separator())

        # ── 6. BNC ────────────────────────────────────────────────
        bnc_header = QtWidgets.QHBoxLayout()
        bnc_header.addWidget(self._section_label("BNC"))
        bnc_header.addStretch()
        bnc_header.addWidget(QtWidgets.QLabel("Duration (ms):"))
        self.bnc_duration = QtWidgets.QSpinBox()
        self.bnc_duration.setRange(1, 60000)
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

    # ── Manual control actions ────────────────────────────────────

    def _toggle_led(self, led_id: int, on: bool):
        self.led_state[led_id] = on
        self._send(f"LED:{led_id}:{'ON' if on else 'OFF'}")
        self._led_buttons[led_id].setStyleSheet(
            "QPushButton { background:#007acc; border-radius:4px; }" if on else ""
        )

    def _pulse_pump(self, pump_id: int):
        self._send(f"MOS:{pump_id}:ON:{self.pump_duration.value()}")

    def _pulse_bnc(self, bnc_id: int):
        self._send(f"BNC:{bnc_id}:PULSE:{self.bnc_duration.value()}")

    def _all_off(self):
        """Immediately turn off all LEDs and all pumps."""
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
