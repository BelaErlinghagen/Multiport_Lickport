# gui_plotter.py
import sys
import os
os.environ["QT_QPA_PLATFORM"] = "xcb"
from shared_states import IMG_SIZE
import time
import numpy as np
from collections import deque
from multiprocessing import Queue, shared_memory

from PyQt5 import QtWidgets, QtCore, QtGui
import pyqtgraph as pg

# -----------------------------
# Global Config
# -----------------------------
pg.setConfigOption('background', 'k')
pg.setConfigOption('foreground', 'w')

NUM_SENSORS = 16
WINDOW_SIZE = 100

# -----------------------------
# Sensor Widget
# -----------------------------
class SensorWidget(pg.GraphicsLayoutWidget):
    def __init__(self, sensor_id):
        super().__init__()
        self.sensor_id = sensor_id

        # Smaller size → avoids overlap
        self.setFixedSize(120, 120)

        self.plot = self.addPlot(title=f"S{sensor_id}")

        # Clean styling
        self.plot.showGrid(x=False, y=False)
        self.plot.hideButtons()
        self.plot.getAxis('left').setTextPen('w')
        self.plot.getAxis('bottom').setTextPen('w')

        # Disable interaction
        self.plot.vb.setMouseEnabled(x=False, y=False)
        self.plot.setMenuEnabled(False)

        # Cumulative curve
        self.cum_curve = self.plot.plot(pen=pg.mkPen('r', width=2))
        self.cum_data = deque([0]*WINDOW_SIZE, maxlen=WINDOW_SIZE)
        self.cumulative = 0
        self.max_cum_display = 5
        self.plot.setYRange(0, self.max_cum_display)

        # LED indicator
        self.led = QtWidgets.QLabel()
        self.led.setFixedSize(12, 12)
        self.led.setStyleSheet("background-color: gray; border-radius: 6px;")

        proxy = QtWidgets.QGraphicsProxyWidget()
        proxy.setWidget(self.led)
        self.scene().addItem(proxy)
        proxy.setPos(5, 5)

    def update_data(self, triggered):
        if triggered:
            self.cumulative += 1
            self.led.setStyleSheet("background-color: red; border-radius: 6px;")
        else:
            self.led.setStyleSheet("background-color: green; border-radius: 6px;")

        self.cum_data.append(self.cumulative)

        # Update plot occasionally
        if triggered or (self.cumulative % 5 == 0):
            self.cum_curve.setData(np.array(self.cum_data)[::2])

        # Dynamic scaling
        if self.cumulative > self.max_cum_display:
            self.max_cum_display *= 2
            self.plot.setYRange(0, self.max_cum_display)

# -----------------------------
# Main Window
# -----------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, data_queue: Queue, shared_image: np.ndarray):
        super().__init__()
        self.data_queue = data_queue
        self.shared_image = shared_image
        self.setWindowTitle("Multiport Live View GUI")

        # --- Graphics View ---
        self.view = QtWidgets.QGraphicsView()
        self.view.setStyleSheet("background-color: black; border: none;")
        self.scene = QtWidgets.QGraphicsScene()
        self.scene.setBackgroundBrush(QtGui.QBrush(QtGui.QColor(0, 0, 0)))
        self.view.setScene(self.scene)
        self.setCentralWidget(self.view)
        self.setStyleSheet("background-color: black;")

        # --- Center Image ---
        self.image_item = pg.ImageItem()
        cmap = pg.colormap.get("CET-C5")
        self.image_item.setLookupTable(cmap.getLookupTable())
        self.image_item.setLevels([0, 1])

        self.image_view = pg.GraphicsLayoutWidget()
        vb = self.image_view.addViewBox()
        vb.addItem(self.image_item)
        vb.setAspectLocked(True)
        vb.setMouseEnabled(x=False, y=False)
        vb.setMenuEnabled(False)
        self.image_proxy = self.scene.addWidget(self.image_view)

        # --- Sensors ---
        self.sensor_widgets = {}
        self.sensor_proxies = {}
        for i in range(NUM_SENSORS):
            sid = i + 1
            w = SensorWidget(sid)
            proxy = self.scene.addWidget(w)
            self.sensor_widgets[sid] = w
            self.sensor_proxies[sid] = proxy

        # --- FPS Label ---
        self.fps_label = QtWidgets.QLabel("FPS: 0")
        self.fps_label.setStyleSheet("color: white; background-color: black; font-size: 14px;")
        self.fps_proxy = self.scene.addWidget(self.fps_label)

        self.last_time = time.time()
        self.frame_count = 0

        # --- Screen / window ---
        screen = QtWidgets.QApplication.primaryScreen().geometry()
        half_width = screen.width() // 2
        full_height = screen.height()
        self.setGeometry(half_width, 0, half_width, full_height)
        self.setFixedSize(half_width, full_height)

        self.arrange_items()

        # --- Timer to poll data queue ---
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.poll_queue_and_image)
        self.timer.start(20)  # check every 20 ms (~50 FPS)

    def arrange_items(self):
        w = self.width()
        h = self.height()
        center_x = w / 2
        center_y = h / 2
        radius = min(w, h) * 0.45
        img_size = min(w, h) * 0.85

        # Center image
        self.image_proxy.setPos(center_x - img_size/2, center_y - img_size/2)
        self.image_proxy.resize(img_size, img_size)

        # Sensor positions
        for i, (sid, proxy) in enumerate(self.sensor_proxies.items()):
            angle = 2 * np.pi * i / NUM_SENSORS
            x = center_x + radius * np.cos(angle)
            y = center_y + radius * np.sin(angle)
            proxy.setPos(x - 60, y - 45)

        self.fps_proxy.setPos(10, 10)

    def resizeEvent(self, event):
        self.arrange_items()
        super().resizeEvent(event)

    def poll_queue_and_image(self):
        # Sensor data
        active = []
        while not self.data_queue.empty():
            try:
                active = self.data_queue.get_nowait()
            except:
                break

        # Image from shared memory
        image = np.array(self.shared_image, copy=True)  # copy to local array for PyQtGraph

        # Update GUI
        self.update_all(image, active)

    def update_all(self, image, active):
        # Update image
        self.image_item.setImage(image, autoLevels=False)

        # Update sensors
        for sid, widget in self.sensor_widgets.items():
            widget.update_data(sid in active)

        # FPS calculation
        self.frame_count += 1
        now = time.time()
        if now - self.last_time >= 1.0:
            fps = self.frame_count / (now - self.last_time)
            self.fps_label.setText(f"FPS: {fps:.1f}")
            self.frame_count = 0
            self.last_time = now

# -----------------------------
# Run plotter process
# -----------------------------
def run_plotter(queue: Queue, shared_image_name):
    import sys
    from PyQt5 import QtWidgets
    app = QtWidgets.QApplication(sys.argv)
    existing_shm = shared_memory.SharedMemory(name=shared_image_name)
    shared_image = np.ndarray((IMG_SIZE, IMG_SIZE), dtype=np.float32, buffer=existing_shm.buf)
    win = MainWindow(queue, shared_image)
    win.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    from multiprocessing import Queue
    import numpy as np
    from multiprocessing import shared_memory

    shm = shared_memory.SharedMemory(create=True, size=IMG_SIZE*IMG_SIZE*4)
    shape = (IMG_SIZE,IMG_SIZE)
    arr = np.ndarray(shape, dtype=np.float32, buffer=shm.buf)

    q = Queue()
    run_plotter(q, shm.name)
    shm.close()
    shm.unlink() 