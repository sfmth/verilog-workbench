#!/usr/bin/env node
// Real-browser acceptance of the built Pages preview and a native companion.
// The VCD is an explicit viewer fixture; no HDL engine or hardware pass is implied.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {captureSiteAssets} from './browser_assets.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const args = {};
const allowed = new Set(['--playwright-module', '--executable', '--output', '--screenshot', '--python', '--native-sim']);
for (let index = 2; index < process.argv.length; index += 2) {
  const name = process.argv[index], value = process.argv[index + 1];
  if (!allowed.has(name) || !value || value.startsWith('--')) throw new Error('Expected a supported --option followed by its value.');
  args[name] = value;
}
if (args['--native-sim'] !== undefined && !['true', 'false'].includes(args['--native-sim'])) throw new Error('--native-sim must be true or false.');
const nativeSimulation = args['--native-sim'] === 'true';
const {chromium} = require(args['--playwright-module'] || 'playwright');
const prefix = '/verilog-workbench/';
const built = path.join(repo, 'dist/web');
const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'vwb-workspace-'));
const project = path.join(temporary, 'project µ');
const topPath = path.join(project, 'src/top.v');
const helperPath = path.join(project, 'src/helper.v');
const testPath = path.join(project, 'test/test_top.v');
const original = 'module top(input wire a, output wire y);\r\nassign y = a;\r\nendmodule\r\n';
const helper = 'module helper; endmodule\n';
const nativeBench = '`timescale 1ns/1ps\nmodule test_top;\nreg a = 0; wire y;\ntop dut(.a(a), .y(y));\ninitial begin\n #1; if (y !== 0) $fatal(1, "low assertion failed");\n a = 1; #5; if (y !== 1) $fatal(1, "high assertion failed");\n $display("VWB_BROWSER_NATIVE_PASS"); $finish;\nend\nendmodule\n';
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
const secrets = new Set();
const children = [];
const staticErrors = [];
const scriptErrors = [];
const assetErrors = [];
const workers = [];
let browser, staticServer, page, assetCapture;
const report = {
  schema: 'vwb.workspace-browser-probe/1', recorded_at: new Date().toISOString(),
  state: 'failed', checks: [], pages_prefix: prefix, native_job: 'not_run',
  waveform: 'not_run', actual_pages_deployment: 'not_tested', hardware_tested: false,
  native_simulation: 'not_run', native_failure: 'not_run', native_cancellation: 'not_run',
  note: 'Built local preview and native inventory acceptance; fixture VCD is not a simulation or FPGA programming result.',
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

async function bounded(promise, label, timeout = 15000) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Timed out: ' + label)), timeout);
    })]);
  } finally { clearTimeout(timer); }
}

async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill();
  await new Promise(resolve => {
    const timer = setTimeout(() => { child.kill('SIGKILL'); resolve(); }, 5000);
    child.once('exit', () => { clearTimeout(timer); resolve(); });
  });
}

async function companion(origin, projectRoot = project) {
  const child = spawn(args['--python'] || process.env.VWB_PYTHON || 'python3', [
    '-m', 'companion', '--project', projectRoot, '--origin', origin, '--port', '0',
  ], {cwd: repo, stdio: ['ignore', 'pipe', 'pipe']});
  children.push(child);
  const ready = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('Companion startup timed out')), 15000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.stdout.on('data', data => {
      output += data;
      if (!output.includes('\n')) return;
      clearTimeout(timer);
      try { resolve(JSON.parse(output.split('\n')[0])); }
      catch { reject(new Error('Invalid companion startup message')); }
    });
    child.once('exit', () => { clearTimeout(timer); reject(new Error('Companion exited before startup')); });
  });
  secrets.add(ready.pairing_code);
  return {child, ready};
}

