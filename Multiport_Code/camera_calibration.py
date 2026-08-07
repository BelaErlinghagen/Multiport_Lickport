"""camera_calibration.py — the tracking camera's fisheye lens correction.

The arena camera looks through a fisheye lens, so a straight line in the arena is a
curve in the image and a centimetre near the wall covers fewer pixels than one in the
middle. Everything geometric downstream assumes the opposite: BeamerCalibration maps
camera coordinates to beamer pixels with a 3-point *affine*, and the state machine's
ITI dwell test asks "is the mouse inside the projected target" through that mapping.
Uncorrected, that behavioural criterion is wrong by an amount that depends on where in
the arena the mouse happens to be — the worst kind of error, because it looks like data.

The correction is applied to the *image*, once, in the camera process, fused into the
crop that was happening there anyway (see camera_controls.run). Everything that reads a
frame — DeepLabCut, the MP4, the live preview, and therefore every pose coordinate —
lives in the same corrected space, so nothing downstream needs to know this module
exists and no two consumers can drift apart.

Calibration is measured against the beamer's own projected dots rather than a printed
checkerboard: the projection disc is larger than the camera's view of it, so dots can be
placed across the whole frame including the corners (the wizard on the Cleaning/Testing
tab). The price of needing no target is that the projector's own (small) lens distortion
and any unevenness of the arena floor fold into the estimate — which is why every fit
stores the raw point pairs it came from, so it can be re-solved, and audited, later.

K and D are always stored in **full-sensor** coordinates, never in cropped ones. The
crop is a user setting (shared_states.DLC_CROP) and moving it must not silently
invalidate a calibration — the cropped/virtual camera matrix is derived at load time.

Like PumpCalibration and BeamerCalibration, this class never raises and never blocks
startup: a missing or unreadable file leaves is_calibrated False, the camera process
falls back to the plain crop, and the rig still runs.
"""

import json
import os
from datetime import datetime

import cv2
import numpy as np

import shared_states

# The two distortion models we fit. "fisheye" is OpenCV's equidistant model
# (cv2.fisheye, coefficients k1..k4), "radial" the standard plumb-bob polynomial
# (cv2.calibrateCamera, k1,k2,p1,p2,k3). Which one wins depends on the lens, so both
# are fitted and the better RMS is kept — a moderate wide angle is often described
# better by the polynomial, a true fisheye almost never is.
MODEL_FISHEYE = "fisheye"
MODEL_RADIAL = "radial"


