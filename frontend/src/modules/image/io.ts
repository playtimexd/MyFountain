// File I/O: project serialisation, autosave (IndexedDB), export, library upload.
import { api } from '../../api/client'
import { emit } from '../../shared/bus'
import type { MediaRow } from '../../types/api'
import { Doc, hooks, makeLayer } from './doc'
import type { AdjustItem, BlendMode, Effects, LayerType, ShapeProps, TextProps, Transform } from './types'
import { canvasToBlob, canvasToDataURL, download, imgToCanvas, loadImage, mkCanvas } from './util'
import { ensureFont } from './render'
import { defaultFx } from './fx'
import { docPid, docSavedRev } from './projectIds'

interface LayerJSON {
  id: string; name: string; type: LayerType; visible: boolean; locked: boolean; opacity: number; blend: BlendMode; t: Transform
  clip: boolean; adjust: AdjustItem[]; fx: Effects; text?: TextProps; shape?: ShapeProps
  png?: string; maskPng?: string; maskEnabled?: boolean; w?: number; h?: number
  parent?: string; collapsed?: boolean; pass?: boolean
}
export interface ProjectJSON { format: 'cutlab-psd'; version: 1; name: string; w: number; h: number; active: number; layers: LayerJSON[]; guides?: { id: string; axis: 'h' | 'v'; pos: number }[] }

export async function serializeDoc(d: Doc): Promise<ProjectJSON> {
  const layers: LayerJSON[] = []
  for (const L of d.layers) {
    const j: LayerJSON = {
      id: L.id, name: L.name, type: L.type, visible: L.visible, locked: L.locked, opacity: L.opacity, blend: L.blend, t: { ...L.t },
      clip: L.clip, adjust: JSON.parse(JSON.stringify(L.adjust)), fx: JSON.parse(JSON.stringify(L.fx)),
      text: L.text && JSON.parse(JSON.stringify(L.text)), shape: L.shape && JSON.parse(JSON.stringify(L.shape)),
      parent: L.parent, collapsed: L.collapsed, pass: L.pass,
    }
    if (L.type === 'raster' || L.type === 'image') {
      if (L.savedPx !== L.px || !L.savedUrl) { L.savedUrl = await canvasToDataURL(L.canvas); L.savedPx = L.px }
      j.png = L.savedUrl; j.w = L.canvas.width; j.h = L.canvas.height
    }
    if (L.mask) {
      if (L.savedMaskPx !== L.px || !L.savedMaskUrl) { L.savedMaskUrl = await canvasToDataURL(L.mask.canvas); L.savedMaskPx = L.px }
      j.maskPng = L.savedMaskUrl; j.maskEnabled = L.mask.enabled
    }
    layers.push(j)
  }
  return { format: 'cutlab-psd', version: 1, name: d.name, w: d.w, h: d.h, active: d.activeIndex, layers, guides: d.guides.map((g) => ({ ...g })) }
}

export async function deserializeDoc(p: ProjectJSON): Promise<Doc> {
  if (p.format !== 'cutlab-psd') throw new Error('Not a CutLab project file')
  if (!(p.w > 0 && p.h > 0 && p.w <= 20000 && p.h <= 20000)) throw new Error('Invalid project size')
  const d = new Doc(p.w, p.h, p.name)
  for (const j of p.layers) {
    let canvas = mkCanvas(2, 2)
    if (j.png) canvas = imgToCanvas(await loadImage(j.png))
    const L = makeLayer({
      id: j.id, type: j.type, name: j.name, visible: j.visible, locked: j.locked, opacity: j.opacity, blend: j.blend, t: j.t, clip: j.clip,
      adjust: j.adjust ?? [], fx: { ...defaultFx(), ...(j.fx ?? {}) }, text: j.text, shape: j.shape, canvas,
      parent: j.parent, collapsed: j.collapsed, pass: j.pass,
    })
    if (j.maskPng) L.mask = { canvas: imgToCanvas(await loadImage(j.maskPng)), enabled: j.maskEnabled !== false }
    if (j.type === 'text' && j.text) { await ensureFont(j.text.font); d.refreshVector(L, false) }
    else if (j.type === 'shape') d.refreshVector(L, false)
    d.layers.push(L)
  }
  d.guides = (p.guides ?? []).map((g) => ({ ...g }))
  d.activeId = d.layers[Math.min(Math.max(0, p.active), d.layers.length - 1)]?.id ?? ''
  d.compDirty = true
  d.savedRevision = d.revision
  return d
}

export async function saveProject(d: Doc) {
  const p = await serializeDoc(d)
  download(new Blob([JSON.stringify(p)], { type: 'application/json' }), `${d.name.replace(/\.[^.]+$/, '')}.cutlab-psd.json`)
  d.savedRevision = d.revision
  hooks.ui()
  hooks.toast('Project saved', 'ok')
}
export async function openProjectFile(f: File): Promise<Doc> {
  return deserializeDoc(JSON.parse(await f.text()))
}

