# Native toolchain CI observations

## Sixth run: all native companion bundles pass; HDL package gaps remain

[Run 39153500063](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063)
tested commit `831ae23f8f10ebc89c09ed3b3a4cae9a0bbebe2e` on 2026-10-18.
All six native companion jobs and actual browser integration passed. Every
toolchain job completed and repeated the CI5 results below. The overall gate
remains **failed** because four required targets still lack HDL packages.

| Toolchain host | Actual checks passed | Remaining package gaps |
| --- | --- | --- |
| [Linux x64](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063/job/117751810181) | 14/14 | None in this smoke gate. |
| [Linux ARM64](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063/job/117751810195) | 11/14 | Three VHDL workflows blocked by GHDL. |
| [macOS Intel](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063/job/117751810229) | 11/14 | Three VHDL workflows blocked by GHDL. |
| [macOS Apple Silicon](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063/job/117751810276) | 14/14 | Requires the explicit external native Tcl-enabled Homebrew Yosys overlay. |
| [Windows x64](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063/job/117751810265) | 9/14 | Five workflows blocked by Cocotb, GHDL and Graphviz. |
| [Windows ARM64](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063/job/117751810211) | 9/14 | The same five package gaps; suite tools use OS x64 translation. |
| [Arch x64 userspace](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063/job/117751810311) | 14/14 | GCC provisioned; development-container evidence. |

The corrected Windows unit fixtures now permit both
[Windows x64](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063/job/117751810321)
and [Windows ARM64](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063/job/117751810292)
native companion bundles to build and pass their actual local-project smoke
checks. Native runtime success does not fill the separate HDL package gaps.
CI5's retained Apple Silicon reports remain the durable detailed evidence for
the unchanged production code. Clean-machine installation and physical FPGA
programming are still unverified.

## Fifth run: macOS process fixes verified

