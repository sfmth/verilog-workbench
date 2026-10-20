import assert from 'node:assert/strict';
import test from 'node:test';
import {mountSyntax} from '../web/workspace/syntax-view.mjs';

class FakeNode extends EventTarget {
  constructor(document, tag = '') {
    super();
    this.ownerDocument = document;
    this.tagName = tag;
    this.dataset = {};
    this.children = [];
    this.value = '';
    this.scrollTop = this.scrollLeft = 0;
    this.attributes = new Map();
    const classes = new Set();
    this.classList = {add: (...names) => names.forEach(name => classes.add(name)),
      remove: (...names) => names.forEach(name => classes.delete(name)), contains: name => classes.has(name)};
  }
  set textContent(text) { this.children = []; this.text = String(text); }
  get textContent() { return this.children.length ? this.children.map(child => child.textContent).join('') : this.text || ''; }
  setAttribute(name, value) { this.attributes.set(name, value); }
  append(...nodes) { this.text = ''; for (const node of nodes) this.children.push(...node.tagName === '#fragment' ? node.children : [node]); }
  replaceChildren(...nodes) { this.children = []; this.text = ''; this.append(...nodes); }
}
const document = {
  createElement: tag => new FakeNode(document, tag),
  createTextNode: text => Object.assign(new FakeNode(document, '#text'), {textContent: text}),
  createDocumentFragment: () => new FakeNode(document, '#fragment'),
};
const pause = () => new Promise(resolve => setTimeout(resolve, 95));

function fixture(text = 'module top;\nendmodule\n', path = 'rtl/top.sv') {
  const container = document.createElement('div'), editor = document.createElement('textarea');
  editor.value = text;
  editor.selectionStart = 3;
  editor.selectionEnd = 7;
  const gutter = document.createElement('pre'), backdrop = document.createElement('pre'), status = document.createElement('span');
  const context = {path, sessionId: 1};
  const worker = {messages: [], postMessage(message) { this.messages.push(message); }, terminate() { this.terminated = true; }};
  let created = 0;
  const view = mountSyntax(container, {editor, gutter, backdrop, status, getContext: () => context,
    workerFactory: () => { created++; return worker; }});
  const respond = (request = worker.messages.at(-1), extra = {}) => worker.onmessage({data: {
    id: request.id, language: 'systemverilog', mode: 'highlighted', line_count: (request.text.match(/\n/g) || []).length + 1,
    tokens: [{from: 0, to: 6, kind: 'keyword'}], ...extra,
  }});
  return {container, editor, gutter, backdrop, status, context, worker, view, respond, created: () => created};
}

test('syntax decoration preserves exact source, trailing blank line, text safety and native selection', async () => {
  const f = fixture('module <script>😀</script>;\n\n');
  await pause();
  f.respond();
  assert.equal(f.container.dataset.syntaxMode, 'highlighted');
  assert.equal(f.backdrop.textContent, f.editor.value + '\n');
  assert.equal(f.backdrop.dataset.trailingLine, 'true');
  assert.equal(f.gutter.textContent, '1\n2\n3');
  assert.equal(f.backdrop.children[0].tagName, 'span');
  assert.equal(f.backdrop.children[0].className, 'syntax-token syntax-keyword tok-keyword');
  assert.equal(f.backdrop.children[1].tagName, '#text');
  assert.equal(f.editor.value, 'module <script>😀</script>;\n\n');
  assert.equal(f.editor.selectionStart, 3);
  assert.equal(f.editor.selectionEnd, 7);
  f.editor.scrollTop = 91; f.editor.scrollLeft = 34;
  f.editor.dispatchEvent(new Event('scroll'));
  assert.equal(f.backdrop.scrollTop, 91);
  assert.equal(f.backdrop.scrollLeft, 34);
  assert.equal(f.gutter.scrollTop, 91);
  assert.equal(f.gutter.scrollLeft, 0);
  f.view.dispose();
});

