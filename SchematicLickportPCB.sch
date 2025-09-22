<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE eagle SYSTEM "eagle.dtd">
<eagle version="9.7.0">
<drawing>
<settings>
<setting alwaysvectorfont="no"/>
<setting verticaltext="up"/>
</settings>
<grid distance="0.1" unitdist="inch" unit="inch" style="lines" multiple="1" display="no" altdistance="0.01" altunitdist="inch" altunit="inch"/>
<layers>
<layer number="1" name="Top" color="4" fill="1" visible="no" active="no"/>
<layer number="2" name="Route2" color="16" fill="1" visible="no" active="no"/>
<layer number="3" name="Route3" color="17" fill="1" visible="no" active="no"/>
<layer number="4" name="Route4" color="18" fill="1" visible="no" active="no"/>
<layer number="5" name="Route5" color="19" fill="1" visible="no" active="no"/>
<layer number="6" name="Route6" color="25" fill="1" visible="no" active="no"/>
<layer number="7" name="Route7" color="26" fill="1" visible="no" active="no"/>
<layer number="8" name="Route8" color="27" fill="1" visible="no" active="no"/>
<layer number="9" name="Route9" color="28" fill="1" visible="no" active="no"/>
<layer number="10" name="Route10" color="29" fill="1" visible="no" active="no"/>
<layer number="11" name="Route11" color="30" fill="1" visible="no" active="no"/>
<layer number="12" name="Route12" color="20" fill="1" visible="no" active="no"/>
<layer number="13" name="Route13" color="21" fill="1" visible="no" active="no"/>
<layer number="14" name="Route14" color="22" fill="1" visible="no" active="no"/>
<layer number="15" name="Route15" color="23" fill="1" visible="no" active="no"/>
<layer number="16" name="Bottom" color="1" fill="1" visible="no" active="no"/>
<layer number="17" name="Pads" color="2" fill="1" visible="no" active="no"/>
<layer number="18" name="Vias" color="2" fill="1" visible="no" active="no"/>
<layer number="19" name="Unrouted" color="6" fill="1" visible="no" active="no"/>
<layer number="20" name="Dimension" color="24" fill="1" visible="no" active="no"/>
<layer number="21" name="tPlace" color="7" fill="1" visible="no" active="no"/>
<layer number="22" name="bPlace" color="7" fill="1" visible="no" active="no"/>
<layer number="23" name="tOrigins" color="15" fill="1" visible="no" active="no"/>
<layer number="24" name="bOrigins" color="15" fill="1" visible="no" active="no"/>
<layer number="25" name="tNames" color="7" fill="1" visible="no" active="no"/>
<layer number="26" name="bNames" color="7" fill="1" visible="no" active="no"/>
<layer number="27" name="tValues" color="7" fill="1" visible="no" active="no"/>
<layer number="28" name="bValues" color="7" fill="1" visible="no" active="no"/>
<layer number="29" name="tStop" color="7" fill="3" visible="no" active="no"/>
<layer number="30" name="bStop" color="7" fill="6" visible="no" active="no"/>
<layer number="31" name="tCream" color="7" fill="4" visible="no" active="no"/>
<layer number="32" name="bCream" color="7" fill="5" visible="no" active="no"/>
<layer number="33" name="tFinish" color="6" fill="3" visible="no" active="no"/>
<layer number="34" name="bFinish" color="6" fill="6" visible="no" active="no"/>
<layer number="35" name="tGlue" color="7" fill="4" visible="no" active="no"/>
<layer number="36" name="bGlue" color="7" fill="5" visible="no" active="no"/>
<layer number="37" name="tTest" color="7" fill="1" visible="no" active="no"/>
<layer number="38" name="bTest" color="7" fill="1" visible="no" active="no"/>
<layer number="39" name="tKeepout" color="4" fill="11" visible="no" active="no"/>
<layer number="40" name="bKeepout" color="1" fill="11" visible="no" active="no"/>
<layer number="41" name="tRestrict" color="4" fill="10" visible="no" active="no"/>
<layer number="42" name="bRestrict" color="1" fill="10" visible="no" active="no"/>
<layer number="43" name="vRestrict" color="2" fill="10" visible="no" active="no"/>
<layer number="44" name="Drills" color="7" fill="1" visible="no" active="no"/>
<layer number="45" name="Holes" color="7" fill="1" visible="no" active="no"/>
<layer number="46" name="Milling" color="3" fill="1" visible="no" active="no"/>
<layer number="47" name="Measures" color="7" fill="1" visible="no" active="no"/>
<layer number="48" name="Document" color="7" fill="1" visible="no" active="no"/>
<layer number="49" name="Reference" color="7" fill="1" visible="no" active="no"/>
<layer number="51" name="tDocu" color="7" fill="1" visible="no" active="no"/>
<layer number="52" name="bDocu" color="7" fill="1" visible="no" active="no"/>
<layer number="88" name="SimResults" color="9" fill="1" visible="yes" active="yes"/>
<layer number="89" name="SimProbes" color="9" fill="1" visible="yes" active="yes"/>
<layer number="90" name="Modules" color="5" fill="1" visible="yes" active="yes"/>
<layer number="91" name="Nets" color="2" fill="1" visible="yes" active="yes"/>
<layer number="92" name="Busses" color="1" fill="1" visible="yes" active="yes"/>
<layer number="93" name="Pins" color="2" fill="1" visible="no" active="yes"/>
<layer number="94" name="Symbols" color="4" fill="1" visible="yes" active="yes"/>
<layer number="95" name="Names" color="7" fill="1" visible="yes" active="yes"/>
<layer number="96" name="Values" color="7" fill="1" visible="yes" active="yes"/>
<layer number="97" name="Info" color="7" fill="1" visible="yes" active="yes"/>
<layer number="98" name="Guide" color="6" fill="1" visible="yes" active="yes"/>
<layer number="255" name="Undefined" color="7" fill="1" visible="yes" active="yes"/>
</layers>
<schematic xreflabel="%F%N/%S.%C%R" xrefpart="/%S.%C%R">
<libraries>
<library name="LickportLibrary" urn="urn:adsk.wipprod:fs.file:vf.VFafNuzRRkGTanVNJLVUVw">
<packages>
<package name="MOSFET_MODULE" library_version="17">
<pad name="P$1" x="-2.54" y="3.81" drill="0.6" shape="square"/>
<pad name="P$2" x="0" y="3.81" drill="0.6" shape="square"/>
<pad name="P$3" x="2.54" y="3.81" drill="0.6" shape="square"/>
<pad name="P$4" x="5.08" y="3.81" drill="0.6" shape="square"/>
<pad name="P$5" x="-1.27" y="1.27" drill="0.6"/>
<pad name="P$6" x="3.81" y="1.27" drill="0.6"/>
<pad name="P$7" x="5.715" y="24.13" drill="0.6"/>
<pad name="P$8" x="5.715" y="29.21" drill="0.6"/>
<pad name="P$9" x="-3.175" y="24.13" drill="0.6"/>
<pad name="P$10" x="-3.175" y="29.21" drill="0.6"/>
</package>
<package name="JOY_IT_PRO_MICRO" library_version="17">
<pad name="P$1" x="-10.16" y="27.94" drill="0.6"/>
<pad name="P$2" x="-10.16" y="25.4" drill="0.6"/>
<pad name="P$3" x="-10.16" y="22.86" drill="0.6"/>
<pad name="P$4" x="-10.16" y="20.32" drill="0.6"/>
<pad name="P$5" x="-10.16" y="17.78" drill="0.6"/>
<pad name="P$6" x="-10.16" y="15.24" drill="0.6"/>
<pad name="P$7" x="-10.16" y="12.7" drill="0.6"/>
<pad name="P$8" x="-10.16" y="10.16" drill="0.6"/>
<pad name="P$9" x="-10.16" y="7.62" drill="0.6"/>
<pad name="P$10" x="-10.16" y="5.08" drill="0.6"/>
<pad name="P$11" x="-10.16" y="2.54" drill="0.6"/>
<pad name="P$12" x="-10.16" y="0" drill="0.6"/>
<pad name="P$13" x="5.08" y="0" drill="0.6"/>
<pad name="P$14" x="5.08" y="2.54" drill="0.6"/>
<pad name="P$15" x="5.08" y="5.08" drill="0.6"/>
<pad name="P$16" x="5.08" y="7.62" drill="0.6"/>
<pad name="P$17" x="5.08" y="10.16" drill="0.6"/>
<pad name="P$18" x="5.08" y="12.7" drill="0.6"/>
<pad name="P$19" x="5.08" y="15.24" drill="0.6"/>
<pad name="P$20" x="5.08" y="17.78" drill="0.6"/>
<pad name="P$21" x="5.08" y="20.32" drill="0.6"/>
<pad name="P$22" x="5.08" y="22.86" drill="0.6"/>
<pad name="P$23" x="5.08" y="25.4" drill="0.6"/>
<pad name="P$24" x="5.08" y="27.94" drill="0.6"/>
</package>
<package name="1X08/90" urn="urn:adsk.eagle:footprint:22261/1" library_version="17">
<description>&lt;b&gt;PIN HEADER&lt;/b&gt;</description>
<wire x1="-10.16" y1="-1.905" x2="-7.62" y2="-1.905" width="0.1524" layer="21"/>
<wire x1="-7.62" y1="-1.905" x2="-7.62" y2="0.635" width="0.1524" layer="21"/>
<wire x1="-7.62" y1="0.635" x2="-10.16" y2="0.635" width="0.1524" layer="21"/>
<wire x1="-10.16" y1="0.635" x2="-10.16" y2="-1.905" width="0.1524" layer="21"/>
<wire x1="-8.89" y1="6.985" x2="-8.89" y2="1.27" width="0.762" layer="21"/>
<wire x1="-7.62" y1="-1.905" x2="-5.08" y2="-1.905" width="0.1524" layer="21"/>
<wire x1="-5.08" y1="-1.905" x2="-5.08" y2="0.635" width="0.1524" layer="21"/>
<wire x1="-5.08" y1="0.635" x2="-7.62" y2="0.635" width="0.1524" layer="21"/>
<wire x1="-6.35" y1="6.985" x2="-6.35" y2="1.27" width="0.762" layer="21"/>
<wire x1="-5.08" y1="-1.905" x2="-2.54" y2="-1.905" width="0.1524" layer="21"/>
<wire x1="-2.54" y1="-1.905" x2="-2.54" y2="0.635" width="0.1524" layer="21"/>
<wire x1="-2.54" y1="0.635" x2="-5.08" y2="0.635" width="0.1524" layer="21"/>
<wire x1="-3.81" y1="6.985" x2="-3.81" y2="1.27" width="0.762" layer="21"/>
<wire x1="-2.54" y1="-1.905" x2="0" y2="-1.905" width="0.1524" layer="21"/>
<wire x1="0" y1="-1.905" x2="0" y2="0.635" width="0.1524" layer="21"/>
<wire x1="0" y1="0.635" x2="-2.54" y2="0.635" width="0.1524" layer="21"/>
<wire x1="-1.27" y1="6.985" x2="-1.27" y2="1.27" width="0.762" layer="21"/>
<wire x1="0" y1="-1.905" x2="2.54" y2="-1.905" width="0.1524" layer="21"/>
<wire x1="2.54" y1="-1.905" x2="2.54" y2="0.635" width="0.1524" layer="21"/>
<wire x1="2.54" y1="0.635" x2="0" y2="0.635" width="0.1524" layer="21"/>
<wire x1="1.27" y1="6.985" x2="1.27" y2="1.27" width="0.762" layer="21"/>
<wire x1="2.54" y1="-1.905" x2="5.08" y2="-1.905" width="0.1524" layer="21"/>
<wire x1="5.08" y1="-1.905" x2="5.08" y2="0.635" width="0.1524" layer="21"/>
<wire x1="5.08" y1="0.635" x2="2.54" y2="0.635" width="0.1524" layer="21"/>
<wire x1="3.81" y1="6.985" x2="3.81" y2="1.27" width="0.762" layer="21"/>
<wire x1="5.08" y1="-1.905" x2="7.62" y2="-1.905" width="0.1524" layer="21"/>
<wire x1="7.62" y1="-1.905" x2="7.62" y2="0.635" width="0.1524" layer="21"/>
<wire x1="7.62" y1="0.635" x2="5.08" y2="0.635" width="0.1524" layer="21"/>
<wire x1="6.35" y1="6.985" x2="6.35" y2="1.27" width="0.762" layer="21"/>
<wire x1="7.62" y1="-1.905" x2="10.16" y2="-1.905" width="0.1524" layer="21"/>
<wire x1="10.16" y1="-1.905" x2="10.16" y2="0.635" width="0.1524" layer="21"/>
<wire x1="10.16" y1="0.635" x2="7.62" y2="0.635" width="0.1524" layer="21"/>
<wire x1="8.89" y1="6.985" x2="8.89" y2="1.27" width="0.762" layer="21"/>
<pad name="1" x="-8.89" y="-3.81" drill="1.016" shape="long" rot="R90"/>
<pad name="2" x="-6.35" y="-3.81" drill="1.016" shape="long" rot="R90"/>
<pad name="3" x="-3.81" y="-3.81" drill="1.016" shape="long" rot="R90"/>
<pad name="4" x="-1.27" y="-3.81" drill="1.016" shape="long" rot="R90"/>
<pad name="5" x="1.27" y="-3.81" drill="1.016" shape="long" rot="R90"/>
<pad name="6" x="3.81" y="-3.81" drill="1.016" shape="long" rot="R90"/>
<pad name="7" x="6.35" y="-3.81" drill="1.016" shape="long" rot="R90"/>
<pad name="8" x="8.89" y="-3.81" drill="1.016" shape="long" rot="R90"/>
<text x="-10.795" y="-3.81" size="1.27" layer="25" ratio="10" rot="R90">&gt;NAME</text>
<text x="12.065" y="-3.81" size="1.27" layer="27" rot="R90">&gt;VALUE</text>
<rectangle x1="-9.271" y1="0.635" x2="-8.509" y2="1.143" layer="21"/>
<rectangle x1="-6.731" y1="0.635" x2="-5.969" y2="1.143" layer="21"/>
<rectangle x1="-4.191" y1="0.635" x2="-3.429" y2="1.143" layer="21"/>
<rectangle x1="-1.651" y1="0.635" x2="-0.889" y2="1.143" layer="21"/>
<rectangle x1="0.889" y1="0.635" x2="1.651" y2="1.143" layer="21"/>
<rectangle x1="3.429" y1="0.635" x2="4.191" y2="1.143" layer="21"/>
<rectangle x1="5.969" y1="0.635" x2="6.731" y2="1.143" layer="21"/>
<rectangle x1="8.509" y1="0.635" x2="9.271" y2="1.143" layer="21"/>
<rectangle x1="-9.271" y1="-2.921" x2="-8.509" y2="-1.905" layer="21"/>
<rectangle x1="-6.731" y1="-2.921" x2="-5.969" y2="-1.905" layer="21"/>
<rectangle x1="-4.191" y1="-2.921" x2="-3.429" y2="-1.905" layer="21"/>
<rectangle x1="-1.651" y1="-2.921" x2="-0.889" y2="-1.905" layer="21"/>
<rectangle x1="0.889" y1="-2.921" x2="1.651" y2="-1.905" layer="21"/>
<rectangle x1="3.429" y1="-2.921" x2="4.191" y2="-1.905" layer="21"/>
<rectangle x1="5.969" y1="-2.921" x2="6.731" y2="-1.905" layer="21"/>
<rectangle x1="8.509" y1="-2.921" x2="9.271" y2="-1.905" layer="21"/>
</package>
<package name="1X02/90" urn="urn:adsk.eagle:footprint:22310/1" library_version="17">
<description>&lt;b&gt;PIN HEADER&lt;/b&gt;</description>
<wire x1="-2.54" y1="-1.905" x2="0" y2="-1.905" width="0.1524" layer="21"/>
<wire x1="0" y1="-1.905" x2="0" y2="0.635" width="0.1524" layer="21"/>
<wire x1="0" y1="0.635" x2="-2.54" y2="0.635" width="0.1524" layer="21"/>
<wire x1="-2.54" y1="0.635" x2="-2.54" y2="-1.905" width="0.1524" layer="21"/>
<wire x1="-1.27" y1="6.985" x2="-1.27" y2="1.27" width="0.762" layer="21"/>
<wire x1="0" y1="-1.905" x2="2.54" y2="-1.905" width="0.1524" layer="21"/>
<wire x1="2.54" y1="-1.905" x2="2.54" y2="0.635" width="0.1524" layer="21"/>
<wire x1="2.54" y1="0.635" x2="0" y2="0.635" width="0.1524" layer="21"/>
<wire x1="1.27" y1="6.985" x2="1.27" y2="1.27" width="0.762" layer="21"/>
<pad name="1" x="-1.27" y="-3.81" drill="1.016" shape="long" rot="R90"/>
<pad name="2" x="1.27" y="-3.81" drill="1.016" shape="long" rot="R90"/>
<text x="-3.175" y="-3.81" size="1.27" layer="25" ratio="10" rot="R90">&gt;NAME</text>
<text x="4.445" y="-3.81" size="1.27" layer="27" rot="R90">&gt;VALUE</text>
<rectangle x1="-1.651" y1="0.635" x2="-0.889" y2="1.143" layer="21"/>
<rectangle x1="0.889" y1="0.635" x2="1.651" y2="1.143" layer="21"/>
<rectangle x1="-1.651" y1="-2.921" x2="-0.889" y2="-1.905" layer="21"/>
<rectangle x1="0.889" y1="-2.921" x2="1.651" y2="-1.905" layer="21"/>
</package>
<package name="RESAD1176W63L850D250B" urn="urn:adsk.eagle:footprint:16378542/5" library_version="17">
<description>AXIAL Resistor, 11.76 mm pitch, 8.5 mm body length, 2.5 mm body diameter
&lt;p&gt;AXIAL Resistor package with 11.76 mm pitch, 0.63 mm lead diameter, 8.5 mm body length and 2.5 mm body diameter&lt;/p&gt;</description>
<wire x1="-4.25" y1="1.25" x2="-4.25" y2="-1.25" width="0.127" layer="21"/>
<wire x1="-4.25" y1="-1.25" x2="4.25" y2="-1.25" width="0.127" layer="21"/>
<wire x1="4.25" y1="-1.25" x2="4.25" y2="1.25" width="0.127" layer="21"/>
<wire x1="4.25" y1="1.25" x2="-4.25" y2="1.25" width="0.127" layer="21"/>
<wire x1="-4.25" y1="0" x2="-4.911" y2="0" width="0.127" layer="21"/>
<wire x1="4.25" y1="0" x2="4.911" y2="0" width="0.127" layer="21"/>
<wire x1="4.25" y1="-1.25" x2="-4.25" y2="-1.25" width="0.12" layer="51"/>
<wire x1="-4.25" y1="-1.25" x2="-4.25" y2="1.25" width="0.12" layer="51"/>
<wire x1="-4.25" y1="1.25" x2="4.25" y2="1.25" width="0.12" layer="51"/>
<wire x1="4.25" y1="1.25" x2="4.25" y2="-1.25" width="0.12" layer="51"/>
<pad name="1" x="-5.88" y="0" drill="0.83" diameter="1.43"/>
<pad name="2" x="5.88" y="0" drill="0.83" diameter="1.43"/>
<text x="0" y="1.885" size="1.27" layer="25" align="bottom-center">&gt;NAME</text>
<text x="0" y="-1.885" size="1.27" layer="27" align="top-center">&gt;VALUE</text>
</package>
<package name="RESAD724W46L381D178B" urn="urn:adsk.eagle:footprint:16378530/5" library_version="17">
<description>Axial Resistor, 7.24 mm pitch, 3.81 mm body length, 1.78 mm body diameter
&lt;p&gt;Axial Resistor package with 7.24 mm pitch (lead spacing), 0.46 mm lead diameter, 3.81 mm body length and 1.78 mm body diameter&lt;/p&gt;</description>
<wire x1="-2.16" y1="1.015" x2="-2.16" y2="-1.015" width="0.127" layer="21"/>
<wire x1="-2.16" y1="-1.015" x2="2.16" y2="-1.015" width="0.127" layer="21"/>
<wire x1="2.16" y1="-1.015" x2="2.16" y2="1.015" width="0.127" layer="21"/>
<wire x1="2.16" y1="1.015" x2="-2.16" y2="1.015" width="0.127" layer="21"/>
<wire x1="-2.16" y1="0" x2="-2.736" y2="0" width="0.127" layer="21"/>
<wire x1="2.16" y1="0" x2="2.736" y2="0" width="0.127" layer="21"/>
<wire x1="2.16" y1="-1.015" x2="-2.16" y2="-1.015" width="0.12" layer="51"/>
<wire x1="-2.16" y1="-1.015" x2="-2.16" y2="1.015" width="0.12" layer="51"/>
<wire x1="-2.16" y1="1.015" x2="2.16" y2="1.015" width="0.12" layer="51"/>
<wire x1="2.16" y1="1.015" x2="2.16" y2="-1.015" width="0.12" layer="51"/>
<pad name="1" x="-3.62" y="0" drill="0.66" diameter="1.26"/>
<pad name="2" x="3.62" y="0" drill="0.66" diameter="1.26"/>
<text x="0" y="1.65" size="1.27" layer="25" align="bottom-center">&gt;NAME</text>
<text x="0" y="-1.65" size="1.27" layer="27" align="top-center">&gt;VALUE</text>
</package>
</packages>
<packages3d>
<package3d name="1X08/90" urn="urn:adsk.eagle:package:22413/2" type="model">
<description>PIN HEADER</description>
<packageinstances>
<packageinstance name="1X08/90"/>
</packageinstances>
</package3d>
<package3d name="1X02/90" urn="urn:adsk.eagle:package:22437/2" type="model">
<description>PIN HEADER</description>
<packageinstances>
<packageinstance name="1X02/90"/>
</packageinstances>
</package3d>
<package3d name="RESAD1176W63L850D250B" urn="urn:adsk.eagle:package:16378560/5" type="model">
<description>AXIAL Resistor, 11.76 mm pitch, 8.5 mm body length, 2.5 mm body diameter
&lt;p&gt;AXIAL Resistor package with 11.76 mm pitch, 0.63 mm lead diameter, 8.5 mm body length and 2.5 mm body diameter&lt;/p&gt;</description>
<packageinstances>
<packageinstance name="RESAD1176W63L850D250B"/>
</packageinstances>
</package3d>
<package3d name="RESAD724W46L381D178B" urn="urn:adsk.eagle:package:16378561/5" type="model">
<description>Axial Resistor, 7.24 mm pitch, 3.81 mm body length, 1.78 mm body diameter
&lt;p&gt;Axial Resistor package with 7.24 mm pitch (lead spacing), 0.46 mm lead diameter, 3.81 mm body length and 1.78 mm body diameter&lt;/p&gt;</description>
<packageinstances>
<packageinstance name="RESAD724W46L381D178B"/>
</packageinstances>
</package3d>
</packages3d>
<symbols>
<symbol name="MOSFET_MODULE" library_version="17">
<pin name="PWM1" x="-22.86" y="7.62" length="middle" rot="R90"/>
<pin name="PWM2" x="-17.78" y="7.62" length="middle" rot="R90"/>
<pin name="GND1" x="-12.7" y="7.62" length="middle" rot="R90"/>
<pin name="GND2" x="-7.62" y="7.62" length="middle" rot="R90"/>
<pin name="PWM3" x="-20.32" y="2.54" length="middle" rot="R270"/>
<pin name="GND3" x="-10.16" y="2.54" length="middle" rot="R270"/>
<pin name="VOUT+" x="-30.48" y="48.26" length="middle"/>
<pin name="VOUT-" x="-30.48" y="43.18" length="middle"/>
<pin name="VIN+" x="0" y="48.26" length="middle" rot="R180"/>
<pin name="VIN-" x="0" y="43.18" length="middle" rot="R180"/>
<wire x1="-25.4" y1="50.8" x2="-25.4" y2="12.7" width="0.254" layer="94"/>
<wire x1="-25.4" y1="12.7" x2="-5.08" y2="12.7" width="0.254" layer="94"/>
<wire x1="-5.08" y1="12.7" x2="-5.08" y2="50.8" width="0.254" layer="94"/>
<wire x1="-5.08" y1="50.8" x2="-25.4" y2="50.8" width="0.254" layer="94"/>
<wire x1="-25.4" y1="12.7" x2="-25.4" y2="-2.54" width="0.254" layer="94"/>
<wire x1="-25.4" y1="-2.54" x2="-5.08" y2="-2.54" width="0.254" layer="94"/>
<wire x1="-5.08" y1="-2.54" x2="-5.08" y2="12.7" width="0.254" layer="94"/>
</symbol>
<symbol name="JOY_IT_PRO_MICRO" library_version="17">
<pin name="TX0" x="-22.86" y="22.86" length="middle"/>
<pin name="RX1" x="-22.86" y="17.78" length="middle"/>
<pin name="GND" x="-22.86" y="12.7" length="middle"/>
<pin name="GND1" x="-22.86" y="7.62" length="middle"/>
<pin name="GPIO2" x="-22.86" y="2.54" length="middle"/>
<pin name="GPIO3" x="-22.86" y="-2.54" length="middle"/>
<pin name="GPIO4" x="-22.86" y="-7.62" length="middle"/>
<pin name="GPIO5" x="-22.86" y="-12.7" length="middle"/>
<pin name="GPIO6" x="-22.86" y="-17.78" length="middle"/>
<pin name="GPIO7" x="-22.86" y="-22.86" length="middle"/>
<pin name="GPIO8" x="-22.86" y="-27.94" length="middle"/>
<pin name="GPIO9" x="-22.86" y="-33.02" length="middle"/>
<pin name="GPIO10" x="10.16" y="-33.02" length="middle" rot="R180"/>
<pin name="GPIO16" x="10.16" y="-27.94" length="middle" rot="R180"/>
<pin name="GPIO14" x="10.16" y="-22.86" length="middle" rot="R180"/>
<pin name="GPIO15" x="10.16" y="-17.78" length="middle" rot="R180"/>
<pin name="A0" x="10.16" y="-12.7" length="middle" rot="R180"/>
<pin name="A1" x="10.16" y="-7.62" length="middle" rot="R180"/>
<pin name="A2" x="10.16" y="-2.54" length="middle" rot="R180"/>
<pin name="A3" x="10.16" y="2.54" length="middle" rot="R180"/>
<pin name="VCC" x="10.16" y="7.62" length="middle" rot="R180"/>
<pin name="RST" x="10.16" y="12.7" length="middle" rot="R180"/>
<pin name="GND2" x="10.16" y="17.78" length="middle" rot="R180"/>
<pin name="RAW" x="10.16" y="22.86" length="middle" rot="R180"/>
<wire x1="-17.78" y1="25.4" x2="-17.78" y2="-35.56" width="0.254" layer="94"/>
<wire x1="-17.78" y1="-35.56" x2="5.08" y2="-35.56" width="0.254" layer="94"/>
<wire x1="5.08" y1="-35.56" x2="5.08" y2="25.4" width="0.254" layer="94"/>
<wire x1="5.08" y1="25.4" x2="-17.78" y2="25.4" width="0.254" layer="94"/>
<text x="-5.08" y="2.54" size="1.778" layer="94" rot="R270">proMicro</text>
</symbol>
<symbol name="PINHD8" library_version="17">
<wire x1="-6.35" y1="-10.16" x2="1.27" y2="-10.16" width="0.4064" layer="94"/>
<wire x1="1.27" y1="-10.16" x2="1.27" y2="12.7" width="0.4064" layer="94"/>
<wire x1="1.27" y1="12.7" x2="-6.35" y2="12.7" width="0.4064" layer="94"/>
<wire x1="-6.35" y1="12.7" x2="-6.35" y2="-10.16" width="0.4064" layer="94"/>
<text x="-6.35" y="13.335" size="1.778" layer="95">&gt;NAME</text>
<text x="-6.35" y="-12.7" size="1.778" layer="96">&gt;VALUE</text>
<pin name="1" x="-2.54" y="10.16" visible="pad" length="short" direction="pas" function="dot"/>
<pin name="2" x="-2.54" y="7.62" visible="pad" length="short" direction="pas" function="dot"/>
<pin name="3" x="-2.54" y="5.08" visible="pad" length="short" direction="pas" function="dot"/>
<pin name="4" x="-2.54" y="2.54" visible="pad" length="short" direction="pas" function="dot"/>
<pin name="5" x="-2.54" y="0" visible="pad" length="short" direction="pas" function="dot"/>
<pin name="6" x="-2.54" y="-2.54" visible="pad" length="short" direction="pas" function="dot"/>
<pin name="7" x="-2.54" y="-5.08" visible="pad" length="short" direction="pas" function="dot"/>
<pin name="8" x="-2.54" y="-7.62" visible="pad" length="short" direction="pas" function="dot"/>
</symbol>
<symbol name="PINHD2" library_version="17">
<wire x1="-6.35" y1="-2.54" x2="1.27" y2="-2.54" width="0.4064" layer="94"/>
<wire x1="1.27" y1="-2.54" x2="1.27" y2="5.08" width="0.4064" layer="94"/>
<wire x1="1.27" y1="5.08" x2="-6.35" y2="5.08" width="0.4064" layer="94"/>
<wire x1="-6.35" y1="5.08" x2="-6.35" y2="-2.54" width="0.4064" layer="94"/>
<text x="-6.35" y="5.715" size="1.778" layer="95">&gt;NAME</text>
<text x="-6.35" y="-5.08" size="1.778" layer="96">&gt;VALUE</text>
<pin name="1" x="-2.54" y="2.54" visible="pad" length="short" direction="pas" function="dot"/>
<pin name="2" x="-2.54" y="0" visible="pad" length="short" direction="pas" function="dot"/>
</symbol>
<symbol name="R" library_version="17">
<description>RESISTOR</description>
<wire x1="-2.54" y1="-0.889" x2="2.54" y2="-0.889" width="0.254" layer="94"/>
<wire x1="2.54" y1="0.889" x2="-2.54" y2="0.889" width="0.254" layer="94"/>
<wire x1="2.54" y1="-0.889" x2="2.54" y2="0.889" width="0.254" layer="94"/>
<wire x1="-2.54" y1="-0.889" x2="-2.54" y2="0.889" width="0.254" layer="94"/>
<pin name="1" x="-5.08" y="0" visible="off" length="short" direction="pas" swaplevel="1"/>
<pin name="2" x="5.08" y="0" visible="off" length="short" direction="pas" swaplevel="1" rot="R180"/>
<text x="0" y="2.54" size="1.778" layer="95" align="center">&gt;NAME</text>
<text x="0" y="-5.08" size="1.778" layer="95" align="center">&gt;SPICEMODEL</text>
<text x="0" y="-2.54" size="1.778" layer="96" align="center">&gt;VALUE</text>
<text x="0" y="-7.62" size="1.778" layer="95" align="center">&gt;SPICEEXTRA</text>
</symbol>
</symbols>
<devicesets>
<deviceset name="MOSFET_MODULE" library_version="17">
<gates>
<gate name="G$1" symbol="MOSFET_MODULE" x="12.7" y="-17.78"/>
</gates>
<devices>
<device name="" package="MOSFET_MODULE">
<connects>
<connect gate="G$1" pin="GND1" pad="P$3"/>
<connect gate="G$1" pin="GND2" pad="P$4"/>
<connect gate="G$1" pin="GND3" pad="P$6"/>
<connect gate="G$1" pin="PWM1" pad="P$1"/>
<connect gate="G$1" pin="PWM2" pad="P$2"/>
<connect gate="G$1" pin="PWM3" pad="P$5"/>
<connect gate="G$1" pin="VIN+" pad="P$8"/>
<connect gate="G$1" pin="VIN-" pad="P$7"/>
<connect gate="G$1" pin="VOUT+" pad="P$10"/>
<connect gate="G$1" pin="VOUT-" pad="P$9"/>
</connects>
<technologies>
<technology name=""/>
</technologies>
</device>
</devices>
</deviceset>
<deviceset name="JOY_IT_PRO_MICRO" library_version="17">
<gates>
<gate name="G$1" symbol="JOY_IT_PRO_MICRO" x="2.54" y="0"/>
</gates>
<devices>
<device name="" package="JOY_IT_PRO_MICRO">
<connects>
<connect gate="G$1" pin="A0" pad="P$17"/>
<connect gate="G$1" pin="A1" pad="P$18"/>
<connect gate="G$1" pin="A2" pad="P$19"/>
<connect gate="G$1" pin="A3" pad="P$20"/>
<connect gate="G$1" pin="GND" pad="P$3"/>
<connect gate="G$1" pin="GND1" pad="P$4"/>
<connect gate="G$1" pin="GND2" pad="P$23"/>
<connect gate="G$1" pin="GPIO10" pad="P$13"/>
<connect gate="G$1" pin="GPIO14" pad="P$15"/>
<connect gate="G$1" pin="GPIO15" pad="P$16"/>
<connect gate="G$1" pin="GPIO16" pad="P$14"/>
<connect gate="G$1" pin="GPIO2" pad="P$5"/>
<connect gate="G$1" pin="GPIO3" pad="P$6"/>
<connect gate="G$1" pin="GPIO4" pad="P$7"/>
<connect gate="G$1" pin="GPIO5" pad="P$8"/>
<connect gate="G$1" pin="GPIO6" pad="P$9"/>
<connect gate="G$1" pin="GPIO7" pad="P$10"/>
<connect gate="G$1" pin="GPIO8" pad="P$11"/>
<connect gate="G$1" pin="GPIO9" pad="P$12"/>
<connect gate="G$1" pin="RAW" pad="P$24"/>
<connect gate="G$1" pin="RST" pad="P$22"/>
<connect gate="G$1" pin="RX1" pad="P$2"/>
<connect gate="G$1" pin="TX0" pad="P$1"/>
<connect gate="G$1" pin="VCC" pad="P$21"/>
</connects>
<technologies>
<technology name=""/>
</technologies>
</device>
</devices>
</deviceset>
<deviceset name="PINHD-1X8" prefix="JP" library_version="17">
<description>&lt;b&gt;PIN HEADER&lt;/b&gt;</description>
<gates>
<gate name="A" symbol="PINHD8" x="0" y="0"/>
</gates>
<devices>
<device name="/90" package="1X08/90">
<connects>
<connect gate="A" pin="1" pad="1"/>
<connect gate="A" pin="2" pad="2"/>
<connect gate="A" pin="3" pad="3"/>
<connect gate="A" pin="4" pad="4"/>
<connect gate="A" pin="5" pad="5"/>
<connect gate="A" pin="6" pad="6"/>
<connect gate="A" pin="7" pad="7"/>
<connect gate="A" pin="8" pad="8"/>
</connects>
<package3dinstances>
<package3dinstance package3d_urn="urn:adsk.eagle:package:22413/2"/>
</package3dinstances>
<technologies>
<technology name="">
<attribute name="CATEGORY" value="Headers" constant="no"/>
<attribute name="DESCRIPTION" value="Header, Right Angle, 8 Position" constant="no"/>
<attribute name="MANUFACTURER" value="Generic" constant="no"/>
<attribute name="MPN" value="Generic" constant="no"/>
<attribute name="OPERATING_TEMPERATURE" value="Generic" constant="no"/>
<attribute name="PART_STATUS" value="Active" constant="no"/>
<attribute name="PITCH" value="0.100&quot; (2.54mm)" constant="no"/>
<attribute name="ROHS_COMPLIANCE" value="Generic" constant="no"/>
<attribute name="SERIES" value="Generic" constant="no"/>
<attribute name="SUB-CATEGORY" value="Headers, Male Pins" constant="no"/>
<attribute name="TYPE" value="Board to Board or Cable, Unshrouded, Through Hole, Right Angle" constant="no"/>
<attribute name="VALUE" value="PINHD-1X8/90" constant="no"/>
</technology>
</technologies>
</device>
</devices>
</deviceset>
<deviceset name="PINHD-1X2" prefix="JP" library_version="17">
<description>&lt;b&gt;PIN HEADER&lt;/b&gt;</description>
<gates>
<gate name="G$1" symbol="PINHD2" x="0" y="0"/>
</gates>
<devices>
<device name="/90" package="1X02/90">
<connects>
<connect gate="G$1" pin="1" pad="1"/>
<connect gate="G$1" pin="2" pad="2"/>
</connects>
<package3dinstances>
<package3dinstance package3d_urn="urn:adsk.eagle:package:22437/2"/>
</package3dinstances>
<technologies>
<technology name="">
<attribute name="CATEGORY" value="Headers" constant="no"/>
<attribute name="DESCRIPTION" value="Header, Right Angle, 2 Position" constant="no"/>
<attribute name="MANUFACTURER" value="Generic" constant="no"/>
<attribute name="MPN" value="Generic" constant="no"/>
<attribute name="OPERATING_TEMPERATURE" value="Generic" constant="no"/>
<attribute name="PART_STATUS" value="Active" constant="no"/>
<attribute name="PITCH" value="0.100&quot; (2.54mm)" constant="no"/>
<attribute name="ROHS_COMPLIANCE" value="Generic" constant="no"/>
<attribute name="SERIES" value="Generic" constant="no"/>
<attribute name="SUB-CATEGORY" value="Headers, Male Pins" constant="no"/>
<attribute name="TYPE" value="Board to Board or Cable, Unshrouded, Through Hole, Right Angle" constant="no"/>
<attribute name="VALUE" value="PINHD-1X2/90" constant="no"/>
</technology>
</technologies>
</device>
</devices>
</deviceset>
<deviceset name="RESISTOR" prefix="R" uservalue="yes" library_version="17">
<description>&lt;b&gt;Resistor Fixed - Generic</description>
<gates>
<gate name="G$1" symbol="R" x="0" y="0"/>
</gates>
<devices>
<device name="AXIAL-11.7MM-PITCH" package="RESAD1176W63L850D250B">
<connects>
<connect gate="G$1" pin="1" pad="1"/>
<connect gate="G$1" pin="2" pad="2"/>
</connects>
<package3dinstances>
<package3dinstance package3d_urn="urn:adsk.eagle:package:16378560/5"/>
</package3dinstances>
<technologies>
<technology name="_">
<attribute name="CATEGORY" value="Resistor" constant="no"/>
<attribute name="MANUFACTURER" value="" constant="no"/>
<attribute name="MPN" value="" constant="no"/>
<attribute name="OPERATING_TEMP" value="" constant="no"/>
<attribute name="PART_STATUS" value="" constant="no"/>
<attribute name="RATING" value="" constant="no"/>
<attribute name="ROHS_COMPLIANT" value="" constant="no"/>
<attribute name="SERIES" value="" constant="no"/>
<attribute name="SUB-CATEGORY" value="Fixed" constant="no"/>
<attribute name="THERMALLOSS" value="" constant="no"/>
<attribute name="TOLERANCE" value="" constant="no"/>
<attribute name="TYPE" value="" constant="no"/>
</technology>
</technologies>
</device>
<device name="AXIAL-7.2MM-PITCH" package="RESAD724W46L381D178B">
<connects>
<connect gate="G$1" pin="1" pad="1"/>
<connect gate="G$1" pin="2" pad="2"/>
</connects>
<package3dinstances>
<package3dinstance package3d_urn="urn:adsk.eagle:package:16378561/5"/>
</package3dinstances>
<technologies>
<technology name="_">
<attribute name="CATEGORY" value="Resistor" constant="no"/>
<attribute name="MANUFACTURER" value="" constant="no"/>
<attribute name="MPN" value="" constant="no"/>
<attribute name="OPERATING_TEMP" value="" constant="no"/>
<attribute name="PART_STATUS" value="" constant="no"/>
<attribute name="RATING" value="" constant="no"/>
<attribute name="ROHS_COMPLIANT" value="" constant="no"/>
<attribute name="SERIES" value="" constant="no"/>
<attribute name="SUB-CATEGORY" value="Fixed" constant="no"/>
<attribute name="THERMALLOSS" value="" constant="no"/>
<attribute name="TOLERANCE" value="" constant="no"/>
<attribute name="TYPE" value="" constant="no"/>
</technology>
</technologies>
</device>
</devices>
<spice>
<pinmapping spiceprefix="R">
<pinmap gate="G$1" pin="1" pinorder="1"/>
<pinmap gate="G$1" pin="2" pinorder="2"/>
</pinmapping>
</spice>
</deviceset>
</devicesets>
</library>
<library name="Connector" urn="urn:adsk.eagle:library:16378166">
<description>&lt;b&gt;Pin Headers,Terminal blocks, D-Sub, Backplane, FFC/FPC, Socket</description>
<packages>
<package name="1X01" urn="urn:adsk.eagle:footprint:22382/1" library_version="50">
<description>&lt;b&gt;PIN HEADER&lt;/b&gt;</description>
<wire x1="-0.635" y1="1.27" x2="0.635" y2="1.27" width="0.1524" layer="21"/>
<wire x1="0.635" y1="1.27" x2="1.27" y2="0.635" width="0.1524" layer="21"/>
<wire x1="1.27" y1="0.635" x2="1.27" y2="-0.635" width="0.1524" layer="21"/>
<wire x1="1.27" y1="-0.635" x2="0.635" y2="-1.27" width="0.1524" layer="21"/>
<wire x1="-1.27" y1="0.635" x2="-1.27" y2="-0.635" width="0.1524" layer="21"/>
<wire x1="-0.635" y1="1.27" x2="-1.27" y2="0.635" width="0.1524" layer="21"/>
<wire x1="-1.27" y1="-0.635" x2="-0.635" y2="-1.27" width="0.1524" layer="21"/>
<wire x1="0.635" y1="-1.27" x2="-0.635" y2="-1.27" width="0.1524" layer="21"/>
<pad name="1" x="0" y="0" drill="1.016" shape="octagon"/>
<text x="-1.3462" y="1.8288" size="1.27" layer="25" ratio="10">&gt;NAME</text>
<text x="-1.27" y="-3.175" size="1.27" layer="27">&gt;VALUE</text>
<rectangle x1="-0.254" y1="-0.254" x2="0.254" y2="0.254" layer="51"/>
</package>
</packages>
<packages3d>
<package3d name="1X01" urn="urn:adsk.eagle:package:22485/2" type="model">
<description>PIN HEADER</description>
<packageinstances>
<packageinstance name="1X01"/>
</packageinstances>
</package3d>
</packages3d>
<symbols>
<symbol name="PINHD1" urn="urn:adsk.eagle:symbol:22381/1" library_version="50">
<wire x1="-6.35" y1="-2.54" x2="1.27" y2="-2.54" width="0.4064" layer="94"/>
<wire x1="1.27" y1="-2.54" x2="1.27" y2="2.54" width="0.4064" layer="94"/>
<wire x1="1.27" y1="2.54" x2="-6.35" y2="2.54" width="0.4064" layer="94"/>
<wire x1="-6.35" y1="2.54" x2="-6.35" y2="-2.54" width="0.4064" layer="94"/>
<text x="-6.35" y="3.175" size="1.778" layer="95">&gt;NAME</text>
<text x="-6.35" y="-5.08" size="1.778" layer="96">&gt;VALUE</text>
<pin name="1" x="-2.54" y="0" visible="pad" length="short" direction="pas" function="dot"/>
</symbol>
</symbols>
<devicesets>
<deviceset name="PINHD-1X1" urn="urn:adsk.eagle:component:16378168/5" prefix="JP" library_version="50">
<description>&lt;b&gt;PIN HEADER&lt;/b&gt;</description>
<gates>
<gate name="G$1" symbol="PINHD1" x="0" y="0"/>
</gates>
<devices>
<device name="" package="1X01">
<connects>
<connect gate="G$1" pin="1" pad="1"/>
</connects>
<package3dinstances>
<package3dinstance package3d_urn="urn:adsk.eagle:package:22485/2"/>
</package3dinstances>
<technologies>
<technology name="">
<attribute name="CATEGORY" value="Headers" constant="no"/>
<attribute name="DESCRIPTION" value="Header, Straight, 1 Position " constant="no"/>
<attribute name="MANUFACTURER" value="Generic" constant="no"/>
<attribute name="MPN" value="Generic" constant="no"/>
<attribute name="OPERATING_TEMPERATURE" value="Generic" constant="no"/>
<attribute name="PART_STATUS" value="Active" constant="no"/>
<attribute name="PITCH" value="0.100&quot; (2.54mm)" constant="no"/>
<attribute name="ROHS_COMPLIANCE" value="Generic" constant="no"/>
<attribute name="SERIES" value="Generic" constant="no"/>
<attribute name="SUB-CATEGORY" value="Headers, Male Pins" constant="no"/>
<attribute name="TYPE" value="Board to Board or Cable, Unshrouded, Through Hole, Straight" constant="no"/>
<attribute name="VALUE" value="PINHD-1X1" constant="no"/>
</technology>
</technologies>
</device>
</devices>
</deviceset>
</devicesets>
</library>
<library name="Opto-Electronic" urn="urn:adsk.eagle:library:16378487">
<description>&lt;B&gt;LED, Display, Optocoupler, Photoemitter</description>
<packages>
<package name="LEDRD254W60D565H860B_B" urn="urn:adsk.eagle:footprint:38808346/2" library_version="21">
<description>Radial LED (Round), 2.54 mm pitch, 5.65 mm body diameter, 8.60 mm body height
 &lt;p&gt;Radial LED (Round) package  with 2.54 mm pitch (lead spacing), 0.60 mm lead width, 0.50 mm lead thickness, 5.65 mm body diameter and 8.60 mm body height&lt;/p&gt;</description>
