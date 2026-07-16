# main.py
from multiprocessing import Process, Array, Queue, Value
import numpy as np
from beamer_controls import beamer_process
from camera_controls import camera_process
from deeplabcut_controls import dlc_process
from main_plotting import run_gui
from serial_controls import sensor_process
from state_machine import state_machine_process


def main():
    cam_shape          = (500, 500)
    timestamp_value    = Queue(maxsize=2)
    dlc_queue          = Queue(maxsize=2)   # camera → DLC (full-res frames)
    frame_queue        = Queue(maxsize=2)   # camera → GUI (downsampled frames)
    pose_queue         = Queue(maxsize=2)   # DLC → saving (latest pose)
    pose_display_queue = Queue(maxsize=1)   # DLC → GUI overlay (latest pose)
    pose_sm_queue      = Queue(maxsize=1)   # DLC → state machine (ITI dwell checks)
    sensor_array       = Array('i', 16)    # shared sensors
    camera_running     = Value('b', True)
    dlc_running        = Value('b', True)
    command_queue      = Queue(maxsize=100) # GUI → Arduino commands
    beamer_queue       = Queue(maxsize=8)   # GUI/SM → beamer projection commands
    beamer_running     = Value('b', True)

    # State-machine control flags
    sm_active      = Value('b', False)   # set True by ExperimentPage to start a session
    sm_stop        = Value('b', False)   # set True for an emergency stop
    sm_running     = Value('b', True)    # set False on shutdown to exit the SM process
    session_done   = Value('b', False)   # SM sets True on natural session end
    protocol_queue = Queue(maxsize=1)    # ExperimentPage puts protocol dict here before sm_active=True

    # Start camera
    cam_proc = Process(target=camera_process, args=(frame_queue, dlc_queue, cam_shape, camera_running))
    cam_proc.start()

    # Start DLC inference (runs continuously; results go to saving + GUI overlay + SM)
    dlc_proc = Process(target=dlc_process,
                       args=(dlc_queue, pose_queue, pose_display_queue, dlc_running,
                             pose_sm_queue))
    dlc_proc.start()

    # Start sensor grabbing (also handles outbound commands)
    sensor_proc = Process(target=sensor_process, args=(sensor_array, timestamp_value, command_queue))
    sensor_proc.start()

    # Start beamer projector (fullscreen window on the beamer's extended display)
    beamer_proc = Process(target=beamer_process, args=(beamer_queue, beamer_running))
    beamer_proc.start()

    # Start state machine (idles until ExperimentPage activates it)
    sm_proc = Process(
        target=state_machine_process,
        args=(sm_active, sm_stop, sm_running, command_queue,
              sensor_array, protocol_queue, session_done,
              pose_sm_queue, beamer_queue),
    )
    sm_proc.start()

    # Start GUI — pass shared data objects so ExperimentPage can wire saving + SM
    data_sources = {
        "frame_queue":        frame_queue,
        "sensor_array":       sensor_array,
        "dlc_queue":          dlc_queue,
        "pose_queue":         pose_queue,
        "pose_display_queue": pose_display_queue,
        "timestamp_value":    timestamp_value,
        "sm_active":          sm_active,
        "sm_stop":            sm_stop,
        "session_done":       session_done,
        "protocol_queue":     protocol_queue,
        "beamer_queue":       beamer_queue,
    }
    run_gui(frame_queue, sensor_array, cam_shape, command_queue, data_sources)

    # ── Clean up after GUI closes ─────────────────────────────────────────────
    # State machine: signal clean exit, then escalate.
    sm_running.value = False
    sm_stop.value    = True
    sm_proc.join(timeout=2)
    if sm_proc.is_alive():
        sm_proc.terminate()
        sm_proc.join(timeout=1)
    if sm_proc.is_alive():
        sm_proc.kill()
        sm_proc.join()

    # DLC: signal clean exit first, then escalate.
    dlc_running.value = False
    dlc_proc.join(timeout=3)
    if dlc_proc.is_alive():
        dlc_proc.terminate()
        dlc_proc.join(timeout=1)
    if dlc_proc.is_alive():
        dlc_proc.kill()
        dlc_proc.join()

    # Camera: signal the loop to exit cleanly so Pylon releases the USB device
    # before we force-kill.  Give it 3 s; escalate to SIGTERM then SIGKILL.
    camera_running.value = False
    cam_proc.join(timeout=3)
    if cam_proc.is_alive():
        cam_proc.terminate()
        cam_proc.join(timeout=1)
    if cam_proc.is_alive():
        cam_proc.kill()
        cam_proc.join()

    # Sensor: no clean-exit flag, so go straight to SIGTERM then SIGKILL.
    sensor_proc.terminate()
    sensor_proc.join(timeout=2)
    if sensor_proc.is_alive():
        sensor_proc.kill()
        sensor_proc.join()

    # Beamer: signal the Qt loop to quit so the fullscreen window closes cleanly,
    # then escalate to SIGTERM then SIGKILL.
    beamer_running.value = False
    beamer_proc.join(timeout=3)
    if beamer_proc.is_alive():
        beamer_proc.terminate()
        beamer_proc.join(timeout=1)
    if beamer_proc.is_alive():
        beamer_proc.kill()
        beamer_proc.join()

if __name__ == "__main__":
    import multiprocessing as mp
    mp.set_start_method("spawn", force=True)
    main()