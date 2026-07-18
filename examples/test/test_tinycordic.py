"""Small top-level test for the CORDIC example."""

import cocotb
from cocotb.triggers import Timer


CLOCK_HALF_PERIOD_US = 5


async def clock_cycles(dut, io_value, count):
    """Pulse bit 0 of io_in while leaving reset and the angle unchanged."""
    for _ in range(count):
        dut.io_in.value = io_value & ~1
        await Timer(CLOCK_HALF_PERIOD_US, "us")
        dut.io_in.value = io_value | 1
        await Timer(CLOCK_HALF_PERIOD_US, "us")


@cocotb.test()
async def test_tinycordic(dut):
    # z0 occupies io_in[7:2]. Use a small positive binary angle.
    angle = 8
    angle_bits = angle << 2

    # Reset is io_in[1]. Three clocks also initialize the example's ROM values.
    await clock_cycles(dut, angle_bits | 0b10, 3)

    # Release reset and allow the six CORDIC steps to finish.
    await clock_cycles(dut, angle_bits, 10)

    assert dut.io_out.value.binstr[0] == "1", "CORDIC did not raise its done output"
