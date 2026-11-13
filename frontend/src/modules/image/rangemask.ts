// Colour Range selection: soft mask of pixels close to a sampled colour.
import type { RGBA } from './util'

/** fuzz 0..100 (percent of max RGB distance). Returns a 0..255 mask. */
export function computeColorRange(img: ImageData, c: RGBA, fuzz: number, invert: boolean): Uint8ClampedArray {
  const { width: w, height: h, data: d } = img
  const mask = new Uint8ClampedArray(w * h)
  const max = 441.67 // sqrt(3 * 255^2)
  const hi = (fuzz / 100) * max, lo = hi * 0.8
  for (let i = 0; i < w * h; i++) {
    const o = i * 4
    const dr = d[o] - c.r, dg = d[o + 1] - c.g, db = d[o + 2] - c.b
    const dist = Math.sqrt(dr * dr + dg * dg + db * db)
    let m = dist <= lo ? 1 : dist >= hi ? 0 : (hi - dist) / Math.max(1e-6, hi - lo)
    m *= d[o + 3] / 255
    const v = Math.round(m * 255)
    mask[i] = invert ? 255 - v : v
  }
  return mask
}
