import { useCallback, useEffect, useState } from 'react'
import { Bot, ClipboardCheck, Loader2, LogOut, Users } from 'lucide-react'
import { api, setApiKey } from './api.js'
import Login from './pages/Login.jsx'
import Agent from './pages/Agent.jsx'
import Customers from './pages/Customers.jsx'
import Approvals from './pages/Approvals.jsx'

const WHO = 'evergreen.who'
const read = k => { try { return sessionStorage.getItem(k) || '' } catch { return '' } }
const write = (k, v) => { try { v ? sessionStorage.setItem(k, v) : sessionStorage.removeItem(k) } catch { /* storage blocked: lasts for this page only */ } }

const TABS = [
  ['agent', 'Retention agent', Bot],
  ['customers', 'Customers & twins', Users],
  ['approvals', 'Approvals', ClipboardCheck],
]

export default function App() {
  const [who, setWho] = useState(() => read(WHO))
  const [boot, setBoot] = useState({ meta: null, stages: null, error: null })
  const [tab, setTab] = useState('agent')
  const [runId, setRunId] = useState(null)          // the run the Customers page shows
  const [pending, setPending] = useState(0)

  useEffect(() => {
    Promise.all([api('/meta'), api('/agent/stages')])
      .then(([meta, stages]) => setBoot({ meta, stages, error: null }))
      .catch(e => setBoot(b => ({ ...b, error: e.message })))
  }, [])

  const refreshPending = useCallback(() => {
    if (!who) return
    api('/cases?status=pending_approval&limit=1').then(d => setPending(d.summary.pending_approval || 0)).catch(() => {})
  }, [who])
  useEffect(() => { refreshPending(); const t = setInterval(refreshPending, 6000); return () => clearInterval(t) }, [refreshPending])

  const signIn = (name, key) => { write(WHO, name); setApiKey(key || ''); setWho(name) }
  const signOut = () => { write(WHO, ''); setApiKey(''); setWho('') }
  const openCustomers = id => { setRunId(id); setTab('customers') }

  if (boot.error) return <div className="boot"><p className="err big">The Evergreen service is not reachable: {boot.error}</p></div>
  if (!boot.meta) return <div className="boot"><Loader2 className="spin" size={26} /><p>Starting Evergreen…</p></div>
  if (!who) return <Login meta={boot.meta} onSignIn={signIn} />

  return (
    <div className="shell">
      <header className="top">
        <div className="brand"><span className="logo" aria-hidden="true" /><b>Evergreen</b><span>Retention</span></div>
        <nav className="tabs" aria-label="Sections">
          {TABS.map(([k, label, Icon]) => (
            <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)} aria-current={tab === k ? 'page' : undefined}>
              <Icon size={16} /> {label}{k === 'approvals' && pending > 0 && <span className="count">{pending}</span>}
            </button>
          ))}
        </nav>
        <div className="me">
          <span className="who" title="Recorded on every approval">{who}</span>
          <button className="icon" onClick={signOut} aria-label="Sign out" title="Sign out"><LogOut size={16} /></button>
        </div>
      </header>
      <main className="main">
        {tab === 'agent' && <Agent who={who} stages={boot.stages} onOpenCustomers={openCustomers} onOpenApprovals={() => setTab('approvals')} onChange={refreshPending} />}
        {tab === 'customers' && <Customers who={who} meta={boot.meta} runId={runId} setRunId={setRunId} onChange={refreshPending} />}
        {tab === 'approvals' && <Approvals who={who} onChange={refreshPending} />}
      </main>
    </div>
  )
}
