import serial

def initialize_serial():
    pass

def read_serial():
    pass

def LED_switch(id, on_off):
    pass

def pump_trigger(id, on_off, time):
    pass

if __name__ == "__main__":
    initialize_serial()

    while True:
        output = read_serial()
        print(output)
        command = input(">>> ")
        if command