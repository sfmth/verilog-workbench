const SCHEMA = 'vwb.drafts/1';
// JSON may expand every source byte to a six-byte escape, plus bounded file metadata.
// Validate exact source sizes after parsing; this outer bound also accepts our exports.
export const MAX_DRAFT_IMPORT_BYTES = 512 * 1024 * 1024;
const MAX_FILE_BYTES = 2 * 1024 * 1024;
const MAX_TOTAL_BYTES = 64 * 1024 * 1024;
const MAX_FILES = 10000;
const encoder = new TextEncoder();
const reserved = /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i;

function pathValue(path) {
  if (typeof path !== 'string' || !path || path.length > 1024 ||
      /[\\:\u0000-\u001f\u007f]/.test(path) || path.startsWith('/') ||
      path.split('/').some(part => !part || part === '.' || part === '..' ||
        /[. ]$/.test(part) || reserved.test(part) || part.toLowerCase() === '.git')) {
    throw new Error('Use a portable project-relative file path.');
  }
  return path;
}

function textBytes(content) {
  if (typeof content !== 'string') throw new Error('Draft content must be text.');
  // TextEncoder silently replaces unpaired surrogates, unlike the companion.
  for (let i = 0; i < content.length; i++) {
    const code = content.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = content.charCodeAt(++i);
      if (!(next >= 0xdc00 && next <= 0xdfff)) throw new Error('Draft content must be valid UTF-8 text.');
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      throw new Error('Draft content must be valid UTF-8 text.');
    }
  }
  const bytes = encoder.encode(content).length;
  if (bytes > MAX_FILE_BYTES) throw new Error('A draft exceeds the 2 MiB file limit.');
  return bytes;
}

function revisionValue(revision) {
  if (revision !== null && (typeof revision !== 'string' || !/^[0-9a-f]{64}$/.test(revision))) {
    throw new Error('Draft revision must be a SHA-256 revision or null.');
  }
  return revision;
}

const normalized = content => content.replace(/\r\n?|\n/g, '\n');
const preferredNewline = content => content.match(/\r\n|\r|\n/)?.[0] || '\n';

function validateDrafts(data) {
  if (!data || typeof data !== 'object' || data.schema !== SCHEMA || !Array.isArray(data.files)) {
    throw new Error('Choose a vwb.drafts/1 recovery export.');
  }
  if (data.files.length > MAX_FILES) throw new Error('Recovery export exceeds 10,000 files.');
  const seen = new Set();
  let total = 0;
  return data.files.map(file => {
    if (!file || typeof file !== 'object' || Array.isArray(file)) throw new Error('Invalid draft file.');
    const path = pathValue(file.path);
    if (seen.has(path)) throw new Error('Recovery export contains duplicate file paths.');
    seen.add(path);
    total += textBytes(file.content);
    if (total > MAX_TOTAL_BYTES) throw new Error('Recovery export exceeds the 64 MiB total limit.');
    const revision = revisionValue(file.revision);
    if (!['\n', '\r\n', '\r'].includes(file.newline)) throw new Error('Invalid draft newline format.');
    return {path, content: file.content, revision, newline: file.newline};
  });
}

function bufferFrom(file) {
  pathValue(file.path);
  textBytes(file.content);
  revisionValue(file.revision);
  return {
    path: file.path, content: normalized(file.content), revision: file.revision,
    diskRevision: file.revision, newline: preferredNewline(file.content),
    dirty: false, conflict: false, saving: false, error: null,
    _exactContent: file.content, _savedContent: file.content, _version: 0,
  };
}

const containsPath = (parent, path) => path === parent || path.startsWith(parent + '/');

function entryValue(entry, {path = null, kind = null, trash = false} = {}) {
  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) throw new Error('Invalid companion project entry.');
  pathValue(entry.path);
  if (!['file', 'directory'].includes(entry.kind) || entry.revision === null ||
      !Number.isSafeInteger(entry.file_count) || entry.file_count < 0 ||
      !Number.isSafeInteger(entry.bytes) || entry.bytes < 0) throw new Error('Invalid companion project entry.');
  revisionValue(entry.revision);
  if ((path !== null && entry.path !== path) || (kind !== null && entry.kind !== kind)) {
    throw new Error('The companion returned a different project entry.');
  }
  if (trash) trashIdValue(entry.id);
  return entry;
}

function trashIdValue(id) {
  if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(id)) throw new Error('Invalid project trash ID.');
  return id;
}

