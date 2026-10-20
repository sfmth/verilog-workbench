import {CompanionClient} from '../m0/client.mjs';
import {WorkspaceModel, MAX_DRAFT_IMPORT_BYTES} from './model.mjs';
import {mountWaveform} from './waveform.mjs';
import {mountIntelligence, offsetAt, positionAt} from './intelligence.mjs';
import {mountFileManager} from './files.mjs';
import {mountSyntax} from './syntax-view.mjs';
import {mountSearch} from './search.mjs';
import {buildHdlJob, selectedTest, runSummary, designHierarchy} from './hdl.mjs';
import {mountSchematic} from './schematic.mjs';

const $ = id => document.getElementById(id);
const all = selector => [...document.querySelectorAll(selector)];
const terminal = new Set(['passed', 'failed', 'cancelled', 'timed_out']);
let client, model, activePath, discovery, activeJob, lastJob, online = false;
let explorerView = 'files', resultView = 'logs', preparing = false, epoch = 0, pollGeneration = 0;
let disposeWaveform, disposeSchematic, previewUrl, openGeneration = 0;
let pendingPath = new URLSearchParams(location.hash.slice(1)).get('file');
let editVersion = 0, lastJobEditVersion = 0;
let lastJobPayload;
let artifactGeneration = 0, lastJobPreviewGeneration;
let pairing = false;
const dirty = () => [...(model?.buffers.values() || [])].some(buffer => buffer.dirty);
let intelligence;
let fileManager;
let syntaxView, fileSearch;

