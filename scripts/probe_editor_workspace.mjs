#!/usr/bin/env node
// Browser acceptance uses the actual UI, native companion and configured LSPs.
// Token replacement below constructs a fixture oracle only; every real edit is
// a language-server rename preview applied through the companion transaction UI.
import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import fs from 'node:fs/promises';
import http from 'node:http';
import {createRequire} from 'node:module';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {captureSiteAssets} from './browser_assets.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const args = {};
const allowed = new Set(['--editor-config', '--playwright-module', '--executable', '--python', '--output', '--screenshot', '--public-url']);
for (let index = 2; index < process.argv.length; index += 2) {
  const name = process.argv[index], value = process.argv[index + 1];
  if (!allowed.has(name) || !value || value.startsWith('--') || args[name] !== undefined) {
    throw new Error('Expected each supported --option once, followed by its value.');
  }
  args[name] = value;
}
if (!args['--editor-config']) throw new Error('--editor-config is required: real, locally configured language servers are mandatory.');
const editorConfig = path.resolve(args['--editor-config']);
const python = args['--python'] || process.env.VWB_PYTHON || 'python3';
const publicUrl = args['--public-url'] ? new URL(args['--public-url']) : null;
if (publicUrl) {
  assert.equal(publicUrl.protocol, 'https:', 'Public workspace acceptance requires HTTPS.');
  assert.ok(!publicUrl.username && !publicUrl.password && !publicUrl.search && !publicUrl.hash, 'Public URL must not contain credentials, a query or a fragment.');
  assert.ok(publicUrl.pathname.endsWith('/workspace/'), 'Public URL must end in /workspace/.');
}
const {chromium} = require(args['--playwright-module'] || 'playwright');
const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'vwb-editor-browser-'));
const secrets = new Set([temporary, repo, editorConfig]);
const children = [], contexts = [], scriptErrors = [], staticErrors = [], assetErrors = [], assetCaptures = [];
const prefix = publicUrl ? new URL('../', publicUrl).pathname : '/verilog-workbench/';
let browser, page, staticServer, workspaceUrl, origin;
const report = {
  schema: 'vwb.editor-workspace-browser-probe/1', recorded_at: new Date().toISOString(), state: 'failed',
  target: publicUrl ? 'public-HTTPS-workspace' : 'built-local-project-prefix-workspace',
  pages_prefix: prefix, ...(publicUrl ? {public_url: publicUrl.href} : {}),
  permission_grant: 'not_run', hardware_tested: false, languages: {}, checks: [],
  note: 'Real Chromium UI, local companion and configured language servers. Exact fixture oracles verify semantic references and rename bytes, including unrelated shadows/comments. Native post-edit checks are isolated compilation/helper behavior, not FPGA or full Cocotb execution. No mocked semantic responses.',
};
const checked = name => report.checks.push(name);
const sha = value => createHash('sha256').update(value).digest('hex');
function safe(value, limit = 12000) {
  let message = String(value);
  for (const secret of secrets) if (secret) message = message.replaceAll(secret, '[redacted]');
  return message.slice(0, limit);
}
async function until(predicate, label, timeout = 20000) {
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
async function companion(projectRoot) {
  const child = spawn(python, ['-m', 'companion', '--project', projectRoot, '--origin', origin, '--port', '0',
    '--src-dir', '.', '--test-dir', 'test', '--editor-config', editorConfig], {cwd: repo, stdio: ['ignore', 'pipe', 'pipe']});
  children.push(child);
  let stderr = '';
  child.stderr.on('data', data => { stderr = (stderr + data).slice(-8000); });
  const ready = await new Promise((resolve, reject) => {
    let output = '', settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
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
async function serveBuild() {
  const built = path.join(repo, 'dist/web');
  await fs.access(path.join(built, 'vwb-web-build.json'));
  const mime = {'.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml'};
  staticServer = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      if (!pathname.startsWith(prefix)) throw new Error('Outside project prefix');
      let target = path.resolve(built, pathname.slice(prefix.length));
      if (target !== built && !target.startsWith(built + path.sep)) throw new Error('Outside build output');
      if ((await fs.stat(target)).isDirectory()) target = path.join(target, 'index.html');
      const content = await fs.readFile(target);
      response.writeHead(200, {'Content-Type': mime[path.extname(target)] || 'application/octet-stream', 'Cache-Control': 'no-store'});
      response.end(content);
    } catch {
      if (!request.url.includes('favicon.ico')) staticErrors.push(safe(request.url, 1000));
      response.writeHead(404); response.end('Not found');
    }
  });
  await new Promise((resolve, reject) => {
    staticServer.once('error', reject);
    staticServer.listen(0, '127.0.0.1', resolve);
  });
  origin = `http://127.0.0.1:${staticServer.address().port}`;
  workspaceUrl = origin + prefix + 'workspace/';
}
function site(texts, spec) {
  const [relative, snippet, token] = spec;
  const text = texts[relative].replace(/\r\n?/g, '\n');
  const lines = text.split('\n');
  const matches = lines.map((line, index) => ({line, index})).filter(value => value.line.includes(snippet));
  assert.equal(matches.length, 1, 'Fixture site must be unique: ' + relative + ' ' + snippet);
  const {line, index} = matches[0];
  const character = line.indexOf(token, line.indexOf(snippet));
  assert.ok(character >= 0, 'Fixture token must occur within its site.');
  return {path: relative, line: index, character, offset: lines.slice(0, index).reduce((sum, value) => sum + value.length + 1, 0) + character};
}
function renamedOracle(originals, testCase) {
  const expected = {...originals};
  const positions = testCase.rename_sites.map(spec => ({...site(originals, spec), token: spec[2]}));
  for (const location of [...positions].sort((a, b) => a.path.localeCompare(b.path) || b.offset - a.offset)) {
    const before = expected[location.path];
    assert.equal(before.slice(location.offset, location.offset + location.token.length), location.token);
    expected[location.path] = before.slice(0, location.offset) + testCase.new_name + before.slice(location.offset + location.token.length);
  }
  return {expected, positions};
}
const canonical = locations => locations.map(location => `${location.path}:${location.range?.start?.line ?? location.line}:${location.range?.start?.character ?? location.character}`).sort();
function completionOracle(text, item) {
  const offset = position => {
    const lines = text.split('\n');
    assert.ok(position.line >= 0 && position.line < lines.length && position.character >= 0 && position.character <= lines[position.line].length);
    return lines.slice(0, position.line).reduce((sum, line) => sum + line.length + 1, 0) + position.character;
  };
  const edits = [{range: item.range, new_text: item.insert_text}, ...(item.additional_edits || [])]
    .map(edit => ({start: offset(edit.range.start), end: offset(edit.range.end), content: edit.new_text}))
    .sort((a, b) => b.start - a.start);
  let expected = text;
  for (const edit of edits) expected = expected.slice(0, edit.start) + edit.content + expected.slice(edit.end);
  return expected.replace(/\r\n?/g, '\n');
}
async function diskTexts(project, files) {
  return Object.fromEntries(await Promise.all(files.map(async relative => [relative, await fs.readFile(path.join(project, relative), 'utf8')])));
}
async function openFile(relative) {
  await page.locator('[data-explorer="files"]').click();
  const button = page.locator('#explorer button[data-path]').filter({hasText: relative});
  await button.click();
  await until(async () => await page.locator('#active-file').textContent() === relative, 'active file ' + relative);
  await page.locator('#editor').waitFor({state: 'visible'});
}
async function cursor(location, within = 0) {
  await openFile(location.path);
  await page.locator('#editor').evaluate((editor, offset) => {
    editor.focus(); editor.setSelectionRange(offset, offset);
    editor.dispatchEvent(new Event('select', {bubbles: true}));
  }, location.offset + within);
  const selection = await page.locator('#editor').evaluate(editor => ({start: editor.selectionStart, end: editor.selectionEnd}));
  assert.deepEqual(selection, {start: location.offset + within, end: location.offset + within});
  await page.locator('[data-result="intelligence"]').click();
}
const pane = () => page.locator('#result-intelligence');
const operationButton = operation => pane().getByRole('button', {name: {completion: 'Complete', hover: 'Hover', definition: 'Definition', references: 'References', rename: 'Rename'}[operation], exact: true});
async function editorResponse(ready, operation, action, status = 200) {
  const pending = page.waitForResponse(response => {
    if (response.url() !== ready.url + '/v1/editor/query' || response.request().method() !== 'POST') return false;
    try { return response.request().postDataJSON().operation === operation; } catch { return false; }
  });
  await action();
  const response = await pending;
  const body = await response.json();
  assert.equal(response.status(), status, `${operation} must use a real companion response: ${safe(body.error || '', 2000)}`);
  await until(() => operationButton('completion').isEnabled(), 'editor UI settled after ' + operation);
  if (status === 200) assert.equal(body.operation, operation);
  return body;
}
async function query(ready, operation) {
  await until(() => operationButton(operation).isEnabled(), operation + ' capability available');
  return editorResponse(ready, operation, () => operationButton(operation).click());
}
async function pair(ready, language) {
  await page.locator('#connect-toggle').click();
  await page.locator('#endpoint').fill(ready.url);
  await page.locator('#code').fill(ready.pairing_code);
  const pending = page.waitForResponse(response => response.url() === ready.url + '/v1/pair');
  const capabilities = page.waitForResponse(response => response.url() === ready.url + '/v1/editor/capabilities');
  await page.locator('#pair').click();
  const paired = await pending;
  const data = await paired.json();
  if (data.token) secrets.add(data.token);
  assert.equal(paired.status(), 200);
  assert.equal(data.protocol, 1);
  assert.equal(typeof data.token, 'string');
  const response = await capabilities;
  const available = await response.json();
  assert.equal(response.status(), 200);
  assert.equal(available.languages?.[language]?.available, true, language + ' must use an installed language server');
  for (const operation of ['completion', 'hover', 'definition', 'references', 'rename']) {
    assert.equal(available.languages[language].operations[operation].available, true, language + ' must support ' + operation);
  }
  await until(() => page.locator('#refresh').isEnabled(), 'paired project inventory');
  assert.equal(await page.locator('#code').inputValue(), '', 'Pairing codes must be cleared.');
  return available.languages[language];
}
async function preview(ready, testCase, location, originals, expected) {
  await cursor(location);
  await until(() => operationButton('rename').isEnabled(), 'rename capability ready');
  await operationButton('rename').click();
  await pane().getByLabel('New symbol name', {exact: true}).fill(testCase.new_name);
  const response = await editorResponse(ready, 'rename', () => pane().getByRole('button', {name: 'Preview rename', exact: true}).click());
  assert.ok(response.plan && Array.isArray(response.plan.files));
  assert.deepEqual(response.plan.files.map(file => file.path).sort(), [...testCase.files].sort());
  assert.equal(response.plan.files.reduce((sum, file) => sum + file.edit_count, 0), testCase.rename_sites.length);
  for (const file of response.plan.files) {
    assert.equal(file.before, originals[file.path], 'Preview before bytes must match ' + file.path);
    assert.equal(file.after, expected[file.path], 'Semantic rename must match the exact fixture oracle for ' + file.path);
    assert.equal(file.before_revision, sha(originals[file.path]));
    assert.equal(file.after_revision, sha(expected[file.path]));
    const detail = pane().locator('.intelligence-results details').filter({has: page.locator('summary', {hasText: file.path})});
    await detail.locator('summary').click();
    assert.deepEqual(await detail.locator('pre').allTextContents(), [file.before, file.after]);
  }
  await pane().getByRole('button', {name: 'Apply rename to local files', exact: true}).waitFor({state: 'visible'});
  return response.plan;
}
async function transaction(ready, operation, button, status = 200) {
  const pending = page.waitForResponse(response => response.url() === ready.url + '/v1/editor/' + operation && response.request().method() === 'POST');
  await button.click();
  const response = await pending;
  const body = await response.json();
  assert.equal(response.status(), status, `${operation} response: ${safe(body.error || '', 2000)}`);
  await until(() => page.locator('#editor').isEnabled(), 'editor unlocked after ' + operation);
  return body;
}
async function nativeValidation(language, project, testCase) {
  const copy = path.join(temporary, 'post-edit-' + language);
  await fs.cp(project, copy, {recursive: true});
  const commands = {
    systemverilog: ['iverilog', '-g2012', '-t', 'null', '-s', 'top', '-s', 'other', ...testCase.files],
    vhdl: ['ghdl', '-a', '--std=08', ...testCase.files],
    python: [python, '-c', 'import compileall,model,other,sys; assert compileall.compile_dir(".", quiet=1); assert getattr(model,sys.argv[1])(1)==2; assert other.result==3; assert other.shadow(5)==4', testCase.new_name],
  };
  const command = commands[language];
  const result = spawnSync(command[0], command.slice(1), {cwd: copy, encoding: 'utf8', timeout: 20000, maxBuffer: 16384});
  const evidence = {tool: path.basename(command[0]), scope: 'Isolated post-edit HDL compilation or Python syntax/helper behavior; not hardware/full Cocotb.', status: 'not_run'};
  if (result.error?.code === 'ENOENT') {
    evidence.reason = 'Native fixture compiler is absent.';
    return evidence;
  }
  evidence.status = result.status === 0 ? 'passed' : 'failed';
  evidence.returncode = result.status;
  evidence.stdout = safe(result.stdout || '', 4000);
  evidence.stderr = safe(result.stderr || result.error?.message || '', 4000);
  return evidence;
}
async function storageCheck() {
  const storage = await page.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}}));
  assert.equal(storage, '{"local":{},"session":{}}', 'No source or credentials should persist without opting into recovery.');
  for (const secret of secrets) {
    assert.ok(!storage.includes(secret), 'A credential reached browser storage.');
    assert.ok(!page.url().includes(secret), 'A credential reached the browser URL.');
  }
}
async function screenshot() {
  if (!args['--screenshot'] || !page) return;
  await fs.mkdir(path.dirname(path.resolve(args['--screenshot'])), {recursive: true});
  await page.screenshot({path: args['--screenshot'], fullPage: true, mask: [page.locator('#connect-dialog'), page.locator('#code')]});
}

