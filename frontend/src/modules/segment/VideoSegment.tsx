import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Clapperboard, Download, Film, FolderOpen, Pause, Play, Plus, Scissors, SplitSquareHorizontal, Trash2, Wand2, X } from 'lucide-react'
import { api, fmtTime, useJob } from '../../api/client'
import type { MediaRow, Segment } from '../../types/api'
import { emit, on } from '../../shared/bus'
import { JobBar } from '../../shared/ui'
import Downloader from '../downloader/Downloader'
import SegmentProjects from './SegmentProjects'

/** Video Segment: get a video (link download or library), mark several ranges on a timeline, split them into separate clips. */
interface Range { id: number; start: number; end: number; label: string; on: boolean }
type Drag = { mode: 'new' | 'start' | 'end' | 'move'; id: number; t0: number; s0: number; e0: number } | null

const METHODS = [
  { id: 'scene', label: 'Scene cuts' },
  { id: 'silence', label: 'Speech / silence' },
  { id: 'asr', label: 'Sentences (Whisper)' },
  { id: 'fixed', label: 'Every N seconds' },
] as const
const COLORS = ['#6c8cff', '#3ecf8e', '#f5a524', '#ef5da8', '#22c3e6', '#a78bfa', '#f87171', '#84cc16']
let nextId = 1

