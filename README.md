<!--
*** Thanks for checking out the Best-README-Template. If you have a suggestion
*** that would make this better, please fork the repo and create a pull request
*** or simply open an issue with the tag "enhancement".
*** Thanks again! Now go create something AMAZING! :D
***
***
***
*** To avoid retyping too much info. Do a search and replace for the following:
*** github_username, repo_name, twitter_handle, email, project_title, project_description
-->



<!-- PROJECT SHIELDS -->
<!--
*** I'm using markdown "reference style" links for readability.
*** Reference links are enclosed in brackets [ ] instead of parentheses ( ).
*** See the bottom of this document for the declaration of the reference variables
*** for contributors-url, forks-url, etc. This is an optional, concise syntax you may use.
*** https://www.markdownguide.org/basic-syntax/#reference-style-links
-->

<!-- PROJECT LOGO -->
  <h1 align="center">The Opensource Verilog Workbench</h1>

  <p align="center">
    A Verilog workbench with opensource toolchains that lets you write and simulate your verilog code with ease
</p>



<!-- TABLE OF CONTENTS -->
<details open="open">
  <summary><h2 style="display: inline-block">Table of Contents</h2></summary>
  <ol>
    <li>
      <a href="#about-the-project">About The Project</a>
      <ul>
        <li><a href="#built-with">Built With</a></li>
      </ul>
    </li>
    <li>
      <a href="#getting-started">Getting Started</a>
      <ul>
        <li><a href="#prerequisites">Prerequisites</a></li>
        <li><a href="#installation">Installation</a></li>
      </ul>
    </li>
    <li>
      <a href="#usage">Usage</a>
      <ul>
        <li><a href="#verilog-work-bench-cli">Verilog Work Bench CLI</a></li>
        <li><a href="#cli-reference">CLI Reference</a></li>
        <li><a href="#bundled-example">Bundled Example</a></li>
        <li><a href="#cicd">CI/CD</a></li>
      </ul>
    </li>
    <li><a href="#license">License</a></li>
  </ol>
</details>



