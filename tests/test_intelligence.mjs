import assert from 'node:assert/strict';
import test from 'node:test';
import {positionAt, offsetAt, buildDocuments, applyCompletion, captureContext, isContextCurrent, mountIntelligence} from '../web/workspace/intelligence.mjs';

const range = (line, start, end) => ({start: {line, character: start}, end: {line, character: end}});

test('UTF-16 positions round trip astral characters, blank lines and terminal newlines', () => {
  const source = 'a😀b\n\n𐐀x\n';
  for (let offset = 0; offset <= source.length; offset++) {
    assert.equal(offsetAt(source, positionAt(source, offset)), offset);
  }
  assert.deepEqual(positionAt(source, 4), {line: 0, character: 4});
  assert.deepEqual(positionAt(source, 8), {line: 2, character: 2});
  assert.deepEqual(positionAt(source, source.length), {line: 3, character: 0});
});

test('positions understand CRLF and CR, reject invalid positions and avoid clamping service edits', () => {
  const source = 'abc\r\nxy\rz\n';
  assert.equal(offsetAt(source, {line: 1, character: 1}), 6);
  assert.deepEqual(positionAt(source, 4), {line: 0, character: 3});
  assert.deepEqual(positionAt(source, 5), {line: 1, character: 0});
  assert.equal(offsetAt(source, {line: 3, character: 0}), source.length);
  for (const position of [{line: -1, character: 0}, {line: 0, character: 4}, {line: 9, character: 0}, {line: 0.2, character: 0}, {line: 0, character: NaN}]) {
    assert.throws(() => offsetAt(source, position), /position|line/);
  }
  assert.throws(() => positionAt(source, source.length + 1), /offset/);
});

test('document snapshots preserve exact CRLF text and copy public model data', () => {
  const file = {path: 'rtl/top.sv', content: 'module top;\nendmodule\n', _exactContent: 'module top;\r\nendmodule\r\n', revision: 'a'.repeat(64), _version: 7};
  assert.deepEqual(buildDocuments({buffers: new Map([[file.path, file]])}), [{path: file.path, content: file._exactContent, revision: file.revision, version: 8}]);
  const original = [{path: 'test.py', content: 'print(1)\r\n', revision: 'b'.repeat(64), version: 2}];
  const snapshot = buildDocuments({editorDocuments: () => original});
  snapshot[0].content = 'changed';
  assert.equal(original[0].content, 'print(1)\r\n');
});

test('completion replaces the full server range including the suffix after the caret', () => {
  const result = applyCompletion('a😀 veryLongName;\n', {label: 'value', insert_text: 'value', range: range(0, 4, 16)}, {start: 7, end: 7});
  assert.deepEqual(result, {content: 'a😀 value;\n', selectionStart: 9, selectionEnd: 9});
  assert.equal(applyCompletion('abc', {label: 'X'}, {start: 1, end: 2}).content, 'aXc');
});

test('completion additional edits apply against the original text and move the caret correctly', () => {
  const result = applyCompletion('import x\npriSuffix\n', {
    label: 'print', insert_text: 'print', range: range(1, 0, 9),
    additional_edits: [{range: range(0, 7, 8), new_text: 'os, x'}],
  });
  assert.deepEqual(result, {content: 'import os, x\nprint\n', selectionStart: 18, selectionEnd: 18});
  assert.throws(() => applyCompletion('abcd', {label: 'x', range: range(0, 1, 3), additional_edits: [{range: range(0, 2, 4), new_text: 'q'}]}), /overlap/);
  assert.throws(() => applyCompletion('abc', {label: 'x', range: range(0, 1, 1), additional_edits: [{range: range(0, 1, 1), new_text: 'q'}]}), /overlap/);
  assert.throws(() => applyCompletion('a😀b', {label: 'x', range: range(0, 1, 2)}), /splits/);
});

test('completion rejects snippets, malformed edits and out-of-bounds ranges', () => {
  for (const item of [
    {label: 'print', insert_text: 'print($1)'},
    {label: 'print', insert_text: 'print(${1:value})'},
    {label: 'print', insert_text: 'print()', insert_text_format: 2},
    {label: 'print', textEdit: {range: range(0, 0, 0), newText: 'print($0)'}},
    {label: 'print', is_snippet: true},
  ]) assert.throws(() => applyCompletion('', item), /[Ss]nippet/);
  assert.throws(() => applyCompletion('abc', {label: 'x', range: range(0, 1, 10)}), /end of its line/);
  assert.throws(() => applyCompletion('abc', {label: 'x', range: range(0, 2, 1)}), /reversed/);
  assert.equal(applyCompletion('', {label: '$display', insert_text: '$display()'}).content, '$display()');
});

