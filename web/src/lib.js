// Pure helpers for the web app (tested in lib.test.js). No DOM, no network.
// The survival / colour / filter helpers below were written for the Customer Galaxy (now in legacy/galaxy) and are kept for it.

// ---- retention app: plain-language labels (no model jargon on screen)
export const OPTION_LABELS = {
  do_nothing: 'Do nothing',
  price_cut_10: '10% off the monthly bill',
  contract_one_year: 'Move to a one-year contract',
  add_tech_support: 'Add tech support',
  combined_package: 'Bundle: one-year contract + tech support + 5% off',
}
export const optionLabel = name => (name == null ? '–' : OPTION_LABELS[name] || String(name).replace(/_/g, ' '))

export const CASE_STATUS = {
  pending_approval: ['Waiting for approval', 'warn'],
  needs_first_name: ['Approved · needs first name', 'info'],
  ready: ['Message ready for CRM', 'ok'],
  exported: ['Handed to CRM', 'ok'],
  rejected: ['Rejected', 'muted'],
  escalated: ['Handed to a person', 'bad'],
  care_handoff: ['Passed to the care team', 'info'],
  no_action: ['No action', 'muted'],
  invalid: ['Unusable row', 'bad'],
}
/** [text, tone] for a review-queue status; tone is ok | warn | info | bad | muted. */
export function caseStatusText(status) {
  if (status == null) return ['Not worked on this run', 'muted']
  return CASE_STATUS[status] || [String(status).replace(/_/g, ' '), 'muted']
}

// ---- the four roles and where a case is in the hand-off chain (stage names come from the server, app/workflow.py)
export const ROLE_TEXT = {
  analyst: ['Retention analyst', 'Runs the agent, studies each twin and sends cases to the manager.'],
  manager: ['Retention manager', 'Approves, sends back or rejects what the analyst sends.'],
  compliance: ['Compliance & care', 'Checks the approved message, releases it to the CRM and looks after protected accounts.'],
  admin: ['System admin', 'Watches the system, the business numbers, the people and the audit trail.'],
}
export const STAGE_TEXT = {
  analyst: ['With the analyst', 'warn'],
  manager: ['Waiting for the manager', 'info'],
  name: ['Needs the first name', 'info'],
  compliance: ['Waiting for compliance', 'info'],
  crm_ready: ['Cleared · ready for the CRM', 'ok'],
  care: ['Protected account · with compliance', 'bad'],
  closed: ['Closed', 'muted'],
}
/** [text, tone] for a hand-off stage. */
export const stageText = stage => STAGE_TEXT[stage] || [String(stage).replace(/_/g, ' '), 'muted']
/** Which stages need work from each role; the header badge adds them up. */
export const ROLE_QUEUE = { analyst: ['analyst'], manager: ['manager'], compliance: ['compliance', 'care'], admin: [] }
export const workCount = (role, counts) => (ROLE_QUEUE[role] || []).reduce((a, k) => a + ((counts || {})[k] || 0), 0)

/** Guardrail approval reasons said plainly. Unknown text is passed through unchanged. */
export function approvalText(s) {
  const t = String(s)
  if (/vulnerable/i.test(t)) return "We don't know whether this customer is vulnerable"
  if (/escalated/i.test(t)) return "We don't know whether this account is in an escalation"
  if (/max_contacts/i.test(t)) return 'No monthly contact limit has been set yet'
  if (/max_discount/i.test(t)) return 'No discount limit has been set yet'
  if (/contacts? this month/i.test(t)) return 'Contact count this month is unknown'
  return t
}

/** De-duplicated plain approval reasons for a case. */
export const approvalReasons = list => [...new Set((list || []).map(approvalText))]

/** CSV text for the given rows and columns (RFC 4180 quoting). */
export function toCsv(rows, columns) {
  const q = v => {
    const s = v == null ? '' : String(v)
    return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s
  }
  return [columns.join(','), ...rows.map(r => columns.map(c => q(r[c])).join(','))].join('\r\n')
}

/** Short age for an ISO time, e.g. "just now", "4 min ago", "2 h ago", or the date. */
export function ago(iso, now = Date.now()) {
  if (!iso) return '–'
  const s = Math.max(0, (now - new Date(iso).getTime()) / 1000)
  if (s < 45) return 'just now'
  if (s < 3600) return Math.round(s / 60) + ' min ago'
  if (s < 86400) return Math.round(s / 3600) + ' h ago'
  return new Date(iso).toLocaleDateString()
}

/** Risk change in percentage points, as text: "−12.3 pts". */
export const pts = (before, after) => (before == null || after == null ? '–' : signed((after - before) * 100, 1) + ' pts')

// Risk ramp: mint (safe) -> blue -> brand yellow -> orange -> hot pink (about to leave).
export const RAMP = [
  [0.0, '#19e3c1'], [0.25, '#4f9cf9'], [0.5, '#ffe492'], [0.75, '#ff8a4c'], [1.0, '#ff3d71'],
]

