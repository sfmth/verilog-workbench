# Verilog Workbench

Verilog Workbench (`vwb.py`) is a small command-line workbench for learning and
checking digital logic. Put Verilog, SystemVerilog, or VHDL source in `src/`,
put tests in `test/`, and let VWB find the files and connect the tools needed to
check them.

You only need basic digital logic knowledge: inputs, outputs, and simple gates.
For clocked designs, it also helps to know what a clock and reset do. You do not
need prior experience with simulators, synthesis tools, Cocotb, Makefiles, or
FPGA build tools. The glossary and examples below explain the remaining words
as they appear.

## Features

| Feature | What VWB does |
| --- | --- |
| Automatic setup | Finds design blocks, files they use, and matching tests without a list of file names. |
| Three design languages | Works with Verilog, SystemVerilog, and VHDL projects. |
| Starter tests | Creates a simple Cocotb test when a named design block has no test yet. |
| Two simulation checks | Tests the source as written and, for `test`, checks the same behavior after synthesis by default. |
| Signal waves | Records signal changes without adding dump code to the design, including supported memories and arrays. |
| Source checks | Runs Icarus, Verilator, Yosys, Verible, or GHDL and reports all problems together. |
| Circuit drawings | Creates real PNG or SVG schematics, with an automatic fallback when one drawing tool fails. |
| Saved work | Keeps useful waveforms, GTKWave layouts, synthesis results, and terminal Tab completion choices. |
| Later projects | Includes optional formal checks and build flows for supported FPGA boards. |
| Careful cleanup | Plain `clean` removes temporary work without deleting saved waves or synthesis results. |

Release changes are listed in [`CHANGELOG.md`](CHANGELOG.md).

## Docker Installation (Recommended)

Docker is the recommended setup. The project image contains Icarus Verilog,
GHDL, Cocotb, Yosys, Verilator, Verible, sv2v, GTKWave, NetlistSVG, Graphviz,
SymbiYosys, and the supported FPGA tools.

You need Docker and Git on the host:

```sh
git clone https://github.com/sfmth/verilog-workbench.git
cd verilog-workbench
./run-docker.sh
```

`run-docker.sh` builds the image, mounts this checkout into the container, and
opens a shell. It also forwards a Linux X11 display when one is available, so
GTKWave, Geeqie, and Inkscape can open windows.

Inside the container, try the bundled project:

```sh
./vwb.py --src-dir examples/src --test-dir examples/test doctor
./vwb.py --src-dir examples/src --test-dir examples/test list
./vwb.py --src-dir examples/src --test-dir examples/test test encoder --waves
./vwb.py --src-dir examples/src --test-dir examples/test synth encoder --no-view
```

The test command runs two functional checks by default:

1. **RTL simulation** checks the code as written.
2. **Gate-level simulation** asks Yosys to synthesize the design, then runs the
   same test against the generated Verilog netlist.

The generated FST waveform is under `.vwb/sim/`. The synthesized PNG is under
`.vwb/synth/`.

## Quick Start: Your First Design

Create a project inside the checkout so it remains available through the
Docker mount:

```sh
mkdir -p learning-counter
./vwb.py init --root learning-counter
```

This creates:

```text
learning-counter/
|-- .vwb.json
|-- src/
`-- test/
    `-- __init__.py
```

Create `learning-counter/src/counter.sv`:

```systemverilog
module counter (
    input  logic       clk,
    input  logic       reset,
    output logic [3:0] count
);
    always_ff @(posedge clk) begin
        if (reset)
            count <= 4'd0;
        else
            count <= count + 4'd1;
    end
endmodule
```

Now enter the project and ask VWB to test the module:

```sh
cd learning-counter
../vwb.py list
../vwb.py test counter
```

There is no test yet, so VWB creates
`test/test_counter_starter.py`. The starter sets every input to a known value,
recognizes common clock and reset names, starts a clock, and waits for the
design to run. VWB never overwrites an existing starter.

The generated test is a scaffold, not a complete verification plan. Open it,
drive useful input values, and add assertions for the expected output. Run the
same command after each change:

```sh
../vwb.py test counter --waves
```

Use `--no-gate-level` while concentrating only on RTL:

```sh
../vwb.py test counter --no-gate-level
```

## Useful Words

