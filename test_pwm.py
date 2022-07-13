import cocotb
from cocotb.triggers import Timer
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles

async def generate_clock(dut):
    """Generate clock pulses."""

    for cycle in range(55000):
        dut.clk.value = 0
        await Timer(1, units="ns")
        dut.clk.value = 1
        await Timer(1, units="ns")

@cocotb.test()
async def my_first_test(dut):
    await cocotb.start(generate_clock(dut))  # run the clock "in the background"
    
    """Initialize"""
    dut.level.value = 0
    dut.reset.value = 1
    await Timer(2, units="ns")
    dut.reset.value = 0

    # begin the tesst:
    for i in range(11): # test different values for dut.level
        dut.level.value = i*25 # normalize i to level
        if (i != 0): # skip if i is zero
            await FallingEdge(dut.out) # find the falling edge of dut.out
            assert dut.counter.value == dut.level.value, "wrong duty cycle" # test if out turns off at the right moment
            await Timer(5000, units="ns") # wait before testing the next level
            

