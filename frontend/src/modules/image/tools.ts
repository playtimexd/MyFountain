// Tool implementations. Each tool receives pointer events in document coordinates.
import type { Doc, PropSnap } from './doc'
import { hooks, makeLayer, PixelEdit } from './doc'
import { Brush, DirectStroke, inpaintDiffuse, paintOnce, PaintSession, setPreviewStroke, tipCanvas } from './paint'
import { defaultShape, defaultText } from './render'
import { floodMask, selCombine, selFeather, selFromCanvas, selFromMask } from './selection'
import { useUI } from './store'
import { startTextEdit } from './TextEditor'
import type { Layer, Rect, Sel, SelMode, ToolId } from './types'
import { clamp, clipRect, ctx2, luminance, mkCanvas, parseColor, toHex } from './util'
import { fitView, toScreen, zoomAt } from './view'
import { snapMove, snapPt } from './guides'

export interface PtrEv { x: number; y: number; sx: number; sy: number; shift: boolean; alt: boolean; ctrl: boolean; pressure: number; button: number }
export interface Tool {
  down?(d: Doc, e: PtrEv): void
  move?(d: Doc, e: PtrEv): void
  up?(d: Doc, e: PtrEv): void
  hover?(d: Doc, e: PtrEv): string | void
  overlay?(c: CanvasRenderingContext2D, d: Doc): void
  key?(d: Doc, e: KeyboardEvent): boolean
  cancel?(d: Doc): void
  cursor: string
}

const ui = () => useUI.getState()
const O = () => useUI.getState().opts
export let liftOffset: { dx: number; dy: number } | null = null
export const toolState = { cloneSrc: null as { x: number; y: number } | null, cloneOffset: null as { x: number; y: number } | null, lastBrushEnd: null as { x: number; y: number; doc: string } | null }

// ---------- shared helpers ----------
function modeFor(e: PtrEv): SelMode {
  const m = O().selMode
  if (m !== 'replace') return m
  return e.shift && e.alt ? 'intersect' : e.shift ? 'add' : e.alt ? 'subtract' : 'replace'
}
function commitShapeSel(d: Doc, draw: (c: CanvasRenderingContext2D) => void, mode: SelMode, name: string) {
  const cv = mkCanvas(d.w, d.h), c = ctx2(cv)
  c.fillStyle = '#000'
  draw(c)
  let s: Sel | null = selFromCanvas(cv)
  if (s && O().feather > 0) s = selFeather(s, O().feather)
  d.setSel(selCombine(d.sel, s, mode, d.w, d.h), name)
}
function dragRect(a: { x: number; y: number }, b: { x: number; y: number }, square: boolean, center: boolean, ratio?: number | null): Rect {
  let dx = b.x - a.x, dy = b.y - a.y
  if (ratio) { const w = Math.abs(dx); dy = Math.sign(dy || 1) * (w / ratio) }
  else if (square) { const m = Math.max(Math.abs(dx), Math.abs(dy)); dx = Math.sign(dx || 1) * m; dy = Math.sign(dy || 1) * m }
  if (center) return { x: Math.round(a.x - Math.abs(dx)), y: Math.round(a.y - Math.abs(dy)), w: Math.round(Math.abs(dx) * 2), h: Math.round(Math.abs(dy) * 2) }
  const x0 = Math.round(Math.min(a.x, a.x + dx)), y0 = Math.round(Math.min(a.y, a.y + dy))
  return { x: x0, y: y0, w: Math.round(Math.abs(dx)), h: Math.round(Math.abs(dy)) }
}
function antsOutline(c: CanvasRenderingContext2D, path: () => void, dash = true) {
  c.save()
  c.lineWidth = 1
  c.strokeStyle = '#000'; c.setLineDash([]); c.beginPath(); path(); c.stroke()
  if (dash) { c.strokeStyle = '#fff'; c.setLineDash([4, 4]); c.beginPath(); path(); c.stroke() }
  c.restore()
}
export function getSampleData(d: Doc, L: Layer | undefined, all: boolean): ImageData {
  const src = all || !L ? d.ensureComp() : d.getCache(L)
  return ctx2(src, true).getImageData(0, 0, d.w, d.h)
}
export function samplePixel(d: Doc, x: number, y: number, all: boolean) { return pickPx(d, x, y, all) }
function pickPx(d: Doc, x: number, y: number, all: boolean) {
  const L = d.active
  const src = all || !L ? d.ensureComp() : d.getCache(L)
  const xi = Math.floor(x), yi = Math.floor(y)
  if (xi < 0 || yi < 0 || xi >= d.w || yi >= d.h) return null
  const p = ctx2(src, true).getImageData(xi, yi, 1, 1).data
  return { r: p[0], g: p[1], b: p[2], a: p[3] / 255 }
}

// layer hit testing --------------------------------------------------------
function localPt(L: Layer, x: number, y: number): [number, number] {
  const dx = x - L.t.cx, dy = y - L.t.cy, c = Math.cos(-L.t.rot), s = Math.sin(-L.t.rot)
  return [dx * c - dy * s, dx * s + dy * c]
}
function hitLayer(L: Layer, x: number, y: number, alpha: boolean): boolean {
  const [lx, ly] = localPt(L, x, y)
  const u = lx / L.t.sx + L.canvas.width / 2, v = ly / L.t.sy + L.canvas.height / 2
  if (u < 0 || v < 0 || u >= L.canvas.width || v >= L.canvas.height) return false
  if (!alpha) return true
  return ctx2(L.canvas, true).getImageData(Math.floor(u), Math.floor(v), 1, 1).data[3] > 8
}
function pickLayer(d: Doc, x: number, y: number, alpha = true): Layer | null {
  for (let i = d.layers.length - 1; i >= 0; i--) {
    const L = d.layers[i]
    if (L.type === 'group' || !d.effVisible(L)) continue
    if (hitLayer(L, x, y, alpha && L.type !== 'text')) return L
  }
  return null
}

// ======================================================================= MOVE
type Drag =
  | { mode: 'move'; L: Layer; sx: number; sy: number; t0: Layer['t']; snap: PropSnap; b0: Rect }
  | { mode: 'scale'; L: Layer; h: [number, number]; t0: Layer['t']; snap: PropSnap }
  | { mode: 'rotate'; L: Layer; a0: number; t0: Layer['t']; snap: PropSnap }
  | { mode: 'gmove'; L: Layer; sx: number; sy: number; b0: Rect; items: { l: Layer; t0: Layer['t']; snap: PropSnap }[] }
  | { mode: 'lift'; L: Layer; sx: number; sy: number; t0: Layer['t']; sel0: Sel; base: Sel }
