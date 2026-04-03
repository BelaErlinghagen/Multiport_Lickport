# gui.py
import numpy as np
from PyQt5 import QtWidgets, QtGui, QtCore

class SensorWidget(QtWidgets.QWidget):
    def __init__(self, sensor_array, num_sensors=16):
        super().__init__()
        self.num_sensors = num_sensors
        self.counts = [0] * num_sensors
        self.sensor_array = sensor_array

        layout = QtWidgets.QGridLayout()
        layout.setSpacing(4)
        self.labels = []

        for i in range(num_sensors):
            label = QtWidgets.QLabel(f"S{i+1}: 0")
            label.setAlignment(QtCore.Qt.AlignCenter)
            label.setFixedHeight(32)
            label.setStyleSheet("""
                QLabel {
                    font-size: 14px;
                    color: white;
                    background-color: #444;
                    border: 1px solid #666;
                    border-radius: 4px;
                }
            """)
            self.labels.append(label)
            layout.addWidget(label, i // 4, i % 4)

        self.setLayout(layout)

    def update_sensors(self):
        active_list = [i+1 for i, val in enumerate(self.sensor_array) if val==1]
        active_set = set(active_list)

        for i in range(self.num_sensors):
            label = self.labels[i]
            if (i + 1) in active_set:
                self.counts[i] += 1
                label.setStyleSheet("""
                    QLabel {
                        font-size: 14px;
                        color: white;
                        background-color: #007BFF;
                        border: 1px solid #66B2FF;
                        border-radius: 4px;
                    }
                """)
            else:
                label.setStyleSheet("""
                    QLabel {
                        font-size: 14px;
                        color: white;
                        background-color: #444;
                        border: 1px solid #666;
                        border-radius: 4px;
                    }
                """)
            label.setText(f"S{i+1}: {self.counts[i]}")


class CameraWidget(QtWidgets.QLabel):
    def __init__(self, shared_image, shape):
        super().__init__()
        self.shared_image = shared_image
        self.shape = shape
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setText("Waiting for camera...")
        self.setStyleSheet("background-color: #2b2b2b; color: white;")

    def update_image(self):
        frame = np.array(self.shared_image[:], dtype=np.float32).reshape(self.shape)
        img = ((frame - frame.min()) / (frame.ptp() + 1e-5) * 255).astype(np.uint8)
        height, width = img.shape
        qimg = QtGui.QImage(img.data, width, height, width, QtGui.QImage.Format_Grayscale8)
        pixmap = QtGui.QPixmap.fromImage(qimg)
        self.setPixmap(pixmap.scaled(self.size(), QtCore.Qt.KeepAspectRatio))


def run_gui(shared_image, sensor_array, shape):
    app = QtWidgets.QApplication([])
    from PyQt5.QtCore import QTimer

    class MainWindow(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Tracking UI")

            self.camera_widget = CameraWidget(shared_image, shape)
            self.sensor_widget = SensorWidget(sensor_array)

            layout = QtWidgets.QVBoxLayout()
            layout.setContentsMargins(4, 4, 4, 4)
            layout.setSpacing(4)

            self.camera_widget.setMaximumHeight(500)
            self.sensor_widget.setMinimumHeight(220)

            layout.addWidget(self.camera_widget)
            layout.addWidget(self.sensor_widget)
            self.setLayout(layout)

            screen = QtWidgets.QApplication.primaryScreen().geometry()
            width = screen.width() // 2
            height = screen.height()
            self.setGeometry(width, 0, width, height)
            self.setFixedSize(width, height)
            self.setStyleSheet("QWidget {background-color: #2b2b2b;}")

            self.timer = QTimer()
            self.timer.timeout.connect(self.update_ui)
            self.timer.start(50)

        def update_ui(self):
            self.camera_widget.update_image()
            self.sensor_widget.update_sensors()

    window = MainWindow()
    window.show()
    app.exec_()