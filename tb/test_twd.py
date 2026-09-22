"""
cocotb tests for the NEORV32 TWD (I2C target) against NXP UM10204 Rev 7.
DUT clock 100 MHz. Default: 400 kHz fast-mode controller, FSEL=0 (clk/8 sampling).
"""
import os
import random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles, Timer
from twd_bfm import I2CController, RegBus, I2C_STANDARD, I2C_FAST, I2C_FASTPLUS, now, US

CLK_NS = 10.0
RX_FIFO = int(os.environ.get("RX_FIFO", "4"))
TX_FIFO = int(os.environ.get("TX_FIFO", "4"))
CTRL, DATA = 0x0, 0x4
# CTRL bits
EN, CLR_RX, CLR_TX, FSEL = 0, 1, 2, 3
ADDR0 = 4
IRQ_RX_AVAIL, IRQ_RX_FULL, IRQ_TX_EMPTY, IRQ_TX_NFULL, IRQ_COM_BEG, IRQ_COM_END = 11, 12, 13, 14, 15, 16
RX_AVAIL, RX_FULL, TX_EMPTY, TX_FULL, COM_BEG, COM_END, COM = 25, 26, 27, 28, 29, 30, 31
DEV = 0x2A


def bit(v, n):
    return (v >> n) & 1


async def setup(dut, addr=DEV, fsel=0, irq_mask=0, timing=I2C_FAST):
    cocotb.start_soon(Clock(dut.clk_i, CLK_NS, unit="ns").start())
    dut.rstn_i.value = 0
    dut.ctl_sda_o.value = 1
    dut.ctl_scl_o.value = 1
    bus = RegBus(dut)
    await ClockCycles(dut.clk_i, 5)
    dut.rstn_i.value = 1
    await ClockCycles(dut.clk_i, 3)
    i2c = I2CController(dut, timing)
    ctrl = (1 << EN) | (fsel << FSEL) | ((addr & 0x7f) << ADDR0) | irq_mask
    await bus.write(CTRL, ctrl)
    await ClockCycles(dut.clk_i, 200)         # let the sampler settle
    return bus, i2c


async def status(bus):
    return await bus.read(CTRL)


async def drain_rx(bus, timeout_cycles=1000):
    out = []
    while bit(await status(bus), RX_AVAIL):
        out.append(await bus.read(DATA) & 0xff)
        if len(out) > 100:
            break
    return out


# ----------------------------------------------------------------------------
@cocotb.test()
async def test_01_registers(dut):
    """Reset values, read-back of CTRL fields, FIFO depth fields, enable/disable clears FIFOs."""
    bus, i2c = await setup(dut, irq_mask=(1 << IRQ_RX_AVAIL))
    v = await status(bus)
    assert bit(v, EN) == 1 and (v >> ADDR0) & 0x7f == DEV and bit(v, IRQ_RX_AVAIL) == 1
    assert (v >> 17) & 0xf == RX_FIFO.bit_length() - 1, "RX FIFO depth field"
    assert (v >> 21) & 0xf == TX_FIFO.bit_length() - 1, "TX FIFO depth field"
    assert bit(v, TX_EMPTY) == 1 and bit(v, RX_AVAIL) == 0 and bit(v, COM) == 0
    await bus.write(DATA, 0x11)
    await ClockCycles(dut.clk_i, 3)
    assert bit(await status(bus), TX_EMPTY) == 0
    await bus.write(CTRL, 0)                   # disable -> reset FIFOs
    await bus.write(CTRL, (1 << EN) | (DEV << ADDR0))
    await ClockCycles(dut.clk_i, 3)
    assert bit(await status(bus), TX_EMPTY) == 1, "disable must clear TX FIFO"
    assert int(dut.dut_sda_o.value) == 1, "SDA must be released when idle"


