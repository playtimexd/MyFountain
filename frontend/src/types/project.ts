// Authoritative editing-document schema. Mirrored by backend/app/services/render.py.

export type TrackKind = 'video' | 'audio' | 'text'
export type ClipType = 'video' | 'image' | 'audio' | 'text' | 'sticker'

export interface Transform {
  x: number          // centre offset from canvas centre, fraction of canvas width  (-1..1)
  y: number          // fraction of canvas height
  scale: number      // multiplier on the fitted size (1 = fit)
  rotation: number   // degrees, clockwise
  opacity: number    // 0..1
  flipX?: boolean
  flipY?: boolean
}

export interface Adjust {
  brightness: number   // -1..1   (0)
  contrast: number     // 0..2    (1)
  saturation: number   // 0..3    (1)
  hue: number          // -180..180 (0)
  blur: number         // px at 1080p, 0..40 (0)
  sharpen: number      // 0..1 (0)
  vignette: number     // 0..1 (0)
  sepia: number        // 0..1 (0)
  grayscale: number    // 0..1 (0)
  temperature: number  // -1 (cool) .. 1 (warm) (0)
}

export interface TextStyle {
  content: string
  font: string          // font-family name (see /api/assets/fonts)
  size: number          // fraction of canvas height, e.g. 0.06
  color: string         // #rrggbb
  bold: boolean
  italic: boolean
  align: 'left' | 'center' | 'right'
  stroke: string        // outline colour
  strokeWidth: number   // fraction of font size (0..0.2)
  shadow: boolean
  bg: string            // '' = none, else css colour (#rrggbbaa)
  bgPadding: number     // fraction of font size
  letterSpacing?: number
}

export interface TransitionIn {
  type: 'fade' | 'slideleft' | 'slideright' | 'slideup' | 'slidedown' | 'zoomin' | 'wipeleft' | 'wiperight'
  duration: number      // seconds
}

export interface Keyframe {
  t: number             // seconds relative to clip start (timeline time)
  x?: number; y?: number; scale?: number; rotation?: number; opacity?: number
}

export interface Clip {
  id: string
  type: ClipType
  assetId?: string      // media.id (video/image/audio) or sticker id
  start: number         // timeline seconds
  in: number            // source in-point (seconds). For image/text/sticker: 0
  out: number           // source out-point. For image/text/sticker: out - in == source duration before speed
  speed: number         // 0.1..8
  volume: number        // 0..2 (1)
  fadeIn: number        // audio+video fade seconds
  fadeOut: number
  fit: 'contain' | 'cover'
  transform: Transform
  adjust: Adjust
  filter?: string       // preset id from /api/assets/catalog filters (already merged into adjust on apply; kept for display)
  text?: TextStyle
  transitionIn?: TransitionIn
  keyframes?: Keyframe[]
  name?: string
  locked?: boolean
  audioDetached?: boolean // video clip whose sound was split onto its own audio clip: the clip itself plays silent
}

export interface Track {
  id: string
  kind: TrackKind
  name: string
  muted: boolean
  hidden: boolean
  locked: boolean
  clips: Clip[]
}

export interface Project {
  version: 1
  name: string
  width: number
  height: number
  fps: number
  background: string    // canvas colour
  tracks: Track[]       // later tracks render on top
}

export const DEFAULT_ADJUST: Adjust = {
  brightness: 0, contrast: 1, saturation: 1, hue: 0, blur: 0, sharpen: 0, vignette: 0, sepia: 0, grayscale: 0, temperature: 0,
}
export const DEFAULT_TRANSFORM: Transform = { x: 0, y: 0, scale: 1, rotation: 0, opacity: 1 }
export const DEFAULT_TEXT: TextStyle = {
  content: 'Text', font: 'Microsoft YaHei', size: 0.07, color: '#ffffff', bold: true, italic: false, align: 'center',
  stroke: '#000000', strokeWidth: 0.06, shadow: true, bg: '', bgPadding: 0.25,
}

export const clipDuration = (c: Clip) => Math.max(0.01, (c.out - c.in) / (c.speed || 1))
export const clipEnd = (c: Clip) => c.start + clipDuration(c)
export const projectDuration = (p: Project) =>
  p.tracks.reduce((m, t) => t.clips.reduce((mm, c) => Math.max(mm, clipEnd(c)), m), 0)

export type Op =
  | { op: 'add_clip'; assetId: string; track?: string; start?: number; in?: number; out?: number }
  | { op: 'split'; clipId: string; at: number }
  | { op: 'delete_clip'; clipId: string }
  | { op: 'trim'; clipId: string; in?: number; out?: number }
  | { op: 'move'; clipId: string; start: number; track?: string }
  | { op: 'set'; clipId: string; patch: Partial<Clip> }
  | { op: 'set_speed'; clipId: string; speed: number }
  | { op: 'add_transition'; clipId: string; type: TransitionIn['type']; duration: number }
  | { op: 'add_text'; text: string; start: number; duration: number; style?: Partial<TextStyle> }
  | { op: 'add_music'; assetId: string; start?: number; volume?: number }
  | { op: 'add_subtitles'; segments: { start: number; end: number; text: string }[]; style?: Partial<TextStyle> }
  | { op: 'remove_ranges'; ranges: { start: number; end: number }[] }
  | { op: 'detach_audio'; clipId: string }
