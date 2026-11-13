// Brush engine: dab stamping onto a doc-sized stroke canvas, composited through the selection, plus direct (pixel) brush tools.
import type { Doc } from './doc'
import { hooks, PixelEdit } from './doc'
import type { Layer, Rect } from './types'
import { clamp, clipRect, ctx2, mkCanvas, parseColor, rectUnion } from './util'

// ---------- tip cache ----------
const tips = new Map<string, HTMLCanvasElement>()
export function tipCanvas(d: number, hardness: number, color: string): HTMLCanvasElement {
  const size = Math.max(1, Math.round(d))
  const key = `${size}|${Math.round(hardness)}|${color}`
  const hit = tips.get(key)
  if (hit) return hit
  const cv = mkCanvas(size + 2, size + 2), c = ctx2(cv), r = size / 2, cx = (size + 2) / 2
  const col = parseColor(color)
  const rgb = `${col.r},${col.g},${col.b}`
  if (hardness >= 99 || size <= 2) {
    c.fillStyle = color; c.beginPath(); c.arc(cx, cx, Math.max(r, 0.6), 0, Math.PI * 2); c.fill()
  } else {
    const h = clamp(hardness / 100, 0, 0.99)
    const g = c.createRadialGradient(cx, cx, 0, cx, cx, r)
    g.addColorStop(0, `rgba(${rgb},${col.a})`); g.addColorStop(h, `rgba(${rgb},${col.a})`)
    // eased falloff
    for (let i = 1; i <= 4; i++) { const t = i / 5; g.addColorStop(h + (1 - h) * t, `rgba(${rgb},${col.a * (1 - t) * (1 - t * 0.35 * (1 - t))})`) }
    g.addColorStop(1, `rgba(${rgb},0)`)
    c.fillStyle = g; c.fillRect(0, 0, size + 2, size + 2)
  }
  if (tips.size > 60) tips.delete(tips.keys().next().value!)
  tips.set(key, cv)
  return cv
}
/** weight 0..1 of a dab pixel at normalised distance t (0 centre .. 1 edge) */
export const dabWeight = (t: number, hardness: number) => {
  if (t >= 1) return 0
  const h = clamp(hardness / 100, 0, 0.99)
  return t <= h ? 1 : (1 - (t - h) / (1 - h)) ** 1.3
}

// ---------- paint session (stroke canvas -> selection mask -> layer) ----------
export let previewStroke: { doc: Doc; alpha: number; color: string } | null = null
export function setPreviewStroke(p: typeof previewStroke) { previewStroke = p }

export class PaintSession {
  doc: Doc
  layer: Layer
  target: 'canvas' | 'mask'
  cv: HTMLCanvasElement
  edit: PixelEdit
  op: GlobalCompositeOperation
  opacity: number
  name: string
  dirty: Rect | null = null
  pend: Rect | null = null
  private m: HTMLCanvasElement | null = null
  hidden: boolean

