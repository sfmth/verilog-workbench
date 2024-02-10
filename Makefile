NAM = rgb_mixer

SINGLE = True

# Set the following parameters for your project:
# In this example we have a verilog file at verilog/pwm.v
# FILE is the address to your main verilog file
FILE = src/$(NAM)

# PREFIX is the prefix for the verilog file
PREFIX = $(NAM)

# TOPLEVEL is the name of the toplevel module in your Verilog file
TOPLEVEL = $(NAM)

# MODULE is the basename of the Python test file
MODULE = test.test_$(NAM)


IGNORE = 'register_file.v\|alu.v'




# Reset
Color_Off='\033[0m'
bold='\033[1m'
normal=$(tput sgr0)
# Regular Colors
Black='\033[0;30m'
Red='\033[0;31m'
Green='\033[0;32m'
Yellow='\033[0;33m'
Blue='\033[0;34m'
Purple='\033[0;35m'
Cyan='\033[0;36m'
White='\033[0;37m'





# Find all of the source files
PWD = $(shell pwd)
DIRLIST_FULL := $(shell find $(PWD)/src/ -name "*.*v" | grep -v $(FILE) | grep -v $(IGNORE))
DIRLIST := $(shell find src/ -name "*.*v" | grep -v $(FILE) | grep -v $(IGNORE))
DIRLIST_IVERILOG := $(PWD)/$(FILE).v $(PWD)/src/ $(DIRLIST_FULL)
# COCOTB stuff
ifeq (True, $(SINGLE))
	VERILOG_SOURCES := $(PWD)/$(FILE).v
else
	VERILOG_SOURCES := $(DIRLIST_IVERILOG)
endif
#VERILOG_SOURCES := $(PWD)/$(FILE).v
# include $(shell cocotb-config --makefiles)/Makefile.sim
include Makefile.icarus

tes:
	echo $(VERILOG_SOURCES)
# Show synthesized diagram with yosys
# #yosys -p "read_verilog $(FILE).v; proc; opt -full; show -prefix $(FILE) -format png -viewer geeqie -colors 2 -width -signed"
# yosys -p "read_verilog $(FILE); hierarchy -top $(TOPLEVEL) -libdir src/; proc; extract -map ${dirlist[*]}; opt -full ; show -colors 2 -width -signed -long rgb_mixer"
# test_:
# 	rm -rf sim_build/
# 	mkdir sim_build/
# 	iverilog -o sim_build/sim.vvp -s rgb_mixer -s dump -g2012 src/rgb_mixer.v test/dump.v src/ $(DIRLIST_FULL)
# 	PYTHONOPTIMIZE=0 MODULE=test.test_rgb_mixer vvp -M $$(cocotb-config --prefix)/cocotb/libs -m libcocotbvpi_icarus sim_build/sim.vvp
# 	! grep failure results.xml
# CFLAGS=-g
# export CFLAGS
# target:
# 	$(MAKE) -C target
module: delete_sim
	@echo -e '\n' $(Yellow)$(bold) '==>' $(Green)$(bold)Starting simulation$(Color_Off) '\n'
	$(MAKE)
# sim:
# 	$(MAKE) delete
# 	$(MAKE)
# 	$(MAKE) gtkwave

formal:
	sby -f properties.sby
	gtkwave properties/engine_0/trace0.vcd $(PREFIX).gtkw

show_synth_full_svg:
	yosys -p "read_verilog $(FILE).v; hierarchy -top $(TOPLEVEL) -libdir src/; synth ; show -prefix show_synth/$(PREFIX) -format svg -viewer inkscape -colors 2 -width -signed $(TOPLEVEL)"

show_synth_svg:
	yosys -p "read_verilog $(FILE).v; hierarchy -top $(TOPLEVEL) -libdir src/; proc; extract -map ${DIRLIST}; opt -full ; show -prefix show_synth/$(PREFIX) -format svg -viewer inkscape -colors 2 -width -signed $(TOPLEVEL)"

show_synth_png:
	yosys -p "read_verilog $(FILE).v; hierarchy -top $(TOPLEVEL) -libdir src/; proc; extract -map ${DIRLIST}; opt -full ; show -prefix show_synth/$(PREFIX) -format png -viewer geeqie -colors 2 -width -signed $(TOPLEVEL)"

