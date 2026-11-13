// Validation / coercion of agent-written ops (MCP bridge, AI agent). Every op is checked against the
// working project right before it runs, so sequential ops see each other's effects.
import type { Clip, Op, Project, TextStyle, TransitionIn } from '../../types/project'
import { clipEnd } from '../../types/project'
import type { OpCtx } from './ops'
import { clipTraits } from './clipTypes'

export interface Dropped { op: unknown; reason: string }
type Check = { op: Op } | { reason: string }

const OPS = ['add_clip', 'split', 'delete_clip', 'trim', 'move', 'set', 'set_speed', 'add_transition', 'add_text', 'add_music', 'add_subtitles', 'remove_ranges', 'detach_audio']
const TRANSITIONS: TransitionIn['type'][] = ['fade', 'slideleft', 'slideright', 'slideup', 'slidedown', 'zoomin', 'wipeleft', 'wiperight']

export const num = (v: unknown): number | undefined => {
  if (typeof v === 'number') return Number.isFinite(v) ? v : undefined
  if (typeof v === 'string' && v.trim() !== '') { const n = parseFloat(v); return Number.isFinite(n) ? n : undefined }
  return undefined
}
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
const bool = (v: unknown) => (typeof v === 'string' ? v === 'true' || v === '1' : !!v)

function findClip(p: Project, id: unknown): { clip: Clip; trackId: string } | null {
  if (typeof id !== 'string' && typeof id !== 'number') return null
  const sid = String(id)
  for (const t of p.tracks) { const c = t.clips.find((x) => x.id === sid); if (c) return { clip: c, trackId: t.id } }
  return null
}

const RANGES: Record<string, [number, number]> = {
  // transform
  x: [-5, 5], y: [-5, 5], scale: [0.01, 20], rotation: [-3600, 3600], opacity: [0, 1],
  // adjust
  brightness: [-1, 1], contrast: [0, 2], saturation: [0, 3], hue: [-180, 180], blur: [0, 40], sharpen: [0, 1],
  vignette: [0, 1], sepia: [0, 1], grayscale: [0, 1], temperature: [-1, 1],
  // text
  size: [0.005, 1], strokeWidth: [0, 0.5], bgPadding: [0, 3], letterSpacing: [-0.5, 2],
}
function cleanNumbers<T extends Record<string, unknown>>(o: unknown, keys?: string[]): Partial<T> {
  if (!o || typeof o !== 'object') return {}
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(o as Record<string, unknown>)) {
    if (keys && !keys.includes(k)) continue
    if (k in RANGES) { const n = num(v); if (n !== undefined) out[k] = clamp(n, RANGES[k][0], RANGES[k][1]) }
    else if (k === 'flipX' || k === 'flipY' || k === 'bold' || k === 'italic' || k === 'shadow') out[k] = bool(v)
    else if (k === 'align') { if (v === 'left' || v === 'center' || v === 'right') out[k] = v }
    else if (typeof v === 'string' || typeof v === 'number') out[k] = typeof v === 'number' ? v : String(v)
  }
  return out as Partial<T>
}
const TRANSFORM_KEYS = ['x', 'y', 'scale', 'rotation', 'opacity', 'flipX', 'flipY']
const ADJUST_KEYS = ['brightness', 'contrast', 'saturation', 'hue', 'blur', 'sharpen', 'vignette', 'sepia', 'grayscale', 'temperature']
const TEXT_KEYS = ['content', 'font', 'size', 'color', 'bold', 'italic', 'align', 'stroke', 'strokeWidth', 'shadow', 'bg', 'bgPadding', 'letterSpacing']
const textStyle = (s: unknown) => cleanNumbers<TextStyle & Record<string, unknown>>(s, TEXT_KEYS) as Partial<TextStyle>

