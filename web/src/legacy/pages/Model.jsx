import { useApp } from '../state.jsx'
import { Badge, Body, Card, Notice, PageHero, Stat } from '../components/ui.jsx'
import { fairnessGaps, humanize, num, pct } from '../lib.js'

const METRICS = ['roc_auc', 'precision', 'recall', 'f1', 'accuracy', 'brier', 'flag_rate']
const NICE = { roc_auc: 'ROC-AUC', precision: 'Precision', recall: 'Recall', f1: 'F1', accuracy: 'Accuracy', brier: 'Brier score', flag_rate: 'Flag rate' }
const FAIR = ['recall', 'precision', 'flag_rate']

function CiBar({ v, lo, hi }) {
  return (
    <div className="ci" title={`${num(v)} (95% CI ${num(lo)} to ${num(hi)})`}>
      <div className="track" />
      {lo != null && <div className="range" style={{ left: lo * 100 + '%', width: (hi - lo) * 100 + '%' }} />}
      <div className="pt" style={{ left: v * 100 + '%' }} />
    </div>
  )
}

export default function Model() {
  const { meta } = useApp()
  const ev = meta.evaluation
  const tm = ev.test_metrics || {}, ci = ev.test_metrics_ci95 || {}, cal = ev.calibration_test || {}
  const cv = Object.entries(ev.nested_cv_auc || {}).sort((a, b) => b[1].mean - a[1].mean)
  const cvMin = Math.min(...cv.map(([, x]) => x.mean - 2 * x.sd), 0.75), cvMax = Math.max(...cv.map(([, x]) => x.mean + 2 * x.sd), 0.9)
  const scale = v => ((v - cvMin) / (cvMax - cvMin)) * 100
  const gaps = fairnessGaps(ev.subgroups, FAIR, meta.fairness_gap_flag)
  const attrs = [...new Set((ev.subgroups || []).map(g => g.attribute))]

  return (
    <>
      <PageHero eyebrow="Model" title="Model & fairness" lead={`${humanize(ev.selected_model)} with 5-fold ${ev.calibration_method} calibration, evaluated once on the held-out test split. Intervals are ${ev.bootstrap_resamples}-resample bootstrap 95% CIs.`} />
      <Body>
        <div className="grid g4">
          <Card tight><Stat k="Test ROC-AUC" v={num(tm.roc_auc)} s={`baseline Contract + tenure: ${num(ev.test_auc_baseline_contract_tenure)}`} /></Card>
          <Card tight><Stat k="Logistic reference" v={num(meta.logreg_reference_test_auc)} s="test ROC-AUC (cross-check model)" /></Card>
          <Card tight><Stat k="ECE" v={num(cal.ece_calibrated)} s={`uncalibrated: ${num(cal.ece_uncalibrated)}`} /></Card>
          <Card tight><Stat k="Brier" v={num(cal.brier_calibrated)} s={`base-rate only: ${num(cal.brier_baseline_rate_only)}`} /></Card>
        </div>

        <div className="grid g2">
          <Card title="Held-out test metrics" sub={`At the served threshold ${num(meta.threshold)}${meta.threshold_is_placeholder ? ' (PLACEHOLDER)' : ''}.`}>
            <div className="table-wrap">
              <table className="tbl">
                <thead><tr><th>Metric</th><th className="num">Estimate</th><th style={{ minWidth: 160 }}>95% CI (0 to 1)</th></tr></thead>
                <tbody>
                  {METRICS.filter(k => tm[k] != null).map(k => (
                    <tr key={k}>
                      <td><strong>{NICE[k]}</strong><div className="tiny muted">{ci[k] ? `${num(ci[k][0])} to ${num(ci[k][1])}` : ''}</div></td>
                      <td className="num">{num(tm[k])}</td>
                      <td><CiBar v={tm[k]} lo={ci[k]?.[0]} hi={ci[k]?.[1]} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <Card title="Nested cross-validation AUC" sub="Training split only. Bars show mean ± 2 sd across outer folds.">
            <div className="stack-sm">
              {cv.map(([name, x]) => (
                <div key={name} className="stack-sm" style={{ gap: 4 }}>
                  <div className="row spread small"><strong>{humanize(name)}</strong><span className="mono">{num(x.mean, 4)} ± {num(x.sd, 4)}</span></div>
                  <div className="ci">
                    <div className="track" />
                    <div className="range" style={{ left: scale(x.mean - 2 * x.sd) + '%', width: scale(x.mean + 2 * x.sd) - scale(x.mean - 2 * x.sd) + '%' }} />
                    <div className="pt" style={{ left: scale(x.mean) + '%', background: name === ev.selected_model ? 'var(--blue)' : 'var(--navy)' }} />
                  </div>
                </div>
              ))}
              <p className="tiny muted">Axis {num(cvMin, 2)} to {num(cvMax, 2)}. Overlapping intervals mean the models are close; the served model is highlighted in blue.</p>
            </div>
          </Card>
        </div>

        <Card title="Fairness screening" sub={`Gap = max - min across groups, on the held-out test split at the saved threshold. Gaps above ${num(meta.fairness_gap_flag, 2)} are flagged.`}>
          <div className="stack">
            <Notice kind="info">Screening only: no mitigation is applied and no group-specific thresholds are used. Gender is audited only; it is not a model input. Small groups make gaps noisy.</Notice>
            <div className="table-wrap">
              <table className="tbl">
                <thead><tr><th>Attribute</th>{FAIR.map(m => <th key={m} className="num">{humanize(m)} gap</th>)}</tr></thead>
                <tbody>
                  {attrs.map(a => (
                    <tr key={a}>
                      <td><strong>{a}</strong></td>
                      {FAIR.map(m => {
                        const g = gaps.find(x => x.attribute === a && x.metric === m)
                        return <td key={m} className="num">{g ? <Badge tone={g.flagged ? 'red' : 'green'}>{num(g.gap)}</Badge> : '-'}</td>
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {gaps.filter(g => g.flagged).map(g => (
              <Notice key={g.attribute + g.metric} kind="warn">{g.attribute} {humanize(g.metric).toLowerCase()} gap {num(g.gap)} ({g.highest} vs {g.lowest}; smallest group n = {g.min_n})</Notice>
            ))}
            <div className="table-wrap">
              <table className="tbl">
                <thead><tr><th>Attribute</th><th>Group</th><th className="num">n</th><th className="num">Churn rate</th><th className="num">Flag rate</th><th className="num">Recall</th><th className="num">Precision</th></tr></thead>
                <tbody>
                  {(ev.subgroups || []).map(g => (
                    <tr key={g.attribute + g.group}>
                      <td>{g.attribute}</td><td><strong>{String(g.group)}</strong></td><td className="num">{g.n}</td>
                      <td className="num">{pct(g.churn_rate)}</td><td className="num">{pct(g.flag_rate)}</td><td className="num">{pct(g.recall)}</td><td className="num">{pct(g.precision)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </Card>
        <p className="tiny muted">Feature set: {ev.feature_set_chosen}. Dropped columns: {(ev.dropped_columns || []).join(', ')}. All numbers are read from churn_model_meta.json.</p>
      </Body>
    </>
  )
}
