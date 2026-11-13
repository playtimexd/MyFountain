import { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { api } from '../../api/client'
import type { MediaRow } from '../../types/api'

export type MediaKind = 'video' | 'audio' | 'image'

/** Loads /api/media (optionally filtered) and keeps it fresh. */
export function useMediaList(kinds?: MediaKind[]) {
  const [items, setItems] = useState<MediaRow[]>([])
  const [loading, setLoading] = useState(false)
  const key = (kinds || []).join(',')
  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const all = await api.get<MediaRow[]>('/api/media?limit=500')
      setItems(kinds && kinds.length ? all.filter((m) => kinds.includes(m.kind)) : all)
    } catch { /* engine offline */ }
    setLoading(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  useEffect(() => { reload() }, [reload])
  return { items, loading, reload }
}

const KIND_LABEL: Record<string, string> = { video: 'Video', audio: 'Audio', image: 'Image' }

/** Compact media chooser: thumbnail + dropdown. `value` is a media id. */
export default function MediaPicker({ value, onChange, kinds, label = 'Target media', extra = [] }: {
  value: string | null
  onChange: (m: MediaRow | null) => void
  kinds?: MediaKind[]
  label?: string
  /** media rows to always include (e.g. a freshly generated file not yet in the cached list) */
  extra?: MediaRow[]
}) {
  const { items, loading, reload } = useMediaList(kinds)
  const all = [...extra.filter((e) => !items.some((i) => i.id === e.id) && (!kinds || kinds.includes(e.kind))), ...items]
  const cur = all.find((m) => m.id === value) || null
  return (
    <div className="field">
      <label>{label}</label>
      <div className="row" style={{ alignItems: 'stretch' }}>
        <div style={{ width: 64, height: 40, flex: 'none', background: '#000', borderRadius: 4, overflow: 'hidden', border: '1px solid var(--line)' }}>
          {cur && <img src={cur.thumb} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />}
        </div>
        <select className="select grow" value={value || ''} onChange={(e) => onChange(all.find((m) => m.id === e.target.value) || null)}>
          <option value="">{all.length ? '— Select media —' : 'Library is empty, import something first'}</option>
          {all.map((m) => (
            <option key={m.id} value={m.id}>[{KIND_LABEL[m.kind]}] {m.name}{m.duration ? ` · ${m.duration.toFixed(1)}s` : ''}</option>
          ))}
        </select>
        <button className="btn icon" onClick={reload} title="Refresh media list"><RefreshCw size={13} className={loading ? 'spin' : ''} /></button>
      </div>
      {cur && <div className="faint" style={{ fontSize: 11 }}>
        {cur.width ? `${cur.width}×${cur.height}` : ''}{cur.fps ? ` · ${Math.round(cur.fps)}fps` : ''}{cur.duration ? ` · ${cur.duration.toFixed(1)}s` : ''}{cur.has_audio ? ' · has audio' : ''}
      </div>}
    </div>
  )
}
