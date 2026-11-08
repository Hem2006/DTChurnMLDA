import { useEffect, useRef, useState } from 'react'
import { GalaxyScene } from './GalaxyScene.js'
import { money, pctRisk } from '../lib.js'

/** The WebGL canvas plus a hover tooltip. `view` and `probe` must be memoised objects; the scene is only touched when they change. */
export default function Galaxy3D({ nodes, view, probe, onPick, pOf }) {
  const box = useRef(null)
  const scene = useRef(null)
  const last = useRef(null)
  const cb = useRef({})
  cb.current = { onPick }
  const [tip, setTip] = useState(null)
  const [gl, setGl] = useState(true)

  useEffect(() => {
    let s
    try {
      s = new GalaxyScene(box.current, {
        onPick: id => cb.current.onPick(id),
        onHover: (id, x, y) => setTip(t => (id < 0 ? (t ? null : t) : { id, x, y })),
      })
    } catch { setGl(false); return }
    scene.current = s
    s.setData(nodes)
    if (last.current) s.update(last.current)
    return () => { s.dispose(); scene.current = null }
  }, [nodes])

  useEffect(() => { last.current = view; scene.current?.update(view) }, [view, nodes])
  useEffect(() => { scene.current?.setProbe(probe ? probe.pos : null, probe ? probe.color : null) }, [probe, view])

  const n = tip ? nodes[tip.id] : null
  return (
    <div className="canvas-wrap" ref={box}>
      {!gl && <div className="gl-fail">This browser could not start WebGL, which the galaxy needs. Try a recent Chrome, Edge, Firefox or Safari with hardware acceleration on.</div>}
      {n && (
        <div className="tip" style={{ left: Math.min(tip.x + 16, (box.current?.clientWidth || 800) - 220), top: Math.max(8, tip.y - 12) }}>
          <b>{n.ref}</b>
          <span className="tip-risk">{pctRisk(pOf[tip.id])} chance of leaving</span>
          <span>{n.c.Contract} · {n.c.tenure} mo · {money(n.c.MonthlyCharges, 2)}/mo</span>
          <span>{n.c.InternetService === 'No' ? 'No internet' : n.c.InternetService}</span>
        </div>
      )}
    </div>
  )
}
