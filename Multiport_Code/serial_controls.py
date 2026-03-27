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
    for item in range(len(shared_states.serial)):
        ser = shared_states.serial[item]
        lookup = shared_states.lookup_tables[item]
        predecode = ser.readline()
        line = ""
        if str(predecode)[2] == "S" and str(predecode) != "0" and str(predecode)[-2] == "n":
            line = predecode.decode("utf-8", "ignore").strip()
        else: break
        extracted = []
        try:
            values = line.split(":")[2].split(",")
            for value in values:
                if int(value) != 0:
                    extracted.append(int(lookup[int(value)]))
                else: extracted.append(int(value))
        except Exception as e: print(e) 
        data.append(extracted)
    #end = time.time()
    #print(end - start)
    return data

def serial_object_mapping(input_id):
    if input_id <= 8:
        serial_object = shared_states.serial[0]
        new_id = list(shared_states.lookup_tables[0].keys())[list(shared_states.lookup_tables[0].values()).index(input_id)]
    elif input_id >= 9:
        serial_object = shared_states.serial[1]
        new_id = list(shared_states.lookup_tables[1].keys())[list(shared_states.lookup_tables[1].values()).index(input_id)] 
        
    else: return input_id

    return serial_object, new_id

def LED_switch(id, on_off):
    """
    Turn on/off LED: params = id, on_off (bool -> True = On, False = Off)
    """
    ser, new_id = serial_object_mapping(id)
    if on_off:
        ser.write(f"LED:{new_id}:ON".encode("utf-8"))
    else: 
        ser.write(f"LED:{new_id}:OFF".encode("utf-8"))


def pump_trigger(id, on_off, time):
    pass

if __name__ == "__main__":
    initialize_serial_connections()

    while True:
        output = read_serial()
        print(output)
        command = input(">>> ")
        if "LED" in command:
            parts = command.split(":")
            if parts[2] == "ON":
                LED_switch(int(parts[1]), True)
            else:
                LED_switch(int(parts[1]), False)