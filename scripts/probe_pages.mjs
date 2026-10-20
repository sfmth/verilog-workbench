#!/usr/bin/env node
// Acceptance of the actual HTTPS Pages deployment with a real local companion.
// Local-network permission is granted by Chromium's test API, not a manual prompt.
// The waveform is an explicit viewer fixture, not a native simulation result.
import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const args = {};
const allowed = new Set(['--public-url', '--python', '--playwright-module', '--executable', '--output', '--screenshot']);
for (let index = 2; index < process.argv.length; index += 2) {
  const name = process.argv[index], value = process.argv[index + 1];
  if (!allowed.has(name) || !value || value.startsWith('--')) throw new Error('Expected a supported --option followed by its value.');
  args[name] = value;
}
const publicUrl = new URL(args['--public-url'] || 'https://sfmth.github.io/verilog-workbench/workspace/');
assert.equal(publicUrl.protocol, 'https:', 'The public acceptance target must use HTTPS');
assert.ok(!publicUrl.username && !publicUrl.password && !publicUrl.search && !publicUrl.hash, 'The public URL must not contain credentials, query parameters or a fragment');
assert.ok(publicUrl.pathname.endsWith('/workspace/'), 'The public URL must end in /workspace/');
const origin = publicUrl.origin;
const siteUrl = new URL('../', publicUrl);
const prefix = siteUrl.pathname;
const python = args['--python'] || process.env.VWB_PYTHON || 'python3';
const {chromium} = require(args['--playwright-module'] || 'playwright');
const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'vwb-pages-'));
const project = path.join(temporary, 'project µ');
const topPath = path.join(project, 'src/top.v');
const helperRelative = 'src/helper_µ.v';
const helperPath = path.join(project, helperRelative);
const original = 'module top(input wire a, output wire y);\r\nassign y = a;\r\nendmodule\r\n';
const helper = 'module helper; endmodule\n';
const vcd = `$timescale 1ns $end
$scope module top $end
$var wire 1 ! clk $end
$var wire 4 " count [3:0] $end
$upscope $end
$enddefinitions $end
#0
0!
b0000 "
#5
1!
b0011 "
#10
0!
b1111 "
`;
const secrets = new Set([temporary, repo]);
const children = [];
const contexts = [];
const scriptErrors = [];
const assetErrors = [];
const assetResponses = [];
const loadedAssets = new Map();
const workers = [];
let browser, page;
const report = {
  schema: 'vwb.pages-browser-probe/1', recorded_at: new Date().toISOString(), state: 'failed',
  public_url: publicUrl.href, public_origin: origin, pages_prefix: prefix, checks: [],
  actual_pages_deployment: 'not_tested', native_job: 'not_run', waveform: 'not_run',
  hardware_tested: false, native_simulation: 'not_run',
  permission_grant: 'not_run', no_grant_result: {state: 'not_run'},
  note: 'Actual public HTTPS app, real temporary Unicode project and native inventory. Chromium test APIs simulate an explicit local-network permission grant. This does not test manual permission prompts, other browsers, native simulation or FPGA programming.',
};
const checked = name => report.checks.push(name);
const safe = value => {
  let message = String(value);
  for (const secret of secrets) if (secret) message = message.replaceAll(secret, '[redacted]');
  return message;
};

async function until(predicate, label, timeout = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error('Timed out: ' + label);
}

async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill();
  await new Promise(resolve => {
    const timer = setTimeout(() => { child.kill('SIGKILL'); resolve(); }, 5000);
    child.once('exit', () => { clearTimeout(timer); resolve(); });
  });
}

async function companion() {
  const child = spawn(python, ['-m', 'companion', '--project', project, '--origin', origin, '--port', '0'],
    {cwd: repo, stdio: ['ignore', 'pipe', 'pipe']});
  children.push(child);
  // Consume stderr without ever logging pairing material or machine-specific paths.
  let stderr = '';
  child.stderr.on('data', data => { stderr = (stderr + data).slice(-8000); });
  const ready = await new Promise((resolve, reject) => {
    let output = '', settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true; clearTimeout(timer);
      if (error) reject(error); else resolve(value);
    };
    const timer = setTimeout(() => finish(new Error('Companion startup timed out: ' + safe(stderr))), 15000);
    child.once('error', error => finish(error));
    child.once('exit', () => finish(new Error('Companion exited before startup: ' + safe(stderr))));
    child.stdout.on('data', data => {
      output += data;
      if (settled || !output.includes('\n')) return;
      try {
        const value = JSON.parse(output.split('\n')[0]);
        if (value.pairing_code) secrets.add(value.pairing_code);
        assert.equal(value.event, 'ready');
        assert.equal(new URL(value.url).hostname, '127.0.0.1');
        assert.equal(typeof value.pairing_code, 'string');
        finish(null, value);
      } catch { finish(new Error('Invalid companion startup message')); }
    });
  });
  return {child, ready};
}

