import cocotb
from cocotb.triggers import Timer


@cocotb.test()
async def xor_truth_table(dut):
    for value in range(4):
        dut.a.value = value >> 1
        dut.b.value = value & 1
        await Timer(1, unit="ns")
        assert int(dut.y.value) == ((value >> 1) ^ (value & 1))
    # Exercise simulator shutdown with a final queued deposit after all four
    # functional assertions. A native simulator crash must still fail the run.
    dut.a.value = 0
