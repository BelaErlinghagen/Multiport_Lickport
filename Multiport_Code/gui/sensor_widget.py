from PyQt5 import QtWidgets, QtGui, QtCore


class SensorWidget(QtWidgets.QWidget):
    """4×4 heat-map of cumulative lick counts.

    Active sensors (currently licking) are highlighted with a blue accent border.
    """

    def __init__(self, sensor_array, num_sensors=16):
        super().__init__()
        self.sensor_array = sensor_array
        self.num_sensors = num_sensors
        self.counts = [0] * num_sensors
        self.active = set()

        self.setMinimumHeight(200)
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)

    def reset_counts(self):
        self.counts = [0] * self.num_sensors
        self.update()

    def update_from_shared(self):
        self.active = set()
        for i in range(self.num_sensors):
            if int(self.sensor_array[i]) == 1:
                self.counts[i] += 1
                self.active.add(i)
        self.update()

    def _heat_color(self, v, vmin, vmax):
        """Greyscale ramp from dark grey (vmin) to white (vmax)."""
        r = (v - vmin) / (vmax - vmin) if vmax != vmin else 0.0
        c1 = QtGui.QColor("#3a3a3a")
        c2 = QtGui.QColor("#ffffff")
        return QtGui.QColor(
            int(c1.red()   + r * (c2.red()   - c1.red())),
            int(c1.green() + r * (c2.green() - c1.green())),
            int(c1.blue()  + r * (c2.blue()  - c1.blue())),
        )

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        cols, rows = 4, 4
        w, h = self.width(), self.height()
        cw, ch = w / cols, h / rows

        vmin, vmax = min(self.counts), max(self.counts)

        for i in range(self.num_sensors):
            row = i // cols
            col = i % cols
            rect = QtCore.QRectF(col * cw + 4, row * ch + 4, cw - 8, ch - 8)
            color = self._heat_color(self.counts[i], vmin, vmax)

            painter.setBrush(color)
            if i in self.active:
                painter.setPen(QtGui.QPen(QtGui.QColor("#00aaff"), 3))
            else:
                painter.setPen(QtGui.QPen(QtGui.QColor("#222"), 1))
            painter.drawRoundedRect(rect, 8, 8)

            painter.setPen(QtGui.QColor("#000" if color.lightness() > 150 else "#fff"))
            painter.drawText(rect, QtCore.Qt.AlignCenter, f"S{i+1}\n{self.counts[i]}")
