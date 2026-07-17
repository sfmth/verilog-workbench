import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer


@cocotb.test()
async def count_when_enabled(dut):
    dut.reset_n.value = 0
    dut.enable.value = 0
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())

    for _ in range(2):
        await RisingEdge(dut.clk)
    await Timer(1, units="ns")
    assert int(dut.count.value) == 0

    dut.reset_n.value = 1
    dut.enable.value = 1
    for expected in range(1, 6):
        await RisingEdge(dut.clk)
        await Timer(1, units="ns")
        assert int(dut.count.value) == expected

    dut.enable.value = 0
    await RisingEdge(dut.clk)
    await Timer(1, units="ns")
    assert int(dut.count.value) == 5
