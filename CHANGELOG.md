# Changelog

All notable changes to Verilog Workbench are recorded here.

## [0.2.0] - 2026-07-17

### Added

- Added `vwb.py`, a command-line workbench that discovers HDL source files,
  design units, dependencies, Cocotb tests, and HDL testbenches automatically.
- Added Verilog, SystemVerilog, and VHDL design support, including sv2v and GHDL
  conversion paths plus beginner examples for each language.
- Added automatic Cocotb starter tests for units without tests. Starters detect
  common clock and reset ports, initialize inputs, and run the design briefly.
- Added RTL and post-synthesis gate-level simulation using the same testbench.
  Both `test` and `wave` run RTL by default and accept `--gate-level` to enable
  the slower post-synthesis check.
- Added source-independent FST and VCD generation, including bounded dumping of
  static arrays. HDL source files no longer need conditional `$dump*` blocks.
- Added saved waveform tags, saved GTKWave layouts, automatic layout reuse, and
  commands for listing and reopening known-good waves.
- Added aggregate linting with Icarus, Verilator, Yosys, Verible, and GHDL.
- Added `setup.sh` for a permanent local Ubuntu/Debian installation, including
  the `vwb` command, PATH setup, and terminal Tab completion.
- Added project configuration through `vwb init`, shell colors, machine-readable
  reports, FPGA flows, formal checks, and shell completion callbacks.
- Added real terminal Tab completion for commands, options, module/entity names,
  and saved wave tags. Docker enables Bash completion automatically through the
  stable `vwb` command; native Bash, Zsh, and Fish setup is documented.

### Changed

- Renamed the old `show` operation to `synth`. The default makes a compact
  circuit drawing, while `--full` exposes more internal logic.
- Replaced `--kind` with `--test-language`; Cocotb is the default generated test
  language and `--max-array-words` now defaults to 32.
- Synthesis now defaults to the compact flow with schematics enabled, preferred
  PNG output, and artifact-aware viewing. Geeqie opens PNG files and Inkscape
  opens SVG files. `--full`, `--no-schematic`, `--format`, and `--view` control
  those choices independently.
- Removed the misspelled `--schemetic` and `--no-schemetic` option aliases so
  shell completion presents only the canonical schematic options.
- PNG schematics now use a higher rendering density and an opaque white
  background. A PNG that would require density reduction is returned as SVG
  instead.
- Plain `clean` removes simulation temporaries and lint work while preserving
  synthesis, saved waveforms, FPGA results, and formal results. Destructive
  cleanup requires an explicit scope.
- Moved bundled designs and tests under `examples/`; root `src/` and `test/`
  remain available for user projects.
- Reworked every CLI help screen in plain logic-design language and replaced
  confusing internal descriptions with direct explanations of each option.
- Source simulation is now the default for both `test` and `wave`. Gate-level
  simulation is an opt-in extra check enabled with `--gate-level`.
- External tools are quiet by default. Successful tool transcripts are hidden,
  failures show a short diagnostic tail, and `--verbose` shows full output.
- Local setup now supports Ubuntu 24.04 and later, Debian, Fedora, Arch, and
  related distributions. It installs a small core tool set by default, offers
  advanced tools through `setup.sh --full`, asks the system package manager
  first, and uses `paru` or `yay` as the Arch AUR fallback.
- Cocotb and Argcomplete are no longer tied to one exact release. Distribution
  packages are preferred; an isolated user environment accepts compatible
  release ranges when those packages are unavailable. Generated tests and the
  simulator environment work with both Cocotb 1.x and 2.x names.
- Removed the Python development headers and direct pinned binary/source
  installers from the normal local setup. Missing optional tools now stay
  optional instead of making the core install fail.
- `doctor` now marks only the tools needed by the current project as required,
  labels the rest as optional, and gives local and Docker installation steps.
- Focused the README on the introduction, features, guided first project,
  project layout, language support, and Docker/native installation. Exact
  option details now live in `vwb.py COMMAND --help` instead of duplicate
  command tables.

### Fixed

- Relative `--src-dir`, `--test-dir`, and `--build-dir` paths now use the
  directory where `vwb` was started when no saved project root is present.
- Discovery now reports duplicate and unterminated declarations per design
  unit without hiding healthy modules or aborting `list`. Inactive
  preprocessor branches and VHDL strings no longer create phantom designs.
- Interfaces and Verilog primitives appear in `list`, interface dependencies
  require real interface syntax, and Verilog test filenames match module names
  without case surprises.
- Generated Cocotb and SystemVerilog starters initialize unpacked array inputs,
  recognize common suffixed clocks and active-low resets, avoid reset-name false
  positives, and explain parameters that need an explicit value.
- Bundled Tang Nano 9K and iCEBreaker constraint files now match the bundled
  `validation_fpga` design. CI place-routes and packs it with both default files.
- `clean` works even after source or test folders are removed, reports accurate
  dry-run actions, ignores missing scope folders, and strictly validates legacy
  ownership markers.
- Simulation, lint, and validation batches now continue after individual
  failures and report all failures together.
- NetlistSVG is now tried for every schematic request regardless of design
  size. The real full-netlist Yosys schematic is used only after NetlistSVG
  actually fails, times out, is unavailable, or returns invalid SVG; synthesis
  never substitutes a port table or smaller overview.
- Yosys lint now prints only warnings and errors in the terminal while saving
  its complete transcript under the lint build directory.
- Lint now runs every available suitable checker and skips missing optional
  backends. Result summaries separate HDL failures from setup limitations.
- Fixed escaped HDL identifiers across discovery, generated starters, Cocotb,
  Icarus, Yosys, waveform instrumentation, and artifact names.
- Fixed Yosys rendering command quoting while preserving safe module selection.
- Permanent Python user-bin PATH setup replaces temporary shell-only exports in
  the Docker and native setup instructions.

### CI/CD

- GitHub Actions runs local-install smoke tests concurrently on Ubuntu 24.04,
  Debian stable, current Fedora, and rolling Arch, then tests the documented
  encoder flow with the tools installed by each distribution path.
- GitHub Actions builds the Docker image and audits the complete discovered
  example inventory, including every Python test source.
- Added software regression tests, generated-starter integration checks, both
  waveform formats, language coverage, lint and synthesis coverage, option
  contract checks, and CI-safe dry runs for GUI and hardware-only operations.
- Tool-heavy simulation, wave, lint, synthesis, and FPGA matrices now use a
  reviewed ten-module profile covering all supported languages, both discovered
  test styles, generated starters, large hierarchies, arrays, interfaces,
  packages, and split-file VHDL.
- CI and release perform one real PNG synthesis for each representative module,
  plus focused default-renderer and forced-fallback PNG fixtures. Alternate
  formats and switch combinations use parser tests or dry runs instead of
  repeating expensive synthesis work.
- FPGA option probes cover each board and stage once, and actual FPGA synthesis
  rotates one representative design per board before the board-specific pack
  fixtures. This replaces redundant module-by-board Cartesian products.
