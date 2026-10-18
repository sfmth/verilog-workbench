# VWB website port plan

Status: owner requirements recorded; platform packaging and project storage
choices remain open. No implementation has started.

Started: 2026-10-17 (America/Los_Angeles).

Planning branch: `web-port-plan`.
Inspected baseline: `master` at `3e384347b93c69266b055c65eda8a6cd308d4536`.

## Goal and confirmed scope

Port Verilog Workbench into an editor workspace hosted on GitHub Pages, with
an optional beginner guide. The owner accepts a local companion, provided it
is very cross-platform. Continue asking focused product questions while
developing this plan separately from the existing CLI.

Owner decisions recorded on 2026-10-17 (America/Los_Angeles):

- **Hosting:** GitHub Pages. A very cross-platform local companion is acceptable.
- **First usable release:** Verilog/SystemVerilog editing, simulation and
  waveforms; Cocotb/Python tests; lint and synthesis schematics; FPGA builds
  and programming; VHDL; and formal verification are all required.
- **Interface:** editor workspace with an optional beginner guide.

Internal development checkpoints may implement smaller slices, but they do
not satisfy the requested first usable release until all required workflows
work on the agreed platform/board matrix. No required feature is deferred
merely because browser-only execution is difficult.

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

Use GitHub Pages for the browser workspace and plan a cross-platform local
companion for the complete native toolchain. This is the practical architecture
proposed from the owner's acceptance of a companion; it is not a promise that
GitHub Pages itself executes native tools.

| Layer | Responsibility |
| --- | --- |
| GitHub Pages app | Editor, file/test/hierarchy views, run controls, diagnostics, waveform viewer, schematics, optional beginner guide. |
| Local companion | Access an explicitly opened project, run VWB tools, return structured results/artifacts, and program attached FPGA hardware. |
| Versioned tool packages | Supply or locate compatible Python, simulators, synthesis/checking tools, solvers, board tools, and programmer drivers per OS/architecture. |
| Optional browser engines | Future convenience for validated no-install workflows; not a prerequisite for reaching full companion-backed capability. |

Without a companion, the website should still open examples, allow editing and
portable import/export, and inspect available waveforms/schematics. The UI must
say when a requested action needs the companion. With a configured companion,
all owner-selected release workflows must work on the agreed support matrix.

A hosted execution service is not currently planned. Do not introduce server
costs, accounts, or cloud uploads as implicit prerequisites. Keep the execution
boundary extensible without implementing extra backends prematurely.

## Required first-release workflows

| Required workflow | Browser experience | Companion/release proof |
| --- | --- | --- |
| Verilog/SystemVerilog editor | Multi-file workspace, includes/defines, top/test selection, useful errors | Discovery, dependency closure, native HDL tests and selected language subset agree with VWB. |
| Simulation and waveforms | Run/stop, logs, signal tree, buses/radix, zoom/cursor, saved layouts | Real pass/fail, waveform correctness, cancellation, and limits. Evaluate VCD first; decide FST and GTKWave layout migration explicitly. |
| Cocotb/Python tests | Edit tests, choose test functions/seed, inspect assertions and results | Packaged Python and simulator libraries work together; real Cocotb tests run, including VHDL where currently supported. |
| VHDL | Editor, entity/architecture/test discovery, simulation and source-linked diagnostics | Preserve VWB's VHDL-2008 subset, source ordering, native tests and Cocotb. Validate VHDL synthesis separately. |
| Lint and synthesis schematics | Per-tool diagnostics, schematic pan/zoom, artifact downloads | Suitable checkers run; Yosys/conversion/rendering paths work across declared platforms. Preserve raw-output fallback. |
| Formal verification | Select/edit configuration, progress, proof status, counterexample waves | Real SBY/solver success, counterexample, failure, timeout and cancellation. Initial scope follows existing VWB rather than every assertion language. |
| FPGA builds and programming | Board/constraints selection, build stages, output download, explicit program action | Build and physically program the supported boards through the local host, with board/cable/driver checks. |

Start FPGA coverage with the existing fixed Tang Nano 9K and iCEBreaker profiles.
This preserves current VWB scope; other boards remain a separate product choice.
FPGA build success is not evidence that USB programming works. Both are release
requirements and need separate results.

Starter tests, gate-level tests, project discovery and saved-wave behavior should
follow the existing CLI wherever exposed. Starter tests must remain clearly
identified as smoke tests rather than proof of functional correctness. Preserve
the current restriction against mixing VHDL and Verilog within one hierarchy.

## What "very cross-platform" means in this plan

Proposed launch OS families are Windows, Linux (including Manjaro/Arch), and
macOS. Minimum versions and CPU architectures await owner input. Package support,
tool availability and verified end-to-end support are three different things.

| Candidate target | Evidence / gap to resolve before claiming support |
| --- | --- |
| Linux x86-64 | OSS CAD Suite candidate; validate Ubuntu and Manjaro/Arch rather than relying on Ubuntu-only package commands. |
| Windows x86-64 | Native suite candidate; prove Python/Cocotb integration and a VHDL route. Decide whether WSL may be a fallback. |
| macOS Apple Silicon | Native suite candidate; validate signing/quarantine handling, runtime loading, and hardware access. |
| macOS Intel | Suite currently lists it but warns distribution will stop soon; identify a sustainable packaging route if required. |
| Linux ARM64 | Suite exists, but bundled GHDL is not listed for this target; needs a separate validated VHDL route. |
| Windows ARM64 | Not listed by the inspected suite documentation; requires its own toolchain strategy if requested. |

