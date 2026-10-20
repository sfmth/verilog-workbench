#!/usr/bin/env node
// Real browser controls -> authenticated companion -> native HDL tools.
// Every workflow receives a fresh copy of the repository's M0 fixtures, so a
// previous run's output can never satisfy a later artifact assertion.
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
const fixtures = path.join(repo, 'toolchains/fixtures');
const require = createRequire(import.meta.url);
const args = {}, allowed = new Set(['--public-url', '--python', '--acquisition', '--only', '--playwright-module', '--executable', '--output', '--screenshot']);
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
const acquisition = args['--acquisition'] ? JSON.parse(await fs.readFile(args['--acquisition'], 'utf8')) : null;
const environment = {...process.env};
if (acquisition) {
  assert.equal(acquisition.schema_version, 'vwb.toolchain-acquisition/1'); assert.equal(acquisition.state, 'acquired');
  assert.equal(typeof acquisition.suitePath, 'string');
  for (const [name, value] of Object.entries(acquisition.environment || {})) {
    assert.ok(['PATH', 'VIRTUAL_ENV', 'VERILATOR_ROOT', 'GHDL_PREFIX', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'].includes(name));
    assert.equal(typeof value, 'string'); environment[name] = value;
  }
  delete environment.PYTHONHOME; delete environment.PYTHONPATH; delete environment.LD_LIBRARY_PATH;
}
const python = args['--python'] || acquisition?.probe?.python || (acquisition && path.join(acquisition.suitePath, 'py3bin/python3')) || process.env.VWB_PYTHON || 'python3';
const {chromium} = require(args['--playwright-module'] || 'playwright');
const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'vwb-hdl-browser-'));
const prefix = publicUrl ? new URL('../', publicUrl).pathname : '/verilog-workbench/';
const secrets = new Set([temporary, repo]);
if (acquisition) secrets.add(acquisition.suitePath);
const sha = value => createHash('sha256').update(value).digest('hex');
const children = [], contexts = [], scriptErrors = [], assetErrors = [];
const loadedAssets = new Map();
let browser, page, staticServer, origin, workspaceUrl, endpoint, project, screenshotSaved = false;
let manifestFingerprint;
const runtimeAssets = ['workspace/index.html', 'workspace/app.mjs', 'workspace/style.css', 'workspace/model.mjs', 'workspace/hdl.mjs', 'workspace/schematic.mjs'];
const report = {
  schema: 'vwb.hdl-workspace-browser-probe/1', recorded_at: new Date().toISOString(), state: 'failed',
  target: publicUrl ? 'public-HTTPS-workspace' : 'built-local-project-prefix-workspace',
  pages_prefix: prefix, ...(publicUrl ? {public_url: publicUrl.href} : {}), checks: [], workflows: {},
  permission_grant: 'not_run', hardware_tested: false, physical_programming: 'not_run',
  note: 'Actual Chromium controls, real temporary Unicode projects, authenticated local companion and native HDL tools. The 14 M0 workflows use repository fixtures, including the documented final queued-write shutdown extension, and the existing probe_toolchain.inspect_result oracle. Additional rows explicitly modify a copied fixture to prove selected-test filtering, real assertion/diagnostic failures, gate-level tests and pending-write shutdown. Every row starts with a fresh project and build directory. No fake tools, substituted job replies, transport faults or physical FPGA programming. Public local-network permission is explicitly simulated through Chromium test APIs, not a manual permission prompt.',
};
if (acquisition) report.tool_acquisition = {release: acquisition.archive?.release, archive: acquisition.archive?.archive, sha256: acquisition.archive?.sha256, size_bytes: acquisition.archive?.size_bytes, target: acquisition.target};
function safe(value, limit = 24000) {
  let message = String(value);
  for (const secret of secrets) if (secret) message = message.replaceAll(secret, '[redacted]');
  return message.slice(0, limit);
}
const checked = name => { report.checks.push(name); process.stderr.write('[hdl-probe] passed ' + name + '\n'); };
async function until(predicate, label, timeout = 30000) {
  const started = Date.now();
  while (Date.now() - started < timeout) { if (await predicate()) return; await new Promise(resolve => setTimeout(resolve, 60)); }
  throw new Error('Timed out: ' + label);
}
function pythonJSON(source, values = []) {
  const result = spawnSync(python, ['-I', '-c', source, repo, ...values], {cwd: repo, env: environment, encoding: 'utf8', maxBuffer: 32 * 1024 * 1024, timeout: 60000});
  assert.equal(result.status, 0, 'Native fixture inspection failed: ' + safe(result.stderr));
  return JSON.parse(result.stdout);
}
const definitions = pythonJSON(`import json,sys
sys.path.insert(0,sys.argv[1])
from scripts.probe_toolchain import SMOKE_WORKFLOWS,command_for
from companion.toolchains import WORKFLOW_TOOLS
print(json.dumps({name:{'argv':command_for(name),'required_tools':WORKFLOW_TOOLS[name]} for name in SMOKE_WORKFLOWS}))`);
const extraCases = {
  native_verilog_assertion_failure: {base: 'verilog_simulation', expected: 'failed', modify: 'verilog-assertion'},
  native_vhdl_assertion_failure: {base: 'vhdl_simulation', expected: 'failed', modify: 'vhdl-assertion'},
  cocotb_function_filter: {base: 'cocotb_verilog', modify: 'cocotb-guard', testcase: 'xor_truth_table'},
  cocotb_expected_assertion_failure: {base: 'cocotb_verilog', expected: 'failed', modify: 'cocotb-guard'},
  lint_source_diagnostic: {base: 'lint', expected: 'failed', modify: 'verilog-syntax', linter: 'iverilog'},
  gate_level_verilog: {base: 'verilog_simulation', gate_level: true},
  gate_level_cocotb_vhdl: {base: 'cocotb_vhdl', gate_level: true},
  compile_defines_includes_plusargs: {base: 'verilog_simulation', modify: 'compile-options', defines: ['VWB_BROWSER_DEFINE=1'], includes: ['src/include µ'], plusargs: ['+BROWSER_EXPECT=73'], max_array_words: 1},
  wave_saved_roundtrip: {base: 'waveforms', wave_tag: 'browser_asserted_run'},
  starter_native_verilog: {base: 'verilog_simulation', starter: true},
  hierarchy_and_test_selection: {base: 'synthesis', hierarchy: true, required_tools: ['iverilog', 'vvp']},
  cocotb_pending_write_shutdown: {base: 'cocotb_verilog', modify: 'pending-write-shutdown'},
};
for (const [name, extra] of Object.entries(extraCases)) definitions[name] = {...definitions[extra.base], ...extra};
const selected = args['--only'] ? args['--only'].split(',') : Object.keys(definitions);
assert.ok(selected.length && new Set(selected).size === selected.length && selected.every(name => definitions[name]), 'Select known workflow names once.');
report.selected_workflows = selected; report.full_probe_scope = selected.length === Object.keys(definitions).length;

