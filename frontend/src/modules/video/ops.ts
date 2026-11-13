// Pure document operations on the Project schema. Everything here is immutable-ish:
// tracks/clips arrays are copied, clips are replaced (never mutated) so React.memo keeps working.
import type { Clip, ClipType, Keyframe, Op, Project, TextStyle, Track, TrackKind } from '../../types/project'
import { DEFAULT_ADJUST, DEFAULT_TEXT, DEFAULT_TRANSFORM, clipDuration, clipEnd } from '../../types/project'
import type { MediaRow } from '../../types/api'
import { clipTraits } from './clipTypes'
import { checkOp } from './opsSanitize'
import type { Dropped } from './opsSanitize'

export const uid = (p = 'c') => p + Math.random().toString(36).slice(2, 8) + Date.now().toString(36).slice(-3)
export const EPS = 1e-4
export const MIN_DUR = 0.1

export interface OpCtx {
  media: (id: string) => MediaRow | undefined
}

export function newProject(): Project {
  return {
    version: 1, name: 'Untitled project', width: 1920, height: 1080, fps: 30, background: '#000000',
    tracks: [
      { id: 'v1', kind: 'video', name: 'Video 1', muted: false, hidden: false, locked: false, clips: [] },
      { id: 'a1', kind: 'audio', name: 'Audio 1', muted: false, hidden: false, locked: false, clips: [] },
    ],
  }
}

export function makeClip(c: Partial<Clip> & { type: ClipType }): Clip {
  return {
    id: uid(), start: 0, in: 0, out: 5, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0, fit: 'contain',
    transform: { ...DEFAULT_TRANSFORM }, adjust: { ...DEFAULT_ADJUST },
    ...c,
  } as Clip
}

/** Projects saved by earlier builds used Chinese default track names; show the English equivalents. */
function legacyTrackName(n: string): string {
  const m = /^(视频|音频|文字|轨道) ?(\d+)$/.exec(n)
  if (m) return `${({ 视频: 'Video', 音频: 'Audio', 文字: 'Text', 轨道: 'Track' } as Record<string, string>)[m[1]]} ${m[2]}`
  return n === '字幕' ? 'Subtitles' : n === '未命名项目' ? 'Untitled project' : n
}

/** Make an arbitrary parsed JSON into a valid Project (fills defaults, drops garbage). */
export function normalizeProject(raw: any): Project | null {
  if (!raw || typeof raw !== 'object' || !Array.isArray(raw.tracks)) return null
  const base = newProject()
  const tracks: Track[] = raw.tracks.map((t: any, i: number): Track => ({
    id: String(t.id ?? uid('t')), kind: (['video', 'audio', 'text'].includes(t.kind) ? t.kind : 'video') as TrackKind,
    name: legacyTrackName(String(t.name ?? `Track ${i + 1}`)), muted: !!t.muted, hidden: !!t.hidden, locked: !!t.locked,
    clips: (Array.isArray(t.clips) ? t.clips : []).map((c: any): Clip => {
      const type: ClipType = ['video', 'image', 'audio', 'text', 'sticker'].includes(c.type) ? c.type : 'video'
      const k = makeClip({ type })
      return {
        ...k, ...c, id: String(c.id ?? k.id), type,
        transform: { ...DEFAULT_TRANSFORM, ...(c.transform || {}) },
        adjust: { ...DEFAULT_ADJUST, ...(c.adjust || {}) },
        text: c.text ? { ...DEFAULT_TEXT, ...c.text } : undefined,
        speed: c.speed > 0 ? c.speed : 1,
      }
    }),
  }))
  return {
    version: 1, name: legacyTrackName(String(raw.name ?? base.name)), width: +raw.width || base.width, height: +raw.height || base.height,
    fps: +raw.fps || base.fps, background: String(raw.background ?? '#000000'), tracks,
  }
}

// ------------------------------------------------------------ lookup helpers
export function findClip(p: Project, id: string): { track: Track; ti: number; ci: number; clip: Clip } | null {
  for (let ti = 0; ti < p.tracks.length; ti++) {
    const ci = p.tracks[ti].clips.findIndex((c) => c.id === id)
    if (ci >= 0) return { track: p.tracks[ti], ti, ci, clip: p.tracks[ti].clips[ci] }
  }
  return null
}
export const allClips = (p: Project): Clip[] => p.tracks.flatMap((t) => t.clips)
export const hasClips = (p: Project) => p.tracks.some((t) => t.clips.length > 0)

