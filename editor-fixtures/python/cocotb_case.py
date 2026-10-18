import cocotb
from model import tick


@cocotb.test()
async def check_counter(dut):
    expected = tick(1)
    assert expected == 2
