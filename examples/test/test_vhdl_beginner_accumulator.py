import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer


@cocotb.test()
async def accumulate_values(dut):
    dut.reset.value = 1
    dut.enable.value = 0
    dut.addend.value = 0
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())

    for _ in range(2):
        await RisingEdge(dut.clk)
    await Timer(1, "ns")
    assert int(dut.total.value) == 0

    dut.reset.value = 0
    dut.enable.value = 1
    dut.addend.value = 10
    for expected in (10, 20, 30):
        await RisingEdge(dut.clk)
        await Timer(1, "ns")
        assert int(dut.total.value) == expected
        assert int(dut.carry.value) == 0

    dut.addend.value = 250
    await RisingEdge(dut.clk)
    await Timer(1, "ns")
    assert int(dut.total.value) == 24
    assert int(dut.carry.value) == 1

    dut.enable.value = 0
    await RisingEdge(dut.clk)
    await Timer(1, "ns")
    assert int(dut.total.value) == 24
    assert int(dut.carry.value) == 0
