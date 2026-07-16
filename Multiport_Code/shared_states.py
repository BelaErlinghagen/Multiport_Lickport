### Serial Controls

serial_ports = ['/dev/ttyACM0', '/dev/ttyACM1']
lookup_tables= [{1:1,2:3,3:5,4:2,5:6,6:7,7:8,8:4},{1:16,2:13,3:15,4:9,5:12,6:11,7:10,8:14}]

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

### Beamer
# The beamer is an HDMI-connected extended display. beamer_screen_index selects
# which QApplication.screens() entry the projection window opens on (0 is usually
# the control monitor, 1 the beamer). The cm/pixel scale is measured empirically
# by the calibration wizard and written to beamer_calibration_path — the lens
# distance is stored for reference only.
beamer_screen_index     = 1
beamer_lens_distance_cm = 196
beamer_calibration_path = "/home/admin1/Documents/GitHub/Multiport_Lickport/Multiport_Code/beamer_calibration.json"

### Data Handling
data_path      = "/home/admin1/Documents/GitHub/Multiport_Lickport/Multiport_Code/Data"
protocols_path = "/home/admin1/Documents/GitHub/Multiport_Lickport/Multiport_Code/Protocols"

### Deeplabcut
model_path = "/home/admin1/Documents/GitHub/Multiport_Lickport/Multiport_Code/DLCModel"