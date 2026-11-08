import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronRight, Info, Inbox, OctagonAlert } from 'lucide-react'
import { pct } from '../lib.js'

export function Logo({ size = 30 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="rgba(255,255,255,.1)" />
      <path d="M7 15l9-8 9 8" fill="none" stroke="#FFE492" strokeWidth="3.6" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M7 23l9-8 9 8" fill="none" stroke="#4F9CF9" strokeWidth="3.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export const Hl = ({ children }) => <span className="hl">{children}</span>

export function PageHero({ eyebrow, title, lead, children }) {
  return (
    <section className="hero page-hero">
      <div className="wrap">
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {lead && <p className="lead">{lead}</p>}
        {children}
      </div>
    </section>
  )
}

export const Body = ({ children }) => <div className="wrap lift stack">{children}</div>

export function Card({ title, sub, action, children, className = '', tight }) {
  return (
    <section className={`card ${tight ? 'card-tight' : ''} ${className}`}>
      {(title || action) && (
        <div className="card-head">
          <div>
            {title && <h3>{title}</h3>}
            {sub && <p className="muted small">{sub}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  )
}

const NOTICE_ICON = { info: Info, warn: AlertTriangle, danger: OctagonAlert, success: CheckCircle2, label: OctagonAlert }

export function Notice({ kind = 'info', title, children }) {
  const I = NOTICE_ICON[kind]
  return (
    <div className={`notice ${kind}`} role={kind === 'danger' ? 'alert' : 'status'}>
      <I size={18} />
      <div>
        {title && <strong>{title} </strong>}
        {children}
      </div>
    </div>
  )
}

export const Badge = ({ tone = '', children }) => <span className={`badge ${tone}`}>{children}</span>

export function Stat({ k, v, s }) {
  return (
    <div className="stat">
      <span className="k">{k}</span>
      <span className="v">{v}</span>
      {s && <span className="s">{s}</span>}
    </div>
  )
}

export function Loading({ text = 'Loading...' }) {
  return <div className="loading"><span className="spinner" /> {text}</div>
}

export function Empty({ children, icon: I = Inbox }) {
  return <div className="empty"><I size={36} /><div>{children}</div></div>
}

export function Field({ label, hint, children }) {
  return (
    <label className="field">
      <span>{label}{hint && <span className="hint"> · {hint}</span>}</span>
      {children}
    </label>
  )
}

export function Select({ value, onChange, options, disabled, format = x => x }) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)} disabled={disabled}>
      {options.map(o => <option key={o} value={o}>{format(o)}</option>)}
    </select>
  )
}

export function Seg({ value, onChange, options }) {
  return (
    <div className="seg" role="tablist">
      {options.map(o => (
        <button key={o.value} type="button" role="tab" aria-selected={value === o.value} className={value === o.value ? 'on' : ''} onClick={() => onChange(o.value)}>
          {o.label}{o.count != null && <span className="count">{o.count}</span>}
        </button>
      ))}
    </div>
  )
}

export function Meter({ value, marker, hot }) {
  const w = Math.max(0, Math.min(1, value || 0)) * 100
  return (
    <div className={`meter ${hot ? 'hot' : ''}`}>
      <i style={{ width: w + '%' }} />
      {marker != null && <b style={{ left: `calc(${Math.min(1, marker) * 100}% - 1px)` }} title={`threshold ${pct(marker)}`} />}
    </div>
  )
}

export function RiskCell({ p, threshold }) {
  return (
    <div className="risk-cell">
      <span className="mono" style={{ fontWeight: 700 }}>{pct(p)}</span>
      <Meter value={p} marker={threshold} hot={threshold != null && p >= threshold} />
    </div>
  )
}

/** Ring gauge for a probability, with a tick at the threshold. */
export function RiskRing({ p, threshold, size = 190 }) {
  const r = 78, c = 2 * Math.PI * r
  const hot = threshold != null && p >= threshold
  const angle = (threshold || 0) * 360 - 90
  const tx = 100 + r * Math.cos((angle * Math.PI) / 180), ty = 100 + r * Math.sin((angle * Math.PI) / 180)
  return (
    <svg width={size} height={size} viewBox="0 0 200 200" role="img" aria-label={`Churn probability ${pct(p)}`}>
      <circle cx="100" cy="100" r={r} fill="none" stroke="#edf1f7" strokeWidth="18" />
      <circle cx="100" cy="100" r={r} fill="none" stroke={hot ? '#d64545' : '#4f9cf9'} strokeWidth="18" strokeLinecap="round"
        strokeDasharray={`${c * Math.max(0.001, Math.min(1, p))} ${c}`} transform="rotate(-90 100 100)" style={{ transition: 'stroke-dasharray .5s' }} />
      {threshold != null && <circle cx={tx} cy={ty} r="7" fill="#043873" stroke="#fff" strokeWidth="3" />}
      <text x="100" y="100" textAnchor="middle" fontSize="36" fontWeight="800" fill="#043873">{pct(p)}</text>
      <text x="100" y="126" textAnchor="middle" fontSize="12" fill="#5b6676">churn probability</text>
    </svg>
  )
}

export function More({ title, children, open }) {
  return (
    <details className="more" open={open}>
      <summary><ChevronRight size={16} className="chev" />{title}</summary>
      <div style={{ marginTop: 12 }}>{children}</div>
    </details>
  )
}

export function useToast() {
  const [msg, setMsg] = useState(null)
  const t = useRef()
  useEffect(() => () => clearTimeout(t.current), [])
  const show = m => { setMsg(m); clearTimeout(t.current); t.current = setTimeout(() => setMsg(null), 4500) }
  const node = msg ? <div className="toast" role="status"><CheckCircle2 size={18} />{msg}</div> : null
  return [node, show]
}
