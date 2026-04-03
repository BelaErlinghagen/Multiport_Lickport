from multiprocessing import Process, Queue, shared_memory, set_start_method
import time
import numpy as np
from plotting_functions import run_plotter
from serial_controls import SerialControls
from camera_controls import TrackingCamera
import shared_states as s

NUM_SENSORS = 16

def camera_process(shm_name):
    existing_shm = shared_memory.SharedMemory(name=shm_name)
    shared_image = np.ndarray((s.IMG_SIZE, s.IMG_SIZE), dtype=np.float32, buffer=existing_shm.buf)
    camera = TrackingCamera(shared_image)
    try:
        camera.run()
    finally:
        camera.stop()
        existing_shm.close()

def serial_process(queue):
    serial_controller = SerialControls()
    try:
        while True:
            timestamp, active_pin = serial_controller.read_serial()
            pin_list = active_pin[0] + active_pin[1]
            if not queue.full():
                queue.put(pin_list)
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    try:
        set_start_method("spawn")
    except RuntimeError:
        pass

    shm = shared_memory.SharedMemory(create=True, size=s.IMG_SIZE*s.IMG_SIZE*4)
    shape = (s.IMG_SIZE, s.IMG_SIZE)
    shared_image = np.ndarray(shape, dtype=np.float32, buffer=shm.buf)
    queue = Queue(maxsize=5)

    gui_process = Process(target=run_plotter, args=(queue, shm.name))
    gui_process.start()

    cam_proc = Process(target=camera_process, args=(shm.name,))
    cam_proc.start()

    serial_proc = Process(target=serial_process, args=(queue,))
    serial_proc.start()

    try:
        cumulative_counts = [0] * NUM_SENSORS
        while True:
            try:
                active = queue.get(timeout=0.05)
                for sid in active:
                    cumulative_counts[sid-1] += 1
            except:
                pass
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