function fork(p: Project): Project {
  return { ...p, tracks: p.tracks.map((t) => ({ ...t, clips: t.clips.slice() })) }
}
function setClip(p: Project, id: string, fn: (c: Clip) => Clip): boolean {
  for (const t of p.tracks) {
    const i = t.clips.findIndex((c) => c.id === id)
    if (i >= 0) { t.clips[i] = fn(t.clips[i]); return true }
  }
  return false
}
function removeClip(p: Project, id: string): Clip | null {
  for (const t of p.tracks) {
    const i = t.clips.findIndex((c) => c.id === id)
    if (i >= 0) return t.clips.splice(i, 1)[0]
  }
  return null
}
function sortTracks(p: Project) {
  for (const t of p.tracks) t.clips.sort((a, b) => a.start - b.start)
}

export const kindForType = (t: ClipType): TrackKind => clipTraits(t).track

export function isFree(t: Track, start: number, end: number, ignore?: Set<string>): boolean {
  return !t.clips.some((c) => !(ignore && ignore.has(c.id)) && c.start < end - EPS && clipEnd(c) > start + EPS)
}

/** Nearest start >= 0 to `want` where [s, s+dur) doesn't overlap clips of the track (ignoring `ignore`). */
export function nearestFree(t: Track, want: number, dur: number, ignore?: Set<string>): number {
  want = Math.max(0, want)
  if (isFree(t, want, want + dur, ignore)) return want
  const cands: number[] = [0]
  for (const c of t.clips) {
    if (ignore && ignore.has(c.id)) continue
    cands.push(clipEnd(c), c.start - dur)
  }
  let best = Infinity, bestD = Infinity
  for (const s of cands) {
    if (s < -EPS) continue
    if (!isFree(t, s, s + dur, ignore)) continue
    const d = Math.abs(s - want)
    if (d < bestD) { bestD = d; best = s }
  }
  return best === Infinity ? want : Math.max(0, best)
}

/** Insert a new track keeping "video tracks below text tracks" invariant. Returns the track (already inserted). */
export function createTrack(p: Project, kind: TrackKind): Track {
  const n = p.tracks.filter((t) => t.kind === kind).length + 1
  const label = kind === 'video' ? 'Video' : kind === 'audio' ? 'Audio' : 'Text'
  const t: Track = { id: uid('t'), kind, name: `${label} ${n}`, muted: false, hidden: false, locked: false, clips: [] }
  if (kind === 'video') {
    const firstText = p.tracks.findIndex((x) => x.kind === 'text')
    let idx = p.tracks.length
    if (firstText >= 0) idx = firstText
    else { // before audio tracks is irrelevant for rendering; put after last video track
      let last = -1
      p.tracks.forEach((x, i) => { if (x.kind === 'video') last = i })
      idx = last >= 0 ? last + 1 : p.tracks.length
    }
    p.tracks.splice(idx, 0, t)
  } else p.tracks.push(t)
  return t
}

/** Choose (or create) a track able to host [start, start+dur). */
function pickTrack(p: Project, kind: TrackKind, spec: string | undefined, start: number, dur: number): Track {
  if (spec && spec !== 'auto') {
    const t = p.tracks.find((x) => x.id === spec)
    if (t && t.kind === kind) return t
  }
  const cands = p.tracks.filter((t) => t.kind === kind && !t.locked)
  for (const t of cands) if (isFree(t, start, start + dur)) return t
  return createTrack(p, kind)
}

function fitProjectToMedia(p: Project, m: MediaRow) {
  if (p.width === 1920 && p.height === 1080 && p.fps === 30 && !hasClips(p) && m.width > 0 && m.height > 0 && m.kind !== 'audio') {
    p.width = Math.max(2, Math.round(m.width / 2) * 2)
    p.height = Math.max(2, Math.round(m.height / 2) * 2)
    // keep long edge reasonable
    const long = Math.max(p.width, p.height)
    if (long > 3840) { const s = 3840 / long; p.width = Math.round(p.width * s / 2) * 2; p.height = Math.round(p.height * s / 2) * 2 }
    if (m.fps > 0) p.fps = Math.max(10, Math.min(60, Math.round(m.fps)))
  }
}