class CameraCalibration:
    """Lens intrinsics + distortion, loaded from shared_states.camera_calibration_path."""

    def __init__(self, path=None):
        self.path = path or shared_states.camera_calibration_path
        self.model = None
        self.K = None                # 3×3, full-sensor pixels
        self.D = None                # distortion coefficients for `model`
        self.image_size = None       # (width, height) the K/D refer to
        self.rms_px = None
        self.method = None
        self.captured = None
        self._mtime = None
        self._maps = {}              # (crop, zoom) → (map1, map2)
        self.reload()

    # ── Loading ───────────────────────────────────────────────────────────────

    def reload(self, force=False):
        """Re-read the calibration JSON. Keeps the rig running on any failure.

        Cheap to call repeatedly: unless *force*, it returns immediately when the
        file's mtime has not moved, so the camera process can poll for a wizard run
        without re-parsing. Any successful read invalidates the cached maps, since
        they were built from the coefficients that just changed.
        """
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            mtime = None
        if not force and mtime == self._mtime:
            return
        self._mtime = mtime

        try:
            with open(self.path, "r") as fh:
                data = json.load(fh)
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}

        self._maps = {}
        self.model = None
        self.K = self.D = None
        self.image_size = None
        self.rms_px = None
        self.method = data.get("method")
        self.captured = data.get("captured")

        model = data.get("model")
        if model not in (MODEL_FISHEYE, MODEL_RADIAL):
            return
        try:
            K = np.asarray(data["camera_matrix"], dtype=np.float64).reshape(3, 3)
            D = np.asarray(data["dist_coeffs"], dtype=np.float64).reshape(-1, 1)
            w, h = (int(v) for v in data["image_size"])
        except Exception as exc:
            print(f"[CameraCalibration] ignoring {self.path}: unreadable "
                  f"calibration ({exc}).")
            return
        if not np.all(np.isfinite(K)) or not np.all(np.isfinite(D)) or w <= 0 or h <= 0:
            print(f"[CameraCalibration] ignoring {self.path}: non-finite coefficients.")
            return

        self.model = model
        self.K = K
        self.D = D
        self.image_size = (w, h)
        rms = data.get("rms_px")
        self.rms_px = float(rms) if rms is not None else None

        # A calibration measured on a different sensor resolution describes different
        # pixels. Loud, but not fatal: the numbers are still self-consistent, they
        # just no longer match what the camera is delivering.
        if (w, h) != (int(shared_states.IMG_WIDTH), int(shared_states.IMG_HEIGHT)):
            print(f"[CameraCalibration] WARNING: calibrated for {w}×{h} but the camera "
                  f"is configured as {shared_states.IMG_WIDTH}×{shared_states.IMG_HEIGHT}"
                  f" — recalibrate.")

    @property
    def is_calibrated(self) -> bool:
        return self.K is not None

    def describe(self) -> str:
        """One-line human summary, for the console log and the GUI."""
        if not self.is_calibrated:
            return "no lens calibration"
        rms = f"{self.rms_px:.2f} px RMS" if self.rms_px is not None else "RMS unknown"
        coeffs = ", ".join(f"{v:+.4f}" for v in self.D.ravel()[:4])
        return (f"{self.model} model, {rms}, method={self.method or '?'}, "
                f"f={self.K[0, 0]:.1f} px, D=({coeffs}), {self.captured or '?'}")

    # ── Virtual (undistorted) camera ──────────────────────────────────────────

    def virtual_matrix(self, crop, zoom=None):
        """The pinhole matrix the corrected image is rendered through.

        Focal length is scaled by *zoom* about the optical centre, and the principal
        point is shifted into crop coordinates, so the corrected frame keeps the same
        centre and (at zoom 1) the same scale at the centre as the raw one. Anything
        else would change the arena's apparent size and quietly invalidate the beamer
        calibration's px/cm every time this file is touched.

        Straightening barrel distortion pushes the periphery *outward*, so at zoom 1
        the corners of the raw view fall outside the frame and are lost. zoom < 1
        shrinks the corrected image until they fit (at the cost of black borders and
        a smaller arena on screen); zoom > 1 magnifies further. fit_zoom() computes
        the value that keeps everything.
        """
        y0, _y1, x0, _x1 = crop
        z = float(shared_states.undistort_zoom if zoom is None else zoom) or 1.0
        P = np.eye(3, dtype=np.float64)
        P[0, 0] = self.K[0, 0] * z
        P[1, 1] = self.K[1, 1] * z
        P[0, 2] = self.K[0, 2] - x0
        P[1, 2] = self.K[1, 2] - y0
        return P

    def fit_zoom(self, crop, margin=0.995):
        """The zoom at which the whole raw crop still fits in the corrected frame.

        Rectifying barrel distortion moves the periphery outward, so at zoom 1 the
        edges of the raw view are pushed off the frame — 20-odd percent of the field
        for a strong fisheye. This returns the scale that pulls all of it back in, for
        the wizard to report and the operator to put in shared_states.undistort_zoom.

        Exact rather than searched: the virtual matrix scales offsets from the
        principal point linearly, so a corrected point sits at P + zoom·(p₁ − P) and
        the limiting zoom is a min over the border samples.
        """
        if not self.is_calibrated:
            return 1.0
        y0, y1, x0, x1 = crop
        w, h = int(x1 - x0), int(y1 - y0)
        n = 64
        s = np.linspace(0, 1, n)
        border = np.vstack([
            np.column_stack([s * w, np.zeros(n)]),          # top
            np.column_stack([s * w, np.full(n, h - 1.0)]),   # bottom
            np.column_stack([np.zeros(n), s * h]),           # left
            np.column_stack([np.full(n, w - 1.0), s * h]),   # right
        ])
        P = self.virtual_matrix(crop, zoom=1.0)
        pts = self.undistort_points(border, crop)
        cx, cy = P[0, 2], P[1, 2]
        zooms = []
        for (x, y) in pts:
            dx, dy = x - cx, y - cy
            if dx > 1e-9:
                zooms.append((w - cx) / dx)
            elif dx < -1e-9:
                zooms.append(-cx / dx)
            if dy > 1e-9:
                zooms.append((h - cy) / dy)
            elif dy < -1e-9:
                zooms.append(-cy / dy)
        return float(min(zooms) * margin) if zooms else 1.0

    def field_note(self, crop):
        """One line on what the current zoom does to the field of view, or ''."""
        if not self.is_calibrated:
            return ""
        zoom = float(shared_states.undistort_zoom or 1.0)
        need = self.fit_zoom(crop)
        if need >= zoom - 0.005:
            return ""
        return (f"at zoom {zoom:g} the corrected frame keeps ~{100 * need / zoom:.0f}% "
                f"of the raw field — set shared_states.undistort_zoom = {need:.2f} "
                f"to keep all of it")

    def build_maps(self, crop, zoom=None):
        """Remap tables that crop *and* undistort in one pass, or None if uncalibrated.

        The maps are the size of the crop but index into the **full** sensor frame.
        Two reasons: cv2.remap's cost is set by the *output* size, so reading from the
        4K frame is free (measured at 11.1 ms single-threaded either way), and the
        maps are then free to sample outside the crop rectangle. Barrel distortion
        mostly reads inward so that rarely happens at zoom 1 — but at the zoom that
        keeps the full field (fit_zoom) it does, and cropping first would have thrown
        those pixels away before the remap could ask for them.

        CV_16SC2 (fixed-point) rather than float maps: half the memory traffic per
        frame for a quarter-pixel interpolation grid, which is far below the accuracy
        of anything this feeds.
        """
        if not self.is_calibrated:
            return None
        key = (tuple(crop), float(shared_states.undistort_zoom if zoom is None else zoom))
        if key in self._maps:
            return self._maps[key]

        y0, y1, x0, x1 = crop
        size = (int(x1 - x0), int(y1 - y0))       # (width, height)
        P = self.virtual_matrix(crop, zoom)
        if self.model == MODEL_FISHEYE:
            maps = cv2.fisheye.initUndistortRectifyMap(
                self.K, self.D, np.eye(3), P, size, cv2.CV_16SC2)
        else:
            maps = cv2.initUndistortRectifyMap(
                self.K, self.D, np.eye(3), P, size, cv2.CV_16SC2)
        self._maps[key] = maps
        return maps

    def preview_maps(self, crop, preview_shape, zoom=None):
        """Maps that rectify the GUI's downsampled preview, for a before/after view.

        The preview is a strided subsample of the crop, so its intrinsics are the
        crop's divided by that stride — the standard way to carry a calibration to a
        resized image (the distortion coefficients are dimensionless and unchanged).
        Only the wizard needs this; the live pipeline rectifies the full frame.
        """
        if not self.is_calibrated:
            return None
        y0, _y1, x0, _x1 = crop
        ph, pw = preview_shape[:2]
        sx, sy = preview_step(crop, preview_shape)
        K = self.K.copy()
        K[0, 0] /= sx
        K[1, 1] /= sy
        K[0, 2] = (self.K[0, 2] - x0) / sx
        K[1, 2] = (self.K[1, 2] - y0) / sy
        P = self.virtual_matrix(crop, zoom)
        P = np.array([[P[0, 0] / sx, 0, P[0, 2] / sx],
                      [0, P[1, 1] / sy, P[1, 2] / sy],
                      [0, 0, 1.0]])
        if self.model == MODEL_FISHEYE:
            return cv2.fisheye.initUndistortRectifyMap(
                K, self.D, np.eye(3), P, (pw, ph), cv2.CV_16SC2)
        return cv2.initUndistortRectifyMap(
            K, self.D, np.eye(3), P, (pw, ph), cv2.CV_16SC2)

    # ── Point transforms ──────────────────────────────────────────────────────

    def undistort_points(self, pts, crop):
        """Raw crop-pixel coordinates → corrected crop-pixel coordinates.

        The live pipeline does not need this (it corrects whole frames), but anything
        re-reading a *raw* recording does — and so does the wizard, which measures dots
        on uncorrected frames.
        """
        p = np.asarray(pts, dtype=np.float64).reshape(-1, 1, 2)
        if not self.is_calibrated or len(p) == 0:
            return np.asarray(pts, dtype=np.float64).reshape(-1, 2)
        y0, _y1, x0, _x1 = crop
        full = p + np.array([[[x0, y0]]], dtype=np.float64)   # → full-sensor pixels
        P = self.virtual_matrix(crop)
        if self.model == MODEL_FISHEYE:
            out = cv2.fisheye.undistortPoints(full, self.K, self.D, R=np.eye(3), P=P)
        else:
            out = cv2.undistortPoints(full, self.K, self.D, R=np.eye(3), P=P)
        return out.reshape(-1, 2)

    def distort_points(self, pts, crop):
        """Corrected crop-pixel coordinates → raw crop-pixel coordinates.

        The inverse of undistort_points, for drawing a corrected-space overlay onto a
        raw frame (the wizard's before/after view).
        """
        p = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
        if not self.is_calibrated or len(p) == 0:
            return p
        y0, _y1, x0, _x1 = crop
        P = self.virtual_matrix(crop)
        # Undo the virtual pinhole to get normalised camera coordinates, then push
        # them back through the real lens.
        norm = np.empty_like(p)
        norm[:, 0] = (p[:, 0] - P[0, 2]) / P[0, 0]
        norm[:, 1] = (p[:, 1] - P[1, 2]) / P[1, 1]
        if self.model == MODEL_FISHEYE:
            out = cv2.fisheye.distortPoints(norm.reshape(-1, 1, 2), self.K, self.D)
            out = out.reshape(-1, 2)
        else:
            xyz = np.hstack([norm, np.ones((len(norm), 1))])
            out, _ = cv2.projectPoints(xyz, np.zeros(3), np.zeros(3), self.K, self.D)
            out = out.reshape(-1, 2)
        return out - np.array([x0, y0], dtype=np.float64)

    # ── Writing ───────────────────────────────────────────────────────────────

    def save(self, fit, method, raw=None, crop=None):
        """Write a fit (as returned by fit_planar/fit_checkerboard) to disk.

        *raw* is the measurement data the fit came from — the projected and observed
        point pairs. It is stored alongside the derived coefficients for the same
        reason pump_calibration keeps its weigh-ins: a stored result with no way to
        see what produced it cannot be sanity-checked afterwards, and with the pairs
        on disk the fit can be redone without putting the rig back in that state.
        """
        data = {
            "model":        fit["model"],
            "image_size":   [int(fit["image_size"][0]), int(fit["image_size"][1])],
            "camera_matrix": np.asarray(fit["K"], dtype=float).reshape(3, 3).tolist(),
            "dist_coeffs":  np.asarray(fit["D"], dtype=float).ravel().tolist(),
            "rms_px":       float(fit["rms_px"]),
            "method":       method,
            "captured":     datetime.now().isoformat(timespec="seconds"),
            "crop_at_calibration": list(crop) if crop else list(shared_states.DLC_CROP),
            "rejected_model": fit.get("rejected"),
            "raw":          raw or {},
        }
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self.path)        # atomic: the camera process polls this file
        self.reload(force=True)
        return data


