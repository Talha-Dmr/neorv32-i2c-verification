## [twd] I²C-spec compliant ACK/NACK handling (fixes #1653)

Ref #1653, #1652 (the second part of #1653's comment about the ACK level is covered here too).

### What was wrong

I wrote a spec-based cocotb testbench for the TWD (Python I²C controller with the
UM10204 Table 11 timing sets, plus a monitor that flags any SDA change by the TWD
while SCL is high). Against `main` (`90a4c867`) it shows:

1. **Read, host NACK → bus hang (common case).** After the host NACKs a byte (the normal
   end of every read, UM10204 3.1.6) the TWD keeps the byte in the TX FIFO and drives its
   MSB again for a "next" byte. If that MSB is 0, SDA stays low and the host cannot
   generate STOP. With random payloads this hangs half of all read transactions
   (tests `test_04`, `test_06`, `test_08`).
2. **Write ACK slot: ACK/NACK level not stable.** `engine.sda <= not rx_fifo.free` is
   evaluated for the whole 9th clock. If the CPU pops the RX FIFO while SCL is high, SDA
   falls during SCL high = a START condition (3.1.3). The TWD then detects its own false
   START, goes to `S_INIT` → `S_ADDR` with `engine.sda` still 0 and holds the bus low until
   the module is disabled (`test_09`).

### Changes

* Read: sample the host's (N)ACK at the rising edge of the 9th clock and pop the TX FIFO in
  both cases (the byte was delivered). On NACK enter a new `S_WAIT` state: SDA released,
  wait for STOP or (repeated) START.
* Write: decide ACK/NACK once at the falling edge that ends the 8th bit (`engine.nack`), keep
  it for the whole 9th clock, and never store a NACKed byte.
* `S_INIT` releases SDA so a (repeated) START never inherits a driven-low line.
* Datasheet (`soc_twd.adoc`) updated. Behavioural change worth noting in the changelog: a
  NACKed read byte is now consumed instead of retransmitted.

### Evidence

| | `main` | this PR |
|---|---|---|
| cocotb tests passing | 10 / 15 | 14 / 15 |
| bus hangs | 3 | 0 |
| SDA changes while SCL high | 1 | 0 |

The remaining failing test documents a limitation, not a bug: with `FSEL=1` (sampling every
64 clocks = 640 ns at 100 MHz) Fast-mode SCL pulses (tHIGH ≥ 0.6 µs) are missed, so only
Standard-mode works; `FSEL=0` works up to ~3 MHz. Maybe worth a sentence in the datasheet.
Data-valid time (110 ns / 660 ns) and START-hold detection (100 ns / 200 ns) are within spec
for both `FSEL` settings.

Testbench (GHDL + cocotb, ~3 s): https://github.com/Talha-Dmr/neorv32-i2c-verification

I could not run `processor_check` locally (no RISC-V toolchain here); CI should cover it.
Happy to adapt anything (state naming, keeping the old retransmit behaviour behind a
generic, etc.).
