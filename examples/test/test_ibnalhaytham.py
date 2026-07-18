import cocotb
from cocotb.triggers import Timer


@cocotb.test()
async def test_elaborates(dut):
    dut.wb_clk_i.value = 0
    dut.la1_data_in.value = 0
    dut.la1_oenb.value = 0
    dut.io_in.value = 0
    dut.user_clock2.value = 0
    await Timer(1, "ns")
