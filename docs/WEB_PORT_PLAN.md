# VWB website port plan

Status: initial draft; product and execution choices await owner input.

Started: 2026-10-17 (America/Los_Angeles).

Planning branch: `web-port-plan`.
Inspected baseline: `master` at `3e384347b93c69266b055c65eda8a6cd308d4536`.

## Goal and confirmed scope

Port Verilog Workbench into a usable website, using this branch to develop the
plan separately from the existing CLI. GitHub Pages is a candidate frontend
host following the branch-publishing discussion. The owner explicitly wants
questions about the website before its behavior and design are settled.

This commit adds planning documentation only. It does not implement the app,
change Pages settings, publish a site, or replace the CLI. Existing source,
tests, and command behavior remain the compatibility baseline for future work.

## What the repository contains today

The application is a 5,753-line Python CLI in [vwb.py](../vwb.py), version 0.2.0.
No existing website, API server, or Pages deployment workflow was found on the
inspected baseline. The remote branch inventory contained only `master`.

| Existing component | Role | Implication for the website |
| --- | --- | --- |
| `SourceCatalog` | HDL declarations, packages, dependencies, and source closure | Reuse its behavior and regression cases; adapt host-file access if running in-browser. |
| `Workbench` | Project settings, tests, tools, generated files, and results | Introduce narrow adapters as needed; avoid a full rewrite before a working slice. |
| `command_list` / `list --json` | Modules, languages, files, dependencies, problems, tests, interfaces, and primitives | Starting point for the project-inventory data contract. |
| `Workbench.run` | Native subprocess execution, output capture, process-group termination | Needs a separate browser or runner implementation. It is not a sandbox. |
| `_compile_simulation`, `_run_vhdl_test` | Native simulation, including Cocotb integration | Browser parity needs toolchain experiments, not just a Python interpreter. |
| `open_waveform`, `SavedWave`, wave archive methods | GTKWave launch, layouts, tags, metadata | Add an integrated browser viewer and explicitly map saved-wave behavior. |
| `lint_with_tool`, `synthesize` | Native checks, Yosys, schematic generation | Separate computation from browser display of diagnostics and artifacts. |
| `run_formal`, `run_fpga` | SBY/solvers, board builds, packing, programming | Later capability-dependent workflows; hardware access needs its own design. |
| [Dockerfile](../Dockerfile) | Full interactive Linux toolchain and desktop support | Useful tool inventory; do not expose this image unchanged as a public execution service. |
| [CI workflow](../.github/workflows/ci.yml) | Distro validation and Docker checks on pushes/PRs | Keep existing coverage; plan focused web checks to avoid rebuilding every tool for each UI edit. |

Existing regression sources include
[core tests](../examples/test/test_vwb.py),
[wave/synthesis tests](../examples/test/test_vwb_wave_synth.py), and the
[validation driver](../.github/scripts/validate_vwb.py).
The repository declares `GPL-2.0-only`; retain its license and notices, and record
the versions, sources, and license information of any distributed web engines.

## The central architecture decision

GitHub Pages hosts static assets. It cannot run the existing Python application
and native tool processes on GitHub's web server. It can serve JavaScript and
WebAssembly that execute on the visitor's device. Therefore a Pages-hosted UI is
possible, while the execution model remains an explicit decision.

| Execution model | What the visitor needs | Main benefit | Main tradeoff / proof required |
| --- | --- | --- | --- |
| Browser only | A supported browser; engines download with the app | No local install or hosted compute service | Prove language, simulator, synthesis, memory, download-size, and testbench support. Full Cocotb/VHDL parity is unproven. |
| Browser plus local runner | VWB/toolchain or a companion container on their computer | Reuse native VWB capabilities and reach local hardware | Installation remains necessary; verify secure pairing and HTTPS-to-local connectivity in target browsers. |
| Browser plus hosted runner | Access to a separately operated compute service | Native tools without visitor installation | Needs hosting, operating budget, queueing, and isolation for user-supplied code. Pages alone does not provide it. |

A hybrid may offer browser execution for a tested subset and an optional runner
for additional features. Do not commit to that extra scope before asking the
owner. Do not silently replace Cocotb with HDL tests or omit VHDL if either is a
first-release requirement.

