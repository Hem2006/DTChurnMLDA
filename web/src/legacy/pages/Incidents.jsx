import { useState } from 'react'
import { Check, Play, RefreshCw, RotateCcw, Send, UserPlus } from 'lucide-react'
import { api } from '../../api.js'
import { UNKNOWN_STATE, useApiCall, useApp } from '../state.jsx'
import { Badge, Body, Card, Empty, Field, Loading, Meter, More, Notice, PageHero, Select } from '../components/ui.jsx'
import { NotCausal, ScenarioCard } from './WhatIf.jsx'
import { AccountStateForm } from '../components/forms.jsx'
import { DEFAULT_CUSTOMER, humanize, pct, signed } from '../lib.js'

const LEVEL_TONE = { calm: 'green', annoyed: 'amber', angry: 'red' }
const SEG_TONE = { persuadable: 'green', sure_thing: 'navy', lost_cause: 'grey', sleeping_dog: 'amber' }
const SEG_LABEL = { persuadable: 'Persuadable', sure_thing: 'Sure thing', lost_cause: 'Lost cause', sleeping_dog: 'Sleeping dog' }
const CONTACT_TONE = { ready_offer: 'green', ready_apology: 'green', pending_approval: 'amber', no_offer: 'grey', no_contact: 'grey', blocked: 'red', escalate_to_human: 'red', unclassified: 'grey' }

// Starting points for the demo only. They are made-up customers; the segment each ends up in comes from the model, not from this list.
const PRESETS = {
  'New fibre customer, month-to-month': { ...DEFAULT_CUSTOMER, tenure: 5, MonthlyCharges: 80 },
  'Long-term two-year customer': { ...DEFAULT_CUSTOMER, tenure: 70, Contract: 'Two year', MonthlyCharges: 40, Partner: 'Yes', Dependents: 'Yes' },
  'Mid-tenure DSL customer': { ...DEFAULT_CUSTOMER, InternetService: 'DSL', MonthlyCharges: 55, tenure: 14 },
  'One-year DSL customer with tech support': { ...DEFAULT_CUSTOMER, InternetService: 'DSL', MonthlyCharges: 53.8, tenure: 11, Contract: 'One year', TechSupport: 'Yes', DeviceProtection: 'Yes', PaperlessBilling: 'No', PaymentMethod: 'Bank transfer (automatic)' },
}
const CURRENT = 'Customer from the Score page'

function when(iso) { return new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) }
// Event times are shown as stored: the hour is the customer-local hour the event was logged with, not the viewer's time zone.
function eventTime(iso) { return new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }) }

