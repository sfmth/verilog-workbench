# M0 implementation and evidence status

Recorded 2026-10-18. **M0's engineering foundation is implemented; the full M0
exit gate remains open.** Linux x86-64, Arch userspace and Apple Silicon pass
the complete 14-check HDL smoke probe. Required tool packages on four target
rows, actual Pages/browser connectivity, clean installation, and physical board
programming remain gates. This is a development foundation, not the requested
complete first usable website release.

Update 2026-10-19: the development workspace is now
[published and tested from its actual HTTPS Pages origin](WEB_WORKSPACE.md#published-https-evidence).
The initial publication blocker recorded below is closed for the preview;
remaining manual permission/browser/platform/install/hardware gates stay open.
The table and historical observations below retain the original M0 checkpoint.

The [workspace semantic editor extension](EDITOR_WORKSPACE.md) now integrates
the proven language-service route into the browser/HTTP companion, with dirty
document snapshots, reviewed cross-file rename, Apply/Undo and interrupted-write
recovery. Real local Chromium checks pass the SystemVerilog, VHDL and Python
fixtures. Broader constructs, managed service installation and other-platform
semantic/durability acceptance remain open; the original M0 table is unchanged.

The local Linux x86-64 toolchain passed **14 real checks in each of three path
cases: 42 executions** across ordinary paths, spaces, and Unicode/spaces. Native
runtime packaging and semantic editor probes also have local evidence. Those
results do not turn other platform or hardware cells into passes.

The [retained CI observations](M0_CI_TOOLCHAINS.md) distinguish native runtime
proofs from actual HDL workflows and preserve the failures that led to fixes.
The complete HDL matrix was measured in
[run 39153200306](https://github.com/sfmth/verilog-workbench/actions/runs/39153200306),
testing `d136b894138213d1aa6483bf1d9b16750f591cd0`. A Windows fixture correction
and smoke-report logging are checked by
[run 39153500063](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063)
at `831ae23f8f10ebc89c09ed3b3a4cae9a0bbebe2e`. Runtime bundle success does not
certify an incomplete HDL package or a clean desktop installation.

All six native runtime bundles now pass their matching-host smoke checks:
isolated startup, pairing, ordinary local-folder I/O, exact revision-checked
saves and a real bundled VWB job. The browser integration also passes again.

| Plan task | Outcome at this checkpoint | Evidence and remaining gate |
| --- | --- | --- |
| M0-01 — Protocol and capabilities | Implemented proof | [Protocol](M0_PROTOCOL.md), [reference schema](../protocol/companion-v1.schema.json), [companion](../companion/README.md), and [tool discovery](M0_TOOLCHAINS.md). Project files, authenticated jobs/status, revisions/staleness, and artifacts are exposed. Production diagnostics/editor transport contracts remain integration work. |
| M0-02 — Runtime/tool acquisition | Partial | [Pinned native runtimes and bundle proof](M0_PACKAGING.md); [managed HDL acquisition and architecture routes](M0_TOOLCHAINS.md). A newly checksum-verified/extracted Linux suite passes all 14 real workflow checks. Other targets, complete managed tools, minimum OS verification, and GHDL gaps remain open. |
| M0-03 — Portable processes | Implemented; native unit checks pass on all six hosts | [Runner](../companion/process.py) and [process tests](../tests/test_process.py) cover tree cancellation, timeout and bounded output, including real native Windows Job Objects. Darwin cleanup retains ownership until final signaling and validates the kernel's zombie-only group behavior. Applicable native unit checks and bundle jobs pass; platform-specific skips remain explicit. |
| M0-04 — Local-folder I/O | Local and six-host HTTP unit proof | [HTTP tests](../tests/test_companion.py) verify launch-authorized folders, inventory/read, atomic revision saves, mode/CRLF preservation, external/concurrent conflicts and path boundaries. Native folder UI, watches, full file lifecycle/recovery, and broad project acceptance remain open. |
| M0-05 — Pages connection | Local and CI Chromium proof; HTTPS gate open | [Browser evidence and reproduction](M0_BROWSER.md) verifies pairing, CORS, file read/save, conflict protection and a native job in Chromium; the CI browser job also passed. [Pages configuration](https://github.com/sfmth/verilog-workbench/actions/runs/39150223828) failed with site Not Found. Publication enablement and actual HTTPS origin, supported browser/OS matrix, denied permission and reconnect remain gates. |
| M0-06 — Full toolchain probe | Linux x86-64, Arch userspace and Apple Silicon full smoke proofs; four target rows have package gaps | [Ordinary](m0-evidence/toolchains-linux-x64.json), [spaces](m0-evidence/toolchains-linux-x64-spaces.json), and [Unicode/spaces](m0-evidence/toolchains-linux-x64-unicode-spaces.json) each pass 14 non-programming checks. The [Apple Silicon report](m0-evidence/toolchains-ci5-darwin-arm64.json) passes 14/14 with an explicitly receipted external native Tcl Yosys prerequisite. [Native CI reports](M0_CI_TOOLCHAINS.md) record each host's missing workflows. |
| M0-07 — Host FPGA programming | Not run | Tang Nano 9K and iCEBreaker packing passed locally. Physical boards/cables were unavailable; programming, native drivers/permissions and observed device behavior are unverified. |
| M0-08 — Clean native installation | Six-host runtime proof; complete installation remains open | The official Linux runtime proof runs relocated with no system Python and poisoned Python environment variables. All six [native bundle checks](M0_PACKAGING.md) exercise pinned isolated interpreters and actual local project jobs on matching hosts. Full tool installation, repair/uninstall, signing and clean target-machine evidence remain open. |
| M0-09 — Semantic editor workflows | Local feasibility proof | [Editor evidence](M0_EDITOR.md) establishes configured cross-file completion/hover/definition/references and exact rename previews for SV, VHDL and Python fixtures with post-edit checks. The production browser/LSP bridge is M1 integration work; safe commit/rollback, collisions, dirty buffers, undo/recovery and all-OS coverage remain required release gates. |

## Scope of observed passes

The 14 toolchain checks cover Verilog, SystemVerilog, VCD, native VHDL,
Cocotb on Icarus and GHDL, Verilator lint, Yosys JSON, SVG schematics, VHDL
synthesis, SBY proof, a genuine counterexample VCD, and packing both launch
FPGA profiles. [Toolchain documentation](M0_TOOLCHAINS.md) describes exactly
what each artifact check proves.

The [Linux runtime evidence](../packaging/m0-linux-evidence.json) includes
checksum-verified CPython 3.12.15, relocation to a path with spaces, standard
library imports, CLI version, companion launch/health/pairing/local inventory,
and an authenticated bundled `list` job. The runtime bundle does not include
the HDL tools or language servers.

The [editor proof](M0_EDITOR.md) uses real upstream language servers and
compares exact proposed edits against independent expected fixture results.
Slang's tested rename omitted a declaration, so the proof uses Verible for SV
rename with checked service agreement. Unsafe Slang rename must remain
disabled. The internal stdio experiment is not exposed through HTTP and does
not constitute a production editor or general-purpose refactoring engine.

## Required platform and release gaps

| Required target | Current evidence | Required remaining proof |
| --- | --- | --- |
| Linux x86-64, including Manjaro/Arch | Development-host proofs; Linux and Arch userspace each pass 14/14 HDL checks | Clean native Manjaro/Arch setup, actual Pages/browser matrix, both boards and complete UI workflows |
| Windows x86-64 | Nine HDL checks pass; five are blocked by absent Cocotb, GHDL and Graphviz | Integrated Python/VPI, GHDL and schematic routes; browser, clean install and boards |
| macOS Apple Silicon | Fourteen HDL checks pass with an external native Tcl-enabled Homebrew Yosys overlay | Reproducible complete tool package, quarantine/signing, browser, clean setup and boards |
| macOS Intel | Eleven HDL checks pass; three blocked by missing GHDL | Supplemental GHDL and sustainable suite distribution, browser, clean install and boards |
| Linux ARM64 | Eleven HDL checks pass; three blocked by missing GHDL | Supplemental GHDL route, browser, clean install and boards; no assumed x64 translation |
| Windows ARM64 | Native ARM64 companion checks; nine HDL checks pass with OS x64 tool translation, five blocked | Cocotb, GHDL and Graphviz routes, ARM64 drivers, browser, clean install and boards |

OSS CAD Suite currently supplies GHDL and its Yosys plugin only on Linux x86-64
and macOS ARM64. A separately pinned Windows x64 GHDL archive is a candidate,
not an integrated pass; Linux ARM64 and Intel Mac need additional reproducible
routes. Owner-approved OS x64 translation applies to individual tools and does
not waive any native companion, driver or workflow requirement.

The additional formal build-path proof exercises a relative build component
such as `build files µ`. VWB keeps the actual configuration cwd and sources in
place, uses an ASCII output basename for SBY execution when necessary, and
publishes generated results to the configured directory. Historical execution
paths remain in SBY logs and `vwb-formal-run.json`; resuming a relocated SBY
workdir is not guaranteed. Rebuilding and inspecting proof/counterexample
artifacts have real passing evidence.

The CI workflow builds and executes each native runtime bundle on its matching
runner, runs unit/CLI checks, and separately acquires the pinned HDL suite and
executes real workflow probes on all six hosts. Missing workflows fail the
toolchain gate and retain diagnostic evidence. It does not provision editor
services or attach FPGA hardware.
An additional Arch Linux container checks the rolling distribution's userspace
against the pinned native tools. This is a developer feasibility check;
end users do not need Docker, and it does not certify a clean Manjaro/Arch host.
The [CI history](M0_CI_TOOLCHAINS.md) distinguishes observed job outcomes from
early missing diagnoses and records subsequent fixes. Configuration and asset
availability are not passes. Artifacts retain JSON diagnoses and native logs
even when a required workflow is blocked.

Public GitHub Pages configuration failed with site Not Found, so publication
enablement and real deployed-origin/browser acceptance remain open. A local
browser demo alone cannot satisfy that gate. No unavailable host, missing tool,
unrun CI job or unattached board is labeled passed in this report.

## Next work

Close the Pages/browser and native platform evidence gaps and supplemental
tool distribution routes, then carry the proven project/process/editor adapters
into the M1 workspace. M1 may
advance as an internal prototype while these gaps are visible; the complete
first release remains gated by every owner-selected workflow and launch target.
