// Pixel adjustments, destructive filters and layer effects.
import type { Effects, Rect } from './types'
import { clamp, clipRect, ctx2, mkCanvas, parseColor } from './util'

export interface ParamDef { key: string; label: string; min: number; max: number; step?: number; def: number }
export interface AdjDef { type: string; label: string; params: ParamDef[] }

export const ADJ_DEFS: AdjDef[] = [
  { type: 'brightness', label: 'Brightness / Contrast', params: [
    { key: 'b', label: 'Brightness', min: -100, max: 100, def: 0 }, { key: 'c', label: 'Contrast', min: -100, max: 100, def: 0 }] },
  { type: 'huesat', label: 'Hue / Saturation', params: [
    { key: 'hue', label: 'Hue', min: -180, max: 180, def: 0 }, { key: 'sat', label: 'Saturation', min: -100, max: 100, def: 0 },
    { key: 'light', label: 'Lightness', min: -100, max: 100, def: 0 }] },
  { type: 'levels', label: 'Levels', params: [
    { key: 'inB', label: 'Input black', min: 0, max: 254, def: 0 }, { key: 'inW', label: 'Input white', min: 1, max: 255, def: 255 },
    { key: 'gamma', label: 'Midtones (gamma)', min: 10, max: 300, def: 100 },
    { key: 'outB', label: 'Output black', min: 0, max: 255, def: 0 }, { key: 'outW', label: 'Output white', min: 0, max: 255, def: 255 }] },
  { type: 'curves', label: 'Curves (simple)', params: [
    { key: 's', label: 'Shadows', min: -60, max: 60, def: 0 }, { key: 'm', label: 'Midtones', min: -60, max: 60, def: 0 },
    { key: 'h', label: 'Highlights', min: -60, max: 60, def: 0 }] },
  { type: 'blur', label: 'Blur', params: [{ key: 'r', label: 'Radius', min: 0, max: 60, step: 0.5, def: 4 }] },
  { type: 'sharpen', label: 'Sharpen', params: [
    { key: 'amount', label: 'Amount', min: 0, max: 300, def: 100 }, { key: 'r', label: 'Radius', min: 0.5, max: 10, step: 0.5, def: 1.5 }] },
  { type: 'vignette', label: 'Vignette', params: [
    { key: 'amount', label: 'Strength', min: 0, max: 100, def: 50 }, { key: 'size', label: 'Size', min: 0, max: 100, def: 60 }] },
  { type: 'temperature', label: 'Temperature / Tint', params: [
    { key: 'temp', label: 'Temperature', min: -100, max: 100, def: 0 }, { key: 'tint', label: 'Tint', min: -100, max: 100, def: 0 }] },
  { type: 'grayscale', label: 'Black & White', params: [{ key: 'a', label: 'Amount', min: 0, max: 100, def: 100 }] },
  { type: 'sepia', label: 'Sepia', params: [{ key: 'a', label: 'Amount', min: 0, max: 100, def: 100 }] },
  { type: 'invert', label: 'Invert', params: [{ key: 'a', label: 'Amount', min: 0, max: 100, def: 100 }] },
  { type: 'posterize', label: 'Posterize', params: [{ key: 'n', label: 'Levels', min: 2, max: 32, def: 5 }] },
  { type: 'threshold', label: 'Threshold', params: [{ key: 'level', label: 'Threshold', min: 0, max: 255, def: 128 }] },
]
export const adjDef = (t: string) => ADJ_DEFS.find((d) => d.type === t)!
export const defaultParams = (defs: ParamDef[]) => Object.fromEntries(defs.map((d) => [d.key, d.def])) as Record<string, number>

