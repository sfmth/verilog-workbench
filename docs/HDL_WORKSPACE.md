# Native HDL workflows in the browser workspace

The workspace now exposes the existing VWB HDL execution controls through its
typed companion API. Real native tools execute on the local host; source,
testbenches, netlists, proof traces and bitstreams remain in the granted project.
The scope follows VWB and its tool-supported language subsets, including the
existing restriction against mixing VHDL and Verilog within one hierarchy.

## Simulation and tests

Select one design and an explicit **Test file**, **Testbench top** or **Cocotb
function**, or leave these fields empty to use native discovery. Choosing a row
in **Tests** selects that row's actual file, test language and bench top, and
clears incompatible Cocotb function/seed settings. Test and lint actions also
accept comma-separated module names when no explicit test file is selected.

The compile/test options expose one define/include folder/plusarg per line,
repeatable Cocotb seeds, waveform array bounds, and an
explicit synthesized-gate check. Source and test include folders are confined
to the granted project. Native tool output is retained for passing tests as
well as failures, including testbench `$display` output. Discovery and saved
wave management retain machine-readable JSON.

VCD waves open in the browser; FST files remain downloadable. Native VHDL
testbenches cannot drive converted Verilog gate netlists, so VWB reports that
gate check as skipped. Cocotb can drive a converted VHDL design for the gate
check. The workspace shows skipped/failed checks and does not summarize a
mixed passing/failing run as a complete pass.

All selected native tests and lint checks run and aggregate their failures.

A selected design without a test may generate a starter smoke test, following
the CLI. This is visible in Files after the job. Discovery is invalidated when
native jobs change project files; run **Discover project** to refresh Designs
and Tests. A generated starter is not functional verification: add assertions.
The first job can be marked stale because it generated new source/test files;
rerunning uses the now-visible saved test.

## Discovery, synthesis, formal and FPGA

**Designs** includes packages, interfaces, primitives and catalog problems.
The selected top expands into a bounded dependency tree with cycle/unresolved
markers and source navigation. These are catalog unit dependencies, not
elaborated instance names. The native catalog determines source ordering and
language conversion.

Synthesis exposes full-logic and flatten-hierarchy options, compile settings,
and JSON/SVG/PNG/DOT output. SVG/PNG artifacts open in an image context with
zoom, fit, pointer dragging, pan controls and native scrollbars. Original bytes
can always be downloaded. Project SVG scripts are not executed.

Formal runs select a project `.sby` configuration. Output retains actual SBY
status; a property failure exposes the generated counterexample VCD through
Artifacts and the waveform viewer. A nonzero tool exit alone is not treated
as proof that a valid counterexample exists; acceptance checks inspect SBY's
status file and the generated trace.

Both fixed board profiles, Tang Nano 9K and iCEBreaker, expose synthesis,
place-and-route and pack stages, compile settings and explicit project
constraints. Programming remains a separate confirmed action. Packed `.fs`
and `.bin` files are verified build outputs; they do not establish physical
board/cable/USB programming support.

## Saved waveforms

Choose **Waveforms / saved runs** to simulate and optionally archive a passing
wave under a tag, list saved tags, or reopen a tag without simulation. Existing
tags require explicit replacement. The companion always uses headless wave
operations; the ordinary CLI keeps its existing GTKWave launch default. A
saved VCD reopens in the browser; FST/layout files remain downloadable artifacts.
Saved waves retain native metadata and survive normal native build cleanup.

## API and acceptance

`POST /v1/jobs` adds the typed `wave` command. Simulation options include `test`,
`test_top`, `testcase`, `seed`, `waves`, `wave_format`, `max_array_words`,
`gate_level`, `keep_going`, `defines`, `includes` and `plusargs`. The supported
compile options also apply to lint/synthesis/FPGA where the CLI supports them.
Wave management accepts `list_saved` or `load`; capture accepts `tag` and
`replace_tag`. Management cannot inherit stale simulation settings. Project
paths and inputs are bounded/validated before starting a native process. Raw
compiler, simulator and Tcl argument passthrough remains outside this API.

