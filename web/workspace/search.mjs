import {positionAt} from './intelligence.mjs';

export const MAX_SEARCH_MATCHES = 2000;
export const MAX_SEARCH_QUERY = 256;

/** Literal, non-overlapping Unicode matches with original UTF-16 offsets. */
export function findMatches(text, query, {matchCase = false, maxMatches = MAX_SEARCH_MATCHES} = {}) {
  if (typeof text !== 'string' || typeof query !== 'string') throw new TypeError('Search needs source text and a literal query.');
  if (!Number.isSafeInteger(maxMatches) || maxMatches < 1 || maxMatches > MAX_SEARCH_MATCHES) throw new RangeError('Invalid search result limit.');
  if (query.length > MAX_SEARCH_QUERY) return {matches: [], truncated: false, reason: 'query'};
  if (!query) return {matches: [], truncated: false};
  const pattern = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const expression = new RegExp(pattern, matchCase ? 'gu' : 'giu');
  const matches = [];
  for (const match of text.matchAll(expression)) {
    if (matches.length === maxMatches) return {matches, truncated: true};
    matches.push({from: match.index, to: match.index + match[0].length});
  }
  return {matches, truncated: false};
}

export function matchIndex(matches, start, end, direction = 1) {
  if (!matches.length) return -1;
  const selected = matches.findIndex(match => match.from === start && match.to === end);
  if (selected >= 0) return (selected + direction + matches.length) % matches.length;
  if (direction < 0) {
    for (let index = matches.length - 1; index >= 0; index--) if (matches[index].to <= start) return index;
    return matches.length - 1;
  }
  const following = matches.findIndex(match => match.from >= end);
  return following < 0 ? 0 : following;
}

function reveal(editor, offset) {
  const style = getComputedStyle(editor);
  const lineHeight = Number.parseFloat(style.lineHeight) || 23;
  const padding = Number.parseFloat(style.paddingTop) || 0;
  const position = positionAt(editor.value, offset);
  const y = position.line * lineHeight + padding;
  if (y < editor.scrollTop + padding || y + lineHeight > editor.scrollTop + editor.clientHeight - padding) {
    editor.scrollTop = Math.max(0, y - editor.clientHeight / 2);
  }
  const canvas = editor.ownerDocument.createElement('canvas');
  const context = canvas.getContext('2d');
  if (!context) return;
  context.font = `${style.fontSize} ${style.fontFamily}`;
  const tabWidth = context.measureText(' ').width * (Number.parseFloat(style.tabSize) || 4);
  let x = 0;
  let start = offset - position.character;
  while (start < offset) {
    const tab = editor.value.indexOf('\t', start);
    const end = tab < 0 || tab >= offset ? offset : tab;
    x += context.measureText(editor.value.slice(start, end)).width;
    if (end < offset) x = (Math.floor(x / tabWidth) + 1) * tabWidth;
    start = end + 1;
  }
  const left = Number.parseFloat(style.paddingLeft) || 0;
  if (x < editor.scrollLeft || x + left * 2 > editor.scrollLeft + editor.clientWidth) {
    editor.scrollLeft = Math.max(0, x - editor.clientWidth / 2);
  }
}

/** Search selects source; it never writes the textarea or the workspace model. */
export function mountSearch(bar, {editor, openButton, query, matchCase, previous, next, closeButton, status, getContext, onSelect = () => {}}) {
  let snapshot, result = {matches: [], truncated: false}, disposed = false;
  const listeners = [];
  const listen = (target, type, handler) => { target.addEventListener(type, handler); listeners.push(() => target.removeEventListener(type, handler)); };
  function render() {
    const index = result.matches.findIndex(match => match.from === editor.selectionStart && match.to === editor.selectionEnd);
    status.textContent = result.reason === 'query' ? 'Query limit: 256 characters' : !query.value ? 'Enter text to find' :
      !result.matches.length ? 'No matches' : `${index < 0 ? '' : `${index + 1} of `}${result.matches.length}${result.truncated ? '+ (first 2,000 shown)' : ''} match${result.matches.length === 1 ? '' : 'es'}`;
    previous.disabled = next.disabled = !result.matches.length || editor.disabled;
  }
  function refresh() {
    if (disposed) return;
    const context = getContext() || {};
    if (!context.path) { bar.hidden = true; snapshot = undefined; return; }
    if (bar.hidden) return;
    if (!snapshot || snapshot.text !== editor.value || snapshot.path !== context.path || snapshot.sessionId !== context.sessionId || snapshot.query !== query.value || snapshot.matchCase !== matchCase.checked) {
      snapshot = {text: editor.value, ...context, query: query.value, matchCase: matchCase.checked};
      result = findMatches(editor.value, query.value, {matchCase: matchCase.checked});
    }
    render();
  }
  function select(index, focus = false) {
    const match = result.matches[index];
    if (!match || editor.disabled) return;
    if (focus) editor.focus({preventScroll: true});
    editor.setSelectionRange(match.from, match.to);
    reveal(editor, match.from);
    onSelect(); render();
  }
  function navigate(direction) {
    refresh();
    select(matchIndex(result.matches, editor.selectionStart, editor.selectionEnd, direction));
  }
  function open() {
    if (openButton.disabled || !getContext()?.path) return;
    bar.hidden = false; refresh(); query.focus(); query.select();
  }
  function close() {
    bar.hidden = true;
    editor.focus({preventScroll: true});
  }
  function changed() {
    refresh();
    const index = result.matches.findIndex(match => match.from >= editor.selectionStart);
    select(index < 0 ? 0 : index);
  }
  listen(openButton, 'click', open);
  listen(closeButton, 'click', close);
  listen(query, 'input', changed);
  listen(matchCase, 'change', changed);
  listen(previous, 'click', () => navigate(-1));
  listen(next, 'click', () => navigate(1));
  listen(editor, 'input', refresh);
  // Keep the match counter current when the native selection changes outside
  // search navigation. The cached text/query snapshot avoids a new scan.
  for (const type of ['select', 'click', 'keyup']) listen(editor, type, refresh);
  listen(query, 'keydown', event => {
    if (event.isComposing) return;
    if (event.key === 'Enter') { event.preventDefault(); navigate(event.shiftKey ? -1 : 1); }
  });
  listen(editor.ownerDocument, 'keydown', event => {
    if (event.isComposing) return;
    const focused = editor.ownerDocument.activeElement;
    if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'f' && (focused === editor || bar.contains(focused))) {
      event.preventDefault(); open();
    } else if (event.key === 'Escape' && !bar.hidden && (focused === editor || bar.contains(focused))) {
      event.preventDefault(); close();
    }
  });
  return {refresh, dispose() { disposed = true; listeners.forEach(remove => remove()); snapshot = undefined; result = {matches: [], truncated: false}; }};
}
