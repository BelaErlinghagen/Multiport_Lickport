"""GUI entry point. Builds the main window (tab panel + camera/sensor panels)
from the widgets in gui/, and installs a guard so an uncaught exception in a
Qt slot can't crash a running experiment."""
import re
import sys
import traceback

from PyQt5 import QtWidgets, QtGui, QtCore

import shared_states
from gui import CameraWidget, SensorWidget, CleaningPage, ExperimentPage, ProtocolPage


def _install_exception_guard():
    """Prevent an uncaught exception in a Qt slot from crashing the process.

    PyQt5 calls Qt's qFatal() (SIGABRT) when a slot raises, which would kill
    a running experiment over something as minor as a bad RSpace API reply.
    This installs a sys.excepthook that logs and displays the error instead.
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
            # Deferred via a timer: this hook runs mid-unwind, and opening a
            # modal dialog's nested event loop at that point isn't safe.
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


def _net_work_area():
    """The desktop work area (_NET_WORKAREA) as a QRect, or None.

    Qt's QScreen.availableGeometry() reports the *full* screen on this rig:
    _NET_WORKAREA is published as one rect spanning the whole multi-monitor
    desktop, and Qt cannot split that per screen, so it gives up and omits the
    dock/top-bar reservation. Asking for a rect that covers reserved space gets
    silently refused by the window manager, so read the property directly.
    """
    import subprocess
    try:
        out = subprocess.run(["xprop", "-root", "_NET_WORKAREA"],
                             capture_output=True, text=True, timeout=2).stdout
        numbers = [int(n) for n in re.findall(r"-?\d+", out.split("=", 1)[1])]
    except Exception:
        return None
    if len(numbers) < 4:
        return None
    return QtCore.QRect(*numbers[:4])   # one rect per desktop; they match here


def _control_screen_rect(app):
    """Find which screen the main window should open on.

    Returns (QScreen or None, target QRect). Uses the hardcoded desktop
    position in shared_states.control_screen_geometry rather than a
    screens() index, since those indices can reshuffle on replug and this is
    the one window the experimenter must always be able to find.

    The rect is clipped to the desktop work area, so what we ask for is what
    the window manager is willing to grant — see _place_on_control_screen.
    """
    x, y, w, h = shared_states.control_screen_geometry
    target = QtCore.QRect(int(x), int(y), int(w), int(h))
    for screen in app.screens():
        if screen.geometry().contains(target.center()):
            rect = screen.availableGeometry()
            work = _net_work_area()
            if work is not None and work.intersects(rect):
                rect = rect.intersected(work)
            return screen, rect
    print(f"[GUI] no display found at {target.getRect()}; placing the GUI there "
          f"anyway. Check shared_states.control_screen_geometry against "
          f"`xrandr --listmonitors`.")
    return None, target


def _place_on_control_screen(window, screen, rect):
    """Move/resize `window` onto the control monitor.

    Called before the window is shown, and again on a retry schedule after it
    is mapped: Mutter can override the requested position and land a new window
    on whichever monitor currently has the pointer — often a touch panel.

    A retry only re-asserts the geometry while the window is on the *wrong*
    monitor, never to chase an exact rect. The window manager clamps a window
    to its work area and refuses anything larger, so re-sending a refused
    request achieves nothing but a re-layout — and CameraWidget (a
    QOpenGLWidget) repaints on every resize, which is what made start-up flicker.
    """
    window.winId()                          # realize native window
    handle = window.windowHandle()
    mapped = window.isVisible()

    if mapped and screen is not None and rect.contains(window.frameGeometry().center()):
        return                              # already on the control monitor

    if handle is not None and screen is not None and handle.screen() is not screen:
        handle.setScreen(screen)

    # setGeometry positions the client area, not the window frame, so the WM's
    # title-bar/border size has to be subtracted from the target rect. Frame
    # size is unknown (zero) before the window is mapped; the post-map calls
    # correct for that once it is.
    frame, geo = window.frameGeometry(), window.geometry()
    target = QtCore.QRect(
        rect.left()   + (geo.left() - frame.left()),
        rect.top()    + (geo.top()  - frame.top()),
        rect.width()  - (frame.width()  - geo.width()),
        rect.height() - (frame.height() - geo.height()),
    )
    if geo == target:
        return
    window.setGeometry(target)              # after setScreen: that can shift it back


def _warn_if_misplaced(window, rect):
    """Log a warning if the GUI window didn't land on the control monitor.

    A window manager silently relocates a window to another display if it
    doesn't fit the intended monitor's work area, rather than raising an
    error — so this prints the numbers needed to diagnose it.
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
    """A QWidget that always paints its own background, avoiding the Qt/X11
    flicker otherwise visible on tab switches and button clicks."""
    _BG = QtGui.QColor("#2b2b2b")

    def __init__(self, parent=None):
        super().__init__(parent)
        # Both flags are needed: they suppress two separate background-clear
        # steps (Qt's own pre-fill, and X11's clear-on-expose).
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)

    def paintEvent(self, event):
        QtGui.QPainter(self).fillRect(event.rect(), self._BG)


def run_gui(shared_image, sensor_array, shape, command_queue, data_sources=None):
    app = QtWidgets.QApplication([])
    _install_exception_guard()

    # ── Dark theme via Fusion style + QPalette ────────────────────────────────
    # Uses a QPalette rather than a global stylesheet background rule, since a
    # stylesheet forces autoFillBackground on every widget and reintroduces the
    # flicker _OpaqueWidget above works around.
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
            # No QWidget{background} rule here on purpose — see the dark-theme
            # comment above; a stylesheet background would reintroduce the flicker.
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

            self._page_experiment = page_experiment

        def closeEvent(self, event):
            """Stop a running recording before the event loop ends.

            Without this, closing the window mid-recording leaves the camera
            process to finalise the video during shutdown, where it is killed
            part-way through. Stopping here starts that flush while every
            process is still alive.
            """
            self.camera_timer.stop()
            self.sensor_timer.stop()
            try:
                self._page_experiment.shutdown()
            except Exception:
                traceback.print_exc()
            super().closeEvent(event)

    window = MainWindow()
    _screen, _rect = _control_screen_rect(app)
    _place_on_control_screen(window, _screen, _rect)
    window.show()
    # Not showMaximized(): that fills whatever monitor the WM thinks the window
    # is on, which is the placement bug this code works around. An explicit
    # geometry achieves the same look without that redirect.
    #
    # Placement is re-asserted several times because Mutter can lose the race
    # on a busy start-up (several child processes, some opening fullscreen
    # windows of their own). The 0 ms retry corrects the window's size before
    # the first frame is drawn, so that one is invisible; later retries are
    # no-ops once the geometry is already correct.
    for _delay in (0, 50, 250, 750, 1500):
        QtCore.QTimer.singleShot(
            _delay, lambda: _place_on_control_screen(window, _screen, _rect))
    QtCore.QTimer.singleShot(2500, lambda: _warn_if_misplaced(window, _rect))
    app.exec_()
