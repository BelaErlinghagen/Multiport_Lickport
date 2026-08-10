"""Serial communication with the two Arduinos (PortMaster boards): sends
LED/pump/BNC commands and reads back which lickports are active. See
SerialControls for the connection itself, and sensor_process() below for how
it runs as a standalone process that drains commands from the GUI/state
machine and mirrors them into hardware_state for the session CSV."""
import serial
import time
import shared_states
import threading
import queue


class _ActuatorTracker:
    """Mirrors the outbound command stream into hardware_state.HardwareState.

    Every LED/pump/BNC command — from the state machine and the Cleaning tab
    alike — passes through the one command_queue this process drains, so this
    is the single place that sees all of them.

    LEDs are simple on/off levels. Pumps and BNCs are pulses ("MOS:3:ON:500")
    with a duration and no matching off command, so their bit is latched here
    and later cleared by sweep().
    """

    # A pulse can be shorter than the 50 ms CSV sampling period (a BNC pulse
    # can be 5 ms) and vanish between two rows if not held open. Latching
    # every pulse for at least one sample period guarantees it's recorded, at
    # the cost of widening short pulses in the log.
    _MIN_LATCH_S = 0.06

    def __init__(self, hw):
        self.hw = hw
        self._pump_until = {}      # channel index → monotonic deadline
        self._bnc_until  = {}

    def note(self, command_string):
        """Update the live state from one outbound command. Never raises."""
        if self.hw is None:
            return
        try:
            parts = command_string.split(":")
            kind, channel = parts[0], int(parts[1])
            action = parts[2].upper() if len(parts) > 2 else ""
            now = time.monotonic()

            if kind == "LED" and 1 <= channel <= len(self.hw.leds):
                self.hw.leds[channel - 1] = 1 if action == "ON" else 0

            elif kind == "MOS" and 1 <= channel <= len(self.hw.pumps):
                idx = channel - 1
                if action == "ON":
                    duration = float(parts[3]) / 1000.0 if len(parts) > 3 else 0.0
                    self.hw.pumps[idx] = 1
                    self._pump_until[idx] = now + max(duration, self._MIN_LATCH_S)
                else:
                    self.hw.pumps[idx] = 0
                    self._pump_until.pop(idx, None)

            elif kind == "BNC" and 1 <= channel <= len(self.hw.bnc_pulse):
                idx = channel - 1
                duration = float(parts[3]) / 1000.0 if len(parts) > 3 else 0.0
                self.hw.bnc_pulse[idx] = 1
                self._bnc_until[idx] = now + max(duration, self._MIN_LATCH_S)
        except (ValueError, IndexError):
            # A malformed command is the writer loop's problem to report; tracking
            # must never be able to stop a command reaching the hardware.
            pass

    def sweep(self):
        """Clear latches whose pulse has elapsed. Called every pass of the loop."""
        if self.hw is None:
            return
        now = time.monotonic()
        for store, array in ((self._pump_until, self.hw.pumps),
                             (self._bnc_until, self.hw.bnc_pulse)):
            for idx in [i for i, deadline in store.items() if deadline <= now]:
                array[idx] = 0
                del store[idx]