<!-- ABOUT THE PROJECT -->
## About The Project
This project aims to ease the setup of your verilog projects by providing you with a precoded workbench.
![image](https://user-images.githubusercontent.com/23662796/178709130-ad64a100-0d17-45ab-8561-e9ab9b15baac.png)

### Built With

* `Icarus Verilog (iverilog)` To simulate your verilog code
* `Cocotb` To verify your verilog code by writing python testbenches
* `GTKWave` To view the input or outpot waveforms of your design 
* `Yosys` To synthesize your design into actual hardware and view it
* `SymbiYosys (sby)` To run formal verification configurations
* `nextpnr-ice40` / `nextpnr-gowin` For place and route on iCE40 or Gowin FPGAs
* `IceStorm (icepack)` To pack iCE40 bitstreams
* `Apicula (gowin_pack)` To pack Gowin bitstreams
* `openFPGALoader` To flash FPGA boards



<!-- GETTING STARTED -->
## Getting Started

To get a local copy up and running follow these simple steps.

### Prerequisites


Ubuntu 26.04 LTS:
```
$ sudo apt update && sudo apt upgrade && sudo apt install iverilog yosys gtkwave verilator graphviz librsvg2-bin nodejs npm geeqie git make python3 python3-pip libpython3-dev nextpnr-ice40 nextpnr-gowin fpga-icestorm openfpgaloader boolector z3
$ pip install --break-system-packages cocotb==1.7.2 apycula click && printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.bashrc" && export PATH="$HOME/.local/bin:$PATH"
$ sudo npm install -g netlistsvg
$ git clone https://github.com/YosysHQ/sby.git /tmp/sby && git -C /tmp/sby checkout --detach fea6e467d067b3ea84b6b5ac08cd48beb59f0d42 && sudo make -C /tmp/sby install && rm -rf /tmp/sby
```

### Installation

1. Clone the repo
   ```sh
   git clone https://github.com/sfmth/verilog-workbench/ && cd verilog-workbench
   ```

2. Check the toolchain and run the bundled example:
   ```sh
   ./vwb.py --src-dir examples/src --test-dir examples/test doctor
   ./vwb.py --src-dir examples/src --test-dir examples/test test encoder --waves
   ```


<!-- USAGE EXAMPLES -->
## Usage
The root `src/` and `test/` directories contain no designs or test cases; their
only tracked files keep the directories and Python package available for a new
checkout. Put your Verilog and testbench files there. The bundled reference
project and regression suite live under `examples/src/` and `examples/test/`.

The legacy Makefile workflow expected one module per same-named source file and
required conditional waveform dump snippets. The `vwb.py` workflow below
discovers declarations and generates waveform dump logic automatically, so
source modules do not need `$dumpfile`, `$dumpvars`, or a `COCOTB_SIM`
conditional block.

### Verilog Work Bench CLI

`vwb.py` discovers Verilog/SystemVerilog module declarations under `src/` and
their tests under `test/`. Source file names do not have to match module names,
and only the selected module hierarchy is compiled.

Python tests must contain at least one `@cocotb.test()` and normally use the
name `test_<module>.py`. HDL testbenches can use `test_<module>.v`,
`test_<module>.sv`, `tb_<module>.*`, or `<module>_tb.*`.

```sh
./vwb.py doctor                         # Check installed tools
./vwb.py list                           # Show modules, dependencies, and tests
./vwb.py test                           # Run every discovered test
./vwb.py test my_module --waves          # Run one module and generate an FST
./vwb.py wave my_module                  # Run and open GTKWave
./vwb.py wave my_module --tag known-good # Archive a passing waveform
./vwb.py wave --load known-good          # Reopen it without simulating
./vwb.py lint my_module
./vwb.py synth my_module                 # Generate and open a PNG schematic
./vwb.py fpga my_module --board ice40 --stage pack
./vwb.py clean all
```

Use the global directory options before the command for a different project
layout. For example, `./vwb.py --src-dir rtl --test-dir verification test`.
To save those paths once, initialize the project configuration:

```sh
./vwb.py init --root . --src-dir rtl --test-dir verification --build-dir .vwb
```

This creates `.vwb.json` in the project root. Later commands discover it from
the current directory or any child directory, while explicit global directory
options remain one-command overrides.

### CLI Reference

The command line has this form:

```text
./vwb.py [GLOBAL OPTIONS] MODE [MODE OPTIONS]
```

Global options must appear before `MODE`. Run `./vwb.py --help` for the command
list or `./vwb.py MODE --help` for concise mode-specific syntax. Relative paths
are resolved from `--root` unless a table says otherwise.

#### Modes

| Mode | Alias | Purpose |
| --- | --- | --- |
| `init` | None | Create source/test directories and persist their project configuration. |
| `list` | None | Report discovered packages, modules, dependencies, and tests. |
| `test` | `sim` | Compile and run one or more discovered or explicitly selected tests. |
| `wave` | `gtkwave` | Run and open one waveform, or list and reopen archived waveforms without simulating. |
| `lint` | None | Run Verilator lint on selected module hierarchies. |
| `synth` | None | Synthesize a module with Yosys and optionally render or open the result. |
| `formal` | None | Run a SymbiYosys configuration and optionally open a failure trace. |
| `fpga` | None | Run a supported FPGA pipeline through synthesis, place and route, packing, or flashing. |
| `clean` | None | Remove VWB-owned generated files. |
| `doctor` | None | Report installed tools and the discovered project size. |

#### Global options

| Option | Default | Detailed behavior |
| --- | --- | --- |
| `-h`, `--help` | None | Show top-level help and exit. Every mode also accepts `-h` or `--help` after its name. |
| `--version` | None | Print the `vwb.py` version and exit. |
| `--root PATH` | Config root or directory containing `vwb.py` | Override the project root for one command. Without it, VWB uses the nearest `.vwb.json` found from the current directory upward, then falls back to the directory containing `vwb.py`. |
| `--src-dir DIR` | Config value or `src` | Override the recursively scanned Verilog/SystemVerilog source tree. It may be absolute or relative to the resolved project root and must exist. |
| `--test-dir DIR` | Config value or `test` | Override the recursively scanned Cocotb and Verilog test tree. It may be absolute or relative to the resolved project root and must exist. |
| `--build-dir DIR` | Config value or `.vwb` | Override the generated-artifact directory. VWB adds an ownership marker and refuses unsafe, foreign, or nonempty unowned build directories. |
| `--color {auto,always,never}` | `auto` | Control ANSI colors in human reports. `auto` colors terminals unless `NO_COLOR` is set or `TERM=dumb`; JSON output never contains color codes. |
| `-v`, `--verbose` | Off | Print each external command before running it and print paths selected for cleanup. |
| `--dry-run` | Off | Validate discovery and input paths and print planned commands without executing tools or creating, replacing, or deleting build artifacts. |

Source discovery reads `.v` and `.sv` files recursively. Test discovery accepts
`test_<module>`, `tb_<module>`, `<module>_test`, `<module>_tb`, and
`test_<module>_<suffix>` file stems. A Python file is runnable only when it
contains a Cocotb test decorator. When a mode needs one module and none is
given, VWB selects a unique tested root or the only tested DUT; ambiguous
projects must name the module explicitly.

#### `init`

```text
./vwb.py init [--root PATH] [--src-dir DIR] [--test-dir DIR]
              [--build-dir DIR] [--force]
```

| Option | Default | Detailed behavior |
| --- | --- | --- |
| `--root PATH` | Current directory | Select the project root and location of the new `.vwb.json`. For `init`, directory options may appear after the mode as shown or as global options before it. |
| `--src-dir DIR` | `src` | Save the source directory and create it when missing. |
| `--test-dir DIR` | `test` | Save the test directory, create it when missing, and add an empty `__init__.py` when absent so Cocotb tests are importable. |
| `--build-dir DIR` | `.vwb` | Save the artifact directory without creating it. Normal commands create it later with the VWB ownership marker. |
| `--force` | Off | Replace an existing `.vwb.json`. Without this flag, `init` refuses to overwrite saved configuration. |

The file stores a version plus source, test, and build paths; its parent is the
project root, so moving the complete project keeps relative paths valid.
`./vwb.py --dry-run init ...` validates and reports the destination without
creating directories or writing configuration.

#### `list`

```text
./vwb.py list [--json]
```

| Option | Default | Detailed behavior |
| --- | --- | --- |
| `--json` | Off | Emit structured JSON instead of the human-readable report. Both forms include packages and their files, modules and their source files, dependencies, associated test kind/path/top, and source files without module declarations. |

`list` only performs discovery and does not create build artifacts.

#### `test` / `sim`

```text
./vwb.py test [OPTIONS] [MODULE ...]
./vwb.py sim  [OPTIONS] [MODULE ...]
```

| Option | Default | Detailed behavior |
| --- | --- | --- |
| `MODULE ...` | Every discovered runnable test | Restrict the run to tests associated with one or more DUT modules. Every named module must have a test matching `--test-language`. |
| `--test FILE` | Use discovery | Run one explicit `.py`, `.v`, or `.sv` test. At most one positional module may be supplied. Without a module, VWB infers the DUT from the file name. A Python test must have an importable module name under the project or configured test tree and contain a Cocotb test decorator. |
| `--test-language {auto,cocotb,verilog}` | `auto` | Select both test languages, Cocotb only, or Verilog/SystemVerilog testbenches only. With `--test`, a non-`auto` language must agree with the file type. This replaces the removed `--kind` option. |
| `--test-top MODULE` | Infer from HDL testbench | Override the HDL testbench top. It applies only when exactly one HDL test is selected and is invalid for a Cocotb test. |
| `--testcase NAME` | All Cocotb testcases | Run one named Cocotb testcase. The selection must be Cocotb-only; an `auto` selection containing any HDL test is rejected. |
| `--seed INTEGER` | Cocotb chooses | Set the Cocotb/Python random seed. The selection must be Cocotb-only; an `auto` selection containing any HDL test is rejected. |
| `--waves` | Off | Generate a waveform without opening GTKWave. VWB injects dump logic into temporary preprocessed sources, so the design does not need `$dumpfile`, `$dumpvars`, or a simulator conditional. |
| `--wave-format {fst,vcd}` | `fst` | Select the generated waveform format. This has an effect only with `--waves`; `wave` enables waves automatically. |
| `--max-array-words COUNT` | `32` | Limit the number of words registered for each static unpacked array during waveform generation. `0` removes the limit. Negative values and values that do not fit in 128 bits are rejected. This option has an effect only when waves are enabled. |
| `-D NAME[=VALUE]`, `--define NAME[=VALUE]` | None | Add a repeatable Icarus preprocessor definition to preprocessing and compilation. Supply the option once per definition. |
| `-I DIR`, `--include DIR` | None | Add a repeatable include directory. VWB already includes the source/test roots and nested directories containing HDL or header files. |
| `--compile-arg ARG` | None | Pass a repeatable raw argument to Icarus preprocessing and compilation. Use `--compile-arg=-option` when the value begins with `-`. |
| `--sim-arg ARG` | None | Pass a repeatable raw `vvp` option before the compiled simulation image. Use `--sim-arg=-option` when needed. |
| `--plusarg ARG` | None | Add a repeatable Verilog runtime plusarg after the simulation image. VWB adds the leading `+` when omitted. |
| `--keep-going` | Off | Continue running the remaining selected test specifications after a compile or runtime failure. Without it, the run stops at the first failure. |

Each run recreates
`.vwb/sim/<dut>/<kind>-<test-stem>/`. The directory contains the compiled
`sim.vvp`, command file, Cocotb `results.xml` when applicable, temporary
waveform instrumentation, and the requested `<dut>.fst` or `<dut>.vcd`.
Cocotb passes only when the simulator exits successfully and `results.xml`
contains testcases without failures or errors.

#### `wave` / `gtkwave`

```text
./vwb.py wave    [TEST OPTIONS] [WAVE OPTIONS] [MODULE ...]
./vwb.py gtkwave [TEST OPTIONS] [WAVE OPTIONS] [MODULE ...]
```

`wave` accepts every option in the `test` table, with these differences:

| Option | Default | Detailed behavior |
| --- | --- | --- |
| `MODULE ...` | Automatically selected top | A normal run must resolve to exactly one runnable test. If a module has both Cocotb and Verilog tests, use `--test-language` or `--test` to disambiguate it. With `--list-saved`, modules filter the report; with `--load`, one module may verify the saved trace's DUT. |
| `--waves` | Always on | Accepted for consistency with `test`, but waveform generation cannot be disabled in this mode. |
| `--keep-going` | Forced off | Accepted by the shared parser but has no effect because `wave` runs exactly one test and always stops on failure. |
| `--save FILE` | Automatic build layout | Load an explicit existing GTKWave save file. Without it, GTKWave uses `<dut>.gtkw` beside the waveform in the simulation build directory. A legacy `<root>/<dut>.gtkw` is copied there once when no build layout exists. |
| `--tag NAME` | None | After a successful simulation, copy the waveform, metadata, and current GTKWave layout into `.vwb/saved-waves/NAME/`. Tags must start with a letter or digit and may then contain letters, digits, dots, underscores, or hyphens. Failed simulations are never archived. |
| `--replace-tag` | Off | Allow `--tag` to replace an existing saved tag. It is invalid without `--tag`. |
| `--load NAME` | None | Skip compilation and simulation and open the self-contained archived waveform. This is mutually exclusive with simulation selectors and `--tag`. |
| `--list-saved` | Off | List saved tags, DUTs, test languages, formats, and creation times without running a simulation. Optional positional modules filter by DUT. |
| `--json` | Off | Emit `--list-saved` as ANSI-free JSON. It is invalid without `--list-saved`. |

GTKWave is opened only after the selected test passes and the waveform file is
confirmed to exist. By default it is launched with `--autosavename` and its
working directory set to `.vwb/sim/<dut>/<kind>-<test-stem>/`. GTKWave therefore
loads the existing `<dut>.gtkw` automatically, and **File > Write Save File**
writes back to that build directory. An explicit `--save FILE` uses GTKWave's
`--save` option instead. VWB preserves `.gtkw` files while recreating the other
simulation outputs, so the signal layout opens again on the next run.

`clean sim` removes live simulations and their layouts but keeps tagged waves.
`clean waves` removes only tagged archives; `clean all` removes both.

#### `lint`

```text
./vwb.py lint [OPTIONS] [MODULE ...]
```

| Option | Default | Detailed behavior |
| --- | --- | --- |
| `MODULE ...` | DUTs with discovered tests | Lint one or more explicitly named module hierarchies. Explicit modules do not need associated tests. |
| `--all` | Off | Lint every discovered source module, including untested modules. It cannot be combined with positional modules. |
| `--keep-going` | Off | Continue with remaining modules after a failed lint run. |
| `-D VALUE`, `--define VALUE` | None | Add a repeatable Verilator preprocessor definition. |
| `-I DIR`, `--include DIR` | None | Add a repeatable include directory in addition to recursively detected source/test include directories. |
| `--lint-arg ARG` | None | Pass a repeatable raw Verilator argument. Use `--lint-arg=-option` when the value begins with `-`. |

Each module and its dependency closure is checked with Verilator
`--lint-only -Wall`. The mode prints a pass/fail summary and creates no
persistent artifact.

#### `synth`

```text
./vwb.py synth [OPTIONS] [MODULE]
```

| Option | Default | Detailed behavior |
| --- | --- | --- |
| `MODULE` | Automatically selected top | Select the source module to synthesize. Name it explicitly when VWB cannot infer one unique tested top. |
| `--format {json,svg,png,dot}` | `png` | Select the final reported artifact independently of `--full` and schematic rendering. JSON is always generated. |
| `--full` | Off | Select the corresponding full Makefile flow. With NetlistSVG this changes `prep` to `prep -flatten`; with Yosys `show` it changes `proc; opt -full` to `synth -top`. |
| `--flatten` | Off | Run `flatten; opt_clean` after the selected flow. The full NetlistSVG flow is already flattened, so this flag is redundant for that one combination. |
| `--schematic`, `--schemetic` | On | Engage `netlistsvg` using the generated Yosys JSON. Both spellings are accepted. SVG is used directly; PNG is rasterized with `rsvg-convert`, avoiding ImageMagick delegate and policy differences. JSON and DOT remain selectable and receive an SVG sidecar while schematic rendering is enabled. |
| `--no-schematic`, `--no-schemetic` | Off | Disable `netlistsvg`. JSON is written directly, while SVG, PNG, and DOT use Yosys `show` with the Makefile's `-colors 2 -width -signed` options and Graphviz. |
| `--view VIEWER` | `geeqie` | Open the requested artifact with this executable after successful generation. Use `--view none` or `--no-view` for CI, Docker smoke tests, SSH sessions, or other headless use. |
| `--no-view` | Off | Equivalent to `--view none`. |
| `-D VALUE`, `--define VALUE` | None | Add a repeatable definition to Yosys `read_verilog -sv`. |
| `-I DIR`, `--include DIR` | None | Add a repeatable include path to Yosys in addition to detected include directories. |

All combinations of full/non-full, schematic/non-schematic, and output format
are supported. NetlistSVG follows the Makefile's `show_synth_human` preparation
with `prep`, or `prep -flatten` under `--full`. Yosys `show` follows the
`show_synth` preparation with `proc; opt -full`, or `synth -top` under
`--full`. The default `./vwb.py synth MODULE` creates a NetlistSVG schematic as
PNG and opens it in `geeqie`. The generated
script, JSON netlist, sidecars, and requested rendering are stored under
`.vwb/synth/<module>/`; the final artifact path is printed on success.

#### `formal`

```text
./vwb.py formal [--view] [CONFIG]
```

| Option | Default | Detailed behavior |
| --- | --- | --- |
| `CONFIG` | Auto-detect one `.sby` | Select a SymbiYosys configuration by absolute path or a path relative to `--root`. When omitted, VWB searches recursively outside `.git` and the build directory and requires exactly one match. |
| `--view` | Off | After a successful run, open the first sorted VCD trace under the result directory with GTKWave. The command fails if no VCD trace was generated. |

The mode runs `sby -f`, replaces its output at
`.vwb/formal/<config-stem>/`, and prints that directory on success.

#### `fpga`

```text
./vwb.py fpga [OPTIONS] [MODULE] --board BOARD
```

| Option | Default | Detailed behavior |
| --- | --- | --- |
| `MODULE` | Automatically selected top | Select the FPGA top module. Name it explicitly when VWB cannot infer one unique tested top. |
| `--board {gowin,tangnano9k,ice40,icebreaker}` | Required | Select a fixed toolchain/device profile. `tangnano9k` maps to `gowin`; `icebreaker` maps to `ice40`. |
| `--stage {synth,pnr,pack,flash}` | `pack` | Run the cumulative pipeline through synthesis, place and route, bitstream packing, or device flashing. |
| `--constraints FILE` | Board-family default | Select a constraints file relative to `--root` or by absolute path. The default is `<src-dir>/io.cst` for Gowin or `<src-dir>/io.pcf` for iCE40. A valid file is currently required for every stage, including `synth`. |
| `-D VALUE`, `--define VALUE` | None | Add a repeatable Yosys definition. VWB also always supplies `LEDS_NR=6`. |
| `-I DIR`, `--include DIR` | None | Add a repeatable Yosys include directory in addition to detected include directories. |

The supported board profiles are fixed:

| Board selection | Yosys/nextpnr target | Place-and-route output | Packed output | Flash target |
| --- | --- | --- | --- | --- |
| `gowin`, `tangnano9k` | Gowin `GW1NR-LV9QN88PC6/I5`, family `GW1N-9C` | `<module>-pnr.json` | `<module>.fs` | `openFPGALoader -b tangnano9k` |
| `ice40`, `icebreaker` | iCE40 UP5K, package `sg48` | `<module>.asc` | `<module>.bin` | `openFPGALoader -b ice40_generic` |

Artifacts are stored under
`.vwb/fpga/<normalized-board>/<module>/`. `synth` returns the Yosys JSON,
`pnr` returns the place-and-route file, `pack` returns the bitstream, and
`flash` returns the packed bitstream after programming the device.

#### `clean`

```text
./vwb.py clean [{sim,waves,synth,fpga,formal,all}]
```

| Argument | Default | Detailed behavior |
| --- | --- | --- |
| `scope` | `all` | Remove only `.vwb/sim`, `.vwb/saved-waves`, `.vwb/synth`, `.vwb/fpga`, `.vwb/formal`, or the entire configured build directory. |

Cleanup proceeds only when the build directory contains the matching VWB
ownership marker. It refuses source/test directories, project roots, ancestor
directories, unowned nonempty paths, and build directories owned by another
project. `--dry-run` prints the selected removal without deleting it.

#### `doctor`

```text
./vwb.py doctor [--json]
```

| Option | Default | Detailed behavior |
| --- | --- | --- |
| `--json` | Off | Emit tool groups whose command values are executable paths or `null`. The text form also prints discovered module and runnable-test counts. |

`doctor` reports the simulation, waveform, lint, synthesis, formal, Gowin, and
iCE40 tools. Its exit status requires `iverilog` and `vvp`, plus
`cocotb-config` when Cocotb tests are discovered. Missing optional tools are
reported but do not make `doctor` fail; the relevant mode will require them
when used. Synthesis reporting includes Yosys, Graphviz `dot`, `netlistsvg`,
`rsvg-convert`, and `geeqie` for the selectable rendering pipeline.

#### Exit status

| Status | Meaning |
| --- | --- |
| `0` | The requested operation succeeded. |
| `1` | A test, waveform run, lint run, or required `doctor` check failed. |
| `2` | Arguments, project input, configuration, or a required external tool were invalid. |
| `130` | The command was interrupted with Ctrl-C. |

### Bundled Example

The same CLI runs the moved example without copying it into the user workspace:

```sh
./vwb.py --src-dir examples/src --test-dir examples/test list
./vwb.py --src-dir examples/src --test-dir examples/test test --keep-going
./vwb.py --src-dir examples/src --test-dir examples/test test array_example --waves --wave-format vcd
./vwb.py --src-dir examples/src --test-dir examples/test synth encoder --format json --view none
```

The legacy Makefile also defaults to this example. Point it at the user
workspace with `make SRC_DIR=src TEST_DIR=test NAM=my_module`.

Generated simulation, synthesis, formal, and FPGA files are stored under
`.vwb/`. Run `./vwb.py <command> --help` for command-specific options.

When waves are requested, `vwb.py` preprocesses the selected hierarchy and
instruments a temporary copy under `.vwb/`. It explicitly registers every word
of standalone static unpacked arrays and memories declared in modules or interfaces,
including parameterized, multidimensional, generated, and typedef-backed
arrays. Project source files remain unchanged. Icarus may report harmless
escaped-identifier warnings for these words.

Array dumping is limited to 32 words per declaration by default. Change
the limit with `--max-array-words COUNT`, or use `0` for no limit. Dynamic,
procedural, and aggregate-member arrays cannot be registered statically and
produce a clear error.

### CI/CD

The CI workflow runs the Python regression suite, every bundled Cocotb and HDL
test, and an encoder synthesis check on pushes and pull requests. Version tags
matching `v*` publish the checked-out repository as a Docker image at
`ghcr.io/sfmth/verilog-workbench`; the publish workflow can also be started
manually from GitHub Actions.


<!-- LICENSE -->
## License

Distributed under the MIT License. See `LICENSE` for more information.
