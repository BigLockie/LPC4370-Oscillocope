# 34 MSa/s Dual Channel Oscillscope
A custom design dual channel USB-C oscilloscope built around the NXP LPC4370 (100 pin TFBGA) intended to output to a Python-based graphical display on a connected computer driven by PyQtGraph. The oscilloscope was designed for the Oregon State University Junior Design II course (ECE 342), the project took place over a 10 week term. 
<p float="left">
<img width="1000" alt="IMG_7518" src="https://github.com/user-attachments/assets/b6696ebf-668d-482b-b29b-a1cb0289794a" />
<img width="1000"" alt="Screenshot 2026-06-05 142354" src="https://github.com/user-attachments/assets/b3b1693e-237f-446d-a00e-379146662170" />
</p>

# Contributors:
Luke Neilson,
Griffin Ziemer, and
Cooper Holm

## Key Features:
* Dual Channel Simulatnuous Sample Rate: 34MSa/s.
* Vertical Resolution: 12-bits.
* Bandwidth: 1MHz.
* Input Voltage Range: +/- 10V with a 10x attenuation probe, +/- 1V with a 1x attenuation probe.
* AC/DC Coupling: Hardware AC/DC coupling using a photo relay to bypass a 100nF capacitor.
* Power: Powered entirely via a standard USB 2.0 connection (nominally drawing 180mA), with a visible LED power indicator.
* User Interface: High-speed display which responds to user input in an average of 0.350 milliseconds

## Hardware Architecture
* Power Block: 5V USB input regulated down to 3.3V digital rail using a TpS79533 LDO. An LM27762 charge pump provides +/- 1.65V rails for some of the Analog Front End op-amps. A 500mV Vref offset is generated off a ferrite bead from the 3.3V line.
* Analog Front End: Incoming signals pass through the coupling and overvoltage protection stages into a variable gain attenuation controlled by TMUX1134 precision analog multiplexers. OPA356 op-amps shift and scale the signals to fit the 100mV to 900mV window required by the LPC4370's ADC.
* Microcontroller: The system uses an LPC4370's Direct Memory Access controller to write 12-bit ADC results into an SRAM buffer without CPU overhead, ensuring maximum throughput.

## Software & Protocol Stack
* Firmware: The backend firmware configures the Analog Front End Gain and AC/DC photorelays at boot. It serializes DMA transfers into packets containing 0xAA start byte, the daya type, sample count, payload and an XOR checksum, transmitting them over a USB CDC link.
* Host Bridge & GUI: A worker thread deserializes incoming packets, verifies the checksum, and translates raw ADC counts into real voltage based on the current user scale. The UI is built using PyQt5 and pyqtgraph. To maximize accessibilty, Channel 1 is rendered in blue and Channel 2 in pink, complying with NCEAS colorblind-safe guidelines.

## Team Work Breakdown
* Luke Neilson: MCU block design, PCB layout (Versions 1 and 2), initial Display blcok design, and Teensy 4.0 backup implementation.
* Griffin Ziemer: User Input GUI, Backend firmware, host bridge protocol serialization/deserialization, and integration of the display.
* Cooper Holm: Power supply block design, Analog Frent End architecture.

## Future Improvements
 ### To Be Implemented with Current Hardware
  Due to the 10 week timeline, the project ran out of time to implement all of the features designed in hardware in the software.
  * Real time control over the attenuation paths, allowing selection between gain path: 0.256x, 0.833x, 2.566x, and 10x
  * Real time control over AC/DC coupling, allowing selection between gain path: 0.256x, 0.833x, 2.566x, and 10x
  * Configure PLLs to create an exact 80MHz clock for the ADC to reach the maximum hardware sampling rate
  * Flash firmware to the non-volitile memory
 ### Version 3 of the PCB Improvements
  * Minimize digital analog cross talk: The bandwidth was limited because a more aggressive RC filter at the ADC input was required because of the noise from the digital components (LPC 4370 and a 12MHz external clock).
   * Shorten the signal path from the analog front end to the ADC.
   * Use differential pair drivers to create differential pairs to go from the analog front end to the ADC
