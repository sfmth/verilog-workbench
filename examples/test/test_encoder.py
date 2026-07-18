import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles
from .encoder import Encoder

async def reset(dut):
    dut.a.value = 0
    dut.b.value = 0
    dut.reset.value = 1

    await ClockCycles(dut.clk, 5)
    dut.reset.value = 0
    await ClockCycles(dut.clk, 5)


@cocotb.test()
async def test_perfect_encoder(dut):
    clock = Clock(dut.clk, 10, "us")
    clocks_per_phase = 5
    # no noise
    encoder = Encoder(dut.clk, dut.a, dut.b, clocks_per_phase = clocks_per_phase, noise_cycles = 0)
    cocotb.start_soon(clock.start())

    await reset(dut)
    assert int(dut.value.value) == 0

    # count up
    for i in range(clocks_per_phase * 2 *  255):
        await encoder.update(1)

    assert int(dut.value.value) == 255

    # count down
    for i in range(clocks_per_phase * 2  * 255):
        await encoder.update(-1)

    await ClockCycles(dut.clk, 5)
    assert int(dut.value.value) == 0
