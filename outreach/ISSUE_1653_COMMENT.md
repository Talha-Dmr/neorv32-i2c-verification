I built a spec-based cocotb testbench for the TWD (Python I²C controller with the UM10204
Table 11 timing sets and a monitor for SDA changes while SCL is high) and can confirm both
points, plus one detail that makes them worse:

* **NACK on read → bus hang is the common case, not a corner case.** The TWD re-drives the
  MSB of the NACKed byte right away; whenever that MSB is 0, SDA stays low and STOP is
  impossible. Three of my ordinary read tests hang on `main` (`90a4c867`).
* **ACK level in the write slot is not latched.** Popping the RX FIFO from the CPU while SCL
  is high in the 9th clock makes SDA fall = false START. The TWD then re-enters `S_ADDR`
  with `engine.sda` still 0 (it is not reset in `S_INIT`) and holds the bus low until the
  module is disabled.
* Not a bug, but undocumented: with `FSEL=1` (640 ns sampling at 100 MHz) Fast-mode 400 kHz
  does not work (tHIGH 0.6 µs pulses get missed); Standard-mode is fine. `FSEL=0` works to
  ~3 MHz. Data-valid time and START-hold detection are within spec for both settings.

I have a fix (latched ACK decision, `S_WAIT` state after a host NACK, SDA release in
`S_INIT`, datasheet update) that takes the suite from 10/15 to 14/15 with zero hangs; PR
incoming. Testbench: https://github.com/Talha-Dmr/neorv32-i2c-verification
