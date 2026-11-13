// Selection masks: construction, boolean ops, feather/grow/shrink, marching-ants outline, flood fill.
import type { Rect, Sel, SelMode } from './types'
import { gaussianBlurData } from './fx'
import { ctx2, mkCanvas } from './util'

function bboxOf(mask: Uint8ClampedArray, w: number, h: number): Rect | null {
  let x0 = w, y0 = h, x1 = -1, y1 = -1
  for (let y = 0; y < h; y++) {
    const o = y * w
    let first = -1, last = -1
    for (let x = 0; x < w; x++) if (mask[o + x]) { first = x; break }
    if (first < 0) continue
    for (let x = w - 1; x >= first; x--) if (mask[o + x]) { last = x; break }
    if (first < x0) x0 = first
    if (last > x1) x1 = last
    if (y < y0) y0 = y
    y1 = y
  }
  return x1 < 0 ? null : { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 }
}

function maskCanvas(mask: Uint8ClampedArray, w: number, h: number): HTMLCanvasElement {
  const cv = mkCanvas(w, h)
  const id = new ImageData(w, h), d = id.data
  for (let i = 0; i < mask.length; i++) d[i * 4 + 3] = mask[i]
  ctx2(cv).putImageData(id, 0, 0)
  return cv
}

export function buildAnts(sel: Sel) {
  const { w, h, mask, bbox } = sel
  const p = new Path2D()
  let count = 0
  const inside = (x: number, y: number) => x >= 0 && y >= 0 && x < w && y < h && mask[y * w + x] >= 128
  const x0 = bbox.x, x1 = bbox.x + bbox.w, y0 = bbox.y, y1 = bbox.y + bbox.h
  for (let y = y0; y <= y1; y++) {
    let run = -1
    for (let x = x0; x <= x1; x++) {
      const e = x < x1 && inside(x, y - 1) !== inside(x, y)
      if (e && run < 0) run = x
      if (!e && run >= 0) { p.moveTo(run, y); p.lineTo(x, y); run = -1; count++ }
    }
  }
  for (let x = x0; x <= x1; x++) {
    let run = -1
    for (let y = y0; y <= y1; y++) {
      const e = y < y1 && inside(x - 1, y) !== inside(x, y)
      if (e && run < 0) run = y
      if (!e && run >= 0) { p.moveTo(x, run); p.lineTo(x, y); run = -1; count++ }
    }
  }
  sel.ants = p
  sel.antsCount = count
}

