# main_plotting.py — GUI entry point
# Imports widgets from the gui/ subpackage and builds the main window.
import sys
import traceback

from PyQt5 import QtWidgets, QtGui, QtCore

import shared_states
from gui import CameraWidget, SensorWidget, CleaningPage, ExperimentPage, ProtocolPage


def _install_exception_guard():
    """Stop a single Python error from killing the whole GUI process.

    PyQt5 (>= 5.5) calls Qt's qFatal() when an exception escapes a slot, which
    aborts the process with SIGABRT — a bad reply from the RSpace API while the
    settings dialog is open would take a running experiment down with it.
    Installing our own sys.excepthook suppresses that abort, so the error is
    logged and shown instead of being fatal.
    """
    reporting = []   # guard: a repeating error must not stack up dialogs

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            QtWidgets.QApplication.quit()
            return
        traceback.print_exception(exc_type, exc, tb)
        if reporting:
            return
        reporting.append(True)
        summary = "".join(traceback.format_exception_only(exc_type, exc)).strip()
        details = "".join(traceback.format_exception(exc_type, exc, tb))

        def report():
            # Shown from a timer, not from here: this hook runs while the stack is
            # still unwinding out of the failed slot, and opening a modal dialog's
            # nested event loop at that point is not safe.
            try:
                box = QtWidgets.QMessageBox(
                    QtWidgets.QMessageBox.Warning, "Error",
                    f"Something went wrong. The GUI is still running and any "
                    f"recording in progress is unaffected.\n\n{summary}")
                box.setDetailedText(details)
                box.exec_()
            finally:
                reporting.clear()

        QtCore.QTimer.singleShot(0, report)

    sys.excepthook = hook


def _control_screen_rect(app):
    """Resolve where the main window belongs: (QScreen or None, target QRect).

    The control monitor is identified by its **hardcoded** position on the X
    virtual desktop (shared_states.control_screen_geometry), not by a screens()
    index: indices reshuffle whenever a display is plugged in or wakes up in a
    different order, and the one window the experimenter has to be able to reach
    is not the one to leave up to that. The matching QScreen is only used for its
    availableGeometry, so the GNOME top bar and dock stay usable; if no screen
    matches, the hardcoded rect is used verbatim.
    """
    x, y, w, h = shared_states.control_screen_geometry
    target = QtCore.QRect(int(x), int(y), int(w), int(h))
    for screen in app.screens():
        if screen.geometry().contains(target.center()):
            return screen, screen.availableGeometry()
    print(f"[GUI] no display found at {target.getRect()}; placing the GUI there "
          f"anyway. Check shared_states.control_screen_geometry against "
          f"`xrandr --listmonitors`.")
    return None, target


def _place_on_control_screen(window, screen, rect):
    """Move/resize `window` onto the control monitor.

    Called both before the window is shown and again after it is mapped, because
    GNOME/Mutter honours an app's requested *size* at map time but overrides its
    *position*, putting a new window on whichever monitor is "current" — and the
    touch panels are pointer devices, so that is regularly one of them. Setting
    the geometry up front only gets the size across; re-asserting it after the
    map is what actually moves the window back.

    The request is only granted if the window *fits* the monitor's work area —
    Mutter bounces a move that doesn't, which is what _warn_if_misplaced checks.
    """
    window.winId()                          # realize native window
    handle = window.windowHandle()
    if handle is not None and screen is not None:
        handle.setScreen(screen)

    # setGeometry positions the *client* area, so asking for the work area
    # verbatim pushes the title bar off the top of the screen and the window can
    # no longer be dragged. Subtract whatever the WM's decoration adds. Before the
    # window is mapped the frame is not known yet and these are all zero — which
    # is fine, the post-map calls below correct it.
    frame, geo = window.frameGeometry(), window.geometry()
    target = QtCore.QRect(
        rect.left()   + (geo.left() - frame.left()),
        rect.top()    + (geo.top()  - frame.top()),
        rect.width()  - (frame.width()  - geo.width()),
        rect.height() - (frame.height() - geo.height()),
    )
    window.setGeometry(target)              # after setScreen: that can shift it back


def _warn_if_misplaced(window, rect):
    """Complain in the console log if the GUI did not land on the control monitor.

    A window manager will not place a window on a monitor whose work area cannot
    hold it, and silently drops it on another display instead — no error, the GUI
    just opens on the touch panels. That is one added GUI section away at any
    time (the window's minimum height is whatever the tallest tab demands), so
    the numbers needed to diagnose it are printed rather than left to guesswork.
    """
    frame = window.frameGeometry()
    if rect.contains(frame.center()):
        return
    hint = window.minimumSizeHint()
    print(f"[GUI] the window is at {frame.getRect()}, not on the control monitor "
          f"{rect.getRect()}. The window manager refuses to place a window that "
          f"does not fit the monitor's work area, and this one needs at least "
          f"{hint.width()}×{hint.height()} px plus the title bar — shrink the tab "
          f"pages (a QScrollArea around the tallest one) or check "
          f"shared_states.control_screen_geometry.")


