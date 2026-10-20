import assert from 'node:assert/strict';
import test from 'node:test';
import {createHash} from 'node:crypto';
import {MAX_DRAFT_IMPORT_BYTES, WorkspaceModel} from '../web/workspace/model.mjs';

const hash = content => createHash('sha256').update(content).digest('hex');
const draft = (path, content, revision, newline = '\n') => ({path, content, revision, newline});
const exportOf = (...files) => ({schema: 'vwb.drafts/1', files});

test('semantic snapshots preserve disk newlines and monotonically version draft content', async () => {
  const {client} = setup({'src/top.sv': '\uFEFFmodule top;\r\nendmodule\r\n'});
  const model = new WorkspaceModel(client);
  await model.open('src/top.sv');
  assert.deepEqual(model.editorDocuments().map(file => [file.path, file.content, file.version]), [
    ['src/top.sv', '\uFEFFmodule top;\r\nendmodule\r\n', 1],
  ]);
  model.edit('src/top.sv', '\uFEFFmodule top;\n// draft\nendmodule\n');
  assert.equal(model.editorDocuments()[0].content, '\uFEFFmodule top;\r\n// draft\r\nendmodule\r\n');
  assert.equal(model.editorDocuments()[0].version, 2);
});

test('committed semantic edits update open buffers together and preserve unopened file state', async () => {
  const {client} = setup({'src/a.py': 'a = 1\n', 'src/b.py': 'b = 2\n'});
  const model = new WorkspaceModel(client);
  await model.open('src/a.py'); await model.open('src/b.py');
  model.acceptEditorFiles([{path: 'src/a.py', content: 'renamed = 1\n', revision: hash('renamed = 1\n')},
    {path: 'src/b.py', content: 'b = 3\r\n', revision: hash('b = 3\r\n')},
    {path: 'src/closed.py', content: 'other = 1\n', revision: hash('other = 1\n')}]);
  assert.equal(model.buffers.get('src/a.py').content, 'renamed = 1\n');
  assert.equal(model.buffers.get('src/b.py').newline, '\r\n');
  assert.equal(model.buffers.get('src/b.py').dirty, false);
  assert.equal(model.buffers.has('src/closed.py'), false);
  assert.equal(model.editorDocuments()[0].version, 2);
});

test('semantic buffer refresh refuses a concurrent draft without partially replacing others', async () => {
  const {client} = setup({'src/a.py': 'a = 1\n', 'src/b.py': 'b = 2\n'});
  const model = new WorkspaceModel(client);
  await model.open('src/a.py'); await model.open('src/b.py');
  model.edit('src/b.py', 'my draft\n');
  assert.throws(() => model.acceptEditorFiles([
    {path: 'src/a.py', content: 'changed\n', revision: hash('changed\n')},
    {path: 'src/b.py', content: 'changed\n', revision: hash('changed\n')},
  ]), /draft/);
  assert.equal(model.buffers.get('src/a.py').content, 'a = 1\n');
  assert.equal(model.buffers.get('src/b.py').content, 'my draft\n');
});

test('a file open started before a committed rename cannot install its obsolete disk read', async () => {
  let finish;
  const client = {request: () => new Promise(resolve => { finish = resolve; })};
  const model = new WorkspaceModel(client);
  const pending = model.open('src/top.py');
  model.acceptEditorFiles([{path: 'src/top.py', content: 'renamed = 1\n', revision: hash('renamed = 1\n')}]);
  finish({path: 'src/top.py', content: 'old = 1\n', revision: hash('old = 1\n')});
  const opened = await pending;
  assert.equal(opened.content, 'renamed = 1\n');
  assert.equal(opened.revision, hash('renamed = 1\n'));
});

function setup(contents = {'src/top.sv': 'module top;\nendmodule\n'}) {
  const disk = new Map(Object.entries(contents));
  const calls = [];
  const client = {token: 'private-session-token', async request(path, options = {}) {
    calls.push({path, options});
    if (path === '/v1/project/files') return {files: [...disk].map(([path, content]) => ({path, size: Buffer.byteLength(content)})), truncated: false};
    if (options.method === 'PUT') {
      const {path, content, revision} = options.body;
      if (revision !== (disk.has(path) ? hash(disk.get(path)) : null)) {
        throw Object.assign(new Error('file changed outside this editor'), {status: 409});
      }
      disk.set(path, content);
      return {path, revision: hash(content), size: Buffer.byteLength(content)};
    }
    const file = new URL(path, 'http://localhost').searchParams.get('path');
    if (!disk.has(file)) throw Object.assign(new Error('file not found'), {status: 404});
    return {path: file, content: disk.get(file), revision: hash(disk.get(file)), encoding: 'utf-8'};
  }};
  return {disk, calls, client};
}

function memoryStorage() {
  const values = new Map();
  return {values, getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)};
}

