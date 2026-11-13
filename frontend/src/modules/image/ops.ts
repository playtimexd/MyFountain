// Editor commands used by menus, shortcuts and panels.
import { Doc, hooks, makeLayer } from './doc'
import { paintOnce } from './paint'
import { selBorder, selFeather, selMorph } from './selection'
import { addDoc, curDoc, useUI } from './store'
import { createTextLayer } from './tools'
import type { Layer } from './types'
import { imgToCanvas, loadImage, mkCanvas, ctx2, canvasToBlob, luminance, parseColor } from './util'
import { openProjectFile } from './io'

export const MAX_DIM = 8192

export function newDocument(w: number, h: number, bg: 'white' | 'transparent' | string, name = 'Untitled') {
  w = Math.min(MAX_DIM, Math.max(1, Math.round(w))); h = Math.min(MAX_DIM, Math.max(1, Math.round(h)))
  const d = new Doc(w, h, name)
  const cv = mkCanvas(w, h)
  if (bg !== 'transparent') { const c = ctx2(cv); c.fillStyle = bg === 'white' ? '#ffffff' : bg; c.fillRect(0, 0, w, h) }
  d.layers.push(makeLayer({ type: 'raster', name: bg === 'transparent' ? 'Layer 1' : 'Background', canvas: cv }))
  d.activeId = d.layers[0].id
  d.savedRevision = d.revision
  addDoc(d)
  return d
}
export async function docFromCanvas(cv: HTMLCanvasElement, name: string) {
  const d = new Doc(cv.width, cv.height, name)
  d.layers.push(makeLayer({ type: 'raster', name: 'Background', canvas: cv }))
  d.activeId = d.layers[0].id
  d.savedRevision = d.revision
  addDoc(d)
  return d
}
async function fileToCanvas(f: File | Blob): Promise<HTMLCanvasElement> {
  const url = URL.createObjectURL(f)
  try { return imgToCanvas(await loadImage(url)) } finally { URL.revokeObjectURL(url) }
}
/** Open files: project json -> project; images -> each in a new document. */
export async function openFiles(files: File[]) {
  for (const f of files) {
    try {
      if (/\.json$/i.test(f.name)) { const d = await openProjectFile(f); addDoc(d) }
      else if (f.type.startsWith('image/') || /\.(png|jpe?g|webp|gif|bmp|avif)$/i.test(f.name)) await docFromCanvas(await fileToCanvas(f), f.name)
      else hooks.toast(`Unsupported file: ${f.name}`, 'error')
    } catch (e) { hooks.toast(`Failed to open ${f.name}: ${e instanceof Error ? e.message : e}`, 'error') }
  }
}
/** Place images as layers into the current doc (creates a doc if none). */
export async function placeFiles(files: File[], at?: { x: number; y: number }) {
  for (const f of files) {
    if (!f.type.startsWith('image/')) continue
    const cv = await fileToCanvas(f)
    placeCanvas(cv, f.name.replace(/\.[^.]+$/, ''), at)
  }
}
export function placeCanvas(cv: HTMLCanvasElement, name: string, at?: { x: number; y: number }, asBackground = false) {
  const d = curDoc()
  if (!d) { void docFromCanvas(cv, name); return null }
  return asBackground ? d.addImage(cv, name, at, true, 'cover', 0, null) : d.addImage(cv, name, at)
}
export async function insertImageUrl(url: string, name: string, asNewDocIfNone = true, asBackground = false) {
  const im = await loadImage(url)
  const cv = imgToCanvas(im)
  const d = curDoc()
  if (!d && asNewDocIfNone) return docFromCanvas(cv, name)
  return placeCanvas(cv, name, undefined, asBackground)
}

// ---------- clipboard ----------
export const clipboard: { cv: HTMLCanvasElement | null; x: number; y: number } = { cv: null, x: 0, y: 0 }
export function selectionPixels(d: Doc, L: Layer): { cv: HTMLCanvasElement; x: number; y: number } | null {
  const src = d.getCache(L)
  const sel = d.sel
  if (!sel) { const cv = mkCanvas(d.w, d.h); ctx2(cv).drawImage(src, 0, 0); return { cv, x: 0, y: 0 } }
  const b = sel.bbox, cv = mkCanvas(b.w, b.h), c = ctx2(cv)
  c.drawImage(src, b.x, b.y, b.w, b.h, 0, 0, b.w, b.h)
  c.globalCompositeOperation = 'destination-in'; c.drawImage(sel.canvas, b.x, b.y, b.w, b.h, 0, 0, b.w, b.h)
  return { cv, x: b.x, y: b.y }
}
export async function copySel(d: Doc, cut = false) {
  const L = d.active
  if (!L) return
  const px = selectionPixels(d, L)
  if (!px) return
  clipboard.cv = px.cv; clipboard.x = px.x; clipboard.y = px.y
  try { const b = await canvasToBlob(px.cv); await navigator.clipboard.write([new ClipboardItem({ 'image/png': b })]) } catch { /* clipboard may be unavailable */ }
  if (cut) clearSel(d, 'Cut')
  else hooks.toast('Copied', 'ok')
}
export function pasteInternal(d: Doc | undefined): boolean {
  if (!clipboard.cv) return false
  const cv = clipboard.cv
  if (!d) { void docFromCanvas(cloneIt(cv), 'Pasted'); return true }
  const L = makeLayer({ type: 'image', name: 'Pasted', canvas: cloneIt(cv) })
  L.t = { cx: clipboard.x + cv.width / 2, cy: clipboard.y + cv.height / 2, sx: 1, sy: 1, rot: 0 }
  d.addLayer(L, undefined, 'Paste')
  useUI.getState().setTool('move')
  return true
}
const cloneIt = (c: HTMLCanvasElement) => { const n = mkCanvas(c.width, c.height); ctx2(n).drawImage(c, 0, 0); return n }
export async function pasteFromEvent(e: ClipboardEvent): Promise<boolean> {
  const items = Array.from(e.clipboardData?.items ?? [])
  const img = items.find((i) => i.kind === 'file' && i.type.startsWith('image/'))
  if (img) {
    const f = img.getAsFile()
    if (f) {
      const cv = await fileToCanvas(f)
      const d = curDoc()
      if (!d) await docFromCanvas(cv, 'Pasted image')
      else { d.addImage(cv, 'Pasted image'); useUI.getState().setTool('move') }
      return true
    }
  }
  return pasteInternal(curDoc())
}

