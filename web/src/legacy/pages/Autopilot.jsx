import { useCallback, useEffect, useRef, useState } from 'react'
import { Activity, ArrowRight, Bot, Brain, FlaskConical, HeartPulse, LayoutGrid, Moon, Pause, PhoneOff, Play, Radar, RadioTower, Receipt, RotateCcw, ShieldAlert, Siren, Sparkles, UserCheck, Wrench, Zap } from 'lucide-react'
import { api } from '../../api.js'
import { useApiCall } from '../state.jsx'
import { Loading, Notice } from '../components/ui.jsx'
import { Detail } from './Incidents.jsx'

const SEG = {
  persuadable: { label: 'Persuadable', color: '#2fbf8a', blurb: 'worth an approved offer' },
  sure_thing: { label: 'Sure thing', color: '#4f9cf9', blurb: 'stays anyway' },
  lost_cause: { label: 'Lost cause', color: '#9aa8bd', blurb: 'offer will not help' },
  sleeping_dog: { label: 'Sleeping dog', color: '#ffb84d', blurb: 'do not disturb' },
}
const LEVEL_COLOR = { calm: '#5fd3a6', annoyed: '#ffc44d', angry: '#ff5d5d' }
const FEED_ICON = {
  system: Bot, incident: Siren, angry: Zap, segment: LayoutGrid, twin: FlaskConical, task: Wrench, autofix: HeartPulse, queued: UserCheck,
  hold: ShieldAlert, escalated: ShieldAlert, calmed: Sparkles,
}
const FEED_TONE = { incident: 'red', angry: 'red', twin: 'blue', autofix: 'green', queued: 'yellow', calmed: 'green', escalated: 'yellow', hold: 'grey' }
const INCIDENT_BTN = [
  ['tower_outage', 'Tower outage', RadioTower],
  ['late_night_outage', 'Late-night outage', Moon],
  ['billing_glitch', 'Billing glitch', Receipt],
  ['support_backlog', 'Support backlog', PhoneOff],
]

function usePoll(fn, ms) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const alive = useRef(true)
  const tick = useCallback(async () => {
    try { const d = await fn(); if (alive.current) { setData(d); setError(null) } } catch (e) { if (alive.current) setError(e) }
  }, [fn])
  useEffect(() => {
    alive.current = true
    tick()
    const t = setInterval(tick, ms)
    return () => { alive.current = false; clearInterval(t) }
  }, [tick, ms])
  return { data, error, refresh: tick, set: setData }
}

