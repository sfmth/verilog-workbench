# Browser workspace development preview

The `web-port-plan` branch now includes a static browser workspace at
`/verilog-workbench/workspace/` and the original connection proof at
`/verilog-workbench/m0/`. The [public workspace](https://sfmth.github.io/verilog-workbench/workspace/)
is deployed as a development preview, not the M4 complete release. It uses
ordinary local folders through the native companion.

## Run it locally

From this checkout:

```sh
python scripts/build_web.py --output dist/web --base-path /verilog-workbench/
python -m http.server 8766 --bind 127.0.0.1 --directory dist
```

Open `http://127.0.0.1:8766/web/workspace/`. Relative assets also work at the
actual Pages project prefix. In another terminal, grant a trusted project:

```sh
python -m companion --project /path/to/project --origin http://127.0.0.1:8766 --port 0
```

Use the exact printed companion URL and one-time code in **Connect companion**.
For the repository examples, use `--project examples`; their `src` and `test`
directories match the default configuration. The guide shows the launch command
for the current website origin. Native tools are separate prerequisites; this
preview does not install them.

## Implemented behavior

- Files open as tabs with retained dirty buffers. Ctrl/Cmd+S saves the current
  file; runs save all open changes first. Disk revision conflicts stop the run
  and preserve the draft. Reload reads successfully before discarding old edits.
  Unchanged saves preserve exact bytes; edited files retain the detected newline
  style and UTF-8 BOM. Mixed newline editing uses the first detected style.
- Browser recovery is opt-in for each project. It stores source text and base
  revisions in local storage, scoped by the companion's canonical-folder ID.
  The opt-in survives reload. Disable it to remove that project's stored drafts;
  current in-memory edits remain. Storage errors are visible. Draft export/import
  is an explicit JSON workflow, not a replacement for the local project folder.
  Restore reads the disk first and retains the draft's base revision, so an
  external change still conflicts when saving. Duplicate differing drafts require
  review/discard rather than silently replacing each other.
- Session credentials remain in tab memory and never enter URLs, browser
  storage, exports or probe reports. Reload requires pairing a freshly restarted
  companion. Retry connection reuses the current token after a temporary network
  interruption; a failed status request keeps the active job available to resume
  or stop. Forgetting the session does not cancel native jobs.
- Discover project populates design/test views and top selection. Tests, lint,
  synthesis, formal, FPGA stages and tool checks use the companion's existing
  validated command adapter. Missing tools surface as real failures. Programming
  is a separate, confirmed action specifying board, module and constraints;
  ordinary FPGA Run requests never flash hardware.
- Output distinguishes pass, failure, timeout and cancellation. Recognized source
  locations link to files; full logs remain available. Companion snapshot changes
  and edits made after a run mark results stale. Staleness remains until a new
  run; external edits after a completed run are not continuously watched.
- Generated files can be downloaded. SVG/PNG schematics display in an image
  context. Artifacts include earlier runs and are not attributed to the latest
  job. VCD viewing parses in a worker and provides scoped signal selection,
  scalar/bus traces, radix, cursor, zoom and pan. Limits bound input, expanded
  values, events, displayed signals and render work. Dense traces ask for zoom.
  FST and GTKWave layout import are not implemented.

Text editing is limited to 2 MiB per file. Draft exports allow at most 10,000
files and 64 MiB of UTF-8 source; the JSON import bound allows for escaped text
and metadata. VCD input is limited to 16 MiB, 2,048 declarations and 250,000
changes, with additional expanded-value bounds. The companion's job and artifact
limits continue to apply.

## Pages build and verification

`scripts/build_web.py` validates all local HTML links, module imports, worker
URLs and CSS references before copying into `dist/web`. Missing, escaping,
root-relative and symlinked assets fail the build. The output includes
`.nojekyll` and a deterministic SHA-256 manifest marked `development-preview`.
Static directories and fragment state need no server-side routing rewrite.

The Pages workflow runs path/model checks and a Chromium acceptance probe
against the built output under `/verilog-workbench/`, then uploads only that
output. Pull requests run the checks; only `web-port-plan` deploys. Site settings
enable GitHub Actions as the Pages source, and the `github-pages` environment
permits `web-port-plan`. A post-deployment Chromium job verifies the actual HTTPS
workspace against a real native companion and retains separate public evidence.
Building or passing the local probe alone does not prove public deployment.

```sh
node --test tests/test_*.mjs
python -m unittest discover -s tests -p test_web_build.py -v
python -m unittest discover -s tests -p test_companion.py -v
# Install the CI-pinned Playwright harness in a development directory first.
node scripts/probe_workspace.mjs --playwright-module ../.web-browser/node_modules/playwright --native-sim true --output pages-browser-evidence.json
# After publication, verify the real HTTPS app with a temporary local project.
node scripts/probe_pages.mjs --playwright-module ../.web-browser/node_modules/playwright --output pages-public-evidence.json
```

The browser probe uses a real companion and temporary Unicode-path project.
Its fixture VCD tests the viewer independently of installed simulators. With
`--native-sim true`, installed Icarus additionally executes assertion-backed
simulation, generated VCD download/rendering, a compiler failure, a running
infinite simulation cancelled through Stop, and a corrected rerun. Pages CI
installs Icarus and requires these checks. Without that option, the probe does
not claim simulation acceptance. Reports explicitly distinguish local browser
acceptance, real native jobs, public Pages deployment, and hardware evidence.
It must not promote an unrun cell to a pass.

Retained [Linux x86-64 browser evidence](workspace-evidence/linux-x64.json)
records 13 passing groups in Chromium 141.0.7390.37 with Icarus 13.0, including
stale-result and session-isolation regressions. Session isolation deliberately
injects delayed old HTTP responses; its new project pairing and native inventory
job are real. The [tested web build manifest](workspace-evidence/web-build.json)
records the shipped file digests. Separate Python runs completed 123 companion/
platform/build checks and 162 CLI regression checks with six explicit missing
tool/platform skips in total. JavaScript checks cover 26 buffer/recovery cases,
nine VCD cases and five protocol cases. This local evidence does not close public
HTTPS, other-browser/platform or hardware gates.

## Published HTTPS evidence

[Pages run 39345754953](https://github.com/sfmth/verilog-workbench/actions/runs/39345754953)
built and deployed `ebf8d10`. Its [hosted Chromium evidence](workspace-evidence/pages-ci-linux-x64.json)
passes all 13 workspace groups with Chromium 151 and Icarus 12, including real
simulation, failure and cancellation. The actual [public HTTPS probe](workspace-evidence/pages-public-linux-x64.json)
then passes nine groups with Chromium 141: public app/manifest loading, real
UI pairing, Unicode multi-file and CRLF saves, revision conflicts, session/file
recovery after reload, native inventory, fixture artifact download and the public
VCD worker. Eight delivered document/module/style/worker hashes match the deployed
manifest. This is public-origin acceptance, distinct from the localhost CI proof.

The public probe explicitly grants local-network permission through Chromium's
test API. A fresh context without that test grant timed out on loopback health;
that observation is not proof of a manual deny decision or browser prompt UX.
The test does not disable mixed-content/CORS security, and credentials are absent
from reports, URLs and persistent browser storage. Manual permission/reconnect
and current Firefox/Safari/platform coverage remain open.

The six native runtime/unit/bundle CI rows pass for this code change. Broader
native toolchain CI continues to expose the documented GHDL/Cocotb/Graphviz
package gaps on four target rows; publishing the preview does not waive them.

## Remaining release gates

M1 still requires semantic language-service integration, safe project-wide
rename/undo, complete folder/file lifecycle operations and broader recovery
fixtures. M2 needs full language/analysis workflows and large-design acceptance.
M3 needs complete native tool acquisition/install/repair/uninstall and recorded
board programming. M4 requires the remaining real HTTPS browser/permission
checks and the complete six-platform/board matrix. The [M0 evidence gaps](M0_STATUS.md)
also remain open. A published development preview is now verified; physical USB
programming and complete first-release support remain unverified.
