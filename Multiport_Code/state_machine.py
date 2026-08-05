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
  beamer_queue     Queue      — projection commands for beamer_controls.
  screen_queue     Queue      — {"screen_id", "pattern_id"} commands for
                               screen_controls (the two HDMI touch screens).

Trial tones go through a SpeakerControls owned directly by this class rather than a
queue and a process of its own: it opens no audio device until the first tone and
plays through a daemon thread, so it never blocks the trial loop.

The protocol is read strictly — every key ProtocolPage writes must be present, so a
malformed protocol raises KeyError rather than silently running on defaults. The
`_meta` block is the exception: the GUI injects it at runtime and a standalone run
legitimately has none.

Session flow:
  IDLE  →  (sm_active=True)  →  [TRIAL → ITI] × N  →  DONE / STOPPED  →  IDLE
"""

import json
import os
import random
import signal
import time
from datetime import datetime

_CIRCLE_SIZE  = 16   # total number of ports in the circular array
_SCREEN_COUNT = 2    # HDMI touch screens driven by screen_controls


class StateMachine:
    """Drives the trial cycle for one behavioural session.

    All hardware commands are written to *command_queue* using the text
    protocol understood by SerialControls:
        LED:id:ON / LED:id:OFF
        MOS:id:ON:duration_ms
        BNC:id:PULSE:duration_ms
    """

    def __init__(self, command_queue, sensor_array, pose_queue=None, beamer_queue=None,
                 screen_queue=None):
        self.command_queue = command_queue
        self.sensor_array  = sensor_array   # multiprocessing.Array('i', 16)
        self.pose_queue    = pose_queue     # optional Queue for DLC-based ITI
        self.beamer_queue  = beamer_queue   # optional Queue for beamer projection
        self.screen_queue  = screen_queue   # optional Queue for touch-screen patterns
        self._calib        = None           # BeamerCalibration, loaded at run()
        self._shadow       = False          # derived from the active ITI region
        self._field_color  = [255, 255, 255]  # stable shadow-field colour for this session
        self._screens      = {}             # protocol["screens"] for this session

        # Live reward layout. Promoted from a local so _screens_apply can follow the
        # rewards around the arena as they swap lickports.
        self._reward_locations: dict = {}     # {reward_id: port}, mutated by switching
        self._screen_anchor_ports: list = []  # port each screen is pinned to (or None)
        self._switch_log: list = []           # [{"trial", "rewards", "ports"}]

        # Reward delay (session-scoped: sampled once, measured from session start)
        self._delay_cfg: dict = {}
        self._delay_deadline = None

        # Trial tones
        self._sounds: dict = {}
        self._speaker = None
        self._speaker_tried = False

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
        """Safety: silence every LED and pump, blank the beamer and the screens."""
        for i in range(1, _CIRCLE_SIZE + 1):
            self._send(f"LED:{i}:OFF")
            self._send(f"MOS:{i}:OFF:0")
        self._beamer({"cmd": "clear"})
        for s in range(1, _SCREEN_COUNT + 1):
            self._screen(s, "black")
        # A long trial tone must not outlive an emergency stop.
        if self._speaker is not None:
            try:
                self._speaker.stop()
            except Exception:
                pass

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
        b = float(region["brightness"]) / 100.0
        c = region["color"]
        return [int(c[0] * b), int(c[1] * b), int(c[2] * b)]

    @staticmethod
    def _active_iti_region(protocol: dict) -> dict | None:
        """The ITI region in play this session, or None for a fixed-time ITI.

        Light/shadow is a property of the target region, so a fixed-time ITI — which
        has no region — simply has no shadow mode and runs the beamer dark.
        """
        iti = protocol["intertrial"]
        if iti["type"] == "fixed_region":
            return iti["region"]
        if iti["type"] == "random_region":
            return iti["random_region"]
        return None

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
            "diameter_cm": float(region["diameter_cm"]),
            "shadow":      shadow,
            "color":       self._region_color(region),
        })

    # ── Speaker ───────────────────────────────────────────────────────────────

    def _speaker_obj(self):
        """Lazily build the tone generator; None if it cannot be created.

        SpeakerControls opens no audio device on construction — it only stores a
        device string — and every tone runs in a daemon thread feeding a short-lived
        `aplay` child, so this costs nothing until the first tone and never blocks
        the trial loop. That is why the state machine owns one directly instead of
        going through a queue and a separate process like the beamer and screens do.
        """
        if not self._speaker_tried:
            self._speaker_tried = True
            try:
                from speaker_controls import SpeakerControls
                self._speaker = SpeakerControls()
            except Exception as exc:
                print(f"[StateMachine] speaker unavailable: {exc}")
                self._speaker = None
        return self._speaker

    def _play_sound(self, key: str):
        """Play the protocol's "trial_start" / "trial_end" tone, if enabled."""
        cfg = self._sounds[key]
        if not cfg["enabled"]:
            return
        # Read the settings outside the guard below: a malformed protocol should
        # raise like everywhere else, only a playback failure is survivable.
        length, freq, volume = (float(cfg["duration_s"]),
                                int(cfg["frequency_hz"]),
                                float(cfg["volume"]))
        spk = self._speaker_obj()
        if spk is None:
            return
        try:
            spk.produce_sound(length, freq, volume)
        except Exception as exc:
            # A dead speaker must never abort a running experiment.
            print(f"[StateMachine] sound '{key}' failed: {exc}")

    # ── Touch screens ─────────────────────────────────────────────────────────

    def _screen(self, screen_id: int, pattern: str):
        """Show *pattern* on one screen; drop silently if the queue is full/absent."""
        if self.screen_queue is None:
            return
        try:
            self.screen_queue.put_nowait({"screen_id": screen_id,
                                          "pattern_id": pattern})
        except Exception:
            pass

    def _screens_apply(self, phase: str) -> list:
        """Show the *phase* ("trial" / "iti") patterns on the screens.

        static  — the protocol's per-screen patterns. With "randomize" on, the trial
                  patterns are shuffled across the screens at the start of every
                  trial, so which screen carries which cue is unpredictable.
        dynamic — each screen is pinned to the lickport its reward started on and
                  shows the pattern of whichever reward currently occupies that
                  port, so the cues follow the rewards when they swap.

        Returns the patterns actually shown (empty in "none" mode).
        """
        mode = self._screens["mode"]
        if mode == "none":
            return []

        if mode == "static":
            patterns = list(self._screens[phase])[:_SCREEN_COUNT]
            if phase == "trial" and self._screens["randomize"]:
                random.shuffle(patterns)
        else:  # dynamic
            by_rid = {row["id"]: row for row in self._screens["dynamic"]}
            port_to_rid = {port: rid for rid, port in self._reward_locations.items()}
            # These lookups read live runtime state — a screen with no anchoring
            # reward, or a port no reward currently sits on — not protocol keys.
            patterns = []
            for port in self._screen_anchor_ports:
                row = by_rid.get(port_to_rid.get(port))
                patterns.append(row[phase] if row else "black")

        for i, pattern in enumerate(patterns):
            self._screen(i + 1, pattern)
        return patterns

    # ── Mouse session log ─────────────────────────────────────────────────────

    @staticmethod
    def _mouse_json_path(protocol: dict) -> str | None:
        """Return path to {mouse_id}.json inside the mouse folder, or None."""
        meta = protocol.get("_meta", {})
        mouse = meta.get("mouse_id", "")
        if not mouse:
            return None
        try:
            from shared_states import get_data_path
            data_path = get_data_path()
        except Exception:
            return None
        return os.path.join(data_path, mouse, f"{mouse}.json")

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
            # Indexed by reward id rather than by whatever the map happens to hold,
            # so a map missing a reward raises instead of quietly running a session
            # with fewer rewards than the protocol asks for.
            return {rid: int(dist["fixed_map"][str(rid)])
                    for rid in range(1, int(protocol["rewards"]["count"]) + 1)}

        # Collect ports to exclude when the user enabled "exclude previous locations"
        excluded: set[int] = set()
        if dist["exclude_previous"]:
            json_path = self._mouse_json_path(protocol)
            if json_path:
                log = self._load_mouse_log(json_path)
                for sess in log.get("sessions", []):
                    excluded.update(int(p) for p in sess.get("reward_ports", []))
                if excluded:
                    print(f"[StateMachine] Excluding previously used ports: {sorted(excluded)}")

        return self._random_locations(
            protocol["rewards"]["count"],
            int(dist["min_spacing"]),
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

    def _maybe_switch_rewards(self, protocol: dict, trial_num: int):
        """Roll once at the start of a trial; on a hit two rewards trade lickports.

        This is a permutation of the ports already in use, never a move to a free
        one, so the set of occupied ports is invariant. That is what keeps the LED
        set stable across trials and guarantees every dynamic screen anchor still
        resolves to some reward — a future "move a reward to a free port" feature
        would break both and needs more than a swap here.
        """
        sw = protocol["rewards"]["switching"]
        # Trial 1 establishes the baseline layout, so rolls start from trial 2.
        if not sw["enabled"] or trial_num <= 1 or len(self._reward_locations) < 2:
            return
        if random.random() >= float(sw["probability"]):
            return

        a, b = random.sample(sorted(self._reward_locations), 2)
        self._reward_locations[a], self._reward_locations[b] = (
            self._reward_locations[b], self._reward_locations[a])
        self._switch_log.append({
            "trial":   trial_num,
            "rewards": [a, b],
            "ports":   [self._reward_locations[a], self._reward_locations[b]],
        })
        print(f"[StateMachine] Trial {trial_num}: rewards {a} and {b} swapped ports "
              f"→ {self._reward_locations}")

    # ── Reward delay ──────────────────────────────────────────────────────────

    def _delay_active(self) -> bool:
        """True while the session-start reward delay is still running."""
        return (self._delay_deadline is not None
                and time.monotonic() < self._delay_deadline)

    def _release_blocked(self) -> bool:
        """Release mode: the ports are inert during the delay — a lick delivers
        nothing and does not use the reward up."""
        return (self._delay_cfg["enabled"]
                and self._delay_cfg["mode"] == "release"
                and self._delay_active())

    def _reward_params(self, cfg: dict) -> tuple:
        """(probability, duration_ms) for one reward.

        Equalise mode replaces both with the delay block's values once the delay
        has passed; until then each reward keeps its own.
        """
        d = self._delay_cfg
        if d["enabled"] and d["mode"] == "equalise" and not self._delay_active():
            return float(d["probability"]), int(d["duration_ms"])
        return float(cfg["probability"]), int(cfg["duration_ms"])

    # ── LED activation ────────────────────────────────────────────────────────

    def _get_led_ports(self, protocol: dict, reward_ports: list) -> set:
        """Return the set of LED port IDs to illuminate based on protocol led_mode."""
        mode = protocol["rewards"]["led_mode"]
        if mode == "none":
            return set()
        if mode == "all":
            return set(range(1, _CIRCLE_SIZE + 1))
        if mode == "reward_only":
            return set(reward_ports)
        if mode == "neighbors":
            n = int(protocol["rewards"]["led_neighbors"])
            ports = set()
            for p in reward_ports:
                for d in range(-n, n + 1):
                    ports.add(((p - 1 + d) % _CIRCLE_SIZE) + 1)
            return ports
        return set(reward_ports)

    # ── Trial ─────────────────────────────────────────────────────────────────

    def _run_trial(self, trial_num: int, protocol: dict, sm_stop, sm_active,
                   sess_deadline: float | None = None) -> bool:
        """Execute one trial.

        Each rewarded port can be collected at most once per trial.
        On sensor trigger: roll probability; if hit, pulse the pump; regardless,
        turn off the LED and mark the port collected.

        *sess_deadline* is a time.monotonic() stamp for the end of the session. It
        bounds the "all rewards collected" end type, which otherwise has no time
        limit at all — a trial would run forever if the mouse never licked, or for
        the whole of a Release delay regardless of how short the session is.

        Returns True on normal completion, False if stopped early.
        """
        # Roll the port swap first: the reward ports, LEDs and screen cues derived
        # below must all describe the post-swap layout for this trial.
        self._maybe_switch_rewards(protocol, trial_num)

        configs       = {cfg["id"]: cfg for cfg in protocol["rewards"]["configs"]}
        reward_ports  = list(self._reward_locations.values())
        port_to_rid   = {v: k for k, v in self._reward_locations.items()}
        led_ports     = self._get_led_ports(protocol, reward_ports)

        # Beamer trial baseline: dark (Light mode) or full lit field (Shadow mode).
        self._beamer_baseline(self._shadow)

        # Touch-screen cues for this trial. Dynamic mode reads the reward layout, so
        # this has to follow the swap above.
        screen_patterns = self._screens_apply("trial")

        for p in led_ports:
            self._send(f"LED:{p}:ON")
        print(f"[StateMachine] Trial {trial_num}: started  reward ports={reward_ports}"
              + (f"  screens={screen_patterns}" if screen_patterns else ""))

        self._play_sound("trial_start")

        trial_conf  = protocol["trial"]
        end_type    = trial_conf["end_type"]
        duration    = float(trial_conf["duration_s"])

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
            if sess_deadline is not None and time.monotonic() >= sess_deadline:
                break

            # ── Sensor polling ────────────────────────────────────
            for port in reward_ports:
                if port in collected:
                    continue
                if int(self.sensor_array[port - 1]) == 1:
                    # Release delay: the port is inert, so the lick is ignored
                    # entirely — no pulse, and the reward stays available.
                    if self._release_blocked():
                        continue

                    rid  = port_to_rid[port]
                    prob, dur = self._reward_params(configs[rid])

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

        # Only on a normal end — an emergency stop returns above, silently.
        self._play_sound("trial_end")

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
        iti_type = iti_config["type"]
        shadow = self._shadow

        # Touch screens switch to their ITI patterns for the whole interval.
        self._screens_apply("iti")

        if iti_type == "time":
            # Beamer stays at the trial baseline for the fixed duration.
            return self._sleep(float(iti_config["duration_s"]), sm_stop, sm_active)

        # ── Region-based ITI (target specified in cm) ─────────────────────────
        if iti_type == "fixed_region":
            region = iti_config["region"]
            x_cm = float(region["x_cm"])
            y_cm = float(region["y_cm"])
        else:  # random_region — pick a centre uniformly inside the margin disc
            region = iti_config["random_region"]
            mx = float(region["margin_x_cm"])
            my = float(region["margin_y_cm"])
            mr = float(region["margin_radius_cm"])
            ang  = random.uniform(0, 2 * math.pi)
            dist = mr * math.sqrt(random.uniform(0, 1))
            x_cm = mx + dist * math.cos(ang)
            y_cm = my + dist * math.sin(ang)

        diameter_cm = float(region["diameter_cm"])
        required_s = (float(region["duration_s"]) if region["duration_type"] == "fixed"
                      else random.uniform(0, float(region["duration_max_s"])))

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
        # Beamer session setup. Light/shadow belongs to the ITI target region, so a
        # fixed-time ITI (no region) simply runs the beamer dark all session.
        region = self._active_iti_region(protocol)
        self._shadow      = bool(region["shadow"]) if region else False
        self._field_color = self._region_color(region) if region else [255, 255, 255]
        self._load_calib()

        # Touch-screen patterns for this session.
        self._screens = protocol["screens"]
        if self._screens["mode"] == "none":
            # Blank once up front, so a pattern left over from the Cleaning/Testing
            # tab doesn't sit on the screens for the whole session.
            for s in range(1, _SCREEN_COUNT + 1):
                self._screen(s, "black")

        self._sounds        = protocol["sounds"]
        self._speaker       = None
        self._speaker_tried = False
        self._switch_log    = []

        locations = self._assign_locations(protocol)
        if locations is None:
            print("[StateMachine] ERROR: reward location constraints are infeasible "
                  f"(count={protocol['rewards']['count']}, "
                  f"spacing={protocol['rewards']['distribution']['min_spacing']}). "
                  "Aborting session.")
            session_done.value = True
            return

        self._reward_locations = dict(locations)
        # Each screen is pinned to the port its reward starts on. Captured before any
        # switch roll, and padded with None so a screen without an anchoring reward
        # deterministically stays black rather than keeping a stale pattern.
        self._screen_anchor_ports = [locations.get(s + 1) for s in range(_SCREEN_COUNT)]

        reward_ports = sorted(locations.values())
        print(f"[StateMachine] Session started.  Reward locations: {locations}")

        # Reward delay: sampled once here, but the deadline is anchored to the
        # session clock below so the mouse-log disk write can't eat into it.
        self._delay_cfg = protocol["rewards"]["delay"]
        delay_s = 0.0
        if self._delay_cfg["enabled"]:
            delay_s = (float(self._delay_cfg["duration_s"])
                       if self._delay_cfg["duration_type"] == "fixed"
                       else random.uniform(0.0, float(self._delay_cfg["duration_max_s"])))

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
            # The initial assignment. Switching permutes which reward sits where but
            # never changes the port set, so this stays the session's port list.
            "reward_ports":  reward_ports,
            "reward_switches": [],
            "reward_delay_s":  round(delay_s, 3) if self._delay_cfg["enabled"] else None,
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

        # Anchored here rather than where it was sampled, so writing the mouse log
        # above doesn't silently shorten the delay.
        self._delay_deadline = (sess_start + delay_s
                                if self._delay_cfg["enabled"] else None)
        if self._delay_deadline is not None:
            print(f"[StateMachine] Reward delay ({self._delay_cfg['mode']}) for "
                  f"{delay_s:.1f} s from session start.")

        # Bounds a trial that ends on "all rewards collected", which otherwise has
        # no time limit of its own.
        sess_deadline = sess_start + float(sess_length) if sess_type == "time" else None

        while True:
            if sm_stop.value or not sm_active.value:
                stopped = True
                break
            if sess_type == "time" and time.monotonic() - sess_start >= sess_length:
                break
            if sess_type == "trials" and trial_num >= sess_length:
                break

            trial_num += 1
            if not self._run_trial(trial_num, protocol, sm_stop, sm_active,
                                   sess_deadline):
                stopped = True
                break

            # Run ITI only if the session will continue after this trial
            session_continues = (
                (sess_type == "time"   and time.monotonic() - sess_start < sess_length) or
                (sess_type == "trials" and trial_num < sess_length)
            )
            if session_continues:
                if not self._run_iti(protocol["intertrial"], sm_stop, sm_active):
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
                    entry["reward_switches"] = self._switch_log
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
                           pose_sm_queue=None, beamer_queue=None,
                           screen_queue=None):
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
                           beamer_queue=beamer_queue, screen_queue=screen_queue)
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