/** In-memory editor buffers. Only explicit, project-scoped recovery stores source text. */
export class WorkspaceModel {
  constructor(client, {storage = null, storageKey = null} = {}) {
    if (!client || typeof client.request !== 'function') throw new Error('A companion client is required.');
    if (storageKey !== null && (typeof storageKey !== 'string' || !storageKey || storageKey.length > 2048)) {
      throw new Error('Recovery requires an explicit project-scoped storage key.');
    }
    this.client = client;
    this.project = null;
    this.files = [];
    this.directories = [];
    this.inventory = null;
    this.buffers = new Map();
    this.storage = null;
    this.storageKey = storageKey;
    this.storageError = null;
    this._recovery = new Map();
    this._opening = new Map();
    this._saving = new Map();
    this._reading = 0;
    this._structureBusy = false;
    this.lastStructureChange = null;
    this.structureError = null;
    this._disposed = false;
    if (storage) this.setStorage(storage);
  }

  _active() {
    if (this._disposed) throw new Error('This workspace session has been closed.');
  }

  _available() {
    this._active();
    if (this._structureBusy) throw new Error('Wait for the current project file operation to finish.');
  }

  get structureBusy() { return this._structureBusy; }

  async _read(action) {
    this._reading++;
    try { return await action(); } finally { this._reading--; }
  }

  async _loadInventory() {
    const inventory = await this.client.request('/v1/project/files');
    this._active();
    if (!inventory || !Array.isArray(inventory.files) ||
        (inventory.directories !== undefined && !Array.isArray(inventory.directories))) {
      throw new Error('Invalid companion file inventory.');
    }
    this.files = inventory.files;
    this.directories = inventory.directories ?? [];
    this.inventory = inventory;
    return inventory;
  }

  async connect() {
    this._available();
    return this._read(() => this._loadInventory());
  }

  async open(path) {
    this._available();
    pathValue(path);
    if (this.buffers.has(path)) return this.buffers.get(path);
    if (this._opening.has(path)) return this._opening.get(path);
    const pending = (async () => {
      const file = await this.client.request('/v1/project/file?path=' + encodeURIComponent(path));
      this._active();
      if (file.path !== path) throw new Error('The companion returned a different file path.');
      // Explicit recovery may have installed a buffer during this disk read.
      if (this.buffers.has(path)) return this.buffers.get(path);
      const buffer = bufferFrom(file);
      this.buffers.set(path, buffer);
      return buffer;
    })();
    this._opening.set(path, pending);
    try { return await pending; } finally { this._opening.delete(path); }
  }

  edit(path, content) {
    this._available();
    const buffer = this.buffers.get(path);
    if (!buffer) throw new Error('Open the file before editing it.');
    textBytes(content);
    const next = normalized(content);
    if (next === buffer.content) return buffer;
    const savedText = normalized(buffer._savedContent);
    const exact = next === savedText ? buffer._savedContent : next.replace(/\n/g, buffer.newline);
    textBytes(exact);
    buffer.content = next;
    buffer._exactContent = exact;
    buffer._version++;
    buffer.dirty = exact !== buffer._savedContent || buffer.revision !== buffer.diskRevision || buffer.conflict;
    buffer.error = null;
    this._persist();
    return buffer;
  }

  async reload(path) {
    this._available();
    pathValue(path);
    const previous = this.buffers.get(path);
    if (!previous) throw new Error('Open the file before reloading it.');
    if (previous.saving || this._saving.has(path)) throw new Error('Wait for the current save before reloading this file.');
    const version = previous._version;
    const revision = previous.revision;
    const diskRevision = previous.diskRevision;
    const savedContent = previous._savedContent;
    const recovery = this._recovery.get(path);
    return this._read(async () => {
      const file = await this.client.request('/v1/project/file?path=' + encodeURIComponent(path));
      this._active();
      if (!file || file.path !== path || file.revision === null) {
        throw new Error('The companion returned an invalid disk file.');
      }
      const next = bufferFrom(file);
      if (this.buffers.get(path) !== previous || previous._version !== version ||
          previous.revision !== revision || previous.diskRevision !== diskRevision || previous._savedContent !== savedContent ||
          previous.saving || this._saving.has(path) || this._recovery.get(path) !== recovery) {
        throw new Error('The buffer or recovery draft changed while the disk file was being read.');
      }
      // Replacing source is deliberate only after a validated, successful read.
      this.buffers.set(path, next);
      this._recovery.delete(path);
      this._persist();
      return next;
    });
  }

