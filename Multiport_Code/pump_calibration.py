"""How much liquid each lickport pump ejects per pulse.

Rewards are configured as a volume in µL, not a pump-on duration: pumps
differ in tubing and back-pressure, so the same duration delivers different
amounts on different ports. A pump held on for more than ~10 ms also shoots
liquid across the arena instead of forming a droplet, so a reward is
delivered as a train of shared_states.pump_pulse_ms pulses (one per lick),
sized from the µL/pulse measured here:

    n_pulses = round(volume_ul / ul_per_pulse)

Measured by the wizard on the Cleaning/Testing tab (fire N pulses into a
tube, weigh it, enter the net mass in mg) and stored in
shared_states.pump_calibration_path, alongside the raw measurements the mean
was computed from — so a stored value can be sanity-checked later.

Never raises and never blocks startup: a missing/partial file just leaves a
port uncalibrated (ul_per_pulse returns None). Refusing to run a session
over that is ExperimentPage/StateMachine's decision, not this module's.
"""

import json
import os
from datetime import datetime

import shared_states

# Ports are keyed by their string form in the JSON, since json.dump turns int
# keys into strings anyway — using strings consistently avoids a load/save mismatch.
PORT_COUNT = 16


class PumpCalibration:
    """Per-pump µL/pulse, loaded from shared_states.pump_calibration_path."""

    def __init__(self, path=None):
        self.path = path or shared_states.pump_calibration_path
        self.pulse_ms = int(shared_states.pump_pulse_ms)
        self.refractory_ms = int(shared_states.pump_refractory_ms)
        self.density_mg_per_ul = 1.0
        self.ports = {}          # int port → dict as stored in the file
        self._mtime = None
        self.reload()

    # ── Loading ───────────────────────────────────────────────────────────────

    def reload(self, force=False):
        """Re-read the calibration JSON; keeps the rig running on any failure.

        Cheap enough to call from a GUI timer: skips re-parsing unless
        *force* or the file's mtime changed, so e.g. the Protocol tab can
        refresh its pulse-count hints on every repaint.
        """
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            mtime = None
        if not force and mtime is not None and mtime == self._mtime:
            return
        self._mtime = mtime

        try:
            with open(self.path, "r") as fh:
                data = json.load(fh)
        except Exception:
            data = {}

        self.density_mg_per_ul = float(data.get("density_mg_per_ul") or 1.0)

        ports = {}
        for key, entry in (data.get("ports") or {}).items():
            try:
                port = int(key)
                ul = float(entry.get("ul_per_pulse"))
            except (TypeError, ValueError):
                continue
            if 1 <= port <= PORT_COUNT and ul > 0:
                ports[port] = dict(entry)
        self.ports = ports

        # A calibration measured at a different pulse width or refill interval
        # doesn't transfer — pump output isn't proportional to pulse width or
        # independent of refill time. Warn loudly rather than silently using
        # stale numbers after shared_states is edited.
        file_pulse = data.get("pulse_ms")
        file_refr = data.get("refractory_ms")
        if ports and file_pulse is not None and int(file_pulse) != self.pulse_ms:
            print(f"[PumpCalibration] WARNING: calibrated at {file_pulse} ms pulses but "
                  f"shared_states.pump_pulse_ms is now {self.pulse_ms} — recalibrate.")
        if ports and file_refr is not None and int(file_refr) != self.refractory_ms:
            print(f"[PumpCalibration] WARNING: calibrated at a {file_refr} ms interval but "
                  f"shared_states.pump_refractory_ms is now {self.refractory_ms} — "
                  f"the per-pulse volume depends on the refill time, so recalibrate.")

    # ── Queries ───────────────────────────────────────────────────────────────

    def is_calibrated(self, port) -> bool:
        return int(port) in self.ports

    def ul_per_pulse(self, port):
        """µL delivered by one pulse at *port*, or None if it is uncalibrated."""
        entry = self.ports.get(int(port))
        return float(entry["ul_per_pulse"]) if entry else None

    def uncalibrated_ports(self, ports) -> list:
        """Which of *ports* have no calibration, in order and without duplicates."""
        seen, missing = set(), []
        for p in ports:
            p = int(p)
            if p not in seen and not self.is_calibrated(p):
                seen.add(p)
                missing.append(p)
        return missing

    def stats(self, port):
        """The stored entry for *port* (ul_per_pulse, sd_ul, cv_pct, …), or None."""
        entry = self.ports.get(int(port))
        return dict(entry) if entry else None

    def pulses_for(self, port, volume_ul):
        """Return (n_pulses, actual_ul) needed to deliver volume_ul at port,
        or None if the port is uncalibrated.

        Uses int(x + 0.5) instead of round(), since Python's round() is
        banker's rounding and would round exactly half a pulse down to zero.
        Clamped to at least 1 pulse (there's no way to deliver less than one
        droplet) and at most pump_max_pulses.
        """
        ul = self.ul_per_pulse(port)
        if ul is None or ul <= 0:
            return None
        n = int(float(volume_ul) / ul + 0.5)
        n = max(1, min(n, int(shared_states.pump_max_pulses)))
        return n, n * ul

    def min_delivery_s(self, n_pulses) -> float:
        """Shortest possible time to deliver *n_pulses*, i.e. with no missed licks.

        The first pulse fires on contact, so only the gaps after it cost time.
        """
        return max(0, int(n_pulses) - 1) * (self.refractory_ms / 1000.0)

    # ── Writing (used by the wizard) ──────────────────────────────────────────

    def save_port(self, port, measurements, density_mg_per_ul=None):
        """Write one port's measurements and their mean into the calibration file.

        *measurements* is a list of {"n_pulses": int, "measured_mg": float}.

        Read-modify-write: re-reads the file rather than dumping self.ports,
        so calibrating one pump can never clear the other fifteen.
        """
        density = float(density_mg_per_ul if density_mg_per_ul is not None
                        else self.density_mg_per_ul)
        if density <= 0:
            raise ValueError("density must be greater than zero")

        per_pulse = []
        clean = []
        for m in measurements:
            n = int(m["n_pulses"])
            mg = float(m["measured_mg"])
            if n <= 0:
                continue
            per_pulse.append((mg / density) / n)
            clean.append({"n_pulses": n, "measured_mg": mg})
        if not per_pulse:
            raise ValueError("no usable measurements")

        mean = sum(per_pulse) / len(per_pulse)
        # Sample SD (n-1). With the 3–5 repeats this wizard collects it is a spread
        # indicator, not a real error bar — the GUI presents it as such.
        if len(per_pulse) > 1:
            var = sum((v - mean) ** 2 for v in per_pulse) / (len(per_pulse) - 1)
            sd = var ** 0.5
        else:
            sd = 0.0

        try:
            with open(self.path, "r") as fh:
                data = json.load(fh)
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}

        data["pulse_ms"] = self.pulse_ms
        data["refractory_ms"] = self.refractory_ms
        data["density_mg_per_ul"] = density
        data.setdefault("ports", {})
        data["ports"][str(int(port))] = {
            "ul_per_pulse": round(mean, 6),
            "sd_ul":        round(sd, 6),
            "cv_pct":       round(100.0 * sd / mean, 2) if mean else 0.0,
            "n_repeats":    len(per_pulse),
            "calibrated":   datetime.now().isoformat(timespec="seconds"),
            "measurements": clean,
        }

        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump(data, fh, indent=2)

        self.density_mg_per_ul = density
        self.reload(force=True)

    def clear_port(self, port):
        """Drop one port's calibration, leaving the rest of the file intact."""
        try:
            with open(self.path, "r") as fh:
                data = json.load(fh)
        except Exception:
            return
        if isinstance(data, dict) and str(int(port)) in (data.get("ports") or {}):
            del data["ports"][str(int(port))]
            with open(self.path, "w") as fh:
                json.dump(data, fh, indent=2)
            self.reload(force=True)


# ── Convenience for callers that only need a summary ───────────────────────────

def summary_line(calib, port) -> str:
    """One-line human description of a port's calibration, for tooltips/labels."""
    entry = calib.stats(port)
    if entry is None:
        return f"Port {port}: not calibrated"
    return (f"Port {port}: {entry['ul_per_pulse']:.3f} µL/pulse "
            f"(±{entry.get('sd_ul', 0.0):.3f}, CV {entry.get('cv_pct', 0.0):.1f}%, "
            f"n={entry.get('n_repeats', 0)}, {entry.get('calibrated', '?')})")
