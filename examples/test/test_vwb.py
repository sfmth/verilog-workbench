import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import vwb


class SourceCatalogTests(unittest.TestCase):
    def write(self, root: Path, relative: str, content: str) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_current_rgb_mixer_closure_excludes_unrelated_stubs(self):
        root = Path(__file__).resolve().parents[1]
        catalog = vwb.SourceCatalog(vwb.find_hdl_files(root / "src"))

        closure = {path.name for path in catalog.closure("rgb_mixer")}

        self.assertEqual(
            closure,
            {"rgb_mixer.v", "debounce.v", "encoder.v", "pwm.v"},
        )
        self.assertNotIn("alu", catalog.names())

    def test_declarations_do_not_depend_on_file_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write(
                root,
                "src/design_blocks.sv",
                """
                module child(input logic a, output logic y);
                  assign y = a;
                endmodule

                module top(input logic a, output logic y);
                  child #() child_instance(.a(a), .y(y));
                endmodule
                """,
            )
            catalog = vwb.SourceCatalog([path])

            self.assertEqual(catalog.definition("top").dependencies, ("child",))
            self.assertEqual(catalog.closure("top"), [path.resolve()])

    def test_comments_and_strings_do_not_create_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write(
                root,
                "src/real.v",
                """
                // module fake; endmodule
                module real;
                  initial $display("module also_fake; endmodule");
                endmodule
                /* module another_fake; endmodule */
                """,
            )
            catalog = vwb.SourceCatalog([path])

            self.assertEqual(catalog.names(), ["real"])

    def test_duplicate_module_is_rejected_only_when_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write(root, "src/a.v", "module duplicate; endmodule\n")
            second = self.write(root, "src/b.v", "module duplicate; endmodule\n")
            valid = self.write(root, "src/valid.v", "module valid; endmodule\n")
            catalog = vwb.SourceCatalog([first, second, valid])

            self.assertEqual(catalog.closure("valid"), [valid.resolve()])
            with self.assertRaisesRegex(vwb.VWBError, "declared more than once"):
                catalog.closure("duplicate")

    def test_systemverilog_packages_precede_modules_that_use_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = self.write(
                root,
                "src/constants_pkg.sv",
                "package constants_pkg; parameter int VALUE = 7; endpackage\n",
            )
            consumer = self.write(
                root,
                "src/consumer.sv",
                """
                module consumer(output logic [3:0] value);
                  assign value = constants_pkg::VALUE;
                endmodule
                """,
            )
            catalog = vwb.SourceCatalog([consumer, package])

            self.assertEqual(
                catalog.closure("consumer"),
                [package.resolve(), consumer.resolve()],
            )

    def test_interfaces_and_primitives_are_included_in_the_design_closure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interface = self.write(
                root,
                "src/sample_bus.sv",
                "interface sample_bus; logic value; modport sink(input value); endinterface\n",
            )
            primitive = self.write(
                root,
                "src/pass_udp.v",
                "primitive pass_udp(out, in); output out; input in; table 0 : 0; 1 : 1; endtable endprimitive\n",
            )
            consumer = self.write(
                root,
                "src/consumer.sv",
                "module consumer(sample_bus.sink bus, input wire a, output wire y); pass_udp u_pass(y, a); endmodule\n",
            )
            catalog = vwb.SourceCatalog([consumer, primitive, interface])

            self.assertEqual(
                catalog.closure("consumer"),
                [primitive.resolve(), interface.resolve(), consumer.resolve()],
            )

    def test_unpacked_array_declarations_are_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write(
                root,
                "src/memories.sv",
                """
                module memories;
                  typedef logic [7:0] word_t;
                  typedef logic [3:0] memory_t [13:14];
                  logic [7:0] packed_value;
                  logic [7:0] first [2:4], second [1:2][5:7];
                  integer counters [0:3];
                  word_t typed [9:10];
                  memory_t inherited;
                endmodule
                """,
            )
            raw = vwb.extract_raw_modules(path)[0]

            self.assertEqual(
                vwb.unpacked_arrays(raw),
                (
                    vwb.ArrayDef(name="first", ranges=("2:4",)),
                    vwb.ArrayDef(name="second", ranges=("1:2", "5:7")),
                    vwb.ArrayDef(name="counters", ranges=("0:3",)),
                    vwb.ArrayDef(name="typed", ranges=("9:10",)),
                    vwb.ArrayDef(name="inherited", ranges=("13:14",)),
                ),
            )

    def test_procedural_array_dump_is_rejected_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write(
                root,
                "procedural.sv",
                """
                module procedural;
                  task automatic work;
                    logic [7:0] local_memory [0:3];
                  endtask
                endmodule
                """,
            )

            with self.assertRaisesRegex(vwb.VWBError, "procedural array"):
                vwb.instrument_source_arrays(path, vwb.DEFAULT_MAX_ARRAY_WORDS)

    def test_final_block_array_dump_is_rejected_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write(
                root,
                "final_array.sv",
                "module final_array; final begin integer values [0:1]; end endmodule\n",
            )

            with self.assertRaisesRegex(vwb.VWBError, "procedural array"):
                vwb.instrument_source_arrays(path, vwb.DEFAULT_MAX_ARRAY_WORDS)

    def test_aggregate_member_array_dump_is_rejected_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write(
                root,
                "aggregate_array.sv",
                "module aggregate_array; struct { logic [7:0] values [0:1]; } item; endmodule\n",
            )

            with self.assertRaisesRegex(vwb.VWBError, "aggregate member array"):
                vwb.instrument_source_arrays(path, vwb.DEFAULT_MAX_ARRAY_WORDS)