| Word | Plain meaning |
| --- | --- |
| **HDL** | A hardware description language: code used to describe digital logic. Verilog, SystemVerilog, and VHDL are HDLs. |
| **DUT** | "Design under test": the module or entity you are checking. |
| **Module / entity** | A named block of digital logic. Verilog calls it a module; VHDL calls it an entity. |
| **RTL** | Your HDL source before it is turned into gates. |
| **Testbench** | Code that drives DUT inputs and checks DUT outputs. A Cocotb test is a Python testbench. |
| **Unit test** | A small, repeatable test of one logic block. In VWB, this is usually a testbench that drives one module/entity and checks its outputs. |
| **Waveform** | A time chart of signals. Use it to see when values changed. |
| **Synthesis** | Turning RTL into logic gates and registers. |
| **Hierarchy** | One module using smaller modules inside it. |
| **Lint** | Checking HDL for likely mistakes without running a simulation. |
| **Gate-level / netlist** | The logic blocks and connections produced by synthesis. |
| **Package** | Shared names, constants, types, or functions used by several design files. |
| **Place and route** | Choosing physical FPGA cells and the wires between them. |

A useful test contains a check, called an assertion. For the counter above,
the middle of a Cocotb test could look like this:

```python
from cocotb.triggers import RisingEdge, Timer

dut.reset.value = 1
await RisingEdge(dut.clk)
dut.reset.value = 0

await RisingEdge(dut.clk)
await Timer(1, units="ns")
assert int(dut.count.value) == 1
```

The generated starter only checks that the design can start and run. Add
assertions like this to check that its logic is correct.

## Project Layout

A normal project looks like this:

```text
my-project/
|-- .vwb.json                 # Paths saved by vwb.py init
|-- src/
|   |-- top.v                 # Verilog
|   |-- control.sv            # SystemVerilog
|   |-- helper.vhd            # VHDL
|   `-- definitions.svh       # Included header
|-- test/
|   |-- __init__.py           # Makes Cocotb tests importable
|   |-- test_top.py           # Cocotb test
|   |-- test_control.sv       # Native Verilog/SystemVerilog testbench
|   `-- test_helper.vhd       # Native VHDL testbench
`-- .vwb/                     # Generated files, owned by VWB
    |-- sim/
    |-- synth/
    |-- lint/
    |-- saved-waves/
    |-- formal/
    `-- fpga/
```

Source file names do not need to match module or entity names. VWB reads
declarations and follows the selected hierarchy. A discovered test normally
uses one of these names:

```text
test_<unit>.*
test_<unit>_anything.*
tb_<unit>.*
<unit>_test.*
<unit>_tb.*
```

Python files count as tests only when they contain a Cocotb test decorator such
as `@cocotb.test()`.

## Language Support

VWB targets the practical, synthesizable parts of these languages. It is not a
complete implementation of every IEEE Verilog, SystemVerilog, or VHDL feature.

| Language | Source suffixes | RTL simulation | Tests | Synthesis, gate, and FPGA input |
| --- | --- | --- | --- | --- |
| Verilog | `.v` | Icarus Verilog | Cocotb or Verilog/SystemVerilog testbench | Yosys |
| SystemVerilog | `.sv` | Icarus first; sv2v is a fallback for constructs Icarus cannot compile | Cocotb or Verilog/SystemVerilog testbench | sv2v conversion when available, then Yosys |
| VHDL-2008 | `.vhd`, `.vhdl` | GHDL | Cocotb through GHDL VPI, or a native VHDL testbench | GHDL `--synth` converts to Verilog, then Yosys |

Important boundaries:

- A single selected hierarchy must not mix VHDL with Verilog/SystemVerilog.
  VWB does not include a mixed-language simulator.
- A native Verilog testbench cannot directly test a VHDL design, and a native
  VHDL testbench cannot directly test a Verilog design.
- A native VHDL testbench cannot drive the generated Verilog gate netlist. Use
  Cocotb to check both stages. For a native VHDL testbench, VWB runs RTL and
  prints `GATE SKIP` for the gate stage. You may still pass `--no-gate-level`.
- A VHDL entity and its architecture may be in the same file or in separate
  files. VWB finds matching architecture files without depending on file names.
