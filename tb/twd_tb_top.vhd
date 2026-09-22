-- Testbench wrapper for neorv32_twd (I2C target).
--  * unpacks the bus_req_t / bus_rsp_t records into scalar ports
--  * generates the SoC's prescaled clock enables (copied from neorv32_sys_clock)
--  * resolves the open-drain SDA line between the DUT and the external controller
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library neorv32;
use neorv32.neorv32_package.all;

entity twd_tb_top is
  generic (
    RX_FIFO : natural := 4;
    TX_FIFO : natural := 4
  );
  port (
    clk_i     : in  std_ulogic;
    rstn_i    : in  std_ulogic;
    -- register bus (subset of bus_req_t / bus_rsp_t)
    bus_addr  : in  std_ulogic_vector(31 downto 0);
    bus_wdata : in  std_ulogic_vector(31 downto 0);
    bus_stb   : in  std_ulogic;
    bus_rw    : in  std_ulogic;
    bus_ack   : out std_ulogic;
    bus_err   : out std_ulogic;
    bus_rdata : out std_ulogic_vector(31 downto 0);
    -- external I2C controller drivers (open drain: 0 = pull low, 1 = release)
    ctl_sda_o : in  std_ulogic;
    ctl_scl_o : in  std_ulogic;
    -- resolved bus lines and DUT driver (for monitoring)
    sda_line  : out std_ulogic;
    scl_line  : out std_ulogic;
    dut_sda_o : out std_ulogic;
    irq_o     : out std_ulogic
  );
end entity;

architecture sim of twd_tb_top is
  signal req      : bus_req_t;
  signal rsp      : bus_rsp_t;
  signal cnt, cnt2, en : std_ulogic_vector(11 downto 0);
  signal clkgen   : std_ulogic_vector(7 downto 0);
  signal sda_dut  : std_ulogic;
  signal sda_res, scl_res : std_ulogic;
begin
  -- prescaled clock enables, identical to neorv32_sys_clock
  ticker: process(rstn_i, clk_i)
  begin
    if (rstn_i = '0') then
      cnt  <= (others => '0');
      cnt2 <= (others => '0');
    elsif rising_edge(clk_i) then
      cnt  <= std_ulogic_vector(unsigned(cnt) + 1);
      cnt2 <= cnt;
    end if;
  end process;
  en <= cnt and (not cnt2);
  clkgen(clk_div2_c)    <= en(0);
  clkgen(clk_div4_c)    <= en(1);
  clkgen(clk_div8_c)    <= en(2);
  clkgen(clk_div64_c)   <= en(5);
  clkgen(clk_div128_c)  <= en(6);
  clkgen(clk_div1024_c) <= en(9);
  clkgen(clk_div2048_c) <= en(10);
  clkgen(clk_div4096_c) <= en(11);

  -- bus request
  req.meta  <= (others => '0');
  req.addr  <= bus_addr;
  req.data  <= bus_wdata;
  req.ben   <= (others => '1');
  req.stb   <= bus_stb;
  req.rw    <= bus_rw;
  req.amo   <= '0';
  req.amoop <= (others => '0');
  req.burst <= '0';
  req.lock  <= '0';
  bus_ack   <= rsp.ack;
  bus_err   <= rsp.err;
  bus_rdata <= rsp.data;

  -- open-drain bus resolution (pull-ups -> '1' when nobody drives low)
  sda_res  <= sda_dut and ctl_sda_o;
  scl_res  <= ctl_scl_o;                 -- TWD never drives SCL
  sda_line <= sda_res;
  scl_line <= scl_res;
  dut_sda_o <= sda_dut;

  dut: entity neorv32.neorv32_twd
    generic map (TWD_RX_FIFO => RX_FIFO, TWD_TX_FIFO => TX_FIFO)
    port map (
      clk_i => clk_i, rstn_i => rstn_i,
      bus_req_i => req, bus_rsp_o => rsp,
      clkgen_i  => clkgen,
      twd_sda_i => sda_res, twd_sda_o => sda_dut,
      twd_scl_i => scl_res,
      irq_o     => irq_o );
end architecture;