// ------------------------------------------------------------ individual ops
function opAddClip(p: Project, o: Extract<Op, { op: 'add_clip' }>, ctx: OpCtx, out: string[]) {
  const m = ctx.media(o.assetId)
  if (!m) return
  const type: ClipType = m.kind
  const srcIn = o.in ?? 0
  let srcOut: number
  if (m.kind === 'image') srcOut = srcIn + Math.max(MIN_DUR, (o.out ?? 5) - srcIn)
  else srcOut = Math.min(m.duration || 5, o.out ?? (m.duration || 5))
  if (srcOut - srcIn < MIN_DUR) srcOut = srcIn + Math.max(MIN_DUR, m.duration || 5)
  if (m.kind === 'image') { /* in:0 convention */ }
  const dur = srcOut - srcIn
  fitProjectToMedia(p, m)
  const kind = kindForType(type)
  let start = o.start ?? 0
  if (o.start == null) {
    // append after the last clip of the first matching track
    const t = p.tracks.find((x) => x.kind === kind && !x.locked)
    start = t && t.clips.length ? Math.max(...t.clips.map(clipEnd)) : 0
  }
  const track = pickTrack(p, kind, o.track, start, dur)
  if (!isFree(track, start, start + dur)) start = nearestFree(track, start, dur)
  const clip = makeClip({
    type, assetId: m.id, start, in: m.kind === 'image' ? 0 : srcIn, out: m.kind === 'image' ? dur : srcOut,
    name: m.name, fit: 'contain',
  })
  track.clips.push(clip)
  out.push(clip.id)
}

function splitClip(p: Project, id: string, at: number): string | null {
  const f = findClip(p, id)
  if (!f) return null
  const c = f.clip
  const end = clipEnd(c)
  if (at <= c.start + MIN_DUR / 2 || at >= end - MIN_DUR / 2) return null
  const srcMid = c.in + (at - c.start) * c.speed
  const media = clipTraits(c.type).timedSource
  const a: Clip = { ...c, out: media ? srcMid : c.in + (at - c.start) * c.speed, fadeOut: 0 }
  const rest = (end - at) * c.speed
  const b: Clip = {
    ...c, id: uid(), start: at, in: media ? srcMid : 0, out: media ? c.out : rest, fadeIn: 0, transitionIn: undefined,
    transform: { ...c.transform }, adjust: { ...c.adjust }, text: c.text ? { ...c.text } : undefined,
    keyframes: c.keyframes ? shiftKeyframes(c.keyframes, c.start, at) : undefined,
  }
  if (c.keyframes) a.keyframes = c.keyframes.filter((k) => k.t <= at - c.start + EPS)
  f.track.clips[f.ci] = a
  f.track.clips.splice(f.ci + 1, 0, b)
  return b.id
}
function shiftKeyframes(ks: Keyframe[], oldStart: number, newStart: number): Keyframe[] {
  const d = newStart - oldStart
  return ks.filter((k) => k.t >= d - EPS).map((k) => ({ ...k, t: Math.max(0, k.t - d) }))
}

function opTrim(p: Project, o: Extract<Op, { op: 'trim' }>, ctx: OpCtx) {
  setClip(p, o.clipId, (c) => {
    const mediaDur = c.assetId && clipTraits(c.type).timedSource ? ctx.media(c.assetId)?.duration : undefined
    let nin = o.in ?? c.in, nout = o.out ?? c.out
    nin = Math.max(0, nin)
    if (mediaDur) nout = Math.min(nout, mediaDur)
    if (nout - nin < MIN_DUR) { if (o.in != null && o.out == null) nin = nout - MIN_DUR; else nout = nin + MIN_DUR }
    return { ...c, in: nin, out: nout }
  })
}

function opMove(p: Project, o: Extract<Op, { op: 'move' }>) {
  const f = findClip(p, o.clipId)
  if (!f) return
  const moved: Clip = { ...f.clip, start: Math.max(0, o.start) }
  if (o.track && o.track !== f.track.id) {
    const dest = p.tracks.find((t) => t.id === o.track)
    if (dest && dest.kind === kindForType(f.clip.type)) {
      f.track.clips.splice(f.ci, 1)
      dest.clips.push(moved)
      return
    }
  }
  f.track.clips[f.ci] = moved
}

function mergePatch(c: Clip, patch: Partial<Clip>): Clip {
  const n: Clip = { ...c, ...patch }
  if (patch.transform) n.transform = { ...c.transform, ...patch.transform }
  if (patch.adjust) n.adjust = { ...c.adjust, ...patch.adjust }
  if (patch.text) n.text = { ...(c.text || DEFAULT_TEXT), ...patch.text }
  if (patch.speed != null) n.speed = clampSpeed(patch.speed)
  return n
}
export const clampSpeed = (s: number) => Math.min(8, Math.max(0.1, s || 1))