function cleanPatch(raw: unknown, clip: Clip): Partial<Clip> | string {
  if (!raw || typeof raw !== 'object') return 'patch must be an object'
  const r = raw as Record<string, unknown>
  const out: Partial<Clip> = {}
  for (const k of ['start', 'in', 'out', 'speed', 'volume', 'fadeIn', 'fadeOut'] as const) {
    if (r[k] === undefined) continue
    const n = num(r[k]); if (n === undefined) return `${k} is not a number`
    out[k] = k === 'speed' ? clamp(n, 0.1, 8) : k === 'volume' ? clamp(n, 0, 2) : Math.max(0, n)
  }
  if (r.fit === 'contain' || r.fit === 'cover') out.fit = r.fit
  if (typeof r.name === 'string') out.name = r.name
  if (r.locked !== undefined) out.locked = bool(r.locked)
  if (r.audioDetached !== undefined) out.audioDetached = bool(r.audioDetached)
  if (typeof r.filter === 'string') out.filter = r.filter
  if (r.transform) out.transform = cleanNumbers(r.transform, TRANSFORM_KEYS) as unknown as Clip['transform']
  if (r.adjust) out.adjust = cleanNumbers(r.adjust, ADJUST_KEYS) as unknown as Clip['adjust']
  if (r.text) { if (!clip.text) return 'text patch on a non-text clip'; out.text = textStyle(r.text) as TextStyle }
  if (r.transitionIn === null) out.transitionIn = undefined
  else if (r.transitionIn && typeof r.transitionIn === 'object') {
    const tr = r.transitionIn as Record<string, unknown>
    if (!TRANSITIONS.includes(tr.type as TransitionIn['type'])) return `unknown transition ${String(tr.type)}`
    out.transitionIn = { type: tr.type as TransitionIn['type'], duration: clamp(num(tr.duration) ?? 0.6, 0.05, 5) }
  }
  if (out.in !== undefined || out.out !== undefined) {
    const i = out.in ?? clip.in, o = out.out ?? clip.out
    if (o - i < 0.05) return 'out must be greater than in'
  }
  if (!Object.keys(out).length && r.transitionIn !== null) return 'patch has no recognised fields'
  return out
}

