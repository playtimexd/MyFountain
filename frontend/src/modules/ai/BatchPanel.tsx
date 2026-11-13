import { useMemo, useRef, useState } from 'react'
import { CheckCircle2, Circle, Layers, Loader2, Play, RefreshCw, Square, XCircle } from 'lucide-react'
import { api, waitJob } from '../../api/client'
import { Callout, Empty, Field } from '../../shared/ui'
import type { MediaRow } from '../../types/api'
import { emit } from '../../shared/bus'
import { useMediaList } from './MediaPicker'
import type { MediaKind } from './MediaPicker'

type ActionId = 'upscale2' | 'upscale4' | 'watermark' | 'removebg'
const ACTIONS: { id: ActionId; label: string; kinds: MediaKind[]; hint: string }[] = [
  { id: 'upscale2', label: 'Upscale 2×', kinds: ['video', 'image'], hint: 'Super-resolution with the best engine installed (Real-ESRGAN, else Lanczos + sharpen).' },
  { id: 'upscale4', label: 'Upscale 4×', kinds: ['video', 'image'], hint: 'Same as 2×, four times the size. Slow on long videos.' },
  { id: 'watermark', label: 'Remove watermark (auto-detect)', kinds: ['video', 'image'], hint: 'Detects the watermark in each file and inpaints it. Files with nothing detected are skipped. For tricky logos, box them by hand in the Video or Image Editor.' },
  { id: 'removebg', label: 'Remove background (images)', kinds: ['image'], hint: 'Cuts out the subject and saves a transparent PNG next to the original.' },
]

type Status = { state: 'queued' | 'running' | 'done' | 'skipped' | 'error'; progress?: number; msg?: string }