// ---------- helpers ----------
function cssPass(cv: HTMLCanvasElement, r: Rect, filter: string) {
  const tmp = mkCanvas(r.w, r.h)
  ctx2(tmp).drawImage(cv, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h)
  const c = ctx2(cv)
  c.save()
  c.globalCompositeOperation = 'copy'
  c.beginPath(); c.rect(r.x, r.y, r.w, r.h); c.clip()
  c.clearRect(r.x, r.y, r.w, r.h)
  c.globalCompositeOperation = 'source-over'
  c.filter = filter
  c.drawImage(tmp, r.x, r.y)
  c.restore()
}
function lutPass(cv: HTMLCanvasElement, r: Rect, lr: Uint8Array, lg: Uint8Array, lb: Uint8Array) {
  const c = ctx2(cv, true)
  const id = c.getImageData(r.x, r.y, r.w, r.h), d = id.data
  for (let i = 0; i < d.length; i += 4) { d[i] = lr[d[i]]; d[i + 1] = lg[d[i + 1]]; d[i + 2] = lb[d[i + 2]] }
  c.putImageData(id, r.x, r.y)
}
function monotoneLUT(xs: number[], ys: number[]): Uint8Array {
  const n = xs.length, dx: number[] = [], m: number[] = [], t: number[] = new Array(n)
  for (let i = 0; i < n - 1; i++) { dx[i] = xs[i + 1] - xs[i]; m[i] = (ys[i + 1] - ys[i]) / dx[i] }
  t[0] = m[0]; t[n - 1] = m[n - 2]
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue }
    const a = t[i] / m[i], b = t[i + 1] / m[i], s = a * a + b * b
    if (s > 9) { const k = 3 / Math.sqrt(s); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i] }
  }
  const lut = new Uint8Array(256)
  for (let x = 0; x < 256; x++) {
    let i = 0
    while (i < n - 2 && x > xs[i + 1]) i++
    const h = dx[i], u = (x - xs[i]) / h
    const h00 = 2 * u ** 3 - 3 * u ** 2 + 1, h10 = u ** 3 - 2 * u ** 2 + u, h01 = -2 * u ** 3 + 3 * u ** 2, h11 = u ** 3 - u ** 2
    lut[x] = clamp(Math.round(h00 * ys[i] + h10 * h * t[i] + h01 * ys[i + 1] + h11 * h * t[i + 1]), 0, 255)
  }
  return lut
}

// ---------- gaussian blur on ImageData (premultiplied float, clamped edges) ----------
function boxesForGauss(sigma: number, n: number): number[] {
  const wi = Math.sqrt((12 * sigma * sigma) / n + 1)
  let wl = Math.floor(wi)
  if (wl % 2 === 0) wl--
  const wu = wl + 2
  const mi = (12 * sigma * sigma - n * wl * wl - 4 * n * wl - 3 * n) / (-4 * wl - 4)
  const m = Math.round(mi)
  return Array.from({ length: n }, (_, i) => (i < m ? wl : wu))
}
function boxPass(src: Float32Array, dst: Float32Array, w: number, h: number, r: number, horiz: boolean) {
  const len = horiz ? w : h, lines = horiz ? h : w
  const step = horiz ? 4 : w * 4
  const iarr = 1 / (r + r + 1)
  for (let l = 0; l < lines; l++) {
    const base = horiz ? l * w * 4 : l * 4
    for (let c = 0; c < 4; c++) {
      const o = base + c
      const fv = src[o], lv = src[o + (len - 1) * step]
      let val = (r + 1) * fv
      for (let j = 0; j < r; j++) val += src[o + Math.min(j, len - 1) * step]
      for (let j = 0; j < len; j++) {
        const add = src[o + Math.min(j + r, len - 1) * step]
        const sub = j - r - 1 < 0 ? fv : src[o + (j - r - 1) * step]
        val += add - sub
        dst[o + j * step] = val * iarr
        void lv
      }
    }
  }
}
export function gaussianBlurData(img: ImageData, radius: number) {
  if (radius <= 0.01) return
  const { width: w, height: h, data: d } = img
  const n = w * h
  let a = new Float32Array(n * 4), b = new Float32Array(n * 4)
  for (let i = 0; i < n; i++) {
    const al = d[i * 4 + 3] / 255
    a[i * 4] = d[i * 4] * al; a[i * 4 + 1] = d[i * 4 + 1] * al; a[i * 4 + 2] = d[i * 4 + 2] * al; a[i * 4 + 3] = d[i * 4 + 3]
  }
  for (const bs of boxesForGauss(radius, 3)) {
    const r = (bs - 1) >> 1
    boxPass(a, b, w, h, r, true)
    boxPass(b, a, w, h, r, false)
  }
  for (let i = 0; i < n; i++) {
    const al = a[i * 4 + 3]
    if (al < 0.5) { d[i * 4] = d[i * 4 + 1] = d[i * 4 + 2] = d[i * 4 + 3] = 0; continue }
    const k = 255 / al
    d[i * 4] = clamp(a[i * 4] * k, 0, 255); d[i * 4 + 1] = clamp(a[i * 4 + 1] * k, 0, 255); d[i * 4 + 2] = clamp(a[i * 4 + 2] * k, 0, 255)
    d[i * 4 + 3] = clamp(al, 0, 255)
  }
}
function rectData(cv: HTMLCanvasElement, r: Rect) { return ctx2(cv, true).getImageData(r.x, r.y, r.w, r.h) }

