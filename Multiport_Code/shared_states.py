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
    """Folder holding the recordings, as Data/<mouse>/<session>/<timestamp>_<session>/."""
    # Imported here rather than at module scope: shared_states is pulled in by every
    # process at startup, and rspace drags in `requests`.
    import rspace
    return rspace.load_setting("data_path", DEFAULT_DATA_PATH)


def get_protocols_path():
    """Folder holding the protocol JSON files."""
    import rspace
    return rspace.load_setting("protocols_path", DEFAULT_PROTOCOLS_PATH)
# Sensor/DLC rows are buffered in RAM and appended to the session CSV this often.
# A crash therefore costs at most this many seconds of table data — lower it for
# more safety, raise it for fewer disk writes. Camera frames are unaffected: they
# are written to Image_Arrays/ as they arrive.
save_chunk_seconds = 30

### Deeplabcut
model_path = "/home/admin1/Documents/GitHub/Multiport_Lickport/Multiport_Code/DLCModel"