class SerialControls:
    """Owns the two serial connections to the Arduinos: a reader thread per
    board decodes STATUS lines into active lickport ids, and one writer
    thread drains a queue of outbound LED/pump/BNC commands. See
    sensor_process() below for how this runs as its own process."""

    # Maps protocol BNC id -> (board index, board-local BNC id 1-2). Kept
    # separate from the lickport lookup_tables: BNCs aren't lickports, and
    # routing a BNC id through those tables maps it onto an invalid channel
    # that the firmware rejects.
    _BNC_MAP = {1: (0, 2), 2: (0, 1), 3: (1, 2), 4: (1, 1)}

    # A board whose USB bridge has crashed still enumerates and still opens, but
    # never drains its bulk OUT endpoint. Without a write timeout pyserial blocks
    # forever there, and since one writer thread serves both boards that would
    # stop every LED/pump/BNC command on the rig, not just the dead board's.
    _WRITE_TIMEOUT_S = 0.5

    # After this many consecutive write timeouts a board is declared dead and
    # skipped, so the writer keeps serving the healthy board at full speed
    # instead of stalling _WRITE_TIMEOUT_S on every command addressed to it.
    _MAX_WRITE_FAILURES = 3

    # Opening the port asserts DTR, which resets the board into its bootloader
    # for roughly two seconds. Commands sent during that window are swallowed
    # silently, so wait for each board's first STATUS line before returning.
    _READY_TIMEOUT_S = 6.0

    def __init__(self, baudrate = 115200, verbose = None):
        print("Initializing Serial Communication.")
        self.serial_arduino1 = serial.Serial(shared_states.serial_ports[0], baudrate,
                                             timeout=1, write_timeout=self._WRITE_TIMEOUT_S)
        self.serial_arduino2 = serial.Serial(shared_states.serial_ports[1], baudrate,
                                             timeout=1, write_timeout=self._WRITE_TIMEOUT_S)
        print("[INFO] Serial connections initialized.")

        # Consecutive write failures per port, and whether we've already said so.
        self._write_failures = {}
        self._reported_dead  = set()

        # Per-command tracing, off by default — see shared_states.serial_verbose.
        self.verbose = (bool(getattr(shared_states, "serial_verbose", False))
                        if verbose is None else bool(verbose))

        self.latest_active_pins = {0:[], 1:[]}
        self._seen_status = {0: False, 1: False}   # has this board ever reported?

        self.write_queue = queue.Queue()
        self.running = True
        self.lock = threading.Lock()

        self.reader_arduino1 = threading.Thread(
            target=self._reader_loop,
            args=(self.serial_arduino1, 0),
            daemon=True)
        self.reader_arduino2 = threading.Thread(
            target=self._reader_loop,
            args=(self.serial_arduino2, 1),
            daemon=True)

        self.writer_thread = threading.Thread(
            target=self._writer_loop,
            daemon=True
        )

        self.reader_arduino1.start()
        self.reader_arduino2.start()
        self.writer_thread.start()

        self.wait_until_ready()

    def wait_until_ready(self, timeout=None):
        """Block until both boards report in, and say which ones didn't.

        A board that never reports is either running no sketch or has a crashed
        USB bridge. Either way its LEDs/pumps/BNCs are dead, and saying so at
        start-up beats discovering it mid-session.
        """
        deadline = time.monotonic() + (self._READY_TIMEOUT_S if timeout is None else timeout)
        while time.monotonic() < deadline and not all(self._seen_status.values()):
            time.sleep(0.05)

        for index, seen in sorted(self._seen_status.items()):
            port = shared_states.serial_ports[index]
            if seen:
                print(f"[INFO] Arduino {index + 1} ready on {port}.")
            else:
                ports = "1-8" if index == 0 else "9-16"
                print(f"[ERROR] Arduino {index + 1} on {port} sent no data within "
                      f"{self._READY_TIMEOUT_S:.0f} s. Lickports {ports} (LEDs, pumps, "
                      f"lick sensors) and its two BNC outputs will not work. Unplug "
                      f"and replug that board's USB cable, then restart.")
        return all(self._seen_status.values())

    def _serial_object_mapping(self, kind, input_id):
        """Return (serial object, board-local id) for one command.

        BNC ids (1-4) map straight through _BNC_MAP. LED/MOS ids are global
        lickport numbers (1-16) and go through shared_states.lookup_tables to
        find which board carries that port and its board-local channel.
        """
        input_id = int(input_id)

        if kind == "BNC":
            if input_id not in self._BNC_MAP:
                raise ValueError(f"BNC id {input_id} out of range (1-4)")
            board, local_id = self._BNC_MAP[input_id]
            return (self.serial_arduino1 if board == 0 else self.serial_arduino2), local_id

        if input_id <= 8:
            serial_object, table = self.serial_arduino1, shared_states.lookup_tables[0]
        else:
            serial_object, table = self.serial_arduino2, shared_states.lookup_tables[1]
        for local_id, global_id in table.items():
            if global_id == input_id:
                return serial_object, local_id
        raise ValueError(f"{kind} id {input_id} is not in the lookup tables")


    def _reader_loop(self, serial_object, arduino_index):
        """Runs in a separate thread: parses this Arduino's STATUS lines into
        the set of currently active lickport ids (self.latest_active_pins)."""
        if serial_object is None:
            return
        while self.running:
            try:
                if serial_object.in_waiting > 0:
                    line = serial_object.readline().decode('utf-8', errors='ignore').strip()
                    if line.startswith("STATUS:"):
                        parts = line.split(":")
                        if len(parts) >= 3:
                            active_pins_str = parts[2]

                            # "0" means no pins are active
                            current_pins = []
                            if active_pins_str != "0":
                                current_pins = [shared_states.lookup_tables[arduino_index][int(p)] for p in active_pins_str.split(",") if p.isdigit()]


                            with self.lock:
                                self.latest_active_pins[arduino_index] = current_pins
                            self._seen_status[arduino_index] = True

                    time.sleep(0.005)
                else:
                    time.sleep(0.005)
            except Exception as e:
                # stop() closes the ports underneath us; that is a normal exit,
                # not a fault worth logging.
                if not self.running:
                    return
                print(f"[READ ERROR on Arduino {arduino_index}] {e}")
                time.sleep(0.05)

    def _write_to(self, serial_object, final_command):
        """Send one already-remapped command to a board. Never blocks for long.

        No flush() afterwards: that is tcdrain, which has no timeout at all and
        never returns on a board whose USB bridge has crashed. The kernel sends
        a queued write on its own, so waiting for it buys nothing.
        """
        port = serial_object.port
        if self._write_failures.get(port, 0) >= self._MAX_WRITE_FAILURES:
            return False        # already declared dead — don't stall on it again
        try:
            serial_object.write((final_command + '\r\n').encode('utf-8'))
        except Exception as exc:
            failures = self._write_failures.get(port, 0) + 1
            self._write_failures[port] = failures
            if failures >= self._MAX_WRITE_FAILURES and port not in self._reported_dead:
                self._reported_dead.add(port)
                print(f"[ERROR] {port} is not accepting data ({exc}). Every command "
                      f"for that board will be dropped until the GUI is restarted. "
                      f"Unplug and replug the Arduino's USB cable — a DTR reset does "
                      f"not clear this.")
            else:
                print(f"[WARN] write to {port} failed ({failures}/"
                      f"{self._MAX_WRITE_FAILURES}): {exc}")
            return False
        self._write_failures[port] = 0
        return True

    def _writer_loop(self):
        """Runs in a separate thread: pulls queued commands and sends each to
        the Arduino that owns its channel, remapping ids via
        _serial_object_mapping()."""
        while self.running:
            try:
                # Blocking get with a short timeout, so a command leaves for the
                # Arduino as soon as it is queued: polling instead would add up
                # to a poll interval of jitter to every pump pulse and TTL edge.
                try:
                    cmd_parts = self.write_queue.get(timeout=0.05)
                except queue.Empty:
                    continue

                if not cmd_parts or len(cmd_parts) < 3:
                    print(f"[WARN] Invalid command format: {cmd_parts}")
                    continue

                command_string = ":".join(cmd_parts)
                # Tracing is off by default: a BNC train would print two lines per
                # pulse and drown both the console and the session log.
                if self.verbose:
                    print(f"Processing: {command_string}")

                try:
                    input_id = int(cmd_parts[1])
                except ValueError:
                    print(f"[ERROR] Invalid input_id: {cmd_parts[1]}")
                    continue

                serial_object, new_id = self._serial_object_mapping(cmd_parts[0], input_id)

                # Rebuild the command with the board-local id in place of the
                # global lickport id.
                if len(cmd_parts) == 3:
                    final_command = f"{cmd_parts[0]}:{new_id}:{cmd_parts[2]}"
                elif len(cmd_parts) == 4:
                    final_command = f"{cmd_parts[0]}:{new_id}:{cmd_parts[2]}:{cmd_parts[3]}"
                else:
                    print(f"[WARN] Unsupported command length: {command_string}")
                    continue

                if self.verbose:
                    print(f"Sending to {serial_object.port}: {final_command}")
                self._write_to(serial_object, final_command)

            except Exception as e:
                print(f"[ERROR] Unexpected error in writer loop: {e}")

    def send_command(self, command_string):
        """Queue a command, e.g. "LED:3:ON", "MOS:5:ON:10", or "BNC:1:PULSE:5"."""
        if not self.running:
            pass

        try:
            parts = command_string.split(":")
            if len(parts) < 2:
                print(f"[WARN] Invalid command format: {command_string}")
                return
            self.write_queue.put(parts)
        except Exception as e:
            print(f"[ERROR in send_command] {e}")

    def read_serial(self):
        """Return [timestamp, [active pins on Arduino 1, active pins on Arduino 2]]."""
        timestamp = time.time()
        combined_pins = []
        with self.lock:
            # Get the latest known state from both Arduinos
            combined_pins.append(self.latest_active_pins[0])
            combined_pins.append(self.latest_active_pins[1])

        return [timestamp, combined_pins]

    def stop(self):
        """Stop the reader/writer threads and close both serial ports."""
        self.running = False
        for serial_object in (self.serial_arduino1, self.serial_arduino2):
            if not serial_object:
                continue
            try:
                # close() drains pending output first, which never finishes on a
                # board that stopped accepting data. Dropping the queue makes the
                # close return instead of hanging shutdown.
                serial_object.reset_output_buffer()
            except Exception:
                pass
            try:
                serial_object.close()
            except Exception as exc:
                print(f"[WARN] closing {serial_object.port} failed: {exc}")

