# M0 native toolchain probe

M0 adds runnable native smoke checks and a pinned distribution inventory. It does
not certify six-platform release support. The actual local execution evidence is
recorded separately from package availability, missing tools, and physical-board
tests. No probe command programs an attached board.

The [complete native CI run](https://github.com/sfmth/verilog-workbench/actions/runs/39366706022)
at `9acd8bb8d071cf5a3660bfc16b0870e1a1271bcb` passes all 14 HDL workflows on
all six required targets, plus Arch userspace. The
[retained report](workspace-evidence/hdl-native-ci.json) includes the
actual workflow results, package receipts, architecture routes and each host's
266 unit tests and 171 CLI tests with explicit skips. This supersedes the
earlier package-gap observations below for these fixtures. See
[native HDL workspace acceptance](HDL_WORKSPACE.md) and the
[remaining M0 release gates](M0_STATUS.md).

## Run and interpret the probe

From the repository root, using an activated native runtime/tool environment:

```sh
python3 scripts/probe_toolchain.py --output discovery.json
python3 scripts/probe_toolchain.py --execute --output execution.json
python3 scripts/probe_toolchain.py --execute --path-case spaces --output spaces.json
python3 scripts/probe_toolchain.py --execute --path-case unicode-spaces --output unicode.json
```

On Windows use the selected native Python executable in place of `python3`.
Activate OSS CAD Suite with its supplied `environment.bat`/`environment.ps1`;
on Linux/macOS source its supplied `environment` before starting VWB. Merely
adding `bin` to PATH can select a different Python from the one linked with
Cocotb's simulator libraries. Do not mix ARM and x64 in-process libraries.

Without `--execute`, engines are discovered and their version commands are
queried, but every workflow stays `blocked` or `not_run`. The companion's
`capability_report(probe_versions=False)` skips version subprocesses for a fast
connection/status response. A discovered executable is not a proved workflow.

`--execute` invokes the existing `vwb.py` against independent copies of checked-in
fixtures. It requires real assertion-test markers and checks VCD contents,
Cocotb XML test cases, Yosys netlist modules, parsed SVG, SBY status and nonempty
bitstreams. Formal counterexample success specifically means a genuine SBY
`FAIL` status **and** a VCD; an execution error or solver crash does not count.
SBY status files with additional counters are supported. Physical programming
stays `not_run`/`blocked` and is never implied by packed image creation.

Evidence directories are freshly created and retained for inspection. Reports
include the host OS/CPU, interpreter, per-tool executable path, version result,
binary header architecture or launcher type, candidate compatibility mechanism,
fixture hashes, actual command/return code, elapsed time, logs and artifact
hashes. `--work-dir` chooses the evidence parent, `--only WORKFLOW` selects checks,
and `--timeout` limits each workflow and its process tree. An execution probe
exits 1 when any selected non-programming workflow is missing, fails or times
out; discovery exits 0 without pretending workflows passed. The schema is
[`report.schema.json`](../toolchains/report.schema.json).

## Reproducible candidate distribution

[`native-lock.json`](../toolchains/native-lock.json) pins OSS CAD Suite
**2026-10-18**, all five published target archives, their exact sizes and SHA-256
digests, plus supplemental GHDL and Graphviz archives and Windows Cocotb wheels.
The suite digest source is the upstream GitHub release asset API; each supplement
records its own integrity source. All five suite archive assets have now been
downloaded, checksum verified and extracted on matching native CI hosts; Windows
ARM64 uses the same x64 archive through OS translation. The
[native CI observations](M0_CI_TOOLCHAINS.md) record actual workflow outcomes.
Verified acquisition alone does not establish complete tool support or a clean
end-user installation.

The lock's target `status`, `ci_status`, `blockers` and `ci_evidence` fields retain
the original base-suite observations from run 39153200306. Acquisition receipts
copy those blockers without recomputing them after supplements; they describe
that historical suite snapshot, not the combined route's current workflow
verdict. The supplemental package receipts and
[current native execution report](workspace-evidence/hdl-native-portable-ci.json)
record the later passing routes. The base archives themselves still lack the
tools supplied by those supplements.

For manual acquisition, select the `targets[TARGET].archive_target` asset from
the lock, download exactly its `url`, verify its exact `sha256` and `size_bytes`,
then extract and activate it using the upstream installation instructions. For
example, the Linux x64 archive is `oss-cad-suite-linux-x64-20261018.tgz`:

```sh
sha256sum oss-cad-suite-linux-x64-20261018.tgz
# 639c8c0366d74dc56201729c9907bf2d8281db7f18bb025abe2e4f3f2ebaa24e
tar -xzf oss-cad-suite-linux-x64-20261018.tgz
. ./oss-cad-suite/environment
python3 scripts/probe_toolchain.py --execute --output execution.json
```

For managed acquisition, the standard-library helper downloads the exact locked
asset (or verifies an existing `--archive`), enforces size/SHA-256, extracts into
a fresh bounded staging directory, rejects traversal/external links/device
entries, writes a receipt and atomically installs a versioned directory:

```sh
python scripts/acquire_toolchain.py --target linux-x64 \
  --directory /path/to/vwb-tools --output acquisition.json \
  --probe-output execution.json --probe-work-dir /path/to/probe-evidence
```

Target IDs are `linux-x64`, `windows-x64`, `darwin-arm64`, `darwin-x64`,
`linux-arm64` and `windows-arm64`. Existing installations are never overwritten.
`--archive` reuses a local locked archive; otherwise a verified archive is cached.
The JSON gives `suitePath`, its exact archive receipt, architecture/compatibility
route and environment paths. `--probe-output` uses a constructed native
environment matching upstream activation settings and runs the real probe;
`--probe-work-dir` retains logs and test artifacts for CI. It selects the bundled
Python unless the explicit Windows supplemental runtime described below is used.
No system-Python fallback or arbitrary activation-script evaluation is used.
Windows ARM selects the pinned x64 suite through the verified OS translation
route; it is not a native ARM64 tool distribution.

`--supplementals` enables the locked supplemental routes. Intel macOS adds the
official GHDL 4.1 mcode archive. Its integrity-checked executable is exclusively
copied into `suite/bin/ghdl` so Cocotb's Python dylib dependencies resolve relative
to the suite; `GHDL_PREFIX` still selects the supplement's matching standard
libraries. Windows adds the locked GHDL 6.0.0 and Graphviz 14.1.1 archives and
requires `--windows-cocotb-python /path/to/python.exe`: an explicit external x64
MSVC CPython 3.12. The helper creates a local virtual environment and installs
the pinned Cocotb 2.1.0 dependency wheels offline, without changing global Python
packages or mixing them with the suite's MinGW Python.

Linux ARM64 uses declared external native Ubuntu GHDL LLVM packages
`ghdl-llvm=4.1.0+dfsg-0ubuntu2` and `ghdl-common=4.1.0+dfsg-0ubuntu2`.
Pass `--external-ghdl /usr/bin/ghdl-llvm --ghdl-prefix /usr/lib/ghdl/llvm/vhdl`
with `--supplementals`; the receipt records the executable and matching prefix.
These OS packages and the external Windows CPython remain prerequisites rather
than a portable managed installation. Apple Silicon retains the explicitly
receipted external Tcl-enabled Yosys overlay described below.

The [actual managed Linux acquisition](m0-evidence/toolchain-acquisition-linux-x64.json)
verified and extracted the locked archive; its [native probe](m0-evidence/toolchain-acquired-probe-linux-x64.json)
passed all 14 non-programming checks using the newly acquired suite.

This implements managed suite acquisition, not the promised finished platform
installer. Repair/uninstall, signing/notarization, clean-machine setup and the
complete tool/runtime installation remain separate packaging gates. Supplemental
acquisition still requires actual execution acceptance; the linked native run
provides that evidence for all 14 fixtures on each target. Preserve the upstream component license notices when
redistributing packages; the suite's repository license does not replace the
individual tools' licenses.

## Required platform matrix and remaining installation gates

Upstream package floors below are candidate minima. A documented floor is not
a result from running VWB on that system.

| Launch target | Pinned tool route | Candidate floor | Unresolved release proof |
| --- | --- | --- | --- |
| Linux x64, including Arch/Manjaro | OSS suite Linux x64, native binaries | Numeric kernel/glibc floor not yet established; development run uses Ubuntu 24.04 | Linux and Arch userspace smoke pass; clean native Manjaro/Arch installation and physical-board tests remain |
| Windows x64 | OSS suite Windows x64, pinned GHDL/Graphviz and local pinned Cocotb environment under explicit external x64 MSVC CPython 3.12; 14/14 pass | Windows 10, from suite documentation; full route minimum unverified | Clean setup, complete managed runtime distribution and native USB drivers |
| macOS Apple Silicon | OSS suite Darwin ARM64, including GHDL, with receipted external Tcl-enabled Yosys overlay; 14/14 pass | macOS 13, from suite documentation | Reproducible integrated package, quarantine/signing, clean setup and USB access |
| macOS Intel | Pinned Darwin x64 suite plus pinned GHDL 4.1 mcode, verified suite executable anchor and matching library prefix; 14/14 pass | macOS 13, from suite documentation | Sustainable tool distribution after upstream Intel retirement, signing/quarantine, clean setup and USB access |
| Linux ARM64 | OSS suite Linux ARM64 plus declared native Ubuntu GHDL LLVM packages and matching prefix; 14/14 pass | ARM64 Linux; numeric kernel/glibc floor not established | Managed prerequisite installation, clean setup and USB access |
| Windows ARM64 | Native ARM64 companion/runtime; suite and supplemental x64 tools/CPython use Windows' verified translation route; 14/14 pass | Windows 11 on ARM for x64 translation | Complete managed runtime distribution, ARM64 drivers, clean setup and physical programming |

OSS CAD Suite currently bundles GHDL and the Yosys GHDL plugin only on Linux x64
and Darwin ARM64. The current supplemental routes close the original GHDL
workflow gaps with a locked official Windows 6.0.0 ZIP, a locked official Intel
Mac 4.1 mcode archive and the declared Ubuntu ARM64 LLVM packages. All passed
native VHDL simulation, Cocotb/VHDL and VHDL synthesis in the linked six-platform
run. The absence of a standalone 6.0.0 Linux ARM64 or Intel Mac archive remains
a distribution constraint, rather than an unrun workflow claim. Sustainable
packages and tested clean installation remain required.

The owner permits OS-provided x64 compatibility for individual tools on ARM.
Windows 11 supports x64 user-mode emulation; Windows 10 on ARM does not. Device
drivers still need to be ARM64-native. macOS Rosetta is a candidate only when
that OS supplies it; prefer the pinned native ARM64 suite. Linux ARM64 has no
assumed built-in x64 route. Discovery inspects ELF/PE/Mach-O headers and labels
scripts as scripts; it does not guess every launcher is native or label a
translated binary ARM64. Host detection accounts for Windows process emulation
and Rosetta when the documented detection interfaces are available.

## Evidence scope

The 2026-10-18 Linux x64 execution produced these real results:

| Probe | Ordinary paths | Spaces | Unicode and spaces |
| --- | --- | --- | --- |
| Verilog, SystemVerilog and VCD (3 checks) | Passed | Passed | Passed |
| Cocotb on Icarus and GHDL (2 checks) | Passed | Passed | Passed |
| Native VHDL testbench and VCD (1 check) | Passed | Passed | Passed |
| Verilator lint, Yosys JSON, SVG schematic, VHDL synthesis (4 checks) | Passed | Passed | Passed |
| SBY proof and real counterexample VCD (2 checks) | Passed | Passed | Passed |
| Tang Nano 9K and iCEBreaker packing (2 checks) | Passed | Passed | Passed |
| Physical programming of either board | Not run | Not run | Not run |

The complete [ordinary-path report](m0-evidence/toolchains-linux-x64.json),
[space-path report](m0-evidence/toolchains-linux-x64-spaces.json) and
[Unicode/space report](m0-evidence/toolchains-linux-x64-unicode-spaces.json) each
pass all 14 non-programming checks. VWB passes a relative workdir to SBY while
keeping the actual configuration directory as cwd, preserving relative source
resolution and avoiding the shared spaced project prefix in SBY's internal
unquoted shell commands. Two real integration regressions verify a proof and a
genuine counterexample from spaced/Unicode project and configuration directories.

The additional [configured build-path proof](m0-evidence/formal-custom-build-path.json)
now also passes proof and genuine counterexample checks with `build files µ`.
When the output path contains whitespace, Unicode, shell metacharacters, or any
character outside the safe ASCII set `[A-Za-z0-9_./-]`, VWB gives SBY a unique ASCII
output basename under the actual configuration directory, then publishes only
generated results into the configured build folder. Configuration cwd and input
sources stay in place. `vwb-formal-run.json` records the historical execution
directory; internal SBY logs may retain it. Rebuilding from configuration works;
resuming a relocated internal SBY workdir is not guaranteed.

The [unactivated discovery snapshot](m0-evidence/toolchains-discovery-no-suite.json)
correctly reports all workflows blocked before native tools are on PATH. It
claims no test passes. Tool versions in the pinned nightly are development
versions, including Icarus 14.0 devel, Verilator 5.053 devel, GHDL 7.0.0-dev,
Cocotb 2.1.0.dev0+41564633 and Yosys 0.70+157. They are recorded exactly in the
report rather than described as stable upstream releases.

Checked-in reports under `docs/m0-evidence/` are execution snapshots, not a
platform-support promise. The machine used here is a Linux x64 development
environment. Native Windows, macOS, ARM machines, Tang Nano 9K and iCEBreaker
hardware were not available for the original local runs. The later
[native CI observations](M0_CI_TOOLCHAINS.md) now provide real per-platform
toolchain outcomes and explicitly retained failure causes. A container used as a development host does not
satisfy the owner's clean-machine/no-Docker installation gate.

The two FPGA fixtures run through VWB's fixed existing board profiles to the
**pack** stage. Their pins follow the repository's pre-existing pack validator
and Tang example. Do not use these small test images as a physical hardware
acceptance procedure: actual cable identity, Windows ARM driver architecture,
permissions and an explicit per-board programming/observed-behavior record are
required separately.

Separate ordinary, space and Unicode path runs prevent an upstream quoting or
encoding problem from being hidden behind a baseline pass. Any unsupported
path case remains a release blocker; a suggested workaround is not support
until implemented and exercised.

LLVM/GCC GHDL backends can need a native C compiler/linker in addition to the
suite. The Arch CI probe verified this dependency by failing with `cc not
found`; its developer host now installs GCC. A managed suite archive alone
therefore does not establish complete native prerequisites. The Windows
archive inspection also confirmed absent Cocotb and Graphviz packages; its
native `verilator_bin.exe` supplies lint without the Perl launcher. The third
native CI run verified Windows lint while Cocotb, Graphviz and GHDL workflows
were still blocked. Those are historical failures: the current supplemental
routes passed all 14 workflows on both Windows targets in run 39364175524.

The optional Mac ARM Tcl-enabled Yosys candidate is an explicitly external
Homebrew dependency. `--overlay-tool yosys=/absolute/path/to/yosys` preserves
the original suite launcher under `.vwb-overlays/originals`, installs an
absolute symlink in `suite/bin`, and records both executable hashes and the
external binary architecture in the acquisition receipt. This survives the
suite Python launcher's PATH reset; a PATH prefix alone does not. Such an
installation is marked nonportable and depends on that external package.
The locked downloaded archive retains its original digest; its contents are
not described as the external Yosys distribution.

## Primary sources checked 2026-10-18

- [OSS CAD Suite platform/tool inventory and environment activation](https://github.com/YosysHQ/oss-cad-suite-build/blob/main/README.md)
- [Pinned suite release](https://github.com/YosysHQ/oss-cad-suite-build/releases/tag/2026-10-18) and [release asset digests](https://api.github.com/repos/YosysHQ/oss-cad-suite-build/releases/tags/2026-10-18)
- [GHDL 6.0.0 release and standalone Windows distributions](https://github.com/ghdl/ghdl/releases/tag/v6.0.0)
- [GHDL native build requirements and backend architectures](https://ghdl.github.io/ghdl/development/building/index.html)
- [Microsoft: x64 emulation on Windows ARM and native driver requirement](https://learn.microsoft.com/en-us/windows/arm/apps-on-arm-x86-emulation)
- [Apple: Rosetta availability and native application architectures](https://support.apple.com/en-us/102527)