<pad name="A" x="-1.27" y="0" drill="0.981" diameter="1.581"/>
<pad name="C" x="1.27" y="0" drill="0.981" diameter="1.581"/>
<wire x1="-2.825" y1="0" x2="2.5425" y2="1.2314" width="0.12" layer="21" curve="-154.1581"/>
<wire x1="2.5425" y1="-1.2314" x2="-2.825" y2="0" width="0.12" layer="21" curve="-154.1581"/>
<wire x1="2.5425" y1="1.2314" x2="2.5425" y2="-1.2314" width="0.12" layer="21"/>
<wire x1="-2.5946" y1="2.5946" x2="-1.8446" y2="2.5946" width="0.12" layer="21"/>
<wire x1="-2.2196" y1="2.9696" x2="-2.2196" y2="2.2196" width="0.12" layer="21"/>
<circle x="0" y="0" radius="2.825" width="0.12" layer="51"/>
<text x="0" y="3.6046" size="1.27" layer="25" align="bottom-center">&gt;NAME</text>
<text x="0" y="-3.46" size="1.27" layer="27" align="top-center">&gt;VALUE</text>
</package>
<package name="LEDRD254W60D565H860B_R" urn="urn:adsk.eagle:footprint:38808347/2" library_version="21">
<description>Radial LED (Round), 2.54 mm pitch, 5.65 mm body diameter, 8.60 mm body height
 &lt;p&gt;Radial LED (Round) package  with 2.54 mm pitch (lead spacing), 0.60 mm lead width, 0.50 mm lead thickness, 5.65 mm body diameter and 8.60 mm body height&lt;/p&gt;</description>
