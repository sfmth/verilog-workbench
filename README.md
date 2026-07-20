# Verilog Workbench

Verilog Workbench (`vwb`) finds HDL designs and tests, runs the tools needed to
check them, records waveforms, creates circuit drawings, and builds supported
FPGA projects. It is intended for people who know basic digital logic and are
starting to write Verilog, SystemVerilog, or VHDL.

## First Five Minutes

Local installation is recommended because it gives VWB direct access to your
display and FPGA hardware:

```sh
# Download Verilog Workbench.
git clone https://github.com/sfmth/verilog-workbench.git
# Enter the downloaded repository.
cd verilog-workbench
# Install the complete local tool set.
./setup.sh --full
```

Start a new terminal after setup finishes. Configure the bundled examples and
check the installation:

```sh
# Configure VWB to use the bundled designs and tests.
vwb init --src-dir examples/src --test-dir examples/test --build-dir .vwb
# Check that the required and optional tools are available.
vwb doctor
```

The last green line should be:

```text
CORE SETUP READY
```

Run the encoder test:

```sh
# Run the bundled encoder test.
vwb test encoder
```

A successful run ends with output like this:

```text
  RTL PASS
Result:
  Your code: PASS - 1/1 test runs passed
  Your setup: OK
```

Open the signal waveform:

```sh
# Simulate the encoder and open its signals.
vwb wave encoder
```

The waveform viewer opens automatically and shows the encoder inputs and
outputs changing over time.

## Starting A Project

Create and enter a project folder, then initialize it:

```sh
# Create a folder for the new project.
mkdir learning-counter
# Enter the project folder.
cd learning-counter
# Create the default source, test, and build configuration.
vwb init
```

Add HDL under `src/`, then check what VWB found:

```sh
# Show the designs and tests VWB discovered.
vwb list
# Check that the required tools are installed.
vwb doctor
# Run the counter test or create its starter test.
vwb test counter
# Run the counter test and open its waveform.
vwb wave counter
```

Use Tab after a partial module name to complete it, for example
`vwb wave cou<Tab>`. Generated work goes under `.vwb/` unless another build
folder was saved by `init`.

## Features

| Feature | What VWB does |
| --- | --- |
| Automatic discovery | Finds modules, entities, source files, dependencies, and matching tests. |
| HDL support | Works with Verilog, SystemVerilog, and VHDL-2008 designs. |
| Starter tests | Creates a small Cocotb test when a named design has no test. |
| Simulation | Tests source code by default and can also test synthesized logic. |
| Signal waves | Records FST or VCD without adding dump code to the design, including supported memories and arrays. |
| Source checks | Runs every suitable installed checker and reports all failures at the end. |
| Synthesis | Creates circuit drawings or raw synthesized data, with an automatic fallback if a schematic cannot be generated. |
| Saved waves | Tags known-good waveforms, remembers viewer layouts, and reopens saved results. |
| FPGA and formal flows | Builds supported FPGA boards and runs checks described by `.sby` files. |
| Project setup | Remembers project folders and provides terminal completion for commands, modules, and saved waves. |

Release changes are listed in [`CHANGELOG.md`](CHANGELOG.md).

## Project Layout

A normal project looks like this:

```text
my-project/
|-- .vwb.json                 # Folder choices saved by vwb init
|-- src/
|   |-- top.v                 # Verilog source
|   |-- control.sv            # SystemVerilog source
|   |-- helper.vhd            # VHDL source
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

Source file names do not have to match module or entity names. A source file
may contain more than one design block. VWB reads the declarations and follows
the selected design hierarchy.

Run `vwb init` once from the project root. Later commands find `.vwb.json` in
the current folder or a parent folder, so they can also be run from a project
subdirectory.

## Test File Discovery

VWB searches the configured `test/` tree recursively. The test file name must
identify the module or entity it tests. For a design named `counter`, these
names are detected automatically:

| Pattern | Examples |
| --- | --- |
| `test_<name>` | `test_counter.py`, `test_counter.sv`, `test_counter.vhd` |
| `test_<name>_<label>` | `test_counter_reset.py`, `test_counter_overflow.sv` |
| `tb_<name>` | `tb_counter.v`, `tb_counter.vhdl` |
| `<name>_test` | `counter_test.py`, `counter_test.sv` |
| `<name>_tb` | `counter_tb.v`, `counter_tb.vhd` |

Name matching is case-insensitive, so `test_counter.py` can match a design
named `Counter`. A name such as `counter.py` is not linked automatically. Use
an explicit module and test path when a file follows another naming scheme:

```sh
# Run a test whose file name does not follow the discovery patterns.
vwb test counter --test test/counter.py
```

Python files count as tests only when they contain at least one Cocotb test:

```python
import cocotb


