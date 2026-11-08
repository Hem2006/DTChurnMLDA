import { useCallback, useEffect, useState } from 'react'
import { Check, Download, Loader2, X } from 'lucide-react'
import { api } from '../api.js'
import { ago, approvalReasons, caseStatusText, optionLabel, pctRisk, toCsv } from '../lib.js'

const OUTBOX_COLUMNS = ['case_id', 'customer_ref', 'first_name', 'offer_name', 'offer_description', 'message', 'approved_by', 'message_log_id']

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }))
  const a = Object.assign(document.createElement('a'), { href: url, download: name })
  document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function Pending({ c, who, onDone }) {
  const [first, setFirst] = useState(c.first_name || '')
  const [reason, setReason] = useState('')
  const [rejecting, setRejecting] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const go = async (path, body) => {
    setBusy(true); setErr(null)
    try { await api(path, body); onDone() } catch (e) { setErr(e.message); setBusy(false) }
  }
  return (
    <li className="card appr">
      <div className="appr-h">
        <div><b>{c.first_name || c.customer_ref}</b> {c.first_name && <small className="muted">{c.customer_ref}</small>}<p className="sub">Case #{c.id} · queued {ago(c.ts)}</p></div>
        <div className="appr-risk"><span>Likelihood of leaving</span><b>{pctRisk(c.probability)}</b></div>
      </div>
      <p className="appr-offer">Proposed: <b>{optionLabel(c.action_name)}</b></p>
      <ul className="plain small">{approvalReasons(c.approvals_needed).map(r => <li key={r}>{r}</li>)}</ul>
      {!c.first_name && <input className="input" placeholder="Customer's first name (for the message)" value={first} onChange={e => setFirst(e.target.value)} maxLength={40} />}
      <div className="row-btns">
        <button className="btn primary" disabled={busy} onClick={() => go(`/cases/${c.id}/approve`, { approved_by: who, ...(first.trim() && !c.first_name ? { first_name: first.trim() } : {}) })}>
          {busy ? <Loader2 className="spin" size={15} /> : <Check size={15} />} Approve
        </button>
        <button className="btn ghost" disabled={busy} onClick={() => setRejecting(!rejecting)}><X size={15} /> Reject</button>
      </div>
      {rejecting && (
        <div className="reject">
          <input className="input" autoFocus placeholder="Why? (kept on the case)" value={reason} onChange={e => setReason(e.target.value)} maxLength={500} />
          <button className="btn ghost" disabled={busy || !reason.trim()} onClick={() => go(`/cases/${c.id}/reject`, { rejected_by: who, reason: reason.trim() })}>Confirm reject</button>
        </div>
      )}
      {err && <p className="err">{err}</p>}
    </li>
  )
}

export default function Approvals({ who, onChange }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [exp, setExp] = useState(null)
  const load = useCallback(() => api('/cases?limit=400').then(setData).catch(e => setErr(e.message)), [])
  useEffect(() => { load() }, [load])
  const done = () => { load(); onChange() }
  const cases = data?.cases || []
  const pending = cases.filter(c => c.status === 'pending_approval')
  const ready = cases.filter(c => c.status === 'ready' || c.status === 'needs_first_name')
  const history = cases.filter(c => ['exported', 'rejected', 'escalated'].includes(c.status)).slice(0, 30)

  const exportNow = async () => {
    setExp('busy')
    try {
      const r = await api('/outbox/export', {})
      if (r.count) download(`evergreen_outbox_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`, toCsv(r.messages, OUTBOX_COLUMNS))
      setExp(`${r.count} message${r.count === 1 ? '' : 's'} handed to the CRM file.`)
      done()
    } catch (e) { setExp(null); setErr(e.message) }
  }

  return (
    <div className="page">
      <div className="page-h">
        <div>
          <h1>Approvals</h1>
          <p className="sub">Every customer contact the agent proposes waits here for a named person. Approving drafts the message (checked against the offer); exporting hands ready messages to the CRM. Nothing is sent from Evergreen.</p>
        </div>
      </div>
      {err && <p className="err">{err}</p>}
      {!data ? <div className="card empty"><Loader2 className="spin" size={20} /></div> : (
        <div className="appr-grid">
          <section>
            <h2 className="sec-h">Waiting for you <span className="count">{pending.length}</span></h2>
            {!pending.length && <div className="card empty"><p className="sub">Nothing waiting. Run the agent to queue new cases.</p></div>}
            <ul className="appr-list">{pending.map(c => <Pending key={c.id} c={c} who={who} onDone={done} />)}</ul>
          </section>
          <section>
            <h2 className="sec-h">Ready for the CRM <span className="count ok">{ready.filter(c => c.status === 'ready').length}</span></h2>
            <div className="card">
              <button className="btn primary wide" disabled={exp === 'busy' || !ready.some(c => c.status === 'ready')} onClick={exportNow}>
                {exp === 'busy' ? <Loader2 className="spin" size={15} /> : <Download size={15} />} Export ready messages
              </button>
              {exp && exp !== 'busy' && <p className="ok-text small">{exp}</p>}
              <ul className="ready">
                {ready.map(c => (
                  <li key={c.id}>
                    <div><b>{c.first_name || c.customer_ref}</b> · {optionLabel(c.action_name)} <span className={'pill tone-' + caseStatusText(c.status)[1]}>{caseStatusText(c.status)[0]}</span></div>
                    {c.message && <blockquote className="msg">{c.message}<footer>approved by {c.approved_by}</footer></blockquote>}
                  </li>
                ))}
                {!ready.length && <li className="muted small">No messages ready yet.</li>}
              </ul>
            </div>
            <h2 className="sec-h">Recent decisions</h2>
            <div className="card">
              <ul className="hist">
                {history.map(c => <li key={c.id}><span>{c.first_name || c.customer_ref} · {optionLabel(c.action_name)}</span><span className={'pill tone-' + caseStatusText(c.status)[1]}>{caseStatusText(c.status)[0]}</span></li>)}
                {!history.length && <li className="muted small">No decisions yet.</li>}
              </ul>
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
