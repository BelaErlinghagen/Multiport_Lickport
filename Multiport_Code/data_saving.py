"""data_saving.py — the session CSV.

One row every 50 ms holding both what the sensors saw and what the setup was doing:
the lick sensors, the DLC pose, and the live state of every actuator (pumps, LEDs,
speaker, beamer, touch screens, BNC lines) read out of the shared hardware_state,
plus the state machine's per-port reward verdict.

Camera frames are *not* saved here any more. They used to be written one .npy per
sample — 4 MB each, pulled out of dlc_queue and therefore stolen from DeepLabCut.
The camera process now encodes them straight to chunked MP4 instead
(camera_controls.TrackingCamera._video_tick → video_writer.ChunkedVideoWriter).

Reading a session back — the 16-character columns must be read as strings, or pandas
parses "0010000000000000" as the integer 10000000000000 and the leading zeros (i.e.
the first channels) are lost:

    df = pd.read_csv(path, dtype={"Sensors": str, "Pumps": str, "LEDs": str,
                                  "BNCs": str, "Rewards": str})
    pumps_on = [i + 1 for i, c in enumerate(df.Pumps[0]) if c == "1"]
    beamer   = json.loads(df.Beamer[0]) if pd.notna(df.Beamer[0]) else None

Rewards is the one that is *not* a bit-string: each character is a 0-6 code (see
hardware_state.REWARD_*), so read it with `[int(c) for c in s]` rather than testing
for "1". It says which ports were rewarded this trial and how each one ended —
complete, partial, or a probability miss — which the Pumps column cannot, now that
one reward is a train of pulses spread over several licks.

Video frames align by index: row N of {prefix}_frames.csv gives the wall-clock
timestamp of frame N of the MP4, on the same clock as the Timestamp column here.

Files are written flat into the Data folder as <prefix>_Data.csv, where the prefix
is shared_states.recording_basename(mouse, session) — there are no per-mouse or
per-session sub-folders.
"""

import json
import pandas as pd
import os
import signal
from time import monotonic, sleep
from datetime import datetime
from threading import Thread

import hardware_state

# How long rows may sit in RAM before being appended to the CSV. Overridden by
# shared_states.save_chunk_seconds; the fallback keeps this module usable standalone.
DEFAULT_CHUNK_SECONDS = 30


class data_saver:
    def __init__(self, sensor_data, dlc_data, timestamp_queue, hw=None):
        self.sensor_data = sensor_data
        self.dlc_data = dlc_data
        self.timestamp = timestamp_queue
        self.hw = hw          # hardware_state.HardwareState, or None
        self.running = True
        print("Data saver launched.")

    def _actuator_row(self):
        """Snapshot every actuator for one CSV row.

        The 16-item states are packed bit-strings ("0010000000000000") — fixed width,
        no whitespace to guess at, and `[int(c) for c in s]` reads them back. Rewards
        uses the same shape but 0-6 codes rather than flags. The beamer is the one
        structured field, so it goes in as compact JSON, and is None whenever nothing
        is being projected.
        """
        hw = self.hw
        if hw is None:
            return {'Pumps': None, 'LEDs': None, 'Speakers': None,
                    'Beamer': None, 'Screens': None, 'BNCs': None,
                    'Rewards': None}

        beamer = hardware_state.read_beamer(hw)
        if beamer is not None:
            _on, shadow, x_cm, y_cm, diameter_cm = beamer
            beamer = json.dumps({"shadow": shadow, "x_cm": round(x_cm, 3),
                                 "y_cm": round(y_cm, 3),
                                 "diameter_cm": round(diameter_cm, 3)})
        return {
            'Pumps':    hardware_state.bits(hw.pumps),
            'LEDs':     hardware_state.bits(hw.leds),
            'Speakers': 1 if hw.speaker.value else 0,
            'Beamer':   beamer,
            'Screens':  hardware_state.screen_names(hw),
            'BNCs':     hardware_state.bnc_bits(hw),
            'Rewards':  hardware_state.reward_codes(hw),
        }

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

    def start_saving(self, mouse_id, session_id, sensor_flag, dlc_flag,
                     file_prefix=None):
        if file_prefix is None:
            # Standalone use (the __main__ demo below). The GUI always builds the
            # prefix itself, so it owns the path from the moment Start is pressed and
            # can open the session console log alongside the CSV immediately.
            from shared_states import get_data_path, recording_basename
            file_prefix = os.path.join(get_data_path(),
                                       recording_basename(mouse_id, session_id))
        # exist_ok: the Data folder normally already exists. Without it, a
        # pre-existing folder raised FileExistsError inside this daemon thread, which
        # then died silently while the GUI still showed "Recording".
        os.makedirs(os.path.dirname(file_prefix) or ".", exist_ok=True)

        print(f"[Saving] {mouse_id} / {session_id} → "
              f"{os.path.basename(file_prefix)}_Data.csv")

        # Rows are buffered here and appended to the CSV every chunk_seconds, so a
        # crash mid-session only loses the rows since the last flush.
        try:
            import shared_states
            chunk_seconds = float(getattr(shared_states, "save_chunk_seconds",
                                          DEFAULT_CHUNK_SECONDS))
        except Exception:
            chunk_seconds = DEFAULT_CHUNK_SECONDS
        csv_path = f"{file_prefix}_Data.csv"
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
            # Each flag is checked independently so all enabled streams are captured.
            # Camera frames are absent here on purpose — the camera process encodes
            # them to MP4 itself, gated by the same Camera checkbox.
            if sensor_flag.value:
                sensor_data_copy = hardware_state.bits(self.sensor_data)
            if dlc_flag.value and self.dlc_data is not None:
                try:
                    dlc_data_copy = self.dlc_data.get_nowait()
                except Exception:
                    dlc_data_copy = None
            # Actuator state is always recorded: it is what the setup was *doing*,
            # and it is cheap, so there is no checkbox to forget to tick.
            row = {'Timestamp': current_timestamp, 'Sensors': sensor_data_copy}
            row.update(self._actuator_row())
            row['DLCStuff'] = dlc_data_copy
            rows.append(row)
            now = monotonic()
            if now - last_flush >= chunk_seconds:
                self._flush(rows, csv_path)
                last_flush = now
            # sampling rate = 20 Hz
            sleep(0.05)


    
