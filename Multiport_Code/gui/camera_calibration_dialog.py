"""camera_calibration_dialog.py — measure the tracking camera's fisheye distortion.

No printed target: the beamer projects the calibration pattern. It lights one 2 cm
dot at a time inside the arena, the wizard finds that dot in the live camera feed, and
the resulting correspondences (known point on the floor ↔ where the lens put it) are
what the distortion fit is solved from. That works here because the projection disc is
*larger* than the camera's view of it, so dots can be placed right into the corners of
the frame — the region where fisheye distortion is largest and a calibration that only
covered the middle would be extrapolating.

What it inherits from that choice: the projector's own (much smaller) lens distortion
and any unevenness of the arena floor are folded into the estimate. The measured point
pairs are saved with the result, so a fit can be audited or redone later without
putting the rig back into this state.

The wizard needs *raw* frames to measure, so it lowers the camera process's
undistort_enabled flag for the duration and raises undistort_reload when it saves.
"""

import numpy as np
from PyQt5 import QtWidgets, QtGui, QtCore

import cv2
import shared_states
import camera_calibration as cc
from beamer_controls import BeamerCalibration


def _detect_dot(diff, min_peak=25):
    """Sub-pixel centre of the brightest blob in *diff*, or None if none stands out.

    *diff* is the lit frame minus the dark reference, so the projected dot is the only
    thing left except sensor noise and anything in the arena that changed. The centre
    is an intensity-weighted centroid over the blob rather than the peak pixel: the
    peak is quantised to whole pixels, the centroid is good to a fraction of one, and
    that fraction is the accuracy the whole calibration inherits.
    """
    blur = cv2.GaussianBlur(diff, (5, 5), 0)
    _mn, mx, _mnloc, mxloc = cv2.minMaxLoc(blur)
    if mx < min_peak:
        return None, mx
    mask = (blur >= 0.5 * mx).astype(np.uint8)
    n, labels, stats, _cent = cv2.connectedComponentsWithStats(mask)
    label = labels[mxloc[1], mxloc[0]]
    if label == 0:
        return None, mx
    x, y, w, h, area = stats[label]
    if area < 4:
        return None, mx
    patch = blur[y:y + h, x:x + w].astype(np.float64)
    patch[labels[y:y + h, x:x + w] != label] = 0.0
    total = patch.sum()
    if total <= 0:
        return None, mx
    ys, xs = np.mgrid[0:h, 0:w]
    return (x + float((patch * xs).sum() / total),
            y + float((patch * ys).sum() / total)), mx