- `synth`, gate simulation, and `fpga` use the same Yosys input conversion:
  sv2v is used when available for SystemVerilog, while GHDL converts VHDL to
  Verilog. The fixed FPGA board/device profiles still determine which designs
  can be placed and routed.

### Advanced Language Details

The points below matter after the basic RTL workflow is familiar:

- The gate-level run is a **functional, zero-delay** post-synthesis check.
  VWB first creates a fully mapped generic netlist. If that Verilog file is
  larger than 1 MiB, it automatically keeps word-level arithmetic and memories
  as generic Yosys cells so large learning designs remain practical to
  simulate. It is not a technology library simulation, does not apply SDF, and
  contains no placed-and-routed timing.
- Icarus, GHDL, sv2v, and Yosys each implement a practical subset of their
  language standards. A construct accepted by one tool may still need a
  simpler form for another stage.

VWB currently generates Cocotb starters for VHDL designs. It can generate a
SystemVerilog starter for a Verilog/SystemVerilog design when
`--test-language verilog` is selected.

## Everyday Commands

Run these from a project containing `.vwb.json`:

```sh
../vwb.py doctor                         # Check tools used by this project
../vwb.py list                           # Show units, languages, dependencies, tests
../vwb.py test                           # Run every discovered RTL and gate test
../vwb.py test counter --waves           # Test one unit and save an RTL FST
../vwb.py wave counter                    # Test one unit and open its RTL wave
../vwb.py lint counter                    # Run every applicable linter
../vwb.py synth counter --no-view         # Create a 2x white-background PNG
../vwb.py clean                           # Remove temporary work, keep synth and tags
```

Global options such as `--dry-run`, `--verbose`, and directory overrides go
before the command:

```sh
../vwb.py --dry-run test counter
../vwb.py --color never lint --all
```

Long option abbreviations are intentionally rejected. Write the complete
option name so commands remain unambiguous as VWB grows.

## Simulation: RTL and Gates

`test` and its alias `sim` discover matching tests and run all selected cases.
The report prints `RTL PASS` or `RTL FAIL`, followed by `GATE PASS` or
`GATE FAIL` unless `--no-gate-level` is used. A native VHDL testbench instead
prints `GATE SKIP`, because it cannot drive a Verilog netlist. The overall test
passes only when every stage that can run passes.

The gate netlist is written to:

```text
.vwb/synth/<unit>/gate/<unit>_gate.v
.vwb/synth/<unit>/gate/yosys_simlib.v
```

The first file is the synthesized design. The second gives Icarus the behavior
of Yosys's generic cells. Gate simulation reuses the test, defines, include
paths, simulator arguments, and plusargs. Wave generation is currently for the
RTL stage only.

For Verilog/SystemVerilog waves, VWB preprocesses a temporary copy of the
selected hierarchy under `.vwb/` and registers static module-scope unpacked
arrays and memories for dumping. It leaves project sources unchanged. The
default is 32 words from each array; `--max-array-words 0` removes the limit. A
larger array is still included, but only its first words are added to the wave.
Dynamic, procedural, or aggregate-member arrays cannot be registered at module
startup. VWB warns and skips those arrays while still dumping every supported
array in the same design. VHDL waves use GHDL's native FST/VCD output and do
not need this instrumentation.

When an explicitly named unit has no matching test, VWB creates a starter and
runs it. With `auto` or `cocotb`, the starter is Python. With `verilog`, a
Verilog/SystemVerilog design gets an `.sv` testbench. The starter initializes
inputs and handles common `clk`, `clock`, `reset`, and `rst` names, but it does
not invent expected output values.

## Waves, Saved Tags, and Completion

Use `test --waves` to generate a wave without opening a GUI:

```sh
../vwb.py test counter --waves                 # FST, the default
../vwb.py test counter --waves --wave-format vcd
```

Use `wave` or its alias `gtkwave` to run exactly one test and open GTKWave:

```sh
../vwb.py wave counter
```

VWB keeps the `.gtkw` layout beside the live waveform when it refreshes other
simulation outputs. Archive a passing run when it is useful for comparison:

```sh
../vwb.py wave counter --tag known-good
../vwb.py wave --list-saved
../vwb.py wave --load known-good
../vwb.py wave counter --tag known-good --replace-tag
```

