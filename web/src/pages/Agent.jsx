import { useCallback, useEffect, useRef, useState } from 'react'
import Papa from 'papaparse'
import { AlertTriangle, ArrowRight, Check, CircleDashed, Clock, Download, FileUp, Loader2, Play, User, Users, X } from 'lucide-react'
import { api } from '../api.js'
import { ago, money, num, optionLabel, toCsv } from '../lib.js'

const ICON = { pending: CircleDashed, running: Loader2, done: Check, waiting: Clock, failed: X, skipped: CircleDashed }
const DECISION = { recommend: 'Offer ready', recommend_pending_approval: 'Offer needs approval', no_action: 'No action', escalate_to_human: 'Handed to a person' }
const OUTBOX_COLUMNS = ['case_id', 'customer_ref', 'first_name', 'offer_name', 'offer_description', 'message', 'approved_by', 'message_log_id']

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }))
  const a = Object.assign(document.createElement('a'), { href: url, download: name })
  document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function RunForm({ who, stages, busy, onStart }) {
  const [source, setSource] = useState('demo')
  const [sample, setSample] = useState(stages.default_sample)
  const [maxTwin, setMaxTwin] = useState(stages.default_max_twin)
  const [file, setFile] = useState(null)
  const [err, setErr] = useState(null)
  const pickFile = f => {
    setErr(null); setFile(null)
    if (!f) return
    Papa.parse(f, {
      header: true, skipEmptyLines: true, transform: v => (typeof v === 'string' ? v.trim() : v),
      complete: r => (r.data.length ? setFile({ name: f.name, rows: r.data.slice(0, 5000), total: r.data.length }) : setErr('The file has no rows.')),
      error: e => setErr(e.message),
    })
  }
  const start = () => onStart(source === 'demo'
    ? { source: 'demo', sample, seed: Math.floor(Math.random() * 100000), max_twin: maxTwin, requested_by: who }
    : { source: 'rows', rows: file.rows, file_name: file.name, max_twin: maxTwin, requested_by: who })

  return (
    <div className="card run-form">
      <h3>Start a run</h3>
      <div className="seg">
        <button className={source === 'demo' ? 'on' : ''} onClick={() => setSource('demo')}>Demo customers</button>
        <button className={source === 'rows' ? 'on' : ''} onClick={() => setSource('rows')}>Upload a file</button>
      </div>
      {source === 'demo' ? (
        <>
          <label className="lbl">How many customers</label>
          <select className="input" value={sample} onChange={e => setSample(Number(e.target.value))}>
            {[50, 100, 200, 400, 800].map(n => <option key={n} value={n}>{n} customers</option>)}
          </select>
          <p className="tiny muted">{stages.demo_note}</p>
        </>
      ) : (
        <>
          <label className="file">
            <FileUp size={16} /> {file ? `${file.name} · ${num(file.total)} rows` : 'Choose a CSV in the Telco column layout'}
            <input type="file" accept=".csv,text/csv" onChange={e => pickFile(e.target.files[0])} />
          </label>
          {file && file.total > 5000 && <p className="tiny warn-text">Only the first 5,000 rows are used.</p>}
          <p className="tiny muted">Columns are mapped with pipeline.yaml. Rows that cannot be used are kept with a reason. Account state that is not in the file counts as unknown, so the offer needs approval.</p>
        </>
      )}
      <label className="lbl">Customers the digital twin works on per run</label>
      <select className="input" value={maxTwin} onChange={e => setMaxTwin(Number(e.target.value))}>
        {[10, 25, 50, 100, 200].map(n => <option key={n} value={n}>Top {n} most at risk</option>)}
      </select>
      <button className="btn primary wide" disabled={busy || (source === 'rows' && !file)} onClick={start}>
        {busy ? <Loader2 className="spin" size={16} /> : <Play size={16} />} {busy ? 'Agent is running…' : 'Run the agent'}
      </button>
      {err && <p className="err">{err}</p>}
    </div>
  )
}

