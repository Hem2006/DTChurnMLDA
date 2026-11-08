import { useState } from 'react'
import { BadgeCheck, CircleSlash, Hand, Send, ShieldAlert } from 'lucide-react'
import { api } from '../../api.js'
import { useApiCall, useApp } from '../state.jsx'
import { Badge, Body, Card, Field, Loading, More, Notice, PageHero, RiskCell, useToast } from '../components/ui.jsx'
import { AccountStateForm, CustomerForm } from '../components/forms.jsx'
import { REF_RE, changesText, humanize, num, pct, signed } from '../lib.js'

export const DECISION = {
  recommend: { icon: BadgeCheck, title: 'Recommend', text: 'Guardrails cleared with verified account state.' },
  recommend_pending_approval: { icon: Hand, title: 'Recommend, pending approval', text: 'A named person must approve before anything is used.' },
  no_action: { icon: CircleSlash, title: 'No action', text: 'Nothing to offer this customer right now.' },
  escalate_to_human: { icon: ShieldAlert, title: 'Escalate to a human', text: 'Do not contact through this system. A specialist reviews it.' },
}

export function DecisionBanner({ decision }) {
  const d = DECISION[decision] || DECISION.no_action
  return (
    <div className={`decision ${decision}`}>
      <div className="icon"><d.icon size={24} /></div>
      <div><h3>Decision: {d.title}</h3><p>{d.text}</p></div>
    </div>
  )
}

function MessagePanel({ rec, customer, accountState }) {
  const [first, setFirst] = useState('')
  const [approver, setApprover] = useState('')
  const [ref, setRef] = useState('')
  const [busy, setBusy] = useState(null)
  const [draft, setDraft] = useState(null)
  const [err, setErr] = useState(null)
  const [toast, show] = useToast()
  const pending = rec.decision === 'recommend_pending_approval'
  const state = accountState

  const draftIt = async () => {
    setBusy('draft'); setErr(null)
    try {
      setDraft(await api('/message', { customer, first_name: first.trim(), account_state: state, offer: rec.action.name, approved_by: approver.trim() || null, customer_ref: ref.trim() || null }))
    } catch (e) { setErr(e.message) } finally { setBusy(null) }
  }
  const queueIt = async () => {
    setBusy('queue'); setErr(null)
    try {
      const r = await api('/cases/ingest', { records: [{ customer_ref: ref.trim(), first_name: first.trim() || null, customer, account_state: state }] })
      if (r.skipped_open_case.length) setErr('This customer already has an open case in the review queue.')
      else show(`Queued: ${Object.entries(r.counts).map(([k, v]) => `${v} ${humanize(k).toLowerCase()}`).join(', ')}.`)
    } catch (e) { setErr(e.message) } finally { setBusy(null) }
  }
  const refOk = REF_RE.test(ref.trim())

  return (
    <Card title="Message" sub={`Offer (fixed by the recommendation; the writer cannot change it): ${changesText(rec.action.changes)}`}>
      <div className="stack">
        <div className="form-grid">
          <Field label="Customer first name" hint="not in the model data"><input value={first} maxLength={40} onChange={e => setFirst(e.target.value)} /></Field>
          <Field label="Approved by" hint={pending ? 'required' : 'optional'}><input value={approver} maxLength={80} onChange={e => setApprover(e.target.value)} /></Field>
          <Field label="Customer reference" hint="for the log / queue"><input value={ref} maxLength={64} placeholder="e.g. 7590-VHVEG" onChange={e => setRef(e.target.value)} /></Field>
        </div>
        {pending && !approver.trim() && <Notice kind="warn">This offer needs a named approver before a message is drafted. Or send it to the review queue for approval.</Notice>}
        <div className="row">
          <button className="btn btn-primary" disabled={!first.trim() || (pending && !approver.trim()) || busy} onClick={draftIt}>
            {busy === 'draft' ? <span className="spinner" /> : <Send size={16} />} Draft message
          </button>
          <button className="btn btn-ghost" disabled={!refOk || busy} onClick={queueIt} title={refOk ? '' : 'Enter a customer reference (letters, digits, - or _)'}>
            {busy === 'queue' && <span className="spinner" />} Send to review queue
          </button>
        </div>
        {err && <Notice kind="danger">{err}</Notice>}
        {draft && (
          <div className="stack-sm">
            {draft.rejected_reason && <Notice kind="warn" title="The model draft was rejected:">{draft.rejected_reason}. The checked template was used instead.</Notice>}
            <div className="message-box">{draft.message}</div>
            <div className="row tiny muted">
              <Badge tone="green">Passed offer, number and pressure-language checks</Badge>
              <span>Source: {draft.source}{draft.model ? ` (${draft.model})` : ''} · logged as #{draft.log_id} · nothing is sent from here</span>
            </div>
          </div>
        )}
      </div>
      {toast}
    </Card>
  )
}