function lifecycleSetup(contents = {'src/design/top.sv': '\uFEFFmodule top;\r\nendmodule\r\n', 'src/other.py': 'other = 1\n'}) {
  const state = setup(contents);
  const directories = new Set(['src', 'src/empty']);
  for (const path of state.disk.keys()) {
    const parts = path.split('/');
    for (let end = 1; end < parts.length; end++) directories.add(parts.slice(0, end).join('/'));
  }
  const trash = new Map();
  const within = (parent, path) => parent === path || path.startsWith(parent + '/');
  const conflict = message => Object.assign(new Error(message), {status: 409});
  const entry = path => {
    const files = [...state.disk].filter(([file]) => within(path, file));
    const dirs = [...directories].filter(directory => within(path, directory)).sort();
    if (!state.disk.has(path) && !directories.has(path)) throw Object.assign(new Error('entry not found'), {status: 404});
    return {path, kind: state.disk.has(path) ? 'file' : 'directory',
      revision: state.disk.has(path) ? hash(state.disk.get(path)) : hash(JSON.stringify({files, dirs})),
      file_count: files.length, bytes: files.reduce((sum, [, content]) => sum + Buffer.byteLength(content), 0)};
  };
  const original = state.client.request.bind(state.client);
  state.client.request = async (url, options = {}) => {
    if (url === '/v1/project/files') {
      const inventory = await original(url, options);
      return {...inventory, directories: [...directories].sort().map(path => ({path}))};
    }
    if (url.startsWith('/v1/project/entry?')) {
      state.calls.push({path: url, options});
      return entry(new URL(url, 'http://localhost').searchParams.get('path'));
    }
    if (url === '/v1/project/trash' && !options.method) {
      state.calls.push({path: url, options});
      return {entries: [...trash.values()].map(record => record.entry)};
    }
    const operation = url.match(/^\/v1\/project\/(create|move|trash|restore)$/)?.[1];
    if (!operation) return original(url, options);
    state.calls.push({path: url, options});
    const body = options.body;
    if (operation === 'create') {
      if (state.disk.has(body.path) || directories.has(body.path)) throw conflict('destination exists');
      if (body.kind === 'file') state.disk.set(body.path, body.content);
      else directories.add(body.path);
      return entry(body.path);
    }
    if (operation === 'restore') {
      const record = trash.get(body.id);
      if (!record) throw conflict('trash entry missing');
      if (state.disk.has(record.entry.path) || directories.has(record.entry.path)) throw conflict('destination exists');
      for (const [path, content] of record.files) state.disk.set(path, content);
      for (const path of record.dirs) directories.add(path);
      trash.delete(body.id);
      return entry(record.entry.path);
    }
    const current = entry(body.path);
    if (current.revision !== body.revision) throw conflict('reviewed entry changed');
    for (const document of body.documents ?? []) {
      if (!state.disk.has(document.path) || hash(state.disk.get(document.path)) !== document.revision) {
        throw conflict('open buffer changed on disk');
      }
    }
    const files = [...state.disk].filter(([path]) => within(body.path, path));
    const dirs = [...directories].filter(path => within(body.path, path));
    if (operation === 'move' && (state.disk.has(body.destination) || directories.has(body.destination))) throw conflict('destination exists');
    for (const [path] of files) state.disk.delete(path);
    for (const path of dirs) directories.delete(path);
    if (operation === 'trash') {
      const id = 'trash_' + String(trash.size + 1);
      const result = {...current, id};
      trash.set(id, {entry: result, files, dirs});
      return result;
    }
    for (const [path, content] of files) state.disk.set(body.destination + path.slice(body.path.length), content);
    for (const path of dirs) directories.add(body.destination + path.slice(body.path.length));
    return {...entry(body.destination), previous_path: body.path};
  };
  return {...state, directories, trash, entry};
}

test('directory inventories tolerate old companions and include empty directories when available', async () => {
  const legacy = new WorkspaceModel(setup().client);
  await legacy.connect();
  assert.deepEqual(legacy.directories, []);
  const model = new WorkspaceModel(lifecycleSetup().client);
  await model.connect();
  assert.ok(model.directories.some(entry => entry.path === 'src/empty'));
});

test('file and directory creation refresh inventory once and leave file contents unopened', async () => {
  const {client, calls, disk} = lifecycleSetup();
  const model = new WorkspaceModel(client);
  const content = '\uFEFFnew source\r\n';
  const created = await model.createEntry('src/new.sv', 'file', content);
  assert.equal(created.revision, hash(content));
  assert.equal(disk.get(created.path), content);
  assert.equal(model.buffers.has(created.path), false);
  assert.deepEqual(calls.map(call => call.path), ['/v1/project/create', '/v1/project/files']);
  await model.createEntry('src/new-folder', 'directory');
  assert.ok(model.directories.some(entry => entry.path === 'src/new-folder'));
  assert.equal(model.structureBusy, false);
  assert.equal(model.lastStructureChange.operation, 'create');
});

test('a directory move remaps clean buffers atomically with exact bytes and revision preserved', async () => {
  const {client, calls, disk} = lifecycleSetup();
  const model = new WorkspaceModel(client);
  const original = await model.open('src/design/top.sv');
  const other = await model.open('src/other.py');
  const before = model.editorDocuments().find(file => file.path === original.path);
  const source = await model.entry('src/design');
  calls.length = 0;
  const result = await model.moveEntry(source.path, 'src/renamed', source.revision);
  assert.equal(result.path, 'src/renamed');
  assert.equal(model.buffers.get('src/renamed/top.sv'), original);
  assert.equal(original.path, 'src/renamed/top.sv');
  assert.equal(original.dirty, false);
  assert.equal(original.newline, '\r\n');
  assert.equal(original.revision, before.revision);
  assert.equal(model.buffers.has('src/design/top.sv'), false);
  assert.equal(model.buffers.get(other.path), other);
  assert.deepEqual(model.editorDocuments().find(file => file.path === original.path), {...before, path: original.path, version: before.version + 1});
  assert.equal(disk.get(original.path), before.content);
  assert.deepEqual(calls.map(call => call.path), ['/v1/project/move', '/v1/project/files']);
  assert.deepEqual(calls[0].options.body.documents, [{path: 'src/design/top.sv', revision: before.revision}]);
});

test('trash closes only the affected subtree and explicit restore preserves exact source on disk', async () => {
  const {client, disk} = lifecycleSetup();
  const model = new WorkspaceModel(client);
  const buffer = await model.open('src/design/top.sv');
  const content = buffer._exactContent;
  const other = await model.open('src/other.py');
  const source = await model.entry('src/design');
  const trashed = await model.trashEntry(source.path, source.revision);
  assert.equal(model.buffers.has(buffer.path), false);
  assert.equal(model.buffers.get(other.path), other);
  assert.equal(disk.has(buffer.path), false);
  assert.deepEqual(await model.trashEntries(), [trashed]);
  const restored = await model.restoreEntry(trashed.id);
  assert.equal(restored.path, source.path);
  assert.equal(disk.get(buffer.path), content);
  assert.equal(model.buffers.has(buffer.path), false);
  assert.deepEqual(await model.trashEntries(), []);
});

