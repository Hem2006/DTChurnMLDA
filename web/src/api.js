// Thin fetch wrapper. Same-origin in production (FastAPI serves this app); proxied to :8000 by Vite in development.
const KEY = 'evergreen.apiKey'

export function getApiKey() {
  try { return sessionStorage.getItem(KEY) || '' } catch { return '' }
}

export function setApiKey(v) {
  try { v ? sessionStorage.setItem(KEY, v) : sessionStorage.removeItem(KEY) } catch { /* storage blocked: key lives for this page only */ }
}

export function formatDetail(d) {
  if (d == null) return 'Request failed'
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map(e => (e.loc ? e.loc.filter(x => x !== 'body').join('.') + ': ' : '') + e.msg).join('; ')
  if (typeof d === 'object') {
    const parts = [d.error, d.reasons && d.reasons.join(' '), d.approvals_needed && 'Needs approval: ' + d.approvals_needed.join('; ')]
    return parts.filter(Boolean).join('. ') || JSON.stringify(d)
  }
  return String(d)
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(formatDetail(detail))
    this.status = status
    this.detail = detail
  }
}

export async function api(path, body) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const k = getApiKey()
  if (k) headers['X-API-Key'] = k
  let r
  try {
    r = await fetch(path, { method: body !== undefined ? 'POST' : 'GET', headers, body: body !== undefined ? JSON.stringify(body) : undefined })
  } catch {
    throw new ApiError(0, 'Cannot reach the Evergreen API. Is the server running?')
  }
  let data = null
  try { data = await r.json() } catch { /* empty body */ }
  if (!r.ok) throw new ApiError(r.status, data && data.detail !== undefined ? data.detail : r.statusText)
  return data
}
