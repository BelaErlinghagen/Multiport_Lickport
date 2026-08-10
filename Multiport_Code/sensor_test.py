"""Standalone two-camera + sensor-15 test recorder, used to validate the
Multiport setup before a real run. The GUI has two tabs:
  - "Record"  : live downsampled feeds from both Basler cameras plus a live
                0/1 trace of lick-sensor 15, recording all three streams
                synchronized (one 4K frame <-> one 1440 frame <-> one sensor sample).
  - "Analyze" : load a saved recording, scrub frames + sensor with a slider,
                pick a square ROI per camera (mouse-wheel zoom), and generate
                a plot of the ~10 timepoints around the selected frame.

Cameras run at ~30 fps (auto-exposure capped to one frame period, USB
bandwidth cap lifted). To keep recordings small, the 4K stream is cropped to
its arena ROI, both streams are downsampled, and each frame is written live
as a compressed JPEG plus a CSV row. On Stop, both videos are encoded from
the saved JPEGs via ffmpeg/H.264 at the exact average fps measured from the
frame timestamps, so playback is real-time. Encode settings are in the
"Recording size controls" block below.

Reuses existing code as-is: serial_controls.sensor_process fills the shared
sensor_array, and gui.CameraWidget provides the live downsampled preview.
Everything else (two-camera acquisition, per-frame JPEG + video saving, the
0/1 time-trace widget) is new and lives here.

Process layout (spawn):
  main process        Qt GUI (two CameraWidgets + sensor trace + record panel)
  sensor_process       fills sensor_array from the Arduinos (existing function)
  acquisition_process  grabs both cameras, feeds the GUI, and (when recording)
                       hands cropped frames to an in-process writer thread.

Output files (in the chosen folder), base = {YYYYmmdd_HHMMSS}_testing_{label}:
  {base}_daA3840_frames/000000.jpg …   per-frame JPEG (cropped+downsampled 4K)
  {base}_daA1440_frames/000000.jpg …   per-frame JPEG (downsampled 1440)
  {base}_daA3840.mp4, {base}_daA1440.mp4   (H.264, ffmpeg; cv2 mp4v fallback)
  {base}_sensor15.csv   (columns: frame_index, timestamp, sensor15)
"""

import csv
import glob
import os
import queue
import signal
import subprocess
import threading
import time
from datetime import datetime

import cv2
import numpy as np
from pypylon import pylon

import shared_states
from serial_controls import sensor_process

# ── Configuration ─────────────────────────────────────────────────────────────
SERIAL_4K   = str(shared_states.camera_serial)   # daA3840-45uc (3840×2160)
SERIAL_1440 = "40458776"                          # daA1440-220um (1440×1080)
# Display sizes (h, w) chosen to match each sensor's aspect ratio so the live
# feed is not stretched. CameraWidget requires frames to be *exactly* this shape.
DISP_4K   = (270, 480)   # 16:9
DISP_1440 = (360, 480)   # 4:3
TARGET_FPS = 30          # requested capture rate; caps exposure to 1/TARGET_FPS s
NOMINAL_FPS = 30         # fallback video fps if the real rate can't be measured
WRITE_Q_MAX = 64         # writer-thread backpressure cap (preserves sync, caps RAM)
SENSOR_INDEX = 14        # sensor 15 → sensor_array[14]

# ── Recording size controls ────────────────────────────────────────────────────
# Saving raw full-resolution frames would make recordings enormous. Instead
# the 4K stream is cropped to its arena ROI (same as camera_controls.py), both
# streams are downsampled, each frame is saved as JPEG, and video is encoded
# with H.264. Every knob below trades quality for file size.
CROP_4K        = (100, 2100, 1000, 3000)  # (y0, y1, x0, x1) — matches camera_controls.py arena ROI → 2000×2000
SAVE_4K_SIZE   = (1000, 1000)             # (w, h) saved size of the 4K crop
SAVE_1440_SIZE = (960, 720)               # (w, h) saved size of the 1440 (keeps 4:3)
JPEG_QUALITY   = 55                       # per-frame .jpg quality (lower → smaller frames folder)
VIDEO_CODEC    = "libx264"                # set "h264_nvenc" if a GPU encoder is present
VIDEO_BITRATE  = "4M"                     # H.264 cap → ≲300 MB/10 min per stream
FFMPEG_BIN     = "ffmpeg"                 # encoder binary (provided by the pixi env)