test('invalid native trash records stay visible without hiding healthy restore candidates', async () => {
  const {client} = lifecycleSetup();
  const model = new WorkspaceModel(client);
  const source = await model.entry('src/design');
  const trashed = await model.trashEntry(source.path, source.revision);
  const request = client.request.bind(client);
  const invalid = {id: '.metadata-leftover.tmp', state: 'invalid', restorable: false,
    error: 'trash entry cannot be verified; retained for native inspection'};
  client.request = async (...args) => args[0] === '/v1/project/trash' && !args[1]?.method
    ? {entries: [invalid, trashed]} : request(...args);
  assert.deepEqual(await model.trashEntries(), [invalid, trashed]);
  await assert.rejects(model.restoreEntry(invalid.id), /Invalid project trash ID/);
  client.request = async (...args) => args[0] === '/v1/project/trash' && !args[1]?.method
    ? {entries: [{...invalid, restorable: true}]} : request(...args);
  await assert.rejects(model.trashEntries(), /Invalid companion project trash warning/);
});

test('stale reviewed entry revisions fail without refreshing or remapping buffers', async () => {
  const {client, calls, disk} = lifecycleSetup();
  const model = new WorkspaceModel(client);
  const buffer = await model.open('src/design/top.sv');
  const source = await model.entry('src/design');
  disk.set(buffer.path, 'external source\n');
  calls.length = 0;
  await assert.rejects(model.moveEntry(source.path, 'src/changed', source.revision), error => error.status === 409);
  assert.equal(model.buffers.get(buffer.path), buffer);
  assert.equal(buffer.path, 'src/design/top.sv');
  assert.deepEqual(calls.map(call => call.path), ['/v1/project/move']);
  assert.equal(model.lastStructureChange, null);
});

test('a fresh subtree review still protects an older clean open buffer from external edits', async () => {
  const {client, calls, disk} = lifecycleSetup();
  const model = new WorkspaceModel(client);
  const buffer = await model.open('src/design/top.sv');
  disk.set(buffer.path, 'external source\n');
  const source = await model.entry('src/design');
  calls.length = 0;
  await assert.rejects(model.trashEntry(source.path, source.revision), /open buffer changed/);
  assert.equal(buffer.content, '\uFEFFmodule top;\nendmodule\n');
  assert.equal(disk.get(buffer.path), 'external source\n');
  assert.equal(model.buffers.get(buffer.path), buffer);
  assert.equal(calls.length, 1);
});

test('structure changes require all buffers clean and all recovery drafts resolved', async () => {
  const {client, calls} = lifecycleSetup();
  const model = new WorkspaceModel(client);
  const buffer = await model.open('src/other.py');
  model.edit(buffer.path, 'draft\n');
  let before = calls.length;
  await assert.rejects(model.createEntry('src/new', 'directory'), /Save all buffers/);
  assert.equal(calls.length, before);
  model.edit(buffer.path, 'other = 1\n');
  model.importDrafts(exportOf(draft('src/recovered.py', 'recovery\n', null)));
  before = calls.length;
  await assert.rejects(model.createEntry('src/new', 'directory'), /recovery drafts/);
  assert.equal(calls.length, before);
  assert.equal(model.recoverable.length, 1);
});

test('pending reads block structural mutations before they can remove an opening path', async () => {
  for (const action of ['open', 'reload', 'recover', 'connect', 'entry', 'trashEntries']) {
    const {client, calls} = lifecycleSetup();
    const model = new WorkspaceModel(client);
    if (['reload', 'recover'].includes(action)) await model.open('src/design/top.sv');
    if (action === 'recover') model.importDrafts(exportOf(draft('src/design/top.sv', 'old', hash('old'))));
    const request = client.request.bind(client);
    let release;
    client.request = async (...args) => { await new Promise(resolve => { release = resolve; }); return request(...args); };
    const pending = action === 'connect' || action === 'trashEntries' ? model[action]() : model[action]('src/design/top.sv');
    await assert.rejects(model.createEntry('src/new', 'directory'), /pending file reads/);
    assert.equal(calls.some(call => call.options.method === 'POST'), false, action);
    release();
    await pending;
    assert.equal(model.structureBusy, false);
  }
});

test('in-flight saves block structural changes until the saved revision is authoritative', async () => {
  const {client, calls} = lifecycleSetup();
  const model = new WorkspaceModel(client);
  const buffer = await model.open('src/other.py');
  model.edit(buffer.path, 'changed = 1\n');
  const request = client.request.bind(client);
  let release;
  client.request = async (...args) => {
    if (args[1]?.method === 'PUT') await new Promise(resolve => { release = resolve; });
    return request(...args);
  };
  const saving = model.save(buffer.path);
  await assert.rejects(model.createEntry('src/new', 'directory'), /pending file reads and saves/);
  assert.equal(calls.some(call => call.options.method === 'POST'), false);
  release();
  await saving;
  const source = await model.entry(buffer.path);
  await model.moveEntry(source.path, 'src/moved.py', source.revision);
  assert.equal(model.buffers.get('src/moved.py'), buffer);
  assert.equal(buffer.revision, hash('changed = 1\n'));
});

