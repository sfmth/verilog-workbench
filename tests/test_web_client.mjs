import assert from 'node:assert/strict';
import test from 'node:test';
import {CompanionClient, localEndpoint} from '../web/m0/client.mjs';

test('only explicit loopback endpoints can receive credentials', () => {
  for (const host of ['127.0.0.1','localhost','[::1]']) assert.equal(localEndpoint(`http://${host}:8765`), `http://${host}:8765`);
  for (const value of ['https://example.com','http://127.0.0.1.evil.test','http://user:pass@localhost','http://localhost/path','http://localhost/?x=1','file:///tmp/a','http://localhost/#code']) assert.throws(() => localEndpoint(value));
});
test('pairing exchanges code once; bearer credential is only in request headers', async () => {
  const calls=[];
  const client=new CompanionClient('http://127.0.0.1:8765', async (url, options) => {
    calls.push({url,options});
    return {ok:true,json:async()=>url.endsWith('/pair')?{protocol:1,token:'test-secret',project:{name:'demo'}}:{files:[]}};
  });
  await assert.rejects(client.request('/v1/project/files'), /Pair/);
  await client.pair('one-time-code');
  await client.request('/v1/project/files');
  assert.equal(calls[0].options.headers.Authorization, undefined);
  assert.equal(calls[1].options.headers.Authorization,'Bearer test-secret');
  assert.ok(calls.every(c=>!c.url.includes('test-secret')&&!c.url.includes('one-time-code')));
  assert.equal(calls[1].options.credentials,'omit');
  client.forget();
  await assert.rejects(client.request('/v1/project/files'),/Pair/);
});
test('conflict responses retain status and do not alter session', async () => {
  const client=new CompanionClient('http://localhost:8765',async()=>({ok:false,status:409,json:async()=>({error:'file changed on disk'})}));
  client.token='local';
  await assert.rejects(client.request('/v1/project/file',{method:'PUT',body:{revision:'old'}}),e=>e.status===409&&/disk/.test(e.message));
  assert.equal(client.token,'local');
});
test('browser fetch receives its Window receiver', async () => {
  const client = new CompanionClient('http://localhost:8765', async function () {
    assert.equal(this, globalThis);
    return {ok:true,json:async()=>({protocol:1,token:'test-secret'})};
  });
  await client.pair('one-time-code');
});