/** Run one AI action over many library files. Every result is saved to the media library. */
export default function BatchPanel() {
  const [action, setAction] = useState<ActionId>('upscale2')
  const def = ACTIONS.find((a) => a.id === action)!
  const { items, loading, reload } = useMediaList()
  const usable = useMemo(() => items.filter((m) => def.kinds.includes(m.kind)), [items, def])
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [status, setStatus] = useState<Record<string, Status>>({})
  const [running, setRunning] = useState(false)
  const stop = useRef(false)
  const jobId = useRef<string | null>(null)

  const sel = usable.filter((m) => picked.has(m.id))
  const toggle = (id: string) => setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const put = (id: string, st: Status) => setStatus((s) => ({ ...s, [id]: st }))

  const job = async (start: Promise<any>, id: string) => {
    const j0 = await start
    jobId.current = j0.id
    const done = await waitJob(j0, (j) => put(id, { state: 'running', progress: j.progress, msg: j.message }))
    jobId.current = null
    return done.result
  }

  const runOne = async (m: MediaRow) => {
    put(m.id, { state: 'running', progress: 0 })
    if (action === 'upscale2' || action === 'upscale4') {
      await job(api.post('/api/ai/upscale', { mediaId: m.id, scale: action === 'upscale2' ? 2 : 4, model: 'auto', denoise: false }), m.id)
    } else if (action === 'watermark') {
      const det = await api.post('/api/ai/watermark/detect', { mediaId: m.id })
      if (!det.regions?.length) { put(m.id, { state: 'skipped', msg: det.message || 'No watermark found' }); return }
      await job(api.post('/api/ai/watermark', { mediaId: m.id, regions: det.regions.map(({ x, y, w, h }: any) => ({ x, y, w, h })), method: 'auto' }), m.id)
    } else {
      const r = await api.post('/api/ai/remove-bg', { mediaId: m.id })
      const blob = await (await fetch(`data:image/png;base64,${r.imageBase64}`)).blob()
      await api.upload('/api/media/upload', new File([blob], `${m.name.replace(/\.[^.]+$/, '')}_cutout.png`, { type: 'image/png' }))
    }
    put(m.id, { state: 'done' })
  }

  const start = async () => {
    stop.current = false
    setRunning(true)
    setStatus(Object.fromEntries(sel.map((m) => [m.id, { state: 'queued' } as Status])))
    for (const m of sel) {
      if (stop.current) break
      try { await runOne(m) } catch (e: any) { put(m.id, { state: 'error', msg: e?.message || String(e) }) }
    }
    setRunning(false)
    emit('cutlab:media-changed', {})
    void reload()
  }
  const cancel = () => { stop.current = true; if (jobId.current) void api.post(`/api/jobs/${jobId.current}/cancel`).catch(() => {}) }

  const done = Object.values(status).filter((s) => s.state === 'done').length
  const failed = Object.values(status).filter((s) => s.state === 'error').length

  return (
    <div style={{ padding: '14px 16px', overflow: 'auto', height: '100%' }}>
      <div className="row" style={{ marginBottom: 4 }}><Layers size={16} /><b>Batch processing</b></div>
      <div className="faint" style={{ fontSize: 12, marginBottom: 12 }}>
        Run one AI tool over many files. For a single clip or image, use the AI tools inside the Video Editor (right-click a clip) or the Image Editor (AI menu).
      </div>
      <Field label="Action">
        <select className="select" value={action} disabled={running} onChange={(e) => { setAction(e.target.value as ActionId); setStatus({}) }}>
          {ACTIONS.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
        </select>
      </Field>
      <div className="faint" style={{ fontSize: 11.5, margin: '-4px 0 12px' }}>{def.hint}</div>

      <div className="row" style={{ marginBottom: 8 }}>
        <span className="ui-section-label grow">Files · {sel.length} of {usable.length} selected</span>
        <button className="btn ghost sm" disabled={running || !usable.length} onClick={() => setPicked(new Set(usable.map((m) => m.id)))}>Select all</button>
        <button className="btn ghost sm" disabled={running || !picked.size} onClick={() => setPicked(new Set())}>Clear</button>
        <button className="btn ghost icon sm" title="Refresh" onClick={() => void reload()}><RefreshCw size={13} className={loading ? 'spin' : ''} /></button>
      </div>

      {!usable.length ? (
        <Empty icon={Layers} title={loading ? 'Loading library…' : 'No matching files'}>
          {def.kinds.map((k) => k[0].toUpperCase() + k.slice(1)).join(' and ')} files from the Library appear here.
        </Empty>
      ) : (
        <div className="batch-grid">
          {usable.map((m) => {
            const st = status[m.id]
            const on = picked.has(m.id)
            return (
              <button key={m.id} className={`batch-card ${on ? 'on' : ''}`} disabled={running} onClick={() => toggle(m.id)} title={m.name}>
                <span className="th"><img src={m.thumb} alt="" loading="lazy" />
                  <span className={`ck ${on ? 'on' : ''}`}>{on && <CheckCircle2 size={14} />}</span>
                  {st && <span className={`st ${st.state}`}>
                    {st.state === 'queued' && <Circle size={12} />}
                    {st.state === 'running' && <><Loader2 size={12} className="spin" />{Math.round((st.progress || 0) * 100)}%</>}
                    {st.state === 'done' && <CheckCircle2 size={12} />}
                    {st.state === 'skipped' && 'Skipped'}
                    {st.state === 'error' && <XCircle size={12} />}
                  </span>}
                </span>
                <span className="nm">{m.name}</span>
                {st?.msg && (st.state === 'error' || st.state === 'skipped') && <span className="er">{st.msg}</span>}
              </button>
            )
          })}
        </div>
      )}

      <div className="batch-foot">
        {running
          ? <button className="btn danger" onClick={cancel}><Square size={13} />Stop</button>
          : <button className="btn primary" disabled={!sel.length} onClick={() => void start()}><Play size={14} />Run on {sel.length || 0} file{sel.length === 1 ? '' : 's'}</button>}
        {Object.keys(status).length > 0 && <span className="muted" style={{ fontSize: 12 }}>{done} done{failed ? ` · ${failed} failed` : ''}{running ? '…' : ''}</span>}
      </div>
      {!running && done > 0 && <Callout tone="ok" style={{ marginTop: 10 }}>Results are saved to the Library.</Callout>}
    </div>
  )
}
