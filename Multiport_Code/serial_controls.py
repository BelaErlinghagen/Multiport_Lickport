import serial
import time
import shared_states
import threading
import queue


class _ActuatorTracker:
    """Mirror the outbound command stream into shared_states-style live state.

    Every LED / pump / BNC command — from the state machine *and* from the Cleaning
    tab — passes through the one command_queue this process drains, so parsing them
    here is the single place that sees all of them.

    LEDs are levels (ON/OFF) and need no bookkeeping. Pumps and BNCs are *pulses*
    ("MOS:3:ON:500", "BNC:1:PULSE:5") with a duration and no matching off command,
    so their bit is latched here and expired by sweep() as the loop runs.
    """

    # A BNC pulse can be 5 ms, far shorter than the 50 ms CSV sampling period, so a
    # pulse could fire and expire entirely between two rows and never be recorded.
    # Holding every latch for at least one sample period guarantees it shows up in
    # one row. It widens sub-60 ms pulses in the log, which is the accepted trade:
    # the CSV answers "when was this line active", not "what were the exact edges".
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
    # Protocol BNC id → (board index, board-local BNC id 1-2).
    # The BNCs are NOT lickports. The lookup tables map the 16 lickports onto the
    # two boards' 8 channels each; running a BNC id through them turned BNC:2 into
    # BNC:4 and BNC:4 into BNC:8, both rejected by the firmware, which only knows
    # ids 1-2. So BNC gets its own straight-through table.
    _BNC_MAP = {1: (0, 2), 2: (0, 1), 3: (1, 2), 4: (1, 1)}

    def __init__(self, baudrate = 115200, verbose = None):
        print("Initializing Serial Communication.")
        self.serial_arduino1 = serial.Serial(shared_states.serial_ports[0], baudrate, timeout = 1)
        self.serial_arduino2 = serial.Serial(shared_states.serial_ports[1], baudrate, timeout = 1)
        print("[INFO] Serial connections initialized.")

        # Per-command tracing. Off by default — see shared_states.serial_verbose.
        self.verbose = (bool(getattr(shared_states, "serial_verbose", False))
                        if verbose is None else bool(verbose))

        self.latest_active_pins = {0:[], 1:[]}

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
    
    def _serial_object_mapping(self, kind, input_id):
        """Return (serial object, board-local id) for one command.

        *kind* is the command's first field ("LED" / "MOS" / "BNC").

        BNC ids 1-4 address the four connectors directly (1-2 on Arduino 1, 3-4 on
        Arduino 2). LED and MOS ids are global lickport numbers 1-16 and go through
        the lookup tables, which say which board carries a port and what its local
        1-8 channel is.
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
        """Runs in a separate thread: constantly listens for data"""
        if serial_object is None:
            return
        while self.running:
            if serial_object.in_waiting > 0:
                try:
                    line = serial_object.readline().decode('utf-8', errors='ignore').strip()
                    if line.startswith("STATUS:"):
                        parts = line.split(":")
                        if len(parts) >= 3:
                            active_pins_str = parts[2]
                            
                            # Parse pins (handle "0" for no pins)
                            current_pins = []
                            if active_pins_str != "0":
                                current_pins = [shared_states.lookup_tables[arduino_index][int(p)] for p in active_pins_str.split(",") if p.isdigit()]
                            
                            
                            # Update shared state atomically
                            with self.lock:
                                self.latest_active_pins[arduino_index] = current_pins
                            
                    time.sleep(0.005)
                                
                except Exception as e:
                    print(f"[READ ERROR on Arduino {arduino_index}] {e}")
                    pass
            else:
                time.sleep(0.005)
    
    def _writer_loop(self):
        """
        Runs in a separate thread.
        Checks write queue and sends commands to the respective Arduino.
        """
        while self.running:
            try:
                # Try to get an item from the queue without blocking
                cmd_parts = self.write_queue.get_nowait()
                
                # Ensure we have valid data
                if not cmd_parts or len(cmd_parts) < 3:
                    print(f"[WARN] Invalid command format: {cmd_parts}")
                    continue
                
                command_string = ":".join(cmd_parts)
                # Tracing is off by default: a BNC train would print two lines per
                # pulse and drown both the console and the session log.
                if self.verbose:
                    print(f"Processing: {command_string}")

                # Extract input_id (assuming it's the second element)
                try:
                    input_id = int(cmd_parts[1])
                except ValueError:
                    print(f"[ERROR] Invalid input_id: {cmd_parts[1]}")
                    continue

                # Get the correct serial object and mapped ID
                serial_object, new_id = self._serial_object_mapping(cmd_parts[0], input_id)

                # Reconstruct the command with the NEW mapped ID
                if len(cmd_parts) == 3:
                    final_command = f"{cmd_parts[0]}:{new_id}:{cmd_parts[2]}"
                elif len(cmd_parts) == 4:
                    final_command = f"{cmd_parts[0]}:{new_id}:{cmd_parts[2]}:{cmd_parts[3]}"
                else:
                    print(f"[WARN] Unsupported command length: {command_string}")
                    continue

                if self.verbose:
                    print(f"Sending to {serial_object.port}: {final_command}")
                serial_object.write((final_command + '\r\n').encode('utf-8'))
                serial_object.flush()
                
            except queue.Empty:
                # No items in the queue - just continue the loop
                # Optional: Add a small sleep to prevent CPU spinning
                import time
                time.sleep(0.01)
            except Exception as e:
                # Handle any other unexpected errors
                print(f"[ERROR] Unexpected error in writer loop: {e}")
    
    def send_command(self, command_string):
        """
        Takes command in the format:
        MODE(LED/MOS):MODULE_ID:ON/OFF:(IF MOS: LENGTH) or
        BNC:ID:PULSE:DURATION_ms
        Queues the command to be executed.
        """
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
        """
        Returns a synchronized snapshot of the current state.
        Returns: [Timestamp, List of all active pins (1-16)]
        """
        timestamp = time.time()
        combined_pins = []
        with self.lock:
            # Get the latest known state from both Arduinos
            combined_pins.append(self.latest_active_pins[0])
            combined_pins.append(self.latest_active_pins[1])
        
        return [timestamp, combined_pins]
    
    def stop(self):
        self.running = False
        if self.serial_arduino1: self.serial_arduino1.close()
        if self.serial_arduino2: self.serial_arduino2.close()

def sensor_process(sensor_array, timestamp_value, command_queue=None, hw=None):
    from console_log import tag_process
    tag_process("Serial")

    # Prevent Queue feeder threads from blocking this process's atexit.
    # Without this, terminating the process while the pipe is full causes a
    # deadlock: main waits for this process to exit, this process waits for
    # main to drain the queue.
    timestamp_value.cancel_join_thread()
    if command_queue is not None:
        command_queue.cancel_join_thread()

    from serial_controls import SerialControls
    ser_machine = SerialControls()
    # Mirrors the outbound commands into shared state so the session CSV can record
    # which pumps, LEDs and BNCs were active (see hardware_state.py).
    tracker = _ActuatorTracker(hw)
    while True:
        # Drain any pending GUI commands and forward them to the Arduino
        if command_queue is not None:
            while not command_queue.empty():
                try:
                    cmd = command_queue.get_nowait()
                    ser_machine.send_command(cmd)
                    tracker.note(cmd)
                except Exception:
                    pass
        # Expire pump/BNC pulse latches. This loop runs at 100 Hz, five times the
        # CSV sampling rate, so the recorded pulse edges are accurate to ~10 ms.
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