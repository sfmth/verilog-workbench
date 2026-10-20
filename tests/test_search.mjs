import assert from 'node:assert/strict';
import test from 'node:test';
import {findMatches, matchIndex, mountSearch, MAX_SEARCH_MATCHES, MAX_SEARCH_QUERY} from '../web/workspace/search.mjs';

test('find is literal, including regex operators and HTML', () => {
  for (const query of ['.*', '[x]', '\\', 'a+b?', '${x}', '<script>']) {
    const source = `😀 ${query} / ${query}`;
    assert.deepEqual(findMatches(source, query).matches, [{from: 3, to: 3 + query.length}, {from: 6 + query.length, to: 6 + 2 * query.length}]);
  }
});
test('case folding keeps exact UTF-16 offsets even after expanding lowercase characters', () => {
  assert.deepEqual(findMatches('İ😀ABC abc', 'abc').matches, [{from: 3, to: 6}, {from: 7, to: 10}]);
  assert.deepEqual(findMatches('İ😀ABC abc', 'abc', {matchCase: true}).matches, [{from: 7, to: 10}]);
  assert.deepEqual(findMatches('𐐀𐐨😀', '𐐨').matches, [{from: 0, to: 2}, {from: 2, to: 4}]);
  assert.deepEqual(findMatches('e\u0301 é', 'é').matches, [{from: 3, to: 4}]);
});
test('empty, multiline, non-overlapping and terminal matches preserve source', () => {
  const source = 'aaa\n😀\naaa\n';
  assert.deepEqual(findMatches(source, '').matches, []);
  assert.deepEqual(findMatches(source, 'aa').matches, [{from: 0, to: 2}, {from: 7, to: 9}]);
  assert.deepEqual(findMatches(source, '\n😀\n').matches, [{from: 3, to: 7}]);
  assert.deepEqual(findMatches(source, '\n').matches.at(-1), {from: 10, to: 11});
});
test('result and query limits are explicit and bounded', () => {
  assert.equal(findMatches('a'.repeat(MAX_SEARCH_MATCHES), 'a').truncated, false);
  const capped = findMatches('a'.repeat(MAX_SEARCH_MATCHES + 1), 'a');
  assert.equal(capped.matches.length, MAX_SEARCH_MATCHES);
  assert.equal(capped.truncated, true);
  assert.deepEqual(findMatches('a', 'x'.repeat(MAX_SEARCH_QUERY + 1)), {matches: [], truncated: false, reason: 'query'});
  assert.equal(findMatches('aa', 'a', {maxMatches: 1}).truncated, true);
  for (const maxMatches of [0, -1, 0.5, Infinity, 2001]) assert.throws(() => findMatches('a', 'a', {maxMatches}), RangeError);
});
test('navigation wraps, respects direction and starts from current caret or selection', () => {
  const matches = [{from: 2, to: 4}, {from: 8, to: 10}];
  assert.equal(matchIndex(matches, 0, 0), 0);
  assert.equal(matchIndex(matches, 4, 4), 1);
  assert.equal(matchIndex(matches, 2, 4), 1);
  assert.equal(matchIndex(matches, 8, 10), 0);
  assert.equal(matchIndex(matches, 2, 4, -1), 1);
  assert.equal(matchIndex(matches, 8, 8, -1), 0);
  assert.equal(matchIndex(matches, 1, 1, -1), 1);
  assert.equal(matchIndex([], 0, 0), -1);
});

