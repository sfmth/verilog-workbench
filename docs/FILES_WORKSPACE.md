# Files and folders in the workspace preview

The browser workspace now creates files and folders, renames or moves existing
entries, moves them to private project trash, and restores them. It uses the
same local companion and granted folder as editing and native jobs. Update the
companion and browser together to use these protocol-1 extensions.

## Browser workflow

Use **New file** or **New folder** in the Files explorer. Enter a project-relative
path whose parent already exists. A new file is empty UTF-8 text and opens in the
editor; save edited contents normally. Empty folders appear in the explorer.

**Manage files**, or a folder button in the explorer, opens the file manager.
Choose a file or folder, review its current disk entry, and enter a **Destination
path** for **Rename / move**. **Reload entry** explicitly obtains a fresh revision.
Moving paths does not rewrite HDL includes, Python imports or project language
configuration. Open tabs and the active file link follow a successful move.

**Move to trash** asks for confirmation for the selected entry. It removes the
entry from the ordinary project tree and closes affected tabs. **Trash** lists
retained items; **Restore** returns an item to its original location only when
that path is unoccupied and the original parent exists. Restoring a parent
folder before its separately trashed child resolves that dependency.

All buffers must be saved or reloaded and imported recovery drafts resolved
before a lifecycle mutation. Pending file reads/saves and native jobs also block
it. The editor, pairing, file actions and semantic mutations are disabled while
an operation is in flight. A delayed file read cannot resurrect an old path.
Directory moves retain exact clean buffer text, BOM/newlines and disk revisions.
If the disk change succeeds but refreshing inventory fails, the model retains
the committed path mapping and reports the refresh error without retrying the
mutation. Source-path changes invalidate semantic previews/undo IDs, discovery
and existing run results.

## Revision and filesystem rules

A file entry's revision is its existing SHA-256 content revision. A directory
revision covers the bounded sorted subtree, names, kinds, exact file bytes and
empty directories. Move/trash requests must present the reviewed revision and
the base revisions of affected open buffers. A fresh directory snapshot cannot
silently accept a stale clean tab. External changes stop the operation with 409.

Destinations must be absent, including empty directories. Native exclusive
rename primitives prevent a concurrently arriving destination from being
overwritten: Linux `renameat2(RENAME_NOREPLACE)`, macOS
`renamex_np(RENAME_EXCL)` and Windows `os.rename`. Unsupported OS/filesystem
routes fail closed. Case-only moves and case-colliding paths are rejected for
portable behavior. Directory moves into themselves, links/reparse points,
hardlinked files and special files are refused. Future descendant path lengths
are checked before a move or trash relocation.

The project root, `.vwb.json`, configured source/test roots and their ancestors,
and the build tree and its ancestors cannot be moved or trashed. Missing source
and test folders can be created. The `.git` and `.vwb-trash` directory names are
reserved. Ordinary project configuration can still be edited through the
revision-checked text editor.

The companion serializes its writers. It does not freeze unrelated native
editors or provide an OS-wide compare-and-swap operation on source contents.
Independent changes during the last source check can still race a rename;
destination replacement remains prohibited. POSIX barriers flush source files
and directories. Windows flushes files using write-capable handles and safely
refuses inaccessible/read-only sources; equivalent directory crash durability
remains an acceptance gate.

## Persistent project trash

Trash lives in the project root's `.vwb-trash`, outside the generated build
directory. An owner marker, bounded checked manifests and verified payload
inventories bind it to the canonical project grant. The companion never adopts
a nonempty unowned store. Restarting the companion preserves entries, and
`vwb clean all` removes generated output without removing trash. Moving the whole
project to another canonical folder changes its ownership identity; retained
trash then requires native inspection instead of automatic adoption.

Trash is excluded from project/artifact inventories, file reads/saves, semantic
source captures and default CLI HDL/Python/include/formal discovery. It is local
source data: add `.vwb-trash/` to your project's Git ignore rules. This repository
already ignores it. It is not an alternate browser source cache.

Interrupted trash/restore operations retain their checked state and last data
copy. A verified interrupted entry can be acknowledged or restored explicitly.
Conflicts never replace a new original-path file. Corrupt or unverifiable records
remain visible beside healthy entries and require native inspection. Empty
metadata records left by early/final cleanup interruption are removed with
`rmdir` only on a later Trash mutation; nonempty records are never swept.

Retention allows 32 entries, 10,000 filesystem entries and 64 MiB total payload.
Each operated subtree also has the 10,000-entry/64 MiB bound. New text files keep
the 2 MiB editor limit. Requests use the ordinary 3 MiB JSON bound and at most
64 affected open-file revision heads. There is no automatic eviction or permanent
delete endpoint; restore items to free capacity, or inspect retained data locally.

