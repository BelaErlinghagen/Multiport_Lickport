from PyQt5 import QtWidgets, QtGui, QtCore


class CameraWidget(QtWidgets.QWidget):
    """Live camera preview drawn via paintEvent.

    Using QWidget + paintEvent (instead of QLabel.setPixmap) avoids the
    background-erase repaint cascade that caused left-panel flicker.
    """

    def __init__(self, frame_queue, shape):
        super().__init__()
        self._pixmap = None
        self.frame_queue = frame_queue
        self.shape = shape
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.setMinimumSize(200, 150)

    def update_from_shared(self):
        """Drain the frame queue and schedule a repaint if a new frame arrived."""
        frame = None
        while not self.frame_queue.empty():
            try:
                frame = self.frame_queue.get_nowait()
            except Exception:
                break

        if frame is None:
            return

        h, w = self.shape
        qimg = QtGui.QImage(
            frame.tobytes(), w, h, w, QtGui.QImage.Format_Grayscale8
        )
        self._pixmap = QtGui.QPixmap.fromImage(qimg)
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#111"))
        if self._pixmap:
            scaled = self._pixmap.scaled(
                self.width(), self.height(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            painter.setPen(QtGui.QColor("#aaa"))
            painter.drawText(
                self.rect(), QtCore.Qt.AlignCenter, "Waiting for camera…"
            )