  private constructor(doc: Doc, layer: Layer, target: 'canvas' | 'mask', cv: HTMLCanvasElement, op: GlobalCompositeOperation, opacity: number, name: string, hidden: boolean) {
    this.doc = doc; this.layer = layer; this.target = target; this.cv = cv; this.op = op; this.opacity = opacity; this.name = name; this.hidden = hidden
    this.edit = new PixelEdit(doc, layer, target)
    const sc = ctx2(doc.stroke)
    sc.setTransform(1, 0, 0, 1, 0, 0); sc.globalCompositeOperation = 'source-over'; sc.globalAlpha = 1; sc.clearRect(0, 0, doc.w, doc.h)
  }
  /** hidden: stroke canvas is collected but not composited into the layer (heal / AI brush) */
  static start(doc: Doc, layer: Layer | undefined, target: 'canvas' | 'mask', op: GlobalCompositeOperation, opacity: number, name: string, hidden = false): PaintSession | null {
    doc.history.begin(name)
    let cv: HTMLCanvasElement | null = null
    if (hidden) cv = layer ? layer.canvas : null
    else cv = doc.paintTarget(layer, target)
    if (!cv || !layer) { doc.history.end(); return null }
    return new PaintSession(doc, layer, target, cv, op, opacity, name, hidden)
  }
  get ctx() { return ctx2(this.doc.stroke) }
  mark(r: Rect) {
    const g = { x: r.x - 2, y: r.y - 2, w: r.w + 4, h: r.h + 4 }
    this.pend = rectUnion(this.pend, g)
    this.dirty = rectUnion(this.dirty, g)
  }
  refresh() {
    if (!this.pend || this.hidden) { this.pend = null; hooks.render(); return }
    const { doc } = this
    const r = clipRect(this.pend, doc.w, doc.h)
    this.pend = null
    if (r.w <= 0 || r.h <= 0) return
    const c = ctx2(this.cv)
    c.save()
    c.setTransform(1, 0, 0, 1, 0, 0); c.filter = 'none'
    c.globalCompositeOperation = 'source-over'; c.globalAlpha = 1
    c.clearRect(r.x, r.y, r.w, r.h)
    c.drawImage(this.edit.base, r.x, r.y, r.w, r.h, r.x, r.y, r.w, r.h)
    if (!this.m || this.m.width < r.w || this.m.height < r.h) this.m = mkCanvas(Math.max(r.w, 64), Math.max(r.h, 64))
    const m = ctx2(this.m)
    m.globalCompositeOperation = 'source-over'; m.clearRect(0, 0, this.m.width, this.m.height)
    m.drawImage(doc.stroke, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h)
    if (doc.sel) { m.globalCompositeOperation = 'destination-in'; m.drawImage(doc.sel.canvas, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h) }
    c.globalAlpha = this.opacity; c.globalCompositeOperation = this.op
    c.drawImage(this.m, 0, 0, r.w, r.h, r.x, r.y, r.w, r.h)
    c.restore()
    doc.touch(this.layer, true)
  }
  clearStroke() {
    const c = ctx2(this.doc.stroke)
    c.setTransform(1, 0, 0, 1, 0, 0); c.globalAlpha = 1; c.globalCompositeOperation = 'source-over'
    if (this.dirty) { const r = clipRect(this.dirty, this.doc.w, this.doc.h); c.clearRect(r.x, r.y, r.w, r.h) } else c.clearRect(0, 0, this.doc.w, this.doc.h)
  }
  finish() {
    this.refresh()
    if (!this.hidden && this.dirty) this.edit.commit(this.name, this.dirty)
    this.clearStroke()
    this.doc.history.end()
    this.doc.changed()
  }
  cancel() {
    if (!this.hidden) this.edit.restore()
    this.clearStroke()
    this.doc.history.end()
    this.doc.touch(this.layer, true)
  }
}

/** Fire-and-forget: draw into the stroke canvas via `draw`, composite through selection once. */
export function paintOnce(doc: Doc, layer: Layer | undefined, target: 'canvas' | 'mask', name: string, draw: (c: CanvasRenderingContext2D) => void, opacity = 1, op: GlobalCompositeOperation = 'source-over'): boolean {
  const s = PaintSession.start(doc, layer, target, op, opacity, name)
  if (!s) return false
  draw(s.ctx)
  s.mark({ x: 0, y: 0, w: doc.w, h: doc.h })
  s.finish()
  return true
}

// ---------- brush stroke walker ----------
export class Brush {
  s: PaintSession
  d: number
  hardness: number
  color: string
  flow: number
  lx = 0; ly = 0; acc = 0; started = false
  custom?: (c: CanvasRenderingContext2D, x: number, y: number, d: number, flow: number) => void
  constructor(s: PaintSession, d: number, hardness: number, color: string, flow: number) {
    this.s = s; this.d = Math.max(1, d); this.hardness = hardness; this.color = color; this.flow = flow
  }
  stamp(x: number, y: number) {
    const c = this.s.ctx, d = this.d
    c.globalAlpha = this.flow
    if (this.custom) this.custom(c, x, y, d, this.flow)
    else {
      const tip = tipCanvas(d, this.hardness, this.color)
      c.drawImage(tip, x - tip.width / 2, y - tip.height / 2)
    }
    c.globalAlpha = 1
    this.s.mark({ x: x - d / 2 - 1, y: y - d / 2 - 1, w: d + 2, h: d + 2 })
  }
  moveTo(x: number, y: number) {
    if (!this.started) { this.started = true; this.lx = x; this.ly = y; this.stamp(x, y); this.s.refresh(); return }
    const dx = x - this.lx, dy = y - this.ly, len = Math.hypot(dx, dy)
    if (len < 0.01) return
    const step = Math.max(1, this.d * 0.1)
    let pos = step - this.acc
    while (pos <= len) { this.stamp(this.lx + (dx * pos) / len, this.ly + (dy * pos) / len); pos += step }
    this.acc = len - (pos - step)
    this.lx = x; this.ly = y
    this.s.refresh()
  }
}

