# M0 editor language-service evidence

Recorded 2026-10-18. This is a real local Linux x86-64 stdio probe, using
downloaded upstream release binaries, rather than an editor mock or text-search
implementation. It establishes a candidate service route on small fixtures.
It does **not** certify all six operating-system/CPU targets or implement the
browser editor/LSP bridge.

## Tested route

| Language | Candidate and method routing | Completion / hover / definition | Cross-file references | Exact semantic rename preview | Post-edit check |
| --- | --- | --- | --- | --- | --- |
| Verilog/SystemVerilog | slang-server 0.3.0 for completion, hover, definition and references; Verible v0.0-4296-g0f262651 for rename | Passed | Passed after adding exactly one independently returned definition; both services agreed on the same definition and use locations | Passed: 3 edits across 3 files through Verible | Icarus compiled both affected top modules |
| VHDL | VHDL-LS 0.88.0 | Passed | Passed, including package declaration and body | Passed: 4 edits across 3 files | GHDL analyzed the edited VHDL-2008 sources |
| Python/Cocotb test source | basedpyright 1.40.2, Node stdio service | Passed | Passed through imports and qualified uses | Passed: 4 edits across 3 files | Python compiled every source and checked renamed helper behavior plus unchanged shadowed parameter behavior |

Every fixture has an unrelated, same-spelled symbol and a comment containing
the name. Exact expected edited texts are compared with the service's proposed
workspace edit, so replacing every textual occurrence would fail. Probes run
under a directory containing a space and a Unicode character. Python includes
an actual `import cocotb` and `@cocotb.test()` declaration, but these checks prove
navigation of project Python symbols, not complete Cocotb dynamic handle/type
intelligence or simulator execution.

The only refactoring operation proved here is **symbol rename** for these
fixtures. Extract-function, move-symbol, macro renaming, arbitrary HDL
refactoring, and safety on every HDL construct have not been implemented or
proved. Diagnostic notification counts are recorded where received; this probe
does not test diagnostic accuracy against deliberately invalid designs.

## Important upstream behavior found

`slang-server` 0.3.0 advertises `renameProvider`, but renaming the tested package
function from a use returned only the two call-site edits and omitted its
declaration. Applying that response would break the design. The raw failing
check is retained as `primary_checks.rename` in a combined probe report. **Do
not enable slang's rename response as a production edit operation.**

Both slang-server and Verible returned the two uses without the declaration
despite `includeDeclaration: true`. The combined experiment adds the single
definition returned by an independent `textDocument/definition` request. It
first requires both services, opened on the identical source set, to agree on
the definition range and reference-use set. Multiple definition candidates,
stale source bytes, external paths or disagreement fail closed. No declaration
is discovered through a text search.

Verible returned the correct declaration and use edits for rename, but did not
advertise completion. Its hover needed `--lsp_enable_hover`. Therefore neither
tested SV server alone met this complete editor probe; the proposed method
routing is exercised only by the M0 script. Production routing, matching
project/source/include/define configuration, service restart/reindex behavior,
dirty-buffer synchronization and an editor-facing capability contract remain
M1 work. Production must keep unsafe rename disabled unless the required
verified route is available.

## Reproduce

Install the pinned upstream binaries below and put `slang-server`, `vhdl_ls`,
`basedpyright-langserver` and `verible-verilog-ls` on `PATH`. Python 3.11+ is used
for the companion probe. basedpyright's npm distribution also needs a compatible
Node runtime; users of the eventual native app should receive a managed runtime
rather than be required to install npm. A VHDL-LS release includes its standard
libraries; preserve that directory layout when extracting it.

```sh
python3 -m unittest discover -s tests -p test_lsp.py -v
python3 scripts/probe_editor.py \
  --sv-refactor-server '["verible-verilog-ls", "--lsp_enable_hover"]' \
  --output editor-evidence.json
```

An alternate local executable can be supplied as a JSON argument array, for
example `--server 'vhdl=["/opt/vwb/tools/vhdl_ls"]'`. No shell command string is
used. `--language vhdl` narrows an investigation. Each request has a bounded
timeout, server logs and message sizes are bounded, shutdown is attempted, and
the owned native process tree is reaped on exit or failure. Missing executables,
unsupported advertised methods and failed semantic results have distinct report
states. A report with any failed required method returns nonzero; absence never
counts as a pass.

When `iverilog` and `ghdl` are on `PATH`, successful rename previews are written
only into disposable fixture copies and compiled. An absent compiler is
`post_rename_validation.status = not_run`; it is not implied to have passed.
Python syntax and helper behavior are checked through the probe's interpreter.
Full simulation and Cocotb execution belong to the separate toolchain checks.

## Boundary and safety checks