@cocotb.test()
async def test_02_write_transaction(dut):
    """Host writes 3 bytes: address ACK, data ACKs, bytes land in RX FIFO in order,
    COM/COM_BEG/COM_END flags behave as documented, W1C clears."""
    bus, i2c = await setup(dut)
    await i2c.start()
    assert await i2c.address(DEV, read=False), "address not ACKed"
    await ClockCycles(dut.clk_i, 20)
    s = await status(bus)
    assert bit(s, COM) == 1 and bit(s, COM_BEG) == 1 and bit(s, COM_END) == 0, hex(s)
    for b in (0xA5, 0x3C, 0x00):
        assert await i2c.write_byte(b), f"data byte {b:02x} not ACKed"
    assert await i2c.stop()
    await ClockCycles(dut.clk_i, 200)
    s = await status(bus)
    assert bit(s, COM) == 0 and bit(s, COM_END) == 1, hex(s)
    assert await drain_rx(bus) == [0xA5, 0x3C, 0x00]
    await bus.write(CTRL, (1 << EN) | (DEV << ADDR0) | (1 << COM_BEG) | (1 << COM_END))
    s = await status(bus)
    assert bit(s, COM_BEG) == 0 and bit(s, COM_END) == 0, "W1C did not clear COM flags"
    assert not i2c.violations, i2c.violations


@cocotb.test()
async def test_03_address_mismatch_and_general_call(dut):
    """Other address and general call (0x00) must not be ACKed and leave no trace."""
    bus, i2c = await setup(dut)
    for a in (DEV ^ 0x01, 0x00):
        await i2c.start()
        assert not await i2c.address(a, read=False), f"address {a:#x} wrongly ACKed"
        await i2c.write_byte(0x55)
        assert await i2c.stop()
    await ClockCycles(dut.clk_i, 100)
    s = await status(bus)
    assert bit(s, RX_AVAIL) == 0 and bit(s, COM_BEG) == 0 and bit(s, COM) == 0, hex(s)
    assert not i2c.violations, i2c.violations


@cocotb.test()
async def test_04_read_transaction(dut):
    """Host reads 3 bytes from a preloaded TX FIFO, ACK ACK NACK, STOP.
    Exactly 3 bytes must be consumed."""
    bus, i2c = await setup(dut)
    for b in (0x81, 0x42, 0x24, 0x18):
        await bus.write(DATA, b)
    await i2c.start()
    assert await i2c.address(DEV, read=True)
    got = [await i2c.read_byte(ack=True), await i2c.read_byte(ack=True), await i2c.read_byte(ack=False)]
    assert await i2c.stop(), "bus hang after NACK+STOP"
    await ClockCycles(dut.clk_i, 100)
    assert got == [0x81, 0x42, 0x24], [hex(x) for x in got]
    s = await status(bus)
    assert bit(s, TX_EMPTY) == 0, "one byte should remain in TX FIFO"
    assert not i2c.violations, i2c.violations


@cocotb.test()
async def test_05_read_empty_tx_fifo(dut):
    """Documented: all-one bytes when TX FIFO is empty."""
    bus, i2c = await setup(dut)
    await i2c.start()
    assert await i2c.address(DEV, read=True)
    got = [await i2c.read_byte(ack=True), await i2c.read_byte(ack=False)]
    assert await i2c.stop()
    assert got == [0xFF, 0xFF], [hex(x) for x in got]
    assert not i2c.violations, i2c.violations


@cocotb.test()
async def test_06_issue1653_nack_then_stop(dut):
    """UM10204 3.1.6: after the controller NACKs a byte it generates STOP (or Sr).
    A target-transmitter must release SDA. Preload a byte whose MSB is 0 behind
    the byte being read, NACK, then try to STOP."""
    bus, i2c = await setup(dut)
    await bus.write(DATA, 0x55)
    await bus.write(DATA, 0x00)                 # next byte starts with a 0 bit
    await i2c.start()
    assert await i2c.address(DEV, read=True)
    b = await i2c.read_byte(ack=False)          # NACK: end of transfer
    assert b == 0x55
    ok = await i2c.stop()
    await ClockCycles(dut.clk_i, 100)
    s = await status(bus)
    dut._log.info(f"after NACK+STOP: sda_line={int(dut.sda_line.value)} bus_hangs={i2c.bus_hangs} "
                  f"COM={bit(s, COM)} TX_EMPTY={bit(s, TX_EMPTY)} violations={i2c.violations}")
    i2c.release()
    assert ok, ("FINDING (issue #1653): TWD keeps driving SDA low after the host NACKed; the host "
                "cannot generate STOP (bus hang)")


