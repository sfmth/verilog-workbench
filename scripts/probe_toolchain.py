#!/usr/bin/env python3
"""Discover native engines, or run real M0 fixtures through the existing CLI.

No install, downloads, board flashing or dry-run successes are performed here.
An execution report with missing engines exits 1. See docs/M0_TOOLCHAINS.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from companion.process import run_process
from companion.toolchains import WORKFLOW_TOOLS, capability_report

FIXTURES = REPO / "toolchains" / "fixtures"
SMOKE_WORKFLOWS = tuple(name for name in WORKFLOW_TOOLS if not name.endswith("programming"))


def command_for(name: str) -> list[str]:
    native = ["test", "m0_gate", "--test", "test/test_m0_gate.v", "--test-language", "verilog", "--test-top", "tb_m0_gate"]
    if name == "verilog_simulation":
        return native
    if name == "waveforms":
        return [*native, "--waves", "--wave-format", "vcd"]
    if name == "systemverilog_simulation":
        return ["test", "m0_sv", "--test", "test/test_m0_sv.sv", "--test-language", "verilog", "--test-top", "tb_m0_sv"]
    if name.startswith("cocotb_"):
        top = "m0_vhdl" if name.endswith("vhdl") else "m0_gate"
        return ["test", top, "--test", "test/test_m0_cocotb.py", "--test-language", "cocotb", "--waves", "--wave-format", "vcd"]
    if name == "vhdl_simulation":
        return ["test", "m0_vhdl", "--test", "test/test_m0_vhdl.vhd", "--test-language", "vhdl", "--test-top", "tb_m0_vhdl", "--waves", "--wave-format", "vcd"]
    if name == "lint":
        return ["lint", "m0_gate", "--linter", "verilator"]
    if name in ("synthesis", "vhdl_synthesis", "schematics"):
        top = "m0_vhdl" if name == "vhdl_synthesis" else "m0_gate"
        return ["synth", top, "--format", "svg" if name == "schematics" else "json", "--no-schematic", "--no-view"]
    if name.startswith("formal_"):
        return ["formal", "formal/good.sby" if name == "formal_proof" else "formal/bad.sby"]
    if name.startswith("fpga_"):
        board = "tangnano9k" if "tangnano9k" in name else "icebreaker"
        return ["fpga", "m0_fpga", "--board", board, "--stage", "pack"]
    raise ValueError(f"no execution fixture for {name}")


def inspect_result(name: str, root: Path, returncode: int, log: str) -> tuple[bool, str]:
    build = root / "build"
    if name == "formal_counterexample":
        statuses = list(build.rglob("status"))
        failure = any(path.read_text(encoding="utf-8").split()[:1] == ["FAIL"] for path in statuses)
        traces = [path for path in build.rglob("*.vcd") if path.stat().st_size > 0]
        return returncode != 0 and failure and bool(traces), "Expected SBY FAIL and nonempty counterexample VCD."
    if returncode != 0:
        return False, "VWB command returned a nonzero status."
    if name == "formal_proof":
        statuses = list(build.rglob("status"))
        return any(path.read_text(encoding="utf-8").split()[:1] == ["PASS"] for path in statuses), "Expected SBY PASS status file."
    if name in ("verilog_simulation", "systemverilog_simulation", "vhdl_simulation", "waveforms"):
        marker = {"systemverilog_simulation": "M0_SYSTEMVERILOG_PASS", "vhdl_simulation": "M0_VHDL_PASS"}.get(name, "M0_VERILOG_PASS")
        if marker not in log:
            return False, f"Expected actual assertion-test marker {marker}."
        if name in ("waveforms", "vhdl_simulation"):
            traces = list(build.rglob("*.vcd"))
            valid = any("$var" in path.read_text(encoding="utf-8", errors="replace") and "#" in path.read_text(encoding="utf-8", errors="replace") for path in traces)
            return valid, "Expected VCD declarations and time changes."
        return True, "Actual HDL assertions completed."
    if name.startswith("cocotb_"):
        for path in build.rglob("*.xml"):
            try:
                tree = ET.parse(path)
                cases = list(tree.iter("testcase"))
                if cases and not list(tree.iter("failure")) and not list(tree.iter("error")) and not list(tree.iter("skipped")):
                    return True, "Cocotb truth-table assertions reported passing test cases."
            except ET.ParseError:
                continue
        return False, "Expected Cocotb XML with executed, passing assertions."
    if name == "schematics":
        for path in build.rglob("*.svg"):
            try:
                if ET.parse(path).getroot().tag.endswith("svg"):
                    return True, "SVG parsed successfully."
            except ET.ParseError:
                continue
        return False, "Expected a valid SVG schematic."
    if name in ("synthesis", "vhdl_synthesis"):
        for path in (build / "synth").rglob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("modules"):
                return True, "Yosys netlist contains modules."
        return False, "Expected nonempty Yosys netlist modules."
    if name.startswith("fpga_"):
        suffix = "*.fs" if "tangnano9k" in name else "*.bin"
        return any(path.stat().st_size > 0 for path in build.rglob(suffix)), "Expected nonempty packed bitstream; no USB programming performed."
    return True, "Actual tool command completed."


def execute_probe(report: dict, destination: Path, *, only: list[str] | None = None, timeout: float = 180, path_case: str = "ordinary") -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    selected = set(only or SMOKE_WORKFLOWS)
    report["mode"] = "execution"
    report["execution_scope"] = "Real CLI smoke fixtures; physical programming and clean-machine installation are not exercised."
    report["evidence_directory"] = str(destination.resolve())
    report["path_case"] = path_case
    report["fixture_sha256"] = {
        str(path.relative_to(FIXTURES)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(FIXTURES.rglob("*")) if path.is_file()
    }
    report["source_sha256"] = {
        name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
        for name in ("vwb.py", "companion/process.py", "companion/toolchains.py", "scripts/probe_toolchain.py")
    }
    for name, workflow in report["workflows"].items():
        if name not in selected:
            if workflow["state"] != "blocked":
                workflow.update(state="not_run", evidence="not-selected")
            continue
        if workflow["missing_tools"]:
            workflow.update(state="blocked", evidence="missing-tools", note="Install and activate required native tools, then rerun this probe.")
            continue
        suffix = " project µ" if path_case == "unicode-spaces" else " project" if path_case == "spaces" else ""
        root = destination / f"{name}{suffix}"
        if root.exists():
            raise ValueError(f"refusing to reuse stale execution directory: {root}")
        shutil.copytree(FIXTURES, root)
        argv = [sys.executable, str(REPO / "vwb.py"), "--root", str(root), "--src-dir", "src", "--test-dir", "test", "--build-dir", "build", "--color", "never", "--verbose", *command_for(name)]
        workflow.update(command=argv, project=str(root), evidence="real-cli-fixture")
        started = time.monotonic()
        try:
            result = run_process(argv, cwd=root, env=os.environ.copy(), timeout=timeout, max_output_bytes=2_000_000)
            log = (result.stdout or "") + "\n" + (result.stderr or "")
            log_path = destination / f"{name}.log"
            log_path.write_text(log, encoding="utf-8")
            passed, note = inspect_result(name, root, result.returncode, log)
            workflow.update(state="timed_out" if getattr(result, "timed_out", False) else "passed" if passed else "failed",
                            returncode=result.returncode, note=note, log=str(log_path),
                            log_sha256=hashlib.sha256(log.encode("utf-8")).hexdigest(),
                            log_excerpt=log[-8000:] if not passed else None,
                            artifacts=[{"path": str(path.relative_to(root)), "bytes": path.stat().st_size,
                                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                                       for path in sorted((root / "build").rglob("*")) if path.is_file()])
        except (OSError, ValueError, ET.ParseError) as exc:
            workflow.update(state="failed", error=str(exc))
        workflow["duration_seconds"] = round(time.monotonic() - started, 3)
    report["smoke_passed"] = all(report["workflows"][name]["state"] == "passed" for name in selected)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="actually run native tools; missing/failed selected workflows exit 1")
    parser.add_argument("--only", action="append", choices=SMOKE_WORKFLOWS, help="run only these fixture workflows (repeatable)")
    parser.add_argument("--work-dir", type=Path, help="parent for fresh evidence directory (kept for inspection)")
    parser.add_argument("--output", type=Path, help="write JSON report here instead of stdout")
    parser.add_argument("--timeout", type=float, default=180, help="per-workflow timeout in seconds")
    parser.add_argument("--path-case", choices=["ordinary", "spaces", "unicode-spaces"], default="ordinary", help="keep portability failures separate from the baseline toolchain probe")
    args = parser.parse_args()
    report = capability_report()
    if args.execute:
        if args.work_dir:
            args.work_dir.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="vwb-m0-", dir=args.work_dir))
        execute_probe(report, directory, only=args.only, timeout=args.timeout, path_case=args.path_case)
    content = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")
    if args.execute:
        selected = args.only or SMOKE_WORKFLOWS
        passed = sum(report["workflows"][name]["state"] == "passed" for name in selected)
        print(f"Native workflows {report['platform']['target']}: {passed}/{len(selected)} passed", file=sys.stderr)
        for name in selected:
            workflow = report["workflows"][name]
            if workflow["state"] == "passed":
                continue
            detail = workflow.get("error") or workflow.get("note", "")
            if workflow["missing_tools"]:
                detail = "missing tools: " + ", ".join(workflow["missing_tools"])
            print(f"  {name}: {workflow['state']}; {detail}", file=sys.stderr)
            if workflow.get("log_excerpt"):
                print(workflow["log_excerpt"][-1800:], file=sys.stderr)
    return int(args.execute and not report["smoke_passed"])


if __name__ == "__main__":
    raise SystemExit(main())
