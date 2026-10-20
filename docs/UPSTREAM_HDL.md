# Upstream HDL acceptance

VWB requires **23 distinct upstream designs** in CI: **15 functional simulations**
and **8 parsing/elaboration checks**. These are separate hardware top modules;
rerunning one design with another parameter or simulator does not increase the
count. All 15 functional cases also run an expected-value corruption control,
which must fail with a real simulator assertion and without the success marker.

| Project | Required designs | Coverage |
| --- | --- | --- |
| [PicoRV32](https://github.com/YosysHQ/picorv32) | CPU, iterative multiplier, fast multiplier, divider | RV32IM program with seven checked stores, load/store stalls; PCPI arithmetic, signed/unsigned corner cases, division by zero and overflow |
| [Ibex](https://github.com/lowRISC/ibex) | `ibex_core` | Complete default core elaboration with its upstream packages and primitives |
| [CV32E40P](https://github.com/openhwfoundation/cv32e40p) | `cv32e40p_core` | Complete default non-FPU core elaboration with the flip-flop register file |
| [SERV](https://github.com/olofk/serv) | `serv_top` | Complete default core elaboration |
| [NVDLA](https://github.com/nvdla/hw) | APB-to-CSB, CACC int8, SDP integer ALU, SDP integer multiplier | Bus stalls, address/data conversion, signed arithmetic, saturation, truncation/rounding, bypass and output backpressure |
| [Verilog AXIS](https://github.com/alexforencich/verilog-axis) | FIFO, register, pipeline FIFO, width adapter, asynchronous FIFO; arbiter mux | Five functional stream scoreboards checking 70 bytes in ten frames, sidebands, ordering and backpressure; mux elaboration |
| [Verilog AXI](https://github.com/alexforencich/verilog-axi) | AXI-lite RAM; AXI RAM, AXI-lite register | RAM reads/writes, byte strobes, channel skew and response backpressure; two elaboration checks |
| [Verilog UART](https://github.com/alexforencich/verilog-uart) | UART | Eight transmitted/received bytes, receive backpressure and error checks |
| [Verilog I2C](https://github.com/alexforencich/verilog-i2c) | I2C master, I2C slave | Parsing and elaboration |

The [design manifest](../toolchains/upstream-designs.json) includes the family
recipe files. The runner rejects duplicate project/top pairs and a corpus smaller
than 20. Missing files, failed commands, timeouts, missing assertion markers and
unrejected negative controls fail the gate. Static elaboration is recorded as
`lint_elaboration`, with no functional correctness claim. Directed simulations
are integration checks, not exhaustive ISA compliance, formal verification or
complete accelerator verification. In particular, the four NVDLA blocks do not
constitute a complete generated `nv_small` accelerator or SystemC/UVM regression.
VexRiscv generator sources are acquired for future integration; an ungenerated
Scala CPU is excluded from the design count.

## Run locally

Use the existing native tools, or acquire the pinned Linux suite. Verilator
simulation requires GNU Make and a C++ compiler; `setup.sh --full` and the Docker
image install those prerequisites. Icarus and GHDL remain the automatic defaults
for Verilog/SystemVerilog and VHDL respectively. The new Verilator timed/asserting
and Cocotb VPI simulation routes have required Linux acceptance; the existing
14-workflow native platform matrix remains separate.

```bash
python scripts/acquire_toolchain.py --target linux-x64 \
  --directory .m0-tools/hdl --output upstream-tool-acquisition.json
python scripts/acquire_upstream_hdl.py \
  --directory .upstream-hdl --output upstream-acquisition.json
python scripts/probe_upstream_hdl.py \
  --acquisition upstream-acquisition.json \
  --toolchain-acquisition upstream-tool-acquisition.json \
  --output upstream-results.json
```

Omit `--toolchain-acquisition` to use the current Python and PATH. For a focused
rerun, add repeatable `--only DESIGN-ID`; its report explicitly says
`selected_subset` and never sets `complete_corpus_passed`. Fresh staged VWB
projects, full command logs and generated outputs remain under
`.upstream-evidence/`. Open a staged project with VWB or the browser companion to
inspect its sources, rerun tests and explore its hierarchy.

The CLI and browser support explicit simulator selection:

```bash
vwb test my_top --simulator verilator --waves --wave-format vcd
vwb test my_top --simulator verilator --test test/test_my_top.py --test-language cocotb
vwb wave my_top --simulator verilator --wave-format fst
```

## Reproducibility and licensing

[The upstream source lock](../toolchains/upstream-hdl-lock.json) pins ten official
repositories by full commit, archive SHA256 and exact archive size. Acquisition
validates every archive entry, safely preserves internal relative symlinks,
retains all licenses/notices, and rechecks the entire installed tree on reuse.
It does not run upstream scripts or modify upstream RTL. Downloaded sources and
build trees are ignored by Git and excluded from the Docker build context.
The retained source archives total about 110 MB compressed.

NVDLA's root license is **NVIDIA Open NVDLA License and Agreement v1.0**; the lock
records it as `LicenseRef-NVIDIA-Open-NVDLA-1.0`. Other projects and vendored
components retain their original license files. Acquiring sources for testing
does not relicense them under VWB's license.

Reports retain project revisions, license metadata, archive/tree hashes,
selected source and bench hashes, exact VWB commands, results, timings and hashed
positive/negative logs. The reusable
[required workflow](../.github/workflows/upstream-hdl.yml) runs all designs and
real Verilator/Cocotb/waveform controls. The normal CI, Pages deployment and Docker
release workflows call this gate; Pages deployment and image publication depend
on its success. No `continue-on-error` or simulator-availability skip substitutes
for a required design pass. CI uploads the provenance and logs for inspection.

## Retained acceptance evidence

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
passes all 31 workflows in Chromium 151, including five new Verilator controls.
The [public HTTPS report](workspace-evidence/upstream-hdl-public-ci-linux-x64.json)
also passes all 31 after deployment. [Pages CI](https://github.com/sfmth/verilog-workbench/actions/runs/39372047711)
passes build, the required corpus, deployment and public verification. The
[retained CI summary](workspace-evidence/upstream-pages-ci.json) also records
the existing editor, source, workspace and file lifecycle checks on both origins.
The added engine's functional acceptance is Linux; discovery on another native
platform does not substitute for simulator execution evidence.
