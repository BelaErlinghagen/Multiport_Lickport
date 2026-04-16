# main_plotting.py — GUI entry point
# Imports widgets from the gui/ subpackage and builds the main window.
from PyQt5 import QtWidgets, QtGui, QtCore

from gui import CameraWidget, SensorWidget, CleaningPage, ExperimentPage


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

            page_clean = CleaningPage(command_queue)

            page_protocol = _OpaqueWidget()
            l2 = QtWidgets.QVBoxLayout(page_protocol)
            l2.addWidget(QtWidgets.QLineEdit())
            l2.addWidget(QtWidgets.QTextEdit())

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
            self.camera = CameraWidget(shared_image, shape)
            self.sensors = SensorWidget(sensor_array)

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
    window.show()
    QtCore.QTimer.singleShot(50, window.showMaximized)
    app.exec_()