[Run 39153200306](https://github.com/sfmth/verilog-workbench/actions/runs/39153200306)
tested commit `d136b894138213d1aa6483bf1d9b16750f591cd0` on 2026-10-18.
All seven toolchain jobs finished and retained diagnostics. The macOS EPERM
regression is resolved in these actual runs.

| Host | Passed checks | Actual failed or blocked checks |
| --- | --- | --- |
| [Linux x64](https://github.com/sfmth/verilog-workbench/actions/runs/39153200306/job/117750929232) | 14/14 | None in this smoke gate. |
| [Linux ARM64](https://github.com/sfmth/verilog-workbench/actions/runs/39153200306/job/117750929247) | 11/14 | Three workflows blocked by missing GHDL: native VHDL, Cocotb/VHDL and VHDL synthesis. |
| [macOS Intel](https://github.com/sfmth/verilog-workbench/actions/runs/39153200306/job/117750929249) | 11/14 | The same three GHDL package gaps; no selected workflow fails after execution. |
| [macOS Apple Silicon](https://github.com/sfmth/verilog-workbench/actions/runs/39153200306/job/117750929273) | 14/14 | None with the explicit external native Tcl-enabled Homebrew Yosys overlay. |
| [Windows x64](https://github.com/sfmth/verilog-workbench/actions/runs/39153200306/job/117750929326) | 9/14 | Five workflows blocked: Cocotb/Verilog needs `cocotb-config`; native VHDL and VHDL synthesis need GHDL; Cocotb/VHDL needs both; SVG needs Graphviz `dot`. |
| [Windows ARM64](https://github.com/sfmth/verilog-workbench/actions/runs/39153200306/job/117750929150) | 9/14 | The same five package gaps; suite tools execute through permitted OS x64 translation. |
| [Arch x64 userspace](https://github.com/sfmth/verilog-workbench/actions/runs/39153200306/job/117750929185) | 14/14 | None with native GCC provisioned; this development-container check does not certify clean installation. |

The durable Apple Silicon [workflow report](m0-evidence/toolchains-ci5-darwin-arm64.json)
records real executed HDL/Cocotb tests, waveforms, lint, Verilog/VHDL synthesis,
SVG, formal proof and counterexample, and both FPGA pack flows. Its
[acquisition receipt](m0-evidence/toolchain-acquisition-ci5-darwin-arm64.json)
records exact suite integrity, the original Yosys launcher backup and the native
ARM64 external executable/hash. Both reports were extracted from
[artifact 12146081249](https://github.com/sfmth/verilog-workbench/actions/runs/39153200306/artifacts/12146081249),
whose ZIP digest is
`99de96a8c9bfd94a2757133db9b760337161f396414d90112579501c92865f30`.
The Homebrew version remains `Yosys 0.70+post`; this external prerequisite and
its transitive libraries are not a fully pinned portable managed distribution.
Physical FPGA programming and clean-machine installation remain unverified.

Native companion bundles were created and exercised successfully on
[Linux x64](https://github.com/sfmth/verilog-workbench/actions/runs/39153200306/job/117750929098),
[Linux ARM64](https://github.com/sfmth/verilog-workbench/actions/runs/39153200306/job/117750929277),
[macOS Intel](https://github.com/sfmth/verilog-workbench/actions/runs/39153200306/job/117750929343)
and [macOS Apple Silicon](https://github.com/sfmth/verilog-workbench/actions/runs/39153200306/job/117750929235).
Both Windows native companion jobs stopped at new Darwin-specific unit
fixtures using the unavailable Windows `signal.SIGKILL` constant; they did not
reach bundle smoke. A fixture correction awaits CI6, so the Windows companion
bundle smoke gate remains pending. The
[actual browser integration job](https://github.com/sfmth/verilog-workbench/actions/runs/39153200306/job/117750928981)
also passed.

## Fourth run: native Yosys candidate and process regression evidence

[Run 39152617957](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957)
tested commit `28aa9dc852c52a9ba559be538bf25ce2a8353e8a` on 2026-10-18.
This is a historical failed gate. Follow-up macOS process cleanup and Windows
bundle smoke fixes require CI5; their results are not assumed here.

| Host | Passed checks | Actual failed or blocked checks |
| --- | --- | --- |
| [Linux x64](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179373) | 14/14 | None in this smoke gate. |
| [Linux ARM64](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179253) | 11/14 | Three blocked workflows: native VHDL, Cocotb/VHDL and VHDL synthesis require missing GHDL. |
| [macOS Intel](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179306) | 0/14 | Eleven workflow attempts fail with process `Operation not permitted`; the three VHDL workflows remain blocked by missing GHDL. |
| [macOS Apple Silicon](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179181) | 0/14 | All fourteen attempts fail with process `Operation not permitted`; no workflow is blocked by a missing discovered tool. |
| [Windows x64](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179420) | 9/14 | Five blocked workflows: Cocotb/Verilog needs `cocotb-config`; VHDL and VHDL synthesis need GHDL; Cocotb/VHDL needs both; SVG needs Graphviz `dot`. |
| [Windows ARM64](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179312) | 9/14 | The same five missing-package workflows; successful suite tool execution uses the permitted OS x64 translation route. |
| [Arch x64 userspace](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179053) | 14/14 | None with GCC provisioned; this remains a development-container check. |

The Apple Silicon prerequisite now passes the exact formula/bottle digest and
installed-version guards, then executes a real Tcl script successfully. Its
recorded version is `Yosys 0.70+post`, git
`0c11ea098be065e585a90a06e7b7da49936cf231`. The acquisition receipt records
the external native ARM64 Mach-O executable, its hash and the original suite
launcher backup. This proves the separate native Tcl prerequisite and overlay
selection; the failed process gate prevents a claim that HDL workflows pass.
The full retained reports contain the per-workflow EPERM failures even though
the outer acquisition log stops at the same exception. Existing generated
netlists/status files are not counted as passes after an execution error.

Native companion jobs passed and exercised bundles on
[Linux x64](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179322)
and [Linux ARM64](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179366).
Both macOS companion jobs stopped in the process/unit gate with EPERM, before
bundle creation. Both Windows jobs passed unit, legacy CLI and browser-client
checks and built native bundles, then failed the bundle smoke assertion
comparing saved file text at `scripts/smoke_native_bundle.py:65`. They are not
counted as successfully exercised bundles. The
[actual browser integration job](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179298)
passed. Physical FPGA programming remains untested on every host.

## Third run: compiler and Windows lint fixes verified

[Run 39151829476](https://github.com/sfmth/verilog-workbench/actions/runs/39151829476)
tested commit `74b1e3ec2b55f6e9cc1cddb3b45f84192ac8de52` on 2026-10-18.
The following results come from retained native job logs. Apple Silicon's
external Yosys prerequisite failed before acquisition/probing, so it has no
HDL workflow results in this run.

| Host | Passed checks | Remaining failed or blocked checks |
| --- | --- | --- |
| [Linux x64](https://github.com/sfmth/verilog-workbench/actions/runs/39151829476/job/117746885189) | 14/14 | None in this smoke gate; physical programming remains untested. |
| [Linux ARM64](https://github.com/sfmth/verilog-workbench/actions/runs/39151829476/job/117746885276) | 11/14 | Native VHDL, Cocotb/VHDL and VHDL synthesis blocked by missing GHDL. |
| [macOS Intel](https://github.com/sfmth/verilog-workbench/actions/runs/39151829476/job/117746885197) | 10/14 | The same three GHDL gaps; schematics fails with `Operation not permitted` after Yosys reports completed SVG generation. The cause remains under investigation. |
| [macOS Apple Silicon](https://github.com/sfmth/verilog-workbench/actions/runs/39151829476/job/117746885162) | Not run | Exact formula/bottle guards and Homebrew installation succeeded, then the installed-version text guard failed. Actual version output was not retained; no HDL probe ran. |
| [Windows x64](https://github.com/sfmth/verilog-workbench/actions/runs/39151829476/job/117746885253) | 9/14 | Cocotb/Verilog, VHDL, Cocotb/VHDL, SVG and VHDL synthesis blocked by missing Cocotb configuration, GHDL and Graphviz. Native Verilator-engine lint now passes. |
| [Windows ARM64](https://github.com/sfmth/verilog-workbench/actions/runs/39151829476/job/117746885241) | 9/14 | The same five package gaps; the nine passes use permitted OS x64 translation for the suite tools. |
| [Arch x64 userspace](https://github.com/sfmth/verilog-workbench/actions/runs/39151829476/job/117746885023) | 14/14 | Provisioning native GCC resolved the three previous VHDL failures. This development-container check does not certify clean-machine installation. |

The separate native companion bundle jobs passed on
[Linux x64](https://github.com/sfmth/verilog-workbench/actions/runs/39151829476/job/117746885250),
[Linux ARM64](https://github.com/sfmth/verilog-workbench/actions/runs/39151829476/job/117746885243),
[macOS Intel](https://github.com/sfmth/verilog-workbench/actions/runs/39151829476/job/117746885183)
and [macOS Apple Silicon](https://github.com/sfmth/verilog-workbench/actions/runs/39151829476/job/117746885299),
including actual pinned native bundle creation and exercise. Both Windows
native companion jobs passed the new unit checks but stopped at a legacy
expected-path separator assertion before bundle creation. Its fixture fix
requires a further native run. The
[actual browser integration job](https://github.com/sfmth/verilog-workbench/actions/runs/39151829476/job/117746885191)
also passed. These companion/browser outcomes do not fill the HDL package
gaps above.

## Second run: retained diagnoses

[Run 39150641107](https://github.com/sfmth/verilog-workbench/actions/runs/39150641107)
tested commit `2797969c500bfd0c61a73174dc35d7314bc75db0` on 2026-10-18. All seven
toolchain jobs acquired their exact locked archive and retained diagnostic
artifacts. The following are actual native probe results, not inferred causes:

| Host | Passed checks | Remaining failed or blocked checks |
| --- | --- | --- |
| [Linux x64](https://github.com/sfmth/verilog-workbench/actions/runs/39150641107/job/117743398282) | 14/14 | None in this smoke gate; physical programming is still not run. |
| [Linux ARM64](https://github.com/sfmth/verilog-workbench/actions/runs/39150641107/job/117743398324) | 11/14 | Native VHDL, Cocotb/VHDL and VHDL synthesis blocked by missing GHDL. |
| [macOS Intel](https://github.com/sfmth/verilog-workbench/actions/runs/39150641107/job/117743398395) | 11/14 | The same three workflows blocked by missing GHDL. |
| [macOS Apple Silicon](https://github.com/sfmth/verilog-workbench/actions/runs/39150641107/job/117743398301) | 8/14 | Five Yosys synthesis/schematic/FPGA checks fail because the supplied Yosys lacks Tcl `-c`; Cocotb/VHDL cannot load bundled libpython's `libintl.8.dylib` dependency from the generated GHDL executable location. |
| [Windows x64](https://github.com/sfmth/verilog-workbench/actions/runs/39150641107/job/117743398298) | 8/14 | Cocotb/Verilog, VHDL, Cocotb/VHDL, lint, SVG and VHDL synthesis blocked by missing discovered Cocotb configuration, GHDL, Verilator launcher and Graphviz. |
| [Windows ARM64](https://github.com/sfmth/verilog-workbench/actions/runs/39150641107/job/117743398333) | 8/14 | The same six blocked workflows; the eight passes exercise the allowed OS x64 translation route, not ARM64-native tool binaries. |
| [Arch x64 userspace](https://github.com/sfmth/verilog-workbench/actions/runs/39150641107/job/117743398257) | 11/14 | Three VHDL workflows fail explicitly with GHDL `installation problem: cc not found`; LLVM GHDL needs a native C compiler/linker. |

The Windows archive's 16,967 member names were also inspected directly: it
contains `bin/verilator_bin.exe` and a bare Perl `bin/verilator` front-end, but
no Cocotb package/configuration files or Graphviz `dot` executable. Discovery
and lint now accept the native Verilator engine for this lint-only operation;
that fix still needs a native CI rerun. Cocotb, Graphviz and GHDL remain genuine
Windows distribution gaps. Arch's developer test host now provisions GCC;
compiler availability must also be accounted for in native installer planning.
Mac runtime/Yosys integration fixes likewise require another actual run.

The [Linux x64 native bundle job](https://github.com/sfmth/verilog-workbench/actions/runs/39150641107/job/117743398269)
and [Linux ARM64 native bundle job](https://github.com/sfmth/verilog-workbench/actions/runs/39150641107/job/117743398278)
both passed their unit/CLI/browser-client checks, built their pinned runtime
packages and exercised the bundles. Other four native jobs passed the new unit
checks but stopped at existing CLI regression checks before bundle generation.
The browser job also passed. None of these native runtime proofs implies full
HDL support on a row whose toolchain checks remain blocked.

## First run: diagnosis upload gap

[M0 run 39150223882](https://github.com/sfmth/verilog-workbench/actions/runs/39150223882)
executed commit `24d3b5d3c2c929f14e1a72feb59264a67f636b22` on 2026-10-18.
These are observed job outcomes, distinct from package availability and local
development results.

| Toolchain host | Observed workflow job | Interpretation |
| --- | --- | --- |
| Linux x64 | [Passed](https://github.com/sfmth/verilog-workbench/actions/runs/39150223882/job/117742165326) | Acquisition and the real native workflow command exited 0. Its strict gate requires all 14 selected non-programming workflows to pass. |
| Linux ARM64 | [Failed](https://github.com/sfmth/verilog-workbench/actions/runs/39150223882/job/117742165368) | Command exited 1; individual workflow states could not be recovered. |
| macOS Apple Silicon | [Failed](https://github.com/sfmth/verilog-workbench/actions/runs/39150223882/job/117742165358) | Command exited 1; individual workflow states could not be recovered. |
| macOS Intel | [Failed](https://github.com/sfmth/verilog-workbench/actions/runs/39150223882/job/117742165272) | Command exited 1; individual workflow states could not be recovered. |
| Windows x64 | [Failed](https://github.com/sfmth/verilog-workbench/actions/runs/39150223882/job/117742165287) | Command exited 1; individual workflow states could not be recovered. |
| Windows ARM64 | [Failed](https://github.com/sfmth/verilog-workbench/actions/runs/39150223882/job/117742165240) | Command exited 1; individual workflow states could not be recovered. |
| Arch Linux x64 userspace | [Failed](https://github.com/sfmth/verilog-workbench/actions/runs/39150223882/job/117742165179) | Command exited 1 in the development container; this is not native clean-machine installation evidence. |

All seven toolchain upload steps reported no matching files: the reports lived
under hidden `.m0-evidence`, while `upload-artifact` excluded hidden files. The
run artifact inventory contains only browser evidence and Pages source, with no
toolchain artifacts. The acquisition/probe CLI also wrote its diagnosis only to
JSON, so the failed job logs contain the exit code without workflow details.

Therefore the five required-host failures cannot yet be attributed to missing
GHDL versus avoidable acquisition, loader, interpreter or tool errors. Known
package gaps remain documented, but are not substituted for observed failure
causes. No failed host is marked supported, and no individual failed-host
workflow is inferred to have passed.

Follow-up changes make the explicitly selected evidence paths uploadable and
print concise acquisition errors and per-workflow missing-tool/failure summaries
to stderr even when JSON is written to a file. The diagnostic changes pass the
22 focused acquisition/toolchain unit checks, including JSON-output diagnostic
regressions, and an actual missing-GHDL probe
prints its blocked state while retaining exit 1. A second CI run is required to
obtain the missing platform diagnoses and verify fixes; it has not been counted
as passing here.

Neither this run nor the local toolchain probes exercise physical FPGA
programming. Actual host drivers/cables and observed board behavior remain
separate required evidence.

## Apple Silicon Tcl-enabled Yosys candidate

The second native run showed that the Apple Silicon suite's Yosys rejects
`-c` because that binary was built without Tcl. VWB's Tcl scripts preserve
include paths and macro values containing spaces or Unicode; switching their
text to the native Yosys command parser would change argument handling.

The tested CI candidate therefore retains those scripts and provisions the
native Homebrew Yosys 0.70 bottle on Apple Silicon. Its official
[formula](https://github.com/Homebrew/homebrew-core/blob/master/Formula/y/yosys.rb)
depends on Tcl/Tk. The version, observed formula digest, upstream source digest,
and `arm64_sequoia` bottle digest are recorded under
`supplemental_candidates.yosys-darwin-arm64-homebrew-tcl` in the toolchain lock.
CI verifies the formula version and bottle digest, checks the installed version,
and requires an actual `yosys -c` script to execute successfully before probing
the HDL workflows. An isolated directory exposes only that Yosys binary through
`--overlay-tool yosys=ABS_EXE`; the acquired suite's Python remains the selected
probe interpreter. The original launcher is backed up and both executable
hashes are receipted. The external symlink survives suite wrapper PATH resets
and marks this installation nonportable.

This is an **external Homebrew prerequisite candidate** with successful native
Tcl and 14/14 workflow evidence in CI5 and CI6 above. Homebrew and its transitive
libraries are not a fully pinned managed runtime, and this route does not
establish clean-machine support or physical FPGA programming. The other
platform jobs retain their existing tool selection.
