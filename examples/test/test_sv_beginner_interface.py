import cocotb
from cocotb.triggers import Timer


@cocotb.test()
async def pass_value_through_interface(dut):
    for value in (0x00, 0x35, 0xA7, 0xFF):
        dut.value_in.value = value
        await Timer(1, units="ns")
        assert int(dut.value_out.value) == value
