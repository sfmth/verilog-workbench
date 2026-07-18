# Verilog Workbench

Verilog Workbench (`vwb.py`) is a small command-line workbench for learning and
checking digital logic. Put HDL source in `src/`, put tests in `test/`, and let
VWB discover the design hierarchy, run simulations, inspect waves, lint code,
synthesize schematics, and build supported FPGA targets.

VWB is designed to make the first steps simple without hiding the real tools.
Its output shows whether the RTL design passed, whether the default
post-synthesis gate-level check passed, and which external command failed when
something needs attention. Release changes are listed in
[`CHANGELOG.md`](CHANGELOG.md).

## Start Here: Docker

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

## Build Your First Design

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

### Useful Words

| Word | Plain meaning |
| --- | --- |
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

For an advanced native Ubuntu 26.04 setup, install the packaged tools first:

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

## Complete CLI Reference

Syntax:

```text
./vwb.py [GLOBAL OPTIONS] COMMAND [COMMAND OPTIONS]
```

Every command accepts `-h` or `--help`. Global options normally go before the
command. `init` also defines its path options after the command for convenient
project creation.

### Global Options

| Option | Default | Behavior |
| --- | --- | --- |
| `-h`, `--help` | Off | Show top-level help and exit. |
| `--version` | Off | Print the VWB version and exit. |
| `--root PATH` | See below | Set the project root. With no explicit root, VWB searches the current directory and its parents for `.vwb.json`; if none exists, it uses the directory containing `vwb.py`. |
| `--src-dir DIR` | Config, then `src` | Override the source directory. Relative paths are resolved from the project root. |
| `--test-dir DIR` | Config, then `test` | Override the test directory. Relative paths are resolved from the project root. |
| `--build-dir DIR` | Config, then `.vwb` | Override the generated artifact directory. It cannot be the project root or one of its ancestors, and it cannot overlap the source or test directory. |
| `--color {auto,always,never}` | `auto` | Control ANSI color. `auto` also honors `NO_COLOR` and non-terminal output. JSON output is never colored. |
| `-v`, `--verbose` | Off | Print external commands and cleanup actions. |
| `--dry-run` | Off | Validate and print planned commands without running tools or writing/removing files. |

Command-line directory values override `.vwb.json`; configuration values
override built-in defaults.

### `init`

```text
./vwb.py init [--root PATH] [--src-dir DIR] [--test-dir DIR]
              [--build-dir DIR] [--force]
```

| Argument or option | Default | Behavior |
| --- | --- | --- |
| `--root PATH` | Current directory | Set the new project root and `.vwb.json` location. |
| `--src-dir DIR` | `src` | Save and create the source directory. |
| `--test-dir DIR` | `test` | Save and create the test directory; create `__init__.py` when missing. |
| `--build-dir DIR` | `.vwb` | Save the artifact path. The directory is created by the first command that needs it. |
| `--force` | Off | Replace an existing `.vwb.json`; otherwise `init` refuses to overwrite it. |
| `-h`, `--help` | Off | Show `init` help. |

`--dry-run init` validates the paths and reports the configuration location
without creating anything.

### `list`

```text
./vwb.py list [--json]
```

| Option | Default | Behavior |
| --- | --- | --- |
| `--json` | Off | Emit packages, modules/entities, source language, files, dependencies, tests, and source files without units as JSON. |
| `-h`, `--help` | Off | Show `list` help. |

The text form is intended for people. `list` performs discovery only and does
not create build artifacts.

### `test` / `sim`

```text
./vwb.py test [OPTIONS] [MODULE ...]
./vwb.py sim  [OPTIONS] [MODULE ...]
```

