# M0 browser connection proof

The static proof at [`web/m0/`](../web/m0/index.html) exercises protocol v1:
pair the native companion, inspect the granted project, read/save UTF-8 files
with revision conflicts, and run/cancel a native inventory job. It is not the
full editor workspace. Credentials stay in tab memory and request headers;
they are excluded from URLs, browser storage and the probe report.

The [recorded Chromium 153 proof](m0-evidence/browser-linux-x64.json) passes
real pairing, cross-origin CORS, file read/save, an external-edit conflict
without overwriting disk, an actual VWB job, and forgetting the session. A
second [native bundle proof](m0-evidence/native-linux-x64.json) verifies the
isolated packaged interpreter, project I/O and native job.

The public `https://sfmth.github.io` test is **blocked**: navigation from this
environment returned `ERR_EMPTY_RESPONSE`. GitHub Pages was not enabled or
published during the proof. Neither the local HTTP pass nor the blocked HTTPS
attempt establishes Pages-to-loopback browser support. Firefox, Safari, other
OSes, denied permission, revocation/reconnect and the deployed UI remain gates.

## Reproduce

Install a pinned Playwright test dependency outside the source tree, then its
Chromium browser. Playwright is a developer test dependency, not a companion
installation requirement. For example, from the repository root:

```sh
npm install --prefix .m0-browser --no-audit --no-fund playwright@1.62.1
.m0-browser/node_modules/.bin/playwright install --with-deps chromium
node scripts/probe_browser.mjs \
  --playwright-module ../.m0-browser/node_modules/playwright \
  --output browser-evidence.json
```

The script creates a temporary project and real companion, serves the actual
static source from another loopback port, drives the UI and checks the file on
disk. It removes the temporary project and terminates its native processes.
Use `--python PATH` for a selected Python, `--executable PATH` for an installed
Chromium, and `--screenshot PATH` for visual inspection. No web-security/CORS
disable flags or request interception are used. Linux's headless test sandbox
is disabled for this development host; the companion API protections remain
active. The recorded local run used Chromium 153.0.8010.0 from the pinned
`@sparticuz/chromium@153.0.0` npm package, controlled by Playwright 1.62.1.

After the actual Pages site exists, append:

```sh
  --public-url https://sfmth.github.io/verilog-workbench/m0/
```

This additional probe navigates to the real HTTPS origin. It records the
without-grant attempt, explicitly grants the test context's local-network
permission through the browser API, then attempts pairing and authenticated
inventory. Deployment/document status and transport status are separate
fields. A blocked public test stays blocked; local UI success does not replace
it. A real user permission/retry flow still needs manual browser acceptance.

## GitHub Pages setup

[`pages.yml`](../.github/workflows/pages.yml) publishes the `web/` static
directory from `web-port-plan` using GitHub Actions on web-source pushes or
when dispatched. It does
not enable Pages administration or alter `master`. Repository Settings → Pages
must first select **GitHub Actions** as the build source. The connected GitHub
App exposes repository code operations but no Pages administration operation;
the browser session available here was signed out. Publication and acceptance
therefore remain pending. After enabling Pages, rerun the branch's Pages job
or push a web-source change. The push trigger permits deployment from this
feature branch without adding the workflow to `master`; manual dispatch UI
availability can still depend on a definition on the default branch.

For a local manual proof, serve `web/` with `python -m http.server 8000`, then
start `python -m companion --project /your/project --origin http://localhost:8000
--port 0`. Open `http://localhost:8000/m0/` and enter the startup address and
one-time code. Use a trusted, disposable project while evaluating this proof.
The native tools run with the user's own permissions.
