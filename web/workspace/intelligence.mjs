const OPERATIONS = ['completion', 'hover', 'definition', 'references', 'rename'];
const LABELS = {completion: 'Complete', hover: 'Hover', definition: 'Definition', references: 'References', rename: 'Rename'};

/** LSP columns count UTF-16 code units, just like a textarea selection. */
export function positionAt(text, offset) {
  if (typeof text !== 'string' || !Number.isSafeInteger(offset) || offset < 0 || offset > text.length) {
    throw new Error('Invalid source offset.');
  }
  let line = 0, start = 0;
  for (let index = 0; index < offset; index++) {
    if (text[index] === '\r' || text[index] === '\n') {
      if (text[index] === '\r' && text[index + 1] === '\n') {
        if (index + 1 === offset) return {line, character: index - start};
        index++;
      }
      line++;
      start = index + 1;
    }
  }
  return {line, character: offset - start};
}

/** Reject out-of-range service edits instead of silently changing a different span. */
export function offsetAt(text, position) {
  if (typeof text !== 'string' || !position || !Number.isSafeInteger(position.line) ||
      !Number.isSafeInteger(position.character) || position.line < 0 || position.character < 0) {
    throw new Error('Invalid UTF-16 source position.');
  }
  let line = 0, start = 0;
  for (let index = 0; line < position.line && index < text.length; index++) {
    if (text[index] === '\r' || text[index] === '\n') {
      if (text[index] === '\r' && text[index + 1] === '\n') index++;
      line++;
      start = index + 1;
    }
  }
  if (line !== position.line) throw new Error('Source position is past the last line.');
  let end = start;
  while (end < text.length && text[end] !== '\r' && text[end] !== '\n') end++;
  if (position.character > end - start) throw new Error('Source position is past the end of its line.');
  return start + position.character;
}

/** Send exact source text and the disk revision used for optimistic conflict checks. */
export function buildDocuments(model) {
  if (!model) return [];
  const documents = typeof model.editorDocuments === 'function'
    ? model.editorDocuments()
    : [...model.buffers.values()].map(buffer => ({
      path: buffer.path,
      content: buffer._exactContent ?? buffer.content.replace(/\n/g, buffer.newline || '\n'),
      revision: buffer.revision,
      version: (buffer._version || 0) + 1,
    }));
  return documents.map(document => ({...document}));
}

function plainCompletion(item) {
  if (!item || typeof item !== 'object') throw new Error('Invalid completion item.');
  if (item.insert_text_format === 2 || item.insertTextFormat === 2 || item.is_snippet || item.snippet) {
    throw new Error('Snippet completions are not supported in this editor.');
  }
  const text = item.insert_text ?? item.insertText ?? item.label;
  if (typeof text !== 'string') throw new Error('Completion must contain plain text.');
  // Some servers omit insertTextFormat despite sending snippet placeholders.
  if (/(?:\$\d|\$\{[^}]*\})/.test(text)) throw new Error('Snippet placeholders cannot be inserted as source text.');
  return text;
}

function editOffsets(text, range, replacement) {
  if (!range || typeof replacement !== 'string') throw new Error('Invalid completion edit.');
  const start = offsetAt(text, range.start), end = offsetAt(text, range.end);
  if (end < start) throw new Error('Completion edit has a reversed range.');
  for (const offset of [start, end]) {
    if (offset > 0 && offset < text.length && /[\uD800-\uDBFF]/.test(text[offset - 1]) && /[\uDC00-\uDFFF]/.test(text[offset])) {
      throw new Error('Completion edit splits a UTF-16 character.');
    }
  }
  return {start, end, text: replacement};
}

/** Apply the server's complete replacement range, including text after the caret. */
export function applyCompletion(text, item, selection = {start: 0, end: 0}) {
  const replacement = plainCompletion(item);
  const range = item.range ?? item.textEdit?.range ?? {
    start: positionAt(text, selection.start), end: positionAt(text, selection.end),
  };
  const primary = editOffsets(text, range, item.textEdit?.newText ?? replacement);
  // Validate textEdit too; a label alone must never hide an unsupported snippet.
  plainCompletion({...item, insert_text: primary.text});
  const additions = item.additional_edits ?? item.additionalTextEdits ?? [];
  if (!Array.isArray(additions)) throw new Error('Invalid additional completion edits.');
  const edits = [primary, ...additions.map(edit => editOffsets(text, edit.range, edit.new_text ?? edit.newText))];
  const ordered = [...edits].sort((a, b) => a.start - b.start || a.end - b.end);
  for (let index = 1; index < ordered.length; index++) {
    if (ordered[index].start < ordered[index - 1].end || ordered[index].start === ordered[index - 1].start) {
      throw new Error('Completion edits overlap.');
    }
  }
  let content = text;
  for (const edit of ordered.reverse()) content = content.slice(0, edit.start) + edit.text + content.slice(edit.end);
  const shift = edits.slice(1).filter(edit => edit.end <= primary.start).reduce((sum, edit) => sum + edit.text.length - (edit.end - edit.start), 0);
  const caret = primary.start + primary.text.length + shift;
  return {content, selectionStart: caret, selectionEnd: caret};
}