function Pipeline({ steps, active, setActive }) {
  return (
    <ol className="pipe">
      {steps.map((s, i) => {
        const Icon = ICON[s.status] || CircleDashed
        return (
          <li key={s.key} className={`stage st-${s.status} ${s.kind} ${active === s.key ? 'sel' : ''}`}>
            <button onClick={() => setActive(s.key)} aria-pressed={active === s.key}>
              <span className="stage-top">
                <span className="stage-n">{i + 1}</span>
                <span className="stage-ic"><Icon size={15} className={s.status === 'running' ? 'spin' : ''} /></span>
                {s.kind === 'human' && <span className="person"><User size={11} /> person</span>}
              </span>
              <b>{s.title}</b>
              <span className="stage-h">{s.headline || s.what}</span>
            </button>
          </li>
        )
      })}
    </ol>
  )
}

function Bars({ items }) {
  const max = Math.max(1, ...items.map(([, v]) => v))
  return (
    <ul className="hbars">
      {items.map(([k, v]) => <li key={k}><span>{k}</span><i style={{ width: (v / max) * 100 + '%' }} /><b>{num(v)}</b></li>)}
    </ul>
  )
}

function StepDetail({ run, step, onOpenCustomers, onOpenApprovals, onExported }) {
  const [exp, setExp] = useState({ busy: false, msg: null, err: null })
  const d = step.detail || {}
  const exportNow = async () => {
    setExp({ busy: true, msg: null, err: null })
    try {
      const r = await api('/outbox/export', {})
      if (r.count) download(`evergreen_outbox_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`, toCsv(r.messages, OUTBOX_COLUMNS))
      setExp({ busy: false, msg: r.count ? `${r.count} message${r.count === 1 ? '' : 's'} handed to the CRM file.` : 'Nothing was ready.', err: null })
      onExported()
    } catch (e) { setExp({ busy: false, msg: null, err: e.message }) }
  }
  return (
    <div className="card step-detail">
      <div className="sd-h">
        <div><div className="eyebrow">{step.kind === 'human' ? 'Human step' : 'Agent step'}</div><h3>{step.title}</h3><p className="sub">{step.what}</p></div>
        <span className={`pill st-${step.status}`}>{step.status}</span>
      </div>
      {step.key === 'ingest' && (
        <>
          <div className="kpis"><div><b>{num(d.read)}</b><span>rows read</span></div><div><b>{num(d.usable)}</b><span>usable</span></div><div><b>{num(d.rejected)}</b><span>rejected with a reason</span></div></div>
          {d.rejected_examples?.length > 0 && <ul className="plain small">{d.rejected_examples.map(x => <li key={x.ref}><b>{x.ref}</b>: {x.reason}</li>)}</ul>}
        </>
      )}
      {step.key === 'score' && d.scored != null && (
        <>
          <div className="kpis">
            <div><b>{num(d.scored)}</b><span>customers scored</span></div>
            <div><b>{num(d.above_alert_line)}</b><span>above the alert line</span></div>
            <div><b>{num(d.expected_leavers, 1)}</b><span>expected to leave (sum of likelihoods)</span></div>
            <div><b>{money(d.monthly_revenue_at_risk)}</b><span>monthly revenue at risk</span></div>
          </div>
          {d.alert_line_is_placeholder && <p className="note warn"><AlertTriangle size={14} /> The alert line is a placeholder: no offer cost, customer value or success rate has been set yet, so it is not a cost-based cut-off.</p>}
        </>
      )}
      {step.key === 'prioritise' && d.prioritised != null && (
        <div className="kpis"><div><b>{num(d.prioritised)}</b><span>to work on this run</span></div><div><b>{num(d.deferred)}</b><span>wait for the next run</span></div><div><b>{num(d.below_alert_line)}</b><span>below the alert line: monitor only</span></div></div>
      )}
      {step.key === 'twin' && (
        <>
          {step.status === 'running' && d.total > 0 && <div className="progress"><i style={{ width: (d.done / d.total) * 100 + '%' }} /></div>}
          {d.option_runs && <Bars items={Object.entries(d.option_runs).map(([k, v]) => [optionLabel(k), v])} />}
          <p className="note warn">The digital twin shows how the model's view of a customer changes when we change their plan. It is a what-if on the model, not proof that the change would keep them.</p>
        </>
      )}
      {step.key === 'decide' && d.decisions && (
        <>
          <Bars items={Object.entries(d.decisions).map(([k, v]) => [DECISION[k] || k, v])} />
          {d.options_chosen && Object.keys(d.options_chosen).length > 0 && <><h4>Options picked</h4><Bars items={Object.entries(d.options_chosen)} /></>}
        </>
      )}
      {step.key === 'queue' && d.counts && (
        <div className="kpis">
          <div><b>{num(Object.values(d.counts).reduce((a, b) => a + b, 0))}</b><span>cases created</span></div>
          <div><b>{num(d.counts.pending_approval || 0)}</b><span>waiting for approval</span></div>
          <div><b>{num(d.counts.ready || 0)}</b><span>messages drafted</span></div>
          <div><b>{num(d.skipped_open_case?.length || 0)}</b><span>already had an open case</span></div>
        </div>
      )}
      {step.key === 'approve' && run.status === 'done' && (
        <>
          <div className="kpis"><div><b>{num(d.waiting)}</b><span>waiting</span></div><div><b>{num(d.approved)}</b><span>approved</span></div><div><b>{num(d.rejected)}</b><span>rejected</span></div></div>
          <button className="btn primary" onClick={onOpenApprovals}>Open approvals <ArrowRight size={15} /></button>
        </>
      )}
      {step.key === 'handoff' && run.status === 'done' && (
        <>
          <div className="kpis"><div><b>{num(d.ready)}</b><span>messages ready</span></div><div><b>{num(d.exported)}</b><span>handed to the CRM</span></div></div>
          <button className="btn primary" disabled={exp.busy || !d.ready} onClick={exportNow}>{exp.busy ? <Loader2 className="spin" size={15} /> : <Download size={15} />} Export ready messages for the CRM</button>
          <p className="tiny muted">Exports every ready message in the queue as a CSV for the CRM team. Nothing is sent to a customer from Evergreen.</p>
          {exp.msg && <p className="ok-text small">{exp.msg}</p>}
          {exp.err && <p className="err">{exp.err}</p>}
        </>
      )}
      {['score', 'prioritise', 'twin', 'decide', 'queue'].includes(step.key) && step.status === 'done' && (
        <button className="btn ghost" onClick={() => onOpenCustomers(run.id)}><Users size={15} /> See the customers</button>
      )}
    </div>
  )
}

