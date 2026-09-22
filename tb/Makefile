# cocotb testbench for NEORV32 TWD (I2C target) / TWI (I2C host) on GHDL
SIM ?= ghdl
TOPLEVEL_LANG ?= vhdl
RTL := $(abspath ../../neorv32/rtl/core)

VHDL_LIB_ORDER := neorv32
VHDL_SOURCES_neorv32 := $(RTL)/neorv32_package.vhd $(RTL)/neorv32_prim.vhd \
                        $(RTL)/neorv32_twd.vhd $(RTL)/neorv32_twi.vhd
VHDL_SOURCES := $(abspath twd_tb_top.vhd)

COCOTB_TOPLEVEL ?= twd_tb_top
COCOTB_TEST_MODULES ?= test_twd

RX_FIFO ?= 4
TX_FIFO ?= 4
GHDL_ARGS += --std=08
SIM_ARGS  += -gRX_FIFO=$(RX_FIFO) -gTX_FIFO=$(TX_FIFO) $(EXTRA_SIM_ARGS)
ifeq ($(WAVES),1)
SIM_ARGS  += --wave=dump.ghw
endif
export RX_FIFO TX_FIFO
include $(shell cocotb-config --makefiles)/Makefile.sim
