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

Rewards are volumes, not durations. A pump held on for more than ~10 ms shoots the
liquid instead of forming a droplet, so a reward is a train of 10 ms pulses gated by
the animal's own licking — one pulse per lick until the protocol's µL are delivered.
The µL/pulse per port comes from pump_calibration.py, and a session refuses to start
if any of its reward ports is uncalibrated. See _run_trial for the delivery rules and
shared_states' Pumps block for why the inter-pulse floor is what it is.

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
import threading
import time
from datetime import datetime

import hardware_state as _hw_state
import shared_states

_CIRCLE_SIZE  = 16   # total number of ports in the circular array
_SCREEN_COUNT = 2    # HDMI touch screens driven by screen_controls

# BNC trigger families (mirrors ProtocolPage._BNC_*_TRIGGERS).
_BNC_TRAIN_TRIGGERS = ("entire_session", "during_trial", "during_intertrial")

# ── Reward delivery (see pump_calibration.py and shared_states' Pumps block) ───
# Bound once at import: these are rig constants, and re-reading them per pulse
# inside the trial loop would only invite them changing halfway through a session.
_PUMP_PULSE_MS      = int(shared_states.pump_pulse_ms)
_PUMP_REFRACTORY_S  = shared_states.pump_refractory_ms / 1000.0
_DELIVERY_TIMEOUT_S = float(shared_states.pump_delivery_timeout_s)

# How long a REWARD_BLOCKED code is held before the port reverts to AVAILABLE. A
# release-delay lick is instantaneous, and the CSV samples every 50 ms, so without a
# latch the event would fall between two rows and never be recorded. Same reasoning
# and same value as serial_controls._ActuatorTracker._MIN_LATCH_S.
_BLOCKED_LATCH_S = 0.06


