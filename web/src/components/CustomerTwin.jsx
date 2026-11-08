import { Fragment, useEffect, useState } from 'react'
import { AlertTriangle, Check, FlaskConical, Loader2, RotateCcw, ShieldAlert, Sparkles, X } from 'lucide-react'
import { api } from '../api.js'
import { useApiCall } from '../hooks.js'
import { approvalReasons, caseStatusText, humanize, money, optionLabel, pctRisk, pts, setEdit } from '../lib.js'

const ADDONS = ['TechSupport', 'OnlineSecurity', 'OnlineBackup', 'DeviceProtection']
const ALLOWED = { permitted: ['Allowed', 'ok'], needs_human_approval: ['Needs approval', 'warn'], blocked: ['Blocked', 'bad'] }
const CAUTION = 'A second, simpler check model sees a much smaller effect, so treat this number with caution.'

/** Likelihood of leaving as a bar with the alert line marked. */
export function RiskMeter({ value, after, threshold, placeholder }) {
  const w = x => Math.max(0, Math.min(100, x * 100)) + '%'
  const tone = v => (v >= threshold ? 'hi' : 'lo')
  return (
    <div className="meter">
      <div className="meter-nums">
        <div><span>Likelihood of leaving</span><b className={tone(value)}>{pctRisk(value)}</b></div>
        {after != null && <div><span>After the change</span><b className={tone(after)}>{pctRisk(after)}</b></div>}
        {after != null && <div><span>Change</span><b className={after < value ? 'good' : after > value ? 'bad' : ''}>{pts(value, after)}</b></div>}
      </div>
      <div className="meter-bar" role="img" aria-label={`Likelihood of leaving ${pctRisk(value)}`}>
        <i className={'now ' + tone(value) + (after != null ? ' faded' : '')} style={{ width: w(value) }} />
        {after != null && <i className={'after ' + tone(after)} style={{ width: w(after) }} />}
        <span className="alert-line" style={{ left: w(threshold) }} title={'Alert line ' + pctRisk(threshold)} />
      </div>
      <p className="tiny muted">Marker = alert line ({pctRisk(threshold)}){placeholder ? ', a placeholder until offer cost and customer value are set' : ''}.</p>
    </div>
  )
}

function Pills({ options, value, onChange, disabled }) {
  return <div className="seg">{options.map(o => <button key={o} type="button" disabled={disabled} className={o === value ? 'on' : ''} onClick={() => onChange(o)}>{o}</button>)}</div>
}