class _Feed(QtWidgets.QWidget):
    """Live camera view with the dots found so far drawn on top."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 300)
        self._pixmap = None
        self._found = []      # preview-pixel coordinates
        self._current = None  # the dot being measured right now

    def set_frame(self, frame):
        if frame is not None:
            h, w = frame.shape[:2]
            qimg = QtGui.QImage(frame.tobytes(), w, h, w,
                                QtGui.QImage.Format_Grayscale8)
            self._pixmap = QtGui.QPixmap.fromImage(qimg)
        self.update()

    def set_points(self, found, current=None):
        self._found = list(found)
        self._current = current
        self.update()

    def _frame_rect(self):
        if self._pixmap is None or self._pixmap.width() == 0:
            side = min(self.width(), self.height())
            return ((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale = min(self.width() / pw, self.height() / ph)
        return ((self.width() - pw * scale) / 2, (self.height() - ph * scale) / 2,
                pw * scale, ph * scale)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#111"))
        ox, oy, w, h = self._frame_rect()
        if self._pixmap is not None:
            painter.drawPixmap(int(ox), int(oy),
                               self._pixmap.scaled(int(w), int(h),
                                                   QtCore.Qt.IgnoreAspectRatio,
                                                   QtCore.Qt.SmoothTransformation))
        else:
            painter.setPen(QtGui.QColor("#aaa"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "Waiting for camera…")

        if self._pixmap is not None and self._pixmap.width():
            sx = w / self._pixmap.width()
            sy = h / self._pixmap.height()
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.setPen(QtGui.QPen(QtGui.QColor(60, 200, 100), 1))
            for (px, py) in self._found:
                painter.drawEllipse(QtCore.QPointF(ox + px * sx, oy + py * sy), 4, 4)
            if self._current is not None:
                painter.setPen(QtGui.QPen(QtGui.QColor(240, 200, 60), 2))
                x, y = ox + self._current[0] * sx, oy + self._current[1] * sy
                painter.drawEllipse(QtCore.QPointF(x, y), 9, 9)
        painter.end()


class CameraCalibrationDialog(QtWidgets.QDialog):
    """Stepped wizard: project a dot grid → fit the lens → save.

      1. Intro / preflight — the beamer calibration this depends on must exist.
      2. Capture — one dot at a time, with the live feed and a progress bar.
      3. Result — model, RMS, before/after preview, suggested zoom → Finish saves.
    """

    # A projected dot has to travel beamer → arena → sensor → frame_queue → the GUI's
    # 50 ms preview timer before we may look at it. 350 ms clears all of that with
    # room for the 50 ms exposure and the ~10 fps the camera actually runs at.
    _SETTLE_MS = 350
    _SAMPLE_MS = 120
    _SAMPLES = 3          # frames averaged per dot, to beat sensor noise
    _DOT_CM = 2.0
    _MARGIN = 0.07        # keep dots this far (in frame fractions) off the edge

    def __init__(self, beamer_queue, frame_provider=None,
                 undistort_enabled=None, undistort_reload=None, parent=None):
        super().__init__(parent)
        self.beamer_queue = beamer_queue
        self.frame_provider = frame_provider
        self.undistort_enabled = undistort_enabled
        self.undistort_reload = undistort_reload
        self.setWindowTitle("Camera Lens Calibration")
        self.setModal(True)
        self.setMinimumSize(560, 620)

        self._calib = cc.CameraCalibration()
        self._beamer = BeamerCalibration()
        self._crop = tuple(shared_states.DLC_CROP)

        # Capture state
        self._targets = []       # beamer-pixel points still to measure
        self._idx = -1
        self._dark = None
        self._samples = []
        self._pairs = []         # (beamer_px, preview_px) for every detected dot
        self._last_raw = None    # last raw frame, for the before/after preview
        self._fit = None
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._feed_timer = QtCore.QTimer(self)
        self._feed_timer.timeout.connect(self._update_feed)

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

    # ── Beamer helper ─────────────────────────────────────────────────────────

    def _send(self, cmd):
        if self.beamer_queue is None:
            return
        try:
            self.beamer_queue.put_nowait(cmd)
        except Exception:
            pass

    # ── Pages ─────────────────────────────────────────────────────────────────

    def _build_pages(self):
        # 1 ── intro / preflight
        intro = QtWidgets.QWidget()
        ilay = QtWidgets.QVBoxLayout(intro)
        text = QtWidgets.QLabel(
            "This measures the fisheye distortion of the tracking camera.\n\n"
            "The beamer projects one dot at a time across the arena and the wizard "
            "finds each one in the camera image — no printed target needed. Keep the "
            "arena empty and the room lights as they are during an experiment.\n\n"
            "Lens correction is switched off while measuring and switched back on "
            "when you finish. Takes about a minute."
        )
        text.setWordWrap(True)
        ilay.addWidget(text)

        self._preflight = QtWidgets.QLabel()
        self._preflight.setWordWrap(True)
        ilay.addWidget(self._preflight)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Grid:"))
        self._grid_spin = QtWidgets.QSpinBox()
        self._grid_spin.setRange(4, 11)
        self._grid_spin.setValue(7)
        self._grid_spin.setSuffix(" × n dots")
        row.addWidget(self._grid_spin)
        row.addStretch()
        ilay.addLayout(row)
        ilay.addStretch()
        self.stack.addWidget(intro)

        # 2 ── capture
        cap = QtWidgets.QWidget()
        clay = QtWidgets.QVBoxLayout(cap)
        self._cap_label = QtWidgets.QLabel("Press Start to begin.")
        self._cap_label.setWordWrap(True)
        clay.addWidget(self._cap_label)
        self._feed = _Feed()
        clay.addWidget(self._feed, 1)
        self._progress = QtWidgets.QProgressBar()
        clay.addWidget(self._progress)
        brow = QtWidgets.QHBoxLayout()
        self._start_btn = QtWidgets.QPushButton("Start")
        self._start_btn.clicked.connect(self._start_capture)
        self._abort_btn = QtWidgets.QPushButton("Stop")
        self._abort_btn.clicked.connect(self._stop_capture)
        self._abort_btn.setEnabled(False)
        brow.addWidget(self._start_btn)
        brow.addWidget(self._abort_btn)
        brow.addStretch()
        clay.addLayout(brow)
        self.stack.addWidget(cap)

        # 3 ── result
        res = QtWidgets.QWidget()
        rlay = QtWidgets.QVBoxLayout(res)
        self._result_label = QtWidgets.QLabel()
        self._result_label.setWordWrap(True)
        rlay.addWidget(self._result_label)
        prow = QtWidgets.QHBoxLayout()
        self._before = _Feed()
        self._after = _Feed()
        for lbl, widget in (("Raw", self._before), ("Corrected", self._after)):
            col = QtWidgets.QVBoxLayout()
            cap_lbl = QtWidgets.QLabel(lbl)
            cap_lbl.setAlignment(QtCore.Qt.AlignCenter)
            col.addWidget(cap_lbl)
            col.addWidget(widget, 1)
            prow.addLayout(col)
        rlay.addLayout(prow, 1)
        self.stack.addWidget(res)

    def _goto(self, page):
        self.stack.setCurrentIndex(page)
        self.back_btn.setEnabled(page > 0)
        if page == 0:
            self._run_preflight()
            self.next_btn.setText("Next")
            self.next_btn.setEnabled(True)
        elif page == 1:
            self.next_btn.setText("Next")
            self.next_btn.setEnabled(bool(self._pairs))
            self._feed_timer.start(100)
        else:
            self._feed_timer.stop()
            self.next_btn.setText("Finish")
            self.next_btn.setEnabled(False)
            self._result_label.setText("Fitting…")
            QtCore.QTimer.singleShot(50, self._run_fit)

    def _back(self):
        if self.stack.currentIndex() > 0:
            self._stop_capture()
            self._goto(self.stack.currentIndex() - 1)

    def _next(self):
        page = self.stack.currentIndex()
        if page == 0:
            self._goto(1)
        elif page == 1:
            self._stop_capture()
            self._goto(2)
        else:
            self._finish()

    # ── Preflight ─────────────────────────────────────────────────────────────

    def _run_preflight(self):
        """Everything this wizard depends on, checked before it wastes a minute."""
        problems, notes = [], []
        if self.beamer_queue is None:
            problems.append("the beamer process is not connected")
        if self.frame_provider is None:
            problems.append("there is no camera feed")
        if not self._beamer.cam_to_beamer:
            # Without it we cannot tell which beamer pixels the camera can see, so
            # the grid would be placed blind — usable, but likely to miss the corners
            # that matter most.
            notes.append("no camera↔beamer mapping yet: the grid will be placed over "
                         "the projection area instead of the camera's view. Run the "
                         "beamer calibration first for a better spread.")
        if self._calib.is_calibrated:
            notes.append(f"replacing the existing calibration ({self._calib.describe()})")

        if problems:
            self._preflight.setText("Cannot calibrate: " + "; ".join(problems) + ".")
            self._preflight.setStyleSheet("color:#e06060;")
            self.next_btn.setEnabled(False)
        else:
            self._preflight.setText("\n".join(f"• {n}" for n in notes))
            self._preflight.setStyleSheet("color:#d0a040;")

    # ── Grid ──────────────────────────────────────────────────────────────────

    def _grid_points(self):
        """Beamer-pixel dot positions that land inside the camera's field of view.

        Placed on a grid in *camera* coordinates and mapped back to the beamer, not
        the other way round: the point of this pattern is to constrain distortion
        everywhere in the frame, and only the camera knows where its own edges are.

        The frame's corners sit outside the projector's round usable area, so those
        dots are pulled back to its rim rather than dropped. A dot near the rim still
        constrains the periphery — where fisheye distortion is largest and a fit made
        only of central points would be extrapolating — and its arena position is
        known just as exactly. Duplicates from several corners landing on the same bit
        of rim are collapsed.
        """
        n = int(self._grid_spin.value())
        m = self._MARGIN
        fracs = np.linspace(m, 1.0 - m, n)
        radius = self._beamer.projection_radius_px
        ox, oy = self._beamer.origin_px
        pts, seen = [], set()
        for v in fracs:
            for u in fracs:
                if self._beamer.cam_to_beamer:
                    bx, by = self._beamer.dlc_to_px(float(u), float(v))
                else:
                    # No mapping: fall back to a grid inscribed in the projection disc.
                    r = (radius or 400) * 0.95
                    bx = ox + (2 * u - 1) * r / np.sqrt(2)
                    by = oy + (2 * v - 1) * r / np.sqrt(2)
                if radius:
                    dx, dy = bx - ox, by - oy
                    dist = (dx * dx + dy * dy) ** 0.5
                    if dist > radius * 0.98:
                        scale = (radius * 0.98) / max(dist, 1e-6)
                        bx, by = ox + dx * scale, oy + dy * scale
                key = (round(bx / 8.0), round(by / 8.0))
                if key in seen:
                    continue
                seen.add(key)
                pts.append((float(bx), float(by)))
        return pts

    def _beamer_to_cm(self, bx, by):
        """Beamer pixels → arena centimetres, using the beamer calibration's scale."""
        ppc = self._beamer.px_per_cm or 1.0
        ox, oy = self._beamer.origin_px
        return ((bx - ox) / (ppc * self._beamer.x_sign),
                (by - oy) / (ppc * self._beamer.y_sign))

    # ── Capture ───────────────────────────────────────────────────────────────

    def _start_capture(self):
        self._targets = self._grid_points()
        if not self._targets:
            self._cap_label.setText("No usable dot positions — check the beamer "
                                    "calibration's projection area.")
            return
        self._pairs = []
        self._dark = None
        self._samples = []
        self._idx = -1                      # -1 = dark reference
        self._misses = 0
        # Measure on raw frames: the whole point is to see what the lens does.
        if self.undistort_enabled is not None:
            self.undistort_enabled.value = False
        self._start_btn.setEnabled(False)
        self._abort_btn.setEnabled(True)
        self.next_btn.setEnabled(False)
        self._progress.setRange(0, len(self._targets))
        self._progress.setValue(0)
        self._feed_timer.start(100)
        self._send({"cmd": "clear"})
        self._cap_label.setText("Measuring the dark reference…")
        # Extra settle: the camera must also flush the frames it grabbed while
        # correction was still on, or the reference would be a corrected frame.
        self._timer.start(3 * self._SETTLE_MS)

    def _stop_capture(self):
        self._timer.stop()
        self._feed_timer.stop()
        self._send({"cmd": "clear"})
        self._start_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self.next_btn.setEnabled(bool(self._pairs))

    def _frame(self):
        if self.frame_provider is None:
            return None
        frame = self.frame_provider.latest_frame()
        return None if frame is None else np.asarray(frame)

    def _update_feed(self):
        frame = self._frame()
        if frame is not None:
            self._feed.set_frame(frame)

    def _tick(self):
        """One step of the capture sequence: settle → sample → measure → next dot."""
        frame = self._frame()
        if frame is None:
            self._cap_label.setText("Waiting for the camera…")
            self._timer.start(self._SETTLE_MS)
            return

        self._samples.append(frame.astype(np.float32))
        if len(self._samples) < self._SAMPLES:
            self._timer.start(self._SAMPLE_MS)
            return

        mean = np.mean(self._samples, axis=0)
        self._samples = []

        if self._idx < 0:                       # that was the dark reference
            self._dark = mean
            self._last_raw = frame.copy()
        else:
            diff = np.clip(mean - self._dark, 0, 255).astype(np.uint8)
            found, peak = _detect_dot(diff)
            if found is None:
                self._misses += 1
            else:
                self._pairs.append((self._targets[self._idx], found))
                self._feed.set_points([p[1] for p in self._pairs], found)

        self._idx += 1
        if self._idx >= len(self._targets):
            self._send({"cmd": "clear"})
            self._cap_label.setText(
                f"Captured {len(self._pairs)} of {len(self._targets)} dots"
                + (f" ({self._misses} not found)." if self._misses else ".")
                + ("  Press Next." if len(self._pairs) >= 8 else
                   "  Too few to fit — check that the projection is visible."))
            self._stop_capture()
            return

        bx, by = self._targets[self._idx]
        diam = max(6.0, self._DOT_CM * (self._beamer.px_per_cm or 12.0))
        self._send({"cmd": "sphere_px", "cx": bx, "cy": by,
                    "diameter_px": diam, "shadow": False})
        self._progress.setValue(self._idx)
        self._cap_label.setText(
            f"Dot {self._idx + 1} / {len(self._targets)} — found {len(self._pairs)}")
        self._timer.start(self._SETTLE_MS)

    # ── Fit ───────────────────────────────────────────────────────────────────

    def _run_fit(self):
        if len(self._pairs) < 8:
            self._result_label.setText(
                f"Only {len(self._pairs)} dots were found — at least 8 are needed. "
                "Go back and check that the projection is visible in the camera.")
            return
        beamer_px = np.array([p[0] for p in self._pairs], dtype=np.float64)
        preview_px = np.array([p[1] for p in self._pairs], dtype=np.float64)
        obj_cm = np.array([self._beamer_to_cm(bx, by) for bx, by in beamer_px])
        shape = self._last_raw.shape if self._last_raw is not None else (500, 500)
        img_px = cc.preview_to_sensor(preview_px, self._crop, shape)

        self._fit = cc.fit_planar(
            obj_cm, img_px,
            (int(shared_states.IMG_WIDTH), int(shared_states.IMG_HEIGHT)))
        if self._fit is None:
            self._result_label.setText(
                "The fit did not converge. Re-run the capture with a larger grid, "
                "and check the feed for reflections being measured instead of dots.")
            return

        # Straightness, stated the way it matters: how far the measured dots sit from
        # the best flat-plane fit, before and after. That is exactly the error the
        # beamer's affine mapping and the ITI dwell test absorb today.
        before = _plane_residual(img_px, obj_cm)
        after = _plane_residual(self._fit_undistort(img_px), obj_cm)
        zoom = self._preview_calib().fit_zoom(self._crop)

        rms = self._fit["rms_px"]
        warn = ("\n\n⚠ The residual is high — check that no dot was mistaken for a "
                "reflection, and that the arena floor is flat."
                if rms > 1.5 else "")
        self._result_label.setText(
            f"Model: {self._fit['model']}   ·   {len(self._pairs)} dots   ·   "
            f"RMS {rms:.2f} px\n"
            f"Arena flatness: {before:.1f} px before → {after:.2f} px after "
            f"({before / max(after, 1e-6):.0f}× straighter)\n"
            f"Focal length {self._fit['focal']:.0f} px"
            + (f"   ·   rejected: {self._fit['rejected']}" if self._fit.get("rejected")
               else "")
            + f"\n\nCorrecting the lens pushes the edges of the view outward, so at "
              f"the default zoom of 1.0 the outermost {100 - 100 * zoom:.0f}% of the "
              f"frame falls outside it. Set shared_states.undistort_zoom = {zoom:.2f} "
              f"to keep the whole arena in view."
            + warn)

        self._show_preview()
        self.next_btn.setEnabled(True)

    def _preview_calib(self):
        """A CameraCalibration carrying this fit, without touching the saved file."""
        calib = cc.CameraCalibration.__new__(cc.CameraCalibration)
        calib.path = self._calib.path
        calib._maps = {}
        calib._mtime = None
        calib.model, calib.K, calib.D = (self._fit["model"], self._fit["K"],
                                         self._fit["D"])
        calib.image_size = self._fit["image_size"]
        calib.rms_px, calib.method, calib.captured = self._fit["rms_px"], "wizard", None
        return calib

    def _fit_undistort(self, img_px):
        crop_px = img_px - np.array([self._crop[2], self._crop[0]], dtype=np.float64)
        return self._preview_calib().undistort_points(crop_px, self._crop)

    def _show_preview(self):
        if self._last_raw is None:
            return
        self._before.set_frame(self._last_raw)
        maps = self._preview_calib().preview_maps(self._crop, self._last_raw.shape)
        if maps is not None:
            self._after.set_frame(cv2.remap(self._last_raw, maps[0], maps[1],
                                            cv2.INTER_LINEAR,
                                            borderMode=cv2.BORDER_CONSTANT))

    # ── Finish / teardown ─────────────────────────────────────────────────────

    def _finish(self):
        raw = {
            "beamer_px":   [list(map(float, p[0])) for p in self._pairs],
            "preview_px":  [list(map(float, p[1])) for p in self._pairs],
            "preview_shape": list(self._last_raw.shape[:2]) if self._last_raw is not None
                             else None,
            "crop":        list(self._crop),
            "px_per_cm":   self._beamer.px_per_cm,
            "origin_px":   list(self._beamer.origin_px),
            "x_sign":      self._beamer.x_sign,
            "y_sign":      self._beamer.y_sign,
        }
        try:
            self._calib.save(self._fit, method="beamer_grid", raw=raw, crop=self._crop)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save failed",
                                           f"Could not write the calibration:\n{exc}")
            return
        if self.undistort_reload is not None:
            self.undistort_reload.value = True
        QtWidgets.QMessageBox.information(
            self, "Camera calibrated",
            "Saved. The camera process is picking it up now.\n\n"
            "Re-run the beamer calibration next: its camera↔beamer mapping was "
            "measured in the old, distorted image and no longer matches what the "
            "camera produces.")
        self.accept()

    def _release(self):
        """Always hand the camera back with correction on, however we leave."""
        self._timer.stop()
        self._feed_timer.stop()
        self._send({"cmd": "clear"})
        if self.undistort_enabled is not None:
            self.undistort_enabled.value = True

    def accept(self):
        self._release()
        super().accept()

    def reject(self):
        self._release()
        super().reject()

    def closeEvent(self, event):
        self._release()
        super().closeEvent(event)


def _plane_residual(pts, obj_cm):
    """RMS distance from the best plane→image homography, in pixels.

    Zero for a pinhole camera looking at a flat floor, whatever the angle. What is
    left is lens distortion, so this is the one number that says whether the
    correction worked, independent of the fit's own error estimate.
    """
    pts = np.asarray(pts, dtype=np.float32).reshape(-1, 2)
    obj = np.asarray(obj_cm, dtype=np.float32).reshape(-1, 2)
    H, _ = cv2.findHomography(obj, pts, 0)
    if H is None:
        return float("nan")
    proj = cv2.perspectiveTransform(obj.reshape(-1, 1, 2), H).reshape(-1, 2)
    return float(np.sqrt(np.mean(np.sum((proj - pts) ** 2, axis=1))))
