import { useCallback, useEffect, useMemo, useState } from 'react'
import { Info, Loader2, Menu, Workflow } from 'lucide-react'
import { api } from './api.js'
import { useApiCall } from './hooks.js'
import { HORIZON, cohortCurve, filterMask, lostAt, riskColor } from './lib.js'
import Galaxy3D from './galaxy/Galaxy3D.jsx'
import TwinPanel from './components/TwinPanel.jsx'
import CampaignLab from './components/CampaignLab.jsx'
import ActionsPanel, { MARK_COLOR } from './components/ActionsPanel.jsx'
import { About, Dock, StatsPanel } from './components/Panels.jsx'

const NO_FILTER = { contracts: new Set(), internet: new Set(), flaggedOnly: false }

export default function App() {
  const [boot, setBoot] = useState({ galaxy: null, meta: null, scenarios: null, error: null })
  useEffect(() => {
    Promise.all([api('/meta'), api('/galaxy'), api('/twin/scenarios')])
      .then(([meta, galaxy, sc]) => setBoot({ meta, galaxy, scenarios: sc.scenarios, error: null }))
      .catch(e => setBoot(b => ({ ...b, error: e.message })))
  }, [])
  const { galaxy, meta, scenarios, error } = boot

  const [mode, setMode] = useState('risk')
  const [filters, setFilters] = useState(NO_FILTER)
  const [selected, setSelected] = useState(-1)
  const [edits, setEdits] = useState({})
  const [lookalikes, setLookalikes] = useState(false)
  const [reveal, setReveal] = useState(false)
  const [month, setMonth] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [left, setLeft] = useState('stats')            // which panel is open on the left: stats | lab | actions
  const [about, setAbout] = useState(false)
  const [menu, setMenu] = useState(false)
  const [camp, setCamp] = useState({ result: null, busy: false, error: null })
  const [showAfter, setShowAfter] = useState(true)

  // ---- actions (propose -> approve -> n8n carries it out -> n8n reports back)
  const [who, setWhoRaw] = useState(() => { try { return sessionStorage.getItem('evergreen.who') || '' } catch { return '' } })
  const setWho = useCallback(v => { setWhoRaw(v); try { sessionStorage.setItem('evergreen.who', v) } catch { /* storage blocked: the name lasts for this page only */ } }, [])
  const [acts, setActs] = useState({ data: null, busy: false, error: null })
  const loadActions = useCallback(() => api('/actions?limit=300').then(data => setActs(a => ({ ...a, data }))).catch(() => {}), [])
  useEffect(() => { loadActions() }, [loadActions])
  const inFlight = acts.data ? acts.data.actions.some(a => a.status === 'sent' || a.status === 'approved') : false
  useEffect(() => {
    if (left !== 'actions' && !inFlight) return
    const t = setInterval(loadActions, 2500)
    return () => clearInterval(t)
  }, [left, inFlight, loadActions])
  const act = async (fn) => {
    setActs(a => ({ ...a, busy: true, error: null }))
    try { await fn(); await loadActions(); setActs(a => ({ ...a, busy: false })) } catch (e) { setActs(a => ({ ...a, busy: false, error: e })) }
  }

  // ---- per-node arrays and the two "worlds" (as it is, and after a campaign)
  const nodes = galaxy ? galaxy.nodes : null
  const base = useMemo(() => nodes && ({
    p: Float32Array.from(nodes, n => n.p), mc: Float32Array.from(nodes, n => n.c.MonthlyCharges), u: Float32Array.from(nodes, n => n.u), pos: null,
  }), [nodes])
  const after = useMemo(() => {
    const r = camp.result
    if (!base || !r) return null
    const p = Float32Array.from(base.p), mc = Float32Array.from(base.mc), pos = new Array(base.p.length).fill(null)
    r.touched.forEach((id, k) => { p[id] = r.p_after[k]; mc[id] = r.mc_after[k]; pos[id] = r.pos_after[k] })
    return { p, mc, u: base.u, pos }
  }, [base, camp.result])
  const hasCamp = !!after
  const world = hasCamp && showAfter ? after : base

  const lostBase = useMemo(() => base && lostAt(base.u, base.p, base.mc, month), [base, month])
  const lostAfter = useMemo(() => after && lostAt(after.u, after.p, after.mc, month), [after, month])
  const lostNow = hasCamp && showAfter ? lostAfter : lostBase
  const cohortBase = useMemo(() => base && cohortCurve(base.p), [base])
  const cohortAfter = useMemo(() => after && cohortCurve(after.p), [after])
  const mask = useMemo(() => nodes && filterMask(nodes, filters, galaxy.threshold), [nodes, filters, galaxy])

  // ---- the selected customer's twin
  const node = selected >= 0 && nodes ? nodes[selected] : null
  const editKey = JSON.stringify([selected, edits])
  const twin = useApiCall(() => api('/galaxy/twin', { id: selected, changes: edits }), editKey, { delay: 170, enabled: selected >= 0 && Object.keys(edits).length > 0 })
  const suggestions = useApiCall(() => api('/twin/run', { customer: node.c }), 'sug' + selected, { delay: 0, enabled: selected >= 0 })
  const twinData = Object.keys(edits).length ? twin.data : null
  const openActionFor = id => (acts.data ? acts.data.actions.find(a => a.node_id === id && ['pending_approval', 'approved', 'sent', 'delivery_failed'].includes(a.status)) : null)
  const marks = useMemo(() => {
    const m = {}
    if (acts.data) for (const a of [...acts.data.actions].reverse()) if (MARK_COLOR[a.status]) m[a.node_id] = MARK_COLOR[a.status]
    return m
  }, [acts.data])
  const pendingCount = acts.data ? acts.data.summary.pending_approval : 0

  const pick = useCallback(id => { setSelected(id); setEdits({}); setReveal(false); setMenu(false) }, [])
  const close = useCallback(() => pick(-1), [pick])
  const propose = async () => {
    setActs(a => ({ ...a, busy: true, error: null }))
    try { await api('/actions', { id: selected, changes: edits, requested_by: who.trim() }); await loadActions(); setEdits({}); setLeft('actions'); setActs(a => ({ ...a, busy: false })) }
    catch (e) { setActs(a => ({ ...a, busy: false, error: e })) }
  }

  const view = useMemo(() => base && ({
    mode, mask, p: world.p, pos: world.pos, lost: month > 0 ? lostNow.mask : null,
    selected, lookalikes, threshold: galaxy.threshold, marks,
  }), [base, mode, mask, world, month, lostNow, selected, lookalikes, galaxy, marks])
  const probe = useMemo(() => (twinData && selected >= 0 ? { pos: twinData.pos_after, color: riskColor(twinData.risk_after) } : null), [twinData, selected])

  // ---- time travel
  useEffect(() => {
    if (!playing) return
    const t = setInterval(() => setMonth(m => { if (m >= HORIZON) { setPlaying(false); return m } return m + 1 }), 850)
    return () => clearInterval(t)
  }, [playing])
  const togglePlay = v => { if (v && month >= HORIZON) setMonth(0); setPlaying(v) }

  useEffect(() => {
    const k = e => { if (e.key === 'Escape') { if (about) setAbout(false); else close() } }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [about, close])

  const launch = async body => {
    setCamp(c => ({ ...c, busy: true, error: null }))
    try {
      const result = await api('/galaxy/campaign', body)
      setCamp({ result, busy: false, error: null }); setShowAfter(true)
    } catch (e) { setCamp(c => ({ ...c, busy: false, error: e })) }
  }

  if (error) return <div className="boot"><p className="err big">The API is not reachable: {error}</p></div>
  if (!galaxy || !meta) return <div className="boot"><Loader2 className="spin" size={28} /><p>Scoring {'the customers'} and building the galaxy…</p></div>

  return (
    <div className="stage">
      <Galaxy3D nodes={nodes} view={view} probe={probe} onPick={pick} pOf={world.p} />

      <header className="topbar">
        <div className="brand"><span className="logo" aria-hidden="true" /> <b>Evergreen</b><span>Customer Galaxy</span></div>
        <div className="top-badges">
          <span className="tag warn" title={galaxy.twin_label}>Model sensitivity · not causal</span>
          <span className="tag warn" title={galaxy.timeline_note}>Timeline illustrative</span>
          {galaxy.threshold_is_placeholder && <span className="tag warn" title="No offer cost, customer value or success rate is set in config.yaml">Alert line placeholder</span>}
        </div>
        <div className="top-actions">
          <button className={'btn ghost ' + (left === 'actions' ? 'on' : '')} onClick={() => setLeft(left === 'actions' ? 'stats' : 'actions')}>
            <Workflow size={15} /> Actions{pendingCount > 0 && <span className="badge">{pendingCount}</span>}
          </button>
          <button className="btn ghost menu-btn" onClick={() => setMenu(!menu)}><Menu size={15} /> Panels</button>
          <button className="btn ghost" onClick={() => setAbout(true)}><Info size={15} /> About</button>
        </div>
      </header>

      <div className={'lefts ' + (menu ? 'open' : '')}>
        {left === 'lab' && (
          <CampaignLab scenarios={scenarios} meta={meta} result={camp.result} busy={camp.busy} error={camp.error} showAfter={showAfter} setShowAfter={setShowAfter}
            onLaunch={launch} onClear={() => setCamp({ result: null, busy: false, error: null })} onClose={() => setLeft('stats')} />
        )}
        {left === 'actions' && (
          <ActionsPanel data={acts.data} who={who} setWho={setWho} busy={acts.busy} error={acts.error} onClose={() => setLeft('stats')}
            onApprove={id => act(() => api(`/actions/${id}/approve`, { decided_by: who.trim() }))}
            onReject={(id, reason) => act(() => api(`/actions/${id}/reject`, { decided_by: who.trim(), reason }))}
            onRetry={id => act(() => api(`/actions/${id}/retry`, {}))}
            onFocus={id => pick(id)} />
        )}
        {left === 'stats' && (
          <StatsPanel galaxy={galaxy} meta={meta} filters={filters} setFilters={setFilters} mode={mode} setMode={setMode} onLab={() => setLeft('lab')} labOpen={false} />
        )}
      </div>

      {node && (
        <TwinPanel node={node} meta={meta} edits={edits} setEdits={setEdits} twin={{ ...twin, data: twinData }} suggestions={suggestions} month={month}
          lookalikes={lookalikes} setLookalikes={setLookalikes} reveal={reveal} setReveal={setReveal} onClose={close} threshold={galaxy.threshold}
          who={who} setWho={setWho} openAction={openActionFor(selected)} onPropose={propose} proposing={acts.busy} proposeError={acts.error}
          onOpenActions={() => setLeft('actions')} />
      )}
      {!node && <p className="hint">Drag to orbit · scroll to zoom · <b>click a glow</b> to open that customer's digital twin</p>}

      <Dock month={month} setMonth={setMonth} playing={playing} setPlaying={togglePlay} base={lostBase} camp={lostAfter || lostBase} showAfter={showAfter} setShowAfter={setShowAfter}
        hasCamp={hasCamp} cohortBase={cohortBase} cohortCamp={cohortAfter} n={nodes.length} galaxy={galaxy} />
      {about && <About meta={meta} galaxy={galaxy} onClose={() => setAbout(false)} />}
    </div>
  )
}