| Argument or option | Default | Behavior |
| --- | --- | --- |
| `MODULE ...` | All discovered tests | Restrict the run to tests for named modules/entities. A named unit without a matching selected test gets a generated starter. |
| `--test FILE` | Discovery | Run one explicit `.py`, `.v`, `.sv`, `.vhd`, or `.vhdl` test. At most one module may accompany it; otherwise VWB infers the DUT from the file name. |
| `--test-language {auto,cocotb,verilog,vhdl}` | `auto` | Select all test kinds or one kind. With an explicit file, the value must match its type. |
| `--test-top UNIT` | Infer | Override the top module/entity for exactly one native HDL test. It is invalid for Cocotb. |
| `--testcase NAME` | All Cocotb tests | Select one Cocotb testcase. The full selection must be Cocotb-only. |
| `--seed INTEGER` | New seed per Cocotb test run | Set the Cocotb/Python random seed. Without it, VWB prints the chosen seed and reuses it for the RTL and gate stages of that test. The full selection must be Cocotb-only. |
| `--waves` | Off | Generate an RTL waveform without opening GTKWave. Verilog/SV sources are instrumented in the build tree; project sources remain unchanged. |
| `--wave-format {fst,vcd}` | `fst` | Select the RTL waveform format when waves are enabled. |
| `--max-array-words COUNT` | `32` | Dump at most the first `COUNT` words from every supported module-scope static Verilog/SV unpacked array; `0` dumps every word. Larger arrays do not fail the run. Unsupported dynamic, task-local, or aggregate-member arrays are warned about and skipped. When waves are enabled, negative values and values that do not fit in 128 bits are rejected. |
| `-D NAME[=VALUE]`, `--define NAME[=VALUE]` | None | Add a repeatable Verilog/SV preprocessing definition to simulation and gate synthesis. |
| `-I DIR`, `--include DIR` | None | Add a repeatable Verilog/SV include directory. Source/test roots and nested HDL/header directories are already included. |
| `--compile-arg ARG` | None | Add a repeatable raw Icarus preprocessing/compile argument for Verilog/SV. Use `--compile-arg=-flag` for values beginning with `-`. |
| `--sim-arg ARG` | None | Add a repeatable raw simulator argument before the simulation image, or to the GHDL run. |
| `--plusarg ARG` | None | Add a repeatable runtime plusarg; VWB supplies the leading `+` when absent. |
| `--no-gate-level` | Gate enabled | Skip post-synthesis functional simulation and require only RTL to pass. |
| `--keep-going` | Compatibility option | Accepted as a legacy option. Current test runs already execute every selected specification and report all failures. |
| `-h`, `--help` | Off | Show `test` help. |

RTL work is under `.vwb/sim/<unit>/<kind>-<test-stem>/`; gate work adds
`-gate` to that directory name. Cocotb success requires a successful simulator
exit and a nonempty passing results file.

### `wave` / `gtkwave`

`wave` uses the simulation options above, but runs only the RTL simulation by
default. Waves are always enabled and exactly one test must be selected. Add
`--gate-level` when you also want the same testbench run against the synthesized
Verilog netlist. GTKWave opens only after every selected stage passes.

```sh
../vwb.py wave counter                 # RTL waveform only
../vwb.py wave counter --gate-level    # RTL plus gate-level check
```

```text
./vwb.py wave [TEST OPTIONS] [WAVE OPTIONS] [MODULE ...]
```

| Additional option | Default | Behavior |
| --- | --- | --- |
| `--gate-level` | Off | Also run post-synthesis functional simulation before opening GTKWave. Without this option, `wave` runs RTL only. |
| `--save FILE` | `<wave>.gtkw` | Use an existing GTKWave save file. With `--load`, apply it to the archived wave. |
| `--tag NAME` | None | Archive a passing wave, metadata, and layout. Tags start with a letter/digit and then use letters, digits, `.`, `_`, or `-`. |
| `--replace-tag` | Off | Permit `--tag` to atomically replace an existing tag; invalid without `--tag`. |
| `--load TAG` | None | Skip simulation and open a self-contained archived wave. One optional module may verify the saved DUT. Simulation selectors are invalid. |
| `--list-saved` | Off | List saved tags. Optional positional modules filter the list. It cannot be combined with load/tag/replace/save or simulation options. |
| `--json` | Off | Emit `--list-saved` as JSON. It is invalid without `--list-saved`. |
| `-h`, `--help` | Off | Show `wave` help. |

