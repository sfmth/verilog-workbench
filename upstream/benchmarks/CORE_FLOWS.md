# Original upstream core verification flows

The recipes in `core-flows.json` invoke the projects' own Makefiles or FuseSoC
targets in copied source trees. They retain the original HDL, C++ testbench,
firmware, assertions, and verdict handling. They are separate from the VWB
directed component tests and structural elaboration checks in the design corpus.

| Project | Original entrypoint | Actual dependencies | Coverage and availability |
| --- | --- | --- | --- |
| PicoRV32 | `make test TOOLCHAIN_PREFIX=riscv-none-elf-` | Icarus/VVP, GNU Make, Python, RISC-V GCC and objcopy | Builds the checked-in firmware and ISA assembly tests, then requires the original `ALL TESTS PASSED.` verdict. The GCC archive is pinned in `toolchains/upstream-riscv-toolchain-lock.json`. |
| PicoRV32 | `make test_verilator TOOLCHAIN_PREFIX=riscv-none-elf-` | Verilator, C++ compiler, GNU Make, Python, RISC-V GCC and objcopy | Runs the same firmware regression through the original C++ bench and requires the same upstream verdict. Two simulators remain one physical design in coverage counts. |
| SERV | `fusesoc --cores-root=. run --target=verilator_tb servant ...` | FuseSoC, Edalize, Verilator, Make, C++ compiler | Runs the original C++ UART decoder against the checked-in Zephyr image. Both the Zephyr banner and Hello World output are required. This is a system output smoke check; the C++ bench's bounded timeout returns zero even when the CPU fails to print. |
| Ibex Simple System | Original firmware Makefile, FuseSoC `sim` target, and generated `Vibex_simple_system` executable | FuseSoC, Edalize, Verilator, Make/C++, libelf development files, RISC-V GCC | Requires the original firmware's Hello message, hexadecimal signatures, and timer-interrupt Tick/Tock output from `ibex_simple_system.log`, plus successful software-requested simulation halt. This is a system smoke check, separate from UVM cosimulation. |
| CV32E40P | `example_tb/core/Makefile`, target `custom-vsim-run` | Questa/ModelSim, RISC-V 32-bit bare-metal GCC/newlib | External requirement. The pinned upstream example defaults to ModelSim/Questa 10.7b and calls `vlib`, `vlog`, `vopt`, and `vsim`. The registry overrides the executable suffixes so the installation's commands can be found on PATH. The complete verification environment is in the separate `core-v-verif` repository. |
| Ibex | `dv/uvm/core_ibex/Makefile`, `SIMULATOR=xlm` | Licensed Xcelium/UVM, lowRISC's `ibex_cosim` Spike fork, RISC-V GCC, repository Python dependencies | External requirement. The recipe follows the upstream quick start and requires real JUnit results. The Makefile and simulator YAML also describe VCS, Questa, and DSim variants; those are not proven by the Xcelium recipe. |
| VexRiscv | `src/test/cpp/regression/makefile` | Java/SBT/Scala/SpinalHDL CPU generation, Verilator, Make/C++, benchmark-data submodule | External requirement until the generated CPU, JVM dependency environment, and `VexRiscvRegressionData` submodule are acquired and pinned. Elaborating a checked-in generated Verilog fixture does not run this regression. |

Ibex Simple System loads the GCC-built ELF directly, so the optional VMEM image
and its `srecord` dependency are not needed for this recipe. The original
Makefile's `ARCH` variable is set to `rv32imc_zicsr`: modern GNU binutils require
CSR instructions to name the `zicsr` extension explicitly. The FuseSoC backend
retains the pinned Verilator's `UNOPTFLAT` warning in the build log through the
`-Wno-fatal` option. The RTL, C++ bench, firmware sources, and assertions remain
unchanged. The original runtime still checks convergence and simulation errors.

FuseSoC needs to match the pinned source metadata. The current SERV `.core` files
contain a `license` field that FuseSoC 2.4.3 rejects. FuseSoC 2.4.6 understands
that field and uses the pure Python dependency set pinned in the `fusesoc`
environment profile. FuseSoC 2.4.7 introduces Pydantic; the tested contemporary
`pydantic_core` wheel could not load against this OSS CAD Suite Python runtime's
bundled libc. Upstream source files are not edited to work around driver version
mismatches.

SERV's original Verilog bench stores the firmware filename in a 128-byte
register. FuseSoC's `file` parameter expands to an absolute path, which can
exceed that limit in a deep workspace and silently truncate the filename.
The recipe uses FuseSoC's supported backend
`--run_options=+firmware=../../../sw/zephyr_hello.hex` to pass the original
firmware through a short relative path from its original build directory.
The upstream HDL, C++ bench and FuseSoC core metadata remain unchanged.

The required PicoRV32 firmware regression was also checked with a deliberately
corrupted copy of its generated firmware. The original bench trapped after 24
cycles, printed `ERROR!`, returned nonzero through `vvp -N`, and did not emit its
success marker. A normal compiler or simulator process exit alone does not prove
the firmware passed.

The SERV output oracle was checked with the original bench and the different
checked-in `blinky.hex` firmware. The upstream process still returned zero after
its bounded timeout. VWB returned failure because the required Zephyr banner and
Hello World output were absent.

Source references at the pinned revisions:

- [PicoRV32 Makefile](https://github.com/YosysHQ/picorv32/blob/ef203c2b0a3fb793280f5114941416c425c5b461/Makefile)
- [SERV running prebuilt software](https://github.com/olofk/serv/blob/f200eb2ed7b69ac1c6b8eddd47654522aeee5ce8/README.md#running-pre-built-test-software)
- [SERV FuseSoC target](https://github.com/olofk/serv/blob/f200eb2ed7b69ac1c6b8eddd47654522aeee5ce8/servant.core)
- [CV32E40P verification scope](https://github.com/openhwfoundation/cv32e40p/blob/360d272898d81806be3377193870dbf83a3ea79f/README.md#verification)
- [CV32E40P original example Makefile](https://github.com/openhwfoundation/cv32e40p/blob/360d272898d81806be3377193870dbf83a3ea79f/example_tb/core/Makefile)
- [Ibex UVM prerequisites](https://github.com/lowRISC/ibex/blob/1b29dae8866e6c0f0a3cd9dead53a77b4f45e357/dv/uvm/core_ibex/README.md)
- [Ibex Simple System](https://github.com/lowRISC/ibex/blob/1b29dae8866e6c0f0a3cd9dead53a77b4f45e357/examples/simple_system/README.md)
- [VexRiscv generation and regression instructions](https://github.com/SpinalHDL/VexRiscv/blob/baf7dc82f855eddaf5b0a20a6802c526f127e38a/README.md#regression-tests)
- [VexRiscv dependency versions](https://github.com/SpinalHDL/VexRiscv/blob/baf7dc82f855eddaf5b0a20a6802c526f127e38a/build.sbt)
