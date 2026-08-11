"""The session CSV: one row every 50 ms of what the sensors saw and what the
setup was doing — lick sensors, DLC pose, and the live state of every
actuator (pumps, LEDs, speaker, beamer, touch screens, BNC lines), read from
the shared hardware_state, plus the state machine's per-port reward verdict.

Camera frames are not saved here — the camera process encodes them straight
to chunked MP4 instead (see video_writer.py).

Reading a session back, the 16-character columns must be read as strings, or
pandas parses "0010000000000000" as an integer and drops the leading zeros:

    df = pd.read_csv(path, dtype={"Sensors": str, "Pumps": str, "LEDs": str,
                                  "BNCs": str, "RewardStates": str})
    pumps_on = [i + 1 for i, c in enumerate(df.Pumps[0]) if c == "1"]
    beamer   = json.loads(df.Beamer[0]) if pd.notna(df.Beamer[0]) else None

Beamer is compact JSON, empty when nothing is being projected:

    {"mode": "light"|"shadow"|"lit_background", "x_cm", "y_cm", "diameter_cm"}

(see hardware_state.BEAMER_MODE_NAMES for what each mode means).

Two columns describe the rewards, both indexed by lickport 1-16:

    Rewards       which reward (protocol id, 0 = none) sits on each port,
                  pipe-separated because ids run to 16 and would not fit one
                  character: "0|1|0|0|0|0|2|0|0|0|0|0|0|0|0|0".
                  Read with `[int(v) for v in s.split("|")]`. It changes
                  mid-session when sporadic switching makes two rewards trade
                  ports, which is why the port alone can't identify a reward.
    RewardStates  what that port's reward was doing, one 0-6 code per port
                  (hardware_state.REWARD_*): "0100002000000000". Read with
                  `[int(c) for c in s]`, not a "1" test. It records how each
                  port's reward ended this trial — complete, partial, or a
                  probability miss — which the Pumps column alone can't show,
                  since one reward is a train of pulses spread over several licks.

So `Rewards[i]` says *which* reward and `RewardStates[i]` says *how it went*,
for the same port i.

Video frames align by index: row N of {prefix}_frames.csv gives the
wall-clock timestamp of frame N of the MP4, on the same clock as this
file's Timestamp column.

Files are written flat into the Data folder as <prefix>_Data.csv, where the
prefix is shared_states.recording_basename(mouse, session).
"""

import json
import pandas as pd
import os
import signal
from time import monotonic, sleep, time
from datetime import datetime
from threading import Thread

import hardware_state

# How long rows may sit in RAM before being appended to the CSV. Overridden by
# shared_states.save_chunk_seconds; the fallback keeps this module usable standalone.
DEFAULT_CHUNK_SECONDS = 30

# Row rate. Kept equal to the camera's frame rate so one CSV row corresponds to
# one video frame, and held to by deadline (see start_saving) rather than by
# sleeping a fixed 50 ms, which would run slow by the cost of building each row.
SAMPLE_HZ = 20.0