async function pair(ready) {
  await page.locator('#connect-toggle').click();
  await page.locator('#endpoint').fill(ready.url);
  await page.locator('#code').fill(ready.pairing_code);
  const response = page.waitForResponse(response => response.url() === ready.url + '/v1/pair');
  await page.locator('#pair').click();
  const data = await (await response).json();
  assert.equal(data.protocol, 1, 'The real companion must negotiate protocol v1');
  assert.equal(typeof data.token, 'string');
  secrets.add(data.token);
  await page.locator('#refresh').waitFor({state: 'visible'});
  await until(() => page.locator('#refresh').isEnabled(), 'paired project files');
  assert.equal(await page.locator('#code').inputValue(), '', 'Pairing code must be cleared from the form');
}

async function openFile(relative) {
  await page.locator('[data-explorer="files"]').click();
  const explicit = page.locator(`#explorer button[data-path="${relative}"]`);
  if (await explicit.count()) await explicit.click();
  else await page.locator('#explorer').getByRole('button', {name: new RegExp(relative.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))}).first().click();
  await page.locator('#editor').waitFor({state: 'visible'});
  await until(async () => (await page.locator('#active-file').textContent()).includes(relative), 'active file ' + relative);
}

async function restoreDraft(relative) {
  const explicit = page.locator(`#recovery-list button[data-path="${relative}"]`);
  await explicit.waitFor({state: 'visible'});
  await explicit.click();
  await explicit.waitFor({state: 'detached'});
  await page.locator('#editor').waitFor({state: 'visible'});
}

async function checkStorage({draftExpected = false} = {}) {
  const stored = await page.evaluate(() => ({local: {...localStorage}, session: {...sessionStorage}}));
  const serialized = JSON.stringify(stored);
  for (const secret of secrets) {
    assert.ok(!serialized.includes(secret), 'A session credential or pairing code reached browser storage');
    assert.ok(!page.url().includes(secret), 'A credential reached the page URL');
  }
  assert.ok(!serialized.includes('Bearer '), 'Bearer headers must stay out of browser storage');
  if (draftExpected) assert.ok(serialized.includes('recovery draft'), 'Opted-in source recovery must be retained');
  else assert.ok(!serialized.includes('recovery draft'), 'Source recovery requires explicit opt-in');
}