function WhatIf({ customer, meta, threshold, placeholder }) {
  const [edits, setEdits] = useState({})
  const c = customer
  const val = k => (k in edits ? edits[k] : k === 'discount_pct' ? 0 : c[k])
  const set = (k, v) => setEdits(e => setEdit(e, c, k, v))
  const dirty = Object.keys(edits).length > 0
  const sim = useApiCall(() => api('/simulate', { customer: c, changes: edits }), JSON.stringify(edits), { delay: 220, enabled: dirty })
  const t = dirty ? sim.data : null
  const lv = meta.categorical_levels
  const noNet = c.InternetService === 'No'
  return (
    <section className="tw-sec">
      <h3><Sparkles size={15} /> Try your own what-if</h3>
      <p className="tiny muted">Change the plan below and the twin re-scores instantly. Tenure and demographics cannot be changed.</p>
      <label className="lbl">Contract</label>
      <Pills options={lv.Contract} value={val('Contract')} onChange={v => set('Contract', v)} />
      <label className="lbl">Discount on the monthly bill <b>{val('discount_pct')}%</b>{t ? <em> → {money(t.resulting_customer.MonthlyCharges, 2)}/month</em> : null}</label>
      <input type="range" min="0" max={meta.max_discount_pct} step="1" value={val('discount_pct')} onChange={e => set('discount_pct', Number(e.target.value))} aria-label="Discount percent" />
      <label className="lbl">Add-ons {noNet && <em>(needs internet)</em>}</label>
      <div className="chips">
        {ADDONS.map(a => <button key={a} type="button" disabled={noNet} className={'chip ' + (val(a) === 'Yes' ? 'on' : '')} onClick={() => set(a, val(a) === 'Yes' ? 'No' : 'Yes')}>{humanize(a)}</button>)}
        <button type="button" className={'chip ' + (val('PaperlessBilling') === 'Yes' ? 'on' : '')} onClick={() => set('PaperlessBilling', val('PaperlessBilling') === 'Yes' ? 'No' : 'Yes')}>Paperless billing</button>
      </div>
      <label className="lbl">Payment method</label>
      <select className="input" value={val('PaymentMethod')} onChange={e => set('PaymentMethod', e.target.value)}>{lv.PaymentMethod.map(o => <option key={o}>{o}</option>)}</select>
      <div className="whatif-out">
        {!dirty && <p className="muted small">Make a change to see the twin's answer.</p>}
        {dirty && sim.loading && !t && <p className="muted small"><Loader2 className="spin" size={14} /> Re-scoring the twin…</p>}
        {t && <RiskMeter value={t.baseline_risk} after={t.risk_after} threshold={threshold} placeholder={placeholder} />}
        {t && t.warnings.map(w => <p key={w} className="note warn"><AlertTriangle size={14} /> {w}</p>)}
        {t && t.confidence === 'low' && <p className="note warn"><AlertTriangle size={14} /> {CAUTION}</p>}
        {sim.error && <p className="err"><ShieldAlert size={14} /> {sim.error.message}</p>}
        {dirty && <button className="btn ghost small" onClick={() => setEdits({})}><RotateCcw size={14} /> Reset</button>}
      </div>
    </section>
  )
}

function CaseBox({ d, who, onChanged }) {
  const cs = d.case
  const [first, setFirst] = useState(cs?.first_name || '')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  useEffect(() => { setFirst(cs?.first_name || '') }, [cs?.first_name])
  if (!cs) return null
  const [label, tone] = caseStatusText(cs.status)
  const go = async (path, body) => {
    setBusy(true); setErr(null)
    try { await api(path, body); await onChanged() } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  return (
    <section className="tw-sec">
      <h3>Case #{cs.id} <span className={'pill tone-' + tone}>{label}</span></h3>
      {cs.status === 'pending_approval' && (
        <>
          <p className="small">The agent proposes <b>{optionLabel(cs.action_name)}</b>. It needs a person because:</p>
          <ul className="plain small">{approvalReasons(cs.approvals_needed).map(r => <li key={r}>{r}</li>)}</ul>
          {!cs.first_name && <><label className="lbl">Customer's first name (for the message)</label><input className="input" value={first} onChange={e => setFirst(e.target.value)} maxLength={40} /></>}
          <div className="row-btns">
            <button className="btn primary" disabled={busy} onClick={() => go(`/cases/${cs.id}/approve`, { approved_by: who, ...(first.trim() && !cs.first_name ? { first_name: first.trim() } : {}) })}>
              {busy ? <Loader2 className="spin" size={15} /> : <Check size={15} />} Approve as {who}
            </button>
          </div>
          <div className="reject">
            <input className="input" placeholder="Reason to reject" value={reason} onChange={e => setReason(e.target.value)} maxLength={500} />
            <button className="btn ghost" disabled={busy || !reason.trim()} onClick={() => go(`/cases/${cs.id}/reject`, { rejected_by: who, reason: reason.trim() })}>Reject</button>
          </div>
        </>
      )}
      {cs.status === 'needs_first_name' && (
        <div className="reject">
          <input className="input" placeholder="Customer's first name" value={first} onChange={e => setFirst(e.target.value)} maxLength={40} />
          <button className="btn primary" disabled={busy || !first.trim()} onClick={() => go(`/cases/${cs.id}/first-name`, { first_name: first.trim() })}>Draft message</button>
        </div>
      )}
      {cs.message && <blockquote className="msg">{cs.message}<footer>{cs.message_source === 'template' ? 'From the approved template' : cs.message_source === 'human_edited' ? 'Edited by a person' : 'Drafted by the assistant and checked'} · approved by {cs.approved_by}</footer></blockquote>}
      {cs.note && <p className="small muted">Note: {cs.note}</p>}
      {err && <p className="err">{err}</p>}
    </section>
  )
}

export default function CustomerTwin({ runId, refId, meta, who, onClose, onChanged }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const load = () => api(`/agent/runs/${runId}/customers/${encodeURIComponent(refId)}`).then(setD).catch(e => setErr(e.message))
  useEffect(() => { setD(null); load() }, [runId, refId]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const k = e => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])
  const changed = async () => { await load(); onChanged() }
  const c = d?.customer
  const tried = (d?.options || []).filter(o => !o.not_applicable)
  const na = (d?.options || []).filter(o => o.not_applicable)

  return (
    <div className="drawer-back" onClick={onClose}>
      <aside className="drawer twin" role="dialog" aria-modal="true" aria-label="Customer digital twin" onClick={e => e.stopPropagation()}>
        <header className="drawer-h">
          <div>
            <div className="eyebrow"><FlaskConical size={13} /> Digital twin</div>
            <h2>{d?.first_name || refId} {d?.first_name && <small>{refId}</small>}</h2>
            {c && <p className="sub">{c.Contract} · {c.tenure} month{c.tenure === 1 ? "" : "s"} with us · {c.InternetService === 'No' ? 'no internet' : c.InternetService + ' internet'} · {money(c.MonthlyCharges, 2)}/month · {c.PaymentMethod}</p>}
          </div>
          <button className="icon" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </header>
        <p className="tag-row"><span className="tag warn" title={d?.twin_label}>What-if on the model · not causal</span></p>
        {err && <p className="err">{err}</p>}
        {!d && !err && <p className="muted"><Loader2 className="spin" size={16} /> Loading the twin…</p>}
        {d && d.error && <p className="note bad">This row could not be used: {d.error}</p>}
        {d && c && (
          <>
            <RiskMeter value={d.probability} threshold={meta.threshold} placeholder={meta.threshold_is_placeholder} />
            {d.reasons.length > 0 && (
              <section className="tw-sec">
                <h3>Why they might leave</h3>
                <ul className="reasons">{d.reasons.map(r => <li key={r.feature}>{r.text}</li>)}</ul>
              </section>
            )}
            {!d.above_alert_line && <p className="note">Below the alert line: the agent only monitors this customer.</p>}
            {d.above_alert_line && !d.prioritised && <p className="note">Above the alert line but outside this run's top list: the agent will reach them in a later run.</p>}

            {tried.length > 0 && (
              <section className="tw-sec">
                <h3>What the agent tested on the twin</h3>
                <table className="tbl options">
                  <thead><tr><th>Option</th><th className="num">Likelihood after</th><th className="num">Change</th><th>Contact rules</th></tr></thead>
                  <tbody>
                    {tried.map(o => {
                      const [al, at] = ALLOWED[o.allowed] || [o.allowed, 'muted']
                      return (
                        <tr key={o.name} className={o.chosen ? 'hl' : ''}>
                          <td>{o.label}{o.chosen && <span className="pill tone-ok">agent's pick</span>}{o.confidence === 'low' && <span className="caution" title={CAUTION}> ⚠</span>}</td>
                          <td className="num">{pctRisk(o.risk_after)}</td>
                          <td className={'num ' + (o.risk_drop > 0 ? 'good' : 'bad')}>{pts(d.probability, o.risk_after)}</td>
                          <td><span className={'pill tone-' + at}>{al}</span></td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
                {tried.some(o => o.confidence === 'low') && <p className="tiny muted">⚠ {CAUTION}</p>}
                {tried.flatMap(o => o.warnings).filter((w, i, a) => a.indexOf(w) === i).map(w => <p key={w} className="note warn"><AlertTriangle size={14} /> {w}</p>)}
                {na.length > 0 && <p className="tiny muted">Not tried: {na.map(o => `${o.label} (${o.not_applicable.replace(/^not applicable because /, '')})`).join('; ')}.</p>}
              </section>
            )}

            <CaseBox d={d} who={who} onChanged={changed} />
            <WhatIf customer={c} meta={meta} threshold={meta.threshold} placeholder={meta.threshold_is_placeholder} />
            <section className="tw-sec">
              <h3>Customer profile</h3>
              <dl className="kv small">
                {['tenure', 'Contract', 'MonthlyCharges', 'InternetService', 'PhoneService', 'MultipleLines', ...ADDONS, 'StreamingTV', 'StreamingMovies', 'PaperlessBilling', 'PaymentMethod', 'SeniorCitizen', 'Partner', 'Dependents']
                  .map(k => <Fragment key={k}><dt>{k === 'tenure' ? 'Months with us' : k === 'MonthlyCharges' ? 'Monthly bill' : k === 'SeniorCitizen' ? 'Senior' : humanize(k)}</dt>
                    <dd>{k === 'MonthlyCharges' ? money(c[k], 2) : k === 'SeniorCitizen' ? (c[k] ? 'Yes' : 'No') : String(c[k])}</dd></Fragment>)}
              </dl>
            </section>
            <p className="tiny muted">Options come from the retention playbook. Their costs are not subtracted because none have been set yet.</p>
          </>
        )}
      </aside>
    </div>
  )
}