function context() {
  const file = {path: 'rtl/top.sv', content: 'module top;\nendmodule\n', _exactContent: 'module top;\r\nendmodule\r\n', _version: 2, revision: 'a'.repeat(64), dirty: false};
  return {client: {request() {}}, model: {buffers: new Map([[file.path, file]])}, path: file.path,
    editor: {value: file.content, selectionStart: 8, selectionEnd: 8}, online: true, sessionId: 1, jobRunning: false};
}

test('query snapshots reject edits, caret changes, reopened tabs, background buffers, sessions, disconnects and file operations', () => {
  const changes = [
    value => { value.editor.value += ' '; },
    value => { value.editor.selectionStart++; },
    value => { value.editor.selectionEnd++; },
    value => { value.model.buffers.get(value.path)._version++; },
    value => { value.model.buffers.get(value.path).revision = 'b'.repeat(64); },
    value => { value.model.buffers.set('test.py', {path: 'test.py', content: '', _version: 0, revision: null}); },
    value => { value.sessionId++; },
    value => { value.path = 'test.py'; },
    value => { value.online = false; },
    value => { value.client = {request() {}}; },
    value => { value.workspaceBusy = true; },
    value => { value.model.structureBusy = true; },
  ];
  for (const change of changes) {
    const current = context(), captured = captureContext(current);
    assert.equal(isContextCurrent(captured, current), true);
    change(current);
    assert.equal(isContextCurrent(captured, current), false);
  }
});

// Small DOM fixture keeps protocol/transaction tests runnable with node alone.
class Node {
  constructor(tag, ownerDocument) {
    this.tagName = tag;
    this.ownerDocument = ownerDocument;
    this.children = [];
    this.attributes = {};
    this.listeners = new Map();
    this.classList = {toggle() {}};
    this.value = '';
    this._text = '';
    this.selectionStart = this.selectionEnd = 0;
    this.hidden = this.disabled = false;
  }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(''); }
  set textContent(value) { this._text = String(value); this.children = []; }
  setAttribute(name, value) { this.attributes[name] = value; }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  replaceChildren(...children) { this._text = ''; this.children = []; this.append(...children); }
  addEventListener(name, listener) { const list = this.listeners.get(name) || []; list.push(listener); this.listeners.set(name, list); }
  dispatchEvent(event) { for (const listener of this.listeners.get(event.type) || []) listener(event); return true; }
  click() { if (!this.disabled) this.dispatchEvent(new Event('click')); }
  focus() { this.ownerDocument.activeElement = this; }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); }
}
const descendants = node => [node, ...node.children.flatMap(descendants)];
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };

function fixture(options = {}) {
  const doc = {createElement(tag) { return new Node(tag, this); }, defaultView: {Event}};
  const container = new Node('div', doc), current = context();
  current.editor = new Node('textarea', doc);
  current.editor.value = current.model.buffers.get(current.path).content;
  current.editor.setSelectionRange(8, 8);
  current.editor.addEventListener('input', () => {
    const file = current.model.buffers.get(current.path);
    file.content = current.editor.value;
    file._exactContent = file.content;
    file._version++;
    file.dirty = true;
  });
  const requests = [], statuses = [], busy = [], callbacks = [];
  current.client.request = async (path, request) => {
    requests.push({path, ...request});
    if (path === '/v1/editor/capabilities') {
      if (options.capabilities) return options.capabilities();
      return {position_encoding: 'utf-16', languages: {systemverilog: {available: true, server: 'fixture-lsp', operations: Object.fromEntries(['completion', 'hover', 'definition', 'references', 'rename'].map(operation => [operation, {available: true}]))}}, transactions: options.transactions || {recovery_required: false}};
    }
    return options.request?.(path, request);
  };
  const api = mountIntelligence(container, {
    getContext: () => current,
    onStatus: (...args) => statuses.push(args),
    onNavigate: (...args) => callbacks.push(['navigate', ...args]),
    onApply: async response => { callbacks.push(['apply', response]); await options.onApply?.(response); },
    onUndo: async response => { callbacks.push(['undo', response]); await options.onUndo?.(response); },
    onRecover: async response => { callbacks.push(['recover', response]); await options.onRecover?.(response); },
    onBusy: value => busy.push(value),
  });
  const find = (tag, text) => descendants(container).find(node => node.tagName === tag && node.textContent === text);
  const input = () => descendants(container).find(node => node.attributes['aria-label'] === 'New symbol name');
  async function rename() {
    await flush();
    find('button', 'Rename').click();
    input().value = 'new_top';
    await api.query('rename');
  }
  return {api, container, current, requests, statuses, busy, callbacks, find, rename};
}
const response = (operation, result, extra = {}) => ({operation, path: 'rtl/top.sv', result, ...extra});
const plan = {id: 'plan-one', expires_in: 60, files: [{path: 'rtl/top.sv', before: '<script>top</script>', after: '<script>new_top</script>', before_revision: 'a'.repeat(64), after_revision: 'b'.repeat(64), edit_count: 2}]};