function observe(targetPage, context) {
  targetPage.setDefaultTimeout(15000);
  targetPage.on('pageerror', error => scriptErrors.push(safe(error.message)));
  targetPage.on('worker', worker => workers.push(worker.url()));
  targetPage.on('dialog', dialog => dialog.accept());
  context.on('response', response => {
    const url = new URL(response.url());
    if (url.origin !== origin || !url.pathname.startsWith(prefix)) return;
    const relative = decodeURIComponent(url.pathname.slice(prefix.length));
    const asset = relative.endsWith('/') ? relative + 'index.html' : relative;
    if (asset === 'vwb-web-build.json' || asset.endsWith('favicon.ico')) return;
    assetResponses.push((async () => {
      if (response.status() !== 200) {
        assetErrors.push({path: asset, status: response.status()});
        return;
      }
      const digest = createHash('sha256').update(await response.body()).digest('hex');
      loadedAssets.set(asset, digest);
    })().catch(error => assetErrors.push({path: asset, error: safe(error.message)})));
  });
}

async function pair(ready) {
  await page.locator('#connect-toggle').click();
  await page.locator('#endpoint').fill(ready.url);
  await page.locator('#code').fill(ready.pairing_code);
  const responsePromise = page.waitForResponse(response => response.url() === ready.url + '/v1/pair');
  await page.locator('#pair').click();
  const response = await responsePromise;
  const data = await response.json();
  if (data.token) secrets.add(data.token);
  assert.equal(response.status(), 200, 'Pairing through the public app UI must succeed');
  assert.equal(data.protocol, 1, 'The real companion must negotiate protocol v1');
  assert.equal(typeof data.token, 'string');
  await until(() => page.locator('#refresh').isEnabled(), 'paired real project files');
  assert.equal(await page.locator('#code').inputValue(), '', 'The one-time code must be cleared after pairing');
  assert.equal(await page.locator('#connection-indicator').textContent(), 'Companion connected');
}

async function openFile(relative) {
  await page.locator('[data-explorer="files"]').click();
  await page.locator('#explorer button[data-path]').filter({hasText: relative}).click();
  await page.locator('#editor').waitFor({state: 'visible'});
  await until(async () => await page.locator('#active-file').textContent() === relative, 'active file ' + relative);
}

async function checkStorage() {
  const storage = await page.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}}));
  for (const secret of secrets) {
    assert.ok(!storage.includes(secret), 'Credentials or absolute project paths reached browser storage');
    assert.ok(!page.url().includes(secret), 'Credentials or absolute project paths reached the page URL');
  }
  assert.ok(!storage.includes('Bearer '), 'Bearer headers must stay out of browser storage');
  assert.ok(!storage.includes('public Pages edit'), 'Source recovery must require explicit opt-in');
}

async function screenshot() {
  if (!args['--screenshot'] || !page) return;
  // Mask the entire pairing dialog even on failures. Never capture entered codes.
  await page.screenshot({path: args['--screenshot'], fullPage: true, mask: [page.locator('#connect-dialog')]});
}