<pad name="A" x="-1.27" y="0" drill="0.981" diameter="1.581"/>
<pad name="C" x="1.27" y="0" drill="0.981" diameter="1.581"/>
<wire x1="-2.825" y1="0" x2="2.5425" y2="1.2314" width="0.12" layer="21" curve="-154.1581"/>
<wire x1="2.5425" y1="-1.2314" x2="-2.825" y2="0" width="0.12" layer="21" curve="-154.1581"/>
<wire x1="2.5425" y1="1.2314" x2="2.5425" y2="-1.2314" width="0.12" layer="21"/>
<wire x1="-2.5946" y1="2.5946" x2="-1.8446" y2="2.5946" width="0.12" layer="21"/>
<wire x1="-2.2196" y1="2.9696" x2="-2.2196" y2="2.2196" width="0.12" layer="21"/>
<circle x="0" y="0" radius="2.825" width="0.12" layer="51"/>
<text x="0" y="3.6046" size="1.27" layer="25" align="bottom-center">&gt;NAME</text>
<text x="0" y="-3.46" size="1.27" layer="27" align="top-center">&gt;VALUE</text>
</package>
<package name="LEDRD254W60D565H860B_W" urn="urn:adsk.eagle:footprint:38808344/2" library_version="21">
<description>Radial LED (Round), 2.54 mm pitch, 5.65 mm body diameter, 8.60 mm body height
 &lt;p&gt;Radial LED (Round) package  with 2.54 mm pitch (lead spacing), 0.60 mm lead width, 0.50 mm lead thickness, 5.65 mm body diameter and 8.60 mm body height&lt;/p&gt;</description>
