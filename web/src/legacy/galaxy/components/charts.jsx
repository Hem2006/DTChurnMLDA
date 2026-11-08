import { HORIZON, humanize, pctRisk, riskColor, survival } from '../lib.js'

const css = c => `rgb(${c.map(x => Math.round(x * 255)).join(',')})`

/** Half-circle risk dial. `before` is a ghost tick, `value` the live arc. Both are probabilities. */
export function Gauge({ value, before, threshold }) {
  const R = 74, cx = 90, cy = 88
  const pt = (v, r = R) => { const a = Math.PI * (1 - v); return [cx + r * Math.cos(a), cy - r * Math.sin(a)] }
  const arc = `M ${cx - R} ${cy} A ${R} ${R} 0 0 1 ${cx + R} ${cy}`
  const tick = (v, len, w, color, key) => { const [x1, y1] = pt(v, R - len), [x2, y2] = pt(v, R + len); return <line key={key} x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth={w} strokeLinecap="round" /> }
  return (
    <svg viewBox="0 0 180 104" className="gauge" role="img" aria-label={`Churn risk ${pctRisk(value)}`}>
      <defs>
        <linearGradient id="gaugeGrad" x1="0" x2="1"><stop offset="0" stopColor="#19e3c1" /><stop offset="0.35" stopColor="#4f9cf9" /><stop offset="0.6" stopColor="#ffe492" /><stop offset="0.8" stopColor="#ff8a4c" /><stop offset="1" stopColor="#ff3d71" /></linearGradient>
      </defs>
      <path d={arc} pathLength="100" className="gauge-track" />
      <path d={arc} pathLength="100" className="gauge-fill" stroke="url(#gaugeGrad)" strokeDasharray={`${Math.max(0.5, value * 100)} 100`} />
      {threshold != null && tick(threshold, 7, 1.5, 'rgba(255,255,255,.55)', 'thr')}
      {before != null && Math.abs(before - value) > 0.004 && tick(before, 9, 3, 'rgba(255,255,255,.35)', 'before')}
      <text x={cx} y={cy - 16} textAnchor="middle" className="gauge-num" fill={css(riskColor(value))}>{pctRisk(value)}</text>
      <text x={cx} y={cy + 2} textAnchor="middle" className="gauge-cap">chance of leaving</text>
    </svg>
  )
}

/** Survival curves over the illustrative 12-month horizon for one customer: base (dashed) vs edited (solid). */
export function Curve({ base, after, month }) {
  const W = 300, H = 96, L = 6, B = 16, T = 6
  const x = m => L + (m / HORIZON) * (W - L - 6), y = s => T + (1 - s) * (H - T - B)
  const line = p => Array.from({ length: HORIZON + 1 }, (_, m) => `${m ? 'L' : 'M'}${x(m).toFixed(1)},${y(survival(p, m)).toFixed(1)}`).join(' ')
  const area = p => `${line(p)} L${x(HORIZON)},${y(0)} L${x(0)},${y(0)} Z`
  const cur = after ?? base
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="curve" role="img" aria-label="Illustrative 12-month survival curve">
      {[0.25, 0.5, 0.75].map(g => <line key={g} x1={L} x2={W - 6} y1={y(g)} y2={y(g)} className="curve-grid" />)}
      <path d={area(cur)} className="curve-area" />
      {after != null && <path d={line(base)} className="curve-base" />}
      <path d={line(cur)} className="curve-line" />
      <line x1={x(month)} x2={x(month)} y1={T} y2={H - B} className="curve-now" />
      <circle cx={x(month)} cy={y(survival(cur, month))} r="3.5" className="curve-dot" />
      {[0, 3, 6, 9, 12].map(m => <text key={m} x={x(m)} y={H - 3} textAnchor="middle" className="curve-tick">{m}m</text>)}
    </svg>
  )
}

/** Diverging bars of SHAP pushes (log-odds of the base score). Right/pink = toward leaving, left/mint = toward staying. */
export function Drivers({ drivers }) {
  const max = Math.max(0.05, ...drivers.map(d => Math.abs(d.push)))
  return (
    <ul className="drivers">
      {drivers.map(d => (
        <li key={d.feature}>
          <span className="drv-name">{humanize(d.feature)} <b>{typeof d.value === 'number' ? Math.round(d.value * 100) / 100 : d.value}</b></span>
          <span className="drv-track"><i className={d.push >= 0 ? 'up' : 'down'} style={{ width: `${(Math.abs(d.push) / max) * 50}%`, [d.push >= 0 ? 'left' : 'right']: '50%' }} /></span>
        </li>
      ))}
    </ul>
  )
}

/** Cohort curve of expected customers remaining, for one or two worlds. */
export function CohortChart({ base, after, month }) {
  const W = 220, H = 56, hi = Math.max(...base), lo = Math.min(...(after || base)) * 0.985
  const x = m => 2 + (m / HORIZON) * (W - 4), y = v => 3 + (1 - (v - lo) / (hi - lo || 1)) * (H - 8)
  const path = a => a.map((v, m) => `${m ? 'L' : 'M'}${x(m).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="cohort" role="img" aria-label="Expected customers remaining by month (illustrative)">
      <path d={path(base)} className={after ? 'curve-base' : 'curve-line'} />
      {after && <path d={path(after)} className="curve-line" />}
      <line x1={x(month)} x2={x(month)} y1={2} y2={H - 4} className="curve-now" />
    </svg>
  )
}