// ---------- adjustments ----------
export function applyAdjust(cv: HTMLCanvasElement, rect: Rect, type: string, p: Record<string, number>) {
  const r = clipRect(rect, cv.width, cv.height)
  if (r.w <= 0 || r.h <= 0) return
  switch (type) {
    case 'brightness': cssPass(cv, r, `brightness(${Math.max(0, 1 + p.b / 100)}) contrast(${Math.max(0, 1 + p.c / 100)})`); break
    case 'huesat': cssPass(cv, r, `hue-rotate(${p.hue}deg) saturate(${Math.max(0, 1 + p.sat / 100)}) brightness(${Math.max(0, 1 + p.light / 100)})`); break
    case 'levels': {
      const inB = p.inB, inW = Math.max(inB + 1, p.inW), g = 1 / Math.max(0.01, p.gamma / 100)
      const lut = new Uint8Array(256)
      for (let i = 0; i < 256; i++) {
        const v = clamp((i - inB) / (inW - inB), 0, 1)
        lut[i] = clamp(Math.round(p.outB + Math.pow(v, g) * (p.outW - p.outB)), 0, 255)
      }
      lutPass(cv, r, lut, lut, lut); break
    }
    case 'curves': {
      const lut = monotoneLUT([0, 64, 128, 192, 255], [0, clamp(64 + p.s, 1, 126), clamp(128 + p.m, 2, 253), clamp(192 + p.h, 129, 254), 255])
      lutPass(cv, r, lut, lut, lut); break
    }
    case 'blur': if (p.r > 0) cssPass(cv, r, `blur(${p.r}px)`); break
    case 'sharpen': {
      const img = rectData(cv, r), bl = rectData(cv, r)
      gaussianBlurData(bl, p.r)
      const k = p.amount / 100, d = img.data, b = bl.data
      for (let i = 0; i < d.length; i += 4) {
        if (!d[i + 3]) continue
        d[i] = clamp(d[i] + (d[i] - b[i]) * k, 0, 255); d[i + 1] = clamp(d[i + 1] + (d[i + 1] - b[i + 1]) * k, 0, 255); d[i + 2] = clamp(d[i + 2] + (d[i + 2] - b[i + 2]) * k, 0, 255)
      }
      ctx2(cv).putImageData(img, r.x, r.y); break
    }
    case 'vignette': {
      const c = ctx2(cv)
      const cx = r.x + r.w / 2, cy = r.y + r.h / 2, R = Math.hypot(r.w, r.h) / 2
      const inner = R * clamp(p.size / 100, 0, 0.99) * 0.9
      const g = c.createRadialGradient(cx, cy, inner, cx, cy, R)
      g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(1, `rgba(0,0,0,${clamp(p.amount / 100, 0, 1)})`)
      c.save(); c.beginPath(); c.rect(r.x, r.y, r.w, r.h); c.clip()
      c.globalCompositeOperation = 'source-atop'; c.fillStyle = g; c.fillRect(r.x, r.y, r.w, r.h); c.restore(); break
    }
    case 'temperature': {
      const lr = new Uint8Array(256), lg = new Uint8Array(256), lb = new Uint8Array(256)
      const gr = 1 + p.temp * 0.0030, gb = 1 - p.temp * 0.0030, gg = 1 - p.tint * 0.0020, gr2 = 1 + p.tint * 0.0012
      for (let i = 0; i < 256; i++) { lr[i] = clamp(i * gr * gr2, 0, 255); lg[i] = clamp(i * gg, 0, 255); lb[i] = clamp(i * gb * gr2, 0, 255) }
      lutPass(cv, r, lr, lg, lb); break
    }
    case 'grayscale': cssPass(cv, r, `grayscale(${p.a}%)`); break
    case 'sepia': cssPass(cv, r, `sepia(${p.a}%)`); break
    case 'invert': cssPass(cv, r, `invert(${p.a}%)`); break
    case 'posterize': {
      const n = Math.max(2, Math.round(p.n)), lut = new Uint8Array(256)
      for (let i = 0; i < 256; i++) lut[i] = clamp(Math.round(Math.floor((i / 256) * n) * (255 / (n - 1))), 0, 255)
      lutPass(cv, r, lut, lut, lut); break
    }
    case 'threshold': {
      const c = ctx2(cv, true), id = c.getImageData(r.x, r.y, r.w, r.h), d = id.data
      for (let i = 0; i < d.length; i += 4) {
        const v = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2] >= p.level ? 255 : 0
        d[i] = d[i + 1] = d[i + 2] = v
      }
      c.putImageData(id, r.x, r.y); break
    }
  }
}
/** extra margin (px) an adjustment needs around the layer bounds */
export function adjPad(items: { type: string; enabled: boolean; p: Record<string, number> }[]): number {
  let m = 0
  for (const a of items) if (a.enabled && a.type === 'blur') m = Math.max(m, Math.ceil(a.p.r * 3))
  return m
}

