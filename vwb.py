#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
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
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence, TextIO


VERSION = "0.2.0"
BUILD_MARKER = ".vwb-root"
BUILD_MARKER_SCHEMA = 1
CONFIG_FILE = ".vwb.json"
CONFIG_VERSION = 1
DEFAULT_MAX_ARRAY_WORDS = 32
FULL_GATE_NETLIST_LIMIT_BYTES = 1024 * 1024
SCALABLE_LAYOUT_JSON_LIMIT_BYTES = 256 * 1024
MAX_PNG_PIXELS = 16_000_000
VERILOG_SUFFIXES = {".v", ".sv"}
VHDL_SUFFIXES = {".vhd", ".vhdl"}
HDL_SUFFIXES = VERILOG_SUFFIXES | VHDL_SUFFIXES
HEADER_SUFFIXES = {".vh", ".svh"}
TEST_KINDS = {"cocotb", "verilog", "vhdl"}
SAVED_WAVE_SCHEMA = 1
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
TCL_BARE_WORD_RE = re.compile(r"^[A-Za-z0-9_./:@%+=,-]+$")
YOSYS_BARE_WORD_RE = re.compile(r"^[A-Za-z0-9_./:@%+=,$-]+$")
YOSYS_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.$-]*$")
TOOL_ALTERNATIVES = {
    "nextpnr-gowin": (
        "nextpnr-himbaechel-gowin",
        "nextpnr-himbaechel",
        "nextpnr-gowin",
    ),
}


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


def find_tool_choice(command: str) -> tuple[str, str] | None:
    for candidate in TOOL_ALTERNATIVES.get(command, (command,)):
        found = shutil.which(candidate)
        if found is not None:
            return candidate, found
    return None


def find_tool(command: str) -> str | None:
    choice = find_tool_choice(command)
    return choice[1] if choice is not None else None


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


def python_identifier_component(value: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_]", "_", value.lstrip("\\"))
    if not candidate or not re.match(r"[A-Za-z_]", candidate):
        candidate = "dut_" + candidate
    if candidate == value:
        return candidate
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{candidate}_{digest}"


def tool_identifier(value: str) -> str:
    """Return the spelling simulators and synthesis tools use for an HDL name."""
    return value[1:] if value.startswith("\\") else value


def require_yosys_identifier(value: str) -> str:
    if SAFE_IDENTIFIER_RE.fullmatch(value):
        return value
    normalized = tool_identifier(value)
    if value.startswith("\\") and YOSYS_IDENTIFIER_RE.fullmatch(normalized):
        return normalized
    raise VWBError(f"Yosys cannot select the top module safely: {value}")


def cocotb_toplevel_names(value: str) -> tuple[str, str]:
    normalized = tool_identifier(value)
    legacy = f"work.{normalized}" if value.startswith("\\") else normalized
    return legacy, normalized


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


def verilog_source_needs_preprocessing(text: str, defines: Sequence[str]) -> bool:
    """Return whether Verible needs an Icarus-preprocessed copy of this source."""
    cleaned = strip_comments_and_strings(text)
    if re.search(r"`include\b", cleaned):
        return True

    identifier_tail = r"(?![A-Za-z0-9_$])"
    for definition in defines:
        name = definition.partition("=")[0].strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name):
            continue
        escaped_name = re.escape(name)
        if re.search(rf"`{escaped_name}{identifier_tail}", cleaned):
            return True
        if re.search(
            rf"`(?:ifdef|ifndef|elsif)\s+{escaped_name}{identifier_tail}",
            cleaned,
        ):
            return True
    return False


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
VHDL_ENTITY_RE = re.compile(
    r"\bentity\s+(?P<name>[A-Za-z][A-Za-z0-9_]*)\s+is\b",
    flags=re.IGNORECASE,
)
VHDL_ARCHITECTURE_RE = re.compile(
    r"\barchitecture\s+[A-Za-z][A-Za-z0-9_]*\s+of\s+"
    r"(?P<entity>[A-Za-z][A-Za-z0-9_]*)\s+is\b",
    flags=re.IGNORECASE,
)
VHDL_DIRECT_ENTITY_RE = re.compile(
    r"\bentity\s+(?:[A-Za-z][A-Za-z0-9_]*\.)?"
    r"(?P<name>[A-Za-z][A-Za-z0-9_]*)\b",
    flags=re.IGNORECASE,
)
VHDL_COMPONENT_INSTANCE_RE = re.compile(
    r"\b[A-Za-z][A-Za-z0-9_]*\s*:\s*(?:component\s+)?"
    r"(?P<name>[A-Za-z][A-Za-z0-9_]*)"
    r"(?=\s*(?:(?:generic|port)\s+map\b|;))",
    flags=re.IGNORECASE,
)
VHDL_PACKAGE_DECL_RE = re.compile(
    r"\bpackage\s+(?!body\b)(?P<name>[A-Za-z][A-Za-z0-9_]*)\s+is\b",
    flags=re.IGNORECASE,
)
VHDL_PACKAGE_BODY_RE = re.compile(
    r"\bpackage\s+body\s+(?P<name>[A-Za-z][A-Za-z0-9_]*)\s+is\b",
    flags=re.IGNORECASE,
)
VHDL_WORK_PACKAGE_USE_RE = re.compile(
    r"\buse\s+work\.(?P<name>[A-Za-z][A-Za-z0-9_]*)\.",
    flags=re.IGNORECASE,
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
    language: str = "verilog"


@dataclass(frozen=True)
class TestSpec:
    dut: str
    kind: str
    path: Path
    top: str | None = None

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.path.name}"