<pad name="A" x="-1.27" y="0" drill="0.981" diameter="1.581"/>
<pad name="C" x="1.27" y="0" drill="0.981" diameter="1.581"/>
<wire x1="-2.825" y1="0" x2="2.5425" y2="1.2314" width="0.12" layer="21" curve="-154.1581"/>
<wire x1="2.5425" y1="-1.2314" x2="-2.825" y2="0" width="0.12" layer="21" curve="-154.1581"/>
<wire x1="2.5425" y1="1.2314" x2="2.5425" y2="-1.2314" width="0.12" layer="21"/>
<wire x1="-2.5946" y1="2.5946" x2="-1.8446" y2="2.5946" width="0.12" layer="21"/>
<wire x1="-2.2196" y1="2.9696" x2="-2.2196" y2="2.2196" width="0.12" layer="21"/>
<circle x="0" y="0" radius="2.825" width="0.12" layer="51"/>
<text x="0" y="3.6046" size="1.27" layer="25" align="bottom-center">&gt;NAME</text>
<text x="0" y="-3.46" size="1.27" layer="27" align="top-center">&gt;VALUE</text>
</package>
<package name="LEDRD254W60D565H860B_Y" urn="urn:adsk.eagle:footprint:38808345/2" library_version="21">
<description>Radial LED (Round), 2.54 mm pitch, 5.65 mm body diameter, 8.60 mm body height
 &lt;p&gt;Radial LED (Round) package  with 2.54 mm pitch (lead spacing), 0.60 mm lead width, 0.50 mm lead thickness, 5.65 mm body diameter and 8.60 mm body height&lt;/p&gt;</description>
