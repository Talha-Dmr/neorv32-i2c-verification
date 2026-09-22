"""
Bus-functional models for the NEORV32 TWD/TWI testbenches.

  I2CController : bit-banged I2C controller (host) with configurable timing
                  (UM10204 Table 11) and a protocol monitor that records every
                  SDA change made by the DUT relative to SCL.
  RegBus        : NEORV32 internal register bus (single-shot stb, ack next cycle).
"""
import cocotb
from cocotb.triggers import RisingEdge, FallingEdge, Timer, Edge, First, Event
from cocotb.utils import get_sim_time

US = 1000.0


def now():
    return get_sim_time(unit="ns")


def _b(sig):
    try:
        return int(sig.value)
    except (ValueError, TypeError):
        return 0


# I2C timing sets (ns), UM10204 Rev 7 Table 11 minimums (max for tVD)
I2C_STANDARD = dict(f_scl=100e3, t_hd_sta=4000, t_low=4700, t_high=4000, t_su_sta=4700,
                    t_su_dat=250, t_su_sto=4000, t_buf=4700, t_vd_dat_max=3450, t_vd_ack_max=3450)
I2C_FAST     = dict(f_scl=400e3, t_hd_sta=600, t_low=1300, t_high=600, t_su_sta=600,
                    t_su_dat=100, t_su_sto=600, t_buf=1300, t_vd_dat_max=900, t_vd_ack_max=900)
I2C_FASTPLUS = dict(f_scl=1000e3, t_hd_sta=260, t_low=500, t_high=260, t_su_sta=260,
                    t_su_dat=50, t_su_sto=260, t_buf=500, t_vd_dat_max=450, t_vd_ack_max=450)


