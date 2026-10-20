# Source editing in the Pages workspace

The development preview adds lexical highlighting, line numbers and literal
current-file search to the existing native textarea. The companion remains the
owner of disk writes and language-service queries. This increment advances M1;
it does not close the complete M4 release gate.

## Editing and finding text

Verilog/SystemVerilog (`.v`, `.vh`, `.sv`, `.svh`), VHDL (`.vhd`, `.vhdl`), and
Python (`.py`, `.pyi`) receive lexical colors. Strings and comments remain opaque;
Python formatted strings receive one string color, including their expressions.
This decoration does not parse source or determine whether a design is correct.
Use the separate code-intelligence and native job controls for those results.

The textarea still owns typing, selection, composition and native undo/redo.
Decoration never writes its value. Exact saved UTF-8/BOM/newline handling and
revision checks remain in the existing workspace model. Unsupported extensions
and bounded large files remain editable in explicit plain-text mode. Forced
colors display native source text without the color layer. Decorative text and
line numbers are hidden from assistive technology.

Choose **Find in file**, or press Ctrl/Cmd+F with the editor focused. Search
treats the query as literal text; **Match case** controls Unicode case matching.
Enter/Shift+Enter and the arrow buttons move forward/backward with wraparound.
Escape closes search and returns focus to the source. Search only selects text;
it neither replaces text nor saves files. Matching uses original UTF-16 offsets,
including astral Unicode characters. Canonically equivalent spellings are not
normalized. Queries are limited to 256 UTF-16 code units. The first 2,000
non-overlapping results are retained; truncation is explicitly shown.

## Bounds and stale work

The dedicated module worker only returns offset ranges and whitelisted token
kinds. The main thread creates text nodes and spans, never source HTML. A
response can decorate only the same file, session and exact source snapshot.
Input immediately restores visible native text until its decoration is ready;
composition keeps native text visible. Worker errors, malformed token ranges or
a 2.5-second response timeout leave source editable in plain-text mode.

Highlighting is limited to 256 KiB of UTF-8, 10,000 lines and 8,000 tokens. The
existing 2 MiB source-editing limit is unchanged. Above 10,000 lines the gutter
uses an ellipsis and status reports the line count instead of creating an
unbounded list. Line numbers and source colors scroll with the native textarea.

## Verification

`tests/test_syntax.mjs` covers language boundaries, unfinished edits, Unicode,
source preservation and all lexer bounds. `tests/test_syntax_view.mjs` covers
stale responses, malformed results, composition, disposal and native selection.
`tests/test_search.mjs` covers escaped literal queries, original Unicode offsets,
navigation and result limits. The new browser probe
`scripts/probe_source_workspace.mjs` exercises real rendered controls and a
temporary native companion, including explicit worker faults. Its `--public-url`
mode repeats acceptance from the actual HTTPS workspace using Chromium's test
local-network grant. This does not establish manual permission UX or other
browser/platform support. Existing simulation, semantic and lifecycle browser
probes remain separate regression requirements in Pages CI.

Broader syntax/semantic constructs, interactive snippets, complete folder/watch
behavior, Firefox/Safari acceptance, native installation/toolchain gaps and
physical board workflows remain tracked release work.

Review also found a file-dialog race: a creation could be offered while a
background trash read was still pending, then rejected by the model's read
guard. Dialog-owned entry/trash reads now block mutations until they finish,
including across close/reopen and out-of-order responses. The lifecycle browser
probe explicitly holds a real trash response, delivers a newer one first and
checks that buttons and Enter cannot create until the older read settles.

The retained [local source report](workspace-evidence/source-local-linux-x64.json)
passes all 15 groups in Chromium 141. Token/gutter scroll geometry differs from
the native textarea by 0 px in the measured cases; a 2,096,922-byte file passes
native edit/save. One group explicitly simulates a persisted `pagehide` DOM
event and verifies the worker and Find remain active; actual BFCache navigation
is not claimed. The [manifest](workspace-evidence/source-web-build.json) and
[source receipt](workspace-evidence/source-source-linux-x64.json) identify the
tested bytes. Separate [workspace](workspace-evidence/source-workspace-regression-linux-x64.json),
[semantic](workspace-evidence/source-editor-regression-linux-x64.json) and
[lifecycle](workspace-evidence/source-files-regression-linux-x64.json) reports pass
13 groups, all three languages and 16 groups respectively. Local native suites
pass 238 Python checks (five skips), 162 CLI checks (one skip) and 106 JavaScript
checks. Local GHDL is unavailable; semantic VHDL language-service checks pass,
with its separate post-edit compiler result explicitly `not_run`.

[Pages run 39357760699](https://github.com/sfmth/verilog-workbench/actions/runs/39357760699)
deployed `8bbb67e` and passed build, deploy and public HTTPS verification. The
[hosted source report](workspace-evidence/source-ci-linux-x64.json) and
[actual HTTPS source report](workspace-evidence/source-public-ci-linux-x64.json)
each pass all 15 groups in Chromium 151, with every new runtime module's loaded
hash matching the tested manifest. The [Pages summary](workspace-evidence/source-pages-ci.json)
also retains the existing 13 built/nine public workspace groups, three semantic
language groups and 16 lifecycle groups passing. Hosted build semantic checks
include real Icarus, GHDL and Python post-edit validation; the public job reports
unavailable HDL compilers explicitly rather than inferring native passes.

All six native unit/CLI/protocol/bundle/smoke jobs pass in
[run 39357760768](https://github.com/sfmth/verilog-workbench/actions/runs/39357760768),
with [retained native evidence](workspace-evidence/source-native-ci.json).
The overall wider workflow still fails four established toolchain rows:
GHDL is missing on Linux ARM64 and Intel macOS; Windows also lacks the Cocotb
and Graphviz package routes. Linux x86-64, Arch and Apple Silicon pass 14/14
toolchain checks. The preview remains subject to the release gates above.