function opAddText(p: Project, o: Extract<Op, { op: 'add_text' }>, out: string[]) {
  const dur = Math.max(MIN_DUR, o.duration)
  const track = pickTrack(p, 'text', 'auto', o.start, dur)
  const text: TextStyle = { ...DEFAULT_TEXT, ...(o.style || {}), content: o.text }
  const clip = makeClip({ type: 'text', start: Math.max(0, o.start), in: 0, out: dur, text, name: o.text.slice(0, 16) })
  track.clips.push(clip)
  out.push(clip.id)
}

function opAddMusic(p: Project, o: Extract<Op, { op: 'add_music' }>, ctx: OpCtx, out: string[]) {
  const m = ctx.media(o.assetId)
  if (!m) return
  const dur = m.duration || 10
  const start = o.start ?? 0
  const track = pickTrack(p, 'audio', 'auto', start, dur)
  const s = isFree(track, start, start + dur) ? start : nearestFree(track, start, dur)
  const clip = makeClip({ type: 'audio', assetId: m.id, start: s, in: 0, out: dur, volume: o.volume ?? 1, name: m.name })
  track.clips.push(clip)
  out.push(clip.id)
}

function opAddSubtitles(p: Project, o: Extract<Op, { op: 'add_subtitles' }>, out: string[]) {
  const segs = o.segments.filter((s) => s.text && s.end > s.start)
  if (!segs.length) return
  const track = createTrack(p, 'text')
  track.name = 'Subtitles'
  for (const s of segs) {
    const text: TextStyle = { ...DEFAULT_TEXT, size: 0.055, bold: true, ...(o.style || {}), content: s.text.trim() }
    const clip = makeClip({
      type: 'text', start: Math.max(0, s.start), in: 0, out: Math.max(MIN_DUR, s.end - s.start), text,
      transform: { ...DEFAULT_TRANSFORM, y: 0.36 }, name: s.text.slice(0, 16),
    })
    track.clips.push(clip)
    out.push(clip.id)
  }
}

/** Split a video clip's sound onto its own audio clip (same source range, timing, volume and fades) and silence the
 *  video clip. The audio clip goes on the first free unlocked audio track, or a new one. Returns the audio clip id. */
export function detachAudio(p: Project, id: string): string | null {
  const f = findClip(p, id)
  if (!f || f.clip.type !== 'video' || f.clip.audioDetached || !f.clip.assetId) return null
  const c = f.clip
  const dur = clipDuration(c)
  const track = pickTrack(p, 'audio', 'auto', c.start, dur)
  const audio = makeClip({
    type: 'audio', assetId: c.assetId, start: c.start, in: c.in, out: c.out, speed: c.speed,
    volume: c.volume, fadeIn: c.fadeIn, fadeOut: c.fadeOut, name: `${c.name || 'Clip'} (audio)`,
  })
  track.clips.push(audio)
  f.track.clips[f.ci] = { ...c, audioDetached: true }
  return audio.id
}

/** Ripple-delete [a,b) from every track. */
function opRemoveRanges(p: Project, ranges: { start: number; end: number }[]) {
  const rs = ranges.filter((r) => r.end > r.start).map((r) => ({ start: Math.max(0, r.start), end: r.end })).sort((x, y) => y.start - x.start)
  for (const r of rs) {
    const a = r.start, b = r.end, d = b - a
    for (const t of p.tracks) {
      const next: Clip[] = []
      for (const c of t.clips) {
        const s = c.start, e = clipEnd(c)
        const media = clipTraits(c.type).timedSource
        if (e <= a + EPS) { next.push(c); continue }
        if (s >= b - EPS) { next.push({ ...c, start: s - d }); continue }
        if (s >= a - EPS && e <= b + EPS) continue // fully removed
        if (s < a - EPS && e > b + EPS) { // strictly inside: split in two
          const left: Clip = { ...c, out: c.in + (a - s) * c.speed, fadeOut: 0 }
          const rightLen = (e - b) * c.speed
          const right: Clip = {
            ...c, id: uid(), start: a, in: media ? c.in + (b - s) * c.speed : 0,
            out: media ? c.out : rightLen, fadeIn: 0, transitionIn: undefined,
            transform: { ...c.transform }, adjust: { ...c.adjust }, text: c.text ? { ...c.text } : undefined,
          }
          next.push(left, right); continue
        }
        if (s < a) { next.push({ ...c, out: c.in + (a - s) * c.speed, fadeOut: 0 }); continue } // tail overlaps range
        // head overlaps range: keep the part after b
        const cut = (b - s) * c.speed
        next.push({ ...c, start: a, in: media ? c.in + cut : 0, out: media ? c.out : c.out - c.in - cut, fadeIn: 0, transitionIn: undefined })
      }
      t.clips = next
    }
  }
}

