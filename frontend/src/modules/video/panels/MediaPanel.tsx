import { useEffect, useRef, useState } from 'react'
import { Download, FolderInput, Plus, RefreshCw, Scissors, Upload, Music, Image as ImageIcon } from 'lucide-react'
import { api, fmtTime } from '../../../api/client'
import { useJobSafe as useJob } from '../hooks'
import type { MediaRow } from '../../../types/api'
import { JobBar } from '../../../shared/ui'
import SegmentDialog from '../../../shared/SegmentDialog'
import { emit, on } from '../../../shared/bus'
import { useMedia } from '../data'
import { useProject } from '../store'
import { DND_MIME, hasFiles, uploadFiles } from '../actions'
import type { DndPayload } from '../actions'

export function MediaCard({ m, onSegment }: { m: MediaRow; onSegment?: (m: MediaRow) => void }) {
  const addMedia = useProject((s) => s.addMedia)
  return (
    <div className="ve-mcard" draggable title={m.name}
      onDragStart={(e) => { const pl: DndPayload = { type: 'media', id: m.id }; e.dataTransfer.setData(DND_MIME, JSON.stringify(pl)); e.dataTransfer.effectAllowed = 'copy' }}
      onDoubleClick={() => addMedia(m)}>
      <div className="ve-mthumb">
        <img src={m.thumb} loading="lazy" alt="" draggable={false} />
        {m.kind === 'audio' && <Music size={20} className="ve-mico" />}
        {m.kind === 'image' && <ImageIcon size={12} className="ve-mkind" />}
        {m.kind !== 'image' && <span className="ve-mdur">{fmtTime(m.duration)}</span>}
        <div className="ve-mact">
          <button title="Add to timeline" onClick={(e) => { e.stopPropagation(); addMedia(m) }}><Plus size={14} /></button>
          {onSegment && m.kind !== 'image' && <button title="Smart split" onClick={(e) => { e.stopPropagation(); onSegment(m) }}><Scissors size={13} /></button>}
        </div>
      </div>
      <div className="ve-mname">{m.name}</div>
    </div>
  )
}

const FILTER_LABEL = { all: 'All', video: 'Video', audio: 'Audio', image: 'Images' } as const

export default function MediaPanel({ kinds }: { kinds?: MediaRow['kind'][] }) {
  const { items, loading, error, refresh } = useMedia()
  const [filter, setFilter] = useState<'all' | MediaRow['kind']>('all')
  const [paths, setPaths] = useState('')
  const [link, setLink] = useState('')
  const [seg, setSeg] = useState<MediaRow | null>(null)
  const [msg, setMsg] = useState('')
  const [over, setOver] = useState(false)
  const imp = useJob<{ items: MediaRow[]; errors: any[] }>()
  const dl = useJob<{ media: MediaRow }>()
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => on('cutlab:media-changed', () => { void refresh() }), [refresh])

  const shown = items.filter((m) => (!kinds || kinds.includes(m.kind)) && (filter === 'all' || m.kind === filter))

  const doImport = async () => {
    const list = paths.split(/[\n;]+/).map((s) => s.trim().replace(/^"|"$/g, '')).filter(Boolean)
    if (!list.length) return
    try {
      const j = await imp.run(() => api.post('/api/media/import', { paths: list, recursive: true, copy: false }))
      const n = j.result?.items?.length ?? 0
      j.result?.items?.forEach((m) => useMedia.getState().upsert(m))
      setMsg(`Imported ${n} item(s)${j.result?.errors?.length ? `, ${j.result.errors.length} failed` : ''}`)
      setPaths('')
      emit('cutlab:media-changed', {})
    } catch { /* JobBar */ }
  }
  const doDownload = async () => {
    if (!link.trim()) return
    try {
      const j = await dl.run(() => api.post('/api/download', { url: link.trim(), quality: 'best' }))
      const m = j.result?.media
      if (m) { useMedia.getState().upsert(m); emit('cutlab:media-changed', { media: m }); setMsg(`Downloaded: ${m.name}`) }
      setLink('')
    } catch { /* JobBar */ }
  }
  const doUpload = async (files: File[]) => {
    if (!files.length) return
    const rows = await uploadFiles(files, setMsg)
    if (rows.length) { setMsg(`Uploaded ${rows.length} file(s)`); emit('cutlab:media-changed', { media: rows[0] }) }
  }

  return (
    <div className="ve-panel-body"
      onDragOver={(e) => { if (hasFiles(e.dataTransfer)) { e.preventDefault(); setOver(true) } }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { if (hasFiles(e.dataTransfer)) { e.preventDefault(); setOver(false); void doUpload(Array.from(e.dataTransfer.files)) } }}>
      <div className={`ve-dropzone ${over ? 'on' : ''}`} onClick={() => fileRef.current?.click()}>
        <Upload size={16} /> Drop files here to upload, or click to choose
        <input ref={fileRef} type="file" multiple hidden accept="video/*,audio/*,image/*" onChange={(e) => { void doUpload(Array.from(e.target.files || [])); e.target.value = '' }} />
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <input className="input" placeholder="Import local path (file or folder; separate several with ;)" value={paths} onChange={(e) => setPaths(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && doImport()} />
        <button className="btn sm" onClick={doImport} disabled={imp.busy} title="Import local path"><FolderInput size={14} />Import</button>
      </div>
      <JobBar job={imp.busy ? imp.job : null} error={imp.error} />
      <div className="row" style={{ marginTop: 6 }}>
        <input className="input" placeholder="Paste a video link to download…" value={link} onChange={(e) => setLink(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && doDownload()} />
        <button className="btn sm" onClick={doDownload} disabled={dl.busy}><Download size={14} />Download</button>
      </div>
      <JobBar job={dl.busy ? dl.job : null} error={dl.error} onCancel={dl.cancel} />
      {msg && <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>{msg}</div>}
      <div className="row" style={{ margin: '10px 0 6px' }}>
        {!kinds && (['all', 'video', 'audio', 'image'] as const).map((k) => (
          <button key={k} className={`ve-chipbtn ${filter === k ? 'on' : ''}`} onClick={() => setFilter(k)}>{FILTER_LABEL[k]}</button>
        ))}
        <div className="grow" />
        <button className="ve-ibtn" title="Refresh" onClick={() => void refresh()}><RefreshCw size={13} className={loading ? 'spin' : ''} /></button>
      </div>
      {error && <div className="err">{error}</div>}
      <div className="ve-mgrid">
        {shown.map((m) => <MediaCard key={m.id} m={m} onSegment={setSeg} />)}
      </div>
      {!shown.length && !loading && <div className="faint" style={{ textAlign: 'center', padding: 24 }}>No media yet. Import or drop files.</div>}
      {seg && <SegmentDialog media={seg} onClose={() => setSeg(null)} />}
    </div>
  )
}