export default function Recommend() {
  const { meta, customer, setCustomer, accountState, setAccountState } = useApp()
  const r = useApiCall(() => api('/recommend', { customer, account_state: accountState }), JSON.stringify([customer, accountState]))
  const rec = r.data

  return (
    <>
      <PageHero eyebrow="Recommend" title="Recommendation & message"
        lead="Risk, the twin's ranking, the guardrails and the cost threshold combine into one decision. The language model only phrases the message; it never picks the offer." />
      <Body>
        <div className="split split-wide">
          <div className="stack">
            {r.error ? <Notice kind="danger">{r.error.message}</Notice> : !rec ? <Card><Loading text="Deciding..." /></Card> : (
              <>
                <div style={{ opacity: r.loading ? 0.6 : 1 }}><DecisionBanner decision={rec.decision} /></div>
                <Card>
                  <div className="grid g2">
                    <div className="stack-sm">
                      <span className="small muted">Churn probability</span>
                      <RiskCell p={rec.risk.churn_probability} threshold={rec.risk.threshold} />
                      <span className="tiny muted">Threshold {num(rec.risk.threshold)}{rec.risk.threshold_is_placeholder ? ' (PLACEHOLDER)' : ''}</span>
                    </div>
                    {rec.action && (
                      <div className="stack-sm">
                        <span className="small muted">Proposed action</span>
                        <strong>{humanize(rec.action.name)}</strong>
                        <div className="chips"><span className="chip">{changesText(rec.action.changes)}</span></div>
                        <div className="row">
                          <Badge tone={rec.action.guardrails.status === 'permitted' ? 'green' : 'amber'}>Guardrails: {humanize(rec.action.guardrails.status)}</Badge>
                          <Badge tone={rec.action.confidence === 'low' ? 'amber' : 'green'}>{rec.action.confidence} confidence</Badge>
                        </div>
                      </div>
                    )}
                  </div>
                  {rec.action?.guardrails.approvals_needed?.length > 0 && (
                    <div style={{ marginTop: 16 }}>
                      <Notice kind="warn" title="Needs human approval because:"><ul>{rec.action.guardrails.approvals_needed.map(x => <li key={x}>{x}</li>)}</ul></Notice>
                    </div>
                  )}
                  <hr className="divider" />
                  <h4 style={{ marginBottom: 8 }}>Reasons</h4>
                  <ul className="list small">{rec.reasons.map(x => <li key={x}>{x}</li>)}</ul>
                </Card>
                {rec.simulated_uplift ? (
                  <Notice kind="label" title={`SIMULATED uplift ${signed(rec.simulated_uplift.simulated_uplift)}`}>
                    (churn {num(rec.simulated_uplift.simulated_p_churn_control)} untreated vs {num(rec.simulated_uplift.simulated_p_churn_treated)} treated). {rec.simulated_uplift.note}
                  </Notice>
                ) : (
                  <p className="tiny muted">{meta.uplift_built ? 'SIMULATED uplift is only calculated for flagged customers (and not for escalated accounts).' : 'SIMULATED uplift model not built (run: python -m app.uplift).'}</p>
                )}
                {rec.candidates.length > 0 && (
                  <Card title="Candidates" sub="Model sensitivity, not causal. The first worthwhile, permitted or approvable one is chosen.">
                    <div className="table-wrap">
                      <table className="tbl">
                        <thead><tr><th>Scenario</th><th className="num">Risk drop</th><th>Expected net value</th><th>Guardrails</th><th>Confidence</th></tr></thead>
                        <tbody>
                          {rec.candidates.map(c => (
                            <tr key={c.name} className={rec.action?.name === c.name ? 'selected' : ''}>
                              <td><strong>{humanize(c.name)}</strong><div className="tiny muted">{changesText(c.changes)}</div></td>
                              <td className="num">{signed(c.risk_drop)}</td>
                              <td className="tiny">{c.expected_net_value == null ? <span className="muted">not computed</span> : num(c.expected_net_value, 2)}</td>
                              <td><Badge tone={c.guardrails.status === 'permitted' ? 'green' : c.guardrails.status === 'blocked' ? 'red' : 'amber'}>{humanize(c.guardrails.status)}</Badge></td>
                              <td>{c.confidence}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </Card>
                )}
                {rec.action && <MessagePanel rec={rec} customer={customer} accountState={accountState} />}
              </>
            )}
          </div>
          <div className="stack sticky">
            <Card title="Account state" sub="Changes the decision, not the score.">
              <AccountStateForm value={accountState} onChange={setAccountState} />
            </Card>
            <Card title="Customer" sub={`Risk ${rec ? pct(rec.risk.churn_probability) : '...'} · shared with the other pages`}>
              <More title="Edit the customer profile"><CustomerForm meta={meta} customer={customer} onChange={setCustomer} /></More>
            </Card>
          </div>
        </div>
      </Body>
    </>
  )
}
