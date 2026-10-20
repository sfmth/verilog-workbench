import {highlight, languageForPath} from './syntax.mjs';

function validId(id) {
  return (Number.isSafeInteger(id) && id >= 0) || (typeof id === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(id));
}

self.onmessage = ({data}) => {
  const id = validId(data?.id) ? data.id : null;
  if (id === null || typeof data?.text !== 'string' || typeof data?.path !== 'string' || data.path.length > 4096) {
    self.postMessage({id, mode: 'plain', language: 'plain', reason: 'Invalid syntax decoration request.', line_count: 0, tokens: []});
    return;
  }
  try {
    self.postMessage({id, ...highlight(data.text, languageForPath(data.path))});
  } catch {
    // Never echo source text, exception details, or arbitrary request fields.
    self.postMessage({id, mode: 'plain', language: 'plain', reason: 'Syntax decoration is unavailable.', line_count: 0, tokens: []});
  }
};
