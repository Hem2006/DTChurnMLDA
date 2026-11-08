import { X, RotateCcw, Radar, Eye, Wand2, ShieldAlert, Loader2, Workflow } from 'lucide-react'
import { STATUS, WhoAmI } from './ActionsPanel.jsx'
import { Curve, Drivers, Gauge } from './charts.jsx'
import { changesText, humanize, money, pctRisk, setEdit, signed } from '../lib.js'

const ADDONS = ['TechSupport', 'OnlineSecurity', 'OnlineBackup', 'DeviceProtection']

function Pills({ options, value, onChange, disabled }) {
  return (
    <div className="seg">
      {options.map(o => <button key={o} type="button" disabled={disabled} className={o === value ? 'on' : ''} onClick={() => onChange(o)}>{o}</button>)}
    </div>
  )
}

export default function TwinPanel({ node, meta, edits, setEdits, twin, suggestions, month, lookalikes, setLookalikes, reveal, setReveal, onClose, threshold,
  who, setWho, openAction, onPropose, proposing, proposeError, onOpenActions }) {
  const c = node.c
  const val = k => (k in edits ? edits[k] : c[k])
  const set = (k, v) => setEdits(e => setEdit(e, c, k, v))
  const noNet = val('InternetService') === 'No'
  const t = twin.data
  const after = t ? t.risk_after : node.p
  const dirty = Object.keys(edits).length > 0
  const monthly = t ? t.monthly_after : c.MonthlyCharges
  const lv = meta.categorical_levels

  return (
    <aside className="panel right" aria-label="Digital twin">
      <header className="panel-h">
        <div>
          <div className="eyebrow">Digital twin</div>
          <h2>{node.ref}</h2>
          <p className="sub">{c.Contract} · {c.tenure} months · {c.InternetService === 'No' ? 'no internet' : c.InternetService} · {money(c.MonthlyCharges, 2)}/mo</p>
        </div>
        <button className="icon" onClick={onClose} aria-label="Close"><X size={16} /></button>
      </header>

      <div className="gauge-row">
        <Gauge value={after} before={dirty ? node.p : null} threshold={threshold} />
        <div className="delta">
          <div className="delta-line"><span>Now</span><b>{pctRisk(node.p, 1)}</b></div>
          <div className="delta-line"><span>With edits</span><b className={t && t.risk_drop > 0 ? 'good' : t && t.risk_drop < 0 ? 'bad' : ''}>{t ? pctRisk(t.risk_after, 1) : dirty ? '…' : '—'}</b></div>
          {t && <div className={'delta-big ' + (t.risk_drop > 0 ? 'good' : 'bad')}>{signed(-t.risk_drop * 100, 1)} pts</div>}
          {twin.loading && dirty && <Loader2 className="spin" size={14} />}
        </div>
      </div>
      <p className="tag-row"><span className="tag warn">model sensitivity · not causal</span>{t?.flagged_before && !t.flagged_after && <span className="tag ok">drops below the alert line</span>}</p>

      <section>
        <h3>Edit this customer</h3>
        <label className="lbl">Contract</label>
        <Pills options={lv.Contract} value={val('Contract')} onChange={v => set('Contract', v)} />
        <label className="lbl">Discount on monthly charge <b>{val('discount_pct') || 0}%</b>{dirty && t ? <em> → {money(monthly, 2)}/mo</em> : null}</label>
        <input type="range" min="0" max={meta.max_discount_pct} step="1" value={val('discount_pct') || 0}
          onChange={e => set('discount_pct', Number(e.target.value))} aria-label="Discount percent" />
        <label className="lbl">Add-ons {noNet && <em>(needs internet)</em>}</label>
        <div className="chips">
          {ADDONS.map(a => (
            <button key={a} type="button" disabled={noNet} className={'chip ' + (val(a) === 'Yes' ? 'on' : '')}
              onClick={() => set(a, val(a) === 'Yes' ? 'No' : 'Yes')}>{humanize(a)}</button>
          ))}
          <button type="button" className={'chip ' + (val('PaperlessBilling') === 'Yes' ? 'on' : '')} onClick={() => set('PaperlessBilling', val('PaperlessBilling') === 'Yes' ? 'No' : 'Yes')}>Paperless</button>
        </div>
        <label className="lbl">Payment method</label>
        <select value={val('PaymentMethod')} onChange={e => set('PaymentMethod', e.target.value)}>
          {lv.PaymentMethod.map(o => <option key={o}>{o}</option>)}
        </select>
        <div className="row-btns">
          <button className="btn ghost" onClick={() => setEdits({})} disabled={!dirty}><RotateCcw size={14} /> Reset</button>
          <button className={'btn ghost ' + (lookalikes ? 'on' : '')} onClick={() => setLookalikes(!lookalikes)}><Radar size={14} /> Lookalikes</button>
          <button className={'btn ghost ' + (reveal ? 'on' : '')} onClick={() => setReveal(!reveal)}><Eye size={14} /> Real outcome</button>
        </div>
        {reveal && <p className={'reveal ' + (node.churned ? 'bad' : 'good')}>In the data this customer <b>{node.churned ? 'churned' : 'stayed'}</b>. The model gave {pctRisk(node.p)}.</p>}
        {twin.error && <p className="err"><ShieldAlert size={14} /> {twin.error.message}</p>}
      </section>

      {t && t.warnings.map(w => <p key={w} className="note warn">{w}</p>)}
      {t && t.confidence === 'low' && <p className="note warn">Low confidence: {t.confidence_reason}</p>}

      <section>
        <h3><Workflow size={14} /> Act on it <small>n8n</small></h3>
        {openAction ? (
          <p className="sub">Action #{openAction.id} is open: <span className={'tag st-' + STATUS[openAction.status][1]}>{STATUS[openAction.status][0]}</span>{' '}
            <button className="linkish" onClick={onOpenActions}>Open Actions</button></p>
        ) : (
          <>
            <WhoAmI who={who} setWho={setWho} />
            <button className="btn primary wide" disabled={!dirty || !t || proposing || !who.trim() || t.risk_drop <= 0.0005} onClick={onPropose}>
              {proposing ? <Loader2 className="spin" size={15} /> : <Workflow size={15} />} Propose this action
            </button>
            <p className="foot">{!dirty ? 'Edit the twin first.' : t && t.risk_drop <= 0.0005 ? 'The model sees no drop in risk for this edit, so there is nothing to act on.' : !who.trim() ? 'Enter your name to propose.' : 'A person approves it under Actions, then n8n carries it out. Nothing is sent to the customer from here.'}</p>
          </>
        )}
        {proposeError && <p className="err"><ShieldAlert size={14} /> {proposeError.message}</p>}
      </section>

      <section>
        <h3><Wand2 size={14} /> Suggested moves <small>model sensitivity</small></h3>
        {suggestions.loading && <p className="muted">Trying the playbook…</p>}
        {suggestions.data && (
          <ul className="moves">
            {suggestions.data.results.filter(r => r.name !== 'do_nothing').slice(0, 4).map(r => (
              <li key={r.name}>
                <button onClick={() => setEdits(r.changes)}>
                  <span><span className="cap">{humanize(r.name)}</span><small>{changesText(r.changes)}</small></span>
                  <b className={r.risk_drop > 0 ? 'good' : 'bad'}>{signed(-r.risk_drop * 100, 1)} pts</b>
                </button>
              </li>
            ))}
            {!suggestions.data.results.length && <li className="muted">No playbook move applies to this customer.</li>}
          </ul>
        )}
      </section>

      <section>
        <h3>Why the model says this</h3>
        {t ? <Drivers drivers={t.drivers} /> : <p className="muted">Edit something to see which inputs push the score.</p>}
        {t && <p className="foot">{t.drivers_note}</p>}
      </section>

      <section>
        <h3>Next 12 months <small>illustrative</small></h3>
        <Curve base={node.p} after={t ? t.risk_after : null} month={month} />
        <p className="foot">Reads the score as the chance of leaving within 12 months. Not a forecast.</p>
      </section>
    </aside>
  )
}
