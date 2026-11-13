export interface MediaRow {
  id: string
  kind: 'video' | 'audio' | 'image'
  name: string
  path: string
  origin: string
  source_url?: string | null
  duration: number
  width: number
  height: number
  fps: number
  size: number
  has_audio: boolean
  tags: string[]
  meta: Record<string, unknown>
  created: number
  url: string
  thumb: string
}

export interface JobInfo<R = any> {
  id: string
  kind: string
  title: string
  status: 'queued' | 'running' | 'done' | 'error' | 'cancelled'
  progress: number
  message: string
  result: R | null
  error: string | null
  created: number
  finished: number | null
}

export interface Segment { start: number; end: number; label?: string; text?: string }

export interface CatalogFilter { id: string; name: string; adjust: Partial<import('./project').Adjust> }
export interface Catalog {
  transitions: { id: string; name: string }[]
  filters: CatalogFilter[]
  stickers: { id: string; name: string; url: string }[]
  textPresets: { id: string; name: string; text: Partial<import('./project').TextStyle> }[]
  backgrounds: { id: string; name: string; css: string; url?: string }[]
  fonts: { family: string; url?: string }[]
}
