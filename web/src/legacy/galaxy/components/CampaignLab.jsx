import { useEffect, useRef, useState } from 'react'
import { Loader2, Rocket, X } from 'lucide-react'
import { changesText, humanize, money, num, signed } from '../lib.js'

const Row = ({ label, a, b, fmt, good, d = 0 }) => (
  <div className="cmp">
    <span>{label}</span>
    <span><s>{fmt(a)}</s> → <b>{fmt(b)}</b></span>
    <em className={(good ? b - a >= 0 : b - a <= 0) ? 'good' : 'bad'}>{signed(b - a, d)}</em>
  </div>
)

export default function CampaignLab({ scenarios, meta, result, busy, error, showAfter, setShowAfter, onLaunch, onClear, onClose }) {
  const [pick, setPick] = useState('combined_package')
  const [custom, setCustom] = useState({ Contract: '', discount_pct: 0, TechSupport: false })
  const [flaggedOnly, setFlaggedOnly] = useState(true)
  const [contracts, setContracts] = useState(new Set())

  const resultRef = useRef(null)
  useEffect(() => { if (result && resultRef.current) resultRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [result])
  const toggleContract = c => setContracts(s => { const n = new Set(s); n.has(c) ? n.delete(c) : n.add(c); return n })
  const customChanges = () => {
    const ch = {}
    if (custom.Contract) ch.Contract = custom.Contract
    if (custom.discount_pct > 0) ch.discount_pct = custom.discount_pct
    if (custom.TechSupport) ch.TechSupport = 'Yes'
    return ch
  }
  const launch = () => {
    const base = { flagged_only: flaggedOnly, contracts: contracts.size ? [...contracts] : null }
    if (pick === 'custom') onLaunch({ ...base, changes: customChanges() })
    else onLaunch({ ...base, scenario: pick })
  }
  const list = scenarios.filter(s => s.name !== 'do_nothing')
  const canLaunch = pick !== 'custom' || Object.keys(customChanges()).length > 0

  return (
    <section className="panel left lab" aria-label="Campaign lab">
      <header className="panel-h">
        <div><div className="eyebrow">Campaign lab</div><h2>Move the whole galaxy</h2></div>
        <button className="icon" onClick={onClose} aria-label="Close"><X size={16} /></button>
      </header>
      <p className="sub">Apply one change to many customers at once and watch the model re-score them. Model sensitivity, not a causal estimate.</p>

      <div className="playbook">
        {list.map(s => (
          <button key={s.name} className={'play ' + (pick === s.name ? 'on' : '')} onClick={() => setPick(s.name)}>
            <b className="cap">{humanize(s.name)}</b><small>{s.description}</small>
          </button>
        ))}
        <button className={'play ' + (pick === 'custom' ? 'on' : '')} onClick={() => setPick('custom')}><b>Build your own</b><small>Pick contract, discount and add-on.</small></button>
      </div>

      {pick === 'custom' && (
        <div className="custom">
          <label className="lbl">Contract</label>
          <select value={custom.Contract} onChange={e => setCustom({ ...custom, Contract: e.target.value })}>
            <option value="">(no change)</option>{meta.categorical_levels.Contract.map(o => <option key={o}>{o}</option>)}
          </select>
          <label className="lbl">Discount <b>{custom.discount_pct}%</b></label>
          <input type="range" min="0" max={meta.max_discount_pct} value={custom.discount_pct} onChange={e => setCustom({ ...custom, discount_pct: Number(e.target.value) })} />
          <button type="button" className={'chip ' + (custom.TechSupport ? 'on' : '')} onClick={() => setCustom({ ...custom, TechSupport: !custom.TechSupport })}>Add tech support</button>
        </div>
      )}

      <label className="lbl">Who gets it</label>
      <div className="chips">
        <button className={'chip ' + (flaggedOnly ? 'on' : '')} onClick={() => setFlaggedOnly(!flaggedOnly)}>Only flagged</button>
        {meta.categorical_levels.Contract.map(c => <button key={c} className={'chip ' + (contracts.has(c) ? 'on' : '')} onClick={() => toggleContract(c)}>{c}</button>)}
      </div>

      <div className="row-btns">
        <button className="btn primary" onClick={launch} disabled={busy || !canLaunch}>{busy ? <Loader2 className="spin" size={15} /> : <Rocket size={15} />} Launch campaign</button>
        {result && <button className="btn ghost" onClick={onClear}>Clear</button>}
      </div>
      {error && <p className="err">{error.message}</p>}

      {result && (
        <div className="result" ref={resultRef}>
          <h3>{changesText(result.changes)}</h3>
          <p className="sub">{num(result.affected)} of {num(result.targeted)} targeted customers could take it{result.skipped ? ` (${num(result.skipped)} could not)` : ''}.</p>
          <Row label="Expected leavers" a={result.before.expected_churners} b={result.after.expected_churners} fmt={v => num(v, 1)} d={1} />
          <Row label="Flagged customers" a={result.before.flagged} b={result.after.flagged} fmt={v => num(v)} />
          <Row label="Monthly revenue kept" a={result.before.monthly_revenue_kept} b={result.after.monthly_revenue_kept} fmt={v => money(v)} good />
          <div className="seg">
            <button className={!showAfter ? 'on' : ''} onClick={() => setShowAfter(false)}>Before</button>
            <button className={showAfter ? 'on' : ''} onClick={() => setShowAfter(true)}>After</button>
          </div>
          {result.skipped_reasons.length > 0 && <ul className="foot">{result.skipped_reasons.map(r => <li key={r.reason}>{num(r.count)} × {r.reason}</li>)}</ul>}
          {(result.warnings || []).map(w => <p key={w} className="note warn">{w}</p>)}
          <p className="foot">{result.note}</p>
        </div>
      )}
    </section>
  )
}