  close(path) {
    this._available();
    pathValue(path);
    if (this.buffers.get(path)?.saving || this._saving.has(path)) {
      throw new Error('Wait for the current save before closing this file.');
    }
    this.buffers.delete(path);
    this._recovery.delete(path);
    this._persist();
  }

  async save(path) {
    this._available();
    const buffer = this.buffers.get(path);
    if (!buffer) throw new Error('Open the file before saving it.');
    if (this._saving.has(path)) return this._saving.get(path);
    if (!buffer.dirty) return buffer;
    const snapshot = {path, content: buffer._exactContent, revision: buffer.revision};
    buffer.saving = true;
    buffer.error = null;
    const pending = (async () => {
      try {
        const saved = await this.client.request('/v1/project/file', {method: 'PUT', body: snapshot});
        if (saved.path !== path) throw new Error('The companion returned a different saved file path.');
        revisionValue(saved.revision);
        buffer.revision = saved.revision;
        buffer.diskRevision = saved.revision;
        buffer._savedContent = snapshot.content;
        // Edits made during the PUT keep their exact text and remain dirty.
        buffer.dirty = buffer._exactContent !== snapshot.content;
        buffer.conflict = false;
        return buffer;
      } catch (error) {
        buffer.error = error;
        buffer.conflict = error.status === 409;
        if (buffer.conflict) buffer.dirty = true;
        throw error;
      } finally {
        buffer.saving = false;
        this._persist();
      }
    })();
    this._saving.set(path, pending);
    try { return await pending; } finally { this._saving.delete(path); }
  }

  async saveAll() {
    this._available();
    const dirty = [...this.buffers.values()].filter(buffer => buffer.dirty);
    // Continue independent saves after a conflict; report all failures to the caller.
    const results = await Promise.allSettled(dirty.map(buffer => this.save(buffer.path)));
    const errors = results.filter(result => result.status === 'rejected').map(result => result.reason);
    if (errors.length) throw new AggregateError(errors, 'Some files could not be saved; their drafts are retained.');
    return results.map(result => result.value);
  }

  editorDocuments() {
    this._active();
    return [...this.buffers.values()].map(buffer => ({
      path: buffer.path, content: buffer._exactContent, revision: buffer.revision,
      version: buffer._version + 1,
    }));
  }

  acceptEditorFiles(files) {
    this._available();
    if (!Array.isArray(files) || !files.length || files.length > 32) throw new Error('Invalid editor transaction files.');
    const replacements = new Map();
    for (const file of files) {
      const next = bufferFrom(file);
      if (file.revision === null || replacements.has(file.path)) throw new Error('Invalid editor transaction revision or duplicate file.');
      const previous = this.buffers.get(file.path);
      if (previous?.dirty || previous?.saving || this._recovery.has(file.path)) {
        throw new Error('The disk edit completed, but a draft changed. Retain/export the draft and reload before continuing.');
      }
      next._version = (previous?._version ?? -1) + 1;
      replacements.set(file.path, next);
    }
    for (const [path, next] of replacements) {
      // A disk read started before the transaction must not install old bytes.
      if (this.buffers.has(path) || this._opening.has(path)) this.buffers.set(path, next);
    }
    this._persist();
    return files;
  }

  async entry(path) {
    this._available();
    pathValue(path);
    return this._read(async () => {
      const entry = await this.client.request('/v1/project/entry?path=' + encodeURIComponent(path));
      this._active();
      return entryValue(entry, {path});
    });
  }

  async trashEntries() {
    this._available();
    return this._read(async () => {
      const result = await this.client.request('/v1/project/trash');
      this._active();
      if (!result || !Array.isArray(result.entries) || result.entries.length > MAX_FILES) throw new Error('Invalid companion project trash inventory.');
      const seen = new Set();
      for (const entry of result.entries) {
        if (entry?.state === 'invalid') {
          // Unverified native entries intentionally have no trusted source path
          // or revision. Keep them visible beside healthy restore candidates;
          // their opaque display ID must never become a restore authorization.
          if (entry.restorable !== false || typeof entry.id !== 'string' || !entry.id || entry.id.length > 1024 ||
              typeof entry.error !== 'string' || entry.error.length > 4096) {
            throw new Error('Invalid companion project trash warning.');
          }
        } else entryValue(entry, {trash: true});
        if (seen.has(entry.id)) throw new Error('Duplicate companion project trash ID.');
        seen.add(entry.id);
      }
      return result.entries;
    });
  }