try {
  await fs.mkdir(path.join(project, 'src'), {recursive: true});
  await fs.mkdir(path.join(project, 'test'));
  await fs.writeFile(topPath, original);
  await fs.writeFile(helperPath, helper);
  const prepare = spawnSync(python, ['-c',
    'from pathlib import Path; import sys,vwb; root=Path(sys.argv[1]); vwb.Workbench(root,root/"src",root/"test",root/".vwb").prepare_build_dir()', project,
  ], {cwd: repo, encoding: 'utf8'});
  assert.equal(prepare.status, 0, 'VWB could not initialize the test build directory: ' + safe(prepare.stderr));
  await fs.writeFile(path.join(project, '.vwb/viewer-fixture.vcd'), vcd);
  let native = await companion();
  // No web-security or local-network bypass flags: permission uses the browser API.
  browser = await chromium.launch({headless: true, executablePath: args['--executable'], args: ['--no-sandbox']});
  report.browser = await browser.version();
  report.browser_security_flags = ['--no-sandbox'];

  const noGrantContext = await browser.newContext();
  contexts.push(noGrantContext);
  await noGrantContext.clearPermissions();
  const noGrantPage = await noGrantContext.newPage();
  const deniedNavigation = await noGrantPage.goto(publicUrl.href, {waitUntil: 'networkidle', timeout: 30000});
  assert.equal(deniedNavigation.status(), 200, 'The no-grant context must load the deployed page');
  report.no_grant_result = await noGrantPage.evaluate(async base => {
    try {
      const response = await fetch(base + '/v1/health', {mode: 'cors', credentials: 'omit',
        targetAddressSpace: 'loopback', signal: AbortSignal.timeout(8000)});
      return {state: response.ok ? 'allowed_without_test_grant' : 'http_error', http_status: response.status};
    } catch (error) { return {state: 'blocked_or_unavailable', error: error.name}; }
  }, native.ready.url);
  report.no_grant_result.setup = 'fresh-context-clearPermissions-no-pairing-attempt';
  report.no_grant_result.note = 'Observed result only. Clearing permissions does not guarantee a deny decision in every Chromium version or headless configuration.';
  checked('fresh-context-observed-loopback-health-without-test-permission-grant');
  await noGrantContext.close();

  const context = await browser.newContext({acceptDownloads: true, viewport: {width: 1512, height: 982}});
  contexts.push(context);
  await context.grantPermissions(['local-network-access'], {origin});
  report.permission_grant = 'explicit-simulated-Chromium-context-grantPermissions-local-network-access';
  page = await context.newPage();
  observe(page, context);
  const navigation = await page.goto(publicUrl.href + '#view=waveform', {waitUntil: 'networkidle', timeout: 30000});
  assert.equal(navigation.status(), 200, 'The actual HTTPS workspace must load');
  assert.equal(new URL(page.url()).origin, origin, 'The browser must remain at the public Pages origin');
  assert.equal(new URL(page.url()).pathname, publicUrl.pathname);
  assert.match(await page.title(), /Workspace.*Verilog Workbench/);
  assert.match(await page.locator('.brand').textContent(), /VERILOG WORKBENCH/);
  assert.equal((await page.locator('.preview-badge').textContent()).trim(), 'DEVELOPMENT PREVIEW');
  assert.equal(await page.locator('[data-result="waveform"]').getAttribute('aria-pressed'), 'true');
  const manifest = await page.evaluate(async url => {
    const response = await fetch(url, {cache: 'no-store'});
    if (!response.ok) throw new Error('Deployed build manifest HTTP ' + response.status);
    return response.json();
  }, new URL('vwb-web-build.json', siteUrl).href);
  assert.equal(manifest.schema, 'vwb.web-build/1');
  assert.equal(manifest.status, 'development-preview');
  assert.equal(manifest.base_path, prefix);
  report.actual_pages_deployment = 'public-HTTPS-workspace-and-development-preview-manifest-loaded';
  report.deployed_manifest = {schema: manifest.schema, status: manifest.status, base_path: manifest.base_path,
    entry_points: manifest.entry_points, file_count: Object.keys(manifest.files).length};
  checked('actual-HTTPS-Pages-app-brand-preview-manifest-and-fragment-route');

  await pair(native.ready);
  await openFile('src/top.v');
  assert.equal(await page.locator('#editor').inputValue(), original.replace(/\r\n/g, '\n'));
  const edited = original.replace(/\r\n/g, '\n') + '// public Pages edit µ\n';
  await page.locator('#editor').fill(edited);
  await page.locator('#save').click();
  await until(async () => await fs.readFile(topPath, 'utf8') === edited.replace(/\n/g, '\r\n'), 'exact CRLF public UI save');
  await openFile(helperRelative);
  await page.locator('#editor').fill(helper + '// public Pages edit helper µ\n');
  await page.keyboard.press('Control+s');
  await until(async () => await fs.readFile(helperPath, 'utf8') === helper + '// public Pages edit helper µ\n', 'Unicode path keyboard save');
  assert.match(await page.locator('#file-tabs').textContent(), /top\.v/);
  assert.ok((await page.locator('#file-tabs').textContent()).includes('helper_µ.v'));
  checked('real-public-app-UI-pairing-Unicode-project-multifile-CRLF-and-keyboard-save');

  await openFile('src/top.v');
  const external = edited.replace(/\n/g, '\r\n') + '// external editor\r\n';
  await fs.writeFile(topPath, external);
  await page.locator('#editor').fill(edited + '// conflicting browser edit\n');
  await page.locator('#save').click();
  await until(async () => /conflict/i.test(await page.locator('#file-status').textContent()), 'visible revision conflict');
  assert.equal(await fs.readFile(topPath, 'utf8'), external, 'The revision conflict must preserve the external disk change');
  await page.locator('#reload').click();
  await until(async () => await page.locator('#editor').inputValue() === external.replace(/\r\n/g, '\n'), 'explicit reload after conflict');
  checked('real-disk-revision-conflict-preserves-external-edit-and-explicit-reload');
  await checkStorage();

  const fragment = new URL(page.url()).hash;
  await page.reload({waitUntil: 'networkidle', timeout: 30000});
  assert.equal(new URL(page.url()).hash, fragment);
  assert.equal(await page.locator('#connection-indicator').textContent(), 'Disconnected');
  assert.ok(await page.locator('#run').isDisabled(), 'Page reload must forget the native session');
  await stop(native.child);
  native = await companion();
  await pair(native.ready);
  await openFile('src/top.v');
  assert.equal(await page.locator('#editor').inputValue(), external.replace(/\r\n/g, '\n'));
  checked('public-fragment-reload-forgets-session-and-new-one-time-UI-pairing-restores-file');

  await page.locator('#command').selectOption('list');
  await page.locator('#run').click();
  await until(async () => /list.*passed/i.test(await page.locator('#job-status').textContent()), 'real native list job', 30000);
  assert.match(await page.locator('#job-output').textContent(), /top/);
  report.native_job = 'passed-real-vwb-list-through-public-app-and-loopback-companion';
  checked('real-native-discovery-job-and-output-through-public-app');

  await page.locator('[data-result="artifacts"]').click();
  await page.locator('#refresh-artifacts').click();
  const artifact = page.locator('#artifact-list .artifact-row').filter({hasText: '.vwb/viewer-fixture.vcd'});
  await artifact.waitFor({state: 'visible'});
  const downloadEvent = page.waitForEvent('download');
  await artifact.getByRole('button', {name: /download/i}).click();
  const download = await downloadEvent;
  const downloadedPath = path.join(temporary, 'downloaded.vcd');
  await download.saveAs(downloadedPath);
  assert.equal(await fs.readFile(downloadedPath, 'utf8'), vcd, 'Companion artifact download must preserve exact fixture bytes');
  await artifact.getByRole('button', {name: /view/i}).click();
  await page.locator('#waveform-view svg').waitFor({state: 'visible'});
  assert.match(await page.locator('#waveform-view').textContent(), /top\.clk/);
  const workerUrl = new URL('vcd-worker.mjs', publicUrl).href;
  assert.ok(workers.includes(workerUrl), 'The waveform must use the worker loaded from the public deployment');
  await page.getByRole('combobox', {name: 'Waveform value radix'}).selectOption('unsigned');
  await page.getByRole('slider', {name: 'Waveform cursor in ticks'}).focus();
  await page.keyboard.press('End');
  await until(async () => (await page.locator('#waveform-view svg').getAttribute('aria-label')).includes('cursor at 10 ticks'), 'public-worker waveform cursor');
  report.waveform = 'passed-exact-viewer-fixture-artifact-download-public-worker-render-and-cursor';
  checked('exact-companion-viewer-fixture-download-and-public-VCD-worker-render');
  await checkStorage();

  await Promise.all(assetResponses);
  for (const asset of ['workspace/index.html', 'workspace/app.mjs', 'workspace/style.css', 'workspace/vcd-worker.mjs']) {
    assert.ok(loadedAssets.has(asset), 'The actual deployed browser did not load ' + asset);
  }
  for (const [asset, digest] of loadedAssets) {
    assert.equal(digest, manifest.files[asset], 'Delivered public asset must match its deployed build manifest: ' + asset);
  }
  assert.deepEqual(assetErrors, [], 'The public app requested missing or unreadable site assets');
  assert.deepEqual(scriptErrors, [], 'Unexpected script errors in the actual public app');
  report.verified_deployed_assets = Object.fromEntries([...loadedAssets].sort(([a], [b]) => a.localeCompare(b)));
  checked('deployed-document-styles-modules-and-worker-bytes-match-public-build-manifest');
  checked('no-browser-script-errors-or-stored-credentials');
  await page.locator('#waveform-view svg').scrollIntoViewIfNeeded();
  await screenshot();
  report.state = 'passed';
} catch (error) {
  report.error = safe(error?.stack || error);
  if (page) {
    report.visible_status = await page.locator('#global-status').textContent().then(safe).catch(() => null);
    report.connection_status = await page.locator('#connection-status').textContent().then(safe).catch(() => null);
    report.job_status = await page.locator('#job-status').textContent().then(safe).catch(() => null);
    report.job_output = await page.locator('#job-output').textContent().then(value => safe(value.slice(0, 12000))).catch(() => null);
    await screenshot().catch(() => {});
  }
} finally {
  report.script_errors = scriptErrors;
  report.asset_errors = assetErrors;
  report.workers = workers;
  for (const context of contexts) await context.close().catch(() => {});
  await browser?.close().catch(() => {});
  for (const child of children) await stop(child);
  await fs.rm(temporary, {recursive: true, force: true});
}
const json = safe(JSON.stringify(report, null, 2)) + '\n';
if (args['--output']) await fs.writeFile(args['--output'], json);
else process.stdout.write(json);
if (report.state !== 'passed') process.exitCode = 1;