class _OpaqueWidget(QtWidgets.QWidget):
    """Plain container that always paints its background.

    Setting WA_OpaquePaintEvent prevents Qt/X11 from erasing the window
    background before painting, eliminating the interaction-triggered flicker
    visible on the left panel during tab switches and button clicks.
    """
    _BG = QtGui.QColor("#2b2b2b")

    def __init__(self, parent=None):
        super().__init__(parent)
        # WA_OpaquePaintEvent: Qt skips its own background pre-fill before paintEvent.
        # WA_NoSystemBackground: X11 server does not clear the window on expose events.
        # Both are needed — they control two separate layers of the paint pipeline.
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)

    def paintEvent(self, event):
        QtGui.QPainter(self).fillRect(event.rect(), self._BG)


def run_gui(shared_image, sensor_array, shape, command_queue, data_sources=None):
    app = QtWidgets.QApplication([])
    _install_exception_guard()

    # ── Dark theme via Fusion style + QPalette ────────────────────────────────
    # Using a global QWidget stylesheet rule ("QWidget { background: ... }") forces
    # autoFillBackground=True on every widget, which causes Qt/X11 to clear every
    # container's background before painting its children — the cascade that creates
    # the visible flicker. The correct approach is to set a dark QPalette and let
    # the Fusion style handle background fills internally without the cascade.
    app.setStyle("Fusion")
    _pal = QtGui.QPalette()
    for _role, _hex in [
        (QtGui.QPalette.Window,          "#2b2b2b"),
        (QtGui.QPalette.WindowText,      "#eeeeee"),
        (QtGui.QPalette.Base,            "#2b2b2b"),
        (QtGui.QPalette.AlternateBase,   "#333333"),
        (QtGui.QPalette.ToolTipBase,     "#2b2b2b"),
        (QtGui.QPalette.ToolTipText,     "#eeeeee"),
        (QtGui.QPalette.Text,            "#eeeeee"),
        (QtGui.QPalette.Button,          "#3a3a3a"),
        (QtGui.QPalette.ButtonText,      "#eeeeee"),
        (QtGui.QPalette.BrightText,      "#ffffff"),
        (QtGui.QPalette.Link,            "#0078d7"),
        (QtGui.QPalette.Highlight,       "#005a99"),
        (QtGui.QPalette.HighlightedText, "#ffffff"),
    ]:
        _pal.setColor(_role, QtGui.QColor(_hex))
    for _role, _hex in [
        (QtGui.QPalette.WindowText, "#555555"),
        (QtGui.QPalette.Text,       "#555555"),
        (QtGui.QPalette.ButtonText, "#555555"),
        (QtGui.QPalette.Button,     "#2e2e2e"),
    ]:
        _pal.setColor(QtGui.QPalette.Disabled, _role, QtGui.QColor(_hex))
    app.setPalette(_pal)

    class MainWindow(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("MultiportGUI")
            self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)

            # ── LEFT: tab panel ───────────────────────────────────
            self.tabs = QtWidgets.QTabWidget()
            self.tabs.setDocumentMode(True)
            self.tabs.tabBar().setExpanding(True)
            self.tabs.tabBar().setMouseTracking(False)
            self.tabs.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Expanding,
            )

            page_clean = CleaningPage(command_queue,
                                      (data_sources or {}).get("beamer_queue"),
                                      (data_sources or {}).get("screen_queue"),
                                      (data_sources or {}).get("undistort_enabled"),
                                      (data_sources or {}).get("undistort_reload"))

            page_protocol = ProtocolPage((data_sources or {}).get("beamer_queue"))

            page_experiment = ExperimentPage(data_sources or {})

            self.tabs.addTab(page_clean, "Cleaning/Testing")
            self.tabs.addTab(page_protocol, "Protocol")
            self.tabs.addTab(page_experiment, "Experiment")

            left = _OpaqueWidget()
            left_layout = QtWidgets.QVBoxLayout(left)
            left_layout.setContentsMargins(0, 0, 0, 0)
            left_layout.setSpacing(0)
            left_layout.addWidget(self.tabs)

            # ── RIGHT: camera + sensors ───────────────────────────
            self.camera = CameraWidget(
                shared_image, shape,
                pose_display_queue=(data_sources or {}).get("pose_display_queue"),
            )

            # Wire ITI region overlay: ProtocolPage → CameraWidget
            page_protocol.overlay_changed.connect(self.camera.set_iti_overlay)

            # Give the beamer calibration dialog a live camera feed to drag
            # correspondence points onto (reads latest frame, no queue contention).
            page_clean.set_frame_provider(self.camera)

            self.sensors = SensorWidget(sensor_array)

            # Zero the lick counts when a recording starts, so the heat-map shows
            # the current session rather than everything since the GUI opened.
            page_experiment.recording_started.connect(self.sensors.reset_counts)

            reset_btn = QtWidgets.QPushButton("Reset counts")
            reset_btn.setFixedHeight(28)
            reset_btn.clicked.connect(self.sensors.reset_counts)

            right = _OpaqueWidget()
            right_layout = QtWidgets.QVBoxLayout(right)
            right_layout.addWidget(self.camera, stretch=3)
            right_layout.addWidget(self.sensors, stretch=2)
            right_layout.addWidget(reset_btn)

            # ── ROOT: 60/40 split ─────────────────────────────────
            root = _OpaqueWidget()
            root_layout = QtWidgets.QHBoxLayout(root)
            divider = QtWidgets.QFrame()
            divider.setFrameShape(QtWidgets.QFrame.VLine)
            divider.setFixedWidth(1)
            divider.setStyleSheet("QFrame { background: #444; }")

            root_layout.addWidget(left, 3)   # 60 %
            root_layout.addWidget(divider)
            root_layout.addWidget(right, 2)  # 40 %
            root_layout.setContentsMargins(0, 0, 0, 0)
            root_layout.setSpacing(0)

            self.setCentralWidget(root)

            # ── Style ─────────────────────────────────────────────
            # Note: QWidget { background } is intentionally absent.
            # The dark palette set above handles container backgrounds without
            # triggering autoFillBackground on every widget (the flicker cause).
            self.setStyleSheet("""
                QPushButton {
                    background: #3a3a3a;
                    border-radius: 4px;
                    padding: 4px;
                }
                QPushButton:hover    { background: #4a4a4a; }
                QPushButton:checked  { background: #005a99; }
                QPushButton:disabled { background: #2e2e2e; color: #555; }

                QTabBar::tab          { padding: 8px; margin: 2px; background: #3a3a3a; }
                QTabBar::tab:selected { background: #4a4a4a; }

                QSpinBox {
                    background: #3a3a3a;
                    border: 1px solid #555;
                    border-radius: 3px;
                    padding: 2px;
                }

                QScrollBar:vertical {
                    width: 8px;
                    background: #222;
                }
                QScrollBar::handle:vertical {
                    background: #555;
                    border-radius: 4px;
                }

                QProgressBar {
                    background: #3a3a3a;
                    border: 1px solid #555;
                    border-radius: 3px;
                    text-align: center;
                }
                QProgressBar::chunk { background: #005a99; border-radius: 3px; }

                QSlider::groove:horizontal {
                    height: 4px;
                    background: #555;
                    border-radius: 2px;
                }
                QSlider::handle:horizontal {
                    width: 14px;
                    height: 14px;
                    margin: -5px 0;
                    background: #eee;
                    border-radius: 7px;
                }
                QSlider::handle:horizontal:disabled { background: #555; }
            """)

            # ── Timers ────────────────────────────────────────────
            self.camera_timer = QtCore.QTimer()
            self.camera_timer.timeout.connect(self.camera.update_from_shared)
            self.camera_timer.start(50)

            self.sensor_timer = QtCore.QTimer()
            self.sensor_timer.timeout.connect(self.sensors.update_from_shared)
            self.sensor_timer.start(80)

    window = MainWindow()
    _screen, _rect = _control_screen_rect(app)
    _place_on_control_screen(window, _screen, _rect)
    window.show()
    # Deliberately not showMaximized(): "maximized" means "fill the monitor the WM
    # thinks this window is on", which is the decision that was sending the GUI to
    # a touch panel in the first place. An explicit geometry filling the control
    # monitor's work area looks the same and cannot be redirected.
    #
    # Re-assert it a few times rather than once: Mutter places the window when it
    # maps it, and on a busy start-up (five child processes, three of them opening
    # fullscreen windows of their own) a single 50 ms callback loses that race.
    for _delay in (50, 250, 750, 1500):
        QtCore.QTimer.singleShot(
            _delay, lambda: _place_on_control_screen(window, _screen, _rect))
    QtCore.QTimer.singleShot(2500, lambda: _warn_if_misplaced(window, _rect))
    app.exec_()
