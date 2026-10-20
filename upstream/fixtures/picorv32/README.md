# PicoRV32 acceptance

These VWB-authored benches run the unmodified `picorv32.v` from the official
[PicoRV32 repository](https://github.com/YosysHQ/picorv32/tree/ef203c2b0a3fb793280f5114941416c425c5b461)
at commit `ef203c2b0a3fb793280f5114941416c425c5b461`, acquired with its ISC license
and original notices by `toolchains/upstream-hdl-lock.json`.

| DUT | Functional checks |
| --- | --- |
| `picorv32` | Runs a hand-encoded RV32IM program through the native memory bus; checks seven stores containing independent arithmetic results, scratch memory store/load, conditional branch comparisons, internal multiply/divide integration, and stable requests during two-cycle memory backpressure |
| `picorv32_pcpi_mul` | Twelve fixed multiply vectors across MUL/MULH/MULHSU/MULHU, signed and unsigned high products, boundary operands, completion/writeback and rejection of an ADD instruction |
| `picorv32_pcpi_fast_mul` | The same twelve mathematical vectors and interface checks against the separate fast multiplier implementation |
| `picorv32_pcpi_div` | Fifteen fixed DIV/DIVU/REM/REMU vectors, negative operands, signed overflow, divide by zero, remainder sign, completion/writeback and rejection of an ADD instruction |

The CPU program is constructed directly from instruction fields in its bench.
It does not require a RISC-V software toolchain or a generated firmware download.
The CPU uses the upstream default native bus with multiply and divide enabled;
the three PCPI blocks use their default parameters. Every bench has a bounded
timeout, assertion failures use `$fatal`, and a success marker is printed only
after the checks complete. The required corpus gate reruns each bench with
`+VWB_NEGATIVE_CONTROL`, which changes one expected signature/result and must make
VWB return a failing test result without a success marker.

The required corpus gate uses Icarus. These benches also work with VWB's explicit
`--simulator verilator` selection. These are functional CPU/block smoke tests;
they do not replace an exhaustive ISA suite, RISC-V formal verification, timing
validation, or verification of every optional PicoRV32 configuration.
