#!/usr/bin/env python3
"""GitHub Actions integration harness for the Verilog Workbench CLI.

The harness treats ``vwb.py list --json`` as the source of truth. It always
audits the complete inventory, while CI can limit tool-heavy phases to a small,
reviewed group of representative modules.
"""

from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import importlib.util
import json
import os
import re
import signal
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ALL_PHASES = (
    "help",
    "regressions",
    "contracts",
    "dry-run",
    "doctor",
    "tests",
    "waves",
    "lint",
    "synth",
    "formal",
    "fpga",
    "clean",
)
COMMAND_TIMEOUT_SECONDS = 600
PNG_MAX_PIXELS = 16_000_000

# Keep this profile small and reviewable. Together these modules cover large
# hierarchies, generated starters, arrays, native HDL tests, SystemVerilog
# packages, clocked logic, arrays, and hierarchical plus split-file VHDL.
REPRESENTATIVE_MODULES: dict[str, str] = {
    "processor": "large pipelined Verilog hierarchy with six child modules",
    "processing_array": "largest source with generated instances and 2D arrays",
    "processing_element": "untested parameterized array design; generates a starter",
    "tinycordic": "wide ports, sequential arithmetic, state machine, and ROM array",
    "encoder": "small parameterized sequential baseline",
    "array_example": "SystemVerilog memory and the native HDL testbench path",
    "sv_beginner_alu": "SystemVerilog package, function, and always_comb",
    "sv_beginner_shift_register": "clocked SystemVerilog with reset and array indexing",
    "vhdl_beginner_accumulator": "hierarchical VHDL with combinational and clocked logic",
    "vhdl_beginner_counter": "VHDL entity and architecture split across files",
}

LINTER_COMMANDS = {
    "iverilog": ("iverilog",),
    "verilator": ("verilator",),
    "yosys": ("yosys",),
    "verible": ("verible-verilog-lint",),
    "ghdl": ("ghdl",),
}

FPGA_PACK_COMMANDS = {
    "gowin": (
        ("nextpnr-himbaechel-gowin", "nextpnr-himbaechel", "nextpnr-gowin"),
        ("gowin_pack",),
    ),
    "ice40": (("nextpnr-ice40",), ("icepack",)),
}


class HarnessError(RuntimeError):
    """Raised when the harness cannot construct a meaningful validation run."""


@dataclass(frozen=True)
class TestCase:
    module: str
    design_language: str
    kind: str
    language: str
    path: str
    top: str | None
    dependency_count: int

    def as_matrix_entry(self, index: int) -> dict[str, Any]:
        return {
            "index": index,
            "module": self.module,
            "design_language": self.design_language,
            "kind": self.kind,
            "language": self.language,
            "path": self.path,
            "top": self.top,
            "runner": [
                "--test-index",
                str(index),
                "--phase",
                "tests",
                "--phase",
                "waves",
            ],
        }


@dataclass(frozen=True)
class CliMetadata:
    commands: tuple[str, ...]
    test_languages: tuple[str, ...]
    wave_formats: tuple[str, ...]
    default_wave_format: str
    default_max_array_words: int
    synth_formats: tuple[str, ...]
    default_synth_format: str
    fpga_boards: tuple[str, ...]
    fpga_stages: tuple[str, ...]
    color_modes: tuple[str, ...]
    clean_scopes: tuple[str, ...]
    linters: tuple[str, ...]
    option_actions: dict[str, dict[str, tuple[str, ...]]]


