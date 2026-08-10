"""Pattern display for the two HDMI touch screens, structured like
beamer_controls.py: ScreenControls owns a QApplication with one fullscreen
window per screen (indices from shared_states.screen_indices) and paints
patterns locally. Its display_pattern(screen_id, pattern_id) call — or a
{"screen_id", "pattern_id"} dict on a multiprocessing queue, see
screen_process() — overwrites whatever that screen is currently showing.

Three patterns, accepted as a canonical name, an alias, or an int 0/1/2:
    "black"   / 0 — a plain black screen
    "circles" / 1 — a scatter of white circles on a black background
    "zigzag"  / 2 — black zigzag lines on a white background
"""

import queue
import random
import signal

from PyQt5 import QtWidgets, QtGui, QtCore

try:
    import shared_states
except Exception:
    shared_states = None

# ── Pattern ids ─────────────────────────────────────────────────────────────────

PATTERN_BLACK   = "black"
PATTERN_CIRCLES = "circles"
PATTERN_ZIGZAG  = "zigzag"

# Accept ints (0/1/2), the canonical names, or a few friendly aliases.
_PATTERN_ALIASES = {
    0: PATTERN_BLACK,   "0": PATTERN_BLACK,   "black": PATTERN_BLACK,
    "blank": PATTERN_BLACK, "off": PATTERN_BLACK,
    1: PATTERN_CIRCLES, "1": PATTERN_CIRCLES, "circles": PATTERN_CIRCLES,
    "white_circles": PATTERN_CIRCLES, "dots": PATTERN_CIRCLES,
    2: PATTERN_ZIGZAG,  "2": PATTERN_ZIGZAG,  "zigzag": PATTERN_ZIGZAG,
    "black_zigzag": PATTERN_ZIGZAG, "lines": PATTERN_ZIGZAG,
}


def normalize_pattern(pattern_id):
    """Return the canonical pattern name for an int/str id (unknown → black)."""
    key = pattern_id.strip().lower() if isinstance(pattern_id, str) else pattern_id
    return _PATTERN_ALIASES.get(key, PATTERN_BLACK)


# ── Pattern window ──────────────────────────────────────────────────────────────

class _PatternWindow(QtWidgets.QWidget):
    """Fullscreen window for one screen; paints the currently-set pattern.
    WA_OpaquePaintEvent avoids the Qt background-flicker issue worked around
    the same way elsewhere in this GUI."""

    _BLACK = QtGui.QColor(0, 0, 0)
    _WHITE = QtGui.QColor(255, 255, 255)

    def __init__(self, title="Screen"):
        super().__init__()
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setWindowTitle(title)
        self.setCursor(QtCore.Qt.BlankCursor)   # hide the pointer on the touch screen
        self.pattern = PATTERN_BLACK

    def set_pattern(self, pattern):
        self.pattern = pattern
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        w, h = self.width(), self.height()
        if self.pattern == PATTERN_CIRCLES:
            self._paint_circles(painter, w, h)
        elif self.pattern == PATTERN_ZIGZAG:
            self._paint_zigzag(painter, w, h)
        else:
            painter.fillRect(self.rect(), self._BLACK)

    def _paint_circles(self, painter, w, h):
        """A scatter of white filled circles on black."""
        painter.fillRect(self.rect(), self._BLACK)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(self._WHITE)
        # Deterministic layout (seeded by size) so repaints don't shift/flicker.
        rng = random.Random(f"circles-{w}x{h}")
        r_min, r_max = max(6.0, h * 0.02), max(12.0, h * 0.08)
        for _ in range(40):
            cx, cy = rng.uniform(0, w), rng.uniform(0, h)
            r = rng.uniform(r_min, r_max)
            painter.drawEllipse(QtCore.QPointF(cx, cy), r, r)

    def _paint_zigzag(self, painter, w, h):
        """Rows of black zigzag (triangle-wave) lines on white."""
        painter.fillRect(self.rect(), self._WHITE)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        pen = QtGui.QPen(self._BLACK)
        pen.setWidth(max(2, int(h / 180)))
        painter.setPen(pen)
        rows = 12
        row_h = h / rows
        amp = row_h * 0.35
        teeth = 16
        half = w / (teeth * 2.0)
        for i in range(rows):
            y0 = (i + 0.5) * row_h
            pts = []
            x, k = 0.0, 0
            while x < w + half:
                y = y0 + (amp if k % 2 else -amp)
                pts.append(QtCore.QPointF(min(x, float(w)), y))
                x += half
                k += 1
            painter.drawPolyline(QtGui.QPolygonF(pts))


# ── Control class ───────────────────────────────────────────────────────────────

