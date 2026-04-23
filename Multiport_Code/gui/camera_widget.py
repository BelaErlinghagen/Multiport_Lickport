from PyQt5 import QtWidgets, QtGui, QtCore


_LIKELIHOOD_THRESHOLD = 0.5   # minimum DLC likelihood to draw a keypoint
_KP_RADIUS            = 4     # keypoint circle radius in widget pixels

# One colour per keypoint (cycles if model has more keypoints than entries)
_KP_COLOURS = [
    QtGui.QColor(0,   255, 100),
    QtGui.QColor(255, 80,  80),
    QtGui.QColor(80,  180, 255),
    QtGui.QColor(255, 200, 0),
    QtGui.QColor(220, 0,   220),
    QtGui.QColor(0,   220, 220),
    QtGui.QColor(255, 130, 0),
    QtGui.QColor(180, 255, 80),
]


class CameraWidget(QtWidgets.QOpenGLWidget):
    """Live camera preview rendered via QOpenGLWidget.

    Inheriting from QOpenGLWidget (rather than QWidget) has one critical
    side-effect: Qt switches the *entire host window* into OpenGL compositing
    mode.  In that mode:
      1. All regular widgets in the window are painted into a shared off-screen
         framebuffer object (FBO).
      2. This widget's own FBO is composited on top.
      3. The combined result is presented to the screen in a single
         eglSwapBuffers call — atomic and vsync-synchronized.

    Under XWayland/Wayland (the user's GNOME setup) the xcb-based software
    path pushes dirty regions to the X server immediately and unsynchronised,
    which lets the Wayland compositor see intermediate window states and
    produce visible flicker.  The GL path eliminates this because nothing
    is shown until the swap is complete.

    When *pose_display_queue* is supplied (from the DLC process), each call to
    update_from_shared() also drains the latest pose and overlays keypoint dots
    in paintGL().
    """

    def __init__(self, frame_queue, shape, pose_display_queue=None):
        super().__init__()
        self._pixmap            = None
        self._last_pose         = None   # latest (num_kp, 3) pose array
        self._iti_overlay       = None   # dict or None; set by set_iti_overlay()
        self.frame_queue        = frame_queue
        self.pose_display_queue = pose_display_queue
        self.shape              = shape
        self.setMinimumSize(200, 150)

    def update_from_shared(self):
        """Drain the frame queue (and pose queue) and schedule a repaint."""
        frame = None
        while not self.frame_queue.empty():
            try:
                frame = self.frame_queue.get_nowait()
            except Exception:
                break

        if self.pose_display_queue is not None:
            while not self.pose_display_queue.empty():
                try:
                    self._last_pose = self.pose_display_queue.get_nowait()
                except Exception:
                    break

        if frame is None:
            if self.pose_display_queue is not None:
                self.update()   # still repaint so overlay redraws even without new frames
            return

        h, w = self.shape
        qimg = QtGui.QImage(
            frame.tobytes(), w, h, w, QtGui.QImage.Format_Grayscale8
        )
        self._pixmap = QtGui.QPixmap.fromImage(qimg)
        self.update()

    def set_iti_overlay(self, overlay):
        """Slot connected to ProtocolPage.overlay_changed. overlay is a dict or None."""
        self._iti_overlay = overlay
        self.update()

    def paintGL(self):
        """Paint the current frame using QPainter on the OpenGL surface.

        QPainter works on QOpenGLWidget via Qt's OpenGL 2D paint engine.
        painter.end() must be called explicitly before returning.
        """
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#111"))

        scaled_w = scaled_h = 0
        off_x = off_y = 0

        if self._pixmap:
            scaled = self._pixmap.scaled(
                self.width(), self.height(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            off_x    = (self.width()  - scaled.width())  // 2
            off_y    = (self.height() - scaled.height()) // 2
            scaled_w = scaled.width()
            scaled_h = scaled.height()
            painter.drawPixmap(off_x, off_y, scaled)
        else:
            painter.setPen(QtGui.QColor("#aaa"))
            painter.drawText(
                self.rect(), QtCore.Qt.AlignCenter, "Waiting for camera…"
            )

        # Resolve the original DLC image size once (used by both overlay blocks)
        if scaled_w > 0:
            try:
                from shared_states import IMG_HEIGHT
                orig_size = IMG_HEIGHT
            except Exception:
                orig_size = self.shape[0]
            scale = scaled_w / orig_size   # same for both axes (square image)
        else:
            orig_size = scale = 1

        # ── DLC keypoint overlay ──────────────────────────────────────────────
        if self._last_pose is not None and scaled_w > 0:
            # DLC coordinates are in the full-res square image space.
            # The display pixmap (cam_shape) is also square, scaled up to fill
            # the widget while preserving aspect ratio.
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            n_colours = len(_KP_COLOURS)
            for idx, kp in enumerate(self._last_pose):
                kx, ky, likelihood = float(kp[0]), float(kp[1]), float(kp[2])
                if likelihood < _LIKELIHOOD_THRESHOLD:
                    continue
                wx = int(kx * scale) + off_x
                wy = int(ky * scale) + off_y
                colour = _KP_COLOURS[idx % n_colours]
                painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
                painter.setBrush(QtGui.QBrush(colour))
                painter.drawEllipse(
                    wx - _KP_RADIUS, wy - _KP_RADIUS,
                    _KP_RADIUS * 2,  _KP_RADIUS * 2,
                )

        # ── ITI region overlay ────────────────────────────────────────────────
        if self._iti_overlay is not None and scaled_w > 0:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            iti_type = self._iti_overlay.get("type")

            if iti_type == "fixed_region":
                cx = self._iti_overlay["x"] * orig_size * scale + off_x
                cy = self._iti_overlay["y"] * orig_size * scale + off_y
                r  = self._iti_overlay["radius"] * orig_size * scale
                painter.setPen(QtGui.QPen(QtGui.QColor(0, 200, 255), 2))
                painter.setBrush(QtGui.QBrush(QtGui.QColor(0, 200, 255, 50)))
                painter.drawEllipse(QtCore.QRectF(cx - r, cy - r, r * 2, r * 2))

            elif iti_type == "random_region":
                mx = self._iti_overlay["margin_x"] * orig_size * scale + off_x
                my = self._iti_overlay["margin_y"] * orig_size * scale + off_y
                mr = self._iti_overlay["margin_radius"] * orig_size * scale
                # Dashed outline shows where the target circle center can fall
                painter.setPen(QtGui.QPen(QtGui.QColor(255, 180, 0), 2,
                                          QtCore.Qt.DashLine))
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawEllipse(QtCore.QRectF(mx - mr, my - mr, mr * 2, mr * 2))
                # Example target circle drawn at margin center
                r = self._iti_overlay["radius"] * orig_size * scale
                painter.setPen(QtGui.QPen(QtGui.QColor(0, 200, 255), 2))
                painter.setBrush(QtGui.QBrush(QtGui.QColor(0, 200, 255, 50)))
                painter.drawEllipse(QtCore.QRectF(mx - r, my - r, r * 2, r * 2))

        painter.end()