test('moving more than 64 affected open buffers requires closing some before a mutation', async () => {
  const contents = Object.fromEntries(Array.from({length: 65}, (_, index) => ['src/design/file' + index + '.py', 'value = 1\n']));
  const {client, calls} = lifecycleSetup(contents);
  const model = new WorkspaceModel(client);
  await Promise.all(Object.keys(contents).map(path => model.open(path)));
  const source = await model.entry('src/design');
  const before = calls.length;
  await assert.rejects(model.moveEntry(source.path, 'src/renamed', source.revision), /more than 64 open buffers/);
  assert.equal(calls.length, before);
  assert.equal(model.buffers.size, 65);
});

test('a structural mutation freezes competing editor and lifecycle actions through inventory refresh', async () => {
  const {client} = lifecycleSetup();
  const model = new WorkspaceModel(client);
  const buffer = await model.open('src/design/top.sv');
  const request = client.request.bind(client);
  let release;
  client.request = async (...args) => {
    if (args[0] === '/v1/project/files') await new Promise(resolve => { release = resolve; });
    return request(...args);
  };
  const pending = model.createEntry('src/new', 'directory');
  assert.equal(model.structureBusy, true);
  await assert.rejects(model.open(buffer.path), /current project file operation/);
  await assert.rejects(model.reload(buffer.path), /current project file operation/);
  await assert.rejects(model.save(buffer.path), /current project file operation/);
  await assert.rejects(model.saveAll(), /current project file operation/);
  await assert.rejects(model.recover(buffer.path), /current project file operation/);
  await assert.rejects(model.connect(), /current project file operation/);
  await assert.rejects(model.createEntry('src/competing', 'directory'), /current project file operation/);
  for (const action of [() => model.edit(buffer.path, 'draft'), () => model.close(buffer.path),
    () => model.importDrafts(exportOf()), () => model.discardRecovery(buffer.path),
    () => model.setStorage(null), () => model.acceptEditorFiles([])]) {
    assert.throws(action, /current project file operation/);
  }
  assert.equal(model.editorDocuments()[0].path, buffer.path); // Read-only status rendering remains available.
  release();
  await pending;
  assert.equal(model.structureBusy, false);
  model.edit(buffer.path, 'draft after operation');
  assert.equal(buffer.dirty, true);
});

test('a committed move stays mapped if its inventory refresh fails and is never posted twice', async () => {
  const {client, calls, disk} = lifecycleSetup();
  const model = new WorkspaceModel(client);
  const buffer = await model.open('src/design/top.sv');
  const source = await model.entry(buffer.path);
  const request = client.request.bind(client);
  client.request = async (...args) => {
    if (args[0] === '/v1/project/files') throw new Error('inventory connection lost');
    return request(...args);
  };
  await assert.rejects(model.moveEntry(source.path, 'src/moved.sv', source.revision), error => {
    assert.equal(error.applied, true);
    assert.equal(error.result.path, 'src/moved.sv');
    assert.equal(error.operation, 'move');
    return /inventory connection lost/.test(error.message);
  });
  assert.equal(model.buffers.get('src/moved.sv'), buffer);
  assert.equal(buffer.path, 'src/moved.sv');
  assert.equal(model.buffers.has(source.path), false);
  assert.equal(disk.has(source.path), false);
  assert.equal(calls.filter(call => call.path === '/v1/project/move').length, 1);
  assert.equal(model.lastStructureChange.entry.path, buffer.path);
  assert.equal(model.structureBusy, false);
  client.request = request;
  await model.connect();
  assert.ok(model.files.some(file => file.path === buffer.path));
  assert.equal(calls.filter(call => call.path === '/v1/project/move').length, 1);
});

test('malformed lifecycle arguments and destination buffers fail before filesystem mutation', async () => {
  const {client, calls} = lifecycleSetup();
  const model = new WorkspaceModel(client);
  for (const action of [() => model.createEntry('../escape', 'file'), () => model.createEntry('src/new', 'link'),
    () => model.createEntry('src/new', 'directory', 'source'), () => model.moveEntry('src/design', 'src/design/nested', hash('base')),
    () => model.moveEntry('src/design', 'src/new', null), () => model.trashEntry('src/design', 'invalid'),
    () => model.restoreEntry('../escape')]) await assert.rejects(action());
  assert.equal(calls.length, 0);
  await model.open('src/other.py');
  const source = await model.entry('src/design');
  const before = calls.length;
  await assert.rejects(model.moveEntry(source.path, 'src/other.py', source.revision), /already open/);
  assert.equal(calls.length, before);
});

test('workspace inventories files and retains independent dirty buffers while switching', async () => {
  const {client, disk, calls} = setup({'src/a.sv': 'a\n', 'src/b.sv': 'b\n'});
  const model = new WorkspaceModel(client);
  const inventory = await model.connect();
  assert.equal(model.files, inventory.files);
  const a = await model.open('src/a.sv');
  model.edit(a.path, 'a changed\n');
  await model.open('src/b.sv');
  assert.equal(await model.open('src/a.sv'), a);
  assert.equal(a.content, 'a changed\n');
  assert.equal(a.dirty, true);
  await model.save(a.path);
  assert.equal(disk.get(a.path), 'a changed\n');
  assert.equal(a.dirty, false);
  assert.equal(a.revision, hash('a changed\n'));
  assert.equal(calls.filter(call => call.path.includes('?path=src%2Fa.sv')).length, 1);
});

test('revision conflicts retain in-memory and persisted text without overwriting disk', async () => {
  const {client, disk} = setup();
  const storage = memoryStorage();
  const model = new WorkspaceModel(client, {storage, storageKey: 'drafts:exact-project'});
  const buffer = await model.open('src/top.sv');
  const base = buffer.revision;
  model.edit(buffer.path, 'unsaved browser source');
  disk.set(buffer.path, 'external source');
  await assert.rejects(model.save(buffer.path), error => error.status === 409);
  assert.equal(buffer.content, 'unsaved browser source');
  assert.equal(buffer.dirty, true);
  assert.equal(buffer.conflict, true);
  assert.equal(buffer.revision, base);
  assert.equal(disk.get(buffer.path), 'external source');
  assert.deepEqual(JSON.parse(storage.getItem('drafts:exact-project')), exportOf(draft(buffer.path, buffer.content, base)));
});

