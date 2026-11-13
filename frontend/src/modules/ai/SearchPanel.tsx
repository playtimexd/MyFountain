import { useEffect, useState } from 'react'
import { FolderSearch, Search } from 'lucide-react'
import { api, useJob } from '../../api/client'
import { JobBar } from '../../shared/ui'
import type { MediaRow } from '../../types/api'
import { Note } from './aiUtil'
import type { AiPanelProps } from './aiUtil'

interface Hit { mediaId?: string; path: string; kind: string; score: number; snippet: string; start?: number; end?: number; source?: string; media?: MediaRow; error?: string }
interface Status {
  indexed: number; docs: number; roots: string[]
  last: { indexed: number; skipped: number; errors: string[]; captions?: string | null } | null
  capabilities: { asr: { ready: boolean; message: string }; clip: { ready: boolean; message: string }; vision_model: string | null; embed: boolean }
}

const KINDS = [{ v: 'video', label: 'Video' }, { v: 'audio', label: 'Audio' }, { v: 'image', label: 'Image' }]
const SRC_LABEL: Record<string, string> = { asr: 'Speech', caption: 'Visual', frame: 'Visual', meta: 'File name' }

export default function SearchPanel({ onAddAsset, standalone }: AiPanelProps) {
  const [folder, setFolder] = useState(() => { try { return localStorage.getItem('cutlab.ai.folder') || '' } catch { return '' } })
  const [asr, setAsr] = useState(true)
  const [caption, setCaption] = useState(false)
  const [status, setStatus] = useState<Status | null>(null)
  const [q, setQ] = useState('')
  const [kinds, setKinds] = useState<string[]>([])
  const [hits, setHits] = useState<Hit[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [err, setErr] = useState('')
  const [added, setAdded] = useState<Record<string, boolean>>({})
  const { job, error, run, cancel, busy } = useJob()

  const loadStatus = () => api.get<Status>('/api/search/status').then(setStatus).catch(() => setStatus(null))
  useEffect(() => { loadStatus() }, [])

  const index = async () => {
    const paths = folder.split(/[\n;]+/).map((s) => s.trim()).filter(Boolean)
    if (!paths.length) { setErr('Enter the folder path(s) to index'); return }
    setErr('')
    try { localStorage.setItem('cutlab.ai.folder', folder) } catch { /* ignore */ }
    try {
      await run(() => api.post('/api/search/index', { paths, recursive: true, asr: asr ? undefined : false, caption: caption }))
    } catch { /* JobBar */ }
    loadStatus()
  }

  const search = async () => {
    if (!q.trim()) return
    setSearching(true); setErr('')
    try {
      const r = await api.post<{ hits: Hit[] }>('/api/search/query', { q, kinds: kinds.length ? kinds : undefined, limit: 24 })
      setHits(r.hits)
    } catch (e: any) { setErr(e.message); setHits(null) }
    setSearching(false)
  }
  const toggle = (k: string) => setKinds((x) => (x.includes(k) ? x.filter((y) => y !== k) : [...x, k]))
  const cap = status?.capabilities

  return (
    <div style={{ padding: 12, overflow: 'auto', height: '100%' }}>
      <div className="row" style={{ marginBottom: 10 }}><FolderSearch size={16} /><b>Local Media Search</b>
        <span className="grow" /><span className="faint" style={{ fontSize: 11 }}>{status ? `${status.indexed} file(s) indexed` : ''}</span></div>

      <div className="field"><label>Folders to index (separate several with new lines or ;)</label>
        <textarea className="textarea" rows={2} value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="D:\Footage\Trip" /></div>
      <div className="row" style={{ flexWrap: 'wrap', fontSize: 12, marginBottom: 6 }}>
        <label className="row" title={cap?.asr.ready ? '' : cap?.asr.message}><input type="checkbox" checked={asr} disabled={!cap?.asr.ready} onChange={(e) => setAsr(e.target.checked)} />Transcribe speech</label>
        <label className="row" title={cap?.vision_model ? `Uses ${cap.vision_model} (slow)` : 'No Ollama vision model installed'}>
          <input type="checkbox" checked={caption} disabled={!cap?.vision_model} onChange={(e) => setCaption(e.target.checked)} />Describe visuals (slow)
        </label>
      </div>
      <div className="row" style={{ flexWrap: 'wrap', gap: 4, marginBottom: 6 }}>
        <span className="chip" style={{ color: cap?.embed ? 'var(--ok)' : undefined }}>{cap?.embed ? '●' : '○'} Text embeddings</span>
        <span className="chip" style={{ color: cap?.asr.ready ? 'var(--ok)' : undefined }} title={cap?.asr.message}>{cap?.asr.ready ? '●' : '○'} Speech</span>
        <span className="chip" style={{ color: cap?.clip.ready ? 'var(--ok)' : undefined }} title={cap?.clip.message}>{cap?.clip.ready ? '●' : '○'} CLIP</span>
        <span className="chip" style={{ color: cap?.vision_model ? 'var(--ok)' : undefined }} title={cap?.vision_model || ''}>{cap?.vision_model ? '●' : '○'} Vision captions</span>
      </div>
      <div className="row">
        <button className="btn" disabled={busy} onClick={index}>{busy ? 'Indexing...' : (status?.indexed ? 'Update index' : 'Build index')}</button>
        {status?.roots.length ? <span className="faint grow" style={{ fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={status.roots.join('\n')}>{status.roots.length} folder(s)</span> : null}
      </div>
      <JobBar job={job} error={error} onCancel={cancel} />
      {job?.status === 'done' && job.result && <Note tone="ok">Added {(job.result as any).indexed} · skipped {(job.result as any).skipped}{(job.result as any).errors?.length ? ` · ${(job.result as any).errors.length} error(s)` : ''}</Note>}
      {err && <div className="err">{err}</div>}

      <div style={{ borderTop: '1px solid var(--line)', margin: '12px 0' }} />
      <div className="row">
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && search()} placeholder="Search: sunset at the beach / interview mentioning price ..." />
        <button className="btn primary icon" disabled={searching} onClick={search} title="Search"><Search size={14} /></button>
      </div>
      <div className="row" style={{ gap: 4, margin: '6px 0' }}>
        {KINDS.map((k) => (
          <button key={k.v} className="chip" onClick={() => toggle(k.v)}
            style={{ cursor: 'pointer', border: '1px solid ' + (kinds.includes(k.v) ? 'var(--accent)' : 'transparent'), background: kinds.includes(k.v) ? 'var(--accent-soft)' : undefined }}>{k.label}</button>
        ))}
      </div>

      {hits && hits.length === 0 && <div className="faint" style={{ padding: 16, textAlign: 'center' }}>Nothing found.{status?.indexed ? '' : ' Build an index first.'}</div>}
      {hits && hits.length > 0 && (
        <div className="media-grid" style={{ gridTemplateColumns: standalone ? 'repeat(auto-fill, minmax(190px, 1fr))' : 'repeat(auto-fill, minmax(130px, 1fr))' }}>
          {hits.map((h) => h.media && (
            <div key={h.path} className="media-card" title={h.path} onClick={() => { onAddAsset(h.media!, { toTimeline: false }); setAdded((a) => ({ ...a, [h.path]: true })) }}>
              <img src={h.media.thumb} alt="" loading="lazy" />
              {h.media.duration > 0 && <span className="dur">{h.media.duration.toFixed(0)}s</span>}
              <div className="cap" style={{ fontWeight: 600 }}>{h.media.name}</div>
              <div className="cap faint" style={{ whiteSpace: 'normal', maxHeight: 48, overflow: 'hidden' }}>
                <span className="chip" style={{ marginRight: 4 }}>{SRC_LABEL[h.source || ''] || 'Match'}{h.start != null ? ` ${h.start.toFixed(0)}s` : ''}</span>{h.snippet}
              </div>
              {added[h.path] && <div className="cap" style={{ color: 'var(--ok)' }}>✓ {standalone ? 'In library' : 'Added'}</div>}
            </div>
          ))}
        </div>
      )}
      {hits && hits.length > 0 && <Note>{standalone ? 'Matching files were added to the library automatically.' : 'Click a card to add it to the editor.'}</Note>}
    </div>
  )
}