let drag: Drag | null = null
const HANDLES: [number, number][] = [[-1, -1], [0, -1], [1, -1], [1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0]]
function extents(L: Layer) { return [Math.abs(L.canvas.width * L.t.sx) / 2, Math.abs(L.canvas.height * L.t.sy) / 2] }
function layerPoint(L: Layer, lx: number, ly: number): [number, number] {
  const c = Math.cos(L.t.rot), s = Math.sin(L.t.rot)
  return [L.t.cx + lx * c - ly * s, L.t.cy + lx * s + ly * c]
}
function handleAt(d: Doc, L: Layer, sx: number, sy: number): { kind: 'scale' | 'rotate' | 'inside' | null; h?: [number, number] } {
  if (L.type === 'group') return { kind: null }
  const [a, b] = extents(L)
  for (const h of HANDLES) {
    const [px, py] = toScreen(d, ...layerPoint(L, h[0] * a, h[1] * b))
    if (Math.hypot(px - sx, py - sy) <= 8) return { kind: 'scale', h }
  }
  for (const h of HANDLES.filter((k) => k[0] && k[1])) {
    const [px, py] = toScreen(d, ...layerPoint(L, h[0] * a, h[1] * b))
    const dd = Math.hypot(px - sx, py - sy)
    if (dd > 8 && dd <= 26) {
      const [dx, dy] = [sx - px, sy - py]
      // outside the box direction
      const [cxs, cys] = toScreen(d, L.t.cx, L.t.cy)
      if (dx * (px - cxs) + dy * (py - cys) > 0) return { kind: 'rotate' }
    }
  }
  const [lx, ly] = localPt(L, ...([(sx - d.view.ox) / d.view.zoom, (sy - d.view.oy) / d.view.zoom] as [number, number]))
  if (Math.abs(lx) <= a && Math.abs(ly) <= b) return { kind: 'inside' }
  return { kind: null }
}
const resizeCursor = (h: [number, number], rot: number) => {
  const ang = Math.atan2(h[1], h[0]) + rot
  const k = ((Math.round(ang / (Math.PI / 4)) % 4) + 4) % 4
  return ['ew-resize', 'nwse-resize', 'ns-resize', 'nesw-resize'][k]
}

function liftSelection(d: Doc, L: Layer, copy: boolean): Layer | null {
  const sel = d.sel!
  const cv = d.paintTarget(L, 'canvas')
  if (!cv) return null
  const b = sel.bbox
  const fl = mkCanvas(b.w, b.h), c = ctx2(fl)
  c.drawImage(L.canvas, b.x, b.y, b.w, b.h, 0, 0, b.w, b.h)
  c.globalCompositeOperation = 'destination-in'; c.drawImage(sel.canvas, b.x, b.y, b.w, b.h, 0, 0, b.w, b.h)
  if (!copy) {
    const e = new PixelEdit(d, L, 'canvas')
    const lc = ctx2(L.canvas); lc.globalCompositeOperation = 'destination-out'; lc.drawImage(sel.canvas, 0, 0); lc.globalCompositeOperation = 'source-over'
    e.commit('Move selection', b)
  }
  const nl = makeLayer({ type: 'image', name: 'Floating selection', canvas: fl })
  nl.t = { cx: b.x + b.w / 2, cy: b.y + b.h / 2, sx: 1, sy: 1, rot: 0 }
  d.addLayer(nl, d.layers.indexOf(L) + 1, 'Move selection')
  return nl
}