function ago(iso) {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`
}

function useFlash(value) {
  const prev = useRef(value)
  const [on, setOn] = useState(false)
  useEffect(() => {
    if (value !== prev.current) { prev.current = value; setOn(true); const t = setTimeout(() => setOn(false), 1100); return () => clearTimeout(t) }
  }, [value])
  return on
}

function Stage({ icon: I, title, sub, value }) {
  const flash = useFlash(value)
  return (
    <div className={`ap-stage ${flash ? 'flash' : ''}`}>
      <div className="ap-stage-ico"><I size={20} /></div>
      <strong>{title}</strong>
      <span className="ap-stage-n">{value}</span>
      <span className="ap-stage-sub">{sub}</span>
    </div>
  )
}

function Kpi({ k, v, tone }) {
  const flash = useFlash(v)
  return <div className={`ap-kpi ${tone || ''} ${flash ? 'flash' : ''}`}><span className="ap-kpi-v">{v}</span><span className="ap-kpi-k">{k}</span></div>
}

// x: SIMULATED uplift, y: churn probability. The four regions are exactly the rules in app.incidents.classify.
function RiskMap({ customers, thr, margin, selected, onPick }) {
  const W = 760, H = 460, L = 54, R = 16, T = 16, B = 46
  const xmin = -0.12, xmax = 0.42
  const X = u => L + ((u - xmin) / (xmax - xmin)) * (W - L - R)
  const Y = p => T + (1 - p) * (H - T - B)
  const regions = [
    { k: 'sleeping_dog', x0: xmin, x1: -margin, y0: 1, y1: 0 },
    { k: 'sure_thing', x0: -margin, x1: xmax, y0: thr, y1: 0 },
    { k: 'lost_cause', x0: -margin, x1: margin, y0: 1, y1: thr },
    { k: 'persuadable', x0: margin, x1: xmax, y0: 1, y1: thr },
  ]
  return (
    <svg className="ap-map" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Customers by simulated uplift and churn risk">
      {regions.map(r => {
        const right = r.k === 'persuadable'
        const x = right ? X(r.x1) - 10 : X(r.x0) + (r.k === 'lost_cause' ? 6 : 10)
        const y = Y(r.y0) + 22
        return (
          <g key={r.k}>
            <rect x={X(r.x0)} y={Y(r.y0)} width={X(r.x1) - X(r.x0)} height={Y(r.y1) - Y(r.y0)} fill={SEG[r.k].color} opacity=".1" />
            <text x={x} y={y} textAnchor={right ? 'end' : 'start'} className="ap-map-label" fill={SEG[r.k].color}>{SEG[r.k].label.toUpperCase()}</text>
            {r.k !== 'lost_cause' && <text x={x} y={y + 15} textAnchor={right ? 'end' : 'start'} className="ap-map-sub">{SEG[r.k].blurb}</text>}
          </g>
        )
      })}
      <line x1={L} x2={W - R} y1={Y(thr)} y2={Y(thr)} className="ap-map-line" />
      <line x1={X(margin)} x2={X(margin)} y1={T} y2={H - B} className="ap-map-line" />
      <line x1={X(-margin)} x2={X(-margin)} y1={T} y2={H - B} className="ap-map-line" />
      {[0, .25, .5, .75, 1].map(p => <text key={p} x={L - 8} y={Y(p) + 4} textAnchor="end" className="ap-map-tick">{Math.round(p * 100)}%</text>)}
      {[-0.1, 0, 0.1, 0.2, 0.3, 0.4].map(u => <text key={u} x={X(u)} y={H - B + 18} textAnchor="middle" className="ap-map-tick">{u > 0 ? '+' : ''}{u.toFixed(1)}</text>)}
      <text x={(L + W - R) / 2} y={H - 8} textAnchor="middle" className="ap-map-axis">SIMULATED uplift: how much an offer is expected to help</text>
      <text transform={`translate(14 ${(T + H - B) / 2}) rotate(-90)`} textAnchor="middle" className="ap-map-axis">Churn probability (calibrated model)</text>
      {customers.filter(c => c.uplift != null).map(c => (
        <g key={c.ref} className={`ap-dot ${c.level} ${selected === c.ref ? 'sel' : ''}`} transform={`translate(${X(c.uplift)} ${Y(c.risk)})`} onClick={() => onPick(c.ref)}>
          {c.level === 'angry' && <circle className="ap-pulse" r="9" fill="none" stroke={LEVEL_COLOR.angry} />}
          <circle r={c.level === 'angry' ? 7.5 : c.level === 'annoyed' ? 6 : 4.5} fill={LEVEL_COLOR[c.level]} stroke="#031d3f" strokeWidth="1.5" />
          {(c.level === 'angry' || selected === c.ref) && <text y="-12" textAnchor="middle" className="ap-dot-name">{c.name}</text>}
          <title>{`${c.name} · ${c.cell}\nchurn ${Math.round(c.risk * 100)}% · uplift ${c.uplift >= 0 ? '+' : ''}${c.uplift.toFixed(2)}\nfrustration ${Math.round(c.frustration * 100)}% (${c.level})`}</title>
        </g>
      ))}
    </svg>
  )
}

function Feed({ items, onPick }) {
  return (
    <div className="ap-feed">
      {items.length === 0 && <p className="ap-muted">Waiting for the first event...</p>}
      {[...items].sort((a, b) => b.ts.localeCompare(a.ts) || a.id - b.id).map(f => {
        const I = FEED_ICON[f.kind] || Activity
        return (
          <button key={f.id} type="button" className={`ap-feed-item ${FEED_TONE[f.kind] || ''}`} onClick={() => f.ref && onPick(f.ref)}>
            <span className="ap-feed-ico"><I size={15} /></span>
            <span className="ap-feed-text">{f.text}</span>
            <span className="ap-feed-time">{ago(f.ts)}</span>
          </button>
        )
      })}
    </div>
  )
}

export default function Autopilot() {
  const poll = usePoll(useCallback(() => api('/autopilot/state'), []), 1500)
  const cfg = useApiCall(() => api('/incidents/config'), 'cfg', { delay: 0 })
  const [sel, setSel] = useState(null)
  const [busy, setBusy] = useState(null)
  const [err, setErr] = useState(null)
  const file = useRef(null)
  const st = poll.data

  const act = async (what, fn) => {
    setBusy(what); setErr(null)
    try { const r = await fn(); if (r && r.customers) poll.set(r); await poll.refresh() } catch (e) { setErr(e.message) } finally { setBusy(null) }
  }
  const start = () => act('start', () => api('/autopilot/start', { customers: 40 }))
  const stop = () => act('stop', () => api('/autopilot/stop', {}))
  const reset = () => act('reset', async () => { setSel(null); await api('/autopilot/reset', {}) })
  const fire = kind => act(kind, () => api('/autopilot/incident', { kind }))
  const pick = ref => { setSel(ref); setTimeout(() => file.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50) }

  if (poll.error && poll.error.status === 401) return <div className="wrap" style={{ padding: 40 }}><Notice kind="danger" title="API key required.">Enter the key on the Review queue page, then come back.</Notice></div>
  if (!st) return <div className="wrap" style={{ padding: 40 }}><Loading text="Connecting to the autopilot..." /></div>

  const s = st.stats, empty = st.customers.length === 0
  const warming = st.warm && st.warm.total > 0 && st.warm.done < st.warm.total
  const sel_c = st.customers.find(c => c.ref === sel)

  return (
    <div className="ap">
      <section className="ap-hero">
        <div className="wrap ap-hero-grid">
          <div>
            <div className="ap-eyebrow"><span className={`ap-live ${st.running ? 'on' : ''}`} /> {st.running ? 'AUTOPILOT LIVE' : 'AUTOPILOT PAUSED'} · SIMULATED</div>
            <h1>Customers who are about to leave get <span className="ap-hl">helped before they do</span></h1>
            <p>Nobody types anything. Evergreen watches every customer, scores them with the calibrated churn model, tests fixes on the digital twin, repairs what is technical by itself, and asks a human only when someone has to be contacted.</p>
            <div className="ap-controls">
              {st.running
                ? <button className="ap-btn" disabled={busy} onClick={stop}><Pause size={16} /> Pause autopilot</button>
                : <button className="ap-btn primary" disabled={busy} onClick={start}><Play size={16} /> {empty ? 'Start autopilot' : 'Resume autopilot'}</button>}
              {!empty && <button className="ap-btn ghost" disabled={busy} onClick={reset}><RotateCcw size={15} /> Reset demo</button>}
            </div>
            {err && <p className="ap-err">{err}</p>}
          </div>
          <div className="ap-fire">
            <h4>Make something go wrong</h4>
            <p className="ap-muted">Each button fires a simulated incident. Watch what the autopilot does next.</p>
            <div className="ap-fire-grid">
              {INCIDENT_BTN.map(([k, label, I]) => (
                <button key={k} className="ap-fire-btn" disabled={empty || busy} onClick={() => fire(k)}><I size={18} />{label}{busy === k && <span className="ap-spin" />}</button>
              ))}
            </div>
            {warming && <p className="ap-muted">Warming up the digital twin: {st.warm.done}/{st.warm.total} customers</p>}
          </div>
        </div>
      </section>

      {empty ? (
        <div className="wrap ap-empty"><Bot size={44} /><h3>The autopilot is not running yet</h3><p>Press <strong>Start autopilot</strong>. It loads 40 real Telco customers and starts watching them.</p></div>
      ) : (
        <>
          <section className="wrap ap-flow" aria-label="What the autopilot does">
            <Stage icon={Radar} title="Detect" sub="events watched" value={s.events} />
            <ArrowRight className="ap-arrow" size={18} />
            <Stage icon={Brain} title="Score" sub="customers triggered" value={s.recoveries_opened} />
            <ArrowRight className="ap-arrow" size={18} />
            <Stage icon={LayoutGrid} title="Segment" sub="sorted into 4 groups" value={s.recoveries_opened} />
            <ArrowRight className="ap-arrow" size={18} />
            <Stage icon={FlaskConical} title="Digital twin" sub="fixes tested" value={s.twin_tests} />
            <ArrowRight className="ap-arrow" size={18} />
            <Stage icon={HeartPulse} title="Auto-heal" sub="faults repaired" value={s.auto_fixed} />
            <ArrowRight className="ap-arrow" size={18} />
            <Stage icon={UserCheck} title="Human approves" sub="waiting for a person" value={s.queued_for_human} />
          </section>

          <section className="wrap ap-kpis">
            <Kpi k="customers watched" v={s.customers} />
            <Kpi k="angry right now" v={s.angry_now} tone="red" />
            <Kpi k="annoyed" v={s.annoyed_now} tone="amber" />
            <Kpi k="repairs done automatically" v={s.auto_fixed} tone="green" />
            <Kpi k="waiting for a human" v={s.queued_for_human} tone="yellow" />
            <Kpi k="steps automated" v={s.automated_steps} tone="blue" />
          </section>

          <section className="wrap ap-main">
            <div className="ap-panel">
              <div className="ap-panel-head">
                <h3>Customer map</h3>
                <div className="ap-legend">
                  {Object.entries(LEVEL_COLOR).map(([k, c]) => <span key={k}><i style={{ background: c }} />{k}</span>)}
                </div>
              </div>
              <RiskMap customers={st.customers} thr={st.risk_threshold} margin={st.uplift_margin} selected={sel} onPick={pick} />
              <p className="ap-foot">Each dot is a real Telco customer. Position = churn probability (served calibrated model, threshold {st.risk_threshold.toFixed(3)} is a PLACEHOLDER) and SIMULATED uplift. Colour = how frustrated the simulated service events have made them.</p>
            </div>
            <div className="ap-panel">
              <div className="ap-panel-head"><h3>Live autopilot feed</h3><span className="ap-muted">tick {st.tick}</span></div>
              <Feed items={st.feed} onPick={pick} />
            </div>
          </section>

          <section className="wrap ap-seg">
            {Object.entries(SEG).map(([k, v]) => (
              <div key={k} className="ap-seg-card" style={{ borderColor: v.color }}>
                <span className="ap-seg-n" style={{ color: v.color }}>{s.by_segment[k]}</span>
                <strong>{v.label}</strong>
                <span className="ap-muted">{v.blurb}</span>
              </div>
            ))}
          </section>

          <section className="wrap ap-file" ref={file}>
            {sel && cfg.data ? (
              <>
                <div className="ap-file-head">
                  <h2>{sel_c ? `${sel_c.name}'s file` : sel}</h2>
                  <button className="ap-btn ghost" onClick={() => setSel(null)}>Close</button>
                </div>
                <div className="ap-light"><Detail key={sel} refId={sel} cfg={cfg.data} onChanged={poll.refresh} /></div>
              </>
            ) : (
              <p className="ap-muted ap-pick">Click a dot or a feed entry to open that customer's full file: timeline, recovery plan and the digital twin.</p>
            )}
          </section>
        </>
      )}
    </div>
  )
}
