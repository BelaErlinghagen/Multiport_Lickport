import numpy as np
import pandas as pd
import os
from time import sleep

class data_saver:
    def __init__(self, camera_data, sensor_data, dlc_data, timestamp_queue):
        self.camera_data = camera_data
        self.sensor_data = sensor_data
        self.dlc_data = dlc_data
        self.timestamp = timestamp_queue
        print("Data saver launched.")

    def start_saving(self, cohort_id, mouse_id, session_id, camera_flag, sensor_flag, dlc_flag, running_flag):
        from shared_states import data_path

        cohort_path = data_path + f"/{cohort_id}"
        mouse_path = cohort_path + f"/{mouse_id}"
        session_path = mouse_path + f"/{session_id}"
        try:
            if not os.path.exists(cohort_path):
                os.makedirs(cohort_path)
            if not os.path.exists(mouse_path):
                os.makedirs(mouse_path)
            if not os.path.exists(session_path):
                os.makedirs(session_path)
        except Exception as e:
            print(f"Folder Creation Error: {e}")

        print(f"Saving Data for {mouse_id}, Session: {session_id} at {data_path}")

        while running_flag:
            print(self.timestamp.get())
            if camera_flag:
                print("Saving Camera")
            elif sensor_flag:
                print("Saving Sensor")
            elif dlc_flag:
                print("Saving DLC")
            sleep(0.05)


    
def saving_process(camera_data, sensor_data, dlc_data, timestamp_queue, cohort_id, mouse_id, session_id, camera_flag, sensor_flag, dlc_flag, running_flag):
    saver = data_saver(camera_data, sensor_data, dlc_data, timestamp_queue)
    saver.start_saving(cohort_id, mouse_id, session_id, camera_flag, sensor_flag, dlc_flag, running_flag)


if __name__ == "__main__":
    from multiprocessing import Process, Value, Array, Queue
    import multiprocessing as mp
    mp.set_start_method("spawn", force=True)
    from serial_controls import sensor_process
    timestamp_queue = Queue(maxsize=2)
    sensor_array = Array('i', 16)  
    sensor_proc = Process(target=sensor_process, args=(sensor_array, timestamp_queue,))
    sensor_proc.start()
    saving_camera_data = Value('b', False) 
    saving_sensor_data = Value('b', False)
    saving_dlc_data = Value('b', False)
    running_flag = Value('b', False)
    saving_proc = Process(target=saving_process, args=(None, sensor_array, None, timestamp_queue, "Test_Cohort", "Test_Mouse", "Test_Session", False, saving_sensor_data, False,  running_flag,))
    saving_proc.start()
    while True:
        command = input(">>> ")
        if command == "Sensor On":
            saving_sensor_data.value = True
        elif command == "Sensor Off":
            saving_sensor_data.value = False
        elif command == "Saving On":
            running_flag.value = True
 
        elif command == "Saving Off":
            running_flag.value = False
            saving_proc.terminate()
