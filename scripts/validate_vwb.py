#!/usr/bin/env python3
"""Dynamic integration harness for the Verilog Workbench CLI.

The harness treats ``vwb.py list --json`` as the source of truth.  It never
contains a DUT or testbench name, so adding a discovered design or test grows
the validation matrix without requiring this file to change.
"""

from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
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


class HarnessError(RuntimeError):
    """Raised when the harness cannot construct a meaningful validation run."""


@dataclass(frozen=True)
class TestCase:
    module: str
    kind: str
    language: str
    path: str
    top: str | None
    dependency_count: int

    def as_matrix_entry(self, index: int) -> dict[str, Any]:
        return {
            "index": index,
            "module": self.module,
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
    wave_formats: tuple[str, ...]
    default_wave_format: str
    default_max_array_words: int
    synth_formats: tuple[str, ...]
    default_synth_format: str
    fpga_boards: tuple[str, ...]
    fpga_stages: tuple[str, ...]
    color_modes: tuple[str, ...]
    clean_scopes: tuple[str, ...]
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
        self._display(command)
        result = subprocess.run(
            command,
            cwd=self.root,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            check=False,
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


def load_cli_metadata(vwb_path: Path) -> CliMetadata:
    spec = importlib.util.spec_from_file_location("_vwb_validation_target", vwb_path)
    if spec is None or spec.loader is None:
        raise HarnessError(f"cannot import {vwb_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)

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
    synth_parser = subparsers.choices["synth"]
    fpga_parser = subparsers.choices["fpga"]
    clean_parser = subparsers.choices["clean"]
    wave_format = parser_action(test_parser, "--wave-format")
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
    canonical_parsers: dict[str, argparse.ArgumentParser] = {}
    seen_parsers: set[int] = set()
    for name, command_parser in subparsers.choices.items():
        if id(command_parser) in seen_parsers:
            continue
        seen_parsers.add(id(command_parser))
        canonical_parsers[name] = command_parser
    return CliMetadata(
        commands=tuple(sorted(subparsers.choices)),
        wave_formats=tuple(wave_format.choices or ()),
        default_wave_format=str(wave_format.default),
        default_max_array_words=int(max_array_words.default),
        synth_formats=tuple(synth_format.choices or ()),
        default_synth_format=str(synth_format.default),
        fpga_boards=tuple(fpga_board.choices or ()),
        fpga_stages=tuple(fpga_stage.choices or ()),
        color_modes=tuple(color_mode.choices or ()),
        clean_scopes=tuple(clean_scope.choices or ()),
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
        "keep_going",
        "save",
        "tag",
        "replace_tag",
        "load",
        "list_saved",
        "as_json",
    },
    "lint": {"all_modules", "keep_going", "define", "include", "lint_arg"},
    "synth": {"format", "full", "flatten", "schematic", "view", "define", "include"},
    "formal": {"view"},
    "fpga": {"board", "stage", "constraints", "define", "include"},
    "doctor": {"as_json"},
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
    for module in modules:
        name = module.get("name")
        if not isinstance(name, str) or not name:
            raise HarnessError("inventory contains a module without a name")
        if name in names:
            raise HarnessError(f"inventory contains duplicate module {name!r}")
        names.add(name)
        for source in module.get("files", []):
            if not project_path(runner.root, source).is_file():
                raise HarnessError(f"module {name!r} references missing source {source}")
    return inventory


def flatten_tests(inventory: dict[str, Any], root: Path) -> list[TestCase]:
    language_for_kind = {"cocotb": "cocotb", "hdl": "verilog"}
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
    for path in sorted(test_dir.rglob("test_*.py")):
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
                "files": inventory_by_name[name].get("files", []),
                "dependencies": inventory_by_name[name].get("dependencies", []),
                "has_tests": bool(inventory_by_name[name].get("tests")),
                "runner": [
                    "--module",
                    name,
                    "--phase",
                    "dry-run",
                    "--phase",
                    "lint",
                    "--phase",
                    "synth",
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
def fake_executable(runner: Runner, name: str):
    with tempfile.TemporaryDirectory(prefix=f"vwb-fake-{name}-") as directory:
        executable = Path(directory) / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        executable.chmod(0o755)
        previous = runner.environment.get("PATH", "")
        runner.environment["PATH"] = os.pathsep.join((directory, previous))
        try:
            yield Path(directory)
        finally:
            runner.environment["PATH"] = previous


def validate_help(runner: Runner, metadata: CliMetadata) -> None:
    runner.run(
        [sys.executable, str(runner.vwb), "--help"],
        label="top-level help",
    )
    runner.run(
        [sys.executable, str(runner.vwb), "--version"],
        label="version option",
    )
    for command in metadata.commands:
        runner.run(
            [sys.executable, str(runner.vwb), command, "--help"],
            label=f"help for {command}",
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


def validate_contracts(
    runner: Runner,
    metadata: CliMetadata,
    tests: Sequence[TestCase],
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
    compiled_tests: dict[Path, list[str]],
    seed: int,
) -> None:
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
        runner.run_vwb(
            arguments,
            label=f"dry-run test {test.module}:{test.kind}",
            dry_run=True,
            capture=True,
        )

    if tests:
        runner.run_vwb(
            ["test", "--test-language", "auto", "--keep-going"],
            label="dry-run automatic test discovery",
            dry_run=True,
            capture=True,
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

    for module in modules:
        runner.run_vwb(
            [
                "lint",
                module,
                "--define",
                "VWB_VALIDATION=1",
                "--include",
                ".",
                "--lint-arg=-Wall",
            ],
            label=f"dry-run lint options for {module}",
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
        for output_format in metadata.synth_formats:
            for schematic in (True, False):
                for full in (False, True):
                    for flatten in (False, True):
                        schematic_option = (
                            "--schemetic"
                            if schematic and output_format == metadata.synth_formats[0]
                            and not full and not flatten
                            else "--no-schemetic"
                            if not schematic
                            and output_format == metadata.synth_formats[0]
                            and not full
                            and not flatten
                            else "--schematic"
                            if schematic
                            else "--no-schematic"
                        )
                        arguments = [
                            "synth",
                            module,
                            "--format",
                            output_format,
                            schematic_option,
                            "-D",
                            "VWB_VALIDATION=1",
                            "-I",
                            runner.src_dir,
                        ]
                        if full:
                            arguments.append("--full")
                        if flatten:
                            arguments.append("--flatten")
                        arguments.extend(
                            ["--no-view"] if flatten else ["--view", "none"]
                        )
                        runner.run_vwb(
                            arguments,
                            label=(
                                f"dry-run synthesis {module}/{output_format}/"
                                f"schematic={schematic}/full={full}/flatten={flatten}"
                            ),
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
        for module_index, module in enumerate(modules):
            for board_index, board in enumerate(metadata.fpga_boards):
                for stage_index, stage in enumerate(metadata.fpga_stages):
                    constraint = constraints[
                        (module_index + board_index + stage_index) % len(constraints)
                    ]
                    preferred_suffix = (
                        ".cst"
                        if "gowin" in board or "tang" in board
                        else ".pcf" if "ice" in board else None
                    )
                    preferred = [
                        path for path in constraints if path.suffix.lower() == preferred_suffix
                    ]
                    if preferred:
                        constraint = preferred[
                            (module_index + stage_index) % len(preferred)
                        ]
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
        for index, module in enumerate(modules):
            config = config_dir / f"module-{index}.sby"
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


def validate_doctor(runner: Runner) -> None:
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
        runner.failures.append(
            "doctor reports tools missing from the exhaustive CI image: "
            + ", ".join(sorted(missing))
        )


def validate_tests(runner: Runner, tests: Sequence[TestCase], seed: int) -> None:
    for test in tests:
        runner.run_vwb(
            explicit_test_arguments(test, seed=seed),
            label=f"simulation {test.module}:{test.kind}",
        )


def waveform_path(runner: Runner, test: TestCase, wave_format: str) -> Path:
    return (
        runner.build_dir
        / "sim"
        / test.module
        / f"{test.kind}-{Path(test.path).stem}"
        / f"{test.module}.{wave_format}"
    )


def validate_waves(
    runner: Runner,
    metadata: CliMetadata,
    tests: Sequence[TestCase],
    seed: int,
    all_formats: bool,
) -> None:
    formats = (
        metadata.wave_formats if all_formats else (metadata.default_wave_format,)
    )
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


def validate_lint(runner: Runner, modules: Sequence[str]) -> None:
    for module in modules:
        runner.run_vwb(
            [
                "lint",
                module,
                "--define",
                "VWB_VALIDATION=1",
                "--include",
                runner.src_dir,
                "--lint-arg=--timing",
                "--lint-arg=-Wno-fatal",
            ],
            label=f"lint {module}",
        )


def synthesis_artifact(runner: Runner, module: str, output_format: str) -> Path:
    return runner.build_dir / "synth" / module / f"{module}.{output_format}"


def validate_synthesis(
    runner: Runner,
    metadata: CliMetadata,
    modules: Sequence[str],
    output_format: str | None,
    schematic: bool,
    option_matrix: bool,
) -> None:
    selected_format = output_format or metadata.synth_formats[0]
    combinations = (
        [
            (candidate_format, candidate_schematic, full, flatten)
            for candidate_format in metadata.synth_formats
            for candidate_schematic in (True, False)
            for full in (False, True)
            for flatten in (False, True)
        ]
        if option_matrix
        else [
            (selected_format, schematic, full, False)
            for full in (False, True)
        ]
    )
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
            )
            if result.returncode != 0:
                continue
            artifact = synthesis_artifact(runner, module, candidate_format)
            if not artifact.is_file() or artifact.stat().st_size == 0:
                runner.failures.append(
                    f"missing or empty synthesis artifact: {artifact}"
                )


def validate_synthesis_fixture(runner: Runner, metadata: CliMetadata) -> None:
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
        for output_format in metadata.synth_formats:
            for schematic in (True, False):
                for full in (False, True):
                    for flatten in (False, True):
                        arguments = [
                            "synth",
                            "validation_design",
                            "--format",
                            output_format,
                            "--schematic" if schematic else "--no-schematic",
                            "--no-view",
                            "-D",
                            'VWB_VALIDATION="docker smoke"',
                            "-I",
                            "include files",
                        ]
                        if full:
                            arguments.append("--full")
                        if flatten:
                            arguments.append("--flatten")
                        result = fixture.run_vwb(
                            arguments,
                            label=(
                                "fixture synthesis "
                                f"{output_format}/schematic={schematic}/"
                                f"full={full}/flatten={flatten}"
                            ),
                        )
                        if result.returncode:
                            continue
                        artifact = synthesis_artifact(
                            fixture, "validation_design", output_format
                        )
                        if not artifact.is_file() or artifact.stat().st_size == 0:
                            fixture.failures.append(
                                f"missing fixture synthesis artifact: {artifact}"
                            )
        runner.failures.extend(fixture.failures)


def validate_formal(runner: Runner) -> None:
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
        output = runner.build_dir / "formal" / config.stem
        if result.returncode == 0 and not output.is_dir():
            runner.failures.append(f"formal output directory is missing: {output}")


def validate_fpga(
    runner: Runner, metadata: CliMetadata, modules: Sequence[str]
) -> None:
    if not modules:
        runner.failures.append("FPGA validation has no discovered module")
        return
    module = modules[0]
    source_dir = project_path(runner.root, runner.src_dir)
    for board in metadata.fpga_boards:
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
            label=f"actual FPGA synthesis for {board}",
        )
        artifact = runner.build_dir / "fpga" / family / module / f"{module}.json"
        if result.returncode == 0 and (
            not artifact.is_file() or artifact.stat().st_size == 0
        ):
            runner.failures.append(f"missing FPGA synthesis artifact: {artifact}")

    validate_fpga_pack_fixture(runner, metadata)


def validate_fpga_pack_fixture(runner: Runner, metadata: CliMetadata) -> None:
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
            artifact = (
                fixture.build_dir
                / "fpga"
                / family
                / "validation_fpga"
                / f"validation_fpga{suffix}"
            )
            if result.returncode == 0 and (
                not artifact.is_file() or artifact.stat().st_size == 0
            ):
                fixture.failures.append(
                    f"missing FPGA pack fixture artifact for {board}: {artifact}"
                )
        runner.failures.extend(fixture.failures)


def validate_clean(runner: Runner, metadata: CliMetadata) -> None:
    scopes = [scope for scope in metadata.clean_scopes if scope != "all"]
    for scope in scopes:
        runner.run_vwb(["clean", scope], label=f"clean scope {scope}")
    runner.run_vwb(["clean", "all"], label="clean scope all")
    if runner.build_dir.exists():
        runner.failures.append(
            f"clean all left the build directory behind: {runner.build_dir}"
        )
    runner.run_vwb(["clean"], label="default clean scope")


def select_modules(all_modules: Sequence[str], requested: Sequence[str]) -> list[str]:
    if not requested:
        return list(all_modules)
    available = set(all_modules)
    unknown = sorted(set(requested) - available)
    if unknown:
        raise HarnessError("unknown --module selection: " + ", ".join(unknown))
    requested_set = set(requested)
    return [module for module in all_modules if module in requested_set]


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
            "Discover the vwb.py inventory and run a name-free integration matrix."
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
    parser.add_argument(
        "--module",
        action="append",
        default=[],
        help="limit module phases to one discovered module; repeat as needed",
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
        help="generate every CLI-supported waveform format for selected tests",
    )
    parser.add_argument(
        "--synth-format",
        help="actual synthesis format (default: first format reported by vwb.py)",
    )
    parser.add_argument(
        "--synth-schematic",
        action="store_true",
        help="use netlistsvg for the actual synthesis phase",
    )
    parser.add_argument(
        "--synth-option-matrix",
        action="store_true",
        help="actually run every synthesis format and renderer combination",
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
        if not modules:
            raise HarnessError("inventory has no module matrix")
        coverage = option_coverage(metadata)
        compiled_tests = compile_test_sources(runner, tests)
        if args.emit_matrix:
            document = matrix_document(inventory, tests, modules, coverage)
            document["integrity_errors"] = list(runner.failures)
            print(json.dumps(document, indent=2))
            return 1 if runner.failures else 0

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
        for phase in phases:
            print(f"\n== {phase} ==", file=sys.stderr, flush=True)
            if phase == "help":
                validate_help(runner, metadata)
            elif phase == "regressions":
                validate_regressions(runner)
            elif phase == "contracts":
                validate_contracts(runner, metadata, selected_tests)
            elif phase == "dry-run":
                validate_dry_runs(
                    runner,
                    metadata,
                    selected_tests,
                    selected_modules,
                    compiled_tests,
                    seed=args.seed,
                )
            elif phase == "doctor":
                validate_doctor(runner)
            elif phase == "tests":
                if not selected_tests:
                    runner.failures.append("inventory contains no runnable tests")
                else:
                    validate_tests(runner, selected_tests, seed=args.seed)
            elif phase == "waves":
                if not selected_tests:
                    runner.failures.append("inventory contains no waveform candidates")
                else:
                    validate_waves(
                        runner,
                        metadata,
                        selected_tests,
                        seed=args.seed,
                        all_formats=args.all_wave_formats,
                    )
            elif phase == "lint":
                validate_lint(runner, selected_modules)
            elif phase == "synth":
                validate_synthesis(
                    runner,
                    metadata,
                    selected_modules,
                    output_format=args.synth_format,
                    schematic=args.synth_schematic,
                    option_matrix=args.synth_option_matrix,
                )
                if not args.module and not args.test_index:
                    validate_synthesis_fixture(runner, metadata)
            elif phase == "formal":
                validate_formal(runner)
            elif phase == "fpga":
                validate_fpga(runner, metadata, selected_modules)
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