  _structureReady() {
    this._available();
    if (this._opening.size || this._reading || this._saving.size || [...this.buffers.values()].some(buffer => buffer.saving)) {
      throw new Error('Wait for pending file reads and saves before changing project files.');
    }
    if (this._recovery.size || [...this.buffers.values()].some(buffer => buffer.dirty || buffer.conflict)) {
      throw new Error('Save all buffers and resolve recovery drafts before changing project files.');
    }
  }

  _structureDocuments(path) {
    const documents = [...this.buffers.values()].filter(buffer => containsPath(path, buffer.path))
      .map(buffer => ({path: buffer.path, revision: buffer.revision}));
    if (documents.length > 64) throw new Error('Close some affected files before changing a subtree with more than 64 open buffers.');
    if (documents.some(file => file.revision === null)) throw new Error('Reload the affected files from disk before changing their paths.');
    return documents;
  }

  async _changeStructure(operation, body, validate, install) {
    this._structureReady();
    this._structureBusy = true;
    this.structureError = null;
    try {
      const result = validate(await this.client.request('/v1/project/' + operation, {method: 'POST', body}));
      this._active();
      // The disk operation has committed. Install the matching buffer paths before
      // attempting any fallible inventory refresh, and never retry its POST.
      install(result);
      this.lastStructureChange = {operation, entry: result};
      if (body.path) this.lastStructureChange.previous_path = body.path;
      this._persist();
      try {
        await this._loadInventory();
      } catch (error) {
        error.applied = true;
        error.result = result;
        error.operation = operation;
        throw error;
      }
      return result;
    } catch (error) {
      this.structureError = error;
      throw error;
    } finally {
      this._structureBusy = false;
    }
  }

  async createEntry(path, kind, content = '') {
    this._structureReady();
    pathValue(path);
    if (!['file', 'directory'].includes(kind)) throw new Error('Choose a file or directory.');
    textBytes(content);
    if (kind === 'directory' && content !== '') throw new Error('A new directory cannot have file content.');
    const body = kind === 'file' ? {path, kind, content} : {path, kind};
    return this._changeStructure('create', body, result => entryValue(result, {path, kind}), () => {
      // A clean buffer whose previous disk file disappeared must not hide newly
      // created bytes. The next explicit open will read the new file.
      this.buffers.delete(path);
    });
  }

  async moveEntry(path, destination, revision) {
    this._structureReady();
    pathValue(path); pathValue(destination);
    if (revision === null) throw new Error('Review the current project entry before moving it.');
    revisionValue(revision);
    if (containsPath(path, destination) || containsPath(destination, path)) {
      throw new Error('Choose a separate destination outside the source subtree.');
    }
    if ([...this.buffers.keys()].some(open => containsPath(destination, open))) {
      throw new Error('Close files already open at the destination before moving this entry.');
    }
    const documents = this._structureDocuments(path);
    return this._changeStructure('move', {path, destination, revision, documents}, result => {
      entryValue(result, {path: destination});
      if (result.previous_path !== undefined && result.previous_path !== path) throw new Error('The companion returned a different source path.');
      return result;
    }, () => {
      const next = new Map();
      for (const [open, buffer] of this.buffers) {
        if (containsPath(path, open)) {
          const moved = destination + open.slice(path.length);
          buffer.path = moved;
          buffer._version++;
          next.set(moved, buffer);
        } else next.set(open, buffer);
      }
      this.buffers = next;
    });
  }

  async trashEntry(path, revision) {
    this._structureReady();
    pathValue(path);
    if (revision === null) throw new Error('Review the current project entry before moving it to trash.');
    revisionValue(revision);
    const documents = this._structureDocuments(path);
    return this._changeStructure('trash', {path, revision, documents}, result => entryValue(result, {path, trash: true}), () => {
      for (const open of this.buffers.keys()) if (containsPath(path, open)) this.buffers.delete(open);
    });
  }

  async restoreEntry(id) {
    this._structureReady();
    trashIdValue(id);
    return this._changeStructure('restore', {id}, result => entryValue(result), result => {
      // Restore returns the original path. Drop any clean, externally deleted
      // buffer at that path so its next open reads the restored disk bytes.
      for (const open of this.buffers.keys()) if (containsPath(result.path, open)) this.buffers.delete(open);
    });
  }

  get recoverable() {
    return [...this._recovery.values()].map(file => ({...file}));
  }