def saving_process(sensor_data, dlc_data, timestamp_queue, mouse_id, session_id,
                   sensor_flag, dlc_flag, running_flag, file_prefix=None, hw=None):
    from console_log import tag_process
    tag_process("Saving")

    saver = data_saver(sensor_data, dlc_data, timestamp_queue, hw)
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
                args=(mouse_id, session_id, sensor_flag, dlc_flag,
                      file_prefix),
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
    from hardware_state import HardwareState
    timestamp_queue = Queue(maxsize=2)
    sensor_array = Array('i', 16)
    command_queue = Queue(maxsize=1000)
    hw = HardwareState()
    sensor_proc = Process(target=sensor_process,
                          args=(sensor_array, timestamp_queue, command_queue, hw))
    sensor_proc.start()
    cam_shape = (300, 300)
    dlc_queue = Queue(maxsize=2)          # shared camera image original cropped
    frame_queue = Queue(maxsize=2)        # shared camera image downsampled
    camera_running = Value('b', True)
    video_running  = Value('b', False)
    video_path     = Array('c', 512)
    cam_proc = Process(target=camera_process,
                       args=(frame_queue, dlc_queue, cam_shape, camera_running,
                             video_running, video_path))
    cam_proc.start()
    saving_sensor_data = Value('b', False)
    saving_dlc_data = Value('b', False)
    running_flag = Value('b', False)
    saving_proc = Process(target=saving_process, args=(
        sensor_array, None, timestamp_queue, "Test_Mouse", "Test_Session",
        saving_sensor_data, saving_dlc_data, running_flag, None, hw)
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
            # The camera process owns the video now; it needs a path prefix.
            video_path.value = b"/tmp/data_saving_demo/Test_Mouse_20250101_000000_Test_Session"
            video_running.value = True
        elif command == "Camera Off":
            video_running.value = False
        elif command == "Saving On":
            running_flag.value = True
        elif command == "Saving Off":
            running_flag.value = False
        elif command.startswith(("LED:", "MOS:", "BNC:")):
            # Exercise the actuator tracking: the command reaches the Arduino and
            # shows up in the Pumps/LEDs/BNCs columns of the CSV.
            command_queue.put(command)
        elif command == "End":
            saving_proc.terminate()
            cam_proc.terminate()
            sensor_proc.terminate()
            print("processes terminated")
            break
