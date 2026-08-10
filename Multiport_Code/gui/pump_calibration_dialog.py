"""Wizard to measure how much each pump ejects per pulse.

Rewards are configured in µL, but a pump only understands "on for 10 ms".
This connects the two: fire a known number of pulses into a tube, weigh it,
and net mass divided by pulse count gives that pump's µL/pulse. Repeated
3-5x for a spread worth trusting.

One port at a time, deliberately — the common case is one drifted pump, not
calibrating a fresh rig, so there's no need for sixteen tubes at once.

Two things easy to get wrong:
  - The wizard fires at exactly the pulse width/interval a session uses
    (shared_states.pump_pulse_ms / pump_refractory_ms) — a calibration
    measured at a different cadence doesn't transfer, since a 10 ms
    energisation is mostly the pump's startup transient.
  - The mass entered must be net (liquid only), which is why empty/full
    weights are entered separately rather than trusting the difference to be
    remembered.

Modelled on BeamerCalibrationDialog in cleaning_page.py (same dark styling,
same "write JSON then tell the live process" ending), but needs no camera,
so it's a plain form rather than a step-by-step wizard.
"""

import time

from PyQt5 import QtCore, QtGui, QtWidgets

import shared_states
from pump_calibration import PumpCalibration

# Above this, the pump is not repeatable enough for the mean to be worth much —
# usually air in the line or a tube that needs priming.
_CV_WARN_PCT = 15.0