@cocotb.test()
async def test_counting(dut):
    # Drive inputs and check outputs here.
    pass
```

An HDL testbench file may contain one testbench module or entity with any name.
If the file contains several possible tops and VWB cannot choose one, select it:

```sh
# Select the testbench top when an HDL test file contains several choices.
vwb test counter --test test/test_counter.sv --test-top counter_tb_top
```

Use `vwb list` after adding a file. The module entry shows the tests that VWB
matched. This is the quickest way to catch a naming mistake before simulation.

## Starter Tests

Naming a module that has no matching test creates and immediately runs a
starter Cocotb test:

```sh
# Generate and run test/test_pulse_counter_starter.py.
vwb test pulse_counter
```

The generated starter:

- initializes every input it can access, including unpacked-array inputs;
- starts a 10 ns clock for common clock names such as `clk`, `clock`, `clk_a`,
  `clk0`, `clk_100`, and `clkdiv`;
- recognizes common reset names, including active-low names such as `rst_n`,
  `arst_n`, `srst_n`, and `nreset`;
- applies reset briefly, releases it, and lets the design run;
- never changes an existing starter file.

The starter confirms that the design can compile and run. It does not know the
correct output values, so add input cases and assertions before treating it as
a complete test.

Cocotb is the default for Verilog, SystemVerilog, and VHDL. For a Verilog or
SystemVerilog design, an HDL starter can be requested instead:

```sh
# Generate and run a SystemVerilog starter testbench.
vwb test pulse_counter --test-language verilog
```

Automatic native VHDL testbench generation is not supported. Use the default
Cocotb starter for VHDL, or write a VHDL testbench and name it using one of the
discovery patterns above.

If a Verilog/SystemVerilog module has a parameter with no default value, VWB
cannot guess it and will not generate a broken starter. Add a default or write
a testbench that supplies the parameter.

Only an explicitly named untested module gets a starter. Running `vwb test`
without module names runs discovered tests; it does not create tests for every
untested source file.

## Language Support

VWB supports the practical, synthesizable parts of each language. Language
support can vary slightly with the tool versions installed on the machine.

| Language | Source files | Automatically detected tests | Synthesis and FPGA use |
| --- | --- | --- | --- |
| Verilog | `.v` | Cocotb, `.v`, or `.sv` testbench | Supported |
| SystemVerilog | `.sv` | Cocotb, `.v`, or `.sv` testbench | Supported, with automatic conversion when needed |
| VHDL-2008 | `.vhd`, `.vhdl` | Cocotb or VHDL testbench | Supported for synthesizable designs |

A selected design hierarchy cannot mix VHDL with Verilog/SystemVerilog. Native
HDL testbenches must use the same language family as the design. A native VHDL
testbench cannot drive the generated Verilog gate netlist, so use Cocotb when a
VHDL design also needs `--gate-level` testing.

## Installation

### Local Linux (Recommended)

The installer supports Ubuntu 24.04 and later, Debian, Fedora, Arch Linux, and
common distributions based on those families:

```sh
# Download Verilog Workbench.
git clone https://github.com/sfmth/verilog-workbench.git
# Enter the downloaded repository.
cd verilog-workbench
# Install the essential Verilog simulation tools.
./setup.sh
```

The default installs the essential Verilog simulation tools. The full setup
adds available VHDL, lint, synthesis, schematic, formal, waveform, and FPGA
tools:

```sh
# Add the available lint, synthesis, waveform, formal, VHDL, and FPGA tools.
./setup.sh --full
```

The installer uses the distribution package manager first. On Arch it can use
`paru` or `yay` when an optional package is available only from the AUR. A
missing optional tool does not prevent the core setup from working.

Useful setup options:

```sh
# Preview installation commands without running them.
./setup.sh --dry-run
# Install the full Arch tool set without using the AUR.
./setup.sh --full --no-aur
# Show every installer option.
./setup.sh --help
```

Start a new terminal after installation, then run:

```sh
# Verify the local installation after opening a new terminal.
vwb doctor
```

Setup installs the stable `vwb` command and Bash, Zsh, or Fish Tab completion.

### Docker

Install Docker Desktop, or Docker Engine on Linux, and Git:

```sh
# Download Verilog Workbench.
git clone https://github.com/sfmth/verilog-workbench.git
# Enter the downloaded repository.
cd verilog-workbench
# Build or reuse the Docker environment and open its shell.
./run-docker.sh
```

The first run builds the image. Later runs reuse a checkout-specific container
and open a shell with the repository mounted. The image includes the complete
simulation, lint, synthesis, waveform, formal, and FPGA tool set.

The stable `vwb` command and Bash completion are enabled in the container. Run
`./run-docker.sh --help` for launcher options. Use this after changing the
image or launcher configuration:

```sh
# Recreate the persistent container after launcher or image changes.
./run-docker.sh --recreate
```

On Linux, the launcher forwards the display and `/dev/bus/usb`, along with the
host user's device groups. FPGA programming can therefore reach a connected
USB programmer. The host must still grant the user access through the board's
udev rules or device group; reconnect the board and sign in again after changing
those permissions.

Docker Desktop on macOS and Windows does not expose the Linux USB device path.
Build in Docker and program the board from the host on those systems.

## Command Basics

Commands use the folders saved by `vwb init`. Global options go before the
command. The most useful ones select another project, show full tool output, or
preview work without running it:

```sh
# List a project without changing into its directory.
vwb --root ../cpu-project list
# Show complete simulator commands and output for a test.
vwb --verbose test counter
# Preview synthesis without running tools or opening a viewer.
vwb --dry-run synth counter --no-view
```

`--src-dir`, `--test-dir`, and `--build-dir` temporarily override saved
folders. Use `--color never` for plain logs. Run `vwb COMMAND --help` for the
complete option list.

## Project Setup: `vwb init`

`init` creates the source and test folders, adds `__init__.py` to the test
folder, and records the paths in `.vwb.json`. The defaults are `src/`, `test/`,
and `.vwb/`.

```sh
# Create the default src/, test/, and .vwb/ project layout.
vwb init
# Create and remember custom project folders.
vwb init --src-dir rtl --test-dir verification --build-dir build/vwb
```

Use `--force` to replace an existing `.vwb.json` after changing the project
layout. This does not erase source or test files.

## Design Discovery: `vwb list`

Run `list` after adding or renaming a design or test. It shows modules,
entities, source files, dependencies, and matched tests. This is the quickest
way to catch a file-naming or discovery problem.

```sh
# Show the discovered designs, dependencies, and tests.
vwb list
# Write the discovery report as JSON for a script or CI job.
vwb list --json > design-index.json
```

`--json` provides the same discovery data for scripts and CI.

## Tests: `vwb test` / `vwb sim`

With no names, `test` runs every discovered test. With names, it runs tests for
those modules. Failures do not stop later tests, and `sim` is an alias.

```sh
# Run every discovered test.
vwb test
# Run every test linked to counter.
vwb test counter
# Test counter and alu in one run.
vwb test counter alu
# Test the source and then the synthesized counter logic.
vwb test counter --gate-level
```

Gate-level simulation is off by default. It runs the same test against the
synthesized design when requested. Naming an untested module creates a starter;
see [Starter Tests](#starter-tests).

Use `--test-language cocotb`, `verilog`, or `vhdl` when a module has more than
one kind of test. `--test FILE` selects a particular file, and `--test-top`
selects the top inside an HDL testbench. For Cocotb, `--testcase` selects one
test function and `--seed` repeats a random run.

```sh
# Run only the counter's Cocotb tests.
vwb test counter --test-language cocotb
# Use one explicitly selected test file.
vwb test counter --test test/smoke.py
# Repeat one Cocotb test with a fixed random seed.
vwb test fifo --testcase test_full --seed 1432
```

Use `--waves` to save a waveform without opening it. FST is the smaller
default; VCD is available with `--wave-format vcd`. VWB records 32 entries from
each supported memory or array by default; change that with
`--max-array-words`, or use `0` for no limit.

```sh
# Run the counter test and save an FST waveform.
vwb test counter --waves
# Save a VCD containing up to 128 entries from each supported array.
vwb test memory --waves --wave-format vcd --max-array-words 128
```

Wave generation never edits source and does not require dump blocks. Use
repeatable `-D` and `-I` options for compile-time definitions and include
folders:

```sh
# Enable FEATURE and add rtl/include to the header search path.
vwb test top -D FEATURE -I rtl/include
```

## Waveforms: `vwb wave` / `vwb gtkwave`

`wave` runs one test, records its signals, and opens the result after a pass.
It accepts the test-selection and waveform options above. Gate simulation is
off unless `--gate-level` is supplied. `gtkwave` is an alias.

```sh
# Run the counter test and open its waveform.
vwb wave counter
# Run one Cocotb test function and open its waveform.
vwb wave counter --testcase test_reset
# Also check synthesized logic before opening the source waveform.
vwb wave counter --gate-level
```

In GTKWave, use **File > Write Save File** or press `Ctrl+S` to save the current
signals, ordering, colors, zoom, and other viewer state under `.vwb/sim/`. VWB
automatically loads that saved state the next time the same test is opened.
`--save FILE` selects an existing layout instead.

A passing waveform can be tagged and reopened without another simulation:

```sh
# Simulate counter and archive the passing waveform as known-good.
vwb wave counter --tag known-good
# Show all archived waveform tags.
vwb wave --list-saved
# Open known-good without rerunning the simulation.
vwb wave --load known-good
```

Use `--replace-tag` to update an existing tag and `--list-saved --json` for a
script-friendly list.

## Source Checks: `vwb lint`

`lint` checks selected designs and their dependencies. With no names, it checks
modules that have tests; `--all` checks every discovered design.

```sh
# Check counter with every suitable installed checker.
vwb lint counter
# Check counter and uart in one run.
vwb lint counter uart
# Check every discovered design.
vwb lint --all
```

Every suitable installed checker runs by default. Missing optional checkers are
marked as skipped, and failures do not stop later checks. Repeat `--linter` to
choose particular checkers.

```sh
# Check counter only with Icarus Verilog.
vwb lint counter --linter iverilog
# Check counter with both selected checkers.
vwb lint counter --linter iverilog --linter verilator
```

Default output shows useful warnings, errors, and a summary. Use global
`--verbose` for the complete transcript. Verilog definitions and include
folders can be passed with `-D` and `-I`.

## Synthesis: `vwb synth`

The default creates and opens a compact, white-background PNG circuit drawing.
`--full` shows more internal logic, while `--flatten` combines child modules
into the selected top.

```sh
# Create and open the default compact counter schematic.
vwb synth counter
# Create a processor schematic with more internal logic.
vwb synth processor --full
```

Use SVG for large designs. If a PNG is too large, VWB keeps an SVG instead. If
normal schematic generation fails, it falls back to raw synthesized output.

```sh
# Create a detailed SVG suited to a large design.
vwb synth processor --full --format svg
# Create an SVG directly from the raw synthesized output.
vwb synth counter --no-schematic --format svg
# Save synthesis data as JSON without opening a viewer.
vwb synth counter --format json --no-view
```

Formats are PNG, SVG, JSON, and DOT. `--no-view` is useful for CI or SSH;
`--view PROGRAM` chooses another opener. `--no-schematic` selects raw output.
Results are stored under `.vwb/synth/<module>/`. The `-D` and `-I` options work
the same way as in simulation.

## Formal Checks: `vwb formal`

`formal` runs checks described by a `.sby` file. VWB selects it automatically
when the project contains exactly one; otherwise name the file.

```sh
# Run the project's only formal configuration.
vwb formal
# Run a selected configuration and open its first generated trace.
vwb formal formal/fifo.sby --view
```

`--view` opens the first trace from a successful run. Results are under
`.vwb/formal/<configuration-name>/`.

## FPGA Builds: `vwb fpga`

VWB currently has fixed profiles for the Tang Nano 9K and iCEBreaker. The
family aliases select those same fixed boards; they do not select every board
from that FPGA family.

| `--board` value | Board profile | Default constraints | Packed file |
| --- | --- | --- | --- |
| `tangnano9k` or `gowin` | Tang Nano 9K, `GW1NR-LV9QN88PC6/I5` (`GW1N-9C`) | `src/io.cst` | `.fs` |
| `icebreaker` or `ice40` | iCEBreaker, UP5K `sg48` device | `src/io.pcf` | `.bin` |

Constraint-file names must match top-level design ports. Use `--constraints`
when the file is not the board default.

```sh
# Create a Tang Nano 9K programming file.
vwb fpga top --board tangnano9k
# Stop an iCEBreaker build after placement and routing.
vwb fpga top --board icebreaker --stage pnr
# Build and program a connected Tang Nano 9K over USB.
vwb fpga top --board tangnano9k --stage flash
# Build an iCEBreaker using a project-specific pin assignment file.
vwb fpga top --board icebreaker --constraints boards/rev-b.pcf
```

The default `pack` stage creates the programming file. Other stages are
`synth`, `pnr`, and `flash`; each includes the earlier work. Flashing needs the
full setup, a connected board, and working USB permissions. Results are under
`.vwb/fpga/<board-family>/<module>/`.

Try a complete bundled build without programming hardware:

```sh
# Build the bundled Tang Nano 9K validation design without programming a board.
vwb --src-dir examples/src --test-dir examples/test \
  fpga validation_fpga --board tangnano9k --stage pack