`scripts/probe_hdl_workspace.mjs` drives rendered controls and a real companion.
It uses fresh temporary Unicode projects and builds for each workflow, retains
source/artifact hashes, verifies downloads against native disk bytes, and
opens real VCD/SVG artifacts. Its core 14 workflows reuse the M0 fixtures and
native result oracles: Verilog/SV/VHDL assertions, both Cocotb
simulators, waveform data, lint, synthesis/schematics, VHDL conversion, formal
proof/counterexample and both packed bitstreams. Additional checks cover actual
failures, selected-function execution, compile flags, gate tests, saved waves,
starter smoke tests and hierarchy/test selection. No successful workflow uses
mock tools or substituted job results. `--only` explicitly reports a subset;
default acceptance requires the entire selected suite.

The M0 Cocotb fixture retains all four truth-table assertions and now queues
one final write after them to guard a real shutdown regression. The pinned
`2.1.0.dev0+41564633` build could invoke that callback after Python finalized,
crashing Icarus 13 and 14 even after a test passed. A private trusted startup
module cancels only the pending callback during final shutdown for this exact
build on Icarus. It never changes live test scheduling, assertions or verdicts;
explicit user startup configuration and other runtime builds are preserved.
Real negative controls retain assertion failures and the original crash as
failures. Missing/invalid/unexecuted Cocotb results also remain failures and
now report setup/startup attention instead of claiming setup is OK.

Some advanced bundled tests need additional Python packages. The existing
`setup.sh --full` route includes bitstring, NumPy and Pillow. With another
simulator Python environment those project test dependencies must be provided
there too. The isolated larger-design checks used bitstring 4.3.1, bitarray
3.4.3, NumPy 2.2.6 and Pillow 11.3.0; package imports succeeding does not prove
an algorithm correct, especially where an example bench has no assertions.

Pages CI requires complete HDL browser acceptance against both built output
and the deployed HTTPS app, using the integrity-pinned Linux tool suite. Public
local-network permission is granted explicitly through the Chromium test API;
manual permission UX remains separate evidence.

Static asset verification uses Chromium's response-stage inspection, scoped to
the static site origin/prefix. It pauses the genuine browser response, hashes
its entity bytes, then continues that original response unchanged. This records
module bytes before a completed parser worker can terminate its inspection
target. Native companion requests retain the browser's ordinary permission,
CORS preflight and HTTP path. Cache behavior remains outside this probe.
Earlier receipts retain the closed-worker-target collection failures explicitly.

## Native platform package candidates

The acquisition helper adds explicit supplemental routes for the previously
blocked targets: the official GHDL 4.1 Intel macOS mcode archive; GHDL 6 and
Graphviz 14.1.1 Windows archives; and pinned Windows CPython 3.12/Cocotb wheels
installed offline into a local virtual environment. Linux ARM64 uses a declared
Ubuntu GHDL LLVM prerequisite with its matching standard-library prefix.
Downloads have locked integrity/size checks and bounded extraction. Windows
ARM64 HDL tools may use the previously accepted OS x64 translation route;
the separate native companion runtime remains ARM64.

Acquisition/discovery alone does not establish workflow support. All 14 native
workflow checks remain mandatory in CI for each target. Supplemental results
must be recorded from those actual runners; installer completeness and physical
board acceptance remain separate release gates.

The retained [local browser report](workspace-evidence/hdl-local-linux-x64.json)
passes all 25 workflows in Chromium 141, including the original 14 native
oracles and 11 extended checks. Its loaded runtime hashes match the
[20-file build](workspace-evidence/hdl-web-build.json). The
[source receipt](workspace-evidence/hdl-source-linux-x64.json) records 253 Python
checks (five skips), 167 CLI checks (one skip) and 116 JavaScript checks passing.
Separate regression reports retain the existing workspace, semantic, source
editing and file-lifecycle checks. Both
[ordinary-path](workspace-evidence/hdl-native-ordinary-linux-x64.json) and
[Unicode/space-path](workspace-evidence/hdl-native-unicode-spaces-linux-x64.json)
native Linux smoke runs pass all 14 workflows. These local passes do not
substitute for the separate hosted/public and foreign-host results.