class TestDiscoveryTests(unittest.TestCase):
    def write(self, root: Path, relative: str, content: str) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def make_workbench(self, root: Path) -> vwb.Workbench:
        return vwb.Workbench(
            root=root,
            src_dir=root / "src",
            test_dir=root / "test",
            build_dir=root / ".vwb",
        )

    def test_cocotb_helpers_are_not_runnable_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(
                root,
                "test/helper.py",
                "import cocotb\nasync def drive(dut):\n    pass\n",
            )
            test_path = self.write(
                root,
                "test/test_dut.py",
                "import cocotb as ct\n@ct.test()\nasync def check(dut):\n    pass\n",
            )

            workbench = self.make_workbench(root)

            self.assertEqual(
                workbench.tests,
                [vwb.TestSpec(dut="dut", kind="cocotb", path=test_path.resolve())],
            )

    def test_hdl_testbench_top_is_inferred(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/passthrough.v",
                "`timescale 1ns/1ps\nmodule passthrough(input wire a, output wire y); assign y = a; endmodule\n",
            )
            test_path = self.write(
                root,
                "test/test_passthrough.sv",
                """
                `timescale 1ns/1ps
                module test_passthrough;
                  logic a;
                  wire y;
                  passthrough dut(.a(a), .y(y));
                endmodule
                """,
            )

            workbench = self.make_workbench(root)

            self.assertEqual(
                workbench.tests,
                [
                    vwb.TestSpec(
                        dut="passthrough",
                        kind="hdl",
                        path=test_path.resolve(),
                        top="test_passthrough",
                    )
                ],
            )

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "needs Icarus")
    def test_hdl_testbench_runs_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/passthrough.v",
                "`timescale 1ns/1ps\nmodule passthrough(input wire a, output wire y); assign y = a; endmodule\n",
            )
            self.write(
                root,
                "test/test_passthrough.sv",
                """
                `timescale 1ns/1ps
                module test_passthrough;
                  reg a = 0;
                  wire y;
                  passthrough dut(.a(a), .y(y));
                  initial begin
                    #1;
                    if (y !== 0) $fatal(1, "low value failed");
                    a = 1;
                    #1;
                    if (y !== 1) $fatal(1, "high value failed");
                    $finish;
                  end
                endmodule
                """,
            )
            workbench = self.make_workbench(root)
            args = SimpleNamespace(
                waves=True,
                wave_format="vcd",
                define=[],
                include=[],
                compile_arg=[],
                sim_arg=[],
                plusarg=[],
                test_top=None,
                testcase=None,
                seed=None,
            )

            passed, wave_path = workbench.run_test_spec(workbench.tests[0], args)

            self.assertTrue(passed)
            self.assertIsNotNone(wave_path)
            self.assertTrue(wave_path.is_file())

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "needs Icarus")
    def test_time_zero_finish_still_creates_waveform(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/instant.v",
                "`timescale 1ns/1ps\nmodule instant; endmodule\n",
            )
            self.write(
                root,
                "test/test_instant.v",
                "`timescale 1ns/1ps\nmodule test_instant; instant dut(); initial $finish; endmodule\n",
            )
            workbench = self.make_workbench(root)
            args = SimpleNamespace(
                waves=True,
                wave_format="vcd",
                define=[],
                include=[],
                compile_arg=[],
                sim_arg=[],
                plusarg=[],
                test_top=None,
                testcase=None,
                seed=None,
            )

            passed, wave_path = workbench.run_test_spec(workbench.tests[0], args)

            self.assertTrue(passed)
            self.assertTrue(wave_path.is_file())

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "needs Icarus")
    def test_waveform_contains_every_unpacked_array_word(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/memory_decl.svh",
                "`define VWB_DECLARE_MEMORY(name) logic [7:0] name [0:1]\n",
            )
            source = self.write(
                root,
                "src/memory_design.sv",
                """
                `timescale 1ns/1ps
                `include "memory_decl.svh"
                package memory_types_pkg;
                  typedef logic [7:0] package_word_t;
                endpackage
                module memory_design #(
                  parameter integer LOW = 2,
                  parameter integer HIGH = 4
                ) (
                  input logic [2:0] row,
                  input logic [2:0] column,
                  output logic [7:0] read_data
                );
                  import memory_types_pkg::*;
                  typedef logic [7:0] word_t;
                  typedef logic [7:0] unpacked_word_t [15:16];
                  logic [7:0] memory [HIGH:LOW];
                  logic [7:0] matrix [1:2][5:6];
                  word_t typed [7:8];
                  unpacked_word_t inherited;
                  package_word_t packaged [11:12];
                  `VWB_DECLARE_MEMORY(included);
                  `ifdef VWB_DISABLED_ARRAY
                    logic [7:0] disabled [0:1];
                  `endif
                  for (genvar bank = 0; bank < 2; bank = bank + 1) begin : banks
                    logic [7:0] bank_memory [3:4];
                  end
                  assign read_data = memory[row] ^ matrix[1][column] ^ typed[7] ^ inherited[15] ^ included[0] ^ packaged[11];
                endmodule
                """,
            )
            self.write(
                root,
                "test/test_memory_design.sv",
                """
                `timescale 1ns/1ps
                module test_memory_design;
                  logic [2:0] row = 2;
                  logic [2:0] column = 5;
                  logic [7:0] read_data;
                  memory_design dut(.row(row), .column(column), .read_data(read_data));
                  initial begin
                    dut.memory[2] = 8'h12;
                    dut.memory[3] = 8'h34;
                    dut.memory[4] = 8'h56;
                    dut.matrix[1][5] = 8'h01;
                    dut.matrix[1][6] = 8'h02;
                    dut.matrix[2][5] = 8'h03;
                    dut.matrix[2][6] = 8'h04;
                    dut.typed[7] = 8'h80;
                    dut.typed[8] = 8'h81;
                    dut.inherited[15] = 8'h20;
                    dut.inherited[16] = 8'h21;
                    dut.packaged[11] = 8'hc0;
                    dut.packaged[12] = 8'hc1;
                    dut.included[0] = 8'h40;
                    dut.included[1] = 8'h41;
                    dut.banks[0].bank_memory[3] = 8'ha0;
                    dut.banks[0].bank_memory[4] = 8'ha1;
                    dut.banks[1].bank_memory[3] = 8'hb0;
                    dut.banks[1].bank_memory[4] = 8'hb1;
                    #1;
                    if (read_data !== 8'h33) $fatal(1, "array read mismatch");
                    $finish;
                  end
                endmodule
                """,
            )
            workbench = self.make_workbench(root)
            args = SimpleNamespace(
                waves=True,
                wave_format="vcd",
                define=[],
                include=[],
                compile_arg=[],
                sim_arg=[],
                plusarg=[],
                test_top=None,
                testcase=None,
                seed=None,
            )

            passed, wave_path = workbench.run_test_spec(workbench.tests[0], args)

            self.assertTrue(passed)
            waveform = wave_path.read_text(encoding="utf-8")
            for index in range(2, 5):
                self.assertIn(f"\\memory[{index}]", waveform)
            # Icarus flattens multidimensional memory-word names in VCD output.
            for index in range(4):
                self.assertIn(f"\\matrix[{index}]", waveform)
            for index in range(7, 9):
                self.assertIn(f"\\typed[{index}]", waveform)
            for index in range(15, 17):
                self.assertIn(f"\\inherited[{index}]", waveform)
            for index in range(11, 13):
                self.assertIn(f"\\packaged[{index}]", waveform)
            for index in range(2):
                self.assertIn(f"\\included[{index}]", waveform)
            for index in range(3, 5):
                self.assertEqual(waveform.count(f"\\bank_memory[{index}]"), 2)
            self.assertNotIn("disabled", waveform)
            self.assertNotIn("$dumpvars", source.read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "needs Icarus")
    def test_array_word_limit_prevents_large_dump_elaboration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/limited.sv",
                "`timescale 1ns/1ps\nmodule limited; logic [7:0] memory [0:4]; assign memory[0] = 0; endmodule\n",
            )
            self.write(
                root,
                "test/test_limited.sv",
                "`timescale 1ns/1ps\nmodule test_limited; limited dut(); initial #1 $finish; endmodule\n",
            )
            workbench = self.make_workbench(root)
            args = SimpleNamespace(
                waves=True,
                wave_format="vcd",
                max_array_words=4,
                define=[],
                include=[],
                compile_arg=[],
                sim_arg=[],
                plusarg=[],
                test_top=None,
                testcase=None,
                seed=None,
            )

            passed, _ = workbench.run_test_spec(workbench.tests[0], args)

            self.assertFalse(passed)

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "needs Icarus")
    def test_explicit_hdl_test_is_authoritative(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(
                root,
                "test/test_dut.sv",
                "module shared_tb; initial $fatal(1, \"wrong test\"); endmodule\n",
            )
            selected = self.write(
                root,
                "test/dut_tb.sv",
                "module shared_tb; dut u_dut(); initial $finish; endmodule\n",
            )
            workbench = self.make_workbench(root)
            specs = workbench.specs_for(
                ["dut"], "hdl", str(selected), "shared_tb"
            )
            args = SimpleNamespace(
                waves=False,
                wave_format="vcd",
                define=[],
                include=[],
                compile_arg=[],
                sim_arg=[],
                plusarg=[],
                test_top="shared_tb",
                testcase=None,
                seed=None,
            )

            passed, _ = workbench.run_test_spec(specs[0], args)

            self.assertTrue(passed)

    def test_explicit_test_rejects_unknown_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            invalid = self.write(root, "test/test_dut.txt", "not HDL\n")
            workbench = self.make_workbench(root)

            with self.assertRaisesRegex(vwb.VWBError, "unsupported test file type"):
                workbench.specs_for(["dut"], "auto", str(invalid), None)

    def test_dry_run_has_no_filesystem_side_effects_and_orders_sim_args(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(
                root,
                "test/test_dut.v",
                "module test_dut; dut u_dut(); initial $finish; endmodule\n",
            )
            workbench = vwb.Workbench(
                root=root,
                src_dir=root / "src",
                test_dir=root / "test",
                build_dir=root / ".vwb",
                verbose=True,
                dry_run=True,
            )
            args = SimpleNamespace(
                waves=True,
                wave_format="vcd",
                define=[],
                include=[],
                compile_arg=[],
                sim_arg=["-v"],
                plusarg=[],
                test_top=None,
                testcase=None,
                seed=None,
            )
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                passed, _ = workbench.run_test_spec(workbench.tests[0], args)

            self.assertTrue(passed)
            self.assertFalse((root / ".vwb").exists())
            vvp_line = next(
                line for line in output.getvalue().splitlines() if line.startswith("$ vvp")
            )
            self.assertLess(vvp_line.index("-v"), vvp_line.index("sim.vvp"))

    def test_cocotb_wave_dump_is_injected_only_when_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(
                root,
                "test/test_dut.py",
                "import cocotb\n@cocotb.test()\nasync def check(dut):\n    pass\n",
            )
            workbench = vwb.Workbench(
                root=root,
                src_dir=root / "src",
                test_dir=root / "test",
                build_dir=root / ".vwb",
                dry_run=True,
            )
            args = SimpleNamespace(
                waves=True,
                wave_format="vcd",
                define=[],
                include=[],
                compile_arg=[],
                sim_arg=[],
                plusarg=[],
                test_top=None,
                testcase=None,
                seed=None,
            )
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                passed, _ = workbench.run_test_spec(workbench.tests[0], args)

            self.assertTrue(passed)
            compile_line = next(
                line
                for line in output.getvalue().splitlines()
                if line.startswith("$ iverilog") and "sim.vvp" in line
            )
            self.assertNotIn("COCOTB_SIM", compile_line)
            self.assertIn("vwb_dump.v", compile_line)

            args.waves = False
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                passed, _ = workbench.run_test_spec(workbench.tests[0], args)
            compile_line = next(
                line
                for line in output.getvalue().splitlines()
                if line.startswith("$ iverilog")
            )
            self.assertTrue(passed)
            self.assertNotIn("COCOTB_SIM", compile_line)
            self.assertNotIn("vwb_dump.v", compile_line)

    def test_synth_is_the_synthesis_command(self):
        parser = vwb.make_parser()

        self.assertEqual(parser.parse_args(["synth", "dut"]).command, "synth")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["show", "dut"])

    def test_fpga_defaults_constraints_to_configured_source_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "examples" / "src"
            test_dir = root / "examples" / "test"
            self.write(source_dir, "dut.v", "module dut; endmodule\n")
            self.write(source_dir, "io.pcf", "set_io clk 35\n")
            self.write(test_dir, ".gitkeep", "")
            workbench = vwb.Workbench(
                root=root,
                src_dir=source_dir,
                test_dir=test_dir,
                build_dir=root / ".vwb",
                dry_run=True,
            )
            workbench.require_tool = lambda _command: None
            args = SimpleNamespace(
                board="ice40",
                stage="synth",
                constraints=None,
                define=[],
                include=[],
            )

            artifact = workbench.run_fpga("dut", args)

            self.assertEqual(
                artifact,
                root / ".vwb" / "fpga" / "ice40" / "dut" / "dut.json",
            )

    def test_formal_dry_run_with_view_does_not_require_a_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(root, "test/test_dut.v", "module test_dut; endmodule\n")
            config = self.write(root, "properties.sby", "[options]\nmode prove\n")
            workbench = vwb.Workbench(
                root=root,
                src_dir=root / "src",
                test_dir=root / "test",
                build_dir=root / ".vwb",
                dry_run=True,
            )
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                formal_output = workbench.run_formal(str(config), view=True)

            self.assertEqual(formal_output, root / ".vwb" / "formal" / "properties")
            self.assertIn("$ gtkwave", output.getvalue())
            self.assertFalse((root / ".vwb").exists())

    def test_clean_refuses_unowned_or_source_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(root, "test/test_dut.v", "module test_dut; endmodule\n")
            unowned = self.write(root, "existing/keep.txt", "keep\n").parent
            workbench = vwb.Workbench(
                root=root,
                src_dir=root / "src",
                test_dir=root / "test",
                build_dir=unowned,
            )
            with self.assertRaisesRegex(vwb.VWBError, "not owned"):
                workbench.clean("all")

            source_build = vwb.Workbench(
                root=root,
                src_dir=root / "src",
                test_dir=root / "test",
                build_dir=root / "src" / "build",
            )
            with self.assertRaisesRegex(vwb.VWBError, "source or test"):
                source_build.prepare_build_dir()

    def test_cocotb_results_require_a_passing_testcase(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            passed = self.write(
                root,
                "passed.xml",
                '<testsuite tests="1" failures="0"><testcase name="ok"/></testsuite>',
            )
            empty = self.write(
                root,
                "empty.xml",
                '<testsuite tests="0" failures="0"/>',
            )
            failed = self.write(
                root,
                "failed.xml",
                '<testsuite tests="1" failures="1"><testcase><failure/></testcase></testsuite>',
            )

            self.assertTrue(vwb.Workbench._results_passed(passed))
            self.assertFalse(vwb.Workbench._results_passed(empty))
            self.assertFalse(vwb.Workbench._results_passed(failed))


if __name__ == "__main__":
    unittest.main()