test('recovery is explicit, reads current disk first, and keeps the draft base revision', async () => {
  const {client, disk, calls} = setup();
  const storage = memoryStorage();
  const old = new WorkspaceModel(client, {storage, storageKey: 'drafts:project-a'});
  const original = await old.open('src/top.sv');
  const base = original.revision;
  old.edit(original.path, 'recovered source');
  old.dispose();
  disk.set(original.path, 'newer disk source');
  const model = new WorkspaceModel(client, {storage, storageKey: 'drafts:project-a'});
  assert.equal(model.buffers.size, 0);
  assert.equal(model.recoverable.length, 1);
  const before = calls.length;
  const recovered = await model.recover(original.path);
  assert.equal(calls.length, before + 1);
  assert.equal(calls.at(-1).options.method, undefined);
  assert.equal(recovered.content, 'recovered source');
  assert.equal(recovered.revision, base);
  assert.equal(recovered.diskRevision, hash('newer disk source'));
  assert.equal(recovered.conflict, true);
  assert.equal(model.recoverable.length, 0);
  await assert.rejects(model.save(original.path), error => error.status === 409);
  assert.equal(disk.get(original.path), 'newer disk source');
});

test('import stages only; new-file recovery checks absence before create-if-absent save', async () => {
  const {client, calls, disk} = setup();
  const model = new WorkspaceModel(client);
  model.importDrafts(exportOf(draft('src/new.sv', 'new source\n', null)));
  assert.equal(calls.length, 0);
  assert.equal(model.buffers.size, 0);
  const buffer = await model.recover('src/new.sv');
  assert.equal(buffer.revision, null);
  assert.equal(buffer.dirty, true);
  assert.equal(disk.has('src/new.sv'), false);
  await model.save('src/new.sv');
  assert.equal(disk.get('src/new.sv'), 'new source\n');
  assert.equal(calls.at(-1).options.body.revision, null);
});

test('recovery cannot clobber a dirty buffer, including an edit made during its disk read', async () => {
  const {client} = setup();
  const model = new WorkspaceModel(client);
  const buffer = await model.open('src/top.sv');
  model.importDrafts(exportOf(draft(buffer.path, 'draft text', buffer.revision)));
  const request = client.request.bind(client);
  let release;
  client.request = async (...args) => { await new Promise(resolve => { release = resolve; }); return request(...args); };
  const recovering = model.recover(buffer.path);
  model.edit(buffer.path, 'newer in-memory edit');
  release();
  await assert.rejects(recovering, /changed/);
  assert.equal(buffer.content, 'newer in-memory edit');
  assert.equal(model.recoverable.length, 1);
  await assert.rejects(model.recover(buffer.path), /current buffer/);
});

test('malformed imports fail atomically for paths, revisions, newlines, text and size', () => {
  const {client, calls} = setup();
  const model = new WorkspaceModel(client);
  const good = draft('src/good.sv', 'retained', hash('base'));
  model.importDrafts(exportOf(good));
  const malformed = [
    null, {}, {schema: 'another-format', files: []},
    ...['../escape', '/absolute', 'a//b', './a', 'a/../b', 'a\\b', 'C:file', '.git/config', 'src/.GIT/config', 'a\0b', 'a/NUL.txt', 'a/trailing.'].map(path => exportOf({...good, path})),
    exportOf({...good, revision: 'old'}), exportOf({...good, revision: undefined}),
    exportOf({...good, newline: 'mixed'}), exportOf({...good, content: 3}),
    exportOf({...good, content: '\ud800'}), exportOf({...good, content: '\udc00'}),
    exportOf({...good, content: 'x'.repeat(2 * 1024 * 1024 + 1)}),
    exportOf(good, good), exportOf(good, {...good, path: '../escape'}),
  ];
  for (const data of malformed) assert.throws(() => model.importDrafts(data));
  assert.deepEqual(model.recoverable, [good]);
  assert.equal(calls.length, 0);
});

test('import merges identical recovery but rejects a different staged draft atomically', () => {
  const {client} = setup();
  const storage = memoryStorage();
  const model = new WorkspaceModel(client, {storage, storageKey: 'drafts:a'});
  const original = draft('src/a.sv', 'existing recovery', hash('base'));
  model.importDrafts(exportOf(original));
  model.importDrafts(exportOf({...original}));
  assert.deepEqual(model.recoverable, [original]);
  const saved = storage.getItem('drafts:a');
  for (const changed of [{content: 'replacement'}, {revision: hash('other base')}, {newline: '\r\n'}]) {
    assert.throws(() => model.importDrafts(exportOf(draft('src/other.sv', 'new', null), {...original, ...changed})), /different recovery draft/);
    assert.deepEqual(model.recoverable, [original]);
    assert.equal(storage.getItem('drafts:a'), saved);
  }
});

test('escaped legal draft exports fit the JSON import bound', () => {
  const {client} = setup();
  const model = new WorkspaceModel(client);
  // A control character consumes one UTF-8 source byte but six JSON bytes.
  model.importDrafts(exportOf(draft('src/escaped.sv', '\u0001'.repeat(2 * 1024 * 1024), null)));
  const json = JSON.stringify(model.exportDrafts());
  assert.ok(Buffer.byteLength(json) > 2 * 1024 * 1024);
  assert.ok(Buffer.byteLength(json) < MAX_DRAFT_IMPORT_BYTES);
  assert.ok(MAX_DRAFT_IMPORT_BYTES >= 64 * 1024 * 1024 * 6 + 10000 * 2048);
});

