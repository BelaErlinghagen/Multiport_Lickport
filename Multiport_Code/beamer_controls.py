"""beamer_controls.py — Driver for the HDMI-connected beamer (projector).

Architecture mirrors camera_controls.py / serial_controls.py:
  - BeamerProjector class    : owns the QApplication + fullscreen projection
                               window and all rendering logic; can be exercised
                               standalone.
  - beamer_process()         : multiprocessing target launched by main.py.

The projector runs in its own process with its own QApplication and a fullscreen
QWidget placed on the beamer's extended-display screen. Commands arrive as dicts
on *beamer_queue* (the beamer analogue of serial_controls' command_queue):

  {"cmd": "sphere", "x_cm", "y_cm", "diameter_cm", "shadow": bool, "duration": s}
        Project a filled circle. Coordinates are centimetres from the arena
        centre; converted to pixels via the calibration. shadow=True draws a
        black sphere on a white field (else white on black). duration>0 auto-
        clears after that many seconds.
  {"cmd": "sphere_px", "cx", "cy", "diameter_px", "shadow", "duration"}
        Same, but geometry is given directly in pixels (used by the calibration
        wizard, which works before any cm scale exists).
  {"cmd": "fill", "color": "black"|"white"}   solid field.
  {"cmd": "clear"}                            blank to black.
  {"cmd": "reload_calibration"}               re-read the calibration JSON.
"""

import json
import signal
import time

from PyQt5 import QtWidgets, QtGui, QtCore

import shared_states


# ── Calibration ────────────────────────────────────────────────────────────────

class BeamerCalibration:
    """Loads beamer calibration and converts arena centimetres to screen pixels.

    Reads beamer_calibration_path (written by the GUI calibration wizard). When
    the file is missing or incomplete it falls back to an uncalibrated default so
    the projector still runs — cm inputs are then only a rough guess derived from
    the live screen size.
    """

    def __init__(self, screen_size=None):
        # screen_size = (w, h) of the actual projection window; used to place the
        # default origin at screen centre and to guess a scale pre-calibration.
        self.screen_w, self.screen_h = screen_size or (1920, 1080)
        self.px_per_cm = None
        self.origin_px = (self.screen_w / 2.0, self.screen_h / 2.0)
        self.x_sign = 1
        self.y_sign = 1
        self.projection_radius_px = None   # max usable projection radius, or None
        self.cam_to_beamer = None   # 2×3 affine [[a,b,c],[d,e,f]] or None
        self.reload()

    def reload(self):
        """Re-read the calibration JSON; keep safe defaults on any failure."""
        try:
            with open(shared_states.beamer_calibration_path, "r") as fh:
                data = json.load(fh)
        except Exception:
            data = {}

        # Last-resort scale so an uncalibrated projector still shows *something*
        # sane (roughly a 20 cm-tall field maps to full screen height).
        self.px_per_cm = float(data.get("px_per_cm") or (self.screen_h / 20.0))
        origin = data.get("origin_px")
        if origin and len(origin) == 2:
            self.origin_px = (float(origin[0]), float(origin[1]))
        else:
            self.origin_px = (self.screen_w / 2.0, self.screen_h / 2.0)
        self.x_sign = int(data.get("x_sign", 1)) or 1
        self.y_sign = int(data.get("y_sign", 1)) or 1
        pr = data.get("projection_radius_px")
        self.projection_radius_px = float(pr) if pr else None
        self.cam_to_beamer = data.get("camera_to_beamer")   # 2×3 affine or None

    def cm_to_px(self, x_cm, y_cm, diameter_cm):
        """Return (cx_px, cy_px, radius_px) for an arena-centred cm request."""
        cx = self.origin_px[0] + x_cm * self.px_per_cm * self.x_sign
        cy = self.origin_px[1] + y_cm * self.px_per_cm * self.y_sign
        radius = (diameter_cm / 2.0) * self.px_per_cm
        return cx, cy, radius

    def dlc_to_px(self, u, v):
        """Map a normalised DLC coordinate to beamer pixels via the camera↔beamer
        affine. (u, v) are fractions (0–1) of the tracking frame — the same frame
        DLC runs on. Returns (cx_px, cy_px), or None if uncalibrated."""
        m = self.cam_to_beamer
        if not m:
            return None
        (a, b, c), (d, e, f) = m
        return (a * u + b * v + c, d * u + e * v + f)

    def px_to_dlc(self, bx, by):
        """Inverse of dlc_to_px: beamer pixels → normalised DLC (u, v).

        Inverts the 2×2 linear part of camera_to_beamer. Returns None if
        uncalibrated or the mapping is singular (collinear calibration points)."""
        m = self.cam_to_beamer
        if not m:
            return None
        (a, b, c), (d, e, f) = m
        det = a * e - b * d
        if abs(det) < 1e-12:
            return None
        px, py = bx - c, by - f
        u = ( e * px - b * py) / det
        v = (-d * px + a * py) / det
        return (u, v)

    def cm_to_dlc(self, x_cm, y_cm, diameter_cm):
        """Map an arena-centred cm request to a normalised DLC circle (u, v, r).

        cm → beamer px → normalised DLC, for both the centre and a point on the
        rim, so r is the radius in DLC-normalised units. Returns None if the
        camera↔beamer mapping is unavailable. Used to draw the camera contour."""
        cx, cy, radius = self.cm_to_px(x_cm, y_cm, diameter_cm)
        centre = self.px_to_dlc(cx, cy)
        edge   = self.px_to_dlc(cx + radius, cy)
        if centre is None or edge is None:
            return None
        r = ((edge[0] - centre[0]) ** 2 + (edge[1] - centre[1]) ** 2) ** 0.5
        return (centre[0], centre[1], r)