export function hexToRgb(hex) {
  const n = parseInt(hex.replace('#', ''), 16)
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]
}

const STOPS = RAMP.map(([t, h]) => [t, hexToRgb(h)])

/** Colour on the ramp for a value in [0,1] (clamped), as [r,g,b] in 0..1. */
export function rampColor(v) {
  const x = Math.min(1, Math.max(0, Number.isFinite(v) ? v : 0))
  for (let i = 1; i < STOPS.length; i++) {
    if (x <= STOPS[i][0]) {
      const [t0, c0] = STOPS[i - 1], [t1, c1] = STOPS[i]
      const k = (x - t0) / (t1 - t0)
      return [c0[0] + (c1[0] - c0[0]) * k, c0[1] + (c1[1] - c0[1]) * k, c0[2] + (c1[2] - c0[2]) * k]
    }
  }
  return STOPS[STOPS.length - 1][1]
}

// Risk is rarely above 0.85, so stretch the ramp a little so the whole range of colours shows up.
export const riskColor = p => rampColor(p / 0.9)

export const CONTRACT_COLORS = { 'Month-to-month': '#ff5d8f', 'One year': '#4f9cf9', 'Two year': '#19e3c1' }

// ILLUSTRATIVE timeline: the model probability p is read as "leaves within `horizon` months", constant monthly hazard.
export const HORIZON = 12

/** Probability the customer is still here after `m` months. */
export function survival(p, m, horizon = HORIZON) {
  const q = Math.min(1, Math.max(0, p))
  return Math.pow(1 - q, Math.min(Math.max(m, 0), horizon) / horizon)
}

/** True if a customer with fixed random draw u (0..1) has left by month m. The same u is used in every world, so worlds are comparable. */
export function leftBy(u, p, m, horizon = HORIZON) {
  return u < 1 - survival(p, m, horizon)
}

/** Lost-customer mask and totals at month m for one world (arrays of equal length). */
export function lostAt(us, ps, mcs, m, horizon = HORIZON) {
  const mask = new Uint8Array(ps.length)
  let lost = 0, mrr = 0
  for (let i = 0; i < ps.length; i++) {
    if (leftBy(us[i], ps[i], m, horizon)) { mask[i] = 1; lost++; mrr += mcs[i] }
  }
  return { mask, lost, mrr }
}

/** Expected customers still here at months 0..horizon (sum of survival probabilities). */
export function cohortCurve(ps, horizon = HORIZON) {
  const out = []
  for (let m = 0; m <= horizon; m++) {
    let s = 0
    for (let i = 0; i < ps.length; i++) s += survival(ps[i], m, horizon)
    out.push(s)
  }
  return out
}

/** Expected monthly revenue still arriving at months 0..horizon for one customer. */
export function revenueCurve(p, monthly, horizon = HORIZON) {
  const out = []
  for (let m = 0; m <= horizon; m++) out.push(survival(p, m, horizon) * monthly)
  return out
}

// ---- formatting
export const pct = (x, d = 0) => (x == null || !Number.isFinite(x) ? '–' : (x * 100).toFixed(d) + '%')
// Isotonic calibration can output exactly 0 or 1; never show that as certainty.
export const pctRisk = (x, d = 0) => (x == null || !Number.isFinite(x) ? '–' : x >= 0.995 ? '>99%' : x < 0.005 ? '<1%' : pct(x, d))
export const num = (x, d = 0) => (x == null || !Number.isFinite(x) ? '–' : Number(x).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }))
export const money = (x, d = 0) => (x == null || !Number.isFinite(x) ? '–' : '$' + num(x, d))
export const signed = (x, d = 0) => (x == null || !Number.isFinite(x) ? '–' : (x > 0 ? '+' : x < 0 ? '−' : '') + num(Math.abs(x), d))

export function humanize(s) {
  return String(s).replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_/g, ' ')
}

/** "Contract → One year · Discount 10%" for a changes dict. */
export function changesText(ch) {
  const parts = Object.entries(ch || {}).map(([k, v]) => (k === 'discount_pct' ? `Discount ${v}%` : `${humanize(k)} → ${v}`))
  return parts.length ? parts.join(' · ') : 'No change'
}

/** Keep only the edits that differ from the customer's current values (so "undoing" an edit removes it). */
export function setEdit(edits, customer, key, value) {
  const next = { ...edits }
  const current = key === 'discount_pct' ? 0 : customer[key]
  if (value === current) delete next[key]
  else next[key] = value
  return next
}

/** Which nodes pass the filter. Returns a Uint8Array (1 = shown). */
export function filterMask(nodes, f, threshold) {
  const mask = new Uint8Array(nodes.length)
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i]
    mask[i] = (!f.contracts.size || f.contracts.has(n.c.Contract)) && (!f.internet.size || f.internet.has(n.c.InternetService))
      && (!f.flaggedOnly || n.p >= threshold) ? 1 : 0
  }
  return mask
}
