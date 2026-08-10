"""Entry point. Builds the shared queues and state, launches every process
(camera, DeepLabCut, serial/Arduino, beamer, touch screens, state machine,
GUI), then tears them down in order when the GUI window closes."""
import time
from multiprocessing import Process, Array, Queue, Value
import numpy as np
from beamer_controls import beamer_process
from camera_controls import camera_process
from deeplabcut_controls import dlc_process
from hardware_state import HardwareState
from main_plotting import run_gui
from screen_controls import screen_process
from serial_controls import sensor_process
from state_machine import state_machine_process


def _wait_for_exit(children, grace, label):
    """Join already-signalled children in parallel, then escalate as one wave.

    Every child here has already been told to stop, so they wind down at the
    same time and the wait is as long as the slowest one rather than the sum
    of all of them. Escalation is per wave for the same reason.
    """
    deadline = time.monotonic() + grace
    for _name, proc in children:
        proc.join(timeout=max(0.0, deadline - time.monotonic()))

    stragglers = [(name, proc) for name, proc in children if proc.is_alive()]
    if not stragglers:
        return
    print(f"[main] {label}: still running after {grace:.0f}s, terminating "
          f"{', '.join(name for name, _ in stragglers)}")
    for _name, proc in stragglers:
        proc.terminate()

    deadline = time.monotonic() + 1.5
    for _name, proc in stragglers:
        proc.join(timeout=max(0.0, deadline - time.monotonic()))

    for name, proc in stragglers:
        if proc.is_alive():
            print(f"[main] {label}: killing {name}")
            proc.kill()
            proc.join()


def main():
    # Must happen before any child process starts: children inherit fds 1/2,
    # which is how their output ends up on the shared console-log pipe.
    import console_log
    console_log.install_capture()

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
    # Generous headroom: a dropped command is a silent missed TTL pulse or a pump
    # that never fires. sensor_process normally drains this quickly, but bursts
    # like an ALL OFF command need room to queue up.
    command_queue      = Queue(maxsize=1000) # GUI → Arduino commands
    beamer_queue       = Queue(maxsize=8)   # GUI/SM → beamer projection commands
    beamer_running     = Value('b', True)
    screen_queue       = Queue(maxsize=8)   # GUI/SM → touch-screen pattern commands
    screen_running     = Value('b', True)

    # Actuator state (pumps, LEDs, BNCs, beamer, screens, speaker), shared by
    # every process and read by the saver for the session CSV. Passed via
    # Process(args=...) rather than a Queue, since it must be inherited.
    hw = HardwareState()

    # Session video: the camera process encodes its own frames, so the GUI only
    # needs to hand it an on/off flag and the recording path to write to.
    video_running = Value('b', False)
    video_path    = Array('c', 512)

    # Lens-correction flags for the camera process. The calibration wizard turns
    # undistort_enabled off while it captures raw frames, then sets
    # undistort_reload so the camera process picks up what it just saved.
    undistort_enabled = Value('b', True)
    undistort_reload  = Value('b', False)

    # State-machine control flags
    sm_active      = Value('b', False)   # set True by ExperimentPage to start a session
    sm_stop        = Value('b', False)   # set True for an emergency stop
    sm_running     = Value('b', True)    # set False on shutdown to exit the SM process
    session_done   = Value('b', False)   # SM sets True on natural session end
    protocol_queue = Queue(maxsize=1)    # ExperimentPage puts protocol dict here before sm_active=True

    # Feeds the GUI's session progress bar: the state machine posts the session
    # start time once and the trial number at each trial start, and the GUI
    # interpolates elapsed/remaining time on its own between updates.
    sm_session_start = Value('d', 0.0)
    sm_trial         = Value('i', 0)

    # Start camera
    cam_proc = Process(target=camera_process,
                       args=(frame_queue, dlc_queue, cam_shape, camera_running,
                             video_running, video_path,
                             undistort_enabled, undistort_reload))
    cam_proc.start()

    # Start DLC inference (runs continuously; results go to saving + GUI overlay + SM)
    dlc_proc = Process(target=dlc_process,
                       args=(dlc_queue, pose_queue, pose_display_queue, dlc_running,
                             pose_sm_queue))
    dlc_proc.start()

    # Start sensor process: reads the lick sensors and drains command_queue to
    # send outbound Arduino commands, mirroring each one into `hw` for the CSV —
    # this is the one process every hardware command passes through.
    sensor_proc = Process(target=sensor_process,
                          args=(sensor_array, timestamp_value, command_queue, hw))
    sensor_proc.start()

    # Start beamer projector (fullscreen window on the beamer's extended display)
    beamer_proc = Process(target=beamer_process, args=(beamer_queue, beamer_running))
    beamer_proc.start()

    # Start the touch screens (one fullscreen pattern window per HDMI screen)
    screen_proc = Process(target=screen_process, args=(screen_queue, screen_running))
    screen_proc.start()

    # Start state machine (idles until ExperimentPage activates it)
    sm_proc = Process(
        target=state_machine_process,
        args=(sm_active, sm_stop, sm_running, command_queue,
              sensor_array, protocol_queue, session_done,
              pose_sm_queue, beamer_queue, screen_queue, hw,
              (sm_session_start, sm_trial)),
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
        "screen_queue":       screen_queue,
        "hw":                 hw,
        "video_running":      video_running,
        "video_path":         video_path,
        "undistort_enabled":  undistort_enabled,
        "undistort_reload":   undistort_reload,
        "sm_session_start":   sm_session_start,
        "sm_trial":           sm_trial,
    }
    run_gui(frame_queue, sensor_array, cam_shape, command_queue, data_sources)

    # ── Clean up after GUI closes ─────────────────────────────────────────────
    # Tell everything to stop before waiting on anything: each process polls its
    # flag every few ms, so signalling them all up front lets them shut down in
    # parallel. Signalling one process only after the previous one has been
    # joined is what used to make closing the GUI take the sum of every timeout.
    sm_running.value     = False
    sm_stop.value        = True
    dlc_running.value    = False
    camera_running.value = False
    beamer_running.value = False
    screen_running.value = False

    # The state machine goes first and on its own: its last act is to switch its
    # actuators off, and those commands still have to travel through the sensor
    # process, which is killed below. The others are already winding down while
    # this waits, so it costs no extra wall-clock time.
    _wait_for_exit([("state machine", sm_proc)], grace=2.0, label="state machine")
    time.sleep(0.2)   # let sensor_process drain the last commands to the Arduinos

    # Sensor: no clean-exit flag, so SIGTERM is how it stops.
    sensor_proc.terminate()

    _wait_for_exit(
        [("DLC",     dlc_proc),
         ("sensor",  sensor_proc),
         ("beamer",  beamer_proc),
         ("screens", screen_proc)],
        grace=3.0, label="shutdown")

    # The camera is joined last and on a much longer leash: if a recording was
    # still running it is finalising the session's video here, and ffmpeg has to
    # drain a frame backlog before the file is playable. The grace is a ceiling,
    # not a wait — with no video to finish the camera exits in well under a
    # second, so this costs an ordinary shutdown nothing.
    _wait_for_exit([("camera", cam_proc)], grace=60.0, label="camera")

    # Last of all: with every child gone, nothing can still be writing into the
    # capture pipe, so console_log's pump thread can see EOF, drain the tail,
    # and close the log.
    console_log.shutdown()

if __name__ == "__main__":
    import multiprocessing as mp
    mp.set_start_method("spawn", force=True)
    main()