class ScreenControls:
    """Owns the fullscreen windows for the HDMI screens and applies patterns.

    display_pattern() is thread-safe: it validates and enqueues the request, and a
    QTimer running on the Qt thread applies it to the right window (Qt widgets must
    only be touched from the GUI thread).
    """

    def __init__(self, screen_queue=None, running_flag=None):
        print("Initializing Screen Controls.")
        self.screen_queue = screen_queue    # optional multiprocessing.Queue of dicts
        self.running_flag = running_flag    # optional multiprocessing.Value('b')
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

        indices = list(getattr(shared_states, "screen_indices", [2, 3]) or [2, 3])
        self.windows = []
        for i, idx in enumerate(indices):
            win = _PatternWindow(title=f"Screen {i + 1}")
            self._place(win, int(idx))
            self.windows.append(win)
        self.n_attached = sum(1 for w in self.windows if w.isVisible())

        self._write_queue = queue.Queue()   # thread-safe; fed by display_pattern()
        self._poll_timer = QtCore.QTimer()
        self._poll_timer.timeout.connect(self._poll)
        print(f"Screen Controls ready ({self.n_attached}/{len(self.windows)} "
              f"screen(s) attached).")

    def _place(self, win, idx):
        """Move `win` fullscreen onto QApplication screen index `idx`.

        If that index doesn't exist (touch screen not plugged in), the window
        stays hidden rather than falling back to the primary display and
        covering the control monitor — commands for it are silently accepted
        but not shown.
        """
        screens = self.app.screens()
        if idx < 0 or idx >= len(screens):
            print(f"[Screens] screen index {idx} unavailable ({len(screens)} screen(s)); "
                  f"'{win.windowTitle()}' stays hidden until it is connected.")
            return
        screen = screens[idx]
        win.setGeometry(screen.geometry())
        win.winId()                          # realize the native window
        handle = win.windowHandle()
        if handle is not None:
            handle.setScreen(screen)
        win.showFullScreen()

    # -- public API --

    def display_pattern(self, screen_id, pattern_id):
        """Show `pattern_id` on screen `screen_id` (1-based), overwriting whatever it
        is currently showing. Thread-safe: the update is queued and applied on the
        Qt thread."""
        try:
            screen_id = int(screen_id)
        except (TypeError, ValueError):
            print(f"[Screens] invalid screen_id {screen_id!r}")
            return
        if not 1 <= screen_id <= len(self.windows):
            print(f"[Screens] screen_id {screen_id} out of range (1..{len(self.windows)})")
            return
        self._write_queue.put((screen_id - 1, normalize_pattern(pattern_id)))

    def stop(self):
        """Signal the run loop to exit (if driven by a running flag)."""
        if self.running_flag is not None:
            self.running_flag.value = False

    # -- Qt-thread poller --

    def _poll(self):
        if self.running_flag is not None and not self.running_flag.value:
            self._poll_timer.stop()
            for win in self.windows:
                win.close()
            self.app.quit()
            return

        # External commands from another process (e.g. the GUI) → display_pattern.
        if self.screen_queue is not None:
            while True:
                try:
                    cmd = self.screen_queue.get_nowait()
                except Exception:
                    break
                if isinstance(cmd, dict):
                    self.display_pattern(cmd.get("screen_id"), cmd.get("pattern_id"))

        # Internal queue → apply to the windows on this (Qt) thread.
        while True:
            try:
                idx, pattern = self._write_queue.get_nowait()
            except queue.Empty:
                break
            if 0 <= idx < len(self.windows):
                self.windows[idx].set_pattern(pattern)

    def run(self):
        self._poll_timer.start(20)
        self.app.exec_()


def screen_process(screen_queue, running_flag):
    """Process entry point: runs a ScreenControls until running_flag clears."""
    from console_log import tag_process
    tag_process("Screens")

    # Prevent Queue feeder threads from blocking this process's exit.
    screen_queue.cancel_join_thread()

    # Convert SIGTERM (from screen_proc.terminate()) into a clean loop exit.
    def _handle_term(_sig, _frame):
        running_flag.value = False
    signal.signal(signal.SIGTERM, _handle_term)

    controls = ScreenControls(screen_queue, running_flag)
    controls.run()


if __name__ == "__main__":
    controls = ScreenControls()
    print(f"Cycling test patterns on {len(controls.windows)} screen(s).")

    # Step through a short visual test, then quit. Each step overwrites the screens.
    steps = [
        ("Screen 1 → white circles,  Screen 2 → black zigzag",
         [(1, PATTERN_CIRCLES), (2, PATTERN_ZIGZAG)]),
        ("swap: Screen 1 → black zigzag,  Screen 2 → white circles",
         [(1, PATTERN_ZIGZAG), (2, PATTERN_CIRCLES)]),
        ("both screens black",
         [(1, PATTERN_BLACK), (2, PATTERN_BLACK)]),
    ]

    def run_step(i=0):
        if i >= len(steps):
            QtCore.QTimer.singleShot(500, controls.app.quit)
            return
        label, cmds = steps[i]
        print(f"  [{i + 1}/{len(steps)}] {label}")
        for sid, pat in cmds:
            controls.display_pattern(sid, pat)
        QtCore.QTimer.singleShot(2000, lambda: run_step(i + 1))

    QtCore.QTimer.singleShot(300, run_step)
    controls.run()
    print("Done.")
