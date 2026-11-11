import { useCallback, useEffect, useState } from 'react'
import { Check, Download, HeartHandshake, Loader2, Pencil, X } from 'lucide-react'
import { api } from '../api.js'
import { download } from '../download.js'
import { ago, optionLabel, pctRisk, stageText, toCsv } from '../lib.js'

const OUTBOX_COLUMNS = ['case_id', 'customer_ref', 'first_name', 'offer_name', 'offer_description', 'message', 'approved_by', 'message_log_id']

function MessageCard({ c, onDone }) {
  const [text, setText] = useState(c.message || '')
  const [editing, setEditing] = useState(false)
  const [note, setNote] = useState('')
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const go = async (path, body) => {
    setBusy(true); setErr(null)
    try { await api(`/workflow/cases/${c.id}/${path}`, body); onDone() } catch (e) { setErr(e.message); setBusy(false) }
  }
  const changed = text.trim() !== (c.message || '').trim()
  return (
    <li className="card appr">
      <div className="appr-h">
        <div><b>{c.first_name}</b> <small className="muted">{c.customer_ref}</small><p className="sub">Case #{c.id} · {optionLabel(c.action_name)}</p></div>
        <div className="appr-risk"><span>Likelihood of leaving</span><b>{pctRisk(c.probability)}</b></div>
      </div>
      <p className="small chain">Sent by <b>{c.submitted_by || 'the agent'}</b>{c.approved_uid && <> → approved by <b>{c.approved_by}</b></>} → <b>you</b> to clear</p>
      {editing
        ? <textarea className="input" rows={5} value={text} onChange={e => setText(e.target.value)} maxLength={2000} aria-label="Message text" />
        : <blockquote className="msg">{c.message}<footer>{c.message_source === 'template' ? 'From the approved template' : c.message_source === 'human_edited' ? 'Edited by a person' : 'Drafted by the assistant and checked'} · automatic checks passed</footer></blockquote>}
      <div className="row-btns">
        <button className="btn ghost small" onClick={() => setEditing(!editing)}><Pencil size={14} /> {editing ? 'Stop editing' : 'Edit wording'}</button>
      </div>
      <input className="input" placeholder="Note (optional)" value={note} onChange={e => setNote(e.target.value)} maxLength={500} />
      <div className="row-btns">
        <button className="btn primary" disabled={busy} onClick={() => go('clear', { note: note.trim() || null, ...(changed ? { message: text.trim() } : {}) })}>
          {busy ? <Loader2 className="spin" size={15} /> : <Check size={15} />} Clear for the CRM{changed ? ' with my edit' : ''}
        </button>
        <button className="btn ghost" disabled={busy} onClick={() => setRejecting(!rejecting)}><X size={15} /> Reject</button>
      </div>
      {rejecting && (
        <div className="reject">
          <input className="input" autoFocus placeholder="Why? (kept on the case)" value={reason} onChange={e => setReason(e.target.value)} maxLength={500} />
          <button className="btn ghost" disabled={busy || !reason.trim()} onClick={() => go('reject', { reason: reason.trim() })}>Confirm reject</button>
        </div>
      )}
      {err && <p className="err">{err}</p>}
    </li>
  )
}

function CareCard({ c, onDone }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const go = async () => {
    setBusy(true); setErr(null)
    try { await api(`/workflow/cases/${c.id}/care-handoff`, { note: note.trim() }); onDone() } catch (e) { setErr(e.message); setBusy(false) }
  }
  return (
    <li className="card appr">
      <div className="appr-h">
        <div><b>{c.first_name || c.customer_ref}</b> {c.first_name && <small className="muted">{c.customer_ref}</small>}<p className="sub">Case #{c.id} · flagged {ago(c.ts)}</p></div>
        <div className="appr-risk"><span>Likelihood of leaving</span><b>{pctRisk(c.probability)}</b></div>
      </div>
      <p className="note"><HeartHandshake size={14} /> This account is marked vulnerable or in an escalation. The system makes no offer and never contacts the customer; a person from the care team does.</p>
      <ul className="plain small">{(c.reasons || []).map(r => <li key={r}>{r}</li>)}</ul>
      <input className="input" placeholder="What should the care team know or do?" value={note} onChange={e => setNote(e.target.value)} maxLength={500} />
      <div className="row-btns">
        <button className="btn primary" disabled={busy || !note.trim()} onClick={go}>{busy ? <Loader2 className="spin" size={15} /> : <HeartHandshake size={15} />} Pass to the care team</button>
      </div>
      {err && <p className="err">{err}</p>}
    </li>
  )
}