`--waves` is accepted but redundant in this mode. The legacy `--keep-going`
option is accepted but a normal wave run always selects exactly one test.

### `lint`

```text
./vwb.py lint [OPTIONS] [MODULE ...]
```

| Argument or option | Default | Behavior |
| --- | --- | --- |
| `MODULE ...` | Units with discovered tests | Select module/entity hierarchies. Explicit units do not need tests. |
| `--all` | Off | Select every discovered source unit; invalid with positional modules. |
| `--linter {all,iverilog,verilator,yosys,verible,ghdl}` | All applicable | Select a checker. Repeat to combine tools. `all` expands by source language and is deduplicated with explicitly repeated tools. |
| `-D VALUE`, `--define VALUE` | None | Add a repeatable definition to the Verilog-family checks. Use `--ghdl-arg` for GHDL-specific controls. |
| `-I DIR`, `--include DIR` | None | Add a repeatable include directory to the Verilog-family checks. |
| `--iverilog-arg ARG` | None | Add a repeatable raw Icarus argument. |
| `--verilator-arg ARG` | None | Add a repeatable raw Verilator argument. |
| `--lint-arg ARG` | None | Legacy hidden alias for `--verilator-arg`. |
| `--yosys-arg TCL` | None | Append a repeatable Tcl line to the generated Yosys lint script. Prefix Yosys commands with `yosys`, for example `--yosys-arg='yosys stat'`. |
| `--verible-arg ARG` | Verible standard rules | Add a repeatable raw Verible argument. Use `--verible-arg=--ruleset=all` for every available rule or `--verible-arg=--ruleset=none` for syntax parsing only. |
| `--ghdl-arg ARG` | None | Add a repeatable raw GHDL argument to import and elaboration. |
| `--keep-going` | Compatibility option | Accepted as a legacy option. Current lint runs already execute every selected module/tool check. |
| `-h`, `--help` | Off | Show `lint` help. |

Arguments beginning with `-` should use the equals form, for example
`--verilator-arg=--timing`.

### `synth`

```text
./vwb.py synth [OPTIONS] [MODULE]
```

| Argument or option | Default | Behavior |
| --- | --- | --- |
| `MODULE` | Unique tested top/root | Select the unit to synthesize. Ambiguous projects must name it. |
| `--format {json,svg,png,dot}` | `png` | Select the preferred artifact. JSON is always generated. JSON skips rendering; DOT always uses Yosys. A PNG request returns SVG instead when a full-density PNG would exceed 16 megapixels. |
| `--full` | Off | Use `prep -flatten` with schematic preparation, or `synth -top` with direct Yosys preparation. |
| `--flatten` | Off | Run `flatten; opt_clean` after preparation unless the full schematic path already flattened the design. |
| `--schematic` | On | Try NetlistSVG for every SVG/PNG request. Use the full-netlist Yosys fallback only if NetlistSVG is missing, fails, times out, or returns invalid SVG. |
| `--no-schematic` | Off | Skip NetlistSVG and render SVG/PNG directly with Yosys `show`. |
| `--view VIEWER` | Automatic | Open the final artifact with an executable. Automatic mode uses Geeqie for PNG and Inkscape for SVG; JSON and DOT stay closed. An explicit viewer overrides this choice. `none`, `off`, `false`, or `0` disables viewing. |
| `--no-view` | Off | Alias for `--view none`. |
| `-D VALUE`, `--define VALUE` | None | Add a repeatable synthesis preprocessing definition. |
| `-I DIR`, `--include DIR` | None | Add a repeatable synthesis include directory. |
| `-h`, `--help` | Off | Show `synth` help. |

Scripts, converted sources, JSON, SVG, PNG, and Yosys DOT files are stored under
`.vwb/synth/<unit>/`. Visual rendering and rasterization have 120-second limits.
PNG files always use 2x density; oversized PNG requests return SVG instead.

### `formal`

```text
./vwb.py formal [--view] [CONFIG]
```

