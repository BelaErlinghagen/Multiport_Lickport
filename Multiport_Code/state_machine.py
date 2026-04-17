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
  IDLE  →  (sm_active=True)  →  [TRIAL] × N  →  DONE / STOPPED  →  IDLE
  (ITI between trials will be added in a future step)
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

    def __init__(self, command_queue, sensor_array):
        self.command_queue = command_queue
        self.sensor_array  = sensor_array   # multiprocessing.Array('i', 16)

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
        """Safety: silence every LED and pump."""
        for i in range(1, _CIRCLE_SIZE + 1):
            self._send(f"LED:{i}:OFF")
            self._send(f"MOS:{i}:OFF:0")

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

    # ── Session ───────────────────────────────────────────────────────────────

    def run(self, protocol: dict, sm_active, sm_stop, session_done):
        """Run a full session driven by *protocol*.

        Assigns reward locations, then cycles through trials until the session
        end condition is met or a stop flag is raised.

        Writes a session entry to {mouse_id}.json at start; updates it with the
        end time on completion.
        """
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

            # ITI placeholder — inter-trial interval will be added in a future step

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
                           sensor_array, protocol_queue, session_done):
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

    machine = StateMachine(command_queue, sensor_array)
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
