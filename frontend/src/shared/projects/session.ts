// One "current project" + debounced autosave to the project store, per editor area.
// The area supplies how to serialize its document (and optionally a thumbnail); this module handles timing,
// in-flight coalescing, save status and remembering which project is open across reloads.
import { create, type StoreApi, type UseBoundStore } from 'zustand'
import { projectsApi, type ProjectKind } from './api'

export type SaveStatus = 'idle' | 'pending' | 'saving' | 'saved' | 'error'
export interface SessionState {
  id: string | null          // open project (null = nothing open yet)
  name: string
  home: boolean              // start screen visible
  status: SaveStatus
  savedAt: number            // ms
  error: string | null
}
export interface Serialized { body: string | Blob; name?: string; meta?: Record<string, unknown> }

export interface ProjectSession {
  kind: ProjectKind
  use: UseBoundStore<StoreApi<SessionState>>
  /** Mark the document changed; saves after `delay` ms of quiet. */
  schedule: () => void
  /** Save now (resolves when stored). `thumb` forces a fresh thumbnail. */
  flush: (thumb?: boolean) => Promise<void>
  /** Switch the session to another project id (does not load anything — the area does that). */
  setCurrent: (id: string | null, name?: string) => void
  showHome: (on: boolean) => void
}

export function createProjectSession(opts: {
  kind: ProjectKind
  serialize: () => Promise<Serialized | null> | Serialized | null
  thumbnail?: () => Promise<Blob | null>
  delay?: number
  thumbEveryMs?: number
}): ProjectSession {
  const KEY = `cutlab.${opts.kind}.projectId`
  const delay = opts.delay ?? 1500
  const thumbEvery = opts.thumbEveryMs ?? 20000
  let saved: { id: string | null; name: string } = { id: null, name: '' }
  try { saved = JSON.parse(localStorage.getItem(KEY) || 'null') || saved } catch { /* ignore */ }

  const use = create<SessionState>(() => ({ id: saved.id, name: saved.name, home: !saved.id, status: 'idle', savedAt: 0, error: null }))
  const remember = () => { const s = use.getState(); try { localStorage.setItem(KEY, JSON.stringify({ id: s.id, name: s.name })) } catch { /* ignore */ } }

  let timer: ReturnType<typeof setTimeout> | undefined
  let inflight: Promise<void> | null = null
  let again = false
  let lastThumb = 0

  const flush = async (forceThumb = false): Promise<void> => {
    clearTimeout(timer)
    if (inflight) { again = true; return inflight }
    const id = use.getState().id
    if (!id) return
    inflight = (async () => {
      use.setState({ status: 'saving' })
      try {
        const s = await opts.serialize()
        if (!s || use.getState().id !== id) { use.setState({ status: 'idle' }); return }
        const meta = await projectsApi.saveDoc(id, s.body, { name: s.name, meta: s.meta })
        use.setState({ status: 'saved', savedAt: Date.now(), error: null, name: meta.name })
        remember()
        if (opts.thumbnail && (forceThumb || Date.now() - lastThumb > thumbEvery)) {
          lastThumb = Date.now()
          const b = await opts.thumbnail().catch(() => null)
          if (b && use.getState().id === id) await projectsApi.saveThumb(id, b).catch(() => undefined)
        }
      } catch (e: any) {
        const gone = e?.status === 404
        use.setState({ status: 'error', error: gone ? 'This project was deleted. Create a new project to keep your work.' : `Autosave failed: ${e?.message || e}` })
      } finally {
        inflight = null
        if (again) { again = false; schedule() }
      }
    })()
    return inflight
  }

  const schedule = () => {
    if (!use.getState().id) return
    clearTimeout(timer)
    if (use.getState().status !== 'saving') use.setState({ status: 'pending' })
    timer = setTimeout(() => { void flush() }, delay)
  }

  const setCurrent = (id: string | null, name = '') => {
    clearTimeout(timer)
    again = false
    lastThumb = 0
    use.setState({ id, name, status: 'idle', error: null, home: id ? false : use.getState().home })
    remember()
  }

  // save when the page goes to the background (tab switch / window close)
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden' && use.getState().status === 'pending') void flush(true) })
  }

  return { kind: opts.kind, use, schedule, flush, setCurrent, showHome: (on) => use.setState({ home: on }) }
}