const moveTool: Tool = {
  cursor: 'default',
  down(d, e) {
    let L = d.active
    const o = O()
    if (L && L.type === 'group' && !e.ctrl) {
      const b = d.layerBounds(L)
      if (e.x >= b.x && e.x <= b.x + b.w && e.y >= b.y && e.y <= b.y + b.h) {
        if (L.locked) { hooks.toast('Layer is locked', 'error'); return }
        const items = d.descendants(L).filter((x) => x.type !== 'group' && !x.locked).map((l) => ({ l, t0: { ...l.t }, snap: d.snap(l) }))
        drag = { mode: 'gmove', L, sx: e.x, sy: e.y, items, b0: b }
        return
      }
    }
    const hit = L && L.visible ? handleAt(d, L, e.sx, e.sy) : { kind: null as null }
    if (L && !L.locked && hit.kind === 'scale' && hit.h) { drag = { mode: 'scale', L, h: hit.h, t0: { ...L.t }, snap: d.snap(L) }; return }
    if (L && !L.locked && hit.kind === 'rotate') {
      drag = { mode: 'rotate', L, a0: Math.atan2(e.y - L.t.cy, e.x - L.t.cx), t0: { ...L.t }, snap: d.snap(L) }; return
    }
    if (!(L && hit.kind === 'inside') && o.autoSelect && !e.ctrl && !d.sel) {
      const p = pickLayer(d, e.x, e.y)
      if (p) { d.activeId = p.id; L = p; hooks.ui() }
    }
    if (!L) return
    if (L.locked) { hooks.toast('Layer is locked', 'error'); return }
    if (d.sel && !e.ctrl && (L.type === 'raster' || L.type === 'image') && L.visible) {
      d.history.begin('Move selection')
      const nl = liftSelection(d, L, e.alt)
      if (!nl) { d.history.end(); return }
      drag = { mode: 'lift', L: nl, sx: e.x, sy: e.y, t0: { ...nl.t }, sel0: d.sel, base: d.sel }
      liftOffset = { dx: 0, dy: 0 }
      return
    }
    drag = { mode: 'move', L, sx: e.x, sy: e.y, t0: { ...L.t }, snap: d.snap(L), b0: d.layerBounds(L) }
  },
  move(d, e) {
    if (!drag) return
    const L = drag.L
    if (drag.mode === 'gmove') {
      let dx = Math.round(e.x - drag.sx), dy = Math.round(e.y - drag.sy)
      if (e.shift) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0 }
      if (!e.alt) ({ dx, dy } = snapMove(d, drag.b0, dx, dy))
      for (const it of drag.items) { it.l.t.cx = it.t0.cx + dx; it.l.t.cy = it.t0.cy + dy; d.touch(it.l) }
      return
    }
    if (drag.mode === 'move' || drag.mode === 'lift') {
      let dx = Math.round(e.x - drag.sx), dy = Math.round(e.y - drag.sy)
      if (e.shift) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0 }
      if (drag.mode === 'move' && !e.alt) ({ dx, dy } = snapMove(d, drag.b0, dx, dy))
      L.t.cx = drag.t0.cx + dx; L.t.cy = drag.t0.cy + dy
      if (drag.mode === 'lift') liftOffset = { dx, dy }
    } else if (drag.mode === 'rotate') {
      let r = drag.t0.rot + Math.atan2(e.y - L.t.cy, e.x - L.t.cx) - drag.a0
      if (e.shift) r = Math.round(r / (Math.PI / 12)) * (Math.PI / 12)
      L.t.rot = r
    } else {
      const t0 = drag.t0
      const a = Math.abs(L.canvas.width * t0.sx) / 2, b = Math.abs(L.canvas.height * t0.sy) / 2
      const rot = t0.rot, c0 = Math.cos(-rot), s0 = Math.sin(-rot)
      const px = (e.x - t0.cx) * c0 - (e.y - t0.cy) * s0, py = (e.x - t0.cx) * s0 + (e.y - t0.cy) * c0
      const [hx, hy] = drag.h
      const Ax = e.alt ? 0 : -hx * a, Ay = e.alt ? 0 : -hy * b
      let kx = hx ? (px - Ax) / (hx * a - Ax) : 1, ky = hy ? (py - Ay) / (hy * b - Ay) : 1
      const uniform = e.shift || ((L.type === 'text') && hx && hy)
      if (uniform) {
        if (hx && hy) { const k = Math.max(Math.abs(kx), Math.abs(ky)); kx = Math.sign(kx || 1) * k; ky = Math.sign(ky || 1) * k }
        else if (hx) ky = kx
        else kx = ky
      }
      if (L.type === 'text') { kx = Math.abs(kx); ky = Math.abs(ky) }
      kx = Math.abs(kx) < 0.01 ? 0.01 * Math.sign(kx || 1) : kx
      ky = Math.abs(ky) < 0.01 ? 0.01 * Math.sign(ky || 1) : ky
      L.t.sx = t0.sx * kx; L.t.sy = t0.sy * ky
      const lx = Ax * (1 - kx), ly = Ay * (1 - ky), cs = Math.cos(rot), sn = Math.sin(rot)
      L.t.cx = t0.cx + lx * cs - ly * sn; L.t.cy = t0.cy + lx * sn + ly * cs
    }
    d.touch(L)
  },
  up(d) {
    if (!drag) return
    const dr = drag
    drag = null
    const L = dr.L
    if (dr.mode === 'gmove') {
      d.history.group('Move group', () => { for (const it of dr.items) d.commitSnap(it.l, it.snap, 'Move layer') })
      d.changed()
      return
    }
    if (dr.mode === 'lift') {
      const off = liftOffset
      liftOffset = null
      if (off && (off.dx || off.dy)) {
        import('./selection').then(({ selShift }) => { d.setSel(selShift(dr.base, off.dx, off.dy), 'Move selection'); d.history.end(); d.changed() })
      } else { d.history.end(); d.changed() }
      return
    }
    // bake scale into vector props so text/shapes stay crisp
    if (dr.mode === 'scale') {
      if (L.type === 'shape' && L.shape) {
        L.shape.w = Math.max(1, L.shape.w * Math.abs(L.t.sx) / 1); L.shape.h = Math.max(1, L.shape.h * Math.abs(L.t.sy))
        L.t.sx = Math.sign(L.t.sx) || 1; L.t.sy = Math.sign(L.t.sy) || 1
        d.refreshVector(L, false)
      } else if (L.type === 'text' && L.text) {
        L.text.size = Math.max(4, L.text.size * Math.abs(L.t.sy)); L.text.strokeWidth *= Math.abs(L.t.sy)
        L.t.sx = Math.sign(L.t.sx) || 1; L.t.sy = Math.sign(L.t.sy) || 1
        d.refreshVector(L, false)
      }
    }
    d.touch(L, true)
    d.commitSnap(L, dr.snap, dr.mode === 'move' ? 'Move layer' : dr.mode === 'scale' ? 'Scale layer' : 'Rotate layer')
    d.changed()
  },
  hover(d, e) {
    const L = d.active
    if (!L || !L.visible) return 'default'
    if (L.type === 'group') return 'move'
    const h = handleAt(d, L, e.sx, e.sy)
    if (h.kind === 'scale' && h.h) return resizeCursor(h.h, L.t.rot)
    if (h.kind === 'rotate') return 'alias'
    return 'default'
  },
  overlay(c, d) {
    const L = d.active
    if (!L || !L.visible || drag?.mode === 'lift') return
    if (L.type === 'group') {
      const b = d.layerBounds(L)
      if (b.w <= 0) return
      const [x0, y0] = toScreen(d, b.x, b.y), [x1, y1] = toScreen(d, b.x + b.w, b.y + b.h)
      c.save(); c.strokeStyle = '#5b8cff'; c.setLineDash([5, 3]); c.lineWidth = 1; c.strokeRect(x0 + 0.5, y0 + 0.5, x1 - x0, y1 - y0); c.restore()
      return
    }
    const [a, b] = extents(L)
    const pts = [[-a, -b], [a, -b], [a, b], [-a, b]].map(([x, y]) => toScreen(d, ...layerPoint(L, x, y)))
    c.save()
    c.lineWidth = 1; c.strokeStyle = '#5b8cff'
    c.beginPath(); pts.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y))); c.closePath(); c.stroke()
    if (!L.locked) {
      c.fillStyle = '#fff'; c.strokeStyle = '#3b6fd6'
      for (const h of HANDLES) {
        const [x, y] = toScreen(d, ...layerPoint(L, h[0] * a, h[1] * b))
        c.fillRect(x - 4, y - 4, 8, 8); c.strokeRect(x - 4.5, y - 4.5, 9, 9)
      }
    }
    c.restore()
  },
  key(d, e) {
    const L = d.active
    const step = e.shiftKey ? 10 : 1
    const dir: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }
    const v = dir[e.key]
    if (!v || !L) return false
    if (L.locked) { hooks.toast('Layer is locked', 'error'); return true }
    if (L.type === 'group') {
      d.history.group('Move group', () => { for (const x of d.descendants(L)) if (x.type !== 'group' && !x.locked) d.mutate(x, 'Move layer', (l) => { l.t.cx += v[0] * step; l.t.cy += v[1] * step }) })
      return true
    }
    d.mutate(L, 'Move layer', (l) => { l.t.cx += v[0] * step; l.t.cy += v[1] * step }, 'nudge')
    return true
  },
  cancel() { drag = null; liftOffset = null },
}

// ================================================================== MARQUEE
let mq: { a: { x: number; y: number }; cur: { x: number; y: number }; mode: SelMode; shiftAtDown: boolean; altAtDown: boolean; shiftFree: boolean; altFree: boolean; r: Rect } | null = null
const marqueeTool: Tool = {
  cursor: 'crosshair',
  down(d, e) {
    const sp0 = snapPt(d, e.x, e.y)
    mq = { a: { x: sp0.x, y: sp0.y }, cur: { x: sp0.x, y: sp0.y }, mode: modeFor(e), shiftAtDown: e.shift, altAtDown: e.alt, shiftFree: !e.shift, altFree: !e.alt, r: { x: 0, y: 0, w: 0, h: 0 } }
    void d
  },
  move(d, e0) {
    if (!mq) return
    const e = { ...e0, ...snapPt(d, e0.x, e0.y) }
    if (!e.shift) mq.shiftFree = true
    if (!e.alt) mq.altFree = true
    const o = O()
    mq.cur = { x: e.x, y: e.y }
    const square = e.shift && mq.shiftFree, center = e.alt && mq.altFree
    if (o.marqueeStyle === 'size') mq.r = { x: Math.round(e.x), y: Math.round(e.y), w: o.mqW, h: o.mqH }
    else mq.r = dragRect(mq.a, mq.cur, square, center, o.marqueeStyle === 'ratio' ? o.mqW / Math.max(1, o.mqH) : null)
    hooks.render()
  },
  up(d, e) {
    if (!mq) return
    const m = mq
    mq = null
    const r = m.r
    const o = O()
    if (o.marqueeStyle !== 'size' && (r.w < 2 || r.h < 2) && Math.hypot(e.x - m.a.x, e.y - m.a.y) < 3) {
      if (m.mode === 'replace') d.deselect()
      hooks.render(); return
    }
    if (r.w < 1 || r.h < 1) { hooks.render(); return }
    commitShapeSel(d, (c) => {
      c.beginPath()
      if (o.marquee === 'ellipse') c.ellipse(r.x + r.w / 2, r.y + r.h / 2, r.w / 2, r.h / 2, 0, 0, Math.PI * 2)
      else c.rect(r.x, r.y, r.w, r.h)
      c.fill()
    }, m.mode, o.marquee === 'ellipse' ? 'Elliptical marquee' : 'Rectangular marquee')
  },
  overlay(c, d) {
    if (!mq || mq.r.w < 1) return
    const r = mq.r, [x0, y0] = toScreen(d, r.x, r.y), [x1, y1] = toScreen(d, r.x + r.w, r.y + r.h)
    antsOutline(c, () => { if (O().marquee === 'ellipse') c.ellipse((x0 + x1) / 2, (y0 + y1) / 2, Math.abs(x1 - x0) / 2, Math.abs(y1 - y0) / 2, 0, 0, Math.PI * 2); else c.rect(x0, y0, x1 - x0, y1 - y0) })
  },
  cancel() { mq = null },
}