// ---------- direct pixel brushes (blur / sharpen / smudge / dodge / burn) ----------
export class DirectStroke {
  doc: Doc
  layer: Layer
  edit: PixelEdit
  kind: string
  d: number
  hardness: number
  strength: number
  dirty: Rect | null = null
  lx = 0; ly = 0; acc = 0; started = false
  carry: ImageData | null = null
  constructor(doc: Doc, layer: Layer, kind: string, d: number, hardness: number, strength: number) {
    this.doc = doc; this.layer = layer; this.kind = kind; this.d = Math.max(2, d); this.hardness = hardness; this.strength = strength
    this.edit = new PixelEdit(doc, layer, 'canvas')
  }
  static start(doc: Doc, layer: Layer | undefined, kind: string, d: number, hardness: number, strength: number, name: string) {
    doc.history.begin(name)
    const cv = doc.paintTarget(layer, 'canvas')
    if (!cv || !layer) { doc.history.end(); return null }
    return new DirectStroke(doc, layer, kind, d, hardness, strength)
  }
  private dab(x: number, y: number) {
    const { doc } = this
    const r = clipRect({ x: x - this.d / 2, y: y - this.d / 2, w: this.d, h: this.d }, doc.w, doc.h)
    if (r.w < 1 || r.h < 1) return
    const c = ctx2(this.layer.canvas, true)
    const id = c.getImageData(r.x, r.y, r.w, r.h), d = id.data, w = r.w, h = r.h
    const orig = new Uint8ClampedArray(d)
    const rad = this.d / 2, sel = doc.sel
    const wt = (px: number, py: number) => {
      const t = Math.hypot(px + r.x + 0.5 - x, py + r.y + 0.5 - y) / rad
      let v = dabWeight(t, this.hardness)
      if (sel && v > 0) v *= sel.mask[(py + r.y) * doc.w + px + r.x] / 255
      return v
    }
    const k = this.strength
    if (this.kind === 'blur' || this.kind === 'sharpen') {
      for (let py = 0; py < h; py++) for (let px = 0; px < w; px++) {
        const ww = wt(px, py) * k
        if (ww <= 0) continue
        const i = (py * w + px) * 4
        for (let ch = 0; ch < 4; ch++) {
          let sum = 0, n = 0
          for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
            const xx = clamp(px + dx, 0, w - 1), yy = clamp(py + dy, 0, h - 1)
            sum += orig[(yy * w + xx) * 4 + ch]; n++
          }
          const avg = sum / n, o = orig[i + ch]
          d[i + ch] = this.kind === 'blur' ? o + (avg - o) * ww : clamp(o + (o - avg) * ww * 2.5, 0, 255)
        }
        if (this.kind === 'sharpen') d[i + 3] = orig[i + 3]
      }
    } else if (this.kind === 'smudge') {
      if (!this.carry) this.carry = new ImageData(new Uint8ClampedArray(orig), w, h)
      if (this.carry.width !== w || this.carry.height !== h) { // edge clip: rebuild
        const nc = new ImageData(w, h); nc.data.set(orig); this.carry = nc
      }
      const cd = this.carry.data
      for (let py = 0; py < h; py++) for (let px = 0; px < w; px++) {
        const ww = wt(px, py) * k
        if (ww <= 0) continue
        const i = (py * w + px) * 4
        for (let ch = 0; ch < 4; ch++) { const v = orig[i + ch] * (1 - ww) + cd[i + ch] * ww; d[i + ch] = v; cd[i + ch] = orig[i + ch] * (1 - k * 0.6) + v * (k * 0.6) }
      }
    } else {
      const dodge = this.kind === 'dodge'
      for (let py = 0; py < h; py++) for (let px = 0; px < w; px++) {
        const ww = wt(px, py) * k * 0.25
        if (ww <= 0) continue
        const i = (py * w + px) * 4
        for (let ch = 0; ch < 3; ch++) d[i + ch] = dodge ? orig[i + ch] + (255 - orig[i + ch]) * ww : orig[i + ch] * (1 - ww)
      }
    }
    c.putImageData(id, r.x, r.y)
    this.dirty = rectUnion(this.dirty, r)
  }
  moveTo(x: number, y: number) {
    if (!this.started) { this.started = true; this.lx = x; this.ly = y; this.dab(x, y); this.doc.touch(this.layer, true); return }
    const dx = x - this.lx, dy = y - this.ly, len = Math.hypot(dx, dy)
    if (len < 0.01) return
    const step = Math.max(1, this.d * 0.2)
    let pos = step - this.acc
    while (pos <= len) { this.dab(this.lx + (dx * pos) / len, this.ly + (dy * pos) / len); pos += step }
    this.acc = len - (pos - step)
    this.lx = x; this.ly = y
    this.doc.touch(this.layer, true)
  }
  finish() {
    if (this.dirty) this.edit.commit(this.kind, this.dirty)
    this.doc.history.end()
    this.doc.changed()
  }
}

