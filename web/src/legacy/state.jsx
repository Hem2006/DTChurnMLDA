import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { DEFAULT_CUSTOMER, makeCoherent } from './lib.js'

const Ctx = createContext(null)
export const useApp = () => useContext(Ctx)

function load(key, fallback) {
  try { const v = sessionStorage.getItem(key); return v ? JSON.parse(v) : fallback } catch { return fallback }
}
function save(key, v) {
  try { sessionStorage.setItem(key, JSON.stringify(v)) } catch { /* storage blocked: state lasts for this page only */ }
}

// Operational account state is NOT in the model data. null = unknown, and unknown is never assumed safe (it forces approval).
export const UNKNOWN_STATE = { vulnerable: null, escalated: null, contacts_this_month: null, discount_pct_used_this_quarter: null }

// UI-level demo roles. This only decides what the app SHOWS; the API itself is not role-gated (use CHURN_API_KEY for that).
export const PAGE_LABELS = {
  '': 'Overview', score: 'Score a customer', 'what-if': 'Digital twin', recommend: 'Recommend & message', batch: 'Score a file',
  autopilot: 'Autopilot', incidents: 'Service incidents', queue: 'Review queue', model: 'Model & fairness',
}
// First page is where the quick login lands. Overview ('') is open to everyone.
export const USERS = [
  { id: 'analyst', name: 'Alex Analyst', role: 'analyst', blurb: 'Model metrics, evaluation and fairness.', pages: ['autopilot', 'what-if', 'model', 'batch'] },
  { id: 'agent', name: 'Riya Retention', role: 'agent', blurb: 'Scores customers, runs what-ifs, drafts messages.', pages: ['autopilot', 'what-if', 'score', 'recommend'] },
  { id: 'approver', name: 'Morgan Manager', role: 'approver', blurb: 'Works the review queue and approves offers.', pages: ['autopilot', 'queue', 'what-if'] },
]

export function AppProvider({ children }) {
  const [user, setUser] = useState(() => USERS.find(u => u.id === load('evergreen.user', null)) || null)
  const login = useCallback(id => { const u = USERS.find(x => x.id === id) || null; save('evergreen.user', u ? u.id : null); setUser(u) }, [])
  const logout = useCallback(() => { save('evergreen.user', null); setUser(null) }, [])
  const [meta, setMeta] = useState(null)
  const [health, setHealth] = useState(null)
  const [bootError, setBootError] = useState(null)
  const [customer, setCustomerRaw] = useState(() => load('evergreen.customer', DEFAULT_CUSTOMER))
  const [accountState, setAccountStateRaw] = useState(() => load('evergreen.state', UNKNOWN_STATE))

  useEffect(() => {
    Promise.all([api('/meta'), api('/health')]).then(([m, h]) => { setMeta(m); setHealth(h) }).catch(e => setBootError(e.message))
  }, [])

  const setCustomer = useCallback(c => {
    setCustomerRaw(prev => { const next = makeCoherent(typeof c === 'function' ? c(prev) : c); save('evergreen.customer', next); return next })
  }, [])
  const setAccountState = useCallback(s => { setAccountStateRaw(s); save('evergreen.state', s) }, [])

  return <Ctx.Provider value={{ user, login, logout, meta, health, bootError, customer, setCustomer, accountState, setAccountState }}>{children}</Ctx.Provider>
}

/** Run an API call whenever `key` changes (debounced); ignores stale responses. */
export function useApiCall(fn, key, { delay = 250, enabled = true } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: enabled })
  const seq = useRef(0)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!enabled) { setState({ data: null, error: null, loading: false }); return }
    const id = ++seq.current
    setState(s => ({ ...s, loading: true }))
    const t = setTimeout(() => {
      fn().then(data => id === seq.current && setState({ data, error: null, loading: false }))
        .catch(error => id === seq.current && setState({ data: null, error, loading: false }))
    }, delay)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled, tick])
  return { ...state, reload: () => setTick(x => x + 1) }
}