| Argument or option | Default | Behavior |
| --- | --- | --- |
| `CONFIG` | Auto-detect one `.sby` | Use an absolute path or one relative to the project. Without it, VWB requires exactly one `.sby` outside `.git` and the build tree. |
| `--view` | Off | After success, open the first sorted VCD trace with GTKWave; fail if no VCD exists. |
| `-h`, `--help` | Off | Show `formal` help. |

SymbiYosys output is replaced at `.vwb/formal/<config-stem>/`.

### `fpga`

```text
./vwb.py fpga [OPTIONS] [MODULE] --board BOARD
```

| Argument or option | Default | Behavior |
| --- | --- | --- |
| `MODULE` | Unique tested top/root | Select the FPGA top unit. |
| `--board {gowin,tangnano9k,ice40,icebreaker}` | Required | Select a fixed device/tool profile. `tangnano9k` maps to Gowin; `icebreaker` maps to iCE40. |
| `--stage {synth,pnr,pack,flash}` | `pack` | Run the cumulative flow through synthesis, place-and-route, packing, or hardware flashing. |
| `--constraints FILE` | Family default | Use an explicit constraints file. Default: `<src-dir>/io.cst` for Gowin or `<src-dir>/io.pcf` for iCE40. A file is required even for `synth`. |
| `-D VALUE`, `--define VALUE` | None | Add a repeatable Yosys definition; VWB also supplies `LEDS_NR=6`. |
| `-I DIR`, `--include DIR` | None | Add a repeatable synthesis include directory. |
| `-h`, `--help` | Off | Show `fpga` help. |

| Board selection | Device/profile | PNR result | Packed result | Flash target |
| --- | --- | --- | --- | --- |
| `gowin`, `tangnano9k` | `GW1NR-LV9QN88PC6/I5`, family `GW1N-9C` | `<unit>-pnr.json` | `<unit>.fs` | `tangnano9k` |
| `ice40`, `icebreaker` | UP5K, package `sg48` | `<unit>.asc` | `<unit>.bin` | `ice40_generic` |

Artifacts are under `.vwb/fpga/<family>/<unit>/`.

### `clean`

```text
./vwb.py clean [{temp,sim,waves,synth,lint,fpga,formal,all}]
```

| Scope | Default | Behavior |
| --- | --- | --- |
| `temp` | Yes | Remove simulation build files and live waves plus all lint work. Preserve live `.gtkw` layouts, synthesis, saved waves, FPGA results, and formal results. |
| `sim` | No | Remove live RTL/gate simulations and their local GTKWave layouts. |
| `waves` | No | Remove archived saved-wave tags only. |
| `synth` | No | Remove synthesis outputs, schematics, converted sources, and gate netlists. |
| `lint` | No | Remove lint work. |
| `fpga` | No | Remove FPGA build outputs. |
| `formal` | No | Remove formal outputs. |
| `all` | No | Remove the complete owned build directory, including synthesis and saved tags. |
| `-h`, `--help` | Off | Show `clean` help. |

### `doctor`

```text
./vwb.py doctor [--json]
```

| Option | Default | Behavior |
| --- | --- | --- |
| `--json` | Off | Emit tool groups as JSON, with executable paths or `null`. |
| `-h`, `--help` | Off | Show `doctor` help. |

The text report covers simulation, waveform, lint, synthesis, completion,
formal, Gowin, and iCE40 tools plus project unit/test counts. Its exit status
always requires Icarus, `vvp`, and Yosys. It also requires Cocotb configuration
when any design is discovered because VWB may create a Cocotb starter, GHDL
when any VHDL design is discovered, and sv2v when any SystemVerilog design is
discovered. Other missing tools are reported and may be required by the command
or option that uses them.

### Exit Status

| Status | Meaning |
| --- | --- |
| `0` | The requested work succeeded. |
| `1` | A test, wave run, lint check, or required doctor check failed. |
| `2` | Arguments, project input, configuration, or a required tool were invalid. |
| `130` | The command was interrupted with Ctrl-C. |

