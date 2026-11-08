import { useState } from 'react'
import { ArrowRight, Bot, ClipboardCheck, Database, FlaskConical, Gauge, Send } from 'lucide-react'
import ModelDrawer from '../components/ModelDrawer.jsx'

const FLOW = [
  [Database, 'Ingest', 'Customer data comes in and every row is checked.'],
  [Gauge, 'Score', 'Each customer gets a likelihood of leaving.'],
  [FlaskConical, 'Digital twin', 'Retention options are tried on a copy of the customer first.'],
  [ClipboardCheck, 'Approve', 'A named person signs off every contact.'],
  [Send, 'Outreach', 'Approved messages go to the CRM.'],
]

export default function Login({ meta, onSignIn }) {
  const [name, setName] = useState('')
  const [key, setKey] = useState('')
  const [model, setModel] = useState(false)
  const ok = name.trim().length > 0 && name.trim().length <= 80 && (!meta.queue_key_required || key.trim())
  const submit = e => { e.preventDefault(); if (ok) onSignIn(name.trim(), key.trim()) }

  return (
    <div className="login">
      <section className="login-hero">
        <div className="brand big"><span className="logo" aria-hidden="true" /><b>Evergreen</b><span>Retention</span></div>
        <h1>Find the customers about to leave, test what would keep them, and act with a person in the loop.</h1>
        <ol className="flow">
          {FLOW.map(([Icon, t, d], i) => (
            <li key={t}>
              <span className="flow-ic"><Icon size={18} /></span>
              <div><b>{t}</b><p>{d}</p></div>
              {i < FLOW.length - 1 && <ArrowRight className="flow-arrow" size={14} aria-hidden="true" />}
            </li>
          ))}
        </ol>
        <p className="login-foot"><Bot size={14} /> The agent never contacts a customer by itself.</p>
      </section>

      <section className="login-card-wrap">
        <form className="card login-card" onSubmit={submit}>
          <h2>Sign in</h2>
          <p className="sub">Your name is recorded on every approval you make.</p>
          <label className="lbl" htmlFor="who">Your name</label>
          <input id="who" className="input" autoFocus value={name} maxLength={80} onChange={e => setName(e.target.value)} placeholder="e.g. Dana Rivera" />
          {meta.queue_key_required && (
            <>
              <label className="lbl" htmlFor="key">Access key</label>
              <input id="key" className="input" type="password" value={key} onChange={e => setKey(e.target.value)} autoComplete="off" />
            </>
          )}
          <button className="btn primary wide" disabled={!ok}>Continue <ArrowRight size={16} /></button>
          <p className="tiny muted">Demo sign-in for this project: no password is stored.</p>
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
