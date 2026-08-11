from pathlib import Path

### Serial Controls

# Addressed by USB serial number (the /dev/serial/by-id symlinks), not by
# /dev/ttyACM*: those are handed out in plug order, so replugging the boards can
# swap them and send every lickport 1-8 command to the board wired to 9-16 with
# no error anywhere. Same reasoning as camera_serial below.
# Run `ls -l /dev/serial/by-id/` to read these off after swapping a board.
serial_ports = [
    '/dev/serial/by-id/usb-Arduino__www.arduino.cc__0042_9503531393535160B051-if00',  # board 1, lickports 1-8
    '/dev/serial/by-id/usb-Arduino__www.arduino.cc__0042_9503531393535190A061-if00',  # board 2, lickports 9-16
]
# Maps lickport id -> (Arduino board, board-local channel). BNC ids use a
# separate mapping in SerialControls._BNC_MAP, not this table.
lookup_tables= [{1:1,2:3,3:5,4:2,5:6,6:7,7:8,8:4},{1:16,2:13,3:15,4:9,5:12,6:11,7:10,8:14}]
# Log every serial command sent/received. Off by default — noisy at BNC train rates.
serial_verbose = False

### Camera controls
# Two Basler cameras are connected; pick the tracking camera by serial number
# so USB enumeration order can't select the wrong one.
#   40609610 -> daA3840-45uc  (3840x2160, the arena camera used for tracking)
#   40458776 -> daA1440-220um (the other camera)
camera_serial = "40609610"
# Native resolution of the selected camera (height, width).
IMG_HEIGHT, IMG_WIDTH = 2160, 3840
# Exposure is pinned to a whole multiple of the beamer's 60 Hz refresh
# (16.667 ms) to avoid rolling-shutter flicker when the beamer is on; keep
# auto-exposure off. Valid values (us): 16667, 33333, 50000, 100000.
#
# Exposure also sets a hard ceiling on frame rate, since a frame can never be
# shorter than its exposure: 50000 us caps the camera at 19.75 fps, which is why
# reaching a true 20 fps requires 33333 here. Gain compensates the light lost by
# the shorter exposure (+3.5 dB ~ the 1.5x drop); the node's range is 0-48 dB.
camera_exposure_us = 33333
camera_gain        = 23.5

# Frame rate, regulated by the camera's own clock (AcquisitionFrameRate) rather
# than left free-running, so the rate is exact and constant. Must be <= what the
# exposure and throughput limit below allow — the camera can only slow itself
# down, so asking for more than the free-running rate silently does nothing.
# Kept equal to video_fps and data_saving.SAMPLE_HZ: one CSV row per frame.
camera_frame_rate = 20.0

# Cap on how fast the camera pushes pixel data onto the USB3 link, in bytes/s.
# The camera enforces it by stretching readout: readout ~ frame_bytes / limit,
# and the frame period is max(exposure, readout). At the camera's 160 MB/s
# default a 3840x2160 Mono8 frame (8.29 MB) takes 52 ms to read out, capping the
# rig at 18.7 fps regardless of exposure. Range is 0.5-400 MB/s; the link itself
# carries ~500 MB/s. Do not raise this to the maximum without checking the whole
# USB3 bus: the limit exists so several cameras can share one controller, and
# the second Basler needs its share when it is plugged in.
camera_throughput_limit = 300_000_000
# Crop of the full sensor frame (y0, y1, x0, x1), used for both the live
# preview and the DLC tracking input. DLC pose coordinates are relative to
# this crop, not the full sensor.
DLC_CROP = (100, 2100, 1000, 3000)   # -> 2000 x 2000

### Camera lens correction
# The lens is a fisheye; the camera process undistorts every frame up front so
# DLC, the saved video, and the preview all share one corrected geometry.
# See camera_calibration.py for how the calibration is measured and applied.
undistort_threads = 4   # threads OpenCV uses for the undistort remap
# Zoom applied to the undistorted image. Undistorting pushes the image edges
# outward, so a value below 1.0 shrinks the result back to fit the frame (at
# the cost of some resolution). Re-run the beamer calibration after changing
# this — it changes the arena's apparent scale.
undistort_zoom = 0.80
camera_calibration_path = str(Path(__file__).resolve().parent / "config" / "camera_calibration.json")  # machine-specific, git-ignored

