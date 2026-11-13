// Document model: layers, compositing, caches, selection and structural history.
import { History } from './history'
import { adjPad, applyAdjust, applyFx, defaultFx, fxActive, fxPad } from './fx'
import { ensureFont, isFontReady, renderShape, renderText } from './render'
import { selCombine, selFromCanvas, selFromMask, selInvert } from './selection'
import type { AdjustItem, BlendMode, Effects, Layer, LayerType, Rect, Sel, SelMode, ShapeProps, TextProps, Transform } from './types'
import { gco } from './types'
import { clipRect, cloneCanvas, ctx2, mkCanvas, uid } from './util'

export const hooks = {
  render: () => {},
  ui: () => {},
  toast: (_m: string, _kind?: 'info' | 'error' | 'ok') => {},
}

export function makeLayer(o: Partial<Layer> & { canvas: HTMLCanvasElement; type: LayerType }): Layer {
  return {
    id: uid(), name: 'Layer', visible: true, locked: false, opacity: 1, blend: 'normal',
    t: { cx: o.canvas.width / 2, cy: o.canvas.height / 2, sx: 1, sy: 1, rot: 0 },
    mask: null, editMask: false, clip: false, adjust: [], fx: defaultFx(),
    rev: 0, px: 0, cache: null, cacheCv: null, cacheOk: false,
    ...o,
  }
}

export interface PropSnap {
  name: string; visible: boolean; locked: boolean; opacity: number; blend: BlendMode; clip: boolean; editMask: boolean; pass?: boolean
  t: Transform; adjust: AdjustItem[]; fx: Effects; text?: TextProps; shape?: ShapeProps; maskEnabled?: boolean
}
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v))

interface LayerState { L: Layer; canvas: HTMLCanvasElement; t: Transform; type: LayerType; text?: TextProps; shape?: ShapeProps; maskCv: HTMLCanvasElement | null }

export class Doc {
  id = uid()
  name: string
  w: number
  h: number
  layers: Layer[] = []
  activeId = ''
  multi = new Set<string>()
  guides: { id: string; axis: 'h' | 'v'; pos: number }[] = []
  sel: Sel | null = null
  comp: HTMLCanvasElement
  compDirty = true
  stroke: HTMLCanvasElement
  clipTmp: HTMLCanvasElement | null = null
  history = new History()
  view = { zoom: 0, ox: 0, oy: 0, fitted: false }
  revision = 0
  savedRevision = 0

  constructor(w: number, h: number, name = 'Untitled') {
    this.name = name
    this.w = w
    this.h = h
    this.comp = mkCanvas(w, h)
    this.stroke = mkCanvas(w, h)
    this.history.onChange = () => { this.revision++; hooks.ui() }
  }

  get active(): Layer | undefined { return this.layers.find((l) => l.id === this.activeId) }
  get activeIndex() { return this.layers.findIndex((l) => l.id === this.activeId) }
  get dirty() { return this.revision !== this.savedRevision }
  layer(id: string) { return this.layers.find((l) => l.id === id) }

  // ---------- change notification ----------
  changed() { this.compDirty = true; this.revision++; hooks.render(); hooks.ui() }
  touch(L: Layer, pixels = false) {
    L.cacheOk = false; L.rev++
    if (pixels) L.px++
    for (let p = L.parent ? this.layer(L.parent) : undefined, g = 0; p && g < 64; p = p.parent ? this.layer(p.parent) : undefined, g++) { p.cacheOk = false; p.rev++ }
    this.compDirty = true
    hooks.render()
  }
  /** pixels of L changed in place during a stroke (cheap: no UI refresh) */
  invalidateAll() { for (const l of this.layers) l.cacheOk = false; this.compDirty = true }

