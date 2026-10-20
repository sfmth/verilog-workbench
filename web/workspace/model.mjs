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
    this.inventory = null;
    this.buffers = new Map();
    this.storage = null;
    this.storageKey = storageKey;
    this.storageError = null;
    this._recovery = new Map();
    this._opening = new Map();
    this._saving = new Map();
    this._disposed = false;
    if (storage) this.setStorage(storage);
  }

  _active() {
    if (this._disposed) throw new Error('This workspace session has been closed.');
  }

  async connect() {
    this._active();
    const inventory = await this.client.request('/v1/project/files');
    this._active();
    if (!inventory || !Array.isArray(inventory.files)) throw new Error('Invalid companion file inventory.');
    this.files = inventory.files;
    this.inventory = inventory;
    return inventory;
  }

  async open(path) {
    this._active();
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
    this._active();
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
    this._active();
    pathValue(path);
    const previous = this.buffers.get(path);
    if (!previous) throw new Error('Open the file before reloading it.');
    if (previous.saving || this._saving.has(path)) throw new Error('Wait for the current save before reloading this file.');
    const version = previous._version;
    const revision = previous.revision;
    const diskRevision = previous.diskRevision;
    const savedContent = previous._savedContent;
    const recovery = this._recovery.get(path);
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
  }

  close(path) {
    this._active();
    pathValue(path);
    if (this.buffers.get(path)?.saving || this._saving.has(path)) {
      throw new Error('Wait for the current save before closing this file.');
    }
    this.buffers.delete(path);
    this._recovery.delete(path);
    this._persist();
  }

  async save(path) {
    this._active();
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
    this._active();
    const dirty = [...this.buffers.values()].filter(buffer => buffer.dirty);
    // Continue independent saves after a conflict; report all failures to the caller.
    const results = await Promise.allSettled(dirty.map(buffer => this.save(buffer.path)));
    const errors = results.filter(result => result.status === 'rejected').map(result => result.reason);
    if (errors.length) throw new AggregateError(errors, 'Some files could not be saved; their drafts are retained.');
    return results.map(result => result.value);
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
    this._active();
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
    this._active();
    const draft = this._recovery.get(path);
    if (!draft) throw new Error('No recovery draft exists for this file.');
    if (this.buffers.get(path)?.dirty) throw new Error('Save or export the current buffer before restoring another draft.');
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
  }

  discardRecovery(path) {
    this._active();
    this._recovery.delete(path);
    this._persist();
  }

  setStorage(storage) {
    this._active();
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
