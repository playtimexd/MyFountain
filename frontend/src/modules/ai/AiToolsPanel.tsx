import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Eraser, Film, Scissors, Subtitles, Wand2, ScanSearch, Trash2, Download } from 'lucide-react'
import { api, useJob } from '../../api/client'
import { Empty, Field, JobBar } from '../../shared/ui'
import type { MediaRow } from '../../types/api'
import type { Op } from '../../types/project'
import MediaPicker from './MediaPicker'
import { Note, Section, Seg, clipsOfMedia, downloadText, fmtDur, guessMediaId, mapRange } from './aiUtil'
import type { AiPanelProps } from './aiUtil'
import { useAiFocus } from './focus'
import type { AiToolId } from './focus'

interface Region { x: number; y: number; w: number; h: number; score?: number }

function useTargetMedia(props: AiPanelProps) {
  const guess = guessMediaId(props)
  const [id, setId] = useState<string | null>(guess)
  const [manual, setManual] = useState(false)
  const [media, setMedia] = useState<MediaRow | null>(null)
  useEffect(() => { if (!manual || props.standalone) setId(guess) }, [guess, manual, props.standalone])
  useEffect(() => {
    let alive = true
    if (!id) { setMedia(null); return }
    api.get<MediaRow>(`/api/media/${id}`).then((m) => alive && setMedia(m)).catch(() => alive && setMedia(null))
    return () => { alive = false }
  }, [id])
  return {
    id, media,
    setPicked: (m: MediaRow | null) => { setManual(true); setId(m?.id || null); if (m) setMedia(m) },
    /** follow the editor selection again, starting from `mediaId` */
    follow: (mediaId: string | null) => { setManual(false); if (mediaId) setId(mediaId) },
  }
}

const NeedKind = ({ kinds }: { kinds: string }) => <Note tone="warn">Select {kinds} media first</Note>

/* ------------------------------------------------------------------------------------------------ upscale */
function UpscaleSection({ media, onUse, onReplace, standalone }: { media: MediaRow | null; onUse: (m: MediaRow) => void; onReplace?: (m: MediaRow) => void; standalone?: boolean }) {
  const [scale, setScale] = useState(2)
  const [model, setModel] = useState('auto')
  const [denoise, setDenoise] = useState(false)
  const [caps, setCaps] = useState<any>(null)
  const { job, error, run, cancel, busy } = useJob<{ media: MediaRow; engine: string; note: string }>()
  const [res, setRes] = useState<{ media: MediaRow; engine: string; note: string } | null>(null)
  useEffect(() => { api.get('/api/ai/capabilities').then((c) => setCaps(c.upscale)).catch(() => {}) }, [])
  const ok = media && (media.kind === 'video' || media.kind === 'image')
  return (
    <>
      {!ok ? <NeedKind kinds="a video or image" /> : <>
        <Field label="Scale"><Seg value={scale} onChange={setScale} options={[{ v: 2, label: '2×' }, { v: 4, label: '4×' }]} /></Field>
        <Field label="Engine">
          <select className="select" value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="auto">Auto</option>
            <option value="realesrgan" disabled={caps && !caps.ncnn && !caps.python}>Real-ESRGAN (AI){caps && !caps.ncnn && !caps.python ? ' · not installed' : ''}</option>
            <option value="lanczos">Lanczos + sharpen (fast)</option>
          </select>
        </Field>
        <label className="row" style={{ marginBottom: 8, fontSize: 12 }}><input type="checkbox" checked={denoise} onChange={(e) => setDenoise(e.target.checked)} />Denoise too</label>
        <button className="btn primary" disabled={busy} onClick={async () => {
          setRes(null)
          try { const d = await run(() => api.post('/api/ai/upscale', { mediaId: media.id, scale, model, denoise })); setRes(d.result) } catch { /* JobBar */ }
        }}><Wand2 size={14} />Upscale</button>
        {caps && <Note>Current engine: {caps.engine === 'ncnn' ? 'Real-ESRGAN (ncnn)' : caps.engine === 'python' ? 'Real-ESRGAN (python)' : 'Lanczos + sharpen (no AI upscaling model detected)'}</Note>}
        <JobBar job={job} error={error} onCancel={cancel} />
        {res && <ResultCard m={res.media} note={res.note} onUse={onUse} onReplace={onReplace} standalone={standalone} />}
      </>}
    </>
  )
}

