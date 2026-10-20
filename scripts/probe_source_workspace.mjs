#!/usr/bin/env node
// Actual native textarea UI, lexical worker and temporary companion project.
// Explicit worker/poll transport faults below never substitute source, tokens,
// project writes or native job results.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
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
const args = {}, allowed = new Set(['--public-url', '--python', '--playwright-module', '--executable', '--output', '--screenshot']);
for (let index = 2; index < process.argv.length; index += 2) {
  const name = process.argv[index], value = process.argv[index + 1];
  if (!allowed.has(name) || !value || value.startsWith('--') || args[name] !== undefined) throw new Error('Expected each supported --option once, followed by its value.');
  args[name] = value;
}
const publicUrl = args['--public-url'] ? new URL(args['--public-url']) : null;
if (publicUrl) {
  assert.equal(publicUrl.protocol, 'https:');
  assert.ok(!publicUrl.username && !publicUrl.password && !publicUrl.search && !publicUrl.hash);
  assert.ok(publicUrl.pathname.endsWith('/workspace/'));
}
const python = args['--python'] || process.env.VWB_PYTHON || 'python3';
const {chromium} = require(args['--playwright-module'] || 'playwright');
const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'vwb-source-browser-'));
const project = path.join(temporary, 'project µ');
const prefix = publicUrl ? new URL('../', publicUrl).pathname : '/verilog-workbench/';
const htmlLiteral = '<img src=x onerror="window.__SOURCE_INJECTED__=true"> & </script>';
const originals = {
  'src/highlight.sv': '\ufeffmodule highlighted_top;\r\n\tlogic [7:0] count = 8\'h2a;\r\n\tstring message = "literal text";\r\n// ' + htmlLiteral + ' 😀 e\u0301\r\nendmodule\r\n\r\n',
  'src/highlight.vhd': 'entity highlighted is\nend entity;\narchitecture rtl of highlighted is\n\tsignal count : integer := 42;\nbegin\n\treport "literal text";\n-- ' + htmlLiteral + ' 😀 e\u0301\nend architecture;\n\n',
  'src/highlight.py': 'def highlighted(value):\n\tmessage = "literal text"\n\treturn value + 42\n# ' + htmlLiteral + ' 😀 e\u0301\n\n',
  'src/find.py': '# İ before needle NEEDLE 😀needle e\u0301needle\n# [a+b] $& literal text\n',
  // Leave a small allowance for a real edit while staying below 2 MiB UTF-8.
  'src/byte_limit.py': '# ' + 'x'.repeat(2 * 1024 * 1024 - 256) + '\n',
  'src/line_limit.py': '# x\n'.repeat(10001),
  'src/token_limit.py': 'x = 1; '.repeat(3000) + '\n',
};
const layoutLines = Array.from({length: 180}, (_, index) => index === 129
  ? '\tlogic marker_130 = 42; // ' + 'long '.repeat(250) + '😀 e\u0301 END_MARKER'
  : `\tlogic line_${index + 1} = ${index}; // 😀 e\u0301`);
