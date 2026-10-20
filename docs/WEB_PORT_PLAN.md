# VWB website port plan

Status: product requirements are confirmed and M0 implementation is in place.
The native companion, protocol, runtime packaging, process adapter, toolchain
and editor probes, connection proof UI, and six-target CI route now exist.
Local Linux evidence is recorded; the full M0 exit gate remains open for the
unverified platform/browser/install/hardware cells and documented blockers.
See [M0 implementation and evidence status](M0_STATUS.md).

Development update, 2026-10-19: a [browser workspace preview](WEB_WORKSPACE.md)
now adds multi-file local editing, revision-safe saves/recovery, controls for the
existing native jobs, artifact downloads, worker-parsed VCD viewing, and a
validated Pages build. This advances portions of M1/M2/M4 without closing their
exit gates. The [semantic editor extension](EDITOR_WORKSPACE.md) now adds real
language-service queries and reviewed cross-file rename Apply/Undo/recovery.
Broader semantic fixtures, remaining platform/tool installation, browser/permission
acceptance and physical board evidence remain open.

The next workspace increment adds [file/folder lifecycle operations](FILES_WORKSPACE.md):
creation, reviewed revision-checked moves, clean-tab remapping, persistent private
trash and conflict-safe restoration. This closes the initial browser file-action
gap while broader project/platform acceptance remains required.

Started: 2026-10-17 (America/Los_Angeles).
Latest owner decisions: 2026-10-18 (America/Los_Angeles).

Planning branch: `web-port-plan`.
Inspected baseline: `master` at `3e384347b93c69266b055c65eda8a6cd308d4536`.

## Goal and confirmed scope

Port Verilog Workbench into an editor workspace hosted on GitHub Pages, with
an optional beginner guide. The owner accepts a local companion, provided it
is very cross-platform. Develop this plan separately from the existing CLI.
The initial product questions are resolved; ask further questions only when a
specific unresolved choice materially affects implementation.

Owner decisions recorded on 2026-10-17 and 2026-10-18 (America/Los_Angeles):

- **Hosting:** GitHub Pages, connected to a native local companion.
- **Installation:** native application; no Docker or WSL required on any launch
  target. Existing Docker assets may still be used for developer CI.
- **Projects:** ordinary local folders opened through the companion are the
  source of truth; no browser-managed project store is required.
- **Launch targets:** Linux x86-64 (including Manjaro/Arch), Windows x86-64,
  macOS Apple Silicon, macOS Intel, Linux ARM64, and Windows ARM64. All six
  require full first-release workflow support.
- **First usable release:** Verilog/SystemVerilog editing, simulation and
  waveforms; Cocotb/Python tests; lint and synthesis schematics; FPGA builds
  and programming; VHDL; and formal verification are all required.
- **Interface:** editor workspace with an optional beginner guide.
- **ARM tool compatibility:** OS-provided x86-64 compatibility is acceptable
  for individual tools. Record and verify the route; do not label translated
  tool binaries ARM64-native. Docker and WSL remain outside the required setup.
- **Editor capabilities:** syntax highlighting, diagnostics, completion, hover,
  go-to-definition, references, and project-wide rename/refactoring at launch.
- **Launch boards:** the existing Tang Nano 9K and iCEBreaker profiles.

Internal development checkpoints may implement smaller slices, but they do
not satisfy the requested first usable release until all required workflows
work on the agreed platform/board matrix. No required feature is deferred
merely because browser-only execution is difficult.

