/*
 * 
 * Hardware Configuration:
 * - 8 Inputs (Capacitive Sensors): Pins 30, 32, 34, 36, 38, 40, 42, 44
 * - 8 LEDs: Pins 31, 33, 35, 37, 39, 41, 43, 45
 * - 16 MOSFET Relays: Pins 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 22, 24, 26, 28
 * - 2 BNC Outputs: Pins 23, 25
 * 
 * Serial Protocol:
 * - Output: STATUS:<timestamp_ms>:<active_pin_numbers>
 *   (e.g., STATUS:1234567890:2 for pin 2 active, or STATUS:1234567890:2,4,7 for multiple)
 * - Input Commands:
 *   LED:<pin_id>:<state>       (state: ON, OFF, or 0-255 for PWM if supported by hardware)
 *   MOS:<pin_id>:<mode>:<duration_ms>
 *      mode: ON, OFF
 *      duration: 0 for continuous, >0 for timed pulse (ms)
 *   BNC:<pin_id>:PULSE:<duration_ms>
 */

// --- Pin Definitions ---
const int INPUT_PINS[] = {30, 32, 34, 36, 38, 40, 42, 44};
const int LED_PINS[]   = {31, 33, 35, 37, 39, 41, 43, 45};
const int MOSFET_PINS[]= {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 22, 24, 26, 28};
const int BNC_PINS[]   = {23, 25};

// --- Constants ---
const int NUM_INPUTS = 8;
const int NUM_LEDS = 8;
const int NUM_MOSFETS = 16;
const int NUM_BNC = 2;
const unsigned long STATUS_INTERVAL = 100; // Send status every 100ms

// --- State Variables ---
unsigned long lastStatusTime = 0;
bool mosfetActive[NUM_MOSFETS] = {false};
unsigned long mosfetEndTime[NUM_MOSFETS] = {0};

// BNC Timer Variables (moved to global scope for persistence)
bool bncActive[NUM_BNC] = {false};
unsigned long bncEndTime[NUM_BNC] = {0};

void setup() {
  Serial.begin(115200);
  
  // Initialize Inputs
  for (int i = 0; i < NUM_INPUTS; i++) {
    pinMode(INPUT_PINS[i], INPUT);
  }

  // Initialize LEDs
  for (int i = 0; i < NUM_LEDS; i++) {
    pinMode(LED_PINS[i], OUTPUT);
    digitalWrite(LED_PINS[i], LOW);
  }

  // Initialize MOSFETs
  for (int i = 0; i < NUM_MOSFETS; i++) {
    pinMode(MOSFET_PINS[i], OUTPUT);
    digitalWrite(MOSFET_PINS[i], LOW);
  }

  // Initialize BNC
  for (int i = 0; i < NUM_BNC; i++) {
    pinMode(BNC_PINS[i], OUTPUT);
    digitalWrite(BNC_PINS[i], LOW);
  }

  Serial.println("System Initialized. Ready.");
}

void loop() {
  unsigned long currentMillis = millis();

  // 1. Handle Serial Commands
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    processCommand(command);
  }

  // 2. Handle MOSFET Timers (Non-blocking)
  for (int i = 0; i < NUM_MOSFETS; i++) {
    if (mosfetActive[i]) {
      if (currentMillis >= mosfetEndTime[i]) {
        // Timer finished - turn off relay
        digitalWrite(MOSFET_PINS[i], LOW);
        mosfetActive[i] = false;
      }
    }
  }

  // 3. Handle BNC Timers (Non-blocking)
  for (int i = 0; i < NUM_BNC; i++) {
    if (bncActive[i]) {
      if (currentMillis >= bncEndTime[i]) {
        digitalWrite(BNC_PINS[i], LOW);
        bncActive[i] = false;
      }
    }
  }

  // 4. Read Inputs and Send Status
  if (currentMillis - lastStatusTime >= STATUS_INTERVAL) {
    lastStatusTime = currentMillis;
    sendStatus(currentMillis);
  }
}

// --- Helper Functions ---

void sendStatus(unsigned long timestamp) {
  // Collect all active input pin numbers (1-indexed for user-friendliness)
  String activePins = "";
  int activeCount = 0;
  
  for (int i = 0; i < NUM_INPUTS; i++) {
    if (digitalRead(INPUT_PINS[i]) == HIGH) {
      if (activeCount > 0) {
        activePins += ",";  // Separate multiple pins with comma
      }
      activePins += String(i + 1);  // Pin ID is 1-8 (input index + 1)
      activeCount++;
    }
  }
  
  // If no pins are active, report 0
  if (activeCount == 0) {
    activePins = "0";
  }

  // Format: STATUS:<timestamp>:<active_pin_numbers>
  // Examples: STATUS:1234567890:0 (no inputs), STATUS:1234567890:2 (pin 2), STATUS:1234567890:2,4,7 (multiple)
  Serial.print("STATUS:");
  Serial.print(timestamp);
  Serial.print(":");
  Serial.println(activePins);
}

