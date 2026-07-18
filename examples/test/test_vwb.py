import contextlib
import io
import json
import shlex
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import vwb


class SourceCatalogTests(unittest.TestCase):
    def write(self, root: Path, relative: str, content: str) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_module_closure_excludes_unrelated_designs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/debounce.v", "module debounce; endmodule\n")
            self.write(root, "src/encoder.v", "module encoder; endmodule\n")
            self.write(root, "src/pwm.v", "module pwm; endmodule\n")
            self.write(
                root,
                "src/rgb_mixer.v",
                "module rgb_mixer; debounce d(); encoder e(); pwm p(); endmodule\n",
            )
            self.write(root, "src/alu.v", "module alu; endmodule\n")
            catalog = vwb.SourceCatalog(vwb.find_hdl_files(root / "src"))

            closure = {path.name for path in catalog.closure("rgb_mixer")}

            self.assertEqual(
                closure,
                {"rgb_mixer.v", "debounce.v", "encoder.v", "pwm.v"},
            )
            self.assertIn("alu", catalog.names())

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

    def test_list_reports_duplicates_interfaces_and_primitives_without_aborting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/first.v", "module duplicate; endmodule\n")
            self.write(root, "src/second.v", "module duplicate; endmodule\n")
            self.write(root, "src/healthy.v", "module healthy; endmodule\n")
            self.write(
                root,
                "src/units.sv",
                "interface stream_if; logic value; endinterface\n"
                "primitive pass_udp(out, in); output out; input in; "
                "table 0 : 0; 1 : 1; endtable endprimitive\n",
            )
            self.write(root, "test/.gitkeep", "")
            workbench = vwb.Workbench(
                root=root,
                src_dir=root / "src",
                test_dir=root / "test",
                build_dir=root / ".vwb",
            )
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                status = vwb.command_list(
                    workbench, SimpleNamespace(as_json=True)
                )

            report = json.loads(output.getvalue())
            modules = {item["name"]: item for item in report["modules"]}
            self.assertEqual(status, 0)
            self.assertIn("healthy", modules)
            self.assertTrue(modules["duplicate"]["problems"])
            self.assertEqual(
                [item["name"] for item in report["interfaces"]], ["stream_if"]
            )
            self.assertEqual(
                [item["name"] for item in report["primitives"]], ["pass_udp"]
            )

    def test_unterminated_module_does_not_swallow_the_next_module(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write(
                root,
                "src/designs.v",
                "module broken;\n"
                "  wire unfinished;\n"
                "module healthy;\n"
                "endmodule\n",
            )

            catalog = vwb.SourceCatalog([path])

            self.assertEqual(catalog.names(), ["broken", "healthy"])
            self.assertTrue(catalog.problems("broken"))
            self.assertEqual(catalog.problems("healthy"), [])
            self.assertEqual(catalog.closure("healthy"), [path.resolve()])

    def test_discovery_uses_only_the_active_preprocessor_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write(
                root,
                "src/conditional.sv",
                "`ifdef NOT_DEFINED\n"
                "module phantom; endmodule\n"
                "`else\n"
                "module live; endmodule\n"
                "`endif\n"
                "`define ENABLED\n"
                "`ifdef ENABLED\n"
                "module selected; endmodule\n"
                "`else\n"
                "module live; endmodule\n"
                "`endif\n",
            )

            catalog = vwb.SourceCatalog([path])

            self.assertEqual(catalog.names(), ["live", "selected"])
            self.assertEqual(catalog.problems("live"), [])

    def test_interface_dependencies_require_interface_syntax(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write(
                root,
                "src/interfaces.sv",
                "interface bus_if; modport source(output value); logic value; "
                "endinterface\n"
                "module local_names; logic bus_if; logic mem; logic data; endmodule\n"
                "module interface_user(bus_if.source bus); endmodule\n",
            )

            catalog = vwb.SourceCatalog([path])

            self.assertEqual(catalog.definition("local_names").dependencies, ())
            self.assertEqual(
                catalog.definition("interface_user").dependencies, ("bus_if",)
            )

    def test_vhdl_strings_do_not_create_entities_or_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write(
                root,
                "src/messages.vhd",
                "entity real_child is end entity;\n"
                "architecture rtl of real_child is begin end architecture;\n"
                "entity reporter is end entity;\n"
                "architecture rtl of reporter is begin\n"
                '  assert false report "entity phantom is child: real_child port map";\n'
                "end architecture;\n",
            )

            catalog = vwb.SourceCatalog([path])

            self.assertEqual(catalog.names(), ["real_child", "reporter"])
            self.assertEqual(catalog.definition("reporter").dependencies, ())

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
            self.assertNotIn(package.resolve(), catalog.files_without_modules)

    def test_vhdl_entities_dependencies_and_test_language_are_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = self.write(
                root,
                "src/vhdl_child.vhd",
                """
                library ieee;
                use ieee.std_logic_1164.all;
                entity vhdl_child is
                  port (value_in : in std_logic; value_out : out std_logic);
                end entity;
                architecture rtl of vhdl_child is
                begin
                  value_out <= value_in;
                end architecture;
                """,
            )
            top = self.write(
                root,
                "src/vhdl_top.vhd",
                """
                library ieee;
                use ieee.std_logic_1164.all;
                entity vhdl_top is
                  port (value_in : in std_logic; value_out : out std_logic);
                end entity;
                architecture rtl of vhdl_top is
                begin
                  child_instance : entity work.vhdl_child
                    port map (value_in => value_in, value_out => value_out);
                end architecture;
                """,
            )
            test_path = self.write(
                root,
                "test/test_vhdl_top.vhd",
                """
                entity test_vhdl_top is end entity;
                architecture test of test_vhdl_top is begin end architecture;
                """,
            )

            workbench = vwb.Workbench(
                root=root,
                src_dir=root / "src",
                test_dir=root / "test",
                build_dir=root / ".vwb",
            )

            self.assertEqual(workbench.catalog.names(), ["vhdl_child", "vhdl_top"])
            definition = workbench.catalog.definition("vhdl_top")
            self.assertEqual(definition.language, "vhdl")
            self.assertEqual(definition.dependencies, ("vhdl_child",))
            self.assertEqual(workbench.catalog.closure("vhdl_top"), [child, top])
            self.assertEqual(
                workbench.tests,
                [
                    vwb.TestSpec(
                        dut="vhdl_top",
                        kind="vhdl",
                        path=test_path.resolve(),
                        top="test_vhdl_top",
                    )
                ],
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    vwb.command_list(workbench, SimpleNamespace(as_json=True)), 0
                )
            report = json.loads(output.getvalue())
            metadata = {item["name"]: item for item in report["modules"]}
            self.assertEqual(metadata["vhdl_top"]["language"], "vhdl")
            self.assertEqual(metadata["vhdl_top"]["dependencies"], ["vhdl_child"])

    def test_vhdl_closure_excludes_unrelated_designs_and_keeps_local_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = self.write(
                root,
                "src/values_pkg.vhd",
                "package values_pkg is constant START_VALUE : integer := 0; end package;\n",
            )
            child = self.write(
                root,
                "src/vhdl_child.vhd",
                "entity vhdl_child is end; architecture rtl of vhdl_child is begin end;\n",
            )
            top = self.write(
                root,
                "src/vhdl_top.vhd",
                """
                use work.values_pkg.all;
                entity vhdl_top is end;
                architecture rtl of vhdl_top is
                  component vhdl_child is end component;
                begin
                  child_instance : vhdl_child;
                end;
                """,
            )
            self.write(
                root,
                "src/unrelated.vhd",
                "entity unrelated is end; architecture rtl of unrelated is begin end;\n",
            )

            catalog = vwb.SourceCatalog(vwb.find_hdl_files(root / "src"))

            self.assertEqual(catalog.closure("vhdl_top"), [package, child, top])
            self.assertNotIn(package, catalog.files_without_modules)
            (root / "test").mkdir()
            workbench = vwb.Workbench(
                root=root,
                src_dir=root / "src",
                test_dir=root / "test",
                build_dir=root / ".vwb",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                vwb.command_list(workbench, SimpleNamespace(as_json=True))
            packages = json.loads(output.getvalue())["packages"]
            self.assertIn(
                {
                    "name": "values_pkg",
                    "language": "vhdl",
                    "files": ["src/values_pkg.vhd"],
                },
                packages,
            )

    def test_vhdl_closure_includes_a_separate_architecture_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entity = self.write(
                root,
                "src/counter_entity.vhd",
                "entity Counter is port (value : out bit); end entity;\n",
            )
            architecture = self.write(
                root,
                "src/counter_rtl.vhd",
                "architecture RTL of counter is begin value <= '0'; end architecture;\n",
            )

            catalog = vwb.SourceCatalog(vwb.find_hdl_files(root / "src"))

            self.assertEqual(catalog.closure("COUNTER"), [entity, architecture])
            self.assertEqual(
                catalog.implementation_files("counter"), [entity, architecture]
            )
            self.assertNotIn(architecture, catalog.files_without_modules)

    def test_vhdl_duplicate_entity_names_are_case_insensitive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/first.vhd", "entity Counter is end entity;\n")
            self.write(root, "src/second.vhd", "entity counter is end entity;\n")
            catalog = vwb.SourceCatalog(vwb.find_hdl_files(root / "src"))

            with self.assertRaisesRegex(vwb.VWBError, "declared more than once"):
                catalog.definition("COUNTER")

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
            self.assertNotIn(interface.resolve(), catalog.files_without_modules)
            self.assertNotIn(primitive.resolve(), catalog.files_without_modules)

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

    def test_procedural_array_dump_is_skipped_with_a_warning(self):
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

            warnings = io.StringIO()
            with contextlib.redirect_stderr(warnings):
                instrumented = vwb.instrument_source_arrays(
                    path, vwb.DEFAULT_MAX_ARRAY_WORDS
                )
            self.assertIsNone(instrumented)
            self.assertIn("skipped procedural array", warnings.getvalue())

    def test_final_block_array_dump_is_skipped_with_a_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write(
                root,
                "final_array.sv",
                "module final_array; final begin integer values [0:1]; end endmodule\n",
            )

            warnings = io.StringIO()
            with contextlib.redirect_stderr(warnings):
                instrumented = vwb.instrument_source_arrays(
                    path, vwb.DEFAULT_MAX_ARRAY_WORDS
                )
            self.assertIsNone(instrumented)
            self.assertIn("skipped procedural array", warnings.getvalue())

    def test_aggregate_member_array_does_not_block_supported_arrays(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write(
                root,
                "aggregate_array.sv",
                "module aggregate_array; logic [7:0] memory [0:1]; "
                "struct { logic [7:0] values [0:1]; } item; endmodule\n",
            )

            warnings = io.StringIO()
            with contextlib.redirect_stderr(warnings):
                instrumented = vwb.instrument_source_arrays(
                    path, vwb.DEFAULT_MAX_ARRAY_WORDS
                )
            self.assertIsNotNone(instrumented)
            self.assertIn("$dumpvars(0, memory[", instrumented)
            self.assertNotIn("$dumpvars(0, values[", instrumented)
            self.assertIn("skipped aggregate member array", warnings.getvalue())


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

    @unittest.skipUnless(shutil.which("sh"), "needs a POSIX shell")
    def test_tool_timeout_terminates_spawned_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(root, "test/.gitkeep", "")
            marker = root / "orphan-finished"
            ready = root / "orphan-ready"
            command = (
                "(trap '' TERM; printf ready > "
                + shlex.quote(str(ready))
                + "; sleep 0.4; printf done > "
                + shlex.quote(str(marker))
                + ") & wait"
            )
            workbench = self.make_workbench(root)

            with contextlib.redirect_stderr(io.StringIO()):
                result = workbench.run(["sh", "-c", command], timeout=0.1)
            time.sleep(0.45)

            self.assertEqual(result.returncode, 124)
            self.assertTrue(ready.exists(), "the TERM-ignoring child never started")
            self.assertFalse(marker.exists())

    @unittest.skipUnless(shutil.which("sh"), "needs a POSIX shell")
    def test_tools_are_quiet_on_success_and_concise_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)

            output = io.StringIO()
            errors = io.StringIO()
            with (
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
            ):
                success = workbench.run(["sh", "-c", "printf noisy-success"])
            self.assertEqual(success.returncode, 0)
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(errors.getvalue(), "")

            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                failure = workbench.run(
                    ["sh", "-c", "printf useful-error >&2; exit 7"]
                )
            self.assertEqual(failure.returncode, 7)
            self.assertIn("useful-error", errors.getvalue())
            self.assertIn("--verbose", errors.getvalue())

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

    def test_missing_module_generates_an_idempotent_cocotb_starter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/dut.sv",
                """
                module dut(
                  input logic clk,
                  input logic reset_n,
                  input logic [3:0] data_in,
                  output logic [3:0] data_out
                );
                  always_ff @(posedge clk) begin
                    if (!reset_n) data_out <= '0;
                    else data_out <= data_in;
                  end
                endmodule
                """,
            )
            self.write(root, "test/.gitkeep", "")
            first = self.make_workbench(root)

            specs = first.specs_for(["dut"], "auto", None, None)

            self.assertEqual(len(specs), 1)
            self.assertEqual(specs[0].kind, "cocotb")
            self.assertEqual(specs[0].path.name, "test_dut_starter.py")
            content = specs[0].path.read_text(encoding="utf-8")
            self.assertIn("INPUTS = ('clk', 'reset_n', 'data_in')", content)
            self.assertIn("Clock(_vwb_signal(dut, 'clk'), 10", content)
            self.assertIn('10, "ns"', content)
            self.assertNotIn("units=", content)
            self.assertIn("reset = _vwb_signal(dut, 'reset_n')", content)
            self.assertIn("reset.value = 0", content)
            self.assertIn("reset.value = 1", content)
            self.assertTrue(vwb.is_cocotb_test(specs[0].path))
            _top, hdl_content = first._verilog_starter("dut")
            self.assertIn("logic [3:0] data_in;", hdl_content)
            self.assertIn("data_in = '0;", hdl_content)

            reloaded = self.make_workbench(root)
            repeated = reloaded.specs_for(["dut"], "auto", None, None)
            self.assertEqual(repeated, specs)
            self.assertEqual(specs[0].path.read_text(encoding="utf-8"), content)

    def test_starter_clock_and_reset_name_detection_uses_conventional_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/dut.sv",
                "module dut(input logic data, input logic reset_condition, "
                "input logic preset, input logic reset_value, input logic rstate); "
                "endmodule\n",
            )
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)

            content = workbench._cocotb_starter("dut")
            _top, verilog_content = workbench._verilog_starter("dut")

            self.assertNotIn("reset = _vwb_signal", content)
            self.assertEqual(content.count('Timer(10, "ns")'), 1)
            self.assertNotIn("Clock(", content)
            self.assertIn("#10 $finish", verilog_content)
            for name in (
                "reset_n",
                "resetn",
                "rst_n",
                "rstn",
                "nreset",
                "nrst",
                "arst_n",
                "srst_n",
            ):
                with self.subTest(name=name):
                    self.assertTrue(workbench._reset_is_active_low(name))
                    self.assertEqual(workbench._reset_name([name]), name)
            for name in ("reset", "reset_condition", "reset_button", "rst_sync"):
                with self.subTest(name=name):
                    self.assertFalse(workbench._reset_is_active_low(name))
            for name in (
                "clk",
                "sys_clk",
                "clk_i",
                "clock_i",
                "aclk",
                "iClock",
                "clk_a",
                "clk0",
                "clk_100",
                "clkdiv",
            ):
                with self.subTest(name=name):
                    self.assertEqual(workbench._clock_name([name]), name)
            self.assertIsNone(workbench._clock_name(["clock_enable"]))
            for name in ("preset", "reset_value", "rstate"):
                with self.subTest(name=name):
                    self.assertIsNone(workbench._reset_name([name]))

    def test_generated_starters_initialize_unpacked_array_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write(
                root,
                "src/array_input.sv",
                "module array_input(input logic clk_a, "
                "input logic [7:0] samples [0:1], "
                "input logic [3:0] grid [0:1][0:2]); endmodule\n",
            )
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)

            self.assertEqual(
                workbench.port_directions("array_input"),
                {"clk_a": "input", "samples": "input", "grid": "input"},
            )
            cocotb_content = workbench._cocotb_starter("array_input")
            top, verilog_content = workbench._verilog_starter("array_input")

            self.assertIn("def _vwb_initialize(handle):", cocotb_content)
            self.assertIn("for child in children:", cocotb_content)
            self.assertIn("logic [7:0] samples [0:1];", verilog_content)
            self.assertIn(
                "foreach (samples[__vwb_index_0]) "
                "samples[__vwb_index_0] = '0;",
                verilog_content,
            )
            self.assertIn(
                "foreach (grid[__vwb_index_0,__vwb_index_1]) "
                "grid[__vwb_index_0][__vwb_index_1] = '0;",
                verilog_content,
            )

            if shutil.which("iverilog"):
                starter = self.write(
                    root, "test/test_array_input_starter.sv", verilog_content
                )
                completed = vwb.subprocess.run(
                    [
                        "iverilog",
                        "-g2012",
                        "-s",
                        top,
                        "-o",
                        root / "starter.vvp",
                        source,
                        starter,
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_starter_explains_required_parameters_without_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/required_parameter.sv",
                "module required_parameter #(parameter WIDTH) "
                "(input logic [WIDTH-1:0] data); endmodule\n",
            )
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)

            for generator in (
                workbench._cocotb_starter,
                lambda module: workbench._verilog_starter(module)[1],
            ):
                with self.subTest(generator=generator):
                    with self.assertRaisesRegex(
                        vwb.VWBError, "WIDTH.*no default value"
                    ):
                        generator("required_parameter")

    def test_cocotb_starter_sanitizes_dollar_in_module_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/dut.v",
                "module foo$bar(input sys$clk, input rst$n); endmodule\n",
            )
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)

            spec = workbench.specs_for(["foo$bar"], "cocotb", None, None)[0]
            content = spec.path.read_text(encoding="utf-8")

            self.assertNotIn("$", spec.path.name)
            function_declaration = next(
                line for line in content.splitlines() if line.startswith("async def ")
            )
            self.assertNotIn("$", function_declaration)
            self.assertIn("Clock(_vwb_signal(dut, 'sys$clk'), 10", content)
            self.assertIn("reset = _vwb_signal(dut, 'rst$n')", content)
            self.assertTrue(vwb.is_cocotb_test(spec.path))
            reloaded = self.make_workbench(root)
            self.assertEqual(reloaded.tests[0].dut, "foo$bar")

    def test_generated_starters_preserve_escaped_port_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write(
                root,
                "src/dut.v",
                "module dut(input wire \\sys.clk , input wire \\rst_n , "
                "output wire \\data.out ); "
                "assign \\data.out = \\rst_n ; endmodule\n",
            )
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)

            self.assertEqual(
                workbench.port_directions("dut"),
                {"\\sys.clk": "input", "\\rst_n": "input", "\\data.out": "output"},
            )
            cocotb_content = workbench._cocotb_starter("dut")
            self.assertIn("simulator_name = name[1:]", cocotb_content)
            self.assertIn("if child._name == simulator_name", cocotb_content)
            self.assertIn("_vwb_signal(dut, '\\\\sys.clk')", cocotb_content)
            self.assertIn("_vwb_signal(dut, '\\\\rst_n')", cocotb_content)
            top, verilog_content = workbench._verilog_starter("dut")
            self.assertIn(".\\sys.clk (\\sys.clk )", verilog_content)
            self.assertIn(".\\rst_n (\\rst_n )", verilog_content)

            if shutil.which("iverilog"):
                starter = self.write(root, "test/test_dut_starter.sv", verilog_content)
                completed = vwb.subprocess.run(
                    [
                        "iverilog",
                        "-g2012",
                        "-s",
                        top,
                        "-o",
                        root / "starter.vvp",
                        source,
                        starter,
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_escaped_top_uses_source_and_tool_specific_spellings(self):
        self.assertEqual(vwb.tool_identifier("dut"), "dut")
        self.assertEqual(vwb.tool_identifier("\\odd.name"), "odd.name")
        self.assertEqual(vwb.require_yosys_identifier("\\odd.name"), "odd.name")
        with self.assertRaises(vwb.VWBError):
            vwb.require_yosys_identifier("\\odd;name")
        self.assertEqual(
            vwb.cocotb_toplevel_names("\\odd.name"),
            ("work.odd.name", "odd.name"),
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write(
                root,
                "src/odd.v",
                "module \\odd.name (input wire \\clk.in ); endmodule\n",
            )
            test_path = self.write(
                root,
                "test/test_odd_name.py",
                "import cocotb\n@cocotb.test()\nasync def check(dut):\n    pass\n",
            )
            workbench = self.make_workbench(root)
            spec = vwb.TestSpec("\\odd.name", "cocotb", test_path)
            args = SimpleNamespace(testcase=None, seed=None)

            environment, _results = workbench._cocotb_environment(
                spec, root / ".vwb" / "sim", args, "verilog"
            )
            self.assertEqual(environment["TOPLEVEL"], "work.odd.name")
            self.assertEqual(environment["COCOTB_TOPLEVEL"], "odd.name")
            self.assertEqual(environment["PYGPI_PYTHON_BIN"], vwb.sys.executable)

            success = vwb.subprocess.CompletedProcess([], 0, "", "")
            with (
                mock.patch.object(workbench, "require_tool"),
                mock.patch.object(workbench, "run", return_value=success) as run,
            ):
                compiled, _wave, _simulation = workbench._compile_simulation(
                    spec,
                    root / ".vwb" / "sim",
                    waves=False,
                    wave_format="vcd",
                    defines=[],
                    includes=[],
                    compile_args=[],
                    test_top=None,
                    max_array_words=32,
                )
            self.assertTrue(compiled)
            command = [str(item) for item in run.call_args.args[0]]
            self.assertEqual(command[command.index("-s") + 1], "odd.name")
            self.assertIn(str(source), command)

            dump = root / "dump.v"
            workbench._write_dump_module(dump, "\\odd.name", root / "wave.vcd")
            self.assertIn("$dumpvars(0, \\odd.name );", dump.read_text())

    def test_cocotb_runtime_settings_accept_old_and_new_config_interfaces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)

            def config_result(command: list[str], **_kwargs: object) -> object:
                if command[-1] == "--python-bin":
                    return SimpleNamespace(
                        returncode=0, stdout="/opt/cocotb/bin/python\n", stderr=""
                    )
                return SimpleNamespace(
                    returncode=2, stdout="", stderr="unknown option --libpython"
                )

            with (
                mock.patch.object(
                    workbench,
                    "require_tool",
                    return_value="/opt/cocotb/bin/cocotb-config",
                ),
                mock.patch.object(workbench, "run", side_effect=config_result),
            ):
                workbench._load_cocotb_runtime()

            self.assertEqual(
                workbench._cocotb_runtime["PYGPI_PYTHON_BIN"],
                "/opt/cocotb/bin/python",
            )
            self.assertNotIn("LIBPYTHON_LOC", workbench._cocotb_runtime)

    def test_generated_native_starter_sanitizes_an_escaped_top(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write(
                root,
                "src/odd.v",
                "module \\odd.name (input wire \\clk.in ); endmodule\n",
            )
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)

            spec = workbench.specs_for(["\\odd.name"], "verilog", None, None)[0]
            content = spec.path.read_text(encoding="utf-8")
            self.assertEqual(spec.top, f"test_{vwb.python_identifier_component('\\odd.name')}")
            self.assertIn("  \\odd.name  dut (", content)
            self.assertIn("logic \\clk.in ;", content)

            reloaded = self.make_workbench(root)
            self.assertEqual(reloaded.tests[0].dut, "\\odd.name")
            if shutil.which("iverilog"):
                completed = vwb.subprocess.run(
                    ["iverilog", "-g2012", "-s", spec.top, "-o", root / "starter.vvp", source, spec.path],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "needs Icarus")
    def test_parameterized_verilog_starter_preserves_input_bus_widths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/dut.v",
                "module dut #(parameter WIDTH = 8) "
                "(input wire clk_i, input wire [WIDTH-1:0] data); endmodule\n",
            )
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)

            spec = workbench.specs_for(["dut"], "verilog", None, None)[0]
            content = spec.path.read_text(encoding="utf-8")
            self.assertIn("module test_dut #(\nparameter WIDTH = 8\n);", content)
            self.assertIn("logic [WIDTH-1:0] data;", content)

            args = vwb.make_parser().parse_args(
                ["test", "dut", "--test-language", "verilog"]
            )
            passed, _wave = workbench.run_test_spec(spec, args)
            self.assertTrue(passed)

    def test_explicit_vhdl_starter_request_is_not_silently_changed_to_cocotb(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/dut.vhd",
                "entity dut is end entity; architecture rtl of dut is begin end;\n",
            )
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)

            with self.assertRaisesRegex(vwb.VWBError, "automatic VHDL testbench"):
                workbench.specs_for(["dut"], "vhdl", None, None)

    def test_verilog_test_matching_ignores_filename_case(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/designs.v",
                "module broken;\n"
                "  wire unfinished;\n"
                "module Counter;\n"
                "endmodule\n",
            )
            test_path = self.write(
                root,
                "test/test_counter.py",
                "import cocotb\n@cocotb.test()\nasync def check(dut):\n    pass\n",
            )

            workbench = self.make_workbench(root)

            self.assertEqual(workbench.catalog.names(), ["Counter", "broken"])
            self.assertEqual(workbench.catalog.problems("Counter"), [])
            self.assertEqual(
                workbench.tests,
                [
                    vwb.TestSpec(
                        dut="Counter", kind="cocotb", path=test_path.resolve()
                    )
                ],
            )

    def test_vhdl_entity_and_test_matching_are_case_insensitive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/counter.vhd",
                "entity Counter is end entity; "
                "architecture rtl of Counter is begin end;\n",
            )
            test = self.write(
                root,
                "test/test_counter.py",
                "import cocotb\n@cocotb.test()\nasync def check(dut):\n    pass\n",
            )
            workbench = self.make_workbench(root)

            self.assertEqual(workbench.catalog.definition("counter").name, "Counter")
            self.assertEqual(workbench.catalog.closure("counter")[0].name, "counter.vhd")
            specs = workbench.specs_for(["counter"], "cocotb", None, None)
            self.assertEqual(specs, [vwb.TestSpec("Counter", "cocotb", test.resolve())])

            completion_args = SimpleNamespace(
                root=str(root), src_dir="src", test_dir="test", build_dir=".vwb"
            )
            self.assertEqual(
                vwb.module_name_completer("cou", completion_args), ["Counter"]
            )

    @unittest.skipUnless(
        shutil.which("bash") and shutil.which("register-python-argcomplete"),
        "needs Bash and argcomplete",
    )
    def test_bash_tab_completion_returns_modules_and_saved_waves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/counter.v", "module counter; endmodule\n")
            self.write(root, "test/.gitkeep", "")
            (root / ".vwb/saved-waves/known-good").mkdir(parents=True)
            completion = root / "vwb-completion.bash"
            generated = vwb.subprocess.run(
                [
                    "register-python-argcomplete",
                    "--shell",
                    "bash",
                    "--external-argcomplete-script",
                    str(Path(vwb.__file__).resolve()),
                    "vwb",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            completion.write_text(generated.stdout, encoding="utf-8")

            shell = r'''
source "$1"
completion_function="$(complete -p vwb | sed -n 's/.* -F \([^ ]*\) .*/\1/p')"
COMP_LINE="vwb --root $2 synth co"
COMP_POINT=${#COMP_LINE}
COMP_TYPE=9
COMP_WORDS=(vwb --root "$2" synth co)
COMP_CWORD=4
"$completion_function" vwb co co
printf 'module:%s\n' "${COMPREPLY[@]}"
COMP_LINE="vwb --root $2 wave --load known-"
COMP_POINT=${#COMP_LINE}
COMP_WORDS=(vwb --root "$2" wave --load known-)
COMP_CWORD=5
COMPREPLY=()
"$completion_function" vwb known- known-
printf 'wave:%s\n' "${COMPREPLY[@]}"
'''
            completed = vwb.subprocess.run(
                [
                    "bash",
                    "--noprofile",
                    "--norc",
                    "-c",
                    shell,
                    "bash",
                    str(completion),
                    str(root),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                completed.stdout.splitlines(),
                ["module:counter ", "wave:known-good "],
            )

    def test_split_file_vhdl_testbench_is_discovered_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/dut.vhd",
                "entity dut is end entity; architecture rtl of dut is begin end;\n",
            )
            entity = self.write(
                root,
                "test/test_dut.vhd",
                "entity test_dut is end entity;\n",
            )
            architecture = self.write(
                root,
                "test/test_dut_arch.vhd",
                "architecture test of test_dut is begin end architecture;\n",
            )

            workbench = self.make_workbench(root)

            self.assertEqual(
                workbench.tests,
                [vwb.TestSpec("dut", "vhdl", entity.resolve(), top="test_dut")],
            )
            self.assertEqual(
                workbench.test_catalog.closure("test_dut"),
                [entity.resolve(), architecture.resolve()],
            )

    def test_missing_module_starter_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut(input clk); endmodule\n")
            self.write(root, "test/.gitkeep", "")
            workbench = vwb.Workbench(
                root=root,
                src_dir=root / "src",
                test_dir=root / "test",
                build_dir=root / ".vwb",
                dry_run=True,
            )

            specs = workbench.specs_for(["dut"], "auto", None, None)

            self.assertEqual(specs[0].kind, "cocotb")
            self.assertFalse(specs[0].path.exists())
            self.assertEqual(
                [path.name for path in (root / "test").iterdir()], [".gitkeep"]
            )

    def test_run_tests_continues_and_aggregates_every_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)
            specs = [
                vwb.TestSpec("first", "cocotb", root / "test_first.py"),
                vwb.TestSpec("second", "cocotb", root / "test_second.py"),
                vwb.TestSpec("third", "cocotb", root / "test_third.py"),
            ]
            wave = root / "third.vcd"
            args = SimpleNamespace(testcase=None, seed=None)
            output = io.StringIO()
            errors = io.StringIO()

            with (
                mock.patch.object(
                    workbench,
                    "run_test_spec",
                    side_effect=[
                        (False, None),
                        vwb.VWBError("second compile failed"),
                        (True, wave),
                    ],
                ) as run,
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
            ):
                passed, waves = workbench.run_tests(specs, args)

            self.assertFalse(passed)
            self.assertEqual(run.call_count, 3)
            self.assertEqual(waves, [wave])
            self.assertIn("1/3 test runs passed", output.getvalue())
            self.assertIn("Failed test runs:", output.getvalue())
            for name in ("first", "second", "third"):
                self.assertIn(name, output.getvalue())
            self.assertIn("second compile failed", errors.getvalue())

    def test_run_test_spec_runs_gate_stage_only_when_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(
                root,
                "test/test_dut.v",
                "module test_dut; dut instance(); initial $finish; endmodule\n",
            )
            workbench = self.make_workbench(root)
            spec = workbench.tests[0]
            netlist = root / ".vwb" / "synth" / "dut" / "gate" / "dut_gate.v"

            with (
                mock.patch.object(
                    workbench, "_run_rtl_test_spec", return_value=(True, None)
                ),
                mock.patch.object(
                    workbench, "gate_netlist", return_value=netlist
                ) as gate_netlist,
                mock.patch.object(
                    workbench, "_run_gate_test_spec", return_value=True
                ) as run_gate,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                rtl_only_args = vwb.make_parser().parse_args(["test", "dut"])
                passed, _wave = workbench.run_test_spec(spec, rtl_only_args)
                self.assertTrue(passed)
                gate_netlist.assert_not_called()
                run_gate.assert_not_called()

                gate_netlist.reset_mock()
                run_gate.reset_mock()
                gate_args = vwb.make_parser().parse_args(
                    ["test", "dut", "--gate-level"]
                )
                passed, _wave = workbench.run_test_spec(spec, gate_args)
                self.assertTrue(passed)
                gate_netlist.assert_called_once_with("dut", [], [])
                run_gate.assert_called_once_with(spec, gate_args, netlist)

    def test_native_vhdl_test_reports_gate_skip_instead_of_guaranteed_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/dut.vhd",
                "entity dut is end entity; architecture rtl of dut is begin end;\n",
            )
            test = self.write(
                root,
                "test/test_dut.vhd",
                "entity test_dut is end entity; "
                "architecture test of test_dut is begin end;\n",
            )
            workbench = self.make_workbench(root)
            spec = vwb.TestSpec("dut", "vhdl", test, top="test_dut")
            args = vwb.make_parser().parse_args(["test", "dut", "--gate-level"])
            output = io.StringIO()

            with (
                mock.patch.object(
                    workbench, "_run_rtl_test_spec", return_value=(True, None)
                ),
                mock.patch.object(workbench, "gate_netlist") as gate_netlist,
                contextlib.redirect_stdout(output),
            ):
                passed, _wave = workbench.run_test_spec(spec, args)

            self.assertTrue(passed)
            gate_netlist.assert_not_called()
            self.assertIn("GATE SKIPPED", output.getvalue())

    def test_missing_gate_tool_is_a_setup_skip_not_a_code_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            test = self.write(
                root,
                "test/test_dut.sv",
                "module test_dut; dut instance(); initial $finish; endmodule\n",
            )
            workbench = self.make_workbench(root)
            spec = vwb.TestSpec("dut", "verilog", test, top="test_dut")
            args = vwb.make_parser().parse_args(["test", "dut", "--gate-level"])
            output = io.StringIO()

            with (
                mock.patch.object(
                    workbench, "_run_rtl_test_spec", return_value=(True, None)
                ),
                mock.patch.object(
                    workbench,
                    "gate_netlist",
                    side_effect=vwb.MissingToolError("yosys", ("yosys",)),
                ),
                contextlib.redirect_stdout(output),
            ):
                passed, _wave = workbench.run_test_spec(spec, args)

            self.assertTrue(passed)
            self.assertIn("GATE SKIPPED (yosys not installed)", output.getvalue())
            self.assertEqual(args._setup_skips, ["gate-level simulation: yosys"])

    def test_cocotb_rtl_and_gate_stages_reuse_one_automatic_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            test = self.write(
                root,
                "test/test_dut.py",
                "import cocotb\n@cocotb.test()\nasync def test_dut(dut):\n    pass\n",
            )
            workbench = self.make_workbench(root)
            spec = vwb.TestSpec("dut", "cocotb", test)
            args = vwb.make_parser().parse_args(["test", "dut", "--gate-level"])
            netlist = root / ".vwb" / "synth" / "dut" / "gate" / "dut_gate.v"

            with (
                mock.patch("vwb.os.urandom", return_value=b"\x01\x02\x03\x04"),
                mock.patch.object(
                    workbench, "_run_rtl_test_spec", return_value=(True, None)
                ) as run_rtl,
                mock.patch.object(workbench, "gate_netlist", return_value=netlist),
                mock.patch.object(
                    workbench, "_run_gate_test_spec", return_value=True
                ) as run_gate,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                passed, _wave = workbench.run_test_spec(spec, args)

            self.assertTrue(passed)
            rtl_args = run_rtl.call_args.args[1]
            gate_args = run_gate.call_args.args[1]
            self.assertEqual(rtl_args.seed, 0x01020304)
            self.assertEqual(gate_args.seed, rtl_args.seed)
            self.assertIsNot(rtl_args, args)
            self.assertIsNone(args.seed)

    def test_gate_netlist_supplies_yosys_simlib(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut(input wire a, output wire y); assign y = a; endmodule\n")
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)
            gate_dir = root / ".vwb" / "synth" / "dut" / "gate"
            netlist = gate_dir / "dut_gate.v"
            simlib = gate_dir / "yosys_simlib.v"

            def synth_run(*_args: object, **_kwargs: object) -> object:
                netlist.write_text("module dut; endmodule\n", encoding="ascii")
                simlib.write_text("module \\$alu; endmodule\n", encoding="ascii")
                return vwb.subprocess.CompletedProcess([], 0, "", "")

            with (
                mock.patch.object(workbench, "require_tool"),
                mock.patch.object(workbench, "run", side_effect=synth_run),
            ):
                self.assertEqual(workbench.gate_netlist("dut", [], []), netlist)

            script = (gate_dir / "gate.ys").read_text(encoding="utf-8")
            self.assertIn("synth -top dut", script)
            self.assertIn("write_file", script)
            self.assertIn("+/simlib.v", script)

            spec = vwb.TestSpec(
                dut="dut", kind="cocotb", path=root / "test" / "test_dut.py"
            )
            work_dir = root / ".vwb" / "sim" / "dut" / "gate"
            success = vwb.subprocess.CompletedProcess([], 0, "", "")
            with (
                mock.patch.object(workbench, "require_tool"),
                mock.patch.object(workbench, "run", return_value=success) as run,
            ):
                compiled, _wave, _simulation = workbench._compile_simulation(
                    spec,
                    work_dir,
                    waves=False,
                    wave_format="fst",
                    defines=[],
                    includes=[],
                    compile_args=[],
                    test_top=None,
                    max_array_words=32,
                    gate_netlist=netlist,
                )

            self.assertTrue(compiled)
            command = [str(item) for item in run.call_args.args[0]]
            self.assertLess(command.index(str(simlib)), command.index(str(netlist)))

    def test_large_gate_netlist_uses_word_level_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)
            gate_dir = root / ".vwb" / "synth" / "dut" / "gate"
            netlist = gate_dir / "dut_gate.v"
            simlib = gate_dir / "yosys_simlib.v"
            coarse_netlist = gate_dir / ".dut_coarse.tmp.v"
            coarse_simlib = gate_dir / ".yosys_simlib.coarse.tmp.v"
            calls = 0

            def synth_run(*_args: object, **_kwargs: object) -> object:
                nonlocal calls
                calls += 1
                if calls == 1:
                    simlib.write_text("module \\$alu; endmodule\n", encoding="ascii")
                    netlist.write_bytes(
                        b"x" * (vwb.FULL_GATE_NETLIST_LIMIT_BYTES + 1)
                    )
                else:
                    coarse_netlist.write_text(
                        "module dut; endmodule\n", encoding="ascii"
                    )
                    coarse_simlib.write_text(
                        "module \\$alu; endmodule\n", encoding="ascii"
                    )
                return vwb.subprocess.CompletedProcess([], 0, "", "")

            with (
                mock.patch.object(workbench, "require_tool"),
                mock.patch.object(workbench, "run", side_effect=synth_run),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(workbench.gate_netlist("dut", [], []), netlist)

            self.assertEqual(calls, 2)
            self.assertEqual(netlist.read_text(encoding="ascii"), "module dut; endmodule\n")
            self.assertEqual(
                simlib.read_text(encoding="ascii"), "module \\$alu; endmodule\n"
            )
            coarse_script = (gate_dir / "gate-coarse.ys").read_text(encoding="utf-8")
            self.assertIn("synth -run begin:fine -top dut", coarse_script)

    def test_failed_full_gate_synthesis_uses_word_level_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)
            gate_dir = root / ".vwb" / "synth" / "dut" / "gate"
            netlist = gate_dir / "dut_gate.v"
            simlib = gate_dir / "yosys_simlib.v"
            coarse_netlist = gate_dir / ".dut_coarse.tmp.v"
            coarse_simlib = gate_dir / ".yosys_simlib.coarse.tmp.v"
            calls = 0

            def synth_run(*_args: object, **_kwargs: object) -> object:
                nonlocal calls
                calls += 1
                if calls == 1:
                    return vwb.subprocess.CompletedProcess([], 1, "", "mapped failed")
                coarse_netlist.write_text("module dut; endmodule\n", encoding="ascii")
                coarse_simlib.write_text(
                    "module \\$alu; endmodule\n", encoding="ascii"
                )
                return vwb.subprocess.CompletedProcess([], 0, "", "")

            with (
                mock.patch.object(workbench, "require_tool"),
                mock.patch.object(workbench, "run", side_effect=synth_run),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(workbench.gate_netlist("dut", [], []), netlist)

            self.assertEqual(calls, 2)
            self.assertEqual(netlist.read_text(encoding="ascii"), "module dut; endmodule\n")
            self.assertEqual(
                simlib.read_text(encoding="ascii"), "module \\$alu; endmodule\n"
            )

    def test_systemverilog_simulation_is_native_first_with_sv2v_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.sv", "module dut; endmodule\n")
            self.write(
                root,
                "test/test_dut.sv",
                "module test_dut; dut instance(); initial $finish; endmodule\n",
            )
            workbench = self.make_workbench(root)
            spec = workbench.tests[0]
            converted = root / ".vwb" / "sim" / "converted.v"

            for returncodes, expected_conversions in (([0], 0), ([1, 0], 1)):
                with self.subTest(returncodes=returncodes):
                    runs = [
                        vwb.subprocess.CompletedProcess([], returncode, "", "")
                        for returncode in returncodes
                    ]
                    with (
                        mock.patch.object(workbench, "require_tool"),
                        mock.patch("vwb.find_tool", return_value="/usr/bin/sv2v"),
                        mock.patch.object(
                            workbench,
                            "_convert_systemverilog",
                            return_value=[converted],
                        ) as convert,
                        mock.patch.object(workbench, "run", side_effect=runs) as run,
                        contextlib.redirect_stderr(io.StringIO()),
                    ):
                        compiled, _wave, _simulation = workbench._compile_simulation(
                            spec,
                            root / ".vwb" / "sim" / f"case-{len(returncodes)}",
                            waves=False,
                            wave_format="vcd",
                            defines=[],
                            includes=[],
                            compile_args=[],
                            test_top=None,
                            max_array_words=32,
                        )

                    self.assertTrue(compiled)
                    self.assertEqual(convert.call_count, expected_conversions)
                    self.assertEqual(run.call_count, len(returncodes))
                    first_command = [str(item) for item in run.call_args_list[0].args[0]]
                    self.assertTrue(any(item.endswith("dut.sv") for item in first_command))
                    if expected_conversions:
                        second_command = [
                            str(item) for item in run.call_args_list[1].args[0]
                        ]
                        self.assertIn(str(converted), second_command)

    def test_iverilog_lint_is_native_first_with_sv2v_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.sv", "module dut; endmodule\n")
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)
            converted = root / ".vwb" / "lint" / "converted.v"
            results = [
                vwb.subprocess.CompletedProcess([], 1, "", ""),
                vwb.subprocess.CompletedProcess([], 0, "", ""),
            ]

            with (
                mock.patch.object(workbench, "require_tool"),
                mock.patch("vwb.find_tool", return_value="/usr/bin/sv2v"),
                mock.patch.object(
                    workbench, "yosys_sources", return_value=[converted]
                ) as convert,
                mock.patch.object(workbench, "run", side_effect=results) as run,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                passed = workbench.lint_with_tool("dut", "iverilog", [], [])

            self.assertTrue(passed)
            convert.assert_called_once()
            self.assertEqual(run.call_count, 2)
            first_command = [str(item) for item in run.call_args_list[0].args[0]]
            second_command = [str(item) for item in run.call_args_list[1].args[0]]
            self.assertTrue(any(item.endswith("dut.sv") for item in first_command))
            self.assertIn(str(converted), second_command)

    def test_verible_uses_standard_rules_unless_overridden(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.sv", "module dut; endmodule\n")
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)
            success = vwb.subprocess.CompletedProcess([], 0, "", "")

            with (
                mock.patch.object(workbench, "require_tool"),
                mock.patch.object(workbench, "run", return_value=success) as run,
            ):
                self.assertTrue(workbench.lint_with_tool("dut", "verible", [], []))
                default_command = [str(item) for item in run.call_args.args[0]]
                self.assertEqual(default_command[0], "verible-verilog-lint")
                self.assertFalse(
                    any(item.startswith("--ruleset=") for item in default_command)
                )

                self.assertTrue(
                    workbench.lint_with_tool(
                        "dut", "verible", [], [], verible_args=["--ruleset=all"]
                    )
                )
                overridden_command = [str(item) for item in run.call_args.args[0]]
                self.assertIn("--ruleset=all", overridden_command)

    def test_verible_preprocesses_shared_defines_and_include_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = self.write(
                root,
                "src/child.sv",
                "module child; endmodule\n",
            )
            self.write(
                root,
                "src/dut.sv",
                "`include \"feature.svh\"\nmodule dut; child child_i(); endmodule\n",
            )
            self.write(root, "headers/feature.svh", "`define FEATURE 1\n")
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)
            results = [
                vwb.subprocess.CompletedProcess(
                    [], 0, "module dut; endmodule\n", ""
                ),
                vwb.subprocess.CompletedProcess([], 0, "", ""),
            ]

            with (
                mock.patch.object(workbench, "require_tool"),
                mock.patch.object(
                    workbench, "run", side_effect=results
                ) as run,
            ):
                passed = workbench.lint_with_tool(
                    "dut",
                    "verible",
                    ["VWB_VALIDATION=1"],
                    ["headers"],
                )

            self.assertTrue(passed)
            preprocess = [str(item) for item in run.call_args_list[0].args[0]]
            lint = [str(item) for item in run.call_args_list[1].args[0]]
            self.assertEqual(preprocess[:3], ["iverilog", "-E", "-g2012"])
            self.assertIn("-DVWB_VALIDATION=1", preprocess)
            self.assertIn("-I", preprocess)
            self.assertIn(str(root / "headers"), preprocess)
            self.assertEqual(lint[0], "verible-verilog-lint")
            self.assertIn(str(child), lint)
            self.assertTrue(any("preprocessed" in item for item in lint[1:]))

    def test_verible_does_not_preprocess_unused_defines_or_include_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write(
                root,
                "src/dut.sv",
                """
                // `include "commented_out.svh"
                module dut;
                  initial $display("`include in a string");
                endmodule
                """,
            )
            self.write(root, "headers/unused.svh", "`define UNUSED 1\n")
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)
            success = vwb.subprocess.CompletedProcess([], 0, "", "")

            with (
                mock.patch.object(workbench, "require_tool") as require_tool,
                mock.patch.object(
                    workbench, "run", return_value=success
                ) as run,
            ):
                passed = workbench.lint_with_tool(
                    "dut",
                    "verible",
                    ["UNUSED=1"],
                    ["headers"],
                )

            self.assertTrue(passed)
            require_tool.assert_called_once_with("verible-verilog-lint")
            run.assert_called_once()
            lint = [str(item) for item in run.call_args.args[0]]
            self.assertEqual(lint[0], "verible-verilog-lint")
            self.assertIn(str(source), lint)
            self.assertFalse(any("preprocessed" in item for item in lint))

    def test_verible_preprocesses_a_referenced_command_line_define(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/dut.sv",
                """
                module dut;
                `ifdef FEATURE
                  logic enabled;
                `endif
                endmodule
                """,
            )
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)
            success = vwb.subprocess.CompletedProcess([], 0, "", "")

            with (
                mock.patch.object(workbench, "require_tool"),
                mock.patch.object(
                    workbench, "run", return_value=success
                ) as run,
            ):
                passed = workbench.lint_with_tool(
                    "dut", "verible", ["FEATURE=1"], []
                )

            self.assertTrue(passed)
            self.assertEqual(run.call_count, 2)
            preprocess = [str(item) for item in run.call_args_list[0].args[0]]
            lint = [str(item) for item in run.call_args_list[1].args[0]]
            self.assertEqual(preprocess[:3], ["iverilog", "-E", "-g2012"])
            self.assertIn("-DFEATURE=1", preprocess)
            self.assertTrue(any("preprocessed" in item for item in lint[1:]))

    def test_verilator_and_yosys_report_warnings_without_making_them_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.sv", "module dut; endmodule\n")
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)
            success = vwb.subprocess.CompletedProcess([], 0, "", "")

            with (
                mock.patch.object(workbench, "require_tool"),
                mock.patch.object(workbench, "run", return_value=success) as run,
            ):
                self.assertTrue(workbench.lint_with_tool("dut", "verilator", [], []))
                verilator = [str(item) for item in run.call_args.args[0]]
                self.assertIn("--timing", verilator)
                self.assertIn("-Wno-fatal", verilator)

                self.assertTrue(workbench.lint_with_tool("dut", "yosys", [], []))
                script = (
                    root / ".vwb" / "lint" / "dut" / "yosys" / "lint.ys"
                ).read_text(encoding="utf-8")
                self.assertIn("yosys check\n", script)
                self.assertNotIn("check -assert", script)

    def test_yosys_lint_prints_only_diagnostics_and_keeps_the_full_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)
            result = vwb.subprocess.CompletedProcess(
                [],
                1,
                "Yosys banner\n1. Executing frontend\n"
                "Warning: unused signal\nERROR: invalid connection\nEnd of script\n",
                "",
            )
            diagnostics = io.StringIO()

            with (
                mock.patch.object(workbench, "require_tool"),
                mock.patch.object(workbench, "run", return_value=result) as run,
                contextlib.redirect_stderr(diagnostics),
            ):
                passed = workbench.lint_with_tool("dut", "yosys", [], [])

            self.assertFalse(passed)
            visible = diagnostics.getvalue()
            self.assertIn("Warning: unused signal", visible)
            self.assertIn("ERROR: invalid connection", visible)
            self.assertNotIn("Yosys banner", visible)
            self.assertNotIn("Executing frontend", visible)
            self.assertNotIn("End of script", visible)
            log = root / ".vwb/lint/dut/yosys/yosys.log"
            self.assertIn("Yosys banner", log.read_text(encoding="utf-8"))
            self.assertTrue(run.call_args.kwargs["capture"])

    def test_verible_preprocessor_failure_prints_the_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/dut.sv",
                "`include \"missing.svh\"\nmodule dut; endmodule\n",
            )
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)
            failure = vwb.subprocess.CompletedProcess(
                [], 1, "", "missing.svh: file not found\n"
            )
            errors = io.StringIO()

            with (
                mock.patch.object(workbench, "require_tool"),
                mock.patch.object(workbench, "run", return_value=failure),
                contextlib.redirect_stderr(errors),
            ):
                passed = workbench.lint_with_tool("dut", "verible", [], [])

            self.assertFalse(passed)
            self.assertIn("missing.svh: file not found", errors.getvalue())

    def test_lint_runs_every_selected_tool_and_reports_all_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/designs.v",
                "module first; endmodule\nmodule second; endmodule\n",
            )
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)
            args = vwb.make_parser().parse_args(
                [
                    "lint",
                    "--all",
                    "--linter",
                    "iverilog",
                    "--linter",
                    "yosys",
                    "--iverilog-arg=-Wimplicit",
                    "--yosys-arg=check -assert",
                ]
            )
            calls: list[tuple[str, str]] = []

            def lint(module: str, tool: str, *_args: object, **_kwargs: object) -> bool:
                calls.append((module, tool))
                if (module, tool) == ("first", "iverilog"):
                    return False
                if (module, tool) == ("first", "yosys"):
                    raise vwb.VWBError("Yosys unavailable")
                return True

            output = io.StringIO()
            errors = io.StringIO()
            with (
                mock.patch.object(workbench, "lint_with_tool", side_effect=lint),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
            ):
                status = vwb.command_lint(workbench, args)

            self.assertEqual(status, 1)
            self.assertEqual(
                calls,
                [
                    ("first", "iverilog"),
                    ("first", "yosys"),
                    ("second", "iverilog"),
                    ("second", "yosys"),
                ],
            )
            self.assertIn("2/4 lint checks passed", output.getvalue())
            self.assertIn("first: iverilog: tool reported errors", output.getvalue())
            self.assertIn("first: yosys: Yosys unavailable", output.getvalue())
            self.assertIn("Yosys unavailable", errors.getvalue())

    def test_lint_succeeds_with_one_available_checker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(root, "test/.gitkeep", "")
            workbench = self.make_workbench(root)
            args = vwb.make_parser().parse_args(["lint", "dut"])

            def lint(
                _module: str, tool: str, *_args: object, **_kwargs: object
            ) -> bool:
                if tool == "iverilog":
                    return True
                command = (
                    "verible-verilog-lint" if tool == "verible" else tool
                )
                raise vwb.MissingToolError(command, (command,))

            output = io.StringIO()
            with (
                mock.patch.object(workbench, "lint_with_tool", side_effect=lint),
                contextlib.redirect_stdout(output),
            ):
                status = vwb.command_lint(workbench, args)

            self.assertEqual(status, 0)
            report = output.getvalue()
            self.assertIn("1/1 available lint checks passed", report)
            self.assertIn("3 check(s) skipped", report)
            self.assertNotIn("FAIL", report)

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
                        kind="verilog",
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
                gate_level=False,
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
                gate_level=False,
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
                gate_level=False,
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
    def test_array_word_limit_caps_each_array_without_failing_simulation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "src/limited.sv",
                "`timescale 1ns/1ps\nmodule limited; logic [7:0] memory [0:4]; endmodule\n",
            )
            self.write(
                root,
                "test/test_limited.sv",
                "`timescale 1ns/1ps\nmodule test_limited; limited dut(); "
                "initial begin dut.memory[0] = 0; dut.memory[1] = 1; "
                "dut.memory[2] = 2; dut.memory[3] = 3; dut.memory[4] = 4; "
                "#1 $finish; end endmodule\n",
            )
            workbench = self.make_workbench(root)
            args = SimpleNamespace(
                gate_level=False,
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

            passed, wave_path = workbench.run_test_spec(workbench.tests[0], args)

            self.assertTrue(passed)
            waveform = wave_path.read_text(encoding="utf-8")
            for index in range(4):
                self.assertIn(f"\\memory[{index}]", waveform)
            self.assertNotIn("\\memory[4]", waveform)

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
                ["dut"], "verilog", str(selected), "shared_tb"
            )
            args = SimpleNamespace(
                gate_level=False,
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
                gate_level=False,
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
                gate_level=False,
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

    def test_fpga_supports_modern_generic_and_legacy_gowin_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            constraints = self.write(root, "src/io.cst", "IO_LOC \"clk\" 52;\n")
            self.write(root, "test/.gitkeep", "")
            workbench = vwb.Workbench(
                root=root,
                src_dir=root / "src",
                test_dir=root / "test",
                build_dir=root / ".vwb",
                dry_run=True,
            )
            args = SimpleNamespace(
                board="gowin",
                stage="pnr",
                constraints=str(constraints),
                define=[],
                include=[],
            )

            cases = [
                (
                    "nextpnr-himbaechel-gowin",
                    ["--vopt family=GW1N-9C", f"--vopt cst={constraints}"],
                    [" --uarch ", " --family ", " --cst "],
                ),
                (
                    "nextpnr-himbaechel",
                    [
                        "--uarch gowin",
                        "--vopt family=GW1N-9C",
                        f"--vopt cst={constraints}",
                    ],
                    [" --family ", " --cst "],
                ),
                (
                    "nextpnr-gowin",
                    ["--family GW1N-9C", f"--cst {constraints}"],
                    [" --uarch ", " --vopt "],
                ),
            ]
            alternatives = set(vwb.TOOL_ALTERNATIVES["nextpnr-gowin"])
            for available, expected, absent in cases:
                with self.subTest(available=available):
                    def which(command: str) -> str | None:
                        if command in alternatives:
                            return f"/usr/bin/{command}" if command == available else None
                        return f"/usr/bin/{command}"

                    output = io.StringIO()
                    with (
                        mock.patch("vwb.shutil.which", side_effect=which),
                        contextlib.redirect_stdout(output),
                    ):
                        workbench.run_fpga("dut", args)

                    command = next(
                        line
                        for line in output.getvalue().splitlines()
                        if f"/usr/bin/{available}" in line
                    )
                    for value in expected:
                        self.assertIn(value, command)
                    for value in absent:
                        self.assertNotIn(value, command)

    def test_gowin_tool_resolution_prefers_modern_and_dry_run_defaults_to_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(root, "test/.gitkeep", "")
            workbench = vwb.Workbench(
                root=root,
                src_dir=root / "src",
                test_dir=root / "test",
                build_dir=root / ".vwb",
                dry_run=True,
            )
            with mock.patch(
                "vwb.shutil.which", side_effect=lambda command: f"/bin/{command}"
            ):
                variant, _path = workbench.require_tool_choice("nextpnr-gowin")
            self.assertEqual(variant, "nextpnr-himbaechel-gowin")

            with mock.patch("vwb.shutil.which", return_value=None):
                variant, path = workbench.require_tool_choice("nextpnr-gowin")
            self.assertEqual((variant, path), (variant, variant))
            self.assertEqual(variant, "nextpnr-himbaechel-gowin")

    def test_gowin_tool_error_and_doctor_report_all_supported_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(root, "test/.gitkeep", "")
            workbench = vwb.Workbench(
                root=root,
                src_dir=root / "src",
                test_dir=root / "test",
                build_dir=root / ".vwb",
            )
            with mock.patch("vwb.shutil.which", return_value=None):
                with self.assertRaises(vwb.VWBError) as raised:
                    workbench.require_tool("nextpnr-gowin")
            for candidate in vwb.TOOL_ALTERNATIVES["nextpnr-gowin"]:
                self.assertIn(candidate, str(raised.exception))

            def which(command: str) -> str | None:
                if command == "nextpnr-himbaechel-gowin":
                    return "/usr/bin/nextpnr-himbaechel-gowin"
                if command in vwb.TOOL_ALTERNATIVES["nextpnr-gowin"]:
                    return None
                return f"/usr/bin/{command}"

            output = io.StringIO()
            with (
                mock.patch("vwb.shutil.which", side_effect=which),
                contextlib.redirect_stdout(output),
            ):
                status = vwb.command_doctor(
                    workbench, SimpleNamespace(as_json=True)
                )
            report = vwb.json.loads(output.getvalue())
            self.assertEqual(status, 0)
            self.assertEqual(
                report["gowin"]["nextpnr-gowin"],
                "/usr/bin/nextpnr-himbaechel-gowin",
            )
            self.assertEqual(report["lint"]["iverilog"], "/usr/bin/iverilog")
            self.assertEqual(report["synthesis"]["inkscape"], "/usr/bin/inkscape")

    def test_doctor_allows_missing_optional_tools_and_explains_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "src/dut.v", "module dut; endmodule\n")
            self.write(root, "test/test_dut.sv", "module test_dut; endmodule\n")
            workbench = self.make_workbench(root)

            def which(command: str) -> str | None:
                if command in {"iverilog", "vvp"}:
                    return f"/usr/bin/{command}"
                return None

            output = io.StringIO()
            with (
                mock.patch("vwb.shutil.which", side_effect=which),
                contextlib.redirect_stdout(output),
            ):
                status = vwb.command_doctor(
                    workbench, SimpleNamespace(as_json=False)
                )

            self.assertEqual(status, 0)
            report = output.getvalue()
            self.assertIn("CORE SETUP READY", report)
            self.assertIn("[optional missing]", report)
            self.assertIn("./setup.sh", report)
            self.assertIn("./run-docker.sh", report)

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