def hdl_language(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in VHDL_SUFFIXES:
        return "vhdl"
    if suffix == ".sv":
        return "systemverilog"
    return "verilog"


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


def strip_vhdl_comments(text: str) -> str:
    return re.sub(
        r"--[^\n]*",
        lambda match: " " * len(match.group(0)),
        text,
    )


def extract_vhdl_entity_names(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return [match.group("name") for match in VHDL_ENTITY_RE.finditer(strip_vhdl_comments(text))]


def vhdl_file_dependencies(
    text: str, own_names: set[str], known_names: dict[str, str]
) -> tuple[str, ...]:
    dependencies: set[str] = set()
    cleaned = strip_vhdl_comments(text)
    matches = [
        *VHDL_DIRECT_ENTITY_RE.finditer(cleaned),
        *VHDL_COMPONENT_INSTANCE_RE.finditer(cleaned),
    ]
    for match in matches:
        lowered = match.group("name").lower()
        dependency = known_names.get(lowered)
        if dependency is not None and lowered not in own_names:
            dependencies.add(dependency)
    return tuple(sorted(dependencies))


def _balanced_end(text: str, start: int, opening: str = "(", closing: str = ")") -> int:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_top_level(text: str, separator: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depths = {"(": 0, "[": 0, "{": 0}
    closings = {")": "(", "]": "[", "}": "{"}
    for index, char in enumerate(text):
        if char in depths:
            depths[char] += 1
        elif char in closings:
            opening = closings[char]
            depths[opening] = max(0, depths[opening] - 1)
        elif char == separator and not any(depths.values()):
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def verilog_module_sections(
    path: Path, module: str
) -> tuple[str | None, str, str] | None:
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    text = strip_comments_and_strings(raw_text)
    declaration = re.search(
        rf"\bmodule\s+(?:automatic\s+)?{re.escape(module)}\b", text
    )
    if declaration is None:
        return None
    cursor = declaration.end()
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    parameters: str | None = None
    if cursor < len(text) and text[cursor] == "#":
        parameter_start = text.find("(", cursor + 1)
        if parameter_start < 0:
            return None
        parameter_end = _balanced_end(text, parameter_start)
        if parameter_end < 0:
            return None
        parameters = raw_text[parameter_start + 1 : parameter_end].strip()
        cursor = parameter_end + 1
    port_start = text.find("(", cursor)
    if port_start < 0:
        return None
    port_end = _balanced_end(text, port_start)
    if port_end < 0:
        return None
    body = text[port_end + 1 :]
    end_match = ENDMODULE_RE.search(body)
    if end_match:
        body = body[: end_match.start()]
    return parameters, text[port_start + 1 : port_end], body


def verilog_port_directions(path: Path, module: str) -> dict[str, str]:
    sections = verilog_module_sections(path, module)
    if sections is None:
        return {}
    _parameters, header, body = sections
    directions: dict[str, str] = {}
    current_direction: str | None = None
    ignored = DATA_TYPES | {
        "const", "signed", "unsigned", "var", "supply0", "supply1"
    }
    for segment in _split_top_level(header, ","):
        direction_match = re.search(r"\b(input|output|inout)\b", segment)
        if direction_match:
            current_direction = direction_match.group(1)
        identifiers = [
            token
            for token in re.findall(r"\\[^\s]+|[A-Za-z_][A-Za-z0-9_$]*", segment)
            if token.startswith("\\")
            or (token not in ignored and token not in {"input", "output", "inout"})
        ]
        if current_direction and identifiers:
            directions[identifiers[-1]] = current_direction

    for match in re.finditer(r"\b(input|output|inout)\b([^;]*);", body):
        direction = match.group(1)
        for segment in _split_top_level(match.group(2), ","):
            identifiers = re.findall(
                r"\\[^\s]+|[A-Za-z_][A-Za-z0-9_$]*", segment
            )
            identifiers = [
                item
                for item in identifiers
                if item.startswith("\\") or item not in ignored
            ]
            if identifiers:
                directions[identifiers[-1]] = direction
    return directions


def verilog_input_declarations(path: Path, module: str) -> dict[str, str]:
    sections = verilog_module_sections(path, module)
    if sections is None:
        return {}
    _parameters, header, body = sections
    ignored = DATA_TYPES | {
        "const", "signed", "unsigned", "var", "supply0", "supply1",
        "input", "output", "inout",
    }
    declarations: dict[str, str] = {}

    def consume(segments: Sequence[str], initial_direction: str | None = None) -> None:
        current_direction = initial_direction
        current_packed = ""
        current_signed = False
        for segment in segments:
            direction_match = re.search(r"\b(input|output|inout)\b", segment)
            without_ranges = re.sub(
                r"\[[^\]]*\]",
                lambda match: " " * len(match.group(0)),
                segment,
            )
            identifier_matches = [
                match
                for match in re.finditer(
                    r"\\[^\s]+|[A-Za-z_][A-Za-z0-9_$]*", without_ranges
                )
                if match.group(0).startswith("\\") or match.group(0) not in ignored
            ]
            if direction_match:
                current_direction = direction_match.group(1)
            if not identifier_matches:
                continue
            name_match = identifier_matches[-1]
            name = name_match.group(0)
            before_name = segment[: name_match.start()]
            after_name = segment[name_match.end() :]
            packed_ranges = re.findall(r"\[[^\]]*\]", before_name)
            if direction_match:
                current_packed = " ".join(item.strip() for item in packed_ranges)
                current_signed = bool(re.search(r"\bsigned\b", before_name))
            elif packed_ranges:
                current_packed = " ".join(item.strip() for item in packed_ranges)
            if current_direction != "input":
                continue
            unpacked = " ".join(
                item.strip() for item in re.findall(r"\[[^\]]*\]", after_name)
            )
            pieces = ["  logic"]
            if current_signed:
                pieces.append("signed")
            if current_packed:
                pieces.append(current_packed)
            rendered_name = hdl_reference(name)
            if unpacked:
                separator = "" if rendered_name.endswith(" ") else " "
                rendered_name += separator + unpacked
            pieces.append(rendered_name + ";")
            declarations[name] = " ".join(pieces)

    consume(_split_top_level(header, ","))
    for match in re.finditer(r"\b(input|output|inout)\b([^;]*);", body):
        consume(
            _split_top_level(match.group(2), ","),
            initial_direction=match.group(1),
        )
    return declarations


def vhdl_port_directions(path: Path, entity: str) -> dict[str, str]:
    text = strip_vhdl_comments(path.read_text(encoding="utf-8", errors="replace"))
    declaration = re.search(
        rf"\bentity\s+{re.escape(entity)}\s+is\b", text, flags=re.IGNORECASE
    )
    if declaration is None:
        return {}
    port_match = re.search(r"\bport\s*\(", text[declaration.end() :], flags=re.IGNORECASE)
    if port_match is None:
        return {}
    start = declaration.end() + port_match.end() - 1
    end = _balanced_end(text, start)
    if end < 0:
        return {}
    directions: dict[str, str] = {}
    for declaration_text in _split_top_level(text[start + 1 : end], ";"):
        match = re.match(
            r"\s*(?P<names>[A-Za-z0-9_,\s]+)\s*:\s*"
            r"(?P<direction>inout|in|out|buffer)\b",
            declaration_text,
            flags=re.IGNORECASE,
        )
        if match is None:
            continue
        direction = match.group("direction").lower()
        for name in match.group("names").split(","):
            cleaned = name.strip()
            if cleaned:
                directions[cleaned] = direction
    return directions


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

    lines = ["  // Generated by vwb.py to include unpacked array words."]
    indent = "  "
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
    linear_index = "128'd0"
    for index_name, (lower, _upper), size in zip(indices, bounds, sizes):
        linear_index = (
            f"(({linear_index}) * ({size}) + (({index_name}) - ({lower})))"
        )
    if max_array_words > 0:
        lines.append(
            f"{indent}if ({linear_index} < 128'd{max_array_words}) "
            f"begin : __vwb_array_{array_index}_within_limit"
        )
        indent += "  "
    selected_word = array_name + "".join(f"[{name}]" for name in indices)
    lines.append(f"{indent}initial $dumpvars(0, {selected_word});")
    if max_array_words > 0:
        indent = indent[:-2]
        lines.append(f"{indent}end")
    for _ in indices:
        indent = indent[:-2]
        lines.append(f"{indent}end")
    return "\n" + "\n".join(lines) + "\n"


def unsupported_array_reason(raw: RawModule, array: ArrayDef) -> str | None:
    if array.unsupported_reason:
        return f"{array.unsupported_reason} array '{raw.name}.{array.name}'"
    if array.procedural:
        return f"procedural array '{raw.name}.{array.name}'"
    for declared_range in array.ranges:
        if ":" not in declared_range:
            return (
                f"non-static array '{raw.name}.{array.name}' "
                f"dimension [{declared_range}]"
            )
        left, right = (item.strip() for item in declared_range.split(":", 1))
        if not left or not right:
            return (
                f"non-static array '{raw.name}.{array.name}' "
                f"dimension [{declared_range}]"
            )
    return None


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
            reason = unsupported_array_reason(raw, array)
            if reason:
                print(
                    f"warning: waveform instrumentation skipped {reason}",
                    file=sys.stderr,
                )
                continue
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
        vhdl_entities: dict[Path, list[str]] = {}
        vhdl_architectures: dict[str, list[Path]] = {}
        vhdl_cleaned_sources: dict[Path, str] = {}
        for path in self.files:
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.suffix.lower() in VHDL_SUFFIXES:
                cleaned = strip_vhdl_comments(text)
                vhdl_cleaned_sources[path] = cleaned
                names = [
                    match.group("name") for match in VHDL_ENTITY_RE.finditer(cleaned)
                ]
                vhdl_entities[path] = names
                architecture_entities = [
                    match.group("entity")
                    for match in VHDL_ARCHITECTURE_RE.finditer(cleaned)
                ]
                for entity in architecture_entities:
                    vhdl_architectures.setdefault(entity.lower(), []).append(path)
                if names or architecture_entities:
                    files_with_modules.add(path.resolve())
                continue
            cleaned = strip_comments_and_strings(text)
            cleaned_sources[path] = cleaned
            found = extract_raw_declarations(path, cleaned, MODULE_RE, ENDMODULE_RE)
            raw_modules.extend(found)
            found_interfaces = extract_raw_declarations(
                path, cleaned, INTERFACE_RE, ENDINTERFACE_RE
            )
            raw_interfaces.extend(found_interfaces)
            found_primitives = extract_raw_declarations(
                path, cleaned, PRIMITIVE_RE, ENDPRIMITIVE_RE
            )
            raw_primitives.extend(found_primitives)
            if found or found_interfaces or found_primitives:
                files_with_modules.add(path.resolve())

        vhdl_name_map: dict[str, str] = {}
        for names in vhdl_entities.values():
            for name in names:
                vhdl_name_map.setdefault(name.lower(), name)
        module_names = {module.name for module in raw_modules} | set(
            vhdl_name_map.values()
        )
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
                language=hdl_language(raw.path),
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

        for path, names in vhdl_entities.items():
            own_names = {name.lower() for name in names}
            for name in names:
                canonical_name = vhdl_name_map[name.lower()]
                source_paths = unique_paths(
                    [path, *vhdl_architectures.get(name.lower(), [])]
                )
                dependencies = tuple(
                    sorted(
                        {
                            dependency
                            for source_path in source_paths
                            for dependency in vhdl_file_dependencies(
                                vhdl_cleaned_sources[source_path],
                                own_names,
                                vhdl_name_map,
                            )
                        }
                    )
                )
                definition = ModuleDef(
                    name=canonical_name,
                    path=path,
                    dependencies=dependencies,
                    language="vhdl",
                )
                self.units.setdefault(canonical_name, []).append(definition)
                self.modules.setdefault(canonical_name, []).append(definition)

        self.vhdl_architectures = {
            name: unique_paths(paths) for name, paths in vhdl_architectures.items()
        }

        self.vhdl_packages: dict[str, list[Path]] = {}
        self.vhdl_package_names: dict[str, str] = {}
        self.vhdl_package_bodies: dict[str, list[Path]] = {}
        for path, cleaned in vhdl_cleaned_sources.items():
            for match in VHDL_PACKAGE_DECL_RE.finditer(cleaned):
                name = match.group("name")
                lowered = name.lower()
                self.vhdl_package_names.setdefault(lowered, name)
                self.vhdl_packages.setdefault(lowered, []).append(path)
                files_with_modules.add(path.resolve())
            for match in VHDL_PACKAGE_BODY_RE.finditer(cleaned):
                lowered = match.group("name").lower()
                self.vhdl_package_bodies.setdefault(lowered, []).append(path)
                files_with_modules.add(path.resolve())
        self.vhdl_package_uses: dict[Path, tuple[str, ...]] = {}
        for path, cleaned in vhdl_cleaned_sources.items():
            used = {
                match.group("name").lower()
                for match in VHDL_WORK_PACKAGE_USE_RE.finditer(cleaned)
                if match.group("name").lower() in self.vhdl_packages
            }
            self.vhdl_package_uses[path] = tuple(sorted(used))

        self.packages: dict[str, list[Path]] = {}
        for path, cleaned in cleaned_sources.items():
            for match in PACKAGE_RE.finditer(cleaned):
                self.packages.setdefault(match.group("name"), []).append(path)
                files_with_modules.add(path.resolve())
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
            vhdl_matches = [
                matches
                for candidate, matches in self.modules.items()
                if candidate.casefold() == name.casefold()
                and all(item.language == "vhdl" for item in matches)
            ]
            if len(vhdl_matches) == 1:
                definitions = vhdl_matches[0]
        if not definitions:
            choices = ", ".join(self.names()) or "none"
            raise VWBError(f"unknown module '{name}'; discovered modules: {choices}")
        if len(definitions) > 1:
            paths = ", ".join(str(item.path) for item in definitions)
            raise VWBError(f"module '{name}' is declared more than once: {paths}")
        return definitions[0]

    def implementation_files(self, name: str) -> list[Path]:
        definition = self.definition(name)
        paths = [item.path for item in self.modules[definition.name]]
        if definition.language == "vhdl":
            paths.extend(self.vhdl_architectures.get(definition.name.lower(), []))
        return unique_paths(paths)

    def closure(self, top: str) -> list[Path]:
        top_definition = self.definition(top)
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
            definition_paths = [definition.path]
            if definition.language == "vhdl":
                definition_paths.extend(
                    self.vhdl_architectures.get(definition.name.lower(), [])
                )
            for path in definition_paths:
                if path not in seen_module_files:
                    module_files.append(path)
                    seen_module_files.add(path)
            visiting.remove(name)
            visited.add(name)

        visit(top_definition.name)
        if top_definition.language == "vhdl":
            package_files: list[Path] = []
            visited_packages: set[str] = set()

            def visit_vhdl_package(name: str) -> None:
                if name in visited_packages:
                    return
                definitions = unique_paths(self.vhdl_packages.get(name, []))
                display_name = self.vhdl_package_names.get(name, name)
                if len(definitions) != 1:
                    paths = ", ".join(str(path) for path in definitions) or "none"
                    raise VWBError(
                        f"VHDL package '{display_name}' must have one declaration; "
                        f"found: {paths}"
                    )
                visited_packages.add(name)
                paths = unique_paths(
                    [*definitions, *self.vhdl_package_bodies.get(name, [])]
                )
                for path in paths:
                    for dependency in self.vhdl_package_uses.get(path, ()):
                        if dependency != name:
                            visit_vhdl_package(dependency)
                package_files.extend(paths)

            for path in module_files:
                for package in self.vhdl_package_uses.get(path, ()):
                    visit_vhdl_package(package)
            return unique_paths([*package_files, *module_files])

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


def module_from_test_stem(
    stem: str,
    module_names: Sequence[str],
    case_insensitive_names: Iterable[str] = (),
) -> str | None:
    matches: list[tuple[int, int, str]] = []
    insensitive = set(case_insensitive_names)
    for name in module_names:
        candidate_stem = stem.casefold() if name in insensitive else stem
        aliases = {name, python_identifier_component(name)}
        matched: tuple[int, int, str] | None = None
        for alias in aliases:
            candidate_name = alias.casefold() if name in insensitive else alias
            exact = {
                f"test_{candidate_name}",
                f"tb_{candidate_name}",
                f"{candidate_name}_test",
                f"{candidate_name}_tb",
            }
            if candidate_stem in exact:
                matched = (0, -len(name), name)
                break
            if candidate_stem.startswith(f"test_{candidate_name}_"):
                matched = (1, -len(name), name)
        if matched is not None:
            matches.append(matched)
    if not matches:
        return None
    matches.sort()
    best = matches[0]
    tied = [item for item in matches if item[:2] == best[:2]]
    return tied[0][2] if len(tied) == 1 else None


def infer_hdl_test_top(path: Path, dut: str) -> str | None:
    if path.suffix.lower() in VHDL_SUFFIXES:
        names = extract_vhdl_entity_names(path)
        preferred = [f"test_{dut}", f"tb_{dut}", f"{dut}_test", f"{dut}_tb"]
        lowered = {name.lower(): name for name in names}
        preferred_found = [lowered[name.lower()] for name in preferred if name.lower() in lowered]
        if len(preferred_found) == 1:
            return preferred_found[0]
        return names[0] if len(names) == 1 else None
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
        self._ghdl_vpi_library: str | None = None

    def style(
        self, value: object, *codes: str, stream: TextIO | None = None
    ) -> str:
        return self.colors.apply(value, *codes, stream=stream)

    def _discover_tests(self) -> list[TestSpec]:
        module_names = self.catalog.names()
        vhdl_names = {
            name
            for name in module_names
            if self.catalog.definition(name).language == "vhdl"
        }
        tests: list[TestSpec] = []
        for path in sorted(self.test_dir.rglob("*.py")):
            if not path.is_file() or not is_cocotb_test(path):
                continue
            dut = module_from_test_stem(path.stem, module_names, vhdl_names)
            if dut:
                tests.append(TestSpec(dut=dut, kind="cocotb", path=path.resolve()))

        hdl_tests: list[TestSpec] = []
        for path in self.test_hdl_files:
            dut = module_from_test_stem(path.stem, module_names, vhdl_names)
            if dut:
                kind = "vhdl" if path.suffix.lower() in VHDL_SUFFIXES else "verilog"
                hdl_tests.append(
                    TestSpec(
                        dut=dut,
                        kind=kind,
                        path=path,
                        top=infer_hdl_test_top(path, dut),
                    )
                )
        declared_tops = {
            (spec.dut, spec.kind)
            for spec in hdl_tests
            if spec.top is not None
        }
        tests.extend(
            spec
            for spec in hdl_tests
            if spec.top is not None or (spec.dut, spec.kind) not in declared_tops
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
        roots = self.catalog.roots()
        if len(roots) == 1:
            return roots[0]
        raise VWBError(f"cannot choose a unique top module; specify one of: {choices}")

    def port_directions(self, module: str) -> dict[str, str]:
        definition = self.catalog.definition(module)
        if definition.language == "vhdl":
            return vhdl_port_directions(definition.path, definition.name)
        return verilog_port_directions(definition.path, definition.name)

    @staticmethod
    def _clock_name(names: Sequence[str]) -> str | None:
        for name in names:
            lowered = name.lower().lstrip("\\")
            if (
                lowered in {"clk", "clock"}
                or lowered.endswith(("clk", "clock", "clk_i", "clock_i"))
                or re.search(r"(?:^|_)(?:clk|clock)(?:_i|_in)$", lowered)
            ):
                return name
        return None

    @staticmethod
    def _reset_name(names: Sequence[str]) -> str | None:
        for name in names:
            lowered = name.lower().lstrip("\\")
            if (
                lowered in {"reset", "rst", "reset_n", "rst_n", "resetn", "rstn"}
                or "reset" in lowered
                or lowered.startswith("rst")
            ):
                return name
        return None

    @staticmethod
    def _reset_is_active_low(name: str) -> bool:
        lowered = name.lower().lstrip("\\")
        return bool(
            re.search(r"(?:^|_)(?:[as]?(?:reset|rst))_?n(?:_|$)", lowered)
            or re.search(r"(?:^|_)(?:nreset|nrst)(?:_|$)", lowered)
        )

    def _cocotb_starter(self, module: str) -> str:
        directions = self.port_directions(module)
        inputs = [
            name
            for name, direction in directions.items()
            if direction in {"input", "in"}
        ]
        input_literal = repr(tuple(inputs))
        clock = self._clock_name(inputs)
        reset = self._reset_name(inputs)
        reset_active_low = bool(reset and self._reset_is_active_low(reset))
        return (
            '"""Starter test generated by vwb.py. Add checks as you learn the design."""\n\n'
            "import cocotb\n"
            "from cocotb.clock import Clock\n"
            "from cocotb.triggers import Timer\n\n\n"
            f"INPUTS = {input_literal}\n\n\n"
            "def _vwb_signal(dut, name):\n"
            "    if not name.startswith(\"\\\\\"):\n"
            "        return getattr(dut, name)\n"
            "    simulator_name = name[1:]\n"
            "    for child in dut:\n"
            "        if child._name == simulator_name:\n"
            "            return child\n"
            "    raise AttributeError(f\"DUT has no signal {name}\")\n\n\n"
            "@cocotb.test()\n"
            f"async def test_{python_identifier_component(module)}_starter(dut):\n"
            "    # Give every DUT input a known starting value.\n"
            "    for name in INPUTS:\n"
            "        _vwb_signal(dut, name).value = 0\n"
            + (
                "\n    cocotb.start_soon(Clock("
                f"_vwb_signal(dut, {clock!r}), 10, units=\"ns\").start(start_high=False))\n"
                if clock
                else ""
            )
            + (
                f"\n    reset = _vwb_signal(dut, {reset!r})\n"
                f"    reset.value = {0 if reset_active_low else 1}\n"
                "    await Timer(10, units=\"ns\")\n"
                f"    reset.value = {1 if reset_active_low else 0}\n"
                if reset
                else ""
            )
            + "\n    await Timer(10, units=\"ns\")\n"
        )

    def _verilog_starter(self, module: str) -> tuple[str, str]:
        definition = self.catalog.definition(module)
        directions = self.port_directions(module)
        inputs = [
            name for name, direction in directions.items() if direction == "input"
        ]
        input_declarations = verilog_input_declarations(definition.path, module)
        sections = verilog_module_sections(definition.path, module)
        parameters = sections[0] if sections is not None else None
        clock = self._clock_name(inputs)
        reset = self._reset_name(inputs)
        top = f"test_{python_identifier_component(module)}"
        declarations = "\n".join(
            input_declarations.get(name, f"  logic {hdl_reference(name)};")
            for name in inputs
        )
        connections = ",\n".join(
            f"    .{hdl_reference(name)}({hdl_reference(name)})"
            for name in inputs
        )
        initialization = "\n".join(
            f"    {hdl_reference(name)} = '0;" for name in inputs
        )
        clock_block = (
            f"\n  always #5 {hdl_reference(clock)} = ~{hdl_reference(clock)};\n"
            if clock
            else ""
        )
        reset_block = ""
        if reset:
            active = "1'b0" if self._reset_is_active_low(reset) else "1'b1"
            inactive = "1'b1" if active == "1'b0" else "1'b0"
            reset_block = (
                f"\n    {hdl_reference(reset)} = {active};\n"
                f"    #10 {hdl_reference(reset)} = {inactive};"
            )
        module_reference = hdl_reference(module)
        instance = (
            f"  {module_reference} dut (\n{connections}\n  );"
            if connections
            else f"  {module_reference} dut ();"
        )
        return (
            top,
            "`timescale 1ns/1ps\n"
            + (
                f"module {top} #(\n{parameters}\n);\n"
                if parameters
                else f"module {top};\n"
            )
            + (declarations + "\n" if declarations else "")
            + instance
            + "\n"
            + clock_block
            + "  initial begin\n"
            + (initialization + "\n" if initialization else "")
            + reset_block
            + "\n    #10 $finish;\n"
            + "  end\nendmodule\n",
        )

    def generate_starter_test(self, module: str, test_language: str) -> TestSpec:
        definition = self.catalog.definition(module)
        module = definition.name
        if test_language == "vhdl":
            raise VWBError(
                "automatic VHDL testbench generation is not supported; "
                "use --test-language cocotb or add a VHDL test file"
            )
        language = "cocotb" if test_language in {"auto", "cocotb"} else test_language
        if language == "verilog" and definition.language == "vhdl":
            raise VWBError(
                "a Verilog testbench cannot directly test VHDL; use --test-language cocotb"
            )
        suffix = ".py" if language == "cocotb" else ".sv"
        component = python_identifier_component(module)
        path = self.test_dir / f"test_{component}_starter{suffix}"
        if path.exists() or path.is_symlink():
            raise VWBError(f"refusing to overwrite existing starter test: {path}")
        top: str | None = None
        if language == "cocotb":
            content = self._cocotb_starter(module)
        else:
            top, content = self._verilog_starter(module)
        print(
            self.style("generated starter test:", Ansi.BOLD, Ansi.GREEN),
            display_path(path, self.root),
        )
        if not self.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            package_marker = path.parent / "__init__.py"
            if not package_marker.exists():
                package_marker.write_text("", encoding="ascii")
            path.write_text(content, encoding="utf-8")
        return TestSpec(dut=module, kind=language, path=path.resolve(), top=top)

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

    def require_tool_choice(self, command: str) -> tuple[str, str]:
        choice = find_tool_choice(command)
        if choice is not None:
            return choice
        candidates = TOOL_ALTERNATIVES.get(command, (command,))
        if self.dry_run:
            return candidates[0], candidates[0]
        raise VWBError(
            "required command is not on PATH: " + " or ".join(candidates)
        )

    def require_tool(self, command: str) -> str:
        return self.require_tool_choice(command)[1]

    def run(
        self,
        command: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        capture: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        argv = [str(item) for item in command]
        if self.verbose or self.dry_run:
            location = f" (cwd={cwd})" if cwd else ""
            print(f"$ {shlex.join(argv)}{location}")
        if self.dry_run:
            return subprocess.CompletedProcess(argv, 0, "", "")
        try:
            process = subprocess.Popen(
                argv,
                cwd=str(cwd) if cwd else None,
                env=env,
                text=True,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.PIPE if capture else None,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    stdout, stderr = process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    stdout, stderr = process.communicate()
                else:
                    # The group leader may exit while a descendant ignores
                    # SIGTERM. The isolated process group can still be killed
                    # safely after communicate() has reaped the leader.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                print(
                    f"error: command timed out after {timeout:g}s: {argv[0]}",
                    file=sys.stderr,
                )
                return subprocess.CompletedProcess(
                    argv, 124, stdout or "", stderr or ""
                )
            except KeyboardInterrupt:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                raise
            return subprocess.CompletedProcess(
                argv, process.returncode, stdout, stderr
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

    def ghdl_vpi_library(self) -> str:
        if self._ghdl_vpi_library is None:
            if self.dry_run:
                self._ghdl_vpi_library = "<cocotb-ghdl-vpi-library>"
            else:
                self._ghdl_vpi_library = self.capture_tool(
                    ["cocotb-config", "--lib-name-path", "vpi", "ghdl"]
                )
            if not self._ghdl_vpi_library:
                raise VWBError("cocotb-config returned an empty GHDL VPI library path")
        return self._ghdl_vpi_library

    def specs_for(
        self,
        modules: Sequence[str],
        test_language: str,
        explicit_test: str | None,
        explicit_test_top: str | None,
    ) -> list[TestSpec]:
        kind = "verilog" if test_language == "hdl" else test_language
        selected_modules = [
            self.catalog.definition(module).name for module in modules
        ]

        if explicit_test:
            if len(selected_modules) > 1:
                raise VWBError("--test can only be used with one module")
            path = project_path(self.root, explicit_test)
            if not path.is_file():
                raise VWBError(f"test file does not exist: {path}")
            suffix = path.suffix.lower()
            if suffix == ".py":
                detected_kind = "cocotb"
            elif suffix in VHDL_SUFFIXES:
                detected_kind = "vhdl"
            elif suffix in VERILOG_SUFFIXES:
                detected_kind = "verilog"
            else:
                raise VWBError(f"unsupported test file type: {path.suffix}")
            if kind != "auto" and kind != detected_kind:
                raise VWBError(
                    f"--test-language {test_language} does not match test file {path}"
                )
            dut = selected_modules[0] if selected_modules else module_from_test_stem(
                path.stem,
                self.catalog.names(),
                {
                    name
                    for name in self.catalog.names()
                    if self.catalog.definition(name).language == "vhdl"
                },
            )
            if dut is None:
                raise VWBError("could not infer DUT from --test; also specify a module")
            self.catalog.definition(dut)
            if detected_kind == "cocotb" and not is_cocotb_test(path):
                raise VWBError(f"Python file has no @cocotb.test: {path}")
            if detected_kind == "cocotb" and explicit_test_top:
                raise VWBError("--test-top only applies to HDL testbenches")
            top = explicit_test_top
            if detected_kind in {"verilog", "vhdl"} and top is None:
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
            if len(specs) != 1 or specs[0].kind not in {"verilog", "vhdl"}:
                raise VWBError("--test-top requires exactly one HDL test")
            specs = [
                TestSpec(
                    dut=specs[0].dut,
                    kind=specs[0].kind,
                    path=specs[0].path,
                    top=explicit_test_top,
                )
            ]
        if requested:
            missing = sorted(requested - {spec.dut for spec in specs})
            for module in missing:
                specs.append(self.generate_starter_test(module, test_language))
        if not specs:
            raise VWBError("no runnable tests were discovered")
        return sorted(specs, key=lambda item: (item.dut, item.kind, str(item.path)))

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

    def _convert_systemverilog(
        self,
        sources: Sequence[Path],
        work_dir: Path,
        defines: Sequence[str],
        include_dirs: Sequence[Path],
        compile_args: Sequence[str],
    ) -> list[Path]:
        if not any(path.suffix.lower() == ".sv" for path in sources):
            return list(sources)
        if find_tool("sv2v") is None and not self.dry_run:
            return list(sources)
        self.require_tool("sv2v")
        preprocessed = work_dir / "sv2v-input.sv"
        preprocess: list[str | Path] = ["iverilog", "-E", "-g2012", "-o", preprocessed]
        for directory in include_dirs:
            preprocess.extend(["-I", directory])
        for define in defines:
            preprocess.append(f"-D{define}")
        preprocess.extend(compile_args)
        preprocess.extend(sources)
        if self.run(preprocess, cwd=self.root).returncode != 0:
            raise VWBError("SystemVerilog preprocessing failed")
        converted = work_dir / "sv2v-output.v"
        result = self.run(["sv2v", preprocessed], cwd=self.root, capture=True)
        if result.returncode != 0:
            detail = result.stderr.strip()
            raise VWBError("SystemVerilog conversion failed" + (f": {detail}" if detail else ""))
        if not self.dry_run:
            converted.write_text(result.stdout, encoding="utf-8")
        return [converted]

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
        gate_netlist: Path | None = None,
    ) -> tuple[bool, Path | None, Path]:
        self.require_tool("iverilog")
        self.prepare_build_dir()
        if not self.dry_run:
            work_dir.mkdir(parents=True, exist_ok=True)
        simulation = work_dir / "sim.vvp"
        command_file = work_dir / "cmds.f"
        if not self.dry_run:
            command_file.write_text("+timescale+1ns/1ps\n", encoding="ascii")
        if gate_netlist is not None:
            simlib = gate_netlist.parent / "yosys_simlib.v"
            if not self.dry_run and (not simlib.is_file() or simlib.stat().st_size == 0):
                raise VWBError(f"Yosys simulation library is missing: {simlib}")
            sources = [simlib, gate_netlist]
        else:
            sources = self.catalog.closure(spec.dut)
        if any(path.suffix.lower() in VHDL_SUFFIXES for path in sources):
            raise VWBError("mixed-language Icarus simulation is not supported")
        selected_top = spec.dut

        if spec.kind == "verilog":
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

        original_sources = unique_paths(sources)
        base_command: list[str | Path] = [
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
            base_command.extend(["-I", directory])
        for directory in sorted({path.parent for path in self.catalog.files}):
            base_command.extend(["-y", directory])
        base_command.extend(["-Y", ".v", "-Y", ".sv"])
        for define in defines:
            base_command.append(f"-D{define}")
        base_command.extend(compile_args)

        wave_path: Path | None = None
        dump_module: Path | None = None
        if waves:
            if max_array_words < 0:
                raise VWBError("--max-array-words must be zero or greater")
            if max_array_words >= 1 << 128:
                raise VWBError("--max-array-words must fit in 128 bits")
            wave_path = work_dir / f"{artifact_component(spec.dut)}.{wave_format}"
            dump_module = work_dir / "vwb_dump.v"
            if not self.dry_run:
                self._write_dump_module(dump_module, selected_top, wave_path)

        def compile_sources(candidate_sources: Sequence[Path]) -> subprocess.CompletedProcess[str]:
            prepared_sources = list(candidate_sources)
            if waves:
                prepared_sources = self._instrument_array_sources(
                    prepared_sources,
                    work_dir,
                    defines,
                    include_dirs,
                    compile_args,
                    max_array_words,
                )
            command = list(base_command)
            if dump_module is not None:
                command.extend(["-s", "vwb_dump"])
                prepared_sources.append(dump_module)
            command.extend(["-s", tool_identifier(selected_top)])
            command.extend(prepared_sources)
            return self.run(command, cwd=self.root)

        result = compile_sources(original_sources)
        if (
            result.returncode != 0
            and any(path.suffix.lower() == ".sv" for path in original_sources)
            and find_tool("sv2v") is not None
        ):
            print(
                self.style(
                    "warning: native SystemVerilog compilation failed; trying sv2v",
                    Ansi.BOLD,
                    Ansi.YELLOW,
                    stream=sys.stderr,
                ),
                file=sys.stderr,
            )
            converted_sources = self._convert_systemverilog(
                original_sources, work_dir, defines, include_dirs, compile_args
            )
            result = compile_sources(converted_sources)
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
                test_language=spec.kind,
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
                "test_language": spec.kind,
                "test_path": display_path(spec.path, self.root),
                "test_top": spec.top,
                "testcase": args.testcase,
                "seed": getattr(args, "_effective_seed", args.seed),
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

    def _legacy_wave_layout(self, dut: str | None) -> Path | None:
        if not dut:
            return None
        filename = f"{artifact_component(dut)}.gtkw"
        directories = unique_paths(
            [
                self.root,
                self.src_dir.parent,
                self.test_dir.parent,
                self.src_dir,
                self.test_dir,
            ]
        )
        for directory in directories:
            candidate = directory / filename
            if candidate.is_file() and not candidate.is_symlink():
                return candidate
        return None

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
            legacy = self._legacy_wave_layout(legacy_dut)
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

    def _cocotb_environment(
        self,
        spec: TestSpec,
        work_dir: Path,
        args: argparse.Namespace,
        top_language: str,
    ) -> tuple[dict[str, str], Path]:
        environment = os.environ.copy()
        results_file = work_dir / "results.xml"
        if results_file.exists() and not self.dry_run:
            results_file.unlink()
        module_name, import_root = python_module_import(
            spec.path, self.root, self.test_dir
        )
        legacy_top, modern_top = cocotb_toplevel_names(spec.dut)
        environment.update(
            {
                "MODULE": module_name,
                "COCOTB_TEST_MODULES": module_name,
                "TOPLEVEL": legacy_top,
                "COCOTB_TOPLEVEL": modern_top,
                "TOPLEVEL_LANG": top_language,
                "COCOTB_RESULTS_FILE": str(results_file),
                "PYTHONDONTWRITEBYTECODE": "1",
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
        return environment, results_file

    def _run_vhdl_test(
        self,
        spec: TestSpec,
        args: argparse.Namespace,
        work_dir: Path,
    ) -> tuple[bool, Path | None]:
        self.require_tool("ghdl")
        self.prepare_build_dir()
        if not self.dry_run:
            work_dir.mkdir(parents=True, exist_ok=True)
        sources = self.catalog.closure(spec.dut)
        top = spec.dut
        if spec.kind == "vhdl":
            top = args.test_top or spec.top or ""
            if not top:
                raise VWBError(
                    f"cannot infer VHDL testbench entity in {spec.path}; use --test-top"
                )
            sources = unique_paths([*sources, spec.path])
            test_definitions = self.test_catalog.modules.get(top, [])
            if (
                len(test_definitions) == 1
                and test_definitions[0].path.resolve() == spec.path.resolve()
            ):
                sources = unique_paths(
                    [*sources, *self.test_catalog.closure(top)]
                )
        if any(path.suffix.lower() not in VHDL_SUFFIXES for path in sources):
            raise VWBError("mixed VHDL/Verilog simulation needs an external mixed-language simulator")
        work_library = work_dir / "ghdl-work"
        if not self.dry_run:
            work_library.mkdir(parents=True, exist_ok=True)
        analyze: list[str | Path] = [
            "ghdl", "-i", "--std=08", f"--workdir={work_library}", *sources
        ]
        if self.run(analyze, cwd=self.root).returncode != 0:
            return False, None
        if self.run(
            ["ghdl", "-m", "--std=08", f"--workdir={work_library}", top],
            cwd=self.root,
        ).returncode != 0:
            return False, None

        wave_path: Path | None = None
        command: list[str | Path] = [
            "ghdl", "-r", "--std=08", f"--workdir={work_library}", top
        ]
        environment = os.environ.copy()
        results_file: Path | None = None
        if spec.kind == "cocotb":
            environment, results_file = self._cocotb_environment(
                spec, work_dir, args, "vhdl"
            )
            command.append(f"--vpi={self.ghdl_vpi_library()}")
        if args.waves:
            wave_path = work_dir / f"{artifact_component(spec.dut)}.{args.wave_format}"
            command.append(f"--{args.wave_format}={wave_path}")
        command.extend(args.sim_arg)
        command.extend(
            item if item.startswith("+") else f"+{item}" for item in args.plusarg
        )
        result = self.run(command, cwd=work_dir, env=environment)
        if self.dry_run:
            return True, wave_path
        passed = result.returncode == 0
        if results_file is not None:
            passed = passed and self._results_passed(results_file)
        if wave_path is not None and not wave_path.is_file():
            print(f"error: waveform was not produced: {wave_path}", file=sys.stderr)
            passed = False
        return passed, wave_path

    def _run_rtl_test_spec(self, spec: TestSpec, args: argparse.Namespace) -> tuple[bool, Path | None]:
        if spec.kind in {"verilog", "vhdl"} and (args.testcase or args.seed is not None):
            raise VWBError("--testcase and --seed only apply to Cocotb tests")
        work_dir = (
            self.build_dir
            / "sim"
            / artifact_component(spec.dut)
            / f"{spec.kind}-{artifact_component(spec.path.stem)}"
        )
        self.prepare_build_dir()
        self._reset_sim_work_dir(work_dir)
        definition = self.catalog.definition(spec.dut)
        if definition.language == "vhdl":
            if spec.kind == "verilog":
                raise VWBError("a Verilog testbench cannot directly test a VHDL design")
            return self._run_vhdl_test(spec, args, work_dir)
        if spec.kind == "vhdl":
            raise VWBError("a VHDL testbench cannot directly test a Verilog design")
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
        results_file: Path | None = None
        if spec.kind == "cocotb":
            lib_dir, lib_name = self.cocotb_library()
            environment, results_file = self._cocotb_environment(
                spec, work_dir, args, "verilog"
            )
            vvp_args.extend(["-M", lib_dir, "-m", lib_name])

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
        if results_file is not None:
            passed = passed and self._results_passed(results_file)
        if args.waves and wave_path is not None and not wave_path.is_file():
            passed = False
            print(f"error: waveform was not produced: {wave_path}", file=sys.stderr)
        return passed, wave_path

    def _run_gate_test_spec(
        self,
        spec: TestSpec,
        args: argparse.Namespace,
        netlist: Path,
    ) -> bool:
        if spec.kind == "vhdl":
            raise VWBError(
                "a native VHDL testbench cannot drive a Verilog gate netlist; "
                "use Cocotb or --no-gate-level"
            )
        work_dir = (
            self.build_dir
            / "sim"
            / artifact_component(spec.dut)
            / f"{spec.kind}-{artifact_component(spec.path.stem)}-gate"
        )
        self._reset_sim_work_dir(work_dir)
        gate_args = argparse.Namespace(**vars(args))
        gate_args.waves = False
        compiled, _, simulation = self._compile_simulation(
            spec,
            work_dir,
            waves=False,
            wave_format=args.wave_format,
            defines=args.define,
            includes=args.include,
            compile_args=args.compile_arg,
            test_top=args.test_top,
            max_array_words=getattr(args, "max_array_words", DEFAULT_MAX_ARRAY_WORDS),
            gate_netlist=netlist,
        )
        if not compiled:
            return False
        self.require_tool("vvp")
        command: list[str | Path] = ["vvp"]
        environment = os.environ.copy()
        results_file: Path | None = None
        if spec.kind == "cocotb":
            lib_dir, lib_name = self.cocotb_library()
            environment, results_file = self._cocotb_environment(
                spec, work_dir, gate_args, "verilog"
            )
            command.extend(["-M", lib_dir, "-m", lib_name])
        command.extend(args.sim_arg)
        command.append(simulation)
        command.extend(
            item if item.startswith("+") else f"+{item}" for item in args.plusarg
        )
        result = self.run(command, cwd=work_dir, env=environment)
        if self.dry_run:
            return True
        passed = result.returncode == 0
        if results_file is not None:
            passed = passed and self._results_passed(results_file)
        return passed

    def run_test_spec(self, spec: TestSpec, args: argparse.Namespace) -> tuple[bool, Path | None]:
        run_args = args
        if spec.kind == "cocotb" and args.seed is None:
            run_args = argparse.Namespace(**vars(args))
            run_args.seed = int.from_bytes(os.urandom(4), "big") & 0x7FFFFFFF
            args._effective_seed = run_args.seed
            print(f"  Cocotb seed: {run_args.seed}")

        rtl_passed, wave_path = self._run_rtl_test_spec(spec, run_args)
        print(
            "  "
            + self.style(
                "RTL PASS" if rtl_passed else "RTL FAIL",
                Ansi.GREEN if rtl_passed else Ansi.RED,
            )
        )
        if not getattr(args, "gate_level", False):
            return rtl_passed, wave_path
        if spec.kind == "vhdl":
            print(
                "  "
                + self.style("GATE SKIP", Ansi.YELLOW)
                + " (native VHDL testbench cannot drive a Verilog netlist)"
            )
            return rtl_passed, wave_path
        try:
            netlist = self.gate_netlist(spec.dut, run_args.define, run_args.include)
            gate_passed = self._run_gate_test_spec(spec, run_args, netlist)
        except VWBError as exc:
            gate_passed = False
            print(f"error: gate-level simulation: {exc}", file=sys.stderr)
        print(
            "  "
            + self.style(
                "GATE PASS" if gate_passed else "GATE FAIL",
                Ansi.GREEN if gate_passed else Ansi.RED,
            )
        )
        return rtl_passed and gate_passed, wave_path

    def run_tests(self, specs: Sequence[TestSpec], args: argparse.Namespace) -> tuple[bool, list[Path]]:
        if any(spec.kind in {"verilog", "vhdl"} for spec in specs) and (
            args.testcase or args.seed is not None
        ):
            raise VWBError(
                "--testcase and --seed require --test-language cocotb when "
                "HDL testbenches are selected"
            )
        passed_count = 0
        wave_paths: list[Path] = []
        failures: list[tuple[TestSpec, str]] = []
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
                reason = str(exc)
                print(f"error: {exc}", file=sys.stderr)
            else:
                reason = "one or more requested simulation stages failed"
            print(
                self.style(
                    "PASS" if passed else "FAIL",
                    Ansi.BOLD,
                    Ansi.GREEN if passed else Ansi.RED,
                )
            )
            if passed:
                passed_count += 1
            else:
                failures.append((spec, reason))
            if wave_path is not None:
                wave_paths.append(wave_path)
        all_passed = passed_count == len(specs)
        print(
            self.style("==>", Ansi.BOLD, Ansi.CYAN)
            + " "
            + self.style(
                f"{passed_count}/{len(specs)} test runs passed",
                Ansi.GREEN if all_passed else Ansi.RED,
            )
        )
        if failures:
            print(self.style("Failed test runs:", Ansi.BOLD, Ansi.RED))
            for spec, reason in failures:
                print(f"  {spec.dut} ({spec.label}): {reason}")
        return all_passed, wave_paths

    def lint_module(
        self,
        module: str,
        defines: Sequence[str],
        includes: Sequence[str],
        extra_args: Sequence[str],
    ) -> bool:
        return self.lint_with_tool(
            module,
            "verilator",
            defines,
            includes,
            verilator_args=extra_args,
        )

    def lint_with_tool(
        self,
        module: str,
        tool: str,
        defines: Sequence[str],
        includes: Sequence[str],
        *,
        iverilog_args: Sequence[str] = (),
        verilator_args: Sequence[str] = (),
        yosys_args: Sequence[str] = (),
        verible_args: Sequence[str] = (),
        ghdl_args: Sequence[str] = (),
    ) -> bool:
        definition = self.catalog.definition(module)
        module = definition.name
        external_top = tool_identifier(module)
        output_dir = self.build_dir / "lint" / artifact_component(module) / tool
        self.prepare_build_dir()
        if not self.dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)
        original_sources = self.catalog.closure(module)
        include_dirs = self.include_dirs(includes)

        if tool == "ghdl":
            if definition.language != "vhdl":
                raise VWBError("GHDL only checks VHDL designs")
            self.require_tool("ghdl")
            work_library = output_dir / "ghdl-work"
            if not self.dry_run:
                work_library.mkdir(parents=True, exist_ok=True)
            analyze = [
                "ghdl", "-i", "--std=08", f"--workdir={work_library}",
                *ghdl_args, *original_sources,
            ]
            if self.run(analyze, cwd=self.root).returncode != 0:
                return False
            return self.run(
                [
                    "ghdl", "-m", "--std=08", f"--workdir={work_library}",
                    *ghdl_args, module,
                ],
                cwd=self.root,
            ).returncode == 0

        if tool == "verible" and definition.language == "vhdl":
            raise VWBError("Verible checks Verilog and SystemVerilog, not VHDL")
        sources = original_sources
        if tool == "yosys" or definition.language == "vhdl":
            sources = self.yosys_sources(module, output_dir, defines, includes)

        if tool == "iverilog":
            self.require_tool("iverilog")

            def run_iverilog(input_sources: Sequence[Path]) -> subprocess.CompletedProcess[str]:
                command: list[str | Path] = [
                    "iverilog", "-g2012", "-Wall", "-t", "null", "-s", external_top
                ]
                for directory in include_dirs:
                    command.extend(["-I", directory])
                for define in defines:
                    command.append(f"-D{define}")
                command.extend(iverilog_args)
                command.extend(input_sources)
                return self.run(command, cwd=self.root)

            result = run_iverilog(sources)
            if (
                result.returncode != 0
                and definition.language != "vhdl"
                and any(path.suffix.lower() == ".sv" for path in original_sources)
                and find_tool("sv2v") is not None
            ):
                print(
                    self.style(
                        "warning: native SystemVerilog lint failed; trying sv2v",
                        Ansi.BOLD,
                        Ansi.YELLOW,
                        stream=sys.stderr,
                    ),
                    file=sys.stderr,
                )
                converted_sources = self.yosys_sources(
                    module, output_dir, defines, includes
                )
                result = run_iverilog(converted_sources)
            return result.returncode == 0

        if tool == "verilator":
            self.require_tool("verilator")
            command = [
                "verilator", "--lint-only", "--top-module", external_top,
                "--timing", "-Wall", "-Wno-fatal", "-Wno-COMBDLY",
                "-Wno-DECLFILENAME", "-Wno-INCABSPATH",
            ]
            for directory in include_dirs:
                command.append(f"-I{directory}")
            for define in defines:
                command.append(f"-D{define}")
            command.extend(verilator_args)
            command.extend(sources)
            return self.run(command, cwd=self.root).returncode == 0

        if tool == "yosys":
            self.require_tool("yosys")
            script = output_dir / "lint.ys"
            log = output_dir / "yosys.log"
            commands = [
                self._yosys_read_sources_command(sources, defines, includes),
                self._yosys_tcl_command(
                    ["hierarchy", "-check", "-top", require_yosys_identifier(module)]
                ),
                self._yosys_tcl_command(["proc"]),
                self._yosys_tcl_command(["check"]),
            ]
            commands.extend(yosys_args)
            if not self.dry_run:
                script.write_text("\n".join(commands) + "\n", encoding="utf-8")
            result = self.run(
                ["yosys", "-c", script], cwd=self.root, capture=True
            )
            output = "\n".join(
                part.rstrip() for part in (result.stdout, result.stderr) if part
            )
            if not self.dry_run:
                log.write_text(output + ("\n" if output else ""), encoding="utf-8")
            diagnostics = [
                line
                for line in output.splitlines()
                if re.search(r"\b(?:warning|error)\b", line, re.IGNORECASE)
            ]
            for diagnostic in diagnostics:
                print(diagnostic, file=sys.stderr)
            if result.returncode != 0 and not diagnostics:
                print(
                    f"error: Yosys lint failed; full log: {display_path(log, self.root)}",
                    file=sys.stderr,
                )
            return result.returncode == 0

        if tool == "verible":
            self.require_tool("verible-verilog-lint")
            preprocess_sources = {
                source
                for source in original_sources
                if verilog_source_needs_preprocessing(
                    source.read_text(encoding="utf-8", errors="replace"),
                    defines,
                )
            }
            preprocessed_dir = output_dir / "preprocessed"
            if preprocess_sources:
                self.require_tool("iverilog")
                if not self.dry_run:
                    preprocessed_dir.mkdir(parents=True, exist_ok=True)
            lint_sources: list[Path] = []
            for index, source in enumerate(original_sources):
                if source in preprocess_sources:
                    generated = (
                        preprocessed_dir
                        / f"{index:03d}-{artifact_component(source.stem)}{source.suffix}"
                    )
                    command: list[str | Path] = [
                        "iverilog",
                        "-E",
                        "-g2012",
                        "-o",
                        generated,
                    ]
                    for define in defines:
                        command.append(f"-D{define}")
                    for directory in include_dirs:
                        command.extend(["-I", directory])
                    command.append(source)
                    result = self.run(command, cwd=self.root, capture=True)
                    if result.returncode != 0:
                        detail = (result.stderr or result.stdout).rstrip()
                        if detail:
                            print(detail, file=sys.stderr)
                        return False
                    lint_sources.append(generated)
                else:
                    lint_sources.append(source)
            command = ["verible-verilog-lint"]
            command.extend(verible_args)
            command.extend(lint_sources)
            return self.run(command, cwd=self.root).returncode == 0
        raise VWBError(f"unknown linter: {tool}")

    @staticmethod
    def _yosys_quote(value: str | Path) -> str:
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    @classmethod
    def _yosys_command(cls, arguments: Sequence[str | Path]) -> str:
        if not arguments:
            raise VWBError("cannot build an empty Yosys command")
        command = str(arguments[0])
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", command):
            raise VWBError(f"unsafe Yosys command name: {command}")

        rendered = [command]
        for item in arguments[1:]:
            text = str(item)
            if text and YOSYS_BARE_WORD_RE.fullmatch(text):
                rendered.append(text)
            else:
                rendered.append(cls._yosys_quote(text))
        return " ".join(rendered)

    @staticmethod
    def _tcl_quote(value: str | Path) -> str:
        text = str(value)
        if text and TCL_BARE_WORD_RE.fullmatch(text):
            return text
        escaped = (
            text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("$", "\\$")
            .replace("[", "\\[")
            .replace("]", "\\]")
            .replace("\r", "\\r")
            .replace("\n", "\\n")
        )
        return f'"{escaped}"'

    @classmethod
    def _yosys_tcl_command(cls, arguments: Sequence[str | Path]) -> str:
        return "yosys " + " ".join(cls._tcl_quote(item) for item in arguments)

    def _vhdl_to_verilog(
        self, module: str, output_dir: Path
    ) -> Path:
        module = self.catalog.definition(module).name
        self.require_tool("ghdl")
        work_library = output_dir / "ghdl-work"
        if not self.dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)
            work_library.mkdir(parents=True, exist_ok=True)
        sources = self.catalog.closure(module)
        if self.run(
            ["ghdl", "-i", "--std=08", f"--workdir={work_library}", *sources],
            cwd=self.root,
        ).returncode != 0:
            raise VWBError(f"GHDL analysis failed for {module}")
        if self.run(
            ["ghdl", "-m", "--std=08", f"--workdir={work_library}", module],
            cwd=self.root,
        ).returncode != 0:
            raise VWBError(f"GHDL elaboration failed for {module}")
        result = self.run(
            [
                "ghdl",
                "--synth",
                "--std=08",
                f"--workdir={work_library}",
                "--out=verilog",
                module,
            ],
            cwd=self.root,
            capture=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip()
            raise VWBError(
                f"GHDL synthesis conversion failed for {module}"
                + (f": {detail}" if detail else "")
            )
        output = output_dir / f"{artifact_component(module)}.v"
        if not self.dry_run:
            output.write_text(result.stdout, encoding="utf-8")
        return output

    def yosys_sources(
        self,
        module: str,
        output_dir: Path,
        defines: Sequence[str],
        includes: Sequence[str],
    ) -> list[Path]:
        definition = self.catalog.definition(module)
        module = definition.name
        if definition.language == "vhdl":
            return [self._vhdl_to_verilog(module, output_dir)]
        sources = self.catalog.closure(module)
        return self._convert_systemverilog(
            sources,
            output_dir,
            defines,
            self.include_dirs(includes),
            (),
        )

    def _yosys_read_sources_command(
        self,
        sources: Sequence[Path],
        defines: Sequence[str],
        includes: Sequence[str],
    ) -> str:
        arguments: list[str | Path] = ["read_verilog", "-sv"]
        for define in defines:
            arguments.append(f"-D{define}")
        for path in self.include_dirs(includes):
            arguments.append(f"-I{path}")
        arguments.extend(sources)
        return self._yosys_tcl_command(arguments)

    def gate_netlist(
        self,
        module: str,
        defines: Sequence[str],
        includes: Sequence[str],
    ) -> Path:
        module = self.catalog.definition(module).name
        yosys_top = require_yosys_identifier(module)
        self.require_tool("yosys")
        self.prepare_build_dir()
        output_dir = self.build_dir / "synth" / artifact_component(module) / "gate"
        if not self.dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)
        sources = self.yosys_sources(module, output_dir, defines, includes)
        netlist = output_dir / f"{artifact_component(module)}_gate.v"
        simlib = output_dir / "yosys_simlib.v"
        script = output_dir / "gate.ys"

        def gate_commands(
            target: Path, library_target: Path, *, coarse: bool
        ) -> list[str]:
            synth_arguments = ["synth", "-top", yosys_top]
            if coarse:
                synth_arguments[1:1] = ["-run", "begin:fine"]
            return [
                self._yosys_read_sources_command(sources, defines, includes),
                self._yosys_tcl_command(
                    ["hierarchy", "-check", "-top", yosys_top]
                ),
                self._yosys_tcl_command(synth_arguments),
                self._yosys_tcl_command(["write_verilog", "-noattr", target]),
                self._yosys_tcl_command(
                    ["write_file", library_target, "+/simlib.v"]
                ),
            ]

        commands = gate_commands(netlist, simlib, coarse=False)
        if not self.dry_run:
            script.write_text("\n".join(commands) + "\n", encoding="utf-8")
        mapped_result = self.run(["yosys", "-c", script], cwd=self.root)
        if self.dry_run:
            return netlist
        mapped_valid = (
            mapped_result.returncode == 0
            and netlist.is_file()
            and netlist.stat().st_size > 0
            and simlib.is_file()
            and simlib.stat().st_size > 0
        )
        use_coarse = (
            not mapped_valid
            or netlist.stat().st_size > FULL_GATE_NETLIST_LIMIT_BYTES
        )

        if use_coarse:
            message = (
                "mapped gate synthesis failed; trying word-level generic cells"
                if not mapped_valid
                else "mapped gate netlist is large; keeping word-level generic "
                "cells for simulation"
            )
            print(
                self.style(
                    "warning: " + message,
                    Ansi.BOLD,
                    Ansi.YELLOW,
                    stream=sys.stderr,
                ),
                file=sys.stderr,
            )
            coarse_netlist = output_dir / f".{artifact_component(module)}_coarse.tmp.v"
            coarse_simlib = output_dir / ".yosys_simlib.coarse.tmp.v"
            coarse_script = output_dir / "gate-coarse.ys"
            coarse_script.write_text(
                "\n".join(
                    gate_commands(coarse_netlist, coarse_simlib, coarse=True)
                )
                + "\n",
                encoding="utf-8",
            )
            result = self.run(["yosys", "-c", coarse_script], cwd=self.root)
            coarse_valid = (
                result.returncode == 0
                and coarse_netlist.is_file()
                and coarse_netlist.stat().st_size > 0
                and coarse_simlib.is_file()
                and coarse_simlib.stat().st_size > 0
            )
            if coarse_valid:
                os.replace(coarse_netlist, netlist)
                os.replace(coarse_simlib, simlib)
            else:
                for temporary in (coarse_netlist, coarse_simlib):
                    if temporary.exists() or temporary.is_symlink():
                        temporary.unlink()
                if not mapped_valid:
                    raise VWBError(f"gate netlist synthesis failed for {module}")
                print(
                    self.style(
                        "warning: word-level fallback failed; using the fully "
                        "mapped gate netlist",
                        Ansi.BOLD,
                        Ansi.YELLOW,
                        stream=sys.stderr,
                    ),
                    file=sys.stderr,
                )
        if not netlist.is_file() or netlist.stat().st_size == 0:
            raise VWBError(f"gate netlist was not produced: {netlist}")
        if not simlib.is_file() or simlib.stat().st_size == 0:
            raise VWBError(f"Yosys simulation library was not produced: {simlib}")
        return netlist

    def _yosys_read_command(
        self,
        module: str,
        defines: Sequence[str],
        includes: Sequence[str],
    ) -> str:
        return self._yosys_read_sources_command(
            self.catalog.closure(module), defines, includes
        )

    @staticmethod
    def _is_valid_svg(path: Path) -> bool:
        try:
            if path.stat().st_size == 0:
                return False
            root: ET.Element | None = None
            root_tag = ""
            for event, element in ET.iterparse(path, events=("start", "end")):
                if root is None and event == "start":
                    root = element
                    root_tag = element.tag.rsplit("}", 1)[-1].lower()
                if event == "end":
                    element.clear()
                    # ElementTree otherwise retains every completed SVG node
                    # under the root. Large schematics must stay stream-sized.
                    if root is not None and element is not root:
                        root.clear()
        except (OSError, ET.ParseError):
            return False
        return root_tag == "svg"

    @staticmethod
    def _svg_root_attributes(path: Path) -> dict[str, str] | None:
        parser = ET.XMLPullParser(events=("start",))
        try:
            with path.open("rb") as source:
                while chunk := source.read(64 * 1024):
                    parser.feed(chunk)
                    for _event, element in parser.read_events():
                        if element.tag.rsplit("}", 1)[-1].lower() != "svg":
                            return None
                        return dict(element.attrib)
        except (OSError, ET.ParseError):
            return None
        return None

    @staticmethod
    def _svg_dimensions(path: Path) -> tuple[float, float] | None:
        attributes = Workbench._svg_root_attributes(path)
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
            if match is None:
                return None
            unit = match.group(3).lower()
            if unit not in unit_scale:
                return None
            number = float(match.group(1))
            if match.group(2):
                number *= 10 ** int(match.group(2))
            return number * unit_scale[unit]

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

    @classmethod
    def _png_would_exceed_limit(cls, svg_path: Path) -> bool:
        dimensions = cls._svg_dimensions(svg_path)
        if dimensions is None:
            return False
        width, height = dimensions
        return width * height * 4.0 > MAX_PNG_PIXELS

    def synthesize(self, module: str, args: argparse.Namespace) -> Path:
        module = self.catalog.definition(module).name
        yosys_top = require_yosys_identifier(module)
        self.require_tool("yosys")
        self.prepare_build_dir()
        output_name = artifact_component(module)
        output_dir = self.build_dir / "synth" / output_name
        if not self.dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / f"{output_name}.json"
        script_path = output_dir / "synth.tcl"
        sources = self.yosys_sources(
            module, output_dir, args.define, args.include
        )
        commands = [
            self._yosys_read_sources_command(sources, args.define, args.include),
            self._yosys_tcl_command(["hierarchy", "-check", "-top", yosys_top]),
        ]
        if args.schematic and args.full:
            commands.append(self._yosys_tcl_command(["prep", "-flatten"]))
        elif args.schematic:
            commands.append(self._yosys_tcl_command(["prep"]))
        elif args.full:
            commands.append(self._yosys_tcl_command(["synth", "-top", yosys_top]))
        else:
            commands.extend(
                [
                    self._yosys_tcl_command(["proc"]),
                    self._yosys_tcl_command(["opt", "-full"]),
                ]
            )
        if args.flatten and not (args.schematic and args.full):
            commands.extend(
                [
                    self._yosys_tcl_command(["flatten"]),
                    self._yosys_tcl_command(["opt_clean"]),
                ]
            )
        commands.append(self._yosys_tcl_command(["write_json", json_path]))

        if not self.dry_run:
            script_path.write_text("\n".join(commands) + "\n", encoding="utf-8")
        result = self.run(["yosys", "-c", script_path], cwd=self.root)
        if result.returncode != 0:
            raise VWBError(f"Yosys synthesis failed for {module}")

        artifact = json_path
        prefix = output_dir / output_name
        render_json_path = json_path

        def render_with_sfdp(dot_path: Path, target: Path) -> bool:
            try:
                self.require_tool("sfdp")
            except VWBError:
                return False
            scalable = self.run(
                ["sfdp", "-Tsvg", "-o", target, dot_path],
                cwd=output_dir,
                timeout=120,
            )
            valid = scalable.returncode == 0 and self._is_valid_svg(target)
            if not valid and not self.dry_run and target.exists():
                target.unlink()
            return valid

        def render_with_dot(dot_path: Path, target: Path) -> bool:
            try:
                self.require_tool("dot")
            except VWBError:
                return False
            conventional = self.run(
                ["dot", "-Tsvg", "-o", target, dot_path],
                cwd=output_dir,
                timeout=120,
            )
            valid = conventional.returncode == 0 and self._is_valid_svg(target)
            if not valid and not self.dry_run and target.exists():
                target.unlink()
            return valid

        def render_with_yosys(output_format: str) -> Path:
            target = prefix.with_suffix(f".{output_format}")
            if not self.dry_run and (target.exists() or target.is_symlink()):
                target.unlink()
            if (
                output_format == "svg"
                and not self.dry_run
                and render_json_path.stat().st_size
                > SCALABLE_LAYOUT_JSON_LIMIT_BYTES
            ):
                print(
                    self.style(
                        "warning: Yosys graph is large; using the scalable sfdp "
                        "layout",
                        Ansi.BOLD,
                        Ansi.YELLOW,
                        stream=sys.stderr,
                    ),
                    file=sys.stderr,
                )
                dot_path = render_with_yosys("dot")
                if not render_with_sfdp(dot_path, target) and not render_with_dot(
                    dot_path, target
                ):
                    raise VWBError(
                        f"visual rendering failed for {module}; synthesis JSON "
                        f"remains at {json_path}"
                    )
                return target
            if output_format in {"svg", "png"}:
                self.require_tool("dot")
            render_script = output_dir / "render.ys"
            show_options = [
                "show",
                "-format",
                output_format,
                "-viewer",
                "none",
                "-prefix",
                prefix.name,
                "-colors",
                "2",
                "-width",
                "-signed",
            ]
            if output_format == "dot":
                show_options.append("-long")
            show_options.append(yosys_top)
            render_commands = [
                self._yosys_command(["read_json", render_json_path.name]),
                self._yosys_command(show_options),
            ]
            if not self.dry_run:
                render_script.write_text(
                    ";\n".join(render_commands) + ";\n", encoding="utf-8"
                )
            render_result = self.run(
                ["yosys", "-s", render_script], cwd=output_dir, timeout=120
            )
            valid_render = render_result.returncode == 0 and (
                self.dry_run or target.is_file()
            ) and (
                output_format != "svg"
                or self.dry_run
                or self._is_valid_svg(target)
            )
            if not valid_render and output_format == "svg" and not self.dry_run:
                print(
                    self.style(
                        "warning: Graphviz dot layout failed; trying the scalable "
                        "sfdp layout",
                        Ansi.BOLD,
                        Ansi.YELLOW,
                        stream=sys.stderr,
                    ),
                    file=sys.stderr,
                )
                dot_path = render_with_yosys("dot")
                valid_render = render_with_sfdp(dot_path, target)
            if not valid_render:
                raise VWBError(
                    f"visual rendering failed for {module}; synthesis JSON remains at {json_path}"
                )
            return target

        if args.format == "dot":
            artifact = render_with_yosys("dot")
        elif args.format in {"svg", "png"}:
            svg_path = output_dir / f"{output_name}.svg"
            rendered = False
            if args.schematic:
                try:
                    self.require_tool("netlistsvg")
                except VWBError:
                    rendered = False
                else:
                    temporary_svg = output_dir / f".{output_name}.netlistsvg.tmp"
                    if not self.dry_run and temporary_svg.exists():
                        temporary_svg.unlink()
                    netlist_result = self.run(
                        ["netlistsvg", render_json_path, "-o", temporary_svg],
                        cwd=self.root,
                        timeout=120,
                    )
                    rendered = netlist_result.returncode == 0 and (
                        self.dry_run or self._is_valid_svg(temporary_svg)
                    )
                    if rendered and not self.dry_run:
                        os.replace(temporary_svg, svg_path)
                    elif not self.dry_run and temporary_svg.exists():
                        temporary_svg.unlink()
            if not rendered:
                if args.schematic:
                    print(
                        self.style(
                            "warning: NetlistSVG could not render this design; "
                            "using the Yosys schematic instead",
                            Ansi.BOLD,
                            Ansi.YELLOW,
                            stream=sys.stderr,
                        ),
                        file=sys.stderr,
                    )
                svg_path = render_with_yosys("svg")
            artifact = svg_path
            if args.format == "png":
                png_path = output_dir / f"{output_name}.png"
                temporary_png = output_dir / f".{output_name}.png.tmp"
                if not self.dry_run:
                    if temporary_png.exists():
                        temporary_png.unlink()
                    if png_path.exists():
                        png_path.unlink()
                if not self.dry_run and self._png_would_exceed_limit(svg_path):
                    print(
                        self.style(
                            "warning: a full-density PNG would exceed 16 "
                            "megapixels; keeping the SVG instead",
                            Ansi.BOLD,
                            Ansi.YELLOW,
                            stream=sys.stderr,
                        ),
                        file=sys.stderr,
                    )
                else:
                    self.require_tool("rsvg-convert")
                    raster_result = self.run(
                        [
                            "rsvg-convert",
                            "--format", "png",
                            "--zoom", "2",
                            "--background-color", "white",
                            "--unlimited",
                            "--output", temporary_png,
                            svg_path,
                        ],
                        cwd=self.root,
                        timeout=120,
                    )
                    if raster_result.returncode != 0 or (
                        not self.dry_run and not temporary_png.is_file()
                    ):
                        raise VWBError(f"SVG rasterization failed for {module}")
                    if not self.dry_run:
                        os.replace(temporary_png, png_path)
                    artifact = png_path

        if not self.dry_run and not artifact.is_file():
            raise VWBError(f"synthesis artifact was not produced: {artifact}")
        viewer = args.view.strip() if args.view else "auto"
        if viewer.lower() == "auto":
            viewer = {
                ".png": "geeqie",
                ".svg": "inkscape",
            }.get(artifact.suffix.lower(), "none")
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
        module = self.catalog.definition(module).name
        yosys_top = require_yosys_identifier(module)
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
        sources = self.yosys_sources(module, output, defines, args.include)
        design_json = output / f"{output_name}.json"
        script = output / "fpga.tcl"
        synth_command = self._yosys_tcl_command(
            [
                "synth_gowin" if board == "gowin" else "synth_ice40",
                "-top",
                yosys_top,
                "-json",
                design_json.name,
            ]
        )
        if not self.dry_run:
            script.write_text(
                "\n".join(
                    [
                        self._yosys_read_sources_command(
                            sources, defines, args.include
                        ),
                        self._yosys_tcl_command(
                            ["hierarchy", "-check", "-top", yosys_top]
                        ),
                        synth_command,
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
        if self.run(["yosys", "-c", script], cwd=output).returncode != 0:
            raise VWBError(f"FPGA synthesis failed for {module}")
        artifact: Path = design_json
        if requested_index == 0:
            return artifact

        if board == "gowin":
            pnr_variant, pnr_tool = self.require_tool_choice("nextpnr-gowin")
            pnr_json = output / f"{output_name}-pnr.json"
            command: list[str | Path] = [
                pnr_tool,
                "--json",
                design_json,
                "--write",
                pnr_json,
                "--device",
                "GW1NR-LV9QN88PC6/I5",
            ]
            if "himbaechel" in pnr_variant:
                if pnr_variant == "nextpnr-himbaechel":
                    command.extend(["--uarch", "gowin"])
                command.extend(
                    [
                        "--vopt",
                        "family=GW1N-9C",
                        "--vopt",
                        f"cst={constraints}",
                    ]
                )
            else:
                command.extend(
                    ["--family", "GW1N-9C", "--cst", constraints]
                )
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

    def _clean_simulation_temporaries(self, target: Path) -> None:
        paths = sorted(
            target.rglob("*"),
            key=lambda path: (len(path.parts), str(path)),
            reverse=True,
        )
        for path in paths:
            if path.is_symlink():
                if self.verbose or self.dry_run:
                    print(
                        self.style("unlink", Ansi.BOLD, Ansi.YELLOW),
                        self.style(path, Ansi.DIM),
                    )
                if not self.dry_run:
                    path.unlink()
                continue
            if path.is_file():
                if path.suffix.lower() == ".gtkw":
                    continue
                if self.verbose or self.dry_run:
                    print(
                        self.style("remove", Ansi.BOLD, Ansi.YELLOW),
                        self.style(path, Ansi.DIM),
                    )
                if not self.dry_run:
                    path.unlink()
                continue
            if path.is_dir() and not self.dry_run:
                try:
                    path.rmdir()
                except OSError:
                    pass
        if not self.dry_run:
            try:
                target.rmdir()
            except OSError:
                pass

    def clean(self, scope: str) -> None:
        individual_targets = {
            "sim": self.build_dir / "sim",
            "waves": self.saved_waves_dir,
            "synth": self.build_dir / "synth",
            "lint": self.build_dir / "lint",
            "fpga": self.build_dir / "fpga",
            "formal": self.build_dir / "formal",
            "all": self.build_dir,
        }
        target_paths = (
            [
                individual_targets["sim"],
                individual_targets["lint"],
            ]
            if scope == "temp"
            else [individual_targets[scope]]
        )
        build = self.build_dir.resolve()
        self._validate_build_location()
        marker = build / BUILD_MARKER
        if not build.exists():
            return
        if not marker.is_file() or marker.is_symlink():
            raise VWBError(f"refusing to clean a directory not owned by vwb.py: {build}")
        self._validate_build_marker(marker, build)
        for target_path in target_paths:
            if target_path.is_symlink():
                if self.verbose or self.dry_run:
                    print(
                        self.style("unlink", Ansi.BOLD, Ansi.YELLOW),
                        self.style(target_path, Ansi.DIM),
                    )
                if not self.dry_run:
                    target_path.unlink()
                continue
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
            if scope == "temp" and target_path == individual_targets["sim"]:
                if target.exists():
                    self._clean_simulation_temporaries(target)
                continue
            if target.exists() and not self.dry_run:
                shutil.rmtree(target)


def module_name_completer(
    prefix: str, parsed_args: argparse.Namespace, **_: object
) -> list[str]:
    try:
        settings = resolve_project_settings(parsed_args)
        source = project_path(settings.root, settings.src_dir)
        names = SourceCatalog(find_hdl_files(source)).names()
    except (OSError, VWBError):
        return []
    folded_prefix = prefix.casefold()
    return [name for name in names if name.casefold().startswith(folded_prefix)]


def saved_wave_completer(
    prefix: str, parsed_args: argparse.Namespace, **_: object
) -> list[str]:
    try:
        settings = resolve_project_settings(parsed_args)
        directory = project_path(settings.root, settings.build_dir) / "saved-waves"
        if directory.is_symlink() or not directory.is_dir():
            return []
        return [
            path.name
            for path in sorted(directory.iterdir())
            if path.is_dir() and not path.is_symlink() and path.name.startswith(prefix)
        ]
    except (OSError, VWBError):
        return []


def add_simulation_options(
    parser: argparse.ArgumentParser, *, gate_level_default: bool
) -> None:
    modules = parser.add_argument("modules", nargs="*", metavar="MODULE")
    modules.completer = module_name_completer  # type: ignore[attr-defined]
    parser.add_argument("--test", help="explicit Cocotb, Verilog, or VHDL test file")
    parser.add_argument(
        "--test-language",
        choices=["auto", "cocotb", "verilog", "vhdl"],
        default="auto",
        help="select Cocotb, Verilog, or VHDL testbenches",
    )
    parser.add_argument("--test-top", help="top unit declared by an HDL testbench")
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
    if gate_level_default:
        parser.add_argument(
            "--no-gate-level",
            dest="gate_level",
            action="store_false",
            default=True,
            help="skip the default post-synthesis functional simulation",
        )
    else:
        parser.add_argument(
            "--gate-level",
            dest="gate_level",
            action="store_true",
            default=False,
            help="also run post-synthesis functional simulation",
        )
    parser.add_argument("--keep-going", action="store_true", help=argparse.SUPPRESS)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vwb.py",
        description="Discover, test, lint, synthesize, and build HDL projects.",
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
    add_simulation_options(test_parser, gate_level_default=True)

    wave_parser = subparsers.add_parser(
        "wave", aliases=["gtkwave"], help="run, open, and manage waveforms"
    )
    add_simulation_options(wave_parser, gate_level_default=False)
    wave_parser.set_defaults(waves=True)
    wave_parser.add_argument("--save", help="explicit GTKWave save file")
    wave_tag = wave_parser.add_argument(
        "--tag", help="archive a passing waveform with this tag"
    )
    wave_tag.completer = saved_wave_completer  # type: ignore[attr-defined]
    wave_parser.add_argument("--replace-tag", action="store_true")
    wave_load = wave_parser.add_argument(
        "--load", metavar="TAG", help="open an archived waveform"
    )
    wave_load.completer = saved_wave_completer  # type: ignore[attr-defined]
    wave_parser.add_argument(
        "--list-saved", action="store_true", help="list archived waveforms"
    )
    wave_parser.add_argument("--json", action="store_true", dest="as_json")

    lint_parser = subparsers.add_parser(
        "lint", help="lint selected module hierarchies"
    )
    lint_modules = lint_parser.add_argument("modules", nargs="*", metavar="MODULE")
    lint_modules.completer = module_name_completer  # type: ignore[attr-defined]
    lint_parser.add_argument("--all", action="store_true", dest="all_modules")
    lint_parser.add_argument(
        "--linter",
        action="append",
        choices=["all", "iverilog", "verilator", "yosys", "verible", "ghdl"],
        default=[],
        help="linter to run; repeat for several (default: all applicable tools)",
    )
    lint_parser.add_argument("--keep-going", action="store_true", help=argparse.SUPPRESS)
    lint_parser.add_argument("-D", "--define", action="append", default=[])
    lint_parser.add_argument("-I", "--include", action="append", default=[])
    lint_parser.add_argument("--iverilog-arg", action="append", default=[])
    lint_parser.add_argument("--verilator-arg", action="append", default=[])
    lint_parser.add_argument(
        "--lint-arg",
        dest="verilator_arg",
        action="append",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    lint_parser.add_argument("--yosys-arg", action="append", default=[])
    lint_parser.add_argument("--verible-arg", action="append", default=[])
    lint_parser.add_argument("--ghdl-arg", action="append", default=[])

    synth_parser = subparsers.add_parser(
        "synth", help="synthesize and render a module"
    )
    synth_module = synth_parser.add_argument("module", nargs="?")
    synth_module.completer = module_name_completer  # type: ignore[attr-defined]
    synth_parser.add_argument(
        "--format",
        choices=["json", "svg", "png", "dot"],
        default="png",
        help="preferred synthesis artifact format",
    )
    synth_parser.add_argument(
        "--full", action="store_true", help="use the Makefile's full preparation flow"
    )
    synth_parser.add_argument("--flatten", action="store_true", help="flatten hierarchy")
    synth_parser.add_argument(
        "--schematic",
        dest="schematic",
        action="store_true",
        default=True,
        help="try netlistsvg before the Yosys fallback",
    )
    synth_parser.add_argument(
        "--no-schematic",
        dest="schematic",
        action="store_false",
        default=argparse.SUPPRESS,
        help="render images through Yosys show instead of netlistsvg",
    )
    synth_parser.add_argument(
        "--view",
        default="auto",
        metavar="VIEWER",
        help="artifact viewer, 'auto', or 'none'",
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
    fpga_module = fpga_parser.add_argument("module", nargs="?")
    fpga_module.completer = module_name_completer  # type: ignore[attr-defined]
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
        choices=["temp", "sim", "waves", "synth", "lint", "fpga", "formal", "all"],
        default="temp",
        help="what to remove; plain clean preserves synthesis and saved waves",
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
        ("gate_level", "--gate-level"),
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
                "language": "systemverilog",
                "files": [
                    display_path(path, workbench.root)
                    for path in workbench.catalog.packages[name]
                ],
            }
            for name in sorted(workbench.catalog.packages)
        ]
        + [
            {
                "name": workbench.catalog.vhdl_package_names[name],
                "language": "vhdl",
                "files": [
                    display_path(path, workbench.root)
                    for path in unique_paths(
                        [
                            *workbench.catalog.vhdl_packages[name],
                            *workbench.catalog.vhdl_package_bodies.get(name, []),
                        ]
                    )
                ],
            }
            for name in sorted(workbench.catalog.vhdl_packages)
        ],
        "modules": [
            {
                "name": name,
                "language": workbench.catalog.definition(name).language,
                "files": [
                    display_path(path, workbench.root)
                    for path in workbench.catalog.implementation_files(name)
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
            language = workbench.style(f"[{package['language']}]", Ansi.MAGENTA)
            print(f"  {name} {language} {files}")
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
    requested_tools = list(dict.fromkeys(args.linter or ["all"]))
    checks: list[tuple[str, str]] = []
    for module in modules:
        language = workbench.catalog.definition(module).language
        if "all" in requested_tools:
            tools = (
                ["ghdl", "iverilog", "verilator", "yosys"]
                if language == "vhdl"
                else ["iverilog", "verilator", "yosys", "verible"]
            )
            tools.extend(tool for tool in requested_tools if tool != "all")
        else:
            tools = requested_tools
        checks.extend((module, tool) for tool in dict.fromkeys(tools))
    passed = 0
    failures: list[tuple[str, str, str]] = []
    for index, (module, tool) in enumerate(checks, start=1):
        print(
            workbench.style("==>", Ansi.BOLD, Ansi.CYAN)
            + f" [{index}/{len(checks)}] lint "
            + workbench.style(module, Ansi.BOLD)
            + f" with {tool}"
        )
        reason = "tool reported errors"
        try:
            ok = workbench.lint_with_tool(
                module,
                tool,
                args.define,
                args.include,
                iverilog_args=args.iverilog_arg,
                verilator_args=args.verilator_arg,
                yosys_args=args.yosys_arg,
                verible_args=args.verible_arg,
                ghdl_args=args.ghdl_arg,
            )
        except VWBError as exc:
            ok = False
            reason = str(exc)
            print(f"error: {exc}", file=sys.stderr)
        print(
            workbench.style(
                "PASS" if ok else "FAIL",
                Ansi.BOLD,
                Ansi.GREEN if ok else Ansi.RED,
            )
        )
        passed += int(ok)
        if not ok:
            failures.append((module, tool, reason))
    print(
        workbench.style("==>", Ansi.BOLD, Ansi.CYAN)
        + " "
        + workbench.style(
            f"{passed}/{len(checks)} lint checks passed",
            Ansi.GREEN if passed == len(checks) else Ansi.RED,
        )
    )
    if failures:
        print(workbench.style("Failed lint checks:", Ansi.BOLD, Ansi.RED))
        for module, tool, reason in failures:
            print(f"  {module}: {tool}: {reason}")
    return 0 if passed == len(checks) else 1


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
        "simulation": ["iverilog", "vvp", "ghdl", "sv2v", "cocotb-config"],
        "waveform": ["gtkwave"],
        "lint": [
            "iverilog",
            "verilator",
            "yosys",
            "verible-verilog-lint",
            "ghdl",
        ],
        "synthesis": [
            "yosys",
            "dot",
            "sfdp",
            "netlistsvg",
            "rsvg-convert",
            "geeqie",
            "inkscape",
        ],
        "completion": ["register-python-argcomplete"],
        "formal": ["sby"],
        "gowin": ["nextpnr-gowin", "gowin_pack", "openFPGALoader"],
        "ice40": ["nextpnr-ice40", "icepack", "openFPGALoader"],
    }
    data = {
        group: {command: find_tool(command) for command in commands}
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
    required.append(data["synthesis"]["yosys"])
    if workbench.catalog.names():
        required.append(data["simulation"]["cocotb-config"])
    if any(
        workbench.catalog.definition(name).language == "vhdl"
        for name in workbench.catalog.names()
    ):
        required.append(data["simulation"]["ghdl"])
    if any(path.suffix.lower() == ".sv" for path in workbench.catalog.files):
        required.append(data["simulation"]["sv2v"])
    return 0 if all(required) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = make_parser()
    try:
        import argcomplete
    except ImportError:
        pass
    else:
        argcomplete.autocomplete(parser)
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
