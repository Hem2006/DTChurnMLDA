import { ArrowRight } from 'lucide-react'
import { PAGE_LABELS, USERS, useApp } from '../state.jsx'
import { Badge, Logo } from '../components/ui.jsx'

export default function Login() {
  const { login } = useApp()
  const enter = (u, page) => { window.location.hash = '#/' + page; login(u.id) }
  return (
    <div className="login">
      <section className="hero">
        <div className="wrap" style={{ display: 'block' }}>
          <div className="eyebrow">Churn risk · what-if · guarded offers</div>
          <h1 className="row" style={{ gap: 12 }}><Logo size={44} /> Evergreen</h1>
          <p className="lead">Sign in to continue.</p>
        </div>
      </section>
      <div className="login-bottom">
        <div className="wrap">
          <h3>Quick logins</h3>
          <p className="small muted">Demo roles: click one to sign in. Nothing here is a password check.</p>
          <div className="grid g3">
            {USERS.map(u => (
              <div key={u.id} className="card quick-login">
                <span className="row spread"><strong>{u.name}</strong><Badge tone={u.role === 'analyst' ? 'navy' : undefined}>{u.role}</Badge></span>
                <span className="small muted">{u.blurb}</span>
                <button type="button" className="btn btn-primary" onClick={() => enter(u, u.pages[0])}>Log in <ArrowRight size={14} /></button>
                <span className="tiny muted">or go straight to</span>
                <span className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
                  {u.pages.map(pg => <button key={pg} type="button" className="btn btn-outline btn-sm" onClick={() => enter(u, pg)}>{PAGE_LABELS[pg]}</button>)}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
