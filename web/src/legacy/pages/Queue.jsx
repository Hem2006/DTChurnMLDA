import { useEffect, useState } from 'react'
import { Check, Download, KeyRound, RefreshCw, X } from 'lucide-react'
import { api, getApiKey, setApiKey } from '../../api.js'
import { useApiCall, useApp } from '../state.jsx'
import { Badge, Body, Card, Empty, Field, Loading, More, Notice, PageHero, RiskCell, Seg, useToast } from '../components/ui.jsx'
import { download, humanize, num, pct, signed, toCsv } from '../lib.js'

const STATUSES = ['pending_approval', 'needs_first_name', 'ready', 'exported', 'escalated', 'rejected', 'invalid', 'no_action']
const TONE = { pending_approval: 'amber', needs_first_name: 'amber', ready: 'green', exported: 'navy', escalated: 'red', rejected: 'grey', invalid: 'red', no_action: 'grey' }

function load(k) { try { return sessionStorage.getItem(k) || '' } catch { return '' } }
function keep(k, v) { try { sessionStorage.setItem(k, v) } catch { /* ignore */ } }

function KeyGate({ onSaved }) {
  const [k, setK] = useState(getApiKey())
  return (
    <Card title="API key required" sub="This server protects the review queue with CHURN_API_KEY. The key is kept for this browser tab only.">
      <div className="row">
        <Field label="X-API-Key"><input type="password" value={k} onChange={e => setK(e.target.value)} style={{ width: 280 }} /></Field>
        <button className="btn btn-primary" style={{ alignSelf: 'end' }} onClick={() => { setApiKey(k.trim()); onSaved() }}><KeyRound size={16} /> Use key</button>
      </div>
    </Card>
  )
}

