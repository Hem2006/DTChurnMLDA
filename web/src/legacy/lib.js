// Pure helpers (unit-tested in lib.test.js). No business numbers live here: thresholds, costs and limits come from the API.

// A starting point for the form only; it is not a real customer and carries no business meaning.
export const DEFAULT_CUSTOMER = {
  SeniorCitizen: 0, Partner: 'No', Dependents: 'No', tenure: 12, PhoneService: 'Yes', MultipleLines: 'No', InternetService: 'Fiber optic',
  OnlineSecurity: 'No', OnlineBackup: 'No', DeviceProtection: 'No', TechSupport: 'No', StreamingTV: 'No', StreamingMovies: 'No',
  Contract: 'Month-to-month', PaperlessBilling: 'Yes', PaymentMethod: 'Electronic check', MonthlyCharges: 75,
}

export const ADDONS = ['OnlineSecurity', 'OnlineBackup', 'DeviceProtection', 'TechSupport', 'StreamingTV', 'StreamingMovies']
export const NO_INTERNET = 'No internet service'
export const NO_PHONE = 'No phone service'

/** Same rule as app.dash_logic.make_coherent: no internet -> add-ons n/a; no phone -> no extra lines. */
export function makeCoherent(c) {
  const out = { ...c }
  for (const a of ADDONS) {
    if (out.InternetService === 'No') out[a] = NO_INTERNET
    else if (out[a] === NO_INTERNET) out[a] = 'No'
  }
  if (out.PhoneService === 'No') out.MultipleLines = NO_PHONE
  else if (out.MultipleLines === NO_PHONE) out.MultipleLines = 'No'
  return out
}

export const LABELS = {
  SeniorCitizen: 'Senior citizen', Partner: 'Partner', Dependents: 'Dependents', tenure: 'Tenure (months)', PhoneService: 'Phone service',
  MultipleLines: 'Multiple lines', InternetService: 'Internet service', OnlineSecurity: 'Online security', OnlineBackup: 'Online backup',
  DeviceProtection: 'Device protection', TechSupport: 'Tech support', StreamingTV: 'Streaming TV', StreamingMovies: 'Streaming movies',
  Contract: 'Contract', PaperlessBilling: 'Paperless billing', PaymentMethod: 'Payment method', MonthlyCharges: 'Monthly charges',
  discount_pct: 'Discount %',
}
export const label = k => LABELS[k] || humanize(k)

export function humanize(s) {
  if (s == null) return ''
  const t = String(s).replace(/_/g, ' ')
  return t.charAt(0).toUpperCase() + t.slice(1)
}

export const pct = (x, d = 1) => (x == null || Number.isNaN(x) ? 'n/a' : (100 * x).toFixed(d) + '%')
export const num = (x, d = 3) => (x == null || Number.isNaN(x) ? 'n/a' : Number(x).toFixed(d))
export const signed = (x, d = 3) => (x == null ? 'n/a' : (x >= 0 ? '+' : '') + Number(x).toFixed(d))

export function changesText(changes) {
  const parts = Object.entries(changes || {}).map(([k, v]) => (k === 'discount_pct' ? `${v}% discount` : `${label(k)}: ${v}`))
  return parts.length ? parts.join(' · ') : 'No change'
}

export function missingColumns(header, required) {
  const have = new Set(header.map(h => String(h).trim()))
  return required.filter(c => !have.has(c))
}

export function chunk(arr, n) {
  const out = []
  for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n))
  return out
}

function csvCell(v) {
  if (v == null) return ''
  const s = String(v)
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s
}

export function toCsv(rows, columns) {
  return [columns.map(csvCell).join(','), ...rows.map(r => columns.map(c => csvCell(r[c])).join(','))].join('\n')
}

export function download(filename, text, type = 'text/csv') {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = Object.assign(document.createElement('a'), { href: url, download: filename })
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/** Gap = max - min of a metric across the groups of one attribute (same definition as app.fairness). */
export function fairnessGaps(subgroups, metrics, flagAt) {
  const by = {}
  for (const g of subgroups || []) (by[g.attribute] = by[g.attribute] || []).push(g)
  const rows = []
  for (const [attribute, groups] of Object.entries(by)) {
    for (const m of metrics) {
      const vals = groups.filter(g => g[m] != null)
      if (vals.length < 2) continue
      const hi = vals.reduce((a, b) => (b[m] > a[m] ? b : a))
      const lo = vals.reduce((a, b) => (b[m] < a[m] ? b : a))
      const gap = hi[m] - lo[m]
      rows.push({ attribute, metric: m, gap, highest: String(hi.group), lowest: String(lo.group), flagged: gap > flagAt, min_n: Math.min(...vals.map(g => g.n)) })
    }
  }
  return rows
}

const TRUE = new Set(['1', 'true', 'yes', 'y']), FALSE = new Set(['0', 'false', 'no', 'n'])

/** Account state from optional CSV columns. Blank or unrecognised = unknown (null), which forces human approval downstream. */
export function accountStateFromRow(row) {
  const tri = v => { const s = String(v ?? '').trim().toLowerCase(); return TRUE.has(s) ? true : FALSE.has(s) ? false : null }
  const n = v => { const s = String(v ?? '').trim(); if (s === '') return null; const x = Number(s); return Number.isFinite(x) && x >= 0 ? x : null }
  const st = { vulnerable: tri(row.vulnerable), escalated: tri(row.escalated), contacts_this_month: n(row.contacts_this_month),
    discount_pct_used_this_quarter: n(row.discount_pct_used_this_quarter) }
  if (st.contacts_this_month != null) st.contacts_this_month = Math.floor(st.contacts_this_month)
  if (st.discount_pct_used_this_quarter != null && st.discount_pct_used_this_quarter > 100) st.discount_pct_used_this_quarter = null
  return st
}

export const REF_RE = /^[A-Za-z0-9_-]{1,64}$/