class data_saver:
    """Buffers sensor/DLC/actuator rows in RAM and periodically flushes them
    to the session CSV. See saving_process() below for how this runs as a
    background thread inside its own process."""

    def __init__(self, sensor_data, dlc_data, timestamp_queue, hw=None):
        self.sensor_data = sensor_data
        self.dlc_data = dlc_data
        self.timestamp = timestamp_queue
        self.hw = hw          # hardware_state.HardwareState, or None
        self.running = True
        print("Data saver launched.")

    def _actuator_row(self):
        """Snapshot every actuator for one CSV row.

        The 16-item states are packed as fixed-width bit-strings
        ("0010000000000000"); RewardStates uses the same shape but with 0-6
        codes instead of flags, and Rewards is pipe-separated since reward ids
        run past one digit. Beamer is the one structured field, stored as
        compact JSON (mode + target geometry), or None when nothing is projected.
        """
        hw = self.hw
        if hw is None:
            return {'Pumps': None, 'LEDs': None, 'Speakers': None,
                    'Beamer': None, 'Screens': None, 'BNCs': None,
                    'Rewards': None, 'RewardStates': None}

        beamer = hardware_state.read_beamer(hw)
        if beamer is not None:
            _on, mode, x_cm, y_cm, diameter_cm = beamer
            beamer = json.dumps({"mode": mode, "x_cm": round(x_cm, 3),
                                 "y_cm": round(y_cm, 3),
                                 "diameter_cm": round(diameter_cm, 3)})
        return {
            'Pumps':    hardware_state.bits(hw.pumps),
            'LEDs':     hardware_state.bits(hw.leds),
            'Speakers': 1 if hw.speaker.value else 0,
            'Beamer':   beamer,
            'Screens':  hardware_state.screen_names(hw),
            'BNCs':     hardware_state.bnc_bits(hw),
            'Rewards':      hardware_state.reward_ids(hw),
            'RewardStates': hardware_state.reward_codes(hw),
        }

    def _latest_timestamp(self):
        """When this row's data was current, on the same clock as the video.

        Drains rather than taking one item: the serial process publishes at
        100 Hz into a 2-deep queue, so a single get() returns the older of the
        two and stamps the row up to 20 ms before the sensor bits that are read
        from shared memory beside it. Taking the newest entry instead makes the
        timestamp match the sensor snapshot this row actually stores.

        Falls back to the wall clock if nothing is queued (sensors disabled, or
        the serial process still starting): a row with no timestamp cannot be
        aligned to anything, and both clocks are time.time() regardless.
        """
        newest = None
        while True:
            try:
                newest = self.timestamp.get_nowait()
            except Exception:
                break
        return newest if newest is not None else time()

    @staticmethod
    def _flush(rows, csv_path):
        """Append buffered rows to the session CSV and clear the buffer.

        Writing in chunks (rather than holding one growing DataFrame for the
        whole session) means a crash costs at most one chunk. The header is
        written only when the file is created; Timestamp is the key, so
        there's no separate index column.
        """
        if not rows:
            return
        pd.DataFrame(rows).to_csv(csv_path, mode="a", index=False,
                                  header=not os.path.exists(csv_path))
        print(f"[Saving] flushed {len(rows)} row(s) to {os.path.basename(csv_path)}")
        rows.clear()

    def start_saving(self, mouse_id, session_id, sensor_flag, dlc_flag,
                     file_prefix=None):
        """Loop at 20 Hz, appending one row per tick until self.running is False."""
        if file_prefix is None:
            # Only hit in standalone use (see the __main__ demo below) — the
            # GUI always builds and passes its own prefix.
            from shared_states import get_data_path, recording_basename
            file_prefix = os.path.join(get_data_path(),
                                       recording_basename(mouse_id, session_id))
        # exist_ok=True: without it, an already-existing Data folder raises
        # FileExistsError inside this daemon thread, which then dies silently
        # while the GUI still shows "Recording".
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

        # Sample on absolute deadlines rather than "do the work, then sleep 50 ms".
        # A fixed sleep makes the period 50 ms *plus* however long the row took to
        # build, so the rate sits below 20 Hz and every slow tick (the chunk flush
        # especially) is lost time the loop never wins back. Anchoring each tick to
        # start + n*PERIOD keeps the long-run rate at exactly 20 Hz.
        period = 1.0 / SAMPLE_HZ
        start  = monotonic()
        tick   = 0

        while True:
            if not self.running:
                # Session ended — write whatever has not been flushed yet and exit
                self._flush(rows, csv_path)
                break
            current_timestamp = self._latest_timestamp()
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

            tick += 1
            delay = (start + tick * period) - monotonic()
            if delay > 0:
                sleep(delay)
            elif delay < -period:
                # More than a whole period late (a long flush, or the machine was
                # busy). Give up on the ticks already missed rather than firing
                # them back-to-back, which would bunch several rows into one
                # instant. Being merely a little late needs no correction: the
                # deadlines are absolute, so the next tick lands back on the grid
                # by itself and no row is lost.
                missed = int(-delay // period)
                tick += missed
                print(f"[Saving] sampling overran by {-delay*1000:.0f} ms; "
                      f"skipped {missed} row(s) to stay on the {SAMPLE_HZ:.0f} Hz grid.")



def saving_process(sensor_data, dlc_data, timestamp_queue, mouse_id, session_id,
                   sensor_flag, dlc_flag, running_flag, file_prefix=None, hw=None):
    """Process entry point: starts/stops a data_saver's saving thread to
    follow running_flag."""
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
            # TODO: move mouse id and so on in here so that it can be flexibly deployed
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
            # The camera process encodes its own video; it just needs a path prefix.
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
