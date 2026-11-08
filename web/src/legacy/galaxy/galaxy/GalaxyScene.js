// Imperative three.js scene for the customer galaxy. React owns the data and the UI; this class owns the pixels.
// Customers are glowing points. Distance from the core = churn risk, so a lower risk (after a what-if or campaign) drifts a node outwards.
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { CONTRACT_COLORS, hexToRgb, rampColor, riskColor } from '../lib.js'

const SCALE = 10                       // scene units -> world units
const R_IN = 0.18, R_OUT = 1.0         // must match app/galaxy.py
const GHOST = [0.42, 0.48, 0.62]
const HOME = new THREE.Vector3(0, -23, 15)

const VERT = `
attribute float aSize; attribute vec3 aColor; attribute float aAlpha; attribute float aPulse;
uniform float uTime; uniform float uPx;
varying vec3 vColor; varying float vAlpha;
void main() {
  vColor = aColor; vAlpha = aAlpha;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  float pulse = 1.0 + aPulse * 0.4 * sin(uTime * 3.2 + position.x * 0.9 + position.y * 0.7);
  gl_PointSize = aSize * pulse * uPx * (210.0 / -mv.z);
  gl_Position = projectionMatrix * mv;
}`
const FRAG = `
varying vec3 vColor; varying float vAlpha;
void main() {
  float r = length(gl_PointCoord - 0.5);
  float glow = pow(smoothstep(0.5, 0.0, r), 2.4);
  float hard = smoothstep(0.17, 0.09, r);
  float a = (glow * 0.85 + hard) * vAlpha;
  if (a < 0.01) discard;
  gl_FragColor = vec4(vColor * (0.3 + glow * 0.75 + hard * 0.3), a);
}`

// A thin glowing ring: marks a customer that has an action (pending, in flight, done ...).
const RING_FRAG = `
varying vec3 vColor; varying float vAlpha;
void main() {
  float r = length(gl_PointCoord - 0.5);
  float ring = smoothstep(0.05, 0.0, abs(r - 0.34));
  float halo = smoothstep(0.5, 0.3, r) * 0.18;
  float a = (ring + halo) * vAlpha;
  if (a < 0.01) discard;
  gl_FragColor = vec4(vColor * 1.2, a);
}`
const MAX_MARKS = 256

function pointsMaterial(uPx) {
  return new THREE.ShaderMaterial({
    vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uTime: { value: 0 }, uPx: { value: uPx } },
  })
}

function textSprite(text, color = '#9fb7e0') {
  const c = document.createElement('canvas'); c.width = 256; c.height = 64
  const x = c.getContext('2d'); x.font = '600 26px Inter, system-ui, sans-serif'; x.fillStyle = color; x.textAlign = 'center'; x.textBaseline = 'middle'
  x.fillText(text, 128, 32)
  const t = new THREE.CanvasTexture(c)
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, transparent: true, depthWrite: false, opacity: 0.4 }))
  s.scale.set(3.2, 0.8, 1)
  return s
}

function coreTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 256
  const x = c.getContext('2d'); const g = x.createRadialGradient(128, 128, 0, 128, 128, 128)
  g.addColorStop(0, 'rgba(0,0,0,1)'); g.addColorStop(0.28, 'rgba(0,0,0,0.95)'); g.addColorStop(0.36, 'rgba(255,61,113,0.75)')
  g.addColorStop(0.5, 'rgba(255,138,76,0.25)'); g.addColorStop(1, 'rgba(255,61,113,0)')
  x.fillStyle = g; x.fillRect(0, 0, 256, 256)
  return new THREE.CanvasTexture(c)
}

const ease = t => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

