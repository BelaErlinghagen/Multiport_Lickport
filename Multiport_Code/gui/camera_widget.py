from PyQt5 import QtWidgets, QtGui, QtCore


class CameraWidget(QtWidgets.QOpenGLWidget):
    """Live camera preview rendered via QOpenGLWidget.

    Inheriting from QOpenGLWidget (rather than QWidget) has one critical
    side-effect: Qt switches the *entire host window* into OpenGL compositing
    mode.  In that mode:
      1. All regular widgets in the window are painted into a shared off-screen
         framebuffer object (FBO).
      2. This widget's own FBO is composited on top.
      3. The combined result is presented to the screen in a single
         eglSwapBuffers call — atomic and vsync-synchronized.

    Under XWayland/Wayland (the user's GNOME setup) the xcb-based software
    path pushes dirty regions to the X server immediately and unsynchronised,
    which lets the Wayland compositor see intermediate window states and
    produce visible flicker.  The GL path eliminates this because nothing
    is shown until the swap is complete.
    """

    def __init__(self, frame_queue, shape):
        super().__init__()
        self._pixmap = None
        self.frame_queue = frame_queue
        self.shape = shape
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

    def paintGL(self):
        """Paint the current frame using QPainter on the OpenGL surface.

        QPainter works on QOpenGLWidget via Qt's OpenGL 2D paint engine.
        painter.end() must be called explicitly before returning.
        """
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
        painter.end()
