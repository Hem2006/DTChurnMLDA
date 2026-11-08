import { ArrowRight } from 'lucide-react'
import { api } from '../../api.js'
import { useApiCall, useApp } from '../state.jsx'
import { Badge, Body, Card, Loading, More, Notice, PageHero, RiskRing } from '../components/ui.jsx'
import { CustomerForm, ResetCustomer } from '../components/forms.jsx'
import { label, num } from '../lib.js'

export function Drivers({ items, limit = 17 }) {
  const max = Math.max(1e-9, ...items.map(d => Math.abs(d.log_odds_push)))
  return (
    <div className="drivers">
      {items.slice(0, limit).map(d => {
        const w = (Math.abs(d.log_odds_push) / max) * 50
        return (
          <div key={d.feature} className="driver">
            <span className="name" title={`${label(d.feature)}: ${d.value}`}>{label(d.feature)}<small>{String(d.value)}</small></span>
            <span className="axis"><i className={d.log_odds_push >= 0 ? 'up' : 'down'} style={{ width: w + '%' }} /></span>
            <span className="val">{(d.log_odds_push >= 0 ? '+' : '') + d.log_odds_push.toFixed(2)}</span>
          </div>
        )
      })}
    </div>
  )
}

export default function Score() {
  const { meta, customer, setCustomer } = useApp()
  const key = JSON.stringify(customer)
  const pred = useApiCall(() => api('/predict', { customers: [customer] }), key)
  const expl = useApiCall(() => api('/explain', { customer }), key, { delay: 400 })
  const p = pred.data?.results[0]

  return (
    <>
      <PageHero eyebrow="Score" title="Score a customer"
        lead="Describe any customer, real or hypothetical. The score, the reasons and the flag update as you edit. Nothing is saved." />
      <Body>
        <div className="split">
          <Card title="Customer profile" sub="The 17 inputs the model uses. Gender and total charges are not model inputs." action={<ResetCustomer onReset={setCustomer} />}>
            <CustomerForm meta={meta} customer={customer} onChange={setCustomer} />
          </Card>
          <div className="stack sticky">
            <Card title="Churn risk" sub="Calibrated probability: of 100 similar customers, about this many left.">
              {pred.error ? <Notice kind="danger">{pred.error.message}</Notice> : !p ? <Loading text="Scoring..." /> : (
                <div className="stack">
                  <div className="row" style={{ justifyContent: 'center', opacity: pred.loading ? 0.6 : 1 }}>
                    <RiskRing p={p.churn_probability} threshold={p.threshold} />
                  </div>
                  <div className="row" style={{ justifyContent: 'center' }}>
                    {p.flagged ? <Badge tone="red">Flagged: high risk</Badge> : <Badge tone="green">Not flagged</Badge>}
                    <Badge tone={p.threshold_is_placeholder ? 'yellow' : 'grey'}>Threshold {num(p.threshold)}{p.threshold_is_placeholder ? ' (placeholder)' : ''}</Badge>
                  </div>
                  {p.reasons ? (
                    <div>
                      <h4 style={{ marginBottom: 8 }}>Top reasons</h4>
                      <ul className="list small">
                        {p.reasons.map(r => <li key={r.feature}><strong>{label(r.feature)}</strong> is {String(r.value)} <span className="muted">(+{r.log_odds_push.toFixed(2)} log-odds)</span></li>)}
                      </ul>
                    </div>
                  ) : <p className="muted small" style={{ textAlign: 'center' }}>Not flagged, so no retention reasons are produced.</p>}
                  <a className="btn btn-primary" href="#/recommend">See the recommendation <ArrowRight size={16} /></a>
                </div>
              )}
            </Card>
            <Card title="What drives this score" sub="Red pushes toward churn, green away from it.">
              {expl.error ? <Notice kind="danger">{expl.error.message}</Notice> : !expl.data ? <Loading /> : (
                <>
                  <Drivers items={expl.data.contributions} limit={8} />
                  <More title="All 17 inputs"><Drivers items={expl.data.contributions} /></More>
                  <p className="muted tiny" style={{ marginTop: 12 }}>SHAP log-odds of the base XGBoost score, averaged over the 5 calibration folds. Describes what the model leans on, not a cause.</p>
                </>
              )}
            </Card>
          </div>
        </div>
      </Body>
    </>
  )
}
