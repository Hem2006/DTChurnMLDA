import { useCallback, useEffect, useState } from 'react'
import { Bot, ClipboardCheck, ListChecks, Loader2, LogOut, ScrollText, Settings2, ShieldCheck, UserRound, Users } from 'lucide-react'
import { api, getToken, setApiKey, setToken } from './api.js'
import { ROLE_TEXT, workCount } from './lib.js'
import Login from './pages/Login.jsx'
import Agent from './pages/Agent.jsx'
import Customers from './pages/Customers.jsx'
import Tracker from './pages/Tracker.jsx'
import Review from './pages/Review.jsx'
import Compliance from './pages/Compliance.jsx'
import Admin from './pages/Admin.jsx'

const USER = 'evergreen.user'
const read = () => { try { return JSON.parse(sessionStorage.getItem(USER) || 'null') } catch { return null } }
const write = u => { try { u ? sessionStorage.setItem(USER, JSON.stringify(u)) : sessionStorage.removeItem(USER) } catch { /* storage blocked: lasts for this page only */ } }

// Each role has its own screens. `badge` = the queue that role works from (see ROLE_QUEUE in lib.js).
const TABS = {
  analyst: [['agent', 'Retention agent', Bot], ['customers', 'Customers & twins', Users], ['tracker', 'Case tracker', ListChecks]],
  manager: [['review', 'Approval queue', ClipboardCheck, true], ['tracker', 'Case tracker', ListChecks]],
  compliance: [['messages', 'Message check', ShieldCheck, true], ['care', 'Protected accounts', UserRound], ['tracker', 'Case tracker', ListChecks]],
  admin: [['overview', 'Control room', Settings2], ['people', 'People & duties', Users], ['audit', 'Audit trail', ScrollText]],
}

export default function App() {
  const [user, setUser] = useState(() => (getToken() ? read() : null))
  const [boot, setBoot] = useState({ meta: null, stages: null, error: null })
  const [tab, setTab] = useState(null)
  const [runId, setRunId] = useState(null)          // the run the Customers page shows
  const [counts, setCounts] = useState(null)

  useEffect(() => {
    Promise.all([api('/meta'), api('/agent/stages')])
      .then(([meta, stages]) => setBoot({ meta, stages, error: null }))
      .catch(e => setBoot(b => ({ ...b, error: e.message })))
  }, [])

  const signOut = useCallback(() => { write(null); setToken(''); setApiKey(''); setUser(null); setTab(null) }, [])
  useEffect(() => { window.addEventListener('evergreen:signed-out', signOut); return () => window.removeEventListener('evergreen:signed-out', signOut) }, [signOut])
  const signIn = u => { write(u); setUser(u); setTab(TABS[u.role][0][0]) }

  const refresh = useCallback(() => {
    if (!user || user.role === 'admin') return
    api('/workflow/board').then(d => setCounts(d.counts)).catch(() => {})
  }, [user])
  useEffect(() => { refresh(); const t = setInterval(refresh, 6000); return () => clearInterval(t) }, [refresh])

  if (boot.error) return <div className="boot"><p className="err big">The Evergreen service is not reachable: {boot.error}</p></div>
  if (!boot.meta) return <div className="boot"><Loader2 className="spin" size={26} /><p>Starting Evergreen…</p></div>
  if (!user) return <Login meta={boot.meta} onSignIn={signIn} />

  const tabs = TABS[user.role]
  const cur = tabs.some(t => t[0] === tab) ? tab : tabs[0][0]
  const waiting = workCount(user.role, counts)
  const common = { user, onChange: refresh }

  return (
    <div className="shell">
      <header className="top">
        <div className="brand"><span className="logo" aria-hidden="true" /><b>Evergreen</b><span>Retention</span></div>
        <nav className="tabs" aria-label="Sections">
          {tabs.map(([k, label, Icon, badge]) => (
            <button key={k} className={cur === k ? 'on' : ''} onClick={() => setTab(k)} aria-current={cur === k ? 'page' : undefined}>
              <Icon size={16} /> {label}{badge && waiting > 0 && <span className="count">{waiting}</span>}
            </button>
          ))}
        </nav>
        <div className="me">
          <span className={'role-chip role-' + user.role} title={ROLE_TEXT[user.role]?.[1]}>{ROLE_TEXT[user.role]?.[0]}</span>
          <span className="who" title="Recorded on every step you take">{user.name}</span>
          <button className="icon" onClick={signOut} aria-label="Sign out" title="Sign out"><LogOut size={16} /></button>
        </div>
      </header>
      <main className="main">
        {cur === 'agent' && <Agent who={user.name} stages={boot.stages} onOpenCustomers={id => { setRunId(id); setTab('customers') }} onOpenApprovals={() => setTab('tracker')} onChange={refresh} />}
        {cur === 'customers' && <Customers who={user.name} user={user} meta={boot.meta} runId={runId} setRunId={setRunId} onChange={refresh} />}
        {cur === 'tracker' && <Tracker {...common} />}
        {cur === 'review' && <Review {...common} />}
        {cur === 'messages' && <Compliance {...common} view="messages" />}
        {cur === 'care' && <Compliance {...common} view="care" />}
        {['overview', 'people', 'audit'].includes(cur) && <Admin {...common} meta={boot.meta} view={cur} />}
      </main>
    </div>
  )
}
