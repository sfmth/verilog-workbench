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

| Bundle target | Native runtime triple | Launcher | CI6 bundled-runtime smoke and durable report |
| --- | --- | --- | --- |
| `linux-x86_64` | `x86_64-unknown-linux-gnu` | `vwb-companion` | [Passed](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063/job/117751810242) · [JSON](m0-evidence/native-ci-linux-x86_64.json) |
| `linux-arm64` | `aarch64-unknown-linux-gnu` | `vwb-companion` | [Passed](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063/job/117751810215) · [JSON](m0-evidence/native-ci-linux-arm64.json) |
| `windows-x86_64` | `x86_64-pc-windows-msvc` | `vwb-companion.cmd` | [Passed](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063/job/117751810321) · [JSON](m0-evidence/native-ci-windows-x86_64.json) |
| `windows-arm64` | `aarch64-pc-windows-msvc` | `vwb-companion.cmd` | [Passed](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063/job/117751810292) · [JSON](m0-evidence/native-ci-windows-arm64.json) |
| `macos-arm64` | `aarch64-apple-darwin` | `vwb-companion` | [Passed](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063/job/117751810200) · [JSON](m0-evidence/native-ci-macos-arm64.json) |
| `macos-x86_64` | `x86_64-apple-darwin` | `vwb-companion` | [Passed](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063/job/117751810154) · [JSON](m0-evidence/native-ci-macos-x86_64.json) |

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

In [CI6](https://github.com/sfmth/verilog-workbench/actions/runs/39153500063),
commit `831ae23f8f10ebc89c09ed3b3a4cae9a0bbebe2e`, all six native targets
passed unit/regression checks, archive construction, and the complete
bundled-runtime smoke. Every target exercised exact CRLF-preserving saves and
the real VWB subprocess. The six JSON reports linked above preserve the actual
sanitized smoke output together with the source run, job, and commit, so this
evidence remains available after CI artifacts expire. The Windows companion
uses its native ARM64 CPython on the ARM64 runner; any x64 translation used by
separate HDL tools is a different part of the toolchain evidence.

Outstanding release work includes minimum supported OS testing, macOS
signing/notarization and Windows signing, desktop installation and update UX,
toolchain provisioning, and physical Tang Nano 9K / iCEBreaker driver and
programming checks. None of those gates is declared passed by this packaging
prototype. Full HDL workflow support has separate evidence in
[M0_CI_TOOLCHAINS.md](M0_CI_TOOLCHAINS.md); HDL tools are not bundled here.
