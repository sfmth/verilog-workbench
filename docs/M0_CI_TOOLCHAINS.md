# First native toolchain CI observation

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
20 focused acquisition/toolchain unit checks, and an actual missing-GHDL probe
prints its blocked state while retaining exit 1. A second CI run is required to
obtain the missing platform diagnoses and verify fixes; it has not been counted
as passing here.

Neither this run nor the local toolchain probes exercise physical FPGA
programming. Actual host drivers/cables and observed board behavior remain
separate required evidence.
