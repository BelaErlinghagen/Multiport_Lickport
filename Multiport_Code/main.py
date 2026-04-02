from multiprocessing import Process, Queue, shared_memory, set_start_method
import time
import numpy as np
from plotting_functions import run_plotter
from serial_controls import SerialControls
from camera_controls import TrackingCamera

NUM_SENSORS = 16
IMG_HEIGHT, IMG_WIDTH = 2160, 3840

# Create shared memory for the camera image
shm = shared_memory.SharedMemory(create=True, size=IMG_HEIGHT * IMG_WIDTH * 4)
shared_image = np.ndarray((IMG_HEIGHT, IMG_WIDTH), dtype=np.float32, buffer=shm.buf)

# Queue for sensor activations (active pins)
queue = Queue(maxsize=5)

def camera_process(shared_image):
    """Continuously capture images into shared memory."""
    camera = TrackingCamera(shared_image)
    try:
        while True:
            time.sleep(0.01)  # small sleep to prevent 100% CPU
    except KeyboardInterrupt:
        camera.end_processes()

def serial_process(queue):
    """Continuously read serial sensor data and push to queue."""
    serial_controller = SerialControls()
    try:
        while True:
            timestamp, active_pin = serial_controller.read_serial()
            pin_list = active_pin[0] + active_pin[1]  # flatten if needed
            # push non-blocking
            if not queue.full():
                queue.put(pin_list)
            time.sleep(0.01)  # small sleep to prevent 100% CPU
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    set_start_method("spawn")

    # Start GUI process (reads shared_image + queue)
    gui_process = Process(target=run_plotter, args=(queue, shared_image))
    gui_process.start()

    # Start camera and serial processes
    cam_proc = Process(target=camera_process, args=(shared_image,))
    cam_proc.start()

    serial_proc = Process(target=serial_process, args=(queue,))
    serial_proc.start()

    try:
        # Main process can optionally track cumulative counts
        cumulative_counts = [0] * NUM_SENSORS
        while True:
            if not queue.empty():
                active = queue.get()
                for sid in active:
                    cumulative_counts[sid-1] += 1
            time.sleep(0.05)

    except KeyboardInterrupt:
        print("Stopping...")
        gui_process.terminate()
        gui_process.join()
        cam_proc.terminate()
        cam_proc.join()
        serial_proc.terminate()
        serial_proc.join()
        shm.close()
        shm.unlink()