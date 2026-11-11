import { useEffect, useState } from 'react'
import { ArrowRight, Bot, BarChart3, ClipboardCheck, Loader2, ScrollText, Send, ShieldCheck, UserRound } from 'lucide-react'
import { api, setApiKey, setToken } from '../api.js'
import { ROLE_TEXT } from '../lib.js'
import ModelDrawer from '../components/ModelDrawer.jsx'

const ROLE_ICON = { analyst: BarChart3, manager: ClipboardCheck, compliance: ShieldCheck, admin: ScrollText }
const FLOW = [
  ['analyst', 'Analyst', 'Runs the agent, studies the twin, sends the case on.'],
  ['manager', 'Manager', 'Approves, sends back or rejects.'],
  ['compliance', 'Compliance', 'Checks the message and releases it to the CRM.'],
]

export default function Login({ meta, onSignIn }) {
  const [accounts, setAccounts] = useState(null)
  const [pick, setPick] = useState(null)
  const [pin, setPin] = useState('')
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [model, setModel] = useState(false)

  useEffect(() => { api('/auth/accounts').then(d => setAccounts(d.accounts)).catch(e => setErr(e.message)) }, [])

  const ok = pick && pin.trim() && (!meta.queue_key_required || key.trim())
  const submit = async e => {
    e.preventDefault()
    if (!ok || busy) return
    setBusy(true); setErr(null)
    try {
      setApiKey(key.trim())
      const r = await api('/auth/login', { user_id: pick.id, pin: pin.trim() })
      setToken(r.token)
      onSignIn(r.user)
    } catch (x) { setErr(x.message); setApiKey(''); setBusy(false) }
  }

  return (
    <div className="login">
      <section className="login-hero">
        <div className="brand big"><span className="logo" aria-hidden="true" /><b>Evergreen</b><span>Retention</span></div>
        <h1>Find the customers about to leave, test what would keep them, and pass each decision through the right people.</h1>
        <ol className="flow">
          {FLOW.map(([role, t, d], i) => {
            const Icon = ROLE_ICON[role]
            return (
              <li key={t}>
                <span className="flow-ic"><Icon size={18} /></span>
                <div><b>{t}</b><p>{d}</p></div>
                {i < FLOW.length - 1 && <ArrowRight className="flow-arrow" size={14} aria-hidden="true" />}
              </li>
            )
          })}
        </ol>
        <p className="login-foot"><Bot size={14} /> Nobody can approve their own work, and the agent never contacts a customer by itself.</p>
      </section>

      <section className="login-card-wrap">
        <form className="card login-card" onSubmit={submit}>
          <h2>Sign in</h2>
          <p className="sub">Choose who you are. What you can see and do depends on your role.</p>
          {!accounts && !err && <p className="muted small"><Loader2 className="spin" size={14} /> Loading accounts…</p>}
          <div className="acct-list" role="radiogroup" aria-label="Account">
            {(accounts || []).map(a => {
              const Icon = ROLE_ICON[a.role] || UserRound
              return (
                <button type="button" key={a.id} role="radio" aria-checked={pick?.id === a.id} className={'acct' + (pick?.id === a.id ? ' on' : '')} onClick={() => { setPick(a); setErr(null) }}>
                  <span className="flow-ic"><Icon size={17} /></span>
                  <span><b>{a.name}</b><small>{ROLE_TEXT[a.role]?.[0] || a.title}</small><em>{a.blurb}</em></span>
                </button>
              )
            })}
          </div>
          <label className="lbl" htmlFor="pin">PIN</label>
          <input id="pin" className="input" type="password" value={pin} onChange={e => setPin(e.target.value)} autoComplete="off" placeholder="Demo PIN" />
          {meta.queue_key_required && (
            <>
              <label className="lbl" htmlFor="key">Access key</label>
              <input id="key" className="input" type="password" value={key} onChange={e => setKey(e.target.value)} autoComplete="off" />
            </>
          )}
          {err && <p className="err">{err}</p>}
          <button className="btn primary wide" disabled={!ok || busy}>{busy ? <Loader2 className="spin" size={16} /> : <Send size={16} />} Sign in{pick ? ' as ' + pick.name.split(' ')[0] : ''}</button>
          <p className="tiny muted">Demo accounts for this project. The shared demo PIN is in <code>users.yaml</code>; every step you take is recorded under your name.</p>
        </form>
      </section>

      {/* Deliberately quiet: technical model details are for the data-science review, not for day-to-day users. */}
      <button className="model-peek" onClick={() => setModel(true)} aria-label="Model and training details" title="Model and training details">
        model&nbsp;info
      </button>
      {model && <ModelDrawer meta={meta} onClose={() => setModel(false)} />}
    </div>
  )
}