try {
  const configBytes = await fs.readFile(editorConfig);
  const config = JSON.parse(configBytes);
  report.editor_config_sha256 = sha(configBytes);
  report.configured_servers = Object.fromEntries(Object.entries(config).map(([language, entry]) => [language, entry?.command?.map(part => path.basename(part)) || null]));
  const cases = JSON.parse(await fs.readFile(path.join(repo, 'editor-fixtures/cases.json'), 'utf8'));
  if (publicUrl) { workspaceUrl = publicUrl.href; origin = publicUrl.origin; }
  else await serveBuild();
  browser = await chromium.launch({headless: true, executablePath: args['--executable'], args: ['--no-sandbox']});
  report.browser = await browser.version();
  report.browser_security_flags = ['--no-sandbox'];
  report.permission_grant = publicUrl ? 'explicit-simulated-Chromium-context-grantPermissions-local-network-access' : 'not-required-loopback-HTTP-preview-origin-no-browser-security-bypass';

  for (const language of ['systemverilog', 'vhdl', 'python']) {
    const evidence = report.languages[language] = {status: 'failed', checks: [], native_post_edit: {status: 'not_run'}};
    const testCase = cases[language];
    const project = path.join(temporary, language + ' project µ');
    await fs.cp(path.join(repo, 'editor-fixtures', language), project, {recursive: true});
    await fs.mkdir(path.join(project, 'test'), {recursive: true});
    const originals = await diskTexts(project, testCase.files);
    const {expected, positions} = renamedOracle(originals, testCase);
    const querySite = site(originals, testCase.query);
    const native = await companion(project);
    const context = await browser.newContext({viewport: {width: 1512, height: 1050}});
    contexts.push(context);
    if (publicUrl) await context.grantPermissions(['local-network-access'], {origin});
    const capture = await captureSiteAssets(context, {origin, prefix,
      onError: error => assetErrors.push({...error, ...(error.error ? {error: safe(error.error, 2000)} : {})}),
    });
    const captured = {language, capture, closed: false};
    assetCaptures.push(captured);
    page = await context.newPage();
    await capture.attachPage(page);
    page.setDefaultTimeout(20000);
    page.on('pageerror', error => scriptErrors.push(safe(error.message, 2000)));
    page.on('dialog', dialog => dialog.accept());
    let applyRequests = 0;
    page.on('request', request => { if (request.url() === native.ready.url + '/v1/editor/apply') applyRequests++; });
    const navigation = await page.goto(workspaceUrl + '#view=intelligence', {waitUntil: 'networkidle', timeout: 30000});
    assert.equal(navigation.status(), 200);
    assert.equal(new URL(page.url()).pathname, new URL(workspaceUrl).pathname);
    assert.equal(await page.locator('[data-result="intelligence"]').getAttribute('aria-pressed'), 'true');
    evidence.server = (await pair(native.ready, language)).server;
    evidence.checks.push('real-companion-pairing-language-capabilities-and-code-intelligence-tab');

    await cursor(querySite);
    const hover = await query(native.ready, 'hover');
    assert.equal(typeof hover.result.text, 'string');
    assert.ok(hover.result.text.trim(), 'Real language-server hover must be nonempty.');
    assert.equal(await pane().locator('.intelligence-results pre').textContent(), hover.result.text);
    evidence.hover = {status: 'passed', text_characters: hover.result.text.length};

    const definition = await query(native.ready, 'definition');
    const target = definition.result.locations.find(location => location.path === testCase.definition_file);
    assert.ok(target, 'Definition must resolve to the fixture helper file.');
    const label = `${target.path}:${target.range.start.line + 1}:${target.range.start.character + 1}`;
    await pane().getByRole('button', {name: label, exact: true}).click();
    await until(async () => await page.locator('#active-file').textContent() === target.path, 'definition source navigation');
    const definitionOffset = originals[target.path].split('\n').slice(0, target.range.start.line).reduce((sum, line) => sum + line.length + 1, 0) + target.range.start.character;
    await until(async () => await page.locator('#editor').evaluate(editor => editor.selectionStart) === definitionOffset, 'definition range selection');
    evidence.definition = {status: 'passed', path: target.path, range: target.range, source_navigation: 'passed'};

    await cursor(querySite);
    const references = await query(native.ready, 'references');
    assert.equal(references.result.truncated, false);
    assert.deepEqual(canonical(references.result.locations), canonical(positions), 'References must exactly include the declaration and intended semantic uses only.');
    assert.equal(await pane().locator('.intelligence-results button').count(), positions.length);
    evidence.references = {status: 'passed', count: positions.length, locations: references.result.locations, includes_declaration: true, unrelated_symbols_excluded: true};

    const completionSite = site(originals, testCase.completion);
    const within = Math.max(1, Math.floor(testCase.completion[2].length / 2));
    await cursor(completionSite, within);
    const completions = await query(native.ready, 'completion');
    evidence.completion = {status: 'failed', received_count: completions.result.items.length,
      truncated: !!completions.result.truncated,
      labels_sample: completions.result.items.slice(0, 30).map(item => safe(item.label, 120)),
      matching_labels: completions.result.items.filter(item => item.label.includes(testCase.completion[2])).slice(0, 10).map(item => safe(item.label, 120))};
    const item = completions.result.items.find(item => item.label === testCase.completion[2]);
    assert.ok(item, 'Real completion must include the fixture symbol.');
    assert.equal(item.range.start.line, completionSite.line);
    assert.equal(item.range.end.line, completionSite.line);
    assert.ok(item.range.start.character <= completionSite.character && item.range.end.character >= completionSite.character + testCase.completion[2].length,
      'Completion replacement must cover the existing suffix after the caret.');
    const completionButton = pane().getByRole('button', {name: item.label + (item.detail ? ' · ' + item.detail : ''), exact: true}).first();
    assert.equal(await completionButton.isEnabled(), true, 'Normalized completion must be insertable.');
    await completionButton.click();
    const completed = await page.locator('#editor').inputValue();
    assert.equal(completed, completionOracle(originals[completionSite.path], item), 'UI completion edits must exactly match all actual server replacement ranges.');
    const suffixDuplicate = testCase.completion[2] + testCase.completion[2].slice(within);
    assert.ok(!completed.includes(suffixDuplicate), 'Accepting completion must not duplicate the existing symbol suffix.');
    assert.ok(completed.includes(item.insert_text.replace(/\r\n?/g, '\n')), 'Completion must insert the actual language-server text.');
    assert.deepEqual(await diskTexts(project, testCase.files), originals, 'Completion is a draft edit, not a disk write.');
    await page.locator('#reload').click();
    await until(async () => await page.locator('#editor').inputValue() === originals[completionSite.path], 'restore clean source after completion');
    evidence.completion = {status: 'passed', label: item.label, range: item.range, mid_symbol_caret: within, suffix_not_duplicated: true, disk_unchanged: true};
    evidence.checks.push('real-hover-definition-navigation-exact-references-and-completion-insertion');

    await cursor(querySite);
    await page.locator('#editor').fill(originals[querySite.path] + (language === 'python' ? '# browser unsaved draft\n' : language === 'vhdl' ? '-- browser unsaved draft\n' : '// browser unsaved draft\n'));
    assert.equal(await operationButton('rename').isEnabled(), false);
    assert.match(await operationButton('rename').getAttribute('title'), /Save all/);
    assert.deepEqual(await diskTexts(project, testCase.files), originals);
    await page.locator('#reload').click();
    await until(async () => await page.locator('#editor').inputValue() === originals[querySite.path], 'reload intentional dirty draft');
    evidence.checks.push('dirty-buffer-blocks-rename-without-disk-writes');

    const plan = await preview(native.ready, testCase, querySite, originals, expected);
    assert.deepEqual(await diskTexts(project, testCase.files), originals, 'Preview must not write any local file.');
    assert.equal(applyRequests, 0, 'A rename preview must require a separate explicit Apply action.');
    const applied = await transaction(native.ready, 'apply', pane().getByRole('button', {name: 'Apply rename to local files', exact: true}));
    assert.equal(typeof applied.undo_id, 'string');
    assert.deepEqual(await diskTexts(project, testCase.files), expected, 'All applied file bytes must exactly match the semantic oracle.');
    for (const relative of testCase.files) {
      await openFile(relative);
      assert.equal(await page.locator('#editor').inputValue(), expected[relative], 'Updated browser model must match disk for ' + relative);
    }
    evidence.native_post_edit = await nativeValidation(language, project, testCase);
    assert.notEqual(evidence.native_post_edit.status, 'failed', language + ' native post-edit check failed: ' + evidence.native_post_edit.stderr);
    await page.locator('[data-result="intelligence"]').click();
    const undone = await transaction(native.ready, 'undo', pane().getByRole('button', {name: 'Undo last rename', exact: true}));
    assert.equal(undone.files.length, testCase.files.length);
    assert.deepEqual(await diskTexts(project, testCase.files), originals, 'Undo must restore every exact original file.');
    for (const relative of testCase.files) {
      await openFile(relative);
      assert.equal(await page.locator('#editor').inputValue(), originals[relative], 'Undo must refresh the browser model for ' + relative);
    }
    evidence.rename = {status: 'passed', edit_count: testCase.rename_sites.length, file_count: plan.files.length,
      exact_preview_and_applied_bytes: true, preserved_unrelated_comments_and_shadows: true, undo_exact_original_bytes: true,
      before_sha256: Object.fromEntries(testCase.files.map(relative => [relative, sha(originals[relative])])),
      after_sha256: Object.fromEntries(testCase.files.map(relative => [relative, sha(expected[relative])]))};
    evidence.checks.push('semantic-cross-file-rename-preview-explicit-apply-exact-bytes-and-undo');

    await preview(native.ready, testCase, querySite, originals, expected);
    const conflictFile = testCase.definition_file;
    const external = originals[conflictFile] + (language === 'python' ? '# external change\n' : language === 'vhdl' ? '-- external change\n' : '// external change\n');
    await fs.writeFile(path.join(project, conflictFile), external);
    const conflict = await transaction(native.ready, 'apply', pane().getByRole('button', {name: 'Apply rename to local files', exact: true}), 409);
    assert.match(conflict.error, /changed|outside|preview|source/i);
    assert.deepEqual(await diskTexts(project, testCase.files), {...originals, [conflictFile]: external}, 'Cross-file preflight conflict must prevent every planned write.');
    assert.match(await page.locator('#global-status').textContent(), /changed|outside|preview|source/i);
    await fs.writeFile(path.join(project, conflictFile), originals[conflictFile]); // Disposable fixture cleanup only.
    evidence.conflict = {status: 'passed', http_status: 409, externally_changed_path: conflictFile, no_partial_writes: true};
    evidence.checks.push('external-cross-file-change-rejects-apply-without-partial-writes');
    await storageCheck();
    evidence.checks.push('credentials-and-source-not-persisted-without-opt-in');
    if (language === 'python') {
      // Real connection loss must update the whole application, not just the
      // semantic pane. Keep a dirty draft to prove disconnect retains edits.
      await cursor(querySite);
      const retained = originals[querySite.path] + '# retained after native companion disconnect\n';
      await page.locator('#editor').fill(retained);
      assert.equal(await page.locator('#save').isEnabled(), true);
      await stop(native.child);
      await operationButton('hover').click();
      await until(async () => await page.locator('#connection-indicator').textContent() === 'Disconnected', 'visible native semantic-query disconnect');
      assert.equal(await page.locator('#run').isEnabled(), false);
      assert.equal(await page.locator('#save').isEnabled(), false);
      assert.equal(await page.locator('#save-all').isEnabled(), false);
      assert.equal(await page.locator('#editor').isEnabled(), true, 'Offline drafts should remain editable.');
      assert.equal(await page.locator('#editor').inputValue(), retained, 'Failed semantic queries must retain dirty source text.');
      assert.match(await page.locator('#connection-status').textContent(), /Disconnected.*Retry/i);
      assert.deepEqual(await diskTexts(project, testCase.files), originals, 'Disconnect must not write or discard a browser draft.');
      await storageCheck();
      evidence.disconnect = {status: 'passed', actual_owned_companion_stopped: true,
        disconnected_header: true, run_and_save_disabled: true, unsaved_draft_retained_and_editable: true, disk_unchanged: true};
      evidence.checks.push('actual-native-disconnect-refreshes-global-controls-and-retains-dirty-drafts');
    }
    evidence.status = 'passed';
    checked(language + '-real-semantic-editor-UI-and-transaction-acceptance');
    if (language === 'python') await screenshot();
    await capture.close();
    captured.closed = true;
    await context.close();
    page = null;
    await stop(native.child);
  }
  assert.deepEqual(staticErrors, [], 'Built project-prefix preview must not request missing assets.');
  assert.deepEqual(assetErrors, [], 'Static editor assets must load successfully in every fresh context.');
  assert.deepEqual(scriptErrors, [], 'Browser must not report script errors.');
  report.state = 'passed';
} catch (error) {
  report.error = safe(error?.stack || error);
  if (page) {
    report.visible_status = safe(await page.locator('#global-status').textContent().catch(() => ''), 2000);
    report.editor_status = safe(await pane().locator('[role="status"]').textContent().catch(() => ''), 2000);
    report.capability_status = safe(await pane().locator('.intelligence-capabilities').textContent().catch(() => ''), 2000);
    await screenshot().catch(() => {});
  }
} finally {
  for (const captured of assetCaptures) if (!captured.closed) {
    try { await captured.capture.close(); }
    catch (error) { assetErrors.push({language: captured.language, phase: 'close-static-response-capture', error: safe(error.message, 2000)}); }
  }
  await browser?.close();
  for (const child of children) await stop(child);
  if (staticServer) await new Promise(resolve => staticServer.close(resolve));
  await fs.rm(temporary, {recursive: true, force: true});
  report.asset_capture = assetCaptures.map(({language, capture}) => ({language, ...capture.summary()}));
  report.asset_errors = assetErrors;
  report.script_errors = scriptErrors.map(error => safe(error, 2000));
  report.static_errors = staticErrors;
  if (assetCaptures.some(({capture}) => capture.errors.length) || assetErrors.length || scriptErrors.length || staticErrors.length) report.state = 'failed';
}
const json = JSON.stringify(report, null, 2) + '\n';
if (args['--output']) {
  await fs.mkdir(path.dirname(path.resolve(args['--output'])), {recursive: true});
  await fs.writeFile(args['--output'], json);
} else process.stdout.write(json);
if (report.state !== 'passed') process.exitCode = 1;
