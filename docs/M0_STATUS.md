# M0 implementation and evidence status

Recorded 2026-10-18. **M0 implementation and Linux feasibility proofs are in
place; the full M0 exit gate remains open.** The required six native targets,
actual Pages/browser connectivity, clean installation, and physical board
programming are not all verified. This is a development foundation, not the
requested complete first usable website release.

The local Linux x86-64 toolchain passed **14 real checks in each of three path
cases: 42 executions** across ordinary paths, spaces, and Unicode/spaces. Native
runtime packaging and semantic editor probes also have local evidence. Those
results do not turn other platform or hardware cells into passes.

The [retained CI observations](M0_CI_TOOLCHAINS.md) now include a second run.
Both Linux architectures built and exercised their native runtime bundles;
all six hosts passed the companion/process/packaging/LSP unit step. Four hosts
stopped at legacy CLI fixture portability failures before bundle construction.
Linux x86-64 passed all 14 HDL workflows; the retained reports identify the
remaining tool gaps and loader failures on other hosts. Chromium passed again.
Follow-up portability and tool fixes await native confirmation.

| Plan task | Outcome at this checkpoint | Evidence and remaining gate |
| --- | --- | --- |
| M0-01 — Protocol and capabilities | Implemented proof | [Protocol](M0_PROTOCOL.md), [reference schema](../protocol/companion-v1.schema.json), [companion](../companion/README.md), and [tool discovery](M0_TOOLCHAINS.md). Project files, authenticated jobs/status, revisions/staleness, and artifacts are exposed. Production diagnostics/editor transport contracts remain integration work. |
| M0-02 — Runtime/tool acquisition | Partial | [Pinned native runtimes and bundle proof](M0_PACKAGING.md); [managed HDL acquisition and architecture routes](M0_TOOLCHAINS.md). A newly checksum-verified/extracted Linux suite passes all 14 real workflow checks. Other targets, complete managed tools, minimum OS verification, and GHDL gaps remain open. |
| M0-03 — Portable processes | Implemented; six-host unit checks passed | [Runner](../companion/process.py) and [process tests](../tests/test_process.py) cover tree cancellation, timeout and bounded output. All six native hosts passed their applicable checks, including Windows Job Objects; platform-specific skips stay explicit. |
| M0-04 — Local-folder I/O | Local and six-host HTTP unit proof | [HTTP tests](../tests/test_companion.py) verify launch-authorized folders, inventory/read, atomic revision saves, mode/CRLF preservation, external/concurrent conflicts and path boundaries. Native folder UI, watches, full file lifecycle/recovery, and broad project acceptance remain open. |
| M0-05 — Pages connection | Local and CI Chromium proof; HTTPS gate open | [Browser evidence and reproduction](M0_BROWSER.md) verifies pairing, CORS, file read/save, conflict protection and a native job in Chromium; the CI browser job also passed. [Pages configuration](https://github.com/sfmth/verilog-workbench/actions/runs/39150223828) failed with site Not Found. Publication enablement and actual HTTPS origin, supported browser/OS matrix, denied permission and reconnect remain gates. |
| M0-06 — Full toolchain probe | Linux x86-64 full proof; other-host gaps measured | [Ordinary](m0-evidence/toolchains-linux-x64.json), [spaces](m0-evidence/toolchains-linux-x64-spaces.json), and [Unicode/spaces](m0-evidence/toolchains-linux-x64-unicode-spaces.json) each pass 14 non-programming checks. The custom formal build-path proof also passes. [Native CI reports](M0_CI_TOOLCHAINS.md) record 14/14 Linux x86-64, 11/14 Linux ARM64 and Intel Mac, 8/14 Apple Silicon and each Windows architecture, and 11/14 Arch userspace. Failed/blocked workflows remain open; follow-up fixes require rerun. |
| M0-07 — Host FPGA programming | Not run | Tang Nano 9K and iCEBreaker packing passed locally. Physical boards/cables were unavailable; programming, native drivers/permissions and observed device behavior are unverified. |
| M0-08 — Clean native installation | Partial | The official Linux runtime proof runs relocated with no system Python and poisoned Python environment variables. Both Linux architectures now build pinned bundles and pass isolated bundled-interpreter smoke checks in CI. Four other hosts pass source unit checks but need legacy fixture fixes before package verification. Full tool installation, repair/uninstall, signing and clean target-machine evidence remain open. |
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
| Linux x86-64, including Manjaro/Arch | Development-host proofs and passing CI HDL gate; Arch userspace job failed without retained diagnosis | Clean native setup, diagnosed Arch/Manjaro pass, actual Pages/browser matrix, both boards and complete UI workflows |
| Windows x86-64 | Native CI checks ran; core and toolchain jobs failed before complete evidence | Full workflow verification; integrated standalone GHDL/Python/VPI route; browser, install and boards |
| macOS Apple Silicon | Native CI checks ran; core and toolchain jobs failed before complete evidence | Full workflow verification; quarantine/signing, browser, clean setup and boards |
| macOS Intel | Native CI checks ran; core and toolchain jobs failed before complete evidence | Full workflow verification; sustainable suite distribution, missing bundled GHDL route, browser, install and boards |
| Linux ARM64 | Native CI checks ran; core and toolchain jobs failed before complete evidence | Full workflow verification; reproducible supplemental GHDL route, browser, install and boards; no assumed x64 translation |
| Windows ARM64 | Native ARM64 CI checks ran; core and candidate translated-toolchain jobs failed | Full tool/runtime workflow verification and GHDL route, ARM64 drivers, browser, install and boards |

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
The [first CI observation](M0_CI_TOOLCHAINS.md) distinguishes observed job
outcomes from missing retained diagnoses. Follow-up logging/upload and core-test
fixes need another run; configuration and asset availability are not passes.

Public GitHub Pages configuration failed with site Not Found, so publication
enablement and real deployed-origin/browser acceptance remain open. A local
browser demo alone cannot satisfy that gate. No unavailable host, missing tool,
unrun CI job or unattached board is labeled passed in this report.

## Next work

Close the Pages/browser and native platform evidence gaps and supplemental
GHDL distribution routes, then
carry the proven project/process/editor adapters into the M1 workspace. M1 may
advance as an internal prototype while these gaps are visible; the complete
first release remains gated by every owner-selected workflow and launch target.
