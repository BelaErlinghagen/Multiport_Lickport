from pathlib import Path

### Serial Controls

serial_ports = ['/dev/ttyACM0', '/dev/ttyACM1']
# Lickport id → (board, board-local channel). BNC ids do NOT go through this — they
# address the four connectors directly, see SerialControls._BNC_MAP.
lookup_tables= [{1:1,2:3,3:5,4:2,5:6,6:7,7:8,8:4},{1:16,2:13,3:15,4:9,5:12,6:11,7:10,8:14}]
# Per-command serial tracing ("Processing: …" / "Sending to …"). Off by default: it
# is two console lines per command, so a BNC train at 10 Hz would bury everything
# else — and those lines now land in the session console log too.
serial_verbose = False

### Camera controls
# Two Basler cameras are connected to the PC; select the tracking camera by its
# serial number so enumeration order can't pick the wrong one.
#   40609610 -> daA3840-45uc  (3840×2160, the 4K arena camera — use this)
#   40458776 -> daA1440-220um (1440×1080, the other camera)
camera_serial = "40609610"
# Native resolution of the selected sensor (height, width).
IMG_HEIGHT, IMG_WIDTH = 2160, 3840
# Anti-flicker: pin exposure to a whole multiple of the beamer's 60 Hz period
# (16.667 ms) so the rolling-shutter rows integrate whole light cycles → no
# scrolling dark bands. Auto-exposure must stay OFF, or it drifts off a clean
# multiple when the beamer brightens the scene. Clean values (µs):
#   16667 (≤60 fps), 33333 (≤30 fps), 50000 (≤20 fps), 100000 (≤10 fps, brightest).
camera_exposure_us = 50000
camera_gain        = 20.0
# Arena focus crop of the 4K frame (y0, y1, x0, x1). Both the live preview and the
# DLC input use this region, so DLC pose coordinates are in this cropped frame —
# normalise by its width/height (not IMG_HEIGHT) to get 0–1 fractions that match
# the beamer calibration feed.
DLC_CROP = (100, 2100, 1000, 3000)   # → 2000 × 2000

### Speaker
# ALSA device that speaker_controls.py plays tones through (via `aplay -D`).
# "default" uses the system's current output; set it to "plughw:0,0" to force the
# analog/headphone jack (card 0, device 0) — e.g. if the default output is the
# HDMI beamer instead of the speakers.
speaker_device = "default"

### Control monitor
# Where the main GUI window opens, as a hardcoded (x, y, width, height) rect on
# the X virtual desktop — the control monitor's position in `xrandr
# --listmonitors`, i.e. DP-0 at +0+0. It is deliberately *not* a screens() index
# like the beamer/touch screens below: those indices reshuffle when a display is
# re-plugged or comes up in a different order, and the GUI is the one window the
# experimenter must always be able to reach. Without this GNOME places the window
# on whichever monitor the pointer happens to be on, which on this rig means the
# GUI opens across the two 800×480 touch panels.
#
# The window is fitted to the work area of whatever display sits at this position
# (so the GNOME bar and dock stay reachable); if no display is there, the rect is
# used as-is.
control_screen_geometry = (0, 0, 1920, 1200)

### Beamer
# The beamer is an HDMI-connected extended display. beamer_screen_index selects
# which QApplication.screens() entry the projection window opens on. The current
# layout (`xrandr --listmonitors`) is:
#   0 = DP-0   1920×1200  control monitor
#   1 = DP-3    800×480   touch screen
#   2 = HDMI-0 1920×1080  beamer
#   3 = DP-5    800×480   touch screen
# Plugging displays in/out reshuffles these indices, so re-check them after any
# cabling change (the beamer must stay on a 1920×1080 display or the saved
# px_per_cm no longer applies). The cm/pixel scale is measured empirically by the
# calibration wizard and written to beamer_calibration_path — the lens distance
# is stored for reference only.
beamer_screen_index     = 2
beamer_lens_distance_cm = 196
beamer_calibration_path = str(Path(__file__).resolve().parent / "config" / "beamer_calibration.json")