A saved tag is self-contained under `.vwb/saved-waves/<tag>/`: waveform,
metadata, and any GTKWave layout are copied together. Plain `clean` preserves
these tags.

VWB has terminal Tab completion for commands, options, discovered module/entity
names, and saved waveform tags. The Docker image enables Bash completion
automatically and provides the stable `vwb` command. After rebuilding with
`./run-docker.sh --recreate` once, examples such as these complete when you
press Tab:

```sh
vwb synth du<Tab>
vwb test sv_beginner_<Tab>
vwb wave --load known-<Tab>
```

For a native installation, first create the same stable command from the
repository root:

```sh
mkdir -p "$HOME/.local/bin"
ln -sf "$PWD/vwb.py" "$HOME/.local/bin/vwb"
```

Then run the matching setup once and restart the terminal.

**Bash:**

```sh
printf '\neval "$(register-python-argcomplete --shell bash vwb)"\n' >> "$HOME/.bashrc"
```

**Zsh:**

```sh
printf '\nautoload -Uz compinit && compinit\neval "$(register-python-argcomplete --shell zsh vwb)"\n' >> "$HOME/.zshrc"
```

**Fish:**

```fish
mkdir -p "$HOME/.config/fish/completions"
register-python-argcomplete --shell fish vwb > "$HOME/.config/fish/completions/vwb.fish"
```

Completion uses the saved project directories from `.vwb.json`, so module and
waveform suggestions continue to work after `vwb init` without repeating path
options.

## Linting

Without `--linter`, VWB runs every applicable checker:

| Design language | Default checks |
| --- | --- |
| Verilog/SystemVerilog | Icarus, Verilator, Yosys, Verible |
| VHDL | GHDL, then converted-Verilog checks with Icarus, Verilator, and Yosys |

Examples:

```sh
../vwb.py lint counter
../vwb.py lint --all
../vwb.py lint counter --linter verilator --verilator-arg=--Wno-UNUSED
../vwb.py lint counter --linter yosys --yosys-arg='yosys stat'
../vwb.py lint counter --linter verible --verible-arg=--ruleset=all
../vwb.py lint counter --linter iverilog --linter verilator
```

All requested checks run so one invocation reports every failure. A missing or
inapplicable selected tool is a failed check, not a silent skip. Tool-specific
work is stored under `.vwb/lint/<unit>/<tool>/`.

The defaults are:

- Icarus: `-g2012 -Wall -t null -s <unit>`.
- Verilator: `--lint-only --top-module <unit> --timing -Wall -Wno-fatal
  -Wno-COMBDLY -Wno-DECLFILENAME -Wno-INCABSPATH`. Warnings are printed but
  do not hide later checker results or turn into tool errors.
- Yosys: read the hierarchy, run `hierarchy -check`, `proc`, then
  `check`. The terminal shows only Yosys warnings and errors; the complete
  Yosys transcript is saved as `.vwb/lint/<unit>/yosys/yosys.log` when more
  detail is needed. Syntax and hierarchy errors still fail the check.