function node(tag, text, className) {
  const value = document.createElement(tag);
  if (text !== undefined) value.textContent = text;
  if (className) value.className = className;
  return value;
}
function button(text, action, className) {
  const value = node('button', text, className);
  value.type = 'button';
  value.addEventListener('click', () => perform(action));
  return value;
}
function say(text, target = 'global-status', error = false) {
  $(target).textContent = text;
  $(target).classList.toggle('error', error);
}
function describe(error) {
  if (error.name === 'AbortError') return 'Connection timed out. Check the companion and local network permission, then retry.';
  if (error instanceof AggregateError) return error.message + ' ' + error.errors.map(describe).join(' ');
  if (error instanceof TypeError) return 'Cannot reach the companion. Check the address, process and browser local network permission. Your edits are retained.';
  return error.message;
}
async function perform(action, target = 'global-status', sessionBound = true) {
  const actionEpoch = epoch;
  try { await action(); }
  catch (error) {
    if (sessionBound && actionEpoch !== epoch) return;
    if (error.status === 401 || error.name === 'AbortError' || error instanceof TypeError) {
      online = false;
      say('Disconnected. Retry the connection to retain this session, or pair a restarted companion.', 'connection-status', true);
    }
    say(describe(error), target, true);
  } finally { renderControls(); renderFileStatus(); renderRecovery(); }
}
function renderControls() {
  const current = model?.buffers.get(activePath);
  for (const id of ['refresh', 'refresh-artifacts', 'check-tools']) $(id).disabled = !online || preparing;
  $('run').disabled = !online || preparing || !!activeJob;
  $('program').disabled = !online || preparing || !!activeJob;
  $('cancel').disabled = !online || !activeJob;
  $('save').disabled = !online || !current?.dirty || !!current?.saving || preparing;
  $('save-all').disabled = !online || !dirty() || preparing;
  $('reload').disabled = !online || !current || !!current.saving || preparing;
  $('editor').disabled = !current || preparing;
  $('find-open').disabled = !current || preparing;
  $('export-drafts').disabled = !model;
  $('import-drafts').disabled = !model || preparing;
  $('persist-drafts').disabled = !model?.storageKey;
  $('forget').disabled = preparing;
  $('connect-toggle').disabled = preparing;
  for (const id of ['new-file', 'new-folder', 'manage-files', 'open-trash']) $(id).disabled = !online || preparing || !!activeJob;
  $('connection-indicator').textContent = online ? 'Companion connected' : 'Disconnected';
  $('connection-indicator').classList.toggle('connected', online);
  $('connect-toggle').textContent = online ? 'Connection' : 'Connect companion';
  $('retry').hidden = !client?.token;
  $('forget').hidden = !client?.token;
  $('resume-job').hidden = !activeJob;
  intelligence?.refresh();
  fileManager?.refresh();
  syntaxView?.refresh(); fileSearch?.refresh();
}
function renderFileStatus() {
  const file = model?.buffers.get(activePath);
  if (file) {
    const message = file.conflict ? 'Conflict: disk changed. Export your draft, then reload to review the disk file.' :
      file.error ? describe(file.error) : file.saving ? 'Saving to local file…' : file.dirty ? 'Unsaved changes' : 'Saved to local file · revision checked';
    say(message, 'file-status', !!file.conflict || !!file.error);
    const position = positionAt($('editor').value, $('editor').selectionStart);
    $('position').textContent = `Ln ${position.line + 1}, Col ${position.character + 1} · ${file.newline === '\r\n' ? 'CRLF' : file.newline === '\r' ? 'CR' : 'LF'} · UTF-8`;
  }
  if (model?.storageError) say(model.storageError + ' Export drafts to keep a copy.', 'global-status', true);
}
function renderTabs() {
  $('file-tabs').replaceChildren();
  for (const file of model?.buffers.values() || []) {
    const tab = node('div', undefined, 'file-tab');
    tab.classList.toggle('active', file.path === activePath);
    const choose = button((file.dirty ? '● ' : '') + file.path.split('/').at(-1), () => openFile(file.path));
    choose.title = file.path;
    choose.disabled = preparing;
    choose.setAttribute('aria-pressed', String(file.path === activePath));
    const close = button('×', () => closeFile(file.path), 'close-tab');
    close.setAttribute('aria-label', 'Close ' + file.path);
    close.disabled = preparing;
    tab.append(choose, close);
    $('file-tabs').append(tab);
  }
}
function renderEditor() {
  const file = model?.buffers.get(activePath);
  $('editor-empty').hidden = !!file;
  $('editor').hidden = !file;
  $('source-editor').hidden = !file;
  $('editor').value = file?.content || '';
  $('active-file').textContent = file?.path || 'No file open';
  syntaxView?.refresh(); fileSearch?.refresh();
  renderTabs(); renderControls(); renderFileStatus(); renderExplorer();
}
async function openFile(path, line) {
  if (!model || preparing) return;
  const generation = ++openGeneration, workspace = model;
  await workspace.open(path);
  if (generation !== openGeneration || workspace !== model) return;
  activePath = path;
  pendingPath = null;
  renderEditor();
  if (line) {
    const editor = $('editor');
    const offset = editor.value.split('\n').slice(0, line - 1).join('\n').length + (line > 1 ? 1 : 0);
    editor.focus(); editor.setSelectionRange(offset, offset);
    editor.scrollTop = Math.max(0, line - 3) * 23;
  }
  updateHash();
}
function closeFile(path) {
  if (preparing) return;
  const file = model.buffers.get(path);
  if (file?.dirty && !confirm('Discard unsaved edits to ' + path + '? Export drafts first to keep a copy.')) return;
  model.close(path);
  if (activePath === path) activePath = model.buffers.keys().next().value;
  renderEditor(); updateHash();
}
function renderExplorer() {
  if (!model) return;
  const container = $('explorer');
  container.replaceChildren();
  const needle = $('filter').value.toLowerCase();
  const files = model.files.filter(file => file.path.toLowerCase().includes(needle));
  $('file-actions').hidden = explorerView !== 'files';
  if (explorerView === 'files') {
    const folders = new Set((model.directories || []).map(entry => entry.path));
    for (const file of model.files) {
      const parts = file.path.split('/');
      for (let index = 1; index < parts.length; index++) folders.add(parts.slice(0, index).join('/'));
    }
    for (const path of [...folders].sort()) {
      if (needle && !path.toLowerCase().includes(needle) && !files.some(file => file.path.startsWith(path + '/'))) continue;
      const folder = button(path + '/', () => fileManager.open('manage', path), 'folder-label folder-button');
      folder.setAttribute('aria-label', 'Manage folder ' + path);
      folder.disabled = preparing;
      container.append(folder);
    }
    for (const file of [...files].sort((a, b) => a.path.localeCompare(b.path))) {
      const item = button(file.path, () => openFile(file.path), 'file-row');
      item.dataset.path = file.path;
      item.classList.toggle('active', file.path === activePath);
      item.disabled = preparing;
      container.append(item);
    }
  } else if (!discovery) {
    container.append(node('p', 'Run “Discover project” to populate design units and tests.', 'empty'));
  } else {
    if (explorerView === 'designs') {
      const selected = $('module').value.trim();
      if ((discovery.modules || []).some(unit => unit.name === selected)) {
        const tree = node('details', undefined, 'design-hierarchy'); tree.open = true;
        tree.append(node('summary', 'Selected top · dependency hierarchy'));
        function branch(unit) {
          const row = node('li');
          row.append(button(unit.name + (unit.cycle ? ' · cycle' : unit.unresolved ? ' · unresolved' : ''), () => {
            $('module').value = unit.name;
            if (unit.files.length) return openFile(unit.files[0]);
            renderExplorer();
          }, 'design-row'));
          for (const problem of unit.problems) row.append(node('p', problem, 'error'));
          if (unit.children.length) { const list = node('ul'); for (const child of unit.children) list.append(branch(child)); row.append(list); }
          if (unit.truncated) row.append(node('p', 'Expansion limit reached. Select a child to inspect it.', 'muted'));
          return row;
        }
        const list = node('ul'); list.append(branch(designHierarchy(discovery, selected))); tree.append(list); container.append(tree);
      }
      for (const [kind, units] of [['Package', discovery.packages], ['Interface', discovery.interfaces], ['Primitive', discovery.primitives]]) {
        for (const unit of units || []) {
          if (!`${unit.name} ${(unit.files || []).join(' ')}`.toLowerCase().includes(needle)) continue;
          container.append(node('p', `${kind} · ${unit.name} · ${unit.language}`, 'folder-label'));
          for (const path of unit.files || []) container.append(button(path, () => openFile(path), 'design-row'));
        }
      }
    }
    for (const design of discovery.modules || []) {
      if (explorerView === 'designs' && (design.name.toLowerCase().includes(needle) || design.files?.some(path => path.toLowerCase().includes(needle)))) {
        container.append(node('p', `${design.name} · ${design.language}`, 'folder-label'));
        for (const path of design.files || []) container.append(button(path, () => { $('module').value = design.name; return openFile(path); }, 'design-row'));
        if (design.dependencies?.length) container.append(node('p', 'Uses: ' + design.dependencies.join(', '), 'empty'));
        for (const problem of design.problems || []) container.append(node('p', problem, 'error'));
      }
      if (explorerView === 'tests') for (const test of design.tests || []) {
        if (!`${test.path} ${design.name}`.toLowerCase().includes(needle)) continue;
        container.append(button(`${design.name} · ${test.kind}\n${test.path}`, () => {
          const selection = selectedTest(design, test);
          for (const [key, id] of [['module', 'module'], ['test', 'test-file'], ['test_top', 'test-top'], ['test_language', 'test-language'], ['testcase', 'testcase'], ['seed', 'seed']]) $(id).value = selection[key];
          $('command').value = 'test'; commandChanged(); return openFile(test.path);
        }, 'test-row'));
      }
    }
  }
  if (!container.childNodes.length) container.append(node('p', 'No matching items.', 'empty'));
  if (model.inventory?.truncated) container.append(node('p', 'Inventory truncated at the companion’s file limit.', 'notice'));
}
function renderRecovery() {
  const files = model?.recoverable || [];
  $('recovery').hidden = files.length === 0;
  $('recovery-list').replaceChildren();
  for (const file of files) {
    const row = node('div', undefined, 'recovery-row');
    const restore = button('Restore ' + file.path, async () => {
      await model.recover(file.path); editVersion++; activePath = file.path; renderEditor(); updateHash();
      if (lastJob) renderJob(lastJob);
    });
    restore.dataset.path = file.path;
    restore.disabled = preparing;
    const discard = button('Discard', () => {
      if (confirm('Discard recovery draft for ' + file.path + '?')) model.discardRecovery(file.path);
    });
    discard.disabled = preparing;
    row.append(node('span', file.path), restore, discard);
    $('recovery-list').append(row);
  }
}
function showResult(view) {
  if (!['logs', 'intelligence', 'diagnostics', 'artifacts', 'waveform', 'tools'].includes(view)) return;
  resultView = view;
  for (const tab of all('[data-result]')) tab.setAttribute('aria-pressed', String(tab.dataset.result === view));
  for (const pane of all('.result-pane')) pane.hidden = pane.id !== 'result-' + view;
  updateHash();
}
function updateHash() {
  const params = new URLSearchParams({view: resultView});
  if (activePath || pendingPath) params.set('file', activePath || pendingPath);
  history.replaceState(null, '', '#' + params);
}
async function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  const path = params.get('file'), view = params.get('view');
  if (path && !online) pendingPath = path;
  if (path && online) {
    if (model.files.some(file => file.path === path)) await openFile(path);
    else { pendingPath = null; say('The linked file is not in this project: ' + path, 'global-status', true); }
  }
  if (view) showResult(view);
}
function commandChanged() {
  const command = $('command').value;
  const simulation = command === 'test' || (command === 'wave' && $('wave-mode').value === 'simulate');
  for (const id of ['lint', 'synth', 'formal', 'fpga', 'wave']) $(id + '-options').hidden = id !== command;
  $('test-options').hidden = !simulation;
  $('hdl-options').hidden = !simulation && !['lint', 'synth', 'fpga'].includes(command);
  $('simulation-options').hidden = !simulation;
  $('keep-going-option').hidden = !['test', 'lint'].includes(command);
  $('replace-tag').disabled = command !== 'wave' || $('wave-mode').value !== 'simulate';
  $('module').disabled = ['list', 'doctor', 'formal'].includes(command);
}
function jobPayload(program) {
  const ids = {command: 'command', module: 'module', test_language: 'test-language', test: 'test-file', test_top: 'test-top', testcase: 'testcase', seed: 'seed', wave_format: 'wave-format', max_array_words: 'max-array-words', defines: 'hdl-defines', includes: 'hdl-includes', plusargs: 'plusargs', linter: 'linter', synth_format: 'synth-format', formal_config: 'formal-config', board: 'board', stage: 'stage', constraints: 'constraints', wave_mode: 'wave-mode', wave_tag: 'wave-tag'};
  const state = Object.fromEntries(Object.entries(ids).map(([key, id]) => [key, $(id).value]));
  for (const [key, id] of [['waves', 'waves'], ['gate_level', 'gate-level'], ['full', 'synth-full'], ['flatten', 'synth-flatten'], ['replace_tag', 'replace-tag']]) state[key] = $(id).checked;
  return buildHdlJob(state, program);
}
async function startJob(program = false) {
  if (!online || activeJob || preparing) return;
  const payload = jobPayload(program);
  if (program && !confirm(`Build and program ${payload.options.module} on ${$('board').selectedOptions[0].textContent}? This runs the flash stage on the attached board with the saved sources and selected constraints.`)) return;
  const previewGeneration = payload.command === 'wave' && payload.options.load ? ++artifactGeneration : undefined;
  preparing = true; renderControls();
  try {
    say('Saving open files before running…');
    await model.saveAll();
    if (dirty()) throw new Error('A buffer changed during save. Save again before running.');
    renderTabs();
    const job = await client.request('/v1/jobs', {method: 'POST', body: payload});
    lastJobPayload = payload;
    lastJobPreviewGeneration = previewGeneration;
    lastJobEditVersion = editVersion;
    activeJob = job.id; lastJob = job;
    showResult('logs'); renderJob(job);
    say('Native job started against saved local files.');
  } finally { preparing = false; renderControls(); }
  await pollJob();
}
function renderJob(job) {
  lastJob = job;
  const stale = job.stale || dirty() || editVersion !== lastJobEditVersion;
  $('job-status').textContent = `${job.command} · ${job.status}${stale ? ' · STALE (source changed)' : ''}`;
  $('job-status').classList.toggle('error', job.status === 'failed' || job.status === 'timed_out' || stale);
  $('job-output').textContent = [job.stdout, job.stderr].filter(Boolean).join('\n') || (job.status === 'running' ? 'Running… Output becomes available when the native process finishes.' : 'No output.');
  if (job.stdout_truncated || job.stderr_truncated) $('job-output').textContent += '\n[Output truncated by companion limit]';
  renderDiagnostics(job);
  const summary = runSummary(job);
  $('hdl-summary').textContent = summary; $('hdl-summary').hidden = !summary;
}
async function pollJob() {
  if (!activeJob || !client?.token) return;
  const id = activeJob, session = client, sessionEpoch = epoch, generation = ++pollGeneration;
  const deadline = Date.now() + 330000;
  try {
    while (Date.now() < deadline && sessionEpoch === epoch && generation === pollGeneration) {
      const job = await session.request('/v1/jobs/' + encodeURIComponent(id));
      if (sessionEpoch !== epoch || generation !== pollGeneration) return;
      online = true; renderJob(job); renderControls();
      if (terminal.has(job.status)) {
        activeJob = null;
        let completionNotice, completionError = false;
        if (job.command === 'list' && job.status === 'passed' && !job.stale) {
          try { discovery = JSON.parse(job.stdout); } catch { say('Discovery output was not valid JSON. Raw output is retained.', 'global-status', true); }
          $('module-list').replaceChildren();
          for (const item of discovery?.modules || []) { const option = node('option'); option.value = item.name; $('module-list').append(option); }
          $('test-file-list').replaceChildren();
          const tests = new Set((discovery?.modules || []).flatMap(item => (item.tests || []).map(test => test.path)));
          for (const path of tests) { const option = node('option'); option.value = path; $('test-file-list').append(option); }
          renderExplorer();
        }
        if (job.command === 'doctor') { $('tools-output').textContent = job.stdout || job.stderr; }
        await refreshArtifacts();
        if (sessionEpoch !== epoch || generation !== pollGeneration) return;
        if (['test', 'wave'].includes(job.command)) {
          const before = model.files.map(file => `${file.path}:${file.size}`).join('\n');
          await refresh();
          if (sessionEpoch !== epoch || generation !== pollGeneration) return;
          if (before !== model.files.map(file => `${file.path}:${file.size}`).join('\n')) {
            discovery = null; $('test-file-list').replaceChildren(); renderExplorer();
            completionNotice = `${job.command}: ${job.status}. Project files changed; run Discover project to refresh designs and tests.`;
          }
        }
        if (sessionEpoch !== epoch || generation !== pollGeneration) return;
        if (job.command === 'wave' && job.status === 'passed' && !job.stale) {
          const options = lastJobPayload?.command === 'wave' ? lastJobPayload.options : {};
          if (options.list_saved) {
            try {
              const saved = JSON.parse(job.stdout);
              if (!Array.isArray(saved)) throw new Error('Invalid saved-wave list.');
              $('saved-wave-list').replaceChildren();
              for (const item of saved) if (typeof item.tag === 'string') { const option = node('option'); option.value = item.tag; $('saved-wave-list').append(option); }
            } catch { completionNotice = 'Saved-wave output was not valid JSON. Raw output is retained.'; completionError = true; }
          } else if (options.load) {
            try {
              const saved = JSON.parse(job.stdout);
              const path = saved.directory + '/' + saved.waveform;
              if (/\.vcd$/i.test(path) && lastJobPreviewGeneration === artifactGeneration) {
                const blob = await artifact(path), text = await blob.text();
                if (sessionEpoch !== epoch || generation !== pollGeneration) return;
                if (lastJobPreviewGeneration === artifactGeneration) openWaveform(text, path);
              } else completionNotice = /\.vcd$/i.test(path) ? 'Saved waveform loaded. It is available in Artifacts.' : 'Saved FST is available to download from Artifacts.';
            } catch (error) {
              if (sessionEpoch !== epoch || generation !== pollGeneration) return;
              completionNotice = 'Saved wave could not be opened: ' + describe(error); completionError = true;
            }
          }
        }
        say(completionNotice || `${job.command}: ${job.status}${job.stale ? ' (source changed; rerun before using results)' : ''}`, 'global-status', completionError);
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 350));
    }
    if (sessionEpoch === epoch && generation === pollGeneration) throw new Error('Job status polling paused. Resume status or Stop; the job remains tracked.');
  } catch (error) {
    if (sessionEpoch === epoch && generation === pollGeneration) {
      $('job-status').textContent = `${lastJob?.command || 'Job'} · connection/status interrupted`;
      throw error;
    }
  } finally { renderControls(); }
}
function renderDiagnostics(job) {
  const lines = (job.stdout + '\n' + job.stderr).replace(/\x1b\[[0-9;]*m/g, '').split('\n');
  const container = $('diagnostics'); container.replaceChildren();
  let count = 0;
  for (const line of lines) {
    const match = line.match(/(?:^|\s)([^\s]+?\.(?:v|sv|svh|vh|vhd|vhdl|py)):(\d+)(?::\d+)?:?\s*(.*)/i);
    if (!match) continue;
    const location = match[1].replaceAll('\\', '/');
    const candidates = model.files.filter(file => location === file.path || location.endsWith('/' + file.path));
    const row = node('div', undefined, 'diagnostic');
    if (candidates.length === 1) row.append(button(`${candidates[0].path}:${match[2]}`, () => openFile(candidates[0].path, Number(match[2]))));
    else row.append(node('span', `${match[1]}:${match[2]}`));
    row.append(node('span', match[3] || line)); container.append(row);
    if (++count >= 200) break;
  }
  $('diagnostic-count').textContent = String(count);
  if (!count) container.append(node('p', 'No source locations recognized. Consult Output for the complete tool response.', 'muted'));
}
async function refreshArtifacts() {
  if (!online) return;
  const sessionEpoch = epoch, session = client;
  const result = await session.request('/v1/artifacts');
  if (sessionEpoch !== epoch) return;
  $('artifact-list').replaceChildren();
  for (const file of result.files) {
    const row = node('div', undefined, 'artifact-row');
    row.append(node('span', file.path), node('small', `${(file.size / 1024).toFixed(1)} KiB`));
    row.append(button('Download', async () => download(await artifact(file.path), file.path.split('/').at(-1))));
    if (/\.(vcd|svg|png)$/i.test(file.path)) {
      row.append(button('View', async () => {
        const viewEpoch = epoch, generation = ++artifactGeneration;
        const blob = await artifact(file.path);
        if (viewEpoch !== epoch || generation !== artifactGeneration) return;
        if (/\.vcd$/i.test(file.path)) {
          const text = await blob.text();
          if (viewEpoch === epoch && generation === artifactGeneration) openWaveform(text, file.path);
        }
        else {
          disposeSchematic?.(); disposeSchematic = null;
          if (previewUrl) URL.revokeObjectURL(previewUrl);
          // SVG is displayed in an image context; project scripts are never executed.
          previewUrl = URL.createObjectURL(new Blob([blob], {type: /\.svg$/i.test(file.path) ? 'image/svg+xml' : 'image/png'}));
          disposeSchematic = mountSchematic($('artifact-preview'), previewUrl, file.path); showResult('artifacts');
        }
      }));
    }
    $('artifact-list').append(row);
  }
  if (!result.files.length) $('artifact-list').append(node('p', 'No generated artifacts. Run a native workflow to produce output.', 'empty'));
  if (result.truncated) $('artifact-list').append(node('p', 'Artifact inventory truncated.', 'notice'));
}
async function artifact(path) {
  const sessionEpoch = epoch;
  const blob = await client.request('/v1/artifacts/file?path=' + encodeURIComponent(path), {responseType: 'blob'});
  if (sessionEpoch !== epoch) throw new Error('Project session changed while loading the artifact.');
  return blob;
}
function download(blob, filename) {
  const url = URL.createObjectURL(blob), anchor = node('a');
  anchor.href = url; anchor.download = filename; document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function openWaveform(text, filename) {
  artifactGeneration++;
  disposeWaveform?.();
  showResult('waveform');
  disposeWaveform = mountWaveform($('waveform-view'), text, {filename});
}
async function capabilities() {
  const sessionEpoch = epoch;
  const report = await client.request('/v1/capabilities');
  if (sessionEpoch !== epoch) return;
  $('tools-output').textContent = JSON.stringify(report, null, 2);
  showResult('tools');
}
async function refresh() {
  const workspace = model, sessionEpoch = epoch;
  await workspace.connect();
  if (sessionEpoch !== epoch || workspace !== model) return;
  renderExplorer(); renderRecovery();
}
function resetResults() {
  artifactGeneration++; lastJobPreviewGeneration = undefined;
  disposeWaveform?.(); disposeWaveform = null;
  disposeSchematic?.(); disposeSchematic = null;
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null;
  $('artifact-preview').replaceChildren(); $('artifact-list').replaceChildren(); $('diagnostics').replaceChildren();
  $('waveform-view').replaceChildren(node('p', 'Open a VCD artifact or choose a local VCD file.', 'empty'));
  $('job-status').textContent = 'No job submitted'; $('job-status').classList.remove('error');
  $('diagnostic-count').textContent = '0';
  $('job-output').textContent = 'No job submitted for this project.';
  $('hdl-summary').textContent = ''; $('hdl-summary').hidden = true; lastJobPayload = null;
  $('tools-output').textContent = 'No capability report loaded.';
  $('module-list').replaceChildren(); $('module').value = ''; $('formal-config').value = ''; $('constraints').value = '';
  for (const id of ['test-file', 'test-top', 'testcase', 'seed', 'hdl-defines', 'hdl-includes', 'plusargs', 'wave-tag', 'max-array-words']) $(id).value = '';
  for (const id of ['gate-level', 'synth-full', 'synth-flatten', 'replace-tag']) $(id).checked = false;
  $('test-language').value = 'auto'; $('wave-mode').value = 'simulate'; $('wave-format').value = 'vcd';
  $('test-file-list').replaceChildren(); $('saved-wave-list').replaceChildren();
  commandChanged();
}

const launch = `python -m companion --project /path/to/project --origin ${location.origin} --port 0`;
$('launch-command').textContent = launch; $('connect-command').textContent = launch;
for (const id of ['connect-toggle', 'empty-connect']) $(id).addEventListener('click', () => $('connect-dialog').showModal());
$('connect-close').addEventListener('click', () => $('connect-dialog').close());
$('guide-toggle').addEventListener('click', () => { $('guide').hidden = !$('guide').hidden; });
$('guide-close').addEventListener('click', () => { $('guide').hidden = true; });
$('pair-form').addEventListener('submit', event => {
  event.preventDefault();
  perform(async () => {
    if (preparing || pairing) return;
    // Ask before exchanging the one-use code, so cancelling never consumes it.
    if ((dirty() || activeJob) && !confirm('Pair a new session? Export unsaved edits first. A running native job is not stopped by switching sessions.')) return;
    pairing = true; $('pair').disabled = true; say('Pairing…', 'connection-status');
    try {
      const next = new CompanionClient($('endpoint').value);
      const data = await next.pair($('code').value.trim());
      const sameProject = data.project.id && model?.project?.id === data.project.id;
      epoch++; pollGeneration++;
      activeJob = null;
      if (sameProject) model.client = next;
      else {
        model?.dispose();
        model = new WorkspaceModel(next, {storageKey: data.project.id ? 'vwb.workspace.drafts.v1:' + data.project.id : null});
        activePath = null; discovery = null; activeJob = null; lastJob = null;
        openGeneration++; resetResults();
        $('persist-drafts').checked = false;
        if (model.storageKey) {
          try {
            if (localStorage.getItem(model.storageKey + ':enabled') === 'true') {
              model.setStorage(localStorage); $('persist-drafts').checked = !!model.storage;
            }
          } catch { say('Browser recovery is unavailable. Export drafts to keep unsaved edits.', 'global-status', true); }
        }
      }
      client = next; model.project = data.project; online = true;
      $('code').value = '';
      $('project-name').textContent = data.project.name;
      say('Connected to ' + data.project.name + '.', 'connection-status');
      $('connect-dialog').close();
      await refresh(); renderEditor(); await readHash();
      say($('persist-drafts').checked ? 'Connected. Unsaved browser recovery is enabled for this project.' : 'Connected. Recovery is opt-in; enable it to restore unsaved edits after a reload.');
    } finally { pairing = false; $('pair').disabled = false; }
  }, 'connection-status', false);
});
$('retry').addEventListener('click', () => perform(async () => {
  const health = await client.request('/v1/health', {authenticated: false});
  if (health.protocol !== 1) throw new Error('Companion protocol mismatch. Update the companion.');
  await model.connect(); online = true; say('Connected to ' + model.project.name + '.', 'connection-status');
  renderExplorer(); $('connect-dialog').close();
  if (activeJob) await pollJob();
}, 'connection-status'));
$('forget').addEventListener('click', () => {
  if (activeJob && !confirm('The native job will keep running. Forget this session anyway?')) return;
  epoch++; pollGeneration++; client?.forget(); online = false;
  say('Session forgotten. Edits remain here; restart the companion and pair its new code.', 'connection-status');
  renderControls();
});
$('refresh').addEventListener('click', () => perform(refresh));
$('new-file').addEventListener('click', () => fileManager.open('file'));
$('new-folder').addEventListener('click', () => fileManager.open('folder'));
$('manage-files').addEventListener('click', () => fileManager.open('manage'));
$('open-trash').addEventListener('click', () => fileManager.open('trash'));
$('filter').addEventListener('input', renderExplorer);
for (const item of all('[data-explorer]')) item.addEventListener('click', () => {
  explorerView = item.dataset.explorer;
  for (const tab of all('[data-explorer]')) tab.setAttribute('aria-pressed', String(tab === item));
  renderExplorer();
});
for (const item of all('[data-result]')) item.addEventListener('click', () => showResult(item.dataset.result));
$('command').addEventListener('change', commandChanged);
$('wave-mode').addEventListener('change', commandChanged);
$('module').addEventListener('input', () => { if (explorerView === 'designs') renderExplorer(); });
$('editor').addEventListener('input', () => {
  try {
    model.edit(activePath, $('editor').value); editVersion++; renderTabs(); renderControls(); renderFileStatus();
    if (lastJob) renderJob(lastJob);
  } catch (error) { $('editor').value = model.buffers.get(activePath).content; say(describe(error), 'global-status', true); }
});
for (const type of ['click', 'keyup']) $('editor').addEventListener(type, renderFileStatus);
$('save').addEventListener('click', () => perform(async () => { await model.save(activePath); renderTabs(); say('Saved to local file.'); }));
$('save-all').addEventListener('click', () => perform(async () => { try { await model.saveAll(); say('All open files saved.'); } finally { renderTabs(); } }));
$('reload').addEventListener('click', () => perform(async () => {
  const file = model.buffers.get(activePath);
  if ((file.dirty || file.conflict) && !confirm('Discard unsaved edits and reload ' + activePath + ' from disk? Export drafts first to keep them.')) return;
  await model.reload(activePath);
  editVersion++;
  if (lastJob) renderJob(lastJob);
  renderEditor();
}));
document.addEventListener('keydown', event => {
  if (event.isComposing) return;
  const command = (event.ctrlKey || event.metaKey) && event.code === 'Space' ? 'completion' : event.key === 'F12' ? (event.shiftKey ? 'references' : 'definition') : null;
  if (command && document.activeElement === $('editor')) {
    event.preventDefault(); showResult('intelligence'); void intelligence.query(command); return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault(); if (!$('save').disabled) $('save').click();
  }
});
$('persist-drafts').addEventListener('change', () => perform(() => {
  const requested = $('persist-drafts').checked;
  try { model.setStorage(requested ? localStorage : null); }
  finally { $('persist-drafts').checked = !!model.storage; }
  if (model.storage) localStorage.setItem(model.storageKey + ':enabled', 'true');
  else localStorage.removeItem(model.storageKey + ':enabled');
  if (model.storageError) throw new Error(model.storageError);
  say(model.storage ? 'Unsaved recovery enabled for this project on this browser.' : 'Browser recovery disabled and stored drafts removed. In-memory edits are retained.');
}));
$('export-drafts').addEventListener('click', () => perform(() => {
  download(new Blob([JSON.stringify(model.exportDrafts(), null, 2) + '\n'], {type: 'application/json'}), 'vwb-drafts.json');
}));
$('import-drafts').addEventListener('change', () => perform(async () => {
  const file = $('import-drafts').files[0]; if (!file) return;
  const workspace = model, importEpoch = epoch;
  try {
    if (file.size > MAX_DRAFT_IMPORT_BYTES) throw new Error('Draft JSON exceeds the import size limit. Source text is limited to 64 MiB total and 2 MiB per file.');
    const data = JSON.parse(await file.text());
    if (workspace !== model || importEpoch !== epoch) return;
    workspace.importDrafts(data); say('Drafts staged for review. Restore individual files before saving.');
  } finally { $('import-drafts').value = ''; }
}));
$('run').addEventListener('click', () => perform(() => startJob()));
$('program').addEventListener('click', () => perform(() => startJob(true)));
$('cancel').addEventListener('click', () => perform(async () => {
  if (activeJob) { await client.request('/v1/jobs/' + encodeURIComponent(activeJob) + '/cancel', {method: 'POST', body: {}}); say('Cancellation requested. Waiting for native process cleanup.'); }
}));
$('resume-job').addEventListener('click', () => perform(pollJob));
$('refresh-artifacts').addEventListener('click', () => perform(refreshArtifacts));
$('check-tools').addEventListener('click', () => perform(capabilities));
$('import-vcd').addEventListener('change', () => perform(async () => {
  const file = $('import-vcd').files[0]; if (!file) return;
  const importEpoch = epoch, generation = ++artifactGeneration;
  try {
    if (file.size > 16 * 1024 * 1024) throw new Error('VCD viewer limit is 16 MiB.');
    const text = await file.text();
    if (importEpoch === epoch && generation === artifactGeneration) openWaveform(text, file.name);
  } finally { $('import-vcd').value = ''; }
}));
window.addEventListener('hashchange', () => perform(readHash));
window.addEventListener('beforeunload', event => {
  if (dirty() || model?.recoverable.length || activeJob) { event.preventDefault(); event.returnValue = ''; }
});
const acceptEdits = result => {
  model.acceptEditorFiles(result.files); editVersion++;
  renderEditor(); if (lastJob) renderJob(lastJob);
};
function resetIntelligence() {
  intelligence?.dispose();
  intelligence = mountIntelligence($('intelligence-view'), {
    getContext: () => ({client, model, path: activePath, editor: $('editor'), online, sessionId: epoch, jobRunning: !!activeJob || preparing, workspaceBusy: preparing || !!model?.structureBusy}),
    onNavigate: async (path, range) => {
      const workspace = model, navigationEpoch = epoch;
      const opening = openFile(path, range?.start?.line + 1), navigationGeneration = openGeneration;
      await opening;
      if (range && workspace === model && navigationEpoch === epoch &&
          navigationGeneration === openGeneration && activePath === path) {
        const editor = $('editor');
        editor.setSelectionRange(offsetAt(editor.value, range.start), offsetAt(editor.value, range.end));
        editor.focus();
      }
    },
    onApply: acceptEdits, onUndo: acceptEdits,
    onRecover: result => { if (result.files?.length) acceptEdits(result); },
    onBusy: value => { preparing = value; renderControls(); },
    onStatus: (message, error = false, cause) => {
      if (cause?.status === 401 || cause?.name === 'AbortError' || cause instanceof TypeError) {
        online = false;
        say('Disconnected. Retry the connection to retain this session, or pair a restarted companion.', 'connection-status', true);
        renderControls();
      }
      say(message, 'global-status', error);
    },
  });
}
resetIntelligence();
fileManager = mountFileManager($('files-dialog'), {
  getContext: () => ({client, model, path: activePath, online, sessionId: epoch, jobRunning: !!activeJob || preparing}),
  onBusy: value => { preparing = value; renderControls(); renderTabs(); renderExplorer(); renderRecovery(); },
  onChanged: async ({operation, entry}) => {
    if (operation === 'move' && activePath && (activePath === entry.previous_path || activePath.startsWith(entry.previous_path + '/'))) {
      activePath = entry.path + activePath.slice(entry.previous_path.length);
    } else if (operation === 'trash' && activePath && (activePath === entry.path || activePath.startsWith(entry.path + '/'))) {
      activePath = model.buffers.keys().next().value;
    }
    if (activePath && !model.buffers.has(activePath)) activePath = model.buffers.keys().next().value;
    discovery = null; editVersion++; openGeneration++;
    resetIntelligence(); renderEditor(); updateHash();
    if (lastJob) renderJob(lastJob);
    if (operation === 'create' && entry.kind === 'file') await openFile(entry.path);
  },
  onStatus: (message, error = false, cause) => {
    if (cause?.status === 401 || cause?.name === 'AbortError' || cause instanceof TypeError) {
      online = false;
      say('Disconnected. Retry the connection to retain this session, or pair a restarted companion.', 'connection-status', true);
      renderControls();
    }
    say(message, 'global-status', error);
  },
});
const sourceContext = () => ({path: model?.buffers.has(activePath) ? activePath : undefined, sessionId: epoch});
syntaxView = mountSyntax($('source-editor'), {editor: $('editor'), gutter: $('editor-gutter'), backdrop: $('syntax-backdrop'), status: $('syntax-status'), getContext: sourceContext});
fileSearch = mountSearch($('find-bar'), {editor: $('editor'), openButton: $('find-open'), query: $('find-query'), matchCase: $('find-case'), previous: $('find-prev'), next: $('find-next'), closeButton: $('find-close'), status: $('find-status'), getContext: sourceContext, onSelect: renderFileStatus});
window.addEventListener('pagehide', event => {
  // A back/forward cache restore keeps the same objects and listeners alive.
  if (event.persisted) return;
  disposeWaveform?.(); disposeSchematic?.(); if (previewUrl) URL.revokeObjectURL(previewUrl);
  intelligence.dispose(); fileManager.dispose(); syntaxView.dispose(); fileSearch.dispose();
});
commandChanged(); renderControls(); perform(readHash);
