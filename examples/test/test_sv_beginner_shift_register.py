import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer


@cocotb.test()
async def shift_serial_bits(dut):
    dut.reset.value = 1
    dut.serial_in.value = 0
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())

    await Timer(2, "ns")
    assert int(dut.data_out.value) == 0
    dut.reset.value = 0

    expected_values = (1, 2, 5, 11)
    for bit, expected in zip((1, 0, 1, 1), expected_values):
        dut.serial_in.value = bit
        await RisingEdge(dut.clk)
        await Timer(1, "ns")
        assert int(dut.data_out.value) == expected