/** Apply a list of ops to a project, returning a NEW project plus ids of created clips. */
export interface ApplyResult { project: Project; created: string[]; applied: number; dropped: Dropped[] }

/** Apply ops (validated + coerced one by one against the working project). Invalid ops are dropped with a reason. */
export function applyOpsToProject(project: Project, rawOps: unknown[], ctx: OpCtx): ApplyResult {
  const p = fork(project)
  const created: string[] = []
  const dropped: Dropped[] = []
  let applied = 0
  for (const raw of rawOps) {
    const chk = checkOp(raw, p, ctx)
    if ('reason' in chk) { dropped.push({ op: raw, reason: chk.reason }); continue }
    const o = chk.op
    try {
      switch (o.op) {
        case 'add_clip': opAddClip(p, o, ctx, created); break
        case 'split': { const id = splitClip(p, o.clipId, o.at); if (id) created.push(id); break }
        case 'delete_clip': removeClip(p, o.clipId); break
        case 'trim': opTrim(p, o, ctx); break
        case 'move': opMove(p, o); break
        case 'set': setClip(p, o.clipId, (c) => mergePatch(c, o.patch)); break
        case 'set_speed': setClip(p, o.clipId, (c) => ({ ...c, speed: clampSpeed(o.speed) })); break
        case 'add_transition': setClip(p, o.clipId, (c) => ({ ...c, transitionIn: { type: o.type, duration: Math.max(0.05, o.duration) } })); break
        case 'add_text': opAddText(p, o, created); break
        case 'add_music': opAddMusic(p, o, ctx, created); break
        case 'add_subtitles': opAddSubtitles(p, o, created); break
        case 'remove_ranges': opRemoveRanges(p, o.ranges); break
        case 'detach_audio': { const id = detachAudio(p, o.clipId); if (id) created.push(id); break }
      }
      applied++
    } catch (e: any) { dropped.push({ op: raw, reason: `failed: ${e?.message || e}` }) }
  }
  sortTracks(p)
  return { project: p, created, applied, dropped }
}

/** Unique asset ids referenced by add_clip / add_music ops (so the caller can pre-fetch media rows). */
export function opsAssetIds(ops: unknown[]): string[] {
  const s = new Set<string>()
  for (const o of ops as any[]) if (o && (o.op === 'add_clip' || o.op === 'add_music') && o.assetId != null) s.add(String(o.assetId))
  return [...s]
}

// ------------------------------------------------------------ misc helpers used by UI
export function trackOf(p: Project, clipId: string): Track | undefined { return findClip(p, clipId)?.track }

/** Time at which a new clip of `kind` should be appended when the user clicks "+" in a panel. */
export function smartStart(p: Project, kind: TrackKind, playhead: number, dur: number): number {
  const t = p.tracks.filter((x) => x.kind === kind && !x.locked)
  if (!t.length) return playhead
  const main = t[0]
  if (isFree(main, playhead, playhead + dur)) return playhead
  // appended behind whatever occupies the playhead on the main track
  let s = playhead
  for (let i = 0; i < 50; i++) {
    const hit = main.clips.find((c) => c.start < s + dur - EPS && clipEnd(c) > s + EPS)
    if (!hit) break
    s = clipEnd(hit)
  }
  return s
}

export function interpKeyframes(c: Clip, lt: number): { x: number; y: number; scale: number; rotation: number; opacity: number } {
  const base = { x: c.transform.x, y: c.transform.y, scale: c.transform.scale, rotation: c.transform.rotation, opacity: c.transform.opacity }
  const ks = c.keyframes
  if (!ks || !ks.length) return base
  const sorted = ks.length > 1 ? [...ks].sort((a, b) => a.t - b.t) : ks
  const out = { ...base }
  const fields = ['x', 'y', 'scale', 'rotation', 'opacity'] as const
  for (const f of fields) {
    const pts = sorted.filter((k) => k[f] != null)
    if (!pts.length) continue
    if (lt <= pts[0].t) { out[f] = pts[0][f]!; continue }
    const last = pts[pts.length - 1]
    if (lt >= last.t) { out[f] = last[f]!; continue }
    for (let i = 0; i < pts.length - 1; i++) {
      const A = pts[i], B = pts[i + 1]
      if (lt >= A.t && lt <= B.t) { const u = (lt - A.t) / Math.max(EPS, B.t - A.t); out[f] = A[f]! + (B[f]! - A[f]!) * u; break }
    }
  }
  return out
}

export { clipDuration, clipEnd }
