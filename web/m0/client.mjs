export function localEndpoint(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) ||
      !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) ||
      url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new Error('Enter a loopback companion address, without a path or credentials.');
  }
  return url.origin;
}

export class CompanionClient {
  constructor(endpoint, fetcher = globalThis.fetch) {
    this.endpoint = localEndpoint(endpoint);
    this.fetcher = (...args) => fetcher.call(globalThis, ...args);
    this.token = null;
  }
  forget() { this.token = null; }
  async request(path, {method = 'GET', body, authenticated = true, responseType = 'json'} = {}) {
    if (!['json', 'blob'].includes(responseType)) throw new Error('Invalid response type.');
    if (!path.startsWith('/v1/') || path.includes('://')) throw new Error('Invalid protocol path.');
    if (authenticated && !this.token) throw new Error('Pair the companion first.');
    const headers = {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (authenticated) headers.Authorization = `Bearer ${this.token}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await this.fetcher(this.endpoint + path, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal, mode: 'cors', credentials: 'omit',
        cache: 'no-store', referrerPolicy: 'no-referrer', targetAddressSpace: 'loopback',
      });
      if (!response.ok) {
        const data = await response.json();
        const error = new Error(data.error || `Companion request failed (${response.status}).`);
        error.status = response.status;
        throw error;
      }
      return responseType === 'blob' ? await response.blob() : await response.json();
    } finally { clearTimeout(timer); }
  }
  async pair(code) {
    const data = await this.request('/v1/pair', {method:'POST', body:{code}, authenticated:false});
    if (data.protocol !== 1) {
      throw new Error('This companion uses a different protocol version.');
    }
    if (typeof data.token !== 'string' || !data.token) throw new Error('Missing session credential.');
    this.token = data.token;
    return data;
  }
}
