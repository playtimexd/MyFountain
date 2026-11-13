// Client for the project store (backend/app/routers/projects.py).
import { api } from '../../api/client'

export type ProjectKind = 'video' | 'image' | 'anim' | 'model' | 'segment'

export interface ProjectMeta {
  id: string
  kind: ProjectKind
  name: string
  created: number
  updated: number
  opened: number | null
  rev: number
  size: number
  thumb: string | null
  thumb_url: string | null
  tags: string[]
  meta: Record<string, any>
  parent_id: string | null
}

export interface ActivityRow {
  id: string
  area: string
  kind: string
  title: string
  status: 'done' | 'error' | 'cancelled'
  error: string | null
  result: { media?: { id: string; kind: string; name: string; url: string; thumb: string; duration?: number; source_url?: string }[]; info?: Record<string, unknown> }
  created: number
  finished: number
}

const P = '/api/projects'

export const projectsApi = {
  list: (kind: ProjectKind, q = '', sort = 'opened') =>
    api.get<ProjectMeta[]>(`${P}?kind=${kind}&sort=${sort}&limit=500${q ? `&q=${encodeURIComponent(q)}` : ''}`),
  get: (id: string) => api.get<ProjectMeta>(`${P}/${id}`),
  create: (kind: ProjectKind, name: string, doc?: unknown, meta: Record<string, unknown> = {}, tags: string[] = []) =>
    api.post<ProjectMeta>(P, { kind, name, doc: doc ?? null, meta, tags }),
  /** The document exactly as it was saved (parsed JSON). */
  doc: async <T = any>(id: string): Promise<T> => {
    const r = await fetch(`${P}/${id}/doc`, { cache: 'no-store' })
    if (!r.ok) throw new Error(r.status === 404 ? 'This project has no saved document yet' : `${r.status} ${r.statusText}`)
    return r.json() as Promise<T>
  },
  /** Save the document body verbatim. `meta` is merged into the project's meta. */
  saveDoc: async (id: string, body: string | Blob, opts: { name?: string; meta?: Record<string, unknown>; rev?: number } = {}) => {
    const qs = new URLSearchParams()
    if (opts.name) qs.set('name', opts.name)
    if (opts.meta) qs.set('meta', JSON.stringify(opts.meta))
    if (opts.rev != null) qs.set('rev', String(opts.rev))
    const r = await fetch(`${P}/${id}/doc?${qs}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body })
    if (!r.ok) {
      let msg = `${r.status} ${r.statusText}`
      try { const j = await r.json(); if (j.detail) msg = String(j.detail) } catch { /* noop */ }
      throw Object.assign(new Error(msg), { status: r.status })
    }
    return r.json() as Promise<ProjectMeta>
  },
  saveThumb: async (id: string, blob: Blob) => {
    const r = await fetch(`${P}/${id}/thumb`, { method: 'PUT', headers: { 'Content-Type': blob.type || 'image/jpeg' }, body: blob })
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
    return r.json() as Promise<ProjectMeta>
  },
  patch: (id: string, patch: { name?: string; tags?: string[]; meta?: Record<string, unknown> }) => api.patch<ProjectMeta>(`${P}/${id}`, patch),
  open: (id: string) => api.post<ProjectMeta>(`${P}/${id}/open`),
  duplicate: (id: string, name?: string) => api.post<ProjectMeta>(`${P}/${id}/duplicate`, { name: name ?? null }),
  remove: (id: string) => api.del<{ ok: boolean; trash: string | null }>(`${P}/${id}`),
  activity: (areas: string[], limit = 60) => api.get<ActivityRow[]>(`${P}/activity?area=${areas.join(',')}&limit=${limit}`),
  removeActivity: (id: string) => api.del(`${P}/activity/${id}`),
  clearActivity: (area: string) => api.del(`${P}/activity?area=${encodeURIComponent(area)}`),
}

/** Downscale any canvas-like source to a JPEG thumbnail (longest edge `max`). */
export async function canvasThumb(src: CanvasImageSource & { width: number; height: number }, max = 360, bg = '#14161c'): Promise<Blob | null> {
  const w0 = src.width, h0 = src.height
  if (!w0 || !h0) return null
  const k = Math.min(1, max / Math.max(w0, h0))
  const c = document.createElement('canvas')
  c.width = Math.max(1, Math.round(w0 * k)); c.height = Math.max(1, Math.round(h0 * k))
  const ctx = c.getContext('2d')!
  ctx.fillStyle = bg; ctx.fillRect(0, 0, c.width, c.height)
  try { ctx.drawImage(src, 0, 0, c.width, c.height) } catch { return null }
  return new Promise((res) => c.toBlob((b) => res(b), 'image/jpeg', 0.82))
}

export const timeAgo = (t: number | null | undefined) => {
  if (!t) return '—'
  const s = Math.max(0, Date.now() / 1000 - t)
  if (s < 45) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  if (s < 86400 * 7) return `${Math.round(s / 86400)} d ago`
  return new Date(t * 1000).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}
