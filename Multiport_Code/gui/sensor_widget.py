"""SensorWidget: 4x4 heat-map of lick-sensor activity, showing cumulative
counts per port with the currently-active sensors highlighted."""
from PyQt5 import QtWidgets, QtGui, QtCore


class SensorWidget(QtWidgets.QWidget):
    """4×4 heat-map of cumulative lick counts.

    Active sensors (currently licking) are highlighted with a blue accent border.
    """

    _BG = QtGui.QColor("#2b2b2b")   # matches the tab pages and _OpaqueWidget

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
        # Collect the currently active sensors.
        new_active = set()
        for i in range(self.num_sensors):
            if int(self.sensor_array[i]) == 1:
                new_active.add(i)

        # Repaint only when something changed — the active set differs, or
        # counts are still accumulating — avoiding unconditional repaints
        # when the setup is idle.
        if new_active or new_active != self.active:
            for i in new_active:
                self.counts[i] += 1
            self.active = new_active
            self.update()
        else:
            self.active = new_active

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
        # WA_OpaquePaintEvent means this method must paint every pixel itself.
        # That matters even though the tiles below are inset/spaced: CameraWidget
        # (a QOpenGLWidget) puts the whole window into OpenGL compositing, where
        # every widget shares one framebuffer — anything left unpainted here would
        # show whatever another widget last rendered into that region.
        painter.fillRect(event.rect(), self._BG)
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