// ---------- export ----------
export type ExportFmt = 'png' | 'jpg' | 'webp'
export async function exportBlob(d: Doc, fmt: ExportFmt, quality = 0.92, scale = 1): Promise<Blob> {
  const cv = d.flatten(fmt === 'jpg' ? '#ffffff' : undefined, scale)
  return canvasToBlob(cv, fmt === 'jpg' ? 'image/jpeg' : fmt === 'webp' ? 'image/webp' : 'image/png', quality)
}
export async function exportFile(d: Doc, fmt: ExportFmt, quality: number, scale: number) {
  const b = await exportBlob(d, fmt, quality, scale)
  download(b, `${d.name.replace(/\.[^.]+$/, '')}.${fmt}`)
  hooks.toast(`Exported ${fmt.toUpperCase()} (${Math.round(b.size / 1024)} KB)`, 'ok')
}
export async function copyToClipboard(d: Doc) {
  try {
    const b = await exportBlob(d, 'png')
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': b })])
    hooks.toast('Copied to clipboard', 'ok')
  } catch (e) {
    hooks.toast('Copy failed: ' + (e instanceof Error ? e.message : e), 'error')
  }
}
export async function sendToLibrary(d: Doc, toTimeline: boolean) {
  try {
    const b = await exportBlob(d, 'png')
    const file = new File([b], `${d.name.replace(/\.[^.]+$/, '') || 'image'}-${Date.now().toString(36)}.png`, { type: 'image/png' })
    const media = await api.upload<MediaRow>('/api/media/upload', file)
    emit('cutlab:media-changed', { media })
    if (toTimeline) emit('cutlab:add-to-timeline', { media })
    hooks.toast(toTimeline ? 'Sent to the video editor' : 'Sent to the library', 'ok')
    return media
  } catch (e) {
    hooks.toast('Send failed: ' + (e instanceof Error ? e.message : e), 'error')
    return null
  }
}

// ---------- autosave (IndexedDB; localStorage keeps a tiny marker) ----------
const DB = 'cutlab-image', STORE = 'kv'
function idb(): Promise<IDBDatabase> {
  return new Promise((res, rej) => {
    const r = indexedDB.open(DB, 1)
    r.onupgradeneeded = () => r.result.createObjectStore(STORE)
    r.onsuccess = () => res(r.result)
    r.onerror = () => rej(r.error)
  })
}
async function kvSet(key: string, val: unknown) {
  const db = await idb()
  await new Promise<void>((res, rej) => { const tx = db.transaction(STORE, 'readwrite'); tx.objectStore(STORE).put(val, key); tx.oncomplete = () => res(); tx.onerror = () => rej(tx.error) })
  db.close()
}
async function kvGet<T>(key: string): Promise<T | undefined> {
  const db = await idb()
  const v = await new Promise<T | undefined>((res, rej) => { const q = db.transaction(STORE).objectStore(STORE).get(key); q.onsuccess = () => res(q.result as T); q.onerror = () => rej(q.error) })
  db.close()
  return v
}
export async function autosave(docs: Doc[], cur: string) {
  try {
    docs = docs.filter((d) => d.w > 0 && d.h > 0)
    if (!docs.length) { await kvSet('autosave', null); localStorage.removeItem('cutlab.image.autosave'); return }
    const list = await Promise.all(docs.map((d) => serializeDoc(d)))
    const size = list.reduce((a, p) => a + p.layers.reduce((b, l) => b + (l.png?.length ?? 0) + (l.maskPng?.length ?? 0), 0), 0)
    if (size > 300e6) { console.warn('[image] autosave skipped: too large'); return }
    await kvSet('autosave', { docs: list, cur: docs.findIndex((d) => d.id === cur), pids: docs.map((d) => docPid.get(d) ?? null) })
    try { localStorage.setItem('cutlab.image.autosave', JSON.stringify({ t: Date.now(), n: docs.length, bytes: size })) } catch { /* quota */ }
  } catch (e) { console.warn('[image] autosave failed', e) }
}
export async function restoreAutosave(): Promise<{ docs: Doc[]; cur: number } | null> {
  try {
    const v = await kvGet<{ docs: ProjectJSON[]; cur: number; pids?: (string | null)[] } | null>('autosave')
    if (!v || !v.docs?.length) return null
    const docs: Doc[] = []
    for (let i = 0; i < v.docs.length; i++) {
      try {
        const d = await deserializeDoc(v.docs[i])
        const pid = v.pids?.[i]
        if (pid) { docPid.set(d, pid); docSavedRev.set(d, d.revision) } // same project as before the reload
        docs.push(d)
      } catch (e) { console.warn('[image] skip bad autosaved doc', e) }
    }
    return docs.length ? { docs, cur: v.cur } : null
  } catch (e) { console.warn('[image] restore failed', e); return null }
}