export function selFromMask(w: number, h: number, mask: Uint8ClampedArray): Sel | null {
  const bbox = bboxOf(mask, w, h)
  if (!bbox) return null
  const s: Sel = { w, h, mask, canvas: maskCanvas(mask, w, h), bbox, ants: null, antsCount: 0 }
  buildAnts(s)
  return s
}
/** Build a selection from a canvas that was drawn on with any opaque colour (alpha = coverage). */
export function selFromCanvas(cv: HTMLCanvasElement): Sel | null {
  const id = ctx2(cv, true).getImageData(0, 0, cv.width, cv.height).data
  const mask = new Uint8ClampedArray(cv.width * cv.height)
  for (let i = 0; i < mask.length; i++) mask[i] = id[i * 4 + 3]
  return selFromMask(cv.width, cv.height, mask)
}
export function selAll(w: number, h: number): Sel | null {
  return selFromMask(w, h, new Uint8ClampedArray(w * h).fill(255))
}
export function selCombine(a: Sel | null, b: Sel | null, mode: SelMode, w: number, h: number): Sel | null {
  if (mode === 'replace') return b
  if (!a) return mode === 'add' ? b : null
  if (!b) return mode === 'intersect' ? null : a
  const out = new Uint8ClampedArray(w * h)
  const am = a.mask, bm = b.mask
  for (let i = 0; i < out.length; i++) {
    const x = am[i], y = bm[i]
    out[i] = mode === 'add' ? Math.max(x, y) : mode === 'subtract' ? Math.max(0, x - y) : Math.min(x, y)
  }
  return selFromMask(w, h, out)
}
export function selInvert(s: Sel): Sel | null {
  const out = new Uint8ClampedArray(s.mask.length)
  for (let i = 0; i < out.length; i++) out[i] = 255 - s.mask[i]
  return selFromMask(s.w, s.h, out)
}
export function selFeather(s: Sel, r: number): Sel | null {
  if (r <= 0) return s
  const id = new ImageData(s.w, s.h)
  for (let i = 0; i < s.mask.length; i++) id.data[i * 4 + 3] = s.mask[i]
  gaussianBlurData(id, r / 2)
  const out = new Uint8ClampedArray(s.mask.length)
  for (let i = 0; i < out.length; i++) out[i] = id.data[i * 4 + 3]
  return selFromMask(s.w, s.h, out)
}
function slide(src: Uint8ClampedArray, dst: Uint8ClampedArray, n: number, stride: number, start: number, r: number, max: boolean) {
  // O(n) sliding extreme over a line using a monotone deque
  const dq = new Int32Array(n)
  let head = 0, tail = 0
  const val = (i: number) => src[start + i * stride]
  for (let i = 0; i < n + r; i++) {
    if (i < n) {
      const v = val(i)
      while (tail > head && (max ? val(dq[tail - 1]) <= v : val(dq[tail - 1]) >= v)) tail--
      dq[tail++] = i
    }
    const c = i - r
    if (c >= 0) {
      while (dq[head] < c - r) head++
      dst[start + c * stride] = val(dq[head])
    }
  }
}
export function selMorph(s: Sel, n: number, grow: boolean): Sel | null {
  const { w, h } = s
  let a = s.mask, b = new Uint8ClampedArray(a.length)
  for (let y = 0; y < h; y++) slide(a, b, w, 1, y * w, n, grow)
  a = new Uint8ClampedArray(a.length)
  for (let x = 0; x < w; x++) slide(b, a, h, w, x, n, grow)
  return selFromMask(w, h, a)
}
export function selShift(s: Sel, dx: number, dy: number): Sel | null {
  const { w, h } = s
  const out = new Uint8ClampedArray(w * h)
  for (let y = 0; y < h; y++) {
    const ny = y + dy
    if (ny < 0 || ny >= h) continue
    for (let x = 0; x < w; x++) {
      const nx = x + dx
      if (nx >= 0 && nx < w) out[ny * w + nx] = s.mask[y * w + x]
    }
  }
  return selFromMask(w, h, out)
}
export function selBorder(s: Sel, width: number): Sel | null {
  const g = selMorph(s, Math.ceil(width / 2), true), sh = selMorph(s, Math.ceil(width / 2), false)
  if (!g) return null
  const out = new Uint8ClampedArray(g.mask.length)
  for (let i = 0; i < out.length; i++) out[i] = Math.max(0, g.mask[i] - (sh ? sh.mask[i] : 0))
  return selFromMask(s.w, s.h, out)
}

// ---------- flood fill (magic wand / bucket) ----------
export function floodMask(img: ImageData, sx: number, sy: number, tol: number, contiguous: boolean): Uint8ClampedArray {
  const { width: w, height: h, data: d } = img
  const mask = new Uint8ClampedArray(w * h)
  if (sx < 0 || sy < 0 || sx >= w || sy >= h) return mask
  const si = (sy * w + sx) * 4
  const sr = d[si], sg = d[si + 1], sb = d[si + 2], sa = d[si + 3]
  const match = (p: number) => {
    const i = p * 4, a = d[i + 3]
    if (a === 0 && sa === 0) return true
    return Math.abs(d[i] - sr) <= tol && Math.abs(d[i + 1] - sg) <= tol && Math.abs(d[i + 2] - sb) <= tol && Math.abs(a - sa) <= tol
  }
  if (!contiguous) {
    for (let p = 0; p < w * h; p++) if (match(p)) mask[p] = 255
    return mask
  }
  const stack: number[] = [sx, sy]
  while (stack.length) {
    const y = stack.pop()!, x = stack.pop()!
    let x1 = x
    while (x1 >= 0 && !mask[y * w + x1] && match(y * w + x1)) x1--
    x1++
    let up = false, down = false
    while (x1 < w && !mask[y * w + x1] && match(y * w + x1)) {
      mask[y * w + x1] = 255
      if (y > 0) {
        const m = !mask[(y - 1) * w + x1] && match((y - 1) * w + x1)
        if (m && !up) { stack.push(x1, y - 1); up = true } else if (!m) up = false
      }
      if (y < h - 1) {
        const m = !mask[(y + 1) * w + x1] && match((y + 1) * w + x1)
        if (m && !down) { stack.push(x1, y + 1); down = true } else if (!m) down = false
      }
      x1++
    }
  }
  return mask
}
