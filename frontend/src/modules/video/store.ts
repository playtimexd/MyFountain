// Zustand store for the video editor document + UI state. History is snapshot-based (projects are immutable).
import { create } from 'zustand'
import type { Clip, Op, Project, TextStyle, TransitionIn } from '../../types/project'
import { DEFAULT_ADJUST, DEFAULT_TRANSFORM, clipDuration, clipEnd, projectDuration } from '../../types/project'
import type { MediaRow } from '../../types/api'
import { applyOpsToProject, clampSpeed, createTrack, findClip, kindForType, makeClip, newProject, normalizeProject, nearestFree, isFree, opsAssetIds, smartStart, uid } from './ops'
import { getMedia, useMedia } from './data'
import type { Dropped } from './opsSanitize'
import { clock } from './engine/clock'

const LS_KEY = 'cutlab.video.project.v1'
const MAX_HISTORY = 120

interface VState {
  project: Project
  past: Project[]
  future: Project[]
  lastKey: string | null
  lastTime: number
  selection: string[]
  playhead: number
  playing: boolean
  zoom: number            // timeline px / second
  magnet: boolean
  ripple: boolean
  toast: string | null

  mutate: (fn: (p: Project) => Project, key?: string) => void
  setProject: (p: Project, resetHistory?: boolean) => void
  undo: () => void
  redo: () => void
  select: (ids: string[], additive?: boolean) => void
  setZoom: (z: number) => void
  say: (msg: string) => void

  applyOps: (ops: Op[]) => Promise<string[]>
  /** Like applyOps but for untrusted (agent / bridge) ops: returns how many applied and which were dropped. */
  applyOpsReport: (ops: unknown[]) => Promise<{ created: string[]; applied: number; dropped: Dropped[] }>
  patchClips: (ids: string[], patch: (c: Clip) => Partial<Clip>, key?: string) => void
  setClipField: (ids: string[], patch: Partial<Clip>, key?: string) => void
  setTransform: (ids: string[], patch: Partial<Clip['transform']>, key?: string) => void
  setAdjust: (ids: string[], patch: Partial<Clip['adjust']>, key?: string) => void
  setText: (ids: string[], patch: Partial<TextStyle>, key?: string) => void
  setTransition: (ids: string[], tr: TransitionIn | undefined) => void
  splitAtPlayhead: () => void
  deleteSelected: (ripple?: boolean) => void
  duplicateSelected: () => void
  /** Split audio: move the sound of the selected video clips onto audio-track clips (one undo step). */
  detachAudioSelected: () => void
  addMedia: (m: MediaRow, opts?: { start?: number; track?: string; in?: number; out?: number }) => void
  addSticker: (id: string, name: string, start?: number) => void
  addTextPreset: (style: Partial<TextStyle>, name?: string, start?: number) => void
  updateTrack: (id: string, patch: Partial<{ muted: boolean; hidden: boolean; locked: boolean; name: string }>) => void
  addTrack: (kind: 'video' | 'audio' | 'text') => string
  removeTrack: (id: string) => void
  setCanvas: (w: number, h: number, fps?: number) => void
}

let gestureSeq = 0
export const newGesture = () => `g:${++gestureSeq}`

const loadSaved = (): Project => {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) { const p = normalizeProject(JSON.parse(raw)); if (p) return p }
  } catch { /* ignore */ }
  return newProject()
}