// ---------- destructive filters ----------
export interface FilterDef { id: string; label: string; params: ParamDef[]; pad?: (p: Record<string, number>) => number; run: (cv: HTMLCanvasElement, r: Rect, p: Record<string, number>) => void }

function convolveLum(img: ImageData, fn: (get: (dx: number, dy: number) => number, x: number, y: number) => number) {
  const { width: w, height: h, data: d } = img
  const lum = new Float32Array(w * h)
  for (let i = 0; i < w * h; i++) lum[i] = 0.299 * d[i * 4] + 0.587 * d[i * 4 + 1] + 0.114 * d[i * 4 + 2]
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const get = (dx: number, dy: number) => lum[clamp(y + dy, 0, h - 1) * w + clamp(x + dx, 0, w - 1)]
    const v = clamp(fn(get, x, y), 0, 255), i = (y * w + x) * 4
    d[i] = d[i + 1] = d[i + 2] = v; d[i + 3] = d[i + 3] ? 255 : 0
  }
}

export const FILTER_DEFS: FilterDef[] = [
  { id: 'gauss', label: 'Gaussian Blur…', params: [{ key: 'r', label: 'Radius', min: 0.5, max: 100, step: 0.5, def: 6 }], pad: (p) => Math.ceil(p.r * 3),
    run: (cv, r, p) => { const id = rectData(cv, r); gaussianBlurData(id, p.r); ctx2(cv).putImageData(id, r.x, r.y) } },
  { id: 'motion', label: 'Motion Blur…', params: [{ key: 'angle', label: 'Angle', min: -180, max: 180, def: 0 }, { key: 'dist', label: 'Distance', min: 1, max: 200, def: 30 }],
    pad: (p) => Math.ceil(p.dist),
    run: (cv, r, p) => {
      const tmp = mkCanvas(r.w, r.h); ctx2(tmp).drawImage(cv, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h)
      const out = mkCanvas(r.w, r.h), o = ctx2(out), n = clamp(Math.round(p.dist), 2, 90), a = (p.angle * Math.PI) / 180
      o.imageSmoothingEnabled = true
      for (let i = 0; i < n; i++) {
        const t = (i / (n - 1) - 0.5) * p.dist
        o.globalAlpha = 1 / (i + 1)
        o.drawImage(tmp, Math.cos(a) * t, -Math.sin(a) * t)
      }
      const c = ctx2(cv); c.save(); c.globalCompositeOperation = 'copy'; c.beginPath(); c.rect(r.x, r.y, r.w, r.h); c.clip(); c.clearRect(r.x, r.y, r.w, r.h); c.globalCompositeOperation = 'source-over'; c.drawImage(out, r.x, r.y); c.restore()
    } },
  { id: 'usm', label: 'Unsharp Mask…', params: [{ key: 'amount', label: 'Amount', min: 1, max: 500, def: 120 }, { key: 'r', label: 'Radius', min: 0.5, max: 50, step: 0.5, def: 2 }, { key: 'th', label: 'Threshold', min: 0, max: 50, def: 0 }],
    pad: (p) => Math.ceil(p.r * 3),
    run: (cv, r, p) => {
      const img = rectData(cv, r), bl = rectData(cv, r); gaussianBlurData(bl, p.r)
      const d = img.data, b = bl.data, k = p.amount / 100
      for (let i = 0; i < d.length; i += 4) for (let c = 0; c < 3; c++) {
        const df = d[i + c] - b[i + c]
        if (Math.abs(df) >= p.th) d[i + c] = clamp(d[i + c] + df * k, 0, 255)
      }
      ctx2(cv).putImageData(img, r.x, r.y)
    } },
  { id: 'noise', label: 'Add Noise…', params: [{ key: 'amount', label: 'Amount', min: 1, max: 100, def: 20 }, { key: 'mono', label: 'Monochrome (0/1)', min: 0, max: 1, def: 0 }],
    run: (cv, r, p) => {
      const c = ctx2(cv, true), id = c.getImageData(r.x, r.y, r.w, r.h), d = id.data, k = p.amount * 2.55
      for (let i = 0; i < d.length; i += 4) {
        if (!d[i + 3]) continue
        const g = () => (Math.random() + Math.random() + Math.random() - 1.5) * k
        if (p.mono >= 0.5) { const n = g(); d[i] = clamp(d[i] + n, 0, 255); d[i + 1] = clamp(d[i + 1] + n, 0, 255); d[i + 2] = clamp(d[i + 2] + n, 0, 255) }
        else { d[i] = clamp(d[i] + g(), 0, 255); d[i + 1] = clamp(d[i + 1] + g(), 0, 255); d[i + 2] = clamp(d[i + 2] + g(), 0, 255) }
      }
      c.putImageData(id, r.x, r.y)
    } },
  { id: 'denoise', label: 'Reduce Noise (Surface Blur)…', params: [{ key: 'r', label: 'Radius', min: 1, max: 5, def: 2 }, { key: 'th', label: 'Threshold', min: 2, max: 80, def: 24 }],
    pad: (p) => p.r + 1,
    run: (cv, r, p) => {
      const c = ctx2(cv, true), id = c.getImageData(r.x, r.y, r.w, r.h), src = new Uint8ClampedArray(id.data), d = id.data
      const w = r.w, h = r.h, R = Math.round(p.r), th = p.th
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4
        let sr = 0, sg = 0, sb = 0, sw = 0
        for (let dy = -R; dy <= R; dy++) {
          const yy = clamp(y + dy, 0, h - 1)
          for (let dx = -R; dx <= R; dx++) {
            const j = (yy * w + clamp(x + dx, 0, w - 1)) * 4
            const df = Math.abs(src[j] - src[i]) + Math.abs(src[j + 1] - src[i + 1]) + Math.abs(src[j + 2] - src[i + 2])
            if (df < th * 3) { const wt = 1 - df / (th * 3); sr += src[j] * wt; sg += src[j + 1] * wt; sb += src[j + 2] * wt; sw += wt }
          }
        }
        if (sw > 0) { d[i] = sr / sw; d[i + 1] = sg / sw; d[i + 2] = sb / sw }
      }
      c.putImageData(id, r.x, r.y)
    } },
  { id: 'pixelate', label: 'Pixelate (Mosaic)…', params: [{ key: 'size', label: 'Cell size', min: 2, max: 200, def: 12 }],
    run: (cv, r, p) => {
      const c = ctx2(cv, true), id = c.getImageData(r.x, r.y, r.w, r.h), d = id.data, w = r.w, h = r.h, s = Math.round(p.size)
      for (let by = 0; by < h; by += s) for (let bx = 0; bx < w; bx += s) {
        let rr = 0, gg = 0, bb = 0, aa = 0, n = 0
        const ye = Math.min(h, by + s), xe = Math.min(w, bx + s)
        for (let y = by; y < ye; y++) for (let x = bx; x < xe; x++) { const i = (y * w + x) * 4, a = d[i + 3]; rr += d[i] * a; gg += d[i + 1] * a; bb += d[i + 2] * a; aa += a; n++ }
        const ar = aa ? aa / n : 0, rd = aa ? rr / aa : 0, gd = aa ? gg / aa : 0, bd = aa ? bb / aa : 0
        for (let y = by; y < ye; y++) for (let x = bx; x < xe; x++) { const i = (y * w + x) * 4; d[i] = rd; d[i + 1] = gd; d[i + 2] = bd; d[i + 3] = ar }
      }
      c.putImageData(id, r.x, r.y)
    } },
  { id: 'emboss', label: 'Emboss…', params: [{ key: 'angle', label: 'Angle', min: -180, max: 180, def: 135 }, { key: 'k', label: 'Strength', min: 1, max: 10, step: 0.5, def: 2 }],
    run: (cv, r, p) => {
      const c = ctx2(cv, true), id = c.getImageData(r.x, r.y, r.w, r.h), a = (p.angle * Math.PI) / 180, ca = Math.cos(a), sa = -Math.sin(a)
      convolveLum(id, (g) => {
        const gx = g(1, 0) - g(-1, 0) + (g(1, -1) - g(-1, -1) + g(1, 1) - g(-1, 1)) * 0.5
        const gy = g(0, 1) - g(0, -1) + (g(-1, 1) - g(-1, -1) + g(1, 1) - g(1, -1)) * 0.5
        return 128 + p.k * (gx * ca + gy * sa) * 0.5
      })
      c.putImageData(id, r.x, r.y)
    } },
  { id: 'edges', label: 'Find Edges…', params: [{ key: 'k', label: 'Strength', min: 1, max: 8, step: 0.5, def: 2 }],
    run: (cv, r, p) => {
      const c = ctx2(cv, true), id = c.getImageData(r.x, r.y, r.w, r.h)
      convolveLum(id, (g) => {
        const gx = -g(-1, -1) - 2 * g(-1, 0) - g(-1, 1) + g(1, -1) + 2 * g(1, 0) + g(1, 1)
        const gy = -g(-1, -1) - 2 * g(0, -1) - g(1, -1) + g(-1, 1) + 2 * g(0, 1) + g(1, 1)
        return 255 - Math.hypot(gx, gy) * p.k * 0.5
      })
      c.putImageData(id, r.x, r.y)
    } },
  { id: 'sharpen', label: 'Sharpen (fast)', params: [{ key: 'a', label: 'Amount', min: 10, max: 300, def: 100 }],
    run: (cv, r, p) => applyAdjust(cv, r, 'sharpen', { amount: p.a, r: 1 }) },
  { id: 'vignette', label: 'Vignette…', params: adjDef('vignette').params, run: (cv, r, p) => applyAdjust(cv, r, 'vignette', p) },
]
export const ADJ_FILTERS: FilterDef[] = ADJ_DEFS.map((d) => ({ id: 'adj:' + d.type, label: d.label + (d.params.length ? '…' : ''), params: d.params, pad: d.type === 'blur' ? (p: Record<string, number>) => p.r * 3 : undefined, run: (cv, r, p) => applyAdjust(cv, r, d.type, p) }))
export const findFilter = (id: string) => [...FILTER_DEFS, ...ADJ_FILTERS].find((f) => f.id === id)!