export class GalaxyScene {
  constructor(container, { onPick, onHover } = {}) {
    this.container = container
    this.onPick = onPick || (() => {})
    this.onHover = onHover || (() => {})
    this.n = 0
    this.nodes = []
    this.selected = -1
    this.idleSince = performance.now()
    this.fly = null
    this.clock = new THREE.Clock()
    this.ripple = 0

    const r = this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' })
    r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    r.setClearColor(0x000000, 0)
    container.appendChild(r.domElement)
    r.domElement.style.display = 'block'
    r.domElement.style.touchAction = 'none'

    this.scene = new THREE.Scene()
    this.camera = new THREE.PerspectiveCamera(52, 1, 0.1, 600)
    this.camera.up.set(0, 0, 1)
    this.camera.position.copy(HOME)
    this.controls = new OrbitControls(this.camera, r.domElement)
    Object.assign(this.controls, { enableDamping: true, dampingFactor: 0.07, minDistance: 3, maxDistance: 45, autoRotate: true, autoRotateSpeed: 0.35 })
    this.controls.addEventListener('start', () => { this.idleSince = performance.now(); this.controls.autoRotate = false; this.fly = null })
    this.controls.addEventListener('end', () => { this.idleSince = performance.now() })

    this._buildBackdrop()
    this._buildSelectionKit()
    this.pointer = { x: 0, y: 0, down: null, inside: false }
    this._bind()
    this.resize()
    this._loop = this._loop.bind(this)
    this.raf = requestAnimationFrame(this._loop)
  }

