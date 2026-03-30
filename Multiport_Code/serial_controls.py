import serial
import time
import shared_states
import threading
import queue


class SerialControls:
    def __init__(self, baudrate = 115200):
        print("Initializing")
        self.serial_arduino1 = serial.Serial(shared_states.serial_ports[0], baudrate, timeout = 1)
        self.serial_arduino2 = serial.Serial(shared_states.serial_ports[1], baudrate, timeout = 1)
        print("[INFO] Serial connections initialized.")
        
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
    
    def _serial_object_mapping(self, input_id):
        if input_id <= 8:
            serial_object = self.serial_arduino1
            new_id = list(shared_states.lookup_tables[0].keys())[list(shared_states.lookup_tables[0].values()).index(input_id)]
        elif input_id >= 9:
            serial_object = self.serial_arduino2
            new_id = list(shared_states.lookup_tables[1].keys())[list(shared_states.lookup_tables[1].values()).index(input_id)] 
            
        else: return input_id

        return serial_object, new_id

    
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
        Checks both write queues and sends commands to the respective Arduino.
        """
        while self.running:
            sent = False
            
            try:
                cmd = self.write_queue.get_nowait()
                if cmd:
                    self.serial_arduino1.write((cmd + '\r\n').encode('utf-8'))
                    self.serial_arduino1.flush()
                    sent = True
            except queue.Empty:
                pass

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


def LED_switch(id, on_off):
    """
    Turn on/off LED: params = id, on_off (bool -> True = On, False = Off)
    """
    ser, new_id = serial_object_mapping(id)
    print(ser)
    if on_off:
        command = f"LED:{new_id}:ON\r\n"
        
    else: 
        command = f"LED:{new_id}:OFF\r\n"
    print(f"Sending: {command.strip()}")
    ser.write(command.encode("utf-8"))
    ser.flush()
    time.sleep(0.01)


def pump_trigger(id, on_off, time):
    pass

if __name__ == "__main__":
    controls = SerialControls()

    # Main Loop
    while True:
        # 1. Get synchronized data instantly
        timestamp, active_pin = controls.read_serial()
        print(timestamp, active_pin)