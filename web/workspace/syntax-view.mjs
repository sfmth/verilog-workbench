import {languageForPath, MAX_HIGHLIGHT_BYTES, MAX_HIGHLIGHT_LINES, MAX_HIGHLIGHT_TOKENS} from './syntax.mjs';

const KINDS = new Set(['comment', 'string', 'keyword', 'number', 'meta', 'operator', 'builtin']);
const LABELS = {systemverilog: 'SystemVerilog', vhdl: 'VHDL', python: 'Python'};
const DEBOUNCE_MS = 75;
const WORKER_TIMEOUT_MS = 2500;

function countLines(text) {
  let lines = 1;
  for (let index = 0; index < text.length; index++) {
    if (text[index] === '\n') lines++;
    else if (text[index] === '\r') {
      lines++;
      if (text[index + 1] === '\n') index++;
    }
  }
  return lines;
}

function validTokens(tokens, text) {
  if (!Array.isArray(tokens) || tokens.length > MAX_HIGHLIGHT_TOKENS) return false;
  let end = 0;
  for (const token of tokens) {
    if (!token || !Number.isSafeInteger(token.from) || !Number.isSafeInteger(token.to) ||
        token.from < end || token.to <= token.from || token.to > text.length || !KINDS.has(token.kind)) return false;
    // A decoration may never separate the halves of a Unicode scalar value.
    for (const offset of [token.from, token.to]) {
      if (offset > 0 && offset < text.length && /[\uD800-\uDBFF]/.test(text[offset - 1]) && /[\uDC00-\uDFFF]/.test(text[offset])) return false;
    }
    end = token.to;
  }
  return true;
}