  // ---------- geometry ----------
  isIdentity(L: Layer) {
    const t = L.t
    return t.sx === 1 && t.sy === 1 && t.rot === 0 && t.cx === this.w / 2 && t.cy === this.h / 2 && L.canvas.width === this.w && L.canvas.height === this.h
  }
  layerBounds(L: Layer): Rect {
    if (L.type === 'group') {
      const rs = this.descendants(L).filter((x) => x.type !== 'group').map((x) => this.layerBounds(x))
      if (!rs.length) return { x: 0, y: 0, w: 0, h: 0 }
      const x0 = Math.min(...rs.map((r) => r.x)), y0 = Math.min(...rs.map((r) => r.y))
      return { x: x0, y: y0, w: Math.max(...rs.map((r) => r.x + r.w)) - x0, h: Math.max(...rs.map((r) => r.y + r.h)) - y0 }
    }
    const { cx, cy, sx, sy, rot } = L.t, hw = (L.canvas.width * sx) / 2, hh = (L.canvas.height * sy) / 2
    const c = Math.cos(rot), s = Math.sin(rot)
    const xs: number[] = [], ys: number[] = []
    for (const [px, py] of [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]]) { xs.push(cx + px * c - py * s); ys.push(cy + px * s + py * c) }
    const x0 = Math.min(...xs), y0 = Math.min(...ys)
    return { x: x0, y: y0, w: Math.max(...xs) - x0, h: Math.max(...ys) - y0 }
  }

  // ---------- caches & compositing ----------
  getCache(L: Layer): HTMLCanvasElement {
    if (L.cacheOk && L.cache) return L.cache
    const { w, h } = this, t = L.t
    const isGroup = L.type === 'group'
    const adj = L.adjust.filter((a) => a.enabled)
    const mask = L.mask?.enabled ? L.mask.canvas : null
    const fxOn = fxActive(L.fx)
    const ident = !isGroup && this.isIdentity(L)
    if (ident && !adj.length && !mask && !fxOn) { L.cache = L.canvas; L.cacheOk = true; return L.cache }
    let cv = L.cacheCv
    if (!cv || cv.width !== w || cv.height !== h) cv = L.cacheCv = mkCanvas(w, h)
    const c = ctx2(cv)
    c.setTransform(1, 0, 0, 1, 0, 0); c.globalAlpha = 1; c.globalCompositeOperation = 'source-over'; c.filter = 'none'
    c.clearRect(0, 0, w, h)
    if (isGroup) this.renderList(c, this.childrenOf(L.id))
    else if (ident) c.drawImage(L.canvas, 0, 0)
    else {
      c.save()
      c.translate(t.cx, t.cy); c.rotate(t.rot); c.scale(t.sx, t.sy)
      c.imageSmoothingEnabled = !(Math.abs(t.sx) === 1 && Math.abs(t.sy) === 1 && t.rot === 0)
      c.imageSmoothingQuality = 'high'
      c.drawImage(L.canvas, -L.canvas.width / 2, -L.canvas.height / 2)
      c.restore()
    }
    if (adj.length || fxOn) {
      const b = this.layerBounds(L), pad = (adj.length ? adjPad(adj) : 0) + (fxOn ? fxPad(L.fx) : 0) + 2
      const r = clipRect({ x: b.x - pad, y: b.y - pad, w: b.w + pad * 2, h: b.h + pad * 2 }, w, h)
      for (const a of adj) applyAdjust(cv, r, a.type, a.p)
      if (fxOn) applyFx(cv, r, L.fx)
    }
    if (mask) { c.globalCompositeOperation = 'destination-in'; c.drawImage(mask, 0, 0); c.globalCompositeOperation = 'source-over' }
    L.cache = cv; L.cacheOk = true
    return cv
  }
  /** composite sibling list (bottom -> top) onto c; pass-through groups recurse straight into c */
  private renderList(c: CanvasRenderingContext2D, list: Layer[]) {
    for (let i = 0; i < list.length; i++) {
      const L = list[i]
      if (!L.visible) continue
      if (L.type === 'group' && L.pass !== false && L.opacity === 1 && !L.clip && !L.mask?.enabled && !L.adjust.some((a) => a.enabled) && !fxActive(L.fx)) {
        this.renderList(c, this.childrenOf(L.id))
        continue
      }
      let src = this.getCache(L)
      if (L.clip) {
        let j = i - 1
        while (j >= 0 && list[j].clip) j--
        const base = j >= 0 ? list[j] : null
        if (!base || !base.visible) continue
        if (!this.clipTmp || this.clipTmp.width !== this.w || this.clipTmp.height !== this.h) this.clipTmp = mkCanvas(this.w, this.h)
        const t = ctx2(this.clipTmp)
        t.globalCompositeOperation = 'source-over'; t.globalAlpha = 1; t.clearRect(0, 0, this.w, this.h)
        t.drawImage(src, 0, 0); t.globalCompositeOperation = 'destination-in'; t.drawImage(this.getCache(base), 0, 0)
        src = this.clipTmp
      }
      c.globalAlpha = L.opacity
      c.globalCompositeOperation = gco(L.blend)
      c.drawImage(src, 0, 0)
      c.globalAlpha = 1; c.globalCompositeOperation = 'source-over'
    }
  }
  ensureComp() {
    if (!this.compDirty) return this.comp
    const c = ctx2(this.comp)
    c.setTransform(1, 0, 0, 1, 0, 0); c.globalAlpha = 1; c.globalCompositeOperation = 'source-over'
    c.clearRect(0, 0, this.w, this.h)
    this.renderList(c, this.childrenOf(undefined))
    c.globalAlpha = 1; c.globalCompositeOperation = 'source-over'
    this.compDirty = false
    return this.comp
  }
  /** Flatten composite of all visible layers into a fresh canvas (optionally over a background colour). */
  flatten(bg?: string, scale = 1): HTMLCanvasElement {
    const comp = this.ensureComp()
    const cv = mkCanvas(this.w * scale, this.h * scale), c = ctx2(cv)
    if (bg) { c.fillStyle = bg; c.fillRect(0, 0, cv.width, cv.height) }
    c.imageSmoothingEnabled = true; c.imageSmoothingQuality = 'high'
    c.drawImage(comp, 0, 0, cv.width, cv.height)
    return cv
  }

  // ---------- property history ----------
  snap(L: Layer): PropSnap {
    return {
      name: L.name, visible: L.visible, locked: L.locked, opacity: L.opacity, blend: L.blend, clip: L.clip, editMask: L.editMask, pass: L.pass,
      t: { ...L.t }, adjust: clone(L.adjust), fx: clone(L.fx), text: L.text && clone(L.text), shape: L.shape && clone(L.shape), maskEnabled: L.mask?.enabled,
    }
  }
  applySnap(L: Layer, s: PropSnap) {
    L.name = s.name; L.visible = s.visible; L.locked = s.locked; L.opacity = s.opacity; L.blend = s.blend; L.clip = s.clip; L.editMask = s.editMask; L.pass = s.pass
    L.t = { ...s.t }; L.adjust = clone(s.adjust); L.fx = clone(s.fx)
    if (s.text) L.text = clone(s.text)
    if (s.shape) L.shape = clone(s.shape)
    if (L.mask && s.maskEnabled !== undefined) L.mask.enabled = s.maskEnabled
    if (L.type === 'text' || L.type === 'shape') this.refreshVector(L, false)
    this.touch(L)
  }
  /** Mutate layer props with undo. Same `key` within ~1s coalesces into one step (sliders). */
  mutate(L: Layer, name: string, fn: (L: Layer) => void, key?: string) {
    const before = this.snap(L)
    fn(L)
    if (L.type === 'text' || L.type === 'shape') this.refreshVector(L, true)
    const after = this.snap(L)
    this.touch(L)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const entry: any = {
      name, bytes: 400, key: key ? key + ':' + L.id : undefined, target: L,
      undo: () => { this.applySnap(L, entry.snapBefore) }, redo: () => { this.applySnap(L, entry.snapAfter) },
      snapBefore: before, snapAfter: after,
      merge(next: { snapAfter?: PropSnap }) { if (next.snapAfter) { entry.snapAfter = next.snapAfter; return true } return false },
    }
    this.history.push(entry as never)
    hooks.ui()
  }
  /** Push a history entry for props that were already changed live (drags). */
  commitSnap(L: Layer, before: PropSnap, name: string) {
    const after = this.snap(L)
    if (JSON.stringify(before) === JSON.stringify(after)) return
    this.history.push({ name, bytes: 400, undo: () => this.applySnap(L, before), redo: () => this.applySnap(L, after) })
    hooks.ui()
  }
  /** Add an image as a new layer; scaled down to fit the document when larger. */
  addImage(canvas: HTMLCanvasElement, name: string, at?: { x: number; y: number }, fit = true, mode: 'fit' | 'cover' = 'fit', index?: number, parent?: string | null) {
    const k = mode === 'cover' ? Math.max(this.w / canvas.width, this.h / canvas.height) : fit ? Math.min(1, this.w / canvas.width, this.h / canvas.height) : 1
    const L = makeLayer({ type: 'image', name, canvas })
    L.t = { cx: at?.x ?? this.w / 2, cy: at?.y ?? this.h / 2, sx: k, sy: k, rot: 0 }
    return this.addLayer(L, index, 'Place image', parent)
  }
  /** Re-render a text/shape layer's canvas from its props. */
  refreshVector(L: Layer, keepAnchor: boolean) {
    const ow = L.canvas.width, oh = L.canvas.height
    if (L.type === 'text' && L.text) {
      L.canvas = renderText(L.text)
      if (!isFontReady(L.text.font)) {
        const fam = L.text.font
        void ensureFont(fam).then(() => {
          if (this.layers.includes(L) && L.text?.font === fam && L.type === 'text') { this.refreshVector(L, true); this.touch(L, true); hooks.ui() }
        })
      }
    } else if (L.type === 'shape' && L.shape) L.canvas = renderShape(L.shape)
    else return
    L.px++
    if (keepAnchor) {
      const ax = L.type === 'text' ? (L.text!.align === 'left' ? -1 : L.text!.align === 'center' ? 0 : 1) : 0
      const va = L.type === 'text' ? -1 : 0
      const dx = (ax * (ow - L.canvas.width)) / 2 * L.t.sx, dy = (va * (oh - L.canvas.height)) / 2 * L.t.sy
      const c = Math.cos(L.t.rot), s = Math.sin(L.t.rot)
      L.t.cx += dx * c - dy * s; L.t.cy += dx * s + dy * c
    }
  }

  // ---------- selection ----------
  setSel(s: Sel | null, name = 'Selection') {
    const before = this.sel
    if (before === s) return
    this.sel = s
    this.history.push({ name, bytes: 64, undo: () => { this.sel = before; hooks.render(); hooks.ui() }, redo: () => { this.sel = s; hooks.render(); hooks.ui() } })
    hooks.render(); hooks.ui()
  }
  combineSel(b: Sel | null, mode: SelMode, name = 'Selection') {
    this.setSel(selCombine(this.sel, b, mode, this.w, this.h), name)
  }
  selectAll() { this.setSel(selFromMask(this.w, this.h, new Uint8ClampedArray(this.w * this.h).fill(255)), 'Select all') }
  deselect() { this.setSel(null, 'Deselect') }
  invertSel() {
    if (!this.sel) { this.selectAll(); return }
    this.setSel(selInvert(this.sel), 'Inverse selection')
  }
  selFromLayerAlpha(L: Layer) {
    this.setSel(selFromCanvas(this.getCache(L)), 'Load layer selection')
  }

  // ---------- layer tree (flat array + parent pointers; sibling order = array order of the sibling nodes) ----------
  childrenOf(id?: string): Layer[] { return this.layers.filter((l) => (l.parent ?? undefined) === id) }
  siblingsOf(L: Layer) { return this.childrenOf(L.parent) }
  descendants(L: Layer): Layer[] {
    const out: Layer[] = []
    const walk = (id: string) => { for (const c of this.childrenOf(id)) { out.push(c); if (c.type === 'group') walk(c.id) } }
    if (L.type === 'group') walk(L.id)
    return out
  }
  /** visible on canvas (self and all ancestors visible) */
  effVisible(L: Layer): boolean {
    for (let n: Layer | undefined = L, g = 0; n && g < 64; n = n.parent ? this.layer(n.parent) : undefined, g++) if (!n.visible) return false
    return true
  }
  depthOf(L: Layer) { let d = 0; for (let n = L.parent ? this.layer(L.parent) : undefined; n && d < 64; n = n.parent ? this.layer(n.parent) : undefined) d++; return d }
  /** panel rows, top first, honouring collapsed groups */
  rows(): { L: Layer; depth: number }[] {
    const out: { L: Layer; depth: number }[] = []
    const walk = (pid: string | undefined, depth: number) => {
      const list = this.childrenOf(pid)
      for (let i = list.length - 1; i >= 0; i--) {
        const L = list[i]
        out.push({ L, depth })
        if (L.type === 'group' && !L.collapsed) walk(L.id, depth + 1)
      }
    }
    walk(undefined, 0)
    return out
  }
  private snapStruct() { return { layers: [...this.layers], parents: new Map(this.layers.map((l) => [l, l.parent] as const)), active: this.activeId } }
  private applyStruct(s: ReturnType<Doc['snapStruct']>) {
    this.layers = [...s.layers]
    for (const [l, p] of s.parents) l.parent = p
    this.activeId = s.active
    this.multi.clear()
    this.invalidateAll(); hooks.render(); hooks.ui()
  }
  /** run a structural mutation (layers array / parent pointers / active) as one undo step */
  structChange(name: string, fn: () => void, bytes = 128) {
    const before = this.snapStruct()
    fn()
    const after = this.snapStruct()
    this.history.push({ name, bytes, undo: () => this.applyStruct(before), redo: () => this.applyStruct(after) })
    this.multi.clear()
    this.changed()
  }
  addLayer(L: Layer, index?: number, name = 'New layer', parent?: string | null) {
    const A = this.active
    let par: string | undefined, at: number
    if (parent !== undefined) par = parent ?? undefined
    else if (A && A.type === 'group' && index === undefined) par = A.id
    else par = A?.parent
    if (index !== undefined) at = Math.min(Math.max(0, index), this.layers.length)
    else if (A && par === A.id && A.type === 'group') at = this.layers.indexOf(A)
    else at = A ? this.layers.indexOf(A) + 1 : this.layers.length
    L.parent = par
    L.cacheOk = false
    this.structChange(name, () => { this.layers.splice(at, 0, L); this.activeId = L.id }, L.canvas.width * L.canvas.height * 4)
    return L
  }
  newRaster(name = 'Layer') {
    return this.addLayer(makeLayer({ type: 'raster', name, canvas: mkCanvas(this.w, this.h) }), undefined, 'New layer')
  }
  removeLayer(id: string, name = 'Delete layer') {
    const L = this.layer(id)
    if (!L) return
    const gone = new Set([L, ...this.descendants(L)])
    const i = this.layers.indexOf(L)
    this.structChange(name, () => {
      this.layers = this.layers.filter((x) => !gone.has(x))
      const sib = this.childrenOf(L.parent)
      this.activeId = (sib[sib.length - 1] ?? this.layers[Math.min(i, this.layers.length - 1)])?.id ?? ''
    }, [...gone].reduce((a, x) => a + x.canvas.width * x.canvas.height * 4, 0))
  }
  /** move a layer/group to `parent` at sibling position `index` (0 = bottom) */
  moveNode(id: string, parent: string | undefined, index: number) {
    const L = this.layer(id)
    if (!L) return
    if (parent) {
      const P = this.layer(parent)
      if (!P || P.type !== 'group' || P === L || this.descendants(L).includes(P)) return
    }
    this.structChange('Reorder layers', () => {
      L.parent = parent
      const target = this.childrenOf(parent).filter((x) => x !== L)
      target.splice(Math.min(Math.max(0, index), target.length), 0, L)
      const out: Layer[] = []
      const walk = (list: Layer[]) => { for (const n of list) { if (n.type === 'group') walk(n.id === parent ? target : this.childrenOf(n.id)); out.push(n) } }
      walk(parent === undefined ? target : this.childrenOf(undefined))
      this.layers = out
    }, 64)
  }
  /** shift a node among its siblings by delta (positive = up) */
  moveBy(id: string, delta: number) {
    const L = this.layer(id)
    if (!L) return
    const sib = this.siblingsOf(L)
    const i = sib.indexOf(L)
    const to = Math.min(Math.max(0, i + delta), sib.length - 1)
    if (to !== i) this.moveNode(id, L.parent, to)
  }
  cloneLayer(L: Layer, suffix = ' copy'): Layer {
    return makeLayer({
      type: L.type, name: L.name + suffix, canvas: cloneCanvas(L.canvas), visible: L.visible, locked: false, opacity: L.opacity, blend: L.blend,
      t: { ...L.t }, mask: L.mask ? { canvas: cloneCanvas(L.mask.canvas), enabled: L.mask.enabled } : null, clip: L.clip,
      adjust: clone(L.adjust).map((a: AdjustItem) => ({ ...a, id: uid() })), fx: clone(L.fx), text: L.text && clone(L.text), shape: L.shape && clone(L.shape),
      pass: L.pass, collapsed: L.collapsed, parent: L.parent,
    })
  }
  duplicate(id: string) {
    const L = this.layer(id)
    if (!L) return
    const nodes = [...this.descendants(L), L] // flat order: children first, header last
    const map = new Map<string, Layer>()
    const clones = nodes.map((n) => { const c = this.cloneLayer(n, n === L ? ' copy' : ''); map.set(n.id, c); return c })
    clones.forEach((c, i) => { const p = nodes[i].parent; c.parent = nodes[i] === L ? L.parent : p ? map.get(p)?.id : undefined })
    const at = this.layers.indexOf(L) + 1
    this.structChange('Duplicate layer', () => { this.layers.splice(at, 0, ...clones); this.activeId = clones[clones.length - 1].id }, nodes.length * this.w * this.h)
  }
  /** composite layer `id` onto the sibling below it (groups are flattened to a raster layer) */
  mergeDown(id: string) {
    const top = this.layer(id)
    if (!top) return
    if (top.type === 'group') { this.flattenGroup(top); return }
    const sib = this.siblingsOf(top)
    const si = sib.indexOf(top)
    if (si <= 0) { hooks.toast('There is no layer below to merge into'); return }
    const base = sib[si - 1]
    if (base.type === 'group') { hooks.toast('Cannot merge into a group - merge the group first'); return }
    const cv = mkCanvas(this.w, this.h), c = ctx2(cv)
    c.drawImage(this.getCache(base), 0, 0)
    c.globalAlpha = top.opacity; c.globalCompositeOperation = gco(top.blend)
    c.drawImage(this.getCache(top), 0, 0)
    const nl = makeLayer({ type: 'raster', name: base.name, canvas: cv, opacity: base.opacity, blend: base.blend, clip: base.clip })
    this.replaceNodes([base, top], nl, base.parent, 'Merge down')
  }
  flattenGroup(G: Layer) {
    if (G.type !== 'group') return
    const cv = cloneCanvas(this.getCache(G))
    const nl = makeLayer({ type: 'raster', name: G.name, canvas: cv, opacity: G.opacity, blend: G.pass === false ? G.blend : 'normal', clip: G.clip })
    this.replaceNodes([G, ...this.descendants(G)], nl, G.parent, 'Merge group')
  }
  flattenImage() {
    if (this.layers.length < 1) return
    this.replaceNodes([...this.layers], makeLayer({ type: 'raster', name: 'Background', canvas: this.flatten() }), undefined, 'Flatten image')
  }
  mergeVisible() {
    const vis = this.layers.filter((l) => l.type !== 'group' && this.effVisible(l))
    if (vis.length < 2) return
    const cv = mkCanvas(this.w, this.h)
    ctx2(cv).drawImage(this.ensureComp(), 0, 0)
    const old = new Set<Layer>(vis)
    for (const g of this.layers) if (g.type === 'group' && this.descendants(g).every((x) => old.has(x) || x.type === 'group')) old.add(g)
    this.replaceNodes([...old], makeLayer({ type: 'raster', name: 'Merged', canvas: cv }), undefined, 'Merge visible')
  }
  private replaceNodes(old: Layer[], nl: Layer, parent: string | undefined, name: string) {
    const gone = new Set(old)
    const top = Math.max(...old.map((l) => this.layers.indexOf(l)))
    nl.parent = parent
    this.structChange(name, () => {
      const removedBefore = this.layers.slice(0, top).filter((l) => gone.has(l)).length
      this.layers = this.layers.filter((l) => !gone.has(l))
      this.layers.splice(top - removedBefore, 0, nl)
      this.activeId = nl.id
    }, nl.canvas.width * nl.canvas.height * 4)
  }
  /** wrap the given sibling layers (or the multi-selection / active layer) in a new group */
  groupLayers(ids?: string[]) {
    const want = (ids && ids.length ? ids : this.multi.size ? [...this.multi] : [this.activeId]).map((i) => this.layer(i)).filter((l): l is Layer => !!l)
    if (!want.length) return null
    const parent = want[0].parent
    const sel = want.filter((l) => l.parent === parent)
    const G = makeLayer({ type: 'group', name: `Group ${this.layers.filter((l) => l.type === 'group').length + 1}`, canvas: mkCanvas(1, 1) })
    G.pass = true; G.parent = parent
    this.structChange('Group layers', () => {
      const maxIdx = Math.max(...sel.map((l) => this.layers.indexOf(l)))
      sel.forEach((l) => { l.parent = G.id })
      this.layers.splice(maxIdx + 1, 0, G)
      this.activeId = G.id
    })
    return G
  }
  ungroup(id: string) {
    const G = this.layer(id)
    if (!G || G.type !== 'group') return
    const kids = this.childrenOf(G.id)
    this.structChange('Ungroup layers', () => {
      this.layers = this.layers.filter((l) => l !== G)
      kids.forEach((k) => { k.parent = G.parent })
      this.activeId = kids[kids.length - 1]?.id ?? ''
    })
  }

  // ---------- rasterise / bake ----------
  private layerState(L: Layer): LayerState {
    return { L, canvas: L.canvas, t: { ...L.t }, type: L.type, text: L.text && clone(L.text), shape: L.shape && clone(L.shape), maskCv: L.mask?.canvas ?? null }
  }
  private restoreLayerState(s: LayerState) {
    const L = s.L
    L.canvas = s.canvas; L.t = { ...s.t }; L.type = s.type; L.text = s.text && clone(s.text); L.shape = s.shape && clone(s.shape)
    if (L.mask && s.maskCv) L.mask.canvas = s.maskCv
    L.px++; L.cacheOk = false; L.rev++
  }
  /** Render the layer (with transform) into a doc-sized identity raster canvas. Content only, no adjustments/effects. */
  bake(L: Layer) {
    if (L.type === 'group') return
    if (L.type === 'raster' && this.isIdentity(L)) return
    const before = this.layerState(L)
    const cv = mkCanvas(this.w, this.h), c = ctx2(cv), t = L.t
    c.translate(t.cx, t.cy); c.rotate(t.rot); c.scale(t.sx, t.sy)
    c.imageSmoothingQuality = 'high'
    c.imageSmoothingEnabled = !(Math.abs(t.sx) === 1 && Math.abs(t.sy) === 1 && t.rot === 0)
    c.drawImage(L.canvas, -L.canvas.width / 2, -L.canvas.height / 2)
    L.canvas = cv; L.t = { cx: this.w / 2, cy: this.h / 2, sx: 1, sy: 1, rot: 0 }; L.type = 'raster'; delete L.text; delete L.shape
    const after = this.layerState(L)
    this.touch(L, true)
    this.history.push({
      name: 'Rasterize layer', bytes: before.canvas.width * before.canvas.height * 4,
      undo: () => { this.restoreLayerState(before); this.compDirty = true; hooks.render(); hooks.ui() },
      redo: () => { this.restoreLayerState(after); this.compDirty = true; hooks.render(); hooks.ui() },
    })
    hooks.ui()
  }
  /** Flatten transform + adjustments + effects + mask into plain pixels. */
  bakeAll(L: Layer) {
    if (L.type === 'group') { this.flattenGroup(L); return }
    const before = this.layerState(L), bAdj = clone(L.adjust), bFx = clone(L.fx), bMask = L.mask, bEdit = L.editMask
    const out = cloneCanvas(this.getCache(L))
    L.canvas = out; L.t = { cx: this.w / 2, cy: this.h / 2, sx: 1, sy: 1, rot: 0 }; L.type = 'raster'; delete L.text; delete L.shape
    L.adjust = []; L.fx = defaultFx(); L.mask = null; L.editMask = false
    const after = this.layerState(L)
    this.touch(L, true)
    this.history.push({
      name: 'Merge adjustments into pixels', bytes: this.w * this.h * 8,
      undo: () => { this.restoreLayerState(before); L.adjust = clone(bAdj); L.fx = clone(bFx); L.mask = bMask; L.editMask = bEdit; this.compDirty = true; hooks.render(); hooks.ui() },
      redo: () => { this.restoreLayerState(after); L.adjust = []; L.fx = defaultFx(); L.mask = null; L.editMask = false; this.compDirty = true; hooks.render(); hooks.ui() },
    })
    hooks.ui()
  }
  /** Returns the canvas a paint op should draw into (doc-aligned), preparing the layer. null if impossible. */
  paintTarget(L: Layer | undefined, target: 'canvas' | 'mask'): HTMLCanvasElement | null {
    if (!L) { hooks.toast('No layer selected', 'error'); return null }
    if (L.locked) { hooks.toast('Layer is locked', 'error'); return null }
    if (!L.visible) { hooks.toast('Layer is hidden', 'error'); return null }
    if (L.type === 'group' && target === 'canvas') { hooks.toast('Groups cannot be painted on directly - select a layer inside the group', 'error'); return null }
    if (target === 'mask') {
      if (!L.mask) this.addMask(L, false)
      return L.mask!.canvas
    }
    if (!(L.type === 'raster' && this.isIdentity(L))) this.bake(L)
    return L.canvas
  }

  // ---------- masks ----------
  addMask(L: Layer, fromSelection: boolean) {
    if (L.mask) return
    const cv = mkCanvas(this.w, this.h), c = ctx2(cv)
    if (fromSelection && this.sel) c.drawImage(this.sel.canvas, 0, 0)
    else { c.fillStyle = '#000'; c.fillRect(0, 0, this.w, this.h) }
    const m = { canvas: cv, enabled: true }
    L.mask = m; L.editMask = true
    this.touch(L, true)
    this.history.push({
      name: 'Add layer mask', bytes: 64,
      undo: () => { L.mask = null; L.editMask = false; this.touch(L, true); hooks.ui() },
      redo: () => { L.mask = m; L.editMask = true; this.touch(L, true); hooks.ui() },
    })
    hooks.ui()
  }
  removeMask(L: Layer, apply: boolean) {
    const m = L.mask
    if (!m) return
    if (apply && L.type === 'group') { this.flattenGroup(L); return }
    this.history.group(apply ? 'Apply mask' : 'Delete mask', () => {
      if (apply) {
        this.bake(L)
        const e = new PixelEdit(this, L, 'canvas')
        const c = ctx2(L.canvas); c.globalCompositeOperation = 'destination-in'; c.drawImage(m.canvas, 0, 0); c.globalCompositeOperation = 'source-over'
        e.commit('Apply mask')
      }
      L.mask = null; L.editMask = false
      this.touch(L, true)
      this.history.push({
        name: 'Mask', bytes: 64,
        undo: () => { L.mask = m; this.touch(L, true); hooks.ui() },
        redo: () => { L.mask = null; L.editMask = false; this.touch(L, true); hooks.ui() },
      })
    })
    hooks.ui()
  }
  invertMask(L: Layer) {
    if (!L.mask) return
    const e = new PixelEdit(this, L, 'mask')
    const c = ctx2(L.mask.canvas, true), id = c.getImageData(0, 0, this.w, this.h), d = id.data
    for (let i = 3; i < d.length; i += 4) { d[i - 3] = d[i - 2] = d[i - 1] = 0; d[i] = 255 - d[i] }
    c.putImageData(id, 0, 0)
    e.commit('Invert mask')
    this.touch(L, true)
  }

  // ---------- doc-wide geometry ----------
  private capture(): { w: number; h: number; layers: LayerState[]; sel: Sel | null; active: string; order: Layer[] } {
    return { w: this.w, h: this.h, layers: this.layers.map((l) => this.layerState(l)), sel: this.sel, active: this.activeId, order: [...this.layers] }
  }
  private restore(s: ReturnType<Doc['capture']>) {
    this.setSize(s.w, s.h)
    this.layers = [...s.order]
    for (const st of s.layers) {
      this.restoreLayerState(st)
      if ((st.type === 'text' || st.type === 'shape') && st.L.type !== 'raster') this.refreshVector(st.L, false)
    }
    this.sel = s.sel; this.activeId = s.active
    this.invalidateAll(); this.view.zoom = 0
    hooks.render(); hooks.ui()
  }
  setSize(w: number, h: number) {
    this.w = w; this.h = h
    this.comp = mkCanvas(w, h); this.stroke = mkCanvas(w, h); this.clipTmp = null
    this.compDirty = true
  }
  /** Run a geometry transform over the whole document as one undo step. */
  transformDoc(name: string, nw: number, nh: number, fn: (L: Layer) => void, maskDraw: (c: CanvasRenderingContext2D, ow: number, oh: number) => void, mapSel?: (s: Sel) => Sel | null) {
    const before = this.capture()
    const ow = this.w, oh = this.h
    for (const L of this.layers) {
      fn(L)
      if (L.mask) {
        const mc = mkCanvas(nw, nh), c = ctx2(mc)
        c.save(); maskDraw(c, ow, oh); c.drawImage(L.mask.canvas, 0, 0); c.restore()
        L.mask.canvas = mc
      }
      if (L.type === 'text' || L.type === 'shape') this.refreshVector(L, false)
    }
    this.setSize(nw, nh)
    this.sel = this.sel && mapSel ? mapSel(this.sel) : null
    this.invalidateAll(); this.view.zoom = 0
    const after = this.capture()
    this.history.push({ name, bytes: this.layers.length * 256, undo: () => this.restore(before), redo: () => this.restore(after) })
    this.changed()
  }
  cropTo(x: number, y: number, w: number, h: number) {
    x = Math.round(x); y = Math.round(y); w = Math.max(1, Math.round(w)); h = Math.max(1, Math.round(h))
    this.transformDoc('Crop', w, h, (L) => { L.t.cx -= x; L.t.cy -= y }, (c) => c.translate(-x, -y))
  }
  /** anchor: 0..2 for x and y (0 = left/top, 1 = centre, 2 = right/bottom) */
  resizeCanvas(nw: number, nh: number, ax: number, ay: number) {
    const dx = Math.round(((nw - this.w) * ax) / 2), dy = Math.round(((nh - this.h) * ay) / 2)
    this.transformDoc('Canvas size', nw, nh, (L) => { L.t.cx += dx; L.t.cy += dy }, (c) => c.translate(dx, dy))
  }
  resizeImage(nw: number, nh: number) {
    const fx = nw / this.w, fy = nh / this.h
    this.history.group('Image size', () => {
    this.transformDoc('Image size', nw, nh, (L) => {
      L.t.cx *= fx; L.t.cy *= fy
      if (L.type === 'text' && L.text) { L.text.size *= (fx + fy) / 2; L.text.strokeWidth *= (fx + fy) / 2; L.t.sx *= 1; L.t.sy *= 1 }
      else if (L.type === 'shape' && L.shape) { L.shape.w *= fx; L.shape.h *= fy; L.shape.strokeWidth *= (fx + fy) / 2; L.shape.radius *= (fx + fy) / 2 }
      else if (L.type === 'raster' || L.type === 'image') { L.t.sx *= fx; L.t.sy *= fy }
      L.fx.shadow.blur *= fx; L.fx.shadow.dx *= fx; L.fx.shadow.dy *= fy; L.fx.stroke.width *= fx; L.fx.glow.size *= fx
    }, (c, ow, oh) => c.scale(nw / ow, nh / oh))
    // keep raster layers doc-sized & crisp
    for (const L of this.layers) if (L.type === 'raster' && !this.isIdentity(L)) this.bake(L)
    })
  }
  rotateCanvas(deg: 90 | -90 | 180) {
    const ow = this.w, oh = this.h, swap = deg !== 180
    const nw = swap ? oh : ow, nh = swap ? ow : oh, a = (deg * Math.PI) / 180
    this.transformDoc(deg === 180 ? 'Rotate 180°' : deg === 90 ? 'Rotate 90° clockwise' : 'Rotate 90° counter-clockwise', nw, nh, (L) => {
      const x = L.t.cx - ow / 2, y = L.t.cy - oh / 2, c = Math.cos(a), s = Math.sin(a)
      L.t.cx = nw / 2 + x * c - y * s; L.t.cy = nh / 2 + x * s + y * c; L.t.rot += a
    }, (c) => { c.translate(nw / 2, nh / 2); c.rotate(a); c.translate(-ow / 2, -oh / 2) })
  }
  flipCanvas(horizontal: boolean) {
    const w = this.w, h = this.h
    this.transformDoc(horizontal ? 'Flip canvas horizontally' : 'Flip canvas vertically', w, h, (L) => {
      if (horizontal) { L.t.cx = w - L.t.cx; L.t.sx = -L.t.sx; L.t.rot = -L.t.rot }
      else { L.t.cy = h - L.t.cy; L.t.sx = -L.t.sx; L.t.rot = Math.PI - L.t.rot }
    }, (c) => { if (horizontal) { c.translate(w, 0); c.scale(-1, 1) } else { c.translate(0, h); c.scale(1, -1) } })
  }

  // ---------- history ----------
  undo() { this.afterHistory(this.history.undo()) }
  redo() { this.afterHistory(this.history.redo()) }
  afterHistory(ok: boolean) {
    if (!ok) return
    if (!this.layers.some((l) => l.id === this.activeId)) this.activeId = this.layers[this.layers.length - 1]?.id ?? ''
    this.invalidateAll(); this.revision++
    hooks.render(); hooks.ui()
  }
}