<pad name="A" x="-1.27" y="0" drill="0.981" diameter="1.581"/>
<pad name="C" x="1.27" y="0" drill="0.981" diameter="1.581"/>
<wire x1="-2.825" y1="0" x2="2.5425" y2="1.2314" width="0.12" layer="21" curve="-154.1581"/>
<wire x1="2.5425" y1="-1.2314" x2="-2.825" y2="0" width="0.12" layer="21" curve="-154.1581"/>
<wire x1="2.5425" y1="1.2314" x2="2.5425" y2="-1.2314" width="0.12" layer="21"/>
<wire x1="-2.5946" y1="2.5946" x2="-1.8446" y2="2.5946" width="0.12" layer="21"/>
<wire x1="-2.2196" y1="2.9696" x2="-2.2196" y2="2.2196" width="0.12" layer="21"/>
<circle x="0" y="0" radius="2.825" width="0.12" layer="51"/>
<text x="0" y="3.6046" size="1.27" layer="25" align="bottom-center">&gt;NAME</text>
<text x="0" y="-3.46" size="1.27" layer="27" align="top-center">&gt;VALUE</text>
</package>
</packages>
<packages3d>
<package3d name="LEDRD254W60D565H860B_B" urn="urn:adsk.eagle:package:38808350/2" type="model">
<description>Radial LED (Round), 2.54 mm pitch, 5.65 mm body diameter, 8.60 mm body height
 &lt;p&gt;Radial LED (Round) package  with 2.54 mm pitch (lead spacing), 0.60 mm lead width, 0.50 mm lead thickness, 5.65 mm body diameter and 8.60 mm body height&lt;/p&gt;</description>
