import type { Doc } from './doc'
import { clamp } from './util'

export const viewport = { w: 800, h: 600 }
/** callbacks run after every viewport redraw (DOM overlays like the text editor sync their position here) */
export const afterDraw = new Set<() => void>()

const ensure = (d: Doc) => { if (!(d.view.zoom > 0)) fitView(d) }
export const toScreen = (d: Doc, x: number, y: number): [number, number] => { ensure(d); return [x * d.view.zoom + d.view.ox, y * d.view.zoom + d.view.oy] }
export const toDoc = (d: Doc, sx: number, sy: number): [number, number] => { ensure(d); return [(sx - d.view.ox) / d.view.zoom, (sy - d.view.oy) / d.view.zoom] }

export function fitView(d: Doc, pad = 32) {
  const z = Math.min((viewport.w - pad * 2) / d.w, (viewport.h - pad * 2) / d.h)
  d.view.zoom = clamp(z > 1 ? Math.floor(z * 100) / 100 : z, 0.02, 64)
  d.view.ox = (viewport.w - d.w * d.view.zoom) / 2
  d.view.oy = (viewport.h - d.h * d.view.zoom) / 2
  d.view.fitted = true
}
export function zoomAt(d: Doc, sx: number, sy: number, z: number) {
  z = clamp(z, 0.02, 64)
  const [dx, dy] = toDoc(d, sx, sy)
  d.view.zoom = z
  d.view.ox = sx - dx * z
  d.view.oy = sy - dy * z
  d.view.fitted = false
}
export function zoomCenter(d: Doc, z: number) { zoomAt(d, viewport.w / 2, viewport.h / 2, z) }
export function actualSize(d: Doc) { zoomCenter(d, 1) }
