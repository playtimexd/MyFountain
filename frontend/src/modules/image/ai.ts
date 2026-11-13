// AI integrations: remove-bg, inpaint, upscale, plus asset/catalog helpers.
import { api, waitJob } from '../../api/client'
import type { JobInfo, MediaRow } from '../../types/api'
import type { Doc } from './doc'
import { hooks, makeLayer, PixelEdit } from './doc'
import type { Layer, Rect } from './types'
import { clipRect, ctx2, imgToCanvas, loadImage, mkCanvas, canvasToBlob } from './util'

const b64 = (cv: HTMLCanvasElement) => cv.toDataURL('image/png').split(',')[1]
async function fromB64(s: string): Promise<HTMLCanvasElement> {
  return imgToCanvas(await loadImage(s.startsWith('data:') ? s : 'data:image/png;base64,' + s))
}
function friendly(path: string, e: unknown): Error {
  const m = e instanceof Error ? e.message : String(e)
  if (/^404|Not Found/i.test(m)) return new Error(`The backend does not provide ${path} yet (404). Try again later or enable the AI service.`)
  return new Error(m)
}

/** region of the doc that an AI op should work on: selection bbox, else layer bounds, clipped to doc */
export function targetRect(d: Doc, L: Layer | undefined, margin = 0): Rect {
  let r: Rect
  if (d.sel) r = d.sel.bbox
  else if (L) r = d.layerBounds(L)
  else r = { x: 0, y: 0, w: d.w, h: d.h }
  return clipRect({ x: r.x - margin, y: r.y - margin, w: r.w + margin * 2, h: r.h + margin * 2 }, d.w, d.h)
}
function regionOf(src: HTMLCanvasElement, r: Rect, maskSel?: Doc['sel']): HTMLCanvasElement {
  const cv = mkCanvas(r.w, r.h), c = ctx2(cv)
  c.drawImage(src, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h)
  if (maskSel) { c.globalCompositeOperation = 'destination-in'; c.drawImage(maskSel.canvas, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h) }
  return cv
}

export type AiMode = 'layer' | 'mask'

export async function aiRemoveBg(d: Doc, mode: AiMode, progress: (m: string) => void): Promise<{ engine?: string }> {
  const L = d.active
  if (!L) throw new Error('No layer selected')
  const r = targetRect(d, L)
  if (r.w < 2 || r.h < 2) throw new Error('The layer is outside the canvas')
  const src = regionOf(d.getCache(L), r, d.sel)
  progress('Removing background…')
  let res: { imageBase64: string; engine?: string }
  try { res = await api.post('/api/ai/remove-bg', { imageBase64: b64(src) }) } catch (e) { throw friendly('/api/ai/remove-bg', e) }
  if (!res?.imageBase64) throw new Error('The backend returned no image')
  const out = await fromB64(res.imageBase64)
  if (mode === 'layer') {
    const nl = makeLayer({ type: 'image', name: L.name + ' cutout', canvas: out })
    nl.t = { cx: r.x + out.width / 2, cy: r.y + out.height / 2, sx: r.w / out.width, sy: r.h / out.height, rot: 0 }
    d.addLayer(nl, d.layers.indexOf(L) + 1, 'AI remove background')
  } else {
    d.history.group('AI remove background (mask)', () => {
      if (!L.mask) d.addMask(L, false)
      L.editMask = false
      const e = new PixelEdit(d, L, 'mask')
      const c = ctx2(L.mask!.canvas)
      c.clearRect(0, 0, d.w, d.h)
      c.drawImage(out, r.x, r.y, r.w, r.h)
      e.commit('AI remove background (mask)')
    })
    d.changed()
  }
  hooks.toast(`Background removed${res.engine ? ` (engine: ${res.engine})` : ''}`, 'ok')
  return { engine: res.engine }
}

export async function aiInpaint(d: Doc, prompt: string, progress: (m: string) => void): Promise<{ engine?: string }> {
  if (!d.sel) throw new Error('Select the area to erase/repair first (selection tools or the AI Mark Brush)')
  const L = d.active
  const r = targetRect(d, L, 48)
  const img = regionOf(d.ensureComp(), r)
  const mask = mkCanvas(r.w, r.h), mc = ctx2(mask)
  mc.fillStyle = '#000'; mc.fillRect(0, 0, r.w, r.h)
  const mm = mkCanvas(r.w, r.h), mmc = ctx2(mm)
  mmc.drawImage(d.sel.canvas, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h)
  // binarise & slightly dilate so the fill covers soft edges
  const id = mmc.getImageData(0, 0, r.w, r.h)
  for (let i = 0; i < id.data.length; i += 4) { const on = id.data[i + 3] > 24; id.data[i] = id.data[i + 1] = id.data[i + 2] = 255; id.data[i + 3] = on ? 255 : 0 }
  mmc.putImageData(id, 0, 0)
  mc.filter = 'blur(1.5px)'; mc.drawImage(mm, 0, 0); mc.filter = 'none'; mc.drawImage(mm, 0, 0)
  progress('Inpainting…')
  let res: { imageBase64: string; engine?: string }
  try { res = await api.post('/api/ai/inpaint', { imageBase64: b64(img), maskBase64: b64(mask), prompt: prompt || undefined }) } catch (e) { throw friendly('/api/ai/inpaint', e) }
  if (!res?.imageBase64) throw new Error('The backend returned no image')
  const out = await fromB64(res.imageBase64)
  const fin = mkCanvas(r.w, r.h), fc = ctx2(fin)
  fc.drawImage(out, 0, 0, r.w, r.h)
  fc.globalCompositeOperation = 'destination-in'
  fc.filter = 'blur(1px)'
  fc.drawImage(d.sel.canvas, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h)
  fc.filter = 'none'
  const nl = makeLayer({ type: 'image', name: 'AI inpaint', canvas: fin })
  nl.t = { cx: r.x + r.w / 2, cy: r.y + r.h / 2, sx: 1, sy: 1, rot: 0 }
  d.addLayer(nl, d.layers.length, 'AI erase / inpaint')
  hooks.toast(`Inpainting done - added as a new layer${res.engine ? ` (engine: ${res.engine})` : ''}`, 'ok')
  return { engine: res.engine }
}

