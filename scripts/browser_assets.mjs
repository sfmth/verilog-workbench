// Read original Chromium response bodies while their static response is paused,
// before a short-lived worker can execute and terminate its debugging target.
// Native companion traffic never matches the protocol interception pattern.
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';

export const SITE_ASSET_CAPTURE_METHOD = 'original-browser-network-entity-bytes-paused-before-unchanged-CDP-continueResponse';
const helperSourceSHA256 = createHash('sha256').update(await readFile(new URL(import.meta.url))).digest('hex');

export async function captureSiteAssets(context, {origin, prefix, onAsset, onError, timeout = 30000}) {
  const site = new URL(origin);
  if (!['http:', 'https:'].includes(site.protocol) || site.origin !== origin || !prefix.startsWith('/') || !prefix.endsWith('/') || /[?*\\]/.test(origin + prefix) || !Number.isFinite(timeout) || timeout <= 0) {
    throw new Error('Expected an exact static origin, literal absolute directory prefix and positive timeout.');
  }
  const assets = new Map(), counts = new Map(), errors = [], pending = new Set(), attachments = new Map();
  const sessions = new Set();
  let delivered = 0, entityBytes = 0, closed = false;
  const recordError = error => { errors.push(error); onError?.(error); };
  const attachPage = page => {
    if (attachments.has(page)) return attachments.get(page);
    if (closed) throw new Error('Cannot attach a page after static capture has closed.');
    const attaching = (async () => {
      const session = await context.newCDPSession(page);
      const paused = event => {
        let asset = event.request.url;
        const task = (async () => {
          let record;
          try {
            const url = new URL(event.request.url);
            if (url.origin !== origin || !url.pathname.startsWith(prefix)) throw new Error('The protocol intercepted a request outside the static site scope.');
            const relative = decodeURIComponent(url.pathname.slice(prefix.length));
            asset = relative.endsWith('/') ? relative + 'index.html' : relative;
            if (asset.endsWith('favicon.ico')) return;
            if (event.responseErrorReason) throw new Error('Original static network response failed: ' + event.responseErrorReason);
            if (event.responseStatusCode !== 200) { recordError({path: asset, status: event.responseStatusCode}); return; }
            const captured = await session.send('Fetch.getResponseBody', {requestId: event.requestId});
            const body = Buffer.from(captured.body, captured.base64Encoded ? 'base64' : 'utf8');
            record = {path: asset, sha256: createHash('sha256').update(body).digest('hex'), bytes: body.length, status: event.responseStatusCode};
          } catch (error) {
            recordError({path: asset, error: error instanceof Error ? error.message : String(error)});
          } finally {
            // No response headers, status or body overrides; this resumes the
            // same original network response, including content encoding.
            try { await session.send('Fetch.continueResponse', {requestId: event.requestId}); }
            catch (error) { record = null; recordError({path: asset, phase: 'continue-original-response', error: error instanceof Error ? error.message : String(error)}); }
          }
          if (record) {
            delivered += 1; entityBytes += record.bytes;
            if (asset !== 'vwb-web-build.json') {
              assets.set(asset, record.sha256);
              counts.set(asset, (counts.get(asset) || 0) + 1);
              onAsset?.(record);
            }
          }
        })();
        pending.add(task);
        task.then(() => pending.delete(task), error => { pending.delete(task); recordError({path: asset, phase: 'capture-handler', error: error instanceof Error ? error.message : String(error)}); });
      };
      session.on('Fetch.requestPaused', paused);
      sessions.add({session, paused});
      // Playwright route() enables '*' internally and can auto-answer unrelated
      // native CORS preflights. This direct protocol pattern is site-only.
      await session.send('Fetch.enable', {patterns: [{urlPattern: origin + prefix + '*', requestStage: 'Response'}]});
      return session;
    })();
    attachments.set(page, attaching);
    return attaching;
  };
  const observePage = page => { attachPage(page).catch(error => recordError({phase: 'attach-static-response-capture', error: error instanceof Error ? error.message : String(error)})); };
  context.on('page', observePage);
  for (const page of context.pages()) await attachPage(page);
  const drain = async () => {
    await Promise.all(attachments.values());
    const deadline = Date.now() + timeout;
    while (pending.size) {
      if (Date.now() >= deadline) throw new Error('Timed out draining original static response capture.');
      await new Promise(resolve => setTimeout(resolve, 20));
    }
  };
  return {
    assets, counts, errors, attachPage, drain,
    summary: () => ({method: SITE_ASSET_CAPTURE_METHOD, source_sha256: helperSourceSHA256, browser_http_cache: 'harness-does-not-change-cache-settings', protocol_scope: origin + prefix + '*', protocol_stage: 'Response', original_network_responses: true, request_refetches: 0, response_overrides: 0, delivered_responses: delivered, delivered_entity_bytes: entityBytes, pending_responses: pending.size}),
    close: async () => {
      if (closed) return;
      closed = true;
      context.off('page', observePage);
      try { await drain(); }
      finally {
        for (const {session, paused} of sessions) {
          session.off('Fetch.requestPaused', paused);
          // Disabling Fetch resumes any new response paused after the final
          // acceptance drain; no body reader is left on a closing context.
          await session.send('Fetch.disable');
          await session.detach();
        }
      }
    },
  };
}
