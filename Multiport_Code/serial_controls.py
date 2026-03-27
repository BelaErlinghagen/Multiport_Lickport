import serial
import time


def initialize_serial_connections():
    try:
        time.sleep(2)  # Give time for Arduinos to reset
        ser1 = serial.Serial('/dev/ttyACM0', 115200, timeout=1)
        ser2 = serial.Serial('/dev/ttyACM1', 115200, timeout=1)

        print("[INFO] Serial connections initialized.")
    except serial.SerialException as e:
        print(f"[ERROR] Serial connection failed: {e}")
        ser1 = None
        ser2 = None

def read_serial():
    pass

def LED_switch(id, on_off):
    pass

def pump_trigger(id, on_off, time):
    pass

if __name__ == "__main__":
    initialize_serial_connections()

    while True:
        output = read_serial()
        print(output)
        command = input(">>> ")
        print(command)