export const useProject = create<VState>((set, get) => ({
  project: loadSaved(), past: [], future: [], lastKey: null, lastTime: 0,
  selection: [], playhead: 0, playing: false, zoom: 80, magnet: true, ripple: false, toast: null,

  say: (msg) => { set({ toast: msg }); setTimeout(() => { if (get().toast === msg) set({ toast: null }) }, 2600) },

  mutate: (fn, key) => {
    const s = get()
    const next = fn(s.project)
    if (next === s.project) return
    const now = performance.now()
    const merge = !!key && key === s.lastKey && (key.startsWith('g:') || now - s.lastTime < 1200)
    const ids = new Set<string>()
    for (const t of next.tracks) for (const c of t.clips) ids.add(c.id)
    const sel = s.selection.filter((id) => ids.has(id))
    set({
      project: next,
      past: merge ? s.past : [...s.past, s.project].slice(-MAX_HISTORY),
      future: [], lastKey: key ?? null, lastTime: now,
      selection: sel.length === s.selection.length ? s.selection : sel,
    })
  },

  setProject: (p, reset = true) => {
    set({ project: p, selection: [], ...(reset ? { past: [], future: [], lastKey: null } : {}) })
  },

  undo: () => {
    const s = get()
    if (!s.past.length) return
    const prev = s.past[s.past.length - 1]
    set({ project: prev, past: s.past.slice(0, -1), future: [s.project, ...s.future].slice(0, MAX_HISTORY), lastKey: null, selection: s.selection.filter((id) => !!findClip(prev, id)) })
  },
  redo: () => {
    const s = get()
    if (!s.future.length) return
    const nx = s.future[0]
    set({ project: nx, future: s.future.slice(1), past: [...s.past, s.project].slice(-MAX_HISTORY), lastKey: null, selection: s.selection.filter((id) => !!findClip(nx, id)) })
  },

  select: (ids, additive) => {
    if (!additive) { set({ selection: ids }); return }
    const cur = new Set(get().selection)
    for (const id of ids) { if (cur.has(id)) cur.delete(id); else cur.add(id) }
    set({ selection: [...cur] })
  },
  setZoom: (z) => set({ zoom: Math.min(600, Math.max(4, z)) }),

  applyOps: async (ops) => (await get().applyOpsReport(ops)).created,

  applyOpsReport: async (ops) => {
    // make sure every referenced media row is cached before the (sync) reducer runs
    const missing = opsAssetIds(ops).filter((id) => !getMedia(id))
    if (missing.length) await Promise.all(missing.map((id) => useMedia.getState().ensure(id)))
    let res = { created: [] as string[], applied: 0, dropped: [] as Dropped[] }
    get().mutate((p) => {
      const r = applyOpsToProject(p, ops, { media: getMedia })
      res = { created: r.created, applied: r.applied, dropped: r.dropped }
      return r.applied ? r.project : p
    })
    if (res.created.length) set({ selection: res.created.slice(-1) })
    return res
  },

  patchClips: (ids, patch, key) => {
    const set_ = new Set(ids)
    get().mutate((p) => {
      let changed = false
      const tracks = p.tracks.map((t) => {
        if (!t.clips.some((c) => set_.has(c.id))) return t
        changed = true
        return { ...t, clips: t.clips.map((c) => (set_.has(c.id) ? { ...c, ...patch(c) } : c)) }
      })
      return changed ? { ...p, tracks } : p
    }, key)
  },
  setClipField: (ids, patch, key) => get().patchClips(ids, () => patch, key),
  setTransform: (ids, patch, key) => get().patchClips(ids, (c) => ({ transform: { ...c.transform, ...patch } }), key),
  setAdjust: (ids, patch, key) => get().patchClips(ids, (c) => ({ adjust: { ...c.adjust, ...patch }, filter: undefined }), key),
  setText: (ids, patch, key) => get().patchClips(ids, (c) => (c.text ? { text: { ...c.text, ...patch }, name: patch.content != null ? patch.content.slice(0, 16) : c.name } : {}), key),
  setTransition: (ids, tr) => get().patchClips(ids, () => ({ transitionIn: tr })),

  splitAtPlayhead: () => {
    const s = get()
    const t = clock.time
    let targets = s.selection.filter((id) => { const f = findClip(s.project, id); return f && t > f.clip.start && t < clipEnd(f.clip) })
    if (!targets.length) { // nothing selected under playhead -> split every unlocked clip under playhead
      targets = []
      for (const tr of s.project.tracks) if (!tr.locked) for (const c of tr.clips) if (t > c.start + 0.02 && t < clipEnd(c) - 0.02) targets.push(c.id)
    }
    if (!targets.length) return
    void get().applyOps(targets.map((clipId) => ({ op: 'split' as const, clipId, at: t })))
  },

  deleteSelected: (ripple) => {
    const s = get()
    if (!s.selection.length) return
    const ids = new Set(s.selection)
    get().mutate((p) => {
      // optional ripple: shift later clips of the same track left by removed duration
      const tracks = p.tracks.map((t) => {
        if (t.locked || !t.clips.some((c) => ids.has(c.id))) return t
        let clips = t.clips
        if (ripple) {
          const rem = t.clips.filter((c) => ids.has(c.id)).sort((a, b) => a.start - b.start)
          clips = t.clips.filter((c) => !ids.has(c.id)).map((c) => {
            let shift = 0
            for (const r of rem) if (r.start < c.start + 1e-6) shift += clipDuration(r)
            return shift ? { ...c, start: Math.max(0, c.start - shift) } : c
          })
        } else clips = t.clips.filter((c) => !ids.has(c.id))
        return { ...t, clips }
      })
      return { ...p, tracks }
    })
    set({ selection: [] })
  },

  duplicateSelected: () => {
    const s = get()
    const created: string[] = []
    get().mutate((p) => {
      const tracks = p.tracks.map((t) => ({ ...t, clips: t.clips.slice() }))
      for (const id of s.selection) {
        const f = findClip({ ...p, tracks }, id)
        if (!f) continue
        const dur = clipDuration(f.clip)
        const dest = tracks[f.ti]
        const start = nearestFree(dest, clipEnd(f.clip), dur)
        const copy: Clip = { ...f.clip, id: uid(), start, transform: { ...f.clip.transform }, adjust: { ...f.clip.adjust }, text: f.clip.text ? { ...f.clip.text } : undefined }
        dest.clips.push(copy)
        created.push(copy.id)
      }
      tracks.forEach((t) => t.clips.sort((a, b) => a.start - b.start))
      return { ...p, tracks }
    })
    if (created.length) set({ selection: created })
  },

  detachAudioSelected: () => {
    const s = get()
    const ids = selectedClips(s).filter((c) => c.type === 'video' && !c.audioDetached && getMedia(c.assetId || '')?.has_audio !== false).map((c) => c.id)
    if (!ids.length) { get().say('Select a video clip with sound to split its audio'); return }
    void get().applyOps(ids.map((clipId) => ({ op: 'detach_audio' as const, clipId }))).then((created) => {
      if (created.length) get().say(created.length > 1 ? `Audio split from ${created.length} clips` : 'Audio split onto its own clip')
    })
  },

  addMedia: (m, opts) => {
    useMedia.getState().upsert(m)
    const p = get().project
    const dur = m.kind === 'image' ? 5 : m.duration || 5
    const kind = kindForType(m.kind)
    const start = opts?.start ?? smartStart(p, kind, clock.time, dur)
    void get().applyOps([{ op: 'add_clip', assetId: m.id, start, track: opts?.track, in: opts?.in, out: opts?.out }])
  },

  addSticker: (id, name, start) => {
    const st = start ?? clock.time
    const created: string[] = []
    get().mutate((p) => {
      const tracks = p.tracks.map((t) => ({ ...t, clips: t.clips.slice() }))
      const q = { ...p, tracks }
      // stickers are overlays: prefer an upper video track that is free, else create one
      let track = [...q.tracks].reverse().find((t) => t.kind === 'video' && !t.locked && t !== q.tracks.find((x) => x.kind === 'video') && isFree(t, st, st + 4))
      if (!track) track = createTrack(q, 'video')
      const clip = makeClip({ type: 'sticker', assetId: id, start: st, in: 0, out: 4, name, transform: { ...DEFAULT_TRANSFORM, scale: 0.3 } })
      track.clips.push(clip)
      created.push(clip.id)
      return q
    })
    set({ selection: created })
  },

  addTextPreset: (style, name, start) => {
    const dur = 3
    const st = start ?? clock.time
    const ids = get().applyOps([{ op: 'add_text', text: 'Text', start: st, duration: dur, style }])
    void ids.then((c) => { if (name && c[0]) get().patchClips([c[0]], () => ({ name })) })
  },

  updateTrack: (id, patch) => get().mutate((p) => {
    const t = p.tracks.find((x) => x.id === id)
    if (!t || (Object.keys(patch) as (keyof typeof patch)[]).every((k) => t[k] === patch[k])) return p
    return { ...p, tracks: p.tracks.map((x) => (x.id === id ? { ...x, ...patch } : x)) }
  }),
  addTrack: (kind) => { let id = ''; get().mutate((p) => { const q = { ...p, tracks: p.tracks.map((t) => ({ ...t })) }; id = createTrack(q, kind).id; return q }); return id },
  removeTrack: (id) => get().mutate((p) => ({ ...p, tracks: p.tracks.filter((t) => t.id !== id) })),
  setCanvas: (w, h, fps) => get().mutate((p) => ({ ...p, width: Math.max(16, Math.round(w)), height: Math.max(16, Math.round(h)), fps: fps ?? p.fps })),
}))

// ---------------------------------------------------------------- helpers shared with UI
export const selectedClips = (s: Pick<VState, 'project' | 'selection'>): Clip[] => {
  const out: Clip[] = []
  for (const t of s.project.tracks) for (const c of t.clips) if (s.selection.includes(c.id)) out.push(c)
  return out
}
export const duration = () => projectDuration(useProject.getState().project)
export { clampSpeed, DEFAULT_ADJUST }

// ---------------------------------------------------------------- persistence
let saveTimer: ReturnType<typeof setTimeout> | undefined
useProject.subscribe((s, prev) => {
  if (s.project === prev.project) return
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    try { localStorage.setItem(LS_KEY, JSON.stringify(useProject.getState().project)) } catch { /* quota */ }
  }, 500)
})
