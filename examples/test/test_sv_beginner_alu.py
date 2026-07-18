import cocotb
from cocotb.triggers import Timer


@cocotb.test()
async def check_alu_operations(dut):
    examples = (
        (0b00, 0xF0, 0x30, 0x20, 1),
        (0b01, 0xA5, 0x3C, 0x24, 0),
        (0b10, 0xA5, 0x3C, 0xBD, 0),
        (0b11, 0xA5, 0x3C, 0x99, 0),
    )

    for operation, left, right, expected_result, expected_carry in examples:
        dut.operation.value = operation
        dut.left.value = left
        dut.right.value = right
        await Timer(1, "ns")

        assert int(dut.result.value) == expected_result
        assert int(dut.carry.value) == expected_carry