test('CRLF and BOM survive edits and a portable export/reimport round trip', async () => {
  const raw = '\ufeffmodule top;\r\nendmodule\r\n';
  const {client, disk} = setup({'src/top.sv': raw});
  const model = new WorkspaceModel(client);
  const buffer = await model.open('src/top.sv');
  assert.equal(buffer.content, '\ufeffmodule top;\nendmodule\n');
  assert.equal(buffer.newline, '\r\n');
  model.edit(buffer.path, buffer.content.replace('top', 'changed'));
  const exported = model.exportDrafts();
  assert.equal(exported.files[0].content, raw.replace('top', 'changed'));
  const reimported = new WorkspaceModel(client);
  reimported.importDrafts(JSON.parse(JSON.stringify(exported)));
  await reimported.recover(buffer.path);
  await reimported.save(buffer.path);
  assert.equal(disk.get(buffer.path), raw.replace('top', 'changed'));
});

test('unchanged mixed line endings and edit-undo preserve exact bytes and final-newline state', async () => {
  for (const raw of ['\ufeffa\r\nb\nc\rd', 'a\r\nb\nc\r', 'last line without newline']) {
    const {client, calls} = setup({'src/top.sv': raw});
    const model = new WorkspaceModel(client);
    const buffer = await model.open('src/top.sv');
    const text = buffer.content;
    model.edit(buffer.path, text); // Simulate a textarea normalizing its initial value.
    assert.equal(buffer.dirty, false);
    await model.save(buffer.path);
    assert.equal(calls.filter(call => call.options.method === 'PUT').length, 0);
    model.edit(buffer.path, text + 'edit');
    model.edit(buffer.path, text);
    assert.equal(buffer.dirty, false);
    model.importDrafts(exportOf(draft(buffer.path, raw, buffer.revision, buffer.newline)));
    assert.equal(model.exportDrafts().files[0].content, raw);
  }
});

test('saving retains edits typed while a previous version is in flight', async () => {
  const {client, disk, calls} = setup();
  const model = new WorkspaceModel(client);
  const buffer = await model.open('src/top.sv');
  model.edit(buffer.path, 'first version');
  const request = client.request.bind(client);
  let release;
  client.request = async (...args) => { await new Promise(resolve => { release = resolve; }); return request(...args); };
  const saving = model.save(buffer.path);
  assert.equal(buffer.saving, true);
  const concurrent = model.save(buffer.path);
  model.edit(buffer.path, 'typed while saving');
  release();
  await Promise.all([saving, concurrent]);
  assert.equal(disk.get(buffer.path), 'first version');
  assert.equal(buffer.content, 'typed while saving');
  assert.equal(buffer.dirty, true);
  assert.equal(buffer.saving, false);
  assert.equal(buffer.revision, hash('first version'));
  assert.equal(calls.filter(call => call.options.method === 'PUT').length, 1);
  client.request = request;
  await model.save(buffer.path);
  assert.equal(disk.get(buffer.path), 'typed while saving');
  assert.equal(buffer.dirty, false);
});

test('saveAll saves independent buffers and retains conflicted drafts', async () => {
  const {client, disk} = setup({'src/a.sv': 'a', 'src/b.sv': 'b'});
  const model = new WorkspaceModel(client);
  await model.open('src/a.sv'); await model.open('src/b.sv');
  model.edit('src/a.sv', 'changed a'); model.edit('src/b.sv', 'changed b');
  disk.set('src/a.sv', 'external a');
  await assert.rejects(model.saveAll(), error => error instanceof AggregateError && error.errors[0].status === 409);
  assert.equal(disk.get('src/a.sv'), 'external a');
  assert.equal(disk.get('src/b.sv'), 'changed b');
  assert.equal(model.buffers.get('src/a.sv').dirty, true);
  assert.equal(model.buffers.get('src/b.sv').dirty, false);
});

test('a save conflict retains recovery even when the user undid edits during the request', async () => {
  const {client, disk} = setup({'src/a.sv': 'original'});
  const model = new WorkspaceModel(client);
  const buffer = await model.open('src/a.sv');
  model.edit(buffer.path, 'save snapshot');
  const request = client.request.bind(client);
  let release;
  client.request = async (...args) => { await new Promise(resolve => { release = resolve; }); return request(...args); };
  const saving = model.save(buffer.path);
  model.edit(buffer.path, 'original');
  disk.set(buffer.path, 'external source');
  release();
  await assert.rejects(saving, error => error.status === 409);
  assert.equal(buffer.dirty, true);
  assert.equal(model.exportDrafts().files[0].content, 'original');
  model.edit(buffer.path, 'another edit');
  model.edit(buffer.path, 'original');
  assert.equal(buffer.dirty, true);
});

test('a slow file open cannot overwrite a draft recovered during its read', async () => {
  const {client} = setup({'src/a.sv': 'disk text'});
  const model = new WorkspaceModel(client);
  model.importDrafts(exportOf(draft('src/a.sv', 'recovered text', hash('disk text'))));
  const request = client.request.bind(client);
  let release;
  let first = true;
  client.request = async (...args) => {
    if (first) {
      first = false;
      await new Promise(resolve => { release = resolve; });
    }
    return request(...args);
  };
  const opening = model.open('src/a.sv');
  const recovered = await model.recover('src/a.sv');
  release();
  assert.equal(await opening, recovered);
  assert.equal(model.buffers.get('src/a.sv').content, 'recovered text');
});