/** Validate + coerce one raw op against the current working project. */
export function checkOp(raw: unknown, p: Project, ctx: OpCtx): Check {
  if (!raw || typeof raw !== 'object') return { reason: 'op must be an object' }
  const o = raw as Record<string, any>
  if (!OPS.includes(o.op)) return { reason: `unknown op "${String(o.op)}"` }
  const needClip = () => findClip(p, o.clipId)
  switch (o.op) {
    case 'add_clip': case 'add_music': {
      if (o.assetId == null) return { reason: 'missing assetId' }
      const m = ctx.media(String(o.assetId))
      if (!m) return { reason: `unknown asset id ${o.assetId}` }
      const start = num(o.start), vin = num(o.in), vout = num(o.out), vol = num(o.volume)
      if (o.op === 'add_music') return { op: { op: 'add_music', assetId: m.id, start: start !== undefined ? Math.max(0, start) : undefined, volume: vol !== undefined ? clamp(vol, 0, 2) : undefined } }
      if (o.track !== undefined && o.track !== 'auto' && !p.tracks.some((t) => t.id === String(o.track))) return { reason: `unknown track ${o.track}` }
      const dur = m.duration || 0
      const i = vin !== undefined ? clamp(vin, 0, Math.max(0, dur - 0.05)) : undefined
      const out = vout !== undefined ? (m.kind === 'image' ? Math.max(0.1, vout) : clamp(vout, (i ?? 0) + 0.05, dur || vout)) : undefined
      return { op: { op: 'add_clip', assetId: m.id, start: start !== undefined ? Math.max(0, start) : undefined, in: i, out, track: o.track !== undefined ? String(o.track) : undefined } }
    }
    case 'split': {
      const f = needClip(); if (!f) return { reason: `unknown clip id ${o.clipId}` }
      const at = num(o.at); if (at === undefined) return { reason: 'at is not a number' }
      if (at <= f.clip.start + 0.05 || at >= clipEnd(f.clip) - 0.05) return { reason: `split point ${at} is outside clip ${f.clip.id} (${f.clip.start.toFixed(2)}–${clipEnd(f.clip).toFixed(2)})` }
      return { op: { op: 'split', clipId: f.clip.id, at } }
    }
    case 'delete_clip': {
      const f = needClip(); if (!f) return { reason: `unknown clip id ${o.clipId}` }
      return { op: { op: 'delete_clip', clipId: f.clip.id } }
    }
    case 'trim': {
      const f = needClip(); if (!f) return { reason: `unknown clip id ${o.clipId}` }
      const i = num(o.in), out = num(o.out)
      if (i === undefined && out === undefined) return { reason: 'trim needs in and/or out' }
      const ni = Math.max(0, i ?? f.clip.in), no = out ?? f.clip.out
      if (no - ni < 0.05) return { reason: 'out must be greater than in' }
      return { op: { op: 'trim', clipId: f.clip.id, in: i !== undefined ? ni : undefined, out } }
    }
    case 'move': {
      const f = needClip(); if (!f) return { reason: `unknown clip id ${o.clipId}` }
      const start = num(o.start); if (start === undefined) return { reason: 'start is not a number' }
      let track: string | undefined
      if (o.track !== undefined && o.track !== null && o.track !== '') {
        const t = p.tracks.find((x) => x.id === String(o.track))
        if (!t) return { reason: `unknown track ${o.track}` }
        if (t.kind !== clipTraits(f.clip.type).track) return { reason: `track ${t.id} is a ${t.kind} track; a ${f.clip.type} clip cannot go there` }
        track = t.id
      }
      return { op: { op: 'move', clipId: f.clip.id, start: Math.max(0, start), track } }
    }
    case 'set': {
      const f = needClip(); if (!f) return { reason: `unknown clip id ${o.clipId}` }
      const patch = cleanPatch(o.patch, f.clip)
      if (typeof patch === 'string') return { reason: patch }
      return { op: { op: 'set', clipId: f.clip.id, patch } }
    }
    case 'set_speed': {
      const f = needClip(); if (!f) return { reason: `unknown clip id ${o.clipId}` }
      const s = num(o.speed); if (s === undefined || s <= 0) return { reason: 'speed must be a positive number' }
      return { op: { op: 'set_speed', clipId: f.clip.id, speed: clamp(s, 0.1, 8) } }
    }
    case 'add_transition': {
      const f = needClip(); if (!f) return { reason: `unknown clip id ${o.clipId}` }
      if (!TRANSITIONS.includes(o.type)) return { reason: `unknown transition "${o.type}" (use ${TRANSITIONS.join(', ')})` }
      return { op: { op: 'add_transition', clipId: f.clip.id, type: o.type, duration: clamp(num(o.duration) ?? 0.6, 0.05, 5) } }
    }
    case 'add_text': {
      const text = o.text == null ? '' : String(o.text)
      if (!text.trim()) return { reason: 'text is empty' }
      return { op: { op: 'add_text', text, start: Math.max(0, num(o.start) ?? 0), duration: clamp(num(o.duration) ?? 3, 0.1, 3600), style: textStyle(o.style) } }
    }
    case 'add_subtitles': {
      if (!Array.isArray(o.segments)) return { reason: 'segments must be an array' }
      const segs = o.segments.map((s: any) => ({ start: num(s?.start), end: num(s?.end), text: s?.text == null ? '' : String(s.text) }))
        .filter((s: any) => s.start !== undefined && s.end !== undefined && s.end > s.start && s.text.trim())
        .map((s: any) => ({ start: Math.max(0, s.start), end: s.end, text: s.text }))
      if (!segs.length) return { reason: 'no valid segments ({start,end,text} with end > start)' }
      return { op: { op: 'add_subtitles', segments: segs, style: textStyle(o.style) } }
    }
    case 'remove_ranges': {
      if (!Array.isArray(o.ranges)) return { reason: 'ranges must be an array' }
      const rs = o.ranges.map((r: any) => ({ start: num(r?.start), end: num(r?.end) }))
        .filter((r: any) => r.start !== undefined && r.end !== undefined && r.end > r.start)
        .map((r: any) => ({ start: Math.max(0, r.start), end: r.end }))
      if (!rs.length) return { reason: 'no valid ranges ({start,end} with end > start)' }
      return { op: { op: 'remove_ranges', ranges: rs } }
    }
    case 'detach_audio': {
      const f = needClip(); if (!f) return { reason: `unknown clip id ${o.clipId}` }
      if (f.clip.type !== 'video') return { reason: `clip ${f.clip.id} is a ${f.clip.type} clip; only video clips have audio to split` }
      if (f.clip.audioDetached) return { reason: `clip ${f.clip.id} already has its audio split off` }
      const m = f.clip.assetId ? ctx.media(f.clip.assetId) : undefined
      if (m && !m.has_audio) return { reason: `clip ${f.clip.id} has no audio stream` }
      return { op: { op: 'detach_audio', clipId: f.clip.id } }
    }
  }
  return { reason: 'unsupported op' }
}