class _BncScheduler:
    """Emits BNC pulse trains from a daemon thread.

    The trains have to keep running while the main thread is between loops — during
    the mouse-log write at session start, between a trial ending and the intertrial
    starting, inside _assign_locations' retry loop. Weaving pulse deadlines into the
    three polling loops would leave every one of those as a silent gap, so the
    trains get a thread of their own and the main thread only says which phase is
    active.

    Single-shot triggers are NOT handled here — they fire straight from the main
    thread at their hook point, so their ordering against the LEDs, screens and tone
    stays deterministic.
    """

    # Wake at least this often, so a phase change or a stop is honoured promptly.
    _TICK_CAP = 0.05

    def __init__(self, send, hw=None):
        self._send   = send            # StateMachine._send
        self._hw     = hw              # hardware_state.HardwareState, for the CSV
        self._trains = []
        self._phase  = None            # None | "trial" | "iti"
        self._lock   = threading.Lock()
        self._stop   = threading.Event()
        self._thread = None

    def configure(self, bnc_config: dict) -> list:
        """Build the train list from protocol["bnc"]. Read strictly."""
        trains = []
        for out in bnc_config["outputs"]:
            if not out["enabled"]:
                continue
            for trig in out["triggers"]:
                if trig["type"] not in _BNC_TRAIN_TRIGGERS:
                    continue
                hz = float(trig["frequency_hz"])
                period = 1.0 / hz
                pulse = int(trig["pulse_ms"])
                # Re-triggering a pin that is already high extends the pulse rather
                # than making an edge, so a pulse at least as long as the period
                # would latch the line high for the whole train. The editor refuses
                # to save that, but a hand-edited file still has to be safe to run.
                limit = int(period * 1000) - 1
                if pulse > limit:
                    print(f"[StateMachine] BNC {out['id']}: pulse {pulse} ms is not "
                          f"shorter than the {period * 1000:.0f} ms period — "
                          f"clamped to {max(limit, 1)} ms.")
                    pulse = max(limit, 1)
                trains.append({"bnc": int(out["id"]), "trigger": trig["type"],
                               "period": period, "hz": hz, "pulse_ms": pulse,
                               "next_at": 0.0, "count": 0, "on": False})
        with self._lock:
            self._trains = trains
        return trains

    def start(self):
        if not self._trains:
            return
        self._stop.clear()
        with self._lock:
            self._phase = None
            self._reconcile_locked(time.monotonic())   # arms entire_session
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="bnc-scheduler")
        self._thread.start()

    def set_phase(self, phase):
        """phase: "trial" | "iti" | None. Safe to call when no trains are running."""
        if self._thread is None:
            return
        with self._lock:
            self._phase = phase
            self._reconcile_locked(time.monotonic())

    def stop(self):
        if self._thread is None:
            return
        with self._lock:
            self._phase = None
            self._reconcile_locked(time.monotonic())   # prints the stop lines
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._thread = None

    # -- internals --

    def _wanted(self, t) -> bool:
        return (t["trigger"] == "entire_session"
                or (t["trigger"] == "during_trial" and self._phase == "trial")
                or (t["trigger"] == "during_intertrial" and self._phase == "iti"))

    def _reconcile_locked(self, now):
        """Switch trains on/off for the current phase. Caller holds the lock."""
        for t in self._trains:
            want = self._wanted(t)
            if want and not t["on"]:
                # Fire at once: the first pulse of a during-trial train marks the
                # trial onset, it does not wait a period for it.
                t["on"], t["next_at"], t["count"] = True, now, 0
                self._publish(t["bnc"], 1)
                print(f"[StateMachine] BNC {t['bnc']} train started "
                      f"({t['trigger']}, {t['hz']:g} Hz, {t['pulse_ms']} ms).")
            elif not want and t["on"]:
                t["on"] = False
                self._publish(t["bnc"], 0)
                print(f"[StateMachine] BNC {t['bnc']} train stopped "
                      f"({t['trigger']}) — {t['count']} pulse(s).")

    def _publish(self, bnc_id, value):
        """Record the train's on/off *span* for the session CSV.

        The individual pulses are far shorter than the CSV's 50 ms sampling period,
        so logging them would alias into a meaningless flicker. What the log wants is
        simply whether this line was running, which is exactly what this method knows
        — and it is the only writer of bnc_train, so it never races sensor_process's
        single-pulse latch in bnc_pulse.
        """
        if self._hw is None:
            return
        idx = int(bnc_id) - 1
        if 0 <= idx < len(self._hw.bnc_train):
            self._hw.bnc_train[idx] = value

    def _loop(self):
        while not self._stop.is_set():
            now = time.monotonic()
            with self._lock:
                next_at = now + self._TICK_CAP
                for t in self._trains:
                    if not t["on"]:
                        continue
                    if t["next_at"] <= now:
                        # Never print here: a train must produce no per-pulse output.
                        self._send(f"BNC:{t['bnc']}:PULSE:{t['pulse_ms']}")
                        t["count"] += 1
                        t["next_at"] += t["period"]
                        if t["next_at"] <= now:
                            # More than a period behind (a long GC pause, a blocked
                            # queue). Resync rather than fire a catch-up burst the
                            # hardware would merge into one pulse anyway.
                            t["next_at"] = now + t["period"]
                    next_at = min(next_at, t["next_at"])
            self._stop.wait(max(0.0, min(next_at - time.monotonic(), self._TICK_CAP)))


