# M0 native companion proof

This module establishes a working, stdlib-only bridge between the GitHub Pages
frontend and one explicitly granted local project. It is an engineering proof,
not a declaration that the website or six-platform release is finished.

The [workspace development preview](../docs/WEB_WORKSPACE.md) uses this same
bridge for multi-file editing, recovery, native jobs and artifact/VCD viewing.
The [semantic editor extension](../docs/EDITOR_WORKSPACE.md) adds locally
configured language services and reviewed cross-file rename transactions.
The M0 proof remains available for focused connection checks.

From a source checkout with Python 3.10 or newer:

```sh
python -m companion --project /path/to/trusted/project --origin https://sfmth.github.io --port 0
```

The companion listens exclusively on `127.0.0.1`. Its startup JSON prints the
chosen address and a one-time pairing code. Enter these in the connection proof
UI. The code expires after five minutes and five incorrect exchanges lock it.
Restart the companion to pair again. Keep the code private. The exchanged bearer
token is never placed in a URL or request log. Stop the process to revoke it.
The session token lasts for this native process's lifetime: automatic session
expiry and selective revocation are not implemented in this proof. Keep the
proof tab open only while needed and stop/restart to revoke or replace pairing.
Normal CLI shutdown, including POSIX SIGTERM, unwinds through server cleanup
and cancels active native job trees before returning. A real subprocess test
verifies a companion-host SIGTERM stops both a native child and grandchild.
Forced termination such as SIGKILL cannot run Python cleanup and is outside
that graceful-shutdown guarantee.

`--src-dir`, `--test-dir`, and `--build-dir` accept project-relative paths.
Without overrides, `.vwb.json` settings are used, with `src`, `test`, and `.vwb`
defaults. Absolute paths, parent traversal, Git metadata, symlinks, Windows
reparse points, and output outside the project grant are refused. Source and
test paths cannot be inside the build directory. The browser cannot choose
another root; selecting another folder requires restarting the native command.

## HTTP contract

Requests must send `Origin: https://sfmth.github.io` (or the exact origin supplied
at startup) and the actual `Host: 127.0.0.1:PORT`. An unauthenticated liveness
request without an Origin is allowed only at `/v1/health`. All mutations take
JSON; all responses except artifact downloads are JSON. After pairing, send
`Authorization: Bearer TOKEN`. Sending a token as a URL query is unsupported.

| Method | Path | Payload or result |
| --- | --- | --- |
| GET | `/v1/health` | Safe version/protocol liveness, no project or credentials |
| POST | `/v1/pair` | `{ "code": "..." }` exchanges code for `{token, project, protocol}` once |
| GET | `/v1/capabilities` | Fixed command list, launch settings, limits, toolchain discovery |
| GET | `/v1/project/files` | `{files: [{path, size}], truncated}`; omits Git metadata, links and generated output |
| GET | `/v1/project/file?path=src/example.sv` | `{path, content, revision, encoding}` with SHA-256 revision |
| PUT | `/v1/project/file` | `{path, content, revision}`; use `null` revision only for create-if-absent |
| GET | `/v1/artifacts` | Metadata for build-directory files only |
| GET | `/v1/artifacts/file?path=.vwb/example.vcd` | Binary download, confined to build directory |
| POST | `/v1/jobs` | `{command, options, timeout}` returns HTTP 202 and a job object |
| GET | `/v1/jobs/ID` | Status, return code, bounded stdout/stderr; no arbitrary command string |
| POST | `/v1/jobs/ID/cancel` | Empty object `{}` signals process-tree cancellation |

Preflights for `GET`, `POST`, and `PUT` permit only `Authorization` and
`Content-Type`; they do not authenticate, pair, or mutate anything. The older
private-network preflight header is supported. Newer browser local-network
permission policies still require real-browser validation from HTTPS GitHub
Pages; HTTP unit tests cannot establish browser-policy compatibility.

Files are edited as UTF-8 bytes without universal-newline conversion: a read and
unchanged save preserves CRLF/LF, BOM and final-newline state. Saves preserve the
existing permission mode, fsync a temporary file in the same directory, recheck
the revision, then replace atomically. A companion lock serializes saves; a
detected external edit returns 409. A process editing the file between the final
revision check and replacement can still race this operation: portable filesystems
do not provide a cross-process compare-and-swap replacement primitive. The
editor extension adds staged multi-file edits with rollback and crash recovery;
it cannot make replacements atomic against independent external processes.

Text files are limited to 2 MiB and ordinary JSON request bodies to 3 MiB. Editor
endpoints have a separate 12 MiB bound for document snapshots. Inventories are
capped at 10,000 files, artifact downloads at 32 MiB. Only one job may run at a
time; 32 recent jobs are retained, each limited to 300 seconds. Output keeps at
most the final 1 MiB per stdout/stderr stream and reports truncation. A timeout
is distinguished from a program that exits with status 124. Cancellation is
distinguished from a program that exits with status 130.
Job status is `running`, `passed`, `failed`, `cancelled`, or `timed_out`.
Source/test paths and file bytes are hashed before and after execution; every
job records both revisions and a `stale` flag. A successful run with changed
source is stale and must not be shown as a current verification result.
Snapshotting is capped at 10,000 files and 64 MiB, excludes generated output
and Git metadata, and refuses source/test links. This detects final source
changes; it does not freeze a native job's input while the job is running.

## Native job scope

There is no shell endpoint or browser-supplied argv. The adapter runs the local
`vwb.py` with fixed launch-granted project/source/test/build paths, colors off,
and validated command-specific options. Each VWB job launches isolated Python
(`-I`), adding only the trusted installed application path to avoid importing
Python modules from the project folder or `PYTHONPATH` during driver startup:

| Command | Options |
| --- | --- |
| `list`, `doctor` | None; JSON output selected automatically |
| `test` | `modules` list, `test_language`, `waves`, `wave_format`, `seed` |
| `lint` | `modules` list, `all`, `linters` list |
| `synth` | `module`, `format`, `full`, `flatten`, `schematic`; viewer disabled |
| `formal` | Optional project-relative `.sby` config; viewer disabled |
| `fpga` | `module`, `board` (`tangnano9k` or `icebreaker`), `stage`, constraints file, `program` |

Programming hardware requires **both** `stage: "flash"` and `program: true` on
that specific job. Build requests default to packing and do not program a board.
The proof intentionally omits free-form tool flags, arbitrary script strings,
external include paths, escaped module identifiers, and browser-created folders.

The folder grant is a **file API boundary, not an execution sandbox**. Cocotb
tests are Python programs; formal configurations and HDL tool scripts may invoke
native programs or reference other files using the user's permissions. Run only
projects and edits you trust. Pairing also authorizes saving project contents;
review downloaded source before granting it and starting tools. Output paths
chosen directly through the API remain inside the approved build directory;
this does not confine what trusted native project scripts can do. An untrusted
local process changing directories or links concurrently is outside this proof's
filesystem threat model.

## Verification

```sh
python -m unittest discover -s tests -p test_companion.py -v
```

Tests exercise real HTTP, exact Origin and Host checks, bearer authentication,
expired/one-use/attempt-limited pairing, harmless CORS preflight, authorized
save, CRLF and mode preservation, external and concurrent revision conflicts,
request/file/path boundaries, symlink/Git protection, artifact confinement,
strict job validation, a real native `vwb list` job, and cancel/timeout status.
Process-tree, output-limit and platform-specific checks live in the process
runner's own tests. Linux execution here does not verify Windows/macOS, ARM64,
signing, USB hardware, complete toolchain installation, or language services.