// ===================================================================== LASSO
let lasso: { pts: [number, number][]; mode: SelMode; poly: boolean; hover: [number, number] | null; lastDown: number; drawing: boolean } | null = null
function finishLasso(d: Doc) {
  if (!lasso) return
  const l = lasso
  lasso = null
  if (l.pts.length < 3) { hooks.render(); return }
  commitShapeSel(d, (c) => { c.beginPath(); l.pts.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y))); c.closePath(); c.fill() }, l.mode, l.poly ? 'Polygonal lasso' : 'Lasso')
}
const lassoTool: Tool = {
  cursor: 'crosshair',
  down(d, e) {
    const polyMode = O().lasso === 'poly' || e.alt
    if (lasso?.poly && polyMode) {
      const first = lasso.pts[0]
      const near = Math.hypot(toScreen(d, ...first)[0] - e.sx, toScreen(d, ...first)[1] - e.sy) < 8
      if (near || performance.now() - lasso.lastDown < 300) { finishLasso(d); return }
      lasso.pts.push([e.x, e.y]); lasso.lastDown = performance.now(); hooks.render(); return
    }
    lasso = { pts: [[e.x, e.y]], mode: modeFor(e), poly: polyMode, hover: null, lastDown: performance.now(), drawing: true }
  },
  move(_d, e) {
    if (!lasso) return
    if (!lasso.poly && lasso.drawing) { const p = lasso.pts[lasso.pts.length - 1]; if (Math.hypot(e.x - p[0], e.y - p[1]) > 0.7) lasso.pts.push([e.x, e.y]) }
    lasso.hover = [e.x, e.y]; hooks.render()
  },
  hover(_d, e) { if (lasso?.poly) { lasso.hover = [e.x, e.y]; hooks.render() } },
  up(d) {
    if (!lasso) return
    lasso.drawing = false
    if (!lasso.poly) finishLasso(d)
  },
  overlay(c, d) {
    if (!lasso) return
    const l = lasso
    antsOutline(c, () => {
      l.pts.forEach(([x, y], i) => { const [sx, sy] = toScreen(d, x, y); if (i) c.lineTo(sx, sy); else c.moveTo(sx, sy) })
      if (l.poly && l.hover) c.lineTo(...toScreen(d, l.hover[0], l.hover[1]))
    })
  },
  key(d, e) {
    if (!lasso) return false
    if (e.key === 'Enter') { finishLasso(d); return true }
    if (e.key === 'Escape') { lasso = null; hooks.render(); return true }
    if (e.key === 'Backspace') { lasso.pts.pop(); hooks.render(); return true }
    return false
  },
  cancel() { lasso = null },
}

// ================================================================== WAND
const wandTool: Tool = {
  cursor: 'crosshair',
  down(d, e) {
    const o = O()
    const img = getSampleData(d, d.active, o.sampleAll)
    const mask = floodMask(img, Math.floor(e.x), Math.floor(e.y), o.tolerance, o.contiguous)
    const cv = mkCanvas(d.w, d.h), c = ctx2(cv)
    const id = new ImageData(d.w, d.h)
    for (let i = 0; i < mask.length; i++) id.data[i * 4 + 3] = mask[i]
    c.putImageData(id, 0, 0)
    let s = selFromCanvas(cv)
    if (s && o.feather > 0) s = selFeather(s, o.feather)
    d.setSel(selCombine(d.sel, s, modeFor(e), d.w, d.h), 'Magic wand')
  },
}

// ============================================================== QUICK SELECTION
let qs: { img: ImageData; mask: Uint8ClampedArray; base: Sel | null; mode: SelMode; last: { x: number; y: number } | null; lastApply: number } | null = null
function qsApply(d: Doc, force: boolean) {
  if (!qs) return
  const now = performance.now()
  if (!force && now - qs.lastApply < 90) return
  qs.lastApply = now
  d.sel = selFromMask(d.w, d.h, new Uint8ClampedArray(qs.mask))
  hooks.render()
}
function qsPaint(d: Doc, x: number, y: number) {
  if (!qs) return
  const o = O(), r = Math.max(4, o.brushSize / 2) * 1.5
  const x0 = Math.max(0, Math.floor(x - r)), y0 = Math.max(0, Math.floor(y - r)), x1 = Math.min(d.w, Math.ceil(x + r)), y1 = Math.min(d.h, Math.ceil(y + r))
  const w = x1 - x0, h = y1 - y0
  if (w < 1 || h < 1) return
  const sub = new ImageData(w, h)
  for (let yy = 0; yy < h; yy++) sub.data.set(qs.img.data.subarray(((yy + y0) * d.w + x0) * 4, ((yy + y0) * d.w + x1) * 4), yy * w * 4)
  const m = floodMask(sub, Math.floor(x) - x0, Math.floor(y) - y0, o.tolerance, true)
  for (let yy = 0; yy < h; yy++) for (let xx = 0; xx < w; xx++) {
    if (!m[yy * w + xx]) continue
    if (Math.hypot(xx + x0 + 0.5 - x, yy + y0 + 0.5 - y) > r) continue
    qs.mask[(yy + y0) * d.w + xx + x0] = qs.mode === 'subtract' ? 0 : 255
  }
}
function qsStep(d: Doc, x: number, y: number) {
  if (!qs) return
  const r = Math.max(4, O().brushSize / 2), l = qs.last
  if (l) {
    const dist = Math.hypot(x - l.x, y - l.y), n = Math.max(1, Math.ceil(dist / (r * 0.6)))
    for (let i = 1; i <= n; i++) qsPaint(d, l.x + ((x - l.x) * i) / n, l.y + ((y - l.y) * i) / n)
  } else qsPaint(d, x, y)
  qs.last = { x, y }
  qsApply(d, false)
}
const quickSelTool: Tool = {
  cursor: 'none',
  down(d, e) {
    const o = O()
    const mode = e.alt ? 'subtract' : o.selMode === 'subtract' ? 'subtract' : e.shift || o.selMode === 'add' ? 'add' : 'replace'
    qs = { img: getSampleData(d, d.active, o.sampleAll), mask: mode === 'replace' || !d.sel ? new Uint8ClampedArray(d.w * d.h) : new Uint8ClampedArray(d.sel.mask), base: d.sel, mode: mode === 'replace' ? 'add' : mode, last: null, lastApply: 0 }
    qsStep(d, e.x, e.y)
  },
  move(d, e) { qsStep(d, e.x, e.y) },
  up(d) {
    if (!qs) return
    qsApply(d, true)
    const final = d.sel, base = qs.base
    qs = null
    d.sel = base
    d.setSel(final, 'Quick selection')
  },
  cancel(d) { if (qs) { d.sel = qs.base; qs = null; hooks.render() } },
}