test('semantic results are plain text; completion dispatches a draft edit without writing disk', async () => {
  const state = fixture({request: (path, {body}) => response(body.operation, {items: [{label: '<b>top</b>', insert_text: 'top', range: range(0, 7, 10)}]})});
  await flush();
  await state.api.query('completion');
  state.find('button', '<b>top</b>').click();
  assert.equal(state.current.editor.value, 'module top;\nendmodule\n');
  assert.equal(state.current.model.buffers.get(state.current.path).dirty, true);
  assert.equal(state.requests.filter(request => request.path !== '/v1/editor/capabilities').length, 1);
  assert.equal(state.requests[1].body.documents[0].content, 'module top;\r\nendmodule\r\n');
  assert.deepEqual(state.requests[1].body.position, {line: 0, character: 8});
});

test('stale query responses and completions cannot affect the editor or next workspace', async () => {
  const pending = deferred();
  const state = fixture({request: () => pending.promise});
  await flush();
  const query = state.api.query('completion');
  state.current.editor.value = 'changed';
  pending.resolve(response('completion', {items: [{label: 'top'}]}));
  await query;
  assert.equal(state.find('button', 'top'), undefined);
  assert.match(state.statuses.at(-1)[0], /Source or cursor changed/);

  const next = deferred();
  const other = fixture({request: () => next.promise});
  await flush();
  const request = other.api.query('hover');
  other.current.sessionId++;
  other.api.refresh();
  next.resolve(response('hover', {text: 'OLD SESSION SECRET'}));
  await request;
  assert.equal(other.container.textContent.includes('OLD SESSION SECRET'), false);

  for (const owner of ['workspace', 'model']) {
    const setBusy = (state, value) => {
      if (owner === 'workspace') state.current.workspaceBusy = value;
      else state.current.model.structureBusy = value;
    };
    const pendingCompletion = deferred();
    const completion = fixture({request: () => pendingCompletion.promise});
    await flush();
    const original = completion.current.editor.value;
    const inFlight = completion.api.query('completion');
    setBusy(completion, true);
    completion.api.refresh();
    pendingCompletion.resolve(response('completion', {items: [{label: 'new_top', range: range(0, 7, 10)}]}));
    await inFlight;
    assert.equal(completion.find('button', 'new_top'), undefined, owner);
    assert.equal(completion.current.editor.value, original);
    assert.equal(completion.current.model.buffers.get(completion.current.path).dirty, false);

    setBusy(completion, false);
    completion.api.refresh();
    await completion.api.query('completion');
    const offered = completion.find('button', 'new_top');
    assert.ok(offered);
    // Exercise the handler guard before rendering has disabled or removed an
    // already offered completion when the filesystem mutation begins.
    setBusy(completion, true);
    offered.click();
    assert.equal(completion.current.editor.value, original);
    assert.equal(completion.current.model.buffers.get(completion.current.path).dirty, false);

    const pendingRename = deferred();
    const rename = fixture({request: () => pendingRename.promise});
    const renaming = rename.rename();
    await flush();
    assert.equal(rename.requests.filter(item => item.path.endsWith('/query')).length, 1);
    setBusy(rename, true);
    rename.api.refresh();
    pendingRename.resolve(response('rename', {}, {plan}));
    await renaming;
    assert.equal(rename.find('button', 'Apply rename to local files'), undefined, owner);
    assert.equal(rename.requests.some(item => item.path.endsWith('/apply')), false);
    assert.equal(rename.callbacks.length, 0);
  }
});

