"""DLC (DeepLabCut) inference process, structured like camera_controls.py /
serial_controls.py: DLCTracker wraps DLCLive and runs the inference loop;
dlc_process() is the multiprocessing target that initialises DLCLive and
loops until dlc_running is set False.

Inter-process communication:
  dlc_queue            Queue      — full-res cropped frames from camera_controls.
  pose_queue           Queue(2)   — latest pose array (num_kp × 3) for data_saving.
  pose_display_queue   Queue(1)   — latest pose array for CameraWidget overlay.
  dlc_running          Value('b') — set False by main.py on shutdown.

Pose array format: numpy float32, shape (num_keypoints, 3).
  Column 0 — x  in the DLC input image coordinate space (= full-res cropped frame)
  Column 1 — y  in the same space
  Column 2 — likelihood (0–1)
"""

import signal
import time

import numpy as np
# DLCLive/TensorFlow are deliberately not imported at module level: TF must
# see TF_FORCE_GPU_ALLOW_GROWTH before it initialises CUDA, or it
# pre-allocates most VRAM and starves Qt's OpenGL. The import is deferred to
# DLCTracker.__init__(), which runs after dlc_process() sets that env var.


class DLCTracker:
    """Runs DLC inference on frames received from camera_controls.

    Produces pose estimates and distributes them to:
      - pose_queue          (consumed by data_saving for CSV storage)
      - pose_display_queue  (consumed by CameraWidget for live overlay)
      - pose_sm_queue       (consumed by the state machine for ITI dwell checks, if given)
    """

    def __init__(self, dlc_queue, pose_queue, pose_display_queue, pose_sm_queue=None):
        # Lazy import: TF_FORCE_GPU_ALLOW_GROWTH must already be set in the
        # environment before this, so TF allocates VRAM on demand.
        from dlclive import DLCLive, Processor
        from shared_states import model_path, dlc_resize

        self.dlc_queue          = dlc_queue
        self.pose_queue         = pose_queue
        self.pose_display_queue = pose_display_queue
        self.pose_sm_queue      = pose_sm_queue   # optional: state machine ITI checks

        print("[DLC] Loading model…")
        # resize is what sets the tracking rate (see shared_states.dlc_resize).
        # DLCLive scales the pose back to full-frame coordinates itself, so the
        # CSV, the live overlay and the state machine's dwell checks all keep
        # working in DLC_CROP pixels regardless of the value.
        self._dlc         = DLCLive(model_path, processor=Processor(),
                                    resize=float(dlc_resize))
        self._initialized = False
        self._resize      = float(dlc_resize)
        self._rate_times  = []        # set to None once the rate is reported
        print("[DLC] Model ready — waiting for frames.")

    # Poses to time before reporting, skipping the first few while the GPU
    # graph is still warming up.
    _RATE_SKIP, _RATE_N = 5, 25

    def _note_rate(self, seconds, shape):
        """Report the measured tracking rate once, early in a session.

        Inference cost is the only thing setting the pose rate, and it is
        invisible otherwise — a model too slow for the camera silently leaves
        most frames untracked rather than failing.
        """
        if self._rate_times is None:
            return
        self._rate_times.append(seconds)
        if len(self._rate_times) < self._RATE_SKIP + self._RATE_N:
            return
        import statistics
        mean = statistics.fmean(self._rate_times[self._RATE_SKIP:])
        self._rate_times = None       # measured once; stop accumulating
        h, w = shape[:2]
        scale = self._resize
        print(f"[DLC] {mean*1000:.0f} ms per pose ({1/mean:.1f} Hz) on "
              f"{int(w*scale)}x{int(h*scale)} (resize {scale} of {w}x{h}).")
        try:
            import shared_states
            camera_fps = float(getattr(shared_states, "camera_frame_rate", 20))
        except Exception:
            return
        if 1 / mean < camera_fps * 0.98:
            print(f"[DLC] NOTE: slower than the camera's {camera_fps:.0f} fps, so "
                  f"only about 1 frame in {camera_fps*mean:.1f} gets a pose. "
                  f"Lower shared_states.dlc_resize to track every frame.")

    @staticmethod
    def _put_latest(q, item):
        """Drop old item and enqueue new one so the queue never exceeds 1 item."""
        if q.full():
            try:
                q.get_nowait()
            except Exception:
                pass
        try:
            q.put_nowait(item)
        except Exception:
            pass

    def run(self, running_flag):
        while running_flag.value:
            try:
                image = self.dlc_queue.get(timeout=0.5)
            except Exception:
                continue

            try:
                if not self._initialized:
                    self._dlc.init_inference(image)
                    self._initialized = True
                started = time.perf_counter()
                pose = self._dlc.get_pose(image)
                self._note_rate(time.perf_counter() - started, image.shape)
            except Exception as exc:
                print(f"[DLC] Inference error: {exc}")
                continue

            self._put_latest(self.pose_queue, pose)
            self._put_latest(self.pose_display_queue, pose)
            if self.pose_sm_queue is not None:
                self._put_latest(self.pose_sm_queue, pose)

        print("[DLC] Inference loop finished.")


def dlc_process(dlc_queue, pose_queue, pose_display_queue, dlc_running,
                pose_sm_queue=None):
    """Process entry point: runs a DLCTracker until dlc_running is set False."""
    from console_log import tag_process
    tag_process("DLC")

    import os
    # Must be set before TF/DLCLive is imported (inside DLCTracker.__init__),
    # or TF pre-allocates most VRAM and starves Qt's OpenGL context.
    os.environ.setdefault("TF_FORCE_GPU_ALLOW_GROWTH", "true")

    dlc_queue.cancel_join_thread()
    pose_queue.cancel_join_thread()
    pose_display_queue.cancel_join_thread()
    if pose_sm_queue is not None:
        pose_sm_queue.cancel_join_thread()

    def _handle_term(_sig, _frame):
        dlc_running.value = False
    signal.signal(signal.SIGTERM, _handle_term)

    tracker = DLCTracker(dlc_queue, pose_queue, pose_display_queue, pose_sm_queue)
    tracker.run(dlc_running)
    print("[DLC] Process exiting.")


if __name__ == "__main__":
    import multiprocessing as mp
    mp.set_start_method("spawn", force=True)
    from multiprocessing import Process, Queue, Value

    dlc_queue          = Queue(maxsize=2)
    pose_queue         = Queue(maxsize=2)
    pose_display_queue = Queue(maxsize=1)
    dlc_running        = Value("b", True)

    proc = Process(
        target=dlc_process,
        args=(dlc_queue, pose_queue, pose_display_queue, dlc_running),
    )
    proc.start()

    print("Sending dummy frames — press Ctrl+C to stop.")
    try:
        while True:
            # Full-res square dummy frame (matches the cropped camera output)
            from shared_states import IMG_HEIGHT
            dummy = np.zeros((IMG_HEIGHT, IMG_HEIGHT, 3), dtype=np.uint8)
            if dlc_queue.full():
                try:
                    dlc_queue.get_nowait()
                except Exception:
                    pass
            dlc_queue.put(dummy)
            time.sleep(0.2)
            try:
                pose = pose_queue.get_nowait()
                print(f"  pose shape={pose.shape}  kp[0]={pose[0]}")
            except Exception:
                pass
    except KeyboardInterrupt:
        pass
    finally:
        dlc_running.value = False
        proc.join(timeout=5)
        if proc.is_alive():
            proc.kill()
        print("Done.")