### Speaker
# ALSA output device for tone playback, see speaker_controls.py. "default"
# uses the system's current output; set e.g. "plughw:0,0" to force a specific device.
speaker_device = "default"

### Control monitor
# Fixed (x, y, width, height) rect where the main GUI window opens, given as a
# position on the X virtual desktop rather than a screens() index like the
# beamer/touch screens below — display indices can reshuffle on replug, and
# the GUI must reliably open somewhere the experimenter can reach.
control_screen_geometry = (0, 0, 1920, 1200)

### Beamer
# The beamer is an HDMI display. beamer_screen_index selects which
# QApplication.screens() entry the projection window opens on. Current layout:
#   0 = control monitor, 1 = touch screen, 2 = beamer, 3 = touch screen
# Re-check these indices after any cabling change. The cm<->pixel scale used
# for projection is measured by the calibration wizard and saved to
# beamer_calibration_path; beamer_lens_distance_cm is informational only.
beamer_screen_index     = 2
beamer_lens_distance_cm = 196
beamer_calibration_path = str(Path(__file__).resolve().parent / "config" / "beamer_calibration.json")

### Pumps
# Rewards are specified as a volume (uL) in the protocol and delivered as a
# train of short pulses (one per lick), using each pump's uL/pulse from
# pump_calibration.py.

# Pump-on duration per pulse. A longer pulse shoots liquid across the arena
# instead of forming a droplet, so don't change this casually — doing so
# invalidates every existing pump calibration.
pump_pulse_ms = 10

# Minimum time between pulses. This is a hard floor, not a tuning knob: the
# Arduino only reports sensor state every 100ms, so anything faster would fire
# multiple pulses off a single stale sensor reading.
pump_refractory_ms = 150

# A reward is marked "partial" if no lick arrives within this many seconds, so
# an animal that walks away mid-reward doesn't block the session.
pump_delivery_timeout_s = 10.0

# Safety ceiling on pulses per reward, in case a calibration would otherwise
# ask for more licking than an animal can do in one sitting.
pump_max_pulses = 100

# Per-pump uL/pulse, written by the calibration wizard (Cleaning/Testing tab).
# Machine-specific, git-ignored.
pump_calibration_path = str(Path(__file__).resolve().parent / "config" / "pump_calibration.json")

### Screens
# QApplication.screens() indices of the two touch-screen displays, in order
# [Screen 1, Screen 2]. An index that doesn't exist just leaves that pattern
# window hidden instead of covering another display.
screen_indices = [1, 3]

### Data Handling
# Fallback Data/Protocols folders. The real paths are user-set in the GUI
# Settings dialog and stored in config/config.json — use the accessor
# functions below rather than caching them, since they can change at runtime.
DEFAULT_DATA_PATH      = str(Path(__file__).resolve().parent / "Data")
DEFAULT_PROTOCOLS_PATH = str(Path(__file__).resolve().parent / "Protocols")


def get_data_path():
    """Return the folder where recordings are saved."""
    # Imported locally: shared_states loads at startup for every process, and
    # rspace pulls in the `requests` package with it.
    import rspace
    return rspace.load_setting("data_path", DEFAULT_DATA_PATH)


def recording_basename(mouse_id, session_id, when=None):
    """Build the shared filename prefix for one recording.

    Every file a recording produces (CSV, video, console log, ...) shares
    this "<mouse>_<date>_<time>_<session>" prefix and lives directly in the
    Data folder — there are no per-mouse or per-session subfolders. Mouse and
    session ids may themselves contain underscores, so don't recover them by
    splitting this string; use mouse_log_path() instead.
    """
    from datetime import datetime
    when = when or datetime.now()
    return f"{mouse_id}_{when.strftime('%Y%m%d_%H%M%S')}_{session_id}"