## HTTP and acceptance

All routes require the existing exact Origin, Host and bearer session checks.

| Method | Route | Body/result |
| --- | --- | --- |
| GET | `/v1/project/entry?path=…` | `{path, kind, revision, file_count, bytes}` |
| POST | `/v1/project/create` | `{path, kind, content?}`; content only for files |
| POST | `/v1/project/move` | `{path, destination, revision, documents?}` |
| POST | `/v1/project/trash` | `{path, revision, documents?}`; returns retained ID |
| GET | `/v1/project/trash` | `{entries: […]}` including interrupted/invalid warnings |
| POST | `/v1/project/restore` | `{id}`; original path only |

`documents` contains `{path, revision}` heads for affected clean open files.
Project file inventories additionally include `directories: [{path}]`; older
companions without that field remain readable. The combined directory/file
inventory is capped at 10,000 entries and reports truncation.

`tests/test_files.py` uses real native filesystem operations, exclusive-rename
races and injected interruption points. `tests/test_companion_files.py` checks
actual authenticated HTTP, private data exclusion, stale buffer protection,
restart/restore, native discovery and mutual exclusion with jobs/editor recovery.
The JavaScript model tests cover pending reads/saves, dirty/recovery gates, exact
buffer remapping and successful disk changes followed by refresh failure.

`scripts/probe_files_workspace.mjs` exercises the actual UI and companion with a
temporary Unicode project. It tests creation, multi-file/four-language moves,
BOM/CRLF, dirty/recovery guards, stale/occupied-path conflicts, trash privacy,
real build cleanup, companion restart, restore and native inventory. It compares
delivered web hashes with the manifest. `--public-url` repeats the checks from
the actual HTTPS workspace using an explicit Chromium test local-network grant.
Pages CI requires both built-output and public lifecycle probes. Language
semantics, simulation, hardware and manual permission UX are separate evidence.

The retained [local Chromium report](workspace-evidence/files-local-linux-x64.json)
passes all 15 groups in Chromium 141. Two groups explicitly delay a real entry
response or abort the post-create file read to verify stale/loading and committed
change error handling; successful filesystem mutations still use the real native
companion. The [web manifest](workspace-evidence/files-web-build.json) and
[source receipt](workspace-evidence/files-source-linux-x64.json) identify tested
bytes. Separate native runs passed 237 Python checks (five skips), 162 CLI checks
(one skip) and 75 JavaScript checks. Public HTTPS and six-host results are recorded
separately after CI rather than inferred from this local proof.

[Pages run 39355643644](https://github.com/sfmth/verilog-workbench/actions/runs/39355643644)
deployed `0a41418` and passed build, deploy and all public checks. The retained
[hosted lifecycle report](workspace-evidence/files-ci-linux-x64.json) and
[actual HTTPS lifecycle report](workspace-evidence/files-public-ci-linux-x64.json)
each pass all 15 groups in Chromium 151. The
[Pages summary](workspace-evidence/files-pages-ci.json) also records the existing
13 built/nine public workspace groups and three semantic language groups passing.
Public local-network access is explicitly granted through Chromium's test API.

All six native unit, CLI, bundle build and bundled-companion smoke jobs pass in
[run 39355643657](https://github.com/sfmth/verilog-workbench/actions/runs/39355643657),
with [retained native evidence](workspace-evidence/files-native-ci.json).
The 40 filesystem checks execute on each target with explicit platform skips:
one long-host-path fixture on Apple Silicon and four POSIX-only cases on Windows.
That is native filesystem/API evidence; full browser/platform and crash-durability
acceptance are separate. The four wider toolchain failures still match the
documented missing GHDL/Cocotb/Graphviz package routes. Linux x86-64, Arch userspace
and Apple Silicon pass all 14 toolchain smoke checks.

The follow-up `6ceb9c3` preserves the prior native Windows uppercase `.PY`/`.SBY`
matching and POSIX case sensitivity in default discovery; `9381fae` canonicalizes
that new test fixture's temporary paths. The web and companion source bytes are
unchanged. [Final Pages run 39356087324](https://github.com/sfmth/verilog-workbench/actions/runs/39356087324)
passes build, deploy and every public probe again. Its
[final Pages summary](workspace-evidence/files-final-pages-ci.json) records all
six browser reports and ten delivered asset hashes matching the retained
14-file manifest. [Native run 39356198313](https://github.com/sfmth/verilog-workbench/actions/runs/39356198313)
verifies the compatibility case on both Windows architectures and the six-host
unit/CLI/bundle/smoke gates, with [final native evidence](workspace-evidence/files-native-final-ci.json).
