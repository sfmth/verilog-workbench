# Directed serial and AXI-lite checks

These benches exercise unchanged source files from the pinned upstream archives
in `upstream/projects.lock.json`. `designs.json` is consumed by the shared
upstream CI runner; sources and their original license notices stay in those
verified archives.

- `uart-loopback` instantiates the actual UART, transmitter and receiver. Eight
  byte patterns traverse the serial wire, including zero, all ones, alternating
  bits and boundary bits. The check stalls the receive AXI-stream channel and
  checks that its data/valid stay stable without frame or overrun errors.
- `axil-ram` checks the pipelined AXI-lite RAM with skewed address/data arrival,
  write/read response backpressure, independent addresses, byte write strobes,
  a zero-strobe write and transaction counts.

Both use timed Verilator simulation through VWB. Each includes a bounded
timeout and completes with `VWB_UPSTREAM_PASS <id>` only after its checks pass.
`+VWB_NEGATIVE_CONTROL` changes the expected first received/read value so the
same real DUT transaction must fail its data comparison. CI requires that
negative run to return failure. These are directed checks, not protocol
exhaustiveness, UART interoperability or physical hardware certification.