export default function Agent({ who, stages, onOpenCustomers, onOpenApprovals, onChange }) {
  const [runs, setRuns] = useState(null)
  const [runId, setRunId] = useState(null)
  const [run, setRun] = useState(null)
  const [active, setActive] = useState('ingest')
  const [starting, setStarting] = useState(false)
  const [err, setErr] = useState(null)
  const follow = useRef(true)

  const loadRuns = useCallback(() => api('/agent/runs').then(d => { setRuns(d.runs); setRunId(id => id ?? d.runs[0]?.id ?? null) }).catch(e => setErr(e.message)), [])
  useEffect(() => { loadRuns() }, [loadRuns])

  const loadRun = useCallback(() => {
    if (runId == null) return Promise.resolve()
    return api(`/agent/runs/${runId}`).then(r => {
      setRun(r)
      if (follow.current) {
        const cur = r.steps.find(s => s.status === 'running') || r.steps.find(s => s.status === 'failed') || r.steps.find(s => s.status === 'waiting')
          || [...r.steps].reverse().find(s => s.status === 'done')
        if (cur) setActive(cur.key)
      }
    }).catch(e => setErr(e.message))
  }, [runId])
  useEffect(() => { setRun(null); follow.current = true; loadRun() }, [loadRun])
  const running = run?.status === 'running'
  useEffect(() => {
    const t = setInterval(() => { loadRun(); if (running) loadRuns() }, running ? 1200 : 6000)
    return () => clearInterval(t)
  }, [loadRun, loadRuns, running])
  const wasRunning = useRef(false)
  useEffect(() => { if (wasRunning.current && !running) { loadRuns(); onChange() } wasRunning.current = running }, [running, loadRuns, onChange])

  const start = async body => {
    setStarting(true); setErr(null)
    try { const r = await api('/agent/run', body); follow.current = true; setRunId(r.id); setRun(r); await loadRuns() } catch (e) { setErr(e.message) }
    setStarting(false)
  }
  const pick = key => { follow.current = false; setActive(key) }
  const step = run?.steps.find(s => s.key === active)
  const anyRunning = running || (runs || []).some(r => r.status === 'running')

  return (
    <div className="page">
      <div className="page-h">
        <div>
          <h1>Retention agent</h1>
          <p className="sub">Runs the churn process from start to finish: it reads customer data, scores everyone, tests retention options on each at-risk customer's digital twin and queues the best one. A person approves every contact; nothing is sent from here.</p>
        </div>
      </div>
      {err && <p className="err">{err}</p>}

      <div className="agent-grid">
        <div className="agent-main">
          {run ? (
            <>
              <div className="run-h">
                <div>
                  <b>Run #{run.id}</b> <span className={`pill st-${run.status}`}>{run.status === 'done' ? 'finished' : run.status}</span>
                  <p className="sub">{run.source} · started {ago(run.started_ts)}{run.requested_by ? ` by ${run.requested_by}` : ''}</p>
                </div>
                {run.status === 'done' && <button className="btn ghost" onClick={() => onOpenCustomers(run.id)}><Users size={15} /> Customers in this run</button>}
              </div>
              {run.error && <p className="note bad"><AlertTriangle size={14} /> {run.error}</p>}
              <Pipeline steps={run.steps} active={active} setActive={pick} />
              {step && <StepDetail run={run} step={step} onOpenCustomers={onOpenCustomers} onOpenApprovals={onOpenApprovals} onExported={() => { loadRun(); onChange() }} />}
            </>
          ) : runs && runs.length === 0 ? (
            <div className="card empty">
              <h3>No runs yet</h3>
              <p className="sub">Start the agent on a demo sample or upload a customer file. The eight steps below will light up as it works.</p>
              <ol className="pipe ghosted">{stages.stages.map((s, i) => <li key={s.key} className={`stage ${s.kind}`}><button disabled><span className="stage-top"><span className="stage-n">{i + 1}</span>{s.kind === 'human' && <span className="person"><User size={11} /> person</span>}</span><b>{s.title}</b><span className="stage-h">{s.what}</span></button></li>)}</ol>
            </div>
          ) : <div className="card empty"><Loader2 className="spin" size={20} /></div>}
        </div>

        <div className="agent-side">
          <RunForm who={who} stages={stages} busy={starting || anyRunning} onStart={start} />
          {runs && runs.length > 0 && (
            <div className="card">
              <h3>Recent runs</h3>
              <ul className="runs">
                {runs.map(r => (
                  <li key={r.id}>
                    <button className={r.id === runId ? 'on' : ''} onClick={() => setRunId(r.id)}>
                      <span><b>#{r.id}</b> <span className={`dot st-${r.status}`} /> {r.summary ? `${num(r.summary.prioritised)} worked on of ${num(r.summary.usable)}` : r.status}</span>
                      <small>{r.source} · {ago(r.started_ts)}</small>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
