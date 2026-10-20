# Representative HDL acceptance

The final Linux x64 development-host run passed all five selected phases of
the existing integration harness: **tests, lint, synthesis, formal and FPGA**.
It exercised 10 reviewed modules and 9 discovered test cases through 73 actual
commands, with zero unexpected exit statuses and 38 retained, hashed artifacts.
The run took 62.3 seconds. It used the locked OSS CAD Suite 2026-10-18 and its
CPython 3.11.6 runtime, plus pinned Verible and isolated example-test dependencies.

- [Final passing report](workspace-evidence/hdl-representative-final-linux-x64.json)
- [Historical initial failure and diagnostic excerpts](workspace-evidence/hdl-representative-initial-failure-linux-x64.json)
- [Exact Python wheel dependency receipt](workspace-evidence/hdl-representative-python-dependencies-linux-x64.json)

The harness audited the complete inventory, then selected `processor`,
`processing_array`, `processing_element`, `tinycordic`, `encoder`, `array_example`,
`sv_beginner_alu`, `sv_beginner_shift_register`, `vhdl_beginner_accumulator` and
`vhdl_beginner_counter`. This includes hierarchical Verilog, arrays, SystemVerilog
packages, clocked designs, hierarchical VHDL and split-file VHDL. It also ran the
harness's generated-starter, escaped-identifier, intentional failure-aggregation,
synthesis, formal and FPGA fixtures. Intentional failing tests remained failures
and were checked against their expected nonzero statuses.

All repository input files were copied into a fresh temporary project before
execution. Original examples were unchanged. The report records 151 input source
hashes, each command and its result, selected diagnostic output, and artifact
hashes. Machine-specific paths are replaced with descriptive placeholders;
individual temporary command logs are identified by their original byte hashes.

## Failures investigated

The historical run found a processor simulator crash after test completion,
missing `bitstring`/NumPy/Pillow dependencies for the image-processing example,
and missing Pillow for PNG validation. The final run used the narrowly versioned
[Cocotb shutdown compatibility hook](../companion/cocotb_runtime.py) and the exact
isolated dependencies in the receipt. The processor and processing-array RTL
and gate runs then each returned zero. Both processor XML reports contain one
executed `test_pwm` case, with no failures, errors or skips. No assertion or
nonzero process result was ignored to obtain this pass.

## Scope and limits

The two legacy examples have weak functional coverage: the processor test's
assertions are commented out, and the processing-array test prints reference and
RTL images without checking numerical equality. Their passing runs establish
execution and clean shutdown; they do not prove instruction or convolution
correctness.

The run explicitly generated JSON synthesis output for all 10 selected modules
and validated two real PNG synthesis outputs. SVG/DOT intermediates were used;
explicit user-facing SVG/DOT output-format acceptance was outside this run.
Starter fixtures generated real, nonempty FST output, and an escaped-identifier
fixture generated VCD. Those temporary waveform fixtures were removed by the
harness; waveform archive/save/load behavior was outside this run.

The separate `waves` phase was not selected. The harness substituted a viewer
executable and one intentionally failing renderer for its display/fallback
checks; actual HDL tools still ran. No GUI viewer behavior, physical board
programming, other operating system, or clean-machine installation is implied.