void processCommand(String cmd) {
  // Parse command format: TYPE:ARG1:ARG2...
  int firstColon = cmd.indexOf(':');
  if (firstColon == -1) return;

  String type = cmd.substring(0, firstColon);
  String args = cmd.substring(firstColon + 1);

  if (type == "LED") {
    handleLED(args);
  } else if (type == "MOS") {
    handleMOSFET(args);
  } else if (type == "BNC") {
    handleBNC(args);
  } else {
    Serial.println("Error: Unknown command type. Use LED, MOS, or BNC.");
  }
}

void handleLED(String args) {
  // Format: ID:STATE (e.g., 1:ON or 3:OFF)
  int firstSep = args.indexOf(':');
  if (firstSep == -1) return;

  int id = args.substring(0, firstSep).toInt();
  String state = args.substring(firstSep + 1);

  if (id < 1 || id > NUM_LEDS) {
    Serial.println("Error: LED ID out of range (1-8).");
    return;
  }

  int pinIndex = id - 1;
  int pin = LED_PINS[pinIndex];

  if (state == "ON" || state == "1") {
    digitalWrite(pin, HIGH);
  } else if (state == "OFF" || state == "0") {
    digitalWrite(pin, LOW);
  } else {
    // Try to parse as PWM value (0-255) if the user sends a number
    int val = state.toInt();
    if (val >= 0 && val <= 255) {
      analogWrite(pin, val);
    } else {
      Serial.println("Error: Invalid LED state. Use ON, OFF, or 0-255.");
    }
  }
}

void handleMOSFET(String args) {
  // Format: ID:MODE:DURATION
  // Example: 1:ON:5000 (Pin 1, ON for 5 seconds)
  // Example: 2:ON:0 (Pin 2, ON continuously until turned OFF)
  // Example: 3:OFF:0 (Pin 3, OFF immediately)
  
  int sep1 = args.indexOf(':');
  if (sep1 == -1) return;
  int id = args.substring(0, sep1).toInt();
  String rest = args.substring(sep1 + 1);

  int sep2 = rest.indexOf(':');
  if (sep2 == -1) return;
  String mode = rest.substring(0, sep2);
  int duration = rest.substring(sep2 + 1).toInt();

  if (id < 1 || id > NUM_MOSFETS) {
    Serial.println("Error: MOSFET ID out of range (1-16).");
    return;
  }

  int pinIndex = id - 1;
  int pin = MOSFET_PINS[pinIndex];

  if (mode == "ON") {
    digitalWrite(pin, HIGH);
    // Set timer if duration > 0 (timed pulse)
    if (duration > 0) {
      mosfetActive[pinIndex] = true;
      mosfetEndTime[pinIndex] = millis() + duration;
    } else {
      // Continuous ON - no auto-turnoff
      mosfetActive[pinIndex] = false;
    }
  } else if (mode == "OFF") {
    digitalWrite(pin, LOW);
    mosfetActive[pinIndex] = false;
  } else {
    Serial.println("Error: Invalid MOS mode. Use ON or OFF.");
  }
}

void handleBNC(String args) {
  // Format: ID:PULSE:DURATION
  // Example: 1:PULSE:100 (Pin 23, 100ms pulse)
  
  int sep1 = args.indexOf(':');
  if (sep1 == -1) return;
  int id = args.substring(0, sep1).toInt();
  String rest = args.substring(sep1 + 1);

  int sep2 = rest.indexOf(':');
  if (sep2 == -1) return;
  String action = rest.substring(0, sep2);
  int duration = rest.substring(sep2 + 1).toInt();

  if (id < 1 || id > NUM_BNC) {
    Serial.println("Error: BNC ID out of range (1-2).");
    return;
  }

  if (action != "PULSE") {
    Serial.println("Error: BNC only supports PULSE command.");
    return;
  }

  int pin = BNC_PINS[id - 1];
  
  // Trigger Pulse (non-blocking)
  digitalWrite(pin, HIGH);
  bncActive[id-1] = true;
  bncEndTime[id-1] = millis() + duration;
}