@cocotb.test()
async def test_07_write_rx_fifo_full_nack(dut):
    """RX FIFO full: the data byte must be NACKed and must not be stored."""
    bus, i2c = await setup(dut)
    await i2c.start()
    assert await i2c.address(DEV, read=False)
    acks = [await i2c.write_byte(0x10 + i) for i in range(RX_FIFO + 2)]
    assert await i2c.stop()
    await ClockCycles(dut.clk_i, 100)
    dut._log.info(f"ACKs for {RX_FIFO + 2} bytes into a {RX_FIFO}-deep FIFO: {acks}")
    assert acks[:RX_FIFO] == [True] * RX_FIFO and acks[RX_FIFO:] == [False, False], acks
    assert await drain_rx(bus) == [0x10 + i for i in range(RX_FIFO)]
    assert not i2c.violations, i2c.violations


@cocotb.test()
async def test_08_repeated_start_write_then_read(dut):
    """Write 1 byte, repeated START, read 2 bytes, NACK, STOP."""
    bus, i2c = await setup(dut)
    await bus.write(DATA, 0xC3)
    await bus.write(DATA, 0x3C)
    await i2c.start()
    assert await i2c.address(DEV, read=False)
    assert await i2c.write_byte(0x77)
    await i2c.repeated_start()
    assert await i2c.address(DEV, read=True)
    got = [await i2c.read_byte(ack=True), await i2c.read_byte(ack=False)]
    assert await i2c.stop()
    await ClockCycles(dut.clk_i, 100)
    assert got == [0xC3, 0x3C], [hex(x) for x in got]
    assert await drain_rx(bus) == [0x77]
    s = await status(bus)
    assert bit(s, COM_BEG) == 1 and bit(s, COM_END) == 1
    assert not i2c.violations, i2c.violations


@cocotb.test()
async def test_09_ack_slot_race_fifo_freed_during_ack(dut):
    """Issue #1653 comment: the ACK/NACK level is recomputed continuously during the
    9th clock. If the CPU frees the RX FIFO while SCL is high in the ACK slot, SDA
    changes during SCL high = a START/STOP condition on the bus. Also check whether
    the NACKed byte is still pushed into the FIFO (duplicate on host retry)."""
    bus, i2c = await setup(dut)
    await i2c.start()
    assert await i2c.address(DEV, read=False)
    for i in range(RX_FIFO):
        assert await i2c.write_byte(0x20 + i)
    # next byte: FIFO is full -> DUT decides NACK. We free one entry in the
    # middle of the ACK bit's HIGH phase.
    for i in range(7, -1, -1):
        await i2c.write_bit((0xEE >> i) & 1)
    await i2c._wait(i2c.t_hd_dat)
    i2c.sda.value = 1
    await i2c._wait(i2c.t["t_low"] - i2c.t_hd_dat)
    await i2c._scl_high()
    await i2c._wait(i2c.t["t_high"] * 0.3)
    early = int(dut.sda_line.value)             # 1 = NACK being driven
    v0 = await bus.read(DATA)                   # CPU pops one byte now
    await i2c._wait(i2c.t["t_high"] * 0.6)
    late = int(dut.sda_line.value)
    i2c.scl.value = 0
    await ClockCycles(dut.clk_i, 50)
    sda_after_slot = int(dut.dut_sda_o.value)
    s1 = await status(bus)
    ok = await i2c.stop()
    await ClockCycles(dut.clk_i, 100)
    sda_after_stop = int(dut.dut_sda_o.value)
    s2 = await status(bus)
    rest = await drain_rx(bus)
    dut._log.info(f"ACK slot: early={early} late={late} popped={v0 & 0xff:#x} remaining={[hex(x) for x in rest]}")
    dut._log.info(f"violations={i2c.violations}")
    dut._log.info(f"DUT SDA after slot={sda_after_slot} COM={bit(s1, COM)}; STOP ok={ok}; DUT SDA after STOP={sda_after_stop} "
                  f"COM={bit(s2, COM)} COM_BEG={bit(s2, COM_BEG)} COM_END={bit(s2, COM_END)}")
    i2c.release()
    assert early == 1, "expected NACK with a full FIFO at the start of the ACK slot"
    assert late == 1 and not i2c.violations, (
        "FINDING: ACK/NACK level changed during SCL high after the CPU freed the RX FIFO "
        f"(early={early}, late={late}); a target must keep SDA stable while SCL is high (UM10204 3.1.3)")
    assert 0xEE not in rest, "FINDING: byte that was NACKed was still written to the RX FIFO"
    assert ok, "FINDING: bus hang after the ACK-slot race"


