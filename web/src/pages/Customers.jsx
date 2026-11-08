import { useCallback, useEffect, useMemo, useState } from 'react'
import { FlaskConical, Loader2, Search } from 'lucide-react'
import { api } from '../api.js'
import { ago, caseStatusText, money, num, pctRisk, pts } from '../lib.js'
import CustomerTwin from '../components/CustomerTwin.jsx'

const SCOPES = [['prioritised', 'Worked on by the agent'], ['above', 'Above the alert line'], ['all', 'Everyone'], ['rejected', 'Unusable rows']]

export default function Customers({ who, meta, runId, setRunId, onChange }) {
  const [runs, setRuns] = useState(null)
  const [scope, setScope] = useState('prioritised')
  const [rows, setRows] = useState(null)
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    api('/agent/runs').then(d => {
      const done = d.runs.filter(r => r.status === 'done')
      setRuns(done)
      if (runId == null && done.length) setRunId(done[0].id)
    }).catch(e => setErr(e.message))
  }, [runId, setRunId])

  const load = useCallback(() => {
    if (runId == null) return
    api(`/agent/runs/${runId}/customers?scope=${scope}`).then(d => setRows(d.customers)).catch(e => setErr(e.message))
  }, [runId, scope])
  useEffect(() => { setRows(null); load() }, [load])

  const shown = useMemo(() => {
    const s = q.trim().toLowerCase()
    return (rows || []).filter(r => !s || r.ref.toLowerCase().includes(s) || (r.first_name || '').toLowerCase().includes(s))
  }, [rows, q])
  const run = (runs || []).find(r => r.id === runId)
  const sm = run?.summary

  if (runs && !runs.length) {
    return <div className="page"><div className="page-h"><h1>Customers &amp; digital twins</h1></div><div className="card empty"><p className="sub">No finished agent run yet. Run the agent first; its customers appear here.</p></div></div>
  }

  return (
    <div className="page">
      <div className="page-h">
        <div>
          <h1>Customers &amp; digital twins</h1>
          <p className="sub">Everyone the agent scored, most likely to leave first. Open a customer to see why they might leave, what the agent tested on their digital twin, and to try your own what-if.</p>
        </div>
        {runs && (
          <select className="input run-pick" value={runId ?? ''} onChange={e => setRunId(Number(e.target.value))} aria-label="Agent run">
            {runs.map(r => <option key={r.id} value={r.id}>Run #{r.id} · {r.source} · {ago(r.started_ts)}</option>)}
          </select>
        )}
      </div>
      {err && <p className="err">{err}</p>}
      {sm && (
        <div className="kpis wide">
          <div><b>{num(sm.usable)}</b><span>customers scored</span></div>
          <div><b>{num(sm.above_alert_line)}</b><span>above the alert line{meta.threshold_is_placeholder && <em className="tag warn">placeholder line</em>}</span></div>
          <div><b>{num(sm.prioritised)}</b><span>worked on by the agent</span></div>
          <div><b>{money(sm.monthly_revenue_at_risk)}</b><span>monthly revenue at risk</span></div>
        </div>
      )}

      <div className="toolbar">
        <div className="seg">{SCOPES.map(([k, l]) => <button key={k} className={scope === k ? 'on' : ''} onClick={() => setScope(k)}>{l}</button>)}</div>
        <label className="search"><Search size={15} /><input placeholder="Search name or ID" value={q} onChange={e => setQ(e.target.value)} /></label>
      </div>

      <div className="card flush">
        {!rows ? <div className="empty"><Loader2 className="spin" size={20} /></div> : (
          <div className="table-wrap">
            <table className="tbl cust">
              <thead>
                <tr><th>Customer</th><th>Likelihood of leaving</th><th className="num">Monthly bill</th><th>Main reasons</th><th>Best option on the twin</th><th>Status</th><th /></tr>
              </thead>
              <tbody>
                {shown.map(r => {
                  const [st, tone] = r.error ? ['Unusable row', 'bad'] : caseStatusText(r.case_status)
                  return (
                    <tr key={r.ref} onClick={() => !r.error && setOpen(r.ref)} className={r.error ? '' : 'click'}>
                      <td><b>{r.first_name || r.ref}</b>{r.first_name && <small className="muted"> {r.ref}</small>}{r.rank && <small className="rank">#{r.rank}</small>}</td>
                      <td>{r.error ? <span className="small muted">{r.error}</span> : (
                        <div className="risk-cell"><span className={'risk-bar ' + (r.above_alert_line ? 'hi' : 'lo')}><i style={{ width: Math.min(100, r.probability * 100) + '%' }} /></span><b>{pctRisk(r.probability)}</b></div>
                      )}</td>
                      <td className="num">{r.monthly != null ? money(r.monthly, 2) : ''}</td>
                      <td>{r.reasons.slice(0, 2).map(x => <span key={x.feature} className="reason">{x.text}</span>)}{!r.reasons.length && !r.error && <span className="muted small">{r.above_alert_line ? '' : 'Below the alert line'}</span>}</td>
                      <td>{r.action_label ? <><span className="small">{r.action_label}</span><br /><small className="good">{pctRisk(r.risk_after)} after ({pts(r.probability, r.risk_after)})</small></>
                        : r.decision ? <span className="small muted">{r.decision_text}</span> : <span className="small muted">–</span>}</td>
                      <td><span className={'pill tone-' + tone}>{st}</span></td>
                      <td>{!r.error && <button className="btn ghost small" onClick={e => { e.stopPropagation(); setOpen(r.ref) }}><FlaskConical size={14} /> Twin</button>}</td>
                    </tr>
                  )
                })}
                {!shown.length && <tr><td colSpan="7" className="muted center">Nobody here.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {open && <CustomerTwin runId={runId} refId={open} meta={meta} who={who} onClose={() => setOpen(null)} onChanged={() => { load(); onChange() }} />}
    </div>
  )
}