/** Snapshot-based pixel edit: remembers pre-edit pixels so the change can be undone (rect patches) or previewed/restored. */
export class PixelEdit {
  doc: Doc
  layer: Layer
  target: 'canvas' | 'mask'
  base: HTMLCanvasElement
  constructor(doc: Doc, layer: Layer, target: 'canvas' | 'mask') {
    this.doc = doc; this.layer = layer; this.target = target
    this.base = cloneCanvas(this.cv)
  }
  get cv(): HTMLCanvasElement { return this.target === 'mask' ? this.layer.mask!.canvas : this.layer.canvas }
  restore(r?: Rect) {
    const c = ctx2(this.cv)
    c.save(); c.setTransform(1, 0, 0, 1, 0, 0); c.globalCompositeOperation = 'copy'; c.globalAlpha = 1; c.filter = 'none'
    if (r) { c.beginPath(); c.rect(r.x, r.y, r.w, r.h); c.clip(); c.clearRect(r.x, r.y, r.w, r.h); c.globalCompositeOperation = 'source-over'; c.drawImage(this.base, 0, 0) }
    else c.drawImage(this.base, 0, 0)
    c.restore()
  }
  commit(name: string, rect?: Rect | null) {
    const cvw = this.base.width, cvh = this.base.height
    const r = rect ? clipRect(rect, cvw, cvh) : { x: 0, y: 0, w: cvw, h: cvh }
    if (r.w <= 0 || r.h <= 0) return
    const before = ctx2(this.base, true).getImageData(r.x, r.y, r.w, r.h)
    const after = ctx2(this.cv, true).getImageData(r.x, r.y, r.w, r.h)
    const { layer, target, doc } = this
    const apply = (img: ImageData) => {
      const cv = target === 'mask' ? layer.mask?.canvas : layer.canvas
      if (!cv) return
      ctx2(cv).putImageData(img, r.x, r.y)
      doc.touch(layer, true)
      hooks.ui()
    }
    doc.history.push({ name, bytes: r.w * r.h * 8, undo: () => apply(before), redo: () => apply(after) })
    doc.touch(layer, true)
    doc.revision++
    hooks.ui()
  }
}
