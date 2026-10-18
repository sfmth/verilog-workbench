#!/usr/bin/env node
// Real-browser transport/UI proof; credentials never enter the evidence report.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const require=createRequire(import.meta.url);
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const args=Object.fromEntries(process.argv.slice(2).reduce((a,v,i,s)=>i%2===0?[...a,[v,s[i+1]]]:a,[]));
const {chromium}=require(args['--playwright-module'] || 'playwright');
const temporary=await fs.mkdtemp(path.join(os.tmpdir(),'vwb-browser-'));
const project=path.join(temporary,'project µ');
await fs.mkdir(path.join(project,'src'),{recursive:true});
await fs.mkdir(path.join(project,'test'));
await fs.writeFile(path.join(project,'src/top.v'),'module top; endmodule\n');
const children=[];
let browser, staticServer, currentPage;

async function companion(origin) {
  const child=spawn(args['--python'] || process.env.VWB_PYTHON || 'python3',[
    '-m','companion','--project',project,'--origin',origin,'--port','0'],{cwd:repo,stdio:['ignore','pipe','pipe']});
  children.push(child);
  const ready=await new Promise((resolve,reject)=>{
    let line=''; const timer=setTimeout(()=>reject(new Error('Companion startup timed out')),15000);
    child.once('error',e=>{clearTimeout(timer);reject(e)});
    child.stdout.on('data',data=>{
      line+=data;
      if(line.includes('\n')) {clearTimeout(timer);try{resolve(JSON.parse(line.split('\n')[0]))}catch{reject(new Error('Invalid startup message'))}}
    });
    child.once('exit',()=>{clearTimeout(timer);reject(new Error('Companion exited before startup'))});
  });
  return ready;
}

async function waitUntil(page, selector, predicate, timeout=15000) {
  await page.waitForFunction(({selector, predicate})=>{
    const text=document.querySelector(selector)?.textContent || '';
    return predicate==='connected'?text.includes('Connected to'):predicate==='saved'?text.includes('Saved to'):
      predicate==='conflict'?text.includes('changed') || text.includes('conflict'):
      predicate==='passed'?(()=>{try{return JSON.parse(text).status==='passed'}catch{return false}})():false;
  },{selector,predicate},{timeout});
}

const report={schema:'vwb.browser-probe/1',recorded_at:new Date().toISOString(),local_ui:{passed:false},
  public_origin_transport:{state:'not_run'},actual_pages_deployment:{state:'not_verified'},hardware_tested:false};