  exportDrafts() {
    const files = new Map(this._recovery);
    for (const buffer of this.buffers.values()) {
      if (buffer.dirty) files.set(buffer.path, {
        path: buffer.path, content: buffer._exactContent, revision: buffer.revision, newline: buffer.newline,
      });
    }
    const result = {schema: SCHEMA, files: [...files.values()].map(file => ({...file}))};
    validateDrafts(result);
    return result;
  }

  importDrafts(data) {
    this._available();
    const files = validateDrafts(data);
    if (files.some(file => this.buffers.get(file.path)?.dirty)) {
      throw new Error('Save or export the existing unsaved buffer before importing a draft for the same file.');
    }
    for (const file of files) {
      const previous = this._recovery.get(file.path);
      if (previous && (previous.content !== file.content || previous.revision !== file.revision || previous.newline !== file.newline)) {
        throw new Error('A different recovery draft already exists for ' + file.path + '. Export or discard it before importing a replacement.');
      }
    }
    const staged = new Map(this._recovery);
    for (const file of files) staged.set(file.path, file);
    validateDrafts({schema: SCHEMA, files: [...staged.values()]});
    for (const file of files) this._recovery.set(file.path, file);
    this._persist();
    return this.recoverable;
  }

  async recover(path) {
    this._available();
    const draft = this._recovery.get(path);
    if (!draft) throw new Error('No recovery draft exists for this file.');
    if (this.buffers.get(path)?.dirty) throw new Error('Save or export the current buffer before restoring another draft.');
    return this._read(async () => {
      let disk;
      try {
        disk = await this.client.request('/v1/project/file?path=' + encodeURIComponent(path));
      } catch (error) {
        if (error.status !== 404) throw error;
        disk = {path, content: '', revision: null};
      }
      this._active();
      if (disk.path !== path) throw new Error('The companion returned a different file path.');
      // Another action may have edited or replaced this draft while the disk read ran.
      if (this._recovery.get(path) !== draft || this.buffers.get(path)?.dirty) {
        throw new Error('The buffer or recovery draft changed while the disk file was being read.');
      }
      const buffer = bufferFrom(disk);
      buffer.content = normalized(draft.content);
      buffer._exactContent = draft.content;
      buffer.revision = draft.revision;
      buffer.newline = draft.newline;
      buffer.dirty = draft.content !== disk.content || draft.revision !== disk.revision;
      buffer.conflict = draft.revision !== disk.revision;
      this.buffers.set(path, buffer);
      this._recovery.delete(path);
      this._persist();
      return buffer;
    });
  }

  discardRecovery(path) {
    this._available();
    this._recovery.delete(path);
    this._persist();
  }

  setStorage(storage) {
    this._available();
    if (storage && !this.storageKey) throw new Error('Recovery requires an explicit project-scoped storage key.');
    const previous = this.storage;
    if (!storage) {
      this.storage = null;
      this.storageError = null;
      try { previous?.removeItem(this.storageKey); } catch (error) { this.storageError = error.message; }
      return;
    }
    this.storageError = null;
    let staged;
    try {
      const saved = storage.getItem(this.storageKey);
      staged = new Map(this._recovery);
      if (saved !== null) {
        const files = validateDrafts(JSON.parse(saved));
        for (const file of files) {
          const pending = staged.get(file.path);
          const live = this.buffers.get(file.path);
          if ((pending && (pending.content !== file.content || pending.revision !== file.revision || pending.newline !== file.newline)) ||
              (live?.dirty && (live._exactContent !== file.content || live.revision !== file.revision || live.newline !== file.newline))) {
            throw new Error('A different recovery draft already exists for ' + file.path + '. Resolve or export the existing browser draft before enabling recovery.');
          }
          if (!pending && !live?.dirty) staged.set(file.path, file);
        }
      }
      validateDrafts({schema: SCHEMA, files: [...staged.values()]});
    } catch (error) {
      this.storage = null;
      this.storageError = 'Browser recovery could not be read: ' + error.message;
      return; // Preserve stored bytes and in-memory drafts; do not enable writes.
    }
    this._recovery = staged;
    this.storage = storage;
    this._persist();
  }

  _persist() {
    if (!this.storage || this._disposed) return;
    try {
      this.storage.setItem(this.storageKey, JSON.stringify(this.exportDrafts()));
      this.storageError = null;
    } catch (error) {
      this.storageError = 'Browser recovery could not be saved: ' + error.message;
    }
  }

  dispose() {
    this._disposed = true;
  }
}