// ==================================================================== CROP
let crop: { doc: string; r: Rect } | null = null
let cropDrag: { h: string; a: { x: number; y: number }; r0: Rect } | null = null
export const getCrop = () => crop
function cropRatio(d: Doc): number | null {
  const o = O(), v = o.cropRatio
  if (v === 'free') return null
  if (v === 'orig') return d.w / d.h
  if (v === 'custom') return o.cropW / Math.max(1, o.cropH)
  const [a, b] = v.split(':').map(Number)
  return a / b
}
function ensureCrop(d: Doc) {
  if (!crop || crop.doc !== d.id) crop = { doc: d.id, r: { x: 0, y: 0, w: d.w, h: d.h } }
  return crop
}
export function resetCrop(d: Doc) { crop = { doc: d.id, r: { x: 0, y: 0, w: d.w, h: d.h } }; hooks.render() }
export function applyCrop() {
  const d = ui().docs.find((x) => x.id === ui().cur)
  if (!d || !crop) return
  const r = crop.r
  if (r.w < 1 || r.h < 1) return
  crop = null
  d.cropTo(r.x, r.y, r.w, r.h)
  ui().setTool('move')
}
export function cancelCrop() { crop = null; hooks.render() }
const cropHandles: [string, number, number][] = [['nw', 0, 0], ['n', 0.5, 0], ['ne', 1, 0], ['e', 1, 0.5], ['se', 1, 1], ['s', 0.5, 1], ['sw', 0, 1], ['w', 0, 0.5]]
function cropHit(d: Doc, sx: number, sy: number): string | null {
  const cr = ensureCrop(d).r
  for (const [id, fx, fy] of cropHandles) {
    const [px, py] = toScreen(d, cr.x + cr.w * fx, cr.y + cr.h * fy)
    if (Math.hypot(px - sx, py - sy) <= 9) return id
  }
  const [x0, y0] = toScreen(d, cr.x, cr.y), [x1, y1] = toScreen(d, cr.x + cr.w, cr.y + cr.h)
  if (sx > x0 && sx < x1 && sy > y0 && sy < y1) return 'move'
  return null
}
const cropTool: Tool = {
  cursor: 'crosshair',
  down(d, e) {
    let h = cropHit(d, e.sx, e.sy)
    const cr = ensureCrop(d)
    if (h === 'move' && cr.r.x === 0 && cr.r.y === 0 && cr.r.w === d.w && cr.r.h === d.h) h = null
    const sp = snapPt(d, e.x, e.y)
    if (h) cropDrag = { h, a: sp, r0: { ...cr.r } }
    else cropDrag = { h: 'new', a: sp, r0: { ...cr.r } }
  },
  move(d, e0) {
    if (!cropDrag || !crop) return
    const e = { ...e0, ...snapPt(d, e0.x, e0.y) }
    const { h, a, r0 } = cropDrag
    const ratio = cropRatio(d)
    let r: Rect
    if (h === 'new') r = dragRect(a, { x: e.x, y: e.y }, e.shift, e.alt, ratio)
    else if (h === 'move') r = { ...r0, x: Math.round(r0.x + e.x - a.x), y: Math.round(r0.y + e.y - a.y) }
    else {
      let x0 = r0.x, y0 = r0.y, x1 = r0.x + r0.w, y1 = r0.y + r0.h
      const px = Math.round(e.x), py = Math.round(e.y)
      if (h.includes('w')) x0 = px
      if (h.includes('e')) x1 = px
      if (h.includes('n')) y0 = py
      if (h.includes('s')) y1 = py
      if (ratio || e.shift) {
        const rr = ratio ?? r0.w / r0.h
        if (h.length === 2) {
          const w = Math.abs(x1 - x0), hh = w / rr
          if (h.includes('n')) y0 = y1 - Math.sign(y1 - y0 || 1) * hh; else y1 = y0 + Math.sign(y1 - y0 || 1) * hh
        } else if (h === 'e' || h === 'w') {
          const hh = Math.abs(x1 - x0) / rr, cy = (r0.y + r0.y + r0.h) / 2
          y0 = cy - hh / 2; y1 = cy + hh / 2
        } else {
          const ww = Math.abs(y1 - y0) * rr, cx = (r0.x + r0.x + r0.w) / 2
          x0 = cx - ww / 2; x1 = cx + ww / 2
        }
      }
      r = { x: Math.round(Math.min(x0, x1)), y: Math.round(Math.min(y0, y1)), w: Math.round(Math.abs(x1 - x0)), h: Math.round(Math.abs(y1 - y0)) }
    }
    crop.r = r
    hooks.render(); hooks.ui()
  },
  up() { cropDrag = null },
  hover(d, e) {
    const h = cropHit(d, e.sx, e.sy)
    if (!h) return 'crosshair'
    if (h === 'move') return 'move'
    return h === 'n' || h === 's' ? 'ns-resize' : h === 'e' || h === 'w' ? 'ew-resize' : h === 'nw' || h === 'se' ? 'nwse-resize' : 'nesw-resize'
  },
  overlay(c, d) {
    const cr = ensureCrop(d).r
    const [x0, y0] = toScreen(d, cr.x, cr.y), [x1, y1] = toScreen(d, cr.x + cr.w, cr.y + cr.h)
    const [dx0, dy0] = toScreen(d, 0, 0), [dx1, dy1] = toScreen(d, d.w, d.h)
    c.save()
    c.fillStyle = 'rgba(0,0,0,.55)'
    c.beginPath(); c.rect(Math.min(dx0, x0) - 4000, Math.min(dy0, y0) - 4000, 12000, 12000); c.rect(x0, y0, x1 - x0, y1 - y0); c.fill('evenodd')
    void dx1; void dy1
    c.strokeStyle = '#fff'; c.lineWidth = 1; c.strokeRect(x0 + 0.5, y0 + 0.5, x1 - x0, y1 - y0)
    c.strokeStyle = 'rgba(255,255,255,.35)'
    for (let i = 1; i < 3; i++) {
      c.beginPath(); c.moveTo(x0 + ((x1 - x0) * i) / 3, y0); c.lineTo(x0 + ((x1 - x0) * i) / 3, y1); c.moveTo(x0, y0 + ((y1 - y0) * i) / 3); c.lineTo(x1, y0 + ((y1 - y0) * i) / 3); c.stroke()
    }
    c.fillStyle = '#fff'
    for (const [, fx, fy] of cropHandles) { const px = x0 + (x1 - x0) * fx, py = y0 + (y1 - y0) * fy; c.fillRect(px - 4, py - 4, 8, 8) }
    c.font = '11px sans-serif'; c.fillStyle = '#fff'; c.fillText(`${cr.w} × ${cr.h}`, x0 + 4, y0 - 6)
    c.restore()
  },
  key(_d, e) {
    if (e.key === 'Enter') { applyCrop(); return true }
    if (e.key === 'Escape') { cancelCrop(); return true }
    return false
  },
  cancel() { cropDrag = null },
}

// ========================================================== BRUSH / ERASER
let stroke: { s: PaintSession; b: Brush; smooth: [number, number]; mode: string; kind: string } | null = null
let directStroke: DirectStroke | null = null
let sm: [number, number] = [0, 0]

