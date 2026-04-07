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
    def __init__(self, frame_queue, shape):
        super().__init__()
        self.frame_queue = frame_queue
        self.shape = shape
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setText("Waiting for camera...")
        self.setStyleSheet("background-color: #2b2b2b; color: white;")

    def update_image(self):
        frame = None
        while not self.frame_queue.empty():
            try:
                frame = self.frame_queue.get_nowait()
            except:
                break

        if frame is None:
            return
        #print(np.mean(frame))
        height, width = self.shape
        qimg = QtGui.QImage(frame.data, width, height, width, QtGui.QImage.Format_Grayscale8)
        pixmap = QtGui.QPixmap.fromImage(qimg)

        scaled = pixmap.scaled(
            self.width(),
            self.height(),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.FastTransformation
        )
        self.setPixmap(scaled)


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

            self.camera_widget.setMinimumHeight(500)
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