/** A query belongs to an exact workspace, all open buffers, and a caret selection. */
export function captureContext(context) {
  return {
    client: context.client, model: context.model, editor: context.editor,
    path: context.path, sessionId: context.sessionId,
    content: context.editor?.value, start: context.editor?.selectionStart, end: context.editor?.selectionEnd,
    documents: buildDocuments(context.model),
  };
}

function sameSession(snapshot, context) {
  return !!context?.online && snapshot.client === context.client && snapshot.model === context.model &&
    snapshot.sessionId === context.sessionId;
}

export function isContextCurrent(snapshot, context) {
  if (context.workspaceBusy || context.model?.structureBusy || !sameSession(snapshot, context) || snapshot.path !== context.path || snapshot.editor !== context.editor ||
      snapshot.content !== context.editor?.value || snapshot.start !== context.editor?.selectionStart ||
      snapshot.end !== context.editor?.selectionEnd) return false;
  const documents = buildDocuments(context.model);
  return documents.length === snapshot.documents.length && snapshot.documents.every((document, index) => {
    const current = documents[index];
    return document.path === current.path && document.content === current.content &&
      document.revision === current.revision && document.version === current.version;
  });
}

const anyDirty = model => [...(model?.buffers.values() || [])].some(buffer => buffer.dirty || buffer.conflict || buffer.saving);
const hasRecoveryDrafts = model => !!model?.recoverable?.length;
function languageOf(path) {
  if (/\.pyi?$/i.test(path || '')) return 'python';
  if (/\.svh?$/i.test(path || '')) return 'systemverilog';
  if (/\.vh?$/i.test(path || '')) return 'systemverilog';
  if (/\.vhdl?$/i.test(path || '')) return 'vhdl';
  return null;
}

