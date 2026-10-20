# Companion protocol 1 — M0 contract

This document describes the implemented HTTP proof in `companion/server.py`.
The matching reference JSON Schema is
[`protocol/companion-v1.schema.json`](../protocol/companion-v1.schema.json).
The server validates requests with its own stdlib checks; it does **not** load
or automatically validate against that schema. Endpoint-specific bodies are
defined under `$defs`, for example `fileSaveRequest` and `jobResponse`.

The [semantic editor extension](EDITOR_WORKSPACE.md) adds authenticated
`/v1/editor/capabilities`, `/query`, `/apply`, `/undo` and `/recover` operations.
Its bounded document snapshots and server-issued edit IDs remain protocol 1;
it does not expose arbitrary LSP RPC or browser-selected native commands.

The protocol version is the JSON number **`1`**. It is not a string or a named
version token. Native tool discovery has a separate nested schema identifier,
`"vwb.toolchains/1"`, which does not change the HTTP protocol version.

## Launch grant and connection

```sh
python -m companion --project /path/to/trusted/project --origin https://sfmth.github.io --port 0
```

`--project` grants exactly one ordinary local folder at native process startup.
The browser cannot choose another root. `--src-dir`, `--test-dir`, and
`--build-dir` are project-relative overrides. Otherwise `.vwb.json` supplies
these settings, defaulting to `src`, `test`, and `.vwb`. Outside-project paths,
Git metadata, symlinks/reparse points, and source/test directories inside the
build directory are refused. Restart the companion to grant another folder.

The CLI prints one local ready event:

```json
{"event":"ready","url":"http://127.0.0.1:49152","pairing_code":"LOCAL_SECRET_CODE","expires_in":300}
```

Use the exact printed URL. The service binds only IPv4 loopback `127.0.0.1`.
Alternate host spellings, `localhost`, IPv6, and a browser-supplied remote
address are not service aliases in this proof. There is no administrator,
Docker, or WSL requirement for this Python bridge; native HDL tools and USB
drivers are separate prerequisites.

Every API request must have the exact `Host: 127.0.0.1:PORT`. Browser requests
must have the exact launch-authorized Origin, including scheme and any port;
an origin contains no path or trailing slash. Duplicate Host/Origin headers
are rejected. A non-loopback website origin must use HTTPS. These headers are
browser-generated; frontend JavaScript must not attempt to override them.

Two endpoints do not require a bearer credential:

- `GET /v1/health` returns liveness/version only. This exact request also permits
  no Origin for a command-line liveness check; a present Origin must still match.
- `POST /v1/pair` exchanges the local code. Pairing **requires** the exact Origin
  and Host, is a mutation, and is not a read-only endpoint.

All other listed operations require exactly one header:

```http
Authorization: Bearer SESSION_TOKEN
```

The code expires after 300 seconds, permits five failed exchanges, and can be
successfully exchanged only once. A successful exchange returns a secret
session token. The token lasts for the native process lifetime. There is no
selective revocation or automatic session timeout in M0; stopping/restarting
the process revokes it. Forgetting a token in one browser tab only clears that
tab. Never put codes/tokens in URLs, persistent frontend storage, or logs.

Project summaries additionally expose an optional `id`: a SHA-256 digest of the
canonical folder path, normalized for host case behavior. The workspace uses it
to scope opted-in recovery so equally named folders do not share drafts. It is
not an authorization token or content revision, and does not expose the absolute
path. Older protocol-1 companions without this field remain usable; automatic
browser recovery is disabled for them. Moving a project changes this identity;
draft exports can be reviewed and imported explicitly.

CORS preflights are unauthenticated and have no pairing or file/job side
effects. With the exact Origin/Host, they allow requested methods `GET`, `POST`,
or `PUT` and headers `Authorization`/`Content-Type` only. An older browser's
`Access-Control-Request-Private-Network: true` gets the corresponding allow
header. These responses authorize no API operation. Current browser permission
and HTTPS-to-loopback behavior still require a real-browser matrix; unit tests
do not establish GitHub Pages browser compatibility.

## Endpoints

Every JSON mutation requires one positive, bounded `Content-Length`,
`Content-Type: application/json`, and a JSON object body. Transfer encoding is
not supported. Listed API replies use JSON except artifact downloads and empty
204 preflight replies. Request-target query parameters are accepted only where
shown; a token query parameter never authenticates a request.

