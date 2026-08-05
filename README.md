# The Multiport Setup - a behavioral setup for mice with 16 reward ports

<p align="middle">
  <img src="https://github.com/BelaErlinghagen/Multiport_Lickport/blob/main/MultiportComplete.jpg" width="250" />
</p>
<center>
  <i>Figure 1: 3D CAD models for the Lick-O-Tron (Left: Front, Middle: Back, Right: Case).</i>
</center>



# Documentation for the Lick-O-Tron and the PORTMASTER

The Lick-O-Tron Lickport and the PORTMASTER connectivity board are the two major components that act together in the automated Multiport system.
The design, assembly and usage of both modules are explained below in detail. Files to recreate the two modules can be found in their respective folders.

### Credit
The Lick-O-Tron and the PORTMASTER were conceptualized and designed by Bela Erlinghagen, with extensive support by Trace Robbins (https://github.com/R-Trace) and Ben Escribano (https://github.com/BenjaminEscribano) from the iBehave CADRE (Website: https://ibehave.nrw/ibots-platform/cadre/ , Github: https://github.com/iBehave-CADRE).

## The Lick-O-Tron Lickport
The idea behind the Lick-O-Tron was to create a lickport for behavioral mouse experiments that can:

1) Register lick events via a capacitive sensor.
2) Be connected to up to two peristaltic pumps which can independently eject precise volumes of liquids.
3) Allow for flexible control of an LED that is located next to the needle.

### Design

The Lick-O-Tron contains three submodules: one Arduino proMicro and two MOSFET relay modules. The Arduino proMicro, as well as the power outlets of the MOSFET relay modules recieve power via the main connector (8 pin plug). 
The Arduino's job is to register licks via a capacitive sensor (a needle that is connected to the single pin at the bottom of the Lick-O-Tron). This is achieved with the code found ![here](https://github.com/BelaErlinghagen/Multiport_Lickport/blob/main/Lick-O-Tron/LickportArduinoCode.ino). It then transmits binary data about lick events to the Arduino Megas that are housed on the PORTMASTER. The MOSFET modules are activated by the master Arduinos on the PORTMASTER and can trigger peristaltic pumps (or valves, etc...). The idea here is that lick information is recieved by the master Arduinos and then, depending on the needs of the experimenter, pumps are activated at set timepoints/with set PWM. This allows for very flexible control of liquid dispensation. The Lick-O-Tron further connects the master Arduinos with an LED, which can be independently controlled.

<p align="middle">
  <img src="https://github.com/BelaErlinghagen/Multiport_Lickport/blob/main/Lick-O-Tron/Lick-O-TronFront.png" width="250" />
  <img src="https://github.com/BelaErlinghagen/Multiport_Lickport/blob/main/Lick-O-Tron/Lick-O-TronBack.png" width="250" /> 
  <img src="https://github.com/BelaErlinghagen/Multiport_Lickport/blob/main/Lick-O-Tron/Lick-O-TronCase.png" width="250" />
</p>
<center>
  <i>Figure 2: 3D CAD models for the Lick-O-Tron (Left: Front, Middle: Back, Right: Case).</i>
</center>

### Assembly
#### Parts
| Item | Quantity | Manufactured by | EAN |
| :---------- | ----------: | :---------- | ----------: |
| Joy It Pro Micro | 1 | Joy It | 4250236822907 |
| MOSFET Relay Module | 2 | tbd | tbd |
| Mini Peristaltic Pump | 1 (up to 2) | Whadda | 5410329730017 |
| Lick-O-Tron PCB | 1 | Eurocircuits | None: see Folder in Repo |
| Lick-O-Tron Case Parts | 1 each | 3D Printed | None: see Folder in Repo |
| LED (white) | 1 | TRU Components | 2050004902785 |
| 10 MOhm Resistor | 1 | TRU Components | 2050004926057 |
| 470 Ohm Resistor | 1 | TRU Components | 4016139313023 |
| M3 Screws | 4 | Toolcraft | 4064161107578 |
| M3 Nuts | 4 | SWG Hox | 4053056028951 |
| Sterican Canula | 2 | Roth | - |


### Usage




## The PORTMASTER

<p align="middle">
  <img src="https://github.com/BelaErlinghagen/Multiport_Lickport/blob/main/PORTMASTER/Portmaster.jpg" width="250" />
</p>
<center>
  <i>Figure 3: The PORTMASTER.</i>
</center>

In order to control up to 16 Lick-O-Tron lickports, the PORTMASTER was designed: two Arduino Mega Microcontroller which are connected within the Multiport-Setup to the Computer via serial connection. The 

### Design

### Assembly

### Usage