function ResultCard({ m, note, onUse, onReplace, standalone }: { m: MediaRow; note?: string; onUse: (m: MediaRow) => void; onReplace?: (m: MediaRow) => void; standalone?: boolean }) {
  const [used, setUsed] = useState(false)
  const [replaced, setReplaced] = useState(false)
  return (
    <div className="panel" style={{ padding: 8, marginTop: 8 }}>
      {m.kind === 'video' ? <video src={m.url} controls style={{ width: '100%', borderRadius: 4, background: '#000' }} />
        : <img src={m.url} alt="" style={{ width: '100%', borderRadius: 4, background: '#000' }} />}
      <div className="faint" style={{ fontSize: 11, margin: '4px 0 6px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.name} · {m.width}×{m.height}</div>
      <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
        {standalone ? <span className="chip" style={{ color: 'var(--ok)' }}>Saved to library</span> : <>
          {onReplace && <button className="btn sm primary" disabled={replaced} title="Swap the clip on the timeline for this result (Ctrl+Z to undo)"
            onClick={() => { onReplace(m); setReplaced(true) }}>{replaced ? 'Clip replaced' : 'Replace clip'}</button>}
          <button className={`btn sm ${onReplace ? '' : 'primary'}`} disabled={used} onClick={() => { onUse(m); setUsed(true) }}>{used ? 'Added to media' : 'Add to media'}</button>
        </>}
      </div>
      {note && <Note>{note}</Note>}
    </div>
  )
}

/* ------------------------------------------------------------------------------------------------ watermark */
function WatermarkSection({ media, onUse, onReplace, standalone }: { media: MediaRow | null; onUse: (m: MediaRow) => void; onReplace?: (m: MediaRow) => void; standalone?: boolean }) {
  const [t, setT] = useState(0)
  const [tCommit, setTCommit] = useState(0)
  const [regions, setRegions] = useState<Region[]>([])
  const [draft, setDraft] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null)
  const [method, setMethod] = useState('auto')
  const [detecting, setDetecting] = useState(false)
  const [msg, setMsg] = useState('')
  const [dim, setDim] = useState<{ w: number; h: number }>({ w: 0, h: 0 })
  const box = useRef<HTMLDivElement>(null)
  const { job, error, run, cancel, busy } = useJob<{ media: MediaRow; engine: string }>()
  const [res, setRes] = useState<{ media: MediaRow; engine: string } | null>(null)
  const W = media?.width || dim.w, H = media?.height || dim.h
  useEffect(() => { setRegions([]); setT(0); setTCommit(0); setRes(null); setMsg('') }, [media?.id])
  if (!media || (media.kind !== 'video' && media.kind !== 'image')) return <NeedKind kinds="a video or image" />

  const frac = (e: React.PointerEvent) => {
    const r = box.current!.getBoundingClientRect()
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) }
  }
  const down = (e: React.PointerEvent) => { try { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId) } catch { /* synthetic pointer */ } const p = frac(e); setDraft({ x0: p.x, y0: p.y, x1: p.x, y1: p.y }) }
  const move = (e: React.PointerEvent) => { if (draft) { const p = frac(e); setDraft({ ...draft, x1: p.x, y1: p.y }) } }
  const up = () => {
    if (!draft || !W) { setDraft(null); return }
    const x = Math.min(draft.x0, draft.x1), y = Math.min(draft.y0, draft.y1), w = Math.abs(draft.x1 - draft.x0), h = Math.abs(draft.y1 - draft.y0)
    if (w > 0.01 && h > 0.01) setRegions((r) => [...r, { x: Math.round(x * W), y: Math.round(y * H), w: Math.round(w * W), h: Math.round(h * H) }])
    setDraft(null)
  }
  const detect = async () => {
    setDetecting(true); setMsg('')
    try {
      const r = await api.post('/api/ai/watermark/detect', { mediaId: media.id })
      setMsg(r.message || '')
      if (r.regions?.length) setRegions(r.regions)
    } catch (e: any) { setMsg(e.message) }
    setDetecting(false)
  }
  const pct = (v: number, total: number) => `${(v / (total || 1)) * 100}%`
  return (
    <>
      <Note>Drag on the picture to box the watermark (several boxes allowed).</Note>
      <div ref={box} style={{ position: 'relative', marginTop: 6, cursor: 'crosshair', touchAction: 'none', background: '#000', borderRadius: 4, overflow: 'hidden', lineHeight: 0 }}
        onPointerDown={down} onPointerMove={move} onPointerUp={up}>
        <img src={`/api/ai/frame?mediaId=${media.id}&t=${tCommit.toFixed(2)}`} alt="" draggable={false} style={{ width: '100%', display: 'block', userSelect: 'none', pointerEvents: 'none' }}
          onLoad={(e) => setDim({ w: (e.target as HTMLImageElement).naturalWidth, h: (e.target as HTMLImageElement).naturalHeight })} />
        {regions.map((r, i) => (
          <div key={i} style={{ position: 'absolute', left: pct(r.x, W), top: pct(r.y, H), width: pct(r.w, W), height: pct(r.h, H), border: '2px solid var(--danger)', background: 'rgba(255,92,108,.2)', pointerEvents: 'none' }}>
            <span style={{ position: 'absolute', left: 0, top: -16, fontSize: 10, background: 'var(--danger)', color: '#fff', padding: '0 3px', lineHeight: '14px' }}>{i + 1}{r.score ? ` · ${(r.score * 100).toFixed(0)}%` : ''}</span>
          </div>
        ))}
        {draft && <div style={{ position: 'absolute', left: `${Math.min(draft.x0, draft.x1) * 100}%`, top: `${Math.min(draft.y0, draft.y1) * 100}%`, width: `${Math.abs(draft.x1 - draft.x0) * 100}%`, height: `${Math.abs(draft.y1 - draft.y0) * 100}%`, border: '2px dashed var(--accent)', background: 'rgba(124,92,255,.2)', pointerEvents: 'none' }} />}
      </div>
      {media.kind === 'video' && media.duration > 0 && (
        <input type="range" min={0} max={media.duration} step={0.1} value={t} onChange={(e) => setT(+e.target.value)}
          onPointerUp={() => setTCommit(t)} onKeyUp={() => setTCommit(t)} style={{ width: '100%', marginTop: 4 }} title={`Preview frame ${t.toFixed(1)}s`} />
      )}
      <div className="row" style={{ flexWrap: 'wrap', marginTop: 4 }}>
        <button className="btn sm" disabled={detecting} onClick={detect}><ScanSearch size={13} />{detecting ? 'Detecting...' : 'Auto-detect'}</button>
        <button className="btn sm ghost" disabled={!regions.length} onClick={() => setRegions([])}><Trash2 size={13} />Clear</button>
        <span className="faint" style={{ fontSize: 11 }}>{regions.length} region(s){W ? ` · source ${W}×${H}` : ''}</span>
      </div>
      {msg && <Note>{msg}</Note>}
      {regions.length > 0 && <div style={{ marginTop: 4 }}>
        {regions.map((r, i) => (
          <div key={i} className="row faint" style={{ fontSize: 11 }}>
            <span className="grow">#{i + 1} x{r.x} y{r.y} {r.w}×{r.h}</span>
            <button className="btn ghost icon sm" onClick={() => setRegions(regions.filter((_, j) => j !== i))}>×</button>
          </div>
        ))}
      </div>}
      <Field label="Method">
        <select className="select" value={method} onChange={(e) => setMethod(e.target.value)}>
          <option value="auto">Auto (recommended: inpaint)</option>
          <option value="inpaint">Inpaint (per frame, best quality)</option>
          <option value="delogo">ffmpeg delogo (very fast, good for small logos)</option>
        </select>
      </Field>
      <button className="btn primary" disabled={busy || !regions.length} onClick={async () => {
        setRes(null)
        try { const d = await run(() => api.post('/api/ai/watermark', { mediaId: media.id, regions: regions.map(({ x, y, w, h }) => ({ x, y, w, h })), method })); setRes(d.result) } catch { /* JobBar */ }
      }}><Eraser size={14} />Remove watermark</button>
      <JobBar job={job} error={error} onCancel={cancel} />
      {res && <ResultCard m={res.media} note={`Method: ${res.engine}`} onUse={onUse} onReplace={onReplace} standalone={standalone} />}
    </>
  )
}

