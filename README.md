# The Multiport Setup - a behavioral setup for mice with 16 reward ports

<p align="middle">
  <img src="https://github.com/BelaErlinghagen/Multiport_Lickport/blob/main/MultiportSetup.png" width="1000" />
</p>
<center>
  <i>Figure 1: The Multiport Setup. A) Complete view of the MultiportSetup. B) Testing adjustments to the setup to show that the capacitive sensor of the Lick-O-Tron detects mouse licks. A Basler daA1440 was used to record the lickport. The mouse was given condensed milk (white droplet in C) manually through the shown tube. C) Top: Timeline over the entire session. Sensor activations are marked as blue bars. Bottom: Zoom into one lick detection. </i>
</center>

## Components of the Multiport Setup

### Arena

The arena of the Multiport Setup is constructed from 16 red acrylic wall panels, which are held together by 16 connector pieces. This circular assembly is standing on a sheet of acrylic that has been roughened on one side, so that it is more comfortable to walk on for the mice and so that light that is projected onto the floor of the arena does not get reflected.

In addition to the circular Multiport arena, two inlets were created to create a Y-Maze.

### Lick-O-Tron and PORTMASTER

The Lick-O-Tron is a self built lickport module, which consists of a capacitive sensor, an LED and a MOSFET relay to trigger a pump/valve. 

The PORTMASTER is a hub to connect all of the Lick-O-Trons with the master Arduinos and deliver power to the pumps. 

More information about these two components can be found below.

### Computer

The computer (a Lenovo Thinkstation) used on this build had the following specifications:

| CPU: Intel(R) Core(TM) i7-14700K |
| GPU: Nvidia GeForce RTX 4060 |
| OS: Ubuntu 24.04 |

In order to run live inference with DeepLabCut, CUDA was installed alongside the necessary Nvidia GPU drivers. Pixi was used to manage the python environment of the software, the .lock and .toml files can be found in the repo.

### Projector

A BenQ Cinema Master Projector was mounted ~ 200cm above the arena floor.

### Camera

A Basler daA3840-45uc camera with an Evetar Lens (E3417A F2.4 f2.5mm 1/1.8") were mounted 40cm below the arena floor.

### Screens

In the Y-Maze configuration, two adafruit (5" 800x480 HDMI Backpack) screens were used to display patterns for the mice. The screens were directly connected to the computer via HDMI.


### Credit
The Lick-O-Tron and the PORTMASTER were conceptualized and designed by Bela Erlinghagen, with extensive support by Trace Robbins (https://github.com/R-Trace) and Ben Escribano (https://github.com/BenjaminEscribano) from the iBehave CADRE (Website: https://ibehave.nrw/ibots-platform/cadre/ , Github: https://github.com/iBehave-CADRE).


# Documentation for the Lick-O-Tron and the PORTMASTER

The Lick-O-Tron Lickport and the PORTMASTER connectivity board are the two major components that act together in the automated Multiport system.
The design, assembly and usage of both modules are explained below in detail. Files to recreate the two modules can be found in their respective folders.

## The Lick-O-Tron Lickport
The idea behind the Lick-O-Tron was to create a lickport for behavioral mouse experiments that can:

1) Register lick events via a capacitive sensor.
2) Be connected to up to two peristaltic pumps which can independently eject precise volumes of liquids.
3) Allow for flexible control of an LED that is located next to the needle.

### Design

The Lick-O-Tron contains three submodules: one Arduino proMicro and two MOSFET relay modules. The Arduino proMicro, as well as the power outlets of the MOSFET relay modules recieve power via the main connector (8 pin plug). 
The Arduino's job is to register licks via a capacitive sensor (a cannula that is connected to the single pin at the bottom of the Lick-O-Tron). This is achieved with the code found ![here](https://github.com/BelaErlinghagen/Multiport_Lickport/blob/main/Lick-O-Tron/LickportArduinoCode.ino). It then transmits binary data about lick events to the Arduino Megas that are housed on the PORTMASTER. The MOSFET modules are activated by the master Arduinos on the PORTMASTER and can trigger peristaltic pumps (or valves, etc...). The idea here is that lick information is recieved by the master Arduinos and then, depending on the needs of the experimenter, pumps are activated at set timepoints/with set PWM. This allows for very flexible control of liquid dispensation. The Lick-O-Tron further connects the master Arduinos with an LED, which can be independently controlled.

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
| Joy It proMicro | 1 | Joy It | 4250236822907 |
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