## Proposed feature map — priorities are not yet approved

| Workflow | Browser-facing work | Execution dependency / open question |
| --- | --- | --- |
| Project creation and examples | File tree, editor, bundled examples, top/test selection | Define project format and import/export behavior. |
| Discovery and hierarchy | Modules/entities, dependencies, matched tests, source navigation | Preserve VWB discovery semantics through a port or native adapter. |
| Simulation | Run/stop controls, compiler messages, pass/fail, logs | Prove selected simulator and supported HDL/test styles. |
| Starter tests | Preview and edit generated tests before running | Preserve clocks/resets/arrays; label starters as smoke tests, not functional validation. |
| Waveforms | Signal tree, buses/radix, zoom, cursor, ordering | Start evaluation with VCD; FST decoding and GTKWave layout compatibility need separate decisions. |
| Lint | Source-linked warnings/errors and per-tool results | Expose only engines actually available in the chosen mode. |
| Synthesis | Schematic pan/zoom, netlist/artifact download | Native or proven browser Yosys path plus a supported renderer. |
| Gate-level tests | RTL versus synthesized results | Preserve test reuse and language restrictions. |
| Saved results | Wave tags, run history, project export | Choose browser-local persistence versus accounts/cloud storage. |
| Formal | Configuration, job status, results/counterexample traces | SBY/solver availability; likely runner-first unless browser support is proved. |
| FPGA | Board/constraints selection and build downloads | Preserve fixed Tang Nano 9K and iCEBreaker profiles; separate build from physical programming. |

## User decisions to collect

The first conversation should resolve these three questions:

1. **Installation and execution:** Must the site run without installing anything,
   can users run a local companion, or is separately hosted compute acceptable?
   If hosted compute is chosen, follow up about budget and who can submit jobs.
2. **First-release priorities:** Which are essential: editing, simulation and
   waves, Cocotb, VHDL, lint/synthesis, formal, or FPGA workflows? Ask for must-have
   versus later features rather than treating every CLI option as an MVP requirement.
3. **Interaction style:** Prefer a workspace with editor/results panes, a guided
   select-edit-run flow, or a workspace with an optional beginner guide?

Follow up after those answers with the relevant details: intended audience,
desktop versus phone/tablet use, visual style and example sites, multi-file
projects, offline use, project size, saving/sharing, and GitHub integration.
Accounts, collaboration, billing, and public project sharing are uncommitted
scope. Record answers in this document before treating them as requirements.

## Implementation sequence and acceptance gates

### M0 — Settle scope and prove execution

- Record the answers above and a first-release feature list.
- Evaluate browser simulator and synthesis candidates using reproducible builds
  or maintained distributions; record provenance and exact versions.
- Test includes/defines, multi-file dependencies, a clocked SV design, errors,
  stop/timeout behavior, and waveform output. If required, separately prove
  Cocotb/Python simulator integration and VHDL.
- Run small examples such as `encoder` and `sv_beginner_counter`, then a larger
  dependency-rich design. Compare results against the native CLI.
- Measure cold download, startup, peak memory, execution time, and waveform size.
  Test under the actual Pages origin and project subpath, not just localhost.
- Check worker/runtime/header needs before selecting an engine. Do not depend
  on multithreading or browser isolation headers without validating the host.

**Exit gate:** a short evidence-backed execution decision and an explicit
supported/unsupported matrix. An unsuccessful spike changes the scope or
execution proposal; it does not justify a button that produces pretend results.

### M1 — Deliver one usable end-to-end workflow

- Build a static UI with an example project, file editing, project import/export,
  top/test selection, and visible engine capabilities.
- Connect one real execution adapter: compile and run the edited project,
  report errors with file/line locations, and show a browser waveform.
- Implement run cancellation, a finite execution limit, recovery after failure,
  and clear unsaved-change handling.
- Use a worker for heavy browser execution/parsing; keep editing and stop
  controls responsive. For native execution, expose structured job operations.

**Exit gate:** a visitor can open an example, change HDL, run a real test,
inspect signals, introduce/fix a compiler error, and export their project.

