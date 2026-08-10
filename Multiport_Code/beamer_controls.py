"""Driver for the HDMI-connected beamer (projector), structured like
camera_controls.py / serial_controls.py: BeamerProjector owns the
QApplication, the fullscreen projection window, and all rendering logic (and
can be run standalone); beamer_process() is the multiprocessing entry point
launched by main.py. Commands arrive as dicts on beamer_queue:

  {"cmd": "sphere", "x_cm", "y_cm", "diameter_cm", "mode", "color", "field_color",
                    "duration": s}
        Project a filled circle. Coordinates are centimetres from the arena
        centre, converted to pixels via BeamerCalibration. duration > 0
        auto-clears the sphere after that many seconds. "mode" picks a
        background/target combination:

            "light"           black background, sphere drawn in "color"
            "shadow"          background lit in "field_color", sphere black
                              (a hole punched in the lit field)
            "lit_background"  background lit in "field_color", sphere drawn in
                              "color" on top of it

        A diameter of 0 in the two lit modes projects just the lit field, no target.
  {"cmd": "sphere_px", "cx", "cy", "diameter_px", "mode", "color", "field_color",
                       "duration"}
        Same as "sphere" but in raw pixels — used by the calibration wizard,
        which runs before any cm scale exists.
  {"cmd": "fill", "color": "black"|"white"}   solid field.
  {"cmd": "clear"}                            blank to black.
  {"cmd": "reload_calibration"}               re-read the calibration JSON.
"""

import json
import signal
import time

from PyQt5 import QtWidgets, QtGui, QtCore

import shared_states
from hardware_state import BEAMER_LIT_MODES, BEAMER_MODES


# ── Protocol schema ────────────────────────────────────────────────────────────

# Menu text for the projection modes, shared by the protocol editor and the
# Cleaning tab's test sphere so label text and stored key never drift apart.
BEAMER_MODE_LABELS = {
    "light":          "Light (dark arena, bright target)",
    "shadow":         "Shadow (lit arena, dark target)",
    "lit_background": "Lit background (lit arena, brighter target)",
}
BEAMER_MODE_KEYS = {v: k for k, v in BEAMER_MODE_LABELS.items()}

# Default shape of the top-level "beamer" protocol block (see
# ProtocolPage.DEFAULT_PROTOCOL). The background is dimmer than the sphere by
# default: the projector is additive, so a sphere in lit_background mode is
# only visible if it out-shines its field.
_DEFAULT_BEAMER = {"mode": "light",
                   "background_color": [255, 255, 255],
                   "background_brightness": 30}


def normalise_protocol_beamer(protocol: dict) -> dict:
    """Upgrade an older protocol (with no top-level "beamer" block), in place.

    The projection mode used to be a per-region boolean ("shadow") rather
    than a session-wide setting; this reconstructs the new "beamer" block
    from the old field so protocol files saved before that change still
    load. Deliberately the only tolerant protocol read in the codebase —
    everywhere else a missing key raises. Idempotent, so it's safe to call
    at every load site (protocol editor, experiment tab, state machine).
    """
    if not isinstance(protocol, dict) or "beamer" in protocol:
        return protocol

    # Read whichever ITI region was actually active; its colour/brightness
    # become the new background, reproducing the old rendering exactly.
    iti = protocol.get("intertrial") or {}
    region = None
    if iti.get("type") == "fixed_region":
        region = iti.get("region")
    elif iti.get("type") == "random_region":
        region = iti.get("random_region")

    beamer = dict(_DEFAULT_BEAMER)
    beamer["background_color"] = list(beamer["background_color"])
    if isinstance(region, dict):
        beamer["mode"] = "shadow" if region.get("shadow") else "light"
        beamer["background_color"] = list(region.get("color", [255, 255, 255]))
        beamer["background_brightness"] = int(region.get("brightness", 100))
    protocol["beamer"] = beamer

    # The per-region flags are dead weight now; drop them so a re-saved file is clean.
    for key in ("region", "random_region"):
        stale = iti.get(key)
        if isinstance(stale, dict):
            stale.pop("shadow", None)
    return protocol


# ── Calibration ────────────────────────────────────────────────────────────────

class BeamerCalibration:
    """Converts arena centimetres to screen pixels, using the calibration
    written by the GUI's calibration wizard (shared_states.beamer_calibration_path).
    Falls back to a rough guess based on screen size if the file is missing
    or incomplete, so the projector still runs uncalibrated rather than failing.
    """

    def __init__(self, screen_size=None):
        # screen_size = (w, h) of the projection window, used for the default
        # origin (screen centre) and to guess a scale before calibration.
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

        # Fallback scale so an uncalibrated projector still shows something
        # reasonable (roughly a 20 cm-tall field mapped to full screen height).
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
        """Map a normalised DLC coordinate (u, v in 0-1, fractions of the DLC
        tracking frame) to beamer pixels. Returns None if uncalibrated."""
        m = self.cam_to_beamer
        if not m:
            return None
        (a, b, c), (d, e, f) = m
        return (a * u + b * v + c, d * u + e * v + f)

    def px_to_dlc(self, bx, by):
        """Inverse of dlc_to_px: beamer pixels -> normalised DLC (u, v).
        Returns None if uncalibrated or the mapping is singular."""
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
        """Map an arena-centred cm request to a normalised DLC circle (u, v, r),
        by converting both the centre and a rim point through cm -> px -> DLC.
        Returns None if uncalibrated. Used to draw the camera-view contour."""
        cx, cy, radius = self.cm_to_px(x_cm, y_cm, diameter_cm)
        centre = self.px_to_dlc(cx, cy)
        edge   = self.px_to_dlc(cx + radius, cy)
        if centre is None or edge is None:
            return None
        r = ((edge[0] - centre[0]) ** 2 + (edge[1] - centre[1]) ** 2) ** 0.5
        return (centre[0], centre[1], r)