/** Decorate a native textarea without participating in its editing or undo paths. */
export function mountSyntax(container, {editor, gutter, backdrop, status, getContext, workerFactory} = {}) {
  if (!container || !editor || !gutter || !backdrop || !status || typeof getContext !== 'function') {
    throw new TypeError('Syntax view needs an editor, gutter, backdrop, status and current context.');
  }
  const document = container.ownerDocument;
  let disposed = false, composing = false, generation = 0;
  let snapshot, pending, inFlight, worker, timer, workerTimeout, workerFailed = false;
  let resizeObserver;
  backdrop.setAttribute('aria-hidden', 'true');
  gutter.setAttribute('aria-hidden', 'true');

  function current(captured) {
    if (disposed || !captured || captured.id !== generation) return false;
    const context = getContext() || {};
    return context.path === captured.path && context.sessionId === captured.sessionId && editor.value === captured.text;
  }

  function syncScroll() {
    if (disposed) return;
    backdrop.scrollTop = editor.scrollTop;
    backdrop.scrollLeft = editor.scrollLeft;
    // The gutter has no horizontal source content and must not move sideways.
    gutter.scrollTop = snapshot?.lineCount <= MAX_HIGHLIGHT_LINES ? editor.scrollTop : 0;
    gutter.scrollLeft = 0;
  }

  function plain(captured, reason) {
    container.classList.remove('syntax-active');
    container.classList.add('syntax-plain');
    container.dataset.syntaxMode = 'plain';
    container.dataset.syntaxLanguage = captured?.language || 'plain';
    backdrop.replaceChildren();
    backdrop.dataset.trailingLine = 'false';
    const language = LABELS[captured?.language] || 'Plain text';
    status.textContent = captured?.path ? `${language} · ${reason}` : 'No file open';
  }

  function stopWorker() {
    clearTimeout(workerTimeout);
    workerTimeout = undefined;
    worker?.terminate();
    worker = undefined;
    inFlight = undefined;
  }

  function failWorker() {
    workerFailed = true;
    pending = undefined;
    stopWorker();
    if (current(snapshot)) plain(snapshot, 'Plain text: highlighting unavailable');
  }

  function render(captured, data) {
    if (!current(captured) || composing) return;
    if (data.mode === 'plain') {
      const reasons = {
        unsupported: 'Plain text: unsupported file type',
        bytes: 'Plain text: file exceeds the highlighting size limit',
        lines: 'Plain text: file exceeds the highlighting line limit',
        tokens: 'Plain text: file exceeds the highlighting token limit',
      };
      const detail = typeof data.reason === 'string' && data.reason.length <= 200 && !/[\x00-\x1f\x7f]/.test(data.reason) ? data.reason : '';
      plain(captured, reasons[data.reason] || (detail ? `Plain text: ${detail}` : 'Plain text: highlighting limit reached'));
      return;
    }
    if (data.mode !== 'highlighted' || data.language !== captured.language ||
        data.line_count !== captured.lineCount || !validTokens(data.tokens, captured.text)) {
      failWorker();
      return;
    }
    const fragment = document.createDocumentFragment();
    let offset = 0;
    for (const token of data.tokens) {
      if (token.from > offset) fragment.append(document.createTextNode(captured.text.slice(offset, token.from)));
      const span = document.createElement('span');
      span.className = `syntax-token syntax-${token.kind} tok-${token.kind}`;
      span.textContent = captured.text.slice(token.from, token.to);
      fragment.append(span);
      offset = token.to;
    }
    if (offset < captured.text.length) fragment.append(document.createTextNode(captured.text.slice(offset)));
    // A pre's final newline does not create the textarea's final blank line.
    const trailingLine = captured.text.endsWith('\n') || captured.text.endsWith('\r');
    if (trailingLine) fragment.append(document.createTextNode('\n'));
    backdrop.replaceChildren(fragment);
    backdrop.dataset.trailingLine = String(trailingLine);
    container.dataset.syntaxMode = 'highlighted';
    container.dataset.syntaxLanguage = captured.language;
    status.textContent = `${LABELS[captured.language] || captured.language} · Lexical highlighting`;
    container.classList.remove('syntax-plain');
    container.classList.add('syntax-active');
    syncScroll();
  }

  function startPending() {
    if (disposed || composing || inFlight || !pending || workerFailed) return;
    const captured = pending;
    pending = undefined;
    if (!current(captured)) return;
    try {
      if (!worker) {
        worker = workerFactory ? workerFactory() : new Worker(new URL('./syntax-worker.mjs', import.meta.url), {type: 'module'});
        worker.onerror = failWorker;
        worker.onmessageerror = failWorker;
        worker.onmessage = ({data}) => {
          const completed = inFlight;
          if (!completed || data?.id !== completed.id) return;
          clearTimeout(workerTimeout);
          workerTimeout = undefined;
          inFlight = undefined;
          render(completed, data);
          startPending();
        };
      }
      inFlight = captured;
      workerTimeout = setTimeout(failWorker, WORKER_TIMEOUT_MS);
      worker.postMessage({id: captured.id, path: captured.path, text: captured.text});
    } catch {
      failWorker();
    }
  }

  function refresh() {
    if (disposed) return;
    const context = getContext() || {};
    const text = editor.value;
    if (snapshot && snapshot.text === text && snapshot.path === context.path && snapshot.sessionId === context.sessionId) {
      syncScroll();
      return;
    }
    clearTimeout(timer);
    pending = undefined;
    const language = languageForPath(context.path || '');
    const lineCount = countLines(text);
    snapshot = {id: ++generation, text, path: context.path, sessionId: context.sessionId, language, lineCount};
    gutter.textContent = context.path ? lineCount <= MAX_HIGHLIGHT_LINES ?
      Array.from({length: lineCount}, (_, index) => String(index + 1)).join('\n') : '…' : '';
    gutter.dataset.lineCount = String(lineCount);
    plain(snapshot, 'Preparing lexical highlighting…');
    syncScroll();
    if (!context.path) return;
    if (language === 'plain') return plain(snapshot, 'Plain text: unsupported file type');
    if (lineCount > MAX_HIGHLIGHT_LINES) return plain(snapshot, `Plain text: ${lineCount.toLocaleString('en-US')} lines exceeds the 10,000-line highlighting limit`);
    if (text.length > MAX_HIGHLIGHT_BYTES || new TextEncoder().encode(text).byteLength > MAX_HIGHLIGHT_BYTES) {
      return plain(snapshot, 'Plain text: file exceeds the 256 KiB highlighting limit');
    }
    if (workerFailed) return plain(snapshot, 'Plain text: highlighting unavailable');
    pending = snapshot;
    if (!composing) timer = setTimeout(startPending, DEBOUNCE_MS);
  }

  function compositionStart() {
    composing = true;
    clearTimeout(timer);
    if (snapshot) plain(snapshot, 'Plain text: composing text');
  }
  function compositionEnd() {
    composing = false;
    // The final input event can precede compositionend in some browsers.
    snapshot = undefined;
    refresh();
  }
  editor.addEventListener('input', refresh);
  editor.addEventListener('scroll', syncScroll, {passive: true});
  editor.addEventListener('compositionstart', compositionStart);
  editor.addEventListener('compositionend', compositionEnd);
  if (typeof ResizeObserver === 'function') {
    resizeObserver = new ResizeObserver(syncScroll);
    resizeObserver.observe(editor);
  }
  refresh();
  return {
    refresh,
    dispose() {
      if (disposed) return;
      disposed = true;
      generation++;
      clearTimeout(timer);
      stopWorker();
      resizeObserver?.disconnect();
      editor.removeEventListener('input', refresh);
      editor.removeEventListener('scroll', syncScroll);
      editor.removeEventListener('compositionstart', compositionStart);
      editor.removeEventListener('compositionend', compositionEnd);
      container.classList.remove('syntax-active', 'syntax-plain');
      backdrop.replaceChildren();
      gutter.replaceChildren();
    },
  };
}
