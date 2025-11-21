# Documentation for the Lick-O-Tron and the PORTMASTER

The Lick-O-Tron Lickport and the PORTMASTER connectivity board are the two major components that act together in the automated Multiport system.
The design, assembly and usage of both modules are explained below in detail. Files to recreate the two modules can be found in their respective folders.

### Credit
The Lick-O-Tron and the PORTMASTER were conceptualized and designed by Bela Erlinghagen, with extensive support by Trace Robbins (https://github.com/R-Trace) and Ben Escribano (https://github.com/BenjaminEscribano) from the iBehave CADRE (Website: https://ibehave.nrw/ibots-platform/cadre/ , Github: https://github.com/iBehave-CADRE).

## The Lick-O-Tron Lickport
The idea behind the Lick-O-Tron was to create a lickport for behavioral mouse experiments that can:

1) Register lick events via a capacitive sensor.
2) Be connected to two peristaltic pumps which can independently eject precise volumes of liquids.
3) Allow for flexible control of a LED that is located next to the needle.

### Design

<p align="middle">
  <img src="https://github.com/BelaErlinghagen/Multiport_Lickport/blob/main/Lick-O-Tron/Lick-O-TronFront.png" width="250" />
  <img src="https://github.com/BelaErlinghagen/Multiport_Lickport/blob/main/Lick-O-Tron/Lick-O-TronBack.png" width="250" /> 
  <img src="https://github.com/BelaErlinghagen/Multiport_Lickport/blob/main/Lick-O-Tron/Lick-O-TronCase.png" width="250" />
</p>

The Lick-O-Tron contains three submodules: one Arduino proMicro and two MOSFET relay modules. The Arduino proMicro, as well as the power outlets of the MOSFET relay modules recieve power via the main connector (8 pin plug). 
The Arduino's job is to register licks via a capacitive sensor (a needle that is connected to the single pin at the bottom of the Lick-O-Tron). This is achieved with the code found in ![site]() It then transmits binary data about lick events to the Arduino Megas that are housed on the PORTMASTER.

### Assembly

### Usage




## The PORTMASTER

In order to control up to 16 Lick-O-Tron lickports, the PORTMASTER was designed: two Arduino Mega Microcontroller which are connected within the Multiport-Setup to the Computer via serial connection. The 

### Design

### Assembly

### Usage