@cocotb.test()
async def test_10_start_stop_mid_byte(dut):
    """Aborted byte (STOP after 3 bits) and START mid-byte: TWD resynchronises."""
    bus, i2c = await setup(dut)
    await i2c.start()
    assert await i2c.address(DEV, read=False)
    for b in (1, 0, 1):
        await i2c.write_bit(b)
    assert await i2c.stop()                     # abort
    await i2c.start()
    assert await i2c.address(DEV, read=False)
    for b in (1, 1):
        await i2c.write_bit(b)
    await i2c.repeated_start()                  # abort with Sr
    assert await i2c.address(DEV, read=False)
    assert await i2c.write_byte(0x99)
    assert await i2c.stop()
    await ClockCycles(dut.clk_i, 100)
    assert await drain_rx(bus) == [0x99], "only the completed byte must be stored"
    assert not i2c.violations, i2c.violations


@cocotb.test()
async def test_11_disable_mid_transfer_releases_bus(dut):
    """Disabling the module during a transfer must release SDA and reset flags."""
    bus, i2c = await setup(dut)
    await bus.write(DATA, 0x00)
    await i2c.start()
    assert await i2c.address(DEV, read=True)
    for _ in range(3):
        await i2c.read_bit()                    # DUT is driving 0s
    assert int(dut.sda_line.value) == 0
    await bus.write(CTRL, 0)
    await ClockCycles(dut.clk_i, 20)
    assert int(dut.dut_sda_o.value) == 1, "SDA not released after disable"
    s = await status(bus)
    assert bit(s, COM) == 0 and bit(s, COM_BEG) == 0
    i2c.release()


# ----------------------------------------------------------------------------
# Timing characterisation against UM10204 Table 11
# ----------------------------------------------------------------------------
def scaled(base, f):
    """Scale a timing set to SCL frequency f (keeps the spec ratios)."""
    k = base["f_scl"] / f
    t = {key: (v * k if key.startswith("t_") else v) for key, v in base.items()}
    t["f_scl"] = f
    return t


async def write_read_probe(bus, i2c, nwrite=2):
    """One write transaction and one read transaction. Returns dict of outcomes."""
    r = {}
    await i2c.start()
    r["addr_ack_w"] = await i2c.address(DEV, read=False)
    r["data_acks"] = [await i2c.write_byte(0x30 + i) for i in range(nwrite)]
    r["stop_w"] = await i2c.stop()
    await ClockCycles(bus.dut.clk_i, 50)
    r["rx"] = await drain_rx(bus)
    await bus.write(DATA, 0xA7)
    await ClockCycles(bus.dut.clk_i, 5)
    await i2c.start()
    r["addr_ack_r"] = await i2c.address(DEV, read=True)
    r["rd"] = await i2c.read_byte(ack=False)
    r["stop_r"] = await i2c.stop()
    await ClockCycles(bus.dut.clk_i, 50)
    await bus.write(CTRL, (1 << EN) | (DEV << ADDR0) | (1 << CLR_TX) | (1 << CLR_RX))   # keep fsel via caller
    r["ok"] = (r["addr_ack_w"] and all(r["data_acks"]) and r["rx"] == [0x30 + i for i in range(nwrite)]
               and r["addr_ack_r"] and r["rd"] == 0xA7)
    return r


