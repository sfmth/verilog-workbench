# Set the following parameters for your project:

# In this example we have a verilog file at verilog/pwm.v
# FILE is the address to your main verilog file
FILE = src/pwm

# PREFIX is the prefix for the verilog file
PREFIX = pwm

# TOPLEVEL is the name of the toplevel module in your Verilog file
TOPLEVEL = pwm  		

# MODULE is the basename of the Python test file
MODULE = test_pwm 		 


# COCOTB stuff
VERILOG_SOURCES += $(PWD)/$(FILE).v
include $(shell cocotb-config --makefiles)/Makefile.sim


# Show synthesized diagram with yosys
show_synth:
	yosys -p "read_verilog $(FILE).v; proc; opt -full; show -prefix $(FILE) -format png -viewer gwenview -colors 2 -width -signed"

# Show waveforms after simulation with gtkwave
gtkwave:
	gtkwave $(PREFIX).vcd $(PREFIX).gtkw

# Delete simulation files
delete:
	rm -rf sim_build/ __pycache__/ $(PREFIX).vcd results.xml