show_synth_dot: 
	yosys -p "read_verilog $(FILE).v; hierarchy -top $(TOPLEVEL) -libdir src/; proc; extract -map ${DIRLIST}; opt -full ; show -prefix show_synth/$(PREFIX) -colors 2 -width -signed -long $(TOPLEVEL)"
	# yosys -p "read_verilog $(FILE).v; hierarchy -top $(TOPLEVEL) -libdir src/; synth -coarse ; show -prefix show_synth/$(PREFIX) -colors 2 -width -signed -long $(TOPLEVEL)"

show_synth_human:
	# yosys -p "read_verilog $(FILE).v; hierarchy -top $(TOPLEVEL) -libdir src/; proc; extract -map ${DIRLIST}; opt -full ; write_json show_synth/$(PREFIX).json"
	yosys -p "read_verilog $(FILE).v; hierarchy -top $(TOPLEVEL) -libdir src/; prep ; write_json show_synth/$(PREFIX).json"
	netlistsvg show_synth/$(PREFIX).json -o show_synth/$(PREFIX).svg
	# geeqie show_synth/$(PREFIX).svg
	convert show_synth/$(PREFIX).svg show_synth/$(PREFIX).png
	geeqie show_synth/$(PREFIX).png


show_synth_full_human:
	yosys -p "read_verilog $(FILE).v; hierarchy -top $(TOPLEVEL) -libdir src/; prep -flatten ; write_json show_synth/$(PREFIX).json"
	netlistsvg show_synth/$(PREFIX).json -o show_synth/$(PREFIX).svg
	# inkscape show_synth/$(PREFIX).svg
	convert show_synth/$(PREFIX).svg show_synth/$(PREFIX).png
	geeqie show_synth/$(PREFIX).png


	# Show waveforms after simulation with gtkwave
gtkwave: module
	@echo -e '\n' $(Yellow)$(bold) '==>' $(Green)$(bold)Runing waveform viewer$(Color_Off) '\n'
	gtkwave $(PREFIX).vcd $(PREFIX).gtkw
gtkwave_good:
	gtkwave $(PREFIX)_good.vcd $(PREFIX).gtkw

# Delete simulation files
delete_sim:
	@echo -e '\n' $(Yellow)$(bold) '==>' $(Green)$(bold)Deleting temporary files$(Color_Off) '\n'
	rm -rf sim_build/ test/__pycache__/ $(PREFIX).vcd results.xml properties/

delete_synth:
	@echo -e '\n' $(Yellow)$(bold) '==>' $(Green)$(bold)Deleting temporary files$(Color_Off) '\n'
	rm -f show_synth/$(PREFIX)*

delete:
	@echo -e '\n' $(Yellow)$(bold) '==>' $(Green)$(bold)Deleting temporary files$(Color_Off) '\n'
	rm -rf sim_build/ test/__pycache__/ $(PREFIX).vcd results.xml properties/
	rm -f show_synth/*


lint: 
	@echo -e '\n' $(Yellow)$(bold) '==>' $(Green)$(bold)Checking for Warnings \& Errors$(Color_Off) '\n'
	verilator --lint-only -Wall -Wno-COMBDLY -Wno-INCABSPATH src/$(PREFIX).v


synth:
	@echo -e '\n' $(Yellow)$(bold) '==>' $(Green)$(bold)Synthesizing the design$(Color_Off) '\n'
	# yosys -D LEDS_NR=6 -p "read_verilog src/$(PREFIX).v; synth_gowin -json fpga/$(PREFIX).json"
	yosys -D LEDS_NR=6 -p "read_verilog $(FILE).v; hierarchy -top $(TOPLEVEL) -libdir src/; synth_gowin -json fpga/$(PREFIX).json"
	

pnr: synth
	@echo -e '\n' $(Yellow)$(bold) '==>' $(Green)$(bold)Running Place and Route$(Color_Off) '\n'
	nextpnr-gowin --json fpga/$(PREFIX).json --write fpga/pnr$(PREFIX).json --device GW1NR-LV9QN88PC6/I5 --family GW1N-9C --cst src/io.cst

pack: pnr
	@echo -e '\n' $(Yellow)$(bold) '==>' $(Green)$(bold)Compiling the final binary file$(Color_Off) '\n'
	gowin_pack -d GW1N-9C -o fpga/pack.fs fpga/pnr$(PREFIX).json

flash_fpga: pack
	@echo -e '\n' $(Yellow)$(bold) '==>' $(Green)$(bold)Flashing the binary file onto the FPGA board$(Color_Off) '\n'
	openFPGALoader -b tangnano9k fpga/pack.fs


