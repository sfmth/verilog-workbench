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
  `test` enables the gate check by default and accepts `--no-gate-level`;
  `wave` runs RTL by default and accepts `--gate-level` to enable it.
- Added source-independent FST and VCD generation, including bounded dumping of
  static arrays. HDL source files no longer need conditional `$dump*` blocks.
- Added saved waveform tags, saved GTKWave layouts, automatic layout reuse, and
  commands for listing and reopening known-good waves.
- Added aggregate linting with Icarus, Verilator, Yosys, Verible, and GHDL.
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
- Focused the README on the introduction, features, guided first project,
  project layout, language support, and Docker/native installation. Exact
  option details now live in `vwb.py COMMAND --help` instead of duplicate
  command tables.

### Fixed

- Simulation, lint, and validation batches now continue after individual
  failures and report all failures together.
- NetlistSVG is now tried for every schematic request regardless of design
  size. The real full-netlist Yosys schematic is used only after NetlistSVG
  actually fails, times out, is unavailable, or returns invalid SVG; synthesis
  never substitutes a port table or smaller overview.
- Yosys lint now prints only warnings and errors in the terminal while saving
  its complete transcript under the lint build directory.
- Fixed escaped HDL identifiers across discovery, generated starters, Cocotb,
  Icarus, Yosys, waveform instrumentation, and artifact names.
- Fixed Yosys rendering command quoting while preserving safe module selection.
- Permanent Python user-bin PATH setup replaces temporary shell-only exports in
  the Docker and native setup instructions.

### CI/CD

- GitHub Actions builds the Docker image and audits the complete discovered
  example inventory, including every Python test source.
- Added software regression tests, generated-starter integration checks, both
  waveform formats, language coverage, lint and synthesis coverage, option
  contract checks, and CI-safe dry runs for GUI and hardware-only operations.
- Tool-heavy simulation, wave, lint, synthesis, and FPGA matrices now use a
  reviewed ten-module profile covering all supported languages, both discovered
  test styles, generated starters, large hierarchies, arrays, interfaces,
  packages, and split-file VHDL.
- Added a synthesis option matrix and artifact validation to both CI and release
  workflows for every module in that representative profile.
