import cocotb
from cocotb.triggers import Timer


@cocotb.test()
async def add_with_carry(dut):
    examples = (
        (7, 5, 12, 0),
        (200, 100, 44, 1),
        (255, 1, 0, 1),
    )

    for left, right, expected_sum, expected_carry in examples:
        dut.left.value = left
        dut.right.value = right
        await Timer(1, units="ns")

        assert int(dut.sum.value) == expected_sum
        assert int(dut.carry.value) == expected_carry
