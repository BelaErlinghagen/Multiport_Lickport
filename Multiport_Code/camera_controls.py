import signal
import time
from pypylon import pylon
from shared_states import camera_serial, camera_exposure_us, camera_gain, DLC_CROP
import numpy as np

class TrackingCamera:
    def __init__(self, frame_queue, dlc_queue, plotting_shape):
        print("Initializing Camera.")
        # Two Basler cameras are connected — select the tracking camera by serial
        # so we never grab the wrong one (CreateFirstDevice depends on enumeration
        # order). Fail loudly if it's missing rather than silently using the other.
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
        time.sleep(2)
        print("Camera is ready.")

    def run(self, running_flag):
        self.cam.Open()
        # The 4K daA3840-45uc defaults to BayerRG8 (colour mosaic); displaying that
        # raw as grayscale looks like noise. Force Mono8 so the whole grayscale
        # pipeline (QImage Grayscale8 + DLC) gets a clean image.
        try:
            self.cam.PixelFormat.SetValue("Mono8")
        except Exception as exc:
            print(f"[Camera] Could not set PixelFormat Mono8: {exc}")
        # Anti-flicker: pin a fixed exposure that is a whole multiple of the
        # beamer's 60 Hz period (see shared_states.camera_exposure_us). Auto-
        # exposure MUST be off, or the beamer brightening the scene drives the
        # exposure off a clean multiple → scrolling rolling-shutter bands. Gain is
        # fixed too for deterministic brightness. Each node is set defensively
        # (names/availability vary across models/firmware).
        for _node, _val in (("ExposureAuto", "Off"), ("GainAuto", "Off"),
                            ("ExposureTime", float(camera_exposure_us)),
                            ("Gain", float(camera_gain))):
            try:
                getattr(self.cam, _node).SetValue(_val)
            except Exception as exc:
                print(f"[Camera] Could not set {_node}={_val}: {exc}")
        self.cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
        try:
            while self.cam.IsGrabbing() and running_flag.value:
                grabResult = self.cam.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
                if grabResult.GrabSucceeded():
                    img = grabResult.Array
                    # Arena focus crop (shared with DLC/beamer normalisation).
                    y0, y1, x0, x1 = DLC_CROP
                    cropped_img = img[y0:y1, x0:x1]
                    # send full quality cropped image to dlc
                    if self.dlc_input.full():
                        try:
                            self.dlc_input.get_nowait()
                        except Exception:
                            pass
                    self.dlc_input.put(cropped_img)
                    # downsample image to *exactly* target_h × target_w for plotting.
                    # Steps are derived from the cropped size so the output shape
                    # always matches self.shape (a mismatch makes QImage read past
                    # the buffer → static noise).
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
            # Always release the device so subsequent runs don't hit "exclusively opened"
            self.cam.StopGrabbing()
            self.cam.Close()

def camera_process(shared_image, dlc_queue, cam_shape, running_flag):
    # Prevent Queue feeder threads from blocking this process's atexit.
    shared_image.cancel_join_thread()
    dlc_queue.cancel_join_thread()

    # Convert SIGTERM (from cam_proc.terminate()) into a clean loop exit
    def _handle_term(_sig, _frame):
        running_flag.value = False
    signal.signal(signal.SIGTERM, _handle_term)

    camera = TrackingCamera(shared_image, dlc_queue, cam_shape)
    camera.run(running_flag)

if __name__ == "__main__":
    from multiprocessing import Queue, Array, Value
    cam_shape = (200, 200)
    cam_size = int(np.prod(cam_shape))
    dlc_queue = Queue(maxsize=2)
    camera_running = Value('b', True)  
    frame_queue = Queue(maxsize=2)
    sensor_array = Array('i', 16)    
    camera_process(frame_queue, dlc_queue, cam_shape, camera_running)