function mounted() {
  const document = new EventTarget();
  document.createElement = () => ({getContext: () => null});
  const make = () => Object.assign(new EventTarget(), {ownerDocument: document, disabled: false, value: '',
    focus() { document.activeElement = this; }, select() { this.selected = true; }});
  const editor = Object.assign(make(), {value: 'x x x', selectionStart: 0, selectionEnd: 0,
    scrollTop: 0, scrollLeft: 0, clientHeight: 200, clientWidth: 400,
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }});
  const bar = Object.assign(make(), {hidden: true});
  const openButton = make(), query = make(), matchCase = Object.assign(make(), {checked: false});
  const previous = make(), next = make(), closeButton = make(), status = make();
  bar.contains = node => [query, matchCase, previous, next, closeButton, status].includes(node);
  const context = {path: 'source.py', sessionId: 1};
  const view = mountSearch(bar, {editor, openButton, query, matchCase, previous, next, closeButton, status, getContext: () => context});
  return {document, editor, bar, openButton, query, matchCase, previous, next, closeButton, status, context, view};
}
const key = (target, values) => target.dispatchEvent(Object.assign(new Event('keydown', {cancelable: true}), values));
const click = target => target.dispatchEvent(new Event('click'));

test('mounted search tracks native selections and disabled editor state without changing text', t => {
  const originalStyle = globalThis.getComputedStyle;
  globalThis.getComputedStyle = () => ({lineHeight: '23px', paddingTop: '20px'});
  t.after(() => { if (originalStyle) globalThis.getComputedStyle = originalStyle; else delete globalThis.getComputedStyle; });
  const f = mounted();
  click(f.openButton);
  assert.equal(f.bar.hidden, false);
  assert.equal(f.document.activeElement, f.query);
  f.query.value = 'x'; f.query.dispatchEvent(new Event('input'));
  assert.equal(f.status.textContent, '1 of 3 matches');
  f.editor.setSelectionRange(3, 3); click(f.editor);
  assert.equal(f.status.textContent, '3 matches');
  f.editor.setSelectionRange(4, 5); f.editor.dispatchEvent(new Event('select'));
  assert.equal(f.status.textContent, '3 of 3 matches');
  f.editor.setSelectionRange(2, 3); f.editor.dispatchEvent(new Event('keyup'));
  assert.equal(f.status.textContent, '2 of 3 matches');
  f.editor.disabled = true; f.view.refresh();
  assert.equal(f.previous.disabled, true);
  assert.equal(f.next.disabled, true);
  click(f.next);
  assert.deepEqual([f.editor.selectionStart, f.editor.selectionEnd], [2, 3]);
  f.editor.disabled = false; f.view.refresh();
  assert.equal(f.next.disabled, false);
  click(f.next);
  assert.deepEqual([f.editor.selectionStart, f.editor.selectionEnd], [4, 5]);
  assert.equal(f.editor.value, 'x x x');
  f.view.dispose();
});

test('mounted search keyboard scope, composition, file refresh and cleanup retain native editing', t => {
  const originalStyle = globalThis.getComputedStyle;
  globalThis.getComputedStyle = () => ({lineHeight: '23px', paddingTop: '20px'});
  t.after(() => { if (originalStyle) globalThis.getComputedStyle = originalStyle; else delete globalThis.getComputedStyle; });
  const f = mounted();
  f.editor.focus();
  assert.equal(key(f.document, {ctrlKey: true, key: 'f', isComposing: true}), true);
  assert.equal(f.bar.hidden, true);
  assert.equal(key(f.document, {ctrlKey: true, key: 'f'}), false);
  assert.equal(f.bar.hidden, false);
  f.query.value = 'x'; f.query.dispatchEvent(new Event('input'));
  assert.equal(key(f.query, {key: 'Enter', isComposing: true}), true);
  assert.deepEqual([f.editor.selectionStart, f.editor.selectionEnd], [0, 1]);
  assert.equal(key(f.query, {key: 'Enter'}), false);
  assert.deepEqual([f.editor.selectionStart, f.editor.selectionEnd], [2, 3]);
  f.editor.value = 'new x'; f.context.path = 'other.py'; f.context.sessionId++; f.view.refresh();
  assert.equal(f.status.textContent, '1 match');
  assert.equal(key(f.document, {key: 'Escape'}), false);
  assert.equal(f.bar.hidden, true);
  assert.equal(f.document.activeElement, f.editor);
  f.view.dispose();
  click(f.openButton);
  assert.equal(f.bar.hidden, true);
  assert.equal(key(f.document, {ctrlKey: true, key: 'f'}), true);
  assert.equal(f.editor.value, 'new x');
});
