import { useEffect, useRef, useState } from 'react'
import { Film, Scissors, X } from 'lucide-react'
import { api, fmtTime, useJob } from '../api/client'
import type { MediaRow, Segment } from '../types/api'
import { emit } from './bus'
import { Field, JobBar } from './ui'

const METHODS = [
  { id: 'scene', label: 'Scene cuts', hint: 'Split automatically at scene changes (good for mixed footage and long downloads)' },
  { id: 'silence', label: 'Silence/speech', hint: 'Split at pauses and keep the parts with sound (good for talking-head videos, interviews)' },
  { id: 'asr', label: 'Speech sentences', hint: 'Split by sentence after speech recognition (needs a Whisper model)' },
  { id: 'fixed', label: 'Fixed length', hint: 'One segment every N seconds' },
] as const

export default function SegmentDialog({ media, onClose }: { media: MediaRow; onClose: () => void }) {
  const [method, setMethod] = useState<(typeof METHODS)[number]['id']>(media.kind === 'video' ? 'scene' : 'silence')
  const [threshold, setThreshold] = useState(27)
  const [db, setDb] = useState(-35)
  const [minLen, setMinLen] = useState(0.6)
  const [every, setEvery] = useState(10)
  const [segs, setSegs] = useState<Segment[]>([])
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [msg, setMsg] = useState('')
  const seg = useJob<{ segments: Segment[] }>()
  const cut = useJob<{ items: MediaRow[] }>()
  const vref = useRef<HTMLVideoElement>(null)

  const run = async () => {
    setMsg('')
    const body: any = { mediaId: media.id, method, min_len: minLen }
    if (method === 'scene') body.threshold = threshold
    if (method === 'silence') body.threshold = db
    if (method === 'fixed') body.every = every
    try {
      const j = await seg.run(() => api.post('/api/segment', body))
      setSegs(j.result?.segments ?? []); setPicked(new Set((j.result?.segments ?? []).map((_, i) => i)))
    } catch { /* shown by JobBar */ }
  }
  useEffect(() => { const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose(); window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])

  const chosen = segs.filter((_, i) => picked.has(i))
  const preview = (s: Segment) => { const v = vref.current; if (v) { v.currentTime = s.start; v.play().catch(() => {}) } }

  const doCut = async () => {
    try {
      const j = await cut.run(() => api.post('/api/segment/cut', { mediaId: media.id, segments: chosen, reencode: false }))
      j.result?.items.forEach((m) => emit('cutlab:media-changed', { media: m }))
      setMsg(`Exported ${j.result?.items.length} segments to the library`)
    } catch { /* JobBar */ }
  }

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ height: '82vh' }}>
        <header><Scissors size={16} />Smart split · <span className="muted">{media.name}</span><span className="grow" />
          <button className="btn ghost icon" onClick={onClose}><X size={16} /></button></header>
        <div className="body" style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 16, minHeight: 0, flex: 1 }}>
          <div>
            <div className="seg-tabs" style={{ flexWrap: 'wrap', marginBottom: 8 }}>
              {METHODS.filter((m) => m.id !== 'scene' || media.kind === 'video').map((m) =>
                <button key={m.id} className={method === m.id ? 'on' : ''} onClick={() => setMethod(m.id)}>{m.label}</button>)}
            </div>
            <div className="faint" style={{ fontSize: 12, marginBottom: 12 }}>{METHODS.find((m) => m.id === method)?.hint}</div>
            {method === 'scene' && <Field label={`Sensitivity threshold ${threshold} (lower = more cuts)`}><input type="range" min={8} max={60} value={threshold} onChange={(e) => setThreshold(+e.target.value)} /></Field>}
            {method === 'silence' && <Field label={`Silence threshold ${db} dB`}><input type="range" min={-60} max={-15} value={db} onChange={(e) => setDb(+e.target.value)} /></Field>}
            {method === 'fixed' && <Field label="Seconds per segment"><input className="input" type="number" min={0.5} step={0.5} value={every} onChange={(e) => setEvery(+e.target.value)} /></Field>}
            {method !== 'fixed' && <Field label={`Minimum segment ${minLen}s`}><input type="range" min={0.2} max={5} step={0.1} value={minLen} onChange={(e) => setMinLen(+e.target.value)} /></Field>}
            <button className="btn primary" onClick={run} disabled={seg.busy}><Film size={14} />Split</button>
            <JobBar job={seg.job} error={seg.error} onCancel={seg.cancel} />
            {media.kind === 'video' && <video ref={vref} src={media.url} controls style={{ width: '100%', marginTop: 14, borderRadius: 8, background: '#000' }} />}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <div className="row" style={{ marginBottom: 8 }}>
              <b>{segs.length ? `${segs.length} segments` : 'Results'}</b>
              {segs.length > 0 && <>
                <span className="faint">Selected {chosen.length} · Total {fmtTime(chosen.reduce((a, s) => a + s.end - s.start, 0))}</span>
                <span className="grow" />
                <button className="btn sm" onClick={() => setPicked(new Set(segs.map((_, i) => i)))}>Select all</button>
                <button className="btn sm" onClick={() => setPicked(new Set())}>Clear</button>
              </>}
            </div>
            <div style={{ overflow: 'auto', flex: 1, border: '1px solid var(--line)', borderRadius: 8 }}>
              {segs.map((s, i) => (
                <div key={i} className="row" style={{ padding: '6px 10px', borderBottom: '1px solid var(--line)', cursor: 'pointer' }} onClick={() => preview(s)}>
                  <input type="checkbox" checked={picked.has(i)} onClick={(e) => e.stopPropagation()}
                    onChange={() => setPicked((p) => { const n = new Set(p); n.has(i) ? n.delete(i) : n.add(i); return n })} />
                  <span className="chip">{i + 1}</span>
                  <span className="grow" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.text || s.label}</span>
                  <span className="faint" style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{fmtTime(s.start)} → {fmtTime(s.end)}</span>
                  <span className="muted" style={{ width: 48, textAlign: 'right' }}>{(s.end - s.start).toFixed(1)}s</span>
                </div>
              ))}
              {!segs.length && <div className="faint" style={{ padding: 28, textAlign: 'center' }}>Choose a method, then click "Split"</div>}
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn" disabled={!chosen.length || cut.busy} onClick={doCut}><Scissors size={14} />Export selected as media</button>
              <button className="btn primary" disabled={!chosen.length || media.kind !== 'video'}
                onClick={() => { emit('cutlab:add-segments', { media, segments: chosen }); onClose() }}>Add to timeline</button>
              <span className="muted" style={{ fontSize: 12 }}>{msg}</span>
            </div>
            <JobBar job={cut.job} error={cut.error} />
          </div>
        </div>
      </div>
    </div>
  )
}