@cocotb.test()
async def test_12_max_scl_frequency_vs_fsel(dut):
    """Which SCL frequencies work with FSEL=0 (clk/8 sampling) and FSEL=1 (clk/64)?
    The datasheet gives no maximum. I2C modes: 100 kHz, 400 kHz, 1 MHz."""
    bus, i2c = await setup(dut)
    results = {}
    for fsel in (0, 1):
        await bus.write(CTRL, (1 << EN) | (fsel << FSEL) | (DEV << ADDR0))
        await ClockCycles(dut.clk_i, 300)
        for f in (100e3, 400e3, 1000e3, 2000e3, 3000e3, 4000e3, 6000e3):
            base = I2C_STANDARD if f <= 100e3 else I2C_FAST if f <= 400e3 else I2C_FASTPLUS
            i2c.set_timing(scaled(base, f))
            i2c.violations.clear()
            r = await write_read_probe(bus, i2c)
            await bus.write(CTRL, (1 << EN) | (fsel << FSEL) | (DEV << ADDR0) | (1 << CLR_TX) | (1 << CLR_RX))
            results[(fsel, f)] = r["ok"]
            dut._log.info(f"FSEL={fsel} fSCL={f/1e3:.0f} kHz: {'OK' if r['ok'] else 'FAIL'}  "
                          f"addrW={r['addr_ack_w']} data={r['data_acks']} rx={[hex(x) for x in r['rx']]} "
                          f"addrR={r['addr_ack_r']} rd={r['rd']:#x} stops={r['stop_w']},{r['stop_r']}")
            i2c.release()
            await Timer(5 * US, unit="ns")
    i2c.stop_monitor()
    assert results[(0, 400e3)] and results[(0, 1000e3)], "FSEL=0 must support Fast-mode and Fast-mode Plus"
    assert results[(1, 100e3)], "FSEL=1 must support Standard-mode"
    if not results[(1, 400e3)]:
        dut._log.warning("FINDING: with FSEL=1 (clk/64 sampling) the TWD does not work at 400 kHz "
                         "(Fast-mode) with a 100 MHz clock; the datasheet does not state any SCL limit.")
    assert results[(1, 400e3)], "FSEL=1: Fast-mode 400 kHz fails (undocumented limitation)"


@cocotb.test()
async def test_13_start_hold_time_minimum(dut):
    """Shortest tHD;STA the TWD still detects, per FSEL. Spec minimum: 4.0 us (Std),
    0.6 us (Fast), 0.26 us (Fast+). A spec-compliant host may use exactly the minimum."""
    bus, i2c = await setup(dut)
    found = {}
    for fsel in (0, 1):
        await bus.write(CTRL, (1 << EN) | (fsel << FSEL) | (DEV << ADDR0))
        await ClockCycles(dut.clk_i, 300)
        found[fsel] = None
        for hold in (4000, 2000, 1300, 1000, 800, 600, 400, 260, 200, 160, 100):
            t = dict(I2C_STANDARD if fsel else I2C_FAST)
            t["t_hd_sta"] = hold
            i2c.set_timing(t)
            await i2c.start()
            ack = await i2c.address(DEV, read=False)
            await i2c.stop()
            await Timer(5 * US, unit="ns")
            dut._log.info(f"FSEL={fsel} tHD;STA={hold} ns -> address {'ACK' if ack else 'no ACK'}")
            if ack:
                found[fsel] = hold
            else:
                break
    i2c.stop_monitor()
    dut._log.info(f"minimum detected tHD;STA: FSEL=0: {found[0]} ns, FSEL=1: {found[1]} ns")
    assert found[0] is not None and found[0] <= 600, f"FSEL=0 needs tHD;STA > 600 ns (Fast-mode minimum): {found[0]}"
    if found[1] is None or found[1] > 600:
        dut._log.warning(f"FINDING: with FSEL=1 a START with the Fast-mode minimum hold time (600 ns) is "
                         f"missed; shortest detected hold = {found[1]} ns. Only Standard-mode (4.0 us) is safe.")
    assert found[1] is not None and found[1] <= 4000, "FSEL=1 fails even Standard-mode tHD;STA"