test('reload preserves the current buffer, staged recovery and browser draft when disk read fails', async () => {
  const {client} = setup({'src/a.sv': 'original'});
  const storage = memoryStorage();
  const model = new WorkspaceModel(client, {storage, storageKey: 'drafts:a'});
  const buffer = await model.open('src/a.sv');
  model.importDrafts(exportOf(draft(buffer.path, 'staged recovery', buffer.revision)));
  model.edit(buffer.path, 'unsaved edit');
  const saved = storage.getItem('drafts:a');
  client.request = async () => { throw Object.assign(new Error('cannot reach disk'), {status: 503}); };
  await assert.rejects(model.reload(buffer.path), /cannot reach disk/);
  assert.equal(model.buffers.get(buffer.path), buffer);
  assert.equal(buffer.content, 'unsaved edit');
  assert.equal(model.recoverable[0].content, 'staged recovery');
  assert.equal(storage.getItem('drafts:a'), saved);
});

test('reload refuses malformed disk responses without losing the existing draft', async () => {
  const {client} = setup({'src/a.sv': 'original'});
  const storage = memoryStorage();
  const model = new WorkspaceModel(client, {storage, storageKey: 'drafts:a'});
  const buffer = await model.open('src/a.sv');
  model.edit(buffer.path, 'unsaved edit');
  const saved = storage.getItem('drafts:a');
  for (const response of [null, {}, {path: 'src/other.sv', content: 'wrong path', revision: hash('wrong path')},
    {path: buffer.path, content: 3, revision: hash('text')},
    {path: buffer.path, content: 'disk', revision: 'invalid'},
    {path: buffer.path, content: 'disk', revision: null}]) {
    client.request = async () => response;
    await assert.rejects(model.reload(buffer.path));
    assert.equal(model.buffers.get(buffer.path), buffer);
    assert.equal(storage.getItem('drafts:a'), saved);
  }
});

test('reload atomically adopts current disk and clears deliberately obsolete recovery', async () => {
  const {client, disk} = setup({'src/a.sv': 'original'});
  const storage = memoryStorage();
  const model = new WorkspaceModel(client, {storage, storageKey: 'drafts:a'});
  const previous = await model.open('src/a.sv');
  model.importDrafts(exportOf(draft(previous.path, 'staged recovery', previous.revision)));
  model.edit(previous.path, 'discarded buffer');
  disk.set(previous.path, '\ufeffupdated disk\r\n');
  const current = await model.reload(previous.path);
  assert.notEqual(current, previous);
  assert.equal(model.buffers.get(previous.path), current);
  assert.equal(current.content, '\ufeffupdated disk\n');
  assert.equal(current.newline, '\r\n');
  assert.equal(current.revision, hash('\ufeffupdated disk\r\n'));
  assert.equal(current.dirty, false);
  assert.equal(model.recoverable.length, 0);
  assert.deepEqual(JSON.parse(storage.getItem('drafts:a')), exportOf());
});

test('reload retains edits made or undone during its read', async () => {
  for (const undo of [false, true]) {
    const {client, disk} = setup({'src/a.sv': 'original'});
    const storage = memoryStorage();
    const model = new WorkspaceModel(client, {storage, storageKey: 'drafts:a'});
    const previous = await model.open('src/a.sv');
    model.edit(previous.path, 'existing draft');
    disk.set(previous.path, 'newer disk');
    const request = client.request.bind(client);
    let release;
    client.request = async (...args) => { await new Promise(resolve => { release = resolve; }); return request(...args); };
    const reloading = model.reload(previous.path);
    model.edit(previous.path, 'typed during read');
    if (undo) model.edit(previous.path, 'existing draft');
    const saved = storage.getItem('drafts:a');
    release();
    await assert.rejects(reloading, /changed/);
    assert.equal(model.buffers.get(previous.path), previous);
    assert.equal(previous.content, undo ? 'existing draft' : 'typed during read');
    assert.equal(storage.getItem('drafts:a'), saved);
  }
});

test('reload cannot replace a new buffer or imported recovery arriving during the read', async () => {
  for (const action of ['replace', 'import']) {
    const {client} = setup({'src/a.sv': 'original'});
    const model = new WorkspaceModel(client);
    const previous = await model.open('src/a.sv');
    const request = client.request.bind(client);
    let release;
    client.request = async (...args) => { await new Promise(resolve => { release = resolve; }); return request(...args); };
    const reloading = model.reload(previous.path);
    if (action === 'replace') model.buffers.set(previous.path, {...previous, content: 'replacement buffer'});
    else model.importDrafts(exportOf(draft(previous.path, 'new recovery', previous.revision)));
    release();
    await assert.rejects(reloading, /changed/);
    if (action === 'replace') assert.equal(model.buffers.get(previous.path).content, 'replacement buffer');
    else assert.equal(model.recoverable[0].content, 'new recovery');
  }
});

test('reload and close reject an in-flight save, leaving its live buffer intact', async () => {
  const {client} = setup({'src/a.sv': 'original'});
  const model = new WorkspaceModel(client);
  const buffer = await model.open('src/a.sv');
  model.edit(buffer.path, 'save snapshot');
  const request = client.request.bind(client);
  let release;
  client.request = async (...args) => { await new Promise(resolve => { release = resolve; }); return request(...args); };
  const saving = model.save(buffer.path);
  await assert.rejects(model.reload(buffer.path), /current save/);
  assert.throws(() => model.close(buffer.path), /current save/);
  assert.equal(model.buffers.get(buffer.path), buffer);
  release();
  await saving;
  assert.equal(buffer.dirty, false);
});

test('a slow reload cannot overwrite a save completed after its disk snapshot', async () => {
  const {client, disk} = setup({'src/a.sv': 'disk snapshot A'});
  const storage = memoryStorage();
  const model = new WorkspaceModel(client, {storage, storageKey: 'drafts:a'});
  const buffer = await model.open('src/a.sv');
  model.edit(buffer.path, 'saved version B');
  const request = client.request.bind(client);
  let release;
  client.request = async (...args) => {
    const response = await request(...args);
    if (args[0].includes('?path=')) await new Promise(resolve => { release = resolve; });
    return response;
  };
  const reloading = model.reload(buffer.path);
  // Let GET capture A before the independent PUT saves B.
  await Promise.resolve();
  await model.save(buffer.path);
  assert.equal(buffer.saving, false);
  assert.equal(disk.get(buffer.path), 'saved version B');
  release();
  await assert.rejects(reloading, /changed/);
  assert.equal(model.buffers.get(buffer.path), buffer);
  assert.equal(buffer.content, 'saved version B');
  assert.equal(buffer.revision, hash('saved version B'));
  assert.equal(buffer.dirty, false);
  assert.deepEqual(JSON.parse(storage.getItem('drafts:a')), exportOf());
});