function startPaint(d: Doc, e: PtrEv, kind: 'brush' | 'eraser' | 'clone') {
  const o = O(), L = d.active
  if (!L) { hooks.toast('No layer selected', 'error'); return }
  const target: 'canvas' | 'mask' = L.editMask && L.mask ? 'mask' : 'canvas'
  if (kind === 'brush' && ui().recent[0] !== ui().fg) ui().pushRecent(ui().fg)
  let op: GlobalCompositeOperation = 'source-over', color = ui().fg
  if (kind === 'eraser') { op = 'destination-out'; color = '#000000' }
  if (target === 'mask') {
    const light = luminance(parseColor(ui().fg)) >= 0.5
    const reveal = kind === 'eraser' ? !light : light
    op = reveal ? 'source-over' : 'destination-out'
    color = '#ffffff'
    if (kind === 'eraser') op = light ? 'destination-out' : 'source-over'
  }
  const s = PaintSession.start(d, L, target, op, o.opacity / 100, kind === 'eraser' ? 'Eraser' : kind === 'clone' ? 'Clone stamp' : 'Brush')
  if (!s) return
  const b = new Brush(s, o.brushSize, kind === 'clone' ? Math.min(o.hardness, 90) : o.hardness, color, o.flow / 100)
  if (kind === 'clone') {
    const src = toolState.cloneSrc
    if (!src) { s.cancel(); hooks.toast('Alt-click first to set the sample source', 'error'); return }
    if (!toolState.cloneOffset || !o.cloneAligned) toolState.cloneOffset = { x: src.x - e.x, y: src.y - e.y }
    const off = toolState.cloneOffset
    const base = s.edit.base
    b.custom = (c, x, y, dd, flow) => {
      const tip = mkCanvas(Math.ceil(dd) + 2, Math.ceil(dd) + 2), tc = ctx2(tip)
      const sx = x + off.x - tip.width / 2, sy = y + off.y - tip.height / 2
      tc.drawImage(base, -sx, -sy)
      tc.globalCompositeOperation = 'destination-in'
      tc.drawImage(tipCanvasFor(dd, b.hardness), 0, 0)
      c.globalAlpha = flow
      c.drawImage(tip, x - tip.width / 2, y - tip.height / 2)
    }
  }
  stroke = { s, b, smooth: [e.x, e.y], mode: target, kind }
  sm = [e.x, e.y]
  const last = toolState.lastBrushEnd
  if (e.shift && last && last.doc === d.id && kind !== 'clone') b.moveTo(last.x, last.y)
  b.moveTo(e.x, e.y)
}
const tipCanvasFor = (dd: number, h: number) => tipCanvas(dd, h, '#000000')
function movePaint(_d: Doc, e: PtrEv) {
  if (!stroke) return
  const k = 1 - clamp(O().smooth / 100, 0, 0.95) * 0.9
  sm = [sm[0] + (e.x - sm[0]) * k, sm[1] + (e.y - sm[1]) * k]
  stroke.b.moveTo(sm[0], sm[1])
}
function endPaint(d: Doc, e: PtrEv) {
  if (!stroke) return
  if (O().smooth > 0) stroke.b.moveTo(e.x, e.y)
  toolState.lastBrushEnd = { x: e.x, y: e.y, doc: d.id }
  stroke.s.finish()
  stroke = null
}
const brushLike = (kind: 'brush' | 'eraser'): Tool => ({
  cursor: 'none',
  down: (d, e) => startPaint(d, e, kind),
  move: movePaint,
  up: endPaint,
  cancel() { stroke?.s.cancel(); stroke = null },
})

// clone
const cloneTool: Tool = {
  cursor: 'none',
  down(d, e) {
    if (e.alt) { toolState.cloneSrc = { x: e.x, y: e.y }; toolState.cloneOffset = null; hooks.render(); hooks.toast('Sample source set', 'ok'); return }
    startPaint(d, e, 'clone')
  },
  move: movePaint,
  up: endPaint,
  cancel() { stroke?.s.cancel(); stroke = null },
  overlay(c, d) {
    const s = toolState.cloneSrc
    if (!s) return
    let { x, y } = s
    if (stroke && toolState.cloneOffset) { x = sm[0] + toolState.cloneOffset.x; y = sm[1] + toolState.cloneOffset.y }
    const [sx, sy] = toScreen(d, x, y)
    c.save(); c.strokeStyle = '#fff'; c.lineWidth = 1.5; c.beginPath(); c.arc(sx, sy, 6, 0, 7); c.moveTo(sx - 9, sy); c.lineTo(sx + 9, sy); c.moveTo(sx, sy - 9); c.lineTo(sx, sy + 9); c.stroke(); c.restore()
  },
}

// heal (spot) + AI brush share a hidden stroke
let hidden: { s: PaintSession; b: Brush; kind: 'heal' | 'ai' } | null = null
function startHidden(d: Doc, e: PtrEv, kind: 'heal' | 'ai') {
  const L = d.active
  if (!L) return
  if (kind === 'heal' && (L.locked || !L.visible)) { hooks.toast('Layer cannot be edited', 'error'); return }
  const s = PaintSession.start(d, L, 'canvas', 'source-over', 1, kind === 'heal' ? 'Spot heal' : 'AI selection', true)
  if (!s) return
  const b = new Brush(s, O().brushSize, 100, '#000000', 1)
  hidden = { s, b, kind }
  setPreviewStroke({ doc: d, alpha: 0.55, color: kind === 'heal' ? '#38c98a' : '#ff5c6c' })
  b.moveTo(e.x, e.y)
  hooks.render()
}
function endHidden(d: Doc) {
  if (!hidden) return
  const { s, kind } = hidden
  hidden = null
  setPreviewStroke(null)
  const r = s.dirty ? clipRect(s.dirty, d.w, d.h) : null
  if (!r || r.w < 1) { s.cancel(); return }
  const sdata = ctx2(d.stroke, true).getImageData(r.x, r.y, r.w, r.h)
  if (kind === 'ai') {
    const mask = new Uint8ClampedArray(d.w * d.h)
    for (let y = 0; y < r.h; y++) for (let x = 0; x < r.w; x++) mask[(y + r.y) * d.w + x + r.x] = sdata.data[(y * r.w + x) * 4 + 3] > 40 ? 255 : 0
    s.cancel()
    import('./selection').then(({ selFromMask }) => d.setSel(selCombine(d.sel, selFromMask(d.w, d.h, mask), 'add', d.w, d.h), 'AI marked area'))
    return
  }
  // heal: inpaint stroke area from its surroundings
  const L = s.layer
  const m = 28
  const rr = clipRect({ x: r.x - m, y: r.y - m, w: r.w + m * 2, h: r.h + m * 2 }, d.w, d.h)
  const c = ctx2(L.canvas, true)
  const img = c.getImageData(rr.x, rr.y, rr.w, rr.h)
  const fill = new Uint8Array(rr.w * rr.h), wgt = new Float32Array(rr.w * rr.h)
  for (let y = 0; y < r.h; y++) for (let x = 0; x < r.w; x++) {
    const a = sdata.data[(y * r.w + x) * 4 + 3]
    if (a > 20) { const i = (y + r.y - rr.y) * rr.w + (x + r.x - rr.x); fill[i] = 1; wgt[i] = a / 255 }
  }
  // grow fill by 1px to swallow antialiased edges
  const g = new Uint8Array(fill)
  for (let y = 1; y < rr.h - 1; y++) for (let x = 1; x < rr.w - 1; x++) { const i = y * rr.w + x; if (fill[i - 1] || fill[i + 1] || fill[i - rr.w] || fill[i + rr.w]) { g[i] = 1; if (!wgt[i]) wgt[i] = 0.8 } }
  const orig = new Uint8ClampedArray(img.data)
  inpaintDiffuse(img, g)
  const sel = d.sel
  for (let y = 0; y < rr.h; y++) for (let x = 0; x < rr.w; x++) {
    const i = y * rr.w + x
    if (!g[i]) { img.data.set(orig.subarray(i * 4, i * 4 + 4), i * 4); continue }
    let w = Math.min(1, wgt[i] * 1.2)
    if (sel) w *= sel.mask[(y + rr.y) * d.w + x + rr.x] / 255
    for (let ch = 0; ch < 4; ch++) img.data[i * 4 + ch] = orig[i * 4 + ch] * (1 - w) + img.data[i * 4 + ch] * w
  }
  c.putImageData(img, rr.x, rr.y)
  s.edit.commit('Spot heal', rr)
  s.clearStroke()
  d.history.end()
  d.changed()
}
const hiddenTool = (kind: 'heal' | 'ai'): Tool => ({
  cursor: 'none',
  down: (d, e) => startHidden(d, e, kind),
  move: (_d, e) => { hidden?.b.moveTo(e.x, e.y) ; hooks.render() },
  up: (d) => endHidden(d),
  cancel() { hidden?.s.cancel(); hidden = null; setPreviewStroke(null) },
})