## Bundled Examples and Development

The repository's own `src/` and `test/` directories are a blank starter
workspace. The larger example and regression project lives under `examples/`:

```sh
./vwb.py --src-dir examples/src --test-dir examples/test list
./vwb.py --src-dir examples/src --test-dir examples/test test --no-gate-level
./vwb.py --src-dir examples/src --test-dir examples/test lint --all
```

These small examples introduce one idea at a time. Each command runs both RTL
and gate-level simulation unless you add `--no-gate-level`.

| Example | What to notice | Run it |
| --- | --- | --- |
| [`sv_beginner_counter`](examples/src/sv_beginner_counter.sv) | `always_ff`, an enable input, and active-low reset | `./vwb.py --src-dir examples/src --test-dir examples/test test sv_beginner_counter` |
| [`sv_beginner_alu`](examples/src/sv_beginner_alu.sv) | `always_comb` and constants/functions from a [package](examples/src/sv_beginner_math_pkg.sv) | `./vwb.py --src-dir examples/src --test-dir examples/test test sv_beginner_alu` |
| [`sv_beginner_interface`](examples/src/sv_beginner_interface.sv) | Signals grouped in a SystemVerilog interface and an included header | `./vwb.py --src-dir examples/src --test-dir examples/test test sv_beginner_interface` |
| [`sv_beginner_shift_register`](examples/src/sv_beginner_shift_register.sv) | Shifting bits and asynchronous reset | `./vwb.py --src-dir examples/src --test-dir examples/test test sv_beginner_shift_register` |
| [`vhdl_beginner_adder`](examples/src/vhdl_beginner_adder.vhd) | A VHDL-2008 combinational process | `./vwb.py --src-dir examples/src --test-dir examples/test test vhdl_beginner_adder` |
| [`vhdl_beginner_accumulator`](examples/src/vhdl_beginner_accumulator.vhd) | One VHDL entity instantiating another | `./vwb.py --src-dir examples/src --test-dir examples/test test vhdl_beginner_accumulator` |
| [`vhdl_beginner_counter`](examples/src/vhdl_beginner_counter.vhd) | An entity and its [architecture](examples/src/vhdl_beginner_counter_rtl.vhd) in separate files | `./vwb.py --src-dir examples/src --test-dir examples/test test vhdl_beginner_counter` |

The matching Cocotb files are under [`examples/test/`](examples/test/). Read a
test beside its design to see how inputs are driven and outputs are checked.

The repository uses three kinds of checks:

| Check | What it checks |
| --- | --- |
| `examples/test/test_<unit>.py`, `.v`, `.sv`, or `.vhd` | A **design unit test** drives one HDL module/entity and checks its outputs. |
| `examples/test/test_vwb*.py` | A **software unit test** checks the Python workbench itself, such as discovery, command options, cleanup, and waveform handling. |
| `scripts/validate_vwb.py` | An **integration test** runs VWB through the real tools in Docker and checks the generated files. |

Generated starter tests are intentionally simpler than design unit tests. They
only prove that a unit initializes and runs; add assertions before relying on a
starter to prove the unit's logic is correct.

GitHub CI builds the Dockerfile and runs `scripts/validate_vwb.py`. The harness
reads `vwb.py list --json`, so it finds new modules, entities, and tests without
a list of names in the CI script. The normal CI-safe paths run for real. This
includes checked-in tests, generated starter tests for units that have no test,
FST and VCD waves, lint, synthesis, and FPGA synthesis. The option matrix also
checks spelling and command setup with `--help` and `--dry-run`. This is how CI
checks options that open a window or flash a physical board without trying to
perform those actions. For a focused local harness run:

```sh
python3 scripts/validate_vwb.py --emit-matrix
python3 scripts/validate_vwb.py --phase regressions --phase contracts
```

Run `./vwb.py COMMAND --help` whenever the installed checkout is newer than
this guide.

## License

Distributed under the MIT License. See [`LICENSE.txt`](LICENSE.txt).
