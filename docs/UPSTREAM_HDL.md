# Upstream HDL acceptance

The primary gate runs **30 original upstream testbench flows for 29 distinct
hardware tops**. It requires at least **300 executed upstream JUnit cases**, plus
the cores' firmware verdicts and NVDLA's golden output CRCs. The projects' HDL,
benches, firmware sources, generators and build drivers run unchanged in copied
workspaces. A separate set of **23 VWB directed component and elaboration checks**
provides supplemental coverage.

Project verification reuses VWB's existing Verilator and Cocotb support. Its
project flow route preserves the upstream build entrypoint and its
simulator/dependency requirements. `vwb flow` and the browser's **Project
testbench** action use that route; a source file's successful elaboration does
not establish that its original testbench ran.

## Required original testbenches

| Project | Distinct designs | Original entrypoints and checks |
| --- | --- | --- |
| [Verilog AXIS](https://github.com/alexforencich/verilog-axis) | 16 | Original Cocotb Makefiles for adapters, arbitration, asynchronous and synchronous FIFOs, broadcast/demux/mux, COBS encode/decode, pipeline and shift-register blocks |
| [Verilog AXI](https://github.com/alexforencich/verilog-axi) | 4 | Original Cocotb Makefiles for AXI and AXI-lite RAM/register blocks |
| [Verilog UART](https://github.com/alexforencich/verilog-uart) | 2 | Original UART transmitter and receiver Cocotb Makefiles |
| [Verilog I2C](https://github.com/alexforencich/verilog-i2c) | 3 | Original MyHDL/Icarus VPI benches for master, AXI-lite master and slave |
| [PicoRV32](https://github.com/YosysHQ/picorv32) | 1 | Original `make test` and `make test_verilator`, GCC-built firmware and ISA assembly tests; both require `ALL TESTS PASSED.` |
| [SERV](https://github.com/olofk/serv) | 1 | Original FuseSoC/Verilator C++ bench with checked-in Zephyr firmware; requires its boot banner and Hello World UART output |
| [Ibex](https://github.com/lowRISC/ibex) | 1 | Original Simple System firmware Makefile, FuseSoC target and C++ bench; requires Hello/signature output, timer-interrupt Tick/Tock and clean software-requested termination |
| [NVDLA](https://github.com/nvdla/hw) | 1 | Original generator builds the complete `nv_small` top, then the original Verilator C++ trace player checks four upstream golden CRCs: SDP pass-through, convolution, average pooling and CDP LRN |

PicoRV32's two simulator flows count as one design. The peripheral Makefiles use
one upstream default parameter configuration and execute every generated test
for that configuration; this is not the entire upstream parameter matrix. SERV
and Ibex Simple System establish firmware execution and directed system behavior,
not exhaustive ISA compliance. NVDLA's four complete-top traces do not establish
all configurations, all traces or the separate UVM/SystemC C-model cross-check.
The [NVDLA receipt](workspace-evidence/upstream-native-nvdla-linux-x64.json) also
retains two exploratory traces that did not pass.

Recipes live in [stream/serial flows](../upstream/benchmarks/stream-serial.json),
[core flows](../upstream/benchmarks/core-flows.json) and
[NVDLA flows](../upstream/benchmarks/nvdla-flows.json). The
[simulator requirements audit](../upstream/simulator-requirements.json) records
original entrypoints and dependencies for all ten acquired projects. More core
flow detail is in [the upstream core audit](../upstream/benchmarks/CORE_FLOWS.md).

## Simulator and dependency environments

The required Linux x64 environment uses the existing pinned OSS CAD Suite, with
separate hash-pinned upstream verification profiles:

- Original Cocotb 1.x APIs, `cocotb-test`, protocol extensions, Pytest and MyHDL,
  including the Icarus MyHDL VPI plugin compiled against the selected simulator.
- FuseSoC 2.4.6 and Edalize 0.6.8 for the upstream core targets. The profile matches
  the current SERV core metadata and the suite's CPython 3.11 runtime.
- xPack RISC-V GCC 14.2.0-3 to build original PicoRV32 and Ibex firmware.
- Pinned Perl XML/YAML modules and host C/C++, Clang, Java, XML parser and
  Capture::Tiny prerequisites for the NVDLA generator; `libelf` development files
  for Ibex Simple System.

The Python and Perl artifact locks are
[upstream-python-lock.json](../toolchains/upstream-python-lock.json) and
[upstream-native-dependencies.json](../toolchains/upstream-native-dependencies.json).
The [RISC-V compiler lock](../toolchains/upstream-riscv-toolchain-lock.json)
records the official release URL, archive size, SHA256 and retained licenses.
These additional profiles currently target Linux x64. The host compiler and
system development packages remain native platform prerequisites.

The original **CV32E40P Questa example**, **Ibex UVM/Xcelium/Spike regression**,
**NVDLA VCS/UVM/SystemC cross-check** and **VexRiscv generated-CPU regression** are
recorded as `external_required`. They are excluded from passing CI coverage.
The commercial flows require working licenses and compatible libraries; VexRiscv
needs its JVM/Scala/SpinalHDL build and separately pinned regression-data
submodule. Finding a simulator executable does not verify a license or execute
its testbench. These cases remain visible for configuration and audit.

## Run locally

On Ubuntu/Debian, install the native prerequisites before acquiring the pinned
archives:

```bash
sudo apt-get install gcc g++ make clang default-jre-headless perl \
  libxml-parser-perl libxml-sax-perl libcapture-tiny-perl libelf-dev zlib1g-dev
python scripts/acquire_toolchain.py --target linux-x64 \
  --directory .m0-tools/hdl --output upstream-tool-acquisition.json
python scripts/acquire_upstream_hdl.py \
  --directory .upstream-hdl --output upstream-acquisition.json
python scripts/acquire_riscv_toolchain.py \
  --directory .upstream-tools --output upstream-riscv-acquisition.json
python scripts/acquire_upstream_test_tools.py \
  --directory .upstream-tools \
  --toolchain-acquisition upstream-tool-acquisition.json \
  --output upstream-test-tools.json
python scripts/probe_upstream_testbenches.py \
  --acquisition upstream-acquisition.json \
  --toolchain-acquisition upstream-tool-acquisition.json \
  --test-tools upstream-test-tools.json \
  --riscv-acquisition upstream-riscv-acquisition.json \
  --work-dir .upstream-evidence --output upstream-native-results.json
```

The runner revalidates the acquired source trees, creates private working
copies, activates each declared profile and runs the original entrypoint through
VWB. It records command logs, result-file hashes, source provenance and any
changed original file. A changed original source, missing dependency, failed
command, timeout, absent/stale result, skipped JUnit case or insufficient test
count fails the gate. Source hashes and provenance metadata alone cannot pass a
testbench. Repeatable `--only FLOW-ID` runs report `selected_subset` and never set
`complete_required_flows_passed`, even when every selected flow passes.

For an editable CLI/browser project, stage a fresh copy with its configured
flows and pinned local environments:

```bash
python scripts/probe_upstream_testbenches.py \
  --acquisition upstream-acquisition.json \
  --toolchain-acquisition upstream-tool-acquisition.json \
  --test-tools upstream-test-tools.json \
  --riscv-acquisition upstream-riscv-acquisition.json \
  --stage-project picorv32 \
  --stage-directory .upstream-evidence/picorv32-project \
  --output upstream-project.json
vwb --root .upstream-evidence/picorv32-project flow --list
vwb --root .upstream-evidence/picorv32-project flow picorv32-upstream-regression
python -m companion --project .upstream-evidence/picorv32-project \
  --origin https://sfmth.github.io --port 0
```

Staging requires a new destination directory. The saved `.vwb-flows.json`
activates the local dependency profile and private driver cache directories.
In the workspace, choose **Project testbench**, leave the flow name empty and
run to discover configured flows, then select a flow and run it. Simulator
changes are accepted only when that project declares the corresponding preset.
Reports and logs are archived in the project's normal build directory and can
be downloaded through Artifacts.

Staging `verilog-axis` also exposes the original Makefile's declared simulator
preset. For example, with that project staged as
`.upstream-evidence/axis-project`:

```bash
vwb --root .upstream-evidence/axis-project flow upstream-axis-adapter \
  --simulator verilator --timeout 300
```

CI requires all nine original adapter JUnit cases on that Verilator preset in
addition to the Icarus run in the primary matrix. This increases simulator
coverage without increasing the distinct-design count.

## Supplemental directed corpus

The earlier [23-design manifest](../toolchains/upstream-designs.json) remains a
required supplemental gate: 15 VWB-authored functional checks with corruption
controls, plus eight parsing/elaboration checks. Its NVDLA component tests cover
APB/CSB conversion, CACC arithmetic and SDP ALU/multiply behavior independently
of the original complete-top traces. Run it with:

```bash
python scripts/probe_upstream_hdl.py \
  --acquisition upstream-acquisition.json \
  --toolchain-acquisition upstream-tool-acquisition.json \
  --output upstream-results.json
```

Elaboration is recorded as `lint_elaboration`; its successful result is not a
functional testbench verdict. `vwb test` and `vwb wave` continue to handle the
usual discovered HDL/Cocotb tests, including explicit Icarus, Verilator and GHDL
selection. The project flow route preserves upstream Make/FuseSoC drivers and
more complex dependency chains.

## Reproducibility, licenses and CI

[The upstream source lock](../toolchains/upstream-hdl-lock.json) pins ten official
repositories by full commit, archive SHA256 and exact archive size. Acquisition
validates every archive entry, preserves safe internal relative links, retains
all licenses/notices and rechecks the entire installed tree on reuse. Downloaded
sources, compilers, isolated dependency profiles and generated build trees are
ignored by Git and excluded from the Docker build context. Source acquisition
does not run upstream scripts; verification runs them explicitly in staged
projects. The source archives total about 110 MB compressed, and the separately
pinned compiler archive is about 344 MB.

NVDLA's root license is **NVIDIA Open NVDLA License and Agreement v1.0**; the lock
records `LicenseRef-NVIDIA-Open-NVDLA-1.0`. Other projects, compiler components and
vendored libraries retain their original license files. Acquiring these sources
for testing does not relicense them under VWB's license.

The [required reusable workflow](../.github/workflows/upstream-hdl.yml) runs both
the original benches and supplemental corpus, checks exact required result
identities and distinct hardware tops, and retains logs/JUnit/provenance. Normal
CI, Pages deployment and Docker image release call this gate. A passing subset,
external requirement or available simulator cannot substitute for complete
required original-bench coverage. Pages deployment and image publication depend
on that result. A separate rendered-browser check discovers and runs the
original PicoRV32 bench, then verifies downloaded result/log bytes against the
native artifacts.

The [local original-bench browser receipt](workspace-evidence/upstream-native-browser-linux-x64.json)
records seven passing checks, including original core execution, unchanged
source inputs and exact result/log downloads. The NVDLA receipt above separately
records four passing complete-top golden-CRC traces and a deliberately corrupted
CRC that the original bench rejects.

The [local original-bench receipt](workspace-evidence/upstream-native-local-linux-x64.json)
records all 30 flows passing across 29 distinct designs, with 300 executed JUnit
cases and unchanged original inputs. Receipts identify their tested source
hashes and distinguish local execution from hosted CI.

The [hosted original-bench receipt](workspace-evidence/upstream-native-ci-linux-x64.json)
records all 30 flows passing across 29 distinct designs at
`cfc4a4c0b236845a495884e227c389ed97695a1c`: 300 executed JUnit cases with zero
failures, errors or skips, unchanged original inputs, nine additional AXIS
adapter cases using its original Verilator preset and seven rendered-browser
checks. The same job passes all 23 supplemental designs and rejects all 15
deliberately corrupted expected values. The receipt retains report, log, JUnit,
source and artifact hashes verified against the downloaded CI artifact.

[Pages CI](https://github.com/sfmth/verilog-workbench/actions/runs/39384709536)
passes build, the required upstream gate, deployment and public verification.
[Native CI](https://github.com/sfmth/verilog-workbench/actions/runs/39384709436)
passes all 14 jobs at the same commit. The
[public HTTPS original-bench receipt](workspace-evidence/upstream-native-browser-public-https.json)
separately records seven passing checks against the deployed workspace,
including the original PicoRV32 firmware regression, unchanged source inputs,
exact archived report/log downloads and matching loaded runtime hashes.

## Earlier supplemental acceptance evidence

The [hosted corpus report](workspace-evidence/upstream-corpus-ci-linux-x64.json)
passes all 23 designs and all 15 expected-value failure controls at
`27905aa5523863aecaf2b5480618fe69ef844dd8`. The same required job passes 17 source
acquisition tests, 22 runner/verdict tests, and 11 simulator tests with no skips;
six of those simulator tests invoke real native Verilator/Cocotb engines.
The [local corpus](workspace-evidence/upstream-corpus-local-linux-x64.json),
[engine/cancellation receipt](workspace-evidence/upstream-verilator-engine-local-linux-x64.json)
and [five focused browser checks](workspace-evidence/upstream-verilator-browser-local-linux-x64.json)
retain independent Linux evidence and original report hashes.

[Native CI](https://github.com/sfmth/verilog-workbench/actions/runs/39372047666)
passes all 14 jobs. The [native receipt](workspace-evidence/upstream-native-ci.json)
records all six platform test/package jobs, their actual optional/platform skips,
and all six 14-workflow toolchain suites plus Arch's 14 workflows. It also retains
the earlier Windows temporary-file failure and the passing corrected run;
Windows ARM64's x64 tool compatibility remains explicitly disclosed.

The [built-browser report](workspace-evidence/upstream-hdl-browser-ci-linux-x64.json)
passes all 31 workflows in Chromium 151, including five Verilator controls.
The [public HTTPS report](workspace-evidence/upstream-hdl-public-ci-linux-x64.json)
also passes all 31 after deployment. [Pages CI](https://github.com/sfmth/verilog-workbench/actions/runs/39372047711)
passes build, the required corpus, deployment and public verification. The
[retained CI summary](workspace-evidence/upstream-pages-ci.json) also records
the existing editor, source, workspace and file lifecycle checks on both origins.
Those Verilator controls have Linux functional acceptance; discovery on another native
platform does not substitute for simulator execution evidence.
