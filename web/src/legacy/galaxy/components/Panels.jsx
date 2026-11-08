import { Pause, Play, Rocket, X } from 'lucide-react'
import { CohortChart } from './charts.jsx'
import { HORIZON, money, num, pct, signed } from '../lib.js'

export const MODES = [['risk', 'Risk'], ['contract', 'Contract'], ['tenure', 'Tenure'], ['truth', 'Reality']]
const MODE_HELP = {
  risk: 'Colour = churn risk from the calibrated model. Pink is about to leave, mint is safe.',
  contract: 'Colour = contract type.',
  tenure: 'Colour = tenure. Warm = new customer, cool = long-standing.',
  truth: 'Reality check on held-out data: pink really churned, mint really stayed. Compare with the Risk view.',
}

function Toggle({ on, onClick, children }) {
  return <button type="button" className={'chip ' + (on ? 'on' : '')} onClick={onClick}>{children}</button>
}

export function StatsPanel({ galaxy, meta, filters, setFilters, mode, setMode, onLab, labOpen, onClose }) {
  const s = galaxy.summary
  const toggle = (key, v) => setFilters(f => { const n = new Set(f[key]); n.has(v) ? n.delete(v) : n.add(v); return { ...f, [key]: n } })
  return (
    <section className="panel left" aria-label="Galaxy overview">
      <header className="panel-h">
        <div><div className="eyebrow">The galaxy</div><h2>{num(s.n)} customers</h2></div>
        {onClose && <button className="icon" onClick={onClose} aria-label="Close"><X size={16} /></button>}
      </header>
      <div className="stats">
        <div><b>{pct(s.mean_risk, 1)}</b><span>average chance of leaving</span></div>
        <div><b>{num(s.flagged)}</b><span>above the alert line*</span></div>
        <div><b>{num(s.expected_churners)}</b><span>expected leavers</span></div>
        <div><b>{money(s.monthly_revenue_at_risk)}</b><span>monthly revenue at risk</span></div>
      </div>
      <p className="foot">*Alert line {pct(galaxy.threshold, 1)}{galaxy.threshold_is_placeholder ? ' is a PLACEHOLDER (no offer cost, customer value or success rate is set).' : '.'}</p>

      <h3>Colour by</h3>
      <div className="seg four">{MODES.map(([k, l]) => <button key={k} className={mode === k ? 'on' : ''} onClick={() => setMode(k)}>{l}</button>)}</div>
      <p className="foot">{MODE_HELP[mode]}</p>

      <h3>Filter</h3>
      <div className="chips">
        {meta.categorical_levels.Contract.map(c => <Toggle key={c} on={filters.contracts.has(c)} onClick={() => toggle('contracts', c)}>{c}</Toggle>)}
      </div>
      <div className="chips">
        {meta.categorical_levels.InternetService.map(c => <Toggle key={c} on={filters.internet.has(c)} onClick={() => toggle('internet', c)}>{c === 'No' ? 'No internet' : c}</Toggle>)}
        <Toggle on={filters.flaggedOnly} onClick={() => setFilters(f => ({ ...f, flaggedOnly: !f.flaggedOnly }))}>Flagged only</Toggle>
      </div>

      <button className={'btn primary wide ' + (labOpen ? 'on' : '')} onClick={onLab}><Rocket size={15} /> Campaign lab</button>
      <p className="foot">{galaxy.layout_note}</p>
      <p className="foot">{galaxy.data_note}</p>
    </section>
  )
}

export function Dock({ month, setMonth, playing, setPlaying, base, camp, showAfter, setShowAfter, hasCamp, cohortBase, cohortCamp, n, galaxy }) {
  const world = hasCamp && showAfter ? camp : base
  const saved = hasCamp ? base.lost - camp.lost : null
  return (
    <div className="dock" role="group" aria-label="Timeline">
      <button className="play-btn" onClick={() => setPlaying(!playing)} aria-label={playing ? 'Pause' : 'Play'}>{playing ? <Pause size={18} /> : <Play size={18} />}</button>
      <div className="scrub">
        <div className="scrub-top">
          <span className="eyebrow">Time travel <i className="tag warn">illustrative</i></span>
          <b>{month === 0 ? 'Today' : `Month ${month}`}</b>
        </div>
        <input type="range" min="0" max={HORIZON} step="1" value={month} onChange={e => { setPlaying(false); setMonth(Number(e.target.value)) }} aria-label="Month" />
      </div>
      <div className="counters">
        <div><b>{num(world.lost)}</b><span>customers gone <small>({pct(world.lost / n, 1)})</small></span></div>
        <div><b>{money(world.mrr)}</b><span>monthly revenue gone</span></div>
        {hasCamp && <div className={saved >= 0 ? 'good' : 'bad'}><b>{signed(saved)}</b><span>kept by the campaign</span></div>}
      </div>
      <CohortChart base={cohortBase} after={hasCamp ? cohortCamp : null} month={month} />
      {hasCamp && (
        <div className="seg">
          <button className={!showAfter ? 'on' : ''} onClick={() => setShowAfter(false)}>Do nothing</button>
          <button className={showAfter ? 'on' : ''} onClick={() => setShowAfter(true)}>Campaign</button>
        </div>
      )}
      <p className="dock-note">{galaxy.timeline_note}</p>
    </div>
  )
}

export function About({ meta, galaxy, onClose }) {
  const ev = meta.evaluation || {}
  const m = ev.test_metrics || {}
  const ci = ev.test_metrics_ci95 || {}
  const auc = m.roc_auc
  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label="About the model" onClick={onClose}>
      <div className="modal-card" onClick={e => e.stopPropagation()}>
        <header className="panel-h"><div><div className="eyebrow">Under the hood</div><h2>The model behind the galaxy</h2></div><button className="icon" onClick={onClose} aria-label="Close"><X size={16} /></button></header>
        <p>Every glow is a real customer from the held-out 20% of the Telco churn data, scored by a <b>calibrated, 5-fold XGBoost ensemble</b> ({meta.calibration_method || 'isotonic'} calibration). The model never saw these customers while training.</p>
        <div className="stats">
          <div><b>{auc != null ? auc.toFixed(3) : '–'}</b><span>test ROC AUC{ci.roc_auc ? ` (95% CI ${ci.roc_auc[0].toFixed(3)}–${ci.roc_auc[1].toFixed(3)})` : ''}</span></div>
          <div><b>{num(galaxy.summary.actual_churners)}</b><span>customers who really churned</span></div>
          <div><b>{num(galaxy.summary.expected_churners, 0)}</b><span>leavers the model expects</span></div>
        </div>
        <h3>How to read it</h3>
        <ul className="plain">
          <li><b>Position.</b> {galaxy.layout_note} Customers on the same side of the galaxy look alike to the model.</li>
          <li><b>Digital twin.</b> A copy of a customer whose contract, discount, add-ons and billing you can edit. The model re-scores it live. This is <b>model sensitivity, not a causal estimate</b>: it shows how the score reacts, not what would happen if you really made the change. Contract changes especially are mostly selection, since loyal customers choose long contracts.</li>
          <li><b>Campaign lab.</b> Runs the same edit on many twins at once and sums the model's probabilities. Offer costs are not set, so none are subtracted.</li>
          <li><b>Time travel.</b> {galaxy.timeline_note}</li>
          <li><b>Alert line.</b> {galaxy.threshold_is_placeholder ? 'A PLACEHOLDER (F1-optimal), because offer cost, customer value and success rate are not set in config.yaml.' : 'Computed from the business numbers in config.yaml.'}</li>
        </ul>
      </div>
    </div>
  )
}
