import { useMemo, useState } from 'react'
import Papa from 'papaparse'
import { Download, FileUp, ListChecks } from 'lucide-react'
import { api } from '../../api.js'
import { useApp } from '../state.jsx'
import { Badge, Body, Card, Empty, Field, Loading, More, Notice, PageHero, RiskCell, Stat, useToast } from '../components/ui.jsx'
import { REF_RE, accountStateFromRow, chunk, download, humanize, missingColumns, num, pct, toCsv } from '../lib.js'

const SCORE_CHUNK = 1000
const QUEUE_CHUNK = 10

export default function Batch() {
  const { meta } = useApp()
  const [file, setFile] = useState(null)
  const [rows, setRows] = useState(null)
  const [res, setRes] = useState(null)
  const [err, setErr] = useState(null)
  const [progress, setProgress] = useState(null)
  const [over, setOver] = useState(false)
  const [cut, setCut] = useState(() => Math.round(meta.threshold * 100) / 100)
  const [queueN, setQueueN] = useState(25)
  const [queued, setQueued] = useState(null)
  const [toast, show] = useToast()

  const parse = f => {
    setFile(f); setRes(null); setErr(null); setRows(null); setQueued(null)
    Papa.parse(f, {
      header: true, skipEmptyLines: true,
      complete: out => {
        const miss = missingColumns(out.meta.fields || [], meta.input_columns)
        if (miss.length) { setErr(`Missing required columns: ${miss.join(', ')}`); return }
        if (!out.data.length) { setErr('The file has no rows'); return }
        setRows(out.data)
        score(out.data)
      },
      error: e => setErr(`That file could not be read as a CSV: ${e.message}`),
    })
  }

  const score = async data => {
    const parts = chunk(data, SCORE_CHUNK)
    const all = { results: [], rejected: [] }
    try {
      for (let i = 0; i < parts.length; i++) {
        setProgress({ done: i * SCORE_CHUNK, total: data.length, what: 'Scoring' })
        const r = await api('/score', { rows: parts[i] })
        all.results.push(...r.results); all.rejected.push(...r.rejected)
        Object.assign(all, { threshold: r.threshold, threshold_is_placeholder: r.threshold_is_placeholder })
      }
      all.results.sort((a, b) => b.churn_probability - a.churn_probability)
      setRes(all)
    } catch (e) { setErr(e.message) } finally { setProgress(null) }
  }

  const above = useMemo(() => (res ? res.results.filter(r => r.churn_probability >= cut) : []), [res, cut])
  const flagged = res ? res.results.filter(r => r.flagged) : []
  const byRef = useMemo(() => Object.fromEntries((rows || []).map((r, i) => [String(r.customerID || `row ${i + 1}`), r])), [rows])
  const queueable = flagged.filter(r => REF_RE.test(r.ref))

  const queue = async () => {
    const todo = queueable.slice(0, queueN)
    const counts = {}
    let skipped = 0
    setErr(null)
    try {
      for (const [i, part] of chunk(todo, QUEUE_CHUNK).entries()) {
        setProgress({ done: i * QUEUE_CHUNK, total: todo.length, what: 'Queueing' })
        const records = part.map(r => {
          const raw = byRef[r.ref] || {}
          const first = String(raw.first_name || '').trim()
          return { customer_ref: r.ref, first_name: first ? first.slice(0, 40) : null, customer: r.customer, account_state: accountStateFromRow(raw) }
        })
        const out = await api('/cases/ingest', { records })
        for (const [k, v] of Object.entries(out.counts)) counts[k] = (counts[k] || 0) + v
        skipped += out.skipped_open_case.length
      }
      setQueued({ counts, skipped })
      show(`${todo.length - skipped} customer(s) added to the review queue.`)
    } catch (e) { setErr(e.message) } finally { setProgress(null) }
  }

  const exportScored = () => {
    const cols = ['ref', 'churn_probability', 'flagged', ...meta.input_columns]
    download('scored_customers.csv', toCsv(res.results.map(r => ({ ref: r.ref, churn_probability: r.churn_probability.toFixed(4), flagged: r.flagged, ...r.customer })), cols))
  }
  const template = () => download('customer_template.csv', toCsv([], ['customerID', 'first_name', ...meta.input_columns, 'vulnerable', 'escalated', 'contacts_this_month', 'discount_pct_used_this_quarter']))

  return (
    <>
      <PageHero eyebrow="Batch" title="Score a file" lead="Upload a CSV in the Telco layout. Every row is validated on its own; rows that fail come back with the reason, never silently dropped." />
      <Body>
        <Card>
          <label className={`dropzone ${over ? 'over' : ''}`}
            onDragOver={e => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
            onDrop={e => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) parse(f) }}>
            <input type="file" accept=".csv,text/csv" onChange={e => e.target.files[0] && parse(e.target.files[0])} />
            <FileUp size={36} color="#4f9cf9" />
            <h3 style={{ marginTop: 10 }}>{file ? file.name : 'Drop a customer CSV here, or click to choose'}</h3>
            <p className="muted small" style={{ marginTop: 6 }}>Needs the {meta.input_columns.length} model columns. Optional: <code>customerID</code>, <code>first_name</code>, and account state (<code>vulnerable</code>, <code>escalated</code>, <code>contacts_this_month</code>, <code>discount_pct_used_this_quarter</code>). Missing account state = unknown = needs approval.</p>
          </label>
          <div className="row" style={{ marginTop: 14 }}>
            <button className="btn btn-ghost btn-sm" onClick={template}><Download size={14} /> Download an empty template</button>
          </div>
        </Card>

        {err && <Notice kind="danger">{err}</Notice>}
        {progress && <Card><Loading text={`${progress.what} ${progress.done.toLocaleString()} of ${progress.total.toLocaleString()}...`} /></Card>}

        {res && (
          <>
            <div className="grid g4">
              <Card tight><Stat k="Rows" v={(res.results.length + res.rejected.length).toLocaleString()} /></Card>
              <Card tight><Stat k="Scored" v={res.results.length.toLocaleString()} /></Card>
              <Card tight><Stat k="Rejected" v={res.rejected.length.toLocaleString()} s={res.rejected.length ? 'see the reasons below' : 'none'} /></Card>
              <Card tight><Stat k="Flagged" v={flagged.length.toLocaleString()} s={`at threshold ${num(res.threshold)}${res.threshold_is_placeholder ? ' (placeholder)' : ''}`} /></Card>
            </div>

            <Card title="Ranked by churn probability" sub="Highest risk first." action={<button className="btn btn-ghost btn-sm" onClick={exportScored}><Download size={14} /> Download all scored rows</button>}>
              <div className="stack">
                <Field label={`Explore a different cut-off: ${cut.toFixed(2)}`} hint={`exploration only; the served threshold is ${num(meta.threshold)}`}>
                  <input type="range" min="0.05" max="0.95" step="0.01" value={cut} onChange={e => setCut(Number(e.target.value))} />
                </Field>
                <p className="small"><strong>{above.length.toLocaleString()}</strong> of {res.results.length.toLocaleString()} customers ({pct(above.length / Math.max(1, res.results.length))}) at or above {cut.toFixed(2)}.</p>
                {above.length === 0 ? <Empty>No customer at or above this cut-off.</Empty> : (
                  <div className="table-wrap">
                    <table className="tbl">
                      <thead><tr><th>Customer</th><th>Churn probability</th><th>Contract</th><th className="num">Tenure</th><th className="num">Monthly</th><th>Internet</th></tr></thead>
                      <tbody>
                        {above.slice(0, 200).map((r, i) => (
                          <tr key={r.ref + i}>
                            <td><strong>{r.ref}</strong></td>
                            <td><RiskCell p={r.churn_probability} threshold={res.threshold} /></td>
                            <td>{r.customer.Contract}</td>
                            <td className="num">{r.customer.tenure}</td>
                            <td className="num">{r.customer.MonthlyCharges.toFixed(2)}</td>
                            <td>{r.customer.InternetService}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {above.length > 200 && <p className="tiny muted">Showing the first 200. Download the file for all rows.</p>}
              </div>
            </Card>

            {flagged.length > 0 && (
              <Card title="Send flagged customers to the review queue" sub="Each one gets a recommendation; anything that needs approval waits for a named person. Nothing is sent.">
                <div className="row">
                  <Field label="How many (highest risk first)" hint={`${queueable.length} have a usable customerID`}>
                    <input type="number" min="1" max={Math.max(1, queueable.length)} value={queueN} onChange={e => setQueueN(Math.max(1, Math.min(queueable.length, Number(e.target.value) || 1)))} style={{ width: 160 }} />
                  </Field>
                  <button className="btn btn-primary" style={{ alignSelf: 'end' }} disabled={!queueable.length || !!progress} onClick={queue}><ListChecks size={16} /> Queue {Math.min(queueN, queueable.length)} customer(s)</button>
                </div>
                {flagged.length > queueable.length && <p className="tiny muted" style={{ marginTop: 10 }}>Rows without a valid <code>customerID</code> (letters, digits, - or _) cannot be queued.</p>}
                {queued && (
                  <div style={{ marginTop: 14 }}>
                    <Notice kind="success" title="Queued.">
                      {Object.entries(queued.counts).map(([k, v]) => `${v} ${humanize(k).toLowerCase()}`).join(', ') || 'nothing new'}{queued.skipped ? `. Skipped ${queued.skipped} with an open case already.` : '.'} <a href="#/queue">Open the review queue</a>
                    </Notice>
                  </div>
                )}
              </Card>
            )}

            {res.rejected.length > 0 && (
              <Card title={`Rejected rows (${res.rejected.length})`} sub="Each of these needs fixing in the file.">
                <More title="Show the reasons" open={res.rejected.length <= 10}>
                  <div className="table-wrap">
                    <table className="tbl"><thead><tr><th>Row</th><th>Reason</th></tr></thead>
                      <tbody>{res.rejected.slice(0, 500).map((r, i) => <tr key={i}><td><strong>{r.ref}</strong></td><td className="small">{r.reason}</td></tr>)}</tbody>
                    </table>
                  </div>
                </More>
              </Card>
            )}
            <div className="row"><Badge tone="grey">Scores describe the model. They are not a promise about any one customer.</Badge></div>
          </>
        )}
      </Body>
      {toast}
    </>
  )
}
