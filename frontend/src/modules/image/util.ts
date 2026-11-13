import type { Rect } from './types'

let _uid = 0
export const uid = () => `${Date.now().toString(36)}${(_uid++).toString(36)}${Math.random().toString(36).slice(2, 5)}`

export const clamp = (v: number, a: number, b: number) => (v < a ? a : v > b ? b : v)

export function mkCanvas(w: number, h: number): HTMLCanvasElement {
  const c = document.createElement('canvas')
  c.width = Math.max(1, Math.round(w))
  c.height = Math.max(1, Math.round(h))
  return c
}
export const ctx2 = (c: HTMLCanvasElement, readback = false) =>
  c.getContext('2d', readback ? { willReadFrequently: true } : undefined) as CanvasRenderingContext2D

export function cloneCanvas(c: HTMLCanvasElement): HTMLCanvasElement {
  const n = mkCanvas(c.width, c.height)
  ctx2(n).drawImage(c, 0, 0)
  return n
}

export function rectUnion(a: Rect | null, b: Rect | null): Rect | null {
  if (!a) return b
  if (!b) return a
  const x0 = Math.min(a.x, b.x), y0 = Math.min(a.y, b.y)
  const x1 = Math.max(a.x + a.w, b.x + b.w), y1 = Math.max(a.y + a.h, b.y + b.h)
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}
export function clipRect(r: Rect, w: number, h: number): Rect {
  const x0 = clamp(Math.floor(r.x), 0, w), y0 = clamp(Math.floor(r.y), 0, h)
  const x1 = clamp(Math.ceil(r.x + r.w), 0, w), y1 = clamp(Math.ceil(r.y + r.h), 0, h)
  return { x: x0, y: y0, w: Math.max(0, x1 - x0), h: Math.max(0, y1 - y0) }
}

// ---------- colour ----------
export interface RGBA { r: number; g: number; b: number; a: number } // a: 0..1
export function parseColor(s: string): RGBA {
  let h = s.trim()
  if (h.startsWith('#')) {
    h = h.slice(1)
    if (h.length === 3 || h.length === 4) h = h.split('').map((c) => c + c).join('')
    const n = (i: number) => parseInt(h.slice(i, i + 2), 16)
    return { r: n(0) || 0, g: n(2) || 0, b: n(4) || 0, a: h.length >= 8 ? n(6) / 255 : 1 }
  }
  const m = h.match(/rgba?\(([^)]+)\)/)
  if (m) {
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number)
    return { r: p[0] || 0, g: p[1] || 0, b: p[2] || 0, a: p[3] === undefined ? 1 : p[3] }
  }
  return { r: 0, g: 0, b: 0, a: 1 }
}
const hx = (n: number) => clamp(Math.round(n), 0, 255).toString(16).padStart(2, '0')
export function toHex(c: RGBA, withAlpha = true): string {
  return '#' + hx(c.r) + hx(c.g) + hx(c.b) + (withAlpha && c.a < 1 ? hx(c.a * 255) : '')
}
export function rgbToHsv(r: number, g: number, b: number): [number, number, number] {
  r /= 255; g /= 255; b /= 255
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn
  let h = 0
  if (d) {
    if (mx === r) h = ((g - b) / d) % 6
    else if (mx === g) h = (b - r) / d + 2
    else h = (r - g) / d + 4
    h *= 60
    if (h < 0) h += 360
  }
  return [h, mx ? d / mx : 0, mx]
}
export function hsvToRgb(h: number, s: number, v: number): [number, number, number] {
  const c = v * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = v - c
  let r = 0, g = 0, b = 0
  if (h < 60) [r, g, b] = [c, x, 0]
  else if (h < 120) [r, g, b] = [x, c, 0]
  else if (h < 180) [r, g, b] = [0, c, x]
  else if (h < 240) [r, g, b] = [0, x, c]
  else if (h < 300) [r, g, b] = [x, 0, c]
  else [r, g, b] = [c, 0, x]
  return [Math.round((r + m) * 255), Math.round((g + m) * 255), Math.round((b + m) * 255)]
}
export const luminance = (c: RGBA) => (0.299 * c.r + 0.587 * c.g + 0.114 * c.b) / 255

export function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((res, rej) => {
    const im = new Image()
    im.onload = () => res(im)
    im.onerror = () => rej(new Error('Failed to load image'))
    im.src = src
  })
}
export function imgToCanvas(im: CanvasImageSource & { width: number; height: number }): HTMLCanvasElement {
  const nw = (im as HTMLImageElement).naturalWidth || im.width, nh = (im as HTMLImageElement).naturalHeight || im.height
  const c = mkCanvas(nw, nh)
  ctx2(c).drawImage(im, 0, 0)
  return c
}
export function canvasToBlob(c: HTMLCanvasElement, type = 'image/png', q?: number): Promise<Blob> {
  return new Promise((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error('Export failed'))), type, q))
}
export function blobToDataURL(b: Blob): Promise<string> {
  return new Promise((res, rej) => {
    const r = new FileReader()
    r.onload = () => res(r.result as string)
    r.onerror = () => rej(r.error)
    r.readAsDataURL(b)
  })
}
export async function canvasToDataURL(c: HTMLCanvasElement): Promise<string> {
  return blobToDataURL(await canvasToBlob(c))
}
export function download(blob: Blob, name: string) {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(a.href), 4000)
}
export const isCanvasBlank = (c: HTMLCanvasElement) => {
  const d = ctx2(c, true).getImageData(0, 0, c.width, c.height).data
  for (let i = 3; i < d.length; i += 4) if (d[i]) return false
  return true
}