<packageinstances>
<packageinstance name="LEDRD254W60D565H860B_B"/>
</packageinstances>
</package3d>
<package3d name="LEDRD254W60D565H860B_R" urn="urn:adsk.eagle:package:38808352/2" type="model">
<description>Radial LED (Round), 2.54 mm pitch, 5.65 mm body diameter, 8.60 mm body height
 &lt;p&gt;Radial LED (Round) package  with 2.54 mm pitch (lead spacing), 0.60 mm lead width, 0.50 mm lead thickness, 5.65 mm body diameter and 8.60 mm body height&lt;/p&gt;</description>
<packageinstances>
<packageinstance name="LEDRD254W60D565H860B_R"/>
</packageinstances>
</package3d>
<package3d name="LEDRD254W60D565H860B_W" urn="urn:adsk.eagle:package:38808348/2" type="model">
<description>Radial LED (Round), 2.54 mm pitch, 5.65 mm body diameter, 8.60 mm body height
 &lt;p&gt;Radial LED (Round) package  with 2.54 mm pitch (lead spacing), 0.60 mm lead width, 0.50 mm lead thickness, 5.65 mm body diameter and 8.60 mm body height&lt;/p&gt;</description>
<packageinstances>
<packageinstance name="LEDRD254W60D565H860B_W"/>
</packageinstances>
</package3d>
<package3d name="LEDRD254W60D565H860B_Y" urn="urn:adsk.eagle:package:38808349/2" type="model">
<description>Radial LED (Round), 2.54 mm pitch, 5.65 mm body diameter, 8.60 mm body height
 &lt;p&gt;Radial LED (Round) package  with 2.54 mm pitch (lead spacing), 0.60 mm lead width, 0.50 mm lead thickness, 5.65 mm body diameter and 8.60 mm body height&lt;/p&gt;</description>