# ── Projection window ───────────────────────────────────────────────────────────

class _ProjectionWindow(QtWidgets.QWidget):
    """Fullscreen renderer for the beamer.

    Holds a single render state — a solid background plus an optional filled
    circle — and paints it edge to edge. WA_OpaquePaintEvent stops Qt from
    pre-filling the background (the flicker-free idiom used across this GUI).
    """

    _BLACK = QtGui.QColor(0, 0, 0)
    _WHITE = QtGui.QColor(255, 255, 255)

    def __init__(self):
        super().__init__()
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setWindowTitle("Beamer")
        self.setCursor(QtCore.Qt.BlankCursor)   # hide the pointer on the projection
        self._bg = self._BLACK
        self._field = None    # (cx, cy, radius, QColor) lit disc drawn under the sphere
        self._circle = None   # (cx, cy, radius, QColor) or None

    def show_sphere(self, cx, cy, radius, shadow, light_color=None, field=None):
        # The "lit" region uses light_color (default white); the rest is black.
        #   Normal      : black background + lit sphere.
        #   Shadow      : black sphere over a lit region. If *field* (fx, fy, fr)
        #                 is given, the lit region is that disc (the calibrated
        #                 projection area); otherwise the whole screen is lit.
        lit = light_color if light_color is not None else self._WHITE
        if shadow:
            if field is not None:
                fx, fy, fr = field
                self._bg = self._BLACK
                self._field = (fx, fy, max(0.0, fr), lit)
            else:
                self._bg = lit
                self._field = None
            self._circle = (cx, cy, max(0.0, radius), self._BLACK)
        else:
            self._bg = self._BLACK
            self._field = None
            self._circle = (cx, cy, max(0.0, radius), lit)
        self.update()

    def show_fill(self, color):
        self._bg = color if isinstance(color, QtGui.QColor) else self._WHITE
        self._field = None
        self._circle = None
        self.update()

    def clear(self):
        self._bg = self._BLACK
        self._field = None
        self._circle = None
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), self._bg)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(QtCore.Qt.NoPen)
        for shape in (self._field, self._circle):
            if shape is not None:
                cx, cy, radius, color = shape
                painter.setBrush(color)
                painter.drawEllipse(QtCore.QPointF(cx, cy), radius, radius)


# ── Projector ───────────────────────────────────────────────────────────────────