| Method and path | Body or query | Successful response | Auth |
| --- | --- | --- | --- |
| `GET /v1/health` | None | 200 `healthResponse`: `{status:"ok", protocol:1, version}` | Host; matching Origin if present |
| `POST /v1/pair` | `pairRequest`: `{code}` | 200 `pairResponse`: `{token, project, protocol:1}` | Host + Origin + valid local code |
| `GET /v1/capabilities` | None | 200 `capabilitiesResponse` | Bearer |
| `GET /v1/project/files` | None | 200 `inventoryResponse`: `{files:[{path,size}], truncated}` | Bearer |
| `GET /v1/project/file?path=...` | Exactly one URL-encoded relative `path` | 200 `fileReadResponse`: `{path,content,revision,encoding:"utf-8"}` | Bearer |
| `PUT /v1/project/file` | `fileSaveRequest`: `{path,content,revision}`; no query | 200 `fileSaveResponse`: `{path,revision,size}` | Bearer |
| `GET /v1/artifacts` | None | 200 `inventoryResponse` for build-directory files | Bearer |
| `GET /v1/artifacts/file?path=...` | Exactly one project-relative `path` within build directory | 200 binary attachment | Bearer |
| `POST /v1/jobs` | `jobRequest`: `{command,options?,timeout?}` | 202 `jobResponse` | Bearer |
| `GET /v1/jobs/ID` | None | 200 `jobResponse` | Bearer |
| `POST /v1/jobs/ID/cancel` | Empty object `{}` | 200 current `jobResponse` | Bearer |

`project` contains only `{name,src_dir,test_dir,build_dir}`, not a browser-selected
root. File and artifact paths are relative to the launch-granted root. Project
inventory omits generated output, Git metadata, temporary save files, and links.
Artifact inventory includes the build prefix, e.g. `.vwb/sim/example/trace.vcd`.
The binary endpoint sends `application/octet-stream`, `Content-Disposition:
attachment`, `nosniff`, and `no-store`; it does not render generated HTML/SVG.

Capabilities contain `{protocol,project,commands,toolchains,limits,execution}`.
`execution` is `"trusted-local-project"`. Toolchain metadata is discovered and
cached on the first capabilities request for this process. It includes paths,
versions when available, binary architecture/compatibility candidates, and
workflow prerequisites. Availability and a successful version query are **not**
proof of workflow correctness or platform support. Workflow discovery states
remain `blocked`/`not_run` until separate native probes establish evidence.

## File revisions and saves

`revision` is lowercase SHA-256 of the current file's exact bytes. UTF-8 text is
decoded without universal-newline conversion. The API preserves supplied CRLF,
LF, BOM, and final-newline state; unchanged supplied content round-trips exactly.
The proof UI uses a textarea and restores a uniform CRLF/LF style; mixed-newline
editing fidelity is not established by the UI proof.

To overwrite, submit the revision returned by the most recent read:

```json
{"path":"src/counter.sv","content":"module counter; endmodule\r\n","revision":"64_LOWERCASE_HEX_CHARACTERS_FROM_READ"}
```

To create an absent file, submit `revision: null`. This never forces overwrite.
Its parent directory must already exist; browser folder creation is not exposed.
The generated build tree is read-only through the save endpoint.

A companion lock serializes saves. The implementation checks the disk revision,
writes/fsyncs a same-directory temporary file, preserves the current file mode,
checks the revision again, and atomically replaces the destination. A detected
external or concurrent edit returns 409 without replacing that version. An
uncooperative native process can still edit between the last check and replace;
portable cross-process compare-and-swap replacement is not claimed. Multi-file
transactional rename/refactoring is not implemented in this file API.

## Job options and execution

`command` is one of the following; unknown fields and free-form argv/shell strings
are rejected. Booleans must be JSON booleans, integer values must be integers,
and list values must be arrays. Module names use ordinary identifiers
`[A-Za-z_][A-Za-z0-9_$]*`, at most 128 characters; escaped identifiers are outside
this proof's adapter. Arrays of modules contain at most 32 names.

| Command | Accepted `options` | Fixed behavior |
| --- | --- | --- |
| `list`, `doctor` | Empty object | Adds `--json` |
| `test` | `modules`, `test_language` (`auto`, `cocotb`, `verilog`, `vhdl`), `waves`, `wave_format` (`fst`, `vcd`), `seed` (0–4294967295) | Reuses native VWB test discovery/execution |
| `lint` | `modules`, `all`, `linters` (up to 6 of `all`, `iverilog`, `verilator`, `yosys`, `verible`, `ghdl`) | Reuses native checks |
| `synth` | `module`, `format` (`json`, `svg`, `png`, `dot`), `full`, `flatten`, `schematic` | Adds `--no-view` |
| `formal` | Optional existing project `.sby` `config` | Does not request a GUI viewer |
| `fpga` | `module`, required `board` (`tangnano9k`, `icebreaker`), `stage` (`synth`, `pnr`, `pack`, `flash`), existing `.pcf`/`.cst` `constraints`, `program` | Programming requires both `stage:"flash"` and `program:true` |

Omitted options use native VWB defaults. With no explicit FPGA stage, VWB packs
a programming file; it does not program hardware. `program:true` is rejected
unless stage is explicitly `flash`; `flash` is rejected without explicit
`program:true`. This authorization applies to that one submitted job.

