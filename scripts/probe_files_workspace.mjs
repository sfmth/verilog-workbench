#!/usr/bin/env node
// Actual browser UI + temporary local project. HTTP calls below prove boundaries;
// user workflows (including every successful mutation) use the rendered controls.
import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import fs from 'node:fs/promises';
import http from 'node:http';
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
  if (!allowed.has(name) || !value || value.startsWith('--') || args[name] !== undefined) {
    throw new Error('Expected each supported --option once, followed by its value.');
  }
  args[name] = value;
}
const publicUrl = args['--public-url'] ? new URL(args['--public-url']) : null;
if (publicUrl) {
  assert.equal(publicUrl.protocol, 'https:', 'The public target must use HTTPS.');
  assert.ok(!publicUrl.username && !publicUrl.password && !publicUrl.search && !publicUrl.hash);
  assert.ok(publicUrl.pathname.endsWith('/workspace/'), 'The public URL must end in /workspace/.');
}
const python = args['--python'] || process.env.VWB_PYTHON || 'python3';
const {chromium} = require(args['--playwright-module'] || 'playwright');
const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'vwb-files-browser-'));
const project = path.join(temporary, 'project µ');
const prefix = publicUrl ? new URL('../', publicUrl).pathname : '/verilog-workbench/';
const beforeFolder = 'src/logic µ', afterFolder = 'src/renamed µ';
const originals = {
  'top.sv': '\ufeffmodule top(input logic a, output logic y);\r\nassign y = a;\r\nendmodule\r\n',
  'side.v': 'module side; endmodule\n',
  'helper.vhd': 'entity helper is end entity;\narchitecture rtl of helper is begin end architecture;\n',
  'model.py': 'def invert(value):\n    return not value\n',
};
const sha = value => createHash('sha256').update(value).digest('hex');
const secrets = new Set([temporary, project, repo]);
const children = [], scriptErrors = [], assetErrors = [], assetResponses = [];
const loadedAssets = new Map();
let browser, context, page, staticServer, origin, workspaceUrl, endpoint, credential;
const report = {
  schema: 'vwb.files-workspace-browser-probe/1', recorded_at: new Date().toISOString(), state: 'failed',
  target: publicUrl ? 'public-HTTPS-workspace' : 'built-local-project-prefix-workspace',
  pages_prefix: prefix, ...(publicUrl ? {public_url: publicUrl.href} : {}), checks: [],
  permission_grant: 'not_run', native_job: 'not_run', hardware_tested: false, transport_fault_injection: [],
  note: 'Real Chromium controls, native companion and temporary Unicode project. Successful create, move, trash and restore actions use the browser UI; direct authenticated HTTP calls only check access boundaries. Two explicit browser transport faults delay a real entry response and abort the post-create file read; native mutation responses remain real. Native inventory is real; no HDL simulation, language-service or FPGA result is implied. Public runs grant local-network access with the Chromium test API, not a manual permission prompt.',
};
const checked = name => { report.checks.push(name); process.stderr.write('[files-probe] passed ' + name + '\n'); };
function safe(value, limit = 14000) {
  let message = String(value);
  for (const secret of secrets) if (secret) message = message.replaceAll(secret, '[redacted]');
  return message.slice(0, limit);
}
async function until(predicate, label, timeout = 20000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error('Timed out: ' + label);
}
async function exists(relative) {
  try { await fs.lstat(path.join(project, relative)); return true; }
  catch (error) { if (error.code === 'ENOENT') return false; throw error; }
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
        assert.equal(value.event, 'ready');
        assert.equal(new URL(value.url).hostname, '127.0.0.1');
        assert.equal(typeof value.pairing_code, 'string');
        secrets.add(value.pairing_code); finish(null, value);
      } catch { finish(new Error('Invalid companion startup message')); }
    });
  });
  return {child, ready};
}
async function serveBuild() {
  const built = path.join(repo, 'dist/web');
  await fs.access(path.join(built, 'vwb-web-build.json'));
  const mime = {'.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml'};
  staticServer = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      if (!pathname.startsWith(prefix)) throw new Error('Outside project prefix');
      let target = path.resolve(built, pathname.slice(prefix.length));
      if (target !== built && !target.startsWith(built + path.sep)) throw new Error('Outside build');
      if ((await fs.stat(target)).isDirectory()) target = path.join(target, 'index.html');
      response.writeHead(200, {'Content-Type': mime[path.extname(target)] || 'application/octet-stream', 'Cache-Control': 'no-store'});
      response.end(await fs.readFile(target));
    } catch {
      if (!request.url.includes('favicon.ico')) assetErrors.push({path: safe(request.url, 1000), status: 404});
      response.writeHead(404); response.end('Not found');
    }
  });
  await new Promise((resolve, reject) => {
    staticServer.once('error', reject); staticServer.listen(0, '127.0.0.1', resolve);
  });
  origin = `http://127.0.0.1:${staticServer.address().port}`;
  workspaceUrl = origin + prefix + 'workspace/';
}
async function pair(ready) {
  endpoint = ready.url;
  await page.locator('#connect-toggle').click();
  await page.locator('#endpoint').fill(endpoint);
  await page.locator('#code').fill(ready.pairing_code);
  const [paired] = await Promise.all([
    page.waitForResponse(value => value.url() === endpoint + '/v1/pair'),
    page.locator('#pair').click(),
  ]);
  const data = await paired.json();
  assert.equal(paired.status(), 200, 'UI pairing must succeed.');
  assert.equal(data.protocol, 1); assert.equal(typeof data.token, 'string');
  credential = data.token; secrets.add(credential);
  await until(() => page.locator('#refresh').isEnabled(), 'paired project inventory');
  assert.equal(await page.locator('#code').inputValue(), '');
  assert.equal(await page.locator('#connection-indicator').textContent(), 'Companion connected');
}
async function api(route, method = 'GET', body) {
  return page.evaluate(async ({base, token, route, method, body}) => {
    const response = await fetch(base + route, {method, mode: 'cors', credentials: 'omit', targetAddressSpace: 'loopback',
      headers: {Authorization: 'Bearer ' + token, ...(body === undefined ? {} : {'Content-Type': 'application/json'})},
      ...(body === undefined ? {} : {body: JSON.stringify(body)}), signal: AbortSignal.timeout(15000)});
    return {status: response.status, body: await response.json()};
  }, {base: endpoint, token: credential, route, method, body});
}
async function openFile(relative) {
  await closeManager();
  await page.locator('[data-explorer="files"]').click();
  await page.locator('#explorer button[data-path]').filter({hasText: relative}).click();
  await until(async () => await page.locator('#active-file').textContent() === relative, 'active file ' + relative);
  await page.locator('#editor').waitFor({state: 'visible'});
}
const manager = () => page.locator('#files-dialog');
async function closeManager() {
  if (await manager().isVisible()) await manager().getByRole('button', {name: 'Close file manager', exact: true}).click();
}
async function showManager(action = 'Manage files') {
  await closeManager();
  await page.getByRole('button', {name: action, exact: true}).click();
  await manager().waitFor({state: 'visible'});
}
async function selectEntry(relative) {
  const select = manager().getByLabel('File or folder', {exact: true});
  await until(async () => await select.locator('option').evaluateAll((options, value) => options.some(option => option.value === value), relative), 'entry option ' + relative);
  if (await select.inputValue() !== relative) {
    const [response] = await Promise.all([
      page.waitForResponse(value => value.request().method() === 'GET' && new URL(value.url()).pathname === '/v1/project/entry' && new URL(value.url()).searchParams.get('path') === relative),
      select.selectOption(relative),
    ]);
    assert.equal(response.status(), 200, 'Selected entry must load.');
  }
  await until(() => manager().getByRole('button', {name: 'Reload entry', exact: true}).isEnabled(), 'loaded entry controls');
}
async function reloadEntry() {
  const [response] = await Promise.all([
    page.waitForResponse(value => value.request().method() === 'GET' && new URL(value.url()).pathname === '/v1/project/entry'),
    manager().getByRole('button', {name: 'Reload entry', exact: true}).click(),
  ]);
  assert.equal(response.status(), 200);
}
async function action(name, operation, expected = 200, trigger) {
  const [result] = await Promise.all([
    page.waitForResponse(value => value.request().method() === 'POST' && new URL(value.url()).pathname === '/v1/project/' + operation),
    trigger ? trigger() : manager().getByRole('button', {name, exact: true}).click(),
  ]);
  const body = await result.json();
  assert.equal(result.status(), expected, `${name}: ${body.error || 'unexpected status'}`);
  return body;
}
async function mutationGuard(label) {
  const toolbar = page.getByRole('button', {name: 'Manage files', exact: true});
  if (await toolbar.isDisabled()) { checked(label); return; }
  await showManager();
  const buttons = ['Create file', 'Create folder', 'Rename / move', 'Move to trash'];
  for (const name of buttons) assert.ok(await manager().getByRole('button', {name, exact: true}).isDisabled(), label + ': ' + name + ' must be disabled');
  assert.match(await manager().textContent(), /dirty|unsaved|draft|save/i, 'The lifecycle guard needs a visible explanation.');
  await closeManager(); checked(label);
}
async function verifyFolder(relative, texts) {
  for (const [name, content] of Object.entries(texts)) assert.equal(await fs.readFile(path.join(project, relative, name), 'utf8'), content, 'Exact moved/restored bytes: ' + name);
}
async function checkPrivateTrash() {
  const inventory = await api('/v1/project/files');
  assert.equal(inventory.status, 200);
  assert.ok(!JSON.stringify(inventory.body).includes('.vwb-trash'), 'Private trash must be absent from inventory.');
  const queue = ['.vwb-trash']; let privateFile;
  while (queue.length && !privateFile) {
    const directory = queue.shift();
    for (const entry of await fs.readdir(path.join(project, directory), {withFileTypes: true})) {
      const relative = directory + '/' + entry.name;
      if (entry.isDirectory()) queue.push(relative); else if (entry.isFile()) { privateFile = relative; break; }
    }
  }
  assert.ok(privateFile, 'Trash must retain actual private disk data.');
  for (const route of ['/v1/project/file?path=', '/v1/project/entry?path=', '/v1/artifacts/file?path=']) {
    assert.equal((await api(route + encodeURIComponent(privateFile))).status, 403, 'Private trash must not be downloadable through ' + route);
  }
}
async function checkStorage() {
  const storage = await page.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}}));
  for (const secret of secrets) {
    assert.ok(!storage.includes(secret), 'Credentials or absolute paths reached browser storage.');
    assert.ok(!page.url().includes(secret), 'Credentials or absolute paths reached page URL.');
  }
  assert.ok(!storage.includes('Bearer '));
  assert.ok(!storage.includes('FILE_LIFECYCLE_RECOVERY_DRAFT'), 'Source recovery requires explicit opt-in.');
}
async function screenshot() {
  if (args['--screenshot'] && page) await page.screenshot({path: args['--screenshot'], fullPage: true, mask: [page.locator('#connect-dialog')]});
}