class StateMachine:
    """Drives the trial cycle for one behavioural session.

    All hardware commands are written to *command_queue* using the text
    protocol understood by SerialControls:
        LED:id:ON / LED:id:OFF
        MOS:id:ON:duration_ms
        BNC:id:PULSE:duration_ms
    """

    def __init__(self, command_queue, sensor_array, pose_queue=None, beamer_queue=None,
                 screen_queue=None, hw=None, progress=None):
        self.command_queue = command_queue
        self.sensor_array  = sensor_array   # multiprocessing.Array('i', 16)
        self.pose_queue    = pose_queue     # optional Queue for DLC-based ITI
        self.beamer_queue  = beamer_queue   # optional Queue for beamer projection
        self.screen_queue  = screen_queue   # optional Queue for touch-screen patterns
        # Live actuator state for the session CSV. The beamer, screens and speaker
        # never touch command_queue, so serial_controls cannot see them — they are
        # mirrored from this class's own chokepoints instead (hardware_state.py).
        self.hw            = hw
        # (session_start Value('d'), trial Value('i')) for the GUI progress bar,
        # or None when nothing is watching. See _publish_progress.
        self.progress      = progress
        self._calib        = None           # BeamerCalibration, loaded at run()
        self._pump_calib   = None           # PumpCalibration, loaded at run()
        self._shadow       = False          # derived from the active ITI region
        self._field_color  = [255, 255, 255]  # stable shadow-field colour for this session
        self._screens      = {}             # protocol["screens"] for this session

        # Live reward layout. Promoted from a local so _screens_apply can follow the
        # rewards around the arena as they swap lickports.
        self._reward_locations: dict = {}     # {reward_id: port}, mutated by switching
        self._screen_anchor_ports: list = []  # port each screen is pinned to (or None)
        self._switch_log: list = []           # [{"trial", "rewards", "ports"}]
        # One record per reward outcome, written to the mouse log at session end. A
        # reward is now a volume delivered over several licks, so how much the animal
        # actually got is no longer implied by "the port was rewarded".
        self._delivery_log: list = []
        self._reward_latches: dict = {}       # port → monotonic deadline (CSV codes)

        # Reward delay (session-scoped: sampled once, measured from session start)
        self._delay_cfg: dict = {}
        self._delay_deadline = None

        # Trial tones
        self._sounds: dict = {}
        self._speaker = None
        self._speaker_tried = False

        # BNC outputs
        self._bnc_cfg: dict = {}
        self._bnc = _BncScheduler(self._send, hw)
        self._dropped_cmds = 0

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _send(self, cmd: str) -> bool:
        """Put a command on the hardware queue; count it if the queue is full.

        Still non-blocking — a stalled serial process must never freeze the trial
        loop. But a dropped command is an invisible missing TTL edge or a pump that
        never fired, so the count is reported once at the end of the session.

        Returns whether the command was actually queued. Reward delivery needs the
        answer: a dropped MOS is a droplet that never left the cannula, and counting
        it toward the target volume would make the delivery log lie about how much
        the animal drank.

        Called from the scheduler thread as well as the main one;
        multiprocessing.Queue.put_nowait is thread-safe and the counter is only a
        diagnostic, so a lost increment does not matter.
        """
        try:
            self.command_queue.put_nowait(cmd)
            return True
        except Exception:
            self._dropped_cmds += 1
            return False

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
        # The log must match what the hardware is now doing. The beamer and screens
        # were already cleared through their own chokepoints above, so only the
        # speaker (silenced out-of-band, behind its timer's back) is left.
        if self.hw is not None:
            self.hw.speaker.value = 0
        # No trial is running, so no port is offering a reward.
        self._clear_reward_states()

    # ── Reward state → CSV ────────────────────────────────────────────────────

    def _set_reward_state(self, port: int, code: int, latch_until: float = None):
        """Publish one port's REWARD_* code for the session CSV.

        *latch_until* holds a transient code (only REWARD_BLOCKED) for at least one
        CSV sample period before it reverts to REWARD_AVAILABLE; see
        _BLOCKED_LATCH_S. Terminal codes are passed without one and simply stand
        until the trial ends.
        """
        _hw_state.set_reward_state(self.hw, port, code)
        if latch_until is not None:
            self._reward_latches[port] = latch_until
        else:
            self._reward_latches.pop(port, None)

    def _sweep_reward_latches(self, now: float):
        """Revert expired transient reward codes. Called every pass of the trial loop."""
        if not self._reward_latches:
            return
        for port in [p for p, deadline in self._reward_latches.items() if deadline <= now]:
            _hw_state.set_reward_state(self.hw, port, _hw_state.REWARD_AVAILABLE)
            del self._reward_latches[port]

    def _clear_reward_states(self):
        """Back to REWARD_NONE everywhere — the trial is over, no port is rewarded."""
        self._reward_latches.clear()
        _hw_state.clear_reward_states(self.hw)

    # ── Reward delivery bookkeeping ───────────────────────────────────────────

    def _log_delivery(self, trial_num, port, rid, requested_ul, delivered_ul,
                      pulses, outcome, elapsed_s):
        """Record one reward outcome for the mouse log.

        Volumes are the µL figures; the CSV's Rewards column carries the same
        outcomes at 20 Hz for alignment with the video and the licks.
        """
        self._delivery_log.append({
            "trial":        trial_num,
            "port":         port,
            "reward_id":    rid,
            "requested_ul": round(float(requested_ul), 3),
            "delivered_ul": round(float(delivered_ul), 3),
            "pulses":       int(pulses),
            "outcome":      outcome,
            "duration_s":   round(float(elapsed_s), 2),
        })

    def _close_partial(self, trial_num, port, state, collected):
        """Close out a reward the animal stopped collecting before it was complete."""
        delivered = state["pulses_done"] * state["ul_per_pulse"]
        elapsed   = time.monotonic() - state["started"]
        print(f"[StateMachine] Trial {trial_num}: port {port} partial — "
              f"{delivered:.2f} of {state['target_ul']:.2f} µL "
              f"({state['pulses_done']}/{state['pulses_total']} pulses)")
        self._log_delivery(trial_num, port, state["rid"], state["requested_ul"],
                           delivered, state["pulses_done"], "partial", elapsed)
        self._set_reward_state(port, _hw_state.REWARD_PARTIAL)
        collected.add(port)

    def _close_partials(self, trial_num, delivery: dict, collected):
        """Close out every still-paying port. Empties *delivery*."""
        for port in list(delivery):
            self._close_partial(trial_num, port, delivery[port], collected)
            del delivery[port]

    # ── Pump calibration ──────────────────────────────────────────────────────

    def _load_pump_calib(self):
        """Load the per-pump µL/pulse table for this session.

        Deliberately not folded into _load_calib below: that method's single
        try/except would let a beamer import failure silently null the pump
        calibration too, and the session would then refuse to start for a reason
        that has nothing to do with the pumps.
        """
        try:
            from pump_calibration import PumpCalibration
            self._pump_calib = PumpCalibration()
        except Exception as exc:
            print(f"[StateMachine] Pump calibration unavailable: {exc}")
            self._pump_calib = None

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
        """Put a command on the beamer queue; drop silently if full/absent.

        The sole funnel for both _beamer_baseline and _beamer_sphere, so mirroring
        the projection into the shared state here covers everything the session
        projects. Note a diameter-0 shadow sphere is the "fully lit field" baseline,
        which is correctly recorded as the beamer being *on*.
        """
        if self.hw is not None:
            from hardware_state import write_beamer
            if cmd.get("cmd") == "sphere":
                write_beamer(self.hw, True,
                             shadow=bool(cmd.get("shadow", False)),
                             x_cm=cmd.get("x_cm", 0.0),
                             y_cm=cmd.get("y_cm", 0.0),
                             diameter_cm=cmd.get("diameter_cm", 0.0))
            elif cmd.get("cmd") == "clear":
                write_beamer(self.hw, False)
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
            return
        # Mark the speaker active for exactly the tone's length. produce_sound is
        # non-blocking (it feeds an aplay child from its own thread), so a timer is
        # what tracks the end — polling it from the trial loop would not.
        if self.hw is not None:
            self.hw.speaker.value = 1
            timer = threading.Timer(length, self._speaker_done)
            timer.daemon = True
            timer.start()

    def _speaker_done(self):
        """Timer callback: the tone has finished playing."""
        if self.hw is not None:
            self.hw.speaker.value = 0

    # ── Session progress ──────────────────────────────────────────────────────

    def _publish_progress(self, session_start=None, trial_num=None):
        """Publish session progress for the GUI's progress bar.

        *session_start* is a wall-clock time.time() (0.0 = no session running), so
        the GUI can interpolate elapsed time on its own timer instead of this loop
        having to keep a counter fresh while it is blocked inside a trial.

        Either argument may be None to leave that value alone.
        """
        if self.progress is None:
            return
        start_v, trial_v = self.progress
        if session_start is not None:
            start_v.value = float(session_start)
        if trial_num is not None:
            trial_v.value = int(trial_num)

    # ── BNC outputs ───────────────────────────────────────────────────────────

    def _bnc_fire(self, when: str):
        """Emit every single-pulse BNC trigger of kind *when*.

        Trains are the scheduler's job; these fire inline so their ordering against
        the LEDs, screens and tone is deterministic.
        """
        for out in self._bnc_cfg["outputs"]:
            if not out["enabled"]:
                continue
            for trig in out["triggers"]:
                if trig["type"] != when:
                    continue
                pulse = int(trig["pulse_ms"])
                self._send(f"BNC:{out['id']}:PULSE:{pulse}")
                print(f"[StateMachine] BNC {out['id']} pulse ({when}, {pulse} ms).")

    # ── Touch screens ─────────────────────────────────────────────────────────

    def _screen(self, screen_id: int, pattern: str):
        """Show *pattern* on one screen; drop silently if the queue is full/absent.

        Sole funnel for the screens (_screens_apply, _all_off and the session-start
        blank all come through here), so the shared state is mirrored here too. The
        pattern is normalised with screen_controls' own table so the code stored
        always matches what the screen actually shows.
        """
        if self.hw is not None:
            from screen_controls import normalize_pattern
            from hardware_state import SCREEN_PATTERNS
            codes = {name: code for code, name in SCREEN_PATTERNS.items()}
            idx = int(screen_id) - 1
            if 0 <= idx < len(self.hw.screens):
                self.hw.screens[idx] = codes.get(normalize_pattern(pattern), 0)
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
        """Return path to Data/{mouse_id}.json, or None.

        Directly in the Data folder: recordings are stored flat, and this file is
        also what the GUI enumerates mice and sessions from, since the ids cannot be
        parsed back out of the flat filenames.
        """
        meta = protocol.get("_meta", {})
        mouse = meta.get("mouse_id", "")
        if not mouse:
            return None
        try:
            from shared_states import mouse_log_path
            return mouse_log_path(mouse)
        except Exception:
            return None

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
        """(probability, volume_ul) for one reward.

        Equalise mode replaces both with the delay block's values once the delay
        has passed; until then each reward keeps its own.

        Called exactly once per port per trial, at first contact — never again while
        that port is paying out. An equalise delay elapsing mid-delivery would
        otherwise move the target volume underneath a port that is already
        delivering, possibly below what it has already given.
        """
        d = self._delay_cfg
        if d["enabled"] and d["mode"] == "equalise" and not self._delay_active():
            return float(d["probability"]), float(d["volume_ul"])
        return float(cfg["probability"]), float(cfg["volume_ul"])

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

        Each rewarded port can be collected at most once per trial, but collecting
        it is no longer a single event: a reward is a *volume*, delivered as a train
        of short pulses gated by the animal's own licking (see pump_calibration.py
        for why a long pulse is not an option — the pump shoots the liquid instead
        of forming a droplet).

        Per port, per trial:
          • First contact rolls the probability once. A miss closes the port out
            immediately with nothing delivered, exactly as before.
          • A hit snapshots the target volume and the pulse count it needs, then
            every subsequent high sensor sample — no sooner than
            pump_refractory_ms after the last one — buys one more pulse.
          • The port is collected when the volume is reached, or closed out as
            "partial" when the animal stops licking for pump_delivery_timeout_s.

        The refractory is a hard floor, not a preference: sensor_array is a 10 Hz
        sample-and-hold (the firmware only sends STATUS every 100 ms), so a shorter
        one would fire several pulses off a single sensor reading and the lick
        gating would stop meaning anything. See shared_states.pump_refractory_ms.

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
        self._bnc_fire("start_of_trial")
        self._bnc.set_phase("trial")

        trial_conf  = protocol["trial"]
        end_type    = trial_conf["end_type"]
        duration    = float(trial_conf["duration_s"])

        collected   = set()
        # Per-port delivery state for ports that rolled a hit and are paying out.
        # Everything in here is snapshotted at the roll and never re-read from the
        # protocol, so nothing can move a target volume under a port mid-delivery.
        delivery: dict = {}
        trial_start = time.monotonic()

        # Publish the trial's reward layout so the CSV records which ports were
        # rewarded, not just which pumps fired.
        for p in reward_ports:
            self._set_reward_state(p, _hw_state.REWARD_AVAILABLE)

        while True:
            # ── Stop checks ───────────────────────────────────────
            if sm_stop.value or not sm_active.value:
                for p in led_ports:
                    self._send(f"LED:{p}:OFF")
                # An in-flight reward is cut short by the stop; record what the
                # animal actually got rather than losing it.
                self._close_partials(trial_num, delivery, collected)
                self._clear_reward_states()
                # No end-of-trial pulse or tone on an emergency stop, but the
                # during-trial train must still be switched off.
                self._bnc.set_phase(None)
                return False

            # ── Trial end conditions ──────────────────────────────
            if end_type == "time" and time.monotonic() - trial_start >= duration:
                break
            if end_type == "all_rewards" and len(collected) >= len(reward_ports):
                break
            if sess_deadline is not None and time.monotonic() >= sess_deadline:
                break

            # ── Sensor polling ────────────────────────────────────
            now = time.monotonic()
            for port in [p for p in reward_ports if p not in collected]:
                high  = int(self.sensor_array[port - 1]) == 1
                state = delivery.get(port)

                # ── Not yet paying out: this is the roll ──────────
                if state is None:
                    if not high:
                        continue
                    # Release delay: the port is inert, so the lick is ignored
                    # entirely — no pulse, no roll, and the reward stays available.
                    if self._release_blocked():
                        self._set_reward_state(port, _hw_state.REWARD_BLOCKED,
                                               latch_until=now + _BLOCKED_LATCH_S)
                        continue

                    rid        = port_to_rid[port]
                    prob, vol  = self._reward_params(configs[rid])

                    if random.random() >= prob:
                        print(f"[StateMachine] Trial {trial_num}: "
                              f"contact at port {port} — probability miss (p={prob:.2f})")
                        self._log_delivery(trial_num, port, rid, vol, 0.0, 0,
                                           "miss", 0.0)
                        self._set_reward_state(port, _hw_state.REWARD_MISS)
                        collected.add(port)
                        continue

                    plan = (self._pump_calib.pulses_for(port, vol)
                            if self._pump_calib is not None else None)
                    if plan is None:
                        # run() refuses to start an uncalibrated session, so this is
                        # a should-not-happen. Close the port out rather than divide
                        # by None and take the whole session down with it.
                        print(f"[StateMachine] ERROR: port {port} has no pump "
                              f"calibration — no reward delivered.")
                        self._log_delivery(trial_num, port, rid, vol, 0.0, 0,
                                           "uncalibrated", 0.0)
                        self._set_reward_state(port, _hw_state.REWARD_PARTIAL)
                        collected.add(port)
                        continue

                    n_pulses, actual_ul = plan
                    state = {
                        "rid":          rid,
                        "requested_ul": vol,
                        "target_ul":    actual_ul,
                        "ul_per_pulse": actual_ul / n_pulses,
                        "pulses_total": n_pulses,
                        "pulses_done":  0,
                        "next_ok_at":   0.0,     # first pulse fires on this same pass
                        "last_pulse":   now,
                        "started":      now,
                    }
                    delivery[port] = state
                    self._set_reward_state(port, _hw_state.REWARD_DELIVERING)
                    print(f"[StateMachine] Trial {trial_num}: reward at port {port} "
                          f"(p={prob:.2f}) → {actual_ul:.2f} µL in {n_pulses} pulse(s)")

                # ── Paying out: one pulse per lick, refractory apart ──
                if high and now >= state["next_ok_at"]:
                    if self._send(f"MOS:{port}:ON:{_PUMP_PULSE_MS}"):
                        state["pulses_done"] += 1
                        state["last_pulse"]   = now
                    state["next_ok_at"] = now + _PUMP_REFRACTORY_S

                if state["pulses_done"] >= state["pulses_total"]:
                    delivered = state["pulses_done"] * state["ul_per_pulse"]
                    elapsed   = now - state["started"]
                    print(f"[StateMachine] Trial {trial_num}: port {port} collected — "
                          f"{delivered:.2f} µL in {state['pulses_done']} pulse(s) "
                          f"over {elapsed:.1f} s")
                    self._log_delivery(trial_num, port, state["rid"],
                                       state["requested_ul"], delivered,
                                       state["pulses_done"], "complete", elapsed)
                    self._set_reward_state(port, _hw_state.REWARD_COMPLETE)
                    collected.add(port)
                    del delivery[port]
                elif now - state["last_pulse"] >= _DELIVERY_TIMEOUT_S:
                    # The animal walked away mid-reward. Without this the port never
                    # becomes collected, and an "all rewards collected" trial in a
                    # *trials*-type session has no sess_deadline to fall back on —
                    # the loop would spin until someone hit stop.
                    self._close_partial(trial_num, port, state, collected)
                    del delivery[port]

            self._sweep_reward_latches(now)
            time.sleep(0.02)

        # Anything still mid-delivery when the trial ends is recorded as partial —
        # the animal did drink some of it, and a silent drop would make the mouse log
        # disagree with the pump pulses in the CSV.
        self._close_partials(trial_num, delivery, collected)

        # Turn off any remaining LEDs
        for p in led_ports:
            self._send(f"LED:{p}:OFF")

        # Only on a normal end — an emergency stop returns above, silently.
        self._bnc.set_phase(None)
        self._bnc_fire("end_of_trial")
        self._play_sound("trial_end")

        print(f"[StateMachine] Trial {trial_num}: ended  "
              f"({len(collected)}/{len(reward_ports)} collected)")
        self._clear_reward_states()
        return True

    # ── Intertrial interval ───────────────────────────────────────────────────

    def _run_iti(self, iti_config: dict, sm_stop, sm_active) -> bool:
        """Run the intertrial interval, with the BNC intertrial phase around it.

        A thin wrapper so the phase is cleared on every one of the body's exit
        paths — normal completion, a stop flag, or the no-calibration fallback.
        """
        self._bnc.set_phase("iti")
        try:
            return self._run_iti_body(iti_config, sm_stop, sm_active)
        finally:
            self._bnc.set_phase(None)

    def _run_iti_body(self, iti_config: dict, sm_stop, sm_active) -> bool:
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
        # Clear the logged actuator state first, so nothing from a previous session
        # (or from the Cleaning tab) leaks into the first rows of this one's CSV.
        if self.hw is not None:
            from hardware_state import reset as _reset_hw
            _reset_hw(self.hw)

        # Beamer session setup. Light/shadow belongs to the ITI target region, so a
        # fixed-time ITI (no region) simply runs the beamer dark all session.
        region = self._active_iti_region(protocol)
        self._shadow      = bool(region["shadow"]) if region else False
        self._field_color = self._region_color(region) if region else [255, 255, 255]
        self._load_calib()
        self._load_pump_calib()

        # Touch-screen patterns for this session. The CSV logs the screens only when
        # they are actually in use — in "none" mode the column stays empty rather
        # than reporting a black screen nobody is looking at.
        self._screens = protocol["screens"]
        if self.hw is not None:
            self.hw.screens_used.value = 1 if self._screens["mode"] != "none" else 0
        if self._screens["mode"] == "none":
            # Blank once up front, so a pattern left over from the Cleaning/Testing
            # tab doesn't sit on the screens for the whole session.
            for s in range(1, _SCREEN_COUNT + 1):
                self._screen(s, "black")

        self._sounds        = protocol["sounds"]
        self._speaker       = None
        self._speaker_tried = False
        self._switch_log    = []
        self._delivery_log  = []
        self._reward_latches = {}
        self._dropped_cmds  = 0

        self._bnc_cfg = protocol["bnc"]
        self._bnc.configure(self._bnc_cfg)

        locations = self._assign_locations(protocol)
        if locations is None:
            print("[StateMachine] ERROR: reward location constraints are infeasible "
                  f"(count={protocol['rewards']['count']}, "
                  f"spacing={protocol['rewards']['distribution']['min_spacing']}). "
                  "Aborting session.")
            session_done.value = True
            return

        # Every reward port must have a measured µL/pulse or the protocol's volumes
        # mean nothing. Checked here rather than earlier because a random
        # distribution does not know its ports until _assign_locations has run.
        # ExperimentPage gates this too, before it creates any files; this is the
        # backstop for a session started any other way.
        missing = (self._pump_calib.uncalibrated_ports(locations.values())
                   if self._pump_calib is not None else sorted(locations.values()))
        if missing:
            print("[StateMachine] ERROR: no pump calibration for port(s) "
                  f"{', '.join(str(p) for p in missing)}. Run the calibration wizard "
                  "on the Cleaning/Testing tab. Aborting session.")
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
            "reward_deliveries": [],
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

        # Publish the session clock for the GUI's progress bar. A wall-clock start is
        # posted once rather than an elapsed value on every tick: the GUI can then
        # interpolate smoothly on its own timer, and this loop — which spends most of
        # its life blocked inside a trial — never has to keep a counter fresh.
        # time.time(), not monotonic(): the two processes share a wall clock only.
        self._publish_progress(time.time(), 0)

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

        # try/finally so the scheduler thread is always stopped — an emergency stop,
        # an infeasible protocol or an exception must never leave it pulsing.
        self._bnc.start()
        try:
            self._bnc_fire("start_of_session")

            while True:
                if sm_stop.value or not sm_active.value:
                    stopped = True
                    break
                if sess_type == "time" and time.monotonic() - sess_start >= sess_length:
                    break
                if sess_type == "trials" and trial_num >= sess_length:
                    break

                trial_num += 1
                self._publish_progress(trial_num=trial_num)
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

            # Fires on a stop too: whatever is on the other end of the cable needs
            # to be told the session is over however it ended.
            self._bnc_fire("end_of_session")
        finally:
            self._bnc.stop()

        self._all_off()
        # Session clock back to 0 — the GUI reads that as "no session running" and
        # stops advancing the progress bar. The trial count is left standing so the
        # final "x / y trials" stays readable after the session ends.
        self._publish_progress(session_start=0.0)

        if self._dropped_cmds:
            print(f"[StateMachine] WARNING: {self._dropped_cmds} hardware "
                  f"command(s) dropped — the command queue was full.")

        # Session totals. Volume delivered is no longer implied by the reward count,
        # so it is worth one line rather than making the user open the mouse log.
        if self._delivery_log:
            total_ul = sum(d["delivered_ul"] for d in self._delivery_log)
            counts   = {}
            for d in self._delivery_log:
                counts[d["outcome"]] = counts.get(d["outcome"], 0) + 1
            breakdown = ", ".join(f"{n} {name}" for name, n in sorted(counts.items()))
            print(f"[StateMachine] Delivered {total_ul:.2f} µL over "
                  f"{len(self._delivery_log)} reward(s): {breakdown}.")

        # ── Update session log with end time ──────────────────────────────────
        if json_path:
            end_str = datetime.now().strftime("%H:%M:%S")
            log = self._load_mouse_log(json_path)
            for entry in reversed(log.get("sessions", [])):
                if (entry.get("session_id") == session_id
                        and entry.get("start_time") == start_str):
                    entry["end_time"] = end_str
                    entry["reward_switches"] = self._switch_log
                    entry["reward_deliveries"] = self._delivery_log
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
                           screen_queue=None, hw=None, progress=None):
    """Long-running process that hosts the StateMachine.

    Lifecycle:
      • Idles (sleep 50 ms) until sm_active becomes True.
      • Reads the protocol dict from protocol_queue (put there by ExperimentPage
        before setting sm_active).
      • Runs a full session, then resets sm_active to False and returns to idle.
      • Exits when sm_running becomes False.

    Called by main.py as a multiprocessing.Process target.
    """
    from console_log import tag_process
    tag_process("StateMachine")

    command_queue.cancel_join_thread()
    protocol_queue.cancel_join_thread()

    def _handle_term(_sig, _frame):
        sm_running.value = False
    signal.signal(signal.SIGTERM, _handle_term)

    machine = StateMachine(command_queue, sensor_array, pose_queue=pose_sm_queue,
                           beamer_queue=beamer_queue, screen_queue=screen_queue,
                           hw=hw, progress=progress)
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
