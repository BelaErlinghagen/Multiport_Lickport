import numpy as np
import pandas as pd
import os
from time import sleep, time
from datetime import datetime
from threading import Thread

class data_saver:
    def __init__(self, camera_data, sensor_data, dlc_data, timestamp_queue):
        self.camera_data = camera_data
        self.sensor_data = sensor_data
        self.dlc_data = dlc_data
        self.timestamp = timestamp_queue
        self.running = True
        print("Data saver launched.")

    def start_saving(self, cohort_id, mouse_id, session_id, camera_flag, sensor_flag, dlc_flag):
        from shared_states import data_path
        # create cohort, mouse and session folders if they do not exist yet
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
            print(f"Folder Creation Error during Saving: {e}")
        
        current_time = current_time = datetime.now().strftime('%Y%m%d_%H:%M:%S')
        recording_id = f"{current_time}_{session_id}"
        recording_folder = f"{session_path}/{recording_id}"
        os.makedirs(recording_folder)
        os.makedirs(f"{recording_folder}/Image_Arrays")

        print(f"Saving Data for {mouse_id} in Cohort {cohort_id}, Session: {session_id} at {data_path}")
        # saving loop, constantly checking if data should still be saved
        loop = True
        dataframe = pd.DataFrame({'Timestamp':[],'Sensors':[], 'DLCStuff':[]})
        while loop:
            if not self.running:
                #wrap up saving
                loop = False
                dataframe.to_csv(f"{recording_folder}/{recording_id}_Data")
            try:
                current_timestamp = self.timestamp.get(timeout= 0.05)
            except:
                current_timestamp = None
                print("Dropped Timestamp: Timeout reached.")    
            sensor_data_copy = None
            dlc_data_copy = None
            if camera_flag.value:
                np.save(f"{recording_folder}/Image_Arrays/{current_timestamp}.npy", np.array(self.camera_data.get()).copy())
            elif sensor_flag.value:
                sensor_data_copy = np.array(self.sensor_data).copy()
            elif dlc_flag.value:
                dlc_data_copy = np.array(self.dlc_data).copy()
            new_row = pd.DataFrame({'Timestamp':[current_timestamp], 'Sensors':[sensor_data_copy], 'DLCStuff':[dlc_data_copy]})
            dataframe =  pd.concat([dataframe, new_row]).reset_index(drop=True)
            # sampling rate = 20Hz
            sleep(0.05)


    
def saving_process(camera_data, sensor_data, dlc_data, timestamp_queue, cohort_id, mouse_id, session_id, camera_flag, sensor_flag, dlc_flag, running_flag):
    saver = data_saver(camera_data, sensor_data, dlc_data, timestamp_queue)
    # constantly checking if the running_flag changes
    running_process = False
    while True:
        if running_process == False and running_flag.value:
            print("Saving now...")
            running_process = True
            saver.running = True
            # To DO: move mouse id and so on in here so that it can be flexibly deployed
            Thread(
                target=saver.start_saving,
                args=(cohort_id, mouse_id, session_id, camera_flag, sensor_flag, dlc_flag),
                daemon=True
            ).start()
            print("Started Process")
        elif running_process == True and running_flag.value == False:
            print("Saving done.")
            saver.running = False
            running_process = False
        sleep(0.05)
            


if __name__ == "__main__":
    from multiprocessing import Process, Value, Array, Queue
    import multiprocessing as mp
    mp.set_start_method("spawn", force=True)
    from serial_controls import sensor_process
    from camera_controls import camera_process
    timestamp_queue = Queue(maxsize=2)
    sensor_array = Array('i', 16)  
    sensor_proc = Process(target=sensor_process, args=(sensor_array, timestamp_queue,))
    sensor_proc.start()
    cam_shape = (300, 300)
    timestamp_value = Queue(maxsize=2)
    dlc_queue = Queue(maxsize=2)            # shared camera image original cropped
    frame_queue = Queue(maxsize=2)        # shared camera image downsampled              # shared sensors
    camera_running = Value('b', True)
    cam_proc = Process(target=camera_process, args = (frame_queue, dlc_queue, cam_shape,camera_running,))
    cam_proc.start()
    saving_camera_data = Value('b', False) 
    saving_sensor_data = Value('b', False)
    saving_dlc_data = Value('b', False)
    running_flag = Value('b', False)
    saving_proc = Process(target=saving_process, args=(
        frame_queue, sensor_array, None, timestamp_queue, "Test_Cohort", "Test_Mouse", "Test_Session", 
        saving_camera_data, saving_sensor_data, saving_dlc_data,  running_flag,)
        )
    saving_proc.start()
    while True:
        print(running_flag.value)
        command = input(">>> ")
        if command == "Sensor On":
            saving_sensor_data.value = True
        elif command == "Sensor Off":
            saving_sensor_data.value = False
        elif command == "Camera On":
            saving_camera_data.value = True
        elif command == "Camera Off":
            saving_camera_data.value = False
        elif command == "Saving On":
            running_flag.value = True
        elif command == "Saving Off":
            running_flag.value = False
        elif command == "End":
            saving_proc.terminate()
            cam_proc.terminate()
            print("processes terminated")
            break