### Pumps
# Rewards are set as a *volume* (µL) in the protocol, not as a pump-on duration. The
# volume is delivered as a train of short pulses, one per lick, using the per-pump
# µL/pulse measured by the calibration wizard (see pump_calibration.py).
#
# pump_pulse_ms is the whole point of the scheme: energising a pump for longer makes
# it shoot the liquid across the arena instead of forming a droplet at the cannula.
# Changing it invalidates every measurement on disk — a 10 ms energisation is
# dominated by the pump's startup transient, so the volume per pulse is not
# proportional to the pulse width. PumpCalibration warns if the file disagrees.
pump_pulse_ms = 10

# HARD FLOOR, not a tuning knob. The firmware sends STATUS only every 100 ms
# (PortMasterArduinoCode.ino STATUS_INTERVAL) and SerialControls._reader_loop only
# updates its state when a STATUS line arrives, so sensor_array is a 10 Hz
# sample-and-hold: the state machine's 20 ms poll re-reads the same held value five
# times. A refractory below ~100 ms therefore fires several pulses off a *single*
# sensor sample and the lick gating stops meaning anything. 150 ms = 100 ms STATUS
# plus reader/writer/poll jitter.
#
# It is also what keeps pulses countable in the session CSV: _ActuatorTracker widens
# each 10 ms pulse to 60 ms (_MIN_LATCH_S) and the CSV samples at 50 ms, so the 90 ms
# gap guarantees at least one "0" row between two pulses. At 100 ms spacing adjacent
# pulses merge into one run.
pump_refractory_ms = 150

# A reward is closed out as "partial" when this long passes with no pulse. Without it
# a mouse that licks once and walks away leaves the port mid-delivery forever, and an
# "all rewards collected" trial in a *trials*-type session has no session deadline to
# fall back on — the trial loop would spin until the user hits stop.
pump_delivery_timeout_s = 10.0

# Ceiling on the pulses one reward may take, whatever the calibration says. At the
# refractory above this is ~15 s of uninterrupted licking, already longer than a
# typical bout; a weak pump plus a large volume would otherwise silently ask for a
# reward the animal cannot physically collect.
pump_max_pulses = 100

# Per-pump µL/pulse, written by the calibration wizard on the Cleaning/Testing tab.
# Machine-specific, so it is git-ignored like the beamer calibration.
pump_calibration_path = str(Path(__file__).resolve().parent / "config" / "pump_calibration.json")

### Screens
# The two Raspberry Pi touch screens are wired to the PC as extra displays.
# screen_indices are the QApplication.screens() indices the two fullscreen pattern
# windows open on, in order: [Screen 1, Screen 2]. On the current layout they are
# the two 800×480 panels, DP-3 and DP-5 (see the beamer note above for the full
# display list). An index that doesn't exist leaves that window hidden rather than
# covering the control monitor.
screen_indices = [1, 3]

### Data Handling
# The Data and Protocols folders are set by the user in the GUI (Settings…) and
# stored in config/config.json; the values below are only the defaults used until
# one is chosen. Always call the accessors instead of caching their result — the
# folders can be changed while the app is running, and a spawned process reads the
# file fresh.
DEFAULT_DATA_PATH      = str(Path(__file__).resolve().parent / "Data")
DEFAULT_PROTOCOLS_PATH = str(Path(__file__).resolve().parent / "Protocols")


def get_data_path():
    """Folder holding the recordings. Every file lives directly in here — see
    recording_basename() for the naming scheme that replaces the folder tree."""
    # Imported here rather than at module scope: shared_states is pulled in by every
    # process at startup, and rspace drags in `requests`.
    import rspace
    return rspace.load_setting("data_path", DEFAULT_DATA_PATH)


