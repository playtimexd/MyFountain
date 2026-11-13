import { useCallback, useEffect, useRef, useState } from 'react'
import { Box, Check, Dices, Gift, Clapperboard, Download, Film, Gamepad2, Link2, Merge, Repeat, RotateCcw, Send, Sparkles, Type, Upload, Wand2, X } from 'lucide-react'
import { api, fmtTime, useJob } from '../../api/client'
import { emit, on } from '../../shared/bus'
import { JobBar } from '../../shared/ui'
import type { MediaRow } from '../../types/api'
import type { AnimClip, AnimRig } from './animApi'
import GlbViewer from './providers/GlbViewer'
import EngineExport from '../models/EngineExport'
import StarterPacks from '../models/StarterPacks'
import VideoGenPanel from './providers/VideoGenPanel'
import Downloader from '../downloader/Downloader'

/** The simple path: 1 reference video + 3D character -> 2 Generate -> 3 Preview & edit in words -> 4 Export. */
interface QuickResult {
  source_clip: { id: string; name: string; duration: number; fps: number; tags?: string[] }
  clip: { id: string; name: string; duration: number; frames: number; fps: number; score: number | null }
  metrics: Record<string, number> | null
  notes: string[] | null
  media: MediaRow | null
  glb_url: string
  options: Record<string, any>
  rig: { id: string; name: string }
  history: { instruction: string; options: Record<string, any>; explanation?: string; planner?: string }[]
  edit?: { changes: Record<string, any>; explanation: string; unsupported: string; planner: string }
  text?: { prompt: string; seed: number | null; duration: number }
  autotune?: { target: number; reached: boolean; best_score: number | null; tries: { try: number; change: string; score: number; penalties: Record<string, number> }[]; remaining: Record<string, number> }
  motionbricks?: { transition_frames: number; distance_m: number; loop: boolean; tokens: number }
}

/** Motions made from a description (Kimodo, SOMA skeleton) or by MotionBricks itself can be joined / looped. */
const isSoma = (tags?: string[]) => !!tags && (tags.includes('kimodo') || tags.includes('motionbricks') || tags.includes('text-to-motion'))
const shortName = (n: string) => n.replace(/^(Kimodo|MotionBricks):\s*/, '').replace(/A person /g, '')

const CHIPS = ['Slower', 'Faster', 'Smoother, less jitter', 'Less foot sliding', 'Make it loop seamlessly', 'More exaggerated', 'Only the first 2 seconds']
const KEY = 'cutlab.anim.wizard'
const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || '{}') } catch { return {} } }
const save = (v: object) => { try { localStorage.setItem(KEY, JSON.stringify(v)) } catch { /* ignore */ } }

function Step({ n, title, done, active, children, right }: { n: number; title: string; done?: boolean; active?: boolean; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="panel" style={{ opacity: active === false ? 0.55 : 1, minWidth: 0 }}>
      <div className="panel-title" style={{ gap: 8 }}>
        <span style={{ width: 22, height: 22, borderRadius: 11, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 600,
          background: done ? 'var(--ok, #3ecf8e)' : 'var(--accent, #6c8cff)', color: '#fff', flex: 'none' }}>{done ? <Check size={13} /> : n}</span>
        <span className="grow">{title}</span>{right}
      </div>
      <div style={{ padding: 12 }}>{children}</div>
    </div>
  )
}

