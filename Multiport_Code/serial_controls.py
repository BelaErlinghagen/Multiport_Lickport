import serial
import time
import shared_states


def initialize_serial_connections():
    try:
        time.sleep(2)  # Give time for Arduinos to reset
        shared_states.serial.append(serial.Serial('/dev/ttyACM0', 115200, timeout=1))
        shared_states.serial.append(serial.Serial('/dev/ttyACM1', 115200, timeout=1))

        print("[INFO] Serial connections initialized.")
    except serial.SerialException as e:
        print(f"[ERROR] Serial connection failed: {e}")
        shared_states.serial.append(None)
        shared_states.serial.append(None)

def read_serial():
    data = []
    for ser in shared_states.serial:
        line = ser.readline().decode("utf-8").strip()
        data.append(line)
    return data

def LED_switch(id, on_off):
    pass

def pump_trigger(id, on_off, time):
    pass

if __name__ == "__main__":
    initialize_serial_connections()

    while True:
        output = read_serial()
        print(output)
        #command = input(">>> ")
        #print(command)