function RegisterCard({ onDone }) {
  const { customer } = useApp()
  const [preset, setPreset] = useState(Object.keys(PRESETS)[0])
  const [ref, setRef] = useState('')
  const [name, setName] = useState('')
  const [state, setState] = useState(UNKNOWN_STATE)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const go = async () => {
    setBusy(true); setErr(null)
    const id = ref.trim() || 'cust-' + Math.random().toString(36).slice(2, 7)
    try {
      await api('/incidents/customers', { customer_ref: id, first_name: name.trim() || null, customer: preset === CURRENT ? customer : PRESETS[preset], account_state: state })
      setRef(''); setName(''); onDone(id)
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  return (
    <Card title="Add a customer to watch" sub="Model inputs plus operational state. Events are logged against the customer ref.">
      <div className="stack-sm">
        <Field label="Customer profile"><Select value={preset} onChange={setPreset} options={[...Object.keys(PRESETS), CURRENT]} /></Field>
        <div className="form-grid">
          <Field label="Customer ref" hint="letters, digits, - _"><input value={ref} onChange={e => setRef(e.target.value)} placeholder="auto" maxLength={64} /></Field>
          <Field label="First name" hint="optional"><input value={name} onChange={e => setName(e.target.value)} maxLength={40} /></Field>
        </div>
        <More title="Account state (default: unknown, which forces approval)"><AccountStateForm value={state} onChange={setState} /></More>
        {err && <Notice kind="danger">{err}</Notice>}
        <button className="btn btn-primary" disabled={busy} onClick={go}><UserPlus size={16} /> Add customer</button>
      </div>
    </Card>
  )
}

function CustomerList({ list, current, onPick }) {
  if (!list.data) return <Card><Loading /></Card>
  const rows = list.data.customers
  return (
    <Card title="Customers" action={<button className="btn btn-ghost btn-sm" onClick={list.reload}><RefreshCw size={14} /></button>}>
      {rows.length === 0 ? <Empty>No customers yet. Add one, then play a storyline.</Empty> : (
        <div className="stack-sm">
          {rows.map(c => (
            <button key={c.customer_ref} type="button" className={`pick ${current === c.customer_ref ? 'on' : ''}`} onClick={() => onPick(c.customer_ref)}>
              <span className="row spread"><strong>{c.first_name ? `${c.first_name} · ` : ''}{c.customer_ref}</strong><Badge tone={LEVEL_TONE[c.level]}>{c.level}</Badge></span>
              <span className="row spread small muted">
                <span>{c.n_events} event{c.n_events === 1 ? '' : 's'}{c.open_recovery ? ' · recovery open' : ''}</span>
                {c.segment ? <Badge tone={SEG_TONE[c.segment]}>{SEG_LABEL[c.segment]}</Badge> : <span>unsegmented</span>}
              </span>
            </button>
          ))}
        </div>
      )}
    </Card>
  )
}

function EventForm({ cfg, refId, onLogged }) {
  const kinds = Object.keys(cfg.event_kinds)
  const [kind, setKind] = useState('outage')
  const [sev, setSev] = useState(3)
  const [work, setWork] = useState(false)
  const [at, setAt] = useState('')
  const [note, setNote] = useState('')
  const [err, setErr] = useState(null)
  const go = async () => {
    setErr(null)
    try {
      const r = await api('/incidents/events', { customer_ref: refId, kind, severity: Number(sev), work_impact: work, note: note.trim() || null, occurred_at: at ? at + ':00' : null })
      setNote(''); onLogged(r)
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="stack-sm">
      <div className="form-grid">
        <Field label="What happened"><Select value={kind} onChange={setKind} options={kinds} format={humanize} /></Field>
        <Field label="Severity (1-5)"><input type="number" min="1" max="5" value={sev} onChange={e => setSev(e.target.value)} /></Field>
        <Field label="When" hint="blank = now; the hour is customer-local"><input type="datetime-local" value={at} onChange={e => setAt(e.target.value)} /></Field>
        <Field label="Note"><input value={note} onChange={e => setNote(e.target.value)} maxLength={300} /></Field>
      </div>
      <label className="row small"><input type="checkbox" checked={work} onChange={e => setWork(e.target.checked)} /> It affected the customer's work</label>
      {err && <Notice kind="danger">{err}</Notice>}
      <button className="btn btn-outline" onClick={go}><Send size={15} /> Log event</button>
    </div>
  )
}

function TwinPanel({ customer, threshold }) {
  const run = useApiCall(() => api('/twin/run', { customer }), JSON.stringify(customer), { delay: 0 })
  const results = run.data?.results.filter(r => r.name !== 'do_nothing') || []
  return (
    <Card title="Digital twin: test the fix on this customer" sub="The same twin as the Digital twin page, run on this customer's inputs. The offer in the plan above is picked from this ranking.">
      <div className="stack">
        <NotCausal />
        {run.error ? <Notice kind="danger">{run.error.message}</Notice> : !run.data ? <Loading text="Simulating..." /> : results.length === 0 ? <Empty>No scenario applies to this customer.</Empty> : (
          <>
            <p className="small muted">Baseline risk {pct(run.data.baseline_risk)}. Ranked by {humanize(run.data.ranked_by)}.</p>
            {results.map((r, i) => <ScenarioCard key={r.name} r={r} rank={i + 1} threshold={threshold} />)}
          </>
        )}
        <a href="#/what-if">Open the full digital twin</a>
      </div>
    </Card>
  )
}

export function Detail({ refId, cfg, onChanged }) {
  const [tick, setTick] = useState(0)
  const d = useApiCall(() => api(`/incidents/customers/${refId}`), refId + ':' + tick, { delay: 0 })
  const [story, setStory] = useState(Object.keys(cfg.storylines)[0])
  const [who, setWho] = useState('')
  const [note, setNote] = useState('')
  const [err, setErr] = useState(null)
  const refresh = () => { setTick(t => t + 1); onChanged() }
  const run = async (fn) => { setErr(null); try { await fn(); refresh() } catch (e) { setErr(e.message) } }
  if (d.error) return <Card><Notice kind="danger">{d.error.message}</Notice></Card>
  if (!d.data) return <Card><Loading /></Card>
  const x = d.data, f = x.frustration, plan = x.plan, rc = plan.customer_contact, rec = x.recovery
  const events = [...x.events].reverse()

  return (
    <div className="stack">
      <Card title={`${x.first_name ? x.first_name + ' · ' : ''}${refId}`} sub="Frustration is rebuilt from the event log every time. SIMULATED behaviour model."
        action={<Badge tone={LEVEL_TONE[f.level]}>{f.level}</Badge>}>
        <div className="stack-sm">
          <div className="row spread"><span className="small muted">Frustration score</span><strong className="mono">{pct(f.score)}</strong></div>
          <Meter value={f.score} marker={cfg.levels.angry} hot={f.level === 'angry'} />
          <p className="tiny muted">Annoyed from {pct(cfg.levels.annoyed)}, angry from {pct(cfg.levels.angry)} (marker). Self-healing starts at "{cfg.trigger_level}".</p>
          {f.notes.map(n => <Notice key={n} kind="warn">{n}</Notice>)}
          {f.drivers.length > 0 && <p className="small">Biggest drivers: {f.drivers.slice(0, 3).map(r => `${humanize(r.kind)}${r.modifiers.length ? ` (${r.modifiers.join(', ')})` : ''}`).join('; ')}.</p>}
        </div>
      </Card>

      <More title="Manual controls: simulate or log an event yourself">
      <div className="grid g2">
        <Card title="Simulate behaviour" sub="Plays a canned bad experience into the log, marked SIMULATED.">
          <div className="stack-sm">
            <Field label="Storyline"><Select value={story} onChange={setStory} options={Object.keys(cfg.storylines)} format={humanize} /></Field>
            <p className="tiny muted">{cfg.storylines[story]}</p>
            <div className="row">
              <button className="btn btn-primary" onClick={() => run(() => api('/incidents/simulate', { customer_ref: refId, storyline: story }))}><Play size={15} /> Play storyline</button>
              <button className="btn btn-ghost" onClick={() => run(() => api(`/incidents/customers/${refId}/reset-simulated`, {}))}><RotateCcw size={15} /> Remove simulated</button>
            </div>
          </div>
        </Card>
        <Card title="Log an event" sub="What the phone, network and support systems would report.">
          <EventForm cfg={cfg} refId={refId} onLogged={refresh} />
        </Card>
      </div>
      </More>
      {err && <Notice kind="danger">{err}</Notice>}

      <Card title="Segment and recovery plan" sub="From the calibrated churn model and the SIMULATED uplift. Frustration decides when to act, not the segment.">
        <div className="stack">
          <div className="seg-card">
            {x.segment ? <Badge tone={SEG_TONE[x.segment]}>{SEG_LABEL[x.segment]}</Badge> : <Badge tone="grey">Not segmented</Badge>}
            <strong>{plan.headline}</strong>
            <span className="small muted">{plan.segment_reason}</span>
            <span className="tiny muted">
              Churn probability {pct(x.risk.churn_probability)} (threshold {x.risk.threshold.toFixed(3)}{x.risk.threshold_is_placeholder ? ', PLACEHOLDER' : ''})
              {x.simulated_uplift && <> · SIMULATED uplift {signed(x.simulated_uplift.simulated_uplift)}</>}
            </span>
          </div>

          <div>
            <h4>Automatic internal tasks</h4>
            {plan.tasks.length === 0 ? <p className="small muted">None: no open event to fix.</p> : (
              <ul className="plain">
                {plan.tasks.map(t => <li key={t.task}><Badge tone="navy">{humanize(t.task)}</Badge> <span className="small">{t.description} <span className="muted">{t.why}.</span></span></li>)}
              </ul>
            )}
            <p className="tiny muted">Internal work items. Nothing is sent to the customer from here.</p>
          </div>

          <div>
            <h4>What may reach the customer</h4>
            <div className="row"><Badge tone={CONTACT_TONE[rc.status]}>{humanize(rc.status)}</Badge>{rc.type && <span className="small">{humanize(rc.type)}</span>}
              {rc.recommendation?.action && <span className="small">offer: <strong>{humanize(rc.recommendation.action)}</strong></span>}</div>
            <ul className="plain small muted">{rc.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
            {(rc.status === 'ready_offer' || rc.status === 'pending_approval') && rc.type === 'retention_offer' && <a href="#/recommend">Draft the message on the Recommend page</a>}
          </div>

          {rec ? (
            <div className="stack-sm">
              <Notice kind="info" title="Self-healing run is open.">Opened {when(rec.ts)}, refreshed {when(rec.updated_ts)}.</Notice>
              <div className="form-grid">
                <Field label="Closed by"><input value={who} onChange={e => setWho(e.target.value)} placeholder="your name" maxLength={80} /></Field>
                <Field label="Note"><input value={note} onChange={e => setNote(e.target.value)} maxLength={500} /></Field>
              </div>
              <button className="btn btn-outline" disabled={!who.trim()} onClick={() => run(() => api(`/incidents/recoveries/${rec.id}/close`, { closed_by: who.trim(), note: note.trim() || null }))}><Check size={15} /> Close recovery</button>
            </div>
          ) : x.triggered ? null : <p className="small muted">Self-healing has not triggered: frustration is below "{cfg.trigger_level}".</p>}
        </div>
      </Card>

      <TwinPanel customer={x.customer} threshold={x.risk.threshold} />

      <Card title="Timeline">
        {events.length === 0 ? <Empty>No events yet.</Empty> : (
          <ul className="tl">
            {events.map(e => (
              <li key={e.id}>
                <span className="row spread"><strong>{humanize(e.kind)}</strong><span className="tiny muted">{eventTime(e.occurred_at)}</span></span>
                <span className="row small">
                  <Badge tone={e.source === 'simulated' ? 'yellow' : 'grey'}>{e.source === 'simulated' ? 'SIMULATED' : 'logged'}</Badge>
                  <Badge>severity {e.severity}</Badge>
                  {e.work_impact && <Badge tone="amber">hit their work</Badge>}
                  {e.resolved ? <Badge tone="green">resolved</Badge> : <button className="btn btn-ghost btn-sm" onClick={() => run(() => api(`/incidents/events/${e.id}/resolve`, {}))}>Mark resolved</button>}
                </span>
                {e.note && <span className="small muted">{e.note}</span>}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

export default function Incidents() {
  const [sel, setSel] = useState(null)
  const cfg = useApiCall(() => api('/incidents/config'), 'cfg', { delay: 0 })
  const list = useApiCall(() => api('/incidents/customers'), 'list', { delay: 0 })
  const unauth = [cfg.error, list.error].find(e => e && e.status === 401)
  return (
    <>
      <PageHero eyebrow="Service incidents" title="Catch angry customers early"
        lead="Log (or simulate) outages, dropped calls and unanswered support. When a customer reaches the trigger level, the model scores them, they are sorted into persuadable, sure thing, lost cause or sleeping dog, and a recovery plan opens." />
      <Body>
        <Notice kind="label" title="SIMULATED.">Frustration weights and segment cut-offs are assumptions in incidents.yaml, and uplift is simulated. The churn model never saw service events. Nothing is sent from this page.</Notice>
        {unauth ? <Notice kind="danger" title="API key required.">Enter the key on the Review queue page, then come back.</Notice>
          : cfg.error ? <Notice kind="danger">{cfg.error.message}</Notice>
          : !cfg.data ? <Loading /> : (
            <div className="inc-grid">
              <div className="stack">
                <RegisterCard onDone={id => { setSel(id); list.reload() }} />
                <CustomerList list={list} current={sel} onPick={setSel} />
              </div>
              {sel ? <Detail key={sel} refId={sel} cfg={cfg.data} onChanged={list.reload} /> : <Card><Empty>Pick or add a customer.</Empty></Card>}
            </div>
          )}
      </Body>
    </>
  )
}