- Verible: use its standard rule set. Pass `--verible-arg=--ruleset=all` for
  every available style rule, or `--verible-arg=--ruleset=none` for syntax only.
  When a source uses `` `include`` or refers to a macro supplied with `-D`, VWB
  first uses Icarus to create a temporary copy for Verible. An `-I` path tells
  Icarus where to find an included file, but does not change files that do not
  use it.
- GHDL: import and elaborate with `--std=08`.

## Synthesis and Schematics

The default command prefers and opens a PNG. For a very large drawing it
returns and opens the SVG instead:

```sh
../vwb.py synth counter
```

Use `--no-view` in Docker without a display, over SSH, or in CI:

```sh
../vwb.py synth counter --no-view
```

Yosys always writes a JSON netlist first. For SVG and PNG, VWB always asks
NetlistSVG for a readable schematic when schematic mode is enabled. NetlistSVG
is not skipped because a design looks large. VWB falls back to Yosys `show`
plus Graphviz only when NetlistSVG is missing, times out, returns an error, or
does not produce a valid SVG. If Graphviz's normal `dot` layout also reaches
the limit, VWB retries the generated DOT file with the scalable `sfdp` layout.
The drawing always comes from the requested JSON netlist, including with
`--full`, and that JSON remains available if every visual renderer fails.

PNG output passes through `rsvg-convert` at 2x scale with an opaque white
background. If that full-density PNG would exceed 16 megapixels, VWB keeps and
returns the SVG instead of shrinking the image. PNG files open in Geeqie by
default; SVG files, including this automatic fallback, open in Inkscape.

```sh
../vwb.py synth counter --format json --no-view
../vwb.py synth counter --format svg --no-view
../vwb.py synth counter --format png --no-view
../vwb.py synth counter --format dot --no-view
../vwb.py synth counter --no-schematic --no-view  # Choose Yosys directly
```

JSON does not invoke a visual renderer. DOT always uses Yosys. VHDL is
converted by GHDL and SystemVerilog is converted through sv2v when available
before Yosys reads the synthesis input.

## Safe Cleanup

VWB marks its build directory and refuses to clean an unowned directory, a
source/test directory, the project root, an ancestor, or a build directory
owned by a different project.

Plain `clean` is intentionally conservative:

```sh
../vwb.py clean
```

It removes temporary simulation and lint work. It preserves:

- `.vwb/synth/`, including gate netlists and schematics.
- `.vwb/saved-waves/`, including tagged waves and layouts.
- `.vwb/fpga/`, including placed designs and bitstreams.
- `.vwb/formal/`, including proof results and traces.
- Live `.gtkw` layouts below `.vwb/sim/`, so the next `wave` run can load them.

Use an explicit scope when you really want to remove those files:

```sh
../vwb.py clean sim
../vwb.py clean lint
../vwb.py clean fpga
../vwb.py clean formal
../vwb.py clean synth       # Remove synthesis and gate artifacts
../vwb.py clean waves       # Remove archived wave tags
../vwb.py clean all         # Remove the entire owned build directory
```

Preview cleanup with the global dry-run option:

```sh
../vwb.py --dry-run clean all
```

## Local Installation

Docker is the complete and recommended installation. Native Linux package
versions differ, and the shorter setup below does not install sv2v or Verible.
Without sv2v, some SystemVerilog designs cannot pass every stage and `doctor`
reports the missing converter. Without Verible, the default SystemVerilog lint
set reports that its Verible check could not run. Use Docker when learning or
when you need every documented feature.

For a native Ubuntu 26.04 setup, install the packaged tools first:

```sh
sudo apt update
sudo apt install iverilog yosys ghdl gtkwave verilator graphviz \
  libgvplugin-neato-layout8 librsvg2-bin \
  nodejs npm geeqie inkscape git make python3 python3-pip libpython3-dev \
  nextpnr-ice40 nextpnr-gowin fpga-icestorm openfpgaloader boolector z3
```

Install the Python tools into your user account:

```sh
python3 -m pip install --user --break-system-packages \
  argcomplete==3.6.3 cocotb==1.7.2 apycula click bitstring numpy pillow
```

Make the user binary directory available permanently. Run this once, then
reload the shell:

```sh
printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.bashrc"
. "$HOME/.bashrc"
```

Install NetlistSVG and the pinned SymbiYosys revision used by the container:

```sh
sudo npm install -g netlistsvg
git clone https://github.com/YosysHQ/sby.git /tmp/sby
git -C /tmp/sby checkout --detach fea6e467d067b3ea84b6b5ac08cd48beb59f0d42
sudo make -C /tmp/sby install
rm -rf /tmp/sby
```

The remaining SystemVerilog conversion uses `sv2v`, and the full lint set uses
`verible-verilog-lint`. Their release packaging is architecture-specific; the
repository `Dockerfile` is the exact, pinned installation reference. This
manual native path is therefore partial until both commands are installed.

After installing tools, check the result:

```sh
./vwb.py doctor
```

Ubuntu's `nextpnr-gowin` package may install a Himbaechel executable. VWB tries
`nextpnr-himbaechel-gowin`, generic `nextpnr-himbaechel`, and legacy
`nextpnr-gowin` in that order.

## Command Help

The README covers the learning workflow instead of repeating every command
option. Use the built-in help for the exact choices available in your copy:

```sh
./vwb.py --help
./vwb.py COMMAND --help
```

## License

Distributed under the MIT License. See [`LICENSE.txt`](LICENSE.txt).