test('multiline completion normalizes service newlines before placing the textarea caret', async () => {
  const state = fixture({request: (path, {body}) => response(body.operation, {items: [{label: 'two lines', insert_text: 'new_top\r\nnext', range: range(0, 7, 10)}]})});
  await flush();
  await state.api.query('completion');
  state.find('button', 'two lines').click();
  assert.equal(state.current.editor.value, 'module new_top\nnext;\nendmodule\n');
  assert.equal(state.current.editor.selectionStart, 'module new_top\nnext'.length);
});

test('rename requires a preview, clean buffers and no running jobs; Apply and Undo lock mutations', async () => {
  const state = fixture({request: (path, {body}) => {
    if (path.endsWith('/query')) return response(body.operation, {}, {plan});
    return {files: [], ...(path.endsWith('/apply') ? {undo_id: 'undo-one'} : {})};
  }});
  await state.rename();
  assert.equal(state.requests.some(request => request.path.endsWith('/apply')), false);
  assert.ok(state.find('pre', '<script>top</script>'));
  const apply = state.find('button', 'Apply rename to local files');
  const file = state.current.model.buffers.get(state.current.path);
  file.dirty = true;
  state.api.refresh();
  assert.equal(apply.disabled, true);
  assert.match(apply.title, /Save all/);
  file.dirty = false;
  state.current.jobRunning = true;
  state.api.refresh();
  assert.equal(apply.disabled, true);
  state.current.jobRunning = false;
  state.api.refresh();
  for (const owner of ['workspace', 'model']) {
    if (owner === 'workspace') state.current.workspaceBusy = true;
    else state.current.model.structureBusy = true;
    state.api.refresh();
    for (const label of ['Complete', 'Hover', 'Definition', 'References', 'Rename']) {
      assert.equal(state.find('button', label).disabled, true, owner + ': ' + label);
      assert.match(state.find('button', label).title, /project file operation/);
    }
    const before = state.requests.length;
    await state.api.query('completion');
    await state.api.query('rename');
    apply.click();
    await flush();
    assert.equal(state.requests.length, before, owner);
    assert.equal(state.callbacks.length, 0);
    assert.equal(file.dirty, false);
    if (owner === 'workspace') state.current.workspaceBusy = false;
    else state.current.model.structureBusy = false;
    state.api.refresh();
  }
  apply.click();
  await flush();
  assert.deepEqual(state.busy, [true, false]);
  assert.deepEqual(state.callbacks.map(call => call[0]), ['apply']);
  const undo = state.find('button', 'Undo last rename');
  assert.equal(undo.hidden, false);
  undo.click();
  await flush();
  assert.deepEqual(state.callbacks.map(call => call[0]), ['apply', 'undo']);
  assert.deepEqual(state.busy, [true, false, true, false]);
  assert.equal(undo.hidden, true);
});

test('undo remains available if disk rename succeeded but the model update failed', async () => {
  const state = fixture({request: (path, {body}) => path.endsWith('/query') ? response(body.operation, {}, {plan}) : {files: [], undo_id: 'undo-one'},
    onApply() { throw new Error('Editor refresh conflict; drafts retained.'); }});
  await state.rename();
  state.find('button', 'Apply rename to local files').click();
  await flush();
  assert.equal(state.find('button', 'Apply rename to local files'), undefined);
  assert.equal(state.find('button', 'Undo last rename').hidden, false);
  assert.match(state.statuses.at(-1)[0], /drafts retained/);
  assert.deepEqual(state.busy, [true, false]);
});

test('transaction results do not invoke callbacks after a workspace switch; dispose removes the panel', async () => {
  const pending = deferred();
  const state = fixture({request: (path, {body}) => path.endsWith('/query') ? response(body.operation, {}, {plan}) : pending.promise});
  await state.rename();
  state.find('button', 'Apply rename to local files').click();
  state.current.sessionId++;
  state.api.refresh();
  pending.resolve({files: [], undo_id: 'undo-one'});
  await flush();
  assert.equal(state.callbacks.length, 0);
  state.api.dispose();
  assert.equal(state.container.children.length, 0);
});

