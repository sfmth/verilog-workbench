#!/usr/bin/env python3
"""Verilog Work Bench command-line driver."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence, TextIO


VERSION = "0.1.0"
BUILD_MARKER = ".vwb-root"
BUILD_MARKER_SCHEMA = 1
CONFIG_FILE = ".vwb.json"
CONFIG_VERSION = 1
DEFAULT_MAX_ARRAY_WORDS = 32
HDL_SUFFIXES = {".v", ".sv"}
HEADER_SUFFIXES = {".vh", ".svh"}
TEST_KINDS = {"cocotb", "hdl"}
SAVED_WAVE_SCHEMA = 1
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


class Ansi:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"


class Colorizer:
    def __init__(self, mode: str = "auto"):
        self.mode = mode

    def enabled(self, stream: TextIO) -> bool:
        if self.mode == "never":
            return False
        if self.mode == "always":
            return True
        return (
            "NO_COLOR" not in os.environ
            and os.environ.get("TERM", "") != "dumb"
            and bool(getattr(stream, "isatty", lambda: False)())
        )

    def apply(self, value: object, *codes: str, stream: TextIO | None = None) -> str:
        output = stream or sys.stdout
        text = str(value)
        if not codes or not self.enabled(output):
            return text
        return "".join(codes) + text + Ansi.RESET


class DefaultsArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: object, **kwargs: object):
        kwargs.setdefault("formatter_class", argparse.ArgumentDefaultsHelpFormatter)
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def parse_known_args(
        self,
        args: Sequence[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> tuple[argparse.Namespace, list[str]]:
        arg_strings = list(sys.argv[1:] if args is None else args)
        explicit = set(getattr(namespace, "_explicit_options", ()))
        for action in self._actions:
            for option in action.option_strings:
                if any(
                    token == option
                    or (option.startswith("--") and token.startswith(option + "="))
                    or (
                        option.startswith("-")
                        and not option.startswith("--")
                        and len(option) == 2
                        and token.startswith(option)
                        and token != option
                    )
                    for token in arg_strings
                ):
                    explicit.add(action.dest)
                    break
        parsed, extras = super().parse_known_args(arg_strings, namespace)
        parsed._explicit_options = explicit
        return parsed, extras


@dataclass(frozen=True)
class ProjectSettings:
    root: Path
    src_dir: str
    test_dir: str
    build_dir: str
    config_path: Path | None = None


class VWBError(RuntimeError):
    """An expected user, project, or tool error."""


def unique_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def project_path(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def artifact_component(value: str) -> str:
    if SAFE_IDENTIFIER_RE.fullmatch(value):
        return value
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.lstrip("\\"))
    readable = readable.strip(".-_")[:48] or "item"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{readable}-{digest}"


def require_yosys_identifier(value: str) -> str:
    if not SAFE_IDENTIFIER_RE.fullmatch(value):
        raise VWBError(
            "Yosys top modules must use an unescaped Verilog identifier: " + value
        )
    return value


def find_project_config(start: Path) -> Path | None:
    directory = start.expanduser().resolve()
    if directory.is_file():
        directory = directory.parent
    for candidate_root in (directory, *directory.parents):
        candidate = candidate_root / CONFIG_FILE
        if candidate.is_file():
            return candidate
    return None


def load_project_config(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise VWBError(f"cannot read project configuration: {path}") from exc
    except json.JSONDecodeError as exc:
        raise VWBError(f"invalid JSON in project configuration {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise VWBError(f"project configuration must be a JSON object: {path}")
    expected = {"version", "src_dir", "test_dir", "build_dir"}
    unknown = sorted(set(raw) - expected)
    missing = sorted(expected - set(raw))
    if unknown:
        raise VWBError(
            f"unknown project configuration key(s) in {path}: {', '.join(unknown)}"
        )
    if missing:
        raise VWBError(
            f"missing project configuration key(s) in {path}: {', '.join(missing)}"
        )
    if raw["version"] != CONFIG_VERSION:
        raise VWBError(
            f"unsupported project configuration version in {path}: {raw['version']}"
        )
    result: dict[str, str] = {}
    for key in ("src_dir", "test_dir", "build_dir"):
        value = raw[key]
        if not isinstance(value, str) or not value.strip():
            raise VWBError(f"project configuration {key} must be a nonempty string")
        result[key] = value
    return result


def resolve_project_settings(
    args: argparse.Namespace, cwd: Path | None = None
) -> ProjectSettings:
    current = (cwd or Path.cwd()).expanduser().resolve()
    explicit_root = getattr(args, "root", None)
    if explicit_root:
        root = Path(explicit_root).expanduser().resolve()
        candidate = root / CONFIG_FILE
        config_path = candidate if candidate.is_file() else None
    else:
        config_path = find_project_config(current)
        root = (
            config_path.parent
            if config_path is not None
            else Path(__file__).resolve().parent
        )
    config = load_project_config(config_path) if config_path is not None else {}
    return ProjectSettings(
        root=root,
        src_dir=getattr(args, "src_dir", None) or config.get("src_dir", "src"),
        test_dir=getattr(args, "test_dir", None) or config.get("test_dir", "test"),
        build_dir=getattr(args, "build_dir", None) or config.get("build_dir", ".vwb"),
        config_path=config_path,
    )


def write_project_config(
    root: Path,
    src_dir: str,
    test_dir: str,
    build_dir: str,
    *,
    force: bool,
    dry_run: bool = False,
) -> Path:
    root = root.expanduser().resolve()
    config_path = root / CONFIG_FILE
    if config_path.exists() and not force:
        raise VWBError(
            f"project configuration already exists: {config_path}; use --force to replace it"
        )
    source = project_path(root, src_dir)
    tests = project_path(root, test_dir)
    build = project_path(root, build_dir)
    if (
        source == tests
        or source in tests.parents
        or tests in source.parents
    ):
        raise VWBError("source and test directories must not overlap")
    if build == root or build in root.parents:
        raise VWBError(f"unsafe build directory: {build}")
    if any(
        build == path or path in build.parents or build in path.parents
        for path in (source, tests)
    ):
        raise VWBError(
            f"build directory cannot overlap source or test files: {build}"
        )

    if dry_run:
        return config_path

    root.mkdir(parents=True, exist_ok=True)
    source.mkdir(parents=True, exist_ok=True)
    tests.mkdir(parents=True, exist_ok=True)
    package_marker = tests / "__init__.py"
    if not package_marker.exists():
        package_marker.write_text("", encoding="ascii")

    data = {
        "version": CONFIG_VERSION,
        "src_dir": src_dir,
        "test_dir": test_dir,
        "build_dir": build_dir,
    }
    temporary = config_path.with_name(f".{config_path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, config_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return config_path


def display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def strip_comments_and_strings(text: str) -> str:
    pattern = re.compile(
        r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"",
        flags=re.MULTILINE | re.DOTALL,
    )

    def blank(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group(0))

    return pattern.sub(blank, text)


MODULE_RE = re.compile(
    r"\bmodule\s+(?:automatic\s+)?(?P<name>\\\S+|[A-Za-z_][A-Za-z0-9_$]*)"
)
PACKAGE_RE = re.compile(
    r"\bpackage\s+(?:automatic\s+)?(?P<name>\\\S+|[A-Za-z_][A-Za-z0-9_$]*)"
)
INTERFACE_RE = re.compile(
    r"\binterface\s+(?:automatic\s+)?(?P<name>\\\S+|[A-Za-z_][A-Za-z0-9_$]*)"
)
PRIMITIVE_RE = re.compile(
    r"\bprimitive\s+(?P<name>\\\S+|[A-Za-z_][A-Za-z0-9_$]*)"
)
ENDMODULE_RE = re.compile(r"\bendmodule\b")
ENDINTERFACE_RE = re.compile(r"\bendinterface\b")
ENDPRIMITIVE_RE = re.compile(r"\bendprimitive\b")
TOKEN_RE = re.compile(r"\\\S+|[A-Za-z_][A-Za-z0-9_$]*|[#()\[\],;:]")
IDENTIFIER_RE = re.compile(r"^(?:\\\S+|[A-Za-z_][A-Za-z0-9_$]*)$")
DECLARATION_TOKEN_RE = re.compile(
    r"\\\S+|[A-Za-z_][A-Za-z0-9_$]*|[\[\](),;={}']"
)
DATA_TYPES = {
    "bit",
    "byte",
    "integer",
    "int",
    "logic",
    "longint",
    "reg",
    "shortint",
    "time",
    "tri",
    "tri0",
    "tri1",
    "triand",
    "trior",
    "trireg",
    "uwire",
    "wand",
    "wire",
    "wor",
}
DECLARATION_BOUNDARIES = {
    "input",
    "inout",
    "output",
    "parameter",
    "localparam",
    *DATA_TYPES,
}


@dataclass(frozen=True)
class RawModule:
    name: str
    path: Path
    body: str
    body_start: int
    end_position: int | None


@dataclass(frozen=True)
class ArrayDef:
    name: str
    ranges: tuple[str, ...]
    insertion_position: int = field(default=0, compare=False)
    procedural: bool = field(default=False, compare=False)
    unsupported_reason: str | None = field(default=None, compare=False)

    @property
    def dimensions(self) -> int:
        return len(self.ranges)


@dataclass(frozen=True)
class TypedefDef:
    name: str
    start: int
    end: int
    unpacked_ranges: tuple[str, ...]


@dataclass(frozen=True)
class ModuleDef:
    name: str
    path: Path
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class TestSpec:
    dut: str
    kind: str
    path: Path
    top: str | None = None

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.path.name}"


@dataclass(frozen=True)
class SavedWave:
    tag: str
    dut: str
    test_language: str
    test_path: str
    wave_format: str
    created_at: str
    directory: Path
    waveform: Path
    layout: Path | None = None

    def as_json(self, root: Path) -> dict[str, object]:
        return {
            "tag": self.tag,
            "dut": self.dut,
            "test_language": self.test_language,
            "test_path": self.test_path,
            "wave_format": self.wave_format,
            "created_at": self.created_at,
            "directory": display_path(self.directory, root),
            "waveform": self.waveform.name,
            "layout": self.layout.name if self.layout else None,
        }


def find_hdl_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        path.resolve()
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in HDL_SUFFIXES
    )


def extract_raw_declarations(
    path: Path,
    cleaned: str,
    declaration_re: re.Pattern[str],
    ending_re: re.Pattern[str],
) -> list[RawModule]:
    declarations: list[RawModule] = []
    position = 0
    while True:
        match = declaration_re.search(cleaned, position)
        if match is None:
            break
        end_match = ending_re.search(cleaned, match.end())
        body_end = end_match.start() if end_match else len(cleaned)
        declarations.append(
            RawModule(
                name=match.group("name"),
                path=path.resolve(),
                body=cleaned[match.end() : body_end],
                body_start=match.end(),
                end_position=end_match.start() if end_match else None,
            )
        )
        position = end_match.end() if end_match else len(cleaned)
    return declarations


def extract_raw_modules(path: Path) -> list[RawModule]:
    text = path.read_text(encoding="utf-8", errors="replace")
    cleaned = strip_comments_and_strings(text)
    return extract_raw_declarations(path, cleaned, MODULE_RE, ENDMODULE_RE)


def skip_balanced(tokens: Sequence[str], index: int, opening: str, closing: str) -> int:
    if index >= len(tokens) or tokens[index] != opening:
        return index
    depth = 0
    while index < len(tokens):
        if tokens[index] == opening:
            depth += 1
        elif tokens[index] == closing:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return index


def module_dependencies(raw: RawModule, known_modules: set[str]) -> tuple[str, ...]:
    tokens = TOKEN_RE.findall(raw.body)
    dependencies: set[str] = set()
    for index, token in enumerate(tokens):
        if token not in known_modules or token == raw.name:
            continue
        cursor = index + 1
        if cursor < len(tokens) and tokens[cursor] == "#":
            cursor += 1
            cursor = skip_balanced(tokens, cursor, "(", ")")
        if cursor >= len(tokens) or not IDENTIFIER_RE.match(tokens[cursor]):
            continue
        cursor += 1
        while cursor < len(tokens) and tokens[cursor] == "[":
            cursor = skip_balanced(tokens, cursor, "[", "]")
        if cursor < len(tokens) and tokens[cursor] == "(":
            dependencies.add(token)
    return tuple(sorted(dependencies))


def typedef_declarations(text: str) -> tuple[TypedefDef, ...]:
    matches = list(DECLARATION_TOKEN_RE.finditer(text))
    declarations: list[TypedefDef] = []
    index = 0
    while index < len(matches):
        if matches[index].group(0) != "typedef":
            index += 1
            continue
        cursor = index + 1
        nesting = 0
        last_identifier: str | None = None
        while cursor < len(matches):
            token = matches[cursor].group(0)
            if token in {"(", "[", "{"}:
                nesting += 1
            elif token in {")",
                "]",
                "}",
            }:
                nesting = max(0, nesting - 1)
            elif token == ";" and nesting == 0:
                break
            elif nesting == 0 and IDENTIFIER_RE.match(token):
                last_identifier = token
            cursor += 1
        if last_identifier:
            end = matches[cursor].end() if cursor < len(matches) else len(text)
            statement = text[matches[index].start() : end]
            name_match = re.search(
                re.escape(last_identifier)
                + r"(?P<ranges>(?:\s*\[[^\]]*\])*)\s*;$",
                statement,
            )
            ranges = (
                tuple(
                    item.strip()
                    for item in re.findall(
                        r"\[([^\]]*)\]", name_match.group("ranges")
                    )
                )
                if name_match
                else ()
            )
            declarations.append(
                TypedefDef(
                    name=last_identifier,
                    start=matches[index].start(),
                    end=end,
                    unpacked_ranges=ranges,
                )
            )
        index = cursor + 1
    return tuple(declarations)


def typedef_names(text: str) -> set[str]:
    return {declaration.name for declaration in typedef_declarations(text)}


def in_procedural_scope(text: str, position: int) -> bool:
    procedural_stack = [False]
    explicit_scopes: list[str] = []
    pending_procedural = False
    for match in DECLARATION_TOKEN_RE.finditer(text[:position]):
        token = match.group(0)
        if token in {"function", "task", "class"}:
            explicit_scopes.append(token)
        elif token in {"endfunction", "endtask", "endclass"}:
            if explicit_scopes:
                explicit_scopes.pop()
        elif token in {
            "always",
            "always_comb",
            "always_ff",
            "always_latch",
            "final",
            "initial",
        }:
            pending_procedural = True
        elif token == "begin":
            procedural_stack.append(
                procedural_stack[-1] or pending_procedural or bool(explicit_scopes)
            )
            pending_procedural = False
        elif token == "end" and len(procedural_stack) > 1:
            procedural_stack.pop()
        elif token == ";" and pending_procedural:
            pending_procedural = False
    return procedural_stack[-1] or bool(explicit_scopes) or pending_procedural


def aggregate_type_spans(text: str) -> tuple[tuple[int, int], ...]:
    matches = list(DECLARATION_TOKEN_RE.finditer(text))
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(matches):
        if matches[index].group(0) not in {"struct", "union"}:
            index += 1
            continue
        cursor = index + 1
        while cursor < len(matches) and matches[cursor].group(0) != "{":
            if matches[cursor].group(0) == ";":
                break
            cursor += 1
        if cursor >= len(matches) or matches[cursor].group(0) != "{":
            index += 1
            continue
        depth = 0
        start = matches[cursor].start()
        while cursor < len(matches):
            token = matches[cursor].group(0)
            if token == "{":
                depth += 1
            elif token == "}":
                depth -= 1
                if depth == 0:
                    spans.append((start, matches[cursor].end()))
                    break
            cursor += 1
        index = cursor + 1
    return tuple(spans)


def unpacked_arrays(
    raw: RawModule,
    additional_data_types: Iterable[str] = (),
) -> tuple[ArrayDef, ...]:
    token_matches = list(DECLARATION_TOKEN_RE.finditer(raw.body))
    tokens = [match.group(0) for match in token_matches]
    local_typedefs = typedef_declarations(raw.body)
    aggregate_spans = aggregate_type_spans(raw.body)
    typedef_ranges = {
        declaration.name: declaration.unpacked_ranges
        for declaration in local_typedefs
        if declaration.unpacked_ranges
    }
    data_types = (
        DATA_TYPES
        | {declaration.name for declaration in local_typedefs}
        | set(additional_data_types)
    )
    boundaries = DECLARATION_BOUNDARIES | data_types
    discovered: list[tuple[str, int, int, tuple[str, ...]]] = []
    seen: set[str] = set()
    index = 0

    while index < len(tokens):
        if tokens[index] not in data_types:
            index += 1
            continue

        type_name = tokens[index]
        inherited_ranges = typedef_ranges.get(type_name, ())
        cursor = index + 1
        while cursor < len(tokens) and tokens[cursor] in {"signed", "unsigned"}:
            cursor += 1
        while cursor < len(tokens) and tokens[cursor] == "[":
            cursor = skip_balanced(tokens, cursor, "[", "]")

        while cursor < len(tokens) and IDENTIFIER_RE.match(tokens[cursor]):
            name_token_index = cursor
            name = tokens[cursor]
            cursor += 1
            dimensions = 0
            while cursor < len(tokens) and tokens[cursor] == "[":
                dimensions += 1
                cursor = skip_balanced(tokens, cursor, "[", "]")
            name_position = token_matches[name_token_index].start()
            inside_typedef = any(
                declaration.start <= name_position < declaration.end
                for declaration in local_typedefs
            )
            if (dimensions or inherited_ranges) and name not in seen and not inside_typedef:
                discovered.append(
                    (name, dimensions, name_position, inherited_ranges)
                )
                seen.add(name)

            nesting = 0
            next_declarator = False
            while cursor < len(tokens):
                token = tokens[cursor]
                if token in {"(", "[", "{"}:
                    nesting += 1
                elif token in {")",
                    "]",
                    "}",
                }:
                    if nesting == 0:
                        break
                    nesting -= 1
                elif token == ";" and nesting == 0:
                    break
                elif token == "," and nesting == 0:
                    cursor += 1
                    if (
                        cursor < len(tokens)
                        and tokens[cursor] not in boundaries
                        and IDENTIFIER_RE.match(tokens[cursor])
                    ):
                        next_declarator = True
                    break
                cursor += 1
            if not next_declarator:
                break
        index += 1

    arrays: list[ArrayDef] = []
    for name, dimensions, approximate_position, inherited_ranges in discovered:
        boundary = "" if name.startswith("\\") else r"(?![A-Za-z0-9_$])"
        prefix = "" if name.startswith("\\") else r"(?<![A-Za-z0-9_$])"
        explicit_ranges = (
            rf"(?P<ranges>(?:\s*\[[^\]]*\]){{{dimensions}}})"
            if dimensions
            else r"(?P<ranges>)"
        )
        declaration = re.compile(
            prefix + re.escape(name) + boundary + explicit_ranges
        )
        match = declaration.search(raw.body, max(0, approximate_position - len(name) - 1))
        if match is None:
            continue
        ranges = tuple(
            item.strip()
            for item in re.findall(r"\[([^\]]*)\]", match.group("ranges"))
        ) + inherited_ranges
        header_end = raw.body.find(";")
        statement_end = raw.body.find(";", match.end())
        insertion_position = (
            header_end + 1
            if 0 <= match.start() < header_end
            else statement_end + 1
        )
        if insertion_position <= 0:
            continue
        arrays.append(
            ArrayDef(
                name=name,
                ranges=ranges,
                insertion_position=insertion_position,
                procedural=in_procedural_scope(raw.body, match.start()),
                unsupported_reason=(
                    "aggregate member"
                    if any(start <= match.start() < end for start, end in aggregate_spans)
                    else None
                ),
            )
        )
    return tuple(arrays)


def unit_dependencies(
    raw: RawModule,
    known_units: set[str],
    interface_names: set[str],
) -> tuple[str, ...]:
    dependencies = set(module_dependencies(raw, known_units))
    for name in interface_names:
        if name != raw.name and re.search(
            rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])",
            raw.body,
        ):
            dependencies.add(name)
    return tuple(sorted(dependencies))


def hdl_reference(name: str) -> str:
    return f"{name} " if name.startswith("\\") else name


def array_dump_instrumentation(
    raw: RawModule,
    array: ArrayDef,
    array_index: int,
    max_array_words: int,
) -> str:
    if array.unsupported_reason:
        raise VWBError(
            f"cannot statically dump {array.unsupported_reason} array "
            f"'{raw.name}.{array.name}'"
        )
    if array.procedural:
        raise VWBError(
            f"cannot statically dump procedural array '{raw.name}.{array.name}'"
        )
    array_name = hdl_reference(array.name)
    indices = [
        f"__vwb_array_{array_index}_index_{dimension}"
        for dimension in range(array.dimensions)
    ]
    bounds: list[tuple[str, str]] = []
    sizes: list[str] = []
    for declared_range in array.ranges:
        if ":" not in declared_range:
            raise VWBError(
                f"cannot dump non-static array '{raw.name}.{array.name}' "
                f"dimension [{declared_range}]"
            )
        left, right = (item.strip() for item in declared_range.split(":", 1))
        if not left or not right:
            raise VWBError(
                f"cannot dump non-static array '{raw.name}.{array.name}' "
                f"dimension [{declared_range}]"
            )
        lower = f"((({left}) < ({right})) ? ({left}) : ({right}))"
        upper = f"((({left}) > ({right})) ? ({left}) : ({right}))"
        bounds.append((lower, upper))
        sizes.append(f"(({upper}) - ({lower}) + 1)")

    count_name = f"__vwb_array_{array_index}_word_count"
    count_expression = "128'd1" + "".join(f" * ({size})" for size in sizes)
    lines = [
        "  // Generated by vwb.py to include unpacked array words.",
        f"  localparam [127:0] {count_name} = {count_expression};",
    ]
    indent = "  "
    if max_array_words > 0:
        lines.extend(
            [
                f"  if ({count_name} > 128'd{max_array_words}) begin : __vwb_array_{array_index}_too_large",
                "    initial $fatal(1, \"vwb.py array dump exceeds --max-array-words\");",
                f"  end else begin : __vwb_array_{array_index}_enabled",
            ]
        )
        indent = "    "
    for dimension, (index_name, (lower, upper)) in enumerate(
        zip(indices, bounds), start=1
    ):
        lines.extend(
            [
                f"{indent}for (genvar {index_name} = {lower};",
                f"{indent}  {index_name} <= {upper};",
                f"{indent}  {index_name} = {index_name} + 1",
                f"{indent}) begin : __vwb_dump_array_{array_index}_dimension_{dimension}",
            ]
        )
        indent += "  "
    selected_word = array_name + "".join(f"[{name}]" for name in indices)
    lines.append(f"{indent}initial $dumpvars(0, {selected_word});")
    for _ in indices:
        indent = indent[:-2]
        lines.append(f"{indent}end")
    if max_array_words > 0:
        lines.append("  end")
    return "\n" + "\n".join(lines) + "\n"


def instrument_source_arrays(path: Path, max_array_words: int) -> str | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    cleaned = strip_comments_and_strings(text)
    declarations = [
        *extract_raw_declarations(path, cleaned, MODULE_RE, ENDMODULE_RE),
        *extract_raw_declarations(path, cleaned, INTERFACE_RE, ENDINTERFACE_RE),
    ]
    known_types = typedef_names(cleaned)
    grouped: dict[int, list[str]] = {}
    for raw in declarations:
        for array_index, array in enumerate(unpacked_arrays(raw, known_types)):
            position = raw.body_start + array.insertion_position
            grouped.setdefault(position, []).append(
                array_dump_instrumentation(
                    raw, array, array_index, max_array_words
                )
            )
    insertions = [
        (position, "".join(contents)) for position, contents in grouped.items()
    ]
    if not insertions:
        return None
    for position, content in sorted(insertions, reverse=True):
        text = text[:position] + content + text[position:]
    return text


class SourceCatalog:
    def __init__(self, files: Sequence[Path]):
        self.files = unique_paths(files)
        raw_modules: list[RawModule] = []
        raw_interfaces: list[RawModule] = []
        raw_primitives: list[RawModule] = []
        files_with_modules: set[Path] = set()
        cleaned_sources: dict[Path, str] = {}
        for path in self.files:
            text = path.read_text(encoding="utf-8", errors="replace")
            cleaned = strip_comments_and_strings(text)
            cleaned_sources[path] = cleaned
            found = extract_raw_declarations(path, cleaned, MODULE_RE, ENDMODULE_RE)
            raw_modules.extend(found)
            raw_interfaces.extend(
                extract_raw_declarations(
                    path, cleaned, INTERFACE_RE, ENDINTERFACE_RE
                )
            )
            raw_primitives.extend(
                extract_raw_declarations(
                    path, cleaned, PRIMITIVE_RE, ENDPRIMITIVE_RE
                )
            )
            if found:
                files_with_modules.add(path.resolve())

        module_names = {module.name for module in raw_modules}
        interface_names = {interface.name for interface in raw_interfaces}
        known_units = module_names | interface_names | {
            primitive.name for primitive in raw_primitives
        }
        self.units: dict[str, list[ModuleDef]] = {}
        self.modules: dict[str, list[ModuleDef]] = {}
        self.interfaces: dict[str, list[Path]] = {}
        self.primitives: dict[str, list[Path]] = {}

        def add_unit(raw: RawModule) -> ModuleDef:
            definition = ModuleDef(
                name=raw.name,
                path=raw.path,
                dependencies=unit_dependencies(raw, known_units, interface_names),
            )
            self.units.setdefault(raw.name, []).append(definition)
            return definition

        for raw in raw_modules:
            self.modules.setdefault(raw.name, []).append(add_unit(raw))
        for raw in raw_interfaces:
            add_unit(raw)
            self.interfaces.setdefault(raw.name, []).append(raw.path)
        for raw in raw_primitives:
            add_unit(raw)
            self.primitives.setdefault(raw.name, []).append(raw.path)

        self.packages: dict[str, list[Path]] = {}
        for path, cleaned in cleaned_sources.items():
            for match in PACKAGE_RE.finditer(cleaned):
                self.packages.setdefault(match.group("name"), []).append(path)
        self.package_uses: dict[Path, tuple[str, ...]] = {}
        for path, cleaned in cleaned_sources.items():
            used = {
                name
                for name in self.packages
                if re.search(rf"(?<![A-Za-z0-9_$]){re.escape(name)}\s*::", cleaned)
            }
            self.package_uses[path] = tuple(sorted(used))
        self.files_without_modules = sorted(
            path for path in self.files if path.resolve() not in files_with_modules
        )

    def names(self) -> list[str]:
        return sorted(self.modules)

    def definition(self, name: str) -> ModuleDef:
        definitions = self.modules.get(name, [])
        if not definitions:
            choices = ", ".join(self.names()) or "none"
            raise VWBError(f"unknown module '{name}'; discovered modules: {choices}")
        if len(definitions) > 1:
            paths = ", ".join(str(item.path) for item in definitions)
            raise VWBError(f"module '{name}' is declared more than once: {paths}")
        return definitions[0]

    def closure(self, top: str) -> list[Path]:
        module_files: list[Path] = []
        seen_module_files: set[Path] = set()
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                return
            visiting.add(name)
            definitions = self.units.get(name, [])
            if not definitions:
                raise VWBError(f"unknown design unit '{name}'")
            if len(definitions) > 1:
                paths = ", ".join(str(item.path) for item in definitions)
                raise VWBError(f"design unit '{name}' is declared more than once: {paths}")
            definition = definitions[0]
            for dependency in definition.dependencies:
                visit(dependency)
            if definition.path not in seen_module_files:
                module_files.append(definition.path)
                seen_module_files.add(definition.path)
            visiting.remove(name)
            visited.add(name)

        visit(top)
        package_files: list[Path] = []
        visited_packages: set[str] = set()

        def visit_package(name: str) -> None:
            if name in visited_packages:
                return
            definitions = self.packages.get(name, [])
            if len(definitions) != 1:
                paths = ", ".join(str(path) for path in definitions) or "none"
                raise VWBError(
                    f"package '{name}' must have one declaration; found: {paths}"
                )
            visited_packages.add(name)
            path = definitions[0]
            for dependency in self.package_uses.get(path, ()):
                if dependency != name:
                    visit_package(dependency)
            package_files.append(path)

        for path in module_files:
            for package in self.package_uses.get(path, ()):
                visit_package(package)
        return unique_paths([*package_files, *module_files])

    def roots(self) -> list[str]:
        depended_on = {
            dependency
            for definitions in self.modules.values()
            for definition in definitions
            for dependency in definition.dependencies
        }
        return sorted(name for name in self.modules if name not in depended_on)


def is_cocotb_test(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return False

    cocotb_aliases = {"cocotb"}
    test_aliases: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "cocotb":
                    cocotb_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "cocotb":
            for alias in node.names:
                if alias.name == "test":
                    test_aliases.add(alias.asname or alias.name)

    def decorator_is_test(decorator: ast.expr) -> bool:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name):
            return target.id in test_aliases
        return (
            isinstance(target, ast.Attribute)
            and target.attr == "test"
            and isinstance(target.value, ast.Name)
            and target.value.id in cocotb_aliases
        )

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(decorator_is_test(item) for item in node.decorator_list):
                return True
    return False


def module_from_test_stem(stem: str, module_names: Sequence[str]) -> str | None:
    matches: list[tuple[int, int, str]] = []
    for name in module_names:
        exact = {f"test_{name}", f"tb_{name}", f"{name}_test", f"{name}_tb"}
        if stem in exact:
            matches.append((0, -len(name), name))
        elif stem.startswith(f"test_{name}_"):
            matches.append((1, -len(name), name))
    if not matches:
        return None
    matches.sort()
    best = matches[0]
    tied = [item for item in matches if item[:2] == best[:2]]
    return tied[0][2] if len(tied) == 1 else None


def infer_hdl_test_top(path: Path, dut: str) -> str | None:
    modules = extract_raw_modules(path)
    names = [module.name for module in modules]
    preferred = [f"test_{dut}", f"tb_{dut}", f"{dut}_test", f"{dut}_tb"]
    preferred_found = [name for name in preferred if name in names]
    if len(preferred_found) == 1:
        return preferred_found[0]
    if len(names) == 1:
        return names[0]
    if not names:
        return None

    known = set(names)
    dependencies = {
        module.name: set(module_dependencies(module, known)) for module in modules
    }
    depended_on = {item for values in dependencies.values() for item in values}
    roots = [name for name in names if name not in depended_on]
    return roots[0] if len(roots) == 1 else None


def python_module_import(
    path: Path, root: Path, test_dir: Path
) -> tuple[str, Path]:
    invalid_path = False
    import_roots = [root, test_dir.parent]
    try:
        path.resolve().relative_to(test_dir.resolve())
    except ValueError:
        pass
    else:
        import_roots.append(path.parent)
    for import_root in unique_paths(import_roots):
        try:
            relative = path.resolve().relative_to(import_root).with_suffix("")
        except ValueError:
            continue
        if all(part.isidentifier() for part in relative.parts):
            return ".".join(relative.parts), import_root
        invalid_path = True
    if invalid_path:
        raise VWBError(f"Cocotb test path is not importable as a Python module: {path}")
    raise VWBError(
        "Cocotb test must be inside the project root or configured test tree: "
        + str(path)
    )


class Workbench:
    def __init__(
        self,
        root: Path,
        src_dir: Path,
        test_dir: Path,
        build_dir: Path,
        verbose: bool = False,
        dry_run: bool = False,
        color: str = "auto",
    ):
        self.root = root.resolve()
        self.src_dir = src_dir.resolve()
        self.test_dir = test_dir.resolve()
        self.build_dir = build_dir.resolve()
        self.verbose = verbose
        self.dry_run = dry_run
        self.colors = Colorizer(color)

        if not self.src_dir.is_dir():
            raise VWBError(f"source directory does not exist: {self.src_dir}")
        if not self.test_dir.is_dir():
            raise VWBError(f"test directory does not exist: {self.test_dir}")
        if (
            self.src_dir == self.test_dir
            or self.src_dir in self.test_dir.parents
            or self.test_dir in self.src_dir.parents
        ):
            raise VWBError("source and test directories must not overlap")

        self.catalog = SourceCatalog(find_hdl_files(self.src_dir))
        self.test_hdl_files = find_hdl_files(self.test_dir)
        self.test_catalog = SourceCatalog(self.test_hdl_files)
        self.tests = self._discover_tests()
        self._cocotb_library: tuple[str, str] | None = None

    def style(
        self, value: object, *codes: str, stream: TextIO | None = None
    ) -> str:
        return self.colors.apply(value, *codes, stream=stream)

    def _discover_tests(self) -> list[TestSpec]:
        module_names = self.catalog.names()
        tests: list[TestSpec] = []
        for path in sorted(self.test_dir.rglob("*.py")):
            if not path.is_file() or not is_cocotb_test(path):
                continue
            dut = module_from_test_stem(path.stem, module_names)
            if dut:
                tests.append(TestSpec(dut=dut, kind="cocotb", path=path.resolve()))

        for path in self.test_hdl_files:
            dut = module_from_test_stem(path.stem, module_names)
            if dut:
                tests.append(
                    TestSpec(
                        dut=dut,
                        kind="hdl",
                        path=path,
                        top=infer_hdl_test_top(path, dut),
                    )
                )
        return sorted(tests, key=lambda item: (item.dut, item.kind, str(item.path)))

    def include_dirs(self, extra: Sequence[str] = ()) -> list[Path]:
        directories = {self.src_dir, self.test_dir}
        for path in self.src_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in HDL_SUFFIXES | HEADER_SUFFIXES:
                directories.add(path.parent.resolve())
        for path in self.test_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in HDL_SUFFIXES | HEADER_SUFFIXES:
                directories.add(path.parent.resolve())
        directories.update(project_path(self.root, item) for item in extra)
        return sorted(directories)

    def default_top(self) -> str:
        tested = {test.dut for test in self.tests}
        candidates = [name for name in self.catalog.roots() if name in tested]
        if len(candidates) == 1:
            return candidates[0]
        if len(tested) == 1:
            return next(iter(tested))
        choices = ", ".join(sorted(tested)) or ", ".join(self.catalog.names())
        raise VWBError(f"cannot choose a unique top module; specify one of: {choices}")

    def _validate_build_location(self) -> None:
        build = self.build_dir.resolve()
        if build == self.root or build in self.root.parents:
            raise VWBError(f"unsafe build directory: {build}")
        if any(
            build == path or path in build.parents or build in path.parents
            for path in (self.src_dir, self.test_dir)
        ):
            raise VWBError(
                f"build directory cannot overlap source or test files: {build}"
            )

    def _validate_build_marker(self, marker: Path, build: Path) -> None:
        if marker.is_symlink():
            raise VWBError(f"refusing symlinked build ownership marker: {marker}")
        try:
            marker_text = marker.read_text(encoding="utf-8")
        except OSError as exc:
            raise VWBError(f"cannot read build ownership marker: {marker}") from exc
        try:
            data = json.loads(marker_text)
        except json.JSONDecodeError:
            if f"project={self.root}\n" in marker_text:
                return
            raise VWBError(f"build directory belongs to another project: {build}")
        if not isinstance(data, dict) or data.get("schema") != BUILD_MARKER_SCHEMA:
            raise VWBError(f"invalid build ownership marker: {marker}")
        relative = data.get("project_relative")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise VWBError(f"invalid build ownership marker: {marker}")
        if (build / relative).resolve() != self.root:
            raise VWBError(f"build directory belongs to another project: {build}")

    def _write_build_marker(self, marker: Path, build: Path) -> None:
        data = {
            "schema": BUILD_MARKER_SCHEMA,
            "vwb_version": VERSION,
            "project_relative": os.path.relpath(self.root, build),
        }
        temporary = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(
                json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            os.replace(temporary, marker)
        finally:
            if temporary.exists() or temporary.is_symlink():
                temporary.unlink()

    def prepare_build_dir(self) -> None:
        self._validate_build_location()
        build = self.build_dir.resolve()
        marker = build / BUILD_MARKER
        if marker.is_file() or marker.is_symlink():
            self._validate_build_marker(marker, build)
        elif build.exists():
            try:
                next(build.iterdir())
            except StopIteration:
                pass
            else:
                raise VWBError(
                    f"build directory is not owned by vwb.py: {build}; "
                    "choose an empty directory"
                )
        if self.dry_run:
            return
        build.mkdir(parents=True, exist_ok=True)
        self._write_build_marker(marker, build)

    def require_tool(self, command: str) -> str:
        found = shutil.which(command)
        if found is None and self.dry_run:
            return command
        if found is None:
            raise VWBError(f"required command is not on PATH: {command}")
        return found

    def run(
        self,
        command: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        argv = [str(item) for item in command]
        if self.verbose or self.dry_run:
            location = f" (cwd={cwd})" if cwd else ""
            print(f"$ {shlex.join(argv)}{location}")
        if self.dry_run:
            return subprocess.CompletedProcess(argv, 0, "", "")
        try:
            return subprocess.run(
                argv,
                cwd=str(cwd) if cwd else None,
                env=env,
                text=True,
                capture_output=capture,
                check=False,
            )
        except OSError as exc:
            raise VWBError(f"could not execute {argv[0]}: {exc}") from exc

    def capture_tool(self, command: Sequence[str]) -> str:
        self.require_tool(command[0])
        result = self.run(command, capture=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise VWBError(f"command failed: {shlex.join(command)}: {detail}")
        return result.stdout.strip()

    def cocotb_library(self) -> tuple[str, str]:
        if self._cocotb_library is None:
            if self.dry_run:
                self._cocotb_library = (
                    "<cocotb-lib-dir>",
                    "<cocotb-vpi-library>",
                )
                return self._cocotb_library
            lib_dir = self.capture_tool(["cocotb-config", "--lib-dir"])
            lib_name = self.capture_tool(
                ["cocotb-config", "--lib-name", "vpi", "icarus"]
            )
            if not lib_dir or not lib_name:
                raise VWBError("cocotb-config returned an empty Icarus library setting")
            self._cocotb_library = (lib_dir, lib_name)
        return self._cocotb_library

    def specs_for(
        self,
        modules: Sequence[str],
        test_language: str,
        explicit_test: str | None,
        explicit_test_top: str | None,
    ) -> list[TestSpec]:
        kind = "hdl" if test_language == "verilog" else test_language
        selected_modules = list(modules)
        for module in selected_modules:
            self.catalog.definition(module)

        if explicit_test:
            if len(selected_modules) > 1:
                raise VWBError("--test can only be used with one module")
            path = project_path(self.root, explicit_test)
            if not path.is_file():
                raise VWBError(f"test file does not exist: {path}")
            suffix = path.suffix.lower()
            if suffix == ".py":
                detected_kind = "cocotb"
            elif suffix in HDL_SUFFIXES:
                detected_kind = "hdl"
            else:
                raise VWBError(f"unsupported test file type: {path.suffix}")
            if kind != "auto" and kind != detected_kind:
                raise VWBError(
                    f"--test-language {test_language} does not match test file {path}"
                )
            dut = selected_modules[0] if selected_modules else module_from_test_stem(
                path.stem, self.catalog.names()
            )
            if dut is None:
                raise VWBError("could not infer DUT from --test; also specify a module")
            self.catalog.definition(dut)
            if detected_kind == "cocotb" and not is_cocotb_test(path):
                raise VWBError(f"Python file has no @cocotb.test: {path}")
            if detected_kind == "cocotb" and explicit_test_top:
                raise VWBError("--test-top only applies to Verilog testbenches")
            top = explicit_test_top
            if detected_kind == "hdl" and top is None:
                top = infer_hdl_test_top(path, dut)
            return [TestSpec(dut=dut, kind=detected_kind, path=path, top=top)]

        allowed = TEST_KINDS if kind == "auto" else {kind}
        requested = set(selected_modules)
        specs = [
            spec
            for spec in self.tests
            if spec.kind in allowed and (not requested or spec.dut in requested)
        ]
        if explicit_test_top:
            if len(specs) != 1 or specs[0].kind != "hdl":
                raise VWBError("--test-top requires exactly one Verilog test")
            specs = [
                TestSpec(
                    dut=specs[0].dut,
                    kind="hdl",
                    path=specs[0].path,
                    top=explicit_test_top,
                )
            ]
        if requested:
            missing = sorted(requested - {spec.dut for spec in specs})
            if missing:
                raise VWBError(
                    f"no {test_language} test discovered for module(s): {', '.join(missing)}"
                )
        if not specs:
            raise VWBError("no runnable tests were discovered")
        return specs

    def _write_dump_module(self, path: Path, top: str, wave_path: Path) -> None:
        escaped_path = str(wave_path).replace("\\", "\\\\").replace('"', '\\"')
        path.write_text(
            "`timescale 1ns/1ps\n"
            "module vwb_dump;\n"
            "  initial begin\n"
            f'    $dumpfile("{escaped_path}");\n'
            f"    $dumpvars(0, {hdl_reference(top)});\n"
            "  end\n"
            "endmodule\n",
            encoding="ascii",
        )

    def _instrument_array_sources(
        self,
        sources: Sequence[Path],
        work_dir: Path,
        defines: Sequence[str],
        include_dirs: Sequence[Path],
        compile_args: Sequence[str],
        max_array_words: int,
    ) -> list[Path]:
        preprocessed = work_dir / "preprocessed.sv"
        preprocess_command: list[str | Path] = [
            "iverilog",
            "-E",
            "-g2012",
            "-o",
            preprocessed,
        ]
        for directory in include_dirs:
            preprocess_command.extend(["-I", directory])
        for define in defines:
            preprocess_command.append(f"-D{define}")
        preprocess_command.extend(compile_args)
        preprocess_command.extend(sources)
        if self.run(preprocess_command, cwd=self.root).returncode != 0:
            raise VWBError("Icarus preprocessing failed before waveform instrumentation")

        output = work_dir / "instrumented.sv"
        if self.dry_run:
            return [output]
        instrumented = instrument_source_arrays(preprocessed, max_array_words)
        if instrumented is None:
            return [preprocessed]
        output.write_text(instrumented, encoding="utf-8")
        return [output]

    def _compile_simulation(
        self,
        spec: TestSpec,
        work_dir: Path,
        *,
        waves: bool,
        wave_format: str,
        defines: Sequence[str],
        includes: Sequence[str],
        compile_args: Sequence[str],
        test_top: str | None,
        max_array_words: int,
    ) -> tuple[bool, Path | None, Path]:
        self.require_tool("iverilog")
        self.prepare_build_dir()
        if not self.dry_run:
            work_dir.mkdir(parents=True, exist_ok=True)
        simulation = work_dir / "sim.vvp"
        command_file = work_dir / "cmds.f"
        if not self.dry_run:
            command_file.write_text("+timescale+1ns/1ps\n", encoding="ascii")
        sources = self.catalog.closure(spec.dut)
        selected_top = spec.dut

        if spec.kind == "hdl":
            selected_top = test_top or spec.top or ""
            if not selected_top:
                raise VWBError(
                    f"cannot infer testbench top in {spec.path}; use --test-top"
                )
            sources.append(spec.path)
            test_definitions = self.test_catalog.modules.get(selected_top, [])
            if (
                len(test_definitions) == 1
                and test_definitions[0].path.resolve() == spec.path.resolve()
            ):
                sources.extend(self.test_catalog.closure(selected_top))

        sources = unique_paths(sources)
        command: list[str | Path] = [
            "iverilog",
            "-g2012",
            "-Wall",
            "-f",
            command_file,
            "-o",
            simulation,
        ]
        include_dirs = self.include_dirs(includes)
        for directory in include_dirs:
            command.extend(["-I", directory])
        for directory in sorted({path.parent for path in self.catalog.files}):
            command.extend(["-y", directory])
        command.extend(["-Y", ".v", "-Y", ".sv"])
        for define in defines:
            command.append(f"-D{define}")
        command.extend(compile_args)

        wave_path: Path | None = None
        if waves:
            if max_array_words < 0:
                raise VWBError("--max-array-words must be zero or greater")
            if max_array_words >= 1 << 128:
                raise VWBError("--max-array-words must fit in 128 bits")
            wave_path = work_dir / f"{artifact_component(spec.dut)}.{wave_format}"
            dump_module = work_dir / "vwb_dump.v"
            if not self.dry_run:
                self._write_dump_module(dump_module, selected_top, wave_path)
            sources = self._instrument_array_sources(
                sources,
                work_dir,
                defines,
                include_dirs,
                compile_args,
                max_array_words,
            )
            command.extend(["-s", "vwb_dump"])
            sources.append(dump_module)

        command.extend(["-s", selected_top])
        command.extend(sources)
        result = self.run(command, cwd=self.root)
        return result.returncode == 0, wave_path, simulation

    @staticmethod
    def _results_passed(path: Path) -> bool:
        if not path.is_file():
            return False
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            return False
        elements = list(root.iter())
        local_names = [element.tag.rsplit("}", 1)[-1] for element in elements]
        if "testcase" not in local_names:
            return False
        if "failure" in local_names or "error" in local_names:
            return False
        suites = [
            element
            for element in elements
            if element.tag.rsplit("}", 1)[-1] == "testsuite"
        ]
        for suite in suites:
            try:
                failures = int(suite.attrib.get("failures", "0"))
                errors = int(suite.attrib.get("errors", "0"))
            except ValueError:
                return False
            if failures > 0 or errors > 0:
                return False
        return True

    def _reset_sim_work_dir(self, work_dir: Path) -> None:
        if self.dry_run:
            return
        sim_path = self.build_dir / "sim"
        cursor = work_dir
        while cursor != sim_path:
            if cursor.is_symlink():
                raise VWBError(f"refusing symlinked simulation path: {cursor}")
            if self.build_dir not in cursor.parents:
                raise VWBError(f"unsafe simulation work directory: {work_dir}")
            cursor = cursor.parent
        if sim_path.is_symlink():
            raise VWBError(f"refusing symlinked simulation path: {sim_path}")
        if not work_dir.exists():
            return
        sim_root = sim_path.resolve()
        resolved = work_dir.resolve()
        if sim_root not in resolved.parents:
            raise VWBError(f"unsafe simulation work directory: {resolved}")
        for child in work_dir.iterdir():
            if child.is_file() and not child.is_symlink() and child.suffix == ".gtkw":
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()

    @property
    def saved_waves_dir(self) -> Path:
        return self.build_dir / "saved-waves"

    @staticmethod
    def _validate_wave_tag(tag: str) -> str:
        if not TAG_RE.fullmatch(tag):
            raise VWBError(
                "wave tag must start with a letter or digit and contain only "
                "letters, digits, '.', '_', or '-'"
            )
        return tag

    def _read_saved_wave(self, tag: str) -> SavedWave:
        tag = self._validate_wave_tag(tag)
        root = self.saved_waves_dir.resolve()
        raw_directory = self.saved_waves_dir / tag
        if self.saved_waves_dir.is_symlink():
            raise VWBError(f"refusing symlinked saved-wave directory: {self.saved_waves_dir}")
        if raw_directory.is_symlink():
            raise VWBError(f"refusing symlinked saved-wave tag: {tag}")
        directory = raw_directory.resolve()
        if directory.parent != root:
            raise VWBError(f"unsafe saved-wave path for tag: {tag}")
        metadata_path = directory / "metadata.json"
        if metadata_path.is_symlink() or not metadata_path.is_file():
            raise VWBError(f"saved waveform does not exist: {tag}")
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VWBError(f"invalid saved-wave metadata for tag {tag}") from exc
        if not isinstance(data, dict) or data.get("schema") != SAVED_WAVE_SCHEMA:
            raise VWBError(f"unsupported saved-wave metadata for tag {tag}")
        required = {
            "tag",
            "dut",
            "test_language",
            "test_path",
            "wave_format",
            "created_at",
            "waveform",
        }
        if not required.issubset(data) or data.get("tag") != tag:
            raise VWBError(f"incomplete saved-wave metadata for tag {tag}")
        scalar_keys = required - {"tag"}
        if any(not isinstance(data.get(key), str) for key in scalar_keys):
            raise VWBError(f"invalid saved-wave metadata values for tag {tag}")
        waveform_name = data["waveform"]
        if Path(waveform_name).name != waveform_name:
            raise VWBError(f"unsafe waveform filename in saved tag {tag}")
        waveform = directory / waveform_name
        if (
            waveform.is_symlink()
            or not waveform.is_file()
            or waveform.resolve().parent != directory
        ):
            raise VWBError(f"saved waveform file is missing for tag {tag}")
        layout: Path | None = None
        layout_name = data.get("layout")
        if layout_name is not None:
            if not isinstance(layout_name, str) or Path(layout_name).name != layout_name:
                raise VWBError(f"unsafe GTKWave layout filename in saved tag {tag}")
            candidate = directory / layout_name
            if candidate.is_symlink():
                raise VWBError(f"refusing symlinked GTKWave layout in saved tag {tag}")
            if candidate.is_file() and candidate.resolve().parent == directory:
                layout = candidate
        return SavedWave(
            tag=tag,
            dut=data["dut"],
            test_language=data["test_language"],
            test_path=data["test_path"],
            wave_format=data["wave_format"],
            created_at=data["created_at"],
            directory=directory,
            waveform=waveform,
            layout=layout,
        )

    def saved_waves(self) -> list[SavedWave]:
        if self.saved_waves_dir.is_symlink():
            raise VWBError(f"refusing symlinked saved-wave directory: {self.saved_waves_dir}")
        if not self.saved_waves_dir.is_dir():
            return []
        return [
            self._read_saved_wave(path.name)
            for path in sorted(self.saved_waves_dir.iterdir(), key=lambda item: item.name)
            if path.is_dir() and not path.name.startswith(".")
        ]

    def archive_wave(
        self,
        tag: str,
        spec: TestSpec,
        wave_path: Path,
        args: argparse.Namespace,
        layout_path: Path | None,
        *,
        replace: bool,
    ) -> SavedWave:
        tag = self._validate_wave_tag(tag)
        self.prepare_build_dir()
        if self.saved_waves_dir.is_symlink():
            raise VWBError(f"refusing symlinked saved-wave directory: {self.saved_waves_dir}")
        target = self.saved_waves_dir / tag
        if (target.exists() or target.is_symlink()) and not replace:
            raise VWBError(
                f"saved waveform tag already exists: {tag}; use --replace-tag"
            )
        wave_name = f"{artifact_component(spec.dut)}.{args.wave_format}"
        created_at = datetime.now(timezone.utc).isoformat()
        if self.dry_run:
            print(f"archive {wave_path} as {target}")
            return SavedWave(
                tag=tag,
                dut=spec.dut,
                test_language="verilog" if spec.kind == "hdl" else spec.kind,
                test_path=display_path(spec.path, self.root),
                wave_format=args.wave_format,
                created_at=created_at,
                directory=target,
                waveform=target / wave_name,
                layout=None,
            )
        if not wave_path.is_file():
            raise VWBError(f"cannot archive missing waveform: {wave_path}")
        self.saved_waves_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.saved_waves_dir / f".{tag}.{os.getpid()}.tmp"
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir()
        try:
            archived_wave = temporary / wave_name
            shutil.copy2(wave_path, archived_wave)
            archived_layout: str | None = None
            if layout_path is not None and layout_path.is_file():
                archived_layout = f"{artifact_component(spec.dut)}.gtkw"
                shutil.copy2(layout_path, temporary / archived_layout)
            metadata = {
                "schema": SAVED_WAVE_SCHEMA,
                "tag": tag,
                "dut": spec.dut,
                "test_language": "verilog" if spec.kind == "hdl" else spec.kind,
                "test_path": display_path(spec.path, self.root),
                "test_top": spec.top,
                "testcase": args.testcase,
                "seed": args.seed,
                "wave_format": args.wave_format,
                "waveform": wave_name,
                "layout": archived_layout,
                "created_at": created_at,
            }
            (temporary / "metadata.json").write_text(
                json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            backup = self.saved_waves_dir / f".{tag}.{os.getpid()}.backup"
            if backup.exists() or backup.is_symlink():
                if backup.is_dir() and not backup.is_symlink():
                    shutil.rmtree(backup)
                else:
                    backup.unlink()
            if target.exists() or target.is_symlink():
                os.replace(target, backup)
            try:
                os.replace(temporary, target)
            except OSError as exc:
                if backup.exists() or backup.is_symlink():
                    os.replace(backup, target)
                raise VWBError(f"could not replace saved waveform tag: {tag}") from exc
            if backup.exists() or backup.is_symlink():
                if backup.is_dir() and not backup.is_symlink():
                    shutil.rmtree(backup)
                else:
                    backup.unlink()
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return self._read_saved_wave(tag)

    def sync_saved_wave_layout(self, tag: str, layout_path: Path) -> None:
        if self.dry_run or not layout_path.is_file():
            return
        saved = self._read_saved_wave(tag)
        layout_name = f"{artifact_component(saved.dut)}.gtkw"
        archived_layout = saved.directory / layout_name
        if archived_layout.is_symlink():
            raise VWBError(f"refusing symlinked GTKWave layout in saved tag {tag}")
        if archived_layout.exists() and (
            not archived_layout.is_file()
            or archived_layout.resolve().parent != saved.directory
        ):
            raise VWBError(f"unsafe GTKWave layout in saved tag {tag}")
        if layout_path.resolve() != archived_layout.resolve():
            shutil.copy2(layout_path, archived_layout)
        metadata_path = saved.directory / "metadata.json"
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        data["layout"] = layout_name
        temporary = metadata_path.with_name(f".{metadata_path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(
                json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            os.replace(temporary, metadata_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def open_waveform(
        self,
        wave_path: Path,
        *,
        explicit_save: str | None = None,
        legacy_dut: str | None = None,
    ) -> tuple[int, Path]:
        self.require_tool("gtkwave")
        directory = wave_path.parent
        if not self.dry_run:
            directory.mkdir(parents=True, exist_ok=True)
        if explicit_save:
            layout = project_path(self.root, explicit_save)
            if not layout.is_file():
                raise VWBError(f"GTKWave save file does not exist: {layout}")
            command: list[str | Path] = [
                "gtkwave",
                f"--save={layout}",
                wave_path.name,
            ]
        else:
            layout = wave_path.with_suffix(".gtkw")
            legacy = (
                self.root / f"{artifact_component(legacy_dut)}.gtkw"
                if legacy_dut
                else None
            )
            if (
                not self.dry_run
                and not layout.exists()
                and legacy is not None
                and legacy.is_file()
            ):
                shutil.copy2(legacy, layout)
            command = ["gtkwave", "--autosavename", wave_path.name]
        result = self.run(command, cwd=directory)
        return result.returncode, layout

    def run_test_spec(self, spec: TestSpec, args: argparse.Namespace) -> tuple[bool, Path | None]:
        if spec.kind == "hdl" and (args.testcase or args.seed is not None):
            raise VWBError("--testcase and --seed only apply to Cocotb tests")
        work_dir = (
            self.build_dir
            / "sim"
            / artifact_component(spec.dut)
            / f"{spec.kind}-{artifact_component(spec.path.stem)}"
        )
        self.prepare_build_dir()
        self._reset_sim_work_dir(work_dir)
        compiled, wave_path, simulation = self._compile_simulation(
            spec,
            work_dir,
            waves=args.waves,
            wave_format=args.wave_format,
            defines=args.define,
            includes=args.include,
            compile_args=args.compile_arg,
            test_top=args.test_top,
            max_array_words=getattr(
                args, "max_array_words", DEFAULT_MAX_ARRAY_WORDS
            ),
        )
        if not compiled:
            return False, wave_path

        self.require_tool("vvp")
        vvp_args: list[str | Path] = ["vvp"]
        environment = os.environ.copy()
        if spec.kind == "cocotb":
            lib_dir, lib_name = self.cocotb_library()
            results_file = work_dir / "results.xml"
            if results_file.exists() and not self.dry_run:
                results_file.unlink()
            module_name, import_root = python_module_import(
                spec.path, self.root, self.test_dir
            )
            vvp_args.extend(["-M", lib_dir, "-m", lib_name])
            environment.update(
                {
                    "MODULE": module_name,
                    "COCOTB_TEST_MODULES": module_name,
                    "TOPLEVEL": spec.dut,
                    "COCOTB_TOPLEVEL": spec.dut,
                    "TOPLEVEL_LANG": "verilog",
                    "COCOTB_RESULTS_FILE": str(results_file),
                }
            )
            python_path = environment.get("PYTHONPATH", "")
            python_entries = [str(import_root)]
            if import_root != self.root:
                python_entries.append(str(self.root))
            if python_path:
                python_entries.append(python_path)
            environment["PYTHONPATH"] = os.pathsep.join(python_entries)
            if args.testcase:
                environment["TESTCASE"] = args.testcase
                environment["COCOTB_TESTCASE"] = args.testcase
            if args.seed is not None:
                environment["RANDOM_SEED"] = str(args.seed)
                environment["COCOTB_RANDOM_SEED"] = str(args.seed)

        vvp_args.extend(args.sim_arg)
        vvp_args.append(simulation)
        if args.waves and args.wave_format == "fst":
            vvp_args.append("-fst")
        for plusarg in args.plusarg:
            vvp_args.append(plusarg if plusarg.startswith("+") else f"+{plusarg}")
        result = self.run(vvp_args, cwd=work_dir, env=environment)
        if self.dry_run:
            return True, wave_path
        passed = result.returncode == 0
        if spec.kind == "cocotb":
            passed = passed and self._results_passed(work_dir / "results.xml")
        if args.waves and wave_path is not None and not wave_path.is_file():
            passed = False
            print(f"error: waveform was not produced: {wave_path}", file=sys.stderr)
        return passed, wave_path

    def run_tests(self, specs: Sequence[TestSpec], args: argparse.Namespace) -> tuple[bool, list[Path]]:
        if any(spec.kind == "hdl" for spec in specs) and (
            args.testcase or args.seed is not None
        ):
            raise VWBError(
                "--testcase and --seed require --test-language cocotb when "
                "Verilog tests are selected"
            )
        passed_count = 0
        wave_paths: list[Path] = []
        for index, spec in enumerate(specs, start=1):
            print(
                self.style("==>", Ansi.BOLD, Ansi.CYAN)
                + f" [{index}/{len(specs)}] "
                + self.style(spec.dut, Ansi.BOLD)
                + f" ({spec.label})"
            )
            try:
                passed, wave_path = self.run_test_spec(spec, args)
            except VWBError as exc:
                passed = False
                wave_path = None
                print(f"error: {exc}", file=sys.stderr)
            print(
                self.style(
                    "PASS" if passed else "FAIL",
                    Ansi.BOLD,
                    Ansi.GREEN if passed else Ansi.RED,
                )
            )
            if passed:
                passed_count += 1
            if wave_path is not None:
                wave_paths.append(wave_path)
            if not passed and not args.keep_going:
                break
        all_passed = passed_count == len(specs)
        print(
            self.style("==>", Ansi.BOLD, Ansi.CYAN)
            + " "
            + self.style(
                f"{passed_count}/{len(specs)} test runs passed",
                Ansi.GREEN if all_passed else Ansi.RED,
            )
        )
        return all_passed, wave_paths

    def lint_module(
        self,
        module: str,
        defines: Sequence[str],
        includes: Sequence[str],
        extra_args: Sequence[str],
    ) -> bool:
        self.require_tool("verilator")
        command: list[str | Path] = [
            "verilator",
            "--lint-only",
            "--top-module",
            module,
            "-Wall",
            "-Wno-COMBDLY",
            "-Wno-INCABSPATH",
        ]
        for directory in self.include_dirs(includes):
            command.append(f"-I{directory}")
        for define in defines:
            command.append(f"-D{define}")
        command.extend(extra_args)
        command.extend(self.catalog.closure(module))
        return self.run(command, cwd=self.root).returncode == 0

    @staticmethod
    def _yosys_quote(value: str | Path) -> str:
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _yosys_read_command(
        self,
        module: str,
        defines: Sequence[str],
        includes: Sequence[str],
    ) -> str:
        arguments = ["read_verilog", "-sv"]
        for define in defines:
            arguments.extend(["-D", self._yosys_quote(define)])
        for path in self.include_dirs(includes):
            arguments.extend(["-I", self._yosys_quote(path)])
        arguments.extend(self._yosys_quote(path) for path in self.catalog.closure(module))
        return " ".join(arguments)

    def synthesize(self, module: str, args: argparse.Namespace) -> Path:
        self.catalog.definition(module)
        require_yosys_identifier(module)
        self.require_tool("yosys")
        self.prepare_build_dir()
        output_name = artifact_component(module)
        output_dir = self.build_dir / "synth" / output_name
        if not self.dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / f"{output_name}.json"
        script_path = output_dir / "synth.ys"
        commands = [
            self._yosys_read_command(module, args.define, args.include),
            f"hierarchy -check -top {module}",
        ]
        if args.schematic and args.full:
            commands.append("prep -flatten")
        elif args.schematic:
            commands.append("prep")
        elif args.full:
            commands.append(f"synth -top {module}")
        else:
            commands.extend(["proc", "opt -full"])
        if args.flatten and not (args.schematic and args.full):
            commands.extend(["flatten", "opt_clean"])
        commands.append(f"write_json {self._yosys_quote(json_path)}")

        artifact = json_path
        prefix = output_dir / output_name
        render_commands: list[str] = []
        needs_yosys_show = args.format != "json" and (
            not args.schematic or args.format == "dot"
        )
        if needs_yosys_show:
            render_commands.append(f"read_json {self._yosys_quote(json_path.name)}")
            show_options = [
                "show",
                f"-format {args.format}",
                "-viewer none",
                f"-prefix {prefix.name}",
                "-colors 2",
                "-width",
                "-signed",
            ]
            if args.format == "dot":
                show_options.append("-long")
            show_options.append(module)
            render_commands.append(" ".join(show_options))
            artifact = prefix.with_suffix(f".{args.format}")
            if args.format in {"svg", "png"}:
                self.require_tool("dot")
        if not self.dry_run:
            script_path.write_text(";\n".join(commands) + ";\n", encoding="utf-8")
        result = self.run(["yosys", "-s", script_path], cwd=self.root)
        if result.returncode != 0:
            raise VWBError(f"Yosys synthesis failed for {module}")

        if render_commands:
            render_script = output_dir / "render.ys"
            if not self.dry_run:
                render_script.write_text(
                    ";\n".join(render_commands) + ";\n", encoding="utf-8"
                )
            result = self.run(["yosys", "-s", render_script], cwd=output_dir)
            if result.returncode != 0:
                raise VWBError(f"Yosys rendering failed for {module}")

        if args.schematic:
            self.require_tool("netlistsvg")
            svg_path = output_dir / f"{output_name}.svg"
            result = self.run(["netlistsvg", json_path, "-o", svg_path], cwd=self.root)
            if result.returncode != 0:
                raise VWBError(f"netlistsvg failed for {module}")
            if args.format == "svg":
                artifact = svg_path
            if args.format == "png":
                self.require_tool("rsvg-convert")
                png_path = output_dir / f"{output_name}.png"
                result = self.run(
                    ["rsvg-convert", "-o", png_path, svg_path], cwd=self.root
                )
                if result.returncode != 0:
                    raise VWBError(f"SVG rasterization failed for {module}")
                artifact = png_path

        if not self.dry_run and not artifact.is_file():
            raise VWBError(f"synthesis artifact was not produced: {artifact}")
        viewer = args.view.strip() if args.view else "none"
        if viewer.lower() not in {"none", "off", "false", "0"}:
            self.require_tool(viewer)
            if self.run([viewer, artifact], cwd=self.root).returncode != 0:
                raise VWBError(f"viewer failed for {artifact}")
        return artifact

    def run_formal(self, config_value: str | None, view: bool) -> Path:
        self.require_tool("sby")
        if config_value:
            config = project_path(self.root, config_value)
        else:
            configs = sorted(
                path.resolve()
                for path in self.root.rglob("*.sby")
                if self.build_dir not in path.parents and ".git" not in path.parts
            )
            if len(configs) != 1:
                choices = ", ".join(display_path(path, self.root) for path in configs)
                raise VWBError(
                    "cannot choose a unique .sby file"
                    + (f"; found: {choices}" if choices else "; none found")
                )
            config = configs[0]
        if not config.is_file():
            raise VWBError(f"formal configuration does not exist: {config}")
        output = self.build_dir / "formal" / artifact_component(config.stem)
        self.prepare_build_dir()
        if not self.dry_run:
            output.parent.mkdir(parents=True, exist_ok=True)
        result = self.run(
            ["sby", "-f", "-d", output, config],
            cwd=config.parent,
        )
        if result.returncode != 0:
            raise VWBError(f"formal verification failed: {config}")
        if view:
            if self.dry_run:
                self.require_tool("gtkwave")
                self.run(["gtkwave", output / "**" / "*.vcd"], cwd=self.root)
                return output
            traces = sorted(output.rglob("*.vcd"))
            if not traces:
                raise VWBError(f"no VCD trace found under {output}")
            self.require_tool("gtkwave")
            if self.run(["gtkwave", traces[0]], cwd=self.root).returncode != 0:
                raise VWBError(f"GTKWave failed for {traces[0]}")
        return output

    def run_fpga(self, module: str, args: argparse.Namespace) -> Path:
        self.catalog.definition(module)
        require_yosys_identifier(module)
        board = {"tangnano9k": "gowin", "icebreaker": "ice40"}.get(
            args.board, args.board
        )
        output_name = artifact_component(module)
        output = self.build_dir / "fpga" / board / output_name
        self.prepare_build_dir()
        if not self.dry_run:
            output.mkdir(parents=True, exist_ok=True)
        constraints = (
            project_path(self.root, args.constraints)
            if args.constraints
            else self.src_dir / ("io.cst" if board == "gowin" else "io.pcf")
        )
        if not constraints.is_file():
            raise VWBError(f"constraint file does not exist: {constraints}")

        stages = ["synth", "pnr", "pack", "flash"]
        requested_index = stages.index(args.stage)
        defines = ["LEDS_NR=6", *args.define]
        self.require_tool("yosys")
        design_json = output / f"{output_name}.json"
        script = output / "fpga.ys"
        synth_command = (
            f"synth_gowin -top {module} -json {self._yosys_quote(design_json)}"
            if board == "gowin"
            else f"synth_ice40 -top {module} -json {self._yosys_quote(design_json)}"
        )
        if not self.dry_run:
            script.write_text(
                ";\n".join(
                    [
                        self._yosys_read_command(module, defines, args.include),
                        f"hierarchy -check -top {module}",
                        synth_command,
                    ]
                )
                + ";\n",
                encoding="utf-8",
            )
        if self.run(["yosys", "-s", script], cwd=self.root).returncode != 0:
            raise VWBError(f"FPGA synthesis failed for {module}")
        artifact: Path = design_json
        if requested_index == 0:
            return artifact

        if board == "gowin":
            self.require_tool("nextpnr-gowin")
            pnr_json = output / f"{output_name}-pnr.json"
            command: list[str | Path] = [
                "nextpnr-gowin",
                "--json",
                design_json,
                "--write",
                pnr_json,
                "--device",
                "GW1NR-LV9QN88PC6/I5",
                "--family",
                "GW1N-9C",
                "--cst",
                constraints,
            ]
            if self.run(command, cwd=self.root).returncode != 0:
                raise VWBError(f"Gowin place and route failed for {module}")
            artifact = pnr_json
            if requested_index == 1:
                return artifact
            self.require_tool("gowin_pack")
            bitstream = output / f"{output_name}.fs"
            if self.run(
                ["gowin_pack", "-d", "GW1N-9C", "-o", bitstream, pnr_json],
                cwd=self.root,
            ).returncode != 0:
                raise VWBError(f"Gowin packing failed for {module}")
            artifact = bitstream
            if requested_index == 2:
                return artifact
            self.require_tool("openFPGALoader")
            if self.run(
                ["openFPGALoader", "-b", "tangnano9k", bitstream], cwd=self.root
            ).returncode != 0:
                raise VWBError(f"Gowin flashing failed for {module}")
            return artifact

        self.require_tool("nextpnr-ice40")
        asc = output / f"{output_name}.asc"
        command = [
            "nextpnr-ice40",
            "--up5k",
            "--package",
            "sg48",
            "--json",
            design_json,
            "--pcf",
            constraints,
            "--asc",
            asc,
        ]
        if self.run(command, cwd=self.root).returncode != 0:
            raise VWBError(f"iCE40 place and route failed for {module}")
        artifact = asc
        if requested_index == 1:
            return artifact
        self.require_tool("icepack")
        bitstream = output / f"{output_name}.bin"
        if self.run(["icepack", asc, bitstream], cwd=self.root).returncode != 0:
            raise VWBError(f"iCE40 packing failed for {module}")
        artifact = bitstream
        if requested_index == 2:
            return artifact
        self.require_tool("openFPGALoader")
        if self.run(
            ["openFPGALoader", "-b", "ice40_generic", bitstream], cwd=self.root
        ).returncode != 0:
            raise VWBError(f"iCE40 flashing failed for {module}")
        return artifact

    def clean(self, scope: str) -> None:
        targets = {
            "sim": self.build_dir / "sim",
            "waves": self.saved_waves_dir,
            "synth": self.build_dir / "synth",
            "fpga": self.build_dir / "fpga",
            "formal": self.build_dir / "formal",
            "all": self.build_dir,
        }
        target_path = targets[scope]
        build = self.build_dir.resolve()
        self._validate_build_location()
        marker = build / BUILD_MARKER
        if not build.exists():
            return
        if not marker.is_file() or marker.is_symlink():
            raise VWBError(f"refusing to clean a directory not owned by vwb.py: {build}")
        self._validate_build_marker(marker, build)
        if target_path.is_symlink():
            if self.verbose or self.dry_run:
                print(
                    self.style("unlink", Ansi.BOLD, Ansi.YELLOW),
                    self.style(target_path, Ansi.DIM),
                )
            if not self.dry_run:
                target_path.unlink()
            return
        target = target_path.resolve()
        if target != build and build not in target.parents:
            raise VWBError(f"refusing to remove unsafe build path: {target}")
        if self.verbose or self.dry_run:
            print(
                self.style("remove", Ansi.BOLD, Ansi.YELLOW),
                self.style(target, Ansi.DIM),
            )
        if target.exists() and not target.is_dir():
            raise VWBError(f"refusing to clean non-directory build target: {target}")
        if target.exists() and not self.dry_run:
            shutil.rmtree(target)


def add_simulation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("modules", nargs="*", metavar="MODULE")
    parser.add_argument("--test", help="explicit Cocotb or Verilog test file")
    parser.add_argument(
        "--test-language",
        choices=["auto", "cocotb", "verilog"],
        default="auto",
        help="select Cocotb and/or Verilog testbenches",
    )
    parser.add_argument("--test-top", help="top module declared by a Verilog testbench")
    parser.add_argument("--testcase", help="run one Cocotb testcase")
    parser.add_argument("--seed", type=int, help="Cocotb/Python random seed")
    parser.add_argument("--waves", action="store_true", help="generate a waveform")
    parser.add_argument(
        "--wave-format",
        choices=["fst", "vcd"],
        default="fst",
        help="waveform format when generation is enabled",
    )
    parser.add_argument(
        "--max-array-words",
        type=int,
        default=DEFAULT_MAX_ARRAY_WORDS,
        metavar="COUNT",
        help="maximum words dumped per static array; 0 disables the limit",
    )
    parser.add_argument("-D", "--define", action="append", default=[], metavar="NAME[=VALUE]")
    parser.add_argument("-I", "--include", action="append", default=[], metavar="DIR")
    parser.add_argument("--compile-arg", action="append", default=[], metavar="ARG")
    parser.add_argument("--sim-arg", action="append", default=[], metavar="ARG")
    parser.add_argument("--plusarg", action="append", default=[], metavar="ARG")
    parser.add_argument("--keep-going", action="store_true")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vwb.py",
        description="Discover, test, lint, synthesize, and build Verilog projects.",
        epilog=(
            "examples:\n"
            "  ./vwb.py list\n"
            "  ./vwb.py test\n"
            "  ./vwb.py test my_module --waves\n"
            "  ./vwb.py lint my_module\n"
            "  ./vwb.py synth my_module --format svg\n"
            "  ./vwb.py fpga my_module --board ice40 --stage pack\n"
            "  ./vwb.py --src-dir examples/src --test-dir examples/test test"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--root")
    parser.add_argument("--src-dir")
    parser.add_argument("--test-dir")
    parser.add_argument("--build-dir")
    parser.add_argument(
        "--color", choices=["auto", "always", "never"], default="auto"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=DefaultsArgumentParser
    )

    init_parser = subparsers.add_parser(
        "init", help="create and save the project directory configuration"
    )
    init_parser.add_argument("--root", dest="init_root")
    init_parser.add_argument("--src-dir", dest="init_src_dir")
    init_parser.add_argument("--test-dir", dest="init_test_dir")
    init_parser.add_argument("--build-dir", dest="init_build_dir")
    init_parser.add_argument("--force", action="store_true")

    list_parser = subparsers.add_parser(
        "list", help="list discovered packages, modules, dependencies, and tests"
    )
    list_parser.add_argument("--json", action="store_true", dest="as_json")

    test_parser = subparsers.add_parser(
        "test", aliases=["sim"], help="compile and run discovered tests"
    )
    add_simulation_options(test_parser)

    wave_parser = subparsers.add_parser(
        "wave", aliases=["gtkwave"], help="run, open, and manage waveforms"
    )
    add_simulation_options(wave_parser)
    wave_parser.set_defaults(waves=True)
    wave_parser.add_argument("--save", help="explicit GTKWave save file")
    wave_parser.add_argument("--tag", help="archive a passing waveform with this tag")
    wave_parser.add_argument("--replace-tag", action="store_true")
    wave_parser.add_argument("--load", metavar="TAG", help="open an archived waveform")
    wave_parser.add_argument(
        "--list-saved", action="store_true", help="list archived waveforms"
    )
    wave_parser.add_argument("--json", action="store_true", dest="as_json")

    lint_parser = subparsers.add_parser(
        "lint", help="lint selected module hierarchies"
    )
    lint_parser.add_argument("modules", nargs="*", metavar="MODULE")
    lint_parser.add_argument("--all", action="store_true", dest="all_modules")
    lint_parser.add_argument("--keep-going", action="store_true")
    lint_parser.add_argument("-D", "--define", action="append", default=[])
    lint_parser.add_argument("-I", "--include", action="append", default=[])
    lint_parser.add_argument("--lint-arg", action="append", default=[])

    synth_parser = subparsers.add_parser(
        "synth", help="synthesize and render a module"
    )
    synth_parser.add_argument("module", nargs="?")
    synth_parser.add_argument(
        "--format",
        choices=["json", "svg", "png", "dot"],
        default="png",
        help="final synthesis artifact format",
    )
    synth_parser.add_argument(
        "--full", action="store_true", help="use the Makefile's full preparation flow"
    )
    synth_parser.add_argument("--flatten", action="store_true", help="flatten hierarchy")
    synth_parser.add_argument(
        "--schematic",
        "--schemetic",
        dest="schematic",
        action="store_true",
        default=True,
        help="render through netlistsvg",
    )
    synth_parser.add_argument(
        "--no-schematic",
        "--no-schemetic",
        dest="schematic",
        action="store_false",
        default=argparse.SUPPRESS,
        help="render images through Yosys show instead of netlistsvg",
    )
    synth_parser.add_argument(
        "--view", default="geeqie", metavar="VIEWER", help="artifact viewer or 'none'"
    )
    synth_parser.add_argument(
        "--no-view",
        dest="view",
        action="store_const",
        const="none",
        default=argparse.SUPPRESS,
        help="do not open the generated artifact",
    )
    synth_parser.add_argument("-D", "--define", action="append", default=[])
    synth_parser.add_argument("-I", "--include", action="append", default=[])

    formal_parser = subparsers.add_parser("formal", help="run a SymbiYosys configuration")
    formal_parser.add_argument("config", nargs="?")
    formal_parser.add_argument("--view", action="store_true")

    fpga_parser = subparsers.add_parser("fpga", help="build or flash an FPGA bitstream")
    fpga_parser.add_argument("module", nargs="?")
    fpga_parser.add_argument(
        "--board",
        required=True,
        choices=["gowin", "tangnano9k", "ice40", "icebreaker"],
    )
    fpga_parser.add_argument(
        "--stage", choices=["synth", "pnr", "pack", "flash"], default="pack"
    )
    fpga_parser.add_argument("--constraints")
    fpga_parser.add_argument("-D", "--define", action="append", default=[])
    fpga_parser.add_argument("-I", "--include", action="append", default=[])

    clean_parser = subparsers.add_parser("clean", help="remove generated files")
    clean_parser.add_argument(
        "scope",
        nargs="?",
        choices=["sim", "waves", "synth", "fpga", "formal", "all"],
        default="all",
    )

    doctor_parser = subparsers.add_parser("doctor", help="check project tools")
    doctor_parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def build_workbench(args: argparse.Namespace) -> Workbench:
    settings = resolve_project_settings(args)
    return Workbench(
        root=settings.root,
        src_dir=project_path(settings.root, settings.src_dir),
        test_dir=project_path(settings.root, settings.test_dir),
        build_dir=project_path(settings.root, settings.build_dir),
        verbose=args.verbose,
        dry_run=args.dry_run,
        color=args.color,
    )


def command_init(args: argparse.Namespace) -> int:
    root_value = args.init_root or args.root
    root = (
        Path(root_value).expanduser().resolve()
        if root_value
        else Path.cwd().resolve()
    )
    config = write_project_config(
        root,
        args.init_src_dir or args.src_dir or "src",
        args.init_test_dir or args.test_dir or "test",
        args.init_build_dir or args.build_dir or ".vwb",
        force=args.force,
        dry_run=args.dry_run,
    )
    action = "would initialize" if args.dry_run else "initialized"
    print(f"{action} {config}")
    return 0


def validate_simulation_args(args: argparse.Namespace) -> None:
    if not args.waves:
        return
    if args.max_array_words < 0:
        raise VWBError("--max-array-words must be zero or greater")
    if args.max_array_words >= 1 << 128:
        raise VWBError("--max-array-words must fit in 128 bits")


def wave_management_overrides(args: argparse.Namespace) -> list[str]:
    explicit = set(getattr(args, "_explicit_options", ()))
    options = [
        ("test", "--test"),
        ("test_language", "--test-language"),
        ("test_top", "--test-top"),
        ("testcase", "--testcase"),
        ("seed", "--seed"),
        ("waves", "--waves"),
        ("wave_format", "--wave-format"),
        ("max_array_words", "--max-array-words"),
        ("define", "--define"),
        ("include", "--include"),
        ("compile_arg", "--compile-arg"),
        ("sim_arg", "--sim-arg"),
        ("plusarg", "--plusarg"),
        ("keep_going", "--keep-going"),
    ]
    return [name for dest, name in options if dest in explicit]


def command_list(workbench: Workbench, args: argparse.Namespace) -> int:
    tests_by_dut: dict[str, list[TestSpec]] = {}
    for spec in workbench.tests:
        tests_by_dut.setdefault(spec.dut, []).append(spec)
    data = {
        "packages": [
            {
                "name": name,
                "files": [
                    display_path(path, workbench.root)
                    for path in workbench.catalog.packages[name]
                ],
            }
            for name in sorted(workbench.catalog.packages)
        ],
        "modules": [
            {
                "name": name,
                "files": [
                    display_path(item.path, workbench.root)
                    for item in workbench.catalog.modules[name]
                ],
                "dependencies": sorted(
                    {
                        dependency
                        for item in workbench.catalog.modules[name]
                        for dependency in item.dependencies
                    }
                ),
                "tests": [
                    {
                        "kind": spec.kind,
                        "path": display_path(spec.path, workbench.root),
                        "top": spec.top,
                    }
                    for spec in tests_by_dut.get(name, [])
                ],
            }
            for name in workbench.catalog.names()
        ],
        "source_files_without_modules": [
            display_path(path, workbench.root)
            for path in workbench.catalog.files_without_modules
        ],
    }
    if args.as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    if data["packages"]:
        print(workbench.style("Packages:", Ansi.BOLD, Ansi.CYAN))
        for package in data["packages"]:
            name = workbench.style(f"{package['name']:<20}", Ansi.BOLD, Ansi.GREEN)
            files = workbench.style(
                ", ".join(package["files"]), Ansi.DIM
            )
            print(f"  {name} {files}")
    print(workbench.style("Modules:", Ansi.BOLD, Ansi.CYAN))
    for module in data["modules"]:
        dependencies = ", ".join(module["dependencies"]) or "-"
        tests = ", ".join(
            f"{item['kind']}:{item['path']}"
            + (f" (top={item['top']})" if item["top"] else "")
            for item in module["tests"]
        ) or "-"
        files = ", ".join(module["files"])
        name = workbench.style(f"{module['name']:<20}", Ansi.BOLD, Ansi.GREEN)
        print(f"  {name} {workbench.style(files, Ansi.DIM)}")
        print(
            f"    {workbench.style('dependencies:', Ansi.CYAN)} "
            f"{workbench.style(dependencies, Ansi.YELLOW)}"
        )
        print(
            f"    {workbench.style('tests:       ', Ansi.CYAN)} "
            f"{workbench.style(tests, Ansi.MAGENTA)}"
        )
    if data["source_files_without_modules"]:
        print(
            workbench.style(
                "Source files without module declarations:", Ansi.BOLD, Ansi.YELLOW
            )
        )
        for path in data["source_files_without_modules"]:
            print(f"  {path}")
    return 0


def command_test(workbench: Workbench, args: argparse.Namespace) -> int:
    validate_simulation_args(args)
    specs = workbench.specs_for(
        args.modules, args.test_language, args.test, args.test_top
    )
    passed, _ = workbench.run_tests(specs, args)
    return 0 if passed else 1


def command_wave(workbench: Workbench, args: argparse.Namespace) -> int:
    if args.list_saved:
        if args.load or args.tag or args.replace_tag or args.save:
            raise VWBError(
                "--list-saved cannot be combined with --load, --tag, "
                "--replace-tag, or --save"
            )
        ignored = wave_management_overrides(args)
        if ignored:
            raise VWBError(
                "--list-saved cannot be combined with: " + ", ".join(ignored)
            )
        requested = set(args.modules)
        saved = [
            item
            for item in workbench.saved_waves()
            if not requested or item.dut in requested
        ]
        if args.as_json:
            print(
                json.dumps(
                    [item.as_json(workbench.root) for item in saved],
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        print(workbench.style("Saved waveforms:", Ansi.BOLD, Ansi.CYAN))
        if not saved:
            print("  none")
            return 0
        print(f"  {'TAG':<20} {'DUT':<20} {'LANGUAGE':<10} {'FORMAT':<7} CREATED")
        for item in saved:
            tag = workbench.style(f"{item.tag:<20}", Ansi.BOLD, Ansi.GREEN)
            dut = workbench.style(f"{item.dut:<20}", Ansi.BOLD)
            print(
                f"  {tag} {dut} {item.test_language:<10} "
                f"{item.wave_format:<7} {item.created_at}"
            )
        return 0

    if args.as_json:
        raise VWBError("--json requires --list-saved")
    if args.replace_tag and not args.tag:
        raise VWBError("--replace-tag requires --tag")
    if args.load:
        if args.tag or args.replace_tag:
            raise VWBError("--load cannot be combined with --tag or --replace-tag")
        if len(args.modules) > 1:
            raise VWBError("--load accepts at most one module filter")
        ignored = wave_management_overrides(args)
        if ignored:
            raise VWBError("--load cannot be combined with: " + ", ".join(ignored))
        saved = workbench._read_saved_wave(args.load)
        if args.modules and args.modules[0] != saved.dut:
            raise VWBError(
                f"saved waveform {args.load} belongs to {saved.dut}, "
                f"not {args.modules[0]}"
            )
        returncode, active_layout = workbench.open_waveform(
            saved.waveform,
            explicit_save=args.save,
        )
        workbench.sync_saved_wave_layout(args.load, active_layout)
        return 0 if returncode == 0 else 1

    if args.tag:
        workbench._validate_wave_tag(args.tag)
        tag_path = workbench.saved_waves_dir / args.tag
        if tag_path.exists() and not args.replace_tag:
            raise VWBError(
                f"saved waveform tag already exists: {args.tag}; use --replace-tag"
            )
    if args.save:
        explicit_layout = project_path(workbench.root, args.save)
        if not explicit_layout.is_file():
            raise VWBError(f"GTKWave save file does not exist: {explicit_layout}")

    validate_simulation_args(args)
    if not args.modules and not args.test:
        args.modules = [workbench.default_top()]
    specs = workbench.specs_for(
        args.modules, args.test_language, args.test, args.test_top
    )
    if len(specs) != 1:
        choices = ", ".join(f"{item.dut}:{item.kind}" for item in specs)
        raise VWBError(
            "wave requires one test; narrow --test-language or --test: " + choices
        )
    args.keep_going = False
    passed, wave_paths = workbench.run_tests(specs, args)
    if not passed or len(wave_paths) != 1:
        return 1
    wave_path = wave_paths[0]
    layout_path = (
        project_path(workbench.root, args.save)
        if args.save
        else wave_path.with_suffix(".gtkw")
    )
    if args.tag:
        archived = workbench.archive_wave(
            args.tag,
            specs[0],
            wave_path,
            args,
            layout_path,
            replace=args.replace_tag,
        )
        print(
            workbench.style("saved", Ansi.GREEN, Ansi.BOLD),
            archived.tag,
            display_path(archived.directory, workbench.root),
        )
    returncode, active_layout = workbench.open_waveform(
        wave_path,
        explicit_save=args.save,
        legacy_dut=specs[0].dut,
    )
    if args.tag:
        workbench.sync_saved_wave_layout(args.tag, active_layout)
    return 0 if returncode == 0 else 1


def command_lint(workbench: Workbench, args: argparse.Namespace) -> int:
    if args.modules and args.all_modules:
        raise VWBError("specify modules or --all, not both")
    modules = list(args.modules)
    if args.all_modules:
        modules = workbench.catalog.names()
    elif not modules:
        modules = sorted({spec.dut for spec in workbench.tests})
    if not modules:
        raise VWBError("no modules selected for lint")
    passed = 0
    for index, module in enumerate(modules, start=1):
        print(
            workbench.style("==>", Ansi.BOLD, Ansi.CYAN)
            + f" [{index}/{len(modules)}] lint "
            + workbench.style(module, Ansi.BOLD)
        )
        ok = workbench.lint_module(module, args.define, args.include, args.lint_arg)
        print(
            workbench.style(
                "PASS" if ok else "FAIL",
                Ansi.BOLD,
                Ansi.GREEN if ok else Ansi.RED,
            )
        )
        passed += int(ok)
        if not ok and not args.keep_going:
            break
    print(
        workbench.style("==>", Ansi.BOLD, Ansi.CYAN)
        + " "
        + workbench.style(
            f"{passed}/{len(modules)} lint runs passed",
            Ansi.GREEN if passed == len(modules) else Ansi.RED,
        )
    )
    return 0 if passed == len(modules) else 1


def command_synth(workbench: Workbench, args: argparse.Namespace) -> int:
    module = args.module or workbench.default_top()
    artifact = workbench.synthesize(module, args)
    print(display_path(artifact, workbench.root))
    return 0


def command_formal(workbench: Workbench, args: argparse.Namespace) -> int:
    output = workbench.run_formal(args.config, args.view)
    print(display_path(output, workbench.root))
    return 0


def command_fpga(workbench: Workbench, args: argparse.Namespace) -> int:
    module = args.module or workbench.default_top()
    artifact = workbench.run_fpga(module, args)
    print(display_path(artifact, workbench.root))
    return 0


def command_doctor(workbench: Workbench, args: argparse.Namespace) -> int:
    groups = {
        "simulation": ["iverilog", "vvp", "cocotb-config"],
        "waveform": ["gtkwave"],
        "lint": ["verilator"],
        "synthesis": ["yosys", "dot", "netlistsvg", "rsvg-convert", "geeqie"],
        "formal": ["sby"],
        "gowin": ["nextpnr-gowin", "gowin_pack", "openFPGALoader"],
        "ice40": ["nextpnr-ice40", "icepack", "openFPGALoader"],
    }
    data = {
        group: {command: shutil.which(command) for command in commands}
        for group, commands in groups.items()
    }
    if args.as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        for group, commands in data.items():
            print(workbench.style(f"{group}:", Ansi.BOLD, Ansi.CYAN))
            for command, path in commands.items():
                name = workbench.style(f"{command:<18}", Ansi.BOLD)
                status = workbench.style(
                    path or "missing",
                    Ansi.GREEN if path else Ansi.RED,
                )
                print(f"  {name} {status}")
        print(
            workbench.style("project:", Ansi.BOLD, Ansi.CYAN)
            + f" {len(workbench.catalog.names())} modules, "
            + f"{len(workbench.tests)} runnable tests"
        )
    required = [data["simulation"]["iverilog"], data["simulation"]["vvp"]]
    if any(spec.kind == "cocotb" for spec in workbench.tests):
        required.append(data["simulation"]["cocotb-config"])
    return 0 if all(required) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return command_init(args)
        workbench = build_workbench(args)
        handlers = {
            "list": command_list,
            "test": command_test,
            "sim": command_test,
            "wave": command_wave,
            "gtkwave": command_wave,
            "lint": command_lint,
            "synth": command_synth,
            "formal": command_formal,
            "fpga": command_fpga,
            "doctor": command_doctor,
        }
        if args.command == "clean":
            workbench.clean(args.scope)
            return 0
        return handlers[args.command](workbench, args)
    except VWBError as exc:
        colors = Colorizer(getattr(args, "color", "auto"))
        label = colors.apply("error:", Ansi.BOLD, Ansi.RED, stream=sys.stderr)
        print(f"{label} {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        colors = Colorizer(getattr(args, "color", "auto"))
        print(colors.apply("interrupted", Ansi.YELLOW, stream=sys.stderr), file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