```

## Generated Files: `vwb clean`

Plain `clean` removes temporary simulation and lint work. It keeps saved waves,
viewer layouts, synthesis results, FPGA builds, and formal results.

```sh
# Remove temporary simulation and lint work only.
vwb clean
# Remove archived waveform tags.
vwb clean waves
# Remove synthesis and gate-netlist results.
vwb clean synth
# Preview removal of every VWB-generated result.
vwb --dry-run clean all
```

Other scopes are `sim`, `lint`, `fpga`, and `formal`. Saved waves and synthesis
are removed only by their explicit scopes or `all`. Cleanup refuses source,
test, project-root, and unrelated directories.

## Installation Check: `vwb doctor`

`doctor` reports installed and missing tools, separates essential tools from
optional features, and prints installation instructions when something is
missing.

```sh
# Show installed, missing, required, and optional tools.
vwb doctor
# Print the tool report as JSON for a script.
vwb doctor --json
```

Missing optional tools do not make the core setup fail. `--json` provides tool
paths for scripts.

## Terminal Completion

The local and Docker installers enable Tab completion for commands, options,
module/entity names, and saved waveform tags. Examples:

```text
vwb syn<Tab>                  -> vwb synth
vwb synth pro<Tab>            -> vwb synth processor
vwb wave --load known<Tab>    -> vwb wave --load known-good
```

Completion uses the active `.vwb.json`, so it also follows custom source,
test, and build directories.

## Exit Status

| Status | Meaning |
| --- | --- |
| `0` | The requested operation succeeded. |
| `1` | A test, lint run, waveform run, or required doctor check failed. |
| `2` | Arguments, project input, configuration, or a required external tool were invalid or unavailable. |
| `130` | The command was interrupted with Ctrl-C. |

The built-in help is the final reference for the installed version:

```sh
# Show global options and available commands.
vwb --help
# Replace COMMAND with a command name to show its complete options.
vwb COMMAND --help
```

## License

Distributed under the GNU General Public License, version 2 only. See
[`LICENSE.txt`](LICENSE.txt).
