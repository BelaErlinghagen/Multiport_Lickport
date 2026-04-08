# main.py
from multiprocessing import Process, Array, Queue, Value
import numpy as np
from camera_controls import camera_process
from plotting_functions import run_gui
from serial_controls import sensor_process


def main():
    cam_shape = (300, 300)
    timestamp_value = Queue(maxsize=2)
    dlc_queue = Queue(maxsize=2)            # shared camera image original cropped
    frame_queue = Queue(maxsize=2)        # shared camera image downsampled
    sensor_array = Array('i', 16)               # shared sensors
    camera_running = Value('b', True)             

    # Start camera
    cam_proc = Process(target=camera_process, args = (frame_queue, dlc_queue, cam_shape,camera_running,))
    cam_proc.start()

    # Start sensor grabbing
    sensor_proc = Process(target=sensor_process, args=(sensor_array, timestamp_value,))
    sensor_proc.start()

    # Start GUI
    run_gui(frame_queue, sensor_array, cam_shape)

    # Clean up (never reached unless GUI closes)
    cam_proc.terminate()
    sensor_proc.terminate()

if __name__ == "__main__":
    import multiprocessing as mp
    mp.set_start_method("spawn", force=True)
    main()