@cocotb.test()
async def test_14_data_valid_time(dut):
    """tVD;DAT / tVD;ACK: time from SCL falling edge until the target's SDA is valid.
    Spec maximum: 3.45 us (Std), 0.9 us (Fast), 0.45 us (Fast+)."""
    bus, i2c = await setup(dut)
    res = {}
    for fsel, timing, limit, mode in ((0, I2C_FAST, 900, "Fast"), (0, I2C_FASTPLUS, 450, "Fast+"),
                                      (1, I2C_STANDARD, 3450, "Std"), (1, I2C_FAST, 900, "Fast")):
        await bus.write(CTRL, (1 << EN) | (fsel << FSEL) | (DEV << ADDR0) | (1 << CLR_TX) | (1 << CLR_RX))
        await ClockCycles(dut.clk_i, 300)
        i2c.set_timing(timing)
        i2c.dut_vd.clear()
        for b in (0x55, 0xAA, 0x0F):
            await bus.write(DATA, b)
        await i2c.start()
        ack = await i2c.address(DEV, read=True)
        got = [await i2c.read_byte(ack=True), await i2c.read_byte(ack=True), await i2c.read_byte(ack=False)]
        await i2c.stop()
        await Timer(5 * US, unit="ns")
        i2c.release()
        await bus.write(CTRL, 0)
        await ClockCycles(dut.clk_i, 20)
        vd = max(i2c.dut_vd) if i2c.dut_vd else None
        res[(fsel, mode)] = (vd, limit, ack, got)
        dut._log.info(f"FSEL={fsel} {mode}: max tVD = {vd} ns (spec max {limit} ns), addr ack={ack}, data={[hex(x) for x in got]}")
    i2c.stop_monitor()
    bad = {k: v for k, v in res.items() if v[0] is not None and v[0] > v[1]}
    for k, v in bad.items():
        dut._log.warning(f"FINDING: FSEL={k[0]} {k[1]}: data valid time {v[0]:.0f} ns exceeds UM10204 maximum {v[1]} ns")
    assert res[(0, "Fast")][0] <= 900 and res[(0, "Fast+")][0] <= 450, "FSEL=0 violates tVD;DAT"
    assert not bad, f"tVD;DAT violations: {bad}"


@cocotb.test()
async def test_15_interrupts(dut):
    """IRQ sources: RX_AVAIL, TX_EMPTY, COM_BEG, COM_END; W1C and FIFO ops clear them."""
    bus, i2c = await setup(dut, irq_mask=(1 << IRQ_RX_AVAIL) | (1 << IRQ_COM_BEG) | (1 << IRQ_COM_END))
    base = (1 << EN) | (DEV << ADDR0) | (1 << IRQ_RX_AVAIL) | (1 << IRQ_COM_BEG) | (1 << IRQ_COM_END)
    await ClockCycles(dut.clk_i, 5)
    assert int(dut.irq_o.value) == 0
    await i2c.start()
    assert await i2c.address(DEV, read=False)
    await ClockCycles(dut.clk_i, 50)
    assert int(dut.irq_o.value) == 1, "COM_BEG must raise IRQ"
    await bus.write(CTRL, base | (1 << COM_BEG))
    await ClockCycles(dut.clk_i, 5)
    assert int(dut.irq_o.value) == 0, "IRQ must drop after clearing COM_BEG"
    assert await i2c.write_byte(0x5A)
    await ClockCycles(dut.clk_i, 50)
    assert int(dut.irq_o.value) == 1, "RX_AVAIL must raise IRQ"
    assert await bus.read(DATA) & 0xff == 0x5A
    await ClockCycles(dut.clk_i, 5)
    assert int(dut.irq_o.value) == 0, "IRQ must drop after RX FIFO drained"
    assert await i2c.stop()
    await ClockCycles(dut.clk_i, 200)
    assert int(dut.irq_o.value) == 1, "COM_END must raise IRQ"
    await bus.write(CTRL, base | (1 << COM_END))
    await ClockCycles(dut.clk_i, 5)
    assert int(dut.irq_o.value) == 0
    # TX_EMPTY / TX_NFULL
    await bus.write(CTRL, (1 << EN) | (DEV << ADDR0) | (1 << IRQ_TX_EMPTY))
    await ClockCycles(dut.clk_i, 5)
    assert int(dut.irq_o.value) == 1, "TX_EMPTY IRQ"
    await bus.write(DATA, 1)
    await ClockCycles(dut.clk_i, 5)
    assert int(dut.irq_o.value) == 0
    await bus.write(CTRL, (1 << EN) | (DEV << ADDR0) | (1 << CLR_TX))
    await ClockCycles(dut.clk_i, 5)
    assert bit(await status(bus), TX_EMPTY) == 1, "CLR_TX must empty the TX FIFO"
    assert bit(await status(bus), CLR_TX) == 0, "CLR_TX must auto-clear"
    assert not i2c.violations, i2c.violations