try {
  const files={
    '/m0/':['index.html','text/html'], '/m0/index.html':['index.html','text/html'],
    '/m0/style.css':['style.css','text/css'], '/m0/app.js':['app.js','text/javascript'],
    '/m0/client.mjs':['client.mjs','text/javascript']};
  staticServer=http.createServer(async(req,res)=>{
    const selected=files[req.url];
    if(!selected) {res.writeHead(404);res.end();return}
    res.writeHead(200,{'Content-Type':selected[1], 'Cache-Control':'no-store'});
    res.end(await fs.readFile(path.join(repo,'web/m0',selected[0])));
  });
  await new Promise(resolve=>staticServer.listen(0,'127.0.0.1',resolve));
  const localOrigin=`http://127.0.0.1:${staticServer.address().port}`;
  const ready=await companion(localOrigin);
  // No CORS/mixed-content/security-disable flags: exercise browser policy.
  browser=await chromium.launch({headless:true,executablePath:args['--executable'],args:['--no-sandbox']});
  report.browser=await browser.version();
  const context=await browser.newContext();
  const page=await context.newPage();
  currentPage=page;
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(localOrigin+'/m0/');
  await page.locator('#endpoint').fill(ready.url);
  await page.locator('#code').fill(ready.pairing_code);
  await page.locator('#pair').click();
  await waitUntil(page,'#connection-status','connected');
  await page.locator('#path').fill('src/top.v');
  await page.locator('#read').click();
  await page.locator('#content').waitFor({state:'visible'});
  await page.waitForFunction(()=>!document.querySelector('#content').disabled);
  const edited='module top; endmodule\n// browser save µ\n';
  await page.locator('#content').fill(edited);
  await page.locator('#save').click();
  await waitUntil(page,'#file-status','saved');
  if(await fs.readFile(path.join(project,'src/top.v'),'utf8')!==edited) throw new Error('Browser save did not reach disk');
  await fs.writeFile(path.join(project,'src/top.v'),edited+'// external editor\n');
  await page.locator('#content').fill(edited+'// conflicting browser\n');
  await page.locator('#save').click();
  await waitUntil(page,'#file-status','conflict');
  if(!(await fs.readFile(path.join(project,'src/top.v'),'utf8')).endsWith('// external editor\n')) throw new Error('Conflict overwrote external edit');
  await page.locator('#run').click();
  await waitUntil(page,'#job-output','passed');
  if(errors.length) throw new Error('Browser script errors: '+errors.join('; '));
  if(args['--screenshot']) await page.screenshot({path:args['--screenshot'],fullPage:true});
  await page.locator('#forget').click();
  if(!(await page.locator('#pair').isEnabled())) throw new Error('Forget session did not reset UI');
  report.local_ui={passed:true,checks:['real-pairing','cross-origin-CORS','local-file-read','revision-save','external-conflict409','native-inventory-job','forget-session'],script_errors:errors};
  await context.close();

  if(args['--public-url']) {
    const publicUrl=args['--public-url'];
    const publicOrigin=new URL(publicUrl).origin;
    const publicReady=await companion(publicOrigin);
    const publicContext=await browser.newContext();
    const publicPage=await publicContext.newPage();
    try {
      const navigation=await publicPage.goto(publicUrl,{timeout:30000,waitUntil:'domcontentloaded'});
      const status=navigation?.status();
      report.actual_pages_deployment={state:status===200?'document_loaded':'not_deployed',url:publicPage.url(),http_status:status,
        note:'A loaded document is not proof that the VWB UI has been deployed or fully tested.'};
      // Simulate an explicit grant through browser test permission APIs; never
      // disable browser web security. A separate denied check has no pairing side effect.
      await publicContext.clearPermissions();
      const denied=await publicPage.evaluate(async(base)=>{
        try{const r=await fetch(base+'/v1/health',{mode:'cors',targetAddressSpace:'loopback'});return {allowed:r.ok}}
        catch(e){return {allowed:false,error:e.name}}
      },publicReady.url);
      await publicContext.grantPermissions(['local-network-access'],{origin:publicOrigin});
      const result=await publicPage.evaluate(async({base,code})=>{
        const response=await fetch(base+'/v1/pair',{method:'POST',mode:'cors',credentials:'omit',targetAddressSpace:'loopback',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});
        const pair=await response.json();
        if(!response.ok) return {passed:false,http_status:response.status};
        const files=await fetch(base+'/v1/project/files',{mode:'cors',targetAddressSpace:'loopback',headers:{Authorization:'Bearer '+pair.token}});
        return {passed:files.ok,protocol:pair.protocol};
      },{base:publicReady.url,code:publicReady.pairing_code});
      report.public_origin_transport={state:result.passed?'passed':'failed',origin:publicOrigin,permission_grant:'explicit-test-context',
        no_grant_result:denied,...result,note:'Real HTTPS origin to loopback pairing; deployment of the VWB document is a separate gate.'};
    } catch(error) {
      report.public_origin_transport={state:'blocked',origin:publicOrigin,error:error.message};
    } finally {await publicContext.close()}
  }
} catch (error) {
  report.local_ui.error=error.message;
  if(currentPage) {
    report.local_ui.connection_status=await currentPage.locator('#connection-status').textContent().catch(()=>null);
    if(args['--screenshot']) await currentPage.screenshot({path:args['--screenshot'],fullPage:true}).catch(()=>{});
  }
} finally {
  await browser?.close();
  for(const child of children) {
    child.kill();
    await new Promise(resolve=>{if(child.exitCode!==null)return resolve(); const timer=setTimeout(()=>{child.kill('SIGKILL');resolve()},3000);child.once('exit',()=>{clearTimeout(timer);resolve()})});
  }
  if(staticServer) await new Promise(resolve=>staticServer.close(resolve));
  await fs.rm(temporary,{recursive:true,force:true});
}
const json=JSON.stringify(report,null,2)+'\n';
if(args['--output']) await fs.writeFile(args['--output'],json); else console.log(json);
if(!report.local_ui.passed || report.public_origin_transport.state==='failed') process.exitCode=1;
