import { useEffect, useState } from 'react'
import { Check, Copy, Download, X } from 'lucide-react'
import { api } from '../../api/client'
import { useJobSafe as useJob } from './hooks'
import type { MediaRow } from '../../types/api'
import { projectDuration } from '../../types/project'
import { JobBar } from '../../shared/ui'
import { emit } from '../../shared/bus'
import { useProject } from './store'
import { useMedia } from './data'
import { fmtTC } from './ui'

type Fmt = 'mp4' | 'webm' | 'gif' | 'mp3'

export default function ExportDialog({ onClose }: { onClose: () => void }) {
  const project = useProject((s) => s.project)
  const [format, setFormat] = useState<Fmt>('mp4')
  const [quality, setQuality] = useState<'draft' | 'normal' | 'high'>('normal')
  const [res, setRes] = useState<'orig' | '1080' | '720' | '480'>('orig')
  const [hw, setHw] = useState(true)
  const [copied, setCopied] = useState(false)
  const job = useJob<{ media: MediaRow; file: string }>()
  const dur = projectDuration(project)
  const result = job.job?.status === 'done' ? job.job.result : null

  useEffect(() => { const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose(); window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])

  const size = () => {
    if (res === 'orig') return {}
    const short = +res
    const k = short / Math.min(project.width, project.height)
    return { width: Math.round((project.width * k) / 2) * 2, height: Math.round((project.height * k) / 2) * 2 }
  }

  const start = async () => {
    try {
      const j = await job.run(() => api.post('/api/export', { project, preset: { ...size(), fps: project.fps, format, quality, hwaccel: hw } }))
      const m = j.result?.media
      if (m) { useMedia.getState().upsert(m); emit('cutlab:media-changed', { media: m }) }
    } catch { /* JobBar */ }
  }

  const copy = async (s: string) => { try { await navigator.clipboard.writeText(s); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* ignore */ } }

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ width: 'min(560px, 94vw)' }}>
        <header><Download size={16} />Export video<span className="grow" /><button className="btn ghost icon" onClick={onClose}><X size={16} /></button></header>
        <div className="body">
          <div className="faint" style={{ marginBottom: 10 }}>{project.name} · {project.width}×{project.height} · {project.fps}fps · Duration {fmtTC(dur, project.fps)}</div>
          <div className="ve-formgrid">
            <label>Format</label>
            <div className="ve-chips">{(['mp4', 'webm', 'gif', 'mp3'] as Fmt[]).map((f) => <button key={f} className={`ve-chipbtn ${format === f ? 'on' : ''}`} onClick={() => setFormat(f)}>{f.toUpperCase()}</button>)}</div>
            <label>Quality</label>
            <div className="ve-chips">{([['draft', 'Draft'], ['normal', 'Standard'], ['high', 'High']] as const).map(([k, l]) => <button key={k} className={`ve-chipbtn ${quality === k ? 'on' : ''}`} onClick={() => setQuality(k)}>{l}</button>)}</div>
            <label>Resolution</label>
            <div className="ve-chips">{([['orig', 'Project size'], ['1080', '1080p'], ['720', '720p'], ['480', '480p']] as const).map(([k, l]) => <button key={k} className={`ve-chipbtn ${res === k ? 'on' : ''}`} onClick={() => setRes(k)}>{l}</button>)}</div>
            <label>Hardware</label>
            <div><input type="checkbox" checked={hw} onChange={(e) => setHw(e.target.checked)} /> <span className="muted">NVENC (when available)</span></div>
          </div>
          <div className="row" style={{ marginTop: 14 }}>
            <button className="btn primary" onClick={start} disabled={job.busy || dur <= 0}><Download size={14} />{job.busy ? 'Exporting…' : 'Start export'}</button>
            {dur <= 0 && <span className="err" style={{ margin: 0 }}>Timeline is empty</span>}
          </div>
          <JobBar job={job.job} error={job.error} onCancel={job.cancel} />
          {result && (
            <div className="ve-result">
              <div className="row" style={{ color: 'var(--ok)' }}><Check size={14} />Export finished and added to the media library</div>
              {result.media.kind === 'video' && <video src={result.media.url} controls style={{ width: '100%', marginTop: 8, borderRadius: 8, background: '#000', maxHeight: 280 }} />}
              {result.media.kind === 'audio' && <audio src={result.media.url} controls style={{ width: '100%', marginTop: 8 }} />}
              {result.media.kind === 'image' && <img src={result.media.url} style={{ width: '100%', marginTop: 8, borderRadius: 8 }} alt="" />}
              <div className="row" style={{ marginTop: 8 }}>
                <code className="ve-path grow" title={result.file}>{result.file}</code>
                <button className="btn sm" onClick={() => copy(result.file)}>{copied ? <Check size={13} /> : <Copy size={13} />}Copy path</button>
                <a className="btn sm" href={result.media.url} download={result.media.name}><Download size={13} />Download</a>
              </div>
              <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>Open the folder of the path above in your file manager to find the file.</div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
