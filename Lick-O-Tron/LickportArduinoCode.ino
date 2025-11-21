#include <CapacitiveSensor.h>
CapacitiveSensor cs_10_15 = CapacitiveSensor(10,15); 
int cs_output = 8;
unsigned long csSum;

void setup() {
    pinMode(cs_output, OUTPUT);
    cs_10_15.set_CS_AutocaL_Millis(0xFFFFFFFF);
}

void loop() {
    long cs1 = cs_10_15.capacitiveSensor(200);
    delay(10);
    if (cs1 > 500) { //b: Arbitrary number
      csSum += cs1;
      digitalWrite(cs_output, HIGH);
      if (csSum>=2000) { //c: This value is the threshold, a High value means it takes longer to trigger
        if (csSum > 0) { csSum = 0; } //Reset
        cs_10_15.reset_CS_AutoCal(); //Stops readings
      }
    } else {
      csSum = 0; //Timeout caused by bad readings
      digitalWrite(cs_output, LOW);
    }
}