class PumpCalibrationDialog(QtWidgets.QDialog):
    """Measure and store µL/pulse for one lickport pump at a time."""

    def __init__(self, command_queue, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pump calibration")
        self.setModal(True)
        self.setMinimumWidth(620)
        self.setStyleSheet(
            "QDialog { background:#2b2b2b; }"
            "QLabel { color:#ddd; }"
            "QTableWidget { background:#333; color:#ddd; gridline-color:#555; }"
            "QHeaderView::section { background:#3a3a3a; color:#aaa; border:0; padding:3px; }"
        )

        self.command_queue = command_queue
        self.calib = PumpCalibration()

        self._pulse_ms   = int(shared_states.pump_pulse_ms)
        self._interval_s = shared_states.pump_refractory_ms / 1000.0

        # Measurements for the port currently selected, cleared when it changes.
        self._measurements: list = []      # [{"n_pulses", "measured_mg"}]

        # Run state, with its own timer — the Cleaning page's cleaning-cycle
        # timer is stopped/cleared by _stop_cleaning, which would silently kill a run.
        self._run_timer = QtCore.QTimer(self)
        self._run_timer.timeout.connect(self._run_tick)
        self._run_remaining = 0
        self._run_total     = 0

        self._build_ui()
        self._reload_port()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)

        intro = QtWidgets.QLabel(
            f"Each run fires a fixed number of {self._pulse_ms} ms pulses at the same "
            f"{int(self._interval_s * 1000)} ms interval a session uses. Collect them "
            "in a tube, weigh it, and enter the empty and full masses below. Three to "
            "five runs per pump gives a spread worth trusting."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#aaa; font-size:10px;")
        root.addWidget(intro)

        # ── Port + run settings ───────────────────────────────────
        form = QtWidgets.QHBoxLayout()
        form.addWidget(QtWidgets.QLabel("Lickport:"))
        self._port_combo = QtWidgets.QComboBox()
        self._port_combo.addItems([str(p) for p in range(1, 17)])
        self._port_combo.setFixedWidth(70)
        self._port_combo.currentIndexChanged.connect(self._port_changed)
        form.addWidget(self._port_combo)

        form.addSpacing(16)
        form.addWidget(QtWidgets.QLabel("Pulses per run:"))
        self._pulses_spin = QtWidgets.QSpinBox()
        self._pulses_spin.setRange(10, 1000)
        self._pulses_spin.setValue(100)
        self._pulses_spin.setSingleStep(10)
        self._pulses_spin.setFixedWidth(80)
        self._pulses_spin.valueChanged.connect(self._update_run_estimate)
        form.addWidget(self._pulses_spin)

        form.addSpacing(16)
        form.addWidget(QtWidgets.QLabel("Density:"))
        self._density_spin = QtWidgets.QDoubleSpinBox()
        self._density_spin.setRange(0.5, 2.0)
        self._density_spin.setDecimals(3)
        self._density_spin.setSingleStep(0.005)
        self._density_spin.setValue(self.calib.density_mg_per_ul or 1.0)
        self._density_spin.setSuffix(" mg/µL")
        self._density_spin.setFixedWidth(110)
        self._density_spin.setToolTip(
            "1.000 for water. Raise it for a sucrose solution — the wizard weighs "
            "liquid, the protocol asks for volume, and this is what converts one "
            "into the other.")
        self._density_spin.valueChanged.connect(lambda _v: self._refresh_table())
        form.addWidget(self._density_spin)
        form.addStretch()
        root.addLayout(form)

        self._estimate_lbl = QtWidgets.QLabel()
        self._estimate_lbl.setStyleSheet("color:#888; font-size:10px;")
        root.addWidget(self._estimate_lbl)

        # ── Run ───────────────────────────────────────────────────
        run_row = QtWidgets.QHBoxLayout()
        self._run_btn = QtWidgets.QPushButton("RUN")
        self._run_btn.setFixedHeight(32)
        self._run_btn.setStyleSheet(
            "QPushButton { background:#1a6b1a; color:#fff; border-radius:4px; font-weight:bold; }"
            "QPushButton:hover { background:#247a24; }"
            "QPushButton:disabled { background:#333; color:#666; }")
        self._run_btn.clicked.connect(self._start_run)
        run_row.addWidget(self._run_btn)

        self._abort_btn = QtWidgets.QPushButton("ABORT")
        self._abort_btn.setFixedHeight(32)
        self._abort_btn.setEnabled(False)
        self._abort_btn.setStyleSheet(
            "QPushButton { background:#8b0000; color:#fff; border-radius:4px; font-weight:bold; }"
            "QPushButton:hover { background:#b00000; }"
            "QPushButton:disabled { background:#333; color:#666; }")
        self._abort_btn.clicked.connect(self._abort_run)
        run_row.addWidget(self._abort_btn)
        root.addLayout(run_row)

        self._progress = QtWidgets.QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("Idle")
        root.addWidget(self._progress)

        # ── Weigh-in ──────────────────────────────────────────────
        weigh = QtWidgets.QHBoxLayout()
        weigh.addWidget(QtWidgets.QLabel("Empty:"))
        self._empty_spin = self._mass_spin()
        weigh.addWidget(self._empty_spin)
        weigh.addWidget(QtWidgets.QLabel("Full:"))
        self._full_spin = self._mass_spin()
        weigh.addWidget(self._full_spin)

        self._add_btn = QtWidgets.QPushButton("Add measurement")
        self._add_btn.setFixedHeight(28)
        self._add_btn.clicked.connect(self._add_measurement)
        weigh.addWidget(self._add_btn)
        weigh.addStretch()
        root.addLayout(weigh)

        # ── Measurements ──────────────────────────────────────────
        self._table = QtWidgets.QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Run", "Pulses", "Net mass (mg)", "µL/pulse"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setFixedHeight(150)
        root.addWidget(self._table)

        self._summary_lbl = QtWidgets.QLabel()
        self._summary_lbl.setWordWrap(True)
        root.addWidget(self._summary_lbl)

        # ── Actions ───────────────────────────────────────────────
        actions = QtWidgets.QHBoxLayout()
        self._drop_btn = QtWidgets.QPushButton("Remove selected")
        self._drop_btn.clicked.connect(self._remove_selected)
        actions.addWidget(self._drop_btn)
        actions.addStretch()

        self._save_btn = QtWidgets.QPushButton("Save this pump")
        self._save_btn.setFixedHeight(30)
        self._save_btn.setStyleSheet(
            "QPushButton { background:#2a5d8f; color:#fff; border-radius:4px; font-weight:bold; }"
            "QPushButton:hover { background:#356fa8; }"
            "QPushButton:disabled { background:#333; color:#666; }")
        self._save_btn.clicked.connect(self._save_port)
        actions.addWidget(self._save_btn)

        close_btn = QtWidgets.QPushButton("Close")
        close_btn.setFixedHeight(30)
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn)
        root.addLayout(actions)

        # ── Rig overview ──────────────────────────────────────────
        root.addWidget(self._separator())
        root.addWidget(self._section_label("All pumps"))
        self._overview_lbl = QtWidgets.QLabel()
        self._overview_lbl.setWordWrap(True)
        self._overview_lbl.setStyleSheet("color:#aaa; font-size:10px;")
        root.addWidget(self._overview_lbl)

        self._update_run_estimate()

    @staticmethod
    def _mass_spin() -> QtWidgets.QDoubleSpinBox:
        """A milligram field. Ranges to 100 g so a full tube on the pan still fits."""
        sb = QtWidgets.QDoubleSpinBox()
        sb.setRange(0.0, 100000.0)
        sb.setDecimals(1)
        sb.setSingleStep(1.0)
        sb.setSuffix(" mg")
        sb.setFixedWidth(120)
        return sb

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

    # ── Hardware ──────────────────────────────────────────────────────────────

    def _send(self, cmd: str):
        """Queue one command; a full queue means a lost pulse, so say so loudly.

        A silently dropped pulse would deliver fewer droplets than the count
        the volume gets divided by — an under-estimate of µL/pulse that
        nothing downstream could detect.
        """
        try:
            self.command_queue.put_nowait(cmd)
            return True
        except Exception:
            return False

    # ── Run ───────────────────────────────────────────────────────────────────

    def _port(self) -> int:
        return int(self._port_combo.currentText())

    def _update_run_estimate(self):
        n = self._pulses_spin.value()
        secs = n * self._interval_s
        self._estimate_lbl.setText(
            f"One run ≈ {secs:.0f} s ({n} × {self._pulse_ms} ms pulses). "
            f"Four runs on all sixteen pumps ≈ {16 * 4 * secs / 60:.0f} min.")

    def _start_run(self):
        if self._run_timer.isActive():
            return
        self._run_total = self._run_remaining = self._pulses_spin.value()
        self._progress.setRange(0, self._run_total)
        self._progress.setValue(0)
        self._set_running(True)
        # Fires the first pulse immediately, then one per interval — so N pulses
        # are spaced exactly as they would be during a session.
        self._run_tick()
        self._run_timer.start(int(self._interval_s * 1000))

    def _run_tick(self):
        if self._run_remaining <= 0:
            self._finish_run()
            return
        if not self._send(f"MOS:{self._port()}:ON:{self._pulse_ms}"):
            self._abort_run(dropped=True)
            return
        self._run_remaining -= 1
        done = self._run_total - self._run_remaining
        self._progress.setValue(done)
        self._progress.setFormat(f"Pulse {done} / {self._run_total}")

    def _finish_run(self):
        self._run_timer.stop()
        self._set_running(False)
        self._progress.setFormat(
            f"{self._run_total} pulses delivered — weigh the tube")
        self._full_spin.setFocus()

    def _abort_run(self, dropped: bool = False):
        """Stop mid-run. The pulses already fired are real, so the count is void."""
        self._run_timer.stop()
        done = self._run_total - self._run_remaining
        self._set_running(False)
        self._progress.setValue(0)
        self._progress.setFormat(f"Aborted after {done} pulse(s) — discard this tube")
        if dropped:
            QtWidgets.QMessageBox.warning(
                self, "Command queue full",
                "The hardware queue was full, so a pulse was lost and this run's "
                "pulse count is no longer known. Discard the tube and run again.")

    def _set_running(self, running: bool):
        self._run_btn.setEnabled(not running)
        self._abort_btn.setEnabled(running)
        for w in (self._port_combo, self._pulses_spin, self._add_btn,
                  self._save_btn, self._drop_btn):
            w.setEnabled(not running)

    # ── Measurements ──────────────────────────────────────────────────────────

    def _add_measurement(self):
        net = self._full_spin.value() - self._empty_spin.value()
        if net <= 0:
            QtWidgets.QMessageBox.warning(
                self, "Mass", "The full tube must weigh more than the empty one.")
            return
        self._measurements.append({"n_pulses": self._pulses_spin.value(),
                                   "measured_mg": net})
        # Collecting every run into the same tube is the usual way to do this, so
        # this run's full weight is the next run's empty weight.
        self._empty_spin.setValue(self._full_spin.value())
        self._refresh_table()

    def _remove_selected(self):
        rows = sorted({i.row() for i in self._table.selectedIndexes()}, reverse=True)
        for r in rows:
            if 0 <= r < len(self._measurements):
                del self._measurements[r]
        self._refresh_table()

    def _refresh_table(self):
        density = self._density_spin.value() or 1.0
        self._table.setRowCount(len(self._measurements))
        per_pulse = []
        for row, m in enumerate(self._measurements):
            ul = (m["measured_mg"] / density) / m["n_pulses"]
            per_pulse.append(ul)
            for col, text in enumerate([str(row + 1), str(m["n_pulses"]),
                                        f"{m['measured_mg']:.1f}", f"{ul:.4f}"]):
                self._table.setItem(row, col, QtWidgets.QTableWidgetItem(text))

        if not per_pulse:
            self._summary_lbl.setText("No measurements yet.")
            self._summary_lbl.setStyleSheet("color:#888;")
            self._save_btn.setEnabled(False)
            return

        mean = sum(per_pulse) / len(per_pulse)
        if len(per_pulse) > 1:
            sd = (sum((v - mean) ** 2 for v in per_pulse) / (len(per_pulse) - 1)) ** 0.5
        else:
            sd = 0.0
        cv = 100.0 * sd / mean if mean else 0.0

        text = (f"Port {self._port()}: <b>{mean:.4f} µL/pulse</b> "
                f"(spread ±{sd:.4f}, CV {cv:.1f}%, n={len(per_pulse)})")
        colour = "#ddd"
        if len(per_pulse) < 3:
            text += " — three runs or more gives a spread worth reading."
            colour = "#c8a000"
        elif cv > _CV_WARN_PCT:
            text += (f" — above {_CV_WARN_PCT:.0f}% the pump is not repeatable enough "
                     "to trust this mean. Prime the line and run again.")
            colour = "#c8a000"
        self._summary_lbl.setText(text)
        self._summary_lbl.setStyleSheet(f"color:{colour};")
        self._save_btn.setEnabled(True)

    # ── Port switching / saving ───────────────────────────────────────────────

    def _port_changed(self):
        if self._measurements:
            keep = QtWidgets.QMessageBox.question(
                self, "Unsaved measurements",
                "This pump has measurements that have not been saved. Discard them?",
                QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel)
            if keep != QtWidgets.QMessageBox.Discard:
                # Bounce back without re-entering this handler.
                self._port_combo.blockSignals(True)
                self._port_combo.setCurrentText(str(self._last_port))
                self._port_combo.blockSignals(False)
                return
        self._measurements = []
        self._reload_port()

    def _reload_port(self):
        """Show what is already stored for the selected port and reset the form."""
        self._last_port = self._port()
        self._empty_spin.setValue(0.0)
        self._full_spin.setValue(0.0)
        self._progress.setValue(0)
        self._progress.setFormat("Idle")
        self._refresh_table()
        self._refresh_overview()

    def _refresh_overview(self):
        self.calib.reload(force=True)
        parts = []
        for p in range(1, 17):
            entry = self.calib.stats(p)
            if entry is None:
                parts.append(f"<span style='color:#a05050;'>{p}: —</span>")
            else:
                parts.append(f"{p}: {entry['ul_per_pulse']:.3f}")
        done = sum(1 for p in range(1, 17) if self.calib.is_calibrated(p))
        self._overview_lbl.setText(
            f"{done} / 16 calibrated (µL/pulse)<br>" + " &nbsp;·&nbsp; ".join(parts))

    def _save_port(self):
        port = self._port()
        try:
            self.calib.save_port(port, self._measurements,
                                 self._density_spin.value())
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Save failed", f"Could not write the calibration:\n{exc}")
            return
        self._measurements = []
        self._reload_port()
        QtWidgets.QMessageBox.information(
            self, "Saved",
            f"Port {port} saved to\n{self.calib.path}\n\n"
            f"{self.calib.ul_per_pulse(port):.4f} µL per {self._pulse_ms} ms pulse.")

    # ── Teardown ──────────────────────────────────────────────────────────────

    def _stop_everything(self):
        """Stop the run and make sure no pump is left energised."""
        self._run_timer.stop()
        self._send(f"MOS:{self._port()}:OFF:0")

    def reject(self):
        self._stop_everything()
        super().reject()

    def accept(self):
        self._stop_everything()
        super().accept()

    def closeEvent(self, event):
        self._stop_everything()
        super().closeEvent(event)