Primary-source packaging check: OSS CAD Suite lists Python, Cocotb, Icarus,
Verilator, Yosys, SBY/solvers, nextpnr and FPGA programming tools, but currently
limits bundled GHDL and its synthesis plugin to Linux x86-64 and macOS ARM64.
Therefore downloading one suite archive is not proof of complete VWB support.
Investigate independent GHDL packages/builds for gaps and test simulation and
synthesis separately. These are documentary findings, not completed runtime tests.

Prefer a normal per-user companion download/installer with managed, versioned
tools. Whether Docker/WSL is allowed as a fallback is an owner decision.
Containers may be useful execution adapters, but must not silently become the
only route to a supposedly native cross-platform app. Keep hardware programming
on the host or prove a documented device bridge; do not assume container USB access.

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

## Browser-to-companion connection gate

Prove the actual HTTPS Pages app can connect in current Firefox and Chromium,
then Safari if macOS is a launch target. Browser permissions, local-network
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

## Questions still to resolve

1. **Installation:** Must full functionality be native without Docker/WSL, is
   native preferred with those fallbacks, or are containers/WSL acceptable as
   the standard installation route?
2. **Project storage:** Open/edit ordinary local folders through the companion,
   keep browser-managed projects with import/export, or provide both? Define
   the authoritative copy, save behavior and conflict handling accordingly.
3. **Launch targets:** Which OS/CPU combinations need verified full support?
   Confirm Windows, Linux, Apple Silicon/Intel Macs and any ARM Linux/Windows needs.

After these answers, ask only unresolved questions that affect implementation:
additional FPGA boards/cables, intended audience, project scale, offline use,
phone/tablet use, visual references and GitHub integration. Accounts, cloud
storage, collaboration and hosted execution remain uncommitted scope.

## Implementation sequence and acceptance gates

### M0 — Prove the companion path and lock the support matrix

- Record remaining installation, storage and OS/architecture decisions.
- Build the smallest pairing/connectivity proof from the actual Pages origin
  to a native companion, including a denied-permission and reconnect case.
- Pin a candidate runtime/tool set and run small Verilog/SV, native HDL,
  Cocotb, VHDL, synthesis, formal and FPGA-build checks on each launch target.
- Identify supplemental tool packages where the suite is incomplete.
- Prove host programming of each supported board/cable combination; record
  hardware-tested cells separately from CI builds.
- Compare representative results with the native CLI and document limits.

**Exit gate:** reproducible install/connect evidence and a matrix of OS/CPU,
simulation, Cocotb, VHDL, lint/synthesis, formal, FPGA build and actual programming.
Unresolved required cells stay blockers. Agree on any fallback with the owner.

### M1 — Internal editor/run/waveform prototype

- Build the workspace, optional guide skeleton, examples, project model and
  the selected save/import/export route.
- Stabilize project inventory from `list --json` and job/result schemas.
- Connect real compile/test execution, source-linked errors and a browser
  waveform; implement stop, limits, failure recovery and stale-result checks.
- Keep large waveform parsing/rendering from blocking editing and stop controls.

**Exit gate:** open, edit, run, inspect, break/fix, save and reopen a multi-file
project. This internal prototype is not the complete first usable release.

### M2 — Complete language and analysis workflows

- Integrate Cocotb/Python and VHDL end to end, including native testbenches,
  source ordering, defines/includes, and the existing supported conversions.
- Add hierarchy/test discovery, starter generation, lint, synthesis schematics,
  raw artifacts, gate-level checks and saved/tagged results.
- Add formal proof/counterexample workflows and trace viewing.
- Validate larger bundled designs, arrays, buses and cancelled/failed runs.

**Exit gate:** required language and analysis workflows match native VWB's
supported behavior across the agreed platform matrix.

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

For project storage, implement the owner's chosen model with explicit save
status, atomic local writes where applicable and portable exports. Browser file
access must have a cross-browser route through the companion/import-export,
rather than require a single browser's folder-picker API.

For documentation changes, review links and run `git diff --check`.
Implementation checks cover real tool pass/fail, discovery compatibility,
waveform/schematic correctness, cancellation, export/reopen and Pages paths.
Run OS-specific integration checks for companion changes and representative
native validation at milestones. USB programming needs physical-device evidence,
not a mocked CI pass. Use focused web checks for ordinary UI-only changes.

## Open risks and next action

Main blockers to resolve are per-platform VHDL/tool packaging, Windows process
and runtime behavior, sustainable Intel Mac support if required, real board USB
access, and browser-to-loopback connectivity. Large waveforms, tool download
size, storage limits and UI/CLI discovery drift also need measurement.

**Next action:** collect the three installation/storage/platform answers, then
turn M0 into small testable tasks. All six requested feature groups and the
editor-plus-optional-guide interaction remain fixed requirements.

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
