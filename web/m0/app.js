import {CompanionClient} from './client.mjs';

const $ = id => document.getElementById(id);
let client, loadedPath, revision, newline = '\n', activeJob, generation = 0;
const connected = value => {
  for (const id of ['files','capabilities','read','run']) $(id).disabled = !value;
  $('endpoint').disabled = value;
  $('pair').disabled = value;
};
const message = error => error.name === 'AbortError' ? 'Companion request timed out. Check the address and browser permission.' : error.message;
async function report(action, target = 'connection-status') {
  try { await action(); } catch (error) { $(target).textContent = message(error); }
}
$('pair-form').addEventListener('submit', event => {
  event.preventDefault();
  report(async () => {
    $('connection-status').textContent = 'Pairing…';
    const next = new CompanionClient($('endpoint').value);
    const data = await next.pair($('code').value);
    client = next;
    $('code').value = '';
    connected(true);
    $('connection-status').textContent = 'Connected to the authorized local project.';
    $('project-summary').textContent = JSON.stringify(data.project, null, 2);
  });
});
$('forget').addEventListener('click', () => {
  generation++;
  client?.forget(); client = null; loadedPath = null; revision = null; activeJob = null;
  $('content').value = ''; $('content').disabled = true; $('save').disabled = true;
  $('cancel').disabled = true; $('code').value = '';
  $('inventory').textContent = 'No project loaded.';
  $('project-summary').textContent = 'Pair a companion to inspect the folder authorized at startup.';
  $('file-status').textContent = 'No file loaded.';
  connected(false);
  $('connection-status').textContent = 'Session forgotten in this tab. Restart the companion for a new pairing code.';
});
$('files').addEventListener('click', () => report(async () => {
  $('inventory').textContent = JSON.stringify(await client.request('/v1/project/files'), null, 2);
}));
$('capabilities').addEventListener('click', () => report(async () => {
  $('inventory').textContent = JSON.stringify(await client.request('/v1/capabilities'), null, 2);
}));
$('read-form').addEventListener('submit', event => {
  event.preventDefault();
  report(async () => {
    const path = $('path').value;
    const file = await client.request('/v1/project/file?path=' + encodeURIComponent(path));
    loadedPath = file.path; revision = file.revision;
    newline = file.content.includes('\r\n') ? '\r\n' : '\n';
    $('content').value = file.content; $('content').disabled = false; $('save').disabled = false;
    $('file-status').textContent = 'Loaded current disk revision.';
  }, 'file-status');
});
$('content').addEventListener('input', () => { $('file-status').textContent = 'Unsaved changes.'; });
$('save').addEventListener('click', () => report(async () => {
  const content = $('content').value.replace(/\r\n/g, '\n').replace(/\n/g, newline);
  const file = await client.request('/v1/project/file', {method:'PUT', body:{path:loadedPath, content, revision}});
  revision = file.revision;
  $('file-status').textContent = 'Saved to the ordinary local file.';
}, 'file-status'));
$('run').addEventListener('click', () => report(async () => {
  const session = client, thisGeneration = generation;
  const job = await session.request('/v1/jobs', {method:'POST', body:{command:'list', options:{}, timeout:30}});
  activeJob = job.id; $('cancel').disabled = false; $('run').disabled = true;
  try {
    for (let i=0; i<160 && generation===thisGeneration; i++) {
      const result = await session.request('/v1/jobs/' + encodeURIComponent(job.id));
      $('job-output').textContent = JSON.stringify(result, null, 2);
      if (['passed','failed','cancelled','timed_out'].includes(result.status)) return;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    if (generation===thisGeneration) throw new Error('Job polling limit reached; use Cancel or reconnect to inspect it.');
  } finally {
    if (generation===thisGeneration) { activeJob=null; $('cancel').disabled=true; $('run').disabled=false; }
  }
}, 'job-output'));
$('cancel').addEventListener('click', () => report(async () => {
  if (activeJob) await client.request('/v1/jobs/' + encodeURIComponent(activeJob) + '/cancel', {method:'POST', body:{}});
}, 'job-output'));