/* ------------------------------------------------------------------------------------------------ subtitles */
function SubtitleSection({ media, props }: { media: MediaRow | null; props: AiPanelProps }) {
  const [st, setSt] = useState<any>(null)
  const [language, setLanguage] = useState('')
  const [translate, setTranslate] = useState('')
  const sub = useJob<{ segments: { start: number; end: number; text: string }[]; srt: string; language: string }>()
  const prep = useJob()
  const [res, setRes] = useState<{ segments: { start: number; end: number; text: string }[]; srt: string; language: string } | null>(null)
  const [applied, setApplied] = useState(false)
  const loadSt = () => api.get('/api/ai/asr/status').then(setSt).catch(() => setSt(null))
  useEffect(() => { loadSt() }, [])
  useEffect(() => { setRes(null); setApplied(false) }, [media?.id])
  if (!media || (media.kind !== 'video' && media.kind !== 'audio')) return <NeedKind kinds="a video or audio" />
  const clip = clipsOfMedia(props, media.id)[0]
  const toTimeline = () => {
    if (!res || !props.applyOps) return
    const segs = res.segments.map((s) => {
      if (!clip) return s
      const r = mapRange(clip, s.start, s.end)
      return r ? { start: r.start, end: r.end, text: s.text } : null
    }).filter(Boolean) as { start: number; end: number; text: string }[]
    props.applyOps([{ op: 'add_subtitles', segments: segs } as Op])
    setApplied(true)
  }
  return (
    <>
      {st && !st.ready && (
        <div className="panel" style={{ padding: 8, marginBottom: 8, borderColor: 'var(--warn)' }}>
          <div style={{ fontSize: 12, color: 'var(--warn)' }}>{st.message}</div>
          {st.package && <>
            <button className="btn sm" style={{ marginTop: 6 }} disabled={prep.busy} onClick={async () => { try { await prep.run(() => api.post('/api/ai/subtitles/prepare')); loadSt() } catch { /* */ } }}>
              <Download size={13} />Download model {st.model} ({st.size})
            </button>
            <JobBar job={prep.job} error={prep.error} onCancel={prep.cancel} />
          </>}
        </div>
      )}
      <div className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
        <div className="field grow"><label>Language</label>
          <select className="select" value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="">Auto-detect</option><option value="zh">Chinese</option><option value="en">English</option><option value="ja">Japanese</option><option value="ko">Korean</option>
          </select></div>
        <div className="field grow"><label>Translate to</label>
          <select className="select" value={translate} onChange={(e) => setTranslate(e.target.value)}>
            <option value="">No translation</option><option value="zh">Chinese</option><option value="en">English</option><option value="ja">Japanese</option>
          </select></div>
      </div>
      <button className="btn primary" disabled={sub.busy || (st && !st.ready)} onClick={async () => {
        setRes(null); setApplied(false)
        try { const d = await sub.run(() => api.post('/api/ai/subtitles', { mediaId: media.id, language: language || undefined, translate_to: translate || undefined })); setRes(d.result) } catch { /* JobBar */ }
      }}><Subtitles size={14} />Generate subtitles</button>
      <JobBar job={sub.job} error={sub.error} onCancel={sub.cancel} />
      {res && <div style={{ marginTop: 8 }}>
        <div className="row" style={{ flexWrap: 'wrap', marginBottom: 4 }}>
          <span className="chip">{res.segments.length} line(s) · {res.language}</span>
          <button className="btn sm" onClick={() => downloadText(`${media.name}.srt`, res.srt)}><Download size={13} />SRT</button>
          {props.applyOps && <button className="btn sm primary" disabled={applied} onClick={toTimeline}>{applied ? 'Added' : 'Add subtitles to timeline'}</button>}
        </div>
        {props.applyOps && !clip && <Note tone="warn">This media is not on the timeline; subtitles will be placed at the media's own times.</Note>}
        <div style={{ maxHeight: 180, overflow: 'auto', border: '1px solid var(--line)', borderRadius: 4 }}>
          {res.segments.map((s, i) => (
            <div key={i} style={{ padding: '3px 6px', fontSize: 12, borderBottom: '1px solid var(--line)' }}>
              <span className="faint" style={{ fontFamily: 'var(--mono)', fontSize: 10 }}>{fmtDur(s.start)} </span>{s.text}
            </div>
          ))}
        </div>
      </div>}
    </>
  )
}

/* ------------------------------------------------------------------------------------------------ auto-cut */
interface CutRes { keep: { start: number; end: number; score: number; reason: string }[]; remove: { start: number; end: number }[]; duration: number; kept_duration: number; note: string }

function AutoCutSection({ media, props }: { media: MediaRow | null; props: AiPanelProps }) {
  const [style, setStyle] = useState<'tight' | 'highlights' | 'talking-head'>('tight')
  const [target, setTarget] = useState(30)
  const { job, error, run, cancel, busy } = useJob<CutRes>()
  const [res, setRes] = useState<CutRes | null>(null)
  const [applied, setApplied] = useState(false)
  useEffect(() => { setRes(null); setApplied(false) }, [media?.id])
  if (!media || (media.kind !== 'video' && media.kind !== 'audio')) return <NeedKind kinds="a video or audio" />
  const clip = clipsOfMedia(props, media.id)[0]
  const apply = () => {
    if (!res || !props.applyOps || !clip) return
    const ranges = res.remove.map((r) => mapRange(clip, r.start, r.end)).filter(Boolean) as { start: number; end: number }[]
    if (!ranges.length) return
    props.applyOps([{ op: 'remove_ranges', ranges } as Op])
    setApplied(true)
  }
  return (
    <>
      <Field label="Cut style">
        <Seg value={style} onChange={setStyle} options={[
          { v: 'tight', label: 'Tight (remove silence)', title: 'Remove silences and pauses' }, { v: 'highlights', label: 'Highlights', title: 'Pick the best moments by loudness + motion' },
          { v: 'talking-head', label: 'Talking head', title: 'Remove silence + filler words (needs Whisper)' }]} />
      </Field>
      {style === 'highlights' && <Field label={`Target length: ${target} s`}><input type="range" min={5} max={Math.max(10, Math.min(300, Math.round(media.duration) || 60))} value={target} onChange={(e) => setTarget(+e.target.value)} /></Field>}
      <button className="btn primary" disabled={busy} onClick={async () => {
        setRes(null); setApplied(false)
        try { const d = await run(() => api.post('/api/ai/auto-cut', { mediaId: media.id, style, target_duration: style === 'highlights' ? target : undefined })); setRes(d.result) } catch { /* JobBar */ }
      }}><Scissors size={14} />Analyse</button>
      <JobBar job={job} error={error} onCancel={cancel} />
      {res && <div style={{ marginTop: 8 }}>
        <div className="row" style={{ flexWrap: 'wrap', marginBottom: 4 }}>
          <span className="chip">Keep {res.keep.length} segment(s)</span>
          <span className="chip">{fmtDur(res.kept_duration)} / {fmtDur(res.duration)}</span>
          {props.applyOps && <button className="btn sm primary" disabled={applied || !clip} onClick={apply}>{applied ? 'Applied' : 'Apply to timeline'}</button>}
        </div>
        {res.note && <Note>{res.note}</Note>}
        {props.applyOps && !clip && <Note tone="warn">Put this media on the timeline first to apply the cut.</Note>}
        <div style={{ maxHeight: 170, overflow: 'auto', border: '1px solid var(--line)', borderRadius: 4, marginTop: 4 }}>
          {res.keep.map((k, i) => (
            <div key={i} className="row" style={{ padding: '3px 6px', fontSize: 12, borderBottom: '1px solid var(--line)' }}>
              <span style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{fmtDur(k.start)} – {fmtDur(k.end)}</span>
              <span className="grow faint" style={{ fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{k.reason}</span>
              <span className="faint" style={{ fontSize: 10 }}>{(k.score * 100).toFixed(0)}</span>
            </div>
          ))}
        </div>
      </div>}
    </>
  )
}

/* ------------------------------------------------------------------------------------------------ remove background */
function RemoveBgSection({ media, onUse, standalone }: { media: MediaRow | null; onUse: (m: MediaRow) => void; standalone?: boolean }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [out, setOut] = useState<{ b64: string; engine: string } | null>(null)
  const [saved, setSaved] = useState(false)
  useEffect(() => { setOut(null); setSaved(false); setErr('') }, [media?.id])
  if (!media || (media.kind !== 'image' && media.kind !== 'video')) return <NeedKind kinds="an image" />
  return (
    <>
      <button className="btn primary" disabled={busy} onClick={async () => {
        setBusy(true); setErr(''); setOut(null); setSaved(false)
        try { const r = await api.post('/api/ai/remove-bg', { mediaId: media.id }); setOut({ b64: r.imageBase64, engine: r.engine }) } catch (e: any) { setErr(e.message) }
        setBusy(false)
      }}><Wand2 size={14} />{busy ? 'Processing...' : 'Remove background'}</button>
      {media.kind === 'video' && <Note>For videos only the first frame is processed.</Note>}
      {err && <div className="err">{err}</div>}
      {out && <div style={{ marginTop: 8 }}>
        <div style={{ borderRadius: 4, overflow: 'hidden', lineHeight: 0, background: 'repeating-conic-gradient(#3a3f4b 0% 25%, #2a2e38 0% 50%) 50% / 16px 16px' }}>
          <img src={`data:image/png;base64,${out.b64}`} alt="" style={{ width: '100%', display: 'block' }} />
        </div>
        <div className="row" style={{ marginTop: 6 }}>
          <span className="chip">{out.engine === 'rembg' ? 'rembg' : 'GrabCut (basic)'}</span>
          <button className="btn sm primary" disabled={saved} onClick={async () => {
            const blob = await (await fetch(`data:image/png;base64,${out.b64}`)).blob()
            const row = await api.upload<MediaRow>('/api/media/upload', new File([blob], `${media.name}_cutout.png`, { type: 'image/png' }))
            setSaved(true); if (!standalone) onUse(row)
          }}>{saved ? 'Saved to library' : standalone ? 'Save to library' : 'Add to editor'}</button>
        </div>
        {out.engine !== 'rembg' && <Note>Install rembg for finer person / object cut-outs.</Note>}
      </div>}
    </>
  )
}

/* ------------------------------------------------------------------------------------------------ panel */
const SECTIONS: { id: AiToolId; title: string; icon: ReactNode }[] = [
  { id: 'upscale', title: 'Upscale (super-resolution)', icon: <Film size={14} /> },
  { id: 'watermark', title: 'Remove watermark', icon: <Eraser size={14} /> },
  { id: 'subtitles', title: 'Smart subtitles', icon: <Subtitles size={14} /> },
  { id: 'smartcut', title: 'Smart cut', icon: <Scissors size={14} /> },
  { id: 'removebg', title: 'Remove background', icon: <Wand2 size={14} /> },
]

export default function AiToolsPanel(props: AiPanelProps & { hideTitle?: boolean }) {
  const { media, setPicked, follow, id } = useTargetMedia(props)
  const [open, setOpen] = useState<Partial<Record<AiToolId, boolean>>>({ upscale: true })
  const rootRef = useRef<HTMLDivElement>(null)
  const use = (m: MediaRow) => props.onAddAsset(m, { toTimeline: false })
  // the clip a result would replace: same media, selected clip first
  const clip = clipsOfMedia(props, media?.id)[0]
  const onReplace = props.replaceClip && clip && media && clip.type === media.kind
    ? (m: MediaRow) => { if (m.kind === media.kind) props.replaceClip!(clip.id, m) } : undefined

  // the inspector / clip menu asks for a specific tool
  const nonce = useAiFocus((s) => s.nonce)
  useEffect(() => {
    if (!nonce || props.standalone) return
    const f = useAiFocus.getState()
    if (!f.tool) return
    follow(f.mediaId)
    setOpen({ [f.tool]: true })
    requestAnimationFrame(() => {
      const el = rootRef.current?.querySelector<HTMLElement>(`[data-sec="${f.tool}"]`)
      if (!el) return
      el.scrollIntoView({ block: 'start', behavior: 'smooth' })
      el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash')
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce])

  const body = (sid: AiToolId) => {
    switch (sid) {
      case 'upscale': return <UpscaleSection media={media} onUse={use} onReplace={onReplace} standalone={props.standalone} />
      case 'watermark': return <WatermarkSection media={media} onUse={use} onReplace={onReplace} standalone={props.standalone} />
      case 'subtitles': return <SubtitleSection media={media} props={props} />
      case 'smartcut': return <AutoCutSection media={media} props={props} />
      case 'removebg': return <RemoveBgSection media={media} onUse={use} standalone={props.standalone} />
    }
  }
  return (
    <div ref={rootRef} style={{ padding: 12, overflow: 'auto', height: '100%' }}>
      {!props.hideTitle && <div className="row" style={{ marginBottom: 8 }}><Wand2 size={16} /><b>AI Tools</b></div>}
      {!props.standalone
        ? <MediaPicker value={id} onChange={setPicked} label={clip ? 'Target (clip on the timeline)' : 'Target media'} />
        : <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>{media ? `Current media: ${media.name}` : 'Choose the target media above'}</div>}
      {!media && !props.standalone && <Empty compact>Select a clip on the timeline, or pick media above. Results can replace the clip in place.</Empty>}
      {SECTIONS.map((sec) => (
        <Section key={sec.id} id={sec.id} title={sec.title} icon={sec.icon} open={!!open[sec.id]} onToggle={(o) => setOpen((st) => ({ ...st, [sec.id]: o }))}>
          {body(sec.id)}
        </Section>
      ))}
    </div>
  )
}
