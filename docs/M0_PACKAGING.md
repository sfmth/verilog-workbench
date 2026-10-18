# M0 native companion packaging

The companion is packaged with its own CPython interpreter. End users extract
one target-specific archive and run its launcher; system Python, pip, Docker,
and WSL are not required. This is a command-line native application foundation,
not a signed GUI installer or the complete first release.

## Runtime choice and platform matrix

[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
publishes redistributable CPython runtimes for all six requested targets,
including native Windows ARM64. `packaging/runtimes.json` pins CPython 3.12.15,
release [20261013](https://github.com/astral-sh/python-build-standalone/releases/tag/20261013),
with exact asset sizes and SHA-256 digests obtained from that release's GitHub
API asset metadata. No moving `latest` URL is used at build time. See the
provider's [running documentation](https://gregoryszorc.com/docs/python-build-standalone/main/running.html)
for distribution and OS compatibility details.

| Bundle target | Native runtime triple | Launcher | Hosted execution evidence through CI4 |
| --- | --- | --- | --- |
| `linux-x86_64` | `x86_64-unknown-linux-gnu` | `vwb-companion` | [CI4 passed full bundled-runtime smoke](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179322) |
| `linux-arm64` | `aarch64-unknown-linux-gnu` | `vwb-companion` | [CI4 passed full bundled-runtime smoke](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179366) |
| `windows-x86_64` | `x86_64-pc-windows-msvc` | `vwb-companion.cmd` | [CI4 built and reached the file-save check](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179255); smoke fixture failed before the actual VWB job |
| `windows-arm64` | `aarch64-pc-windows-msvc` | `vwb-companion.cmd` | [CI4 built and reached the file-save check](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179328); smoke fixture failed before the actual VWB job |
| `macos-arm64` | `aarch64-apple-darwin` | `vwb-companion` | [CI3 passed full bundled-runtime smoke](https://github.com/sfmth/verilog-workbench/actions/runs/39151829476/job/117746885299); [CI4 stopped during process unit checks](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179263) |
| `macos-x86_64` | `x86_64-apple-darwin` | `vwb-companion` | [CI3 passed full bundled-runtime smoke](https://github.com/sfmth/verilog-workbench/actions/runs/39151829476/job/117746885183); [CI4 stopped during process unit checks](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957/job/117749179396) |

These are native CPython distributions for all six targets. Individual HDL tools
may use owner-approved OS x86-64 compatibility; tool binaries are **not** part
of these bundles and have their own matrix. Successfully assembling an ARM64
archive on Linux x86-64 does not prove that it runs on ARM64.

## Build and launch

Maintainers need Python 3.12 or later to build, but no packaging dependency:

```sh
python scripts/build_native_bundle.py \
  --target linux-x86_64 \
  --download-runtime \
  --output-dir dist \
  --archive
```

This produces `dist/vwb-companion-linux-x86_64/` and a `.tar.gz` archive.
Windows targets produce `.zip` instead. Replace `--target` with any table entry;
the builder can assemble any target without executing the target interpreter.
`--runtime-archive PATH` uses an already downloaded **pinned** archive and still
verifies its size and digest. `--cache-dir PATH` controls the download cache.
An existing output bundle is never overwritten.

On Linux/macOS, including project paths with spaces:

```sh
./dist/vwb-companion-linux-x86_64/vwb-companion \
  --project "/home/user/HDL project" \
  --origin https://sfmth.github.io \
  --port 0
```

On Windows, in Command Prompt:

```bat
dist\vwb-companion-windows-x86_64\vwb-companion.cmd --project "C:\HDL project" --origin https://sfmth.github.io --port 0
```

`--origin` is the exact website origin, without a path or trailing slash. The
launcher prints the localhost URL and one-time pairing code. That code grants
access to the explicitly selected trusted local project. The companion binds
to loopback, and the browser still needs to pass its local-network permission
rules. A separate `vwb` / `vwb.cmd` launcher exposes the existing CLI.

For an audited custom self-contained CPython tree, use `--runtime-dir PATH`
instead of downloading. Its symlinks must remain inside that tree and its
license notices must be present. This route is labeled `caller-supplied` and
`archive_verified: false` in `bundle.json`; it does not claim that an arbitrary
system Python or virtualenv is portable. The official pinned route is the CI
and release-candidate path.

## Bundle contents and boundaries

- `runtime/`: the unmodified runtime and its license notices.
- `app/vwb.py`, `app/companion/*.py`, `app/_launch.py`: readable application source.
- `vwb-companion` and `vwb` (or `.cmd`): relative-path launchers using the bundled
  interpreter with `-I` isolation. They ignore `PYTHONPATH`, `PYTHONHOME`, and
  user site packages; changing the working directory does not select imports.
- `bundle.json`: target, pinned runtime provenance, application source digests,
  and an explicit `toolchains_included: false` marker.
- GPL license, README, third-party notices, and packaging source for audit.

Only application Python source is copied from the checkout. The build does
not copy the repository wholesale or include `.git`, local project data,
test fixtures, caches, generated results, or local agent files. Archive
extraction rejects traversal, external links, special devices, and excessive
expanded sizes before the runtime is copied. Runtime archives are authenticated
against the pinned digest before extraction.

The dependency-free companion runtime does not include Cocotb, simulators,
formal solvers, FPGA tools, language servers, or USB drivers. Those dependencies
must be managed and tested separately before the required first usable release.
The optional Python wheel described by `pyproject.toml` is for developers who
already have Python; it is not the system-Python-free application deliverable.

## M0 evidence and remaining gates

On 2026-10-18, the official Linux x86-64 archive passed its pinned digest check
and produced a working bundle. The bundle was relocated to a directory with
spaces and launched from a clean working directory with no Python on `PATH`,
a nonexistent `PYTHONHOME`, and a deliberately invalid `PYTHONPATH`. Its bundled
CPython reported 3.12.15 and the relocated runtime prefix; `vwb --version`,
companion startup, health, browser-origin pairing, and local-folder inventory
all succeeded. An authenticated `list` job also ran the bundled VWB subprocess
and discovered the fixture's `top` module under that same environment. The same
bundled interpreter loaded `ssl`, `sqlite3`, and `zlib`. The observations are
recorded in [m0-linux-evidence.json](../packaging/m0-linux-evidence.json).

`tests/test_packaging.py` covers digest rejection, corrupt caches, traversal,
links and special devices, decompression bounds, source allowlisting, archive
layout, launcher quoting, caller-supplied provenance, and overwrite refusal.

The hosted native job builds the archive on its matching OS/architecture and
invokes the bundled interpreter directly with `-I` and the bundled application
entry point. Its full smoke covers an empty working directory, poisoned Python
environment, version command, native startup, pairing, file inventory, a
revision-checked save, and a real VWB `list` subprocess. This proves the runtime
and API path; the hosted smoke does not launch a desktop installer or establish
interactive `.cmd`/shell launcher behavior. Cross-target archive construction
alone is not execution evidence.

In [CI4](https://github.com/sfmth/verilog-workbench/actions/runs/39152617957),
both Linux targets passed that complete smoke. Both Windows targets passed
unit/regression checks, built their pinned native runtime archives, and
successfully started, paired, and reached the save assertion. The save API
preserved CRLF bytes; the fixture compared them through `Path.read_text`, which
normalizes line endings. The fixture now checks exact bytes and uses explicit
CRLF input on every OS, but the corrected Windows smoke still needs native CI
confirmation. Both Macs had passed the complete smoke in CI3; CI4 stopped in
the process tests before rebuilding or exercising the bundle. That regression
also needs confirmation after its fix. A prior passing bundle is not recorded
as a passing current job.

Outstanding release work includes confirming the corrected smoke and process
tests on all six targets, minimum supported OS testing, macOS
signing/notarization and Windows signing, desktop installation and update UX,
toolchain provisioning, and physical Tang Nano 9K / iCEBreaker driver and
programming checks. None of those gates is declared passed by this packaging
prototype. Full HDL workflow support has separate evidence in
[M0_CI_TOOLCHAINS.md](M0_CI_TOOLCHAINS.md); HDL tools are not bundled here.
