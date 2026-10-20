# M0 implementation and evidence status

Initial checkpoint recorded 2026-10-18; native evidence updated 2026-10-20 UTC.
**M0's engineering foundation is implemented; the full M0 exit gate remains
open.** All six required platform targets and Arch userspace now pass the
complete 14-workflow HDL smoke probe. All six native companion builds also
pass their unit, CLI, browser protocol, bundle and isolated smoke checks.
Clean installation, the supported browser/permission matrix and physical board
programming remain release gates. This remains a development foundation.

The [complete native run](https://github.com/sfmth/verilog-workbench/actions/runs/39366706022)
tests `9acd8bb8d071cf5a3660bfc16b0870e1a1271bcb`. Its
[retained report](workspace-evidence/hdl-native-ci.json) records each
platform's 266 unit tests and 171 CLI tests, explicit platform skips, actual
14-workflow results, package hashes, tool versions and architecture routes.
The earlier [all-green portable-runtime run](https://github.com/sfmth/verilog-workbench/actions/runs/39364175524)
at `304832b233916ce8e4cd69a325396a3b27b40c95` remains retained in its own
[report](workspace-evidence/hdl-native-portable-ci.json); the later revision
changes browser-probe evidence collection, with production/runtime/toolchain
code unchanged.
The [intermediate native run](https://github.com/sfmth/verilog-workbench/actions/runs/39364816202)
at `8484840400edc50096c949a2de376000464145cc` also remains retained in its
[report](workspace-evidence/hdl-native-drained-ci.json), before the shared
collector adopted narrowly scoped original-response CDP capture. Independent
[CORS](workspace-evidence/hdl-browser-cors-collector-review-linux-x64.json) and
[short-lived worker](workspace-evidence/hdl-browser-worker-collector-review-linux-x64.json)
method controls distinguish preserved browser networking from the rejected
Playwright routing candidate; they are separate from native HDL passes.
The earlier [initial attempt](workspace-evidence/hdl-native-initial-ci.json)
and [runtime correction attempt](workspace-evidence/hdl-native-runtime-attempt-ci.json)
remain available, including the failures that led to the fixes. The four
previously missing platform tool routes now have actual passing workflow
evidence for these fixtures.

Update 2026-10-19: the development workspace is now
[published and tested from its actual HTTPS Pages origin](WEB_WORKSPACE.md#published-https-evidence).
The initial publication blocker recorded below is closed for the preview;
remaining manual permission/browser/platform/install/hardware gates stay open.
The task table and historical observations below retain the original M0
checkpoint; the required-platform table reflects the newer native evidence.

The [workspace semantic editor extension](EDITOR_WORKSPACE.md) now integrates
the proven language-service route into the browser/HTTP companion, with dirty
document snapshots, reviewed cross-file rename, Apply/Undo and interrupted-write
recovery. Real local Chromium checks pass the SystemVerilog, VHDL and Python
fixtures. Broader constructs, managed service installation and other-platform
semantic/durability acceptance remain open; the original M0 table is unchanged.

The [file/folder workspace integration](FILES_WORKSPACE.md) now adds native
revision-checked creation/moves, private persistent trash and conflict-safe
restoration. Its Linux Chromium built/public probes pass 15 groups; all six
native unit/CLI/bundle/smoke jobs pass. Broader browser/install/hardware gates
remain open; the previous four toolchain package gaps have since passed the
complete native workflow probes linked above.

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

All six native runtime bundles pass their matching-host smoke checks again in
the current complete native run:
isolated startup, pairing, ordinary local-folder I/O, exact revision-checked
saves and a real bundled VWB job. The browser integration also passes again.

The following table preserves the original 2026-10-18 M0 checkpoint. Later
workspace and native proofs are linked above and in the platform table below.

| Plan task | Outcome at the original checkpoint | Evidence and remaining gate at that checkpoint |
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
| Linux x86-64, including Manjaro/Arch | Native companion checks pass; Linux and Arch userspace each pass 14/14 HDL workflows | Clean native Manjaro/Arch setup, supported Pages/browser and permission matrix, both boards and broader projects |
| Windows x86-64 | Native companion checks and 14/14 HDL workflows pass with pinned GHDL/Graphviz and explicit MSVC CPython/Cocotb | Browser/permission matrix, clean installation and native board drivers/programming |
| macOS Apple Silicon | Native companion checks and 14/14 HDL workflows pass with an explicitly receipted external native Tcl-enabled Homebrew Yosys overlay | Complete tool distribution without a separately managed prerequisite, quarantine/signing, browser, clean setup and boards |
| macOS Intel | Native companion checks and 14/14 HDL workflows pass with pinned supplemental GHDL, verified executable anchoring and its matching standard-library prefix | Sustainable complete tool distribution, browser, clean installation and boards |
| Linux ARM64 | Native companion checks and 14/14 HDL workflows pass with explicitly declared Ubuntu ARM64 GHDL/LLVM prerequisites | Managed prerequisite installation, browser, clean installation and boards; no x64 translation is assumed |
| Windows ARM64 | Native ARM64 companion checks and 14/14 HDL workflows pass; the explicitly selected x64 tools and MSVC CPython execute through Windows 11 x64 translation | ARM64 drivers/USB and physical programming, browser/permission matrix and clean installation |

The passing reports distinguish the pinned suite, supplemental archives and
externally managed prerequisites. Windows uses checksum-pinned GHDL and
Graphviz archives plus pinned Cocotb packages in an explicit x64 MSVC CPython
environment. Intel macOS uses a pinned GHDL executable copied with integrity
checks into the suite runtime anchor, while retaining the supplemental GHDL
standard libraries. Linux ARM64 uses the declared native Ubuntu GHDL/LLVM
route. These are actual workflow passes, with the package and architecture
receipts retained above; they do not establish a clean desktop installer.
Owner-approved Windows x64 translation applies to individual tools and does
not waive native companion, driver or hardware requirements.

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

The original GitHub Pages configuration failed with site Not Found; the later
[published HTTPS evidence](WEB_WORKSPACE.md#published-https-evidence) closes
that initial preview publication gate. The broader supported browser/OS and
denied-permission acceptance matrix remains open. No unavailable host, unrun
CI job or unattached board is labeled passed in this report.

## Next work

Complete clean native installation and repair/uninstall, sustainable tool
distribution, the supported browser/permission matrix and physical FPGA
programming. Extend the fixture proofs to broader projects and supported
minimum OS versions. The workspace may advance while these gaps remain
visible; the complete first release remains gated by every owner-selected
workflow and launch target.