test('service errors are reported, unavailable operation reasons are visible, and locations navigate ranges', async () => {
  const state = fixture({request: (path, {body}) => body.operation === 'hover' ? Promise.reject(Object.assign(new Error('Companion disconnected'), {status: 401})) :
    response(body.operation, {locations: [{path: 'rtl/top.sv', range: range(0, 7, 10)}]}, {diagnostics: [{path: 'rtl/top.sv', version: 2, items: [{message: '<img src=x>', range: range(1, 0, 9)}]}]})});
  await flush();
  await state.api.query('hover');
  assert.match(state.statuses.at(-1)[0], /disconnected/);
  assert.equal(state.statuses.at(-1)[1], true);
  assert.equal(state.statuses.at(-1)[2].status, 401);
  await state.api.query('definition');
  state.find('button', 'rtl/top.sv:1:8').click();
  assert.deepEqual(state.callbacks[0], ['navigate', 'rtl/top.sv', range(0, 7, 10)]);
  assert.ok(state.find('button', 'rtl/top.sv:2 · <img src=x>'));
  state.current.model.buffers.get(state.current.path).dirty = true;
  state.api.refresh();
  assert.equal(state.find('button', 'Rename').disabled, true);
  assert.match(state.find('button', 'Rename').title, /Save all/);
});

test('Verilog and Verilog headers use the companion SystemVerilog language service', async () => {
  for (const path of ['rtl/top.v', 'rtl/top.vh']) {
    const state = fixture({request: (endpoint, {body}) => ({operation: body.operation, path, result: {text: 'module top'}})});
    await flush();
    const file = state.current.model.buffers.get(state.current.path);
    state.current.model.buffers.clear();
    file.path = path;
    state.current.model.buffers.set(path, file);
    state.current.path = path;
    state.api.refresh();
    assert.equal(state.find('button', 'Hover').disabled, false);
    await state.api.query('hover');
    assert.ok(state.find('pre', 'module top'));
  }
});

test('interrupted transactions require explicit recovery and retain recovery drafts', async () => {
  const transactions = {recovery_required: true, message: 'Interrupted rename needs recovery.'};
  const state = fixture({transactions, request: (path, request) => {
    assert.equal(path, '/v1/editor/recover');
    assert.deepEqual(request.body, {});
    return {files: [], recovery_required: false};
  }});
  await flush();
  const recover = state.find('button', 'Recover interrupted rename');
  assert.equal(recover.hidden, false);
  assert.equal(state.find('button', 'Complete').disabled, true);
  assert.equal(state.container.textContent.includes('Interrupted rename needs recovery.'), true);
  state.current.model.recoverable = [{path: 'rtl/top.sv', content: 'draft'}];
  state.api.refresh();
  assert.equal(recover.disabled, true);
  assert.match(recover.title, /Restore or discard recovery drafts/);
  recover.click();
  await flush();
  assert.equal(state.requests.filter(request => request.path.endsWith('/recover')).length, 0);
  assert.equal(state.current.model.recoverable.length, 1);
  state.current.model.recoverable = [];
  state.api.refresh();
  recover.click();
  await flush();
  assert.deepEqual(state.busy, [true, false]);
  assert.deepEqual(state.callbacks.map(call => call[0]), ['recover']);
  assert.equal(recover.hidden, true);
  assert.equal(state.find('button', 'Complete').disabled, false);
});

test('failed transactions refresh recovery status and capability errors remain usable', async () => {
  const transactions = {recovery_required: false};
  const state = fixture({transactions, request: (path, {body}) => {
    if (path.endsWith('/query')) return response(body.operation, {}, {plan});
    transactions.recovery_required = true;
    throw new Error('Interrupted transaction; recovery required.');
  }});
  await state.rename();
  state.find('button', 'Apply rename to local files').click();
  await flush();
  assert.equal(state.find('button', 'Recover interrupted rename').hidden, false);
  assert.equal(state.find('button', 'Complete').disabled, true);

  const unavailable = fixture({capabilities() { throw new Error('Companion unavailable'); }});
  await flush();
  assert.equal(unavailable.container.textContent.includes('Companion unavailable'), true);
  assert.equal(unavailable.find('button', 'Complete').disabled, true);
  assert.equal(unavailable.find('button', 'Check language services').disabled, false);
});
