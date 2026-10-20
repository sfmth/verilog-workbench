/** Explicit, companion-backed file management. Source and credentials stay local. */
export function mountFileManager(dialog, {getContext, onBusy, onChanged, onStatus}) {
  const doc = dialog.ownerDocument;
  const make = (tag, text, parent, attributes = {}) => {
    const value = doc.createElement(tag);
    if (text !== undefined) value.textContent = text;
    for (const [key, content] of Object.entries(attributes)) value.setAttribute(key, content);
    parent?.append(value);
    return value;
  };
  dialog.replaceChildren();
  const heading = make('div', undefined, dialog, {class: 'section-heading'});
  make('h2', 'Project files', heading);
  const close = make('button', '×', heading, {type: 'button', 'aria-label': 'Close file manager'});
  make('p', 'Create files and folders, move them, or restore items from project trash.', dialog, {class: 'muted'});
  const status = make('p', 'Choose an action.', dialog, {role: 'status', 'aria-live': 'polite', id: 'files-status'});
  const availability = make('p', '', dialog, {class: 'muted', 'aria-live': 'polite'});
  const createSection = make('section', undefined, dialog, {class: 'files-section'});
  make('h3', 'Create', createSection);
  const createForm = make('form', undefined, createSection);
  const createLabel = make('label', 'Project-relative path', createForm);
  const createPath = make('input', undefined, createLabel, {type: 'text', 'aria-label': 'New path', autocomplete: 'off', spellcheck: 'false', required: ''});
  const createActions = make('div', undefined, createForm, {class: 'actions'});
  const createFile = make('button', 'Create file', createActions, {type: 'button'});
  const createFolder = make('button', 'Create folder', createActions, {type: 'button'});
  const manageSection = make('section', undefined, dialog, {class: 'files-section'});
  make('h3', 'Rename, move or remove', manageSection);
  const entryLabel = make('label', 'File or folder', manageSection);
  const entries = make('select', undefined, entryLabel, {'aria-label': 'File or folder'});
  const details = make('p', 'Choose an item to check its current disk revision.', manageSection, {class: 'muted'});
  const reload = make('button', 'Reload entry', manageSection, {type: 'button'});
  const moveLabel = make('label', 'Destination path', manageSection);
  const destination = make('input', undefined, moveLabel, {type: 'text', 'aria-label': 'Destination path', autocomplete: 'off', spellcheck: 'false'});
  make('p', 'Moving a file does not update HDL includes or Python imports.', manageSection, {class: 'muted'});
  const moveActions = make('div', undefined, manageSection, {class: 'actions'});
  const move = make('button', 'Rename / move', moveActions, {type: 'button'});
  const remove = make('button', 'Move to trash', moveActions, {type: 'button', class: 'danger'});
  const trashSection = make('section', undefined, dialog, {class: 'files-section'});
  const trashHeading = make('div', undefined, trashSection, {class: 'section-heading'});
  make('h3', 'Project trash', trashHeading);
  const refreshTrash = make('button', 'Refresh trash', trashHeading, {type: 'button'});
  make('p', 'Items stay on this machine and can be restored to their original, unoccupied paths.', trashSection, {class: 'muted'});
  const trash = make('div', undefined, trashSection, {class: 'files-trash'});
  let busy = false, loading = false, disposed = false, snapshot = null, owner = null;
  let generation = 0, trashGeneration = 0, selected = '', renderedInventory = null;
  let preferredKind = 'file';
  const restoreButtons = [];
  // A stale reply still owns a model read until its promise settles. Keep all
  // reads for each model counted across dialog close/reopen and session changes.
  const pendingReads = new Map();
  const same = previous => {
    const context = getContext();
    return !disposed && previous.model === context.model && previous.client === context.client && previous.sessionId === context.sessionId;
  };
  function message(text, error = false, cause) {
    status.textContent = text;
    status.classList.toggle('error', error);
    if (error) onStatus(text, true, cause);
  }
  function reason() {
    const context = getContext();
    if (!context.online || !context.model) return 'Connect a companion to manage project files.';
    if (busy || context.jobRunning) return 'Wait for the active job or file operation.';
    if ([...context.model.buffers.values()].some(buffer => buffer.dirty || buffer.saving || buffer.conflict)) return 'Save or reload all files before changing project paths.';
    if (context.model.recoverable.length) return 'Restore or discard recovery drafts before changing project paths.';
    if (pendingReads.get(context.model)) return 'Wait for pending file and project trash checks before changing project files.';
    return '';
  }
  function controls() {
    const blocked = reason();
    for (const control of [createFile, createFolder, move, remove, ...restoreButtons]) {
      control.disabled = !!blocked || loading || ((control === move || control === remove) && !snapshot);
      control.title = blocked || (loading ? 'Checking the disk entry…' : '');
    }
    for (const control of [entries, destination, createPath, close]) control.disabled = busy;
    reload.disabled = busy || !getContext().online || !selected || loading;
    refreshTrash.disabled = busy || !getContext().online || !!pendingReads.get(getContext().model);
    availability.textContent = blocked;
  }
  async function read(context, action) {
    pendingReads.set(context.model, (pendingReads.get(context.model) || 0) + 1);
    controls();
    try { return await action(); }
    finally {
      const remaining = pendingReads.get(context.model) - 1;
      if (remaining) pendingReads.set(context.model, remaining);
      else pendingReads.delete(context.model);
      controls();
    }
  }
  async function inspect() {
    const context = getContext(), request = ++generation;
    snapshot = null;
    if (!context.online || !selected) { loading = false; details.textContent = 'Choose a file or folder.'; controls(); return; }
    loading = true; controls();
    try {
      const entry = await read(context, () => context.model.entry(selected));
      if (!same(context) || request !== generation || entry.path !== selected) return;
      snapshot = entry;
      details.textContent = `${entry.kind === 'directory' ? 'Folder' : 'File'} · ${entry.file_count} file${entry.file_count === 1 ? '' : 's'} · ${entry.bytes} bytes`;
      message('Disk revision loaded. External changes will stop a move or removal.');
    } catch (error) {
      if (same(context) && request === generation) message(error.message, true, error);
    } finally {
      if (request === generation) loading = false;
      controls();
    }
  }
  function renderInventory(path = selected) {
    const model = getContext().model;
    const choices = [...(model?.directories || []).map(value => ({...value, directory: true})), ...(model?.files || [])]
      .sort((a, b) => a.path.localeCompare(b.path));
    entries.replaceChildren();
    make('option', 'Choose a file or folder', entries, {value: ''});
    for (const entry of choices) make('option', entry.path + (entry.directory ? '/' : ''), entries, {value: entry.path});
    selected = choices.some(entry => entry.path === path) ? path : '';
    entries.value = selected;
    renderedInventory = model?.inventory;
  }
  async function loadTrash() {
    const context = getContext(), request = ++trashGeneration;
    if (!context.online || !context.model) return;
    try {
      const result = await read(context, () => context.model.trashEntries());
      if (!same(context) || request !== trashGeneration) return;
      trash.replaceChildren(); restoreButtons.length = 0;
      for (const entry of result) {
        const row = make('div', undefined, trash, {class: 'files-trash-row'});
        make('span', entry.path || 'Unverified trash entry', row);
        if (entry.error) make('p', entry.error, row, {class: 'error'});
        if (entry.restorable === false) continue;
        const restore = make('button', 'Restore ' + entry.path, row, {type: 'button'});
        restoreButtons.push(restore);
        restore.addEventListener('click', () => mutate('restore', () => context.model.restoreEntry(entry.id)));
      }
      if (!result.length) make('p', 'Project trash is empty.', trash, {class: 'muted'});
      controls();
    } catch (error) { if (same(context) && request === trashGeneration) message(error.message, true, error); }
  }
  async function mutate(operation, action) {
    const blocked = reason();
    if (blocked || loading) { message(blocked || 'Wait for the disk entry check.', true); return; }
    const context = getContext();
    let result, failure;
    busy = true; onBusy(true); controls(); message('Updating local project files…');
    try { result = await action(); }
    catch (error) { failure = error; if (error.applied) result = error.result; }
    finally { busy = false; onBusy(false); controls(); }
    if (!same(context)) return;
    if (result) {
      snapshot = null;
      renderInventory(operation === 'trash' ? '' : result.path);
      try { await onChanged({operation, entry: result}); }
      catch (error) { error.applied = true; failure = error; }
      if (operation === 'create' && result.kind === 'file') dialog.close();
    } else if (failure) {
      // A failed durability acknowledgement can follow a native rename. Re-read
      // inventory without repeating the mutation; existing buffers keep their revisions.
      try { await context.model.connect(); renderInventory(); } catch {}
    }
    if (!same(context)) return;
    if (failure) message((failure.applied ? 'The file change succeeded, but opening or refreshing the project failed. ' : '') + failure.message, true, failure);
    else message(operation === 'trash' ? 'Moved to project trash. Use Restore to bring it back.' : operation === 'restore' ? 'Restored to the original path.' : 'Project files updated.');
    await loadTrash(); controls();
  }
  entries.addEventListener('change', () => { selected = entries.value; destination.value = selected; void inspect(); });
  reload.addEventListener('click', () => void inspect());
  refreshTrash.addEventListener('click', () => void loadTrash());
  createForm.addEventListener('submit', event => { event.preventDefault(); void mutate('create', () => getContext().model.createEntry(createPath.value.trim(), preferredKind)); });
  createFile.addEventListener('click', () => void mutate('create', () => getContext().model.createEntry(createPath.value.trim(), 'file')));
  createFolder.addEventListener('click', () => void mutate('create', () => getContext().model.createEntry(createPath.value.trim(), 'directory')));
  move.addEventListener('click', () => {
    const entry = snapshot;
    if (entry) void mutate('move', () => getContext().model.moveEntry(entry.path, destination.value.trim(), entry.revision));
  });
  remove.addEventListener('click', () => {
    const entry = snapshot;
    if (entry && !reason() && doc.defaultView.confirm(`Move ${entry.path} (${entry.file_count} files) to project trash?`)) {
      void mutate('trash', () => getContext().model.trashEntry(entry.path, entry.revision));
    }
  });
  close.addEventListener('click', () => { if (!busy) dialog.close(); });
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  function refresh() {
    if (disposed) return;
    const context = getContext();
    if (!owner || !same(owner)) {
      owner = context; generation++; trashGeneration++; snapshot = null; selected = ''; loading = false;
      trash.replaceChildren(); restoreButtons.length = 0; renderedInventory = null;
      if (dialog.open && !busy) dialog.close();
    }
    if (renderedInventory !== context.model?.inventory) renderInventory();
    controls();
  }
  refresh();
  return {refresh, open(mode = 'manage', path = getContext().path) {
    refresh();
    const context = getContext();
    if (!context.online || !context.model || busy) return;
    renderInventory(path || selected);
    destination.value = selected;
    const directory = mode === 'folder' || mode === 'file';
    preferredKind = mode === 'folder' ? 'directory' : 'file';
    createFile.classList.toggle('primary', preferredKind === 'file');
    createFolder.classList.toggle('primary', preferredKind === 'directory');
    createPath.value = path?.includes('/') ? path.slice(0, path.lastIndexOf('/') + 1) : '';
    snapshot = null; message(reason() || 'Choose an action.');
    if (!dialog.open) dialog.showModal();
    if (directory) createPath.focus();
    else if (mode === 'trash') refreshTrash.focus();
    else entries.focus();
    void inspect(); void loadTrash();
  }, dispose() { disposed = true; generation++; trashGeneration++; dialog.close(); }};
}