// retouch / tone
const retouchLike = (which: 'retouch' | 'tone'): Tool => ({
  cursor: 'none',
  down(d, e) {
    const o = O()
    const kind = which === 'retouch' ? o.retouch : o.tone
    const s = DirectStroke.start(d, d.active, kind, o.brushSize, o.hardness, (which === 'retouch' ? o.strength : o.exposure) / 100, { blur: 'Blur', sharpen: 'Sharpen', smudge: 'Smudge', dodge: 'Dodge', burn: 'Burn' }[kind] ?? kind)
    if (!s) return
    directStroke = s
    s.moveTo(e.x, e.y)
  },
  move(_d, e) { directStroke?.moveTo(e.x, e.y) },
  up() { directStroke?.finish(); directStroke = null },
  cancel() { directStroke?.finish(); directStroke = null },
})

// =================================================================== BUCKET
const bucketTool: Tool = {
  cursor: 'crosshair',
  down(d, e) {
    const o = O(), L = d.active
    if (!L) return
    const target: 'canvas' | 'mask' = L.editMask && L.mask ? 'mask' : 'canvas'
    const img = target === 'mask' && L.mask ? ctx2(L.mask.canvas, true).getImageData(0, 0, d.w, d.h) : getSampleData(d, L, o.bucketAll)
    const mask = floodMask(img, Math.floor(e.x), Math.floor(e.y), o.tolerance, o.contiguous)
    const col = parseColor(ui().fg)
    const light = luminance(col) >= 0.5
    const fc = mkCanvas(d.w, d.h), id = new ImageData(d.w, d.h)
    const c = target === 'mask' ? { r: 255, g: 255, b: 255, a: 1 } : col
    for (let i = 0; i < mask.length; i++) if (mask[i]) { id.data[i * 4] = c.r; id.data[i * 4 + 1] = c.g; id.data[i * 4 + 2] = c.b; id.data[i * 4 + 3] = 255 * c.a }
    ctx2(fc).putImageData(id, 0, 0)
    // soften jagged edges slightly
    paintOnce(d, L, target, 'Paint bucket', (cc) => { cc.drawImage(fc, 0, 0) }, o.opacity / 100, target === 'mask' && !light ? 'destination-out' : 'source-over')
  },
}

// ================================================================= GRADIENT
let grad: { s: PaintSession; a: { x: number; y: number }; target: 'canvas' | 'mask' } | null = null
function drawGradient(d: Doc, a: { x: number; y: number }, b: { x: number; y: number }, target: 'canvas' | 'mask', alt: boolean) {
  void alt
  const o = O(), c = ctx2(d.stroke)
  c.setTransform(1, 0, 0, 1, 0, 0); c.globalAlpha = 1; c.globalCompositeOperation = 'source-over'
  c.clearRect(0, 0, d.w, d.h)
  const fg = ui().fg, bg = ui().bg
  const g = o.gradType === 'radial' ? c.createRadialGradient(a.x, a.y, 0, a.x, a.y, Math.max(1, Math.hypot(b.x - a.x, b.y - a.y))) : c.createLinearGradient(a.x, a.y, b.x, b.y)
  if (target === 'mask') { g.addColorStop(0, fg); g.addColorStop(1, bg) }
  else if (o.gradMode === 'fgtrans') { const f = parseColor(fg); g.addColorStop(0, fg); g.addColorStop(1, `rgba(${f.r},${f.g},${f.b},0)`) }
  else { g.addColorStop(0, fg); g.addColorStop(1, bg) }
  c.fillStyle = g
  c.fillRect(0, 0, d.w, d.h)
  if (target === 'mask') { // luminance -> alpha
    const id = c.getImageData(0, 0, d.w, d.h), px = id.data
    for (let i = 0; i < px.length; i += 4) { const l = 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2]; px[i] = px[i + 1] = px[i + 2] = 255; px[i + 3] = l }
    c.putImageData(id, 0, 0)
  }
}
const gradientTool: Tool = {
  cursor: 'crosshair',
  down(d, e) {
    const L = d.active
    if (!L) return
    const target: 'canvas' | 'mask' = L.editMask && L.mask ? 'mask' : 'canvas'
    const s = PaintSession.start(d, L, target, target === 'mask' && !d.sel ? 'copy' : 'source-over', O().opacity / 100, 'Gradient')
    if (!s) return
    grad = { s, a: { x: e.x, y: e.y }, target }
  },
  move(d, e) {
    if (!grad) return
    let b = { x: e.x, y: e.y }
    if (e.shift) { const dx = b.x - grad.a.x, dy = b.y - grad.a.y, ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4), l = Math.hypot(dx, dy); b = { x: grad.a.x + Math.cos(ang) * l, y: grad.a.y + Math.sin(ang) * l } }
    if (Math.hypot(b.x - grad.a.x, b.y - grad.a.y) < 1) return
    drawGradient(d, grad.a, b, grad.target, e.alt)
    grad.s.mark({ x: 0, y: 0, w: d.w, h: d.h })
    grad.s.refresh()
  },
  up(_d, e) {
    if (!grad) return
    const g = grad
    grad = null
    if (!g.s.dirty || Math.hypot(e.x - g.a.x, e.y - g.a.y) < 2) g.s.cancel()
    else g.s.finish()
  },
  cancel() { grad?.s.cancel(); grad = null },
}

