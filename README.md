# NEORV32 I²C (TWD / TWI) — independent verification with cocotb + GHDL

Spec-based verification of the NEORV32 SoC's I²C target (`neorv32_twd`) against
NXP UM10204 Rev 7, triggered by issues stnolting/neorv32#1652 and #1653.

* `tb/twd_bfm.py` — bit-banged I²C controller with UM10204 Table 11 timing sets
  (Standard / Fast / Fast-mode Plus), a protocol monitor that flags any DUT SDA
  change while SCL is high and measures data-valid time, plus a NEORV32
  register-bus driver.
* `tb/test_twd.py` — 15 tests: register map, write/read transactions, address
  mismatch and general call, FIFO full/empty behaviour, NACK handling (#1653),
  ACK-slot race, START/STOP mid-byte, disable mid-transfer, maximum SCL
  frequency per `FSEL`, START hold time, data valid time, interrupts.
* `tb/twd_tb_top.vhd` — wrapper: record ports unpacked, SoC clock enables,
  open-drain bus resolution.
* `report/REPORT.md` — findings T1..T5 and the proposed fix.

The fix itself lives on branch `fix-twd-i2c-nack` of the NEORV32 clone
(`rtl/core/neorv32_twd.vhd`, `docs/datasheet/soc_twd.adoc`).

Results: original RTL 10/15 tests pass (2 bus-hang defects, 1 protocol
violation, 1 undocumented limit); fixed RTL 14/15.

```
source env.sh
make -C tb
```
Tooling: GHDL 7.0 (OSS CAD Suite), cocotb 2.1.0.
