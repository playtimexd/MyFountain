// Asset Hub API types + small helpers (backend: backend/app/routers/hub.py, prefix /api/hub)
import { api } from '../../api/client'
import type { MediaRow } from '../../types/api'

export type HubKind = 'model' | 'animation' | 'texture' | 'material' | 'hdri' | 'sfx' | 'music' | 'voice' | 'sprite' | 'image' | 'video' | 'font' | 'prefab'
export type LicenseClass = 'commercial-safe' | 'attribution' | 'non-commercial' | 'restricted'
export type Policy = 'cc0' | 'commercial' | 'any'

export interface HubFile { role: string; path: string; format: string; bytes: number; sha256: string; resolution?: string | null; url: string }
export interface HubAsset {
  id: string; kind: HubKind; name: string; description: string; tags: string[]
  source: string; source_id: string; source_url?: string | null; author?: string | null; author_url?: string | null
  license: string; license_url?: string | null; license_class: LicenseClass; attribution: string
  commercial_ok: boolean; modifiable: boolean; stats: Record<string, any>; thumb?: string | null; thumb_url?: string | null
  media_id?: string | null; formats: string[]; rigged: boolean; animated: boolean; total_bytes: number
  has_embedding: boolean; has_clip: boolean; created: number; updated: number; files?: HubFile[]; score?: number
  license_info?: LicenseInfo
}
export interface Facets { kind: Record<string, number>; source: Record<string, number>; license: Record<string, number>; format: Record<string, number>; rigged: number; animated: number }
export interface AssetPage { items: HubAsset[]; total: number; page: number; page_size: number; has_more: boolean; facets: Facets }
export interface Candidate {
  source: string; source_id: string; kind: HubKind; name: string; description: string; tags: string[]; source_url?: string | null
  author?: string | null; author_url?: string | null; license: string; license_label: string; license_class: LicenseClass; license_url: string
  commercial_ok: boolean; modifiable: boolean; preview_url?: string | null; stats: Record<string, any>; formats: string[]
  downloadable: boolean; manual: boolean; extra?: Record<string, any>; in_hub?: string | null; allowed?: boolean
}
export interface OnlinePage { items: Candidate[]; page: number; total: number | null; has_more: boolean; source: string }
export interface SourceInfo {
  id: string; name: string; homepage: string; kinds: HubKind[]; available: boolean; note: string; needs_key: boolean
  key_name: string | null; key_set: boolean | null; automated: boolean; terms_url: string; terms: string; default_license: string; items: number
}
export interface LicenseInfo { id: string; label: string; url: string; commercial_ok: boolean; modifiable: boolean; attribution_required: boolean; class: LicenseClass }
export interface HubStats {
  total: number; bytes: number; by_kind: Record<string, number>; by_source: Record<string, number>; by_license: Record<string, number>
  by_license_class: Record<string, number>; with_text_embedding: number; with_clip: number; in_media_library: number; rigged: number; animated: number
}
export interface HarvestSource { cursor: number[]; added: number; exists: number; duplicates: number; skipped: number; errors: number; bytes: number; done: boolean; note: string; quota: number }
export interface HarvestState {
  id: string; status: string; job_id?: string; created: number; updated: number
  params: { sources: string[]; queries: string[]; kinds: (string | null)[]; max_items: number; max_gb: number; license_policy: Policy; variants: Record<string, string> }
  totals: { added: number; exists: number; duplicates: number; skipped: number; errors: number; bytes: number }
  sources: Record<string, HarvestSource>; log: string[]; recent: { id: string; name: string; kind: string; source: string; thumb?: string; license: string }[]
}

export const KINDS: { id: HubKind; label: string }[] = [
  { id: 'model', label: 'Models' }, { id: 'animation', label: 'Animations' }, { id: 'material', label: 'Materials' }, { id: 'texture', label: 'Textures' },
  { id: 'hdri', label: 'HDRIs' }, { id: 'sfx', label: 'Sound FX' }, { id: 'music', label: 'Music' }, { id: 'voice', label: 'Voice' },
  { id: 'sprite', label: 'Sprites' }, { id: 'image', label: 'Images' }, { id: 'video', label: 'Video' }, { id: 'font', label: 'Fonts' }, { id: 'prefab', label: 'Prefabs' },
]
export const kindLabel = (k: string) => KINDS.find((x) => x.id === k)?.label.replace(/s$/, '') ?? k
export const LICENSE_CLASSES: { id: LicenseClass; label: string; hint: string }[] = [
  { id: 'commercial-safe', label: 'Commercial-safe', hint: 'CC0 / OFL / similar - no attribution needed' },
  { id: 'attribution', label: 'Attribution', hint: 'Commercial OK, credit required (CC-BY…)' },
  { id: 'non-commercial', label: 'Non-commercial', hint: 'Not for commercial products (CC-BY-NC…)' },
  { id: 'restricted', label: 'Restricted', hint: 'No-derivatives or unknown terms' },
]
export const AUDIO_KINDS = new Set(['sfx', 'music', 'voice'])

export const hubFile = (id: string, role = 'main') => `/api/hub/file/${id}/${encodeURIComponent(role)}`

export async function mediaFor(a: HubAsset): Promise<MediaRow | null> {
  if (!a.media_id) return null
  try { return await api.get<MediaRow>(`/api/media/${a.media_id}`) } catch { return null }
}

export async function copyText(t: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(t); return true } catch {
    const ta = document.createElement('textarea'); ta.value = t; document.body.appendChild(ta); ta.select()
    const ok = document.execCommand('copy'); ta.remove(); return ok
  }
}

export function downloadText(name: string, text: string) {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([text], { type: 'text/markdown' }))
  a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000)
}