### M2 — Preserve the workflows selected for release

- Add hierarchy/test discovery, required starter-test generation, and the
  selected lint/synthesis capabilities.
- Add run history and tagged waves with an explicit browser-state format.
  Evaluate existing GTKWave layout import rather than claiming compatibility.
- Add Cocotb, VHDL, gate-level testing, and additional engines in priority order
  only after each passes its execution gate.
- Validate a larger bundled design and meaningful array/bus waveforms.

**Exit gate:** selected workflows agree with native VWB on pass/fail and
important artifacts. Discovery preserves include/define handling, packages,
interfaces, dependency closure, and VHDL ordering. Unsupported features explain
the limitation and any available execution mode.

### M3 — Publish the selected release and expand

- Add a dedicated Pages build/deploy workflow triggered by the chosen web branch.
  For a built frontend, use Actions to deploy its output artifact; do not serve
  raw TypeScript or assume an arbitrary source folder is a branch-publishing option.
- Configure the `/verilog-workbench/` base path, asset/worker/WASM URLs, and
  reload-safe navigation. Keep deployment concurrency controlled.
- Validate Chromium and Firefox, keyboard use, and the mobile/tablet level
  selected by the owner. Verify storage/export and large-waveform behavior.
- Add formal/FPGA build flows when requested and supported. Treat USB programming
  as a later explicit capability, with local-runner/browser support evaluated.
- Document supported languages/tools, limits, project portability, and recovery.

**Exit gate:** the actual deployed Pages URL passes the end-to-end workflow,
asset reloads, error handling, and export/reimport checks.

## Boundaries that keep the port maintainable

Use a common project model, capability report, job status, diagnostics, and
artifact manifest across execution modes. Include source revision and tool
versions in results so stale waveforms cannot masquerade as a fresh run.
Model job states explicitly: queued, running, passed, failed, cancelled, timed out.

Derive inventory from `list --json`; stabilize its schema without scraping
colored terminal output. Introduce structured results around selected workflows
before refactoring unrelated CLI code. Keep native CLI commands and tests working.

A tentative layout is `web/` for frontend/workers, `docs/` for decisions, and
a runner package only if that execution mode is selected. Framework, editor
library, waveform library, and Python-in-browser approach remain unselected.
Prefer maintained components after checking bundle size, capability, and license.

For browser persistence, evaluate IndexedDB plus portable project export. Make
uploads explicit if any remote execution or storage is added. Hosted execution
must isolate arbitrary Cocotb Python and formal/tool jobs, constrain resources
and network access, and confine project/artifact paths. A local runner must
require intentional pairing and restrict accepted origins.

## Verification strategy

This planning commit needs documentation review, valid relative links, and
`git diff --check`; it changes no executable behavior.

Implementation checks should cover project-model/discovery compatibility,
real simulator pass/fail and waveform correctness, cancellation/timeouts,
stale results, export/reimport, and Pages subpath loading. Use existing CLI
regressions when native behavior changes. Run the existing representative native
validation at integration milestones; use focused web checks for ordinary UI edits.

## Open risks and next action

The highest uncertainty is execution parity, especially Cocotb/Python, VHDL,
formal, and FPGA toolchains. Other risks are large waveform memory, slow engine
downloads, browser storage limits, local-runner connectivity, and drift between
native and web discovery. Measure these in the selected mode.

**Next action:** collect the owner's three initial answers, update scope and
architecture here, and then begin M0's smallest relevant feasibility experiment.
No estimates or full-parity promises should precede that evidence.

## Primary technical references

- [GitHub Pages overview](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)
- [Pages publishing sources and branch/folder choices](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
- [Creating a Pages site and static hosting restrictions](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)
- [Emscripten API limitations](https://emscripten.org/docs/porting/guidelines/api_limitations.html)
- [Emscripten filesystem overview](https://emscripten.org/docs/porting/files/file_systems_overview.html)
- [Cocotb simulator integration / GPI](https://docs.cocotb.org/en/stable/library_reference_c.html)

References checked during initial planning on 2026-10-17 (America/Los_Angeles).
