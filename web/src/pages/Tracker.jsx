import { useCallback, useEffect, useMemo, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { api } from '../api.js'
import { ago, optionLabel, pctRisk, stageText } from '../lib.js'

const ORDER = ['analyst', 'manager', 'name', 'compliance', 'crm_ready', 'care', 'closed']

/** Read-only board of every case and who has touched it. Same view for analyst, manager and compliance. */
export default function Tracker({ user }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [stage, setStage] = useState('all')
  const load = useCallback(() => api('/workflow/board').then(setData).catch(e => setErr(e.message)), [])
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t) }, [load])
  const cases = useMemo(() => (data?.cases || []).filter(c => stage === 'all' || c.stage === stage), [data, stage])

  return (
    <div className="page">
      <div className="page-h">
        <div>
          <h1>Case tracker</h1>
          <p className="sub">Every case, where it is in the chain, and who did each step. A case moves analyst → manager → compliance → CRM; nobody can approve their own work.</p>
        </div>
      </div>
      {err && <p className="err">{err}</p>}
      {!data ? <div className="card empty"><Loader2 className="spin" size={20} /></div> : (
        <>
          <div className="seg stage-seg" role="tablist" aria-label="Stage">
            <button className={stage === 'all' ? 'on' : ''} onClick={() => setStage('all')}>All <span className="muted">{data.cases.length}</span></button>
            {ORDER.map(k => <button key={k} className={stage === k ? 'on' : ''} onClick={() => setStage(k)}>{data.stages[k]} <span className="muted">{data.counts[k]}</span></button>)}
          </div>
          <div className="card flush">
            <table className="tbl">
              <thead><tr><th>Case</th><th>Customer</th><th>Offer</th><th className="num">Likelihood of leaving</th><th>Where it is</th><th>Chain</th><th>Updated</th></tr></thead>
              <tbody>
                {cases.map(c => {
                  const [label, tone] = stageText(c.stage)
                  return (
                    <tr key={c.id} className={c.submitted_uid === user.id || c.approved_uid === user.id || c.cleared_uid === user.id ? 'hl' : ''}>
                      <td>#{c.id}</td>
                      <td><b>{c.first_name || c.customer_ref}</b>{c.first_name && <small className="muted"> {c.customer_ref}</small>}</td>
                      <td>{optionLabel(c.action_name)}</td>
                      <td className="num">{pctRisk(c.probability)}</td>
                      <td><span className={'pill tone-' + tone}>{label}</span>{c.status === 'rejected' && <span className="pill tone-muted">Rejected</span>}{c.status === 'exported' && <span className="pill tone-ok">Sent to CRM file</span>}{c.status === 'care_handoff' && <span className="pill tone-info">Care team</span>}</td>
                      <td className="chain small">
                        {c.submitted_by ? <span>Sent by {c.submitted_by}</span> : <span className="muted">not sent yet</span>}
                        {c.approved_uid && <span> → Approved by {c.approved_by}</span>}
                        {c.cleared_by && <span> → Cleared by {c.cleared_by}</span>}
                        {c.care_by && <span> → Care hand-off by {c.care_by}</span>}
                      </td>
                      <td className="muted small">{ago(c.updated_ts)}</td>
                    </tr>
                  )
                })}
                {!cases.length && <tr><td colSpan="7" className="muted small">No cases here yet. The analyst starts a retention-agent run to create them.</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