This branch contains the M0 engineering foundation, local feasibility
evidence and workspace preview alongside this plan. The development preview is
[published on Pages](https://sfmth.github.io/verilog-workbench/workspace/) with
[public HTTPS evidence](WEB_WORKSPACE.md#published-https-evidence). It does not yet
provide the complete editor website or certify the requested six-platform release. Existing CLI behavior
remains the compatibility baseline; narrow process and formal-path fixes are
covered by regression checks. The remaining full browser/platform/board release
matrix remains a separate gate.

## Inspected CLI baseline

At the start of planning, the application was a 5,753-line Python CLI in
[vwb.py](../vwb.py), version 0.2.0. No existing website, API server, or Pages
deployment workflow was found on that inspected baseline; the remote branch
inventory contained only `master`. The table below describes that reusable
baseline, not the new M0 components summarized in [M0_STATUS.md](M0_STATUS.md).

| Existing component | Role | Implication for the website |
| --- | --- | --- |
| `SourceCatalog` | HDL declarations, packages, dependencies, and source closure | Reuse through the native companion against the opened folder; preserve regression behavior. |
| `Workbench` | Project settings, tests, tools, generated files, and results | Introduce narrow adapters as needed; avoid a full rewrite before a working slice. |
| `command_list` / `list --json` | Modules, languages, files, dependencies, problems, tests, interfaces, and primitives | Starting point for the project-inventory data contract. |
| `Workbench.run` | Native subprocess execution, output capture, process-group termination | Needs a separate browser or runner implementation. It is not a sandbox. |
| `_compile_simulation`, `_run_vhdl_test` | Native simulation, including Cocotb integration | Package and validate native simulators, Python, and simulator libraries for all six launch targets. |
| `open_waveform`, `SavedWave`, wave archive methods | GTKWave launch, layouts, tags, metadata | Add an integrated browser viewer and explicitly map saved-wave behavior. |
| `lint_with_tool`, `synthesize` | Native checks, Yosys, schematic generation | Separate computation from browser display of diagnostics and artifacts. |
| `run_formal`, `run_fpga` | SBY/solvers, board builds, packing, programming | Required release workflows; verify native tools and physical hardware access per platform. |
| [Dockerfile](../Dockerfile) | Full interactive Linux toolchain and desktop support | Useful tool inventory; do not expose this image unchanged as a public execution service. |
| [CI workflow](../.github/workflows/ci.yml) | Distro validation and Docker checks on pushes/PRs | Keep existing coverage; plan focused web checks to avoid rebuilding every tool for each UI edit. |

Existing regression sources include
[core tests](../examples/test/test_vwb.py),
[wave/synthesis tests](../examples/test/test_vwb_wave_synth.py), and the
[validation driver](../.github/scripts/validate_vwb.py).
The repository declares `GPL-2.0-only`; retain its license and notices, and record
the versions, sources, and license information of any distributed web engines.

## Architecture direction after owner input

Use GitHub Pages for the browser workspace and a native local companion for the
complete toolchain and local projects. The companion must run on all six launch
targets without Docker or WSL. GitHub Pages serves the interface; actual tool
execution and hardware access take place on the user's machine.

| Layer | Responsibility |
| --- | --- |
| GitHub Pages app | Editor, file/test/hierarchy views, run controls, diagnostics, waveform viewer, schematics, optional beginner guide. |
| Local companion | Access an explicitly opened project, run VWB tools, return structured results/artifacts, and program attached FPGA hardware. |
| Versioned tool packages | Supply or locate compatible Python, simulators, synthesis/checking tools, solvers, board tools, and programmer drivers per OS/architecture. |
| Optional browser engines | Future convenience for validated no-install workflows; not a prerequisite for reaching full companion-backed capability. |

The normal project workflow is Open Folder through the companion, edit/save
ordinary files, then run tools against that local project. The website may also
offer example previews and artifact import without a companion; this is a
convenience rather than a second project-storage architecture. Full release
functionality uses the companion and must work on all six launch targets.

A hosted execution service is not currently planned. Do not introduce server
costs, accounts, or cloud uploads as implicit prerequisites. Keep the execution
boundary extensible without implementing extra backends prematurely.

## Required first-release workflows

| Required workflow | Browser experience | Companion/release proof |
| --- | --- | --- |
| Verilog/SystemVerilog editor | Multi-file workspace, highlighting, diagnostics, completion/hover, definition/references, project-wide rename/refactoring, includes/defines and top/test selection | Discovery and semantic edits use the real project configuration; navigation and safe cross-file edits pass language-specific fixtures. |
| Simulation and waveforms | Run/stop, logs, signal tree, buses/radix, zoom/cursor, saved layouts | Real pass/fail, waveform correctness, cancellation, and limits. Evaluate VCD first; decide FST and GTKWave layout migration explicitly. |
| Cocotb/Python tests | Edit tests, choose test functions/seed, inspect assertions and results | Packaged Python and simulator libraries work together; real Cocotb tests run, including VHDL where currently supported. |
| VHDL | Editor, entity/architecture/test discovery, simulation and source-linked diagnostics | Preserve VWB's VHDL-2008 subset, source ordering, native tests and Cocotb. Validate VHDL synthesis separately. |
| Lint and synthesis schematics | Per-tool diagnostics, schematic pan/zoom, artifact downloads | Suitable checkers run; Yosys/conversion/rendering paths work across declared platforms. Preserve raw-output fallback. |
| Formal verification | Select/edit configuration, progress, proof status, counterexample waves | Real SBY/solver success, counterexample, failure, timeout and cancellation. Initial scope follows existing VWB rather than every assertion language. |
| FPGA builds and programming | Board/constraints selection, build stages, output download, explicit program action | Build and physically program the supported boards through the local host, with board/cable/driver checks. |

The owner confirmed the existing fixed Tang Nano 9K and iCEBreaker profiles
as launch board scope. Additional boards are later scope, not launch blockers.
FPGA build success is not evidence that USB programming works. Both are release
requirements and need separate results.

Starter tests, gate-level tests, project discovery and saved-wave behavior should
follow the existing CLI wherever exposed. Starter tests must remain clearly
identified as smoke tests rather than proof of functional correctness. Preserve
the current restriction against mixing VHDL and Verilog within one hierarchy.

## Required native platform matrix

All six OS/CPU combinations below are mandatory launch targets. Minimum OS
versions remain to be pinned against tested runtime/tool packages. Archive
availability, tool availability and verified full support are distinct states.

| Required target | Native packaging gap / acceptance work |
| --- | --- |
| Linux x86-64 | Validate Ubuntu and Manjaro/Arch; do not depend on Ubuntu-only package commands. |
| Windows x86-64 | Prove native Python/Cocotb integration, VHDL, tool processes and USB programming without WSL. |
| macOS Apple Silicon | Validate runtime loading, signing/quarantine handling, tools and hardware access. |
| macOS Intel | Identify a sustainable native tool distribution; upstream suite retirement is a release blocker to solve. |
| Linux ARM64 | Prove a compatible GHDL/VHDL route and all other tools. Use native builds when no suitable OS-provided compatibility route exists. |
| Windows ARM64 | Establish native companion/runtime and compatible tool packages, allowing verified OS-provided x86-64 compatibility for individual tools; validate hardware access without WSL/Docker. |

Primary-source packaging check: OSS CAD Suite lists Python, Cocotb, Icarus,
Verilator, Yosys, SBY/solvers, nextpnr and FPGA programming tools, but currently
limits bundled GHDL and its synthesis plugin to Linux x86-64 and macOS ARM64.
Its documented targets do not include Windows ARM64, and it warns Intel Mac
distribution will stop. Therefore it is a candidate package source, not the
complete six-platform solution. Independent builds/packages or owner-approved
OS-provided compatibility routes must close gaps. These findings are documentary;
the original review alone proves no runtime support. The
[M0 evidence matrix](M0_STATUS.md) now records actual native executions and
remaining platform gaps separately.

Plan per-user installers/downloads with managed, pinned native tools. A launch
target cannot be marked supported by requiring Docker, WSL or a remote service.
The owner permits OS-provided x86-64 compatibility for individual tools on ARM.
Record each tool's binary architecture, compatibility mechanism, prerequisites
and tested workflows. Do not claim translated binaries are ARM64-native or assume
every ARM platform supplies the same compatibility mechanism. Verify Python,
simulator libraries and other in-process components as a compatible runtime set;
validate host/device-driver access separately from translated user-space tools.

Keep FPGA programming on the host. Drivers or device-permission setup may be
needed; diagnose those explicitly without requiring an elevated companion
process for normal work.

### Companion engineering work

- Reuse Python/VWB first instead of rewriting HDL orchestration in another language.
  Evaluate packaging the Python runtime so a system Python install is not assumed.
- Replace POSIX-specific process-group handling in `Workbench.run` with tested
  platform adapters. Audit `os.killpg`, signals, executable names, path handling,
  environment activation, native library loading, encodings and child cleanup.
- Test paths with spaces, Unicode names, nested includes and long Windows paths.
  Keep structured argument arrays rather than building shell command strings.
- Discover compatible user-installed tools as an advanced option; default managed
  packages should use pinned versions, integrity checks and rollbackable updates.
- Report capabilities by workflow/tool/version, including actionable repair steps.
  A missing required engine is an installation failure to fix, not a waived feature.
- Diagnose Linux device permissions and per-board Windows/macOS USB/driver needs.
  Do not require the entire companion to run as administrator/root.

## Editor workspace and beginner guide

The confirmed interaction is an editor workspace; the optional guide should use
the same projects and controls rather than maintain a second application.

Proposed layout for owner review:

- Left: project files and tabs for hierarchy, tests and examples.
- Center: HDL/Python editor, with waveform or schematic tabs/split views.
- Bottom: diagnostics, test results, tool logs, formal status and FPGA build output.
- Visible project top/test selection and connection status; context-sensitive
  Run, Lint, Synthesize, Formal, Build and Program actions.
- Optional guide: open an example, identify the top/test, run it, inspect a signal,
  fix an error, then explore synthesis. It can be skipped and reopened.

Support keyboard use and clear focus/state indicators. Visual theme, resizing,
phone/tablet expectations and preferred example sites remain to be discussed.

### Required code intelligence and safe refactoring

Provide completion, hover, go-to-definition, find references, and project-wide
rename/refactoring in addition to highlighting and diagnostics. Evaluate language
services separately for Verilog/SystemVerilog, VHDL and Python/Cocotb; host them
through the companion so they use the actual opened local project.

Use configured source roots, include paths, defines, packages and dependencies
when resolving symbols. An editor widget alone does not satisfy code-intelligence
requirements. Missing language-service support is an engineering gap to resolve,
not a reason to silently substitute text search or global string replacement.

For rename/refactoring, preview affected files, resolve the intended symbol,
detect name collisions and stale/external edits, and apply multi-file changes
without leaving a partial project. Respect open dirty buffers and provide undo
or recovery. Verify references with shadowed names, packages, ports, includes,
Unicode paths and case-sensitive/case-insensitive filesystems.

Enumerate supported semantic refactoring operations per language before claiming
general refactoring support. Test every advertised operation on representative
multi-file projects, including post-edit compilation and relevant behavior tests.
Treat ambiguous macro-generated symbols explicitly. The required reference and
project-wide symbol-rename workflows remain launch gates.

## Browser-to-companion connection gate

Prove the actual HTTPS Pages app can connect in current Firefox, Chromium and
Safari on the applicable launch platforms. Browser permissions, local-network
restrictions, mixed-content rules and cross-origin requests must be exercised;
a successful localhost-only development demo is insufficient.

Use intentional pairing, a session credential, narrow origin/host checks, and
loopback-only listening by default. Authorize an explicitly selected project,
validate path boundaries, and avoid an unauthenticated arbitrary-command API.
GitHub Pages project paths share the owner origin: origin checks alone cannot
distinguish sibling repositories, so pairing must also authenticate the session.

Start transport evaluation with authenticated HTTP requests and job polling or
fetch streaming; add WebSocket streaming only after browser support is proved.
Handle companion absent, permission denied, wrong version, expired pairing,
restart, disconnect during a job, and reconnect. Do not require browser security
flags or a public relay. A locally served copy of the same UI may be an offline
fallback, but must not silently replace the requested Pages workflow.

## Local-folder project model

The opened ordinary local folder is authoritative. Preserve its HDL/Python,
constraints, `.vwb.json`, tests and version-control layout. Existing projects
must open in place; do not require conversion to an app-private workspace or
upload to a hosted service.

- The companion opens/authorizes the folder using native UI or an explicit
  local launch action. The browser receives a scoped project handle and
  relative paths, rather than unrestricted host filesystem access.
- Reuse configured source/test/build paths. Explicitly grant any legitimate
  out-of-root include/configured directory; prevent unintended traversal and
  symlink escapes. Keep generated artifacts under the configured VWB build
  directory with its existing cleanup/ownership rules.
- Define read, change/watch, save, rename and delete operations, with revision
  tokens and visible unsaved state. Preserve case behavior, file permissions,
  Unicode paths, line endings and long Windows paths.
- Save writes must not silently overwrite changes from another editor or Git
  operation. Validate atomic-write behavior on each OS; show a conflict when
  the disk revision differs from the revision being edited.
- Tie each run to a saved project/source snapshot and label stale results after
  edits. Define whether Run first saves modified files; recover unsaved buffers
  after disconnect/restart without making recovery storage the project authority.
- Normal work requires no cloud account or upload. ZIP export may remain a
  convenience; it is not the default way to open or save a project.

## Product decisions and implementation defaults

The CPU-compatibility, editor-depth and board questions are answered. Preserve
OS-provided x86-64 tool compatibility on ARM, full requested editor capabilities,
and Tang Nano 9K/iCEBreaker launch scope alongside all prior requirements. Do not
repeat those questions or require them to be reapproved.

Provisional implementation defaults: resizable editor/results panes, dark/light
themes, explicit Save/Ctrl+S with unsaved-buffer recovery, and a skippable guide.
Use save-before-run as the initial proposal so results correspond to saved local
sources; expose unsaved state clearly and preserve external-edit conflict checks.
Minimum OS versions and tool/editor service selection should be established from
M0 evidence. Further broad product interviews are not needed to prepare M0.

Accounts, collaboration, cloud storage and hosted execution remain uncommitted
scope. New board families or new refactoring operations require explicit scope
decisions when they arise.

## Implementation sequence and acceptance gates

### M0 — Prove native execution on the confirmed support matrix

Current evidence: [M0_STATUS.md](M0_STATUS.md). Implementation and local Linux
proofs exist; this milestone's full matrix exit gate is **not yet passed**.

- Pin minimum OS versions and tool/runtime routes against the now-confirmed
  compatibility and board requirements.
  Preserve the confirmed native/no-Docker/no-WSL, local-folder, six-target scope.
- Build the smallest pairing/connectivity proof from the actual Pages origin
  to a native companion, including a denied-permission and reconnect case.
- Pin a candidate runtime/tool set and run small Verilog/SV, native HDL,
  Cocotb, VHDL, synthesis, formal and FPGA-build checks on each launch target.
- Identify supplemental tool packages or verified OS-provided CPU-compatibility
  routes where the suite is incomplete. Audit editor language-service support
  and prove cross-file references/rename on each required source language.
- Prove host programming of each supported board/cable combination; record
  hardware-tested cells separately from CI builds.
- Compare representative results with the native CLI and document limits.

**Exit gate:** reproducible install/connect evidence and a matrix of OS/CPU,
simulation, Cocotb, VHDL, lint/synthesis, formal, FPGA build and actual programming.
Unresolved required cells stay blockers; a container, WSL or remote-service
route does not satisfy the native installation requirement.

### M1 — Internal editor/run/waveform prototype

- Build the workspace, optional guide skeleton, examples, project inventory,
  local Open Folder flow, native file operations, save/recovery and conflict UI.
- Stabilize project inventory from `list --json` and job/result schemas.
- Integrate completion, hover, definition/references and project-wide semantic
  edits through companion-hosted language services, with edit previews/conflicts.
- Connect real compile/test execution, source-linked errors and a browser
  waveform; implement stop, limits, failure recovery and stale-result checks.
- Keep large waveform parsing/rendering from blocking editing and stop controls.

**Exit gate:** open a multi-file local project; complete and navigate symbols;
find references; preview/apply/undo a project-wide rename; run, inspect, break/fix,
save and reopen it. This prototype is not the complete first usable release.

### M2 — Complete language and analysis workflows

- Integrate Cocotb/Python and VHDL end to end, including native testbenches,
  source ordering, defines/includes, and the existing supported conversions.
- Add hierarchy/test discovery, starter generation, lint, synthesis schematics,
  raw artifacts, gate-level checks and saved/tagged results.
- Add formal proof/counterexample workflows and trace viewing.
- Validate larger bundled designs, arrays, buses and cancelled/failed runs.

**Exit gate:** required language and analysis workflows match native VWB's
supported behavior across the agreed platform matrix. Code-intelligence and
refactoring fixtures pass for the supported HDL and Python project constructs.

### M3 — Complete FPGA and packaging integration

- Integrate board/constraints editing, synth/PNR/pack stages, artifact download
  and actual host programming for the supported boards.
- Present board identity, selected image and programming progress clearly.
  Programming is an explicit user action.
- Finish per-platform installers/tool acquisition, repair/uninstall, version
  negotiation, logs and documentation. Validate clean-machine installation.

**Exit gate:** every launch platform has a reproducible full toolchain route;
supported board/cable combinations have recorded build and programming evidence.

### M4 — Publish the first complete usable release

- Add a Pages Actions workflow scoped to the web branch and built output.
  Configure `/verilog-workbench/`, assets/workers/WASM URLs and reload-safe routing.
- Verify real deployment, supported browsers, pairing, keyboard navigation,
  saving/export/reimport, toolchain installation and guide behavior.
- Run the full required workflow checklist; verify failures, cancellation,
  offline/disconnect behavior and recovery.
- Publish a precise OS/CPU/board support matrix and tested tool versions.

**Exit gate:** all owner-selected features work with the companion on the agreed
launch matrix. An incomplete feature set may be shown as a development preview,
but must not be labeled the requested first usable release.

## Maintainability and verification

Use a common project model, capability report, job state, diagnostic format and
artifact manifest. Include source revision and tool versions so old outputs
cannot appear to be results of newly edited code. Model queued, running,
passed, failed, cancelled and timed-out states explicitly.

Refactor narrow execution/result boundaries only as required. Preserve CLI
behavior and regressions. Tentative layout: `web/`, `companion/`, shared
contracts/fixtures, and `docs/`. Framework/editor/viewer/installer libraries
remain unselected pending a small compatibility and size check.

Use the companion's native folder/file operations across browsers; do not depend
on a browser-specific directory-picker API. Verify saved-file revisions, external
changes, reconnect recovery and source/result consistency. Browser recovery
buffers and portable exports must not replace ordinary local project files.

For documentation changes, review links and run `git diff --check`.
Implementation checks cover real tool pass/fail, discovery compatibility,
waveform/schematic correctness, cancellation, export/reopen and Pages paths.
Run OS-specific integration checks for companion changes and representative
native validation at milestones. USB programming needs physical-device evidence,
not a mocked CI pass. Use focused web checks for ordinary UI-only changes.

## Open risks and next action

Main blockers to resolve are per-platform VHDL/tool packaging, Windows process
and runtime behavior, sustainable Intel Mac support, compatible Windows ARM64
tool/runtime routes, semantic editor services, real board USB access, and
browser-to-loopback connectivity. Large waveforms, tool download
size, storage limits and UI/CLI discovery drift also need measurement.

**Next action:** close the M0 platform/browser/install/hardware evidence gaps
and supplemental tool routes described in [M0_STATUS.md](M0_STATUS.md), then
integrate the proven adapters into the M1 workspace. The formal custom-build
whitespace proof now passes. All required features, six launch targets, ordinary
local projects and native installation without Docker/WSL remain release gates.

## Concrete M0 task breakdown

These are the original M0 completion conditions. The
[current outcome and gate table](M0_STATUS.md) distinguishes implemented local
proofs from unrun, blocked and incomplete matrix cells; it does not mark the
entire milestone complete.

| ID | Task | Reviewable evidence / completion condition |
| --- | --- | --- |
| M0-01 | Define protocol and capability manifests | Versioned project/job/diagnostic/artifact/editor contracts; per-tool version/architecture/compatibility route and six-platform capability matrix. |
| M0-02 | Establish native runtime/tool acquisition | Reproducible packages/builds or verified OS-provided CPU compatibility for individual tools; cover Python, simulators, VHDL/conversion, checkers, synthesis/rendering, formal, board tools and editor services; pin sources, versions and integrity per target. |
| M0-03 | Introduce portable process execution | POSIX and Windows process-tree handling; real timeout/cancel leaves no child tools running; test environment/library paths, Unicode and spaces. |
| M0-04 | Prove native local-folder I/O | Open an existing VWB project in place, inventory/read/save files, detect external edits and conflicts, preserve configuration, recover on restart and confine paths. |
| M0-05 | Prove Pages-to-companion connection | Actual HTTPS Pages origin connects in supported browsers with pairing; permission denied, version mismatch, reconnect and unauthorized requests behave correctly. |
| M0-06 | Run the full native toolchain probe | Verilog/SV and VHDL tests, Cocotb assertions, waveforms, lint, synthesis artifacts, formal success/counterexample, FPGA build outputs; record per-target results. |
| M0-07 | Verify host FPGA programming | Actual Tang Nano 9K and iCEBreaker/cable tests on required platform rows; record driver/permission steps and device behavior separately from build tests. |
| M0-08 | Verify clean native installation | Install/launch/repair/uninstall on all six targets without Docker, WSL or assumed system Python; disclose OS-provided tool translation when used; publish accurate tested OS/tool/board matrix. |
| M0-09 | Prove semantic editor workflows | HDL and Python language-service capability inventory; configured multi-file completion/hover/definition/references and project-wide rename; preview, conflicts, undo/recovery, and post-edit correctness fixtures. |

Keep build-only, simulated, hardware-tested, blocked and not-yet-run results
distinct. Missing physical test hosts/boards remain explicit evidence gaps;
never turn an unexecuted matrix cell into a pass.

## Primary technical references

- [GitHub Pages overview](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)
- [Pages publishing sources and branch/folder choices](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
- [Creating a Pages site and static hosting restrictions](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)
- [Emscripten API limitations](https://emscripten.org/docs/porting/guidelines/api_limitations.html)
- [Emscripten filesystem overview](https://emscripten.org/docs/porting/files/file_systems_overview.html)
- [Cocotb simulator integration / GPI](https://docs.cocotb.org/en/stable/library_reference_c.html)
- [OSS CAD Suite platforms and included tools](https://github.com/YosysHQ/oss-cad-suite-build/blob/main/README.md)
- [openFPGALoader installation and device permissions](https://trabucayre.github.io/openFPGALoader/guide/install.html)
- [Chrome local network access](https://developer.chrome.com/blog/local-network-access)
- [MDN local network access](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Local_network_access)
- [MDN filesystem API](https://developer.mozilla.org/en-US/docs/Web/API/File_System_API)

References checked during initial planning on 2026-10-17 (America/Los_Angeles).
