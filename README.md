# Verilog Workbench

## First Five Minutes

The local Linux install is recommended. It gives VWB direct access to your
files, display, and FPGA hardware. Install Git, then run:

```sh
git clone https://github.com/sfmth/verilog-workbench.git
cd verilog-workbench
./setup.sh --full
```

Start a new terminal after setup finishes. Point VWB at the bundled examples
and check the setup:

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
VWB opens it automatically.

## What VWB Is

Verilog Workbench (`vwb.py`) is a command-line tool for people who know basic
digital logic and are starting to work with HDL. Put Verilog, SystemVerilog, or
VHDL source in `src/`, put tests in `test/`, and let VWB find the files, design
blocks, and matching tests. VWB handles the tool commands and keeps their
output focused on the result.

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

## Installation

### Local Linux (Recommended)

The local installer supports Ubuntu 24.04 and later, Debian, Fedora, Arch Linux,
and common distributions based on those families:

```sh
git clone https://github.com/sfmth/verilog-workbench.git
cd verilog-workbench
./setup.sh
```

The default installs the core Verilog tools. Use `./setup.sh --full` to add the
available VHDL, lint, synthesis, schematic, formal, waveform-viewing, and FPGA
tools. Start a new terminal when setup finishes, then run `vwb doctor`.

The installer uses your distribution's package manager first. On Arch it can
use `paru` or `yay` for packages that are only in the AUR. Optional packages
that are unavailable on your Linux release do not prevent the core setup from
working. Use `./setup.sh --dry-run` to preview the package commands.

Setup installs the `vwb` command and terminal Tab completion for Bash, Zsh, or
Fish.

### Docker

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

On Linux, `run-docker.sh` also forwards `/dev/bus/usb` and your account's device
groups into the container. This lets FPGA programming commands reach a USB
programmer, including one connected after the container starts. USB forwarding
does not bypass the host's device permissions, so install the udev rules for
your board or add your account to the group required by your distribution.
After changing permissions, sign out and back in.

Run `./run-docker.sh --recreate` once if the persistent container was created
before USB forwarding was added. Docker Desktop on macOS and Windows does not
provide this Linux USB path; program the board from the host on those systems.

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

## Command Help

The built-in help is the source of truth for current commands and options:

```sh
vwb --help
vwb COMMAND --help
```

## License

Distributed under the MIT License. See [`LICENSE.txt`](LICENSE.txt).