def recording_basename(mouse_id, session_id, when=None):
    """The prefix every file of one recording shares: <mouse>_<date>_<time>_<session>.

    Recordings are stored flat: the Data folder holds no per-mouse or per-session
    sub-folders, so the filename alone has to say which mouse, when, and which
    session a file belongs to. One recording produces, for example:

        BECU371_20260807_113631_Test2_Data.csv
        BECU371_20260807_113631_Test2_console.log
        BECU371_20260807_113631_Test2_Video.mp4
        BECU371_20260807_113631_Test2_frames.csv

    Note the ids are *not* recoverable by splitting on "_" — a mouse or session id
    may itself contain underscores. Anything that needs to enumerate mice or
    sessions must read <mouse>.json instead (see mouse_log_path), never parse these
    names.
    """
    from datetime import datetime
    when = when or datetime.now()
    return f"{mouse_id}_{when.strftime('%Y%m%d_%H%M%S')}_{session_id}"


def mouse_log_path(mouse_id):
    """Path of a mouse's session log, Data/<mouse>.json.

    Per-mouse rather than per-recording, so it carries no timestamp — and it is the
    authoritative list of which mice and sessions exist, now that the folder tree is
    gone.
    """
    import os
    return os.path.join(get_data_path(), f"{mouse_id}.json")


def get_protocols_path():
    """Folder holding the protocol JSON files."""
    import rspace
    return rspace.load_setting("protocols_path", DEFAULT_PROTOCOLS_PATH)
# Sensor/DLC/actuator rows are buffered in RAM and appended to the session CSV this
# often. A crash therefore costs at most this many seconds of table data — lower it
# for more safety, raise it for fewer disk writes. Video is unaffected: it has its
# own chunking, see video_chunk_seconds below.
save_chunk_seconds = 30

### Video
# The camera stream is encoded to H.264 by the camera process itself (ffmpeg, from
# the pixi env) and written to <recording>/Video/ as video_%04d.mp4 chunks. Each
# chunk is finalized as the next one begins, so a crash costs at most one chunk.
#
# video_fps is only the *nominal* rate stamped into the MP4 — frames are pushed at
# whatever rate the camera delivers them. The true per-frame times are written to
# Video/video_frames.csv, which is what aligns the video with the session CSV, so a
# wrong value here makes playback speed off but never loses information. Keep it in
# step with camera_exposure_us (50000 µs caps the sensor at 20 fps).
video_fps            = 20
# libx264's quality dial (Constant Rate Factor). It is NOT a bitrate: the encoder
# spends whatever bits each frame needs to hit this quality, so a still scene costs
# little and a noisy one costs a lot. Lower = better and bigger, in steps of about
# ±6 = ×2 file size. 18 ≈ visually lossless, 23 default, 28 visibly soft.
video_crf            = 23
# Longest side of the saved video, in pixels (0 = save at the camera's own size).
# This is by far the biggest lever on file size, and it is nearly free: the frames
# are cropped to 2000x2000, and at gain 20 most of those pixels are sensor grain,
# which is expensive to encode. Halving to 1000 averages four noisy pixels into one
# clean one, so the file shrinks ~13x (≈20.7 → ≈1.6 GB/hour, measured on a real
# recording) rather than the ~4x the pixel count alone would suggest.
# DeepLabCut is unaffected — it runs live on the full-resolution frames and never
# reads the video. This only changes what you see when re-watching a session.
video_max_size       = 1000
video_chunk_seconds  = 60
# Normally left empty: video_writer finds ffmpeg next to the running interpreter
# (i.e. inside the pixi env) and then on PATH. Set an absolute path here only if the
# encoder lives somewhere else.
ffmpeg_path          = ""
# Join the chunks into one <recording_id>_Video.mp4 when the session stops. The
# chunks are deleted only once the (lossless, stream-copy) join has succeeded.
video_concat_on_stop = True

### Deeplabcut
model_path = "/home/admin1/Documents/GitHub/Multiport_Lickport/Multiport_Code/DLCModel"