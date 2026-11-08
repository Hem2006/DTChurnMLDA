import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { api } from '../api.js'
import { humanize, num, pct } from '../lib.js'

// Data-science view, opened only from the quiet "model info" button on the sign-in screen.
// Every number is read from churn_model_meta.json through /meta; nothing here is computed in the browser.
const METRICS = [['roc_auc', 'ROC AUC'], ['precision', 'Precision'], ['recall', 'Recall'], ['f1', 'F1'], ['accuracy', 'Accuracy'], ['brier', 'Brier score'], ['flag_rate', 'Flag rate']]
const FAIR = ['recall', 'precision', 'flag_rate']

function Ci({ v, lo, hi, min = 0, max = 1 }) {
  const s = x => ((x - min) / (max - min)) * 100
  return (
    <div className="ci" title={`${num(v, 3)} (95% CI ${num(lo, 3)} to ${num(hi, 3)})`}>
      <div className="ci-track" />
      {lo != null && <div className="ci-range" style={{ left: s(lo) + '%', width: s(hi) - s(lo) + '%' }} />}
      <div className="ci-pt" style={{ left: s(v) + '%' }} />
    </div>
  )
}

export default function ModelDrawer({ meta, onClose }) {
  const [card, setCard] = useState(null)
  const [health, setHealth] = useState(null)
  const [tab, setTab] = useState('summary')
  useEffect(() => {
    api('/model/card').then(setCard).catch(() => setCard({}))
    api('/health').then(setHealth).catch(() => {})
    const k = e => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])

  const ev = meta.evaluation || {}
  const tm = ev.test_metrics || {}, ci = ev.test_metrics_ci95 || {}, cal = ev.calibration_test || {}
  const cv = Object.entries(ev.nested_cv_auc || {}).sort((a, b) => b[1].mean - a[1].mean)
  const rcv = Object.entries(ev.repeated_cv_auc || {})
  const abl = Object.entries(ev.ablation_mean_auc || {}).sort((a, b) => b[1] - a[1])
  const groups = ev.subgroups || []
  const attrs = [...new Set(groups.map(g => g.attribute))]
  const gap = (a, m) => { const v = groups.filter(g => g.attribute === a).map(g => g[m]); return v.length ? Math.max(...v) - Math.min(...v) : null }
  const ov = ev.explainer_top3_overlap || {}

  return (
    <div className="drawer-back" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Model and training details" onClick={e => e.stopPropagation()}>
        <header className="drawer-h">
          <div>
            <div className="eyebrow">For the data-science review</div>
            <h2>Model &amp; training details</h2>
          </div>
          <button className="icon" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </header>
        <div className="seg small">
          {[['summary', 'Summary'], ['validation', 'Validation'], ['fairness', 'Fairness'], ['card', 'Model card']].map(([k, l]) => (
            <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>
          ))}
        </div>

        {tab === 'summary' && (
          <div className="dsec">
            <dl className="kv">
              <dt>Served model</dt><dd>{ev.selected_model === 'xgboost' ? 'XGBoost' : humanize(ev.selected_model)} in CalibratedClassifierCV ({ev.calibration_method}, 5-fold ensemble)</dd>
              <dt>Data</dt><dd>IBM Telco Customer Churn, 80/20 stratified split, seed {ev.seed}; {groups.length ? groups.filter(g => g.attribute === attrs[0]).reduce((s, g) => s + g.n, 0) : '–'} test customers</dd>
              <dt>Inputs</dt><dd>{meta.input_columns.length} columns. Feature set: {ev.feature_set_chosen}. Dropped: {(ev.dropped_columns || []).join(', ')}</dd>
              <dt>Alert threshold</dt><dd>{num(meta.threshold, 3)} ({ev.threshold_source}){meta.threshold_is_placeholder && <><br />{meta.threshold_note}</>}</dd>
              <dt>Explanations</dt><dd>SHAP on the {ev.explainer_model} base pipelines, averaged over the 5 calibration folds (log-odds of the base score). Top-3 agreement with a single explainer: {pct(ov.mean_overlap, 1)} over {ov.n_rows} rows.</dd>
              <dt>Cross-check model</dt><dd>Logistic regression, test ROC AUC {num(meta.logreg_reference_test_auc, 3)}. The digital twin flags low confidence when the two disagree.</dd>
              <dt>Digital twin</dt><dd>{meta.labels.twin}.</dd>
              <dt>Uplift</dt><dd>{meta.labels.uplift}</dd>
              {health && <><dt>Library versions</dt><dd>{health.versions_match ? 'match the saved artifacts' : 'DIFFER from the saved artifacts: ' + Object.keys(health.version_differences).join(', ')}</dd></>}
            </dl>
            <h3>Held-out test metrics <small>{ev.bootstrap_resamples}-resample bootstrap 95% CI</small></h3>
            <table className="tbl">
              <thead><tr><th>Metric</th><th className="num">Estimate</th><th className="num">95% CI</th><th style={{ width: '34%' }} /></tr></thead>
              <tbody>
                {METRICS.filter(([k]) => tm[k] != null).map(([k, l]) => (
                  <tr key={k}><td>{l}</td><td className="num"><b>{num(tm[k], 3)}</b></td><td className="num muted">{ci[k] ? `${num(ci[k][0], 3)}–${num(ci[k][1], 3)}` : ''}</td>
                    <td><Ci v={tm[k]} lo={ci[k]?.[0]} hi={ci[k]?.[1]} /></td></tr>
                ))}
              </tbody>
            </table>
            <p className="tiny muted">Baseline (Contract + tenure only): test ROC AUC {num(ev.test_auc_baseline_contract_tenure, 3)}. Metrics at the placeholder threshold.</p>
            <h3>Calibration (test)</h3>
            <dl className="kv">
              <dt>Brier</dt><dd>{num(cal.brier_calibrated, 4)} calibrated · {num(cal.brier_uncalibrated, 4)} uncalibrated · {num(cal.brier_baseline_rate_only, 4)} base rate only</dd>
              <dt>ECE</dt><dd>{num(cal.ece_calibrated, 4)} calibrated · {num(cal.ece_uncalibrated, 4)} uncalibrated</dd>
              <dt>Mean score</dt><dd>{pct(cal.mean_score_calibrated, 1)} calibrated vs observed churn rate {pct(cal.observed_churn_rate_test, 1)}</dd>
            </dl>
          </div>
        )}

        {tab === 'validation' && (
          <div className="dsec">
            <h3>Nested cross-validation ROC AUC <small>training split only, mean ± sd over outer folds</small></h3>
            <table className="tbl">
              <tbody>{cv.map(([n, x]) => <tr key={n} className={n === ev.selected_model ? 'hl' : ''}><td>{humanize(n)}</td><td className="num"><b>{num(x.mean, 4)}</b> ± {num(x.sd, 4)}</td></tr>)}</tbody>
            </table>
            {rcv.length > 0 && <>
              <h3>Repeated cross-validation ROC AUC</h3>
              <table className="tbl"><tbody>{rcv.map(([n, x]) => <tr key={n}><td>{n}</td><td className="num"><b>{num(x.mean, 4)}</b> ± {num(x.sd, 4)}</td></tr>)}</tbody></table>
            </>}
            {abl.length > 0 && <>
              <h3>Feature-set ablation <small>mean CV ROC AUC</small></h3>
              <table className="tbl"><tbody>{abl.map(([n, v]) => <tr key={n} className={n === ev.feature_set_chosen ? 'hl' : ''}><td>{n}</td><td className="num">{num(v, 4)}</td></tr>)}</tbody></table>
            </>}
            {ev.versions && <>
              <h3>Training environment</h3>
              <p className="mono small">{Object.entries(ev.versions).map(([k, v]) => `${k} ${v}`).join(' · ')}</p>
            </>}
          </div>
        )}

        {tab === 'fairness' && (
          <div className="dsec">
            <p className="small muted">Screening on the test split at the saved threshold. Gaps above {num(meta.fairness_gap_flag, 2)} are flagged. No mitigation is applied; gender is audited but is not a model input.</p>
            <table className="tbl">
              <thead><tr><th>Attribute</th>{FAIR.map(m => <th key={m} className="num">{humanize(m)} gap</th>)}</tr></thead>
              <tbody>{attrs.map(a => <tr key={a}><td>{a}</td>{FAIR.map(m => { const g = gap(a, m); return <td key={m} className={'num ' + (g > meta.fairness_gap_flag ? 'bad' : '')}>{num(g, 3)}</td> })}</tr>)}</tbody>
            </table>
            <h3>By group</h3>
            <table className="tbl">
              <thead><tr><th>Attribute</th><th>Group</th><th className="num">n</th><th className="num">Churn</th><th className="num">Flagged</th><th className="num">Recall</th><th className="num">Precision</th></tr></thead>
              <tbody>{groups.map(g => <tr key={g.attribute + g.group}><td>{g.attribute}</td><td>{String(g.group)}</td><td className="num">{g.n}</td><td className="num">{pct(g.churn_rate)}</td><td className="num">{pct(g.flag_rate)}</td><td className="num">{pct(g.recall)}</td><td className="num">{pct(g.precision)}</td></tr>)}</tbody>
            </table>
          </div>
        )}

        {tab === 'card' && (
          <div className="dsec">
            {!card && <p className="muted">Loading…</p>}
            {card && <pre className="md">{card.model_card || 'MODEL_CARD.md not found.'}</pre>}
            {card?.fairness_report && <><h3>Fairness report</h3><pre className="md">{card.fairness_report}</pre></>}
          </div>
        )}
      </aside>
    </div>
  )
}
