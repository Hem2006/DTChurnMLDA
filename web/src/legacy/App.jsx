import { useEffect, useState } from 'react'
import { LogOut } from 'lucide-react'
import { PAGE_LABELS, useApp } from './state.jsx'
import { Loading, Logo, Notice } from './components/ui.jsx'
import Home from './pages/Home.jsx'
import Score from './pages/Score.jsx'
import WhatIf from './pages/WhatIf.jsx'
import Recommend from './pages/Recommend.jsx'
import Batch from './pages/Batch.jsx'
import Queue from './pages/Queue.jsx'
import Model from './pages/Model.jsx'
import Autopilot from './pages/Autopilot.jsx'
import Incidents from './pages/Incidents.jsx'
import Login from './pages/Login.jsx'

const PAGES = { '': Home, score: Score, 'what-if': WhatIf, recommend: Recommend, batch: Batch, autopilot: Autopilot, incidents: Incidents, queue: Queue, model: Model }
export const ROUTES = Object.entries(PAGES).map(([path, el]) => ({ path, label: PAGE_LABELS[path], el }))

const current = () => window.location.hash.replace(/^#\/?/, '').split('?')[0]

export default function App() {
  const { meta, bootError, user, logout } = useApp()
  const [path, setPath] = useState(current)

  useEffect(() => {
    const on = () => { setPath(current()); window.scrollTo(0, 0) }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  const routes = user ? ROUTES.filter(r => r.path === '' || user.pages.includes(r.path)) : []
  const route = routes.find(r => r.path === path) || ROUTES[0]
  useEffect(() => { document.title = route.path ? `${route.label} · Evergreen` : 'Evergreen Retention' }, [route])
  const Page = route.el

  if (!user) return <Login />

  return (
    <>
      <header className="topbar">
        <div className="wrap">
          <a className="brand" href={"#/" + user.pages[0]}><Logo /> Evergreen</a>
          <div className="actions">
            <span className="pill" title={user.name}>{user.name} · {user.role}</span>
            <button className="btn btn-outline-light" onClick={logout}><LogOut size={14} /> Sign out</button>
          </div>
        </div>
      </header>
      <main>
        {bootError ? (
          <div className="wrap" style={{ padding: '48px 24px' }}><Notice kind="danger" title="The API is not reachable.">{bootError}</Notice></div>
        ) : !meta ? (
          <div className="wrap" style={{ padding: '48px 24px' }}><Loading text="Loading the model..." /></div>
        ) : (
          <Page />
        )}
      </main>
      <footer className="footer">
        <div className="wrap">
          <span className="row"><Logo size={22} /> Evergreen retention</span>
          <span>What-if results are model sensitivity, not causal estimates. Uplift is SIMULATED. Nothing is sent from this app.</span>
        </div>
      </footer>
    </>
  )
}
