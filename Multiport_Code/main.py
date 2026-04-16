# main.py
from multiprocessing import Process, Array, Queue, Value
import numpy as np
from camera_controls import camera_process
from main_plotting import run_gui
from serial_controls import sensor_process


def main():
    cam_shape = (300, 300)
    timestamp_value = Queue(maxsize=2)
    dlc_queue = Queue(maxsize=2)            # shared camera image original cropped
    frame_queue = Queue(maxsize=2)        # shared camera image downsampled
    sensor_array = Array('i', 16)               # shared sensors
    camera_running = Value('b', True)
    command_queue = Queue(maxsize=100)          # GUI → Arduino commands

    # Start camera
    cam_proc = Process(target=camera_process, args = (frame_queue, dlc_queue, cam_shape,camera_running,))
    cam_proc.start()

    # Start sensor grabbing (also handles outbound commands)
    sensor_proc = Process(target=sensor_process, args=(sensor_array, timestamp_value, command_queue))
    sensor_proc.start()

    # Start GUI — pass shared data objects so ExperimentPage can wire saving
    data_sources = {
        "frame_queue":     frame_queue,
        "sensor_array":    sensor_array,
        "dlc_queue":       dlc_queue,
        "timestamp_value": timestamp_value,
    }
    run_gui(frame_queue, sensor_array, cam_shape, command_queue, data_sources)

    # Clean up after GUI closes — signal camera to stop cleanly first so
    # Pylon releases the USB device before the process is force-killed.
    camera_running.value = False
    cam_proc.join(timeout=2)
    if cam_proc.is_alive():
        cam_proc.terminate()
    cam_proc.join(timeout=1)

    sensor_proc.terminate()
    sensor_proc.join(timeout=1)

if __name__ == "__main__":
    import multiprocessing as mp
    mp.set_start_method("spawn", force=True)
    main()