# Bundled runtime notices

Verilog Workbench source is included in `app/` under GPL-2.0-only; see
`LICENSE.txt`. The native launchers and build scripts are part of that source.
The corresponding project source is at
https://github.com/sfmth/verilog-workbench (branch `web-port-plan`).

Official-runtime bundles contain unmodified CPython distributions produced by
Astral's python-build-standalone project. Python and its included libraries
retain their own licenses and notices under `runtime/`; those notices must be
kept when redistributing the bundle. Provider and upstream source:

- https://github.com/astral-sh/python-build-standalone
- https://docs.astral.sh/python-build-standalone/
- https://www.python.org/psf/license/

`bundle.json` records the pinned distribution URL and its SHA-256 digest.
The build does not download or include HDL tools, board drivers, language
servers, or Python packages such as Cocotb. Those are separate managed
toolchains with their own licenses and platform support evidence. A custom
`--runtime-dir` is the caller's responsibility to audit and redistribute; its
metadata is marked as caller-supplied instead of claiming official provenance.