class I2CController:
    """Drives ctl_sda_o / ctl_scl_o (open drain: 0 = pull low, 1 = release) and
    observes sda_line / scl_line / dut_sda_o."""

    def __init__(self, dut, timing=I2C_FAST, t_hd_dat=300.0):
        self.dut = dut
        self.sda = dut.ctl_sda_o
        self.scl = dut.ctl_scl_o
        self.sda_line = dut.sda_line
        self.scl_line = dut.scl_line
        self.dut_sda = dut.dut_sda_o
        self.t = dict(timing)
        self.t_hd_dat = t_hd_dat           # our data hold after SCL fall
        self.sda.value = 1
        self.scl.value = 1
        # monitor state
        self.last_scl_fall = None
        self.last_scl_rise = None
        self.dut_changes = []              # (t, new_value, scl_high, dt_since_fall)
        self.violations = []               # DUT changed SDA while SCL high
        self.dut_vd = []                   # data-valid delays after SCL fall (ns)
        self.bus_hangs = 0
        self._own_change = False           # controller is doing START/STOP
        self._run = True
        self._mon = cocotb.start_soon(self._monitor())

    def stop_monitor(self):
        self._run = False

    def set_timing(self, timing):
        self.t = dict(timing)

    # ---------------- monitor
    async def _monitor(self):
        prev_dut = _b(self.dut_sda)
        prev_scl = _b(self.scl_line)
        while self._run:
            await First(Edge(self.dut_sda), Edge(self.scl_line))
            t = now()
            scl = _b(self.scl_line)
            d = _b(self.dut_sda)
            if scl != prev_scl:
                if scl:
                    self.last_scl_rise = t
                else:
                    self.last_scl_fall = t
                prev_scl = scl
            if d != prev_dut:
                dt = None if self.last_scl_fall is None else t - self.last_scl_fall
                self.dut_changes.append((t, d, bool(scl), dt))
                if scl == 1 and _b(self.sda) == 1:
                    # DUT changed the resolved SDA line while SCL was high
                    self.violations.append((t, d, "SDA %s while SCL high (looks like %s)"
                                            % ("fell" if d == 0 else "rose", "START" if d == 0 else "STOP")))
                elif scl == 0 and dt is not None:
                    self.dut_vd.append(dt)
                prev_dut = d

    # ---------------- low-level bit timing
    async def _wait(self, ns):
        if ns > 0:
            await Timer(round(ns), unit="ns")

    async def _scl_high(self, timeout_ns=50 * US):
        self.scl.value = 1
        t0 = now()
        while _b(self.scl_line) == 0:          # clock stretching
            await Timer(10, unit="ns")
            if now() - t0 > timeout_ns:
                raise TimeoutError("SCL held low (clock stretching) for too long")

    async def write_bit(self, b):
        # SCL is low on entry; change data after hold time, keep setup before rise
        await self._wait(self.t_hd_dat)
        self.sda.value = int(b)
        await self._wait(self.t["t_low"] - self.t_hd_dat)
        await self._scl_high()
        await self._wait(self.t["t_high"])
        self.scl.value = 0

    async def read_bit(self, sample_frac=0.5):
        await self._wait(self.t_hd_dat)
        self.sda.value = 1                     # release
        await self._wait(self.t["t_low"] - self.t_hd_dat)
        await self._scl_high()
        await self._wait(self.t["t_high"] * sample_frac)
        b = _b(self.sda_line)
        await self._wait(self.t["t_high"] * (1 - sample_frac))
        self.scl.value = 0
        return b

    # ---------------- conditions
    async def start(self):
        """START from an idle bus (SDA=SCL=1)."""
        self._own_change = True
        self.sda.value = 0
        await self._wait(self.t["t_hd_sta"])
        self.scl.value = 0
        self._own_change = False

    async def repeated_start(self):
        """Repeated START from SCL low."""
        await self._wait(self.t_hd_dat)
        self.sda.value = 1
        await self._wait(self.t["t_low"] - self.t_hd_dat)
        await self._scl_high()
        await self._wait(self.t["t_su_sta"])
        self._own_change = True
        self.sda.value = 0
        await self._wait(self.t["t_hd_sta"])
        self.scl.value = 0
        self._own_change = False

    async def stop(self):
        """STOP from SCL low. Returns False if the DUT keeps SDA low (bus hang)."""
        await self._wait(self.t_hd_dat)
        self.sda.value = 0
        await self._wait(self.t["t_low"] - self.t_hd_dat)
        await self._scl_high()
        await self._wait(self.t["t_su_sto"])
        self._own_change = True
        self.sda.value = 1
        await self._wait(200)
        ok = _b(self.sda_line) == 1
        self._own_change = False
        if not ok:
            self.bus_hangs += 1
        await self._wait(self.t["t_buf"])
        return ok

    # ---------------- bytes
    async def write_byte(self, byte):
        for i in range(7, -1, -1):
            await self.write_bit((byte >> i) & 1)
        ack_bit = await self.read_bit()
        return ack_bit == 0                    # True = ACK

    async def read_byte(self, ack=True):
        v = 0
        for _ in range(8):
            v = (v << 1) | await self.read_bit()
        await self.write_bit(0 if ack else 1)
        return v

    async def address(self, addr7, read):
        return await self.write_byte(((addr7 & 0x7f) << 1) | (1 if read else 0))

    def release(self):
        self.sda.value = 1
        self.scl.value = 1


class RegBus:
    """NEORV32 device bus: stb single-shot, ack one cycle later, read data
    valid in the ack cycle."""

    def __init__(self, dut):
        self.dut = dut
        dut.bus_addr.value = 0
        dut.bus_wdata.value = 0
        dut.bus_stb.value = 0
        dut.bus_rw.value = 0

    async def write(self, addr, data):
        d = self.dut
        await FallingEdge(d.clk_i)
        d.bus_addr.value = addr
        d.bus_wdata.value = data & 0xffffffff
        d.bus_rw.value = 1
        d.bus_stb.value = 1
        await FallingEdge(d.clk_i)
        d.bus_stb.value = 0
        await RisingEdge(d.clk_i)
        assert _b(d.bus_ack) == 1, "no bus ack on write"

    async def read(self, addr):
        d = self.dut
        await FallingEdge(d.clk_i)
        d.bus_addr.value = addr
        d.bus_rw.value = 0
        d.bus_stb.value = 1
        await FallingEdge(d.clk_i)
        d.bus_stb.value = 0
        await RisingEdge(d.clk_i)
        assert _b(d.bus_ack) == 1, "no bus ack on read"
        return int(d.bus_rdata.value)
