import { useCallback, useEffect, useState } from 'react'
import { Check, ChevronDown, ChevronRight, CornerUpLeft, Loader2, X } from 'lucide-react'
import { api } from '../api.js'
import { ago, approvalReasons, optionLabel, pctRisk, pts, stageText } from '../lib.js'

const ALLOWED = { permitted: ['Allowed', 'ok'], needs_human_approval: ['Needs approval', 'warn'], blocked: ['Blocked', 'bad'] }

/** What the twin tested for this case, so the manager decides on evidence rather than on the analyst's word. */
function Evidence({ id }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { api(`/workflow/cases/${id}`).then(setD).catch(e => setErr(e.message)) }, [id])
  if (err) return <p className="err">{err}</p>
  if (!d) return <p className="muted small"><Loader2 className="spin" size={14} /> Loading what the twin tested…</p>
  const rows = d.scenarios?.rows || []
  return (
    <div className="evidence">
      {rows.length ? (
        <table className="tbl options">
          <thead><tr><th>Option the twin tried</th><th className="num">Likelihood after</th><th className="num">Change</th><th>Contact rules</th></tr></thead>
          <tbody>
            {rows.map(r => {
              const [al, at] = ALLOWED[r.guardrails] || [r.guardrails, 'muted']
              return (
                <tr key={r.scenario} className={r.chosen ? 'hl' : ''}>
                  <td>{optionLabel(r.scenario)}{r.chosen && <span className="pill tone-ok">proposed</span>}{r.confidence === 'low' && <span className="caution" title={r.warning}> ⚠</span>}</td>
                  <td className="num">{pctRisk(r.risk_after)}</td>
                  <td className={'num ' + (r.risk_drop > 0 ? 'good' : 'bad')}>{pts(r.risk_now, r.risk_after)}</td>
                  <td><span className={'pill tone-' + at}>{al}</span></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      ) : <p className="muted small">No twin results were stored for this case.</p>}
      <p className="tiny muted">A what-if on the model, not proof that the change keeps the customer. ⚠ = a simpler check model sees a much smaller effect.</p>
      {d.events?.length > 0 && <ul className="events small">{d.events.map((e, i) => <li key={i}><b>{e.actor || 'Agent'}</b> · {e.event.replace(/_/g, ' ')}{e.detail ? ` — ${e.detail}` : ''} <span className="muted">{ago(e.ts)}</span></li>)}</ul>}
    </div>
  )
}

function Card({ c, onDone }) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState(null)             // null | 'back' | 'reject'
  const [reason, setReason] = useState('')
  const [first, setFirst] = useState(c.first_name || '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const go = async (path, body) => {
    setBusy(true); setErr(null)
    try { await api(`/workflow/cases/${c.id}/${path}`, body); onDone() } catch (e) { setErr(e.message); setBusy(false) }
  }
  return (
    <li className="card appr">
      <div className="appr-h">
        <div><b>{c.first_name || c.customer_ref}</b> {c.first_name && <small className="muted">{c.customer_ref}</small>}<p className="sub">Case #{c.id} · sent by <b>{c.submitted_by}</b> {ago(c.submitted_ts)}</p></div>
        <div className="appr-risk"><span>Likelihood of leaving</span><b>{pctRisk(c.probability)}</b></div>
      </div>
      <p className="appr-offer">Proposed: <b>{optionLabel(c.action_name)}</b></p>
      <blockquote className="msg handoff"><span className="tiny muted">Analyst's note</span>{c.submit_note}</blockquote>
      <ul className="plain small">{approvalReasons(c.approvals_needed).map(r => <li key={r}>{r}</li>)}</ul>
      <button className="linkish" onClick={() => setOpen(!open)}>{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />} What the digital twin tested</button>
      {open && <Evidence id={c.id} />}
      {!c.first_name && <input className="input" placeholder="Customer's first name (needed for the message)" value={first} onChange={e => setFirst(e.target.value)} maxLength={40} />}
      <div className="row-btns">
        <button className="btn primary" disabled={busy || (!c.first_name && !first.trim())} onClick={() => go('approve', first.trim() && !c.first_name ? { first_name: first.trim() } : {})}>
          {busy ? <Loader2 className="spin" size={15} /> : <Check size={15} />} Approve
        </button>
        <button className="btn ghost" disabled={busy} onClick={() => setMode(mode === 'back' ? null : 'back')}><CornerUpLeft size={15} /> Send back</button>
        <button className="btn ghost" disabled={busy} onClick={() => setMode(mode === 'reject' ? null : 'reject')}><X size={15} /> Reject</button>
      </div>
      {mode && (
        <div className="reject">
          <input className="input" autoFocus placeholder={mode === 'back' ? 'What should the analyst fix or check?' : 'Why is this being rejected?'} value={reason} onChange={e => setReason(e.target.value)} maxLength={500} />
          <button className="btn ghost" disabled={busy || !reason.trim()} onClick={() => go(mode === 'back' ? 'send-back' : 'reject', { reason: reason.trim() })}>Confirm {mode === 'back' ? 'send back' : 'reject'}</button>
        </div>
      )}
      {err && <p className="err">{err}</p>}
    </li>
  )
}

export default function Review({ user, onChange }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const load = useCallback(() => api('/workflow/board').then(setData).catch(e => setErr(e.message)), [])
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t) }, [load])
  const done = () => { load(); onChange() }
  const cases = data?.cases || []
  const waiting = cases.filter(c => c.stage === 'manager')
  const mine = cases.filter(c => c.approved_uid === user.id).slice(0, 25)

  return (
    <div className="page">
      <div className="page-h">
        <div>
          <h1>Approval queue</h1>
          <p className="sub">Cases the analyst has sent you. Approving releases the offer to be drafted into a message, which then goes to compliance. You cannot approve a case you sent yourself, and you never see a customer's contact details.</p>
        </div>
      </div>
      {err && <p className="err">{err}</p>}
      {!data ? <div className="card empty"><Loader2 className="spin" size={20} /></div> : (
        <div className="appr-grid">
          <section>
            <h2 className="sec-h">Waiting for you <span className="count">{waiting.length}</span></h2>
            {!waiting.length && <div className="card empty"><p className="sub">Nothing waiting. The analyst sends cases here after studying the twin.</p></div>}
            <ul className="appr-list">{waiting.map(c => <Card key={c.id} c={c} onDone={done} />)}</ul>
          </section>
          <section>
            <h2 className="sec-h">Approved by you</h2>
            <div className="card">
              <ul className="hist">
                {mine.map(c => { const [l, t] = stageText(c.stage); return <li key={c.id}><span>{c.first_name || c.customer_ref} · {optionLabel(c.action_name)}</span><span className={'pill tone-' + t}>{l}</span></li> })}
                {!mine.length && <li className="muted small">Nothing approved yet.</li>}
              </ul>
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