export default function AnimWizard({ rigs, reloadRigs, onAdvanced }: { rigs: AnimRig[]; reloadRigs: () => void; onAdvanced: () => void }) {
  const init = load()
  const [videos, setVideos] = useState<MediaRow[]>([])
  const [video, setVideo] = useState<MediaRow | null>(null)
  const [rigId, setRigId] = useState<string>(init.rigId || '')
  const [range, setRange] = useState<{ start?: number; end?: number }>(init.range || {})
  const [mode, setMode] = useState<'video' | 'text' | 'genvideo'>(init.mode || 'video')
  const [prompt, setPrompt] = useState<string>(init.prompt || '')
  const [dur, setDur] = useState<number>(init.dur || 4)
  const [kt, setKt] = useState<{ available: boolean; note: string; examples: string[]; models: string[]; license: string } | null>(null)
  const [res, setRes] = useState<QuickResult | null>(init.res || null)
  const [instr, setInstr] = useState('')
  const [showDl, setShowDl] = useState(false)
  const [eng, setEng] = useState(false)
  const [packs, setPacks] = useState(false)
  const [msg, setMsg] = useState('')
  const [view, setView] = useState<'3d' | 'video'>('3d')
  const [mb, setMb] = useState<{ available: boolean; note: string | null; licence: string } | null>(null)
  const [somaClips, setSomaClips] = useState<AnimClip[]>([])
  const [joinWith, setJoinWith] = useState('')
  const vref = useRef<HTMLVideoElement>(null)
  const vidUp = useRef<HTMLInputElement>(null)
  const modelUp = useRef<HTMLInputElement>(null)
  const gen = useJob<QuickResult>()
  const ed = useJob<QuickResult>()
  const up = useJob<any>()
  const ex = useJob<any>()
  const busy = gen.busy || ed.busy

  const loadVideos = useCallback(async () => {
    const v = await api.get<MediaRow[]>('/api/media?kind=video&limit=200').catch(() => [] as MediaRow[])
    setVideos(v)
    if (init.videoId) setVideo((cur) => cur || v.find((x) => x.id === init.videoId) || null)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { loadVideos() }, [loadVideos])
  useEffect(() => on('cutlab:media-changed', (d) => { loadVideos(); if (d.media?.kind === 'video' && d.media.origin === 'download') { setVideo(d.media); setRange({}); setShowDl(false) } }), [loadVideos])
  useEffect(() => { if (!rigId && rigs.length) setRigId(rigs[0].id) }, [rigs, rigId])
  useEffect(() => { save({ rigId, range, videoId: video?.id, res, mode, prompt, dur }) }, [rigId, range, video, res, mode, prompt, dur])
  useEffect(() => { api.get('/api/anim/quick/text/status').then(setKt).catch(() => setKt({ available: false, note: 'the engine has no text-to-motion endpoint yet (restart it)', examples: [], models: [], license: '' })) }, [])

  useEffect(() => { api.get('/api/anim/quick/transition/status').then(setMb).catch(() => setMb({ available: false, note: 'restart the engine to enable transitions', licence: '' })) }, [])
  const soma = !!res && isSoma(res.source_clip.tags)
  useEffect(() => {
    if (!soma) return
    api.get<AnimClip[]>('/api/anim/clips').then((cs) => {
      const seen = new Set<string>()
      setSomaClips(cs.filter((c) => c.kind === 'source' && isSoma(c.tags) && c.id !== res?.source_clip.id)
        .sort((a, b) => b.created - a.created)
        .filter((c) => { const k = shortName(c.name); if (seen.has(k)) return false; seen.add(k); return true }))
    }).catch(() => setSomaClips([]))
  }, [soma, res?.source_clip.id])

  const rig = rigs.find((r) => r.id === rigId)
  const textOk = !!kt?.available && prompt.trim().length > 3 && rig?.kind !== 'quadruped'
  const step1 = !!rig && (mode === 'video' ? !!video : mode === 'text' ? textOk : false)
  const len = (range.end ?? video?.duration ?? 0) - (range.start ?? 0)

  // ---------------------------------------------------------------- inputs
  const uploadVideo = async (f: File) => {
    try { const m = await api.upload<MediaRow>('/api/media/upload', f); await loadVideos(); setVideo(m); setRange({}) } catch (e: any) { setMsg(`Upload failed: ${e.message}`) }
  }
  const uploadModel = async (f: File) => {
    setMsg('')
    try {
      const m = await api.upload<{ file: string }>('/api/models/upload', f)
      const j = await up.run(() => api.post(`/api/models/file/${encodeURIComponent(m.file)}/to-animation`))
      const id = j.result?.rig?.id as string | undefined
      reloadRigs()
      if (id) setRigId(id)
    } catch { /* JobBar */ }
  }
  const mark = (k: 'start' | 'end') => { const t = vref.current?.currentTime ?? 0; setRange((r) => ({ ...r, [k]: Math.round(t * 100) / 100 })) }

  // ---------------------------------------------------------------- generate / edit
  const generate = async (seed?: number) => {
    if (!rig || (mode === 'video' && !video)) return
    setMsg('')
    try {
      const j = await gen.run(() => mode === 'text'
        ? api.post('/api/anim/quick/generate-text', { prompt, rigId: rig.id, duration: dur, seed })
        : api.post('/api/anim/quick/generate', { mediaId: video!.id, rigId: rig.id, start: range.start, end: range.end }))
      if (j.result) { setRes(j.result); setView('3d') }
    } catch { /* JobBar */ }
  }
  const doEdit = async (text: string) => {
    if (!res || !text.trim()) return
    try {
      const j = await ed.run(() => api.post('/api/anim/quick/edit', { sourceClipId: res.source_clip.id, rigId: res.rig.id, instruction: text, options: res.options, metrics: res.metrics, history: res.history }))
      if (j.result) { setRes(j.result); setInstr('') }
    } catch { /* JobBar */ }
  }
  const autotune = async () => {
    if (!res) return
    try {
      const j = await ed.run(() => api.post('/api/anim/quick/autotune', { sourceClipId: res.source_clip.id, rigId: res.rig.id, options: res.options, target: 90, max_tries: 10, history: res.history }))
      if (j.result) setRes(j.result)
    } catch { /* JobBar */ }
  }
  const undoTo = async (i: number) => {
    if (!res) return
    const h = res.history.slice(0, i + 1)
    try {
      const j = await ed.run(() => api.post('/api/anim/quick/rerun', { sourceClipId: res.source_clip.id, rigId: res.rig.id, options: h[h.length - 1].options, history: h }))
      if (j.result) setRes(j.result)
    } catch { /* JobBar */ }
  }
  const transition = async (loop: boolean) => {
    if (!res || (!loop && !joinWith)) return
    try {
      const j = await ed.run(() => api.post('/api/anim/quick/transition', { clipA: res.source_clip.id, clipB: loop ? null : joinWith, loop, rigId: res.rig.id }))
      if (j.result) { setRes(j.result); setView('3d') }
    } catch { /* JobBar */ }
  }
  const exportFile = async (format: 'glb' | 'fbx') => {
    if (!res) return
    try {
      const j = await ex.run(() => api.post('/api/anim/export', { clipIds: [res.clip.id], rigId: res.rig.id, format, options: { preset: 'game', all_actions: false } }))
      const url = j.result?.url
      if (url) { const a = document.createElement('a'); a.href = `${url}?download=true`; a.download = ''; a.click() }
    } catch { /* JobBar */ }
  }
  const sendMyEngine = async () => {
    if (!res) return
    try { await ex.run(() => api.post('/api/anim/send/my-engine', { clipIds: [res.clip.id], rigId: res.rig.id })) } catch { /* JobBar */ }
  }

  const m = res?.metrics || {}
  const score = res?.clip.score

  return (
    <div style={{ height: '100%', overflow: 'auto', padding: 14 }}>
      <div className="row" style={{ marginBottom: 10, gap: 8 }}>
        <Sparkles size={16} /><b style={{ fontSize: 15 }}>Make an animation</b>
        <span className="faint" style={{ fontSize: 12 }}>reference video + 3D character → game-ready animation</span>
        <span className="grow" />
        <button className="btn ghost sm" onClick={onAdvanced} title="Bone maps, retarget settings, clip library, motion files">Advanced tools</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(340px, 1fr) minmax(420px, 1.35fr)', gap: 14, alignItems: 'start' }}>
        {/* left: 1 + 2 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
          <Step n={1} title="Motion and 3D character" done={step1}>
            <div className="seg-tabs" style={{ marginBottom: 8 }}>
              <button className={mode === 'video' ? 'on' : ''} onClick={() => setMode('video')}><Film size={13} /> From a video</button>
              <button className={mode === 'text' ? 'on' : ''} onClick={() => setMode('text')}><Type size={13} /> Describe the motion</button>
              <button className={mode === 'genvideo' ? 'on' : ''} onClick={() => setMode('genvideo')}><Sparkles size={13} /> Generate a video</button>
            </div>
            {mode === 'genvideo' && (
              <div>
                <div className="faint" style={{ fontSize: 11, marginBottom: 6 }}>Make a reference clip with a video model (local ComfyUI or Seedance), then it is motion-captured like any video. Works for animals too (text-to-motion is humans only).</div>
                <VideoGenPanel onAddMedia={(m) => { loadVideos(); setVideo(m); setRange({}); setMode('video') }} />
              </div>
            )}
            {mode === 'text' && (
              <div>
                <textarea className="input" rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} maxLength={500}
                  placeholder="e.g. A person swings a heavy sword overhead and strikes down" style={{ width: '100%', resize: 'vertical' }} />
                <div className="row" style={{ gap: 4, flexWrap: 'wrap', margin: '4px 0 6px' }}>
                  {(kt?.examples || []).slice(0, 6).map((x) => <button key={x} className="btn ghost sm" style={{ fontSize: 11 }} onClick={() => setPrompt(x)}>{x.replace(/^A person /, '').replace(/\.$/, '')}</button>)}
                </div>
                <label style={{ display: 'grid', gridTemplateColumns: '70px 1fr 40px', gap: 8, alignItems: 'center', fontSize: 12 }}>
                  <span className="faint">Length</span>
                  <input type="range" min={1} max={10} step={0.5} value={dur} onChange={(e) => setDur(Number(e.target.value))} />
                  <span>{dur} s</span>
                </label>
                <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>
                  {kt == null ? 'Checking Kimodo…' : kt.available
                    ? <>NVIDIA Kimodo, running locally ({kt.models.join(', ')} · {kt.license}). Humanoid characters only; describe one person and one action in English.</>
                    : <span style={{ color: 'var(--warn)' }}>Text-to-motion unavailable: {kt.note}</span>}
                </div>
                {rig?.kind === 'quadruped' && <div style={{ fontSize: 11, color: 'var(--warn)', marginTop: 4 }}>{rig.name} has four legs: text-to-motion makes human motion. Use "From a video" or pick a humanoid.</div>}
              </div>
            )}
            {mode === 'video' && <>
            <div className="faint" style={{ fontSize: 12, marginBottom: 4 }}>Reference video (the motion to copy)</div>
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              <select className="select" style={{ flex: 1, minWidth: 180 }} value={video?.id || ''} onChange={(e) => { setVideo(videos.find((v) => v.id === e.target.value) || null); setRange({}) }}>
                <option value="">Choose a video…</option>
                {videos.map((v) => <option key={v.id} value={v.id}>{v.name} · {fmtTime(v.duration)}</option>)}
              </select>
              <button className="btn sm" onClick={() => vidUp.current?.click()}><Upload size={13} />Upload</button>
              <button className="btn sm" onClick={() => setShowDl(true)}><Link2 size={13} />Paste link</button>
              <input ref={vidUp} type="file" accept="video/*" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadVideo(f); e.target.value = '' }} />
            </div>
            {video && (
              <>
                <video ref={vref} key={video.id} src={video.url} controls muted style={{ width: '100%', maxHeight: 230, background: '#000', borderRadius: 6, marginTop: 8, display: 'block' }} />
                <div className="row" style={{ gap: 6, fontSize: 12, marginTop: 6, flexWrap: 'wrap' }}>
                  <span className="faint">Use part of it:</span>
                  <button className="btn sm" onClick={() => mark('start')}>Start here</button>
                  <button className="btn sm" onClick={() => mark('end')}>End here</button>
                  <span style={{ fontFamily: 'var(--mono)' }}>{fmtTime(range.start ?? 0)} → {fmtTime(range.end ?? video.duration)}</span>
                  {(range.start != null || range.end != null) && <button className="btn ghost sm" onClick={() => setRange({})}>Whole video</button>}
                </div>
                {len > 12 && <div style={{ fontSize: 11, color: 'var(--warn)', marginTop: 4 }}>Tip: 2–8 s of one clean action (e.g. one walk cycle, side view) gives the best result.</div>}
              </>
            )}
            </>}

            <div className="faint" style={{ fontSize: 12, margin: '12px 0 4px' }}>3D character (rigged model)</div>
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              <select className="select" style={{ flex: 1, minWidth: 180 }} value={rigId} onChange={(e) => setRigId(e.target.value)}>
                {!rigs.length && <option value="">No characters yet</option>}
                {rigs.map((r) => <option key={r.id} value={r.id}>{r.name} · {r.kind} · {r.bone_count} bones</option>)}
              </select>
              <button className="btn sm" onClick={() => modelUp.current?.click()} title="GLB / FBX / BLEND with a skeleton"><Upload size={13} />Upload model</button>
              <button className="btn sm ghost" onClick={() => window.dispatchEvent(new CustomEvent('cutlab:goto', { detail: { tab: 'models' } }))}><Box size={13} />3D Models</button>
              <button className="btn sm ghost" onClick={() => setPacks(true)} title="Free rigged characters (robot, fox, humans)"><Gift size={13} />Free characters</button>
              {packs && <StarterPacks onClose={() => setPacks(false)} onInstalled={reloadRigs} />}
              <input ref={modelUp} type="file" accept=".glb,.gltf,.fbx,.blend" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadModel(f); e.target.value = '' }} />
            </div>
            <JobBar job={up.job} error={up.error} onCancel={up.cancel} />
            {rig && (
              <div className="row" style={{ gap: 10, marginTop: 8 }}>
                {rig.thumb_url && <img src={rig.thumb_url} alt="" style={{ width: 72, height: 72, objectFit: 'cover', borderRadius: 6, background: '#111' }} />}
                <div className="faint" style={{ fontSize: 12 }}>{rig.kind === 'quadruped' ? 'Four-legged character: animal motion capture' : 'Humanoid character: human motion capture'}{rig.height ? ` · ${rig.height.toFixed(2)} m` : ''}</div>
              </div>
            )}
            {msg && <div style={{ fontSize: 12, color: 'var(--warn)', marginTop: 6 }}>{msg}</div>}
          </Step>

          <Step n={2} title="Generate the animation" done={!!res} active={step1}>
            <button className="btn primary" style={{ width: '100%', padding: '10px 0', fontSize: 14 }} disabled={!step1 || busy} onClick={() => generate()}>
              <Wand2 size={16} />{res ? (mode === 'text' ? 'Generate again from the description' : 'Generate again from the video') : 'Generate animation'}
            </button>
            {mode === 'text' && res?.text && (
              <button className="btn sm" style={{ width: '100%', marginTop: 6 }} disabled={busy} onClick={() => generate(Math.floor(Math.random() * 1e6))}>
                <Dices size={13} />Another variation of the same description
              </button>
            )}
            <JobBar job={gen.job} error={gen.error} onCancel={gen.cancel} />
            <div className="faint" style={{ fontSize: 11, marginTop: 6 }}>
              {mode === 'text'
                ? <>Kimodo generates the motion from your description, then it is transferred onto your character (feet locked to the ground, in place) and built into a game GLB. About 1–2 minutes.</>
                : <>Runs motion capture on the video, fits a 3D skeleton, transfers the motion onto your character (feet locked to the ground, in place, looping) and builds a game GLB. Usually 1–2 minutes on the GPU.</>}
            </div>
          </Step>
        </div>

        {/* right: 3 + 4 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
          <Step n={3} title="Preview and edit" done={!!res && !ed.busy} active={!!res}
            right={res && <div className="seg-tabs"><button className={view === '3d' ? 'on' : ''} onClick={() => setView('3d')}>3D</button><button className={view === 'video' ? 'on' : ''} onClick={() => setView('video')}>3 views</button></div>}>
            {!res ? <div className="faint" style={{ fontSize: 12, padding: '30px 0', textAlign: 'center' }}>The animated character appears here after step 2.</div> : (
              <>
                {view === '3d'
                  ? <GlbViewer key={res.glb_url} url={res.glb_url} height={340} />
                  : res.media ? <video key={res.media.id} src={res.media.url} autoPlay loop muted controls style={{ width: '100%', maxHeight: 340, background: '#1d2027', borderRadius: 6 }} title="front | side | top" />
                    : <div className="faint">No preview video.</div>}
                <div className="row" style={{ gap: 12, fontSize: 12, margin: '8px 0', flexWrap: 'wrap' }}>
                  {score != null && <span>Quality <b style={{ color: score >= 90 ? 'var(--ok)' : score >= 50 ? 'var(--warn)' : 'var(--danger)' }}>{Math.round(score)}</b>/100</span>}
                  {score != null && score < 90 && <button className="btn sm primary" disabled={busy} onClick={autotune} title="Tries settings aimed at what costs the most points (foot sliding, ground contact, jitter, loop seam) and keeps the best, up to 10 tries"><Wand2 size={12} />Auto-tune to 90+</button>}
                  <span className="faint">{res.clip.duration.toFixed(2)} s · {res.clip.frames} frames</span>
                  {typeof m.foot_slide_pct_height_per_s === 'number' && <span className="faint">foot slide {m.foot_slide_pct_height_per_s.toFixed(1)}%/s</span>}
                  {typeof m.jitter_p95_deg === 'number' && <span className="faint">jitter {m.jitter_p95_deg.toFixed(1)}°</span>}
                  <span className="faint">speed ×{Number(res.options.speed ?? 1).toFixed(2)}{res.options.loop ? ' · loop' : ''}{res.options.in_place ? ' · in place' : ''}</span>
                </div>
                <div className="row" style={{ gap: 6 }}>
                  <input className="input" value={instr} onChange={(e) => setInstr(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') doEdit(instr) }}
                    placeholder="Describe a change, e.g. “make it 20% slower and smoother”, “less foot sliding”, “只要前两秒”" style={{ flex: 1 }} disabled={ed.busy} />
                  <button className="btn primary" disabled={!instr.trim() || ed.busy} onClick={() => doEdit(instr)}><Send size={14} />Apply</button>
                </div>
                <div className="row" style={{ gap: 4, flexWrap: 'wrap', marginTop: 6 }}>
                  {CHIPS.map((c) => <button key={c} className="btn ghost sm" disabled={ed.busy} onClick={() => doEdit(c)}>{c}</button>)}
                </div>
                {soma && (
                  <div style={{ marginTop: 10, padding: '8px 10px', border: '1px solid var(--line)', borderRadius: 'var(--radius-sm)', background: 'var(--bg-inset)' }}>
                    <div className="row" style={{ gap: 6, fontSize: 12, marginBottom: 6 }}>
                      <b>Join or loop</b><span className="faint">MotionBricks generates the in-between motion</span>
                    </div>
                    {mb && !mb.available ? <div style={{ fontSize: 11, color: 'var(--warn)' }}>Unavailable: {mb.note}</div> : (
                      <>
                        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                          <button className="btn sm" disabled={ed.busy} onClick={() => transition(true)}
                            title="Generate the way from the last pose back to the first one, so the clip repeats with no visible seam"><Repeat size={13} />Make a seamless loop</button>
                          <select className="select" style={{ flex: 1, minWidth: 160 }} value={joinWith} onChange={(e) => setJoinWith(e.target.value)} disabled={ed.busy}>
                            <option value="">Then play… (another described motion)</option>
                            {somaClips.map((c) => <option key={c.id} value={c.id}>{shortName(c.name)} · {c.duration.toFixed(1)} s</option>)}
                          </select>
                          <button className="btn sm primary" disabled={!joinWith || ed.busy} onClick={() => transition(false)}
                            title="This motion, a generated transition, then the chosen motion"><Merge size={13} />Join with a transition</button>
                        </div>
                        {!somaClips.length && <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>Generate another motion from a description to join it after this one.</div>}
                        {res.motionbricks && <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>
                          {res.motionbricks.loop ? 'Looped' : 'Joined'} with {res.motionbricks.transition_frames} generated frames ({(res.motionbricks.transition_frames / 30).toFixed(1)} s){res.motionbricks.loop ? '' : `, ${res.motionbricks.distance_m.toFixed(2)} m apart`}.
                        </div>}
                        <div className="faint" style={{ fontSize: 10, marginTop: 4 }}>{mb?.licence || 'NVIDIA Open Model Agreement'} · runs locally</div>
                      </>
                    )}
                  </div>
                )}
                <JobBar job={ed.job} error={ed.error} onCancel={ed.cancel} />
                {res.autotune && (
                  <div style={{ fontSize: 12, marginTop: 6 }}>
                    <span style={{ color: res.autotune.reached ? 'var(--ok)' : 'var(--warn)' }}>
                      {res.autotune.reached ? `Auto-tune reached ${Math.round(res.autotune.best_score ?? 0)}` : `Auto-tune best ${Math.round(res.autotune.best_score ?? 0)} (target ${res.autotune.target})`}
                    </span>
                    <span className="faint"> · {res.autotune.tries.length} tries: {res.autotune.tries.map((t) => `${t.change} → ${Math.round(t.score)}`).join(' · ')}</span>
                    {!res.autotune.reached && Object.keys(res.autotune.remaining).length > 0 &&
                      <div className="faint">Still costing points: {Object.entries(res.autotune.remaining).map(([k, v]) => `${k.replace('_', ' ')} −${v}`).join(', ')}. Try a cleaner part of the video or a different description.</div>}
                  </div>
                )}
                {res.edit && <div style={{ fontSize: 12, marginTop: 6 }}>{res.edit.explanation}{res.edit.unsupported ? <span style={{ color: 'var(--warn)' }}> · Not possible with these settings: {res.edit.unsupported}</span> : null}<span className="faint"> ({res.edit.planner})</span></div>}
                {res.history.length > 1 && (
                  <div style={{ marginTop: 8, fontSize: 12 }}>
                    <div className="faint" style={{ marginBottom: 2 }}>Edit history</div>
                    {res.history.map((h, i) => (
                      <div key={i} className="row" style={{ gap: 6, padding: '2px 0' }}>
                        <span className="chip" style={{ fontSize: 10 }}>{i + 1}</span>
                        <span className="grow" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.instruction}</span>
                        {i < res.history.length - 1 && <button className="btn ghost sm" disabled={ed.busy} onClick={() => undoTo(i)} title="Go back to this version"><RotateCcw size={12} /></button>}
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </Step>

          <Step n={4} title="Export" active={!!res}>
            {!res ? <div className="faint" style={{ fontSize: 12 }}>Available after generating.</div> : (
              <>
                <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                  <button className="btn primary" disabled={ex.busy} onClick={() => setEng(true)}><Gamepad2 size={14} />Export to game engine…</button>
                  <button className="btn" disabled={ex.busy} onClick={() => exportFile('glb')}><Download size={14} />GLB</button>
                  <button className="btn" disabled={ex.busy} onClick={() => exportFile('fbx')}><Download size={14} />FBX</button>
                  {res.media && <button className="btn" onClick={() => emit('cutlab:add-to-timeline', { media: res.media! })}><Clapperboard size={14} />Preview video → timeline</button>}
                  <button className="btn ghost sm" disabled={ex.busy} onClick={sendMyEngine} title="POST to the my-engine asset server"><Film size={13} />my-engine</button>
                </div>
                <JobBar job={ex.job} error={ex.error} onCancel={ex.cancel} />
                <div className="faint" style={{ fontSize: 11, marginTop: 6 }}>
                  Game preset: one file with mesh, skeleton and this clip, Y-up, metres, in place. Godot / Unity / three.js import it automatically; Unreal gets an import script.
                  The clip is also saved in the Animation library as “{res.clip.name}”.
                </div>
                {eng && <EngineExport asset={{ type: 'character', rig_id: res.rig.id, clip_ids: [res.clip.id] }} defaultName={`${res.rig.name}_${res.clip.name.split(' ')[0]}`} onClose={() => setEng(false)} />}
              </>
            )}
          </Step>
        </div>
      </div>

      {showDl && (
        <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && setShowDl(false)}>
          <div className="modal" style={{ width: 'min(1100px, 94vw)', height: '84vh' }}>
            <header><Link2 size={16} />Download a reference video<span className="grow" /><button className="btn ghost icon" onClick={() => setShowDl(false)}><X size={16} /></button></header>
            <div className="body" style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 0 }}><Downloader onGoLibrary={() => setShowDl(false)} /></div>
          </div>
        </div>
      )}
    </div>
  )
}
