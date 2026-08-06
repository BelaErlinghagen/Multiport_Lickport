import numpy as np
import pandas as pd
import os
import signal
from time import monotonic, sleep
from datetime import datetime
from threading import Thread

# How long rows may sit in RAM before being appended to the CSV. Overridden by
# shared_states.save_chunk_seconds; the fallback keeps this module usable standalone.
DEFAULT_CHUNK_SECONDS = 30


class data_saver:
    def __init__(self, camera_data, sensor_data, dlc_data, timestamp_queue):
        self.camera_data = camera_data
        self.sensor_data = sensor_data
        self.dlc_data = dlc_data
        self.timestamp = timestamp_queue
        self.running = True
        print("Data saver launched.")

    @staticmethod
    def _flush(rows, csv_path):
        """Append buffered rows to the session CSV and clear the buffer.

        Writing in chunks (rather than holding one DataFrame until the session ends)
        means a crash costs at most one chunk, and it avoids re-concatenating a
        growing DataFrame on every 50 ms sample. The header is written only when the
        file is created; there is no index column — Timestamp is the key.
        """
        if not rows:
            return
        pd.DataFrame(rows).to_csv(csv_path, mode="a", index=False,
                                  header=not os.path.exists(csv_path))
        print(f"[Saving] flushed {len(rows)} row(s) to {os.path.basename(csv_path)}")
        rows.clear()

    def start_saving(self, mouse_id, session_id, camera_flag, sensor_flag, dlc_flag,
                     recording_folder=None):
        if recording_folder is None:
            # Standalone use (the __main__ demo below). The GUI always passes a
            # folder it created itself, so it owns the path from the moment Start is
            # pressed and can open the session console log there immediately.
            from shared_states import get_data_path
            recording_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{session_id}"
            recording_folder = os.path.join(get_data_path(), mouse_id, session_id,
                                            recording_id)
        recording_id = os.path.basename(recording_folder)
        # exist_ok: the folder normally already exists because the GUI made it.
        # Without it, a pre-existing folder raised FileExistsError inside this daemon
        # thread, which then died silently while the GUI still showed "Recording".
        os.makedirs(os.path.join(recording_folder, "Image_Arrays"), exist_ok=True)

        print(f"[Saving] {mouse_id} / {session_id} → {recording_folder}")

        # Rows are buffered here and appended to the CSV every chunk_seconds, so a
        # crash mid-session only loses the rows since the last flush.
        try:
            import shared_states
            chunk_seconds = float(getattr(shared_states, "save_chunk_seconds",
                                          DEFAULT_CHUNK_SECONDS))
        except Exception:
            chunk_seconds = DEFAULT_CHUNK_SECONDS
        csv_path = f"{recording_folder}/{recording_id}_Data.csv"
        rows = []
        last_flush = monotonic()

        # saving loop, constantly checking if data should still be saved
        while True:
            if not self.running:
                # Session ended — write whatever has not been flushed yet and exit
                self._flush(rows, csv_path)
                break
            try:
                current_timestamp = self.timestamp.get(timeout=0.05)
            except Exception:
                current_timestamp = None
            sensor_data_copy = None
            dlc_data_copy    = None
            # Each flag is checked independently so all enabled streams are captured
            if camera_flag.value and current_timestamp is not None:
                np.save(f"{recording_folder}/Image_Arrays/{current_timestamp}.npy",
                        np.array(self.camera_data.get()).copy())
            if sensor_flag.value:
                sensor_data_copy = np.array(self.sensor_data).copy()
            if dlc_flag.value and self.dlc_data is not None:
                try:
                    dlc_data_copy = self.dlc_data.get_nowait()
                except Exception:
                    dlc_data_copy = None
            rows.append({
                'Timestamp': current_timestamp,
                'Sensors':   sensor_data_copy,
                'DLCStuff':  dlc_data_copy,
            })
            now = monotonic()
            if now - last_flush >= chunk_seconds:
                self._flush(rows, csv_path)
                last_flush = now
            # sampling rate = 20 Hz
            sleep(0.05)


    
def saving_process(camera_data, sensor_data, dlc_data, timestamp_queue, mouse_id, session_id, camera_flag, sensor_flag, dlc_flag, running_flag, recording_folder=None):
    from console_log import tag_process
    tag_process("Saving")

    saver = data_saver(camera_data, sensor_data, dlc_data, timestamp_queue)
    saving_thread = None

    # A SIGTERM (the GUI ending the recording) must not cut the saving thread off
    # mid-chunk: stop the loop and give it a moment to append the buffered rows.
    def _handle_term(_sig, _frame):
        saver.running = False
        if saving_thread is not None:
            saving_thread.join(timeout=2)
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _handle_term)

    # constantly checking if the running_flag changes
    running_process = False
    while True:
        if running_process == False and running_flag.value:
            print("Saving now...")
            running_process = True
            saver.running = True
            # To DO: move mouse id and so on in here so that it can be flexibly deployed
            saving_thread = Thread(
                target=saver.start_saving,
                args=(mouse_id, session_id, camera_flag, sensor_flag, dlc_flag,
                      recording_folder),
                daemon=True
            )
            saving_thread.start()
            print("Started Process")
        elif running_process == True and running_flag.value == False:
            print("Saving done.")
            saver.running = False
            # Wait for the final flush so the process can exit without losing rows.
            if saving_thread is not None:
                saving_thread.join(timeout=5)
                saving_thread = None
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
        frame_queue, sensor_array, None, timestamp_queue, "Test_Mouse", "Test_Session",
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