#### Step 1: Soldering

With the PCB, the proMicro, the LED, the Resistors, some pins and the Relay Modules at hand, the first step is to solder all of these components onto the PCB. By the end, the PCB should look similar to what can be seen in Figure 2.

#### Step 2: Uploading the code and testing the sensor

Upload the Lick-O-Tron ![code](https://github.com/BelaErlinghagen/Multiport_Lickport/blob/main/Lick-O-Tron/LickportArduinoCode.ino) from this repo to the JoyIt proMicro using the Arduino IDE. Then, using the serial monitor, test whether touching the sensor pin registers something.

#### Step 3: Preparing the cannula

<p align="middle">
  <img src="https://github.com/BelaErlinghagen/Multiport_Lickport/blob/main/Lick-O-Tron/CannulaExample.jpg" width="500" />
</p>
<center>
  <i>Figure 3: Example cannula assembly.</i>
</center>

In order to electrically connect the cannula to the sensor pin, for this build of the Multiport Setup, a wire was wrapped around the cannula, clamped to it and then a second wire was soldered onto the first one. This second wire was then soldered to a pin connector piece to connect to the sensor pin.

#### Step 4: Assembly

Once all those pieces are created, the Lick-O-Tron can be assembled. In the Multiport build that is previewed here, the PCB was simply put into the 3D printed chamber and everything was screwed together directly onto the arena (where the spout frame was already fixed into the acrylic sheets).

#### Step 5: Add the pump

In order to add the pump, solder two wires to the solder flags and on their other ends to a pin connector. Next, for exactly rebuilding the setup shown here, 3D print the pump holder pieces and attach the pump as shown in Fig. 4.


### Usage

<p align="middle">
  <img src="https://github.com/BelaErlinghagen/Multiport_Lickport/blob/main/Lick-O-Tron/Lick-O-Tron_insetup.jpg" width="500" />
</p>
<center>
  <i>Figure 4: All pieces together.</i>
</center>

The Lick-O-Tron is supposed to be connected via an 8 band ribbon cable to the PORTMASTER. The PORTMASTER can control up to 16 Lick-O-Trons.



## The PORTMASTER

<p align="middle">
  <img src="https://github.com/BelaErlinghagen/Multiport_Lickport/blob/main/PORTMASTER/Portmaster.jpg" width="700" />
</p>
<center>
  <i>Figure 5: The PORTMASTER rack, lid opened.</i>
</center> 

### Design

The PORTMASTER is designed to: 
- enable users to power up to 5 6V pumps comfortably (more are possible)
- provide an easily accessible interface between the Arduinos and the computer/BNCs/power supply

### Assembly
#### Parts
| Item | Quantity | Manufactured by | EAN |
| :---------- | ----------: | :---------- | ----------: |
| Arduino Mega 2560 Rev3  | 2 | Arduino | 7630049200067 |
| 8 band ribbon cable | - | - | - |
| 8-Way IDC Connector Female for Cable, 1 Row | 32 | Wurth Elektronik | 661008151923 (Mf. part#) |
| PORTMASTER PCB | 1 | Eurocircuits | None: see Folder in Repo |
| RSP-100-5 SMPSU 5V DC 20A 100W | 1 | MEAN WELL | 4711287435657 |

The remaining parts that can be seen in Figure 5 have been left out on purpose, because they are specific to the spatial properties of where the Multiport_Setup and especially the PORTMASTER was housed.

#### Step 1: Solder pins and connector pieces to the PCB

In order to connect the 2 Arduino Mega to the PCB, first the pins and connector pieces for the power supply and BNCs have to be soldered onto the PCB.

#### Step 2: Wiring

Once the Arduinos are in pace, all that is left to do is the wiring. To connect to the 16 Lick-O-Trons, an 8 band ribbon cable can be used, together with two 8-Way IDC connectors, one that goes on the Lick-O-Trons and one that goes on the PORTMASTER. Then, the power supply needs to be connected to power and to the PCB (Safety: make sure to properly ground the casing of the PORTMASTER). In the build showcased here, male BNC plugs were also connected to the PCB.

### Usage

The PORTMASTER can be connected to a computer via two USB-B to USB-A cables. It can then be utilized with the ![custom software](https://github.com/BelaErlinghagen/Multiport_Lickport/tree/main/Multiport_Code).