# ── Camera helpers ─────────────────────────────────────────────────────────────
def open_camera(serial):
    """Open the Basler camera with the given serial as Mono8.

    Selects by serial rather than CreateFirstDevice, since two cameras are
    connected. Raises with a list of available cameras if the serial isn't found.
    """
    tl = pylon.TlFactory.GetInstance()
    dev = next((d for d in tl.EnumerateDevices()
                if d.GetSerialNumber() == str(serial)), None)
    if dev is None:
        available = [f"{d.GetModelName()} ({d.GetSerialNumber()})"
                     for d in tl.EnumerateDevices()]
        raise RuntimeError(
            f"Camera with serial {serial} not found. Available: {available}"
        )
    cam = pylon.InstantCamera(tl.CreateDevice(dev))
    cam.Open()
    # The 4K cam defaults to BayerRG8 (colour mosaic); force Mono8 so the whole
    # grayscale pipeline (CameraWidget + grayscale video) gets a clean image.
    try:
        cam.PixelFormat.SetValue("Mono8")
    except Exception as exc:
        print(f"[Cam {serial}] could not set Mono8: {exc}")
    _configure_framerate(cam, TARGET_FPS)
    return cam


def _configure_framerate(cam, target_fps):
    """Configure a Basler camera to sustain target_fps.

    By default, auto-exposure can drive exposure time up in dim scenes and
    cap the frame rate well below target, and the USB bandwidth limit further
    throttles the 4K stream. This lifts the bandwidth cap and bounds
    auto-exposure to one frame period, letting auto-gain compensate for the
    shorter exposure. Each node is set defensively, since names and
    availability vary across camera models/firmware.
    """
    def _set(name, val):
        try:
            getattr(cam, name).SetValue(val)
            return True
        except Exception:
            return False

    # 1) lift the per-camera USB bandwidth cap (default throttles the 4K stream).
    if not _set("DeviceLinkThroughputLimitMode", "Off"):
        try:
            need = int(cam.Width.GetValue() * cam.Height.GetValue() * target_fps * 1.2)
            _set("DeviceLinkThroughputLimit", need)
        except Exception:
            pass

    # 2) keep auto-exposure/gain but cap exposure to one frame period so the
    #    auto loop can never drop the frame rate below target_fps.
    frame_period_us = 1_000_000.0 / target_fps
    try:
        expo_min = cam.ExposureTime.GetMin()
    except Exception:
        expo_min = 1.0
    expo_cap = max(expo_min, frame_period_us - 1000.0)   # small readout margin
    _set("ExposureAuto", "Continuous")
    _set("GainAuto", "Continuous")
    if not _set("AutoExposureTimeUpperLimit", expo_cap):
        # older firmware without the cap node: fix the exposure instead
        _set("ExposureAuto", "Off")
        _set("ExposureTime", expo_cap)

    # 3) request the target frame rate explicitly.
    _set("AcquisitionFrameRateEnable", True)
    _set("AcquisitionFrameRate", float(target_fps))

    try:
        print(f"[Cam {cam.GetDeviceInfo().GetSerialNumber()}] "
              f"ResultingFrameRate = {cam.ResultingFrameRate.GetValue():.1f} fps "
              f"(exposure ≤ {expo_cap/1000:.0f} ms)")
    except Exception:
        pass


def _downsample(frame, target_h, target_w):
    """Resize a grayscale frame to exactly (target_h, target_w), C-contiguous uint8.

    Returning the exact target shape matters: CameraWidget builds a QImage sized
    to its `shape`, and a smaller buffer makes QImage read past the end → noise.
    """
    ds = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(ds, dtype=np.uint8)


def _drop_put(q, item):
    """Replace any stale item on a maxsize-bounded display queue with the newest."""
    if q.full():
        try:
            q.get_nowait()
        except Exception:
            pass
    try:
        q.put_nowait(item)
    except Exception:
        pass


# ── Video encoding (from the saved .jpg sequence, on Stop) ──────────────────────
def _encode_one_ffmpeg(folder, out_path, fps):
    """Encode a folder of NNNNNN.jpg into an H.264 mp4 via ffmpeg.

    Returns True on success, False if ffmpeg is unavailable or fails — the
    caller then falls back to the cv2 encoder so a video is still produced.
    """
    cmd = [
        FFMPEG_BIN, "-y", "-framerate", f"{fps:.4f}",
        "-start_number", "0", "-i", os.path.join(folder, "%06d.jpg"),
        "-c:v", VIDEO_CODEC, "-b:v", VIDEO_BITRATE,
        "-maxrate", VIDEO_BITRATE, "-bufsize", "8M",
        "-pix_fmt", "yuv420p", out_path,
    ]
    try:
        subprocess.run(cmd, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        print(f"[Rec] ffmpeg encode failed ({exc}); falling back to cv2 mp4v.")
        return False


def _encode_one_cv2(folder, out_path, fps):
    """Fallback: read the .jpg sequence and write an mp4v video with cv2 (no ffmpeg)."""
    paths = sorted(glob.glob(os.path.join(folder, "*.jpg")))
    if not paths:
        return
    h, w = cv2.imread(paths[0], cv2.IMREAD_GRAYSCALE).shape[:2]
    vw = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"),
                         fps, (w, h), False)
    for p in paths:
        vw.write(cv2.imread(p, cv2.IMREAD_GRAYSCALE))
    vw.release()


