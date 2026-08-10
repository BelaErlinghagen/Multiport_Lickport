"""CameraWidget: the flicker-free live camera preview used on the Experiment
and Cleaning/Testing tabs, with an optional DeepLabCut keypoint overlay."""
from PyQt5 import QtWidgets, QtGui, QtCore


_KP_RADIUS = 4                # keypoint circle radius in widget pixels


def _overlay_threshold():
    """Likelihood above which a keypoint is drawn as confident (solid)."""
    try:
        import shared_states
        return float(getattr(shared_states, "dlc_overlay_min_likelihood", 0.5))
    except Exception:
        return 0.5


def _dlc_image_size(fallback):
    """Size of the image DLC's coordinates are expressed in.

    Poses come back in DLC_CROP pixels — the arena crop, not the full sensor
    and not the downscaled preview — whatever shared_states.dlc_resize is set
    to, since DLCLive rescales its output. Getting this wrong silently drags
    every keypoint toward one corner instead of failing.
    """
    try:
        from shared_states import DLC_CROP
        y0, y1, x0, x1 = DLC_CROP
        return (x1 - x0), (y1 - y0)
    except Exception:
        return fallback

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

    Inheriting QOpenGLWidget rather than QWidget switches the entire host
    window into OpenGL compositing: every widget is painted into a shared
    off-screen framebuffer, this widget's own framebuffer is composited on
    top, and the result is presented in one atomic, vsync-synchronized swap.

    That matters under XWayland/Wayland (the target GNOME setup): the
    plain-QWidget software path pushes dirty regions to the X server
    immediately and unsynchronised, letting the Wayland compositor see
    intermediate window states and flicker. The GL path shows nothing until
    the swap completes, eliminating that.

    When *pose_display_queue* is supplied (from the DLC process), each call
    to update_from_shared() also drains the latest pose and overlays
    keypoint dots in paintGL().
    """

    def __init__(self, frame_queue, shape, pose_display_queue=None):
        super().__init__()
        self._pixmap            = None
        self._last_frame        = None   # latest grayscale frame (numpy H×W)
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

        self._last_frame = frame
        h, w = self.shape
        qimg = QtGui.QImage(
            frame.tobytes(), w, h, w, QtGui.QImage.Format_Grayscale8
        )
        self._pixmap = QtGui.QPixmap.fromImage(qimg)
        self.update()

    def latest_frame(self):
        """Return the most recent grayscale frame (numpy H×W) or None.

        Used by the beamer calibration dialog to show a live feed without
        draining frame_queue (which this widget already owns).
        """
        return self._last_frame

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

        # ── DLC keypoint overlay ──────────────────────────────────────────────
        if self._last_pose is not None and scaled_w > 0:
            # Map DLC_CROP pixels onto the drawn pixmap. Per-axis rather than one
            # shared factor, so a non-square crop still lands correctly.
            dlc_w, dlc_h = _dlc_image_size((self.shape[1], self.shape[0]))
            scale_x = scaled_w / float(dlc_w)
            scale_y = scaled_h / float(dlc_h)
            threshold = _overlay_threshold()

            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            n_colours = len(_KP_COLOURS)
            for idx, kp in enumerate(self._last_pose):
                kx, ky, likelihood = float(kp[0]), float(kp[1]), float(kp[2])
                wx = int(kx * scale_x) + off_x
                wy = int(ky * scale_y) + off_y
                colour = _KP_COLOURS[idx % n_colours]
                if likelihood >= threshold:
                    painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
                    painter.setBrush(QtGui.QBrush(colour))
                else:
                    # Drawn hollow and dim rather than hidden: a preview that goes
                    # blank when the model is unsure looks exactly like DLC having
                    # died, which is the one thing the live view should rule out.
                    faint = QtGui.QColor(colour)
                    faint.setAlpha(110)
                    painter.setPen(QtGui.QPen(faint, 1))
                    painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawEllipse(
                    wx - _KP_RADIUS, wy - _KP_RADIUS,
                    _KP_RADIUS * 2,  _KP_RADIUS * 2,
                )

        # ── ITI region overlay ────────────────────────────────────────────────
        # overlay is a dict from ProtocolPage: {"target": {x,y,radius},
        # "margin": {x,y,radius}}, in normalised camera coords (fraction of
        # the frame). Either key may be absent.
        if self._iti_overlay is not None and scaled_w > 0:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

            # Coordinates are fractions of the frame, so they scale by the drawn
            # pixmap's size directly.
            margin = self._iti_overlay.get("margin")
            if margin is not None:
                mx = margin["x"] * scaled_w + off_x
                my = margin["y"] * scaled_h + off_y
                mr = margin["radius"] * scaled_w
                painter.setPen(QtGui.QPen(QtGui.QColor(255, 180, 0), 2,
                                          QtCore.Qt.DashLine))
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawEllipse(QtCore.QRectF(mx - mr, my - mr, mr * 2, mr * 2))

            target = self._iti_overlay.get("target")
            if target is not None:
                cx = target["x"] * scaled_w + off_x
                cy = target["y"] * scaled_h + off_y
                r  = target["radius"] * scaled_w
                painter.setPen(QtGui.QPen(QtGui.QColor(0, 200, 255), 2))
                painter.setBrush(QtGui.QBrush(QtGui.QColor(0, 200, 255, 50)))
                painter.drawEllipse(QtCore.QRectF(cx - r, cy - r, r * 2, r * 2))

        painter.end()
