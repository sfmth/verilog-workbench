"""Narrow shutdown compatibility for the locked Cocotb development build.

This module is also copied as a standalone private startup module into a fresh
simulation directory. It must not import the application or the user's tests.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping


AFFECTED_VERSION = "2.1.0.dev0+41564633"
RUNTIME_MODULE = "_vwb_cocotb_runtime"
STARTUP_ENVIRONMENT = ("PYGPI_USERS", "PYGPI_ENTRY_POINT", "GPI_USERS")

# This exact build's pygpi.entry.load_entry defaults, with our shutdown
# registration after package initialization and before regression starts.
STARTUP_USERS = ",".join((
    "cocotb_tools._coverage:start_cocotb_library_coverage",
    "cocotb.logging:_configure",
    "cocotb._init:init_package_from_simulation",
    RUNTIME_MODULE + ":install",
    "cocotb._init:run_regression",
))


def automatic_startup(version: str, environment: Mapping[str, str]) -> str | None:
    """Preserve every explicit startup configuration and every other version."""
    if version != AFFECTED_VERSION or any(name in environment for name in STARTUP_ENVIRONMENT):
        return None
    return STARTUP_USERS


def stage_runtime(work_dir: Path) -> Path:
    """Expose only this trusted module, without shadowing project packages."""
    target = work_dir / (RUNTIME_MODULE + ".py")
    content = Path(__file__).read_bytes()
    try:
        with target.open("xb") as output:
            output.write(content)
    except FileExistsError:
        if target.is_symlink() or not target.is_file() or target.read_bytes() != content:
            raise ValueError("Refusing a different Cocotb runtime startup module")
    return target


def install(_argv: list[str]) -> bool:
    """Register final cleanup only for the proven affected build on Icarus.

    Cocotb's regression first records its real verdict and XML, then invokes
    its shutdown callbacks, then stops/finalizes Python. This build can leave
    handle._apply_writes_cb registered after all test Tasks have been cancelled.
    Icarus 13 and 14 subsequently invoke that callback with no Python interpreter.
    Cancelling it at shutdown changes neither test execution nor its verdict.
    """
    import cocotb

    if getattr(cocotb, "__version__", None) != AFFECTED_VERSION:
        return False
    if not getattr(cocotb, "SIM_NAME", "").lower().startswith("icarus"):
        return False

    import cocotb.handle as handle
    from cocotb._init import _register_shutdown_callback

    if not isinstance(getattr(handle, "_write_calls", None), dict) or not hasattr(handle, "_apply_writes_cb") or not callable(_register_shutdown_callback):
        raise RuntimeError("The known Cocotb development build has an unsupported shutdown layout")
    if getattr(handle, "_vwb_shutdown_cleanup_registered", False):
        return False

    def cancel_final_write_callback() -> None:
        callback = handle._apply_writes_cb
        if callback is not None:
            cancel = getattr(callback, "cancel", None)
            if not callable(cancel):
                raise RuntimeError("The known Cocotb pending write callback cannot be cancelled")
            cancel()
            handle._apply_writes_cb = None
        handle._write_calls.clear()

    _register_shutdown_callback(cancel_final_write_callback)
    handle._vwb_shutdown_cleanup_registered = True
    return True