The [runtime follow-up browser report](workspace-evidence/hdl-runtime-local-linux-x64.json)
passes all 26 workflows against one coherent
[20-file build](workspace-evidence/hdl-runtime-web-build.json). Every fresh
workflow context verifies its six declared core runtime asset hashes. The additional workflow
executes the pending-write shutdown regression and checks the staged startup
module against the trusted source. The
[follow-up source receipt](workspace-evidence/hdl-runtime-source-linux-x64.json)
records 264 native checks (eight skips), 171 CLI checks (one skip), 116 JavaScript
checks, and a separate nine-check managed-runtime run with no skips, including
three real simulator controls. The
[missing-dependency negative control](workspace-evidence/hdl-cocotb-missing-results-local-linux-x64.json)
also verifies the actual simulator's nonzero exit and improved setup diagnosis.
The [shutdown diagnosis](workspace-evidence/hdl-cocotb-shutdown-local-linux-x64.json)
retains the native stack, affected Icarus versions and before/after controls.

[Representative acceptance](HDL_REPRESENTATIVE_ACCEPTANCE.md) exercises 10
larger example modules across five phases: 73 real commands and 38 hashed
artifacts, including unchanged processor and processing-array RTL/gate runs.
Their weak legacy assertions are documented explicitly. Separate
[output-format evidence](workspace-evidence/hdl-output-formats-local-linux-x64.json)
verifies native FST capture, archive/list/reopen, byte-identical saved output,
FST-to-VCD conversion and DOT schematic generation.

The final [portable archive validation](workspace-evidence/hdl-portable-archive-local-linux-x64.json)
adds a host-independent POSIX link-graph check before extraction. It rejects
symlink-plus-`..` escapes consistently on Windows and Unix, retains the original
real extraction test, and accepts all 86 symlinks in the locked Linux archive.
All 266 native regression checks pass locally (eight optional skips), including
25 acquisition checks. The earlier 264-check receipt remains historical.

[Final native CI](https://github.com/sfmth/verilog-workbench/actions/runs/39366706022)
passes every job at `9acd8bb8d071cf5a3660bfc16b0870e1a1271bcb`:
six native unit/CLI/runtime-package jobs, all six 14-workflow toolchain suites,
Arch userspace's 14 workflows and the browser-source job. The
[retained final report](workspace-evidence/hdl-native-ci.json) records exact
tool/package/architecture evidence and platform skips. Earlier failed attempts
and the preceding passing portable-runtime run remain separately retained.

The accepted response-stage collector passes all 26 HDL workflows against both
the [local build](workspace-evidence/hdl-runtime-cdp-local-linux-x64.json) and
the [actual public HTTPS app](workspace-evidence/hdl-runtime-cdp-public-local-linux-x64.json)
on the local Linux host. Each workflow's captured asset map matches its manifest;
waveform cases require both the parser worker and its imported module bytes.
The [independent method controls](workspace-evidence/browser-asset-capture-control-linux-x64.json)
verify an immediately terminated worker, a compressed module and a genuine
rejected native CORS preflight. The
[other browser regressions](workspace-evidence/hdl-browser-asset-regression-local-linux-x64.json)
pass workspace 13, source 15, file lifecycle 16 and semantic editing in three
languages. A deliberate late script error remains a failed probe after cleanup.

[Final hosted Pages CI](https://github.com/sfmth/verilog-workbench/actions/runs/39366706107)
passes build, deployment and public HTTPS verification at `9acd8bb`. Both the
[built-output HDL report](workspace-evidence/hdl-runtime-ci-linux-x64.json) and
[public HTTPS HDL report](workspace-evidence/hdl-runtime-public-ci-linux-x64.json)
pass all 26 workflows in Chromium 151 with original-network asset capture.
The [retained Pages summary](workspace-evidence/hdl-pages-ci.json) also records
the existing workspace, source, semantic and lifecycle probes on both origins,
and nine Cocotb shutdown checks, including three real simulator controls, in
the build job. Public local
network permission is still explicitly granted by the test API; manual
permission UX, clean installers and physical programming remain separate gates.