# ── Projection window ───────────────────────────────────────────────────────────

class _ProjectionWindow(QtWidgets.QWidget):
    """Fullscreen widget that renders one background colour plus an optional
    filled circle on top. WA_OpaquePaintEvent avoids the Qt/X11 background
    flicker worked around the same way elsewhere in this GUI."""

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

    def show_sphere(self, cx, cy, radius, mode,
                    sphere_color=None, field=None, field_color=None):
        # One rendered frame: a background plus a sphere on top of it.
        #   light          black background + sphere in sphere_color
        #   shadow         lit background   + black sphere (a hole in the field)
        #   lit_background lit background   + sphere in sphere_color on top
        # The two lit modes share this branch; only the sphere colour differs.
        # If *field* (fx, fy, fr) is given, only that disc is lit (the
        # calibrated projection area); otherwise the whole screen is lit.
        sphere = sphere_color if sphere_color is not None else self._WHITE
        if mode in BEAMER_LIT_MODES:
            lit = field_color if field_color is not None else self._WHITE
            if field is not None:
                fx, fy, fr = field
                self._bg = self._BLACK
                self._field = (fx, fy, max(0.0, fr), lit)
            else:
                self._bg = lit
                self._field = None
        else:
            self._bg = self._BLACK
            self._field = None
        self._circle = (cx, cy, max(0.0, radius),
                        self._BLACK if mode == "shadow" else sphere)
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
    """Owns the QApplication, fullscreen projection window, and calibration;
    services beamer_queue. Can run standalone (see __main__ below) or inside
    beamer_process(), the multiprocessing entry point."""

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
        """Move the window fullscreen onto the configured beamer display.

        If that screen index doesn't exist (beamer unplugged, or display
        indices shifted after a cabling change), the window stays hidden
        rather than falling back to the primary display and blacking out the
        control monitor — commands are still accepted, just not shown, the
        same way screen_controls.py handles a missing touch screen.

        Returns the geometry the calibration should use as its default size.
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

    @staticmethod
    def _mode_of(cmd):
        """Return a sphere command's projection mode, falling back to the
        older boolean "shadow" flag so a stale command still renders."""
        mode = cmd.get("mode")
        if mode in BEAMER_MODES:
            return mode
        return "shadow" if cmd.get("shadow") else "light"

    def _show_sphere(self, cx, cy, radius, mode, duration,
                     sphere_color=None, field_color=None):
        self._duration_timer.stop()
        # In the lit modes, restrict the lit area to the calibrated projection
        # disc instead of lighting the whole screen.
        field = None
        if mode in BEAMER_LIT_MODES and self.calib.projection_radius_px:
            ox, oy = self.calib.origin_px
            field = (ox, oy, self.calib.projection_radius_px)
        self.window.show_sphere(cx, cy, radius, mode, sphere_color, field, field_color)
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
                              self._mode_of(cmd),
                              float(cmd.get("duration", 0.0) or 0.0),
                              self._parse_color(cmd.get("color")),
                              self._parse_color(cmd.get("field_color")))
        elif action == "sphere_px":
            self._show_sphere(
                float(cmd.get("cx", self.calib.origin_px[0])),
                float(cmd.get("cy", self.calib.origin_px[1])),
                float(cmd.get("diameter_px", 0.0)) / 2.0,
                self._mode_of(cmd),
                float(cmd.get("duration", 0.0) or 0.0),
                self._parse_color(cmd.get("color")),
                self._parse_color(cmd.get("field_color")),
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
    """Process entry point: runs a BeamerProjector until running_flag clears."""
    from console_log import tag_process
    tag_process("Beamer")

    # Prevent Queue feeder threads from blocking this process's exit.
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

    # Manual test: push a few frames a couple seconds apart and watch the
    # beamer — a centred light sphere, an off-centre one, a shadow sphere, the
    # lit_background baseline (field only), a lit_background sphere, then
    # blank. The field should not flicker or change intensity between the
    # last two; only the brighter sphere should appear on top of it.
    dim  = [70, 70, 70]
    full = [255, 255, 255]
    try:
        time.sleep(2)
        q.put({"cmd": "sphere", "x_cm": 0, "y_cm": 0, "diameter_cm": 10,
               "mode": "light", "color": full})
        time.sleep(2)
        q.put({"cmd": "sphere", "x_cm": 5, "y_cm": 5, "diameter_cm": 6,
               "mode": "light", "color": full})
        time.sleep(2)
        q.put({"cmd": "sphere", "x_cm": 0, "y_cm": 0, "diameter_cm": 12,
               "mode": "shadow", "field_color": full})
        time.sleep(2)
        q.put({"cmd": "sphere", "x_cm": 0, "y_cm": 0, "diameter_cm": 0,
               "mode": "lit_background", "color": full, "field_color": dim})
        time.sleep(2)
        q.put({"cmd": "sphere", "x_cm": 0, "y_cm": 0, "diameter_cm": 12,
               "mode": "lit_background", "color": full, "field_color": dim})
        time.sleep(2)
        q.put({"cmd": "clear"})
        time.sleep(1)
    finally:
        running.value = False
        proc.join(timeout=3)
        if proc.is_alive():
            proc.terminate()
            proc.join()