`companion/lsp.py` is an internal experimental stdio client and preview helper.
It is **not exposed through the companion HTTP API**. The command comes from a
trusted local operator; the website cannot choose an arbitrary executable.

Rename preview checks current disk bytes and document versions, authorized
project boundaries, URI type, valid UTF-16 positions and overlapping edits.
It rejects file resource operations, annotated edits needing an interactive UI,
unknown files and external edits. Preview never writes ordinary project files.
Tests cover framing/Unicode, fragmented reads, malformed messages, configuration
requests, bounded timeout/shutdown, stale bytes/versions, overlap, CRLF
preservation and reference-merge ambiguity. Fake language servers are used only
for those protocol tests and are not the semantic feasibility evidence above.
Large request and `didOpen` writes to a server that never reads stdin are tested
too: a bounded writer queue enforces the send deadline, terminates the owned
process tree on expiry, and unblocks pipe cleanup.

These checks do not implement semantic collision detection, multi-file atomic
commit/rollback, undo, dirty editor buffers or recovery. Those remain release
gates. The real semantic probe validates against known explicit expected edits;
that fixture oracle is not a general validation algorithm for user projects.

## Provenance and remaining platform work

Upstream release artifacts tested on Linux x86-64:

| Service | Pinned upstream source | Archive SHA-256 |
| --- | --- | --- |
| slang-server | [v0.3.0 Linux x64](https://github.com/hudson-trading/slang-server/releases/download/v0.3.0/slang-server-linux-x64.tar.gz) | `29ad91f245f100337c35ce27dba711d5f0ea50ebc70e73c78e2b27400a87ae62` |
| VHDL-LS | [v0.88.0 Linux GNU x64](https://github.com/VHDL-LS/rust_hdl/releases/download/v0.88.0/vhdl_ls-x86_64-unknown-linux-gnu.zip) | `8cba241c6df50c9d6d8c2bd7e8ddd592b15c54f5b7656c440265de25bc3335f3` |
| Verible | [v0.0-4296-g0f262651 Linux static x64](https://github.com/chipsalliance/verible/releases/download/v0.0-4296-g0f262651/verible-v0.0-4296-g0f262651-linux-static-x86_64.tar.gz) | `8569defb891d2316067613ea00442af28a7a09d405d95b54c0c91f9942d26635` |
| basedpyright | [npm 1.40.2](https://www.npmjs.com/package/basedpyright/v/1.40.2) | npm package integrity plus server version `1.40.2`; executable hashes are emitted in the probe report |

These observed checksums identify downloaded bytes; they are not independent
publisher signatures. No service binaries are committed to this repository.
The observed basedpyright npm integrity was
`sha512-9uld8+5tZC2Gxf8nIynyunK0N4xn01W1ApzLMXDAcuA8uJq2QSfldYn9gSbxn9MWR8wmHDo7GaEHLhlnLFHaJA==`.
Managed distribution needs pinned artifact integrity, appropriate notices and
runtime packaging. Service `initialize` information and full advertised
capabilities are captured in JSON; VHDL-LS did not return `serverInfo`, so its
version was separately verified with `vhdl_ls --version` (`0.88.0`).

The slang release inventory includes Linux x64/ARM64, Windows x64/ARM64 and a
macOS archive; verify actual macOS architectures before claiming both Mac
targets. The VHDL-LS release inventory checked has macOS ARM64, Linux x64 and
Windows x64 binaries, but no Linux ARM64, Intel Mac or Windows ARM64-specific
asset. Source builds and the permitted Windows ARM x64 compatibility route need
native tests. Verible likewise needs per-target packaging/compatibility checks.
None of these documentary asset routes proves OS support. All five other
required OS/CPU environments remain **not tested**, including their process
lifecycle and full editor refactoring behavior.

Primary feature/configuration references checked on 2026-10-18:

- [slang language features and rename](https://hudson-trading.github.io/slang-server/features/features/)
- [slang project configuration](https://hudson-trading.github.io/slang-server/start/config/)
- [slang limitations](https://hudson-trading.github.io/slang-server/features/limitations/)
- [Verible language-server README and filelist setup](https://github.com/chipsalliance/verible/blob/master/verible/verilog/tools/ls/README.md)
- [VHDL-LS project and library setup](https://github.com/VHDL-LS/rust_hdl)
- [basedpyright language-server installation](https://docs.basedpyright.com/latest/installation/command-line-and-language-server/)

Live documentation may describe newer development behavior than the pinned
releases. The captured probe outcomes govern this M0 decision. Large projects,
includes/defines, ports and implicit connections, overloaded symbols, disabled
preprocessor branches, macros, dependency changes, case-insensitive paths and
collision handling still need dedicated fixtures before release.
