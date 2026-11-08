import { RotateCcw } from 'lucide-react'
import { DEFAULT_CUSTOMER, label, ADDONS } from '../lib.js'
import { UNKNOWN_STATE } from '../state.jsx'
import { Field, Select } from './ui.jsx'

/** The 17 model inputs. Dependent fields (no internet / no phone) are forced coherent by the app state. */
export function CustomerForm({ meta, customer, onChange }) {
  const lv = meta.categorical_levels
  const set = (k, v) => onChange({ ...customer, [k]: v })
  const noNet = customer.InternetService === 'No'
  const noPhone = customer.PhoneService === 'No'
  const sel = (k, opts, disabled) => (
    <Field label={label(k)} key={k}>
      <Select value={customer[k]} onChange={v => set(k, v)} options={opts || lv[k]} disabled={disabled} />
    </Field>
  )
  return (
    <div>
      <div className="form-section">
        <h4>Household</h4>
        <div className="form-grid">
          <Field label="Senior citizen">
            <Select value={String(customer.SeniorCitizen)} onChange={v => set('SeniorCitizen', Number(v))} options={['0', '1']} format={v => (v === '1' ? 'Yes' : 'No')} />
          </Field>
          {sel('Partner')}
          {sel('Dependents')}
          <Field label="Tenure" hint={`${customer.tenure} months`}>
            <input type="range" min="0" max="72" value={customer.tenure} onChange={e => set('tenure', Number(e.target.value))} aria-label="Tenure in months" />
          </Field>
        </div>
      </div>
      <div className="form-section">
        <h4>Account</h4>
        <div className="form-grid">
          {sel('Contract')}
          {sel('PaymentMethod')}
          {sel('PaperlessBilling')}
          <Field label="Monthly charges">
            <input type="number" min="0" max="500" step="0.05" value={customer.MonthlyCharges}
              onChange={e => set('MonthlyCharges', e.target.value === '' ? 0 : Math.max(0, Number(e.target.value)))} />
          </Field>
        </div>
      </div>
      <div className="form-section">
        <h4>Services</h4>
        <div className="form-grid">
          {sel('PhoneService')}
          {sel('MultipleLines', noPhone ? lv.MultipleLines : lv.MultipleLines.filter(x => x !== meta.no_phone), noPhone)}
          {sel('InternetService')}
          {ADDONS.map(a => sel(a, noNet ? lv[a] : lv[a].filter(x => x !== meta.no_internet), noNet))}
        </div>
        {(noNet || noPhone) && <p className="muted tiny" style={{ marginTop: 10 }}>Fields that do not apply (no internet / no phone) are set automatically.</p>}
      </div>
    </div>
  )
}

export function ResetCustomer({ onReset }) {
  return <button type="button" className="btn btn-ghost btn-sm" onClick={() => onReset(DEFAULT_CUSTOMER)}><RotateCcw size={14} /> Reset</button>
}

const TRI = [['unknown', null], ['no', false], ['yes', true]]
const triKey = v => TRI.find(t => t[1] === v)[0]

/** Account state is not in the model data. Unknown is never treated as safe: it forces human approval. */
export function AccountStateForm({ value, onChange }) {
  const set = (k, v) => onChange({ ...value, [k]: v })
  const numIn = (k, max, int) => (
    <input type="number" min="0" max={max} step={int ? 1 : 0.5} placeholder="unknown" value={value[k] ?? ''}
      onChange={e => {
        const s = e.target.value
        if (s === '') return set(k, null)
        const x = Math.min(max, Math.max(0, Number(s)))
        set(k, int ? Math.floor(x) : x)
      }} />
  )
  return (
    <div className="stack-sm">
      <div className="form-grid">
        <Field label="Vulnerable">
          <Select value={triKey(value.vulnerable)} onChange={v => set('vulnerable', TRI.find(t => t[0] === v)[1])} options={TRI.map(t => t[0])} />
        </Field>
        <Field label="Open escalation">
          <Select value={triKey(value.escalated)} onChange={v => set('escalated', TRI.find(t => t[0] === v)[1])} options={TRI.map(t => t[0])} />
        </Field>
        <Field label="Contacts this month" hint="blank = unknown">{numIn('contacts_this_month', 50, true)}</Field>
        <Field label="Discount % used this quarter" hint="blank = unknown">{numIn('discount_pct_used_this_quarter', 100, false)}</Field>
      </div>
      <div className="row spread">
        <p className="muted tiny">Not in the model data. <strong>Unknown is never assumed safe</strong>: it forces human approval.</p>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange(UNKNOWN_STATE)}>Set all to unknown</button>
      </div>
    </div>
  )
}