Each job launches isolated Python (`-I`) against the trusted application code,
with fixed launch-granted root/source/test/build paths and colors disabled.
Project configuration changes cannot replace those paths. There is no native
viewer launch or arbitrary executable endpoint in the adapter.

Only one job runs at a time. `timeout` defaults to 300 seconds and accepts 1–300.
POST returns a 24-hex-character job ID immediately; GET polls that ID. Output
becomes available when the runner finishes, rather than streaming during M0.
Cancel signals the process tree and returns its current state; poll until a
terminal status arrives. Cancelling an already finished job leaves its result.
Normal companion CLI shutdown unwinds through native-job cancellation. A real
POSIX test sends SIGTERM to a running companion host and verifies that its
native child and grandchild both stop. SIGKILL, forced Windows process
termination, machine failure, and other exits that cannot run cleanup are
outside that graceful-shutdown guarantee.

| `status` | Meaning |
| --- | --- |
| `running` | Process is active; `returncode` and final source revision are null |
| `passed` | Native process exited 0; inspect `stale` before treating it as current evidence |
| `failed` | Native process exited nonzero or the runner could not start/finish normally |
| `cancelled` | Explicit cancellation was observed by the runner |
| `timed_out` | Runner's time bound expired |

The runner distinguishes its cancellation/timeout flags from a program that
happens to exit 130/124. Job replies include `{id,command,status,returncode,
stdout,stderr,source_revision,source_revision_after,stale}`. Completed runner
responses also include stdout/stderr truncation flags. Failed process startup
can have no final revision or truncation flags. Internal events, threads,
credentials, and constructed argv are not returned.

Before execution, the companion hashes source/test settings, names and file
bytes. After the runner finishes, it hashes them again. Different revisions or
an unreadable final snapshot mark `stale:true`, optionally with
`source_snapshot_error`. This detects changed final inputs; it does not freeze
the input tree, detect a change later reverted to identical bytes, or invalidate
a finished record when a subsequent edit happens. The frontend must treat
missing final revision and stale records as insufficient current evidence.

## Limits and errors

| Resource | M0 limit |
| --- | --- |
| Editable/read text file | 2 MiB of UTF-8 file bytes |
| JSON request | 3 MiB encoded body |
| Path spelling | 1–1024 characters, subject to boundary/portable-name checks |
| Inventory | 10,000 files; reports when limit is reached |
| Source/test snapshot | 10,000 files and 64 MiB; linked source/test trees refused |
| Artifact download | 32 MiB |
| Concurrent jobs | 1 per native process/project |
| Retained jobs | 32; oldest finished record evicted before another is created |
| Job timeout | 1–300 seconds, default 300 |
| Captured output | Final 1 MiB of bytes per stdout/stderr stream, decoded with replacement |
| Pairing | 300 seconds; five failed exchanges; one successful exchange |

Listed API errors have the body `{"error":"human-readable explanation"}`.
Clients should use HTTP status and not parse the wording.

| Status | Typical reason |
| --- | --- |
| 400 | Malformed JSON/request, unsupported option/type, invalid portable path, or failed filesystem operation |
| 401 | Missing/invalid bearer credential, or incorrect pairing code |
| 403 | Origin/Host mismatch, path escape, Git/link access, read-only build write, or artifact outside build tree |
| 404 | Missing endpoint/file/parent directory/job |
| 409 | Revision conflict, already-used pairing, or another job is active |
| 410 | Pairing code expired |
| 411 | Missing, duplicated, or invalid Content-Length |
| 413 | Request/file/artifact/snapshot exceeds a bound; zero-length mutation body is also rejected here |
| 415 | Non-JSON mutation content type or non-UTF-8 editable file |
| 429 | Pairing attempt limit reached |
| 500 | Unexpected internal companion error |

Native tool failures normally arrive as a successful HTTP poll response with
`status:"failed"` and a native return code, not as an HTTP 500. Unsupported
HTTP methods and malformed HTTP transport may be rejected by Python's standard
HTTP handler outside the JSON API contract.

## Trust boundary and evidence

Pairing authorizes edits and native execution in a trusted local project.
The file API's project grant is **not an execution sandbox**. Cocotb is Python,
formal configurations can run tools/scripts, and native HDL tool inputs may
reference files outside the project using the user's permissions. Native
scripts can also choose their own outputs. No browser-supplied path/argv grants
an API escape, but these checks do not confine trusted executable project
content. Malicious local processes changing links/directories concurrently are
outside M0's filesystem threat model. Do not pair or run untrusted projects.

Companion HTTP tests cover exact Origin/Host/bearer checks, pairing lifecycle,
side-effect-free preflight, authorized revision saves, unchanged rejected
writes, CRLF/mode preservation, external/concurrent conflicts, path/size/link/
Git/build boundaries, native `vwb list`, strict job options, terminal statuses,
cancellation, and stale results after an external edit. Process-tree tests and
toolchain/editor probes are separate. These checks do not establish every OS,
browser, native toolchain, language service, or attached board as verified.