class Runner:
    def __init__(
        self,
        *,
        root: Path,
        vwb: Path,
        src_dir: str,
        test_dir: str,
        build_dir: Path,
    ) -> None:
        self.root = root
        self.vwb = vwb
        self.src_dir = src_dir
        self.test_dir = test_dir
        self.build_dir = build_dir
        self.failures: list[str] = []
        self.vwb_invocations: list[tuple[str, ...]] = []
        self._target_module: Any | None = None
        self.environment = os.environ.copy()
        prior_pythonpath = self.environment.get("PYTHONPATH")
        self.environment["PYTHONPATH"] = os.pathsep.join(
            item for item in (str(root), prior_pythonpath) if item
        )

    @property
    def vwb_prefix(self) -> list[str]:
        return [
            sys.executable,
            str(self.vwb),
            "--root",
            str(self.root),
            "--src-dir",
            self.src_dir,
            "--test-dir",
            self.test_dir,
            "--build-dir",
            str(self.build_dir),
            "--color",
            "never",
        ]

    def artifact_component(self, value: str) -> str:
        if self._target_module is None:
            self._target_module = load_target_module(self.vwb)
        component = self._target_module.artifact_component(value)
        if not isinstance(component, str) or not component:
            raise HarnessError(
                f"vwb.py returned an invalid artifact component for {value!r}"
            )
        return component

    @staticmethod
    def _display(command: Sequence[str]) -> None:
        print("$ " + shlex.join(command), file=sys.stderr, flush=True)

    def run(
        self,
        command: Sequence[str],
        *,
        label: str,
        expected: Iterable[int] = (0,),
        capture: bool = False,
        record_failure: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [str(item) for item in command]
        if (
            len(command) >= 2
            and Path(command[1]).expanduser().resolve() == self.vwb.resolve()
        ):
            self.vwb_invocations.append(tuple(command[2:]))
        self._display(command)
        process = subprocess.Popen(
            command,
            cwd=self.root,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=COMMAND_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
            result = subprocess.CompletedProcess(command, 124, stdout, stderr)
            detail = f"{label}: timed out after {COMMAND_TIMEOUT_SECONDS} seconds"
            if capture:
                output = "\n".join(
                    part.strip()
                    for part in (result.stdout, result.stderr)
                    if part.strip()
                )
                if output:
                    detail += f"\n{output}"
            if record_failure:
                self.failures.append(detail)
                return result
            raise HarnessError(detail) from None
        result = subprocess.CompletedProcess(
            command, process.returncode, stdout, stderr
        )
        allowed = set(expected)
        if result.returncode not in allowed:
            detail = f"{label}: exit {result.returncode}, expected {sorted(allowed)}"
            if capture:
                output = "\n".join(
                    part.strip() for part in (result.stdout, result.stderr) if part.strip()
                )
                if output:
                    detail += f"\n{output}"
            if record_failure:
                self.failures.append(detail)
            else:
                raise HarnessError(detail)
        return result

    def run_vwb(
        self,
        arguments: Sequence[str],
        *,
        label: str,
        expected: Iterable[int] = (0,),
        capture: bool = False,
        dry_run: bool = False,
        record_failure: bool = True,
        global_options: Sequence[str] = (),
    ) -> subprocess.CompletedProcess[str]:
        command = self.vwb_prefix.copy()
        command.extend(global_options)
        if dry_run:
            command.append("--dry-run")
        command.extend(arguments)
        return self.run(
            command,
            label=label,
            expected=expected,
            capture=capture,
            record_failure=record_failure,
        )


def project_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def parser_action(parser: argparse.ArgumentParser, option: str) -> argparse.Action:
    for action in parser._actions:
        if option in action.option_strings:
            return action
    raise HarnessError(f"vwb.py parser does not define {option}")


def option_actions(parser: argparse.ArgumentParser) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, set[str]] = {}
    for action in parser._actions:
        if not action.option_strings or action.dest == "help":
            continue
        grouped.setdefault(action.dest, set()).update(action.option_strings)
    return {
        destination: tuple(sorted(options))
        for destination, options in sorted(grouped.items())
    }


def load_target_module(vwb_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("_vwb_validation_target", vwb_path)
    if spec is None or spec.loader is None:
        raise HarnessError(f"cannot import {vwb_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def load_cli_metadata(vwb_path: Path) -> CliMetadata:
    module = load_target_module(vwb_path)
    parser = module.make_parser()
    subparsers = next(
        (
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ),
        None,
    )
    if subparsers is None:
        raise HarnessError("vwb.py parser has no commands")
    test_parser = subparsers.choices["test"]
    lint_parser = subparsers.choices["lint"]
    synth_parser = subparsers.choices["synth"]
    fpga_parser = subparsers.choices["fpga"]
    clean_parser = subparsers.choices["clean"]
    wave_format = parser_action(test_parser, "--wave-format")
    test_language = parser_action(test_parser, "--test-language")
    max_array_words = parser_action(test_parser, "--max-array-words")
    synth_format = parser_action(synth_parser, "--format")
    fpga_board = parser_action(fpga_parser, "--board")
    fpga_stage = parser_action(fpga_parser, "--stage")
    color_mode = parser_action(parser, "--color")
    clean_scope = next(
        (action for action in clean_parser._actions if action.dest == "scope"), None
    )
    if clean_scope is None:
        raise HarnessError("vwb.py clean parser has no scope argument")
    linter = parser_action(lint_parser, "--linter")
    canonical_parsers: dict[str, argparse.ArgumentParser] = {}
    seen_parsers: set[int] = set()
    for name, command_parser in subparsers.choices.items():
        if id(command_parser) in seen_parsers:
            continue
        seen_parsers.add(id(command_parser))
        canonical_parsers[name] = command_parser
    return CliMetadata(
        commands=tuple(sorted(subparsers.choices)),
        test_languages=tuple(test_language.choices or ()),
        wave_formats=tuple(wave_format.choices or ()),
        default_wave_format=str(wave_format.default),
        default_max_array_words=int(max_array_words.default),
        synth_formats=tuple(synth_format.choices or ()),
        default_synth_format=str(synth_format.default),
        fpga_boards=tuple(fpga_board.choices or ()),
        fpga_stages=tuple(fpga_stage.choices or ()),
        color_modes=tuple(color_mode.choices or ()),
        clean_scopes=tuple(clean_scope.choices or ()),
        linters=tuple(linter.choices or ()),
        option_actions={
            "global": option_actions(parser),
            **{
                name: option_actions(command_parser)
                for name, command_parser in canonical_parsers.items()
            },
        },
    )


# Every public option must be assigned to an automated matrix or explicitly
# documented as a manual/side-effect path. A new parser option therefore makes
# the contracts phase fail until its validation strategy is deliberate.
MATRIX_OPTION_DESTINATIONS: dict[str, set[str]] = {
    "global": {
        "version",
        "root",
        "src_dir",
        "test_dir",
        "build_dir",
        "color",
        "dry_run",
        "verbose",
    },
    "init": {"init_root", "init_src_dir", "init_test_dir", "init_build_dir", "force"},
    "list": {"as_json"},
    "test": {
        "test",
        "test_language",
        "test_top",
        "testcase",
        "seed",
        "waves",
        "wave_format",
        "max_array_words",
        "define",
        "include",
        "compile_arg",
        "sim_arg",
        "plusarg",
        "gate_level",
        "keep_going",
    },
    "wave": {
        "test",
        "test_language",
        "test_top",
        "testcase",
        "seed",
        "waves",
        "wave_format",
        "max_array_words",
        "define",
        "include",
        "compile_arg",
        "sim_arg",
        "plusarg",
        "gate_level",
        "keep_going",
        "save",
        "tag",
        "replace_tag",
        "load",
        "list_saved",
        "as_json",
    },
    "lint": {
        "all_modules",
        "linter",
        "keep_going",
        "define",
        "include",
        "iverilog_arg",
        "verilator_arg",
        "yosys_arg",
        "verible_arg",
        "ghdl_arg",
    },
    "synth": {"format", "full", "flatten", "schematic", "view", "define", "include"},
    "formal": {"view"},
    "fpga": {"board", "stage", "constraints", "define", "include"},
    "doctor": {"as_json"},
}

# Destinations classify behavior, while this map pins every alternate spelling.
# A new alias must be added deliberately and exercised by the contracts probe.
OPTION_ALIAS_SPELLINGS: dict[str, dict[str, set[str]]] = {
    "global": {
        "verbose": {"-v", "--verbose"},
    },
    "test": {
        "define": {"-D", "--define"},
        "include": {"-I", "--include"},
    },
    "wave": {
        "define": {"-D", "--define"},
        "include": {"-I", "--include"},
    },
    "lint": {
        "define": {"-D", "--define"},
        "include": {"-I", "--include"},
        "verilator_arg": {"--lint-arg", "--verilator-arg"},
    },
    "synth": {
        "schematic": {
            "--schematic",
            "--no-schematic",
        },
        "view": {"--no-view", "--view"},
        "define": {"-D", "--define"},
        "include": {"-I", "--include"},
    },
    "fpga": {
        "define": {"-D", "--define"},
        "include": {"-I", "--include"},
    },
}

MANUAL_OPTION_DESTINATIONS: dict[str, set[str]] = {}


def option_coverage(metadata: CliMetadata) -> dict[str, dict[str, Any]]:
    coverage: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    scopes = set(metadata.option_actions) | set(MATRIX_OPTION_DESTINATIONS) | set(
        MANUAL_OPTION_DESTINATIONS
    )
    for scope in sorted(scopes):
        actual = metadata.option_actions.get(scope, {})
        matrix = MATRIX_OPTION_DESTINATIONS.get(scope, set())
        manual = MANUAL_OPTION_DESTINATIONS.get(scope, set())
        classified = matrix | manual
        unknown = set(actual) - classified
        stale = classified - set(actual)
        if unknown:
            problems.append(
                f"unclassified CLI options for {scope}: {', '.join(sorted(unknown))}"
            )
        if stale:
            problems.append(
                f"stale CLI option classifications for {scope}: "
                + ", ".join(sorted(stale))
            )
        expected_aliases = OPTION_ALIAS_SPELLINGS.get(scope, {})
        actual_aliases = {
            destination: set(options)
            for destination, options in actual.items()
            if len(options) > 1
        }
        if actual_aliases != expected_aliases:
            for destination in sorted(set(actual_aliases) | set(expected_aliases)):
                found = actual_aliases.get(destination, set())
                expected = expected_aliases.get(destination, set())
                if found != expected:
                    problems.append(
                        f"CLI option spellings for {scope}.{destination} are "
                        f"{', '.join(sorted(found)) or 'none'}, expected "
                        f"{', '.join(sorted(expected)) or 'none'}"
                    )
        coverage[scope] = {
            destination: {
                "options": list(options),
                "status": (
                    "matrix" if destination in matrix else "manual-side-effect"
                ),
            }
            for destination, options in actual.items()
        }
    if problems:
        raise HarnessError("; ".join(problems))
    return coverage


def read_inventory(runner: Runner) -> dict[str, Any]:
    result = runner.run_vwb(
        ["list", "--json"],
        label="inventory discovery",
        capture=True,
        record_failure=False,
    )
    try:
        inventory = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise HarnessError(f"vwb.py list --json returned invalid JSON: {error}") from error
    modules = inventory.get("modules")
    if not isinstance(modules, list) or not modules:
        raise HarnessError("inventory contains no modules")

    names: set[str] = set()
    discovered_languages: set[str] = set()
    for module in modules:
        name = module.get("name")
        if not isinstance(name, str) or not name:
            raise HarnessError("inventory contains a module without a name")
        if name in names:
            raise HarnessError(f"inventory contains duplicate module {name!r}")
        names.add(name)
        language = module.get("language")
        if language not in {"verilog", "systemverilog", "vhdl"}:
            raise HarnessError(
                f"module {name!r} has unsupported language metadata {language!r}"
            )
        discovered_languages.add(language)
        problems = module.get("problems", [])
        if not isinstance(problems, list) or problems:
            raise HarnessError(
                f"module {name!r} has discovery problems: {problems!r}"
            )
        for source in module.get("files", []):
            if not project_path(runner.root, source).is_file():
                raise HarnessError(f"module {name!r} references missing source {source}")
    missing_languages = {"systemverilog", "vhdl"} - discovered_languages
    if missing_languages:
        raise HarnessError(
            "example inventory does not exercise: "
            + ", ".join(sorted(missing_languages))
        )
    for package in inventory.get("packages", []):
        package_name = package.get("name")
        if not isinstance(package_name, str) or not package_name:
            raise HarnessError("inventory contains a package without a name")
        if package_name in names:
            raise HarnessError(
                f"package-only design unit {package_name!r} is also runnable"
            )
        for source in package.get("files", []):
            if not project_path(runner.root, source).is_file():
                raise HarnessError(
                    f"package {package_name!r} references missing source {source}"
                )
    for kind in ("interfaces", "primitives"):
        unit_names: set[str] = set()
        for unit in inventory.get(kind, []):
            unit_name = unit.get("name")
            if not isinstance(unit_name, str) or not unit_name:
                raise HarnessError(f"inventory contains a {kind[:-1]} without a name")
            if unit_name in unit_names:
                raise HarnessError(
                    f"inventory contains duplicate {kind[:-1]} {unit_name!r}"
                )
            unit_names.add(unit_name)
            problems = unit.get("problems", [])
            if not isinstance(problems, list) or problems:
                raise HarnessError(
                    f"{kind[:-1]} {unit_name!r} has discovery problems: {problems!r}"
                )
            for source in unit.get("files", []):
                if not project_path(runner.root, source).is_file():
                    raise HarnessError(
                        f"{kind[:-1]} {unit_name!r} references missing source {source}"
                    )
    return inventory


def flatten_tests(inventory: dict[str, Any], root: Path) -> list[TestCase]:
    language_for_kind = {
        "cocotb": "cocotb",
        "verilog": "verilog",
        "vhdl": "vhdl",
    }
    tests: list[TestCase] = []
    seen: set[tuple[str, str, str]] = set()
    for module in inventory["modules"]:
        dependencies = module.get("dependencies", [])
        for test in module.get("tests", []):
            kind = test.get("kind")
            if kind not in language_for_kind:
                raise HarnessError(
                    f"module {module['name']!r} has unsupported test kind {kind!r}"
                )
            path = test.get("path")
            if not isinstance(path, str) or not project_path(root, path).is_file():
                raise HarnessError(
                    f"module {module['name']!r} references missing test {path!r}"
                )
            key = (module["name"], kind, path)
            if key in seen:
                raise HarnessError(f"inventory contains duplicate test {key}")
            seen.add(key)
            tests.append(
                TestCase(
                    module=module["name"],
                    design_language=module["language"],
                    kind=kind,
                    language=language_for_kind[kind],
                    path=path,
                    top=test.get("top"),
                    dependency_count=len(dependencies),
                )
            )
    return sorted(tests, key=lambda item: (item.language, item.module, item.path))


def cocotb_testcase_names(tree: ast.AST) -> list[str]:
    cocotb_aliases = {"cocotb"}
    decorator_aliases: set[str] = set()
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "cocotb":
                    cocotb_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "cocotb":
            for alias in node.names:
                if alias.name == "test":
                    decorator_aliases.add(alias.asname or alias.name)

    def is_test_decorator(decorator: ast.expr) -> bool:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name):
            return target.id in decorator_aliases
        return (
            isinstance(target, ast.Attribute)
            and target.attr == "test"
            and isinstance(target.value, ast.Name)
            and target.value.id in cocotb_aliases
        )

    return sorted(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(is_test_decorator(item) for item in node.decorator_list)
    )


def compile_test_sources(
    runner: Runner, tests: Sequence[TestCase]
) -> dict[Path, list[str]]:
    test_dir = project_path(runner.root, runner.test_dir)
    represented = {project_path(runner.root, test.path) for test in tests}
    discovered: dict[Path, list[str]] = {}
    for path in sorted(test_dir.rglob("*.py")):
        shown = path.relative_to(runner.root) if path.is_relative_to(runner.root) else path
        try:
            source = path.read_text(encoding="utf-8")
            tree = compile(
                source,
                str(path),
                "exec",
                flags=ast.PyCF_ONLY_AST,
                dont_inherit=True,
            )
        except SyntaxError as error:
            runner.failures.append(
                f"Python test does not compile: {shown}:{error.lineno}:{error.offset}: "
                f"{error.msg}"
            )
            continue
        except (OSError, UnicodeError) as error:
            runner.failures.append(f"cannot compile Python test {shown}: {error}")
            continue
        names = cocotb_testcase_names(tree)
        discovered[path.resolve()] = names
        if (
            names
            and not path.name.startswith("test_vwb")
            and path.resolve() not in represented
        ):
            runner.failures.append(
                f"Cocotb test is absent from list --json: {shown} "
                f"(testcases: {', '.join(names)})"
            )
    return discovered


def module_names(inventory: dict[str, Any]) -> list[str]:
    modules = {module["name"]: module for module in inventory["modules"]}
    return sorted(
        modules,
        key=lambda name: (
            len(modules[name].get("dependencies", [])),
            len(modules[name].get("files", [])),
            name,
        ),
    )


def applicable_linters(metadata: CliMetadata, language: str) -> tuple[str, ...]:
    supported = (
        {"all", "ghdl", "iverilog", "verilator", "yosys"}
        if language == "vhdl"
        else {"all", "iverilog", "verilator", "yosys", "verible"}
    )
    return tuple(linter for linter in metadata.linters if linter in supported)


def command_available(runner: Runner, command: str) -> bool:
    return shutil.which(command, path=runner.environment.get("PATH")) is not None


def alternatives_available(runner: Runner, commands: Sequence[str]) -> bool:
    return any(command_available(runner, command) for command in commands)


def linter_available(runner: Runner, linter: str) -> bool:
    commands = LINTER_COMMANDS.get(linter)
    return commands is None or all(
        command_available(runner, command) for command in commands
    )


def fpga_pack_available(runner: Runner, family: str) -> bool:
    command_groups = FPGA_PACK_COMMANDS.get(family, ())
    return bool(command_groups) and all(
        alternatives_available(runner, commands) for commands in command_groups
    )


def report_optional_skip(label: str, missing: Sequence[str]) -> None:
    print(
        f"SKIP {label}: optional tools unavailable: {', '.join(sorted(missing))}",
        file=sys.stderr,
    )


def matrix_document(
    inventory: dict[str, Any],
    tests: Sequence[TestCase],
    modules: Sequence[str],
    coverage: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    inventory_by_name = {module["name"]: module for module in inventory["modules"]}
    return {
        "module": [
            {
                "index": index,
                "name": name,
                "language": inventory_by_name[name].get("language"),
                "files": inventory_by_name[name].get("files", []),
                "dependencies": inventory_by_name[name].get("dependencies", []),
                "has_tests": bool(inventory_by_name[name].get("tests")),
                "runner": [
                    "--module",
                    name,
                    "--phase",
                    "dry-run",
                    "--phase",
                    "tests",
                    "--phase",
                    "lint",
                    "--phase",
                    "synth",
                    "--phase",
                    "fpga",
                ],
            }
            for index, name in enumerate(modules)
        ],
        "test": [
            test.as_matrix_entry(index) for index, test in enumerate(tests)
        ],
        "option_coverage": coverage,
    }


def explicit_test_arguments(
    test: TestCase, *, seed: int, command: str = "test"
) -> list[str]:
    arguments = [
        command,
        test.module,
        "--test",
        test.path,
        "--test-language",
        test.language,
    ]
    if test.top:
        arguments.extend(["--test-top", test.top])
    if test.language == "cocotb":
        arguments.extend(["--seed", str(seed)])
    return arguments


@contextmanager
def fake_executable(
    runner: Runner,
    name: str,
    *,
    exit_code: int = 0,
    marker: Path | None = None,
):
    with tempfile.TemporaryDirectory(prefix=f"vwb-fake-{name}-") as directory:
        executable = Path(directory) / name
        marker_command = (
            f"printf called > {shlex.quote(str(marker))}\n" if marker else ""
        )
        executable.write_text(
            f"#!/bin/sh\n{marker_command}exit {exit_code}\n", encoding="ascii"
        )
        executable.chmod(0o755)
        previous = runner.environment.get("PATH", "")
        runner.environment["PATH"] = os.pathsep.join((directory, previous))
        try:
            yield Path(directory)
        finally:
            runner.environment["PATH"] = previous


COMMAND_SCOPE_ALIASES = {"sim": "test", "gtkwave": "wave"}


def option_probe_value(action: argparse.Action) -> str:
    if action.choices:
        return str(next(iter(action.choices)))
    if action.type is int:
        return "1"
    return {
        "root": ".",
        "src_dir": "src",
        "test_dir": "test",
        "build_dir": ".vwb-option-audit",
        "init_root": ".",
        "init_src_dir": "src",
        "init_test_dir": "test",
        "init_build_dir": ".vwb-option-audit",
        "test": "audit.py",
        "include": ".",
        "define": "VWB_OPTION_AUDIT=1",
        "view": "none",
        "constraints": "audit.constraints",
    }.get(action.dest, "VWB_OPTION_AUDIT")


def option_probe_tokens(parser: argparse.ArgumentParser) -> list[str]:
    tokens: list[str] = []
    for action in parser._actions:
        if action.dest in {"help", "version"}:
            continue
        for option in action.option_strings:
            tokens.append(option)
            if action.nargs != 0:
                tokens.append(option_probe_value(action))
    return tokens


def invoked_option_spellings(
    runner: Runner, metadata: CliMetadata
) -> dict[str, set[str]]:
    observed = {scope: set() for scope in metadata.option_actions}
    known = {
        scope: {
            option
            for options in actions.values()
            for option in options
        }
        for scope, actions in metadata.option_actions.items()
    }

    def record(scope: str, tokens: Sequence[str]) -> None:
        available = known.get(scope, set())
        for token in tokens:
            exact = token.split("=", 1)[0]
            if exact in available:
                observed[scope].add(exact)
                continue
            if token.startswith("-") and not token.startswith("--"):
                for option in available:
                    if len(option) == 2 and token.startswith(option):
                        observed[scope].add(option)
                        break

    command_names = set(metadata.commands)
    target_parser = load_target_module(runner.vwb).make_parser()
    global_value_options = {
        option
        for action in target_parser._actions
        if action.nargs != 0
        for option in action.option_strings
    }

    def command_position(invocation: Sequence[str]) -> int | None:
        index = 0
        global_options = known.get("global", set())
        while index < len(invocation):
            token = invocation[index]
            if token in command_names:
                return index
            spelling = token.split("=", 1)[0]
            if spelling in global_options:
                if spelling in global_value_options and "=" not in token:
                    index += 2
                else:
                    index += 1
                continue
            index += 1
        return None

    for invocation in runner.vwb_invocations:
        command_index = command_position(invocation)
        global_tokens = (
            invocation if command_index is None else invocation[:command_index]
        )
        record("global", global_tokens)
        if command_index is None:
            continue
        command = COMMAND_SCOPE_ALIASES.get(
            invocation[command_index], invocation[command_index]
        )
        record(command, invocation[command_index + 1 :])
    return observed


def validate_option_spelling_invocations(
    runner: Runner, metadata: CliMetadata
) -> None:
    observed = invoked_option_spellings(runner, metadata)
    for scope, actions in metadata.option_actions.items():
        expected = {
            option
            for options in actions.values()
            for option in options
        }
        missing = sorted(expected - observed.get(scope, set()))
        if missing:
            runner.failures.append(
                f"CLI option spellings were not invoked for {scope}: "
                + ", ".join(missing)
            )


def validate_option_spelling_probes(
    runner: Runner, metadata: CliMetadata
) -> None:
    target = load_target_module(runner.vwb)
    parser = target.make_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    runner.run(
        [sys.executable, str(runner.vwb), *option_probe_tokens(parser), "list", "--help"],
        label="exact global option-spelling probe",
        capture=True,
    )
    runner.run(
        [sys.executable, str(runner.vwb), "--version"],
        label="version spelling probe",
        capture=True,
    )
    seen_parsers: set[int] = set()
    for command, command_parser in subparsers.choices.items():
        if id(command_parser) in seen_parsers:
            continue
        seen_parsers.add(id(command_parser))
        runner.run(
            [
                *runner.vwb_prefix,
                command,
                *option_probe_tokens(command_parser),
                "--help",
            ],
            label=f"exact option-spelling probe for {command}",
            capture=True,
        )
    validate_option_spelling_invocations(runner, metadata)


def validate_help(runner: Runner, metadata: CliMetadata) -> None:
    for option in ("-h", "--help"):
        runner.run(
            [sys.executable, str(runner.vwb), option],
            label=f"top-level help via {option}",
        )
    runner.run(
        [sys.executable, str(runner.vwb), "--version"],
        label="version option",
    )
    for command in metadata.commands:
        for option in ("-h", "--help"):
            runner.run(
                [sys.executable, str(runner.vwb), command, option],
                label=f"help for {command} via {option}",
            )


def validate_regressions(runner: Runner) -> None:
    test_dir = project_path(runner.root, runner.test_dir)
    regression_files = sorted(test_dir.rglob("test_vwb*.py"))
    if not regression_files:
        print("No test_vwb*.py regression files discovered; skipping.", file=sys.stderr)
        return
    try:
        test_dir.relative_to(runner.root)
    except ValueError as error:
        raise HarnessError(
            "unittest regressions require --test-dir to be within --root"
        ) from error
    runner.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-v",
            "-s",
            str(test_dir),
            "-t",
            str(runner.root),
            "-p",
            "test_vwb*.py",
        ],
        label="Python regression suite",
    )


def validate_completions(runner: Runner, modules: Sequence[str]) -> None:
    target = load_target_module(runner.vwb)
    parsed_args = argparse.Namespace(
        root=str(runner.root),
        src_dir=runner.src_dir,
        test_dir=runner.test_dir,
        build_dir=str(runner.build_dir),
    )
    completed_modules = target.module_name_completer("", parsed_args)
    if completed_modules != sorted(modules):
        runner.failures.append(
            "module completion does not match the dynamically discovered inventory"
        )
    if modules:
        prefix = modules[0][: max(1, len(modules[0]) // 2)]
        expected = sorted(module for module in modules if module.startswith(prefix))
        if target.module_name_completer(prefix, parsed_args) != expected:
            runner.failures.append(
                f"module completion returned the wrong matches for prefix {prefix!r}"
            )

    with tempfile.TemporaryDirectory(prefix="vwb-completion-") as directory:
        root = Path(directory)
        (root / "src").mkdir()
        (root / "test").mkdir()
        saved = root / "build" / "saved-waves"
        (saved / "known-good").mkdir(parents=True)
        (saved / "known-second").mkdir()
        (saved / "unrelated").mkdir()
        saved_args = argparse.Namespace(
            root=str(root),
            src_dir="src",
            test_dir="test",
            build_dir="build",
        )
        completed_saved = target.saved_wave_completer("known-", saved_args)
        if completed_saved != ["known-good", "known-second"]:
            runner.failures.append(
                "saved-wave completion did not filter and sort known tags"
            )


def validate_contracts(
    runner: Runner,
    metadata: CliMetadata,
    tests: Sequence[TestCase],
    modules: Sequence[str],
) -> None:
    report = option_coverage(metadata)
    matrix_count = sum(
        item["status"] == "matrix"
        for scope in report.values()
        for item in scope.values()
    )
    manual_count = sum(
        item["status"] == "manual-side-effect"
        for scope in report.values()
        for item in scope.values()
    )
    print(
        f"CLI option audit: {matrix_count} matrix, {manual_count} manual-side-effect",
        file=sys.stderr,
    )
    validate_option_spelling_probes(runner, metadata)
    validate_completions(runner, modules)
    for color_mode in metadata.color_modes:
        result = runner.run_vwb(
            ["list"],
            label=f"color mode {color_mode}",
            capture=True,
            global_options=["--color", color_mode, "--verbose"],
        )
        has_ansi = "\x1b[" in result.stdout
        if color_mode == "always" and not has_ansi:
            runner.failures.append("--color always did not colorize list output")
        if color_mode != "always" and has_ansi:
            runner.failures.append(
                f"--color {color_mode} colorized redirected list output"
            )

    with tempfile.TemporaryDirectory(prefix="vwb-init-") as directory:
        project = Path(directory)
        init_command = [
            sys.executable,
            str(runner.vwb),
            "init",
            "--root",
            str(project),
            "--src-dir",
            "rtl",
            "--test-dir",
            "verification",
            "--build-dir",
            "output",
        ]
        runner.run(init_command, label="persistent project initialization")
        runner.run(
            [*init_command, "--force"],
            label="forced project configuration replacement",
        )
        config_path = project / ".vwb.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            runner.failures.append(f"init produced invalid configuration: {error}")
        else:
            expected = {
                "src_dir": "rtl",
                "test_dir": "verification",
                "build_dir": "output",
            }
            for key, value in expected.items():
                if config.get(key) != value:
                    runner.failures.append(
                        f"init configuration {key}={config.get(key)!r}, expected {value!r}"
                    )

    runner.run_vwb(
        ["test", "--kind", "cocotb"],
        label="removed --kind option is rejected",
        expected=(2,),
        capture=True,
    )
    runner.run_vwb(
        ["wave", "--list-saved", "--json"],
        label="saved-wave JSON contract",
        capture=True,
    )
    runner.run_vwb(
        ["lint", "--all", "--keep-going"],
        label="lint --all selection contract",
        capture=True,
        dry_run=True,
    )
    expected_languages = {"auto", "cocotb", "verilog", "vhdl"}
    if set(metadata.test_languages) != expected_languages:
        runner.failures.append(
            "--test-language choices are "
            f"{', '.join(metadata.test_languages)}, expected "
            f"{', '.join(sorted(expected_languages))}"
        )
    expected_linters = {
        "all",
        "iverilog",
        "verilator",
        "yosys",
        "verible",
        "ghdl",
    }
    if set(metadata.linters) != expected_linters:
        runner.failures.append(
            "--linter choices are "
            f"{', '.join(metadata.linters)}, expected "
            f"{', '.join(sorted(expected_linters))}"
        )
    runner.run_vwb(
        ["wave", "--list-saved", "--wave-format", metadata.default_wave_format],
        label="wave list rejects simulation-only --wave-format",
        expected=(2,),
        capture=True,
    )
    runner.run_vwb(
        [
            "wave",
            "--list-saved",
            "--max-array-words",
            str(metadata.default_max_array_words),
        ],
        label="wave list rejects simulation-only --max-array-words",
        expected=(2,),
        capture=True,
    )
    if tests:
        arguments = explicit_test_arguments(tests[0], seed=1)
        arguments.extend(["--waves", "--max-array-words", "-1"])
        runner.run_vwb(
            arguments,
            label="negative array dump limit is rejected",
            expected=(2,),
            capture=True,
            dry_run=True,
        )
        zero_limit = explicit_test_arguments(tests[0], seed=1)
        zero_limit.extend(["--waves", "--max-array-words", "0"])
        runner.run_vwb(
            zero_limit,
            label="unlimited array dump boundary",
            dry_run=True,
            capture=True,
        )
        runner.run_vwb(
            explicit_test_arguments(tests[0], seed=1, command="sim"),
            label="simulation command alias",
            dry_run=True,
            capture=True,
        )
        runner.run_vwb(
            explicit_test_arguments(tests[0], seed=1, command="gtkwave"),
            label="waveform command alias",
            dry_run=True,
            capture=True,
        )


def validate_dry_runs(
    runner: Runner,
    metadata: CliMetadata,
    tests: Sequence[TestCase],
    modules: Sequence[str],
    module_languages: dict[str, str],
    compiled_tests: dict[Path, list[str]],
    seed: int,
) -> None:
    tested_modules = {test.module for test in tests}
    for test in tests:
        arguments = explicit_test_arguments(test, seed=seed)
        testcases = compiled_tests.get(project_path(runner.root, test.path), [])
        if test.language == "cocotb" and testcases:
            arguments.extend(["--testcase", testcases[0]])
        arguments.extend(
            [
                "--waves",
                "--wave-format",
                metadata.default_wave_format,
                "--max-array-words",
                str(metadata.default_max_array_words),
                "--define",
                "VWB_VALIDATION=1",
                "--include",
                ".",
                "--compile-arg=-Wall",
                "--sim-arg=",
                "--plusarg=VWB_VALIDATION",
            ]
        )
        default_result = runner.run_vwb(
            arguments,
            label=f"dry-run test {test.module}:{test.kind}",
            dry_run=True,
            capture=True,
        )
        if any(
            stage in default_result.stdout
            for stage in ("GATE PASS", "GATE FAIL", "GATE SKIPPED")
        ):
            runner.failures.append(
                f"default test unexpectedly ran the gate phase for {test.module}"
            )
        gate_arguments = explicit_test_arguments(test, seed=seed)
        gate_arguments.append("--gate-level")
        gate_result = runner.run_vwb(
            gate_arguments,
            label=f"dry-run gate simulation {test.module}:{test.kind}",
            dry_run=True,
            capture=True,
        )
        expected_gate_stage = (
            "GATE SKIPPED" if test.language == "vhdl" else "GATE PASS"
        )
        if expected_gate_stage not in gate_result.stdout:
            runner.failures.append(
                f"--gate-level did not report its stage for {test.module}"
            )

    if tests:
        runner.run_vwb(
            ["test", "--test-language", "auto", "--keep-going"],
            label="dry-run automatic test discovery",
            dry_run=True,
            capture=True,
        )
        default_wave_arguments = explicit_test_arguments(
            tests[0], seed=seed, command="wave"
        )
        default_wave = runner.run_vwb(
            default_wave_arguments,
            label="dry-run default RTL-only wave",
            dry_run=True,
            capture=True,
        )
        if any(
            stage in default_wave.stdout
            for stage in ("GATE PASS", "GATE FAIL", "GATE SKIPPED")
        ):
            runner.failures.append("default wave unexpectedly ran the gate phase")

        gated_wave = runner.run_vwb(
            [*default_wave_arguments, "--gate-level"],
            label="dry-run opt-in gate-level wave",
            dry_run=True,
            capture=True,
        )
        expected_gate_stage = (
            "GATE SKIPPED" if tests[0].language == "vhdl" else "GATE PASS"
        )
        if expected_gate_stage not in gated_wave.stdout:
            runner.failures.append(
                "wave --gate-level did not run or report the gate phase"
            )
        for wave_format in metadata.wave_formats:
            arguments = explicit_test_arguments(tests[0], seed=seed)
            arguments.extend(["--waves", "--wave-format", wave_format])
            runner.run_vwb(
                arguments,
                label=f"dry-run wave format {wave_format}",
                dry_run=True,
                capture=True,
            )

    covered_linter_options: set[str] = set()
    for module in modules:
        cocotb_bundle = [
            "test",
            module,
            "--test-language",
            "cocotb",
            "--testcase",
            f"test_{module}_starter",
            "--seed",
            str(seed),
            "--waves",
            "--wave-format",
            metadata.default_wave_format,
            "--max-array-words",
            str(metadata.default_max_array_words),
            "--define",
            "VWB_VALIDATION=1",
            "--include",
            ".",
            "--compile-arg=-Wall",
            "--sim-arg=",
            "--plusarg=VWB_VALIDATION",
            "--keep-going",
            "--gate-level",
        ]
        cocotb_result = runner.run_vwb(
            cocotb_bundle,
            label=f"dry-run full Cocotb option bundle for {module}",
            dry_run=True,
            capture=True,
        )
        if module not in tested_modules and "generated starter test:" not in cocotb_result.stdout:
            runner.failures.append(
                f"dry-run did not select a generated Cocotb starter for {module}"
            )
        if module_languages[module] != "vhdl":
            runner.run_vwb(
                [
                    "test",
                    module,
                    "--test-language",
                    "verilog",
                    "--waves",
                    "--wave-format",
                    metadata.default_wave_format,
                    "--max-array-words",
                    str(metadata.default_max_array_words),
                    "--define",
                    "VWB_VALIDATION=1",
                    "--include",
                    ".",
                    "--compile-arg=-Wall",
                    "--sim-arg=",
                    "--plusarg=VWB_VALIDATION",
                    "--keep-going",
                ],
                label=f"dry-run native Verilog starter options for {module}",
                dry_run=True,
                capture=True,
            )
        if module not in tested_modules:
            wave_result = runner.run_vwb(
                ["wave", *cocotb_bundle[1:]],
                label=f"dry-run generated wave starter for {module}",
                dry_run=True,
                capture=True,
            )
            if "generated starter test:" not in wave_result.stdout:
                runner.failures.append(
                    f"dry-run wave did not generate a Cocotb starter for {module}"
                )

        for linter in applicable_linters(metadata, module_languages[module]):
            if linter in covered_linter_options:
                continue
            covered_linter_options.add(linter)
            runner.run_vwb(
                [
                    "lint",
                    module,
                    "--linter",
                    linter,
                    "--define",
                    "VWB_VALIDATION=1",
                    "--include",
                    ".",
                    "--iverilog-arg=-Wall",
                    "--verilator-arg=-Wno-fatal",
                    "--yosys-arg=yosys check",
                    "--verible-arg=--ruleset=none",
                    "--ghdl-arg=-frelaxed-rules",
                ],
                label=f"dry-run {linter} lint options for {module}",
                dry_run=True,
                capture=True,
            )
        runner.run_vwb(
            [
                "synth",
                module,
                "-D",
                "VWB_VALIDATION=1",
                "-I",
                runner.src_dir,
            ],
            label=f"dry-run default synthesis for {module}",
            dry_run=True,
            capture=True,
        )
        if module == modules[0]:
            runner.run_vwb(
                [
                    "synth",
                    module,
                    "--format",
                    "png",
                    "--no-schematic",
                    "--full",
                    "--flatten",
                    "--no-view",
                    "-D",
                    "VWB_VALIDATION=1",
                    "-I",
                    runner.src_dir,
                ],
                label="dry-run combined non-default synthesis switches",
                dry_run=True,
                capture=True,
            )

    constraint_suffixes = {".cst", ".lpf", ".pcf", ".xdc"}
    constraints = sorted(
        path
        for path in project_path(runner.root, runner.src_dir).rglob("*")
        if path.is_file() and path.suffix.lower() in constraint_suffixes
    )
    if modules and not constraints:
        runner.failures.append(
            "dry-run FPGA matrix requires a discovered constraint file "
            "(.cst, .lpf, .pcf, or .xdc)"
        )
    elif modules and metadata.fpga_boards and metadata.fpga_stages:
        probe_count = max(len(metadata.fpga_boards), len(metadata.fpga_stages))
        for index in range(probe_count):
            module = modules[index % len(modules)]
            board = metadata.fpga_boards[index % len(metadata.fpga_boards)]
            stage = metadata.fpga_stages[index % len(metadata.fpga_stages)]
            preferred_suffix = (
                ".cst"
                if "gowin" in board or "tang" in board
                else ".pcf" if "ice" in board else None
            )
            preferred = [
                path for path in constraints if path.suffix.lower() == preferred_suffix
            ]
            choices = preferred or constraints
            constraint = choices[index % len(choices)]
            runner.run_vwb(
                [
                    "fpga",
                    module,
                    "--board",
                    board,
                    "--stage",
                    stage,
                    "--constraints",
                    str(constraint),
                    "-D",
                    "VWB_VALIDATION=1",
                    "-I",
                    runner.src_dir,
                ],
                label=f"dry-run FPGA {module}/{board}/{stage}",
                dry_run=True,
                capture=True,
            )

    with tempfile.TemporaryDirectory(prefix="vwb-formal-config-") as directory:
        config_dir = Path(directory)
        if modules:
            module = modules[0]
            config = config_dir / "representative.sby"
            config.write_text(
                f"[options]\nmode prove\n\n# validation target: {module}\n",
                encoding="utf-8",
            )
            runner.run_vwb(
                ["formal", str(config), "--view"],
                label=f"dry-run formal options for {module}",
                dry_run=True,
                capture=True,
            )


def validate_doctor(runner: Runner, *, portable_tools: bool = False) -> None:
    result = runner.run_vwb(
        ["doctor", "--json"],
        label="toolchain doctor",
        capture=True,
    )
    if result.returncode != 0:
        return
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        runner.failures.append(f"toolchain doctor returned invalid JSON: {error}")
        return
    if not isinstance(report, dict):
        runner.failures.append("toolchain doctor JSON is not an object")
        return
    missing = [
        f"{group}/{command}"
        for group, commands in report.items()
        if isinstance(commands, dict)
        for command, path in commands.items()
        if not path
    ]
    if missing:
        if portable_tools:
            report_optional_skip("doctor optional-tool audit", missing)
        else:
            runner.failures.append(
                "doctor reports tools missing from the exhaustive CI image: "
                + ", ".join(sorted(missing))
            )


def validate_tests(runner: Runner, tests: Sequence[TestCase], seed: int) -> None:
    for test in tests:
        rtl_result = runner.run_vwb(
            explicit_test_arguments(test, seed=seed),
            label=f"RTL simulation {test.module}:{test.kind}",
            capture=True,
        )
        if rtl_result.returncode != 0:
            continue
        if "RTL PASS" not in rtl_result.stdout:
            runner.failures.append(
                f"default simulation did not report RTL PASS: {test.module}:{test.kind}"
            )
        if any(
            stage in rtl_result.stdout
            for stage in ("GATE PASS", "GATE FAIL", "GATE SKIPPED")
        ):
            runner.failures.append(
                f"default simulation unexpectedly ran a gate stage: "
                f"{test.module}:{test.kind}"
            )

        gate_arguments = explicit_test_arguments(test, seed=seed)
        gate_arguments.append("--gate-level")
        gate_result = runner.run_vwb(
            gate_arguments,
            label=f"gate simulation {test.module}:{test.kind}",
            capture=True,
        )
        if gate_result.returncode != 0:
            continue
        expected_gate_report = (
            "GATE SKIPPED" if test.kind == "vhdl" else "GATE PASS"
        )
        if (
            "RTL PASS" not in gate_result.stdout
            or expected_gate_report not in gate_result.stdout
        ):
            runner.failures.append(
                f"opt-in gate simulation did not report RTL PASS and "
                f"{expected_gate_report}: {test.module}:{test.kind}"
            )
        if test.kind == "vhdl":
            gate_netlist = None
        else:
            module_artifact = runner.artifact_component(test.module)
            gate_netlist = (
                runner.build_dir
                / "synth"
                / module_artifact
                / "gate"
                / f"{module_artifact}_gate.v"
            )
        if gate_netlist is not None and (
            not gate_netlist.is_file() or gate_netlist.stat().st_size == 0
        ):
            runner.failures.append(
                f"gate simulation did not produce a gate netlist: {gate_netlist}"
            )
        gate_simlib = gate_netlist.parent / "yosys_simlib.v" if gate_netlist else None
        if gate_simlib is not None and (
            not gate_simlib.is_file() or gate_simlib.stat().st_size == 0
        ):
            runner.failures.append(
                "gate simulation did not preserve the Yosys generic-cell "
                f"library: {gate_simlib}"
            )


def validate_starter_tests(runner: Runner) -> None:
    with tempfile.TemporaryDirectory(prefix="vwb-starter-tests-") as directory:
        root = Path(directory)
        source = root / "src"
        tests = root / "test"
        source.mkdir()
        tests.mkdir()
        (source / "clocked_sv.sv").write_text(
            "module clocked_sv(input logic clk, input logic reset_n, "
            "output logic value);\n"
            "  always_ff @(posedge clk) begin\n"
            "    if (!reset_n) value <= 1'b0; else value <= ~value;\n"
            "  end\n"
            "endmodule\n",
            encoding="ascii",
        )
        (source / "clocked_vhdl.vhd").write_text(
            "library ieee;\n"
            "use ieee.std_logic_1164.all;\n"
            "entity clocked_vhdl is\n"
            "  port (clk : in std_logic; reset : in std_logic; "
            "value : out std_logic);\n"
            "end entity;\n"
            "architecture rtl of clocked_vhdl is\n"
            "  signal state : std_logic := '0';\n"
            "begin\n"
            "  process(clk) begin\n"
            "    if rising_edge(clk) then\n"
            "      if reset = '1' then state <= '0'; else state <= not state; end if;\n"
            "    end if;\n"
            "  end process;\n"
            "  value <= state;\n"
            "end architecture;\n",
            encoding="ascii",
        )
        (source / "plain_verilog.v").write_text(
            "module plain_verilog(input wire value, output wire copy);\n"
            "  assign copy = value;\n"
            "endmodule\n",
            encoding="ascii",
        )
        (source / "array_input.sv").write_text(
            "module array_input(\n"
            "  input logic clk_a,\n"
            "  input logic arst_n,\n"
            "  input logic [7:0] samples [0:1],\n"
            "  output logic [8:0] total\n"
            ");\n"
            "  always_ff @(posedge clk_a or negedge arst_n) begin\n"
            "    if (!arst_n) total <= '0;\n"
            "    else total <= samples[0] + samples[1];\n"
            "  end\n"
            "endmodule\n",
            encoding="ascii",
        )
        (source / "native_vhdl.vhd").write_text(
            "library ieee;\n"
            "use ieee.std_logic_1164.all;\n"
            "entity native_vhdl is\n"
            "  port (value : in std_logic; copy : out std_logic);\n"
            "end entity;\n"
            "architecture rtl of native_vhdl is begin copy <= value; end architecture;\n",
            encoding="ascii",
        )
        (tests / "test_native_vhdl.vhd").write_text(
            "library ieee;\n"
            "use ieee.std_logic_1164.all;\n"
            "use std.env.all;\n"
            "entity test_native_vhdl is end entity;\n"
            "architecture test of test_native_vhdl is\n"
            "  signal value : std_logic := '0';\n"
            "  signal copy : std_logic;\n"
            "begin\n"
            "  dut: entity work.native_vhdl port map (value => value, copy => copy);\n"
            "  process begin\n"
            "    value <= '1'; wait for 1 ns;\n"
            "    assert copy = '1' severity failure;\n"
            "    finish;\n"
            "  end process;\n"
            "end architecture;\n",
            encoding="ascii",
        )
        fixture = Runner(
            root=root,
            vwb=runner.vwb,
            src_dir="src",
            test_dir="test",
            build_dir=root / "build",
        )
        for module in ("clocked_sv", "clocked_vhdl", "array_input"):
            arguments = ["test", module]
            if module == "array_input":
                arguments.extend(["--waves", "--wave-format", "fst"])
            result = fixture.run_vwb(
                arguments,
                label=f"generated Cocotb starter for {module}",
                capture=True,
            )
            starter = tests / f"test_{module}_starter.py"
            if result.returncode == 0 and not starter.is_file():
                fixture.failures.append(f"starter test was not generated: {starter}")
            if starter.is_file():
                content = starter.read_text(encoding="utf-8")
                if "Clock(" not in content or "reset" not in content:
                    fixture.failures.append(
                        f"starter test does not initialize clock and reset: {starter}"
                    )
                if module == "array_input" and "_vwb_initialize" not in content:
                    fixture.failures.append(
                        f"array starter has no recursive input initialization: {starter}"
                    )
            if module == "array_input" and "conflict with an escaped identifier" in (
                (result.stdout or "") + (result.stderr or "")
            ):
                fixture.failures.append(
                    "successful FST array simulation leaked an Icarus warning"
                )
        fixture.run_vwb(
            [
                "test",
                "plain_verilog",
                "--test-language",
                "verilog",
            ],
            label="generated native Verilog starter",
        )
        native_starter = tests / "test_plain_verilog_starter.sv"
        if not native_starter.is_file():
            fixture.failures.append(
                f"native Verilog starter was not generated: {native_starter}"
            )
        fixture.run_vwb(
            [
                "test",
                "array_input",
                "--test-language",
                "verilog",
            ],
            label="generated native SystemVerilog array starter",
        )
        native_array_starter = tests / "test_array_input_starter.sv"
        if not native_array_starter.is_file():
            fixture.failures.append(
                f"native array starter was not generated: {native_array_starter}"
            )
        elif "foreach (samples[" not in native_array_starter.read_text(
            encoding="utf-8"
        ):
            fixture.failures.append(
                f"native array starter does not initialize its array: {native_array_starter}"
            )
        native_vhdl_result = fixture.run_vwb(
            [
                "test",
                "native_vhdl",
                "--test-language",
                "vhdl",
                "--gate-level",
            ],
            label="native VHDL testbench opt-in gate handling",
            capture=True,
        )
        if (
            native_vhdl_result.returncode == 0
            and "GATE SKIPPED" not in native_vhdl_result.stdout
        ):
            fixture.failures.append(
                "native VHDL testbench did not report its gate skip"
            )
        runner.failures.extend(fixture.failures)


def validate_escaped_identifier_fixture(runner: Runner) -> None:
    with tempfile.TemporaryDirectory(prefix="vwb-escaped-identifier-") as directory:
        root = Path(directory)
        source = root / "src"
        tests = root / "test"
        source.mkdir()
        tests.mkdir()
        module = r"\odd.name"
        (source / "escaped_top.sv").write_text(
            "module \\odd.name  (\n"
            "  input wire \\clk.in ,\n"
            "  input wire \\reset.n ,\n"
            "  output reg \\value.out \n"
            ");\n"
            "  always @(posedge \\clk.in ) begin\n"
            "    if (\\reset.n ) \\value.out  <= 1'b0;\n"
            "    else \\value.out  <= ~\\value.out ;\n"
            "  end\n"
            "endmodule\n",
            encoding="ascii",
        )
        fixture = Runner(
            root=root,
            vwb=runner.vwb,
            src_dir="src",
            test_dir="test",
            build_dir=root / "build",
        )
        result = fixture.run_vwb(
            [
                "test",
                module,
                "--waves",
                "--wave-format",
                "vcd",
                "--gate-level",
            ],
            label="escaped identifier starter, waveform, and gate simulation",
            capture=True,
        )
        target = load_target_module(runner.vwb)
        module_artifact = fixture.artifact_component(module)
        starter_name = f"test_{target.python_identifier_component(module)}_starter"
        starter = tests / f"{starter_name}.py"
        starter_artifact = fixture.artifact_component(starter_name)
        waveform = (
            fixture.build_dir
            / "sim"
            / module_artifact
            / f"cocotb-{starter_artifact}"
            / f"{module_artifact}.vcd"
        )
        gate_netlist = (
            fixture.build_dir
            / "synth"
            / module_artifact
            / "gate"
            / f"{module_artifact}_gate.v"
        )
        if result.returncode == 0:
            output = result.stdout or ""
            if "RTL PASS" not in output or "GATE PASS" not in output:
                fixture.failures.append(
                    "escaped identifier simulation did not report RTL and gate passes"
                )
            for label, artifact in (
                ("generated starter", starter),
                ("waveform", waveform),
                ("gate netlist", gate_netlist),
            ):
                if not artifact.is_file() or artifact.stat().st_size == 0:
                    fixture.failures.append(
                        f"escaped identifier {label} is missing or empty: {artifact}"
                    )
        runner.failures.extend(fixture.failures)


def validation_wave_formats(
    metadata: CliMetadata, all_formats: bool
) -> tuple[str, ...]:
    if not all_formats:
        return (metadata.default_wave_format,)
    return (
        metadata.default_wave_format,
        *(
            wave_format
            for wave_format in metadata.wave_formats
            if wave_format != metadata.default_wave_format
        ),
    )


def validate_discovered_starter_tests(
    runner: Runner,
    metadata: CliMetadata,
    modules: Sequence[str],
    tests: Sequence[TestCase],
    *,
    seed: int,
    all_formats: bool,
) -> None:
    tested_modules = {test.module for test in tests}
    missing = [module for module in modules if module not in tested_modules]
    if not missing:
        return
    target = load_target_module(runner.vwb)
    with tempfile.TemporaryDirectory(prefix="vwb-discovered-starters-") as directory:
        root = Path(directory)
        source = root / "src"
        generated_tests = root / "test"
        shutil.copytree(project_path(runner.root, runner.src_dir), source)
        generated_tests.mkdir()
        fixture = Runner(
            root=root,
            vwb=runner.vwb,
            src_dir="src",
            test_dir="test",
            build_dir=root / "build",
        )
        for module in missing:
            component = target.python_identifier_component(module)
            starter = generated_tests / f"test_{component}_starter.py"
            module_artifact = target.artifact_component(module)
            starter_artifact = target.artifact_component(starter.stem)
            starter_content: bytes | None = None
            for format_index, wave_format in enumerate(
                validation_wave_formats(metadata, all_formats)
            ):
                if format_index == 0:
                    arguments = [
                        "test",
                        module,
                        "--seed",
                        str(seed),
                        "--waves",
                        "--wave-format",
                        wave_format,
                        "--gate-level",
                    ]
                else:
                    arguments = [
                        "test",
                        module,
                        "--test",
                        str(starter),
                        "--test-language",
                        "cocotb",
                        "--seed",
                        str(seed),
                        "--waves",
                        "--wave-format",
                        wave_format,
                    ]
                result = fixture.run_vwb(
                    arguments,
                    label=(
                        f"generated starter {wave_format} waveform for discovered "
                        f"module {module}"
                    ),
                    capture=True,
                )
                output = result.stdout or ""
                if format_index == 0:
                    if not starter.is_file():
                        fixture.failures.append(
                            f"discovered module did not get a starter test: {module}"
                        )
                        break
                    try:
                        starter_content = starter.read_bytes()
                    except OSError as error:
                        fixture.failures.append(
                            f"cannot read generated starter for {module}: {error}"
                        )
                        break
                    if result.returncode == 0 and (
                        "RTL PASS" not in output or "GATE PASS" not in output
                    ):
                        fixture.failures.append(
                            f"generated starter did not pass RTL and gate simulation: "
                            f"{module}"
                        )
                else:
                    if "generated starter test:" in output:
                        fixture.failures.append(
                            f"extra waveform run regenerated the starter test: {module}"
                        )
                    try:
                        unchanged = starter.read_bytes() == starter_content
                    except OSError:
                        unchanged = False
                    if not unchanged:
                        fixture.failures.append(
                            f"extra waveform run changed the starter test: {module}"
                        )
                    if result.returncode == 0 and "RTL PASS" not in output:
                        fixture.failures.append(
                            f"generated starter {wave_format} RTL simulation did not pass: "
                            f"{module}"
                        )

                waveform = (
                    fixture.build_dir
                    / "sim"
                    / module_artifact
                    / f"cocotb-{starter_artifact}"
                    / f"{module_artifact}.{wave_format}"
                )
                if result.returncode == 0 and (
                    not waveform.is_file() or waveform.stat().st_size == 0
                ):
                    fixture.failures.append(
                        "missing or empty generated-starter waveform: "
                        f"{waveform}"
                    )
        runner.failures.extend(fixture.failures)


def validate_failure_aggregation(runner: Runner) -> None:
    with tempfile.TemporaryDirectory(prefix="vwb-aggregation-") as directory:
        root = Path(directory)
        source = root / "src"
        tests = root / "test"
        source.mkdir()
        tests.mkdir()
        (source / "bad_design.v").write_text(
            "module bad_design(output wire value); assign value = 1'b0; endmodule\n",
            encoding="ascii",
        )
        (source / "good_design.v").write_text(
            "module good_design(output wire value); assign value = 1'b1; endmodule\n",
            encoding="ascii",
        )
        (tests / "test_bad_design.sv").write_text(
            "module test_bad_design; bad_design dut(); initial begin #1 $fatal; end endmodule\n",
            encoding="ascii",
        )
        (tests / "test_good_design.sv").write_text(
            "module test_good_design; good_design dut(); initial begin #1 $finish; end endmodule\n",
            encoding="ascii",
        )
        fixture = Runner(
            root=root,
            vwb=runner.vwb,
            src_dir="src",
            test_dir="test",
            build_dir=root / "build",
        )
        result = fixture.run_vwb(
            ["test", "--test-language", "verilog"],
            label="simulation failure aggregation",
            expected=(1,),
            capture=True,
        )
        if "good_design" not in result.stdout or "1/2 test runs passed" not in result.stdout:
            fixture.failures.append(
                "simulation stopped before the passing test after an earlier failure"
            )

        (source / "bad_lint.v").write_text(
            "module bad_lint; missing_cell child(); endmodule\n",
            encoding="ascii",
        )
        (source / "good_lint.v").write_text(
            "module good_lint; endmodule\n",
            encoding="ascii",
        )
        lint_result = fixture.run_vwb(
            [
                "lint",
                "bad_lint",
                "good_lint",
                "--linter",
                "iverilog",
            ],
            label="lint failure aggregation",
            expected=(1,),
            capture=True,
        )
        if "good_lint" not in lint_result.stdout or "1/2 lint checks passed" not in lint_result.stdout:
            fixture.failures.append(
                "lint stopped before the passing design after an earlier failure"
            )
        runner.failures.extend(fixture.failures)


def waveform_path(runner: Runner, test: TestCase, wave_format: str) -> Path:
    module_artifact = runner.artifact_component(test.module)
    test_artifact = runner.artifact_component(Path(test.path).stem)
    return (
        runner.build_dir
        / "sim"
        / module_artifact
        / f"{test.kind}-{test_artifact}"
        / f"{module_artifact}.{wave_format}"
    )


def validate_waves(
    runner: Runner,
    metadata: CliMetadata,
    tests: Sequence[TestCase],
    seed: int,
    all_formats: bool,
) -> None:
    formats = validation_wave_formats(metadata, all_formats)
    with fake_executable(runner, "gtkwave") as fake_directory:
        explicit_layout = fake_directory / "validation.gtkw"
        explicit_layout.write_text("[*] vwb validation layout\n", encoding="ascii")
        saved: list[tuple[TestCase, str]] = []
        for test_index, test in enumerate(tests):
            tag = f"ci-wave-{os.getpid()}-{test_index}"
            saved.append((test, tag))
            for format_index, wave_format in enumerate(formats):
                arguments = explicit_test_arguments(
                    test, seed=seed, command="wave"
                )
                arguments.extend(
                    [
                        "--waves",
                        "--wave-format",
                        wave_format,
                        "--max-array-words",
                        str(metadata.default_max_array_words),
                        "--tag",
                        tag,
                    ]
                )
                if format_index:
                    arguments.append("--replace-tag")
                if test_index == 0 and format_index == 0:
                    arguments.extend(["--save", str(explicit_layout)])
                result = runner.run_vwb(
                    arguments,
                    label=f"{test.language} {wave_format} waveform",
                )
                if result.returncode != 0:
                    continue
                artifact = waveform_path(runner, test, wave_format)
                if not artifact.is_file() or artifact.stat().st_size == 0:
                    runner.failures.append(
                        f"missing or empty waveform artifact: {artifact}"
                    )

            if len(formats) == 1 and test_index == 0:
                replacement = explicit_test_arguments(
                    test, seed=seed, command="wave"
                )
                replacement.extend(
                    [
                        "--wave-format",
                        formats[0],
                        "--tag",
                        tag,
                        "--replace-tag",
                    ]
                )
                runner.run_vwb(replacement, label="explicit saved-wave replacement")

        for test, tag in saved:
            runner.run_vwb(
                ["wave", test.module, "--load", tag],
                label=f"load saved waveform {tag}",
            )
        runner.run_vwb(
            ["wave", "--list-saved"], label="human saved-wave inventory"
        )
        runner.run_vwb(
            ["wave", "--list-saved", "--json"],
            label="JSON saved-wave inventory",
            capture=True,
        )


def validate_lint(
    runner: Runner,
    metadata: CliMetadata,
    modules: Sequence[str],
    module_languages: dict[str, str],
    *,
    portable_tools: bool = False,
) -> None:
    for module in modules:
        result = runner.run_vwb(
            [
                "lint",
                module,
                "--linter",
                "all",
                "--define",
                "VWB_VALIDATION=1",
                "--include",
                runner.src_dir,
                "--iverilog-arg=-Wall",
                "--verilator-arg=-Wno-fatal",
                "--yosys-arg=yosys check",
                "--verible-arg=--ruleset=none",
                "--ghdl-arg=-frelaxed-rules",
            ],
            label=f"exhaustive lint {module}",
            capture=True,
        )
        if result.returncode != 0:
            continue
        applicable_tools = [
            tool
            for tool in applicable_linters(metadata, module_languages[module])
            if tool != "all"
        ]
        tools = [
            tool
            for tool in applicable_tools
            if not portable_tools or linter_available(runner, tool)
        ]
        missing_tools = sorted(set(applicable_tools) - set(tools))
        if missing_tools:
            report_optional_skip(
                f"lint backends for {module}", missing_tools
            )
        module_artifact = runner.artifact_component(module)
        for tool in tools:
            if f"with {tool}" not in result.stdout:
                runner.failures.append(
                    f"lint --all skipped applicable {tool} check for {module}"
                )
            output_dir = runner.build_dir / "lint" / module_artifact / tool
            if not output_dir.is_dir():
                runner.failures.append(
                    f"lint {tool} did not create its result directory: {output_dir}"
                )
            if tool == "yosys":
                yosys_log = output_dir / "yosys.log"
                if not yosys_log.is_file() or yosys_log.stat().st_size == 0:
                    runner.failures.append(
                        f"Yosys lint did not keep its full transcript: {yosys_log}"
                    )
        if module_languages[module] != "vhdl" and (
            not portable_tools or linter_available(runner, "verible")
        ):
            style = runner.run_vwb(
                [
                    "lint",
                    module,
                    "--linter",
                    "verible",
                    "--define",
                    "VWB_VALIDATION=1",
                    "--include",
                    runner.src_dir,
                ],
                label=f"default Verible rules {module}",
                expected=(0, 1),
                capture=True,
            )
            combined_output = "\n".join(
                part for part in (style.stdout, style.stderr) if part
            )
            if "with verible" not in combined_output:
                runner.failures.append(
                    f"default Verible lint did not run for {module}"
                )


def synthesis_artifact(runner: Runner, module: str, output_format: str) -> Path:
    module_artifact = runner.artifact_component(module)
    requested = (
        runner.build_dir
        / "synth"
        / module_artifact
        / f"{module_artifact}.{output_format}"
    )
    if output_format == "png" and not requested.is_file():
        svg_fallback = requested.with_suffix(".svg")
        if svg_fallback.is_file():
            return svg_fallback
    return requested


def validate_svg_artifact(runner: Runner, path: Path, *, label: str) -> None:
    try:
        first = next(ET.iterparse(path, events=("start",)), None)
    except (OSError, ET.ParseError) as error:
        runner.failures.append(f"invalid SVG for {label}: {path}: {error}")
        return
    if first is None or first[1].tag.rsplit("}", 1)[-1].lower() != "svg":
        runner.failures.append(f"invalid SVG root for {label}: {path}")


def svg_pixel_dimensions(path: Path) -> tuple[float, float] | None:
    parser = ET.XMLPullParser(events=("start",))
    try:
        attributes: dict[str, str] | None = None
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                parser.feed(chunk)
                for _event, element in parser.read_events():
                    if element.tag.rsplit("}", 1)[-1].lower() != "svg":
                        return None
                    attributes = dict(element.attrib)
                    break
                if attributes is not None:
                    break
    except (OSError, ET.ParseError):
        return None
    if attributes is None:
        return None

    unit_scale = {
        "": 1.0,
        "px": 1.0,
        "pt": 96.0 / 72.0,
        "pc": 16.0,
        "in": 96.0,
        "cm": 96.0 / 2.54,
        "mm": 96.0 / 25.4,
        "q": 96.0 / 101.6,
    }

    def length(value: str | None) -> float | None:
        if value is None:
            return None
        match = re.fullmatch(
            r"\s*([0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
            r"(?:[eE]([+-]?[0-9]+))?\s*([A-Za-z]*)\s*",
            value,
        )
        if match is None or match.group(3).lower() not in unit_scale:
            return None
        number = float(match.group(1))
        if match.group(2):
            number *= 10 ** int(match.group(2))
        return number * unit_scale[match.group(3).lower()]

    width = length(attributes.get("width"))
    height = length(attributes.get("height"))
    if width is None or height is None:
        view_box = attributes.get("viewBox") or attributes.get("viewbox")
        if view_box:
            values = re.split(r"[\s,]+", view_box.strip())
            if len(values) == 4:
                try:
                    width = float(values[2])
                    height = float(values[3])
                except ValueError:
                    return None
    if width is None or height is None or width <= 0 or height <= 0:
        return None
    return width, height


def validate_png_artifact(
    runner: Runner,
    png_path: Path,
    *,
    svg_path: Path | None = None,
    label: str,
) -> None:
    try:
        from PIL import Image
    except ImportError:
        runner.failures.append("Pillow is required to validate synthesis PNG files")
        return
    try:
        with Image.open(png_path) as image:
            image.load()
            width, height = image.size
            rgba = image.convert("RGBA")
            alpha = rgba.getchannel("A").getextrema()
            corners = {
                rgba.getpixel((0, 0)),
                rgba.getpixel((width - 1, 0)),
                rgba.getpixel((0, height - 1)),
                rgba.getpixel((width - 1, height - 1)),
            }
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        runner.failures.append(f"invalid PNG for {label}: {png_path}: {error}")
        return
    if width < 2 or height < 2:
        runner.failures.append(f"PNG is too small for {label}: {width}x{height}")
    if alpha != (255, 255):
        runner.failures.append(f"PNG has transparent pixels for {label}: {png_path}")
    if any(pixel[:3] != (255, 255, 255) for pixel in corners):
        runner.failures.append(f"PNG does not have a white background for {label}")
    if width * height > PNG_MAX_PIXELS * 1.01:
        runner.failures.append(
            f"PNG exceeds the 16-megapixel limit for {label}: {width}x{height}"
        )
    if svg_path is None or not svg_path.is_file():
        return
    try:
        dimensions = svg_pixel_dimensions(svg_path)
        if dimensions is not None:
            svg_width, svg_height = dimensions
            if svg_width * svg_height * 4.0 > PNG_MAX_PIXELS:
                runner.failures.append(
                    f"oversized SVG was rasterized instead of returned for {label}"
                )
                return
            if width < svg_width * 1.9 or height < svg_height * 1.9:
                runner.failures.append(
                    f"PNG density was not increased for {label}: "
                    f"SVG {svg_width:g}x{svg_height:g}, PNG {width}x{height}"
                )
    except (OSError, UnicodeError, ValueError) as error:
        runner.failures.append(f"could not compare PNG density for {label}: {error}")


def validate_synthesis(
    runner: Runner,
    metadata: CliMetadata,
    modules: Sequence[str],
    output_format: str | None,
    schematic: bool | None,
    option_matrix: bool,
) -> None:
    selected_format = output_format or metadata.default_synth_format
    selected_schematic = True if schematic is None else schematic
    if option_matrix:
        combinations = [
            (candidate_format, candidate_schematic, full, flatten)
            for candidate_format in metadata.synth_formats
            for candidate_schematic in (True, False)
            for full in (False, True)
            for flatten in (False, True)
        ]
    else:
        combinations = [(selected_format, selected_schematic, False, False)]
    for module in modules:
        for candidate_format, candidate_schematic, full, flatten in combinations:
            arguments = [
                "synth",
                module,
                "--format",
                candidate_format,
                "--view",
                "none",
                "--schematic" if candidate_schematic else "--no-schematic",
                "-D",
                "VWB_VALIDATION=1",
                "-I",
                runner.src_dir,
            ]
            if full:
                arguments.append("--full")
            if flatten:
                arguments.append("--flatten")
            result = runner.run_vwb(
                arguments,
                label=(
                    f"synthesis {module}/{candidate_format} via "
                    f"{'netlistsvg' if candidate_schematic else 'Yosys show'} "
                    f"(full={full}, flatten={flatten})"
                ),
                capture=True,
            )
            if result.returncode != 0:
                continue
            artifact = synthesis_artifact(runner, module, candidate_format)
            if not artifact.is_file() or artifact.stat().st_size == 0:
                runner.failures.append(
                    f"missing or empty synthesis artifact: {artifact}"
                )
            elif candidate_format == "png":
                if artifact.suffix == ".svg":
                    validate_svg_artifact(
                        runner, artifact, label=f"{module} synthesis SVG fallback"
                    )
                    if "keeping the SVG instead" not in result.stderr:
                        runner.failures.append(
                            f"PNG-to-SVG fallback was not reported for {module}"
                        )
                else:
                    validate_png_artifact(
                        runner,
                        artifact,
                        svg_path=artifact.with_suffix(".svg"),
                        label=f"{module} synthesis",
                    )


def validate_synthesis_fixture(runner: Runner) -> None:
    with tempfile.TemporaryDirectory(prefix="vwb-synthesis-fixture-") as directory:
        root = Path(directory) / "project with spaces"
        source = root / "source files"
        tests = root / "test files"
        includes = root / "include files"
        source.mkdir(parents=True)
        tests.mkdir()
        includes.mkdir()
        (includes / "validation_defs.svh").write_text(
            "`define VALIDATION_WIDTH 2\n",
            encoding="ascii",
        )
        (source / "design.sv").write_text(
            "`include \"validation_defs.svh\"\n"
            "module validation_design(\n"
            "  input logic [`VALIDATION_WIDTH-1:0] a, b,\n"
            "  output logic [`VALIDATION_WIDTH-1:0] y\n"
            ");\n"
            "`ifndef VWB_VALIDATION\n"
            "  assign = ;\n"
            "`endif\n"
            "  assign y = a ^ b;\n"
            "endmodule\n",
            encoding="ascii",
        )
        fixture = Runner(
            root=root,
            vwb=runner.vwb,
            src_dir="source files",
            test_dir="test files",
            build_dir=root / "build files",
        )
        viewer_marker = root / "default-viewer-called"
        with fake_executable(
            fixture,
            "geeqie",
            marker=viewer_marker,
        ):
            default_result = fixture.run_vwb(
                [
                    "synth",
                    "validation_design",
                    "-D",
                    'VWB_VALIDATION="docker smoke"',
                    "-I",
                    "include files",
                ],
                label="default synthesis format, renderer, and viewer",
                capture=True,
            )
        if not viewer_marker.is_file():
            fixture.failures.append(
                "default synthesis did not invoke the configured geeqie viewer"
            )
        default_png = synthesis_artifact(fixture, "validation_design", "png")
        if default_result.returncode == 0:
            if not default_png.is_file() or default_png.stat().st_size == 0:
                fixture.failures.append(
                    f"default synthesis did not produce a PNG: {default_png}"
                )
            elif default_png.suffix == ".svg":
                validate_svg_artifact(
                    fixture, default_png, label="default synthesis SVG fallback"
                )
            else:
                validate_png_artifact(
                    fixture,
                    default_png,
                    svg_path=default_png.with_suffix(".svg"),
                    label="default synthesis",
                )

        with fake_executable(fixture, "netlistsvg", exit_code=1):
            fallback = fixture.run_vwb(
                [
                    "synth",
                    "validation_design",
                    "--format",
                    "png",
                    "--schematic",
                    "--no-view",
                    "-D",
                    'VWB_VALIDATION="docker smoke"',
                    "-I",
                    "include files",
                ],
                label="forced NetlistSVG renderer fallback",
                capture=True,
            )
        fallback_png = synthesis_artifact(fixture, "validation_design", "png")
        if fallback.returncode == 0:
            if "using the Yosys schematic instead" not in fallback.stderr:
                fixture.failures.append(
                    "NetlistSVG failure did not report the Yosys renderer fallback"
                )
            if not fallback_png.is_file() or fallback_png.stat().st_size == 0:
                fixture.failures.append(
                    f"renderer fallback did not produce a PNG: {fallback_png}"
                )
            else:
                validate_png_artifact(
                    fixture,
                    fallback_png,
                    svg_path=fallback_png.with_suffix(".svg"),
                    label="forced Yosys renderer fallback",
                )
        runner.failures.extend(fixture.failures)


def validate_formal(runner: Runner, *, portable_tools: bool = False) -> None:
    formal_commands = ("sby", "yosys", "z3")
    missing = [
        command
        for command in formal_commands
        if not command_available(runner, command)
    ]
    if portable_tools and missing:
        report_optional_skip("formal proof", missing)
        return
    with tempfile.TemporaryDirectory(prefix="vwb-formal-smoke-") as directory:
        root = Path(directory)
        source = root / "formal_smoke.sv"
        config = root / "formal_smoke.sby"
        source.write_text(
            "module formal_smoke(input logic value);\n"
            "  always @* assert (value == value);\n"
            "endmodule\n",
            encoding="ascii",
        )
        config.write_text(
            "[options]\n"
            "mode prove\n"
            "depth 1\n\n"
            "[engines]\n"
            "smtbmc z3\n\n"
            "[script]\n"
            "read -formal formal_smoke.sv\n"
            "prep -top formal_smoke\n\n"
            "[files]\n"
            "formal_smoke.sv\n",
            encoding="ascii",
        )
        result = runner.run_vwb(
            ["formal", str(config)], label="actual SymbiYosys proof"
        )
        output = (
            runner.build_dir
            / "formal"
            / runner.artifact_component(config.stem)
        )
        if result.returncode == 0 and not output.is_dir():
            runner.failures.append(f"formal output directory is missing: {output}")


def validate_fpga(
    runner: Runner,
    metadata: CliMetadata,
    modules: Sequence[str],
    *,
    portable_tools: bool = False,
) -> None:
    if not modules:
        runner.failures.append("FPGA validation has no discovered module")
        return
    source_dir = project_path(runner.root, runner.src_dir)
    for board_index, board in enumerate(metadata.fpga_boards):
        family = {"tangnano9k": "gowin", "icebreaker": "ice40"}.get(
            board, board
        )
        suffix = ".cst" if family == "gowin" else ".pcf"
        constraints = sorted(source_dir.rglob(f"*{suffix}"))
        if not constraints:
            runner.failures.append(
                f"no {suffix} constraints found for actual FPGA {board} synthesis"
            )
            continue
        module = modules[board_index % len(modules)]
        result = runner.run_vwb(
            [
                "fpga",
                module,
                "--board",
                board,
                "--stage",
                "synth",
                "--constraints",
                str(constraints[0]),
                "-D",
                "VWB_VALIDATION=1",
                "-I",
                runner.src_dir,
            ],
            label=f"actual FPGA synthesis for {module}/{board}",
            capture=True,
        )
        module_artifact = runner.artifact_component(module)
        artifact = (
            runner.build_dir
            / "fpga"
            / family
            / module_artifact
            / f"{module_artifact}.json"
        )
        if result.returncode == 0 and (
            not artifact.is_file() or artifact.stat().st_size == 0
        ):
            runner.failures.append(
                f"missing FPGA synthesis artifact: {artifact}"
            )

    validation_module = "validation_fpga"
    inventory_result = runner.run_vwb(
        ["list", "--json"],
        label="bundled FPGA constraint inventory",
        capture=True,
        record_failure=False,
    )
    try:
        inventory_names = {
            item["name"] for item in json.loads(inventory_result.stdout)["modules"]
        }
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        runner.failures.append(
            f"could not inspect bundled FPGA example inventory: {error}"
        )
        inventory_names = set()
    if validation_module not in inventory_names:
        runner.failures.append(
            f"bundled constraint files have no matching {validation_module!r} module"
        )
    else:
        for board in metadata.fpga_boards:
            family = {"tangnano9k": "gowin", "icebreaker": "ice40"}.get(
                board, board
            )
            if portable_tools and not fpga_pack_available(runner, family):
                report_optional_skip(
                    f"bundled FPGA place-route-pack for {board}",
                    [
                        "/".join(commands)
                        for commands in FPGA_PACK_COMMANDS.get(family, ())
                        if not alternatives_available(runner, commands)
                    ],
                )
                continue
            result = runner.run_vwb(
                [
                    "fpga",
                    validation_module,
                    "--board",
                    board,
                    "--stage",
                    "pack",
                ],
                label=f"bundled default FPGA constraints for {board}",
            )
            suffix = ".fs" if family == "gowin" else ".bin"
            module_artifact = runner.artifact_component(validation_module)
            artifact = (
                runner.build_dir
                / "fpga"
                / family
                / module_artifact
                / f"{module_artifact}{suffix}"
            )
            if result.returncode == 0 and (
                not artifact.is_file() or artifact.stat().st_size == 0
            ):
                runner.failures.append(
                    f"missing bundled FPGA artifact for {board}: {artifact}"
                )

    validate_fpga_pack_fixture(
        runner, metadata, portable_tools=portable_tools
    )


def validate_fpga_pack_fixture(
    runner: Runner,
    metadata: CliMetadata,
    *,
    portable_tools: bool = False,
) -> None:
    with tempfile.TemporaryDirectory(prefix="vwb-fpga-fixture-") as directory:
        root = Path(directory) / "project with spaces"
        source = root / "source files"
        tests = root / "test files"
        includes = root / "include files"
        source.mkdir(parents=True)
        tests.mkdir()
        includes.mkdir()
        (includes / "fpga_defs.svh").write_text(
            "`define FIXTURE_WIDTH 1\n",
            encoding="ascii",
        )
        (source / "design.sv").write_text(
            "`include \"fpga_defs.svh\"\n"
            "module validation_fpga(input wire clock, output reg led);\n"
            "`ifndef VWB_VALIDATION\n"
            "  assign = ;\n"
            "`endif\n"
            "  wire [`FIXTURE_WIDTH-1:0] width_check = '0;\n"
            "  initial led = 1'b0;\n"
            "  always @(posedge clock) led <= ~led;\n"
            "endmodule\n",
            encoding="ascii",
        )
        gowin_constraints = source / "fixture.cst"
        gowin_constraints.write_text(
            'IO_LOC "clock" 52;\n'
            'IO_PORT "clock" IO_TYPE=LVCMOS33 PULL_MODE=UP;\n'
            'IO_LOC "led" 10;\n'
            'IO_PORT "led" IO_TYPE=LVCMOS33 DRIVE=8;\n',
            encoding="ascii",
        )
        ice40_constraints = source / "fixture.pcf"
        ice40_constraints.write_text(
            "set_io clock 35\nset_io led 11\n",
            encoding="ascii",
        )
        fixture = Runner(
            root=root,
            vwb=runner.vwb,
            src_dir="source files",
            test_dir="test files",
            build_dir=root / "build files",
        )
        for board in metadata.fpga_boards:
            family = {"tangnano9k": "gowin", "icebreaker": "ice40"}.get(
                board, board
            )
            if portable_tools and not fpga_pack_available(runner, family):
                report_optional_skip(
                    f"FPGA pack fixture for {board}",
                    [
                        "/".join(commands)
                        for commands in FPGA_PACK_COMMANDS.get(family, ())
                        if not alternatives_available(fixture, commands)
                    ],
                )
                continue
            constraints = (
                gowin_constraints if family == "gowin" else ice40_constraints
            )
            result = fixture.run_vwb(
                [
                    "fpga",
                    "validation_fpga",
                    "--board",
                    board,
                    "--stage",
                    "pack",
                    "--constraints",
                    str(constraints),
                    "-D",
                    'VWB_VALIDATION="docker smoke"',
                    "-I",
                    "include files",
                ],
                label=f"actual FPGA place-route-pack fixture for {board}",
            )
            suffix = ".fs" if family == "gowin" else ".bin"
            module_artifact = fixture.artifact_component("validation_fpga")
            artifact = (
                fixture.build_dir
                / "fpga"
                / family
                / module_artifact
                / f"{module_artifact}{suffix}"
            )
            if result.returncode == 0 and (
                not artifact.is_file() or artifact.stat().st_size == 0
            ):
                fixture.failures.append(
                    f"missing FPGA pack fixture artifact for {board}: {artifact}"
                )
        runner.failures.extend(fixture.failures)


def validate_clean(runner: Runner, metadata: CliMetadata) -> None:
    expected_scopes = {
        "temp",
        "sim",
        "waves",
        "synth",
        "lint",
        "fpga",
        "formal",
        "all",
    }
    if set(metadata.clean_scopes) != expected_scopes:
        runner.failures.append(
            "clean scopes are "
            f"{', '.join(metadata.clean_scopes)}, expected "
            f"{', '.join(sorted(expected_scopes))}"
        )
        return

    with tempfile.TemporaryDirectory(prefix="vwb-clean-") as directory:
        root = Path(directory)
        source = root / "src"
        tests = root / "test"
        source.mkdir()
        tests.mkdir()
        (source / "clean_fixture.v").write_text(
            "module clean_fixture(input wire value, output wire copy);\n"
            "  assign copy = value;\n"
            "endmodule\n",
            encoding="ascii",
        )
        fixture = Runner(
            root=root,
            vwb=runner.vwb,
            src_dir="src",
            test_dir="test",
            build_dir=root / "build",
        )
        fixture.run_vwb(
            [
                "synth",
                "clean_fixture",
                "--format",
                "json",
                "--no-view",
            ],
            label="create retained synthesis result for clean validation",
            capture=True,
        )
        synth_sentinel = fixture.build_dir / "synth" / "clean_fixture" / "clean_fixture.json"
        wave_sentinel = fixture.build_dir / "saved-waves" / "saved" / "wave.vcd"
        wave_sentinel.parent.mkdir(parents=True)
        wave_sentinel.write_text("$date validation $end\n", encoding="ascii")
        layout_sentinel = (
            fixture.build_dir / "sim" / "clean_fixture" / "run" / "clean_fixture.gtkw"
        )
        layout_sentinel.parent.mkdir(parents=True)
        layout_sentinel.write_text("[*] saved layout\n", encoding="ascii")

        temporary_scopes = ("sim", "lint")
        retained_result_scopes = ("fpga", "formal")
        explicit_scopes = (*temporary_scopes, *retained_result_scopes)

        def populate_scopes() -> None:
            for scope in explicit_scopes:
                sentinel = fixture.build_dir / scope / "sentinel.txt"
                sentinel.parent.mkdir(parents=True, exist_ok=True)
                sentinel.write_text(scope + "\n", encoding="ascii")

        def assert_retained(label: str) -> None:
            for retained in (synth_sentinel, wave_sentinel):
                if not retained.is_file():
                    fixture.failures.append(
                        f"{label} removed retained artifact: {retained}"
                    )

        def assert_temp_retained(label: str) -> None:
            assert_retained(label)
            if not layout_sentinel.is_file():
                fixture.failures.append(
                    f"{label} removed retained artifact: {layout_sentinel}"
                )
            for scope in retained_result_scopes:
                retained = fixture.build_dir / scope / "sentinel.txt"
                if not retained.is_file():
                    fixture.failures.append(
                        f"{label} removed retained {scope} result: {retained}"
                    )

        def assert_temporary_removed(label: str) -> None:
            for scope in temporary_scopes:
                target = fixture.build_dir / scope
                if scope == "sim":
                    target = target / "sentinel.txt"
                if target.exists():
                    fixture.failures.append(
                        f"{label} left temporary {scope} results behind"
                    )

        populate_scopes()
        fixture.run_vwb(["clean"], label="default temporary clean")
        assert_temp_retained("plain clean")
        assert_temporary_removed("plain clean")

        populate_scopes()
        fixture.run_vwb(["clean", "temp"], label="explicit temporary clean")
        assert_temp_retained("clean temp")
        assert_temporary_removed("clean temp")

        for scope in explicit_scopes:
            sentinel = fixture.build_dir / scope / "sentinel.txt"
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text(scope + "\n", encoding="ascii")
            fixture.run_vwb(["clean", scope], label=f"clean scope {scope}")
            if (fixture.build_dir / scope).exists():
                fixture.failures.append(f"clean {scope} left its target behind")
            assert_retained(f"clean {scope}")

        fixture.run_vwb(["clean", "waves"], label="manual saved-wave clean")
        if wave_sentinel.exists():
            fixture.failures.append("clean waves did not remove saved waveforms")
        if not synth_sentinel.is_file():
            fixture.failures.append("clean waves removed synthesis results")

        fixture.run_vwb(["clean", "synth"], label="manual synthesis clean")
        if synth_sentinel.exists():
            fixture.failures.append("clean synth did not remove synthesis results")

        other = fixture.build_dir / "other" / "sentinel.txt"
        other.parent.mkdir(parents=True)
        other.write_text("all\n", encoding="ascii")
        fixture.run_vwb(["clean", "all"], label="manual clean scope all")
        if fixture.build_dir.exists():
            fixture.failures.append(
                f"clean all left the build directory behind: {fixture.build_dir}"
            )
        runner.failures.extend(fixture.failures)


def select_modules(all_modules: Sequence[str], requested: Sequence[str]) -> list[str]:
    if not requested:
        return list(all_modules)
    available = set(all_modules)
    unknown = sorted(set(requested) - available)
    if unknown:
        raise HarnessError("unknown --module selection: " + ", ".join(unknown))
    requested_set = set(requested)
    return [module for module in all_modules if module in requested_set]


def select_representative_modules(
    inventory: dict[str, Any],
    all_modules: Sequence[str],
    tests: Sequence[TestCase],
) -> list[str]:
    if len(REPRESENTATIVE_MODULES) != 10:
        raise HarnessError("the representative CI profile must contain 10 modules")

    available = set(all_modules)
    selected = list(REPRESENTATIVE_MODULES)
    missing = sorted(set(selected) - available)
    if missing:
        raise HarnessError(
            "representative CI modules are missing from the inventory: "
            + ", ".join(missing)
        )

    inventory_by_name = {
        module["name"]: module for module in inventory["modules"]
    }
    languages = {
        inventory_by_name[name]["language"] for name in selected
    }
    required_languages = {"verilog", "systemverilog", "vhdl"}
    if languages != required_languages:
        raise HarnessError(
            "representative CI modules must cover Verilog, SystemVerilog, and VHDL"
        )
    for language in required_languages:
        count = sum(
            inventory_by_name[name]["language"] == language for name in selected
        )
        if count < 2:
            raise HarnessError(
                f"representative CI profile needs at least two {language} modules"
            )

    selected_set = set(selected)
    all_test_kinds = {test.kind for test in tests}
    selected_test_kinds = {
        test.kind for test in tests if test.module in selected_set
    }
    missing_test_kinds = sorted(all_test_kinds - selected_test_kinds)
    if missing_test_kinds:
        raise HarnessError(
            "representative CI modules do not cover discovered test kinds: "
            + ", ".join(missing_test_kinds)
        )

    has_tests = {
        name: bool(inventory_by_name[name].get("tests")) for name in selected
    }
    if not any(has_tests.values()) or all(has_tests.values()):
        raise HarnessError(
            "representative CI modules must include tested and untested designs"
        )
    if not any(inventory_by_name[name].get("dependencies") for name in selected):
        raise HarnessError("representative CI modules must include a hierarchy")
    if not any(
        len(inventory_by_name[name].get("files", [])) > 1 for name in selected
    ):
        raise HarnessError("representative CI modules must include a multi-file design")
    return selected


def select_tests(
    all_tests: Sequence[TestCase],
    indices: Sequence[int],
    requested_modules: Sequence[str],
) -> list[TestCase]:
    if indices:
        invalid = sorted({index for index in indices if index < 0 or index >= len(all_tests)})
        if invalid:
            raise HarnessError(
                "invalid --test-index selection: "
                + ", ".join(str(index) for index in invalid)
            )
        selected_indices = set(indices)
        selected = [
            test for index, test in enumerate(all_tests) if index in selected_indices
        ]
    else:
        selected = list(all_tests)
    if requested_modules:
        module_set = set(requested_modules)
        selected = [test for test in selected if test.module in module_set]
    if indices and not selected:
        raise HarnessError("--test-index and --module selections do not intersect")
    return selected


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the complete vwb.py inventory and run tool-heavy checks on "
            "the selected modules."
        )
    )
    parser.add_argument("--root", default=str(REPOSITORY_ROOT))
    parser.add_argument("--vwb", help="vwb.py path; defaults to ROOT/vwb.py")
    parser.add_argument("--src-dir", default="examples/src")
    parser.add_argument("--test-dir", default="examples/test")
    parser.add_argument(
        "--build-dir",
        help="artifact directory; defaults to an isolated temporary directory",
    )
    parser.add_argument(
        "--phase",
        action="append",
        choices=ALL_PHASES,
        help="run one phase; repeat as needed (default: all phases)",
    )
    parser.add_argument("--seed", type=int, default=1)
    module_selection = parser.add_mutually_exclusive_group()
    module_selection.add_argument(
        "--module",
        action="append",
        default=[],
        help="limit module phases to one discovered module; repeat as needed",
    )
    module_selection.add_argument(
        "--representative-modules",
        action="store_true",
        help="limit tool-heavy phases to the reviewed 10-module CI profile",
    )
    parser.add_argument(
        "--portable-tools",
        action="store_true",
        help=(
            "skip optional backends unavailable from a distribution while "
            "still requiring the main simulation and synthesis toolchain"
        ),
    )
    parser.add_argument(
        "--test-index",
        action="append",
        type=int,
        default=[],
        help="limit test phases to an index from --emit-matrix; repeat as needed",
    )
    parser.add_argument(
        "--all-wave-formats",
        action="store_true",
        help=(
            "generate every CLI-supported waveform format for selected tests "
            "and generated starters"
        ),
    )
    parser.add_argument(
        "--synth-format",
        help="actual synthesis format (default: the default reported by vwb.py)",
    )
    synth_renderer = parser.add_mutually_exclusive_group()
    synth_renderer.add_argument(
        "--synth-schematic",
        dest="synth_schematic",
        action="store_true",
        help="use NetlistSVG for the actual synthesis phase",
    )
    synth_renderer.add_argument(
        "--synth-no-schematic",
        dest="synth_schematic",
        action="store_false",
        help="use the Yosys renderer for the actual synthesis phase",
    )
    parser.set_defaults(synth_schematic=None)
    parser.add_argument(
        "--synth-option-matrix",
        action="store_true",
        help="run every synthesis format and both renderers for each module",
    )
    parser.add_argument(
        "--keep-build",
        action="store_true",
        help="retain a harness-created temporary build directory",
    )
    parser.add_argument(
        "--emit-matrix",
        action="store_true",
        help="write the discovered JSON matrix to stdout and exit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    root = Path(args.root).expanduser().resolve()
    vwb = project_path(root, args.vwb or "vwb.py")
    if not root.is_dir():
        raise HarnessError(f"project root does not exist: {root}")
    if not vwb.is_file():
        raise HarnessError(f"vwb.py does not exist: {vwb}")

    temporary_build = args.build_dir is None
    build_dir = (
        Path(tempfile.mkdtemp(prefix="vwb-validation-"))
        if temporary_build
        else project_path(root, args.build_dir)
    )
    phases = list(args.phase or ALL_PHASES)
    if args.keep_build and "clean" in phases:
        phases.remove("clean")

    runner = Runner(
        root=root,
        vwb=vwb,
        src_dir=args.src_dir,
        test_dir=args.test_dir,
        build_dir=build_dir,
    )
    try:
        metadata = load_cli_metadata(vwb)
        inventory = read_inventory(runner)
        tests = flatten_tests(inventory, root)
        modules = module_names(inventory)
        module_languages = {
            module["name"]: module["language"]
            for module in inventory["modules"]
        }
        if not modules:
            raise HarnessError("inventory has no module matrix")
        coverage = option_coverage(metadata)
        compiled_tests = compile_test_sources(runner, tests)
        if args.emit_matrix:
            document = matrix_document(inventory, tests, modules, coverage)
            document["integrity_errors"] = list(runner.failures)
            print(json.dumps(document, indent=2))
            return 1 if runner.failures else 0

        if args.representative_modules and args.test_index:
            raise HarnessError(
                "--representative-modules cannot be combined with --test-index"
            )
        if args.representative_modules:
            selected_modules = select_representative_modules(
                inventory, modules, tests
            )
            selected_tests = select_tests(tests, [], selected_modules)
        else:
            selected_modules = select_modules(modules, args.module)
            selected_tests = select_tests(tests, args.test_index, args.module)
            if args.test_index and not args.module:
                test_modules = {test.module for test in selected_tests}
                selected_modules = [
                    module for module in modules if module in test_modules
                ]
        if args.synth_format and args.synth_format not in metadata.synth_formats:
            raise HarnessError(
                f"unsupported --synth-format {args.synth_format!r}; choose from: "
                + ", ".join(metadata.synth_formats)
            )

        print(
            f"Discovered {len(inventory['modules'])} modules and {len(tests)} tests; "
            f"selected {len(selected_modules)} modules and {len(selected_tests)} tests; "
            f"build directory: {build_dir}",
            file=sys.stderr,
        )
        if args.representative_modules:
            print("Representative module profile:", file=sys.stderr)
            for module in selected_modules:
                print(
                    f"  {module}: {REPRESENTATIVE_MODULES[module]}",
                    file=sys.stderr,
                )
        generated_starter_coverage_done = False
        for phase in phases:
            print(f"\n== {phase} ==", file=sys.stderr, flush=True)
            if phase == "help":
                validate_help(runner, metadata)
            elif phase == "regressions":
                validate_regressions(runner)
            elif phase == "contracts":
                validate_contracts(
                    runner,
                    metadata,
                    selected_tests,
                    modules,
                )
            elif phase == "dry-run":
                validate_dry_runs(
                    runner,
                    metadata,
                    selected_tests,
                    selected_modules,
                    module_languages,
                    compiled_tests,
                    seed=args.seed,
                )
            elif phase == "doctor":
                validate_doctor(
                    runner, portable_tools=args.portable_tools
                )
            elif phase == "tests":
                if selected_tests:
                    validate_tests(runner, selected_tests, seed=args.seed)
                if not generated_starter_coverage_done:
                    validate_discovered_starter_tests(
                        runner,
                        metadata,
                        selected_modules,
                        selected_tests,
                        seed=args.seed,
                        all_formats=args.all_wave_formats,
                    )
                    generated_starter_coverage_done = True
                if not selected_tests and not selected_modules:
                    runner.failures.append("inventory contains no simulation candidates")
                if not args.module and not args.test_index:
                    validate_starter_tests(runner)
                    validate_escaped_identifier_fixture(runner)
                    validate_failure_aggregation(runner)
            elif phase == "waves":
                if not generated_starter_coverage_done:
                    validate_discovered_starter_tests(
                        runner,
                        metadata,
                        selected_modules,
                        selected_tests,
                        seed=args.seed,
                        all_formats=args.all_wave_formats,
                    )
                    generated_starter_coverage_done = True
                if not selected_tests and not selected_modules:
                    runner.failures.append("inventory contains no waveform candidates")
                elif selected_tests:
                    validate_waves(
                        runner,
                        metadata,
                        selected_tests,
                        seed=args.seed,
                        all_formats=args.all_wave_formats,
                    )
            elif phase == "lint":
                validate_lint(
                    runner,
                    metadata,
                    selected_modules,
                    module_languages,
                    portable_tools=args.portable_tools,
                )
            elif phase == "synth":
                validate_synthesis(
                    runner,
                    metadata,
                    selected_modules,
                    output_format=args.synth_format,
                    schematic=args.synth_schematic,
                    option_matrix=args.synth_option_matrix,
                )
                validate_synthesis_fixture(runner)
            elif phase == "formal":
                validate_formal(
                    runner, portable_tools=args.portable_tools
                )
            elif phase == "fpga":
                validate_fpga(
                    runner,
                    metadata,
                    selected_modules,
                    portable_tools=args.portable_tools,
                )
            elif phase == "clean":
                validate_clean(runner, metadata)

        if runner.failures:
            print("\nValidation failures:", file=sys.stderr)
            for failure in runner.failures:
                print(f"- {failure}", file=sys.stderr)
            return 1
        print("\nAll selected validation phases passed.", file=sys.stderr)
        return 0
    finally:
        if temporary_build and not args.keep_build and build_dir.exists():
            shutil.rmtree(build_dir)
        elif temporary_build and args.keep_build:
            print(f"Retained build directory: {build_dir}", file=sys.stderr)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as error:
        print(f"validation error: {error}", file=sys.stderr)
        raise SystemExit(2) from None