<packageinstances>
<packageinstance name="LEDRD254W60D565H860B_Y"/>
</packageinstances>
</package3d>
</packages3d>
<symbols>
<symbol name="LED" urn="urn:adsk.eagle:symbol:16378488/2" library_version="21">
<wire x1="1.27" y1="0" x2="0" y2="-2.54" width="0.254" layer="94"/>
<wire x1="0" y1="-2.54" x2="-1.27" y2="0" width="0.254" layer="94"/>
<wire x1="1.27" y1="-2.54" x2="0" y2="-2.54" width="0.254" layer="94"/>
<wire x1="0" y1="-2.54" x2="-1.27" y2="-2.54" width="0.254" layer="94"/>
<wire x1="1.27" y1="0" x2="-1.27" y2="0" width="0.254" layer="94"/>
<wire x1="-2.032" y1="-0.762" x2="-3.429" y2="-2.159" width="0.1524" layer="94"/>
<wire x1="-1.905" y1="-1.905" x2="-3.302" y2="-3.302" width="0.1524" layer="94"/>
<text x="2.286" y="-0.762" size="1.778" layer="95" rot="R180" align="top-right">&gt;NAME</text>
<text x="1.905" y="-3.302" size="1.778" layer="96" rot="R180" align="top-right">&gt;VALUE</text>
<pin name="C" x="0" y="-5.08" visible="off" length="short" direction="pas" rot="R90"/>
<pin name="A" x="0" y="2.54" visible="off" length="short" direction="pas" rot="R270"/>
<polygon width="0.1524" layer="94" pour="solid">
<vertex x="-3.048" y="-1.27"/>
<vertex x="-3.429" y="-2.159"/>
<vertex x="-2.54" y="-1.778"/>
</polygon>
<polygon width="0.1524" layer="94" pour="solid">
<vertex x="-2.921" y="-2.413"/>
<vertex x="-3.302" y="-3.302"/>
<vertex x="-2.413" y="-2.921"/>
</polygon>
</symbol>
</symbols>
<devicesets>
<deviceset name="LED_RADIAL" urn="urn:adsk.eagle:component:16378513/12" prefix="D" library_version="21">
<description>&lt;B&gt; LED - Generic</description>
<gates>
<gate name="G$1" symbol="LED" x="0" y="0"/>
</gates>
<devices>
<device name="BLUE" package="LEDRD254W60D565H860B_B">
<connects>
<connect gate="G$1" pin="A" pad="A"/>
<connect gate="G$1" pin="C" pad="C"/>
</connects>
<package3dinstances>
<package3dinstance package3d_urn="urn:adsk.eagle:package:38808350/2"/>
</package3dinstances>
<technologies>
<technology name="">
<attribute name="VALUE" value="LED-BLUE" constant="no"/>
</technology>
</technologies>
</device>
<device name="RED" package="LEDRD254W60D565H860B_R">
<connects>
<connect gate="G$1" pin="A" pad="A"/>
<connect gate="G$1" pin="C" pad="C"/>
</connects>
<package3dinstances>
<package3dinstance package3d_urn="urn:adsk.eagle:package:38808352/2"/>
</package3dinstances>
<technologies>
<technology name="">
<attribute name="VALUE" value="LED-RED" constant="no"/>
</technology>
</technologies>
</device>
<device name="WHITE" package="LEDRD254W60D565H860B_W">
<connects>
<connect gate="G$1" pin="A" pad="A"/>
<connect gate="G$1" pin="C" pad="C"/>
</connects>
<package3dinstances>
<package3dinstance package3d_urn="urn:adsk.eagle:package:38808348/2"/>
</package3dinstances>
<technologies>
<technology name="">
<attribute name="VALUE" value="LED-WHITE" constant="no"/>
</technology>
</technologies>
</device>
<device name="YELLOW" package="LEDRD254W60D565H860B_Y">
<connects>
<connect gate="G$1" pin="A" pad="A"/>
<connect gate="G$1" pin="C" pad="C"/>
</connects>
<package3dinstances>
<package3dinstance package3d_urn="urn:adsk.eagle:package:38808349/2"/>
</package3dinstances>
<technologies>
<technology name="">
<attribute name="VALUE" value="LED-YELLOW" constant="no"/>
</technology>
</technologies>
</device>
</devices>
</deviceset>
</devicesets>
</library>
</libraries>
<attributes>
</attributes>
<variantdefs>
</variantdefs>
<classes>
<class number="0" name="default" width="0" drill="0">
</class>
</classes>
<parts>
<part name="U$1" library="LickportLibrary" library_urn="urn:adsk.wipprod:fs.file:vf.VFafNuzRRkGTanVNJLVUVw" deviceset="MOSFET_MODULE" device=""/>
<part name="U$2" library="LickportLibrary" library_urn="urn:adsk.wipprod:fs.file:vf.VFafNuzRRkGTanVNJLVUVw" deviceset="MOSFET_MODULE" device=""/>
<part name="U$3" library="LickportLibrary" library_urn="urn:adsk.wipprod:fs.file:vf.VFafNuzRRkGTanVNJLVUVw" deviceset="JOY_IT_PRO_MICRO" device=""/>
<part name="JP1" library="LickportLibrary" library_urn="urn:adsk.wipprod:fs.file:vf.VFafNuzRRkGTanVNJLVUVw" deviceset="PINHD-1X8" device="/90" package3d_urn="urn:adsk.eagle:package:22413/2" value="PINHD-1X8/90"/>
<part name="JP2" library="LickportLibrary" library_urn="urn:adsk.wipprod:fs.file:vf.VFafNuzRRkGTanVNJLVUVw" deviceset="PINHD-1X2" device="/90" package3d_urn="urn:adsk.eagle:package:22437/2" value="PINHD-1X2/90"/>
<part name="JP3" library="LickportLibrary" library_urn="urn:adsk.wipprod:fs.file:vf.VFafNuzRRkGTanVNJLVUVw" deviceset="PINHD-1X2" device="/90" package3d_urn="urn:adsk.eagle:package:22437/2" value="PINHD-1X2/90"/>
<part name="R1" library="LickportLibrary" library_urn="urn:adsk.wipprod:fs.file:vf.VFafNuzRRkGTanVNJLVUVw" deviceset="RESISTOR" device="AXIAL-11.7MM-PITCH" package3d_urn="urn:adsk.eagle:package:16378560/5" technology="_"/>
<part name="R2" library="LickportLibrary" library_urn="urn:adsk.wipprod:fs.file:vf.VFafNuzRRkGTanVNJLVUVw" deviceset="RESISTOR" device="AXIAL-11.7MM-PITCH" package3d_urn="urn:adsk.eagle:package:16378560/5" technology="_"/>
<part name="JP6" library="Connector" library_urn="urn:adsk.eagle:library:16378166" deviceset="PINHD-1X1" device="" package3d_urn="urn:adsk.eagle:package:22485/2" value="PINHD-1X1"/>
<part name="D1" library="Opto-Electronic" library_urn="urn:adsk.eagle:library:16378487" deviceset="LED_RADIAL" device="BLUE" package3d_urn="urn:adsk.eagle:package:38808350/2" value="LED-BLUE"/>
</parts>
<sheets>
<sheet>
<plain>
</plain>
<instances>
<instance part="U$1" gate="G$1" x="38.1" y="81.28" smashed="yes"/>
<instance part="U$2" gate="G$1" x="86.36" y="81.28" smashed="yes"/>
<instance part="U$3" gate="G$1" x="55.88" y="17.78" smashed="yes" rot="R270"/>
<instance part="JP1" gate="A" x="48.26" y="144.78" smashed="yes" rot="MR270">
<attribute name="NAME" x="34.925" y="151.13" size="1.778" layer="95" rot="MR270"/>
<attribute name="VALUE" x="60.96" y="151.13" size="1.778" layer="96" rot="MR270"/>
</instance>
<instance part="JP2" gate="G$1" x="-15.24" y="121.92" smashed="yes" rot="R180">
<attribute name="NAME" x="-8.89" y="116.205" size="1.778" layer="95" rot="R180"/>
<attribute name="VALUE" x="-8.89" y="127" size="1.778" layer="96" rot="R180"/>
</instance>
<instance part="JP3" gate="G$1" x="109.22" y="121.92" smashed="yes" rot="MR180">
<attribute name="NAME" x="102.87" y="116.205" size="1.778" layer="95" rot="MR180"/>
<attribute name="VALUE" x="102.87" y="127" size="1.778" layer="96" rot="MR180"/>
</instance>
<instance part="R1" gate="G$1" x="27.94" y="-2.54" smashed="yes">
<attribute name="NAME" x="27.94" y="0" size="1.778" layer="95" align="center"/>
<attribute name="VALUE" x="27.94" y="-5.08" size="1.778" layer="96" align="center"/>
</instance>
<instance part="R2" gate="G$1" x="5.08" y="15.24" smashed="yes" rot="R90">
<attribute name="NAME" x="2.54" y="15.24" size="1.778" layer="95" rot="R90" align="center"/>
<attribute name="VALUE" x="7.62" y="15.24" size="1.778" layer="96" rot="R90" align="center"/>
</instance>
<instance part="JP6" gate="G$1" x="73.66" y="-35.56" smashed="yes">
<attribute name="NAME" x="67.31" y="-32.385" size="1.778" layer="95"/>
<attribute name="VALUE" x="67.31" y="-40.64" size="1.778" layer="96"/>
</instance>
<instance part="D1" gate="G$1" x="10.16" y="-38.1" smashed="yes">
<attribute name="NAME" x="12.446" y="-38.862" size="1.778" layer="95" rot="R180" align="top-right"/>
<attribute name="VALUE" x="12.065" y="-41.402" size="1.778" layer="96" rot="R180" align="top-right"/>
</instance>
</instances>
<busses>
</busses>
<nets>
<net name="VIN+PUMP" class="0">
<segment>
<pinref part="JP1" gate="A" pin="1"/>
<wire x1="38.1" y1="147.32" x2="38.1" y2="160.02" width="0.1524" layer="91"/>
<label x="38.1" y="160.02" size="1.778" layer="95" rot="R90"/>
</segment>
<segment>
<pinref part="U$2" gate="G$1" pin="VIN+"/>
<wire x1="86.36" y1="129.54" x2="91.44" y2="129.54" width="0.1524" layer="91"/>
<label x="91.44" y="129.54" size="1.778" layer="95"/>
</segment>
<segment>
<pinref part="U$1" gate="G$1" pin="VIN+"/>
<wire x1="38.1" y1="129.54" x2="43.18" y2="129.54" width="0.1524" layer="91"/>
<label x="40.64" y="132.08" size="1.778" layer="95"/>
</segment>
</net>
<net name="VIN-PUMP" class="0">
<segment>
<pinref part="JP1" gate="A" pin="2"/>
<wire x1="40.64" y1="147.32" x2="40.64" y2="158.75" width="0.1524" layer="91"/>
<label x="40.64" y="158.75" size="1.778" layer="95" rot="R90"/>
</segment>
<segment>
<pinref part="U$2" gate="G$1" pin="VIN-"/>
<wire x1="86.36" y1="124.46" x2="91.44" y2="124.46" width="0.1524" layer="91"/>
<label x="88.9" y="121.92" size="1.778" layer="95"/>
</segment>
<segment>
<pinref part="U$1" gate="G$1" pin="VIN-"/>
<wire x1="38.1" y1="124.46" x2="43.18" y2="124.46" width="0.1524" layer="91"/>
<label x="35.56" y="121.92" size="1.778" layer="95"/>
</segment>
</net>
<net name="VIN+ARDUINO" class="0">
<segment>
<pinref part="JP1" gate="A" pin="3"/>
<wire x1="43.18" y1="147.32" x2="43.18" y2="160.02" width="0.1524" layer="91"/>
<label x="43.18" y="160.02" size="1.778" layer="95" rot="R90"/>
</segment>
<segment>
<pinref part="U$3" gate="G$1" pin="RAW"/>
<wire x1="78.74" y1="7.62" x2="78.74" y2="5.08" width="0.1524" layer="91"/>
<wire x1="78.74" y1="5.08" x2="83.82" y2="5.08" width="0.1524" layer="91"/>
<label x="83.82" y="5.08" size="1.778" layer="95"/>
</segment>
</net>
<net name="VIN-ARDUINO" class="0">
<segment>
<pinref part="JP1" gate="A" pin="4"/>
<wire x1="45.72" y1="147.32" x2="45.72" y2="160.02" width="0.1524" layer="91"/>
<label x="45.72" y="160.02" size="1.778" layer="95" rot="R90"/>
</segment>
<segment>
<pinref part="U$3" gate="G$1" pin="GND2"/>
<wire x1="73.66" y1="7.62" x2="73.66" y2="2.54" width="0.1524" layer="91"/>
<wire x1="73.66" y1="2.54" x2="83.82" y2="2.54" width="0.1524" layer="91"/>
<label x="83.82" y="2.54" size="1.778" layer="95"/>
</segment>
</net>
<net name="OUTPUT1ARDUINO" class="0">
<segment>
<pinref part="JP1" gate="A" pin="5"/>
<wire x1="48.26" y1="147.32" x2="48.26" y2="160.02" width="0.1524" layer="91"/>
<label x="48.26" y="160.02" size="1.778" layer="95" rot="R90"/>
</segment>
<segment>
<pinref part="U$3" gate="G$1" pin="GPIO8"/>
<wire x1="27.94" y1="40.64" x2="27.94" y2="48.26" width="0.1524" layer="91"/>
<label x="22.86" y="48.26" size="1.778" layer="95"/>
</segment>
</net>
<net name="PWMINPUTPUMPRIGHT" class="0">
<segment>
<pinref part="JP1" gate="A" pin="6"/>
<wire x1="50.8" y1="147.32" x2="50.8" y2="160.02" width="0.1524" layer="91"/>
<label x="50.8" y="160.02" size="1.778" layer="95" rot="R90"/>
</segment>
<segment>
<pinref part="U$2" gate="G$1" pin="PWM1"/>
<wire x1="63.5" y1="88.9" x2="58.42" y2="88.9" width="0.1524" layer="91"/>
<label x="58.42" y="83.82" size="1.778" layer="95" rot="R90"/>
</segment>
</net>
<net name="PWMINPUTPUMPLEFT" class="0">
<segment>
<pinref part="JP1" gate="A" pin="7"/>
<wire x1="53.34" y1="147.32" x2="53.34" y2="160.02" width="0.1524" layer="91"/>
<label x="53.34" y="160.02" size="1.778" layer="95" rot="R90"/>
</segment>
<segment>
<pinref part="U$1" gate="G$1" pin="PWM1"/>
<wire x1="15.24" y1="88.9" x2="7.62" y2="88.9" width="0.1524" layer="91"/>
<label x="7.62" y="81.28" size="1.778" layer="95" rot="R90"/>
</segment>
</net>
<net name="LEDINPUT" class="0">
<segment>
<pinref part="JP1" gate="A" pin="8"/>
<wire x1="55.88" y1="147.32" x2="55.88" y2="160.02" width="0.1524" layer="91"/>
<label x="55.88" y="160.02" size="1.778" layer="95" rot="R90"/>
</segment>
<segment>
<pinref part="R2" gate="G$1" pin="2"/>
<wire x1="5.08" y1="20.32" x2="5.08" y2="27.94" width="0.1524" layer="91"/>
<label x="2.54" y="30.48" size="1.778" layer="95"/>
</segment>
</net>
<net name="N$9" class="0">
<segment>
<pinref part="U$1" gate="G$1" pin="VOUT+"/>
<wire x1="7.62" y1="129.54" x2="7.62" y2="127" width="0.1524" layer="91"/>
<wire x1="7.62" y1="127" x2="-3.81" y2="127" width="0.1524" layer="91"/>
<wire x1="0" y1="119.38" x2="-12.7" y2="119.38" width="0.1524" layer="91"/>
<wire x1="0" y1="119.38" x2="-3.81" y2="127" width="0.1524" layer="91"/>
<pinref part="JP2" gate="G$1" pin="1"/>
</segment>
</net>
<net name="N$10" class="0">
<segment>
<pinref part="U$1" gate="G$1" pin="VOUT-"/>
<wire x1="7.62" y1="124.46" x2="7.62" y2="114.3" width="0.1524" layer="91"/>
<wire x1="7.62" y1="114.3" x2="-17.78" y2="114.3" width="0.1524" layer="91"/>
<wire x1="-17.78" y1="114.3" x2="-12.7" y2="121.92" width="0.1524" layer="91"/>
<pinref part="JP2" gate="G$1" pin="2"/>
</segment>
</net>
<net name="N$17" class="0">
<segment>
<pinref part="U$2" gate="G$1" pin="VOUT+"/>
<wire x1="55.88" y1="129.54" x2="48.26" y2="129.54" width="0.1524" layer="91"/>
<wire x1="48.26" y1="129.54" x2="48.26" y2="111.76" width="0.1524" layer="91"/>
<wire x1="48.26" y1="111.76" x2="129.54" y2="111.76" width="0.1524" layer="91"/>
<wire x1="129.54" y1="111.76" x2="129.54" y2="121.92" width="0.1524" layer="91"/>
<wire x1="129.54" y1="121.92" x2="106.68" y2="121.92" width="0.1524" layer="91"/>
<pinref part="JP3" gate="G$1" pin="2"/>
</segment>
</net>
<net name="N$18" class="0">
<segment>
<pinref part="U$2" gate="G$1" pin="VOUT-"/>
<wire x1="55.88" y1="124.46" x2="50.8" y2="124.46" width="0.1524" layer="91"/>
<wire x1="50.8" y1="124.46" x2="50.8" y2="114.3" width="0.1524" layer="91"/>
<wire x1="50.8" y1="114.3" x2="127" y2="114.3" width="0.1524" layer="91"/>
<wire x1="127" y1="114.3" x2="127" y2="119.38" width="0.1524" layer="91"/>
<wire x1="127" y1="119.38" x2="106.68" y2="119.38" width="0.1524" layer="91"/>
<pinref part="JP3" gate="G$1" pin="1"/>
</segment>
</net>
<net name="N$24" class="0">
<segment>
<pinref part="R2" gate="G$1" pin="1"/>
<wire x1="5.08" y1="10.16" x2="5.08" y2="-27.94" width="0.1524" layer="91"/>
<wire x1="5.08" y1="-27.94" x2="2.54" y2="-27.94" width="0.1524" layer="91"/>
<wire x1="2.54" y1="-27.94" x2="2.54" y2="-35.56" width="0.1524" layer="91"/>
<wire x1="2.54" y1="-35.56" x2="10.16" y2="-35.56" width="0.1524" layer="91"/>
<pinref part="D1" gate="G$1" pin="A"/>
</segment>
</net>
<net name="N$26" class="0">
<segment>
<pinref part="U$3" gate="G$1" pin="GPIO10"/>
<wire x1="22.86" y1="7.62" x2="22.86" y2="-2.54" width="0.1524" layer="91"/>
<pinref part="R1" gate="G$1" pin="1"/>
</segment>
</net>
<net name="N$27" class="0">
<segment>
<pinref part="R1" gate="G$1" pin="2"/>
<wire x1="33.02" y1="-2.54" x2="38.1" y2="-2.54" width="0.1524" layer="91"/>
<wire x1="38.1" y1="-2.54" x2="38.1" y2="7.62" width="0.1524" layer="91"/>
<pinref part="U$3" gate="G$1" pin="GPIO15"/>
<wire x1="33.02" y1="-2.54" x2="71.12" y2="-2.54" width="0.1524" layer="91"/>
<wire x1="71.12" y1="-2.54" x2="71.12" y2="-35.56" width="0.1524" layer="91"/>
<junction x="33.02" y="-2.54"/>
<pinref part="JP6" gate="G$1" pin="1"/>
</segment>
</net>
<net name="GNDARDUINO" class="0">
<segment>
<pinref part="U$3" gate="G$1" pin="GND"/>
<wire x1="68.58" y1="40.64" x2="68.58" y2="48.26" width="0.1524" layer="91"/>
<label x="68.58" y="48.26" size="1.778" layer="95"/>
</segment>
<segment>
<pinref part="U$2" gate="G$1" pin="GND1"/>
<wire x1="73.66" y1="88.9" x2="73.66" y2="86.36" width="0.1524" layer="91"/>
<wire x1="73.66" y1="86.36" x2="88.9" y2="86.36" width="0.1524" layer="91"/>
<label x="91.44" y="83.82" size="1.778" layer="95" rot="R90"/>
</segment>
<segment>
<pinref part="U$1" gate="G$1" pin="GND1"/>
<wire x1="25.4" y1="88.9" x2="25.4" y2="86.36" width="0.1524" layer="91"/>
<wire x1="25.4" y1="86.36" x2="38.1" y2="86.36" width="0.1524" layer="91"/>
<label x="40.64" y="81.28" size="1.778" layer="95" rot="R90"/>
</segment>
<segment>
<wire x1="17.78" y1="-27.94" x2="17.78" y2="-17.78" width="0.1524" layer="91"/>
<label x="27.94" y="-15.24" size="1.778" layer="95" rot="R180"/>
<pinref part="D1" gate="G$1" pin="C"/>
<wire x1="10.16" y1="-43.18" x2="10.16" y2="-45.72" width="0.1524" layer="91"/>
<wire x1="10.16" y1="-45.72" x2="30.48" y2="-45.72" width="0.1524" layer="91"/>
<wire x1="30.48" y1="-45.72" x2="30.48" y2="-27.94" width="0.1524" layer="91"/>
<wire x1="30.48" y1="-27.94" x2="17.78" y2="-27.94" width="0.1524" layer="91"/>
</segment>
</net>
</nets>
</sheet>
</sheets>
</schematic>
</drawing>
<compatibility>
<note version="8.2" severity="warning">
Since Version 8.2, EAGLE supports online libraries. The ids
of those online libraries will not be understood (or retained)
with this version.
</note>
<note version="8.3" severity="warning">
Since Version 8.3, EAGLE supports URNs for individual library
assets (packages, symbols, and devices). The URNs of those assets
will not be understood (or retained) with this version.
</note>
<note version="8.3" severity="warning">
Since Version 8.3, EAGLE supports the association of 3D packages
with devices in libraries, schematics, and board files. Those 3D
packages will not be understood (or retained) with this version.
</note>
<note version="8.4" severity="warning">
Since Version 8.4, EAGLE supports properties for SPICE simulation. 
Probes in schematics and SPICE mapping objects found in parts and library devices
will not be understood with this version. Update EAGLE to the latest version
for full support of SPICE simulation. 
</note>
</compatibility>
</eagle>
