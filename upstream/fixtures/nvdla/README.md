# NVDLA block acceptance

These benches test four distinct blocks from the official
[NVDLA hardware repository](https://github.com/nvdla/hw/tree/1a65f1f5b48268accaa47c95f95c2601918be095)
at commit `1a65f1f5b48268accaa47c95f95c2601918be095`. The source archive and its
NVIDIA Open NVDLA License and Agreement v1.0 are acquired through
`toolchains/upstream-hdl-lock.json`.
The upstream RTL is copied byte for byte into the generated VWB projects; these
benches are VWB-authored acceptance code.

| DUT | Functional checks |
| --- | --- |
| `NV_NVDLA_apb2csb` | APB setup/access distinction, address conversion, write backpressure, delayed read response, suppression of repeated CSB read requests |
| `NV_NVDLA_CACC_CALC_int8` | Twelve signed accumulation vectors, optional prior accumulator, 34-bit partial saturation, 32-bit final saturation, positive and negative round/truncate cases, valid pipeline drain |
| `NV_NVDLA_SDP_HLS_X_int_alu` | Signed max/min/add, shifted operands, 33-bit sum, register and channel operands, bypass, stable output during backpressure and no duplicate output |
| `NV_NVDLA_SDP_HLS_X_int_mul` | Signed multiplication, wide product, channel operand, positive and negative PReLU, bypass, stable output during backpressure and no duplicate output |

All four use Icarus Verilog with `SYNTHESIS`, `NO_PLI`, and `DISABLE_FUNCPOINT`
defined. The selected source files are already ordinary Verilog in the upstream
tree and require no source generation or local RTL patches. `SYNTHESIS` disables
simulation-only upstream assertion-library hooks; the acceptance benches retain
their explicit `$fatal` checks. Each bench has a bounded timeout and emits
`VWB_UPSTREAM_PASS <design-id>` only after all its checks pass. The
`+VWB_NEGATIVE_CONTROL` simulator argument changes one expected result; the
required corpus gate checks that this invocation fails and emits no success
marker. The four benches also pass with VWB's explicit `--simulator verilator`.

For an acquired project staged by the upstream runner, an individual invocation is:

```sh
python vwb.py --root /path/to/nvdla-sdp-alu --verbose test \
  NV_NVDLA_SDP_HLS_X_int_alu \
  --test test/tb_nvdla_sdp_alu.sv --test-top tb_nvdla_sdp_alu \
  --test-language verilog --simulator icarus \
  --define SYNTHESIS --define NO_PLI --define DISABLE_FUNCPOINT
```

These are functional block tests. They do not validate the complete accelerator,
network execution, numerical conformance for all inputs, implementation timing,
or the upstream UVM/SystemC verification environment. The configurable full
`NV_nvdla` design requires the official `nv_small` generation flow, including a
C preprocessor, Python configuration generator, Perl/ePerl RTL generation and
register/interface generators. The upstream
[Verilator file list](https://github.com/nvdla/hw/blob/1a65f1f5b48268accaa47c95f95c2601918be095/verif/verilator/verilator_nv_small.f)
targets generated `outdir/nv_small` files. The
[environment guide](https://nvdla.org/hw/v2/environment_setup_guide.html)
documents the broader build and verification dependencies. Full-core generation,
elaboration and trace replay are separate acceptance scopes and are not reported
as passing by these four block checks.
