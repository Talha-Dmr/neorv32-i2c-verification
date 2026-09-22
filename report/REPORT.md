# Verification Report: NEORV32 TWD (I²C target) against NXP UM10204

| | |
|---|---|
| **DUT** | `neorv32_twd.vhd`, NEORV32 v1.13.6.1 (commit `90a4c867`, 2026-09-20), 100 MHz clock, RX/TX FIFO 4 |
| **Reference** | NXP UM10204 Rev 7 (I²C-bus specification), NEORV32 datasheet section "TWD" |
| **Method** | cocotb 2.1 on GHDL 7; Python I²C controller with UM10204 Table 11 timing sets, protocol monitor (SDA changes vs. SCL), register-bus driver; 15 tests |
| **Trigger** | Open issues #1652 / #1653 (2026-09-19); maintainer asked for fix suggestions |
| **Author / date** | Talha Demir, 22 September 2026 |

## 1. Summary

The TWD is not I²C-compliant on the 9th clock (ACK/NACK). Two defects make the bus hang until software disables the module, one of them on ordinary read transactions. A third finding is an undocumented speed limit of the slower input filter. Timing that the specification bounds (data valid time, START hold detection) is within limits. A fix is provided on branch `fix-twd-i2c-nack` (+36 lines net in the engine, datasheet updated); with it 14 of 15 tests pass, the remaining one being the documentation item.

| Test suite | Original RTL | Fixed RTL |
|---|---|---|
| Passing | 10 / 15 | 14 / 15 |
| Bus hangs observed | 3 tests | 0 |
| SDA changes while SCL high (UM10204 3.1.3) | 1 | 0 |

## 2. Findings

| ID | Severity | Finding | Evidence |
|---|---|---|---|
| T1 | **High** (bus hang, common case) | After the host NACKs a byte in a read (the normal end of every read, UM10204 3.1.6), the TWD keeps the byte in the TX FIFO and immediately drives its MSB for the next byte. If that MSB is 0, SDA is held low and the host cannot generate STOP. With random data this hangs 50 % of all read transactions. Confirms and widens issue #1653. | `test_04`, `test_06`, `test_08` |
| T2 | **High** (protocol violation, then hang) | In the write ACK slot the ACK/NACK level is recomputed combinationally from the RX FIFO state. If the CPU pops the FIFO while SCL is high, SDA falls during SCL high, which is a START condition (3.1.3). The TWD detects its own false START, re-enters the address phase without releasing SDA (`engine.sda` is not reset in `S_INIT`) and holds the bus low until disabled. Confirms the comment in #1653 and adds the root cause of the hang. | `test_09` |
| T3 | Low (undocumented limitation) | With `FSEL=1` the bus is sampled every 64 clocks (640 ns at 100 MHz). Fast-mode allows tHIGH = 0.6 µs, so SCL pulses can be missed: at 400 kHz the address is ACKed but every data byte is NACKed. `FSEL=1` therefore supports Standard-mode only; `FSEL=0` works up to 3 MHz. The datasheet states no SCL limit for either setting. | `test_12` |
| T4 | Info (checked, compliant) | Data valid time tVD;DAT/tVD;ACK: 110 ns (`FSEL=0`), 660 ns (`FSEL=1`); limits 0.9 µs (Fast) / 3.45 µs (Std). START hold detected down to 100 ns / 200 ns. Address mismatch and general call ignored. Aborted bytes (STOP or Sr mid-byte) resynchronise. IRQ sources and W1C flags behave as documented. | `test_13`, `test_14`, `test_03`, `test_10`, `test_15` |
| T5 | Info (documentation, TWI section) | The TWI register map describes bit 29 `TWI_CTRL_TX_FULL` as "set if the TWI bus is claimed by any controller" (it is TX FIFO full), and refers to generics `IO_TWI_RX_FIFO`/`IO_TWI_TX_FIFO` and flags `TWD_CTRL_RX_*` that do not exist for the TWI. | datasheet `soc_twi.adoc` |

## 3. Fix (branch `fix-twd-i2c-nack`)

* Read: the host's ACK/NACK is sampled at the rising edge of the 9th clock; the byte is popped in both cases (it was delivered). On NACK the engine enters a new `S_WAIT` state that releases SDA and waits for STOP or (repeated) START, as 3.1.6 requires.
* Write: ACK/NACK is decided once, at the falling edge that ends the 8th bit, and kept for the whole 9th clock; a NACKed byte is never written to the RX FIFO.
* `S_INIT` releases SDA so a (repeated) START never inherits a driven-low line.
* Datasheet text for read/write operation updated. Behavioural change to document: a NACKed read byte is consumed (previously retransmitted).

Synthesis check with Yosys (GHDL front-end): see `report/synth.txt`.

## 4. Reproduce

```
source env.sh
make -C tb                                # 15 tests, ~3 s
(cd neorv32 && git checkout main)        # original RTL: 5 failures
(cd neorv32 && git checkout fix-twd-i2c-nack)   # fixed RTL: 1 failure (T3)
```
