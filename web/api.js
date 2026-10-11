// Session credentials live in memory only. No credential URLs or browser storage.
export const MAX_TEXT = 262144;
export class ApiError extends Error {
  constructor(error, status = 0) {
    super(error?.message || 'The service could not complete this request.');
    this.code = error?.code || 'connection_unavailable';
    this.requirements = Array.isArray(error?.requirements) ? error.requirements : [];
    this.status = status;
  }
}
export class GarageAPI {
  constructor(fetcher = globalThis.fetch.bind(globalThis)) { this.fetcher = fetcher; this.csrf = null; }
  async request(path, body, extraHeaders = {}) {
    let response;
    try {
      response = await this.fetcher(path, {method:body === undefined ? 'GET' : 'POST', credentials:'same-origin',
        cache:'no-store', headers:{'Content-Type':'application/json', ...(this.csrf ? {'X-CSRF-Token':this.csrf} : {}), ...extraHeaders},
        ...(body === undefined ? {} : {body:JSON.stringify(body)})});
    } catch { throw new ApiError({code:'connection_unavailable',message:'Cannot reach the private service.',requirements:['Check the SSH tunnel and dashboard service. No action is confirmed.']}); }
    let envelope;
    try { envelope = await response.json(); } catch { throw new ApiError({code:'invalid_response',message:'The service returned an unreadable response.'},response.status); }
    if (!response.ok || envelope.ok !== true) throw new ApiError(envelope.error,response.status);
    return envelope.result;
  }
  async resume() { const result = await this.request('/api/session'); this.csrf = result.csrf; return result; }
  async login(token) { const result = await this.request('/api/login',{token}); this.csrf = result.csrf; return result; }
  async logout() { await this.request('/api/logout',{}); this.csrf = null; }
  status() { return this.request('/api/status'); }
  operation(operation, args = {}) { return this.request('/api/operations',{operation,args}); }
}
export function validateFile(file) {
  if (!file || file.size > MAX_TEXT) throw new ApiError({code:'file_limit',message:'Select a UTF-8 text file no larger than 256 KiB.'});
  if (!/^[\w .-]{1,120}$/.test(file.name) || ['.','..'].includes(file.name)) throw new ApiError({code:'filename',message:'Use a filename of at most 120 letters, numbers, spaces, dots, hyphens or underscores.'});
}
export async function readTextFile(file) {
  validateFile(file);
  let text;
  // ignoreBOM=true keeps U+FEFF in the decoded text, preserving source bytes
  // when the backend re-encodes UTF-8 for the original file and SHA256 provenance.
  try { text = new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await file.arrayBuffer()); }
  catch { throw new ApiError({code:'file_encoding',message:'Export the source as valid UTF-8 text.'}); }
  if (text.includes('\0') || text.split(/\r?\n/).filter(line => /^[+-]?(?:\d|\.\d)/.test(line.trim())).length > 20000)
    throw new ApiError({code:'file_limit',message:'Text must contain no binary bytes and at most 20,000 numeric rows.'});
  return text;
}