# ── Preview geometry ───────────────────────────────────────────────────────────

def preview_step(crop, preview_shape):
    """(step_x, step_y) between the GUI preview and the cropped frame.

    Mirrors camera_controls' downsample exactly — it strides (`[::step]`), it does
    not resample, so preview pixel j is crop pixel step·j and nothing in between.
    Anything converting a preview coordinate back to a sensor pixel (the wizard
    measuring projected dots) has to use the same integer step or it acquires a
    half-pixel-per-stride bias that grows across the frame.
    """
    y0, y1, x0, x1 = crop
    ph, pw = preview_shape[:2]
    return (max(1, int((x1 - x0) // pw)), max(1, int((y1 - y0) // ph)))


def preview_to_sensor(pts, crop, preview_shape):
    """Preview pixel coordinates → full-sensor pixel coordinates."""
    sx, sy = preview_step(crop, preview_shape)
    y0, _y1, x0, _x1 = crop
    p = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    return np.column_stack([x0 + p[:, 0] * sx, y0 + p[:, 1] * sy])


# ── Fitting ────────────────────────────────────────────────────────────────────

def _reprojection_rms(obj, img, K, D, rvec, tvec, model):
    """RMS reprojection error in pixels for one view."""
    if model == MODEL_FISHEYE:
        proj, _ = cv2.fisheye.projectPoints(obj.reshape(-1, 1, 3), rvec, tvec, K, D)
    else:
        proj, _ = cv2.projectPoints(obj.reshape(-1, 3), rvec, tvec, K, D)
    diff = proj.reshape(-1, 2) - img.reshape(-1, 2)
    return float(np.sqrt(np.mean(np.sum(diff ** 2, axis=1))))


def _fit_single_view(obj_cm, img_px, image_size, model, focal):
    """Fit distortion for one planar view at a *fixed* focal length.

    Returns (rms, K, D) or None. Everything but the distortion coefficients and the
    board pose is held fixed: one view of one plane cannot separate focal length,
    distance and distortion, so pinning f is what makes the problem well posed. The
    caller sweeps f and keeps the best.
    """
    w, h = image_size
    K = np.array([[focal, 0.0, w / 2.0],
                  [0.0, focal, h / 2.0],
                  [0.0, 0.0, 1.0]], dtype=np.float64)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 60, 1e-8)
    try:
        if model == MODEL_FISHEYE:
            D = np.zeros((4, 1), dtype=np.float64)
            flags = (cv2.fisheye.CALIB_USE_INTRINSIC_GUESS
                     | cv2.fisheye.CALIB_FIX_PRINCIPAL_POINT
                     | cv2.fisheye.CALIB_FIX_FOCAL_LENGTH
                     | cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC
                     | cv2.fisheye.CALIB_FIX_K3
                     | cv2.fisheye.CALIB_FIX_K4)
            rms, K, D, rvecs, tvecs = cv2.fisheye.calibrate(
                [obj_cm.reshape(-1, 1, 3)], [img_px.reshape(-1, 1, 2)],
                (w, h), K, D, flags=flags, criteria=crit)
        else:
            D = np.zeros((5, 1), dtype=np.float64)
            flags = (cv2.CALIB_USE_INTRINSIC_GUESS
                     | cv2.CALIB_FIX_PRINCIPAL_POINT
                     | cv2.CALIB_FIX_ASPECT_RATIO
                     | cv2.CALIB_FIX_FOCAL_LENGTH
                     | cv2.CALIB_ZERO_TANGENT_DIST
                     | cv2.CALIB_FIX_K3)
            rms, K, D, rvecs, tvecs = cv2.calibrateCamera(
                [obj_cm.reshape(-1, 3).astype(np.float32)],
                [img_px.reshape(-1, 1, 2).astype(np.float32)],
                (w, h), K, D, flags=flags, criteria=crit)
        rms = _reprojection_rms(obj_cm, img_px, K, D, rvecs[0], tvecs[0], model)
    except cv2.error:
        return None
    if not np.isfinite(rms) or not np.all(np.isfinite(K)) or not np.all(np.isfinite(D)):
        return None
    return rms, K, D


def fit_planar(obj_cm, img_px, image_size, models=(MODEL_FISHEYE, MODEL_RADIAL),
               progress=None):
    """Fit lens distortion from ONE planar view — the beamer-projected dot grid.

    *obj_cm* is (N,2) positions on the arena floor in cm, *img_px* the (N,2) pixel
    positions they were observed at, in full-sensor coordinates.

    Focal length and distortion trade off against each other in a single view (a
    longer lens further away looks much like a shorter one closer up), so f is swept
    — coarsely over a wide geometric range, then finely around the winner — and the
    lowest reprojection error wins. Both distortion models are tried; the one that
    describes this lens better is returned and the other is reported as `rejected` so
    the margin between them is visible.

    Returns a dict {model, K, D, rms_px, focal, rejected} or None if nothing fitted.
    """
    obj_cm = np.asarray(obj_cm, dtype=np.float64).reshape(-1, 2)
    img_px = np.asarray(img_px, dtype=np.float64).reshape(-1, 2)
    if len(obj_cm) != len(img_px) or len(obj_cm) < 8:
        return None
    obj3 = np.hstack([obj_cm, np.zeros((len(obj_cm), 1))]).astype(np.float64)

    w, _h = image_size
    results = {}
    for model in models:
        coarse = np.geomspace(0.2 * w, 4.0 * w, 24)
        best = None
        for i, f in enumerate(coarse):
            got = _fit_single_view(obj3, img_px, image_size, model, float(f))
            if got and (best is None or got[0] < best[0]):
                best = (got[0], got[1], got[2], float(f))
            if progress is not None:
                progress(model, i + 1, len(coarse))
        if best is None:
            continue
        # Refine around the coarse winner: the sweep's own spacing is the dominant
        # error left in f at this point.
        span = best[3] * 0.35
        for f in np.linspace(max(1.0, best[3] - span), best[3] + span, 21):
            got = _fit_single_view(obj3, img_px, image_size, model, float(f))
            if got and got[0] < best[0]:
                best = (got[0], got[1], got[2], float(f))
        results[model] = best

    if not results:
        return None
    model = min(results, key=lambda m: results[m][0])
    rms, K, D, focal = results[model]
    rejected = {m: round(results[m][0], 4) for m in results if m != model}
    return {"model": model, "K": K, "D": D, "rms_px": rms, "focal": focal,
            "image_size": tuple(int(v) for v in image_size),
            "rejected": rejected or None}