try {
  await fs.access(path.join(built, 'vwb-web-build.json'));
  await fs.mkdir(path.join(project, 'src'), {recursive: true});
  await fs.mkdir(path.join(project, 'test'));
  await fs.mkdir(path.join(project, '.vwb'));
  await fs.writeFile(topPath, original);
  await fs.writeFile(helperPath, helper);
  if (nativeSimulation) {
    const version = spawnSync('iverilog', ['-V'], {encoding: 'utf8'});
    assert.equal(version.status, 0, '--native-sim requires a working native Icarus install');
    report.iverilog_version = version.stdout.split('\n')[0];
    await fs.writeFile(testPath, nativeBench);
  }
  // Let VWB establish its real build ownership marker before adding viewer
  // fixtures. A nonempty unowned build directory is deliberately rejected.
  const prepare = spawnSync(args['--python'] || process.env.VWB_PYTHON || 'python3', [
    '-c', 'from pathlib import Path; import sys,vwb; root=Path(sys.argv[1]); vwb.Workbench(root,root/"src",root/"test",root/".vwb").prepare_build_dir()', project,
  ], {cwd: repo, encoding: 'utf8'});
  assert.equal(prepare.status, 0, 'VWB could not initialize the test project build directory: ' + safe(prepare.stderr));
  await fs.writeFile(path.join(project, '.vwb/fixture.vcd'), vcd);
  await fs.writeFile(path.join(project, '.vwb/notes.txt'), 'Viewer fixture; not an HDL engine result.\n');
  const mime = {'.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.json': 'application/json'};
  staticServer = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      if (!pathname.startsWith(prefix)) throw new Error('Outside project prefix');
      let target = path.resolve(built, pathname.slice(prefix.length));
      if (target !== built && !target.startsWith(built + path.sep)) throw new Error('Path outside build output');
      if ((await fs.stat(target)).isDirectory()) target = path.join(target, 'index.html');
      const data = await fs.readFile(target);
      response.writeHead(200, {'Content-Type': mime[path.extname(target)] || 'application/octet-stream', 'Cache-Control': 'no-store'});
      response.end(data);
    } catch {
      if (!request.url.includes('favicon.ico')) staticErrors.push(request.url);
      response.writeHead(404); response.end('Not found');
    }
  });
  await new Promise((resolve, reject) => {
    staticServer.once('error', reject);
    staticServer.listen(0, '127.0.0.1', resolve);
  });
  const origin = `http://127.0.0.1:${staticServer.address().port}`;
  const publicPath = origin + prefix;
  let native = await companion(origin);
  browser = await chromium.launch({headless: true, executablePath: args['--executable'], args: ['--no-sandbox']});
  report.browser = await browser.version();
  const context = await browser.newContext({acceptDownloads: true, viewport: {width: 1512, height: 982}});
  assetCapture = await captureSiteAssets(context, {origin, prefix,
    onError: error => assetErrors.push({...error, ...(error.error ? {error: safe(error.error)} : {})}),
  });
  page = await context.newPage();
  await assetCapture.attachPage(page);
  page.setDefaultTimeout(15000);
  page.on('pageerror', error => scriptErrors.push(safe(error.message)));
  page.on('worker', worker => workers.push(worker.url()));
  page.on('dialog', dialog => dialog.accept());
  const root = await page.goto(publicPath, {waitUntil: 'networkidle'});
  assert.equal(root.status(), 200);
  await page.locator('a[href="workspace/"]').first().click();
  await page.waitForURL(publicPath + 'workspace/**');
  await page.goto(publicPath + 'workspace/#view=waveform', {waitUntil: 'networkidle'});
  await page.reload({waitUntil: 'networkidle'});
  assert.equal(page.url(), publicPath + 'workspace/#view=waveform');
  assert.equal(await page.locator('[data-result="waveform"]').getAttribute('aria-pressed'), 'true');
  checked('built-root-project-prefix-and-fragment-reload');

  await pair(native.ready);
  await openFile('src/top.v');
  assert.equal(await page.locator('#editor').inputValue(), original.replace(/\r\n/g, '\n'));
  const edited = original.replace(/\r\n/g, '\n') + '// browser save µ\n';
  await page.locator('#editor').fill(edited);
  await page.locator('#save').click();
  await until(async () => await fs.readFile(topPath, 'utf8') === edited.replace(/\n/g, '\r\n'), 'exact CRLF disk save');
  await openFile('src/helper.v');
  await page.locator('#editor').fill(helper + '// keyboard save\n');
  await page.keyboard.press('Control+s');
  await until(async () => await fs.readFile(helperPath, 'utf8') === helper + '// keyboard save\n', 'keyboard save');
  assert.ok((await page.locator('#file-tabs').textContent()).includes('top.v'));
  assert.ok((await page.locator('#file-tabs').textContent()).includes('helper.v'));
  checked('real-pairing-multifile-tabs-revision-save-CRLF-and-keyboard');

  await openFile('src/top.v');
  const external = edited.replace(/\n/g, '\r\n') + '// external editor\r\n';
  await fs.writeFile(topPath, external);
  await page.locator('#editor').fill(edited + '// conflicting browser edit\n');
  await page.locator('#save').click();
  await until(async () => /conflict|changed|outside/i.test(await page.locator('#file-status').textContent()), 'visible external save conflict');
  assert.equal(await fs.readFile(topPath, 'utf8'), external, 'Conflict must not overwrite external edits');
  await page.locator('#reload').click();
  await until(async () => (await page.locator('#editor').inputValue()).includes('// external editor'), 'reload after conflict');
  checked('external-change-conflict-without-overwrite');

  const recovery = external.replace(/\r\n/g, '\n') + '// recovery draft µ\n';
  await page.locator('#editor').fill(recovery);
  await checkStorage();
  const exportEvent = page.waitForEvent('download');
  await page.locator('#export-drafts').click();
  const exported = await exportEvent;
  const exportPath = path.join(temporary, 'drafts.json');
  await exported.saveAs(exportPath);
  const exportText = await fs.readFile(exportPath, 'utf8');
  const exportData = JSON.parse(exportText);
  assert.equal(exportData.schema, 'vwb.drafts/1');
  assert.equal(exportData.files.find(file => file.path === 'src/top.v').content, recovery.replace(/\n/g, '\r\n'));
  for (const secret of secrets) assert.ok(!exportText.includes(secret), 'Credentials must not appear in draft exports');
  await page.locator('#persist-drafts').check();
  await until(async () => (await page.evaluate(() => JSON.stringify({...localStorage}))).includes('recovery draft'), 'opted-in recovery storage');
  await checkStorage({draftExpected: true});
  assert.equal(await fs.readFile(topPath, 'utf8'), external, 'Recovery must not write project files');
  const draftHash = new URL(page.url()).hash;
  assert.equal(new URLSearchParams(draftHash.slice(1)).get('file'), 'src/top.v');
  await page.reload({waitUntil: 'networkidle'});
  assert.equal(new URL(page.url()).hash, draftHash, 'Reload must preserve the requested file while disconnected');
  await stop(native.child);
  native = await companion(origin);
  await pair(native.ready);
  await page.locator('#persist-drafts').check();
  await restoreDraft('src/top.v');
  assert.equal(await page.locator('#editor').inputValue(), recovery);
  assert.equal(await fs.readFile(topPath, 'utf8'), external, 'Restoring a draft requires an explicit later save');
  await page.locator('#reload').click();
  await until(async () => await page.locator('#editor').inputValue() === external.replace(/\r\n/g, '\n'), 'discard recovered draft');
  await page.locator('#import-drafts').setInputFiles(exportPath);
  await restoreDraft('src/top.v');
  assert.equal(await page.locator('#editor').inputValue(), recovery, 'Imported draft must restore exact text');
  await checkStorage({draftExpected: true});
  await page.locator('#save').click();
  await until(async () => await fs.readFile(topPath, 'utf8') === recovery.replace(/\n/g, '\r\n'), 'save restored export');
  checked('draft-export-import-explicit-recovery-reload-and-no-stored-credentials');

  await page.locator('#command').selectOption('list');
  await page.locator('#run').click();
  await until(async () => /passed/i.test(await page.locator('#job-status').textContent()), 'real native inventory job', 30000);
  assert.ok((await page.locator('#job-output').textContent()).includes('top'), 'Native discovery must contain the real fixture module');
  report.native_job = 'passed-real-vwb-list';
  checked('native-companion-discover-job');

  await openFile('src/top.v');
  const staleSaved = await page.locator('#editor').inputValue() + '// saved stale-result check\n';
  await page.locator('#editor').fill(staleSaved);
  await page.locator('#save').click();
  await until(async () => await fs.readFile(topPath, 'utf8') === staleSaved.replace(/\n/g, '\r\n'), 'saved edit after completed job');
  await page.locator('#editor').fill(staleSaved + '// transient edit\n');
  await page.locator('#editor').fill(staleSaved);
  assert.ok(await page.locator('#save').isDisabled(), 'Returning to saved text must clear dirty state');
  assert.match(await page.locator('#job-status').textContent(), /STALE/, 'Saving and reverting edits must not revalidate an earlier job');
  checked('saved-and-reverted-edits-retain-stale-job-status');

  await page.locator('[data-result="artifacts"]').click();
  await page.locator('#refresh-artifacts').click();
  const artifact = page.locator('#artifact-list .artifact-row').filter({hasText: '.vwb/fixture.vcd'});
  await artifact.waitFor({state: 'visible'});
  const artifactDownload = page.waitForEvent('download');
  await artifact.getByRole('button', {name: /download/i}).click();
  const downloaded = await artifactDownload;
  const artifactPath = path.join(temporary, 'downloaded.vcd');
  await downloaded.saveAs(artifactPath);
  assert.equal(await fs.readFile(artifactPath, 'utf8'), vcd, 'Artifact download must preserve exact companion bytes');
  await artifact.getByRole('button', {name: /waveform|open|view/i}).click();
  await page.locator('#waveform-view').locator('svg, canvas').first().waitFor({state: 'visible'});
  assert.ok((await page.locator('#waveform-view').textContent()).includes('top.clk'));
  assert.ok(workers.some(url => url.includes(prefix + 'workspace/vcd-worker.mjs')), 'VCD parsing must use the built worker under the project prefix');
  await page.locator('#import-vcd').setInputFiles({name: 'broken.vcd', mimeType: 'text/plain', buffer: Buffer.from('$var wire 1 ! bad $end\n')});
  await until(async () => /missing|invalid|unable|error|failed/i.test(await page.locator('#waveform-view').textContent()), 'malformed waveform error');
  await page.locator('#import-vcd').setInputFiles({name: 'fixture.vcd', mimeType: 'text/plain', buffer: Buffer.from(vcd)});
  await page.locator('#waveform-view').locator('svg, canvas').first().waitFor({state: 'visible'});
  await page.getByRole('combobox', {name: 'Waveform value radix'}).selectOption('unsigned');
  await page.getByRole('slider', {name: 'Waveform cursor in ticks'}).focus();
  await page.keyboard.press('End');
  await until(async () => (await page.locator('#waveform-view svg').getAttribute('aria-label')).includes('cursor at 10 ticks'), 'keyboard waveform cursor');
  await page.getByRole('button', {name: 'Zoom in', exact: true}).click();
  await page.getByRole('button', {name: 'Fit trace', exact: true}).click();
  report.waveform = 'passed-explicit-fixture-worker-render-download-and-malformed-input';
  checked('artifact-download-vcd-worker-render-and-malformed-recovery');

  if (nativeSimulation) {
    await openFile('test/test_top.v');
    await page.locator('#command').selectOption('test');
    await page.locator('#module').fill('top');
    await page.locator('#test-language').selectOption('verilog');
    await page.locator('#waves').check();
    await page.locator('#run').click();
    await until(async () => /test.*passed/i.test(await page.locator('#job-status').textContent()), 'native Icarus simulation pass', 30000);
    assert.match(await page.locator('#job-output').textContent(), /RTL PASS/, 'The real assertion-backed testbench must pass');
    await until(() => page.locator('#run').isEnabled(), 'native simulation completion');
    await page.locator('[data-result="artifacts"]').click();
    await page.locator('#refresh-artifacts').click();
    const realWave = page.locator('#artifact-list .artifact-row').filter({hasText: /\/top\.vcd/});
    await realWave.waitFor({state: 'visible'});
    const downloadEvent = page.waitForEvent('download');
    await realWave.getByRole('button', {name: /download/i}).click();
    const waveDownload = await downloadEvent;
    const generatedWave = path.join(temporary, 'native-top.vcd');
    await waveDownload.saveAs(generatedWave);
    const waveText = await fs.readFile(generatedWave, 'utf8');
    assert.ok(waveText.includes('$enddefinitions') && waveText.includes('$var'));
    await realWave.getByRole('button', {name: /view/i}).click();
    await page.locator('#waveform-view svg').waitFor({state: 'visible'});
    assert.ok((await page.locator('#waveform-view').textContent()).includes('test_top'));
    report.native_simulation = 'passed-real-Icarus-assertions-and-generated-VCD';
    checked('native-Icarus-pass-assertions-generated-VCD-download-and-worker-render');

    await page.locator('#editor').fill('module test_top;\ninitial begin\n this is deliberately invalid Verilog;\nend\nendmodule\n');
    await page.locator('#run').click();
    await until(async () => /test.*failed/i.test(await page.locator('#job-status').textContent()), 'real compiler syntax failure', 30000);
    const failedOutput = await page.locator('#job-output').textContent();
    assert.match(failedOutput, /syntax|error/i, 'Failed compiler output must remain available');
    assert.ok(failedOutput.includes('test_top.v'), 'Native failure must retain its source path');
    await until(() => page.locator('#run').isEnabled(), 'compiler failure completion');
    report.native_failure = 'passed-real-compiler-failure-with-raw-source-diagnostics';
    checked('native-compiler-failure-and-raw-diagnostics');

    await page.locator('#editor').fill('`timescale 1ns/1ps\nmodule test_top;\nreg a = 0; wire y; integer marker;\ntop dut(.a(a), .y(y));\ninitial begin\n marker = $fopen("native-cancel-started.txt", "w");\n $fdisplay(marker, "real vvp running"); $fclose(marker);\n forever #1 a = ~a;\nend\nendmodule\n');
    await page.locator('#waves').uncheck();
    await page.locator('#run').click();
    await until(async () => (await fs.readdir(path.join(project, '.vwb'), {recursive: true})).some(name => name.endsWith('native-cancel-started.txt')), 'real vvp runtime cancellation marker', 30000);
    await page.locator('#cancel').click();
    await until(async () => /test.*cancelled/i.test(await page.locator('#job-status').textContent()), 'native simulation cancellation', 30000);
    await until(() => page.locator('#run').isEnabled(), 'cancellation cleanup');
    report.native_cancellation = 'passed-real-vvp-runtime-stop-and-terminal-cancelled';
    checked('native-infinite-simulation-stop-after-runtime-marker');

    await page.locator('#editor').fill(nativeBench);
    await page.locator('#waves').check();
    await page.locator('#run').click();
    await until(async () => /test.*passed/i.test(await page.locator('#job-status').textContent()), 'native simulation recovery after failure and cancellation', 30000);
    await until(() => page.locator('#run').isEnabled(), 'recovered simulation completion');
    await page.locator('[data-result="waveform"]').click();
    checked('native-simulation-recovery-after-failure-and-cancellation');
  }
  await page.locator('#guide-toggle').click();
  await page.locator('#guide').waitFor({state: 'visible'});
  await page.locator('#guide-close').click();
  checked('optional-guide-open-and-close');
  await checkStorage();
  await page.locator('#waveform-view svg').scrollIntoViewIfNeeded();
  if (args['--screenshot']) await page.screenshot({path: args['--screenshot'], fullPage: true});

  // Only these delayed responses are transport fault injection. Both project
  // grants, their pairing and the final new-project job use real companions.
  const secondProject = path.join(temporary, 'different project µ');
  await fs.mkdir(path.join(secondProject, 'src'), {recursive: true});
  await fs.mkdir(path.join(secondProject, 'test'));
  await fs.writeFile(path.join(secondProject, 'src/second.v'), 'module second; endmodule\n');
  const secondNative = await companion(origin, secondProject);
  const oldOrigin = new URL(native.ready.url).origin;
  const capabilityMatch = url => url.origin === oldOrigin && url.pathname === '/v1/capabilities';
  const artifactMatch = url => url.origin === oldOrigin && url.pathname === '/v1/artifacts/file';
  let holdCapabilities, holdArtifact;
  const capabilityHeld = new Promise(resolve => { holdCapabilities = resolve; });
  const artifactHeld = new Promise(resolve => { holdArtifact = resolve; });
  await page.route(capabilityMatch, route => {
    if (route.request().method() !== 'GET') return route.continue();
    holdCapabilities(route);
  });
  await page.route(artifactMatch, route => {
    if (route.request().method() !== 'GET') return route.continue();
    holdArtifact(route);
  });
  await page.locator('[data-result="tools"]').click();
  await page.locator('#check-tools').click();
  const capabilityRoute = await bounded(capabilityHeld, 'intercept old capability request');
  await page.locator('[data-result="artifacts"]').click();
  await page.locator('#artifact-list .artifact-row').filter({hasText: '.vwb/fixture.vcd'}).getByRole('button', {name: 'View', exact: true}).click();
  const artifactRoute = await bounded(artifactHeld, 'intercept old artifact request');
  await pair(secondNative.ready);
  await page.locator('#explorer button[data-path="src/second.v"]').waitFor({state: 'visible'});
  assert.equal(await page.locator('#job-status').textContent(), 'No job submitted');
  assert.equal(await page.locator('#job-output').textContent(), 'No job submitted for this project.');
  assert.equal(await page.locator('#tools-output').textContent(), 'No capability report loaded.');
  assert.equal(await page.locator('#artifact-list .artifact-row').count(), 0);
  assert.equal(await page.locator('#waveform-view svg').count(), 0);
  assert.equal(await page.locator('#module').inputValue(), '');
  const finishedCapability = page.waitForEvent('requestfinished', {predicate: request => request.url() === capabilityRoute.request().url()});
  const finishedArtifact = page.waitForEvent('requestfinished', {predicate: request => request.url() === artifactRoute.request().url()});
  const cors = {'Access-Control-Allow-Origin': origin, 'Content-Type': 'application/json'};
  await capabilityRoute.fulfill({status: 200, headers: cors, body: JSON.stringify({oldSessionSentinel: 'SIMULATED_OLD_TOOL_RESPONSE'})});
  await artifactRoute.fulfill({status: 401, headers: cors, body: JSON.stringify({error: 'SIMULATED_OLD_ARTIFACT_AUTH_FAILURE'})});
  await Promise.all([finishedCapability, finishedArtifact]);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.locator('#tools-output').textContent(), 'No capability report loaded.', 'Delayed old capabilities must not overwrite the new project');
  assert.equal(await page.locator('#connection-indicator').textContent(), 'Companion connected', 'Delayed old 401 must not disconnect the new project');
  assert.ok(!(await page.locator('#global-status').textContent()).includes('SIMULATED_OLD'));
  assert.ok(await page.locator('#run').isEnabled());
  await page.unroute(capabilityMatch);
  await page.unroute(artifactMatch);
  await page.locator('#command').selectOption('list');
  await page.locator('#run').click();
  await until(async () => /list.*passed/i.test(await page.locator('#job-status').textContent()), 'real second-project inventory after late responses', 30000);
  assert.ok((await page.locator('#job-output').textContent()).includes('second'));
  report.session_isolation = 'passed-distinct-real-projects-with-simulated-delayed-old-tool-success-and-artifact401';
  checked('distinct-project-clears-results-and-ignores-simulated-late-old-session-responses');
  await checkStorage();
  await assetCapture.drain();
  assert.deepEqual(staticErrors, [], 'Built preview requested missing assets or paths outside the project prefix');
  assert.deepEqual(assetErrors, [], 'Built preview static responses must remain available and successful');
  assert.deepEqual(scriptErrors, [], 'Unexpected browser script errors');
  report.state = 'passed';
} catch (error) {
  report.error = safe(error?.stack || error);
  if (page) {
    report.visible_status = await page.locator('#global-status').textContent().catch(() => null);
    report.job_status = await page.locator('#job-status').textContent().catch(() => null);
    report.job_output = await page.locator('#job-output').textContent().then(text => safe(text.slice(0, 12000))).catch(() => null);
    if (args['--screenshot']) await page.screenshot({path: args['--screenshot'], fullPage: true}).catch(() => {});
  }
} finally {
  try { await assetCapture?.close(); }
  catch (error) { assetErrors.push({phase: 'close-static-response-capture', error: safe(error.message)}); }
  await browser?.close();
  for (const child of children) await stop(child);
  if (staticServer) await new Promise(resolve => staticServer.close(resolve));
  await fs.rm(temporary, {recursive: true, force: true});
  if (assetCapture) report.asset_capture = assetCapture.summary();
  report.asset_errors = assetErrors;
  report.static_errors = staticErrors.map(safe);
  report.script_errors = scriptErrors;
  report.workers = workers;
  if (assetCapture?.errors.length || assetErrors.length || scriptErrors.length || staticErrors.length) report.state = 'failed';
}
const json = JSON.stringify(report, null, 2) + '\n';
if (args['--output']) await fs.writeFile(args['--output'], json);
else process.stdout.write(json);
if (report.state !== 'passed') process.exitCode = 1;
