// Persistent history of finished background jobs (downloads, AI processing, imports …) for the tool areas,
// which work on single files rather than documents. Backed by /api/projects/activity (recorded by the engine).
import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, CircleSlash, Clapperboard, Image as ImageIcon, RefreshCw, Trash2, XCircle } from 'lucide-react'
import { api } from '../../api/client'
import type { MediaRow } from '../../types/api'
import { emit } from '../bus'
import { projectsApi, timeAgo, type ActivityRow } from './api'
import { ConfirmDialog } from './ProjectHome'
import './projects.css'

const ICON = { done: CheckCircle2, error: XCircle, cancelled: CircleSlash }
const COLOR = { done: 'var(--ok)', error: 'var(--danger)', cancelled: 'var(--text-faint)' }

export default function ActivityHistory({ areas, title = 'History', empty = 'Nothing here yet. Finished jobs appear here and stay after restarts.', limit = 60, compact }: {
  areas: string[]; title?: string; empty?: string; limit?: number; compact?: boolean
}) {
  const [rows, setRows] = useState<ActivityRow[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'done' | 'error'>('all')
  const [confirm, setConfirm] = useState(false)
  const key = areas.join(',')
  const load = useCallback(() => {
    projectsApi.activity(key.split(','), limit).then((r) => { setRows(r); setErr(null) }).catch((e) => setErr(e.message))
  }, [key, limit])
  useEffect(() => {
    load()
    const t = setInterval(() => { if (document.visibilityState === 'visible') load() }, 6000)
    return () => clearInterval(t)
  }, [load])

  const sendMedia = async (id: string, action: 'timeline' | 'image') => {
    try {
      const m = await api.get<MediaRow>(`/api/media/${id}`)
      emit(action === 'image' ? 'cutlab:open-image' : 'cutlab:add-to-timeline', { media: m })
    } catch { setErr('That file is no longer in the library.') }
  }
  const shown = (rows || []).filter((r) => filter === 'all' || (filter === 'done' ? r.status === 'done' : r.status !== 'done'))

  return (
    <div className="panel" style={{ display: 'flex', flexDirection: 'column', minHeight: 0, ...(compact ? {} : { flex: 1 }) }}>
      <div className="panel-title">
        <span className="grow">{title}</span>
        <div className="seg-tabs" style={{ padding: 2 }}>
          {(['all', 'done', 'error'] as const).map((f) => <button key={f} className={filter === f ? 'on' : ''} style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => setFilter(f)}>{f === 'all' ? 'All' : f === 'done' ? 'Finished' : 'Failed'}</button>)}
        </div>
        <button className="btn ghost icon sm" title="Refresh" onClick={load}><RefreshCw size={13} /></button>
        <button className="btn ghost icon sm" title="Clear history" disabled={!rows?.length} onClick={() => setConfirm(true)}><Trash2 size={13} /></button>
      </div>
      <div style={{ padding: 10, overflow: 'auto', minHeight: 0 }}>
        {err && <div className="err" style={{ marginBottom: 8 }}>{err}</div>}
        {rows && !shown.length && <div className="faint" style={{ textAlign: 'center', padding: 24 }}>{rows.length ? 'No entries match this filter.' : empty}</div>}
        <div className="ph-activity">
          {shown.map((r) => {
            const I = ICON[r.status] || CheckCircle2
            const media = r.result?.media || []
            const first = media[0]
            return (
              <div key={r.id} className="ph-act">
                {first ? <img src={first.thumb} alt="" loading="lazy" /> : <I size={18} style={{ color: COLOR[r.status], flex: 'none', margin: '0 23px' }} />}
                <div className="t">
                  <div title={r.title}><I size={12} style={{ color: COLOR[r.status], verticalAlign: -2, marginRight: 5 }} />{r.title}</div>
                  <div className="faint" style={{ fontSize: 11 }} title={r.error || ''}>
                    {timeAgo(r.finished)}
                    {r.status === 'error' && r.error ? ` · ${r.error}` : ''}
                    {r.status === 'cancelled' ? ' · cancelled' : ''}
                    {media.length ? ` · ${media.length === 1 ? first!.name : `${media.length} files`}` : ''}
                  </div>
                </div>
                {first && (first.kind === 'video' || first.kind === 'audio') && <button className="btn sm" title="Add to the video timeline" onClick={() => void sendMedia(first.id, 'timeline')}><Clapperboard size={13} />Timeline</button>}
                {first && first.kind === 'image' && <button className="btn sm" title="Open in the image editor" onClick={() => void sendMedia(first.id, 'image')}><ImageIcon size={13} />Edit</button>}
                <button className="btn ghost icon sm" title="Remove from history" onClick={() => { void projectsApi.removeActivity(r.id).then(load) }}><Trash2 size={12} /></button>
              </div>
            )
          })}
        </div>
      </div>
      {confirm && (
        <ConfirmDialog title="Clear history" confirm="Clear" onClose={() => setConfirm(false)}
          onOk={() => { void Promise.all(areas.map((a) => projectsApi.clearActivity(a))).then(load) }}>
          Remove all {title.toLowerCase()} entries? Files in the media library are not deleted.
        </ConfirmDialog>
      )}
    </div>
  )
}