test('close persists removal of buffer and staged recovery without re-reading storage', async () => {
  const {client} = setup({'src/a.sv': 'original', 'src/b.sv': 'another'});
  const storage = memoryStorage();
  const model = new WorkspaceModel(client, {storage, storageKey: 'drafts:a'});
  const a = await model.open('src/a.sv');
  const b = await model.open('src/b.sv');
  model.importDrafts(exportOf(draft(a.path, 'staged recovery', a.revision)));
  model.edit(a.path, 'discarded buffer');
  model.edit(b.path, 'retained buffer');
  storage.getItem = () => { throw new Error('storage must not be re-read'); };
  model.close(a.path);
  assert.equal(model.buffers.has(a.path), false);
  assert.equal(model.recoverable.length, 0);
  assert.deepEqual(JSON.parse(storage.values.get('drafts:a')), exportOf(draft(b.path, 'retained buffer', b.revision)));
  assert.equal(model.storageError, null);
});

test('browser storage is opt-in and explicitly scoped, stores only draft schema, and survives disposal', async () => {
  const {client} = setup();
  const storage = memoryStorage();
  assert.throws(() => new WorkspaceModel(client, {storage}), /project-scoped/);
  const model = new WorkspaceModel(client, {storageKey: 'drafts:project-a'});
  const buffer = await model.open('src/top.sv');
  model.edit(buffer.path, 'private local source');
  assert.equal(storage.values.size, 0);
  model.setStorage(storage);
  const saved = storage.getItem('drafts:project-a');
  assert.ok(saved.includes('private local source'));
  assert.ok(!saved.includes(client.token));
  assert.deepEqual(Object.keys(JSON.parse(saved)), ['schema', 'files']);
  assert.equal(new WorkspaceModel(client, {storage, storageKey: 'drafts:project-b'}).recoverable.length, 0);
  model.dispose();
  assert.equal(storage.getItem('drafts:project-a'), saved);
  assert.throws(() => model.edit(buffer.path, 'after disposal'), /closed/);
  const restored = new WorkspaceModel(client, {storage, storageKey: 'drafts:project-a'});
  restored.setStorage(null);
  assert.equal(storage.getItem('drafts:project-a'), null);
  assert.equal(restored.recoverable.length, 1);
});

test('malformed or unavailable browser storage leaves source buffers usable', async () => {
  const {client} = setup();
  const storage = memoryStorage();
  storage.setItem('drafts:a', 'not json');
  const model = new WorkspaceModel(client, {storage, storageKey: 'drafts:a'});
  assert.match(model.storageError, /could not be read/);
  assert.equal(storage.getItem('drafts:a'), 'not json');
  assert.equal(model.storage, null);
  const buffer = await model.open('src/top.sv');
  storage.setItem('drafts:a', JSON.stringify(exportOf()));
  model.setStorage(storage);
  storage.setItem = () => { throw new Error('quota exceeded'); };
  model.edit(buffer.path, 'still recoverable by export');
  assert.match(model.storageError, /quota exceeded/);
  assert.equal(model.exportDrafts().files[0].content, 'still recoverable by export');
});

test('enabling browser recovery rejects conflicting stored drafts without changing either source', async () => {
  for (const origin of ['dirty', 'staged']) {
    const {client} = setup({'src/a.sv': 'base'});
    const storage = memoryStorage();
    const model = new WorkspaceModel(client, {storageKey: 'drafts:a'});
    const source = draft('src/a.sv', 'current in-memory source', hash('base'));
    if (origin === 'dirty') {
      await model.open(source.path);
      model.edit(source.path, source.content);
    } else model.importDrafts(exportOf(source));
    const stored = JSON.stringify(exportOf(draft(source.path, 'older browser source', hash('older base'))));
    storage.setItem('drafts:a', stored);
    model.setStorage(storage);
    assert.equal(model.storage, null);
    assert.match(model.storageError, /different recovery draft/);
    assert.equal(storage.getItem('drafts:a'), stored);
    assert.deepEqual(model.exportDrafts(), exportOf(source));
    // Later edits must not overwrite the older stored source after failed opt-in.
    if (origin === 'dirty') model.edit(source.path, 'later in-memory edit');
    else model.importDrafts(exportOf(draft('src/b.sv', 'another staged file', null)));
    assert.equal(storage.getItem('drafts:a'), stored);
  }
});

test('enabling browser recovery accepts identical dirty or staged drafts', async () => {
  for (const origin of ['dirty', 'staged']) {
    const {client} = setup({'src/a.sv': 'base'});
    const storage = memoryStorage();
    const model = new WorkspaceModel(client, {storageKey: 'drafts:a'});
    const source = draft('src/a.sv', 'same source', hash('base'));
    if (origin === 'dirty') {
      await model.open(source.path);
      model.edit(source.path, source.content);
    } else model.importDrafts(exportOf(source));
    storage.setItem('drafts:a', JSON.stringify(exportOf(source)));
    model.setStorage(storage);
    assert.equal(model.storage, storage);
    assert.equal(model.storageError, null);
    assert.deepEqual(model.exportDrafts(), exportOf(source));
    assert.equal(model.recoverable.length, origin === 'dirty' ? 0 : 1);
  }
});
