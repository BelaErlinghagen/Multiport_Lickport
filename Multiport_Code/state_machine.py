"""state_machine.py — Behavioral state machine for the Multiport setup.

Architecture mirrors camera_controls.py / serial_controls.py:
  - StateMachine class        : all session logic; can be unit-tested standalone.
  - state_machine_process()   : multiprocessing target; idles until activated,
                                runs a session from a protocol dict, then resets.

Inter-process communication (all multiprocessing objects):
  sm_active        Value('b') — ExperimentPage sets True to start a session;
                               SM resets to False when the session ends.
  sm_stop          Value('b') — immediate emergency stop.
  sm_running       Value('b') — main.py sets False to terminate the process.
  command_queue    Queue      — shared hardware command bus (LED / MOS / BNC).
  sensor_array     Array('i') — 16-element live sensor state (0/1 per port).
  protocol_queue   Queue      — ExperimentPage puts the protocol dict here before
                               setting sm_active=True; SM reads it at session start.
  session_done     Value('b') — SM sets True on natural session completion so that
                               ExperimentPage can auto-stop recording.

Session flow:
  IDLE  →  (sm_active=True)  →  [TRIAL → ITI] × N  →  DONE / STOPPED  →  IDLE
"""

import json
import os
import random
import signal
import time
from datetime import datetime

_CIRCLE_SIZE = 16   # total number of ports in the circular array