// ---------- layer effects ----------
export const defaultFx = (): Effects => ({
  shadow: { on: false, color: '#000000', dx: 6, dy: 6, blur: 10, opacity: 0.6 },
  stroke: { on: false, color: '#ffffff', width: 4, opacity: 1 },
  glow: { on: false, color: '#ffe066', size: 14, opacity: 0.9 },
})
export const fxActive = (fx: Effects) => fx.shadow.on || fx.stroke.on || fx.glow.on
export function fxPad(fx: Effects): number {
  let p = 0
  if (fx.stroke.on) p = Math.max(p, fx.stroke.width + 2)
  if (fx.glow.on) p = Math.max(p, fx.glow.size * 2 + 2)
  if (fx.shadow.on) p = Math.max(p, fx.shadow.blur * 2 + Math.max(Math.abs(fx.shadow.dx), Math.abs(fx.shadow.dy)) + 2)
  return Math.ceil(p)
}
function rgba(color: string, op: number) { const c = parseColor(color); return `rgba(${c.r},${c.g},${c.b},${c.a * op})` }
export function applyFx(cv: HTMLCanvasElement, rect: Rect, fx: Effects) {
  const r = clipRect(rect, cv.width, cv.height)
  if (r.w <= 0 || r.h <= 0) return
  const src = mkCanvas(r.w, r.h)
  ctx2(src).drawImage(cv, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h)
  const out = mkCanvas(r.w, r.h), o = ctx2(out)
  const OFF = 20000
  const shadowOnly = (color: string, blur: number, dx: number, dy: number) => {
    o.save(); o.shadowColor = color; o.shadowBlur = blur; o.shadowOffsetX = OFF + dx; o.shadowOffsetY = dy
    o.drawImage(src, -OFF, 0); o.restore()
  }
  if (fx.glow.on) { const col = rgba(fx.glow.color, fx.glow.opacity); shadowOnly(col, fx.glow.size, 0, 0); shadowOnly(col, fx.glow.size, 0, 0) }
  if (fx.shadow.on) shadowOnly(rgba(fx.shadow.color, fx.shadow.opacity), fx.shadow.blur, fx.shadow.dx, fx.shadow.dy)
  if (fx.stroke.on && fx.stroke.width > 0) {
    const tint = mkCanvas(r.w, r.h), t = ctx2(tint)
    t.drawImage(src, 0, 0); t.globalCompositeOperation = 'source-in'; t.fillStyle = fx.stroke.color; t.fillRect(0, 0, r.w, r.h)
    const sc = mkCanvas(r.w, r.h), s = ctx2(sc)
    const W = fx.stroke.width
    for (const rad of [W * 0.5, W]) for (let k = 0; k < 28; k++) { const a = (k / 28) * Math.PI * 2; s.drawImage(tint, Math.cos(a) * rad, Math.sin(a) * rad) }
    o.globalAlpha = fx.stroke.opacity; o.drawImage(sc, 0, 0); o.globalAlpha = 1
  }
  o.drawImage(src, 0, 0)
  const c = ctx2(cv)
  c.save(); c.globalCompositeOperation = 'copy'; c.beginPath(); c.rect(r.x, r.y, r.w, r.h); c.clip(); c.clearRect(r.x, r.y, r.w, r.h)
  c.globalCompositeOperation = 'source-over'; c.drawImage(out, r.x, r.y); c.restore()
}
