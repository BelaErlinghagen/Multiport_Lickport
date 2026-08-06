"""deeplabcut_controls.py — DLC inference process for the Multiport setup.

Architecture mirrors camera_controls.py / serial_controls.py:
  - DLCTracker class         : wraps DLCLive; runs the inference loop.
  - dlc_process()            : multiprocessing target; initialises DLCLive and
                               loops until dlc_running is set to False.

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
# NOTE: DLCLive / TensorFlow are NOT imported at module level.
# TF must see TF_FORCE_GPU_ALLOW_GROWTH before it initialises its CUDA
# context, otherwise it pre-allocates most VRAM and starves Qt's OpenGL.
# The import is deferred to DLCTracker.__init__() which runs after the env
# var has been set inside dlc_process().


class DLCTracker:
    """Runs DLC inference on frames received from camera_controls.

    Produces pose estimates and distributes them to:
      - pose_queue          (consumed by data_saving for CSV storage)
      - pose_display_queue  (consumed by CameraWidget for live overlay)
    """

    def __init__(self, dlc_queue, pose_queue, pose_display_queue, pose_sm_queue=None):
        # Lazy import: TF_FORCE_GPU_ALLOW_GROWTH must already be set in the
        # environment before this line, so TF uses on-demand VRAM allocation.
        from dlclive import DLCLive, Processor
        from shared_states import model_path

        self.dlc_queue          = dlc_queue
        self.pose_queue         = pose_queue
        self.pose_display_queue = pose_display_queue
        self.pose_sm_queue      = pose_sm_queue   # optional: state machine ITI checks

        print("[DLC] Loading model…")
        self._dlc         = DLCLive(model_path, processor=Processor())
        self._initialized = False
        print("[DLC] Model ready — waiting for frames.")

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
                pose = self._dlc.get_pose(image)
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
    """Multiprocessing target — hosts DLCTracker until dlc_running=False.

    Called by main.py as a multiprocessing.Process target.
    """
    from console_log import tag_process
    tag_process("DLC")

    import os
    # Must be set before TF/DLCLive is imported (happens inside DLCTracker.__init__).
    # Without this TF pre-allocates most VRAM, starving Qt's OpenGL context.
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