export async function aiUpscale(d: Doc, scale: 2 | 4, progress: (j: JobInfo | null, m: string) => void) {
  const L = d.active
  if (!L) throw new Error('No layer selected')
  const r = targetRect(d, L)
  const src = regionOf(d.getCache(L), r, d.sel)
  progress(null, 'Uploading image…')
  const blob = await canvasToBlob(src)
  let media: MediaRow
  try { media = await api.upload<MediaRow>('/api/media/upload', new File([blob], `upscale-src-${Date.now().toString(36)}.png`, { type: 'image/png' })) } catch (e) { throw friendly('/api/media/upload', e) }
  let job: JobInfo<{ media: MediaRow }>
  try { job = await api.post('/api/ai/upscale', { mediaId: media.id, scale }) } catch (e) { throw friendly('/api/ai/upscale', e) }
  const done = await waitJob<{ media: MediaRow }>(job, (j) => progress(j, j.message || 'Upscaling…'))
  const url = done.result?.media?.url
  if (!url) throw new Error('The job finished without returning an image')
  const out = imgToCanvas(await loadImage(url))
  const nl = makeLayer({ type: 'image', name: `${L.name} ${scale}x`, canvas: out })
  nl.t = { cx: r.x + r.w / 2, cy: r.y + r.h / 2, sx: r.w / out.width, sy: r.h / out.height, rot: 0 }
  d.addLayer(nl, d.layers.indexOf(L) + 1, 'AI upscale')
  hooks.toast(`Upscale done (${out.width}×${out.height}, placed at the original size)`, 'ok')
}

// ---------- catalog / library ----------
export interface CatalogLite {
  stickers: { id: string; name: string; url: string }[]
  backgrounds: { id: string; name: string; css: string; url?: string }[]
}
export async function fetchCatalog(): Promise<CatalogLite | null> {
  try {
    const c = await api.get<Partial<CatalogLite>>('/api/assets/catalog')
    return { stickers: c.stickers ?? [], backgrounds: c.backgrounds ?? [] }
  } catch { return null }
}
export async function fetchImages(q = ''): Promise<MediaRow[]> {
  try { return await api.get<MediaRow[]>(`/api/media?kind=image${q ? '&q=' + encodeURIComponent(q) : ''}`) } catch { return [] }
}

/** Render a simple CSS linear-gradient()/colour string to a canvas (used for catalog backgrounds without url). */
export function cssToCanvas(css: string, w: number, h: number): HTMLCanvasElement {
  const cv = mkCanvas(w, h), c = ctx2(cv)
  const m = css.match(/linear-gradient\((.*)\)\s*$/i)
  if (m) {
    const parts: string[] = []
    let depth = 0, cur = ''
    for (const ch of m[1]) {
      if (ch === '(') depth++
      if (ch === ')') depth--
      if (ch === ',' && depth === 0) { parts.push(cur.trim()); cur = '' } else cur += ch
    }
    parts.push(cur.trim())
    let angle = 180
    if (/deg$/.test(parts[0])) angle = parseFloat(parts.shift()!)
    else if (/^to /.test(parts[0])) { const t = parts.shift()!; angle = t.includes('right') ? (t.includes('bottom') ? 135 : t.includes('top') ? 45 : 90) : t.includes('left') ? (t.includes('bottom') ? 225 : t.includes('top') ? 315 : 270) : t.includes('top') ? 0 : 180 }
    const a = ((angle - 90) * Math.PI) / 180, len = Math.abs(w * Math.cos(a)) + Math.abs(h * Math.sin(a))
    const cx = w / 2, cy = h / 2
    const g = c.createLinearGradient(cx - (Math.cos(a) * len) / 2, cy - (Math.sin(a) * len) / 2, cx + (Math.cos(a) * len) / 2, cy + (Math.sin(a) * len) / 2)
    parts.forEach((p, i) => {
      const mm = p.match(/^(.*?)(?:\s+(\d+(?:\.\d+)?)%)?$/)
      try { g.addColorStop(mm && mm[2] ? parseFloat(mm[2]) / 100 : i / Math.max(1, parts.length - 1), mm ? mm[1] : p) } catch { /* bad stop */ }
    })
    c.fillStyle = g
  } else c.fillStyle = css || '#888'
  c.fillRect(0, 0, w, h)
  return cv
}