// ---------- push-pull inpainting (healing-lite) ----------
/** Fill pixels where fill[i]==1 from surrounding known pixels. img is modified in place. */
export function inpaintDiffuse(img: ImageData, fill: Uint8Array) {
  const { width: W, height: H, data } = img
  type Lvl = { w: number; h: number; v: Float32Array; a: Float32Array }
  const lv0: Lvl = { w: W, h: H, v: new Float32Array(W * H * 4), a: new Float32Array(W * H) }
  for (let i = 0; i < W * H; i++) {
    if (fill[i]) continue
    lv0.a[i] = 1
    for (let c = 0; c < 4; c++) lv0.v[i * 4 + c] = data[i * 4 + c]
  }
  const levels: Lvl[] = [lv0]
  while (levels[levels.length - 1].w > 1 || levels[levels.length - 1].h > 1) {
    const p = levels[levels.length - 1]
    const nw = Math.max(1, (p.w + 1) >> 1), nh = Math.max(1, (p.h + 1) >> 1)
    const n: Lvl = { w: nw, h: nh, v: new Float32Array(nw * nh * 4), a: new Float32Array(nw * nh) }
    for (let y = 0; y < nh; y++) for (let x = 0; x < nw; x++) {
      let sw = 0; const sv = [0, 0, 0, 0]
      for (let dy = 0; dy < 2; dy++) for (let dx = 0; dx < 2; dx++) {
        const px = Math.min(p.w - 1, x * 2 + dx), py = Math.min(p.h - 1, y * 2 + dy), pi = py * p.w + px, a = p.a[pi]
        if (a <= 0) continue
        sw += a
        for (let c = 0; c < 4; c++) sv[c] += p.v[pi * 4 + c] * a
      }
      const ni = y * nw + x
      if (sw > 0) { n.a[ni] = Math.min(1, sw / 2); for (let c = 0; c < 4; c++) n.v[ni * 4 + c] = sv[c] / sw }
    }
    levels.push(n)
  }
  for (let l = levels.length - 2; l >= 0; l--) {
    const f = levels[l], co = levels[l + 1]
    for (let y = 0; y < f.h; y++) for (let x = 0; x < f.w; x++) {
      const i = y * f.w + x
      if (f.a[i] >= 1) continue
      // bilinear sample from coarse
      const gx = clamp((x + 0.5) / 2 - 0.5, 0, co.w - 1), gy = clamp((y + 0.5) / 2 - 0.5, 0, co.h - 1)
      const x0 = Math.floor(gx), y0 = Math.floor(gy), x1 = Math.min(co.w - 1, x0 + 1), y1 = Math.min(co.h - 1, y0 + 1), fx = gx - x0, fy = gy - y0
      const a = f.a[i]
      for (let c = 0; c < 4; c++) {
        const cv = (co.v[(y0 * co.w + x0) * 4 + c] * (1 - fx) + co.v[(y0 * co.w + x1) * 4 + c] * fx) * (1 - fy) + (co.v[(y1 * co.w + x0) * 4 + c] * (1 - fx) + co.v[(y1 * co.w + x1) * 4 + c] * fx) * fy
        f.v[i * 4 + c] = a > 0 ? f.v[i * 4 + c] * a + cv * (1 - a) : cv
      }
      f.a[i] = 1
    }
  }
  const out = lv0.v
  // relax masked pixels to remove blockiness
  const idx: number[] = []
  for (let i = 0; i < W * H; i++) if (fill[i]) idx.push(i)
  for (let it = 0; it < 24; it++) {
    for (const i of idx) {
      const x = i % W, y = (i / W) | 0
      const l = x > 0 ? i - 1 : i, r = x < W - 1 ? i + 1 : i, u = y > 0 ? i - W : i, d = y < H - 1 ? i + W : i
      for (let c = 0; c < 4; c++) out[i * 4 + c] = (out[l * 4 + c] + out[r * 4 + c] + out[u * 4 + c] + out[d * 4 + c]) * 0.25
    }
  }
  for (const i of idx) for (let c = 0; c < 4; c++) data[i * 4 + c] = out[i * 4 + c]
}
