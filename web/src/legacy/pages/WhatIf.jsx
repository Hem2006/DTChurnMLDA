import { useState } from 'react'
import { api } from '../../api.js'
import { useApiCall, useApp } from '../state.jsx'
import { Badge, Body, Card, Empty, Field, Loading, Meter, More, Notice, PageHero, Select } from '../components/ui.jsx'
import { CustomerForm, ResetCustomer } from '../components/forms.jsx'
import { ADDONS, changesText, humanize, label, num, pct, signed } from '../lib.js'

export function NotCausal() {
  return (
    <Notice kind="label" title="MODEL SENSITIVITY, NOT A CAUSAL ESTIMATE.">
      This shows how the model's score moves when inputs are edited, not what an action would achieve.
    </Notice>
  )
}

function Compare({ before, after, threshold }) {
  return (
    <div className="compare">
      <div className="line"><span>Now</span><Meter value={before} marker={threshold} hot={before >= threshold} /><span className="mono">{pct(before)}</span></div>
      <div className="line"><span>After</span><div className="meter"><i className="after" style={{ width: Math.min(1, after) * 100 + '%', background: 'var(--green)' }} /><b style={{ left: `calc(${threshold * 100}% - 1px)` }} /></div><span className="mono">{pct(after)}</span></div>
    </div>
  )
}

export function ScenarioCard({ r, threshold, rank }) {
  return (
    <div className="card card-tight stack-sm">
      <div className="row spread">
        <div className="row">
          <Badge tone="navy">#{rank}</Badge>
          <h4>{humanize(r.name)}</h4>
        </div>
        <Badge tone={r.confidence === 'low' ? 'amber' : 'green'}>{r.confidence} confidence</Badge>
      </div>
      <div className="chips"><span className="chip">{changesText(r.changes)}</span></div>
      <Compare before={r.baseline_risk} after={r.risk_after} threshold={threshold} />
      <div className="row small">
        <span>Risk drop <strong className="mono">{signed(r.risk_drop)}</strong></span>
        <span className="muted">· logistic check {signed(r.reference_logreg?.risk_drop)}</span>
        {r.flagged_after ? <Badge tone="red">still flagged</Badge> : <Badge tone="green">below threshold</Badge>}
      </div>
      <p className="tiny muted">Expected net value: {r.expected_net_value == null ? r.expected_net_value_reason : num(r.expected_net_value, 2)}</p>
      {r.warnings.map(w => <Notice key={w} kind="warn">{w}</Notice>)}
      {r.confidence === 'low' && <p className="tiny muted">{r.confidence_reason}</p>}
    </div>
  )
}

const EDITABLE = ['Contract', 'PaymentMethod', 'PaperlessBilling']

export default function WhatIf() {
  const { meta, customer, setCustomer } = useApp()
  const [changes, setChanges] = useState({})
  const ckey = JSON.stringify(customer)
  const run = useApiCall(() => api('/twin/run', { customer }), ckey)
  const sim = useApiCall(() => api('/simulate', { customer, changes }), ckey + JSON.stringify(changes), { enabled: Object.keys(changes).length > 0 })
  const lv = meta.categorical_levels
  const noNet = customer.InternetService === 'No'
  const valueOf = k => changes[k] ?? customer[k]
  const edit = (k, v) => setChanges(c => { const n = { ...c }; if (v === customer[k] || v === 0) delete n[k]; else n[k] = v; return n })
  const t = meta.threshold
  const results = run.data?.results.filter(r => r.name !== 'do_nothing') || []

  return (
    <>
      <PageHero eyebrow="Digital twin" title="The customer twin" lead="A live copy of the customer inside the churn model. Change the levers the business controls (contract, tech support, discount, payment) and watch the calibrated score move, ranked and cross-checked against a second model. Every retention offer in Evergreen is chosen from this ranking.">
        <div style={{ marginTop: 20, maxWidth: 760 }}><NotCausal /></div>
      </PageHero>
      <Body>
        <div className="split">
          <div className="stack">
            <Card title="Custom what-if" sub="Locked: tenure, demographics and monthly charges (only through the discount)." action={Object.keys(changes).length > 0 && <button className="btn btn-ghost btn-sm" onClick={() => setChanges({})}>Clear edits</button>}>
              <div className="form-grid">
                {EDITABLE.map(k => <Field key={k} label={label(k)}><Select value={valueOf(k)} onChange={v => edit(k, v)} options={lv[k]} /></Field>)}
                <Field label="Discount" hint={`${changes.discount_pct || 0}%`}>
                  <input type="range" min="0" max={meta.max_discount_pct} value={changes.discount_pct || 0} onChange={e => edit('discount_pct', Number(e.target.value))} aria-label="Discount percent" />
                </Field>
                {ADDONS.map(a => (
                  <Field key={a} label={label(a)}>
                    <Select value={valueOf(a)} onChange={v => edit(a, v)} options={noNet ? [meta.no_internet] : ['No', 'Yes']} disabled={noNet} />
                  </Field>
                ))}
              </div>
              <hr className="divider" />
              {Object.keys(changes).length === 0 ? <p className="muted small">Change any lever above to simulate it.</p>
                : sim.error ? <Notice kind="danger">{sim.error.message}</Notice>
                : !sim.data ? <Loading text="Simulating..." /> : (
                  <div className="stack-sm" style={{ opacity: sim.loading ? 0.6 : 1 }}>
                    <Compare before={sim.data.baseline_risk} after={sim.data.risk_after} threshold={t} />
                    <div className="row small">
                      <span>Risk drop <strong className="mono">{signed(sim.data.risk_drop)}</strong></span>
                      <span className="muted">· logistic check {signed(sim.data.reference_logreg?.risk_drop)}</span>
                      <Badge tone={sim.data.confidence === 'low' ? 'amber' : 'green'}>{sim.data.confidence} confidence</Badge>
                    </div>
                    {sim.data.warnings.map(w => <Notice key={w} kind="warn">{w}</Notice>)}
                    {sim.data.confidence === 'low' && <Notice kind="warn" title="LOW CONFIDENCE.">{sim.data.confidence_reason}</Notice>}
                  </div>
                )}
            </Card>
            <Card title="Scenario library" sub={run.data ? `Baseline risk ${pct(run.data.baseline_risk)} · ranked by ${humanize(run.data.ranked_by)}` : 'Scenarios from scenarios.yaml'}>
              {run.error ? <Notice kind="danger">{run.error.message}</Notice> : !run.data ? <Loading /> : results.length === 0 ? <Empty>No scenario applies to this customer.</Empty> : (
                <div className="stack">
                  {results.map((r, i) => <ScenarioCard key={r.name} r={r} rank={i + 1} threshold={t} />)}
                  {run.data.not_applicable.length > 0 && (
                    <More title={`Not applicable (${run.data.not_applicable.length})`}>
                      <ul className="list small">{run.data.not_applicable.map(n => <li key={n.name}><strong>{humanize(n.name)}</strong>: {n.reason}</li>)}</ul>
                    </More>
                  )}
                </div>
              )}
            </Card>
          </div>
          <Card title="Customer" sub="Shared with the other pages." action={<ResetCustomer onReset={c => { setChanges({}); setCustomer(c) }} />} className="sticky">
            <CustomerForm meta={meta} customer={customer} onChange={c => { setChanges({}); setCustomer(c) }} />
          </Card>
        </div>
      </Body>
    </>
  )
}