test('input immediately reveals native text and discards held responses across text, paths and sessions', async () => {
  const f = fixture();
  await pause();
  const first = f.worker.messages[0];
  f.context.path = 'rtl/second.sv'; f.context.sessionId++;
  f.editor.value = 'module second;\n';
  f.view.refresh();
  assert.equal(f.container.dataset.syntaxMode, 'plain');
  assert.equal(f.backdrop.textContent, '');
  await pause();
  assert.equal(f.worker.messages.length, 1, 'at most one worker request is in flight');
  f.respond(first);
  assert.equal(f.container.dataset.syntaxMode, 'plain', 'the old response cannot decorate a new buffer');
  assert.equal(f.worker.messages.length, 2, 'only latest pending work starts after held response');
  f.respond();
  assert.equal(f.backdrop.textContent, f.editor.value + '\n');
  f.editor.value += '// edit';
  f.editor.dispatchEvent(new Event('input'));
  assert.equal(f.container.classList.contains('syntax-active'), false);
  assert.equal(f.backdrop.textContent, '');
  f.view.dispose();
});

test('malformed token ranges and worker failures leave native text editable', async () => {
  for (const tokens of [[{from: 0, to: 1000, kind: 'keyword'}], [{from: 0, to: 1, kind: '<script>'}],
    [{from: 0, to: 4, kind: 'keyword'}, {from: 3, to: 5, kind: 'keyword'}]]) {
    const f = fixture();
    await pause();
    f.respond(undefined, {tokens});
    assert.equal(f.container.dataset.syntaxMode, 'plain');
    assert.match(f.status.textContent, /unavailable/);
    assert.equal(f.worker.terminated, true);
    assert.equal(f.editor.value, 'module top;\nendmodule\n');
    f.view.dispose();
  }
  const f = fixture();
  await pause();
  f.worker.onerror(new Event('error'));
  f.editor.value = 'module still_editable;';
  f.editor.dispatchEvent(new Event('input'));
  assert.match(f.status.textContent, /unavailable/);
  assert.equal(f.created(), 1);
  f.view.dispose();
});

test('unsupported and bounded large-file modes avoid worker and unbounded decoration DOM', () => {
  for (const [text, path, reason] of [['<html>literal</html>', 'notes.html', /unsupported/],
    ['x'.repeat(262145), 'top.sv', /256 KiB/], ['😀'.repeat(70000), 'top.sv', /256 KiB/],
    ['\n'.repeat(10000), 'top.sv', /10,001 lines/]]) {
    const f = fixture(text, path);
    assert.equal(f.created(), 0);
    assert.equal(f.container.dataset.syntaxMode, 'plain');
    assert.equal(f.backdrop.children.length, 0);
    assert.match(f.status.textContent, reason);
    assert.equal(f.editor.value, text);
    if (text.startsWith('\n')) assert.equal(f.gutter.textContent, '…');
    f.view.dispose();
  }
});

test('composition keeps native text visible and disposal removes worker/listeners', async () => {
  const f = fixture();
  await pause();
  f.respond();
  f.editor.dispatchEvent(new Event('compositionstart'));
  assert.equal(f.container.classList.contains('syntax-active'), false);
  f.editor.value += '日本語';
  f.editor.dispatchEvent(new Event('input'));
  await pause();
  assert.equal(f.worker.messages.length, 1);
  f.editor.dispatchEvent(new Event('compositionend'));
  await pause();
  assert.equal(f.worker.messages.length, 2);
  f.respond();
  assert.equal(f.container.classList.contains('syntax-active'), true);
  f.view.dispose();
  assert.equal(f.worker.terminated, true);
  assert.equal(f.container.classList.contains('syntax-active'), false);
  assert.equal(f.backdrop.textContent, '');
  assert.equal(f.gutter.textContent, '');
  f.editor.value = 'after dispose';
  f.editor.dispatchEvent(new Event('input'));
  assert.equal(f.worker.messages.length, 2);
});
