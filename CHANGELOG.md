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
- Added RTL and default post-synthesis gate-level simulation using the same
  testbench. Use `--no-gate-level` to skip the gate-level check.
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

- Renamed the old `show` operation to `synth` and kept the synthesis commands
  aligned with the Makefile flows.
- Replaced `--kind` with `--test-language`; Cocotb is the default generated test
  language and `--max-array-words` now defaults to 32.
- Synthesis now defaults to the compact flow with schematics enabled, PNG output,
  and Geeqie viewing. `--full`, `--no-schematic`, `--format`, and `--view` control
  those choices independently.
- PNG schematics now use a higher rendering density and an opaque white
  background.
- Plain `clean` removes simulation temporaries and lint work while preserving
  synthesis, saved waveforms, FPGA results, and formal results. Destructive
  cleanup requires an explicit scope.
- Moved bundled designs and tests under `examples/`; root `src/` and `test/`
  remain available for user projects.
- Reworked the README around novice logic-design vocabulary and added detailed
  command option tables and guided examples.

### Fixed

- Simulation, lint, and validation batches now continue after individual
  failures and report all failures together.
- Large NetlistSVG inputs now use bounded rendering and fall back to the Yosys
  schematic path when NetlistSVG cannot render the design.
- Fixed escaped HDL identifiers across discovery, generated starters, Cocotb,
  Icarus, Yosys, waveform instrumentation, and artifact names.
- Fixed Yosys rendering command quoting while preserving safe module selection.
- Permanent Python user-bin PATH setup replaces temporary shell-only exports in
  the Docker and native setup instructions.

### CI/CD

- GitHub Actions now builds the Docker image and discovers all example designs
  and tests dynamically instead of maintaining a design-name list.
- Added software regression tests, generated-starter integration checks, both
  waveform formats, language coverage, lint and synthesis coverage, option
  contract checks, and CI-safe dry runs for GUI and hardware-only operations.
- Added a synthesis option matrix and artifact validation to both CI and release
  workflows so future examples are included automatically.