/** Ctrl+J / Ctrl+Shift+J */
export function layerViaCopy(d: Doc, cut = false) {
  const L = d.active
  if (!L) return
  if (!d.sel) { d.duplicate(L.id); return }
  const px = selectionPixels(d, L)
  if (!px) return
  d.history.group(cut ? 'Layer via Cut' : 'Layer via Copy', () => {
    const nl = makeLayer({ type: 'image', name: L.name + (cut ? ' cut' : ' copy'), canvas: px.cv })
    nl.t = { cx: px.x + px.cv.width / 2, cy: px.y + px.cv.height / 2, sx: 1, sy: 1, rot: 0 }
    if (cut) clearSel(d, 'Cut')
    d.addLayer(nl, d.layers.indexOf(L) + 1, 'New layer')
  })
}

// ---------- fill / clear ----------
export function fillSel(d: Doc, color: string, name = 'Fill', opacity = 1) {
  const L = d.active
  if (!L) return
  if (useUI.getState().recent[0] !== color) useUI.getState().pushRecent(color)
  const target: 'canvas' | 'mask' = L.editMask && L.mask ? 'mask' : 'canvas'
  let op: GlobalCompositeOperation = 'source-over', col = color
  if (target === 'mask') { col = '#ffffff'; if (luminance(parseColor(color)) < 0.5) op = 'destination-out' }
  const b = d.sel?.bbox
  paintOnce(d, L, target, name, (c) => { c.fillStyle = col; if (b) c.fillRect(b.x, b.y, b.w, b.h); else c.fillRect(0, 0, d.w, d.h) }, opacity, op)
}
export function clearSel(d: Doc, name = 'Clear') {
  const L = d.active
  if (!L) return
  if (L.editMask && L.mask) { fillSel(d, '#000000', name); return }
  paintOnce(d, L, 'canvas', name, (c) => { c.fillStyle = '#000'; c.fillRect(0, 0, d.w, d.h) }, 1, 'destination-out')
}
export function strokeSel(d: Doc, color: string, width: number) {
  if (!d.sel || !d.active) { hooks.toast('Make a selection first', 'error'); return }
  const b = selBorder(d.sel, width)
  if (!b) return
  paintOnce(d, d.active, 'canvas', 'Stroke selection', (c) => { c.drawImage(b.canvas, 0, 0); c.globalCompositeOperation = 'source-in'; c.fillStyle = color; c.fillRect(0, 0, d.w, d.h) })
}

// ---------- selection modifiers ----------
export function selModify(d: Doc, kind: 'feather' | 'grow' | 'shrink' | 'border', v: number) {
  if (!d.sel) { hooks.toast('There is no selection', 'error'); return }
  const s = d.sel
  if (kind === 'feather') d.setSel(selFeather(s, v), 'Feather selection')
  else if (kind === 'grow') d.setSel(selMorph(s, Math.round(v), true), 'Grow selection')
  else if (kind === 'shrink') d.setSel(selMorph(s, Math.round(v), false), 'Shrink selection')
  else d.setSel(selBorder(s, v), 'Border selection')
}

// ---------- layer helpers ----------
export function flipLayer(d: Doc, h: boolean) {
  const L = d.active
  if (!L || L.locked) return
  d.mutate(L, h ? 'Flip layer horizontally' : 'Flip layer vertically', (l) => { if (h) l.t.sx = -l.t.sx; else { l.t.sy = -l.t.sy } })
}
export function toggleMask(d: Doc) {
  const L = d.active
  if (!L) return
  if (!L.mask) d.addMask(L, !!d.sel)
  else d.mutate(L, 'Toggle layer mask', (l) => { l.mask!.enabled = !l.mask!.enabled })
}
export function addTextAt(d: Doc, x: number, y: number) { return createTextLayer(d, x, y) }

