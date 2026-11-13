// Rulers, guides and snapping helpers.
import { hooks } from './doc'
import type { Doc } from './doc'
import { useUI } from './store'
import type { Rect } from './types'
import { uid } from './util'

export const RULER = 18
export const SNAP_PX = 6

/** snap candidates along an axis: 'x' -> vertical guides + doc edges/centre, 'y' -> horizontal guides + doc edges/centre */
function candidates(d: Doc, axis: 'x' | 'y', skipId?: string): number[] {
  const out = axis === 'x' ? [0, d.w / 2, d.w] : [0, d.h / 2, d.h]
  for (const g of d.guides) if (g.id !== skipId && g.axis === (axis === 'x' ? 'v' : 'h')) out.push(g.pos)
  return out
}
export const snapOn = () => useUI.getState().snap

export function snapAxis(d: Doc, axis: 'x' | 'y', v: number, skipId?: string): number {
  if (!snapOn()) return v
  const thr = SNAP_PX / Math.max(0.01, d.view.zoom)
  let best = v, bd = thr
  for (const c of candidates(d, axis, skipId)) { const dist = Math.abs(c - v); if (dist <= bd) { bd = dist; best = c } }
  return best
}
export function snapPt(d: Doc, x: number, y: number): { x: number; y: number } {
  return { x: snapAxis(d, 'x', x), y: snapAxis(d, 'y', y) }
}
/** snap a moving rectangle (its edges + centre) by adjusting the translation (dx,dy) */
export function snapMove(d: Doc, b: Rect, dx: number, dy: number): { dx: number; dy: number } {
  if (!snapOn() || b.w <= 0) return { dx, dy }
  const thr = SNAP_PX / Math.max(0.01, d.view.zoom)
  const fit = (axis: 'x' | 'y', pos: number, size: number, delta: number) => {
    let best = delta, bd = thr
    for (const e of [pos, pos + size / 2, pos + size]) for (const c of candidates(d, axis)) {
      const dist = Math.abs(e + delta - c)
      if (dist <= bd) { bd = dist; best = c - e }
    }
    return best
  }
  return { dx: fit('x', b.x, b.w, dx), dy: fit('y', b.y, b.h, dy) }
}

export function commitGuides(d: Doc, before: Doc['guides'], name: string) {
  const after = d.guides.map((g) => ({ ...g }))
  const prev = before.map((g) => ({ ...g }))
  if (JSON.stringify(prev) === JSON.stringify(after)) return
  d.history.push({
    name, bytes: 64,
    undo: () => { d.guides = prev.map((g) => ({ ...g })); hooks.render(); hooks.ui() },
    redo: () => { d.guides = after.map((g) => ({ ...g })); hooks.render(); hooks.ui() },
  })
  d.revision++
  hooks.render(); hooks.ui()
}
export function addGuide(d: Doc, axis: 'h' | 'v', pos: number) {
  const before = d.guides.map((g) => ({ ...g }))
  d.guides = [...d.guides, { id: uid(), axis, pos: Math.round(pos) }]
  commitGuides(d, before, 'New guide')
}
export function clearGuides(d: Doc) {
  const before = d.guides.map((g) => ({ ...g }))
  d.guides = []
  commitGuides(d, before, 'Clear guides')
}

// ---------- drawing ----------
const STEPS = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000]
export function drawRulers(c: CanvasRenderingContext2D, d: Doc, vw: number, vh: number, mouse: { sx: number; sy: number; in: boolean }) {
  const { zoom: z, ox, oy } = d.view
  const step = STEPS.find((s) => s * z >= 70) ?? 10000
  const minor = step / 10 * z >= 5 ? step / 10 : step / 5 * z >= 5 ? step / 5 : step
  c.save()
  c.fillStyle = '#15171d'; c.fillRect(0, 0, vw, RULER); c.fillRect(0, 0, RULER, vh)
  c.fillStyle = '#242833'; c.fillRect(0, 0, RULER, RULER)
  c.strokeStyle = '#3a4050'; c.lineWidth = 1
  c.beginPath(); c.moveTo(RULER + 0.5, RULER); c.lineTo(RULER + 0.5, vh); c.moveTo(RULER, RULER + 0.5); c.lineTo(vw, RULER + 0.5); c.stroke()
  c.fillStyle = '#9aa1b2'; c.font = '9px "Segoe UI", sans-serif'; c.textBaseline = 'top'
  // horizontal
  c.save(); c.beginPath(); c.rect(RULER, 0, vw, RULER); c.clip()
  for (let x = Math.floor((RULER - ox) / z / minor) * minor; ox + x * z < vw; x += minor) {
    const sx = Math.round(ox + x * z) + 0.5, major = Math.abs(x / step - Math.round(x / step)) < 1e-9
    c.beginPath(); c.moveTo(sx, RULER); c.lineTo(sx, RULER - (major ? 10 : 4)); c.stroke()
    if (major) c.fillText(String(Math.round(x)), sx + 2, 1)
  }
  c.restore()
  // vertical
  c.save(); c.beginPath(); c.rect(0, RULER, RULER, vh); c.clip()
  for (let y = Math.floor((RULER - oy) / z / minor) * minor; oy + y * z < vh; y += minor) {
    const sy = Math.round(oy + y * z) + 0.5, major = Math.abs(y / step - Math.round(y / step)) < 1e-9
    c.beginPath(); c.moveTo(RULER, sy); c.lineTo(RULER - (major ? 10 : 4), sy); c.stroke()
    if (major) { c.save(); c.translate(1, sy + 2); c.rotate(-Math.PI / 2); c.textAlign = 'right'; c.fillText(String(Math.round(y)), 0, 0); c.restore() }
  }
  c.restore()
  if (mouse.in) {
    c.strokeStyle = '#7c5cff'
    c.beginPath(); c.moveTo(mouse.sx + 0.5, 0); c.lineTo(mouse.sx + 0.5, RULER); c.moveTo(0, mouse.sy + 0.5); c.lineTo(RULER, mouse.sy + 0.5); c.stroke()
  }
  c.restore()
}
export function drawGuides(c: CanvasRenderingContext2D, d: Doc, vw: number, vh: number, rulers: boolean) {
  const { zoom: z, ox, oy } = d.view
  const r0 = rulers ? RULER : 0
  c.save()
  c.lineWidth = 1; c.strokeStyle = '#2fd4ff'
  for (const g of d.guides) {
    c.beginPath()
    if (g.axis === 'v') { const x = Math.round(ox + g.pos * z) + 0.5; c.moveTo(x, r0); c.lineTo(x, vh) } else { const y = Math.round(oy + g.pos * z) + 0.5; c.moveTo(r0, y); c.lineTo(vw, y) }
    c.stroke()
  }
  c.restore()
}