def sensor_process(sensor_array, timestamp_value, command_queue=None, hw=None):
    """Process entry point: owns the two Arduino connections. Polls lick
    sensors into sensor_array, and drains command_queue (written by both the
    state machine and the Cleaning tab) to forward outbound LED/pump/BNC
    commands, mirroring each into `hw` via _ActuatorTracker for the CSV."""
    from console_log import tag_process
    tag_process("Serial")

    # Prevent Queue feeder threads from blocking this process's exit: without
    # this, terminating while a queue is full deadlocks against main waiting
    # to join it.
    timestamp_value.cancel_join_thread()
    if command_queue is not None:
        command_queue.cancel_join_thread()

    from serial_controls import SerialControls
    ser_machine = SerialControls()
    tracker = _ActuatorTracker(hw)
    while True:
        # Forward any pending GUI/state-machine commands to the Arduino.
        if command_queue is not None:
            while not command_queue.empty():
                try:
                    cmd = command_queue.get_nowait()
                    ser_machine.send_command(cmd)
                    tracker.note(cmd)
                except Exception:
                    pass
        # Runs at 100 Hz (5x the CSV sample rate), so latch expiry is accurate
        # to about 10 ms.
        tracker.sweep()
        timestamp, active_pin = ser_machine.read_serial()
        active = active_pin[0] + active_pin[1]
        for i in range(16):
            sensor_array[i] = 1 if (i+1) in active else 0
        if timestamp_value.full():
            try:
                timestamp_value.get_nowait()
            except:pass
        timestamp_value.put(timestamp)
        time.sleep(0.01)



if __name__ == "__main__":
    controls = SerialControls()

    # Main Loop
    while True:
        # 1. Get synchronized data instantly
        timestamp, active_pin = controls.read_serial()
        print(timestamp, active_pin)
        cmd = input(">>> ")
        controls.send_command(cmd)
        time.sleep(0.1)