  // ------------------------------------------------------------ static decoration
  _buildBackdrop() {
    const stars = 1800, pos = new Float32Array(stars * 3), col = new Float32Array(stars * 3)
    for (let i = 0; i < stars; i++) {
      const rad = 70 + Math.random() * 160, th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1)
      pos.set([rad * Math.sin(ph) * Math.cos(th), rad * Math.sin(ph) * Math.sin(th), rad * Math.cos(ph)], i * 3)
      const k = 0.5 + Math.random() * 0.5
      col.set([0.6 * k, 0.75 * k, 1 * k], i * 3)
    }
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3)); g.setAttribute('color', new THREE.BufferAttribute(col, 3))
    this.stars = new THREE.Points(g, new THREE.PointsMaterial({ size: 0.55, vertexColors: true, transparent: true, opacity: 0.8, depthWrite: false, sizeAttenuation: true }))
    this.scene.add(this.stars)

    // the churn core
    this.core = new THREE.Mesh(new THREE.PlaneGeometry(4.2, 4.2), new THREE.MeshBasicMaterial({ map: coreTexture(), transparent: true, depthWrite: false, blending: THREE.NormalBlending }))
    this.scene.add(this.core)

    // risk rings
    this.rings = new THREE.Group()
    for (const p of [0.75, 0.5, 0.25]) {
      const rad = (R_IN + (R_OUT - R_IN) * Math.pow(1 - p, 0.8)) * SCALE
      const pts = []; for (let i = 0; i <= 128; i++) { const a = (i / 128) * Math.PI * 2; pts.push(new THREE.Vector3(Math.cos(a) * rad, Math.sin(a) * rad, 0)) }
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0x4f9cf9, transparent: true, opacity: 0.16, depthWrite: false }))
      this.rings.add(line)
      const lab = textSprite(Math.round(p * 100) + '% risk'); lab.position.set(0, -rad, 0.2); lab.scale.set(2.6, 0.65, 1)
      this.rings.add(lab)
    }
    this.scene.add(this.rings)
  }

  _buildSelectionKit() {
    this.halo = new THREE.Mesh(new THREE.RingGeometry(0.34, 0.4, 64), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.9, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide }))
    this.ripple1 = new THREE.Mesh(new THREE.RingGeometry(0.46, 0.5, 64), new THREE.MeshBasicMaterial({ color: 0x4f9cf9, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide }))
    this.halo.visible = this.ripple1.visible = false
    this.scene.add(this.halo, this.ripple1)

    const lg = new THREE.BufferGeometry(); lg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(2 * 8 * 3), 3))
    this.links = new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ color: 0x19e3c1, transparent: true, opacity: 0.55, depthWrite: false, blending: THREE.AdditiveBlending }))
    this.links.visible = false; this.links.frustumCulled = false
    this.scene.add(this.links)

    // the twin's probe: where the selected customer would sit after the edit, with a trail from where it is now
    const pg = new THREE.BufferGeometry()
    pg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(3), 3)); pg.setAttribute('aSize', new THREE.BufferAttribute(new Float32Array([3.4]), 1))
    pg.setAttribute('aColor', new THREE.BufferAttribute(new Float32Array([1, 1, 1]), 3)); pg.setAttribute('aAlpha', new THREE.BufferAttribute(new Float32Array([1]), 1))
    pg.setAttribute('aPulse', new THREE.BufferAttribute(new Float32Array([1]), 1))
    this.probe = new THREE.Points(pg, pointsMaterial(this.renderer.getPixelRatio()))
    this.probe.visible = false; this.probe.frustumCulled = false
    this.probeTo = new THREE.Vector3(); this.probeCur = new THREE.Vector3(); this.probeFrom = new THREE.Vector3()
    const tg = new THREE.BufferGeometry(); tg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(6), 3))
    this.trail = new THREE.Line(tg, new THREE.LineDashedMaterial({ color: 0xffe492, transparent: true, opacity: 0.9, dashSize: 0.25, gapSize: 0.15, depthWrite: false }))
    this.trail.visible = false; this.trail.frustumCulled = false
    this.scene.add(this.probe, this.trail)

    // action rings
    const mg = new THREE.BufferGeometry()
    this.markAttr = { pos: new Float32Array(MAX_MARKS * 3), col: new Float32Array(MAX_MARKS * 3), size: new Float32Array(MAX_MARKS).fill(3.6),
      alpha: new Float32Array(MAX_MARKS).fill(0.95), pulse: new Float32Array(MAX_MARKS).fill(1) }
    mg.setAttribute('position', new THREE.BufferAttribute(this.markAttr.pos, 3)); mg.setAttribute('aColor', new THREE.BufferAttribute(this.markAttr.col, 3))
    mg.setAttribute('aSize', new THREE.BufferAttribute(this.markAttr.size, 1)); mg.setAttribute('aAlpha', new THREE.BufferAttribute(this.markAttr.alpha, 1))
    mg.setAttribute('aPulse', new THREE.BufferAttribute(this.markAttr.pulse, 1)); mg.setDrawRange(0, 0)
    const mm = pointsMaterial(this.renderer.getPixelRatio()); mm.fragmentShader = RING_FRAG
    this.marks = new THREE.Points(mg, mm); this.marks.frustumCulled = false
    this.markIds = []
    this.scene.add(this.marks)
  }

  // ------------------------------------------------------------ data
  setData(nodes) {
    if (this.points) { this.scene.remove(this.points); this.points.geometry.dispose() }
    this.nodes = nodes
    const n = this.n = nodes.length
    this.cur = { pos: new Float32Array(n * 3), col: new Float32Array(n * 3), size: new Float32Array(n), alpha: new Float32Array(n), pulse: new Float32Array(n) }
    this.tgt = { pos: new Float32Array(n * 3), col: new Float32Array(n * 3), size: new Float32Array(n), alpha: new Float32Array(n), pulse: new Float32Array(n) }
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(this.cur.pos, 3)); g.setAttribute('aColor', new THREE.BufferAttribute(this.cur.col, 3))
    g.setAttribute('aSize', new THREE.BufferAttribute(this.cur.size, 1)); g.setAttribute('aAlpha', new THREE.BufferAttribute(this.cur.alpha, 1))
    g.setAttribute('aPulse', new THREE.BufferAttribute(this.cur.pulse, 1))
    this.points = new THREE.Points(g, pointsMaterial(this.renderer.getPixelRatio()))
    this.points.frustumCulled = false
    this.scene.add(this.points)
    // entrance: every node starts at the core, dark, and spirals out to its place
    for (let i = 0; i < n; i++) { this.cur.pos.set([0, 0, 0], i * 3); this.cur.alpha[i] = 0; this.cur.size[i] = 0 }
    this.entering = 1
  }

  /**
   * view = { mode, mask (Uint8Array), p (Float32Array|number[]), pos (array of [x,y,z] or null per node), lost (Uint8Array|null),
   *          selected (id|-1), lookalikes (bool), threshold }
   */
  update(view) {
    if (!this.n) return
    this.view = view
    const { mode, mask, p, pos, lost, selected, threshold } = view
    const t = this.tgt
    const near = new Set(view.lookalikes && selected >= 0 ? this.nodes[selected].nb : [])
    for (let i = 0; i < this.n; i++) {
      const nd = this.nodes[i]
      const pp = p[i]
      const q = pos && pos[i] ? pos[i] : nd.pos
      t.pos[i * 3] = q[0] * SCALE; t.pos[i * 3 + 1] = q[1] * SCALE; t.pos[i * 3 + 2] = q[2] * SCALE
      let c
      if (mode === 'contract') c = hexToRgb(CONTRACT_COLORS[nd.c.Contract] || '#9fb7e0')
      else if (mode === 'tenure') c = rampColor(1 - nd.c.tenure / 72)
      else if (mode === 'truth') c = hexToRgb(nd.churned ? '#ff3d71' : '#19e3c1')
      else c = riskColor(pp)
      let alpha = mask[i] ? 0.85 : 0.06
      let size = 0.8 + Math.min(nd.c.MonthlyCharges, 120) / 120 * 0.9
      if (lost && lost[i]) { c = GHOST; alpha = Math.min(alpha, 0.2); size *= 0.7 }
      if (near.has(i)) { size *= 1.6; alpha = Math.max(alpha, 0.95) }
      if (i === selected) { size *= 1.5; alpha = 1 }
      t.col[i * 3] = c[0]; t.col[i * 3 + 1] = c[1]; t.col[i * 3 + 2] = c[2]
      t.size[i] = size; t.alpha[i] = alpha
      t.pulse[i] = pp >= threshold && mask[i] && !(lost && lost[i]) ? 1 : 0
    }
    this._select(selected, near)
    // rings for customers with an action: view.marks = { nodeId: '#rrggbb' }
    const ids = Object.keys(view.marks || {}).map(Number).filter(i => i >= 0 && i < this.n).slice(0, MAX_MARKS)
    this.markIds = ids
    ids.forEach((id, k) => this.markAttr.col.set(hexToRgb(view.marks[id]), k * 3))
    this.marks.geometry.attributes.aColor.needsUpdate = true
    this.marks.geometry.setDrawRange(0, ids.length)
  }

  _select(id, near) {
    const changed = id !== this.selected
    this.selected = id
    const on = id >= 0
    this.halo.visible = on
    this.links.visible = on && near.size > 0
    if (changed) {
      this.probe.visible = this.trail.visible = false
      if (on) { this.ripple = 1; this.flyTo(this._nodePos(id), 7.5) } else this.flyTo(new THREE.Vector3(0, 0, 0), HOME.length())
    }
    if (this.links.visible) {
      const a = this.links.geometry.attributes.position.array; a.fill(0)
      const from = this._nodeTarget(id); let k = 0
      for (const j of near) { const to = this._nodeTarget(j); a.set([from.x, from.y, from.z, to.x, to.y, to.z], k * 6); k++ }
      this.links.geometry.setDrawRange(0, near.size * 2); this.links.geometry.attributes.position.needsUpdate = true
    }
  }

  /** Show (or clear with null) where the selected customer would be after the what-if. pos is in scene units. */
  setProbe(pos, color) {
    if (!pos || this.selected < 0) { this.probe.visible = this.trail.visible = false; return }
    this.probeFrom.copy(this._nodeTarget(this.selected))
    this.probeTo.set(pos[0] * SCALE, pos[1] * SCALE, pos[2] * SCALE)
    if (!this.probe.visible) this.probeCur.copy(this.probeFrom)
    const c = this.probe.geometry.attributes.aColor.array; c.set(color || [1, 1, 1]); this.probe.geometry.attributes.aColor.needsUpdate = true
    this.probe.visible = this.trail.visible = true
  }

  _nodePos(i) { return new THREE.Vector3(this.cur.pos[i * 3], this.cur.pos[i * 3 + 1], this.cur.pos[i * 3 + 2]) }
  _nodeTarget(i) { return new THREE.Vector3(this.tgt.pos[i * 3], this.tgt.pos[i * 3 + 1], this.tgt.pos[i * 3 + 2]) }

  flyTo(target, dist) {
    const dir = this.camera.position.clone().sub(this.controls.target)
    if (dir.lengthSq() < 1e-6) dir.copy(HOME)
    dir.setLength(dist)
    this.fly = { t0: performance.now(), dur: 1100, fromT: this.controls.target.clone(), toT: target.clone(), fromP: this.camera.position.clone(), toP: target.clone().add(dir) }
    this.controls.autoRotate = false
  }

  // ------------------------------------------------------------ input
  _bind() {
    const el = this.renderer.domElement
    this._onMove = e => {
      const b = el.getBoundingClientRect()
      this.pointer.x = e.clientX - b.left; this.pointer.y = e.clientY - b.top; this.pointer.inside = true; this.pointer.dirty = true
    }
    this._onLeave = () => { this.pointer.inside = false; this.hover = -1; this.onHover(-1) }
    this._onDown = e => { this.pointer.down = { x: e.clientX, y: e.clientY } }
    this._onUp = e => {
      const d = this.pointer.down; this.pointer.down = null
      if (d && Math.hypot(e.clientX - d.x, e.clientY - d.y) < 5) { const b = el.getBoundingClientRect(); this.onPick(this._nearest(e.clientX - b.left, e.clientY - b.top, 18)) }
    }
    el.addEventListener('pointermove', this._onMove); el.addEventListener('pointerleave', this._onLeave)
    el.addEventListener('pointerdown', this._onDown); el.addEventListener('pointerup', this._onUp)
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(this.container)
  }

  _nearest(px, py, maxPx) {
    if (!this.n) return -1
    const w = this.container.clientWidth, h = this.container.clientHeight, v = new THREE.Vector3()
    let best = -1, bd = maxPx * maxPx
    this.camera.updateMatrixWorld()
    for (let i = 0; i < this.n; i++) {
      if (this.cur.alpha[i] < 0.3) continue
      v.set(this.cur.pos[i * 3], this.cur.pos[i * 3 + 1], this.cur.pos[i * 3 + 2]).project(this.camera)
      if (v.z > 1) continue
      const dx = (v.x * 0.5 + 0.5) * w - px, dy = (-v.y * 0.5 + 0.5) * h - py, d = dx * dx + dy * dy
      if (d < bd) { bd = d; best = i }
    }
    return best
  }

  resize() {
    const w = this.container.clientWidth || 1, h = this.container.clientHeight || 1
    this.renderer.setSize(w, h, false)
    this.renderer.domElement.style.width = w + 'px'; this.renderer.domElement.style.height = h + 'px'
    this.camera.aspect = w / h; this.camera.updateProjectionMatrix()
  }

  // ------------------------------------------------------------ frame
  _loop() {
    this.raf = requestAnimationFrame(this._loop)
    const dt = Math.min(this.clock.getDelta(), 0.5), time = this.clock.elapsedTime
    const k = 1 - Math.exp(-dt * 5)
    if (this.n) {
      const c = this.cur, t = this.tgt
      const kk = this.entering > 0 ? 1 - Math.exp(-dt * 2.2) : k
      this.entering = Math.max(0, this.entering - dt * 0.5)
      for (let i = 0; i < this.n * 3; i++) { c.pos[i] += (t.pos[i] - c.pos[i]) * kk; c.col[i] += (t.col[i] - c.col[i]) * k }
      for (let i = 0; i < this.n; i++) {
        c.size[i] += (t.size[i] - c.size[i]) * k; c.alpha[i] += (t.alpha[i] - c.alpha[i]) * k; c.pulse[i] = t.pulse[i]
      }
      const a = this.points.geometry.attributes
      a.position.needsUpdate = a.aColor.needsUpdate = a.aSize.needsUpdate = a.aAlpha.needsUpdate = a.aPulse.needsUpdate = true
      this.points.material.uniforms.uTime.value = time
    }
    if (this.n && this.markIds.length) {
      const mp = this.markAttr.pos
      this.markIds.forEach((id, k) => { mp[k * 3] = this.cur.pos[id * 3]; mp[k * 3 + 1] = this.cur.pos[id * 3 + 1]; mp[k * 3 + 2] = this.cur.pos[id * 3 + 2] })
      this.marks.geometry.attributes.position.needsUpdate = true
      this.marks.material.uniforms.uTime.value = time
    }
    this.stars.rotation.z += dt * 0.004
    this.core.quaternion.copy(this.camera.quaternion)
    this.core.scale.setScalar(1 + Math.sin(time * 1.4) * 0.03)

    if (this.selected >= 0 && this.n) {
      const p = this._nodePos(this.selected)
      this.halo.position.copy(p); this.halo.quaternion.copy(this.camera.quaternion)
      this.halo.scale.setScalar(1 + Math.sin(time * 4) * 0.08)
      if (this.ripple > 0) {
        this.ripple = Math.max(0, this.ripple - dt * 0.45)
        const s = 1 + (1 - this.ripple) * 14
        this.ripple1.visible = true; this.ripple1.position.copy(p); this.ripple1.quaternion.copy(this.camera.quaternion)
        this.ripple1.scale.setScalar(s); this.ripple1.material.opacity = this.ripple * 0.7
      } else this.ripple1.visible = false
      if (this.links.visible) {
        const arr = this.links.geometry.attributes.position.array, near = this.nodes[this.selected].nb
        arr.set([p.x, p.y, p.z], 0)
        let kx = 0
        near.forEach(j => { arr.set([p.x, p.y, p.z, this.cur.pos[j * 3], this.cur.pos[j * 3 + 1], this.cur.pos[j * 3 + 2]], kx * 6); kx++ })
        this.links.geometry.attributes.position.needsUpdate = true
      }
      if (this.probe.visible) {
        this.probeCur.lerp(this.probeTo, k * 1.4)
        this.probe.geometry.attributes.position.array.set([this.probeCur.x, this.probeCur.y, this.probeCur.z]); this.probe.geometry.attributes.position.needsUpdate = true
        this.trail.geometry.attributes.position.array.set([p.x, p.y, p.z, this.probeCur.x, this.probeCur.y, this.probeCur.z]); this.trail.geometry.attributes.position.needsUpdate = true
        this.trail.computeLineDistances()
        this.probe.material.uniforms.uTime.value = time
      }
    } else { this.halo.visible = false; this.ripple1.visible = false }

    if (this.fly) {
      const q = Math.min(1, (performance.now() - this.fly.t0) / this.fly.dur), e = ease(q)
      this.controls.target.lerpVectors(this.fly.fromT, this.fly.toT, e)
      this.camera.position.lerpVectors(this.fly.fromP, this.fly.toP, e)
      if (q >= 1) this.fly = null
    } else if (!this.controls.autoRotate && this.selected < 0 && performance.now() - this.idleSince > 8000) this.controls.autoRotate = true

    if (this.pointer.dirty && this.pointer.inside && !this.pointer.down) {
      this.pointer.dirty = false
      const h = this._nearest(this.pointer.x, this.pointer.y, 14)
      if (h !== this.hover) { this.hover = h; this.renderer.domElement.style.cursor = h >= 0 ? 'pointer' : 'grab' }
      this.onHover(h, this.pointer.x, this.pointer.y)
    }
    this.controls.update()
    this.renderer.render(this.scene, this.camera)
  }

  dispose() {
    cancelAnimationFrame(this.raf)
    const el = this.renderer.domElement
    el.removeEventListener('pointermove', this._onMove); el.removeEventListener('pointerleave', this._onLeave)
    el.removeEventListener('pointerdown', this._onDown); el.removeEventListener('pointerup', this._onUp)
    this.ro.disconnect(); this.controls.dispose()
    this.scene.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => { if (m.map) m.map.dispose(); m.dispose() }) })
    this.renderer.dispose(); el.remove()
  }
}