# ── Writer thread (runs inside the acquisition process) ─────────────────────────
def _writer_loop(write_q):
    """Consume synchronized records and write them to disk.

    Message protocol on write_q:
      ("open", out_dir, label)                          → begin a recording
      ("frame", idx, t, frame_4k, frame_1440, sensor15) → one synchronized record
      ("close",)                                        → finalize current recording
      None                                              → exit the thread

    Only the per-frame JPEG and CSV are written in realtime, so video encoding
    never throttles capture. On "close", both videos are encoded from the
    saved JPEGs at the exact average fps measured from the frame timestamps,
    so playback is real-time. Only timestamps and file paths are buffered, so
    memory use stays negligible.
    """
    folder4k = folder1440 = None
    csvfile = csvw = None
    video_cfg = None
    recorded = []   # [(timestamp, path_4k, path_1440)] for this recording

    def _encode_videos():
        # ffmpeg/H.264 is tried first; cv2 mp4v is the fallback if ffmpeg is
        # unavailable.
        if not recorded or video_cfg is None:
            return
        out_dir, base, fdir4k, fdir1440 = video_cfg
        span = recorded[-1][0] - recorded[0][0]
        fps = (float(min(240.0, max(1.0, (len(recorded) - 1) / span)))
               if span > 0 else float(NOMINAL_FPS))
        for name, folder in (("daA3840", fdir4k), ("daA1440", fdir1440)):
            out = os.path.join(out_dir, f"{base}_{name}.mp4")
            if not _encode_one_ffmpeg(folder, out, fps):
                _encode_one_cv2(folder, out, fps)
        print(f"[Rec] encoded {len(recorded)} frames @ {fps:.2f} fps")

    def _finalize():
        nonlocal csvfile, csvw
        if csvfile is not None:
            csvfile.flush(); csvfile.close(); csvfile = None; csvw = None
        _encode_videos()

    while True:
        item = write_q.get()
        if item is None:
            _finalize()
            break

        tag = item[0]
        if tag == "open":
            _, out_dir, label = item
            base = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_testing_{label}"
            folder4k   = os.path.join(out_dir, f"{base}_daA3840_frames")
            folder1440 = os.path.join(out_dir, f"{base}_daA1440_frames")
            os.makedirs(folder4k, exist_ok=True)
            os.makedirs(folder1440, exist_ok=True)
            csvfile = open(os.path.join(out_dir, f"{base}_sensor15.csv"),
                           "w", newline="")
            csvw = csv.writer(csvfile)
            csvw.writerow(["frame_index", "timestamp", "sensor15"])
            recorded = []
            video_cfg = (out_dir, base, folder4k, folder1440)
            print(f"[Rec] started: {base}")

        elif tag == "frame":
            _, idx, t, f4k, f1440, s15 = item
            # Downsample then JPEG-encode each frame to keep the frames folder
            # small while staying frame-accurate for the Analyze tab.
            f4 = cv2.resize(f4k,   SAVE_4K_SIZE,   interpolation=cv2.INTER_AREA)
            f1 = cv2.resize(f1440, SAVE_1440_SIZE, interpolation=cv2.INTER_AREA)
            p4k   = os.path.join(folder4k,   f"{idx:06d}.jpg")
            p1440 = os.path.join(folder1440, f"{idx:06d}.jpg")
            cv2.imwrite(p4k,   f4, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            cv2.imwrite(p1440, f1, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if csvw is not None:
                csvw.writerow([idx, f"{t:.6f}", s15])
            recorded.append((t, p4k, p1440))

        elif tag == "close":
            if csvfile is not None:
                csvfile.flush(); csvfile.close(); csvfile = None; csvw = None
            print("[Rec] stopped — encoding video…")
            _encode_videos()
            recorded = []


# ── Acquisition process ─────────────────────────────────────────────────────────
def acquisition_process(disp_q_4k, disp_q_1440, sensor_array, ctrl_queue,
                        running_flag, disp_shape_4k, disp_shape_1440):
    """Grab both cameras in lockstep, feed the GUI, and record on request.

    The 4K camera (slower max fps) is retrieved first and sets the loop rate;
    the 1440 camera is retrieved right after, so each iteration yields one
    synchronized (4K, 1440, sensor) triplet. Full-res frames are copied only
    while recording and handed to the writer thread — the GUI only ever sees
    small downsampled frames.
    """
    disp_q_4k.cancel_join_thread()
    disp_q_1440.cancel_join_thread()
    ctrl_queue.cancel_join_thread()

    def _term(_sig, _frame):
        running_flag.value = False
    signal.signal(signal.SIGTERM, _term)

    cam4k   = open_camera(SERIAL_4K)
    cam1440 = open_camera(SERIAL_1440)
    cam4k.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
    cam1440.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

    write_q = queue.Queue(maxsize=WRITE_Q_MAX)
    writer_thread = threading.Thread(target=_writer_loop, args=(write_q,),
                                     daemon=True)
    writer_thread.start()

    recording = False
    frame_index = 0
    print("[Acq] cameras grabbing.")
    try:
        while running_flag.value:
            # ── handle record start/stop ──────────────────────────────────────
            try:
                while True:
                    msg = ctrl_queue.get_nowait()
                    if msg[0] == "start":
                        _, out_dir, label = msg
                        write_q.put(("open", out_dir, label))
                        frame_index = 0
                        recording = True
                    elif msg[0] == "stop" and recording:
                        recording = False
                        write_q.put(("close",))
            except queue.Empty:
                pass

            # ── grab 4K (drives the loop rate) ────────────────────────────────
            res4k = cam4k.RetrieveResult(5000, pylon.TimeoutHandling_Return)
            if not res4k or not res4k.GrabSucceeded():
                if res4k:
                    res4k.Release()
                continue
            arr4k = res4k.Array
            disp4k = _downsample(arr4k, disp_shape_4k[0], disp_shape_4k[1])
            # Record only the arena focus ROI (same crop as camera_controls.py),
            # not the full frame, so less data crosses to the writer and the
            # saved frames are smaller.
            if recording:
                y0, y1, x0, x1 = CROP_4K
                rec4k = arr4k[y0:y1, x0:x1].copy()   # copy before Release
            else:
                rec4k = None
            res4k.Release()

            # ── grab 1440 (latest) ────────────────────────────────────────────
            res1440 = cam1440.RetrieveResult(5000, pylon.TimeoutHandling_Return)
            if not res1440 or not res1440.GrabSucceeded():
                if res1440:
                    res1440.Release()
                _drop_put(disp_q_4k, disp4k)   # still refresh the 4K feed
                continue
            arr1440 = res1440.Array
            disp1440 = _downsample(arr1440, disp_shape_1440[0], disp_shape_1440[1])
            rec1440 = arr1440.copy() if recording else None
            res1440.Release()

            # ── one synchronized sample ───────────────────────────────────────
            s15 = int(sensor_array[SENSOR_INDEX])
            t = time.time()

            _drop_put(disp_q_4k, disp4k)
            _drop_put(disp_q_1440, disp1440)

            if recording:
                # Blocks when the writer is behind (disk-bound) — this is the
                # backpressure that keeps the three streams aligned.
                write_q.put(("frame", frame_index, t, rec4k, rec1440, s15))
                frame_index += 1
    finally:
        if recording:
            write_q.put(("close",))
        write_q.put(None)
        # Generous timeout: on shutdown the writer may still be batch-encoding the
        # videos from the saved JPEG frames; don't truncate them.
        writer_thread.join(timeout=300)
        for cam in (cam4k, cam1440):
            try:
                cam.StopGrabbing(); cam.Close()
            except Exception:
                pass
        print("[Acq] stopped.")


# ── GUI ─────────────────────────────────────────────────────────────────────────
def run_gui(disp_q_4k, disp_q_1440, sensor_array, ctrl_queue, running_flag,
            disp_shape_4k, disp_shape_1440):
    """Build and run the Qt GUI. Qt-only imports are local so the camera/sensor
    subprocesses never import PyQt/pyqtgraph."""
    from PyQt5 import QtWidgets, QtGui, QtCore
    import pyqtgraph as pg
    from collections import deque
    from gui import CameraWidget

    pg.setConfigOption("background", "#2b2b2b")
    pg.setConfigOption("foreground", "#dddddd")
    pg.setConfigOption("imageAxisOrder", "row-major")  # ImageItem/ROI in (row, col)

    app = QtWidgets.QApplication([])
    app.setStyle("Fusion")
    pal = QtGui.QPalette()
    for role, hexc in [
        (QtGui.QPalette.Window,     "#2b2b2b"),
        (QtGui.QPalette.WindowText, "#eeeeee"),
        (QtGui.QPalette.Base,       "#2b2b2b"),
        (QtGui.QPalette.Text,       "#eeeeee"),
        (QtGui.QPalette.Button,     "#3a3a3a"),
        (QtGui.QPalette.ButtonText, "#eeeeee"),
        (QtGui.QPalette.Highlight,  "#005a99"),
    ]:
        pal.setColor(role, QtGui.QColor(hexc))
    app.setPalette(pal)

    class SensorTraceWidget(pg.PlotWidget):
        """Moving-window 0/1 trace of one sensor (x = time, y ∈ {0, 1})."""

        def __init__(self, sensor_array, index=SENSOR_INDEX, window_s=10.0):
            super().__init__()
            self.sensor_array = sensor_array
            self.index = index
            self.window_s = window_s
            self.t0 = time.time()
            self.xs = deque(maxlen=5000)
            self.ys = deque(maxlen=5000)
            self.setYRange(-0.1, 1.1)
            self.setLabel("left", f"Sensor {index + 1}")
            self.setLabel("bottom", "Time (s)")
            self.showGrid(x=True, y=True, alpha=0.3)
            self.getAxis("left").setTicks([[(0, "0"), (1, "1")]])
            self.curve = self.plot(pen=pg.mkPen("#00e676", width=2))

        def update_from_shared(self):
            now = time.time() - self.t0
            self.xs.append(now)
            self.ys.append(int(self.sensor_array[self.index]))
            self.curve.setData(list(self.xs), list(self.ys))
            self.setXRange(max(0.0, now - self.window_s), now, padding=0)

    class AnalysisTab(QtWidgets.QWidget):
        """Browse a saved recording: scrub frames + sensor with a slider, pick a
        square ROI per camera (zoom with the mouse wheel), and generate a
        comprehensive plot of the ~10 timepoints around the chosen frame."""

        SPAN = 20     # total frames covered by a generated plot (centered on idx)
        STEP = 2      # plot every Nth frame → SPAN/STEP image columns

        def __init__(self):
            super().__init__()
            self.found = {}          # base -> (csv_path, folder_4k, folder_1440)
            self.frames4k = []
            self.frames1440 = []
            self.timestamps = []
            self.sensor = []
            self.n = 0
            self.base = ""
            self._first_load = False
            self._dialogs = []       # keep generated plot dialogs alive

            # ── folder / recording pickers ────────────────────────────────────
            self.dir_edit = QtWidgets.QLineEdit()
            self.dir_edit.setReadOnly(True)
            self.dir_edit.setPlaceholderText("Recording folder…")
            browse = QtWidgets.QPushButton("Browse…")
            browse.clicked.connect(self._browse)
            self.rec_combo = QtWidgets.QComboBox()
            self.rec_combo.currentIndexChanged.connect(self._load_selected)
            top = QtWidgets.QHBoxLayout()
            top.addWidget(QtWidgets.QLabel("Folder:"))
            top.addWidget(self.dir_edit, 1)
            top.addWidget(browse)
            top.addWidget(QtWidgets.QLabel("Recording:"))
            top.addWidget(self.rec_combo, 1)

            # ── camera views with a square ROI ────────────────────────────────
            self.view4k, self.vb4k, self.img4k, self.roi4k = self._make_view()
            self.view1440, self.vb1440, self.img1440, self.roi1440 = self._make_view()
            cams = QtWidgets.QHBoxLayout()
            cams.addWidget(self._titled(
                "daA3840-45uc (4K) — wheel = zoom, drag ROI (square)", self.view4k))
            cams.addWidget(self._titled(
                "daA1440-220um — wheel = zoom, drag ROI (square)", self.view1440))

            # ── slider + info + generate ──────────────────────────────────────
            self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            self.slider.setEnabled(False)
            self.slider.valueChanged.connect(self._show_frame)
            self.info = QtWidgets.QLabel("No recording loaded.")
            self.gen_btn = QtWidgets.QPushButton("Generate plots")
            self.gen_btn.setEnabled(False)
            self.gen_btn.clicked.connect(self._generate)
            bottom = QtWidgets.QHBoxLayout()
            bottom.addWidget(self.slider, 1)
            bottom.addWidget(self.info)
            bottom.addWidget(self.gen_btn)

            root = QtWidgets.QVBoxLayout(self)
            root.addLayout(top)
            root.addLayout(cams, 1)
            root.addLayout(bottom)

        # ── construction helpers ──────────────────────────────────────────────
        def _titled(self, title, widget):
            box = QtWidgets.QVBoxLayout()
            lab = QtWidgets.QLabel(title)
            lab.setAlignment(QtCore.Qt.AlignCenter)
            box.addWidget(lab)
            box.addWidget(widget)
            cont = QtWidgets.QWidget()
            cont.setLayout(box)
            return cont

        def _make_view(self):
            glw = pg.GraphicsLayoutWidget()
            vb = glw.addViewBox()
            vb.setAspectLocked(True)
            vb.invertY(True)                 # image origin top-left
            img = pg.ImageItem()
            vb.addItem(img)
            roi = pg.RectROI([10, 10], [100, 100],
                             pen=pg.mkPen("#00e676", width=2))
            vb.addItem(roi)
            guard = {"busy": False}
            def _square(*_):                 # keep the ROI square on every change
                if guard["busy"]:
                    return
                guard["busy"] = True
                s = float(roi.size()[0])
                roi.setSize([s, s])
                guard["busy"] = False
            roi.sigRegionChanged.connect(_square)
            return glw, vb, img, roi

        # ── loading ───────────────────────────────────────────────────────────
        def _browse(self):
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select recording folder")
            if not d:
                return
            self.dir_edit.setText(d)
            self.found = {}
            for csvp in sorted(glob.glob(os.path.join(d, "*_sensor15.csv"))):
                base = os.path.basename(csvp)[:-len("_sensor15.csv")]
                f4 = os.path.join(d, f"{base}_daA3840_frames")
                f1 = os.path.join(d, f"{base}_daA1440_frames")
                if os.path.isdir(f4) and os.path.isdir(f1):
                    self.found[base] = (csvp, f4, f1)
            self.rec_combo.blockSignals(True)
            self.rec_combo.clear()
            self.rec_combo.addItems(list(self.found.keys()))
            self.rec_combo.blockSignals(False)
            if self.found:
                self.rec_combo.setCurrentIndex(0)
                self._load_selected()
            else:
                QtWidgets.QMessageBox.warning(
                    self, "No recordings",
                    "No '*_sensor15.csv' with matching frame folders here.")
                self.info.setText("No recording loaded.")
                self.slider.setEnabled(False)
                self.gen_btn.setEnabled(False)

        def _load_selected(self, *_):
            base = self.rec_combo.currentText()
            if not base or base not in self.found:
                return
            csvp, f4, f1 = self.found[base]
            self.base = base
            self.frames4k = sorted(glob.glob(os.path.join(f4, "*.jpg")))
            self.frames1440 = sorted(glob.glob(os.path.join(f1, "*.jpg")))
            ts, sv = [], []
            with open(csvp) as fh:
                reader = csv.reader(fh)
                next(reader, None)
                for row in reader:
                    if len(row) >= 3:
                        ts.append(float(row[1]))
                        sv.append(int(row[2]))
            self.timestamps, self.sensor = ts, sv
            self.n = min(len(self.frames4k), len(self.frames1440), len(sv))
            if self.n == 0:
                self.info.setText(f"{base}: no frames found.")
                self.slider.setEnabled(False)
                self.gen_btn.setEnabled(False)
                return
            self.slider.setEnabled(True)
            self.slider.setMinimum(0)
            self.slider.setMaximum(self.n - 1)
            self.gen_btn.setEnabled(True)
            self._first_load = True
            self.slider.blockSignals(True)
            self.slider.setValue(0)
            self.slider.blockSignals(False)
            self._show_frame(0)

        def _init_roi(self, roi, shape):
            h, w = shape[:2]
            s = min(h, w) // 2
            roi.setSize([s, s])
            roi.setPos([(w - s) // 2, (h - s) // 2])

        def _show_frame(self, i):
            if self.n == 0 or i < 0 or i >= self.n:
                return
            f4 = cv2.imread(self.frames4k[i], cv2.IMREAD_GRAYSCALE)
            f1 = cv2.imread(self.frames1440[i], cv2.IMREAD_GRAYSCALE)
            self.img4k.setImage(f4, autoLevels=False, levels=(0, 255))
            self.img1440.setImage(f1, autoLevels=False, levels=(0, 255))
            if self._first_load:
                self._init_roi(self.roi4k, f4.shape)
                self._init_roi(self.roi1440, f1.shape)
                self.vb4k.autoRange()
                self.vb1440.autoRange()
                self._first_load = False
            t = self.timestamps[i] - self.timestamps[0]
            self.info.setText(
                f"Frame {i + 1}/{self.n}    t = {t:.2f} s    sensor15 = {self.sensor[i]}")

        # ── square crop from a ROI (row-major data coords) ────────────────────
        def _crop(self, frame, roi):
            h, w = frame.shape[:2]
            pos, size = roi.pos(), roi.size()
            x0 = max(0, min(int(round(float(pos[0]))), w - 1))
            y0 = max(0, min(int(round(float(pos[1]))), h - 1))
            x1 = max(x0 + 1, min(x0 + int(round(float(size[0]))), w))
            y1 = max(y0 + 1, min(y0 + int(round(float(size[1]))), h))
            return frame[y0:y1, x0:x1]

        # ── comprehensive plot of the surrounding timepoints ──────────────────
        def _generate(self):
            if self.n == 0:
                return
            idx = self.slider.value()
            # Every STEP-th frame across SPAN frames centred on idx (idx always
            # included, clamped at the recording boundaries).
            offsets = range(-self.SPAN // 2, self.SPAN // 2, self.STEP)
            idxs = [idx + o for o in offsets if 0 <= idx + o < self.n]

            crops4k, crops1440 = [], []
            for j in idxs:
                crops4k.append(self._crop(
                    cv2.imread(self.frames4k[j], cv2.IMREAD_GRAYSCALE), self.roi4k))
                crops1440.append(self._crop(
                    cv2.imread(self.frames1440[j], cv2.IMREAD_GRAYSCALE), self.roi1440))

            dlg = QtWidgets.QDialog(self)
            dlg.setWindowTitle(f"Analysis around frame {idx} — {self.base}")
            dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
            screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
            dlg.resize(int(screen.width() * 0.95), int(screen.height() * 0.9))
            glw = pg.GraphicsLayoutWidget()

            ncol = len(idxs)
            AXIS_W = 60   # fixed width of column 0 (row labels + plot Y-axis) so
                          # the image columns line up with the sensor data below
            t0 = self.timestamps[idxs[0]]
            # column headers + images live in columns 1..ncol; column 0 is reserved
            # for the row labels and the plot's Y-axis (keeps everything aligned).
            for col, j in enumerate(idxs):
                dt = (self.timestamps[j] - t0) * 1000.0
                mark = "  ◀" if j == idx else ""
                glw.addLabel(f"f{j}  {dt:.0f} ms{mark}", row=0, col=col + 1)
            glw.addLabel("4K", row=1, col=0)
            glw.addLabel("1440", row=2, col=0)
            for label_row, crops in ((1, crops4k), (2, crops1440)):
                for col, j in enumerate(idxs):
                    vb = glw.addViewBox(row=label_row, col=col + 1)
                    vb.setAspectLocked(True)
                    vb.invertY(True)
                    vb.addItem(pg.ImageItem(crops[col], levels=(0, 255)))
                    vb.autoRange()
                    if j == idx:
                        vb.setBorder(pg.mkPen("#ffcc00", width=3))

            plot = glw.addPlot(row=3, col=0, colspan=ncol + 1)
            rel = [self.timestamps[j] - t0 for j in idxs]
            sv = [self.sensor[j] for j in idxs]
            plot.plot(rel, sv, pen=pg.mkPen("#00e676", width=2),
                      symbol="o", symbolBrush="#00e676", symbolSize=8)
            plot.setYRange(-0.1, 1.1)
            plot.getAxis("left").setTicks([[(0, "0"), (1, "1")]])
            plot.getAxis("left").setWidth(AXIS_W)
            plot.getAxis("bottom").enableAutoSIPrefix(False)
            plot.setLabel("bottom", f"Time (s) — Sensor {SENSOR_INDEX + 1}")
            plot.addItem(pg.InfiniteLine(
                pos=self.timestamps[idx] - t0, angle=90,
                pen=pg.mkPen("#ffcc00", width=2, style=QtCore.Qt.DashLine)))
            # centre each data point under its image column
            d = (rel[-1] - rel[0]) / (len(rel) - 1) if len(rel) > 1 else 1.0
            plot.setXRange(rel[0] - d / 2, rel[-1] + d / 2, padding=0)

            # Camera rows fill the upper 2/3, sensor plot the lower 1/3; column 0
            # is fixed to the axis width so image columns align with the data.
            grid = glw.ci.layout
            grid.setRowStretchFactor(0, 0)   # frame labels: minimal height
            grid.setRowStretchFactor(1, 1)   # 4K images   ┐
            grid.setRowStretchFactor(2, 1)   # 1440 images ┘ together = 2/3
            grid.setRowStretchFactor(3, 1)   # sensor plot  = 1/3
            grid.setColumnFixedWidth(0, AXIS_W)
            for c in range(1, ncol + 1):
                grid.setColumnStretchFactor(c, 1)

            save_btn = QtWidgets.QPushButton("Save PNG")
            def _save():
                default = os.path.join(self.dir_edit.text(),
                                       f"{self.base}_analysis_f{idx}.png")
                path, _ = QtWidgets.QFileDialog.getSaveFileName(
                    dlg, "Save plot", default, "PNG (*.png)")
                if path:
                    try:
                        import pyqtgraph.exporters
                        pg.exporters.ImageExporter(glw.scene()).export(path)
                    except Exception as exc:
                        QtWidgets.QMessageBox.warning(dlg, "Save failed", str(exc))
            save_btn.clicked.connect(_save)

            lay = QtWidgets.QVBoxLayout(dlg)
            lay.addWidget(glw, 1)
            lay.addWidget(save_btn)
            dlg.show()
            self._dialogs.append(dlg)

    class MainWindow(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Sensor / Camera Test")

            tabs = QtWidgets.QTabWidget()
            tabs.addTab(self._build_record_page(), "Record")
            tabs.addTab(AnalysisTab(), "Analyze")
            self.setCentralWidget(tabs)

            # ── live refresh timers (record tab) ─────────────────────────────
            self.cam_timer = QtCore.QTimer()
            self.cam_timer.timeout.connect(self._update_cams)
            self.cam_timer.start(40)
            self.trace_timer = QtCore.QTimer()
            self.trace_timer.timeout.connect(self.trace.update_from_shared)
            self.trace_timer.start(30)

        def _build_record_page(self):
            self.cam4k = CameraWidget(disp_q_4k, disp_shape_4k)
            self.cam1440 = CameraWidget(disp_q_1440, disp_shape_1440)
            self.trace = SensorTraceWidget(sensor_array, index=SENSOR_INDEX)

            def cam_box(title, widget):
                box = QtWidgets.QVBoxLayout()
                lab = QtWidgets.QLabel(title)
                lab.setAlignment(QtCore.Qt.AlignCenter)
                box.addWidget(lab)
                box.addWidget(widget)
                cont = QtWidgets.QWidget()
                cont.setLayout(box)
                return cont

            cams = QtWidgets.QHBoxLayout()
            cams.addWidget(cam_box("daA3840-45uc (4K)", self.cam4k))
            cams.addWidget(cam_box("daA1440-220um", self.cam1440))
            cams_w = QtWidgets.QWidget()
            cams_w.setLayout(cams)

            # ── recording controls ──────────────────────────────────────────
            self.dir_edit = QtWidgets.QLineEdit()
            self.dir_edit.setPlaceholderText("Output folder…")
            browse = QtWidgets.QPushButton("Browse…")
            browse.clicked.connect(self._browse)
            self.label_edit = QtWidgets.QLineEdit("sensor15")
            self.start_btn = QtWidgets.QPushButton("Start recording")
            self.start_btn.clicked.connect(self._start)
            self.stop_btn = QtWidgets.QPushButton("Stop recording")
            self.stop_btn.clicked.connect(self._stop)
            self.stop_btn.setEnabled(False)
            self.status = QtWidgets.QLabel("Idle")

            ctrl = QtWidgets.QGridLayout()
            ctrl.addWidget(QtWidgets.QLabel("Folder:"), 0, 0)
            ctrl.addWidget(self.dir_edit, 0, 1)
            ctrl.addWidget(browse, 0, 2)
            ctrl.addWidget(QtWidgets.QLabel("Label:"), 1, 0)
            ctrl.addWidget(self.label_edit, 1, 1)
            btns = QtWidgets.QHBoxLayout()
            btns.addWidget(self.start_btn)
            btns.addWidget(self.stop_btn)
            btns.addWidget(self.status)
            btns.addStretch(1)
            ctrl.addLayout(btns, 2, 0, 1, 3)
            ctrl_w = QtWidgets.QWidget()
            ctrl_w.setLayout(ctrl)

            root = QtWidgets.QVBoxLayout()
            root.addWidget(cams_w, stretch=4)
            root.addWidget(self.trace, stretch=2)
            root.addWidget(ctrl_w)
            page = QtWidgets.QWidget()
            page.setLayout(root)
            return page

        def _update_cams(self):
            self.cam4k.update_from_shared()
            self.cam1440.update_from_shared()

        def _browse(self):
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output folder")
            if d:
                self.dir_edit.setText(d)

        def _start(self):
            out = self.dir_edit.text().strip()
            if not out:
                self._browse()
                out = self.dir_edit.text().strip()
                if not out:
                    QtWidgets.QMessageBox.warning(
                        self, "No folder", "Please choose an output folder first.")
                    return
            label = self.label_edit.text().strip() or "sensor15"
            ctrl_queue.put(("start", out, label))
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.dir_edit.setEnabled(False)
            self.label_edit.setEnabled(False)
            self.status.setText("● Recording…")

        def _stop(self):
            ctrl_queue.put(("stop",))
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.dir_edit.setEnabled(True)
            self.label_edit.setEnabled(True)
            self.status.setText("Idle")

        def closeEvent(self, event):
            running_flag.value = False
            try:
                ctrl_queue.put_nowait(("stop",))
            except Exception:
                pass
            event.accept()

    window = MainWindow()
    window.show()
    QtCore.QTimer.singleShot(50, window.showMaximized)
    app.exec_()


# ── Entry point ─────────────────────────────────────────────────────────────────
def main():
    from multiprocessing import Process, Array, Queue, Value

    sensor_array    = Array('i', 16)
    timestamp_value = Queue(maxsize=2)     # required by sensor_process (unused here)
    disp_q_4k       = Queue(maxsize=2)
    disp_q_1440     = Queue(maxsize=2)
    ctrl_queue      = Queue(maxsize=8)
    running_flag    = Value('b', True)

    sensor_proc = Process(target=sensor_process,
                          args=(sensor_array, timestamp_value))
    sensor_proc.start()

    acq_proc = Process(
        target=acquisition_process,
        args=(disp_q_4k, disp_q_1440, sensor_array, ctrl_queue,
              running_flag, DISP_4K, DISP_1440),
    )
    acq_proc.start()

    run_gui(disp_q_4k, disp_q_1440, sensor_array, ctrl_queue, running_flag,
            DISP_4K, DISP_1440)

    # ── shutdown ─────────────────────────────────────────────────────────────
    running_flag.value = False
    try:
        ctrl_queue.put_nowait(("stop",))
    except Exception:
        pass
    # Generous: the acquisition process blocks on the writer finishing any
    # in-progress video encode before it returns — don't kill it mid-encode.
    acq_proc.join(timeout=300)
    if acq_proc.is_alive():
        acq_proc.terminate(); acq_proc.join(timeout=2)
    if acq_proc.is_alive():
        acq_proc.kill(); acq_proc.join()

    sensor_proc.terminate()
    sensor_proc.join(timeout=2)
    if sensor_proc.is_alive():
        sensor_proc.kill(); sensor_proc.join()


if __name__ == "__main__":
    import multiprocessing as mp
    mp.set_start_method("spawn", force=True)
    main()