function CaseDetail({ id, onChanged }) {
  const { meta } = useApp()
  const c = useApiCall(() => api(`/cases/${id}`), id, { delay: 0 })
  const [who, setWho] = useState(() => load('evergreen.reviewer'))
  const [first, setFirst] = useState('')
  const [reason, setReason] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [toast, show] = useToast()
  const d = c.data
  useEffect(() => { if (d) { setFirst(d.first_name || ''); setText(d.message || ''); setReason(''); setErr(null) } }, [d])

  const act = async (path, body, done) => {
    setBusy(true); setErr(null)
    try { await api(`/cases/${id}/${path}`, body); show(done); c.reload(); onChanged() } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  if (c.error) return <Card><Notice kind="danger">{c.error.message}</Notice></Card>
  if (!d) return <Card><Loading /></Card>
  const open = ['pending_approval', 'needs_first_name', 'ready'].includes(d.status)
  const sc = d.scenarios

  return (
    <div className="stack">
      <Card title={`Case #${d.id}: ${d.customer_ref}`} sub={`Opened ${new Date(d.ts).toLocaleString()} · updated ${new Date(d.updated_ts).toLocaleString()}`} action={<Badge tone={TONE[d.status]}>{humanize(d.status)}</Badge>}>
        <div className="grid g2">
          <div className="stack-sm">
            <span className="small muted">Churn probability</span>
            {d.probability != null ? <RiskCell p={d.probability} threshold={d.threshold} /> : <span className="muted">n/a</span>}
          </div>
          <div className="stack-sm">
            <span className="small muted">Offer</span>
            <strong>{d.action_name ? humanize(d.action_name) : 'none'}</strong>
            {d.approved_by && <span className="tiny muted">Approved by {d.approved_by}</span>}
          </div>
        </div>
        {d.error && <div style={{ marginTop: 14 }}><Notice kind="danger">{d.error}</Notice></div>}
        {d.approvals_needed.length > 0 && d.status === 'pending_approval' && (
          <div style={{ marginTop: 14 }}><Notice kind="warn" title="Needs human approval:"><ul>{d.approvals_needed.map(x => <li key={x}>{x}</li>)}</ul></Notice></div>
        )}
        <More title={`Reasons (${d.reasons.length})`}><ul className="list small">{d.reasons.map(x => <li key={x}>{x}</li>)}</ul></More>
      </Card>

      {sc.rows.length > 0 && (
        <Card title="Twin scenarios" sub={`Run automatically at intake, ranked by ${humanize(sc.ranked_by)}. Risk now ${pct(sc.baseline_risk)}.`}>
          <Notice kind="label" title="MODEL SENSITIVITY, NOT A CAUSAL ESTIMATE.">The chosen scenario is the first permitted or approvable one in this order.</Notice>
          <div className="table-wrap" style={{ marginTop: 14 }}>
            <table className="tbl">
              <thead><tr><th>Scenario</th><th className="num">Risk after</th><th className="num">Drop</th><th>Guardrails</th><th>Confidence</th></tr></thead>
              <tbody>
                {sc.rows.map(r => (
                  <tr key={r.scenario} className={r.chosen ? 'selected' : ''}>
                    <td><strong>{humanize(r.scenario)}</strong>{r.chosen && <> <Badge tone="navy">chosen</Badge></>}{r.warning && <div className="tiny muted">{r.warning}</div>}</td>
                    <td className="num">{pct(r.risk_after)}</td>
                    <td className="num">{signed(r.risk_drop)}</td>
                    <td>{humanize(r.guardrails)}</td>
                    <td>{r.confidence}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {sc.not_applicable.length > 0 && <p className="tiny muted" style={{ marginTop: 10 }}>Not applicable: {sc.not_applicable.map(n => `${humanize(n.scenario)} (${n.reason})`).join('; ')}</p>}
        </Card>
      )}

      {(d.message || open) && (
        <Card title="Message" sub={d.message ? `Source: ${d.message_source}. Passed the offer, number and pressure-language checks.` : 'Drafted once the case is approved and has a first name.'}>
          {d.status === 'ready' ? (
            <div className="stack-sm">
              <Field label="Message (editable until export; re-checked on save)"><textarea rows={6} value={text} onChange={e => setText(e.target.value)} /></Field>
              <div className="row"><button className="btn btn-ghost btn-sm" disabled={busy || !who.trim() || text === d.message || !text.trim()} onClick={() => act('message', { edited_by: who.trim(), message: text }, 'Message saved.')}>Save edit</button></div>
            </div>
          ) : d.message ? <div className="message-box">{d.message}</div> : <Empty>No message yet.</Empty>}
        </Card>
      )}

      {open && (
        <Card title="Decide" sub="Your name is recorded with every action.">
          <div className="stack">
            <div className="form-grid">
              <Field label="Your name"><input value={who} maxLength={80} onChange={e => { setWho(e.target.value); keep('evergreen.reviewer', e.target.value) }} /></Field>
              {d.status !== 'ready' && <Field label="Customer first name" hint="not in the model data"><input value={first} maxLength={40} onChange={e => setFirst(e.target.value)} /></Field>}
            </div>
            <div className="row">
              {d.status === 'pending_approval' && (
                <button className="btn btn-primary" disabled={busy || !who.trim()} onClick={() => act('approve', { approved_by: who.trim(), first_name: first.trim() || null }, 'Approved.')}><Check size={16} /> Approve offer</button>
              )}
              {d.status !== 'ready' && first.trim() && first.trim() !== (d.first_name || '') && (
                <button className="btn btn-ghost" disabled={busy} onClick={() => act('first-name', { first_name: first.trim() }, 'First name saved.')}>Save first name</button>
              )}
            </div>
            <hr className="divider" style={{ margin: 0 }} />
            <div className="row" style={{ alignItems: 'end' }}>
              <Field label="Reason (to reject)"><input value={reason} maxLength={500} onChange={e => setReason(e.target.value)} style={{ minWidth: 260 }} /></Field>
              <button className="btn btn-danger" disabled={busy || !who.trim() || !reason.trim()} onClick={() => act('reject', { rejected_by: who.trim(), reason: reason.trim() }, 'Rejected.')}><X size={16} /> Reject</button>
            </div>
            {err && <Notice kind="danger">{err}</Notice>}
          </div>
        </Card>
      )}

      <Card title="History">
        <ul className="timeline">
          {d.events.map((e, i) => (
            <li key={i}><div><strong>{humanize(e.event)}</strong>{e.actor && <> by {e.actor}</>}<div className="tiny muted">{new Date(e.ts).toLocaleString()}{e.detail ? ` · ${e.detail}` : ''}</div></div></li>
          ))}
        </ul>
      </Card>
      {meta.threshold_is_placeholder && <p className="tiny muted">Case scores used threshold {num(d.threshold)}, a PLACEHOLDER.</p>}
      {toast}
    </div>
  )
}

export default function Queue() {
  const [status, setStatus] = useState('pending_approval')
  const [sel, setSel] = useState(null)
  const [bump, setBump] = useState(0)
  const [toast, show] = useToast()
  const list = useApiCall(() => api(`/cases?status=${status === 'all' ? '' : status}&limit=500`), status + bump, { delay: 0 })
  const counts = list.data?.summary || {}
  const cases = list.data?.cases || []
  useEffect(() => { setSel(null) }, [status])

  const exportOutbox = async () => {
    try {
      const r = await api('/outbox/export', {})
      if (r.count) {
        const cols = Object.keys(r.messages[0])
        download(`outbox_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.csv`, toCsv(r.messages, cols))
      }
      show(`${r.count} message(s) exported and marked exported. Send them from your CRM.`)
      setBump(b => b + 1)
    } catch (e) { show(e.message) }
  }

  const needKey = list.error?.status === 401
  return (
    <>
      <PageHero eyebrow="Review queue" title="Review queue"
        lead="Customers come in as cases. Anything the guardrails mark needs-approval waits here for a named person. Nothing is sent: ready messages are exported to a file for the CRM team." />
      <Body>
        {needKey ? <KeyGate onSaved={() => setBump(b => b + 1)} /> : (
          <>
            <Card>
              <div className="row spread">
                <Seg value={status} onChange={setStatus} options={[...STATUSES.map(s => ({ value: s, label: humanize(s), count: counts[s] || 0 })), { value: 'all', label: 'All' }]} />
                <div className="row">
                  <button className="btn btn-ghost btn-sm" onClick={() => setBump(b => b + 1)}><RefreshCw size={14} /> Refresh</button>
                  <button className="btn btn-primary btn-sm" disabled={!counts.ready} onClick={exportOutbox}><Download size={14} /> Export {counts.ready || 0} ready message(s)</button>
                </div>
              </div>
            </Card>
            {list.error && <Notice kind="danger">{list.error.message}</Notice>}
            <div className="split" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.2fr)' }}>
              <Card title={`${humanize(status === 'all' ? 'all cases' : status)} (${cases.length})`} sub="Newest first. Select a case to open it.">
                {list.loading && !list.data ? <Loading /> : cases.length === 0 ? <Empty>No cases with this status. Add customers from <a href="#/batch">Score a file</a> or <a href="#/recommend">Recommend</a>.</Empty> : (
                  <div className="table-wrap">
                    <table className="tbl">
                      <thead><tr><th>Customer</th><th>Churn probability</th></tr></thead>
                      <tbody>
                        {cases.map(c => (
                          <tr key={c.id} className={`clickable ${sel === c.id ? 'selected' : ''}`} onClick={() => setSel(c.id)}>
                            <td><strong>{c.customer_ref}</strong> <span className="tiny muted">#{c.id}{c.first_name ? ` · ${c.first_name}` : ''}</span><div className="row" style={{ gap: 6, marginTop: 4 }}><Badge tone={TONE[c.status]}>{humanize(c.status)}</Badge>{c.action_name && <span className="tiny muted">{humanize(c.action_name)}</span>}</div></td>
                            <td>{c.probability != null ? <RiskCell p={c.probability} threshold={c.threshold} /> : 'n/a'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
              <div className={sel ? '' : 'sticky'}>{sel ? <CaseDetail id={sel} onChanged={() => setBump(b => b + 1)} /> : <Card><Empty>Select a case to see the reasons, the twin scenarios, the message and its history.</Empty></Card>}</div>
            </div>
          </>
        )}
      </Body>
      {toast}
    </>
  )
}