async function walk(directory) {
  const found = [];
  for (const entry of await fs.readdir(directory, {withFileTypes: true})) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) found.push(...await walk(target)); else if (entry.isFile()) found.push(target);
  }
  return found.sort();
}
async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill(); await new Promise(resolve => { const timer = setTimeout(() => { child.kill('SIGKILL'); resolve(); }, 5000); child.once('exit', () => { clearTimeout(timer); resolve(); }); });
}
async function companion() {
  const child = spawn(python, ['-m', 'companion', '--project', project, '--origin', origin, '--src-dir', 'src', '--test-dir', 'test', '--build-dir', 'build', '--port', '0'], {cwd: repo, env: environment, stdio: ['ignore', 'pipe', 'pipe']});
  children.push(child); let stderr = '';
  child.stderr.on('data', data => { stderr = (stderr + data).slice(-10000); });
  const ready = await new Promise((resolve, reject) => {
    let output = '', settled = false;
    const finish = (error, value) => { if (settled) return; settled = true; clearTimeout(timer); if (error) reject(error); else resolve(value); };
    const timer = setTimeout(() => finish(new Error('Companion startup timed out: ' + safe(stderr))), 20000);
    child.once('error', error => finish(error)); child.once('exit', () => finish(new Error('Companion exited before startup: ' + safe(stderr))));
    child.stdout.on('data', data => {
      output += data; if (settled || !output.includes('\n')) return;
      try { const value = JSON.parse(output.split('\n')[0]); assert.equal(value.event, 'ready'); assert.equal(new URL(value.url).hostname, '127.0.0.1'); assert.equal(typeof value.pairing_code, 'string'); secrets.add(value.pairing_code); finish(null, value); }
      catch { finish(new Error('Invalid companion startup message')); }
    });
  });
  return {child, ready};
}
async function serveBuild() {
  const built = path.join(repo, 'dist/web'); await fs.access(path.join(built, 'vwb-web-build.json'));
  const mime = {'.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml'};
  staticServer = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      if (!pathname.startsWith(prefix)) throw new Error('Outside prefix');
      let target = path.resolve(built, pathname.slice(prefix.length));
      if (target !== built && !target.startsWith(built + path.sep)) throw new Error('Outside build');
      if ((await fs.stat(target)).isDirectory()) target = path.join(target, 'index.html');
      response.writeHead(200, {'Content-Type': mime[path.extname(target)] || 'application/octet-stream', 'Cache-Control': 'no-store'}); response.end(await fs.readFile(target));
    } catch { if (!request.url.includes('favicon.ico')) assetErrors.push({path: safe(request.url, 1000), status: 404}); response.writeHead(404); response.end('Not found'); }
  });
  await new Promise((resolve, reject) => { staticServer.once('error', reject); staticServer.listen(0, '127.0.0.1', resolve); });
  origin = `http://127.0.0.1:${staticServer.address().port}`; workspaceUrl = origin + prefix + 'workspace/';
}
async function pair(ready) {
  endpoint = ready.url;
  await page.locator('#connect-toggle').click(); await page.locator('#endpoint').fill(endpoint); await page.locator('#code').fill(ready.pairing_code);
  const [response] = await Promise.all([page.waitForResponse(value => value.url() === endpoint + '/v1/pair'), page.locator('#pair').click()]);
  const result = await response.json(); assert.equal(response.status(), 200); assert.equal(result.protocol, 1); secrets.add(result.token);
  await until(() => page.locator('#refresh').isEnabled(), 'UI pairing and native project inventory');
  assert.equal(await page.locator('#code').inputValue(), '');
}
async function openFile(relative) {
  await page.locator('[data-explorer="files"]').click();
  await page.locator('#explorer button[data-path]').filter({hasText: relative}).click();
  await until(async () => await page.locator('#active-file').textContent() === relative, 'active source ' + relative);
  await page.locator('#editor').waitFor({state: 'visible'});
}
function specFor(definition) {
  if (definition.hierarchy) return {command: 'list'};
  const argv = definition.argv, command = argv[0], option = flag => argv.includes(flag) ? argv[argv.indexOf(flag) + 1] : undefined;
  const spec = {command, module: ['test', 'lint', 'synth', 'fpga'].includes(command) ? argv[1] : undefined};
  if (command === 'test') Object.assign(spec, {test: option('--test'), test_top: option('--test-top'), test_language: option('--test-language'), waves: argv.includes('--waves'), wave_format: 'vcd', testcase: definition.testcase, gate_level: !!definition.gate_level});
  if (command === 'lint') spec.linter = definition.linter || option('--linter');
  if (command === 'synth') spec.format = option('--format');
  if (command === 'formal') spec.config = argv[1];
  if (command === 'fpga') { spec.board = option('--board'); spec.stage = 'pack'; spec.constraints = spec.board === 'tangnano9k' ? 'src/io.cst' : 'src/io.pcf'; }
  for (const key of ['defines', 'includes', 'plusargs', 'max_array_words']) if (definition[key] !== undefined) spec[key] = definition[key];
  if (definition.wave_tag) Object.assign(spec, {command: 'wave', wave_mode: 'simulate', wave_tag: definition.wave_tag});
  if (definition.starter) Object.assign(spec, {module: 'browser_starter', test: undefined, test_top: undefined, expected_stale: true});
  return spec;
}
async function configure(spec) {
  await page.locator('#command').selectOption(spec.command);
  if (spec.module) await page.locator('#module').fill(spec.module);
  if (spec.command === 'wave') {
    await page.locator('#wave-mode').selectOption(spec.wave_mode); await page.locator('#wave-tag').fill(spec.wave_tag || '');
  }
  if (spec.command === 'test' || spec.command === 'wave' && spec.wave_mode === 'simulate') {
    await page.locator('#test-language').selectOption(spec.test_language);
    await page.locator('#test-file').fill(spec.test || ''); await page.locator('#test-top').fill(spec.test_top || ''); await page.locator('#testcase').fill(spec.testcase || '');
    await page.locator('#waves').setChecked(spec.waves);
    if ((spec.gate_level || spec.defines || spec.includes || spec.plusargs) && !await page.locator('#hdl-options').evaluate(element => element.open)) await page.locator('#hdl-options summary').click();
    if (await page.locator('#gate-level').isChecked() !== spec.gate_level) await page.locator('#gate-level').setChecked(spec.gate_level);
    await page.locator('#seed').fill(spec.test_language === 'cocotb' ? '12345' : '');
    for (const [key, id] of [['defines', 'hdl-defines'], ['includes', 'hdl-includes'], ['plusargs', 'plusargs']]) if (spec[key]) await page.locator('#' + id).fill(spec[key].join('\n'));
    if (spec.max_array_words !== undefined) await page.locator('#max-array-words').fill(String(spec.max_array_words));
  }
  if (spec.command === 'lint') await page.locator('#linter').selectOption(spec.linter);
  if (spec.command === 'synth') await page.locator('#synth-format').selectOption(spec.format);
  if (spec.command === 'formal') await page.locator('#formal-config').fill(spec.config);
  if (spec.command === 'fpga') { await page.locator('#board').selectOption(spec.board); await page.locator('#stage').selectOption(spec.stage); await page.locator('#constraints').fill(spec.constraints); }
}
async function runUI(spec, expected, configureControls = true) {
  if (configureControls) await configure(spec);
  const [response] = await Promise.all([page.waitForResponse(value => value.url() === endpoint + '/v1/jobs' && value.request().method() === 'POST'), page.locator('#run').click()]);
  const payload = response.request().postDataJSON(), created = await response.json();
  assert.equal(response.status(), 202, JSON.stringify(created)); assert.equal(payload.command, spec.command);
  if (spec.module) assert.ok(payload.options.module === spec.module || payload.options.modules?.length === 1 && payload.options.modules[0] === spec.module, 'The browser must select exactly one requested design.');
  if (spec.command === 'test' || spec.command === 'wave' && spec.wave_mode === 'simulate') {
    assert.equal(payload.options.test, spec.test); assert.equal(payload.options.test_language, spec.test_language);
    if (spec.test_top) assert.equal(payload.options.test_top, spec.test_top);
    if (spec.testcase) assert.equal(payload.options.testcase, spec.testcase);
    if (spec.gate_level) assert.equal(payload.options.gate_level, true);
    for (const key of ['defines', 'includes', 'plusargs', 'max_array_words']) if (spec[key] !== undefined) assert.deepEqual(payload.options[key], spec[key]);
  }
  if (spec.command === 'wave') {
    if (spec.wave_mode === 'list') assert.equal(payload.options.list_saved, true);
    else if (spec.wave_mode === 'load') assert.equal(payload.options.load, spec.wave_tag);
    else assert.equal(payload.options.tag, spec.wave_tag);
  }
  let job;
  const deadline = Date.now() + 330000;
  while (Date.now() < deadline) {
    const visible = await page.locator('#job-status').textContent();
    if (new RegExp('^' + spec.command + '.*(?:passed|failed|cancelled|timed_out)').test(visible)) {
      // Read the last genuine job response that drove the UI, not another API
      // request which could finish before the browser actually displays it.
      job = await page.evaluate(async ({base, id}) => {
        const entries = window.__vwbHDLResponses || [];
        return entries.findLast(entry => entry.url === base + '/v1/jobs/' + id)?.body;
      }, {base: endpoint, id: created.id});
      if (job && job.status !== 'running' && await page.locator('#run').isEnabled()) break;
    }
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  assert.ok(job, 'Native job did not finish and display in the browser.');
  assert.equal(job.status, expected, safe((job.stdout || '') + '\n' + (job.stderr || ''), 12000));
  assert.equal(job.stale, !!spec.expected_stale); assert.equal(job.stdout_truncated, false); assert.equal(job.stderr_truncated, false);
  assert.equal(job.returncode === 0, expected === 'passed');
  const shown = await page.locator('#job-output').textContent();
  assert.equal(shown, [job.stdout, job.stderr].filter(Boolean).join('\n') || 'No output.');
  await until(() => page.locator('#refresh-artifacts').isEnabled(), 'artifact controls after finished job');
  return {job, payload};
}
async function modifyCopy(kind) {
  if (!kind) return null;
  if (kind === 'compile-options') {
    const changes = [];
    for (const relative of ['src/m0_gate.v', 'test/test_m0_gate.v']) {
      const before = await fs.readFile(path.join(project, relative), 'utf8');
      const after = relative.startsWith('src/') ? '`include "browser_defs.vh"\n`ifndef VWB_BROWSER_DEFINE\nmissing_required_browser_define\n`endif\n' + before.replace('a ^ b', '(a ^ b) ^ `VWB_HEADER_MASK') :
        before.replace('  initial begin', '  integer browser_expect;\n  initial begin\n    if (!$value$plusargs("BROWSER_EXPECT=%d", browser_expect) || browser_expect != 73) $fatal(1, "BROWSER_PLUSARG_MISSING");\n    $display("BROWSER_COMPILE_OPTIONS_PASS");\n  end\n  initial begin');
      await openFile(relative); await page.locator('#editor').fill(after); await page.locator('#save').click();
      await until(async () => await fs.readFile(path.join(project, relative), 'utf8') === after, 'UI save of compile-options fixture extension');
      changes.push({path: relative, before_sha256: sha(before), after_sha256: sha(after)});
    }
    return {reason: kind, changes, prepared_fixture_header: {path: 'src/include µ/browser_defs.vh', sha256: sha(await fs.readFile(path.join(project, 'src/include µ/browser_defs.vh')))}};
  }
  let relative, before, after;
  if (kind === 'verilog-assertion') { relative = 'src/m0_gate.v'; before = await fs.readFile(path.join(project, relative), 'utf8'); after = before.replace('a ^ b', 'a & b'); }
  if (kind === 'vhdl-assertion') { relative = 'src/m0_vhdl.vhd'; before = await fs.readFile(path.join(project, relative), 'utf8'); after = before.replace('a xor b', 'a and b'); }
  if (kind === 'verilog-syntax') { relative = 'src/m0_gate.v'; before = await fs.readFile(path.join(project, relative), 'utf8'); after = before + '\nthis is deliberately invalid Verilog;\n'; }
  if (kind === 'cocotb-guard') {
    relative = 'test/test_m0_cocotb.py'; before = await fs.readFile(path.join(project, relative), 'utf8');
    after = before + '\n\n@cocotb.test()\nasync def unselected_browser_guard(dut):\n    assert False, "BROWSER_UNSELECTED_COCOTB_GUARD"\n';
  }
  if (kind === 'pending-write-shutdown') {
    relative = 'test/test_m0_cocotb.py'; before = await fs.readFile(path.join(project, relative), 'utf8');
    after = before.trimEnd() + '\n    await Timer(1, unit="ns")\n    dut.a.value = 1\n';
  }
  assert.ok(relative && after !== before); await openFile(relative);
  await page.locator('#editor').fill(after); await page.locator('#save').click();
  await until(async () => await fs.readFile(path.join(project, relative), 'utf8') === after, 'UI save of explicit copied-fixture mutation');
  return {path: relative, before_sha256: sha(before), after_sha256: sha(after), reason: kind};
}
function inspect(name, root, job, logPath) {
  return pythonJSON(`import hashlib,json,sys,xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scripts.probe_toolchain import inspect_result
name,root,code,log_path=sys.argv[2:]
root=Path(root); build=root/'build'; log=Path(log_path).read_text(encoding='utf-8')
passed,note=inspect_result(name,root,int(code),log)
artifacts=[]; xml=[]; statuses=[]
for item in sorted(build.rglob('*')):
 if not item.is_file(): continue
 data=item.read_bytes(); relative=item.relative_to(root).as_posix()
 if item.suffix in {'.vcd','.xml','.json','.svg','.fs','.bin'} or item.name=='status' or item.name.endswith('_gate.v'):
  artifacts.append({'path':relative,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
 if item.name=='status': statuses.append({'path':relative,'value':data.decode('utf-8',errors='replace').strip()})
 if item.suffix=='.xml':
  try:
   tree=ET.fromstring(data); nodes=list(tree.iter()); tag=lambda node:node.tag.rsplit('}',1)[-1]
   details=[]
   for case in nodes:
    if tag(case)!='testcase': continue
    children=list(case.iter()); details.append({'name':case.attrib.get('name'),'failures':sum(tag(node)=='failure' for node in children),'errors':sum(tag(node)=='error' for node in children),'skipped':sum(tag(node)=='skipped' for node in children)})
   xml.append({'path':relative,'cases':[node.attrib.get('name') for node in nodes if tag(node)=='testcase'],'case_details':details,'failures':sum(tag(node)=='failure' for node in nodes),'errors':sum(tag(node)=='error' for node in nodes),'skipped':sum(tag(node)=='skipped' for node in nodes)})
  except ET.ParseError: pass
print(json.dumps({'passed':passed,'note':note,'artifacts':artifacts,'xml':xml,'statuses':statuses}))`, [name, root, String(job.returncode), logPath]);
}
async function downloadArtifact(artifact) {
  await page.locator('[data-result="artifacts"]').click();
  const row = page.locator('#artifact-list .artifact-row').filter({has: page.getByText(artifact.path, {exact: true})});
  await until(async () => await row.count() === 1, 'real artifact inventory row ' + artifact.path);
  const [download] = await Promise.all([page.waitForEvent('download'), row.getByRole('button', {name: 'Download', exact: true}).click()]);
  assert.equal(download.suggestedFilename(), path.basename(artifact.path));
  const bytes = await fs.readFile(await download.path()); assert.equal(sha(bytes), artifact.sha256); assert.equal(bytes.length, artifact.bytes);
  return row;
}
async function acceptArtifacts(name, evidence) {
  let selected;
  if (/fpga_/.test(name)) selected = evidence.artifacts.find(item => item.path.endsWith(name.includes('tangnano9k') ? '.fs' : '.bin'));
  else if (name.includes('formal')) selected = name === 'formal_counterexample' ? evidence.artifacts.find(item => item.path.endsWith('.vcd')) : evidence.artifacts.find(item => item.path.endsWith('/status'));
  else if (name === 'schematics') selected = evidence.artifacts.find(item => item.path.endsWith('.svg'));
  else if (['synthesis', 'vhdl_synthesis'].includes(name)) selected = evidence.artifacts.find(item => item.path.startsWith('build/synth/') && item.path.endsWith('.json') && !item.path.includes('render'));
  else if (name.includes('cocotb')) selected = evidence.artifacts.find(item => item.path.endsWith('.xml'));
  else if (['waveforms', 'vhdl_simulation', 'wave_saved_roundtrip'].includes(name)) selected = evidence.artifacts.find(item => item.path.endsWith('.vcd'));
  else if (name === 'gate_level_verilog') selected = evidence.artifacts.find(item => item.path.endsWith('_gate.v'));
  if (!selected) return {download: 'not_applicable-no-primary-output-required'};
  assert.ok(selected.bytes > 0); const row = await downloadArtifact(selected);
  const accepted = {download: 'passed-exact-native-disk-bytes-and-sha256', artifact: selected};
  if (selected.path.endsWith('.vcd')) {
    const text = await fs.readFile(path.join(project, selected.path), 'utf8'); assert.match(text, /\$var\b/); assert.match(text, /#\d+/);
    await row.getByRole('button', {name: 'View', exact: true}).click();
    await page.locator('#waveform-view svg').waitFor({state: 'visible'});
    assert.ok(await page.locator('#waveform-view .waveform-signal').count() > 0 || /signals|\./i.test(await page.locator('#waveform-view').textContent()));
    accepted.browser_view = 'passed-real-native-VCD-parsed-and-rendered-by-browser-worker';
  }
  if (selected.path.endsWith('.svg')) {
    await row.getByRole('button', {name: 'View', exact: true}).click();
    await until(() => page.locator('#artifact-preview img').evaluateAll(images => images.some(image => image.complete && image.naturalWidth > 0)), 'actual generated SVG image decode');
    accepted.browser_view = 'passed-real-generated-SVG-decoded-in-image-context';
    await page.locator('#schematic-zoom').selectOption('200');
    const viewport = page.getByRole('region', {name: 'Schematic viewport', exact: true});
    const geometry = await viewport.evaluate(element => ({viewport_width: element.clientWidth, image_width: element.querySelector('img').getBoundingClientRect().width, scroll_width: element.scrollWidth}));
    assert.ok(geometry.image_width > geometry.viewport_width * 1.8 && geometry.scroll_width > geometry.viewport_width);
    await page.getByRole('button', {name: 'Pan right', exact: true}).click(); assert.ok(await viewport.evaluate(element => element.scrollLeft) > 0);
    await page.getByRole('button', {name: 'Fit schematic', exact: true}).click(); assert.equal(await page.locator('#schematic-zoom').inputValue(), '100');
    assert.deepEqual(await viewport.evaluate(element => ({left: element.scrollLeft, top: element.scrollTop})), {left: 0, top: 0});
    accepted.browser_pan_zoom = {state: 'passed-200-percent-geometry-real-pan-and-fit-reset', ...geometry};
  }
  return accepted;
}
async function screenshot() { if (args['--screenshot'] && page) await page.screenshot({path: args['--screenshot'], fullPage: true, mask: [page.locator('#connect-dialog')]}); }

try {
  report.fixture_sha256 = {};
  for (const file of await walk(fixtures)) report.fixture_sha256[path.relative(fixtures, file).split(path.sep).join('/')] = sha(await fs.readFile(file));
  report.oracle_sha256 = sha(await fs.readFile(path.join(repo, 'scripts/probe_toolchain.py')));
  report.source_sha256 = {};
  for (const relative of ['vwb.py', 'companion/server.py', 'companion/projects.py', 'companion/process.py', 'companion/cocotb_runtime.py', 'scripts/probe_hdl_workspace.mjs']) report.source_sha256[relative] = sha(await fs.readFile(path.join(repo, relative)));
  if (publicUrl) { origin = publicUrl.origin; workspaceUrl = publicUrl.href; } else await serveBuild();
  browser = await chromium.launch({headless: true, executablePath: args['--executable'], args: ['--no-sandbox']});
  report.browser = await browser.version(); report.browser_security_flags = ['--no-sandbox'];
  for (const name of selected) {
    const started = Date.now(), definition = definitions[name], spec = specFor(definition);
    const evidence = report.workflows[name] = {state: 'failed', fixture: definition.base || name, selected_controls: spec};
    const workflowAssets = new Map(), responseTasks = new Set(), staticRequests = new Set();
    let context, native, workflowPage, observeStaticResponse, observeJobResponse, observeStaticRequest, forgetStaticRequest;
    const trackResponse = promise => {
      responseTasks.add(promise);
      promise.finally(() => responseTasks.delete(promise));
    };
    const isStaticRequest = request => {
      const url = new URL(request.url());
      return url.origin === origin && url.pathname.startsWith(prefix);
    };
    const drainResponses = async () => {
      // Responses can start more module/worker requests while their bodies are
      // read. A Promise.all(array) snapshot misses those later readers and can
      // close their browser context underneath response.body(). Track both the
      // live requests and readers, then detach observers before closing.
      await until(() => responseTasks.size === 0 && staticRequests.size === 0,
        'all current workflow static requests and response bodies before context teardown');
    };
    try {
      project = path.join(temporary, name + ' project µ'); await fs.cp(fixtures, project, {recursive: true});
      await fs.writeFile(path.join(project, '.vwb.json'), JSON.stringify({version: 1, src_dir: 'src', test_dir: 'test', build_dir: 'build'}) + '\n');
      if (definition.modify === 'compile-options') {
        await fs.mkdir(path.join(project, 'src/include µ'));
        await fs.writeFile(path.join(project, 'src/include µ/browser_defs.vh'), "`define VWB_HEADER_MASK 1'b0\n");
      }
      if (definition.starter) {
        const text = (await fs.readFile(path.join(project, 'src/m0_gate.v'), 'utf8')).replace('module m0_gate', 'module browser_starter');
        await fs.writeFile(path.join(project, 'src/browser_starter.v'), text);
        evidence.fixture_extensions = [{path: 'src/browser_starter.v', sha256: sha(text), purpose: 'Copied M0 XOR under a new name without a test, to exercise explicit starter smoke generation.'}];
      }
      if (definition.hierarchy) {
        const extensions = {
          'src/browser_top.sv': 'module browser_top(input wire a, input wire b, output wire y);\n  m0_gate child(.a(a), .b(b), .y(y));\nendmodule\n',
          'src/browser_pkg.sv': 'package browser_pkg; localparam int WIDTH = 1; endpackage\n',
          'src/browser_if.sv': 'interface browser_if; logic valid; endinterface\n',
        };
        evidence.fixture_extensions = [];
        for (const [relative, text] of Object.entries(extensions)) { await fs.writeFile(path.join(project, relative), text); evidence.fixture_extensions.push({path: relative, sha256: sha(text), purpose: 'Explicit discovery-only wrapper/package/interface extension to the unchanged M0 designs.'}); }
      }
      assert.equal((await fs.readdir(project)).includes('build'), false, 'Every browser native workflow starts without generated output.');
      native = await companion(); context = await browser.newContext({acceptDownloads: true, viewport: {width: 1512, height: 1050}}); contexts.push(context);
      if (publicUrl) { await context.grantPermissions(['local-network-access'], {origin}); report.permission_grant = 'explicit-simulated-Chromium-context-grantPermissions-local-network-access'; }
      else report.permission_grant = 'not-required-loopback-HTTP-preview-origin-no-browser-security-bypass';
      page = workflowPage = await context.newPage(); page.setDefaultTimeout(30000);
      page.on('pageerror', error => scriptErrors.push({workflow: name, message: safe(error.message)})); page.on('dialog', dialog => dialog.accept());
      observeJobResponse = response => {
        if (!response.url().startsWith(native.ready.url + '/v1/jobs/')) return;
        if (response.request().method() !== 'GET') return;
        trackResponse(response.json().then(body => workflowPage.evaluate(({url, body}) => { (window.__vwbHDLResponses ||= []).push({url, body}); }, {url: response.url(), body})).catch(() => {}));
      };
      observeStaticRequest = request => { if (isStaticRequest(request)) staticRequests.add(request); };
      forgetStaticRequest = request => staticRequests.delete(request);
      observeStaticResponse = response => {
        const url = new URL(response.url()); if (url.origin !== origin || !url.pathname.startsWith(prefix)) return;
        const relative = decodeURIComponent(url.pathname.slice(prefix.length)), asset = relative.endsWith('/') ? relative + 'index.html' : relative;
        if (asset === 'vwb-web-build.json' || asset.endsWith('favicon.ico')) return;
        trackResponse((async () => { if (response.status() !== 200) assetErrors.push({workflow: name, path: asset, status: response.status()}); else { const digest = sha(await response.body()); loadedAssets.set(asset, digest); workflowAssets.set(asset, digest); } })().catch(error => assetErrors.push({workflow: name, path: asset, error: safe(error.message)})));
      };
      page.on('response', observeJobResponse);
      context.on('request', observeStaticRequest);
      context.on('requestfinished', forgetStaticRequest);
      context.on('requestfailed', forgetStaticRequest);
      context.on('response', observeStaticResponse);
      const navigation = await page.goto(workspaceUrl, {waitUntil: 'networkidle', timeout: 45000}); assert.equal(navigation.status(), 200); await pair(native.ready);
      await page.locator('[data-result="tools"]').click();
      const [capabilityResponse] = await Promise.all([page.waitForResponse(response => response.url() === endpoint + '/v1/capabilities'), page.locator('#check-tools').click()]);
      const capabilities = await capabilityResponse.json(); assert.equal(capabilityResponse.status(), 200);
      const required = [...definition.required_tools, ...(definition.gate_level ? ['iverilog', 'vvp', 'yosys'] : [])];
      const missing = [...new Set(required)].filter(tool => capabilities.toolchains.tools[tool]?.state !== 'available');
      if (missing.length) { evidence.state = 'blocked'; evidence.missing_tools = missing; continue; }
      evidence.fixture_change = await modifyCopy(definition.modify);
      const expected = definition.expected || (name === 'formal_counterexample' ? 'failed' : 'passed');
      const {job, payload} = await runUI(spec, expected);
      evidence.job = {command: job.command, status: job.status, returncode: job.returncode, stale: job.stale, source_revision: job.source_revision, source_revision_after: job.source_revision_after};
      evidence.browser_request = payload;
      const log = [job.stdout, job.stderr].filter(Boolean).join('\n'), logPath = path.join(temporary, name + '.log'); await fs.writeFile(logPath, log);
      evidence.log_sha256 = sha(log); evidence.log_characters = log.length;
      const inspected = inspect(definition.base || name, project, job, logPath);
      inspected.original_M0_oracle_applicable = !definition.base;
      evidence.native_inspection = inspected;
      if (!definition.base) assert.equal(inspected.passed, true, inspected.note + '\n' + safe(log, 10000));
      if (name === 'native_verilog_assertion_failure' || name === 'native_vhdl_assertion_failure') assert.match(log, /xor mismatch/);
      if (spec.command === 'test' && spec.test_language === 'verilog' && expected === 'passed') assert.ok(!log.includes('M0_VHDL_PASS') && (spec.module === 'm0_sv' ? !log.includes('M0_VERILOG_PASS') : !log.includes('M0_SYSTEMVERILOG_PASS')), 'Explicit test selection must not accidentally run other fixture tests.');
      if (name.includes('cocotb') && expected === 'passed') {
        assert.ok(inspected.xml.length > 0 && inspected.xml.every(item => item.cases.length > 0 && item.failures === 0 && item.errors === 0 && (name === 'cocotb_function_filter' || item.skipped === 0)));
        if (name === 'cocotb_function_filter') {
          for (const xml of inspected.xml) {
            const executed = xml.case_details.filter(item => item.skipped === 0);
            assert.equal(executed.length, 1); assert.equal(executed[0].name, 'xor_truth_table');
            assert.equal(executed[0].failures, 0); assert.equal(executed[0].errors, 0);
            assert.ok(xml.case_details.filter(item => item.name === 'unselected_browser_guard').every(item => item.skipped > 0), 'A real failing sibling Cocotb function must be absent or explicitly unselected.');
          }
          evidence.cocotb_filter = 'passed-only-selected-function-executed-failing-sibling-absent-or-explicitly-skipped';
        }
      }
      if (name === 'cocotb_expected_assertion_failure') { assert.match(log, /BROWSER_UNSELECTED_COCOTB_GUARD/); assert.ok(inspected.xml.some(item => item.cases.includes('unselected_browser_guard') && item.failures > 0)); }
      if (definition.gate_level) { assert.match(log, /RTL PASS/); assert.match(log, /GATE PASS/); assert.ok(!log.includes('GATE SKIPPED')); assert.ok(inspected.artifacts.some(item => item.path.endsWith('_gate.v') && item.bytes > 0)); }
      if (name === 'compile_defines_includes_plusargs') { assert.match(log, /BROWSER_COMPILE_OPTIONS_PASS/); assert.match(log, /M0_VERILOG_PASS/); }
      if (name === 'cocotb_pending_write_shutdown') {
        const staged = (await walk(path.join(project, 'build'))).filter(file => path.basename(file) === '_vwb_cocotb_runtime.py');
        if (log.includes('Initialized cocotb v2.1.0.dev0+41564633')) assert.equal(staged.length, 1, 'The affected locked runtime must load the trusted shutdown compatibility module.');
        evidence.shutdown_regression = {state: 'passed-real-XOR-assertions-and-final-pending-write-with-zero-native-exit', staged_modules: []};
        for (const file of staged) {
          const bytes = await fs.readFile(file); assert.equal(sha(bytes), report.source_sha256['companion/cocotb_runtime.py']);
          evidence.shutdown_regression.staged_modules.push({path: path.relative(project, file).split(path.sep).join('/'), bytes: bytes.length, sha256: sha(bytes)});
        }
      }
      if (definition.starter) {
        assert.match(log, /generated starter test:/i); assert.match(log, /RTL PASS/);
        const relative = 'test/test_browser_starter_starter.sv'; const generated = await fs.readFile(path.join(project, relative), 'utf8');
        assert.match(generated, /browser_starter\s+dut/);
        await page.locator('[data-explorer="files"]').click();
        assert.equal(await page.locator('#explorer button[data-path]').filter({hasText: relative}).count(), 1, 'The native-created starter must appear after the browser inventory refresh.');
        assert.match(await page.locator('#hdl-summary').textContent(), /smoke tests/i);
        const repeat = await runUI({...spec, expected_stale: false}, 'passed');
        assert.equal(repeat.job.source_revision, repeat.job.source_revision_after);
        assert.ok(!repeat.job.stdout.includes('generated starter test:'));
        evidence.starter = {state: 'passed-created-and-listed-then-fresh-rerun', first_result_stale: true, second_result_stale: false, functional_assertions: 'not_supplied-generated-smoke-only', generated: {path: relative, bytes: Buffer.byteLength(generated), sha256: sha(generated)}};
      }
      if (definition.hierarchy) {
        const catalog = JSON.parse(job.stdout), top = catalog.modules.find(item => item.name === 'browser_top');
        assert.ok(top?.dependencies.includes('m0_gate')); assert.ok(catalog.packages.some(item => item.name === 'browser_pkg')); assert.ok(catalog.interfaces.some(item => item.name === 'browser_if'));
        await page.locator('[data-explorer="designs"]').click();
        await page.locator('#explorer .design-row').filter({hasText: /^src\/browser_top\.sv$/}).click();
        await until(async () => await page.locator('#active-file').textContent() === 'src/browser_top.sv', 'hierarchy wrapper source navigation');
        const tree = page.locator('#explorer .design-hierarchy'); await tree.waitFor({state: 'visible'});
        assert.ok(await tree.getByRole('button', {name: 'browser_top', exact: true}).isVisible());
        await tree.getByRole('button', {name: 'm0_gate', exact: true}).click();
        await until(async () => await page.locator('#active-file').textContent() === 'src/m0_gate.v', 'hierarchy child source navigation');
        for (const relative of ['src/browser_pkg.sv', 'src/browser_if.sv']) { await page.locator('#explorer .design-row').filter({hasText: new RegExp('^' + relative.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$')}).click(); await until(async () => await page.locator('#active-file').textContent() === relative, 'package/interface source navigation'); }
        await page.locator('#command').selectOption('test'); await page.locator('#testcase').fill('unselected_browser_guard'); await page.locator('#seed').fill('99');
        await page.locator('[data-explorer="tests"]').click();
        await page.locator('#explorer .test-row').filter({hasText: 'm0_gate · verilog'}).filter({hasText: 'test/test_m0_gate.v'}).click();
        for (const [id, value] of [['module', 'm0_gate'], ['test-file', 'test/test_m0_gate.v'], ['test-top', 'tb_m0_gate'], ['test-language', 'verilog'], ['testcase', ''], ['seed', '']]) assert.equal(await page.locator('#' + id).inputValue(), value);
        const selectedTest = await runUI({command: 'test', module: 'm0_gate', test: 'test/test_m0_gate.v', test_top: 'tb_m0_gate', test_language: 'verilog'}, 'passed', false);
        assert.match(selectedTest.job.stdout, /M0_VERILOG_PASS/); assert.equal(selectedTest.payload.options.testcase, undefined); assert.equal(selectedTest.payload.options.seed, undefined);
        evidence.hierarchy = {state: 'passed-real-native-catalog-wrapper-child-package-interface-navigation-and-Test-row-selection', selected_test_request: selectedTest.payload};
      }
      if (name === 'lint_source_diagnostic') {
        await openFile('src/m0_sv.sv');
        await page.locator('[data-result="diagnostics"]').click(); const link = page.locator('#diagnostics button').filter({hasText: /src\/m0_gate\.v:\d+/}).first();
        await link.waitFor({state: 'visible'}); const line = Number((await link.textContent()).split(':').at(-1));
        await link.click(); await until(async () => await page.locator('#active-file').textContent() === 'src/m0_gate.v', 'native diagnostic source navigation');
        assert.equal(await page.locator('#editor').evaluate(editor => editor.value.slice(0, editor.selectionStart).split('\n').length), line);
        evidence.source_diagnostics = 'passed-real-native-compiler-error-links-to-edited-source';
      }
      evidence.artifact_acceptance = await acceptArtifacts(name, inspected);
      if (name === 'wave_saved_roundtrip') {
        const tag = definition.wave_tag;
        const savedVcd = inspected.artifacts.find(item => item.path.startsWith('build/saved-waves/' + tag + '/') && item.path.endsWith('.vcd'));
        const simulatedVcd = inspected.artifacts.find(item => item.path.startsWith('build/sim/') && item.path.endsWith('.vcd'));
        assert.ok(savedVcd && simulatedVcd); assert.equal(savedVcd.sha256, simulatedVcd.sha256, 'The archived waveform must match the actual native simulation.');
        const listed = await runUI({command: 'wave', module: spec.module, wave_mode: 'list'}, 'passed');
        const entries = JSON.parse(listed.job.stdout); assert.ok(Array.isArray(entries));
        const entry = entries.find(item => item.tag === tag); assert.ok(entry); assert.equal(entry.dut, 'm0_gate');
        assert.equal(await page.locator('#saved-wave-list option').evaluateAll((options, tag) => options.some(option => option.value === tag), tag), true);
        const loaded = await runUI({command: 'wave', module: spec.module, wave_mode: 'load', wave_tag: tag}, 'passed');
        const saved = JSON.parse(loaded.job.stdout); assert.equal(saved.tag, tag);
        assert.equal(saved.directory + '/' + saved.waveform, savedVcd.path);
        await page.locator('#waveform-view svg').waitFor({state: 'visible'});
        const row = await downloadArtifact(savedVcd);
        assert.ok(await row.getByRole('button', {name: 'View', exact: true}).isVisible());
        evidence.saved_wave_roundtrip = {state: 'passed-real-tag-list-load-and-browser-waveform', archived_vcd: savedVcd, list_request: listed.payload, load_request: loaded.payload};
      }
      if (name === 'schematics' || name === 'formal_counterexample') {
        if (name === 'formal_counterexample') {
          await openFile('formal/bad.sv');
          await page.locator('#waveform-view svg').scrollIntoViewIfNeeded();
          report.screenshot_stage = 'actual-failing-formal-property-source-with-real-counterexample-VCD';
        } else report.screenshot_stage = 'actual-native-generated-SVG-with-pan-and-zoom-controls';
        await screenshot(); screenshotSaved = !!args['--screenshot'];
      }
      const storage = await page.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}}));
      for (const secret of secrets) { assert.ok(!storage.includes(secret)); assert.ok(!page.url().includes(secret)); }
      assert.ok(!storage.includes('Bearer ')); evidence.state = 'passed'; checked(name + '-real-browser-native-HDL-workflow');
    } catch (error) {
      evidence.error = safe(error.stack || error.message); evidence.log_excerpt = page ? await page.locator('#job-output').textContent().then(value => safe(value, 12000)).catch(() => null) : null;
      evidence.visible_status = page ? await page.locator('#global-status').textContent().then(value => safe(value, 4000)).catch(() => null) : null;
      process.stderr.write('[hdl-probe] FAILED ' + name + ': ' + safe(error.message, 2000) + '\n');
      await screenshot().catch(() => {});
    } finally {
      evidence.duration_seconds = Math.round((Date.now() - started) / 10) / 100;
      if (page && context) {
        const manifest = await page.evaluate(async url => { const response = await fetch(url, {cache: 'no-store'}); if (!response.ok) throw new Error('Manifest HTTP ' + response.status); return response.json(); }, origin + prefix + 'vwb-web-build.json').catch(() => null);
        await drainResponses();
        assert.ok(manifest, 'Every HDL workflow must read its runtime manifest before context teardown.');
        if (manifest) {
          const fingerprint = sha(JSON.stringify(manifest));
          if (manifestFingerprint !== undefined) assert.equal(fingerprint, manifestFingerprint, 'The deployed build changed between HDL workflow contexts; rerun one coherent deployment.');
          manifestFingerprint = fingerprint; evidence.runtime_manifest_sha256 = fingerprint;
          for (const asset of runtimeAssets) { assert.equal(typeof manifest.files?.[asset], 'string'); assert.equal(workflowAssets.get(asset), manifest.files[asset], 'Each HDL workflow must load the declared runtime asset: ' + asset); }
          report.deployed_manifest = manifest;
        }
      }
      if (context) {
        await drainResponses();
        evidence.response_collection = {state: 'drained-before-context-close', pending_response_readers: responseTasks.size, pending_static_requests: staticRequests.size};
        if (observeStaticResponse) context.off('response', observeStaticResponse);
        if (observeStaticRequest) context.off('request', observeStaticRequest);
        if (forgetStaticRequest) { context.off('requestfinished', forgetStaticRequest); context.off('requestfailed', forgetStaticRequest); }
        if (observeJobResponse) workflowPage.off('response', observeJobResponse);
      }
      await context?.close().catch(() => {}); if (native) await stop(native.child);
    }
  }
  assert.equal(report.deployed_manifest?.schema, 'vwb.web-build/1'); assert.equal(report.deployed_manifest?.base_path, prefix);
  for (const asset of runtimeAssets) assert.equal(loadedAssets.get(asset), report.deployed_manifest.files[asset], 'Actually loaded HDL asset must match the build manifest: ' + asset);
  report.deployed_manifest = {schema: report.deployed_manifest.schema, status: report.deployed_manifest.status, base_path: prefix, file_count: Object.keys(report.deployed_manifest.files).length, loaded_runtime_sha256: Object.fromEntries(runtimeAssets.map(asset => [asset, loadedAssets.get(asset)]))};
  report.original_M0_workflows_passed = Object.keys(definitions).filter(name => !definitions[name].base).every(name => report.workflows[name]?.state === 'passed');
  report.all_requested_workflows_passed = selected.every(name => report.workflows[name]?.state === 'passed');
  assert.deepEqual(scriptErrors, []); assert.deepEqual(assetErrors, []);
  assert.equal(report.all_requested_workflows_passed, true, 'Every selected real native/browser workflow must pass; blocked tools are not successes.');
  report.state = 'passed';
} catch (error) { report.error = safe(error.stack || error.message); }
finally {
  report.script_errors = scriptErrors; report.asset_errors = assetErrors; report.screenshot_recorded = screenshotSaved;
  for (const context of contexts) await context.close().catch(() => {}); await browser?.close().catch(() => {});
  for (const child of children) await stop(child);
  if (staticServer) await new Promise(resolve => staticServer.close(resolve)); await fs.rm(temporary, {recursive: true, force: true});
}
const json = safe(JSON.stringify(report, null, 2), 1500000) + '\n';
if (args['--output']) await fs.writeFile(args['--output'], json); else process.stdout.write(json);
if (report.state !== 'passed') process.exitCode = 1;
