"""Pylon camera driver: grabs frames from the tracking camera, crops and
undistorts them (see camera_calibration.py), feeds DeepLabCut and the GUI
preview, and encodes the session video (see video_writer.py) — all from a
single grab loop so a frame is only ever read from the camera once."""
import signal
import threading
import time
import cv2
from pypylon import pylon
from shared_states import (camera_serial, camera_exposure_us, camera_gain,
                           camera_frame_rate, camera_throughput_limit, DLC_CROP)
import numpy as np

class TrackingCamera:
    """Owns the Pylon camera connection and the grab loop (see run()). One
    instance per process; see camera_process() below for how it's launched."""

    def __init__(self, frame_queue, dlc_queue, plotting_shape,
                 video_running=None, video_path=None,
                 undistort_enabled=None, undistort_reload=None):
        print("Initializing Camera.")
        # Two Basler cameras are connected; select by serial number so
        # enumeration order can't grab the wrong one. Fail loudly if the
        # configured camera is missing rather than silently using the other.
        tl = pylon.TlFactory.GetInstance()
        device = next(
            (d for d in tl.EnumerateDevices()
             if d.GetSerialNumber() == str(camera_serial)),
            None,
        )
        if device is None:
            available = [f"{d.GetModelName()} ({d.GetSerialNumber()})"
                         for d in tl.EnumerateDevices()]
            raise RuntimeError(
                f"Camera with serial {camera_serial} not found. "
                f"Available: {available}"
            )
        self.cam = pylon.InstantCamera(tl.CreateDevice(device))
        self.shared_image_queue = frame_queue
        self.dlc_input = dlc_queue
        self.shape = plotting_shape
        # video_running is the GUI's Camera checkbox; video_path is the recording
        # prefix the parent process already created. Encoding happens here (not
        # in the saving process) because the frames already live in this process.
        self.video_running = video_running
        self.video_path    = video_path
        self._writer       = None
        # Set once a start attempt fails, so a broken encoder is reported once
        # instead of retried every frame. Cleared when recording stops.
        self._video_failed = False
        self._concat_thread = None   # in-flight chunk join, see _await_concat()
        # Fisheye correction: rectified here, before the frame forks off to the
        # video, DeepLabCut and the preview, so every consumer shares one
        # corrected geometry. undistort_enabled lets the calibration wizard see
        # raw frames while measuring; undistort_reload tells us to pick up a
        # calibration it just saved.
        self.undistort_enabled = undistort_enabled
        self.undistort_reload  = undistort_reload
        self._calib = None
        self._maps  = None
        time.sleep(2)
        print("Camera is ready.")

    # ── Lens correction ───────────────────────────────────────────────────────

    def _load_calibration(self):
        """(Re)read the lens calibration and rebuild the remap tables.

        Building the maps is too slow to redo per frame, so this only runs on
        load and when undistort_reload asks for it — not on every frame.
        """
        from camera_calibration import CameraCalibration
        if self._calib is None:
            self._calib = CameraCalibration()
        else:
            self._calib.reload(force=True)
        self._maps = self._calib.build_maps(DLC_CROP)
        if self._maps is None:
            # Not fatal: the rig still records and tracks, just in the lens's own
            # bent coordinates. Loud, because a DLC model trained on corrected
            # frames will quietly do worse here.
            print("[Camera] WARNING: no lens calibration — running on RAW (fisheye) "
                  "frames. Pose coordinates and the camera↔beamer mapping will be "
                  "distorted; run 'Calibrate camera…' on the Cleaning/Testing tab.")
        else:
            print(f"[Camera] lens correction active: {self._calib.describe()}; "
                  f"{cv2.getNumThreads()} remap thread(s).")
            note = self._calib.field_note(DLC_CROP)
            if note:
                print(f"[Camera] {note}.")

    # ── Video recording ───────────────────────────────────────────────────────

    def _recording_prefix(self):
        """Decode the recording path prefix the GUI wrote into the shared buffer
        — a full path stem like /.../Data/BECU371_20260807_113631_Test2, not a
        folder, since recordings are stored flat (shared_states.recording_basename)."""
        if self.video_path is None:
            return None
        raw = bytes(self.video_path[:]).split(b'\x00', 1)[0]
        return raw.decode('utf-8', 'ignore') or None

    def _video_start(self, frame):
        """Open a ChunkedVideoWriter sized to the frame we are actually saving."""
        prefix = self._recording_prefix()
        if not prefix:
            print("[Camera] video requested but no recording path was set.")
            self._video_failed = True
            return
        import shared_states
        from video_writer import ChunkedVideoWriter
        h, w = frame.shape[:2]
        self._writer = ChunkedVideoWriter(
            prefix, w, h,
            fps=getattr(shared_states, "video_fps", 20),
            chunk_seconds=getattr(shared_states, "video_chunk_seconds", 60),
            crf=getattr(shared_states, "video_crf", 23),
            max_size=getattr(shared_states, "video_max_size", 0),
        )

    def _video_stop(self):
        """Close the encoder, then join the chunks on a background thread, so
        frame grabbing (and therefore DLC and the live preview) keeps running
        while ffmpeg stitches the session together."""
        writer, self._writer = self._writer, None
        # A new recording gets a fresh attempt even if the last one could not start.
        self._video_failed = False
        if writer is None:
            return
        prefix = writer.prefix
        writer.close()

        import shared_states
        if not getattr(shared_states, "video_concat_on_stop", True):
            return

        def _join():
            from video_writer import concat_chunks
            try:
                concat_chunks(prefix)
            except Exception as exc:
                print(f"[Camera] video concat failed: {exc}")

        self._concat_thread = threading.Thread(target=_join, daemon=True,
                                               name="video-concat")
        self._concat_thread.start()

    def _await_concat(self, timeout=45):
        """Wait for a running chunk join to finish before the process exits.

        The join runs on a daemon thread so it never holds up frame grabbing,
        but that also means process exit would kill it mid-write and leave the
        session as loose chunks. Only ever waits when a join is actually in
        flight.
        """
        thread, self._concat_thread = self._concat_thread, None
        if thread is None or not thread.is_alive():
            return
        print("[Camera] waiting for the session video to finish joining…")
        thread.join(timeout=timeout)
        if thread.is_alive():
            print("[Camera] video join did not finish in time; the numbered "
                  "_Video_*.mp4 chunks are kept and are individually playable.")

    def _video_tick(self, frame):
        """Start/feed/stop the writer to follow the video_running flag.
        Everything here is guarded — a video failure must never break the
        grab loop, which also feeds DeepLabCut and the GUI preview."""
        if self.video_running is None:
            return
        try:
            want = bool(self.video_running.value)
            if want and self._writer is None and not self._video_failed:
                self._video_start(frame)
            elif not want and (self._writer is not None or self._video_failed):
                self._video_stop()
            if self._writer is not None:
                self._writer.write(frame, time.time())
        except Exception as exc:
            # Latch the failure, or this retries (re-spawning ffmpeg) on every frame.
            print(f"[Camera] video error, recording disabled for this session: {exc}")
            self._writer = None
            self._video_failed = True

    def run(self, running_flag):
        self.cam.Open()
        # The camera defaults to BayerRG8 (colour mosaic); displayed raw as
        # grayscale that looks like noise. Force Mono8 so the grayscale
        # pipeline (QImage Grayscale8 + DLC) gets a clean image.
        try:
            self.cam.PixelFormat.SetValue("Mono8")
        except Exception as exc:
            print(f"[Camera] Could not set PixelFormat Mono8: {exc}")
        # Anti-flicker: pin exposure to a whole multiple of the beamer's 60 Hz
        # period (shared_states.camera_exposure_us) with auto-exposure off, or
        # the beamer brightening the scene drifts it off that multiple and
        # causes scrolling rolling-shutter bands. Gain is fixed too, for
        # deterministic brightness.
        #
        # Order matters: the throughput limit sets the sensor readout time and
        # exposure sets the floor on the frame period, and between them they fix
        # the free-running rate. AcquisitionFrameRate can only slow the camera
        # below that, so it has to be applied last.
        #
        # Each node is set defensively since names and availability vary across
        # camera models/firmware.
        for _node, _val in (
                ("DeviceLinkThroughputLimitMode", "On"),
                ("DeviceLinkThroughputLimit", int(camera_throughput_limit)),
                ("ExposureAuto", "Off"), ("GainAuto", "Off"),
                ("ExposureTime", float(camera_exposure_us)),
                ("Gain", float(camera_gain)),
                ("AcquisitionFrameRateEnable", True),
                ("AcquisitionFrameRate", float(camera_frame_rate))):
            try:
                getattr(self.cam, _node).SetValue(_val)
            except Exception as exc:
                print(f"[Camera] Could not set {_node}={_val}: {exc}")

        # Report what the camera will actually deliver. Asking for a rate the
        # exposure or the throughput limit cannot support is silently clamped,
        # so this is the only place the real rate becomes visible.
        try:
            actual = self.cam.ResultingFrameRate.GetValue()
            print(f"[Camera] {actual:.2f} fps "
                  f"(exposure {camera_exposure_us/1000:.2f} ms, "
                  f"readout {self.cam.SensorReadoutTime.GetValue()/1000:.1f} ms, "
                  f"gain {camera_gain} dB)")
            if actual < float(camera_frame_rate) - 0.05:
                print(f"[Camera] WARNING: {camera_frame_rate} fps was requested but "
                      f"only {actual:.2f} is achievable. A frame cannot be shorter "
                      f"than its exposure ({camera_exposure_us/1000:.2f} ms) or than "
                      f"the sensor readout, so lower shared_states.camera_exposure_us "
                      f"to the next multiple of 16.667 ms, or raise "
                      f"camera_throughput_limit.")
        except Exception:
            pass
        # Remap is the one heavy per-frame operation here; OpenCV splits it
        # across threads. Deliberately capped rather than using the whole
        # machine, since DLC, ffmpeg, the GUI and the state machine need
        # cores too.
        import shared_states
        cv2.setNumThreads(int(getattr(shared_states, "undistort_threads", 4)))
        self._load_calibration()

        self.cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
        try:
            while self.cam.IsGrabbing() and running_flag.value:
                if self.undistort_reload is not None and self.undistort_reload.value:
                    self.undistort_reload.value = False
                    self._load_calibration()
                grabResult = self.cam.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
                if grabResult.GrabSucceeded():
                    img = grabResult.Array
                    # Arena crop, fused with the fisheye correction when the lens
                    # is calibrated. The maps are crop-sized but index into the
                    # full sensor frame — free, since remap's cost follows the
                    # output size, and it lets the correction sample just
                    # outside the crop rectangle when the zoom needs to.
                    if self._maps is not None and (self.undistort_enabled is None
                                                   or self.undistort_enabled.value):
                        cropped_img = cv2.remap(img, self._maps[0], self._maps[1],
                                                cv2.INTER_LINEAR,
                                                borderMode=cv2.BORDER_CONSTANT)
                    else:
                        y0, y1, x0, x1 = DLC_CROP
                        cropped_img = img[y0:y1, x0:x1]
                    # Record the same cropped arena view DLC sees, so pose
                    # coordinates map onto the video 1:1. Non-blocking — a slow
                    # encoder drops frames rather than stalling this loop.
                    self._video_tick(cropped_img)
                    # send full quality cropped image to dlc
                    if self.dlc_input.full():
                        try:
                            self.dlc_input.get_nowait()
                        except Exception:
                            pass
                    self.dlc_input.put(cropped_img)
                    # Downsample to exactly target_h x target_w for the preview.
                    # Steps are derived from the cropped size so the output
                    # always matches self.shape — a mismatch makes QImage read
                    # past the buffer, which looks like static noise.
                    target_h, target_w = self.shape
                    step_h = max(1, cropped_img.shape[0] // target_h)
                    step_w = max(1, cropped_img.shape[1] // target_w)
                    downsampled = cropped_img[::step_h, ::step_w][:target_h, :target_w]
                    if self.shared_image_queue.full():
                        try:
                            self.shared_image_queue.get_nowait()
                        except: pass
                    self.shared_image_queue.put(np.ascontiguousarray(downsampled, dtype=np.uint8))
                grabResult.Release()
                time.sleep(0.01)
        finally:
            # Finalize any in-flight recording before the device goes away, so the
            # last chunk is closed properly and the concat still runs.
            try:
                self._video_stop()
                self._await_concat()
            except Exception as exc:
                print(f"[Camera] could not close the video writer: {exc}")
            # Always release the device so subsequent runs don't hit "exclusively opened"
            self.cam.StopGrabbing()
            self.cam.Close()

def camera_process(shared_image, dlc_queue, cam_shape, running_flag,
                   video_running=None, video_path=None,
                   undistort_enabled=None, undistort_reload=None):
    """Process entry point: runs a TrackingCamera until running_flag clears."""
    from console_log import tag_process
    tag_process("Camera")

    # Prevent Queue feeder threads from blocking this process's exit.
    shared_image.cancel_join_thread()
    dlc_queue.cancel_join_thread()

    # Convert SIGTERM (from cam_proc.terminate()) into a clean loop exit.
    def _handle_term(_sig, _frame):
        running_flag.value = False
    signal.signal(signal.SIGTERM, _handle_term)

    camera = TrackingCamera(shared_image, dlc_queue, cam_shape,
                            video_running, video_path,
                            undistort_enabled, undistort_reload)
    camera.run(running_flag)

if __name__ == "__main__":
    from multiprocessing import Queue, Array, Value
    cam_shape = (200, 200)
    cam_size = int(np.prod(cam_shape))
    dlc_queue = Queue(maxsize=2)
    camera_running = Value('b', True)
    frame_queue = Queue(maxsize=2)
    sensor_array = Array('i', 16)
    # Record straight away into /tmp so the video path can be exercised standalone.
    video_running = Value('b', True)
    video_path    = Array('c', 512)
    video_path.value = b"/tmp/camera_demo/DemoMouse_20250101_000000_DemoSession"
    camera_process(frame_queue, dlc_queue, cam_shape, camera_running,
                   video_running, video_path)
