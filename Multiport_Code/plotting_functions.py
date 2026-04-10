from PyQt5 import QtWidgets, QtGui, QtCore


# =========================================================
# SENSOR WIDGET
# =========================================================
class SensorWidget(QtWidgets.QWidget):
    def __init__(self, sensor_array, num_sensors=16):
        super().__init__()

        self.sensor_array = sensor_array
        self.num_sensors = num_sensors
        self.counts = [0] * num_sensors

        self.setMinimumHeight(220)
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)

    def update_from_shared(self):
        for i in range(self.num_sensors):
            if int(self.sensor_array[i]) == 1:
                self.counts[i] += 1

        self.update()

    def _color(self, v, vmin, vmax):
        if vmax == vmin:
            r = 0.0
        else:
            r = (v - vmin) / (vmax - vmin)

        c1 = QtGui.QColor("#3a3a3a")
        c2 = QtGui.QColor("#ffffff")

        return QtGui.QColor(
            int(c1.red()   + r * (c2.red()   - c1.red())),
            int(c1.green() + r * (c2.green() - c1.green())),
            int(c1.blue()  + r * (c2.blue()  - c1.blue()))
        )

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        cols = 4
        rows = 4

        w = self.width()
        h = self.height()

        cw = w / cols
        ch = h / rows

        vmin = min(self.counts)
        vmax = max(self.counts)

        for i in range(self.num_sensors):
            r = i // cols
            c = i % cols

            rect = QtCore.QRectF(
                c * cw + 4,
                r * ch + 4,
                cw - 8,
                ch - 8
            )

            val = self.counts[i]
            color = self._color(val, vmin, vmax)

            painter.setPen(QtGui.QColor("#222"))
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 8, 8)

            painter.setPen(QtGui.QColor("#000" if color.lightness() > 150 else "#fff"))
            painter.drawText(
                rect,
                QtCore.Qt.AlignCenter,
                f"S{i+1}\n{val}"
            )


# =========================================================
# CAMERA WIDGET
# =========================================================
class CameraWidget(QtWidgets.QLabel):
    def __init__(self, frame_queue, shape):
        super().__init__()

        self.frame_queue = frame_queue
        self.shape = shape

        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setText("Waiting for camera...")

    def update_from_shared(self):
        frame = None

        while not self.frame_queue.empty():
            try:
                frame = self.frame_queue.get_nowait()
            except:
                break

        if frame is None:
            return

        h, w = self.shape

        qimg = QtGui.QImage(
            frame.data,
            w,
            h,
            w,
            QtGui.QImage.Format_Grayscale8
        )

        pixmap = QtGui.QPixmap.fromImage(qimg)

        self.setPixmap(
            pixmap.scaled(
                self.width(),
                self.height(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.FastTransformation
            )
        )


# =========================================================
# MAIN GUI
# =========================================================
def run_gui(shared_image, sensor_array, shape):

    app = QtWidgets.QApplication([])

    class MainWindow(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()

            self.setWindowTitle("MultiportGUI")

            # ---------------- LEFT SIDE (TABS) ----------------
            self.tabs = QtWidgets.QTabWidget()
            self.tabs.setDocumentMode(True)
            self.tabs.tabBar().setMouseTracking(False)

            self.tabs.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Expanding
            )

            # Page 1
            page_clean = QtWidgets.QWidget()
            l1 = QtWidgets.QVBoxLayout(page_clean)
            l1.addWidget(QtWidgets.QLabel("Serial Device Controls"))
            l1.addWidget(QtWidgets.QPushButton("Connect"))
            l1.addWidget(QtWidgets.QPushButton("Disconnect"))
            l1.addWidget(QtWidgets.QPushButton("Send Test"))
            l1.addStretch()

            # Page 2
            page_protocol = QtWidgets.QWidget()
            l2 = QtWidgets.QVBoxLayout(page_protocol)
            l2.addWidget(QtWidgets.QLineEdit())
            l2.addWidget(QtWidgets.QTextEdit())

            # Page 3
            page_experiment = QtWidgets.QWidget()
            l3 = QtWidgets.QVBoxLayout(page_experiment)
            l3.addWidget(QtWidgets.QComboBox())
            l3.addWidget(QtWidgets.QPushButton("Start"))
            l3.addWidget(QtWidgets.QPushButton("Stop"))

            self.tabs.addTab(page_clean, "Cleaning")
            self.tabs.addTab(page_protocol, "Protocol")
            self.tabs.addTab(page_experiment, "Experiment")

            # LEFT PANEL
            left = QtWidgets.QWidget()
            left_layout = QtWidgets.QVBoxLayout(left)
            left_layout.setContentsMargins(0, 0, 0, 0)
            left_layout.setSpacing(0)
            left_layout.addWidget(self.tabs)

            # ---------------- RIGHT SIDE ----------------
            self.camera = CameraWidget(shared_image, shape)
            self.sensors = SensorWidget(sensor_array)

            right = QtWidgets.QWidget()
            right_layout = QtWidgets.QVBoxLayout(right)
            right_layout.addWidget(self.camera)
            right_layout.addWidget(self.sensors)

            # ---------------- FIXED 60/40 SPLIT ----------------
            root = QtWidgets.QWidget()
            root_layout = QtWidgets.QHBoxLayout(root)

            root_layout.addWidget(left, 3)   # 60%
            root_layout.addWidget(right, 2)  # 40%

            root_layout.setContentsMargins(0, 0, 0, 0)
            root_layout.setSpacing(0)

            self.setCentralWidget(root)

            # ---------------- STYLE ----------------
            self.setStyleSheet("""
                QWidget { background:#2b2b2b; color:#eee; }

                QPushButton {
                    background:#3a3a3a;
                    border-radius:4px;
                    padding:6px;
                }

                QPushButton:hover {
                    background:#4a4a4a;
                }

                QTabBar::tab {
                    padding:8px;
                    margin:2px;
                    background:#3a3a3a;
                }

                QTabBar::tab:selected {
                    background:#4a4a4a;
                }
            """)

            # ---------------- TIMERS ----------------
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