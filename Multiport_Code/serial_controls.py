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
    #start = time.time()
    data.append(time.time())
    for ser in shared_states.serial:
        predecode = ser.readline()
        if str(predecode)[2] == "S" and str(predecode) != "0" and str(predecode)[-2] == "n":
            line = predecode.decode("utf-8").strip()
        extracted = []
        try:
            values = line.split(":")[2].split(",")
            for value in values:
                extracted.append(int(value))
        except: pass
        data.append(extracted)
    #end = time.time()
    #print(end - start)
    return data

def serial_object_mapping(input_id):
    if input_id <= 8:
        serial_object = shared_states.serial[0]
        new_id = 1
    elif input_id >= 9:
        serial_object = shared_states.serial[0] 
        
    else: return input_id

    return serial_object, new_id

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