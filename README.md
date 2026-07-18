# Verilog Workbench

## First Five Minutes

Docker is the quickest way to get the same tools on every computer. Install
[Docker](https://docs.docker.com/get-docker/) and Git, then run:

```sh
git clone https://github.com/sfmth/verilog-workbench.git
cd verilog-workbench
./run-docker.sh
```

Inside the Docker shell, point VWB at the bundled examples and check the setup:

```sh
vwb init --src-dir examples/src --test-dir examples/test --build-dir .vwb
vwb doctor
```

The last green line should be:

```text
CORE SETUP READY
```

Now test the example encoder:

```sh
vwb test encoder
```

A successful run ends with output like this:

```text
  RTL PASS
Result:
  Your code: PASS - 1/1 test runs passed
  Your setup: OK
```

Open its signal waveform:

```sh
vwb wave encoder
```

GTKWave shows the encoder inputs and outputs changing over time. On Linux,
`run-docker.sh` forwards the display automatically when a graphical session is
available.

## What VWB Is

Verilog Workbench (`vwb.py`) is a command-line tool for learning and checking
digital logic. Put Verilog, SystemVerilog, or VHDL source in `src/`, put tests
in `test/`, and let VWB find the files, design blocks, and matching tests.

You only need basic digital logic knowledge: inputs, outputs, gates, and, for a
clocked design, what its clock and reset do. You do not need to know simulator
command lines, Cocotb setup, or FPGA tool commands before starting.

## Features

| Feature | What VWB does |
| --- | --- |
| Automatic discovery | Finds modules, entities, their source files, dependencies, and matching tests. |
| Three HDL languages | Supports Verilog, SystemVerilog, and VHDL-2008 designs. |
| Starter tests | Creates a simple Cocotb test when a named design has no test. |
| Simulation | Tests source code by default; `--gate-level` also tests synthesized logic. |
| Signal waves | Records FST or VCD waves without dump code in the design, including bounded memories and arrays. |
| Source checks | Uses every available suitable linter and clearly skips optional linters that are not installed. |
| Circuit drawings | Creates PNG or SVG schematics and uses the fallback only when NetlistSVG actually fails. |
| Saved work | Keeps tagged waves, GTKWave layouts, synthesis results, and terminal Tab-completion choices. |
| Optional advanced flows | Includes formal checks and builds for supported FPGA boards. |
| Careful cleanup | Plain `clean` removes temporary files without deleting saved waves or synthesis results. |

Release changes are listed in [`CHANGELOG.md`](CHANGELOG.md).

## Project Layout

A normal project looks like this:

```text
my-project/
|-- .vwb.json                 # Folder choices saved by vwb init
|-- src/
|   |-- top.v                 # Verilog
|   |-- control.sv            # SystemVerilog
|   |-- helper.vhd            # VHDL
|   `-- definitions.svh       # Included SystemVerilog header
|-- test/
|   |-- __init__.py
|   |-- test_top.py           # Cocotb test
|   |-- test_control.sv       # Verilog/SystemVerilog testbench
|   `-- test_helper.vhd       # VHDL testbench
`-- .vwb/                     # Files created by VWB
    |-- sim/
    |-- synth/
    |-- lint/
    |-- saved-waves/
    |-- formal/
    `-- fpga/
```

Source file names do not need to match module or entity names. VWB reads the
declarations and follows the design hierarchy. Test files normally use names
such as `test_counter.py`, `test_counter.sv`, `tb_counter.vhd`, or
`counter_test.py`. A Python file is treated as a test only when it contains a
Cocotb test marked with `@cocotb.test()`.

Create `src/` and `test/` in your project when you are ready to add a design.
Bundled learning designs and VWB's own regression tests live under `examples/`.

## Language Support

VWB supports the practical, synthesizable parts of each language. No simulator
or synthesis tool supports every possible language feature.

| Language | Source files | Source simulation | Test choices | Synthesis input |
| --- | --- | --- | --- | --- |
| Verilog | `.v` | Icarus Verilog | Cocotb or an HDL testbench | Yosys |
| SystemVerilog | `.sv` | Icarus first, with sv2v conversion when needed | Cocotb or an HDL testbench | sv2v when needed, then Yosys |
| VHDL-2008 | `.vhd`, `.vhdl` | GHDL | Cocotb or a VHDL testbench | GHDL conversion, then Yosys |

A native VHDL testbench cannot directly drive the Verilog netlist made by
Yosys. Use a Cocotb test when you want the optional gate-level check for VHDL.

## Tests And Results

Run a test by giving VWB a module or entity name:

```sh
vwb test counter
```

VWB tests the source design, called RTL, by default. Gate-level simulation is
slower and is an extra learning check, so it is opt-in:

```sh
vwb test counter --gate-level
```

When no matching test exists, VWB creates a Cocotb starter. It initializes DUT
inputs, recognizes common clock and reset names, starts the clock, releases the
reset, and lets the design run briefly. The starter proves that the design can
compile and run; add assertions to check that its outputs are correct.

Results deliberately separate design problems from setup problems:

- `Your code: FAIL` means a checker or test ran and found a problem.
- `Your setup: TOOLS MISSING` means a stage could not run. Use `vwb doctor`.
- `SKIPPED (verible-verilog-lint not installed)` does not mean the HDL is bad.

Linting uses all suitable tools that are installed. For example, a Verilog
project can still run `vwb lint counter` with only Icarus installed; Verilator,
Yosys, and Verible are reported as optional skips. A lint command needs at
least one available checker to inspect the code.

## Installation

### Docker (Recommended)

Install Docker Desktop, or Docker Engine on Linux, and Git. From a terminal:

```sh
git clone https://github.com/sfmth/verilog-workbench.git
cd verilog-workbench
./run-docker.sh
```

The first run builds the image. Later runs reuse it and open a shell with the
repository mounted, so files created in the container remain on the host. The
image includes Icarus Verilog, Cocotb, GHDL, Yosys, Verilator, Verible, sv2v,
GTKWave, NetlistSVG, Graphviz, Inkscape, formal tools, and supported FPGA tools.

The stable `vwb` command and Bash Tab completion are enabled inside the
container. Run `./run-docker.sh --help` to see its container option.

### Local Linux

The local installer supports Ubuntu 24.04 and later, Debian, Fedora, Arch Linux,
and common distributions based on those families. It asks the system package
manager for tools first, so package names and versions follow your Linux
release instead of being fixed inside VWB:

```sh
git clone https://github.com/sfmth/verilog-workbench.git
cd verilog-workbench
./setup.sh
# Start a new terminal after setup finishes, then run:
vwb --src-dir examples/src --test-dir examples/test doctor
```

The default is a small core install for Verilog and common SystemVerilog. Use
`./setup.sh --full` when you also want every available VHDL, lint, synthesis,
schematic, formal, and FPGA tool. Optional packages that your release does not
provide are skipped without breaking the core install.

On Arch, the script uses official `pacman` packages first and can then use
`paru` or `yay` for missing AUR packages. On every supported system, Cocotb and
Tab completion use an isolated user Python environment only when the system
package manager does not provide them. Run `./setup.sh --dry-run` to inspect the
planned package commands without changing the computer.

The script makes the `vwb` command and terminal Tab completion permanent for
Bash, Zsh, or Fish. Start a new terminal after it finishes. Doctor shows what
is available, what the current project needs, and which advanced features are
still optional.

## Starting Your Own Project

From a new project folder:

```sh
vwb init
```

Put HDL in `src/`, then use:

```sh
vwb list
vwb doctor
vwb test MODULE
vwb wave MODULE
```

Use Tab after a partial module name to complete it. VWB writes generated work
under `.vwb/`. Plain `vwb clean` removes temporary simulation and lint files.
Saved wave tags and synthesis results require the explicit `clean waves` or
`clean synth` scopes.

## Useful Words

| Word | Plain meaning |
| --- | --- |
| **HDL** | Code used to describe digital logic. Verilog, SystemVerilog, and VHDL are HDLs. |
| **DUT** | The design under test: the module or entity being checked. |
| **RTL** | Your HDL source before synthesis turns it into logic cells. |
| **Testbench** | Code that drives DUT inputs and checks DUT outputs. Cocotb testbenches use Python. |
| **Waveform** | A time chart showing how signal values change. |
| **Lint** | A source check for likely mistakes without running a simulation. |
| **Synthesis** | Turning HDL source into connected logic cells and registers. |
| **Gate-level / netlist** | The synthesized logic cells and their connections. |
| **Hierarchy** | A design block using smaller design blocks inside it. |

## Command Help

The built-in help is the source of truth for current commands and options:

```sh
vwb --help
vwb COMMAND --help
```

## License

Distributed under the MIT License. See [`LICENSE.txt`](LICENSE.txt).