class StateMachine:
    """Drives the trial cycle for one behavioural session.

    All hardware commands are written to *command_queue* using the text
    protocol understood by SerialControls:
        LED:id:ON / LED:id:OFF
        MOS:id:ON:duration_ms
        BNC:id:PULSE:duration_ms
    """

    def __init__(self, command_queue, sensor_array, pose_queue=None, beamer_queue=None):
        self.command_queue = command_queue
        self.sensor_array  = sensor_array   # multiprocessing.Array('i', 16)
        self.pose_queue    = pose_queue     # optional Queue for DLC-based ITI
        self.beamer_queue  = beamer_queue   # optional Queue for beamer projection
        self._calib        = None           # BeamerCalibration, loaded at run()
        self._shadow       = False          # protocol["beamer"]["shadow"] for this session
        self._field_color  = [255, 255, 255]  # stable shadow-field colour for this session

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _send(self, cmd: str):
        """Put a command on the hardware queue; drop silently if full."""
        try:
            self.command_queue.put_nowait(cmd)
        except Exception:
            pass

    def _sleep(self, duration: float, sm_stop, sm_active) -> bool:
        """Sleep *duration* seconds with 20 ms polling granularity.

        Returns True on normal completion, False if interrupted by a stop flag.
        """
        end = time.monotonic() + duration
        while time.monotonic() < end:
            if sm_stop.value or not sm_active.value:
                return False
            time.sleep(0.02)
        return True

    def _all_off(self):
        """Safety: silence every LED and pump, and blank the beamer."""
        for i in range(1, _CIRCLE_SIZE + 1):
            self._send(f"LED:{i}:OFF")
            self._send(f"MOS:{i}:OFF:0")
        self._beamer({"cmd": "clear"})

    # ── Beamer ────────────────────────────────────────────────────────────────

    def _load_calib(self):
        """Load the current beamer calibration (cm ↔ camera mapping) for the session."""
        try:
            from beamer_controls import BeamerCalibration
            self._calib = BeamerCalibration()
        except Exception as exc:
            print(f"[StateMachine] Beamer calibration unavailable: {exc}")
            self._calib = None

    def _beamer(self, cmd: dict):
        """Put a command on the beamer queue; drop silently if full/absent."""
        if self.beamer_queue is None:
            return
        try:
            self.beamer_queue.put_nowait(cmd)
        except Exception:
            pass

    @staticmethod
    def _region_color(region: dict) -> list:
        """Region colour scaled by its brightness → [r, g, b] (0–255)."""
        b = float(region.get("brightness", 100)) / 100.0
        c = region.get("color", [255, 255, 255])
        return [int(c[0] * b), int(c[1] * b), int(c[2] * b)]

    def _session_field_color(self, protocol: dict) -> list:
        """Shadow-field colour for the whole session, taken from the ITI region so
        the lit intensity matches between trials and ITIs. Defaults to full white
        (e.g. a fixed-time ITI, which has no region colour)."""
        iti = protocol.get("intertrial", {})
        region = {"fixed_region":  iti.get("region", {}),
                  "random_region": iti.get("random_region", {})}.get(
                      iti.get("type", "time"), {})
        return self._region_color(region)

    def _beamer_baseline(self, shadow: bool):
        """Trial / fixed-time baseline: dark (Light mode) or a fully lit projection
        area (Shadow mode — a diameter-0 shadow sphere lights the whole disc).

        In Shadow mode the field uses the session-wide _field_color so the lit
        intensity is identical during trials and ITIs (it never switches — only
        the dark target hole appears/disappears)."""
        if shadow:
            self._beamer({"cmd": "sphere", "x_cm": 0.0, "y_cm": 0.0,
                          "diameter_cm": 0.0, "shadow": True,
                          "color": self._field_color})
        else:
            self._beamer({"cmd": "clear"})

    def _beamer_sphere(self, region: dict, x_cm: float, y_cm: float, shadow: bool):
        """Project the ITI target: bright sphere (Light) or dark hole (Shadow)."""
        self._beamer({
            "cmd":         "sphere",
            "x_cm":        x_cm,
            "y_cm":        y_cm,
            "diameter_cm": float(region.get("diameter_cm", 6.0)),
            "shadow":      shadow,
            "color":       self._region_color(region),
        })

    # ── Mouse session log ─────────────────────────────────────────────────────

    @staticmethod
    def _mouse_json_path(protocol: dict) -> str | None:
        """Return path to {mouse_id}.json inside the mouse folder, or None."""
        meta = protocol.get("_meta", {})
        cohort = meta.get("cohort_id", "")
        mouse  = meta.get("mouse_id", "")
        if not cohort or not mouse:
            return None
        try:
            from shared_states import data_path
        except Exception:
            return None
        return os.path.join(data_path, cohort, mouse, f"{mouse}.json")

    @staticmethod
    def _load_mouse_log(path: str) -> dict:
        """Load the mouse JSON log, returning an empty skeleton on missing/corrupt file."""
        if os.path.exists(path):
            try:
                with open(path, "r") as fh:
                    return json.load(fh)
            except Exception:
                pass
        return {"sessions": []}

    @staticmethod
    def _save_mouse_log(path: str, log: dict):
        """Write the mouse JSON log to disk, creating parent dirs if needed."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                json.dump(log, fh, indent=2)
        except Exception as exc:
            print(f"[StateMachine] WARNING: could not save mouse log: {exc}")

    # ── Reward location assignment ────────────────────────────────────────────

    def _assign_locations(self, protocol: dict) -> dict | None:
        """Return {reward_id: port} mapping, or None if impossible."""
        dist = protocol["rewards"]["distribution"]
        if dist["type"] == "fixed":
            return {int(k): int(v) for k, v in dist["fixed_map"].items()}

        # Collect ports to exclude when the user enabled "exclude previous locations"
        excluded: set[int] = set()
        if dist.get("exclude_previous", False):
            json_path = self._mouse_json_path(protocol)
            if json_path:
                log = self._load_mouse_log(json_path)
                for sess in log.get("sessions", []):
                    excluded.update(int(p) for p in sess.get("reward_ports", []))
                if excluded:
                    print(f"[StateMachine] Excluding previously used ports: {sorted(excluded)}")

        return self._random_locations(
            protocol["rewards"]["count"],
            int(dist.get("min_spacing", 4)),
            excluded,
        )

    def _random_locations(self, count: int, min_spacing: int,
                          excluded: set | None = None) -> dict | None:
        """Randomly assign *count* rewards to ports respecting circular spacing.

        Ports in *excluded* are never chosen.
        """
        excluded = excluded or set()
        available = [p for p in range(1, _CIRCLE_SIZE + 1) if p not in excluded]
        if len(available) < count:
            print(f"[StateMachine] Not enough available ports "
                  f"({len(available)} free, {count} needed after exclusions).")
            return None
        for _ in range(2000):
            ports = random.sample(available, count)
            if self._check_spacing(ports, min_spacing):
                return {i + 1: ports[i] for i in range(count)}
        return None   # infeasible with given constraints

    @staticmethod
    def _check_spacing(ports: list, min_spacing: int) -> bool:
        """True if every adjacent pair in the circular arrangement is ≥ min_spacing apart."""
        s = sorted(ports)
        n = len(s)
        for i in range(n):
            arc = s[(i + 1) % n] - s[i]
            if arc <= 0:
                arc += _CIRCLE_SIZE
            if arc < min_spacing:
                return False
        return True

    # ── LED activation ────────────────────────────────────────────────────────

    def _get_led_ports(self, protocol: dict, reward_ports: list) -> set:
        """Return the set of LED port IDs to illuminate based on protocol led_mode."""
        mode = protocol["rewards"].get("led_mode", "reward_only")
        if mode == "none":
            return set()
        if mode == "all":
            return set(range(1, _CIRCLE_SIZE + 1))
        if mode == "reward_only":
            return set(reward_ports)
        if mode == "neighbors":
            n = int(protocol["rewards"].get("led_neighbors", 1))
            ports = set()
            for p in reward_ports:
                for d in range(-n, n + 1):
                    ports.add(((p - 1 + d) % _CIRCLE_SIZE) + 1)
            return ports
        return set(reward_ports)

    # ── Trial ─────────────────────────────────────────────────────────────────

    def _run_trial(self, trial_num: int, protocol: dict,
                   reward_locations: dict, sm_stop, sm_active) -> bool:
        """Execute one trial.

        Each rewarded port can be collected at most once per trial.
        On sensor trigger: roll probability; if hit, pulse the pump; regardless,
        turn off the LED and mark the port collected.

        Returns True on normal completion, False if stopped early.
        """
        configs       = {cfg["id"]: cfg for cfg in protocol["rewards"]["configs"]}
        reward_ports  = list(reward_locations.values())
        port_to_rid   = {v: k for k, v in reward_locations.items()}
        led_ports     = self._get_led_ports(protocol, reward_ports)

        # Beamer trial baseline: dark (Light mode) or full lit field (Shadow mode).
        self._beamer_baseline(self._shadow)

        for p in led_ports:
            self._send(f"LED:{p}:ON")
        print(f"[StateMachine] Trial {trial_num}: started  reward ports={reward_ports}")

        trial_conf  = protocol["trial"]
        end_type    = trial_conf["end_type"]
        duration    = float(trial_conf.get("duration_s", 30))

        collected   = set()
        trial_start = time.monotonic()

        while True:
            # ── Stop checks ───────────────────────────────────────
            if sm_stop.value or not sm_active.value:
                for p in led_ports:
                    self._send(f"LED:{p}:OFF")
                return False

            # ── Trial end conditions ──────────────────────────────
            if end_type == "time" and time.monotonic() - trial_start >= duration:
                break
            if end_type == "all_rewards" and len(collected) >= len(reward_ports):
                break

            # ── Sensor polling ────────────────────────────────────
            for port in reward_ports:
                if port in collected:
                    continue
                if int(self.sensor_array[port - 1]) == 1:
                    rid  = port_to_rid[port]
                    cfg  = configs.get(rid, {})
                    prob = float(cfg.get("probability", 1.0))
                    dur  = int(cfg.get("duration_ms", 500))

                    if random.random() < prob:
                        print(f"[StateMachine] Trial {trial_num}: "
                              f"reward at port {port} (p={prob:.2f}) → MOS pulse {dur} ms")
                        self._send(f"MOS:{port}:ON:{dur}")
                    else:
                        print(f"[StateMachine] Trial {trial_num}: "
                              f"contact at port {port} — probability miss (p={prob:.2f})")

                    # Mark collected; LED stays on until end of trial
                    collected.add(port)

            time.sleep(0.02)

        # Turn off any remaining LEDs
        for p in led_ports:
            self._send(f"LED:{p}:OFF")

        print(f"[StateMachine] Trial {trial_num}: ended  "
              f"({len(collected)}/{len(reward_ports)} collected)")
        return True

    # ── Intertrial interval ───────────────────────────────────────────────────

    def _run_iti(self, iti_config: dict, sm_stop, sm_active) -> bool:
        """Run the intertrial interval between two trials.

        Returns True on normal completion, False if a stop flag fires.

        Three modes driven by iti_config["type"]:
          "time"          — fixed sleep, beamer holds the trial baseline
          "fixed_region"  — project a cm-defined target sphere; wait until the DLC
                            centroid dwells inside it (checked in beamer-pixel space)
          "random_region" — same but the target centre is drawn from a cm margin disc
        """
        import math
        iti_type = iti_config.get("type", "time")
        shadow = self._shadow

        if iti_type == "time":
            # Beamer stays at the trial baseline for the fixed duration.
            return self._sleep(float(iti_config.get("duration_s", 3.0)), sm_stop, sm_active)

        # ── Region-based ITI (target specified in cm) ─────────────────────────
        if iti_type == "fixed_region":
            region = iti_config.get("region", {})
            x_cm = float(region.get("x_cm", 0.0))
            y_cm = float(region.get("y_cm", 0.0))
        else:  # random_region — pick a centre uniformly inside the margin disc
            region = iti_config.get("random_region", {})
            mx = float(region.get("margin_x_cm", 0.0))
            my = float(region.get("margin_y_cm", 0.0))
            mr = float(region.get("margin_radius_cm", 10.0))
            ang  = random.uniform(0, 2 * math.pi)
            dist = mr * math.sqrt(random.uniform(0, 1))
            x_cm = mx + dist * math.cos(ang)
            y_cm = my + dist * math.sin(ang)

        diameter_cm = float(region.get("diameter_cm", 6.0))
        dur_type = region.get("duration_type", "fixed")
        required_s = (float(region.get("duration_s", 2.0)) if dur_type == "fixed"
                      else random.uniform(0, float(region.get("duration_max_s", 3.0))))

        # Project the target for the whole ITI.
        self._beamer_sphere(region, x_cm, y_cm, shadow)

        # Target in beamer pixels; the mouse is mapped DLC → beamer via the affine.
        calib = self._calib
        can_track = bool(calib and calib.cam_to_beamer and self.pose_queue is not None)
        if not can_track:
            # No camera↔beamer mapping or no pose feed: hold the cue for the dwell
            # time instead of true dwell detection so the session can't hang.
            print("[StateMachine] ITI: no camera↔beamer mapping / pose feed — "
                  f"holding the cue for {required_s:.1f} s.")
            ok = self._sleep(required_s, sm_stop, sm_active)
            self._beamer_baseline(shadow)
            return ok

        bx, by, r_px = calib.cm_to_px(x_cm, y_cm, diameter_cm)
        from shared_states import DLC_CROP
        crop_w = DLC_CROP[3] - DLC_CROP[2]
        crop_h = DLC_CROP[1] - DLC_CROP[0]
        _THRESH = 0.5
        in_region_since = None

        print(f"[StateMachine] ITI ({iti_type}): waiting for mouse at "
              f"({x_cm:.1f}, {y_cm:.1f}) cm for {required_s:.1f} s")

        while True:
            if sm_stop.value or not sm_active.value:
                return False   # run()/_all_off blanks the beamer

            pose = None
            try:
                pose = self.pose_queue.get_nowait()
            except Exception:
                pass

            if pose is not None:
                pts = [(float(kp[0]), float(kp[1]))
                       for kp in pose if float(kp[2]) > _THRESH]
                in_r = False
                if pts:
                    cx_e = sum(p[0] for p in pts) / len(pts)
                    cy_e = sum(p[1] for p in pts) / len(pts)
                    mapped = calib.dlc_to_px(cx_e / crop_w, cy_e / crop_h)
                    if mapped is not None:
                        in_r = ((mapped[0] - bx) ** 2 + (mapped[1] - by) ** 2) ** 0.5 <= r_px

                now = time.monotonic()
                if in_r:
                    if in_region_since is None:
                        in_region_since = now
                    elif now - in_region_since >= required_s:
                        print("[StateMachine] ITI complete — mouse in region.")
                        self._beamer_baseline(shadow)
                        return True
                else:
                    in_region_since = None

            time.sleep(0.02)

    # ── Session ───────────────────────────────────────────────────────────────

    def run(self, protocol: dict, sm_active, sm_stop, session_done):
        """Run a full session driven by *protocol*.

        Assigns reward locations, then cycles through trials until the session
        end condition is met or a stop flag is raised.

        Writes a session entry to {mouse_id}.json at start; updates it with the
        end time on completion.
        """
        # Beamer session setup: global light/shadow, a stable shadow-field colour,
        # and the current cm↔camera calibration.
        self._shadow = bool(protocol.get("beamer", {}).get("shadow", False))
        self._field_color = self._session_field_color(protocol)
        self._load_calib()

        reward_locations = self._assign_locations(protocol)
        if reward_locations is None:
            print("[StateMachine] ERROR: reward location constraints are infeasible "
                  f"(count={protocol['rewards']['count']}, "
                  f"spacing={protocol['rewards']['distribution'].get('min_spacing')}). "
                  "Aborting session.")
            session_done.value = True
            return

        reward_ports = sorted(reward_locations.values())
        print(f"[StateMachine] Session started.  Reward locations: {reward_locations}")

        # ── Write session start to mouse log ─────────────────────────────────
        meta       = protocol.get("_meta", {})
        session_id = meta.get("session_id", "")
        now        = datetime.now()
        start_str  = now.strftime("%H:%M:%S")
        date_str   = now.strftime("%Y-%m-%d")

        # Strip internal _meta key before storing the protocol in the log
        protocol_clean = {k: v for k, v in protocol.items() if k != "_meta"}

        session_entry = {
            "session_id":    session_id,
            "date":          date_str,
            "start_time":    start_str,
            "end_time":      None,
            "protocol_path": meta.get("protocol_path", ""),
            "protocol":      protocol_clean,
            "reward_ports":  reward_ports,
        }

        json_path = self._mouse_json_path(protocol)
        if json_path:
            log = self._load_mouse_log(json_path)
            log.setdefault("mouse_id", meta.get("mouse_id", ""))
            log["sessions"].append(session_entry)
            self._save_mouse_log(json_path, log)

        # ── Session loop ──────────────────────────────────────────────────────
        sess_type   = protocol["session"]["type"]
        sess_length = protocol["session"]["length"]
        sess_start  = time.monotonic()
        trial_num   = 0
        stopped     = False

        while True:
            if sm_stop.value or not sm_active.value:
                stopped = True
                break
            if sess_type == "time" and time.monotonic() - sess_start >= sess_length:
                break
            if sess_type == "trials" and trial_num >= sess_length:
                break

            trial_num += 1
            if not self._run_trial(trial_num, protocol, reward_locations,
                                   sm_stop, sm_active):
                stopped = True
                break

            # Run ITI only if the session will continue after this trial
            session_continues = (
                (sess_type == "time"   and time.monotonic() - sess_start < sess_length) or
                (sess_type == "trials" and trial_num < sess_length)
            )
            if session_continues:
                iti_config = protocol.get("intertrial", {"type": "time", "duration_s": 3.0})
                if not self._run_iti(iti_config, sm_stop, sm_active):
                    stopped = True
                    break

        self._all_off()

        # ── Update session log with end time ──────────────────────────────────
        if json_path:
            end_str = datetime.now().strftime("%H:%M:%S")
            log = self._load_mouse_log(json_path)
            for entry in reversed(log.get("sessions", [])):
                if (entry.get("session_id") == session_id
                        and entry.get("start_time") == start_str):
                    entry["end_time"] = end_str
                    break
            self._save_mouse_log(json_path, log)

        if stopped:
            print(f"[StateMachine] Session stopped after {trial_num} trial(s).")
        else:
            print(f"[StateMachine] Session complete — {trial_num} trial(s) finished.")
            session_done.value = True


# ── Process entry point ───────────────────────────────────────────────────────

def state_machine_process(sm_active, sm_stop, sm_running, command_queue,
                           sensor_array, protocol_queue, session_done,
                           pose_sm_queue=None, beamer_queue=None):
    """Long-running process that hosts the StateMachine.

    Lifecycle:
      • Idles (sleep 50 ms) until sm_active becomes True.
      • Reads the protocol dict from protocol_queue (put there by ExperimentPage
        before setting sm_active).
      • Runs a full session, then resets sm_active to False and returns to idle.
      • Exits when sm_running becomes False.

    Called by main.py as a multiprocessing.Process target.
    """
    command_queue.cancel_join_thread()
    protocol_queue.cancel_join_thread()

    def _handle_term(_sig, _frame):
        sm_running.value = False
    signal.signal(signal.SIGTERM, _handle_term)

    machine = StateMachine(command_queue, sensor_array, pose_queue=pose_sm_queue,
                           beamer_queue=beamer_queue)
    print("[StateMachine] Process ready — waiting for activation.")

    while sm_running.value:
        if sm_active.value:
            sm_stop.value    = False
            session_done.value = False

            # ExperimentPage puts the protocol dict on this queue before
            # setting sm_active=True; wait up to 2 s for it.
            try:
                protocol = protocol_queue.get(timeout=2)
            except Exception:
                print("[StateMachine] No protocol received — aborting session.")
                sm_active.value = False
                time.sleep(0.05)
                continue

            machine.run(protocol, sm_active, sm_stop, session_done)
            sm_active.value = False

        time.sleep(0.05)

    print("[StateMachine] Process exiting.")