/** Companion-backed semantic tools. All service output is rendered as plain text. */
export function mountIntelligence(container, {getContext, onNavigate = () => {}, onApply = () => {}, onUndo = () => {}, onRecover = () => {}, onStatus = () => {}, onBusy = () => {}}) {
  const doc = container.ownerDocument;
  const element = (tag, text, parent, attributes = {}) => {
    const result = doc.createElement(tag);
    if (text !== undefined) result.textContent = text;
    for (const [name, value] of Object.entries(attributes)) result.setAttribute(name, value);
    parent?.append(result);
    return result;
  };
  const root = element('section', undefined, null, {class: 'intelligence-view', 'aria-label': 'Semantic editor tools'});
  container.replaceChildren(root);
  const capabilityStatus = element('p', 'Connect a companion to use semantic editor tools.', root, {class: 'intelligence-capabilities'});
  const toolbar = element('div', undefined, root, {class: 'intelligence-controls'});
  const buttons = new Map();
  const status = element('p', 'Open a Verilog, SystemVerilog, VHDL or Python file to inspect symbols.', root, {role: 'status', 'aria-live': 'polite'});
  const renameForm = element('form', undefined, root, {class: 'intelligence-rename'});
  renameForm.hidden = true;
  const renameLabel = element('label', 'New symbol name ', renameForm);
  const renameName = element('input', undefined, renameLabel, {type: 'text', 'aria-label': 'New symbol name', autocomplete: 'off', spellcheck: 'false'});
  const previewButton = element('button', 'Preview rename', renameForm, {type: 'submit'});
  const results = element('div', undefined, root, {class: 'intelligence-results'});
  const diagnostics = element('div', undefined, root, {class: 'intelligence-diagnostics'});
  let disposed = false, capabilities = null, capabilityError = '', capabilityLoading = false;
  let owner = null, generation = 0, capabilityGeneration = 0, busy = false, pendingPlan = null, undo = null, displayed = null, wasOnline = false;

  function report(message, error = false, cause) {
    if (disposed) return;
    status.textContent = message;
    status.classList.toggle('error', error);
    onStatus(message, error, cause);
  }
  function makeButton(label, action, parent = toolbar) {
    const button = element('button', label, parent, {type: 'button'});
    button.addEventListener('click', () => { void action(); });
    return button;
  }
  for (const operation of OPERATIONS) {
    buttons.set(operation, makeButton(LABELS[operation], operation === 'rename' ? () => {
      renameForm.hidden = !renameForm.hidden;
      if (!renameForm.hidden) renameName.focus();
    } : () => query(operation)));
  }
  const retryButton = makeButton('Check language services', async () => {
    capabilities = null;
    capabilityError = '';
    await loadCapabilities();
  });
  const undoButton = makeButton('Undo last rename', () => mutate('undo'));
  undoButton.hidden = true;
  const recoverButton = makeButton('Recover interrupted rename', () => mutate('recover'));
  recoverButton.hidden = true;
  renameForm.addEventListener('submit', event => {
    event.preventDefault();
    void query('rename');
  });

  function languageState(context) {
    const language = languageOf(context.path);
    return {language, state: capabilities?.languages?.[language] ??
      (language === 'systemverilog' ? capabilities?.languages?.verilog : null)};
  }
  function reasonFor(operation, context) {
    if (!context.online || !context.client || !context.model) return 'Connect the companion first.';
    if (context.workspaceBusy || context.model.structureBusy) return 'Wait for the project file operation.';
    if (!context.path || !context.editor || !context.model.buffers.has(context.path)) return 'Open a source file first.';
    if (busy) return 'Wait for the current editor request.';
    if (!capabilities) return capabilityError || 'Checking language service capabilities…';
    if (capabilities.transactions?.recovery_required) return 'Recover the interrupted rename before requesting semantic results.';
    const {state} = languageState(context);
    if (!state) return 'No language service is configured for this file type.';
    if (!state.available) return state.reason || 'This language server is unavailable on the companion.';
    if (!state.operations?.[operation]?.available) return state.operations?.[operation]?.reason || `${LABELS[operation]} is unavailable from this language server.`;
    if (operation === 'rename' && anyDirty(context.model)) return 'Save all files before previewing a rename.';
    if (operation === 'rename' && hasRecoveryDrafts(context.model)) return 'Restore or discard recovery drafts before previewing a rename.';
    if (operation === 'rename' && context.jobRunning) return 'Wait for the running job before previewing a rename.';
    return '';
  }
  function renderControls() {
    if (disposed) return;
    const context = getContext();
    const {language, state} = languageState(context);
    capabilityStatus.textContent = !context.online ? 'Connect a companion to use semantic editor tools.' :
      capabilityLoading ? 'Checking companion language services…' : capabilityError ? capabilityError :
      capabilities?.transactions?.recovery_required ? capabilities.transactions.message || 'An interrupted rename requires recovery before semantic tools can continue.' :
      !language ? 'Semantic tools support Verilog, SystemVerilog, VHDL and Python source files.' :
      !state ? `No language service is configured for ${language}.` :
      !state.available ? `${language}: ${state.reason || 'language server unavailable'}` :
      `${language} · ${typeof state.server === 'string' ? state.server : state.server?.name || 'language server'} · UTF-16 positions`;
    for (const [operation, button] of buttons) {
      const reason = reasonFor(operation, context);
      button.disabled = !!reason;
      button.title = reason || LABELS[operation];
    }
    previewButton.disabled = !!reasonFor('rename', context);
    renameName.disabled = busy;
    retryButton.disabled = !context.online || !context.client || capabilityLoading || busy;
    undoButton.hidden = !undo;
    const recoveryRequired = !!capabilities?.transactions?.recovery_required;
    undoButton.disabled = !context.online || busy || context.workspaceBusy || anyDirty(context.model) || hasRecoveryDrafts(context.model) || !!context.jobRunning || recoveryRequired;
    undoButton.title = anyDirty(context.model) ? 'Save all files before undoing a rename.' : hasRecoveryDrafts(context.model) ? 'Restore or discard recovery drafts before undoing a rename.' : context.jobRunning ? 'Wait for the running job before undoing a rename.' : recoveryRequired ? 'Recover the interrupted rename first.' : 'Restore the last rename using revision checks.';
    recoverButton.hidden = !recoveryRequired;
    recoverButton.disabled = !context.online || busy || context.workspaceBusy || anyDirty(context.model) || hasRecoveryDrafts(context.model) || !!context.jobRunning;
    recoverButton.title = anyDirty(context.model) ? 'Save all files before recovering a rename.' : hasRecoveryDrafts(context.model) ? 'Restore or discard recovery drafts before recovering a rename.' : context.jobRunning ? 'Wait for the running job before recovering a rename.' : 'Restore the file revisions recorded before the interrupted transaction.';
    if (pendingPlan) {
      pendingPlan.button.disabled = !context.online || busy || context.workspaceBusy || anyDirty(context.model) || hasRecoveryDrafts(context.model) || !!context.jobRunning || recoveryRequired;
      pendingPlan.button.title = anyDirty(context.model) ? 'Save all files before applying the rename.' : hasRecoveryDrafts(context.model) ? 'Restore or discard recovery drafts before applying the rename.' : context.jobRunning ? 'Wait for the running job before applying the rename.' : recoveryRequired ? 'Recover the interrupted rename first.' : 'Apply the previewed rename to local files.';
    }
  }
  async function loadCapabilities() {
    const context = getContext();
    if (disposed || !context.online || !context.client || capabilityLoading) return;
    const snapshot = captureContext(context), request = ++capabilityGeneration;
    capabilityLoading = true;
    renderControls();
    try {
      const response = await context.client.request('/v1/editor/capabilities');
      if (disposed || request !== capabilityGeneration || !sameSession(snapshot, getContext())) return;
      if (!response || typeof response.languages !== 'object' || response.position_encoding !== 'utf-16') throw new Error('Invalid language service capabilities.');
      capabilities = response;
      capabilityError = '';
    } catch (error) {
      if (disposed || request !== capabilityGeneration || !sameSession(snapshot, getContext())) return;
      capabilityError = error.message || 'Unable to check language services.';
      report(capabilityError, true, error);
    } finally {
      if (request === capabilityGeneration) capabilityLoading = false;
      renderControls();
    }
  }
  async function navigate(location, snapshot) {
    if (snapshot && !sameSession(snapshot, getContext())) return;
    if (!location || typeof location.path !== 'string' || !location.range?.start) return;
    try { await onNavigate(location.path, location.range); }
    catch (error) { if (!snapshot || sameSession(snapshot, getContext())) report(error.message, true, error); }
  }
  function renderDiagnostics(groups, snapshot) {
    diagnostics.replaceChildren();
    let count = 0;
    for (const group of Array.isArray(groups) ? groups : []) {
      for (const item of (Array.isArray(group.items) ? group.items : []).slice(0, 100)) {
        if (++count > 100) break;
        const line = item.range?.start?.line;
        const label = `${group.path}${Number.isSafeInteger(line) ? ':' + (line + 1) : ''} · ${item.message}`;
        makeButton(label, () => navigate({path: group.path, range: item.range}, snapshot), diagnostics);
      }
      if (count > 100) break;
    }
    if (count) element('p', count > 100 ? 'Showing the first 100 language server diagnostics.' : `${count} language server diagnostic${count === 1 ? '' : 's'}.`, diagnostics);
  }
  function renderResponse(operation, response, snapshot) {
    const result = response.result || {};
    results.replaceChildren();
    renderDiagnostics(response.diagnostics, snapshot);
    if (operation === 'completion') {
      const items = Array.isArray(result.items) ? result.items.slice(0, 100) : [];
      for (const item of items) {
        let unsupported = '';
        try { plainCompletion(item); } catch (error) { unsupported = error.message; }
        const button = makeButton(`${item.label}${item.detail ? ' · ' + item.detail : ''}`, () => {
          if (!isContextCurrent(snapshot, getContext())) {
            report('Source or cursor changed. Request completions again.');
            results.replaceChildren();
            return;
          }
          try {
            const edit = applyCompletion(snapshot.content, item, {start: snapshot.start, end: snapshot.end});
            const editor = snapshot.editor;
            // Textarea values normalize line endings. Normalize the caret prefix
            // too when a language server inserts CRLF or CR text.
            editor.value = edit.content.replace(/\r\n?/g, '\n');
            const caret = edit.content.slice(0, edit.selectionStart).replace(/\r\n?/g, '\n').length;
            editor.setSelectionRange(caret, caret);
            editor.dispatchEvent(new (doc.defaultView?.Event || Event)('input', {bubbles: true}));
            editor.focus();
            results.replaceChildren();
            displayed = null;
            report('Completion inserted into the current draft. Save to write the file.');
            refresh();
          } catch (error) { report(error.message, true, error); }
        }, results);
        button.disabled = !!unsupported;
        button.title = unsupported || 'Insert this completion into the current draft';
      }
      report(!items.length ? 'No completions at this position.' : `${items.length} completion${items.length === 1 ? '' : 's'}${result.is_incomplete || result.truncated ? ' · narrow the prefix for more results' : ''}. Select one to insert it.`);
    } else if (operation === 'hover') {
      element('pre', result.text || 'No symbol information at this position.', results);
      report('Symbol information received.');
    } else if (operation === 'definition' || operation === 'references') {
      const locations = Array.isArray(result.locations) ? result.locations.slice(0, 200) : [];
      for (const location of locations) {
        const start = location.range?.start;
        makeButton(`${location.path}:${(start?.line ?? 0) + 1}:${(start?.character ?? 0) + 1}`, () => navigate(location, snapshot), results);
      }
      report(`${locations.length} ${operation === 'definition' ? 'definition' : 'reference'}${locations.length === 1 ? '' : 's'}${result.truncated ? ' · results truncated' : ''}.`);
    } else if (operation === 'rename') {
      const plan = response.plan;
      if (!plan || typeof plan.id !== 'string' || !Array.isArray(plan.files) || !plan.files.length ||
          plan.files.some(file => typeof file.path !== 'string' || typeof file.before !== 'string' || typeof file.after !== 'string')) {
        throw new Error('The companion returned an invalid rename preview.');
      }
      const edits = plan.files.reduce((sum, file) => sum + (file.edit_count || 0), 0);
      element('p', `${edits} edit${edits === 1 ? '' : 's'} in ${plan.files.length} file${plan.files.length === 1 ? '' : 's'}. Review every file before applying.`, results);
      for (const file of plan.files) {
        const detail = element('details', undefined, results);
        element('summary', file.path, detail);
        const columns = element('div', undefined, detail, {class: 'intelligence-diff'});
        const before = element('div', undefined, columns);
        element('h4', 'Before', before);
        element('pre', file.before, before);
        const after = element('div', undefined, columns);
        element('h4', 'After', after);
        element('pre', file.after, after);
      }
      const applyButton = makeButton('Apply rename to local files', () => mutate('apply'), results);
      pendingPlan = {id: plan.id, snapshot, button: applyButton};
      report(`Rename preview ready${Number.isFinite(plan.expires_in) ? ' · expires in ' + plan.expires_in + ' seconds' : ''}. No files have been changed.`);
      renameForm.hidden = true;
    }
  }
  async function query(operation) {
    if (disposed || !OPERATIONS.includes(operation)) return;
    const context = getContext();
    const reason = reasonFor(operation, context);
    if (reason) { report(reason, true); return; }
    if (operation === 'rename' && !renameName.value.trim()) {
      renameForm.hidden = false;
      renameName.focus();
      report('Enter the new symbol name before previewing the rename.');
      return;
    }
    const snapshot = captureContext(context), request = ++generation;
    const body = {operation, path: context.path, position: positionAt(snapshot.content, snapshot.start), documents: snapshot.documents};
    if (operation === 'rename') body.new_name = renameName.value.trim();
    busy = true;
    pendingPlan = null;
    displayed = null;
    results.replaceChildren();
    diagnostics.replaceChildren();
    report(`Requesting ${operation}…`);
    renderControls();
    try {
      const response = await context.client.request('/v1/editor/query', {method: 'POST', body});
      if (disposed || request !== generation || !sameSession(snapshot, getContext())) return;
      if (!isContextCurrent(snapshot, getContext())) { report('Source or cursor changed. Request semantic results again.'); return; }
      if (!response || response.operation !== operation || response.path !== snapshot.path) throw new Error('The companion returned results for a different editor request.');
      const document = snapshot.documents.find(document => document.path === snapshot.path);
      if (response.version !== undefined && response.version !== document?.version) throw new Error('The companion returned results for a different source version.');
      renderResponse(operation, response, snapshot);
      displayed = {snapshot, operation};
    } catch (error) {
      if (!disposed && request === generation && sameSession(snapshot, getContext())) report(error.message || 'Editor request failed.', true, error);
    } finally {
      if (request === generation) busy = false;
      renderControls();
    }
  }
  async function mutate(operation) {
    const context = getContext();
    if (context.workspaceBusy || context.model?.structureBusy) return;
    const item = operation === 'apply' ? pendingPlan : operation === 'undo' ? undo :
      capabilities?.transactions?.recovery_required ? {snapshot: captureContext(context)} : null;
    if (disposed || !item || busy) return;
    if (!sameSession(item.snapshot, context)) { report('This rename belongs to a closed workspace session.', true); return; }
    if (anyDirty(context.model)) { report('Save all files before changing a rename on disk.', true); return; }
    if (hasRecoveryDrafts(context.model)) { report('Restore or discard recovery drafts before changing a rename on disk.', true); return; }
    if (context.jobRunning) { report('Wait for the running job before changing a rename on disk.', true); return; }
    if (operation !== 'recover' && capabilities?.transactions?.recovery_required) { report('Recover the interrupted rename before applying or undoing a rename.', true); return; }
    const request = ++generation;
    busy = true;
    onBusy(true);
    renderControls();
    report(operation === 'apply' ? 'Applying rename with revision checks…' : operation === 'undo' ? 'Undoing rename with revision checks…' : 'Recovering the interrupted rename with revision checks…');
    try {
      const response = await context.client.request('/v1/editor/' + operation, {method: 'POST', body: operation === 'recover' ? {} : {id: item.id}});
      if (disposed || request !== generation || !sameSession(item.snapshot, getContext())) return;
      // The transaction already changed disk. Preserve Undo even if a subsequent
      // model refresh fails, and never offer the already-consumed plan again.
      undo = operation === 'apply' && typeof response.undo_id === 'string' ? {id: response.undo_id, snapshot: item.snapshot} : null;
      pendingPlan = null;
      displayed = null;
      results.replaceChildren();
      diagnostics.replaceChildren();
      if (operation === 'apply') await onApply(response);
      else if (operation === 'undo') await onUndo(response);
      else await onRecover(response);
      if (disposed || request !== generation || !sameSession(item.snapshot, getContext())) return;
      if (undo) undo.snapshot = captureContext(getContext());
      if (capabilities?.transactions) capabilities.transactions.recovery_required = false;
      report(operation === 'apply' ? 'Rename applied to local files. Undo is available while the file revisions still match.' : operation === 'undo' ? 'Rename undone. Local files were restored with revision checks.' : 'Interrupted rename recovered. Local files were restored with revision checks.');
    } catch (error) {
      if (!disposed && request === generation && sameSession(item.snapshot, getContext())) {
        report(error.message || 'Rename could not be changed.', true, error);
        // A failed disk transaction may have installed a recovery journal.
        capabilities = null;
        capabilityError = '';
        await loadCapabilities();
      }
    } finally {
      if (request === generation) busy = false;
      const current = getContext();
      if (item.snapshot.client === current.client && item.snapshot.model === current.model && item.snapshot.sessionId === current.sessionId) onBusy(false);
      renderControls();
    }
  }
  function refresh() {
    if (disposed) return;
    const context = getContext();
    if (context.online && !wasOnline) {
      capabilities = null;
      capabilityError = '';
    }
    wasOnline = !!context.online;
    if (!owner || owner.model !== context.model || owner.client !== context.client || owner.sessionId !== context.sessionId) {
      owner = {model: context.model, client: context.client, sessionId: context.sessionId};
      generation++;
      capabilityGeneration++;
      capabilities = null;
      capabilityError = '';
      capabilityLoading = false;
      busy = false;
      pendingPlan = null;
      undo = null;
      displayed = null;
      results.replaceChildren();
      diagnostics.replaceChildren();
      renameForm.hidden = true;
      renameName.value = '';
      status.textContent = 'Open a Verilog, SystemVerilog, VHDL or Python file to inspect symbols.';
      status.classList.toggle('error', false);
    }
    if (displayed && displayed.operation === 'completion' && !isContextCurrent(displayed.snapshot, context)) {
      displayed = null;
      results.replaceChildren();
      diagnostics.replaceChildren();
    }
    renderControls();
    if (context.online && context.client && !capabilities && !capabilityError && !capabilityLoading) void loadCapabilities();
  }
  refresh();
  return {refresh, query, dispose() {
    disposed = true;
    generation++;
    capabilityGeneration++;
    root.remove();
  }};
}