export default function Compliance({ user, onChange, view }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [exp, setExp] = useState(null)
  const load = useCallback(() => api('/workflow/board').then(setData).catch(e => setErr(e.message)), [])
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t) }, [load])
  const done = () => { load(); onChange() }
  const cases = data?.cases || []

  const exportNow = async () => {
    setExp('busy')
    try {
      const r = await api('/workflow/export', {})
      if (r.count) download(`evergreen_outbox_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`, toCsv(r.messages, OUTBOX_COLUMNS))
      setExp(`${r.count} cleared message${r.count === 1 ? '' : 's'} handed to the CRM file.`)
      done()
    } catch (e) { setExp(null); setErr(e.message) }
  }

  if (view === 'care') {
    const care = cases.filter(c => c.stage === 'care')
    const handled = cases.filter(c => c.status === 'care_handoff').slice(0, 20)
    return (
      <div className="page">
        <div className="page-h"><div><h1>Protected accounts</h1><p className="sub">Vulnerable or escalated customers are never offered anything by this system. You decide how the care team follows up.</p></div></div>
        {err && <p className="err">{err}</p>}
        {!data ? <div className="card empty"><Loader2 className="spin" size={20} /></div> : (
          <div className="appr-grid">
            <section>
              <h2 className="sec-h">Waiting for you <span className="count">{care.length}</span></h2>
              {!care.length && <div className="card empty"><p className="sub">No protected accounts waiting.</p></div>}
              <ul className="appr-list">{care.map(c => <CareCard key={c.id} c={c} onDone={done} />)}</ul>
            </section>
            <section>
              <h2 className="sec-h">Passed to the care team</h2>
              <div className="card"><ul className="hist">
                {handled.map(c => <li key={c.id}><span>{c.first_name || c.customer_ref} · by {c.care_by}</span><span className="pill tone-info">Care team</span></li>)}
                {!handled.length && <li className="muted small">None yet.</li>}
              </ul></div>
            </section>
          </div>
        )}
      </div>
    )
  }

  const waiting = cases.filter(c => c.stage === 'compliance')
  const cleared = cases.filter(c => c.stage === 'crm_ready')
  return (
    <div className="page">
      <div className="page-h">
        <div>
          <h1>Message check</h1>
          <p className="sub">Messages the manager has approved. Read each one, correct the wording if needed (it must still pass the automatic checks), then clear it. Only cleared messages can go to the CRM, and you cannot clear a case you sent or approved. Nothing is sent from Evergreen.</p>
        </div>
      </div>
      {err && <p className="err">{err}</p>}
      {!data ? <div className="card empty"><Loader2 className="spin" size={20} /></div> : (
        <div className="appr-grid">
          <section>
            <h2 className="sec-h">Waiting for you <span className="count">{waiting.length}</span></h2>
            {!waiting.length && <div className="card empty"><p className="sub">No messages waiting for a check.</p></div>}
            <ul className="appr-list">{waiting.map(c => <MessageCard key={c.id} c={c} onDone={done} />)}</ul>
          </section>
          <section>
            <h2 className="sec-h">Cleared for the CRM <span className="count ok">{cleared.length}</span></h2>
            <div className="card">
              <button className="btn primary wide" disabled={exp === 'busy' || !cleared.length} onClick={exportNow}>
                {exp === 'busy' ? <Loader2 className="spin" size={15} /> : <Download size={15} />} Release cleared messages to the CRM
              </button>
              {exp && exp !== 'busy' && <p className="ok-text small">{exp}</p>}
              <ul className="ready">
                {cleared.map(c => (
                  <li key={c.id}>
                    <div><b>{c.first_name}</b> · {optionLabel(c.action_name)} <span className={'pill tone-' + stageText(c.stage)[1]}>{stageText(c.stage)[0]}</span></div>
                    <blockquote className="msg">{c.message}<footer>approved by {c.approved_by} · cleared by {c.cleared_by}</footer></blockquote>
                  </li>
                ))}
                {!cleared.length && <li className="muted small">Nothing cleared yet.</li>}
              </ul>
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
