"""Native tool discovery. Discovery is not evidence that a workflow works.

This module uses the standard library only; it is safe to import in the minimal
companion before any HDL tool package has been installed.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
from typing import Mapping

from .process import run_process

SCHEMA_VERSION = "vwb.toolchains/1"

# Tool alternatives mirror VWB's nextpnr fallback, not a hypothetical tool name.
TOOLS = {
    "iverilog": (("iverilog",), ("-V",)),
    "vvp": (("vvp",), ("-V",)),
    "verilator": (("verilator", "verilator_bin"), ("--version",)),
    "make": (("make",), ("--version",)),
    "c++": (("g++", "clang++", "c++"), ("--version",)),
    "ghdl": (("ghdl",), ("--version",)),
    "cocotb-config": (("cocotb-config",), ("--version",)),
    "sv2v": (("sv2v",), ("--version",)),
    "yosys": (("yosys",), ("-V",)),
    "dot": (("dot",), ("-V",)),
    "netlistsvg": (("netlistsvg",), ("--version",)),
    "sby": (("sby",), ("--version",)),
    "z3": (("z3",), ("--version",)),
    "nextpnr-gowin": (("nextpnr-gowin", "nextpnr-himbaechel-gowin", "nextpnr-himbaechel"), ("--version",)),
    "gowin_pack": (("gowin_pack",), ("--version",)),
    "nextpnr-ice40": (("nextpnr-ice40",), ("--version",)),
    "icepack": (("icepack",), ("-h",)),
    "openFPGALoader": (("openFPGALoader",), ("--Version",)),
}

WORKFLOW_TOOLS = {
    "verilog_simulation": ("iverilog", "vvp"),
    "systemverilog_simulation": ("iverilog", "vvp"),
    "verilator_simulation": ("verilator", "make", "c++"),
    "cocotb_verilator": ("verilator", "make", "c++", "cocotb-config"),
    "waveforms": ("iverilog", "vvp"),
    "cocotb_verilog": ("iverilog", "vvp", "cocotb-config"),
    "vhdl_simulation": ("ghdl",),
    "cocotb_vhdl": ("ghdl", "cocotb-config"),
    "lint": ("verilator",),
    "synthesis": ("yosys",),
    "schematics": ("yosys", "dot"),
    "vhdl_synthesis": ("ghdl", "yosys"),
    "formal_proof": ("sby", "yosys", "z3"),
    "formal_counterexample": ("sby", "yosys", "z3"),
    "fpga_tangnano9k_build": ("yosys", "nextpnr-gowin", "gowin_pack"),
    "fpga_icebreaker_build": ("yosys", "nextpnr-ice40", "icepack"),
    "fpga_tangnano9k_programming": ("openFPGALoader",),
    "fpga_icebreaker_programming": ("openFPGALoader",),
}


def normalize_arch(machine: str) -> str:
    return {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine.lower(), machine.lower())


def host_platform() -> dict:
    system = platform.system().lower()
    arch = normalize_arch(platform.machine())
    detection = "platform.machine"
    if system == "windows":
        arch = normalize_arch(os.environ.get("PROCESSOR_ARCHITEW6432", os.environ.get("PROCESSOR_ARCHITECTURE", arch)))
        detection = "Windows processor environment (fallback)"
        # An emulated process can receive x64 from ordinary processor queries.
        # IsWow64Process2 reports the native machine, including Windows ARM64.
        try:
            import ctypes
            from ctypes import wintypes
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.GetCurrentProcess.restype = wintypes.HANDLE
            api.IsWow64Process2.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.WORD)]
            api.IsWow64Process2.restype = wintypes.BOOL
            process_machine, native_machine = wintypes.WORD(), wintypes.WORD()
            if api.IsWow64Process2(api.GetCurrentProcess(), ctypes.byref(process_machine), ctypes.byref(native_machine)):
                arch = {0x8664: "x64", 0xaa64: "arm64", 0x14c: "x86"}.get(native_machine.value, arch)
                detection = "IsWow64Process2 native machine"
        except (OSError, AttributeError):
            pass
    elif system == "darwin" and arch == "x64":
        try:
            translated = subprocess.run(["/usr/sbin/sysctl", "-n", "sysctl.proc_translated"], capture_output=True, text=True, timeout=2)
            if translated.returncode == 0 and translated.stdout.strip() == "1":
                arch, detection = "arm64", "sysctl.proc_translated"
        except (OSError, subprocess.TimeoutExpired):
            pass
    distribution = {}
    if system == "linux" and hasattr(platform, "freedesktop_os_release"):
        try:
            distribution = platform.freedesktop_os_release()
        except OSError:
            pass
    return {"target": f"{system}-{arch}", "system": system, "architecture": arch,
            "architecture_detection": detection, "release": platform.release(), "version": platform.version(),
            "distribution": distribution}


def binary_architecture(path: Path) -> dict:
    """Inspect ELF/PE/Mach-O headers without assuming PATH tools are native.

    A launcher script is labelled a script, not the architecture of the Python
    interpreter running the companion. Fat Mach-O lists all contained slices.
    """
    try:
        with path.resolve().open("rb") as stream:
            head = stream.read(4096)
            if head.startswith(b"\x7fELF") and len(head) >= 20:
                endian = "<" if head[5] == 1 else ">"
                cpu = struct.unpack_from(endian + "H", head, 18)[0]
                return {"format": "elf", "architectures": [{62: "x64", 183: "arm64", 3: "x86", 40: "arm"}.get(cpu, f"elf-{cpu}")]}
            if head[:2] == b"MZ" and len(head) >= 64:
                offset = struct.unpack_from("<I", head, 60)[0]
                stream.seek(offset)
                pe = stream.read(6)
                if pe[:4] == b"PE\0\0":
                    cpu = struct.unpack_from("<H", pe, 4)[0]
                    return {"format": "pe", "architectures": [{0x8664: "x64", 0xaa64: "arm64", 0x14c: "x86", 0xa641: "arm64ec"}.get(cpu, f"pe-{cpu}")]}
            magic = head[:4]
            if magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce") and len(head) >= 8:
                endian = "<" if magic[0] in (0xcf, 0xce) else ">"
                cpu = struct.unpack_from(endian + "I", head, 4)[0]
                return {"format": "mach-o", "architectures": [{0x01000007: "x64", 0x0100000c: "arm64"}.get(cpu, f"macho-{cpu}")]}
            if magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf") and len(head) >= 8:
                count = min(struct.unpack_from(">I", head, 4)[0], 64)
                width = 32 if magic[-1] == 0xbf else 20
                cpus = [struct.unpack_from(">I", head, 8 + i * width)[0] for i in range(count) if 12 + i * width <= len(head)]
                return {"format": "mach-o-universal", "architectures": [{0x01000007: "x64", 0x0100000c: "arm64"}.get(cpu, f"macho-{cpu}") for cpu in cpus]}
            if head.startswith(b"#!"):
                return {"format": "script", "architectures": [], "interpreter": head.splitlines()[0][2:].decode("utf-8", errors="replace")}
    except (OSError, ValueError, struct.error) as exc:
        return {"format": "unknown", "architectures": [], "error": str(exc)}
    return {"format": "unknown", "architectures": []}


def compatibility_route(binary: dict, host: dict) -> dict:
    architectures = binary.get("architectures", [])
    if host["architecture"] in architectures:
        return {"mechanism": "native", "verified": False, "note": "Header matches host; real workflow probe still required."}
    if host["architecture"] == "arm64" and "x64" in architectures:
        mechanism = {"darwin": "rosetta-2", "windows": "windows-11-x64-emulation"}.get(host["system"])
        if mechanism:
            return {"mechanism": mechanism, "verified": False, "note": "OS-provided translation candidate; runtime/library and device-driver tests required."}
        return {"mechanism": "unsupported-architecture", "verified": False, "note": "No OS-provided Linux x64-on-ARM route is assumed."}
    return {"mechanism": "unknown", "verified": False, "note": "Launcher or binary architecture needs runtime-set validation."}


def wrapper_payload(path: Path) -> dict | None:
    """Inspect the literal OSS suite wrapper payload without evaluating shell.

    Generic scripts stay unknown. This identifies only the published suite
    wrapper convention, and reports the payload path/header for independent
    review. A Python/Perl payload remains a script rather than guessed native.
    """
    try:
        script = path.read_bytes()[:8192]
        token = f"/libexec/{path.name}".encode("utf-8")
        if b"release_topdir_abs" in script and token in script:
            payload = path.parent.parent / "libexec" / path.name
            if payload.is_file():
                return {"path": str(payload), "binary": binary_architecture(payload),
                        "resolution": "literal-oss-suite-wrapper-target"}
    except OSError:
        pass
    return None


def capability_report(env: Mapping[str, str] | None = None, *, probe_versions: bool = True) -> dict:
    """Return JSON-ready discovery, never a claimed workflow pass.

    ``env`` is the full activated environment for a managed suite, when used.
    The API can use ``probe_versions=False`` for fast connection diagnostics.
    """
    environment = dict(os.environ if env is None else env)
    host = host_platform()
    discovered = {}
    for name, (alternatives, version_args) in TOOLS.items():
        path = next((candidate for alias in alternatives if (candidate := shutil.which(alias, path=environment.get("PATH", "")))), None)
        tool = {"state": "available" if path else "missing", "path": path, "version": None,
                "version_state": "not_run", "alternatives": list(alternatives)}
        if path:
            binary = binary_architecture(Path(path))
            tool["binary"] = binary
            payload = wrapper_payload(Path(path)) if binary["format"] == "script" else None
            if payload:
                tool["payload"] = payload
            tool["compatibility"] = compatibility_route(payload["binary"] if payload else binary, host)
            if probe_versions:
                try:
                    result = run_process([path, *version_args], env=environment, timeout=6, max_output_bytes=16384)
                    lines = ((result.stdout or "") + "\n" + (result.stderr or "")).strip().splitlines()
                    tool.update(version="\n".join(lines[:6])[:1500] or None,
                                version_state="error" if getattr(result, "timed_out", False) else "reported" if result.returncode == 0 and lines else "unavailable",
                                version_returncode=result.returncode)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    tool.update(version_state="error", version_error=str(exc))
        discovered[name] = tool
    workflows = {}
    for name, required in WORKFLOW_TOOLS.items():
        missing = [tool for tool in required if discovered[tool]["state"] == "missing"]
        workflows[name] = {"state": "blocked" if missing else "not_run", "required_tools": list(required),
                           "missing_tools": missing, "evidence": "discovery-only"}
        if name.endswith("programming"):
            workflows[name]["note"] = "Physical board, cable, native driver/permissions and explicit programming test required."
    return {"schema_version": SCHEMA_VERSION, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "mode": "discovery", "platform": host,
            "required_targets": ["linux-x64", "windows-x64", "darwin-arm64", "darwin-x64", "linux-arm64", "windows-arm64"],
            "runtime": {"python": sys.version, "executable": sys.executable,
                        "architecture": normalize_arch(platform.machine()), "binary": binary_architecture(Path(sys.executable))},
            "tools": discovered, "workflows": workflows}