try {
  await fs.mkdir(path.join(project, beforeFolder), {recursive: true});
  await fs.mkdir(path.join(project, 'test'));
  await fs.writeFile(path.join(project, '.vwb.json'), JSON.stringify({version: 1, src_dir: 'src', test_dir: 'test', build_dir: '.vwb'}) + '\n');
  for (const [name, content] of Object.entries(originals)) await fs.writeFile(path.join(project, beforeFolder, name), content);
  if (publicUrl) { origin = publicUrl.origin; workspaceUrl = publicUrl.href; }
  else await serveBuild();
  let native = await companion();
  browser = await chromium.launch({headless: true, executablePath: args['--executable'], args: ['--no-sandbox']});
  report.browser = await browser.version(); report.browser_security_flags = ['--no-sandbox'];
  context = await browser.newContext({acceptDownloads: true, viewport: {width: 1512, height: 982}});
  if (publicUrl) {
    await context.grantPermissions(['local-network-access'], {origin});
    report.permission_grant = 'explicit-simulated-Chromium-context-grantPermissions-local-network-access';
  }
  page = await context.newPage(); page.setDefaultTimeout(20000);
  page.on('pageerror', error => scriptErrors.push(safe(error.message)));
  page.on('dialog', dialog => dialog.accept());
  page.on('response', response => {
    const url = new URL(response.url());
    if (url.origin !== origin || !url.pathname.startsWith(prefix)) return;
    const relative = decodeURIComponent(url.pathname.slice(prefix.length));
    const asset = relative.endsWith('/') ? relative + 'index.html' : relative;
    if (asset === 'vwb-web-build.json' || asset.endsWith('favicon.ico')) return;
    assetResponses.push((async () => {
      if (response.status() !== 200) assetErrors.push({path: asset, status: response.status()});
      else loadedAssets.set(asset, sha(await response.body()));
    })().catch(error => assetErrors.push({path: asset, error: safe(error.message)})));
  });
  const navigation = await page.goto(workspaceUrl, {waitUntil: 'networkidle', timeout: 30000});
  assert.equal(navigation.status(), 200);
  assert.equal(new URL(page.url()).pathname, new URL(workspaceUrl).pathname);
  await pair(native.ready);
  checked('real-workspace-project-prefix-and-companion-UI-pairing');

  await showManager('New folder');
  await manager().getByLabel('New path', {exact: true}).fill('src/new µ');
  await action('Create folder', 'create', 200, () => manager().getByLabel('New path', {exact: true}).press('Enter'));
  assert.ok((await fs.stat(path.join(project, 'src/new µ'))).isDirectory());
  await closeManager();
  await showManager('New file');
  await manager().getByLabel('New path', {exact: true}).fill('src/new µ/new.sv');
  await action('Create file', 'create');
  await manager().waitFor({state: 'hidden'});
  await until(async () => await page.locator('#active-file').textContent() === 'src/new µ/new.sv', 'new file opens in editor');
  assert.equal(await fs.readFile(path.join(project, 'src/new µ/new.sv'), 'utf8'), '');
  const created = 'module new_design; endmodule\n';
  await page.locator('#editor').fill(created); await page.locator('#save').click();
  await until(async () => await fs.readFile(path.join(project, 'src/new µ/new.sv'), 'utf8') === created, 'new file real disk save');
  checked('UI-Enter-creates-Unicode-folder-and-button-creates-empty-file-then-edits-and-saves');

  // Hold the real native reply rather than substitute entry metadata. Selecting
  // the placeholder must invalidate it and release the manager's loading gate.
  await showManager();
  const heldPath = beforeFolder + '/top.sv';
  const heldMatcher = url => url.origin === new URL(endpoint).origin && url.pathname === '/v1/project/entry' && url.searchParams.get('path') === heldPath;
  let releaseEntry, entryArrived = false;
  const released = new Promise(resolve => { releaseEntry = resolve; });
  await page.route(heldMatcher, async route => {
    const response = await route.fetch();
    entryArrived = true; await released;
    await route.fulfill({response});
  });
  try {
    await manager().getByLabel('File or folder', {exact: true}).selectOption(heldPath);
    await until(() => entryArrived, 'held real native entry response');
    assert.ok(await manager().getByRole('button', {name: 'Create file', exact: true}).isDisabled(), 'Pending entry inspection must block mutation.');
    await manager().getByLabel('File or folder', {exact: true}).selectOption('');
    const delivered = page.waitForResponse(response => heldMatcher(new URL(response.url())));
    releaseEntry(); await delivered;
    await until(() => manager().getByRole('button', {name: 'Create file', exact: true}).isEnabled(), 'placeholder releases stale entry loading gate');
    assert.equal(await manager().getByLabel('File or folder', {exact: true}).inputValue(), '');
    assert.ok(await manager().getByRole('button', {name: 'Rename / move', exact: true}).isDisabled(), 'Late entry reply must not install a snapshot after deselection.');
    assert.ok(await manager().getByRole('button', {name: 'Move to trash', exact: true}).isDisabled());
  } finally { releaseEntry(); await page.unroute(heldMatcher); }
  await closeManager();
  report.transport_fault_injection.push('delayed-real-native-entry-response-followed-by-placeholder-selection');
  checked('delayed-entry-response-after-deselection-cannot-stick-loading-or-enable-stale-mutation');

  // The POST still reaches the real companion and creates the actual file.
  // Only its follow-up browser GET is aborted, proving a post-commit display
  // failure reports success accurately and never resubmits the mutation.
  const transportPath = 'src/new µ/transport.sv';
  const transportMatcher = url => url.origin === new URL(endpoint).origin && url.pathname === '/v1/project/file' && url.searchParams.get('path') === transportPath;
  let createPosts = 0, abortedReads = 0;
  const countCreate = request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/v1/project/create' && request.postDataJSON()?.path === transportPath) createPosts++;
  };
  page.on('request', countCreate);
  await page.route(transportMatcher, async route => { abortedReads++; await route.abort('failed'); });
  try {
    await showManager('New file');
    await manager().getByLabel('New path', {exact: true}).fill(transportPath);
    await action('Create file', 'create');
    await until(async () => /change succeeded/i.test((await page.locator('#global-status').textContent()) + ' ' + (await manager().textContent())), 'post-create read failure acknowledges committed mutation');
    assert.equal(await fs.readFile(path.join(project, transportPath), 'utf8'), '');
    assert.equal(createPosts, 1, 'Post-commit display failure must not retry the native create POST.');
    assert.equal(abortedReads, 1, 'The injected transport failure must target the post-create open GET.');
    assert.deepEqual(scriptErrors, [], 'Post-commit callback failure must be caught, not become an unhandled browser rejection.');
  } finally { await page.unroute(transportMatcher); }
  await closeManager();
  if (await page.locator('#connection-indicator').textContent() !== 'Companion connected') {
    await page.locator('#connect-toggle').click();
    await page.locator('#retry').click();
    await until(async () => await page.locator('#connection-indicator').textContent() === 'Companion connected', 'UI retry after explicit transport fault');
    await page.locator('#connect-dialog').waitFor({state: 'hidden'});
  }
  await openFile(transportPath);
  assert.equal(await page.locator('#editor').inputValue(), '');
  assert.equal(createPosts, 1, 'UI reconnection and explicit open must not resubmit create.');
  page.off('request', countCreate);
  report.transport_fault_injection.push('aborted-browser-file-GET-after-real-native-create-POST');
  checked('post-create-open-transport-failure-keeps-committed-file-reports-applied-error-and-never-retries-POST');

  await openFile(beforeFolder + '/top.sv');
  const topEdited = originals['top.sv'] + '// lifecycle CRLF and BOM µ\r\n';
  await page.locator('#editor').fill(topEdited.replace(/\r\n/g, '\n'));
  await page.locator('#save').click();
  await until(async () => await fs.readFile(path.join(project, beforeFolder, 'top.sv'), 'utf8') === topEdited, 'BOM and CRLF exact disk save');
  originals['top.sv'] = topEdited;
  await openFile(beforeFolder + '/model.py');
  await showManager(); await selectEntry(beforeFolder);
  await manager().getByLabel('Destination path', {exact: true}).fill(afterFolder);
  await action('Rename / move', 'move'); await closeManager();
  assert.equal(await exists(beforeFolder), false); await verifyFolder(afterFolder, originals);
  assert.equal(await page.locator('#active-file').textContent(), afterFolder + '/model.py');
  assert.equal(new URLSearchParams(new URL(page.url()).hash.slice(1)).get('file'), afterFolder + '/model.py');
  assert.equal(await page.locator('#file-tabs button[title]').filter({hasText: 'top.sv'}).getAttribute('title'), afterFolder + '/top.sv');
  assert.equal(await page.locator('#file-tabs button[title]').filter({hasText: 'model.py'}).getAttribute('title'), afterFolder + '/model.py');
  const pythonEdited = originals['model.py'] + '# saved after folder move µ\n';
  await page.locator('#editor').fill(pythonEdited); await page.locator('#save').click();
  await until(async () => await fs.readFile(path.join(project, afterFolder, 'model.py'), 'utf8') === pythonEdited, 'remapped buffer disk save');
  originals['model.py'] = pythonEdited;
  checked('multi-file-folder-move-preserves-exact-bytes-remaps-clean-tabs-hash-and-save-target');

  await page.locator('#editor').fill(pythonEdited + '# unsaved guard\n');
  await mutationGuard('dirty-buffer-blocks-lifecycle-without-disk-changes');
  assert.equal(await fs.readFile(path.join(project, afterFolder, 'model.py'), 'utf8'), pythonEdited);
  await page.locator('#reload').click();
  await until(async () => await page.locator('#editor').inputValue() === pythonEdited, 'discard guard draft');
  const recovery = {schema: 'vwb.drafts/1', files: [{path: afterFolder + '/model.py', revision: sha(pythonEdited),
    content: pythonEdited + '# FILE_LIFECYCLE_RECOVERY_DRAFT\n', newline: '\n'}]};
  await page.locator('#import-drafts').setInputFiles({name: 'drafts.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(recovery))});
  await page.locator('#recovery').waitFor({state: 'visible'});
  await mutationGuard('unresolved-recovery-draft-blocks-lifecycle');
  await page.locator('#recovery-list').getByRole('button', {name: 'Discard', exact: true}).click();
  await page.locator('#recovery').waitFor({state: 'hidden'});

  await showManager(); await selectEntry('src/new µ/new.sv'); await reloadEntry();
  const external = created + '// changed by another local editor\n';
  await fs.writeFile(path.join(project, 'src/new µ/new.sv'), external);
  await manager().getByLabel('Destination path', {exact: true}).fill('src/new µ/stale.sv');
  await action('Rename / move', 'move', 409); await closeManager();
  assert.equal(await fs.readFile(path.join(project, 'src/new µ/new.sv'), 'utf8'), external);
  assert.equal(await exists('src/new µ/stale.sv'), false);
  checked('stale-entry-revision-conflict-preserves-external-bytes-and-path');
  await openFile('src/new µ/new.sv'); await page.locator('#reload').click();
  await until(async () => await page.locator('#editor').inputValue() === external, 'reload externally changed file');
  const collision = 'module collision; endmodule\n';
  await fs.writeFile(path.join(project, 'src/collision.sv'), collision);
  await showManager(); await selectEntry('src/new µ/new.sv');
  await manager().getByLabel('Destination path', {exact: true}).fill('src/collision.sv');
  await action('Rename / move', 'move', 409); await closeManager();
  assert.equal(await fs.readFile(path.join(project, 'src/collision.sv'), 'utf8'), collision);
  assert.equal(await fs.readFile(path.join(project, 'src/new µ/new.sv'), 'utf8'), external);
  checked('existing-destination-conflict-never-overwrites-either-file');

  await openFile(afterFolder + '/model.py');
  await showManager(); await selectEntry(afterFolder);
  const trashed = await action('Move to trash', 'trash'); await closeManager();
  assert.equal(await exists(afterFolder), false);
  assert.equal(await page.locator('#file-tabs button[title]').evaluateAll((buttons, prefix) => buttons.some(button => button.title.startsWith(prefix + '/')), afterFolder), false);
  assert.notEqual(await page.locator('#active-file').textContent(), afterFolder + '/model.py');
  assert.notEqual(new URLSearchParams(new URL(page.url()).hash.slice(1)).get('file'), afterFolder + '/model.py');
  const retained = await api('/v1/project/trash');
  assert.equal(retained.status, 200);
  const entry = retained.body.entries.find(value => value.path === afterFolder);
  assert.ok(entry && typeof entry.id === 'string', 'Retained trash metadata must identify the original path.');
  if (trashed.id) assert.equal(entry.id, trashed.id);
  await checkPrivateTrash();
  checked('UI-folder-trash-closes-remapped-tabs-retains-private-undownloadable-data');

  const prepare = spawnSync(python, ['-c', 'from pathlib import Path; import sys,vwb; r=Path(sys.argv[1]); vwb.Workbench(r,r/"src",r/"test",r/".vwb").prepare_build_dir()', project], {cwd: repo, encoding: 'utf8'});
  assert.equal(prepare.status, 0, 'Cannot initialize temporary build: ' + safe(prepare.stderr));
  await fs.writeFile(path.join(project, '.vwb/disposable.txt'), 'generated\n');
  const clean = spawnSync(python, ['vwb.py', '--root', project, '--src-dir', 'src', '--test-dir', 'test', '--build-dir', '.vwb', '--color', 'never', 'clean', 'all'], {cwd: repo, encoding: 'utf8'});
  assert.equal(clean.status, 0, 'Native clean failed: ' + safe(clean.stderr));
  assert.equal(await exists('.vwb/disposable.txt'), false);
  assert.ok(await exists('.vwb-trash'), 'Project trash must survive native build cleanup.');
  await stop(native.child); await page.reload({waitUntil: 'networkidle'});
  native = await companion(); await pair(native.ready);
  await page.getByRole('button', {name: 'Trash', exact: true}).click();
  await manager().waitFor({state: 'visible'});
  const restore = manager().getByRole('button', {name: 'Restore ' + afterFolder, exact: true});
  await restore.waitFor({state: 'visible'});
  checked('trash-survives-real-native-clean-companion-restart-and-new-UI-session');

  await fs.mkdir(path.join(project, afterFolder));
  await fs.writeFile(path.join(project, afterFolder, 'keep.txt'), 'external replacement must survive\n');
  await action('Restore ' + afterFolder, 'restore', 409);
  assert.equal(await fs.readFile(path.join(project, afterFolder, 'keep.txt'), 'utf8'), 'external replacement must survive\n');
  const conflictEntries = await api('/v1/project/trash');
  assert.ok(conflictEntries.body.entries.some(value => value.id === entry.id), 'Restore conflict must retain the trash entry.');
  await fs.rm(path.join(project, afterFolder), {recursive: true});
  await action('Restore ' + afterFolder, 'restore'); await closeManager();
  await verifyFolder(afterFolder, originals);
  assert.ok(!(await api('/v1/project/trash')).body.entries.some(value => value.id === entry.id));
  await openFile(afterFolder + '/top.sv');
  assert.equal(await page.locator('#editor').inputValue(), originals['top.sv'].replace(/\r\n/g, '\n'));
  checked('UI-restore-refuses-external-replacement-then-restores-original-four-language-tree');

  for (const relative of ['src', 'test']) {
    const snapshot = await api('/v1/project/entry?path=' + encodeURIComponent(relative));
    assert.equal(snapshot.status, 200);
    assert.equal((await api('/v1/project/move', 'POST', {path: relative, destination: relative + '-moved', revision: snapshot.body.revision})).status, 403);
    assert.ok(await exists(relative)); assert.equal(await exists(relative + '-moved'), false);
  }
  const configBytes = await fs.readFile(path.join(project, '.vwb.json'));
  assert.equal((await api('/v1/project/trash', 'POST', {path: '.vwb.json', revision: sha(configBytes)})).status, 403);
  assert.deepEqual(await fs.readFile(path.join(project, '.vwb.json')), configBytes);
  for (const relative of ['.git/config', '../outside', '.vwb-trash']) {
    assert.equal((await api('/v1/project/entry?path=' + encodeURIComponent(relative))).status, 403);
  }
  assert.equal((await api('/v1/project/create', 'POST', {path: '.vwb/blocked.sv', kind: 'file', content: ''})).status, 403);
  checked('authenticated-HTTP-rejects-configured-roots-config-build-Git-traversal-and-private-trash');

  await page.locator('#command').selectOption('list'); await page.locator('#run').click();
  await until(async () => /list.*passed/i.test(await page.locator('#job-status').textContent()), 'native discover after restoration', 30000);
  assert.match(await page.locator('#job-output').textContent(), /top/);
  report.native_job = 'passed-real-vwb-list-through-browser-after-restored-folder';
  checked('restored-project-runs-real-native-discovery-through-UI');
  await checkStorage();
  const manifest = await page.evaluate(async url => {
    const response = await fetch(url, {cache: 'no-store'});
    if (!response.ok) throw new Error('Build manifest HTTP ' + response.status);
    return response.json();
  }, origin + prefix + 'vwb-web-build.json');
  await Promise.all(assetResponses);
  assert.equal(manifest.schema, 'vwb.web-build/1'); assert.equal(manifest.base_path, prefix);
  for (const asset of ['workspace/index.html', 'workspace/app.mjs', 'workspace/model.mjs', 'workspace/files.mjs']) {
    const expected = typeof manifest.files[asset] === 'string' ? manifest.files[asset] : manifest.files[asset]?.sha256;
    assert.equal(loadedAssets.get(asset), expected, 'Loaded file lifecycle asset must match the build manifest: ' + asset);
  }
  report.deployed_manifest = {schema: manifest.schema, status: manifest.status, base_path: manifest.base_path, file_count: Object.keys(manifest.files).length};
  report.fixture_sha256 = Object.fromEntries(Object.entries(originals).map(([name, text]) => [name, sha(text)]));
  assert.deepEqual(scriptErrors, []); assert.deepEqual(assetErrors, []);
  checked('loaded-assets-match-manifest-and-browser-storage-keeps-credentials-and-unopted-drafts-out');
  report.state = 'passed'; await screenshot();
} catch (error) {
  report.error = safe(error.stack || error.message);
  if (page) {
    report.visible_status = await page.locator('#global-status').textContent().then(safe).catch(() => null);
    report.file_manager_status = await manager().textContent().then(value => safe(value, 6000)).catch(() => null);
    report.active_file = await page.locator('#active-file').textContent().then(safe).catch(() => null);
    await screenshot().catch(() => {});
  }
} finally {
  report.script_errors = scriptErrors; report.asset_errors = assetErrors;
  await context?.close().catch(() => {}); await browser?.close().catch(() => {});
  for (const child of children) await stop(child);
  if (staticServer) await new Promise(resolve => staticServer.close(resolve));
  await fs.rm(temporary, {recursive: true, force: true});
}
const json = safe(JSON.stringify(report, null, 2), 100000) + '\n';
if (args['--output']) await fs.writeFile(args['--output'], json); else process.stdout.write(json);
if (report.state !== 'passed') process.exitCode = 1;
