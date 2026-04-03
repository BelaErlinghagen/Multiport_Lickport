# main.py
from multiprocessing import Process, Array
import numpy as np
from camera_controls import TrackingCamera
from plotting_functions import run_gui
from serial_controls import sensor_process
from shared_states import IMG_HEIGHT, IMG_WIDTH


def main():
    cam_shape = (IMG_HEIGHT, IMG_WIDTH)
    cam_size = int(np.prod(cam_shape))
    shared_image = Array('f', cam_size)        # shared camera
    sensor_array = Array('i', 16)             # shared sensors

    # Start camera
    camera = TrackingCamera(shared_image)
    cam_proc = Process(target=camera.run)
    cam_proc.start()

    # Start sensor simulation
    sensor_proc = Process(target=sensor_process, args=(sensor_array,))
    sensor_proc.start()

    # Start GUI
    run_gui(shared_image, sensor_array, cam_shape)

    # Clean up (never reached unless GUI closes)
    camera.stop()
    cam_proc.terminate()
    sensor_proc.terminate()

if __name__ == "__main__":
    main()