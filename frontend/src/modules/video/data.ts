// Media library cache + asset catalog (with built-in fallbacks) used across the video editor.
import { create } from 'zustand'
import { api } from '../../api/client'
import type { Catalog, CatalogFilter, MediaRow } from '../../types/api'
import { registerFonts } from './engine/assets'

interface MediaState {
  items: MediaRow[]
  byId: Record<string, MediaRow>
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
  upsert: (row: MediaRow) => void
  ensure: (id: string) => Promise<MediaRow | undefined>
}

export const useMedia = create<MediaState>((set, get) => ({
  items: [], byId: {}, loading: false, error: null,
  refresh: async () => {
    set({ loading: true })
    try {
      const items = await api.get<MediaRow[]>('/api/media')
      const byId = { ...get().byId }
      for (const m of items) byId[m.id] = m
      set({ items, byId, loading: false, error: null })
    } catch (e: any) { set({ loading: false, error: e.message || String(e) }) }
  },
  upsert: (row) => set((s) => ({
    byId: { ...s.byId, [row.id]: row },
    items: s.items.some((m) => m.id === row.id) ? s.items.map((m) => (m.id === row.id ? row : m)) : [row, ...s.items],
  })),
  ensure: async (id) => {
    const have = get().byId[id]
    if (have) return have
    try {
      const row = await api.get<MediaRow>(`/api/media/${id}`)
      set((s) => ({ byId: { ...s.byId, [id]: row } }))
      return row
    } catch { return undefined }
  },
}))

export const getMedia = (id: string) => useMedia.getState().byId[id]

// ------------------------------------------------------------------ catalog
const mk = (id: string, name: string, adjust: CatalogFilter['adjust']): CatalogFilter => ({ id, name, adjust })
export const DEFAULT_CATALOG: Catalog = {
  transitions: [
    { id: 'fade', name: 'Fade' }, { id: 'slideleft', name: 'Slide left' }, { id: 'slideright', name: 'Slide right' },
    { id: 'slideup', name: 'Slide up' }, { id: 'slidedown', name: 'Slide down' }, { id: 'zoomin', name: 'Zoom in' },
    { id: 'wipeleft', name: 'Wipe left' }, { id: 'wiperight', name: 'Wipe right' },
  ],
  filters: [
    mk('vivid', 'Vivid', { saturation: 1.5, contrast: 1.1 }),
    mk('bw', 'B&W', { grayscale: 1, contrast: 1.1 }),
    mk('retro', 'Retro', { sepia: 0.6, contrast: 1.05, saturation: 0.85, vignette: 0.3 }),
    mk('cinema', 'Cinematic', { contrast: 1.2, saturation: 0.85, temperature: -0.15, vignette: 0.35, brightness: -0.05 }),
    mk('cool', 'Cool', { temperature: -0.5, saturation: 1.05 }),
    mk('warm', 'Warm', { temperature: 0.5, saturation: 1.05 }),
    mk('fresh', 'Fresh', { brightness: 0.08, saturation: 1.25, contrast: 0.95, temperature: -0.1 }),
    mk('contrast', 'High contrast', { contrast: 1.45, saturation: 1.1 }),
  ],
  stickers: [],
  textPresets: [
    { id: 'title', name: 'Title', text: { size: 0.1, bold: true, color: '#ffffff', stroke: '#000000', strokeWidth: 0.06, shadow: true } },
    { id: 'subtitle', name: 'Subtitle', text: { size: 0.055, bold: true, color: '#ffffff', stroke: '#000000', strokeWidth: 0.08, shadow: false } },
    { id: 'yellow', name: 'Yellow outline', text: { size: 0.08, bold: true, color: '#ffd400', stroke: '#3a2a00', strokeWidth: 0.1, shadow: true } },
    { id: 'box', name: 'White on black', text: { size: 0.06, bold: true, color: '#ffffff', stroke: '#000000', strokeWidth: 0, shadow: false, bg: '#000000b3', bgPadding: 0.3 } },
    { id: 'red', name: 'Bold red', text: { size: 0.09, bold: true, color: '#ff3b4a', stroke: '#ffffff', strokeWidth: 0.07, shadow: true } },
    { id: 'neon', name: 'Neon', text: { size: 0.09, bold: true, color: '#7cf7ff', stroke: '#6a3cff', strokeWidth: 0.09, shadow: true } },
    { id: 'plain', name: 'Plain white', text: { size: 0.05, bold: false, color: '#ffffff', stroke: '#000000', strokeWidth: 0, shadow: true } },
    { id: 'quote', name: 'Italic quote', text: { size: 0.06, bold: false, italic: true, color: '#f4e9d0', stroke: '#000000', strokeWidth: 0.03, shadow: true } },
  ],
  backgrounds: [],
  fonts: [{ family: 'Microsoft YaHei' }, { family: 'SimHei' }, { family: 'KaiTi' }, { family: 'Arial' }, { family: 'Impact' }],
}

interface CatalogState {
  catalog: Catalog
  loaded: boolean
  load: () => Promise<void>
}
export const useCatalog = create<CatalogState>((set, get) => ({
  catalog: DEFAULT_CATALOG, loaded: false,
  load: async () => {
    if (get().loaded) return
    let cat: Partial<Catalog> | null = null
    try { cat = await api.get<Catalog>('/api/assets/catalog') } catch { /* fallback */ }
    let fonts = cat?.fonts
    if (!fonts || !fonts.length) {
      try { fonts = (await api.get<{ family: string; url?: string }[]>('/api/assets/fonts')).map((f) => ({ family: f.family, url: f.url })) } catch { /* fallback */ }
    }
    const base = DEFAULT_CATALOG
    const merged: Catalog = {
      transitions: cat?.transitions?.length ? cat.transitions : base.transitions,
      filters: cat?.filters?.length ? cat.filters : base.filters,
      stickers: cat?.stickers ?? [],
      textPresets: cat?.textPresets?.length ? cat.textPresets : base.textPresets,
      backgrounds: cat?.backgrounds ?? [],
      fonts: fonts && fonts.length ? fonts : base.fonts,
    }
    registerFonts(merged.fonts)
    set({ catalog: merged, loaded: !!cat })
  },
}))

export function stickerUrl(id: string): string {
  const s = useCatalog.getState().catalog.stickers.find((x) => x.id === id)
  return s?.url || `/api/assets/sticker/${id}.png`
}

export const TRANSITION_LABEL: Record<string, string> = {
  fade: 'Fade', slideleft: 'Slide left', slideright: 'Slide right', slideup: 'Slide up', slidedown: 'Slide down',
  zoomin: 'Zoom in', wipeleft: 'Wipe left', wiperight: 'Wipe right',
}