def mouse_log_path(mouse_id):
    """Path to a mouse's session log (Data/<mouse>.json) — the authoritative
    record of which sessions exist for that mouse."""
    import os
    return os.path.join(get_data_path(), f"{mouse_id}.json")


def get_protocols_path():
    """Return the folder holding protocol JSON files."""
    import rspace
    return rspace.load_setting("protocols_path", DEFAULT_PROTOCOLS_PATH)
# How often buffered sensor/DLC/actuator rows are flushed to the session CSV.
# Lower = safer against crashes, higher = fewer disk writes. Video has its own
# chunking (video_chunk_seconds) and is unaffected by this.
save_chunk_seconds = 30

### Video
# The camera process encodes its own video (H.264 via ffmpeg) in chunks, so a
# crash loses at most one chunk — see video_writer.py. Video files share the
# recording's flat filename prefix, e.g. {prefix}_Video_0001.mp4.

# Frame rate stamped into the video file. Tied to camera_frame_rate so the MP4
# plays at true speed — stamping a rate the camera does not deliver makes the
# video play fast or slow. True per-frame timestamps are still saved to
# {prefix}_frames.csv, which is what aligns video to the session CSV.
video_fps            = camera_frame_rate
# The two levers on file size, measured on this rig at 20 fps as a multiple of
# the current (1000 px, crf 23) setting. Absolute sizes depend heavily on how
# much of the arena is lit and moving — a dark arena costs almost nothing — so
# treat these as ratios:
#
#     max_size  crf    relative size
#         1000   23        1.00   (current)
#          750   23        0.50
#          500   23        0.13
#         1000   28        0.30
#         1000   32        0.04
#          500   28        0.02
#
# crf is the more efficient lever: it drops detail the eye barely sees, whereas
# halving max_size halves the resolution available for scoring behaviour later.
# Neither affects DeepLabCut, which tracks the full-resolution frames in memory
# and never reads the saved file.

# libx264 quality (Constant Rate Factor), not a bitrate — lower is higher
# quality and a bigger file. ~18 is visually lossless, 23 is default, 28 is
# visibly soft.
video_crf            = 28
# Longest side of the saved video in pixels (0 = camera's native size), i.e. how
# far the 2000x2000 arena crop is downscaled before encoding.
video_max_size       = 1000
video_chunk_seconds  = 60
# Leave empty to auto-detect ffmpeg (next to the Python interpreter, then
# PATH). Set only if the encoder lives somewhere else.
ffmpeg_path          = ""
# Join video chunks into one file when the session stops; chunks are deleted
# only after the join succeeds.
video_concat_on_stop = True

### Deeplabcut
model_path = "/home/admin1/Documents/GitHub/Multiport_Lickport/Multiport_Code/DLCModel"

# Scale applied to the frame before inference (1.0 = the full DLC_CROP, 2000x2000).
# Inference cost is proportional to pixel area, so this is the one lever on
# tracking rate: at 1.0 a ResNet-50 pose takes ~160 ms (6 Hz, a pose on only
# every third frame), at 0.5 about 42 ms — fast enough to pose every frame at
# camera_frame_rate. DLCLive scales the returned coordinates back up, so poses
# stay in full DLC_CROP pixels whatever this is set to and nothing downstream
# changes. The cost is keypoint precision, which is worth re-checking against a
# newly trained model: a body-part model tolerates far more downscaling than one
# resolving something small like a pupil.
dlc_resize = 0.5

# Likelihood at or above which a keypoint is drawn solid in the live camera
# preview. Keypoints below it are still drawn, as faint hollow rings, so the
# overlay shows where the model is guessing instead of going blank — a blank
# overlay is indistinguishable from DLC having crashed. Raise this once a model
# trained on this arena makes 0.5 a meaningful bar again.
dlc_overlay_min_likelihood = 0.5
