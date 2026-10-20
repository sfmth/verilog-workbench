# Semantic editing in the workspace preview

The browser workspace now routes completion, hover, definition, references and
symbol rename through native language services. The services run locally in the
companion; project source stays on the user's machine. This advances M1 without
closing the full language, platform, installation or hardware release gates.

## Enable the services

Install compatible `slang-server`, `verible-verilog-ls`, `vhdl_ls` and
`basedpyright-langserver` executables on the companion's PATH. Missing services
are shown as unavailable with a reason. SystemVerilog rename requires both
slang-server and Verible; the other SystemVerilog operations use slang-server.

Alternatively supply an explicit, trusted local configuration:

```json
{
  "systemverilog": {"command": ["/opt/editor/slang-server"]},
  "sv_refactor": {"command": ["/opt/editor/verible-verilog-ls", "--lsp_enable_hover"]},
  "vhdl": {"command": ["/opt/editor/vhdl_ls"]},
  "python": {"command": ["/opt/editor/basedpyright-langserver", "--stdio"]}
}
```

```sh
python -m companion --project /path/to/project --origin https://sfmth.github.io \
  --editor-config /path/to/editor-config.json --port 0
```

Each service entry also accepts `initialization_options` and `configuration`
objects, or `false` to disable it. Commands and these options come only from
the local operator, never the HTTP request or `.vwb.json`. Native project
language-service configuration is still trusted executable-tool input, just as
native test and build configuration is. The folder grant is not an execution
sandbox. Keep the VHDL-LS standard libraries beside its executable.

For Linux x86-64 development and CI, `python scripts/acquire_editor_tools.py`
downloads the four [M0-pinned services](M0_EDITOR.md), checks archive integrity,
rejects unsafe archive entries and writes `.m0-tools/editor/editor-config.json`.
The basedpyright package needs a separately installed Node runtime. This script
is an acceptance helper, not the six-platform end-user installer. Binaries and
generated absolute-path configuration are ignored by Git.

## Use the editor

Open a source file and select **Code intelligence**. Use Complete, Hover,
Definition or References at the current cursor. Ctrl+Space requests completion;
F12 requests definition and Shift+F12 references. Completion replaces the
service's specified range, including any supported additional edits, rather
than blindly inserting at the cursor. Snippet defaults become ordinary text;
the preview does not provide a snippet session. Hover and diagnostics render as
plain text. Diagnostics are included when a query receives a notification for
the current document version; background diagnostic streaming is not provided.

Queries synchronize all open buffers, preserving exact UTF-8 text, BOM/newlines
and UTF-16 cursor positions. The companion checks each base disk revision,
captures project source and configuration, and rejects changes during a query.
Each query owns fresh service processes and a bounded deadline, then cleans up
their process trees. Browser responses are discarded if the session, buffers or
cursor have changed. This favors reproducible snapshots over a persistent,
incrementally indexed language-server session.

Rename requires saved buffers and no unresolved browser recovery drafts or
native job. Enter a new identifier to request a read-only before/after preview.
**Apply rename** submits only the companion-issued plan ID. **Undo last rename**
uses the inverse transaction while it is retained. Plans expire after five
minutes; undo is in memory and does not survive restarting the companion.
Both Apply and Undo recheck the entire captured source/configuration context,
so an unrelated external source edit invalidates the operation too.

SystemVerilog rename is deliberately routed through Verible, with independent
slang/Verible agreement on definitions and references and exact edit coverage.
The unsafe slang-only rename response discovered in M0 remains disabled.
Same-scope name collisions are rejected when the service's symbol information
proves them. This is not general collision detection or a proof for all macros,
overloads, generated sources or HDL constructs. Review the proposed edits.

## File transactions and recovery

The companion validates all affected files before writing, stages same-directory
files, preserves modes/newlines and records private durable backups. POSIX file
and directory fsync precede source replacement. A detected failure rolls back
only files still matching this transaction's expected bytes. It never knowingly
overwrites an intervening external edit. Companion operations serialize writes;
portable filesystems do not provide atomic multi-file replacement against other
processes, and a crash can interrupt the replacement sequence.

An interrupted transaction retains a journal under
`<build-directory>/editor-recovery/pending`. On restart, a pending recovery blocks
editor queries, ordinary saves and native jobs. **Recover interrupted edit**
explicitly restores verified backups where safe. External changes leave recovery
pending for manual resolution. The companion never adopts a foreign nonempty
build directory as its recovery store. Private backups are excluded from project
inventory, file reads and artifact downloads. Windows uses the existing process
and file primitives, but equivalent crash-durability acceptance remains open.

## Protocol extension

These authenticated protocol-1 endpoints use the same exact Origin, Host and
bearer checks as file/job operations:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/v1/editor/capabilities` | Available operations and transaction recovery state |
| POST | `/v1/editor/query` | Operation, active path/position and versioned open documents; rename also takes `new_name` |
| POST | `/v1/editor/apply` | Apply a retained server plan with `{ "id": "..." }` |
| POST | `/v1/editor/undo` | Apply a retained inverse with `{ "id": "..." }` |
| POST | `/v1/editor/recover` | Explicit recovery with `{}` |

There is no arbitrary LSP method, executable, argv, URI or edit endpoint. Query
documents are bounded to 64 files, 2 MiB each and 8 MiB total UTF-8 source. Editor
JSON bodies allow 12 MiB for escaped text and metadata; other JSON endpoints keep
the 3 MiB limit. Transactions allow 32 files and 8 MiB of combined before/after
text. The cache retains at most 16 plans/inverses and 32 MiB. A query has an
eight-second operation budget including service cleanup. Reference results,
completions and diagnostics have separate bounded result counts.

## Evidence and remaining work

`tests/test_editor.py`, `tests/test_companion_editor.py` and `tests/test_edits.py`
cover transport normalization, authenticated HTTP boundaries, revision conflicts,
rollback, interrupted recovery and unsafe paths using controlled fake services.
They do not establish language semantics. JavaScript checks cover stale results,
completion ranges, atomic buffer updates and editor controls.

`scripts/probe_editor_workspace.mjs` exercises the actual browser UI, real
companion, pinned services and disposable Unicode-path multi-file projects for
all three languages. Its explicit expected edits preserve unrelated same-spelled
symbols and comments. It checks completion insertion, navigation, references,
preview/Apply/Undo, dirty-buffer rejection and external conflicts. Icarus, GHDL
and Python post-edit validation are reported separately; missing compilers are
`not_run`. `--public-url` runs the same semantic acceptance from actual HTTPS
Pages with an explicit Chromium test local-network grant. Pages CI requires both
built-output and post-deployment probes and retains their reports.

The retained [local browser report](workspace-evidence/editor-local-linux-x64.json)
passes all three languages in Chromium 141, including a real stopped-companion
check that preserves a dirty draft and disables Run/Save. Icarus compilation and
Python syntax/helper validation passed; local GHDL was unavailable. The existing
[13-group workspace regression](workspace-evidence/editor-workspace-regression-linux-x64.json)
also passed with real simulation, failure and cancellation. The
[web build manifest](workspace-evidence/editor-web-build.json) and
[companion/service hashes](workspace-evidence/editor-source-linux-x64.json)
identify tested bytes. Public HTTPS results are recorded separately after deploy.

Broader construct/configuration fixtures, diagnostic accuracy, interactive
snippets, syntax highlighting, file/folder lifecycle, managed service/runtime
installation, current Firefox/Safari, six-platform durability and physical board
acceptance remain open. See the [workspace release gates](WEB_WORKSPACE.md).
