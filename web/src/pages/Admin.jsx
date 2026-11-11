import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Circle, Loader2, ShieldCheck } from 'lucide-react'
import { api } from '../api.js'
import { ago, pctRisk, ROLE_TEXT, stageText } from '../lib.js'
import ModelDrawer from '../components/ModelDrawer.jsx'

const DUTIES = [
  ['analyst', 'Run the agent · study the twin · send a case to the manager', 'Approve, clear or export anything'],
  ['manager', 'Approve, send back or reject a case the analyst sent', 'Approve a case they sent · run the agent · clear messages'],
  ['compliance', 'Clear or reject an approved message · release cleared messages · pass protected accounts to care', 'Clear a case they sent or approved · approve offers'],
  ['admin', 'See the system, the business numbers, the people and the audit trail', 'Act on any case · run the agent · approve anything'],
]
const ORDER = ['analyst', 'manager', 'name', 'compliance', 'crm_ready', 'care', 'closed']

function Overview({ meta }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [model, setModel] = useState(false)
  const load = useCallback(() => api('/admin/overview').then(setD).catch(e => setErr(e.message)), [])
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t) }, [load])
  if (err) return <p className="err">{err}</p>
  if (!d) return <div className="card empty"><Loader2 className="spin" size={20} /></div>
  const missing = d.numbers.filter(n => !n.set)
  const h = d.health
  return (
    <>
      <div className="kpis wide">
        <div><b>{d.total_cases}</b><span>cases in total</span></div>
        <div><b className={d.duty_violations ? 'bad' : 'good'}>{d.duty_violations}</b><span>cases where one person did two steps</span></div>
        <div><b>{missing.length}</b><span>business numbers still not set</span></div>
        <div><b>{h.model_loaded ? 'Running' : 'Down'}</b><span>prediction service</span></div>
      </div>

      <div className="appr-grid">
        <section>
          <h2 className="sec-h">Where the cases are</h2>
          <div className="card">
            <ul className="hist">
              {ORDER.map(k => { const [l, t] = stageText(k); return <li key={k}><span>{d.stages[k]}</span><span className={'pill tone-' + t}>{d.counts[k]}</span></li> })}
            </ul>
          </div>

          <h2 className="sec-h" style={{ marginTop: 20 }}>Business numbers</h2>
          <div className="card">
            <p className="tiny muted">These come from <code>config.yaml</code> and <code>guardrails.yaml</code> and are set by the business, never guessed. While one is empty, every offer waits for a person and the alert line stays a placeholder.</p>
            <ul className="hist">
              {d.numbers.map(n => (
                <li key={n.key}>
                  <span>{n.label}</span>
                  {n.set ? <span className="pill tone-ok"><CheckCircle2 size={12} /> {typeof n.value === 'object' ? 'set' : String(n.value)}</span> : <span className="pill tone-warn"><Circle size={12} /> not set</span>}
                </li>
              ))}
            </ul>
            {h.threshold_is_placeholder && <p className="note warn"><AlertTriangle size={14} /> The alert line ({pctRisk(h.threshold)}) is a placeholder, not a cost-based cut-off.</p>}
          </div>
        </section>

        <section>
          <h2 className="sec-h">System</h2>
          <div className="card">
            <ul className="hist">
              <li><span>Sign-in required for data endpoints</span><span className={'pill tone-' + (d.settings.login_required ? 'ok' : 'warn')}>{d.settings.login_required ? 'Yes' : 'No (demo mode)'}</span></li>
              <li><span>API access key</span><span className={'pill tone-' + (d.settings.access_key_set ? 'ok' : 'muted')}>{d.settings.access_key_set ? 'Set' : 'Not set'}</span></li>
              <li><span>Message assistant</span><span className="pill tone-muted">{d.settings.assistant_key_set ? 'Claude drafts, code checks' : 'Template only'}</span></li>
              <li><span>n8n automation</span><span className="pill tone-muted">{d.settings.automation_configured ? 'Connected' : 'Not configured'}</span></li>
              <li><span>Library versions match the saved model</span><span className={'pill tone-' + (h.versions_match ? 'ok' : 'bad')}>{h.versions_match ? 'Yes' : 'No'}</span></li>
            </ul>
            <button className="btn ghost wide" onClick={() => setModel(true)}><ShieldCheck size={15} /> Model details</button>
          </div>

          <h2 className="sec-h" style={{ marginTop: 20 }}>Recent agent runs</h2>
          <div className="card">
            <ul className="hist">
              {(d.recent_runs || []).map(r => <li key={r.id}><span>Run #{r.id} · {r.requested_by || 'unknown'} · {ago(r.started_ts || r.ts)}</span><span className={'pill st-' + r.status}>{r.status}</span></li>)}
              {!d.recent_runs?.length && <li className="muted small">No runs yet.</li>}
            </ul>
          </div>
        </section>
      </div>
      {model && <ModelDrawer meta={meta} onClose={() => setModel(false)} />}
    </>
  )
}

function People() {
  const [d, setD] = useState(null)
  useEffect(() => { api('/admin/overview').then(setD).catch(() => {}) }, [])
  return (
    <>
      <div className="card flush">
        <table className="tbl">
          <thead><tr><th>Person</th><th>Role</th><th>Can</th><th>Cannot</th></tr></thead>
          <tbody>
            {DUTIES.map(([role, can, cannot]) => {
              const p = (d?.people || []).find(x => x.role === role)
              return (
                <tr key={role}>
                  <td><b>{p?.name || '–'}</b></td>
                  <td>{ROLE_TEXT[role][0]}</td>
                  <td className="small">{can}</td>
                  <td className="small muted">{cannot}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h3>The chain</h3>
        <p className="small">Analyst sends → Manager approves → Compliance clears → Compliance releases to the CRM. The server checks, on every step, that the person is in the right role and has not already done an earlier step on the same case. These are demo accounts; a real deployment connects the company's own sign-in.</p>
      </div>
    </>
  )
}

function Audit() {
  const [ev, setEv] = useState(null)
  const [err, setErr] = useState(null)
  const load = useCallback(() => api('/admin/audit?limit=300').then(d => setEv(d.events)).catch(e => setErr(e.message)), [])
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t) }, [load])
  if (err) return <p className="err">{err}</p>
  if (!ev) return <div className="card empty"><Loader2 className="spin" size={20} /></div>
  return (
    <div className="card flush">
      <table className="tbl">
        <thead><tr><th>When</th><th>Who</th><th>What</th><th>Case</th><th>Detail</th></tr></thead>
        <tbody>
          {ev.map((e, i) => (
            <tr key={i}>
              <td className="muted small">{ago(e.ts)}</td>
              <td>{e.actor || <span className="muted">Agent</span>}</td>
              <td>{e.event.replace(/_/g, ' ')}</td>
              <td>{e.case_id ? `#${e.case_id} ${e.customer_ref || ''}` : '–'}</td>
              <td className="small">{e.detail || ''}</td>
            </tr>
          ))}
          {!ev.length && <tr><td colSpan="5" className="muted small">Nothing recorded yet.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

const TITLES = {
  overview: ['Control room', 'The state of the whole system. You can look at everything here but you cannot act on a case.'],
  people: ['People & duties', 'Who holds which role and what each role can and cannot do.'],
  audit: ['Audit trail', 'Every sign-in and every step on every case, with the person who did it.'],
}

export default function Admin({ view, meta }) {
  const [t, sub] = TITLES[view]
  return (
    <div className="page">
      <div className="page-h"><div><h1>{t}</h1><p className="sub">{sub}</p></div></div>
      {view === 'overview' && <Overview meta={meta} />}
      {view === 'people' && <People />}
      {view === 'audit' && <Audit />}
    </div>
  )
}