// =============================================================== EYEDROPPER
const eyedropperTool: Tool = {
  cursor: 'crosshair',
  down(d, e) { this.move!(d, e) },
  move(d, e) {
    const p = pickPx(d, e.x, e.y, true)
    if (!p) return
    const hex = toHex({ r: p.r, g: p.g, b: p.b, a: 1 })
    if (e.alt) ui().setBg(hex); else ui().setFg(hex)
  },
  up() { const c = ui().fg; ui().pushRecent(c) },
}

// ======================================================================= TEXT
export function createTextLayer(d: Doc, x: number, y: number, text?: string) {
  const L = makeLayer({ type: 'text', name: 'Text', canvas: mkCanvas(2, 2) })
  L.text = { ...defaultText(ui().fg), ...(text ? { text } : {}) }
  d.refreshVector(L, false)
  L.t = { cx: x + L.canvas.width / 2, cy: y + L.canvas.height / 2, sx: 1, sy: 1, rot: 0 }
  L.name = (L.text.text || 'Text').split('\n')[0].slice(0, 16)
  d.addLayer(L, d.layers.length, 'Text')
  useUI.setState((s) => ({ panelsOpen: { ...s.panelsOpen, props: true } }))
  startTextEdit(L.id)
  return L
}
const textTool: Tool = {
  cursor: 'text',
  down(d, e) {
    for (let i = d.layers.length - 1; i >= 0; i--) {
      const L = d.layers[i]
      if (L.type === 'text' && L.visible && hitLayer(L, e.x, e.y, false)) {
        d.activeId = L.id; hooks.ui()
        useUI.setState((s) => ({ panelsOpen: { ...s.panelsOpen, props: true } }))
        startTextEdit(L.id)
        return
      }
    }
    createTextLayer(d, e.x, e.y)
  },
}

// ====================================================================== SHAPE
let shp: { a: { x: number; y: number }; cur: { x: number; y: number }; shift: boolean; alt: boolean } | null = null
const shapeTool: Tool = {
  cursor: 'crosshair',
  down(d, e) { const p = snapPt(d, e.x, e.y); shp = { a: p, cur: p, shift: e.shift, alt: e.alt } },
  move(d, e) { if (shp) { shp.cur = snapPt(d, e.x, e.y); shp.shift = e.shift; shp.alt = e.alt; hooks.render() } },
  up(d, e0) {
    if (!shp) return
    const e = { ...e0, ...snapPt(d, e0.x, e0.y) }
    const s = shp
    shp = null
    const o = O()
    let x0 = s.a.x, y0 = s.a.y, w: number, h: number
    const click = Math.hypot(e.x - s.a.x, e.y - s.a.y) < 4
    let flipY = false
    if (click) { w = 200; h = o.shape === 'line' ? 0 : 200; x0 = e.x - w / 2; y0 = e.y - h / 2 }
    else if (o.shape === 'line') {
      let dx = e.x - s.a.x, dy = e.y - s.a.y
      if (s.shift) { const ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4), l = Math.hypot(dx, dy); dx = Math.cos(ang) * l; dy = Math.sin(ang) * l }
      x0 = Math.min(s.a.x, s.a.x + dx); y0 = Math.min(s.a.y, s.a.y + dy); w = Math.abs(dx); h = Math.abs(dy)
      flipY = dx * dy < 0
    } else {
      const r = dragRect(s.a, { x: e.x, y: e.y }, s.shift, s.alt)
      x0 = r.x; y0 = r.y; w = r.w; h = r.h
    }
    const fg = ui().fg, bg = ui().bg
    const sp = defaultShape(o.shape, Math.max(1, w), Math.max(1, h), fg, o.shape === 'line' ? fg : bg)
    sp.fillOn = o.shape !== 'line' && o.shapeFill
    sp.strokeOn = o.shape === 'line' || o.shapeStroke
    sp.strokeWidth = o.shape === 'line' ? Math.max(o.shapeStrokeW, 2) : o.shapeStrokeW
    sp.radius = o.shapeRadius; sp.sides = o.shape === 'star' ? 5 : o.shapeSides; sp.flipY = flipY
    if (!sp.fillOn && !sp.strokeOn) sp.fillOn = true
    const L = makeLayer({ type: 'shape', name: { rect: 'Rectangle', ellipse: 'Ellipse', line: 'Line', polygon: 'Polygon', star: 'Star' }[o.shape], canvas: mkCanvas(2, 2) })
    L.shape = sp
    d.refreshVector(L, false)
    L.t = { cx: x0 + w / 2, cy: y0 + h / 2, sx: 1, sy: 1, rot: 0 }
    d.addLayer(L, d.layers.length, 'Shape')
    useUI.setState((st) => ({ panelsOpen: { ...st.panelsOpen, props: true } }))
  },
  overlay(c, d) {
    if (!shp) return
    const o = O()
    const [ax, ay] = toScreen(d, shp.a.x, shp.a.y), [bx, by] = toScreen(d, shp.cur.x, shp.cur.y)
    antsOutline(c, () => {
      if (o.shape === 'line') { c.moveTo(ax, ay); c.lineTo(bx, by) }
      else {
        const r = dragRect(shp!.a, shp!.cur, shp!.shift, shp!.alt)
        const [x0, y0] = toScreen(d, r.x, r.y), [x1, y1] = toScreen(d, r.x + r.w, r.y + r.h)
        if (o.shape === 'ellipse') c.ellipse((x0 + x1) / 2, (y0 + y1) / 2, Math.abs(x1 - x0) / 2, Math.abs(y1 - y0) / 2, 0, 0, 7); else c.rect(x0, y0, x1 - x0, y1 - y0)
      }
    })
  },
  cancel() { shp = null },
}

// ================================================================ HAND / ZOOM
let pan: { sx: number; sy: number; ox: number; oy: number } | null = null
export function beginPan(d: Doc, sx: number, sy: number) { pan = { sx, sy, ox: d.view.ox, oy: d.view.oy } }
export function movePan(d: Doc, sx: number, sy: number) {
  if (!pan) return
  d.view.ox = pan.ox + sx - pan.sx; d.view.oy = pan.oy + sy - pan.sy; d.view.fitted = false
  hooks.render()
}
export function endPan() { pan = null }
const handTool: Tool = {
  cursor: 'grab',
  down: (d, e) => beginPan(d, e.sx, e.sy),
  move: (d, e) => movePan(d, e.sx, e.sy),
  up: () => endPan(),
}
const zoomTool: Tool = {
  cursor: 'zoom-in',
  down(d, e) { zoomAt(d, e.sx, e.sy, d.view.zoom * (e.alt ? 0.5 : 2)); hooks.render(); hooks.ui() },
}

export const TOOLS: Record<ToolId, Tool> = {
  move: moveTool, marquee: marqueeTool, lasso: lassoTool, wand: wandTool, crop: cropTool,
  brush: brushLike('brush'), eraser: brushLike('eraser'), bucket: bucketTool, gradient: gradientTool, eyedropper: eyedropperTool,
  clone: cloneTool, heal: hiddenTool('heal'), retouch: retouchLike('retouch'), tone: retouchLike('tone'),
  text: textTool, shape: shapeTool, hand: handTool, zoom: zoomTool, aibrush: hiddenTool('ai'), quicksel: quickSelTool,
}
export const BRUSH_TOOLS: ToolId[] = ['brush', 'eraser', 'clone', 'heal', 'retouch', 'tone', 'aibrush', 'quicksel']
export { fitView }
