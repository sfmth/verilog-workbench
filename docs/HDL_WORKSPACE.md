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
repeatable Cocotb seeds, waveform array bounds, continue-after-failure, and an
explicit synthesized-gate check. Source and test include folders are confined
to the granted project. Native tool output is retained for passing tests as
well as failures, including testbench `$display` output. Discovery and saved
wave management retain machine-readable JSON.

VCD waves open in the browser; FST files remain downloadable. Native VHDL
testbenches cannot drive converted Verilog gate netlists, so VWB reports that
gate check as skipped. Cocotb can drive a converted VHDL design for the gate
check. The workspace shows skipped/failed checks and does not summarize a
mixed passing/failing run as a complete pass.

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
opens real VCD/SVG artifacts. Its original 14 workflows reuse the unchanged M0
fixtures and native result oracles: Verilog/SV/VHDL assertions, both Cocotb
simulators, waveform data, lint, synthesis/schematics, VHDL conversion, formal
proof/counterexample and both packed bitstreams. Additional checks cover actual
failures, selected-function execution, compile flags, gate tests, saved waves,
starter smoke tests and hierarchy/test selection. No successful workflow uses
mock tools or substituted job results. `--only` explicitly reports a subset;
default acceptance requires the entire selected suite.

Pages CI requires complete HDL browser acceptance against both built output
and the deployed HTTPS app, using the integrity-pinned Linux tool suite. Public
local-network permission is granted explicitly through the Chromium test API;
manual permission UX remains separate evidence.

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
