#!/usr/bin/env node
// Actual Project testbench controls -> paired companion -> original upstream
// Makefile/testbench/firmware. Static capture never replaces native responses.
import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import fs from 'node:fs/promises';
import http from 'node:http';
import {createRequire} from 'node:module';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {captureSiteAssets} from './browser_assets.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const allowed = new Set(['--project', '--output', '--flow', '--python', '--acquisition', '--playwright-module', '--executable', '--public-url', '--screenshot']);
const args = {};
for (let index = 2; index < process.argv.length; index += 2) {
  const key = process.argv[index], value = process.argv[index + 1];
  if (!allowed.has(key) || !value || value.startsWith('--') || args[key] !== undefined) throw new Error('Expected each supported --option once with its value.');
  args[key] = value;
}
assert.ok(args['--project'] && args['--output'], '--project and --output are required.');
const project = await fs.realpath(args['--project']);
const flowID = args['--flow'] || 'picorv32-upstream-regression';
assert.match(flowID, /^[a-z0-9][a-z0-9-]{0,95}$/);
const manifestBytes = await fs.readFile(path.join(project, '.vwb-flows.json'));
const manifest = JSON.parse(manifestBytes);
assert.ok(Array.isArray(manifest.source_files) && manifest.source_files.length > 0, 'Stage a fresh project with the complete original source-file snapshot.');
assert.equal(new Set(manifest.source_files).size, manifest.source_files.length);
const configured = manifest.flows.find(flow => flow.id === flowID);
assert.ok(configured && configured.project === 'picorv32', 'Stage the pinned original PicoRV32 project and select one of its declared flows.');
assert.equal(configured.driver, 'make');
assert.equal(configured.entrypoint, 'Makefile');
const publicURL = args['--public-url'] ? new URL(args['--public-url']) : null;
if (publicURL) {
  assert.equal(publicURL.protocol, 'https:');
  assert.ok(!publicURL.username && !publicURL.password && !publicURL.search && !publicURL.hash);
  assert.ok(publicURL.pathname.endsWith('/workspace/'));
}
const acquisition = args['--acquisition'] ? JSON.parse(await fs.readFile(args['--acquisition'], 'utf8')) : null;
const environment = {...process.env};
for (const key of ['LD_LIBRARY_PATH', 'PYTHONHOME', 'PYTHONPATH']) delete environment[key];
if (acquisition) {
  assert.equal(acquisition.schema_version, 'vwb.toolchain-acquisition/1');
  assert.equal(acquisition.state, 'acquired');
  Object.assign(environment, acquisition.environment);
}
const python = args['--python'] || acquisition?.probe?.python || process.env.VWB_PYTHON || 'python3';
const {chromium} = require(args['--playwright-module'] || 'playwright');
const prefix = publicURL ? new URL('../', publicURL).pathname : '/verilog-workbench/';
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const secrets = new Set([repo, project, acquisition?.suitePath].filter(Boolean));
function safe(value, maximum = 16000) {
  let text = String(value);
  for (const secret of secrets) text = text.replaceAll(secret, '[redacted]');
  return text.slice(0, maximum);
}
const report = {
  schema: 'vwb.original-upstream-workspace-probe/1', recorded_at: new Date().toISOString(), state: 'failed',
  target: publicURL ? 'public-HTTPS-workspace' : 'built-local-project-prefix-workspace',
  project: 'picorv32', flow: flowID, simulator: configured.simulator, checks: [],
  note: 'Real browser controls run the original upstream Makefile, testbench and GCC-built firmware through an authenticated companion. No fake tool, replacement testbench or substituted native response is used. PicoRV32 firmware/ISA regression coverage is determined by the pinned upstream bench.',
};
const checked = name => { report.checks.push(name); process.stderr.write('[upstream-ui] passed ' + name + '\n'); };
let server, native, browser, context, page, capture, endpoint, origin, workspaceURL;
const scriptErrors = [], assetErrors = [], assets = new Map(), requests = [], jobs = new Map(), readers = new Set();
async function until(predicate, label, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 60));
  }
  throw new Error('Timed out: ' + label);
}
async function originals() {
  const result = {};
  for (const name of [...manifest.source_files].sort()) {
    assert.equal(typeof name, 'string');
    assert.ok(name && !name.includes('\\') && !name.split('/').includes('..') && !path.isAbsolute(name));
    const file = path.join(project, name);
    assert.equal((await fs.lstat(file)).isFile(), true, 'An original input is a regular file: ' + name);
    result[name] = digest(await fs.readFile(file));
  }
  return result;
}
async function serveBuild() {
  const built = path.join(repo, 'dist/web');
  await fs.access(path.join(built, 'vwb-web-build.json'));
  const types = {'.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml'};
  server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://localhost');
      const pathname = decodeURIComponent(url.pathname);
      if (!pathname.startsWith(prefix)) throw new Error('Outside prefix');
      let target = path.resolve(built, pathname.slice(prefix.length));
      if (target !== built && !target.startsWith(built + path.sep)) throw new Error('Outside build');
      if ((await fs.stat(target)).isDirectory()) target = path.join(target, 'index.html');
      response.writeHead(200, {'Content-Type': types[path.extname(target)] || 'application/octet-stream', 'Cache-Control': 'no-store'});
      response.end(await fs.readFile(target));
    } catch {
      if (!request.url.includes('favicon.ico')) assetErrors.push({path: request.url, status: 404});
      response.writeHead(404); response.end('Not found');
    }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  origin = `http://127.0.0.1:${server.address().port}`;
  workspaceURL = origin + prefix + 'workspace/';
}
async function startCompanion() {
  native = spawn(python, ['-m', 'companion', '--project', project, '--origin', origin, '--port', '0'], {cwd: repo, env: environment, stdio: ['ignore', 'pipe', 'pipe']});
  let stderr = '';
  native.stderr.on('data', data => { stderr = (stderr + data).slice(-12000); });
  return new Promise((resolve, reject) => {
    let stdout = '', settled = false;
    const finish = (error, ready) => { if (settled) return; settled = true; clearTimeout(timer); error ? reject(error) : resolve(ready); };
    const timer = setTimeout(() => finish(new Error('Companion startup timed out: ' + safe(stderr))), 20000);
    native.once('error', error => finish(error));
    native.once('exit', () => finish(new Error('Companion exited during startup: ' + safe(stderr))));
    native.stdout.on('data', data => {
      stdout += data;
      if (settled || !stdout.includes('\n')) return;
      try {
        const ready = JSON.parse(stdout.split('\n')[0]);
        assert.equal(ready.event, 'ready'); assert.equal(new URL(ready.url).hostname, '127.0.0.1');
        secrets.add(ready.pairing_code); finish(null, ready);
      } catch { finish(new Error('Invalid companion startup message')); }
    });
  });
}
async function runUI(expectedOptions, timeout = 300) {
  const [response] = await Promise.all([
    page.waitForResponse(response => response.url() === endpoint + '/v1/jobs' && response.request().method() === 'POST'),
    page.locator('#run').click(),
  ]);
  const payload = response.request().postDataJSON(), created = await response.json();
  assert.equal(response.status(), 202, JSON.stringify(created));
  assert.deepEqual(payload, {command: 'flow', options: expectedOptions, timeout});
  let job;
  await until(async () => {
    const candidate = jobs.get(created.id);
    if (!candidate || !['passed', 'failed', 'timed_out', 'cancelled'].includes(candidate.status)) return false;
    if (!await page.locator('#run').isEnabled()) return false;
    if (!/^flow · (passed|failed|timed_out|cancelled)/.test(await page.locator('#job-status').textContent())) return false;
    job = candidate; return true;
  }, 'original project flow completion displayed by browser', (timeout + 30) * 1000);
  assert.equal(job.status, 'passed', safe(job.stdout + '\n' + job.stderr));
  assert.equal(job.returncode, 0);
  assert.equal(job.stdout_truncated, false); assert.equal(job.stderr_truncated, false);
  assert.equal(await page.locator('#job-output').textContent(), [job.stdout, job.stderr].filter(Boolean).join('\n') || 'No output.');
  return {payload, job};
}
async function download(relative) {
  const bytes = await fs.readFile(path.join(project, relative));
  const row = page.locator('#artifact-list .artifact-row').filter({has: page.getByText(relative, {exact: true})});
  await until(async () => await row.count() === 1, 'archived original-flow artifact ' + relative);
  const [item] = await Promise.all([page.waitForEvent('download'), row.getByRole('button', {name: 'Download', exact: true}).click()]);
  assert.equal(item.suggestedFilename(), path.basename(relative));
  const received = await fs.readFile(await item.path());
  assert.equal(received.length, bytes.length); assert.equal(digest(received), digest(bytes));
  return {path: relative, bytes: bytes.length, sha256: digest(bytes), browser_download: 'exact-native-file-bytes'};
}
try {
  const before = await originals();
  report.original_file_count = Object.keys(before).length;
  report.original_files_sha256 = digest(JSON.stringify(before));
  report.project_manifest_sha256 = digest(manifestBytes);
  const sourceLock = JSON.parse(await fs.readFile(path.join(repo, 'toolchains/upstream-hdl-lock.json'), 'utf8'));
  const sourcePin = sourceLock.projects.find(project => project.id === 'picorv32');
  report.upstream_source = {repository: sourcePin.repository, revision: sourcePin.revision, archive_sha256: sourcePin.archive.sha256,
    acquisition: 'Project staged by probe_upstream_testbenches.py from its integrity-verified source acquisition.'};
  const head = spawnSync('git', ['rev-parse', 'HEAD'], {cwd: repo, encoding: 'utf8'});
  if (head.status === 0) report.checkout = {head: head.stdout.trim(), source_state: 'Local working-tree acceptance before commit; source_sha256 identifies the tested files. This is not a claim that HEAD contains these changes.'};
  report.source_sha256 = {};
  for (const relative of ['scripts/probe_upstream_workspace.mjs', 'scripts/probe_upstream_testbenches.py', 'scripts/browser_assets.mjs', 'web/workspace/hdl.mjs', 'web/workspace/app.mjs', 'companion/flows.py', 'companion/server.py', 'companion/projects.py', 'vwb.py', 'upstream/benchmarks/core-flows.json', 'toolchains/upstream-hdl-lock.json']) report.source_sha256[relative] = digest(await fs.readFile(path.join(repo, relative)));
  if (publicURL) { origin = publicURL.origin; workspaceURL = publicURL.href; } else await serveBuild();
  const ready = await startCompanion(); endpoint = ready.url;
  browser = await chromium.launch({headless: true, executablePath: args['--executable'], args: ['--no-sandbox']});
  report.browser = await browser.version();
  context = await browser.newContext({acceptDownloads: true, viewport: {width: 1512, height: 1050}});
  if (publicURL) {
    await context.grantPermissions(['local-network-access'], {origin});
    report.permission_grant = 'explicit-simulated-Chromium-local-network-access';
  } else report.permission_grant = 'not-required-loopback-HTTP-origin';
  capture = await captureSiteAssets(context, {origin, prefix, onAsset: asset => assets.set(asset.path, asset.sha256), onError: error => assetErrors.push(error)});
  page = await context.newPage(); await capture.attachPage(page); page.setDefaultTimeout(30000);
  page.on('pageerror', error => scriptErrors.push(safe(error.message)));
  page.on('request', request => { if (request.url() === endpoint + '/v1/jobs' && request.method() === 'POST') requests.push(request.postDataJSON()); });
  page.on('response', response => {
    if (!response.url().startsWith(endpoint + '/v1/jobs/') || response.request().method() !== 'GET') return;
    const read = response.json().then(job => jobs.set(job.id, job)).catch(error => scriptErrors.push('Native job response: ' + safe(error.message)));
    readers.add(read); read.finally(() => readers.delete(read));
  });
  const navigation = await page.goto(workspaceURL, {waitUntil: 'networkidle', timeout: 45000}); assert.equal(navigation.status(), 200);
  await page.locator('#connect-toggle').click(); await page.locator('#endpoint').fill(endpoint); await page.locator('#code').fill(ready.pairing_code);
  const [pairResponse] = await Promise.all([page.waitForResponse(response => response.url() === endpoint + '/v1/pair'), page.locator('#pair').click()]);
  const paired = await pairResponse.json(); assert.equal(pairResponse.status(), 200); secrets.add(paired.token);
  await until(() => page.locator('#run').isEnabled(), 'paired project inventory');
  assert.equal(await page.locator('#code').inputValue(), ''); checked('authenticated-browser-pairing');
  await page.locator('#command').selectOption('flow');
  assert.equal(await page.locator('#flow-options').isVisible(), true);
  assert.equal(await page.locator('#module').isDisabled(), true);
  assert.equal(await page.locator('#test-options').isVisible(), false);
  assert.equal(await page.locator('#flow-timeout').inputValue(), '300');
  const listed = await runUI({list: true});
  const discovered = JSON.parse(listed.job.stdout);
  assert.ok(discovered.flows.some(flow => flow.id === flowID && flow.driver === 'make'));
  await until(() => page.locator('#flow-list option').evaluateAll((options, id) => options.some(option => option.value === id), flowID), 'discovered upstream flow in browser chooser');
  report.discovery = {request: listed.payload, flow_ids: discovered.flows.map(flow => flow.id)};
  checked('original-upstream-flow-discovery-and-chooser');
  const posted = requests.length;
  await page.locator('#flow-name').fill('invalid/flow'); await page.locator('#run').click();
  await until(async () => /named project flow/.test(await page.locator('#global-status').textContent()), 'invalid flow rejected before native submission');
  assert.equal(requests.length, posted); checked('invalid-flow-name-blocked-before-native-request');
  await page.locator('#flow-name').fill(flowID); await page.locator('#flow-simulator').selectOption('default');
  await page.locator('#flow-timeout').fill('300');
  const build = paired.project.build_dir;
  const reportRelative = path.posix.join(build, 'flows', flowID, 'report.json');
  const previousReport = await fs.stat(path.join(project, reportRelative), {bigint: true}).catch(() => null);
  const executionStarted = BigInt(Date.now()) * 1000000n;
  const executed = await runUI({flow: flowID});
  assert.equal(executed.job.stale, false, 'Generated firmware outputs must not change the original input snapshot.');
  assert.match(executed.job.stdout, /^ALL TESTS PASSED\.$/m);
  assert.match(executed.job.stdout, new RegExp('Upstream flow ' + flowID + ': passed'));
  const reportStat = await fs.stat(path.join(project, reportRelative), {bigint: true});
  assert.ok(reportStat.mtimeNs >= executionStarted, 'The browser run produced a fresh archived verdict.');
  assert.ok(!previousReport || reportStat.mtimeNs !== previousReport.mtimeNs, 'An earlier report cannot satisfy this browser run.');
  const nativeReport = JSON.parse(await fs.readFile(path.join(project, reportRelative), 'utf8'));
  assert.equal(nativeReport.id, flowID); assert.equal(nativeReport.state, 'passed'); assert.equal(nativeReport.simulator, configured.simulator);
  assert.equal(nativeReport.verdict.passed, true); assert.equal(nativeReport.verdict.kind, 'log'); assert.deepEqual(nativeReport.verdict.missing_patterns, []);
  const log = nativeReport.steps.map(step => step.output || '').join('\n');
  assert.match(log, /^ALL TESTS PASSED\.$/m);
  assert.deepEqual(nativeReport.steps[0].argv, configured.argv);
  report.execution = {request: executed.payload, status: executed.job.status, returncode: executed.job.returncode, stale: executed.job.stale, native_state: nativeReport.state, simulator: nativeReport.simulator, verdict: nativeReport.verdict, original_driver_argv: nativeReport.steps[0].argv, log_sha256: digest(log), duration_seconds: nativeReport.duration_seconds};
  checked('original-Makefile-testbench-and-GCC-firmware-pass-through-browser');
  await page.locator('[data-result="artifacts"]').click();
  assert.equal(reportRelative, path.posix.join(build, nativeReport.report_artifact));
  const logRelative = path.posix.join(build, 'flows', flowID, 'run.log');
  report.artifacts = [await download(reportRelative), await download(logRelative)];
  assert.deepEqual(JSON.parse(await fs.readFile(path.join(project, reportRelative), 'utf8')), nativeReport);
  assert.match(await fs.readFile(path.join(project, logRelative), 'utf8'), /^ALL TESTS PASSED\.$/m);
  checked('native-verdict-and-original-test-log-downloaded-with-exact-file-hashes');
  const after = await originals();
  report.original_files_changed = Object.keys(before).filter(name => before[name] !== after[name]);
  assert.deepEqual(report.original_files_changed, []);
  assert.equal(digest(await fs.readFile(path.join(project, '.vwb-flows.json'))), report.project_manifest_sha256);
  checked('original-source-testbench-and-driver-files-unchanged');
  const deployed = await page.evaluate(async url => { const response = await fetch(url, {cache: 'no-store'}); if (!response.ok) throw new Error('Manifest request failed'); return response.json(); }, origin + prefix + 'vwb-web-build.json');
  await capture.drain(); await until(() => readers.size === 0, 'native response readers drained');
  assert.equal(deployed.schema, 'vwb.web-build/1'); assert.equal(deployed.base_path, prefix);
  for (const relative of ['workspace/index.html', 'workspace/app.mjs', 'workspace/hdl.mjs', 'workspace/style.css']) assert.equal(assets.get(relative), deployed.files[relative], 'Browser loaded the declared build asset: ' + relative);
  for (const [relative, hash] of assets) assert.equal(hash, deployed.files[relative], 'Original browser response matched manifest: ' + relative);
  report.loaded_runtime_sha256 = Object.fromEntries(assets); report.asset_capture = capture.summary();
  checked('original-static-response-bytes-match-built-manifest');
  const storage = await page.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}}));
  for (const secret of secrets) { assert.ok(!storage.includes(secret)); assert.ok(!page.url().includes(secret)); }
  assert.deepEqual(scriptErrors, []); assert.deepEqual(assetErrors, []);
  if (args['--screenshot']) await page.screenshot({path: args['--screenshot'], fullPage: true, mask: [page.locator('#connect-dialog')]});
  report.state = 'passed';
} catch (error) {
  report.error = safe(error.stack || error.message);
  if (page) report.visible_output = safe(await page.locator('#job-output').textContent().catch(() => ''));
} finally {
  report.script_errors = scriptErrors; report.asset_errors = assetErrors;
  await Promise.all([...readers]);
  await capture?.close().catch(error => { report.state = 'failed'; report.capture_error = safe(error.message); });
  await context?.close().catch(() => {}); await browser?.close().catch(() => {});
  if (native && native.exitCode === null && native.signalCode === null) {
    await new Promise(resolve => { const timer = setTimeout(() => { native.kill('SIGKILL'); resolve(); }, 5000); native.once('exit', () => { clearTimeout(timer); resolve(); }); native.kill(); });
  }
  if (server) await new Promise(resolve => server.close(resolve));
}
await fs.mkdir(path.dirname(path.resolve(args['--output'])), {recursive: true});
await fs.writeFile(args['--output'], safe(JSON.stringify(report, null, 2), 1500000) + '\n');
if (report.state !== 'passed') { process.stderr.write(safe(report.error || report.capture_error) + '\n'); process.exitCode = 1; }
