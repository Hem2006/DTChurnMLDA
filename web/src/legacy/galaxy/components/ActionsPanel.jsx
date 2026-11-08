import { useState } from 'react'
import { AlertTriangle, Check, Crosshair, Loader2, RotateCw, Workflow, X } from 'lucide-react'
import { pctRisk, signed } from '../lib.js'

export const STATUS = {
  pending_approval: ['Awaiting approval', 'warn'],
  approved: ['Approved · waiting for n8n', 'info'],
  sent: ['Sent to n8n', 'info'],
  done: ['Done', 'ok'],
  failed: ['n8n reported a problem', 'bad'],
  rejected: ['Rejected', 'muted'],
  delivery_failed: ['Could not reach n8n', 'bad'],
}

/** Ring colour per status on the galaxy (rejected actions are closed and get no ring). */
export const MARK_COLOR = { pending_approval: '#ffe492', approved: '#4f9cf9', sent: '#4f9cf9', delivery_failed: '#ff8a4c', done: '#19e3c1', failed: '#ff3d71' }

export function WhoAmI({ who, setWho }) {
  return (
    <label className="who">
      <span>Your name</span>
      <input value={who} onChange={e => setWho(e.target.value.slice(0, 80))} placeholder="e.g. Riya" maxLength={80} aria-label="Your name" />
    </label>
  )
}

function Card({ a, who, busy, onApprove, onReject, onRetry, onFocus }) {
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const [label, kind] = STATUS[a.status] || [a.status, 'muted']
  const last = a.events && a.events.length ? a.events[a.events.length - 1] : null
  return (
    <li className="act">
      <div className="act-top">
        <button className="act-ref" onClick={() => onFocus(a.node_id)} title="Show this customer in the galaxy"><Crosshair size={13} /> {a.customer_ref}</button>
        <span className={'tag st-' + kind}>{label}</span>
      </div>
      <p className="act-change">{a.changes_text}</p>
      <p className="act-risk">{pctRisk(a.risk_before, 1)} → <b>{pctRisk(a.risk_after, 1)}</b> <span>({signed(-a.risk_drop * 100, 1)} pts, model sensitivity)</span></p>
      <p className="foot">Proposed by {a.requested_by}{a.decided_by ? ` · ${a.status === 'rejected' ? 'rejected' : 'approved'} by ${a.decided_by}` : ''}</p>
      {a.decided_note && <p className="foot">“{a.decided_note}”</p>}
      {a.detail && <p className="act-detail">n8n: {a.detail}{a.external_ref ? ` (${a.external_ref})` : ''}</p>}
      {a.last_error && a.status === 'delivery_failed' && <p className="err"><AlertTriangle size={13} /> {a.last_error}</p>}
      {a.status === 'pending_approval' && a.approvals_needed.length > 0 && (
        <details className="foot"><summary>Why a person must approve</summary><ul>{a.approvals_needed.map(w => <li key={w}>{w}</li>)}</ul></details>
      )}
      {a.status === 'pending_approval' && !rejecting && (
        <div className="row-btns">
          <button className="btn primary" disabled={busy || !who.trim()} onClick={() => onApprove(a.id)}><Check size={14} /> Approve</button>
          <button className="btn ghost" disabled={busy || !who.trim()} onClick={() => setRejecting(true)}><X size={14} /> Reject</button>
        </div>
      )}
      {a.status === 'pending_approval' && rejecting && (
        <div className="reject">
          <input value={reason} onChange={e => setReason(e.target.value)} placeholder="Why? (required)" maxLength={500} autoFocus />
          <button className="btn ghost" disabled={busy || !reason.trim()} onClick={() => { onReject(a.id, reason.trim()); setRejecting(false) }}>Confirm</button>
          <button className="icon" onClick={() => setRejecting(false)} aria-label="Cancel"><X size={14} /></button>
        </div>
      )}
      {(a.status === 'delivery_failed' || a.status === 'approved') && (
        <div className="row-btns"><button className="btn ghost" disabled={busy} onClick={() => onRetry(a.id)}><RotateCw size={14} /> Send to n8n again</button></div>
      )}
      {a.status === 'pending_approval' && !who.trim() && <p className="foot">Enter your name above to approve or reject.</p>}
      {last && a.status !== 'pending_approval' && <p className="foot">Last event: {last.event.replace(/_/g, ' ')}</p>}
    </li>
  )
}

export default function ActionsPanel({ data, who, setWho, busy, error, onApprove, onReject, onRetry, onFocus, onClose }) {
  const [filter, setFilter] = useState('all')
  const list = (data?.actions || []).filter(a => (filter === 'open' ? ['pending_approval', 'approved', 'sent', 'delivery_failed'].includes(a.status) : true))
  const auto = data?.automation
  return (
    <section className="panel left actions" aria-label="Actions">
      <header className="panel-h">
        <div><div className="eyebrow"><Workflow size={12} /> Actions · n8n</div><h2>Do something about it</h2></div>
        <button className="icon" onClick={onClose} aria-label="Close"><X size={16} /></button>
      </header>
      <p className="sub">Propose a change from a customer's twin. A named person approves it here, then n8n carries it out and reports back.</p>
      {auto && !auto.configured && <p className="note warn">{auto.hint}</p>}
      {auto && auto.configured && <p className="foot">Connected to n8n at <b>{auto.webhook_host}</b>. Nothing is sent to a customer from this app.</p>}
      <WhoAmI who={who} setWho={setWho} />
      <div className="seg">
        <button className={filter === 'open' ? 'on' : ''} onClick={() => setFilter('open')}>Open</button>
        <button className={filter === 'all' ? 'on' : ''} onClick={() => setFilter('all')}>All</button>
      </div>
      {error && <p className="err">{error.message}</p>}
      {!data && <p className="muted"><Loader2 className="spin" size={14} /> Loading…</p>}
      {data && !list.length && <p className="muted pad">Nothing here yet. Click a customer, edit their twin and choose <b>Propose this action</b>.</p>}
      <ul className="acts">
        {list.map(a => <Card key={a.id} a={a} who={who} busy={busy} onApprove={onApprove} onReject={onReject} onRetry={onRetry} onFocus={onFocus} />)}
      </ul>
    </section>
  )
}
