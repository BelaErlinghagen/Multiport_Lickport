"""The tracking camera's fisheye lens correction.

The arena camera's fisheye lens distorts straight lines into curves and
compresses distances near the edges of the frame, which throws off anything
that maps camera coordinates onto real positions — notably BeamerCalibration
and the state machine's "is the mouse inside the target" test. This module
corrects for that.

The correction is applied to the image once, in the camera process, fused
into the crop that already happens there (see camera_controls.py). DeepLabCut,
the saved video, the live preview, and every pose coordinate downstream all
read the corrected frame, so nothing else needs to know this module exists.

Calibrated using the beamer's own projected dots rather than a printed
checkerboard: the projection disc is bigger than the camera's view of it, so
dots can reach the frame corners (see the wizard on the Cleaning/Testing tab).
The trade-off is that the projector's own lens distortion and any floor
unevenness fold into the fit — which is why every saved calibration keeps its
raw point pairs, so it can be re-solved or audited later.

K/D (lens intrinsics/distortion) are always stored in full-sensor
coordinates, never cropped ones, so changing shared_states.DLC_CROP can't
silently invalidate a calibration.

Like PumpCalibration and BeamerCalibration, this never raises or blocks
startup: a missing/unreadable file just leaves is_calibrated False and the
camera process falls back to a plain crop.
"""

import json
import os
from datetime import datetime

import cv2
import numpy as np

import shared_states

# The two distortion models fit_planar() tries: "fisheye" is OpenCV's
# equidistant model (cv2.fisheye), "radial" the standard plumb-bob polynomial
# (cv2.calibrateCamera). Both are fit and the better RMS is kept, since which
# one suits a given lens isn't known ahead of time.
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
        """Re-read the calibration JSON; keeps the rig running on any failure.

        Cheap to call repeatedly: skips re-parsing unless *force* or the
        file's mtime changed, so the camera process can poll for a wizard
        run. Invalidates the cached remap tables on any successful read.
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

        # A calibration for a different sensor resolution no longer matches
        # what the camera delivers. Warn loudly, but don't refuse to run.
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
        """The pinhole camera matrix the corrected image is rendered through.

        Scales focal length by *zoom* about the optical centre and shifts the
        principal point into crop coordinates, so at zoom=1 the corrected
        frame keeps the same centre and scale as the raw one — changing that
        would quietly invalidate the beamer calibration's px/cm.

        Undistorting pushes the periphery outward, so at zoom=1 the corners
        of the raw view fall outside the frame. zoom<1 shrinks the result to
        fit them back in (with black borders); fit_zoom() computes the value
        that keeps everything.
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
        """Return the zoom at which the whole raw crop still fits in the
        corrected frame, for the wizard to report and the operator to copy
        into shared_states.undistort_zoom.

        Computed exactly, not searched: the virtual matrix scales offsets
        from the principal point linearly, so the limiting zoom is just a
        min over sampled border points.
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
        """Remap tables that crop and undistort in one pass, or None if uncalibrated.

        The maps are crop-sized but index into the full sensor frame — free,
        since cv2.remap's cost depends on output size, not input — and this
        lets the remap sample pixels just outside the crop rectangle, which
        matters at zoom levels that pull the full field back in (see fit_zoom).

        Uses CV_16SC2 (fixed-point) maps rather than float ones: half the
        memory traffic for interpolation precision well beyond what this needs.
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
        """Remap tables that rectify the GUI's downsampled preview, for a
        before/after view. Only the wizard needs this — the live pipeline
        always rectifies the full frame via build_maps().

        Scales the crop's intrinsics by the preview's stride, since the
        distortion coefficients themselves are resolution-independent.
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
        """Raw crop-pixel coordinates -> corrected crop-pixel coordinates.

        The live pipeline corrects whole frames and doesn't need this; it's
        for re-reading raw recordings and for the wizard, which measures
        calibration dots on uncorrected frames.
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
        """Inverse of undistort_points: corrected crop-pixel coordinates ->
        raw crop-pixel coordinates. Used to draw a corrected-space overlay
        onto a raw frame (the wizard's before/after view)."""
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
        """Write a fit (as returned by fit_planar) to disk.

        *raw* — the point pairs the fit came from — is stored alongside the
        derived coefficients, like pump_calibration keeps its weigh-ins, so
        the fit can be audited or redone later without repeating the capture.
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
    """Return (step_x, step_y) between the GUI preview and the cropped frame.

    Matches camera_controls.py's downsampling exactly (`[::step]`, a stride,
    not a resample), so preview pixel j is crop pixel step*j. Anything
    converting a preview coordinate back to a sensor pixel — e.g. the wizard
    reading projected dots — must use this same step or drift accumulates
    across the frame.
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
    """Fit distortion for one planar view at a fixed focal length.

    Returns (rms, K, D) or None. Focal length is pinned because a single
    planar view can't separate focal length, distance, and distortion from
    each other; the caller (fit_planar) sweeps focal length and keeps the best.
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
    """Fit lens distortion from ONE planar view (the beamer-projected dot grid).

    obj_cm is (N,2) arena-floor positions in cm; img_px the (N,2) pixel
    positions they were observed at, in full-sensor coordinates.

    A single view can't separate focal length from distortion (a longer lens
    further away looks like a shorter one closer up), so focal length is
    swept — coarse then fine around the best result — for both distortion
    models, and the better-fitting model is returned. The other is reported
    under "rejected" so the margin between them is visible.

    Returns {model, K, D, rms_px, focal, rejected}, or None if nothing fit.
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
        # Refine around the coarse winner — the sweep's own spacing is now
        # the dominant source of error in f.
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