export default function VideoSegment({ onGoLibrary }: { onGoLibrary: () => void }) {
  const [videos, setVideos] = useState<MediaRow[]>([])
  const [media, setMedia] = useState<MediaRow | null>(null)
  const [showDl, setShowDl] = useState(false)
  const [ranges, setRanges] = useState<Range[]>([])
  const [selId, setSelId] = useState<number | null>(null)
  const [t, setT] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [inPt, setInPt] = useState<number | null>(null)
  const [method, setMethod] = useState<(typeof METHODS)[number]['id']>('scene')
  const [every, setEvery] = useState(10)
  const [accurate, setAccurate] = useState(true)
  const [results, setResults] = useState<MediaRow[]>([])
  const [playRange, setPlayRange] = useState<Range | null>(null)
  const vref = useRef<HTMLVideoElement>(null)
  const track = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag>(null)
  const detect = useJob<{ segments: Segment[] }>()
  const cut = useJob<{ items: MediaRow[] }>()
  const dur = media?.duration || 0
  const fps = media?.fps || 30

  const loadVideos = useCallback(() => api.get<MediaRow[]>('/api/media?kind=video&limit=200').then(setVideos).catch(() => {}), [])
  useEffect(() => { loadVideos() }, [loadVideos])
  // a finished link download becomes the current video
  useEffect(() => on('cutlab:media-changed', (d) => {
    loadVideos()
    if (d.media && d.media.kind === 'video' && d.media.origin === 'download') { pick(d.media); setShowDl(false) }
  }), [loadVideos]) // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (m: MediaRow | null) => { setMedia(m); setRanges([]); setSelId(null); setInPt(null); setResults([]); setT(0) }

  // ------------------------------------------------------------------ helpers
  const sorted = useMemo(() => [...ranges].sort((a, b) => a.start - b.start), [ranges])
  const chosen = sorted.filter((r) => r.on)
  const clamp = (x: number) => Math.max(0, Math.min(dur, x))
  const snap = (x: number) => Math.round(x * fps) / fps
  const seek = (x: number) => { const v = vref.current; if (v) v.currentTime = clamp(x); setT(clamp(x)) }
  const add = (start: number, end: number, label?: string) => {
    const s = snap(clamp(Math.min(start, end))), e = snap(clamp(Math.max(start, end)))
    if (e - s < 0.1) return null
    const r: Range = { id: nextId++, start: s, end: e, label: label || `Clip ${ranges.length + 1}`, on: true }
    setRanges((rs) => [...rs, r]); setSelId(r.id)
    return r
  }
  const update = (id: number, f: Partial<Range>) => setRanges((rs) => rs.map((r) => (r.id === id ? { ...r, ...f } : r)))
  const remove = (id: number) => { setRanges((rs) => rs.filter((r) => r.id !== id)); if (selId === id) setSelId(null) }
  const splitAt = (x: number) => {
    const r = sorted.find((q) => q.start < x - 0.05 && q.end > x + 0.05)
    if (!r) return
    update(r.id, { end: snap(x) })
    const n: Range = { id: nextId++, start: snap(x), end: r.end, label: `${r.label} b`, on: true }
    setRanges((rs) => [...rs, n]); setSelId(n.id)
  }

  // ------------------------------------------------------------------ playback
  useEffect(() => {
    const v = vref.current
    if (!v) return
    const tick = () => {
      setT(v.currentTime)
      if (playRange && v.currentTime >= playRange.end) { v.pause(); setPlayRange(null) }
    }
    const onPlay = () => setPlaying(true), onPause = () => setPlaying(false)
    v.addEventListener('timeupdate', tick); v.addEventListener('play', onPlay); v.addEventListener('pause', onPause)
    let raf = 0
    const loop = () => { if (!v.paused) setT(v.currentTime); raf = requestAnimationFrame(loop) }
    raf = requestAnimationFrame(loop)
    return () => { v.removeEventListener('timeupdate', tick); v.removeEventListener('play', onPlay); v.removeEventListener('pause', onPause); cancelAnimationFrame(raf) }
  }, [media, playRange])
  const toggle = () => { const v = vref.current; if (!v) return; if (v.paused) v.play().catch(() => {}); else v.pause() }
  const preview = (r: Range) => { setSelId(r.id); setPlayRange(r); seek(r.start); vref.current?.play().catch(() => {}) }

  // ------------------------------------------------------------------ keyboard
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (!media || tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || document.querySelector('[data-projects-home=segment]')) return
      const k = e.key.toLowerCase()
      if (k === ' ') { e.preventDefault(); toggle() }
      else if (k === 'i') setInPt(snap(t))
      else if (k === 'o') { if (inPt != null) { add(inPt, t); setInPt(null) } }
      else if (k === 's') splitAt(t)
      else if ((k === 'delete' || k === 'backspace') && selId != null) remove(selId)
      else if (k === 'arrowleft') seek(t - (e.shiftKey ? 1 : 1 / fps))
      else if (k === 'arrowright') seek(t + (e.shiftKey ? 1 : 1 / fps))
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  })

  // ------------------------------------------------------------------ timeline pointer
  const tAt = (clientX: number) => { const el = track.current; if (!el || !dur) return 0; const b = el.getBoundingClientRect(); return clamp(((clientX - b.left) / b.width) * dur) }
  const pxPerSec = () => (track.current ? track.current.getBoundingClientRect().width / Math.max(dur, 0.001) : 1)
  const onDown = (e: React.PointerEvent) => {
    if (!dur) return
    const x = tAt(e.clientX)
    const tol = 6 / pxPerSec()
    const hit = sorted.find((r) => x >= r.start - tol && x <= r.end + tol)
    ;(e.target as HTMLElement).setPointerCapture?.(e.pointerId)
    if (hit) {
      setSelId(hit.id)
      const mode = Math.abs(x - hit.start) <= tol ? 'start' : Math.abs(x - hit.end) <= tol ? 'end' : 'move'
      drag.current = { mode, id: hit.id, t0: x, s0: hit.start, e0: hit.end }
      if (mode === 'move') seek(x)
    } else {
      seek(x)
      const r = add(x, x + 0.1)
      if (r) drag.current = { mode: 'new', id: r.id, t0: x, s0: x, e0: x }
    }
  }
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    const x = tAt(e.clientX)
    if (d.mode === 'new') update(d.id, { start: snap(Math.min(d.t0, x)), end: snap(Math.max(d.t0, x)) })
    else if (d.mode === 'start') update(d.id, { start: snap(Math.min(x, d.e0 - 0.1)) })
    else if (d.mode === 'end') update(d.id, { end: snap(Math.max(x, d.s0 + 0.1)) })
    else { const len = d.e0 - d.s0; const s = Math.max(0, Math.min(dur - len, d.s0 + x - d.t0)); update(d.id, { start: snap(s), end: snap(s + len) }) }
    seek(d.mode === 'end' ? Math.max(x, d.s0) : x)
  }
  const onUp = () => {
    const d = drag.current
    drag.current = null
    if (d?.mode === 'new') setRanges((rs) => rs.filter((r) => r.id !== d.id || r.end - r.start >= 0.2))
  }

  // ------------------------------------------------------------------ auto detect + export
  const runDetect = async (replace: boolean) => {
    if (!media) return
    const body: Record<string, unknown> = { mediaId: media.id, method }
    if (method === 'fixed') body.every = every
    try {
      const j = await detect.run(() => api.post('/api/segment', body))
      const segs = j.result?.segments ?? []
      const made = segs.map((s, i) => ({ id: nextId++, start: snap(s.start), end: snap(s.end), label: (s.text || s.label || `Clip ${i + 1}`).slice(0, 40), on: true }))
      setRanges((rs) => (replace ? made : [...rs, ...made]))
    } catch { /* JobBar */ }
  }
  const doSplit = async () => {
    if (!media || !chosen.length) return
    try {
      const j = await cut.run(() => api.post('/api/segment/cut', { mediaId: media.id, reencode: accurate, segments: chosen.map((r) => ({ start: r.start, end: r.end, label: r.label })) }))
      const items = j.result?.items ?? []
      setResults(items)
      items.forEach((m) => emit('cutlab:media-changed', { media: m }))
    } catch { /* JobBar */ }
  }

  // ------------------------------------------------------------------ render
  const pct = (x: number) => `${(x / Math.max(dur, 0.001)) * 100}%`
  const total = chosen.reduce((a, r) => a + r.end - r.start, 0)

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: 12, padding: 12, height: '100%', minHeight: 0 }}>
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, gap: 10 }}>
        {/* source bar */}
        <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <SegmentProjects state={{ media, ranges, method, every, accurate, results }} set={{ setMedia, setRanges, setMethod, setEvery, setAccurate, setResults, reset: () => { setSelId(null); setInPt(null); setT(0) } }} />
          <Film size={15} />
          <select className="select" style={{ minWidth: 260, flex: 1, maxWidth: 520 }} value={media?.id || ''} onChange={(e) => pick(videos.find((v) => v.id === e.target.value) || null)}>
            <option value="">Choose a video from the library…</option>
            {videos.map((v) => <option key={v.id} value={v.id}>{v.name} · {fmtTime(v.duration)}</option>)}
          </select>
          <button className="btn sm" onClick={() => setShowDl(true)}><Download size={13} />Download from link</button>
          <button className="btn ghost sm" onClick={onGoLibrary}><FolderOpen size={13} />Library</button>
        </div>

        {!media ? (
          <div className="panel" style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 10 }}>
            <SplitSquareHorizontal size={34} className="faint" />
            <div style={{ fontSize: 15 }}>Split a video into several clips</div>
            <div className="faint" style={{ fontSize: 12, maxWidth: 460, textAlign: 'center' }}>
              Paste a link (YouTube, Bilibili, Douyin, Xiaohongshu, X…) or pick a library video, mark the parts you want on the timeline
              (drag, or press I and O), or detect them automatically, then split them into separate clips in one go.
            </div>
            <div className="row"><button className="btn primary" onClick={() => setShowDl(true)}><Download size={14} />Download from link</button></div>
            {videos.length > 0 && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 8, width: '100%', maxWidth: 820, marginTop: 8 }}>
                {videos.slice(0, 8).map((v) => (
                  <button key={v.id} className="media-card" onClick={() => pick(v)} style={{ textAlign: 'left' }}>
                    <img src={v.thumb} alt="" /><span className="dur">{fmtTime(v.duration)}</span><div className="cap">{v.name}</div>
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <>
            <div style={{ flex: 1, minHeight: 160, background: '#000', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
              <video ref={vref} key={media.id} src={media.url} style={{ maxWidth: '100%', maxHeight: '100%' }} onClick={toggle} />
            </div>
            {/* transport */}
            <div className="row" style={{ gap: 8, fontSize: 12 }}>
              <button className="btn sm" onClick={toggle} title="Play / pause (Space)">{playing ? <Pause size={13} /> : <Play size={13} />}</button>
              <span style={{ fontFamily: 'var(--mono)' }}>{fmtTime(t)} / {fmtTime(dur)}</span>
              <button className="btn sm" onClick={() => setInPt(snap(t))} title="Set in point (I)">In</button>
              <button className="btn sm" disabled={inPt == null} onClick={() => { if (inPt != null) { add(inPt, t); setInPt(null) } }} title="Set out point and add the range (O)">Out</button>
              <button className="btn sm" onClick={() => splitAt(t)} title="Split the range under the playhead (S)"><Scissors size={13} />Split here</button>
              <button className="btn sm" onClick={() => add(t, Math.min(dur, t + 5))} title="Add a 5 s range at the playhead"><Plus size={13} />Range</button>
              {inPt != null && <span className="chip">In {fmtTime(inPt)}</span>}
              <span className="grow" />
              <span className="faint">Drag on the strip to mark · drag edges to trim · I / O · S split · Del remove · ←/→ frame (Shift = 1 s)</span>
            </div>
            {/* timeline */}
            <div ref={track} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
              style={{ position: 'relative', height: 74, borderRadius: 6, border: '1px solid var(--line)', cursor: 'crosshair', userSelect: 'none', touchAction: 'none',
                background: `#111 url(/api/segment/filmstrip/${media.id}?n=30&h=54) 0 0 / 100% 100% no-repeat` }}>
              <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,.35)' }} />
              {sorted.map((r, i) => (
                <div key={r.id} title={`${r.label}: ${fmtTime(r.start)} → ${fmtTime(r.end)}`}
                  style={{ position: 'absolute', top: 6, bottom: 6, left: pct(r.start), width: pct(r.end - r.start), borderRadius: 4,
                    background: `${COLORS[i % COLORS.length]}${r.on ? '55' : '22'}`, border: `2px solid ${COLORS[i % COLORS.length]}`,
                    boxShadow: r.id === selId ? '0 0 0 2px #fff' : 'none' }}>
                  <span style={{ position: 'absolute', left: 4, top: 2, fontSize: 10, color: '#fff', whiteSpace: 'nowrap', textShadow: '0 1px 2px #000', pointerEvents: 'none' }}>{i + 1}</span>
                </div>
              ))}
              {inPt != null && <div style={{ position: 'absolute', top: 0, bottom: 0, left: pct(inPt), width: 2, background: '#3ecf8e' }} />}
              <div style={{ position: 'absolute', top: 0, bottom: 0, left: pct(t), width: 2, background: '#fff', pointerEvents: 'none' }} />
            </div>
          </>
        )}
      </div>

      {/* right: ranges */}
      <div className="panel" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div className="panel-title"><span className="grow">Ranges {ranges.length ? `(${chosen.length}/${ranges.length})` : ''}</span>
          {ranges.length > 0 && <button className="btn ghost sm" onClick={() => { setRanges([]); setSelId(null) }}>Clear</button>}</div>
        <div style={{ padding: 10, borderBottom: '1px solid var(--line)' }}>
          <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
            <select className="select" value={method} onChange={(e) => setMethod(e.target.value as typeof method)} style={{ flex: 1 }}>
              {METHODS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
            {method === 'fixed' && <input className="input" type="number" min={0.5} step={0.5} value={every} onChange={(e) => setEvery(+e.target.value)} style={{ width: 64 }} title="Seconds per clip" />}
            <button className="btn sm" disabled={!media || detect.busy} onClick={() => runDetect(true)}><Wand2 size={13} />Detect</button>
          </div>
          <JobBar job={detect.job} error={detect.error} onCancel={detect.cancel} />
        </div>
        <div style={{ flex: 1, overflow: 'auto' }}>
          {!sorted.length && <div className="faint" style={{ padding: 16, fontSize: 12 }}>No ranges yet. Drag on the timeline, press I then O, or use Detect.</div>}
          {sorted.map((r, i) => (
            <div key={r.id} onClick={() => { setSelId(r.id); seek(r.start) }}
              style={{ padding: '6px 10px', borderBottom: '1px solid var(--line)', background: r.id === selId ? 'var(--bg-2, rgba(255,255,255,.04))' : undefined, cursor: 'pointer' }}>
              <div className="row" style={{ gap: 6 }}>
                <input type="checkbox" checked={r.on} onClick={(e) => e.stopPropagation()} onChange={(e) => update(r.id, { on: e.target.checked })} />
                <span style={{ width: 10, height: 10, borderRadius: 2, background: COLORS[i % COLORS.length], flex: 'none' }} />
                <input className="input" value={r.label} onClick={(e) => e.stopPropagation()} onChange={(e) => update(r.id, { label: e.target.value })} style={{ flex: 1, minWidth: 0, padding: '2px 6px' }} />
                <button className="btn ghost sm" title="Play this range" onClick={(e) => { e.stopPropagation(); preview(r) }}><Play size={12} /></button>
                <button className="btn ghost sm" title="Remove" onClick={(e) => { e.stopPropagation(); remove(r.id) }}><Trash2 size={12} /></button>
              </div>
              <div className="row faint" style={{ gap: 6, fontSize: 11, marginTop: 3, fontFamily: 'var(--mono)' }}>
                <input className="input" type="number" step={1 / fps} value={r.start.toFixed(2)} onClick={(e) => e.stopPropagation()}
                  onChange={(e) => update(r.id, { start: Math.min(clamp(+e.target.value), r.end - 0.1) })} style={{ width: 72, padding: '1px 4px' }} />
                →
                <input className="input" type="number" step={1 / fps} value={r.end.toFixed(2)} onClick={(e) => e.stopPropagation()}
                  onChange={(e) => update(r.id, { end: Math.max(clamp(+e.target.value), r.start + 0.1) })} style={{ width: 72, padding: '1px 4px' }} />
                <span className="grow" />{(r.end - r.start).toFixed(1)} s
              </div>
            </div>
          ))}
        </div>
        <div style={{ padding: 10, borderTop: '1px solid var(--line)' }}>
          <label className="row" style={{ gap: 6, fontSize: 12, cursor: 'pointer' }} title="Frame-accurate cuts (re-encodes). Off = instant lossless copy, cuts snap to the nearest keyframe.">
            <input type="checkbox" checked={accurate} onChange={(e) => setAccurate(e.target.checked)} />Frame-accurate (re-encode)
          </label>
          <button className="btn primary" style={{ width: '100%', marginTop: 8 }} disabled={!media || !chosen.length || cut.busy} onClick={doSplit}>
            <Scissors size={14} />Split into {chosen.length} clip{chosen.length === 1 ? '' : 's'}{chosen.length ? ` · ${fmtTime(total)}` : ''}
          </button>
          <button className="btn sm" style={{ width: '100%', marginTop: 6 }} disabled={!media || !chosen.length}
            onClick={() => media && emit('cutlab:add-segments', { media, segments: chosen.map((r) => ({ start: r.start, end: r.end, label: r.label })) })}>
            <Clapperboard size={13} />Put ranges on the video timeline
          </button>
          <JobBar job={cut.job} error={cut.error} onCancel={cut.cancel} />
          {results.length > 0 && (
            <div style={{ marginTop: 8, fontSize: 12 }}>
              <div className="row"><b className="grow">{results.length} clips saved to the Library</b>
                <button className="btn ghost sm" onClick={onGoLibrary}>Open</button></div>
              <div style={{ maxHeight: 140, overflow: 'auto', marginTop: 4 }}>
                {results.map((m) => (
                  <div key={m.id} className="row" style={{ gap: 6, padding: '2px 0' }}>
                    <span className="grow faint" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{m.name}</span>
                    <span className="faint">{fmtTime(m.duration)}</span>
                    <button className="btn ghost sm" title="Add to the video timeline" onClick={() => emit('cutlab:add-to-timeline', { media: m })}><Clapperboard size={12} /></button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {showDl && (
        <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && setShowDl(false)}>
          <div className="modal" style={{ width: 'min(1100px, 94vw)', height: '84vh' }}>
            <header><Download size={16} />Download from link<span className="grow" /><button className="btn ghost icon" onClick={() => setShowDl(false)}><X size={16} /></button></header>
            <div className="body" style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 0 }}>
              <Downloader onGoLibrary={() => { setShowDl(false); onGoLibrary() }} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