class BeamerProjector:
    """Owns the QApplication + fullscreen window and services beamer_queue.

    Mirrors TrackingCamera / SerialControls: all rendering logic lives here so it
    can be run standalone, while beamer_process() is the multiprocessing entry.
    """

    def __init__(self, beamer_queue, running_flag):
        print("Initializing Beamer.")
        self.beamer_queue = beamer_queue
        self.running_flag = running_flag
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.window = _ProjectionWindow()
        self._geo = self._place_on_beamer_screen()

        self.calib = BeamerCalibration(
            screen_size=(self._geo.width(), self._geo.height())
        )

        # Single-shot timer that clears a timed sphere after its duration.
        self._duration_timer = QtCore.QTimer(self.window)
        self._duration_timer.setSingleShot(True)
        self._duration_timer.timeout.connect(self.window.clear)

        # Poll the command queue + running flag on the Qt event loop.
        self._poll_timer = QtCore.QTimer(self.window)
        self._poll_timer.timeout.connect(self._poll)
        print("Beamer is ready.")

    def _place_on_beamer_screen(self):
        """Move the window onto the configured extended-display screen, fullscreen.

        If that screen doesn't exist (the beamer isn't plugged in, or the display
        indices shifted after a cabling change) the window stays hidden instead of
        falling back to the primary display — a fullscreen projection window there
        just blacks out the control monitor. Commands are still accepted and simply
        not projected, matching how screen_controls handles a missing touch screen.

        Returns the geometry the calibration should assume for its default size.
        """
        screens = self.app.screens()
        idx = int(getattr(shared_states, "beamer_screen_index", 1))
        if not screens:
            return QtCore.QRect(0, 0, 1920, 1080)
        if idx < 0 or idx >= len(screens):
            print(f"[Beamer] screen index {idx} unavailable "
                  f"({len(screens)} screen(s)); the projection window stays hidden "
                  f"until the beamer is connected. Check "
                  f"shared_states.beamer_screen_index against the current displays.")
            return screens[0].geometry()
        screen = screens[idx]
        geo = screen.geometry()
        # Position on the target screen *before* going fullscreen so the window
        # manager sends it to the right monitor.
        self.window.setGeometry(geo)
        self.window.winId()                     # realize native window
        handle = self.window.windowHandle()
        if handle is not None:
            handle.setScreen(screen)
        self.window.showFullScreen()
        return geo

    # ── Command handling ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_color(value):
        """Turn an [r, g, b] list (0–255) into a QColor; None if absent/invalid."""
        if not value:
            return None
        try:
            r, g, b = value[:3]
            return QtGui.QColor(int(r), int(g), int(b))
        except Exception:
            return None

    def _show_sphere(self, cx, cy, radius, shadow, duration, light_color=None):
        self._duration_timer.stop()
        # In shadow mode, bound the lit region to the calibrated projection area
        # (the arena disc) instead of lighting the whole screen.
        field = None
        if shadow and self.calib.projection_radius_px:
            ox, oy = self.calib.origin_px
            field = (ox, oy, self.calib.projection_radius_px)
        self.window.show_sphere(cx, cy, radius, shadow, light_color, field)
        if duration and duration > 0:
            self._duration_timer.start(int(duration * 1000))

    def _handle(self, cmd):
        if not isinstance(cmd, dict):
            return
        action = cmd.get("cmd")
        if action == "sphere":
            cx, cy, radius = self.calib.cm_to_px(
                float(cmd.get("x_cm", 0.0)),
                float(cmd.get("y_cm", 0.0)),
                float(cmd.get("diameter_cm", 0.0)),
            )
            self._show_sphere(cx, cy, radius,
                              bool(cmd.get("shadow", False)),
                              float(cmd.get("duration", 0.0) or 0.0),
                              self._parse_color(cmd.get("color")))
        elif action == "sphere_px":
            self._show_sphere(
                float(cmd.get("cx", self.calib.origin_px[0])),
                float(cmd.get("cy", self.calib.origin_px[1])),
                float(cmd.get("diameter_px", 0.0)) / 2.0,
                bool(cmd.get("shadow", False)),
                float(cmd.get("duration", 0.0) or 0.0),
                self._parse_color(cmd.get("color")),
            )
        elif action == "fill":
            self._duration_timer.stop()
            col = self._parse_color(cmd.get("color")) or QtGui.QColor(255, 255, 255)
            self.window.show_fill(col)
        elif action == "clear":
            self._duration_timer.stop()
            self.window.clear()
        elif action == "reload_calibration":
            self.calib.reload()

    def _poll(self):
        """Drain pending commands and honour the shutdown flag."""
        if not self.running_flag.value:
            self._poll_timer.stop()
            self.window.close()
            self.app.quit()
            return
        while True:
            try:
                cmd = self.beamer_queue.get_nowait()
            except Exception:
                break
            try:
                self._handle(cmd)
            except Exception as exc:
                print(f"[Beamer] bad command {cmd!r}: {exc}")

    def run(self):
        self._poll_timer.start(15)
        self.app.exec_()


def beamer_process(beamer_queue, running_flag):
    from console_log import tag_process
    tag_process("Beamer")

    # Prevent Queue feeder threads from blocking this process's atexit.
    beamer_queue.cancel_join_thread()

    # Convert SIGTERM (from beamer_proc.terminate()) into a clean loop exit.
    def _handle_term(_sig, _frame):
        running_flag.value = False
    signal.signal(signal.SIGTERM, _handle_term)

    projector = BeamerProjector(beamer_queue, running_flag)
    projector.run()


if __name__ == "__main__":
    import multiprocessing as mp
    from multiprocessing import Queue, Value

    mp.set_start_method("spawn", force=True)
    q = Queue(maxsize=8)
    running = Value('b', True)
    proc = mp.Process(target=beamer_process, args=(q, running))
    proc.start()

    # Push a few test frames a couple seconds apart, then shut down. Watch the
    # beamer: a centred 10 cm light sphere, an off-centre one, a shadow sphere,
    # then blank.
    try:
        time.sleep(2)
        q.put({"cmd": "sphere", "x_cm": 0, "y_cm": 0, "diameter_cm": 10, "shadow": False})
        time.sleep(2)
        q.put({"cmd": "sphere", "x_cm": 5, "y_cm": 5, "diameter_cm": 6, "shadow": False})
        time.sleep(2)
        q.put({"cmd": "sphere", "x_cm": 0, "y_cm": 0, "diameter_cm": 12, "shadow": True})
        time.sleep(2)
        q.put({"cmd": "clear"})
        time.sleep(1)
    finally:
        running.value = False
        proc.join(timeout=3)
        if proc.is_alive():
            proc.terminate()
            proc.join()