originals['src/layout.sv'] = layoutLines.join('\n') + '\n';
const normalized = value => value.replace(/\r\n?/g, '\n');
const sha = value => createHash('sha256').update(value).digest('hex');
const secrets = new Set([temporary, project, repo]);
const children = [], scriptErrors = [], assetErrors = [], workers = [];
const loadedAssets = new Map();
let browser, context, page, staticServer, origin, workspaceUrl, endpoint, credential, assetCapture, featureScreenshotSaved = false;
const report = {
  schema: 'vwb.source-workspace-browser-probe/1', recorded_at: new Date().toISOString(), state: 'failed',
  target: publicUrl ? 'public-HTTPS-workspace' : 'built-local-project-prefix-workspace',
  pages_prefix: prefix, ...(publicUrl ? {public_url: publicUrl.href} : {}), checks: [],
  permission_grant: 'not_run', native_job: 'not_run', native_simulation: 'not_run', language_services: 'not_run',
  hardware_tested: false, transport_fault_injection: [],
  note: 'Actual Chromium native textarea, lexical worker and real temporary Unicode project. Pairing, editing, saving and search use normal rendered controls. Explicit browser instrumentation delays real worker responses, raises one simulated worker transport error and holds one real native inventory poll. Stop responsiveness is a real cancellation HTTP request while that poll is held; no running compiler cancellation or semantic correctness is implied. Public runs grant local-network access through Chromium test APIs, not a manual permission prompt.',
};
const checked = name => { report.checks.push(name); process.stderr.write('[source-probe] passed ' + name + '\n'); };
function safe(value, limit = 16000) {
  let message = String(value);
  for (const secret of secrets) if (secret) message = message.replaceAll(secret, '[redacted]');
  return message.slice(0, limit);
}
async function until(predicate, label, timeout = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 35));
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
  let stderr = '';
  child.stderr.on('data', data => { stderr = (stderr + data).slice(-8000); });
  const ready = await new Promise((resolve, reject) => {
    let output = '', settled = false;
    const finish = (error, value) => {
      if (settled) return; settled = true; clearTimeout(timer);
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
        assert.equal(value.event, 'ready'); assert.equal(new URL(value.url).hostname, '127.0.0.1');
        assert.equal(typeof value.pairing_code, 'string'); secrets.add(value.pairing_code); finish(null, value);
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
  await new Promise((resolve, reject) => { staticServer.once('error', reject); staticServer.listen(0, '127.0.0.1', resolve); });
  origin = `http://127.0.0.1:${staticServer.address().port}`;
  workspaceUrl = origin + prefix + 'workspace/';
}
async function pair(ready) {
  endpoint = ready.url;
  await page.locator('#connect-toggle').click();
  await page.locator('#endpoint').fill(endpoint); await page.locator('#code').fill(ready.pairing_code);
  const [response] = await Promise.all([page.waitForResponse(value => value.url() === endpoint + '/v1/pair'), page.locator('#pair').click()]);
  const data = await response.json();
  assert.equal(response.status(), 200); assert.equal(data.protocol, 1); assert.equal(typeof data.token, 'string');
  credential = data.token; secrets.add(credential);
  await until(() => page.locator('#refresh').isEnabled(), 'paired project inventory');
  assert.equal(await page.locator('#code').inputValue(), '');
}
async function openFile(relative) {
  await page.locator('[data-explorer="files"]').click();
  await page.locator('#explorer button[data-path]').filter({hasText: relative}).click();
  await until(async () => await page.locator('#active-file').textContent() === relative, 'active file ' + relative);
  await page.locator('#editor').waitFor({state: 'visible'});
}
async function highlighted(expected) {
  await until(async () => await page.locator('#source-editor').getAttribute('data-syntax-mode') === 'highlighted', 'highlighted current source');
  assert.equal(await page.locator('#editor').inputValue(), expected);
  const backdrop = await page.locator('#syntax-backdrop').textContent();
  const sentinel = await page.locator('#syntax-backdrop').getAttribute('data-trailing-line') === 'true';
  assert.equal(backdrop, expected + (sentinel ? '\n' : ''), 'Decoration must preserve every source UTF-16 code unit.');
  assert.equal(sentinel, expected.endsWith('\n'), 'Only the last blank visual line may need a sentinel.');
  for (const id of ['syntax-backdrop', 'editor-gutter']) assert.equal(await page.locator('#' + id).getAttribute('aria-hidden'), 'true');
  assert.equal(await page.locator('#syntax-backdrop img, #syntax-backdrop script, #syntax-backdrop iframe').count(), 0);
  assert.equal(await page.evaluate(() => window.__SOURCE_INJECTED__ === true), false);
}
async function selected() {
  return page.locator('#editor').evaluate(editor => ({start: editor.selectionStart, end: editor.selectionEnd, text: editor.value.slice(editor.selectionStart, editor.selectionEnd)}));
}
async function screenshot() {
  if (args['--screenshot'] && page) await page.screenshot({path: args['--screenshot'], fullPage: true, mask: [page.locator('#connect-dialog')]});
}

try {
  await fs.mkdir(path.join(project, 'src'), {recursive: true}); await fs.mkdir(path.join(project, 'test'));
  await fs.writeFile(path.join(project, '.vwb.json'), JSON.stringify({version: 1, src_dir: 'src', test_dir: 'test', build_dir: '.vwb'}) + '\n');
  for (const [relative, text] of Object.entries(originals)) await fs.writeFile(path.join(project, relative), text);
  if (publicUrl) { origin = publicUrl.origin; workspaceUrl = publicUrl.href; } else await serveBuild();
  const native = await companion();
  browser = await chromium.launch({headless: true, executablePath: args['--executable'], args: ['--no-sandbox']});
  report.browser = await browser.version(); report.browser_security_flags = ['--no-sandbox'];
  context = await browser.newContext({viewport: {width: 1512, height: 1050}});
  if (publicUrl) {
    await context.grantPermissions(['local-network-access'], {origin});
    report.permission_grant = 'explicit-simulated-Chromium-context-grantPermissions-local-network-access';
  } else report.permission_grant = 'not-required-loopback-HTTP-preview-origin-no-browser-security-bypass';
  assetCapture = await captureSiteAssets(context, {origin, prefix,
    onAsset: ({path, sha256}) => loadedAssets.set(path, sha256),
    onError: error => assetErrors.push({...error, ...(error.error ? {error: safe(error.error)} : {})}),
  });
  // Transparent wrapper forwards genuine worker responses. The only controls
  // are an explicit delayed delivery and an explicit injected transport error.
  await context.addInitScript(() => {
    const NativeWorker = window.Worker;
    const control = window.__vwbSourceProbe = {hold: false, fail: false, held: [], observed: 0, failures: 0,
      release() { const deliveries = this.held.splice(0); for (const deliver of deliveries) deliver(); }};
    window.Worker = class extends EventTarget {
      constructor(url, options) {
        super(); this.native = new NativeWorker(url, options); this.lexical = new URL(url, location.href).pathname.endsWith('/syntax-worker.mjs');
        this.native.onmessage = event => {
          const deliver = () => { const value = new MessageEvent('message', {data: event.data}); this.dispatchEvent(value); this.onmessage?.(value); };
          if (this.lexical) control.observed++;
          if (this.lexical && control.hold) control.held.push(deliver); else deliver();
        };
        this.native.onerror = event => this.deliverError(event.message);
        this.native.onmessageerror = () => { const value = new MessageEvent('messageerror'); this.dispatchEvent(value); this.onmessageerror?.(value); };
      }
      deliverError(message) { const value = new ErrorEvent('error', {message, cancelable: true}); this.dispatchEvent(value); this.onerror?.(value); }
      postMessage(value, transfer) {
        if (this.lexical && control.fail) { control.fail = false; control.failures++; setTimeout(() => this.deliverError('Explicit source-probe worker transport fault'), 0); }
        else if (transfer === undefined) this.native.postMessage(value); else this.native.postMessage(value, transfer);
      }
      terminate() { this.native.terminate(); }
    };
  });
  page = await context.newPage(); page.setDefaultTimeout(20000);
  await assetCapture.attachPage(page);
  page.on('pageerror', error => scriptErrors.push(safe(error.message)));
  page.on('worker', worker => workers.push(worker.url()));
  page.on('dialog', dialog => dialog.accept());
  const navigation = await page.goto(workspaceUrl, {waitUntil: 'networkidle', timeout: 30000});
  assert.equal(navigation.status(), 200); assert.equal(new URL(page.url()).pathname, new URL(workspaceUrl).pathname);
  await pair(native.ready);
  checked('project-prefix-entry-and-real-native-companion-UI-pairing');

  for (const [language, relative] of [['systemverilog', 'src/highlight.sv'], ['vhdl', 'src/highlight.vhd'], ['python', 'src/highlight.py']]) {
    await openFile(relative); const text = normalized(originals[relative]); await highlighted(text);
    for (const kind of ['keyword', 'comment', 'string', 'number']) assert.ok(await page.locator('#syntax-backdrop .tok-' + kind).count() > 0, language + ' must color lexical ' + kind);
    const gutter = (await page.locator('#editor-gutter').textContent()).trim().split('\n').map(Number);
    assert.deepEqual(gutter, Array.from({length: text.split('\n').length}, (_, index) => index + 1), 'Gutter must include the final blank line.');
    assert.ok(text.includes(htmlLiteral) && text.includes('😀') && text.includes('e\u0301') && text.includes('\t'));
    assert.equal(await fs.readFile(path.join(project, relative), 'utf8'), originals[relative], 'Read-only decoration must not alter local bytes.');
    checked(language + '-real-lexical-tokens-exact-Unicode-tabs-trailing-lines-and-no-source-HTML-injection');
  }
  await screenshot(); featureScreenshotSaved = !!args['--screenshot'];
  report.screenshot_stage = featureScreenshotSaved ? 'normal-highlighted-source-before-explicit-worker-and-network-faults' : 'not_requested';

  // This exercises the persisted-event cleanup guard only. Dispatching the DOM
  // event does not navigate, enter the browser cache or prove actual BFCache.
  const beforePersistedEvent = await page.evaluate(() => {
    const observed = window.__vwbSourceProbe.observed;
    window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}));
    return observed;
  });
  await openFile('src/find.py'); await highlighted(originals['src/find.py']);
  assert.ok(await page.evaluate(() => window.__vwbSourceProbe.observed) > beforePersistedEvent);
  await page.locator('#find-open').click(); await page.locator('#find-query').fill('needle');
  assert.equal((await selected()).text, 'needle');
  await page.locator('#find-close').click();
  report.persisted_pagehide = 'explicit-DOM-event-only-real-worker-and-Find-remain-functional-not-actual-BFCache-proof';
  checked('explicit-persisted-pagehide-DOM-event-keeps-real-worker-and-Find-functional-without-BFCache-claim');

  await openFile('src/layout.sv'); await highlighted(originals['src/layout.sv']);
  const alignment = await page.locator('#editor').evaluate(async (editor, text) => {
    const backdrop = document.getElementById('syntax-backdrop'), gutter = document.getElementById('editor-gutter');
    const style = getComputedStyle(editor), painted = getComputedStyle(backdrop);
    const same = ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'fontVariantLigatures', 'lineHeight', 'letterSpacing', 'wordSpacing', 'tabSize']
      .map(property => ({property, editor: style[property], backdrop: painted[property]}));
    function at(root, offset) {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let node; (node = walker.nextNode());) {
        if (offset < node.length) { const range = document.createRange(); range.setStart(node, offset); range.setEnd(node, offset + 1); return range.getBoundingClientRect(); }
        offset -= node.length;
      }
      throw new Error('Cannot locate painted character.');
    }
    const mirror = document.createElement('pre');
    for (const property of ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'fontVariantLigatures', 'lineHeight', 'letterSpacing', 'wordSpacing', 'tabSize', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'borderTopWidth', 'borderLeftWidth', 'boxSizing']) mirror.style[property] = style[property];
    const box = editor.getBoundingClientRect();
    Object.assign(mirror.style, {position: 'fixed', top: box.top + 'px', left: box.left + 'px', margin: '0', width: editor.offsetWidth + 'px', whiteSpace: 'pre', visibility: 'hidden', pointerEvents: 'none'});
    mirror.textContent = text; document.body.append(mirror);
    const marker = text.indexOf('marker_130'), character = marker + 5;
    const origin = at(mirror, character), gutterX = gutter.getBoundingClientRect().left;
    editor.scrollTop = 129 * parseFloat(style.lineHeight); editor.scrollLeft = 110;
    editor.dispatchEvent(new Event('scroll'));
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const actual = at(backdrop, character), gutterLine = at(gutter, Array.from({length: 129}, (_, index) => String(index + 1)).join('\n').length + 1);
    const value = {same, scrollTop: editor.scrollTop, scrollLeft: editor.scrollLeft,
      x_delta: actual.left - (origin.left - editor.scrollLeft), y_delta: actual.top - (origin.top - editor.scrollTop),
      gutter_y_delta: gutterLine.top - actual.top, gutter_x_delta: gutter.getBoundingClientRect().left - gutterX};
    mirror.remove(); return value;
  }, originals['src/layout.sv']);
  for (const value of alignment.same) assert.equal(value.editor, value.backdrop, 'Text metrics differ: ' + value.property);
  assert.ok(alignment.scrollTop > 0 && alignment.scrollLeft > 0, 'Both scroll axes must actually move.');
  for (const key of ['x_delta', 'y_delta', 'gutter_y_delta', 'gutter_x_delta']) assert.ok(Math.abs(alignment[key]) <= 1.5, key + ': ' + alignment[key]);
  report.alignment = alignment;
  checked('actual-token-and-line-number-geometry-matches-native-textarea-after-both-scroll-axes');

  await openFile('src/find.py'); await highlighted(originals['src/find.py']);
  await page.locator('#editor').focus(); await page.locator('#editor').press('Control+f');
  await page.locator('#find-bar').waitFor({state: 'visible'});
  assert.ok(await page.locator('#find-query').evaluate(element => document.activeElement === element));
  await page.locator('#find-query').fill('needle');
  const oracle = [...originals['src/find.py'].matchAll(/needle/gi)].map(match => match.index);
  const seen = new Set();
  for (let index = 0; index < oracle.length + 1; index++) {
    await page.locator('#find-next').click(); const location = await selected();
    assert.ok(oracle.includes(location.start), 'Search offset must remain correct after expanding Unicode lowercase and emoji.');
    assert.equal(location.end - location.start, 6); seen.add(location.start);
  }
  assert.deepEqual([...seen].sort((a, b) => a - b), oracle);
  await page.locator('#find-case').check();
  const exactOracle = [...originals['src/find.py'].matchAll(/needle/g)].map(match => match.index);
  const exactSeen = new Set();
  for (let index = 0; index < exactOracle.length + 1; index++) { await page.locator('#find-next').click(); const location = await selected(); assert.equal(location.text, 'needle'); exactSeen.add(location.start); }
  assert.deepEqual([...exactSeen].sort((a, b) => a - b), exactOracle);
  await page.locator('#find-query').fill('[a+b]'); await page.locator('#find-query').press('Enter');
  assert.equal((await selected()).text, '[a+b]', 'Find must treat regex punctuation literally.');
  await page.locator('#find-query').fill('$&'); await page.locator('#find-query').press('Shift+Enter');
  assert.equal((await selected()).text, '$&');
  await page.locator('#find-query').fill('absent literal'); assert.match(await page.locator('#find-status').textContent(), /no match|0 match/i);
  assert.ok(await page.locator('#find-next').isDisabled()); assert.ok(await page.locator('#find-prev').isDisabled());
  await page.locator('#find-query').press('Escape'); await page.locator('#find-bar').waitFor({state: 'hidden'});
  assert.ok(await page.locator('#editor').evaluate(element => document.activeElement === element));
  assert.equal(await page.locator('#editor').inputValue(), originals['src/find.py']);
  assert.ok(await page.locator('#save').isDisabled(), 'Find must not dirty the file.');
  checked('read-only-literal-find-Unicode-offsets-case-toggle-next-previous-and-keyboard-focus');

  await openFile('src/highlight.sv'); const nativeOriginal = normalized(originals['src/highlight.sv']);
  await page.locator('#editor').focus(); await page.locator('#editor').press('Control+End');
  const addition = '// native undo µ😀 e\u0301\n'; await page.keyboard.insertText(addition);
  await highlighted(nativeOriginal + addition);
  await page.locator('#editor').press('Control+z'); await until(async () => await page.locator('#editor').inputValue() === nativeOriginal, 'native undo after decoration');
  await page.locator('#editor').press('Control+Shift+z'); await until(async () => await page.locator('#editor').inputValue() === nativeOriginal + addition, 'native redo after decoration');
  await highlighted(nativeOriginal + addition); await page.locator('#editor').press('Control+s');
  const expectedDisk = originals['src/highlight.sv'] + addition.replace(/\n/g, '\r\n');
  await until(async () => await fs.readFile(path.join(project, 'src/highlight.sv'), 'utf8') === expectedDisk, 'exact CRLF and BOM save');
  originals['src/highlight.sv'] = expectedDisk;
  checked('native-textarea-input-undo-redo-and-keyboard-save-preserve-CRLF-BOM-and-Unicode');

  for (const [relative, reason] of [['src/byte_limit.py', /256 KiB|size limit|byte limit/i], ['src/line_limit.py', /10,000-line|line limit/i], ['src/token_limit.py', /token limit/i]]) {
    await openFile(relative);
    await until(async () => await page.locator('#source-editor').getAttribute('data-syntax-mode') === 'plain' && reason.test(await page.locator('#syntax-status').textContent()), 'visible bounded plain text: ' + relative);
    assert.equal(await page.locator('#editor').inputValue(), originals[relative]);
    assert.equal(await page.locator('#syntax-backdrop .syntax-token').count(), 0);
    assert.ok(await page.locator('#editor').isEnabled());
  }
  checked('byte-line-and-token-budgets-use-explicit-plain-text-fallback-with-complete-editable-source');

  // Bound search memory without silently reporting a truncated count as exact.
  await openFile('src/byte_limit.py'); await page.locator('#find-open').click();
  await page.locator('#find-query').fill('x');
  assert.match(await page.locator('#find-status').textContent(), /2000|2,000|limit|at least/i);
  await page.locator('#find-next').click(); assert.equal((await selected()).text, 'x');
  await page.locator('#find-close').click();
  checked('large-literal-search-remains-bounded-and-discloses-truncated-match-inventory');

  // The job and its response are real. Holding its first poll keeps Stop visible
  // even when native discovery has already finished; it proves UI/request
  // responsiveness, not cancellation of an active simulator process.
  const pollMatcher = url => url.origin === new URL(endpoint).origin && /^\/v1\/jobs\/[A-Za-z0-9_-]+$/.test(url.pathname);
  let pollHeld = false, releasePoll, pollFailure;
  const pollTasks = [];
  const pollReleased = new Promise(resolve => { releasePoll = resolve; });
  await page.route(pollMatcher, route => {
    const task = (async () => { const response = await route.fetch(); pollHeld = true; await pollReleased; await route.fulfill({response}); })()
      .catch(error => { pollFailure = error; });
    pollTasks.push(task); return task;
  });
  try {
    await page.locator('#command').selectOption('list'); await page.locator('#run').click(); await until(() => pollHeld, 'held real inventory poll');
    await page.locator('#editor').focus(); await page.locator('#editor').press('Control+End'); await page.keyboard.insertText('# large native edit µ\n');
    const largeEdited = originals['src/byte_limit.py'] + '# large native edit µ\n';
    assert.equal(await page.locator('#editor').inputValue(), largeEdited);
    const [cancelled] = await Promise.all([
      page.waitForResponse(response => response.url().startsWith(endpoint + '/v1/jobs/') && response.url().endsWith('/cancel')),
      page.locator('#cancel').click(),
    ]);
    assert.equal(cancelled.status(), 200); releasePoll(); await Promise.all(pollTasks); await page.unroute(pollMatcher);
    if (pollFailure) throw pollFailure;
    await until(() => page.locator('#cancel').isDisabled(), 'real inventory poll released');
    await page.locator('#save').click();
    await until(async () => await fs.readFile(path.join(project, 'src/byte_limit.py'), 'utf8') === largeEdited, 'large plain file exact disk save');
    originals['src/byte_limit.py'] = largeEdited;
  } finally { releasePoll(); await Promise.all(pollTasks); await page.unroute(pollMatcher); }
  report.native_job = 'real-vwb-list-and-real-Stop-request-with-explicit-delayed-poll';
  report.large_file = {utf8_bytes_after_real_edit_and_save: Buffer.byteLength(originals['src/byte_limit.py']), supported_file_limit_bytes: 2 * 1024 * 1024};
  report.transport_fault_injection.push('held-real-native-inventory-poll-for-Stop-UI-responsiveness-without-running-process-cancellation-claim');
  checked('large-plain-file-native-input-save-and-Stop-request-remain-responsive');

  await openFile('src/highlight.sv'); await highlighted(normalized(originals['src/highlight.sv']));
  await page.evaluate(() => { window.__vwbSourceProbe.hold = true; });
  await page.locator('#editor').focus(); await page.locator('#editor').press('Control+End'); await page.keyboard.insertText('// delayed real worker response\n');
  await until(() => page.evaluate(() => window.__vwbSourceProbe.held.length > 0), 'held genuine lexical worker response');
  await openFile('src/highlight.py');
  await page.evaluate(() => { const control = window.__vwbSourceProbe; control.hold = false; control.release(); });
  await highlighted(originals['src/highlight.py']);
  assert.equal(await page.locator('#syntax-backdrop .tok-keyword').filter({hasText: /^module$/}).count(), 0);
  report.transport_fault_injection.push('delayed-genuine-lexical-worker-response-delivered-after-active-file-change');
  checked('delayed-real-worker-output-cannot-repaint-a-different-file-or-change-its-source');

  await page.evaluate(() => { window.__vwbSourceProbe.fail = true; });
  await page.locator('#editor').focus(); await page.locator('#editor').press('Control+End'); await page.keyboard.insertText('# worker fault draft\n');
  const faultDraft = originals['src/highlight.py'] + '# worker fault draft\n';
  await until(async () => await page.locator('#source-editor').getAttribute('data-syntax-mode') === 'plain' && /worker|unavailable|failed|error/i.test(await page.locator('#syntax-status').textContent()), 'visible worker transport failure fallback');
  assert.equal(await page.locator('#editor').inputValue(), faultDraft); assert.ok(await page.locator('#editor').isEnabled());
  await page.locator('#save').click(); await until(async () => await fs.readFile(path.join(project, 'src/highlight.py'), 'utf8') === faultDraft, 'native save after worker failure');
  originals['src/highlight.py'] = faultDraft;
  assert.equal(await page.evaluate(() => window.__vwbSourceProbe.failures), 1);
  report.transport_fault_injection.push('one-explicit-browser-worker-transport-error-no-substituted-token-or-source-response');
  checked('worker-failure-reveals-native-plain-text-retains-draft-and-permits-real-save');

  await stop(native.child); await page.locator('#refresh').click();
  await until(async () => await page.locator('#connection-indicator').textContent() === 'Disconnected', 'real companion disconnect');
  await page.locator('#editor').focus(); await page.locator('#editor').press('Control+End'); await page.keyboard.insertText('# offline retained draft\n');
  const offlineDraft = originals['src/highlight.py'] + '# offline retained draft\n';
  assert.equal(await page.locator('#editor').inputValue(), offlineDraft);
  assert.ok(await page.locator('#editor').isEnabled()); assert.ok(await page.locator('#save').isDisabled());
  assert.equal(await fs.readFile(path.join(project, 'src/highlight.py'), 'utf8'), originals['src/highlight.py']);
  checked('real-companion-disconnect-retains-editable-unsaved-source-and-prevents-disk-save');

  const manifest = await page.evaluate(async url => { const response = await fetch(url, {cache: 'no-store'}); if (!response.ok) throw new Error('Build manifest HTTP ' + response.status); return response.json(); }, origin + prefix + 'vwb-web-build.json');
  await assetCapture.drain();
  assert.equal(manifest.schema, 'vwb.web-build/1'); assert.equal(manifest.base_path, prefix);
  for (const asset of ['workspace/index.html', 'workspace/app.mjs', 'workspace/style.css', 'workspace/syntax.mjs', 'workspace/syntax-view.mjs', 'workspace/syntax-worker.mjs', 'workspace/search.mjs']) {
    assert.equal(loadedAssets.get(asset), manifest.files[asset], 'Actual loaded source asset must match build manifest: ' + asset);
  }
  assert.ok(workers.some(url => new URL(url).pathname.endsWith('/workspace/syntax-worker.mjs')), 'A real module worker must perform lexical work.');
  report.real_worker_response_count = await page.evaluate(() => window.__vwbSourceProbe.observed);
  assert.ok(report.real_worker_response_count > 0);
  const storage = await page.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}}));
  for (const secret of secrets) { assert.ok(!storage.includes(secret)); assert.ok(!page.url().includes(secret)); }
  assert.ok(!storage.includes('Bearer ') && !storage.includes('offline retained draft'));
  report.deployed_manifest = {schema: manifest.schema, status: manifest.status, base_path: manifest.base_path, file_count: Object.keys(manifest.files).length};
  report.fixture_sha256 = Object.fromEntries(Object.entries(originals).map(([relative, text]) => [relative, sha(text)]));
  assert.deepEqual(scriptErrors, []); assert.deepEqual(assetErrors, []);
  checked('real-lexical-worker-loaded-assets-match-manifest-without-source-or-credentials-in-browser-storage');
  report.state = 'passed'; if (!featureScreenshotSaved) await screenshot();
} catch (error) {
  report.error = safe(error.stack || error.message);
  if (page) {
    report.screenshot_stage = 'failure-state';
    report.visible_status = await page.locator('#global-status').textContent().then(value => safe(value)).catch(() => null);
    report.syntax_status = await page.locator('#syntax-status').textContent().then(value => safe(value)).catch(() => null);
    report.find_status = await page.locator('#find-status').textContent().then(value => safe(value)).catch(() => null);
    report.active_file = await page.locator('#active-file').textContent().then(value => safe(value)).catch(() => null);
    await screenshot().catch(() => {});
  }
} finally {
  try { await assetCapture?.close(); }
  catch (error) { assetErrors.push({phase: 'close-static-response-capture', error: safe(error.message)}); }
  await context?.close().catch(() => {}); await browser?.close().catch(() => {});
  for (const child of children) await stop(child);
  if (staticServer) await new Promise(resolve => staticServer.close(resolve));
  await fs.rm(temporary, {recursive: true, force: true});
  if (assetCapture) report.asset_capture = assetCapture.summary();
  report.script_errors = scriptErrors; report.asset_errors = assetErrors;
  if (assetCapture?.errors.length || assetErrors.length || scriptErrors.length) report.state = 'failed';
}
const json = safe(JSON.stringify(report, null, 2), 120000) + '\n';
if (args['--output']) await fs.writeFile(args['--output'], json); else process.stdout.write(json);
if (report.state !== 'passed') process.exitCode = 1;
